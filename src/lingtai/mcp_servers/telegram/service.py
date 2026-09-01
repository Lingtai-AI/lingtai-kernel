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
from lingtai.mcp_servers.task_card.event_projection import TaskCardEventProjection

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
# Task Card projection language (locale). ``en`` is the canonical default;
# ``zh`` is the opt-in Chinese surface (set via ``/taskcard lang zh|en``).
_TASKCARD_DEFAULT_LOCALE = "en"
_TASKCARD_SUPPORTED_LOCALES = frozenset({"en", "zh"})


class TelegramServiceStopError(RuntimeError):
    """Aggregate every account stop failure after all accounts were attempted."""

    def __init__(self, failures: list[tuple[str, Exception]]) -> None:
        self.failures = tuple(failures)
        summary = ", ".join(f"{alias}:{type(exc).__name__}" for alias, exc in failures)
        super().__init__(f"Telegram account cleanup incomplete ({summary})")


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
            self._taskcard_locale,
            self._taskcard_display_expression,
        ) = self._load_taskcard_state()
        self._taskcard_mtime = self._current_taskcard_mtime()
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
                taskcard_locale=self.taskcard_locale,
                set_taskcard_locale=self.set_taskcard_locale,
                local_command_core=local_command_core,
            )
            self._accounts[alias] = acct
            self._account_order.append(alias)

    @staticmethod
    def _taskcard_defaults() -> tuple[bool, int, int, str, tuple[str, ...] | None]:
        return (
            True,
            _TASKCARD_DEFAULT_NORMAL_ROWS,
            _TASKCARD_DEFAULT_MAX_REFRESHES,
            _TASKCARD_DEFAULT_LOCALE,
            None,
        )

    def _parse_taskcard_fields(
        self, data: dict
    ) -> tuple[bool, int, int, str, tuple[str, ...] | None]:
        """Extract and validate every durable field; siblings never see a field's failure."""
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
        locale = data.get("locale")
        if locale not in _TASKCARD_SUPPORTED_LOCALES:
            if locale is not None:
                logger.warning("Invalid Telegram taskcard locale; using default")
            locale = _TASKCARD_DEFAULT_LOCALE
        raw_display_expression = data.get("display_expression")
        display_expression = TaskCardEventProjection.validate_display_expression(
            raw_display_expression
        )
        if raw_display_expression is not None and display_expression is None:
            logger.warning("Invalid Telegram taskcard display_expression; using default")
        return enabled, normal_rows, max_refreshes, locale, display_expression

    def _load_taskcard_state(self) -> tuple[bool, int, int, str, tuple[str, ...] | None]:
        """Load preferences; one invalid field never erases valid sibling fields."""
        if not self._taskcard_path.is_file():
            return self._taskcard_defaults()
        try:
            data = read_json(self._taskcard_path, expect=dict)
        except (OSError, ValueError, TypeError):
            logger.warning("Invalid or unreadable Telegram taskcard state; using defaults")
            return self._taskcard_defaults()
        return self._parse_taskcard_fields(data)

    def _current_taskcard_mtime(self) -> int | None:
        try:
            return self._taskcard_path.stat().st_mtime_ns
        except OSError:
            return None

    def _maybe_reload_taskcard_state(self) -> None:
        """Pick up a direct atomic external edit of ``taskcard.json`` in place.

        Callers hold ``self._taskcard_lock``. Bounded to one ``stat`` per call
        and a single re-parse only when the file's mtime actually changed
        since the last load — no retry loop, no background poll. A transient
        unreadable/corrupt read (the file is normally only ever replaced
        atomically, so this is defensive) preserves the last valid in-memory
        settings rather than reverting every field to its hardcoded default.
        """
        current_mtime = self._current_taskcard_mtime()
        if current_mtime == self._taskcard_mtime:
            return
        if current_mtime is None:
            self._taskcard_mtime = None
            return
        try:
            data = read_json(self._taskcard_path, expect=dict)
        except (OSError, ValueError, TypeError):
            logger.warning(
                "Invalid or unreadable Telegram taskcard state during reload; "
                "keeping last valid in-memory settings"
            )
            return
        (
            self._taskcard,
            self._taskcard_normal_rows,
            self._taskcard_max_refreshes,
            self._taskcard_locale,
            self._taskcard_display_expression,
        ) = self._parse_taskcard_fields(data)
        self._taskcard_mtime = current_mtime

    def _persist_taskcard_state(
        self,
        enabled: bool,
        normal_rows: int,
        max_refreshes: int,
        locale: str,
        display_expression: tuple[str, ...] | None,
    ) -> None:
        atomic_write_json(
            self._taskcard_path,
            {
                "taskcard": enabled,
                "normal_rows": normal_rows,
                "max_refreshes": max_refreshes,
                "locale": locale,
                "display_expression": (
                    list(display_expression) if display_expression is not None else None
                ),
            },
            fsync=True,
        )
        self._taskcard_mtime = self._current_taskcard_mtime()

    def taskcard_enabled(self) -> bool:
        """Return the current agent-wide Telegram Task Card delivery setting."""
        with self._taskcard_lock:
            self._maybe_reload_taskcard_state()
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
            # Reload first: an unseen direct external edit (siblings this
            # process has not read yet) must not be clobbered by writing back
            # this process's stale cached fields alongside the requested
            # change. ``changed`` is computed only after this, so it reflects
            # the freshly reloaded ``taskcard`` value, not a stale cache.
            self._maybe_reload_taskcard_state()
            changed = self._taskcard != enabled
            self._persist_taskcard_state(
                enabled,
                self._taskcard_normal_rows,
                self._taskcard_max_refreshes,
                self._taskcard_locale,
                self._taskcard_display_expression,
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
            self._maybe_reload_taskcard_state()
            return self._taskcard_normal_rows

    def set_taskcard_normal_rows(self, normal_rows: int) -> None:
        """Durably set the normal-row window, committing memory only after fsync."""
        if (
            type(normal_rows) is not int
            or not _TASKCARD_MIN_NORMAL_ROWS <= normal_rows <= _TASKCARD_MAX_NORMAL_ROWS
        ):
            raise ValueError("normal_rows must be an integer from 1 through 10")
        with self._taskcard_lock:
            # Reload first so an unseen direct external edit to the other
            # fields is not overwritten by this process's stale cache.
            self._maybe_reload_taskcard_state()
            self._persist_taskcard_state(
                self._taskcard,
                normal_rows,
                self._taskcard_max_refreshes,
                self._taskcard_locale,
                self._taskcard_display_expression,
            )
            self._taskcard_normal_rows = normal_rows

    def taskcard_max_refreshes(self) -> int:
        """Return the positive agent-wide Task Card refresh ceiling."""
        with self._taskcard_lock:
            self._maybe_reload_taskcard_state()
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
            # Reload first so an unseen direct external edit to the other
            # fields is not overwritten by this process's stale cache.
            self._maybe_reload_taskcard_state()
            self._persist_taskcard_state(
                self._taskcard,
                self._taskcard_normal_rows,
                max_refreshes,
                self._taskcard_locale,
                self._taskcard_display_expression,
            )
            self._taskcard_max_refreshes = max_refreshes

    def taskcard_locale(self) -> str:
        """Return the current agent-wide Task Card projection language."""
        with self._taskcard_lock:
            self._maybe_reload_taskcard_state()
            return self._taskcard_locale

    def set_taskcard_locale(self, locale: str) -> None:
        """Durably set the Task Card projection language (en|zh).

        Defaults to English. This is the configurable replacement for the
        hard-coded Chinese surface attempted in #1209 (Jason 2026-08-10): the
        shared projection stays English by default, and a Chinese-first agent
        opts in per agent via ``/taskcard lang zh``.
        """
        if locale not in _TASKCARD_SUPPORTED_LOCALES:
            raise ValueError(
                f"locale must be one of {sorted(_TASKCARD_SUPPORTED_LOCALES)}"
            )
        with self._taskcard_lock:
            # Reload first so an unseen direct external edit to the other
            # fields is not overwritten by this process's stale cache.
            self._maybe_reload_taskcard_state()
            self._persist_taskcard_state(
                self._taskcard,
                self._taskcard_normal_rows,
                self._taskcard_max_refreshes,
                locale,
                self._taskcard_display_expression,
            )
            self._taskcard_locale = locale

    def taskcard_display_expression(self) -> tuple[str, ...] | None:
        """Return the current agent-wide Task Card display expression.

        ``None`` means the caller must compose with
        ``TaskCardEventProjection.DEFAULT_DISPLAY_EXPRESSION``. This is the
        one durable, hot-swappable knob for *how* the projection's already
        rendered fragments are arranged; it never carries interpolated data.
        A direct atomic external edit of ``taskcard.json`` becomes visible on
        the next call, without a process restart.
        """
        with self._taskcard_lock:
            self._maybe_reload_taskcard_state()
            return self._taskcard_display_expression

    def set_taskcard_display_expression(
        self, display_expression: list[str] | None
    ) -> None:
        """Durably set the display expression; ``None`` restores the default.

        Validated against the same fixed slot allowlist the projection
        composes with (see ``TaskCardEventProjection.DISPLAY_SLOTS``); an
        unrecognized shape raises rather than persisting a silently degraded
        layout.
        """
        if display_expression is None:
            normalized: tuple[str, ...] | None = None
        else:
            normalized = TaskCardEventProjection.validate_display_expression(
                list(display_expression)
            )
            if normalized is None:
                raise ValueError(
                    "display_expression must be a non-empty list of at most "
                    f"{TaskCardEventProjection.MAX_DISPLAY_EXPRESSION_LENGTH} "
                    "slot names drawn only from "
                    f"{sorted(TaskCardEventProjection.DISPLAY_SLOTS)}"
                )
        with self._taskcard_lock:
            # Reload first so an unseen direct external edit to the other
            # fields is not overwritten by this process's stale cache.
            self._maybe_reload_taskcard_state()
            self._persist_taskcard_state(
                self._taskcard,
                self._taskcard_normal_rows,
                self._taskcard_max_refreshes,
                self._taskcard_locale,
                normalized,
            )
            self._taskcard_display_expression = normalized

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
        """Attempt every account stop, then report all uncertain cleanup."""
        failures: list[tuple[str, Exception]] = []
        for alias in self._account_order:
            try:
                self._accounts[alias].stop()
            except Exception as exc:
                failures.append((alias, exc))
        if failures:
            raise TelegramServiceStopError(failures) from failures[0][1]
