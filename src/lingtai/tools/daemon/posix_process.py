"""POSIX production adapter for the daemon process port."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from collections.abc import Callable

from lingtai.adapters.posix.process_identity import process_identity
from .process_port import (
    DaemonProcessCommand, DaemonProcessExit, DaemonProcessHandle,
    DaemonProcessObservation, DaemonProcessTerminationScope,
)
from .runtime import iter_stdout_with_deadline


class _Drain:
    def __init__(self, thread: threading.Thread, lines: list[str]) -> None:
        self._thread, self.lines = thread, lines

    def join(self, timeout: float = 2.0) -> None:
        self._thread.join(timeout=timeout)


class PosixDaemonProcessPort:
    """Owns POSIX Popen, pipes, sessions, groups, and bounded reaping."""

    def __init__(
        self, *, term_timeout: float = 5.0, kill_timeout: float = 3.0,
        start_new_session: bool = True,
        observation_callback: Callable[[DaemonProcessObservation], None] | None = None,
    ) -> None:
        if os.name != "posix":
            raise RuntimeError("daemon POSIX process adapter is unsupported on this platform")
        self._term_timeout = term_timeout
        self._kill_timeout = kill_timeout
        self._start_new_session = start_new_session
        self._termination_scope = (
            DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
            if start_new_session
            else DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP
        )
        self._observation_callback = observation_callback
        self._handles: dict[DaemonProcessHandle, tuple[subprocess.Popen, str | None, str | None, int | None]] = {}
        self._lock = threading.Lock()

    def set_observation_callback(
        self, callback: Callable[[DaemonProcessObservation], None] | None,
    ) -> None:
        """Install the owner callback for immediate post-spawn identity publication."""
        self._observation_callback = callback

    def _popen(self, command: DaemonProcessCommand) -> subprocess.Popen:
        env = dict(command.environment) if command.environment is not None else None
        return subprocess.Popen(
            tuple(command.argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(command.cwd), env=env,
            start_new_session=self._start_new_session,
        )

    def spawn(self, command, *, group_id=None):
        proc = self._popen(command)
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None
        handle = DaemonProcessHandle(object())
        with self._lock:
            self._handles[handle] = (proc, group_id, None, pgid)
        callback = self._observation_callback
        if callback is not None:
            try:
                callback(DaemonProcessObservation(
                    pid=proc.pid, pgid=pgid,
                    start_identity=process_identity(proc.pid),
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

    def _signal(self, proc, sig: signal.Signals, pgid: int | None) -> None:
        """Signal according to the explicit ownership scope, never PGID alone."""
        if self._termination_scope is DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP:
            target = pgid if isinstance(pgid, int) else proc.pid
            os.killpg(target, sig)
        else:
            # The detached execution host owns the inherited group. A Port
            # lifecycle operation is exact-child-only so it cannot kill the
            # host interpreter/caller or sibling handles.
            os.kill(proc.pid, sig)

    def _terminate(self, proc, reason, pgid=None, *, reap_fully=False):
        if proc.poll() is not None:
            return DaemonProcessExit(proc.returncode, reason)
        try:
            self._signal(proc, signal.SIGTERM, pgid)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=self._term_timeout)
        except subprocess.TimeoutExpired:
            try:
                self._signal(proc, signal.SIGKILL, pgid)
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=self._kill_timeout)
            except subprocess.TimeoutExpired:
                if reap_fully:
                    # Callback failure owns no recoverable handle. Once the
                    # exact child has been SIGKILLed, wait without a second
                    # bounded escape so the transaction cannot leak a zombie.
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
        proc, _group, _reason, pgid = entry
        try:
            self._terminate(proc, "observation-failed", pgid, reap_fully=True)
        finally:
            with self._lock:
                self._handles.pop(handle, None)
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
                proc, group, old_reason, pgid = self._handles[handle]
            except (KeyError, TypeError) as exc:
                raise KeyError("unknown daemon process handle") from exc
            chosen_reason = old_reason if old_reason is not None else reason
            self._handles[handle] = (proc, group, chosen_reason, pgid)
        return self._terminate(proc, chosen_reason, pgid)

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
            return True
