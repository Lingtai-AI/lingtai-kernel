"""Regression tests for inbound WeChat message replay after worker-hang refresh.

Bug: after an LLM worker hangs for 300s, the kernel refreshes via the
``chat_history_save_skipped`` path and relaunches without committing the
WeChat ``get_updates`` cursor. The iLink server then re-delivers the same
backlog, which used to land as brand-new inbox entries with fresh local UUIDs
and counted as new unread — polluting unread/notification state. These tests
pin the idempotency guard that suppresses such replays while preserving
genuinely new messages.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from lingtai.mcp_servers.wechat.manager import WechatManager
from lingtai.mcp_servers.wechat.types import (
    MessageItem, MessageItemType, TextItem, WeixinMessage,
)


def _manager(tmp_path: Path, events: list[dict]) -> WechatManager:
    return WechatManager(
        token="test-token",
        user_id="test-bot",
        working_dir=tmp_path,
        on_inbound=events.append,
    )


def _text_msg(
    *,
    from_user: str,
    text: str,
    message_id: int | None = None,
    seq: int | None = None,
    item_msg_id: str | None = None,
    create_time_ms: int | None = None,
) -> WeixinMessage:
    """Build a USER (type=1) text WeixinMessage as msg_from_dict would."""
    return WeixinMessage(
        seq=seq,
        message_id=message_id,
        from_user_id=from_user,
        message_type=1,
        create_time_ms=create_time_ms,
        item_list=[
            MessageItem(
                type=MessageItemType.TEXT,
                msg_id=item_msg_id,
                text_item=TextItem(text=text),
            )
        ],
    )


def _inbox_count(tmp_path: Path) -> int:
    inbox = tmp_path / "wechat" / "inbox"
    return sum(1 for d in inbox.iterdir() if (d / "message.json").is_file())


def _deliver(mgr: WechatManager, msg: WeixinMessage) -> None:
    asyncio.run(mgr._on_incoming(msg))


# ── Replay suppression (the bug) ──────────────────────────────────────────

def test_replay_same_upstream_id_lands_once(tmp_path):
    """Same upstream message_id re-delivered with a new fetch lands ONCE
    and wakes the host ONCE, even though the local UUID would differ."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    user = "wxid_alice@im.wechat"

    first = _text_msg(from_user=user, text="hi", message_id=12345)
    # Replay: identical upstream id, distinct WeixinMessage object (the
    # addon never sees the old local UUID, only the upstream payload).
    replay = _text_msg(from_user=user, text="hi", message_id=12345)

    _deliver(mgr, first)
    _deliver(mgr, replay)

    assert _inbox_count(tmp_path) == 1, "replay must not create a 2nd inbox entry"
    assert len(events) == 1, "replay must not trigger a 2nd LICC wake"


def test_replay_falls_back_to_content_signature(tmp_path):
    """When upstream omits message_id/seq/item msg_id, dedup falls back to
    (from_user, create_time_ms, body_hash) — same content + same upstream
    send time is a replay even though the local landing date differs."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    user = "wxid_bob@im.wechat"

    first = _text_msg(from_user=user, text="status?", create_time_ms=1_700_000_000_000)
    replay = _text_msg(from_user=user, text="status?", create_time_ms=1_700_000_000_000)

    _deliver(mgr, first)
    _deliver(mgr, replay)

    assert _inbox_count(tmp_path) == 1
    assert len(events) == 1


def test_replay_survives_relaunch_via_persisted_index(tmp_path):
    """The guard is durable: a fresh manager (simulating refresh+relaunch)
    loads inbox_seen.json and still suppresses the replay."""
    events1: list[dict] = []
    mgr1 = _manager(tmp_path, events1)
    user = "wxid_carol@im.wechat"
    _deliver(mgr1, _text_msg(from_user=user, text="ping", message_id=99))
    assert (tmp_path / "wechat" / "inbox_seen.json").is_file()

    # New manager instance == relaunch after refresh.
    events2: list[dict] = []
    mgr2 = _manager(tmp_path, events2)
    _deliver(mgr2, _text_msg(from_user=user, text="ping", message_id=99))

    assert _inbox_count(tmp_path) == 1
    assert events2 == [], "replay after relaunch must not re-wake the host"


# ── Genuine messages must NOT be dropped ──────────────────────────────────

def test_distinct_upstream_ids_both_land(tmp_path):
    """Two different upstream messages (different ids) both land, even with
    identical text."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    user = "wxid_dave@im.wechat"

    _deliver(mgr, _text_msg(from_user=user, text="ok", message_id=1))
    _deliver(mgr, _text_msg(from_user=user, text="ok", message_id=2))

    assert _inbox_count(tmp_path) == 2
    assert len(events) == 2


def test_same_text_different_time_both_land(tmp_path):
    """Content-signature fallback keys on create_time_ms, so the same text
    sent at two different upstream times is two real messages, not a replay."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    user = "wxid_erin@im.wechat"

    _deliver(mgr, _text_msg(from_user=user, text="here", create_time_ms=1_700_000_000_000))
    _deliver(mgr, _text_msg(from_user=user, text="here", create_time_ms=1_700_000_060_000))

    assert _inbox_count(tmp_path) == 2
    assert len(events) == 2


def test_same_text_different_users_both_land(tmp_path):
    """Stable key is namespaced by sender — same text from two users is not
    a replay."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    _deliver(mgr, _text_msg(from_user="wxid_a@im.wechat", text="yo", create_time_ms=42))
    _deliver(mgr, _text_msg(from_user="wxid_b@im.wechat", text="yo", create_time_ms=42))

    assert _inbox_count(tmp_path) == 2
    assert len(events) == 2


# ── Provenance / traceability ─────────────────────────────────────────────

def test_landed_message_records_stable_key_provenance(tmp_path):
    """The landed message.json carries the stable_key + upstream ids so a
    suppressed duplicate is always traceable to its original (no silent loss)."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    user = "wxid_frank@im.wechat"
    _deliver(mgr, _text_msg(from_user=user, text="trace me", message_id=777,
                            create_time_ms=123))

    inbox = tmp_path / "wechat" / "inbox"
    msg_file = next(inbox.iterdir()) / "message.json"
    data = json.loads(msg_file.read_text(encoding="utf-8"))
    assert data["stable_key"] == f"{user}|mid:777"
    assert data["upstream_message_id"] == 777
    assert data["upstream_create_time_ms"] == 123
