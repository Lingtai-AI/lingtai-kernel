"""Durable desired/applied revisions for resident Telegram Task Cards."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class TaskCardRevisionStore:
    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS telegram_task_card_revisions (
                    route_key TEXT PRIMARY KEY,
                    desired_rev INTEGER NOT NULL DEFAULT 0,
                    desired_text TEXT NOT NULL DEFAULT '',
                    applied_rev INTEGER NOT NULL DEFAULT 0,
                    applied_text TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row(self, route_key: str):
        return self._conn.execute(
            "SELECT * FROM telegram_task_card_revisions WHERE route_key=?", (route_key,)
        ).fetchone()

    def propose(self, route_key: str, text: str, now: float) -> bool:
        with self._lock, self._conn:
            row = self._row(route_key)
            if row is not None and row["desired_text"] == text:
                return False
            revision = (int(row["desired_rev"]) if row else 0) + 1
            self._conn.execute("""
                INSERT INTO telegram_task_card_revisions
                (route_key,desired_rev,desired_text,applied_rev,applied_text,updated_at)
                VALUES (?,?,?,0,'',?) ON CONFLICT(route_key) DO UPDATE SET
                desired_rev=excluded.desired_rev,desired_text=excluded.desired_text,
                updated_at=excluded.updated_at
            """, (route_key, revision, text, now))
            return True

    def applied(self, route_key: str, now: float) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE telegram_task_card_revisions SET applied_rev=desired_rev,
                applied_text=desired_text,updated_at=? WHERE route_key=?
            """, (now, route_key))

    def pending_routes(self) -> list[tuple[str, int, str]]:
        with self._lock:
            rows = self._conn.execute("""
                SELECT route_key,desired_rev,desired_text
                FROM telegram_task_card_revisions WHERE applied_rev<desired_rev
            """).fetchall()
        return [(row["route_key"], int(row["desired_rev"]), row["desired_text"]) for row in rows]

    def desired_rev(self, route_key: str) -> int:
        with self._lock:
            row = self._row(route_key)
        return int(row["desired_rev"]) if row else 0

    def applied_rev(self, route_key: str) -> int:
        with self._lock:
            row = self._row(route_key)
        return int(row["applied_rev"]) if row else 0
