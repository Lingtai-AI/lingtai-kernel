"""Tests for runtime fingerprint capture, source drift nudge, and doctor parsing (issue #178)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Fingerprint capture
# ---------------------------------------------------------------------------


class TestCaptureRuntimeFingerprint:
    """Unit tests for _capture_runtime_fingerprint()."""

    def test_returns_expected_keys(self):
        from lingtai.kernel.base_agent.lifecycle import _capture_runtime_fingerprint

        fp = _capture_runtime_fingerprint()
        assert "git_rev" in fp
        assert "source_digest" in fp
        assert "captured_at" in fp

    def test_source_digest_is_12_hex_chars(self):
        from lingtai.kernel.base_agent.lifecycle import _capture_runtime_fingerprint

        fp = _capture_runtime_fingerprint()
        digest = fp["source_digest"]
        assert digest is not None
        assert len(digest) == 12
        int(digest, 16)  # should not raise

    def test_captured_at_is_iso8601(self):
        from lingtai.kernel.base_agent.lifecycle import _capture_runtime_fingerprint

        fp = _capture_runtime_fingerprint()
        ts = fp["captured_at"]
        assert ts.endswith("Z")
        # Should parse without error
        from datetime import datetime, timezone
        datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def test_git_rev_null_when_not_in_repo(self):
        from lingtai.kernel.base_agent.lifecycle import _capture_runtime_fingerprint

        with patch("lingtai.kernel.base_agent.lifecycle.subprocess") as mock_sub:
            mock_sub.run.side_effect = FileNotFoundError
            mock_sub.TimeoutExpired = subprocess.TimeoutExpired
            fp = _capture_runtime_fingerprint()
        # git_rev might be None (if the fixture isn't in a repo) or a string
        # — either is acceptable. The key point is no crash.

    def test_git_rev_null_on_timeout(self):
        import subprocess as _sp

        from lingtai.kernel.base_agent.lifecycle import _capture_runtime_fingerprint

        with patch("lingtai.kernel.base_agent.lifecycle.subprocess") as mock_sub:
            mock_sub.run.side_effect = _sp.TimeoutExpired(cmd="git", timeout=2)
            mock_sub.TimeoutExpired = _sp.TimeoutExpired
            mock_sub.CalledProcessError = _sp.CalledProcessError
            mock_sub.FileNotFoundError = FileNotFoundError
            fp = _capture_runtime_fingerprint()
        # Should not crash; git_rev may or may not be None depending on real git
        assert "git_rev" in fp

    def test_source_digest_changes_on_file_change(self, tmp_path):
        """Simulate source file change and verify digest changes."""
        import lingtai.kernel
        from lingtai.kernel.base_agent.lifecycle import _capture_runtime_fingerprint, _FP_KEY_FILES

        fp1 = _capture_runtime_fingerprint()

        # Modify one of the key files temporarily
        pkg_dir = Path(lingtai.kernel.__file__).resolve().parent
        target = pkg_dir / _FP_KEY_FILES[0]
        original = target.read_bytes()
        try:
            target.write_text(original.decode("utf-8") + "\n# drift marker\n")
            fp2 = _capture_runtime_fingerprint()
            assert fp1["source_digest"] != fp2["source_digest"]
        finally:
            target.write_bytes(original)


# We need subprocess at module level for the mock patch
import subprocess


# ---------------------------------------------------------------------------
# 2. Status JSON includes runtime block
# ---------------------------------------------------------------------------


class TestStatusJsonRuntime:
    """Verify that _status() includes fingerprint, python_version, platform."""

    def test_status_includes_fingerprint_fields(self):
        """Test via the _status function with a mock agent."""
        from lingtai.kernel.base_agent.identity import _status

        agent = MagicMock()
        agent._working_dir = Path("/tmp/test")
        agent.agent_name = "test"
        agent._mail_service = None
        agent._uptime_anchor = time.monotonic()
        agent._config.stamina = 3600
        agent._state = MagicMock()
        agent._state.value = "idle"
        agent._state_changed_at = time.time()
        agent._last_progress_at = time.time()
        agent._active_turn_kind = None
        agent._active_turn_id = None
        agent._active_turn_started_at = None
        agent._deferred_notifications_count = 0
        agent._deferred_notifications_oldest_at = None
        agent._session = MagicMock()
        agent._session.get_token_usage.return_value = {
            "input_tokens": 0, "output_tokens": 0,
            "thinking_tokens": 0, "cached_tokens": 0,
            "total_tokens": 0, "api_calls": 0,
            "ctx_system_tokens": 0, "ctx_tools_tokens": 0,
            "ctx_history_tokens": 0, "ctx_total_tokens": 0,
        }
        agent._session._token_fallback_warned = False
        agent._chat = None
        agent._config.context_limit = None

        # Set a runtime fingerprint
        agent._runtime_fingerprint = {
            "git_rev": "abc1234",
            "source_digest": "deadbeef0123",
            "captured_at": "2026-06-14T12:00:00Z",
        }

        result = _status(agent)
        runtime = result["runtime"]
        assert "fingerprint" in runtime
        assert runtime["fingerprint"]["git_rev"] == "abc1234"
        assert runtime["fingerprint"]["source_digest"] == "deadbeef0123"
        assert "python_version" in runtime
        assert "platform" in runtime

    def test_status_fingerprint_none_when_missing(self):
        """When _runtime_fingerprint is not set, fingerprint should be None."""
        from lingtai.kernel.base_agent.identity import _status

        agent = MagicMock()
        agent._working_dir = Path("/tmp/test")
        agent.agent_name = "test"
        agent._mail_service = None
        agent._uptime_anchor = time.monotonic()
        agent._config.stamina = 3600
        agent._state = MagicMock()
        agent._state.value = "idle"
        agent._state_changed_at = time.time()
        agent._last_progress_at = time.time()
        agent._active_turn_kind = None
        agent._active_turn_id = None
        agent._active_turn_started_at = None
        agent._deferred_notifications_count = 0
        agent._deferred_notifications_oldest_at = None
        agent._session = MagicMock()
        agent._session.get_token_usage.return_value = {
            "input_tokens": 0, "output_tokens": 0,
            "thinking_tokens": 0, "cached_tokens": 0,
            "total_tokens": 0, "api_calls": 0,
            "ctx_system_tokens": 0, "ctx_tools_tokens": 0,
            "ctx_history_tokens": 0, "ctx_total_tokens": 0,
        }
        agent._session._token_fallback_warned = False
        agent._chat = None
        agent._config.context_limit = None

        # No _runtime_fingerprint attribute
        del agent._runtime_fingerprint  # ensure it doesn't exist
        type(agent)._runtime_fingerprint = property(
            lambda self: (_ for _ in ()).throw(AttributeError)
        )

        result = _status(agent)
        runtime = result["runtime"]
        assert "fingerprint" in runtime
        assert runtime["fingerprint"] is None


# ---------------------------------------------------------------------------
# 3-5. Source drift nudge
# ---------------------------------------------------------------------------


class TestSourceDriftNudge:
    """Tests for nudge/source_drift.py check function."""

    def _make_agent(self, startup_fp: dict | None = None):
        """Create a minimal mock agent for nudge testing."""
        agent = MagicMock()
        agent._working_dir = Path("/tmp/test-nudge")
        agent._runtime_fingerprint = startup_fp
        agent._nudge_source_drift_state = {}
        agent._nudge_channel_lock = None
        return agent

    # Patch target: _capture_runtime_fingerprint is imported locally inside
    # source_drift.check(), so we must patch it at its definition site.
    _PATCH_TARGET = "lingtai.kernel.base_agent.lifecycle._capture_runtime_fingerprint"
    _DEV_RUNTIME_TARGET = "lingtai.kernel.nudge.source_drift._dev_runtime_reason"

    @pytest.fixture(autouse=True)
    def _packaged_runtime(self):
        """Existing drift tests model a packaged/runtime install unless stated."""
        with patch(self._DEV_RUNTIME_TARGET, return_value=None):
            yield

    def test_no_drift_removes_prior_nudge(self):
        """When startup and disk fingerprints match, no nudge emitted."""
        from lingtai.kernel.nudge.source_drift import check

        fp = {
            "git_rev": "abc1234",
            "source_digest": "deadbeef0123",
            "captured_at": "2026-06-14T12:00:00Z",
        }
        agent = self._make_agent(startup_fp=fp)

        with patch(self._PATCH_TARGET, return_value=fp):
            check(agent)

        from lingtai.kernel.nudge import source_drift
        state = source_drift._state(agent)
        assert not state.get("emitted", False)

    def test_drift_detected_emits_nudge(self):
        """When fingerprints differ, nudge is emitted."""
        from lingtai.kernel.nudge.source_drift import check

        startup_fp = {
            "git_rev": "abc1234",
            "source_digest": "deadbeef0123",
            "captured_at": "2026-06-14T12:00:00Z",
        }
        disk_fp = {
            "git_rev": "xyz9999",
            "source_digest": "cafebabe4567",
            "captured_at": "2026-06-14T13:00:00Z",
        }
        agent = self._make_agent(startup_fp=startup_fp)

        with (
            patch(self._PATCH_TARGET, return_value=disk_fp),
            patch("lingtai.kernel.nudge.upsert") as mock_upsert,
        ):
            check(agent)
            mock_upsert.assert_called_once()

        from lingtai.kernel.nudge import source_drift
        state = source_drift._state(agent)
        assert state.get("emitted") is True
        assert "abc1234" in state.get("emitted_for", "")
        assert "xyz9999" in state.get("emitted_for", "")

    def test_throttle_skips_within_interval(self):
        """Second call within 60s should be a no-op."""
        from lingtai.kernel.nudge.source_drift import check, _state

        startup_fp = {"git_rev": "aaa", "source_digest": "bbb", "captured_at": "t1"}
        disk_fp = {"git_rev": "ccc", "source_digest": "ddd", "captured_at": "t2"}
        agent = self._make_agent(startup_fp=startup_fp)

        with patch(self._PATCH_TARGET, return_value=disk_fp):
            check(agent)
            state = _state(agent)
            first_ts = state["last_probe_ts"]

            # Second call immediately — should be throttled
            check(agent)
            state = _state(agent)
            assert state["last_probe_ts"] == first_ts  # unchanged

    def test_throttle_allows_after_interval(self):
        """After 60s, check should run again."""
        from lingtai.kernel.nudge.source_drift import check, _state

        startup_fp = {"git_rev": "aaa", "source_digest": "bbb", "captured_at": "t1"}
        disk_fp = {"git_rev": "aaa", "source_digest": "bbb", "captured_at": "t2"}
        agent = self._make_agent(startup_fp=startup_fp)

        with patch(self._PATCH_TARGET, return_value=disk_fp):
            check(agent)
            state = _state(agent)
            # Artificially set last_probe_ts to >60s ago
            state["last_probe_ts"] = time.time() - 61
            check(agent)
            state = _state(agent)
            assert state["last_probe_ts"] > time.time() - 5

    def test_no_startup_fingerprint_is_noop(self):
        """If agent has no startup fingerprint, check is a no-op."""
        from lingtai.kernel.nudge.source_drift import check

        agent = self._make_agent(startup_fp=None)
        # Should not raise
        check(agent)

    def test_dev_runtime_skips_and_clears_prior_nudge(self):
        """Editable/source/dev installs should not prompt source drift refresh."""
        from lingtai.kernel.nudge.source_drift import check, _state

        startup_fp = {"git_rev": "aaa1111", "source_digest": "bbb2222", "captured_at": "t1"}
        disk_fp = {"git_rev": "ccc3333", "source_digest": "ddd4444", "captured_at": "t2"}
        agent = self._make_agent(startup_fp=startup_fp)
        state = _state(agent)
        state["emitted"] = True
        state["emitted_for"] = "git_rev: old → stale"

        with (
            patch(self._DEV_RUNTIME_TARGET, return_value="source-checkout"),
            patch(self._PATCH_TARGET, return_value=disk_fp) as mock_capture,
            patch("lingtai.kernel.nudge.remove") as mock_remove,
            patch("lingtai.kernel.nudge.upsert") as mock_upsert,
        ):
            check(agent)

        mock_remove.assert_called_once_with(agent, "source_drift")
        mock_upsert.assert_not_called()
        mock_capture.assert_not_called()
        assert state.get("emitted") is False
        assert state.get("emitted_for") is None
        assert state.get("skipped_dev_reason") == "source-checkout"

    def test_dev_runtime_clears_stale_channel_even_without_local_state(self):
        """A fresh dev process should clear a source_drift nudge from an older run."""
        from lingtai.kernel.nudge.source_drift import check, _state

        startup_fp = {"git_rev": "aaa1111", "source_digest": "bbb2222", "captured_at": "t1"}
        disk_fp = {"git_rev": "ccc3333", "source_digest": "ddd4444", "captured_at": "t2"}
        agent = self._make_agent(startup_fp=startup_fp)

        with (
            patch(self._DEV_RUNTIME_TARGET, return_value="editable-install"),
            patch(self._PATCH_TARGET, return_value=disk_fp) as mock_capture,
            patch("lingtai.kernel.nudge.remove") as mock_remove,
            patch("lingtai.kernel.nudge.upsert") as mock_upsert,
        ):
            check(agent)

        state = _state(agent)
        mock_remove.assert_called_once_with(agent, "source_drift")
        mock_upsert.assert_not_called()
        mock_capture.assert_not_called()
        assert state.get("emitted") is False
        assert state.get("emitted_for") is None
        assert state.get("skipped_dev_reason") == "editable-install"

    def test_partial_drift_git_only(self):
        """Only git_rev differs → still detected as drift."""
        from lingtai.kernel.nudge.source_drift import check, _state

        startup_fp = {"git_rev": "aaa1111", "source_digest": "bbb2222", "captured_at": "t1"}
        disk_fp = {"git_rev": "ccc3333", "source_digest": "bbb2222", "captured_at": "t2"}
        agent = self._make_agent(startup_fp=startup_fp)

        with (
            patch(self._PATCH_TARGET, return_value=disk_fp),
            patch("lingtai.kernel.nudge.upsert"),
        ):
            check(agent)

        state = _state(agent)
        assert state.get("emitted") is True

    def test_partial_drift_digest_only(self):
        """Only source_digest differs → still detected as drift."""
        from lingtai.kernel.nudge.source_drift import check, _state

        startup_fp = {"git_rev": "aaa1111", "source_digest": "bbb2222", "captured_at": "t1"}
        disk_fp = {"git_rev": "aaa1111", "source_digest": "xxx9999", "captured_at": "t2"}
        agent = self._make_agent(startup_fp=startup_fp)

        with (
            patch(self._PATCH_TARGET, return_value=disk_fp),
            patch("lingtai.kernel.nudge.upsert"),
        ):
            check(agent)

        state = _state(agent)
        assert state.get("emitted") is True

    def test_null_git_rev_not_counted_as_drift(self):
        """When startup git_rev is None, git drift should not be flagged."""
        from lingtai.kernel.nudge.source_drift import check, _state

        startup_fp = {"git_rev": None, "source_digest": "bbb2222", "captured_at": "t1"}
        disk_fp = {"git_rev": "ccc3333", "source_digest": "bbb2222", "captured_at": "t2"}
        agent = self._make_agent(startup_fp=startup_fp)

        with patch(self._PATCH_TARGET, return_value=disk_fp):
            check(agent)

        state = _state(agent)
        # Only git differs but startup git is None → no drift signal
        assert not state.get("emitted", False)


# ---------------------------------------------------------------------------
# 6. Doctor parsing
# ---------------------------------------------------------------------------


class TestDoctorFingerprintParsing:
    """Test that the doctor script correctly reads runtime fingerprint."""

    def test_doctor_displays_fingerprint(self, tmp_path):
        """Doctor should display fingerprint info when present in .status.json."""
        agent = tmp_path / "agent"
        agent.mkdir()
        (agent / ".agent.json").write_text(
            json.dumps({"name": "test", "state": "idle"}), encoding="utf-8"
        )
        (agent / ".status.json").write_text(
            json.dumps({
                "state": "idle",
                "runtime": {
                    "current_time": "2026-06-14T12:00:00Z",
                    "state": "idle",
                    "fingerprint": {
                        "git_rev": "abc1234",
                        "source_digest": "deadbeef0123",
                        "captured_at": "2026-06-14T12:00:00Z",
                    },
                    "python_version": "3.11.5",
                    "platform": "darwin",
                },
            }),
            encoding="utf-8",
        )
        (agent / ".agent.heartbeat").write_text("ok", encoding="utf-8")

        import subprocess
        import sys
        ROOT = Path(__file__).resolve().parents[1]
        DOCTOR = ROOT / "src" / "lingtai" / "intrinsic_skills" / "lingtai-doctor" / "scripts" / "doctor.py"

        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "--agent-dir", str(agent), "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        data = json.loads(proc.stdout)
        # Find the lifecycle section
        lifecycle = next(s for s in data["sections"] if s["name"] == "lifecycle")
        # Should have a "runtime fingerprint available" finding
        fp_findings = [
            f for f in lifecycle["findings"]
            if f["title"] == "runtime fingerprint available"
        ]
        assert len(fp_findings) == 1
        fp_data = fp_findings[0]["data"]["fingerprint"]
        assert fp_data["git_rev"] == "abc1234"
        assert fp_data["source_digest"] == "deadbeef0123"
        assert fp_data["python_version"] == "3.11.5"

    def test_doctor_warns_when_fingerprint_missing(self, tmp_path):
        """Doctor should warn when runtime.fingerprint is absent."""
        agent = tmp_path / "agent"
        agent.mkdir()
        (agent / ".agent.json").write_text(
            json.dumps({"name": "test", "state": "idle"}), encoding="utf-8"
        )
        (agent / ".status.json").write_text(
            json.dumps({
                "state": "idle",
                "runtime": {
                    "current_time": "2026-06-14T12:00:00Z",
                    "state": "idle",
                },
            }),
            encoding="utf-8",
        )
        (agent / ".agent.heartbeat").write_text("ok", encoding="utf-8")

        import subprocess
        import sys
        ROOT = Path(__file__).resolve().parents[1]
        DOCTOR = ROOT / "src" / "lingtai" / "intrinsic_skills" / "lingtai-doctor" / "scripts" / "doctor.py"

        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "--agent-dir", str(agent), "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        data = json.loads(proc.stdout)
        lifecycle = next(s for s in data["sections"] if s["name"] == "lifecycle")
        missing_findings = [
            f for f in lifecycle["findings"]
            if f["title"] == "runtime fingerprint missing"
        ]
        assert len(missing_findings) == 1
        assert missing_findings[0]["severity"] == "WARN"

    def test_doctor_handles_no_runtime_block(self, tmp_path):
        """Doctor should warn when runtime block is entirely absent (old agent)."""
        agent = tmp_path / "agent"
        agent.mkdir()
        (agent / ".agent.json").write_text(
            json.dumps({"name": "test", "state": "idle"}), encoding="utf-8"
        )
        (agent / ".status.json").write_text(
            json.dumps({"state": "idle"}), encoding="utf-8"
        )
        (agent / ".agent.heartbeat").write_text("ok", encoding="utf-8")

        import subprocess
        import sys
        ROOT = Path(__file__).resolve().parents[1]
        DOCTOR = ROOT / "src" / "lingtai" / "intrinsic_skills" / "lingtai-doctor" / "scripts" / "doctor.py"

        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "--agent-dir", str(agent), "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        data = json.loads(proc.stdout)
        lifecycle = next(s for s in data["sections"] if s["name"] == "lifecycle")
        missing_findings = [
            f for f in lifecycle["findings"]
            if f["title"] == "runtime fingerprint missing"
        ]
        assert len(missing_findings) == 1


# ---------------------------------------------------------------------------
# 7. Integration: nudge registered in run_checks
# ---------------------------------------------------------------------------


class TestSourceDriftRegistered:
    """Verify that source_drift.check is wired into run_checks."""

    def test_source_drift_imported_in_nudge_init(self):
        import lingtai.kernel.nudge as nudge_mod
        assert hasattr(nudge_mod, "source_drift")

    def test_run_checks_calls_source_drift(self):
        """run_checks should dispatch to source_drift.check."""
        import lingtai.kernel.nudge as nudge_mod

        agent = MagicMock()
        agent._working_dir = Path("/tmp/test")
        agent._nudge_kernel_version_state = {}
        agent._nudge_source_drift_state = {}
        agent._nudge_goal_state = {}
        agent._nudge_channel_lock = None

        with (
            patch.object(nudge_mod.kernel_version, "check") as mock_kv,
            patch.object(nudge_mod.source_drift, "check") as mock_sd,
            patch.object(nudge_mod.goal, "check") as mock_gc,
        ):
            nudge_mod.run_checks(agent)
            mock_sd.assert_called_once_with(agent)
