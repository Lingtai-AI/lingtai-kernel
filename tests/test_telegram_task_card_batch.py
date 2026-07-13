"""BaseAgent batch/rows/heartbeat orchestration for the Task Card.

The pre-dispatch hook builds one row per tool call and keeps a rolling window of
the newest three tool rows in pre-dispatch order (Jason: append in actual order,
drop the oldest displayed tool when a fourth enters — sequential completed rows
are NOT cleared down to only the current tool).  The result hook freezes a
completed row (final whole-second elapsed + done marker) on the orchestrating
thread while other rows keep ticking; a row that has already scrolled out of the
window can no longer mutate it.  A 0.5s heartbeat edits the same card with fresh
whole-second elapsed values (floor display, so half-second frames read 0s, 0s,
1s, 1s, 2s), never sends a new card per tick, uses a monotonic clock, and a stale
timer can never overwrite a newer window, a recreated card, or the frozen
last-behavior state.  The three-row cap applies to ordinary tool rows only; the
single API-error row keeps its own lifecycle/visibility.

Clock and sleep are injected so the tick logic is exercised without real time.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

import pytest

from lingtai.kernel.base_agent import BaseAgent, _TASK_CARD_TOOL


@pytest.fixture
def roll3(monkeypatch):
    """Set the rolling window to 3 ordinary tool rows for latest-three coverage.

    The production default is 1 (single latest tool row); the multi-row window
    tests opt into the configurable N=3 via ``LINGTAI_TASK_CARD_MAX_TOOL_ROWS``.
    ``monkeypatch`` reverts the env var after each test, so there is no
    process-global leakage across tests.
    """
    monkeypatch.setenv("LINGTAI_TASK_CARD_MAX_TOOL_ROWS", "3")


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


# Fixed local instant so the immutable per-row start stamp is deterministic.
_FIXED_LOCAL_DT = datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7)))
_FIXED_STAMP = "04:08:08 UTC-07"


def _agent(client, clock, *, card_message_id=None):
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": card_message_id,
        "_lock": threading.RLock(),
        "clock": clock,
        "wall_clock": lambda: _FIXED_LOCAL_DT,
    }
    return agent


def _rows_of(call):
    return call[1]["rows"]


# ---------------------------------------------------------------------------
# Batch: first tool creates the card, subsequent pre-hooks append (rolling)
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


def test_parallel_pre_hooks_append_to_same_batch(roll3):
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


def _last_rows(client):
    # Rows of the most recent reverse call (create or update), whichever is last.
    return _rows_of(client.calls[-1]) if client.calls else []


def test_sequential_completed_tools_accumulate_up_to_three(roll3):
    """Sequential tools are NOT cleared to just the current tool after each one
    finishes — they accumulate as a rolling window (Jason #6894/#6899-followup)."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    # Two sequential tools, each completing before the next starts.
    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "s1"}, tool_call_id="c1")
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "s2"}, tool_call_id="c2")

    rows = _last_rows(client)
    # The completed first row is retained beside the active second row.
    assert [r["tool"] for r in rows] == ["bash", "read"]
    by_tool = {r["tool"]: r for r in rows}
    assert by_tool["bash"]["done"] is True
    assert by_tool["read"]["done"] is False


def test_four_sequential_completed_tools_render_latest_three_in_order(roll3):
    """Four sequential tools → the card shows [2,3,4] in order, not just [4]."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    for i in range(1, 5):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"s{i}"}, tool_call_id=f"c{i}")
        agent._on_tool_result_hook(f"tool{i}", {}, {"ok": True}, tool_call_id=f"c{i}")

    rows = _last_rows(client)
    assert [r["tool"] for r in rows] == ["tool2", "tool3", "tool4"]


def test_dropped_row_stale_result_cannot_mutate_window(roll3):
    """Once a tool has scrolled out of the newest-three window, its (late) result
    hook must not resurrect it or otherwise change the visible rows."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    # Four sequential tools push tool1 out of the window.
    for i in range(1, 5):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"s{i}"}, tool_call_id=f"c{i}")
        if i < 4:
            agent._on_tool_result_hook(f"tool{i}", {}, {"ok": True}, tool_call_id=f"c{i}")

    window_before = [r["tool"] for r in _last_rows(client)]
    calls_before = len(client.calls)
    assert window_before == ["tool2", "tool3", "tool4"]

    # A late/stale result for the already-dropped tool1 must be a no-op render.
    agent._on_tool_result_hook("tool1", {}, {"ok": True}, tool_call_id="c1")

    assert [r["tool"] for r in _last_rows(client)] == ["tool2", "tool3", "tool4"]
    # No extra reverse call was made for the dropped row (nothing to freeze).
    assert len(client.calls) == calls_before


def test_completed_row_is_retained_not_cleared_when_next_tool_starts(roll3):
    """Regression for the exact behavior Jason flagged: when the previous tool
    finished and a new tool starts, the completed row must NOT be cleared down to
    only the current tool — it stays in the rolling window (N=3 here)."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook(
        "bash", {"_reasoning": "step1", "action": "run"}, tool_call_id="c1")
    agent._on_tool_result_hook(
        "bash", {"_reasoning": "step1"}, {"ok": True}, tool_call_id="c1")

    # Next tool → appended into the rolling window beside the completed one.
    agent._on_tool_pre_dispatch_hook(
        "read", {"_reasoning": "step2"}, tool_call_id="c2")

    rows = _rows_of(client.updates()[-1]) if client.updates() else _rows_of(client.creates()[-1])
    assert [r["tool"] for r in rows] == ["bash", "read"]
    # The retained first row keeps its completed marker.
    assert {r["tool"]: r["done"] for r in rows} == {"bash": True, "read": False}


# ---------------------------------------------------------------------------
# Default (env unset): the window shows only the latest ONE ordinary tool row
# ---------------------------------------------------------------------------

def test_default_unset_sequential_shows_only_latest_one_tool(monkeypatch):
    """With ``LINGTAI_TASK_CARD_MAX_TOOL_ROWS`` unset the default is 1: after a
    prior tool completes and a new one starts, only the newest tool row shows."""
    monkeypatch.delenv("LINGTAI_TASK_CARD_MAX_TOOL_ROWS", raising=False)
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "s1"}, tool_call_id="c1")
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "s2"}, tool_call_id="c2")

    assert [r["tool"] for r in _last_rows(client)] == ["read"]


def test_default_unset_parallel_keeps_only_latest_one_tool(monkeypatch):
    """Default 1 also caps a parallel batch to the single newest pre-dispatch."""
    monkeypatch.delenv("LINGTAI_TASK_CARD_MAX_TOOL_ROWS", raising=False)
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "a"}, tool_call_id="c1")
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "b"}, tool_call_id="c2")
    agent._on_tool_pre_dispatch_hook("grep", {"_reasoning": "c"}, tool_call_id="c3")

    assert [r["tool"] for r in _last_rows(client)] == ["grep"]


def test_env_two_keeps_latest_two_tools(monkeypatch):
    """A positive value other than 3 also works: N=2 keeps the newest two."""
    monkeypatch.setenv("LINGTAI_TASK_CARD_MAX_TOOL_ROWS", "2")
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    for i in range(1, 5):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"s{i}"}, tool_call_id=f"c{i}")
        agent._on_tool_result_hook(f"tool{i}", {}, {"ok": True}, tool_call_id=f"c{i}")

    assert [r["tool"] for r in _last_rows(client)] == ["tool3", "tool4"]


# ---------------------------------------------------------------------------
# Freeze: completed row frozen while others keep ticking
# ---------------------------------------------------------------------------

def test_completed_row_freezes_final_elapsed_while_other_advances(roll3):
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook(
        "bash", {"_reasoning": "build"}, tool_call_id="c1")  # started t=1000
    agent._on_tool_pre_dispatch_hook(
        "read", {"_reasoning": "open"}, tool_call_id="c2")   # started t=1000

    clock.advance(4.5)  # now t=1004.5
    # c1 completes at 4.5s elapsed and freezes floored to whole seconds (4).
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")

    clock.advance(3)  # now t=1007.5
    # Heartbeat tick: c2 reads floor(7.5)=7s, c1 stays frozen at floor(4.5)=4s.
    agent._task_card_heartbeat_tick()

    rows = _rows_of(client.updates()[-1])
    by_tool = {r["tool"]: r for r in rows}
    assert by_tool["bash"]["done"] is True
    assert by_tool["bash"]["elapsed_s"] == 4
    assert by_tool["read"]["done"] is False
    assert by_tool["read"]["elapsed_s"] == 7


# ---------------------------------------------------------------------------
# Rolling newest-three window: parallel entry, retained-row liveness, freeze
# ---------------------------------------------------------------------------

def test_four_parallel_pre_dispatches_render_latest_three_in_order(roll3):
    """Four parallel pre-dispatches (all serialized before any future starts)
    render the newest three in pre-dispatch order; the oldest is dropped."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    for i in range(1, 5):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"p{i}"}, tool_call_id=f"c{i}")

    rows = _last_rows(client)
    assert [r["tool"] for r in rows] == ["tool2", "tool3", "tool4"]
    assert all(r["done"] is False for r in rows)


def test_retained_parallel_rows_keep_live_elapsed_and_freeze_on_own_result(roll3):
    """Rows retained in the window keep ticking; each freezes on its OWN result,
    and the dropped tool1's late result never re-enters the window."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    # Four parallel tools, started together at t=1000; tool1 scrolls out.
    for i in range(1, 5):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"p{i}"}, tool_call_id=f"c{i}")
    assert [r["tool"] for r in _last_rows(client)] == ["tool2", "tool3", "tool4"]

    clock.advance(3)
    # tool3 completes; tool2 and tool4 keep ticking.
    agent._on_tool_result_hook("tool3", {}, {"ok": True}, tool_call_id="c3")
    clock.advance(2)  # t=1005
    agent._task_card_heartbeat_tick()

    by_tool = {r["tool"]: r for r in _last_rows(client)}
    assert by_tool["tool3"]["done"] is True
    assert by_tool["tool3"]["elapsed_s"] == 3          # frozen at its result
    assert by_tool["tool2"]["done"] is False
    assert by_tool["tool2"]["elapsed_s"] == 5          # still live
    assert by_tool["tool4"]["done"] is False
    assert by_tool["tool4"]["elapsed_s"] == 5

    # tool1 was dropped; its late result cannot bring it back into the window.
    agent._on_tool_result_hook("tool1", {}, {"ok": True}, tool_call_id="c1")
    assert [r["tool"] for r in _last_rows(client)] == ["tool2", "tool3", "tool4"]


def test_stale_generation_tick_cannot_overwrite_rolling_window(roll3):
    """A heartbeat tick carrying an OLD generation must not touch the rolling
    window after the window has advanced to a newer epoch."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "b1"}, tool_call_id="c1")
    gen1 = agent._telegram_task_card_context["generation"]
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")

    # A second sequential tool advances the epoch (new generation) while keeping
    # the completed first row in the rolling window.
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "b2"}, tool_call_id="c2")
    assert agent._telegram_task_card_context["generation"] != gen1
    window = [r["tool"] for r in _last_rows(client)]
    assert window == ["bash", "read"]

    updates_before = len(client.updates())
    clock.advance(5)
    # A tick from the OLD generation must be a no-op — the window is preserved.
    agent._task_card_heartbeat_tick(generation=gen1)
    assert len(client.updates()) == updates_before
    assert [r["tool"] for r in _last_rows(client)] == ["bash", "read"]


# ---------------------------------------------------------------------------
# API-error row coexists with the newest-three tool window
# ---------------------------------------------------------------------------
#
# Lifecycle note (preserved from before the rolling window): a NEW epoch — a
# tool arriving with no active tool predecessor — supersedes a lingering
# API-error row, because a fresh tool batch means the LLM has responded past the
# error.  An API error reported WHILE a tool batch is active instead coexists,
# and there the newest-three *tool* cap must never evict the API-error row.

def test_api_error_reported_mid_batch_survives_the_three_tool_cap(roll3):
    """An API error surfaced while tools are active coexists with the tool rows,
    and the newest-three cap evicts only TOOL rows — never the API-error row."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    class Exc(Exception):
        status_code = 429
        code = "usage_limit_reached"

    # Open the epoch with a live tool, surface an API error mid-batch, then let
    # more tools arrive while the batch stays active (each still running).
    agent._on_tool_pre_dispatch_hook("tool1", {"_reasoning": "s1"}, tool_call_id="c1")
    agent._report_task_card_api_error(Exc(), attempt=1, max_attempts=3, terminal=False)
    for i in range(2, 6):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"s{i}"}, tool_call_id=f"c{i}")

    rows = _last_rows(client)
    tool_rows = [r for r in rows if r.get("kind") != "api_error"]
    api_rows = [r for r in rows if r.get("kind") == "api_error"]
    # Newest three TOOL rows, in order...
    assert [r["tool"] for r in tool_rows] == ["tool3", "tool4", "tool5"]
    # ...and the API-error row is still present (not dropped by the cap).
    assert len(api_rows) == 1
    assert api_rows[0]["status"] == 429


def test_tool_cap_never_exceeds_three_while_api_error_coexists(roll3):
    """Under sustained churn within one active batch the payload never carries
    more than three tool rows; the coexisting API-error row is not counted."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    class Exc(Exception):
        status_code = 500
        code = None

    # First tool opens the epoch; the API error then coexists for the rest of the
    # active batch (no tool ever completes, so no new epoch supersedes it).
    agent._on_tool_pre_dispatch_hook("tool1", {"_reasoning": "s1"}, tool_call_id="c1")
    agent._report_task_card_api_error(Exc(), attempt=1, max_attempts=3, terminal=False)
    for i in range(2, 8):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"s{i}"}, tool_call_id=f"c{i}")
        rows = _last_rows(client)
        tool_rows = [r for r in rows if r.get("kind") != "api_error"]
        assert len(tool_rows) <= 3

    rows = _last_rows(client)
    assert [r["tool"] for r in rows if r.get("kind") != "api_error"] == [
        "tool5", "tool6", "tool7"]
    assert any(r.get("kind") == "api_error" for r in rows)


def test_new_epoch_supersedes_lingering_api_error_row():
    """Preserved lifecycle: when a completed batch is followed by a fresh tool
    (new epoch), a lingering API-error row is superseded — but this is the epoch
    transition, NOT the tool-row cap."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    class Exc(Exception):
        status_code = 429
        code = "usage_limit_reached"

    agent._report_task_card_api_error(Exc(), attempt=1, max_attempts=1, terminal=True)
    # A brand-new tool batch (no active tool predecessor) opens a new epoch.
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "next"}, tool_call_id="c9")

    rows = _last_rows(client)
    assert not any(r.get("kind") == "api_error" for r in rows)
    assert [r.get("tool") for r in rows] == ["read"]


# ---------------------------------------------------------------------------
# Env parser: LINGTAI_TASK_CARD_MAX_TOOL_ROWS positive-int / fail-safe-to-1
# ---------------------------------------------------------------------------

def _max_rows():
    from lingtai.kernel.base_agent import _task_card_max_tool_rows
    return _task_card_max_tool_rows()


def test_env_parser_unset_defaults_to_one(monkeypatch):
    monkeypatch.delenv("LINGTAI_TASK_CARD_MAX_TOOL_ROWS", raising=False)
    assert _max_rows() == 1


@pytest.mark.parametrize("value,expected", [
    ("1", 1),
    ("2", 2),
    ("3", 3),
    ("10", 10),
    ("  4  ", 4),   # surrounding whitespace tolerated
])
def test_env_parser_positive_integers(monkeypatch, value, expected):
    monkeypatch.setenv("LINGTAI_TASK_CARD_MAX_TOOL_ROWS", value)
    assert _max_rows() == expected


@pytest.mark.parametrize("value", [
    "",          # empty
    "   ",       # blank
    "abc",       # non-integer
    "1.5",       # float text is not an int
    "3x",        # trailing junk
    "0",         # zero
    "-1",        # negative
    "-5",        # negative
])
def test_env_parser_invalid_or_nonpositive_falls_back_to_one(monkeypatch, value):
    monkeypatch.setenv("LINGTAI_TASK_CARD_MAX_TOOL_ROWS", value)
    assert _max_rows() == 1


# ---------------------------------------------------------------------------
# Heartbeat: same message edited, monotonic elapsed, no per-tick create
# ---------------------------------------------------------------------------

def test_heartbeat_edits_same_card_never_sends_new():
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    for _ in range(3):
        clock.advance(1)
        agent._task_card_heartbeat_tick()

    # Exactly one create; every heartbeat is an update of the same card.
    assert len(client.creates()) == 1
    assert len(client.updates()) >= 3
    # Elapsed is monotonic non-decreasing across ticks.
    elapsed_seq = [_rows_of(u)[0]["elapsed_s"] for u in client.updates()]
    assert elapsed_seq == sorted(elapsed_seq)
    assert elapsed_seq[-1] == 3


def test_heartbeat_half_second_cadence_floors_to_whole_seconds():
    """At the 0.5s cadence, whole-second floor display yields duplicated integer
    frames 0s, 0s, 1s, 1s, 2s over successive half-second ticks (Jason's spec)."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    frames = []
    for _ in range(5):
        clock.advance(0.5)
        agent._task_card_heartbeat_tick()
        frames.append(_rows_of(client.updates()[-1])[0]["elapsed_s"])

    # +0.5→0, +0.5→1, +0.5→1, +0.5→2, +0.5→2 (floor of 0.5,1.0,1.5,2.0,2.5)
    assert frames == [0, 1, 1, 2, 2]
    # No frame carries a decimal point once rendered.
    from lingtai.mcp_servers.telegram.manager import TelegramManager
    rendered = TelegramManager._format_task_card_text(
        "", "", "", rows=[{"tool": "bash", "tool_action": "",
                           "reasoning": "x", "elapsed_s": frames[-1], "done": False}])
    assert "2s" in rendered and "2.0s" not in rendered


def test_heartbeat_interval_is_half_second():
    """The mechanical cadence constant is 0.5s (Jason's live-validated request)."""
    from lingtai.kernel.base_agent import BaseAgent
    assert BaseAgent._TASK_CARD_HEARTBEAT_INTERVAL == 0.5


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
    assert finals[0][1]["account"] == "mybot"
    assert finals[0][1]["chat_id"] == 123
    rows = finals[0][1]["rows"]
    # Concrete last-behavior row, frozen, no generic DONE subject; the immutable
    # captured start stamp survives the freeze.
    assert rows == [{
        "tool": "bash", "tool_action": "run", "reasoning": "build",
        "elapsed_s": 3, "done": True, "started_at": _FIXED_STAMP,
    }]
    assert agent._telegram_task_card_context is None


def test_teardown_keeps_latest_three_concrete_rows(roll3):
    """Finalization freezes the newest-three rolling window (not just the last
    tool), so the resident card's last-behavior record shows [2,3,4]."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    for i in range(1, 5):
        agent._on_tool_pre_dispatch_hook(
            f"tool{i}", {"_reasoning": f"s{i}"}, tool_call_id=f"c{i}")
        agent._on_tool_result_hook(f"tool{i}", {}, {"ok": True}, tool_call_id=f"c{i}")

    agent._teardown_telegram_task_card()

    finals = [c for c in client.calls if c[1].get("sub_action") == "finalize"]
    assert len(finals) == 1
    rows = finals[0][1]["rows"]
    assert [r["tool"] for r in rows] == ["tool2", "tool3", "tool4"]
    assert all(r["done"] is True for r in rows)


def test_teardown_freezes_row_still_active_at_turn_end():
    """A row still running when the turn ends (e.g. cancellation) is frozen at
    its current elapsed, so the resident card never keeps ticking."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    clock.advance(6.5)
    agent._teardown_telegram_task_card()  # never got a result hook

    finals = [c for c in client.calls if c[1].get("sub_action") == "finalize"]
    rows = finals[0][1]["rows"]
    assert rows[0]["done"] is True
    # Frozen with whole-second floor: floor(6.5) == 6.
    assert rows[0]["elapsed_s"] == 6


# ---------------------------------------------------------------------------
# Real heartbeat thread: stops promptly on teardown/shutdown, no stale write
# ---------------------------------------------------------------------------

def test_real_heartbeat_thread_stops_on_teardown():
    """A real heartbeat thread (deterministic sleep gate) ticks the active card
    and stops promptly when the turn tears down — no real 0.5s sleeps."""
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
