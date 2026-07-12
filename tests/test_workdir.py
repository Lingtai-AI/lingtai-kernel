"""Tests for WorkingDir — filesystem, locking, git, manifest."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lingtai.kernel.workdir import WorkingDir, WorkdirLayout, workdir_layout


class TestWorkdirLayout:
    """Lock down the exact relative names the layout helper hands out.

    These are the agent-workdir filesystem protocol — separate processes read
    and write them by name — so each property is pinned to an exact path. A
    silent rename here would break mail delivery, notification sync, handshake,
    and spill recovery; the assertions make any drift fail loudly.
    """

    def test_returns_frozen_layout_rooted_at_path(self, tmp_path):
        layout = workdir_layout(tmp_path)
        assert isinstance(layout, WorkdirLayout)
        assert layout.root == tmp_path
        with pytest.raises(Exception):
            layout.root = tmp_path / "other"  # frozen dataclass

    def test_accepts_str_path(self, tmp_path):
        assert workdir_layout(str(tmp_path)).root == tmp_path

    def test_agent_protocol_files(self, tmp_path):
        layout = workdir_layout(tmp_path)
        assert layout.agent_lock == tmp_path / ".agent.lock"
        assert layout.agent_manifest == tmp_path / ".agent.json"
        assert layout.agent_manifest_corrupt == tmp_path / ".agent.json.corrupt"
        assert layout.heartbeat == tmp_path / ".agent.heartbeat"
        assert layout.status_json == tmp_path / ".status.json"
        assert layout.init_json == tmp_path / "init.json"

    def test_directories(self, tmp_path):
        layout = workdir_layout(tmp_path)
        assert layout.system_dir == tmp_path / "system"
        assert layout.logs_dir == tmp_path / "logs"
        assert layout.history_dir == tmp_path / "history"
        assert layout.notification_dir == tmp_path / ".notification"
        assert layout.tool_results_dir == tmp_path / "tmp" / "tool-results"

    def test_derived_files(self, tmp_path):
        layout = workdir_layout(tmp_path)
        assert layout.chat_history == tmp_path / "history" / "chat_history.jsonl"
        assert layout.resolved_manifest == tmp_path / "system" / "manifest.resolved.json"
        assert layout.resolved_manifest_tmp == tmp_path / "system" / "manifest.resolved.json.tmp"

    def test_notification_file(self, tmp_path):
        layout = workdir_layout(tmp_path)
        assert layout.notification_file("email") == tmp_path / ".notification" / "email.json"
        assert layout.notification_file("system") == tmp_path / ".notification" / "system.json"
        assert layout.notification_file("mcp.telegram") == tmp_path / ".notification" / "mcp.telegram.json"

    def test_system_file(self, tmp_path):
        layout = workdir_layout(tmp_path)
        assert layout.system_file("covenant.md") == tmp_path / "system" / "covenant.md"


def test_workdir_accepts_path(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    assert wd.path == tmp_path / "myagent"
    assert wd.path.is_dir()


def test_workdir_creates_parents(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "deep" / "nested" / "agent")
    assert wd.path == tmp_path / "deep" / "nested" / "agent"
    assert wd.path.is_dir()


# The exclusive working-directory lock moved out of WorkingDir to the Core-owned
# WorkdirLeasePort and its POSIX flock adapter. Collision, release/reuse, timeout,
# and expiry are now pinned in tests/test_workdir_lease.py against both the
# production adapter and the deterministic fake.


def test_git_init_creates_repo(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    wd.init_git()
    assert (wd.path / ".git").is_dir()
    assert (wd.path / ".gitignore").is_file()
    assert (wd.path / "system" / "covenant.md").is_file()
    assert (wd.path / "system" / "pad.md").is_file()


def test_git_init_skips_if_already_initialized(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    wd.init_git()
    result1 = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=wd.path, capture_output=True, text=True,
    )
    wd.init_git()  # second call — should be no-op
    result2 = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=wd.path, capture_output=True, text=True,
    )
    assert result1.stdout.strip() == result2.stdout.strip()


def test_read_manifest_returns_empty_when_missing(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    assert wd.read_manifest() == ""


def test_write_and_read_manifest(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    manifest = {"address": "/agents/a1b2c3d4e5f6", "covenant": "researcher", "started_at": "2026-01-01T00:00:00Z"}
    wd.write_manifest(manifest)
    covenant = wd.read_manifest()
    assert covenant == "researcher"


def test_diff_and_commit(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    wd.init_git()
    # Write to tracked file
    pad_file = wd.path / "system" / "pad.md"
    pad_file.write_text("hello world")
    diff_text, commit_hash = wd.diff_and_commit("system/pad.md", "pad")
    assert commit_hash is not None
    assert diff_text  # should have some diff content


def test_diff_and_commit_no_changes(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    wd.init_git()
    diff_text, commit_hash = wd.diff_and_commit("system/pad.md", "pad")
    assert diff_text is None
    assert commit_hash is None


def test_diff_read_only(tmp_path):
    wd = WorkingDir(working_dir=tmp_path / "myagent")
    wd.init_git()
    pad_file = wd.path / "system" / "pad.md"
    pad_file.write_text("new content")
    result = wd.diff("system/pad.md")
    assert isinstance(result, str)
    # Should not commit — file should still show as changed
    status = subprocess.run(
        ["git", "status", "--porcelain", "system/pad.md"],
        cwd=wd.path, capture_output=True, text=True,
    )
    assert status.stdout.strip()  # still dirty
