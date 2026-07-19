"""Shared low-level Win32 process observation/termination surface.

Internal to ``lingtai.adapters.windows``: capability adapters (workdir lease,
refresh watcher, daemon, avatar) each keep their own capability-local Ports and
policy; this module only prevents divergent copies of the same raw ctypes
plumbing. It deliberately exposes exactly four primitives — liveness,
creation-time identity, exact-PID termination, and the detached-spawn creation
flags — and no process enumeration, tree, or policy surface, so it cannot grow
into a generic process supervisor.

All helpers require Windows at call time and guard with ``os.name``; importing
this module is safe on every platform.

Windows semantics worth naming once:

- ``os.kill(pid, sig)`` on Windows unconditionally *terminates* the target for
  any signal other than the two console-control events — it is never a liveness
  probe or a graceful request. Liveness here uses ``OpenProcess`` +
  ``GetExitCodeProcess``.
- ``GetExitCodeProcess`` reports the sentinel ``STILL_ACTIVE`` (259) for live
  processes; a process that exited *with* code 259 is indistinguishable. This
  is a documented Win32 caveat; callers that need stronger truth combine
  liveness with creation-time identity.
- Creation-time identity (``windows:<creation_filetime>``) matches the format
  used by the shell adapter's process references; PID alone is never authority.
"""
from __future__ import annotations

import functools
import os

# Detached spawn: new process group (isolates console-control events), no
# visible window. Children on Windows survive parent death by default; if the
# parent runs inside a kill-on-close Job Object the host controls that, not us.
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_CREATIONFLAGS = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


@functools.lru_cache(maxsize=1)
def _kernel32():
    """Return kernel32 with explicit 64-bit-safe ctypes signatures."""
    if os.name != "nt":
        raise OSError("Win32 process surface requires Windows")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def process_alive(pid: int) -> bool:
    """Report liveness via ``OpenProcess`` + ``GetExitCodeProcess``.

    Never signals. An ``OpenProcess`` failure with ``ERROR_ACCESS_DENIED``
    means the process exists but is not queryable — reported alive.
    """
    if os.name != "nt":
        raise OSError("Win32 process surface requires Windows")
    if pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    kernel = _kernel32()
    handle = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _filetime_value(filetime) -> int:
    return (int(filetime.dwHighDateTime) << 32) | int(filetime.dwLowDateTime)


def process_creation_identity(pid: int) -> str | None:
    """Return ``windows:<creation_filetime>`` for a live observable process.

    ``None`` when the process is gone or unobservable — callers must refuse
    ownership-sensitive operations on ``None`` rather than fall back to PID
    trust. Format matches the shell adapter's process-reference identity.
    """
    if os.name != "nt":
        raise OSError("Win32 process surface requires Windows")
    if pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    kernel = _kernel32()
    handle = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
    if not handle:
        return None
    created = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    try:
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return f"windows:{_filetime_value(created)}"
    finally:
        kernel.CloseHandle(handle)


def terminate_pid(pid: int, exit_code: int = 1) -> bool:
    """Forcefully terminate exactly one PID via ``TerminateProcess``.

    Returns ``False`` when the process cannot be opened for termination
    (already gone or access denied). Never touches children: process-tree
    ownership belongs to capability adapters (Job Objects), not this helper.
    """
    if os.name != "nt":
        raise OSError("Win32 process surface requires Windows")
    if pid <= 0:
        return False
    import ctypes  # noqa: F401 - uniform lazy-import discipline
    from ctypes import wintypes

    kernel = _kernel32()
    handle = kernel.OpenProcess(_PROCESS_TERMINATE, False, wintypes.DWORD(pid))
    if not handle:
        return False
    try:
        return bool(kernel.TerminateProcess(handle, exit_code))
    finally:
        kernel.CloseHandle(handle)


__all__ = [
    "CREATE_NEW_PROCESS_GROUP",
    "CREATE_NO_WINDOW",
    "DETACHED_CREATIONFLAGS",
    "process_alive",
    "process_creation_identity",
    "terminate_pid",
]
