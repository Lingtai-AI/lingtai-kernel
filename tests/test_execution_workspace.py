"""Focused execution-workspace rooting and isolation tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.kernel.execution_workspace import (
    ExecutionWorkspace,
    bind_execution_workspace,
    current_execution_workspace,
    reset_execution_workspace,
)
from lingtai.kernel.risky_action_gate import build_risky_action_check
from lingtai.kernel.tool_call_guard import ToolProposal
from lingtai.tools.bash import ShellManager, ShellPolicy


def test_execution_workspace_canonicalizes_and_requires_existing_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workspace, target_is_directory=True)

    assert ExecutionWorkspace(alias).root == workspace.resolve()
    with pytest.raises(ValueError, match="existing directory"):
        ExecutionWorkspace(tmp_path / "missing")
    not_directory = tmp_path / "file"
    not_directory.write_text("x")
    with pytest.raises(ValueError, match="existing directory"):
        ExecutionWorkspace(not_directory)


def test_non_workspace_shell_keeps_historical_agent_root(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    outside = tmp_path / "outside"
    agent_dir.mkdir()
    outside.mkdir()

    shell = ShellManager(ShellPolicy.yolo(), str(agent_dir), rehydrate=False)
    escaped = shell.handle({
        "action": "run", "command": "pwd", "working_dir": "../outside"
    })
    assert escaped["status"] == "error"
    assert "must be under agent working directory" in escaped["message"]


def test_shell_uses_canonical_workspace_outside_agent_dir(tmp_path: Path):
    process_cwd = Path.cwd()
    agent_dir = tmp_path / "agent"
    workspace = tmp_path / "project"
    agent_dir.mkdir()
    workspace.mkdir()
    token = bind_execution_workspace(ExecutionWorkspace(workspace.resolve()))
    try:
        shell = ShellManager(ShellPolicy.yolo(), str(agent_dir), rehydrate=False)
        result = shell.handle({"action": "run", "command": "pwd"})
        assert result["status"] == "ok"
        assert Path(result["stdout"].strip()).resolve() == workspace.resolve()
        written = shell.handle({
            "action": "run", "command": "mkdir -p src && printf ok > src/a.txt"
        })
        assert written["status"] == "ok"
        assert (workspace / "src/a.txt").read_text() == "ok"
        (workspace / "subdir").mkdir()
        relative = shell.handle({
            "action": "run", "command": "pwd", "working_dir": "subdir"
        })
        assert Path(relative["stdout"].strip()).resolve() == (workspace / "subdir").resolve()
        assert shell.handle({
            "action": "run", "command": "pwd", "working_dir": "../outside"
        })["message"] == "Invalid working_dir path"
    finally:
        reset_execution_workspace(token)

    assert current_execution_workspace() is None
    assert Path.cwd() == process_cwd
    assert not (agent_dir / "src/a.txt").exists()


def test_risky_action_guard_rejects_shell_working_dir_escape_from_workspace(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    workspace = tmp_path / "workspace"
    (agent_dir / ".security").mkdir(parents=True)
    workspace.mkdir()
    (agent_dir / ".security/gate_config.json").write_text(json.dumps({
        "local_write_roots": [str(workspace)],
    }))
    check = build_risky_action_check(agent_dir)
    token = bind_execution_workspace(ExecutionWorkspace(workspace.resolve()))
    try:
        inside_shell = check(ToolProposal(
            tool_name="shell",
            tool_args={
                "action": "run",
                "input": {"command": "pwd", "working_dir": "nested"},
            },
        ))
        escaped_shell = check(ToolProposal(
            tool_name="shell",
            tool_args={
                "action": "run",
                "input": {"command": "pwd", "working_dir": "../outside"},
            },
        ))
    finally:
        reset_execution_workspace(token)
    assert inside_shell.allowed
    assert not escaped_shell.allowed
    assert escaped_shell.reason == "shell working_dir escapes execution workspace"
    assert "pending_request_id" not in escaped_shell.metadata
    assert not (agent_dir / ".security/pending").exists()
