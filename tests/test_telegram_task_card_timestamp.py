"""Manager-rendered per-row/render-instant timestamps on the Task Card.

Presentation contract (manager render, Jason's row-timestamp/current-time/footer
request): every normal automatic row carries its OWN inline stamp (from that
row's own ``started_at`` field, not the first row's), and the card's final
standalone line reports the RENDER instant (not any row's start instant) as
``Current Time: HH:MM:SS UTC±HH``, always present. An injected ``now`` keeps
these assertions deterministic.

Retired: this file used to also test BaseAgent's per-row local-timestamp
*capture* (``BaseAgent._format_task_card_timestamp``, ``_on_tool_pre_dispatch_hook``
stamping ``started_at`` once per row, immutability across heartbeats/finalize).
That capture mechanism no longer exists — rows the automatic Task Card renders
now come from ``TelegramManager``'s own bounded projection of
``logs/events.jsonl`` (see ``tests/test_telegram_task_card_event_tail.py``),
which does not track a per-row start instant at all (only ``tool``,
``tool_action``, ``reasoning``). The manager's ``_format_task_card_text``
rendering behavior tested below is unaffected — it still accepts an optional
``started_at`` per row (rendered inline when present, omitted safely when not)
and always renders the render-instant ``Current Time`` line.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from lingtai.mcp_servers.telegram.manager import TelegramManager, _TASK_CARD_FOOTER


# ---------------------------------------------------------------------------
# Rendering: every normal row carries its OWN inline stamp, and the card's
# final standalone line is the render-time ``Current Time: ...`` label.
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 12, 17, 18, 36, tzinfo=timezone(timedelta(hours=-7)))


def _current_time_line(text):
    return next(ln for ln in text.splitlines() if ln.startswith("Current Time: "))


def test_manager_renders_each_row_with_its_own_started_at():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
    ], now=_NOW)
    row_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    assert "04:08:08 UTC-07" in row_line


def test_manager_renders_current_time_line_from_render_instant_not_row_start():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
    ], now=_NOW)
    lines = text.splitlines()
    # The bottom line is the labelled render-time stamp, not the row's own start.
    assert lines[-1] == "Current Time: 17:18:36 UTC-07"
    assert "04:08:08 UTC-07" != "17:18:36 UTC-07"


def test_current_time_line_follows_the_footer():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
    ], now=_NOW)
    lines = text.splitlines()
    footer_idx = next(i for i, ln in enumerate(lines) if _TASK_CARD_FOOTER in ln)
    time_idx = next(i for i, ln in enumerate(lines) if ln.startswith("Current Time: "))
    assert time_idx > footer_idx
    assert lines[time_idx] == "Current Time: 17:18:36 UTC-07"


def test_parallel_rows_each_keep_their_own_started_at_not_the_first_rows():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "a",
         "elapsed_s": 5, "done": False, "started_at": "04:08:07 UTC-07"},
        {"tool": "read", "tool_action": "", "reasoning": "b",
         "elapsed_s": 2, "done": False, "started_at": "04:08:09 UTC-07"},
        {"tool": "grep", "tool_action": "", "reasoning": "c",
         "elapsed_s": 1, "done": False, "started_at": "04:08:11 UTC-07"},
    ], now=_NOW)
    bash_line = next(ln for ln in text.splitlines() if "bash" in ln and ln.startswith(("•", "✓")))
    read_line = next(ln for ln in text.splitlines() if ln.startswith(("•", "✓")) and "read" in ln)
    grep_line = next(ln for ln in text.splitlines() if ln.startswith(("•", "✓")) and "grep" in ln)
    # Each row shows its OWN stamp — none reused from another row.
    assert "04:08:07 UTC-07" in bash_line
    assert "04:08:09 UTC-07" in read_line
    assert "04:08:11 UTC-07" in grep_line
    # The bottom line is still the single render-time stamp, distinct from any row.
    assert _current_time_line(text) == "Current Time: 17:18:36 UTC-07"


def test_current_time_line_present_even_when_no_row_has_a_stamp():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 1, "done": False, "started_at": ""},
        {"tool": "read", "tool_action": "", "reasoning": "y",
         "elapsed_s": 2, "done": False},
    ], now=_NOW)
    # Current Time never depends on any row carrying a stamp — it always
    # reflects the render instant.
    assert text.splitlines()[-1] == "Current Time: 17:18:36 UTC-07"
    # Rows without a stamp render with no inline suffix (malformed/missing
    # timestamp tolerance), never crashing and never fabricating one.
    for ln in text.splitlines():
        if ln.startswith(("•", "✓")):
            assert "UTC" not in ln


def test_api_error_row_never_carries_a_stamp_alongside_a_stamped_tool_row():
    """A mixed batch (tool row + API-error row): the tool row shows its own
    stamp; the API-error row never carries one; the render-time line is last.
    """
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False, "started_at": "04:08:08 UTC-07"},
        {"kind": "api_error", "status": 429, "code": "usage_limit_reached",
         "state": "retrying", "attempt": 1, "max_attempts": 3, "done": False},
    ], now=_NOW)
    bash_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    assert "04:08:08 UTC-07" in bash_line
    api_line = next(ln for ln in text.splitlines() if "API error" in ln)
    assert "UTC" not in api_line
    assert text.splitlines()[-1] == "Current Time: 17:18:36 UTC-07"


def test_render_tool_row_without_started_at_is_safe():
    """Backward-compatible: a row missing started_at renders without any inline
    stamp (no crash) — the event-tail projection never sets this field, so this
    is now the common case, not an edge case; the render-time line still
    renders unconditionally."""
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "", "reasoning": "x",
         "elapsed_s": 1, "done": False},
    ], now=_NOW)
    assert "bash" in text
    assert "(1s)" in text
    row_line = next(ln for ln in text.splitlines() if ln.startswith(("•", "✓")))
    assert "UTC" not in row_line
    assert text.splitlines()[-1] == "Current Time: 17:18:36 UTC-07"


def test_footer_shows_actual_current_normal_row_setting():
    text = TelegramManager._format_task_card_text("", "", "", rows=[
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 1, "done": False, "started_at": "04:08:08 UTC-07"},
    ], normal_rows=7, now=_NOW)
    assert "/taskcard N sets normal rows (1-10, current: 7)." in text


def test_footer_current_row_count_stays_within_1_10_semantics():
    for n in (1, 10):
        text = TelegramManager._format_task_card_text("", "", "", rows=[
            {"tool": "bash", "tool_action": "run", "reasoning": "x",
             "elapsed_s": 1, "done": False, "started_at": "04:08:08 UTC-07"},
        ], normal_rows=n, now=_NOW)
        assert f"current: {n}" in text
