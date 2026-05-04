"""Regression tests for WorkerStillRunningError fail-closed recovery."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import queue
import threading
from types import SimpleNamespace

import pytest

from lingtai_kernel.base_agent import turn
from lingtai_kernel.llm_utils import WorkerStillRunningError
from lingtai_kernel.message import _make_message, MSG_REQUEST
from lingtai_kernel.state import AgentState


@dataclass
class _FakeAgent:
    _working_dir: object
    _state: AgentState = AgentState.ACTIVE
    _asleep: threading.Event = field(default_factory=threading.Event)
    _logs: list[tuple[str, dict]] = field(default_factory=list)
    _states: list[AgentState] = field(default_factory=list)

    def _log(self, event_type: str, **fields):
        self._logs.append((event_type, fields))

    def _set_state(self, new_state: AgentState, reason: str = ""):
        self._state = new_state
        self._states.append(new_state)
        self._log("agent_state", new=new_state.value, reason=reason)


def _worker_error() -> WorkerStillRunningError:
    return WorkerStillRunningError(elapsed=300.0, grace=5.0, agent_name="test")


def test_send_with_watchdog_keeps_llm_hang_for_worker_still_running(tmp_path, monkeypatch):
    agent = _FakeAgent(tmp_path)
    (tmp_path / ".llm_hang").write_text(json.dumps({"detected_at": 1}), encoding="utf-8")
    agent._session = SimpleNamespace(send=lambda content: (_ for _ in ()).throw(_worker_error()))

    monkeypatch.setattr(turn.threading, "Timer", lambda *a, **kw: SimpleNamespace(start=lambda: None, cancel=lambda: None, daemon=False))

    with pytest.raises(WorkerStillRunningError):
        turn._send_with_watchdog(agent, "hi")

    payload = json.loads((tmp_path / ".llm_hang").read_text(encoding="utf-8"))
    assert "worker_still_running_at" in payload
    assert "ChatInterface is unsafe" in payload["error"]


def test_send_with_watchdog_removes_llm_hang_for_ordinary_exception(tmp_path, monkeypatch):
    agent = _FakeAgent(tmp_path)
    (tmp_path / ".llm_hang").write_text(json.dumps({"detected_at": 1}), encoding="utf-8")
    agent._session = SimpleNamespace(send=lambda content: (_ for _ in ()).throw(TimeoutError("ordinary")))

    monkeypatch.setattr(turn.threading, "Timer", lambda *a, **kw: SimpleNamespace(start=lambda: None, cancel=lambda: None, daemon=False))

    with pytest.raises(TimeoutError):
        turn._send_with_watchdog(agent, "hi")

    assert not (tmp_path / ".llm_hang").exists()


def test_handle_worker_still_running_sets_asleep_and_signal(tmp_path):
    agent = _FakeAgent(tmp_path)

    turn._handle_worker_still_running(agent, _worker_error())

    assert agent._asleep.is_set()
    assert agent._states[-2:] == [AgentState.STUCK, AgentState.ASLEEP]
    assert (tmp_path / ".llm_hang").exists()
    assert any(name == "llm_worker_still_running" for name, _ in agent._logs)


def test_run_loop_skips_chat_history_save_after_worker_still_running(tmp_path, monkeypatch):
    agent = _FakeAgent(tmp_path)
    agent._shutdown = threading.Event()
    agent._cancel_event = threading.Event()
    agent._inbox_timeout = 0.01
    agent._config = SimpleNamespace(insights_interval=0)
    agent.inbox = queue.Queue()
    agent.inbox.put(_make_message(MSG_REQUEST, "human", "go"))
    agent.saves = 0
    agent._save_chat_history = lambda: setattr(agent, "saves", agent.saves + 1)

    def fake_handle(_agent, _msg):
        raise _worker_error()

    monkeypatch.setattr(turn, "_handle_message", fake_handle)

    def cancel_timer(_agent):
        _agent._shutdown.set()

    import lingtai_kernel.intrinsics.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", cancel_timer)

    turn._run_loop(agent)

    assert agent.saves == 0
    assert any(name == "chat_history_save_skipped" for name, _ in agent._logs)
    assert (tmp_path / ".llm_hang").exists()


def test_asleep_wake_refuses_when_llm_hang_signal_exists(tmp_path, monkeypatch):
    agent = _FakeAgent(tmp_path, _state=AgentState.ASLEEP)
    agent._shutdown = threading.Event()
    agent._asleep.set()
    agent._cancel_event = threading.Event()
    agent._inbox_timeout = 0.01
    agent.inbox = queue.Queue()
    agent.inbox.put(_make_message(MSG_REQUEST, "human", "wake?"))
    (tmp_path / ".llm_hang").write_text(json.dumps({"detected_at": 1}), encoding="utf-8")

    # Stop the loop after it refuses the wake and returns to the asleep wait.
    calls = {"n": 0}
    def cancel_timer(_agent):
        calls["n"] += 1
        if calls["n"] >= 2:
            _agent._shutdown.set()

    monkeypatch.setattr(turn, "_cancel_soul_timer", cancel_timer, raising=False)

    # The function imports _cancel_soul_timer locally; patch the source symbol.
    import lingtai_kernel.intrinsics.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", cancel_timer)

    turn._run_loop(agent)

    assert agent._asleep.is_set()
    assert any(name == "wake_refused_llm_hang" for name, _ in agent._logs)
    assert not any(new == AgentState.ACTIVE for new in agent._states)
