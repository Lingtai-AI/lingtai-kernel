"""Deterministic regressions for the lifecycle stop-wake fix.

Baseline symptom (see tmp/test-suite-baseline-profile-20260713.md): the main
run loop blocks in ``inbox.get(timeout=_inbox_timeout)`` and the heartbeat loop
sleeps in an uninterruptible ``time.sleep(1.0)``. ``stop()`` only sets the
``_shutdown`` Event, so neither wait is woken — the run loop waits out the full
poll interval (and, with a large ``_inbox_timeout``, effectively forever) and
the final heartbeat stop waits out the current 1s cadence tick.

These tests pin the fix WITHOUT lowering the permanent poll interval or the 1s
heartbeat cadence:

* ``stop()`` enqueues a no-payload ``MSG_TC_WAKE`` so the blocked ``inbox.get``
  returns immediately, and ``_run_loop`` re-checks shutdown after the dequeue so
  the wake never becomes a turn.
* ``_stop_heartbeat`` sets a dedicated ``_heartbeat_stop`` Event that the
  heartbeat cadence waits on, so the final stop interrupts the wait immediately
  while ordinary teardown (``_shutdown`` set) keeps the heartbeat alive.

Both tests use Events/gates rather than arbitrary sleeps so the pre-fix failure
is specifically "no wake was posted / the cadence is not interruptible", not a
generic timing threshold.
"""
from __future__ import annotations

import queue
import threading

from lingtai.adapters.posix.agent_presence import PosixAgentPresenceStoreAdapter
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from lingtai.kernel import BaseAgent, AgentState
from lingtai.kernel.message import _make_message, MSG_TC_WAKE
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS
from tests._service_helpers import make_tool_result_mock_service as make_mock_service
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import (
    make_test_snapshot_port,
    make_test_source_revision_port,
)
from tests._notification_store_helpers import notification_store_for


def _make_agent(tmp_path):
    workdir = tmp_path / "stop_wake_agent"
    return BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        agent_name="stop-wake",
        working_dir=workdir,
        workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(),
        source_revision_port=make_test_source_revision_port(),
        notification_store=notification_store_for(workdir),
        agent_presence=PosixAgentPresenceStoreAdapter(workdir),
        lifecycle_clock=make_test_lifecycle_clock(),
    )


class _GatedQueue(queue.Queue):
    """Inbox that signals the moment the run loop enters a blocking ``get``.

    ``get_entered`` lets the test observe that the loop is parked in
    ``inbox.get`` before it triggers stop, so the assertion targets the missing
    wake rather than a race on loop startup.
    """

    def __init__(self):
        super().__init__()
        self.get_entered = threading.Event()

    def get(self, block=True, timeout=None):
        self.get_entered.set()
        return super().get(block=block, timeout=timeout)


class _RecordingEvent(threading.Event):
    """Event that records every ``wait`` outcome from the heartbeat cadence.

    ``wait_started`` pulses when the loop enters a cadence wait; ``waits`` keeps
    each completed wait's return value (``True`` == woken by ``set``, ``False``
    == 1s cadence timeout). On the pre-fix loop (``time.sleep(1.0)``) this
    Event's ``wait`` is never called, so ``wait_started`` never fires.
    """

    def __init__(self):
        super().__init__()
        self.waits: list[bool] = []
        self.wait_started = threading.Event()

    def wait(self, timeout=None):
        self.wait_started.set()
        result = super().wait(timeout)
        self.waits.append(result)
        return result


def test_stop_posts_wake_that_unblocks_a_parked_run_loop(tmp_path):
    """``stop()`` must wake a run loop parked in ``inbox.get`` and let it exit.

    A very large ``_inbox_timeout`` makes the awake-path ``inbox.get`` block
    effectively forever without a wake. On baseline ``stop()`` sets ``_shutdown``
    but posts no wake, so the loop stays blocked and the thread never exits —
    ``is_alive()`` stays True after ``stop()`` returns. The fix posts a
    no-payload ``MSG_TC_WAKE`` and re-checks shutdown after the dequeue, so the
    loop returns from ``get`` and exits promptly without running a turn.
    """
    agent = _make_agent(tmp_path)

    gated = _GatedQueue()
    agent.inbox = gated
    # Large enough that a poll-only exit cannot happen within the test; the exit
    # MUST come from a posted wake, not from the interval expiring.
    agent._inbox_timeout = 3600.0

    agent._thread = threading.Thread(
        target=agent._run_loop, daemon=True, name="stop-wake-run-loop"
    )
    agent._thread.start()
    try:
        assert gated.get_entered.wait(timeout=5.0), (
            "run loop never parked in inbox.get(); cannot test the stop wake"
        )
        # The loop is IDLE and blocked in inbox.get(timeout=3600); it has not
        # started a turn.
        assert agent._state == AgentState.IDLE

        agent.stop(timeout=2.0)

        # The only way the thread can be gone is that stop() posted a wake that
        # unblocked inbox.get and the post-dequeue shutdown re-check exited the
        # loop. On baseline (no wake) the thread is still parked here.
        assert not agent._thread.is_alive(), (
            "run loop still parked after stop(): no MSG_TC_WAKE posted to "
            "unblock inbox.get(timeout=3600)"
        )
        # Invariant: the shutdown wake must NOT become a turn — the loop broke
        # out before transitioning to ACTIVE.
        assert agent._state != AgentState.ACTIVE
    finally:
        agent._shutdown.set()
        try:
            gated.put_nowait(_make_message(MSG_TC_WAKE, "system", ""))
        except Exception:
            pass
        if agent._thread is not None:
            agent._thread.join(timeout=2.0)


def test_final_stop_heartbeat_interrupts_cadence_shutdown_keeps_it_alive(tmp_path):
    """Final ``_stop_heartbeat`` interrupts the 1s cadence; ``_shutdown`` doesn't.

    The heartbeat cadence must be a dedicated interruptible ``_heartbeat_stop``
    Event so the final stop wakes it immediately. Baseline uses
    ``time.sleep(1.0)`` and has no such Event, so the instrumented Event's
    ``wait`` is never entered and the first gate assertion fails.

    It also pins the teardown contract: setting ``_shutdown`` (start of
    ``_stop``) must NOT stop the heartbeat — only the final ``_stop_heartbeat``
    (which sets the dedicated Event) does.
    """
    agent = _make_agent(tmp_path)

    rec = _RecordingEvent()
    # Install the instrumented Event where the loop is expected to wait. After
    # the fix, _start_heartbeat clears this same Event and the loop waits on it.
    agent._heartbeat_stop = rec

    hb_file = agent._working_dir / ".agent.heartbeat"
    agent._start_heartbeat()
    try:
        # (1) The cadence must be the dedicated interruptible Event's wait.
        assert rec.wait_started.wait(timeout=5.0), (
            "heartbeat cadence never entered _heartbeat_stop.wait(): the loop is "
            "still sleeping in an uninterruptible time.sleep(1.0)"
        )

        # (2) Teardown sets _shutdown first — the heartbeat must stay alive.
        agent._shutdown.set()
        assert not rec.is_set(), "_shutdown must not set the heartbeat-stop Event"
        rec.wait_started.clear()
        assert rec.wait_started.wait(timeout=5.0), (
            "heartbeat stopped cycling after _shutdown was set (lifetime "
            "shortened during teardown)"
        )
        assert agent._heartbeat_thread is not None
        assert hb_file.exists()

        # (3) Only the final stop wakes the cadence wait. Gate on a fresh wait so
        # the wake lands on an in-progress (or immediately-entered) wait.
        rec.wait_started.clear()
        assert rec.wait_started.wait(timeout=5.0)
        hb_thread = agent._heartbeat_thread

        agent._stop_heartbeat()

        assert rec.waits, "no cadence wait ever completed"
        assert rec.waits[-1] is True, (
            "final _stop_heartbeat did not wake the cadence wait (it timed out "
            "instead of being interrupted by the dedicated Event)"
        )
        assert not hb_thread.is_alive()
        assert not hb_file.exists()
    finally:
        # Ensure the loop cannot outlive the test if an assertion fires early.
        thread = agent._heartbeat_thread
        agent._heartbeat_thread = None
        rec.set()
        if thread is not None:
            thread.join(timeout=2.0)
