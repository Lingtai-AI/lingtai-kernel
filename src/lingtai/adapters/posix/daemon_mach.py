"""Darwin-only, file-free rendezvous for anonymous daemon capsule sockets.

Only a socketpair endpoint crosses Mach. Capsules and adopted descriptors use
our existing AF_UNIX/SCM_RIGHTS wire. Both peers check kernel audit credentials;
the client also checks the exact manager PID before releasing capsule bytes.
No launchd configuration, credential, token, or capsule is persisted here.
"""
from __future__ import annotations

import ctypes as C
import os
import socket
import sys
import uuid

_U32 = C.c_uint32


class _Header(C.Structure):
    _fields_ = [("bits", _U32), ("size", _U32), ("remote", _U32),
                ("local", _U32), ("voucher", _U32), ("id", C.c_int32)]


class _Port(C.Structure):
    _pack_ = 4
    _fields_ = [("name", _U32), ("pad1", _U32), ("pad2", C.c_uint16),
                ("disposition", C.c_uint8), ("type", C.c_uint8)]


class _Request(C.Structure):
    _pack_ = 4
    _fields_ = [("header", _Header), ("count", _U32), ("port", _Port)]


class _Audit(C.Structure):
    _fields_ = [("val", _U32 * 8)]


_COMPLEX = 0x80000000
_REQUEST = 0x4C544401
_REPLY = 0x4C544402
_PREFIX = "org.lingtai.daemon."


class _Mach:
    """Public SDK ABI only; lazy so importing POSIX code is portable."""

    def __init__(self):
        if sys.platform != "darwin":
            raise NotImplementedError("Mach daemon rendezvous requires Darwin")
        if (C.sizeof(_Header), C.sizeof(_Port), C.sizeof(_Request)) != (24, 12, 40):
            raise RuntimeError("unsupported Darwin Mach ABI")
        self.lib = C.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        pointer = C.POINTER(_U32)
        bindings = [
            ("mach_task_self", [], _U32),
            ("mach_port_allocate", [_U32, C.c_int, pointer], C.c_int),
            ("mach_port_insert_right", [_U32, _U32, _U32, C.c_int], C.c_int),
            ("mach_port_destroy", [_U32, _U32], C.c_int),
            ("mach_port_deallocate", [_U32, _U32], C.c_int),
            ("bootstrap_register", [_U32, C.c_char_p, _U32], C.c_int),
            ("bootstrap_look_up", [_U32, C.c_char_p, pointer], C.c_int),
            ("fileport_makeport", [C.c_int, pointer], C.c_int),
            ("fileport_makefd", [_U32], C.c_int),
            ("mach_msg", [C.c_void_p, C.c_int, _U32, _U32, _U32, _U32, _U32], C.c_int),
            ("mach_msg_destroy", [C.c_void_p], None),
        ]
        for name, args, result in bindings:
            func = getattr(self.lib, name)
            func.argtypes, func.restype = args, result
        self.bsm = C.CDLL("/usr/lib/libbsm.dylib")
        for name in ("audit_token_to_euid", "audit_token_to_pid"):
            func = getattr(self.bsm, name)
            func.argtypes, func.restype = [_Audit], _U32
        self.task = self.lib.mach_task_self()
        self.bootstrap = _U32.in_dll(self.lib, "bootstrap_port").value

    @staticmethod
    def check(code):
        if code:
            # No remote message/secret content in errors.
            raise OSError(f"Darwin daemon rendezvous error {code:#x}")

    def allocate(self):
        port = _U32()
        self.check(self.lib.mach_port_allocate(self.task, 1, C.byref(port)))
        return port.value

    def destroy(self, port):
        if port:
            self.lib.mach_port_destroy(self.task, port)

    def release(self, port):
        if port:
            self.lib.mach_port_deallocate(self.task, port)

    def send(self, message):
        self.check(self.lib.mach_msg(C.byref(message), 1 | 0x10,
                                    C.sizeof(message), 0, 0, 500, 0))

    def receive(self, port):
        buf = C.create_string_buffer(256)
        code = self.lib.mach_msg(buf, 2 | 0x100 | (3 << 24), 0, 256, port, 500, 0)
        if code == 0x10004003:  # MACH_RCV_TIMED_OUT
            raise socket.timeout("Darwin daemon rendezvous timed out")
        if code == 0x10004004:  # MACH_RCV_TOO_LARGE; kernel discards this message
            raise BufferError("oversized daemon rendezvous message")
        self.check(code)
        return buf

    def peer(self, buf):
        header = _Header.from_buffer(buf)
        offset = (header.size + 3) & ~3
        if header.size < 24 or offset + 52 > len(buf):
            raise ValueError("invalid daemon Mach message size")
        trailer = (_U32 * 13).from_buffer(buf, offset)
        if trailer[0] != 0 or trailer[1] < 52:
            raise ValueError("missing daemon Mach audit identity")
        audit = _Audit.from_buffer(buf, offset + 20)
        return (self.bsm.audit_token_to_euid(audit), self.bsm.audit_token_to_pid(audit))


class MachCapsuleListener:
    """accept/close seam returning authenticated anonymous AF_UNIX sockets."""

    def __init__(self):
        self.api = _Mach()
        self.name = _PREFIX + uuid.uuid4().hex
        self.port = self.api.allocate()
        try:
            self.api.check(self.api.lib.mach_port_insert_right(
                self.api.task, self.port, self.port, 20))
            # Public but deprecated since 10.5; denial is a visible startup
            # failure, never an invitation to write a local filesystem socket.
            self.api.check(self.api.lib.bootstrap_register(
                self.api.bootstrap, self.name.encode("ascii"), self.port))
        except BaseException:
            self.close()
            raise

    def close(self):
        port, self.port = self.port, 0
        self.api.destroy(port)

    def accept(self):
        while self.port:
            try:
                buf = self.api.receive(self.port)
            except (socket.timeout, BufferError):
                continue
            fd = None
            try:
                header = _Header.from_buffer(buf)
                if (header.size != 40 or header.id != _REQUEST
                        or not header.bits & _COMPLEX):
                    continue
                message = _Request.from_buffer(buf)
                if message.count != 1 or message.port.type != 0 or not header.remote:
                    continue
                uid, pid = self.api.peer(buf)
                if uid != os.geteuid():
                    continue
                fd = self.api.lib.fileport_makefd(message.port.name)
                if fd < 0:
                    fd = None
                    continue
                os.set_inheritable(fd, False)
                conn = socket.socket(fileno=fd)
                fd = None
                try:
                    if conn.family != socket.AF_UNIX or conn.type != socket.SOCK_STREAM:
                        raise ValueError("daemon rendezvous requires a Unix stream socket")
                    conn.settimeout(5.0)
                    reply = _Header(18, 24, header.remote, 0, 0, _REPLY)
                    self.api.send(reply)  # MOVE_SEND_ONCE consumes reply right
                    header.remote = 0
                    return conn, pid
                except BaseException:
                    conn.close()
                    raise
            except (OSError, ValueError):
                # Reject one broken request without retiring the listener.
                continue
            finally:
                if fd is not None:
                    os.close(fd)
                self.api.lib.mach_msg_destroy(buf)
        raise OSError("daemon rendezvous listener closed")


def connect_mach_capsule(name: str, *, manager_pid: int) -> socket.socket:
    """Return a socket only after authenticating the receiver's kernel PID/UID."""
    suffix = name[len(_PREFIX):] if isinstance(name, str) and name.startswith(_PREFIX) else ""
    if len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
        raise ValueError("invalid daemon rendezvous name")
    api = _Mach()
    reply_port = api.allocate()
    destination = _U32()
    fileport = _U32()
    local = remote = None
    try:
        api.check(api.lib.bootstrap_look_up(api.bootstrap, name.encode("ascii"), C.byref(destination)))
        local, remote = socket.socketpair()
        if api.lib.fileport_makeport(remote.fileno(), C.byref(fileport)) != 0:
            raise OSError(C.get_errno(), "daemon socket endpoint conversion failed")
        message = _Request(
            _Header(_COMPLEX | 19 | (21 << 8), 40, destination.value, reply_port, 0, _REQUEST),
            1, _Port(fileport.value, 0, 0, 19, 0))
        api.send(message)
        remote.close()
        remote = None
        buf = api.receive(reply_port)
        try:
            header = _Header.from_buffer(buf)
            if (header.size != 24 or header.id != _REPLY or header.bits & _COMPLEX
                    or api.peer(buf) != (os.geteuid(), manager_pid)):
                raise PermissionError("daemon rendezvous peer identity mismatch")
        finally:
            api.lib.mach_msg_destroy(buf)
        result, local = local, None
        return result
    finally:
        for sock in (local, remote):
            if sock is not None:
                sock.close()
        api.release(fileport.value)
        api.release(destination.value)
        api.destroy(reply_port)
