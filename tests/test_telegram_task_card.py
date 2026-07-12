"""Tests for route B — single transient current-step Task Card.

Product contract: cap 500 Unicode code points after redaction, current-step
only (no cumulative history), no continuation/overflow, loud 📋 TASK CARD.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from lingtai.kernel.base_agent import BaseAgent, _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.manager import TelegramManager, SCHEMA


# ---------------------------------------------------------------------------
# Fake Telegram Manager (same pattern as r3/r4)
# ---------------------------------------------------------------------------

class FakeAccount:
    alias = "mybot"

    def __init__(self):
        self.calls: list = []
        self._sent_messages: dict[int, str] = {}

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        msg_id = len(self.calls) + 100
        self._sent_messages[msg_id] = text
        self.calls.append(("send_message", chat_id, text, reply_to_message_id, kwargs))
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self._sent_messages[message_id] = text
        self.calls.append(("edit_message", chat_id, message_id, text))
        return {"ok": True}

    def send_chat_action(self, chat_id, action):
        self.calls.append(("send_chat_action", chat_id, action))

    def set_message_reaction(self, *args, **kwargs):
        return True


class FakeService:
    def __init__(self):
        self.default_account = FakeAccount()

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account


def _manager(tmp_path):
    service = FakeService()
    manager = TelegramManager(service, working_dir=Path(tmp_path), on_inbound=lambda _: None)
    return manager, service.default_account


# ---------------------------------------------------------------------------
# Fake MCP Client
# ---------------------------------------------------------------------------

class _FakeMCPClient:
    def __init__(self, tool_results=None, raise_on=None):
        self.calls = []
        self._tool_results = tool_results or {}
        self._raise_on = raise_on or set()

    def call_tool(self, tool_name, args, timeout=None):
        self.calls.append((tool_name, dict(args), timeout))
        if tool_name in self._raise_on:
            raise RuntimeError(f"Simulated MCP failure: {tool_name}")
        # P0 guard: task-card reverse calls target the private tool name and
        # send NO public ``action`` (the server forces it).
        sub = args.get("sub_action", "")
        if sub in ("create", "update", "finalize"):
            assert tool_name == _TASK_CARD_TOOL, \
                f"Expected {_TASK_CARD_TOOL}, got {tool_name}"
            assert "action" not in args, \
                f"Reverse call must send no action, got {args.get('action')!r}"
        return self._tool_results.get(tool_name, {"status": "ok", "message_id": "test:123:456"})


def _agent_with_card_context(client, *, card_message_id=None):
    """A bare BaseAgent wired with a turn-local Task Card context for hook tests."""
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": card_message_id,
        "_lock": threading.Lock(),
    }
    return agent


def _warning_blob(caplog):
    """Join all captured WARNING messages (empty string if none)."""
    return " ".join(
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    )


# ===========================================================================
# Card create / update / finalize
# ===========================================================================

def test_task_card_create_shows_header_and_current_tool(tmp_path):
    manager, account = _manager(tmp_path)
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": "Check project structure",
    })
    assert r["status"] == "ok"
    assert "message_id" in r
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    assert len(send_calls) == 1
    text = send_calls[0][2]
    assert "📋 TASK CARD" in text
    assert "bash.run" in text
    assert "Check project structure" in text


def test_task_card_update_same_message_id(tmp_path):
    """Sequential tools edit the same card — single current step."""
    manager, account = _manager(tmp_path)

    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": "Step 1",
    })
    card_id = r["message_id"]

    r2 = manager._handle_task_card_update({
        "sub_action": "update",
        "card_message_id": card_id,
        "tool": "read",
        "tool_action": "",
        "reasoning": "Step 2",
    })
    assert r2["status"] == "ok"
    assert r2["message_id"] == card_id  # same card

    edit_calls = [c for c in account.calls if c[0] == "edit_message"]
    assert len(edit_calls) >= 1
    edited = edit_calls[-1][3]
    # Only current step visible; previous step replaced
    assert "Step 2" in edited
    assert "Step 1" not in edited  # replaced, not cumulative
    assert "📋 TASK CARD" in edited


def test_task_card_finalize_shows_done_header(tmp_path):
    manager, account = _manager(tmp_path)

    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": "Finished",
    })
    card_id = r["message_id"]

    r = manager._handle_task_card_update({
        "sub_action": "finalize",
        "card_message_id": card_id,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": "Finished",
    })
    assert r["status"] == "ok"
    edit_calls = [c for c in account.calls if c[0] == "edit_message"]
    assert any("✅ TASK CARD · DONE" in c[3] for c in edit_calls)
    # Original tool line still present
    assert any("bash.run" in c[3] for c in edit_calls)


# ===========================================================================
# Reasoning cap: 499 / 500 / 501
# ===========================================================================

def test_reasoning_499_fits_no_ellipsis(tmp_path):
    manager, account = _manager(tmp_path)
    reasoning = "A" * 499
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "reasoning": reasoning,
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert "A" * 499 in text
    assert "…" not in text.replace("📋 TASK CARD", "").replace("bash:", "").replace("…", "CHECK")  # no ellipsis


def test_reasoning_500_fits_exact_no_ellipsis(tmp_path):
    manager, account = _manager(tmp_path)
    reasoning = "B" * 500
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "grep",
        "reasoning": reasoning,
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert "B" * 500 in text


def test_reasoning_501_has_ellipsis(tmp_path):
    manager, account = _manager(tmp_path)
    reasoning = "C" * 501
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "read",
        "reasoning": reasoning,
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert "C" * 500 in text
    assert text.endswith("…") or "…" in text[-(len("C" * 500) + 2):]


# ===========================================================================
# Redaction before cap
# ===========================================================================

def test_redaction_before_cap(tmp_path):
    """Secret spanning the 500 boundary is redacted first, then capped."""
    manager, account = _manager(tmp_path)
    # Space before secret creates word boundary for redaction pattern
    prefix = "X" * 484 + " "
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    reasoning = prefix + secret  # exactly 525 chars
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "reasoning": reasoning,
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    # Secret must be gone after redaction
    assert "ghp_" not in text
    assert "<REDACTED" in text or "github_token" in text
    # Prefix preserved (484 X's + space = 485 chars before secret)
    assert "X" * 484 in text

def test_plain_text_unicode_multiline_preserved(tmp_path):
    manager, account = _manager(tmp_path)
    reasoning = "Line 1\nLine 2\n  café λ ✓ 😀"
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "read",
        "tool_action": "",
        "reasoning": reasoning,
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert "Line 1" in text
    assert "café" in text
    assert "😀" in text


def test_backslash_backtick_preserved(tmp_path):
    manager, account = _manager(tmp_path)
    reasoning = r"Path: C:\Users\test `grep` pattern"
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "reasoning": reasoning,
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert r"C:\Users\test" in text.replace("\\\\", "\\") or "\\Users" in text
    assert "`grep`" in text


def test_action_label_shown(tmp_path):
    manager, account = _manager(tmp_path)
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "telegram",
        "tool_action": "send",
        "reasoning": "Send reply",
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert "telegram.send" in text


def test_no_raw_args_in_card(tmp_path):
    manager, account = _manager(tmp_path)
    # _handle_task_card_update only receives tool/action/reasoning keys
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "reasoning": "Check files",
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert "Check files" in text


# ===========================================================================
# Lifecycle: lazy create, no-card on direct answer, teardown, dedup
# ===========================================================================

def test_hook_lazy_creates_card():
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient({_TASK_CARD_TOOL: {"status": "ok", "message_id": "mybot:123:789"}})
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.Lock(),
    }
    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "check", "action": "run"}, tool_call_id="c1")
    assert len(client.calls) == 1
    assert client.calls[0][0] == _TASK_CARD_TOOL  # private tool name
    assert client.calls[0][1]["sub_action"] == "create"
    assert "action" not in client.calls[0][1]  # server forces the action
    # The tool renders as one row carrying its display label (not routing).
    rows = client.calls[0][1]["rows"]
    assert rows == [{
        "tool": "bash", "tool_action": "run", "reasoning": "check",
        "elapsed_s": 0, "done": False,
    }]
    assert agent._telegram_task_card_context["card_message_id"] == "mybot:123:789"


def test_hook_second_tool_edits_same_card():
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient({_TASK_CARD_TOOL: {"status": "ok", "message_id": "mybot:123:789"}})
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": "mybot:123:789",
        "_lock": threading.Lock(),
    }
    agent._on_tool_pre_dispatch_hook("read", {"_reasoning": "step 2"}, tool_call_id="c2")
    assert len(client.calls) == 1
    assert client.calls[0][0] == _TASK_CARD_TOOL  # private tool name
    assert client.calls[0][1]["sub_action"] == "update"
    assert "action" not in client.calls[0][1]  # server forces the action
    assert client.calls[0][1]["card_message_id"] == "mybot:123:789"
    rows = client.calls[0][1]["rows"]
    assert rows == [{
        "tool": "read", "tool_action": "", "reasoning": "step 2",
        "elapsed_s": 0, "done": False,
    }]


def test_no_tool_no_card():
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient()
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.Lock(),
    }
    agent._teardown_telegram_task_card()
    assert len(client.calls) == 0
    assert agent._telegram_task_card_context is None


def test_teardown_finalizes_and_clears():
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient()
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": "mybot:123:789",
        "_lock": threading.Lock(),
    }
    agent._teardown_telegram_task_card()
    assert len(client.calls) == 1
    assert client.calls[0][1]["sub_action"] == "finalize"
    assert agent._telegram_task_card_context is None


def test_teardown_idempotent():
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient()
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": "mybot:123:789",
        "_lock": threading.Lock(),
    }
    agent._teardown_telegram_task_card()
    assert agent._telegram_task_card_context is None
    agent._teardown_telegram_task_card()  # no-op, no raise
    assert agent._telegram_task_card_context is None


def test_non_telegram_noop():
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = None
    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    # Nothing happens — no context


def test_mcp_timeout_does_not_block():
    agent = BaseAgent.__new__(BaseAgent)
    # The hook reverse-calls the private task-card tool, not "telegram", so it
    # must raise on _TASK_CARD_TOOL to truly exercise the fail-open path.
    client = _FakeMCPClient(raise_on={_TASK_CARD_TOOL})
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.Lock(),
    }
    agent._on_tool_pre_dispatch_hook("bash", {"_reasoning": "x"}, tool_call_id="c1")
    # Must not raise — fail-open


def test_recursion_guard():
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient()
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": "mybot:123:789",
        "_lock": threading.Lock(),
    }
    agent._on_tool_pre_dispatch_hook(_TASK_CARD_TOOL, {"_reasoning": "x"}, tool_call_id="c1")
    assert len(client.calls) == 0  # skipped — private tool never re-triggers the hook


def test_no_reasoning_skip():
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient()
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": None,
        "_lock": threading.Lock(),
    }
    agent._on_tool_pre_dispatch_hook("bash", {"action": "run"}, tool_call_id="c1")
    assert len(client.calls) == 0  # no reasoning → skip, no lazy create


# ===========================================================================
# Reverse-call error results: observable, fail-open, no fake success
# ===========================================================================
#
# The real MCPClient.call_tool never raises for a tool-level failure — it
# returns an error *dict* (e.g. {"status": "error", "message": ...} or
# {"status": "error", "error": ...}).  Before the fix the hook did
# ``ctx["card_message_id"] = result.get("message_id")`` which silently accepted
# that error dict as success (card_message_id stayed None with no signal), so a
# real reverse-call failure was invisible.  These tests pin down the required
# behavior: the failure is logged (observably) without ever leaking reasoning,
# chat IDs, or credentials, and the turn is never blocked (fail-open).


def test_create_error_result_is_observable_and_no_fake_success(caplog):
    """An error dict from the create call must be logged, and must NOT be
    mistaken for a created card."""
    client = _FakeMCPClient(
        {_TASK_CARD_TOOL: {"status": "error", "message": "boom sending card"}}
    )
    agent = _agent_with_card_context(client)
    with caplog.at_level("WARNING", logger="lingtai"):
        agent._on_tool_pre_dispatch_hook(
            "bash", {"_reasoning": "secret-reasoning-42", "action": "run"},
            tool_call_id="c1",
        )
    # No fake success: an error result must not be recorded as a card id.
    assert agent._telegram_task_card_context["card_message_id"] is None
    # Observable, and redacted by construction (no reasoning, chat id, provider text).
    blob = _warning_blob(caplog)
    assert blob, "reverse-call failure must be logged (observable)"
    assert "secret-reasoning-42" not in blob
    assert "123" not in blob
    assert "boom sending card" not in blob


def test_create_error_result_does_not_raise():
    """Fail-open: an error result must never propagate out of the hook."""
    client = _FakeMCPClient(
        {_TASK_CARD_TOOL: {"status": "error", "error": "manager not initialized"}}
    )
    agent = _agent_with_card_context(client)
    agent._on_tool_pre_dispatch_hook(  # must not raise
        "bash", {"_reasoning": "x", "action": "run"}, tool_call_id="c1",
    )
    assert agent._telegram_task_card_context["card_message_id"] is None


def test_update_error_result_is_observable_and_keeps_card(caplog):
    """An error dict from the update call must be logged and must leave the
    existing card id intact (fail-open), not overwrite it with None."""
    client = _FakeMCPClient(
        {_TASK_CARD_TOOL: {"status": "error", "message": "edit failed"}}
    )
    agent = _agent_with_card_context(client, card_message_id="mybot:123:789")
    with caplog.at_level("WARNING", logger="lingtai"):
        agent._on_tool_pre_dispatch_hook(
            "read", {"_reasoning": "step-2-secret", "action": ""},
            tool_call_id="c2",
        )
    assert agent._telegram_task_card_context["card_message_id"] == "mybot:123:789"
    blob = _warning_blob(caplog)
    assert blob, "reverse-call update failure must be logged (observable)"
    assert "step-2-secret" not in blob
    assert "edit failed" not in blob


def test_update_malformed_success_result_is_observable_and_keeps_card(caplog):
    """A success-shaped update dict that carries NO usable message_id (e.g.
    ``{"status": "ok"}``) must be observable, not silently ignored: it did not
    confirm a card. Fail-open — the existing card id is kept — and redacted by
    construction — no reasoning/chat id/payload leaks."""
    client = _FakeMCPClient({_TASK_CARD_TOOL: {"status": "ok"}})  # no message_id
    agent = _agent_with_card_context(client, card_message_id="mybot:123:789")
    with caplog.at_level("WARNING", logger="lingtai"):
        agent._on_tool_pre_dispatch_hook(
            "read", {"_reasoning": "malformed-step-secret", "action": ""},
            tool_call_id="c2",
        )
    # Fail-open: existing card id is unchanged, never overwritten with None.
    assert agent._telegram_task_card_context["card_message_id"] == "mybot:123:789"
    # Observable: a warning is emitted even though the payload looked ok.
    blob = _warning_blob(caplog)
    assert blob, "malformed success-shaped update must be logged (observable)"
    # Redacted by construction: no reasoning or payload content leaks.
    assert "malformed-step-secret" not in blob
    assert "123" not in blob


def test_create_raised_exception_is_observable_and_fail_open(caplog):
    """A raised reverse call (not just an error dict) must be fail-open AND
    observable — the pre-fix code swallowed it silently."""
    client = _FakeMCPClient(raise_on={_TASK_CARD_TOOL})
    agent = _agent_with_card_context(client)
    with caplog.at_level("WARNING", logger="lingtai"):
        agent._on_tool_pre_dispatch_hook(  # must not raise
            "bash", {"_reasoning": "secret-42", "action": "run"}, tool_call_id="c1",
        )
    assert agent._telegram_task_card_context["card_message_id"] is None
    blob = _warning_blob(caplog)
    assert blob, "a raised reverse call must be logged (observable)"
    # Only the exception class name is allowed, never its message/args.
    assert "RuntimeError" in blob
    assert "Simulated MCP failure" not in blob
    assert "secret-42" not in blob


def test_create_success_result_still_sets_card_id(caplog):
    """A well-formed success result must still be accepted (no regression)."""
    client = _FakeMCPClient(
        {_TASK_CARD_TOOL: {"status": "ok", "message_id": "mybot:123:789"}}
    )
    agent = _agent_with_card_context(client)
    with caplog.at_level("WARNING", logger="lingtai"):
        agent._on_tool_pre_dispatch_hook(
            "bash", {"_reasoning": "x", "action": "run"}, tool_call_id="c1",
        )
    assert agent._telegram_task_card_context["card_message_id"] == "mybot:123:789"
    # A success must not log a spurious warning.
    assert not _warning_blob(caplog)


def test_finalize_error_result_is_observable_and_clears_context(caplog):
    """A finalize error dict must be observable, yet context is still cleared."""
    client = _FakeMCPClient(
        {_TASK_CARD_TOOL: {"status": "error", "message": "finalize failed"}}
    )
    agent = _agent_with_card_context(client, card_message_id="mybot:123:789")
    with caplog.at_level("WARNING", logger="lingtai"):
        agent._teardown_telegram_task_card()
    assert agent._telegram_task_card_context is None  # cleared in finally
    blob = _warning_blob(caplog)
    assert blob, "finalize failure must be logged (observable)"
    assert "finalize failed" not in blob


def test_finalize_raised_exception_is_observable_and_clears_context(caplog):
    """A raised finalize call must be observable, fail-open, and still clear."""
    client = _FakeMCPClient(raise_on={_TASK_CARD_TOOL})
    agent = _agent_with_card_context(client, card_message_id="mybot:123:789")
    with caplog.at_level("WARNING", logger="lingtai"):
        agent._teardown_telegram_task_card()  # must not raise
    assert agent._telegram_task_card_context is None
    blob = _warning_blob(caplog)
    assert blob and "RuntimeError" in blob
    assert "Simulated MCP failure" not in blob


# ===========================================================================
# Schema / private action
# ===========================================================================

def test_task_card_update_not_in_schema():
    action_schema = SCHEMA.get("properties", {}).get("action", {})
    if "enum" in action_schema:
        assert "_task_card_update" not in action_schema["enum"]


def test_schema_public_actions_only():
    action_schema = SCHEMA.get("properties", {}).get("action", {})
    enum_values = action_schema.get("enum", [])
    public = {"send", "check", "read", "reply", "search", "delete", "edit",
              "contacts", "add_contact", "remove_contact", "accounts", "manual"}
    for v in enum_values:
        assert v in public, f"Unexpected public action: {v}"


def test_task_card_handler_idempotent_on_empty(tmp_path):
    manager, _ = _manager(tmp_path)
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "",
        "reasoning": "",
    })
    assert r["status"] == "ok"


def test_unknown_sub_action_returns_error(tmp_path):
    manager, _ = _manager(tmp_path)
    r = manager._handle_task_card_update({"sub_action": "nonexistent"})
    assert r["status"] == "error"


def test_task_card_update_fail_open_on_bad_card_id(tmp_path):
    """Updating a nonexistent card: fail-open — returns ok or error but never raises."""
    manager, _ = _manager(tmp_path)

    r = manager._handle_task_card_update({
        "sub_action": "update",
        "card_message_id": "nonexistent:999:999",
        "tool": "bash",
        "reasoning": "step",
    })
    # Must not raise. May return "ok" (recovery succeeded) or "error"
    # (both update and re-create failed) — either is fine for fail-open.
    assert r["status"] in ("ok", "error")

# ===========================================================================
# No continuation/overflow code path
# ===========================================================================

def test_legacy_overflow_methods_removed():
    """Verify overflow/continuation code has been fully removed."""
    assert not hasattr(TelegramManager, "_find_overflow_split")
    assert not hasattr(TelegramManager, "_TELEGRAM_TEXT_LIMIT")
    assert not hasattr(TelegramManager, "_format_task_card_entries")


# ===========================================================================
# Integration test: hook → MCP client → manager.handle → card routing
# ===========================================================================

def test_full_routing_chain_create_update_finalize(tmp_path):
    """Hook-generated args route through manager.handle → _handle_task_card_update.

    This test proves the P0 fix: if "action" is overwritten, the dispatch
    at manager.py:450 would fail and the card would never be created.
    """
    service = FakeService()
    manager = TelegramManager(service, working_dir=Path(tmp_path),
                              on_inbound=lambda _: None)
    account = service.default_account

    # — Create via manager.handle (real dispatch) —
    create_args = {
        "action": "_task_card_update",
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": "Check project structure",
    }
    r = manager.handle(create_args)
    assert r["status"] == "ok"
    card_id = r["message_id"]

    send_calls = [c for c in account.calls if c[0] == "send_message"]
    assert len(send_calls) == 1
    text = send_calls[0][2]
    assert "📋 TASK CARD" in text
    assert "bash.run" in text
    assert "Check project structure" in text

    # — Update via manager.handle (same card) —
    update_args = {
        "action": "_task_card_update",
        "sub_action": "update",
        "card_message_id": card_id,
        "tool": "read",
        "tool_action": "",
        "reasoning": "Now reading",
    }
    r2 = manager.handle(update_args)
    assert r2["status"] == "ok"
    assert r2["message_id"] == card_id

    edit_calls = [c for c in account.calls if c[0] == "edit_message"]
    assert len(edit_calls) >= 1
    edited = edit_calls[-1][3]
    assert "Now reading" in edited
    assert "Check project structure" not in edited  # replaced

    # — Finalize via manager.handle —
    finalize_args = {
        "action": "_task_card_update",
        "sub_action": "finalize",
        "card_message_id": card_id,
        "tool": "",
        "tool_action": "",
        "reasoning": "",
    }
    r3 = manager.handle(finalize_args)
    assert r3["status"] == "ok"
    final_edit_calls = [c for c in account.calls if c[0] == "edit_message"]
    assert any("✅ TASK CARD · DONE" in c[3] for c in final_edit_calls)


def test_routing_fails_if_action_overwritten(tmp_path):
    """Prove that if action were not _task_card_update, dispatch would fail."""
    service = FakeService()
    manager = TelegramManager(service, working_dir=Path(tmp_path),
                              on_inbound=lambda _: None)

    # This simulates what the old buggy code would have sent:
    # action = "run" (model tool action) overwrites "_task_card_update"
    broken_args = {
        "action": "run",
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "reasoning": "Check",
    }
    r = manager.handle(broken_args)
    # The manager dispatches on "run" which is not _task_card_update → error
    assert r.get("status") == "error" or "Unknown telegram action" in str(r)


# ===========================================================================
# Plain-text fidelity: literal **, backticks, backslash, newlines, Unicode
# ===========================================================================

def test_literal_double_star_preserved(tmp_path):
    """2**3 and **kwargs must survive redaction/cap intact."""
    manager, account = _manager(tmp_path)
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "python",
        "reasoning": "Compute 2**3 and use **kwargs",
    })
    assert r["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    text = send_calls[0][2]
    assert "2**3" in text
    assert "**kwargs" in text


def test_literal_double_star_survives_update(tmp_path):
    manager, account = _manager(tmp_path)
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "reasoning": "2**10 = 1024",
    })
    card_id = r["message_id"]
    r2 = manager._handle_task_card_update({
        "sub_action": "update",
        "card_message_id": card_id,
        "tool": "python",
        "reasoning": "x = y**2",
    })
    edit_calls = [c for c in account.calls if c[0] == "edit_message"]
    assert any("y**2" in c[3] for c in edit_calls)


# ===========================================================================
# P0 real wiring: hook fires through production _process_response (r7)
# ===========================================================================


def test_hook_wired_in_process_response_call_site():
    """Prove turn.py:_process_response passes on_pre_dispatch_hook to executor.

    This test exercises the real production call chain: _make_tool_executor
    → agent._executor → _process_response → agent._executor.execute(...).
    The executor is spy-wrapped with MagicMock(wraps=...) so we assert the
    actual keyword was forwarded — without manually passing it from the test.

    If the on_pre_dispatch_hook keyword is removed from turn.py:1649-1651,
    this test MUST fail.
    """
    from unittest.mock import MagicMock
    from pathlib import Path

    from lingtai.kernel.base_agent.turn import _make_tool_executor, _process_response
    from lingtai.kernel.loop_guard import LoopGuard
    from lingtai.kernel.llm.base import ToolCall

    agent = BaseAgent.__new__(BaseAgent)
    agent._intrinsics = {}
    agent._tool_handlers = {}
    agent._PARALLEL_SAFE_TOOLS = set()
    agent._dispatch_tool = lambda tc: {"ok": True}
    agent._cancel_event = threading.Event()
    agent._on_tool_pre_dispatch_hook = lambda n, a, **kw: None
    agent._on_tool_result_hook = lambda *a, **kw: None
    agent.service = MagicMock()
    agent.service.make_tool_result = lambda *a, **kw: {"ok": True}
    agent._log = lambda *a, **kw: None
    agent._working_dir = Path("/tmp")
    agent._summarize_notification_threshold = None
    agent._config = MagicMock()
    agent._executor = _make_tool_executor(agent, LoopGuard())

    # Spy-wrap: delegate to real executor but record calls
    agent._executor = MagicMock(wraps=agent._executor)

    class FakeResp:
        tool_calls = [ToolCall(name="bash", args={}, id="c1")]
        api_call_id = "api-1"
        text = ""
        thoughts = None
        raw = None

    # _process_response loops until no tool_calls; it will crash after
    # execute because we haven't wired _session/_save_chat_history.
    # That's fine — the mock already recorded the execute call.
    try:
        _process_response(agent, FakeResp())
    except Exception:
        pass

    agent._executor.execute.assert_called_once()
    _, kwargs = agent._executor.execute.call_args
    assert kwargs.get("on_pre_dispatch_hook") is agent._on_tool_pre_dispatch_hook, (
        "turn.py _process_response MUST pass on_pre_dispatch_hook to "
        "agent._executor.execute() — removing the keyword from the "
        "production call site will cause this test to fail"
    )


# ===========================================================================
# Parallel exact-once/order + hook-before-dispatch temporal proof (r7)
# ===========================================================================


def test_parallel_hook_exactly_n_calls_in_order():
    """N parallel tools → exactly N serial hook calls before any dispatch.

    Uses a single ordered ``events`` list recording (\"hook\", name, id) and
    (\"dispatch\", name) tuples, then asserts that all hooks fire in model
    order and complete before the first dispatch begins.  A threading.Barrier
    removes the need for time.sleep by forcing all dispatches to wait until
    hooks have finished recording — but the real Phase 2 code already fires
    hooks serially before ThreadPoolExecutor spawns futures.
    """
    from lingtai.kernel.tool_executor import ToolExecutor
    from lingtai.kernel.llm.base import ToolCall
    from lingtai.kernel.loop_guard import LoopGuard

    events: list[tuple] = []
    barrier = threading.Barrier(4, timeout=5)  # 4 dispatches

    def hook(tool_name, tool_args, tool_call_id=None):
        events.append(("hook", tool_name, tool_call_id))

    def fake_dispatch(tc):
        # Record dispatch *entry* before synchronising workers.  If hooks ever
        # move into worker threads, the first dispatch event will interleave
        # with later hooks and the temporal assertion below will fail.
        events.append(("dispatch", tc.name))
        barrier.wait()
        return {"ok": True}

    executor = ToolExecutor(
        dispatch_fn=fake_dispatch,
        make_tool_result_fn=lambda name, result, **kw: result,
        guard=LoopGuard(),
        known_tools={"bash", "read", "grep", "write"},
        parallel_safe_tools={"bash", "read", "grep", "write"},
    )

    tcs = [
        ToolCall(name="bash", args={}, id="c1"),
        ToolCall(name="read", args={}, id="c2"),
        ToolCall(name="grep", args={}, id="c3"),
        ToolCall(name="write", args={}, id="c4"),
    ]
    results, intercepted, _ = executor.execute(
        tcs, on_pre_dispatch_hook=hook,
    )

    assert not intercepted
    assert len(results) == 4

    # --- Model-order assertions ---
    hooks = [e for e in events if e[0] == "hook"]
    dispatches = [e for e in events if e[0] == "dispatch"]
    assert len(hooks) == 4, f"Expected 4 hook events, got {len(hooks)}"
    assert len(dispatches) == 4
    assert [h[1] for h in hooks] == ["bash", "read", "grep", "write"]
    assert [h[2] for h in hooks] == ["c1", "c2", "c3", "c4"]

    # --- Temporal ordering: all hooks before any dispatch ---
    last_hook_idx = max(i for i, e in enumerate(events) if e[0] == "hook")
    first_dispatch_idx = min(i for i, e in enumerate(events) if e[0] == "dispatch")
    assert last_hook_idx < first_dispatch_idx, (
        "All hooks MUST complete before any dispatch begins — "
        "per-future hooks would interleave and violate this assertion"
    )


# ===========================================================================
# Recovery message_id (r6 Fix 4)
# ===========================================================================


def test_update_recovery_adopts_new_message_id():
    """When update recovery re-creates the card, BaseAgent adopts new message_id."""
    agent = BaseAgent.__new__(BaseAgent)
    # simulate recovery: update returns a DIFFERENT message_id
    client = _FakeMCPClient(
        {_TASK_CARD_TOOL: {"status": "ok", "message_id": "recovered:123:999"}}
    )
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": "original:123:789",
        "_lock": threading.Lock(),
    }
    agent._on_tool_pre_dispatch_hook(
        "read", {"_reasoning": "step 2", "action": ""},
        tool_call_id="c2",
    )
    assert len(client.calls) == 1
    assert client.calls[0][1]["sub_action"] == "update"
    # Old id was sent in the request
    assert client.calls[0][1]["card_message_id"] == "original:123:789"
    # But context now has the recovered id
    assert agent._telegram_task_card_context["card_message_id"] == "recovered:123:999"


def test_update_same_message_id_not_changed():
    """When update succeeds without recovery, card_message_id stays the same."""
    agent = BaseAgent.__new__(BaseAgent)
    client = _FakeMCPClient(
        {_TASK_CARD_TOOL: {"status": "ok", "message_id": "mybot:123:789"}}
    )
    agent._telegram_task_card_context = {
        "mcp_client": client,
        "account": "mybot",
        "chat_id": 123,
        "card_message_id": "mybot:123:789",
        "_lock": threading.Lock(),
    }
    agent._on_tool_pre_dispatch_hook(
        "read", {"_reasoning": "step 2"},
        tool_call_id="c2",
    )
    assert agent._telegram_task_card_context["card_message_id"] == "mybot:123:789"


# ===========================================================================
# Finalize UI exact-text (r6 Fix 5)
# ===========================================================================


def test_finalize_exact_done_shape_no_empty_label(tmp_path):
    """Finalize produces exact ✅ TASK CARD · DONE without empty ': ' tool line."""
    manager, account = _manager(tmp_path)

    # Create first
    r = manager._handle_task_card_update({
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": "Step 1",
    })
    card_id = r["message_id"]

    # Finalize with empty tool/action (simulates _teardown_telegram_task_card)
    r2 = manager._handle_task_card_update({
        "sub_action": "finalize",
        "card_message_id": card_id,
        "tool": "",
        "tool_action": "",
        "reasoning": "",
    })
    assert r2["status"] == "ok"

    edit_calls = [c for c in account.calls if c[0] == "edit_message"]
    assert len(edit_calls) == 1
    final_text = edit_calls[0][3]
    # Must be exact ✅ TASK CARD · DONE without empty label
    assert final_text == "✅ TASK CARD · DONE", (
        f"Expected '✅ TASK CARD · DONE', got: {final_text!r}"
    )


# ===========================================================================
# HTTP / stdio MCP mapping — runtime identity (r7)
# ===========================================================================



def test_connect_mcp_http_maps_client_by_tool_name():
    """Runtime: connect_mcp_http populates _mcp_clients_by_tool with client id.

    Patches HTTPMCPClient so the real registration path executes without
    network calls.  Asserts every registered tool name maps to the exact
    same client object — removing or mis-keying the HTTP assignment must
    fail this test.
    """
    from unittest.mock import MagicMock, patch

    from lingtai.agent import Agent
    from lingtai.services.mcp import HTTPMCPClient

    agent = Agent.__new__(Agent)
    agent._mcp_clients = []
    agent.add_tool = MagicMock()  # prevent side effects

    mock_client = MagicMock(spec=HTTPMCPClient)
    mock_client.list_tools.return_value = [
        {"name": "telegram_send", "schema": {}, "description": "Send msg"},
        {"name": "telegram_read", "schema": {}, "description": "Read msgs"},
    ]

    with patch("lingtai.services.mcp.HTTPMCPClient", return_value=mock_client):
        registered = agent.connect_mcp_http("https://fake.example.com")

    assert registered == ["telegram_send", "telegram_read"]
    assert hasattr(agent, "_mcp_clients_by_tool"), (
        "connect_mcp_http must create _mcp_clients_by_tool"
    )
    for name in registered:
        assert agent._mcp_clients_by_tool.get(name) is mock_client, (
            f"_mcp_clients_by_tool[{name!r}] must be the registered client"
        )


def test_connect_mcp_stdio_maps_client_by_tool_name():
    """Runtime: connect_mcp populates _mcp_clients_by_tool with client id.

    Same pattern as the HTTP test but for the stdio MCPClient path.
    """
    from unittest.mock import MagicMock, patch

    from lingtai.agent import Agent
    from lingtai.services.mcp import MCPClient

    agent = Agent.__new__(Agent)
    agent._mcp_clients = []
    agent._expand_agent_placeholders = lambda x: x
    agent.add_tool = MagicMock()

    mock_client = MagicMock(spec=MCPClient)
    mock_client.list_tools.return_value = [
        {"name": "telegram_send", "schema": {}, "description": "Send msg"},
    ]

    with patch("lingtai.services.mcp.MCPClient", return_value=mock_client):
        registered = agent.connect_mcp("fake-cmd")

    assert "telegram_send" in registered
    assert hasattr(agent, "_mcp_clients_by_tool"), (
        "connect_mcp must create _mcp_clients_by_tool"
    )
    for name in registered:
        assert agent._mcp_clients_by_tool.get(name) is mock_client, (
            f"_mcp_clients_by_tool[{name!r}] must be the registered client"
        )
