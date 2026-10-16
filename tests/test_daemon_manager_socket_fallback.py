"""Unit coverage for the capsule socket fallback on socket-less filesystems.

exFAT volumes (for example ``/Volumes/<disk>``) reject ``bind`` on an
``AF_UNIX`` socket with ``EOPNOTSUPP``; the resident daemon manager used to die
with that unlogged error, leaving a pid file that still claimed ``running``.
"""
from __future__ import annotations

import errno
import os
import hashlib
import socket
import sys
from pathlib import Path

import pytest

from lingtai.adapters.posix import daemon_manager

import shutil
import tempfile

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX socket transport")


def _short_root() -> Path:
    """A short root: ``tmp_path`` under pytest is long enough to cross the
    ``_UNIX_SOCKET_PATH_LIMIT`` by itself, which would make these assertions
    pass for the wrong reason."""
    return Path(tempfile.mkdtemp(prefix="dm-", dir="/tmp"))


@pytest.fixture

def short_root():
    root = _short_root()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    daemon_manager._SOCKET_DIR_PROBE_CACHE.clear()
    yield
    daemon_manager._SOCKET_DIR_PROBE_CACHE.clear()


class _UnsupportedSocket:
    """An AF_UNIX socket whose bind is rejected by the filesystem."""

    def bind(self, _address):  # noqa: D102 - test double
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    def close(self):  # noqa: D102 - test double
        return None


def _assert_fallback(path, root):
    if sys.platform == "darwin":
        assert path == root / "capsule.endpoint.json"
    else:
        assert daemon_manager._is_capsule_socket_fallback(path)


def _make_socket_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _UnsupportedSocket())


def test_probe_reports_usable_on_a_normal_directory(tmp_path: Path) -> None:
    assert daemon_manager._unix_socket_usable(tmp_path) is True
    assert list(tmp_path.iterdir()) == []  # probe socket is always cleaned up


def test_probe_reports_unsupported_when_bind_is_rejected(tmp_path: Path, monkeypatch) -> None:
    _make_socket_unsupported(monkeypatch)
    assert daemon_manager._unix_socket_usable(tmp_path) is False
    assert daemon_manager._SOCKET_DIR_PROBE_CACHE[str(tmp_path)] is False


def test_direct_socket_path_survives_a_supported_directory(short_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(daemon_manager, "_unix_socket_usable", lambda _dir: True)
    direct = short_root / "capsule.sock"
    assert len(str(direct)) < daemon_manager._UNIX_SOCKET_PATH_LIMIT
    assert daemon_manager._capsule_socket_path(short_root) == direct


def test_socket_path_falls_back_when_the_filesystem_cannot_bind(
    short_root: Path, monkeypatch
) -> None:
    monkeypatch.setattr(daemon_manager, "_unix_socket_usable", lambda _dir: False)
    socket_path = daemon_manager._capsule_socket_path(short_root)
    _assert_fallback(socket_path, short_root)
    if sys.platform != "darwin":
        digest = hashlib.sha256(str(short_root.resolve()).encode("utf-8")).hexdigest()[:24]
        assert socket_path.name == "capsule.sock"
        assert socket_path.parent.name == f"lingtai-dm-{os.getuid()}-{digest}"
    # The agent process and the resident manager must derive the same path.
    assert daemon_manager._capsule_socket_path(short_root) == socket_path


def test_socket_path_still_falls_back_for_long_paths(short_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(daemon_manager, "_unix_socket_usable", lambda _dir: True)
    long_root = short_root / ("segment" * 12)
    assert len(str(long_root / "capsule.sock")) >= daemon_manager._UNIX_SOCKET_PATH_LIMIT
    socket_path = daemon_manager._capsule_socket_path(long_root)
    _assert_fallback(socket_path, long_root)


def test_recorded_start_failure_is_surfaced(tmp_path: Path) -> None:
    assert daemon_manager._manager_start_failure(tmp_path) is None
    daemon_manager._write_private_json(
        tmp_path / "manager.pid",
        {"pid": None, "state": "failed", "error": "OSError: [Errno 45] Operation not supported"},
    )
    failure = daemon_manager._manager_start_failure(tmp_path)
    assert failure == "OSError: [Errno 45] Operation not supported"


def test_running_manager_is_not_a_recorded_failure(tmp_path: Path) -> None:
    daemon_manager._write_private_json(tmp_path / "manager.pid", {"pid": 1234, "state": "running"})
    assert daemon_manager._manager_start_failure(tmp_path) is None


@pytest.mark.parametrize("error_number", sorted(daemon_manager._SOCKET_UNSUPPORTED_ERRNOS))
def test_probe_caches_real_unsupported_result(short_root, monkeypatch, error_number):
    calls = []
    class Probe(_UnsupportedSocket):
        def bind(self, address):
            calls.append(address)
            raise OSError(error_number, "unsupported fixture")
    monkeypatch.setattr(socket, "socket", lambda *a, **k: Probe())
    first = daemon_manager._capsule_socket_path(short_root)
    second = daemon_manager._capsule_socket_path(short_root)
    assert first == second
    _assert_fallback(first, short_root)
    assert len(calls) == 1


def test_probe_name_does_not_mask_unsupported_filesystem(short_root, monkeypatch):
    # Direct pathname fits, but the old 34-byte probe name exceeded AF_UNIX.
    root = short_root / ("x" * (84 - len(str(short_root)) - 1))
    root.mkdir()
    assert len(str(root / "capsule.sock")) < 100
    class Probe(_UnsupportedSocket):
        def bind(self, address):
            if len(address.encode()) >= 104:
                raise OSError("AF_UNIX path too long")
            raise OSError(errno.EOPNOTSUPP, "unsupported fixture")
    monkeypatch.setattr(socket, "socket", lambda *a, **k: Probe())
    _assert_fallback(daemon_manager._capsule_socket_path(root), root)


def test_missing_directory_does_not_poison_later_probe(short_root, monkeypatch):
    root = short_root / "later"
    assert daemon_manager._unix_socket_usable(root) is True
    root.mkdir()
    _make_socket_unsupported(monkeypatch)
    assert daemon_manager._unix_socket_usable(root) is False


def test_unicode_path_limit_is_bytes(short_root, monkeypatch):
    root = short_root / ("界" * 28)
    monkeypatch.setattr(daemon_manager, "_unix_socket_usable", lambda _: True)
    assert len(str(root / "capsule.sock")) < 100
    assert len(str(root / "capsule.sock").encode()) >= 100
    _assert_fallback(daemon_manager._capsule_socket_path(root), root)


def test_successful_probe_is_cached(short_root, monkeypatch):
    assert daemon_manager._unix_socket_usable(short_root) is True
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("unexpected reprobe"))
    assert daemon_manager._capsule_socket_path(short_root) == short_root / "capsule.sock"


def test_permission_error_is_not_cached_as_capability(short_root, monkeypatch):
    class Probe(_UnsupportedSocket):
        def bind(self, address):
            raise PermissionError(errno.EACCES, "permission fixture")
    monkeypatch.setattr(socket, "socket", lambda *a, **k: Probe())
    assert daemon_manager._unix_socket_usable(short_root) is True
    assert str(short_root) not in daemon_manager._SOCKET_DIR_PROBE_CACHE


def test_startup_bind_failure_records_failed_state(short_root, monkeypatch):
    def fail(*a, **k):
        raise OSError(errno.EOPNOTSUPP, "bind fixture")
    monkeypatch.setattr(daemon_manager._DaemonManagerProcess, "start_capsule_server", fail)
    with pytest.raises(OSError, match="bind fixture"):
        daemon_manager.run_manager(short_root, pool_size=1)
    root = daemon_manager._manager_dir(short_root)
    info = daemon_manager._read_manager_pid_info(root)
    assert info["state"] == "failed"
    assert info["pid"] is None
    assert "bind fixture" in info["error"]


def test_send_capsule_surfaces_failure_without_sleep(short_root, monkeypatch):
    daemon_manager._write_private_json(short_root / "manager.pid", {"state":"failed","error":"bind fixture"})
    monkeypatch.setattr(daemon_manager.time, "sleep", lambda _: pytest.fail("waited after known failure"))
    with pytest.raises(RuntimeError, match="failed to start.*bind fixture"):
        daemon_manager._send_capsule(short_root, "synthetic-run", {})


def test_spawn_stderr_is_captured(short_root, monkeypatch):
    observed = []
    def fake_popen(*args, **kwargs):
        stream = kwargs["stderr"]
        stream.write(b"fixture startup diagnostic\n")
        observed.append(stream.name)
    monkeypatch.setattr(daemon_manager, "_manager_runtime_identity", lambda: {"fixture":"same"})
    monkeypatch.setattr(daemon_manager.subprocess, "Popen", fake_popen)
    daemon_manager._ensure_manager(short_root, pool_size=1)
    log = daemon_manager._manager_dir(short_root) / "manager.stderr.log"
    assert observed == [str(log)]
    assert log.read_bytes() == b"fixture startup diagnostic\n"
