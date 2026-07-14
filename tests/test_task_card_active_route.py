"""Durable single-slot Telegram inbound route fallback for ``task_card.start``.

Confirmed bug (distinct from #904): after a process refresh/molt/system
recovery, the automatic driver's turn-local ``agent._telegram_task_card_context``
is gone (heartbeat setup re-derives it only from a fresh Telegram notification
preview; teardown clears it in a ``finally``), so a public ``task_card.start``
raises ``no active Telegram chat to attach a Task Card to`` even though the
Telegram conversation the agent should resume is well known on disk.

This module locks the deterministic fallback contract:

1. Active turn-local context remains first priority, exactly as before.
2. Absent that, ``TaskCardController._resolve_route`` may fall back to exactly
   ONE producer-owned, atomically persisted latest genuine inbound Telegram
   route, written only by ``TelegramManager.on_incoming`` for accepted
   ``message``/``callback_query`` updates — never by ``telegram.read``,
   outbound ``send``, controller calls, or an ``edited_message`` update.
3. The pointer survives process refresh/molt (it is plain durable state). Each
   valid route-bearing inbound reserves manager-wide order before slow media
   work; after inbox persistence only the newest reservation may replace the
   pointer as one atomic whole record, so a slow older account thread cannot
   overwrite a later inbound and account/chat fields can never mix.
4. Missing/corrupt/malformed state, an unknown/removed account, an invalid
   chat_id, or no currently configured Telegram route all fail closed with the
   existing exact error.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from lingtai.kernel.base_agent import _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.task_card import TaskCardController
from tests._notification_store_helpers import FakeNotificationStore


# ---------------------------------------------------------------------------
# Manager-level fixtures (mirrors tests/test_telegram_task_card_singleton.py)
# ---------------------------------------------------------------------------


class FakeAccount:
    def __init__(self, alias="mybot"):
        self.alias = alias
        self._task_cards: dict[str, str] = {}
        self._next_id = 100

    def get_task_card(self, chat_id):
        return self._task_cards.get(str(chat_id))

    def set_task_card(self, chat_id, compound_id):
        self._task_cards[str(chat_id)] = compound_id

    def clear_task_card(self, chat_id):
        self._task_cards.pop(str(chat_id), None)

    def set_message_reaction(self, chat_id, message_id, reaction):
        pass

    def get_last_message_id(self, chat_id):
        return None

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        msg_id = self._next_id
        self._next_id += 1
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        return {"ok": True}

    def delete_message(self, chat_id, message_id):
        return {"ok": True}


class FakeService:
    def __init__(self, accounts):
        self._accounts = {a.alias: a for a in accounts}
        self.default_account = accounts[0]

    def get_account(self, alias):
        return self._accounts[alias]

    def list_accounts(self):
        return list(self._accounts)


def _manager(tmp_path, *accounts, on_inbound=None):
    if not accounts:
        accounts = (FakeAccount(),)
    service = FakeService(list(accounts))
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=on_inbound or (lambda _: None),
        notification_store=FakeNotificationStore(),
    )
    return manager, service


def _inbound_message_update(
    chat_id: int, message_id: int, text: str = "hi", chat_type: str = "private"
) -> dict:
    return {
        "message": {
            "message_id": message_id,
            "date": 0,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": 1, "username": "u"},
            "text": text,
        }
    }


def _inbound_callback_update(chat_id: int, message_id: int) -> dict:
    return {
        "callback_query": {
            "id": "cbid",
            "data": "press",
            "from": {"id": 1, "username": "u"},
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id, "type": "private"},
            },
        }
    }


def _resolve_fallback(manager: TelegramManager) -> dict:
    return manager._handle_task_card_update({"sub_action": "resolve_fallback_route"})


# ---------------------------------------------------------------------------
# Manager-level: on_incoming persists exactly one durable route
# ---------------------------------------------------------------------------


def test_no_inbound_yet_fallback_route_is_absent(tmp_path):
    manager, _service = _manager(tmp_path)
    result = _resolve_fallback(manager)
    assert result["status"] == "error"


def test_genuine_inbound_message_persists_fallback_route(tmp_path):
    manager, _service = _manager(tmp_path)
    manager.on_incoming("mybot", _inbound_message_update(chat_id=555, message_id=1))

    result = _resolve_fallback(manager)
    assert result["status"] == "ok"
    assert result["account"] == "mybot"
    assert result["chat_id"] == 555


def test_negative_supergroup_chat_id_is_a_valid_fallback_route(tmp_path):
    manager, _service = _manager(tmp_path)
    chat_id = -1001234567890
    manager.on_incoming(
        "mybot",
        _inbound_message_update(
            chat_id=chat_id, message_id=1, chat_type="supergroup"
        ),
    )

    result = _resolve_fallback(manager)
    assert result == {"status": "ok", "account": "mybot", "chat_id": chat_id}


def test_later_genuine_inbound_supersedes_earlier_whole_record(tmp_path):
    """Two-account/two-chat fixture: a later inbound message from a DIFFERENT
    account+chat must fully replace the pointer — never a mixed/cross-chat
    route made of stale fields from the first."""
    acct_a = FakeAccount("acct_a")
    acct_b = FakeAccount("acct_b")
    manager, _service = _manager(tmp_path, acct_a, acct_b)

    manager.on_incoming("acct_a", _inbound_message_update(chat_id=111, message_id=1))
    first = _resolve_fallback(manager)
    assert (first["account"], first["chat_id"]) == ("acct_a", 111)

    manager.on_incoming("acct_b", _inbound_message_update(chat_id=222, message_id=1))
    second = _resolve_fallback(manager)
    assert second["account"] == "acct_b"
    assert second["chat_id"] == 222
    # No mixing: never acct_a with chat 222, nor acct_b with chat 111.
    assert not (second["account"] == "acct_a" and second["chat_id"] == 222)


def test_slow_earlier_account_cannot_overwrite_later_inbound(tmp_path, monkeypatch):
    """A reserves first and stalls before inbox/route persistence. B then
    reserves and commits. When A resumes, its stale reservation must not replace
    B even though the two account pollers call ``on_incoming`` concurrently."""
    acct_a = FakeAccount("acct_a")
    acct_b = FakeAccount("acct_b")
    manager, _service = _manager(tmp_path, acct_a, acct_b)
    a_in_slow_media = threading.Event()
    release_a = threading.Event()
    errors: list[BaseException] = []

    def delayed_download(account_alias, _tg_msg, _msg_dir, _payload):
        if account_alias == "acct_a":
            a_in_slow_media.set()
            if not release_a.wait(timeout=5):
                raise TimeoutError("test did not release the earlier inbound")

    monkeypatch.setattr(manager, "_download_media", delayed_download)

    def receive_a():
        try:
            manager.on_incoming(
                "acct_a", _inbound_message_update(chat_id=111, message_id=1)
            )
        except BaseException as exc:
            errors.append(exc)

    a_thread = threading.Thread(target=receive_a)
    a_thread.start()
    try:
        assert a_in_slow_media.wait(timeout=5)
        manager.on_incoming(
            "acct_b", _inbound_message_update(chat_id=222, message_id=1)
        )
        assert _resolve_fallback(manager) == {
            "status": "ok",
            "account": "acct_b",
            "chat_id": 222,
        }
    finally:
        release_a.set()
        a_thread.join(timeout=5)

    assert not a_thread.is_alive()
    assert not errors
    assert _resolve_fallback(manager) == {
        "status": "ok",
        "account": "acct_b",
        "chat_id": 222,
    }


def test_callback_query_inbound_also_establishes_route(tmp_path):
    manager, _service = _manager(tmp_path)
    manager.on_incoming("mybot", _inbound_callback_update(chat_id=777, message_id=9))
    result = _resolve_fallback(manager)
    assert result["status"] == "ok"
    assert result["chat_id"] == 777


def test_inline_callback_without_chat_never_establishes_or_overwrites_route(tmp_path):
    manager, _service = _manager(tmp_path)
    inline_callback = {
        "callback_query": {
            "id": "inline-cbid",
            "data": "press",
            "from": {"id": 1, "username": "u"},
            "inline_message_id": "inline-message",
        }
    }

    # Inline callback queries have no message/chat route. The manager's existing
    # callback parsing uses chat_id=0 for them, which must not become authority.
    manager.on_incoming("mybot", inline_callback)
    assert _resolve_fallback(manager)["status"] == "error"

    manager.on_incoming("mybot", _inbound_message_update(chat_id=555, message_id=1))
    before = _resolve_fallback(manager)
    manager.on_incoming("mybot", inline_callback)
    assert _resolve_fallback(manager) == before


def test_edited_message_never_establishes_or_overwrites_route(tmp_path):
    manager, _service = _manager(tmp_path)
    manager.on_incoming("mybot", _inbound_message_update(chat_id=555, message_id=1))
    before = _resolve_fallback(manager)

    # An edit to the SAME message must not (re)write route authority — it is
    # not a new genuine inbound message.
    edited = {
        "edited_message": {
            "message_id": 1,
            "date": 0,
            "edit_date": 1,
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 1, "username": "u"},
            "text": "hi (edited)",
        }
    }
    manager.on_incoming("mybot", edited)
    after = _resolve_fallback(manager)
    assert after == before


def test_unrelated_edited_message_from_different_chat_does_not_create_route(tmp_path):
    manager, _service = _manager(tmp_path)
    # No genuine inbound message has ever arrived; an edited_message alone
    # (orphan, no matching inbox entry) must never establish a fallback route.
    edited = {
        "edited_message": {
            "message_id": 999,
            "date": 0,
            "edit_date": 1,
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 1, "username": "u"},
            "text": "orphan edit",
        }
    }
    manager.on_incoming("mybot", edited)
    result = _resolve_fallback(manager)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Manager-level: fail-closed validation
# ---------------------------------------------------------------------------


def test_corrupt_persisted_route_fails_closed(tmp_path):
    manager, _service = _manager(tmp_path)
    manager.on_incoming("mybot", _inbound_message_update(chat_id=555, message_id=1))
    route_path = Path(tmp_path) / "telegram" / "task_card_route.json"
    assert route_path.is_file()
    route_path.write_text("{not valid json", encoding="utf-8")

    result = _resolve_fallback(manager)
    assert result["status"] == "error"


def test_malformed_route_missing_fields_fails_closed(tmp_path):
    manager, _service = _manager(tmp_path)
    route_path = Path(tmp_path) / "telegram" / "task_card_route.json"
    route_path.parent.mkdir(parents=True, exist_ok=True)
    route_path.write_text(json.dumps({"account": "mybot"}), encoding="utf-8")

    result = _resolve_fallback(manager)
    assert result["status"] == "error"


@pytest.mark.parametrize("bad_chat_id", ["not-an-int", True, 0])
def test_invalid_chat_id_type_or_zero_fails_closed(tmp_path, bad_chat_id):
    manager, _service = _manager(tmp_path)
    route_path = Path(tmp_path) / "telegram" / "task_card_route.json"
    route_path.parent.mkdir(parents=True, exist_ok=True)
    route_path.write_text(
        json.dumps({"account": "mybot", "chat_id": bad_chat_id}), encoding="utf-8"
    )

    result = _resolve_fallback(manager)
    assert result["status"] == "error"


def test_unknown_account_in_persisted_route_fails_closed(tmp_path):
    manager, _service = _manager(tmp_path)
    manager.on_incoming("mybot", _inbound_message_update(chat_id=555, message_id=1))

    # Simulate the account being removed from the currently loaded config —
    # the durable fallback MUST validate against the live manager/accounts.
    _service._accounts.pop("mybot")
    _service._accounts["otherbot"] = FakeAccount("otherbot")

    result = _resolve_fallback(manager)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Manager-level: telegram.read / outbound send never touch route authority
# ---------------------------------------------------------------------------


def test_telegram_read_does_not_create_route(tmp_path):
    manager, _service = _manager(tmp_path)
    manager.handle({"action": "read", "chat_id": 555})
    result = _resolve_fallback(manager)
    assert result["status"] == "error"


def test_outbound_send_does_not_create_or_overwrite_route(tmp_path, monkeypatch):
    manager, service = _manager(tmp_path)
    acct = service.default_account

    def _fake_send(chat_id, text, reply_to_message_id=None, **kwargs):
        return {"message_id": 42}

    acct.send_message = _fake_send  # type: ignore[attr-defined]

    # No inbound route exists yet — an outbound send must not create one.
    manager.handle(
        {"action": "send", "account": "mybot", "chat_id": 555, "text": "hello"}
    )
    result = _resolve_fallback(manager)
    assert result["status"] == "error"

    # Establish a genuine inbound route from a DIFFERENT chat, then send
    # outbound to the first chat — outbound must not overwrite the pointer.
    manager.on_incoming("mybot", _inbound_message_update(chat_id=999, message_id=1))
    manager.handle(
        {"action": "send", "account": "mybot", "chat_id": 555, "text": "hello"}
    )
    after = _resolve_fallback(manager)
    assert after["chat_id"] == 999


# ---------------------------------------------------------------------------
# Controller-level integration: exactly the failure scenario from the bug
# report, end to end through TaskCardController._resolve_route.
# ---------------------------------------------------------------------------


class _RouteAwareClient:
    """Fake MCP client that dispatches straight into a real TelegramManager,
    exactly like the production reverse channel does — so the controller-level
    test exercises the real ``_resolve_route`` -> reverse-call -> manager path
    with NO manually injected active context (per the task spec: "no
    manual-only context shortcuts for the recovery test")."""

    def __init__(self, manager: TelegramManager) -> None:
        self.manager = manager
        self.calls: list = []

    def call_tool(self, name, args, timeout=None):
        self.calls.append((name, dict(args)))
        assert name == _TASK_CARD_TOOL
        return self.manager._handle_task_card_update(args)


class _RecoveredAgent:
    """Simulates a fresh agent/controller after refresh/molt/system recovery:
    NO active turn-local context, matching real teardown behavior."""

    def __init__(self, working_dir: Path, manager: TelegramManager) -> None:
        self._working_dir = working_dir
        self._client = _RouteAwareClient(manager)
        self._mcp_clients_by_tool = {"telegram": self._client}
        self._telegram_task_card_context = None
        self._shutdown = threading.Event()

    def _enqueue_system_notification(self, **kwargs):
        return "notif-id"

    def add_tool(self, *a, **k):
        pass


def _write_renderer(workdir: Path, name: str = "r.py") -> str:
    path = workdir / name
    path.write_text("import json; print(json.dumps({'title': 'T', 'lines': ['a']}))")
    return str(path)


def test_no_active_context_and_no_durable_route_fails_with_exact_error(tmp_path):
    manager, _service = _manager(tmp_path)
    agent = _RecoveredAgent(Path(tmp_path), manager)
    controller = TaskCardController(agent)

    result = controller.handle(
        {"action": "start", "renderer_path": _write_renderer(Path(tmp_path))}
    )
    assert result["status"] == "error"
    assert result["message"] == "no active Telegram chat to attach a Task Card to"


def test_start_after_refresh_uses_durable_route_from_genuine_inbound(tmp_path):
    """The core regression: genuine accepted inbound A, then a refresh-equivalent
    new controller/new agent with NO injected active context — start must use
    the exact A account/chat, proving the recovery path end to end."""
    manager, _service = _manager(tmp_path)
    manager.on_incoming("mybot", _inbound_message_update(chat_id=555, message_id=1))

    agent = _RecoveredAgent(Path(tmp_path), manager)
    controller = TaskCardController(agent)

    result = controller.handle(
        {"action": "start", "renderer_path": _write_renderer(Path(tmp_path))}
    )
    assert result["status"] == "ok"
    watch = controller._watches[result["watch_id"]]
    assert watch.account == "mybot"
    assert watch.chat_id == 555
    controller.handle({"action": "stop", "watch_id": result["watch_id"]})


def test_active_context_overrides_persisted_fallback(tmp_path):
    manager, _service = _manager(tmp_path)
    # Durable fallback points at chat 555...
    manager.on_incoming("mybot", _inbound_message_update(chat_id=555, message_id=1))

    agent = _RecoveredAgent(Path(tmp_path), manager)
    # ...but an active turn-local context (fresh Telegram-originated turn) B
    # exists and must take priority.
    agent._telegram_task_card_context = {"account": "mybot", "chat_id": 888}
    controller = TaskCardController(agent)

    result = controller.handle(
        {"action": "start", "renderer_path": _write_renderer(Path(tmp_path))}
    )
    assert result["status"] == "ok"
    watch = controller._watches[result["watch_id"]]
    assert watch.account == "mybot"
    assert watch.chat_id == 888
    controller.handle({"action": "stop", "watch_id": result["watch_id"]})


def test_later_genuine_inbound_supersedes_used_by_new_start(tmp_path):
    acct_a = FakeAccount("acct_a")
    acct_b = FakeAccount("acct_b")
    manager, _service = _manager(tmp_path, acct_a, acct_b)

    manager.on_incoming("acct_a", _inbound_message_update(chat_id=111, message_id=1))
    manager.on_incoming("acct_b", _inbound_message_update(chat_id=222, message_id=1))

    agent = _RecoveredAgent(Path(tmp_path), manager)
    controller = TaskCardController(agent)
    result = controller.handle(
        {"action": "start", "renderer_path": _write_renderer(Path(tmp_path))}
    )
    assert result["status"] == "ok"
    watch = controller._watches[result["watch_id"]]
    assert watch.account == "acct_b"
    assert watch.chat_id == 222
    controller.handle({"action": "stop", "watch_id": result["watch_id"]})
