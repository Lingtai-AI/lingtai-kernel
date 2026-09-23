"""Owner-only ACP transport for an already running Agent.

Ordinary clients get generic same-user ACP. A connection-first Puffo attach
preface may bind Driver authority to that connection's turns, but session MCP
remains disabled until a concurrency-safe overlay exists.
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import socket
import stat
import struct
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from lingtai.adapters.acp.driver_authority import (
    DriverAuthorityClient,
    DriverDerivedLaunchAdmissionAdapter,
)
from lingtai.adapters.acp.puffo_v0 import PuffoV0RegistryError, resolve_runtime
from lingtai.adapters.acp.server import AcpStdioServer
from lingtai.kernel.execution_workspace import ExecutionWorkspace
from lingtai.kernel.provider_admission import ConnectionScopedProviderAdmissionPort


_ATTACH_FRAME_LIMIT = 8192
_FIRST_FRAME_LIMIT = 64 * 1024
_log = logging.getLogger(__name__)
_ATTACH_REJECTION_CODES = frozenset({
    "invalid_attach_frame",
    "authority_fd_missing",
    "authority_fd_count_invalid",
    "runtime_registry_unavailable",
    "runtime_agent_mismatch",
    "authority_binding_mismatch",
    "attach_frame_too_large",
    "resident_provider_gate_unavailable",
})


def _attach_rejection_code(exc: Exception) -> str:
    """Log only fixed codes; exception text may contain private paths or IDs."""
    if isinstance(exc, PuffoV0RegistryError):
        return "runtime_resolution_failed"
    if isinstance(exc, ValueError) and str(exc) in _ATTACH_REJECTION_CODES:
        return str(exc)
    return "authority_handshake_failed"


class _PrefixedLines:
    """Return bytes consumed while sniffing the first frame to ACP unchanged."""

    def __init__(self, prefix: bytes, stream):
        self._prefix = prefix
        self._stream = stream

    def __iter__(self):
        pending = self._prefix
        while pending:
            before, sep, after = pending.partition(b"\n")
            if sep:
                yield (before + sep).decode("utf-8", errors="strict")
                pending = after
            else:
                chunk = self._stream.readline()
                if not chunk:
                    yield pending.decode("utf-8", errors="strict")
                    return
                pending += chunk
        for line in self._stream:
            yield line.decode("utf-8", errors="strict")


def _first_frame(client: socket.socket) -> tuple[bytes, bytes, list[int]]:
    """Read one bounded line while retaining any ancillary descriptors."""
    import array

    received = bytearray()
    descriptors: list[int] = []
    itemsize = array.array("i").itemsize
    try:
        client.settimeout(2.0)
        while b"\n" not in received:
            data, ancillary, flags, _ = client.recvmsg(
                _FIRST_FRAME_LIMIT + 1, socket.CMSG_SPACE(itemsize * 2)
            )
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    fds = array.array("i")
                    fds.frombytes(payload[: len(payload) - len(payload) % itemsize])
                    descriptors.extend(fds)
            if not data or flags & socket.MSG_CTRUNC:
                raise ValueError("attach frame or ancillary data incomplete")
            received.extend(data)
            if len(received) > _FIRST_FRAME_LIMIT:
                raise ValueError("first ACP frame too large")
        first, remaining = bytes(received).split(b"\n", 1)
        return first + b"\n", remaining, descriptors
    except BaseException:
        for fd in descriptors:
            os.close(fd)
        raise
    finally:
        try:
            client.settimeout(None)
        except OSError:
            pass


def _attach_authority(frame: bytes, fds: list[int], agent_dir: Path):
    """Resolve durable identity and consume exactly one connection authority."""
    authority = None
    try:
        payload = json.loads(frame.decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"type", "runtime_id", "registry", "launch_id"}
            or payload["type"] != "puffo.attach/1"
            or any(
                not isinstance(payload[key], str) or not payload[key]
                for key in ("runtime_id", "registry", "launch_id")
            )
        ):
            raise ValueError("invalid_attach_frame")
        if not fds:
            raise ValueError("authority_fd_missing")
        if len(fds) != 1:
            raise ValueError("authority_fd_count_invalid")
        registry_path = Path(payload["registry"])
        if not registry_path.is_file():
            raise ValueError("runtime_registry_unavailable")
        runtime = resolve_runtime(payload["runtime_id"], registry_path=registry_path)
        if runtime.agent_dir.resolve() != agent_dir.resolve():
            raise ValueError("runtime_agent_mismatch")
        fd = fds.pop()
        authority = DriverAuthorityClient.from_inherited_fd(fd)
        if (
            authority.identity.role != "root"
            or authority.identity.launch_id != payload["launch_id"]
        ):
            raise ValueError("authority_binding_mismatch")
        return authority, ExecutionWorkspace(runtime.workspace)
    except (ValueError, TypeError, UnicodeError, PuffoV0RegistryError, OSError):
        if authority is not None:
            authority.close()
        raise
    finally:
        for fd in fds:
            os.close(fd)


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
        self._agent_dir = agent_dir.resolve()
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
        authority = None
        fixed_workspace = None
        try:
            if self._closed.is_set():
                return
            first, remainder, fds = _first_frame(client)
            try:
                candidate = json.loads(first.decode("utf-8"))
            except (ValueError, UnicodeError):
                candidate = None
            is_attach = (
                isinstance(candidate, dict)
                and candidate.get("type") == "puffo.attach/1"
            )
            if is_attach:
                try:
                    if len(first) > _ATTACH_FRAME_LIMIT:
                        for fd in fds:
                            os.close(fd)
                        raise ValueError("attach_frame_too_large")
                    if not isinstance(
                        getattr(self._agent, "_provider_call_admission_port", None),
                        ConnectionScopedProviderAdmissionPort,
                    ):
                        for fd in fds:
                            os.close(fd)
                        raise ValueError("resident_provider_gate_unavailable")
                    authority, fixed_workspace = _attach_authority(
                        first, fds, self._agent_dir
                    )
                except Exception as exc:
                    _log.warning("resident ACP attach rejected: %s", _attach_rejection_code(exc))
                    client.sendall(b'{"ok":false,"reason":"attach_rejected"}\n')
                    return
                try:
                    kernel_version = version("lingtai")
                except PackageNotFoundError:
                    kernel_version = "0+unknown"
                client.sendall(
                    (json.dumps({"ok": True, "kernel_version": kernel_version}) + "\n")
                    .encode("utf-8")
                )
                prefix = remainder
            else:
                for fd in fds:
                    os.close(fd)
                if fds:
                    return
                prefix = first + remainder
            reader = client.makefile("rb")
            writer = client.makefile("w", encoding="utf-8", errors="strict", newline="\n")
            session = AcpStdioServer(
                self._agent,
                _PrefixedLines(prefix, reader),
                writer,
                allow_session_mcp=False,
                fixed_execution_workspace=fixed_workspace,
                connection_provider_port=authority,
                connection_derived_port=(
                    DriverDerivedLaunchAdmissionAdapter(authority)
                    if authority is not None else None
                ),
            )
            with self._lock:
                if self._closed.is_set():
                    return
                self._session = session
            session.serve()
        except (OSError, UnicodeError, ValueError):
            pass
        finally:
            if session is not None:
                session.close()
            if authority is not None:
                authority.close()
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
