"""Focused durable projection coverage for passive Feishu channel events."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lingtai.mcp_servers.feishu.account import FeishuInboundChannelEvent
from lingtai.mcp_servers.feishu.manager import FeishuManager


class _FakeService:
    def get_account(self, _alias: str) -> Any:
        raise KeyError(_alias)


def _operator(open_id: str = "ou_allowed") -> SimpleNamespace:
    return SimpleNamespace(open_id=open_id, user_id="user_allowed", name="Actor")


def _raw(event_id: str | None) -> dict[str, Any]:
    header: dict[str, Any] = {"create_time": "1785680000000"}
    if event_id is not None:
        header["event_id"] = event_id
    return {"schema": "2.0", "header": header, "event": {"kept": True}}


def _manager(tmp_path: Path, outbound: list[dict[str, Any]]) -> FeishuManager:
    return FeishuManager(
        _FakeService(),
        working_dir=tmp_path,
        on_inbound=outbound.append,
    )


def test_passive_events_are_durable_searchable_deduped_and_never_published(
    tmp_path: Path,
) -> None:
    outbound: list[dict[str, Any]] = []
    manager = _manager(tmp_path, outbound)
    reaction = SimpleNamespace(
        message_id="om_target",
        operator=_operator(),
        emoji_type="THUMBSUP",
        action="added",
        chat_id="oc_source",
        chat_type="p2p",
        action_time=1785680001000,
        raw=_raw("evt-reaction"),
    )
    message_read = SimpleNamespace(
        reader=_operator(),
        message_ids=["om_target"],
        raw=_raw("evt-read"),
    )
    bot_added = SimpleNamespace(
        chat_id="oc_source",
        operator=_operator(),
        chat_name="Test chat",
        external=False,
        raw=_raw("evt-added"),
    )
    # No provider event id exercises the deterministic derived-id fallback.
    bot_leave = SimpleNamespace(
        chat_id="oc_source",
        operator=_operator(),
        raw=_raw(None),
    )

    for event_type, event in (
        ("reaction", reaction),
        ("message_read", message_read),
        ("bot_added", bot_added),
        ("bot_leave", bot_leave),
        ("reaction", reaction),
        ("bot_leave", bot_leave),
    ):
        manager.on_channel_event(
            "main",
            FeishuInboundChannelEvent(event_type=event_type, event=event),
        )

    # Passive events are producer state only. Publishing even wake=False would
    # still replace .notification/mcp.feishu.json and race a human message.
    assert outbound == []

    result = manager.handle({
        "action": "read",
        "account": "main",
        "chat_id": "events",
        "limit": 10,
    })
    assert result["status"] == "ok"
    assert len(result["messages"]) == 4
    assert {message["event_type"] for message in result["messages"]} == {
        "reaction",
        "message_read",
        "bot_added",
        "bot_leave",
    }
    assert all(message["synthetic"] is True for message in result["messages"])
    assert all(message["chat_id"] == "events" for message in result["messages"])
    assert all(message["feishu"] for message in result["messages"])

    reaction_message = next(
        message for message in result["messages"]
        if message["event_type"] == "reaction"
    )
    assert reaction_message["source_chat_id"] == "oc_source"
    assert reaction_message["event"] == {
        "message_id": "om_target",
        "operator": {
            "open_id": "ou_allowed",
            "user_id": "user_allowed",
            "name": "Actor",
        },
        "emoji_type": "THUMBSUP",
        "action": "added",
        "chat_id": "oc_source",
        "chat_type": "p2p",
        "action_time": 1785680001000,
    }

    search = manager.handle({
        "action": "search",
        "account": "main",
        "chat_id": "events",
        "query": "THUMBSUP",
    })
    assert search["status"] == "ok"
    assert search["total"] == 1
    assert search["messages"][0]["feishu"] == reaction.raw


def test_event_id_fallback_is_stable() -> None:
    projection = {
        "chat_id": "oc_source",
        "operator": {"open_id": "ou_allowed"},
    }
    first = FeishuManager._channel_event_id({}, "bot_leave", projection)
    second = FeishuManager._channel_event_id({}, "bot_leave", projection)

    assert first == second
    assert first.startswith("derived-")
