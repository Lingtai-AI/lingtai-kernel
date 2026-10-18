"""Workflow contract fixtures: mocked workers, NEVER live provider dispatch."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lingtai.adapters.workflow_cli import DaemonCliBatches
from lingtai.tools.workflow import DECLARATION, _family, _handle
from lingtai.tools.workflow.core import WorkflowError, WorkflowManager, load_plan


class Batches:
    """Deterministic mock of the external batch port, not a real daemon worker."""
    def __init__(self):
        self.calls = []
        self.states = {}
        self.cancelled = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.fail_dispatch = False
        self.fail_cancel = False

    def dispatch(self, root, payload, count):
        data = json.loads(payload.read_text())
        self.calls.append((root, data))
        self.entered.set()
        if self.block:
            assert self.release.wait(3)
        if self.fail_dispatch:
            raise WorkflowError("ambiguous acceptance")
        ids = [f"em-{len(self.calls)}-{i}" for i in range(count)]
        for worker_id in ids:
            self.states[worker_id] = "running"
        return dict(status="dispatched", ids=ids, count=count)

    def inspect(self, root, worker_id):
        return dict(id=worker_id, run_id=worker_id + "-actual", state=self.states[worker_id],
                    result_path=str(root / worker_id / "result.txt"), error=None)

    def cancel(self, root):
        self.cancelled.append(root)
        if self.fail_cancel:
            raise WorkflowError("reclaim uncertain")
        return dict(status="reclaimed")


def plan_file(tmp_path, *, auto=False, pause=False, tasks=2):
    path = tmp_path / "plan.json"
    plan = dict(auto_continue=auto, stages=[
        dict(tasks=[dict(task=f"authorized {i}", preset="fixture") for i in range(tasks)],
             pause_after=pause), dict(tasks=[dict(task="next", preset="fixture")])])
    path.write_text(json.dumps(plan))
    return path, plan


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("bounded fixture wait expired")


@pytest.fixture
def runtime(tmp_path):
    batches, notifications, shutdown = Batches(), MagicMock(), threading.Event()
    notifications.publish_system.return_value = True
    manager = WorkflowManager(tmp_path, batches, notifications, shutdown, poll_seconds=.005)
    yield manager, batches, notifications, shutdown
    for run_id, run in list(manager._runs.items()):
        run.stop.set()
        if run.thread:
            run.thread.join(3)
            assert not run.thread.is_alive()


def test_ordered_parallel_auto_without_main_calls_and_source_independence(runtime, tmp_path):
    manager, batches, notifications, _ = runtime
    path, _ = plan_file(tmp_path, auto=True)
    run_id = manager.run(str(path))["run_id"]
    path.unlink()
    eventually(lambda: len(batches.states) == 2)
    assert len(batches.calls) == 1 and len(batches.calls[0][1]["tasks"]) == 2
    # Dispatch and one finished worker are not stage completion.
    batches.states["em-1-0"] = "done"
    time.sleep(.025)
    assert len(batches.calls) == 1
    assert not notifications.publish_system.called
    batches.states["em-1-1"] = "done"
    eventually(lambda: len(batches.calls) == 2)
    assert not notifications.publish_system.called  # no automatic boundary wake
    batches.states["em-2-0"] = "done"
    eventually(lambda: manager.check(run_id)["status"] == "done")
    state = manager.check(run_id)
    assert state["stage_index"] == 2
    assert state["history"][0]["results"]["em-1-0"]["run_id"] == "em-1-0-actual"
    assert Path(state["submissions"][0]).is_file()
    assert notifications.publish_system.call_count == 1


@pytest.mark.parametrize("auto,pause", [(False, False), (True, True)])
def test_paused_continuation_preserves_prefix_refs_and_snapshots(runtime, tmp_path, auto, pause):
    manager, batches, _, _ = runtime
    path, plan = plan_file(tmp_path, auto=auto, pause=pause, tasks=1)
    run_id = manager.run(str(path))["run_id"]
    eventually(lambda: bool(batches.states))
    batches.states["em-1-0"] = "done"
    eventually(lambda: manager.check(run_id)["status"] == "paused")
    before = manager.check(run_id)
    plan["stages"][0]["tasks"][0]["task"] = "replay changed external effect"
    path.write_text(json.dumps(plan))
    with pytest.raises(WorkflowError, match="immutable"):
        manager.run(str(path), run_id)
    plan["stages"][0] = before["plan"]["stages"][0]
    plan["stages"][1]["tasks"][0]["task"] = "edited remaining task"
    path.write_text(json.dumps(plan))
    manager.run(str(path), run_id)
    eventually(lambda: len(batches.calls) == 2)
    after = manager.check(run_id)
    assert after["history"][0] == before["history"][0]
    assert len(after["submissions"]) == 2
    assert batches.calls[1][1]["tasks"][0]["task"] == "edited remaining task"


@pytest.mark.parametrize("failure", ["failed", "timeout", "cancelled"])
def test_daemon_failure_never_advances(runtime, tmp_path, failure):
    manager, batches, notifications, _ = runtime
    path, _ = plan_file(tmp_path, auto=True, tasks=1)
    run_id = manager.run(str(path))["run_id"]
    eventually(lambda: bool(batches.states))
    batches.states["em-1-0"] = failure
    eventually(lambda: manager.check(run_id)["status"] == "failed")
    assert len(batches.calls) == 1 and notifications.publish_system.called
    with pytest.raises(WorkflowError, match="paused"):
        manager.run(str(path), run_id)


def test_ambiguous_dispatch_no_replay_and_reconstruction(runtime, tmp_path):
    manager, batches, notifications, shutdown = runtime
    batches.fail_dispatch = True
    path, _ = plan_file(tmp_path, auto=True)
    run_id = manager.run(str(path))["run_id"]
    eventually(lambda: manager.check(run_id)["status"] == "uncertain")
    assert len(batches.calls) == 1
    assert manager.check(run_id)["history"][0]["ids"] == []
    with pytest.raises(WorkflowError):
        manager.run(str(path), run_id)
    other = WorkflowManager(tmp_path, batches, notifications, shutdown)
    assert other.check(run_id)["status"] == "uncertain"
    assert len(batches.calls) == 1


def test_reconstruction_running_is_uncertain_not_replayed(runtime, tmp_path):
    manager, batches, notifications, shutdown = runtime
    path, _ = plan_file(tmp_path)
    run_id = manager.run(str(path))["run_id"]
    eventually(lambda: bool(batches.states))
    manager._runs[run_id].stop.set()
    manager._runs[run_id].thread.join(3)
    other = WorkflowManager(tmp_path, batches, notifications, shutdown)
    assert other.check(run_id)["status"] == "uncertain"
    assert len(batches.calls) == 1


def test_stop_scope_and_dispatch_race(runtime, tmp_path):
    manager, batches, _, _ = runtime
    batches.block = True
    path, _ = plan_file(tmp_path, auto=True, tasks=1)
    run_id = manager.run(str(path))["run_id"]
    assert batches.entered.wait(2)
    output = []
    stopper = threading.Thread(target=lambda: output.append(manager.stop(run_id)))
    stopper.start()
    eventually(lambda: manager._runs[run_id].stop.is_set())
    batches.release.set()
    stopper.join(3)
    assert not stopper.is_alive()
    assert output[0]["status"] == "stopped"
    assert batches.cancelled == [tmp_path / "workflow" / run_id / "daemon"]
    assert len(batches.calls) == 1
    # Another workflow owns a different root; stop never reaches it or main.
    batches.block = False
    other_id = manager.run(str(path))["run_id"]
    eventually(lambda: len(batches.calls) == 2)
    assert batches.calls[1][0] != batches.cancelled[0]
    batches.fail_cancel = True
    assert manager.stop(other_id)["status"] == "stop_failed"


def test_shutdown_no_next_stage_and_notification_failure_visible(runtime, tmp_path):
    manager, batches, notifications, shutdown = runtime
    path, _ = plan_file(tmp_path, auto=True, tasks=1)
    run_id = manager.run(str(path))["run_id"]
    eventually(lambda: bool(batches.states))
    notifications.publish_system.return_value = False
    shutdown.set()
    eventually(lambda: manager.check(run_id)["status"] == "uncertain")
    assert manager.check(run_id)["notification_error"] == "publication not confirmed"
    assert len(batches.calls) == 1


@pytest.mark.parametrize("payload", [[], {}, {"stages": [], "shell": "x"},
    {"stages": [{"tasks": [{}], "command": "x"}]},
    {"stages": [{"tasks": [{}]}], "auto_continue": 1},
    {"stages": [{"tasks": [{}], "pause_after": "yes"}]},
    {"stages": [{"tasks": [{}], "timeout": float("nan")}]},
    {"stages": [{"tasks": [{}], "max_turns": True}]}])
def test_strict_file_input(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(WorkflowError):
        load_plan(path)


def test_strict_tool_envelope_and_declaration():
    family = _family()
    for args in [dict(action="manual", input={"file": "x"}),
                 dict(action="check", input={"run_id": "x", "file": "x"}),
                 dict(action="stop", input={"run_id": "x"}, extra=True),
                 dict(action="run", input={}, file="relocated-not-allowed"),
                 dict(action="manual", input={}, summarize="no")]:
        assert _handle(family, args)["status"] in {"error", "failed"}
    from lingtai.kernel.tool_result_summary import _LTP_V2_MIGRATED_FAMILIES
    assert "workflow" in _LTP_V2_MIGRATED_FAMILIES
    assert DECLARATION.public_actions == ("run", "check", "stop", "manual")
    assert set(family.build_schema()["properties"]) == {"action", "input", "reasoning", "summarize"}


@pytest.mark.parametrize("result,code", [({}, 0), ({"status": "dispatched", "ids": ["same", "same"], "count": 2}, 0),
    ({"status": "dispatched", "ids": ["only"], "count": 2}, 0), ({"status": "error"}, 1), ("not JSON", 0)])
def test_cli_ambiguous_acceptance_no_second_call(monkeypatch, tmp_path, result, code):
    calls = []
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [{"task": "one"}, {"task": "two"}]}))
    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, result if isinstance(result, str) else json.dumps(result), "")
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(WorkflowError):
        DaemonCliBatches(tmp_path).dispatch(tmp_path / "isolated", tmp_path / "tasks.json", 2)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:5] == [sys.executable, "-m", "lingtai", "daemon", "emanate"]
    assert kwargs["cwd"] == tmp_path and "shell" not in kwargs
    assert argv[-2:] == ["--state-root", str(tmp_path / "isolated")]


def test_cli_check_nonterminal_and_reclaim_validation(monkeypatch, tmp_path):
    replies = iter([dict(id="em-1", run_id="actual", state="running"),
                    dict(id="em-1", run_id="actual", state="mystery"), dict(status="not reclaimed")])
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, json.dumps(next(replies)), ""))
    adapter = DaemonCliBatches(tmp_path)
    assert adapter.inspect(tmp_path, "em-1")["state"] == "running"
    with pytest.raises(WorkflowError):
        adapter.inspect(tmp_path, "em-1")
    with pytest.raises(WorkflowError):
        adapter.cancel(tmp_path)


def test_real_agent_opt_in_prompt_manual_and_refresh_slot(tmp_path, monkeypatch):
    """Real Agent/prompt assembly; mocked LLM service, no start/provider dispatch."""
    from lingtai.agent import Agent
    from lingtai.tools.registry import BUILTIN_TOOLS, CORE_DEFAULTS
    from tests.test_agent import make_mock_service
    from tests._workdir_lease_helpers import make_test_lease
    from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
    from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
    from tests._notification_store_helpers import notification_store_for
    from tests._agent_presence_helpers import make_test_presence_store

    monkeypatch.setenv("HOME", str(tmp_path))
    assert "workflow" in BUILTIN_TOOLS and "workflow" not in CORE_DEFAULTS
    assert "daemon" in BUILTIN_TOOLS and "daemon" in CORE_DEFAULTS
    root = tmp_path / "agent"
    agent = Agent(service=make_mock_service(), agent_name="workflow-fixture", working_dir=root,
                  capabilities={"workflow": {}, "daemon": {}},
                  workdir_lease=make_test_lease(), agent_presence=make_test_presence_store(),
                  snapshot_port=make_test_snapshot_port(), lifecycle_clock=make_test_lifecycle_clock(),
                  source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(root))
    assert agent.get_capability("workflow") is agent._workflow_manager
    assert "workflow" in agent._tool_handlers and "daemon" in agent._tool_handlers
    names = {schema.name for schema in agent._build_tool_schemas()}
    assert {"workflow", "daemon"} <= names
    prompt = agent._build_system_prompt()
    assert isinstance(prompt, str) and len(prompt) > 100
    manual = root / ".library/intrinsic/capabilities/workflow/SKILL.md"
    assert manual.is_file()
    assert _family(type("Workdir", (), {"path": root})(), agent._workflow_manager).handle(
        dict(action="manual", input={}, summarize=False))["structuredContent"]["manual_path"] == str(manual)
    # Exercise the exact binder's refresh path without remounting a reserved name.
    from lingtai.adapters.tool_plugin_host import agent_host_ports
    from lingtai.kernel.tool_plugin import ToolPluginHost
    manager = agent._workflow_manager
    DECLARATION.bind(ToolPluginHost.grant(DECLARATION, agent_host_ports(agent, "workflow")))
    assert manager is agent._workflow_manager
    assert agent._build_system_prompt()


def test_real_cli_read_only_candidate_namespace(tmp_path, monkeypatch):
    """Actual candidate CLI subprocess, read-only unknown-id check; NO workers."""
    candidate_src = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.setenv("PYTHONPATH", str(candidate_src))
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(WorkflowError, match="refused or outcome uncertain"):
        DaemonCliBatches(tmp_path).inspect(tmp_path, "em-does-not-exist")
    assert not (tmp_path / "daemon" / "runs").exists()


def test_cli_timeout_is_uncertain_and_preserves_no_replay(monkeypatch, tmp_path):
    (tmp_path / "batch.json").write_text(json.dumps({"tasks": [{"task": "one"}]}))
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(WorkflowError, match="uncertain.*do not replay"):
        DaemonCliBatches(tmp_path).dispatch(tmp_path, tmp_path / "batch.json", 1)


@pytest.mark.parametrize("daemon", [False, True])
def test_workflow_summary_control_reaches_actual_executor(daemon):
    """Real executor paths with a mocked dispatch/summarizer; no provider."""
    from tests.test_tool_executor import test_apriori_default_omission_and_false_reach_actual_executor
    test_apriori_default_omission_and_false_reach_actual_executor("workflow", daemon)


def test_stop_intent_during_persistence_prevents_dispatch(runtime, tmp_path, monkeypatch):
    manager, batches, _, _ = runtime
    original_save = manager._save
    intent_seen = threading.Event()
    def save_with_stop(run):
        original_save(run)
        if run.state["status"] == "dispatching":
            run.stop.set()
            intent_seen.set()
    monkeypatch.setattr(manager, "_save", save_with_stop)
    path, _ = plan_file(tmp_path, auto=True)
    run_id = manager.run(str(path))["run_id"]
    assert intent_seen.wait(2)
    assert manager.stop(run_id)["status"] == "stopped"
    assert batches.calls == []


def test_ambiguous_structured_acceptance_receipt_is_preserved(runtime, tmp_path, monkeypatch):
    manager, batches, _, _ = runtime
    receipt = dict(status="dispatched", count=2, ids=["one-known-worker"])
    def ambiguous(*args):
        raise WorkflowError("ambiguous acceptance; no replay", receipt=receipt)
    monkeypatch.setattr(batches, "dispatch", ambiguous)
    path, _ = plan_file(tmp_path)
    run_id = manager.run(str(path))["run_id"]
    eventually(lambda: manager.check(run_id)["status"] == "uncertain")
    assert manager.check(run_id)["error_receipt"] == receipt
    disk = json.loads((tmp_path / "workflow" / run_id / "state.json").read_text())
    assert disk["error_receipt"] == receipt


@pytest.mark.parametrize("during_admission", [False, True])
def test_paused_continuation_stop_intent_is_monotonic(runtime, tmp_path, monkeypatch, during_admission):
    """Reviewer schedule: stop publishes intent, then yields before lock entry."""
    manager, batches, _, _ = runtime
    path, _ = plan_file(tmp_path, tasks=1)
    run_id = manager.run(str(path))["run_id"]
    eventually(lambda: bool(batches.states))
    batches.states["em-1-0"] = "done"
    eventually(lambda: manager.check(run_id)["status"] == "paused")
    run = manager._runs[run_id]
    run.thread.join(2)
    assert not run.thread.is_alive()
    before = manager.check(run_id)
    intent_set, allow_stop_lock = threading.Event(), threading.Event()

    class ScheduledEvent(threading.Event):
        def set(self):
            super().set()
            intent_set.set()
            assert allow_stop_lock.wait(3)

    run.stop = ScheduledEvent()
    output = []
    stopper = threading.Thread(target=lambda: output.append(manager.stop(run_id)))
    if during_admission:
        original_save = manager._save
        def save_with_concurrent_stop(current):
            if current.state["status"] == "running" and not intent_set.is_set():
                stopper.start()
                assert intent_set.wait(2)
            original_save(current)
        monkeypatch.setattr(manager, "_save", save_with_concurrent_stop)
    else:
        stopper.start()
        assert intent_set.wait(2)
    try:
        if during_admission:
            manager.run(str(path), run_id)
            # The new monitor has finished its pre-dispatch stop guard.
            run.thread.join(2)
            assert not run.thread.is_alive()
        else:
            with pytest.raises(WorkflowError, match="stop intent"):
                manager.run(str(path), run_id)
            assert manager.check(run_id) == before
        assert run.stop.is_set()
        assert len(batches.calls) == 1
    finally:
        allow_stop_lock.set()
        stopper.join(3)
    assert not stopper.is_alive() and output[0]["status"] == "stopped"
    assert len(batches.calls) == 1


def daemon_input_validator(root):
    """Real existing validation/snapshot methods, no runtime/worker/provider."""
    from types import SimpleNamespace
    from lingtai.tools.daemon import DaemonManager
    manager = object.__new__(DaemonManager)
    manager._workdir = SimpleNamespace(path=root)
    return manager


@pytest.mark.parametrize("absolute", [False, True])
def test_cli_task_files_host_staging_actual_daemon_snapshot(tmp_path, monkeypatch, absolute):
    host = tmp_path / "host"
    host.mkdir()
    root = host / "workflow" / "wf-fixture" / "daemon"
    source = host / "input.txt"
    content = b"authorized input\r\nUTF-8: \xe4\xb8\xad\n"
    source.write_bytes(content)
    original_path = str(source) if absolute else "input.txt"
    batch = {"tasks": [{"task": "read", "preset": "unchanged.json", "skills": ["unchanged-skill"],
                        "task_files": [{"path": original_path, "label": "evidence", "role": "input"}]}]}
    payload = host / "stage-0.json"
    payload.write_text(json.dumps(batch))
    submitted = payload.read_bytes()
    adapter = DaemonCliBatches(host)
    calls = []
    def validate_only(private, *args):
        assert private == root and args[:2] == ("emanate", "--tasks")
        derived = Path(args[2])
        staged = json.loads(derived.read_text())
        # The subprocess would consume this payload, not the submitted snapshot.
        assert derived != payload and derived.is_relative_to(root)
        spec = staged["tasks"][0]
        assert spec["preset"] == "unchanged.json" and spec["skills"] == ["unchanged-skill"]
        validator = daemon_input_validator(root)
        rows = validator._snapshot_task_files(spec)
        assert rows[0]["label"] == "evidence" and rows[0]["role"] == "input"
        assert Path(rows[0]["resolved"]).is_relative_to(root)
        # Delete mutable host input BEFORE canonical materialization.
        source.unlink()
        manifest = validator._materialize_task_files("fixture", [rows])
        assert Path(rows[0]["snapshot"]).read_bytes() == content
        assert json.loads(Path(manifest).read_text())["files"][0] == rows
        mapping = json.loads(derived.with_suffix(".sources.json").read_text())
        assert mapping["source_payload"] == str(payload)
        assert mapping["files"][0]["path"] == original_path
        assert mapping["files"][0]["resolved"] == str(source.resolve())
        calls.append(derived)
        return {"status": "dispatched", "count": 1, "ids": ["mock-only"]}
    monkeypatch.setattr(adapter, "_call", validate_only)
    adapter.dispatch(root, payload, 1)
    assert payload.read_bytes() == submitted
    assert len(calls) == 1


@pytest.mark.parametrize("escape", ["absolute", "relative", "symlink"])
def test_cli_task_files_out_of_host_refused_before_cli(tmp_path, monkeypatch, escape):
    host = tmp_path / "host"
    host.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not authorized")
    if escape == "symlink":
        (host / "link.txt").symlink_to(outside)
        path = "link.txt"
    else:
        path = str(outside) if escape == "absolute" else "../outside.txt"
    payload = host / "stage.json"
    payload.write_text(json.dumps({"tasks": [{"task_files": [{"path": path}]}]}))
    adapter = DaemonCliBatches(host)
    call = MagicMock()
    monkeypatch.setattr(adapter, "_call", call)
    root = host / "private"
    with pytest.raises(WorkflowError, match="outside the host"):
        adapter.dispatch(root, payload, 1)
    assert not root.exists()
    call.assert_not_called()


@pytest.mark.parametrize("content,message", [(b"x" * 1_000_001, "1000000-byte"),
    (b"\xff", "UTF-8"), (b"text\x00", "NUL")])
def test_cli_task_files_staging_text_cap(tmp_path, monkeypatch, content, message):
    (tmp_path / "input").write_bytes(content)
    payload = tmp_path / "stage.json"
    payload.write_text(json.dumps({"tasks": [{"task_files": [{"path": "input"}]}]}))
    adapter = DaemonCliBatches(tmp_path)
    call = MagicMock()
    monkeypatch.setattr(adapter, "_call", call)
    with pytest.raises(WorkflowError, match=message):
        adapter.dispatch(tmp_path / "private", payload, 1)
    call.assert_not_called()


def test_cli_task_files_daemon_owns_count_and_annotations(tmp_path, monkeypatch):
    payload = tmp_path / "stage.json"
    root = tmp_path / "private"
    adapter = DaemonCliBatches(tmp_path)
    validator = daemon_input_validator(root)
    def validate_only(private, *args):
        batch = json.loads(Path(args[2]).read_text())
        validator._snapshot_task_files(batch["tasks"][0])
        raise AssertionError("canonical validator should refuse")
    monkeypatch.setattr(adapter, "_call", validate_only)
    # More than 50 files must reach daemon validation without reading/copying.
    payload.write_text(json.dumps({"tasks": [{"task_files": [{"path": "missing"}] * 51}]}))
    with pytest.raises(ValueError, match="50-file"):
        adapter.dispatch(root, payload, 1)
    assert not root.exists()
    # Annotation rules remain canonical, not reimplemented in the adapter.
    (tmp_path / "input").write_text("valid input")
    payload.write_text(json.dumps({"tasks": [{"task_files": [{"path": "input", "label": 42}]}]}))
    with pytest.raises(ValueError, match="label must be a string"):
        adapter.dispatch(root, payload, 1)


def test_cli_task_files_digest_store_does_not_overwrite_earlier_inputs(tmp_path):
    source = tmp_path / "input"
    payload = tmp_path / "stage.json"
    payload.write_text(json.dumps({"tasks": [{"task_files": [{"path": "input"}]}]}))
    adapter = DaemonCliBatches(tmp_path)
    root = tmp_path / "private"
    source.write_text("first")
    first = adapter._stage_task_files(root, payload)
    assert adapter._stage_task_files(root, payload) == first
    source.write_text("second")
    second = adapter._stage_task_files(root, payload)
    assert first != second
    for derived, expected in [(first, b"first"), (second, b"second")]:
        spec = json.loads(derived.read_text())["tasks"][0]
        rows = daemon_input_validator(root)._snapshot_task_files(spec)
        assert Path(rows[0]["resolved"]).read_bytes() == expected
