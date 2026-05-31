"""LoggingService — structured event logging backing agent observability.

The primary durable event log remains ``logs/events.jsonl``.  SQLite support is
implemented as an additive, rebuildable sidecar index: JSONL is the source of
truth; ``logs/log.sqlite`` exists to make history queryable.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
DEFAULT_SQLITE_NAME = "log.sqlite"
DEFAULT_JSONL_NAME = "events.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LoggingService(ABC):
    """Abstract structured event logging service.

    Backs agent observability. Implementations provide the actual storage
    mechanism (JSONL file, database, network sink, etc.).
    """

    @abstractmethod
    def log(self, event: dict) -> dict | None:
        """Log a structured event. Must be thread-safe.

        Implementations may return storage metadata (for example JSONL source
        offsets). Callers should treat the return value as optional.
        """

    def close(self) -> None:
        """Flush and release resources. Default no-op."""


class JSONLLoggingService(LoggingService):
    """Append structured events as JSON lines to a file.

    Thread-safe via lock. Flushes after every write for real-time tailing.
    """

    def __init__(self, path: Path | str, *, ensure_ascii: bool = False) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "ab")
        self._lock = threading.Lock()
        self._closed = False
        self._ensure_ascii = ensure_ascii

    @property
    def path(self) -> Path:
        return self._path

    def log(self, event: dict) -> dict | None:
        if self._closed:
            return None
        line = json.dumps(event, ensure_ascii=self._ensure_ascii, default=str)
        payload = (line + "\n").encode("utf-8")
        with self._lock:
            source_offset = self._file.tell()
            self._file.write(payload)
            self._file.flush()
        return {"source_file": str(self._path), "source_offset": source_offset}

    def get_events(self) -> list[dict]:
        """Read all events from the JSONL file. Thread-safe."""
        with self._lock:
            if not self._path.exists():
                return []
            events = []
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            return events

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._file.close()


class SQLiteEventIndex:
    """Best-effort SQLite index for JSONL events.

    The index is a derived artifact.  It is safe to delete and rebuild from
    ``events.jsonl``.  Runtime writes fail open by disabling the sidecar after
    the first sqlite error; they never raise into the agent turn.
    """

    def __init__(self, path: Path | str, *, ensure: bool = True, keep_open: bool = True) -> None:
        self.path = Path(path)
        self._keep_open = keep_open
        self._lock = threading.RLock()
        self._disabled_reason: str | None = None
        self._conn: sqlite3.Connection | None = None
        if ensure:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._ensure_open()
            except (OSError, sqlite3.Error) as exc:
                # Runtime sidecar creation must fail open.  JSONL remains the
                # source of truth, and callers can rebuild the sidecar later.
                self._disabled_reason = str(exc)
                self.close()

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def _ensure_open(self) -> sqlite3.Connection:
        if self._disabled_reason:
            raise sqlite3.Error(self._disabled_reason)
        if self._conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._configure(conn)
            self._ensure_schema(conn)
        return self._conn

    @staticmethod
    def _configure(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_cursors (
              source_file TEXT PRIMARY KEY,
              byte_offset INTEGER NOT NULL DEFAULT 0,
              line_no INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              type TEXT NOT NULL,
              agent_address TEXT,
              agent_name_snapshot TEXT,
              fields_json TEXT NOT NULL,
              source_file TEXT,
              source_offset INTEGER,
              inserted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );

            CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(type, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_offset
              ON events(source_file, source_offset)
              WHERE source_file IS NOT NULL AND source_offset IS NOT NULL;
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (SCHEMA_VERSION, "initial_events_index", _utc_now()),
        )
        conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def disable(self, reason: str) -> None:
        self._disabled_reason = reason
        self.close()

    @staticmethod
    def event_row(event: dict, *, source_file: str | None = None, source_offset: int | None = None) -> tuple[Any, ...]:
        fields = dict(event)
        event_type = fields.pop("type", "")
        ts = fields.pop("ts", 0.0)
        agent_address = fields.pop("address", None)
        agent_name = fields.pop("agent_name", None)
        return (
            float(ts or 0.0),
            str(event_type),
            str(agent_address) if agent_address is not None else None,
            str(agent_name) if agent_name is not None else None,
            json.dumps(fields, ensure_ascii=False, default=str),
            source_file,
            source_offset,
        )

    def log_event(self, event: dict, *, source_file: str | None = None, source_offset: int | None = None) -> None:
        if self._disabled_reason:
            return
        try:
            with self._lock:
                conn = self._ensure_open()
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events(
                        ts, type, agent_address, agent_name_snapshot, fields_json,
                        source_file, source_offset
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    self.event_row(event, source_file=source_file, source_offset=source_offset),
                )
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            self.disable(str(exc))
        finally:
            if not self._keep_open:
                self.close()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        statement = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
        if statement != "select":
            raise ValueError("log query only accepts SELECT statements")
        with self._lock:
            conn = self._ensure_open()
            original_factory = conn.row_factory
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                before_changes = conn.total_changes
                cur = conn.execute(sql, tuple(params))
                if cur.description is None:
                    return []
                rows = [dict(row) for row in cur.fetchall()]
                if conn.total_changes != before_changes:
                    raise ValueError("log query must not modify the sqlite index")
                return rows
            finally:
                try:
                    conn.execute("PRAGMA query_only=OFF")
                finally:
                    conn.row_factory = original_factory

    def doctor(self) -> dict[str, Any]:
        if self._disabled_reason:
            return {"status": "disabled", "path": str(self.path), "reason": self._disabled_reason}
        with self._lock:
            conn = self._ensure_open()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            cursor_count = conn.execute("SELECT COUNT(*) FROM import_cursors").fetchone()[0]
            schema_versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        return {
            "status": "ok" if integrity == "ok" else "error",
            "path": str(self.path),
            "integrity_check": integrity,
            "event_count": event_count,
            "cursor_count": cursor_count,
            "schema_versions": schema_versions,
        }


class CompositeLoggingService(LoggingService):
    """Primary JSONL logger plus best-effort sidecar indexes."""

    def __init__(self, primary: JSONLLoggingService, *, sqlite_index: SQLiteEventIndex | None = None) -> None:
        self.primary = primary
        self.sqlite_index = sqlite_index

    def log(self, event: dict) -> dict | None:
        metadata = self.primary.log(event)
        # If the primary JSONL write did not happen (for example after close),
        # do not create sidecar-only facts.  JSONL must remain the source of truth.
        if metadata is None:
            return None
        if self.sqlite_index is not None:
            source_file = metadata.get("source_file")
            source_offset = metadata.get("source_offset")
            self.sqlite_index.log_event(event, source_file=source_file, source_offset=source_offset)
        return metadata

    def get_events(self) -> list[dict]:
        return self.primary.get_events()

    def close(self) -> None:
        self.primary.close()
        if self.sqlite_index is not None:
            self.sqlite_index.close()


def _iter_jsonl_events_with_offsets(path: Path) -> Iterable[tuple[dict, int, int, int]]:
    """Yield ``(event, byte_offset, next_offset, line_no)`` from a JSONL file."""
    with open(path, "rb") as f:
        line_no = 0
        while True:
            offset = f.tell()
            raw = f.readline()
            if not raw:
                break
            line_no += 1
            next_offset = f.tell()
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(event, dict):
                yield event, offset, next_offset, line_no


def rebuild_sqlite_event_index(
    agent_dir: Path | str,
    *,
    jsonl_path: Path | str | None = None,
    sqlite_path: Path | str | None = None,
) -> dict[str, Any]:
    """Rebuild ``logs/log.sqlite`` from ``logs/events.jsonl`` atomically."""
    agent_dir = Path(agent_dir)
    logs_dir = agent_dir / "logs"
    source = Path(jsonl_path) if jsonl_path is not None else logs_dir / DEFAULT_JSONL_NAME
    target = Path(sqlite_path) if sqlite_path is not None else logs_dir / DEFAULT_SQLITE_NAME
    if not source.is_file():
        raise FileNotFoundError(f"events JSONL not found: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="log-sqlite-rebuild-", dir=str(target.parent)))
    tmp_db = tmp_dir / target.name
    count = 0
    last_offset = 0
    last_line = 0
    try:
        index = SQLiteEventIndex(tmp_db)
        conn = index._ensure_open()
        with index._lock:
            conn.execute("BEGIN")
            for event, offset, next_offset, line_no in _iter_jsonl_events_with_offsets(source):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events(
                        ts, type, agent_address, agent_name_snapshot, fields_json,
                        source_file, source_offset
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    SQLiteEventIndex.event_row(event, source_file=str(source), source_offset=offset),
                )
                count += 1
                last_offset = next_offset
                last_line = line_no
            conn.execute(
                """
                INSERT OR REPLACE INTO import_cursors(source_file, byte_offset, line_no, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(source), last_offset, last_line, _utc_now()),
            )
            conn.commit()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        index.close()
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"rebuilt sqlite integrity_check={integrity}")
        for suffix in ("-wal", "-shm"):
            sidecar = target.with_name(target.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        tmp_db.replace(target)
        return {"status": "ok", "source": str(source), "target": str(target), "event_count": count, "line_no": last_line, "byte_offset": last_offset}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def doctor_sqlite_event_index(agent_dir: Path | str, *, sqlite_path: Path | str | None = None) -> dict[str, Any]:
    agent_dir = Path(agent_dir)
    target = Path(sqlite_path) if sqlite_path is not None else agent_dir / "logs" / DEFAULT_SQLITE_NAME
    if not target.is_file():
        return {"status": "missing", "path": str(target)}
    index = SQLiteEventIndex(target)
    try:
        return index.doctor()
    finally:
        index.close()


def query_sqlite_event_index(agent_dir: Path | str, sql: str, *, sqlite_path: Path | str | None = None) -> list[dict[str, Any]]:
    agent_dir = Path(agent_dir)
    target = Path(sqlite_path) if sqlite_path is not None else agent_dir / "logs" / DEFAULT_SQLITE_NAME
    if not target.is_file():
        rebuild_sqlite_event_index(agent_dir, sqlite_path=target)
    index = SQLiteEventIndex(target)
    try:
        return index.query(sql)
    finally:
        index.close()
