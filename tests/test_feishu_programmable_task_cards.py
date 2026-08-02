"""Focused Feishu programmable resident Task Card coverage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lingtai.mcp_servers.feishu.manager import FeishuManager
from lingtai.mcp_servers.feishu.task_card import (
    FeishuProgrammableTaskCardPoller,
)
from lingtai.mcp_servers.task_card import TaskCardRoute


class _FakeAccount:
    alias = "main"

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._next_message = 0
        self.fail_edits: set[str] = set()

    def _result(self, chat_id: str, *, thread_id: str = "") -> dict[str, Any]:
        self._next_message += 1
        message_id = f"om_card_{self._next_message}"
        return {
            "message_id": message_id,
            "message_ids": [message_id],
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

    def send_content(
        self, receive_id: str, receive_id_type: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("send", receive_id, receive_id_type, message))
        return self._result(receive_id)

    def reply_content(
        self,
        message_id: str,
        chat_id: str,
        message: dict[str, Any],
        *,
        reply_in_thread: bool,
    ) -> dict[str, Any]:
        self.calls.append(("reply", message_id, chat_id, message, reply_in_thread))
        return self._result(chat_id, thread_id="omt_topic")

    def update_content(
        self, message_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("edit", message_id, message))
        if message_id in self.fail_edits:
            raise RuntimeError("injected route failure")
        return {"message_id": message_id, "message_ids": [message_id]}

    def delete_message(self, message_id: str) -> bool:
        self.calls.append(("delete", message_id))
        return True


class _FakeService:
    def __init__(self, account: _FakeAccount) -> None:
        self.default_account = account
        self._account = account
        self.calls: list[str] = []

    def get_account(self, alias: str) -> _FakeAccount:
        assert alias == self._account.alias
        return self._account

    def list_accounts(self) -> list[str]:
        return [self._account.alias]

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")


def _manager(
    tmp_path: Path,
) -> tuple[FeishuManager, _FakeAccount, _FakeService]:
    account = _FakeAccount()
    service = _FakeService(account)
    manager = FeishuManager(
        service,
        working_dir=tmp_path,
        on_inbound=lambda _event: None,
    )
    manager._task_card_active = True
    return manager, account, service


def _seed_route(manager: FeishuManager, route: TaskCardRoute) -> str:
    anchor = f"{route.account}:{route.chat_id}:om_anchor"
    manager._note_task_card_message(route, anchor)
    outcome = manager._ensure_automatic_task_card(route)
    assert outcome["status"] == "ok"
    return outcome["message_id"].rsplit(":", 1)[-1]


def _write_artifact(tmp_path: Path, status: str, body: str | None = None) -> None:
    taskcard_dir = tmp_path / "taskcard"
    taskcard_dir.mkdir(parents=True, exist_ok=True)
    (taskcard_dir / "status").write_text(status, encoding="utf-8")
    if body is not None:
        (taskcard_dir / "taskcard.md").write_text(body, encoding="utf-8")


def _card_frame(call: tuple[Any, ...]) -> str:
    return call[2]["card"]["body"]["elements"][0]["content"]


def test_active_body_composes_with_automatic_and_updates_diff_only(
    tmp_path: Path,
) -> None:
    manager, account, _service = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat")
    resident_message = _seed_route(manager, route)
    account.calls.clear()
    _write_artifact(tmp_path, "active", "one truthful frame")

    assert manager._programmable_task_card_poller.poll_once() is True
    assert manager._programmable_task_card_poller.poll_once() is True

    assert [call[0] for call in account.calls] == ["edit"]
    assert account.calls[0][1] == resident_message
    frame = _card_frame(account.calls[0])
    assert "📋 TASK CARD" in frame
    assert frame.endswith("— WATCH —\none truthful frame")
    assert manager._resident.frames[route.key]["programmable"] == ("one truthful frame")


def test_exact_inactive_clears_only_programmable_slot_idempotently(
    tmp_path: Path,
) -> None:
    manager, account, _service = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat", "omt_topic")
    _seed_route(manager, route)
    _write_artifact(tmp_path, "active", "watch body")
    manager._programmable_task_card_poller.poll_once()
    automatic = manager._resident.frames[route.key]["automatic"]
    account.calls.clear()

    _write_artifact(tmp_path, "inactive", "ignored stale body")
    assert manager._programmable_task_card_poller.poll_once() is True
    assert manager._programmable_task_card_poller.poll_once() is True

    assert [call[0] for call in account.calls] == ["edit"]
    assert _card_frame(account.calls[0]) == automatic
    assert manager._resident.frames[route.key] == {"automatic": automatic}


def test_invalid_or_blank_artifact_preserves_last_committed_frame(
    tmp_path: Path,
) -> None:
    manager, account, _service = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat")
    _seed_route(manager, route)
    _write_artifact(tmp_path, "active", "last committed")
    manager._programmable_task_card_poller.poll_once()
    account.calls.clear()

    _write_artifact(tmp_path, "paused", "replacement")
    assert manager._programmable_task_card_poller.poll_once() is False
    _write_artifact(tmp_path, "active", " \n")
    assert manager._programmable_task_card_poller.poll_once() is False

    assert account.calls == []
    assert manager._resident.frames[route.key]["programmable"] == ("last committed")


def test_body_is_bounded_before_projection(tmp_path: Path) -> None:
    frames: list[str] = []
    _write_artifact(
        tmp_path,
        "active",
        "x" * (FeishuProgrammableTaskCardPoller.TEXT_LIMIT + 20),
    )
    poller = FeishuProgrammableTaskCardPoller(
        tmp_path,
        on_active=frames.append,
        on_inactive=lambda: None,
    )

    assert poller.poll_once() is True
    assert len(frames) == 1
    assert len(frames[0]) == FeishuProgrammableTaskCardPoller.TEXT_LIMIT


def test_missing_or_unreadable_artifact_is_a_noop(tmp_path: Path) -> None:
    intents: list[str] = []
    poller = FeishuProgrammableTaskCardPoller(
        tmp_path,
        on_active=intents.append,
        on_inactive=lambda: intents.append("inactive"),
    )

    assert poller.poll_once() is False
    taskcard_dir = tmp_path / "taskcard"
    taskcard_dir.mkdir()
    (taskcard_dir / "status").write_text("active", encoding="utf-8")
    (taskcard_dir / "taskcard.md").mkdir()
    assert poller.poll_once() is False
    assert intents == []


def test_one_route_failure_does_not_block_other_routes(tmp_path: Path) -> None:
    manager, account, _service = _manager(tmp_path)
    failed_route = TaskCardRoute("main", "oc_failed")
    healthy_route = TaskCardRoute("main", "oc_healthy")
    failed_message = _seed_route(manager, failed_route)
    healthy_message = _seed_route(manager, healthy_route)
    account.fail_edits.add(failed_message)
    account.calls.clear()
    _write_artifact(tmp_path, "active", "broadcast body")

    manager._programmable_task_card_poller.poll_once()

    edit_messages = [call[1] for call in account.calls if call[0] == "edit"]
    assert edit_messages == [failed_message, healthy_message]
    assert "programmable" not in manager._resident.frames[failed_route.key]
    assert manager._resident.frames[healthy_route.key]["programmable"] == (
        "broadcast body"
    )


def test_lifecycle_starts_automatic_before_programmable_and_stops_reverse(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    manager, _account, service = _manager(tmp_path)
    order: list[str] = []
    monkeypatch.setattr(
        manager._task_card_journal, "start", lambda: order.append("journal.start")
    )
    monkeypatch.setattr(
        manager._task_card_journal, "stop", lambda: order.append("journal.stop")
    )
    monkeypatch.setattr(
        manager._programmable_task_card_poller,
        "start",
        lambda: order.append("programmable.start"),
    )
    monkeypatch.setattr(
        manager._programmable_task_card_poller,
        "stop",
        lambda: order.append("programmable.stop"),
    )

    manager.start()
    order.append("started")
    manager.stop()

    assert service.calls == ["start", "stop"]
    assert order == [
        "journal.start",
        "programmable.start",
        "started",
        "programmable.stop",
        "journal.stop",
    ]
