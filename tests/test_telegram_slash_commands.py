"""Telegram slash-command local handling (PR: fix/telegram-slash-commands).

Covers the audited local-command contract: the built-in commands are handled
locally without reaching the agent; the /kanban layered overview + drill-down
(kb: callbacks) works; /clear writes the .clear forced-molt signal; unknown
commands still pass through to the agent.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from lingtai.mcp_servers.telegram.account import TelegramAccount


# ---------------------------------------------------------------------------
# Fixtures / helpers (mirrors test_telegram_lossless_envelope.py)
# ---------------------------------------------------------------------------

DATE = 1781600000
USER_A = {"id": 1, "is_bot": False, "first_name": "Alice", "username": "alice"}
PRIVATE_CHAT = {"id": 123, "type": "private", "username": "alice"}


def _account(state_dir: Path, allowed_users, seen: list) -> TelegramAccount:
    acct = TelegramAccount(
        alias="main",
        bot_token="123:ABC",
        allowed_users=allowed_users,
        state_dir=state_dir,
        on_message=lambda alias, upd: seen.append(upd),
    )
    acct._request = lambda *_a, **_kw: {}  # no network in tests
    return acct


def _msg(message_id: int = 100, *, text: str = "hello", **extra: Any) -> dict:
    m: dict[str, Any] = {
        "message_id": message_id,
        "chat": PRIVATE_CHAT,
        "date": DATE,
        "from": USER_A,
        "text": text,
    }
    m.update(extra)
    return m


def _callback(data: str, message_id: int = 9001) -> dict:
    return {
        "update_id": 999,
        "callback_query": {
            "id": "cq1",
            "from": USER_A,
            "data": data,
            "message": {"message_id": message_id, "chat": PRIVATE_CHAT, "date": DATE},
        },
    }


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    d.mkdir()
    (d / "system").mkdir()
    (d / ".agent.json").write_text(
        json.dumps({
            "name": "test-agent",
            "llm": {"model": "sonnet", "provider": "claude-code"},
            "molt_count": 3,
        }),
        encoding="utf-8",
    )
    (d / ".status.json").write_text(
        json.dumps({"state": "ACTIVE", "started_at": "2026-08-01T00:00:00Z"}),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def acct(agent_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TelegramAccount, list[dict]]:
    seen: list[dict] = []
    a = _account(agent_dir, [1], seen)
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(agent_dir))
    return a, seen


# ---------------------------------------------------------------------------
# All 9 commands stay local
# ---------------------------------------------------------------------------

def test_all_default_commands_stay_local(acct: tuple[TelegramAccount, list[dict]]) -> None:
    handled = acct[0]._handle_slash_command(
        {"update_id": 1, "message": _msg(text="/status")}
    )
    assert handled is True
    assert acct[1] == []

    handled = acct[0]._handle_slash_command(
        {"update_id": 2, "message": _msg(text="/help")}
    )
    assert handled is True
    assert acct[1] == []

    handled = acct[0]._handle_slash_command(
        {"update_id": 3, "message": _msg(text="/clear")}
    )
    assert handled is True
    assert acct[1] == []

    # /kanban stays local too
    handled = acct[0]._handle_slash_command(
        {"update_id": 5, "message": _msg(text="/kanban")}
    )
    assert handled is True
    assert acct[1] == []


def test_unknown_command_still_passes_through(acct: tuple[TelegramAccount, list[dict]]) -> None:
    handled = acct[0]._handle_slash_command(
        {"update_id": 6, "message": _msg(text="/unknowncmd")}
    )
    assert handled is False


# ---------------------------------------------------------------------------
# /clear writes the forced-molt signal
# ---------------------------------------------------------------------------

def test_clear_writes_signal_file(acct: tuple[TelegramAccount, list[dict]], agent_dir: Path) -> None:
    acct[0]._handle_slash_command({"update_id": 7, "message": _msg(text="/clear")})
    clear_file = agent_dir / ".clear"
    assert clear_file.exists()
    assert clear_file.read_text(encoding="utf-8") == "telegram\n"


def test_clear_missing_agent_dir_does_not_crash(tmp_path: Path) -> None:
    seen: list[dict] = []
    a = _account(tmp_path, [1], seen)
    a._request = lambda *_a, **_kw: {}
    os.environ.pop("LINGTAI_AGENT_DIR", None)
    # Should not raise; returns None.
    a._cmd_clear(123)


# ---------------------------------------------------------------------------
# /kanban layered drill-down via kb: callbacks
# ---------------------------------------------------------------------------

def test_kanban_callback_drill_down(acct: tuple[TelegramAccount, list[dict]]) -> None:
    handled = acct[0]._handle_slash_command(_callback("kb:1", message_id=9001))
    assert handled is True
    assert acct[1] == []


def test_kanban_callback_overview(acct: tuple[TelegramAccount, list[dict]]) -> None:
    handled = acct[0]._handle_slash_command(_callback("kb:overview", message_id=9001))
    assert handled is True
    assert acct[1] == []


def test_kanban_callback_all(acct: tuple[TelegramAccount, list[dict]]) -> None:
    handled = acct[0]._handle_slash_command(_callback("kb:all", message_id=9001))
    assert handled is True
    assert acct[1] == []


def test_kanban_all_text_produces_full_message(acct: tuple[TelegramAccount, list[dict]]) -> None:
    sent: list[dict] = []
    acct[0].send_message = (  # type: ignore[method-assign]
        lambda chat_id, text, **kw: sent.append({"chat_id": chat_id, "text": text})
    )
    acct[0]._cmd_kanban(123, text="/kanban all")
    assert len(sent) == 1
    body = sent[0]["text"]
    assert "Kanban" in body
    assert "Identity" in body
    assert "Addons" in body
    assert "/clear" in body


def test_kanban_overview_uses_keyboard_and_stats(acct: tuple[TelegramAccount, list[dict]]) -> None:
    sent: list[dict] = []
    acct[0].send_message = (  # type: ignore[method-assign]
        lambda chat_id, text, **kw: sent.append({"chat_id": chat_id, "text": text, **kw})
    )
    acct[0]._cmd_kanban(123)
    assert len(sent) == 1
    body = sent[0]["text"]
    # Overview should carry real stats, not just a menu
    assert "Kanban" in body
    assert "sonnet" in body
    assert "Tokens" in body
    assert "Context" in body
    assert "Tap a layer to drill in" in body
    markup = sent[0].get("reply_markup") or {}
    rows = markup.get("inline_keyboard", [])
    flat = [btn["text"] for row in rows for btn in row]
    assert any("Identity" in t for t in flat)
    assert any("All" in t for t in flat)


def test_kanban_layer_edits_in_place(acct: tuple[TelegramAccount, list[dict]]) -> None:
    edited: list[dict] = []
    acct[0].edit_message = (  # type: ignore[method-assign]
        lambda chat_id, message_id, text, **kw: edited.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, **kw}
        )
    )
    acct[0]._cmd_kanban_layer(123, 9001, "1")
    assert len(edited) == 1
    assert edited[0]["message_id"] == 9001
    assert "Identity" in edited[0]["text"]
    markup = edited[0].get("reply_markup") or {}
    rows = markup.get("inline_keyboard", [])
    flat = [btn["text"] for row in rows for btn in row]
    assert any("Back" in t for t in flat)


def test_kanban_layer_invalid_key_no_crash(acct: tuple[TelegramAccount, list[dict]]) -> None:
    # Invalid layer key falls back to overview behavior (no exception).
    acct[0]._cmd_kanban_layer(123, 9001, "9")


# ---------------------------------------------------------------------------
# /help lists all commands
# ---------------------------------------------------------------------------

def test_help_lists_all_default_commands(acct: tuple[TelegramAccount, list[dict]]) -> None:
    sent: list[str] = []
    acct[0].send_message = (  # type: ignore[method-assign]
        lambda chat_id, text, **kw: sent.append(text)
    )
    acct[0]._cmd_help(123)
    body = sent[0]
    for cmd in ["/help", "/kanban", "/taskcard", "/refresh", "/sleep", "/clear"]:
        assert cmd in body
    # Hidden commands still documented as hidden, still work if typed
    for cmd in ["/status", "/system"]:
        assert cmd in body
    assert "Hidden" in body


def test_retired_brief_command_passes_through(
    acct: tuple[TelegramAccount, list[dict]],
) -> None:
    assert acct[0]._handle_slash_command(
        {"update_id": 6, "message": _msg(text="/brief")}
    ) is False


def test_status_compact(acct: tuple[TelegramAccount, list[dict]]) -> None:
    sent: list[str] = []
    acct[0].send_message = (  # type: ignore[method-assign]
        lambda chat_id, text, **kw: sent.append(text)
    )
    acct[0]._cmd_status(123)
    assert len(sent) == 1
    assert "test-agent" in sent[0] or "sonnet" in sent[0]
