"""Focused contract tests for the agent-wide Telegram Task Card toggle."""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lingtai.kernel.base_agent import _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.account import DEFAULT_COMMANDS
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.service import TelegramService
from tests._notification_store_helpers import notification_store_for


def _configs(*aliases: str, allowed_users: list[int] | None = None) -> list[dict]:
    return [
        {
            "alias": alias,
            "bot_token": f"token-{alias}",
            "allowed_users": allowed_users,
        }
        for alias in aliases
    ]


def _service(
    workdir: Path,
    *aliases: str,
    allowed_users: list[int] | None = None,
    on_message=lambda _alias, _update: None,
) -> TelegramService:
    return TelegramService(
        workdir,
        _configs(*(aliases or ("main",)), allowed_users=allowed_users),
        on_message,
    )


def _manager(workdir: Path, service: TelegramService, inbound=None) -> TelegramManager:
    return TelegramManager(
        service,
        working_dir=workdir,
        on_inbound=inbound or (lambda _event: None),
        notification_store=notification_store_for(workdir),
    )


def _incoming_message(message_id: int = 53, *, text: str = "hello") -> dict[str, Any]:
    return {
        "id": f"main:123:{message_id}",
        "from": {"username": "alice"},
        "chat": {"id": 123, "type": "private"},
        "date": "2026-07-12T18:00:00Z",
        "text": text,
        "media": None,
        "callback_query": None,
        "reply_to_message_id": None,
        "_folder": "inbox",
    }


def _write_message(workdir: Path, message: dict[str, Any], folder: str = "inbox") -> Path:
    msg_dir = workdir / "telegram" / "main" / folder / message["id"].replace(":", "-")
    msg_dir.mkdir(parents=True, exist_ok=True)
    stored = {key: value for key, value in message.items() if not key.startswith("_")}
    path = msg_dir / "message.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    return path


# Durable one-source-of-truth state -------------------------------------------------


def test_taskcard_state_defaults_true_and_is_shared_across_accounts(tmp_path: Path) -> None:
    service = _service(tmp_path, "one", "two")

    assert service.taskcard_enabled() is True
    assert service.get_account("one")._taskcard_enabled() is True
    assert service.get_account("two")._taskcard_enabled() is True
    assert not (tmp_path / "telegram" / "taskcard.json").exists()


def test_taskcard_state_is_independent_between_agent_workdirs(tmp_path: Path) -> None:
    first = _service(tmp_path / "agent-one", "main")
    second = _service(tmp_path / "agent-two", "main")
    first.set_taskcard_enabled(False)

    assert first.taskcard_enabled() is False
    assert second.taskcard_enabled() is True


def test_taskcard_state_persists_false_and_true_across_service_instances(tmp_path: Path) -> None:
    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(False)
    state_path = tmp_path / "telegram" / "taskcard.json"
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"taskcard": False, "normal_rows": 1}
    assert _service(tmp_path, "main").taskcard_enabled() is False

    service.set_taskcard_enabled(True)
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"taskcard": True, "normal_rows": 1}
    assert _service(tmp_path, "main").taskcard_enabled() is True


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", "{}", '{"taskcard": "false"}', '{"taskcard": 0}'],
)
def test_invalid_taskcard_state_defaults_true_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    state_path = tmp_path / "telegram" / "taskcard.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(raw, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        service = _service(tmp_path, "main")

    assert service.taskcard_enabled() is True
    assert "taskcard state" in caplog.text.lower()
    assert raw not in caplog.text


def test_taskcard_write_failure_preserves_effective_and_durable_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lingtai.mcp_servers.telegram.service as service_module

    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(False)
    state_path = tmp_path / "telegram" / "taskcard.json"
    before = state_path.read_bytes()

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated durable write failure")

    monkeypatch.setattr(service_module, "atomic_write_json", fail_write)
    with pytest.raises(OSError):
        service.set_taskcard_enabled(True)

    assert service.taskcard_enabled() is False
    assert state_path.read_bytes() == before
    assert not list(state_path.parent.glob("*.tmp"))


# Local slash command ---------------------------------------------------------------


def test_default_menu_contains_taskcard_once_and_custom_menu_stays_replacement_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert [item["command"] for item in DEFAULT_COMMANDS].count("taskcard") == 1
    service = TelegramService(
        tmp_path,
        [
            {"alias": "default", "bot_token": "x"},
            {
                "alias": "custom",
                "bot_token": "y",
                "commands": [{"command": "onlymine", "description": "Mine"}],
            },
            {"alias": "empty", "bot_token": "z", "commands": []},
        ],
        lambda _alias, _update: None,
    )
    registered: dict[str, list[dict[str, str]]] = {}
    for alias in service.list_accounts():
        account = service.get_account(alias)
        monkeypatch.setattr(
            account,
            "_request",
            lambda _method, *, json, alias=alias: registered.setdefault(alias, json["commands"]),
        )
        account._register_commands()

    assert registered["default"] == DEFAULT_COMMANDS
    assert registered["custom"] == [{"command": "onlymine", "description": "Mine"}]
    assert registered["empty"] == []


def test_taskcard_commands_are_local_agent_wide_and_mentions_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forwarded: list[tuple[str, dict]] = []
    service = _service(
        tmp_path,
        "one",
        "two",
        allowed_users=[7],
        on_message=lambda alias, update: forwarded.append((alias, update)),
    )
    replies: list[str] = []
    for alias in service.list_accounts():
        monkeypatch.setattr(
            service.get_account(alias),
            "send_message",
            lambda _chat_id, text, **_kwargs: replies.append(text) or {"message_id": 1},
        )

    one = service.get_account("one")
    two = service.get_account("two")
    one._process_update({
        "update_id": 1,
        "message": {"from": {"id": 7}, "chat": {"id": 10}, "text": "/taskcard"},
    })
    assert "taskcard: True" in replies[-1]
    assert "Usage: /taskcard on | /taskcard off" in replies[-1]

    one._process_update({
        "update_id": 2,
        "message": {"from": {"id": 7}, "chat": {"id": 10}, "text": "/taskcard@SomeBot off"},
    })
    assert service.taskcard_enabled() is False
    assert "taskcard: False" in replies[-1]
    assert "internal mechanics still run" in replies[-1]

    two._process_update({
        "update_id": 3,
        "message": {"from": {"id": 7}, "chat": {"id": 99}, "text": "/taskcard on"},
    })
    assert service.taskcard_enabled() is True
    assert "taskcard: True" in replies[-1]
    assert not forwarded


def test_taskcard_invalid_help_and_unauthorized_forms_do_not_mutate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, "main", allowed_users=[7])
    account = service.get_account("main")
    replies: list[str] = []
    monkeypatch.setattr(
        account,
        "send_message",
        lambda _chat_id, text, **_kwargs: replies.append(text) or {"message_id": 1},
    )

    for update_id, text in enumerate(("/taskcard help", "/taskcard off extra"), 1):
        account._process_update({
            "update_id": update_id,
            "message": {"from": {"id": 7}, "chat": {"id": 10}, "text": text},
        })
        assert replies[-1] == "❌ Usage: /taskcard on | /taskcard off | /taskcard N (1-10)"
        assert service.taskcard_enabled() is True

    account._process_update({
        "update_id": 3,
        "message": {"from": {"id": 99}, "chat": {"id": 10}, "text": "/taskcard off"},
    })
    assert service.taskcard_enabled() is True
    assert len(replies) == 2


def test_taskcard_command_write_failure_warns_without_false_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lingtai.mcp_servers.telegram.service as service_module

    service = _service(tmp_path, "main")
    account = service.get_account("main")
    replies: list[str] = []
    monkeypatch.setattr(
        account,
        "send_message",
        lambda _chat_id, text, **_kwargs: replies.append(text) or {"message_id": 1},
    )
    monkeypatch.setattr(
        service_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fail")),
    )

    account._process_update({
        "update_id": 1,
        "message": {"from": {"id": 1}, "chat": {"id": 10}, "text": "/taskcard off"},
    })
    assert service.taskcard_enabled() is True
    assert replies == ["⚠️ Could not update taskcard; the previous setting is unchanged."]


# Presentation-boundary suppression -------------------------------------------------


@pytest.mark.parametrize("channel", ["automatic", "programmable"])
@pytest.mark.parametrize("sub_action", ["create", "update", "finalize"])
def test_disabled_manager_suppresses_transport_and_state_except_hidden_programmable_finalize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    sub_action: str,
) -> None:
    """While hidden, NO Telegram transport ever runs. State is also untouched —
    with exactly one targeted exception (requirement #7): a hidden programmable
    FINALIZE clears its committed slot internally (still no transport) so a
    stopped hidden watch cannot resurface after /taskcard on; the automatic slot
    and the resident id are preserved."""
    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(False)
    manager = _manager(tmp_path, service)
    manager._task_card_channels = {
        "main:123": {"automatic": "old auto", "programmable": "old watch"}
    }
    account = service.get_account("main")
    account.set_task_card(123, "main:123:77")
    before_channels = deepcopy(manager._task_card_channels)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Telegram transport must not run while hidden")

    # Transport is forbidden in every hidden case.
    monkeypatch.setattr(manager, "send_progress_message", unexpected)
    monkeypatch.setattr(manager, "update_progress_message", unexpected)
    monkeypatch.setattr(manager, "_delete_task_card_message", unexpected)

    hidden_finalize_clears = channel == "programmable" and sub_action == "finalize"
    if not hidden_finalize_clears:
        # Every other hidden case mutates no committed slot state at all.
        monkeypatch.setattr(manager, "_set_channel_frame", unexpected)

    args: dict[str, Any] = {
        "sub_action": sub_action,
        "channel": channel,
        "account": "main",
        "chat_id": 123,
        "card_message_id": "main:123:77",
        "rows": [{"tool": "bash", "action": "run", "reasoning": "work"}],
        "card": {"lines": ["watch"]},
    }
    assert manager._handle_task_card_update(args) == {
        "status": "ok",
        "suppressed": True,
        "taskcard": False,
    }
    if hidden_finalize_clears:
        # The programmable slot is cleared internally; the automatic slot and the
        # resident id survive.
        assert "programmable" not in manager._task_card_channels["main:123"]
        assert manager._task_card_channels["main:123"]["automatic"] == "old auto"
    else:
        assert manager._task_card_channels == before_channels
    assert account.get_task_card(123) == "main:123:77"


@pytest.mark.parametrize("args", [
    {"channel": "unknown", "sub_action": "create"},
    {"channel": "automatic", "sub_action": "unknown"},
    {"channel": "programmable", "sub_action": "unknown"},
])
def test_disabled_manager_does_not_mask_invalid_channel_or_sub_action(
    tmp_path: Path, args: dict[str, str]
) -> None:
    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(False)
    result = _manager(tmp_path, service)._handle_task_card_update(args)
    assert result["status"] == "error"


def test_reenable_projects_next_frame_without_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    account = service.get_account("main")
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        account,
        "send_message",
        lambda chat_id, text, **_kwargs: calls.append((chat_id, text)) or {"message_id": 88},
    )

    service.set_taskcard_enabled(False)
    hidden = manager._handle_task_card_update({
        "sub_action": "create", "account": "main", "chat_id": 123,
        "tool": "bash", "tool_action": "run", "reasoning": "hidden frame",
    })
    assert hidden["suppressed"] is True
    assert calls == []

    service.set_taskcard_enabled(True)
    shown = manager._handle_task_card_update({
        "sub_action": "create", "account": "main", "chat_id": 123,
        "tool": "bash", "tool_action": "run", "reasoning": "visible frame",
    })
    assert shown == {"status": "ok", "message_id": "main:123:88"}
    assert len(calls) == 1 and "visible frame" in calls[0][1]


def test_manager_treats_suppression_as_success_without_transport(
    tmp_path: Path,
) -> None:
    """Suppressed automatic broadcast reports success but sends nothing.

    The automatic slot no longer reaches ``TelegramManager`` through a
    BaseAgent-owned reverse call — ``TelegramManager`` itself checks
    ``taskcard_enabled()`` before broadcasting its own event-tail window (see
    ``_broadcast_task_card_event_window``). This exercises the same
    suppression contract at the layer that now owns it: transport calls stay
    zero while suppressed, matching the private-action result shape asserted
    elsewhere in this file.
    """
    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(False)
    manager = _manager(tmp_path, service)

    result = manager._handle_task_card_update({
        "sub_action": "create", "account": "main", "chat_id": 123,
        "tool": "bash", "tool_action": "run", "reasoning": "work",
    })

    assert result == {"status": "ok", "suppressed": True, "taskcard": False}


def test_programmable_watch_keeps_rendering_while_hidden_and_projects_after_reenable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lingtai.mcp_servers.telegram.task_card.controller import (
        TaskCardController,
        _Watch,
    )

    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    account = service.get_account("main")
    sends: list[str] = []
    monkeypatch.setattr(
        account,
        "send_message",
        lambda _chat_id, text, **_kwargs: sends.append(text) or {"message_id": 91},
    )

    class Client:
        def call_tool(self, tool_name, args, timeout=None):
            assert tool_name == _TASK_CARD_TOOL
            return manager.handle({**args, "action": "_task_card_update"})

    events: list[dict[str, Any]] = []
    agent = SimpleNamespace(
        _working_dir=tmp_path,
        _mcp_clients_by_tool={"telegram": Client()},
        _enqueue_system_notification=lambda **event: events.append(event),
    )
    controller = TaskCardController(agent)
    watch = _Watch("tc_1", tmp_path / "renderer.py", 5.0, 1.0, "main", 123)
    frames = iter(({"lines": ["hidden latest"]}, {"lines": ["visible latest"]}))
    monkeypatch.setattr(controller, "_run_renderer", lambda *_args: next(frames))

    service.set_taskcard_enabled(False)
    controller._tick(watch)
    assert watch.last_valid_frame == {"lines": ["hidden latest"]}
    assert watch.error is None and events == [] and sends == []

    service.set_taskcard_enabled(True)
    controller._tick(watch)
    assert watch.last_valid_frame == {"lines": ["visible latest"]}
    assert watch.error is None and events == []
    assert len(sends) == 1 and "visible latest" in sends[0]


def test_stopping_a_hidden_programmable_watch_does_not_resurface_after_reenable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping a HIDDEN programmable watch must clear its committed slot
    internally (no transport), so the stale frame cannot resurface once
    /taskcard is turned back on. Ordinary hidden create/update stay untouched."""
    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    account = service.get_account("main")
    rendered: dict[int, str] = {}

    def _send(_chat_id, text, **_kwargs):
        message_id = 200 + len(rendered)
        rendered[message_id] = text
        return {"message_id": message_id}

    def _edit(_chat_id, message_id, text, **_kwargs):
        rendered[message_id] = text
        return {"ok": True}

    monkeypatch.setattr(account, "send_message", _send)
    monkeypatch.setattr(account, "edit_message", _edit)

    def prog(sub_action, card=None):
        args = {
            "sub_action": sub_action,
            "channel": "programmable",
            "account": "main",
            "chat_id": 123,
        }
        if card is not None:
            args["card"] = card
        return manager._handle_task_card_update(args)

    # A visible programmable resident is created and committed.
    assert prog("create", {"lines": ["live watch"]})["status"] == "ok"
    assert manager._task_card_channels["main:123"].get("programmable")

    # Hide delivery, then STOP (finalize) the hidden watch: no transport occurs,
    # but the committed slot is cleared internally.
    service.set_taskcard_enabled(False)
    sends_before = len(rendered)
    result = prog("finalize")
    assert result.get("suppressed") is True
    assert len(rendered) == sends_before  # no transport while suppressed
    assert "programmable" not in manager._task_card_channels.get("main:123", {})

    # Re-enable and drive an automatic update: the stale watch frame is gone.
    service.set_taskcard_enabled(True)
    manager._handle_task_card_update(
        {
            "sub_action": "update",
            "card_message_id": manager._get_resident_task_card("main", 123),
            "tool": "read",
            "tool_action": "open",
            "reasoning": "after reenable",
        }
    )
    latest = rendered[max(rendered)]
    assert "after reenable" in latest
    assert "live watch" not in latest
    assert "— WATCH —" not in latest


def test_hidden_programmable_create_still_does_not_commit_its_slot(
    tmp_path: Path,
) -> None:
    """The targeted hidden-finalize clear must NOT change ordinary hidden
    create/update: those stay non-committing under suppression."""
    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    service.set_taskcard_enabled(False)
    result = manager._handle_task_card_update(
        {
            "sub_action": "create",
            "channel": "programmable",
            "account": "main",
            "chat_id": 123,
            "card": {"lines": ["hidden"]},
        }
    )
    assert result.get("suppressed") is True
    # No committed slot state was written for a hidden create.
    assert "programmable" not in manager._task_card_channels.get("main:123", {})


# Every agent-visible message representation ---------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
def test_current_taskcard_flag_is_derived_for_preview_structured_and_tool_reads(
    tmp_path: Path, enabled: bool
) -> None:
    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(enabled)
    manager = _manager(tmp_path, service)
    message = _incoming_message()
    stored_path = _write_message(tmp_path, message)
    stored_before = stored_path.read_bytes()

    structured = manager._structured_message(message)
    preview = manager._render_conversation_preview(
        [message], chat_id=123, current_compound_id=message["id"]
    )
    check = manager._check({"account": "main"})
    read = manager._read({"account": "main", "chat_id": 123})
    search = manager._search({"account": "main", "query": "hello"})

    assert structured["taskcard"] is enabled
    message_lines = [line for line in preview.splitlines() if "#main:123:53" in line]
    assert message_lines and all(f"taskcard: {enabled}" in line for line in message_lines)
    for result in (check, read, search):
        assert result["taskcard"] is enabled
        assert result["messages"]
        assert all(item["taskcard"] is enabled for item in result["messages"])
    assert stored_path.read_bytes() == stored_before


def test_reply_target_preview_line_has_current_taskcard_flag(tmp_path: Path) -> None:
    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    original = _incoming_message(52, text="original")
    reply = _incoming_message(53, text="reply")
    reply["reply_to_message_id"] = 52

    preview = manager._render_conversation_preview(
        [original, reply], chat_id=123, current_compound_id=reply["id"]
    )
    rendered_lines = [line for line in preview.splitlines() if "#main:123:" in line]
    assert len(rendered_lines) == 3
    assert all("taskcard: True" in line for line in rendered_lines)


def test_degraded_incoming_preview_includes_current_taskcard_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(False)
    account = service.get_account("main")
    monkeypatch.setattr(account, "send_chat_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(account, "set_message_reaction", lambda *_args, **_kwargs: None)
    inbound: list[dict[str, Any]] = []
    manager = _manager(tmp_path, service, inbound.append)
    monkeypatch.setattr(
        manager,
        "_build_conversation_preview_and_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("degraded")),
    )

    manager.on_incoming(
        "main",
        {
            "message": {
                "message_id": 53,
                "date": 1781600000,
                "from": {"id": 1, "username": "alice"},
                "chat": {"id": 123, "type": "private"},
                "text": "fallback body",
            }
        },
    )
    assert inbound and "taskcard: False" in inbound[0]["body"]


@pytest.mark.parametrize("update_type", ["callback_query", "edited_message"])
def test_callback_and_edited_message_projections_carry_current_taskcard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, update_type: str
) -> None:
    service = _service(tmp_path, "main")
    service.set_taskcard_enabled(False)
    account = service.get_account("main")
    monkeypatch.setattr(account, "send_chat_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(account, "set_message_reaction", lambda *_args, **_kwargs: None)
    inbound: list[dict[str, Any]] = []
    manager = _manager(tmp_path, service, inbound.append)

    if update_type == "callback_query":
        update = {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 1, "username": "alice"},
                "data": "approved",
                "message": {
                    "message_id": 53,
                    "chat": {"id": 123, "type": "private"},
                },
            }
        }
    else:
        _write_message(tmp_path, _incoming_message(text="before edit"))
        update = {
            "edited_message": {
                "message_id": 53,
                "date": 1781600000,
                "from": {"id": 1, "username": "alice"},
                "chat": {"id": 123, "type": "private"},
                "text": "after edit",
            }
        }

    manager.on_incoming("main", update)
    assert inbound
    assert all(
        item["taskcard"] is False
        for item in inbound[-1]["metadata"]["recent_messages"]
    )
    assert "taskcard: False" in inbound[-1]["body"]


def test_old_message_projection_changes_with_current_state_without_record_rewrite(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    path = _write_message(tmp_path, _incoming_message())
    before = path.read_bytes()

    assert manager._read({"account": "main", "chat_id": 123})["messages"][0]["taskcard"] is True
    service.set_taskcard_enabled(False)
    assert manager._read({"account": "main", "chat_id": 123})["messages"][0]["taskcard"] is False
    assert path.read_bytes() == before


# Numeric normal-row preference

def test_taskcard_normal_rows_persist_and_are_shared_across_accounts(tmp_path: Path) -> None:
    service = _service(tmp_path, "one", "two")
    assert service.taskcard_normal_rows() == 1
    service.set_taskcard_normal_rows(7)
    assert service.taskcard_normal_rows() == 7
    assert service.get_account("one")._taskcard_normal_rows() == 7
    assert service.get_account("two")._taskcard_normal_rows() == 7
    assert json.loads((tmp_path / "telegram" / "taskcard.json").read_text()) == {
        "taskcard": True,
        "normal_rows": 7,
    }
    restored = _service(tmp_path, "one")
    assert restored.taskcard_enabled() is True
    assert restored.taskcard_normal_rows() == 7


def test_taskcard_normal_rows_write_failure_preserves_memory_and_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lingtai.mcp_servers.telegram.service as service_module

    service = _service(tmp_path, "main")
    service.set_taskcard_normal_rows(4)
    state_path = tmp_path / "telegram" / "taskcard.json"
    before = state_path.read_bytes()

    monkeypatch.setattr(
        service_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fail")),
    )
    with pytest.raises(OSError):
        service.set_taskcard_normal_rows(8)

    assert service.taskcard_normal_rows() == 4
    assert state_path.read_bytes() == before


def test_taskcard_legacy_boolean_state_defaults_normal_rows_to_one(tmp_path: Path) -> None:
    state_path = tmp_path / "telegram" / "taskcard.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"taskcard": false}', encoding="utf-8")
    service = _service(tmp_path, "main")
    assert service.taskcard_enabled() is False
    assert service.taskcard_normal_rows() == 1


def test_taskcard_numeric_command_is_strict_and_does_not_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, "main", allowed_users=[7])
    account = service.get_account("main")
    replies: list[str] = []
    monkeypatch.setattr(
        account,
        "send_message",
        lambda _chat_id, text, **_kwargs: replies.append(text) or {"message_id": 1},
    )

    account._process_update({
        "update_id": 1,
        "message": {"from": {"id": 7}, "chat": {"id": 10}, "text": "/taskcard 7"},
    })
    assert service.taskcard_enabled() is True
    assert service.taskcard_normal_rows() == 7
    assert "normal rows: 7" in replies[-1]

    usage = "❌ Usage: /taskcard on | /taskcard off | /taskcard N (1-10)"
    for update_id, value in enumerate(
        ("0", "11", "-1", "1.5", "seven", "٧", "7 extra"), 2
    ):
        account._process_update({
            "update_id": update_id,
            "message": {"from": {"id": 7}, "chat": {"id": 10}, "text": f"/taskcard {value}"},
        })
        assert replies[-1] == usage
        assert service.taskcard_normal_rows() == 7
