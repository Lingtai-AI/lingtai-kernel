"""Shared Task Card event projection with Telegram compatibility coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lingtai.mcp_servers.task_card import TaskCardEventProjection
from lingtai.mcp_servers.telegram.manager import TelegramManager, _telegram_task_card_html


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


def test_shared_render_stays_unchanged_while_telegram_relayouts_metadata() -> None:
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
    metadata = {
        "agent_lifecycle": "active",
        "api_calls": 2,
        "output_tokens": 12_345,
        "model": "gpt<5>&",
        "device_short_name": "dev-1",
        "working_dir": "/tmp/taskcard",
        "async_work": {"running": 1},
    }

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

    assert shared == (
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10, current: 1).\n"
        "📋 ACTIVITIES\n"
        f"{TaskCardEventProjection.API_CALL_DIVIDER}\n"
        "• public response\n"
        "• bash.run: build (0ms, running)\n"
        "\n"
        "────────\n"
        "Session · active · gpt<5>& · out 12.3k · calls 2\n"
        "────────\n"
        "Identity · device · dev-1 | path · /tmp/taskcard\n"
        "────────\n"
        "Async Work · running 1\n"
        "Scope · recorded running/queued + finished in last 10m\n"
        "Last Updated: 02:30:00 U+8\n"
        "Ask agent for \"Task Card\""
    )
    assert _telegram_task_card_html(shared) == telegram
    assert telegram == (
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10, current: 1).\n"
        "📋 <b>ACTIVITIES</b>\n"
        f"{TaskCardEventProjection.API_CALL_DIVIDER}\n"
        "• public response\n"
        "• bash.run: build (0ms, running)\n"
        "\n"
        "📊 <b>SESSION</b>\n"
        "<b>Agent</b> · active · gpt&lt;5&gt;&amp;\n"
        "<b>Context</b> · out 12.3k\n"
        "<b>Cache</b> · calls 2\n"
        "\n"
        "🪪 <b>IDENTITY</b>\n"
        "<b>Device</b> · dev-1\n"
        "<b>Path</b> · <code>/tmp/taskcard</code>\n"
        "\n"
        "⚙️ <b>ASYNC WORK</b>\n"
        "<b>Status</b> · running 1\n"
        "<b>Scope</b> · recorded running/queued + finished in last 10m\n"
        "🕒 Last Updated: 02:30:00 U+8\n"
        "💬 <i>Ask agent for \"Task Card\"</i>\n"
        "⚙️ <i>Settings: /taskcard on|off · /taskcard N (1-10)</i>"
    )


def test_telegram_html_converter_localizes_taskcard_settings_hint() -> None:
    assert _telegram_task_card_html('向 agent 询问 "Task Card"') == (
        '💬 <i>向 agent 询问 "Task Card"</i>\n'
        "⚙️ <i>设置: /taskcard on|off · /taskcard N (1-10)</i>"
    )


def test_telegram_html_converter_keeps_api_metrics_plain() -> None:
    shared = "↻ 3.4s ↓1.2k (56.8k) ↑512.3k ◌ 259.8k | 55.0%"

    telegram = _telegram_task_card_html(shared)

    assert telegram == shared
    assert "<code>" not in telegram


def test_telegram_html_converter_escapes_dynamic_text_before_static_markup() -> None:
    shared = TaskCardEventProjection.format_rows_task_card_text([
        {"kind": "text", "text": "public <reply> & note"},
        {
            "tool": "shell",
            "tool_action": "run",
            "reasoning": "inspect <node> & preserve > state",
            "status": "???",
        },
    ])
    telegram = _telegram_task_card_html(shared)

    assert "📋 <b>ACTIVITIES</b>" in telegram
    assert "public &lt;reply&gt; &amp; note" in telegram
    assert "inspect &lt;node&gt; &amp; preserve &gt; state" in telegram
    assert "public <reply>" not in telegram


def test_shared_render_rejects_malformed_pending_activity_labels() -> None:
    for marker in ([], {"arbitrary": "PRIVATE_LABEL"}, "PRIVATE_LABEL"):
        text = TaskCardEventProjection.render_event_groups(
            [{
                "api_call_id": "api-1",
                "events": [{
                    "kind": "tool",
                    "tool": "bash",
                    "tool_action": "run",
                    "reasoning": "build",
                    "status": "???",
                    "_pending_activity": marker,
                }],
            }],
            normal_rows=1,
            metadata=None,
            now=datetime(2026, 8, 3, 2, 30, tzinfo=timezone.utc),
        )
        assert "• bash.run: build (0ms, running)" in text
        assert "PRIVATE_LABEL" not in text
        assert "foreground" not in text
        assert "dispatching async job" not in text


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
    # Explicit Session and Identity sections, with no identity closing divider.
    assert len(lines) == 3
    assert lines[0] == "Session · active · calls 2"
    # Session and identity are separate sections with a divider between them.
    assert lines[1] == "────────"
    identity = lines[2]
    assert lines[-1].startswith("Identity · ")
    assert "device · zesen-desktop · shell powershell" in identity
    assert "path · C:\\Users\\zhuang" in identity
    assert " | " in identity


def test_metadata_renders_service_tier_when_supplied() -> None:
    lines = TaskCardEventProjection.format_metadata(
        {"model": "gpt-5.6-terra", "service_tier": "fast"}
    )
    assert lines == ["Session · gpt-5.6-terra · tier fast"]


_ROW = "total ~$0.0054 · in $0.0029 · write $0.0009 · read $0.0001 · out $0.0015"


def _owner_metadata() -> dict:
    """Deterministic owner-shaped fields whose rendered block exceeds 500 chars."""
    return {
        "model": "m" * 60,
        "thinking": "high",
        "service_tier": "fast",
        "endpoint": "e" * 40,
        "context_tokens": 100_000,
        "context_window": 272_000,
        "context_usage": 0.37,
        "input_tokens": 5_000_000,
        "output_tokens": 200_000,
        "api_calls": 12,
        "device_short_name": "d" * 40,
        "working_dir": "/w/.lingtai/" + "p" * 180,
        "session_cost": _ROW,
        "async_work": {
            "daemon": {
                "running": 2, "done": 1,
                "backend_counts": {"lingtai": 2, "claude-p": 1},
                "usage": {"input_tokens": 1_200, "output_tokens": 300,
                          "cached_tokens": 600, "api_calls": 4},
            },
            "shell": {"running": 1},
        },
    }


@pytest.mark.parametrize("locale", ["en", "zh"])
def test_metadata_has_no_500_char_budget_and_keeps_every_row(locale: str) -> None:
    lines = TaskCardEventProjection.format_metadata(_owner_metadata(), locale)
    assert len("\n".join(lines)) > 500
    divider = TaskCardEventProjection.METADATA_DIVIDER
    assert lines.count(divider) == 2  # Session | Identity | Async Work
    assert any(line.startswith("Cost · ") for line in lines)
    identity = next(line for line in lines if "path · " in line)
    assert "…" not in identity and identity.endswith("p" * 180)
    stats_label = TaskCardEventProjection.daemon_stats_label(locale)
    stats = next(line for line in lines if line.startswith(f"{stats_label} · "))
    assert "in 1.2k" in stats and "api 4" in stats
    assert not any("omitted" in line or "省略" in line for line in lines)


def test_daemon_stats_states_selection_scope_and_cost_unavailable() -> None:
    label = TaskCardEventProjection.daemon_stats_label("en")
    assert label == "Daemon stats (selected runs' reported lifetime usage)"
    zh = TaskCardEventProjection.daemon_stats_label("zh")
    assert "累计" in zh
    lines = TaskCardEventProjection.format_metadata(_owner_metadata())
    # The shared Scope row carries the selection window for every async lane.
    assert "Scope · recorded running/queued + finished in last 10m" in lines
    stats = next(line for line in lines if line.startswith(label))
    assert stats.endswith("cost n/a (not reported)")
    # The Session cost row is the parent's own cost; no daemon price or $0 enters.
    assert [line for line in lines if "$" in line] == [f"Cost · {_ROW}"]
    zh_lines = TaskCardEventProjection.format_metadata(_owner_metadata(), "zh")
    assert "范围 · 已记录的运行中/排队 + 最近 10 分钟内结束" in zh_lines
    zh_stats = next(line for line in zh_lines if line.startswith(zh))
    assert zh_stats.endswith("费用 不可用（未上报）")
    # Daemon runs without reported usage are unavailable, never zero or priced.
    bare = TaskCardEventProjection.format_metadata(
        {"async_work": {"daemon": {"running": 1}}}
    )
    assert bare[-1] == f"{label} · usage n/a (no positive usage reported) · cost n/a (not reported)"
    zh_bare = TaskCardEventProjection.format_metadata(
        {"async_work": {"daemon": {"running": 1}}}, "zh"
    )
    assert zh_bare[-1] == f"{zh} · 用量 不可用（未上报正值） · 费用 不可用（未上报）"
    # Shell-only and all-zero snapshots: scope only when work exists, no stats row.
    shell = TaskCardEventProjection.format_metadata(
        {"async_work": {"shell": {"running": 1}}}
    )
    assert shell[1] == "Scope · recorded running/queued + finished in last 10m"
    assert not any(line.startswith("Daemon stats") for line in shell)
    zero = TaskCardEventProjection.format_metadata(
        {"async_work": {"daemon": {"running": 0}, "shell": {"done": 0}}}
    )
    assert zero == []


def test_telegram_html_renders_scoped_daemon_stats_row() -> None:
    text = TaskCardEventProjection.format_rows_task_card_text(
        [{"tool": "bash", "tool_action": "run", "reasoning": "x"}],
        metadata=_owner_metadata(),
    )
    html = _telegram_task_card_html(text)
    label = TaskCardEventProjection.daemon_stats_label("en")
    assert f"<b>{label}</b> · in 1.2k" in html
    assert "<b>Cost</b> · total ~$0.0054" in html


def test_extreme_metadata_fits_overall_limit_with_explicit_omitted_indicator() -> None:
    metadata = _owner_metadata()
    metadata["async_work"]["daemon"]["backend_counts"] = {
        f"backend-{index:03d}": 1 for index in range(400)
    }
    metadata["async_work"]["daemon"]["model_counts"] = {
        f"model-{index:03d}": 1 for index in range(300)
    }
    rows = [
        {"tool": "bash", "tool_action": "run", "reasoning": "r" * 400, "status": "success"}
        for _ in range(3)
    ]
    for kwargs in ({"rows": rows}, {"rows": []}):
        text = TaskCardEventProjection.format_rows_task_card_text(
            kwargs["rows"], metadata=metadata
        )
        assert len(text) <= TaskCardEventProjection.TEXT_LIMIT
        assert "omitted" in text
        stats_label = TaskCardEventProjection.daemon_stats_label("en")
        assert any(line.startswith(f"{stats_label} · ") for line in text.splitlines())
        assert "Cost · " in text and "Identity · " in text
        assert text.splitlines()[-1] == 'Ask agent for "Task Card"'
    # Within the limit nothing is omitted, so the unbounded form is unchanged.
    small = TaskCardEventProjection.format_rows_task_card_text(
        rows[:1], metadata=_owner_metadata()
    )
    assert "omitted" not in small


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
    assert [row["api_delay_s"] for row in rows] == pytest.approx([0.0, 3.4])
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


def _session_usage_event(
    *,
    molt_count: int = 2,
    api_call_index: int = 2,
    input_tokens: int = 250_000,
    output_tokens: int = 1_000,
    cached_tokens: int = 200_000,
    current_input: int = 150_300,
    current_output: int = 100,
    current_cached: int = 120_000,
) -> dict:
    cache_miss = input_tokens - cached_tokens
    window = 272_000
    budget = 1_000_000
    return {
        "type": "llm_response",
        "api_call_id": f"api-{api_call_index}",
        "input_tokens": current_input,
        "output_tokens": current_output,
        "cached_tokens": current_cached,
        "session_usage": {
            "schema": TaskCardEventProjection.SESSION_USAGE_SCHEMA,
            "molt_count": molt_count,
            "api_call_index": api_call_index,
            "api_calls": api_call_index,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "avg_input_tokens_per_api_call": int(round(input_tokens / api_call_index)),
            "session_cache_rate": round(cached_tokens / input_tokens, 5),
            "cache_miss_tokens": cache_miss,
            "cache_miss_budget": budget,
            "cache_miss_remaining_tokens": budget - cache_miss,
            "context_tokens": current_input,
            "context_window": window,
            "context_usage": round(current_input / window, 5),
        },
    }


def _legacy_session_carrier(api_calls: object, **metadata: object) -> dict:
    return {
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": api_calls, **metadata},
        }}}},
    }


def test_session_usage_reducer_prefers_v1_and_rejects_out_of_order() -> None:
    state = TaskCardEventProjection.reduce_session_usage_event(
        None, _legacy_session_carrier(93)
    )
    assert TaskCardEventProjection.session_usage_metadata(state) == {"api_calls": 93}

    state = TaskCardEventProjection.reduce_session_usage_event(
        state, _session_usage_event()
    )
    expected = TaskCardEventProjection.session_usage_metadata(state)
    for stale in (
        _legacy_session_carrier(999),
        _session_usage_event(api_call_index=1, input_tokens=160_000),
    ):
        state = TaskCardEventProjection.reduce_session_usage_event(state, stale)
        assert TaskCardEventProjection.session_usage_metadata(state) == expected


def test_session_usage_reducer_fails_closed_and_molt_allows_reset() -> None:
    state = TaskCardEventProjection.reduce_session_usage_event(
        None, _session_usage_event()
    )
    malformed = _session_usage_event(api_call_index=3, input_tokens=300_000)
    malformed["session_usage"]["cache_miss_tokens"] += 1
    state = TaskCardEventProjection.reduce_session_usage_event(state, malformed)
    assert TaskCardEventProjection.session_usage_metadata(state) == {}

    state = TaskCardEventProjection.reduce_session_usage_event(
        state, {"type": "psyche_molt", "molt_count": 3}
    )
    reset = _session_usage_event(
        molt_count=3,
        api_call_index=1,
        input_tokens=150_300,
        output_tokens=100,
        cached_tokens=120_000,
    )
    state = TaskCardEventProjection.reduce_session_usage_event(state, reset)
    expected = TaskCardEventProjection.session_usage_metadata(state)
    assert (expected["api_calls"], expected["input_tokens"]) == (1, 150_300)

    state = TaskCardEventProjection.reduce_session_usage_event(
        state, _session_usage_event(molt_count=2, api_call_index=9)
    )
    assert TaskCardEventProjection.session_usage_metadata(state) == expected


def test_carrierless_legacy_llm_response_invalidates_legacy_session() -> None:
    state = TaskCardEventProjection.reduce_session_usage_event(
        None, _legacy_session_carrier(7)
    )
    assert TaskCardEventProjection.session_usage_metadata(state) == {"api_calls": 7}
    state = TaskCardEventProjection.reduce_session_usage_event(state, {
        "type": "llm_response",
        "api_call_id": "api-legacy",
        "input_tokens": 10,
        "output_tokens": 2,
        "cached_tokens": 0,
    })
    assert TaskCardEventProjection.session_usage_metadata(state) == {}


def test_session_usage_projector_rejects_malformed_fields_and_rates() -> None:
    for key, value in (("context_tokens", "150300"), ("input_tokens", 100)):
        event = _session_usage_event()
        event["session_usage"][key] = value
        assert TaskCardEventProjection.project_llm_response_session_usage(event) == {}
    for key in ("session_cache_rate", "context_usage"):
        event = _session_usage_event()
        event["session_usage"][key] += 0.000009
        assert TaskCardEventProjection.project_llm_response_session_usage(event) == {}


def test_generation_fences_ignore_old_and_recover_after_malformed_new() -> None:
    state = TaskCardEventProjection.reduce_session_usage_event(
        None, _session_usage_event(molt_count=3, api_call_index=5)
    )
    expected = TaskCardEventProjection.session_usage_metadata(state)
    malformed_old = _session_usage_event(molt_count=2, api_call_index=6)
    malformed_old["session_usage"]["api_call_index"] = "bad"
    for old in (malformed_old, _session_usage_event(molt_count=2, api_call_index=6)):
        state = TaskCardEventProjection.reduce_session_usage_event(state, old)
        assert TaskCardEventProjection.session_usage_metadata(state) == expected

    malformed_new = _session_usage_event(molt_count=4, api_call_index=1)
    malformed_new["session_usage"]["api_call_index"] = "bad"
    state = TaskCardEventProjection.reduce_session_usage_event(state, malformed_new)
    assert TaskCardEventProjection.session_usage_metadata(state) == {}
    state = TaskCardEventProjection.reduce_session_usage_event(
        state, _session_usage_event(molt_count=4, api_call_index=1)
    )
    assert TaskCardEventProjection.session_usage_metadata(state)["api_calls"] == 1


def test_session_usage_accepts_optional_or_over_window_context_metadata() -> None:
    without_window = _session_usage_event()
    without_window["session_usage"].pop("context_window")
    without_window["session_usage"].pop("context_usage")
    metadata = TaskCardEventProjection.project_llm_response_session_usage(
        without_window
    )["metadata"]
    assert "context_window" not in metadata and "context_usage" not in metadata

    over_window = _session_usage_event(input_tokens=400_000, current_input=300_000)
    over_window["session_usage"]["context_usage"] = round(300_000 / 272_000, 5)
    metadata = TaskCardEventProjection.project_llm_response_session_usage(
        over_window
    )["metadata"]
    assert metadata["context_usage"] > 1.0


def test_stream_metrics_formula_two_lines_and_carrier_preservation():
    event = {"type": "llm_response", "api_call_id": "api_timed",
             "input_tokens": 1000, "cached_tokens": 100, "output_tokens": 200,
             "thinking_tokens": 20,
             "stream_timing": {"first_token_s": 1.2, "generation_s": 4.0,
                               "generation_tokens": 180}}
    _, usage = TaskCardEventProjection.project_llm_response_usage(event)
    info = TaskCardEventProjection.format_divider_info(12.4, usage, stream_metrics=True)
    assert info == "↻12.4s · ⏱7.2s · ⚡1.2s · 45 tok/s\n↓200 (20) ↑900 ◌ 1.0k | 10.0%"
    # Other consumers opt out, preserving the established single line.
    assert "⚡" not in TaskCardEventProjection.format_divider_info(12.4, usage)
    groups = [{"events": [{"kind": "text", "text": "hello", "api_delay_s": 12.4,
                            "_api_call_id": "api_timed", "_tool_call_id": "tool"}]}]
    TaskCardEventProjection.apply_tool_usages(groups, {
        "api_timed": usage, "tool": {"output": 200, "thinking": 20, "cache_miss": 900},
    })
    assert groups[0]["events"][0]["_usage"]["stream_timing"] == usage["stream_timing"]
    frame = TaskCardEventProjection.render_event_groups(groups, normal_rows=10, stream_metrics=True)
    assert "↻12.4s · ⏱7.2s · ⚡1.2s · 45 tok/s\n↓200 (20) ↑900" in frame


def test_missing_invalid_estimated_stream_metrics_omit_speed():
    base = {"type": "llm_response", "api_call_id": "api",
            "input_tokens": 10, "cached_tokens": 0, "output_tokens": 5}
    for timing in (None, {}, {"first_token_s": float("nan")},
                   {"first_token_s": True}, {"first_token_s": 1, "generation_s": 0,
                                             "generation_tokens": 5},
                   {"first_token_s": 1, "generation_s": float("inf"),
                    "generation_tokens": 5}):
        _, usage = TaskCardEventProjection.project_llm_response_usage({**base, "stream_timing": timing})
        text = TaskCardEventProjection.format_divider_info(12.4, usage, stream_metrics=True)
        assert "tok/s" not in text
    _, usage = TaskCardEventProjection.project_llm_response_usage({
        **base, "estimated": True, "stream_timing": {
            "first_token_s": 1, "generation_s": 2, "generation_tokens": 5,
        },
    })
    text = TaskCardEventProjection.format_divider_info(12.4, usage, stream_metrics=True)
    assert "⚡1.0s" in text and "tok/s" not in text


def test_idle_time_is_opt_in_and_does_not_change_other_channel_frames():
    legacy = TaskCardEventProjection.format_divider_info(12.4, None)
    assert TaskCardEventProjection.format_divider_info(12.4, None, idle_s=2.5) == legacy
    assert TaskCardEventProjection.format_divider_info(
        12.4, None, stream_metrics=True, idle_s=2.5,
    ) == "↻12.4s · ☕2.5s"


def test_other_time_uses_unrounded_gap_and_omits_incomplete_or_negative():
    timing = {"first_token_s": 1.234, "generation_s": 4.567,
              "generation_tokens": 100}
    render = TaskCardEventProjection.format_divider_info
    text = render(12.345, {"stream_timing": timing}, stream_metrics=True, idle_s=2)
    assert text.startswith("↻12.3s · ⏱4.5s · ☕2.0s · ⚡1.2s")
    assert "⏱" not in render(12.345, {"stream_timing": timing})
    for gap in (None, 0, 5, float("nan"), float("inf")):
        assert "⏱" not in render(gap, {"stream_timing": timing}, stream_metrics=True)
    for invalid in ({}, {"first_token_s": 1}, {"generation_s": 2},
                    {"first_token_s": True, "generation_s": 2},
                    {"first_token_s": 1, "generation_s": -1},
                    {"first_token_s": 1, "generation_s": float("inf")}):
        assert "⏱" not in render(12, {"stream_timing": invalid}, stream_metrics=True)
    assert "⏱0.0s" in render(3, {"stream_timing": {
        "first_token_s": 1, "generation_s": 2}}, stream_metrics=True)
    events = [({"api_call_id": f"api-{i}"}, {"_ts": ts})
              for i, ts in enumerate((100.0, 112.345))]
    groups = TaskCardEventProjection.group_events(events)
    assert groups[1]["events"][0]["api_delay_s"] == 112.345 - 100.0


def test_residual_subtracts_measured_idle_without_changing_gap_or_speed():
    render = TaskCardEventProjection.format_divider_info
    usage = {"stream_timing": {"first_token_s": 9.4, "generation_s": 7.3,
                               "generation_tokens": 190}}
    assert render(121.0, usage, stream_metrics=True, idle_s=102.8) == (
        "↻121.0s · ⏱1.5s · ☕102.8s · ⚡9.4s · 26 tok/s")
    # Missing/invalid idle is not a measured zero: retain the prior inclusive
    # residual, without claiming coffee, rather than inventing an idle value.
    for idle in (None, True, -1, float("nan"), float("inf")):
        assert render(121, usage, stream_metrics=True, idle_s=idle) == (
            "↻121.0s · ⏱104.3s · ⚡9.4s · 26 tok/s")
    assert "⏱" not in render(121, usage, stream_metrics=True, idle_s=105)
    assert "☕105.0s" in render(121, usage, stream_metrics=True, idle_s=105)
