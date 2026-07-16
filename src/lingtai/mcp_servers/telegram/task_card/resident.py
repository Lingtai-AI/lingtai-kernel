"""Telegram-owned resident Task Card state and serialized delivery."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class TaskCardResident:
    """Own channel frames, enablement, and one lock per account/chat route."""

    CHANNELS = ("automatic", "programmable")
    API_CALL_DIVIDER = "──────────"

    def __init__(
        self,
        *,
        enabled: bool,
        deliver: Callable[..., dict],
    ) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        self._enabled = enabled
        self._deliver = deliver
        self._frames: dict[str, dict[str, str]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self._state_lock = threading.Lock()

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
        with self._state_lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> bool:
        """Atomically set presentation state and return whether it changed."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        with self._state_lock:
            changed = self._enabled != enabled
            self._enabled = enabled
            return changed

    def set_frame(self, account: str, chat_id: int, channel: str, frame: str | None) -> None:
        """Store one channel's last frame; ``None`` clears that channel only."""
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
        """Compose the resident message from the two channel frames.

        ``channel``/``frame`` build a *proposed* payload for a not-yet-committed
        edit: that slot uses ``frame`` (``None`` clears it) instead of the
        stored frame, WITHOUT mutating ``_frames``. Callers commit the frame
        via ``set_frame`` only after the transport succeeds, so a failed edit
        never poisons the stored state.
        """
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
        watch = f"— WATCH —\n{programmable}"
        return watch if not automatic else f"{automatic}\n\n{watch}"

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
        if channel not in self.CHANNELS:
            return {"status": "error", "error": f"Unknown channel: {channel}"}
        with self.delivery_lock(account, chat_id):
            if not self.enabled():
                if channel == "programmable" and frame is None:
                    self.set_frame(account, chat_id, channel, None)
                return {"status": "ok", "suppressed": True, "taskcard": False}
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
        return self._frames

    def ensure(self, account: str, chat_id: int, frame: str, *, error: str) -> dict[str, Any]:
        return self.project(account, chat_id, "automatic", frame, error=error)
