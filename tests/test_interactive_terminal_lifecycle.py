"""Daemon ownership tests for injected interactive terminal children."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import patch

from lingtai.tools.daemon import DaemonManager


class _HeadlessPort:
    def __init__(self):
        self.all = []
        self.groups = []

    def terminate_all(self, *, reason=None):
        self.all.append(reason)
        return 0

    def terminate_group(self, group_id, *, reason=None):
        self.groups.append((group_id, reason))
        return 0


class _InteractivePort(_HeadlessPort):
    pass


def _manager(tmp_path):
    agent = SimpleNamespace(
        service=SimpleNamespace(model="test-model"),
        _working_dir=tmp_path / "agent",
        _log=lambda *args, **kwargs: None,
    )
    headless = _HeadlessPort()
    interactive = _InteractivePort()
    manager = DaemonManager(
        agent,
        process_port=headless,
        interactive_terminal_port=interactive,
    )
    return manager, headless, interactive


def test_group_watchdog_sweeps_injected_interactive_port(tmp_path):
    manager, headless, interactive = _manager(tmp_path)

    manager._kill_cli_group("batch-1", reason="timeout")

    assert headless.groups == [("batch-1", "timeout")]
    assert interactive.groups == [("batch-1", "timeout")]


def test_agent_stop_sweeps_interactive_port_and_reports_count(tmp_path):
    manager, headless, interactive = _manager(tmp_path)

    report = manager.shutdown_for_agent_stop(reason="agent_stop", wait_timeout=0)

    assert headless.all == ["agent_stop"]
    assert interactive.all == ["agent_stop"]
    assert report["interactive_terminal_processes_killed"] == 0
    assert report["cli_processes_killed"] == 0


def test_initial_and_resume_bridge_calls_use_manager_owned_port(tmp_path):
    manager, _, interactive = _manager(tmp_path)
    captured = []

    class _RunDir:
        def __init__(self):
            self.run_id = "em-test"
            self.group_id = None
            self.done = []

        def mark_done(self, text):
            self.done.append(text)

        def mark_failed(self, exc):
            raise AssertionError(f"unexpected bridge failure: {exc}")

    run_dir = _RunDir()
    entry = {
        "cancel_event": threading.Event(),
        "followup_lock": threading.Lock(),
        "ask_in_flight": True,
    }

    def fake_run_claude_interactive(**kwargs):
        captured.append(kwargs)
        final_text = "" if kwargs.get("resume_session_id") else "initial result"
        return SimpleNamespace(final_text=final_text)

    with patch(
        "lingtai.tools.daemon.run_claude_interactive",
        side_effect=fake_run_claude_interactive,
    ):
        initial = manager._run_claude_interactive_emanation(
            "em-initial",
            run_dir,
            "initial task",
            threading.Event(),
        )
        resumed = manager._run_ask_claude_interactive_stream(
            "em-resume",
            entry,
            "follow-up",
            "session-1",
            run_dir,
        )

    assert initial == "initial result"
    assert resumed == {"status": "sent", "id": "em-resume", "output": ""}
    assert run_dir.done == ["initial result"]
    assert captured[0]["terminal_port"] is interactive
    assert "resume_session_id" not in captured[0]
    assert captured[1]["terminal_port"] is interactive
    assert captured[1]["resume_session_id"] == "session-1"
    assert entry["ask_in_flight"] is False
