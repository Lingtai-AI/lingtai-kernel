"""Codex rebuild-replay filter for timely transient ``_meta`` blocks.

Contract (Jason #4307/#4313, plan
``reports/context-rebuild-pr-plan/20260706-timely-transient-rebuild-filter-plan.html``):

``_meta.agent_meta`` / ``_meta.guidance`` and ``_meta.notifications`` /
``_meta.notification_guidance`` are TIMELY transient state — only the newest
payload represents current state. Canonical history no longer retroactively
strips old payloads (they stay as historical traces; see
``meta_block.attach_active_runtime`` / ``attach_active_notifications``); instead
the Codex provider-context rebuild / fresh replay filters those four keys from
HISTORICAL tool-result outputs at serialization time, without mutating
``ChatInterface`` / ``ToolResultBlock.content`` / durable history.

"Historical" is precise: outputs the provider already received (their
``call_id`` was in the freeze map when ``_reset_ws_epoch`` cleared it). A
result first seen on the rebuild send itself still freezes WITH its live
payload — the model must not lose a payload it has never seen.

Summary replacement is unaffected: a summarized result's canonical content IS
the marker dict, and the rebuilt replay carries it.

These tests are content-free where possible: they assert key structure, not
tool-result bodies.
"""

from __future__ import annotations

import json

from lingtai_kernel.llm.interface import ToolResultBlock

from lingtai.llm.openai.adapter import (
    _filter_timely_transient_output,
    _freeze_responses_outputs,
)

from tests.test_codex_ws_session import (
    _PerTurnToolCallWsTransport,
    _make_session,
)


def _fco(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


_ALL_TRANSIENT_META = {
    "agent_meta": {"elapsed_ms": 5},
    "guidance": {"ref": "meta_guidance"},
    "notifications": {"email": {"data": {"email_ids": ["email-1"]}}},
    "notification_guidance": {"ref": "meta_guidance.notification_handling"},
}


# ---------------------------------------------------------------------------
# _filter_timely_transient_output — pure, non-mutating output-string filter.
# ---------------------------------------------------------------------------


def test_filter_removes_all_four_transient_keys_and_keeps_others():
    content = {
        "ok": True,
        "_meta": {"tool_meta": {"id": "t1"}, **_ALL_TRANSIENT_META},
    }
    output = json.dumps(content, default=str)

    filtered = _filter_timely_transient_output(output)

    parsed = json.loads(filtered)
    assert parsed["ok"] is True
    assert parsed["_meta"] == {"tool_meta": {"id": "t1"}}


def test_filter_preserves_notification_persistent_lane():
    # notification_persistent is the DURABLE communication-context lane, not a
    # timely transient block — the rebuild filter must never touch it.
    content = {
        "ok": True,
        "_meta": {
            "notifications": {"mcp.telegram": {"data": {"message_ids": ["m1"]}}},
            "notification_persistent": {"mcp": {"telegram": {"messages": []}}},
        },
    }

    parsed = json.loads(_filter_timely_transient_output(json.dumps(content, default=str)))

    assert "notifications" not in parsed["_meta"]
    assert parsed["_meta"]["notification_persistent"] == {
        "mcp": {"telegram": {"messages": []}}
    }


def test_filter_omits_meta_envelope_when_only_transient_keys():
    content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}

    parsed = json.loads(_filter_timely_transient_output(json.dumps(content, default=str)))

    assert parsed == {"ok": True}
    assert "_meta" not in parsed


def test_filter_returns_original_string_when_nothing_to_filter():
    # Byte stability matters for the freeze/prefix machinery: an output with no
    # transient keys must pass through as the SAME string object (no re-dump).
    for output in (
        "plain text, not json",
        json.dumps([1, 2, 3]),
        json.dumps({"ok": True}),
        json.dumps({"ok": True, "_meta": {"tool_meta": {"id": "t1"}}}),
        json.dumps(
            {
                "artifact": "lingtai_agent_summarized_result",
                "agent_summary": "digest",
                "status": "pending",
            }
        ),
    ):
        assert _filter_timely_transient_output(output) is output


def test_filter_passes_non_string_output_through():
    assert _filter_timely_transient_output(None) is None


# ---------------------------------------------------------------------------
# _freeze_responses_outputs + transient_filter_ids — the rebuild wiring.
# ---------------------------------------------------------------------------


def test_freeze_filters_only_listed_historical_call_ids():
    frozen: dict[str, str] = {}
    historical = json.dumps({"ok": True, "_meta": {"tool_meta": {"id": "a"}, **_ALL_TRANSIENT_META}})
    fresh = json.dumps(
        {
            "done": True,
            "_meta": {
                "agent_meta": {"elapsed_ms": 9},
                "notifications": {"email": {"data": {"email_ids": ["email-2"]}}},
            },
        }
    )
    items = [_fco("call_a", historical), _fco("call_b", fresh)]

    out = _freeze_responses_outputs(items, frozen, transient_filter_ids={"call_a"})

    # Historical call_a is filtered at first (re)freeze.
    parsed_a = json.loads(out[0]["output"])
    assert parsed_a["_meta"] == {"tool_meta": {"id": "a"}}
    assert frozen["call_a"] == out[0]["output"]
    # Fresh call_b (never sent before) keeps its live payload.
    assert out[1]["output"] == fresh
    assert frozen["call_b"] == fresh


def test_freeze_filter_does_not_mutate_caller_items():
    frozen: dict[str, str] = {}
    original_output = json.dumps({"ok": True, "_meta": dict(_ALL_TRANSIENT_META)})
    original = _fco("call_a", original_output)
    latest = _fco("call_b", original_output)

    out = _freeze_responses_outputs(
        [original, latest], frozen, transient_filter_ids={"call_a", "call_b"}
    )

    assert original["output"] == original_output
    assert out[0] is not original
    assert "_meta" not in json.loads(out[0]["output"])
    # The newest occurrence is still-current and therefore not filtered.
    assert out[1]["output"] == original_output


def test_freeze_filter_ignored_for_already_frozen_ids():
    # Once re-frozen, the cached string replays regardless of the filter set.
    frozen = {"call_a": "frozen-bytes"}
    items = [_fco("call_a", json.dumps({"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}))]

    out = _freeze_responses_outputs(items, frozen, transient_filter_ids={"call_a"})

    assert out[0]["output"] == "frozen-bytes"


# ---------------------------------------------------------------------------
# Session-level: epoch reset marks previously-sent outputs historical; the
# fresh replay filters them without mutating canonical content.
# ---------------------------------------------------------------------------


def _tool_loop_session():
    """A Codex WS session mid tool loop: call_1 answered (sent), call_2 open."""
    transport = _PerTurnToolCallWsTransport()
    session = _make_session(transport)
    session.send("start the tool loop")  # turn 1 -> assistant emits call_1
    return transport, session


def _result_block(call_id: str, content) -> ToolResultBlock:
    return ToolResultBlock(id=call_id, name="do_x", content=content)


def _replay_outputs(frame) -> dict[str, str]:
    return {
        item["call_id"]: item["output"]
        for item in frame["input"]
        if item.get("type") == "function_call_output"
    }


def test_rebuild_replay_filters_historical_transients_without_mutating_canonical():
    transport, session = _tool_loop_session()
    call_1_content = {
        "ok": True,
        "_meta": {"tool_meta": {"id": "call_1"}, **_ALL_TRANSIENT_META},
    }
    session.send([_result_block("call_1", call_1_content)])  # emits call_2

    assert session.request_history_rebuild() is True

    # Turn 3 answers call_2 and carries a LIVE payload the model has not seen:
    # it is first-seen on the rebuild send itself and must NOT be filtered.
    call_2_content = {
        "done": True,
        "_meta": {
            "tool_meta": {"id": "call_2"},
            "agent_meta": {"elapsed_ms": 9},
            "guidance": {"ref": "meta_guidance"},
            "notifications": {"email": {"data": {"email_ids": ["email-2"]}}},
            "notification_guidance": {"ref": "meta_guidance.notification_handling"},
        },
    }
    result = session.send([_result_block("call_2", call_2_content)])

    assert result.usage.extra["codex_ws_epoch_reset_reason"] == "summarize_rebuild_only"
    outputs = _replay_outputs(transport.sent_frames[-1])

    # Historical call_1 sheds the four timely transient keys; tool_meta stays.
    replayed_1 = json.loads(outputs["call_1"])
    assert replayed_1["ok"] is True
    assert replayed_1["_meta"] == {"tool_meta": {"id": "call_1"}}
    # Fresh call_2 keeps its live timely payload.
    replayed_2 = json.loads(outputs["call_2"])
    assert replayed_2["_meta"]["agent_meta"] == {"elapsed_ms": 9}
    assert replayed_2["_meta"]["notifications"] == {
        "email": {"data": {"email_ids": ["email-2"]}}
    }

    # Non-mutating: canonical content still carries every transient key.
    assert call_1_content["_meta"]["agent_meta"] == {"elapsed_ms": 5}
    assert call_1_content["_meta"]["guidance"] == {"ref": "meta_guidance"}
    assert call_1_content["_meta"]["notifications"] == {
        "email": {"data": {"email_ids": ["email-1"]}}
    }
    assert call_1_content["_meta"]["notification_guidance"] == {
        "ref": "meta_guidance.notification_handling"
    }


def test_rebuild_replay_omits_empty_meta_envelope():
    transport, session = _tool_loop_session()
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    session.send([_result_block("call_1", call_1_content)])

    assert session.request_history_rebuild() is True
    # call_2 carries newer values for both timely families, so call_1 becomes an
    # old copy and its now-empty `_meta` envelope should disappear in replay.
    session.send([_result_block("call_2", {"done": True, "_meta": dict(_ALL_TRANSIENT_META)})])

    outputs = _replay_outputs(transport.sent_frames[-1])
    assert "_meta" not in json.loads(outputs["call_1"])
    assert set(json.loads(outputs["call_2"])["_meta"]) == set(_ALL_TRANSIENT_META)
    # Canonical keeps the envelope.
    assert set(call_1_content["_meta"]) == set(_ALL_TRANSIENT_META)


def test_rebuild_replay_preserves_newest_historical_live_holder():
    transport, session = _tool_loop_session()
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    session.send([_result_block("call_1", call_1_content)])

    assert session.request_history_rebuild() is True
    # No newer timely payload appears before rebuild replay. call_1 is historical
    # in the Responses sense, but it is still the newest/current timely holder.
    session.send([_result_block("call_2", {"done": True})])

    outputs = _replay_outputs(transport.sent_frames[-1])
    replayed_1 = json.loads(outputs["call_1"])
    assert replayed_1["_meta"]["agent_meta"] == {"elapsed_ms": 5}
    assert replayed_1["_meta"]["notifications"] == {
        "email": {"data": {"email_ids": ["email-1"]}}
    }


def test_rebuild_replay_still_applies_summary_marker():
    transport, session = _tool_loop_session()
    block = _result_block(
        "call_1", {"raw": "x" * 50, "_meta": {"agent_meta": {"elapsed_ms": 5}}}
    )
    session.send([block])

    # Mimic the summarize intrinsic: replace the canonical content in place
    # with the marker dict (summarize.py builds a fresh dict, no _meta).
    marker = {
        "artifact": "lingtai_agent_summarized_result",
        "tool_call_id": "call_1",
        "agent_summary": "the digest",
        "status": "pending",
    }
    for entry in session._interface.entries:
        for idx, blk in enumerate(entry.content or []):
            if isinstance(blk, ToolResultBlock) and blk.id == "call_1":
                entry.content[idx].content = marker

    assert session.request_history_rebuild() is True
    session.send([_result_block("call_2", {"done": True})])

    outputs = _replay_outputs(transport.sent_frames[-1])
    replayed = json.loads(outputs["call_1"])
    assert replayed["artifact"] == "lingtai_agent_summarized_result"
    assert replayed["agent_summary"] == "the digest"
    # Applied on rebuild: the pending marker flipped to done.
    assert replayed["status"] == "done"


def test_rebuild_replay_preserves_ordinary_outputs_byte_identically():
    transport, session = _tool_loop_session()
    ordinary = {"stdout": "all good", "exit_code": 0, "_meta": {"tool_meta": {"id": "call_1"}}}
    session.send([_result_block("call_1", ordinary)])
    before = _replay_outputs(transport.sent_frames[-1])["call_1"]

    assert session.request_history_rebuild() is True
    session.send([_result_block("call_2", {"done": True})])

    after = _replay_outputs(transport.sent_frames[-1])["call_1"]
    assert after == before
