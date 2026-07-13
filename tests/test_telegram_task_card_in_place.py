"""Focused regressions for stable, in-place Telegram Task Card delivery."""

from __future__ import annotations

from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class FakeAccount:
    alias = "mybot"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.messages: dict[int, str] = {}
        self.resident: dict[int, str] = {}
        self.next_id = 100
        self.edit_error: Exception | None = None

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        message_id = self.next_id
        self.next_id += 1
        self.messages[message_id] = text
        self.calls.append(("send", chat_id, message_id, text))
        return {"message_id": message_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.calls.append(("edit", chat_id, message_id, text))
        if self.edit_error is not None:
            raise self.edit_error
        self.messages[message_id] = text
        return {"ok": True}

    def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", chat_id, message_id))
        self.messages.pop(message_id, None)
        return {"ok": True}

    def get_task_card(self, chat_id):
        return self.resident.get(chat_id)

    def set_task_card(self, chat_id, compound_id):
        self.resident[chat_id] = compound_id


class FakeService:
    def __init__(self, account: FakeAccount) -> None:
        self.default_account = account

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account


def _manager(tmp_path):
    account = FakeAccount()
    manager = TelegramManager(
        FakeService(account),
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, account


def _automatic(manager, sub_action, *, card_message_id=None, reasoning="work", done=False):
    args = {
        "sub_action": sub_action,
        "account": "mybot",
        "chat_id": 55,
        "rows": [{
            "tool": "bash",
            "tool_action": "run",
            "reasoning": reasoning,
            "elapsed_s": 1,
            "done": done,
        }],
    }
    if card_message_id is not None:
        args["card_message_id"] = card_message_id
    return manager._handle_task_card_update(args)


def _programmable(manager, card):
    return manager._handle_task_card_update({
        "sub_action": "update",
        "channel": "programmable",
        "account": "mybot",
        "chat_id": 55,
        "card": card,
    })


def _calls(account, kind):
    return [call for call in account.calls if call[0] == kind]


def test_repeated_automatic_and_programmable_updates_keep_one_message_id(tmp_path):
    manager, account = _manager(tmp_path)

    created = _automatic(manager, "create", reasoning="first")
    resident_id = created["message_id"]
    assert _programmable(manager, {"lines": ["watch-v1"]})["message_id"] == resident_id
    assert _automatic(
        manager, "update", card_message_id=resident_id, reasoning="second"
    )["message_id"] == resident_id
    assert _programmable(manager, {"lines": ["watch-v2"]})["message_id"] == resident_id

    assert len(_calls(account, "send")) == 1
    assert not _calls(account, "delete")
    final_text = account.messages[100]
    assert "second" in final_text
    assert "watch-v2" in final_text


def test_unchanged_heartbeat_and_final_are_successful_noop_edits(tmp_path):
    manager, account = _manager(tmp_path)
    created = _automatic(manager, "create", reasoning="steady")
    resident_id = created["message_id"]

    # Telegram reports an identical heartbeat/final render as a 400 no-op. It is
    # evidence that the resident already has the proposed content, not evidence
    # that the message is uneditable.
    account.edit_error = RuntimeError(
        "Telegram API error: Bad Request: message is not modified: specified new "
        "message content and reply markup are exactly the same as the current content"
    )
    heartbeat = _automatic(
        manager, "update", card_message_id=resident_id, reasoning="steady"
    )
    final = _automatic(
        manager, "finalize", card_message_id=resident_id, reasoning="steady"
    )

    assert heartbeat == {"status": "ok", "message_id": resident_id}
    assert final == {"status": "ok", "message_id": resident_id}
    assert len(_calls(account, "send")) == 1
    assert not _calls(account, "delete")
    assert account.get_task_card(55) == resident_id


def test_transient_edit_failure_fails_loud_without_replacement_or_state_commit(tmp_path):
    manager, account = _manager(tmp_path)
    created = _automatic(manager, "create", reasoning="delivered")
    resident_id = created["message_id"]
    committed = manager._task_card_channels["mybot:55"]["automatic"]

    account.edit_error = RuntimeError("connection reset")
    failed = _automatic(
        manager, "update", card_message_id=resident_id, reasoning="UNSENT"
    )

    assert failed["status"] == "error"
    assert len(_calls(account, "send")) == 1
    assert not _calls(account, "delete")
    assert account.get_task_card(55) == resident_id
    assert manager._task_card_channels["mybot:55"]["automatic"] == committed
    assert "UNSENT" not in committed


def test_edit_impossible_replaces_only_after_failed_edit_attempt(tmp_path):
    manager, account = _manager(tmp_path)
    created = _automatic(manager, "create", reasoning="first")
    stale_id = created["message_id"]

    account.edit_error = RuntimeError(
        "Telegram API error: Bad Request: message to edit not found"
    )
    recovered = _automatic(
        manager, "update", card_message_id=stale_id, reasoning="replacement"
    )

    assert recovered["status"] == "ok"
    assert recovered["message_id"] != stale_id
    assert [call[0] for call in account.calls[-3:]] == ["edit", "send", "delete"]
    assert account.get_task_card(55) == recovered["message_id"]
