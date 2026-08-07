"""Multi-row Task Card render: parallel/sequential rows + fixed footer.

Parallel tool calls appear as one row per call id (tool.action + reasoning +
own elapsed); a completed row is frozen with a done marker and its final
elapsed.  Both the running and the frozen last-behavior render carry the fixed
human warning footer, and redaction happens before any truncation so a secret
can never survive a length-pressure trim.
"""

from __future__ import annotations

from lingtai.mcp_servers.telegram.manager import (
    TelegramManager,
    _TASK_CARD_FOOTER,
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
    # Elapsed renders as whole seconds, no decimal point.
    assert "3s" in text
    assert "3.0s" not in text


def test_parallel_rows_all_represented_with_independent_elapsed():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 5, "done": False},
        {"tool": "read", "tool_action": "", "reasoning": "open file",
         "elapsed_s": 2, "done": False},
        {"tool": "grep", "tool_action": "", "reasoning": "scan",
         "elapsed_s": 8, "done": True},
    ])
    # Each row present with its own tool + whole-second elapsed.
    assert "bash.run" in text
    assert "read" in text
    assert "grep" in text
    assert "5s" in text
    assert "2s" in text
    assert "8s" in text


# ---------------------------------------------------------------------------
# Whole-second display rule (no decimal point)
# ---------------------------------------------------------------------------

def test_no_decimal_point_in_render():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 12, "done": False},
    ])
    assert "12s" in text
    # The elapsed suffix is whole-second, no decimal point in it.
    row_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    elapsed_suffix = row_line[row_line.rindex("("):]  # "(12s)"
    assert elapsed_suffix == "(12s)"
    assert "." not in elapsed_suffix


def test_float_elapsed_payload_is_floored_to_whole_second():
    """A float elapsed (e.g. from an in-flight value) is floored, not rounded,
    and shows no decimal — 8.99s displays 8s."""
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 8.99, "done": False},
    ])
    assert "8s" in text
    assert "8.99" not in text
    assert "9s" not in text


def test_zero_elapsed_renders_zero_seconds():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 0, "done": False},
    ])
    assert "0s" in text
    assert "0.0s" not in text


def test_done_row_elapsed_is_whole_second():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 7, "done": True},
    ])
    assert "7s" in text
    assert "7.0s" not in text


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
    metadata_lines = lines[footer_idx + 1:time_idx]
    assert metadata_lines == [
        "session · cache 87.8% · miss 170.6k/1.0M · calls 13",
        "ctx · 171.2k/272.0k · 63%",
    ]
    assert len(metadata_lines) <= 2
    assert len("\n".join(metadata_lines)) <= 150


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
    assert len(lines) == 2
    assert len("\n".join(lines)) <= 150
    assert "inf" not in "\n".join(lines).lower()


# ---------------------------------------------------------------------------
# Agent lifecycle/health in the automatic metadata footer
# ---------------------------------------------------------------------------

def test_metadata_renders_compact_normal_lifecycle_states():
    for state in ("active", "idle", "asleep", "suspended"):
        lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": state})
        assert lines == [f"agent · {state}"]
        assert "/refresh" not in lines[0]


def test_metadata_renders_stuck_with_refresh_hint():
    lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": "stuck"})
    assert lines == ["agent · stuck · try /refresh"]


def test_metadata_renders_offline_with_refresh_hint():
    lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": "offline"})
    assert lines == ["agent · offline · try /refresh"]


def test_metadata_suspended_never_gets_refresh_hint():
    lines = TelegramManager._format_task_card_metadata({"agent_lifecycle": "suspended"})
    assert lines == ["agent · suspended"]
    assert "/refresh" not in lines[0]


def test_metadata_ignores_unrecognized_lifecycle_value():
    lines = TelegramManager._format_task_card_metadata({
        "agent_lifecycle": "haunted",
        "session_cache_rate": 0.5,
    })
    assert lines == ["session · cache 50.0%"]


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
        "agent · active · session · cache 87.8% · miss 170.6k/1.0M · calls 13",
        "ctx · 171.2k/272.0k · 63%",
    ]
    assert len(lines) <= 2
    assert len("\n".join(lines)) <= 150


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
        "agent · stuck · try /refresh · session · cache 87.8% · miss 170.6k/1.0M · calls 13",
        "ctx · 171.2k/272.0k · 63%",
    ]
    assert lines[0].startswith("agent · stuck · try /refresh")
    assert len(lines) <= 2
    assert len("\n".join(lines)) <= 150


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
        "session · cache 87.8% · miss 170.6k/1.0M · calls 13",
        "ctx · 171.2k/272.0k · 63%",
    ]


# ---------------------------------------------------------------------------
# _task_card_agent_lifecycle_status — sources .status.json, degrades safely
# ---------------------------------------------------------------------------

def _write_status(tmp_path, payload):
    import json as _json
    (tmp_path / ".status.json").write_text(_json.dumps(payload), encoding="utf-8")


def test_lifecycle_status_missing_file_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_malformed_json_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    (tmp_path / ".status.json").write_text("{not json", encoding="utf-8")
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_non_object_json_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    (tmp_path / ".status.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_non_utf8_bytes_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    (tmp_path / ".status.json").write_bytes(b"\xff\xfe\x00\x01")
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_missing_runtime_block_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"identity": {}})
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_runtime_not_a_dict_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": "active"})
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_unrecognized_state_degrades_to_none(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "haunted"}})
    assert mgr._task_card_agent_lifecycle_status() is None


def test_lifecycle_status_suspended_never_checks_presence(tmp_path, monkeypatch):
    import lingtai.mcp_servers.telegram.manager as manager_mod

    def _boom(*a, **kw):
        raise AssertionError("suspended must not consult presence liveness")

    monkeypatch.setattr(manager_mod, "observe_alive", _boom)
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "suspended"}})
    assert mgr._task_card_agent_lifecycle_status() == "suspended"


def test_lifecycle_status_stuck_never_checks_presence(tmp_path, monkeypatch):
    import lingtai.mcp_servers.telegram.manager as manager_mod

    def _boom(*a, **kw):
        raise AssertionError("stuck must not consult presence liveness")

    monkeypatch.setattr(manager_mod, "observe_alive", _boom)
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "stuck"}})
    assert mgr._task_card_agent_lifecycle_status() == "stuck"


def test_lifecycle_status_active_trusts_fresh_presence(tmp_path, monkeypatch):
    import lingtai.mcp_servers.telegram.manager as manager_mod

    monkeypatch.setattr(manager_mod, "observe_alive", lambda *a, **kw: True)
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "active"}})
    assert mgr._task_card_agent_lifecycle_status() == "active"


def test_lifecycle_status_idle_with_stale_presence_becomes_offline(tmp_path, monkeypatch):
    import lingtai.mcp_servers.telegram.manager as manager_mod

    monkeypatch.setattr(manager_mod, "observe_alive", lambda *a, **kw: False)
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "idle"}})
    assert mgr._task_card_agent_lifecycle_status() == "offline"


def test_lifecycle_status_asleep_with_stale_presence_becomes_offline(tmp_path, monkeypatch):
    import lingtai.mcp_servers.telegram.manager as manager_mod

    monkeypatch.setattr(manager_mod, "observe_alive", lambda *a, **kw: False)
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "asleep"}})
    assert mgr._task_card_agent_lifecycle_status() == "offline"


def test_lifecycle_status_presence_error_falls_back_to_raw_state(tmp_path, monkeypatch):
    import lingtai.mcp_servers.telegram.manager as manager_mod

    def _boom(*a, **kw):
        raise OSError("presence adapter blew up")

    monkeypatch.setattr(manager_mod, "observe_alive", _boom)
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "idle"}})
    assert mgr._task_card_agent_lifecycle_status() == "idle"


# ---------------------------------------------------------------------------
# _task_card_event_metadata_snapshot — merges lifecycle without a new store
# ---------------------------------------------------------------------------

def test_event_metadata_snapshot_none_when_nothing_available(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    assert mgr._task_card_event_metadata_snapshot() is None


def test_event_metadata_snapshot_adds_lifecycle_alone(tmp_path, monkeypatch):
    import lingtai.mcp_servers.telegram.manager as manager_mod

    monkeypatch.setattr(manager_mod, "observe_alive", lambda *a, **kw: True)
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "active"}})
    assert mgr._task_card_event_metadata_snapshot() == {"agent_lifecycle": "active"}


def test_event_metadata_snapshot_merges_with_existing_session_metadata(tmp_path):
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "suspended"}})
    mgr._task_card_event_metadata = {"api_calls": 4}
    snapshot = mgr._task_card_event_metadata_snapshot()
    assert snapshot == {"api_calls": 4, "agent_lifecycle": "suspended"}
    # The manager's own stored metadata must not be mutated by the merge.
    assert mgr._task_card_event_metadata == {"api_calls": 4}


def test_metadata_renders_current_model_first():
    lines = TelegramManager._format_task_card_metadata({
        "model": "deepseek-v4-flash",
        "session_cache_rate": 0.5,
    })
    assert lines == ["session · model deepseek-v4-flash · cache 50.0%"]


def test_event_metadata_snapshot_adds_current_model(tmp_path):
    import json as _json

    (tmp_path / ".agent.json").write_text(
        _json.dumps({"llm": {"model": "deepseek-v4-flash"}}), encoding="utf-8"
    )
    mgr, _ = _integration_manager(tmp_path)
    _write_status(tmp_path, {"runtime": {"state": "active"}})
    snapshot = mgr._task_card_event_metadata_snapshot()
    assert snapshot == {"agent_lifecycle": "active", "model": "deepseek-v4-flash"}
