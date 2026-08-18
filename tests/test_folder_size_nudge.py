from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import pytest

from lingtai.kernel import nudge as nudge_mod
from lingtai.kernel.nudge import ENTRY_CHANNEL_STORAGE_SIZE, run_checks
from lingtai.kernel.nudge import folder_size
from lingtai.kernel.nudge.folder_size import (
    DEFAULT_LIMIT_GB,
    LIMIT_ENV,
    _dir_size,
    _read_limit_gb,
    _today_utc,
)
from tests._notification_store_helpers import notification_store_for, snapshot_notifications

CHUNK = 2 * 1024 * 1024  # 2 MiB
TINY_LIMIT = "0.0001"  # 100_000 decimal bytes
_POLL = threading.Event()


class _Agent:
    def __init__(self, workdir):
        self._working_dir = workdir
        self._notification_store = notification_store_for(workdir)
        self._notification_fp = ()
        self.logs = []

    def _log(self, event, **fields):
        self.logs.append((event, fields))


@pytest.fixture
def over_agent(monkeypatch, tmp_path):
    (tmp_path / "chunk.bin").write_bytes(b"z" * CHUNK)
    monkeypatch.setenv(LIMIT_ENV, TINY_LIMIT)
    return _Agent(tmp_path)


def _entries(workdir):
    return snapshot_notifications(workdir).get("nudge", {}).get("data", {}).get("nudges", [])


def _state(workdir):
    path = Path(workdir) / ".notification" / ".nudge_state.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _state_file(workdir):
    return Path(workdir) / ".notification" / ".nudge_state.json"


def _force_next_walk(workdir):
    data = _state(workdir)
    data.setdefault("folder_size", {})["last_check_date"] = "2000-01-01"
    _state_file(workdir).parent.mkdir(parents=True, exist_ok=True)
    _state_file(workdir).write_text(json.dumps(data), encoding="utf-8")


def _call_until(call, ready, *, timeout=1.0):
    """Drive heartbeats until their observable result arrives."""
    deadline = time.monotonic() + timeout
    while True:
        call()
        if ready():
            return
        remaining = deadline - time.monotonic()
        assert remaining > 0, "heartbeat did not publish the expected result"
        _POLL.wait(min(0.01, remaining))


def _check_until(agent, ready, *, timeout=1.0):
    _call_until(lambda: folder_size.check(agent), ready, timeout=timeout)


def _run_checks_until(agent, ready, *, timeout=1.0):
    _call_until(lambda: run_checks(agent), ready, timeout=timeout)


def _check_until_raises(agent, error, *, timeout=1.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            folder_size.check(agent)
        except error:
            return
        remaining = deadline - time.monotonic()
        assert remaining > 0, "heartbeat did not raise the expected error"
        _POLL.wait(min(0.01, remaining))


def _current_folder_state(workdir):
    return _state(workdir).get("folder_size", {}).get("last_check_date") == _today_utc()


def _assert_fast_check(agent):
    started = time.monotonic()
    folder_size.check(agent)
    assert time.monotonic() - started <= 0.5


def test_limit_env_default_invalid_and_non_finite(monkeypatch):
    monkeypatch.delenv(LIMIT_ENV, raising=False)
    assert _read_limit_gb() == (DEFAULT_LIMIT_GB, None)
    for good in ("10", "0.5"):
        monkeypatch.setenv(LIMIT_ENV, good)
        assert _read_limit_gb() == (float(good), None)
    for bad in ("wat", "0", "-3", "nan", "inf", "-inf", "1e309"):
        monkeypatch.setenv(LIMIT_ENV, bad)
        value, invalid = _read_limit_gb()
        assert value == DEFAULT_LIMIT_GB and invalid == bad
        assert math.isfinite(value)


def test_dir_size_sums_files_and_skips_symlinks(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"x" * 100)
    (tmp_path / "link-file").symlink_to(tmp_path / "a.txt")
    (tmp_path / "link-dir").symlink_to(sub, target_is_directory=True)
    assert _dir_size(tmp_path) == 105


def test_under_limit_noop_and_clears(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "small.bin").write_bytes(b"y" * 1024)
    monkeypatch.setenv(LIMIT_ENV, "5")
    _check_until(agent, lambda: _current_folder_state(tmp_path))
    assert _entries(tmp_path) == []
    nudge_mod.upsert(agent, "folder_size", {"title": "stale", "detail": "old"})
    _check_until(agent, lambda: _entries(tmp_path) == [])
    assert _entries(tmp_path) == []


def test_over_limit_emits_facts(over_agent, tmp_path):
    _check_until(over_agent, lambda: bool(_entries(tmp_path)))
    entry = _entries(tmp_path)[0]
    assert entry["kind"] == "folder_size"
    assert entry["nudge_channel"] == ENTRY_CHANNEL_STORAGE_SIZE
    assert entry["size_bytes"] == CHUNK
    assert entry["limit_gb"] == float(TINY_LIMIT)
    assert entry["local_path"] == str(tmp_path)
    assert "does not authorize deletion" in entry["detail"]
    assert "owner/human authorization" in entry["detail"]


def test_no_non_finite_reaches_state_or_notification(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * CHUNK)
    monkeypatch.setenv(LIMIT_ENV, "nan")
    _check_until(agent, lambda: _current_folder_state(tmp_path))
    state = _state(tmp_path)["folder_size"]
    assert state["limit_gb"] == DEFAULT_LIMIT_GB
    for entry in _entries(tmp_path):
        assert all(math.isfinite(entry[k]) for k in ("limit_gb", "size_bytes", "size_gb"))


def test_decimal_gb_boundary(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv(LIMIT_ENV, TINY_LIMIT)
    (tmp_path / "at-limit.bin").write_bytes(b"a" * 100_000)
    _check_until(agent, lambda: _current_folder_state(tmp_path))
    assert _entries(tmp_path) == []
    (tmp_path / "over.bin").write_bytes(b"b" * 1)
    _force_next_walk(tmp_path)
    _check_until(agent, lambda: bool(_entries(tmp_path)))
    assert len(_entries(tmp_path)) == 1


def test_walk_gate_skips_second_walk_same_day(over_agent, tmp_path):
    _check_until(over_agent, lambda: _current_folder_state(tmp_path))
    first = _state(tmp_path)["folder_size"]["size_bytes"]
    (tmp_path / "more.bin").write_bytes(b"q" * (3 * 1024 * 1024))
    folder_size.check(over_agent)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first
    assert len(_entries(tmp_path)) == 1


def test_global_repeat_expiry_reappears_same_day(over_agent, tmp_path, monkeypatch):
    _check_until(over_agent, lambda: bool(_entries(tmp_path)))
    first = _state(tmp_path)["folder_size"]["size_bytes"]
    monkeypatch.setenv("LINGTAI_NUDGE_REPEAT_INTERVAL", "2s")
    nudge_mod.record_dismissal(over_agent)
    calls: list[str] = []
    real = nudge_mod.upsert

    def spy(a, k, b):
        calls.append(k)
        return real(a, k, b)

    monkeypatch.setattr(nudge_mod, "upsert", spy)
    folder_size.check(over_agent)
    assert "folder_size" in calls
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first
    data = _state(tmp_path)
    for e in data.get("dismissed", {}).values():
        e["until"] = 1_000_000.0
    _state_file(tmp_path).write_text(json.dumps(data), encoding="utf-8")
    folder_size.check(over_agent)
    assert len(_entries(tmp_path)) == 1


def test_enable_off_on_restores_same_day(over_agent, tmp_path, monkeypatch):
    _check_until(over_agent, lambda: bool(_entries(tmp_path)))
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "off")
    _run_checks_until(over_agent, lambda: _entries(tmp_path) == [])
    assert _entries(tmp_path) == []
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "on")
    _run_checks_until(over_agent, lambda: bool(_entries(tmp_path)))
    assert len(_entries(tmp_path)) == 1


def test_transient_upsert_failure_retried_same_day(over_agent, tmp_path, monkeypatch):
    real = nudge_mod.upsert

    def fail(a, k, b):
        raise RuntimeError("transient store failure")

    monkeypatch.setattr(nudge_mod, "upsert", fail)
    _check_until_raises(over_agent, RuntimeError)
    first = _state(tmp_path)["folder_size"]["size_bytes"]
    monkeypatch.setattr(nudge_mod, "upsert", real)
    _check_until(over_agent, lambda: bool(_entries(tmp_path)))
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first
    assert len(_entries(tmp_path)) == 1


def test_run_checks_dispatches(over_agent, tmp_path, monkeypatch):
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "on")
    _run_checks_until(over_agent, lambda: bool(_entries(tmp_path)))
    assert any(e["kind"] == "folder_size" for e in _entries(tmp_path))


def test_reprobe_next_day_clears(over_agent, tmp_path):
    _check_until(over_agent, lambda: bool(_entries(tmp_path)))
    assert len(_entries(tmp_path)) == 1
    (tmp_path / "chunk.bin").unlink()
    _force_next_walk(tmp_path)
    _check_until(over_agent, lambda: _current_folder_state(tmp_path) and _entries(tmp_path) == [])
    assert _entries(tmp_path) == []


def test_today_utc_format():
    v = _today_utc()
    assert len(v) == 10 and v[4] == "-" and v[7] == "-"


def test_blocking_probe_returns_and_is_single_flight_until_a_heartbeat_consumes_it(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv(LIMIT_ENV, TINY_LIMIT)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def blocked_probe(path):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(3.0)
        return CHUNK

    monkeypatch.setattr(folder_size, "_dir_size", blocked_probe)
    try:
        _assert_fast_check(agent)
        assert started.wait(1.0)
        _assert_fast_check(agent)
        assert calls == 1
        release.set()
        _check_until(agent, lambda: bool(_entries(tmp_path)) and _current_folder_state(tmp_path))
    finally:
        release.set()


def test_blocked_workdir_probe_does_not_delay_another_workdir(monkeypatch, tmp_path):
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = _Agent(first_path)
    second = _Agent(second_path)
    monkeypatch.setenv(LIMIT_ENV, TINY_LIMIT)
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()

    def probe(path):
        if path == first_path:
            first_started.set()
            assert release.wait(3.0)
        else:
            second_started.set()
        return CHUNK

    monkeypatch.setattr(folder_size, "_dir_size", probe)
    try:
        _assert_fast_check(first)
        assert first_started.wait(1.0)
        _assert_fast_check(second)
        assert second_started.wait(1.0)
        _check_until(second, lambda: bool(_entries(second_path)))
        assert "folder_size" not in _state(first_path)
        release.set()
        _check_until(first, lambda: bool(_entries(first_path)))
    finally:
        release.set()


def test_probe_error_is_bounded_and_the_next_heartbeat_retries(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv(LIMIT_ENV, TINY_LIMIT)
    first_attempt = threading.Event()
    calls = 0

    def flaky_probe(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_attempt.set()
            raise RuntimeError("x" * 500)
        return CHUNK

    monkeypatch.setattr(folder_size, "_dir_size", flaky_probe)
    _assert_fast_check(agent)
    assert first_attempt.wait(1.0)
    _check_until(agent, lambda: any(event == "folder_size_probe_error" for event, _ in agent.logs))
    errors = [fields["error"] for event, fields in agent.logs if event == "folder_size_probe_error"]
    assert errors == ["x" * 200]
    _check_until(agent, lambda: bool(_entries(tmp_path)))
    assert calls == 2


def test_probe_completed_after_utc_rollover_is_discarded_and_remeasured(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv(LIMIT_ENV, TINY_LIMIT)
    today = {"value": "2026-08-17"}
    monkeypatch.setattr(folder_size, "_today_utc", lambda: today["value"])
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def rollover_probe(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(3.0)
            return CHUNK
        return CHUNK + 1

    monkeypatch.setattr(folder_size, "_dir_size", rollover_probe)
    try:
        _assert_fast_check(agent)
        assert started.wait(1.0)
        today["value"] = "2026-08-18"
        release.set()
        _check_until(
            agent,
            lambda: _state(tmp_path).get("folder_size", {}).get("last_check_date") == today["value"]
            and bool(_entries(tmp_path)),
        )
        entry = _entries(tmp_path)[0]
        assert calls == 2
        assert entry["size_bytes"] == CHUNK + 1
        assert entry["checked_at_date"] == "2026-08-18"
    finally:
        release.set()
