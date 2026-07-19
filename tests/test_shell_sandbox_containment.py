"""Nested-sandbox containment for the shell ``working_dir`` check.

The containment helper decides whether a resolved cwd is inside the agent
sandbox using the live platform ``os.sep`` as the boundary. On POSIX
``Path.resolve()`` yields ``/``-joined strings; on Windows it yields
``\\``-joined strings, so a hardcoded ``"/"`` boundary would reject every
legitimate nested Windows cwd. These tests pin both the POSIX end-to-end
behavior and — by monkeypatching ``os.sep`` — the platform-parametric helper
logic, so the Windows separator path is proven on POSIX CI too.
"""
from __future__ import annotations

import os

import pytest

from lingtai.tools.bash import BashManager, BashPolicy, _working_dir_contained


class _SyncOnlyAgent:
    """Stand-in agent for synchronous manager runs."""


# ---------------------------------------------------------------------------
# Direct unit tests of the module-level helper
# ---------------------------------------------------------------------------

def test_helper_accepts_sandbox_itself_and_nested_posix():
    assert _working_dir_contained("/a/b", "/a/b") is True
    assert _working_dir_contained("/a/b/c", "/a/b") is True
    assert _working_dir_contained("/a/b/c/d", "/a/b") is True


def test_helper_rejects_sibling_prefix_and_outside_posix():
    # Sibling-prefix escape: /a/bb must NOT be treated as nested under /a/b.
    assert _working_dir_contained("/a/bb", "/a/b") is False
    assert _working_dir_contained("/a/b-sibling", "/a/b") is False
    assert _working_dir_contained("/other", "/a/b") is False
    # A bare prefix that is not separated is also rejected.
    assert _working_dir_contained("/a/bc", "/a/b") is False


def test_helper_reads_os_sep_at_call_time_windows(monkeypatch):
    """With os.sep patched to '\\\\', backslash-nested paths are accepted and
    sibling-prefix paths are rejected — exactly mirroring the POSIX case for
    Windows-shaped resolve() output."""
    monkeypatch.setattr(os, "sep", "\\")
    sandbox = r"C:\agents\abc"
    # Nested under the sandbox with a backslash boundary → accepted.
    assert _working_dir_contained(sandbox, sandbox) is True
    assert _working_dir_contained(r"C:\agents\abc\sub", sandbox) is True
    assert _working_dir_contained(r"C:\agents\abc\sub\deep", sandbox) is True
    # Sibling-prefix escape with a backslash separator → rejected.
    assert _working_dir_contained(r"C:\agents\abcd", sandbox) is False
    assert _working_dir_contained(r"C:\agents\abc-sibling", sandbox) is False
    # And a forward-slash boundary is NOT accepted while os.sep is backslash,
    # proving the separator is genuinely read at call time.
    assert _working_dir_contained(r"C:\agents\abc/sub", sandbox) is False


# ---------------------------------------------------------------------------
# POSIX end-to-end through the manager (skips on native Windows where the
# real os.sep is '\\' and these forward-slash paths would not resolve nested).
# ---------------------------------------------------------------------------

posix_paths = pytest.mark.skipif(
    os.name == "nt", reason="POSIX '/'-separator working_dir semantics"
)


@posix_paths
def test_nested_working_dir_is_accepted(tmp_path):
    sandbox = tmp_path / "agent"
    nested = sandbox / "sub" / "deep"
    nested.mkdir(parents=True)
    mgr = BashManager(agent=_SyncOnlyAgent(), policy=BashPolicy.yolo(), working_dir=str(sandbox))
    result = mgr.handle({"command": "pwd", "working_dir": str(nested)})
    assert result["status"] == "ok"
    assert str(nested.resolve()) in result["stdout"]


@posix_paths
def test_sibling_prefix_dir_is_rejected(tmp_path):
    # sandbox /.../agent-b vs sibling /.../agent-bb — the sibling shares the
    # sandbox string as a prefix but is not nested, and must be refused.
    sandbox = tmp_path / "agent-b"
    sibling = tmp_path / "agent-bb"
    sandbox.mkdir()
    sibling.mkdir()
    mgr = BashManager(agent=_SyncOnlyAgent(), policy=BashPolicy.yolo(), working_dir=str(sandbox))
    result = mgr.handle({"command": "pwd", "working_dir": str(sibling)})
    assert result["status"] == "error"
    assert "under agent working directory" in result["message"]
