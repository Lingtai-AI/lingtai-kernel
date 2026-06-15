# tests/test_daemon_terminal_state_gate.py
"""Tests for PR3A: daemon terminal-state priority gate.

Verifies that _classify_terminal_state correctly identifies non-normal
terminal states (timeout, cancelled, failed) so they are never swallowed
by the ``suppressed_short`` check in ``_on_emanation_done``.
"""
import queue
import threading
import time
from unittest.mock import MagicMock

from lingtai.core.daemon import DaemonManager, _classify_terminal_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(is_set: bool) -> threading.Event:
    """Return a threading.Event in the desired state."""
    ev = threading.Event()
    if is_set:
        ev.set()
    return ev


def _make_run_dir(state: str) -> MagicMock:
    """Return a mock DaemonRunDir whose state_snapshot returns *state*."""
    rd = MagicMock()
    rd.state_snapshot.return_value = {"state": state}
    return rd


def _make_entry(
    *,
    timeout_event_set: bool = False,
    cancel_event_set: bool = False,
    run_dir_state: str | None = None,
    start_time: float | None = None,
    timeout_s: float = 3600.0,
) -> dict:
    """Build a synthetic emanation entry dict."""
    entry: dict = {
        "start_time": start_time if start_time is not None else time.time() - 10.0,
        "timeout_event": _make_event(timeout_event_set),
        "cancel_event": _make_event(cancel_event_set),
        "timeout_s": timeout_s,
    }
    if run_dir_state is not None:
        entry["run_dir"] = _make_run_dir(run_dir_state)
    else:
        entry["run_dir"] = None
    return entry


def _make_agent(tmp_path):
    """Create a minimal Agent with mock LLM service."""
    from lingtai_kernel.config import AgentConfig
    svc = MagicMock()
    svc.provider = "mock"
    svc.model = "mock-model"
    svc.create_session = MagicMock()
    svc.make_tool_result = MagicMock()
    agent = DaemonManager.__init__  # just for type hints; real agent below
    from lingtai.agent import Agent
    agent = Agent(
        svc,
        working_dir=tmp_path / "daemon-agent",
        capabilities=["daemon"],
        config=AgentConfig(),
    )
    return agent


# ---------------------------------------------------------------------------
# Tests: _classify_terminal_state unit tests
# ---------------------------------------------------------------------------

class TestClassifyTerminalState:
    """Unit tests for the _classify_terminal_state helper."""

    def test_timeout_event_set_returns_timeout(self):
        """P2: timeout_event is set -> 'timeout'."""
        entry = _make_entry(timeout_event_set=True, cancel_event_set=True)
        status = _classify_terminal_state(entry, True, "[cancelled]", 3600.0)
        assert status == "timeout"

    def test_run_dir_timeout_overrides_done(self):
        """P1: run_dir.state='timeout' overrides even if text looks normal."""
        entry = _make_entry(run_dir_state="timeout")
        status = _classify_terminal_state(entry, True, "[cancelled]", 3600.0)
        assert status == "timeout"

    def test_run_dir_cancelled(self):
        """P1: run_dir.state='cancelled' -> 'cancelled'."""
        entry = _make_entry(cancel_event_set=True, run_dir_state="cancelled")
        status = _classify_terminal_state(entry, True, "[cancelled]", 3600.0)
        assert status == "cancelled"

    def test_cancel_event_set_returns_cancelled(self):
        """P3: cancel_event set, timeout_event NOT set -> 'cancelled'."""
        entry = _make_entry(cancel_event_set=True)
        status = _classify_terminal_state(entry, True, "[cancelled]", 3600.0)
        assert status == "cancelled"

    def test_cancelled_not_suppressed(self):
        """cancelled state -> status != 'done', so suppressed_short never fires."""
        entry = _make_entry(cancel_event_set=True, run_dir_state="cancelled")
        status = _classify_terminal_state(entry, True, "[cancelled]", 3600.0)
        # If status is 'done' and text is short, it would be suppressed.
        # Ensure it's not 'done'.
        assert status != "done"

    def test_run_dir_failed_defensive_override(self):
        """P1: run_dir.state='failed' -> 'failed' (defensive override)."""
        entry = _make_entry(run_dir_state="failed")
        status = _classify_terminal_state(entry, True, "some result", 3600.0)
        assert status == "failed"

    def test_failed_exception_still_notifies(self):
        """When future raises, _on_emanation_done sets status='failed' directly.

        This test verifies the integration: a failed future always produces
        status='failed' regardless of _classify_terminal_state.
        """
        # Simulate what _on_emanation_done does for a failed future:
        # It sets status = "failed" without calling _classify_terminal_state.
        # We verify the classify function doesn't override 'failed' from run_dir.
        entry = _make_entry(run_dir_state="failed")
        status = _classify_terminal_state(entry, True, "Failed: some error", 3600.0)
        assert status == "failed"

    def test_genuine_short_success_still_suppressed(self):
        """No events set, run_dir.state='done', short text -> 'done'."""
        entry = _make_entry(run_dir_state="done")
        status = _classify_terminal_state(entry, True, "Not found", 3600.0)
        assert status == "done"
        # And if text < threshold, suppressed_short would fire

    def test_genuine_short_success_no_run_dir(self):
        """No events set, no run_dir, short text -> 'done'."""
        entry = _make_entry()
        status = _classify_terminal_state(entry, True, "42", 3600.0)
        assert status == "done"

    def test_timeout_priority_over_cancel(self):
        """When both timeout_event and cancel_event are set, timeout wins."""
        entry = _make_entry(
            timeout_event_set=True,
            cancel_event_set=True,
            run_dir_state="timeout",
        )
        status = _classify_terminal_state(entry, True, "[cancelled]", 3600.0)
        assert status == "timeout"

    def test_cancelled_sentinel_backstop(self):
        """P4: text='[cancelled]' with no events/run_dir -> 'cancelled'."""
        entry = _make_entry()  # no events set, no run_dir
        status = _classify_terminal_state(entry, True, "[cancelled]", 3600.0)
        assert status == "cancelled"

    def test_elapsed_near_timeout_backstop(self):
        """P5: '[no output]' near timeout -> 'timeout'."""
        # start_time was 3300s ago, timeout_s = 3600 -> elapsed/timeout = 0.917 > 0.9
        entry = _make_entry(start_time=time.time() - 3300.0, timeout_s=3600.0)
        status = _classify_terminal_state(entry, True, "[no output]", 3600.0)
        assert status == "timeout"

    def test_no_output_genuine_short_not_timeout(self):
        """'[no output]' from genuine quick result -> 'done'."""
        entry = _make_entry(
            run_dir_state="done",
            start_time=time.time() - 5.0,
            timeout_s=3600.0,
        )
        status = _classify_terminal_state(entry, True, "[no output]", 3600.0)
        assert status == "done"

    def test_entry_none(self):
        """entry is None -> falls through to 'done' or sentinel."""
        # With entry=None, no events/run_dir are available
        status = _classify_terminal_state(None, True, "[cancelled]", 3600.0)
        assert status == "cancelled"  # P4 sentinel

    def test_entry_none_normal_text(self):
        """entry=None with normal text -> 'done'."""
        status = _classify_terminal_state(None, True, "all done here", 3600.0)
        assert status == "done"

    def test_run_dir_state_running_not_terminal(self):
        """run_dir.state='running' does not trigger terminal classification."""
        entry = _make_entry(run_dir_state="running")
        status = _classify_terminal_state(entry, True, "some text", 3600.0)
        assert status == "done"

    def test_intercepted_still_suppressed(self):
        """'[intercepted]' from guard -> 'done' (genuine, guard handled it)."""
        entry = _make_entry(run_dir_state="done")
        status = _classify_terminal_state(entry, True, "[intercepted]", 3600.0)
        assert status == "done"


# ---------------------------------------------------------------------------
# Tests: Integration with _on_emanation_done
# ---------------------------------------------------------------------------

class TestOnEmanationDoneIntegration:
    """Integration tests verifying _on_emanation_done uses the gate correctly."""

    def test_timeout_not_suppressed(self, tmp_path):
        """timeout_event + [cancelled] -> notification fires, not suppressed."""
        from lingtai_kernel.notifications import collect_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")

        rd = _make_run_dir("timeout")
        rd.path = tmp_path / "run"

        em_id = "em-test-timeout"
        future = MagicMock()
        future.result.return_value = "[cancelled]"
        mgr._emanations[em_id] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 3601.0,
            "cancel_event": _make_event(True),
            "timeout_event": _make_event(True),
            "followup_buffer": "",
            "followup_lock": threading.Lock(),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done(em_id, "test task", future)

        # Verify notification was published (not suppressed)
        notifications = collect_notifications(agent._working_dir)
        events = notifications["system"]["data"]["events"]
        assert len(events) == 1
        assert "timeout" in events[0]["body"].lower()

    def test_genuine_short_still_suppressed(self, tmp_path):
        """Genuine short success -> suppressed_short, no notification."""
        from lingtai_kernel.notifications import collect_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")

        rd = _make_run_dir("done")
        rd.path = tmp_path / "run"

        em_id = "em-test-done"
        future = MagicMock()
        future.result.return_value = "42"
        mgr._emanations[em_id] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 2.0,
            "cancel_event": _make_event(False),
            "timeout_event": _make_event(False),
            "followup_buffer": "",
            "followup_lock": threading.Lock(),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done(em_id, "test task", future)

        # Verify NO notification was published (suppressed_short)
        notifications = collect_notifications(agent._working_dir)
        events = notifications.get("system", {}).get("data", {}).get("events", [])
        assert len(events) == 0

    def test_cancelled_not_suppressed(self, tmp_path):
        """Manual cancel (no timeout) -> notification fires."""
        from lingtai_kernel.notifications import collect_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")

        rd = _make_run_dir("cancelled")
        rd.path = tmp_path / "run"

        em_id = "em-test-cancel"
        future = MagicMock()
        future.result.return_value = "[cancelled]"
        mgr._emanations[em_id] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 45.0,
            "cancel_event": _make_event(True),
            "timeout_event": _make_event(False),
            "followup_buffer": "",
            "followup_lock": threading.Lock(),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done(em_id, "test task", future)

        notifications = collect_notifications(agent._working_dir)
        events = notifications["system"]["data"]["events"]
        assert len(events) == 1
        assert "cancelled" in events[0]["body"].lower()

    def test_failed_exception_notifies(self, tmp_path):
        """Future raises exception -> notification fires with status 'failed'."""
        from lingtai_kernel.notifications import collect_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")

        rd = _make_run_dir("failed")
        rd.path = tmp_path / "run"

        em_id = "em-test-fail"
        future = MagicMock()
        future.result.side_effect = RuntimeError("AuthenticationError: 401")
        mgr._emanations[em_id] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 3.0,
            "cancel_event": _make_event(False),
            "timeout_event": _make_event(False),
            "followup_buffer": "",
            "followup_lock": threading.Lock(),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done(em_id, "test task", future)

        notifications = collect_notifications(agent._working_dir)
        events = notifications["system"]["data"]["events"]
        assert len(events) == 1
        assert "failed" in events[0]["body"].lower()

    def test_timeout_priority_over_cancel_integration(self, tmp_path):
        """When both events are set, status is 'timeout', not 'cancelled'."""
        from lingtai_kernel.notifications import collect_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")

        rd = _make_run_dir("timeout")
        rd.path = tmp_path / "run"

        em_id = "em-test-priority"
        future = MagicMock()
        future.result.return_value = "[cancelled]"
        mgr._emanations[em_id] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 3601.0,
            "cancel_event": _make_event(True),
            "timeout_event": _make_event(True),
            "followup_buffer": "",
            "followup_lock": threading.Lock(),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done(em_id, "test task", future)

        notifications = collect_notifications(agent._working_dir)
        events = notifications["system"]["data"]["events"]
        assert len(events) == 1
        assert "timeout" in events[0]["body"].lower()
