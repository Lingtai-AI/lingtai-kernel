import json
import sqlite3
import time
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.receipts import ReceiptStore
from lingtai.mcp_servers.telegram.task_card_revisions import TaskCardRevisionStore
from tests._notification_store_helpers import FakeNotificationStore


class _Account:
    alias = "bot"

    def __init__(self, failures=0):
        self.failures = failures
        self.reactions = []
        self.task_cards = {}

    def set_message_reaction(self, chat_id, message_id, reaction):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("offline")
        self.reactions.append((chat_id, message_id, reaction))

    def get_task_card(self, chat_id):
        return self.task_cards.get(str(chat_id))

    def set_task_card(self, chat_id, message_id):
        self.task_cards[str(chat_id)] = message_id

    def get_last_message_id(self, chat_id):
        return None

    def edit_message(self, chat_id, message_id, text, **kwargs):
        return {"ok": True}


class _Service:
    def __init__(self, account):
        self.default_account = account

    def get_account(self, alias):
        return self.default_account

    def list_accounts(self):
        return [self.default_account.alias]

    def taskcard_enabled(self):
        return True

    def taskcard_normal_rows(self):
        return 1


def _manager(path: Path, account: _Account) -> TelegramManager:
    return TelegramManager(
        _Service(account), working_dir=path, on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )


def test_receipt_survives_failure_and_restart(tmp_path: Path):
    events = tmp_path / "logs" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"notifications": {"persistent": {"mcp": {
            "telegram": {"messages": [{"id": "telegram:bot:123:55"}]},
        }}}}},
    }) + "\n", encoding="utf-8")

    first = _manager(tmp_path, _Account(failures=1))
    first._poll_receipt_events()
    first._receipt_worker.poll_once(now=time.time())
    assert first._ensure_receipt_store().get("telegram:bot:123:55")["state"] == "pending"

    healthy = _Account()
    restarted = _manager(tmp_path, healthy)
    restarted._receipt_worker = None
    restarted._ensure_receipt_store()
    restarted._receipt_worker.poll_once(now=time.time() + 3600)
    assert [(chat, message) for chat, message, _ in healthy.reactions] == [(123, 55)]
    assert restarted._ensure_receipt_store().get("telegram:bot:123:55")["state"] == "applied"


def test_receipt_and_cursor_are_atomic(tmp_path: Path):
    store = ReceiptStore(tmp_path / "receipts.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        store.enqueue_many_and_advance(["telegram:bot:1:2"], -1, 0, time.time())
    assert store.pending_due(time.time() + 1) == []
    assert store.cursor()[:2] == (0, 0)


def test_task_card_revisions_keep_only_the_latest_pending_text(tmp_path: Path):
    store = TaskCardRevisionStore(tmp_path / "taskcards.sqlite3")
    assert store.propose("bot:123", "第一版", 1.0)
    assert not store.propose("bot:123", "第一版", 2.0)
    assert store.propose("bot:123", "第二版", 3.0)
    assert store.pending_routes() == [("bot:123", 2, "第二版")]
    store.applied("bot:123", 4.0)
    assert store.pending_routes() == []
