"""Behavioral evidence for the watcher-local process-mechanism Port."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lingtai.kernel.refresh_watcher import (
    RefreshWatcherProcessHandle,
    RefreshWatcherProcessObservation,
    RefreshWatcherProcessPort,
    RefreshWatcherRequest,
)
from lingtai.kernel.refresh_watcher.watcher_program import render_watcher_script


class FakeWatcherProcess(RefreshWatcherProcessPort):
    """Small deterministic mechanism used only to exercise Core policy."""

    def __init__(
        self,
        working_dir: Path,
        *,
        force: bool,
        include_duplicate_pid: bool = True,
    ) -> None:
        self.working_dir = working_dir
        self.force = force
        self.include_duplicate_pid = include_duplicate_pid
        self.calls: list[tuple[str, int | None]] = []
        self.launches = 0
        self.duplicate_alive = True
        self.observation = RefreshWatcherProcessObservation(
            pid=4242,
            command_line=f"{sys.executable} -m lingtai run {working_dir}",
        )

    def observe(self, pid: int) -> RefreshWatcherProcessObservation | None:
        self.calls.append(("observe", pid))
        return self.observation if pid == self.observation.pid else None

    def is_alive(
        self,
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> bool:
        self.calls.append(("is_alive", process.pid))
        return process.pid == self.observation.pid and self.duplicate_alive

    def start_agent(self, cmd, stderr_log: str) -> RefreshWatcherProcessHandle:
        self.launches += 1
        self.calls.append(("start_agent", self.launches))
        if self.launches == 1:
            with open(stderr_log, "a", encoding="utf-8") as stream:
                stream.write("another lingtai agent is already running\n")
                if self.include_duplicate_pid:
                    stream.write("PID 4242: stale duplicate\n")
        else:
            (self.working_dir / ".agent.heartbeat").write_text(
                "0", encoding="utf-8"
            )
            # The generated policy compares this timestamp with its current
            # wall clock; use the actual value after the call is entered.
            import time

            (self.working_dir / ".agent.heartbeat").write_text(
                str(time.time()), encoding="utf-8"
            )
        return RefreshWatcherProcessHandle(pid=9000 + self.launches)

    def graceful_stop(self, process) -> None:
        self.calls.append(("graceful_stop", process.pid))
        if not self.force:
            self.duplicate_alive = False

    def force_stop(self, process) -> None:
        self.calls.append(("force_stop", process.pid))
        self.duplicate_alive = False


def _run_policy(
    tmp_path: Path,
    *,
    force: bool,
    include_duplicate_pid: bool = True,
) -> FakeWatcherProcess:
    working_dir = tmp_path / ("force" if force else "graceful")
    (working_dir / "logs").mkdir(parents=True)
    (working_dir / ".refresh.taken").touch()
    request = RefreshWatcherRequest(
        taken_path=str(working_dir / ".refresh.taken"),
        lock_path=str(working_dir / ".agent.lock"),
        events_path=str(working_dir / "logs" / "events.jsonl"),
        stderr_log=str(working_dir / "logs" / "refresh_relaunch.log"),
        working_dir=str(working_dir),
        cmd=(sys.executable, "-m", "lingtai", "run", str(working_dir)),
        agent_name="alice",
        address=str(working_dir),
    )
    script = render_watcher_script(request)
    script = (
        script.replace("MAX_ATTEMPTS = 12", "MAX_ATTEMPTS = 2")
        .replace("HEALTH_CHECK_WAIT = 10", "HEALTH_CHECK_WAIT = 0.01")
        .replace("deadline = time.time() + 60", "deadline = time.time() + 1")
        .replace("deadline = time.time() + 5", "deadline = time.time() + 0.02")
        .replace("time.sleep(0.2)", "time.sleep(0.005)")
    )
    mechanism = FakeWatcherProcess(
        working_dir,
        force=force,
        include_duplicate_pid=include_duplicate_pid,
    )
    namespace = {"PROCESS_MECHANISM": mechanism}
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(script, "<refresh-policy>", "exec"), namespace)
    assert exit_info.value.code == 0
    return mechanism


def test_refresh_watcher_selector_fails_loudly_on_unsupported_platform(monkeypatch):
    import lingtai.adapters.refresh_watcher as selector

    monkeypatch.setattr(selector.sys, "platform", "win32")
    with pytest.raises(NotImplementedError, match="refresh-watcher adapter"):
        selector.select_refresh_watcher()


def test_core_policy_chooses_process_port_operations_without_keywords(tmp_path):
    graceful = _run_policy(tmp_path, force=False)
    assert graceful.launches == 2
    assert [name for name, _ in graceful.calls].count("observe") == 1
    assert ("graceful_stop", 4242) in graceful.calls
    assert not any(name == "force_stop" for name, _ in graceful.calls)
    assert ("start_agent", 1) in graceful.calls
    assert ("start_agent", 2) in graceful.calls

    forced = _run_policy(tmp_path, force=True)
    assert forced.launches == 2
    assert ("graceful_stop", 4242) in forced.calls
    assert ("force_stop", 4242) in forced.calls
    assert ("start_agent", 1) in forced.calls
    assert ("start_agent", 2) in forced.calls


def test_core_policy_does_not_observe_missing_duplicate_pid(tmp_path):
    mechanism = _run_policy(
        tmp_path,
        force=False,
        include_duplicate_pid=False,
    )

    assert mechanism.launches == 2
    assert not any(name == "observe" for name, _ in mechanism.calls)
    assert not any(name == "graceful_stop" for name, _ in mechanism.calls)
    assert not any(name == "force_stop" for name, _ in mechanism.calls)
