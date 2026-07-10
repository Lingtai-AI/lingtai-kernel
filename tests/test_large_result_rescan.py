"""Tests for the (now inert) large-result rescan and legacy compatibility.

Large tool results no longer raise ``large_tool_result`` system notifications.
They are ranked through ``_meta.agent_meta.current_tool_result_chars`` (see
``tests/test_large_result_no_notification.py`` and ``tests/test_meta_block.py``)
and digested via ``system(action="summarize")``.

This file pins what survives that removal:

1. ``_rescan_large_tool_results`` is retained as a callable no-op (returns 0)
   and never publishes, on any history.
2. The generic ``_enqueue_system_notification`` (still used by other producers)
   keeps its ``skip_if_ref_id_exists`` dedup contract.
3. Legacy ``large_tool_result`` events that already exist in ``system.json``
   (e.g. persisted before this change / before a molt) remain dismissible via
   ``notification(action="dismiss_ref")`` as an escape hatch (P0 requirement §3).
4. ToolExecutor metadata stamping (``tool_meta``) is unaffected.
"""
from __future__ import annotations
from tools.registry import INTRINSICS as _TEST_INTRINSICS

import threading
from pathlib import Path
from unittest.mock import MagicMock

from lingtai.kernel.llm.interface import (
    ChatInterface,
    ToolCallBlock,
    ToolResultBlock,
)
from lingtai.kernel.base_agent.messaging import (
    _rescan_large_tool_results,
    _enqueue_system_notification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stub_agent(chat_interface: ChatInterface | None = None):
    """Return a minimal stub agent that records any publish attempt."""
    iface = chat_interface if chat_interface is not None else ChatInterface()

    class _StubChat:
        interface = iface

    agent = MagicMock()
    agent._chat = _StubChat()
    agent._chat.interface = iface
    agent._log = MagicMock()
    agent._summarize_notification_threshold = 5000
    agent._system_notification_lock = threading.Lock()

    published: list[dict] = []

    def _fake_enqueue(*, source, ref_id, body, skip_if_ref_id_exists=False, **kw):
        published.append({"source": source, "ref_id": ref_id, "body": body})
        return f"evt_{len(published):03d}"

    agent._enqueue_system_notification = _fake_enqueue
    agent._published = published
    return agent


def _add_tool_pair(iface: ChatInterface, call_id: str, tool_name: str, result_content):
    iface.add_assistant_message([ToolCallBlock(id=call_id, name=tool_name, args={})])
    iface.add_tool_results([ToolResultBlock(id=call_id, name=tool_name, content=result_content)])


def _run_executor_metadata(
    tmp_path: Path,
    *,
    output,
    tool_name: str = "bash",
    tool_call_id: str = "tc-001",
    threshold: int | None = None,
):
    """Run one tool call through a real ToolExecutor and return its result content."""
    from lingtai.kernel.tool_executor import ToolExecutor
    from lingtai.kernel.loop_guard import LoopGuard
    from lingtai.kernel.llm.interface import ToolResultBlock as _TRB
    from lingtai.kernel.llm.base import ToolCall as _TC

    def _dispatch(tc):
        return {"output": output, "status": "ok"}

    def _make_result(name, content, *, tool_call_id=None):
        return _TRB(id=tool_call_id, name=name, content=content)

    kwargs = {}
    if threshold is not None:
        kwargs["summarize_notification_threshold"] = threshold
    executor = ToolExecutor(
        dispatch_fn=_dispatch,
        make_tool_result_fn=_make_result,
        guard=LoopGuard(),
        working_dir=tmp_path,
        **kwargs,
    )
    tc = _TC(name=tool_name, args={}, id=tool_call_id)
    results, _, _ = executor.execute([tc])
    return results[0].content


# ---------------------------------------------------------------------------
# 1. Rescan is an inert no-op — never publishes, returns 0
# ---------------------------------------------------------------------------


def test_rescan_returns_zero_for_huge_history():
    """Even a result far above any old gate publishes nothing."""
    iface = ChatInterface()
    _add_tool_pair(iface, "tc-huge", "bash", {"output": "X" * 80000, "status": "ok"})
    agent = _make_stub_agent(iface)
    agent._summarize_notification_threshold = 100

    count = _rescan_large_tool_results(agent)

    assert count == 0
    assert agent._published == []


def test_rescan_no_chat_session_is_noop():
    """If agent has no chat session, rescan returns 0."""
    agent = MagicMock()
    agent._chat = None
    agent._summarize_notification_threshold = 5000
    agent._log = MagicMock()

    count = _rescan_large_tool_results(agent)
    assert count == 0


def test_base_agent_has_rescan_method(tmp_path):
    """BaseAgent exposes _rescan_large_tool_results as a callable inert no-op."""
    from lingtai.kernel.base_agent import BaseAgent

    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"

    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="test-rescan", working_dir=tmp_path / "ag")
    assert callable(agent._rescan_large_tool_results)
    assert agent._rescan_large_tool_results() == 0


def test_base_agent_rescan_with_chat_session_publishes_nothing(tmp_path):
    """With a real chat session holding a large block, rescan still publishes nothing."""
    from lingtai.kernel.base_agent import BaseAgent

    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"

    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="test-rescan-chat", working_dir=tmp_path / "ag2")
    agent._summarize_notification_threshold = 100

    iface = ChatInterface()
    iface.add_assistant_message([ToolCallBlock(id="tc-real-001", name="bash", args={})])
    iface.add_tool_results([ToolResultBlock(id="tc-real-001", name="bash", content="X" * 55_000)])

    class _FakeChat:
        interface = iface

    agent._chat = _FakeChat()

    published: list[dict] = []
    original_enqueue = agent._enqueue_system_notification

    def _capture(**kw):
        published.append(kw)
        return original_enqueue(**kw)

    agent._enqueue_system_notification = _capture

    count = agent._rescan_large_tool_results()
    assert count == 0
    assert published == []


# ---------------------------------------------------------------------------
# 2. Generic _enqueue_system_notification dedup contract (still used elsewhere)
# ---------------------------------------------------------------------------


def test_enqueue_skip_if_ref_id_exists(tmp_path):
    """skip_if_ref_id_exists=True skips publishing when ref_id already in events."""
    from lingtai.kernel.base_agent import BaseAgent

    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"

    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="test-dedup", working_dir=tmp_path / "ag")

    ev1 = _enqueue_system_notification(
        agent,
        source="daemon",
        ref_id="daemon:tc-test-001",
        body="first notification",
        skip_if_ref_id_exists=False,
    )
    assert ev1 != ""

    ev2 = _enqueue_system_notification(
        agent,
        source="daemon",
        ref_id="daemon:tc-test-001",
        body="second notification — same ref_id",
        skip_if_ref_id_exists=True,
    )
    assert ev2 == "", "must return empty string when skipped"

    from lingtai.kernel.notifications import collect_notifications
    notifs = collect_notifications(agent._working_dir)
    events = notifs.get("system", {}).get("data", {}).get("events", [])
    ref_ids = [ev.get("ref_id") for ev in events]
    assert ref_ids.count("daemon:tc-test-001") == 1


def test_enqueue_no_skip_publishes_twice(tmp_path):
    """Without skip_if_ref_id_exists, same ref_id is published twice (normal behavior)."""
    from lingtai.kernel.base_agent import BaseAgent

    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"

    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="test-nodedup", working_dir=tmp_path / "ag")

    ev1 = _enqueue_system_notification(
        agent,
        source="daemon",
        ref_id="daemon:tc-dup-001",
        body="first",
        skip_if_ref_id_exists=False,
    )
    ev2 = _enqueue_system_notification(
        agent,
        source="daemon",
        ref_id="daemon:tc-dup-001",
        body="second",
        skip_if_ref_id_exists=False,
    )
    assert ev1 != ""
    assert ev2 != ""
    assert ev1 != ev2

    from lingtai.kernel.notifications import collect_notifications
    notifs = collect_notifications(agent._working_dir)
    events = notifs.get("system", {}).get("data", {}).get("events", [])
    ref_ids = [ev.get("ref_id") for ev in events]
    assert ref_ids.count("daemon:tc-dup-001") == 2


# ---------------------------------------------------------------------------
# 3. Legacy compatibility: pre-existing large_tool_result event stays dismissible
# ---------------------------------------------------------------------------


def test_stale_large_result_event_can_be_dismissed(tmp_path):
    """A persisted large_tool_result event (e.g. from before this change or a
    pre-molt session) can still be dismissed via dismiss_ref (escape hatch)."""
    import threading
    from dataclasses import dataclass, field
    from typing import Any
    from lingtai.kernel.notifications import (
        load_large_result_acks,
        notification_fingerprint,
        publish,
    )
    from tools import notification as notif_intrinsic

    @dataclass
    class _StubAgent:
        _working_dir: Path
        _logs: list = field(default_factory=list)
        _notification_fp: tuple = ()
        _system_notification_lock: threading.Lock = field(default_factory=threading.Lock)

        def _log(self, event_type: str, **fields: Any) -> None:
            self._logs.append((event_type, fields))

    agent = _StubAgent(tmp_path)

    stale_ref = "large_tool_result:toolu_pre_molt_xyz"
    publish(
        tmp_path,
        "system",
        {
            "header": "1 system notification",
            "icon": "🔔",
            "priority": "normal",
            "published_at": "2026-06-01T00:00:00Z",
            "data": {
                "events": [
                    {
                        "event_id": "evt_stale",
                        "source": "large_tool_result",
                        "ref_id": stale_ref,
                        "body": "stale reminder from before this change",
                    }
                ]
            },
        },
    )
    agent._notification_fp = notification_fingerprint(tmp_path)

    res = notif_intrinsic.handle(agent, {"action": "dismiss_ref", "ref_id": stale_ref})

    assert res["status"] == "ok"
    assert stale_ref in res.get("acked_large_result_refs", [])

    acks = load_large_result_acks(tmp_path)
    assert stale_ref in acks
    assert not (tmp_path / ".notification" / "system.json").exists()


# ---------------------------------------------------------------------------
# 4. Legacy _tool_result_metadata removal (ToolExecutor stamping unaffected)
# ---------------------------------------------------------------------------


def test_legacy_tool_result_metadata_removed_for_small_dict_result(tmp_path):
    content = _run_executor_metadata(
        tmp_path,
        output={"status": "ok", "value": "tiny"},
        tool_call_id="tc-small",
    )

    assert "_tool_result_metadata" not in content
    assert content["_meta"]["tool_meta"]["id"] == "tc-small"
    assert content["_meta"]["tool_meta"]["char_count"] > 0


def test_legacy_tool_result_metadata_removed_for_large_dict_result(tmp_path):
    content = _run_executor_metadata(
        tmp_path,
        output={"data": "x" * 5000},
        tool_call_id="tc-large",
    )

    assert "_tool_result_metadata" not in content
    assert content["_meta"]["tool_meta"]["id"] == "tc-large"
    assert content["_meta"]["tool_meta"]["char_count"] > 0
