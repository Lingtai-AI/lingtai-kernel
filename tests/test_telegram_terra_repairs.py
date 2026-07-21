"""Repairs for the five Terra review findings on the lossless envelope PR.

1. The polling boundary subscribes to every catalogued Update branch via
   ``allowed_updates`` (not just the server-side default set).
2. The reserved synthetic ``updates`` bucket is schema-callable for
   read/search recovery; send/reply stay numeric-only.
3. Per-update event identity propagates through the curated LICC preview and
   persistent construction, so repeated callbacks on one keyboard never
   collapse; the legacy compound reply id is preserved.
4. Local slash-command interception covers the new non-edit human
   Message-typed branches (business_message, guest_message) after the
   allowlist gate.
5. ``licc_structured_omitted`` recovery markers ride into the agent-facing
   persistent lane instead of being silently discarded.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import mcp.types as types

from lingtai.kernel import meta_block
from lingtai.mcp_servers.telegram import updates as tg_updates
from lingtai.mcp_servers.telegram.account import TelegramAccount
from lingtai.mcp_servers.telegram.manager import SCHEMA, TelegramManager
from lingtai.mcp_servers.telegram.server import build_server
from lingtai.services import mcp_inbox as inbox
from tests._notification_store_helpers import notification_store_for

DATE = 1781600000
USER_A = {"id": 1, "is_bot": False, "first_name": "Alice", "username": "alice"}
USER_B = {"id": 666, "is_bot": False, "first_name": "Mallory", "username": "mallory"}
PRIVATE_CHAT = {"id": 123, "type": "private", "username": "alice"}
TELEGRAM_LANE = next(
    lane for lane in meta_block._IM_PERSISTENT_LANES
    if lane.source_key == "mcp.telegram"
)


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


class _Agent:
    """Bare agent stub for persistent-lane construction."""


def _manager(workdir: Path, sink: list[dict] | None = None) -> TelegramManager:
    return TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=(sink.append if sink is not None else (lambda _e: None)),
        notification_store=notification_store_for(workdir),
    )


def _account(state_dir: Path, allowed_users, seen: list) -> TelegramAccount:
    acct = TelegramAccount(
        alias="main",
        bot_token="123:ABC",
        allowed_users=allowed_users,
        state_dir=state_dir,
        on_message=lambda alias, upd: seen.append(upd),
    )
    acct._request = lambda *_a, **_kw: {}
    return acct


def _msg(message_id: int = 100, *, frm: dict = USER_A, text: str = "hello",
         **extra: Any) -> dict:
    m: dict[str, Any] = {"message_id": message_id, "from": frm,
                         "chat": PRIVATE_CHAT, "date": DATE, "text": text}
    m.update(extra)
    return m


def _call_tool(manager: TelegramManager, arguments: dict):
    """Drive the real transport handler (native SCHEMA validation included)."""
    server = build_server(manager)
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="telegram", arguments=arguments),
    )
    return asyncio.run(handler(req)).root


def _payload(result) -> dict:
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)


def _preview_for(event: dict) -> dict:
    """Build the consumer-side preview exactly as _consume_event does."""
    return {
        "from": event["from"],
        "subject": event["subject"],
        "preview": event["body"][:inbox._PREVIEW_FIELD_CAP],
        "preview_truncated": False,
        **inbox._extract_preview_meta(event),
    }


def _notification_payload(previews: list[dict], count: int) -> dict:
    return {
        meta_block.NOTIFICATIONS_KEY: {
            "mcp.telegram": {"data": {"previews": previews, "count": count}},
        },
    }


# ---------------------------------------------------------------------------
# Finding 1 — acquisition boundary subscribes to all catalogued branches.
# ---------------------------------------------------------------------------

def test_poll_loop_requests_every_catalogued_branch(tmp_path: Path) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, [1], seen)
    calls: list[tuple[str, dict]] = []

    def _record_request(method, json=None, **_kw):
        calls.append((method, json))
        acct._stop_event.set()  # one poll cycle
        return []

    acct._request = _record_request
    acct._poll_loop()

    assert calls, "poll loop must issue getUpdates"
    method, payload = calls[0]
    assert method == "getUpdates"
    requested = payload["allowed_updates"]
    assert requested == list(tg_updates.KNOWN_UPDATE_BRANCHES)
    assert len(requested) == 26
    # Named non-default branches from the audit are explicitly requested.
    for branch in ("chat_member", "message_reaction", "message_reaction_count"):
        assert branch in requested
    assert payload["offset"] == acct._last_update_id + 1


# ---------------------------------------------------------------------------
# Finding 2 — synthetic bucket is schema-callable; send stays numeric-only.
# ---------------------------------------------------------------------------

def test_chat_id_schema_accepts_int_and_reserved_bucket_only() -> None:
    chat_id_schema = SCHEMA["properties"]["chat_id"]
    assert {"type": "integer"} in chat_id_schema["anyOf"]
    assert {
        "type": "string",
        "enum": [tg_updates.SYNTHETIC_EVENTS_CHAT_ID],
    } in chat_id_schema["anyOf"]


def test_public_read_recovers_generic_event_from_updates_bucket(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    reaction = {"chat": PRIVATE_CHAT, "message_id": 55, "user": USER_A,
                "date": DATE, "old_reaction": [],
                "new_reaction": [{"type": "emoji", "emoji": "👍"}]}
    manager.on_incoming("main", {"update_id": 5001, "message_reaction": reaction})

    result = _call_tool(manager, {
        "action": "read",
        "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
    })
    assert not result.isError
    payload = _payload(result)
    assert payload["status"] == "ok"
    envelope = payload["messages"][0]["telegram"]
    assert envelope["event_id"] == "main:update:5001"
    assert envelope["update"]["message_reaction"] == reaction


def test_public_read_recovers_inline_only_callback(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    cq = {"id": "cb-inline", "from": USER_A, "chat_instance": "ci",
          "data": "go", "inline_message_id": "im-77"}
    manager.on_incoming("main", {"update_id": 5002, "callback_query": cq})

    result = _call_tool(manager, {
        "action": "read",
        "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
    })
    assert not result.isError
    payload = _payload(result)
    assert payload["messages"][0]["telegram"]["update"]["callback_query"] == cq


def test_public_search_accepts_reserved_bucket_chat_id(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.on_incoming("main", {"update_id": 5003, "poll": {
        "id": "p1", "question": "?", "options": [], "total_voter_count": 0,
        "is_closed": False, "is_anonymous": True, "type": "regular",
        "allows_multiple_answers": False,
    }})
    result = _call_tool(manager, {
        "action": "search",
        "query": "poll",
        "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
    })
    assert not result.isError
    payload = _payload(result)
    assert payload["total"] == 1
    assert payload["messages"][0]["telegram"]["event_id"] == "main:update:5003"


def test_send_rejects_reserved_bucket_and_reply_rejects_event_records(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    result = _call_tool(manager, {
        "action": "send",
        "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
        "text": "hi",
    })
    assert not result.isError  # schema-valid; rejected by the handler
    assert "read/search-only" in _payload(result)["error"]

    reply = manager.handle({
        "action": "reply",
        "message_id": f"main:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:5001",
        "text": "hi",
    })
    assert "synthetic events-bucket" in reply["error"]


def test_arbitrary_string_chat_id_still_schema_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    result = _call_tool(manager, {
        "action": "read",
        "chat_id": "not-a-chat",
    })
    assert result.isError is True


# ---------------------------------------------------------------------------
# Finding 3 — event identity survives to the persistent lane.
# ---------------------------------------------------------------------------

def _two_callback_previews(tmp_path: Path) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    manager = _manager(tmp_path, events)
    cq = {"id": "cb-1", "from": USER_A, "chat_instance": "ci",
          "data": "approve",
          "message": {"message_id": 53, "chat": PRIVATE_CHAT, "date": DATE,
                      "text": "pick"}}
    manager.on_incoming("main", {"update_id": 8001, "callback_query": cq})
    manager.on_incoming(
        "main",
        {"update_id": 8002,
         "callback_query": dict(cq, id="cb-2", data="reject")},
    )
    return events, [_preview_for(e) for e in events]


def test_preview_seam_carries_event_id_scalar(tmp_path: Path) -> None:
    events, previews = _two_callback_previews(tmp_path)
    assert events[0]["metadata"]["event_id"] == "main:update:8001"
    assert previews[0]["event_id"] == "main:update:8001"
    assert previews[1]["event_id"] == "main:update:8002"


def test_repeated_callbacks_never_collapse_in_persistent_messages(
    tmp_path: Path,
) -> None:
    _events, previews = _two_callback_previews(tmp_path)
    payload = _notification_payload(previews, count=2)

    messages = meta_block._im_persistent_messages_from_notifications(
        payload, "mcp.telegram",
    )
    callbacks = [m for m in messages
                 if str(m.get("event_id", "")).startswith("main:update:800")]
    assert len(callbacks) == 2
    by_event = {m["event_id"]: m for m in callbacks}
    assert set(by_event) == {"main:update:8001", "main:update:8002"}
    # Legacy compound reply id preserved on both entries.
    assert all(m["id"] == "main:123:53" for m in callbacks)
    # Each persistent entry carries its own matching raw envelope.
    assert by_event["main:update:8001"]["telegram"]["update"][
        "callback_query"]["data"] == "approve"
    assert by_event["main:update:8002"]["telegram"]["update"][
        "callback_query"]["data"] == "reject"

    hooks = meta_block._im_persistent_events_from_notifications(
        payload, "mcp.telegram",
    )
    assert [h["event_id"] for h in hooks] == [
        "main:update:8001", "main:update:8002",
    ]


def test_delta_delivery_tracking_uses_event_identity(tmp_path: Path) -> None:
    _events, previews = _two_callback_previews(tmp_path)
    payload = _notification_payload(previews, count=2)

    agent = _Agent()
    # Simulate a warm provider context in which the first callback (and enough
    # unrelated context) was already delivered; the compound id itself is NOT
    # in the delivered set — only identities are tracked now.
    padding = [f"main:123:{i}" for i in range(TELEGRAM_LANE.min_context)]
    setattr(agent, TELEGRAM_LANE.delivered_ids_attr,
            padding + ["main:update:8001"])
    setattr(agent, TELEGRAM_LANE.last_tool_id_attr, "tool-1")

    lane_payload = meta_block._build_im_notification_persistent_payload(
        agent, payload, TELEGRAM_LANE,
    )
    assert lane_payload is not None
    delivered_event_ids = [
        m.get("event_id") for m in lane_payload["messages"]
    ]
    # The already-delivered event is filtered; the repeat callback (same
    # compound id, new event id) is still delivered as new.
    assert "main:update:8001" not in delivered_event_ids
    assert "main:update:8002" in delivered_event_ids

    meta_block._record_im_persistent_delivery(
        agent, lane_payload, TELEGRAM_LANE, tool_call_id="tool-2",
    )
    recorded = getattr(agent, TELEGRAM_LANE.delivered_ids_attr)
    assert "main:update:8002" in recorded


# ---------------------------------------------------------------------------
# Finding 4 — slash commands stay local on new human Message branches.
# ---------------------------------------------------------------------------

def test_business_and_guest_slash_commands_stay_local(tmp_path: Path) -> None:
    for i, branch in enumerate(("business_message", "guest_message")):
        seen: list[dict] = []
        acct = _account(tmp_path / branch, [1], seen)
        handled: list[int] = []
        acct._cmd_kanban = lambda chat_id: handled.append(chat_id)

        obj = _msg(400 + i, text="/kanban")
        if branch == "business_message":
            obj["business_connection_id"] = "bc1"
        else:
            obj["guest_query_id"] = "gq1"
        acct._process_update({"update_id": 40 + i, branch: obj})

        assert handled == [123], f"{branch} /kanban must be handled locally"
        assert seen == [], f"{branch} /kanban must not reach the agent"

        # Non-command and unknown-command messages still pass through.
        acct._process_update({"update_id": 50 + i, branch: dict(obj, text="hi")})
        acct._process_update(
            {"update_id": 60 + i, branch: dict(obj, text="/unknowncmd")},
        )
        assert [u["update_id"] for u in seen] == [50 + i, 60 + i]


def test_disallowed_user_business_command_rejected_before_local_handling(
    tmp_path: Path,
) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, [1], seen)
    handled: list[int] = []
    acct._cmd_kanban = lambda chat_id: handled.append(chat_id)
    acct._process_update({"update_id": 70, "business_message": _msg(
        401, frm=USER_B, text="/kanban", business_connection_id="bc1",
    )})
    assert handled == []  # allowlist gate runs before command interception
    assert seen == []


def test_channel_post_slash_command_is_not_intercepted(tmp_path: Path) -> None:
    seen: list[dict] = []
    acct = _account(tmp_path, [1], seen)
    handled: list[int] = []
    acct._cmd_kanban = lambda chat_id: handled.append(chat_id)
    channel = {"id": -1002, "type": "channel", "title": "ann"}
    acct._process_update({"update_id": 80, "channel_post": {
        "message_id": 7, "sender_chat": channel, "chat": channel,
        "date": DATE, "text": "/kanban",
    }})
    # Broadcast content is not operator console input: no local handling,
    # normal dispatch to the agent.
    assert handled == []
    assert [u["update_id"] for u in seen] == [80]


# ---------------------------------------------------------------------------
# Finding 5 — omission markers ride into the persistent lane.
# ---------------------------------------------------------------------------

def _marker_preview() -> dict:
    event = {
        "from": "alice",
        "subject": "hi",
        "body": "bounded body preview",
        "metadata": {
            "conversation_ref": "main:123",
            "message_ref": "main:123:53",
            "platform": "telegram",
            "recent_messages": [{"payload": "x" * 30_000}],   # oversize
            "latest_incoming": {"bad": object()},              # unserializable
        },
    }
    return _preview_for(event)


def test_markers_are_not_messages_and_do_not_suppress_fallback() -> None:
    preview = _marker_preview()
    assert preview["recent_messages"]["licc_structured_omitted"] is True
    assert preview["latest_incoming"]["licc_structured_omitted"] is True

    payload = _notification_payload([preview], count=1)
    messages = meta_block._im_persistent_messages_from_notifications(
        payload, "mcp.telegram",
    )
    # Marker dicts are not message candidates; the bounded preview fallback
    # message still lands so the agent keeps triage content.
    assert len(messages) == 1
    assert messages[0]["source"] == "notification_preview"
    assert messages[0]["text"] == "bounded body preview"


def test_markers_ride_into_delta_persistent_payload() -> None:
    payload = _notification_payload([_marker_preview()], count=1)
    lane_payload = meta_block._build_im_notification_persistent_payload(
        _Agent(), payload, TELEGRAM_LANE,
    )
    assert lane_payload is not None
    markers = lane_payload["structured_omitted"]
    assert {m["reason"] for m in markers} == {"oversize", "unserializable"}
    assert {m["field"] for m in markers} == {"recent_messages", "latest_incoming"}
    for marker in markers:
        assert marker["licc_structured_omitted"] is True
        assert "read" in marker["recovery"]
    # The fallback message is still delivered beside the markers.
    assert lane_payload["messages"]


def test_markers_ride_into_snapshot_persistent_payload() -> None:
    marker = {
        "licc_structured_omitted": True,
        "field": "recent_messages",
        "reason": "oversize",
        "json_chars": 30_100,
        "recovery": "call the producing MCP's read action",
    }
    snapshot_lane = next(
        (lane for lane in meta_block._IM_PERSISTENT_LANES
         if lane.mode == "snapshot"),
        None,
    )
    assert snapshot_lane is not None
    lane_payload = meta_block._build_snapshot_im_persistent_payload(
        {}, snapshot_lane, [], [], omission_markers=[marker],
    )
    assert lane_payload["structured_omitted"] == [marker]


def test_marker_only_payload_still_builds_a_block() -> None:
    """Even with no messages/events at all, the omission marker must surface."""
    preview = {
        "from": "alice",
        "subject": "hi",
        "preview": "",  # no fallback possible
        "preview_truncated": False,
        **inbox._extract_preview_meta({"metadata": {
            "recent_messages": [{"payload": "x" * 30_000}],
        }}),
    }
    payload = _notification_payload([preview], count=1)
    lane_payload = meta_block._build_im_notification_persistent_payload(
        _Agent(), payload, TELEGRAM_LANE,
    )
    assert lane_payload is not None
    assert lane_payload["structured_omitted"][0]["reason"] == "oversize"


# ---------------------------------------------------------------------------
# Terra R2 residual — matched edits stay deliverable in a warm persistent
# lane via the additive current (last-applied edit) event identity.
# ---------------------------------------------------------------------------

def _warm_agent_with_delivered(identity: str) -> _Agent:
    """Agent stub whose warm Telegram lane already delivered *identity*."""
    agent = _Agent()
    padding = [f"main:123:{i}" for i in range(TELEGRAM_LANE.min_context)]
    setattr(agent, TELEGRAM_LANE.delivered_ids_attr, padding + [identity])
    setattr(agent, TELEGRAM_LANE.last_tool_id_attr, "tool-1")
    return agent


def test_matched_edit_reaches_warm_persistent_lane_with_raw_evidence(
    tmp_path: Path,
) -> None:
    events: list[dict] = []
    manager = _manager(tmp_path, events)

    original = {"update_id": 9001, "message": _msg(70, text="v1")}
    manager.on_incoming("main", original)
    # Fresh records start with current identity == root identity (no
    # regression for the non-edit path).
    latest = events[-1]["metadata"]["latest_incoming"]
    assert latest["event_id"] == "main:update:9001"
    assert latest["telegram"]["current_event_id"] == "main:update:9001"

    # Warm lane: the original event identity is already delivered.
    agent = _warm_agent_with_delivered("main:update:9001")

    edit = {"update_id": 9002,
            "edited_message": _msg(70, text="v2", edit_date=DATE + 60)}
    manager.on_incoming("main", edit)

    payload = _notification_payload([_preview_for(events[-1])], count=1)
    lane_payload = meta_block._build_im_notification_persistent_payload(
        agent, payload, TELEGRAM_LANE,
    )
    assert lane_payload is not None
    delta = [m for m in lane_payload["messages"]
             if m.get("id") == "main:123:70"]
    assert len(delta) == 1, "merged edited record must re-deliver in the delta"
    merged = delta[0]
    # Delta carries the edit identity, the immutable root identity, the
    # legacy compound reply id, and the full append-only raw edit evidence.
    assert merged["event_id"] == "main:update:9002"
    assert merged["telegram"]["event_id"] == "main:update:9001"
    assert merged["telegram"]["current_event_id"] == "main:update:9002"
    assert merged["telegram"]["update"] == json.loads(json.dumps(original))
    edits = merged["telegram"]["edits"]
    assert [e["event_id"] for e in edits] == ["main:update:9002"]
    assert edits[0]["update"] == json.loads(json.dumps(edit))
    assert merged["text"] == "v2"

    meta_block._record_im_persistent_delivery(
        agent, lane_payload, TELEGRAM_LANE, tool_call_id="tool-2",
    )
    assert "main:update:9002" in getattr(agent, TELEGRAM_LANE.delivered_ids_attr)


def test_repeated_edits_each_deliver_under_new_identity(tmp_path: Path) -> None:
    events: list[dict] = []
    manager = _manager(tmp_path, events)
    manager.on_incoming("main", {"update_id": 9101, "message": _msg(71, text="v1")})
    manager.on_incoming("main", {
        "update_id": 9102,
        "edited_message": _msg(71, text="v2", edit_date=DATE + 60),
    })

    # First edit delivered; a later second edit must still deliver as new.
    agent = _warm_agent_with_delivered("main:update:9101")
    getattr(agent, TELEGRAM_LANE.delivered_ids_attr).append("main:update:9102")

    second_edit = {"update_id": 9103,
                   "edited_message": _msg(71, text="v3", edit_date=DATE + 120)}
    manager.on_incoming("main", second_edit)

    payload = _notification_payload([_preview_for(events[-1])], count=1)
    lane_payload = meta_block._build_im_notification_persistent_payload(
        agent, payload, TELEGRAM_LANE,
    )
    assert lane_payload is not None
    merged = next(m for m in lane_payload["messages"]
                  if m.get("id") == "main:123:71")
    assert merged["event_id"] == "main:update:9103"
    assert [e["event_id"] for e in merged["telegram"]["edits"]] == [
        "main:update:9102", "main:update:9103",
    ]
    assert merged["telegram"]["edits"][1]["update"] == json.loads(
        json.dumps(second_edit),
    )
    assert merged["text"] == "v3"


def test_edit_identity_does_not_regress_callback_or_delivered_filtering(
    tmp_path: Path,
) -> None:
    """Non-edit records keep current == root identity end-to-end: an already
    delivered (unedited) message is still delta-filtered, and repeated
    callbacks keep their distinct per-update identities."""
    _events, previews = _two_callback_previews(tmp_path)
    payload = _notification_payload(previews, count=2)
    messages = meta_block._im_persistent_messages_from_notifications(
        payload, "mcp.telegram",
    )
    callbacks = [m for m in messages
                 if str(m.get("event_id", "")).startswith("main:update:800")]
    assert {m["event_id"] for m in callbacks} == {
        "main:update:8001", "main:update:8002",
    }
    for m in callbacks:
        assert m["telegram"]["current_event_id"] == m["event_id"]

    events: list[dict] = []
    manager = _manager(tmp_path / "plain", events)
    manager.on_incoming("main", {"update_id": 9201, "message": _msg(72)})
    agent = _warm_agent_with_delivered("main:update:9201")
    plain_payload = _notification_payload([_preview_for(events[-1])], count=1)
    lane_payload = meta_block._build_im_notification_persistent_payload(
        agent, plain_payload, TELEGRAM_LANE,
    )
    # The only candidate is already delivered and unedited: no re-delivery.
    assert lane_payload is None or not [
        m for m in lane_payload.get("messages", [])
        if m.get("id") == "main:123:72"
    ]


# ---------------------------------------------------------------------------
# Terra R3 residual — reading the synthetic events bucket must clear its
# notification mirror (read-state-only identity validation).
# ---------------------------------------------------------------------------

import pytest as _pytest

from lingtai.kernel.notifications import submit as _submit
from lingtai.mcp_servers.telegram.manager import _mirror_identity_account
from tests._notification_store_helpers import store_agent_for


def _publish_mirror_from_event(workdir: Path, event: dict) -> Path:
    """Publish the real mcp.telegram mirror from an actual LICC event."""
    _submit(
        store_agent_for(workdir),
        "mcp.telegram",
        header="1 new event from MCP 'telegram'",
        icon="💬",
        priority="normal",
        instructions="Call the MCP 'telegram' read/check action to fetch.",
        data={
            "count": 1,
            "source": "telegram",
            "has_human_messages": False,
            "previews": [_preview_for(event)],
        },
    )
    return workdir / ".notification" / "mcp.telegram.json"


@_pytest.mark.parametrize(
    ("branch", "obj"),
    [
        (
            "message_reaction",  # known actorless/service-family branch
            {"chat": PRIVATE_CHAT, "message_id": 55, "user": USER_A,
             "date": DATE, "old_reaction": [],
             "new_reaction": [{"type": "emoji", "emoji": "👍"}]},
        ),
        (
            "brand_new_branch",  # unknown-branch open fallback
            {"future": {"shape": [1, 2, 3]}, "date": DATE},
        ),
    ],
)
def test_reading_updates_bucket_clears_its_notification_mirror(
    tmp_path: Path, branch: str, obj: dict,
) -> None:
    workdir = tmp_path / "agent"
    events: list[dict] = []
    manager = _manager(workdir, events)
    manager.on_incoming("main", {"update_id": 5100, branch: obj})

    assert events and events[0]["metadata"]["message_ref"] == "main:updates:5100"
    mirror = _publish_mirror_from_event(workdir, events[0])
    assert mirror.exists()

    result = _call_tool(manager, {
        "action": "read",
        "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
    })
    assert not result.isError
    payload = _payload(result)
    assert payload["status"] == "ok"
    assert payload["messages"][0]["id"] == "main:updates:5100"

    # The synthetic id is marked read AND the mirror is cleared.
    read_ids = json.loads(
        (workdir / "telegram" / "main" / "read.json").read_text(encoding="utf-8"),
    )
    assert "main:updates:5100" in read_ids
    assert not mirror.exists()


def test_mirror_stays_while_another_numeric_event_is_unread(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "agent"
    events: list[dict] = []
    manager = _manager(workdir, events)
    manager.on_incoming("main", {"update_id": 5200, "poll": {
        "id": "p1", "question": "?", "options": [], "total_voter_count": 0,
        "is_closed": False, "is_anonymous": True, "type": "regular",
        "allows_multiple_answers": False,
    }})
    manager.on_incoming("main", {"update_id": 5201, "message": _msg(90)})

    _submit(
        store_agent_for(workdir),
        "mcp.telegram",
        header="2 new events from MCP 'telegram'",
        icon="💬",
        priority="high",
        data={
            "count": 2,
            "source": "telegram",
            "previews": [_preview_for(e) for e in events],
        },
    )
    mirror = workdir / ".notification" / "mcp.telegram.json"
    assert mirror.exists()

    # Reading only the synthetic bucket must NOT clear a mirror that still
    # references the unread numeric message.
    result = _call_tool(manager, {
        "action": "read",
        "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
    })
    assert not result.isError
    assert mirror.exists()

    # Reading the remaining numeric chat completes the lifecycle.
    result = _call_tool(manager, {"action": "read", "chat_id": 123})
    assert not result.isError
    assert not mirror.exists()


def test_mirror_identity_account_accepts_exactly_two_shapes() -> None:
    assert _mirror_identity_account("main:123:53") == "main"
    assert _mirror_identity_account("main:-1001:7") == "main"
    assert _mirror_identity_account(
        f"main:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:5100") == "main"
    for bad in (
        "junk",
        "main:123",
        "main:123:53:9",
        ":123:53",
        "main:notachat:53",
        f"main:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:not-an-update-id",
        f"main:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:",
        "main:123:abc",
    ):
        assert _mirror_identity_account(bad) is None, bad


def test_outbound_paths_still_reject_synthetic_bucket(tmp_path: Path) -> None:
    """The read-state validator must not loosen outbound targeting."""
    manager = _manager(tmp_path)
    send = _payload(_call_tool(manager, {
        "action": "send",
        "chat_id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
        "text": "hi",
    }))
    assert "read/search-only" in send["error"]
    for action in ("reply", "edit"):
        result = manager.handle({
            "action": action,
            "message_id": f"main:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:5100",
            "text": "hi",
        })
        assert "synthetic events-bucket" in result["error"], action
    result = manager.handle({
        "action": "delete",
        "message_id": f"main:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:5100",
    })
    assert "synthetic events-bucket" in result["error"]
