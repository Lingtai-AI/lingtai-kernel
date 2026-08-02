"""Focused compatibility coverage for the low-level lark-channel-sdk adapter."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from types import SimpleNamespace

from lingtai.mcp_servers.feishu import account as account_module
from lingtai.mcp_servers.feishu.account import FeishuAccount


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

    reaction = requests["reaction"]
    assert reaction.message_id == "om_original"
    assert reaction.request_body.reaction_type.emoji_type == "OK"

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
