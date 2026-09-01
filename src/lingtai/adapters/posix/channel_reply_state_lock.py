"""POSIX cross-process mutation session for channel_reply state."""

from __future__ import annotations

import contextlib
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import os
from pathlib import Path
import secrets
import stat
import struct
import sys

from lingtai.kernel.channel_reply._mutation_lock import (
    AtomicWriteResult,
    ChannelReplyDirectoryToken,
    ChannelReplyEntryInfo,
    ChannelReplyExpectedRootMismatch,
    ChannelReplyObjectIdentity,
    ChannelReplyRootIdentity,
    DirectoryScanBatch,
    DirectoryScanBudget,
    DirectoryScanCursor,
    MoveDisposition,
    MoveResult,
    OwnedRemovalBudget,
    OwnedRemovalResult,
    WriteMode,
)

_LOCK_FILE = ".channel-reply.lock"
_SESSION_MARKER = "channel-reply-mutation-session/v1"
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_RENAME_EXCL = 0x00000004
_RENAME_NOREPLACE = 1
_NO_REPLACE_PROBE_SOURCE = ".channel-reply-no-replace-capability-source"
_NO_REPLACE_PROBE_DESTINATION = ".channel-reply-no-replace-capability-destination"


def _rename_no_replace(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
) -> None:
    """Rename one descriptor-relative entry without replacing a destination."""

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        primitive = getattr(libc, "renameatx_np", None)
        if primitive is None:
            raise OSError(errno.ENOSYS, "renameatx_np unavailable")
        primitive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        primitive.restype = ctypes.c_int
        flags = _RENAME_EXCL
    elif sys.platform.startswith("linux"):
        primitive = getattr(libc, "renameat2", None)
        if primitive is None:
            raise OSError(errno.ENOSYS, "renameat2 unavailable")
        primitive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        primitive.restype = ctypes.c_int
        flags = _RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, f"no descriptor-relative no-replace rename on {sys.platform}")

    ctypes.set_errno(0)
    result = primitive(
        source_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(destination_name),
        flags,
    )
    if result != 0:
        code = ctypes.get_errno()
        if code == 0:
            raise OSError(errno.EIO, "no-replace rename failed without reporting errno", destination_name)
        raise OSError(code, os.strerror(code), destination_name)


def _require_no_replace_rename() -> None:
    """Fail before session acquisition unless the native kernel primitive exists."""

    try:
        _rename_no_replace(
            -1,
            _NO_REPLACE_PROBE_SOURCE,
            -1,
            _NO_REPLACE_PROBE_DESTINATION,
        )
    except OSError as exc:
        if exc.errno == errno.EBADF:
            return
        code = exc.errno if exc.errno is not None else errno.ENOSYS
        raise OSError(code, "POSIX channel_reply session requires native no-replace rename") from exc
    raise OSError(errno.EIO, "no-replace rename capability probe unexpectedly mutated state")


def _require_primitives() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OSError("POSIX channel_reply session requires no-follow directory primitives")
    dir_fd_required = (os.open, os.stat, os.mkdir, os.unlink, os.rmdir, os.rename, os.link)
    if any(func not in os.supports_dir_fd for func in dir_fd_required):
        raise OSError("POSIX channel_reply session requires dir_fd primitives")
    if os.stat not in os.supports_follow_symlinks:
        raise OSError("POSIX channel_reply session requires no-follow stat")
    if os.link not in os.supports_follow_symlinks:
        raise OSError("POSIX channel_reply session requires no-follow hard-link publication")


def _require_descriptor_scandir(directory_fd: int) -> None:
    duplicated = os.dup(directory_fd)
    try:
        iterator = os.scandir(duplicated)
        try:
            pass
        finally:
            iterator.close()
    finally:
        try:
            os.close(duplicated)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise


def _close_fd_allow_ebadf(fd: int) -> None:
    try:
        os.close(fd)
    except OSError as exc:
        if exc.errno != errno.EBADF:
            raise


def _current_euid() -> int | None:
    return os.geteuid() if hasattr(os, "geteuid") else None


def _identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _object_identity(st: os.stat_result) -> ChannelReplyObjectIdentity:
    return ChannelReplyObjectIdentity("posix", st.st_dev, st.st_ino)


def _same_object(left: os.stat_result, right: os.stat_result) -> bool:
    return _identity(left) == _identity(right)


def _validate_component_name(name: str) -> None:
    if not isinstance(name, str):
        raise TypeError("channel_reply entry name must be a string")
    if name in {"", ".", ".."}:
        raise ValueError("channel_reply entry name must be one non-dot component")
    if "/" in name or "\\" in name or "\x00" in name or ":" in name:
        raise ValueError("channel_reply entry name must not contain separators or drive/ADS syntax")
    if name.startswith("//") or name.startswith("\\\\"):
        raise ValueError("channel_reply entry name must not be UNC syntax")


def _require_private_root(st: os.stat_result) -> None:
    if not stat.S_ISDIR(st.st_mode):
        raise OSError("channel_reply state root must be an existing directory")
    if stat.S_IMODE(st.st_mode) != _PRIVATE_DIR_MODE:
        raise OSError("channel_reply state root must be private")
    euid = _current_euid()
    if euid is not None and st.st_uid != euid:
        raise OSError("channel_reply state root must be owned by current user")


def _require_private_directory(st: os.stat_result) -> None:
    if not stat.S_ISDIR(st.st_mode):
        raise OSError("channel_reply entry must be a directory")
    if stat.S_IMODE(st.st_mode) != _PRIVATE_DIR_MODE:
        raise OSError("channel_reply directory must be private")
    euid = _current_euid()
    if euid is not None and st.st_uid != euid:
        raise OSError("channel_reply directory must be owned by current user")


def _require_private_lock(st: os.stat_result) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise OSError("channel_reply lock leaf must be regular")
    if getattr(st, "st_nlink", 1) != 1:
        raise OSError("channel_reply lock leaf must not be hard-linked")
    if stat.S_IMODE(st.st_mode) != _PRIVATE_FILE_MODE:
        raise OSError("channel_reply lock leaf must be private")
    euid = _current_euid()
    if euid is not None and st.st_uid != euid:
        raise OSError("channel_reply lock leaf must be owned by current user")


def _entry_kind(st: os.stat_result) -> str:
    if stat.S_ISREG(st.st_mode):
        return "regular"
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return "other"


def _entry_info(name: str, st: os.stat_result, *, root_dev: int) -> ChannelReplyEntryInfo:
    euid = _current_euid()
    kind = _entry_kind(st)
    private_access = False
    if kind == "regular":
        private_access = stat.S_IMODE(st.st_mode) == _PRIVATE_FILE_MODE
    elif kind == "directory":
        private_access = stat.S_IMODE(st.st_mode) == _PRIVATE_DIR_MODE
    return ChannelReplyEntryInfo(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        identity=_object_identity(st),
        size=st.st_size,
        link_count=getattr(st, "st_nlink", 1),
        private_owner=euid is None or st.st_uid == euid,
        private_access=private_access,
        same_root_filesystem=st.st_dev == root_dev,
    )


def _identity_matches(identity: ChannelReplyObjectIdentity, st: os.stat_result) -> bool:
    return identity.scheme == "posix" and identity.volume == st.st_dev and identity.object_id == st.st_ino


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short channel_reply state write")
        view = view[written:]


def _private_staging_name(prefix: str) -> str:
    return f".{prefix}.{os.getpid()}.{secrets.token_hex(16)}.tmp"


@dataclass(slots=True)
class _PosixDirectoryToken:
    session_nonce: str
    fd: int
    identity: ChannelReplyObjectIdentity
    root_dev: int
    parent_fd: int | None = None
    name: str | None = None
    closed: bool = False


class _PosixChannelReplyMutationSession:
    protocol_marker = _SESSION_MARKER

    def __init__(
        self,
        *,
        state_dir: Path,
        root_fd: int,
        root_st: os.stat_result,
        lock_fd: int,
        lock_st: os.stat_result,
    ) -> None:
        self._state_dir = state_dir
        self._root_fd = root_fd
        self._lock_fd = lock_fd
        self._root_st = root_st
        self._lock_st = lock_st
        self._nonce = secrets.token_hex(16)
        self._closed = False
        self._tokens: list[_PosixDirectoryToken] = []
        self.root_identity = ChannelReplyRootIdentity("posix", root_st.st_dev, root_st.st_ino)
        self.root = _PosixDirectoryToken(
            session_nonce=self._nonce,
            fd=root_fd,
            identity=self.root_identity,
            root_dev=root_st.st_dev,
        )
        self._tokens.append(self.root)

    def close_tokens(self) -> None:
        self._closed = True
        for token in reversed(self._tokens):
            if token is self.root:
                token.closed = True
                continue
            if not token.closed:
                token.closed = True
                try:
                    os.close(token.fd)
                except OSError:
                    pass

    def verify(self) -> None:
        if self._closed:
            raise OSError("channel_reply mutation session is closed")
        root_open = os.fstat(self._root_fd)
        _require_private_root(root_open)
        if not _same_object(root_open, self._root_st):
            raise OSError("channel_reply root descriptor identity changed")
        named_root = os.stat(self._state_dir, follow_symlinks=False)
        _require_private_root(named_root)
        if not _same_object(named_root, self._root_st):
            raise OSError("channel_reply named root replaced")
        lock_open = os.fstat(self._lock_fd)
        _require_private_lock(lock_open)
        if not _same_object(lock_open, self._lock_st):
            raise OSError("channel_reply lock descriptor identity changed")
        lock_leaf = os.stat(_LOCK_FILE, dir_fd=self._root_fd, follow_symlinks=False)
        _require_private_lock(lock_leaf)
        if not _same_object(lock_leaf, self._lock_st):
            raise OSError("channel_reply lock leaf replaced")

    def _unlink_name_and_fsync(self, parent_fd: int, name: str, *, missing_ok: bool = False) -> None:
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        os.fsync(parent_fd)

    def _restore_hidden_no_replace(
        self,
        parent_fd: int,
        hidden_name: str,
        canonical_name: str,
        *,
        operation: str,
    ) -> None:
        try:
            _rename_no_replace(parent_fd, hidden_name, parent_fd, canonical_name)
        except FileExistsError as exc:
            raise OSError(
                errno.EEXIST,
                f"channel_reply {operation} collision; canonical and hidden entries retained",
                canonical_name,
            ) from exc
        except OSError as exc:
            code = exc.errno if exc.errno is not None else errno.EIO
            raise OSError(
                code,
                f"channel_reply {operation} failed; hidden entry retained",
                canonical_name,
            ) from exc
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            code = exc.errno if exc.errno is not None else errno.EIO
            raise OSError(
                code,
                f"channel_reply {operation} durability ambiguous",
                canonical_name,
            ) from exc

    def _token(self, token: ChannelReplyDirectoryToken) -> _PosixDirectoryToken:
        if not isinstance(token, _PosixDirectoryToken):
            raise TypeError("channel_reply directory token is not POSIX-backed")
        if token.session_nonce != self._nonce or token.closed or self._closed:
            raise OSError("channel_reply directory token is stale or foreign")
        self.verify()
        opened = os.fstat(token.fd)
        if not _identity_matches(token.identity, opened):
            raise OSError("channel_reply directory token descriptor changed")
        _require_private_directory(opened)
        if opened.st_dev != token.root_dev or token.root_dev != self._root_st.st_dev:
            raise OSError("channel_reply directory token escaped root filesystem")
        if token.parent_fd is not None and token.name is not None:
            current = os.stat(token.name, dir_fd=token.parent_fd, follow_symlinks=False)
            _require_private_directory(current)
            if not _identity_matches(token.identity, current):
                raise OSError("channel_reply directory token name replaced")
        return token

    def open_directory(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        *,
        create_private: bool = False,
    ) -> ChannelReplyDirectoryToken:
        try:
            _validate_component_name(name)
            parent_token = self._token(parent)
            created = False
            try:
                before = os.stat(name, dir_fd=parent_token.fd, follow_symlinks=False)
            except FileNotFoundError:
                if not create_private:
                    raise
                os.mkdir(name, _PRIVATE_DIR_MODE, dir_fd=parent_token.fd)
                created = True
                before = os.stat(name, dir_fd=parent_token.fd, follow_symlinks=False)
            _require_private_directory(before)
            if before.st_dev != self._root_st.st_dev:
                raise OSError("channel_reply directory is on a different device")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(name, flags, dir_fd=parent_token.fd)
            try:
                opened = os.fstat(fd)
                _require_private_directory(opened)
                if not _same_object(opened, before):
                    raise OSError("channel_reply directory changed during open")
                if created:
                    os.fchmod(fd, _PRIVATE_DIR_MODE)
                    os.fsync(fd)
                    os.fsync(parent_token.fd)
                after = os.stat(name, dir_fd=parent_token.fd, follow_symlinks=False)
                if not _same_object(opened, after):
                    raise OSError("channel_reply directory replaced during open")
            except BaseException:
                os.close(fd)
                raise
            token = _PosixDirectoryToken(
                session_nonce=self._nonce,
                fd=fd,
                identity=_object_identity(opened),
                root_dev=self._root_st.st_dev,
                parent_fd=parent_token.fd,
                name=name,
            )
            self._tokens.append(token)
            return token
        finally:
            self.verify()

    def inspect(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
    ) -> ChannelReplyEntryInfo | None:
        try:
            _validate_component_name(name)
            parent_token = self._token(parent)
            try:
                st = os.stat(name, dir_fd=parent_token.fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            return _entry_info(name, st, root_dev=self._root_st.st_dev)
        finally:
            self.verify()

    def scan(
        self,
        directory: ChannelReplyDirectoryToken,
        *,
        budget: DirectoryScanBudget,
        cursor: DirectoryScanCursor | None = None,
    ) -> DirectoryScanBatch:
        """Inventory one bounded page using a directory-FD continuation on Darwin.

        Darwin's directory cookie, rather than lexical entry order, is persisted in
        the cursor.  A small native read can return several short names; unconsumed
        names are carried in the opaque cursor and receive no-follow metadata
        inspection only when charged to a later page.
        """
        try:
            token = self._token(directory)
            if budget.inspections < 0 or budget.candidates < 0:
                raise ValueError("channel_reply scan budget must be non-negative")
            if cursor is not None and (
                cursor.scheme != token.identity.scheme
                or cursor.volume != token.identity.volume
                or cursor.object_id != token.identity.object_id
                or cursor.position < 0
            ):
                raise OSError("channel_reply directory scan cursor identity mismatch")
            if budget.inspections == 0 or budget.candidates == 0:
                return DirectoryScanBatch((), 0, False, cursor or DirectoryScanCursor(
                    token.identity.scheme,
                    token.identity.volume,
                    token.identity.object_id,
                    0,
                ))
            if sys.platform == "darwin":
                return self._scan_darwin(token, budget=budget, cursor=cursor)

            # The production feature is Darwin-only. Retain a bounded one-shot
            # fallback for POSIX test hosts, without claiming resumability there.
            if cursor is not None:
                raise OSError("channel_reply resumable directory scan requires Darwin")
            entries: list[ChannelReplyEntryInfo] = []
            inspections = 0
            complete = True
            scan_fd = os.dup(token.fd)
            try:
                with os.scandir(scan_fd) as iterator:
                    for entry in iterator:
                        if inspections >= budget.inspections or len(entries) >= budget.candidates:
                            complete = False
                            break
                        inspections += 1
                        try:
                            st = os.stat(entry.name, dir_fd=token.fd, follow_symlinks=False)
                        except OSError:
                            continue
                        entries.append(_entry_info(entry.name, st, root_dev=self._root_st.st_dev))
            finally:
                if scan_fd >= 0:
                    _close_fd_allow_ebadf(scan_fd)
            return DirectoryScanBatch(tuple(entries), inspections, complete)
        finally:
            self.verify()

    def _scan_darwin(
        self,
        token: _PosixDirectoryToken,
        *,
        budget: DirectoryScanBudget,
        cursor: DirectoryScanCursor | None,
    ) -> DirectoryScanBatch:
        libc = ctypes.CDLL(None, use_errno=True)
        primitive = getattr(libc, "getdirentries", None)
        if primitive is None:
            raise OSError(errno.ENOSYS, "getdirentries unavailable")
        primitive.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_longlong),
        ]
        primitive.restype = ctypes.c_int

        position = cursor.position if cursor is not None else 0
        pending = list(cursor.pending if cursor is not None else ())
        entries: list[ChannelReplyEntryInfo] = []
        inspections = 0
        eof = False
        scan_fd = os.dup(token.fd)
        try:
            os.lseek(scan_fd, position, os.SEEK_SET)
            while inspections < budget.inspections and len(entries) < budget.candidates:
                if not pending:
                    # One maximum-size Darwin dirent plus alignment. A directory
                    # block may contain several tiny names; the bounded remainder
                    # is carried in ``pending`` rather than inspected eagerly.
                    buffer = ctypes.create_string_buffer(272)
                    base = ctypes.c_longlong(position)
                    ctypes.set_errno(0)
                    count = primitive(scan_fd, buffer, len(buffer), ctypes.byref(base))
                    if count < 0:
                        code = ctypes.get_errno() or errno.EIO
                        raise OSError(code, os.strerror(code))
                    position = os.lseek(scan_fd, 0, os.SEEK_CUR)
                    if count == 0:
                        eof = True
                        break
                    offset = 0
                    raw = buffer.raw[:count]
                    while offset + 8 <= len(raw):
                        _inode, record_length, _kind, name_length = struct.unpack_from(
                            "=I H B B", raw, offset
                        )
                        if record_length < 8 or offset + record_length > len(raw):
                            raise OSError(errno.EIO, "invalid Darwin directory record")
                        encoded = raw[offset + 8 : offset + 8 + name_length]
                        name = os.fsdecode(encoded)
                        if name not in {".", ".."}:
                            pending.append(name)
                        offset += record_length
                    if offset != len(raw):
                        raise OSError(errno.EIO, "truncated Darwin directory record")
                    if not pending:
                        continue
                name = pending.pop(0)
                inspections += 1
                try:
                    st = os.stat(name, dir_fd=token.fd, follow_symlinks=False)
                except OSError:
                    continue
                entries.append(_entry_info(name, st, root_dev=self._root_st.st_dev))
        finally:
            _close_fd_allow_ebadf(scan_fd)

        complete = eof and not pending
        next_cursor = None if complete else DirectoryScanCursor(
            token.identity.scheme,
            token.identity.volume,
            token.identity.object_id,
            position,
            tuple(pending),
        )
        return DirectoryScanBatch(tuple(entries), inspections, complete, next_cursor)

    def read_bytes(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        *,
        max_bytes: int,
        expected: ChannelReplyObjectIdentity | None = None,
    ) -> bytes:
        try:
            if max_bytes < 0:
                raise ValueError("channel_reply read limit must be non-negative")
            _validate_component_name(name)
            parent_token = self._token(parent)
            before = os.stat(name, dir_fd=parent_token.fd, follow_symlinks=False)
            info = _entry_info(name, before, root_dev=self._root_st.st_dev)
            if not info.private_regular_single_link:
                raise OSError("channel_reply entry is not a private regular single-link file")
            if expected is not None and not _identity_matches(expected, before):
                raise OSError("channel_reply entry identity mismatch")
            expected_size = before.st_size
            if expected_size > max_bytes:
                raise ValueError("channel_reply entry exceeds read limit")
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(name, flags, dir_fd=parent_token.fd)
            try:
                opened = os.fstat(fd)
                if not _same_object(opened, before):
                    raise OSError("channel_reply entry changed during read open")
                opened_info = _entry_info(name, opened, root_dev=self._root_st.st_dev)
                if not opened_info.private_regular_single_link:
                    raise OSError("channel_reply opened entry is not private regular single-link")
                if opened.st_size != expected_size:
                    raise OSError("channel_reply entry size changed during read open")
                chunks: list[bytes] = []
                remaining = expected_size
                while remaining:
                    chunk = os.read(fd, remaining)
                    if chunk == b"":
                        raise OSError("channel_reply entry ended before expected size")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(fd, 1) != b"":
                    raise ValueError("channel_reply entry exceeds read limit")
                data = b"".join(chunks)
                after = os.stat(name, dir_fd=parent_token.fd, follow_symlinks=False)
                if not _same_object(opened, after):
                    raise OSError("channel_reply entry changed during read")
                if after.st_size != expected_size:
                    raise OSError("channel_reply entry size changed during read")
                return data
            finally:
                os.close(fd)
        finally:
            self.verify()

    def atomic_write_bytes(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        data: bytes,
        *,
        mode: WriteMode,
    ) -> AtomicWriteResult:
        fd = -1
        hidden_created = False
        parent_token: _PosixDirectoryToken | None = None
        hidden = ""
        try:
            _validate_component_name(name)
            parent_token = self._token(parent)
            hidden = _private_staging_name(name)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(hidden, flags, _PRIVATE_FILE_MODE, dir_fd=parent_token.fd)
            hidden_created = True
            state = "created"
            try:
                _write_all(fd, data)
                os.fchmod(fd, _PRIVATE_FILE_MODE)
                os.fsync(fd)
                hidden_st = os.fstat(fd)
                _require_private_lock(hidden_st)
                if hidden_st.st_dev != self._root_st.st_dev:
                    raise OSError("channel_reply temp escaped root filesystem")
            finally:
                if fd >= 0:
                    os.close(fd)
                    fd = -1
            if mode == "atomic-replace":
                try:
                    os.stat(name, dir_fd=parent_token.fd, follow_symlinks=False)
                    state = "replaced"
                except FileNotFoundError:
                    state = "created"
                os.rename(hidden, name, src_dir_fd=parent_token.fd, dst_dir_fd=parent_token.fd)
                hidden_created = False
                os.fsync(parent_token.fd)
            elif mode == "atomic-create-hard-link":
                try:
                    os.link(hidden, name, src_dir_fd=parent_token.fd, dst_dir_fd=parent_token.fd, follow_symlinks=False)
                except FileExistsError:
                    state = "exists"
                else:
                    state = "created"
                os.fsync(parent_token.fd)
                try:
                    os.unlink(hidden, dir_fd=parent_token.fd)
                    hidden_created = False
                    os.fsync(parent_token.fd)
                except FileNotFoundError:
                    hidden_created = False
                    pass
            else:
                raise ValueError("unsupported channel_reply atomic write mode")
            if mode == "atomic-replace":
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(hidden, dir_fd=parent_token.fd)
            entry = self.inspect(parent_token, name)
            return AtomicWriteResult(state, entry)
        except BaseException as exc:
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
            if hidden_created and parent_token is not None:
                try:
                    os.unlink(hidden, dir_fd=parent_token.fd)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_exc:
                    raise OSError("channel_reply atomic write cleanup ambiguous") from cleanup_exc
                else:
                    try:
                        os.fsync(parent_token.fd)
                    except OSError as cleanup_exc:
                        raise OSError("channel_reply atomic write cleanup durability ambiguous") from cleanup_exc
            raise exc
        finally:
            self.verify()

    def move_entry(
        self,
        source_parent: ChannelReplyDirectoryToken,
        source_name: str,
        destination_parent: ChannelReplyDirectoryToken,
        destination_name: str,
        *,
        expected_source: ChannelReplyObjectIdentity,
        disposition: MoveDisposition,
    ) -> MoveResult:
        try:
            _validate_component_name(source_name)
            _validate_component_name(destination_name)
            source_token = self._token(source_parent)
            destination_token = self._token(destination_parent)
            try:
                source_st = os.stat(source_name, dir_fd=source_token.fd, follow_symlinks=False)
            except FileNotFoundError:
                return MoveResult("source-absent", None)
            source_info = _entry_info(source_name, source_st, root_dev=self._root_st.st_dev)
            if not source_info.private_regular_single_link:
                return MoveResult("rejected", None)
            if not _identity_matches(expected_source, source_st):
                return MoveResult("source-changed", None)
            if disposition == "destination-must-be-absent":
                return self._move_absent_destination(
                    source_token,
                    source_name,
                    destination_token,
                    destination_name,
                    expected_source,
                )
            if disposition == "replace-destination-entry":
                return self._move_replace_destination(
                    source_token,
                    source_name,
                    destination_token,
                    destination_name,
                    expected_source,
                )
            raise ValueError("unsupported channel_reply move disposition")
        finally:
            self.verify()

    def _move_absent_destination(
        self,
        source_token: _PosixDirectoryToken,
        source_name: str,
        destination_token: _PosixDirectoryToken,
        destination_name: str,
        expected_source: ChannelReplyObjectIdentity,
    ) -> MoveResult:
        staging = _private_staging_name(source_name)
        removal = _private_staging_name(f"{source_name}.remove")
        staged = False
        removal_exists = False
        recovery_started = False
        compensation_started = False
        linearized = False

        def cleanup_staging() -> None:
            nonlocal staged
            if not staged:
                return
            os.unlink(staging, dir_fd=source_token.fd)
            staged = False
            os.fsync(source_token.fd)

        def canonical_name_holds_expected(parent_fd: int, name: str) -> bool:
            try:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            except OSError as exc:
                code = exc.errno if exc.errno is not None else errno.EIO
                raise OSError(
                    code,
                    "channel_reply absent-move canonical proof ambiguous; hidden staging entry retained",
                    name,
                ) from exc
            return _identity_matches(expected_source, current)

        def cleanup_expected_staging() -> None:
            nonlocal staged
            if not staged:
                return
            try:
                staged_current = os.stat(staging, dir_fd=source_token.fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                staged = False
                raise OSError(
                    errno.EIO,
                    "channel_reply absent-move staging cleanup ambiguous; hidden staging entry disappeared",
                    staging,
                ) from exc
            except OSError as exc:
                code = exc.errno if exc.errno is not None else errno.EIO
                raise OSError(
                    code,
                    "channel_reply absent-move staging cleanup ambiguous; hidden staging entry retained",
                    staging,
                ) from exc
            if not _identity_matches(expected_source, staged_current):
                raise OSError(
                    errno.EIO,
                    "channel_reply absent-move staging cleanup ambiguous; changed hidden entry retained",
                    staging,
                )
            if not (
                canonical_name_holds_expected(source_token.fd, source_name)
                or canonical_name_holds_expected(destination_token.fd, destination_name)
            ):
                raise OSError(
                    errno.EIO,
                    "channel_reply absent-move rollback ambiguous; "
                    "expected source retained only under hidden staging entry",
                    staging,
                )
            cleanup_staging()

        def rollback_destination_and_staging() -> None:
            nonlocal compensation_started
            compensation_started = True
            cleanup_expected_staging()

        try:
            os.link(
                source_name,
                staging,
                src_dir_fd=source_token.fd,
                dst_dir_fd=source_token.fd,
                follow_symlinks=False,
            )
            staged = True
        except FileNotFoundError:
            return MoveResult("source-absent", None)
        except OSError:
            return MoveResult("rejected", None)

        try:
            try:
                staged_st = os.stat(staging, dir_fd=source_token.fd, follow_symlinks=False)
            except FileNotFoundError:
                staged = False
                return MoveResult("source-changed", None)
            staged_info = _entry_info(staging, staged_st, root_dev=self._root_st.st_dev)
            if not (
                staged_info.kind == "regular"
                and staged_info.private_owner
                and staged_info.private_access
                and staged_info.same_root_filesystem
                and getattr(staged_st, "st_nlink", 1) >= 2
            ):
                cleanup_staging()
                return MoveResult("rejected", None)
            if not _identity_matches(expected_source, staged_st):
                cleanup_staging()
                return MoveResult("source-changed", None)
            os.fsync(source_token.fd)

            try:
                os.link(
                    staging,
                    destination_name,
                    src_dir_fd=source_token.fd,
                    dst_dir_fd=destination_token.fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                entry = self.inspect(destination_token, destination_name)
                cleanup_expected_staging()
                return MoveResult("destination-exists", entry)
            except OSError:
                cleanup_expected_staging()
                return MoveResult("rejected", None)
            os.fsync(destination_token.fd)

            try:
                os.rename(
                    source_name,
                    removal,
                    src_dir_fd=source_token.fd,
                    dst_dir_fd=source_token.fd,
                )
                removal_exists = True
            except FileNotFoundError:
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    staging,
                    source_name,
                    operation="absent-move source restore",
                )
                staged = False
                rollback_destination_and_staging()
                return MoveResult("source-changed", None)
            except OSError:
                rollback_destination_and_staging()
                return MoveResult("rejected", None)
            os.fsync(source_token.fd)

            removal_st = os.stat(removal, dir_fd=source_token.fd, follow_symlinks=False)
            if not _identity_matches(expected_source, removal_st):
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    removal,
                    source_name,
                    operation="absent-move source restore",
                )
                removal_exists = False
                rollback_destination_and_staging()
                return MoveResult("source-changed", None)

            entry = self.inspect(destination_token, destination_name)
            if entry is None or entry.identity != expected_source:
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    removal,
                    source_name,
                    operation="absent-move source restore",
                )
                removal_exists = False
                cleanup_expected_staging()
                return MoveResult("source-changed", entry)

            linearized = True
            os.unlink(removal, dir_fd=source_token.fd)
            removal_exists = False
            os.fsync(source_token.fd)
            cleanup_expected_staging()
            return MoveResult("moved", entry)
        except BaseException as original_exc:
            if linearized or recovery_started or compensation_started:
                raise
            if removal_exists:
                recovery_started = True
                try:
                    self._restore_hidden_no_replace(
                        source_token.fd,
                        removal,
                        source_name,
                        operation="absent-move source restore",
                    )
                    removal_exists = False
                except BaseException as recovery_exc:
                    raise recovery_exc from original_exc
            try:
                rollback_destination_and_staging()
            except BaseException as cleanup_exc:
                raise cleanup_exc from original_exc
            raise

    def _move_replace_destination(
        self,
        source_token: _PosixDirectoryToken,
        source_name: str,
        destination_token: _PosixDirectoryToken,
        destination_name: str,
        expected_source: ChannelReplyObjectIdentity,
    ) -> MoveResult:
        quarantine = _private_staging_name(f"{source_name}.move")
        backup = _private_staging_name(f"{source_name}.move.backup")
        quarantined = False
        backup_exists = False
        recovery_started = False
        linearized = False

        def cleanup_backup() -> None:
            nonlocal backup_exists
            if not backup_exists:
                return
            os.unlink(backup, dir_fd=source_token.fd)
            backup_exists = False
            os.fsync(source_token.fd)

        try:
            os.rename(source_name, quarantine, src_dir_fd=source_token.fd, dst_dir_fd=source_token.fd)
            quarantined = True
        except FileNotFoundError:
            return MoveResult("source-absent", None)
        except OSError:
            return MoveResult("rejected", None)

        try:
            os.fsync(source_token.fd)
            st = os.stat(quarantine, dir_fd=source_token.fd, follow_symlinks=False)
            info = _entry_info(quarantine, st, root_dev=self._root_st.st_dev)
            if not info.private_regular_single_link or not _identity_matches(expected_source, st):
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    quarantine,
                    source_name,
                    operation="replace-move source restore",
                )
                quarantined = False
                return MoveResult("source-changed", None)

            try:
                os.link(
                    quarantine,
                    backup,
                    src_dir_fd=source_token.fd,
                    dst_dir_fd=source_token.fd,
                    follow_symlinks=False,
                )
                backup_exists = True
            except OSError:
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    quarantine,
                    source_name,
                    operation="replace-move source restore",
                )
                quarantined = False
                return MoveResult("rejected", None)

            try:
                backup_st = os.stat(backup, dir_fd=source_token.fd, follow_symlinks=False)
            except FileNotFoundError:
                backup_exists = False
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    quarantine,
                    source_name,
                    operation="replace-move source restore",
                )
                quarantined = False
                return MoveResult("source-changed", None)
            backup_info = _entry_info(backup, backup_st, root_dev=self._root_st.st_dev)
            if not (
                backup_info.kind == "regular"
                and backup_info.private_owner
                and backup_info.private_access
                and backup_info.same_root_filesystem
                and getattr(backup_st, "st_nlink", 1) >= 2
                and _identity_matches(expected_source, backup_st)
            ):
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    quarantine,
                    source_name,
                    operation="replace-move source restore",
                )
                quarantined = False
                cleanup_backup()
                return MoveResult("source-changed", None)
            os.fsync(source_token.fd)

            os.rename(quarantine, destination_name, src_dir_fd=source_token.fd, dst_dir_fd=destination_token.fd)
            quarantined = False
            os.fsync(destination_token.fd)
            if source_token.fd != destination_token.fd:
                os.fsync(source_token.fd)

            entry = self.inspect(destination_token, destination_name)
            if entry is None or entry.identity != expected_source:
                recovery_started = True
                self._restore_hidden_no_replace(
                    source_token.fd,
                    backup,
                    source_name,
                    operation="replace-move source restore",
                )
                backup_exists = False
                return MoveResult("source-changed", entry)

            linearized = True
            cleanup_backup()
            return MoveResult("moved", entry)
        except BaseException as original_exc:
            if linearized or recovery_started:
                raise
            recovery_started = True
            try:
                if quarantined:
                    self._restore_hidden_no_replace(
                        source_token.fd,
                        quarantine,
                        source_name,
                        operation="replace-move source restore",
                    )
                    quarantined = False
                elif backup_exists:
                    self._restore_hidden_no_replace(
                        source_token.fd,
                        backup,
                        source_name,
                        operation="replace-move source restore",
                    )
                    backup_exists = False
                cleanup_backup()
            except BaseException as recovery_exc:
                raise recovery_exc from original_exc
            raise

    def remove_owned_entry(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        *,
        budget: OwnedRemovalBudget,
        expected: ChannelReplyObjectIdentity | None = None,
    ) -> OwnedRemovalResult:
        try:
            _validate_component_name(name)
            parent_token = self._token(parent)
            mutable_budget = _MutableRemovalBudget(
                inspections=budget.inspections,
                removals=budget.removals,
                max_depth=budget.max_depth,
                candidates=budget.candidates,
            )
            if expected is None:
                return self._remove_name(parent_token.fd, name, mutable_budget, 0)
            quarantine = _private_staging_name(f"{name}.remove")
            quarantined = False
            recovery_started = False
            try:
                os.rename(name, quarantine, src_dir_fd=parent_token.fd, dst_dir_fd=parent_token.fd)
                quarantined = True
            except FileNotFoundError:
                return OwnedRemovalResult("absent", 1, 0)
            except OSError as exc:
                return OwnedRemovalResult("retryable", 1, 0, type(exc).__name__)
            try:
                os.fsync(parent_token.fd)
                try:
                    st = os.stat(quarantine, dir_fd=parent_token.fd, follow_symlinks=False)
                except OSError as exc:
                    recovery_started = True
                    self._restore_hidden_no_replace(
                        parent_token.fd,
                        quarantine,
                        name,
                        operation="expected-removal restore",
                    )
                    quarantined = False
                    return OwnedRemovalResult("retryable", 1, 0, type(exc).__name__)
                if not _identity_matches(expected, st):
                    recovery_started = True
                    self._restore_hidden_no_replace(
                        parent_token.fd,
                        quarantine,
                        name,
                        operation="expected-removal restore",
                    )
                    quarantined = False
                    return OwnedRemovalResult("retryable", 1, 0, "entry_changed")
                result = self._remove_name(parent_token.fd, quarantine, mutable_budget, 0, expected)
                if result.state in {"removed", "absent"}:
                    quarantined = False
                    return result
                recovery_started = True
                self._restore_hidden_no_replace(
                    parent_token.fd,
                    quarantine,
                    name,
                    operation="expected-removal restore",
                )
                quarantined = False
                if result.error not in {
                    "inspection_budget_exhausted",
                    "depth_budget_exhausted",
                    "removal_budget_exhausted",
                    "candidate_budget_exhausted",
                }:
                    raise OSError("channel_reply expected removal cleanup ambiguous")
                return result
            except BaseException as original_exc:
                if recovery_started:
                    raise
                if quarantined:
                    recovery_started = True
                    try:
                        self._restore_hidden_no_replace(
                            parent_token.fd,
                            quarantine,
                            name,
                            operation="expected-removal restore",
                        )
                        quarantined = False
                    except BaseException as recovery_exc:
                        raise recovery_exc from original_exc
                raise
        finally:
            self.verify()

    def _remove_name(
        self,
        parent_fd: int,
        name: str,
        budget: "_MutableRemovalBudget",
        depth: int,
        expected: ChannelReplyObjectIdentity | None = None,
    ) -> OwnedRemovalResult:
        if budget.inspections <= 0:
            return OwnedRemovalResult("retryable", error="inspection_budget_exhausted")
        if depth > budget.max_depth:
            return OwnedRemovalResult("retryable", error="depth_budget_exhausted")
        budget.inspections -= 1
        inspections = 1
        removals = 0
        try:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return OwnedRemovalResult("absent", inspections, removals)
        except OSError as exc:
            return OwnedRemovalResult("retryable", inspections, removals, type(exc).__name__)
        if expected is not None and not _identity_matches(expected, st):
            return OwnedRemovalResult("retryable", inspections, removals, "entry_changed")
        if not stat.S_ISDIR(st.st_mode):
            if budget.removals <= 0:
                return OwnedRemovalResult("retryable", inspections, removals, "removal_budget_exhausted")
            try:
                os.unlink(name, dir_fd=parent_fd)
            except FileNotFoundError:
                return OwnedRemovalResult("absent", inspections, removals)
            except OSError as exc:
                return OwnedRemovalResult("retryable", inspections, removals, type(exc).__name__)
            budget.removals -= 1
            removals += 1
            os.fsync(parent_fd)
            return OwnedRemovalResult("removed", inspections, removals)
        info = _entry_info(name, st, root_dev=self._root_st.st_dev)
        if not (info.private_owner and info.private_access and info.same_root_filesystem):
            return OwnedRemovalResult("rejected", inspections, removals, "directory_not_private")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            child_fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            return OwnedRemovalResult("retryable", inspections, removals, type(exc).__name__)
        try:
            opened = os.fstat(child_fd)
            if not _same_object(opened, st):
                return OwnedRemovalResult("retryable", inspections, removals, "directory_replaced")
            entries_seen = 0
            scan_fd = os.dup(child_fd)
            try:
                with os.scandir(scan_fd) as iterator:
                    for entry in iterator:
                        if entries_seen >= budget.candidates:
                            return OwnedRemovalResult(
                                "progress" if removals else "retryable",
                                inspections,
                                removals,
                                "candidate_budget_exhausted",
                            )
                        entries_seen += 1
                        child = self._remove_name(child_fd, entry.name, budget, depth + 1)
                        inspections += child.inspections
                        removals += child.removals
                        if child.state in {"progress", "retryable", "rejected"}:
                            state = "progress" if removals else child.state
                            return OwnedRemovalResult(state, inspections, removals, child.error)
                        if budget.inspections <= 0 or budget.removals <= 0:
                            return OwnedRemovalResult("progress" if removals else "retryable", inspections, removals)
            except OSError as exc:
                return OwnedRemovalResult("retryable", inspections, removals, type(exc).__name__)
            finally:
                if scan_fd >= 0:
                    _close_fd_allow_ebadf(scan_fd)
            if budget.removals <= 0:
                return OwnedRemovalResult("progress" if removals else "retryable", inspections, removals)
            try:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not _same_object(current, opened):
                    return OwnedRemovalResult(
                        "progress" if removals else "retryable",
                        inspections,
                        removals,
                        "directory_replaced",
                    )
                os.rmdir(name, dir_fd=parent_fd)
            except FileNotFoundError:
                return OwnedRemovalResult("absent", inspections, removals)
            except OSError as exc:
                return OwnedRemovalResult(
                    "progress" if removals else "retryable",
                    inspections,
                    removals,
                    type(exc).__name__,
                )
            budget.removals -= 1
            removals += 1
            os.fsync(parent_fd)
            return OwnedRemovalResult("removed", inspections, removals)
        finally:
            os.close(child_fd)

    def fsync_directory(self, directory: ChannelReplyDirectoryToken) -> None:
        try:
            token = self._token(directory)
            os.fsync(token.fd)
        finally:
            self.verify()


@dataclass(slots=True)
class _MutableRemovalBudget:
    inspections: int
    removals: int
    max_depth: int
    candidates: int


def _open_root(
    state_dir: Path,
    expected_root: ChannelReplyRootIdentity | None,
) -> tuple[int, os.stat_result]:
    before = os.stat(state_dir, follow_symlinks=False)
    _require_private_root(before)
    if expected_root is not None and not _identity_matches(expected_root, before):
        raise ChannelReplyExpectedRootMismatch(
            "channel_reply state root does not match expected identity"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(state_dir, flags)
    try:
        opened = os.fstat(root_fd)
        _require_private_root(opened)
        if not _same_object(opened, before):
            raise OSError("channel_reply state root changed during lock acquisition")
        _require_descriptor_scandir(root_fd)
    except BaseException:
        os.close(root_fd)
        raise
    return root_fd, opened


def _open_lock(root_fd: int) -> tuple[int, os.stat_result]:
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    for _ in range(4):
        try:
            before = os.stat(_LOCK_FILE, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            try:
                fd = os.open(_LOCK_FILE, flags | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE_MODE, dir_fd=root_fd)
            except FileExistsError:
                continue
            try:
                os.fchmod(fd, _PRIVATE_FILE_MODE)
                opened = os.fstat(fd)
                _require_private_lock(opened)
                after = os.stat(_LOCK_FILE, dir_fd=root_fd, follow_symlinks=False)
                if not _same_object(opened, after):
                    raise OSError("channel_reply lock leaf changed during creation")
                return fd, opened
            except BaseException:
                os.close(fd)
                raise
        else:
            _require_private_lock(before)
            fd = os.open(_LOCK_FILE, flags, dir_fd=root_fd)
            try:
                opened = os.fstat(fd)
                _require_private_lock(opened)
                if not _same_object(opened, before):
                    raise OSError("channel_reply lock leaf changed during open")
                after = os.stat(_LOCK_FILE, dir_fd=root_fd, follow_symlinks=False)
                if not _same_object(opened, after):
                    raise OSError("channel_reply lock leaf replaced during open")
                return fd, opened
            except BaseException:
                os.close(fd)
                raise
    raise OSError(errno.EAGAIN, "channel_reply lock leaf create race did not settle")


class PosixChannelReplyStateLockAdapter:
    """Advisory flock plus root-bound channel_reply mutation session."""

    @contextlib.contextmanager
    def exclusive(
        self,
        state_dir: Path,
        *,
        expected_root: ChannelReplyRootIdentity | None = None,
    ):
        _require_primitives()
        _require_no_replace_rename()
        root_fd, root_st = _open_root(state_dir, expected_root)
        try:
            lock_fd, lock_st = _open_lock(root_fd)
        except BaseException:
            os.close(root_fd)
            raise
        locked = False
        session: _PosixChannelReplyMutationSession | None = None
        try:
            named_root = os.stat(state_dir, follow_symlinks=False)
            _require_private_root(named_root)
            if not _same_object(named_root, root_st):
                raise OSError("channel_reply state root replaced before lock")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked = True
            current_lock = os.fstat(lock_fd)
            _require_private_lock(current_lock)
            leaf = os.stat(_LOCK_FILE, dir_fd=root_fd, follow_symlinks=False)
            if not _same_object(current_lock, leaf) or not _same_object(current_lock, lock_st):
                raise OSError("channel_reply lock leaf replaced after flock")
            named_root = os.stat(state_dir, follow_symlinks=False)
            _require_private_root(named_root)
            if not _same_object(named_root, root_st):
                raise OSError("channel_reply state root replaced after flock")
            session = _PosixChannelReplyMutationSession(
                state_dir=state_dir,
                root_fd=root_fd,
                root_st=root_st,
                lock_fd=lock_fd,
                lock_st=lock_st,
            )
            yield session
        finally:
            try:
                if session is not None:
                    session.verify()
            finally:
                if session is not None:
                    session.close_tokens()
                try:
                    if locked:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
                    os.close(root_fd)
