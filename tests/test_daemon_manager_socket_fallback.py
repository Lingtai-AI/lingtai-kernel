"""Unit coverage for the capsule socket fallback on socket-less filesystems.

exFAT volumes (for example ``/Volumes/HDPH-UTV``) reject ``bind`` on an
``AF_UNIX`` socket with ``EOPNOTSUPP``; the resident daemon manager used to die
with that unlogged error, leaving a pid file that still claimed ``running``.
"""
from __future__ import annotations

import errno
import hashlib
import socket
from pathlib import Path

import pytest

from lingtai.adapters.posix import daemon_manager

import shutil
import tempfile


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
    assert daemon_manager._is_capsule_socket_fallback(socket_path) is True
    digest = hashlib.sha256(str(short_root.resolve()).encode("utf-8")).hexdigest()[:24]
    assert socket_path.name == "capsule.sock"
    assert socket_path.parent.name == f"lingtai-dm-{__import__('os').getuid()}-{digest}"
    # The agent process and the resident manager must derive the same path.
    assert daemon_manager._capsule_socket_path(short_root) == socket_path


def test_socket_path_still_falls_back_for_long_paths(short_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(daemon_manager, "_unix_socket_usable", lambda _dir: True)
    long_root = short_root / ("segment" * 12)
    assert len(str(long_root / "capsule.sock")) >= daemon_manager._UNIX_SOCKET_PATH_LIMIT
    socket_path = daemon_manager._capsule_socket_path(long_root)
    assert daemon_manager._is_capsule_socket_fallback(socket_path) is True


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
