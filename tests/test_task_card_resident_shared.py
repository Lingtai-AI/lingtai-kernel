"""Provider-neutral resident Task Card delivery state-machine coverage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lingtai.mcp_servers.task_card import (
    TaskCardResident,
    TaskCardResidentTransport,
    TaskCardRoute,
)
from lingtai.mcp_servers.telegram.task_card.resident import (
    TaskCardResident as TelegramCompatResident,
)


@dataclass
class _Harness:
    resident_id: str | None = None
    superseded: bool = False
    edit_results: list[tuple[str, str | None]] = field(default_factory=list)
    delete_result: str = TaskCardResident.DELETE_OK
    send_result: dict[str, Any] | None = field(
        default_factory=lambda: {"status": "sent", "message_id": "new#2"}
    )
    persist_result: bool = True
    peer_after_delete: str | None = None
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def transport(self) -> TaskCardResidentTransport:
        return TaskCardResidentTransport(
            get_resident=self.get_resident,
            matches_route=self.matches_route,
            is_superseded=self.is_superseded,
            edit=self.edit,
            delete=self.delete,
            send=self.send,
            persist=self.persist,
        )

    def get_resident(self, route: TaskCardRoute) -> str | None:
        self.calls.append(("get", route))
        return self.resident_id

    def matches_route(self, route: TaskCardRoute, resident_id: str) -> bool:
        self.calls.append(("matches", route, resident_id))
        return resident_id.startswith(f"{route.key}#")

    def is_superseded(self, route: TaskCardRoute, resident_id: str) -> bool:
        self.calls.append(("superseded", route, resident_id))
        return self.superseded

    def edit(self, resident_id: str, text: str) -> tuple[str, str | None]:
        self.calls.append(("edit", resident_id, text))
        if self.edit_results:
            return self.edit_results.pop(0)
        return TaskCardResident.EDIT_OK, None

    def delete(self, resident_id: str) -> str:
        self.calls.append(("delete", resident_id))
        if self.peer_after_delete is not None:
            self.resident_id = self.peer_after_delete
        return self.delete_result

    def send(self, route: TaskCardRoute, text: str) -> dict[str, Any] | None:
        self.calls.append(("send", route, text))
        return self.send_result

    def persist(self, route: TaskCardRoute, resident_id: str) -> bool:
        self.calls.append(("persist", route, resident_id))
        self.resident_id = resident_id
        return self.persist_result


def _resident(harness: _Harness) -> TaskCardResident:
    return TaskCardResident(enabled=True, transport=harness.transport())


def test_shared_route_includes_thread_and_telegram_import_is_compatible() -> None:
    route = TaskCardResident.route("main", "chat", "thread")

    assert route == TaskCardRoute("main", "chat", "thread")
    assert route.key == "main:chat:thread"
    assert TaskCardResident.key("main", 55) == "main:55"
    assert TelegramCompatResident is TaskCardResident


def test_initial_send_and_in_place_slot_compose_commit_after_success() -> None:
    harness = _Harness(
        send_result={
            "status": "sent",
            "message_id": "main:chat:thread#1",
        }
    )
    resident = _resident(harness)

    created = resident.project(
        "main",
        "chat",
        "automatic",
        "automatic-v1",
        error="failed",
        thread_id="thread",
    )
    updated = resident.project(
        "main",
        "chat",
        "programmable",
        "watch-v1",
        error="failed",
        thread_id="thread",
    )

    assert created == {"status": "ok", "message_id": "main:chat:thread#1"}
    assert updated == {"status": "ok", "message_id": "main:chat:thread#1"}
    assert resident.frames["main:chat:thread"] == {
        "automatic": "automatic-v1",
        "programmable": "watch-v1",
    }
    assert ("send", TaskCardRoute("main", "chat", "thread"), "automatic-v1") in (
        harness.calls
    )
    assert (
        "edit",
        "main:chat:thread#1",
        "automatic-v1\n\n— TASK CARD —\nwatch-v1",
    ) in harness.calls


def test_failed_edit_preserves_committed_frame_and_never_sends() -> None:
    harness = _Harness(
        resident_id="main:chat#1",
        edit_results=[(TaskCardResident.EDIT_FAILED, "safe failure")],
    )
    resident = _resident(harness)
    resident.set_frame("main", "chat", "automatic", "automatic-v1")

    outcome = resident.project(
        "main",
        "chat",
        "automatic",
        "automatic-v2",
        error="failed",
    )

    assert outcome == {"status": "error", "error": "safe failure"}
    assert resident.frames["main:chat"]["automatic"] == "automatic-v1"
    assert not any(call[0] in {"delete", "send", "persist"} for call in harness.calls)


def test_superseded_rotation_is_probe_delete_send_persist_old_first() -> None:
    harness = _Harness(
        resident_id="main:chat#1",
        superseded=True,
        send_result={"status": "sent", "message_id": "main:chat#2"},
    )
    resident = _resident(harness)
    resident.set_frame("main", "chat", "automatic", "automatic-v1")

    outcome = resident.project(
        "main",
        "chat",
        "automatic",
        "automatic-v2",
        error="failed",
    )

    assert outcome == {"status": "ok", "message_id": "main:chat#2"}
    operations = [call[0] for call in harness.calls]
    assert operations == [
        "get",
        "matches",
        "superseded",
        "edit",
        "delete",
        "get",
        "send",
        "persist",
    ]
    assert harness.calls[3] == ("edit", "main:chat#1", "automatic-v1")
    assert harness.calls[4] == ("delete", "main:chat#1")
    assert harness.calls[6] == (
        "send",
        TaskCardRoute("main", "chat"),
        "automatic-v2",
    )
    assert resident.frames["main:chat"]["automatic"] == "automatic-v2"


def test_delete_failure_blocks_replacement_and_preserves_committed_frame() -> None:
    harness = _Harness(
        resident_id="main:chat#1",
        superseded=True,
        delete_result=TaskCardResident.DELETE_FAILED,
    )
    resident = _resident(harness)
    resident.set_frame("main", "chat", "automatic", "automatic-v1")

    outcome = resident.project(
        "main",
        "chat",
        "automatic",
        "automatic-v2",
        error="failed",
    )

    assert outcome == {
        "status": "error",
        "error": "failed",
        "stale_delete_failed": True,
    }
    assert resident.frames["main:chat"]["automatic"] == "automatic-v1"
    assert not any(call[0] in {"send", "persist"} for call in harness.calls)


def test_send_failure_after_delete_reports_zero_resident_risk() -> None:
    harness = _Harness(
        resident_id="main:chat#1",
        superseded=True,
        send_result=None,
    )
    resident = _resident(harness)
    resident.set_frame("main", "chat", "automatic", "automatic-v1")

    outcome = resident.project(
        "main",
        "chat",
        "automatic",
        "automatic-v2",
        error="failed",
    )

    assert outcome == {
        "status": "error",
        "error": "failed",
        "old_resident_deleted": True,
    }
    assert resident.frames["main:chat"]["automatic"] == "automatic-v1"
    assert not any(call[0] == "persist" for call in harness.calls)


def test_peer_resident_is_adopted_without_duplicate_send() -> None:
    harness = _Harness(
        resident_id="main:chat#1",
        superseded=True,
        peer_after_delete="main:chat#2",
    )
    resident = _resident(harness)
    resident.set_frame("main", "chat", "automatic", "automatic-v1")

    outcome = resident.project(
        "main",
        "chat",
        "automatic",
        "automatic-v2",
        error="failed",
    )

    assert outcome == {
        "status": "ok",
        "message_id": "main:chat#2",
        "adopted_resident": True,
    }
    assert not any(call[0] in {"send", "persist"} for call in harness.calls)
    assert resident.frames["main:chat"]["automatic"] == "automatic-v2"


def test_suppressed_programmable_clear_commits_without_transport() -> None:
    harness = _Harness(resident_id="main:chat#1")
    resident = TaskCardResident(enabled=False, transport=harness.transport())
    resident.set_frame("main", "chat", "automatic", "automatic-v1")
    resident.set_frame("main", "chat", "programmable", "watch-v1")

    outcome = resident.project(
        "main",
        "chat",
        "programmable",
        None,
        error="failed",
    )

    assert outcome == {"status": "ok", "suppressed": True, "taskcard": False}
    assert resident.frames["main:chat"] == {"automatic": "automatic-v1"}
    assert harness.calls == []
