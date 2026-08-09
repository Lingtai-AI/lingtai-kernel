"""Manager-rendered timestamp placement on the Task Card.

Presentation contract (manager render, Jason 2026-08-09): per-tool-row inline
stamps are retired — each API-call group renders exactly ONE wall-clock stamp,
above its API metadata line (``api_ts``), and the card's final standalone line
reports the RENDER instant (not any row's start instant) as ``Last Updated:
HH:MM:SS UTC±HH``, always present. An injected ``now`` keeps these assertions
deterministic.

Retired: this file used to test per-row ``started_at`` inline rendering. That
contract is gone — rows the automatic Task Card renders now come from
``TelegramManager``'s own bounded projection of ``logs/events.jsonl`` (see
``tests/test_telegram_task_card_event_tail.py``); the manager's
``_format_task_card_text`` rendering behavior tested below accepts an optional
per-row ``started_at`` but no longer renders it inline (tool rows stay
compact), while the render-instant ``Last Updated`` line is always present.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from lingtai.mcp_servers.telegram.manager import TelegramManager, _TASK_CARD_FOOTER


# ---------------------------------------------------------------------------
# Rendering: tool rows carry no inline stamp; the render-time ``Last Updated``
# line is the only standalone timestamp the row renderer itself produces.
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 12, 17, 18, 36, tzinfo=timezone(timedelta(hours=-7)))


def _current_time_line(text):
    return next(ln for ln in text.splitlines() if ln.startswith("最后更新: "))


def test_tool_row_never_renders_its_started_at_inline():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
    ], now=_NOW)
    row_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    assert "04:08:08 UTC-07" not in row_line
    assert "UTC" not in row_line


def test_manager_renders_current_time_line_from_render_instant_not_row_start():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
    ], now=_NOW)
    lines = text.splitlines()
    # The bottom line is the labelled render-time stamp, not the row's own start.
    assert lines[-1] == "最后更新: 17:18:36 U-7"


def test_current_time_line_follows_the_footer():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
    ], now=_NOW)
    lines = text.splitlines()
    footer_idx = next(i for i, ln in enumerate(lines) if _TASK_CARD_FOOTER in ln)
    time_idx = next(i for i, ln in enumerate(lines) if ln.startswith("最后更新: "))
    assert time_idx > footer_idx
    assert lines[time_idx] == "最后更新: 17:18:36 U-7"


def test_parallel_rows_never_renders_any_per_row_stamp():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "a",
         "elapsed_s": 5, "done": False, "started_at": "04:08:07 UTC-07"},
        {"tool": "read", "tool_action": "", "reasoning": "b",
         "elapsed_s": 2, "done": False, "started_at": "04:08:09 UTC-07"},
        {"tool": "grep", "tool_action": "", "reasoning": "c",
         "elapsed_s": 1, "done": False, "started_at": "04:08:11 UTC-07"},
    ], now=_NOW)
    for ln in text.splitlines():
        if ln.startswith(("•", "✓")):
            assert "UTC" not in ln
    # The bottom line is still the single render-time stamp.
    assert _current_time_line(text) == "最后更新: 17:18:36 U-7"


def test_current_time_line_present_even_when_no_row_has_a_stamp():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 1, "done": False, "started_at": ""},
        {"tool": "read", "tool_action": "", "reasoning": "y",
         "elapsed_s": 2, "done": False},
    ], now=_NOW)
    # Last Updated never depends on any row carrying a stamp — it always
    # reflects the render instant.
    assert text.splitlines()[-1] == "最后更新: 17:18:36 U-7"
    # Tool rows never render an inline stamp even when one is supplied.
    for ln in text.splitlines():
        if ln.startswith(("•", "✓")):
            assert "UTC" not in ln


def test_api_error_row_never_carries_a_stamp_alongside_a_tool_row():
    """A mixed batch (tool row + API-error row): neither row carries an inline
    stamp; the render-time line is last."""
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
        {"kind": "api_error", "status": 429, "code": "usage_limit_reached",
         "state": "retrying", "attempt": 1, "max_attempts": 3, "done": False},
    ], now=_NOW)
    bash_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    assert "UTC" not in bash_line
    api_line = next(ln for ln in text.splitlines() if "API 错误" in ln)
    assert "UTC" not in api_line
    assert text.splitlines()[-1] == "最后更新: 17:18:36 U-7"


def test_render_tool_row_without_started_at_is_safe():
    """A row missing started_at (the event-tail projection omits it when the
    source event's ``ts`` was missing or malformed) renders without any
    inline stamp — no crash, no fabricated timestamp; the render-time line
    still renders unconditionally."""
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 1, "done": False},
    ], now=_NOW)
    assert "bash.run" in text
    assert text.splitlines()[-1] == "最后更新: 17:18:36 U-7"


def test_footer_shows_actual_current_normal_row_setting():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 1, "done": False, "started_at": "04:08:08 UTC-07"},
    ], normal_rows=7, now=_NOW)
    assert "/taskcard N 设置显示组数 (1-10，当前: 7)。" in text


def test_footer_current_row_count_stays_within_1_10_semantics():
    for n in (1, 10):
        text = TelegramManager._format_task_card_text("", "", "", rows=[
            {"tool": "bash", "tool_action": "run", "reasoning": "x",
             "elapsed_s": 1, "done": False, "started_at": "04:08:08 UTC-07"},
        ], normal_rows=n, now=_NOW)
        assert f"当前: {n}" in text
