"""Local Darwin rendezvous: real SCM_RIGHTS, identity gates and cleanup."""
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from lingtai.adapters.posix import daemon_manager as dm
from lingtai.adapters.posix.daemon_capsule import receive_capsule, send_capsule
from lingtai.adapters.posix.daemon_mach import MachCapsuleListener, connect_mach_capsule

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-only rendezvous")


def fd_count():
    return len(os.listdir('/dev/fd'))


def test_real_capsule_and_adopted_fd():
    before = fd_count()
    listener = MachCapsuleListener()
    result = []
    def serve():
        conn, pid = listener.accept()
        with conn:
            wire = receive_capsule(conn)
            try:
                result.append((wire.value, os.read(wire.adopted_fd, 10), pid))
                assert not os.get_inheritable(wire.adopted_fd)
                conn.sendall(b'OK')
            finally:
                wire.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    r, w = os.pipe()
    os.write(w, b'fd-proof')
    os.close(w)
    try:
        with connect_mach_capsule(listener.name, manager_pid=os.getpid()) as conn:
            send_capsule(conn, b'{"fixture": true}', adopted_fd=r)
            assert conn.recv(2) == b'OK'
        thread.join(3)
        assert result == [({'fixture': True}, b'fd-proof', os.getpid())]
    finally:
        os.close(r)
        listener.close()
    assert fd_count() == before


def test_wrong_manager_pid_rejected_before_capsule():
    before = fd_count()
    listener = MachCapsuleListener()
    result = []
    def serve():
        conn, _ = listener.accept()
        with conn:
            result.append(conn.recv(1))
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        with pytest.raises(PermissionError, match='identity mismatch'):
            connect_mach_capsule(listener.name, manager_pid=os.getpid() + 100000)
        thread.join(3)
        assert result == [b'']  # no capsule byte reached the wrong peer
    finally:
        listener.close()
    assert fd_count() == before


def test_wrong_sender_uid_rejected_and_listener_survives(monkeypatch):
    before = fd_count()
    listener = MachCapsuleListener()
    peer = listener.api.peer
    monkeypatch.setattr(listener.api, 'peer', lambda buf: (os.geteuid()+1, os.getpid()))
    stopped = threading.Event()
    def serve():
        try:
            conn, _ = listener.accept()
            conn.close()
        except OSError:
            pass
        finally:
            stopped.set()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        # Destroying the rejected send-once reply right may deliver a kernel
        # notification immediately; it is not an authenticated protocol ACK.
        with pytest.raises((socket.timeout, PermissionError)):
            connect_mach_capsule(listener.name, manager_pid=os.getpid())
        assert not stopped.is_set()
        monkeypatch.setattr(listener.api, 'peer', peer)
        with connect_mach_capsule(listener.name, manager_pid=os.getpid()):
            pass
        thread.join(3)
        assert stopped.is_set()
    finally:
        listener.close()
    assert fd_count() == before


def test_retired_endpoint_does_not_reconnect():
    listener = MachCapsuleListener()
    name = listener.name
    listener.close()
    listener.close()
    with pytest.raises(OSError):
        connect_mach_capsule(name, manager_pid=os.getpid())


def test_darwin_fallback_is_project_ordinary_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, '_unix_socket_usable', lambda _: False)
    assert dm._capsule_socket_path(tmp_path) == tmp_path / 'capsule.endpoint.json'


def test_manager_fd_roundtrip_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, '_unix_socket_usable', lambda _: False)
    before = fd_count()
    def manager():
        root = tmp_path
        instance = dm._DaemonManagerProcess(root/'queue', root/'journal', pool_size=1, root=root)
        instance.start_capsule_server(dm._capsule_socket_path(root))
        return instance
    first = manager()
    old = (tmp_path/'capsule.endpoint.json').read_text()
    r, w = os.pipe()
    os.write(w, b'owned')
    os.close(w)
    try:
        dm._send_capsule(tmp_path, 'run-one', {'fixture': True}, adopted_fd=r)
        wire = first.capsules.pop('run-one')
        try:
            assert os.read(wire.adopted_fd, 10) == b'owned'
        finally:
            wire.close()
    finally:
        os.close(r)
        first._capsule_socket.close()
        first._capsule_server_thread.join(2)
    second = manager()
    try:
        assert (tmp_path/'capsule.endpoint.json').read_text() != old
        dm._send_capsule(tmp_path, 'run-two', {})
        second.capsules.pop('run-two').close()
    finally:
        second._capsule_socket.close()
        second._capsule_server_thread.join(2)
    assert fd_count() == before


def mach_right_count():
    """Count current task's Mach names; release the kernel-allocated arrays."""
    import ctypes as C
    from lingtai.adapters.posix.daemon_mach import _Mach, _U32
    api = _Mach()
    names, types = C.POINTER(_U32)(), C.POINTER(_U32)()
    n, t = _U32(), _U32()
    fn = api.lib.mach_port_names
    fn.argtypes = [_U32, C.POINTER(C.POINTER(_U32)), C.POINTER(_U32),
                   C.POINTER(C.POINTER(_U32)), C.POINTER(_U32)]
    fn.restype = C.c_int
    dealloc = api.lib.vm_deallocate
    dealloc.argtypes = [_U32, C.c_size_t, C.c_size_t]
    dealloc.restype = C.c_int
    api.check(fn(api.task, C.byref(names), C.byref(n), C.byref(types), C.byref(t)))
    try:
        return n.value
    finally:
        for ptr, size in ((names, n.value), (types, t.value)):
            api.check(dealloc(api.task, C.cast(ptr, C.c_void_p).value, size * 4))


@pytest.mark.parametrize('bad', ['wrong-id', 'oversized'])
def test_malformed_request_isolated_and_rights_released(monkeypatch, bad):
    import ctypes as C
    from lingtai.adapters.posix.daemon_mach import _Mach, _Request, _REQUEST
    before_fd, before_rights = fd_count(), mach_right_count()
    listener = MachCapsuleListener()
    done = []
    def serve():
        conn, _ = listener.accept()
        conn.close()
        done.append(True)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    original = _Mach.send
    def corrupt(api, message):
        if isinstance(message, _Request) and message.header.id == _REQUEST:
            if bad == 'wrong-id':
                message.header.id = 0
            else:
                class Oversized(C.Structure):
                    _fields_ = [('request', _Request), ('padding', C.c_char * 512)]
                message = Oversized(message)
                message.request.header.size = C.sizeof(message)
        return original(api, message)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(_Mach, 'send', corrupt)
            with pytest.raises((PermissionError, socket.timeout, BufferError)):
                connect_mach_capsule(listener.name, manager_pid=os.getpid())
        with connect_mach_capsule(listener.name, manager_pid=os.getpid()):
            pass
        thread.join(3)
        assert done == [True]
    finally:
        listener.close()
    assert fd_count() == before_fd
    assert mach_right_count() == before_rights


def test_repeated_native_failure_and_success_do_not_leak_rights():
    before_fd, before_rights = fd_count(), mach_right_count()
    listener = MachCapsuleListener()
    def serve():
        for _ in range(40):
            conn, _ = listener.accept()
            conn.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        for _ in range(20):
            with pytest.raises(PermissionError):
                connect_mach_capsule(listener.name, manager_pid=os.getpid()+100000)
            with connect_mach_capsule(listener.name, manager_pid=os.getpid()):
                pass
        thread.join(5)
        assert not thread.is_alive()
    finally:
        listener.close()
    assert fd_count() == before_fd
    assert mach_right_count() == before_rights


def test_oversized_capsule_releases_fd_and_keeps_manager_alive(tmp_path, monkeypatch):
    from lingtai.adapters.posix.daemon_capsule import MAX_CAPSULE_BYTES
    monkeypatch.setattr(dm, '_unix_socket_usable', lambda _: False)
    before = fd_count()
    instance = dm._DaemonManagerProcess(tmp_path/'queue', tmp_path/'journal', pool_size=1, root=tmp_path)
    instance.start_capsule_server(dm._capsule_socket_path(tmp_path))
    r, w = os.pipe()
    try:
        with dm._connect_capsule_socket(tmp_path, dm._capsule_socket_path(tmp_path)) as conn:
            conn.settimeout(3)
            try:
                send_capsule(conn, b' '*(MAX_CAPSULE_BYTES+1), adopted_fd=w)
                assert conn.recv(3) == b'ERR'
            except (BrokenPipeError, ConnectionResetError):
                pass
        os.close(w)
        w = None
        os.set_blocking(r, False)
        # Server cleanup is after its ERR write; join by a later valid ACK.
        dm._send_capsule(tmp_path, 'after-oversize', {})
        instance.capsules.pop('after-oversize').close()
        assert os.read(r, 1) == b''
        assert not instance.capsules
    finally:
        os.close(r)
        if w is not None:
            os.close(w)
        instance._capsule_socket.close()
        instance._capsule_server_thread.join(2)
    assert fd_count() == before


def test_request_send_failure_releases_all_native_resources(monkeypatch):
    from lingtai.adapters.posix.daemon_mach import _Mach
    before_fd, before_rights = fd_count(), mach_right_count()
    listener = MachCapsuleListener()
    try:
        def fail_send(*args):
            raise OSError('injected send failure')
        monkeypatch.setattr(_Mach, 'send', fail_send)
        with pytest.raises(OSError, match='injected send failure'):
            connect_mach_capsule(listener.name, manager_pid=os.getpid())
    finally:
        listener.close()
    assert fd_count() == before_fd
    assert mach_right_count() == before_rights


def test_reply_send_failure_releases_request_and_listener_survives(monkeypatch):
    before_fd, before_rights = fd_count(), mach_right_count()
    listener = MachCapsuleListener()
    original = listener.api.send
    def fail_reply(message):
        raise OSError('injected reply failure')
    monkeypatch.setattr(listener.api, 'send', fail_reply)
    done=[]
    def serve():
        conn, _ = listener.accept()
        conn.close()
        done.append(True)
    thread=threading.Thread(target=serve,daemon=True)
    thread.start()
    try:
        with pytest.raises((PermissionError,socket.timeout)):
            connect_mach_capsule(listener.name, manager_pid=os.getpid())
        monkeypatch.setattr(listener.api, 'send', original)
        with connect_mach_capsule(listener.name, manager_pid=os.getpid()):
            pass
        thread.join(3)
        assert done == [True]
    finally:
        listener.close()
    assert fd_count() == before_fd
    assert mach_right_count() == before_rights
