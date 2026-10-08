"""Telegram-owned provider for the generic read-only settings action."""
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.task_card.event_projection import TaskCardEventProjection
from lingtai.mcp_servers.telegram.service import (
    _TASKCARD_DEFAULT_ENABLED,
    _TASKCARD_DEFAULT_NORMAL_ROWS,
)
from lingtai.tools.tool_family import SettingRow, SettingsProvider

DEFAULT_AUTOMATIC_POLL_INTERVAL_SECONDS = 5.0


def telegram_setting_rows(manager: Any | None) -> Iterable[SettingRow]:
    """Return fresh five-field source rows for one live Telegram manager.

    Account authority remains private: the generic projector replaces both
    values on those rows before serialization. Any missing live fact raises so
    the generic action returns its one fixed no-partial-row failure.
    """
    service = getattr(manager, "_service", None)
    if service is None:
        raise RuntimeError("Telegram settings require a running service")
    config_source = getattr(service, "_config_source", None)
    if not isinstance(config_source, (str, Path)) or not str(config_source).strip():
        raise RuntimeError("Telegram config path is unavailable")

    aliases = service.list_accounts()
    if not isinstance(aliases, list) or not aliases or not all(
        isinstance(alias, str) and alias for alias in aliases
    ):
        raise RuntimeError("Telegram account settings are unavailable")
    accounts = {alias: service.get_account(alias) for alias in aliases}

    bot_tokens = {
        alias: deepcopy(account._bot_token) for alias, account in accounts.items()
    }
    raw_allowed_users = {
        alias: deepcopy(account._allowed_users) for alias, account in accounts.items()
    }
    allowed_users = {
        alias: sorted(value) if isinstance(value, set) else value
        for alias, value in raw_allowed_users.items()
    }
    poll_intervals = {
        alias: deepcopy(account._poll_interval) for alias, account in accounts.items()
    }
    commands = {
        alias: "built-in" if account._commands is None else deepcopy(account._commands)
        for alias, account in accounts.items()
    }

    manager_poll_interval = deepcopy(
        getattr(manager, "_TASK_CARD_EVENT_POLL_INTERVAL")
    )
    enabled = service.taskcard_enabled()
    normal_rows = service.taskcard_normal_rows()
    locale = service.taskcard_locale()
    display_expression = service.taskcard_display_expression()
    if type(enabled) is not bool:
        raise RuntimeError("Telegram Task Card delivery setting is unavailable")
    if type(normal_rows) is not int or not 1 <= normal_rows <= 10:
        raise RuntimeError("Telegram Task Card row setting is unavailable")
    if locale not in {"en", "zh"}:
        raise RuntimeError("Telegram Task Card locale setting is unavailable")

    default_expression = list(TaskCardEventProjection.DEFAULT_DISPLAY_EXPRESSION)
    if display_expression is None:
        current_expression = list(default_expression)
    else:
        expression_value = (
            list(display_expression)
            if isinstance(display_expression, tuple)
            else display_expression
        )
        current_expression_value = TaskCardEventProjection.validate_display_expression(
            expression_value
        )
        if current_expression_value is None:
            raise RuntimeError("Telegram Task Card display setting is unavailable")
        current_expression = list(current_expression_value)

    return (
        SettingRow(
            "config.path", str(config_source), None, True,
            "telegram-mcp-manual#telegram-config-path", _sensitive=True,
        ),
        SettingRow(
            "accounts.aliases", deepcopy(aliases), None, True,
            "telegram-mcp-manual#account-aliases", _sensitive=True,
        ),
        SettingRow(
            "accounts.bot_tokens", bot_tokens, None, True,
            "telegram-mcp-manual#bot-tokens", _sensitive=True,
        ),
        SettingRow(
            "accounts.allowed_users", allowed_users, None, True,
            "telegram-mcp-manual#allowed-users", _sensitive=True,
        ),
        SettingRow(
            "accounts.poll_intervals", poll_intervals, 1.0, True,
            "telegram-mcp-manual#account-poll-intervals", _sensitive=True,
        ),
        SettingRow(
            "accounts.commands", commands, "built-in", True,
            "telegram-mcp-manual#slash-command-menu", _sensitive=True,
        ),
        SettingRow(
            "automatic.poll_interval_seconds", manager_poll_interval,
            DEFAULT_AUTOMATIC_POLL_INTERVAL_SECONDS, True,
            "telegram-mcp-manual#task-card-poll-interval",
        ),
        SettingRow(
            "automatic.enabled", enabled, _TASKCARD_DEFAULT_ENABLED, True,
            "telegram-mcp-manual#task-card-delivery",
        ),
        SettingRow(
            "automatic.normal_rows", normal_rows, _TASKCARD_DEFAULT_NORMAL_ROWS, True,
            "telegram-mcp-manual#task-card-normal-rows",
        ),
        SettingRow(
            "automatic.locale", locale, "en", True,
            "telegram-mcp-manual#task-card-locale",
        ),
        SettingRow(
            "automatic.display_expression", current_expression,
            default_expression, True,
            "telegram-mcp-manual#task-card-display-expression",
        ),
    )


def build_telegram_settings_provider(manager: Any | None) -> SettingsProvider:
    """Bind Telegram's live manager lazily to the generic provider seam."""

    def provider() -> Iterable[SettingRow]:
        return telegram_setting_rows(manager)

    return provider
