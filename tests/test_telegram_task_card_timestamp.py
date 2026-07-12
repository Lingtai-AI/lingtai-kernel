"""Immutable local start timestamp on each Task Card tool row.

Final UI contract: every tool row shows one local start time captured once when
that tool begins, shaped ``HH:MM:SS UTC±HH`` (hour-only offset, no colon, no
date, no ``Started`` label, no regional abbreviation).  Heartbeats/elapsed edits
never change it, and parallel rows each keep their own captured instant.  A
fixed injected wall-clock keeps these assertions deterministic (no real clock).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

from lingtai.kernel.base_agent import BaseAgent, _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.manager import TelegramManager


# ---------------------------------------------------------------------------
# Format helper — exact shape, offset sign/width, no colon/date/label
# ---------------------------------------------------------------------------

def _fmt(dt):
    return BaseAgent._format_task_card_timestamp(dt)


def test_format_negative_offset():
    dt = datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7)))
    assert _fmt(dt) == "04:08:08 UTC-07"


def test_format_positive_offset():
    dt = datetime(2026, 7, 12, 12, 8, 8, tzinfo=timezone(timedelta(hours=8)))
    assert _fmt(dt) == "12:08:08 UTC+08"


def test_format_utc_zero_offset():
    dt = datetime(2026, 7, 12, 9, 0, 0, tzinfo=timezone.utc)
    assert _fmt(dt) == "09:00:00 UTC+00"


def test_format_fractional_offset_omits_minutes():
    # India is UTC+05:30 — the final UI contract intentionally shows hour-only.
    dt = datetime(2026, 7, 12, 15, 4, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    out = _fmt(dt)
    assert out == "15:04:05 UTC+05"
    assert ":30" not in out.split("UTC")[1]  # no minute component in the offset


def test_format_has_no_date_label_or_abbreviation():
    dt = datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7)))
    out = _fmt(dt)
    assert "2026" not in out
    assert "Started" not in out
    assert "PDT" not in out and "PST" not in out
    # Time and offset only.
    assert out.count(":") == 2  # HH:MM:SS colons only, none in the offset


def test_format_zero_padded_single_digit_hour_offset():
    dt = datetime(2026, 7, 12, 1, 2, 3, tzinfo=timezone(timedelta(hours=5)))
    assert _fmt(dt) == "01:02:03 UTC+05"


# ---------------------------------------------------------------------------
# Row lifecycle: capture once at pre-dispatch, immutable across heartbeat/freeze
# ---------------------------------------------------------------------------

class FakeMCPClient:
    def __init__(self, message_id="mybot:123:100"):
        self.calls: list = []
        self._message_id = message_id

    def call_tool(self, tool_name, args, timeout=None):
        self.calls.append((tool_name, dict(args), timeout))
        assert tool_name == _TASK_CARD_TOOL
        assert "action" not in args
        return {"status": "ok", "message_id": self._message_id}

    def last_rows(self):
        return self.calls[-1][1]["rows"]


class FakeMonotonic:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


class FakeWallClock:
    """Returns a fixed local-aware datetime; ``set`` changes what the NEXT
    capture sees, so parallel rows can be given distinct instants."""

    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt

    def set(self, dt):
        self.dt = dt


def _agent(client, mono, wall):
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.RLock(),
        "clock": mono,
        "wall_clock": wall,
        "rows": [],
        "generation": 0,
    }
    return agent


def _tool_rows(client):
    return [r for r in client.last_rows() if r.get("kind") != "api_error"]


def test_tool_row_carries_started_at_timestamp():
    client = FakeMCPClient()
    mono = FakeMonotonic()
    wall = FakeWallClock(datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7))))
    agent = _agent(client, mono, wall)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "build"}, tool_call_id="c1")

    rows = _tool_rows(client)
    assert rows[0]["started_at"] == "04:08:08 UTC-07"


def test_timestamp_immutable_across_heartbeats():
    client = FakeMCPClient()
    mono = FakeMonotonic()
    wall = FakeWallClock(datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7))))
    agent = _agent(client, mono, wall)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    # Even if the wall clock moves and heartbeats fire, the captured stamp holds.
    wall.set(datetime(2026, 7, 12, 5, 0, 0, tzinfo=timezone(timedelta(hours=-7))))
    for _ in range(3):
        mono.advance(0.5)
        agent._task_card_heartbeat_tick()

    rows = _tool_rows(client)
    assert rows[0]["started_at"] == "04:08:08 UTC-07"  # unchanged
    # Elapsed still floors to whole seconds (unchanged behavior).
    assert rows[0]["elapsed_s"] == 1


def test_timestamp_immutable_across_finalize():
    client = FakeMCPClient()
    mono = FakeMonotonic()
    wall = FakeWallClock(datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7))))
    agent = _agent(client, mono, wall)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    mono.advance(2)
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")
    wall.set(datetime(2026, 7, 12, 6, 0, 0, tzinfo=timezone(timedelta(hours=-7))))
    agent._teardown_telegram_task_card()

    finals = [c for c in client.calls if c[1].get("sub_action") == "finalize"]
    frozen = [r for r in finals[-1][1]["rows"] if r.get("kind") != "api_error"]
    assert frozen[0]["started_at"] == "04:08:08 UTC-07"
    assert frozen[0]["done"] is True


def test_parallel_rows_keep_distinct_timestamps():
    client = FakeMCPClient()
    mono = FakeMonotonic()
    wall = FakeWallClock(datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7))))
    agent = _agent(client, mono, wall)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "a"}, tool_call_id="c1")
    # Second tool of the same active batch captures a later instant.
    wall.set(datetime(2026, 7, 12, 4, 8, 9, tzinfo=timezone(timedelta(hours=-7))))
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "b"}, tool_call_id="c2")

    rows = _tool_rows(client)
    by_tool = {r["tool"]: r for r in rows}
    assert by_tool["bash"]["started_at"] == "04:08:08 UTC-07"
    assert by_tool["read"]["started_at"] == "04:08:09 UTC-07"
    # A later heartbeat leaves both distinct captures intact.
    mono.advance(0.5)
    agent._task_card_heartbeat_tick()
    rows2 = {r["tool"]: r for r in _tool_rows(client)}
    assert rows2["bash"]["started_at"] == "04:08:08 UTC-07"
    assert rows2["read"]["started_at"] == "04:08:09 UTC-07"


# ---------------------------------------------------------------------------
# Rendering: standalone timestamp on the tool row, none on API-error rows
# ---------------------------------------------------------------------------

def test_manager_renders_timestamp_standalone_on_tool_row():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
    ])
    row_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    assert "04:08:08 UTC-07" in row_line
    assert "(3s)" in row_line
    # Standalone — no "Started" prose.
    assert "Started" not in text


def test_api_error_row_has_no_timestamp_field():
    client = FakeMCPClient()
    mono = FakeMonotonic()
    wall = FakeWallClock(datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7))))
    agent = _agent(client, mono, wall)

    class Exc(Exception):
        status_code = 429
        code = "usage_limit_reached"

    agent._report_task_card_api_error(Exc(), attempt=1, max_attempts=3, terminal=False)
    api_rows = [r for r in client.last_rows() if r.get("kind") == "api_error"]
    assert len(api_rows) == 1
    assert "started_at" not in api_rows[0]
    # The rendered API line carries no timestamp either.
    text = TelegramManager._format_task_card_text("", "", "", rows=client.last_rows())
    api_line = next(ln for ln in text.splitlines() if "API error" in ln)
    assert "UTC" not in api_line


def test_render_tool_row_without_started_at_is_safe():
    """Backward-compatible: a row missing started_at renders without a stamp
    (no crash), so an older in-flight payload still displays."""
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "", "reasoning": "x",
         "elapsed_s": 1, "done": False},
    ])
    assert "bash" in text
    assert "(1s)" in text
