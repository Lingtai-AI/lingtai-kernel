"""Focused Telegram family and intrinsic Task Card LTP-v2 invariants."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram._family import (
    TELEGRAM_ACTIONS,
    TELEGRAM_SCHEMA,
    _basic_validate,
    handle_telegram,
)
from lingtai.mcp_servers.telegram.service import TelegramService
from lingtai.tools.task_card import TaskCardManager, get_schema as task_card_schema

from .test_task_card_controller import _FakeAgent, _OK_BODY, _write_renderer


class _CountingManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


def _branches(schema: dict) -> dict[str, dict]:
    inputs = schema["properties"]["input"]
    branches = inputs.get("oneOf") or inputs.get("anyOf")
    return {branch["title"].removesuffix(" input"): branch for branch in branches}


def test_family_dispatch_rejects_root_and_cross_branch_before_manager_io():
    manager = _CountingManager()
    valid = {
        "action": "accounts",
        "input": {},
        "reasoning": "schema probe",
    }
    invalid = [
        {"action": "accounts", "input": {}, "reasoning": "x", "_reasoning": "legacy"},
        {"action": "accounts", "input": {}, "reasoning": "x", "unknown": 1},
        {"action": "accounts", "input": {}, "reasoning": 7},
        {"action": "accounts", "input": {"chat_id": 2}, "reasoning": "x"},
        {"action": "send", "input": {"message_id": "acct:1:2", "text": "x"}, "reasoning": "x"},
        {"action": "send", "input": {"text": "missing chat"}, "reasoning": "x"},
        {"action": "send", "input": {"chat_id": 2, "text": 17}, "reasoning": "x"},
    ]
    for args in invalid:
        result = handle_telegram(manager, args)
        assert result["status"] == "failed", args
        assert manager.calls == []
    assert handle_telegram(manager, valid)["status"] == "ok"
    assert len(manager.calls) == 1


def test_telegram_send_requires_rendering_mode_before_manager_io():
    manager = _CountingManager()
    for input_ in (
        {"chat_id": 2, "text": "x"},
        {"chat_id": 2, "text": "x", "parse_mode": "HTML"},
        {"chat_id": 2, "chat_action": "typing"},
    ):
        result = handle_telegram(manager, {"action": "send", "input": input_, "reasoning": "schema probe"})
        assert result["status"] == "failed", input_
        assert result["error_code"] == "INVALID_ARGUMENT"
        assert manager.calls == []

def test_telegram_send_schema_requires_rendering_mode_and_preserves_content_alternatives():
    send = _branches(TELEGRAM_SCHEMA)["send"]
    assert "rendering_mode" in send["required"]
    assert send["properties"]["rendering_mode"]["enum"] == ["plain_text", "HTML", "MarkdownV2", "Markdown", "entities", "rich"]
    assert send["anyOf"] == [{"required": ["text"]}, {"required": ["media"]}, {"required": ["chat_action"]}, {"required": ["structured_message"]}]
    assert _basic_validate({"chat_id": 3, "rendering_mode": "plain_text", "text": "hello"}, send)
    assert _basic_validate({"chat_id": 3, "rendering_mode": "plain_text", "media": {"type": "photo", "path": "x"}}, send)
    assert _basic_validate({"chat_id": 3, "rendering_mode": "plain_text", "chat_action": "typing"}, send)
    assert _basic_validate({"chat_id": 3, "rendering_mode": "rich", "structured_message": {"title": "✅ Done"}}, send)
    assert not _basic_validate({"chat_id": 3, "text": "hello"}, send)
    assert not _basic_validate({"chat_id": 3, "rendering_mode": "plain_text"}, send)
    assert not _basic_validate({"text": "missing chat", "rendering_mode": "plain_text"}, send)
    assert not _basic_validate({"chat_id": 3, "rendering_mode": "plain_text", "text": 17}, send)

def test_taskcard_refresh_setting_defaults_invalid_values_preserves_valid_siblings(
    tmp_path, caplog
):
    path = tmp_path / "telegram" / "taskcard.json"
    path.parent.mkdir()
    cases = [
        {},
        {"taskcard": False, "normal_rows": 4, "max_refreshes": True},
        {"taskcard": False, "normal_rows": 4, "max_refreshes": 0},
        {"taskcard": False, "normal_rows": 4, "max_refreshes": "10"},
        {"taskcard": False, "normal_rows": 4, "max_refreshes": 1001},
    ]
    for payload in cases:
        caplog.clear()
        path.write_text(json.dumps(payload))
        service = TelegramService(tmp_path, [{"alias": "acct", "bot_token": "x"}], lambda *_: None)
        assert any("max_refreshes" in record.message for record in caplog.records)
        assert service.taskcard_enabled() is payload.get("taskcard", True)
        assert service.taskcard_normal_rows() == payload.get("normal_rows", 1)
        assert service.taskcard_max_refreshes() == 1000
        service.set_taskcard_max_refreshes(7)
        persisted = json.loads(path.read_text())
        assert persisted["max_refreshes"] == 7
        assert persisted["taskcard"] == service.taskcard_enabled()
        assert persisted["normal_rows"] == service.taskcard_normal_rows()


def test_taskcard_refresh_setting_positive_value_round_trips(tmp_path):
    path = tmp_path / "telegram" / "taskcard.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"taskcard": False, "normal_rows": 3, "max_refreshes": 9}))
    service = TelegramService(tmp_path, [{"alias": "acct", "bot_token": "x"}], lambda *_: None)
    assert service.taskcard_max_refreshes() == 9
    service.set_taskcard_max_refreshes(4)
    assert json.loads(path.read_text())["max_refreshes"] == 4
    with pytest.raises(ValueError, match="1 through 1000"):
        service.set_taskcard_max_refreshes(1001)
    assert json.loads(path.read_text())["max_refreshes"] == 4


def test_intrinsic_task_card_schema_is_a_strict_ltp_v2_family():
    schema = task_card_schema()
    names = schema["properties"]["action"]["enum"]
    branches = schema["properties"]["input"]["oneOf"]

    assert schema["required"] == ["action", "input", "reasoning"]
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["additionalProperties"] is False
    assert names == ["start", "inspect", "retry", "stop", "remove", "manual"]
    assert len(schema["allOf"]) == len(names) == len(branches)
    for action, branch, condition in zip(names, branches, schema["allOf"]):
        assert branch["title"] == f"{action} input"
        assert condition["if"]["properties"]["action"]["const"] == action


def _start_with_ceiling(agent: _FakeAgent, ceiling: int, *, requested=None):
    manager = TaskCardManager(agent)
    args = {
        "action": "start",
        "input": {
            "renderer_path": _write_renderer(agent._working_dir, _OK_BODY),
            "interval_s": 3600,
            "max_refreshes": ceiling if requested is None else requested,
        },
        "reasoning": "start a task card",
    }
    return manager, manager.handle(args)


def test_intrinsic_start_caps_requested_refresh_limit_at_configured_default_2000(tmp_path):
    agent = _FakeAgent(tmp_path)
    manager = TaskCardManager(agent)
    result = manager.handle(
        {
            "action": "start",
            "input": {
                "renderer_path": _write_renderer(tmp_path, _OK_BODY),
                "interval_s": 3600,
                "max_refreshes": 5000,
            },
            "reasoning": "cap the requested ceiling",
        }
    )
    assert result["max_refreshes"] == 2000
    manager.handle({"action": "stop", "input": {"watch_id": result["watch_id"]}, "reasoning": "cleanup"})


@pytest.mark.parametrize("requested", [0, -1, True, "2"])
def test_intrinsic_start_rejects_invalid_refresh_limits(requested, tmp_path):
    agent = _FakeAgent(tmp_path)
    manager = TaskCardManager(agent)
    result = manager.handle(
        {
            "action": "start",
            "input": {
                "renderer_path": _write_renderer(tmp_path, _OK_BODY),
                "max_refreshes": requested,
            },
            "reasoning": "reject malformed max_refreshes",
        }
    )
    assert result["status"] == "failed"
    assert manager._watch is None


@pytest.mark.parametrize("body", [_OK_BODY, "import sys; sys.exit(1)"])
def test_intrinsic_refresh_limit_counts_later_attempts_and_notifies_once(body, tmp_path):
    agent = _FakeAgent(tmp_path)
    manager, start = _start_with_ceiling(agent, 1)
    watch = manager._watch
    assert watch is not None
    Path(watch.renderer_path).write_text(body, encoding="utf-8")

    manager._tick(watch)

    assert watch.refreshes_used == 1
    assert watch.stop_reason == "max_refreshes"
    assert (tmp_path / "taskcard" / "status").read_text(encoding="utf-8") == "inactive"
    limit_wakes = [wake for wake in agent.wakes if wake["source"] == "task_card.limit"]
    assert len(limit_wakes) == 1
    assert limit_wakes[0]["idempotency_key"] == "task_card.limit:tc_1:1"
    manager._tick(watch)
    assert len([wake for wake in agent.wakes if wake["source"] == "task_card.limit"]) == 1


def test_openai_responses_scrub_preserves_telegram_family_root_and_action_branches():
    from lingtai.llm.openai.adapter import _scrub_responses_schema

    wire = _scrub_responses_schema(copy.deepcopy(TELEGRAM_SCHEMA), is_root=True)
    assert wire["required"] == TELEGRAM_SCHEMA["required"]
    assert wire["properties"]["action"]["enum"] == list(TELEGRAM_ACTIONS)
    assert wire["properties"]["input"]["anyOf"]
    assert len(wire["allOf"]) == len(TELEGRAM_ACTIONS)
    assert wire["additionalProperties"] is False
