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

This unit is Telegram MCP-owned: registration is gated by the Telegram reverse
route, projection targets ``_lingtai_telegram_task_card``, and the Telegram
manager/server/service own the resident slots, in-place edits, toggle,
persistence, transport, and rendering destination. The controller depends only
on the narrow :class:`~.interface.TelegramTaskCardAgent` host surface, never on
the concrete ``Agent`` class. See the co-located ``CONTRACT.md`` for the
interface promise and ``SKILL.md`` for the model-facing manual.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .interface import TelegramTaskCardAgent


def _utc_now_iso() -> str:
    """Return the current instant as a UTC ISO-8601 string (``...+00:00``).

    Timezone-aware and documented: the value stamped on every accepted frame and
    surfaced as ``inspect``'s ``last_valid_frame_at``. Kept as a module function
    so tests can substitute a deterministic clock.
    """
    return datetime.now(timezone.utc).isoformat()

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
        "Control a programmable Telegram Task Card watch. Provide a Python renderer "
        "file under your working directory that prints exactly one Task Card JSON "
        "object to stdout; the controller runs it on an interval and projects the "
        "output onto the resident Task Card's programmable channel, alongside the "
        "automatic tool-activity channel. Actions: start, inspect, retry, stop. "
        "Full manual (renderer contract, a safe runnable example, and a "
        "start|inspect|retry|stop walkthrough): call telegram(action='manual') and "
        "follow its 'Programmable Task Card' section to the co-located task_card "
        "manual (task_card/SKILL.md)."
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
        "stopping",
        "finalized",
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
        # UTC ISO-8601 timestamp of the last accepted frame (initial or later);
        # unchanged across failed renderer/backend attempts.
        self.last_valid_at: str | None = None
        # Set once ``stop`` is requested. Kept sticky so ``inspect`` never reports
        # ``watching`` after a stop request; a successful stop removes the watch
        # entirely. NOTE: a retained ``stop_thread_alive`` state still has a LIVE
        # watcher thread — an update authorized just before stop may still be in
        # flight — so ``stopping`` does NOT imply the renderer thread is stopped.
        self.stopping: bool = False
        # True once the programmable slot has been cleared (a ``finalize`` was
        # accepted), whether by the public ``_stop`` path or by the watcher thread
        # compensating a late in-flight update. It is the stop/compensation
        # handshake: a later public stop/retry removes the watch WITHOUT a second
        # reverse clear once this is set.
        self.finalized: bool = False
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

    def __init__(self, agent: TelegramTaskCardAgent) -> None:
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
                # Once stop has been requested, ``retry`` continues the stop path
                # only (re-check quiescence, then retry finalize). It must NEVER
                # run the renderer again or project a fresh update, so a stopped
                # watch cannot be resurrected into a visible frame.
                if watch.stopping:
                    return self._stop(watch)
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
            watch.last_valid_at = _utc_now_iso()
        self._spawn(watch)
        return {"status": "ok", "watch_id": watch_id, "state": "watching"}

    def _inspect(self, watch: _Watch) -> dict:
        with watch.lock:
            # After a stop request the watch must never read back as ``watching``
            # (even while a ``stop_thread_alive`` handle still has a live watcher
            # thread); it exposes a truthful, retryable state instead.
            if watch.stopping:
                state = "stop_failed" if watch.error else "stopping"
            elif watch.error:
                state = "error"
            else:
                state = "watching"
            return {
                "status": "ok",
                "watch_id": watch.watch_id,
                "state": state,
                "last_valid_frame": watch.last_valid_frame,
                "last_valid_frame_at": watch.last_valid_at,
                "error": watch.error,
            }

    def _stop(self, watch: _Watch) -> dict:
        """Advance the stop lifecycle and finalize exactly once when quiescent.

        Idempotent and retryable: both the ``stop`` action and ``retry`` on an
        already-stopping watch route here. Invariants:

        - The watch is never finalized, removed, or reported ``stopped`` while its
          watcher thread is still alive. An update authorized just before stop may
          still be in flight (the reverse ``call_tool`` has no total-time bound
          because a stale-resource restart+retry can exceed the per-attempt
          timeout), so waiting for the thread is what makes the clear/removal safe.
        - The programmable clear happens exactly once per stop attempt. If the
          watcher thread already compensated an in-flight update by finalizing
          (``watch.finalized``), this path removes the watch WITHOUT a second
          reverse clear; the watcher and public stop never finalize concurrently
          because public stop refuses to finalize while the thread is alive.
        """
        # Mark stopping BEFORE setting the event so a tick that observes the event
        # (see ``_stop_requested``) also observes the explicit-stop intent and can
        # compensate a landed update.
        with watch.lock:
            watch.stopping = True
        watch.stop_event.set()
        thread = watch.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_JOIN_TIMEOUT_S)
        # If the thread has not gone quiescent within the join budget (e.g. a
        # renderer, or an in-flight update projection, running past it), do NOT
        # finalize/remove/report stopped. Keep a truthful, retryable handle so stop
        # can be retried once it is quiescent; the live thread compensates any
        # landed update itself.
        if thread is not None and thread.is_alive():
            error = {
                "code": "stop_thread_alive",
                "retryable": True,
                "message": (
                    "the watcher thread has not stopped yet (renderer or an "
                    "in-flight update still running); retry stop once quiescent"
                ),
            }
            with watch.lock:
                if not watch.finalized:
                    watch.error = error
            return {
                "status": "error",
                "watch_id": watch.watch_id,
                "state": "stop_failed",
                "error": error,
            }
        # Quiescent. If the watcher thread already cleared the programmable slot
        # (compensating a late update), there is nothing more to send: just remove.
        with watch.lock:
            already_cleared = watch.finalized
        if already_cleared:
            with self._lock:
                self._watches.pop(watch.watch_id, None)
            return {"status": "ok", "watch_id": watch.watch_id, "state": "stopped"}
        # Otherwise clear only the programmable channel now (the automatic channel
        # is untouched and renderer files are never deleted). Commit the watch
        # removal ONLY after the backend durably accepts the clear — the manager
        # preserves the old programmable frame on a transient/unknown edit failure,
        # so reporting ``stopped`` and dropping the handle would strand a visible
        # frame with no way to retry.
        if self._project(watch, "finalize", None).get("status") == "error":
            error = {
                "code": "stop_finalize_failed",
                "retryable": True,
                "message": (
                    "task card backend rejected the stop/finalize; the watch is "
                    "retained so stop can be retried"
                ),
            }
            with watch.lock:
                if not watch.finalized:
                    watch.error = error
            return {
                "status": "error",
                "watch_id": watch.watch_id,
                "state": "stop_failed",
                "error": error,
            }
        with watch.lock:
            watch.finalized = True
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

    def _stop_requested(self, watch: _Watch) -> bool:
        """True once stop or agent shutdown was requested for this watch.

        Both the blocking renderer and the (restart-retryable, not time-bounded)
        ``update`` reverse call can straddle a stop. ``_tick`` checks this twice —
        before projecting (drop; nothing was sent) and after projecting (drop the
        state mutation: no ``update`` follow-through, no last-valid overwrite, no
        stop-error clear). Checking ``stop_event`` (set by
        ``_stop``/``shutdown_for_agent_stop``) makes the request observable to the
        watcher thread.
        """
        if watch.stop_event.is_set():
            return True
        shutdown = getattr(self._agent, "_shutdown", None)
        return bool(shutdown is not None and shutdown.is_set())

    def _tick(self, watch: _Watch) -> None:
        """Run the renderer once and reconcile the watch's error/frame state.

        Stop can straddle either blocking step. If it was requested while the
        renderer ran, the tick is dropped before projecting (nothing was sent). If
        it arrived while the ``update`` projection was in flight, the tick drops
        its state mutation (no last-valid overwrite, no stop-error clear) and — for
        an explicit stop — compensates by clearing the slot, since that update may
        already have landed."""
        try:
            frame = self._run_renderer(watch.renderer_path, watch.timeout_s)
        except TaskCardControllerError as e:
            if self._stop_requested(watch):
                return  # stop observed: drop this late failure, keep stop state
            self._mark_error(watch, self._error_from_exc(e))
            return
        if self._stop_requested(watch):
            # Stop/shutdown observed BEFORE projecting: drop. Nothing was sent, so
            # there is no late frame to compensate — the public stop clears the
            # slot's prior frame once this (about-to-exit) thread is quiescent.
            return
        result = self._project(watch, "update", frame)
        if self._stop_requested(watch):
            # Stop/shutdown arrived DURING the update projection. Because the
            # reverse call has no total-time bound (a stale-resource restart+retry
            # can exceed the per-attempt timeout), the update may already have
            # landed. Drop the tick's normal state mutation — no
            # ``_mark_recovered``/``_mark_error``, hence no last-valid overwrite and
            # no stop-error clear. For an EXPLICIT stop (not a shutdown, which keeps
            # its intentional no-finalize policy) the live watcher thread
            # compensates by clearing the slot so the late frame cannot linger.
            if watch.stopping and not self._shutdown_requested():
                self._compensate_stop_finalize(watch)
            return
        if result.get("status") == "error":
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

    def _shutdown_requested(self) -> bool:
        """True once the agent's global shutdown was requested (no-finalize path)."""
        shutdown = getattr(self._agent, "_shutdown", None)
        return bool(shutdown is not None and shutdown.is_set())

    def _compensate_stop_finalize(self, watch: _Watch) -> None:
        """Watcher-thread compensation for an update that landed after stop.

        Runs on the live watcher thread when an explicit stop was observed after
        an ``update`` projection. Public ``_stop`` deferred the programmable clear
        because this thread was mid-projection, so clear it here. The reverse call
        runs with NO lock held; the outcome is recorded under the watch lock so a
        later public stop/retry can remove the watch without a second reverse clear
        (accepted) or re-attempt the clear (failed). No duplicate/concurrent
        finalize is possible: public stop/retry refuse to finalize while this
        thread is alive."""
        accepted = self._project(watch, "finalize", None).get("status") != "error"
        with watch.lock:
            if accepted:
                watch.finalized = True
                # Keep a truthful, retryable error (never clear it to None, which
                # would misreport a non-error ``stopping`` state) so ``inspect``
                # reports ``stop_failed`` until a retry removes the handle.
                watch.error = {
                    "code": "stop_thread_alive",
                    "retryable": True,
                    "message": (
                        "stop requested; the in-flight update was cleared and the "
                        "programmable slot finalized — retry stop to remove the watch"
                    ),
                }
            elif not watch.finalized:
                watch.error = {
                    "code": "stop_finalize_failed",
                    "retryable": True,
                    "message": (
                        "stop requested; clearing the in-flight update failed — "
                        "retry stop to re-attempt the clear"
                    ),
                }

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
            watch.last_valid_at = _utc_now_iso()
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


def setup(agent: TelegramTaskCardAgent) -> TaskCardController:
    """Register the public ``task_card`` controller tool on *agent*.

    Telegram MCP-owned (it drives the Telegram-owned reverse channel), so it adds
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
