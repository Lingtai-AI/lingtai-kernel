"""Focused Feishu automatic resident Task Card coverage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.feishu.manager import FeishuManager
from lingtai.mcp_servers.feishu.task_card import FeishuTaskCardJournal
from lingtai.mcp_servers.task_card import TaskCardRoute


class _FakeAccount:
    alias = "main"

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._next_message = 0

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
        return {"message_id": message_id, "message_ids": [message_id]}

    def delete_message(self, message_id: str) -> bool:
        self.calls.append(("delete", message_id))
        return True


class _FakeService:
    def __init__(self, account: _FakeAccount) -> None:
        self.default_account = account
        self._account = account

    def get_account(self, alias: str) -> _FakeAccount:
        assert alias == self._account.alias
        return self._account


def _manager(
    tmp_path: Path, account: _FakeAccount | None = None
) -> tuple[FeishuManager, _FakeAccount]:
    account = account or _FakeAccount()
    return (
        FeishuManager(
            _FakeService(account),
            working_dir=tmp_path,
            on_inbound=lambda _event: None,
        ),
        account,
    )


def _state(tmp_path: Path) -> dict[str, Any]:
    return json.loads(
        (tmp_path / "feishu" / "task_cards.json").read_text(encoding="utf-8")
    )


def test_flat_resident_sends_once_then_edits_in_place(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat")
    manager._note_task_card_message(route, "main:oc_chat:om_inbound")

    created = manager._ensure_automatic_task_card(route)
    updated = manager._ensure_automatic_task_card(route)

    assert created == {"status": "ok", "message_id": "main:oc_chat:om_card_1"}
    assert updated == {"status": "ok", "message_id": "main:oc_chat:om_card_1"}
    assert [call[0] for call in account.calls] == ["send", "edit"]
    assert account.calls[0][3]["card"]["schema"] == "2.0"
    assert account.calls[1][2]["card"]["config"]["update_multi"] is True
    entry = _state(tmp_path)["routes"]["main:oc_chat"]
    assert entry == {
        "account": "main",
        "chat_id": "oc_chat",
        "thread_id": None,
        "resident_id": "main:oc_chat:om_card_1",
    }


def test_observed_newer_message_rotates_exact_old_card_first(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat")
    manager._note_task_card_message(route, "main:oc_chat:om_inbound")
    manager._ensure_automatic_task_card(route)
    account.calls.clear()

    manager._note_task_card_message(route, "main:oc_chat:om_user_newer")
    rotated = manager._ensure_automatic_task_card(route)

    assert rotated == {"status": "ok", "message_id": "main:oc_chat:om_card_2"}
    assert [call[0] for call in account.calls] == ["edit", "delete", "send"]
    assert account.calls[0][1] == "om_card_1"
    assert account.calls[1][1] == "om_card_1"
    assert _state(tmp_path)["routes"]["main:oc_chat"]["resident_id"] == (
        "main:oc_chat:om_card_2"
    )


def test_restart_with_unknown_high_water_edits_persisted_card(
    tmp_path: Path,
) -> None:
    first, first_account = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat")
    first._note_task_card_message(route, "main:oc_chat:om_inbound")
    first._ensure_automatic_task_card(route)

    restarted, restarted_account = _manager(tmp_path)
    outcome = restarted._ensure_automatic_task_card(route)

    assert outcome == {"status": "ok", "message_id": "main:oc_chat:om_card_1"}
    assert [call[0] for call in restarted_account.calls] == ["edit"]
    assert not any(call[0] in {"send", "delete"} for call in restarted_account.calls)
    assert [call[0] for call in first_account.calls] == ["send"]


def test_topic_resident_replies_to_exact_route_anchor_and_stays_hidden_from_read(
    tmp_path: Path,
) -> None:
    manager, account = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat", "omt_topic")
    anchor = "main:oc_chat:om_topic_message"
    manager._note_task_card_message(route, anchor)

    outcome = manager._ensure_automatic_task_card(route)

    assert outcome == {"status": "ok", "message_id": "main:oc_chat:om_card_1"}
    assert account.calls[0][:3] == ("reply", "om_topic_message", "oc_chat")
    assert account.calls[0][4] is True
    assert account.calls[0][3]["card"]["schema"] == "2.0"
    entry = _state(tmp_path)["routes"]["main:oc_chat:omt_topic"]
    assert entry["thread_id"] == "omt_topic"
    assert manager._list_messages("main", "sent") == []
    records = list((tmp_path / "feishu" / "main" / "sent").glob("*/message.json"))
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["task_card"] is True
    assert record["thread_id"] == "omt_topic"
    assert record["reply_to"] == anchor


def test_corrupt_resident_state_fails_closed_without_send(tmp_path: Path) -> None:
    state_path = tmp_path / "feishu" / "task_cards.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    manager, account = _manager(tmp_path)
    route = TaskCardRoute("main", "oc_chat")
    manager._note_task_card_message(route, "main:oc_chat:om_inbound")

    outcome = manager._ensure_automatic_task_card(route)

    assert outcome == {
        "status": "error",
        "error": "Failed to ensure Feishu Task Card",
    }
    assert account.calls == []


def test_journal_rehydrates_complete_lines_and_consumes_partial_later(
    tmp_path: Path,
) -> None:
    path = tmp_path / "logs" / "events.jsonl"
    path.parent.mkdir(parents=True)
    tool_call = {
        "type": "tool_call",
        "api_call_id": "api-1",
        "tool_call_id": "tool-1",
        "tool_name": "feishu",
        "tool_args": {"action": "read", "_reasoning": "inspect"},
    }
    result = {
        "type": "tool_result",
        "tool_call_id": "tool-1",
        "status": "success",
        "elapsed_ms": 1250,
    }
    partial = {"type": "diary", "api_call_id": "api-2", "text": "done"}
    path.write_bytes(
        json.dumps(tool_call).encode()
        + b"\n"
        + json.dumps(result).encode()
        + b"\n"
        + json.dumps(partial).encode()
    )
    changes: list[bool] = []
    journal = FeishuTaskCardJournal(path, lambda: changes.append(True))

    journal.start()
    groups, _metadata = journal.snapshot()
    path.write_bytes(path.read_bytes() + b"\n")
    changed = journal.poll_once()
    completed_groups, _metadata = journal.snapshot()
    journal.stop()

    assert groups[0]["events"][0]["status"] == "success"
    assert groups[0]["events"][0]["elapsed_s"] == 1.25
    assert all(row.get("text") != "done" for row in groups[0]["events"])
    assert changed is True
    assert completed_groups[-1]["events"] == [{"kind": "text", "text": "done"}]
    assert len(changes) == 2


def test_journal_tool_result_append_updates_existing_group(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "events.jsonl"
    path.parent.mkdir(parents=True)
    tool_call = {
        "type": "tool_call",
        "api_call_id": "api-1",
        "tool_call_id": "tool-1",
        "tool_name": "feishu",
        "tool_args": {"_reasoning": "inspect"},
    }
    path.write_text(json.dumps(tool_call) + "\n", encoding="utf-8")
    changes: list[bool] = []
    journal = FeishuTaskCardJournal(path, lambda: changes.append(True))
    journal.start()
    before, _metadata = journal.snapshot()

    result = {
        "type": "tool_result",
        "tool_call_id": "tool-1",
        "status": "error",
        "elapsed_ms": 500,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result) + "\n")
    changed = journal.poll_once()
    after, _metadata = journal.snapshot()
    journal.stop()

    assert before[0]["events"][0]["status"] == "???"
    assert changed is True
    assert after[0]["events"][0]["status"] == "error"
    assert after[0]["events"][0]["elapsed_s"] == 0.5
    assert len(changes) == 2
