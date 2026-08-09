from __future__ import annotations

import json
import math
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
    folder_size.check(agent)
    assert _entries(tmp_path) == []
    nudge_mod.upsert(agent, "folder_size", {"title": "stale", "detail": "old"})
    folder_size.check(agent)
    assert _entries(tmp_path) == []


def test_over_limit_emits_facts(over_agent, tmp_path):
    folder_size.check(over_agent)
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
    folder_size.check(agent)
    state = _state(tmp_path)["folder_size"]
    assert state["limit_gb"] == DEFAULT_LIMIT_GB
    for entry in _entries(tmp_path):
        assert all(math.isfinite(entry[k]) for k in ("limit_gb", "size_bytes", "size_gb"))


def test_decimal_gb_boundary(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv(LIMIT_ENV, TINY_LIMIT)
    (tmp_path / "at-limit.bin").write_bytes(b"a" * 100_000)
    folder_size.check(agent)
    assert _entries(tmp_path) == []
    (tmp_path / "over.bin").write_bytes(b"b" * 1)
    _force_next_walk(tmp_path)
    folder_size.check(agent)
    assert len(_entries(tmp_path)) == 1


def test_walk_gate_skips_second_walk_same_day(over_agent, tmp_path):
    folder_size.check(over_agent)
    first = _state(tmp_path)["folder_size"]["size_bytes"]
    (tmp_path / "more.bin").write_bytes(b"q" * (3 * 1024 * 1024))
    folder_size.check(over_agent)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first
    assert len(_entries(tmp_path)) == 1


def test_global_repeat_expiry_reappears_same_day(over_agent, tmp_path, monkeypatch):
    folder_size.check(over_agent)
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
    folder_size.check(over_agent)
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "off")
    run_checks(over_agent)
    assert _entries(tmp_path) == []
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "on")
    run_checks(over_agent)
    assert len(_entries(tmp_path)) == 1


def test_transient_upsert_failure_retried_same_day(over_agent, tmp_path, monkeypatch):
    real = nudge_mod.upsert

    def fail(a, k, b):
        raise RuntimeError("transient store failure")

    monkeypatch.setattr(nudge_mod, "upsert", fail)
    with pytest.raises(RuntimeError, match="transient"):
        folder_size.check(over_agent)
    first = _state(tmp_path)["folder_size"]["size_bytes"]
    monkeypatch.setattr(nudge_mod, "upsert", real)
    folder_size.check(over_agent)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first
    assert len(_entries(tmp_path)) == 1


def test_run_checks_dispatches(over_agent, tmp_path, monkeypatch):
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "on")
    run_checks(over_agent)
    assert any(e["kind"] == "folder_size" for e in _entries(tmp_path))


def test_reprobe_next_day_clears(over_agent, tmp_path):
    folder_size.check(over_agent)
    assert len(_entries(tmp_path)) == 1
    (tmp_path / "chunk.bin").unlink()
    _force_next_walk(tmp_path)
    folder_size.check(over_agent)
    assert _entries(tmp_path) == []


def test_today_utc_format():
    v = _today_utc()
    assert len(v) == 10 and v[4] == "-" and v[7] == "-"
