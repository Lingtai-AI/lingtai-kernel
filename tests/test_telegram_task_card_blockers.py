"""Regressions for two of the four independently-identified PR #871 blockers.

B1/B2 (retired) used to regression-test BaseAgent's turn-local heartbeat and
after-tool-continuation interaction with the API-error row it upserted into the
automatic Task Card's rolling window. That whole mechanism — the BaseAgent
row/heartbeat model, ``_report_task_card_api_error``/``_recover_task_card_api_error``,
and the reverse-call it drove — was retired: the automatic Task Card is now a
mechanical broadcast of ``logs/events.jsonl`` owned by ``TelegramManager`` (see
``mcp_servers/telegram/manager.py``'s event-tail worker), which whitelists only
``tool_call`` events and does not reconstruct completion, elapsed time, or
API-error state at all. B1/B2's premise no longer applies.

B3 — a final AED attempt with a viable preset fallback must not render the row
     as terminal ``error`` before the fallback runs. Exercised here through a
     test double (``_run_loop_spy``) that stubs ``_report_task_card_api_error``
     as a plain recording attribute — it asserts ``_run_loop``'s AED/fallback
     control flow, not the (now-removed) BaseAgent method body, so it is
     unaffected by the retirement above.
B4 — the ``_TASK_CARD_TEXT_LIMIT`` budget shrinks reasoning excerpts so a
     moderate-row card fits under the ceiling; it is NOT a guarantee for every
     possible row count.  Fixed per-row scaffolding is unbounded in row count, so
     an extreme row count can exceed the ceiling (and Telegram's transport
     limit) — and the renderer still keeps every requested row rather than
     dropping or truncating. Exercised directly against
     ``TelegramManager._format_task_card_text``, unaffected by the retirement.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

from lingtai.mcp_servers.telegram.manager import TelegramManager, _TASK_CARD_FOOTER


# ===========================================================================
# B3 — preset auto-fallback is not rendered as terminal failure before fallback
# ===========================================================================

import queue as _queue
from types import SimpleNamespace as _NS
from lingtai.kernel.base_agent import turn as _turnmod
from lingtai.kernel.message import _make_message, MSG_REQUEST
from lingtai.kernel.state import AgentState


class _SpyIface:
    def has_pending_tool_calls(self):
        return False

    def close_pending_tool_calls(self, *, reason, tool_completed=False):
        pass


def _run_loop_spy(tmp_path, *, max_aed_attempts=1, can_fallback=False,
                  activate_raises=False):
    agent = _NS()
    agent._working_dir = tmp_path
    agent.agent_name = "test"
    agent._state = AgentState.ACTIVE
    agent._chat = None
    agent._asleep = threading.Event()
    agent._shutdown = threading.Event()
    agent._cancel_event = threading.Event()
    agent._inbox_timeout = 0.01
    agent._reset_uptime = lambda: None
    agent._save_chat_history = lambda *a, **kw: None
    agent._logs = []
    agent._log = lambda ev, **kw: agent._logs.append((ev, kw))
    agent._set_state = lambda s, reason="": setattr(agent, "_state", s)
    # Break the loop once the terminal ASLEEP boundary is reached.
    agent._cancel_soul_timer = lambda: agent._shutdown.set()
    agent._config = _NS(insights_interval=0, max_aed_attempts=max_aed_attempts,
                        language="en", time_awareness=True, timezone_awareness=True)
    agent._session = _NS(chat=_NS(interface=_SpyIface()),
                         _rebuild_session=lambda interface: None)
    agent.inbox = _queue.Queue()
    agent.inbox.put(_make_message(MSG_REQUEST, "human", "go"))
    agent._preset_fallback_attempted = False
    agent._can_fallback_preset = lambda: can_fallback
    agent.refreshes = []
    agent._perform_refresh = lambda *a, **kw: agent.refreshes.append(True)

    def _activate():
        if activate_raises:
            raise RuntimeError("activate boom")
    agent._activate_default_preset = _activate

    # Capture API-error report calls (terminal flags in order).
    agent.reports = []
    agent._report_task_card_api_error = lambda exc, **kw: agent.reports.append(kw)
    agent._recover_task_card_api_error = lambda: None
    return agent


def _drive(agent, monkeypatch, exc):
    def fake_handle(_agent, _msg):
        raise exc
    monkeypatch.setattr(_turnmod, "_handle_message", fake_handle)
    monkeypatch.setattr(_turnmod.time, "sleep", lambda _s: None)
    import lingtai.tools.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda _a: None)
    _turnmod._run_loop(agent)


def test_final_attempt_with_successful_fallback_never_renders_terminal(tmp_path, monkeypatch):
    """A viable, successful preset fallback must NOT freeze the row as terminal
    error before the refresh."""
    agent = _run_loop_spy(tmp_path, max_aed_attempts=1, can_fallback=True)

    class Exc(Exception):
        status_code = 429
        code = "usage_limit_reached"

    _drive(agent, monkeypatch, Exc("x"))

    # Fallback ran and refresh was triggered.
    assert agent.refreshes == [True]
    # No report marked the row terminal — the card stays truthful pre-refresh.
    assert agent.reports, "the error should still be surfaced"
    assert all(r["terminal"] is False for r in agent.reports)


def test_preset_activation_failure_renders_terminal(tmp_path, monkeypatch):
    """If preset activation itself raises (no recovery left), the row is frozen
    terminal error before ASLEEP."""
    agent = _run_loop_spy(tmp_path, max_aed_attempts=1, can_fallback=True,
                          activate_raises=True)

    class Exc(Exception):
        status_code = 429
        code = "usage_limit_reached"

    _drive(agent, monkeypatch, Exc("x"))

    assert agent.refreshes == []  # no refresh — activation failed
    assert agent._asleep.is_set()
    # A terminal report was emitted after activation failed.
    assert any(r["terminal"] is True for r in agent.reports)


def test_no_fallback_exhaustion_renders_terminal(tmp_path, monkeypatch):
    """No fallback available → terminal error on exhaustion."""
    agent = _run_loop_spy(tmp_path, max_aed_attempts=1, can_fallback=False)

    class Exc(Exception):
        status_code = 429
        code = "usage_limit_reached"

    _drive(agent, monkeypatch, Exc("x"))

    assert agent._asleep.is_set()
    assert any(r["terminal"] is True for r in agent.reports)


# ===========================================================================
# B4 — the reasoning-excerpt budget keeps a MODERATE-row card under the ceiling
#      (it is not a guarantee for every N; see the high-N boundary test below)
# ===========================================================================

_MODERATE_NOW = datetime(2026, 7, 12, 17, 18, 36, tzinfo=timezone(timedelta(hours=-7)))


def test_timestamped_moderate_rows_stay_under_text_limit():
    """At a moderate row count the excerpt budget has headroom, so shrinking the
    (huge) per-row reasoning keeps the whole render under ``_TASK_CARD_TEXT_LIMIT``.
    This proves the excerpt-shrinkage guarantee, not an all-N bound."""
    rows = [
        {"tool": f"tool{i}", "tool_action": "run", "reasoning": "Z" * 600,
         "elapsed_s": i, "done": i % 2 == 0, "started_at": "04:08:08 UTC-07"}
        for i in range(12)
    ]
    text = TelegramManager._format_task_card_text("", "", "", rows=rows, now=_MODERATE_NOW)
    assert len(text) <= TelegramManager._TASK_CARD_TEXT_LIMIT
    # Every row still represented and the footer survives.
    for i in range(12):
        assert f"tool{i}" in text
    assert _TASK_CARD_FOOTER in text
    # Every row carries its own inline stamp now, counted in the excerpt budget
    # so this moderate card still fits.
    for ln in text.splitlines():
        if ln.startswith(("•", "✓")):
            assert "04:08:08 UTC-07" in ln
    # The bottom line is the single render-time stamp, distinct from row stamps.
    assert text.splitlines()[-1] == "Current Time: 17:18:36 UTC-07"


def test_extreme_row_count_exceeds_budget_but_keeps_every_row():
    """Truthful high-N boundary: fixed per-row scaffolding is unbounded in row
    count, so a very large operator-set N produces a render ABOVE the budget (and
    above Telegram's 4096 transport limit).  The renderer deliberately does NOT
    drop rows or truncate the final string to force a fit — the operator asked for
    N rows, so all N are shown.  A stable threshold (N far above the ~153/~181
    first-exceed points) is used so the assertion does not overfit an incidental
    exact character count.
    """
    n = 300
    rows = [
        {"tool": f"tool{i}", "tool_action": "run", "reasoning": "",
         "elapsed_s": 0, "done": False, "started_at": "04:08:08 UTC-07"}
        for i in range(n)
    ]
    text = TelegramManager._format_task_card_text("", "", "", rows=rows, now=_MODERATE_NOW)
    # Honest: the whole render is NOT bounded by the budget for extreme N.
    assert len(text) > TelegramManager._TASK_CARD_TEXT_LIMIT
    assert len(text) > 4096  # also above Telegram's hard transport limit
    # Yet every requested row is still present — none dropped to fit...
    line_count = sum(1 for ln in text.splitlines() if ln.startswith(("•", "✓")))
    assert line_count == n
    for i in (0, n // 2, n - 1):
        assert f"tool{i}" in text
    # ...and there is no blind final-string truncation ellipsis tail.
    assert not text.endswith("…")
    # The fixed footer and the single render-time line still render.
    assert _TASK_CARD_FOOTER in text
    assert text.splitlines()[-1] == "Current Time: 17:18:36 UTC-07"


def test_timestamped_rows_redaction_before_truncation():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    rows = [
        {"tool": f"t{i}", "tool_action": "", "reasoning": "X" * 400 + " " + secret,
         "elapsed_s": 1, "done": False, "started_at": "04:08:08 UTC-07"}
        for i in range(12)
    ]
    text = TelegramManager._format_task_card_text("", "", "", rows=rows)
    assert len(text) <= TelegramManager._TASK_CARD_TEXT_LIMIT
    assert "ghp_" not in text  # redacted even under heavy length pressure
    for i in range(12):
        assert f"t{i}" in text
