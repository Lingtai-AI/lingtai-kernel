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
        "📋 活动\n"
        f"{TaskCardEventProjection.API_CALL_DIVIDER}\n"
        "• public response\n"
        "• bash.run: build (0ms, running)\n"
        "\n"
        "请勿回复此任务卡片。使用 /taskcard on|off 切换；"
        "/taskcard N 设置显示组数 (1-10，当前: 1)。\n"
        "active · calls 2\n"
        "最后更新: 02:30:00 U+8"
    )


def test_api_call_embeds_single_timestamp_in_symmetric_divider() -> None:
    """Each API-call group embeds exactly one wall-clock stamp centered in its
    divider line (Jason 2026-08-09, follow-up): per-tool-row stamps are gone,
    and the group's first progress ts becomes the single per-API-call timestamp
    rendered as a symmetric log-style section header `──── 00:22:02 U-7 ────`
    — no separate timestamp row, no marker."""
    ts = datetime(2026, 8, 3, 2, 30, tzinfo=timezone(timedelta(hours=8))).timestamp()
    stamp = TaskCardEventProjection.format_row_timestamp(ts)
    groups = [
        {
            "api_call_id": "api-1",
            "events": [
                {
                    "kind": "tool",
                    "tool": "bash",
                    "tool_action": "run",
                    "reasoning": "build",
                    "status": "success",
                    "_ts": ts,
                    "api_delay_s": 2.3,
                },
            ],
        }
    ]
    now = datetime(2026, 8, 3, 3, 0, tzinfo=timezone(timedelta(hours=8)))
    text = TaskCardEventProjection.render_event_groups(
        groups, normal_rows=1, metadata=None, now=now,
    )
    assert "↻ 2.3s" in text
    tool_row = next(ln for ln in text.splitlines() if "bash.run" in ln)
    assert stamp not in tool_row
    divider = TaskCardEventProjection.API_CALL_DIVIDER
    # The stamp sits centered between the symmetric dash runs, before the API
    # metadata line.
    divider_line = next(ln for ln in text.splitlines() if ln.startswith(divider))
    assert f"{divider} {stamp} {divider}" == divider_line
    divider_idx = text.index(divider_line)
    api_idx = text.index("↻ 2.3s")
    tool_idx = text.index("bash.run")
    assert divider_idx < api_idx < tool_idx


def test_metadata_renders_device_and_working_dir_lines() -> None:
    """Device identity metadata renders a compact ||-separated identity line."""
    metadata = {
        "agent_lifecycle": "active",
        "api_calls": 2,
        "device_short_name": "zesen-desktop",
        "shell_name": "powershell",
        "working_dir": "C:\\Users\\zhuang\\.lingtai\\deepseek-1",
    }
    lines = TaskCardEventProjection.format_metadata(metadata)
    assert len(lines) == 2
    assert lines[0] == "active · calls 2"
    identity = lines[1]
    assert "device · zesen-desktop · shell powershell" in identity
    assert "path · C:\\Users\\zhuang" in identity
    assert " | " in identity


def test_metadata_omits_device_line_when_only_bad_values() -> None:
    """Missing or malformed device identity degrades to no device/path groups."""
    metadata = {"agent_lifecycle": "active", "device_short_name": 42, "working_dir": ""}
    lines = TaskCardEventProjection.format_metadata(metadata)
    assert not any("device · " in line for line in lines)
    assert not any("path · " in line for line in lines)


def test_group_events_sets_api_delay_s_delta_from_previous_tool_ts() -> None:
    """CHANGE A: tool rows carry ``api_delay_s`` = ts gap since the previous
    tool_call (0.0 for the very first), and the private raw ``_ts`` never leaks
    into the flattened public window."""
    events = [
        {"type": "tool_call", "api_call_id": "api-1", "ts": 100.0,
         "tool_name": "bash", "tool_call_id": "call-1",
         "tool_args": {"action": "run", "_reasoning": "a"}},
        {"type": "tool_call", "api_call_id": "api-1", "ts": 103.4,
         "tool_name": "read", "tool_call_id": "call-2",
         "tool_args": {"action": "read", "_reasoning": "b"}},
    ]
    projected = [
        (event, TaskCardEventProjection.project_event(event)) for event in events
    ]
    groups = TaskCardEventProjection.group_events(projected)
    rows = TaskCardEventProjection.flatten_groups(groups)
    assert [row["api_delay_s"] for row in rows] == [0.0, 3.4]
    assert all("_ts" not in row for row in rows)
    assert all("api_delay_s" in row for row in rows)


def test_format_elapsed_ms_renders_milliseconds_defensively() -> None:
    """CHANGE B: sub-second durations render as whole ms (``412ms``), with a
    sane floor/ceiling and no crash on junk payloads."""
    assert TaskCardEventProjection.format_elapsed_ms(412) == "0.4s"
    assert TaskCardEventProjection.format_elapsed_ms(0) == "0ms"
    assert TaskCardEventProjection.format_elapsed_ms(2300) == "2.3s"
    # A legacy elapsed_s value converts to ms through row_elapsed_ms.
    assert TaskCardEventProjection.row_elapsed_ms({"elapsed_s": 0.412}) == 412.0
    assert TaskCardEventProjection.format_elapsed_ms(
        TaskCardEventProjection.row_elapsed_ms({"elapsed_s": 0.412})
    ) == "0.4s"
    # raw elapsed_ms wins over the converted elapsed_s.
    row = {"elapsed_ms": 412, "elapsed_s": 2.3}
    assert TaskCardEventProjection.row_elapsed_ms(row) == 412.0
    # Defensive: junk, negatives, non-finite, and runaway values degrade safely.
    assert TaskCardEventProjection.format_elapsed_ms("junk") == "0ms"
    assert TaskCardEventProjection.format_elapsed_ms(-5) == "0ms"
    assert TaskCardEventProjection.format_elapsed_ms(float("nan")) == "0ms"
    assert TaskCardEventProjection.format_elapsed_ms(10**12) == (
        f"{TaskCardEventProjection.MAX_ELAPSED_MS / 1000:.1f}s"
    )
