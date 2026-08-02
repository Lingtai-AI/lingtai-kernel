"""Shared Task Card event projection with Telegram compatibility coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lingtai.mcp_servers.task_card import TaskCardEventProjection
from lingtai.mcp_servers.telegram.manager import TelegramManager


def test_shared_projection_matches_telegram_safe_group_shape() -> None:
    events = [
        {
            "type": "diary",
            "api_call_id": "api-1",
            "text": "public response",
            "visibility": "public",
        },
        {
            "type": "tool_call",
            "api_call_id": "api-1",
            "tool_call_id": "call-1",
            "tool_name": "bash",
            "tool_args": {
                "action": "run",
                "_reasoning": "build safely ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                "command": "PRIVATE_ARGUMENT",
            },
        },
        {"type": "thinking", "api_call_id": "api-1", "text": "HIDDEN"},
    ]
    shared = []
    telegram = []
    for event in events:
        shared_row = TaskCardEventProjection.project_event(event)
        telegram_row = TelegramManager._project_task_card_event(event)
        assert shared_row == telegram_row
        if shared_row is not None:
            shared.append((event, shared_row))
            telegram.append((event, telegram_row))

    shared_groups = TaskCardEventProjection.group_events(shared)
    manager = object.__new__(TelegramManager)
    telegram_groups = manager._group_task_card_events(telegram)

    assert shared_groups == telegram_groups
    assert "PRIVATE_ARGUMENT" not in str(shared_groups)
    assert "HIDDEN" not in str(shared_groups)
    assert "ghp_" not in str(shared_groups)


def test_shared_projection_sanitizes_cross_route_private_locators() -> None:
    private_values = (
        "synthetic-secret-value",
        "https://example.invalid/private?id=1",
        "/Users/synthetic/private/file.txt",
        r"C:\Users\synthetic\private\file.txt",
        "ou_abcdefghijklmnopqrstuvwxyz012345",
    )
    source = (
        f"secret={private_values[0]} url {private_values[1]} "
        f"paths {private_values[2]} {private_values[3]} actor {private_values[4]}"
    )
    diary = TaskCardEventProjection.project_agent_text_event({
        "type": "diary",
        "visibility": "public",
        "text": source,
    })
    tool = TaskCardEventProjection.project_tool_call_row({
        "type": "tool_call",
        "tool_name": "bash",
        "tool_args": {"action": "run", "_reasoning": source},
    })

    assert diary is not None
    assert tool is not None
    for rendered in (diary["text"], tool["reasoning"]):
        assert all(value not in rendered for value in private_values)
        assert "<REDACTED:secret>" in rendered
        assert "<REDACTED:url>" in rendered
        assert "<REDACTED:path>" in rendered
        assert "<REDACTED:provider_id>" in rendered


def test_shared_projection_rejects_private_or_malformed_machine_labels() -> None:
    assert TaskCardEventProjection.project_tool_call_row({
        "type": "tool_call",
        "tool_name": "bad label with spaces",
        "tool_args": {"action": "run", "_reasoning": "safe"},
    }) is None
    assert TaskCardEventProjection.project_tool_call_row({
        "type": "tool_call",
        "tool_name": "x" * 65,
        "tool_args": {"action": "run", "_reasoning": "safe"},
    }) is None

    row = TaskCardEventProjection.project_tool_call_row({
        "type": "tool_call",
        "tool_name": "bash",
        "tool_args": {
            "action": "https://example.invalid/private",
            "_reasoning": "safe",
        },
    })
    assert row is not None
    assert row["tool"] == "bash"
    assert "tool_action" not in row


def test_shared_result_projection_updates_only_matching_safe_rows() -> None:
    groups = [
        {
            "api_call_id": "api-1",
            "events": [
                {
                    "kind": "tool",
                    "tool": "read",
                    "reasoning": "inspect",
                    "_tool_call_id": "call-1",
                    "status": "???",
                },
                {"kind": "text", "text": "public response"},
            ],
        }
    ]

    changed = TaskCardEventProjection.apply_tool_results(
        groups,
        {
            "call-1": {
                "status": "ok",
                "elapsed_ms": 2300,
                "result": "PRIVATE_RESULT",
            },
        },
    )

    assert changed is True
    tool = groups[0]["events"][0]
    assert tool["status"] == "success"
    assert tool["elapsed_s"] == 2.3
    assert "PRIVATE_RESULT" not in str(groups)


def test_shared_render_is_byte_identical_to_telegram_golden_surface() -> None:
    groups = [
        {
            "api_call_id": "api-1",
            "events": [
                {"kind": "text", "text": "public response"},
                {
                    "kind": "tool",
                    "tool": "bash",
                    "tool_action": "run",
                    "reasoning": "build",
                    "status": "???",
                    "started_at": "12:00:00 UTC+00",
                },
            ],
        }
    ]
    now = datetime(2026, 8, 3, 2, 30, tzinfo=timezone(timedelta(hours=8)))
    metadata = {"agent_lifecycle": "active", "api_calls": 2}

    shared = TaskCardEventProjection.render_event_groups(
        groups,
        normal_rows=1,
        metadata=metadata,
        now=now,
    )
    telegram = TelegramManager._format_task_card_text(
        "",
        "",
        "",
        rows=[
            {"kind": "divider", "text": TelegramManager._TASK_CARD_API_CALL_DIVIDER},
            *groups[0]["events"],
        ],
        normal_rows=1,
        metadata=metadata,
        now=now,
    )

    assert shared == telegram
    assert shared == (
        "📋 TASK CARD\n"
        "──────────\n"
        "• public response\n"
        "• bash.run: build (0s, ???) · 12:00:00 UTC+00\n"
        "\n"
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10, current: 1).\n"
        "agent · active · session · calls 2\n"
        "Last Updated: 02:30:00 UTC+08"
    )
