"""POSIX-only platform policy for the workdir lease.

This slice ships a POSIX ``flock`` adapter and no Windows production adapter. The
outer selector must fail loudly on an unsupported platform rather than silently
degrade, and no ``msvcrt`` import or Windows lease adapter may remain in the
selected implementation.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


def test_selector_fails_loudly_on_unsupported_platform(monkeypatch, tmp_path):
    import lingtai.adapters.workdir_lease as sel

    monkeypatch.setattr(sel.sys, "platform", "win32")
    with pytest.raises(NotImplementedError) as exc:
        sel.select_workdir_lease(tmp_path)
    message = str(exc.value)
    assert "win32" in message
    assert "POSIX" in message


def test_no_windows_production_lease_adapter_exists():
    repo_root = Path(__file__).resolve().parents[1]
    windows_adapter = repo_root / "src" / "lingtai" / "adapters" / "windows" / "workdir_lease.py"
    assert not windows_adapter.exists(), (
        "This POSIX-only slice must not ship a Windows production lease adapter"
    )


def test_no_msvcrt_import_in_lease_implementation():
    repo_root = Path(__file__).resolve().parents[1]
    files = [
        repo_root / "src" / "lingtai" / "kernel" / "workdir_lease" / "__init__.py",
        repo_root / "src" / "lingtai" / "kernel" / "workdir.py",
        repo_root / "src" / "lingtai" / "adapters" / "workdir_lease.py",
        repo_root / "src" / "lingtai" / "adapters" / "posix" / "workdir_lease.py",
    ]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "msvcrt", f"{path}: import msvcrt"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "msvcrt", f"{path}: from msvcrt ..."
        # No textual msvcrt reference either (belt and braces).
        assert "msvcrt" not in path.read_text(encoding="utf-8"), path


def test_posix_adapter_uses_fcntl_flock_faithfully():
    """The POSIX adapter is the real mechanism — it uses ``fcntl.flock``."""
    repo_root = Path(__file__).resolve().parents[1]
    adapter = repo_root / "src" / "lingtai" / "adapters" / "posix" / "workdir_lease.py"
    text = adapter.read_text(encoding="utf-8")
    assert "import fcntl" in text
    assert "fcntl.flock" in text
    assert "LOCK_EX" in text and "LOCK_NB" in text and "LOCK_UN" in text


def test_posix_facade_does_not_eagerly_import_fcntl():
    """The ``lingtai.adapters.posix`` facade must not import the ``fcntl`` lease
    adapter at package-import time (the export is lazy via ``__getattr__``), or
    importing the package or a portable sibling would fail without ``fcntl``
    before the selector can raise its explicit error."""
    repo_root = Path(__file__).resolve().parents[1]
    facade = repo_root / "src" / "lingtai" / "adapters" / "posix" / "__init__.py"
    for node in ast.parse(facade.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom):
            assert "workdir_lease" not in (node.module or ""), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "workdir_lease" not in alias.name, alias.name


def test_import_posix_facade_and_selector_survive_missing_fcntl(monkeypatch):
    """With ``fcntl`` unavailable (simulated non-POSIX), importing the POSIX
    package and a portable sibling still works, and ``select_workdir_lease`` raises
    its explicit ``NotImplementedError`` — never a bare ``fcntl`` import error."""
    import builtins
    import importlib

    real_import = builtins.__import__

    def _no_fcntl(name, *args, **kwargs):
        if name == "fcntl" or name.startswith("fcntl."):
            raise ModuleNotFoundError("No module named 'fcntl' (simulated Windows)")
        return real_import(name, *args, **kwargs)

    for mod_name in list(sys.modules):
        if "adapters.posix.workdir_lease" in mod_name or mod_name == "fcntl":
            monkeypatch.delitem(sys.modules, mod_name, raising=False)
    monkeypatch.setattr(builtins, "__import__", _no_fcntl)

    importlib.reload(importlib.import_module("lingtai.adapters.posix"))  # facade: no fcntl
    importlib.import_module("lingtai.adapters.posix.event_journal")  # portable sibling: no fcntl

    import lingtai.adapters.workdir_lease as sel

    monkeypatch.setattr(sel.sys, "platform", "win32")
    with pytest.raises(NotImplementedError) as exc:
        sel.select_workdir_lease("/tmp/does-not-matter")
    assert "win32" in str(exc.value) and "POSIX" in str(exc.value)
