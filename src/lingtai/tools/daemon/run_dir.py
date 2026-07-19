"""Per-emanation filesystem run directory.

Each daemon emanation gets one DaemonRunDir, which owns every filesystem
effect for that run: folder layout, daemon.json atomic writes, JSONL appends,
heartbeat touches, terminal state markers. The DaemonManager calls into a
DaemonRunDir at every hook (start, per-turn, per-tool-dispatch, terminal)
without itself touching the filesystem.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from lingtai.adapters.posix.process_identity import (
    process_identity,
    process_identity_matches,
)
from lingtai.kernel._fsutil import append_jsonl, atomic_write_json
from lingtai.kernel.token_ledger import (
    append_token_entry,
    safe_codex_pool_usage_extra,
)


@contextmanager
def _exclusive_state_lock(lock_path: Path):
    """Hold the cross-process exclusive lock on one ``.daemon-state.lock`` file.

    Module-internal platform seam shared by ``DaemonRunDir.state_file_lock``
    and ``DaemonRunDir._state_transaction`` — the only two daemon-state lock
    entry points. POSIX keeps the historical blocking ``fcntl.flock(LOCK_EX)``
    on a text-mode ``"a+"`` handle, byte-for-byte unchanged. Windows uses the
    ``msvcrt`` byte-range mechanism below. Any other platform fails loudly at
    the ``fcntl`` import rather than pretending to serialize.
    """
    if os.name == "nt":
        with _windows_exclusive_state_lock(lock_path):
            yield
        return
    import fcntl

    lock_path.touch(mode=0o600, exist_ok=True)
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def _windows_exclusive_state_lock(lock_path: Path):
    """Windows daemon-state lock: ``msvcrt`` byte 0 / length 1, retried.

    ``LK_NBLCK`` in an explicit sleep loop, never ``LK_LOCK`` — the CRT's
    blocking mode retries ~10 times at ~1s intervals and then *fails*, a
    hidden bounded timeout where this transaction needs indefinite blocking
    semantics (same rationale as
    ``adapters/windows/powershell_state_lock.py``). The single seeded byte and
    the ``seek(0)`` before every lock/unlock match the workdir-lease adapter's
    ``msvcrt`` discipline; the OS releases the byte range when a holder dies,
    so a crashed writer never wedges the file.
    """
    import msvcrt

    with open(lock_path, "a+b") as lock:
        lock.seek(0)
        if lock.tell() == 0 and lock_path.stat().st_size == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        while True:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.01)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


class DaemonRunDir:
    """Filesystem-backed mini-avatar log surface for one daemon emanation.

    Folder layout for new manager-created runs:
        <parent>/daemons/em-<hash4>[-<collision-suffix>]/

    Direct callers that omit ``run_id`` retain the legacy timestamp/hash folder
    form for compatibility with older tests and integrations:
        <parent>/daemons/<handle>-<YYYYMMDD-HHMMSS>-<hash6>/
            daemon.json                  # identity card + live status
            .prompt                      # system prompt verbatim
            .heartbeat                   # mtime-touched on activity
            history/chat_history.jsonl   # session transcript
            logs/token_ledger.jsonl      # per-call tokens, daemon-scoped
            logs/events.jsonl            # tool_call, tool_result, cli_output, cli_usage, daemon_*
            result.txt                   # full terminal result when available
    """

    DATA_VERSION = 1
    PENDING_LAUNCH_LEASE_S = 5.0
    _TERMINAL_STATES = frozenset({"done", "failed", "cancelled", "timeout"})

    def __init__(
        self,
        *,
        parent_working_dir: Path,
        handle: str,
        task: str,
        tools: list[str],
        model: str,
        max_turns: int,
        timeout_s: float,
        parent_addr: str,
        parent_pid: int,
        system_prompt: str,
        log_callback=None,
        run_id: str | None = None,
        preset_name: str | None = None,
        preset_provider: str | None = None,
        preset_model: str | None = None,
        backend: str = "lingtai",
        group_id: str | None = None,
        call_parameters: dict | None = None,
    ):
        self._handle = handle
        self._parent_token_ledger = parent_working_dir / "logs" / "token_ledger.jsonl"
        # Optional callback for swallowed OSError visibility — invoked as
        # log_callback("daemon_fs_error", op=<op_name>, error=<str(exc)>).
        # When None, _safe stays silent (preserves prior behavior for tests).
        self._log_callback = log_callback
        self._started_monotonic = time.monotonic()
        # Every live writer for this run shares one re-entrant transaction lock.
        # It covers the in-memory mutation, daemon.json replacement, and any
        # paired event/heartbeat write; per-run ownership avoids serializing
        # unrelated daemon runs. Terminal-notification methods acquire their
        # separate guard before this lock; RLock lets owner helpers compose
        # without reversing that lock order.
        self._state_writer_lock = threading.RLock()
        self._terminal_notification_lock = threading.Lock()
        self._ephemeral_redactions: tuple[str, ...] = ()
        started_at_iso = self._now_iso()

        # New daemon-manager callers pass a compact id as ``run_id`` so the
        # user-facing id and folder name are identical. If omitted, keep the
        # legacy long folder form for direct callers and old tests.
        if run_id is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            hash6 = secrets.token_hex(3)
            run_id = f"{handle}-{timestamp}-{hash6}"
        self._run_id = run_id

        self._path = parent_working_dir / "daemons" / self._run_id

        # Identity-card construction is strict — failures here propagate up to
        # _handle_emanate which converts them into a tool-level error response.
        self._path.mkdir(parents=True, exist_ok=False)
        (self._path / "history").mkdir()
        (self._path / "logs").mkdir()

        self._state = {
            "data_version": self.DATA_VERSION,
            "handle": handle,
            "run_id": self._run_id,
            "group_id": group_id,
            "parent_addr": parent_addr,
            "parent_pid": parent_pid,
            "task": task,
            "tools": list(tools),
            "call_parameters": dict(call_parameters or {}),
            "model": model,
            "max_turns": max_turns,
            "timeout_s": timeout_s,
            "state": "running",
            "started_at": started_at_iso,
            "finished_at": None,
            "elapsed_s": 0.0,
            "turn": 0,
            "current_tool": None,
            "tool_call_count": 0,
            "tokens": {"input": 0, "output": 0, "thinking": 0, "cached": 0},
            # CLI-backend usage (claude-p / codex / ...) is accumulated here
            # for UI display only — NOT in `tokens` (which feeds the kernel
            # token ledgers). External CLIs bill on their own provider account
            # with cache semantics that don't map onto the adapter accounting,
            # so this lives separate. `calls` increments once per usage event.
            "cli_tokens": {"input": 0, "output": 0, "thinking": 0,
                           "cached": 0, "calls": 0},
            "result_preview": None,
            "result_path": None,
            "last_output": None,
            "last_output_at": None,
            "error": None,
            "terminal_notified": False,
            "terminal_notification_claim": None,
            "terminal_notification_receipt": None,
            "pending_followups": [],
            "child_pid": None,
            "child_pgid": None,
            "child_start_identity": None,
            "child_registered_at": None,
            "child_history": [],
            "execution_pid": None,
            "execution_pgid": None,
            "execution_start_identity": None,
            "execution_registered_at": None,
            "execution_registration": "pending",
            "resume_generation": 0,
            "resume_claim": None,
            "resume_pid": None,
            "resume_start_identity": None,
            "resume_state": None,
            "followup_status": None,
            "followup_result_path": None,
            "followup_result_preview": None,
            "preset_name": preset_name,
            "preset_provider": preset_provider,
            "preset_model": preset_model,
            "backend": backend,
            "claude_session_id": None,
            # "parent": run_dir is executed in-process by the agent's own
            # DaemonManager pool (legacy path, still true for most backends
            # in this slice). "supervisor": a detached
            # lingtai.kernel.daemon_supervisor process owns execution and
            # terminal truth; parent-owned reaping/shutdown must not touch it.
            # See DaemonManager._reap_dead_parent_daemon_records.
            "owner": "parent",
            "supervisor_pid": None,
        }

        self._atomic_write_json(self.daemon_json_path, self._state)
        self.prompt_path.write_text(system_prompt, encoding="utf-8")
        self.heartbeat_path.touch()
        self._append_jsonl(self.events_path,
                           {"event": "daemon_start", "ts": self._now_iso()})

    @staticmethod
    def read_state_from_disk(path: Path) -> dict:
        """Read the current ``daemon.json`` state fresh from disk.

        Unlike ``state_snapshot()`` (which returns this in-process object's
        own in-memory copy — correct only for the process actually writing
        it), this is the read a caller uses to observe state a DIFFERENT
        process wrote — specifically, the agent-side manager or a fresh
        `DaemonManager` polling a run a detached supervisor process owns.
        Two live `DaemonRunDir` objects for the same run_id (one per
        process) never share memory; disk is the only synchronization point.
        Raises the same errors ``json.loads``/``Path.read_text`` would on a
        missing/corrupt file — callers polling for a field's appearance
        should catch ``(OSError, json.JSONDecodeError)`` around a call taken
        before the file is guaranteed to exist.
        """
        state = json.loads((Path(path) / "daemon.json").read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError(f"daemon.json at {path} is not a JSON object")
        return state

    @classmethod
    def attach(cls, path: Path, *, log_callback=None) -> "DaemonRunDir":
        """Bind a ``DaemonRunDir`` to an existing run folder on disk.

        Unlike ``__init__`` (which creates a fresh folder and identity card),
        this loads the current ``daemon.json`` state from *path* without
        writing anything. Used by the detached daemon supervisor, which
        reconstructs its owning ``DaemonRunDir`` from a run directory the
        agent-side manager already created (folder, ``daemon.json``,
        ``.prompt``) before spawning the supervisor process — attaching must
        not re-create or reset that state.
        """
        path = Path(path)
        daemon_json_path = path / "daemon.json"
        state = json.loads(daemon_json_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError(f"daemon.json at {daemon_json_path} is not a JSON object")
        instance = object.__new__(cls)
        instance._handle = state.get("handle") or path.name
        instance._parent_token_ledger = None  # set below once parent dir is known
        instance._log_callback = log_callback
        instance._started_monotonic = time.monotonic()
        instance._state_writer_lock = threading.RLock()
        instance._terminal_notification_lock = threading.Lock()
        instance._ephemeral_redactions = ()
        instance._run_id = state.get("run_id") or path.name
        instance._path = path
        instance._state = state
        parent_addr = state.get("parent_addr")
        # The parent working dir is the run dir's grandparent
        # (<parent>/daemons/<run_id>), which is how every constructor-created
        # run dir is laid out; recovering it here lets append_tokens continue
        # writing to the same parent ledger the in-process path would have used.
        instance._parent_token_ledger = path.parent.parent / "logs" / "token_ledger.jsonl"
        return instance

    # ------------------------------------------------------------------
    # Path properties
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def handle(self) -> str:
        return self._handle

    @property
    def group_id(self) -> str | None:
        """The daemon batch group id this run belongs to (None if ungrouped)."""
        with self._state_writer_lock:
            return self._state.get("group_id")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def daemon_json_path(self) -> Path:
        return self._path / "daemon.json"

    @property
    def state_lock_path(self) -> Path:
        return self._path / ".daemon-state.lock"

    @classmethod
    @contextmanager
    def state_file_lock(cls, path: Path):
        """Hold the cross-process daemon.json transaction lock for *path*.

        Recovery code that cannot attach a ``DaemonRunDir`` because daemon.json
        is missing or invalid still has to serialize with a live detached owner.
        This narrow boundary exposes the same fixed lock file used by
        ``_state_transaction`` without constructing or mutating an in-memory run.
        """
        with _exclusive_state_lock(Path(path) / ".daemon-state.lock"):
            yield

    @property
    def prompt_path(self) -> Path:
        return self._path / ".prompt"

    @property
    def heartbeat_path(self) -> Path:
        return self._path / ".heartbeat"

    @property
    def chat_path(self) -> Path:
        return self._path / "history" / "chat_history.jsonl"

    @property
    def events_path(self) -> Path:
        return self._path / "logs" / "events.jsonl"

    @property
    def token_ledger_path(self) -> Path:
        return self._path / "logs" / "token_ledger.jsonl"

    @property
    def result_path(self) -> Path:
        return self._path / "result.txt"

    @property
    def manifest_path(self) -> Path:
        return self._path / "artifacts.json"

    def state_snapshot(self) -> dict:
        """Return a shallow copy of the current daemon.json state."""
        with self._state_writer_lock:
            return dict(self._state)

    def update_state(self, **fields) -> None:
        """Persist owner-controlled daemon state fields as one write transaction.

        Callers that need to add backend/session metadata must use this boundary
        rather than mutating ``_state`` and calling ``_atomic_write_json``
        directly. The lock is re-entrant so a caller already in a state
        transaction can safely compose this helper.
        """
        from lingtai.kernel.daemon_supervisor.manifest import redact_durable_value

        with self._state_transaction():
            requested = fields.get("state")
            current = self._state.get("state")
            if current in self._TERMINAL_STATES and requested not in (None, current):
                fields = {key: value for key, value in fields.items() if key != "state"}
            self._state.update({
                key: self._durable_value(redact_durable_value(value, field=key))
                for key, value in fields.items()
            })
            self._atomic_write_json(self.daemon_json_path, self._state)

    @contextmanager
    def _state_transaction(self):
        """Serialize one daemon.json read/modify/write across processes."""
        with self._state_writer_lock:
            with _exclusive_state_lock(self.state_lock_path):
                try:
                    disk = json.loads(self.daemon_json_path.read_text(encoding="utf-8"))
                    if isinstance(disk, dict):
                        self._state = disk
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    pass
                yield

    def set_ephemeral_redactions(self, values) -> None:
        """Install runtime-only literals that must not reach durable output."""
        clean = sorted({str(value) for value in (values or ()) if value}, key=len, reverse=True)
        self._ephemeral_redactions = tuple(clean)

    def _durable_value(self, value):
        if isinstance(value, str):
            for secret in self._ephemeral_redactions:
                value = value.replace(secret, "<redacted>")
            return value
        if isinstance(value, dict):
            return {key: self._durable_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._durable_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._durable_value(item) for item in value]
        return value

    def claim_resume_generation(self) -> dict:
        """Atomically claim the single detached post-terminal writer.

        Claims are append-only generation records.  A fixed advisory lock
        serializes the scan/create transaction; the generation record remains
        after release so a crash is inspectable and never needs destructive
        cleanup.  A live claim is returned as ``busy``.  A dead owner claim is
        marked stale and may be replaced by the next generation.
        """
        claims_dir = self.path / "resume-claims"
        claims_dir.mkdir(exist_ok=True)
        with self._state_transaction():
            active = None
            now = time.time()
            for path in sorted(claims_dir.glob("resume-*.json")):
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                status = row.get("status")
                if status == "pending-launch":
                    if float(row.get("pending_until", 0.0)) > now:
                        active = row
                        break
                    row["status"] = "stale"
                elif status == "running" or status == "active":
                    try:
                        pid = row.get("owner_pid")
                        saved_identity = row.get("owner_start_identity")
                        alive = (
                            isinstance(pid, int)
                            and not isinstance(pid, bool)
                            and isinstance(saved_identity, str)
                            and bool(saved_identity)
                            and process_identity_matches(pid, saved_identity)
                        )
                    except (OSError, ValueError):
                        alive = False
                    if alive or not isinstance(saved_identity, str) or not saved_identity:
                        active = row
                        break
                    row["status"] = "stale"
                if row.get("status") == "stale":
                    row["released_at"] = self._now_iso()
                    self._atomic_write_json(path, row)
            if active is not None:
                return {"status": "busy", "generation": active.get("generation")}
            generation_number = self._state.get("resume_generation", 0) + 1
            generation = f"g{generation_number}-{secrets.token_hex(8)}"
            owner_pid = os.getpid()
            nonce = secrets.token_hex(16)
            claim = {
                "run_id": self._run_id, "generation": generation,
                "status": "pending-launch", "owner_pid": owner_pid,
                "owner_start_identity": process_identity(owner_pid),
                "launch_nonce": nonce, "claimed_at": self._now_iso(),
                "pending_until": now + self.PENDING_LAUNCH_LEASE_S,
            }
            claim_path = claims_dir / f"resume-{generation}.json"
            self._atomic_write_json(claim_path, claim)
            self._state.update({
                "resume_generation": generation_number,
                "resume_claim": claim,
                "resume_state": "claimed",
            })
            self._atomic_write_json(self.daemon_json_path, self._state)
            return {**claim, "status": "running", "launch_status": "pending-launch", "path": str(claim_path)}

    def update_resume_claim(self, generation: str, **fields) -> bool:
        path = self.path / "resume-claims" / f"resume-{generation}.json"
        with self._state_transaction():
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return False
            if not isinstance(row, dict) or row.get("generation") != generation:
                return False
            row.update(fields)
            self._atomic_write_json(path, row)
            self._state["resume_claim"] = self._durable_value(row)
            self._atomic_write_json(self.daemon_json_path, self._state)
            return True

    def activate_resume_generation(self, generation: str, nonce: str) -> bool:
        """Promote only this generation's one-shot launch to its child owner."""
        identity = process_identity(os.getpid())
        if not isinstance(nonce, str) or not nonce or not identity:
            return False
        path = self.path / "resume-claims" / f"resume-{generation}.json"
        with self._state_transaction():
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return False
            if (
                not isinstance(row, dict)
                or row.get("generation") != generation
                or row.get("status") != "pending-launch"
                or row.get("launch_nonce") != nonce
                or float(row.get("pending_until", 0.0)) <= time.time()
            ):
                return False
            row.update({"status": "active", "owner_pid": os.getpid(),
                        "owner_start_identity": identity, "started_at": self._now_iso()})
            self._atomic_write_json(path, row)
            self._state.update({"resume_claim": row, "resume_pid": os.getpid(),
                                "resume_start_identity": identity, "resume_state": "running"})
            self._atomic_write_json(self.daemon_json_path, self._state)
            return True

    def release_resume_generation(self, generation: str, nonce: str, *,
                                  owner_pid: int | None = None,
                                  owner_identity: str | None = None,
                                  result_status: str | None = None) -> bool:
        """Release exactly one generation after validating its owner/nonce."""
        path = self.path / "resume-claims" / f"resume-{generation}.json"
        with self._state_transaction():
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return False
            if not isinstance(row, dict) or row.get("generation") != generation:
                return False
            if row.get("launch_nonce") != nonce:
                return False
            if owner_pid is not None and row.get("owner_pid") != owner_pid:
                return False
            if owner_identity is not None and row.get("owner_start_identity") != owner_identity:
                return False
            if row.get("status") not in {"pending-launch", "active"}:
                return False
            row.update({"status": "released", "released_at": self._now_iso()})
            if result_status is not None:
                row["result_status"] = result_status
            self._atomic_write_json(path, row)
            self._state["resume_claim"] = row
            self._state["resume_state"] = result_status or self._state.get("resume_state")
            self._state["resume_pid"] = None
            self._atomic_write_json(self.daemon_json_path, self._state)
            return True

    def record_followup(self, generation: str, *, status: str,
                        output: str = "", error: str | None = None) -> None:
        """Persist the latest detached follow-up result for ``daemon(check)``."""
        followups = self.path / "followups"
        followups.mkdir(exist_ok=True)
        result_path = followups / f"{generation}.txt"
        text = output if isinstance(output, str) else str(output)
        if error:
            text = error if not text else f"{text}\n{error}"
        text = self._durable_value(text)
        result_path.write_text(text, encoding="utf-8")
        try:
            result_path.chmod(0o600)
        except OSError:
            pass
        preview = text[:500]
        self.update_state(
            followup_status=status,
            followup_result_path=str(result_path),
            followup_result_preview=preview,
            followup_generation=generation,
            followup_error=error,
            resume_state=status,
        )

    def enqueue_followup(self, message: str) -> bool:
        """Persist a follow-up before acknowledging its control request."""
        if not isinstance(message, str) or not message:
            return False
        message = self._durable_value(message)
        with self._state_transaction():
            if self._state.get("state") not in {"running", "active"}:
                return False
            queue = self._state.setdefault("pending_followups", [])
            if not isinstance(queue, list):
                queue = []
                self._state["pending_followups"] = queue
            queue.append(message)
            self._atomic_write_json(self.daemon_json_path, self._state)
            return True

    def drain_followups(self) -> str | None:
        """Atomically consume queued follow-ups at a safe text-only boundary."""
        with self._state_transaction():
            queue = self._state.get("pending_followups")
            if not isinstance(queue, list) or not queue:
                return None
            messages = [item for item in queue if isinstance(item, str) and item]
            self._state["pending_followups"] = []
            self._atomic_write_json(self.daemon_json_path, self._state)
        return "\n\n".join(messages) or None

    def set_session_id(self, key: str, value: str, *, overwrite: bool = True) -> bool:
        """Persist a backend resume id into daemon.json under *key*.

        Returns ``True`` only when the value was actually written (so callers
        can log a session event exactly once on a real change). Specifically:

        - returns ``False`` for an empty *value*;
        - returns ``False`` when the stored value already equals *value*
          (no redundant rewrite/log);
        - when *overwrite* is ``False`` and a truthy value is already stored,
          returns ``False`` and keeps the first id (OpenCode-family backends
          establish the resume id from the first session-shaped header and must
          not let later event ids clobber it);
        - otherwise stores the value, atomically rewrites daemon.json, and
          returns ``True``.

        Write failures are intentionally NOT swallowed: ``_atomic_write_json``
        may raise, matching the previous inline behavior where a failed
        session-id write fails the run.
        """
        if not value:
            return False
        with self._state_transaction():
            if self._state.get(key) == value:
                return False
            if not overwrite and self._state.get(key):
                return False
            self._state[key] = value
            self._atomic_write_json(self.daemon_json_path, self._state)
            return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def new_group_id() -> str:
        """Return a daemon batch group id shared by one emanate call."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"dg-{timestamp}-{secrets.token_hex(3)}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def terminal_notification_idempotency_key(run_id: str) -> str:
        return f"daemon-terminal:{run_id}"

    def _now_secs(self) -> float:
        return round(time.monotonic() - self._started_monotonic, 3)

    def _atomic_write_json(self, path: Path, data: dict) -> None:
        """Write JSON atomically — readers never see partial state."""
        atomic_write_json(path, data, ensure_ascii=False, indent=2)

    def _append_jsonl(self, path: Path, entry: dict) -> None:
        """Append one JSON line."""
        append_jsonl(path, self._durable_value(entry), ensure_ascii=False)

    def append_event(self, event: str, **fields) -> None:
        """Append a redacted durable event from detached execution code."""
        from lingtai.kernel.daemon_supervisor.manifest import redact_durable_event_fields

        safe = redact_durable_event_fields(fields)
        safe = self._durable_value(safe)
        safe["event"] = event
        safe.setdefault("ts", self._now_iso())
        self._safe("append_event", lambda: self._append_jsonl(self.events_path, safe))

    def _safe(self, op: str, fn) -> None:
        """Run `fn`; swallow OSError (best-effort policy for mutation writes).

        If a log_callback was provided at construction, the swallowed error is
        forwarded so the parent agent can record it without breaking the run.
        """
        try:
            fn()
        except OSError as e:
            if self._log_callback is not None:
                try:
                    self._log_callback(
                        "daemon_fs_error",
                        em_id=self._handle,
                        run_id=self._run_id,
                        op=op,
                        error=str(e),
                    )
                except Exception:
                    # Logging itself must never break the run — secondary
                    # failure is silent by design.
                    pass

    def _safe_state(self, op: str, fn) -> None:
        """Run a best-effort state/event transaction under the run writer lock."""
        with self._state_transaction():
            self._safe(op, fn)

    # ------------------------------------------------------------------
    # Per-turn hooks
    # ------------------------------------------------------------------

    def record_user_send(self, text: str, kind: str) -> None:
        """Append a user-role entry to chat_history.jsonl before session.send.

        kind ∈ {"task", "tool_results", "followup"}. Tool result payloads are
        written verbatim — no truncation. Chat history is forensic; we want
        full fidelity. Single-writer per file (only the run thread).
        """
        def _write():
            self._append_jsonl(
                self.chat_path,
                {
                    "role": "user",
                    "text": text,
                    "kind": kind,
                    "turn": self._state["turn"],
                    "ts": self._now_iso(),
                },
            )
        self._safe("record_user_send", _write)

    def bump_turn(self, turn: int, response_text: str) -> None:
        """Mark the end of an LLM round.

        Updates daemon.json (turn, elapsed_s, current_tool=null) atomically,
        appends an assistant entry to chat_history, touches heartbeat.
        """
        def _write():
            self._state["turn"] = turn
            self._state["current_tool"] = None
            self._state["elapsed_s"] = self._now_secs()
            self._atomic_write_json(self.daemon_json_path, self._state)
            self._append_jsonl(
                self.chat_path,
                {
                    "role": "assistant",
                    "text": response_text,
                    "turn": turn,
                    "ts": self._now_iso(),
                },
            )
            self.heartbeat_path.touch()
        self._safe_state("bump_turn", _write)

    # ------------------------------------------------------------------
    # Tool dispatch hooks
    # ------------------------------------------------------------------

    _ARGS_PREVIEW_MAX = 500

    def set_current_tool(self, name: str, args: dict) -> None:
        """Mark a tool dispatch starting.

        Increments tool_call_count, sets current_tool, logs tool_call event,
        touches heartbeat. Tracked tool name (current_tool) is what the parent
        sees on a `cat daemon.json` poll.
        """
        def _write():
            self._state["current_tool"] = name
            self._state["tool_call_count"] += 1
            self._atomic_write_json(self.daemon_json_path, self._state)
            args_preview = json.dumps(args, ensure_ascii=False)
            if len(args_preview) > self._ARGS_PREVIEW_MAX:
                suffix = "...[truncated]"
                args_preview = args_preview[: self._ARGS_PREVIEW_MAX - len(suffix)] + suffix
            self._append_jsonl(
                self.events_path,
                {
                    "event": "tool_call",
                    "name": name,
                    "args_preview": args_preview,
                    "turn": self._state["turn"],
                    "ts": self._now_iso(),
                },
            )
            self.heartbeat_path.touch()
        self._safe_state("set_current_tool", _write)

    def clear_current_tool(self, result_status: str) -> None:
        """Mark a tool dispatch finished.

        Clears current_tool in daemon.json, logs tool_result event.
        result_status is "ok" on normal returns or "error" when the handler
        raised or returned {"status": "error", ...}.
        """
        def _write():
            tool_name = self._state["current_tool"]
            self._state["current_tool"] = None
            self._atomic_write_json(self.daemon_json_path, self._state)
            self._append_jsonl(
                self.events_path,
                {
                    "event": "tool_result",
                    "name": tool_name,
                    "status": result_status,
                    "turn": self._state["turn"],
                    "ts": self._now_iso(),
                },
            )
        self._safe_state("clear_current_tool", _write)


    # ------------------------------------------------------------------
    # External CLI backend hooks
    # ------------------------------------------------------------------

    _CLI_OUTPUT_EVENT_MAX = 4000
    _LAST_OUTPUT_MAX = 1000

    def record_cli_output(self, text: str, *, stream: str = "stdout") -> None:
        """Record one stdout/stderr progress line from an external CLI backend.

        CLI backends (Claude Code / Codex) do not run through the LingTai
        ChatSession tool loop, so ``turn`` and ``current_tool`` stay mostly
        static while the child process works.  This hook makes their progress
        visible to ``daemon(check)`` by appending bounded ``cli_output`` events,
        updating a small ``last_output`` field in daemon.json, and touching the
        heartbeat.  The final full output is still captured by ``mark_done``.
        """
        text = text.rstrip("\n")
        if not text:
            return
        if stream not in ("stdout", "stderr", "combined"):
            stream = "stdout"
        event_text = text
        truncated = False
        if len(event_text) > self._CLI_OUTPUT_EVENT_MAX:
            event_text = event_text[:self._CLI_OUTPUT_EVENT_MAX] + "...[truncated]"
            truncated = True
        last_output = self._durable_value(text)
        if len(last_output) > self._LAST_OUTPUT_MAX:
            last_output = last_output[-self._LAST_OUTPUT_MAX:]

        def _write():
            ts = self._now_iso()
            self._state["elapsed_s"] = self._now_secs()
            self._state["last_output"] = last_output
            self._state["last_output_at"] = ts
            self._atomic_write_json(self.daemon_json_path, self._state)
            entry = {
                "event": "cli_output",
                "stream": stream,
                "text": event_text,
                "elapsed_s": self._state["elapsed_s"],
                "ts": ts,
            }
            if truncated:
                entry["truncated"] = True
            self._append_jsonl(self.events_path, entry)
            self.heartbeat_path.touch()
        self._safe_state("record_cli_output", _write)

    # ------------------------------------------------------------------
    # Token accounting — dual ledger writes
    # ------------------------------------------------------------------

    def append_tokens(self, *, input: int, output: int,
                     thinking: int, cached: int,
                     model: str | None = None,
                     endpoint: str | None = None,
                     usage_extra: object = None) -> None:
        """Record per-call token usage to both ledgers.

        Both the daemon's own logs/token_ledger.jsonl and the parent's
        logs/token_ledger.jsonl get a tagged entry with source="daemon",
        em_id, and run_id, so every row is self-describing for uniform
        analytics regardless of which ledger it lives in. The file location
        still aids attribution, but it is no longer the only signal. Existing
        sum_token_ledger callers continue to count daemon spend in the
        parent's lifetime totals (they only read the numeric fields).

        ``model`` and ``endpoint`` (if provided) are written as first-class
        attribution fields on both ledgers — the daemon may use a different
        model/provider than the parent, so per-entry tagging is required for
        multi-provider cost analytics. ``usage_extra`` is projected once onto
        the five safe codex-pool attribution fields; arbitrary provider metadata
        is never copied into either ledger.

        Skips both writes if all four values are zero — avoids ledger noise
        from LLM calls that returned no usage.

        Each write is independently fault-tolerant — if the parent's ledger
        write fails, the daemon's local ledger is still authoritative.
        """
        if not (input or output or thinking or cached):
            return

        # Update running totals in daemon.json
        def _update_state():
            self._state["tokens"]["input"] += input
            self._state["tokens"]["output"] += output
            self._state["tokens"]["thinking"] += thinking
            self._state["tokens"]["cached"] += cached
            self._atomic_write_json(self.daemon_json_path, self._state)
        self._safe_state("append_tokens.state", _update_state)

        # Sanitize once, then mirror the same safe pool-attribution subset
        # into both ledgers.  Arbitrary UsageMetadata.extra fields never cross
        # this helper-writer boundary.
        ledger_extra = {
            "source": "daemon",
            "em_id": self._handle,
            "run_id": self._run_id,
            **safe_codex_pool_usage_extra(usage_extra),
        }

        # Daemon's own ledger — tagged source=daemon for uniformity with
        # parent's ledger and main/soul writes (every entry self-describes).
        self._safe(
            "append_tokens.daemon_ledger",
            lambda: append_token_entry(
                self.token_ledger_path,
                input=input, output=output,
                thinking=thinking, cached=cached,
                model=model, endpoint=endpoint,
                extra=ledger_extra,
            ),
        )

        # Parent's ledger — same tags so daemon spend is identifiable in
        # the parent's lifetime totals.
        self._safe(
            "append_tokens.parent_ledger",
            lambda: append_token_entry(
                self._parent_token_ledger,
                input=input, output=output,
                thinking=thinking, cached=cached,
                model=model, endpoint=endpoint,
                extra=ledger_extra,
            ),
        )

    # ------------------------------------------------------------------
    # CLI-backend usage — UI-only, never touches the token ledgers
    # ------------------------------------------------------------------

    def record_cli_tokens(self, *, input: int, output: int,
                          cached: int, thinking: int = 0,
                          raw: dict | None = None) -> None:
        """Accumulate external CLI token usage into daemon.json ``cli_tokens``.

        This is deliberately separate from :meth:`append_tokens`: CLI backends
        (claude-p / claude-code, codex, cursor, ...) run as external processes billing
        on their own provider account, and their cache-creation/cache-read
        semantics don't map onto the kernel's adapter accounting. So this
        method writes ONLY to ``daemon.json.cli_tokens`` — never to the daemon
        or parent ``token_ledger.jsonl`` — purely so the TUI ``/daemons`` view
        can show what a CLI run cost without contaminating ``sum_token_ledger``
        lifetime totals.

        Normalized totals accumulate across usage events:
            input    — sum of each backend's normalized disjoint input count
                       (Codex: ``max(input_tokens - cached_input_tokens, 0)``)
            output   — sum of ``output_tokens``
            cached   — sum of the backend's reported cached input tokens
            thinking — sum of any recognizable thinking/reasoning tokens (0 if none)
            calls    — incremented once per recorded usage event

        Skips entirely (no state mutation, no ``calls`` bump, no event) when
        all four totals are zero — there is genuinely nothing to count, and we
        avoid ledger-style noise. ``raw`` (if provided) is redacted at this
        durable producer boundary, then appended to ``logs/events.jsonl`` as a
        ``cli_usage`` event for forensic inspection.
        """
        if not (input or output or cached or thinking):
            return

        def _write():
            cli_tokens = self._state.setdefault(
                "cli_tokens",
                {"input": 0, "output": 0, "thinking": 0,
                 "cached": 0, "calls": 0},
            )
            cli_tokens.setdefault("input", 0)
            cli_tokens.setdefault("output", 0)
            cli_tokens.setdefault("cached", 0)
            cli_tokens.setdefault("thinking", 0)
            cli_tokens.setdefault("calls", 0)
            cli_tokens["input"] += input
            cli_tokens["output"] += output
            cli_tokens["cached"] += cached
            cli_tokens["thinking"] += thinking
            cli_tokens["calls"] += 1
            self._atomic_write_json(self.daemon_json_path, self._state)
            entry = {
                "event": "cli_usage",
                "input": input,
                "output": output,
                "cached": cached,
                "thinking": thinking,
                "ts": self._now_iso(),
            }
            if raw is not None:
                from lingtai.kernel.daemon_supervisor.manifest import redact_durable_value
                entry["raw"] = redact_durable_value(raw, field="raw")
            self._append_jsonl(self.events_path, entry)
        self._safe_state("record_cli_tokens", _write)

    # ------------------------------------------------------------------
    # Artifact manifest
    # ------------------------------------------------------------------

    # Manifest schema version — bump when the artifacts.json shape changes so
    # readers can detect a stale layout (mirrors daemon.json's data_version).
    MANIFEST_VERSION = 1

    # Hard cap on listed artifacts so a run that writes hundreds of files (e.g.
    # a daemon that drops many work-product files into its run dir) cannot make
    # the manifest unbounded. The well-known artifacts are always listed first;
    # the cap only ever drops *extra* discovered files, and the manifest records
    # how many were omitted.
    _MANIFEST_MAX_ENTRIES = 64

    # Inferred role for each well-known artifact, by run-dir-relative path.
    # Anything not in this map is reported with role=None (still listed).
    _MANIFEST_ROLES = {
        "daemon.json": "status",
        "result.txt": "result",
        ".prompt": "prompt",
        ".heartbeat": "heartbeat",
        "history/chat_history.jsonl": "transcript",
        "logs/events.jsonl": "events",
        "logs/token_ledger.jsonl": "token_ledger",
    }

    # Order in which well-known artifacts are emitted (most useful first). Files
    # present on disk but not in this list are appended afterward, sorted by
    # relative path, until the entry cap is reached.
    _MANIFEST_WELL_KNOWN_ORDER = (
        "daemon.json",
        "result.txt",
        ".prompt",
        "history/chat_history.jsonl",
        "logs/events.jsonl",
        "logs/token_ledger.jsonl",
        ".heartbeat",
    )

    @classmethod
    def build_manifest(cls, run_path: Path) -> dict:
        """Compute a compact artifact manifest for a daemon run dir on disk.

        Pure read over ``run_path`` — does not require a live DaemonRunDir, so
        the ``check``/``list`` historical fallback can compute a manifest for an
        old run that predates the on-terminal ``artifacts.json`` write. Lists
        path/size/mtime/role metadata ONLY — never file contents — with a hard
        entry cap. Best-effort: any per-file ``OSError`` is skipped, and a
        global read failure yields an ``error``-tagged manifest rather than
        raising, so a manifest never breaks a ``check`` response.

        Returned shape::

            {
              "manifest_version": 1,
              "generated_at": "<ISO8601 UTC>",
              "run_id": "<folder name>",
              "state": "<daemon.json state or null>",
              "result_path": "<abs path or null>",
              "error_path": "<abs path or null>",   # result.txt for failed runs
              "artifact_count": <int>,              # files listed
              "artifacts_total": <int>,             # files found before cap
              "truncated": <bool>,
              "artifacts": [
                {"path": "daemon.json", "size": 1234,
                 "mtime": "<ISO8601 UTC>", "role": "status"},
                ...
              ],
            }
        """
        run_path = Path(run_path)
        manifest = {
            "manifest_version": cls.MANIFEST_VERSION,
            "generated_at": cls._now_iso(),
            "run_id": run_path.name,
            "state": None,
            "result_path": None,
            "error_path": None,
            "artifact_count": 0,
            "artifacts_total": 0,
            "truncated": False,
            "artifacts": [],
        }

        # Pull state + result/error path from daemon.json if readable. A missing
        # or corrupt daemon.json simply leaves these fields null — the file
        # listing below still works.
        daemon_json = run_path / "daemon.json"
        state_val = None
        try:
            data = json.loads(daemon_json.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state_val = data.get("state")
                manifest["state"] = state_val
                rp = data.get("result_path")
                if isinstance(rp, str) and rp:
                    manifest["result_path"] = rp
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Discover files: well-known artifacts in priority order first, then any
        # other regular files anywhere under the run dir (relative paths), sorted
        # for determinism. De-dup so a well-known file isn't listed twice.
        ordered_rel: list[str] = []
        seen: set[str] = set()
        for rel in cls._MANIFEST_WELL_KNOWN_ORDER:
            if (run_path / rel).is_file():
                ordered_rel.append(rel)
                seen.add(rel)

        extras: list[str] = []
        try:
            for fp in run_path.rglob("*"):
                try:
                    if not fp.is_file():
                        continue
                except OSError:
                    continue
                rel = fp.relative_to(run_path).as_posix()
                # Skip atomic-write tempfiles and the manifest itself.
                if rel.endswith(".tmp"):
                    continue
                if rel == "artifacts.json":
                    continue
                if rel in seen:
                    continue
                seen.add(rel)
                extras.append(rel)
        except OSError as e:
            manifest["error"] = f"scan failed: {e}"

        extras.sort()
        all_rel = ordered_rel + extras
        manifest["artifacts_total"] = len(all_rel)

        listed = all_rel[: cls._MANIFEST_MAX_ENTRIES]
        if len(all_rel) > len(listed):
            manifest["truncated"] = True

        artifacts: list[dict] = []
        for rel in listed:
            fp = run_path / rel
            try:
                st = fp.stat()
            except OSError:
                continue
            artifacts.append({
                "path": rel,
                "size": st.st_size,
                "mtime": cls._mtime_iso(st.st_mtime),
                "role": cls._MANIFEST_ROLES.get(rel),
            })
        manifest["artifacts"] = artifacts
        manifest["artifact_count"] = len(artifacts)

        # error_path: for a failed/timeout/cancelled run, result.txt (if any)
        # holds the closest thing to a failure record. Point at it explicitly so
        # the parent doesn't have to infer which file to read.
        result_txt = run_path / "result.txt"
        if state_val in ("failed", "timeout", "cancelled") and result_txt.is_file():
            manifest["error_path"] = str(result_txt)

        return manifest

    @staticmethod
    def _mtime_iso(epoch: float) -> str:
        """Format a POSIX mtime as ISO 8601 UTC (matches _now_iso style)."""
        return datetime.fromtimestamp(epoch, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def write_manifest(self) -> None:
        """Compute and persist this run's artifact manifest to artifacts.json.

        Called at terminal time so the manifest captures the final result.txt.
        Best-effort: an OSError during the scan or write is swallowed via
        ``_safe`` so a manifest failure never blocks the terminal transition.
        Writing artifacts.json before re-scanning would race its own size/mtime,
        so we build first, then write atomically.
        """
        def _write():
            manifest = self.build_manifest(self._path)
            self._atomic_write_json(self.manifest_path, manifest)
        self._safe("write_manifest", _write)

    # ------------------------------------------------------------------
    # Terminal markers
    # ------------------------------------------------------------------

    _RESULT_PREVIEW_MAX = 200

    def mark_done(self, text: str) -> None:
        """Normal completion. Sets state=done, finished_at, result_preview.

        The complete terminal text is written to ``result.txt`` for deliberate
        inspection; daemon.json keeps only a bounded preview so list/check stay
        compact.  A failure to write result.txt must not prevent the terminal
        state transition.
        """
        text = self._durable_value(text or "")

        def _write():
            try:
                disk = json.loads(self.daemon_json_path.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    self._state = disk
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
            current_state = self._state.get("state")
            if (
                current_state in {"done", "failed", "cancelled", "timeout"}
                and current_state != "done"
            ):
                return
            result_path = None
            try:
                self.result_path.write_text(text, encoding="utf-8")
                result_path = str(self.result_path)
            except OSError as e:
                if self._log_callback is not None:
                    try:
                        self._log_callback(
                            "daemon_fs_error",
                            em_id=self._handle,
                            run_id=self._run_id,
                            op="mark_done.result",
                            error=str(e),
                        )
                    except Exception:
                        pass
            self._state["state"] = "done"
            self._state["finished_at"] = self._now_iso()
            self._state["elapsed_s"] = self._now_secs()
            self._state["current_tool"] = None
            preview = text
            if len(preview) > self._RESULT_PREVIEW_MAX:
                preview = preview[:self._RESULT_PREVIEW_MAX]
            self._state["result_preview"] = preview
            self._state["result_path"] = result_path
            self._atomic_write_json(self.daemon_json_path, self._state)
            self._append_jsonl(
                self.events_path,
                {
                    "event": "daemon_done",
                    "elapsed_s": self._state["elapsed_s"],
                    "result_path": result_path,
                    "ts": self._now_iso(),
                },
            )
            self.heartbeat_path.touch()
        self._safe_state("mark_done", _write)
        # Manifest last: result.txt + daemon.json are now final, so the scan
        # captures the terminal artifact set. Its own _safe means a manifest
        # failure can't undo the terminal transition above.
        self.write_manifest()

    def mark_failed(self, exc: BaseException) -> None:
        """Exception in run loop. Sets state=failed, error.{type, message}.

        Defensive: a user-defined exception's `__str__` may itself raise
        (TypeError, AttributeError, ...). _safe only catches OSError, so we
        materialize the message string before entering the closure.
        """
        try:
            msg = self._durable_value(str(exc))
        except Exception:
            msg = f"<unrenderable {type(exc).__name__}>"

        def _write():
            try:
                disk = json.loads(self.daemon_json_path.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    self._state = disk
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
            if self._state.get("state") in {"done", "failed", "cancelled", "timeout"}:
                return
            self._state["state"] = "failed"
            self._state["finished_at"] = self._now_iso()
            self._state["elapsed_s"] = self._now_secs()
            self._state["current_tool"] = None
            self._state["error"] = {
                "type": type(exc).__name__,
                "message": msg,
            }
            self._atomic_write_json(self.daemon_json_path, self._state)
            self._append_jsonl(
                self.events_path,
                {
                    "event": "daemon_error",
                    "exception": type(exc).__name__,
                    "message": msg,
                    "elapsed_s": self._state["elapsed_s"],
                    "ts": self._now_iso(),
                },
            )
        self._safe_state("mark_failed", _write)
        self.write_manifest()

    def record_cli_termination(
        self, *, reason: str, signal_name: str, returncode: int | None
    ) -> None:
        """Record that LingTai signalled this run's CLI subprocess.

        Writes a structured ``daemon_cli_terminate`` event and stamps
        ``cli_termination`` onto daemon.json so a later ``daemon(check)`` or a
        post-mortem run-dir inspection can attribute an otherwise-opaque
        signal exit (e.g. SIGTERM/143) to its local cause — parent
        refresh/agent_stop, reclaim, or watchdog timeout. This is forensic
        metadata only; it does not itself transition terminal state (the run
        loop still records failed/cancelled/timeout). See GH #455.
        """
        def _write():
            self._state["cli_termination"] = {
                "reason": reason,
                "signal": signal_name,
                "returncode": returncode,
                "ts": self._now_iso(),
            }
            self._atomic_write_json(self.daemon_json_path, self._state)
            self._append_jsonl(
                self.events_path,
                {
                    "event": "daemon_cli_terminate",
                    "reason": reason,
                    "signal": signal_name,
                    "returncode": returncode,
                    "ts": self._now_iso(),
                },
            )
        self._safe_state("record_cli_termination", _write)

    def mark_cancelled(self) -> None:
        """Cancel event observed. Sets state=cancelled."""
        self._mark_terminal("cancelled", "daemon_cancelled")

    def mark_timeout(self) -> None:
        """Watchdog timeout. Sets state=timeout."""
        self._mark_terminal("timeout", "daemon_timeout")

    def claim_terminal_notification(self, status: str) -> str | None:
        """Claim a temporary terminal-notification attempt for this run.

        ``terminal_notified`` is a durable published receipt only. A separate
        pending claim prevents concurrent callbacks from publishing at the same
        time; failed enqueue clears the claim so the terminal notification
        remains retryable. A crash with only a pending claim is intentionally
        treated as unpublished by startup reconciliation.
        """
        # All live terminal-notification state transitions use one lock order:
        # terminal-notification lock -> per-run state-writer lock.
        with self._terminal_notification_lock:
            with self._state_transaction():
                try:
                    disk = json.loads(self.daemon_json_path.read_text(encoding="utf-8"))
                    if isinstance(disk, dict):
                        self._state = disk
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    pass
                if self._state.get("terminal_notified") is True:
                    return None
                if isinstance(self._state.get("terminal_notification_claim"), dict):
                    return None
                key = self.terminal_notification_idempotency_key(self._run_id)
                self._state["terminal_notification_claim"] = {
                    "status": "pending",
                    "terminal_status": status,
                    "idempotency_key": key,
                    "claimed_at": self._now_iso(),
                }
                self._safe(
                    "claim_terminal_notification",
                    lambda: self._atomic_write_json(self.daemon_json_path, self._state),
                )
                return key

    def clear_terminal_notification_claim(self) -> None:
        """Clear a failed pending terminal-notification attempt."""
        # Keep the same terminal-notification lock -> state-writer order as
        # claim_terminal_notification and mark_terminal_notification_published.
        with self._terminal_notification_lock:
            with self._state_transaction():
                try:
                    disk = json.loads(self.daemon_json_path.read_text(encoding="utf-8"))
                    if isinstance(disk, dict):
                        self._state = disk
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    pass
                if self._state.get("terminal_notified") is True:
                    return
                self._state["terminal_notification_claim"] = None
                self._safe(
                    "clear_terminal_notification_claim",
                    lambda: self._atomic_write_json(self.daemon_json_path, self._state),
                )

    def mark_terminal_notification_published(self, idempotency_key: str) -> None:
        """Persist the durable terminal-notification receipt after publication."""
        # Keep the single terminal-notification lock -> state-writer order. All
        # receipt decisions, mutations, and daemon.json persistence stay in the
        # owner transaction so live writers cannot observe a partial transition.
        with self._terminal_notification_lock:
            with self._state_transaction():
                try:
                    disk = json.loads(self.daemon_json_path.read_text(encoding="utf-8"))
                    if isinstance(disk, dict):
                        self._state = disk
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    pass
                self._state["terminal_notified"] = True
                self._state["terminal_notification_claim"] = None
                self._state["terminal_notification_receipt"] = {
                    "idempotency_key": idempotency_key,
                    "published_at": self._now_iso(),
                }
                self._safe(
                    "mark_terminal_notification_published",
                    lambda: self._atomic_write_json(self.daemon_json_path, self._state),
                )

    @classmethod
    def mark_terminal_notification_published_on_disk(
        cls, daemon_json_path: Path, *, idempotency_key: str
    ) -> bool:
        """Persist a terminal-notification receipt for an existing run dir."""
        daemon_json_path = Path(daemon_json_path)
        try:
            with cls.state_file_lock(daemon_json_path.parent):
                state = json.loads(daemon_json_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict) or state.get("terminal_notified") is True:
                    return False
                state["terminal_notified"] = True
                state["terminal_notification_claim"] = None
                state["terminal_notification_receipt"] = {
                    "idempotency_key": idempotency_key,
                    "published_at": cls._now_iso(),
                }
                atomic_write_json(daemon_json_path, state, ensure_ascii=False, indent=2)
                return True
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return False

    def _mark_terminal(self, state: str, event: str) -> None:
        def _write():
            try:
                disk = json.loads(self.daemon_json_path.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    self._state = disk
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
            if self._state.get("state") in {"done", "failed", "cancelled", "timeout"}:
                return
            self._state["state"] = state
            self._state["finished_at"] = self._now_iso()
            self._state["elapsed_s"] = self._now_secs()
            self._state["current_tool"] = None
            self._atomic_write_json(self.daemon_json_path, self._state)
            self._append_jsonl(
                self.events_path,
                {
                    "event": event,
                    "elapsed_s": self._state["elapsed_s"],
                    "ts": self._now_iso(),
                },
            )
        self._safe_state(f"mark_{state}", _write)
        self.write_manifest()
