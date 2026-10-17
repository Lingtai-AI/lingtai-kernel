"""Focused tests for executable/filesystem case-identity deny semantics (issue 991).

Policy checks here are parsed strings and pure gates only. Every executable
identity is an inert scratch stub created under ``tmp_path`` (a ``#!/bin/sh``
script that exits 0); no denied payload such as ``rm``/``format`` is ever
executed, and case-sensitive behavior the host filesystem cannot provide is
covered by a deterministic, explicitly labelled simulation.
"""
from __future__ import annotations

import os
import stat
from types import SimpleNamespace

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

    The marker file is preserved on disk (no-deletion discipline): it is inert
    scratch content under ``tmp_path``, and re-writing it on a repeat probe in
    the same directory is harmless idempotent overwrite."""
    probe = directory / "caseprobe_marker"
    probe.write_text("x", encoding="utf-8")
    return os.path.exists(os.path.join(str(directory), "CASEPROBE_MARKER"))


def test_real_filesystem_decides_case_variant_denial(tmp_path):
    """Old remaining failure: with the real cwd/PATH context, a case-variant
    spelling of an actually-denied executable is blocked on a case-insensitive
    filesystem, and stays allowed where the filesystem is case-sensitive."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _inert_stub(stub_dir, "ktlsh")
    policy = BashPolicy(deny=["ktlsh"])

    context = {"cwd": str(tmp_path), "path": str(stub_dir)}
    if _dir_is_case_insensitive(stub_dir):
        assert policy.is_allowed("ktlsh --version", **context) is False
        assert policy.is_allowed("./ktlsh --version", **context) is False
        assert policy.is_allowed("KTLSH --version", **context) is False
        assert (
            policy.is_allowed(str(stub_dir / "KTLSH") + " --version", **context)
            is False
        )
    else:
        # Case-sensitive filesystem: the variant is a distinct (here missing)
        # executable and must keep the historical allow.
        assert policy.is_allowed("KTLSH --version", **context) is True

    # A genuinely different program name is never swept in by identity.
    _inert_stub(stub_dir, "ktlsh-extra")
    assert policy.is_allowed("ktlsh-extra --version", **context) is True


def test_pure_gate_and_manager_agree_with_real_context(tmp_path, monkeypatch):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _inert_stub(stub_dir, "ktlsh")
    policy = BashPolicy(deny=["ktlsh"])
    manager = _manager(tmp_path, policy)
    command = "KTLSH --version"

    # The manager path threads the real cwd/PATH (here via the host PATH).
    monkeypatch.setenv(
        "PATH", str(stub_dir) + os.pathsep + os.environ.get("PATH", "")
    )
    gate = policy.is_allowed(command, cwd=str(tmp_path))
    verdict = manager._validate_command(command, cwd=str(tmp_path))
    if _dir_is_case_insensitive(stub_dir):
        assert gate is False
        assert verdict["status"] == "error"
        assert "KTLSH" in verdict["message"]
    else:
        assert gate is True
        assert verdict is None

    # Default callers without execution context keep the historical behavior.
    assert policy.is_allowed(command) is True
    assert manager._validate_command(command) is None

    # Yolo is untouched by identity resolution.
    assert BashPolicy.yolo().is_allowed(command, cwd=str(tmp_path)) is True


def test_case_sensitive_distinct_executables_keep_distinction(tmp_path, monkeypatch):
    """Deterministic case-SENSITIVE simulation on hosts (e.g. macOS default)
    whose filesystem cannot hold two case-variant names in one directory: the
    stored file is ``ktlsh`` only, and an ``KTLSH`` token must not be denied.
    The simulation is scoped to the scratch directory; every other path
    delegates to the real filesystem functions."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_path = _inert_stub(stub_dir, "ktlsh")
    policy = BashPolicy(deny=["ktlsh"])

    real_isfile = os.path.isfile
    real_access = os.access
    real_listdir = os.listdir
    stub_dir_str = str(stub_dir)

    def _simulated(candidate: str) -> bool:
        return os.path.dirname(os.path.normpath(candidate)) == stub_dir_str

    def _stored_name(candidate: str):
        base = os.path.basename(candidate)
        try:
            entries = real_listdir(os.path.dirname(candidate) or ".")
        except OSError:
            return None
        # Simulation: only the exact stored spelling exists (case-sensitive FS).
        return base if base in entries else None

    def isfile_cs(candidate):
        if not _simulated(candidate):
            return real_isfile(candidate)
        return _stored_name(candidate) is not None and real_isfile(candidate)

    def access_cs(candidate, mode):
        if not _simulated(candidate):
            return real_access(candidate, mode)
        return _stored_name(candidate) is not None and real_access(candidate, mode)

    def listdir_cs(directory):
        return list(real_listdir(directory))

    monkeypatch.setattr(os.path, "isfile", isfile_cs)
    monkeypatch.setattr(os, "access", access_cs)
    monkeypatch.setattr(os, "listdir", listdir_cs)

    context = {"cwd": str(tmp_path), "path": str(stub_dir)}
    assert policy.is_allowed("ktlsh --version", **context) is False
    assert policy.is_allowed("KTLSH --version", **context) is True
    assert _resolve_executable_token("KTLSH", **context) is None
    assert _canonical_stored_name(stub_path) == "ktlsh"


def test_resolver_failures_are_conservative_and_explicit(tmp_path):
    """Unprovable identity keeps the historical allow (documented semantics:
    a denylist is advisory policy, not a sandbox, and blocking case variants
    of programs that do not exist would change default behavior)."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    non_exec = stub_dir / "ktlsh"
    non_exec.write_text(_STUB_BODY, encoding="utf-8")  # deliberately not +x
    policy = BashPolicy(deny=["ktlsh"])
    context = {"cwd": str(tmp_path), "path": str(stub_dir)}

    # Not executable: no provable identity, and no silent deny either.
    assert _resolve_executable_token("ktlsh", **context) is None
    assert policy.is_allowed("KTLSH --version", **context) is True
    # The exact raw token is still denied by the pre-existing raw rule.
    assert policy.is_allowed("ktlsh --version", **context) is False
    # Unknown name on the search path: nothing to resolve, historical allow.
    assert _resolve_executable_token("ghostcmd", **context) is None
    assert policy.is_allowed("GHOSTCMD --version", **context) is True


def test_exact_path_rules_do_not_widen_and_stay_exact(tmp_path):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = _inert_stub(stub_dir, "ktlsh")
    context = {"cwd": str(tmp_path), "path": str(stub_dir)}

    exact = BashPolicy(deny=[stub])
    assert exact.is_allowed(stub + " --version", **context) is False
    # Identity must not erase the configured full-path rule into a basename rule.
    assert exact.is_allowed("ktlsh --version", **context) is True
    assert exact.is_allowed("./ktlsh --version", **context) is True

    mixed = BashPolicy(deny=["ktlsh", str(tmp_path / "elsewhere" / "other")])
    assert mixed.is_allowed("ktlsh --version", **context) is False
    assert mixed.is_allowed(str(stub_dir / "OTHER") + " --version", **context) is True


def test_allowlist_mode_is_untouched_by_identity_resolution(tmp_path):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _inert_stub(stub_dir, "ktlsh")
    context = {"cwd": str(tmp_path), "path": str(stub_dir)}

    allow = BashPolicy(allow=["ktlsh"])
    assert allow.is_allowed("ktlsh --version", **context) is True
    # Allowlist membership stays exact-token; identity resolution is denylist-only.
    assert allow.is_allowed("KTLSH --version", **context) is False

    allow_exact = BashPolicy(allow=[str(stub_dir / "ktlsh")])
    assert allow_exact.is_allowed("ktlsh --version", **context) is False
    assert allow_exact.is_allowed(str(stub_dir / "ktlsh") + " --version", **context) is True


def test_manager_path_variants_and_pipe_awareness(tmp_path, monkeypatch):
    """End-to-end through the manager validation entry: relative './KTLSH' and
    piped variants are denied exactly like the bare spelling on a
    case-insensitive filesystem; context-free direct calls stay historical."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _inert_stub(stub_dir, "ktlsh")
    # A second stub in the execution cwd itself exercises the relative './' token.
    cwd_stub = _inert_stub(tmp_path, "ktlsh")
    manager = _manager(tmp_path, BashPolicy(deny=["ktlsh"]))
    monkeypatch.setenv(
        "PATH", str(stub_dir) + os.pathsep + os.environ.get("PATH", "")
    )

    if not _dir_is_case_insensitive(stub_dir):
        # Semantics are filesystem-decided; on case-sensitive hosts the
        # variant is a different program and the gate stays open.
        assert manager._validate_command("./KTLSH --version", cwd=str(tmp_path)) is None
        return
    err = manager._validate_command("./KTLSH --version", cwd=str(tmp_path))
    assert err["status"] == "error"
    assert "KTLSH" in err["message"]
    piped = manager._validate_command("echo hi | KTLSH --version", cwd=str(tmp_path))
    assert piped["status"] == "error"
    # Manager direct-call without cwd context keeps historical token-only behavior.
    assert manager._validate_command("./KTLSH --version") is None
