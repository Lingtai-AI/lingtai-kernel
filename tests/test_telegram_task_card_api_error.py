"""LLM/provider API errors surfaced into the automatic Telegram Task Card.

Jason: a provider ``429 usage_limit_reached`` (or any real API failure in a
Telegram-originated turn) must appear in the Task Card even though no tool call
produced it.  One stable API-error row per turn/retry sequence; repeated failures
update the same card; recovery marks it ``recovered``; a terminal failure freezes
it as ``error``.  Reporting is observe-only/fail-open and the rendered summary is
sanitized to bounded machine identifiers (type, public provider/model, valid
HTTP status, allow-listed code, retry state) — never opaque external identifiers or raw
exception text, body, URL, headers, tokens, prompts, traceback, or paths.
"""

from __future__ import annotations

import threading

from lingtai.kernel.base_agent import BaseAgent, _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.manager import TelegramManager, _TASK_CARD_FOOTER


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeMCPClient:
    def __init__(self, message_id="mybot:123:100", raise_on_update=False):
        self.calls: list = []
        self._message_id = message_id
        self._raise_on_update = raise_on_update

    def call_tool(self, tool_name, args, timeout=None):
        self.calls.append((tool_name, dict(args), timeout))
        assert tool_name == _TASK_CARD_TOOL
        assert "action" not in args
        if self._raise_on_update and args.get("sub_action") == "update":
            raise RuntimeError("edit boom")
        return {"status": "ok", "message_id": self._message_id}

    def creates(self):
        return [c for c in self.calls if c[1].get("sub_action") == "create"]

    def updates(self):
        return [c for c in self.calls if c[1].get("sub_action") == "update"]

    def last_rows(self):
        return self.calls[-1][1]["rows"]


def _agent(client=None):
    agent = BaseAgent.__new__(BaseAgent)
    if client is not None:
        agent._telegram_task_card_context = {
            "mcp_client": client,
            "account": "mybot",
            "chat_id": 123,
            "card_message_id": None,
            "_lock": threading.RLock(),
            "clock": lambda: 0.0,
            "rows": [],
            "generation": 0,
        }
    else:
        agent._telegram_task_card_context = None
    return agent


# Structured provider-style exceptions (mimic openai.RateLimitError shape).

class FakeRateLimitError(Exception):
    def __init__(self, message, status_code=429, code="usage_limit_reached"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class FakeServerError(Exception):
    def __init__(self, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code


def _rendered(client):
    """Render the last card payload through the real manager formatter."""
    return TelegramManager._format_task_card_text("", "", "", rows=client.last_rows())


# ---------------------------------------------------------------------------
# 429 usage_limit_reached before any tool creates one sanitized card
# ---------------------------------------------------------------------------

def test_429_before_tool_creates_one_sanitized_card_with_footer():
    client = FakeMCPClient()
    agent = _agent(client)
    exc = FakeRateLimitError(
        "Rate limited: https://api.example.com/v1/chat?key=sk-secret usage_limit_reached",
        status_code=429, code="usage_limit_reached")

    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)

    # Exactly one card created, no per-error spam.
    assert len(client.creates()) == 1
    text = _rendered(client)
    # Sanitized machine summary present.
    assert "429" in text
    assert "usage_limit_reached" in text
    assert "retrying" in text.lower()
    assert "1/3" in text
    # Footer preserved.
    assert _TASK_CARD_FOOTER in text


def test_raw_exception_text_never_leaks_into_render():
    client = FakeMCPClient()
    agent = _agent(client)
    exc = FakeRateLimitError(
        "boom https://api.example.com/v1?token=bearer-abc Authorization: Bearer sk-xyz "
        "/home/user/secret/path traceback line 42",
        status_code=429, code="usage_limit_reached")

    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)
    text = _rendered(client)

    for leaked in ("https://", "token=", "bearer", "Bearer", "sk-xyz",
                   "/home/user", "traceback", "Authorization"):
        assert leaked not in text, f"leaked: {leaked!r}"


def test_structured_diagnostics_project_from_exception_and_live_service():
    client = FakeMCPClient()
    agent = _agent(client)
    agent.service = type(
        "Service", (), {"provider": "codex-pool", "model": "gpt-5.6-sol"}
    )()
    exc = FakeServerError("raw response body with secret", status_code=504)
    secret_request_id = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    exc.request_id = secret_request_id
    exc.response = type("Response", (), {"headers": {"x-request-id": secret_request_id}})()

    agent._report_task_card_api_error(exc, attempt=2, max_attempts=3, terminal=False)

    [row] = client.last_rows()
    assert row["error_type"] == "FakeServerError"
    assert row["provider"] == "codex-pool"
    assert row["model"] == "gpt-5.6-sol"
    assert row["status"] == 504
    assert "request_id" not in row
    text = _rendered(client)
    assert "codex-pool/gpt-5.6-sol" in text
    assert "HTTP 504" in text
    assert secret_request_id not in text
    assert "raw response body" not in text


def test_unlisted_machine_code_is_dropped_not_shown():
    """A machine code not on the safe allow-list must not be rendered; the
    status code still shows."""
    client = FakeMCPClient()
    agent = _agent(client)
    exc = FakeRateLimitError("x", status_code=429, code="some_internal_untrusted_code")

    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)
    text = _rendered(client)

    assert "429" in text
    assert "some_internal_untrusted_code" not in text


def test_no_status_no_code_uses_safe_class_category():
    """An exception with no status/code degrades to a safe generic summary, never
    ``str(exc)``."""
    client = FakeMCPClient()
    agent = _agent(client)

    class WeirdError(Exception):
        pass

    agent._report_task_card_api_error(
        WeirdError("raw secret message /etc/passwd"), attempt=1, max_attempts=3,
        terminal=False)
    text = _rendered(client)

    assert "API error" in text
    assert "raw secret message" not in text
    assert "/etc/passwd" not in text


# ---------------------------------------------------------------------------
# Retry updates the same row/card; recovery marks recovered (no new card)
# ---------------------------------------------------------------------------

def test_repeated_errors_update_same_card_no_per_error_card():
    client = FakeMCPClient()
    agent = _agent(client)
    exc = FakeServerError("503 down", status_code=503)

    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)
    agent._report_task_card_api_error(exc, attempt=2, max_attempts=3, terminal=False)
    agent._report_task_card_api_error(exc, attempt=3, max_attempts=3, terminal=False)

    # One create, subsequent are edits of the same card.
    assert len(client.creates()) == 1
    assert len(client.updates()) >= 2
    # One stable API row (not three).
    rows = client.last_rows()
    api_rows = [r for r in rows if r.get("kind") == "api_error"]
    assert len(api_rows) == 1
    assert "3/3" in _rendered(client)


def test_recovery_marks_recovered_without_new_card():
    client = FakeMCPClient()
    agent = _agent(client)
    exc = FakeServerError("503", status_code=503)

    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)
    creates_after_error = len(client.creates())
    agent._recover_task_card_api_error()

    # No new card created by recovery.
    assert len(client.creates()) == creates_after_error
    text = _rendered(client)
    assert "recovered" in text.lower()
    # The fact that an error happened is preserved (still an API row).
    api_rows = [r for r in client.last_rows() if r.get("kind") == "api_error"]
    assert len(api_rows) == 1
    assert api_rows[0]["done"] is True


def test_recovery_without_prior_error_is_noop():
    client = FakeMCPClient()
    agent = _agent(client)
    agent._recover_task_card_api_error()
    assert client.calls == []


# ---------------------------------------------------------------------------
# Terminal unrecovered failure freezes as error (concrete last behavior)
# ---------------------------------------------------------------------------

def test_terminal_error_freezes_as_error():
    client = FakeMCPClient()
    agent = _agent(client)
    exc = FakeRateLimitError("x", status_code=429, code="usage_limit_reached")

    agent._report_task_card_api_error(exc, attempt=3, max_attempts=3, terminal=True)
    text = _rendered(client)

    api_rows = [r for r in client.last_rows() if r.get("kind") == "api_error"]
    assert len(api_rows) == 1
    assert api_rows[0]["done"] is True
    assert api_rows[0]["state"] == "error"
    # Terminal wording is a frozen error, not "retrying".
    assert "retrying" not in text.lower()
    assert "429" in text
    assert "usage_limit_reached" in text


# ---------------------------------------------------------------------------
# API error after an existing tool batch updates the same card (new last behavior)
# ---------------------------------------------------------------------------

def test_api_error_after_tool_batch_updates_same_card():
    client = FakeMCPClient()
    agent = _agent(client)

    # A tool batch already produced a card.
    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "build"}, tool_call_id="c1")
    assert len(client.creates()) == 1

    # An API error afterward updates the SAME card, adding the API row.
    exc = FakeRateLimitError("x", status_code=429, code="usage_limit_reached")
    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)

    assert len(client.creates()) == 1  # still one card
    text = _rendered(client)
    assert "429" in text
    assert "usage_limit_reached" in text
    # Tool row still present.
    assert "bash" in text


def test_new_tool_batch_replaces_lingering_api_error_row():
    """A later tool batch supersedes a prior API-error row (even a retrying one)
    as the concrete last behavior — no unbounded accretion."""
    client = FakeMCPClient()
    agent = _agent(client)

    exc = FakeRateLimitError("x", status_code=429, code="usage_limit_reached")
    # Terminal API error becomes the last behavior...
    agent._report_task_card_api_error(exc, attempt=1, max_attempts=1, terminal=True)
    # ...then the LLM recovers on a later turn and a fresh tool batch starts.
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "next"}, tool_call_id="c9")

    rows = client.last_rows()
    # The API-error row was replaced by the fresh batch (one tool row, no API row).
    assert not any(r.get("kind") == "api_error" for r in rows)
    assert [r.get("tool") for r in rows] == ["read"]


def test_retrying_api_error_then_new_tool_batch_still_replaces():
    client = FakeMCPClient()
    agent = _agent(client)

    exc = FakeServerError("503", status_code=503)
    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)
    # Even a not-yet-done (retrying) API row does not keep a new tool batch from
    # resetting — a tool batch means the LLM responded.
    agent._on_tool_pre_dispatch_hook("grep", {"_reasoning": "scan"}, tool_call_id="c1")

    rows = client.last_rows()
    assert not any(r.get("kind") == "api_error" for r in rows)
    assert [r.get("tool") for r in rows] == ["grep"]


# ---------------------------------------------------------------------------
# Fail-open + no-op guards
# ---------------------------------------------------------------------------

def test_card_update_failure_is_fail_open():
    client = FakeMCPClient(raise_on_update=True)
    agent = _agent(client)
    exc = FakeServerError("503", status_code=503)
    # First call creates (ok); a second reporting call updates (raises) — must
    # not propagate.
    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)
    agent._report_task_card_api_error(exc, attempt=2, max_attempts=3, terminal=False)


def test_non_telegram_context_is_noop():
    agent = _agent(client=None)  # no card context
    exc = FakeRateLimitError("x", status_code=429, code="usage_limit_reached")
    agent._report_task_card_api_error(exc, attempt=1, max_attempts=3, terminal=False)
    agent._recover_task_card_api_error()
    # No context, nothing created; simply must not raise.
    assert agent._telegram_task_card_context is None


# ===========================================================================
# Integration: the real AED loop drives report/recover on the orchestrating
# thread (uses the same run-loop harness as test_aed_recovery.py).
# ===========================================================================

import queue
import threading
from types import SimpleNamespace

from lingtai.kernel.base_agent import turn
from lingtai.kernel.message import _make_message, MSG_REQUEST
from lingtai.kernel.state import AgentState


class _SpyInterface:
    def has_pending_tool_calls(self):
        return False

    def close_pending_tool_calls(self, *, reason, tool_completed=False):
        pass


def _run_loop_spy_agent(tmp_path):
    """Minimal agent for driving turn._run_loop, with API-error spies."""
    agent = SimpleNamespace()
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
    agent._logs: list = []
    agent._log = lambda ev, **kw: agent._logs.append((ev, kw))
    agent._set_state = lambda s, reason="": setattr(agent, "_state", s)
    # Break the run loop once the turn settles (ASLEEP terminal path or the
    # test's fake_handle sets _shutdown on success): the real _cancel_soul_timer
    # fires each turn boundary, so use it as the loop-exit signal.
    agent._cancel_soul_timer = lambda: agent._shutdown.set()
    agent._config = SimpleNamespace(
        insights_interval=0, max_aed_attempts=3, language="en",
        time_awareness=True, timezone_awareness=True,
    )
    agent._session = SimpleNamespace(
        chat=SimpleNamespace(interface=_SpyInterface()),
        _rebuild_session=lambda interface: None,
    )
    agent.inbox = queue.Queue()
    agent.inbox.put(_make_message(MSG_REQUEST, "human", "go"))
    agent._preset_fallback_attempted = False
    agent._can_fallback_preset = lambda: False
    # Spies for the observe-only Task Card API-error reporting.
    agent.reports: list = []
    agent.recovers: list = []
    agent._report_task_card_api_error = lambda exc, **kw: agent.reports.append(
        (type(exc).__name__, kw))
    agent._recover_task_card_api_error = lambda: agent.recovers.append(True)
    return agent


def test_aed_transient_retry_then_success_reports_then_recovers(tmp_path, monkeypatch):
    agent = _run_loop_spy_agent(tmp_path)
    calls = {"n": 0}

    def fake_handle(_agent, _msg):
        calls["n"] += 1
        if calls["n"] <= 2:
            # 5xx → transient retry path.
            raise FakeServerError("peer closed connection", status_code=503)
        _agent._shutdown.set()  # 3rd attempt succeeds

    monkeypatch.setattr(turn, "_handle_message", fake_handle)
    monkeypatch.setattr(turn.time, "sleep", lambda _s: None)
    import lingtai.tools.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda _a: None)

    turn._run_loop(agent)

    # Two transient failures each reported as retrying; then recovery on success.
    assert len(agent.reports) == 2
    assert all(kw["terminal"] is False for _, kw in agent.reports)
    assert [kw["attempt"] for _, kw in agent.reports] == [1, 2]
    assert agent.recovers == [True]


def test_aed_terminal_failure_reports_terminal(tmp_path, monkeypatch):
    agent = _run_loop_spy_agent(tmp_path)
    agent._config.max_aed_attempts = 1  # exhaust immediately

    def fake_handle(_agent, _msg):
        # Non-transient (4xx) → deterministic AED, exhausts at attempt 1.
        raise FakeRateLimitError("x", status_code=429, code="usage_limit_reached")

    monkeypatch.setattr(turn, "_handle_message", fake_handle)
    monkeypatch.setattr(turn.time, "sleep", lambda _s: None)
    # After the terminal ASLEEP, break the run loop (mirrors test_aed_recovery).
    import lingtai.tools.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda _a: _a._shutdown.set())

    turn._run_loop(agent)

    # The terminal attempt is reported with terminal=True; no recovery fired.
    assert any(kw["terminal"] is True for _, kw in agent.reports)
    assert agent.recovers == []


def test_report_hook_absent_agent_does_not_break_aed(tmp_path, monkeypatch):
    """An agent without the reporting hook (getattr None) must not break the
    retry loop — reporting is strictly optional at the call site."""
    agent = _run_loop_spy_agent(tmp_path)
    del agent._report_task_card_api_error
    del agent._recover_task_card_api_error
    agent._config.max_aed_attempts = 1

    def fake_handle(_agent, _msg):
        raise FakeRateLimitError("x", status_code=429, code="usage_limit_reached")

    monkeypatch.setattr(turn, "_handle_message", fake_handle)
    monkeypatch.setattr(turn.time, "sleep", lambda _s: None)
    import lingtai.tools.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda _a: None)

    # Must complete without raising AttributeError; the terminal path still
    # reaches ASLEEP exactly as it would with the hooks present.
    turn._run_loop(agent)
    assert agent._asleep.is_set()


def test_api_error_renders_structured_diagnostics_without_raw_text():
    row = {
        "kind": "api_error",
        "error_type": "APITimeoutError",
        "provider": "codex-pool",
        "model": "gpt-5.6-sol",
        "status": 504,
        "code": "timeout",
        "state": "retrying",
        "attempt": 2,
        "max_attempts": 3,
        "done": False,
    }
    text = TelegramManager._format_api_error_line(row)
    assert text == (
        "⚠️ API error · APITimeoutError · codex-pool/gpt-5.6-sol · HTTP 504 · "
        "timeout · retrying 2/3"
    )
    assert "response body" not in text


def test_api_error_drops_malformed_machine_identifiers():
    text = TelegramManager._format_api_error_line({
        "kind": "api_error",
        "error_type": "Timeout Error: token=secret",
        "provider": "provider\nAuthorization: Bearer secret",
        "model": "model with spaces",
        "request_id": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "status": 503,
        "state": "error",
        "done": True,
    })
    assert text == "⚠️ API error · HTTP 503 · failed"
    assert "secret" not in text
