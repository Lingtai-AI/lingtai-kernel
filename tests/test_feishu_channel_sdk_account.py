"""Focused compatibility coverage for the low-level lark-channel-sdk adapter."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from types import SimpleNamespace

import lark_channel as lark_channel_sdk

from lingtai.mcp_servers.feishu import account as account_module
from lingtai.mcp_servers.feishu.account import (
    FeishuAccount,
    FeishuInboundChannelEvent,
    FeishuInboundEvent,
)


class _Success:
    def __init__(self, **values: object) -> None:
        self.code = 0
        self.msg = "ok"
        for key, value in values.items():
            setattr(self, key, value)

    def success(self) -> bool:
        return True


def test_outbound_methods_use_channel_sdk_generated_models() -> None:
    requests: dict[str, object] = {}

    class _Message:
        def create(self, request: object) -> _Success:
            requests["create"] = request
            return _Success(
                data=SimpleNamespace(
                    message_id="om_created",
                    chat_id="oc_chat",
                    create_time="123",
                )
            )

        def reply(self, request: object) -> _Success:
            requests["reply"] = request
            return _Success(
                data=SimpleNamespace(message_id="om_reply", chat_id="oc_chat")
            )

        def patch(self, request: object) -> _Success:
            requests["patch"] = request
            return _Success()

        def delete(self, request: object) -> _Success:
            requests["delete"] = request
            return _Success()

    class _MessageResource:
        def get(self, request: object) -> _Success:
            requests["resource"] = request
            return _Success(file_name="voice.ogg", file=io.BytesIO(b"audio"))

    class _MessageReaction:
        def create(self, request: object) -> _Success:
            requests["reaction"] = request
            return _Success(data=SimpleNamespace(reaction_id="reaction-1"))

        def delete(self, request: object) -> _Success:
            requests["reaction_delete"] = request
            return _Success()

    rest_client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(
                message=_Message(),
                message_resource=_MessageResource(),
                message_reaction=_MessageReaction(),
            )
        )
    )
    account = FeishuAccount("main", "app", "secret", ["ou_allowed"])
    account._rest_client = rest_client

    assert account.send_text("ou_allowed", "open_id", "hello") == {
        "message_id": "om_created",
        "chat_id": "oc_chat",
        "create_time": "123",
    }
    assert account.reply_text("om_original", "reply") == {
        "message_id": "om_reply",
        "chat_id": "oc_chat",
    }
    assert account.get_message_resource("om_original", "file-key") == (
        "voice.ogg",
        b"audio",
    )
    assert account.add_reaction("om_original", "OK") is True
    assert account.add_reaction_with_id("om_original", "SMILE") == "reaction-1"
    assert account.add_typing_reaction("om_original") == "reaction-1"
    assert account.remove_reaction("om_original", "reaction-1") is True
    assert account.update_message("om_reply", "edited") == {}
    assert account.delete_message("om_reply") is True

    create = requests["create"]
    assert create.receive_id_type == "open_id"
    assert create.request_body.receive_id == "ou_allowed"
    assert create.request_body.msg_type == "text"
    assert json.loads(create.request_body.content) == {"text": "hello"}

    reply = requests["reply"]
    assert reply.message_id == "om_original"
    assert json.loads(reply.request_body.content) == {"text": "reply"}

    resource = requests["resource"]
    assert resource.message_id == "om_original"
    assert resource.file_key == "file-key"
    assert resource.type == "file"
    for logical_type, api_type in (
        ("image", "image"),
        ("sticker", "image"),
        ("audio", "file"),
        ("video", "file"),
    ):
        account.get_message_resource("om_original", "file-key", logical_type)
        assert requests["resource"].type == api_type

    reaction = requests["reaction"]
    assert reaction.message_id == "om_original"
    assert reaction.request_body.reaction_type.emoji_type == "Typing"

    reaction_delete = requests["reaction_delete"]
    assert reaction_delete.message_id == "om_original"
    assert reaction_delete.reaction_id == "reaction-1"

    patch = requests["patch"]
    assert patch.message_id == "om_reply"
    assert json.loads(patch.request_body.content) == {"text": "edited"}
    assert requests["delete"].message_id == "om_reply"


def test_ws_loop_stops_channel_sdk_client_without_public_stop(monkeypatch) -> None:
    fallback_loop = asyncio.new_event_loop()
    sdk_module = SimpleNamespace(loop=fallback_loop)
    monkeypatch.setattr(account_module, "_sdk_ws_client_module", sdk_module)

    class _BlockingWsClient:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.disconnected = threading.Event()
            self.loop: asyncio.AbstractEventLoop | None = None

        def _set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
            self.loop = loop

        def start(self) -> None:
            assert self.loop is not None
            self.started.set()
            self.loop.run_forever()

        async def _disconnect(self) -> None:
            self.disconnected.set()

    ws_client = _BlockingWsClient()
    account = FeishuAccount("main", "app", "secret", ["ou_allowed"])
    account._ws_client = ws_client
    account._stop_event.clear()
    account._ws_thread = threading.Thread(target=account._ws_loop, daemon=True)

    try:
        account._ws_thread.start()
        assert ws_client.started.wait(timeout=2.0)

        account.stop()

        assert ws_client.disconnected.is_set()
        assert account._ws_thread is None
        assert account._ws_event_loop is None
        assert ws_client.loop is not None and ws_client.loop.is_closed()
    finally:
        account.stop()
        fallback_loop.close()


def _normalized_message(
    message_id: str,
    *,
    open_id: str = "ou_allowed",
    chat_type: str = "p2p",
    mentioned_bot: bool = False,
    mentions: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        sender=SimpleNamespace(open_id=open_id),
        conversation=SimpleNamespace(chat_id="oc_chat", chat_type=chat_type),
        mentioned_bot=mentioned_bot,
        mentions=mentions or [],
    )


def test_start_keeps_routing_in_account_and_disables_sdk_text_merging(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    registered: list[object] = []

    class _Channel:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.client = object()
            self.dispatcher = object()

        def on(self, event: str, _handler: object) -> None:
            registered.append(event)

        def stop(self) -> None:
            pass

    fake_lark = SimpleNamespace(
        FeishuChannel=_Channel,
        InboundConfig=lark_channel_sdk.InboundConfig,
        LogLevel=lark_channel_sdk.LogLevel,
        OutboundConfig=lark_channel_sdk.OutboundConfig,
        PolicyConfig=lark_channel_sdk.PolicyConfig,
        RetryConfig=lark_channel_sdk.RetryConfig,
        SafetyConfig=lark_channel_sdk.SafetyConfig,
        SecurityConfig=lark_channel_sdk.SecurityConfig,
        TextBatchConfig=lark_channel_sdk.TextBatchConfig,
        Events=lark_channel_sdk.Events,
        ws=SimpleNamespace(Client=lambda *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(account_module, "_import_lark", lambda: fake_lark)

    account = FeishuAccount("main", "app", "secret", ["ou_allowed"])
    monkeypatch.setattr(account, "_ws_loop", lambda: None)
    account.start()
    account.stop()

    policy = captured["policy"]
    assert policy.require_mention is False
    assert policy.respond_to_mention_all is False
    safety = captured["safety"]
    assert safety.text_batch.max_messages == 1
    assert captured["inbound"].reaction_notifications == "all"
    outbound = captured["outbound"]
    assert outbound.retry.max_attempts == 1
    assert captured["security"].mode == "compat"
    assert {
        lark_channel_sdk.Events.RAW,
        lark_channel_sdk.Events.MESSAGE,
        lark_channel_sdk.Events.REACTION,
        lark_channel_sdk.Events.MESSAGE_READ,
        lark_channel_sdk.Events.BOT_ADDED,
        lark_channel_sdk.Events.BOT_LEAVE,
    }.issubset(set(registered))


def test_passive_channel_events_reuse_account_allowlist() -> None:
    seen: list[tuple[str, FeishuInboundChannelEvent]] = []
    account = FeishuAccount(
        "main",
        "app",
        "secret",
        ["ou_allowed"],
        on_event=lambda alias, event: seen.append((alias, event)),
    )

    account._process_channel_event(
        "reaction",
        SimpleNamespace(operator=SimpleNamespace(open_id="ou_denied")),
    )
    account._process_channel_event(
        "reaction",
        SimpleNamespace(operator=SimpleNamespace(open_id="ou_allowed")),
    )
    account._process_channel_event(
        "message_read",
        SimpleNamespace(reader=SimpleNamespace(open_id="ou_allowed")),
    )
    account._process_channel_event(
        "bot_added",
        SimpleNamespace(operator=SimpleNamespace(open_id="ou_allowed")),
    )
    account._process_channel_event(
        "bot_leave",
        SimpleNamespace(operator=SimpleNamespace(open_id="ou_allowed")),
    )

    assert [event.event_type for _alias, event in seen] == [
        "reaction",
        "message_read",
        "bot_added",
        "bot_leave",
    ]
    assert all(alias == "main" for alias, _event in seen)


def test_normalized_routing_preserves_allowlist_and_requires_group_mention() -> None:
    seen: list[tuple[str, FeishuInboundEvent]] = []
    account = FeishuAccount(
        "main",
        "app",
        "secret",
        ["ou_allowed"],
        on_message=lambda alias, event: seen.append((alias, event)),
    )
    account._bot_open_id = "ou_bot"

    envelopes = {
        message_id: {
            "schema": "2.0",
            "header": {"event_id": f"event-{message_id}"},
            "event": {"message": {"message_id": message_id}},
        }
        for message_id in ("om_dm", "om_plain_group", "om_group", "om_cached")
    }
    for envelope in envelopes.values():
        account._capture_raw_envelope(envelope)

    # Legacy allowlist semantics still apply to direct messages.
    account._process_event(_normalized_message("om_denied", open_id="ou_denied"))
    account._process_event(_normalized_message("om_dm"))
    # Groups do not wake without an explicit @Bot.
    account._process_event(_normalized_message("om_plain_group", chat_type="group"))
    account._process_event(
        _normalized_message("om_group", chat_type="group", mentioned_bot=True)
    )
    # A resolved mention also supports conservative cached-identity gating.
    account._process_event(
        _normalized_message(
            "om_cached",
            chat_type="topic",
            mentions=[SimpleNamespace(open_id="ou_bot")],
        )
    )

    assert [event.message.id for _alias, event in seen] == [
        "om_dm",
        "om_group",
        "om_cached",
    ]
    assert all(alias == "main" for alias, _event in seen)
    assert seen[0][1].feishu == envelopes["om_dm"]
    assert seen[1][1].feishu == envelopes["om_group"]
    assert seen[2][1].feishu == envelopes["om_cached"]
