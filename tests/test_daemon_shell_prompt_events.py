"""Focused contract tests for detached daemon Shell prompt events."""
from __future__ import annotations

import json
import time

from lingtai.tools.bash import ShellManager, ShellPolicy
from lingtai.tools.bash._async_supervisor import load_state, write_initial_state
from lingtai.tools.daemon.shell_prompt_events import DaemonShellPromptEventAdapter
from tests._daemon_helpers import make_daemon_run_dir


def _job(index: int) -> str:
    return f"job-{index:032x}"


def _completion_payload(job_id: str, *, stdout_preview: str = "untrusted") -> dict:
    return {
        "data": {
            "job_id": job_id,
            "exit_status_known": True,
            "exit_code": 0,
            # The adapter must ignore this process-derived field completely.
            "stdout_preview": stdout_preview,
            "stderr_preview": "also-untrusted",
        }
    }


def test_daemon_shell_adapter_queues_bounded_deduped_output_free_events(tmp_path):
    run_dir = make_daemon_run_dir(parent_working_dir=tmp_path / "agent", tools=["shell"])
    adapter = DaemonShellPromptEventAdapter(run_dir)
    job_id = _job(1)
    ref_id = f"bash.completion:{job_id}"

    assert adapter.publish_channel(
        "bash", _completion_payload(job_id, stdout_preview="MALICIOUS-SHELL-OUTPUT"), ref_id=ref_id,
    ) is True
    # Shell retry after a crash before durable acknowledgement must receive a
    # truthful success for the already-durable stable reference, not a duplicate.
    assert adapter.publish_channel("bash", _completion_payload(job_id), ref_id=ref_id) is True
    state = run_dir.state_snapshot()
    queued = state["pending_shell_prompt_events"]
    assert len(queued) == 1
    assert queued[0] == {
        "kind": "shell_completion",
        "ref_id": ref_id,
        "job_id": job_id,
        "queued_at": queued[0]["queued_at"],
        "exit_status_known": True,
        "exit_code": 0,
    }
    durable_text = run_dir.daemon_json_path.read_text(encoding="utf-8") + run_dir.events_path.read_text(encoding="utf-8")
    assert "MALICIOUS-SHELL-OUTPUT" not in durable_text
    assert "stderr_preview" not in durable_text

    drained = run_dir.drain_shell_prompt_events()
    assert [event["ref_id"] for event in drained] == [ref_id]
    assert run_dir.drain_shell_prompt_events() == []
    assert adapter.publish_channel("bash", _completion_payload(job_id), ref_id=ref_id) is True
    assert run_dir.state_snapshot()["pending_shell_prompt_events"] == []

    # Pending capacity is finite. New refs fail rather than displacing an
    # undelivered event, leaving Shell's completion state retryable.
    for index in range(2, 2 + run_dir._MAX_PENDING_SHELL_PROMPT_EVENTS):
        other = _job(index)
        assert adapter.publish_channel(
            "bash", _completion_payload(other), ref_id=f"bash.completion:{other}"
        ) is True
    overflow = _job(99)
    assert adapter.publish_channel(
        "bash", _completion_payload(overflow), ref_id=f"bash.completion:{overflow}"
    ) is False
    assert len(run_dir.state_snapshot()["pending_shell_prompt_events"]) == run_dir._MAX_PENDING_SHELL_PROMPT_EVENTS
    assert not (tmp_path / "agent" / ".notification" / "system.json").exists()
    assert not (tmp_path / "agent" / ".notification" / "bash.json").exists()

    run_dir.mark_done("daemon is terminal")
    assert adapter.publish_system(
        source="bash.reminder",
        ref_id=f"bash.reminder:{_job(100)}",
        body="untrusted reminder body",
        skip_if_ref_id_exists=True,
    ) is False


def test_shell_completion_publication_remains_retryable_until_run_dir_acknowledges(tmp_path):
    """A full RunDir queue returns false, so Shell does not mark completion published."""
    parent = tmp_path / "agent"
    run_dir = make_daemon_run_dir(parent_working_dir=parent, tools=["shell"])
    adapter = DaemonShellPromptEventAdapter(run_dir)
    manager = ShellManager(
        ShellPolicy.yolo(), str(parent), notification_port=adapter, rehydrate=False,
    )
    for index in range(1, 1 + run_dir._MAX_PENDING_SHELL_PROMPT_EVENTS):
        job_id = _job(index)
        assert adapter.publish_channel(
            "bash", _completion_payload(job_id), ref_id=f"bash.completion:{job_id}"
        ) is True

    job_id = _job(200)
    job_dir = parent / "system" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "stdout.log").write_text("SHELL-OUTPUT-MUST-NOT-ENTER-PROMPT", encoding="utf-8")
    (job_dir / "stderr.log").write_text("", encoding="utf-8")
    state = manager._initial_async_state(job_id, "printf ignored", str(parent), 60)
    state.update({"status": "completed", "exit_status_known": True, "exit_code": 0})
    write_initial_state(job_dir, state)

    manager._publish_completion_if_due(job_id, job_dir)
    assert load_state(job_dir)["completion"]["state"] == "publishing"
    assert job_id not in json.dumps(run_dir.state_snapshot()["pending_shell_prompt_events"])

    run_dir.drain_shell_prompt_events(limit=run_dir._MAX_PENDING_SHELL_PROMPT_EVENTS)
    manager._publish_completion_if_due(job_id, job_dir)
    assert load_state(job_dir)["completion"]["state"] == "published"
    queued = run_dir.state_snapshot()["pending_shell_prompt_events"]
    assert queued[0]["job_id"] == job_id
    assert "SHELL-OUTPUT-MUST-NOT-ENTER-PROMPT" not in json.dumps(queued)


def _wait_for(predicate, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met before timeout")


def _fill_shell_prompt_queue(run_dir, adapter) -> None:
    for index in range(1, 1 + run_dir._MAX_PENDING_SHELL_PROMPT_EVENTS):
        job_id = _job(index)
        assert adapter.publish_channel(
            "bash", _completion_payload(job_id), ref_id=f"bash.completion:{job_id}",
        ) is True


def _detached_retry_manager(parent, run_dir, adapter):
    return ShellManager(
        ShellPolicy.yolo(), str(parent), notification_port=adapter,
        async_jobs_dir=run_dir.path / "shell-jobs", retry_failed_publications=True,
        rehydrate=False,
    )


def test_detached_shell_completion_retries_after_full_queue_drains_without_polling(tmp_path):
    """A live completion watcher reconciles its ref once the bounded queue frees."""
    parent = tmp_path / "agent"
    run_dir = make_daemon_run_dir(parent_working_dir=parent, tools=["shell"])
    adapter = DaemonShellPromptEventAdapter(run_dir)
    manager = _detached_retry_manager(parent, run_dir, adapter)
    _fill_shell_prompt_queue(run_dir, adapter)

    job_id = _job(200)
    job_dir = manager._ensure_jobs_dir() / job_id
    job_dir.mkdir()
    (job_dir / "stdout.log").write_text("COMPLETION-OUTPUT-MUST-STAY-PRIVATE", encoding="utf-8")
    (job_dir / "stderr.log").write_text("", encoding="utf-8")
    state = manager._initial_async_state(job_id, "printf ignored", str(parent), 60)
    state.update({"status": "completed", "exit_status_known": True, "exit_code": 0})
    write_initial_state(job_dir, state)
    manager._start_completion_watcher(job_id, job_dir)

    _wait_for(lambda: load_state(job_dir)["completion"]["state"] == "publishing")
    assert job_id not in json.dumps(run_dir.state_snapshot()["pending_shell_prompt_events"])
    run_dir.drain_shell_prompt_events(limit=run_dir._MAX_PENDING_SHELL_PROMPT_EVENTS)

    _wait_for(lambda: load_state(job_dir)["completion"]["state"] == "published")
    queued = run_dir.state_snapshot()["pending_shell_prompt_events"]
    assert [event["job_id"] for event in queued] == [job_id]
    assert queued[0]["kind"] == "shell_completion"
    assert load_state(job_dir)["terminal_polled"] is False
    assert "COMPLETION-OUTPUT-MUST-STAY-PRIVATE" not in json.dumps(queued)


def test_detached_shell_reminder_retries_after_full_queue_drains_without_polling(tmp_path):
    """A due reminder re-arms capped retry timers until its stable ref is queued."""
    parent = tmp_path / "agent"
    run_dir = make_daemon_run_dir(parent_working_dir=parent, tools=["shell"])
    adapter = DaemonShellPromptEventAdapter(run_dir)
    manager = _detached_retry_manager(parent, run_dir, adapter)
    _fill_shell_prompt_queue(run_dir, adapter)

    job_id = _job(201)
    job_dir = manager._ensure_jobs_dir() / job_id
    job_dir.mkdir()
    state = manager._initial_async_state(job_id, "sleep 60", str(parent), 60)
    state.update({"status": "running", "return_handoff": {"state": "armed"}})
    state["reminder"]["deadline_at"] = time.time() - 1
    write_initial_state(job_dir, state)
    manager._start_reminder_timer(job_id, job_dir, delay=0)

    _wait_for(lambda: load_state(job_dir)["reminder"]["state"] == "publishing")
    assert job_id not in json.dumps(run_dir.state_snapshot()["pending_shell_prompt_events"])
    run_dir.drain_shell_prompt_events(limit=run_dir._MAX_PENDING_SHELL_PROMPT_EVENTS)

    _wait_for(lambda: load_state(job_dir)["reminder"]["state"] == "published")
    queued = run_dir.state_snapshot()["pending_shell_prompt_events"]
    assert [event["job_id"] for event in queued] == [job_id]
    assert queued[0]["kind"] == "shell_reminder"
    assert load_state(job_dir)["terminal_polled"] is False


def test_daemon_system_prompt_exposes_shell_local_delivery_only_when_selected():
    from lingtai.tools.daemon.system_prompt import build_daemon_system_prompt

    shell_prompt = build_daemon_system_prompt(task="x", tool_names=["shell"])
    plain_prompt = build_daemon_system_prompt(task="x", tool_names=["web_search"])
    assert "Detached Shell async events" in shell_prompt
    assert "call shell.poll for exact output" in shell_prompt
    assert "Detached Shell async events" not in plain_prompt
