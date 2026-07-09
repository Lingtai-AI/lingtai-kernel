"""Platform-aware process control primitives for detached agent processes.

Neutral, standard-library-only helpers shared by the kernel lifecycle watcher,
the ``lingtai`` agent/avatar spawn paths, and any other site that launches a
*detached* child agent process or has to probe/terminate one by PID.

Why this lives in ``lingtai_kernel`` rather than ``lingtai.core.daemon.runtime``
(which already has equivalent daemon-scoped helpers): ``base_agent/lifecycle.py``
is kernel code and must not import daemon internals from the ``lingtai`` package.
The daemon keeps its own ``_new_process_group_kwargs`` / ``kill_process_group``
for its CLI-backend process *groups*; those stay daemon-owned. This module is the
kernel-side equivalent for single detached agent processes.

Design invariants:

* POSIX (macOS/Linux): a detached child gets its own session/process group via
  ``start_new_session=True``; liveness is ``os.kill(pid, 0)``; termination is
  ``SIGTERM`` then ``SIGKILL``. This is exactly the pre-existing behavior, so
  POSIX callers are byte-for-byte unchanged.

* Native Windows: there is no ``start_new_session`` and ``os.kill(pid, 0)`` is
  not a liveness probe (signal 0 is not a valid Windows signal). Detached
  children use ``creationflags=CREATE_NEW_PROCESS_GROUP`` where the flag exists;
  liveness uses ``OpenProcess`` via ctypes; termination uses ``taskkill``.
  Nothing here fakes success — a probe that cannot determine liveness says so,
  and a terminate that cannot reach ``taskkill`` reports failure to the caller.
"""
from __future__ import annotations

import os
import subprocess
import sys


def supports_posix_signals() -> bool:
    """True when the platform exposes POSIX process-group/signal semantics.

    ``os.killpg`` and ``start_new_session``-backed process groups only exist on
    POSIX. Native Windows has neither. Kept as a tiny predicate so tests can
    simulate a Windows host on a POSIX machine by monkeypatching this function.
    """
    return hasattr(os, "killpg")


def detached_process_kwargs() -> dict:
    """``Popen`` kwargs that detach a child agent process from this one.

    POSIX: ``start_new_session=True`` puts the child in its own session so it
    survives the parent's exit and can be signalled as a group.

    Native Windows: ``creationflags=CREATE_NEW_PROCESS_GROUP`` where the flag is
    available, so the child is not tied to the parent's console control group
    (and can be reaped as a tree by ``taskkill /T``). If the platform build
    lacks the flag, return no isolation kwarg rather than crashing — the child
    still launches and termination falls back to direct ``taskkill``/handle.
    """
    if supports_posix_signals():
        return {"start_new_session": True}
    flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
    if flag is None:
        return {}
    return {"creationflags": flag}


def pid_is_alive(pid: int) -> bool | None:
    """Best-effort liveness probe for *pid*.

    Returns ``True`` if the process is known to exist, ``False`` if it is known
    not to exist, and ``None`` when liveness cannot be determined honestly
    (e.g. Windows without ctypes, or an OS error other than "no such process").
    Callers decide how to treat ``None`` — the point is not to *fake* a definite
    answer.

    POSIX: ``os.kill(pid, 0)`` — no such process ⇒ False, permission error ⇒
    True (process exists but is owned by another user), other OS error ⇒ None.

    Native Windows: ``OpenProcess`` via ctypes. ``os.kill(pid, 0)`` is NOT used
    on Windows because signal 0 is not a valid Windows signal and does not probe
    liveness.
    """
    if pid <= 0:
        return False
    if supports_posix_signals():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True
    return _windows_pid_is_alive(pid)


def _windows_pid_is_alive(pid: int) -> bool | None:
    """Windows liveness via ``OpenProcess``; ``None`` if ctypes is unavailable."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except Exception:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # OpenProcess failed. ERROR_INVALID_PARAMETER (87) ⇒ no such pid;
        # ERROR_ACCESS_DENIED (5) ⇒ the pid exists but we cannot open it.
        err = kernel32.GetLastError()
        if err == 5:  # ACCESS_DENIED — process exists
            return True
        if err == 87:  # INVALID_PARAMETER — no such process
            return False
        return None
    try:
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return None
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate_pid(pid: int, *, force: bool = False, tree: bool = False,
                  timeout: float = 5.0) -> bool:
    """Terminate the process *pid*, returning True if the request was issued.

    POSIX: sends ``SIGKILL`` when *force* else ``SIGTERM``. *tree* is ignored
    (POSIX group teardown is the caller's job via ``os.killpg`` where it holds a
    process group). Returns True if the signal was delivered or the process was
    already gone; False on an unexpected OS error.

    Native Windows: ``taskkill /PID <pid>`` with ``/F`` when *force* and ``/T``
    when *tree* (reap the whole process tree). Returns True when ``taskkill``
    reports success OR reports the process is already gone; False when the
    ``taskkill`` executable is missing, times out, or fails for another reason.
    Never fakes success — a missing ``taskkill`` returns False so the caller can
    escalate or report honestly.
    """
    if pid <= 0:
        return False
    if supports_posix_signals():
        import signal
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True  # already gone — nothing to terminate
        except OSError:
            return False
        return True
    return _windows_taskkill(pid, force=force, tree=tree, timeout=timeout)


def _windows_taskkill(pid: int, *, force: bool, tree: bool, timeout: float) -> bool:
    """Run ``taskkill`` for *pid*; True on success or already-gone."""
    cmd = ["taskkill", "/PID", str(pid)]
    if tree:
        cmd.append("/T")
    if force:
        cmd.append("/F")
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode == 0:
        return True
    # taskkill returns 128 when the PID is not found ("process not found"),
    # which for a terminate request means "already gone" — treat as success.
    stderr = (completed.stderr or b"").decode("utf-8", "replace").lower()
    if "not found" in stderr or "not running" in stderr:
        return True
    return False
