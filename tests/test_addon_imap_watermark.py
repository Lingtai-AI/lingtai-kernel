"""Tests for the per-(account, folder) UIDNEXT watermark store."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.addons.imap._watermark import WatermarkStore


def test_load_returns_empty_when_no_file(tmp_path: Path) -> None:
    store = WatermarkStore(tmp_path / "missing.json")
    assert store.load() == {}


def test_save_and_reload_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = WatermarkStore(path)
    store.save({"INBOX": {"uidvalidity": 12345, "last_delivered_uid": 4821}})

    reloaded = WatermarkStore(path).load()
    assert reloaded == {"INBOX": {"uidvalidity": 12345, "last_delivered_uid": 4821}}


def test_save_is_atomic_no_partial_file_on_crash(tmp_path: Path, monkeypatch) -> None:
    """If os.replace fails, the original file must remain untouched."""
    path = tmp_path / "state.json"
    store = WatermarkStore(path)
    store.save({"INBOX": {"uidvalidity": 1, "last_delivered_uid": 100}})

    import os
    original_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save({"INBOX": {"uidvalidity": 1, "last_delivered_uid": 200}})

    # Original file is unchanged
    monkeypatch.setattr(os, "replace", original_replace)
    assert WatermarkStore(path).load() == {
        "INBOX": {"uidvalidity": 1, "last_delivered_uid": 100}
    }
    # No leftover .tmp files
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_load_corrupt_json_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{ this is not json")
    assert WatermarkStore(path).load() == {}
