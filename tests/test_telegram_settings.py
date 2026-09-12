"""Focused five-field SHOW coverage for the Telegram owner."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from lingtai.mcp_servers.task_card.event_projection import TaskCardEventProjection
from lingtai.mcp_servers.telegram._family import (
    TELEGRAM_ACTIONS,
    TELEGRAM_SCHEMA,
    handle_telegram,
)
from lingtai.mcp_servers.telegram.plugin import (
    TELEGRAM_DECLARED_ACTIONS,
    TELEGRAM_PLUGIN,
)
from lingtai.mcp_servers.telegram.service import TelegramService
from lingtai.mcp_servers.telegram.settings import (
    DEFAULT_AUTOMATIC_POLL_INTERVAL_SECONDS,
    telegram_setting_rows,
)

_SETTING_REFS = (
    ("config.path", "telegram-mcp-manual#telegram-config-path"),
    ("accounts.aliases", "telegram-mcp-manual#account-aliases"),
    ("accounts.bot_tokens", "telegram-mcp-manual#bot-tokens"),
    ("accounts.allowed_users", "telegram-mcp-manual#allowed-users"),
    ("accounts.poll_intervals", "telegram-mcp-manual#account-poll-intervals"),
    ("accounts.commands", "telegram-mcp-manual#slash-command-menu"),
    ("automatic.poll_interval_seconds", "telegram-mcp-manual#task-card-poll-interval"),
    ("automatic.enabled", "telegram-mcp-manual#task-card-delivery"),
    ("automatic.normal_rows", "telegram-mcp-manual#task-card-normal-rows"),
    ("automatic.locale", "telegram-mcp-manual#task-card-locale"),
    ("automatic.display_expression", "telegram-mcp-manual#task-card-display-expression"),
)
_KEYS = tuple(key for key, _comment in _SETTING_REFS)
_COMMENTS = dict(_SETTING_REFS)
_SENSITIVE_KEYS = _KEYS[:6]


class _Manager:
    def __init__(
        self,
        service: TelegramService,
        poll_interval: object = 2.5,
    ) -> None:
        self._service = service
        self._TASK_CARD_EVENT_POLL_INTERVAL = poll_interval
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


def _service(
    tmp_path: Path,
    *,
    account_poll_interval: object = 2.0,
) -> TelegramService:
    state_path = tmp_path / "telegram" / "taskcard.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "taskcard": False,
                "normal_rows": 4,
                "max_refreshes": 9,
                "locale": "zh",
                "display_expression": ["footer", "header"],
            }
        ),
        encoding="utf-8",
    )
    config_path = (tmp_path / "private" / "telegram.json").resolve()
    return TelegramService(
        tmp_path,
        [
            {
                "alias": "private-account",
                "bot_token": "fixture-token-not-a-credential",
                "allowed_users": [998877],
                "poll_interval": account_poll_interval,
                "commands": [{"command": "status", "description": "Status"}],
            }
        ],
        lambda *_args: None,
        config_source=str(config_path),
    )


def _call(manager: object, action: str, action_input: object) -> dict:
    return handle_telegram(
        manager,
        {
            "action": action,
            "input": action_input,
            "reasoning": "Telegram settings owner test",
        },
    )


def _rows(result: dict) -> dict[str, dict]:
    return {row["key"]: row for row in result["settings"]}


def test_family_opts_in_immediately_before_manual() -> None:
    assert TELEGRAM_PLUGIN.settings is True
    assert TELEGRAM_ACTIONS == (*TELEGRAM_DECLARED_ACTIONS, "settings", "manual")
    assert TELEGRAM_SCHEMA["properties"]["action"]["enum"] == list(
        TELEGRAM_ACTIONS
    )
    settings_branch, manual_branch = TELEGRAM_SCHEMA["properties"]["input"][
        "anyOf"
    ][-2:]
    assert (settings_branch["title"], manual_branch["title"]) == (
        "settings inventory input", "manual input"
    )
    assert all(
        branch["type"] == "object"
        and branch["properties"] == {}
        and branch["additionalProperties"] is False
        for branch in (settings_branch, manual_branch)
    )
    assert settings_branch["required"] == []


def test_show_returns_exact_keys_values_defaults_and_manual_pointers(
    tmp_path: Path,
) -> None:
    result = _call(_Manager(_service(tmp_path)), "settings", {})
    assert set(result) == {"settings"}
    rows = _rows(result)
    assert tuple(rows) == _KEYS
    assert all(
        tuple(row) == ("key", "current", "default", "configurable", "comment")
        for row in rows.values()
    )

    assert rows["automatic.poll_interval_seconds"] == {
        "key": "automatic.poll_interval_seconds",
        "current": 2.5,
        "default": DEFAULT_AUTOMATIC_POLL_INTERVAL_SECONDS,
        "configurable": True,
        "comment": _COMMENTS["automatic.poll_interval_seconds"],
    }
    assert rows["automatic.enabled"]["current"] is False
    assert rows["automatic.enabled"]["default"] is True
    assert rows["automatic.normal_rows"]["current"] == 4
    assert rows["automatic.normal_rows"]["default"] == 3
    assert rows["automatic.locale"]["current"] == "zh"
    assert rows["automatic.locale"]["default"] == "en"
    assert rows["automatic.display_expression"]["current"] == ["footer", "header"]
    assert rows["automatic.display_expression"]["default"] == list(
        TaskCardEventProjection.DEFAULT_DISPLAY_EXPRESSION
    )
    assert all(row["configurable"] is True for row in rows.values())
    assert {key: row["comment"] for key, row in rows.items()} == _COMMENTS

    manual = TELEGRAM_PLUGIN.skill_body.casefold()
    manual_anchors = {
        line.removeprefix("### ").strip().replace(" ", "-")
        for line in manual.splitlines()
        if line.startswith("### ")
    }
    for pointer in _COMMENTS.values():
        manual_name, anchor = pointer.split("#", 1)
        assert manual_name == TELEGRAM_PLUGIN.skill_name
        assert anchor in manual_anchors


def test_private_sources_are_exact_deep_copied_and_fully_redacted(
    tmp_path: Path,
) -> None:
    manager = _Manager(_service(tmp_path, account_poll_interval="unchanged-runtime-value"))
    source = {row.key: row for row in telegram_setting_rows(manager)}
    expected_path = str((tmp_path / "private" / "telegram.json").resolve())
    assert source["config.path"].current == expected_path
    assert source["accounts.aliases"].current == ["private-account"]
    assert source["accounts.bot_tokens"].current == {
        "private-account": "fixture-token-not-a-credential"
    }
    assert source["accounts.allowed_users"].current == {
        "private-account": [998877]
    }
    assert source["accounts.poll_intervals"].current == {
        "private-account": "unchanged-runtime-value"
    }
    assert source["accounts.poll_intervals"].default == 1.0
    assert source["accounts.commands"].current == {
        "private-account": [{"command": "status", "description": "Status"}]
    }
    assert source["accounts.commands"].default == "built-in"
    assert all(source[key].default is None for key in _SENSITIVE_KEYS[:4])
    assert all(source[key]._sensitive is True for key in _SENSITIVE_KEYS)

    source["accounts.commands"].current["private-account"][0]["command"] = "changed"
    account = manager._service.get_account("private-account")
    assert account._commands[0]["command"] == "status"

    public_rows = _rows(_call(manager, "settings", {}))
    for key in _SENSITIVE_KEYS:
        assert public_rows[key]["current"] == "<redacted>"
        assert public_rows[key]["default"] == "<redacted>"
    serialized = json.dumps(public_rows)
    assert all(
        private not in serialized
        for private in (
            "private-account",
            "fixture-token-not-a-credential",
            "998877",
            "unchanged-runtime-value",
            expected_path,
        )
    )


def test_show_reads_fresh_hot_task_card_values(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manager = _Manager(service)
    assert _rows(_call(manager, "settings", {}))["automatic.enabled"]["current"] is False

    service.set_taskcard_enabled(True)
    service.set_taskcard_normal_rows(7)
    service.set_taskcard_locale("en")
    service.set_taskcard_display_expression(["rows", "footer"])
    rows = _rows(_call(manager, "settings", {}))
    assert rows["automatic.enabled"]["current"] is True
    assert rows["automatic.normal_rows"]["current"] == 7
    assert rows["automatic.locale"]["current"] == "en"
    assert rows["automatic.display_expression"]["current"] == ["rows", "footer"]


def test_build_manager_captures_the_successfully_resolved_config_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lingtai.mcp_servers.telegram import server as telegram_server

    config_path = tmp_path / "private" / "telegram.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {"alias": "one", "bot_token": "fixture-not-a-credential"}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LINGTAI_TELEGRAM_CONFIG", "private/telegram.json")

    manager, working_dir = telegram_server.build_manager()

    assert working_dir == tmp_path
    assert manager._service._config_source == str(config_path)


@pytest.mark.parametrize("manager", [object(), None])
def test_unavailable_current_returns_the_fixed_no_partial_failure(manager: object) -> None:
    assert _call(manager, "settings", {}) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_nonfinite_public_value_fails_the_whole_inventory(tmp_path: Path) -> None:
    manager = _Manager(_service(tmp_path), poll_interval=float("nan"))
    assert math.isnan(manager._TASK_CARD_EVENT_POLL_INTERVAL)
    assert _call(manager, "settings", {}) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_settings_accepts_only_empty_input_and_never_mutates(tmp_path: Path) -> None:
    manager = _Manager(_service(tmp_path))
    state_path = tmp_path / "telegram" / "taskcard.json"
    before = state_path.read_bytes()
    result = _call(
        manager,
        "settings",
        {"set": "automatic.normal_rows", "value": 8},
    )
    assert result == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "invalid telegram input",
    }
    assert state_path.read_bytes() == before
    assert manager.calls == []


def test_ordinary_send_dispatch_remains_manager_owned(tmp_path: Path) -> None:
    manager = _Manager(_service(tmp_path))
    result = _call(
        manager,
        "send",
        {"chat_id": 42, "text": "unchanged Bot API operation"},
    )
    assert result == {"status": "ok", "action": "send"}
    assert manager.calls == [
        {"action": "send", "chat_id": 42, "text": "unchanged Bot API operation"}
    ]
