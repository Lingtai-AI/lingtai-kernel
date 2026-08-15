from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from lingtai.adapters.posix.daemon_manager import MANAGER_DIR
from lingtai.adapters.posix.daemon_manager import _DaemonManagerProcess
from lingtai.adapters.posix.process_identity import process_identity
from lingtai.kernel.daemon_supervisor import DaemonSupervisorRequest, encode_request
from lingtai.kernel.daemon_supervisor.manifest import build_manifest, manifest_path_for, write_manifest
from lingtai.tools.daemon import DaemonManager
from lingtai.tools.daemon.run_dir import DaemonRunDir
from tests._daemon_helpers import install_fake_detached_owner, make_daemon_agent


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


def _write_job(
    queue_dir: Path,
    request: DaemonSupervisorRequest,
    capsule: dict | None = None,
    *,
    enqueued_at: float | None = None,
) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / f"{request.run_id}.json").write_text(
        json.dumps(
            {
                "schema": "lingtai.daemon_manager_job.v1",
                "run_id": request.run_id,
                "request": encode_request(request),
                "capsule_in_memory": True,
                "enqueued_at": enqueued_at if enqueued_at is not None else time.time(),
            }
        ),
        encoding="utf-8",
    )


def _manager_with_capsules(
    queue_dir: Path,
    journal_dir: Path,
    *,
    pool_size: int,
    capsules: dict[str, dict] | None = None,
) -> _DaemonManagerProcess:
    manager = _DaemonManagerProcess(queue_dir, journal_dir, pool_size=pool_size)
    manager.capsules.update(capsules or {})
    return manager


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


def _enable_detached_fake_llm(monkeypatch, agent, *, sleep_s: float = 0.0) -> None:
    agent.service.provider = "lingtai-supervisor-test-fake"
    agent.service.model = "fake-model"
    agent.service.api_key = "detached-test-key"
    agent.service._base_url = None
    agent.service._provider_defaults = {}
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM", "1")
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH", "1")
    if sleep_s:
        monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP", str(sleep_s))
    tests_dir = str(Path(__file__).parent)
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    existing = os.environ.get("PYTHONPATH", "")
    parts = [tests_dir, src_dir]
    parts.extend(p for p in existing.split(os.pathsep) if p)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(dict.fromkeys(parts)))


def _install_fake_opencode(tmp_path: Path, monkeypatch, *, sleep_s: float = 1.0) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "opencode"
    script.write_text(
        "\n".join([
            "#!/usr/bin/env python3",
            "import json, os, pathlib, time",
            "cfg = json.loads(os.environ.get('OPENCODE_CONFIG_CONTENT') or '{}')",
            "env = cfg.get('mcp', {}).get('daemon_common', {}).get('environment', {})",
            "completion = env.get('LINGTAI_DAEMON_COMPLETION_FILE')",
            "if completion:",
            "    pathlib.Path(completion).write_text(json.dumps({",
            "        'schema': 'lingtai.daemon_completion.v1',",
            "        'status': 'done',",
            "        'run_id': env.get('LINGTAI_DAEMON_RUN_ID'),",
            "        'summary': 'fake opencode done',",
            "    }), encoding='utf-8')",
            f"time.sleep({sleep_s!r})",
            "print(json.dumps({'type': 'result', 'session_id': 'fake-session', 'result': 'done'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))


def _wait_for(predicate, *, timeout: float = 5.0, message: str = "condition") -> object:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {message}; last={last!r}")


def _terminate_resident_manager(agent) -> None:
    pid_path = agent._working_dir / MANAGER_DIR / "manager.pid"
    if not pid_path.exists():
        return
    try:
        info = json.loads(pid_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    pid = info.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool):
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def _write_live_manager_pid(agent, *, pid: int | None = None) -> None:
    pid = os.getpid() if pid is None else pid
    root = agent._working_dir / MANAGER_DIR
    root.mkdir(parents=True, exist_ok=True)
    (root / "manager.pid").write_text(
        json.dumps({
            "pid": pid,
            "state": "running",
            "started_at": time.time(),
            "manager_start_identity": process_identity(pid),
        }),
        encoding="utf-8",
    )


def test_central_manager_completes_run_and_notifies(tmp_path, monkeypatch):
    run_dir, request = _make_run(tmp_path, "em-manager")
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, request)

    def fake_run(rd, manifest, capsule):
        rd.mark_done("manager completed")

    monkeypatch.setattr("lingtai.adapters.posix.daemon_manager._run_one_emanation", fake_run)

    manager = _manager_with_capsules(
        queue_dir, journal_dir, pool_size=1, capsules={request.run_id: {}},
    )
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
                "capsule_in_memory": True,
                "capsule_scrubbed": True,
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


def test_central_manager_restart_fails_queued_job_without_capsule(tmp_path):
    run_dir, request = _make_run(tmp_path, "em-missing-capsule")
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, request, enqueued_at=0.0)

    manager = _DaemonManagerProcess(queue_dir, journal_dir, pool_size=1)
    manager.run()

    state = _wait_state(run_dir, "failed")
    assert "manager_restart_capsule_unavailable" in state["error"]["message"]
    assert state["terminal_notified"] is True
    assert not (queue_dir / f"{request.run_id}.json").exists()
    events = _notification_events(run_dir.path.parent.parent)
    assert [ev["ref_id"] for ev in events].count("em-missing-capsule") == 1
    record = json.loads((journal_dir / f"{request.run_id}.json").read_text(encoding="utf-8"))
    assert record["state"] == "failed_missing_capsule"
    assert record["capsule_in_memory"] is False
    assert record["capsule_scrubbed"] is True
    assert record["evidence"]["reason"] == "manager_restart_capsule_unavailable"
    assert record["evidence"]["source"].endswith("em-missing-capsule.json")
    assert "capsule" not in record


def test_central_manager_timeout_run_notifies(tmp_path, monkeypatch):
    run_dir, request = _make_run(tmp_path, "em-timeout", timeout_s=5.0)
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, request)

    def fake_timeout(rd, manifest, capsule):
        rd.mark_timeout()

    monkeypatch.setattr("lingtai.adapters.posix.daemon_manager._run_one_emanation", fake_timeout)

    manager = _manager_with_capsules(
        queue_dir, journal_dir, pool_size=1, capsules={request.run_id: {}},
    )
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

    manager = _manager_with_capsules(
        queue_dir,
        journal_dir,
        pool_size=1,
        capsules={req1.run_id: {}, req2.run_id: {}},
    )
    manager.run()

    assert starts == ["em-first", "em-second"]
    assert _wait_state(first, "done")["state"] == "done"
    assert _wait_state(second, "done")["state"] == "done"


def test_central_manager_dispatches_high_concurrency_without_waiting_for_queued_pid(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, ["file", "daemon"])
    _enable_detached_fake_llm(monkeypatch, agent, sleep_s=1.0)
    manager = DaemonManager(agent, manager_pool_size=1)

    try:
        start = time.monotonic()
        result = manager._handle_emanate([
            {"task": "slow task A", "tools": ["file"]},
            {"task": "slow task B", "tools": ["file"]},
        ])
        elapsed = time.monotonic() - start

        assert result["status"] == "dispatched", result
        assert result["count"] == 2
        assert elapsed < 0.75

        run_dirs = [manager._emanations[run_id]["run_dir"] for run_id in result["ids"]]
        queue_dir = agent._working_dir / MANAGER_DIR / "queue"

        _wait_for(
            lambda: DaemonRunDir.read_state_from_disk(run_dirs[0].path).get("supervisor_pid"),
            timeout=5.0,
            message="first managed run to start",
        )
        second_state = DaemonRunDir.read_state_from_disk(run_dirs[1].path)
        assert not second_state.get("supervisor_pid")
        queued_job = queue_dir / f"{result['ids'][1]}.json"
        assert queued_job.exists()
        assert "detached-test-key" not in queued_job.read_text(encoding="utf-8")
        active_journal = agent._working_dir / MANAGER_DIR / "journal" / f"{result['ids'][0]}.json"
        assert "detached-test-key" not in active_journal.read_text(encoding="utf-8")

        for run_dir in run_dirs:
            _wait_state(run_dir, "done", timeout=10.0)
    finally:
        _terminate_resident_manager(agent)


def test_parent_restart_does_not_reap_queued_manager_owned_run(tmp_path):
    agent = make_daemon_agent(tmp_path, ["file", "daemon"], working_dir_name="agent")
    run_dir, request = _make_run(tmp_path, "em-queued")
    run_dir.update_state(owner="manager")
    _write_live_manager_pid(agent)
    queue_dir = agent._working_dir / MANAGER_DIR / "queue"
    _write_job(queue_dir, request)

    DaemonManager(agent)

    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    assert state["state"] == "running"
    assert state["owner"] == "manager"
    assert state.get("finished_at") is None


def test_parent_restart_does_not_reap_active_manager_owned_run(tmp_path):
    agent = make_daemon_agent(tmp_path, ["file", "daemon"], working_dir_name="agent")
    run_dir, request = _make_run(tmp_path, "em-active")
    manager_pid = os.getpid()
    run_dir.update_state(
        owner="manager",
        manager_pid=manager_pid,
        manager_start_identity=process_identity(manager_pid),
    )
    _write_live_manager_pid(agent, pid=manager_pid)
    journal_dir = agent._working_dir / MANAGER_DIR / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / f"{request.run_id}.json").write_text(
        json.dumps({
            "schema": "lingtai.daemon_manager_journal.v1",
            "run_id": request.run_id,
            "request": encode_request(request),
            "state": "active",
            "manager_pid": manager_pid,
            "capsule_in_memory": True,
            "capsule_scrubbed": True,
        }),
        encoding="utf-8",
    )

    DaemonManager(agent)

    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    assert state["state"] == "running"
    assert state["owner"] == "manager"
    assert state.get("finished_at") is None


def test_central_manager_cli_route_dispatches_without_waiting_for_queued_pid(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, ["file", "daemon"])
    _install_fake_opencode(tmp_path, monkeypatch, sleep_s=1.0)
    manager = DaemonManager(agent, manager_pool_size=1)

    try:
        start = time.monotonic()
        result = manager._handle_emanate_cli(
            [
                {"task": "slow cli task A", "tools": ["file"]},
                {"task": "slow cli task B", "tools": ["file"]},
            ],
            backend="opencode",
            effective_max_turns=1,
            effective_timeout=10.0,
        )
        elapsed = time.monotonic() - start

        assert result["status"] == "dispatched", result
        assert result["count"] == 2
        assert elapsed < 0.75

        run_dirs = [manager._emanations[run_id]["run_dir"] for run_id in result["ids"]]
        queue_dir = agent._working_dir / MANAGER_DIR / "queue"

        _wait_for(
            lambda: DaemonRunDir.read_state_from_disk(run_dirs[0].path).get("supervisor_pid"),
            timeout=5.0,
            message="first managed CLI run to start",
        )
        second_state = DaemonRunDir.read_state_from_disk(run_dirs[1].path)
        assert not second_state.get("supervisor_pid")
        assert (queue_dir / f"{result['ids'][1]}.json").exists()

        for run_dir in run_dirs:
            _wait_state(run_dir, "done", timeout=10.0)
    finally:
        _terminate_resident_manager(agent)


def test_default_disabled_routing_uses_legacy_spawn_adapter(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, ["file", "daemon"])
    records = install_fake_detached_owner(monkeypatch)
    manager = DaemonManager(agent, manager_pool_size=0)

    result = manager._handle_emanate([
        {"task": "legacy detached", "tools": ["file"]},
    ])

    assert result["status"] == "dispatched"
    assert len(records) == 1
    assert not (agent._working_dir / MANAGER_DIR / "queue").exists()


def test_central_manager_defaults_pool_100_all_batch_sizes(tmp_path):
    agent = SimpleNamespace(
        service=SimpleNamespace(model="mock"),
        _working_dir=tmp_path / "agent",
        _log=lambda *a, **k: None,
    )
    manager = DaemonManager(agent)

    assert manager._manager_pool_size == 100
    assert manager._should_use_central_daemon_manager(1) is (os.name == "posix")
    assert manager._should_use_central_daemon_manager(50) is (os.name == "posix")
    assert manager._should_use_central_daemon_manager(51) is (os.name == "posix")


def test_central_manager_orders_queue_by_enqueued_at(tmp_path, monkeypatch):
    older, older_req = _make_run(tmp_path, "em-z-older")
    newer, newer_req = _make_run(tmp_path, "em-a-newer")
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, newer_req, enqueued_at=200.0)
    _write_job(queue_dir, older_req, enqueued_at=100.0)
    starts: list[str] = []

    def fake_run(rd, manifest, capsule):
        starts.append(rd.run_id)
        rd.mark_done(rd.run_id)

    monkeypatch.setattr("lingtai.adapters.posix.daemon_manager._run_one_emanation", fake_run)

    manager = _manager_with_capsules(
        queue_dir,
        journal_dir,
        pool_size=1,
        capsules={older_req.run_id: {}, newer_req.run_id: {}},
    )
    manager.run()

    assert starts == ["em-z-older", "em-a-newer"]
    assert _wait_state(older, "done")["state"] == "done"
    assert _wait_state(newer, "done")["state"] == "done"


def test_central_manager_terminal_journal_scrubs_capsule(tmp_path, monkeypatch):
    run_dir, request = _make_run(tmp_path, "em-scrub")
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    _write_job(queue_dir, request, capsule={"api_key": "secret", "backend_env": {"X": "Y"}})

    def fake_run(rd, manifest, capsule):
        assert capsule["api_key"] == "secret"
        rd.mark_done("manager completed")

    monkeypatch.setattr("lingtai.adapters.posix.daemon_manager._run_one_emanation", fake_run)

    manager = _manager_with_capsules(
        queue_dir,
        journal_dir,
        pool_size=1,
        capsules={request.run_id: {"api_key": "secret", "backend_env": {"X": "Y"}}},
    )
    manager.run()

    _wait_state(run_dir, "done")
    record = json.loads((journal_dir / "em-scrub.json").read_text(encoding="utf-8"))
    assert "capsule" not in record
    assert record["capsule_scrubbed"] is True


def test_central_manager_recovery_error_keeps_evidence(tmp_path):
    journal_dir = tmp_path / "manager" / "journal"
    journal_dir.mkdir(parents=True)
    (journal_dir / "em-bad.json").write_text(
        json.dumps({
            "schema": "lingtai.daemon_manager_journal.v1",
            "run_id": "em-bad",
            "request": "not-a-valid-request",
            "state": "active",
        }),
        encoding="utf-8",
    )

    manager = _DaemonManagerProcess(tmp_path / "manager" / "queue", journal_dir, pool_size=1)
    manager.recover_interrupted_active_runs()

    record = json.loads((journal_dir / "em-bad.json").read_text(encoding="utf-8"))
    assert record["state"] == "recovery_error"
    assert record["source"].endswith("em-bad.json")
    assert record["error"]["type"]


def test_central_manager_malformed_top_level_queue_job_is_terminal(tmp_path):
    queue_dir = tmp_path / "manager" / "queue"
    journal_dir = tmp_path / "manager" / "journal"
    queue_dir.mkdir(parents=True)
    (queue_dir / "em-bad.json").write_text("[1, 2, 3]", encoding="utf-8")

    manager = _DaemonManagerProcess(queue_dir, journal_dir, pool_size=1)
    manager.run()

    assert not (queue_dir / "em-bad.json").exists()
    record = json.loads((journal_dir / "em-bad.json").read_text(encoding="utf-8"))
    assert record["state"] == "failed_malformed_queue_job"
    assert record["error"]["type"] == "ValueError"
