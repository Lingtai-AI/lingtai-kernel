"""POSIX adapter for the daemon-local interactive terminal Port."""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import threading
import termios
import time
from pathlib import Path
from collections.abc import Callable

from lingtai.adapters.posix.process_identity import process_identity
from lingtai.tools.daemon.process_port import (
    DaemonProcessObservation,
    DaemonProcessTerminationScope,
)
from lingtai.tools.daemon.interactive_terminal import (
    InteractiveTerminalCommand,
    InteractiveTerminalExit,
    InteractiveTerminalHandle,
)


@dataclass
class _Entry:
    process: subprocess.Popen
    master_fd: int
    group_id: str | None
    reason: str | None = None
    pgid: int | None = None


class PosixInteractiveTerminalAdapter:
    """Own real POSIX PTYs, process sessions, byte I/O, and bounded reaping."""

    def __init__(
        self, *, term_timeout: float = 2.0, kill_timeout: float = 1.0,
        start_new_session: bool = True,
        observation_callback: Callable[[DaemonProcessObservation], None] | None = None,
    ) -> None:
        if os.name != "posix":
            raise RuntimeError(
                "daemon POSIX interactive terminal adapter is unsupported on this platform"
            )
        self._term_timeout = term_timeout
        self._kill_timeout = kill_timeout
        self._start_new_session = start_new_session
        self._termination_scope = (
            DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
            if start_new_session
            else DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP
        )
        self._observation_callback = observation_callback
        self._handles: dict[InteractiveTerminalHandle, _Entry] = {}
        self._lock = threading.Lock()

    def set_observation_callback(
        self, callback: Callable[[DaemonProcessObservation], None] | None,
    ) -> None:
        """Install the detached-owner identity publication callback."""
        self._observation_callback = callback

    @staticmethod
    def _set_size(fd: int, command: InteractiveTerminalCommand) -> None:
        # winsize is rows, columns, vertical pixels, horizontal pixels.
        fcntl.ioctl(
            fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", command.rows, command.columns, 0, 0),
        )

    @staticmethod
    def _entry_process(entry: _Entry) -> subprocess.Popen:
        return entry.process

    def spawn(
        self,
        command: InteractiveTerminalCommand,
        *,
        group_id: str | None = None,
    ) -> InteractiveTerminalHandle:
        if not isinstance(command, InteractiveTerminalCommand):
            raise TypeError("interactive terminal spawn requires InteractiveTerminalCommand")
        env = dict(command.environment) if command.environment is not None else None
        master_fd, slave_fd = pty.openpty()
        try:
            self._set_size(slave_fd, command)
            process = subprocess.Popen(
                command.argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(command.cwd),
                env=env,
                start_new_session=self._start_new_session,
                close_fds=True,
            )
        except BaseException:
            try:
                os.close(slave_fd)
            finally:
                os.close(master_fd)
            raise
        os.close(slave_fd)
        try:
            pgid = os.getpgid(process.pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None
        handle = InteractiveTerminalHandle(object())
        with self._lock:
            self._handles[handle] = _Entry(
                process=process,
                master_fd=master_fd,
                group_id=group_id,
                pgid=pgid,
            )
        callback = self._observation_callback
        if callback is not None:
            try:
                callback(DaemonProcessObservation(
                    pid=process.pid, pgid=pgid,
                    start_identity=process_identity(process.pid),
                    termination_scope=self._termination_scope,
                ))
            except BaseException:
                self._cleanup_spawn_failure(handle)
                raise
        return handle

    def _get(self, handle: InteractiveTerminalHandle) -> _Entry:
        with self._lock:
            try:
                return self._handles[handle]
            except (KeyError, TypeError) as exc:
                raise KeyError("unknown interactive terminal handle") from exc

    def read(
        self,
        handle: InteractiveTerminalHandle,
        *,
        deadline: float | None = None,
    ):
        """Yield available raw chunks until this bounded read call has no data."""
        entry = self._get(handle)
        while True:
            if deadline is None:
                timeout = None
            else:
                timeout = max(0.0, deadline - time.monotonic())
                if timeout == 0.0:
                    return
            try:
                ready, _, _ = select.select([entry.master_fd], [], [], timeout)
            except (OSError, ValueError):
                yield b""
                return
            if not ready:
                return
            try:
                data = os.read(entry.master_fd, 8192)
            except OSError as exc:
                # Linux/macOS report PTY slave closure as EIO rather than an
                # ordinary zero-length read. Both are terminal EOF to policy.
                if getattr(exc, "errno", None) in (5,):
                    yield b""
                    return
                raise
            if not data:
                yield b""
                return
            yield data
            # A Port read is intentionally bounded to one ready turn. The
            # bridge re-enters it so cancellation and hook events stay live.
            return

    def write(self, handle: InteractiveTerminalHandle, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("interactive terminal writes require bytes")
        entry = self._get(handle)
        view = memoryview(data)
        while view:
            written = os.write(entry.master_fd, view)
            view = view[written:]

    def wait(
        self,
        handle: InteractiveTerminalHandle,
        *,
        timeout: float | None = None,
    ) -> InteractiveTerminalExit:
        entry = self._get(handle)
        try:
            entry.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("interactive terminal wait deadline expired") from exc
        with self._lock:
            current = self._handles.get(handle)
            reason = current.reason if current is not None else entry.reason
        return InteractiveTerminalExit(entry.process.returncode, reason)

    def _signal(self, process: subprocess.Popen, sig: signal.Signals, pgid: int | None) -> None:
        """Apply only the termination scope this adapter explicitly owns."""
        if self._termination_scope is DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP:
            target = pgid if isinstance(pgid, int) else process.pid
            os.killpg(target, sig)
        else:
            # Detached execution inherits the supervisor group; a terminal
            # handle may signal only its exact child, never that shared group.
            os.kill(process.pid, sig)

    def _terminate_process(
        self,
        entry: _Entry,
        reason: str | None,
        *,
        reap_fully: bool = False,
    ) -> InteractiveTerminalExit:
        process = entry.process
        if process.poll() is None:
            try:
                self._signal(process, signal.SIGTERM, entry.pgid)
            except (ProcessLookupError, OSError):
                pass
            try:
                process.wait(timeout=self._term_timeout)
            except subprocess.TimeoutExpired:
                try:
                    self._signal(process, signal.SIGKILL, entry.pgid)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    process.wait(timeout=self._kill_timeout)
                except subprocess.TimeoutExpired:
                    if reap_fully:
                        try:
                            process.kill()
                        except (ProcessLookupError, OSError):
                            pass
                        try:
                            process.wait()
                        except (ProcessLookupError, OSError):
                            pass
        return InteractiveTerminalExit(process.returncode, reason)

    def _cleanup_spawn_failure(self, handle) -> None:
        """Reap the exact PTY child, remove its registry entry, and close master."""
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            return
        try:
            self._terminate_process(entry, "observation-failed", reap_fully=True)
        finally:
            with self._lock:
                self._handles.pop(handle, None)
            try:
                os.close(entry.master_fd)
            except OSError:
                pass

    def terminate(
        self,
        handle: InteractiveTerminalHandle,
        *,
        reason: str | None = None,
    ) -> InteractiveTerminalExit:
        with self._lock:
            try:
                entry = self._handles[handle]
            except (KeyError, TypeError) as exc:
                raise KeyError("unknown interactive terminal handle") from exc
            if entry.reason is None and reason is not None:
                entry.reason = reason
            chosen_reason = entry.reason
        return self._terminate_process(entry, chosen_reason)

    def terminate_group(self, group_id: str, *, reason: str | None = None) -> int:
        with self._lock:
            handles = [
                handle for handle, entry in self._handles.items()
                if entry.group_id == group_id
            ]
        for handle in handles:
            try:
                self.terminate(handle, reason=reason)
            except KeyError:
                continue
        return len(handles)

    def terminate_all(self, *, reason: str | None = None) -> int:
        with self._lock:
            handles = list(self._handles)
        for handle in handles:
            try:
                self.terminate(handle, reason=reason)
            except KeyError:
                continue
        return len(handles)

    def release(self, handle: InteractiveTerminalHandle) -> bool:
        with self._lock:
            entry = self._handles.get(handle)
            if entry is None:
                return True
            if entry.process.poll() is None:
                return False
            self._handles.pop(handle, None)
        try:
            os.close(entry.master_fd)
        except OSError:
            pass
        return True


__all__ = ["PosixInteractiveTerminalAdapter"]
