"""Focused tests for Shell execution-context anchoring (issue 991).

Covers the two repaired defects:

* R1 — every relative (and empty) PATH search entry must be anchored to the
  supplied *execution* cwd (``execvp`` semantics), never to the Python
  process cwd that happens to run the check.
* R2 — ``ShellManager._handle_run`` must resolve the actual final execution
  cwd (explicit ``working_dir``, else the bound execution workspace root,
  else the manager workdir) *before* policy validation and share that one
  cwd with both the policy gate and dispatch.

All executable identities are inert ``#!/bin/sh`` scratch stubs under
``tmp_path`` (they exit 0 and are never invoked); no denied payload such as
``rm``/``format`` is ever executed.  The R2 manager tests synthesize the
dispatch recorder (``m._run_sync = ...``) so no child process is ever
spawned.  Scratch files — including the case-probe marker — are preserved
(no-deletion discipline).
"""
from __future__ import annotations

import os
import stat
from types import SimpleNamespace

from lingtai.kernel.execution_workspace import (
    ExecutionWorkspace,
    bind_execution_workspace,
    reset_execution_workspace,
)
from lingtai.tools.bash import (
    _canonical_stored_name,
    _resolve_executable_token,
    BashManager,
    BashPolicy,
)
from tests._notification_store_helpers import notification_store_for

_STUB_BODY = "#!/bin/sh\nexit 0\n"


def _inert_stub(directory, name: str) -> str:
    """Create an executable but inert stub and return its path."""
    path = directory / name
    path.write_text(_STUB_BODY, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _manager(tmp_path, policy):
    return BashManager(
        policy=policy,
        working_dir=str(tmp_path),
        agent=SimpleNamespace(_notification_store=notification_store_for(tmp_path)),
    )


def _dir_is_case_insensitive(directory) -> bool:
    """Probe the real filesystem case semantics of *directory*.

    The marker file is preserved on disk (no-deletion discipline): it is
    inert scratch content under ``tmp_path``, and re-writing it on a repeat
    probe in the same directory is harmless idempotent overwrite."""
    probe = directory / "caseprobe_marker"
    probe.write_text("x", encoding="utf-8")
    return os.path.exists(os.path.join(str(directory), "CASEPROBE_MARKER"))


# --------------------------------------------------------------------------
# R1: relative/empty PATH entries anchor to the supplied execution cwd.
# --------------------------------------------------------------------------


def test_relative_path_entry_anchors_to_execution_cwd_not_process_cwd(tmp_path):
    """A nonempty relative PATH entry must join against the supplied command
    cwd, even though the checking Python process runs somewhere else."""
    command_cwd = tmp_path / "command_cwd"
    command_cwd.mkdir()
    bindir = command_cwd / "bin"
    bindir.mkdir()
    stub = _inert_stub(bindir, "ktlsh")

    assert os.getcwd() != str(command_cwd)  # the defect's precondition
    resolved = _resolve_executable_token("ktlsh", cwd=str(command_cwd), path="bin")
    assert resolved == os.path.normpath(os.path.join(str(command_cwd), "bin", "ktlsh"))
    assert os.path.dirname(resolved) == os.path.dirname(stub)

    # The identical search against a different cwd must not find this file.
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()
    assert _resolve_executable_token("ktlsh", cwd=str(other_cwd), path="bin") is None


def test_relative_path_identity_denial_matches_absolute_path_denial(tmp_path):
    """Red→green for the reproduced defect: with PATH=bin the uppercase
    inert stub is denied exactly like the absolute-PATH spelling on a
    case-insensitive filesystem, and keeps its historical allow where the
    filesystem is case-sensitive."""
    command_cwd = tmp_path / "command_cwd"
    command_cwd.mkdir()
    bindir = command_cwd / "bin"
    bindir.mkdir()
    stub = _inert_stub(bindir, "ktlsh")
    policy = BashPolicy(deny=["ktlsh"])

    absolute = policy.is_allowed(
        "KTLSH --version", cwd=str(command_cwd), path=str(bindir)
    )
    relative = policy.is_allowed("KTLSH --version", cwd=str(command_cwd), path="bin")
    if _dir_is_case_insensitive(bindir):
        assert absolute is False  # absolute PATH blocks the uppercase inert file
        assert relative is False  # fixed: relative PATH must agree
        assert _canonical_stored_name(stub) == "ktlsh"
    else:
        # Case-sensitive filesystem: KTLSH is a distinct (missing) program
        # and both spellings keep the historical allow.
        assert absolute is True
        assert relative is True


def test_empty_path_entry_means_execution_cwd(tmp_path):
    """An empty PATH component (leading/trailing/doubled colon) is the
    execution cwd itself, anchored the same way."""
    command_cwd = tmp_path / "command_cwd"
    command_cwd.mkdir()
    stub = _inert_stub(command_cwd, "ktlsh")

    for search in ("", os.pathsep, os.pathsep + os.pathsep):
        resolved = _resolve_executable_token("ktlsh", cwd=str(command_cwd), path=search)
        assert resolved == os.path.normpath(os.path.join(str(command_cwd), "ktlsh"))

    policy = BashPolicy(deny=["ktlsh"])
    if _dir_is_case_insensitive(command_cwd):
        assert (
            policy.is_allowed("KTLSH --version", cwd=str(command_cwd), path="")
            is False
        )


def test_path_ordering_first_executable_and_skipping_rules(tmp_path):
    """PATH ordering picks the first executable; missing and non-executable
    candidates are skipped without changing the search order."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_stub = _inert_stub(first, "ktlsh")
    second_stub = _inert_stub(second, "ktlsh")
    # A non-executable same-name file must not win over a later executable.
    decoy_dir = tmp_path / "decoy"
    decoy_dir.mkdir()
    (decoy_dir / "ktlsh").write_text(_STUB_BODY, encoding="utf-8")

    search = os.pathsep.join([str(decoy_dir), str(first), str(second)])
    assert _resolve_executable_token("ktlsh", cwd=str(tmp_path), path=search) == (
        os.path.normpath(first_stub)
    )
    search = os.pathsep.join([str(first), str(second)])
    assert _resolve_executable_token("ktlsh", cwd=str(tmp_path), path=search) == (
        os.path.normpath(first_stub)
    )
    search = os.pathsep.join([str(second)])
    assert _resolve_executable_token("ktlsh", cwd=str(tmp_path), path=search) == (
        os.path.normpath(second_stub)
    )
    # Missing everywhere: no identity, conservative None.
    assert _resolve_executable_token("ghostcmd", cwd=str(tmp_path), path=search) is None


def test_manager_gate_uses_execution_cwd_for_relative_path(tmp_path, monkeypatch):
    """Through the manager validation entry the relative PATH entry is still
    anchored to the passed command cwd, not the process cwd."""
    command_cwd = tmp_path / "command_cwd"
    command_cwd.mkdir()
    bindir = command_cwd / "bin"
    bindir.mkdir()
    _inert_stub(bindir, "ktlsh")
    manager = _manager(tmp_path, BashPolicy(deny=["ktlsh"]))
    monkeypatch.setenv("PATH", "bin")

    verdict = manager._validate_command("KTLSH --version", cwd=str(command_cwd))
    if _dir_is_case_insensitive(bindir):
        assert verdict["status"] == "error"
        assert "KTLSH" in verdict["message"]
    else:
        assert verdict is None
    # Relative entry anchored to a cwd that lacks bin/: nothing resolves.
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()
    assert manager._validate_command("KTLSH --version", cwd=str(other_cwd)) is None


# --------------------------------------------------------------------------
# R2: _handle_run shares one resolved execution cwd between gate & dispatch.
# --------------------------------------------------------------------------


def _recording_manager(tmp_path, policy, working_dir):
    manager = BashManager(
        policy=policy,
        working_dir=str(working_dir),
        agent=SimpleNamespace(_notification_store=notification_store_for(tmp_path)),
        rehydrate=False,
    )
    dispatches = []
    manager._run_sync = (
        lambda command, cwd, timeout, invocation: dispatches.append(
            {"command": command, "cwd": cwd}
        )
        or {"status": "recorded", "cwd": cwd}
    )
    return manager, dispatches


def test_handle_run_denies_workspace_case_variant_before_dispatch(tmp_path):
    """Red→green for the reproduced defect: with a bound execution workspace
    the pure workspace gate denies ./KTLSH and _handle_run must now agree —
    policy validation uses the workspace cwd, so dispatch is never reached."""
    root = tmp_path / "root"
    root.mkdir()
    workspace_dir = root / "workspace"
    workspace_dir.mkdir()
    _inert_stub(workspace_dir, "ktlsh")
    policy = BashPolicy(deny=["ktlsh"])
    manager, dispatches = _recording_manager(tmp_path, policy, root)

    gate = policy.is_allowed("./KTLSH", cwd=str(workspace_dir), path="")
    binding = bind_execution_workspace(ExecutionWorkspace(workspace_dir))
    try:
        result = manager._handle_run({"command": "./KTLSH"})
    finally:
        reset_execution_workspace(binding)

    if _dir_is_case_insensitive(workspace_dir):
        assert gate is False
        assert result["status"] == "error"
        assert "KTLSH" in result["message"]
        assert dispatches == []  # the denied payload never reaches dispatch
    else:
        # Case-sensitive host: distinct missing program, dispatch proceeds
        # with exactly the workspace cwd.
        assert gate is True
        assert result["status"] == "recorded"
        assert dispatches == [{"command": "./KTLSH", "cwd": str(workspace_dir.resolve())}]


def test_handle_run_explicit_relative_working_dir_resolved_by_workspace(tmp_path):
    """An explicit relative working_dir is resolved against the bound
    workspace and the *resolved* cwd is what dispatch receives."""
    root = tmp_path / "root"
    root.mkdir()
    workspace_dir = root / "workspace"
    subdir = workspace_dir / "subdir"
    subdir.mkdir(parents=True)
    manager, dispatches = _recording_manager(tmp_path, BashPolicy.yolo(), root)

    binding = bind_execution_workspace(ExecutionWorkspace(workspace_dir))
    try:
        result = manager._handle_run({"command": "echo ok", "working_dir": "subdir"})
    finally:
        reset_execution_workspace(binding)

    assert result["status"] == "recorded"
    expected = str((workspace_dir / "subdir").resolve())
    assert dispatches == [{"command": "echo ok", "cwd": expected}]


def test_handle_run_default_cwd_without_workspace_is_manager_workdir(tmp_path):
    """No workspace bound: default execution cwd stays the manager workdir
    for both validation and dispatch."""
    manager, dispatches = _recording_manager(tmp_path, BashPolicy.yolo(), tmp_path)
    result = manager._handle_run({"command": "echo ok"})
    assert result["status"] == "recorded"
    assert dispatches == [{"command": "echo ok", "cwd": str(tmp_path)}]


def test_handle_run_safe_invalid_cwd_error_never_dispatches(tmp_path):
    """An escaping explicit working_dir under a bound workspace is refused
    with the established error before any dispatch."""
    root = tmp_path / "root"
    root.mkdir()
    workspace_dir = root / "workspace"
    workspace_dir.mkdir()
    manager, dispatches = _recording_manager(tmp_path, BashPolicy.yolo(), root)

    binding = bind_execution_workspace(ExecutionWorkspace(workspace_dir))
    try:
        result = manager._handle_run({"command": "echo ok", "working_dir": "../escape"})
    finally:
        reset_execution_workspace(binding)

    assert result["status"] == "error"
    assert "Invalid working_dir" in result["message"]
    assert dispatches == []
