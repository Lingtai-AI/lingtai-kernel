"""Windows production adapter for the daemon process port."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Callable

from lingtai.adapters.windows.process_identity import process_identity
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


class WindowsDaemonProcessPort:
    """Owns Windows Popen, pipes, and bounded reaping.

    On Windows, process groups/sessions are not supported, so termination
    is always per-process (INHERITED_SUPERVISOR_GROUP scope).
    """

    def __init__(
        self, *, term_timeout: float = 5.0, kill_timeout: float = 3.0,
        start_new_session: bool = False,
        observation_callback: Callable[[DaemonProcessObservation], None] | None = None,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("daemon Windows process adapter is unsupported on this platform")
        self._term_timeout = term_timeout
        self._kill_timeout = kill_timeout
        self._termination_scope = DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP
        self._observation_callback = observation_callback
        self._handles: dict[DaemonProcessHandle, tuple[subprocess.Popen, str | None, str | None]] = {}
        self._lock = threading.Lock()

    def set_observation_callback(
        self, callback: Callable[[DaemonProcessObservation], None] | None,
    ) -> None:
        """Install the owner callback for immediate post-spawn identity publication."""
        self._observation_callback = callback

    def _popen(self, command: DaemonProcessCommand) -> subprocess.Popen:
        env = dict(command.environment) if command.environment is not None else None
        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(
            tuple(command.argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(command.cwd), env=env,
            creationflags=creationflags,
        )

    def spawn(self, command, *, group_id=None):
        proc = self._popen(command)
        handle = DaemonProcessHandle(object())
        with self._lock:
            self._handles[handle] = (proc, group_id, None)
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
        proc, _, reason = self._entry(handle)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError("daemon process wait deadline expired")
        with self._lock:
            current = self._handles.get(handle)
            if current is not None and current[0] is proc:
                reason = current[2]
        return DaemonProcessExit(proc.returncode, reason)

    def _terminate(self, proc, reason, *, reap_fully=False):
        if proc.poll() is not None:
            return DaemonProcessExit(proc.returncode, reason)
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (ProcessLookupError, OSError, ValueError):
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass
        try:
            proc.wait(timeout=self._term_timeout)
        except subprocess.TimeoutExpired:
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
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            return
        proc, _group, _reason = entry
        try:
            self._terminate(proc, "observation-failed", reap_fully=True)
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
        with self._lock:
            try:
                proc, group, old_reason = self._handles[handle]
            except (KeyError, TypeError) as exc:
                raise KeyError("unknown daemon process handle") from exc
            chosen_reason = old_reason if old_reason is not None else reason
            self._handles[handle] = (proc, group, chosen_reason)
        return self._terminate(proc, chosen_reason)

    def terminate_group(self, group_id, *, reason=None):
        with self._lock:
            handles = [h for h, (_, group, _) in self._handles.items() if group == group_id]
        for handle in handles:
            try:
                self.terminate(handle, reason=reason)
            except KeyError:
                continue
        return len(handles)

    def terminate_all(self, *, reason=None):
        with self._lock:
            handles = list(self._handles)
        for handle in handles:
            try:
                self.terminate(handle, reason=reason)
            except KeyError:
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
