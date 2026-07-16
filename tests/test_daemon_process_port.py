"""Focused contract tests for the daemon-local process boundary."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from lingtai.tools.daemon.process_port import (
    DaemonProcessCommand,
    DaemonProcessExit,
)


class _FakeDrain:
    def __init__(self, lines):
        self.lines = lines

    def join(self, timeout=2.0):
        return None


class _FakePort:
    def __init__(self):
        self.commands = []
        self.groups = {}
        self.terminated = []
        self._lines = {}

    def spawn(self, command, *, group_id=None):
        handle = object()
        self.commands.append((command, group_id))
        self.groups[handle] = group_id
        self._lines[handle] = []
        return handle

    def iter_stdout(self, handle, *, deadline=None):
        return iter(self._lines[handle])

    def drain_stderr(self, handle, *, on_line=None, thread_name="daemon-stderr"):
        return _FakeDrain([])

    def wait(self, handle, *, timeout=None):
        return DaemonProcessExit(0)

    def terminate(self, handle, *, reason=None):
        self.terminated.append((handle, reason))
        return DaemonProcessExit(-15, reason)

    def terminate_group(self, group_id, *, reason=None):
        self.terminated.append((group_id, reason))
        return sum(1 for owned_group in self.groups.values() if owned_group == group_id)

    def terminate_all(self, *, reason=None):
        self.terminated.append(("all", reason))
        return len(self.groups)

    def release(self, handle):
        return True


def test_command_is_immutable_and_never_a_shell_string(tmp_path):
    argv = ["codex", "exec", "--json"]
    environment = [["X_TEST", "1"]]
    command = DaemonProcessCommand(argv, tmp_path, environment)
    argv.append("mutated")
    environment[0][1] = "changed"
    assert command.argv == ("codex", "exec", "--json")
    assert command.environment == (("X_TEST", "1"),)
    assert isinstance(command.cwd, Path)
    try:
        command.argv += ("task",)
    except AttributeError:
        pass
    else:
        raise AssertionError("command must be immutable")

    for bad_environment in [("KEY", "VALUE"), (("KEY", 1),), (("KEY",),)]:
        try:
            DaemonProcessCommand(("codex",), tmp_path, bad_environment)
        except ValueError:
            pass
        else:
            raise AssertionError("environment must contain string key/value pairs")


def test_posix_adapter_owns_direct_spawn_flags_and_idempotent_release(tmp_path, monkeypatch):
    from lingtai.tools.daemon import posix_process

    class Proc:
        def __init__(self):
            self.stdout, self.stderr, self.returncode, self.pid = [], [], 0, 1234
            self.wait_calls = []

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            return self.returncode

    proc = Proc()
    calls = []
    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return proc
    monkeypatch.setattr(posix_process.subprocess, "Popen", fake_popen)
    port = posix_process.PosixDaemonProcessPort()
    handle = port.spawn(
        DaemonProcessCommand(("codex", "exec"), tmp_path, (("X_TEST", "1"),)),
        group_id="batch",
    )
    assert calls[0][0] == (("codex", "exec"),)
    assert calls[0][1]["start_new_session"] is True
    assert calls[0][1]["text"] is True
    assert calls[0][1]["env"] == {"X_TEST": "1"}
    assert port.release(handle) is True
    assert port.release(handle) is True
    assert proc.wait_calls == []


def test_posix_adapter_rejects_unknown_handles(tmp_path):
    from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort

    port = PosixDaemonProcessPort()
    fake = object()
    for operation in (
        lambda: port.iter_stdout(fake),
        lambda: port.drain_stderr(fake),
        lambda: port.wait(fake),
        lambda: port.terminate(fake),
    ):
        try:
            operation()
        except KeyError:
            pass
        else:
            raise AssertionError("unknown handles must fail loudly")
    assert port.release(fake) is True


def test_posix_termination_contract_and_ownership(tmp_path, monkeypatch):
    from lingtai.tools.daemon import posix_process

    class Proc:
        next_signal = {}

        def __init__(self, pid, returncode=None):
            self.pid = pid
            self.returncode = returncode
            self.stdout, self.stderr = [], []
            self.wait_calls = []

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return self.returncode

    procs = iter([Proc(101, 0), Proc(102), Proc(103), Proc(104)])
    monkeypatch.setattr(posix_process.subprocess, "Popen", lambda *a, **k: next(procs))
    signals = []

    def killpg(pid, sig):
        signals.append((pid, sig))
        proc = next(p for p in port._handles.values() if p[0].pid == pid)[0]
        if sig == posix_process.signal.SIGTERM and pid == 102:
            proc.returncode = -15

    monkeypatch.setattr(posix_process.os, "killpg", killpg)
    port = posix_process.PosixDaemonProcessPort(term_timeout=0.01, kill_timeout=0.01)
    exited = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id="batch")
    term_ok = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id="batch")
    stubborn = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id="batch")
    ungrouped = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id=None)

    assert port.terminate(exited, reason="timeout").returncode == 0
    assert signals == []
    assert port.terminate(term_ok, reason="timeout") == DaemonProcessExit(-15, "timeout")
    assert signals == [(102, posix_process.signal.SIGTERM)]
    receipt = port.terminate(stubborn, reason="reclaim")
    assert receipt == DaemonProcessExit(None, "reclaim")
    assert signals[-2:] == [(103, posix_process.signal.SIGTERM), (103, posix_process.signal.SIGKILL)]
    assert port.release(stubborn) is False
    assert stubborn in port._handles
    assert port.terminate_group("batch", reason="timeout") == 3
    assert all(pid != 104 for pid, _ in signals)
    assert port.terminate_all(reason="agent_stop") == 4
    assert (104, posix_process.signal.SIGTERM) in signals
    # The first local cause remains authoritative across later sweeps.
    assert port.terminate(stubborn, reason="agent_stop").reason == "reclaim"


def test_group_and_all_sweeps_survive_concurrent_terminal_release(tmp_path, monkeypatch):
    from lingtai.tools.daemon import posix_process

    class Proc:
        def __init__(self, pid, returncode=None):
            self.pid = pid
            self.returncode = returncode
            self.stdout, self.stderr = [], []

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return self.returncode

    for sweep in ("group", "all"):
        terminal = Proc(2301, 0)
        live = Proc(2302)
        procs = iter((terminal, live))
        monkeypatch.setattr(posix_process.subprocess, "Popen", lambda *a, **k: next(procs))
        signals = []

        def killpg(pid, sig):
            signals.append((pid, sig))
            if pid == live.pid and sig == posix_process.signal.SIGTERM:
                live.returncode = -15

        monkeypatch.setattr(posix_process.os, "killpg", killpg)
        port = posix_process.PosixDaemonProcessPort(term_timeout=0.01, kill_timeout=0.01)
        first = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id="batch")
        second = port.spawn(
            DaemonProcessCommand(("codex",), tmp_path),
            group_id="batch" if sweep == "group" else None,
        )
        real_terminate = port.terminate
        calls = []

        def release_before_first_terminate(handle, *, reason=None):
            if not calls:
                assert handle is first
                assert port.release(handle) is True
            calls.append(handle)
            return real_terminate(handle, reason=reason)

        monkeypatch.setattr(port, "terminate", release_before_first_terminate)
        reason = "timeout" if sweep == "group" else "agent_stop"
        if sweep == "group":
            targeted = port.terminate_group("batch", reason=reason)
        else:
            targeted = port.terminate_all(reason=reason)

        assert targeted == 2
        assert calls == [first, second]
        assert signals == [(live.pid, posix_process.signal.SIGTERM)]
        assert port.wait(second) == DaemonProcessExit(-15, reason)


def test_wait_observes_concurrent_first_termination_reason(tmp_path, monkeypatch):
    from lingtai.tools.daemon import posix_process

    wait_started = threading.Event()
    exited = threading.Event()

    class Proc:
        def __init__(self):
            self.pid = 2201
            self.returncode = None
            self.stdout, self.stderr = [], []

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            wait_started.set()
            if not exited.wait(timeout):
                raise subprocess.TimeoutExpired("fake", timeout)
            return self.returncode

    proc = Proc()
    monkeypatch.setattr(posix_process.subprocess, "Popen", lambda *a, **k: proc)
    signals = []

    def killpg(pid, sig):
        signals.append((pid, sig))
        if sig == posix_process.signal.SIGTERM:
            proc.returncode = -15
            exited.set()

    monkeypatch.setattr(posix_process.os, "killpg", killpg)
    port = posix_process.PosixDaemonProcessPort(term_timeout=0.1, kill_timeout=0.1)
    handle = port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    receipts = []
    failures = []

    def wait_for_exit():
        try:
            receipts.append(port.wait(handle, timeout=1.0))
        except BaseException as exc:  # captured so the parent test can assert it
            failures.append(exc)

    waiter = threading.Thread(target=wait_for_exit)
    waiter.start()
    assert wait_started.wait(timeout=1.0)
    terminated = port.terminate(handle, reason="timeout")
    waiter.join(timeout=1.0)

    assert not waiter.is_alive()
    assert failures == []
    assert terminated == DaemonProcessExit(-15, "timeout")
    assert receipts == [DaemonProcessExit(-15, "timeout")]
    assert signals == [(2201, posix_process.signal.SIGTERM)]
    # A later lifecycle sweep cannot overwrite the watchdog's first cause and
    # must not signal an already-exited child again.
    assert port.terminate(handle, reason="agent_stop") == DaemonProcessExit(-15, "timeout")
    assert signals == [(2201, posix_process.signal.SIGTERM)]


def test_fake_port_group_and_all_ownership_are_disjoint():
    port = _FakePort()
    first = port.spawn(DaemonProcessCommand(("codex",), Path(".")), group_id="batch-a")
    second = port.spawn(DaemonProcessCommand(("codex",), Path(".")), group_id=None)
    port.terminate_group("batch-a", reason="timeout")
    assert ("batch-a", "timeout") in port.terminated
    assert second not in [item[0] for item in port.terminated]
    port.terminate_all(reason="agent_stop")
    assert ("all", "agent_stop") in port.terminated


def test_codex_initial_and_resume_use_injected_port(tmp_path):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    class CodexPort(_FakePort):
        def spawn(self, command, *, group_id=None):
            handle = super().spawn(command, group_id=group_id)
            self._lines[handle] = [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n",
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "reply"}}) + "\n",
                json.dumps({"type": "turn.completed"}) + "\n",
            ]
            return handle

    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    port = CodexPort()
    manager._process_port = port
    run_dir = make_daemon_run_dir(agent, backend="codex")
    manager._run_codex_emanation("em-codex", run_dir, "task", threading.Event(), threading.Event())
    entry = {
        "run_dir": run_dir,
        "followup_lock": threading.Lock(),
        "ask_in_flight": False,
    }
    manager._emanations["em-codex"] = entry
    result = manager._handle_ask_codex("em-codex", entry, "follow up")
    assert result["status"] == "sent"
    entry["ask_future"].result(timeout=2)
    assert port.commands[0][0].argv[:4] == (
        "codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox"
    )
    assert port.commands[0][1] == run_dir.group_id
    assert port.commands[1][0].argv[:6] == (
        "codex", "exec", "resume", "thread-1", "--json",
        "--dangerously-bypass-approvals-and-sandbox",
    )
    assert port.commands[1][1] is None


def test_claude_print_initial_and_resume_use_injected_port(tmp_path, monkeypatch):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    class ClaudePort(_FakePort):
        def __init__(self):
            super().__init__()
            self.handles = []
            self.deadlines = []
            self.drained = []
            self.waited = []
            self.released = []

        def spawn(self, command, *, group_id=None):
            handle = super().spawn(command, group_id=group_id)
            self.handles.append(handle)
            if len(self.handles) == 1:
                self._lines[handle] = [
                    json.dumps({"type": "system", "session_id": "claude-session"}) + "\n",
                    json.dumps({
                        "type": "result",
                        "session_id": "claude-session",
                        "result": "initial reply",
                        "is_error": False,
                    }) + "\n",
                ]
            else:
                self._lines[handle] = [
                    json.dumps({
                        "type": "result",
                        "result": "follow-up reply",
                        "is_error": False,
                    }) + "\n",
                ]
            return handle

        def iter_stdout(self, handle, *, deadline=None):
            self.deadlines.append((handle, deadline))
            return super().iter_stdout(handle, deadline=deadline)

        def drain_stderr(self, handle, *, on_line=None, thread_name="daemon-stderr"):
            self.drained.append(handle)
            return super().drain_stderr(
                handle, on_line=on_line, thread_name=thread_name,
            )

        def wait(self, handle, *, timeout=None):
            self.waited.append((handle, timeout))
            return DaemonProcessExit(0)

        def release(self, handle):
            self.released.append(handle)
            return True

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-claude")
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    port = ClaudePort()
    manager._process_port = port
    run_dir = make_daemon_run_dir(agent, handle="em-claude", backend="claude-p")

    initial = manager._run_claude_code_emanation(
        "em-claude", run_dir, "initial task", threading.Event(),
        threading.Event(), backend_argv=["--model", "opus"],
    )
    assert initial == "initial reply"

    entry = {
        "run_dir": run_dir,
        "followup_lock": threading.Lock(),
        "ask_in_flight": False,
    }
    manager._emanations["em-claude"] = entry
    result = manager._handle_ask_cli("em-claude", entry, "follow up")
    assert result["status"] == "sent"
    assert entry["ask_future"].result(timeout=2) == {
        "status": "sent", "id": "em-claude", "output": "follow-up reply",
    }

    initial_command, initial_group = port.commands[0]
    ask_command, ask_group = port.commands[1]
    assert initial_command.argv == (
        "claude", "--print", "--dangerously-skip-permissions",
        "--output-format", "stream-json", "--verbose", "--name", "em-claude",
        "--model", "opus", "initial task",
    )
    assert ask_command.argv == (
        "claude", "--resume", "claude-session", "--print",
        "--dangerously-skip-permissions", "--output-format", "stream-json",
        "--verbose", "follow up",
    )
    assert initial_command.cwd == agent._working_dir
    assert ask_command.cwd == agent._working_dir
    assert "ANTHROPIC_API_KEY" not in dict(initial_command.environment or ())
    assert "ANTHROPIC_API_KEY" not in dict(ask_command.environment or ())
    assert initial_group == run_dir.group_id
    assert ask_group is None
    assert port.deadlines[0] == (port.handles[0], None)
    assert port.deadlines[1][0] is port.handles[1]
    assert port.deadlines[1][1] is not None
    assert port.waited[0] == (port.handles[0], None)
    assert port.waited[1][0] is port.handles[1]
    assert port.waited[1][1] is not None
    assert port.drained == port.handles
    assert port.released == port.handles
    assert entry["ask_in_flight"] is False


def test_claude_ask_port_spawn_failure_clears_in_flight(tmp_path):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    class FailingPort(_FakePort):
        def spawn(self, command, *, group_id=None):
            raise OSError("spawn failed")

    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    manager._process_port = FailingPort()
    run_dir = make_daemon_run_dir(agent, handle="em-claude", backend="claude-p")
    assert run_dir.set_session_id(
        "claude_session_id", "claude-session", overwrite=True,
    )
    entry = {
        "run_dir": run_dir,
        "followup_lock": threading.Lock(),
        "ask_in_flight": False,
    }

    result = manager._handle_ask_cli("em-claude", entry, "follow up")

    assert result == {
        "status": "error", "message": "Failed to start claude CLI: spawn failed",
    }
    assert entry["ask_in_flight"] is False


def test_cursor_initial_and_resume_use_injected_port(tmp_path):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    class CursorPort(_FakePort):
        def __init__(self):
            super().__init__()
            self.handles = []
            self.deadlines = []
            self.drained = []
            self.waited = []
            self.released = []

        def spawn(self, command, *, group_id=None):
            handle = super().spawn(command, group_id=group_id)
            self.handles.append(handle)
            if len(self.handles) == 1:
                self._lines[handle] = [
                    json.dumps({
                        "type": "system", "subtype": "init",
                        "session_id": "cursor-session", "model": "cursor-model",
                    }) + "\n",
                    json.dumps({
                        "type": "result", "subtype": "success",
                        "is_error": False, "session_id": "cursor-session",
                        "result": "initial reply",
                    }) + "\n",
                ]
            else:
                self._lines[handle] = [
                    json.dumps({
                        "type": "result", "subtype": "success",
                        "is_error": False, "result": "follow-up reply",
                    }) + "\n",
                ]
            return handle

        def iter_stdout(self, handle, *, deadline=None):
            self.deadlines.append((handle, deadline))
            return super().iter_stdout(handle, deadline=deadline)

        def drain_stderr(self, handle, *, on_line=None, thread_name="daemon-stderr"):
            self.drained.append(handle)
            return super().drain_stderr(
                handle, on_line=on_line, thread_name=thread_name,
            )

        def wait(self, handle, *, timeout=None):
            self.waited.append((handle, timeout))
            return DaemonProcessExit(0)

        def release(self, handle):
            self.released.append(handle)
            return True

    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    port = CursorPort()
    manager._process_port = port
    run_dir = make_daemon_run_dir(agent, handle="em-cursor", backend="cursor")

    initial = manager._run_cursor_emanation(
        "em-cursor", run_dir, "initial task", threading.Event(),
        threading.Event(), backend_argv=["--model", "gpt-5"],
    )
    assert initial == "initial reply"

    entry = {
        "run_dir": run_dir,
        "followup_lock": threading.Lock(),
        "ask_in_flight": False,
    }
    manager._emanations["em-cursor"] = entry
    result = manager._handle_ask_cursor("em-cursor", entry, "follow up")
    assert result["status"] == "sent"
    assert entry["ask_future"].result(timeout=2) == {
        "status": "sent", "id": "em-cursor", "output": "follow-up reply",
    }

    initial_command, initial_group = port.commands[0]
    ask_command, ask_group = port.commands[1]
    assert initial_command.argv[:7] == (
        "agent", "-p", "--force", "--output-format", "stream-json",
        "--model", "gpt-5",
    )
    assert initial_command.argv[-1].rstrip().endswith("initial task")
    assert ask_command.argv == (
        "agent", "-p", "--force", "--resume", "cursor-session",
        "--output-format", "stream-json", "follow up",
    )
    assert initial_command.cwd == agent._working_dir
    assert ask_command.cwd == agent._working_dir
    assert initial_command.environment is None
    assert ask_command.environment is None
    assert initial_group == run_dir.group_id
    assert ask_group is None
    assert port.deadlines[0] == (port.handles[0], None)
    assert port.deadlines[1][0] is port.handles[1]
    assert port.deadlines[1][1] is not None
    assert port.waited[0] == (port.handles[0], None)
    assert port.waited[1][0] is port.handles[1]
    assert port.waited[1][1] is not None
    assert port.drained == port.handles
    assert port.released == port.handles
    assert entry["ask_in_flight"] is False
    assert run_dir._state["cursor_session_id"] == "cursor-session"
    assert run_dir._state["model"] == "cursor-model"


def test_cursor_ask_port_spawn_failure_clears_in_flight(tmp_path):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    class FailingPort(_FakePort):
        def spawn(self, command, *, group_id=None):
            raise OSError("spawn failed")

    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    manager._process_port = FailingPort()
    run_dir = make_daemon_run_dir(agent, handle="em-cursor", backend="cursor")
    assert run_dir.set_session_id(
        "cursor_session_id", "cursor-session", overwrite=True,
    )
    entry = {
        "run_dir": run_dir,
        "followup_lock": threading.Lock(),
        "ask_in_flight": False,
    }

    result = manager._handle_ask_cursor("em-cursor", entry, "follow up")

    assert result == {
        "status": "error", "message": "Failed to start Cursor CLI: spawn failed",
    }
    assert entry["ask_in_flight"] is False


def test_codex_post_wait_cancellation_wins_over_zero_exit(tmp_path):
    """A late watchdog cancellation remains terminal despite a zero receipt."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir
    import threading

    cancel_event = threading.Event()

    class CancelAfterWaitPort(_FakePort):
        def spawn(self, command, *, group_id=None):
            handle = super().spawn(command, group_id=group_id)
            self._lines[handle] = [
                json.dumps({"type": "thread.started", "thread_id": "late-cancel"}) + "\n",
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "reply"}}) + "\n",
                json.dumps({"type": "turn.completed"}) + "\n",
            ]
            return handle

        def wait(self, handle, *, timeout=None):
            cancel_event.set()
            return DaemonProcessExit(0)

    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    manager._process_port = CancelAfterWaitPort()
    run_dir = make_daemon_run_dir(agent, backend="codex")
    result = manager._run_codex_emanation(
        "em-late-cancel", run_dir, "task", cancel_event, threading.Event(),
    )
    assert result == "[cancelled]"
    assert run_dir.state_snapshot()["state"] == "cancelled"
