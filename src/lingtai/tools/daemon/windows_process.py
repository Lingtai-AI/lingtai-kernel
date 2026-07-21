"""Windows production adapter for the daemon process port.

Mirrors the POSIX adapter's ownership model (see ``posix_process.py``) onto
native Windows mechanisms: ``PRIVATE_PROCESS_GROUP`` scope gives each spawn
its own Job Object (assigned while the child is ``CREATE_SUSPENDED``, resumed
only after assignment, closing the spawn-to-assignment tree-ownership race);
``INHERITED_SUPERVISOR_GROUP`` scope creates no Job and touches only the exact
``Popen`` child. See ``CONTRACT.md`` "Windows invariants" for the full design.
"""
from __future__ import annotations

import functools
import os
import subprocess
import threading
from collections.abc import Callable

from lingtai.adapters.windows import _win32
from lingtai.adapters.windows.process_identity import process_identity
from .process_port import (
    DaemonProcessCommand, DaemonProcessExit, DaemonProcessHandle,
    DaemonProcessObservation, DaemonProcessTerminationScope,
)
from .runtime import iter_stdout_with_deadline

_CREATE_SUSPENDED = 0x00000004
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


@functools.lru_cache(maxsize=1)
def _kernel32():
    """Return kernel32 with explicit 64-bit-safe ctypes signatures."""
    if os.name != "nt":
        raise OSError("daemon Windows process adapter requires Windows")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
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
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    return kernel


@functools.lru_cache(maxsize=1)
def _ntdll():
    """Return ntdll with the process-resume signature used after Job assignment."""
    if os.name != "nt":
        raise OSError("daemon Windows process adapter requires Windows")
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
    # Deliberately NOT KILL_ON_JOB_CLOSE: POSIX private-session children
    # survive manager death, so do Job members here (see CONTRACT.md).
    info.BasicLimitInformation.LimitFlags = 0
    if not _kernel32().SetInformationJobObject(
        job_handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info), ctypes.sizeof(info),
    ):
        raise _last_win_error("SetInformationJobObject failed")


def _assign_new_job(proc: subprocess.Popen):
    """Create a Job Object and assign the still-suspended child to it."""
    kernel = _kernel32()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _last_win_error("CreateJobObjectW failed")
    try:
        _set_kill_on_close(job)
        handle = kernel.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False, proc.pid,
        )
        if not handle:
            raise _last_win_error("OpenProcess failed while assigning the daemon Job Object")
        try:
            if not kernel.AssignProcessToJobObject(job, handle):
                raise _last_win_error("AssignProcessToJobObject failed")
        finally:
            kernel.CloseHandle(handle)
        return job
    except Exception:
        kernel.CloseHandle(job)
        raise


def _resume_suspended_process(proc: subprocess.Popen) -> None:
    """Resume a CREATE_SUSPENDED Popen through its retained process handle."""
    handle = getattr(proc, "_handle", None)
    if handle is None:
        raise OSError("Popen did not retain the suspended process handle")
    status = int(_ntdll().NtResumeProcess(handle))
    if status < 0:
        raise OSError(
            f"NtResumeProcess failed after assigning the daemon Job Object "
            f"(NTSTATUS 0x{status & 0xFFFFFFFF:08x})"
        )


def _active_job_processes(job_handle) -> int:
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
        job_handle, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info), ctypes.sizeof(info), None,
    ):
        raise _last_win_error("QueryInformationJobObject(accounting) failed")
    return int(info.ActiveProcesses)


def _terminate_job(job_handle) -> bool:
    """Terminate every process in the Job. Returns whether the call succeeded."""
    return bool(_kernel32().TerminateJobObject(job_handle, 1))


def _wait_job_empty(job_handle, timeout: float) -> bool:
    """Block until the Job's active-process count reaches zero, or time out."""
    import time

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if _active_job_processes(job_handle) == 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def _close_job(job_handle) -> None:
    _kernel32().CloseHandle(job_handle)


class _Drain:
    def __init__(self, thread: threading.Thread, lines: list[str]) -> None:
        self._thread, self.lines = thread, lines

    def join(self, timeout: float = 2.0) -> None:
        self._thread.join(timeout=timeout)


class WindowsDaemonProcessPort:
    """Owns Windows Popen, pipes, Job Objects, and bounded reaping.

    ``PRIVATE_PROCESS_GROUP`` (default) gives each spawn its own Job Object
    for group-wide reclaim. ``INHERITED_SUPERVISOR_GROUP`` creates no Job;
    lifecycle operations touch only the exact ``Popen`` child.
    """

    def __init__(
        self, *, term_timeout: float = 5.0, kill_timeout: float = 3.0,
        termination_scope: DaemonProcessTerminationScope = (
            DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
        ),
        observation_callback: Callable[[DaemonProcessObservation], None] | None = None,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("daemon Windows process adapter is unsupported on this platform")
        self._term_timeout = term_timeout
        self._kill_timeout = kill_timeout
        self._termination_scope = termination_scope
        self._observation_callback = observation_callback
        # (proc, group_id, reason, job_handle)
        self._handles: dict[DaemonProcessHandle, tuple[subprocess.Popen, str | None, str | None, object | None]] = {}
        self._lock = threading.Lock()

    def set_observation_callback(
        self, callback: Callable[[DaemonProcessObservation], None] | None,
    ) -> None:
        """Install the owner callback for immediate post-spawn identity publication."""
        self._observation_callback = callback

    def _popen(self, command: DaemonProcessCommand) -> subprocess.Popen:
        env = dict(command.environment) if command.environment is not None else None
        creationflags = _win32.DETACHED_CREATIONFLAGS
        if self._termination_scope is DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP:
            # Keep the child suspended until it is assigned to the Job Object;
            # this closes the spawn-to-assignment tree-ownership race.
            creationflags |= _CREATE_SUSPENDED
        return subprocess.Popen(
            tuple(command.argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(command.cwd), env=env,
            creationflags=creationflags,
        )

    def spawn(self, command, *, group_id=None):
        proc = self._popen(command)
        job = None
        try:
            if self._termination_scope is DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP:
                job = _assign_new_job(proc)
                _resume_suspended_process(proc)
            handle = DaemonProcessHandle(object())
            with self._lock:
                self._handles[handle] = (proc, group_id, None, job)
            callback = self._observation_callback
            if callback is not None:
                try:
                    callback(DaemonProcessObservation(
                        pid=proc.pid, pgid=None,
                        start_identity=process_identity(proc.pid),
                        termination_scope=self._termination_scope,
                    ))
                except BaseException:
                    self._cleanup_spawn_failure(handle)
                    raise
            return handle
        except BaseException:
            if job is not None:
                try:
                    _terminate_job(job)
                finally:
                    _close_job(job)
            try:
                proc.kill()
                proc.wait(timeout=2)
            except (ProcessLookupError, OSError, subprocess.SubprocessError):
                pass
            raise

    def _entry(self, handle):
        with self._lock:
            try:
                return self._handles[handle]
            except (KeyError, TypeError) as exc:
                raise KeyError("unknown daemon process handle") from exc

    def iter_stdout(self, handle, *, deadline=None):
        entry = self._entry(handle)
        proc = entry[0]
        if deadline is None:
            return iter(proc.stdout or ())
        return iter_stdout_with_deadline(proc, deadline, "daemon-process-stdout")

    def drain_stderr(self, handle, *, on_line=None, thread_name="daemon-stderr"):
        entry = self._entry(handle)
        proc = entry[0]
        lines: list[str] = []
        def drain():
            if proc.stderr is None:
                return
            for line in proc.stderr:
                line = line.rstrip("\n")
                if not line:
                    continue
                lines.append(line)
                if on_line is not None:
                    on_line(line)
        thread = threading.Thread(target=drain, daemon=True, name=thread_name)
        thread.start()
        return _Drain(thread, lines)

    def wait(self, handle, *, timeout=None):
        proc, _, reason, _job = self._entry(handle)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError("daemon process wait deadline expired")
        # A watchdog or lifecycle sweep may stamp the local cause while this
        # thread is blocked in wait(). Re-read the current receipt metadata so
        # the terminal observation cannot lose that concurrent attribution.
        with self._lock:
            current = self._handles.get(handle)
            if current is not None and current[0] is proc:
                reason = current[2]
        return DaemonProcessExit(proc.returncode, reason)

    def _terminate(self, proc, reason, job, *, reap_fully=False):
        # Windows termination is forceful-only: the SIGTERM->SIGKILL ladder
        # collapses to force-then-reap with the same bounded waits.
        if proc.poll() is not None:
            return DaemonProcessExit(proc.returncode, reason)
        if job is not None:
            # Group-owned (PRIVATE_PROCESS_GROUP): reclaim the whole Job tree.
            terminated = _terminate_job(job)
            if terminated:
                _wait_job_empty(job, self._term_timeout + self._kill_timeout)
        if proc.poll() is None:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=self._kill_timeout)
            except subprocess.TimeoutExpired:
                if reap_fully:
                    try:
                        proc.kill()
                    except (ProcessLookupError, OSError):
                        pass
                    try:
                        proc.wait()
                    except (ProcessLookupError, OSError):
                        pass
        return DaemonProcessExit(proc.returncode, reason)

    def _cleanup_spawn_failure(self, handle) -> None:
        """Reap and forget a child whose observation transaction failed."""
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            return
        proc, _group, _reason, job = entry
        try:
            self._terminate(proc, "observation-failed", job, reap_fully=True)
        finally:
            with self._lock:
                self._handles.pop(handle, None)
            if job is not None:
                _close_job(job)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def terminate(self, handle, *, reason=None):
        # Choose and persist the first local cause atomically, then perform the
        # bounded signal/wait sequence outside the registry lock.
        with self._lock:
            try:
                proc, group, old_reason, job = self._handles[handle]
            except (KeyError, TypeError) as exc:
                raise KeyError("unknown daemon process handle") from exc
            chosen_reason = old_reason if old_reason is not None else reason
            self._handles[handle] = (proc, group, chosen_reason, job)
        return self._terminate(proc, chosen_reason, job)

    def terminate_group(self, group_id, *, reason=None):
        with self._lock:
            handles = [h for h, (_, group, _, _) in self._handles.items() if group == group_id]
        for handle in handles:
            try:
                self.terminate(handle, reason=reason)
            except KeyError:
                # A terminal owner may release after our snapshot. That handle
                # no longer needs a signal; continue sweeping the remaining
                # group instead of abandoning live siblings.
                continue
        return len(handles)

    def terminate_all(self, *, reason=None):
        with self._lock:
            handles = list(self._handles)
        for handle in handles:
            try:
                self.terminate(handle, reason=reason)
            except KeyError:
                # Same snapshot/release race as the group path above.
                continue
        return len(handles)

    def release(self, handle):
        with self._lock:
            entry = self._handles.get(handle)
            if entry is None:
                return True
            proc, _group, _reason, job = entry
            if proc.poll() is None:
                return False
            # The root exiting does not prove the Job is empty: a descendant
            # spawned under the root can outlive it. The Job is deliberately
            # created WITHOUT KILL_ON_JOB_CLOSE (private sessions survive
            # manager death), so closing its last handle while members remain
            # would silently orphan them with no supervisor sweep to recover
            # them. Confirm emptiness before ever closing the handle.
            if job is not None and not _wait_job_empty(job, 0.0):
                return False
            self._handles.pop(handle, None)
        if job is not None:
            _close_job(job)
        return True


__all__ = ["WindowsDaemonProcessPort"]
