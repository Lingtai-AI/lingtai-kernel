"""Unsupported-platform and lazy-import policy for the POSIX lease selector."""
from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_selector_fails_loudly_on_unsupported_platform(monkeypatch, tmp_path):
    import lingtai.adapters.workdir_lease as selector

    monkeypatch.setattr(selector.sys, "platform", "win32")
    with pytest.raises(NotImplementedError) as exc:
        selector.select_workdir_lease(tmp_path)
    assert "win32" in str(exc.value)
    assert "POSIX" in str(exc.value)


def test_posix_facade_and_selector_survive_missing_fcntl(monkeypatch):
    real_import = builtins.__import__

    def no_fcntl(name, *args, **kwargs):
        if name == "fcntl" or name.startswith("fcntl."):
            raise ModuleNotFoundError("No module named 'fcntl' (simulated Windows)")
        return real_import(name, *args, **kwargs)

    for module_name in list(sys.modules):
        if "adapters.posix.workdir_lease" in module_name or module_name == "fcntl":
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setattr(builtins, "__import__", no_fcntl)

    importlib.reload(importlib.import_module("lingtai.adapters.posix"))
    importlib.import_module("lingtai.adapters.posix.event_journal")
    import lingtai.adapters.workdir_lease as selector

    monkeypatch.setattr(selector.sys, "platform", "win32")
    with pytest.raises(NotImplementedError) as exc:
        selector.select_workdir_lease("/tmp/does-not-matter")
    assert "win32" in str(exc.value) and "POSIX" in str(exc.value)
