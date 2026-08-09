from __future__ import annotations

import json
from pathlib import Path

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


def _force_next_probe(workdir):
    path = Path(workdir) / ".notification" / ".nudge_state.json"
    data = _state(workdir)
    data.setdefault("folder_size", {})["last_check_date"] = "2000-01-01"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


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
    _force_next_probe(tmp_path)

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


def test_daily_gate_skips_second_probe_same_utc_day(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    (tmp_path / "chunk.bin").write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setenv(LIMIT_ENV, "0.0001")

    folder_size.check(agent)
    assert len(_entries(tmp_path)) == 1
    first_state = _state(tmp_path)["folder_size"]["size_bytes"]

    (tmp_path / "more.bin").write_bytes(b"q" * (3 * 1024 * 1024))
    folder_size.check(agent)
    assert _state(tmp_path)["folder_size"]["size_bytes"] == first_state


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
    _force_next_probe(tmp_path)

    folder_size.check(agent)
    assert _entries(tmp_path) == []


def test_today_utc_format():
    value = _today_utc()
    assert len(value) == 10
    assert value[4] == "-" and value[7] == "-"
