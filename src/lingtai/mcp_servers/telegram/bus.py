"""SQLite WAL command bus between agent-side proxies and the Gateway owner.

Proxy-mode ``TelegramManager`` instances (one per station) never touch the
Telegram network: every outbound call is serialized into a gateway command and
submitted through this bus. The single-process Gateway is the sole consumer:
it claims pending commands one at a time, dispatches them through
``GatewayCommandRouter``, and writes the result back.

Why SQLite (WAL) and not an in-memory list:

- Agent host and Gateway host are separate processes that share one bus file
  per station (``<agent_dir>/telegram/gateway.sqlite3`` by default).
- A submission is durable the moment it is inserted; pending commands survive
  a Gateway restart (``reset_stale`` re-opens any row a dead owner left in
  ``running`` state — the single-owner invariant makes every ``running`` row
  stale on startup).
- ``claim`` is a single atomic ``UPDATE ... RETURNING`` so exactly one
  consumer ever executes one command, and the agent's bounded wait reads back
  exactly the result that consumer wrote.

Test doubles may keep using a plain ``list`` sink; production code paths use
this module only.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_STATE_PENDING = "pending"
_STATE_RUNNING = "running"
_STATE_DONE = "done"

# Agent-side bounded wait for a gateway result; the command stays queued when
# it expires so a restart can still execute it (never silently dropped).
_DEFAULT_SUBMIT_TIMEOUT = 30.0
# Both sides poll at a small interval; SQLite WAL keeps readers lock-free.
_POLL_INTERVAL = 0.02

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    method TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done')),
    result TEXT,
    created_at REAL NOT NULL,
    claimed_at REAL,
    completed_at REAL
)
"""
_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_gateway_commands_pending "
    "ON gateway_commands (status, id)"
)


class SqliteCommandBus:
    """Durable, single-owner command queue backed by one SQLite WAL database.

    Agent side: :meth:`submit` inserts one command and blocks until the owner
    completes it (bounded by ``timeout``).
    Gateway side (single owner): :meth:`claim` atomically moves the oldest
    pending command to ``running``; :meth:`complete` writes back the JSON
    result; :meth:`reset_stale` re-opens rows left behind by a dead owner.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        with self._lock:
            with self._conn:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=10000")
                self._conn.execute(_SCHEMA)
                self._conn.execute(_INDEX_SQL)

    # -- Agent side ------------------------------------------------------------

    def submit(
        self,
        command: dict[str, Any],
        *,
        timeout: float = _DEFAULT_SUBMIT_TIMEOUT,
    ) -> Any:
        """Insert one command and wait (bounded) for the gateway result.

        Returns the JSON-decoded result the gateway owner wrote back, or an
        error dict when the wait expires (the command remains queued and a
        Gateway restart will still execute it).
        """
        if not isinstance(command, dict):
            return {"status": "error", "error": "command must be an object"}
        method = command.get("method")
        params = command.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return {"status": "error", "error": "command needs 'method' and 'params'"}
        with self._lock:
            if self._closed:
                return {"status": "error", "error": "command bus is closed"}
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO gateway_commands "
                    "(method, params, status, created_at) VALUES (?, ?, ?, ?)",
                    (method, json.dumps(params, default=str), _STATE_PENDING, time.time()),
                )
                command_id = int(cur.lastrowid)
        deadline = time.monotonic() + timeout
        while True:
            result = self._result(command_id)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                log.warning(
                    "gateway command %s (%s) timed out after %.1fs; still queued",
                    command_id, method, timeout,
                )
                return {
                    "status": "error",
                    "error": "gateway command timed out; it remains queued",
                    "command_id": command_id,
                    "method": method,
                }
            time.sleep(_POLL_INTERVAL)

    def _result(self, command_id: int) -> Any | None:
        """Return the completed result for one command, or ``None`` if pending."""
        with self._lock:
            if self._closed:
                return None
            row = self._conn.execute(
                "SELECT status, result FROM gateway_commands WHERE id = ?",
                (command_id,),
            ).fetchone()
        if row is None or row["status"] != _STATE_DONE:
            return None
        try:
            return json.loads(row["result"]) if row["result"] is not None else None
        except (TypeError, ValueError):
            return None

    # -- Gateway side (single owner) -------------------------------------------

    def claim(self) -> tuple[int, dict[str, Any]] | None:
        """Atomically claim the oldest pending command.

        Returns ``(command_id, command)`` or ``None`` when nothing is pending.
        The atomic ``UPDATE ... RETURNING`` guarantees exactly one consumer
        ever executes one command, even with multiple gateway threads or
        processes attached to the same file.
        """
        with self._lock:
            if self._closed:
                return None
            with self._conn:
                row = self._conn.execute(
                    """
                    UPDATE gateway_commands
                    SET status = 'running', claimed_at = ?
                    WHERE id = (
                        SELECT id FROM gateway_commands
                        WHERE status = 'pending' ORDER BY id LIMIT 1
                    )
                    RETURNING id, method, params
                    """,
                    (time.time(),),
                ).fetchone()
        if row is None:
            return None
        command_id = int(row["id"])
        try:
            params = json.loads(row["params"])
        except (TypeError, ValueError):
            params = {}
        return command_id, {"method": row["method"], "params": params}

    def complete(self, command_id: int, result: Any) -> None:
        """Write back the gateway result for one claimed command."""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE gateway_commands
                    SET status = 'done', result = ?, completed_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (json.dumps(result, default=str), time.time(), command_id),
                )

    def reset_stale(self) -> int:
        """Re-open every ``running`` row left by a dead Gateway owner.

        The single-owner invariant makes this safe: only the one Gateway
        consumes a bus, so at startup any ``running`` row is stale by
        definition. Returns the number of rows re-opened.
        """
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE gateway_commands SET status = 'pending', claimed_at = NULL "
                    "WHERE status = 'running'"
                )
                return int(cur.rowcount)

    def pending_count(self) -> int:
        """Number of commands waiting to be claimed (never raises when open)."""
        with self._lock:
            if self._closed:
                return 0
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM gateway_commands WHERE status = 'pending'",
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def close(self) -> None:
        """Close the underlying connection; idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
