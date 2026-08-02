"""Focused Feishu schema-2.0 card and business-callback coverage."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import lark_channel as lark

from lingtai.mcp_servers.feishu._family import handle_feishu
from lingtai.mcp_servers.feishu.account import (
    FeishuAccount,
    FeishuInboundCardAction,
)
from lingtai.mcp_servers.feishu.manager import FeishuManager


def _card(label: str = "ready") -> dict[str, Any]:
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Business card"},
            "template": "blue",
        },
        "body": {"elements": [
            {"tag": "markdown", "content": f"**State:** {label}"},
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "Confirm"},
                "type": "primary",
                "value": {"action": "confirm", "private": "callback-only"},
            },
        ]},
    }


class _FakeAccount:
    alias = "main"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._next_id = 0

    def _result(self, chat_id: str = "oc_chat") -> dict[str, Any]:
        self._next_id += 1
        message_id = f"om_{self._next_id}"
        return {
            "message_id": message_id,
            "message_ids": [message_id],
            "chat_id": chat_id,
        }

    def send_content(
        self, receive_id: str, receive_id_type: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("send", (receive_id, receive_id_type, message), {}))
        return self._result(
            receive_id if receive_id_type == "chat_id" else "oc_chat"
        )

    def reply_content(
        self,
        message_id: str,
        chat_id: str,
        message: dict[str, Any],
        *,
        reply_in_thread: bool,
    ) -> dict[str, Any]:
        self.calls.append((
            "reply",
            (message_id, chat_id, message),
            {"reply_in_thread": reply_in_thread},
        ))
        return self._result(chat_id)

    def update_content(
        self, message_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("edit", (message_id, message), {}))
        return {"message_id": message_id, "message_ids": [message_id]}

    def add_reaction(self, message_id: str, emoji_type: str) -> bool:
        self.calls.append(("reaction", (message_id, emoji_type), {}))
        return True


class _FakeService:
    def __init__(self, account: _FakeAccount | None = None) -> None:
        self.default_account = account or _FakeAccount()

    def get_account(self, alias: str) -> _FakeAccount:
        assert alias == self.default_account.alias
        return self.default_account


def _manager(
    tmp_path: Path,
    *,
    on_inbound: Any | None = None,
) -> tuple[FeishuManager, _FakeAccount]:
    account = _FakeAccount()
    return (
        FeishuManager(
            _FakeService(account),
            working_dir=tmp_path,
            on_inbound=on_inbound or (lambda _payload: None),
        ),
        account,
    )


def _call(manager: FeishuManager, action: str, input_: dict[str, Any]) -> dict:
    return handle_feishu(
        manager,
        {"action": action, "input": input_, "reasoning": "focused test"},
    )


def _write_target(tmp_path: Path) -> str:
    compound_id = "main:oc_chat:om_target"
    target = tmp_path / "feishu" / "main" / "inbox" / "target"
    target.mkdir(parents=True)
    (target / "message.json").write_text(
        json.dumps({
            "id": compound_id,
            "feishu_message_id": "om_target",
            "chat_id": "oc_chat",
            "thread_id": None,
            "message_type": "text",
            "text": "target",
            "date": "2026-08-03T00:00:00Z",
        }),
        encoding="utf-8",
    )
    return compound_id


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(tmp_path.glob("feishu/main/*/*/message.json"))
    ]


def test_send_reply_and_edit_preserve_exact_schema_2_card(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)
    initial = _card("initial")

    sent = _call(manager, "send", {
        "receive_id": "ou_user",
        "content": {"type": "card", "card": initial},
    })
    target = _write_target(tmp_path)
    replied = _call(manager, "reply", {
        "message_id": target,
        "content": {"type": "card", "card": initial},
    })
    updated = _card("updated")
    edited = _call(manager, "edit", {
        "message_id": sent["message_id"],
        "content": {"type": "card", "card": updated},
    })

    assert sent["content_type"] == "card"
    assert replied["content_type"] == "card"
    assert edited == {
        "status": "edited",
        "message_id": sent["message_id"],
        "content_type": "card",
    }
    assert account.calls[0][1][2] == {"card": initial}
    assert next(call for call in account.calls if call[0] == "reply")[1][2] == {
        "card": initial,
    }
    assert next(call for call in account.calls if call[0] == "edit")[1][1] == {
        "card": updated,
    }

    sent_record = next(
        record for record in _records(tmp_path)
        if record.get("id") == sent["message_id"]
    )
    assert sent_record["content"] == {"type": "card", "card": updated}
    assert sent_record["message_type"] == "interactive"
    assert "Business card" in sent_record["text"]
    assert "Confirm" in sent_record["text"]
    assert "callback-only" not in sent_record["text"]


def test_invalid_or_placeholder_cards_never_reach_transport(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)

    wrong_schema = _call(manager, "send", {
        "receive_id": "ou_user",
        "content": {"type": "card", "card": {
            "schema": "1.0", "body": {"elements": []},
        }},
    })
    placeholder = _call(manager, "send", {
        "receive_id": "ou_user",
        "content": {"type": "card", "card": _card()},
        "placeholder": True,
    })

    assert wrong_schema["status"] == "failed"
    assert wrong_schema["error_code"] == "INVALID_ARGUMENT"
    assert "placeholder only supports" in placeholder["error"]
    assert account.calls == []


def test_account_card_send_and_update_use_sdk_native_wire_shapes() -> None:
    creates: list[dict[str, Any]] = []
    updates: list[tuple[str, dict[str, Any]]] = []

    def create_message(**kwargs: Any) -> dict[str, Any]:
        creates.append(kwargs)
        return {
            "code": 0,
            "data": {"message_id": "om_card", "chat_id": "oc_chat"},
        }

    driver = SimpleNamespace(
        create_message=create_message,
        reply_message=create_message,
        upload_image=None,
        upload_file=None,
    )
    sender = lark.OutboundSender(driver=driver)

    class _Channel:
        async def update_card(
            self, message_id: str, card: dict[str, Any],
        ) -> lark.SendResult:
            updates.append((message_id, card))
            return lark.SendResult.ok(message_id=message_id)

    channel = _Channel()
    channel.sender = sender
    account = FeishuAccount("main", "app", "secret", ["ou_user"])
    account._channel = channel
    card = _card()

    sent = account.send_content("ou_user", "open_id", {"card": card})
    edited = account.update_content("om_card", {"card": card})

    assert sent["message_id"] == "om_card"
    assert creates[0]["msg_type"] == "interactive"
    assert json.loads(creates[0]["content"]) == card
    assert edited["message_id"] == "om_card"
    assert updates == [("om_card", card)]


def _action(
    event_id: str,
    *,
    actor: str = "ou_allowed",
    value: dict[str, Any] | None = None,
) -> lark.CardActionEvent:
    return lark.CardActionEvent(
        message_id="om_card",
        chat_id="oc_chat",
        operator=lark.EventOperator(open_id=actor),
        action=lark.CardActionPayload(
            tag="button",
            value=value or {"action": "confirm"},
        ),
        raw={
            "schema": "2.0",
            "header": {"event_id": event_id, "create_time": "1785680001000"},
            "event": {"context": {"open_thread_id": "omt_topic"}},
        },
    )


def test_account_rejects_missing_and_non_allowlisted_card_actors() -> None:
    seen: list[tuple[str, FeishuInboundCardAction]] = []
    account = FeishuAccount(
        "main",
        "app",
        "secret",
        ["ou_allowed"],
        on_card_action=lambda alias, event: seen.append((alias, event)),
    )

    account._process_card_action(_action("evt_missing", actor=""))
    account._process_card_action(_action("evt_denied", actor="ou_denied"))
    allowed = _action("evt_allowed")
    account._process_card_action(allowed)

    assert seen == [(
        "main",
        FeishuInboundCardAction(action=allowed, feishu=allowed.raw),
    )]


def test_raw_card_events_bypass_sdk_action_identity_dedup() -> None:
    seen: list[FeishuInboundCardAction] = []
    account = FeishuAccount(
        "main",
        "app",
        "secret",
        ["ou_allowed"],
        on_card_action=lambda _alias, event: seen.append(event),
    )

    def envelope(event_id: str) -> dict[str, Any]:
        return {
            "schema": "2.0",
            "header": {
                "event_id": event_id,
                "event_type": "card.action.trigger",
            },
            "event": {
                "operator": {"open_id": "ou_allowed"},
                "context": {
                    "open_message_id": "om_card",
                    "open_chat_id": "oc_chat",
                },
                "action": {
                    "tag": "button",
                    "value": "{\"action\":\"confirm\"}",
                },
            },
        }

    # Same actor/message/value, but two real Feishu event ids: both must reach
    # the manager, which owns durable event-id dedupe.
    account._capture_raw_envelope(envelope("evt_raw_1"))
    account._capture_raw_envelope(envelope("evt_raw_2"))

    assert len(seen) == 2
    assert [event.feishu["header"]["event_id"] for event in seen] == [
        "evt_raw_1", "evt_raw_2",
    ]
    assert seen[0].action.action.value == {"action": "confirm"}


def test_business_callback_is_durably_deduped_and_wakes_same_conversation(
    tmp_path: Path,
) -> None:
    wakes: list[dict[str, Any]] = []
    manager, _account = _manager(tmp_path, on_inbound=wakes.append)
    first = _action("evt_click_1")

    wrapped = FeishuInboundCardAction(action=first, feishu=first.raw)
    manager.on_card_action("main", wrapped)
    manager.on_card_action("main", wrapped)
    # A distinct click with the same action value has its own Feishu event id
    # and must remain a separate business event.
    second = _action("evt_click_2")
    manager.on_card_action(
        "main", FeishuInboundCardAction(action=second, feishu=second.raw),
    )

    card_records = [
        record for record in _records(tmp_path)
        if record.get("event_type") == "card_action"
    ]
    assert {record["feishu_event_id"] for record in card_records} == {
        "evt_click_1", "evt_click_2",
    }
    assert len(wakes) == 2
    assert all(wake["wake"] is True for wake in wakes)
    assert all(wake["metadata"]["chat_id"] == "oc_chat" for wake in wakes)
    assert card_records[0]["thread_id"] == "omt_topic"
    assert card_records[0]["source_message_ref"] == "main:oc_chat:om_card"
    assert card_records[0]["content"]["action"]["value"] == {
        "action": "confirm",
    }

    # Re-create the manager to prove dedupe uses the durable inbox record, not
    # only an in-memory cache.
    restarted_wakes: list[dict[str, Any]] = []
    restarted, _account = _manager(tmp_path, on_inbound=restarted_wakes.append)
    restarted.on_card_action("main", wrapped)
    assert restarted_wakes == []


def test_card_callbacks_are_serialized_per_account_and_chat(tmp_path: Path) -> None:
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def on_inbound(_payload: dict[str, Any]) -> None:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with state_lock:
            active -= 1

    manager, _account = _manager(tmp_path, on_inbound=on_inbound)
    threads = [
        threading.Thread(
            target=manager.on_card_action,
            args=(
                "main",
                FeishuInboundCardAction(
                    action=_action(f"evt_serial_{index}"),
                    feishu=_action(f"evt_serial_{index}").raw,
                ),
            ),
        )
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1
