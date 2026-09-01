"""Manager/server integration gates for target-only Telegram Agent switching."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lingtai.mcp_servers.telegram import account as account_module
from lingtai.mcp_servers.telegram import agent_switching as switching
from lingtai.mcp_servers.telegram import manager as manager_module
from lingtai.mcp_servers.telegram import server
from lingtai.mcp_servers.telegram.agent_switching import (
    EligibleTarget,
    TelegramAgentSwitchingRouter,
)
from lingtai.mcp_servers.telegram.account import TelegramAccount, TelegramAccountStopError
from lingtai.mcp_servers.telegram.manager import (
    REACTION_RECEIVED,
    TelegramManager,
    TelegramManagerStopError,
)
from lingtai.mcp_servers.telegram.service import TelegramService, TelegramServiceStopError
from tests._notification_store_helpers import FakeNotificationStore


class _Account:
    alias = "main"

    def __init__(self) -> None:
        self.reactions: list[tuple[int, int, str]] = []
        self.sent: list[tuple[int, str, dict[str, Any]]] = []

    def public_identity(self) -> dict[str, str]:
        return {"bot_username": "OwnerBot"}

    def set_message_reaction(self, chat_id: int, message_id: int, reaction: str) -> None:
        self.reactions.append((chat_id, message_id, reaction))

    def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        self.sent.append((chat_id, text, kwargs))
        return {"message_id": 9001, "chat": {"id": chat_id}, "text": text}


class _Service:
    def __init__(self, events: list[str] | None = None) -> None:
        self.default_account = _Account()
        self.events = events if events is not None else []

    def get_account(self, alias: str) -> _Account:
        assert alias == "main"
        return self.default_account

    def list_accounts(self) -> list[str]:
        return ["main"]

    def start(self) -> None:
        self.events.append("service.start")

    def stop(self) -> None:
        self.events.append("service.stop")


class _Router:
    def __init__(
        self,
        *,
        handled: bool = True,
        events: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.handled = handled
        self.events = events if events is not None else []
        self.error = error
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def start(self) -> None:
        self.events.append("router.start")

    def stop(self) -> None:
        self.events.append("router.stop")

    def handle(self, account: str, update: dict[str, Any], branch: str) -> bool:
        self.calls.append((account, update, branch))
        if self.error is not None:
            raise self.error
        return self.handled


def _update(text: str = "@worker do the task") -> dict[str, Any]:
    return {
        "update_id": 44,
        "message": {
            "message_id": 55,
            "date": 1781600000,
            "from": {
                "id": 7,
                "is_bot": False,
                "first_name": "Alice",
                "username": "alice",
            },
            "chat": {"id": 123, "type": "private", "username": "alice"},
            "text": text,
        },
    }


def _manager(
    tmp_path: Path,
    *,
    router: _Router | None,
    inbound: list[dict[str, Any]] | None = None,
    events: list[str] | None = None,
) -> tuple[TelegramManager, _Service]:
    service = _Service(events)
    manager = TelegramManager(
        service,
        working_dir=tmp_path,
        notification_store=FakeNotificationStore(),
        on_inbound=(inbound.append if inbound is not None else (lambda _event: True)),
        agent_switching_router=router,
    )
    return manager, service


def _raw_records(tmp_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    inbox = tmp_path / "telegram" / "main" / "inbox"
    if inbox.is_dir():
        for path in sorted(inbox.glob("*/message.json")):
            records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def test_direct_admin_reply_is_identity_labeled_when_switching_is_active(
    tmp_path: Path,
) -> None:
    manager, service = _manager(tmp_path, router=_Router(handled=False))

    result = manager._reply(
        {
            "message_id": "main:123:117",
            "text": "Here is the folder structure.",
            "rendering_mode": "Markdown",
        }
    )

    assert result["status"] == "sent"
    assert service.default_account.sent == [
        (
            123,
            "[admin] Here is the folder structure.",
            {
                "reply_markup": None,
                "reply_to_message_id": 117,
                "parse_mode": "Markdown",
            },
        )
    ]
    sent_records = list((tmp_path / "telegram" / "main" / "sent").glob("*/message.json"))
    assert len(sent_records) == 1
    sent = json.loads(sent_records[0].read_text(encoding="utf-8"))
    assert sent["text"] == "[admin] Here is the folder structure."
    assert sent["reply_to_message_id"] == 117


def test_manager_lifecycle_nests_router_between_service_and_pollers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    router = _Router(events=events)
    manager, _service = _manager(tmp_path, router=router, events=events)
    monkeypatch.setattr(
        manager,
        "_start_pending_task_card_edit_worker",
        lambda: events.append("pending.start"),
    )
    monkeypatch.setattr(manager, "_start_task_card_tail", lambda: events.append("tail.start"))
    monkeypatch.setattr(
        manager,
        "_start_programmable_task_card_poller",
        lambda: events.append("programmable.start"),
    )
    monkeypatch.setattr(
        manager,
        "_stop_programmable_task_card_poller",
        lambda: events.append("programmable.stop"),
    )
    monkeypatch.setattr(manager, "_stop_task_card_tail", lambda: events.append("tail.stop"))
    monkeypatch.setattr(
        manager,
        "_stop_pending_task_card_edit_worker",
        lambda: events.append("pending.stop"),
    )
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_all",
        lambda: events.append("typing.stop_all"),
    )

    manager.start()
    manager.stop()

    assert events == [
        "service.start",
        "router.start",
        "pending.start",
        "tail.start",
        "programmable.start",
        "programmable.stop",
        "tail.stop",
        "pending.stop",
        "router.stop",
        "service.stop",
        "typing.stop_all",
    ]


def test_manager_feature_off_keeps_existing_lifecycle_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    manager, _service = _manager(tmp_path, router=None, events=events)
    monkeypatch.setattr(
        manager,
        "_start_pending_task_card_edit_worker",
        lambda: events.append("pending.start"),
    )
    monkeypatch.setattr(manager, "_start_task_card_tail", lambda: events.append("tail.start"))
    monkeypatch.setattr(
        manager,
        "_start_programmable_task_card_poller",
        lambda: events.append("programmable.start"),
    )
    monkeypatch.setattr(
        manager,
        "_stop_programmable_task_card_poller",
        lambda: events.append("programmable.stop"),
    )
    monkeypatch.setattr(manager, "_stop_task_card_tail", lambda: events.append("tail.stop"))
    monkeypatch.setattr(
        manager,
        "_stop_pending_task_card_edit_worker",
        lambda: events.append("pending.stop"),
    )
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_all",
        lambda: events.append("typing.stop_all"),
    )

    manager.start()
    manager.stop()

    assert events == [
        "service.start",
        "pending.start",
        "tail.start",
        "programmable.start",
        "programmable.stop",
        "tail.stop",
        "pending.stop",
        "service.stop",
        "typing.stop_all",
    ]


def test_manager_partial_start_retains_then_clears_unstarted_pending_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    created: list[object] = []

    class UnstartedThread:
        ident = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def start(self) -> None:
            events.append("pending.start")
            raise RuntimeError("pending thread start failed")

    router = _Router(events=events)
    manager, _service = _manager(tmp_path, router=router, events=events)
    original_programmable_stop = manager._stop_programmable_task_card_poller
    original_tail_stop = manager._stop_task_card_tail
    original_pending_stop = manager._stop_pending_task_card_edit_worker

    monkeypatch.setattr(manager_module.threading, "Thread", UnstartedThread)
    monkeypatch.setattr(
        manager,
        "_stop_programmable_task_card_poller",
        lambda: (events.append("programmable.stop"), original_programmable_stop())[1],
    )
    monkeypatch.setattr(
        manager,
        "_stop_task_card_tail",
        lambda: (events.append("tail.stop"), original_tail_stop())[1],
    )
    monkeypatch.setattr(
        manager,
        "_stop_pending_task_card_edit_worker",
        lambda: (events.append("pending.stop"), original_pending_stop())[1],
    )
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_all",
        lambda: events.append("typing.stop_all"),
    )

    with pytest.raises(RuntimeError, match="pending thread start failed"):
        manager.start()
    assert manager._task_card_pending_edit_thread is created[0]
    assert manager._task_card_tail_thread is None
    assert manager._programmable_task_card_thread is None
    with pytest.raises(RuntimeError, match="pending task card edit lifecycle already retained"):
        manager._start_pending_task_card_edit_worker()

    manager.stop()

    assert manager._task_card_pending_edit_thread is None
    assert events == [
        "service.start",
        "router.start",
        "pending.start",
        "programmable.stop",
        "tail.stop",
        "pending.stop",
        "router.stop",
        "service.stop",
        "typing.stop_all",
    ]


def test_handled_update_stops_after_raw_owner_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = _Router(handled=True)
    inbound: list[dict[str, Any]] = []
    manager, service = _manager(tmp_path, router=router, inbound=inbound)
    typing: list[tuple[str, int]] = []
    monkeypatch.setattr(
        manager_module._typing_manager,
        "start_typing",
        lambda _account, chat_id: typing.append(("start", chat_id)),
    )
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_typing",
        lambda _account, chat_id: typing.append(("stop", chat_id)),
    )
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_args, **_kwargs: pytest.fail("handled update reached admin preview"),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_task_card_resident",
        lambda *_args, **_kwargs: pytest.fail("handled update reached admin Task Card"),
    )

    update = _update()
    manager.on_incoming("main", update)

    assert router.calls == [("main", update, "message")]
    assert typing == [("start", 123), ("stop", 123)]
    assert inbound == []
    records = _raw_records(tmp_path)
    assert len(records) == 1
    assert records[0]["text"] == "@worker do the task"
    assert records[0]["telegram"]["update"] == update
    assert service.default_account.reactions == [(123, 55, REACTION_RECEIVED)]


def test_real_router_delivers_once_to_target_and_never_projects_admin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = tmp_path / "network" / "owner"
    target_dir = tmp_path / "network" / "alpha"
    owner.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    service = _Service()
    target = EligibleTarget(
        name="alpha",
        agent_id="agent-a",
        workdir=target_dir,
        manifest_digest="a" * 64,
        ledger_chain_digest="b" * 64,
    )
    router = TelegramAgentSwitchingRouter(
        owner_workdir=owner,
        service=service,
        accounts_config=[
            {"alias": "main", "agent_switching": {"enabled": True}}
        ],
    )
    monkeypatch.setattr(
        router,
        "_resolve_name",
        lambda name: (target, False) if name == "alpha" else (None, False),
    )
    monkeypatch.setattr(router, "_register_adapter", lambda _target: SimpleNamespace())
    inbound: list[dict[str, Any]] = []
    manager = TelegramManager(
        service,
        working_dir=owner,
        notification_store=FakeNotificationStore(),
        on_inbound=inbound.append,
        agent_switching_router=router,
    )
    typing: list[tuple[str, int]] = []
    monkeypatch.setattr(
        manager_module._typing_manager,
        "start_typing",
        lambda _account, chat_id: typing.append(("start", chat_id)),
    )
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_typing",
        lambda _account, chat_id: typing.append(("stop", chat_id)),
    )
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_args, **_kwargs: pytest.fail("target route reached admin preview"),
    )

    update = _update("@alpha private human words")
    manager.on_incoming("main", update)

    route_id = router._route_event_id("main", 123, 7, 55, update)
    target_event_path = router._target_event_path(target, "as-" + route_id)
    target_event = json.loads(target_event_path.read_text(encoding="utf-8"))
    assert target_event["body"].endswith("\n\nHuman message:\nprivate human words")
    assert "channel_reply(action='submit')" in target_event["body"]
    assert target_event["metadata"]["target_agent_id"] == "agent-a"
    assert target_event["metadata"]["delivery_semantics"] == "at-most-once/v1"
    assert inbound == []
    assert typing == [("start", 123), ("stop", 123)]
    assert len(_raw_records(owner)) == 1
    assert service.default_account.sent == []


@pytest.mark.parametrize(
    "forward_fields",
    [
        {"forward_origin": {"type": "user", "date": 1, "sender_user": {"id": 9}}},
        {"forward_from": {"id": 9}, "forward_date": 1},
        {"forward_from_chat": {"id": -9}, "forward_from_message_id": 8, "forward_date": 1},
    ],
    ids=("current-origin", "legacy-user", "legacy-chat"),
)
@pytest.mark.parametrize("route_mode", ["saved", "one-shot"])
def test_forwarded_switching_text_is_local_before_admin_or_target_wake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forward_fields: dict[str, Any],
    route_mode: str,
) -> None:
    owner = tmp_path / "network" / "owner"
    target_dir = tmp_path / "network" / "alpha"
    owner.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    service = _Service()
    router = TelegramAgentSwitchingRouter(
        owner_workdir=owner,
        service=service,
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )
    target = EligibleTarget(
        name="alpha",
        agent_id="agent-a",
        workdir=target_dir,
        manifest_digest="a" * 64,
        ledger_chain_digest="b" * 64,
    )
    if route_mode == "saved":
        router._state.save_selection("main", 123, 7, target)
        text = "forwarded words"
    else:
        text = "@alpha forwarded words"
    monkeypatch.setattr(
        router,
        "_route",
        lambda *_args, **_kwargs: pytest.fail("forwarded text reached target route"),
    )
    inbound: list[dict[str, Any]] = []
    manager = TelegramManager(
        service,
        working_dir=owner,
        notification_store=FakeNotificationStore(),
        on_inbound=inbound.append,
        agent_switching_router=router,
    )
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_args, **_kwargs: pytest.fail("forwarded switching text reached admin preview"),
    )
    update = _update(text)
    update["message"].update(forward_fields)

    manager.on_incoming("main", update)

    assert inbound == []
    assert service.default_account.sent == [
        (
            123,
            "[admin] Agent routing supports non-forwarded plain text messages only.",
            {"reply_markup": None, "reply_to_message_id": 55},
        )
    ]
    assert not (target_dir / ".mcp_inbox").exists()
    assert not (owner / ".mcp_inbox").exists()
    assert len(_raw_records(owner)) == 1



def test_selected_start_stays_admin_and_never_wakes_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = tmp_path / "network" / "owner"
    target_dir = tmp_path / "network" / "alpha"
    owner.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    service = _Service()
    router = TelegramAgentSwitchingRouter(
        owner_workdir=owner,
        service=service,
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )
    target = EligibleTarget(
        "alpha", "agent-a", target_dir, "a" * 64, "b" * 64,
    )
    router._state.save_selection("main", 123, 7, target)
    monkeypatch.setattr(
        router, "_route", lambda *_args, **_kwargs: pytest.fail("/start reached target")
    )
    inbound: list[dict[str, Any]] = []
    manager = TelegramManager(
        service,
        working_dir=owner,
        notification_store=FakeNotificationStore(),
        on_inbound=inbound.append,
        agent_switching_router=router,
    )
    update = _update("/start@OwnerBot setup")
    manager.on_incoming("main", update)

    assert len(inbound) == 1
    assert inbound[0]["metadata"]["type"] == "message"
    assert router._state.load_selection("main", 123, 7)["target_name"] == "alpha"
    assert not (target_dir / ".mcp_inbox").exists()
    records = _raw_records(owner)
    assert len(records) == 1
    assert records[0]["text"] == "/start@OwnerBot setup"
    assert service.default_account.sent == []


def test_router_exception_fails_closed_after_raw_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = _Router(error=RuntimeError("router failed"))
    inbound: list[dict[str, Any]] = []
    manager, _service = _manager(tmp_path, router=router, inbound=inbound)
    typing: list[tuple[str, int]] = []
    monkeypatch.setattr(
        manager_module._typing_manager,
        "start_typing",
        lambda _account, chat_id: typing.append(("start", chat_id)),
    )
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_typing",
        lambda _account, chat_id: typing.append(("stop", chat_id)),
    )
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_args, **_kwargs: pytest.fail("router failure fell through to admin"),
    )

    manager.on_incoming("main", _update())

    assert typing == [("start", 123), ("stop", 123)]
    assert inbound == []
    assert len(_raw_records(tmp_path)) == 1


def test_unhandled_update_preserves_existing_admin_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = _Router(handled=False)
    inbound: list[dict[str, Any]] = []
    manager, _service = _manager(tmp_path, router=router, inbound=inbound)
    typing: list[tuple[str, int]] = []
    monkeypatch.setattr(
        manager_module._typing_manager,
        "start_typing",
        lambda _account, chat_id: typing.append(("start", chat_id)),
    )
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_typing",
        lambda _account, chat_id: typing.append(("stop", chat_id)),
    )

    update = _update("ordinary admin message")
    manager.on_incoming("main", update)

    assert router.calls == [("main", update, "message")]
    assert typing == [("start", 123)]
    assert len(inbound) == 1
    assert inbound[0]["metadata"]["type"] == "message"
    assert len(_raw_records(tmp_path)) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_server_composes_prepared_accounts_and_optional_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    raw_accounts = [
        {
            "alias": "main",
            "bot_token": "123:TEST",
            "agent_switching": {"enabled": enabled},
        }
    ]
    prepared_accounts = [dict(raw_accounts[0]), {"_prepared": True}]
    calls: dict[str, Any] = {}

    config_path = tmp_path / "telegram.json"
    monkeypatch.setattr(
        server,
        "_load_config_with_source",
        lambda: ({"accounts": raw_accounts}, config_path),
    )
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))

    def prepare(
        accounts: list[dict[str, Any]],
        *,
        default_commands: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        calls["prepare"] = accounts
        calls["default_commands"] = default_commands
        return prepared_accounts

    monkeypatch.setattr(server, "prepare_account_configs", prepare, raising=False)

    class Service:
        def __init__(self, **kwargs: Any) -> None:
            calls["service"] = kwargs

    monkeypatch.setattr(server, "TelegramService", Service)
    router = object() if enabled else None

    def build_router(**kwargs: Any) -> object | None:
        calls["router"] = kwargs
        return router

    monkeypatch.setattr(server, "build_agent_switching_router", build_router, raising=False)

    class Manager:
        def __init__(self, **kwargs: Any) -> None:
            calls["manager"] = kwargs

    monkeypatch.setattr(server, "TelegramManager", Manager)

    manager, working_dir = server.build_manager()

    assert working_dir == tmp_path
    assert calls["prepare"] is not raw_accounts
    assert calls["prepare"] == raw_accounts
    assert calls["default_commands"] is server.DEFAULT_COMMANDS
    assert calls["service"]["accounts_config"] is prepared_accounts
    assert calls["service"]["config_source"] == str(config_path)
    assert calls["router"] == {
        "owner_workdir": tmp_path,
        "service": calls["service"] and calls["manager"]["service"],
        "accounts_config": prepared_accounts,
    }
    assert calls["manager"]["agent_switching_router"] is router
    assert manager is not None


def test_corrupt_persisted_selection_is_local_raw_only_with_zero_provider_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = tmp_path / "network" / "owner"
    target_dir = tmp_path / "network" / "alpha"
    owner.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    service = _Service()
    router = TelegramAgentSwitchingRouter(
        owner_workdir=owner,
        service=service,
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )
    selection_path = router._state._selection_path("main", 123, 7)
    selection_path.write_text("{corrupt", encoding="utf-8")
    monkeypatch.setattr(
        router,
        "_route",
        lambda *_args, **_kwargs: pytest.fail("corrupt selection reached target route"),
    )
    inbound: list[dict[str, Any]] = []
    manager = TelegramManager(
        service,
        working_dir=owner,
        notification_store=FakeNotificationStore(),
        on_inbound=inbound.append,
        agent_switching_router=router,
    )
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_args, **_kwargs: pytest.fail("corrupt selection reached admin preview"),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_task_card_resident",
        lambda *_args, **_kwargs: pytest.fail("corrupt selection reached admin Task Card"),
    )

    manager.on_incoming("main", _update("ordinary private words"))

    assert inbound == []
    assert len(_raw_records(owner)) == 1
    assert service.default_account.sent == [
        (
            123,
            "[admin] Saved Agent selection is unavailable. Use /agent reset or select an Agent again.",
            {"reply_markup": None, "reply_to_message_id": 55},
        )
    ]
    assert not (owner / ".mcp_inbox").exists()
    assert not (target_dir / ".mcp_inbox").exists()
    reply_json = list(router._reply_root.rglob("*.json"))
    assert {path.name for path in reply_json} <= {"owner-maintenance-progress.json"}
    assert not list((router._reply_root / "grants").glob("*.json"))
    assert not list((router._reply_root / "route_events").glob("*.json"))
    assert not list((router._reply_root / "route_decisions").glob("*.json"))
    assert router._state.read_selection("main", 123, 7).status == "unavailable"


@pytest.mark.parametrize(
    "corruption",
    [
        "wrong-field-type",
        "missing-field",
        "unsafe-agent-id",
        "read-oserror",
        "missing-marker",
        "wrong-marker",
        "quarantined",
        "reset",
        "unsafe-capsule",
    ],
)
def test_production_manager_ingress_negative_state_matrix_is_raw_only_no_wake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    owner = tmp_path / "network" / "owner"
    target_dir = tmp_path / "network" / "alpha"
    owner.mkdir(parents=True)
    target_dir.mkdir()
    service = _Service()
    router = TelegramAgentSwitchingRouter(
        owner_workdir=owner,
        service=service,
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )
    target = EligibleTarget("alpha", "agent-a", target_dir, "a" * 64, "b" * 64)
    router._state.save_selection("main", 123, 7, target)
    selection_path = router._state._selection_path("main", 123, 7)
    record = json.loads(selection_path.read_text(encoding="utf-8"))
    if corruption == "wrong-field-type":
        record["protocol_version"] = "1"
        switching._atomic_private_json(selection_path, record)
    elif corruption == "missing-field":
        record.pop("manifest_digest")
        switching._atomic_private_json(selection_path, record)
    elif corruption == "unsafe-agent-id":
        record["target_agent_id"] = "../agent-a"
        switching._atomic_private_json(selection_path, record)
    elif corruption == "read-oserror":
        monkeypatch.setattr(
            switching,
            "_read_private_json",
            lambda path: (_ for _ in ()).throw(OSError("synthetic read failure"))
            if path == selection_path
            else None,
        )
    elif corruption in {"missing-marker", "wrong-marker"}:
        manifest = target_dir / ".agent.json"
        manifest.write_text(
            json.dumps(
                {
                    "name": "alpha",
                    "agent_id": "agent-a",
                    "channel_reply": None if corruption == "missing-marker" else "wrong/v1",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(router, "_resolve_name", lambda _name: (None, False))
    elif corruption == "quarantined":
        selection_path.write_text("{", encoding="utf-8")
        assert router._state.read_selection("main", 123, 7).status == "unavailable"
    elif corruption == "reset":
        assert router._state.clear_selection("main", 123, 7)
        # An explicit selector still fails closed when the target cannot validate.
        monkeypatch.setattr(router, "_resolve_name", lambda _name: (None, False))
    elif corruption == "unsafe-capsule":
        capsule = target_dir / ".channel_reply" / "active_capsule.json"
        capsule.parent.mkdir(mode=0o700)
        capsule.symlink_to(tmp_path / "outside-capsule")
        # Force the production capsule validator to inspect the unsafe existing
        # shape rather than replacing the synthetic path in test setup.
        monkeypatch.setattr(
            switching.ChannelReplyTargetCapsule,
            "create",
            lambda **_kwargs: (_ for _ in ()).throw(ValueError("capsule_unavailable")),
        )
    if corruption not in {"missing-marker", "wrong-marker", "reset"}:
        monkeypatch.setattr(router, "_resolve_name", lambda _name: (target, False))

    inbound: list[dict[str, Any]] = []
    manager = TelegramManager(
        service,
        working_dir=owner,
        notification_store=FakeNotificationStore(),
        on_inbound=inbound.append,
        agent_switching_router=router,
    )
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_a, **_k: pytest.fail("negative route reached admin ChatSession preview"),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_task_card_resident",
        lambda *_a, **_k: pytest.fail("negative route reached admin Task Card"),
    )
    text = "@alpha private words" if corruption == "reset" else "ordinary private words"
    manager.on_incoming("main", _update(text))

    assert inbound == []
    assert len(_raw_records(owner)) == 1
    assert not (owner / ".mcp_inbox").exists()
    assert not any((target_dir / ".mcp_inbox").glob("**/*.json"))


def test_service_partial_start_is_exhaustively_cleaned_without_starting_later_accounts():
    events: list[str] = []

    class Account:
        def __init__(self, alias: str, *, start_failure: bool = False) -> None:
            self.alias = alias
            self.start_failure = start_failure

        def start(self) -> None:
            events.append(f"start:{self.alias}")
            if self.start_failure:
                raise RuntimeError(f"start {self.alias}")

        def stop(self) -> None:
            events.append(f"stop:{self.alias}")

    service = TelegramService.__new__(TelegramService)
    service._account_order = ["one", "two", "three"]
    service._accounts = {
        "one": Account("one"),
        "two": Account("two", start_failure=True),
        "three": Account("three"),
    }

    with pytest.raises(RuntimeError, match="start two"):
        service.start()
    service.stop()

    assert events == [
        "start:one",
        "start:two",
        "stop:one",
        "stop:two",
        "stop:three",
    ]


def test_service_stop_attempts_every_account_and_aggregates_multiple_failures():
    calls: list[str] = []

    class Account:
        def __init__(self, alias: str, fail: bool) -> None:
            self.alias = alias
            self.fail = fail

        def stop(self) -> None:
            calls.append(self.alias)
            if self.fail:
                raise RuntimeError(f"stop {self.alias}")

    service = TelegramService.__new__(TelegramService)
    service._account_order = ["one", "two", "three"]
    service._accounts = {
        "one": Account("one", True),
        "two": Account("two", True),
        "three": Account("three", False),
    }

    with pytest.raises(TelegramServiceStopError) as raised:
        service.stop()

    assert calls == ["one", "two", "three"]
    assert [name for name, _exc in raised.value.failures] == ["one", "two"]


def test_real_account_stop_interrupts_long_poll_and_never_dispatches_late_batch(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    dispatched: list[dict[str, Any]] = []

    class Client:
        def close(self):
            release.set()

    account = TelegramAccount(
        "main", "test-token", None, state_dir=tmp_path, on_message=lambda _a, u: dispatched.append(u)
    )
    account._client = Client()

    def request(method, **_kwargs):
        assert method == "getUpdates"
        entered.set()
        assert release.wait(1.0)
        return [{"update_id": 1, "message": {"chat": {"id": 1}, "message_id": 2}}]

    account._request = request
    thread = threading.Thread(target=account._poll_loop)
    account._poll_thread = thread
    account._stop_event.clear()
    thread.start()
    assert entered.wait(1.0)
    account.stop()
    assert dispatched == []
    assert account._poll_thread is None
    assert account._client is None


def test_real_account_retains_live_or_join_uncertainty_then_retry_converges(tmp_path):
    account = TelegramAccount("main", "test-token", None, state_dir=tmp_path)

    class Client:
        def __init__(self):
            self.calls = 0

        def close(self):
            self.calls += 1

    class Thread:
        ident = 7

        def __init__(self):
            self.join_calls = 0

        def join(self, timeout):
            self.join_calls += 1
            if self.join_calls == 1:
                raise RuntimeError("uncertain")

        def is_alive(self):
            return False

    client = Client()
    thread = Thread()
    account._client = client
    account._poll_thread = thread
    with pytest.raises(TelegramAccountStopError):
        account.stop()
    assert account._poll_thread is thread
    assert account._client is client
    with pytest.raises(RuntimeError, match="lifecycle already retained"):
        account.start()
    account.stop()
    assert account._poll_thread is None
    assert account._client is None
    assert client.calls == 2


def test_real_account_start_failure_retains_assigned_unstarted_then_stop_discards_it(
    tmp_path,
    monkeypatch,
):
    account = TelegramAccount("main", "test-token", None, state_dir=tmp_path)

    class Client:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class UnstartedThread:
        ident = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self) -> None:
            raise RuntimeError("synthetic thread start failure")

    client = Client()
    monkeypatch.setattr(account, "_ensure_client", lambda: setattr(account, "_client", client))
    monkeypatch.setattr(account, "_request", lambda method, **_kwargs: {"username": "LocalBot"})
    monkeypatch.setattr(account, "_save_state", lambda: None)
    monkeypatch.setattr(account, "_register_commands", lambda: None)
    monkeypatch.setattr(account_module.threading, "Thread", UnstartedThread)

    with pytest.raises(RuntimeError, match="thread start failure"):
        account.start()
    retained = account._poll_thread
    assert isinstance(retained, UnstartedThread)
    with pytest.raises(RuntimeError, match="lifecycle already retained"):
        account.start()

    account.stop()
    assert account._poll_thread is None
    assert account._client is None
    assert client.closed is True


def test_real_account_discards_assigned_unstarted_thread_and_independently_closes(tmp_path):
    account = TelegramAccount("main", "test-token", None, state_dir=tmp_path)
    account._poll_thread = threading.Thread(target=lambda: None)

    class Client:
        def close(self):
            raise RuntimeError("close failed")

    client = Client()
    account._client = client
    with pytest.raises(TelegramAccountStopError):
        account.stop()
    assert account._poll_thread is None
    assert account._client is client


def test_real_manager_workers_retain_uncertainty_reject_overlap_and_converge(
    tmp_path, monkeypatch
):
    manager, _service = _manager(tmp_path, router=None)
    typing_events: list[str] = []
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_all",
        lambda: typing_events.append("typing.stop_all"),
    )

    class Thread:
        ident = 9

        def __init__(self, *, raises=False, live=False):
            self.raises = raises
            self.live = live

        def join(self, timeout):
            if self.raises:
                self.raises = False
                raise RuntimeError("join")

        def is_alive(self):
            return self.live

    tail = Thread(raises=True)
    programmable = Thread(live=True)
    pending_unstarted = threading.Thread(target=lambda: None)
    manager._task_card_tail_thread = tail
    manager._programmable_task_card_thread = programmable
    manager._task_card_pending_edit_thread = pending_unstarted

    with pytest.raises(TelegramManagerStopError) as raised:
        manager.stop()
    assert manager._task_card_tail_thread is tail
    assert manager._programmable_task_card_thread is programmable
    assert manager._task_card_pending_edit_thread is None
    assert [name for name, _ in raised.value.failures] == [
        "programmable_task_card_poller",
        "task_card_tail",
    ]
    with pytest.raises(RuntimeError, match="already retained"):
        manager._start_task_card_tail()

    programmable.live = False
    manager.stop()
    assert manager._task_card_tail_thread is None
    assert manager._programmable_task_card_thread is None
    assert manager._task_card_pending_edit_thread is None
    assert typing_events == ["typing.stop_all", "typing.stop_all"]


def test_manager_stop_attempts_every_component_and_aggregates_multiple_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FailingService(_Service):
        def stop(self) -> None:
            events.append("service.stop")
            raise RuntimeError("service uncertain")

    class FailingRouter(_Router):
        def stop(self) -> None:
            events.append("router.stop")
            raise RuntimeError("router uncertain")

    manager = TelegramManager(
        FailingService(events),
        working_dir=tmp_path,
        notification_store=FakeNotificationStore(),
        on_inbound=lambda _event: None,
        agent_switching_router=FailingRouter(events=events),
    )

    def fail(name: str):
        def _stop() -> None:
            events.append(name)
            raise RuntimeError(name)
        return _stop

    monkeypatch.setattr(manager, "_stop_programmable_task_card_poller", fail("programmable.stop"))
    monkeypatch.setattr(manager, "_stop_task_card_tail", fail("tail.stop"))
    monkeypatch.setattr(manager, "_stop_pending_task_card_edit_worker", fail("pending.stop"))
    monkeypatch.setattr(manager_module._typing_manager, "stop_all", fail("typing.stop_all"))

    with pytest.raises(TelegramManagerStopError) as raised:
        manager.stop()

    assert events == [
        "programmable.stop",
        "tail.stop",
        "pending.stop",
        "router.stop",
        "service.stop",
        "typing.stop_all",
    ]
    assert [name for name, _exc in raised.value.failures] == [
        "programmable_task_card_poller",
        "task_card_tail",
        "pending_task_card_edit_worker",
        "agent_switching_router",
        "telegram_service",
        "typing_manager",
    ]

    clean_events: list[str] = []
    monkeypatch.setattr(
        manager,
        "_stop_programmable_task_card_poller",
        lambda: clean_events.append("programmable.stop"),
    )
    monkeypatch.setattr(manager, "_stop_task_card_tail", lambda: clean_events.append("tail.stop"))
    monkeypatch.setattr(
        manager,
        "_stop_pending_task_card_edit_worker",
        lambda: clean_events.append("pending.stop"),
    )
    monkeypatch.setattr(
        manager._agent_switching_router,
        "stop",
        lambda: clean_events.append("router.stop"),
    )
    monkeypatch.setattr(manager._service, "stop", lambda: clean_events.append("service.stop"))
    monkeypatch.setattr(
        manager_module._typing_manager,
        "stop_all",
        lambda: clean_events.append("typing.stop_all"),
    )

    manager.stop()
    assert clean_events == [
        "programmable.stop",
        "tail.stop",
        "pending.stop",
        "router.stop",
        "service.stop",
        "typing.stop_all",
    ]


def _write_real_discovery_files(owner: Path, target: Path, *, case: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    marker: Any = {
        "marker": "channel_reply/v1",
        "version": 1,
        "submit": "target-local-filesystem-capsule",
    }
    if case == "missing-manifest-marker":
        marker = None
    elif case == "wrong-manifest-marker":
        marker = {"marker": "wrong/v1", "version": 1, "submit": "wrong"}
    manifest = {
        "agent_name": "alpha",
        "address": "alpha",
        "agent_id": "agent-a",
        "route_capabilities": {} if marker is None else {"channel_reply": marker},
    }
    manifest_path = target / ".agent.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger = owner / "delegates" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    edge = {"event": "avatar", "boot_status": "ok", "name": "alpha", "working_dir": "alpha"}
    if case == "unsafe-ledger":
        edge["working_dir"] = "../alpha"
    lines = [json.dumps(edge)]
    if case == "ambiguous-ledger":
        lines.append(json.dumps(edge))
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "case",
    [
        "missing-manifest-marker",
        "wrong-manifest-marker",
        "malformed-manifest",
        "unsafe-ledger",
        "ambiguous-ledger",
        "ledger-read-error",
        "manifest-read-error",
        "invalid-capsule-shape",
    ],
)
def test_real_manager_ingress_invalid_discovery_and_capsule_is_local_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    owner = tmp_path / "network" / "owner"
    target = tmp_path / "network" / "alpha"
    owner.mkdir(parents=True)
    _write_real_discovery_files(owner, target, case=case)
    manifest = target / ".agent.json"
    ledger = owner / "delegates" / "ledger.jsonl"
    if case == "malformed-manifest":
        manifest.write_text("{", encoding="utf-8")
    elif case == "ledger-read-error":
        ledger.unlink()
        ledger.mkdir()
    elif case == "manifest-read-error":
        manifest.unlink()
        manifest.mkdir()

    service = _Service()
    router = TelegramAgentSwitchingRouter(
        owner_workdir=owner,
        service=service,
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )
    if case == "invalid-capsule-shape":
        # Discovery itself is real and eligible; capsule construction fails on an
        # actual unsafe existing root shape after route reservation, which must be
        # terminalized without target publication or provider wake.
        monkeypatch.setattr(switching, "observe_alive", lambda *_a, **_k: True)
        reply_root = target / ".channel_reply"
        reply_root.symlink_to(tmp_path / "outside-channel-reply")
    else:
        monkeypatch.setattr(switching, "observe_alive", lambda *_a, **_k: True)

    inbound: list[dict[str, Any]] = []
    manager = TelegramManager(
        service,
        working_dir=owner,
        notification_store=FakeNotificationStore(),
        on_inbound=inbound.append,
        agent_switching_router=router,
    )
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_a, **_k: pytest.fail("real invalid state reached admin preview"),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_task_card_resident",
        lambda *_a, **_k: pytest.fail("real invalid state reached admin Task Card"),
    )

    manager.on_incoming("main", _update("@alpha private words"))

    assert inbound == []
    assert len(_raw_records(owner)) == 1
    expected = (
        "[admin] Agent routing is temporarily unavailable."
        if case == "invalid-capsule-shape"
        else "[admin] That Agent is not currently eligible."
    )
    assert service.default_account.sent == [
        (123, expected, {"reply_markup": None, "reply_to_message_id": 55})
    ]
    assert not (owner / ".mcp_inbox").exists()
    assert not any((target / ".mcp_inbox").glob("**/*.json"))
    assert not any((target / "events").glob("*.json"))
    grants = list((router._reply_root / "grants").glob("*.json"))
    events = list((router._reply_root / "route_events").glob("*.json"))
    decisions = list((router._reply_root / "route_decisions").glob("*.json"))
    if case == "invalid-capsule-shape":
        # Reserve-then-revoke may leave owner-local proof-digest bookkeeping, but
        # no raw proof or active authority. The permanent decision is nonauthorizing.
        assert len(grants) == 1
        grant = json.loads(grants[0].read_text(encoding="utf-8"))
        assert grant["revoked"] is True
        assert "proof" not in grant
        assert len(events) == 1
        event = json.loads(events[0].read_text(encoding="utf-8"))
        assert event["decision"] != "active"
        assert event["proof"] == ""
        assert len(decisions) == 1
        decision = json.loads(decisions[0].read_text(encoding="utf-8"))
        assert decision["decision"] != "active"
    else:
        assert grants == []
        assert events == []
        assert decisions == []



def _edited_update(
    text: str,
    *,
    update_id: int = 45,
    message_id: int = 55,
) -> dict[str, Any]:
    message = dict(_update(text)["message"])
    message["message_id"] = message_id
    message["edit_date"] = 1781600100
    return {"update_id": update_id, "edited_message": message}


def _real_edit_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_names: tuple[str, ...] = ("alpha",),
):
    owner = tmp_path / "network" / "owner"
    owner.mkdir(parents=True)
    service = _Service()
    targets: dict[str, EligibleTarget] = {}
    for index, name in enumerate(target_names):
        target_dir = tmp_path / "network" / name
        target_dir.mkdir()
        targets[name] = EligibleTarget(
            name=name,
            agent_id=f"agent-{index}",
            workdir=target_dir,
            manifest_digest=("a" if index == 0 else "c") * 64,
            ledger_chain_digest=("b" if index == 0 else "d") * 64,
        )
    router = TelegramAgentSwitchingRouter(
        owner_workdir=owner,
        service=service,
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )
    monkeypatch.setattr(
        router,
        "_resolve_name",
        lambda name: (targets.get(name), False),
    )
    monkeypatch.setattr(router, "_register_adapter", lambda _target: SimpleNamespace())
    inbound: list[dict[str, Any]] = []
    manager = TelegramManager(
        service,
        working_dir=owner,
        notification_store=FakeNotificationStore(),
        on_inbound=inbound.append,
        agent_switching_router=router,
    )
    return owner, service, targets, router, manager, inbound


def _forbid_owner_projection(manager: TelegramManager, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_args, **_kwargs: pytest.fail("switching-owned edit reached owner preview"),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_task_card_resident",
        lambda *_args, **_kwargs: pytest.fail("switching-owned edit reached owner Task Card"),
    )


def test_real_manager_selected_edit_is_raw_local_only_and_retains_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, service, targets, router, manager, inbound = _real_edit_stack(
        tmp_path, monkeypatch
    )
    router._state.save_selection("main", 123, 7, targets["alpha"])
    _forbid_owner_projection(manager, monkeypatch)
    monkeypatch.setattr(
        switching,
        "push_inbox_event",
        lambda **_kwargs: pytest.fail("edited message reached target publication"),
    )

    update = _edited_update("ordinary edited words")
    manager.on_incoming("main", update)

    records = _raw_records(owner)
    assert len(records) == 1
    assert records[0]["text"] == "ordinary edited words"
    assert records[0]["telegram"]["branch"] == "edited_message"
    assert records[0]["telegram"]["update"] == update
    assert service.default_account.sent == [
        (
            123,
            f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}",
            {"reply_markup": None, "reply_to_message_id": 55},
        )
    ]
    assert inbound == []
    assert router._state.load_selection("main", 123, 7)["target_name"] == "alpha"
    assert not (targets["alpha"].workdir / ".mcp_inbox").exists()
    assert not (owner / ".mcp_inbox").exists()


@pytest.mark.parametrize("text", ["@alpha changed", "/agent status"])
def test_real_manager_unselected_selector_like_edits_are_raw_local_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> None:
    owner, service, targets, _router, manager, inbound = _real_edit_stack(
        tmp_path, monkeypatch
    )
    _forbid_owner_projection(manager, monkeypatch)
    manager.on_incoming("main", _edited_update(text))

    assert len(_raw_records(owner)) == 1
    assert service.default_account.sent == [
        (
            123,
            f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}",
            {"reply_markup": None, "reply_to_message_id": 55},
        )
    ]
    assert inbound == []
    assert not (targets["alpha"].workdir / ".mcp_inbox").exists()


def test_real_manager_unselected_unmarked_edit_preserves_v103_admin_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, service, targets, _router, manager, inbound = _real_edit_stack(
        tmp_path, monkeypatch
    )
    manager.on_incoming("main", _edited_update("ordinary unmatched edit"))

    assert len(_raw_records(owner)) == 1
    assert service.default_account.sent == []
    assert len(inbound) == 1
    assert inbound[0]["metadata"]["type"] == "edited_message"
    assert inbound[0]["wake"] is False
    assert not (targets["alpha"].workdir / ".mcp_inbox").exists()


def test_real_manager_corrupt_selection_edit_fails_closed_with_generic_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, service, targets, router, manager, inbound = _real_edit_stack(
        tmp_path, monkeypatch
    )
    router._state._selection_path("main", 123, 7).write_text(
        "{corrupt", encoding="utf-8"
    )
    _forbid_owner_projection(manager, monkeypatch)
    manager.on_incoming("main", _edited_update("ordinary edit"))

    assert len(_raw_records(owner)) == 1
    assert service.default_account.sent[-1][1] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"
    assert inbound == []
    assert router._state.read_selection("main", 123, 7).status == "unavailable"
    assert not (targets["alpha"].workdir / ".mcp_inbox").exists()


def test_real_manager_prior_route_edit_stays_local_after_reset_and_reselection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, service, targets, router, manager, inbound = _real_edit_stack(
        tmp_path, monkeypatch, target_names=("alpha", "beta")
    )
    _forbid_owner_projection(manager, monkeypatch)

    original = _update("@alpha private original")
    manager.on_incoming("main", original)
    assert router._state.read_original_ownership(
        "main",
        123,
        7,
        55,
        now=switching._utc_now(),
        retention_seconds=router._retention_seconds,
    ).status == "owned"
    alpha_events = list((targets["alpha"].workdir / ".mcp_inbox").glob("**/*.json"))
    assert len(alpha_events) == 1

    reset = _update("/agent reset")
    reset["update_id"] = 46
    reset["message"]["message_id"] = 56
    manager.on_incoming("main", reset)
    assert router._state.read_selection("main", 123, 7).status == "absent"

    manager.on_incoming(
        "main",
        _edited_update("changed after reset", update_id=47, message_id=55),
    )

    select_beta = _update("@beta")
    select_beta["update_id"] = 48
    select_beta["message"]["message_id"] = 57
    manager.on_incoming("main", select_beta)
    assert router._state.load_selection("main", 123, 7)["target_name"] == "beta"

    second_edit = _edited_update(
        "changed after reselection", update_id=49, message_id=55
    )
    manager.on_incoming("main", second_edit)
    manager.on_incoming("main", second_edit)  # transport replay must never republish

    assert inbound == []
    assert len(list((targets["alpha"].workdir / ".mcp_inbox").glob("**/*.json"))) == 1
    assert not (targets["beta"].workdir / ".mcp_inbox").exists()
    assert router._state.load_selection("main", 123, 7)["target_name"] == "beta"
    generic = [item for item in service.default_account.sent if item[1] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"]
    assert len(generic) == 2
    assert all(item[2]["reply_to_message_id"] == 55 for item in generic)
    ownership_files = list(router._state._original_ownership.glob("*.json"))
    assert len(ownership_files) == 1
    owner_state = "\n".join(path.read_text(encoding="utf-8") for path in ownership_files)
    assert "private original" not in owner_state
    assert "changed after" not in owner_state


def test_real_manager_original_ownership_conflict_blocks_first_target_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, service, targets, router, manager, inbound = _real_edit_stack(
        tmp_path, monkeypatch
    )
    marker = router._state._original_ownership_path("main", 123, 7, 55)
    marker.write_text("{}", encoding="utf-8")
    _forbid_owner_projection(manager, monkeypatch)
    monkeypatch.setattr(
        switching,
        "push_inbox_event",
        lambda **_kwargs: pytest.fail("marker conflict still published target task"),
    )

    manager.on_incoming("main", _update("@alpha private words"))

    assert len(_raw_records(owner)) == 1
    assert inbound == []
    assert service.default_account.sent[-1][1] == (
        "[admin] Agent routing is temporarily unavailable."
    )
    assert not (targets["alpha"].workdir / ".mcp_inbox").exists()
    assert not (targets["alpha"].workdir / ".telegram-agent-switching").exists()
    assert not (targets["alpha"].workdir / ".channel_reply").exists()
    assert router._state.read_original_ownership(
        "main",
        123,
        7,
        55,
        now=switching._utc_now(),
        retention_seconds=router._retention_seconds,
    ).status == "unavailable"
