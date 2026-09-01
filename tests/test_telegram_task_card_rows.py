"""Multi-row Task Card render: parallel/sequential rows + fixed footer.

Parallel tool calls appear as one row per call id (tool.action + reasoning +
own elapsed); a completed row is frozen with a done marker and its final
elapsed.  Both the running and the frozen last-behavior render carry the fixed
human warning footer, and redaction happens before any truncation so a secret
can never survive a length-pressure trim.
"""

from __future__ import annotations

import json
import os
import time

from lingtai.mcp_servers.telegram.manager import (
    TelegramManager,
    _TASK_CARD_FOOTER,
    _task_card_shell_status,
    _task_card_shell_in_window,
)
from tests._notification_store_helpers import FakeNotificationStore


def _fmt(rows):
    return TelegramManager._format_task_card_text("", "", "", rows=rows)


# ---------------------------------------------------------------------------
# Footer — fixed human warning in every render
# ---------------------------------------------------------------------------

def test_footer_constant_exact_text():
    assert _TASK_CARD_FOOTER == (
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10"
    )


def test_footer_renders_with_current_row_count_suffix():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "compile",
         "elapsed_s": 3, "done": False},
    ])
    assert (
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10, current: 1)."
    ) in text


def test_running_render_has_footer():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "compile",
         "elapsed_s": 3, "done": False},
    ])
    assert _TASK_CARD_FOOTER in text
    assert "📋 ACTIVITIES" in text


def test_frozen_render_has_footer():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "compile",
         "elapsed_s": 7, "done": True},
    ])
    assert _TASK_CARD_FOOTER in text


# ---------------------------------------------------------------------------
# One row per call, with tool.action + reasoning + own elapsed
# ---------------------------------------------------------------------------

def test_single_row_shows_tool_action_reasoning_elapsed():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "compile project",
         "elapsed_s": 3, "done": False},
    ])
    assert "bash.run" in text
    assert "compile project" in text
    # Elapsed renders adaptively: ms under 0.1s, one-decimal seconds above.
    assert "3.0s" in text
    assert "3000ms" not in text


def test_parallel_rows_all_represented_with_independent_elapsed():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 5, "done": False},
        {"tool": "read", "tool_action": "", "reasoning": "open file",
         "elapsed_s": 2, "done": False},
        {"tool": "grep", "tool_action": "", "reasoning": "scan",
         "elapsed_s": 8, "done": True},
    ])
    # Each row present with its own tool + whole-millisecond elapsed.
    assert "bash.run" in text
    assert "read" in text
    assert "grep" in text
    assert "5.0s" in text
    assert "2.0s" in text
    assert "8.0s" in text


# ---------------------------------------------------------------------------
# Adaptive duration display rule (ms under 0.1s, x.x s above)
# ---------------------------------------------------------------------------

def test_adaptive_duration_renders_one_decimal_second():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 12, "done": False},
    ])
    assert "12.0s" in text
    # The elapsed suffix is one-decimal seconds for >=0.1s durations.
    row_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    elapsed_suffix = row_line[row_line.rindex("("):]  # "(12000ms)"
    assert elapsed_suffix == "(12.0s)"
    assert "." in elapsed_suffix


def test_float_elapsed_payload_floors_to_one_decimal_second():
    """A float elapsed (e.g. from an in-flight value) floors to one decimal — 8.99s displays 9.0s."""
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 8.99, "done": False},
    ])
    assert "9.0s" in text
    assert "8.99" not in text
    assert "9s" not in text


def test_zero_elapsed_renders_zero_ms():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 0, "done": False},
    ])
    assert "0ms" in text
    assert "0.0s" not in text


def test_done_row_elapsed_is_whole_ms():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 7, "done": True},
    ])
    assert "7.0s" in text
    assert "7000ms" not in text


def test_done_row_has_marker_and_active_row_does_not():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 5, "done": True},
        {"tool": "read", "tool_action": "", "reasoning": "open",
         "elapsed_s": 2, "done": False},
    ])
    lines = text.splitlines()
    bash_line = next(ln for ln in lines if "bash.run" in ln)
    read_line = next(ln for ln in lines if "read" in ln and "open" in ln)
    assert "✓" in bash_line
    assert "✓" not in read_line


# ---------------------------------------------------------------------------
# Frozen last-behavior: concrete rows, NOT a generic overall DONE headline
# ---------------------------------------------------------------------------

def test_frozen_render_keeps_concrete_rows_no_generic_done_subject():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 5, "done": True},
        {"tool": "read", "tool_action": "", "reasoning": "open file",
         "elapsed_s": 2, "done": True},
    ])
    # Concrete last-behavior preserved.
    assert "bash.run" in text
    assert "read" in text
    assert "open file" in text
    # No generic overall DONE subject replacing the rows.
    assert "TASK CARD · DONE" not in text
    assert "✅" not in text


# ---------------------------------------------------------------------------
# Redaction BEFORE truncation; every parallel row still represented
# ---------------------------------------------------------------------------

def test_redaction_before_truncation_per_row():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    text = _fmt([
        {"tool": "bash", "tool_action": "run",
         "reasoning": "X" * 480 + " " + secret,
         "elapsed_s": 1, "done": False},
    ])
    assert "ghp_" not in text
    assert "<REDACTED" in text or "github_token" in text


def test_moderate_rows_fit_under_transport_limit_with_every_row_represented():
    """At a moderate row count, shrinking the huge per-row reasoning keeps the
    render under the Telegram 4096-char hard limit while no parallel row is hidden
    — each call id remains visible.  This is the excerpt-shrinkage guarantee at a
    row count with headroom, NOT an all-N bound: fixed scaffolding is unbounded in
    row count, so an extreme N can still exceed the limit (see
    tests/test_telegram_task_card_blockers.py::
    test_extreme_row_count_exceeds_budget_but_keeps_every_row)."""
    rows = [
        {"tool": f"tool{i}", "tool_action": "", "reasoning": "Z" * 600,
         "elapsed_s": i, "done": False}
        for i in range(8)
    ]
    text = _fmt(rows)
    # At 8 rows the excerpt budget has ample headroom under the 4096 hard limit.
    assert len(text) <= 4096
    # Each of the 8 rows is still represented by its tool name.
    for i in range(8):
        assert f"tool{i}" in text
    # Footer survives the length pressure.
    assert _TASK_CARD_FOOTER in text


# ---------------------------------------------------------------------------
# Integration: rows route through manager.handle → real dispatch → send/edit
# ---------------------------------------------------------------------------

def _integration_manager(tmp_path):
    from pathlib import Path

    class Acct:
        alias = "mybot"

        def __init__(self):
            self.calls = []
            self._cards = {}

        def send_message(self, chat_id, text, reply_to_message_id=None, **kw):
            self.calls.append(("send_message", chat_id, text))
            return {"message_id": 100}

        def edit_message(self, chat_id, message_id, text, **kw):
            self.calls.append(("edit_message", chat_id, message_id, text))
            return {"ok": True}

        def delete_message(self, chat_id, message_id):
            self.calls.append(("delete_message", chat_id, message_id))
            return {"ok": True}

        def get_task_card(self, chat_id):
            return self._cards.get(str(chat_id))

        def set_task_card(self, chat_id, cid):
            self._cards[str(chat_id)] = cid

        def clear_task_card(self, chat_id):
            self._cards.pop(str(chat_id), None)

    class Svc:
        def __init__(self):
            self.default_account = Acct()

        def get_account(self, alias):
            assert alias == "mybot"
            return self.default_account

    svc = Svc()
    mgr = TelegramManager(
        svc,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return mgr, svc.default_account


def test_full_routing_rows_create_renders_multi_row_card(tmp_path):
    mgr, account = _integration_manager(tmp_path)
    r = mgr.handle({
        "action": "_task_card_update",
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "rows": [
            {"tool": "bash", "tool_action": "run", "reasoning": "build",
             "elapsed_s": 2, "done": False},
            {"tool": "read", "tool_action": "", "reasoning": "open",
             "elapsed_s": 1, "done": False},
        ],
    })
    assert r["status"] == "ok"
    sends = [c for c in account.calls if c[0] == "send_message"]
    assert len(sends) == 1
    text = sends[0][2]
    assert "bash.run" in text
    assert "read" in text
    assert _TASK_CARD_FOOTER in text


def test_full_routing_rows_finalize_freezes_without_generic_done(tmp_path):
    mgr, account = _integration_manager(tmp_path)
    r1 = mgr.handle({
        "action": "_task_card_update", "sub_action": "create",
        "account": "mybot", "chat_id": 999,
        "rows": [{"tool": "bash", "tool_action": "run", "reasoning": "build",
                  "elapsed_s": 2, "done": False}],
    })
    card_id = r1["message_id"]
    r2 = mgr.handle({
        "action": "_task_card_update", "sub_action": "finalize",
        "card_message_id": card_id,
        "rows": [{"tool": "bash", "tool_action": "run", "reasoning": "build",
                  "elapsed_s": 5, "done": True}],
    })
    assert r2["status"] == "ok"
    edits = [c for c in account.calls if c[0] == "edit_message"]
    final_text = edits[-1][3]
    assert "bash.run" in final_text
    assert "✓" in final_text
    assert "TASK CARD · DONE" not in final_text
    assert _TASK_CARD_FOOTER in final_text


def test_metadata_is_two_lines_bounded_and_between_footer_and_timestamp():
    text = TelegramManager._format_task_card_text(
        "",
        "",
        "",
        rows=[{
            "tool": "bash",
            "tool_action": "run",
            "reasoning": "build",
            "elapsed_s": 3,
            "done": False,
            "started_at": "12:34:56 UTC-07",
        }],
        metadata={
            "session_cache_rate": 0.87803,
            "cache_miss_tokens": 170631,
            "cache_miss_budget": 1_000_000,
            "api_calls": 13,
            "context_tokens": 171246,
            "context_window": 272000,
            "context_usage": 0.62958,
        },
    )
    lines = text.splitlines()
    footer_idx = next(i for i, line in enumerate(lines) if _TASK_CARD_FOOTER in line)
    time_idx = next(i for i, line in enumerate(lines) if line.startswith("Last Updated: "))
    expected_metadata = (
        "Session · ctx 63% · 171.2k/272.0k · cache 87.8% · "
        "miss 170.6k/1.0M · calls 13"
    )
    metadata_idx = lines.index(expected_metadata, footer_idx)
    assert lines[metadata_idx - 1] == "────────"
    metadata_lines = lines[metadata_idx:time_idx]
    assert metadata_lines == [
        expected_metadata,
    ]
    assert len(metadata_lines) == 1
    assert len(metadata_lines[0]) <= 500


def test_metadata_omits_untrusted_or_invalid_values():
    lines = TelegramManager._format_task_card_metadata({
        "session_cache_rate": "secret",
        "cache_miss_tokens": True,
        "cache_miss_budget": -1,
        "api_calls": object(),
        "context_tokens": "bad",
        "context_window": None,
        "context_usage": 7,
    })
    assert lines == []


def test_metadata_pathological_counts_never_overflow_or_break_budget():
    lines = TelegramManager._format_task_card_metadata({
        "session_cache_rate": 1.0,
        "cache_miss_tokens": 10**1000,
        "cache_miss_budget": 10**1000,
        "api_calls": 10**1000,
        "context_tokens": 10**1000,
        "context_window": 10**1000,
        "context_usage": 1.0,
    })
    assert len(lines) == 1
    assert len(lines[0]) <= 500
    assert "inf" not in lines[0].lower()


# ---------------------------------------------------------------------------
# Agent lifecycle/health in the automatic metadata footer
# ---------------------------------------------------------------------------

def test_metadata_renders_compact_normal_lifecycle_states():
    for state in ("active", "idle", "asleep", "suspended"):
        lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": state})
        assert lines == [f"Session · {'active' if state == 'active' else 'agent · ' + state}"]


def test_metadata_renders_active_seconds_only_when_active():
    # Active state plus a sane age renders the (N sec) suffix.
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "active",
        "agent_active_seconds": 12.0,
    })
    assert lines == ["Session · active (12s)"]
    # Non-active states never render the suffix even if the field is present.
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "idle",
        "agent_active_seconds": 12.0,
    })
    assert lines == ["Session · agent · idle"]
    # Missing/invalid age degrades to the plain lifecycle line.
    lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": "active"})
    assert lines == ["Session · active"]
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "active",
        "agent_active_seconds": "secret",
    })
    assert lines == ["Session · active"]


def test_metadata_renders_stuck_with_refresh_hint():
    lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": "stuck"})
    assert lines == ["Session · agent · stuck · try /refresh"]


def test_metadata_renders_offline_with_refresh_hint():
    lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": "offline"})
    assert lines == ["Session · agent · offline · try /refresh"]


def test_metadata_suspended_never_gets_refresh_hint():
    lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": "suspended"})
    assert lines == ["Session · agent · suspended"]
    assert "/refresh" not in lines[0]


def test_metadata_ignores_unrecognized_lifecycle_value():
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "haunted",
        "session_cache_rate": 0.5,
    })
    assert lines == ["Session · cache 50.0%"]


def test_metadata_agent_and_session_combine_on_line_one_ctx_preserved():
    """Regression: merging agent+session onto line 1 must not drop ctx."""
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "active",
        "session_cache_rate": 0.87803,
        "cache_miss_tokens": 170631,
        "cache_miss_budget": 1_000_000,
        "api_calls": 13,
        "context_tokens": 171246,
        "context_window": 272000,
        "context_usage": 0.62958,
    })
    assert lines == [
        "Session · active · ctx 63% · 171.2k/272.0k · cache 87.8% · miss 170.6k/1.0M · calls 13",
    ]
    assert len(lines) == 1
    assert len(lines[0]) <= 500


def test_metadata_stuck_hint_and_ctx_both_survive_full_metadata():
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "stuck",
        "session_cache_rate": 0.87803,
        "cache_miss_tokens": 170631,
        "cache_miss_budget": 1_000_000,
        "api_calls": 13,
        "context_tokens": 171246,
        "context_window": 272000,
        "context_usage": 0.62958,
    })
    # The 2-line cap holds; the hint leads line 1 (safe from end-truncation)
    # and ctx survives as line 2 instead of being dropped by the cap.
    assert lines == [
        "Session · agent · stuck · try /refresh · ctx 63% · 171.2k/272.0k · cache 87.8% · miss 170.6k/1.0M · calls 13",
    ]
    assert lines[0].startswith("Session · agent · stuck · try /refresh ·")
    assert len(lines) == 1
    assert len(lines[0]) <= 500


def test_metadata_unchanged_when_no_agent_lifecycle_present():
    """Regression guard: no agent key means byte-identical old behavior."""
    lines = TelegramManager._format_task_card_metadata({
        "session_cache_rate": 0.87803,
        "cache_miss_tokens": 170631,
        "cache_miss_budget": 1_000_000,
        "api_calls": 13,
        "context_tokens": 171246,
        "context_window": 272000,
        "context_usage": 0.62958,
    })
    assert lines == [
        "Session · ctx 63% · 171.2k/272.0k · cache 87.8% · miss 170.6k/1.0M · calls 13",
    ]


# ---------------------------------------------------------------------------
# _task_card_agent_lifecycle_status — sources the published Agent record
# (system/agent_record.json), degrades safely. The record's own
# health.liveness field is a write-time snapshot baked in by whichever agent
# process was alive when it wrote the record; it never advances once that
# process dies, so the read path recomputes freshness itself from
# health.heartbeat_at against the current wall clock (HEARTBEAT_LIVENESS_SECONDS
# threshold) instead of trusting the stored string. These tests assert on the
# record's heartbeat_at field (not liveness) driving the outcome, and include
# cases where a stale heartbeat_at is paired with a frozen liveness="fresh"
# string to prove the frozen field is ignored.
# ---------------------------------------------------------------------------

from lingtai.kernel.config import HEARTBEAT_LIVENESS_SECONDS


def _write_agent_record(tmp_path, *, state=None, liveness=None, heartbeat_at=None,
                         last_api_call_at=None, last_progress_at=None, raw=None):
    import json as _json

    if raw is not None:
        payload = raw
    else:
        payload = {
            "schema": "lingtai.agent_record/v1", "schema_version": 1,
            "generated_at": "2026-08-20T00:00:00Z",
            "session": {} if state is None else {"state": state},
            "health": {
                k: v for k, v in {
                    "liveness": liveness,
                    "heartbeat_at": heartbeat_at,
                    "last_api_call_at": last_api_call_at,
                    "last_progress_at": last_progress_at,
                }.items() if v is not None
            },
        }
    path = tmp_path / "system" / "agent_record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    elif isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(_json.dumps(payload), encoding="utf-8")


def test_lifecycle_status_missing_file_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_malformed_json_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, raw="{not json")
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_non_object_json_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, raw=[1, 2, 3])
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_non_utf8_bytes_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, raw=b"\xff\xfe\x00\x01")
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_missing_runtime_block_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, raw={"identity": {}})
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_runtime_not_a_dict_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, raw={"session": "active"})
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_unrecognized_state_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, state="haunted")
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_suspended_ignores_stale_heartbeat(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    stale_heartbeat_at = time.time() - (HEARTBEAT_LIVENESS_SECONDS + 60.0)
    _write_agent_record(
        tmp_path, state="suspended", liveness="fresh", heartbeat_at=stale_heartbeat_at,
    )
    assert mgr._task_card_agent_lifecycle_status() == "suspended"


def test_lifecycle_status_stuck_ignores_stale_heartbeat(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    stale_heartbeat_at = time.time() - (HEARTBEAT_LIVENESS_SECONDS + 60.0)
    _write_agent_record(
        tmp_path, state="stuck", liveness="fresh", heartbeat_at=stale_heartbeat_at,
    )
    assert mgr._task_card_agent_lifecycle_status() == "stuck"


def test_lifecycle_status_active_with_fresh_heartbeat_stays_active(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(
        tmp_path, state="active", liveness="fresh", heartbeat_at=time.time(),
    )
    assert mgr._task_card_agent_lifecycle_status() == "active"


def test_lifecycle_status_idle_with_stale_heartbeat_becomes_offline_despite_frozen_fresh_liveness(tmp_path):
    # Regression: a dead process's Agent record keeps whatever health.liveness
    # string was true the instant it last wrote the record ("fresh", since it
    # was alive then) forever after — that field can never advance on its own.
    # The read path must recompute freshness from heartbeat_at against the
    # current wall clock instead of trusting the frozen field, or a dead
    # process's last idle record renders as "idle" forever instead of offline.
    mgr, _ = _integration_manager(tmp_path)
    stale_heartbeat_at = time.time() - (HEARTBEAT_LIVENESS_SECONDS + 60.0)
    _write_agent_record(
        tmp_path, state="idle", liveness="fresh", heartbeat_at=stale_heartbeat_at,
    )
    assert mgr._task_card_agent_lifecycle_status() == "offline"


def test_lifecycle_status_asleep_with_stale_heartbeat_becomes_offline_despite_frozen_fresh_liveness(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    stale_heartbeat_at = time.time() - (HEARTBEAT_LIVENESS_SECONDS + 60.0)
    _write_agent_record(
        tmp_path, state="asleep", liveness="fresh", heartbeat_at=stale_heartbeat_at,
    )
    assert mgr._task_card_agent_lifecycle_status() == "offline"


def test_lifecycle_status_fresh_heartbeat_wins_over_frozen_stale_liveness(tmp_path):
    # The inverse of the regression above: a frozen liveness="stale" string
    # must not override a heartbeat that is, right now, still within the
    # threshold — the recomputation is authoritative in both directions.
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(
        tmp_path, state="idle", liveness="stale", heartbeat_at=time.time(),
    )
    assert mgr._task_card_agent_lifecycle_status() == "idle"


def test_lifecycle_status_missing_heartbeat_becomes_offline(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, state="idle")
    assert mgr._task_card_agent_lifecycle_status() == "offline"


# ---------------------------------------------------------------------------
# _task_card_event_metadata_snapshot — merges lifecycle without a new store
# ---------------------------------------------------------------------------

def test_event_metadata_snapshot_none_when_nothing_available(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    snapshot = mgr._task_card_event_metadata_snapshot()
    # The snapshot always carries the self-identifying device/path fields, so
    # it is never None; lifecycle/model remain absent when nothing is available.
    assert snapshot is not None
    assert "agent_lifecycle" not in snapshot
    assert "model" not in snapshot
    assert "device_short_name" in snapshot
    assert "working_dir" in snapshot


def test_event_metadata_snapshot_adds_lifecycle_alone(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, state="active", liveness="fresh", heartbeat_at=time.time())
    snapshot = mgr._task_card_event_metadata_snapshot()
    assert snapshot["agent_lifecycle"] == "active"
    assert "device_short_name" in snapshot
    assert "working_dir" in snapshot


def test_event_metadata_snapshot_merges_with_existing_session_metadata(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, state="suspended")
    mgr._task_card_event_metadata = {"api_calls": 4}
    snapshot = mgr._task_card_event_metadata_snapshot()
    assert snapshot["api_calls"] == 4
    assert snapshot["agent_lifecycle"] == "suspended"
    assert "device_short_name" in snapshot
    assert "working_dir" in snapshot
    # The manager's own stored metadata must not be mutated by the merge.
    assert mgr._task_card_event_metadata == {"api_calls": 4}


def test_metadata_renders_current_model_first():
    lines = TelegramManager._format_task_card_metadata({
        "model": "deepseek-v4-flash",
        "session_cache_rate": 0.5,
    })
    assert lines == ["Session · deepseek-v4-flash · cache 50.0%"]


def test_event_metadata_snapshot_adds_current_model(tmp_path):
    import json as _json

    (tmp_path / ".agent.json").write_text(
        _json.dumps({"llm": {"model": "deepseek-v4-flash"}}), encoding="utf-8"
    )
    mgr, _ = _integration_manager(tmp_path)
    _write_agent_record(tmp_path, state="active", liveness="fresh", heartbeat_at=time.time())
    snapshot = mgr._task_card_event_metadata_snapshot()
    assert snapshot["agent_lifecycle"] == "active"
    assert snapshot["model"] == "deepseek-v4-flash"
    assert "device_short_name" in snapshot
    assert "working_dir" in snapshot


def test_metadata_renders_absolute_path_not_shortened():
    lines = TelegramManager._format_task_card_metadata({
        "working_dir": "/Users/huangzesen/work/projects/lingtai-dev/dev-2/.lingtai/mimo-1",
        "device_short_name": "MacStudio",
    })
    assert len(lines) == 1
    assert "Identity · device · MacStudio | path · /Users/huangzesen/work/projects/lingtai-dev/dev-2/.lingtai/mimo-1" in lines[0]
    assert "dev-2/.lingtai/mimo-1" not in lines[0].replace("dev-2/.lingtai/mimo-1", "")


def test_metadata_renders_daemon_status_and_stats():
    lines = TelegramManager._format_task_card_metadata({
        "async_work": {
            "running": 3, "failed": 1, "done": 2,
            "daemon": {"running": 3, "failed": 1, "done": 2,
                        "backend_counts": {"lingtai": 2, "claude-p": 1},
                        "input_tokens": 1_234_567, "output_tokens": 340_000,
                        "cached_tokens": 1_049_382, "cli_calls": 12},
        },
    })
    assert lines == [
        "Async Work · running 3 · done 2 · failed 1",
        "Daemons · running 3 · done 2 · failed 1",
        "Backends · claude-p 1 · lingtai 2",
        "Daemon stats · in 1.2M · out 340.0k · cache 85.0% · api 12",
    ]


def test_metadata_renders_backend_stats_only_when_present():
    # No backend_counts -> no backends line.
    lines = TelegramManager._format_task_card_metadata({
        "async_work": {"running": 1, "daemon": {"running": 1}},
    })
    assert lines == ["Async Work · running 1", "Daemons · running 1"]
    # Empty backend_counts -> no backends line.
    lines = TelegramManager._format_task_card_metadata({
        "async_work": {"running": 1, "daemon": {"running": 1, "backend_counts": {}}},
    })
    assert lines == ["Async Work · running 1", "Daemons · running 1"]
    # Populated backend_counts -> sorted backends line.
    lines = TelegramManager._format_task_card_metadata({
        "async_work": {"running": 1, "daemon": {"running": 1, "backend_counts": {"claude-p": 2, "lingtai": 1}}},
    })
    assert lines == [
        "Async Work · running 1",
        "Daemons · running 1",
        "Backends · claude-p 2 · lingtai 1",
    ]


def test_metadata_section_dividers_between_present_sections():
    # Session + Identity -> one divider between the two sections.
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "active",
        "working_dir": "/Users/huangzesen/work/projects/lingtai-dev/dev-2/.lingtai/mimo-1",
    })
    assert lines == [
        "Session · active",
        "────────",
        "Identity · path · /Users/huangzesen/work/projects/lingtai-dev/dev-2/.lingtai/mimo-1",
    ]
    # Session + Identity + Daemon -> dividers between all adjacent sections.
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "active",
        "working_dir": "/Users/huangzesen/work/projects/lingtai-dev/dev-2/.lingtai/mimo-1",
        "async_work": {"running": 1, "daemon": {"running": 1, "backend_counts": {"lingtai": 1}}},
    })
    assert lines == [
        "Session · active",
        "────────",
        "Identity · path · /Users/huangzesen/work/projects/lingtai-dev/dev-2/.lingtai/mimo-1",
        "────────",
        "Async Work · running 1",
        "Daemons · running 1",
        "Backends · lingtai 1",
    ]
    # A single daemon section alone stays clean with no divider.
    lines = TelegramManager._format_task_card_metadata({
        "async_work": {"running": 2, "daemon": {"running": 2}},
    })
    assert lines == ["Async Work · running 2", "Daemons · running 2"]


def test_metadata_omits_daemon_lines_when_no_daemons():
    lines = TelegramManager._format_task_card_metadata({
        "working_dir": "/Users/huangzesen/work/projects/lingtai-dev/dev-2/.lingtai/mimo-1",
        "async_work": {"running": 0, "failed": 0, "done": 0, "cancelled": 0, "timeout": 0},
    })
    assert len(lines) == 1
    assert "Async Work" not in lines[0]
    assert "Daemon stats" not in lines[0]


def test_metadata_daemon_stats_require_positive_counts():
    lines = TelegramManager._format_task_card_metadata({
        "async_work": {"running": 1, "daemon": {
            "running": 1, "failed": 0, "done": 0, "cancelled": 0,
            "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cli_calls": 0,
        }},
    })
    assert lines == ["Async Work · running 1", "Daemons · running 1"]


def test_metadata_displays_lingtai_daemon_models_compactly_and_deterministically():
    single = TelegramManager._format_task_card_metadata({
        "async_work": {"daemon": {
            "running": 1,
            "model_counts": {"gpt-5.6": 1},
        }},
    })
    assert single == [
        "Async Work · running 1",
        "Daemons · running 1 · gpt-5.6",
    ]

    multiple = TelegramManager._format_task_card_metadata({
        "async_work": {"daemon": {
            "running": 3,
            "model_counts": {"zeta-2": 1, "alpha-1": 2, "unsafe model": 99},
        }},
    })
    assert multiple == [
        "Async Work · running 3",
        "Daemons · running 3 · alpha-1 × 2 · zeta-2 × 1",
    ]


def test_daemon_snapshot_uses_ledger_tail_and_windows(tmp_path):
    import json as _json
    from datetime import datetime, timedelta, timezone

    daemons = tmp_path / "daemons"
    (daemons / "em-1").mkdir(parents=True)
    (daemons / "em-2").mkdir()
    (daemons / "em-3").mkdir()
    (daemons / "em-4").mkdir()
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name, state in [
        ("em-1", {"state": "running", "backend": "lingtai", "model": "gpt-5.6", "tokens": {"input": 1000, "output": 500, "cached": 800, "calls": 3}}),
        ("em-2", {"state": "done", "backend": "claude-p", "model": "claude-external", "finished_at": recent, "tokens": {"input": 2000, "output": 700, "cached": 1500, "calls": 4}}),
        ("em-3", {"state": "failed", "finished_at": recent, "model": "external-unknown", "cli_tokens": {"input": 3000, "output": 100, "cached": 0, "calls": 1}}),
        ("em-4", {"state": "done", "backend": "codex", "finished_at": old, "tokens": {"input": 9999, "output": 9999, "cached": 9999, "calls": 99}}),
    ]:
        (daemons / name / "daemon.json").write_text(_json.dumps(state), encoding="utf-8")
        from lingtai.kernel.daemon_dispatch import append_dispatch
        append_dispatch(tmp_path, run_id=name, created_at="2026-08-20T00:00:00Z")

    mgr, _ = _integration_manager(tmp_path)
    snap = mgr._task_card_daemon_snapshot()
    assert snap["running"] == 1
    assert snap["done"] == 1
    assert snap["failed"] == 1
    # em-4 outside the 10-minute window is excluded entirely.
    assert snap["input_tokens"] == 1000 + 2000 + 3000
    assert snap["output_tokens"] == 500 + 700 + 100
    assert snap["cached_tokens"] == 800 + 1500 + 0
    # In-window selected ledgers: LingTai tokens 3 + external fallback tokens 4 + CLI ledger 1.
    # em-4 is outside the window and its 99 calls remain excluded.
    assert snap["cli_calls"] == 8
    # Only actual LingTai backend model values are eligible; external and
    # backend-less records remain visible in their existing lanes but are not
    # misrepresented as LingTai model statistics.
    assert snap["model_counts"] == {"gpt-5.6": 1}
    # Backend distribution counts only in-window runs; em-3 without a backend
    # falls back to "unknown".
    assert snap["backend_counts"] == {"lingtai": 1, "claude-p": 1, "unknown": 1}


def test_daemon_snapshot_returns_none_when_no_daemons(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    assert mgr._task_card_daemon_snapshot() is None


# ---------------------------------------------------------------------------
# Unified async-work lanes: shell classification, mixed arithmetic, and RO scan
# ---------------------------------------------------------------------------


def test_shell_status_classification_matrix() -> None:
    now = time.time()
    cases = [
        ({"status": "launching"}, "running"),
        ({"status": "running", "cancel_requested_at": now}, "running"),
        ({"status": "completed", "cancellation_outcome": "group_cancelled"}, "cancelled"),
        ({"status": "completed", "exit_status_known": True, "exit_code": 0}, "done"),
        ({"status": "completed", "exit_status_known": True, "exit_code": 7}, "failed"),
        ({"status": "completed", "exit_status_known": True, "exit_code": False}, "unknown"),
        ({"status": "completed", "exit_status_known": False}, "unknown"),
        ({"status": "unrecoverable"}, "failed"),
    ]
    for state, expected in cases:
        assert _task_card_shell_status(state) == expected
    assert _task_card_shell_in_window({"status": "running"}, now) is True
    assert _task_card_shell_in_window(
        {"status": "completed", "finished_at": now - 600}, now
    ) is True
    assert _task_card_shell_in_window(
        {"status": "completed", "finished_at": now - 601}, now
    ) is False
    assert _task_card_shell_in_window(
        {"status": "completed", "finished_at": float("nan")}, now
    ) is False


def test_shell_snapshot_is_read_only_and_skips_legacy_jobs(tmp_path):
    jobs = tmp_path / "system" / "jobs"
    jobs.mkdir(parents=True)
    state_dir = jobs / "job-1"
    state_dir.mkdir()
    state_path = state_dir / "state.json"
    state_path.write_text(json.dumps({
        "status": "completed", "finished_at": time.time(),
        "exit_status_known": True, "exit_code": 0,
    }), encoding="utf-8")
    legacy = jobs / "legacy"
    legacy.mkdir()
    before = state_path.read_bytes()
    before_mtime = state_path.stat().st_mtime_ns
    mgr, _ = _integration_manager(tmp_path)
    assert mgr._task_card_async_shell_snapshot() == {
        "running": 0, "done": 1, "failed": 0,
        "cancelled": 0, "timeout": 0, "unknown": 0,
    }
    assert state_path.read_bytes() == before
    assert state_path.stat().st_mtime_ns == before_mtime


def test_async_work_snapshot_mixes_daemon_and_shell_lanes(tmp_path, monkeypatch):
    mgr, _ = _integration_manager(tmp_path)
    monkeypatch.setattr(mgr, "_task_card_daemon_snapshot", lambda: {
        "running": 2, "done": 1, "failed": 0, "cancelled": 0,
        "timeout": 0, "unknown": 1, "backend_counts": {"lingtai": 2},
        "input_tokens": 4, "output_tokens": 5, "cached_tokens": 3,
        "cli_calls": 2,
    })
    monkeypatch.setattr(mgr, "_task_card_async_shell_snapshot", lambda: {
        "running": 1, "done": 0, "failed": 1, "cancelled": 1,
        "timeout": 0, "unknown": 0,
    })
    snapshot = mgr._task_card_async_work_snapshot()
    assert snapshot["running"] == 3
    assert snapshot["done"] == 1
    assert snapshot["failed"] == 1
    assert snapshot["cancelled"] == 1
    assert snapshot["unknown"] == 1
    assert "daemon" in snapshot and "shell" in snapshot


def test_daemon_real_schema_cli_ledger_not_shadowed_and_kernel_api_calls_use_tokens(tmp_path):
    import datetime as _datetime
    daemons = tmp_path / "daemons"
    daemons.mkdir()
    recent = (_datetime.datetime.now(_datetime.timezone.utc) - _datetime.timedelta(seconds=2)).isoformat()
    cli = daemons / "cli"
    cli.mkdir()
    (cli / "daemon.json").write_text(json.dumps({
        "state": "done", "backend": "codex", "finished_at": recent,
        "tokens": {"input": 0, "output": 0, "cached": 0},
        "cli_tokens": {"input": 100, "output": 40, "cached": 20, "calls": 3},
        "tool_call_count": 99,
    }), encoding="utf-8")
    kernel = daemons / "kernel"
    kernel.mkdir()
    (kernel / "daemon.json").write_text(json.dumps({
        "state": "running", "backend": "lingtai",
        "tokens": {"input": 9, "output": 4, "cached": 2, "calls": 4},
        "tool_call_count": 17,
    }), encoding="utf-8")
    from lingtai.kernel.daemon_dispatch import append_dispatch
    append_dispatch(tmp_path, run_id="cli", created_at="2026-08-20T00:00:00Z")
    append_dispatch(tmp_path, run_id="kernel", created_at="2026-08-20T00:00:01Z")
    mgr, _ = _integration_manager(tmp_path)
    snapshot = mgr._task_card_daemon_snapshot()
    assert snapshot["input_tokens"] == 109
    assert snapshot["output_tokens"] == 44
    assert snapshot["cached_tokens"] == 22
    # 3 external CLI calls + 4 LingTai API calls; tool_call_count 17 is not an API ledger.
    assert snapshot["cli_calls"] == 7
