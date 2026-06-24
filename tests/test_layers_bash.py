"""Tests for the bash capability."""
import json
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from lingtai.core.bash import (
    BashManager,
    BashPolicy,
    get_schema,
    setup as setup_bash,
    _augment_command_result,
    _broad_scan_hint,
    _detect_failure_signature,
    _redact_warning_tail,
)


# ---------------------------------------------------------------------------
# BashPolicy
# ---------------------------------------------------------------------------

class TestBashPolicy:
    def test_load_from_file(self, tmp_path):
        """Policy should load allow/deny from JSON file."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"allow": ["git", "ls"], "deny": ["rm"]}))
        policy = BashPolicy.from_file(str(policy_file))
        assert policy.is_allowed("git status")
        assert policy.is_allowed("ls -la")
        assert not policy.is_allowed("rm -rf /")

    def test_allow_only(self, tmp_path):
        """With only allow list, unlisted commands are denied."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"allow": ["git", "echo"]}))
        policy = BashPolicy.from_file(str(policy_file))
        assert policy.is_allowed("git push")
        assert not policy.is_allowed("curl http://evil.com")

    def test_deny_only(self, tmp_path):
        """With only deny list, unlisted commands are allowed."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"deny": ["rm", "sudo"]}))
        policy = BashPolicy.from_file(str(policy_file))
        assert policy.is_allowed("ls -la")
        assert not policy.is_allowed("rm file.txt")
        assert not policy.is_allowed("sudo apt install")

    def test_allow_ignores_deny(self, tmp_path):
        """When allow is present, deny is ignored (allowlist mode)."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"allow": ["git", "rm"], "deny": ["rm"]}))
        policy = BashPolicy.from_file(str(policy_file))
        assert policy.is_allowed("git status")
        assert policy.is_allowed("rm file")  # allow mode — rm is in allow, deny ignored
        assert not policy.is_allowed("curl http://x")  # not in allow → blocked

    def test_pipe_awareness(self, tmp_path):
        """Should check all commands in a pipe chain."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"deny": ["rm"]}))
        policy = BashPolicy.from_file(str(policy_file))
        assert not policy.is_allowed("ls | rm -rf /")
        assert not policy.is_allowed("echo hello && rm file")
        assert not policy.is_allowed("echo hello; rm file")
        assert policy.is_allowed("ls | grep foo | sort")

    def test_subshell_awareness(self, tmp_path):
        """Should check commands inside $()."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"deny": ["rm"]}))
        policy = BashPolicy.from_file(str(policy_file))
        assert not policy.is_allowed("echo $(rm file)")

    def test_yolo_allows_everything(self):
        """Yolo policy should allow all commands."""
        policy = BashPolicy.yolo()
        assert policy.is_allowed("rm -rf /")
        assert policy.is_allowed("sudo shutdown -h now")

    def test_missing_file_raises(self):
        """Loading from nonexistent file should raise."""
        with pytest.raises(FileNotFoundError):
            BashPolicy.from_file("/nonexistent/policy.json")

    def test_empty_policy_file(self, tmp_path):
        """Empty policy (no allow, no deny) should allow everything."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({}))
        policy = BashPolicy.from_file(str(policy_file))
        assert policy.is_allowed("anything")


# ---------------------------------------------------------------------------
# BashManager
# ---------------------------------------------------------------------------

class TestBashManager:
    def test_echo(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle({"command": "echo hello"})
        assert result["status"] == "ok"
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_nonexistent_command(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle({"command": "definitely_not_a_real_command_xyz"})
        # status stays "ok" — it reflects that the shell spawned, not that the
        # inner command succeeded (preserving the existing executor contract).
        assert result["status"] == "ok"
        assert result["exit_code"] != 0

    # --- result fidelity: explicit pass/fail fields (T1a) ------------------

    def test_success_is_marked_ok(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle({"command": "echo hi"})
        assert result["exit_code"] == 0
        assert result["ok"] is True
        assert result["command_status"] == "success"
        # No warning on a clean success.
        assert "warning" not in result

    def test_nonzero_exit_is_flagged_failed_with_warning(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle({"command": "exit 3"})
        # status unchanged, but the failure is now impossible to miss.
        assert result["status"] == "ok"
        assert result["exit_code"] == 3
        assert result["ok"] is False
        assert result["command_status"] == "failed"
        assert "exited with code 3" in result["warning"]

    def test_warning_includes_stderr_tail(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle(
            {"command": "echo boom-marker 1>&2; exit 1"}
        )
        assert result["ok"] is False
        assert "boom-marker" in result["warning"]

    def test_python_traceback_is_detected(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        # A real interpreter traceback exits nonzero and prints to stderr.
        result = mgr.handle(
            {"command": "python3 -c 'raise ValueError(\"x\")'"}
        )
        assert result["ok"] is False
        assert result["command_status"] == "failed"
        assert "python_traceback" in result["warning"]

    def test_missing_module_is_detected(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle(
            {"command": "python3 -c 'import lingtai_kernel_does_not_exist_xyz'"}
        )
        assert result["ok"] is False
        assert "missing_module" in result["warning"]

    def test_zero_exit_with_traceback_in_output_is_flagged_without_failing(self):
        # A subshell swallows the nonzero exit but the traceback text leaks to
        # stdout — flag it as suspicious without claiming the command failed.
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle(
            {
                "command": "python3 -c 'raise ValueError(1)' 2>&1 | cat; true"
            }
        )
        assert result["exit_code"] == 0
        assert result["ok"] is True
        assert result["command_status"] == "success"
        assert "warning" in result
        assert "python_traceback" in result["warning"]

    def test_empty_command(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle({"command": ""})
        assert result["status"] == "error"

    def test_timeout(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle({"command": "sleep 10", "timeout": 0.5})
        assert result["status"] == "error"
        assert "timed out" in result["message"]
        # A plain sleep is not a broad scan — no recipe hint appended.
        assert "rg --files" not in result["message"]

    def test_timeout_on_broad_find_appends_rg_hint(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp")
        result = mgr.handle(
            {
                "command": (
                    "sleep 10; find /Users/x/work -name '*.py' -type f"
                ),
                "timeout": 0.5,
            }
        )
        assert result["status"] == "error"
        assert "timed out" in result["message"]
        assert "rg --files" in result["message"]

    def test_policy_denies(self, tmp_path):
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"deny": ["rm"]}))
        policy = BashPolicy.from_file(str(policy_file))
        mgr = BashManager(policy=policy, working_dir="/tmp")
        result = mgr.handle({"command": "rm -rf /"})
        assert result["status"] == "error"
        assert "not allowed" in result["message"]

    def test_policy_allows(self, tmp_path):
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"allow": ["echo", "ls"]}))
        policy = BashPolicy.from_file(str(policy_file))
        mgr = BashManager(policy=policy, working_dir="/tmp")
        result = mgr.handle({"command": "echo ok"})
        assert result["status"] == "ok"

    def test_working_dir(self, tmp_path):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir=str(tmp_path))
        result = mgr.handle({"command": "pwd"})
        assert result["status"] == "ok"
        assert str(tmp_path) in result["stdout"]

    def test_working_dir_empty_string_defaults_to_agent_dir(self, tmp_path):
        # An empty-string working_dir is treated as unset and runs in the
        # agent working directory rather than failing the sandbox check.
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir=str(tmp_path))
        result = mgr.handle({"command": "pwd", "working_dir": ""})
        assert result["status"] == "ok"
        assert str(tmp_path) in result["stdout"]

    def test_working_dir_whitespace_only_defaults_to_agent_dir(self, tmp_path):
        # Whitespace-only working_dir is also treated as unset.
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir=str(tmp_path))
        result = mgr.handle({"command": "pwd", "working_dir": "   "})
        assert result["status"] == "ok"
        assert str(tmp_path) in result["stdout"]

    def test_working_dir_outside_sandbox_error_suggests_cd_workaround(self, tmp_path):
        sandbox = tmp_path / "agent"
        external = tmp_path / "external"
        sandbox.mkdir()
        external.mkdir()
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir=str(sandbox))

        result = mgr.handle({"command": "pwd", "working_dir": str(external)})

        assert result["status"] == "error"
        assert "under agent working directory" in result["message"]
        assert "cd " in result["message"]
        assert str(external.resolve()) in result["message"]

    def test_schema_documents_working_dir_sandbox_and_cd_workaround(self):
        desc = get_schema("en")["properties"]["working_dir"]["description"]

        assert "agent working directory sandbox" in desc
        assert "paths outside" in desc
        assert "cd /absolute/path &&" in desc

    def test_output_truncation(self):
        mgr = BashManager(policy=BashPolicy.yolo(), working_dir="/tmp", max_output=20)
        result = mgr.handle({"command": "echo 'a very long output string that exceeds the limit'"})
        assert "truncated" in result["stdout"]


# ---------------------------------------------------------------------------
# Result-fidelity helpers (unit-level, no subprocess)
# ---------------------------------------------------------------------------

class TestAugmentCommandResult:
    def test_success(self):
        r = _augment_command_result({"status": "done", "exit_code": 0, "stdout": "", "stderr": ""})
        assert r["ok"] is True
        assert r["command_status"] == "success"
        assert "warning" not in r

    def test_failure_has_warning(self):
        r = _augment_command_result(
            {"status": "done", "exit_code": 2, "stdout": "", "stderr": "bad arg\n"}
        )
        assert r["ok"] is False
        assert r["command_status"] == "failed"
        assert "exited with code 2" in r["warning"]
        assert "bad arg" in r["warning"]

    def test_long_stderr_tail_is_truncated_with_ellipsis(self):
        big = "E" * 5000
        r = _augment_command_result(
            {"status": "done", "exit_code": 1, "stdout": "", "stderr": big}
        )
        assert r["warning"].startswith("command exited with code 1")
        assert "…" in r["warning"]
        # The warning carries only a bounded tail, not the whole 5000 chars.
        assert len(r["warning"]) < 1200

    def test_missing_exit_code_is_left_untouched(self):
        # poll on a still-running job has no exit_code yet → no fidelity fields.
        r = _augment_command_result({"status": "running", "job_id": "x"})
        assert "ok" not in r
        assert "command_status" not in r

    def test_warning_tail_redacts_secret_shaped_stderr(self):
        # A secret-shaped token in the stderr tail must not be hoisted verbatim
        # into the model-visible `warning`; the raw `stderr` field is unchanged.
        token = "ghp_" + "a" * 36  # GitHub PAT shape the kernel redactor catches
        stderr = f"fatal: auth failed using token {token}\n"
        r = _augment_command_result(
            {"status": "done", "exit_code": 1, "stdout": "", "stderr": stderr}
        )
        assert token not in r["warning"]
        assert "<REDACTED:github_token>" in r["warning"]
        # Raw stderr is mirrored verbatim — only the warning tail is redacted.
        assert token in r["stderr"]

    def test_suspicious_zero_exit_warning_wording(self):
        # A zero exit whose output carries a traceback signature is flagged as a
        # suspicious success — the wording must say so, not claim a failure.
        r = _augment_command_result(
            {
                "status": "done",
                "exit_code": 0,
                "stdout": "Traceback (most recent call last):\n  File ...",
                "stderr": "",
            }
        )
        assert r["ok"] is True
        assert r["command_status"] == "success"
        assert "exited 0 but output contains" in r["warning"]
        assert "python_traceback" in r["warning"]


class TestRedactWarningTail:
    def test_redacts_known_token_shape(self):
        token = "ghp_" + "b" * 36
        out = _redact_warning_tail(f"using {token}")
        assert token not in out
        assert "<REDACTED:github_token>" in out

    def test_passes_through_ordinary_text(self):
        text = "command exited with code 1; bad argument"
        assert _redact_warning_tail(text) == text

    def test_fail_open_returns_input_when_redactor_unavailable(self, monkeypatch):
        # If the kernel redactor import fails, the tail is returned unchanged
        # rather than breaking the bash result (raw stderr already mirrors it).
        import builtins

        real_import = builtins.__import__

        def boom(name, *args, **kwargs):
            if name == "lingtai_kernel.trace_redaction":
                raise ImportError("simulated missing redactor")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", boom)
        text = "some stderr with sk-" + "x" * 30
        assert _redact_warning_tail(text) == text


class TestDetectFailureSignature:
    def test_traceback(self):
        assert (
            _detect_failure_signature("", "Traceback (most recent call last):\n  ...")
            == "python_traceback"
        )

    def test_missing_module(self):
        assert (
            _detect_failure_signature("", "ModuleNotFoundError: No module named 'lingtai'")
            == "missing_module"
        )

    def test_clean(self):
        assert _detect_failure_signature("all good", "") is None


class TestBroadScanHint:
    def test_find_with_name(self):
        assert _broad_scan_hint("find /work -name '*.py'") is not None

    def test_rglob(self):
        assert _broad_scan_hint("python3 -c 'list(Path(\".\").rglob(\"*.py\"))'") is not None

    def test_os_walk(self):
        assert _broad_scan_hint("python3 -c 'list(os.walk(\"/work\"))'") is not None

    def test_glob_double_star(self):
        assert _broad_scan_hint("python3 -c \"glob('**/*.py', recursive=True)\"") is not None

    def test_plain_command_not_flagged(self):
        assert _broad_scan_hint("ls -la") is None
        assert _broad_scan_hint("git status") is None
        assert _broad_scan_hint("find . -maxdepth 1") is None


# ---------------------------------------------------------------------------
# setup()
# ---------------------------------------------------------------------------

class TestSetupBash:
    def test_setup_with_policy_file(self, tmp_path):
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"allow": ["echo"]}))
        agent = MagicMock()
        agent._working_dir = Path("/tmp")
        mgr = setup_bash(agent, policy_file=str(policy_file))
        assert isinstance(mgr, BashManager)
        agent.add_tool.assert_called_once()

    def test_setup_yolo(self):
        agent = MagicMock()
        agent._working_dir = Path("/tmp")
        mgr = setup_bash(agent, yolo=True)
        assert isinstance(mgr, BashManager)
        agent.add_tool.assert_called_once()

    def test_setup_uses_default_policy_when_none_specified(self):
        agent = MagicMock()
        agent._working_dir = Path("/tmp")
        agent._config.bash_policy_file = None  # no config fallback
        # Should succeed — falls back to bundled default policy
        setup_bash(agent)
        agent.add_tool.assert_called_once()


# ---------------------------------------------------------------------------
# add_capability integration
# ---------------------------------------------------------------------------

class TestAddCapability:
    def test_add_capability_bash_yolo(self, tmp_path):
        from lingtai.agent import Agent
        svc = MagicMock()
        svc.get_adapter.return_value = MagicMock()
        svc.provider = "gemini"
        svc.model = "gemini-test"
        agent = Agent(service=svc, agent_name="test", working_dir=tmp_path / "test",
                           capabilities={"bash": {"yolo": True}})
        mgr = agent.get_capability("bash")
        assert isinstance(mgr, BashManager)
        assert "bash" in agent._tool_handlers

    def test_add_capability_bash_with_policy(self, tmp_path):
        from lingtai.agent import Agent
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"allow": ["echo"]}))
        svc = MagicMock()
        svc.get_adapter.return_value = MagicMock()
        svc.provider = "gemini"
        svc.model = "gemini-test"
        agent = Agent(service=svc, agent_name="test", working_dir=tmp_path / "test",
                           capabilities={"bash": {"policy_file": str(policy_file)}})
        mgr = agent.get_capability("bash")
        assert isinstance(mgr, BashManager)
