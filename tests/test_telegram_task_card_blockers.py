"""Regressions for the four independently-identified PR #871 blockers.

B1 — heartbeat/teardown must not read ``started`` on a retrying API-error row
     (it has ``done=False``, ``kind='api_error'``, no ``started``) → no KeyError,
     no heartbeat-thread death.
B2 — an after-tool continuation provider error must reach the *live* Task Card
     context (before ``_handle_request``'s finally tears it down).
B3 — a final AED attempt with a viable preset fallback must not render the row
     as terminal ``error`` before the fallback runs.
B4 — the ``_TASK_CARD_TEXT_LIMIT`` budget shrinks reasoning excerpts so a
     moderate-row card fits under the ceiling; it is NOT a guarantee for every
     possible row count.  Fixed per-row scaffolding is unbounded in row count, so
     an extreme operator-set ``LINGTAI_TASK_CARD_MAX_TOOL_ROWS`` can exceed the
     ceiling (and Telegram's transport limit) — and the renderer still keeps
     every requested row rather than dropping or truncating.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

from lingtai.kernel.base_agent import BaseAgent, _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.manager import TelegramManager, _TASK_CARD_FOOTER


_FIXED_LOCAL_DT = datetime(2026, 7, 12, 4, 8, 8, tzinfo=timezone(timedelta(hours=-7)))


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


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

    def updates(self):
        return [c for c in self.calls if c[1].get("sub_action") == "update"]


def _agent(client, clock, *, heartbeat_enabled=False, sleep=None, stop_event=None):
    ctx = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.RLock(),
        "clock": clock,
        "wall_clock": lambda: _FIXED_LOCAL_DT,
        "rows": [],
        "generation": 0,
    }
    if heartbeat_enabled:
        ctx["heartbeat_enabled"] = True
        ctx["stop_event"] = stop_event or threading.Event()
        if sleep is not None:
            ctx["sleep"] = sleep
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = ctx
    return agent


class _ApiExc(Exception):
    status_code = 429
    code = "usage_limit_reached"


# ===========================================================================
# B1 — heartbeat/teardown do not crash on retrying API-error rows
# ===========================================================================

def test_heartbeat_tick_ignores_retrying_api_error_row():
    """A retrying API-error row (done=False, no 'started') must not crash the
    tick, and must never receive elapsed/start fields."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._report_task_card_api_error(_ApiExc(), attempt=1, max_attempts=3, terminal=False)
    clock.advance(1)
    # No active TOOL rows → tick no-ops (does not read r['started']).
    agent._task_card_heartbeat_tick()

    api = [r for r in agent._telegram_task_card_context["rows"] if r.get("kind") == "api_error"]
    assert len(api) == 1
    assert "elapsed_s" not in api[0]
    assert "started" not in api[0]


def test_heartbeat_tick_updates_tool_row_alongside_retrying_api_row():
    """An active tool row still ticks even when a retrying API row coexists."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    agent._report_task_card_api_error(_ApiExc(), attempt=1, max_attempts=3, terminal=False)
    clock.advance(2)
    agent._task_card_heartbeat_tick()  # must not raise

    rows = agent._telegram_task_card_context["rows"]
    tool = [r for r in rows if r.get("kind") != "api_error"][0]
    api = [r for r in rows if r.get("kind") == "api_error"][0]
    assert tool["elapsed_s"] == 2
    assert "elapsed_s" not in api and "started" not in api


def test_teardown_ignores_retrying_api_error_row():
    """Teardown's final freeze must skip the API row's missing 'started' rather
    than aborting finalization inside the fail-open wrapper."""
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)

    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    clock.advance(3)
    agent._on_tool_result_hook("bash", {}, {"ok": True}, tool_call_id="c1")
    agent._report_task_card_api_error(_ApiExc(), attempt=1, max_attempts=3, terminal=False)

    agent._teardown_telegram_task_card()  # must not raise; must finalize

    finals = [c for c in client.calls if c[1].get("sub_action") == "finalize"]
    assert len(finals) == 1  # finalization actually happened
    rows = finals[0][1]["rows"]
    tool = [r for r in rows if r.get("kind") != "api_error"][0]
    api = [r for r in rows if r.get("kind") == "api_error"][0]
    assert tool["done"] is True and tool["elapsed_s"] == 3
    # API row is untouched by the freeze loop (no elapsed/start added).
    assert "elapsed_s" not in api and "started" not in api
    assert agent._telegram_task_card_context is None


def test_api_only_teardown_does_not_crash():
    client = FakeMCPClient()
    clock = FakeClock()
    agent = _agent(client, clock)
    agent._report_task_card_api_error(_ApiExc(), attempt=2, max_attempts=3, terminal=False)
    agent._teardown_telegram_task_card()  # must not raise
    assert agent._telegram_task_card_context is None


def test_real_heartbeat_thread_survives_retrying_api_row():
    """A REAL heartbeat thread must not die (KeyError) when a retrying API row
    is present; it keeps ticking the active tool row."""
    client = FakeMCPClient()
    clock = FakeClock()
    tick_gate = threading.Event()
    stop_event = threading.Event()

    def fake_sleep(_interval):
        while not tick_gate.wait(timeout=0.05):
            if stop_event.is_set():
                return True
        tick_gate.clear()
        return stop_event.is_set()

    agent = _agent(client, clock, heartbeat_enabled=True, sleep=fake_sleep,
                   stop_event=stop_event)

    # Active tool row arms the real heartbeat thread; then a retrying API row.
    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    agent._report_task_card_api_error(_ApiExc(), attempt=1, max_attempts=3, terminal=False)

    clock.advance(1)
    updates_before = len(client.updates())
    tick_gate.set()  # allow one real tick
    import time as _t
    start = _t.monotonic()
    while len(client.updates()) <= updates_before and _t.monotonic() - start < 2.0:
        _t.sleep(0.01)

    thread = agent._telegram_task_card_context.get("timer_thread")
    # The thread survived the tick (no KeyError death) and produced an edit.
    assert len(client.updates()) > updates_before
    assert thread is not None and thread.is_alive()

    agent._teardown_telegram_task_card()
    assert agent._telegram_task_card_context is None
    thread.join(timeout=2.0)
    assert not thread.is_alive()


# ===========================================================================
# B2 — after-tool continuation error reaches the LIVE card before teardown
# ===========================================================================

def _make_continuation_failure_agent(client, exc):
    """A BaseAgent wired to drive the real _handle_request/_process_response/
    finally ordering: initial send returns a tool call, the post-tool
    continuation send raises ``exc``."""
    from unittest.mock import MagicMock
    from pathlib import Path
    from lingtai.kernel.base_agent.turn import _make_tool_executor
    from lingtai.kernel.loop_guard import LoopGuard
    from lingtai.kernel.llm.base import ToolCall

    class Resp:
        def __init__(self, tool_calls):
            self.tool_calls = tool_calls
            self.text = ""
            self.thoughts = None
            self.raw = None
            self.api_call_id = "api-1"
            self.usage = MagicMock()

    calls = {"n": 0}

    class Session:
        chat = None
        intermediate_text_streamed = False

        def send(self, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return Resp([ToolCall(name="bash", args={"_reasoning": "x"}, id="c1")])
            raise exc  # continuation send fails

    agent = BaseAgent.__new__(BaseAgent)
    agent._session = Session()  # first — several attrs are session-delegating properties
    agent._intrinsics = {}
    agent._tool_handlers = {}
    agent._PARALLEL_SAFE_TOOLS = set()
    agent._dispatch_tool = lambda tc: {"ok": True}
    agent._cancel_event = threading.Event()
    # Real card hooks (the code under test) + a live task-card context.
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.RLock(),
        "clock": FakeClock(),
        "wall_clock": lambda: _FIXED_LOCAL_DT,
        "rows": [],
        "generation": 0,
    }
    agent.service = MagicMock()
    agent.service.make_tool_result = lambda *a, **kw: {"ok": True}
    agent._log = lambda *a, **kw: None
    agent._log_notification_block_injected = lambda *a, **kw: None
    agent._working_dir = Path("/tmp")
    agent._summarize_notification_threshold = None
    agent._config = MagicMock()
    agent._drain_tc_inbox = lambda: None
    agent._pre_request = lambda m: "hi"
    agent._post_request = lambda m, r: None
    agent._save_chat_history = lambda *a, **kw: None
    agent._notification_live_holder = None
    agent._runtime_live_holder = None
    agent._sent_tracker = MagicMock()
    agent._executor = _make_tool_executor(agent, LoopGuard())
    return agent


def test_after_tool_continuation_error_reaches_live_card_before_teardown(monkeypatch):
    """The real _handle_request path: a tool batch then a continuation provider
    error must surface an API-error row to the SAME (still-live) card, before
    the finally-teardown nulls the context, while the exception still propagates
    unchanged and no raw text leaks.

    The notification-attach / external-send machinery is neutralised so the test
    isolates the send→except→finally ordering under test (not the wire subsystem);
    _process_response, its continuation send, and _handle_request's finally run
    for real."""
    from lingtai.kernel.base_agent import turn as _turn
    from lingtai.kernel.base_agent.turn import _handle_request

    monkeypatch.setattr(_turn, "attach_active_notifications", lambda *a, **kw: None)
    monkeypatch.setattr(_turn, "_check_external_send", lambda *a, **kw: None, raising=False)

    client = FakeMCPClient()

    class Exc(Exception):
        status_code = 429
        code = "usage_limit_reached"

    exc = Exc("boom https://api.example.com?token=sk-secret usage_limit_reached")
    agent = _make_continuation_failure_agent(client, exc)

    raised = None
    try:
        _handle_request(agent, MagicMockMsg())
    except Exception as e:  # AED would catch this in production
        raised = e

    # Original exception propagates unchanged for AED/retry/fallback.
    assert raised is exc

    # An API-error row was sent to the card...
    api_calls = [
        c for c in client.calls
        if any(r.get("kind") == "api_error" for r in c[1].get("rows", []))
    ]
    assert api_calls, "continuation error must reach the live card"

    # ...BEFORE the finalize (teardown) call.
    first_api_idx = client.calls.index(api_calls[0])
    finalize_idxs = [
        i for i, c in enumerate(client.calls) if c[1].get("sub_action") == "finalize"
    ]
    assert finalize_idxs, "teardown should finalize the card"
    assert first_api_idx < finalize_idxs[0], "API row must be reported before teardown"

    # Context is torn down after the turn.
    assert agent._telegram_task_card_context is None

    # No raw exception content leaks in any payload.
    blob = repr(client.calls)
    for leaked in ("https://", "token=", "sk-secret", "boom"):
        assert leaked not in blob


from unittest.mock import MagicMock


class MagicMockMsg:
    type = "request"
    content = "hi"


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

def test_timestamped_moderate_rows_stay_under_text_limit():
    """At a moderate row count the excerpt budget has headroom, so shrinking the
    (huge) per-row reasoning keeps the whole render under ``_TASK_CARD_TEXT_LIMIT``.
    This proves the excerpt-shrinkage guarantee, not an all-N bound."""
    rows = [
        {"tool": f"tool{i}", "tool_action": "run", "reasoning": "Z" * 600,
         "elapsed_s": i, "done": i % 2 == 0, "started_at": "04:08:08 UTC-07"}
        for i in range(12)
    ]
    text = TelegramManager._format_task_card_text("", "", "", rows=rows)
    assert len(text) <= TelegramManager._TASK_CARD_TEXT_LIMIT
    # Every row still represented and the footer survives.
    for i in range(12):
        assert f"tool{i}" in text
    assert _TASK_CARD_FOOTER in text
    # The card renders ONE card-level time line (never a per-row inline suffix),
    # counted in the excerpt budget so this moderate card fits.
    assert text.count("04:08:08 UTC-07") == 1
    assert text.splitlines()[-1] == "时间 04:08:08 UTC-07"
    for ln in text.splitlines():
        if ln.startswith(("•", "✓")):
            assert "UTC" not in ln  # no inline stamp on any row


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
    text = TelegramManager._format_task_card_text("", "", "", rows=rows)
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
    # The fixed footer and the single card-level time line still render.
    assert _TASK_CARD_FOOTER in text
    assert text.splitlines()[-1] == "时间 04:08:08 UTC-07"


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
