"""Feishu-owned durable routes and Task Card projection workers."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lingtai.kernel._fsutil import atomic_write_json, read_json
from lingtai.mcp_servers.task_card import TaskCardEventProjection, TaskCardRoute

log = logging.getLogger(__name__)


class FeishuTaskCardStore:
    """Persist exact resident ids with their account/chat/thread routes."""

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> tuple[bool, dict[str, dict[str, Any]]]:
        if not self._path.exists():
            return True, {}
        try:
            payload = read_json(self._path, expect=dict)
        except (OSError, ValueError, TypeError):
            return False, {}
        if payload.get("version") != self.VERSION:
            return False, {}
        routes = payload.get("routes")
        if not isinstance(routes, dict):
            return False, {}
        return True, {
            key: dict(value)
            for key, value in routes.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    @staticmethod
    def _entry_route(entry: dict[str, Any]) -> TaskCardRoute | None:
        account = entry.get("account")
        chat_id = entry.get("chat_id")
        thread_id = entry.get("thread_id")
        if not isinstance(account, str) or not account:
            return None
        if not isinstance(chat_id, str) or not chat_id:
            return None
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
            return None
        return TaskCardRoute(account, chat_id, thread_id)

    def get(self, route: TaskCardRoute) -> str | None:
        valid, routes = self._read()
        if not valid:
            return "invalid-resident-state"
        entry = routes.get(route.key)
        if entry is None:
            return None
        resident_id = entry.get("resident_id")
        stored_route = self._entry_route(entry)
        if not isinstance(resident_id, str) or not resident_id:
            return "invalid-resident-state"
        return resident_id if stored_route == route else "invalid-resident-state"

    def contains(self, resident_id: str) -> bool | None:
        valid, routes = self._read()
        if not valid:
            return None
        return any(entry.get("resident_id") == resident_id for entry in routes.values())

    def routes(self) -> list[tuple[TaskCardRoute, str]]:
        valid, entries = self._read()
        if not valid:
            return []
        result: list[tuple[TaskCardRoute, str]] = []
        for key, entry in entries.items():
            route = self._entry_route(entry)
            resident_id = entry.get("resident_id")
            if (
                route is not None
                and route.key == key
                and isinstance(resident_id, str)
                and resident_id
            ):
                result.append((route, resident_id))
        return result

    def set(self, route: TaskCardRoute, resident_id: str) -> bool:
        if not isinstance(resident_id, str) or not resident_id:
            return False
        with self._lock:
            valid, routes = self._read()
            if not valid:
                return False
            routes[route.key] = {
                "account": route.account,
                "chat_id": str(route.chat_id),
                "thread_id": (
                    str(route.thread_id) if route.thread_id is not None else None
                ),
                "resident_id": resident_id,
            }
            try:
                atomic_write_json(
                    self._path,
                    {"version": self.VERSION, "routes": routes},
                    fsync=True,
                )
            except OSError:
                return False
            return True


class FeishuTaskCardJournal:
    """Tail canonical agent events and notify the Feishu resident projector."""

    POLL_INTERVAL = 1.0
    TAIL_CHUNK = 65_536

    def __init__(self, path: Path, on_change: Callable[[], None]) -> None:
        self._path = Path(path)
        self._on_change = on_change
        self._groups: list[dict[str, Any]] = []
        self._metadata: dict[str, Any] | None = None
        self._offset = 0
        self._identity: tuple[str, int] | None = None
        self._exists = False
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _identity_for(stat: os.stat_result) -> tuple[str, int] | None:
        inode = getattr(stat, "st_ino", None)
        if isinstance(inode, int) and not isinstance(inode, bool) and inode:
            return "ino", inode
        return None

    @staticmethod
    def _project(
        data: bytes,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, Any] | None,
        dict[str, dict[str, Any]],
    ]:
        projected: list[tuple[dict[str, Any], dict[str, Any]]] = []
        results: dict[str, dict[str, Any]] = {}
        metadata: dict[str, Any] | None = None
        for raw in data.split(b"\n"):
            event = TaskCardEventProjection.decode_event_line(raw)
            if event is None:
                continue
            call_id = event.get("tool_call_id")
            if (
                event.get("type") == "tool_result"
                and isinstance(call_id, str)
                and call_id
            ):
                results[call_id] = event
            row = TaskCardEventProjection.project_event(event)
            if row is not None:
                projected.append((event, row))
            candidate = TaskCardEventProjection.project_final_carrier_metadata(event)
            if candidate is not None:
                metadata = candidate
        groups = TaskCardEventProjection.group_events(projected)
        TaskCardEventProjection.apply_tool_results(groups, results)
        return groups, metadata, results

    def _rehydrate(self) -> None:
        try:
            stat = self._path.stat()
        except OSError:
            with self._lock:
                self._groups = []
                self._metadata = None
                self._offset = 0
                self._identity = None
                self._exists = False
            return

        size = stat.st_size
        read_size = self.TAIL_CHUNK
        groups: list[dict[str, Any]] = []
        metadata: dict[str, Any] | None = None
        offset = size
        try:
            with self._path.open("rb") as handle:
                while True:
                    start = max(0, size - read_size)
                    handle.seek(start)
                    data = handle.read(size - start)
                    data_start = start
                    if start:
                        newline = data.find(b"\n")
                        if newline < 0:
                            data = b""
                        else:
                            data_start += newline + 1
                            data = data[newline + 1 :]
                    if data and not data.endswith(b"\n"):
                        newline = data.rfind(b"\n")
                        if newline < 0:
                            offset = data_start
                            complete = b""
                        else:
                            offset = data_start + newline + 1
                            complete = data[: newline + 1]
                    else:
                        offset = size
                        complete = data
                    groups, metadata, _results = self._project(complete)
                    if (
                        len(groups) >= TaskCardEventProjection.EVENT_WINDOW
                        or start == 0
                    ):
                        break
                    read_size *= 2
        except OSError:
            return

        with self._lock:
            self._groups = groups
            self._metadata = metadata
            self._offset = offset
            self._identity = self._identity_for(stat)
            self._exists = True

    def snapshot(self) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        with self._lock:
            groups = [
                {
                    "api_call_id": group.get("api_call_id"),
                    "events": [dict(row) for row in group.get("events", [])],
                }
                for group in self._groups
            ]
            metadata = dict(self._metadata) if self._metadata is not None else None
        return groups, metadata

    def poll_once(self) -> bool:
        try:
            stat = self._path.stat()
        except OSError:
            return False
        with self._lock:
            exists = self._exists
            offset = self._offset
            identity = self._identity
        current_identity = self._identity_for(stat)
        replaced = (
            identity is not None
            and current_identity is not None
            and identity != current_identity
        )
        if not exists or stat.st_size < offset or replaced:
            self._rehydrate()
            self._on_change()
            return True
        if stat.st_size <= offset:
            return False
        try:
            with self._path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(stat.st_size - offset)
        except OSError:
            return False
        newline = data.rfind(b"\n")
        if newline < 0:
            return False
        complete = data[: newline + 1]
        new_offset = offset + len(complete)
        projected, metadata, results = self._project(complete)

        with self._lock:
            combined: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for group in self._groups:
                group_id = group.get("api_call_id")
                combined.extend(
                    ({"api_call_id": group_id}, dict(row))
                    for row in group.get("events", [])
                )
            for group in projected:
                group_id = group.get("api_call_id")
                combined.extend(
                    ({"api_call_id": group_id}, dict(row))
                    for row in group.get("events", [])
                )
            old_groups = self._groups
            old_metadata = self._metadata
            self._groups = TaskCardEventProjection.group_events(combined)
            TaskCardEventProjection.apply_tool_results(self._groups, results)
            if metadata is not None:
                self._metadata = metadata
            self._offset = new_offset
            self._identity = current_identity or identity
            changed = self._groups != old_groups or self._metadata != old_metadata
        if changed:
            self._on_change()
        return changed

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._rehydrate()
        self._on_change()
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.wait(self.POLL_INTERVAL):
                try:
                    self.poll_once()
                except Exception as exc:  # noqa: BLE001
                    log.debug("Feishu Task Card journal poll failed: %s", exc)

        self._thread = threading.Thread(
            target=_loop,
            name="feishu-task-card-event-tail",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None


class FeishuProgrammableTaskCardPoller:
    """Read the intrinsic Task Card artifact and project valid intent."""

    POLL_INTERVAL = 1.0
    TEXT_LIMIT = TaskCardEventProjection.TEXT_LIMIT

    def __init__(
        self,
        working_dir: Path,
        *,
        on_active: Callable[[str], None],
        on_inactive: Callable[[], None],
    ) -> None:
        taskcard_dir = Path(working_dir) / "taskcard"
        self._status_path = taskcard_dir / "status"
        self._body_path = taskcard_dir / "taskcard.md"
        self._on_active = on_active
        self._on_inactive = on_inactive
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _read_status(self) -> str | None:
        try:
            return self._status_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _read_body(self) -> str | None:
        try:
            body = self._body_path.read_text(encoding="utf-8")
        except OSError:
            return None
        body = TaskCardEventProjection.sanitize_public_text(body)
        if not body.strip():
            return None
        return body[: self.TEXT_LIMIT]

    def poll_once(self) -> bool:
        """Dispatch one valid intent; malformed or incomplete state is a no-op."""
        status = self._read_status()
        if status == "inactive":
            self._on_inactive()
            return True
        if status != "active":
            return False
        body = self._read_body()
        if body is None:
            return False
        self._on_active(body)
        return True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            self.poll_once()
        except Exception as exc:  # noqa: BLE001
            log.debug("Initial Feishu programmable Task Card poll failed: %s", exc)

        def _loop() -> None:
            while not self._stop.wait(self.POLL_INTERVAL):
                try:
                    self.poll_once()
                except Exception as exc:  # noqa: BLE001
                    log.debug("Feishu programmable Task Card poll failed: %s", exc)

        self._thread = threading.Thread(
            target=_loop,
            name="feishu-task-card-programmable-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
