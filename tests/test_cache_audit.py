"""Tests for the fail-open cache audit JSONL writer."""
from __future__ import annotations

import json
from pathlib import Path

from lingtai.kernel.cache_audit import CacheAuditor, batch_hash


def test_cache_auditor_writes_jsonl(tmp_path):
    path = tmp_path / "logs" / "cache_audit.jsonl"
    auditor = CacheAuditor(path)

    auditor.record(
        call_role="main",
        provider="anthropic",
        model="claude-test",
        input_tokens=100,
        cached_tokens=75,
        output_tokens=10,
        thinking_tokens=3,
        system_tokens=50,
        tools_tokens=20,
        batch_hashes=["sha256:abc"],
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["call_role"] == "main"
    assert row["provider"] == "anthropic"
    assert row["model"] == "claude-test"
    assert row["input_tokens"] == 100
    assert row["cached_tokens"] == 75
    assert row["uncached_input"] == 25
    assert row["cache_ratio"] == 0.75
    assert row["batch_hashes"] == ["sha256:abc"]


def test_cache_auditor_appends(tmp_path):
    path = tmp_path / "logs" / "cache_audit.jsonl"
    auditor = CacheAuditor(path)

    for _ in range(3):
        auditor.record(call_role="main", provider="x", model="y", input_tokens=10, cached_tokens=0)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_cache_auditor_constructor_fail_open(tmp_path, monkeypatch):
    def broken_mkdir(self, *args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "mkdir", broken_mkdir)

    path = tmp_path / "logs" / "cache_audit.jsonl"
    auditor = CacheAuditor(path)
    assert auditor.enabled is False

    auditor.record(call_role="main", provider="x", model="y", input_tokens=10, cached_tokens=0)
    assert not path.exists()


def test_cache_auditor_record_fail_open(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "cache_audit.jsonl"
    auditor = CacheAuditor(path)

    def broken_open(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", broken_open)

    auditor.record(call_role="main", provider="x", model="y", input_tokens=10, cached_tokens=0)


def test_batch_hash_is_stable_and_distinguishes_content():
    assert batch_hash("abc") == batch_hash("abc")
    assert batch_hash("abc") != batch_hash("xyz")
    assert batch_hash("abc").startswith("sha256:")
