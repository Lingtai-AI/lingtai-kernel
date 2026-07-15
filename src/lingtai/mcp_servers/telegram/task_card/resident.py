"""Telegram-owned resident Task Card boundary.

This module owns only resident concerns shared by automatic and programmable
channels: channel frames, per-route serialization, enablement transitions, and
the callback-driven delivery boundary.  TelegramManager remains the transport
adapter for provider calls and durable account state.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class TaskCardResident:
    """One manager-owned resident target per ``(account, chat_id)``.

    The callbacks are intentionally narrow Telegram-addon adapters, not a
    generic plugin/schema mechanism.  The manager's existing fail-open
    edit/send/persist/replacement transaction is invoked only after this object
    serializes the route and after the enablement boundary is checked.
    """

    CHANNELS = ("automatic", "programmable")
    API_CALL_DIVIDER = "──────────"

    def __init__(
        self,
        *,
        enabled: Callable[[], bool],
        deliver: Callable[..., dict],
        on_enabled: Callable[[], None] | None = None,
    ) -> None:
        self._enabled = enabled
        self._deliver = deliver
        self._on_enabled = on_enabled
        self._frames: dict[str, dict[str, str]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self._enabled_state: bool | None = None
        self._enabled_override: bool | None = None
        self._transition_guard = False

    @property
    def frames(self) -> dict[str, dict[str, str]]:
        return self._frames

    @frames.setter
    def frames(self, value: dict[str, dict[str, str]]) -> None:
        self._frames = value if isinstance(value, dict) else {}

    @property
    def locks(self) -> dict[str, threading.RLock]:
        return self._locks

    @locks.setter
    def locks(self, value: dict[str, threading.RLock]) -> None:
        self._locks = value if isinstance(value, dict) else {}

    @staticmethod
    def key(account: str, chat_id: int) -> str:
        return f"{account}:{chat_id}"

    def enabled(self) -> bool:
        """Synchronize explicit presentation state and detect off -> on once."""
        current = (
            self._enabled_override
            if self._enabled_override is not None
            else bool(self._enabled())
        )
        previous = self._enabled_state
        self._enabled_state = current
        if (
            current
            and previous is False
            and self._on_enabled is not None
            and not self._transition_guard
        ):
            self._transition_guard = True
            try:
                self._on_enabled()
            finally:
                self._transition_guard = False
        return current

    def set_enabled(self, enabled: bool) -> None:
        """Set the resident presentation state without transport."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        self._enabled_override = enabled
        self._enabled_state = enabled

    def set_frame(self, account: str, chat_id: int, channel: str, frame: str | None) -> None:
        """Commit one channel frame after the adapter accepts delivery."""
        if channel not in self.CHANNELS:
            raise ValueError(f"unknown Task Card channel: {channel}")
        key = self.key(account, chat_id)
        slots = self._frames.setdefault(key, {})
        if frame is None:
            slots.pop(channel, None)
            if not slots:
                self._frames.pop(key, None)
        else:
            slots[channel] = frame

    def compose(
        self,
        account: str,
        chat_id: int,
        *,
        channel: str | None = None,
        frame: str | None = None,
    ) -> str:
        """Compose a proposed channel update without mutating committed state."""
        slots = dict(self._frames.get(self.key(account, chat_id), {}))
        if channel is not None:
            if channel not in self.CHANNELS:
                raise ValueError(f"unknown Task Card channel: {channel}")
            if frame is None:
                slots.pop(channel, None)
            else:
                slots[channel] = frame
        automatic = slots.get("automatic", "")
        programmable = slots.get("programmable", "")
        if not programmable:
            return automatic
        programmable_block = f"— WATCH —\n{programmable}"
        if not automatic:
            return programmable_block
        return f"{automatic}\n\n{programmable_block}"

    def delivery_lock(self, account: str, chat_id: int) -> threading.RLock:
        key = self.key(account, chat_id)
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def project(
        self,
        account: str,
        chat_id: int,
        channel: str,
        frame: str | None,
        *,
        error: str,
        resident_id: str | None = None,
        empty_fallback: str | None = None,
    ) -> dict[str, Any]:
        """Serialize one channel projection through the Telegram adapter."""
        if channel not in self.CHANNELS:
            return {"status": "error", "error": f"Unknown channel: {channel}"}
        if not self.enabled():
            if channel == "programmable" and frame is None:
                # Hidden stop clears only the committed watch slot, so a later
                # explicit /taskcard on cannot resurrect a stopped watch.
                self.set_frame(account, chat_id, channel, None)
            return {"status": "ok", "suppressed": True, "taskcard": False}
        with self.delivery_lock(account, chat_id):
            return self._deliver(
                account,
                chat_id,
                channel,
                frame,
                error=error,
                resident_id=resident_id,
                empty_fallback=empty_fallback,
            )

    def rehydrate(self) -> dict[str, dict[str, str]]:
        """Return committed channel frames after manager reconstruction.

        Resident message ids remain in TelegramAccount's existing durable map;
        no new route index or schema is introduced.  The manager event tail
        rebuilds its bounded projection and calls :meth:`project` against those
        ids.
        """
        return self._frames

    def ensure(self, account: str, chat_id: int, frame: str, *, error: str) -> dict[str, Any]:
        """Ensure/update the automatic resident frame via the same transaction."""
        return self.project(account, chat_id, "automatic", frame, error=error)
