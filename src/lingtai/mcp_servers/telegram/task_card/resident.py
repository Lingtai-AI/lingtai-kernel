"""Telegram-owned resident Task Card state and serialized delivery."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class TaskCardResident:
    """Own channel frames, enablement, and one lock per account/chat route."""

    CHANNELS = ("automatic", "programmable")
    API_CALL_DIVIDER = "──────────"

    # Sentinel distinguishing "no automatic override proposed" from "propose
    # clearing the automatic slot" (``None`` is a legitimate override value).
    NO_AUTOMATIC_OVERRIDE = object()

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
        # Routes whose stored "automatic" frame was produced by the
        # tail-driven rows/metadata render, as opposed to the legacy scalar
        # single-tool form (which carries no footer/metadata to refresh).
        # Only a tail-driven frame is safe for a pre-edit refresh to
        # regenerate; refreshing a scalar-form frame would silently discard
        # its actual content. Owned here, alongside ``_frames``, so the
        # marker can never drift out of sync with the frame it describes.
        self._automatic_tail_driven: set[str] = set()

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

    def set_frame(
        self, account: str, chat_id: int, channel: str, frame: str | None,
        *, tail_driven: bool = False,
    ) -> None:
        """Store one channel's last frame; ``None`` clears that channel only.

        ``tail_driven`` marks (or, when clearing, unmarks) whether the stored
        "automatic" frame was produced by the tail-driven rows/metadata
        render — the only shape a pre-edit refresh may safely regenerate. It
        is ignored for the "programmable" channel.
        """
        if channel not in self.CHANNELS:
            raise ValueError(f"unknown Task Card channel: {channel}")
        key = self.key(account, chat_id)
        slots = self._frames.setdefault(key, {})
        if frame is None:
            slots.pop(channel, None)
            if not slots:
                self._frames.pop(key, None)
            if channel == "automatic":
                self._automatic_tail_driven.discard(key)
        else:
            slots[channel] = frame
            if channel == "automatic":
                if tail_driven:
                    self._automatic_tail_driven.add(key)
                else:
                    self._automatic_tail_driven.discard(key)

    def is_automatic_tail_driven(self, account: str, chat_id: int) -> bool:
        """Whether the committed "automatic" frame for this route was tail-driven."""
        return self.key(account, chat_id) in self._automatic_tail_driven

    def compose(
        self,
        account: str,
        chat_id: int,
        *,
        channel: str | None = None,
        frame: str | None = None,
        automatic_override: object = NO_AUTOMATIC_OVERRIDE,
    ) -> str:
        """Compose the resident message from the two channel frames.

        ``channel``/``frame`` build a *proposed* payload for a not-yet-committed
        edit: that slot uses ``frame`` (``None`` clears it) instead of the
        stored frame, WITHOUT mutating ``_frames``. Callers commit the frame
        via ``set_frame`` only after the transport succeeds, so a failed edit
        never poisons the stored state.

        ``automatic_override`` independently proposes replacement text for the
        "automatic" slot (``None`` proposes clearing it), for the one case
        where a programmable edit's pre-transport telemetry refresh needs to
        compose with a *not-yet-committed* fresher automatic frame while
        ``channel``/``frame`` simultaneously propose the programmable edit. It
        never mutates ``_frames`` either — the caller commits it (or not)
        exactly like the primary ``channel``/``frame`` override.
        """
        slots = dict(self._frames.get(self.key(account, chat_id), {}))
        if channel is not None:
            if channel not in self.CHANNELS:
                raise ValueError(f"unknown Task Card channel: {channel}")
            if frame is None:
                slots.pop(channel, None)
            else:
                slots[channel] = frame
        if automatic_override is not self.NO_AUTOMATIC_OVERRIDE:
            if automatic_override is None:
                slots.pop("automatic", None)
            else:
                slots["automatic"] = automatic_override
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
        tail_driven: bool = False,
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
                tail_driven=tail_driven,
            )

    def rehydrate(self) -> dict[str, dict[str, str]]:
        return self._frames

    def ensure(self, account: str, chat_id: int, frame: str, *, error: str) -> dict[str, Any]:
        return self.project(account, chat_id, "automatic", frame, error=error)
