"""Public ``task_card`` controller — programmable Telegram Task Card watches.

Model-facing controller for the *programmable* slot of the single resident
Telegram Task Card (Jason #7258/#7259). Telegram never executes code: the agent
writes a Python renderer file under its own workdir whose stdout is exactly one
schema-valid Task Card JSON object; the controller runs it with the runtime
interpreter under a strict timeout, validates the JSON, and forwards only
validated data to the private ``_lingtai_telegram_task_card`` reverse channel.
``TelegramManager`` stays the single render/compose/persistence owner (no
competing system). Fail-loud is the kernel notification wake
(``_enqueue_system_notification``); all model-facing copy is English-only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base_agent import BaseAgent

# Private, unlisted Telegram reverse-channel tool name — identical to
# ``base_agent._TASK_CARD_TOOL`` and ``telegram/server.py:_PRIVATE_TASK_CARD_TOOL``.
_TASK_CARD_TOOL = "_lingtai_telegram_task_card"
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_INTERVAL_S = 5.0
_MIN_INTERVAL_S = 1.0
_REVERSE_CALL_TIMEOUT_S = 5.0
# Watcher-thread join budget on stop/shutdown. A tick blocked in the reverse
# call can take up to ``_REVERSE_CALL_TIMEOUT_S``; join must exceed that (plus a
# small margin) to be a truthful join rather than a premature timeout.
_JOIN_TIMEOUT_S = _REVERSE_CALL_TIMEOUT_S + 1.0
_MAX_LINES = 20  # bound so a runaway renderer cannot flood the card


def get_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "inspect", "retry", "stop"],
                "description": (
                    "start: validate + run a renderer file once, then watch it and "
                    "project its output onto the resident Task Card; returns a "
                    "watch_id. inspect: report status + last frame. retry: re-run a "
                    "failed watch now. stop: end a watch and clear its programmable "
                    "frame (renderer files are never deleted)."
                ),
            },
            "renderer_path": {
                "type": "string",
                "description": (
                    "start only. Python renderer file inside the agent working "
                    "directory; its stdout MUST be exactly one Task Card JSON object "
                    "with any of title (string), lines (array of strings), footer "
                    "(string)."
                ),
            },
            "interval_s": {
                "type": "number",
                "description": "start only. Seconds between runs (min 1).",
            },
            "timeout_s": {
                "type": "number",
                "description": "start only. Per-run renderer timeout.",
            },
            "watch_id": {
                "type": "string",
                "description": "inspect/retry/stop: the watch_id from start.",
            },
        },
        "required": ["action"],
    }


def get_description() -> str:
    return (
        "Control a programmable Task Card watch. Provide a Python renderer file "
        "under your working directory that prints exactly one Task Card JSON object "
        "to stdout; the controller runs it on an interval and projects the output "
        "onto the resident Task Card's programmable channel, alongside the automatic "
        "tool-activity channel. Actions: start, inspect, retry, stop."
    )


class TaskCardControllerError(Exception):
    """Synchronous, user-visible controller error (invalid action/config/path)."""


class _Watch:
    """In-memory state for one programmable Task Card watch."""

    __slots__ = (
        "watch_id",
        "renderer_path",
        "interval_s",
        "timeout_s",
        "account",
        "chat_id",
        "thread",
        "stop_event",
        "lock",
        "last_valid_frame",
        "last_valid_at",
        "error",
        "error_key",
        "error_epoch",
    )

    def __init__(
        self, watch_id, renderer_path, interval_s, timeout_s, account, chat_id
    ) -> None:
        self.watch_id = watch_id
        self.renderer_path = renderer_path
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self.account = account
        self.chat_id = chat_id
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.last_valid_frame: dict | None = None
        self.last_valid_at: str | None = None
        # Current error (or None when healthy); ``error_key`` is the dedup identity
        # of the last-emitted failure so identical repeats stay silent.
        self.error: dict | None = None
        self.error_key: str | None = None
        # Monotonic failure-episode counter, bumped on each healthy→error
        # transition. It scopes the durable LICC wake's idempotency key so that,
        # after a recovery, the SAME failure code re-fires a fresh wake instead of
        # being suppressed by the still-stored notification from a prior episode.
        self.error_epoch: int = 0


class TaskCardController:
    """Public controller for programmable Task Card watches (thin Core)."""

    def __init__(self, agent: "BaseAgent") -> None:
        self._agent = agent
        self._watches: dict[str, _Watch] = {}
        self._lock = threading.RLock()
        self._counter = 0

    def handle(self, args: dict) -> dict:
        action = args.get("action")
        try:
            if action == "start":
                return self._start(args)
            if action == "inspect":
                return self._inspect(self._require_watch(args.get("watch_id")))
            if action == "retry":
                watch = self._require_watch(args.get("watch_id"))
                self._tick(watch)
                return self._inspect(watch)
            if action == "stop":
                return self._stop(self._require_watch(args.get("watch_id")))
        except TaskCardControllerError as e:
            return {"status": "error", "message": str(e)}
        return {"status": "error", "message": f"Unknown action: {action!r}"}

    # -- actions -----------------------------------------------------------

    def _start(self, args: dict) -> dict:
        renderer_path = self._validate_renderer_path(args.get("renderer_path"))
        interval_s = self._coerce_positive(
            args.get("interval_s", _DEFAULT_INTERVAL_S), "interval_s", _MIN_INTERVAL_S
        )
        timeout_s = self._coerce_positive(
            args.get("timeout_s", _DEFAULT_TIMEOUT_S), "timeout_s", 0.1
        )
        account, chat_id = self._resolve_route()
        # First frame is synchronous: renderer/JSON/schema failure is an immediate
        # tool error and NO watch handle is created.
        frame = self._run_renderer(renderer_path, timeout_s)
        with self._lock:
            self._counter += 1
            watch_id = f"tc_{self._counter}"
            watch = _Watch(
                watch_id, renderer_path, interval_s, timeout_s, account, chat_id
            )
            self._watches[watch_id] = watch
        # Project the first valid frame; a reverse-call failure here is synchronous
        # and the (unstarted) watch is discarded — no bogus handle is returned.
        if self._project(watch, "create", frame).get("status") == "error":
            with self._lock:
                self._watches.pop(watch_id, None)
            raise TaskCardControllerError(
                "task card backend rejected the first frame; watch not started"
            )
        with watch.lock:
            watch.last_valid_frame = frame
        self._spawn(watch)
        return {"status": "ok", "watch_id": watch_id, "state": "watching"}

    def _inspect(self, watch: _Watch) -> dict:
        with watch.lock:
            return {
                "status": "ok",
                "watch_id": watch.watch_id,
                "state": "error" if watch.error else "watching",
                "last_valid_frame": watch.last_valid_frame,
                "last_valid_frame_at": watch.last_valid_at,
                "error": watch.error,
            }

    def _stop(self, watch: _Watch) -> dict:
        watch.stop_event.set()
        if watch.thread is not None and watch.thread.is_alive():
            watch.thread.join(timeout=_JOIN_TIMEOUT_S)
        # Clear only the programmable channel; the automatic channel is untouched.
        # Renderer files are never deleted.
        self._project(watch, "finalize", None)
        with self._lock:
            self._watches.pop(watch.watch_id, None)
        return {"status": "ok", "watch_id": watch.watch_id, "state": "stopped"}

    # -- watcher loop ------------------------------------------------------

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
                return  # interruptible: stop ends the watch promptly
            if shutdown is not None and shutdown.is_set():
                return
            self._tick(watch)

    def _tick(self, watch: _Watch) -> None:
        """Run the renderer once and reconcile the watch's error/frame state."""
        try:
            frame = self._run_renderer(watch.renderer_path, watch.timeout_s)
        except TaskCardControllerError as e:
            self._mark_error(watch, self._error_from_exc(e))
            return
        if self._project(watch, "update", frame).get("status") == "error":
            self._mark_error(
                watch,
                {
                    "code": "backend_edit_failed",
                    "retryable": True,
                    "message": "task card backend rejected the frame",
                },
            )
            return
        self._mark_recovered(watch, frame)

    # -- fail-loud / recovery transitions ----------------------------------

    def _mark_error(self, watch: _Watch, error: dict) -> None:
        key = str(error.get("code"))
        with watch.lock:
            # A fresh failure episode (healthy→error) advances the epoch so its
            # durable wake key is distinct from any prior episode's stored wake.
            if watch.error is None:
                watch.error_epoch += 1
            watch.error = error
            already = watch.error_key == key
            watch.error_key = key
            epoch = watch.error_epoch
            last_valid_at = watch.last_valid_at
        if already:
            return  # dedupe identical repeated failure state within this episode
        self._emit_event(watch, error, last_valid_at, epoch=epoch, recovered=False)

    def _mark_recovered(self, watch: _Watch, frame: dict) -> None:
        with watch.lock:
            was_errored = watch.error is not None
            epoch = watch.error_epoch
            watch.error = None
            watch.error_key = None
            watch.last_valid_frame = frame
        if was_errored:
            self._emit_event(watch, None, None, epoch=epoch, recovered=True)

    def _emit_event(
        self,
        watch: _Watch,
        error: dict | None,
        last_valid_at: str | None,
        *,
        epoch: int,
        recovered: bool,
    ) -> None:
        """Emit a structured, deduped ``task_card.error`` wake — only a stable
        code/message and safe watch metadata, never renderer output or secrets.

        The idempotency key is scoped by ``epoch`` (the failure-episode counter)
        so identical failures inside one episode dedupe, while the same code after
        a recovery re-fires a new durable wake rather than being swallowed by the
        prior episode's still-stored notification."""
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
            body = (
                f"Task Card watch {watch.watch_id} failed: "
                f"{(error or {}).get('message', code)}"
            )
            extra = {
                "watch_id": watch.watch_id,
                "state": "error",
                "code": code,
                "retryable": (error or {}).get("retryable", "unknown"),
            }
            if last_valid_at:
                extra["last_valid_frame_at"] = last_valid_at
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
            pass  # wake is best-effort; never raise into a watcher thread

    # -- renderer execution + validation -----------------------------------

    def _run_renderer(self, path: Path, timeout_s: float) -> dict:
        """Execute the renderer; return the single validated frame. Raises
        :class:`TaskCardControllerError` on nonzero exit, timeout, or stdout that
        is not exactly one schema-valid JSON object. Raw renderer output is never
        echoed into the raised message (redaction by construction)."""
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self._agent._working_dir),
            )
        except subprocess.TimeoutExpired as e:
            raise TaskCardControllerError(
                f"renderer timed out after {timeout_s}s"
            ) from e
        except OSError as e:
            raise TaskCardControllerError("renderer could not be executed") from e
        if proc.returncode != 0:
            raise TaskCardControllerError(
                f"renderer exited with status {proc.returncode}"
            )
        return self._validate_frame(proc.stdout)

    @staticmethod
    def _validate_frame(stdout: str) -> dict:
        text = stdout.strip()
        if not text:
            raise TaskCardControllerError("renderer produced no output")
        try:
            obj = json.loads(text)  # rejects multiple concatenated objects too
        except json.JSONDecodeError as e:
            raise TaskCardControllerError(
                "renderer stdout must be exactly one JSON object"
            ) from e
        if not isinstance(obj, dict):
            raise TaskCardControllerError("renderer JSON must be a Task Card object")
        title, footer, lines = obj.get("title"), obj.get("footer"), obj.get("lines", [])
        if title is not None and not isinstance(title, str):
            raise TaskCardControllerError("title must be a string")
        if footer is not None and not isinstance(footer, str):
            raise TaskCardControllerError("footer must be a string")
        if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
            raise TaskCardControllerError("lines must be an array of strings")
        if len(lines) > _MAX_LINES:
            raise TaskCardControllerError(f"lines must be at most {_MAX_LINES}")
        if not (title or footer or lines):
            raise TaskCardControllerError(
                "Task Card object must have a title, lines, or footer"
            )
        card: dict[str, Any] = {"lines": lines}
        if title is not None:
            card["title"] = title
        if footer is not None:
            card["footer"] = footer
        return card

    @staticmethod
    def _error_from_exc(exc: TaskCardControllerError) -> dict:
        msg = str(exc)
        if "timed out" in msg:
            code = "renderer_timeout"
        elif "status" in msg:
            code = "renderer_nonzero_exit"
        elif any(w in msg for w in ("JSON", "object", "string", "array")):
            code = "invalid_frame"
        else:
            code = "renderer_failed"
        return {"code": code, "message": msg, "retryable": True}

    # -- reverse call to the private Telegram programmable channel ----------

    def _project(self, watch: _Watch, sub_action: str, frame: dict | None) -> dict:
        """Forward validated data to the programmable channel; never raises."""
        client = getattr(self._agent, "_mcp_clients_by_tool", {}).get("telegram")
        if client is None:
            return {"status": "error"}
        payload: dict[str, Any] = {
            "sub_action": sub_action,
            "channel": "programmable",
            "account": watch.account,
            "chat_id": watch.chat_id,
        }
        if frame is not None:
            payload["card"] = frame
        try:
            result = client.call_tool(
                _TASK_CARD_TOOL, payload, timeout=_REVERSE_CALL_TIMEOUT_S
            )
        except Exception:
            return {"status": "error"}
        if not isinstance(result, dict) or result.get("status") == "error":
            return {"status": "error"}
        partial = {
            flag: True
            for flag in ("resident_persist_failed", "stale_delete_failed")
            if result.get(flag) is True
        }
        if partial:
            return {"status": "error", "partial": True, **partial}
        return {"status": "ok"}

    # -- validation + route helpers ----------------------------------------

    def _validate_renderer_path(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise TaskCardControllerError("renderer_path is required for start")
        workdir = Path(self._agent._working_dir)
        candidate = raw.strip()
        try:
            wd = workdir.resolve()
            joined = (
                Path(candidate)
                if Path(candidate).is_absolute()
                else (workdir / candidate)
            )
            resolved = joined.resolve()
        except (OSError, RuntimeError, ValueError) as e:
            raise TaskCardControllerError(
                f"renderer_path could not be resolved ({e})"
            ) from e
        try:
            resolved.relative_to(wd)
        except ValueError:
            raise TaskCardControllerError(
                "renderer_path must be inside the agent working directory "
                "(no path traversal, no absolute escape)"
            )
        if not resolved.is_file():
            raise TaskCardControllerError(
                "renderer_path must be an existing regular file"
            )
        return resolved

    @staticmethod
    def _coerce_positive(value: Any, name: str, minimum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TaskCardControllerError(f"{name} must be a number")
        v = float(value)
        if v < minimum:
            raise TaskCardControllerError(f"{name} must be at least {minimum}")
        return v

    def _resolve_route(self) -> tuple[str, int]:
        """Resolve the current Telegram (account, chat_id) from the automatic
        driver's turn-local route, so both slots share one resident message."""
        ctx = getattr(self._agent, "_telegram_task_card_context", None)
        if (
            isinstance(ctx, dict)
            and ctx.get("account")
            and ctx.get("chat_id") is not None
        ):
            return str(ctx["account"]), int(ctx["chat_id"])
        raise TaskCardControllerError(
            "no active Telegram chat to attach a Task Card to"
        )

    def _require_watch(self, watch_id: Any) -> _Watch:
        if not isinstance(watch_id, str):
            raise TaskCardControllerError("watch_id is required")
        with self._lock:
            watch = self._watches.get(watch_id)
        if watch is None:
            raise TaskCardControllerError(f"unknown watch_id: {watch_id}")
        return watch

    # -- lifecycle ----------------------------------------------------------

    def shutdown_for_agent_stop(self, *, reason: str = "") -> None:
        """Stop all watcher threads without any filesystem deletion."""
        with self._lock:
            watches = list(self._watches.values())
            self._watches.clear()
        for watch in watches:
            watch.stop_event.set()
            if watch.thread is not None and watch.thread.is_alive():
                watch.thread.join(timeout=_JOIN_TIMEOUT_S)


def setup(agent: "BaseAgent") -> TaskCardController:
    """Register the public ``task_card`` controller tool on *agent*.

    Kernel-owned (drives the kernel-owned Telegram reverse channel), so it adds
    no ``lingtai.tools`` package and no glossary obligation (``glossary_package=None``).
    """
    mgr = TaskCardController(agent)
    agent.add_tool(
        "task_card",
        schema=get_schema(),
        handler=mgr.handle,
        description=get_description(),
        glossary_package=None,
    )
    return mgr
