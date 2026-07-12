"""BaseAgent batch/rows/heartbeat orchestration for the Task Card.

The pre-dispatch hook builds one row per tool call (the first active row of a
batch resets the batch; later pre-hooks append while any row is active).  The
result hook freezes a completed row (final whole-second elapsed + done marker)
on the orchestrating thread while other rows keep ticking.  A 1s heartbeat edits
the same card with fresh elapsed values, never sends a new card per tick, uses a
monotonic clock, and a stale timer can never overwrite a newer batch, a
recreated card, or the frozen last-behavior state.

Clock and sleep are injected so the tick logic is exercised without real time.
"""

from __future__ import annotations

import threading

from lingtai.kernel.base_agent import BaseAgent, _TASK_CARD_TOOL


class FakeClock:
    """Deterministic monotonic clock: advance() moves it forward."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


class FakeMCPClient:
    def __init__(self, message_id="mybot:123:100"):
        self.calls: list = []
        self._message_id = message_id
        self._next = 100

    def call_tool(self, tool_name, args, timeout=None):
        self.calls.append((tool_name, dict(args), timeout))
        # Reverse-channel invariant: private tool name, no public action.
        assert tool_name == _TASK_CARD_TOOL
        assert "action" not in args
        return {"status": "ok", "message_id": self._message_id}

    def creates(self):
        return [c for c in self.calls if c[1].get("sub_action") == "create"]

    def updates(self):
        return [c for c in self.calls if c[1].get("sub_action") == "update"]


def _agent(client, clock, *, card_message_id=None):
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": card_message_id,
        "_lock": threading.RLock(),
        "clock": clock,
    }
    return agent


def _rows_of(call):
    return call[1]["rows"]


# ---------------------------------------------------------------------------
# Batch: first active row resets, subsequent pre-hooks append
# ---------------------------------------------------------------------------

def test_first_tool_creates_card_with_one_row():
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook(
        "bash", {"_reasoning": "build", "action": "run"}, tool_call_id="c1")

    creates = client.creates()
    assert len(creates) == 1
    rows = _rows_of(creates[0])
    assert len(rows) == 1
    assert rows[0]["tool"] == "bash"
    assert rows[0]["tool_action"] == "run"
    assert rows[0]["reasoning"] == "build"
    assert rows[0]["done"] is False
    assert agent._telegram_task_card_context["card_message_id"] == "mybot:123:100"


def test_parallel_pre_hooks_append_to_same_batch():
    """Two pre-hooks while the first row is active → both rows on one card."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook(
        "bash", {"_reasoning": "build", "action": "run"}, tool_call_id="c1")
    agent._on_tool_pre_dispatch_hook(
        "read", {"_reasoning": "open"}, tool_call_id="c2")

    # First is a create, second is an update of the same card (append).
    assert len(client.creates()) == 1
    assert len(client.updates()) == 1
    rows = _rows_of(client.updates()[-1])
    assert [r["tool"] for r in rows] == ["bash", "read"]
    assert all(r["done"] is False for r in rows)


def test_next_batch_after_completion_replaces_prior_rows():
    """A new active row after the batch fully completed resets to a fresh batch
    (sequential tools don't accumulate unbounded history)."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook(
        "bash", {"_reasoning": "step1", "action": "run"}, tool_call_id="c1")
    agent._on_tool_result_hook(
        "bash", {"_reasoning": "step1"}, {"ok": True}, tool_call_id="c1")

    # Next tool → fresh batch, prior row replaced.
    agent._on_tool_pre_dispatch_hook(
        "read", {"_reasoning": "step2"}, tool_call_id="c2")

    rows = _rows_of(client.updates()[-1]) if client.updates() else _rows_of(client.creates()[-1])
    assert [r["tool"] for r in rows] == ["read"]


# ---------------------------------------------------------------------------
# Freeze: completed row frozen while others keep ticking
# ---------------------------------------------------------------------------

def test_completed_row_freezes_final_elapsed_while_other_advances():
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook(
        "bash", {"_reasoning": "build"}, tool_call_id="c1")  # started t=1000
    agent._on_tool_pre_dispatch_hook(
        "read", {"_reasoning": "open"}, tool_call_id="c2")   # started t=1000

    clock.advance(4)  # now t=1004
    # c1 completes at 4s elapsed and freezes.
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")

    clock.advance(3)  # now t=1007
    # Heartbeat tick: c2 should read 7s, c1 stays frozen at 4s.
    agent._task_card_heartbeat_tick()

    rows = _rows_of(client.updates()[-1])
    by_tool = {r["tool"]: r for r in rows}
    assert by_tool["bash"]["done"] is True
    assert by_tool["bash"]["elapsed_s"] == 4
    assert by_tool["read"]["done"] is False
    assert by_tool["read"]["elapsed_s"] == 7


# ---------------------------------------------------------------------------
# Heartbeat: same message edited, monotonic elapsed, no per-tick create
# ---------------------------------------------------------------------------

def test_heartbeat_edits_same_card_never_sends_new():
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    for step in (1, 2, 3):
        clock.advance(1)
        agent._task_card_heartbeat_tick()

    # Exactly one create; every heartbeat is an update of the same card.
    assert len(client.creates()) == 1
    assert len(client.updates()) >= 3
    # Elapsed is monotonic non-decreasing across ticks.
    elapsed_seq = [_rows_of(u)[0]["elapsed_s"] for u in client.updates()]
    assert elapsed_seq == sorted(elapsed_seq)
    assert elapsed_seq[-1] == 3


def test_heartbeat_after_all_done_does_not_tick():
    """Once every row is frozen, a stray heartbeat must not re-edit the card
    (it would overwrite the frozen last-behavior state)."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    clock.advance(2)
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")
    updates_before = len(client.updates())

    clock.advance(5)
    agent._task_card_heartbeat_tick()  # all rows done → no-op

    assert len(client.updates()) == updates_before


# ---------------------------------------------------------------------------
# Stale-timer guard: a tick from an old generation cannot overwrite a new batch
# ---------------------------------------------------------------------------

def test_stale_generation_tick_is_noop():
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "b1"}, tool_call_id="c1")
    gen1 = agent._telegram_task_card_context["generation"]

    # Complete the batch and start a new one (generation advances).
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "b2"}, tool_call_id="c2")
    assert agent._telegram_task_card_context["generation"] != gen1

    updates_before = len(client.updates())
    # A tick carrying the OLD generation must not touch the new batch.
    agent._task_card_heartbeat_tick(generation=gen1)
    assert len(client.updates()) == updates_before


# ---------------------------------------------------------------------------
# Fail-open: a heartbeat edit failure never raises
# ---------------------------------------------------------------------------

def test_heartbeat_edit_failure_is_fail_open():
    class RaisingClient(FakeMCPClient):
        def call_tool(self, tool_name, args, timeout=None):
            self.calls.append((tool_name, dict(args), timeout))
            if args.get("sub_action") == "update":
                raise RuntimeError("edit boom")
            return {"status": "ok", "message_id": self._message_id}

    client = RaisingClient()
    clock = FakeClock()
    agent = _agent(client, clock)
    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    clock.advance(1)
    agent._task_card_heartbeat_tick()  # must not raise


# ---------------------------------------------------------------------------
# Teardown freezes the last batch (no generic DONE) as the resident record
# ---------------------------------------------------------------------------

def test_teardown_finalizes_frozen_rows_not_generic_done():
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook(
        "bash", {"_reasoning": "build", "action": "run"}, tool_call_id="c1")
    clock.advance(3)
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")

    agent._teardown_telegram_task_card()

    finals = [c for c in client.calls if c[1].get("sub_action") == "finalize"]
    assert len(finals) == 1
    rows = finals[0][1]["rows"]
    # Concrete last-behavior row, frozen, no generic DONE subject.
    assert rows == [{
        "tool": "bash", "tool_action": "run", "reasoning": "build",
        "elapsed_s": 3, "done": True,
    }]
    assert agent._telegram_task_card_context is None


def test_teardown_freezes_row_still_active_at_turn_end():
    """A row still running when the turn ends (e.g. cancellation) is frozen at
    its current elapsed, so the resident card never keeps ticking."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    clock.advance(6)
    agent._teardown_telegram_task_card()  # never got a result hook

    finals = [c for c in client.calls if c[1].get("sub_action") == "finalize"]
    rows = finals[0][1]["rows"]
    assert rows[0]["done"] is True
    assert rows[0]["elapsed_s"] == 6


# ---------------------------------------------------------------------------
# Real heartbeat thread: stops promptly on teardown/shutdown, no stale write
# ---------------------------------------------------------------------------

def test_real_heartbeat_thread_stops_on_teardown():
    """A real heartbeat thread (deterministic sleep gate) ticks the active card
    and stops promptly when the turn tears down — no real 1s sleeps."""
    import threading

    client = FakeMCPClient()
    clock = FakeClock()
    tick_gate = threading.Event()   # released once per allowed tick
    ticked = threading.Event()

    def fake_sleep(_interval):
        # Block until the test releases a tick, or the stop_event is set.
        # Returns True to signal "stop requested" like Event.wait would.
        while not tick_gate.wait(timeout=0.05):
            if stop_event.is_set():
                return True
        tick_gate.clear()
        return stop_event.is_set()

    agent = BaseAgent.__new__(BaseAgent)
    stop_event = threading.Event()
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.RLock(),
        "clock": clock,
        "rows": [],
        "generation": 0,
        "heartbeat_enabled": True,
        "stop_event": stop_event,
        "sleep": fake_sleep,
    }

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    # Allow exactly one heartbeat tick.
    clock.advance(1)
    updates_before = len(client.updates())
    tick_gate.set()
    # Wait for the tick to land.
    deadline = 2.0
    import time as _t
    start = _t.monotonic()
    while len(client.updates()) <= updates_before and _t.monotonic() - start < deadline:
        _t.sleep(0.01)
    assert len(client.updates()) > updates_before

    # Teardown must stop the thread promptly.
    agent._teardown_telegram_task_card()
    thread = None  # context is cleared; thread already joined inside teardown
    assert agent._telegram_task_card_context is None
