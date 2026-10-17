"""Telegram mirror selection must preserve history, not filter by read IDs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from lingtai.mcp_servers.telegram.mirror_index import MirrorIndex
from tests.test_telegram_task_card_transport import _make_manager


def _message(root, n, *, folder="inbox", chat=7, date=None, **extra):
    directory = root / "telegram/mybot" / folder / str(uuid4())
    directory.mkdir(parents=True)
    data = {"id": f"mybot:{chat}:{n}", "date": date or f"2026-10-{n:02d}",
            "text": f"message {n}" + "x" * 120,
            "from": {"first_name": "sender", "username": "user"},
            "chat": {"id": chat, "type": "private"},
            "telegram": {"update": {"message": {"text": "raw untouched"}}}}
    if folder == "sent":
        data.pop("chat")
        data.pop("from")
        data["to"] = {"chat_id": chat}
    data.update(extra)
    path = directory / "message.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def _count_reads(monkeypatch):
    paths = []
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path.name == "message.json":
            paths.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    return paths


def test_check_keeps_read_conversations_and_uses_live_ledger(tmp_path, monkeypatch):
    manager, _ = _make_manager(tmp_path)
    _, one = _message(tmp_path, 1)
    _, two = _message(tmp_path, 2, folder="sent")
    _, three = _message(tmp_path, 3, chat=8)
    ledger = tmp_path / "telegram/mybot/read.json"
    ledger.write_text(json.dumps([three["id"]]))
    reads = _count_reads(monkeypatch)
    result = manager._check({})
    assert len(reads) == 3
    assert result["total"] == 3
    a, b = result["messages"]
    assert (a["chat_id"], a["unread"], a["total"]) == (8, 0, 1)
    assert (b["chat_id"], b["unread"], b["total"]) == (7, 1, 2)
    assert b["last_from"] == {"is_bot": True}
    assert b["last_text"] == two["text"][:100]
    reads.clear()
    assert manager._check({}) == result
    assert reads == []
    ledger.write_text(json.dumps([one["id"], three["id"]]))
    assert manager._check({})["messages"][1]["unread"] == 0
    assert reads == []
    result["messages"][0]["last_from"]["username"] = "mutated"
    assert manager._check({})["messages"][0]["last_from"]["username"] == "user"


@pytest.mark.parametrize("limit", [0, 1, 2, 10, -1])
def test_read_selects_full_bodies_and_preserves_ledger(tmp_path, monkeypatch, limit):
    manager, _ = _make_manager(tmp_path)
    rows = [_message(tmp_path, 1, media={"type": "document"},
                     callback_query={"data": "click"}, reply_to_message_id=9,
                     update_type="message", synthetic=False, unmatched_edit=False,
                     voice_transcript="hello"),
            _message(tmp_path, 2, folder="sent"), _message(tmp_path, 3, chat=8)]
    manager._check({})
    selected = [rows[1], rows[0]][:limit]
    reads = _count_reads(monkeypatch)
    result = manager._read({"chat_id": 7, "limit": limit})
    assert reads == [path for path, _ in selected]
    assert [m["id"] for m in result["messages"]] == [data["id"] for _, data in selected]
    for actual, (_, data) in zip(result["messages"], selected):
        for key in ("id", "date", "text", "from", "to", "chat", "telegram", "media",
                    "callback_query", "reply_to_message_id"):
            assert actual[key] == data.get(key)
        for key in ("update_type", "synthetic", "unmatched_edit", "voice_transcript"):
            if key in data:
                assert actual[key] == data[key]
        assert actual["_direction"] == ("outgoing" if data.get("to") else "incoming")
    ledger = tmp_path / "telegram/mybot/read.json"
    assert (json.loads(ledger.read_text()) if ledger.exists() else []) == sorted(
        data["id"] for _, data in selected)


def test_id_lookup_reopens_one_body_and_preserves_raw_fields(tmp_path, monkeypatch):
    manager, _ = _make_manager(tmp_path)
    inbox, data = _message(tmp_path, 1, extra_field={"custom": "kept"})
    sent, outgoing = _message(tmp_path, 2, folder="sent")
    manager._check({})
    reads = _count_reads(monkeypatch)
    assert manager._find_message_by_compound_id("mybot", outgoing["id"]) == {
        **outgoing, "_folder": "sent"}
    assert reads == [sent]
    reads.clear()
    assert manager._find_message_by_compound_id("mybot", data["id"]) == {**data, "_folder": "inbox"}
    assert reads == [inbox]
    reads.clear()
    assert manager._find_inbox_by_compound_id("mybot", data["id"]) == inbox.parent
    assert manager._find_message_by_compound_id("mybot", "missing") is None
    assert reads == []


def test_changed_added_deleted_corrupt_recovered_and_replaced(tmp_path, monkeypatch):
    manager, _ = _make_manager(tmp_path)
    path, data = _message(tmp_path, 1)
    manager._check({})
    reads = _count_reads(monkeypatch)
    data["text"] = "a changed preview"
    path.write_text(json.dumps(data))
    assert manager._check({})["messages"][0]["last_text"] == data["text"]
    assert reads == [path]
    reads.clear()
    new, _ = _message(tmp_path, 2)
    assert manager._check({})["total"] == 2
    assert reads == [new]
    path.unlink()
    assert manager._check({})["total"] == 1
    new.write_text("{")
    assert manager._check({})["total"] == 0
    new.write_text(json.dumps(data))
    assert manager._check({})["total"] == 1
    replacement = new.with_name("replacement.json")
    data["text"] = "replacement"
    replacement.write_text(json.dumps(data))
    os.replace(replacement, new)
    assert manager._check({})["messages"][0]["last_text"] == "replacement"
    new.rename(new.with_name("moved.json"))
    assert manager._check({})["total"] == 0


def test_explicit_write_invalidation_even_if_signature_is_unchanged(tmp_path, monkeypatch):
    index = MirrorIndex()
    path, data = _message(tmp_path, 1)
    folder = path.parent.parent
    signature = index._signature(path)
    monkeypatch.setattr(index, "_signature", lambda _: signature)
    index.records(folder)
    with index.writing(path):
        data["text"] = "internal edit"
        path.write_text(json.dumps(data))
    assert index.records(folder)[0]["text"] == "internal edit"
    with pytest.raises(RuntimeError), index.writing(path):
        data["text"] = "write then raise"
        path.write_text(json.dumps(data))
        raise RuntimeError("after write")
    assert index.records(folder)[0]["text"] == "write then raise"


def test_metadata_does_not_retain_raw_envelopes_or_full_text(tmp_path):
    path, data = _message(tmp_path, 1)
    index = MirrorIndex()
    view = index.records(path.parent.parent)
    assert set(view[0]) == {"id", "date", "from", "chat", "text", "_dir"}
    assert len(view[0]["text"]) == 100
    view[0]["from"]["username"] = "polluted"
    assert index.records(path.parent.parent)[0]["from"] == data["from"]


def test_tie_order_and_duplicate_id_match_legacy_enumeration(tmp_path):
    manager, _ = _make_manager(tmp_path)
    a, data = _message(tmp_path, 1, date="same")
    b, _ = _message(tmp_path, 2, date="same", id=data["id"])
    _message(tmp_path, 3, folder="sent", date="same", id=data["id"])
    legacy = manager._list_messages("mybot", "inbox")
    assert [m["_dir"] for m in manager._message_summaries("mybot", "inbox")] == [
        m["_dir"] for m in legacy]
    assert manager._find_inbox_by_compound_id("mybot", data["id"]) == Path(legacy[0]["_dir"])
    assert manager._find_message_by_compound_id("mybot", data["id"])["text"] == legacy[0]["text"]


def test_search_remains_full_scan_and_inbox_only(tmp_path, monkeypatch):
    manager, _ = _make_manager(tmp_path)
    inbox, data = _message(tmp_path, 1)
    _message(tmp_path, 2, folder="sent")
    manager._check({})
    reads = _count_reads(monkeypatch)
    result = manager._search({"query": "USER"})
    assert result["total"] == 1
    assert result["messages"][0]["text"] == data["text"]
    assert result["messages"][0]["telegram"] == data["telegram"]
    assert reads == [inbox]


def test_account_isolation_missing_folder_and_invalid_regex(tmp_path, monkeypatch):
    manager, _ = _make_manager(tmp_path)
    _message(tmp_path, 1)
    assert manager._message_summaries("other", "inbox") == []
    reads = _count_reads(monkeypatch)
    assert "error" in manager._search({"query": "["})
    assert manager._read({}) == {"error": "chat_id is required"}
    assert reads == []


def test_real_incoming_edit_invalidates_coarse_signature(tmp_path, monkeypatch):
    manager, _ = _make_manager(tmp_path)
    path, data = _message(tmp_path, 1)
    signature = manager._mirror_index._signature(path)
    monkeypatch.setattr(manager._mirror_index, "_signature", lambda _: signature)
    manager._check({})
    manager.on_incoming("mybot", {
        "update_id": 200,
        "edited_message": {"message_id": 1, "chat": {"id": 7, "type": "private"},
                           "from": {"id": 99, "first_name": "sender"},
                           "date": 1700000000, "edit_date": 1800000000,
                           "text": "new live edit"},
    })
    assert manager._check({})["messages"][0]["last_text"] == "new live edit"
    result = manager._read({"chat_id": 7})
    assert len(result["messages"]) == 1
    assert result["messages"][0]["telegram"]["edits"][0]["update_id"] == 200


def test_real_sent_write_visible_after_warm_check(tmp_path):
    manager, _ = _make_manager(tmp_path)
    _message(tmp_path, 1)
    assert manager._check({})["total"] == 1
    result = manager._send({"chat_id": 7, "text": "sent now"})
    assert result["status"] == "sent"
    assert manager._check({})["total"] == 2
    assert manager._find_message_by_compound_id("mybot", result["message_id"])["text"] == "sent now"


def test_concurrent_readers_do_not_mutate_cached_metadata(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    manager, _ = _make_manager(tmp_path)
    _message(tmp_path, 1)
    expected = manager._check({})
    def check(_):
        result = manager._check({})
        assert result == expected
        result["messages"][0]["last_from"]["first_name"] = "caller local"
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(check, range(20)))
    assert manager._check({}) == expected


def test_external_write_during_parse_not_cached(tmp_path, monkeypatch):
    path, data = _message(tmp_path, 1)
    index = MirrorIndex()
    original = Path.read_text
    changed = False
    def read_then_change(p, *args, **kwargs):
        nonlocal changed
        value = original(p, *args, **kwargs)
        if p == path and not changed:
            changed = True
            data["text"] = "external edit after read"
            path.write_text(json.dumps(data))
        return value
    monkeypatch.setattr(Path, "read_text", read_then_change)
    index.records(path.parent.parent)
    assert index.records(path.parent.parent)[0]["text"] == data["text"]
