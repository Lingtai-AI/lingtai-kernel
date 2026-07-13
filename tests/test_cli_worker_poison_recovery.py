"""Regression: a stuck LLM worker (WorkerStillRunning poison) must not strand
the agent by keeping the old process alive.

When ``WorkerStillRunningError`` fires, the AED loop poisons the in-process
ChatInterface and requests a refresh/relaunch (see ``test_aed_recovery.py``).
The refresh watcher spawned by ``_perform_refresh`` relaunches a fresh process,
and ``_stop`` withdraws ``.agent.heartbeat`` / ``.agent.lock`` so the watcher
can proceed. But the wedged worker thread lives in the session's non-daemon
``_timeout_pool`` ThreadPoolExecutor. ``session.close()`` can only call
``shutdown(wait=False)`` — it cannot reclaim a thread stuck inside the LLM call.
That thread then blocks interpreter exit through ``concurrent.futures``' atexit
join, leaving a heartbeat-less, lock-free but ``ps``-visible ``lingtai run``
process. The relaunch's duplicate-process guard (``_check_duplicate_process``)
sees that lingering process and refuses to boot, so the agent is stranded as a
stale ``asleep`` marker with no working process — exactly the production
incident.

``_stop`` already reclaims daemon ThreadPoolExecutor workers / CLI process
groups for the same reason ("keep this interpreter visible in ps after
heartbeat/lock are gone, which makes refresh watchers race the
duplicate-process guard"). A wedged LLM worker is the one resource it cannot
reclaim, so the CLI process owner must hard-exit after the graceful ``stop()``
teardown when the interface was poisoned.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from lingtai import cli
from lingtai.kernel.state import AgentState


class _ForceExit(Exception):
    """Sentinel raised by the patched os._exit so tests can observe it."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _FakeAgent:
    def __init__(self, poisoned: bool):
        self._llm_worker_interface_poisoned = poisoned
        self._llm_worker_poison_artifact = (
            "history/unfinished_turns/worker_still_running_x.json" if poisoned else None
        )
        self._shutdown = threading.Event()
        self._shutdown.set()  # run()'s _shutdown.wait() returns immediately
        self._asleep = threading.Event()
        self._state = None
        self._venv_path = None
        self.started = False
        self.stop_calls = 0
        self.logs: list[tuple[str, dict]] = []

    def start(self):
        self.started = True

    def stop(self, timeout: float | None = None):
        # Graceful teardown ran here in production (heartbeat unlinked, lock
        # released, watcher already spawned). The wedged worker thread is NOT
        # reclaimed — modeled by leaving the poison flag set after stop().
        self.stop_calls += 1

    def send(self, *a, **k):  # pragma: no cover - only on refresh boot
        pass

    def _log(self, event_type: str, **fields):
        self.logs.append((event_type, fields))


def _patch_run_dependencies(monkeypatch, tmp_path: Path, agent: _FakeAgent):
    monkeypatch.setattr(cli, "_check_duplicate_process", lambda wd: None)
    monkeypatch.setattr(cli, "_clean_signal_files", lambda wd: None)
    monkeypatch.setattr(cli, "load_init", lambda wd: {"venv_path": "/fake/venv"})
    monkeypatch.setattr(
        "lingtai.venv_resolve.resolve_venv", lambda data: Path("/fake/venv")
    )
    monkeypatch.setattr(cli, "build_agent", lambda data, wd: agent)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda wd, a: None)

    exits: list[int] = []

    def _fake_exit(code):
        exits.append(code)
        raise _ForceExit(code)

    monkeypatch.setattr("os._exit", _fake_exit)
    return exits


def test_run_force_exits_when_worker_interface_poisoned(tmp_path, monkeypatch):
    """A poisoned worker at shutdown must hard-exit the process so the wedged
    thread cannot keep the old process alive and block the relaunch."""
    agent = _FakeAgent(poisoned=True)
    exits = _patch_run_dependencies(monkeypatch, tmp_path, agent)

    with pytest.raises(_ForceExit) as excinfo:
        cli.run(tmp_path)

    assert excinfo.value.code == 0
    assert exits == [0]
    assert agent.stop_calls == 1  # graceful teardown runs BEFORE the hard exit


def test_run_clean_exit_when_worker_not_poisoned(tmp_path, monkeypatch):
    """The normal (non-poisoned) shutdown path must never hard-exit — it must
    return cleanly so ordinary refresh/stop semantics are unchanged."""
    agent = _FakeAgent(poisoned=False)
    exits = _patch_run_dependencies(monkeypatch, tmp_path, agent)

    cli.run(tmp_path)  # returns normally, no _ForceExit

    assert exits == []
    assert agent.stop_calls == 1
    assert agent._state == AgentState.ASLEEP
