"""Tests for async mode of the bash capability."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path

import pytest

from lingtai_kernel.notifications import collect_notifications
from tools.bash import BashManager, BashPolicy, get_schema


class TestBashAsync:
    """Tests for async run / poll / cancel."""

    def _make_manager(self, tmp_path: Path) -> BashManager:
        return BashManager(policy=BashPolicy.yolo(), working_dir=str(tmp_path))

    # 1. async run returns job_id and pid
    def test_async_run_returns_job_id_and_pid(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo hello", "async": True})
        assert result["status"] == "ok"
        assert result["job_id"].startswith("job-")
        assert isinstance(result["pid"], int)
        assert "poll" in result["message"]
        # Allow process to finish and clean up
        time.sleep(0.3)

    # 2. poll returns 'running' while command is executing
    def test_poll_returns_running(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "sleep 5", "async": True})
        job_id = result["job_id"]

        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "running"
        assert poll["job_id"] == job_id

        # Clean up
        mgr.handle({"action": "cancel", "command": "", "job_id": job_id})

    # 3. poll returns 'done' with output after command finishes
    def test_poll_returns_done_with_output(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo async-output", "async": True})
        job_id = result["job_id"]

        # Wait for the fast command to finish
        time.sleep(0.5)

        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "done"
        assert "async-output" in poll["stdout"]
        assert "exit_code" in poll
        # Fidelity fields apply to the async poll path too.
        assert poll["ok"] is True
        assert poll["command_status"] == "success"

    # 3b. poll on a failed async job surfaces the failure explicitly
    def test_poll_nonzero_exit_is_flagged_failed(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "exit 7", "async": True})
        job_id = result["job_id"]

        time.sleep(0.5)

        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "done"
        assert poll["exit_code"] == 7
        assert poll["ok"] is False
        assert poll["command_status"] == "failed"
        assert "exited with code 7" in poll["warning"]

    # 3c. a still-running poll has no exit_code, so no fidelity fields
    def test_poll_running_has_no_fidelity_fields(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "sleep 5", "async": True})
        job_id = result["job_id"]

        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "running"
        assert "ok" not in poll
        assert "command_status" not in poll

        mgr.handle({"action": "cancel", "command": "", "job_id": job_id})

    # 4. cancel kills the process
    def test_cancel_kills_process(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "sleep 60", "async": True})
        job_id = result["job_id"]
        pid = result["pid"]

        cancel = mgr.handle({"action": "cancel", "command": "", "job_id": job_id})
        assert cancel["status"] == "cancelled"
        assert cancel["job_id"] == job_id

        # Verify process is dead
        time.sleep(0.2)
        import os
        with pytest.raises(OSError):
            os.kill(pid, 0)

    # 5. policy still applies to async commands
    def test_policy_applies_to_async(self, tmp_path):
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"deny": ["rm"]}))
        policy = BashPolicy.from_file(str(policy_file))
        mgr = BashManager(policy=policy, working_dir=str(tmp_path))

        result = mgr.handle({"command": "rm -rf /", "async": True})
        assert result["status"] == "error"
        assert "not allowed" in result["message"]

    # 6. working_dir validation still applies
    def test_working_dir_validation_applies_to_async(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({
            "command": "echo hi",
            "async": True,
            "working_dir": "/etc",
        })
        assert result["status"] == "error"
        assert "working_dir" in result["message"]

    # 7. missing job_id returns error
    def test_poll_missing_job_id(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"action": "poll", "command": ""})
        assert result["status"] == "error"
        assert "job_id is required" in result["message"]

    def test_cancel_missing_job_id(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"action": "cancel", "command": ""})
        assert result["status"] == "error"
        assert "job_id is required" in result["message"]

    # 8. double-poll after completion returns error (job already cleaned up)
    def test_double_poll_after_completion(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo done", "async": True})
        job_id = result["job_id"]

        time.sleep(0.5)

        # First poll — should succeed and clean up
        poll1 = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll1["status"] == "done"

        # Second poll — job dir is gone
        poll2 = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll2["status"] == "error"
        assert "not found" in poll2["message"].lower()

    def test_poll_nonexistent_job(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"action": "poll", "command": "", "job_id": "job-doesnotexist"})
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_cancel_nonexistent_job(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"action": "cancel", "command": "", "job_id": "job-doesnotexist"})
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    # Sync path unchanged — default action='run', async=false
    def test_sync_path_unchanged(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo sync-test"})
        assert result["status"] == "ok"
        assert result["exit_code"] == 0
        assert "sync-test" in result["stdout"]

    def test_sync_ignores_reminder_field(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo sync-test", "reminder": float("nan")})
        assert result["status"] == "ok"
        assert result["exit_code"] == 0
        assert "sync-test" in result["stdout"]

    def test_async_stderr_captured(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo err >&2", "async": True})
        job_id = result["job_id"]

        time.sleep(0.5)

        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "done"
        assert "err" in poll["stderr"]

    def test_schema_requires_reminder_with_runtime_default(self, tmp_path):
        schema = get_schema()
        assert "reminder" in schema["required"]
        assert schema["properties"]["reminder"]["default"] == 1800.0

        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo compat", "async": True})
        assert result["status"] == "ok"
        mgr.handle({"action": "cancel", "command": "", "job_id": result["job_id"]})

    @pytest.mark.parametrize(
        "value",
        [
            -1,
            "soon",
            object(),
            True,
            float("nan"),
            float("inf"),
            -float("inf"),
            threading.TIMEOUT_MAX + 1,
        ],
    )
    def test_async_reminder_rejects_invalid_values(self, tmp_path, value):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo nope", "async": True, "reminder": value})
        assert result["status"] == "error"
        assert "reminder" in result["message"]

    def test_async_reminder_publishes_after_delay_even_if_process_exited_unpolled(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo finished", "async": True, "reminder": 0.05})
        job_id = result["job_id"]

        time.sleep(0.3)

        events = collect_notifications(tmp_path)["system"]["data"]["events"]
        assert len(events) == 1
        event = events[0]
        assert event["source"] == "bash.reminder"
        assert event["ref_id"] == f"bash.reminder:{job_id}"
        assert job_id in event["body"]
        assert "poll" in event["body"]
        assert "echo finished" not in event["body"]

        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "done"

    def test_async_reminder_does_not_overwrite_close_due_jobs(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        first = mgr.handle({"command": "sleep 1", "async": True, "reminder": 0.05})
        second = mgr.handle({"command": "sleep 1", "async": True, "reminder": 0.05})
        job_ids = {first["job_id"], second["job_id"]}

        time.sleep(0.3)

        events = collect_notifications(tmp_path)["system"]["data"]["events"]
        assert len(events) == 2
        assert {event["ref_id"] for event in events} == {
            f"bash.reminder:{job_id}" for job_id in job_ids
        }
        assert len({event["event_id"] for event in events}) == 2

        for job_id in job_ids:
            mgr.handle({"action": "cancel", "command": "", "job_id": job_id})

    def test_terminal_poll_suppresses_async_reminder(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo handled", "async": True, "reminder": 0.3})
        job_id = result["job_id"]

        time.sleep(0.1)
        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "done"
        time.sleep(0.3)

        assert "system" not in collect_notifications(tmp_path)

    def test_successful_cancel_suppresses_async_reminder(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "sleep 5", "async": True, "reminder": 0.2})
        job_id = result["job_id"]

        cancel = mgr.handle({
            "action": "cancel",
            "command": "",
            "job_id": job_id,
            "reminder": float("nan"),
        })
        assert cancel["status"] == "cancelled"
        time.sleep(0.3)

        assert "system" not in collect_notifications(tmp_path)

    def test_poll_ignores_reminder_field(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "sleep 5", "async": True, "reminder": 10})
        job_id = result["job_id"]

        poll = mgr.handle({
            "action": "poll",
            "command": "",
            "job_id": job_id,
            "reminder": float("nan"),
        })
        assert poll["status"] == "running"

        mgr.handle({"action": "cancel", "command": "", "job_id": job_id})

    def test_terminal_pop_before_deadline_claim_suppresses_reminder(self, tmp_path, monkeypatch):
        mgr = self._make_manager(tmp_path)
        job_id = "job-race-terminal"
        job_dir = tmp_path / "system" / "jobs" / job_id
        job_dir.mkdir(parents=True)
        cancel_event = threading.Event()
        with mgr._reminder_lock:
            mgr._reminder_cancel_events[job_id] = cancel_event

        published = []
        monkeypatch.setattr(mgr, "_publish_async_reminder", published.append)

        mgr._cancel_reminder_timer(job_id)
        mgr._run_reminder_timer(job_id, job_dir, 0, cancel_event)

        assert published == []
        assert job_id not in mgr._reminder_cancel_events

    def test_deadline_claim_before_terminal_pop_publishes_once(self, tmp_path, monkeypatch):
        mgr = self._make_manager(tmp_path)
        job_id = "job-race-deadline"
        job_dir = tmp_path / "system" / "jobs" / job_id
        job_dir.mkdir(parents=True)
        cancel_event = threading.Event()
        with mgr._reminder_lock:
            mgr._reminder_cancel_events[job_id] = cancel_event

        published = []

        def fake_publish(claimed_job_id):
            published.append(claimed_job_id)
            mgr._cancel_reminder_timer(claimed_job_id)

        monkeypatch.setattr(mgr, "_publish_async_reminder", fake_publish)

        mgr._run_reminder_timer(job_id, job_dir, 0, cancel_event)

        assert published == [job_id]
        assert job_id not in mgr._reminder_cancel_events

    def test_agent_exception_fallback_uses_agent_system_lock(self, tmp_path, monkeypatch):
        agent_lock = threading.Lock()
        agent = SimpleNamespace(_system_notification_lock=agent_lock)

        def failing_enqueue(**_kwargs):
            raise RuntimeError("boom")

        agent._enqueue_system_notification = failing_enqueue
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir=str(tmp_path), agent=agent)

        seen = {}

        def fake_fallback(**kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(mgr, "_append_system_notification_fallback", fake_fallback)

        mgr._publish_async_reminder("job-fallback")

        assert seen["lock"] is agent_lock

    def test_direct_manager_fallback_is_shared_across_managers(self, tmp_path):
        first = self._make_manager(tmp_path)
        second = self._make_manager(tmp_path)
        managers = [first, second]
        n_events = 20

        def worker(i: int) -> None:
            managers[i % 2]._append_system_notification_fallback(
                source="bash.reminder",
                ref_id=f"bash.reminder:job-{i}",
                body=f"poll job-{i}",
            )

        with ThreadPoolExecutor(max_workers=n_events) as pool:
            list(pool.map(worker, range(n_events)))

        events = collect_notifications(tmp_path)["system"]["data"]["events"]
        assert len(events) == n_events
        assert {event["ref_id"] for event in events} == {
            f"bash.reminder:job-{i}" for i in range(n_events)
        }
        assert len({event["event_id"] for event in events}) == n_events

    def test_direct_manager_fallback_event_ids_keep_entropy_with_fixed_millisecond(
        self, tmp_path, monkeypatch
    ):
        mgr = self._make_manager(tmp_path)
        suffixes = iter(("a" * 16, "b" * 16))
        monkeypatch.setattr("time.time", lambda: 1234.567)
        monkeypatch.setattr("secrets.token_hex", lambda n: next(suffixes))

        first = mgr._append_system_notification_fallback(
            source="bash.reminder",
            ref_id="bash.reminder:job-a",
            body="poll job-a",
        )
        second = mgr._append_system_notification_fallback(
            source="bash.reminder",
            ref_id="bash.reminder:job-b",
            body="poll job-b",
        )

        assert first.startswith("evt_")
        assert second.startswith("evt_")
        assert first != second
        assert [len(event_id.rsplit("_", 1)[1]) for event_id in (first, second)] == [16, 16]
