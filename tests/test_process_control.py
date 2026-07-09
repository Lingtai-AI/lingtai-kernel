"""Unit tests for the neutral platform-aware process-control primitives.

These prove the native-Windows branches (spawn kwargs, liveness, termination)
without a real Windows host by monkeypatching ``supports_posix_signals`` and the
Windows subprocess surface, and prove the POSIX branches are byte-for-byte the
historical behavior.
"""
import os
import signal
import subprocess

import pytest

from lingtai_kernel import process_control as pc


# --------------------------------------------------------------------------
# detached_process_kwargs
# --------------------------------------------------------------------------

def test_detached_kwargs_posix_uses_start_new_session(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)
    assert pc.detached_process_kwargs() == {"start_new_session": True}


def test_detached_kwargs_windows_uses_creationflags(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.setattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200,
                        raising=False)
    kwargs = pc.detached_process_kwargs()
    assert kwargs == {"creationflags": 0x200}
    assert "start_new_session" not in kwargs


def test_detached_kwargs_windows_without_flag_spawns_plain(monkeypatch):
    # A platform build lacking the flag must degrade to plain spawn, not crash.
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.delattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", raising=False)
    assert pc.detached_process_kwargs() == {}


# --------------------------------------------------------------------------
# pid_is_alive — POSIX
# --------------------------------------------------------------------------

def test_pid_is_alive_true_for_self():
    assert pc.pid_is_alive(os.getpid()) is True


def test_pid_is_alive_false_for_dead_pid():
    # A high pid extremely unlikely to exist -> ProcessLookupError -> False.
    assert pc.pid_is_alive(999_999) is False


@pytest.mark.parametrize("bad", [0, -1, -999])
def test_pid_is_alive_false_for_nonpositive(bad):
    assert pc.pid_is_alive(bad) is False


def test_pid_is_alive_permission_error_means_alive(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)

    def _raise(pid, sig):
        raise PermissionError("owned by another user")

    monkeypatch.setattr(pc.os, "kill", _raise)
    assert pc.pid_is_alive(1234) is True


def test_pid_is_alive_other_oserror_is_indeterminate(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)

    def _raise(pid, sig):
        raise OSError("weird")

    monkeypatch.setattr(pc.os, "kill", _raise)
    assert pc.pid_is_alive(1234) is None


# --------------------------------------------------------------------------
# pid_is_alive — Windows (ctypes-free simulation)
# --------------------------------------------------------------------------

def test_pid_is_alive_windows_no_ctypes_is_indeterminate(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    # Force the ctypes import inside _windows_pid_is_alive to fail.
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "ctypes":
            raise ImportError("no ctypes")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert pc.pid_is_alive(1234) is None


# --------------------------------------------------------------------------
# terminate_pid — POSIX
# --------------------------------------------------------------------------

def test_terminate_pid_posix_sends_sigterm(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)
    sent = []
    monkeypatch.setattr(pc.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert pc.terminate_pid(4321) is True
    assert sent == [(4321, signal.SIGTERM)]


def test_terminate_pid_posix_force_sends_sigkill(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)
    sent = []
    monkeypatch.setattr(pc.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert pc.terminate_pid(4321, force=True) is True
    assert sent == [(4321, signal.SIGKILL)]


def test_terminate_pid_posix_already_gone_is_success(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)

    def _raise(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(pc.os, "kill", _raise)
    assert pc.terminate_pid(4321) is True


def test_terminate_pid_posix_oserror_is_failure(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)

    def _raise(pid, sig):
        raise OSError("denied")

    monkeypatch.setattr(pc.os, "kill", _raise)
    assert pc.terminate_pid(4321) is False


def test_terminate_pid_ignores_tree_on_posix(monkeypatch):
    # tree= is a Windows-only concept; POSIX must not error on it.
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)
    monkeypatch.setattr(pc.os, "kill", lambda pid, sig: None)
    assert pc.terminate_pid(4321, tree=True, force=True) is True


# --------------------------------------------------------------------------
# terminate_pid — Windows taskkill
# --------------------------------------------------------------------------

def _fake_completed(returncode=0, stderr=b""):
    class _C:
        pass
    c = _C()
    c.returncode = returncode
    c.stderr = stderr
    return c


def test_terminate_pid_windows_taskkill_builds_command(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(returncode=0)

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    assert pc.terminate_pid(777, force=True, tree=True) is True
    assert captured["cmd"] == ["taskkill", "/PID", "777", "/T", "/F"]


def test_terminate_pid_windows_taskkill_graceful_no_force_flag(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(returncode=0)

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    assert pc.terminate_pid(777) is True
    assert captured["cmd"] == ["taskkill", "/PID", "777"]


def test_terminate_pid_windows_taskkill_missing_is_failure(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("taskkill not on PATH")

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    # Honest failure — does NOT fake success when taskkill is unavailable.
    assert pc.terminate_pid(777, force=True) is False


def test_terminate_pid_windows_taskkill_not_found_is_success(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)

    def fake_run(cmd, **kwargs):
        return _fake_completed(returncode=128, stderr=b"ERROR: process not found")

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    # Already gone -> treat as success for a terminate request.
    assert pc.terminate_pid(777, force=True) is True


def test_terminate_pid_windows_taskkill_other_failure(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)

    def fake_run(cmd, **kwargs):
        return _fake_completed(returncode=1, stderr=b"ERROR: access denied")

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    assert pc.terminate_pid(777, force=True) is False


def test_terminate_pid_windows_timeout_is_failure(monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=1.0)

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    assert pc.terminate_pid(777) is False
