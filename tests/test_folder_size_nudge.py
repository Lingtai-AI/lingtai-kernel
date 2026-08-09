from __future__ import annotations

import json
import math
from pathlib import Path

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


class _Agent:
    def __init__(self, workdir):
        self._working_dir = workdir
        self._notification_store = notification_store_for(workdir)
        self._notification_fp = ()
        self.logs = []

    def _log(self, event, **fields):
        self.logs.append((event, fields))


def _entries(workdir):
    payload = snapshot_notifications(workdir).get("nudge", {})
    return payload.get("data", {}).get("nudges", [])


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


def test_limit_env_default_and_invalid_values(monkeypatch):
    monkeypatch.delenv(LIMIT_ENV, raising=False)
    assert _read_limit_gb() == (DEFAULT_LIMIT_GB, None)

    monkeypatch.setenv(LIMIT_ENV, "10")
    assert _read_limit_gb() == (10.0, None)

    monkeypatch.setenv(LIMIT_ENV, "0.5")
    assert _read_limit_gb() == (0.5, None)

    monkeypatch.setenv(LIMIT_ENV, "wat")
    assert _read_limit_gb() == (DEFAULT_LIMIT_GB, "wat")

    monkeypatch.setenv(LIMIT_ENV, "0")
    assert _read_limit_gb() == (DEFAULT_LIMIT_GB, "0")

    monkeypatch.setenv(LIMIT_ENV, "-3")
    assert _read_limit_gb() == (DEFAULT_LIMIT_GB, "-3")


def test_limit_env_rejects_non_finite_values(monkeypatch):
    for bad in ("nan", "inf", "-inf", "1e309"):
        monkeypatch.setenv(LIMIT_ENV, bad)
        value, invalid = _read_limit_gb()
        assert value == DEFAULT_LIMIT_GB, bad
        assert invalid == bad, bad
        assert math.isfinite(value), bad


def test_dir_size_sums_files_and_skips_symlinks(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"x" * 100)
    (tmp_path / "link-file").symlink_to(tmp_path / "a.txt")
    (tmp_path / "link-dir").symlink_to(sub, target_is_directory=True)

    assert _dir_size(tmp_path) == 5 + 100


def test_under_limit_emits_no_finding_and_clears(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "small.bin").write_bytes(b"y" * 1024)
    monkeypatch.setenv(LIMIT_ENV, "5")

    folder_size.check(agent)
    assert _entries(tmp_path) == []
    assert _state(tmp_path)["folder_size"]["size_bytes"] == 1024

    from lingtai.kernel.nudge import upsert as _upsert

    _upsert(agent, "folder_size", {"title": "stale", "detail": "old"})
    assert len(_entries(tmp_path)) == 1

    folder_size.check(agent)
    assert _entries(tmp_path) == []


def test_over_limit_emits_finding_with_facts(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")

    folder_size.check(agent)
    entries = _entries(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kind"] == "folder_size"
    assert entry["nudge_channel"] == ENTRY_CHANNEL_STORAGE_SIZE
    assert entry["size_bytes"] == 2 * 1024 * 1024
    assert entry["limit_gb"] == 0.0001
    assert entry["local_path"] == str(tmp_path)
    assert "exceeds" in entry["title"]
    assert str(tmp_path) in entry["detail"]
    assert LIMIT_ENV in entry["detail"]
    assert "does not authorize deletion" in entry["detail"]
    assert "owner/human authorization" in entry["detail"]


def test_no_non_finite_value_reaches_state_or_notification(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "nan")

    folder_size.check(agent)
    state = _state(tmp_path)["folder_size"]
    assert state["limit_gb"] == DEFAULT_LIMIT_GB
    assert math.isfinite(state["limit_gb"])
    assert math.isfinite(state["size_bytes"])
    for entry in _entries(tmp_path):
        assert math.isfinite(entry["limit_gb"])
        assert math.isfinite(entry["size_bytes"])
        assert math.isfinite(entry["size_gb"])


def test_decimal_gb_boundary(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv(LIMIT_ENV, "0.0001")  # 100_000 bytes

    (tmp_path / "at-limit.bin").write_bytes(b"a" * 100_000)
    folder_size.check(agent)
    assert _entries(tmp_path) == []  # exactly at threshold is not over

    (tmp_path / "over.bin").write_bytes(b"b" * 1)
    _force_next_walk(tmp_path)
    folder_size.check(agent)
    assert len(_entries(tmp_path)) == 1


def test_walk_gate_skips_second_walk_same_utc_day(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")

    folder_size.check(agent)
    assert len(_entries(tmp_path)) == 1
    first_state = _state(tmp_path)["folder_size"]["size_bytes"]

    (tmp_path / "more.bin").write_bytes(b"q" * (3 * 1024 * 1024))
    folder_size.check(agent)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first_state
    assert len(_entries(tmp_path)) == 1


def test_global_repeat_expiry_reappears_without_new_walk(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")

    folder_size.check(agent)
    assert len(_entries(tmp_path)) == 1
    first_walk = _state(tmp_path)["folder_size"]["size_bytes"]

    # Dismiss the currently displayed finding (short repeat interval).
    monkeypatch.setenv("LINGTAI_NUDGE_REPEAT_INTERVAL", "2s")
    nudge_mod.record_dismissal(agent)

    # Same-day re-evaluation must call upsert again (not early-return) and,
    # because the dismissal is still fresh, the persisted finding stays.
    upsert_calls: list[tuple] = []
    real_upsert = nudge_mod.upsert

    def _spy(agent_, kind, body):
        upsert_calls.append((kind, body))
        return real_upsert(agent_, kind, body)

    monkeypatch.setattr(nudge_mod, "upsert", _spy)
    folder_size.check(agent)
    assert any(kind == "folder_size" for kind, _ in upsert_calls)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first_walk
    assert len(_entries(tmp_path)) == 1

    # Expire the dismissal (rewrite until in the past) and re-evaluate: the
    # same-day finding re-emits without a second walk.
    data = _state(tmp_path)
    for entry in data.get("dismissed", {}).values():
        entry["until"] = 1_000_000.0
    _state_file(tmp_path).write_text(json.dumps(data), encoding="utf-8")

    folder_size.check(agent)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first_walk
    assert len(_entries(tmp_path)) == 1


def test_enable_off_on_restores_finding_same_day_without_walk(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")

    folder_size.check(agent)
    assert len(_entries(tmp_path)) == 1

    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "off")
    run_checks(agent)
    assert _entries(tmp_path) == []

    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "on")
    run_checks(agent)
    assert len(_entries(tmp_path)) == 1


def test_transient_upsert_failure_is_retried_same_day_without_walk(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")
    real_upsert = nudge_mod.upsert

    def _fail_once(agent_, kind, body):
        raise RuntimeError("transient store failure")

    monkeypatch.setattr(nudge_mod, "upsert", _fail_once)
    import pytest

    with pytest.raises(RuntimeError, match="transient store failure"):
        folder_size.check(agent)
    assert _entries(tmp_path) == []
    first_walk = _state(tmp_path)["folder_size"]["size_bytes"]

    monkeypatch.setattr(nudge_mod, "upsert", real_upsert)
    folder_size.check(agent)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first_walk
    assert len(_entries(tmp_path)) == 1


def test_run_checks_dispatches_folder_size(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "on")

    run_checks(agent)
    entries = _entries(tmp_path)
    assert any(e["kind"] == "folder_size" for e in entries)


def test_reprobe_next_day_clears_when_under_threshold(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")

    folder_size.check(agent)
    assert len(_entries(tmp_path)) == 1

    (tmp_path / "chunk.bin").unlink()
    _force_next_walk(tmp_path)

    folder_size.check(agent)
    assert _entries(tmp_path) == []


def test_today_utc_format():
    value = _today_utc()
    assert len(value) == 10
    assert value[4] == "-" and value[7] == "-"
