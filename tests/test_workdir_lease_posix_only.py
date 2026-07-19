"""Platform-selection and lazy-import policy for the workdir-lease selector.

Historically this file proved the selector was POSIX-only. The selector now
owns two production branches (POSIX ``flock``, Windows ``msvcrt`` byte-range);
this file keeps proving the selection *policy*: the right adapter type per
platform, loud failure on genuinely unsupported platforms, and no eager
mechanism import (``fcntl``/``msvcrt``) at selector or facade import time.
"""
from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_selector_returns_windows_adapter_on_win32(monkeypatch, tmp_path):
    import lingtai.adapters.workdir_lease as selector
    from lingtai.adapters.windows.workdir_lease import WindowsWorkdirLeaseAdapter

    monkeypatch.setattr(selector.sys, "platform", "win32")
    lease = selector.select_workdir_lease(tmp_path)
    assert isinstance(lease, WindowsWorkdirLeaseAdapter)


def test_selector_fails_loudly_on_genuinely_unsupported_platform(monkeypatch, tmp_path):
    import lingtai.adapters.workdir_lease as selector

    monkeypatch.setattr(selector.sys, "platform", "haiku1")
    monkeypatch.setattr(selector.os, "name", "java")
    with pytest.raises(NotImplementedError) as exc:
        selector.select_workdir_lease(tmp_path)
    assert "haiku1" in str(exc.value)
    assert "POSIX" in str(exc.value)
    assert "Windows" in str(exc.value)


def test_windows_adapter_module_imports_without_msvcrt():
    """The Windows adapter module must import everywhere; msvcrt loads lazily."""
    module = importlib.import_module("lingtai.adapters.windows.workdir_lease")
    assert "msvcrt" not in module.__dict__


def test_posix_facade_and_selector_survive_missing_fcntl(monkeypatch, tmp_path):
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
    from lingtai.adapters.windows.workdir_lease import WindowsWorkdirLeaseAdapter

    # A win32 selection never touches fcntl: it selects the Windows adapter.
    monkeypatch.setattr(selector.sys, "platform", "win32")
    lease = selector.select_workdir_lease(tmp_path)
    assert isinstance(lease, WindowsWorkdirLeaseAdapter)

    # A genuinely unsupported platform still fails via the selector's explicit
    # NotImplementedError, not a bare fcntl ModuleNotFoundError.
    monkeypatch.setattr(selector.sys, "platform", "haiku1")
    monkeypatch.setattr(selector.os, "name", "java")
    with pytest.raises(NotImplementedError) as exc:
        selector.select_workdir_lease(tmp_path)
    assert "haiku1" in str(exc.value)
