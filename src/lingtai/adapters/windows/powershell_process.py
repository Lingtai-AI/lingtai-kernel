"""Windows process adapter using Job Objects for owned process trees.

This module is imported only by the Windows composition selector.  It does not
use ``killpg``, ``/proc`` or ``ps``.  A command process is assigned to a native
Job Object immediately after spawn; cancellation terminates that job and waits
for its active-process count to reach zero before reporting ``group_cancelled``.
"""
from __future__ import annotations

import functools
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lingtai.tools.bash._async_process import (
    BashAsyncProcessPort,
    ProcessCompletion,
    ProcessObservation,
    ProcessRef,
)
from lingtai.tools.bash._shell_dialect import ShellInvocation

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


@functools.lru_cache(maxsize=1)
def _kernel32():
    """Return kernel32 with explicit 64-bit-safe ctypes signatures."""
    if os.name != "nt":
        raise OSError("Windows process adapter requires Windows")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    return kernel


@functools.lru_cache(maxsize=1)
def _ntdll():
    """Return ntdll with the process-resume signature used after Job assignment."""
    if os.name != "nt":
        raise OSError("Windows process adapter requires Windows")
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = wintypes.LONG
    return ntdll


def _last_win_error(message: str) -> OSError:
    import ctypes

    error = ctypes.get_last_error()
    return OSError(error, f"{message} (WinError {error})")


def _set_kill_on_close(job_handle) -> None:
    """Make supervisor death close the owned process tree instead of leaking it."""
    import ctypes
    from ctypes import wintypes

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
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
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _kernel32().SetInformationJobObject(
        job_handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise _last_win_error("SetInformationJobObject(KILL_ON_JOB_CLOSE) failed")


def _filetime_value(filetime) -> int:
    return (int(filetime.dwHighDateTime) << 32) | int(filetime.dwLowDateTime)


def _identity(pid: int) -> str | None:
    """Return a creation-time identity, never a PID-only authority."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    kernel = _kernel32()
    handle = kernel.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid)
    )
    if not handle:
        return None
    created = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    try:
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(kernel_time), ctypes.byref(user_time),
        ):
            return None
        return f"windows:{_filetime_value(created)}"
    finally:
        kernel.CloseHandle(handle)


def _ref(pid: int) -> ProcessRef:
    return ProcessRef(pid, _identity(pid))


@dataclass
class _Owned:
    process: subprocess.Popen
    ref: ProcessRef
    job_handle: object
    stdout: object | None = None
    stderr: object | None = None

    def close(self) -> None:
        kernel = _kernel32()
        for stream in (self.stdout, self.stderr):
            if stream is not None:
                stream.close()
        if self.job_handle:
            kernel.CloseHandle(self.job_handle)
            self.job_handle = None


def _new_job_for_process(process: subprocess.Popen):
    """Create and assign a kill-on-close Job before exposing the owned token."""
    kernel = _kernel32()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _last_win_error("CreateJobObjectW failed")
    try:
        _set_kill_on_close(job)
        handle = kernel.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            process.pid,
        )
        if not handle:
            raise _last_win_error("OpenProcess failed while assigning the shell Job Object")
        try:
            if not kernel.AssignProcessToJobObject(job, handle):
                raise _last_win_error("AssignProcessToJobObject failed")
        finally:
            kernel.CloseHandle(handle)
        return job
    except Exception:
        kernel.CloseHandle(job)
        raise


def _resume_suspended_process(process: subprocess.Popen) -> None:
    """Resume a CREATE_SUSPENDED Popen through its retained process handle.

    CPython closes the primary thread handle inside ``Popen`` and therefore has
    no ``_thread`` attribute to pass to ``ResumeThread``.  ``NtResumeProcess``
    operates on the retained process handle after Job assignment and closes the
    spawn-to-assignment child-tree race without depending on that missing handle.
    """
    handle = getattr(process, "_handle", None)
    if handle is None:
        raise OSError("Popen did not retain the suspended process handle")
    status = int(_ntdll().NtResumeProcess(handle))
    if status < 0:
        raise OSError(
            f"NtResumeProcess failed after assigning the shell Job Object "
            f"(NTSTATUS 0x{status & 0xFFFFFFFF:08x})"
        )


def _active_job_processes(job_handle) -> int:
    """Return the Job's exact active-process count.

    Natural Job completion does not reliably signal the Job handle on every
    supported Windows runtime.  Basic accounting is the documented ownership
    source of truth for both ordinary completion and cancellation.
    """
    import ctypes
    from ctypes import wintypes

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    info = _BasicAccountingInformation()
    if not _kernel32().QueryInformationJobObject(
        job_handle,
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
        None,
    ):
        raise _last_win_error("QueryInformationJobObject(accounting) failed")
    return int(info.ActiveProcesses)


def _wait_job(job_handle, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        if _active_job_processes(job_handle) == 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


class WindowsShellAsyncProcessAdapter(BashAsyncProcessPort):
    """Native Windows implementation of the shared async process Port."""

    def launch_supervisor(self, job_dir: Path, start_token: str):
        if os.name != "nt":
            raise OSError("Windows shell process adapter requires Windows")
        import sys
        process = subprocess.Popen(
            [sys.executable, "-m", "lingtai.tools.bash._async_supervisor", str(job_dir), start_token],
            creationflags=_CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        ref = _ref(process.pid)
        return ref, process

    def identify_current_process(self):
        return _ref(os.getpid())

    def spawn(self, invocation: ShellInvocation, cwd: str, stdout_path: Path, stderr_path: Path):
        if os.name != "nt":
            raise OSError("Windows shell process adapter requires Windows")
        stdout = stdout_path.open("xb")
        stderr = stderr_path.open("xb")
        process = None
        job = None
        try:
            args, kwargs = invocation.process_args()
            kwargs.update({
                "stdin": subprocess.DEVNULL,
                "stdout": stdout,
                "stderr": stderr,
                "cwd": cwd,
                # Keep the child suspended until it is assigned to the Job
                # Object; this closes the spawn-to-assignment tree-ownership race.
                "creationflags": _CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED | _CREATE_NO_WINDOW,
                "close_fds": True,
            })
            process = subprocess.Popen(args, **kwargs)
            job = _new_job_for_process(process)
            _resume_suspended_process(process)
            ref = _ref(process.pid)
            return ref, _Owned(process, ref, job, stdout, stderr)
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
            stdout.close()
            stderr.close()
            raise

    def observe(self, process: ProcessRef):
        if os.name != "nt":
            return ProcessObservation("gone")
        observed = _identity(process.public_id)
        if observed is None:
            return ProcessObservation("gone")
        if process.incarnation is None:
            return ProcessObservation("unknown")
        return ProcessObservation("same" if observed == process.incarnation else "changed")

    def wait_supervisor(self, owned) -> int:
        return owned.wait() if hasattr(owned, "wait") else owned.returncode

    def wait(self, owned: _Owned, cancellation_requested: Callable[[], bool]):
        process = owned.process

        def cancel_owned_tree() -> ProcessCompletion | None:
            if not cancellation_requested():
                return None
            terminated = bool(_kernel32().TerminateJobObject(owned.job_handle, 1))
            if not terminated:
                return ProcessCompletion(process.wait(), "unconfirmed")
            # ActiveProcesses reaches zero only after every assigned child exits,
            # which is the full-tree ownership proof.
            if not _wait_job(owned.job_handle, 5.0):
                return ProcessCompletion(process.wait(), "unconfirmed")
            return ProcessCompletion(process.wait(), "group_cancelled")

        try:
            while True:
                cancellation = cancel_owned_tree()
                if cancellation is not None:
                    return cancellation
                if process.poll() is None:
                    time.sleep(0.05)
                    continue

                # The root Popen may have exited while a descendant is still in
                # the Job.  Keep the Job handle owned and poll it in short
                # intervals so a later durable cancel request still gets the
                # confirmed TerminateJobObject/group_cancelled path.  Basic Job
                # accounting is used because natural completion did not reliably
                # signal the Job handle in native CI.
                code = process.wait()
                while not _wait_job(owned.job_handle, 0.05):
                    cancellation = cancel_owned_tree()
                    if cancellation is not None:
                        return cancellation
                return ProcessCompletion(code)
        finally:
            owned.close()


__all__ = ["WindowsShellAsyncProcessAdapter"]
