"""TelegramService — multi-account orchestrator.

Creates one TelegramAccount per config entry.
Routes outbound sends to the correct account by alias.
Delegates lifecycle (start/stop) to all accounts.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from lingtai.kernel._fsutil import atomic_write_json, read_json
from lingtai.mcp_servers.local_commands import LocalCommandCore

from .. import _identity
from .account import TelegramAccount

logger = logging.getLogger(__name__)

_TASKCARD_DEFAULT_NORMAL_ROWS = 1
_TASKCARD_MIN_NORMAL_ROWS = 1
_TASKCARD_MAX_NORMAL_ROWS = 10
# Both the default AND the global hard ceiling: no per-agent setting may
# exceed this value; only the settings owner can raise it (by changing this
# constant), never a single ``start`` call.
_TASKCARD_DEFAULT_MAX_REFRESHES = 1000
_TASKCARD_MIN_MAX_REFRESHES = 1
_TASKCARD_MAX_MAX_REFRESHES = 1000


class TelegramService:
    """Multi-account Telegram bot service."""

    def __init__(
        self,
        working_dir: Path,
        accounts_config: list[dict],
        on_message: Callable[[str, dict], None],
        config_source: str | None = None,
    ) -> None:
        self._working_dir = Path(working_dir)
        self._on_message = on_message
        self._config_source = config_source
        self._account_order: list[str] = []
        self._accounts: dict[str, TelegramAccount] = {}
        # Durable presentation preferences for the current agent. They are not
        # account-, chat-, session-, or project-scoped.
        self._taskcard_path = self._working_dir / "telegram" / "taskcard.json"
        self._taskcard_lock = threading.RLock()
        (
            self._taskcard,
            self._taskcard_normal_rows,
            self._taskcard_max_refreshes,
        ) = self._load_taskcard_state()
        self._taskcard_listener: Callable[[bool], None] | None = None
        local_command_core = LocalCommandCore(self._working_dir)

        for cfg in accounts_config:
            alias = cfg["alias"]
            state_dir = self._working_dir / "telegram" / alias
            acct = TelegramAccount(
                alias=alias,
                bot_token=cfg["bot_token"],
                allowed_users=cfg.get("allowed_users"),
                poll_interval=cfg.get("poll_interval", 1.0),
                on_message=on_message,
                state_dir=state_dir,
                commands=cfg.get("commands"),
                taskcard_enabled=self.taskcard_enabled,
                set_taskcard_enabled=self.set_taskcard_enabled,
                taskcard_normal_rows=self.taskcard_normal_rows,
                set_taskcard_normal_rows=self.set_taskcard_normal_rows,
                local_command_core=local_command_core,
            )
            self._accounts[alias] = acct
            self._account_order.append(alias)

    def _load_taskcard_state(self) -> tuple[bool, int, int]:
        """Load preferences; one invalid field never erases valid sibling fields."""
        if not self._taskcard_path.is_file():
            return True, _TASKCARD_DEFAULT_NORMAL_ROWS, _TASKCARD_DEFAULT_MAX_REFRESHES
        try:
            data = read_json(self._taskcard_path, expect=dict)
        except (OSError, ValueError, TypeError):
            logger.warning("Invalid or unreadable Telegram taskcard state; using defaults")
            return True, _TASKCARD_DEFAULT_NORMAL_ROWS, _TASKCARD_DEFAULT_MAX_REFRESHES
        enabled = data.get("taskcard")
        if type(enabled) is not bool:
            logger.warning("Invalid Telegram taskcard state field; defaulting enabled to True")
            enabled = True
        normal_rows = data.get("normal_rows", _TASKCARD_DEFAULT_NORMAL_ROWS)
        if (
            type(normal_rows) is not int
            or not _TASKCARD_MIN_NORMAL_ROWS <= normal_rows <= _TASKCARD_MAX_NORMAL_ROWS
        ):
            logger.warning("Invalid Telegram taskcard normal_rows; using default")
            normal_rows = _TASKCARD_DEFAULT_NORMAL_ROWS
        max_refreshes = data.get("max_refreshes")
        if (
            type(max_refreshes) is not int
            or not _TASKCARD_MIN_MAX_REFRESHES <= max_refreshes <= _TASKCARD_MAX_MAX_REFRESHES
        ):
            logger.warning("Invalid Telegram taskcard max_refreshes; using default")
            max_refreshes = _TASKCARD_DEFAULT_MAX_REFRESHES
        return enabled, normal_rows, max_refreshes

    def _persist_taskcard_state(self, enabled: bool, normal_rows: int, max_refreshes: int) -> None:
        atomic_write_json(
            self._taskcard_path,
            {"taskcard": enabled, "normal_rows": normal_rows, "max_refreshes": max_refreshes},
            fsync=True,
        )

    def taskcard_enabled(self) -> bool:
        """Return the current agent-wide Telegram Task Card delivery setting."""
        with self._taskcard_lock:
            return self._taskcard

    def set_taskcard_listener(self, listener: Callable[[bool], None]) -> None:
        """Install the manager callback for durable enablement transitions."""
        with self._taskcard_lock:
            self._taskcard_listener = listener

    def set_taskcard_enabled(self, enabled: bool) -> None:
        """Persist state, then notify the resident outside the state lock."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        with self._taskcard_lock:
            changed = self._taskcard != enabled
            self._persist_taskcard_state(
                enabled, self._taskcard_normal_rows, self._taskcard_max_refreshes
            )
            self._taskcard = enabled
            listener = self._taskcard_listener if changed else None
        if listener is not None:
            try:
                listener(enabled)
            except Exception as e:
                logger.warning("Task Card setting persisted but projection failed: %s", e)

    def taskcard_normal_rows(self) -> int:
        """Return the current agent-wide normal-row window."""
        with self._taskcard_lock:
            return self._taskcard_normal_rows

    def set_taskcard_normal_rows(self, normal_rows: int) -> None:
        """Durably set the normal-row window, committing memory only after fsync."""
        if (
            type(normal_rows) is not int
            or not _TASKCARD_MIN_NORMAL_ROWS <= normal_rows <= _TASKCARD_MAX_NORMAL_ROWS
        ):
            raise ValueError("normal_rows must be an integer from 1 through 10")
        with self._taskcard_lock:
            self._persist_taskcard_state(
                self._taskcard, normal_rows, self._taskcard_max_refreshes
            )
            self._taskcard_normal_rows = normal_rows

    def taskcard_max_refreshes(self) -> int:
        """Return the positive agent-wide Task Card refresh ceiling."""
        with self._taskcard_lock:
            return self._taskcard_max_refreshes

    def set_taskcard_max_refreshes(self, max_refreshes: int) -> None:
        """Persist a refresh ceiling atomically and fsynced.

        ``max_refreshes`` is both the default and the global hard ceiling: a
        single ``start`` call may only lower it via ``min(requested,
        configured)``, never raise it. Rejecting values above the ceiling here
        is what keeps that invariant enforceable.
        """
        if (
            type(max_refreshes) is not int
            or not _TASKCARD_MIN_MAX_REFRESHES <= max_refreshes <= _TASKCARD_MAX_MAX_REFRESHES
        ):
            raise ValueError(
                f"max_refreshes must be an integer from {_TASKCARD_MIN_MAX_REFRESHES} "
                f"through {_TASKCARD_MAX_MAX_REFRESHES}"
            )
        with self._taskcard_lock:
            self._persist_taskcard_state(
                self._taskcard, self._taskcard_normal_rows, max_refreshes
            )
            self._taskcard_max_refreshes = max_refreshes

    def get_account(self, alias: str) -> TelegramAccount:
        """Get account by alias. Raises KeyError if not found."""
        return self._accounts[alias]

    @property
    def default_account(self) -> TelegramAccount:
        """Return the first configured account."""
        return self._accounts[self._account_order[0]]

    def list_accounts(self) -> list[str]:
        """Return list of account aliases in config order."""
        return list(self._account_order)

    def account_details(self) -> list[dict[str, Any]]:
        """Return non-secret public identity details for each account."""
        details: list[dict[str, Any]] = []
        for alias in self._account_order:
            acct = self._accounts[alias]
            item = acct.public_identity()
            item["allowed_users_count"] = acct.allowed_users_count
            item["contact_count"] = self._contact_count(alias)
            if self._config_source:
                item["config_source"] = self._config_source
            details.append(item)
        return details

    def identity_payload(self) -> dict[str, Any]:
        """Build the non-secret MCP identity document for this service."""
        return _identity.identity_payload("telegram", self.account_details())

    def identity_path(self) -> Path:
        return _identity.identity_path(self._working_dir, "telegram")

    def write_identity_file(self) -> Path:
        """Atomically write public, non-secret MCP identity metadata."""
        return _identity.write_identity_file(
            self.identity_path(), self.identity_payload()
        )

    def _contact_count(self, alias: str) -> int | None:
        contacts_path = self._working_dir / "telegram" / alias / "contacts.json"
        if not contacts_path.is_file():
            return 0
        try:
            data = json.loads(contacts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return len(data) if isinstance(data, dict) else None

    def start(self) -> None:
        """Start all accounts' polling threads and publish public identity."""
        for acct in self._accounts.values():
            acct.start()
        try:
            path = self.write_identity_file()
            logger.info("Wrote Telegram MCP identity metadata to %s", path)
        except Exception as e:
            logger.warning(
                "Failed to write Telegram MCP identity metadata (continuing): %s", e
            )

    def stop(self) -> None:
        """Stop all accounts."""
        for acct in self._accounts.values():
            acct.stop()
