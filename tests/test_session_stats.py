"""Contract tests for the Agent Record / daemon self-record (kernel.session_stats).

Covers: atomic publication, schema/version/freshness, redaction, the
per-turn daemon self-record writer, bounded present-only aggregation, and the
migrated Telegram/local-commands consumer paths.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.kernel import _fsutil, session_stats
from lingtai.kernel.base_agent import BaseAgent
from lingtai.kernel.state import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, wall: float = 1000.0, mono: float = 100.0):
        self._wall = wall
        self._mono = mono

    def wall_seconds(self) -> float:
        return self._wall

    def monotonic_seconds(self) -> float:
        return self._mono


def _stub_agent(tmp_path: Path, **overrides) -> SimpleNamespace:
    defaults = dict(
        _agent_id="agent-123",
        agent_name="真名",
        nickname="小明",
        _mail_service=None,
        _lifecycle_clock=_FakeClock(),
        _started_at="2026-08-20T00:00:00Z",
        _uptime_anchor=90.0,
        _heartbeat=999.0,
        _molt_count=2,
        _chat=None,
        _config=SimpleNamespace(context_limit=None),
        _state=AgentState.ACTIVE,
        _working_dir=tmp_path,
        _last_api_call_at=995.0,
        _last_progress_at=990.0,
        service=None,
        get_token_usage=lambda: {
            "input_tokens": 10, "output_tokens": 5, "thinking_tokens": 1,
            "cached_tokens": 2, "total_tokens": 16, "api_calls": 3,
            "ctx_system_tokens": 100, "ctx_tools_tokens": 50,
            "ctx_history_tokens": 25, "ctx_total_tokens": 175,
        },
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _daemon_state(**overrides) -> dict:
    state = {
        "run_id": "em-abcd", "handle": "em-abcd", "group_id": "dg-1",
        "backend": "lingtai", "state": "running",
        "started_at": "2026-08-20T00:00:00Z", "finished_at": None,
        "turn": 3, "tool_call_count": 5,
        "tokens": {"input": 40, "output": 20, "thinking": 0, "cached": 5},
        "cli_tokens": {"input": 0, "output": 0, "thinking": 0, "cached": 0, "calls": 0},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Agent record — schema / atomic write / redaction
# ---------------------------------------------------------------------------


def test_agent_record_schema_version_and_generated_at(tmp_path):
    record = session_stats.build_agent_record(_stub_agent(tmp_path))
    assert record["schema"] == session_stats.AGENT_RECORD_SCHEMA
    assert record["schema_version"] == session_stats.AGENT_RECORD_VERSION
    assert record["generated_at"].endswith("Z")
    assert record["sequence"] == 0


def test_agent_record_never_contains_working_dir_path(tmp_path):
    secret_marker = str(tmp_path)
    record = session_stats.build_agent_record(_stub_agent(tmp_path))
    dumped = json.dumps(record)
    assert secret_marker not in dumped


def test_agent_record_identity_and_usage_fields(tmp_path):
    record = session_stats.build_agent_record(_stub_agent(tmp_path))
    assert record["identity"]["agent_id"] == "agent-123"
    assert record["identity"]["agent_name"] == "真名"
    assert record["identity"]["nickname"] == "小明"
    assert record["session"]["state"] == "active"
    assert record["session"]["molt_count"] == 2
    assert record["usage"]["input_tokens"] == 10
    assert record["usage"]["api_calls"] == 3
    assert record["usage"]["context_used_tokens"] == 175
    assert record["health"]["last_api_call_at"] == 995.0
    assert record["health"]["last_progress_at"] == 990.0


def test_agent_record_liveness_fresh_vs_stale(tmp_path):
    fresh = session_stats.build_agent_record(
        _stub_agent(tmp_path, _lifecycle_clock=_FakeClock(wall=1000.0), _heartbeat=999.0)
    )
    assert fresh["health"]["liveness"] == "fresh"

    stale = session_stats.build_agent_record(
        _stub_agent(tmp_path, _lifecycle_clock=_FakeClock(wall=1100.0), _heartbeat=999.0)
    )
    assert stale["health"]["liveness"] == "stale"

    unknown = session_stats.build_agent_record(_stub_agent(tmp_path, _heartbeat=0.0))
    assert unknown["health"]["liveness"] == "unknown"


def test_agent_record_extra_hook_merges_handles_and_integrations(tmp_path):
    agent = _stub_agent(tmp_path)
    agent._build_agent_record_extra = lambda: {
        "handles": {"telegram": "botuser"},
        "integrations": [{"name": "telegram", "transport": "stdio", "connected": True}],
    }
    record = session_stats.build_agent_record(agent)
    assert record["handles"] == {"telegram": "botuser"}
    assert record["integrations"] == [{"name": "telegram", "transport": "stdio", "connected": True}]


def test_agent_record_extra_hook_failure_is_swallowed(tmp_path):
    agent = _stub_agent(tmp_path)

    def _boom():
        raise RuntimeError("mcp registry unavailable")

    agent._build_agent_record_extra = _boom
    record = session_stats.build_agent_record(agent)
    assert record["handles"] == {}
    assert record["integrations"] == []


def test_write_agent_record_is_atomic(tmp_path, monkeypatch):
    replacements = []
    real_replace = _fsutil.os.replace

    def spy_replace(src, dst):
        replacements.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(_fsutil.os, "replace", spy_replace)

    record = session_stats.build_agent_record(_stub_agent(tmp_path))
    path = session_stats.write_agent_record(tmp_path, record)

    assert path == tmp_path / "system" / "agent_record.json"
    assert len(replacements) == 1
    temp_path, replaced_target = map(Path, replacements[0])
    assert temp_path.parent == path.parent
    assert replaced_target == path
    assert session_stats.read_agent_record(tmp_path) == record


def test_read_agent_record_missing_and_corrupt_return_none(tmp_path):
    assert session_stats.read_agent_record(tmp_path) is None
    path = session_stats.agent_record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert session_stats.read_agent_record(tmp_path) is None


# ---------------------------------------------------------------------------
# Published Agent Record lifecycle classification
# ---------------------------------------------------------------------------


def _published_agent_record(state: str, health: dict) -> dict:
    return {
        "schema": session_stats.AGENT_RECORD_SCHEMA,
        "schema_version": session_stats.AGENT_RECORD_VERSION,
        "session": {"state": state},
        "health": health,
    }


def test_published_agent_record_liveness_boundary_is_strict():
    heartbeat_at = 100.0
    record = _published_agent_record("active", {
        "heartbeat_at": heartbeat_at,
        "liveness": "fresh",
    })

    assert session_stats.classify_published_agent_record(
        record,
        wall_now=heartbeat_at + session_stats.HEARTBEAT_LIVENESS_SECONDS - 0.001,
    ) == "active"
    assert session_stats.classify_published_agent_record(
        record,
        wall_now=heartbeat_at + session_stats.HEARTBEAT_LIVENESS_SECONDS,
    ) == "offline"


@pytest.mark.parametrize("health", [
    {"liveness": "fresh"},
    {"heartbeat_at": float("nan"), "liveness": "fresh"},
    {"heartbeat_at": float("inf"), "liveness": "fresh"},
])
def test_published_agent_record_missing_or_nonfinite_heartbeat_is_offline(health):
    assert session_stats.classify_published_agent_record(
        _published_agent_record("idle", health), wall_now=100.0,
    ) == "offline"


@pytest.mark.parametrize("state", ["stuck", "suspended"])
def test_published_agent_record_keeps_explicit_terminal_state(state):
    assert session_stats.classify_published_agent_record(
        _published_agent_record(state, {"heartbeat_at": 0.0}), wall_now=10_000.0,
    ) == state


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (_published_agent_record("active", {"heartbeat_at": 100.0}), "active"),
        (_published_agent_record("idle", {"heartbeat_at": 90.0}), "offline"),
        (_published_agent_record("stuck", {"heartbeat_at": 0.0}), "stuck"),
        (_published_agent_record("suspended", {"heartbeat_at": 0.0}), "suspended"),
        ({}, "unavailable"),
    ],
)
def test_query_published_agent_liveness_is_the_complete_consumer_dict(record, expected):
    assert session_stats.query_published_agent_liveness(
        record,
        wall_now=100.0,
    ) == {"liveness": expected}


# ---------------------------------------------------------------------------
# should_refresh_agent_record / env var validation
# ---------------------------------------------------------------------------


def test_should_refresh_agent_record_first_write_always_refreshes():
    assert session_stats.should_refresh_agent_record(None, 100.0, 5.0) is True


def test_should_refresh_agent_record_throttles_within_window():
    assert session_stats.should_refresh_agent_record(100.0, 102.0, 5.0) is False
    assert session_stats.should_refresh_agent_record(100.0, 105.0, 5.0) is True


def test_should_refresh_agent_record_wall_clock_regression_refreshes():
    assert session_stats.should_refresh_agent_record(200.0, 100.0, 5.0) is True


@pytest.mark.parametrize("raw", [None, "", "  ", "not-a-number", "0", "-1", "nan", "inf"])
def test_session_stats_refresh_seconds_falls_back(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(session_stats.REFRESH_SECONDS_ENV, raising=False)
    else:
        monkeypatch.setenv(session_stats.REFRESH_SECONDS_ENV, raw)
    assert session_stats.session_stats_refresh_seconds() == session_stats.DEFAULT_REFRESH_SECONDS


def test_session_stats_refresh_seconds_accepts_valid_value(monkeypatch):
    monkeypatch.setenv(session_stats.REFRESH_SECONDS_ENV, "12.5")
    assert session_stats.session_stats_refresh_seconds() == 12.5


@pytest.mark.parametrize("raw", [None, "", "  ", "not-an-int", "0", "-5", "3.5"])
def test_session_stats_daemon_limit_falls_back(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(session_stats.DAEMON_LIMIT_ENV, raising=False)
    else:
        monkeypatch.setenv(session_stats.DAEMON_LIMIT_ENV, raw)
    assert session_stats.session_stats_daemon_limit() == session_stats.DEFAULT_DAEMON_LIMIT


def test_session_stats_daemon_limit_accepts_valid_value(monkeypatch):
    monkeypatch.setenv(session_stats.DAEMON_LIMIT_ENV, "7")
    assert session_stats.session_stats_daemon_limit() == 7


# ---------------------------------------------------------------------------
# BaseAgent._write_session_stats_record — throttled, best-effort hook
# ---------------------------------------------------------------------------


def test_write_session_stats_record_writes_on_first_call(tmp_path):
    agent = _stub_agent(tmp_path)
    agent._session_stats_last_written_at = None
    agent._session_stats_sequence = 0

    BaseAgent._write_session_stats_record(agent)

    record = session_stats.read_agent_record(tmp_path)
    assert record is not None
    assert record["sequence"] == 1
    assert agent._session_stats_last_written_at == 1000.0


def test_write_session_stats_record_publishes_last_complete_async_work_pair(tmp_path, monkeypatch):
    async_work = session_stats.build_async_work_snapshot(tmp_path, now_epoch=1_000.0)
    assert async_work is not None
    daemon_summary = {
        "source": "dispatch_ledger", "present": 0, "scanned": 0,
        "limit": 1000, "counts_by_state": {}, "usage": {},
        "checked": {}, "warnings": [], "refreshing": False,
    }

    class _Owner:
        def __init__(self, *args, **kwargs):
            pass

        def schedule(self):
            return True

        def snapshot(self):
            return {"daemons": daemon_summary, "async_work": async_work}

    monkeypatch.setattr(session_stats, "RecentAsyncWorkSnapshot", _Owner)
    agent = _stub_agent(tmp_path)
    agent._session_stats_last_written_at = None
    agent._session_stats_sequence = 0
    agent._async_work_snapshot = None

    BaseAgent._write_session_stats_record(agent)

    record = session_stats.read_agent_record(tmp_path)
    assert record is not None
    assert record["daemons"] == daemon_summary
    assert record["async_work"] == async_work


def test_write_session_stats_record_throttled_within_window(tmp_path, monkeypatch):
    monkeypatch.setenv(session_stats.REFRESH_SECONDS_ENV, "5")
    agent = _stub_agent(tmp_path, _lifecycle_clock=_FakeClock(wall=1000.0))
    agent._session_stats_last_written_at = 998.0
    agent._session_stats_sequence = 0

    BaseAgent._write_session_stats_record(agent)

    assert session_stats.read_agent_record(tmp_path) is None
    assert agent._session_stats_sequence == 0


def test_write_session_stats_record_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    agent = _stub_agent(tmp_path)
    agent._session_stats_last_written_at = None
    agent._session_stats_sequence = 0

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(session_stats, "write_agent_record", _boom)
    with caplog.at_level(logging.WARNING):
        BaseAgent._write_session_stats_record(agent)  # must not raise
    assert "Failed to write agent record" in caplog.text


# ---------------------------------------------------------------------------
# Ledger-selected daemon aggregation / background snapshot
# ---------------------------------------------------------------------------


def _write_ledger_daemon(working_dir: Path, run_id: str, **overrides) -> None:
    from lingtai.kernel.daemon_dispatch import append_dispatch

    run_dir = working_dir / "daemons" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = _daemon_state(run_id=run_id, **overrides)
    (run_dir / "daemon.json").write_text(json.dumps(state), encoding="utf-8")
    append_dispatch(working_dir, run_id=run_id, created_at="2026-08-20T00:00:00Z")


def test_aggregate_daemon_records_empty_ledger_is_honest_and_diagnostic(tmp_path):
    agg = session_stats.aggregate_daemon_records(tmp_path)
    assert agg["source"] == "dispatch_ledger"
    assert agg["present"] == 0
    assert agg["scanned"] == 0
    assert agg["checked"]["source"] == "tail"
    assert [warning["code"] for warning in agg["warnings"]] == ["dispatch_ledger_empty"]


def test_aggregate_daemon_records_none_working_dir_is_honest_zero():
    agg = session_stats.aggregate_daemon_records(None)
    assert agg["present"] == 0
    assert agg["scanned"] == 0


def test_aggregate_uses_only_ledger_selected_daemon_json(tmp_path):
    # Legacy or foreign directories are intentionally invisible without a
    # ledger record; no fallback directory scan/backfill is permitted.
    legacy = tmp_path / "daemons" / "em-legacy"
    legacy.mkdir(parents=True)
    (legacy / "daemon.json").write_text(json.dumps(_daemon_state(run_id="em-legacy", state="running")))
    _write_ledger_daemon(tmp_path, "em-fresh", state="done")

    agg = session_stats.aggregate_daemon_records(tmp_path)
    assert agg["present"] == 1
    assert agg["scanned"] == 1
    assert agg["counts_by_state"] == {"done": 1}
    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=2_000.0)
    assert snapshot is not None
    assert snapshot["daemon"]["running"] == 0


def test_aggregate_sums_usage_across_ledger_selected_states(tmp_path):
    _write_ledger_daemon(
        tmp_path, "em-1", state="running",
        tokens={"input": 10, "output": 5, "thinking": 0, "cached": 0},
        cli_tokens={"input": 0, "output": 0, "thinking": 0, "cached": 0, "calls": 0},
    )
    _write_ledger_daemon(
        tmp_path, "em-2", state="done",
        tokens={"input": 0, "output": 0, "thinking": 0, "cached": 0},
        cli_tokens={"input": 20, "output": 8, "thinking": 0, "cached": 2, "calls": 3},
    )

    agg = session_stats.aggregate_daemon_records(tmp_path)
    assert agg["present"] == 2
    assert agg["scanned"] == 2
    assert agg["counts_by_state"] == {"running": 1, "done": 1}
    assert agg["usage"] == {
        "input_tokens": 30, "output_tokens": 13, "thinking_tokens": 0,
        "cached_tokens": 2, "api_calls": 3,
    }


def test_aggregate_tails_ledger_append_order_without_directory_sort(tmp_path):
    for i in range(5):
        _write_ledger_daemon(tmp_path, f"em-{i}", state=f"state-{i}")

    agg = session_stats.aggregate_daemon_records(tmp_path, limit=2)
    assert agg["present"] == 2
    assert agg["scanned"] == 2
    assert agg["checked"]["sequence_from"] == 4
    assert agg["checked"]["sequence_to"] == 5
    assert agg["counts_by_state"] == {"state-3": 1, "state-4": 1}


def test_aggregate_reports_missing_or_corrupt_selected_state(tmp_path):
    _write_ledger_daemon(tmp_path, "em-good")
    from lingtai.kernel.daemon_dispatch import append_dispatch
    append_dispatch(tmp_path, run_id="em-missing", created_at="2026-08-20T00:00:00Z")

    agg = session_stats.aggregate_daemon_records(tmp_path)
    assert agg["present"] == 2
    assert agg["scanned"] == 1
    assert [warning["code"] for warning in agg["warnings"]] == ["dispatch_ledger_daemon_state_unreadable"]


def _write_shell_job(working_dir: Path, job_id: str, state: dict) -> None:
    job_dir = working_dir / "system" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def test_async_work_snapshot_mixes_daemon_and_shell_lanes_and_scopes_usage(tmp_path):
    now = 2_000.0
    _write_ledger_daemon(
        tmp_path,
        "em-running",
        state="running",
        backend="lingtai",
        model="gpt-5.6",
        tokens={"input": 10, "output": 5, "thinking": 2, "cached": 4, "calls": 3},
    )
    _write_ledger_daemon(
        tmp_path,
        "em-done",
        state="done",
        backend="codex",
        finished_at=_iso(now - 1),
        tokens={"input": 999, "output": 999, "thinking": 999, "cached": 999},
        cli_tokens={"input": 20, "output": 8, "thinking": 1, "cached": 2, "calls": 4},
    )
    _write_shell_job(tmp_path, "job-queued", {"status": "launching"})
    _write_shell_job(tmp_path, "job-running", {"status": "running"})
    _write_shell_job(
        tmp_path,
        "job-failed",
        {"status": "completed", "finished_at": now - 1,
         "exit_status_known": True, "exit_code": 7},
    )

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=now)

    assert snapshot == {
        "schema": "lingtai.async_work/v1",
        "schema_version": 1,
        "generated_at": "1970-01-01T00:33:20Z",
        "window_seconds": 600,
        "running": 2,
        "queued": 1,
        "done": 1,
        "failed": 1,
        "daemon": {
            "running": 1,
            "queued": 0,
            "done": 1,
            "failed": 0,
            "backend_counts": {"lingtai": 1, "codex": 1},
            "model_counts": {"gpt-5.6": 1},
            "usage": {
                "input_tokens": 30,
                "output_tokens": 13,
                "thinking_tokens": 3,
                "cached_tokens": 6,
                "api_calls": 7,
            },
        },
        "shell": {"running": 1, "queued": 1, "done": 0, "failed": 1},
    }
    # There is intentionally no aggregate usage/backend/model block.
    assert "usage" not in snapshot
    assert "backend_counts" not in snapshot
    assert "model_counts" not in snapshot


def test_async_work_terminal_window_includes_exact_600_and_excludes_beyond(tmp_path):
    now = 2_000.0
    _write_ledger_daemon(
        tmp_path, "em-edge", state="done", finished_at=_iso(now - 600)
    )
    _write_ledger_daemon(
        tmp_path, "em-old", state="failed", finished_at=_iso(now - 600.001)
    )
    _write_shell_job(
        tmp_path,
        "job-edge",
        {"status": "completed", "finished_at": now - 600,
         "exit_status_known": True, "exit_code": 0},
    )
    _write_shell_job(
        tmp_path,
        "job-old",
        {"status": "completed", "finished_at": now - 600.001,
         "exit_status_known": True, "exit_code": 9},
    )

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=now)

    assert snapshot is not None
    assert snapshot["daemon"]["done"] == 1
    assert snapshot["daemon"]["failed"] == 0
    assert snapshot["shell"]["done"] == 1
    assert snapshot["shell"]["failed"] == 0
    assert snapshot["done"] == 2
    assert snapshot["failed"] == 0


def test_async_work_malformed_states_are_skipped_without_invented_counts(tmp_path):
    _write_ledger_daemon(tmp_path, "em-malformed", state="mystery")
    _write_shell_job(
        tmp_path,
        "job-malformed",
        {"status": "completed", "finished_at": 1_999.0,
         "exit_status_known": False, "exit_code": 0},
    )

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=2_000.0)

    assert snapshot is not None
    assert {key: snapshot[key] for key in session_stats.ASYNC_WORK_STATUS_KEYS} == {
        "running": 0, "queued": 0, "done": 0, "failed": 0,
    }


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        pytest.param(
            {"status": "completed", "cancellation_outcome": "group_cancelled"},
            "failed",
            id="group-cancelled",
        ),
        pytest.param({"status": "unrecoverable"}, "failed", id="unrecoverable"),
        pytest.param(
            {
                "status": "completed", "finished_at": 1_999.0,
                "exit_status_known": True, "exit_code": False,
            },
            None,
            id="bool-exit-code-invalid",
        ),
        pytest.param(
            {
                "status": "completed", "finished_at": float("nan"),
                "exit_status_known": True, "exit_code": 0,
            },
            None,
            id="nonfinite-finished-at-nan",
        ),
        pytest.param(
            {
                "status": "completed", "finished_at": float("inf"),
                "exit_status_known": True, "exit_code": 0,
            },
            None,
            id="nonfinite-finished-at-inf",
        ),
        pytest.param(
            {
                "status": "completed", "finished_at": float("-inf"),
                "exit_status_known": True, "exit_code": 0,
            },
            None,
            id="nonfinite-finished-at-neg-inf",
        ),
        pytest.param(
            {"status": "running", "cancel_requested_at": 1_999.0},
            "running",
            id="running-cancellation-requested",
        ),
    ],
)
def test_async_work_shell_classification_regressions(tmp_path, state, expected):
    now = 2_000.0
    durable_state = dict(state)
    if expected in {"done", "failed"}:
        durable_state.setdefault("finished_at", now - 1)
    _write_shell_job(tmp_path, "job-case", durable_state)

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=now)

    assert snapshot is not None
    expected_lane = {key: 0 for key in session_stats.ASYNC_WORK_STATUS_KEYS}
    if expected is not None:
        expected_lane[expected] = 1
    assert snapshot["shell"] == expected_lane
    assert {
        key: snapshot[key] for key in session_stats.ASYNC_WORK_STATUS_KEYS
    } == expected_lane


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        pytest.param("active", "running", id="active"),
        pytest.param("queued", "queued", id="queued"),
        pytest.param("pending", "queued", id="pending"),
        pytest.param("cancelled", "failed", id="cancelled"),
        pytest.param("timeout", "failed", id="timeout"),
    ],
)
def test_async_work_daemon_classification_regressions(tmp_path, state, expected):
    now = 2_000.0
    overrides = {"state": state}
    if expected in {"done", "failed"}:
        overrides["finished_at"] = _iso(now - 1)
    _write_ledger_daemon(tmp_path, "em-case", **overrides)

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=now)

    assert snapshot is not None
    expected_lane = {key: 0 for key in session_stats.ASYNC_WORK_STATUS_KEYS}
    expected_lane[expected] = 1
    assert {
        key: snapshot["daemon"][key]
        for key in session_stats.ASYNC_WORK_STATUS_KEYS
    } == expected_lane


@pytest.mark.parametrize(
    "malformed_state",
    [
        pytest.param([], id="list"),
        pytest.param({}, id="dict"),
        pytest.param(True, id="bool"),
        pytest.param(7, id="number"),
        pytest.param(None, id="null"),
    ],
)
def test_async_work_non_string_daemon_state_does_not_abort_healthy_lanes(
    tmp_path, malformed_state,
):
    _write_ledger_daemon(tmp_path, "em-valid", state="active")
    _write_ledger_daemon(tmp_path, "em-malformed", state=malformed_state)
    _write_shell_job(tmp_path, "job-valid", {"status": "running"})

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=2_000.0)

    assert snapshot is not None
    assert snapshot["daemon"]["running"] == 1
    assert snapshot["shell"]["running"] == 1
    assert snapshot["running"] == 2
    assert all(snapshot[key] == 0 for key in ("queued", "done", "failed"))


@pytest.mark.parametrize(
    ("backend", "tokens", "cli_tokens", "expected"),
    [
        pytest.param(
            None,
            {"input": 1, "output": 2, "thinking": 3, "cached": 4, "calls": 1},
            {"input": 10, "output": 20, "thinking": 30, "cached": 40, "calls": 5},
            {"input_tokens": 10, "output_tokens": 20, "thinking_tokens": 30,
             "cached_tokens": 40, "api_calls": 5},
            id="backendless-prefers-nonzero-cli",
        ),
        pytest.param(
            None,
            {"input": 11, "output": 12, "thinking": 13, "cached": 14},
            {"input": 0, "output": 0, "thinking": 0, "cached": 0, "calls": 0},
            {"input_tokens": 11, "output_tokens": 12, "thinking_tokens": 13,
             "cached_tokens": 14, "api_calls": 0},
            id="backendless-falls-back-to-tokens",
        ),
        pytest.param(
            "claude-p",
            {"input": 31, "output": 32, "thinking": 33, "cached": 34, "calls": 7},
            {},
            {"input_tokens": 31, "output_tokens": 32, "thinking_tokens": 33,
             "cached_tokens": 34, "api_calls": 7},
            id="external-falls-back-to-tokens",
        ),
    ],
)
def test_async_work_daemon_usage_selects_only_token_ledgers(
    tmp_path, backend, tokens, cli_tokens, expected,
):
    _write_ledger_daemon(
        tmp_path,
        "em-usage",
        backend=backend,
        tokens=tokens,
        cli_tokens=cli_tokens,
        tool_call_count=999,
    )
    if backend is None:
        state_path = tmp_path / "daemons" / "em-usage" / "daemon.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("backend")
        state_path.write_text(json.dumps(state), encoding="utf-8")

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=2_000.0)

    assert snapshot is not None
    assert snapshot["daemon"]["usage"] == expected
    assert snapshot["daemon"]["usage"]["api_calls"] != 999


def test_async_work_empty_daemon_ledger_and_shell_io_failure_preserve_truth(
    tmp_path, monkeypatch,
):
    (tmp_path / "system" / "jobs" / "legacy").mkdir(parents=True)
    _write_shell_job(tmp_path, "job-running", {"status": "running"})
    state_path = tmp_path / "system" / "jobs" / "job-running" / "state.json"
    source_bytes = state_path.read_bytes()
    source_mtime_ns = state_path.stat().st_mtime_ns
    monkeypatch.setattr(session_stats.time, "time", lambda: 1_000.0)
    owner = session_stats.RecentAsyncWorkSnapshot(tmp_path)
    owner._refresh()
    assert state_path.read_bytes() == source_bytes
    assert state_path.stat().st_mtime_ns == source_mtime_ns
    before = owner.snapshot()["async_work"]
    assert before is not None
    assert before["generated_at"] == "1970-01-01T00:16:40Z"
    assert before["running"] == 1
    assert {
        key: before["daemon"][key]
        for key in session_stats.ASYNC_WORK_STATUS_KEYS
    } == {key: 0 for key in session_stats.ASYNC_WORK_STATUS_KEYS}
    assert before["shell"] == {
        "running": 1, "queued": 0, "done": 0, "failed": 0,
    }

    real_read_text = Path.read_text

    def fail_selected_shell_state(path, *args, **kwargs):
        if path == state_path:
            raise PermissionError("selected Shell state denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_selected_shell_state)
    monkeypatch.setattr(session_stats.time, "time", lambda: 1_100.0)
    assert session_stats.build_async_work_snapshot(
        tmp_path, now_epoch=1_100.0
    ) is None
    owner._refresh()

    after = owner.snapshot()["async_work"]
    assert after == before
    assert after["generated_at"] == "1970-01-01T00:16:40Z"
    assert after["running"] == 1


def test_async_work_daemon_reader_warning_retains_prior_snapshot(tmp_path, monkeypatch):
    from lingtai.kernel.daemon_dispatch import ledger_path, read_recent_daemon_states

    _write_ledger_daemon(tmp_path, "em-running", state="running")
    _write_shell_job(tmp_path, "job-running", {"status": "running"})
    monkeypatch.setattr(session_stats.time, "time", lambda: 1_000.0)
    owner = session_stats.RecentAsyncWorkSnapshot(tmp_path)
    owner._refresh()
    before = owner.snapshot()["async_work"]
    assert before is not None
    assert before["running"] == 2

    daemon_path = tmp_path / "daemons" / "em-running" / "daemon.json"
    daemon_path.write_text(
        json.dumps(_daemon_state(run_id="em-running", state="queued")),
        encoding="utf-8",
    )
    _write_shell_job(tmp_path, "job-running", {"status": "launching"})
    with ledger_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")
    _, _, warnings = read_recent_daemon_states(tmp_path)
    assert [warning["code"] for warning in warnings] == [
        "dispatch_ledger_invalid_record"
    ]

    monkeypatch.setattr(session_stats.time, "time", lambda: 1_100.0)
    assert session_stats.build_async_work_snapshot(
        tmp_path, now_epoch=1_100.0
    ) is None
    owner._refresh()

    after = owner.snapshot()["async_work"]
    assert after == before
    assert after["generated_at"] == "1970-01-01T00:16:40Z"
    assert after["running"] == 2
    assert after["queued"] == 0


def _published_with_async_work(snapshot: object) -> dict:
    return {
        "schema": session_stats.AGENT_RECORD_SCHEMA,
        "schema_version": session_stats.AGENT_RECORD_VERSION,
        "async_work": snapshot,
    }


def test_published_async_work_missing_malformed_and_stale_fail_safely(tmp_path):
    valid = session_stats.build_async_work_snapshot(tmp_path, now_epoch=1_000.0)
    assert valid is not None
    assert session_stats.query_published_async_work(
        _published_with_async_work(valid), wall_now=1_600.0
    ) == valid
    assert session_stats.query_published_async_work(
        _published_with_async_work(valid), wall_now=1_600.001
    ) is None
    fractional = session_stats.build_async_work_snapshot(
        tmp_path, now_epoch=1_000.75
    )
    assert fractional is not None
    assert fractional["generated_at"].endswith(".750000Z")
    assert session_stats.query_published_async_work(
        _published_with_async_work(fractional), wall_now=1_600.75
    ) == fractional
    assert session_stats.query_published_async_work(
        _published_with_async_work(fractional), wall_now=1_600.751
    ) is None
    assert session_stats.query_published_async_work({}, wall_now=1_000.0) is None
    old_record = {
        "schema": session_stats.AGENT_RECORD_SCHEMA,
        "schema_version": session_stats.AGENT_RECORD_VERSION,
    }
    assert session_stats.query_published_async_work(
        old_record, wall_now=1_000.0
    ) is None

    malformed = json.loads(json.dumps(valid))
    malformed["running"] = 99
    assert session_stats.query_published_async_work(
        _published_with_async_work(malformed), wall_now=1_000.0
    ) is None
    malformed = json.loads(json.dumps(valid))
    malformed["shell"]["done"] = "1"
    assert session_stats.query_published_async_work(
        _published_with_async_work(malformed), wall_now=1_000.0
    ) is None


def test_agent_record_attaches_only_completed_async_work_snapshot(tmp_path):
    without = session_stats.build_agent_record(_stub_agent(tmp_path))
    assert "async_work" not in without

    snapshot = session_stats.build_async_work_snapshot(tmp_path, now_epoch=1_000.0)
    record = session_stats.build_agent_record(
        _stub_agent(tmp_path), async_work_snapshot=snapshot
    )
    assert record["async_work"] == snapshot
    assert record["async_work"] is not snapshot


def test_recent_async_work_snapshot_single_flight_and_nonblocking(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_aggregate(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {"present": 1, "scanned": 1, "limit": 1000, "counts_by_state": {}, "usage": {}}

    async_work = session_stats.build_async_work_snapshot(tmp_path, now_epoch=1000.0)
    assert async_work is not None
    monkeypatch.setattr(session_stats, "aggregate_daemon_records", slow_aggregate)
    monkeypatch.setattr(
        session_stats, "build_async_work_snapshot", lambda *args, **kwargs: async_work
    )
    owner = session_stats.RecentAsyncWorkSnapshot(tmp_path)
    started = time.monotonic()
    assert owner.schedule() is True
    assert owner.schedule() is False
    assert time.monotonic() - started < 0.2
    assert entered.wait(1)
    assert owner.snapshot()["daemons"]["refreshing"] is True
    assert owner.snapshot()["async_work"] is None
    release.set()
    deadline = time.monotonic() + 1
    while owner.snapshot()["daemons"]["refreshing"] and time.monotonic() < deadline:
        time.sleep(0.01)
    snapshot = owner.snapshot()
    assert snapshot["daemons"]["present"] == 1
    assert snapshot["daemons"]["refreshing"] is False
    assert snapshot["async_work"] == async_work


def test_recent_async_work_snapshot_partial_failure_does_not_freeze_daemons(
    tmp_path, monkeypatch,
):
    async_work = session_stats.build_async_work_snapshot(
        tmp_path, now_epoch=1_000.0
    )
    assert async_work is not None
    owner = session_stats.RecentAsyncWorkSnapshot(tmp_path)

    monkeypatch.setattr(
        session_stats,
        "aggregate_daemon_records",
        lambda *args, **kwargs: {"present": 1},
    )
    monkeypatch.setattr(
        session_stats,
        "build_async_work_snapshot",
        lambda *args, **kwargs: async_work,
    )
    owner._refresh()

    monkeypatch.setattr(
        session_stats,
        "aggregate_daemon_records",
        lambda *args, **kwargs: {"present": 2},
    )
    def broken_async_work(*args, **kwargs):
        raise OSError("unreadable Shell state")

    monkeypatch.setattr(
        session_stats,
        "build_async_work_snapshot",
        broken_async_work,
    )
    owner._refresh()

    snapshot = owner.snapshot()
    assert snapshot["daemons"] == {"present": 2, "refreshing": False}
    assert snapshot["async_work"] == async_work
