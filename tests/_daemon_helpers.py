"""Focused daemon test helpers.

These keep repeated daemon test setup in one place without becoming a general
test framework.  The helpers intentionally model the concrete shapes the daemon
tests already use: a mock daemon-capable agent, a run directory, finite fake CLI
processes, and in-memory daemon entries.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Iterable
from unittest.mock import MagicMock

from lingtai.adapters.posix.daemon_supervisor import PosixDaemonSupervisorAdapter
from lingtai.adapters.posix.process_identity import process_identity
from lingtai.agent import Agent
from lingtai.tools.daemon.run_dir import DaemonRunDir
from lingtai.kernel.config import AgentConfig


def make_daemon_agent(
    tmp_path: Path,
    capabilities: Any | None = None,
    *,
    working_dir_name: str = "daemon-agent",
) -> Agent:
    """Create the minimal mock-service Agent used by daemon tests."""
    svc = MagicMock()
    svc.provider = "mock"
    svc.model = "mock-model"
    svc.create_session = MagicMock()
    svc.make_tool_result = MagicMock()
    return Agent(
        svc,
        working_dir=tmp_path / working_dir_name,
        capabilities=capabilities or ["daemon"],
        config=AgentConfig(),
    )


def make_daemon_run_dir(
    agent: Agent | None = None,
    *,
    parent_working_dir: Path | None = None,
    handle: str = "em-test",
    em_id: str | None = None,
    task: str = "test task",
    tools: Iterable[str] | None = ("file",),
    model: str = "mock-model",
    max_turns: int = 30,
    timeout_s: float = 300.0,
    parent_addr: str | None = None,
    parent_pid: int = 12345,
    system_prompt: str = "You are a daemon.",
    backend: str = "lingtai",
    call_parameters: dict[str, Any] | None = None,
) -> DaemonRunDir:
    """Create a DaemonRunDir with explicit, daemon-test-oriented defaults."""
    if em_id is not None:
        handle = em_id
    if parent_working_dir is None:
        if agent is None:
            raise ValueError("make_daemon_run_dir requires agent or parent_working_dir")
        parent_working_dir = agent._working_dir
    parent_working_dir.mkdir(parents=True, exist_ok=True)

    return DaemonRunDir(
        parent_working_dir=parent_working_dir,
        handle=handle,
        task=task,
        tools=list(tools or []),
        model=model,
        max_turns=max_turns,
        timeout_s=timeout_s,
        parent_addr=parent_addr or parent_working_dir.name,
        parent_pid=parent_pid,
        system_prompt=system_prompt,
        backend=backend,
        call_parameters=call_parameters,
    )


def install_fake_detached_owner(monkeypatch: Any) -> list[dict[str, Any]]:
    """Install a deterministic detached owner that commits durable evidence."""
    records: list[dict[str, Any]] = []

    def spawn(self, request, *, capsule=None):
        manifest = json.loads(Path(request.manifest_path).read_text(encoding="utf-8"))
        run_dir = DaemonRunDir.attach(Path(manifest["run_dir"]))
        pid = os.getpid()
        run_dir.update_state(
            owner="supervisor",
            supervisor_pid=pid,
            supervisor_start_identity=process_identity(pid),
            test_owner="detached-fake",
        )
        run_dir._append_jsonl(run_dir.events_path, {
            "event": "test_detached_backend_invocation",
            "backend": manifest["backend"],
            "argv": list(manifest.get("backend_argv") or []),
            "capsule_argv": list((capsule or {}).get("backend_argv") or []),
        })
        run_dir.mark_done("[fake detached done]")
        records.append({
            "manifest": manifest,
            "capsule": capsule or {},
            "run_dir": run_dir,
        })

    monkeypatch.setattr(PosixDaemonSupervisorAdapter, "spawn_detached", spawn)
    return records


def wait_daemon_terminal(run_dir: DaemonRunDir, timeout: float = 5.0) -> dict:
    """Wait until detached durable state reaches one of the terminal states."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = DaemonRunDir.read_state_from_disk(run_dir.path)
        if state.get("state") in {"done", "failed", "cancelled", "timeout"}:
            return state
        time.sleep(0.02)
    raise AssertionError(f"run did not reach terminal state: {run_dir.path}")


class FiniteFakeProc:
    """Minimal ``subprocess.Popen`` stand-in with finite stdout/stderr streams."""

    def __init__(
        self,
        *,
        stdout_lines: Iterable[str] = (),
        stderr_lines: Iterable[str] = (),
        returncode: int = 0,
        pid: int = 0,
    ) -> None:
        self.stdout = iter(list(stdout_lines))
        self.stderr = iter(list(stderr_lines))
        self.returncode = returncode
        self.pid = pid

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def completed_future(result: Any = None) -> Future[Any]:
    future: Future[Any] = Future()
    future.set_result(result)
    return future


def register_daemon_entry(
    mgr: Any,
    em_id: str,
    run_dir: DaemonRunDir,
    *,
    future: Any | None = None,
    task: str = "test task",
    start_time: float = 0.0,
    backend: str | None = None,
    ask_in_flight: bool | None = None,
    ask_future: Any | None = None,
) -> dict[str, Any]:
    """Register an in-memory daemon entry and return it for assertions."""
    entry: dict[str, Any] = {
        "future": future if future is not None else Future(),
        "task": task,
        "start_time": start_time,
        "cancel_event": threading.Event(),
        "timeout_event": threading.Event(),
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    if backend is not None:
        entry["backend"] = backend
    if ask_in_flight is not None:
        entry["ask_in_flight"] = ask_in_flight
        entry["ask_future"] = ask_future
    mgr._emanations[em_id] = entry
    return entry
