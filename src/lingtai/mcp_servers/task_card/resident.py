"""Channel-neutral resident Task Card composition and delivery state machine."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskCardRoute:
    """Provider-neutral resident route, including an optional topic/thread."""

    account: str
    chat_id: str | int
    thread_id: str | int | None = None

    @property
    def key(self) -> str:
        base = f"{self.account}:{self.chat_id}"
        return base if self.thread_id is None else f"{base}:{self.thread_id}"


@dataclass(frozen=True)
class TaskCardResidentTransport:
    """Narrow provider adapter consumed by the resident delivery state machine."""

    get_resident: Callable[[TaskCardRoute], str | None]
    matches_route: Callable[[TaskCardRoute, str], bool]
    is_superseded: Callable[[TaskCardRoute, str], bool]
    edit: Callable[[str, str], tuple[str, str | None]]
    delete: Callable[[str], str]
    send: Callable[[TaskCardRoute, str], dict[str, Any] | None]
    persist: Callable[[TaskCardRoute, str], bool]


class TaskCardResident:
    """Own slots, route locks, and fail-closed resident delivery transitions."""

    CHANNELS = ("automatic", "programmable")
    API_CALL_DIVIDER = "──────────"

    EDIT_OK = "ok"
    EDIT_IMPOSSIBLE = "edit_impossible"
    EDIT_FAILED = "failed"
    # The provider accepted the logical projection, but its hard edit interval
    # deferred transport.  The provider adapter owns the pending-latest retry;
    # resident commits the channel slot so later compositions cannot lose it.
    EDIT_THROTTLED = "throttled"

    DELETE_OK = "ok"
    DELETE_MISSING = "missing"
    DELETE_NONDELETABLE = "nondeletable"
    DELETE_FAILED = "failed"

    SEND_OK = "sent"
    SEND_FAILED = "failed"
    SEND_INDETERMINATE = "indeterminate_send"

    def __init__(
        self,
        *,
        enabled: bool,
        transport: TaskCardResidentTransport | None = None,
        deliver: Callable[..., dict[str, Any]] | None = None,
        programmable_heading: str | Callable[[], str] = "— TASK CARD —",
    ) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        if transport is None and deliver is None:
            raise TypeError("transport or deliver must be provided")
        self._enabled = enabled
        self._transport = transport
        # Compatibility hook for the original Telegram-owned resident wrapper.
        self._legacy_deliver = deliver
        self._programmable_heading = programmable_heading
        # ``_frames`` is provider-confirmed truth. ``_desired_frames`` may be
        # newer while a provider adapter owns one accepted, throttled retry.
        self._frames: dict[str, dict[str, str]] = {}
        self._desired_frames: dict[str, dict[str, str]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self._state_lock = threading.Lock()

    @property
    def frames(self) -> dict[str, dict[str, str]]:
        return self._frames

    @frames.setter
    def frames(self, value: dict[str, dict[str, str]]) -> None:
        self._frames = value if isinstance(value, dict) else {}
        self._desired_frames = {
            key: dict(slots) for key, slots in self._frames.items()
        }

    @property
    def desired_frames(self) -> dict[str, dict[str, str]]:
        return self._desired_frames

    @property
    def locks(self) -> dict[str, threading.RLock]:
        return self._locks

    @locks.setter
    def locks(self, value: dict[str, threading.RLock]) -> None:
        self._locks = value if isinstance(value, dict) else {}

    @staticmethod
    def route(
        account: str,
        chat_id: str | int,
        thread_id: str | int | None = None,
    ) -> TaskCardRoute:
        return TaskCardRoute(account=account, chat_id=chat_id, thread_id=thread_id)

    @classmethod
    def key(
        cls,
        account: str,
        chat_id: str | int,
        thread_id: str | int | None = None,
    ) -> str:
        return cls.route(account, chat_id, thread_id).key

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
        self,
        account: str,
        chat_id: str | int,
        channel: str,
        frame: str | None,
        *,
        thread_id: str | int | None = None,
    ) -> None:
        """Seed one provider-confirmed frame; ``None`` clears that channel."""
        if channel not in self.CHANNELS:
            raise ValueError(f"unknown Task Card channel: {channel}")
        key = self.key(account, chat_id, thread_id)
        self._set_slot(self._frames, key, channel, frame)
        self._set_slot(self._desired_frames, key, channel, frame)

    @staticmethod
    def _set_slot(
        store: dict[str, dict[str, str]],
        key: str,
        channel: str,
        frame: str | None,
    ) -> None:
        slots = store.setdefault(key, {})
        if frame is None:
            slots.pop(channel, None)
            if not slots:
                store.pop(key, None)
        else:
            slots[channel] = frame

    def _store_slots(
        self,
        account: str,
        chat_id: str | int,
        slots: dict[str, str],
        *,
        confirmed: bool,
        thread_id: str | int | None = None,
    ) -> None:
        key = self.key(account, chat_id, thread_id)
        if slots:
            self._desired_frames[key] = dict(slots)
            if confirmed:
                self._frames[key] = dict(slots)
        else:
            self._desired_frames.pop(key, None)
            if confirmed:
                self._frames.pop(key, None)

    def _proposed_slots(
        self,
        account: str,
        chat_id: str | int,
        *,
        thread_id: str | int | None,
        channel: str | None,
        frame: str | None,
        confirmed: bool,
    ) -> dict[str, str]:
        source = self._frames if confirmed else self._desired_frames
        slots = dict(source.get(self.key(account, chat_id, thread_id), {}))
        if channel is not None:
            if channel not in self.CHANNELS:
                raise ValueError(f"unknown Task Card channel: {channel}")
            if frame is None:
                slots.pop(channel, None)
            else:
                slots[channel] = frame
        return slots

    def _compose_slots(self, slots: dict[str, str]) -> str:
        automatic = slots.get("automatic", "")
        programmable = slots.get("programmable", "")
        if not programmable:
            return automatic
        heading = (
            self._programmable_heading()
            if callable(self._programmable_heading)
            else self._programmable_heading
        )
        watch = f"{heading}\n{programmable}"
        return watch if not automatic else f"{automatic}\n\n{watch}"

    def compose_slots(self, slots: dict[str, str]) -> str:
        """Compose one already-proposed two-slot transaction."""
        unknown = set(slots) - set(self.CHANNELS)
        if unknown:
            raise ValueError(f"unknown Task Card channel: {sorted(unknown)[0]}")
        return self._compose_slots(dict(slots))

    def compose(
        self,
        account: str,
        chat_id: str | int,
        *,
        thread_id: str | int | None = None,
        channel: str | None = None,
        frame: str | None = None,
        confirmed: bool = False,
    ) -> str:
        """Compose desired slots, or confirmed slots for an existence probe."""
        slots = self._proposed_slots(
            account,
            chat_id,
            thread_id=thread_id,
            channel=channel,
            frame=frame,
            confirmed=confirmed,
        )
        return self._compose_slots(slots)

    def delivery_lock(
        self,
        account: str,
        chat_id: str | int,
        thread_id: str | int | None = None,
    ) -> threading.RLock:
        key = self.key(account, chat_id, thread_id)
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def project(
        self,
        account: str,
        chat_id: str | int,
        channel: str,
        frame: str | None,
        *,
        error: str,
        resident_id: str | None = None,
        empty_fallback: str | None = None,
        thread_id: str | int | None = None,
    ) -> dict[str, Any]:
        with self.delivery_lock(account, chat_id, thread_id):
            return self.project_locked(
                account,
                chat_id,
                channel,
                frame,
                error=error,
                resident_id=resident_id,
                empty_fallback=empty_fallback,
                thread_id=thread_id,
            )

    def project_locked(
        self,
        account: str,
        chat_id: str | int,
        channel: str,
        frame: str | None,
        *,
        error: str,
        resident_id: str | None = None,
        empty_fallback: str | None = None,
        thread_id: str | int | None = None,
        proposed_slots: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Project while the caller already owns this route's delivery lock."""
        if channel not in self.CHANNELS:
            return {"status": "error", "error": f"Unknown channel: {channel}"}
        if not self.enabled():
            if channel == "programmable" and frame is None:
                # A hidden finalize retires the watch without transport. Clear
                # both accepted intent and the resident's retained comparison
                # frame so neither can resurrect after re-enable; the adapter
                # keeps one pending clear to reconcile provider bytes later.
                desired = self._proposed_slots(
                    account, chat_id, thread_id=thread_id,
                    channel=channel, frame=None, confirmed=False,
                )
                self._store_slots(
                    account, chat_id, desired, confirmed=False,
                    thread_id=thread_id,
                )
                confirmed = self._proposed_slots(
                    account, chat_id, thread_id=thread_id,
                    channel=channel, frame=None, confirmed=True,
                )
                self._store_slots(
                    account, chat_id, confirmed, confirmed=True,
                    thread_id=thread_id,
                )
            return {"status": "ok", "suppressed": True, "taskcard": False}
        if self._transport is None:
            assert self._legacy_deliver is not None
            return self._legacy_deliver(
                account,
                chat_id,
                channel,
                frame,
                error=error,
                resident_id=resident_id,
                empty_fallback=empty_fallback,
            )
        return self.deliver_locked(
            account,
            chat_id,
            channel,
            frame,
            error=error,
            resident_id=resident_id,
            empty_fallback=empty_fallback,
            thread_id=thread_id,
            proposed_slots=proposed_slots,
        )

    def deliver_locked(
        self,
        account: str,
        chat_id: str | int,
        channel: str,
        frame: str | None,
        *,
        error: str,
        resident_id: str | None = None,
        empty_fallback: str | None = None,
        thread_id: str | int | None = None,
        proposed_slots: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one already-serialized resident delivery transaction."""
        if channel not in self.CHANNELS:
            return {"status": "error", "error": f"Unknown channel: {channel}"}
        transport = self._require_transport()
        route = self.route(account, chat_id, thread_id)
        if proposed_slots is None:
            proposed_slots = self._proposed_slots(
                account, chat_id, thread_id=thread_id,
                channel=channel, frame=frame, confirmed=False,
            )
        else:
            proposed_slots = dict(proposed_slots)
            unknown = set(proposed_slots) - set(self.CHANNELS)
            if unknown:
                return {
                    "status": "error",
                    "error": f"Unknown channel: {sorted(unknown)[0]}",
                }
        text = self._compose_slots(proposed_slots)
        if not text and empty_fallback is not None:
            text = empty_fallback

        tracked_resident = transport.get_resident(route)
        resident_id = tracked_resident or resident_id
        if resident_id:
            if not transport.matches_route(route, resident_id):
                return {"status": "error", "error": error}
            if transport.is_superseded(route, resident_id):
                outcome = self.rotate_to_latest(
                    route,
                    resident_id,
                    text,
                    error=error,
                )
                if outcome.get("status") == "ok":
                    self._store_slots(
                        account, chat_id, proposed_slots,
                        confirmed=not outcome.get("pending", False),
                        thread_id=thread_id,
                    )
                return outcome

            edit_outcome, edit_error = transport.edit(resident_id, text)
            if edit_outcome in (self.EDIT_OK, self.EDIT_THROTTLED):
                self._store_slots(
                    account, chat_id, proposed_slots,
                    confirmed=edit_outcome == self.EDIT_OK,
                    thread_id=thread_id,
                )
                outcome: dict[str, Any] = {
                    "status": "ok",
                    "message_id": resident_id,
                }
                if edit_outcome == self.EDIT_THROTTLED:
                    outcome["pending"] = True
                return outcome
            if edit_outcome == self.EDIT_FAILED:
                return {"status": "error", "error": edit_error or error}

            outcome = self.replace_after_probe(
                route,
                resident_id,
                text,
                error=error,
            )
            if outcome.get("status") == "ok":
                self._store_slots(
                    account, chat_id, proposed_slots,
                    confirmed=not outcome.get("pending", False),
                    thread_id=thread_id,
                )
            return outcome

        result = transport.send(route, text)
        if result is None or result.get("status") != self.SEND_OK:
            reported = result.get("error") if result is not None else None
            outcome: dict[str, Any] = {
                "status": "error",
                "error": reported if isinstance(reported, str) and reported else error,
            }
            if result is not None and result.get("status") == self.SEND_INDETERMINATE:
                outcome["indeterminate_send"] = True
            return outcome

        new_id = result["message_id"]
        self._store_slots(
            account, chat_id, proposed_slots, confirmed=True,
            thread_id=thread_id,
        )
        persisted = transport.persist(route, new_id)
        outcome = {"status": "ok", "message_id": new_id}
        if not persisted:
            outcome["resident_persist_failed"] = True
        return outcome

    def rotate_to_latest(
        self,
        route: TaskCardRoute,
        stale_id: str,
        text: str,
        *,
        error: str,
    ) -> dict[str, Any]:
        """Probe committed content, then replace the exact stale resident."""
        transport = self._require_transport()
        committed_text = self.compose(
            route.account,
            route.chat_id,
            thread_id=route.thread_id,
            confirmed=True,
        )
        if committed_text:
            probe_outcome, probe_error = transport.edit(stale_id, committed_text)
            if probe_outcome == self.EDIT_FAILED:
                return {"status": "error", "error": probe_error or error}
            if probe_outcome == self.EDIT_THROTTLED:
                # The provider adapter retries the whole latest projection, not
                # this existence-probe text, once the route becomes eligible.
                return {
                    "status": "ok",
                    "message_id": stale_id,
                    "pending": True,
                }
        return self.replace_after_probe(route, stale_id, text, error=error)

    def replace_after_probe(
        self,
        route: TaskCardRoute,
        stale_id: str,
        text: str,
        *,
        error: str,
    ) -> dict[str, Any]:
        """Delete/missing-confirm the exact old resident, then replace once."""
        transport = self._require_transport()
        delete_outcome = transport.delete(stale_id)
        if delete_outcome == self.DELETE_FAILED:
            return {
                "status": "error",
                "error": error,
                "stale_delete_failed": True,
            }

        current = transport.get_resident(route)
        if current and current != stale_id:
            adopt_outcome, adopt_error = transport.edit(current, text)
            if adopt_outcome in (self.EDIT_OK, self.EDIT_THROTTLED):
                outcome = {
                    "status": "ok",
                    "message_id": current,
                    "adopted_resident": True,
                }
                if adopt_outcome == self.EDIT_THROTTLED:
                    outcome["pending"] = True
                return outcome
            if adopt_outcome == self.EDIT_FAILED:
                return {"status": "error", "error": adopt_error or error}

        result = transport.send(route, text)
        if result is None or result.get("status") != self.SEND_OK:
            reported = result.get("error") if result is not None else None
            outcome: dict[str, Any] = {
                "status": "error",
                "error": reported if isinstance(reported, str) and reported else error,
            }
            if delete_outcome == self.DELETE_OK:
                outcome["old_resident_deleted"] = True
            if result is not None and result.get("status") == self.SEND_INDETERMINATE:
                outcome["indeterminate_send"] = True
            return outcome

        new_id = result["message_id"]
        persisted = transport.persist(route, new_id)
        outcome = {"status": "ok", "message_id": new_id}
        if not persisted:
            outcome["resident_persist_failed"] = True
        return outcome

    def _require_transport(self) -> TaskCardResidentTransport:
        if self._transport is None:
            raise RuntimeError("resident transport is not configured")
        return self._transport

    def rehydrate(self) -> dict[str, dict[str, str]]:
        return self._frames

    def ensure(
        self,
        account: str,
        chat_id: str | int,
        frame: str,
        *,
        error: str,
        thread_id: str | int | None = None,
    ) -> dict[str, Any]:
        return self.project(
            account,
            chat_id,
            "automatic",
            frame,
            error=error,
            thread_id=thread_id,
        )
