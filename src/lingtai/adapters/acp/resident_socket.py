"""Owner-only local ACP transport for an already running Agent.

This is a generic same-user transport, not the Puffo profile.  In particular it
does not accept a Puffo runtime id or mount session MCP tools: those require a
separate authenticated, per-turn policy boundary before they can be shared with
a resident Agent.
"""
from __future__ import annotations

import errno
import hashlib
import os
import socket
import stat
import struct
import threading
from pathlib import Path

from lingtai.adapters.acp.server import AcpStdioServer


def resident_acp_socket_path(agent_dir: Path) -> Path:
    """Return a stable short path despite macOS's small AF_UNIX path limit."""
    if os.name != "posix":
        raise RuntimeError("resident ACP socket requires POSIX peer credentials")
    identity = str(agent_dir.resolve()).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return Path("/tmp") / f"lingtai-acp-{os.getuid()}" / f"{digest}.sock"


class ResidentAcpSocket:
    """Serve one local ACP session at a time without owning Agent lifecycle."""

    def __init__(self, agent, agent_dir: Path):
        self._agent = agent
        self.path = resident_acp_socket_path(agent_dir)
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._client: socket.socket | None = None
        self._session: AcpStdioServer | None = None
        self._socket_inode: int | None = None

    @staticmethod
    def _check_peer(client: socket.socket) -> bool:
        """Require the process owning this Agent, not merely a reachable socket."""
        if hasattr(socket, "SO_PEERCRED"):
            raw = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", raw)
            return uid == os.getuid()
        if hasattr(socket, "LOCAL_PEERCRED"):
            import ctypes

            uid = ctypes.c_uint()
            gid = ctypes.c_uint()
            if ctypes.CDLL(None).getpeereid(
                client.fileno(), ctypes.byref(uid), ctypes.byref(gid)
            ) != 0:
                return False
            return uid.value == os.getuid()
        return False

    def _clear_stale_socket(self) -> None:
        try:
            existing = self.path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
            raise RuntimeError(f"refusing to replace non-owned ACP socket: {self.path}")
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.settimeout(0.2)
            try:
                probe.connect(str(self.path))
            except OSError as exc:
                if exc.errno != errno.ECONNREFUSED:
                    raise RuntimeError(f"cannot verify stale ACP socket: {self.path}") from exc
            else:
                raise RuntimeError(f"ACP socket is already listening: {self.path}")
        finally:
            probe.close()
        if self.path.lstat().st_ino != existing.st_ino:
            raise RuntimeError(f"ACP socket changed during stale check: {self.path}")
        self.path.unlink()

    def start(self) -> None:
        if os.name != "posix":
            raise RuntimeError("resident ACP socket requires POSIX peer credentials")
        try:
            self.path.parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        directory = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
        ):
            raise RuntimeError(f"resident ACP socket directory is not owner-only: {self.path.parent}")
        self._clear_stale_socket()
        listener = socket.socket(socket.AF_UNIX)
        try:
            listener.bind(str(self.path))
            self._socket_inode = self.path.lstat().st_ino
            os.chmod(self.path, 0o600)
            listener.listen(1)
            listener.settimeout(0.2)
            self._listener = listener
            self._thread = threading.Thread(
                target=self._serve,
                daemon=True,
                name="resident-acp-listener",
            )
            self._thread.start()
        except BaseException:
            listener.close()
            self._remove_owned_socket()
            raise

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._closed.is_set() and not self._agent._shutdown.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                allowed = self._check_peer(client)
            except Exception:
                allowed = False
            with self._lock:
                busy = self._client is not None
                if allowed and not busy and not self._closed.is_set():
                    self._client = client
            if not allowed or busy or self._closed.is_set():
                client.close()
                continue
            threading.Thread(
                target=self._serve_client,
                args=(client,),
                daemon=True,
                name="resident-acp-session",
            ).start()

    def _serve_client(self, client: socket.socket) -> None:
        reader = None
        writer = None
        session = None
        try:
            if self._closed.is_set():
                return
            reader = client.makefile("r", encoding="utf-8", errors="strict")
            writer = client.makefile("w", encoding="utf-8", errors="strict", newline="\n")
            session = AcpStdioServer(self._agent, reader, writer, allow_session_mcp=False)
            with self._lock:
                if self._closed.is_set():
                    return
                self._session = session
            session.serve()
        except (OSError, UnicodeError):
            pass
        finally:
            if session is not None:
                session.close()
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if reader is not None:
                reader.close()
            if writer is not None:
                writer.close()
            client.close()
            with self._lock:
                if self._client is client:
                    self._client = None
                    self._session = None

    def _remove_owned_socket(self) -> None:
        if self._socket_inode is None:
            return
        try:
            if self.path.lstat().st_ino == self._socket_inode:
                self.path.unlink()
        except FileNotFoundError:
            pass

    def close(self) -> None:
        self._closed.set()
        listener = self._listener
        if listener is not None:
            listener.close()
        with self._lock:
            session, client = self._session, self._client
        if session is not None:
            session.close()
        if client is not None:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._remove_owned_socket()
