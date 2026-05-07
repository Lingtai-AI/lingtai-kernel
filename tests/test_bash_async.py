"""Tests for async mode of the bash capability."""
import json
import time
from pathlib import Path

import pytest

from lingtai.core.bash import BashManager, BashPolicy


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

    def test_async_stderr_captured(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.handle({"command": "echo err >&2", "async": True})
        job_id = result["job_id"]

        time.sleep(0.5)

        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        assert poll["status"] == "done"
        assert "err" in poll["stderr"]
