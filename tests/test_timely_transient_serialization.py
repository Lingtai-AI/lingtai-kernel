"""Shared timely transient ``_meta`` serialization semantics (adapter parity).

Contract (Jason #4307/#4313 + adapter-parity follow-up to the
timely-transient plan in ``reports/context-rebuild-pr-plan/``):

``_meta.agent_meta`` / ``_meta.guidance`` (family ``agent_meta``) and
``_meta.notifications`` / ``_meta.notification_guidance`` (family
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
content directly for the two timely-transient families, with no filtering
step — every historical holder's ``agent_meta`` / ``guidance`` /
``notifications`` / ``notification_guidance`` reaches the model. On the
Codex WS path the per-``call_id`` freeze keeps already-sent outputs
byte-stable within an epoch for reasons unrelated to this preservation
(in-place canonical rewrites such as summarize marker/status flips); a fresh
replay after an epoch reset re-serializes through the shared converter,
which still emits every historical holder's content.

Summary replacement is unaffected: a summarized result's canonical content IS
the marker dict, and replays carry it — ``summarize`` is the only mechanism
that replaces a historical tool-result BODY during a rebuild. Synthetic
notification-holder skeletonization (moving/clearing a synthesized payload) is
a separate canonical-history mutation that happens before replay, not a
replay-time filter; it is unchanged by this contract and not exercised here.
The durable ``notification_persistent`` lane and permanent ``tool_meta`` were
never filtered and remain untouched either way.

``notification_persistent.email`` is a SEPARATE, narrower whole-snapshot
invariant (see the dedicated section below): it is an atomic current-unread
snapshot, not timely-transient-family state and not a set of independent
per-id records. It is the ONE full-history projection the five renderers
still apply, through the internal
``interface_converters._render_full_history_result(block, newest_email_snapshot)``
primitive — only the newest ``notification_persistent.email`` child survives
replay; every older one (live or an explicit clear tombstone) loses its
``email`` child in full.

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
    _render_full_history_result,
    newest_email_snapshot_holder,
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
    # timely transient block — it was never filtered and stays untouched,
    # except the narrower whole-snapshot email invariant tested separately
    # below. This block carries only a Telegram delta-lane payload (no
    # email), so it is untouched either way.
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
    # The shared converters no longer route a ToolResultBlock's timely
    # transient _meta keys (agent_meta/guidance, notifications/
    # notification_guidance) through any newest-holder/filtering helper —
    # they read block.content directly for those families. The removed
    # helper API surface stays absent, protecting against a regression that
    # reintroduces family-level filtering. `newest_email_snapshot_holder`
    # and `_render_full_history_result` remain — they are the narrower email
    # whole-snapshot projection, not timely-transient filtering.
    import lingtai.llm.interface_converters as ic

    assert not hasattr(ic, "filter_stale_timely_transient")
    assert not hasattr(ic, "timely_transient_newest_holders")
    assert not hasattr(ic, "TIMELY_TRANSIENT_META_FAMILIES")


# ---------------------------------------------------------------------------
# Email whole-snapshot invariant (replaces the rejected per-email-id premise).
#
# Email is a producer-owned ATOMIC current-unread snapshot (see
# ``tools/email/primitives.py::_unread_notification_context`` /
# ``meta_block.py::_build_email_notification_persistent_payload`` /
# ``LICC_NOTIFICATION_CONTRACT.md``), not an append-only collection of
# independent per-id records. Correlated fields (``count``,
# ``newest_received_at``, ``context_comment``, ``email_ids``, ``emails``) all
# describe ONE snapshot together and must never be spliced against a
# different snapshot. Full-history replay keeps only the newest whole
# ``notification_persistent.email`` child (a live snapshot, or an explicit
# ``{"cleared": True, ...}`` tombstone once unread count reaches zero — see
# ``meta_block.build_email_persistent_cleared_marker``) and removes the
# entire child, whole-block, from every earlier holder.
# ---------------------------------------------------------------------------


def _email_snapshot_content(
    *, email_ids: list[str], emails: list[dict], count: int, newest_received_at: str
) -> dict:
    """An authentic-shaped whole email snapshot, mirroring the real producer
    payload built by ``_build_email_notification_persistent_payload`` — every
    correlated field (``count``, ``newest_received_at``, ``context_comment``,
    ``email_ids``, ``emails``) belongs to the SAME snapshot."""
    return {
        "ok": True,
        "_meta": {
            "notification_persistent": {
                "email": {
                    "context_comment": "Unread email content moved here from "
                    "_meta.notifications.email.",
                    "email_ids": email_ids,
                    "count": count,
                    "newest_received_at": newest_received_at,
                    "emails": emails,
                }
            }
        },
    }


def _email_message(msg_id: str, *, sender: str, message: str, subject: str = "Q3 numbers") -> dict:
    return {
        "id": msg_id,
        "from": sender,
        "subject": subject,
        "message": message,
        "unread": True,
    }


def _email_cleared_content() -> dict:
    from lingtai.kernel.meta_block import build_email_persistent_cleared_marker

    return {
        "ok": True,
        "_meta": {
            "notification_persistent": {"email": build_email_persistent_cleared_marker()}
        },
    }


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_older_whole_snapshot_absent_after_id_drops_out(outputs):
    """`[A, B]` then current `[B]`: the earlier WHOLE email child is absent;
    `A` is never resurrected — this is the corrected replacement for the
    rejected per-ID merge premise, which kept unique older ids alive."""
    older = ToolResultBlock(
        id="call_1",
        name="email",
        content=_email_snapshot_content(
            email_ids=["A", "B"],
            emails=[
                _email_message("A", sender="human", message="message A"),
                _email_message("B", sender="human", message="message B"),
            ],
            count=2,
            newest_received_at="2026-07-06T07:00:00Z",
        ),
    )
    current = ToolResultBlock(
        id="call_2",
        name="email",
        content=_email_snapshot_content(
            email_ids=["B"],
            emails=[_email_message("B", sender="human", message="message B")],
            count=1,
            newest_received_at="2026-07-06T07:00:00Z",
        ),
    )
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="email", args={})])
    iface.add_tool_results([older])
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="email", args={})])
    iface.add_tool_results([current])

    serialized = outputs(iface)

    replayed_older = json.loads(serialized["call_1"])
    assert "notification_persistent" not in replayed_older.get("_meta", {}), (
        "the earlier whole email snapshot must be removed in full, not "
        "partially spliced to keep id A alive"
    )
    replayed_current = json.loads(serialized["call_2"])
    current_email = replayed_current["_meta"]["notification_persistent"]["email"]
    assert [e["id"] for e in current_email["emails"]] == ["B"]
    # A must never resurface anywhere in the newest block either.
    assert "A" not in [e["id"] for e in current_email["emails"]]


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_transition_to_zero_unread_suppresses_all_historical_email_content(outputs):
    """Read/dismiss to zero must leave no historical email content
    model-visible after full replay — the explicit clear tombstone is the
    newest state and every earlier nonempty snapshot is fully removed."""
    nonempty = ToolResultBlock(
        id="call_1",
        name="email",
        content=_email_snapshot_content(
            email_ids=["A"],
            emails=[_email_message("A", sender="human", message="secret body")],
            count=1,
            newest_received_at="2026-07-06T07:00:00Z",
        ),
    )
    cleared = ToolResultBlock(id="call_2", name="email", content=_email_cleared_content())
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="email", args={})])
    iface.add_tool_results([nonempty])
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="email", args={})])
    iface.add_tool_results([cleared])

    serialized = outputs(iface)

    replayed_older = json.loads(serialized["call_1"])
    assert "notification_persistent" not in replayed_older.get("_meta", {})
    assert "secret body" not in serialized["call_1"]
    assert "secret body" not in serialized["call_2"]

    replayed_cleared = json.loads(serialized["call_2"])
    email_state = replayed_cleared["_meta"]["notification_persistent"]["email"]
    assert email_state.get("cleared") is True


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_correlated_snapshot_fields_all_come_from_one_authoritative_block(outputs):
    """`count`, `newest_received_at`, `context_comment`, `email_ids`, and
    `emails` must all come intact from the SAME snapshot — never a partial
    cross-history splice (the exact honesty violation of the rejected
    per-ID candidate, which left an old `count`/timestamp standing next to a
    pruned id list)."""
    older = ToolResultBlock(
        id="call_1",
        name="email",
        content=_email_snapshot_content(
            email_ids=["A", "B"],
            emails=[
                _email_message("A", sender="human", message="message A"),
                _email_message("B", sender="human", message="message B"),
            ],
            count=2,
            newest_received_at="2026-07-06T07:00:00Z",
        ),
    )
    current = ToolResultBlock(
        id="call_2",
        name="email",
        content=_email_snapshot_content(
            email_ids=["B"],
            emails=[_email_message("B", sender="human", message="message B")],
            count=1,
            newest_received_at="2026-07-06T08:00:00Z",
        ),
    )
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="email", args={})])
    iface.add_tool_results([older])
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="email", args={})])
    iface.add_tool_results([current])

    serialized = outputs(iface)

    current_email = json.loads(serialized["call_2"])["_meta"]["notification_persistent"]["email"]
    assert current_email["count"] == 1
    assert current_email["newest_received_at"] == "2026-07-06T08:00:00Z"
    assert current_email["email_ids"] == ["B"]
    assert "context_comment" in current_email
    assert len(current_email["emails"]) == 1


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_stale_email_snapshot_does_not_masquerade_as_current_sender(outputs):
    """Reproduces the refresh/full-history-replay incident: a historical
    ``notification_persistent.email`` block for ``email-1`` carries the WRONG
    sender plus appended unrelated content, while a LATER block for the SAME
    email id carries the correct, producer-verified sender/body. The stale
    WHOLE block must be removed in full, not merely have its id fields
    patched."""
    stale = ToolResultBlock(
        id="call_1",
        name="email",
        content=_email_snapshot_content(
            email_ids=["email-1"],
            emails=[
                _email_message(
                    "email-1",
                    sender="wrong-sender@example.com",
                    message="Full body. Also: unrelated SDK claim bolted on by mistake.",
                )
            ],
            count=1,
            newest_received_at="2026-07-06T07:00:00Z",
        ),
    )
    current = ToolResultBlock(
        id="call_2",
        name="email",
        content=_email_snapshot_content(
            email_ids=["email-1"],
            emails=[
                _email_message(
                    "email-1", sender="right-sender@example.com", message="Full body."
                )
            ],
            count=1,
            newest_received_at="2026-07-06T07:00:00Z",
        ),
    )
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="email", args={})])
    iface.add_tool_results([stale])
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="email", args={})])
    iface.add_tool_results([current])

    serialized = outputs(iface)

    replayed_stale = json.loads(serialized["call_1"])
    assert "notification_persistent" not in replayed_stale.get("_meta", {}), (
        "stale historical email-1 snapshot with the wrong sender survived "
        "full-history replay"
    )

    replayed_current = json.loads(serialized["call_2"])
    current_email = replayed_current["_meta"]["notification_persistent"]["email"]
    assert current_email["emails"][0]["from"] == "right-sender@example.com"
    assert current_email["emails"][0]["message"] == "Full body."


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_email_snapshot_coexists_with_delta_lane_previous_block(outputs):
    """A block sharing the ``notification_persistent`` envelope with a
    Telegram delta-lane payload must lose only the stale ``.email`` child;
    the sibling ``mcp.telegram`` payload and its ``previous_block`` continuity
    must survive untouched."""
    mixed_old = ToolResultBlock(
        id="call_1",
        name="mixed",
        content={
            "ok": True,
            "_meta": {
                "notification_persistent": {
                    "email": {
                        "email_ids": ["A"],
                        "emails": [_email_message("A", sender="human", message="old")],
                        "count": 1,
                    },
                    "mcp": {
                        "telegram": {
                            "messages": [{"id": "t1", "text": "hi"}],
                            "previous_block": {"is_first_block": True, "tool_result_id": None},
                        }
                    },
                }
            },
        },
    )
    mixed_new = ToolResultBlock(
        id="call_2",
        name="mixed",
        content={
            "ok": True,
            "_meta": {
                "notification_persistent": {
                    "email": {
                        "email_ids": ["B"],
                        "emails": [_email_message("B", sender="human", message="new")],
                        "count": 1,
                    },
                }
            },
        },
    )
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="mixed", args={})])
    iface.add_tool_results([mixed_old])
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="mixed", args={})])
    iface.add_tool_results([mixed_new])

    serialized = outputs(iface)

    replayed_old = json.loads(serialized["call_1"])
    persistent_old = replayed_old["_meta"]["notification_persistent"]
    assert "email" not in persistent_old, "stale email child must be removed"
    assert persistent_old["mcp"]["telegram"]["previous_block"] == {
        "is_first_block": True,
        "tool_result_id": None,
    }
    assert persistent_old["mcp"]["telegram"]["messages"] == [{"id": "t1", "text": "hi"}]

    replayed_new = json.loads(serialized["call_2"])
    assert replayed_new["_meta"]["notification_persistent"]["email"]["email_ids"] == ["B"]


def _combined_state_iface(*, current_email_content: dict) -> ChatInterface:
    """Older unread email + historical ``agent_meta``/``guidance`` and
    ``notifications``/``notification_guidance`` (both #918 families) on the
    SAME older block, followed by the current authoritative email state on a
    newer block that carries none of those four keys. Mirrors the shape a
    real multi-turn conversation produces: one older tool result stamped
    with the full ``_meta`` envelope, a later one narrower."""
    older = ToolResultBlock(
        id="call_1",
        name="email",
        content={
            "ok": True,
            "_meta": {
                **_ALL_TRANSIENT_META,
                "notification_persistent": {
                    "email": {
                        "email_ids": ["older-1"],
                        "emails": [
                            _email_message("older-1", sender="human", message="older unread body")
                        ],
                        "count": 1,
                    },
                },
            },
        },
    )
    current = ToolResultBlock(id="call_2", name="email", content=current_email_content)
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="email", args={})])
    iface.add_tool_results([older])
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="email", args={})])
    iface.add_tool_results([current])
    return iface


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
@pytest.mark.parametrize(
    "current_state",
    [
        pytest.param("cleared", id="newest_zero_tombstone"),
        pytest.param("changed", id="newest_changed_whole_snapshot"),
    ],
)
def test_combined_state_post_918_families_preserved_alongside_email_projection(
    outputs, current_state
):
    """Post-#918 combined-state regression (mandatory follow-up): an older
    block carries a live email snapshot AND historical ``agent_meta``/
    ``guidance``/``notifications``/``notification_guidance`` — the four
    families #918 now preserves verbatim on EVERY historical holder, not
    just the newest. A newer block then either clears email to zero (an
    explicit tombstone) or replaces it with a different whole snapshot.

    Full-history replay must do exactly two things and nothing else:
      1. Remove the older block's stale ``.notification_persistent.email``
         child in full (the one remaining projection this module performs).
      2. Leave the older block's ``agent_meta``/``guidance``/
         ``notifications``/``notification_guidance`` keys completely
         untouched — #918 removed the family-filter wire-level strip
         entirely, so even though a newer block exists, the OLDER holder's
         own copies of these four keys must still serialize verbatim.

    Exercises all four direct wire converters (parametrized) plus Claude
    Code's ``_render_conversation`` explicitly below, so every direct
    serializer in the codebase is covered by this one scenario.
    """
    if current_state == "cleared":
        current_content = _email_cleared_content()
    else:
        current_content = _email_snapshot_content(
            email_ids=["newer-1"],
            emails=[_email_message("newer-1", sender="human", message="newer unread body")],
            count=1,
            newest_received_at="2026-07-06T08:00:00Z",
        )
    iface = _combined_state_iface(current_email_content=current_content)

    serialized = outputs(iface)

    replayed_older = json.loads(serialized["call_1"])
    older_meta = replayed_older["_meta"]
    # (1) Stale whole email child removed in full from the older holder.
    assert "notification_persistent" not in older_meta or "email" not in older_meta.get(
        "notification_persistent", {}
    ), "the older block's stale email snapshot must be fully removed"
    # (2) Every #918 family untouched on the OLDER holder — #918 preserves
    # every historical holder's content verbatim; there is no "only newest
    # family survives" wire-level strip to interact with the email
    # projection at all.
    assert older_meta["agent_meta"] == _ALL_TRANSIENT_META["agent_meta"]
    assert older_meta["guidance"] == _ALL_TRANSIENT_META["guidance"]
    assert older_meta["notifications"] == _ALL_TRANSIENT_META["notifications"]
    assert older_meta["notification_guidance"] == _ALL_TRANSIENT_META["notification_guidance"]

    replayed_current = json.loads(serialized["call_2"])
    current_email = replayed_current["_meta"]["notification_persistent"]["email"]
    if current_state == "cleared":
        assert current_email["cleared"] is True
        assert "emails" not in current_email and "email_ids" not in current_email
    else:
        assert current_email["email_ids"] == ["newer-1"]
        assert "cleared" not in current_email


@pytest.mark.parametrize(
    "current_state",
    [
        pytest.param("cleared", id="newest_zero_tombstone"),
        pytest.param("changed", id="newest_changed_whole_snapshot"),
    ],
)
def test_combined_state_claude_code_render_preserves_families_and_projects_email(
    current_state,
):
    """Claude Code coverage for the same mandatory combined-state scenario:
    ``_render_conversation`` must project only the stale email child while
    still rendering the older holder's historical #918 family content
    (proven here via presence of representative substrings, matching this
    module's existing Claude Code test style, which asserts on rendered text
    rather than parsed JSON)."""
    from lingtai.llm.claude_code.adapter import ClaudeCodeChatSession

    if current_state == "cleared":
        current_content = _email_cleared_content()
    else:
        current_content = _email_snapshot_content(
            email_ids=["newer-1"],
            emails=[_email_message("newer-1", sender="human", message="newer unread body")],
            count=1,
            newest_received_at="2026-07-06T08:00:00Z",
        )
    iface = _combined_state_iface(current_email_content=current_content)
    session = ClaudeCodeChatSession(
        adapter=None,
        model="sonnet",
        system_prompt="",
        tools=[],
        interface=iface,
        context_window=100_000,
    )

    rendered = session._render_conversation()

    # Stale older email body is gone from the rendered transcript.
    assert "older unread body" not in rendered
    # The older holder's #918 families still render verbatim (elapsed_ms=5
    # only appears in the older _ALL_TRANSIENT_META agent_meta; email-1 is
    # the older notifications family's email id).
    assert "elapsed_ms" in rendered and "5" in rendered
    assert "email-1" in rendered
    if current_state == "cleared":
        assert '"cleared": true' in rendered or '"cleared":true' in rendered.replace(" ", "")
    else:
        assert "newer unread body" in rendered


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_malformed_notification_persistent_and_email_fields_do_not_crash(outputs):
    """Malformed non-dict ``notification_persistent``, a non-dict ``email``
    value, and malformed id fields must never crash replay — every
    intermediate value is guarded with ``isinstance``."""
    malformed_persistent_string = ToolResultBlock(
        id="call_1", name="x", content={"ok": True, "_meta": {"notification_persistent": "not-a-dict"}}
    )
    malformed_persistent_list = ToolResultBlock(
        id="call_2", name="x", content={"ok": True, "_meta": {"notification_persistent": ["a", "b"]}}
    )
    malformed_email_none = ToolResultBlock(
        id="call_3",
        name="x",
        content={"ok": True, "_meta": {"notification_persistent": {"email": None}}},
    )
    malformed_email_fields = ToolResultBlock(
        id="call_4",
        name="email",
        content={
            "ok": True,
            "_meta": {
                "notification_persistent": {
                    "email": {"email_ids": "not-a-list", "emails": "not-a-list-either"}
                }
            },
        },
    )
    iface = ChatInterface()
    iface.add_user_message("start")
    for cid, block in (
        ("call_1", malformed_persistent_string),
        ("call_2", malformed_persistent_list),
        ("call_3", malformed_email_none),
        ("call_4", malformed_email_fields),
    ):
        iface.add_assistant_message([ToolCallBlock(id=cid, name=block.name, args={})])
        iface.add_tool_results([block])

    # Must not raise.
    serialized = outputs(iface)
    assert set(serialized) == {"call_1", "call_2", "call_3", "call_4"}
    # The one block with a genuinely dict-shaped (even if malformed-fielded)
    # email child is the sole newest holder and keeps its content verbatim.
    replayed_4 = json.loads(serialized["call_4"])
    assert replayed_4["_meta"]["notification_persistent"]["email"] == {
        "email_ids": "not-a-list",
        "emails": "not-a-list-either",
    }


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_duplicate_and_out_of_order_email_ids_are_safe(outputs):
    """Duplicate ids within one snapshot, and ids repeating across snapshots
    in any combination, must not crash and must resolve to exactly the
    newest whole block — the final valid-schema behavior is explicit (newest
    wire-order block wins), not inferred."""
    dup_within_block = ToolResultBlock(
        id="call_1",
        name="email",
        content=_email_snapshot_content(
            email_ids=["A", "A", "B"],
            emails=[
                _email_message("A", sender="human", message="first"),
                _email_message("A", sender="human", message="dup"),
                _email_message("B", sender="human", message="b"),
            ],
            count=2,
            newest_received_at="2026-07-06T07:00:00Z",
        ),
    )
    later_same_ids_different_order = ToolResultBlock(
        id="call_2",
        name="email",
        content=_email_snapshot_content(
            email_ids=["B", "A"],
            emails=[
                _email_message("B", sender="human", message="b-updated"),
                _email_message("A", sender="human", message="a-updated"),
            ],
            count=2,
            newest_received_at="2026-07-06T09:00:00Z",
        ),
    )
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="email", args={})])
    iface.add_tool_results([dup_within_block])
    iface.add_assistant_message([ToolCallBlock(id="call_2", name="email", args={})])
    iface.add_tool_results([later_same_ids_different_order])

    serialized = outputs(iface)  # must not raise

    replayed_1 = json.loads(serialized["call_1"])
    assert "notification_persistent" not in replayed_1.get("_meta", {})
    replayed_2 = json.loads(serialized["call_2"])
    current_email = replayed_2["_meta"]["notification_persistent"]["email"]
    assert current_email["email_ids"] == ["B", "A"]
    assert current_email["emails"][0]["message"] == "b-updated"
    assert current_email["emails"][1]["message"] == "a-updated"


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_canonical_dict_and_json_string_content_equivalent_after_every_renderer(outputs):
    """Canonical dict-shaped and JSON-string-shaped ``ToolResultBlock``
    content must remain byte/value-equivalent after every renderer: a
    stale whole email snapshot is removed the same way regardless of the
    canonical content's on-wire representation."""
    dict_content = _email_snapshot_content(
        email_ids=["A"],
        emails=[_email_message("A", sender="human", message="stale")],
        count=1,
        newest_received_at="2026-07-06T07:00:00Z",
    )
    string_content = json.dumps(dict_content, default=str)
    current = _email_snapshot_content(
        email_ids=["B"],
        emails=[_email_message("B", sender="human", message="current")],
        count=1,
        newest_received_at="2026-07-06T08:00:00Z",
    )

    for stale_content in (dict_content, string_content):
        iface = ChatInterface()
        iface.add_user_message("start")
        iface.add_assistant_message([ToolCallBlock(id="call_1", name="email", args={})])
        iface.add_tool_results(
            [ToolResultBlock(id="call_1", name="email", content=stale_content)]
        )
        iface.add_assistant_message([ToolCallBlock(id="call_2", name="email", args={})])
        iface.add_tool_results([ToolResultBlock(id="call_2", name="email", content=current)])

        serialized = outputs(iface)
        replayed_stale = json.loads(serialized["call_1"])
        assert "notification_persistent" not in replayed_stale.get("_meta", {})

    # Non-mutating regardless of shape: canonical content is untouched.
    assert "notification_persistent" in dict_content["_meta"]
    assert json.loads(string_content)["_meta"]["notification_persistent"]["email"]


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


def test_claude_code_render_filters_stale_whole_email_snapshot():
    """B3 regression: an earlier candidate updated the four ``to_*``
    converters but left Claude Code's ``_render_conversation`` unfiltered
    because ``filter_stale_timely_transient`` defaulted the email argument to
    ``None`` there. The wrong-sender/appended-body incident must not
    reproduce on this renderer either."""
    from lingtai.llm.claude_code.adapter import ClaudeCodeChatSession

    stale = _email_snapshot_content(
        email_ids=["email-1"],
        emails=[
            _email_message(
                "email-1",
                sender="wrong-sender@example.com",
                message="Full body. Also: unrelated SDK claim bolted on by mistake.",
            )
        ],
        count=1,
        newest_received_at="2026-07-06T07:00:00Z",
    )
    current = _email_snapshot_content(
        email_ids=["email-1"],
        emails=[
            _email_message("email-1", sender="right-sender@example.com", message="Full body.")
        ],
        count=1,
        newest_received_at="2026-07-06T07:00:00Z",
    )
    iface = _iface_with_two_results(stale, current)
    session = ClaudeCodeChatSession(
        adapter=None,
        model="sonnet",
        system_prompt="",
        tools=[],
        interface=iface,
        context_window=100_000,
    )

    rendered = session._render_conversation()

    assert "wrong-sender@example.com" not in rendered
    assert "unrelated SDK claim" not in rendered
    assert "right-sender@example.com" in rendered
    # Non-mutating: canonical content still carries the stale copy.
    assert stale["_meta"]["notification_persistent"]["email"]["emails"][0]["from"] == (
        "wrong-sender@example.com"
    )


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
