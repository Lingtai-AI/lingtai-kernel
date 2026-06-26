"""Producer-side notification reconcile for the Telegram MCP (issue #111).

Covers the residual gap in #111: delivery of `.notification/mcp.telegram.json`
is edge-triggered off new inbound LICC events. If that mirror is absent or was
dismissed at the moment a context molt completes — while durable read state
(`read.json`) still has unread Telegram messages — nothing re-derives the
notification until a *fresh* inbound message creates a new edge. The reconcile
tick converts delivery to edge-triggered + periodic level-reconcile.

Contract under test (`TelegramManager.reconcile_notifications`):
  - unread > 0 AND no live `.notification/mcp.telegram.json` -> emit a LICC
    event so the kernel republishes the mirror (post-molt recovery).
  - unread > 0 AND a live mirror already exists -> no-op (idempotent; the
    kernel already surfaced it — don't churn the wire).
  - unread == 0 (all read) -> no-op; clearing the stale mirror is owned by the
    read-state cleanup (PR #310), reconcile must not republish a stale mirror.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.manager import TelegramManager


class _FakeService:
    """Minimal stand-in for TelegramService — reconcile only needs the alias
    list; no network, no accounts."""

    def __init__(self, aliases: list[str]) -> None:
        self._aliases = list(aliases)

    def list_accounts(self) -> list[str]:
        return list(self._aliases)


def _make_manager(tmp_path: Path, aliases: list[str], inbound):
    return TelegramManager(
        service=_FakeService(aliases),
        working_dir=tmp_path,
        on_inbound=inbound,
    )


def _write_inbox_message(tmp_path: Path, account: str, compound_id: str,
                         text: str, *, date: str | None = None) -> None:
    """Persist one inbox message the way on_incoming() does."""
    chat_id = int(compound_id.split(":")[1])
    msg_dir = tmp_path / "telegram" / account / "inbox" / compound_id.replace(":", "_")
    msg_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": compound_id,
        "from": {"username": "jason", "first_name": "Jason"},
        "chat": {"id": chat_id},
        "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "text": text,
        "media": None,
        "reply_to_message_id": None,
        "callback_query": None,
    }
    (msg_dir / "message.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_read_json(tmp_path: Path, account: str, compound_ids: list[str]) -> None:
    acct_dir = tmp_path / "telegram" / account
    acct_dir.mkdir(parents=True, exist_ok=True)
    (acct_dir / "read.json").write_text(json.dumps(sorted(compound_ids)), encoding="utf-8")


def _write_notification_mirror(tmp_path: Path) -> Path:
    notif = tmp_path / ".notification"
    notif.mkdir(parents=True, exist_ok=True)
    path = notif / "mcp.telegram.json"
    path.write_text(json.dumps({"data": {"count": 1}}), encoding="utf-8")
    return path


def test_republishes_when_unread_exists_and_mirror_absent(tmp_path):
    """Durable unread present, no live mirror -> emit a LICC event so the
    kernel republishes mcp.telegram (the #111 post-molt recovery path)."""
    events: list[dict] = []
    mgr = _make_manager(tmp_path, ["main"], events.append)

    _write_inbox_message(tmp_path, "main", "main:100:1", "first")
    _write_inbox_message(tmp_path, "main", "main:100:2", "hello?")
    # read.json marks only the first as read -> #2 is durable unread.
    _write_read_json(tmp_path, "main", ["main:100:1"])
    # No .notification/mcp.telegram.json on disk.

    published = mgr.reconcile_notifications()

    assert published == 1
    assert len(events) == 1
    ev = events[0]
    assert ev["wake"] is True
    assert ev["metadata"]["type"] == "reconcile"
    assert ev["metadata"]["account"] == "main"
    assert ev["metadata"]["unread_count"] == 1
    # The unread message id is carried so the kernel mirror reflects it.
    assert "main:100:2" in ev["metadata"]["unread_ids"]
    assert "main:100:1" not in ev["metadata"]["unread_ids"]


def test_noop_when_all_messages_read(tmp_path):
    """No durable unread -> reconcile is a no-op. It must not republish a
    stale mirror; clearing is owned by the read-state cleanup (PR #310)."""
    events: list[dict] = []
    mgr = _make_manager(tmp_path, ["main"], events.append)

    _write_inbox_message(tmp_path, "main", "main:100:1", "first")
    _write_inbox_message(tmp_path, "main", "main:100:2", "second")
    _write_read_json(tmp_path, "main", ["main:100:1", "main:100:2"])

    published = mgr.reconcile_notifications()

    assert published == 0
    assert events == []


def test_noop_when_mirror_already_present(tmp_path):
    """Durable unread present but a live mirror already exists -> no-op
    (idempotent: the kernel already surfaced it; don't churn the wire)."""
    events: list[dict] = []
    mgr = _make_manager(tmp_path, ["main"], events.append)

    _write_inbox_message(tmp_path, "main", "main:100:2", "hello?")
    _write_read_json(tmp_path, "main", [])
    _write_notification_mirror(tmp_path)

    published = mgr.reconcile_notifications()

    assert published == 0
    assert events == []


def test_noop_when_inbox_empty(tmp_path):
    """No inbox at all -> nothing to reconcile."""
    events: list[dict] = []
    mgr = _make_manager(tmp_path, ["main"], events.append)

    published = mgr.reconcile_notifications()

    assert published == 0
    assert events == []


def test_does_not_double_publish_across_accounts_with_existing_mirror(tmp_path):
    """The mirror is a single channel file shared by all telegram accounts.
    Once any account has caused the mirror to exist, a second account with
    unread must not emit again — the existing edge already covers the channel."""
    events: list[dict] = []
    mgr = _make_manager(tmp_path, ["main", "alt"], events.append)

    _write_inbox_message(tmp_path, "alt", "alt:200:9", "unread on alt")
    _write_read_json(tmp_path, "alt", [])
    _write_notification_mirror(tmp_path)

    published = mgr.reconcile_notifications()

    assert published == 0
    assert events == []
