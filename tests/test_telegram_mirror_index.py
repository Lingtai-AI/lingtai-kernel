"""Tests for the Telegram mirror sidecar index.

The mirror stores every message as ``<account>/<folder>/<dir>/message.json``.
Reading a folder used to stat+open every one of those files, which on slow
volumes costs 0.1-0.25s per message and blows the MCP tool timeout once a
mirror holds a few thousand messages.  These tests pin the sidecar-index
behaviour: an index built on first read, append-on-write, tolerant parsing,
fallback when absent, and no directory walk once the index exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram import manager as tg
from tests._notification_store_helpers import FakeNotificationStore

from lingtai.mcp_servers.telegram.manager import (
    TelegramManager,
    _tg_index_append_dir,
    _tg_index_path,
    _tg_index_rows,
)

ACCOUNT = "mybot"


class _FakeAccount:
    alias = ACCOUNT

    def __init__(self) -> None:
        self.calls: list = []

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        self.calls.append(("send_message", chat_id, text))
        return {"message_id": len(self.calls) + 100}


class _FakeService:
    def __init__(self, working_dir: Path) -> None:
        self._working_dir = Path(working_dir)
        self.default_account = _FakeAccount()

    def get_account(self, alias):
        assert alias == ACCOUNT
        return self.default_account

    def list_accounts(self):
        return [ACCOUNT]

    def account_details(self):
        return [{"alias": ACCOUNT}]

    def identity_path(self):
        return self._working_dir / "system" / "mcp_identities" / "telegram.json"


def _make_manager(tmp_path: Path) -> TelegramManager:
    return TelegramManager(
        _FakeService(tmp_path),
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )


def _write_message(
    tmp_path: Path,
    folder: str,
    msg_id: str,
    date: str,
    text: str,
    *,
    chat_id: int = 424242,
) -> Path:
    """Create one on-disk mirror message and return its directory."""
    msg_dir = tmp_path / "telegram" / ACCOUNT / folder / f"{msg_id.replace(':', '_')}"
    msg_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": msg_id,
        "date": date,
        "text": text,
        "chat": {"id": chat_id},
        "from": {"id": 1, "first_name": "Pucheng"},
    }
    (msg_dir / "message.json").write_text(json.dumps(payload), encoding="utf-8")
    return msg_dir


@pytest.fixture(autouse=True)
def _clear_index_cache():
    tg._TG_INDEX_CACHE.clear()
    yield
    tg._TG_INDEX_CACHE.clear()


def test_first_read_builds_index_and_matches_scan(tmp_path):
    _write_message(tmp_path, "inbox", f"{ACCOUNT}:424242:1", "2026-10-01T00:00:01Z", "one")
    _write_message(tmp_path, "inbox", f"{ACCOUNT}:424242:2", "2026-10-01T00:00:02Z", "two")
    _write_message(tmp_path, "inbox", f"{ACCOUNT}:424242:3", "2026-10-01T00:00:03Z", "three")

    mgr = _make_manager(tmp_path)
    index_path = _tg_index_path(tmp_path / "telegram" / ACCOUNT, "inbox")
    assert not index_path.exists()

    messages = mgr._list_messages(ACCOUNT, "inbox")

    assert [m["text"] for m in messages] == ["three", "two", "one"]
    # Reading a pre-index mirror migrates it, so no manual backfill is needed.
    assert index_path.exists()
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 3
    assert {row["text"] for row in rows} == {"one", "two", "three"}


def test_index_read_skips_directory_walk(tmp_path, monkeypatch):
    _write_message(tmp_path, "inbox", f"{ACCOUNT}:424242:1", "2026-10-01T00:00:01Z", "one")
    mgr = _make_manager(tmp_path)
    mgr._list_messages(ACCOUNT, "inbox")  # builds the index

    folder_dir = tmp_path / "telegram" / ACCOUNT / "inbox"
    real_iterdir = Path.iterdir

    def _guarded_iterdir(self):
        if Path(self) == folder_dir:
            raise AssertionError("indexed read must not walk the mirror directory")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _guarded_iterdir)
    tg._TG_INDEX_CACHE.clear()  # simulate a fresh process reading on-disk index

    messages = mgr._list_messages(ACCOUNT, "inbox")
    assert [m["text"] for m in messages] == ["one"]


def test_append_keeps_new_messages_visible(tmp_path):
    first = _write_message(tmp_path, "inbox", f"{ACCOUNT}:424242:1", "2026-10-01T00:00:01Z", "one")
    mgr = _make_manager(tmp_path)
    assert [m["text"] for m in mgr._list_messages(ACCOUNT, "inbox")] == ["one"]

    _write_message(tmp_path, "inbox", f"{ACCOUNT}:424242:2", "2026-10-01T00:00:02Z", "two")
    payload = json.loads((first / "message.json").read_text(encoding="utf-8"))
    payload.update({"id": f"{ACCOUNT}:424242:2", "date": "2026-10-01T00:00:02Z", "text": "two"})
    _tg_index_append_dir(tmp_path / "telegram" / ACCOUNT / "inbox" / "second", "inbox", payload)

    texts = [m["text"] for m in mgr._list_messages(ACCOUNT, "inbox")]
    assert texts == ["two", "one"]


def test_corrupt_index_lines_are_skipped(tmp_path):
    account_dir = tmp_path / "telegram" / ACCOUNT
    (account_dir / "inbox").mkdir(parents=True)
    index_path = _tg_index_path(account_dir, "inbox")
    good = {"id": f"{ACCOUNT}:424242:7", "date": "2026-10-01T00:00:07Z", "text": "good"}
    index_path.write_text(
        "not json at all\n" + json.dumps(good) + "\n{bad json\n",
        encoding="utf-8",
    )

    rows = _tg_index_rows(account_dir, "inbox")
    assert rows is not None
    assert [row["text"] for row in rows] == ["good"]

    mgr = _make_manager(tmp_path)
    assert [m["text"] for m in mgr._list_messages(ACCOUNT, "inbox")] == ["good"]


def test_find_message_by_compound_id_uses_index(tmp_path):
    _write_message(tmp_path, "inbox", f"{ACCOUNT}:424242:11", "2026-10-01T00:00:11Z", "hello")
    _write_message(tmp_path, "sent", f"{ACCOUNT}:424242:12", "2026-10-01T00:00:12Z", "reply")
    mgr = _make_manager(tmp_path)

    inbox_hit = mgr._find_message_by_compound_id(ACCOUNT, f"{ACCOUNT}:424242:11")
    sent_hit = mgr._find_message_by_compound_id(ACCOUNT, f"{ACCOUNT}:424242:12")
    missing = mgr._find_message_by_compound_id(ACCOUNT, f"{ACCOUNT}:424242:99")

    assert inbox_hit is not None and inbox_hit["text"] == "hello"
    assert inbox_hit["_folder"] == "inbox"
    assert sent_hit is not None and sent_hit["text"] == "reply"
    assert sent_hit["_folder"] == "sent"
    assert missing is None


def test_missing_index_falls_back_to_scan(tmp_path):
    account_dir = tmp_path / "telegram" / ACCOUNT
    (account_dir / "inbox").mkdir(parents=True)
    assert _tg_index_rows(account_dir, "inbox") is None
    assert _tg_index_rows(account_dir, "absent_folder") is None

    mgr = _make_manager(tmp_path)
    assert mgr._list_messages(ACCOUNT, "inbox") == []
    assert mgr._list_messages(ACCOUNT, "absent_folder") == []
