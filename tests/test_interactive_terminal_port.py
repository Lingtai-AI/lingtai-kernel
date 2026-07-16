"""Contract and POSIX mechanism tests for the interactive terminal capability."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

if os.name != "posix":
    pytest.skip("POSIX interactive terminal adapter tests", allow_module_level=True)

from lingtai.tools.daemon.interactive_terminal import (
    InteractiveTerminalCommand,
)
from lingtai.adapters.posix import interactive_terminal as posix_interactive_terminal
from lingtai.adapters.posix.interactive_terminal import PosixInteractiveTerminalAdapter


def _read_until(port, handle, marker: bytes, timeout: float = 3.0) -> bytes:
    deadline = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < deadline:
        for chunk in port.read(handle, deadline=min(deadline, time.monotonic() + 0.1)):
            received.extend(chunk)
            if marker in received:
                return bytes(received)
    raise AssertionError(f"did not receive {marker!r}: {received!r}")


def test_command_freezes_argv_environment_and_dimensions(tmp_path: Path):
    argv = ["child", "--raw"]
    environment = [["RAW_TEST", "one"]]
    command = InteractiveTerminalCommand(argv, tmp_path, environment)
    argv.append("changed")
    environment[0][1] = "changed"

    assert command.argv == ("child", "--raw")
    assert command.environment == (("RAW_TEST", "one"),)
    assert command.columns == 120
    assert command.rows == 40
    with pytest.raises((AttributeError, TypeError)):
        command.columns = 80


def test_posix_port_is_bidirectional_raw_bytes_and_reaped_release(
    tmp_path: Path,
    monkeypatch,
):
    port = PosixInteractiveTerminalAdapter()
    script = (
        "import sys; "
        "sys.stdout.buffer.write(b'\\x1b[?1;2cREADY\\x00'); "
        "sys.stdout.buffer.flush(); "
        "data=sys.stdin.buffer.read(6); "
        "sys.stdout.buffer.write(b'OUT:' + data + b'\\x1b[6n'); "
        "sys.stdout.buffer.flush()"
    )
    handle = port.spawn(
        InteractiveTerminalCommand(
            (sys.executable, "-c", script),
            tmp_path,
            tuple(os.environ.items()),
        ),
        group_id="interactive-test",
    )
    try:
        assert port.release(handle) is False
        initial = _read_until(port, handle, b"READY")
        assert b"\x1b[?1;2c" in initial

        actual_write = posix_interactive_terminal.os.write
        writes = []

        def write_at_most_two_bytes(fd, data):
            chunk = bytes(data[:2])
            writes.append(chunk)
            return actual_write(fd, chunk)

        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                posix_interactive_terminal.os,
                "write",
                write_at_most_two_bytes,
            )
            port.write(handle, b"hello\n")
        assert b"".join(writes) == b"hello\n"

        echoed = _read_until(port, handle, b"OUT:hello")
        assert b"OUT:hello" in echoed
        receipt = port.wait(handle, timeout=3)
        assert receipt.returncode == 0
    finally:
        # A terminal child is reaped before release; release is idempotent.
        if port.release(handle) is False:
            port.terminate(handle, reason="test_cleanup")
            assert port.release(handle) is True
        assert port.release(handle) is True


def test_posix_port_group_all_and_first_cause(tmp_path: Path):
    port = PosixInteractiveTerminalAdapter(term_timeout=0.1, kill_timeout=0.1)
    script = "import time; time.sleep(30)"
    handles = [
        port.spawn(
            InteractiveTerminalCommand((sys.executable, "-c", script), tmp_path),
            group_id="group-a",
        )
        for _ in range(2)
    ]
    other = port.spawn(
        InteractiveTerminalCommand((sys.executable, "-c", script), tmp_path),
        group_id="group-b",
    )
    try:
        assert port.release(handles[0]) is False
        with pytest.raises(TimeoutError, match="wait deadline expired"):
            port.wait(handles[0], timeout=0)
        assert port.terminate_group("group-a", reason="timeout") == 2
        first = port.terminate(handles[0], reason="reclaim")
        assert first.reason == "timeout"
        assert port.terminate_all(reason="agent_stop") == 3
        assert port.release(handles[0]) is True
        assert port.release(handles[1]) is True
        assert port.release(other) is True
    finally:
        # The assertions above normally reap all children. Keep the cleanup
        # bounded without releasing a live child.
        port.terminate_all(reason="test_cleanup")
        for handle in (*handles, other):
            port.release(handle)
