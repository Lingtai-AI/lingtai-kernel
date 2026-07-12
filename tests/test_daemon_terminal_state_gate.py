# tests/test_daemon_terminal_state_gate.py
"""Tests for the daemon terminal-state priority gate.

Modernized from PR #296 (#194) against current main. Current main already
keys the terminal status off ``run_dir.state_snapshot()`` (the authoritative
P1 signal) and delivers each terminal notification exactly once via
``run_dir.claim_terminal_notification(status)``. These tests verify the *fallback*
signals layered on top of that: when the run_dir state was never recorded
(crash before mark_*, missing run_dir), the timeout_event / cancel_event /
``[cancelled]`` sentinel / elapsed-near-timeout signals still keep a
timeout/cancelled run from being mislabeled as an ordinary success. Every
terminal state now publishes the compact daemon notification, including short
successful results.

The once-only terminal-notification dedupe and the run_dir-authoritative
classification are exercised in tests/test_daemon.py and must remain intact;
a regression test for the dedupe lives here too.
"""
import queue
import threading
import time
from unittest.mock import MagicMock

from lingtai.tools.daemon import DaemonManager, _classify_terminal_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(is_set: bool) -> threading.Event:
    """Return a threading.Event in the desired state."""
    ev = threading.Event()
    if is_set:
        ev.set()
    return ev


def _make_state_run_dir(state):
    """Return a mock run_dir whose state_snapshot reports *state*."""
    rd = MagicMock()
    rd.state_snapshot.return_value = {"state": state}
    return rd


def _make_entry(
    *,
    timeout_event_set: bool = False,
    cancel_event_set: bool = False,
    run_dir_state=None,
    start_time: float | None = None,
    timeout_s: float = 3600.0,
    include_run_dir: bool = True,
) -> dict:
    """Build a synthetic emanation entry dict for classify-level unit tests."""
    entry: dict = {
        "start_time": start_time if start_time is not None else time.time() - 10.0,
        "timeout_event": _make_event(timeout_event_set),
        "cancel_event": _make_event(cancel_event_set),
        "timeout_s": timeout_s,
    }
    if include_run_dir:
        entry["run_dir"] = _make_state_run_dir(run_dir_state)
    else:
        entry["run_dir"] = None
    return entry


# ---------------------------------------------------------------------------
# _classify_terminal_state — P1 run_dir authority (preserve current main)
# ---------------------------------------------------------------------------

class TestClassifyRunDirAuthority:
    """P1: the recorded run_dir state is the most authoritative signal."""

    def test_run_dir_timeout_is_authoritative(self):
        entry = _make_entry(run_dir_state="timeout")
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "timeout"

    def test_run_dir_cancelled_is_authoritative(self):
        entry = _make_entry(run_dir_state="cancelled")
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "cancelled"

    def test_run_dir_failed_is_authoritative(self):
        entry = _make_entry(run_dir_state="failed")
        assert _classify_terminal_state(entry, True, "anything", 3600.0) == "failed"

    def test_run_dir_done_with_short_text_stays_done(self):
        """A genuinely-done run with a short body stays 'done'."""
        entry = _make_entry(run_dir_state="done")
        assert _classify_terminal_state(entry, True, "ok", 3600.0) == "done"

    def test_run_dir_state_overrides_events(self):
        """run_dir state wins over a stale event flag."""
        entry = _make_entry(run_dir_state="done", timeout_event_set=True)
        assert _classify_terminal_state(entry, True, "ok", 3600.0) == "done"

    def test_run_dir_running_is_not_terminal(self):
        """A non-terminal recorded state does not classify; fall through."""
        entry = _make_entry(run_dir_state="running")
        assert _classify_terminal_state(entry, True, "some text", 3600.0) == "done"

    def test_run_dir_snapshot_raises_falls_through(self):
        """A run_dir that raises on snapshot must not crash classification."""
        rd = MagicMock()
        rd.state_snapshot.side_effect = RuntimeError("disk gone")
        entry = {
            "start_time": time.time() - 10.0,
            "timeout_event": _make_event(True),
            "cancel_event": _make_event(False),
            "timeout_s": 3600.0,
            "run_dir": rd,
        }
        # P1 unavailable -> P2 timeout_event takes over.
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "timeout"


# ---------------------------------------------------------------------------
# _classify_terminal_state — fallback signals (the #296 contribution)
# ---------------------------------------------------------------------------

class TestClassifyFallbackSignals:
    """P2-P5: signals used only when run_dir did not record a terminal state."""

    def test_timeout_event_when_no_run_dir_state(self):
        """P2: timeout_event set, run_dir state unrecorded -> 'timeout'."""
        entry = _make_entry(run_dir_state=None, timeout_event_set=True)
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "timeout"

    def test_timeout_event_no_run_dir_at_all(self):
        """P2 still fires when there is no run_dir object."""
        entry = _make_entry(include_run_dir=False, timeout_event_set=True)
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "timeout"

    def test_cancel_event_when_no_run_dir_state(self):
        """P3: cancel_event set (timeout not set) -> 'cancelled'."""
        entry = _make_entry(run_dir_state=None, cancel_event_set=True)
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "cancelled"

    def test_timeout_event_priority_over_cancel_event(self):
        """P2 before P3: watchdog timeout sets BOTH events; timeout wins."""
        entry = _make_entry(
            run_dir_state=None, timeout_event_set=True, cancel_event_set=True,
        )
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "timeout"

    def test_cancelled_sentinel_backstop(self):
        """P4: no events, no run_dir state, text is the sentinel -> 'cancelled'."""
        entry = _make_entry(run_dir_state=None)
        assert _classify_terminal_state(entry, True, "[cancelled]", 3600.0) == "cancelled"

    def test_elapsed_near_timeout_low_priority_fallback(self):
        """P5: '[no output]' near the deadline -> 'timeout' (low-priority)."""
        entry = _make_entry(
            run_dir_state=None,
            start_time=time.time() - 3300.0,  # 0.917 of 3600
            timeout_s=3600.0,
        )
        assert _classify_terminal_state(entry, True, "[no output]", 3600.0) == "timeout"

    def test_elapsed_far_from_timeout_stays_done(self):
        """'[no output]' from a quick run is NOT a timeout."""
        entry = _make_entry(
            run_dir_state=None,
            start_time=time.time() - 5.0,
            timeout_s=3600.0,
        )
        assert _classify_terminal_state(entry, True, "[no output]", 3600.0) == "done"

    def test_elapsed_fallback_requires_no_output_sentinel(self):
        """Elapsed-near-timeout only upgrades the '[no output]' sentinel, not
        an arbitrary long-running success."""
        entry = _make_entry(
            run_dir_state=None,
            start_time=time.time() - 3300.0,
            timeout_s=3600.0,
        )
        assert _classify_terminal_state(entry, True, "real result body", 3600.0) == "done"

    def test_genuine_short_success_stays_done(self):
        """No abnormal signal anywhere -> 'done'."""
        entry = _make_entry(run_dir_state=None)
        assert _classify_terminal_state(entry, True, "42", 3600.0) == "done"

    def test_entry_none_uses_sentinel_backstop(self):
        """entry=None: no events/run_dir, only the sentinel backstop applies."""
        assert _classify_terminal_state(None, True, "[cancelled]", 3600.0) == "cancelled"

    def test_entry_none_normal_text_is_done(self):
        assert _classify_terminal_state(None, True, "all good", 3600.0) == "done"

    def test_intercepted_sentinel_is_done(self):
        """'[intercepted]' is a guard-handled normal exit, not a terminal abort."""
        entry = _make_entry(run_dir_state=None)
        assert _classify_terminal_state(entry, True, "[intercepted]", 3600.0) == "done"


# ---------------------------------------------------------------------------
# Integration: _on_emanation_done uses the gate AND preserves dedupe
# ---------------------------------------------------------------------------

def _make_agent(tmp_path):
    from lingtai.agent import Agent
    from lingtai.kernel.config import AgentConfig
    svc = MagicMock()
    svc.provider = "mock"
    svc.model = "mock-model"
    svc.create_session = MagicMock()
    svc.make_tool_result = MagicMock()
    return Agent(
        svc,
        working_dir=tmp_path / "daemon-agent",
        capabilities=["daemon"],
        config=AgentConfig(),
    )


def _real_run_dir(agent, em_id, task="test task"):
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    return DaemonRunDir(
        parent_working_dir=agent._working_dir,
        handle=em_id,
        task=task,
        tools=["file"],
        model="mock-model",
        max_turns=30,
        timeout_s=300.0,
        parent_addr=agent._working_dir.name,
        parent_pid=12345,
        system_prompt="You are a daemon.",
        call_parameters=None,
    )


class TestOnEmanationDoneIntegration:
    """Verify the gate is wired into _on_emanation_done end-to-end and that the
    once-only terminal-notification dedupe (current main) is preserved."""

    def test_timeout_event_notifies_without_run_dir_state(self, tmp_path):
        """The key #194 case via the *event* fallback: a timed-out run whose
        run_dir never recorded 'timeout' (e.g. crash before mark_timeout) still
        notifies with the correct terminal label because timeout_event is set.
        """
        from tests._notification_store_helpers import snapshot_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")
        rd = _real_run_dir(agent, "em-to-event")  # state stays 'running'

        future = MagicMock()
        future.result.return_value = "[cancelled]"
        mgr._emanations["em-to-event"] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 3601.0,
            "cancel_event": _make_event(True),
            "timeout_event": _make_event(True),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done("em-to-event", "test task", future)

        assert agent.inbox.empty()
        events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
        assert len(events) == 1
        assert "timeout" in events[0]["body"].lower()

    def test_cancel_event_notifies_without_run_dir_state(self, tmp_path):
        from tests._notification_store_helpers import snapshot_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")
        rd = _real_run_dir(agent, "em-cancel-event")

        future = MagicMock()
        future.result.return_value = "[cancelled]"
        mgr._emanations["em-cancel-event"] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 45.0,
            "cancel_event": _make_event(True),
            "timeout_event": _make_event(False),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done("em-cancel-event", "test task", future)

        events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
        assert len(events) == 1
        assert "cancelled" in events[0]["body"].lower()

    def test_genuine_short_success_notifies(self, tmp_path):
        """Even a tiny successful result must wake the parent: the daemon
        terminal notification is the completion signal, not a long-output echo.
        """
        from tests._notification_store_helpers import snapshot_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")
        rd = _real_run_dir(agent, "em-short")
        rd.mark_done("42")

        future = MagicMock()
        future.result.return_value = "42"
        mgr._emanations["em-short"] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 2.0,
            "cancel_event": _make_event(False),
            "timeout_event": _make_event(False),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done("em-short", "test task", future)

        events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
        assert len(events) == 1
        assert events[0]["ref_id"] == "em-short"
        assert "Daemon em-short done." in events[0]["body"]
        assert "Preview:\n42" in events[0]["body"]

    def test_timeout_terminal_notified_only_once(self, tmp_path):
        """Dedupe preservation: a timeout surfaced via the event fallback is
        still delivered exactly once across duplicate done-callbacks."""
        from tests._notification_store_helpers import snapshot_notifications

        agent = _make_agent(tmp_path)
        agent.inbox = queue.Queue()
        mgr = agent.get_capability("daemon")
        rd = _real_run_dir(agent, "em-once")

        future = MagicMock()
        future.result.return_value = "[cancelled]"
        mgr._emanations["em-once"] = {
            "future": future,
            "task": "test task",
            "start_time": time.time() - 3601.0,
            "cancel_event": _make_event(True),
            "timeout_event": _make_event(True),
            "run_dir": rd,
            "timeout_s": 3600.0,
        }

        mgr._on_emanation_done("em-once", "test task", future)
        mgr._on_emanation_done("em-once", "test task", future)

        events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
        daemon_events = [e for e in events if e["ref_id"] == "em-once"]
        assert len(daemon_events) == 1
        assert "timeout" in daemon_events[0]["body"].lower()
