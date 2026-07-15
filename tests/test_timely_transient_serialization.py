"""Shared timely transient ``_meta`` serialization semantics (adapter parity).

Contract (Jason #4307/#4313 + adapter-parity follow-up to the
timely-transient plan in ``reports/context-rebuild-pr-plan/``):

``_meta.agent_meta`` / ``_meta.guidance`` (family ``agent_meta``) and
legacy-input ``_meta.notifications`` / ``_meta.notification_guidance`` (family
``notifications``) are TIMELY transient state — only the newest occurrence per
family represents current state. Canonical history keeps old copies as
historical traces (no retroactive strip; see ``meta_block``); every
model-facing full-history serialization now PRESERVES every historical
occurrence of these keys — it does not strip, filter, or otherwise omit an
older holder's content. Only the newest holder in history is current/
actionable; older holders remain visible in replay as historical traces that
must not be acted on, and the producer channel (e.g. ``telegram.read``,
``email.read``) remains the source of truth for actionable channel content.

This is SHARED semantics, not a Codex special case: the converters
(``to_anthropic`` / ``to_openai`` / ``to_responses_input`` / ``to_gemini``)
and the claude_code full-history render all serialize ``ToolResultBlock``
content directly, with no timely-transient filtering step. On the Codex WS
path the per-``call_id`` freeze keeps already-sent outputs byte-stable within
an epoch for reasons unrelated to this preservation (in-place canonical
rewrites such as summarize marker/status flips); a fresh replay after an
epoch reset re-serializes through the shared converter, which still emits
every historical holder's content.

Summary replacement is unaffected: a summarized result's canonical content IS
the marker dict, and replays carry it — ``summarize`` is the only mechanism
that replaces a historical tool-result BODY during a rebuild. Synthetic
notification-holder skeletonization (moving/clearing a synthesized payload) is
a separate canonical-history mutation that happens before replay, not a
replay-time filter; it is unchanged by this contract and not exercised here.
The durable ``notification_persistent`` lane and permanent ``tool_meta`` were
never filtered and remain untouched either way.

These tests are content-free where possible: they assert key structure, not
tool-result bodies.
"""

from __future__ import annotations

import json

import pytest

from lingtai.kernel.llm.interface import (
    ChatInterface,
    ToolCallBlock,
    ToolResultBlock,
)

from lingtai.llm.interface_converters import (
    to_anthropic,
    to_gemini,
    to_openai,
    to_responses_input,
)


_ALL_TRANSIENT_META = {
    "agent_meta": {"elapsed_ms": 5},
    "guidance": {"ref": "meta_guidance"},
    "notifications": {"email": {"data": {"email_ids": ["email-1"]}}},
    "notification_guidance": {"ref": "meta_guidance.notification_handling"},
}

_NEWER_TRANSIENT_META = {
    "agent_meta": {"elapsed_ms": 9},
    "guidance": {"ref": "meta_guidance"},
    "notifications": {"email": {"data": {"email_ids": ["email-2"]}}},
    "notification_guidance": {"ref": "meta_guidance.notification_handling"},
}


def _iface_with_two_results(call_1_content, call_2_content) -> ChatInterface:
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="do_x", args={})])
    iface.add_tool_results(
        [ToolResultBlock(id="call_1", name="do_x", content=call_1_content)]
    )
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="do_x", args={})])
    iface.add_tool_results(
        [ToolResultBlock(id="call_2", name="do_x", content=call_2_content)]
    )
    return iface


def _anthropic_outputs(iface: ChatInterface) -> dict[str, str]:
    return {
        b["tool_use_id"]: b["content"]
        for m in to_anthropic(iface)
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    }


def _openai_outputs(iface: ChatInterface) -> dict[str, str]:
    return {
        m["tool_call_id"]: m["content"]
        for m in to_openai(iface)
        if m.get("role") == "tool"
    }


def _responses_outputs(iface: ChatInterface) -> dict[str, str]:
    return {
        it["call_id"]: it["output"]
        for it in to_responses_input(iface)
        if it.get("type") == "function_call_output"
    }


def _gemini_outputs(iface: ChatInterface) -> dict[str, str]:
    return {
        b["call_id"]: b["result"]
        for t in to_gemini(iface)
        for b in t["content"]
        if isinstance(b, dict) and b.get("type") == "function_result"
    }


_CONVERTER_OUTPUTS = [
    pytest.param(_anthropic_outputs, id="to_anthropic"),
    pytest.param(_openai_outputs, id="to_openai"),
    pytest.param(_responses_outputs, id="to_responses_input"),
    pytest.param(_gemini_outputs, id="to_gemini"),
]


# ---------------------------------------------------------------------------
# Converter parity — every model-facing full-history conversion preserves
# both historical and newest holders' content, and never mutates canonical
# content.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_converters_preserve_historical_and_newest_holders(outputs):
    call_1_content = {
        "ok": True,
        "_meta": {"tool_meta": {"id": "call_1"}, **_ALL_TRANSIENT_META},
    }
    call_2_content = {"done": True, "_meta": dict(_NEWER_TRANSIENT_META)}
    iface = _iface_with_two_results(call_1_content, call_2_content)

    serialized = outputs(iface)

    # Historical call_1 keeps every timely transient key it carried, plus
    # tool_meta — replay does not strip the older holder's content.
    parsed_1 = json.loads(serialized["call_1"])
    assert parsed_1["ok"] is True
    assert parsed_1["_meta"] == {"tool_meta": {"id": "call_1"}, **_ALL_TRANSIENT_META}
    # Newest call_2 keeps its live payload too.
    parsed_2 = json.loads(serialized["call_2"])
    assert parsed_2["_meta"] == _NEWER_TRANSIENT_META

    # Non-mutating: canonical content is untouched by conversion.
    assert set(call_1_content["_meta"]) == {"tool_meta", *_ALL_TRANSIENT_META}
    assert call_1_content["_meta"]["agent_meta"] == {"elapsed_ms": 5}


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_converters_preserve_family_on_non_newest_holder_too(outputs):
    # call_2 refreshes only the agent_meta family, so call_1 is the newest
    # notifications holder but a historical agent_meta/guidance holder.
    # Full-history replay must still carry call_1's ENTIRE _meta, including
    # the historical agent_meta/guidance copy alongside its current
    # notifications payload.
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    call_2_content = {
        "done": True,
        "_meta": {"agent_meta": {"elapsed_ms": 9}, "guidance": {"ref": "meta_guidance"}},
    }
    iface = _iface_with_two_results(call_1_content, call_2_content)

    serialized = outputs(iface)

    parsed_1 = json.loads(serialized["call_1"])
    assert set(parsed_1["_meta"]) == set(_ALL_TRANSIENT_META)
    assert parsed_1["_meta"]["notifications"] == {
        "email": {"data": {"email_ids": ["email-1"]}}
    }
    # The historical agent_meta/guidance copy on call_1 is preserved too, even
    # though call_2 carries a newer agent_meta emission.
    assert parsed_1["_meta"]["agent_meta"] == {"elapsed_ms": 5}
    parsed_2 = json.loads(serialized["call_2"])
    assert parsed_2["_meta"]["agent_meta"] == {"elapsed_ms": 9}


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_converters_pass_unaffected_outputs_through_unchanged(outputs):
    # Ordinary payloads, summary markers, and permanent _meta lanes are not
    # timely transient and must serialize exactly as before.
    ordinary = {"stdout": "all good", "exit_code": 0, "_meta": {"tool_meta": {"id": "call_1"}}}
    marker = {
        "artifact": "lingtai_agent_summarized_result",
        "agent_summary": "digest",
        "status": "pending",
    }
    iface = _iface_with_two_results(ordinary, marker)

    serialized = outputs(iface)

    assert serialized["call_1"] == json.dumps(ordinary, default=str)
    assert serialized["call_2"] == json.dumps(marker, default=str)


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_converters_preserve_notification_persistent_lane(outputs):
    # notification_persistent is the DURABLE communication-context lane, not a
    # timely transient block — it was never filtered and stays untouched.
    content = {
        "ok": True,
        "_meta": {
            "notifications": {"mcp.telegram": {"data": {"message_ids": ["m1"]}}},
            "notification_persistent": {"mcp": {"telegram": {"messages": []}}},
        },
    }
    iface = _iface_with_two_results(content, {"done": True})

    serialized = outputs(iface)

    parsed_1 = json.loads(serialized["call_1"])
    assert parsed_1["_meta"]["notifications"] == {
        "mcp.telegram": {"data": {"message_ids": ["m1"]}}
    }
    assert parsed_1["_meta"]["notification_persistent"] == {
        "mcp": {"telegram": {"messages": []}}
    }


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_converters_leave_string_content_byte_identical(outputs):
    # String tool-result content passes through as the exact same string —
    # no re-dump, no mutation.
    original = json.dumps({"ok": True, "_meta": dict(_ALL_TRANSIENT_META)})
    iface = _iface_with_two_results(original, {"done": True})

    serialized = outputs(iface)

    assert serialized["call_1"] == original


def test_content_is_read_directly_without_intermediate_helpers():
    # The shared converters no longer route ToolResultBlock.content through
    # any newest-holder/filtering helper — they read block.content directly.
    # This asserts the absence of the removed API surface, protecting against
    # a regression that reintroduces filtering.
    import lingtai.llm.interface_converters as ic

    assert not hasattr(ic, "filter_stale_timely_transient")
    assert not hasattr(ic, "timely_transient_newest_holders")
    assert not hasattr(ic, "TIMELY_TRANSIENT_META_FAMILIES")


# ---------------------------------------------------------------------------
# claude_code — the CLI full-history render is model-facing serialization too.
# ---------------------------------------------------------------------------


def test_claude_code_render_preserves_historical_and_newest_holders():
    from lingtai.llm.claude_code.adapter import ClaudeCodeChatSession

    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    call_2_content = {"done": True, "_meta": dict(_NEWER_TRANSIENT_META)}
    iface = _iface_with_two_results(call_1_content, call_2_content)
    session = ClaudeCodeChatSession(
        adapter=None,
        model="sonnet",
        system_prompt="",
        tools=[],
        interface=iface,
        context_window=100_000,
    )

    rendered = session._render_conversation()

    assert "email-1" in rendered  # historical notification copy preserved
    assert "email-2" in rendered  # newest payload also present
    # Non-mutating: canonical content still carries the historical copy.
    assert call_1_content["_meta"]["notifications"] == {
        "email": {"data": {"email_ids": ["email-1"]}}
    }


# ---------------------------------------------------------------------------
# Codex session-level: within an epoch the freeze keeps already-sent outputs
# byte-stable; an epoch reset clears the freeze map so the fresh replay
# re-serializes through the shared converter, which still carries every
# historical holder's content — all without mutating canonical content.
# ---------------------------------------------------------------------------

from tests.test_codex_ws_session import (  # noqa: E402
    _PerTurnToolCallWsTransport,
    _make_session,
)


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


def test_rebuild_replay_preserves_historical_and_newest_transients_without_mutating_canonical():
    transport, session = _tool_loop_session()
    call_1_content = {
        "ok": True,
        "_meta": {"tool_meta": {"id": "call_1"}, **_ALL_TRANSIENT_META},
    }
    session.send([_result_block("call_1", call_1_content)])  # emits call_2

    assert session.request_history_rebuild() is True

    # Turn 3 answers call_2 with newer values for both timely families, making
    # call_1 the historical holder for both.
    call_2_content = {
        "done": True,
        "_meta": {"tool_meta": {"id": "call_2"}, **_NEWER_TRANSIENT_META},
    }
    result = session.send([_result_block("call_2", call_2_content)])

    assert result.usage.extra["codex_ws_epoch_reset_reason"] == "summarize_rebuild_only"
    outputs = _replay_outputs(transport.sent_frames[-1])

    # Historical call_1 keeps every timely transient key plus tool_meta.
    replayed_1 = json.loads(outputs["call_1"])
    assert replayed_1["ok"] is True
    assert replayed_1["_meta"] == {"tool_meta": {"id": "call_1"}, **_ALL_TRANSIENT_META}
    # Newest call_2 keeps its live timely payload.
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


def test_rebuild_replay_keeps_meta_envelope_for_historical_holder():
    transport, session = _tool_loop_session()
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    session.send([_result_block("call_1", call_1_content)])

    assert session.request_history_rebuild() is True
    # call_2 carries newer values for both timely families; call_1 becomes the
    # historical holder but its _meta envelope must still survive replay.
    session.send([_result_block("call_2", {"done": True, "_meta": dict(_ALL_TRANSIENT_META)})])

    outputs = _replay_outputs(transport.sent_frames[-1])
    assert set(json.loads(outputs["call_1"])["_meta"]) == set(_ALL_TRANSIENT_META)
    assert set(json.loads(outputs["call_2"])["_meta"]) == set(_ALL_TRANSIENT_META)
    # Canonical keeps the envelope too (unmutated).
    assert set(call_1_content["_meta"]) == set(_ALL_TRANSIENT_META)


def test_rebuild_replay_preserves_newest_holder():
    transport, session = _tool_loop_session()
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    session.send([_result_block("call_1", call_1_content)])

    assert session.request_history_rebuild() is True
    # No newer timely payload appears before the rebuild replay: call_1 is
    # still the newest/current holder for both families and keeps its payload.
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
    # with the marker dict (summarize.py builds a fresh dict, no _meta). This
    # is the ONE mechanism by which a rebuild replaces a historical
    # tool-result BODY — unrelated to (and unaffected by) transient-metadata
    # preservation.
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


def test_frozen_output_stays_byte_stable_across_turns_within_epoch():
    # The per-call_id output freeze (unrelated to transient-metadata
    # preservation) keeps an already-sent function_call_output.output
    # string byte-identical across turns within the same epoch, even though
    # the resident kernel may move latest-only _meta blocks between tool
    # results in place between turns. Within an epoch the WS session sends
    # only the incremental delta (the new turn's result), not a full replay,
    # so the freeze is observed by the delta staying ws_incremental (the
    # frozen call_1 output kept the strict-prefix baseline byte-stable)
    # rather than by call_1 reappearing in a later frame's input.
    transport, session = _tool_loop_session()
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    session.send([_result_block("call_1", call_1_content)])  # emits call_2

    # Simulate the kernel moving the latest-only _meta blocks off call_1 by
    # mutating its canonical content in place (no epoch reset in between).
    call_1_content["_meta"].pop("agent_meta", None)
    call_1_content["_meta"].pop("guidance", None)

    result = session.send([_result_block("call_2", {"done": True})])

    assert result.usage.extra["codex_request_mode"] == "ws_incremental"
    delta = transport.sent_frames[-1]["input"]
    assert [i.get("call_id") for i in delta if i.get("type") == "function_call_output"] == ["call_2"]
