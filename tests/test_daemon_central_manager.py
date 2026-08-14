from __future__ import annotations

import json
import time
from pathlib import Path

from lingtai.adapters.posix.daemon_manager import _DaemonManagerProcess
from lingtai.kernel.daemon_supervisor import DaemonSupervisorRequest, encode_request
from lingtai.kernel.daemon_supervisor.manifest import build_manifest, manifest_path_for, write_manifest
from lingtai.tools.daemon.run_dir import DaemonRunDir


def _make_run(tmp_path: Path, run_id: str, *, timeout_s: float = 30.0) -> tuple[DaemonRunDir, DaemonSupervisorRequest]:
    parent = tmp_path / "agent"
    parent.mkdir(parents=True, exist_ok=True)
    run_dir = DaemonRunDir(
        parent_working_dir=parent,
        handle=run_id,
        run_id=run_id,
        task=f"task {run_id}",
        tools=[],
        model="fake",
        max_turns=1,
        timeout_s=timeout_s,
        parent_addr=parent.name,
        parent_pid=12345,
        system_prompt=f"daemon\n\nYour task:\ntask {run_id}",
        call_parameters={"task": f"task {run_id}", "tools": []},
    )
    manifest = build_manifest(
        run_id=run_id,
        backend="lingtai",
        parent_working_dir=str(parent),
        run_dir=str(run_dir.path),
        task=f"task {run_id}",
        tools=[],
        max_turns=1,
        timeout_s=timeout_s,
        group_id=None,
        llm={"provider": "fake", "model": "fake"},
    )
    write_manifest(run_dir.path, manifest)
    return run_dir, DaemonSupervisorRequest(
        run_id=run_id,
        manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable="python",
    )


def _write_job(queue_dir: Path, request: DaemonSupervisorRequest, capsule: dict | None = None) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / f"{request.run_id}.json").write_text(
        json.dumps(
            {
                "schema": "lingtai.daemon_manager_job.v1",
                "run_id": request.run_id,
                "request": encode_request(request),
                "capsule": capsule or {},
            }
        ),
        encoding="utf-8",
    )


def _wait_state(run_dir: DaemonRunDir, state: str, *, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = DaemonRunDir.read_state_from_disk(run_dir.path)
        if data.get("state") == state:
            return data
        time.sleep(0.02)
    raise AssertionError(f"{run_dir.run_id} did not reach {state}")


def _notification_events(parent: Path) -> list[dict]:
    path = parent / ".notification" / "system.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("data", {}).get("events", []))


def test_central_manager_completes_run_and_notifies(tmp_path, monkeypatch):
    run_dir, request = _make_run(tmp_path, "em-manager")
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, request)

    def fake_run(rd, manifest, capsule):
        rd.mark_done("manager completed")

    monkeypatch.setattr("lingtai.adapters.posix.daemon_manager._run_one_emanation", fake_run)

    manager = _DaemonManagerProcess(queue_dir, journal_dir, pool_size=1)
    manager.run()

    state = _wait_state(run_dir, "done")
    assert state["owner"] == "manager"
    assert state["terminal_notified"] is True
    events = _notification_events(run_dir.path.parent.parent)
    assert [ev["ref_id"] for ev in events].count("em-manager") == 1


def test_central_manager_recovery_marks_active_failed_without_duplicate_notify(tmp_path):
    run_dir, request = _make_run(tmp_path, "em-recover")
    journal_dir = tmp_path / "manager" / "journal"
    journal_dir.mkdir(parents=True)
    (journal_dir / "em-recover.json").write_text(
        json.dumps(
            {
                "schema": "lingtai.daemon_manager_journal.v1",
                "run_id": "em-recover",
                "request": encode_request(request),
                "capsule": {},
                "state": "active",
            }
        ),
        encoding="utf-8",
    )

    manager = _DaemonManagerProcess(tmp_path / "manager" / "queue", journal_dir, pool_size=1)
    manager.recover_interrupted_active_runs()
    manager.recover_interrupted_active_runs()

    state = _wait_state(run_dir, "failed")
    assert "central daemon manager recovered" in state["error"]["message"]
    events = _notification_events(run_dir.path.parent.parent)
    assert [ev["ref_id"] for ev in events].count("em-recover") == 1


def test_central_manager_timeout_run_notifies(tmp_path, monkeypatch):
    run_dir, request = _make_run(tmp_path, "em-timeout", timeout_s=5.0)
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, request)

    def fake_timeout(rd, manifest, capsule):
        rd.mark_timeout()

    monkeypatch.setattr("lingtai.adapters.posix.daemon_manager._run_one_emanation", fake_timeout)

    manager = _DaemonManagerProcess(queue_dir, journal_dir, pool_size=1)
    manager.run()

    state = _wait_state(run_dir, "timeout")
    assert state["terminal_notified"] is True
    events = _notification_events(run_dir.path.parent.parent)
    assert [ev["ref_id"] for ev in events].count("em-timeout") == 1


def test_central_manager_queues_until_worker_frees(tmp_path, monkeypatch):
    first, req1 = _make_run(tmp_path, "em-first")
    second, req2 = _make_run(tmp_path, "em-second")
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, req1)
    _write_job(queue_dir, req2)
    starts: list[str] = []

    def fake_slow(rd, manifest, capsule):
        starts.append(rd.run_id)
        time.sleep(0.15)
        rd.mark_done(rd.run_id)

    monkeypatch.setattr("lingtai.adapters.posix.daemon_manager._run_one_emanation", fake_slow)

    manager = _DaemonManagerProcess(queue_dir, journal_dir, pool_size=1)
    manager.run()

    assert starts == ["em-first", "em-second"]
    assert _wait_state(first, "done")["state"] == "done"
    assert _wait_state(second, "done")["state"] == "done"


def test_central_manager_is_disabled_by_default(tmp_path):
    from types import SimpleNamespace

    from lingtai.tools.daemon import DaemonManager

    agent = SimpleNamespace(
        service=SimpleNamespace(model="mock"),
        _working_dir=tmp_path / "agent",
        _log=lambda *a, **k: None,
    )
    manager = DaemonManager(agent)

    assert manager._manager_pool_size == 0
    assert manager._should_use_central_daemon_manager(10_000) is False

