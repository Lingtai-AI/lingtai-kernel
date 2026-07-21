"""Lossless inbound Telegram Update envelope + per-branch actor policy.

Covers the audited inbound signal-fidelity contract: every admitted Update
survives verbatim inside an additive ``telegram`` envelope through durable
inbox persistence, the structured persistent lane, ``telegram.read``, and
``telegram.search``; all 26 current Update branches plus unknown future
branches are admitted under an explicit actor policy; edits are append-only
evidence rather than destructive replacement; oversize envelopes degrade to
an exact recoverable reference instead of silent omission.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lingtai.mcp_servers.telegram import updates as tg_updates
from lingtai.mcp_servers.telegram.account import TelegramAccount
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.services import mcp_inbox as inbox
from tests._notification_store_helpers import notification_store_for


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

DATE = 1781600000
USER_A = {"id": 1, "is_bot": False, "first_name": "Alice", "username": "alice"}
USER_B = {"id": 666, "is_bot": False, "first_name": "Mallory", "username": "mallory"}
PRIVATE_CHAT = {"id": 123, "type": "private", "username": "alice"}
GROUP_CHAT = {"id": -1001, "type": "supergroup", "title": "ops"}
CHANNEL_CHAT = {"id": -1002, "type": "channel", "title": "announcements"}


class _FakeAccount:
    alias = "main"

    def send_message(self, chat_id: int, text: str, **_kw: Any) -> dict[str, Any]:
        return {"message_id": 9001, "chat": {"id": chat_id}, "text": text}

    def set_message_reaction(self, *_a: Any, **_kw: Any) -> None:
        return None

    def send_chat_action(self, *_a: Any, **_kw: Any) -> None:
        return None


class _FakeService:
    default_account = _FakeAccount()

    def get_account(self, _alias: str) -> _FakeAccount:
        return self.default_account

    def list_accounts(self) -> list[str]:
        return ["main"]


def _manager(workdir: Path, sink: list[dict] | None = None) -> TelegramManager:
    return TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=(sink.append if sink is not None else (lambda _e: None)),
        notification_store=notification_store_for(workdir),
    )


def _inbox_records(workdir: Path, account: str = "main") -> list[dict]:
    records = []
    inbox_dir = workdir / "telegram" / account / "inbox"
    if inbox_dir.is_dir():
        for msg_dir in sorted(inbox_dir.iterdir()):
            f = msg_dir / "message.json"
            if f.is_file():
                records.append(json.loads(f.read_text(encoding="utf-8")))
    return records


def _record_by_event_id(workdir: Path, event_id: str) -> dict | None:
    for rec in _inbox_records(workdir):
        env = rec.get("telegram") or {}
        if env.get("event_id") == event_id:
            return rec
    return None


def _account(state_dir: Path, allowed_users, seen: list) -> TelegramAccount:
    acct = TelegramAccount(
        alias="main",
        bot_token="123:ABC",
        allowed_users=allowed_users,
        state_dir=state_dir,
        on_message=lambda alias, upd: seen.append(upd),
    )
    acct._request = lambda *_a, **_kw: {}  # no network in tests
    return acct


def _msg(message_id: int = 100, *, frm: dict | None = USER_A,
         chat: dict = PRIVATE_CHAT, text: str = "hello", **extra: Any) -> dict:
    m: dict[str, Any] = {"message_id": message_id, "chat": chat, "date": DATE,
                         "text": text}
    if frm is not None:
        m["from"] = frm
    m.update(extra)
    return m


# All 26 current official Update branches with representative payloads and
# the actor kind the policy must resolve.  (update_id + these 26 = the 27
# official Update fields.)
BRANCH_FIXTURES: dict[str, tuple[dict, str]] = {
    "message": (_msg(), "user"),
    "edited_message": (_msg(text="hello v2", edit_date=DATE + 60), "user"),
    "channel_post": (
        {"message_id": 7, "sender_chat": CHANNEL_CHAT, "chat": CHANNEL_CHAT,
         "date": DATE, "text": "announcement"},
        "chat",
    ),
    "edited_channel_post": (
        {"message_id": 7, "sender_chat": CHANNEL_CHAT, "chat": CHANNEL_CHAT,
         "date": DATE, "edit_date": DATE + 60, "text": "announcement v2"},
        "chat",
    ),
    "business_connection": (
        {"id": "bc1", "user": USER_A, "user_chat_id": 123, "date": DATE,
         "can_reply": True, "is_enabled": True},
        "user",
    ),
    "business_message": (
        _msg(200, text="biz hello", business_connection_id="bc1"), "user",
    ),
    "edited_business_message": (
        _msg(200, text="biz hello v2", business_connection_id="bc1",
             edit_date=DATE + 60),
        "user",
    ),
    "deleted_business_messages": (
        {"business_connection_id": "bc1", "chat": PRIVATE_CHAT,
         "message_ids": [1, 2, 3]},
        "none",
    ),
    "guest_message": (
        _msg(300, text="guest hi", guest_query_id="gq1"), "user",
    ),
    "message_reaction": (
        {"chat": GROUP_CHAT, "message_id": 55, "user": USER_A, "date": DATE,
         "old_reaction": [], "new_reaction": [{"type": "emoji", "emoji": "👍"}]},
        "user",
    ),
    "message_reaction_count": (
        {"chat": GROUP_CHAT, "message_id": 55, "date": DATE,
         "reactions": [{"type": {"type": "emoji", "emoji": "👍"},
                        "total_count": 3}]},
        "none",
    ),
    "inline_query": (
        {"id": "iq1", "from": USER_A, "query": "weather", "offset": ""}, "user",
    ),
    "chosen_inline_result": (
        {"result_id": "r1", "from": USER_A, "query": "weather"}, "user",
    ),
    "callback_query": (
        {"id": "cb1", "from": USER_A, "chat_instance": "ci-1", "data": "approve",
         "message": {"message_id": 53, "chat": PRIVATE_CHAT, "date": DATE,
                     "text": "pick one"}},
        "user",
    ),
    "shipping_query": (
        {"id": "sq1", "from": USER_A, "invoice_payload": "p",
         "shipping_address": {"country_code": "US", "state": "CA",
                              "city": "SF", "street_line1": "1 Main St",
                              "street_line2": "", "post_code": "94100"}},
        "user",
    ),
    "pre_checkout_query": (
        {"id": "pcq1", "from": USER_A, "currency": "USD", "total_amount": 500,
         "invoice_payload": "p"},
        "user",
    ),
    "purchased_paid_media": (
        {"from": USER_A, "paid_media_payload": "pm1"}, "user",
    ),
    "poll": (
        {"id": "poll1", "question": "?", "options": [{"text": "a", "voter_count": 0}],
         "total_voter_count": 0, "is_closed": False, "is_anonymous": True,
         "type": "regular", "allows_multiple_answers": False},
        "none",
    ),
    "poll_answer": (
        {"poll_id": "poll1", "user": USER_A, "option_ids": [0]}, "user",
    ),
    "my_chat_member": (
        {"chat": GROUP_CHAT, "from": USER_A, "date": DATE,
         "old_chat_member": {"status": "member", "user": USER_B},
         "new_chat_member": {"status": "administrator", "user": USER_B}},
        "user",
    ),
    "chat_member": (
        {"chat": GROUP_CHAT, "from": USER_A, "date": DATE,
         "old_chat_member": {"status": "left", "user": USER_B},
         "new_chat_member": {"status": "member", "user": USER_B}},
        "user",
    ),
    "chat_join_request": (
        {"chat": GROUP_CHAT, "from": USER_A, "user_chat_id": 1, "date": DATE},
        "user",
    ),
    "chat_boost": (
        {"chat": CHANNEL_CHAT,
         "boost": {"boost_id": "b1", "add_date": DATE,
                   "expiration_date": DATE + 86400,
                   "source": {"source": "premium", "user": USER_B}}},
        "none",
    ),
    "removed_chat_boost": (
        {"chat": CHANNEL_CHAT, "boost_id": "b1", "remove_date": DATE,
         "source": {"source": "premium", "user": USER_B}},
        "none",
    ),
    "managed_bot": (
        {"bot": {"id": 999, "is_bot": True, "first_name": "childbot",
                 "username": "child_bot"}, "date": DATE},
        "none",
    ),
    "subscription": (
        {"user": USER_A, "date": DATE, "is_active": True,
         "expiration_date": DATE + 86400},
        "user",
    ),
}


# All 119 current official Message fields (audit field-matrix, 2026-07-20).
OFFICIAL_MESSAGE_FIELDS = [
    "message_id", "message_thread_id", "direct_messages_topic", "from",
    "sender_chat", "sender_boost_count", "sender_business_bot", "sender_tag",
    "receiver_user", "ephemeral_message_id", "date", "guest_query_id",
    "business_connection_id", "chat", "forward_origin", "is_topic_message",
    "is_automatic_forward", "reply_to_message", "external_reply", "quote",
    "reply_to_story", "reply_to_checklist_task_id", "reply_to_poll_option_id",
    "via_bot", "guest_bot_caller_user", "guest_bot_caller_chat", "edit_date",
    "has_protected_content", "is_from_offline", "is_paid_post",
    "media_group_id", "author_signature", "paid_star_count", "text",
    "entities", "link_preview_options", "suggested_post_info", "effect_id",
    "rich_message", "animation", "audio", "document", "live_photo",
    "paid_media", "photo", "sticker", "story", "video", "video_note", "voice",
    "caption", "caption_entities", "show_caption_above_media",
    "has_media_spoiler", "checklist", "contact", "dice", "game", "poll",
    "venue", "location", "new_chat_members", "left_chat_member",
    "chat_owner_left", "chat_owner_changed", "new_chat_title",
    "new_chat_photo", "delete_chat_photo", "group_chat_created",
    "supergroup_chat_created", "channel_chat_created",
    "message_auto_delete_timer_changed", "migrate_to_chat_id",
    "migrate_from_chat_id", "pinned_message", "invoice", "successful_payment",
    "refunded_payment", "users_shared", "chat_shared", "gift", "unique_gift",
    "gift_upgrade_sent", "connected_website", "write_access_allowed",
    "passport_data", "proximity_alert_triggered", "boost_added",
    "chat_background_set", "checklist_tasks_done", "checklist_tasks_added",
    "community_chat_added", "community_chat_removed",
    "direct_message_price_changed", "forum_topic_created",
    "forum_topic_edited", "forum_topic_closed", "forum_topic_reopened",
    "general_forum_topic_hidden", "general_forum_topic_unhidden",
    "giveaway_created", "giveaway", "giveaway_winners", "giveaway_completed",
    "managed_bot_created", "paid_message_price_changed", "poll_option_added",
    "poll_option_deleted", "suggested_post_approved",
    "suggested_post_approval_failed", "suggested_post_declined",
    "suggested_post_paid", "suggested_post_refunded", "video_chat_scheduled",
    "video_chat_started", "video_chat_ended",
    "video_chat_participants_invited", "web_app_data", "reply_markup",
]

# All 9 current official MessageEntity fields.
FULL_ENTITY = {
    "type": "text_mention", "offset": 0, "length": 5,
    "url": "https://example.org", "user": USER_B, "language": "python",
    "custom_emoji_id": "ce1", "unix_time": DATE, "date_time_format": "relative",
}


def _full_message_fixture() -> dict:
    msg: dict[str, Any] = {
        name: {"_official_field": name} for name in OFFICIAL_MESSAGE_FIELDS
    }
    msg.update({
        "message_id": 999,
        "from": USER_A,
        "chat": PRIVATE_CHAT,
        "date": DATE,
        "text": "field matrix",
        "caption": "caption text",
        "entities": [FULL_ENTITY],
        "caption_entities": [dict(FULL_ENTITY, type="bold")],
        "reply_to_message": {"message_id": 42, "from": USER_B,
                             "chat": PRIVATE_CHAT, "date": DATE - 60,
                             "text": "original target"},
        "external_reply": {"origin": {"type": "user", "date": DATE - 120,
                                      "sender_user": USER_B},
                           "chat": GROUP_CHAT, "message_id": 9},
        "quote": {"text": "exact selected span", "entities": [FULL_ENTITY],
                  "position": 17, "is_manual": True},
        "photo": [{"file_id": "ph-s", "file_unique_id": "u1", "width": 90,
                   "height": 90, "file_size": 1000},
                  {"file_id": "ph-l", "file_unique_id": "u2", "width": 900,
                   "height": 900, "file_size": 90000}],
        "message_thread_id": 77,
        "is_topic_message": True,
        "edit_date": DATE + 5,
        "media_group_id": "mg1",
    })
    assert set(msg) == set(OFFICIAL_MESSAGE_FIELDS)
    return msg


# ---------------------------------------------------------------------------
# 1. Unknown-field sentinel: account -> manager -> inbox -> persistent lane
#    -> read -> search, unchanged.
# ---------------------------------------------------------------------------

def test_unknown_field_sentinel_survives_end_to_end(tmp_path: Path) -> None:
    sentinel = {"nested": [1, {"deep": "value"}], "flag": True}
    update = {
        "update_id": 4001,
        "message": _msg(53, x_future_field=sentinel, x_scalar="keep-me"),
    }
    original = json.loads(json.dumps(update))

    # Account admission (allowlist mode) passes the raw update through.
    seen: list[dict] = []
    acct = _account(tmp_path / "state", [1], seen)
    acct._process_update(update)
    assert seen == [original]

    # Manager persistence + notification.
    events: list[dict] = []
    manager = _manager(tmp_path / "agent", events)
    manager.on_incoming("main", seen[0])

    stored = _record_by_event_id(tmp_path / "agent", "main:update:4001")
    assert stored is not None
    assert stored["telegram"]["update"] == original
    assert stored["telegram"]["update_id"] == 4001
    assert stored["telegram"]["branch"] == "message"
    # Backward-compatible concise fields intact.
    assert stored["id"] == "main:123:53"
    assert stored["text"] == "hello"

    # Persistent structured lane: the current message carries the full
    # envelope, and it passes the LICC consumer seam unchanged.
    metadata = events[0]["metadata"]
    assert metadata["event_id"] == "main:update:4001"
    latest = metadata["latest_incoming"]
    assert latest["telegram"]["update"] == original
    seam = inbox._extract_preview_meta({"metadata": metadata})
    assert seam["latest_incoming"]["telegram"]["update"] == original

    # read
    read = manager._read({"account": "main", "chat_id": 123, "limit": 5})
    assert read["messages"][0]["telegram"]["update"] == original

    # search
    found = manager._search({"account": "main", "query": "hello"})
    assert found["total"] == 1
    assert found["messages"][0]["telegram"]["update"] == original


# ---------------------------------------------------------------------------
# 2. All 26 Update branches + unknown-branch sentinel.
# ---------------------------------------------------------------------------

def test_branch_catalog_matches_official_table() -> None:
    assert set(BRANCH_FIXTURES) == set(tg_updates.KNOWN_UPDATE_BRANCHES)
    assert len(tg_updates.KNOWN_UPDATE_BRANCHES) == 26


@pytest.mark.parametrize("branch", sorted(BRANCH_FIXTURES))
def test_every_branch_is_admitted_recorded_and_projected(
    tmp_path: Path, branch: str,
) -> None:
    obj, expected_kind = BRANCH_FIXTURES[branch]
    update = {"update_id": 5000, branch: obj}
    original = json.loads(json.dumps(update))

    actor = tg_updates.resolve_update_actor(update)
    assert actor["kind"] == expected_kind
    if expected_kind == "user":
        assert actor["user_id"] == USER_A["id"]

    # Account admission with an allowlist containing only USER_A.
    seen: list[dict] = []
    acct = _account(tmp_path / "state", [1], seen)
    acct._process_update(update)
    assert seen == [original], f"branch {branch} must be admitted"

    # Manager records it with the verbatim raw update and stable identity.
    events: list[dict] = []
    manager = _manager(tmp_path / "agent", events)
    manager.on_incoming("main", seen[0])

    stored = _record_by_event_id(tmp_path / "agent", "main:update:5000")
    assert stored is not None, f"branch {branch} must persist a record"
    env = stored["telegram"]
    assert env["update"] == original
    assert env["update_id"] == 5000
    assert env["branch"] == branch
    assert env["actor"]["kind"] == expected_kind

    # Notification metadata carries the stable event identity.
    assert events, f"branch {branch} must notify the agent"
    assert events[0]["metadata"]["event_id"] == "main:update:5000"
    assert events[0]["metadata"]["type"] == branch

    # Discoverable through read: real chat bucket for message-typed
    # branches, the synthetic "updates" bucket otherwise.
    if branch in tg_updates.MESSAGE_TYPED_BRANCHES:
        chat_bucket = obj["chat"]["id"]
    elif branch == "callback_query":
        chat_bucket = obj["message"]["chat"]["id"]
    else:
        chat_bucket = tg_updates.SYNTHETIC_EVENTS_CHAT_ID
        assert stored["synthetic"] is True
        assert stored["chat"]["synthetic"] is True
    read = manager._read({"account": "main", "chat_id": chat_bucket, "limit": 10})
    envelopes = [m.get("telegram") or {} for m in read["messages"]]
    assert any(e.get("event_id") == "main:update:5000" for e in envelopes)


def test_unknown_branch_sentinel_is_preserved(tmp_path: Path) -> None:
    payload = {"future": {"shape": [1, 2, 3]}, "date": DATE}
    update = {"update_id": 6001, "brand_new_branch": payload}
    original = json.loads(json.dumps(update))

    actor = tg_updates.resolve_update_actor(update)
    assert actor["kind"] == "unknown"

    seen: list[dict] = []
    acct = _account(tmp_path / "state", [1], seen)
    acct._process_update(update)
    assert seen == [original]

    events: list[dict] = []
    manager = _manager(tmp_path / "agent", events)
    manager.on_incoming("main", seen[0])
    stored = _record_by_event_id(tmp_path / "agent", "main:update:6001")
    assert stored is not None
    assert stored["telegram"]["update"] == original
    assert stored["telegram"]["branch"] == "brand_new_branch"
    assert stored["telegram"]["actor"]["kind"] == "unknown"
    assert stored["update_type"] == "brand_new_branch"
    assert stored["synthetic"] is True
    assert events[0]["wake"] is False


def test_unknown_branch_with_top_level_user_is_still_allowlisted(
    tmp_path: Path,
) -> None:
    update = {"update_id": 6002, "brand_new_branch": {"from": USER_B, "x": 1}}
    seen: list[dict] = []
    acct = _account(tmp_path / "state", [1], seen)
    acct._process_update(update)
    assert seen == []  # resolvable disallowed user is enforced even on unknown branches


# ---------------------------------------------------------------------------
# 3. Message 119-field matrix.
# ---------------------------------------------------------------------------

def test_all_119_message_fields_survive_unchanged(tmp_path: Path) -> None:
    fixture = _full_message_fixture()
    update = {"update_id": 7001, "message": fixture}
    original_msg = json.loads(json.dumps(fixture))

    manager = _manager(tmp_path)
    manager.on_incoming("main", update)

    stored = _record_by_event_id(tmp_path, "main:update:7001")
    assert stored is not None
    raw_msg = stored["telegram"]["update"]["message"]
    assert set(raw_msg) == set(OFFICIAL_MESSAGE_FIELDS)
    assert raw_msg == original_msg

    read = manager._read({"account": "main", "chat_id": 123, "limit": 5})
    assert read["messages"][0]["telegram"]["update"]["message"] == original_msg
    # Concise compatibility fields still derived as before.
    assert read["messages"][0]["text"] == "field matrix"
    assert read["messages"][0]["reply_to_message_id"] == 42


# ---------------------------------------------------------------------------
# 4-6. TextQuote / reply / external_reply / entities / CallbackQuery.
# ---------------------------------------------------------------------------

def test_text_quote_survives_exactly(tmp_path: Path) -> None:
    quote = {"text": "selected span", "entities": [FULL_ENTITY],
             "position": 4, "is_manual": True}
    update = {
        "update_id": 7101,
        "message": _msg(
            60, text="replying to a selection",
            reply_to_message={"message_id": 42, "from": USER_B,
                              "chat": PRIVATE_CHAT, "date": DATE - 60,
                              "text": "long original text"},
            external_reply={"origin": {"type": "hidden_user",
                                       "sender_user_name": "X",
                                       "date": DATE - 300},
                            "message_id": 8},
            quote=quote,
        ),
    }
    events: list[dict] = []
    manager = _manager(tmp_path, events)
    manager.on_incoming("main", update)

    stored = _record_by_event_id(tmp_path, "main:update:7101")
    raw = stored["telegram"]["update"]["message"]
    assert raw["quote"] == quote
    assert set(raw["quote"]) == {"text", "entities", "position", "is_manual"}
    assert raw["quote"]["entities"][0] == FULL_ENTITY
    assert set(FULL_ENTITY) == {"type", "offset", "length", "url", "user",
                                "language", "custom_emoji_id", "unix_time",
                                "date_time_format"}
    # reply_to_message and external_reply remain distinct complete structures.
    assert raw["reply_to_message"]["text"] == "long original text"
    assert raw["external_reply"]["origin"]["type"] == "hidden_user"
    # Compat scalar still present.
    assert stored["reply_to_message_id"] == 42
    # Quote reaches the persistent structured lane via the envelope.
    latest = events[0]["metadata"]["latest_incoming"]
    assert latest["telegram"]["update"]["message"]["quote"] == quote


def test_callback_query_all_seven_fields_and_distinct_event_identity(
    tmp_path: Path,
) -> None:
    cq = {
        "id": "cb-1", "from": USER_A, "chat_instance": "ci-9",
        "data": "approve", "game_short_name": "chess",
        "inline_message_id": "im-1",
        "message": {"message_id": 53, "chat": PRIVATE_CHAT, "date": DATE,
                    "text": "pick", "reply_markup": {"inline_keyboard": [[
                        {"text": "ok", "callback_data": "approve"}]]}},
    }
    manager = _manager(tmp_path)
    manager.on_incoming("main", {"update_id": 8001, "callback_query": cq})
    second = dict(cq, id="cb-2")
    manager.on_incoming("main", {"update_id": 8002, "callback_query": second})

    first = _record_by_event_id(tmp_path, "main:update:8001")
    repeat = _record_by_event_id(tmp_path, "main:update:8002")
    assert first is not None and repeat is not None
    assert set(first["telegram"]["update"]["callback_query"]) == {
        "id", "from", "message", "inline_message_id", "chat_instance",
        "data", "game_short_name",
    }
    assert first["telegram"]["update"]["callback_query"]["message"]["reply_markup"]
    # Same keyboard message => same compound id, but distinct event identity.
    assert first["id"] == repeat["id"] == "main:123:53"
    assert first["telegram"]["event_id"] != repeat["telegram"]["event_id"]
    # Legacy convenience field intact.
    assert first["callback_query"] == "approve"


def test_inline_only_callback_gets_discoverable_synthetic_identity(
    tmp_path: Path,
) -> None:
    cq = {"id": "cb-inline", "from": USER_A, "chat_instance": "ci",
          "data": "go", "inline_message_id": "im-77"}
    manager = _manager(tmp_path)
    manager.on_incoming("main", {"update_id": 8101, "callback_query": cq})

    stored = _record_by_event_id(tmp_path, "main:update:8101")
    assert stored is not None
    assert stored["id"] == "main:updates:8101"
    assert stored["chat"]["synthetic"] is True
    assert stored["telegram"]["update"]["callback_query"] == cq
    read = manager._read({
        "account": "main", "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
        "limit": 5,
    })
    assert read["messages"][0]["telegram"]["update"]["callback_query"] == cq


# ---------------------------------------------------------------------------
# 7-8. Edits: append-only evidence, media/reply preservation, unmatched edit.
# ---------------------------------------------------------------------------

def test_edit_preserves_original_raw_and_context_and_appends_history(
    tmp_path: Path,
) -> None:
    # Original message with document media whose download fails: the media
    # dict (with download_error) and reply linkage must survive the edit.
    original_msg = _msg(
        70, text="v1",
        document={"file_id": "d1", "file_unique_id": "du1",
                  "file_name": "a.pdf", "file_size": 10, "mime_type":
                  "application/pdf"},
        reply_to_message={"message_id": 42},
    )
    update1 = {"update_id": 9001, "message": original_msg}
    manager = _manager(tmp_path)
    manager.on_incoming("main", update1)
    before = _record_by_event_id(tmp_path, "main:update:9001")
    assert before["media"] is not None  # failure-preserved document metadata
    assert before["reply_to_message_id"] == 42

    edit = {"update_id": 9002,
            "edited_message": _msg(70, text="v2", edit_date=DATE + 60)}
    manager.on_incoming("main", edit)

    records = [r for r in _inbox_records(tmp_path) if r["id"] == "main:123:70"]
    assert len(records) == 1  # merged in place, not duplicated
    merged = records[0]
    # Legacy behavior: latest text wins.
    assert merged["text"] == "v2"
    # New guarantees: context not destroyed, original raw intact, edit
    # evidence appended.
    assert merged["media"] is not None
    assert merged["reply_to_message_id"] == 42
    assert merged["telegram"]["update"] == json.loads(json.dumps(update1))
    edits = merged["telegram"]["edits"]
    assert len(edits) == 1
    assert edits[0]["event_id"] == "main:update:9002"
    assert edits[0]["update"] == json.loads(json.dumps(edit))

    second_edit = {"update_id": 9003,
                   "edited_message": _msg(70, text="v3", edit_date=DATE + 120)}
    manager.on_incoming("main", second_edit)
    merged = [r for r in _inbox_records(tmp_path) if r["id"] == "main:123:70"][0]
    assert merged["text"] == "v3"
    assert [e["event_id"] for e in merged["telegram"]["edits"]] == [
        "main:update:9002", "main:update:9003",
    ]


def test_unmatched_edit_is_visible_and_does_not_wake(tmp_path: Path) -> None:
    events: list[dict] = []
    manager = _manager(tmp_path, events)
    edit = {"update_id": 9101,
            "edited_message": _msg(71, text="edited orphan", edit_date=DATE + 60)}
    manager.on_incoming("main", edit)

    stored = _record_by_event_id(tmp_path, "main:update:9101")
    assert stored is not None
    assert stored["unmatched_edit"] is True
    assert stored["text"] == "edited orphan"
    assert stored["telegram"]["update"] == json.loads(json.dumps(edit))
    assert events and events[0]["wake"] is False
    read = manager._read({"account": "main", "chat_id": 123, "limit": 5})
    assert read["messages"][0]["unmatched_edit"] is True


# ---------------------------------------------------------------------------
# 9. Actor policy security cases.
# ---------------------------------------------------------------------------

def test_disallowed_user_branches_are_rejected(tmp_path: Path) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, [1], seen)
    for branch in sorted(BRANCH_FIXTURES):
        obj, kind = BRANCH_FIXTURES[branch]
        if kind != "user":
            continue
        hostile = json.loads(json.dumps(obj))
        for key in ("from", "user"):
            if key in hostile and isinstance(hostile[key], dict):
                hostile[key] = USER_B
                break
        acct._process_update({"update_id": 1, branch: hostile})
    assert seen == []


def test_nested_users_never_grant_or_deny_admission(tmp_path: Path) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, [1], seen)

    # Disallowed sender quoting/replying to an allowed user: rejected.
    acct._process_update({"update_id": 2, "message": _msg(
        80, frm=USER_B, text="spoof",
        reply_to_message={"message_id": 1, "from": USER_A, "chat": PRIVATE_CHAT,
                          "date": DATE, "text": "hi"},
        quote={"text": "hi", "position": 0},
    )})
    assert seen == []

    # Allowed sender quoting a disallowed user: admitted.
    acct._process_update({"update_id": 3, "message": _msg(
        81, text="legit",
        reply_to_message={"message_id": 2, "from": USER_B, "chat": PRIVATE_CHAT,
                          "date": DATE, "text": "mallory said"},
        entities=[dict(FULL_ENTITY, user=USER_B)],
    )})
    assert [u["update_id"] for u in seen] == [3]


def test_actorless_channel_aggregate_service_events_admitted_under_bot_trust(
    tmp_path: Path,
) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, [1], seen)
    for uid, branch in enumerate(
        ("channel_post", "message_reaction_count", "poll",
         "deleted_business_messages", "chat_boost"), start=10,
    ):
        obj, kind = BRANCH_FIXTURES[branch]
        assert kind in ("chat", "none")
        acct._process_update({"update_id": uid, branch: obj})
    assert [u["update_id"] for u in seen] == [10, 11, 12, 13, 14]


def test_no_allowlist_admits_everything(tmp_path: Path) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, None, seen)
    acct._process_update({"update_id": 20, "message": _msg(frm=USER_B)})
    assert len(seen) == 1


def test_local_slash_commands_stay_local(tmp_path: Path) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, [1], seen)
    handled: list[str] = []
    acct._cmd_kanban = lambda chat_id: handled.append(f"kanban:{chat_id}")
    acct._process_update({"update_id": 30, "message": _msg(text="/kanban")})
    assert handled == ["kanban:123"]
    assert seen == []  # intercepted locally, never reaches the agent
    # Unknown slash commands still pass through.
    acct._process_update({"update_id": 31, "message": _msg(text="/unknowncmd")})
    assert [u["update_id"] for u in seen] == [31]


# ---------------------------------------------------------------------------
# 10. Oversize envelope: bounded summary, exact recoverable representation.
# ---------------------------------------------------------------------------

def test_oversize_envelope_degrades_to_exact_recoverable_ref(
    tmp_path: Path,
) -> None:
    big = "a" * 30_000
    update = {"update_id": 9501, "message": _msg(90, x_future_blob=big)}
    events: list[dict] = []
    manager = _manager(tmp_path, events)
    manager.on_incoming("main", update)

    # Durable inbox record keeps the full raw regardless of size.
    stored = _record_by_event_id(tmp_path, "main:update:9501")
    assert stored["telegram"]["update"]["message"]["x_future_blob"] == big

    # Structured lane: no silent loss — explicit oversize ref with identity
    # and a recovery path instead of the raw envelope.
    latest = events[0]["metadata"]["latest_incoming"]
    assert "telegram" not in latest
    ref = latest["telegram_ref"]
    assert ref["oversize"] is True
    assert ref["event_id"] == "main:update:9501"
    assert "read" in ref["recovery"]

    # The structured families stay under the LICC cap and pass the seam.
    seam = inbox._extract_preview_meta({"metadata": events[0]["metadata"]})
    assert seam["latest_incoming"]["telegram_ref"]["event_id"] == "main:update:9501"
    assert "licc_structured_omitted" not in json.dumps(seam)

    # And read recovers the exact envelope.
    read = manager._read({"account": "main", "chat_id": 123, "limit": 5})
    assert read["messages"][0]["telegram"]["update"]["message"]["x_future_blob"] == big


def test_licc_cap_now_yields_explicit_marker_not_silent_omission() -> None:
    huge = [{"payload": "x" * 30_000}]
    out = inbox._extract_preview_meta({"metadata": {"recent_messages": huge}})
    marker = out["recent_messages"]
    assert marker["licc_structured_omitted"] is True
    assert marker["reason"] == "oversize"
    assert marker["json_chars"] > 20_000
    assert "read" in marker["recovery"]

    bad = inbox._extract_preview_meta({"metadata": {"latest_incoming": {"x": object()}}})
    assert bad["latest_incoming"]["reason"] == "unserializable"


# ---------------------------------------------------------------------------
# Structured-lane shape: refs for older window entries, full envelope for
# the current message.
# ---------------------------------------------------------------------------

def test_structured_window_uses_refs_for_older_messages(tmp_path: Path) -> None:
    events: list[dict] = []
    manager = _manager(tmp_path, events)
    for i in range(3):
        manager.on_incoming("main", {
            "update_id": 9600 + i,
            "message": _msg(100 + i, text=f"m{i}"),
        })
    metadata = events[-1]["metadata"]
    recent = metadata["recent_messages"]
    assert len(recent) == 3
    current = [m for m in recent if m.get("is_current")]
    older = [m for m in recent if not m.get("is_current")]
    assert current and older
    assert current[0]["telegram"]["event_id"] == "main:update:9602"
    for item in older:
        assert "telegram" not in item
        assert item["telegram_ref"]["event_id"].startswith("main:update:960")


def test_generic_event_appears_in_check_and_search(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    obj, _ = BRANCH_FIXTURES["message_reaction"]
    manager.on_incoming("main", {"update_id": 9700, "message_reaction": obj})

    check = manager._check({"account": "main"})
    buckets = {c["chat_id"] for c in check["messages"]}
    assert tg_updates.SYNTHETIC_EVENTS_CHAT_ID in buckets

    found = manager._search({"account": "main", "query": "message_reaction"})
    assert found["total"] == 1
    assert found["messages"][0]["telegram"]["update"]["message_reaction"] == obj
