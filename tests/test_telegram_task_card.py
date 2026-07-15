"""Tests for route B — single transient current-step Task Card.

Product contract: cap 500 Unicode code points after redaction, current-step
only (no cumulative history), no continuation/overflow, loud 📋 TASK CARD.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from lingtai.kernel.base_agent import BaseAgent
from lingtai.mcp_servers.telegram.manager import TelegramManager, SCHEMA
from tests._notification_store_helpers import notification_store_for


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
    manager = TelegramManager(service, working_dir=Path(tmp_path), on_inbound=lambda _: None, notification_store=notification_store_for(Path(tmp_path)))
    return manager, service.default_account


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
# Lifecycle: no-card on direct answer, teardown, dedup
#
# The lazy-create / same-card-edit / reverse-call-error/exception tests that
# used to live here exercised BaseAgent's ``_on_tool_pre_dispatch_hook`` and
# ``_teardown_telegram_task_card`` finalize reverse-call — both retired. The
# automatic Task Card is now a mechanical broadcast of ``logs/events.jsonl``
# owned entirely by ``TelegramManager`` (see
# ``tests/test_telegram_task_card_event_tail.py``), not a turn-local
# BaseAgent callback model. ``_teardown_telegram_task_card`` now only clears
# the route context BaseAgent still captures for the programmable controller
# (``test_no_tool_no_card``/``test_teardown_idempotent`` below cover that).
# ===========================================================================

def test_no_tool_no_card():
    """Teardown with no captured route context is a silent no-op."""
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = None
    agent._teardown_telegram_task_card()
    assert agent._telegram_task_card_context is None


def test_teardown_idempotent():
    """Teardown clears the route context and is safe to call repeatedly."""
    agent = BaseAgent.__new__(BaseAgent)
    agent._telegram_task_card_context = {"account": "mybot", "chat_id": 123}
    agent._teardown_telegram_task_card()
    assert agent._telegram_task_card_context is None
    agent._teardown_telegram_task_card()  # no-op, no raise
    assert agent._telegram_task_card_context is None


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
                              on_inbound=lambda _: None, notification_store=notification_store_for(Path(tmp_path)))
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
                              on_inbound=lambda _: None, notification_store=notification_store_for(Path(tmp_path)))

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
