"""Real-process acceptance tests for the detached daemon supervisor.

These prove the behavioral contract in
`scratch/daemon-detached-supervisor-20260714/parent-contract.md`: a real
OS-process boundary (an actual `subprocess.Popen` detached supervisor, not a
structural mock) survives the launching process's own shutdown path, enforces
its own deadline, and publishes terminal notifications without a live parent
agent. The LLM call itself is faked via a deterministic, in-repo adapter
registered only when `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM=1` is set in the
child's environment — the same "deterministic fake over live network/model
calls" approach the rest of the daemon test suite already uses (see
`tests/test_daemon.py`'s `FakeService`/`create_session` monkeypatches), the
only difference being this fake must be registered *inside* the spawned
subprocess (an env-var-gated hook, not a monkeypatch, since a monkeypatch
cannot cross a process boundary).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import textwrap

import pytest
import sys
import time
from pathlib import Path

from lingtai.tools.daemon.run_dir import DaemonRunDir
from lingtai.kernel.daemon_supervisor.manifest import build_manifest, write_manifest, manifest_path_for
from lingtai.kernel.daemon_supervisor import DaemonSupervisorRequest
from lingtai.adapters.posix.daemon_supervisor import PosixDaemonSupervisorAdapter
from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter


FAKE_LLM_ENV = "LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM"


def _disk_state(run_dir: DaemonRunDir) -> dict:
    """Fresh disk read of *run_dir*'s daemon.json.

    A spawned supervisor subprocess writes to the SAME daemon.json file but
    has its own separate in-memory ``DaemonRunDir`` object — this test
    process's ``run_dir`` handle never observes those writes via
    ``state_snapshot()`` (which only returns this process's own copy).
    Tests must poll via a fresh disk read, exactly like
    ``DaemonManager._read_run_dir_state_from_disk`` does in production.
    """
    try:
        return DaemonRunDir.read_state_from_disk(run_dir.path)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _poll_until(predicate, *, timeout=15.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _make_run_dir(tmp_path: Path, *, task="say hi", timeout_s=30.0, max_turns=5) -> DaemonRunDir:
    parent = tmp_path / "agent"
    parent.mkdir(parents=True, exist_ok=True)
    run_dir = DaemonRunDir(
        parent_working_dir=parent,
        handle="em-test",
        run_id="em-test",
        task=task,
        tools=[],
        model="fake-model",
        max_turns=max_turns,
        timeout_s=timeout_s,
        parent_addr=parent.name,
        parent_pid=os.getpid(),
        system_prompt=f"You are a test daemon.\n\nYour task:\n{task}",
        call_parameters={"task": task, "tools": []},
    )
    return run_dir


def _spawn_lingtai_supervisor(run_dir: DaemonRunDir, *, task="say hi", timeout_s=30.0, max_turns=5, extra_env=None):
    manifest = build_manifest(
        run_id=run_dir.run_id,
        backend="lingtai",
        parent_working_dir=str(run_dir.path.parent.parent),
        run_dir=str(run_dir.path),
        task=task,
        tools=[],
        max_turns=max_turns,
        timeout_s=timeout_s,
        group_id=None,
        llm={
            "provider": "lingtai-supervisor-test-fake",
            "model": "fake-model",
            "api_key": None,
            "base_url": None,
            "context_window": None,
            "provider_defaults": None,
        },
    )
    write_manifest(run_dir.path, manifest)
    request = DaemonSupervisorRequest(
        run_id=run_dir.run_id,
        manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )
    env = dict(os.environ)
    env[FAKE_LLM_ENV] = "1"
    tests_dir = str(Path(__file__).parent)
    repo_src = str(Path(__file__).parent.parent / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([tests_dir, repo_src] + [p for p in existing_pp.split(os.pathsep) if p])
    )
    if extra_env:
        env.update(extra_env)
    # Same mechanics as PosixDaemonSupervisorAdapter.spawn_detached, but with
    # an explicit env override for the test-only fake-LLM hook (the real
    # adapter always inherits full os.environ, no override point).
    proc = subprocess.Popen(
        [sys.executable, "-m", "lingtai.adapters.posix.daemon_supervisor_entrypoint",
         __import__("lingtai.kernel.daemon_supervisor", fromlist=["encode_request"]).encode_request(request)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    return proc


def test_posix_adapter_spawns_real_detached_process(tmp_path):
    """The production Port/adapter actually launches a real OS process."""
    run_dir = _make_run_dir(tmp_path, task="say hi", timeout_s=30.0)
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="lingtai",
        parent_working_dir=str(run_dir.path.parent.parent),
        run_dir=str(run_dir.path), task="say hi", tools=[],
        max_turns=1, timeout_s=30.0, group_id=None,
        llm={"provider": "lingtai-supervisor-test-fake", "model": "fake-model",
             "api_key": None, "base_url": None, "context_window": None,
             "provider_defaults": None},
    )
    write_manifest(run_dir.path, manifest)
    request = DaemonSupervisorRequest(
        run_id=run_dir.run_id,
        manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )
    PosixDaemonSupervisorAdapter().spawn_detached(request)

    def _pid_recorded():
        state = _disk_state(run_dir)
        pid = state.get("supervisor_pid")
        return pid if pid else None

    pid = _poll_until(_pid_recorded, timeout=10.0)
    assert pid != os.getpid()

    # No fake-LLM env in this call — real provider construction should fail
    # loudly and commit `failed` rather than silently hang, proving the
    # process really tried to run (not a no-op stub).
    def _terminal():
        st = _disk_state(run_dir).get("state")
        return st if st in ("failed", "done", "cancelled", "timeout") else None

    terminal_state = _poll_until(_terminal, timeout=10.0)
    assert terminal_state == "failed"


def test_detached_lingtai_run_survives_agent_stop_shutdown_and_reaches_done(tmp_path):
    """Acceptance test 1: real detached LingTai run outlives shutdown_for_agent_stop."""
    from lingtai.tools import daemon as daemon_module

    run_dir = _make_run_dir(tmp_path, task="say hi", timeout_s=30.0)
    # Sleep briefly inside the fake LLM call so the supervisor is still
    # genuinely mid-run when shutdown_for_agent_stop below is invoked —
    # otherwise a trivial task could finish before the assertion even runs,
    # making "still alive" trivially true rather than a real proof.
    proc = _spawn_lingtai_supervisor(
        run_dir, task="say hi", timeout_s=30.0,
        extra_env={"LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP": "2"},
    )
    try:
        _poll_until(lambda: _disk_state(run_dir).get("supervisor_pid"), timeout=10.0)

        # Simulate the exact shutdown path agent_stop/refresh performs. This
        # must be a no-op with respect to the detached run: it was never
        # registered as a pool/cli_proc/future the manager owns.
        from types import SimpleNamespace
        agent = SimpleNamespace(
            service=SimpleNamespace(model="mock-model"),
            _working_dir=run_dir.path.parent.parent,
            _log=lambda *a, **k: None,
        )
        mgr = daemon_module.DaemonManager(agent)
        report = mgr.shutdown_for_agent_stop(reason="agent_stop", wait_timeout=0.0)
        assert report["cli_processes_killed"] == 0
        assert report["cancelled"] == 0

        # The detached supervisor process must still be alive right after
        # the parent's shutdown path ran (not killed by it) — it is still
        # inside the 2s fake-LLM sleep.
        assert proc.poll() is None, "detached supervisor was killed by shutdown_for_agent_stop"

        def _terminal_state():
            st = _disk_state(run_dir).get("state")
            return st if st == "done" else None

        _poll_until(_terminal_state, timeout=15.0)
        result_text = run_dir.result_path.read_text(encoding="utf-8")
        assert "fake-response" in result_text

        # Terminal notification is published by the owner, independently of
        # the parent.  State and notification are separate durable writes, so
        # wait for the receipt rather than asserting across that tiny race.
        store = PosixNotificationStoreAdapter(run_dir.path.parent.parent)
        def _terminal_event():
            snap = store.snapshot(lambda ch: ch == "system")
            events = snap.get("system", {}).get("data", {}).get("events", [])
            return next((ev for ev in events if ev.get("ref_id") == "em-test"), None)
        assert _poll_until(_terminal_event, timeout=5.0)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_parent_pool_and_cli_procs_never_track_detached_run(tmp_path):
    """Acceptance test 2: no non-daemon executor/future keeps the parent alive."""
    from lingtai.tools import daemon as daemon_module
    from types import SimpleNamespace

    parent_dir = tmp_path / "agent2"
    parent_dir.mkdir()
    agent = SimpleNamespace(
        service=SimpleNamespace(model="mock-model"),
        _working_dir=parent_dir,
        _log=lambda *a, **k: None,
    )
    mgr = daemon_module.DaemonManager(agent)
    assert mgr._pools == []
    assert mgr._cli_procs == []


def test_fresh_manager_lists_and_checks_detached_run_after_registry_reset(tmp_path):
    """Acceptance test 3: fresh manager (empty in-memory registry) still sees the run."""
    from lingtai.tools import daemon as daemon_module
    from types import SimpleNamespace

    run_dir = _make_run_dir(tmp_path, task="say hi", timeout_s=30.0)
    proc = _spawn_lingtai_supervisor(run_dir, task="say hi", timeout_s=30.0)
    try:
        _poll_until(lambda: _disk_state(run_dir).get("state") == "done", timeout=20.0)
        proc.wait(timeout=5)

        agent = SimpleNamespace(
            service=SimpleNamespace(model="mock-model"),
            _working_dir=run_dir.path.parent.parent,
            _log=lambda *a, **k: None,
        )
        fresh_mgr = daemon_module.DaemonManager(agent)  # empty _emanations
        assert fresh_mgr._emanations == {}

        check = fresh_mgr._handle_check("em-test")
        assert check.get("state") == "done"

        listing = fresh_mgr._handle_list()
        ids = [e.get("id") or e.get("run_id") for e in listing.get("emanations", listing.get("data", {}).get("emanations", []) if isinstance(listing.get("data"), dict) else [])]
        # _handle_list's exact top-level shape is exercised by other tests;
        # here we only need proof the run is discoverable post-refresh.
        found = "em-test" in json.dumps(listing)
        assert found
    finally:
        if proc.poll() is None:
            proc.terminate()


def test_explicit_reclaim_cancels_detached_run_via_control_spool(tmp_path):
    """Acceptance test 4: explicit reclaim truthfully cancels a detached run."""
    from lingtai.tools import daemon as daemon_module
    from types import SimpleNamespace

    run_dir = _make_run_dir(tmp_path, task="sleep-forever", timeout_s=120.0)
    # The fake LLM's .send() blocks synchronously for this long — the
    # cancel_event set by the reclaim control request is only observed
    # BETWEEN turns (matching the in-process daemon path's own limitation:
    # a blocking session.send() cannot be interrupted mid-call). Send()
    # returns before this test's own reclaim-confirm poll window so the
    # cancellation is actually observed after the fake call unblocks, on
    # the very next cancel_event check ahead of a nonexistent next turn.
    proc = _spawn_lingtai_supervisor(
        run_dir, task="sleep-forever", timeout_s=120.0,
        extra_env={"LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP": "0.5"},
    )
    try:
        _poll_until(lambda: _disk_state(run_dir).get("supervisor_pid"), timeout=10.0)

        agent = SimpleNamespace(
            service=SimpleNamespace(model="mock-model"),
            _working_dir=run_dir.path.parent.parent,
            _log=lambda *a, **k: None,
        )
        mgr = daemon_module.DaemonManager(agent)
        mgr._emanations["em-test"] = {
            "detached": True, "task": "sleep-forever", "start_time": time.time(),
            "timeout_s": 120.0, "run_dir": run_dir, "backend": "lingtai",
        }
        result = mgr._handle_reclaim()
        assert result["status"] == "reclaimed"
        assert result["cancelled"] >= 1
        assert _disk_state(run_dir).get("state") == "cancelled"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_detached_supervisor_enforces_own_timeout_after_parent_gone(tmp_path):
    """Acceptance test 5: the supervisor's own deadline fires with nobody watching.

    The fake LLM's single blocking `.send()` call cannot itself be
    interrupted mid-flight (same limitation the in-process daemon path has
    for a genuinely hung provider call) — the deadline is observed on the
    very next cancel_event check, which for a single-turn task is
    immediately after `.send()` returns. The fake sleeps past the 2s
    deadline but returns well within the poll window so that post-return
    check is what this test actually observes firing.
    """
    run_dir = _make_run_dir(tmp_path, task="sleep-forever", timeout_s=2.0)
    proc = _spawn_lingtai_supervisor(
        run_dir, task="sleep-forever", timeout_s=2.0,
        extra_env={"LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP": "4"},
    )
    try:
        def _terminal():
            st = _disk_state(run_dir).get("state")
            return st if st == "timeout" else None

        _poll_until(_terminal, timeout=15.0)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_terminal_notification_published_once_across_supervisor_and_reconcile(tmp_path):
    """Acceptance test 6: reconciliation does not duplicate the terminal event."""
    from lingtai.tools import daemon as daemon_module
    from types import SimpleNamespace

    run_dir = _make_run_dir(tmp_path, task="say hi", timeout_s=30.0)
    proc = _spawn_lingtai_supervisor(run_dir, task="say hi", timeout_s=30.0)
    try:
        _poll_until(lambda: _disk_state(run_dir).get("state") == "done", timeout=20.0)
        proc.wait(timeout=5)

        store = PosixNotificationStoreAdapter(run_dir.path.parent.parent)
        snap_before = store.snapshot(lambda ch: ch == "system")
        events_before = snap_before.get("system", {}).get("data", {}).get("events", [])
        matching_before = [e for e in events_before if e.get("ref_id") == "em-test"]
        assert len(matching_before) == 1

        # A fresh manager's startup reconciliation must not duplicate it.
        agent = SimpleNamespace(
            service=SimpleNamespace(model="mock-model"),
            _working_dir=run_dir.path.parent.parent,
            _log=lambda *a, **k: None,
        )
        daemon_module.DaemonManager(agent)  # runs _reconcile_terminal_notifications in __init__

        snap_after = store.snapshot(lambda ch: ch == "system")
        events_after = snap_after.get("system", {}).get("data", {}).get("events", [])
        matching_after = [e for e in events_after if e.get("ref_id") == "em-test"]
        assert len(matching_after) == 1
    finally:
        if proc.poll() is None:
            proc.terminate()


def test_agent_stop_does_not_reap_live_detached_supervisor_as_dead_parent(tmp_path):
    """A detached run's parent_pid mismatch must not trigger orphan-reaping."""
    from lingtai.tools import daemon as daemon_module
    from types import SimpleNamespace

    run_dir = _make_run_dir(tmp_path, task="sleep-forever", timeout_s=30.0)
    proc = _spawn_lingtai_supervisor(
        run_dir, task="sleep-forever", timeout_s=30.0,
        extra_env={"LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP": "8"},
    )
    try:
        _poll_until(lambda: _disk_state(run_dir).get("owner") == "supervisor", timeout=10.0)

        agent = SimpleNamespace(
            service=SimpleNamespace(model="mock-model"),
            _working_dir=run_dir.path.parent.parent,
            _log=lambda *a, **k: None,
        )
        daemon_module.DaemonManager(agent)  # constructor runs _reap_dead_parent_daemon_records

        state = _disk_state(run_dir)
        assert state.get("state") == "running"
        assert state.get("error") is None
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_codex_detached_cli_child_survives_parent_stop_and_reclaim_is_scoped(tmp_path):
    """Acceptance test 7: a fake deterministic CLI child stays under the supervisor."""
    from lingtai.tools import daemon as daemon_module
    from lingtai.kernel.daemon_supervisor import DaemonSupervisorRequest, encode_request
    from lingtai.kernel.daemon_supervisor.manifest import build_manifest, write_manifest, manifest_path_for
    from types import SimpleNamespace

    run_dir = _make_run_dir(tmp_path, task="codex task", timeout_s=30.0)
    fake_cli = Path(__file__).parent / "_fake_codex_cli.py"
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="codex",
        parent_working_dir=str(run_dir.path.parent.parent),
        run_dir=str(run_dir.path), task="codex task", tools=[],
        max_turns=1, timeout_s=30.0, group_id=None,
        backend_argv=[sys.executable, str(fake_cli), "--sleep", "6"],
    )
    write_manifest(run_dir.path, manifest)
    request = DaemonSupervisorRequest(
        run_id=run_dir.run_id,
        manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )
    PosixDaemonSupervisorAdapter().spawn_detached(request)
    try:
        def _cli_pid():
            return _disk_state(run_dir).get("cli_pid")

        cli_pid = _poll_until(_cli_pid, timeout=10.0)

        # shutdown_for_agent_stop must not touch this CLI child's process
        # group — it was never registered with the parent's DaemonManager.
        agent = SimpleNamespace(
            service=SimpleNamespace(model="mock-model"),
            _working_dir=run_dir.path.parent.parent,
            _log=lambda *a, **k: None,
        )
        mgr = daemon_module.DaemonManager(agent)
        mgr.shutdown_for_agent_stop(reason="agent_stop", wait_timeout=0.0)

        # Exact-PID liveness check — no broad process matching.
        try:
            os.kill(cli_pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert alive, "detached codex CLI child was killed by parent shutdown_for_agent_stop"

        def _terminal():
            st = _disk_state(run_dir).get("state")
            return st if st == "done" else None

        _poll_until(_terminal, timeout=15.0)
        assert "fake-codex-output" in run_dir.result_path.read_text(encoding="utf-8")
    finally:
        pid = _disk_state(run_dir).get("cli_pid")
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass


def test_cli_session_policy_preserves_legacy_and_inherits_for_detached():
    """Only detached execution hosts inherit their already isolated process group."""
    from lingtai.tools.daemon import DaemonManager
    from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost

    assert DaemonManager.__new__(DaemonManager)._cli_start_new_session() is True
    host = DetachedDaemonExecutionHost.__new__(DetachedDaemonExecutionHost)
    assert host._cli_start_new_session() is False


def test_codex_detached_reclaim_kills_exact_own_process_group_only(tmp_path):
    """Explicit reclaim of a detached codex run kills only its own pgid."""
    from lingtai.kernel.daemon_supervisor import DaemonSupervisorRequest
    from lingtai.kernel.daemon_supervisor.manifest import build_manifest, write_manifest, manifest_path_for
    from lingtai.kernel.daemon_supervisor import control

    run_dir = _make_run_dir(tmp_path, task="codex task", timeout_s=30.0)
    fake_cli = Path(__file__).parent / "_fake_codex_cli.py"
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="codex",
        parent_working_dir=str(run_dir.path.parent.parent),
        run_dir=str(run_dir.path), task="codex task", tools=[],
        max_turns=1, timeout_s=30.0, group_id=None,
        backend_argv=[sys.executable, str(fake_cli), "--sleep", "20"],
    )
    write_manifest(run_dir.path, manifest)
    request = DaemonSupervisorRequest(
        run_id=run_dir.run_id,
        manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )
    PosixDaemonSupervisorAdapter().spawn_detached(request)
    try:
        cli_pid = _poll_until(lambda: _disk_state(run_dir).get("cli_pid"), timeout=10.0)
        state = _disk_state(run_dir)
        assert os.getpgid(cli_pid) == state["execution_pgid"]
        control.submit_request(run_dir.path, "reclaim", {})

        def _terminal():
            st = _disk_state(run_dir).get("state")
            return st if st == "cancelled" else None

        _poll_until(_terminal, timeout=15.0)

        def _cli_dead():
            try:
                os.kill(cli_pid, 0)
                return False
            except ProcessLookupError:
                return True

        _poll_until(_cli_dead, timeout=10.0)
    finally:
        pass


def test_no_broad_process_cleanup_in_supervisor_source():
    """Structural guard: the supervisor never uses machine-wide process matching."""
    root = Path(__file__).parent.parent / "src" / "lingtai"
    src = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            Path("tools/daemon/supervisor_runtime.py"),
            Path("tools/daemon/runtime.py"),
            Path("adapters/posix/process_identity.py"),
        )
    )
    forbidden = ["ps aux", "ps -e", "pkill", "killall", "xargs kill", "ps|grep", "ps | grep"]
    for token in forbidden:
        assert token not in src, f"found forbidden broad process pattern: {token!r}"


def test_detached_fake_cli_receives_secret_argv_and_credential_env_without_durable_leak(
    tmp_path, monkeypatch,
):
    """The real detached child gets auth, while every run artifact is redacted."""
    fake_cli = Path(__file__).parent / "_fake_codex_cli.py"
    report = tmp_path / "fake-cli-report.json"
    argv_secret = "argv-secret-20260714"
    env_secret = "env-secret-20260714"
    monkeypatch.setenv("OPENAI_API_KEY", env_secret)
    monkeypatch.setenv("LINGTAI_FAKE_CLI_REPORT", str(report))
    run_dir = _make_run_dir(tmp_path, task="credential regression")
    raw_argv = [sys.executable, str(fake_cli), "--token", argv_secret]
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="codex",
        parent_working_dir=str(run_dir.path.parent.parent), run_dir=str(run_dir.path),
        task="credential regression", tools=[], max_turns=1, timeout_s=30,
        group_id=None, backend_argv=raw_argv,
    )
    write_manifest(run_dir.path, manifest)
    request = DaemonSupervisorRequest(
        run_id=run_dir.run_id, manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )
    PosixDaemonSupervisorAdapter().spawn_detached(
        request, capsule={"backend_argv": raw_argv, "credential_env": {"OPENAI_API_KEY": env_secret}}
    )
    _poll_until(lambda: json.loads(report.read_text(encoding="utf-8")) if report.exists() else None)
    received = json.loads(report.read_text(encoding="utf-8"))
    assert argv_secret in received["argv"]
    assert received["openai_api_key"] == env_secret
    for path in run_dir.path.rglob("*"):
        if path.is_file():
            assert argv_secret not in path.read_text(encoding="utf-8", errors="replace")
            assert env_secret not in path.read_text(encoding="utf-8", errors="replace")


def test_detached_fake_cli_auth_survives_parent_exit(tmp_path):
    """A short-lived launcher can exit before the credentialed CLI starts."""
    parent = tmp_path / "parent-exit"
    report = tmp_path / "parent-exit-report.json"
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = "env-secret-parent-exit"
    env["LINGTAI_FAKE_CLI_REPORT"] = str(report)
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([
            str(Path(__file__).parent), str(Path(__file__).parent.parent / "src"),
            *[p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p],
        ])
    )
    launched = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "_detached_cli_parent.py"),
         str(parent), str(Path(__file__).parent / "_fake_codex_cli.py"), str(report)],
        env=env, text=True, capture_output=True, timeout=10,
    )
    assert launched.returncode == 0, launched.stderr
    run_path = Path(launched.stdout.strip().splitlines()[-1])
    _poll_until(lambda: json.loads(report.read_text(encoding="utf-8")) if report.exists() else None)
    received = json.loads(report.read_text(encoding="utf-8"))
    assert received["openai_api_key"] == env["OPENAI_API_KEY"]
    assert "argv-secret-parent-exit" in received["argv"]
    for path in run_path.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            assert env["OPENAI_API_KEY"] not in content
            assert "argv-secret-parent-exit" not in content


def test_real_manager_handle_emanate_capsule_and_fresh_active_control(tmp_path, monkeypatch):
    """The product path uses a real manager and fresh-manager durable control."""
    from tests._daemon_helpers import make_daemon_agent
    from lingtai.tools.daemon import DaemonManager

    agent = make_daemon_agent(tmp_path)
    # Configure the real parent service shape; the deterministic adapter is
    # registered only after the detached supervisor starts.
    agent.service.provider = "lingtai-supervisor-test-fake"
    agent.service.model = "fake-model"
    agent.service.api_key = "INLINE_API_KEY_SENTINEL"
    agent.service._base_url = None
    # The private in-process alias must be normalized to the public manifest
    # key in the one-shot capsule.  ``api_compat`` is intentionally
    # redaction-shaped: the durable manifest hides it, while the execution
    # child must still receive the real value and pass its redaction gate.
    agent.service._provider_defaults = {
        "lingtai-supervisor-test-fake": {
            "api_compat": "PROVIDER_DEFAULT_RUNTIME_SENTINEL",
        },
    }
    monkeypatch.setenv(FAKE_LLM_ENV, "1")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            dict.fromkeys([
                str(Path(__file__).parent),
                str(Path(__file__).parent.parent / "src"),
                *[p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p],
            ])
        ),
    )
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH", "1")
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP", "1")

    mgr = agent.get_capability("daemon")
    result = mgr.handle({
        "action": "emanate",
        "tasks": [{"task": "real manager detached path", "tools": []}],
        "timeout": 30,
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    run_dir = mgr._emanations[em_id]["run_dir"]
    _poll_until(lambda: _disk_state(run_dir).get("owner") == "supervisor", timeout=10)

    # A fresh manager resolves the exact run directory, checks supervisor
    # identity, and submits control without adopting the process.
    fresh = DaemonManager(agent)
    ask = fresh.handle({"action": "ask", "id": em_id, "message": "keep going"})
    assert ask == {"status": "sent", "id": em_id}
    stop = fresh.shutdown_for_agent_stop(reason="agent_stop", wait_timeout=0.0)
    assert stop["cancelled"] == 0
    _poll_until(lambda: _disk_state(run_dir).get("state") == "done", timeout=20)
    check = fresh.handle({"action": "check", "id": em_id})
    assert check["state"] == "done"
    listing = fresh.handle({"action": "list", "include_done": True})
    assert em_id in json.dumps(listing)

    # The inline value is consumed by the fake provider only as a boolean
    # capability check; it must never enter durable state or the notification.
    raw = "INLINE_API_KEY_SENTINEL"
    manifest = (run_dir.path / "supervisor_manifest.json").read_text(encoding="utf-8")
    assert raw not in manifest
    assert "PROVIDER_DEFAULT_RUNTIME_SENTINEL" not in manifest
    manifest_data = json.loads(manifest)
    assert manifest_data["llm"]["provider_defaults"] == {
        "lingtai-supervisor-test-fake": {"api_compat": "<redacted>"}
    }
    for path in run_dir.path.rglob("*"):
        if path.is_file():
            assert raw not in path.read_text(encoding="utf-8", errors="replace")
    assert raw not in json.dumps(
        PosixNotificationStoreAdapter(run_dir.path.parent.parent)
        .snapshot(lambda ch: ch == "system")
    )


def test_manager_capsule_sentinel_redacts_public_mcp_and_cli_values(tmp_path):
    """Manifest/public artifacts contain no inline LLM/MCP/CLI sentinel values."""
    from lingtai.kernel.daemon_supervisor.manifest import build_manifest

    sentinels = {
        "llm": {
            "provider": "fake", "model": "fake", "api_key": "INLINE_KEY_SENTINEL",
            "api_key_env": "INLINE_KEY_ENV",
            "provider_defaults": {"default_header": "PROVIDER_DEFAULT_SENTINEL"},
        },
        "mcp": [
            {
                "name": "stdio-secret", "transport": "stdio", "command": "python",
                "env": {"MCP_TOKEN": "MCP_ENV_SENTINEL"},
            },
            {
                "name": "http-secret", "transport": "http", "url": "https://example.test/mcp",
                "headers": {"Authorization": "Bearer MCP_HEADER_SENTINEL"},
            },
        ],
        "backend_argv": ["--api-key=CLI_OPTION_SENTINEL", "--safe-flag"],
    }
    manifest = build_manifest(
        run_id="em-sentinel", backend="lingtai", parent_working_dir=str(tmp_path),
        run_dir=str(tmp_path / "em-sentinel"), task="sentinel test", tools=[],
        max_turns=1, timeout_s=30, group_id=None, **sentinels,
    )
    rendered = json.dumps(manifest)
    for value in (
        "INLINE_KEY_SENTINEL", "MCP_ENV_SENTINEL", "MCP_HEADER_SENTINEL",
        "CLI_OPTION_SENTINEL", "PROVIDER_DEFAULT_SENTINEL",
    ):
        assert value not in rendered
    assert manifest["llm"]["api_key"] == "<redacted>"
    assert manifest["llm"]["api_key_env"] == "INLINE_KEY_ENV"
    assert manifest["backend_argv"] == ["--api-key=<redacted>", "--safe-flag"]
    assert manifest["mcp"][0]["env"]["MCP_TOKEN"] == "<redacted>"
    assert manifest["mcp"][1]["headers"]["Authorization"] == "<redacted>"


def test_capsule_is_bounded_before_spawn_and_env_is_secret_scrubbed(tmp_path, monkeypatch):
    """Transport rejects oversized payloads and removes inherited credentials."""
    from lingtai.adapters.posix import daemon_supervisor as adapter_mod

    run_dir = _make_run_dir(tmp_path, task="capsule", timeout_s=30)
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="lingtai",
        parent_working_dir=str(run_dir.path.parent.parent), run_dir=str(run_dir.path),
        task="capsule", tools=[], max_turns=1, timeout_s=30, group_id=None,
        llm={"provider": "fake", "model": "fake", "api_key_env": "CUSTOM_CRED_REF"},
    )
    write_manifest(run_dir.path, manifest)
    request = DaemonSupervisorRequest(
        run_id=run_dir.run_id, manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )
    monkeypatch.setenv("CUSTOM_CRED_REF", "CUSTOM_CRED_SENTINEL")
    monkeypatch.setenv("OPENAI_API_KEY", "OPENAI_ENV_SENTINEL")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ANTHROPIC_ENV_SENTINEL")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_OAUTH_SENTINEL")
    monkeypatch.setenv("UNRELATED_TOKEN", "TOKEN_SENTINEL")
    monkeypatch.setenv("ORDINARY_TEST_VALUE", "kept")
    env = adapter_mod._supervisor_environment(request)
    assert "CUSTOM_CRED_REF" not in env
    assert "UNRELATED_TOKEN" not in env
    assert env["ORDINARY_TEST_VALUE"] == "kept"
    assert adapter_mod.selected_credential_environment("codex") == {
        "OPENAI_API_KEY": "OPENAI_ENV_SENTINEL"
    }
    assert adapter_mod.selected_credential_environment("claude-p") == {}
    assert adapter_mod.selected_credential_environment("claude-code") == {}
    called = []
    monkeypatch.setattr(adapter_mod.subprocess, "Popen", lambda *a, **k: called.append(1))
    with pytest.raises(ValueError, match="exceeds"):
        PosixDaemonSupervisorAdapter().spawn_detached(
            request, capsule={"blob": "x" * (adapter_mod._MAX_CAPSULE_BYTES + 1)}
        )
    assert called == []


def test_real_manager_parent_interpreter_exit_keeps_supervisor_owner(tmp_path, monkeypatch):
    """A contained parent interpreter can exit after real manager dispatch."""
    child = Path(__file__).parent / "_manager_detached_parent.py"
    parent = (tmp_path / "parent-exit-agent").resolve()
    env = dict(os.environ)
    env[FAKE_LLM_ENV] = "1"
    env["LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([str(Path(__file__).parent), str(Path(__file__).parent.parent / "src")] +
                      [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    )
    parent_proc = subprocess.run(
        [sys.executable, str(child), str(parent)],
        env=env, text=True, capture_output=True, timeout=15,
    )
    assert parent_proc.returncode == 0, parent_proc.stderr
    em_id = parent_proc.stdout.strip().splitlines()[-1]
    run_path = parent / "daemons" / em_id
    state = _poll_until(
        lambda: _disk_state(DaemonRunDir.attach(run_path))
        if _disk_state(DaemonRunDir.attach(run_path)).get("state") == "done" else None,
        timeout=20,
    )
    assert state["owner"] == "supervisor"
    assert (run_path / "result.txt").is_file()
    assert "PARENT_EXIT_INLINE_SENTINEL" not in (run_path / "supervisor_manifest.json").read_text()


@pytest.mark.parametrize(
    ("backend", "session_key", "session_id", "expected"),
    [
        ("codex", "codex_session_id", "fake-codex-session", "fake-codex-followup"),
        ("opencode", "opencode_session_id", "fake-opencode-session", "fake-opencode-followup"),
    ],
)
def test_fresh_manager_terminal_cli_ask_has_one_detached_resume_owner(
    tmp_path, monkeypatch, backend, session_key, session_id, expected,
):
    """Supported CLI resume is a durable generation, not a parent future."""
    from tests._daemon_helpers import make_daemon_agent
    from lingtai.kernel.daemon_supervisor.manifest import build_manifest, write_manifest
    from lingtai.tools.daemon import DaemonManager

    monkeypatch.setenv("PATH", str(Path(__file__).parent) + os.pathsep + os.environ["PATH"])
    calls_path = tmp_path / f"{backend}-resume-calls.jsonl"
    monkeypatch.setenv("FAKE_DAEMON_RESUME_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_DAEMON_CLI_SLEEP", "0.5")
    parent = tmp_path / "agent"
    parent.mkdir()
    run_dir = _make_run_dir(tmp_path, task="terminal primary")
    run_dir.update_state(
        backend=backend, owner="supervisor", state="done", supervisor_pid=None,
        **{session_key: session_id},
    )
    write_manifest(
        run_dir.path,
        build_manifest(
            run_id=run_dir.run_id, backend=backend,
            parent_working_dir=str(run_dir.path.parent.parent),
            run_dir=str(run_dir.path), task="terminal primary", tools=[],
            max_turns=1, timeout_s=30, group_id=None, backend_argv=[],
        ),
    )
    agent = make_daemon_agent(run_dir.path.parent.parent, ["daemon"], working_dir_name="")
    first_manager = DaemonManager(agent)
    second_manager = DaemonManager(agent)
    from concurrent.futures import ThreadPoolExecutor
    import threading

    start = threading.Barrier(3)

    def _ask(manager, message):
        start.wait(timeout=5)
        return manager.handle({"action": "ask", "id": run_dir.run_id, "message": message})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_ask, first_manager, "follow"),
            pool.submit(_ask, second_manager, "concurrent"),
        ]
        start.wait(timeout=5)
        results = [future.result(timeout=10) for future in futures]

    assert sorted(result["status"] for result in results) == ["busy", "sent"]
    sent = next(result for result in results if result["status"] == "sent")
    assert sent.get("generation")
    assert not first_manager._emanations[run_dir.run_id].get("ask_future")
    assert not second_manager._emanations[run_dir.run_id].get("ask_future")
    claims = list((run_dir.path / "resume-claims").glob("resume-*.json"))
    assert len(claims) == 1

    def _followup_done():
        state = _disk_state(run_dir)
        return state if state.get("followup_status") in {"done", "failed", "timeout"} else None

    state = _poll_until(_followup_done, timeout=20)
    assert state["followup_status"] == "done"
    assert expected in (state.get("followup_result_preview") or "")
    assert state["resume_state"] == "done"
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    assert calls[0]["name"] == backend
    check = first_manager.handle({"action": "check", "id": run_dir.run_id})
    assert check["followup_status"] == "done"
    assert check["followup_result_path"] == state["followup_result_path"]


def test_detached_execution_composes_inherited_process_and_terminal_ports(tmp_path):
    """Detached initial/resume composition supplies both inherited Ports."""
    from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
    from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
    from lingtai.adapters.posix.interactive_terminal import PosixInteractiveTerminalAdapter
    from threading import Event

    run_dir = _make_run_dir(tmp_path, task="port composition")
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="claude-p",
        parent_working_dir=str(run_dir.path.parent.parent), run_dir=str(run_dir.path),
        task="port composition", tools=[], max_turns=1, timeout_s=30,
        group_id=None,
        llm={"provider": "fake", "model": "fake", "api_key": None,
             "base_url": None, "context_window": None, "provider_defaults": None},
    )
    host = DetachedDaemonExecutionHost(run_dir, manifest, Event(), Event())
    assert isinstance(host._process_port, PosixDaemonProcessPort)
    assert isinstance(host._interactive_terminal_port, PosixInteractiveTerminalAdapter)
    assert host._process_port._start_new_session is False
    assert host._interactive_terminal_port._start_new_session is False


def test_detached_lingtai_file_surface_executes_against_parent_workdir(tmp_path):
    """Detached LingTai composition can execute its advertised file floor."""
    from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
    from threading import Event

    run_dir = _make_run_dir(tmp_path, task="detached file execution")
    parent_working_dir = run_dir.path.parent.parent
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="lingtai",
        parent_working_dir=str(parent_working_dir), run_dir=str(run_dir.path),
        task="detached file execution", tools=["file"], max_turns=1, timeout_s=30,
        group_id=None,
        llm={"provider": "fake", "model": "fake", "api_key": None,
             "base_url": None, "context_window": None, "provider_defaults": None},
    )

    host = DetachedDaemonExecutionHost(run_dir, manifest, Event(), Event())
    schemas, dispatch = host._build_lingtai_surface()
    assert {schema.name for schema in schemas}.issuperset(
        {"read", "write", "edit", "glob", "grep"}
    )

    write_result = dispatch["write"]({
        "file_path": "detached-file-probe.txt",
        "content": "detached file handler works\n",
    })
    assert write_result["status"] == "ok"
    target = parent_working_dir / "detached-file-probe.txt"
    assert target.read_text(encoding="utf-8") == "detached file handler works\n"

    read_result = dispatch["read"]({"file_path": "detached-file-probe.txt"})
    assert read_result["content"] == "1\tdetached file handler works\n"


def test_detached_port_observation_publishes_identity_before_watchdog_and_cancel_race(tmp_path):
    '''Port observation durably registers identity and cancels exact child only.'''
    from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
    from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
    from lingtai.tools.daemon.process_port import DaemonProcessCommand
    from threading import Event

    run_dir = _make_run_dir(tmp_path, task="identity observation")
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="codex",
        parent_working_dir=str(run_dir.path.parent.parent), run_dir=str(run_dir.path),
        task="identity observation", tools=[], max_turns=1, timeout_s=30,
        group_id=None,
        llm={"provider": "fake", "model": "fake", "api_key": None,
             "base_url": None, "context_window": None, "provider_defaults": None},
    )
    cancel_event = Event()
    port = PosixDaemonProcessPort(start_new_session=False)
    host = DetachedDaemonExecutionHost(
        run_dir, manifest, cancel_event, Event(), process_port=port,
    )
    handle = port.spawn(
        DaemonProcessCommand((sys.executable, "-c", "import time; time.sleep(1)"),
                             run_dir.path.parent.parent),
        group_id=None,
    )
    state = _disk_state(run_dir)
    cli_pid = state["cli_pid"]
    assert state["child_pid"] == cli_pid
    assert state["child_pgid"] == state["cli_pgid"]
    assert state["child_start_identity"]
    assert state["child_history"][-1]["pid"] == cli_pid
    assert state["child_pgid"] == os.getpgid(cli_pid)
    assert state["child_pgid"] == os.getpgrp()
    assert port.wait(handle, timeout=3).returncode == 0
    port.release(handle)

    cancel_event.set()
    raced = port.spawn(
        DaemonProcessCommand((sys.executable, "-c", "import time; time.sleep(5)"),
                             run_dir.path.parent.parent),
        group_id=None,
    )
    receipt = port.wait(raced, timeout=3)
    assert receipt.returncode == -15
    assert os.getpid() > 0
    assert port.release(raced)


def _run_isolated_port_probe(body: str) -> dict:
    '''Run a signal probe in its own session, never the pytest process group.'''
    repo_src = str(Path(__file__).resolve().parent.parent / "src")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [repo_src] + [item for item in env.get("PYTHONPATH", "").split(os.pathsep) if item]
    )
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        env=env, start_new_session=True, capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)



def _run_legacy_direct_popen_cleanup_probe(cleanup: str) -> dict:
    '''Exercise one retained direct-Popen cleanup path in an isolated host.'''
    assert cleanup in {"group", "reclaim-all"}
    parent_pid = os.getpid()
    return _run_isolated_port_probe(f'''\
        import json, os, signal, subprocess, sys, tempfile
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from threading import Event
        from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
        from lingtai.tools.daemon.run_dir import DaemonRunDir

        execution_host_pid = os.getpid()
        parent_pid = {parent_pid}
        parent = Path(tempfile.mkdtemp(prefix="legacy-popen-host-"))
        run_dir = DaemonRunDir(
            parent_working_dir=parent,
            handle="legacy-popen",
            run_id="legacy-popen",
            task="legacy direct Popen signal regression",
            tools=[],
            model="fake-model",
            max_turns=1,
            timeout_s=30.0,
            parent_addr="legacy-parent",
            parent_pid=parent_pid,
            system_prompt="legacy direct Popen signal regression",
            backend="codex",
        )
        manifest = {{
            "parent_working_dir": str(parent),
            "task": "legacy direct Popen signal regression",
            "tools": [],
            "max_turns": 1,
            "timeout_s": 30.0,
            "backend": "codex",
            "llm": {{"model": "fake-model"}},
        }}
        host = DetachedDaemonExecutionHost(run_dir, manifest, Event(), Event())
        host._max_emanations = 1
        host._ask_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="legacy-popen-regression"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # The probe itself has a fresh session from _run_isolated_port_probe;
            # this is the retained direct-Popen inherited-group behavior.
            start_new_session=False,
        )
        child_pid = child.pid
        child_pgid = os.getpgid(child_pid)
        host_pgid = os.getpgrp()
        host._register_cli_proc(child, group_id="legacy-group")
        scope = child._lingtai_termination_scope.value

        if {cleanup!r} == "group":
            host._kill_cli_group("legacy-group", reason="timeout")
            report = {{"path": "group", "status": "group-killed"}}
        else:
            report = host._handle_reclaim()

        waited_returncode = child.wait(timeout=5)
        child_alive_after = True
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_alive_after = False
        except PermissionError:
            child_alive_after = True
        host_alive = True
        try:
            os.kill(execution_host_pid, 0)
        except OSError:
            host_alive = False
        parent_alive = True
        try:
            os.kill(parent_pid, 0)
        except OSError:
            parent_alive = False
        replacement_pool = host._ask_pool
        if replacement_pool is not None:
            replacement_pool.shutdown(wait=False, cancel_futures=True)
        print(json.dumps({{
            "cleanup": {cleanup!r},
            "execution_host_pid": execution_host_pid,
            "parent_pid": parent_pid,
            "child_pid": child_pid,
            "child_pgid": child_pgid,
            "host_pgid": host_pgid,
            "termination_scope": scope,
            "returncode": child.returncode,
            "waited_returncode": waited_returncode,
            "expected_signal_returncode": -signal.SIGTERM,
            "child_alive_after": child_alive_after,
            "reaped": waited_returncode == child.returncode and not child_alive_after,
            "host_alive": host_alive,
            "parent_alive": parent_alive,
            "tracked_after": len(host._cli_procs),
            "report": report,
        }}), flush=True)
    ''')


@pytest.mark.skipif(os.name != "posix", reason="legacy process-group regression requires POSIX signals")
def test_legacy_direct_popen_group_cleanup_signals_exact_child_and_reaps():
    '''The retained detached direct-Popen group path sends real SIGTERM.'''
    result = _run_legacy_direct_popen_cleanup_probe("group")
    assert result["termination_scope"] == "inherited_supervisor_group"
    assert result["child_pgid"] == result["host_pgid"]
    assert result["returncode"] == result["expected_signal_returncode"] == -signal.SIGTERM
    assert result["waited_returncode"] == -signal.SIGTERM
    assert result["reaped"] is True
    assert result["child_alive_after"] is False
    assert result["tracked_after"] == 0
    assert result["host_alive"] is True
    assert result["parent_alive"] is True
    assert result["report"]["path"] == "group"


@pytest.mark.skipif(os.name != "posix", reason="legacy process-group regression requires POSIX signals")
def test_legacy_direct_popen_reclaim_all_signals_exact_child_and_reaps():
    '''Reclaim-all drains the retained direct-Popen registry and SIGTERMs it.'''
    result = _run_legacy_direct_popen_cleanup_probe("reclaim-all")
    assert result["termination_scope"] == "inherited_supervisor_group"
    assert result["child_pgid"] == result["host_pgid"]
    assert result["returncode"] == result["expected_signal_returncode"] == -signal.SIGTERM
    assert result["waited_returncode"] == -signal.SIGTERM
    assert result["reaped"] is True
    assert result["child_alive_after"] is False
    assert result["tracked_after"] == 0
    assert result["host_alive"] is True
    assert result["parent_alive"] is True
    assert result["report"]["status"] == "reclaimed"


@pytest.mark.parametrize("reason", ["timeout", "error"])
def test_detached_headless_termination_survives_execution_host(reason):
    result = _run_isolated_port_probe(f'''\
        import json, os, signal, sys, time
        from pathlib import Path
        from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
        from lingtai.tools.daemon.process_port import DaemonProcessCommand
        host_pid = os.getpid()
        observed = {{}}
        port = PosixDaemonProcessPort(
            term_timeout=0.1, kill_timeout=0.2, start_new_session=False,
            observation_callback=lambda receipt: observed.update(pid=receipt.pid),
        )
        command = DaemonProcessCommand((sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"), Path.cwd())
        handle = port.spawn(command)
        child_pid = observed["pid"]
        receipt = port.terminate(handle, reason={reason!r})
        host_alive = True
        try:
            os.kill(host_pid, 0)
        except OSError:
            host_alive = False
        released = port.release(handle)
        print(json.dumps({{"host_alive": host_alive, "receipt": receipt.returncode, "released": released, "child_pid": child_pid}}), flush=True)
    ''')
    assert result["host_alive"] is True
    assert result["receipt"] in {-15, -9}
    assert result["released"] is True



def _write_detached_fake_interactive_claude(bin_dir: Path) -> Path:
    # Create a local Claude-shaped PTY executable for detached production tests.
    fake = bin_dir / "claude"
    fake.write_text(textwrap.dedent(r'''
        #!/usr/bin/env python3
        from __future__ import annotations
        import json
        import os
        from pathlib import Path
        import signal
        import subprocess
        import sys
        import time

        args = sys.argv[1:]
        settings = None
        resume_session = None
        i = 0
        while i < len(args):
            if args[i] == "--settings":
                settings = json.loads(args[i + 1])
                i += 2
            elif args[i] == "--resume":
                resume_session = args[i + 1]
                i += 2
            else:
                i += 1
        if settings is None:
            raise SystemExit("missing --settings")

        def hook_command(event):
            for group in settings["hooks"][event]:
                for hook in group["hooks"]:
                    return hook["command"]
            raise SystemExit(f"missing hook {event}")

        session_id = resume_session or "detached-production-session"
        answer = (
            "detached production resume answer"
            if resume_session
            else "detached production initial answer"
        )
        transcript = Path.cwd() / "detached-fake-claude-transcript.jsonl"
        signal_record = Path(os.environ["LINGTAI_TEST_FAKE_CLAUDE_SIGNAL_RECORD"])

        # Exercise the bridge's real terminal-probe path; production Claude/Ink
        # emits these probes before SessionStart.
        sys.stdout.buffer.write(b"\x1b[c\x1b[>c\x1b[6n\x1b[>q\x1b[18t")
        sys.stdout.buffer.flush()
        subprocess.run(
            hook_command("SessionStart"),
            input=json.dumps({"session_id": session_id}),
            text=True,
            shell=True,
            check=True,
        )

        # The bridge sends a bracketed paste followed by CR through the PTY.
        # Consume it as a real interactive client would, without relying on a
        # direct adapter call from the test process.
        got = bytearray()
        deadline = time.time() + 5
        while time.time() < deadline:
            ch = sys.stdin.buffer.read(1)
            if not ch:
                time.sleep(0.01)
                continue
            got += ch
            if ch in (b"\r", b"\n"):
                break
        if not got:
            raise SystemExit("prompt not received")

        with transcript.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "type": "custom-title",
                "customTitle": "detached-production",
                "sessionId": session_id,
            }) + "\n")
            stream.write(json.dumps({
                "type": "assistant",
                "session_id": session_id,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                },
            }) + "\n")

        subprocess.run(
            hook_command("Stop"),
            input=json.dumps({
                "session_id": session_id,
                "transcript_path": str(transcript),
                "last_assistant_message": answer,
            }),
            text=True,
            shell=True,
            check=True,
        )

        if resume_session:
            # Keep the interactive child alive after Stop.  The production
            # bridge must invoke the injected inherited Posix Port's exact-PID
            # terminate path, survive that child termination, parse the
            # transcript, and publish followup_status=done.  A group signal
            # would also kill the execution host before it could publish done.
            def record_exact_child_signal(signum, frame):
                signal_record.write_text(json.dumps({
                    "pid": os.getpid(),
                    "pgid": os.getpgrp(),
                    "parent_pid": os.getppid(),
                    "signal": signum,
                }), encoding="utf-8")
                raise SystemExit(0)
            signal.signal(signal.SIGTERM, record_exact_child_signal)
            time.sleep(30)
    ''').lstrip(), encoding="utf-8")
    fake.chmod(0o755)
    return fake


def _wait_exact_process_gone(pid: int, identity: str | None):
    from lingtai.adapters.posix.process_identity import process_identity_matches

    assert isinstance(pid, int) and pid > 0
    assert isinstance(identity, str) and identity
    return _poll_until(
        lambda: not process_identity_matches(pid, identity),
        timeout=15.0,
        interval=0.05,
    )


def _assert_detached_interactive_identity(state: dict) -> None:
    # Check the durable execution/child identity chain from outside the host.
    assert state["owner"] == "supervisor"
    assert isinstance(state["supervisor_pid"], int)
    assert state["supervisor_start_identity"]
    assert state["execution_registration"] == "registered"
    assert isinstance(state["execution_pid"], int)
    assert state["execution_start_identity"]
    assert state["child_pid"] == state["cli_pid"]
    assert state["child_pgid"] == state["cli_pgid"] == state["execution_pgid"]
    assert state["child_start_identity"]
    assert state["child_termination_scope"] == "inherited_supervisor_group"
    assert state["child_history"][-1]["pid"] == state["child_pid"]


def _dispatch_detached_interactive(tmp_path: Path, monkeypatch):
    from tests._daemon_helpers import make_daemon_agent

    bin_dir = tmp_path / "fake-claude-bin"
    bin_dir.mkdir()
    _write_detached_fake_interactive_claude(bin_dir)
    signal_record = tmp_path / "fake-claude-signal.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("LINGTAI_CLAUDE_MANAGED_ROOT", str(tmp_path / "managed-claude"))
    monkeypatch.setenv("LINGTAI_TEST_FAKE_CLAUDE_SIGNAL_RECORD", str(signal_record))

    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    dispatched = manager.handle({
        "action": "emanate",
        "backend": "claude",
        "tasks": [{"task": "detached production interactive task", "tools": []}],
        "timeout": 30,
    })
    assert dispatched["status"] == "dispatched"
    run_dir = manager._emanations[dispatched["ids"][0]]["run_dir"]
    return agent, manager, dispatched["ids"][0], run_dir, signal_record


def test_detached_interactive_initial_production_host_publishes_durable_result(tmp_path, monkeypatch):
    # Real detached initial host -> manager runner -> interactive bridge completes.
    _agent, _manager, em_id, run_dir, _signal_record = _dispatch_detached_interactive(
        tmp_path, monkeypatch,
    )
    state = _poll_until(
        lambda: (
            current
            if (current := _disk_state(run_dir)).get("state") == "done"
            else None
        ),
        timeout=30,
    )
    assert state["handle"] == em_id
    _poll_until(lambda: _disk_state(run_dir).get("terminal_notified") is True, timeout=10)
    assert run_dir.result_path.read_text(encoding="utf-8") == "detached production initial answer"
    assert state["result_preview"] == "detached production initial answer"
    assert state["claude_session_id"] == "detached-production-session"
    assert state["claude_interactive_prompt_sent"] is True
    _assert_detached_interactive_identity(state)

    # The backend child was waited/reaped by the real Port.  The execution
    # host survived its full production bridge/terminal publication path, so
    # its inherited host group was not signalled as a unit.
    _wait_exact_process_gone(state["child_pid"], state["child_start_identity"])
    _wait_exact_process_gone(state["execution_pid"], state["execution_start_identity"])
    assert not (tmp_path / "fake-claude-signal.json").exists()


def test_detached_interactive_resume_production_host_publishes_followup_done_after_exact_child_stop(
    tmp_path, monkeypatch,
):
    # Real detached resume host -> manager runner -> bridge survives exact child Stop.
    agent, manager, em_id, run_dir, signal_record = _dispatch_detached_interactive(
        tmp_path, monkeypatch,
    )
    _poll_until(lambda: _disk_state(run_dir).get("state") == "done", timeout=30)

    # A fresh production manager reads durable state, claims one generation,
    # and launches the detached resume owner; no parent future or adapter is
    # substituted in this path.
    from lingtai.tools.daemon import DaemonManager
    fresh_manager = DaemonManager(agent)
    ask = fresh_manager.handle({
        "action": "ask",
        "id": em_id,
        "message": "detached production follow-up message",
    })
    assert ask["status"] == "sent"
    generation = ask["generation"]

    state = _poll_until(
        lambda: (
            current
            if (
                (current := _disk_state(run_dir)).get("followup_generation") == generation
                and current.get("followup_status") == "done"
                and current.get("resume_pid") is None
                and isinstance(current.get("resume_claim"), dict)
                and current["resume_claim"].get("status") == "released"
            )
            else None
        ),
        timeout=30,
    )
    assert state["followup_result_preview"] == "detached production resume answer"
    assert state["resume_state"] == "done"
    assert state["resume_pid"] is None
    claim = state["resume_claim"]
    assert claim["generation"] == generation
    assert claim["status"] == "released"
    assert claim["result_status"] == "done"
    assert state["claude_session_id"] == "detached-production-session"
    _assert_detached_interactive_identity(state)

    # The fake resume child records the exact PID/PGID that received SIGTERM.
    # Its parent execution host then remained alive long enough to parse Stop,
    # record followup_status=done, and release the generation.  This is the
    # external evidence that Port termination did not signal the host group.
    signal_seen = _poll_until(
        lambda: json.loads(signal_record.read_text(encoding="utf-8"))
        if signal_record.exists() else None,
        timeout=10,
    )
    assert signal_seen["pid"] == state["child_pid"]
    assert signal_seen["pgid"] == state["child_pgid"] == state["execution_pgid"]
    assert signal_seen["parent_pid"] == state["execution_pid"]
    assert signal_seen["signal"] == signal.SIGTERM
    _wait_exact_process_gone(state["child_pid"], state["child_start_identity"])
    _wait_exact_process_gone(state["execution_pid"], state["execution_start_identity"])

def test_headless_observation_failure_reaps_child_and_clears_registry():
    result = _run_isolated_port_probe(r'''
        import errno, json, os, signal, sys, time
        from pathlib import Path
        from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
        from lingtai.tools.daemon.process_port import DaemonProcessCommand
        observed = {}
        def callback(receipt):
            observed["pid"] = receipt.pid
            raise RuntimeError("state publication failed")
        port = PosixDaemonProcessPort(term_timeout=0.1, kill_timeout=0.2, start_new_session=False, observation_callback=callback)
        command = DaemonProcessCommand((sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"), Path.cwd())
        try:
            port.spawn(command)
        except RuntimeError:
            pass
        else:
            raise AssertionError("callback failure must propagate")
        pid = observed["pid"]
        try:
            os.kill(pid, 0)
        except OSError as exc:
            dead = exc.errno == errno.ESRCH
        else:
            dead = False
        print(json.dumps({"dead": dead, "registry_empty": port.terminate_all() == 0}), flush=True)
    ''')
    assert result == {"dead": True, "registry_empty": True}


def test_pty_observation_failure_reaps_child_closes_master_and_clears_registry():
    result = _run_isolated_port_probe(r'''
        import errno, json, os, pty, signal, sys, time
        from pathlib import Path
        from lingtai.adapters.posix.interactive_terminal import PosixInteractiveTerminalAdapter
        from lingtai.tools.daemon.interactive_terminal import InteractiveTerminalCommand
        observed = {}
        master = {}
        real_openpty = pty.openpty
        def capture_openpty():
            fds = real_openpty()
            master["fd"] = fds[0]
            return fds
        pty.openpty = capture_openpty
        def callback(receipt):
            observed["pid"] = receipt.pid
            raise RuntimeError("state publication failed")
        adapter = PosixInteractiveTerminalAdapter(term_timeout=0.1, kill_timeout=0.2, start_new_session=False, observation_callback=callback)
        command = InteractiveTerminalCommand(argv=(sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"), cwd=Path.cwd(), rows=24, columns=80)
        try:
            adapter.spawn(command)
        except RuntimeError:
            pass
        else:
            raise AssertionError("callback failure must propagate")
        pid = observed["pid"]
        try:
            os.kill(pid, 0)
        except OSError as exc:
            dead = exc.errno == errno.ESRCH
        else:
            dead = False
        try:
            os.fstat(master["fd"])
        except OSError as exc:
            fd_closed = exc.errno == errno.EBADF
        else:
            fd_closed = False
        print(json.dumps({"dead": dead, "fd_closed": fd_closed, "registry_empty": adapter.terminate_all() == 0}), flush=True)
    ''')
    assert result == {"dead": True, "fd_closed": True, "registry_empty": True}
