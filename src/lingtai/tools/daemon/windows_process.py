"""Windows production adapter for the daemon process port.

``WindowsDaemonProcessPort`` is the ``os.name == "nt"`` sibling of
``PosixDaemonProcessPort`` behind the same ``DaemonProcessPort`` protocol. The
POSIX session/process-group vocabulary maps onto Windows like this:

- ``PRIVATE_PROCESS_GROUP`` (ordinary manager composition): each spawn gets
  its own Job Object, assigned while the child is ``CREATE_SUSPENDED`` and
  resumed only after assignment, so the Port owns the child's whole tree from
  its first instruction (the Windows analog of ``start_new_session=True``).
  The Job deliberately does NOT set ``KILL_ON_JOB_CLOSE``: POSIX
  private-session children survive manager death, and this Port mirrors that
  — Job handles closing with the manager must not reap live daemons.
- ``INHERITED_SUPERVISOR_GROUP`` (detached execution host): no Job is
  created. Lifecycle operations touch only the exact ``Popen`` child through
  its retained process handle, mirroring the POSIX exact-PID rule; only the
  supervisor's exact-run reclaim may reach wider.

Termination semantics worth naming once: Windows has no graceful-signal
stage, so the POSIX SIGTERM→SIGKILL ladder collapses to force-then-reap —
``TerminateJobObject`` (private scope) or ``TerminateProcess`` via the exact
child handle (inherited scope), followed by the same bounded waits and the
same first-writer-wins termination-reason receipts.

All Win32 mechanism lives in module-local helpers guarded by ``os.name`` so
this module imports on every platform; construction requires Windows.
"""
from __future__ import annotations

import functools
import os
import subprocess
import threading
import time
from collections.abc import Callable

from lingtai.adapters.windows import _win32
from .process_port import (
    DaemonProcessCommand, DaemonProcessExit, DaemonProcessHandle,
    DaemonProcessObservation, DaemonProcessTerminationScope,
)
from .runtime import iter_stdout_with_deadline

_CREATE_SUSPENDED = 0x00000004
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1


@functools.lru_cache(maxsize=1)
def _kernel32():
    """Return kernel32 with explicit 64-bit-safe ctypes signatures."""
    if os.name != "nt":
        raise OSError("Windows daemon process adapter requires Windows")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
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
        raise OSError("Windows daemon process adapter requires Windows")
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


def _assign_new_job(process: subprocess.Popen):
    """Create a plain Job (no KILL_ON_JOB_CLOSE) and assign the suspended child.

    Returns the Job handle. Raises on any failure — a private-scope spawn whose
    tree cannot be owned must fail loudly, never degrade to an unowned child.
    """
    kernel = _kernel32()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _last_win_error("CreateJobObjectW failed")
    try:
        handle = kernel.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            process.pid,
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


def _resume_suspended_process(process: subprocess.Popen) -> None:
    """Resume a ``CREATE_SUSPENDED`` Popen through its retained process handle.

    ``NtResumeProcess`` operates on the process handle CPython retains
    (``Popen._handle``); the primary thread handle is already closed inside
    ``Popen`` and cannot be used. Same rationale as the shell Job adapter.
    """
    handle = getattr(process, "_handle", None)
    if handle is None:
        raise OSError("Popen did not retain the suspended process handle")
    status = int(_ntdll().NtResumeProcess(handle))
    if status < 0:
        raise OSError(
            f"NtResumeProcess failed after assigning the daemon Job Object "
            f"(NTSTATUS 0x{status & 0xFFFFFFFF:08x})"
        )


def _terminate_job(job_handle) -> bool:
    """Forcefully terminate every process currently assigned to the Job."""
    return bool(_kernel32().TerminateJobObject(job_handle, 1))


def _active_job_processes(job_handle) -> int:
    """Return the Job's exact active-process count via basic accounting."""
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


def _wait_job_empty(job_handle, timeout_seconds: float) -> bool:
    """Bounded poll until the Job owns zero active processes."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        try:
            if _active_job_processes(job_handle) == 0:
                return True
        except OSError:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def _close_job(job_handle) -> None:
    """Close a Job handle; without KILL_ON_JOB_CLOSE members keep running."""
    if job_handle:
        _kernel32().CloseHandle(job_handle)


class _Drain:
    def __init__(self, thread: threading.Thread, lines: list[str]) -> None:
        self._thread, self.lines = thread, lines

    def join(self, timeout: float = 2.0) -> None:
        self._thread.join(timeout=timeout)


class WindowsDaemonProcessPort:
    """Owns Windows Popen, pipes, per-spawn Job Objects, and bounded reaping."""

    def __init__(
        self, *, term_timeout: float = 5.0, kill_timeout: float = 3.0,
        termination_scope: DaemonProcessTerminationScope = (
            DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
        ),
        observation_callback: Callable[[DaemonProcessObservation], None] | None = None,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("daemon Windows process adapter is unsupported on this platform")
        if not isinstance(termination_scope, DaemonProcessTerminationScope):
            raise ValueError("termination_scope must be a DaemonProcessTerminationScope")
        self._term_timeout = term_timeout
        self._kill_timeout = kill_timeout
        self._termination_scope = termination_scope
        self._observation_callback = observation_callback
        # handle -> (proc, group_id, first termination reason, job handle|None)
        self._handles: dict[DaemonProcessHandle, tuple[subprocess.Popen, str | None, str | None, object | None]] = {}
        self._lock = threading.Lock()

    def set_observation_callback(
        self, callback: Callable[[DaemonProcessObservation], None] | None,
    ) -> None:
        """Install the owner callback for immediate post-spawn identity publication."""
        self._observation_callback = callback

    def _popen(self, command: DaemonProcessCommand, creationflags: int) -> subprocess.Popen:
        env = dict(command.environment) if command.environment is not None else None
        return subprocess.Popen(
            tuple(command.argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(command.cwd), env=env,
            creationflags=creationflags,
        )

    def spawn(self, command, *, group_id=None):
        private = (
            self._termination_scope
            is DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
        )
        creationflags = _win32.DETACHED_CREATIONFLAGS
        if private:
            # Suspended spawn closes the spawn-to-assignment tree-ownership
            # race: no grandchild can be created before the Job owns the root.
            creationflags |= _CREATE_SUSPENDED
        proc = self._popen(command, creationflags)
        job_handle = None
        if private:
            try:
                job_handle = _assign_new_job(proc)
                _resume_suspended_process(proc)
            except BaseException:
                _close_job(job_handle)
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=self._kill_timeout)
                except (OSError, subprocess.SubprocessError):
                    pass
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
                raise
        handle = DaemonProcessHandle(object())
        with self._lock:
            self._handles[handle] = (proc, group_id, None, job_handle)
        callback = self._observation_callback
        if callback is not None:
            try:
                callback(DaemonProcessObservation(
                    pid=proc.pid, pgid=None,
                    start_identity=_win32.process_creation_identity(proc.pid),
                    termination_scope=self._termination_scope,
                ))
            except BaseException:
                self._cleanup_spawn_failure(handle)
                raise
        return handle

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
        proc, _, reason, _ = self._entry(handle)
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

    def _force_terminate(self, proc, job_handle) -> None:
        """Force-terminate according to the explicit ownership scope.

        Private scope terminates the owned Job (whole tree); inherited scope
        terminates only the exact child through its retained handle — never a
        wider group the detached execution host owns.
        """
        if (
            self._termination_scope
            is DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
            and job_handle is not None
        ):
            _terminate_job(job_handle)
        else:
            proc.kill()

    def _terminate(self, proc, reason, job_handle=None, *, reap_fully=False):
        if proc.poll() is not None:
            return DaemonProcessExit(proc.returncode, reason)
        try:
            self._force_terminate(proc, job_handle)
        except OSError:
            pass
        if job_handle is not None:
            # Bounded whole-tree confirmation; POSIX killpg offers no stronger
            # receipt, so an expired wait degrades silently to the exact-child
            # reap below rather than blocking the caller.
            _wait_job_empty(job_handle, self._term_timeout)
        try:
            proc.wait(timeout=self._term_timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=self._kill_timeout)
            except subprocess.TimeoutExpired:
                if reap_fully:
                    # Callback failure owns no recoverable handle. Once the
                    # exact child has been force-killed, wait without a second
                    # bounded escape so the transaction cannot leak a zombie.
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    try:
                        proc.wait()
                    except OSError:
                        pass
        return DaemonProcessExit(proc.returncode, reason)

    def _cleanup_spawn_failure(self, handle) -> None:
        """Reap and forget a child whose observation transaction failed."""
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            return
        proc, _group, _reason, job_handle = entry
        try:
            self._terminate(proc, "observation-failed", job_handle, reap_fully=True)
        finally:
            with self._lock:
                self._handles.pop(handle, None)
            _close_job(job_handle)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def terminate(self, handle, *, reason=None):
        # Choose and persist the first local cause atomically, then perform the
        # bounded force/wait sequence outside the registry lock.
        with self._lock:
            try:
                proc, group, old_reason, job_handle = self._handles[handle]
            except (KeyError, TypeError) as exc:
                raise KeyError("unknown daemon process handle") from exc
            chosen_reason = old_reason if old_reason is not None else reason
            self._handles[handle] = (proc, group, chosen_reason, job_handle)
        return self._terminate(proc, chosen_reason, job_handle)

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
            if entry[0].poll() is None:
                return False
            self._handles.pop(handle, None)
        # The root child is reaped; closing the Job handle cannot kill any
        # still-running grandchildren (no KILL_ON_JOB_CLOSE) — it only lets
        # the kernel free the Job once its last member exits.
        _close_job(entry[3])
        return True


__all__ = ["WindowsDaemonProcessPort"]
