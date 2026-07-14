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
that replaces a historical tool-result BODY during a rebuild.
``skeletonize_notification_holder`` (moving/clearing a synthesized payload)
is append-only: it only releases a holder from LIVE tracking and never
mutates its recorded content, synthesized or not — see the dedicated
synthesized-holder section below for direct coverage of this across all five
renderers plus Codex ordinary/manual-rebuild/forced-epoch-reset replay.
The durable ``notification_persistent`` lane and permanent ``tool_meta`` were
never filtered and remain untouched either way.

``notification_persistent.email`` is a SEPARATE, narrower whole-snapshot
lane (see the dedicated section below): it is an atomic current-unread
snapshot, not timely-transient-family state and not a set of independent
per-id records. Full-history replay is a straight pass-through for this lane
too — no converter strips, filters, or selects across email holders. Which
child is CURRENT is a READING CONVENTION the model applies (the newest whole
child in wire order — a live snapshot or an explicit clear tombstone — is
authoritative; see ``lingtai.kernel.meta_block.newest_email_snapshot_holder``),
exactly like the two timely-transient families above. Every older snapshot
(live or cleared) remains present, in full, in every replay.

These tests are content-free where possible: they assert key structure, not
tool-result bodies.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lingtai.kernel.llm.interface import (
    ChatInterface,
    ToolCallBlock,
    ToolResultBlock,
)
from lingtai.kernel.meta_block import (
    build_email_persistent_cleared_marker,
    newest_email_snapshot_holder,
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
    # The shared converters read `ToolResultBlock.content` directly for
    # EVERY `_meta` family, including `notification_persistent.email` — no
    # newest-holder/filtering helper sits between canonical content and the
    # provider wire. The removed helper API surface stays absent, protecting
    # against a regression that reintroduces family-level or email
    # whole-snapshot filtering at the converter layer.
    import lingtai.llm.interface_converters as ic

    assert not hasattr(ic, "filter_stale_timely_transient")
    assert not hasattr(ic, "timely_transient_newest_holders")
    assert not hasattr(ic, "TIMELY_TRANSIENT_META_FAMILIES")
    assert not hasattr(ic, "_render_full_history_result")
    assert not hasattr(ic, "_drop_stale_email_snapshot")
    assert not hasattr(ic, "newest_email_snapshot_holder")


# ---------------------------------------------------------------------------
# Email whole-snapshot append-only chronology.
#
# Email is a producer-owned ATOMIC current-unread snapshot (see
# ``tools/email/primitives.py::_unread_notification_context`` /
# ``meta_block.py::_build_email_notification_persistent_payload`` /
# ``LICC_NOTIFICATION_CONTRACT.md``), not an append-only collection of
# independent per-id records. Correlated fields (``count``,
# ``newest_received_at``, ``context_comment``, ``email_ids``, ``emails``) all
# describe ONE snapshot together and must never be spliced against a
# different snapshot.
#
# Under the literal provider-context rebuild/replay invariant, full-history
# replay is a straight pass-through for this lane exactly like every other
# ``_meta`` family: NO converter strips, filters, deduplicates, or selects
# across ``notification_persistent.email`` holders. Every historical
# snapshot (a live snapshot, or an explicit ``{"cleared": True, ...}``
# tombstone once unread count reaches zero — see
# ``meta_block.build_email_persistent_cleared_marker``) survives every
# replay, unchanged, forever. Which child is CURRENT is a READING CONVENTION
# the model applies: the newest whole child in wire order is authoritative
# (``lingtai.kernel.meta_block.newest_email_snapshot_holder``) — a fact about
# reading order, never a wire-level deletion of the others.
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
def test_older_whole_snapshot_survives_replay_after_id_drops_out(outputs):
    """`[A, B]` then current `[B]`: the earlier WHOLE email child (still
    naming A and B) survives full-history replay byte/value-for-value —
    replay never resurrects, drops, or edits it. The newest holder's own
    snapshot correctly reflects only `[B]`."""
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
    older_email = replayed_older["_meta"]["notification_persistent"]["email"]
    assert [e["id"] for e in older_email["emails"]] == ["A", "B"], (
        "the earlier whole email snapshot must survive full-history replay "
        "unchanged, including id A"
    )
    replayed_current = json.loads(serialized["call_2"])
    current_email = replayed_current["_meta"]["notification_persistent"]["email"]
    assert [e["id"] for e in current_email["emails"]] == ["B"]


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_transition_to_zero_unread_appends_tombstone_without_erasing_history(outputs):
    """Read/dismiss to zero appends an explicit clear tombstone as the
    NEWEST state; the earlier nonempty snapshot remains fully present in
    replay — the tombstone makes it non-authoritative by reading order, not
    by deletion."""
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
    assert "secret body" in serialized["call_1"], (
        "the older nonempty snapshot must remain in full-history replay"
    )
    older_email = replayed_older["_meta"]["notification_persistent"]["email"]
    assert older_email["emails"][0]["message"] == "secret body"

    replayed_cleared = json.loads(serialized["call_2"])
    email_state = replayed_cleared["_meta"]["notification_persistent"]["email"]
    assert email_state.get("cleared") is True

    # Reading convention: the newest holder in wire order is the tombstone,
    # so it — not the earlier nonempty snapshot — is authoritative.
    newest = newest_email_snapshot_holder(iface)
    assert newest.id == "call_2"


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_correlated_snapshot_fields_all_come_from_one_authoritative_block(outputs):
    """`count`, `newest_received_at`, `context_comment`, `email_ids`, and
    `emails` must all come intact from the SAME snapshot on the newest
    holder — never a partial cross-history splice (the exact honesty
    violation of the rejected per-ID candidate, which left an old
    `count`/timestamp standing next to a pruned id list). The older
    snapshot's own correlated fields must also stay internally consistent
    in replay."""
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

    older_email = json.loads(serialized["call_1"])["_meta"]["notification_persistent"]["email"]
    assert older_email["count"] == 2
    assert older_email["newest_received_at"] == "2026-07-06T07:00:00Z"
    assert older_email["email_ids"] == ["A", "B"]

    current_email = json.loads(serialized["call_2"])["_meta"]["notification_persistent"]["email"]
    assert current_email["count"] == 1
    assert current_email["newest_received_at"] == "2026-07-06T08:00:00Z"
    assert current_email["email_ids"] == ["B"]
    assert "context_comment" in current_email
    assert len(current_email["emails"]) == 1


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_superseded_email_snapshot_is_readable_but_not_authoritative(outputs):
    """A historical ``notification_persistent.email`` block for ``email-1``
    carries a stale sender/body, while a LATER block for the SAME email id
    carries the correct, producer-verified sender/body (e.g. after a
    correction). Both survive full-history replay unchanged; the model's
    reading convention (newest wins) is what makes the later one
    authoritative, not a wire-level strip of the earlier one."""
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

    # Both survive replay verbatim.
    assert "wrong-sender@example.com" in serialized["call_1"]
    replayed_current = json.loads(serialized["call_2"])
    current_email = replayed_current["_meta"]["notification_persistent"]["email"]
    assert current_email["emails"][0]["from"] == "right-sender@example.com"
    assert current_email["emails"][0]["message"] == "Full body."

    # Reading convention: the newest wire-order holder is authoritative.
    newest = newest_email_snapshot_holder(iface)
    assert newest.id == "call_2"


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_email_snapshot_coexists_with_delta_lane_previous_block(outputs):
    """A block sharing the ``notification_persistent`` envelope with a
    Telegram delta-lane payload preserves BOTH the ``email`` child and the
    sibling ``mcp.telegram`` payload (with its ``previous_block`` continuity)
    in full-history replay — no lane is dropped from any holder."""
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
    assert persistent_old["email"]["email_ids"] == ["A"]
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
def test_combined_state_all_families_and_email_preserved_on_every_holder(
    outputs, current_state
):
    """Post-#918 combined-state regression, restated for the literal
    no-strip invariant: an older block carries a live email snapshot AND
    historical ``agent_meta``/``guidance``/``notifications``/
    ``notification_guidance`` — the four families #918 preserves verbatim on
    EVERY historical holder. A newer block then either clears email to zero
    (an explicit tombstone) or replaces it with a different whole snapshot.

    Full-history replay must do exactly ONE thing: leave every holder's
    content — the older block's email child included — completely
    untouched. Exercises all four direct wire converters (parametrized).
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
    # The older holder's email child survives replay in full.
    older_email = older_meta["notification_persistent"]["email"]
    assert older_email["email_ids"] == ["older-1"]
    assert older_email["emails"][0]["message"] == "older unread body"
    # Every #918 family untouched on the OLDER holder too.
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

    # Reading convention: the newest wire-order holder is authoritative.
    newest = newest_email_snapshot_holder(iface)
    assert newest.id == "call_2"


@pytest.mark.parametrize(
    "current_state",
    [
        pytest.param("cleared", id="newest_zero_tombstone"),
        pytest.param("changed", id="newest_changed_whole_snapshot"),
    ],
)
def test_combined_state_claude_code_render_preserves_all_families_and_email(
    current_state,
):
    """Claude Code coverage for the same combined-state scenario:
    ``_render_conversation`` must render BOTH the older holder's stale email
    child and the newer authoritative state — no lane is dropped from any
    holder (proven here via presence of representative substrings, matching
    this module's existing Claude Code test style, which asserts on
    rendered text rather than parsed JSON)."""
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

    # The older holder's stale email body survives the rendered transcript.
    assert "older unread body" in rendered
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
    intermediate value is guarded with ``isinstance``, and every block's
    content still serializes as-is."""
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
    # Every block's content, however malformed-shaped, serializes verbatim.
    replayed_4 = json.loads(serialized["call_4"])
    assert replayed_4["_meta"]["notification_persistent"]["email"] == {
        "email_ids": "not-a-list",
        "emails": "not-a-list-either",
    }
    replayed_3 = json.loads(serialized["call_3"])
    assert replayed_3["_meta"]["notification_persistent"]["email"] is None


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_duplicate_and_out_of_order_email_ids_survive_replay(outputs):
    """Duplicate ids within one snapshot, and ids repeating across snapshots
    in any combination, must not crash and must serialize every holder
    as-is — the newest wire-order block is the reading-convention winner,
    not a wire-level survivor of a strip."""
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
    older_email = replayed_1["_meta"]["notification_persistent"]["email"]
    assert older_email["email_ids"] == ["A", "A", "B"]
    replayed_2 = json.loads(serialized["call_2"])
    current_email = replayed_2["_meta"]["notification_persistent"]["email"]
    assert current_email["email_ids"] == ["B", "A"]
    assert current_email["emails"][0]["message"] == "b-updated"
    assert current_email["emails"][1]["message"] == "a-updated"

    newest = newest_email_snapshot_holder(iface)
    assert newest.id == "call_2"


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_canonical_dict_and_json_string_content_equivalent_after_every_renderer(outputs):
    """Canonical dict-shaped and JSON-string-shaped ``ToolResultBlock``
    content must remain byte/value-equivalent after every renderer: an older
    whole email snapshot survives replay the same way regardless of the
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
        stale_email = replayed_stale["_meta"]["notification_persistent"]["email"]
        assert stale_email["emails"][0]["message"] == "stale"

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


def test_claude_code_render_preserves_stale_whole_email_snapshot():
    """B3 regression, restated: an earlier candidate updated the four
    ``to_*`` converters but left Claude Code's ``_render_conversation``
    unfiltered because a helper defaulted the email argument to ``None``
    there. Under the literal no-strip invariant the correct behavior is the
    OPPOSITE of the old assertion: this renderer must render the older
    holder's content exactly like the four direct converters do — no
    filtering, on any renderer."""
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

    assert "wrong-sender@example.com" in rendered
    assert "unrelated SDK claim" in rendered
    assert "right-sender@example.com" in rendered
    # Non-mutating: canonical content still carries the stale copy.
    assert stale["_meta"]["notification_persistent"]["email"]["emails"][0]["from"] == (
        "wrong-sender@example.com"
    )
    # Reading convention: the newest wire-order holder is authoritative.
    newest = newest_email_snapshot_holder(iface)
    assert newest.id == "call_2"


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


def test_ordinary_continuation_manual_rebuild_and_forced_epoch_all_see_same_summary():
    """B2 regression: after a real ``summarize`` replacement, ordinary
    continuation BEFORE any reset, a manual ``request_history_rebuild()``, and a
    forced epoch reset must all expose the SAME summarized semantics for
    ``call_1`` — the per-``call_id`` output freeze must never replay a
    pre-summarize raw string once canonical content has moved on. Also covers
    orphan-placeholder -> real-output pairing for ``call_1`` (it is answered
    with a placeholder-shaped open call before the real result lands) so
    baseline/placeholder handling cannot mask the divergence.
    """
    transport, session = _tool_loop_session()

    # Turn 2: answer call_1 with a large raw payload (freezes call_1 raw).
    raw_content = {"raw": "x" * 200, "_meta": {"tool_meta": {"id": "call_1"}}}
    session.send([_result_block("call_1", raw_content)])  # emits call_2, freezes call_1 raw

    # Explicit summarize: replace call_1's canonical body with the real marker
    # shape (mirrors the summarize intrinsic — a fresh dict, no _meta carried
    # over).
    marker = {
        "artifact": "lingtai_agent_summarized_result",
        "tool_call_id": "call_1",
        "agent_summary": "the digest",
        "status": "done",
    }
    for entry in session._interface.entries:
        for idx, blk in enumerate(entry.content or []):
            if isinstance(blk, ToolResultBlock) and blk.id == "call_1":
                entry.content[idx].content = marker

    # (a) Ordinary continuation BEFORE any epoch reset/rebuild: answer call_2.
    # call_1's canonical content changed (raw -> summary marker) since it was
    # frozen, so the freshly converted full input for THIS turn no longer
    # matches the previously recorded baseline for call_1 — a prefix mismatch
    # is the honest, expected consequence (never a silent replay of the stale
    # frozen raw string). Whichever request mode results, the full converted
    # input for call_1 must already carry the summarized semantics, not the
    # frozen pre-summarize raw payload.
    session.send([_result_block("call_2", {"done": True})])
    ordinary_outputs = _replay_outputs(transport.sent_frames[-1])
    if "call_1" in ordinary_outputs:
        ordinary_call_1 = json.loads(ordinary_outputs["call_1"])
        assert ordinary_call_1["artifact"] == "lingtai_agent_summarized_result"
        assert ordinary_call_1["agent_summary"] == "the digest"
        assert "raw" not in ordinary_call_1

    # (b) Manual rebuild: forces a full replay through the shared converter.
    assert session.request_history_rebuild() is True
    session.send([_result_block("call_3", {"done": True})])
    rebuild_outputs = _replay_outputs(transport.sent_frames[-1])
    rebuild_call_1 = json.loads(rebuild_outputs["call_1"])
    assert rebuild_call_1["artifact"] == "lingtai_agent_summarized_result"
    assert rebuild_call_1["agent_summary"] == "the digest"
    assert "raw" not in rebuild_call_1

    # (c) Forced epoch reset (turn_count / summarize_delayed-style clear): the
    # very next full replay must show the identical summarized semantics.
    session._reset_ws_epoch("turn_count")
    session.send([_result_block("call_4", {"done": True})])
    forced_outputs = _replay_outputs(transport.sent_frames[-1])
    forced_call_1 = json.loads(forced_outputs["call_1"])
    assert forced_call_1["artifact"] == "lingtai_agent_summarized_result"
    assert forced_call_1["agent_summary"] == "the digest"
    assert "raw" not in forced_call_1

    # Canonical content changed ONLY by the explicit summarize replacement —
    # never re-mutated by any of the three replays above.
    for entry in session._interface.entries:
        for blk in entry.content or []:
            if isinstance(blk, ToolResultBlock) and blk.id == "call_1":
                assert blk.content == marker


def test_frozen_output_refreezes_and_forces_full_replay_when_canonical_content_changes():
    # The per-call_id output freeze (unrelated to transient-metadata
    # preservation) keeps an already-sent function_call_output.output string
    # byte-identical across turns ONLY while the freshly converted canonical
    # output stays byte-identical to what was frozen (see
    # ``_freeze_responses_outputs``). ``agent_meta``/``guidance`` promotion is
    # append-only (``attach_active_runtime``): the kernel never pops a prior
    # holder's snapshot in place. This test simulates the defensive/
    # hypothetical case where an older tool result's canonical content DOES
    # change in place anyway (no real code path does this today — mirrors
    # ``test_frozen_output_refreezes_when_canonical_content_changes`` in
    # ``test_codex_ws_session.py``): the freeze must never paper over the
    # change by replaying the stale cached string. It refreezes to the new
    # value, and the honest, expected consequence is a prefix mismatch
    # (``ws_full``) carrying the refreshed call_1 body, not a silent
    # ``ws_incremental`` replay of the pre-change output.
    transport, session = _tool_loop_session()
    call_1_content = {"ok": True, "_meta": dict(_ALL_TRANSIENT_META)}
    session.send([_result_block("call_1", call_1_content)])  # emits call_2

    # Mutate call_1's canonical content in place (no epoch reset in between).
    call_1_content["_meta"].pop("agent_meta", None)
    call_1_content["_meta"].pop("guidance", None)

    result = session.send([_result_block("call_2", {"done": True})])

    assert result.usage.extra["codex_request_mode"] == "ws_full"
    replayed = _replay_outputs(transport.sent_frames[-1])
    # The full replay carries BOTH the refreshed call_1 body and call_2.
    assert set(replayed) == {"call_1", "call_2"}
    call_1_replayed = json.loads(replayed["call_1"])
    assert "agent_meta" not in call_1_replayed["_meta"]
    assert "guidance" not in call_1_replayed["_meta"]
    # The still-present families on call_1 (unaffected by the mutation)
    # confirm this is a refreshed re-serialization, not an empty/reset body.
    assert call_1_replayed["_meta"]["notifications"] == _ALL_TRANSIENT_META["notifications"]

    # Canonical content reflects only the explicit in-place mutation above —
    # the freeze itself never mutates canonical history.
    assert "agent_meta" not in call_1_content["_meta"]
    assert "guidance" not in call_1_content["_meta"]


# ---------------------------------------------------------------------------
# Synthesized notification-holder append-only guarantee — a synthesized
# IDLE/ASLEEP-wake pair carrying a live email persistent snapshot must
# survive verbatim across every renderer and every replay path once it is
# released from live tracking (skeletonize_notification_holder). No
# in-place skeletonization, no wire-strip: only NEW append-only
# snapshot/tombstone records may represent newer state.
# ---------------------------------------------------------------------------


def _synthesized_email_holder(*, email_id: str, message: str) -> dict:
    """The real shape `_inject_notification_pair` builds for an IDLE/ASLEEP
    wake delivering an unread email (`base_agent/__init__.py`)."""
    return {
        "_synthesized": True,
        "_meta": {
            "notifications": {"email": {"data": {"email_ids": [email_id]}}},
            "notification_guidance": {"ref": "meta_guidance.notification_handling"},
            "notification_persistent": {
                "email": {
                    "email_ids": [email_id],
                    "emails": [{"id": email_id, "message": message}],
                }
            },
        },
        "injection_seq": 1,
    }


def _iface_with_synthesized_email_a_then_b_then_zero() -> ChatInterface:
    """Three synthesized (call, result) pairs: email A, then B, then a clear
    tombstone — mirroring three successive IDLE/ASLEEP wake deliveries with
    `skeletonize_notification_holder` releasing each prior holder from live
    tracking in between (append-only, never mutating the released holder).
    """
    iface = ChatInterface()
    iface.add_user_message("start")

    holder_a = _synthesized_email_holder(email_id="email-A", message="body A")
    iface.add_assistant_message([ToolCallBlock(id="notif_a", name="notification", args={"action": "check"})])
    iface.add_tool_results(
        [ToolResultBlock(id="notif_a", name="notification", content=holder_a, synthesized=True)]
    )
    from lingtai.kernel.meta_block import skeletonize_notification_holder

    agent = SimpleNamespace(_notification_live_holder=holder_a)
    skeletonize_notification_holder(agent)  # release only — no mutation

    holder_b = _synthesized_email_holder(email_id="email-B", message="body B")
    iface.add_assistant_message([ToolCallBlock(id="notif_b", name="notification", args={"action": "check"})])
    iface.add_tool_results(
        [ToolResultBlock(id="notif_b", name="notification", content=holder_b, synthesized=True)]
    )
    agent._notification_live_holder = holder_b
    skeletonize_notification_holder(agent)  # release only — no mutation

    holder_zero = {
        "_synthesized": True,
        "_meta": {
            "notification_persistent": {
                "email": build_email_persistent_cleared_marker(),
            }
        },
        "injection_seq": 2,
    }
    iface.add_assistant_message([ToolCallBlock(id="notif_zero", name="notification", args={"action": "check"})])
    iface.add_tool_results(
        [ToolResultBlock(id="notif_zero", name="notification", content=holder_zero, synthesized=True)]
    )
    return iface, holder_a, holder_b, holder_zero


@pytest.mark.parametrize("outputs", _CONVERTER_OUTPUTS)
def test_synthesized_email_holders_survive_release_across_four_converters(outputs):
    iface, holder_a, holder_b, holder_zero = _iface_with_synthesized_email_a_then_b_then_zero()

    serialized = outputs(iface)

    parsed_a = json.loads(serialized["notif_a"])
    assert parsed_a["_meta"]["notification_persistent"]["email"]["emails"] == [
        {"id": "email-A", "message": "body A"}
    ]
    parsed_b = json.loads(serialized["notif_b"])
    assert parsed_b["_meta"]["notification_persistent"]["email"]["emails"] == [
        {"id": "email-B", "message": "body B"}
    ]
    parsed_zero = json.loads(serialized["notif_zero"])
    assert parsed_zero["_meta"]["notification_persistent"]["email"]["cleared"] is True

    # Release from live tracking never mutated any released holder in place.
    assert holder_a["_meta"]["notification_persistent"]["email"]["emails"] == [
        {"id": "email-A", "message": "body A"}
    ]
    assert holder_b["_meta"]["notification_persistent"]["email"]["emails"] == [
        {"id": "email-B", "message": "body B"}
    ]
    assert holder_a["_synthesized"] is True
    assert holder_b["_synthesized"] is True


def test_synthesized_email_holders_survive_release_claude_code_renderer():
    from lingtai.llm.claude_code.adapter import ClaudeCodeChatSession

    iface, holder_a, holder_b, holder_zero = _iface_with_synthesized_email_a_then_b_then_zero()
    session = ClaudeCodeChatSession(
        adapter=None,
        model="sonnet",
        system_prompt="",
        tools=[],
        interface=iface,
        context_window=100_000,
    )

    rendered = session._render_conversation()

    assert "body A" in rendered
    assert "body B" in rendered
    assert holder_a["_meta"]["notification_persistent"]["email"]["emails"] == [
        {"id": "email-A", "message": "body A"}
    ]


def test_synthesized_email_holders_survive_codex_ordinary_manual_rebuild_and_forced_epoch_reset():
    """The A/B/zero synthesized chain renders identically on an ordinary
    Codex send, an explicit `request_history_rebuild()`, and a forced
    websocket epoch reset — no skeletonization mutation on any path."""
    transport, session = _tool_loop_session()
    iface, holder_a, holder_b, holder_zero = (
        _iface_with_synthesized_email_a_then_b_then_zero()
    )
    # Splice the synthesized chain into the live session's own interface so
    # the WS replay machinery (freeze map, epoch state) exercises it.
    session._interface._entries = list(iface.entries)

    def _reasoning_free_outputs(frame):
        return {
            item["call_id"]: item["output"]
            for item in frame["input"]
            if item.get("type") == "function_call_output"
        }

    ordinary = session._frozen_responses_input(session._interface)
    ordinary_outputs = {
        item["call_id"]: item["output"]
        for item in ordinary
        if item.get("type") == "function_call_output"
    }
    assert "body A" in ordinary_outputs["notif_a"]
    assert "body B" in ordinary_outputs["notif_b"]
    assert json.loads(ordinary_outputs["notif_zero"])["_meta"][
        "notification_persistent"
    ]["email"]["cleared"] is True

    assert session.request_history_rebuild() is True
    after_manual = session._frozen_responses_input(session._interface)
    after_manual_outputs = {
        item["call_id"]: item["output"]
        for item in after_manual
        if item.get("type") == "function_call_output"
    }
    assert after_manual_outputs["notif_a"] == ordinary_outputs["notif_a"]
    assert after_manual_outputs["notif_b"] == ordinary_outputs["notif_b"]
    assert after_manual_outputs["notif_zero"] == ordinary_outputs["notif_zero"]

    session._reset_ws_epoch("test_forced_epoch_reset")
    after_reset = session._frozen_responses_input(session._interface)
    after_reset_outputs = {
        item["call_id"]: item["output"]
        for item in after_reset
        if item.get("type") == "function_call_output"
    }
    assert after_reset_outputs["notif_a"] == ordinary_outputs["notif_a"]
    assert after_reset_outputs["notif_b"] == ordinary_outputs["notif_b"]
    assert after_reset_outputs["notif_zero"] == ordinary_outputs["notif_zero"]

    # No holder was mutated by any replay path.
    assert holder_a["_meta"]["notification_persistent"]["email"]["emails"] == [
        {"id": "email-A", "message": "body A"}
    ]
    assert holder_b["_meta"]["notification_persistent"]["email"]["emails"] == [
        {"id": "email-B", "message": "body B"}
    ]
