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

from .. import _identity
from .account import TelegramAccount

logger = logging.getLogger(__name__)

_TASKCARD_DEFAULT_NORMAL_ROWS = 1
_TASKCARD_MIN_NORMAL_ROWS = 1
_TASKCARD_MAX_NORMAL_ROWS = 10


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
        self._taskcard, self._taskcard_normal_rows = self._load_taskcard_state()

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
            )
            self._accounts[alias] = acct
            self._account_order.append(alias)

    def _load_taskcard_state(self) -> tuple[bool, int]:
        """Load agent-wide Task Card preferences, preserving legacy state files."""
        if not self._taskcard_path.is_file():
            return True, _TASKCARD_DEFAULT_NORMAL_ROWS
        try:
            data = read_json(self._taskcard_path, expect=dict)
            enabled = data.get("taskcard")
            if type(enabled) is not bool:
                raise TypeError("taskcard must be a boolean")
            normal_rows = data.get("normal_rows", _TASKCARD_DEFAULT_NORMAL_ROWS)
            if (
                type(normal_rows) is not int
                or not _TASKCARD_MIN_NORMAL_ROWS <= normal_rows <= _TASKCARD_MAX_NORMAL_ROWS
            ):
                logger.warning(
                    "Invalid Telegram taskcard normal_rows; defaulting to %d",
                    _TASKCARD_DEFAULT_NORMAL_ROWS,
                )
                normal_rows = _TASKCARD_DEFAULT_NORMAL_ROWS
            return enabled, normal_rows
        except (OSError, ValueError, TypeError):
            # Content-free warning: the state file may be malformed and must never
            # be echoed into logs. Preserve legacy behavior by failing open to on.
            logger.warning("Invalid or unreadable Telegram taskcard state; defaulting to True")
            return True, _TASKCARD_DEFAULT_NORMAL_ROWS

    def _persist_taskcard_state(self, enabled: bool, normal_rows: int) -> None:
        atomic_write_json(
            self._taskcard_path,
            {"taskcard": enabled, "normal_rows": normal_rows},
            fsync=True,
        )

    def taskcard_enabled(self) -> bool:
        """Return the current agent-wide Telegram Task Card delivery setting."""
        with self._taskcard_lock:
            return self._taskcard

    def set_taskcard_enabled(self, enabled: bool) -> None:
        """Durably set Task Card delivery, committing memory only after fsync."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        with self._taskcard_lock:
            self._persist_taskcard_state(enabled, self._taskcard_normal_rows)
            self._taskcard = enabled

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
            self._persist_taskcard_state(self._taskcard, normal_rows)
            self._taskcard_normal_rows = normal_rows

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
