"""Intrinsic declarative Task Card capability.

One model-facing root ``task_card`` owns a single agent-local Task Card artifact
under ``<workdir>/taskcard/``:

- ``status``     — exact text ``active`` or ``inactive``
- ``taskcard.md`` — the rendered card body
- ``watch.json`` — persisted active-watch descriptor for restart resume

The producer writes only those files. Channels consume/project them
independently. The watch descriptor survives ``refresh``/molt/agent-stop so a
restart rehydrates the active watch; ``stop``/``remove``/refresh exhaustion
clear it because those are deliberate terminal ends.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from lingtai.kernel import notifications
from lingtai.kernel._fsutil import atomic_write_json, read_json

from ..tool_family import ChildTool, ToolFamily
from ..tool_family.manual import MANUAL_INPUT_SCHEMA, build_manual_child

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent

# Built-in fallback defaults, used when no configured value applies (missing
# config file, missing field, or an invalid field). ``interval_s`` is a pure
# cadence default with no ceiling — only ``_MIN_INTERVAL_S`` bounds it, in
# either direction. ``timeout_s``/``max_refreshes`` are safety ceilings: a
# configured value lowers the effective ceiling for both the default (used
# when a watch omits the field) and the maximum an explicit per-watch value
# may request.
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_INTERVAL_S = 5.0
_MIN_INTERVAL_S = 1.0
_MIN_TIMEOUT_S = 0.1
_DEFAULT_MAX_REFRESHES = 2000
_DEFAULT_REMINDER_TURNS = 10
_TASKCARD_DIR = "taskcard"
# Hard ceiling for the rendered card body (Jason #taskcard-resident). A renderer
# output longer than this is REFUSED, never truncated, so the resident
# ``_meta.agent_meta.taskcard`` projection can stay a bounded high-attention
# goal. Complex progress belongs in files behind the card.
_MAX_BODY_CHARS = 2000
_STATUS_FILENAME = "status"
_BODY_FILENAME = "taskcard.md"
_CONFIG_FILENAME = "taskcard.json"
_WATCH_FILENAME = "watch.json"
# One-way migration source only: the retired Telegram-owned reverse-channel
# design persisted its own refresh ceiling here. Consulted only when this
# capability's own config file has never been created; that first resolution
# is always persisted (migrated or not), so this path is never read again
# afterward (see ``TaskCardManager._migrate_legacy_config``).
_LEGACY_CONFIG_DIR = "telegram"
# ``TelegramService``'s own untouched default/ceiling for that legacy field
# (see ``mcp_servers/telegram/service.py:_TASKCARD_DEFAULT_MAX_REFRESHES``).
# The ordinary ``/taskcard on|off|N`` commands persist that file's three
# fields together purely to toggle unrelated presentation settings, so a
# ``max_refreshes`` sitting at exactly this value carries no migration
# signal — only a value that actually differs proves a human, or an earlier
# now-removed interface, once chose it on purpose. Migrating this untouched
# value forward would silently cap most real agents below the new built-in
# default instead of leaving them on it.
_LEGACY_UNTOUCHED_MAX_REFRESHES = 1000
_MANUAL_SKILL_NAME = "task_card"
notifications.register_notification_channel("task_card")


class _Config(NamedTuple):
    """Resolved agent-wide Task Card defaults/ceilings for a new watch."""

    interval_s: float
    timeout_s: float
    max_refreshes: int
    reminder_turns: int
    max_body_chars: int


_BUILTIN_CONFIG = _Config(
    _DEFAULT_INTERVAL_S,
    _DEFAULT_TIMEOUT_S,
    _DEFAULT_MAX_REFRESHES,
    _DEFAULT_REMINDER_TURNS,
    _MAX_BODY_CHARS,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required is not None:
        value["required"] = required
    return value


_START_INPUT_SCHEMA = _object(
    {
        "renderer_path": {"type": "string"},
        "interval_s": {"type": "number", "minimum": 1},
        "timeout_s": {"type": "number", "minimum": 0.1},
        "max_refreshes": {"type": "integer", "minimum": 1},
    },
    required=["renderer_path"],
)
_WATCH_INPUT_SCHEMA = _object({"watch_id": {"type": "string"}}, required=["watch_id"])
_REMOVE_INPUT_SCHEMA = _object({}, required=[])

_CHILDREN: tuple[tuple[str, dict[str, Any]], ...] = (
    ("start", _START_INPUT_SCHEMA),
    ("inspect", _WATCH_INPUT_SCHEMA),
    ("retry", _WATCH_INPUT_SCHEMA),
    ("stop", _WATCH_INPUT_SCHEMA),
    ("remove", _REMOVE_INPUT_SCHEMA),
    ("manual", MANUAL_INPUT_SCHEMA),
)


def _schema_family() -> ToolFamily:
    return ToolFamily(
        "task_card",
        [ChildTool(name, schema, lambda _input: {}) for name, schema in _CHILDREN],
    )


_SCHEMA_FAMILY = _schema_family()


def get_schema() -> dict[str, Any]:
    schema = _SCHEMA_FAMILY.build_schema()
    schema["properties"]["action"]["description"] = (
        "Declarative Task Card action. start keeps one renderer watch writing the "
        "agent-local taskcard/status and taskcard/taskcard.md files; inspect, retry, "
        "and stop read or control that one artifact; remove is the terminal "
        "lifecycle cleanup; manual explains the full contract."
    )
    return schema


def get_description() -> str:
    return (
        "Manage the intrinsic declarative Task Card artifact. Provide a Python "
        "renderer under your working directory whose stdout is the full Task Card "
        "body to write into taskcard/taskcard.md. The capability writes taskcard/"
        "taskcard.md atomically, writes taskcard/status as exact active/inactive, "
        "keeps at most one active watch per agent, and leaves projection to "
        "channel-specific readers. Use it proactively for meaningful long-running, "
        "multi-step, or parallel work so a human can follow progress; skip it for "
        "quick single-step work, ritual updates, or a body you cannot keep truthful "
        "and current. Restart a new watch when one expires mid-task. Use stop to "
        "pause a watch while preserving its last body, and "
        "remove once the work is completed, cancelled, or abandoned so the artifact "
        "cannot mislead a consumer as stale. Actions: start, inspect, retry, stop, "
        "remove, manual."
    )


class TaskCardError(Exception):
    """Synchronous, user-visible Task Card error."""


class _Watch:
    __slots__ = (
        "watch_id",
        "renderer_path",
        "interval_s",
        "timeout_s",
        "thread",
        "stop_event",
        "lock",
        "last_valid_body",
        "last_valid_at",
        "error",
        "error_key",
        "error_epoch",
        "stopping",
        "max_refreshes",
        "refreshes_used",
        "attempt_lock",
        "limit_notified",
        "stop_reason",
        "terminated",
    )

    def __init__(
        self,
        watch_id: str,
        renderer_path: Path,
        interval_s: float,
        timeout_s: float,
        max_refreshes: int,
    ) -> None:
        self.watch_id = watch_id
        self.renderer_path = renderer_path
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.last_valid_body: str | None = None
        self.last_valid_at: str | None = None
        self.error: dict[str, Any] | None = None
        self.error_key: str | None = None
        self.error_epoch = 0
        self.stopping = False
        self.max_refreshes = max_refreshes
        self.refreshes_used = 0
        self.attempt_lock = threading.Lock()
        self.limit_notified = False
        self.stop_reason: str | None = None
        self.terminated = False


class TaskCardManager:
    """Own the intrinsic Task Card watch and atomic writer contract."""

    def __init__(self, agent: BaseAgent) -> None:
        self._agent = agent
        self._lock = threading.RLock()
        self._counter = 0
        self._completed_text_turns = 0
        self._watch: _Watch | None = None
        self._taskcard_dir = Path(agent._working_dir) / _TASKCARD_DIR
        self._status_path = self._taskcard_dir / _STATUS_FILENAME
        self._body_path = self._taskcard_dir / _BODY_FILENAME
        self._config_path = self._taskcard_dir / _CONFIG_FILENAME
        self._watch_path = self._taskcard_dir / _WATCH_FILENAME
        self._legacy_config_path = self._taskcard_dir.parent / _LEGACY_CONFIG_DIR / _CONFIG_FILENAME

    def handle(self, args: dict[str, Any] | None) -> dict[str, Any]:
        try:
            return self._family().handle(args or {})
        except TaskCardError as exc:
            return {"status": "failed", "message": str(exc)}

    def _family(self) -> ToolFamily:
        children = [
            ChildTool("start", _START_INPUT_SCHEMA, self._start_child),
            ChildTool("inspect", _WATCH_INPUT_SCHEMA, self._inspect_child),
            ChildTool("retry", _WATCH_INPUT_SCHEMA, self._retry_child),
            ChildTool("stop", _WATCH_INPUT_SCHEMA, self._stop_child),
            ChildTool("remove", _REMOVE_INPUT_SCHEMA, self._remove_child),
            build_manual_child(self._agent, _MANUAL_SKILL_NAME),
        ]
        return ToolFamily("task_card", children)

    def _start_child(self, input_: dict[str, Any]) -> dict[str, Any]:
        renderer_path = self._validate_renderer_path(input_.get("renderer_path"))
        config = self._load_config()
        # interval_s is a cadence default, not a safety ceiling: an omitted
        # value uses the configured cadence, and an explicit value is never
        # clamped down to it — only the absolute floor applies either way.
        interval_s = self._coerce_positive(
            input_.get("interval_s", config.interval_s), "interval_s", _MIN_INTERVAL_S
        )
        # timeout_s and max_refreshes are safety ceilings: an omitted value
        # uses the configured ceiling, and an explicit value may only lower
        # it (min-clamped), never exceed it.
        requested_timeout = input_.get("timeout_s")
        if requested_timeout is None:
            timeout_s = config.timeout_s
        else:
            timeout_s = min(
                self._coerce_positive(requested_timeout, "timeout_s", _MIN_TIMEOUT_S),
                config.timeout_s,
            )
        requested_max = input_.get("max_refreshes")
        if requested_max is None:
            effective_max = config.max_refreshes
        elif type(requested_max) is int and requested_max > 0:
            effective_max = min(requested_max, config.max_refreshes)
        else:
            raise TaskCardError("max_refreshes must be a positive integer")
        with self._lock:
            if self._watch is not None:
                raise TaskCardError("only one Task Card watch may be active per agent")
            self._counter += 1
            watch = _Watch(
                f"tc_{self._counter}",
                renderer_path,
                interval_s,
                timeout_s,
                effective_max,
            )
            self._watch = watch
        try:
            body = self._run_renderer(renderer_path, timeout_s)
            self._publish_active(body)
            self._clear_reminder()
        except Exception:
            with self._lock:
                if self._watch is watch:
                    self._watch = None
            try:
                self._write_status("inactive")
            except OSError:
                pass
            raise
        with watch.lock:
            watch.last_valid_body = body
            watch.last_valid_at = _utc_now_iso()
        # Persist the descriptor before spawning the thread: the updater could
        # otherwise exhaust and clear it in the gap, resurrecting a dead watch.
        # Persistence is best-effort here — a start that already succeeded must
        # not fail because the descriptor write hit ENOSPC/EROFS/EACCES.
        try:
            self._persist_watch(watch)
        except OSError:
            pass
        self._spawn(watch)
        return {
            "status": "ok",
            "watch_id": watch.watch_id,
            "state": "watching",
            **self._paths_payload(),
            **self._status_payload("active"),
            **self._refresh_fields(watch),
        }

    def _inspect_child(self, input_: dict[str, Any]) -> dict[str, Any]:
        watch = self._require_watch(input_.get("watch_id"))
        with watch.lock:
            if watch.stopping:
                state = "stop_failed" if watch.error else "stopping"
                status_value = "inactive"
            elif watch.error:
                state = "error"
                status_value = "active"
            else:
                state = "watching"
                status_value = "active"
            return {
                "status": "ok",
                "watch_id": watch.watch_id,
                "state": state,
                "last_valid_body": watch.last_valid_body,
                "last_valid_body_at": watch.last_valid_at,
                "error": watch.error,
                **self._paths_payload(),
                **self._status_payload(status_value),
                **self._refresh_fields(watch),
            }

    def _retry_child(self, input_: dict[str, Any]) -> dict[str, Any]:
        watch = self._require_watch(input_.get("watch_id"))
        if watch.stopping:
            return self._stop_watch(watch)
        self._tick(watch)
        return self._inspect_child({"watch_id": watch.watch_id})

    def _stop_child(self, input_: dict[str, Any]) -> dict[str, Any]:
        return self._stop_watch(self._require_watch(input_.get("watch_id")))

    def _stop_watch(self, watch: _Watch) -> dict[str, Any]:
        with watch.lock:
            watch.stopping = True
        try:
            self._write_status("inactive")
        except OSError as exc:
            error = {
                "code": "stop_finalize_failed",
                "retryable": True,
                "message": f"failed to write inactive status: {type(exc).__name__}",
            }
            with watch.lock:
                watch.error = error
            return {
                "status": "error",
                "watch_id": watch.watch_id,
                "state": "stop_failed",
                "error": error,
                **self._paths_payload(),
                **self._status_payload("inactive"),
                **self._refresh_fields(watch),
            }
        watch.stop_event.set()
        thread = watch.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=watch.timeout_s + 1.0)
        if thread is not None and thread.is_alive():
            error = {
                "code": "stop_thread_alive",
                "retryable": True,
                "message": "the watcher thread has not stopped yet; retry stop once quiescent",
            }
            with watch.lock:
                watch.error = error
            return {
                "status": "error",
                "watch_id": watch.watch_id,
                "state": "stop_failed",
                "error": error,
                **self._paths_payload(),
                **self._status_payload("inactive"),
                **self._refresh_fields(watch),
            }
        with self._lock:
            if self._watch is watch:
                self._watch = None
        with watch.lock:
            watch.terminated = True
        self._clear_watch_descriptor()
        return {
            "status": "ok",
            "watch_id": watch.watch_id,
            "state": "stopped",
            **self._paths_payload(),
            **self._status_payload("inactive"),
            **self._refresh_fields(watch),
        }

    def _remove_child(self, input_: dict[str, Any]) -> dict[str, Any]:
        """Terminal lifecycle cleanup: retire any active watch, then delete the body.

        Unlike ``stop``, ``remove`` takes no ``watch_id`` — it targets this
        agent's one artifact, not a specific watch, so it stays useful even
        after a restart lost the in-memory watch handle. If a watch is
        active it is retired exactly like ``stop`` (write ``inactive`` before
        the updater joins), so the updater cannot race a deleted body back
        into existence; only once that retirement is confirmed is the body
        actually removed. A stop failure (thread still running) blocks
        removal and is returned verbatim under ``state: "remove_blocked"`` so
        the caller can retry once the watch is quiescent.
        """
        with self._lock:
            watch = self._watch
        if watch is not None:
            stopped = self._stop_watch(watch)
            if stopped["status"] != "ok":
                return {**stopped, "state": "remove_blocked"}
        return self._finalize_remove()

    def _finalize_remove(self) -> dict[str, Any]:
        try:
            self._write_status("inactive")
        except OSError as exc:
            return {
                "status": "error",
                "state": "remove_failed",
                "error": {
                    "code": "remove_finalize_failed",
                    "retryable": True,
                    "message": f"failed to write inactive status: {type(exc).__name__}",
                },
                **self._paths_payload(),
                **self._status_payload("inactive"),
            }
        body_removed, delete_error = self._delete_body()
        if delete_error is not None:
            return {
                "status": "error",
                "state": "remove_failed",
                "error": {
                    "code": "remove_body_failed",
                    "retryable": True,
                    "message": f"failed to remove task card body: {type(delete_error).__name__}",
                },
                **self._paths_payload(),
                **self._status_payload("inactive"),
            }
        self._clear_watch_descriptor()
        # A concurrent agent-shutdown may still hold this watch: mark it
        # terminated so shutdown does not re-persist the descriptor after
        # ``remove`` deliberately retired it.
        with self._lock:
            watch = self._watch
        if watch is not None:
            with watch.lock:
                watch.terminated = True
        self._clear_reminder()
        return {
            "status": "ok",
            "state": "removed",
            "body_removed": body_removed,
            **self._paths_payload(),
            **self._status_payload("inactive"),
        }

    def _delete_body(self) -> tuple[bool, OSError | None]:
        try:
            self._body_path.unlink()
        except FileNotFoundError:
            return False, None
        except OSError as exc:
            return False, exc
        return True, None

    def _spawn(self, watch: _Watch) -> None:
        watch.thread = threading.Thread(
            target=self._loop,
            args=(watch,),
            daemon=True,
            name=f"task-card-watch-{watch.watch_id}",
        )
        watch.thread.start()

    def _loop(self, watch: _Watch) -> None:
        shutdown = getattr(self._agent, "_shutdown", None)
        while not watch.stop_event.is_set():
            if shutdown is not None and shutdown.is_set():
                return
            if watch.stop_event.wait(timeout=watch.interval_s):
                return
            if shutdown is not None and shutdown.is_set():
                return
            try:
                self._tick(watch)
            except Exception:
                # A background watch must never die without a trace: mark the
                # error (and notify) instead of letting the thread vanish.
                self._mark_error(
                    watch,
                    {
                        "code": "watch_crash",
                        "retryable": True,
                        "message": "task card watch crashed in refresh loop",
                    },
                    emit_notification=True,
                )
                return

    def _tick(self, watch: _Watch) -> None:
        with watch.attempt_lock:
            with watch.lock:
                if watch.stopping or watch.refreshes_used >= watch.max_refreshes:
                    return
                watch.refreshes_used += 1
                exhausted = watch.refreshes_used >= watch.max_refreshes
            try:
                body = self._run_renderer(watch.renderer_path, watch.timeout_s)
            except TaskCardError as exc:
                if self._stop_requested(watch):
                    return
                self._mark_error(watch, self._error_from_exc(exc), emit_notification=not exhausted)
                if exhausted:
                    self._exhaust(watch)
                return
            if self._stop_requested(watch):
                return
            try:
                self._write_body(body)
            except TaskCardError as exc:
                self._mark_error(
                    watch,
                    {
                        "code": "body_too_large",
                        "retryable": False,
                        "message": str(exc),
                    },
                    emit_notification=not exhausted,
                )
                if exhausted:
                    self._exhaust(watch)
                return
            except OSError as exc:
                self._mark_error(
                    watch,
                    {
                        "code": "write_failed",
                        "retryable": True,
                        "message": f"failed to update task card body: {type(exc).__name__}",
                    },
                    emit_notification=not exhausted,
                )
                if exhausted:
                    self._exhaust(watch)
                return
            self._mark_recovered(watch, body)
            self._clear_reminder()
            if exhausted and not watch.stopping:
                self._exhaust(watch)

    def _exhaust(self, watch: _Watch) -> None:
        with watch.lock:
            if watch.stop_reason == "max_refreshes":
                return
            watch.stop_reason = "max_refreshes"
            watch.stopping = True
        try:
            self._write_status("inactive")
        except OSError as exc:
            with watch.lock:
                watch.error = {
                    "code": "stop_finalize_failed",
                    "retryable": True,
                    "message": f"failed to write inactive status: {type(exc).__name__}",
                }
            self._emit_limit_event(watch)
            return
        with watch.lock:
            watch.terminated = True
        watch.stop_event.set()
        self._emit_limit_event(watch)
        with self._lock:
            if self._watch is watch:
                self._watch = None
        self._clear_watch_descriptor()

    def _stop_requested(self, watch: _Watch) -> bool:
        if watch.stop_event.is_set():
            return True
        shutdown = getattr(self._agent, "_shutdown", None)
        return bool(shutdown is not None and shutdown.is_set())

    def _publish_active(self, body: str) -> None:
        self._write_body(body)
        self._write_status("active")

    def _write_body(self, body: str) -> None:
        limit = self._load_config().max_body_chars
        if len(body) > limit:
            raise TaskCardError(
                f"taskcard body exceeds the {limit}-char cap "
                f"({len(body)} chars); keep the card a progressive-disclosure "
                "summary and move complex progress into files"
            )
        self._atomic_write_text(self._body_path, body, trailing_newline=False)

    def _write_status(self, status: str) -> None:
        if status not in {"active", "inactive"}:
            raise ValueError(f"invalid task card status: {status}")
        self._atomic_write_text(self._status_path, status, trailing_newline=False)

    @staticmethod
    def _atomic_write_text(path: Path, text: str, *, trailing_newline: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = text if not trailing_newline else f"{text}\n"
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _run_renderer(self, path: Path, timeout_s: float) -> str:
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self._agent._working_dir),
            )
        except subprocess.TimeoutExpired as exc:
            raise TaskCardError(f"renderer timed out after {timeout_s}s") from exc
        except OSError as exc:
            raise TaskCardError("renderer could not be executed") from exc
        if proc.returncode != 0:
            raise TaskCardError(f"renderer exited with status {proc.returncode}")
        return self._validate_body(proc.stdout)

    @staticmethod
    def _validate_body(stdout: str) -> str:
        if not isinstance(stdout, str) or not stdout.strip():
            raise TaskCardError("renderer produced no output")
        return stdout

    @staticmethod
    def _error_from_exc(exc: TaskCardError) -> dict[str, Any]:
        message = str(exc)
        if "timed out" in message:
            code = "renderer_timeout"
        elif "status" in message:
            code = "renderer_nonzero_exit"
        else:
            code = "renderer_failed"
        return {"code": code, "message": message, "retryable": True}

    def _mark_error(
        self,
        watch: _Watch,
        error: dict[str, Any],
        *,
        emit_notification: bool = True,
    ) -> None:
        key = str(error.get("code"))
        with watch.lock:
            if watch.error is None:
                watch.error_epoch += 1
            already = watch.error_key == key
            watch.error = error
            watch.error_key = key
            epoch = watch.error_epoch
            last_valid_at = watch.last_valid_at
        if already or not emit_notification:
            return
        self._emit_event(watch, error, last_valid_at, epoch=epoch, recovered=False)

    def _mark_recovered(self, watch: _Watch, body: str) -> None:
        with watch.lock:
            was_errored = watch.error is not None
            epoch = watch.error_epoch
            watch.error = None
            watch.error_key = None
            watch.last_valid_body = body
            watch.last_valid_at = _utc_now_iso()
        if was_errored:
            self._emit_event(watch, None, None, epoch=epoch, recovered=True)

    def _emit_event(
        self,
        watch: _Watch,
        error: dict[str, Any] | None,
        last_valid_at: str | None,
        *,
        epoch: int,
        recovered: bool,
    ) -> None:
        enqueue = getattr(self._agent, "_enqueue_system_notification", None)
        if not callable(enqueue):
            return
        if recovered:
            body = f"Task Card watch {watch.watch_id} recovered."
            extra: dict[str, Any] = {"watch_id": watch.watch_id, "state": "recovered"}
            key = f"task_card.recovered:{watch.watch_id}:{epoch}"
            priority = "normal"
        else:
            code = str((error or {}).get("code", "error"))
            body = f"Task Card watch {watch.watch_id} failed: {(error or {}).get('message', code)}"
            extra = {
                "watch_id": watch.watch_id,
                "state": "error",
                "code": code,
                "retryable": (error or {}).get("retryable", "unknown"),
            }
            if last_valid_at:
                extra["last_valid_body_at"] = last_valid_at
            key = f"task_card.error:{watch.watch_id}:{epoch}:{code}"
            priority = "high"
        try:
            enqueue(
                source="task_card.error",
                ref_id=watch.watch_id,
                body=body,
                idempotency_key=key,
                skip_if_idempotency_key_exists=True,
                priority=priority,
                extra=extra,
            )
        except Exception:
            pass

    def _emit_limit_event(self, watch: _Watch) -> None:
        enqueue = getattr(self._agent, "_enqueue_system_notification", None)
        if not callable(enqueue):
            return
        with watch.lock:
            if watch.limit_notified:
                return
            watch.limit_notified = True
            used = watch.refreshes_used
            maximum = watch.max_refreshes
            last_valid_at = watch.last_valid_at
        extra: dict[str, Any] = {
            "watch_id": watch.watch_id,
            "state": "stopped",
            "reason": "max_refreshes",
            "used": used,
            "max": maximum,
        }
        if last_valid_at:
            extra["last_valid_body_at"] = last_valid_at
        try:
            enqueue(
                source="task_card.limit",
                ref_id=watch.watch_id,
                body=(
                    f"Task Card watch {watch.watch_id} reached its refresh limit. "
                    "Refresh or reinspect the underlying task state, and start a new "
                    "watch only if useful. If this work is still ongoing, start a new "
                    "watch (task_card action='start') — do not let the card go dark "
                    "mid-task."
                ),
                idempotency_key=f"task_card.limit:{watch.watch_id}:{maximum}",
                skip_if_idempotency_key_exists=True,
                priority="normal",
                extra=extra,
            )
        except Exception:
            pass

    def has_active_watch(self) -> bool:
        """True while exactly one watch is running and not yet retiring.

        Read-only cross-capability probe: the daemon fleet nudge asks this
        before suggesting a card, so an agent that already keeps one is never
        told to start another.
        """
        with self._lock:
            watch = self._watch
        if watch is None:
            return False
        with watch.lock:
            return not (watch.stopping or watch.terminated)

    def _require_watch(self, watch_id: Any) -> _Watch:
        if not isinstance(watch_id, str):
            raise TaskCardError("watch_id is required")
        with self._lock:
            watch = self._watch
        if watch is None or watch.watch_id != watch_id:
            raise TaskCardError(f"unknown watch_id: {watch_id}")
        return watch

    def on_completed_work_turn(self) -> None:
        threshold = self._reminder_turns()
        with self._lock:
            self._completed_text_turns += 1
            if self._completed_text_turns < threshold:
                return
            self._completed_text_turns = 0
        notifications.submit(
            self._agent,
            "task_card",
            data={"source": "task_card.reminder", "turns": threshold},
            header="Task Card reminder",
            instructions="Check whether the Task Card is absent or stale; update or issue one only if useful.",
        )

    def _clear_reminder(self) -> None:
        with self._lock:
            self._completed_text_turns = 0
        try:
            notifications.clear(self._agent, "task_card")
        except AttributeError:
            pass

    def shutdown_for_agent_stop(self, *, reason: str = "") -> None:
        del reason
        self._clear_reminder()
        with self._lock:
            watch = self._watch
            self._watch = None
        if watch is None:
            return
        with watch.lock:
            watch.stopping = True
        try:
            self._write_status("inactive")
        except OSError:
            pass
        watch.stop_event.set()
        if watch.thread is not None and watch.thread.is_alive():
            watch.thread.join(timeout=watch.timeout_s + 1.0)
        # Carry the live refresh budget into the persisted descriptor so a
        # restart resume honors it instead of starting over at zero. A watch
        # already deliberately terminated (stop/remove/exhaust racing this
        # shutdown) must not be resurrected: re-check the flag under
        # ``watch.lock``, which also serializes against the descriptor write.
        with watch.lock:
            if watch.terminated:
                return
            try:
                self._persist_watch(watch)
            except OSError:
                pass

    def _persist_watch(self, watch: _Watch) -> None:
        """Persist the active watch descriptor so a restart can resume it.

        Written atomically on a successful ``start``. Kept across
        ``shutdown_for_agent_stop`` (refresh/molt/agent-stop) so the next
        process can rehydrate the watch; cleared on ``stop``, ``remove``, or
        refresh-limit exhaustion because those are deliberate terminal ends
        of the watch, not process-transient stops.
        """
        with watch.lock:
            workdir = Path(self._agent._working_dir).resolve()
            try:
                renderer_rel = str(watch.renderer_path.relative_to(workdir))
            except ValueError:
                # Path not under the workdir (should not happen given
                # validation); fall back to the absolute path.
                renderer_rel = str(watch.renderer_path)
            payload = {
                "watch_id": watch.watch_id,
                "renderer_path": renderer_rel,
                "interval_s": watch.interval_s,
                "timeout_s": watch.timeout_s,
                "max_refreshes": watch.max_refreshes,
                "refreshes_used": watch.refreshes_used,
                "started_at": _utc_now_iso(),
            }
        atomic_write_json(self._watch_path, payload, fsync=True)

    def _clear_watch_descriptor(self) -> None:
        try:
            self._watch_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # A stale descriptor is harmless; a later start/resume overwrites it.
            pass

    def resume_persisted_watch(self) -> dict[str, Any] | None:
        """Rehydrate the persisted watch after a process restart.

        Called from ``setup`` on every boot. If ``taskcard/watch.json`` exists
        and is valid, re-creates the watch (same id/params/refresh budget),
        writes the current renderer output to the body, marks ``active``, and
        spawns the updater thread. Returns the start-style payload on success
        or ``None`` when there is nothing to resume.

        A missing/corrupt descriptor, a renderer that no longer exists, an
        invalid path, or an already-exhausted refresh budget are treated as
        stale: the descriptor is cleared and the card left ``inactive`` so a
        boot never silently resurrects a dead watch.
        """
        with self._lock:
            if self._watch is not None:
                return None
        try:
            payload = read_json(self._watch_path, expect=dict)
        except (OSError, ValueError, TypeError):
            # Corrupt descriptor: the contract promises stale descriptors are
            # cleared on boot, not left to wedge every future boot.
            self._clear_watch_descriptor()
            return None
        if not payload:
            # Valid-JSON but empty (e.g. ``{}``): can never resume; clear it.
            self._clear_watch_descriptor()
            return None
        watch_id = payload.get("watch_id")
        if not isinstance(watch_id, str):
            self._clear_watch_descriptor()
            return None
        # Carry the watch-id counter before any stale-clear path: a discard
        # must not reset the counter and let a later start reuse ``tc_1``
        # (notification idempotency keys embed the watch id).
        with self._lock:
            try:
                counter = int(str(watch_id).split("_")[-1])
                self._counter = max(self._counter, counter)
            except (ValueError, IndexError):
                pass
        renderer_raw = payload.get("renderer_path")
        if not isinstance(renderer_raw, str):
            self._clear_watch_descriptor()
            return None
        try:
            renderer_path = self._validate_renderer_path(renderer_raw)
        except TaskCardError:
            self._clear_watch_descriptor()
            return None
        config = self._load_config()
        try:
            interval_s = self._coerce_positive(
                payload.get("interval_s", config.interval_s), "interval_s", _MIN_INTERVAL_S
            )
            requested_timeout = payload.get("timeout_s")
            if requested_timeout is None:
                timeout_s = config.timeout_s
            else:
                timeout_s = min(
                    self._coerce_positive(requested_timeout, "timeout_s", _MIN_TIMEOUT_S),
                    config.timeout_s,
                )
        except TaskCardError:
            # Non-numeric cadence/ceiling in the descriptor: treat as stale.
            self._clear_watch_descriptor()
            return None
        requested_max = payload.get("max_refreshes")
        if type(requested_max) is int and requested_max > 0:
            effective_max = min(requested_max, config.max_refreshes)
        else:
            effective_max = config.max_refreshes
        refreshes_used = payload.get("refreshes_used", 0)
        if type(refreshes_used) is not int or refreshes_used < 0:
            refreshes_used = 0
        if refreshes_used >= effective_max:
            # Budget already exhausted while away: retire the stale card.
            self._clear_watch_descriptor()
            return None
        watch = _Watch(
            watch_id,
            renderer_path,
            interval_s,
            timeout_s,
            effective_max,
        )
        watch.refreshes_used = refreshes_used
        with self._lock:
            self._watch = watch
        try:
            body = self._run_renderer(renderer_path, timeout_s)
            self._publish_active(body)
            self._clear_reminder()
        except Exception:
            # A transient renderer failure at boot must not kill the card:
            # keep the last body, mark active unconditionally (the watch IS
            # live and the returned payload says so), and let the thread retry.
            try:
                body = self._body_path.read_text(encoding="utf-8")
            except OSError:
                body = None
            try:
                self._write_status("active")
            except OSError:
                pass
            with watch.lock:
                watch.last_valid_body = body
                watch.last_valid_at = _utc_now_iso() if body is not None else None
        else:
            with watch.lock:
                watch.last_valid_body = body
                watch.last_valid_at = _utc_now_iso()
        self._spawn(watch)
        return {
            "status": "ok",
            "watch_id": watch.watch_id,
            "state": "watching",
            "resumed": True,
            **self._paths_payload(),
            **self._status_payload("active"),
            **self._refresh_fields(watch),
        }

    def _load_config(self) -> _Config:
        """Load this agent's persisted Task Card defaults/ceilings.

        Each field falls back to its own built-in default independently, so
        one invalid field never discards a valid sibling. A config file that
        has never been created (not merely empty/invalid) triggers one-time
        resolution against legacy state instead of the plain built-in
        defaults; that resolution is persisted unconditionally, so this
        branch is taken at most once per agent (see
        ``_migrate_legacy_config``). If the config file already exists but
        cannot be read as a JSON object — missing, malformed, undecodable, or
        the wrong top-level type — it remains the sole owner: this falls back
        to built-in defaults without ever consulting legacy state.
        """
        if not self._config_path.is_file():
            return self._migrate_legacy_config()
        try:
            data = read_json(self._config_path, expect=dict)
        except (OSError, ValueError, TypeError):
            return _BUILTIN_CONFIG
        return _Config(
            self._config_number(data.get("interval_s"), _MIN_INTERVAL_S, _DEFAULT_INTERVAL_S),
            self._config_number(data.get("timeout_s"), _MIN_TIMEOUT_S, _DEFAULT_TIMEOUT_S),
            self._config_max_refreshes(data.get("max_refreshes")),
            self._config_reminder_turns(data.get("reminder_turns")),
            self._config_max_body_chars(data.get("max_body_chars")),
        )

    def _migrate_legacy_config(self) -> _Config:
        """One-time resolution against legacy state; always persisted.

        Reads ``<workdir>/telegram/taskcard.json`` (the retired Telegram
        controller's persisted ``max_refreshes`` fuse) only because this
        capability's own config file has never been created. A valid value
        that actually differs from Telegram's own untouched default is
        migrated into the resolved config; an absent, invalid, undecodable,
        or untouched-default legacy value resolves to the plain built-in
        defaults instead, exactly as if no legacy state existed — this is
        what keeps an ordinary ``/taskcard on|off|N`` user (who never
        customized the refresh ceiling, but whose settings file still
        carries that field at its own default) on the new built-in default
        instead of an incidental, non-chosen 1000.

        Either way, the resolved config is written into the intrinsic config
        file unconditionally. This is the only way that file is ever
        created, and creating it regardless of whether anything actually
        migrated is what makes the legacy read genuinely one-way and
        one-time: once it exists, ``_load_config`` never calls this method
        again for this agent, so a later change to the Telegram-owned file
        cannot alter intrinsic policy.
        """
        try:
            legacy = read_json(self._legacy_config_path, expect=dict)
        except (OSError, ValueError, TypeError):
            legacy = None
        legacy_max = legacy.get("max_refreshes") if legacy is not None else None
        if (
            type(legacy_max) is not int
            or legacy_max <= 0
            or legacy_max == _LEGACY_UNTOUCHED_MAX_REFRESHES
        ):
            resolved = _BUILTIN_CONFIG
        else:
            resolved = _Config(
                _DEFAULT_INTERVAL_S,
                _DEFAULT_TIMEOUT_S,
                legacy_max,
                _DEFAULT_REMINDER_TURNS,
                _MAX_BODY_CHARS,
            )
        try:
            atomic_write_json(
                self._config_path,
                {
                    "interval_s": resolved.interval_s,
                    "timeout_s": resolved.timeout_s,
                    "max_refreshes": resolved.max_refreshes,
                },
                fsync=True,
            )
        except OSError:
            pass
        return resolved

    @staticmethod
    def _config_number(value: Any, minimum: float, default: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        numeric = float(value)
        return numeric if math.isfinite(numeric) and numeric >= minimum else default

    @staticmethod
    def _config_max_refreshes(value: Any) -> int:
        return value if type(value) is int and value > 0 else _DEFAULT_MAX_REFRESHES

    @staticmethod
    def _config_reminder_turns(value: Any) -> int:
        return value if type(value) is int and value > 0 else _DEFAULT_REMINDER_TURNS

    @staticmethod
    def _config_max_body_chars(value: Any) -> int:
        return value if type(value) is int and value >= 100 else _MAX_BODY_CHARS

    def _reminder_turns(self) -> int:
        try:
            return self._config_reminder_turns(read_json(self._config_path, expect=dict).get("reminder_turns"))
        except (OSError, ValueError, TypeError):
            return _DEFAULT_REMINDER_TURNS

    def _validate_renderer_path(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise TaskCardError("renderer_path is required for start")
        workdir = Path(self._agent._working_dir)
        candidate = raw.strip()
        try:
            wd = workdir.resolve()
            joined = Path(candidate) if Path(candidate).is_absolute() else (workdir / candidate)
            resolved = joined.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise TaskCardError(f"renderer_path could not be resolved ({exc})") from exc
        try:
            resolved.relative_to(wd)
        except ValueError as exc:
            raise TaskCardError(
                "renderer_path must be inside the agent working directory "
                "(no path traversal, no absolute escape)"
            ) from exc
        if not resolved.is_file():
            raise TaskCardError("renderer_path must be an existing regular file")
        return resolved

    @staticmethod
    def _coerce_positive(value: Any, name: str, minimum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TaskCardError(f"{name} must be a number")
        numeric = float(value)
        if numeric < minimum:
            raise TaskCardError(f"{name} must be at least {minimum}")
        return numeric

    @staticmethod
    def _refresh_fields(watch: _Watch) -> dict[str, Any]:
        with watch.lock:
            used = watch.refreshes_used
            maximum = watch.max_refreshes
            reason = watch.stop_reason
        return {
            "refreshes_used": used,
            "max_refreshes": maximum,
            "refreshes_remaining": max(0, maximum - used),
            "stop_reason": reason,
        }

    def _paths_payload(self) -> dict[str, str]:
        return {
            "taskcard_dir": str(self._taskcard_dir),
            "status_path": str(self._status_path),
            "body_path": str(self._body_path),
            "watch_path": str(self._watch_path),
        }

    @staticmethod
    def _status_payload(status_value: str) -> dict[str, str]:
        return {"status_value": status_value}


def setup(agent: BaseAgent, **_ignored: Any) -> TaskCardManager:
    manager = getattr(agent, "_task_card_manager", None)
    if not isinstance(manager, TaskCardManager):
        manager = TaskCardManager(agent)
        agent._task_card_manager = manager
    else:
        manager._agent = agent
    agent.add_tool(
        "task_card",
        schema=get_schema(),
        handler=manager.handle,
        description=get_description(),
        glossary_package=None,
    )
    # A watch persisted by a previous process (refresh/molt/agent-stop) is
    # rehydrated on boot so the card survives process restarts. Deliberate
    # terminal ends (stop/remove/exhaust) already cleared the descriptor.
    try:
        manager.resume_persisted_watch()
    except Exception as exc:
        log = getattr(agent, "_log", None)
        if callable(log):
            try:
                log("task_card_resume_failed", error=str(exc))
            except Exception:
                pass
    return manager
