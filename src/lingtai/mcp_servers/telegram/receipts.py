"""Durable Agent-open receipts for Telegram."""
from __future__ import annotations

import re
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .account import TelegramRateLimitError

_RETRYABLE = {"rate_limit", "network", "server"}
log = logging.getLogger(__name__)


def extract_opened_message_ids(event: dict[str, Any]) -> list[str]:
    if event.get("type") == "steering_messages_opened":
        messages = event.get("message_ids")
    elif event.get("type") == "notification_block_injected":
        try:
            messages = event["_meta"]["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]["messages"]
        except (KeyError, TypeError):
            return []
    else:
        return []
    return [
        message_id
        for item in (messages if isinstance(messages, list) else [])
        if isinstance((message_id := item.get("id") if isinstance(item, dict) else item), str)
        and message_id
    ]


def classify_receipt_error(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, TelegramRateLimitError):
        retry_after = exc.retry_after
        return "rate_limit", retry_after if type(retry_after) is int and retry_after >= 0 else None
    if isinstance(exc, OSError) or type(exc).__module__.startswith("httpx"):
        return "network", None
    if str(exc).startswith("Telegram API error:"):
        match = re.search(r"\bHTTP (\d{3})\b", str(exc))
        return ("server" if not match or int(match.group(1)) >= 500 else "invalid"), None
    return "other", None


class ReceiptStore:
    """SQLite receipt intent and event cursor committed in one transaction."""

    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS telegram_receipts (
                    message_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK (state IN ('pending','applied','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    applied_at REAL,
                    last_error TEXT,
                    updated_at REAL NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS telegram_receipt_cursor (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    offset INTEGER NOT NULL CHECK (offset >= 0),
                    size INTEGER NOT NULL CHECK (size >= 0),
                    identity TEXT
                )
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def enqueue_many_and_advance(
        self, message_ids: list[str], offset: int, size: int, now: float,
        identity: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO telegram_receipts "
                "(message_id,state,attempts,next_attempt_at,updated_at) "
                "VALUES (?,'pending',0,0,?)",
                [(message_id, now) for message_id in message_ids],
            )
            self._conn.execute("""
                INSERT INTO telegram_receipt_cursor (id,offset,size,identity)
                VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET
                offset=excluded.offset,size=excluded.size,identity=excluded.identity
            """, (offset, size, identity))

    def reset_cursor(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                INSERT INTO telegram_receipt_cursor (id,offset,size,identity)
                VALUES (1,0,0,NULL) ON CONFLICT(id) DO UPDATE SET
                offset=0,size=0,identity=NULL
            """)

    def cursor(self) -> tuple[int, int, str | None]:
        with self._lock:
            row = self._conn.execute(
                "SELECT offset,size,identity FROM telegram_receipt_cursor WHERE id=1"
            ).fetchone()
        return (int(row["offset"]), int(row["size"]), row["identity"]) if row else (0, 0, None)

    def pending_due(self, now: float) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("""
                SELECT message_id,attempts FROM telegram_receipts
                WHERE state='pending' AND next_attempt_at<=?
                ORDER BY next_attempt_at LIMIT 100
            """, (now,)).fetchall()
        return [dict(row) for row in rows]

    def get(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM telegram_receipts WHERE message_id=?", (message_id,)
            ).fetchone()
        return dict(row) if row else None

    def mark_applied(self, message_id: str, now: float) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE telegram_receipts SET state='applied',applied_at=?,updated_at=?
                WHERE message_id=?
            """, (now, now, message_id))

    def mark_failed(
        self, message_id: str, now: float, attempts: int,
        next_attempt_at: float | None, error_class: str,
    ) -> None:
        state = "pending" if next_attempt_at is not None else "failed"
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE telegram_receipts SET state=?,attempts=?,next_attempt_at=?,
                last_error=?,updated_at=? WHERE message_id=?
            """, (state, attempts, next_attempt_at, error_class[:40], now, message_id))


class ReceiptWorker:
    BACKOFF_CAP = 60.0

    def __init__(
        self, store: ReceiptStore, apply: Callable[[str], None], *, poll_interval: float = 1.0,
    ) -> None:
        self._store = store
        self._apply = apply
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        applied = 0
        for row in self._store.pending_due(now):
            message_id = row["message_id"]
            try:
                self._apply(message_id)
            except Exception as exc:
                error_class, retry_after = classify_receipt_error(exc)
                attempts = int(row["attempts"]) + 1
                next_at = None
                if error_class in _RETRYABLE:
                    delay = retry_after if retry_after is not None else min(self.BACKOFF_CAP, 2 ** attempts)
                    next_at = now + delay
                self._store.mark_failed(message_id, now, attempts, next_at, error_class)
            else:
                self._store.mark_applied(message_id, now)
                applied += 1
        return applied

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception as exc:
                    log.warning("Telegram receipt worker poll failed: %s", exc)
                if self._stop.wait(self._poll_interval):
                    return

        self._thread = threading.Thread(target=run, name="telegram-receipt-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
