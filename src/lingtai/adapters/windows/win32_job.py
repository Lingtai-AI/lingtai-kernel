"""Windows Job Object process-tree containment (Codex ``process_group`` pattern).

Internal to ``lingtai.adapters.windows``: the race-free descendant-kill
primitive for the shell capability.  The technique follows Codex CLI's
``process_group`` module (``codex-rs/utils/pty/src/win/job.rs``): create a Job
Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK``,
spawn the child suspended (``CREATE_SUSPENDED``), assign it with
``AssignProcessToJobObject``, then resume it with ``NtResumeProcess`` — so no
descendant can exist outside the job once the spawn-to-assignment race window
closes.  Terminating the Job Object then kills the whole tree in one step;
``taskkill /PID <pid> /T /F`` is the bounded best-effort fallback when the
Job-Object kill itself fails.

The bounded output drain (``drain_pipes``) mirrors Codex's ``io_drain_timeout``
(``codex-rs/core/src/exec.rs``): after a kill, stdout/stderr pipes are drained
for only a bounded window, because a grandchild that inherited the pipe write
ends and survived the kill keeps them open and would block an unbounded reader
forever (Goose PR #7689).  The drain never ``close()``s a stream whose reader
thread is still alive: on CPython the reader thread holds the buffered-IO lock
for the duration of a blocking ``read()``, so ``close()`` would wait on that
same lock forever.  The pipe reader threads are daemon threads and finish on
their own once the killed tree releases the write ends.

Importing this module is safe on every platform; every Job Object helper raises
``OSError`` on non-Windows at call time.  ``taskkill_tree_best_effort`` is the
one deliberately best-effort exception: when ``taskkill`` is unavailable it is
a safe no-op.
"""
from __future__ import annotations

import functools
import os
import subprocess
import time

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
#: Creation flags for a contained spawn: isolated console-control events, no
#: window, and suspended until the Job Object assignment completes.
CONTAINED_CREATIONFLAGS = CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED | CREATE_NO_WINDOW

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00001000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1

_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ASSIGN_PROCESS_ACCESS = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION

_TASKKILL_TIMEOUT_SECONDS = 10.0
#: Post-kill pipe drain bound (Codex ``IO_DRAIN_TIMEOUT_MS``; Goose PR #7689).
IO_DRAIN_TIMEOUT_SECONDS = 0.5


def _require_windows() -> None:
    """Guard every Job Object helper: these primitives are Windows-only."""
    if os.name != "nt":
        raise OSError("Windows Job Object containment requires Windows")


@functools.lru_cache(maxsize=1)
def _kernel32():
    """Return kernel32 with explicit 64-bit-safe ctypes signatures."""
    _require_windows()
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


@functools.lru_cache(maxsize=1)
def _ntdll():
    """Return ntdll with the NtResumeProcess signature used after Job assignment."""
    _require_windows()
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    return ntdll


def _last_win_error(message: str) -> OSError:
    import ctypes

    error = ctypes.get_last_error()
    return OSError(error, f"{message} (WinError {error})")


def _extended_limit_info(limit_flags: int):
    """Build the JOBOBJECT_EXTENDED_LIMIT_INFORMATION ctypes buffer.

    Exposed for unit tests: constructing the argument (not the Win32 call) is
    platform-neutral, so the exact byte layout can be pinned on any host.
    """
    import ctypes

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = limit_flags
    return info


def _basic_accounting_info():
    """Build the JOBOBJECT_BASIC_ACCOUNTING_INFORMATION ctypes buffer."""
    import ctypes

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", ctypes.c_uint32),
            ("TotalProcesses", ctypes.c_uint32),
            ("ActiveProcesses", ctypes.c_uint32),
            ("TotalTerminatedProcesses", ctypes.c_uint32),
        ]

    return _BasicAccountingInformation()


def create_job_handle() -> int:
    """Create a kill-on-close + breakaway-ok Job Object (Codex ``JobObject::create``)."""
    _require_windows()
    import ctypes

    kernel = _kernel32()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _last_win_error("CreateJobObjectW failed")
    try:
        _set_limit_flags(
            job, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
    except Exception:
        kernel.CloseHandle(job)
        raise
    return job


def _set_limit_flags(job_handle, limit_flags: int) -> None:
    """Configure Job Object limit flags (Codex ``set_limit_flags``)."""
    import ctypes

    info = _extended_limit_info(limit_flags)
    if not _kernel32().SetInformationJobObject(
        job_handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise _last_win_error("SetInformationJobObject(limit flags) failed")


def open_process_handle(pid: int, access: int) -> int:
    """Open a process handle with ``access`` rights; raises on failure."""
    _require_windows()
    handle = _kernel32().OpenProcess(access, False, pid)
    if not handle:
        raise _last_win_error(f"OpenProcess({pid}) failed")
    return handle


def assign_process(job_handle, pid: int) -> None:
    """Assign a live process to the Job Object (Codex ``assign_process``)."""
    kernel = _kernel32()
    handle = open_process_handle(pid, _ASSIGN_PROCESS_ACCESS)
    try:
        if not kernel.AssignProcessToJobObject(job_handle, handle):
            raise _last_win_error("AssignProcessToJobObject failed")
    finally:
        kernel.CloseHandle(handle)


def resume_suspended(process) -> None:
    """Resume a CREATE_SUSPENDED Popen after Job assignment (Codex ``spawn_contained``).

    CPython closes the primary thread handle inside ``Popen`` and therefore has
    no ``_thread`` attribute to pass to ``ResumeThread``; ``NtResumeProcess``
    operates on the retained process handle after Job assignment, which closes
    the spawn-to-assignment child-tree race without that missing handle.
    """
    _require_windows()
    handle = getattr(process, "_handle", None)
    if handle is None:
        raise OSError("Popen did not retain the suspended process handle")
    status = int(_ntdll().NtResumeProcess(handle))
    if status < 0:
        raise OSError(
            f"NtResumeProcess failed after Job assignment "
            f"(NTSTATUS 0x{status & 0xFFFFFFFF:08x})"
        )


def spawn_into_job(args, kwargs):
    """Spawn ``args``/``kwargs`` inside a fresh kill-on-close Job Object.

    Returns ``(process, job_handle)``.  The child is spawned suspended, assigned
    to the Job, then resumed — the Codex ``spawn_contained`` sequence that
    closes the spawn-to-assignment descendant race.  On failure the Job and the
    suspended child are terminated before re-raising, so nothing is leaked.
    """
    _require_windows()
    spawn_kwargs = dict(kwargs)
    # Callers never pass creation flags; the contained spawn owns them.
    spawn_kwargs["creationflags"] = CONTAINED_CREATIONFLAGS
    process = None
    job = None
    try:
        process = subprocess.Popen(args, **spawn_kwargs)
        job = create_job_handle()
        assign_process(job, process.pid)
        resume_suspended(process)
        return process, job
    except Exception:
        if job is not None:
            try:
                _kernel32().TerminateJobObject(job, 1)
            finally:
                _kernel32().CloseHandle(job)
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
        raise


def terminate_job(job_handle, exit_code: int = 1) -> bool:
    """Terminate every process assigned to the Job (Codex ``terminate``)."""
    _require_windows()
    return bool(_kernel32().TerminateJobObject(job_handle, exit_code))


def job_active_processes(job_handle) -> int:
    """Return the Job's exact active-process count (ownership source of truth)."""
    _require_windows()
    import ctypes

    info = _basic_accounting_info()
    if not _kernel32().QueryInformationJobObject(
        job_handle,
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
        None,
    ):
        raise _last_win_error("QueryInformationJobObject(accounting) failed")
    return int(info.ActiveProcesses)


def wait_job_empty(job_handle, timeout_seconds: float) -> bool:
    """Poll until the Job's active-process count reaches zero."""
    _require_windows()
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        try:
            active = job_active_processes(job_handle)
        except OSError:
            return False
        if active == 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def close_handle(handle) -> None:
    """Best-effort CloseHandle; never raises (safe in cleanup paths)."""
    if not handle:
        return
    if os.name != "nt":
        return
    try:
        _kernel32().CloseHandle(handle)
    except OSError:
        pass


def taskkill_tree_best_effort(pid: int) -> None:
    """Best-effort ``taskkill /PID <pid> /T /F`` fallback (no-op when unavailable).

    The identity of ``pid`` is authoritative here: the caller spawned the
    process itself and holds its ``Popen`` handle, so PID reuse between spawn
    and cleanup is impossible and no creation-time identity re-check applies.
    """
    if pid <= 0:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=_TASKKILL_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def terminate_owned_tree(job_handle, pid: int) -> bool:
    """Terminate the whole contained tree; fall back to taskkill on failure.

    Returns ``True`` when the Job-Object kill itself succeeded (the race-free
    path); ``False`` when the ``taskkill /T /F`` fallback had to be used.
    """
    terminated = False
    try:
        if job_handle:
            terminated = terminate_job(job_handle, 1)
    except OSError:
        terminated = False
    if not terminated:
        taskkill_tree_best_effort(pid)
        return False
    return True


def drain_pipes(process, timeout_seconds: float = IO_DRAIN_TIMEOUT_SECONDS):
    """Bound the post-kill stdout/stderr drain (Codex ``io_drain_timeout``).

    Returns ``(stdout, stderr)``.  When EOF has not arrived within the bound (a
    grandchild survived the kill and still holds the pipe write ends), the
    partial output is returned and the pipe reader threads are detached --
    the caller must never block on EOF forever.

    The streams are never ``close()``-d from this side: CPython's Windows
    reader threads are daemon threads blocked in ``read()`` holding the
    buffered-IO lock, and ``stream.close()`` would wait on that same lock
    forever.  Detaching ``process.stdout``/``process.stderr`` prevents any
    later ``communicate()`` re-entry and lets the reader threads finish on
    their own once the killed tree releases the pipe write ends.
    """
    try:
        return process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout, exc.stderr)
    except (OSError, ValueError):
        # The kill can leave a pipe already broken; the reader thread then
        # surfaces OSError/ValueError instead of TimeoutExpired.  Same bounded
        # outcome: hand back what we have and never block on EOF.
        partial = (None, None)
    # Detach instead of close(): see docstring.  The reader threads are daemon
    # threads and close their own handle when EOF arrives; forcing close()
    # here would block on the buffered-IO lock held by the blocked read.
    try:
        process.stdout = None
        process.stderr = None
    except (AttributeError, TypeError):
        pass
    return partial


__all__ = [
    "CONTAINED_CREATIONFLAGS",
    "CREATE_NO_WINDOW",
    "CREATE_NEW_PROCESS_GROUP",
    "CREATE_SUSPENDED",
    "IO_DRAIN_TIMEOUT_SECONDS",
    "JOB_OBJECT_LIMIT_BREAKAWAY_OK",
    "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "assign_process",
    "close_handle",
    "create_job_handle",
    "drain_pipes",
    "job_active_processes",
    "open_process_handle",
    "resume_suspended",
    "spawn_into_job",
    "taskkill_tree_best_effort",
    "terminate_job",
    "terminate_owned_tree",
    "wait_job_empty",
]
