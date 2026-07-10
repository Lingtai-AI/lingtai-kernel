"""Shared timely transient ``_meta`` serialization semantics (adapter parity).

Contract (Jason #4307/#4313 + adapter-parity follow-up to the
timely-transient plan in ``reports/context-rebuild-pr-plan/``):

``_meta.agent_meta`` / ``_meta.guidance`` (family ``agent_meta``) and
``_meta.notifications`` / ``_meta.notification_guidance`` (family
``notifications``) are TIMELY transient state — only the newest occurrence per
family represents current state. Canonical history keeps old copies as
historical traces (no retroactive strip; see ``meta_block``); every
model-facing full-history serialization instead presents only the newest
occurrence per family and omits the stale copies, without mutating
``ChatInterface`` / ``ToolResultBlock.content`` / durable history.

This is SHARED semantics, not a Codex special case: the converters
(``to_anthropic`` / ``to_openai`` / ``to_responses_input`` / ``to_gemini``)
and the claude_code full-history render all filter through
``interface_converters.filter_stale_timely_transient``. On the Codex WS path
the per-``call_id`` freeze keeps already-sent outputs byte-identical within an
epoch; a fresh replay after an epoch reset re-freezes from the shared
converter's serialization and so sheds the stale copies — no adapter-private
filter state.

Summary replacement is unaffected: a summarized result's canonical content IS
the marker dict, and replays carry it. The durable ``notification_persistent``
lane and permanent ``tool_meta`` are never filtered.

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
    filter_stale_timely_transient,
    timely_transient_newest_holders,
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
# Converter parity — every model-facing full-history conversion strips stale
# copies, keeps the newest per family, and never mutates canonical content.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_converters_strip_stale_copies_and_keep_newest(outputs):
    call_1_content = {
        "ok": True,
        "_meta": {"tool_meta": {"id": "call_1"}, **_ALL_TRANSIENT_META},
    }
    call_2_content = {"done": True, "_meta": dict(_NEWER_TRANSIENT_META)}
    iface = _iface_with_two_results(call_1_content, call_2_content)

    serialized = outputs(iface)

    # Stale call_1 sheds the four timely transient keys; tool_meta stays.
    parsed_1 = json.loads(serialized["call_1"])
    assert parsed_1["ok"] is True
    assert parsed_1["_meta"] == {"tool_meta": {"id": "call_1"}}
    # Newest call_2 keeps its live payload.
    parsed_2 = json.loads(serialized["call_2"])
    assert parsed_2["_meta"] == _NEWER_TRANSIENT_META

    # Non-mutating: canonical content still carries every transient key.
    assert set(call_1_content["_meta"]) == {"tool_meta", *_ALL_TRANSIENT_META}
    assert call_1_content["_meta"]["agent_meta"] == {"elapsed_ms": 5}


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_converters_keep_family_on_its_own_newest_holder(outputs):
    # call_2 refreshes only the agent_meta family, so call_1 stays the newest
    # notifications holder: it sheds agent_meta/guidance but KEEPS the
    # notification payload.
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    call_2_content = {
        "done": True,
        "_meta": {"agent_meta": {"elapsed_ms": 9}, "guidance": {"ref": "meta_guidance"}},
    }
    iface = _iface_with_two_results(call_1_content, call_2_content)

    serialized = outputs(iface)

    parsed_1 = json.loads(serialized["call_1"])
    assert set(parsed_1["_meta"]) == {"notifications", "notification_guidance"}
    assert parsed_1["_meta"]["notifications"] == {
        "email": {"data": {"email_ids": ["email-1"]}}
    }
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


# ---------------------------------------------------------------------------
# Pure helpers — non-mutating filter and newest-holder detection.
# ---------------------------------------------------------------------------


def test_filter_returns_original_object_when_nothing_to_filter():
    # Byte/object stability matters (e.g. for the Codex freeze/prefix
    # machinery): content with nothing to remove passes through as the SAME
    # object, not a re-dump.
    newest: dict[str, ToolResultBlock] = {}
    for content in (
        "plain text, not json",
        json.dumps([1, 2, 3]),
        json.dumps({"ok": True}),
        {"ok": True},
        {"ok": True, "_meta": {"tool_meta": {"id": "t1"}}},
        {
            "artifact": "lingtai_agent_summarized_result",
            "agent_summary": "digest",
            "status": "pending",
        },
    ):
        block = ToolResultBlock(id="call_1", name="do_x", content=content)
        assert filter_stale_timely_transient(block, newest) is content


def test_filter_keeps_content_of_the_newest_holder_itself():
    content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    block = ToolResultBlock(id="call_1", name="do_x", content=content)
    newest = {"agent_meta": block, "notifications": block}

    assert filter_stale_timely_transient(block, newest) is content


def test_filter_removes_all_four_stale_keys_and_keeps_others():
    content = {
        "ok": True,
        "_meta": {"tool_meta": {"id": "t1"}, **_ALL_TRANSIENT_META},
    }
    block = ToolResultBlock(id="call_1", name="do_x", content=content)

    filtered = filter_stale_timely_transient(block, {})

    assert filtered == {"ok": True, "_meta": {"tool_meta": {"id": "t1"}}}
    # Canonical content is untouched.
    assert set(content["_meta"]) == {"tool_meta", *_ALL_TRANSIENT_META}


def test_filter_preserves_notification_persistent_lane():
    # notification_persistent is the DURABLE communication-context lane, not a
    # timely transient block — the filter must never touch it.
    content = {
        "ok": True,
        "_meta": {
            "notifications": {"mcp.telegram": {"data": {"message_ids": ["m1"]}}},
            "notification_persistent": {"mcp": {"telegram": {"messages": []}}},
        },
    }
    block = ToolResultBlock(id="call_1", name="do_x", content=content)

    filtered = filter_stale_timely_transient(block, {})

    assert "notifications" not in filtered["_meta"]
    assert filtered["_meta"]["notification_persistent"] == {
        "mcp": {"telegram": {"messages": []}}
    }


def test_filter_omits_meta_envelope_when_only_stale_keys():
    content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    block = ToolResultBlock(id="call_1", name="do_x", content=content)

    filtered = filter_stale_timely_transient(block, {})

    assert filtered == {"ok": True}
    assert "_meta" in content  # canonical keeps the envelope


def test_filter_handles_json_string_content_without_mutating_it():
    original = json.dumps({"ok": True, "_meta": dict(_ALL_TRANSIENT_META)})
    block = ToolResultBlock(id="call_1", name="do_x", content=original)

    filtered = filter_stale_timely_transient(block, {})

    assert isinstance(filtered, str)
    assert json.loads(filtered) == {"ok": True}
    assert block.content == original  # same canonical string, unmutated


def test_newest_holders_track_last_occurrence_per_family():
    call_1_content = {"ok": True, "_meta": {"notifications": {"email": {}}}}
    call_2_content = {"done": True, "_meta": {"agent_meta": {"elapsed_ms": 9}}}
    iface = _iface_with_two_results(call_1_content, call_2_content)

    newest = timely_transient_newest_holders(iface)

    assert newest["notifications"].id == "call_1"
    assert newest["agent_meta"].id == "call_2"


# ---------------------------------------------------------------------------
# claude_code — the CLI full-history render is model-facing serialization too.
# ---------------------------------------------------------------------------


def test_claude_code_render_filters_stale_copies():
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

    assert "email-1" not in rendered  # stale notification copy omitted
    assert "email-2" in rendered  # newest payload kept
    # Non-mutating: canonical content still carries the stale copy.
    assert call_1_content["_meta"]["notifications"] == {
        "email": {"data": {"email_ids": ["email-1"]}}
    }


# ---------------------------------------------------------------------------
# Codex session-level: within an epoch the freeze keeps already-sent outputs
# byte-identical; an epoch reset clears the freeze map so the fresh replay
# re-freezes from the shared converter and sheds the stale copies — all
# without mutating canonical content.
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


def test_rebuild_replay_filters_stale_transients_without_mutating_canonical():
    transport, session = _tool_loop_session()
    call_1_content = {
        "ok": True,
        "_meta": {"tool_meta": {"id": "call_1"}, **_ALL_TRANSIENT_META},
    }
    session.send([_result_block("call_1", call_1_content)])  # emits call_2

    assert session.request_history_rebuild() is True

    # Turn 3 answers call_2 with newer values for both timely families, making
    # call_1's copies stale.
    call_2_content = {
        "done": True,
        "_meta": {"tool_meta": {"id": "call_2"}, **_NEWER_TRANSIENT_META},
    }
    result = session.send([_result_block("call_2", call_2_content)])

    assert result.usage.extra["codex_ws_epoch_reset_reason"] == "summarize_rebuild_only"
    outputs = _replay_outputs(transport.sent_frames[-1])

    # Stale call_1 sheds the four timely transient keys; tool_meta stays.
    replayed_1 = json.loads(outputs["call_1"])
    assert replayed_1["ok"] is True
    assert replayed_1["_meta"] == {"tool_meta": {"id": "call_1"}}
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


def test_rebuild_replay_omits_empty_meta_envelope():
    transport, session = _tool_loop_session()
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    session.send([_result_block("call_1", call_1_content)])

    assert session.request_history_rebuild() is True
    # call_2 carries newer values for both timely families, so call_1 becomes a
    # stale copy and its now-empty `_meta` envelope should disappear in replay.
    session.send([_result_block("call_2", {"done": True, "_meta": dict(_ALL_TRANSIENT_META)})])

    outputs = _replay_outputs(transport.sent_frames[-1])
    assert "_meta" not in json.loads(outputs["call_1"])
    assert set(json.loads(outputs["call_2"])["_meta"]) == set(_ALL_TRANSIENT_META)
    # Canonical keeps the envelope.
    assert set(call_1_content["_meta"]) == set(_ALL_TRANSIENT_META)


def test_rebuild_replay_preserves_newest_holder_even_when_historical():
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
