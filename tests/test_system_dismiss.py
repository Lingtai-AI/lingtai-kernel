"""Tests for the ``system(action='dismiss')`` deprecation shim.

Under the legacy ``tc_inbox`` model, agents dismissed individual
notification pairs by ``notif_id``.  The filesystem redesign replaces
that lifecycle: producers write `.notification/<tool>.json` files and
clear them when their state changes; the kernel keeps the wire in sync
automatically.  There is no per-notification dismiss action under the
new model.

The shim survives so chat histories that reference dismiss calls don't
crash on replay.  These tests verify the shim contract:

  * Returns ``{"status": "ok", "note": "<deprecation>"}`` regardless
    of input shape.
  * Does NOT mutate the wire chat or tc_inbox queue.
  * Does NOT validate ``ids`` (no error path) — the call is a no-op.
  * Logs a ``system_dismiss_deprecated`` event so unintended calls
    surface in agent logs.

Phase 3 deletes ``_dismiss`` and this test file entirely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lingtai_kernel.intrinsics import system as sys_intrinsic
from lingtai_kernel.llm.interface import (
    ChatInterface, ToolCallBlock, ToolResultBlock,
)
from lingtai_kernel.tc_inbox import TCInbox, InvoluntaryToolCall


class _StubChatSession:
    def __init__(self, interface: ChatInterface):
        self.interface = interface


@dataclass
class _StubSession:
    chat: _StubChatSession


@dataclass
class _StubAgent:
    _tc_inbox: TCInbox = field(default_factory=TCInbox)
    _session: _StubSession = field(default=None)
    _logs: list[tuple[str, dict]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self._session is None:
            self._session = _StubSession(chat=_StubChatSession(ChatInterface()))

    def _log(self, event_type: str, **fields: Any) -> None:
        self._logs.append((event_type, fields))


def _enqueue_notification(agent: _StubAgent, notif_id: str) -> str:
    call_id = f"call_{notif_id}"
    call = ToolCallBlock(id=call_id, name="system", args={
        "action": "notification",
        "notif_id": notif_id,
    })
    result = ToolResultBlock(id=call_id, name="system", content="...")
    agent._session.chat.interface.add_assistant_message(content=[call])
    agent._session.chat.interface.add_tool_results([result])
    return call_id


# ---------------------------------------------------------------------------
# Deprecation contract
# ---------------------------------------------------------------------------


def test_dismiss_returns_ok_with_deprecation_note():
    agent = _StubAgent()
    res = sys_intrinsic._dismiss(agent, {"ids": ["notif_xxx"]})
    assert res["status"] == "ok"
    assert "note" in res
    assert "deprecated" in res["note"].lower()


def test_dismiss_does_not_remove_from_chat():
    """Pre-redesign behavior: dismiss removed wire pairs.  Post-redesign:
    the wire stays untouched — producers manage state, not dismiss."""
    agent = _StubAgent()
    _enqueue_notification(agent, "notif_xxx")
    sys_intrinsic._dismiss(agent, {"ids": ["notif_xxx"]})
    # 2 entries (call + result) still present.
    assert len(agent._session.chat.interface.conversation_entries()) == 2


def test_dismiss_does_not_remove_from_queue():
    agent = _StubAgent()
    call = ToolCallBlock(id="c1", name="system", args={
        "action": "notification",
        "notif_id": "notif_q",
    })
    result = ToolResultBlock(id="c1", name="system", content="...")
    agent._tc_inbox.enqueue(InvoluntaryToolCall(
        call=call, result=result,
        source="system.notification:notif_q",
        enqueued_at=0.0, coalesce=False, replace_in_history=False,
    ))
    sys_intrinsic._dismiss(agent, {"ids": ["notif_q"]})
    # Queue still has the item.
    assert len(agent._tc_inbox) == 1


def test_dismiss_unknown_id_no_error():
    """Pre-redesign: unknown id returned 'not_found' (still ok).
    Post-redesign: every call is the same deprecation no-op — there is
    no per-id status to report."""
    agent = _StubAgent()
    res = sys_intrinsic._dismiss(agent, {"ids": ["does_not_exist"]})
    assert res["status"] == "ok"
    assert "results" not in res


def test_dismiss_empty_list_no_error():
    """Pre-redesign: empty/missing ids was an error.  Post-redesign: the
    call is a no-op shim; argument validation is irrelevant."""
    agent = _StubAgent()
    res = sys_intrinsic._dismiss(agent, {"ids": []})
    assert res["status"] == "ok"


def test_dismiss_missing_ids_no_error():
    agent = _StubAgent()
    res = sys_intrinsic._dismiss(agent, {})
    assert res["status"] == "ok"


def test_dismiss_logs_deprecation():
    agent = _StubAgent()
    sys_intrinsic._dismiss(agent, {"ids": ["a", "b"]})
    deprecated_events = [e for e, _ in agent._logs if e == "system_dismiss_deprecated"]
    assert len(deprecated_events) == 1


# ---------------------------------------------------------------------------
# Voluntary system(action='notification') is now ALLOWED (was rejected pre-redesign)
# ---------------------------------------------------------------------------


def test_handle_dispatches_voluntary_notification(tmp_path):
    """Under the .notification/ redesign, agent CAN voluntarily call
    system(action='notification') to read the current state of the
    notification surface.  Returns the collect_notifications() dict
    (or {} when nothing is published).
    """
    agent = _StubAgent()
    agent._working_dir = tmp_path
    res = sys_intrinsic.handle(agent, {"action": "notification"})
    # No producers have written; result should be an empty dict.
    assert isinstance(res, dict)
    assert res == {}
