"""Hermetic read-count and behavior evidence for the unread mailbox fast path."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.kernel import notifications
from lingtai.tools.email import primitives as p
from lingtai.tools.email import manager as m


def agent_at(root):
    return SimpleNamespace(_working_dir=root, _agent_id="reader", _mailbox_tool="email",
                           _config=SimpleNamespace(language="en", time_awareness=False,
                                                   timezone_awareness=False),
                           _log=lambda *a, **kw: None)


def put(agent, mid, *, folder="inbox", **overrides):
    path = agent._working_dir / "mailbox" / folder / mid
    path.mkdir(parents=True, exist_ok=True)
    message = dict(_mailbox_id=mid, received_at="2026-10-15T12:00:00Z",
                   **{"from": "peer", "to": ["reader"]}, subject="Subject", message="body")
    message.update(overrides)
    (path / "message.json").write_text(json.dumps(message), encoding="utf-8")
    return mid


def seed(agent, count):
    ids = [put(agent, f"20261015T120000-{i:04x}") for i in range(count)]
    # Three unread: newest two plus the oldest directory, behind all read IDs.
    unread = {ids[0], ids[-2], ids[-1]}
    p._save_read_ids(agent, set(ids) - unread)
    return ids, unread


def observe_reads(monkeypatch):
    counts = Counter()
    original = Path.read_text
    def read(path, *args, **kwargs):
        counts[path.name] += 1
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    return counts


def legacy_list(agent, *, read_ids=None):
    """Original full-scan/sort oracle; apply unread exclusion only after reads."""
    inbox = p._inbox_dir(agent)
    if not inbox.is_dir():
        return []
    result = []
    for path in inbox.iterdir():
        msgfile = path / "message.json"
        if not path.is_dir() or not msgfile.is_file():
            continue
        try:
            msg = json.loads(msgfile.read_text(encoding="utf-8"))
            msg["_mailbox_id"] = path.name
            result.append(msg)
        except (json.JSONDecodeError, OSError):
            continue
    result.sort(key=lambda msg: msg.get("received_at", ""), reverse=True)
    return [x for x in result if read_ids is None or x["_mailbox_id"] not in read_ids]


@pytest.mark.parametrize("count", [20, 2000])
@pytest.mark.parametrize("operation", ["digest", "context", "rerender", "check"])
def test_unread_body_reads_scale_with_unread_not_mailbox(tmp_path, monkeypatch, count, operation):
    agent = agent_at(tmp_path)
    ids, unread = seed(agent, count)
    published = []
    monkeypatch.setattr(notifications, "submit", lambda *args, **kw: published.append(kw))
    counts = observe_reads(monkeypatch)
    if operation == "digest":
        _, total, _ = p._render_unread_digest(agent)
        assert total == 3
    elif operation == "context":
        _, shown = p._unread_notification_context(agent)
        assert set(shown) == unread
    elif operation == "rerender":
        assert p._rerender_unread_digest(agent) == "email"
        assert published[0]["data"]["count"] == 3
        assert set(published[0]["data"]["email_ids"]) == unread
    else:
        result = m.EmailManager(agent)._check({"filter": {"unread_only": True}})
        assert result["total"] == 3
        assert {e["id"] for e in result["emails"]} == unread
    assert counts["message.json"] == 3
    assert counts["read.json"] == 1


def test_all_read_skips_every_body_and_clears(tmp_path, monkeypatch):
    agent = agent_at(tmp_path)
    ids, _ = seed(agent, 60)
    p._save_read_ids(agent, set(ids))
    cleared = []
    monkeypatch.setattr(notifications, "clear", lambda *args: cleared.append(args[-1]))
    counts = observe_reads(monkeypatch)
    assert p._rerender_unread_digest(agent) is None
    assert cleared == ["email"]
    assert counts["message.json"] == 0
    assert counts["read.json"] == 1


@pytest.mark.parametrize("args", [
    {}, {"n": 1}, {"n": 0}, {"filter": {"sort": "oldest"}},
    {"filter": {"unread_only": True}},
    {"filter": {"unread_only": True, "contains": "SPECIAL", "sort": "oldest"}},
    {"filter": {"from": "pe", "subject": "Sub", "after": "2026-01", "before": "2027"}},
    {"filter": {"has_attachments": True, "truncate": 0}},
])
def test_check_preserves_legacy_order_filters_total_and_preview(tmp_path, monkeypatch, args):
    agent = agent_at(tmp_path)
    put(agent, "20261015T120000-ffff")
    # Canonical-looking ID is deliberately inconsistent with its payload time.
    put(agent, "20260101T000000-aaaa", received_at="2026-10-15T13:00:00Z",
        message="special body", attachments=[{"name": "x"}])
    put(agent, "legacy-id", received_at="2026-10-14T12:00:00Z")
    p._save_read_ids(agent, {"20261015T120000-ffff"})
    manager = m.EmailManager(agent)
    actual = manager._check(args)
    with monkeypatch.context() as patch:
        patch.setattr(m, "_list_inbox", legacy_list)
        expected = manager._check(args)
    assert actual == expected
    if args == {"n": 1}:
        assert actual["total"] == 3
        assert actual["emails"][0]["id"] == "20260101T000000-aaaa"


def test_unread_projection_matches_legacy_and_stable_ties(tmp_path, monkeypatch):
    agent = agent_at(tmp_path)
    ids, unread = seed(agent, 20)
    actual = (p._render_unread_digest(agent), p._unread_notification_context(agent))
    with monkeypatch.context() as patch:
        patch.setattr(p, "_list_inbox", legacy_list)
        expected = (p._render_unread_digest(agent), p._unread_notification_context(agent))
    assert actual == expected


@pytest.mark.parametrize("operation", ["read", "dismiss"])
def test_read_and_dismiss_only_read_target_and_remaining_unread(tmp_path, monkeypatch, operation):
    agent = agent_at(tmp_path)
    ids, unread = seed(agent, 2000)
    captured = []
    monkeypatch.setattr(notifications, "submit", lambda *a, **kw: captured.append(kw))
    counts = observe_reads(monkeypatch)
    result = getattr(m.EmailManager(agent), "_" + operation)({"email_id": [ids[-1]]})
    assert counts["message.json"] == 3  # one target + two remaining, not 2*2000
    assert captured[0]["data"]["count"] == 2
    assert set(captured[0]["data"]["email_ids"]) == unread - {ids[-1]}
    assert ids[-1] in p._read_ids(agent)
    assert result["status"] == "ok"
    if operation == "dismiss":
        assert result == {"status": "ok", "dismissed": [ids[-1]]}
    else:
        assert result["emails"][0]["message"] == "body"


def test_missing_corrupt_non_file_and_legacy_entries(tmp_path):
    agent = agent_at(tmp_path)
    assert p._list_inbox(agent) == []
    good = put(agent, "legacy")
    root = p._inbox_dir(agent)
    (root / "missing").mkdir()
    (root / "not-a-directory").write_text("ignore", encoding="utf-8")
    (root / "bad").mkdir()
    (root / "bad" / "message.json").write_text("{", encoding="utf-8")
    (root / "not-a-file" / "message.json").mkdir(parents=True)
    assert p._list_inbox(agent) == legacy_list(agent)
    assert [x["_mailbox_id"] for x in p._list_inbox(agent)] == [good]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_fifo_message_is_skipped_without_open(tmp_path, monkeypatch):
    agent = agent_at(tmp_path)
    root = p._inbox_dir(agent) / "fifo"
    root.mkdir(parents=True)
    os.mkfifo(root / "message.json")
    counts = observe_reads(monkeypatch)
    assert p._list_inbox(agent) == []
    assert counts["message.json"] == 0


def test_no_cache_new_arrival_and_changed_read_ledger_are_observed(tmp_path):
    agent = agent_at(tmp_path)
    put(agent, "old")
    assert len(p._unread_snapshot(agent)[1]) == 1
    p._save_read_ids(agent, {"old"})
    put(agent, "new")
    assert [m["_mailbox_id"] for m in p._unread_snapshot(agent)[1]] == ["new"]
