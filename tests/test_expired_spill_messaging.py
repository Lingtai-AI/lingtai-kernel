"""Tests for issue #192 — lightweight expired spill artifact messaging.

Verifies that:
- New spill manifests include ``artifact_lifetime`` and ``artifact_state``
- ``mark_expired_spill_manifests`` correctly marks missing sidecars as expired
- Marking is idempotent
- ``is_spill_manifest`` still recognises updated manifests
- No ``archive/tool-results`` directory is ever created
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.kernel.tool_result_artifacts import (
    ARTIFACT_MARKER,
    is_spill_manifest,
    mark_expired_spill_manifests,
    spill_oversized_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_history(working_dir: Path, entries: list[dict]) -> None:
    """Write a chat_history.jsonl with the given entry dicts."""
    history_dir = working_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / "chat_history.jsonl"
    lines = [json.dumps(e, ensure_ascii=False, default=str) for e in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_history(working_dir: Path) -> list[dict]:
    """Read back chat_history.jsonl as a list of dicts."""
    path = working_dir / "history" / "chat_history.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_spill_manifest(
    *,
    spill_path: str,
    artifact_state: str = "available",
    with_lifetime: bool = True,
) -> dict:
    """Build a realistic spill manifest for testing."""
    manifest: dict = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "source": "preventive",
        "warning": "spilled",
        "spill_path": spill_path,
        "spill_path_abs": f"/fake/{spill_path}",
        "tool_name": "bash",
        "tool_call_id": "tc-test",
        "original_char_count": 50000,
        "original_byte_count": 50000,
        "cap_chars": 10000,
        "timestamp": "2025-01-01T00:00:00+00:00",
        "preview": "head...",
    }
    if with_lifetime:
        manifest["artifact_lifetime"] = "ephemeral_tmp"
    manifest["artifact_state"] = artifact_state
    return manifest


# ---------------------------------------------------------------------------
# 1. New manifests carry ephemeral fields
# ---------------------------------------------------------------------------

def test_new_manifest_has_ephemeral_fields(tmp_path):
    """``spill_oversized_result`` includes ``artifact_lifetime`` and
    ``artifact_state`` in every new manifest."""
    big = "X" * 30_000  # well over the cap
    manifest = spill_oversized_result(
        big,
        max_chars=10_000,
        tool_name="read",
        tool_call_id="tc-ephemeral",
        working_dir=tmp_path,
    )
    assert isinstance(manifest, dict)
    assert manifest["artifact_lifetime"] == "ephemeral_tmp"
    assert manifest["artifact_state"] == "available"

    # Also verify the updated warning mentions "ephemeral"
    assert "ephemeral" in manifest["warning"].lower()


# ---------------------------------------------------------------------------
# 2. Existing sidecar → not marked expired
# ---------------------------------------------------------------------------

def test_existing_sidecar_not_marked_expired(tmp_path):
    """When the sidecar file still exists, ``mark_expired_spill_manifests``
    leaves it as ``available``."""
    # Create a real sidecar file
    sidecar_dir = tmp_path / "tmp" / "tool-results"
    sidecar_dir.mkdir(parents=True)
    sidecar_file = sidecar_dir / "test-artifact.json"
    sidecar_file.write_text('{"data": "big"}', encoding="utf-8")

    rel_path = "tmp/tool-results/test-artifact.json"
    manifest = _make_spill_manifest(spill_path=rel_path, artifact_state="available")

    # Wrap in a realistic chat-history entry
    entry = {
        "role": "tool",
        "content": [manifest],
    }
    _write_history(tmp_path, [entry])

    expired = mark_expired_spill_manifests(tmp_path)
    assert expired == 0

    history = _read_history(tmp_path)
    m = history[0]["content"][0]
    assert m["artifact_state"] == "available"
    assert "artifact_expired_at" not in m


# ---------------------------------------------------------------------------
# 3. Missing sidecar → marked expired
# ---------------------------------------------------------------------------

def test_missing_sidecar_marked_expired(tmp_path):
    """When the sidecar file is gone, ``mark_expired_spill_manifests`` sets
    ``artifact_state="expired"`` and adds ``artifact_expired_at``."""
    rel_path = "tmp/tool-results/gone-artifact.json"
    # Do NOT create the file — it's missing
    manifest = _make_spill_manifest(spill_path=rel_path, artifact_state="available")

    entry = {"role": "tool", "content": [manifest]}
    _write_history(tmp_path, [entry])

    expired = mark_expired_spill_manifests(tmp_path)
    assert expired == 1

    history = _read_history(tmp_path)
    m = history[0]["content"][0]
    assert m["artifact_state"] == "expired"
    assert "artifact_expired_at" in m
    # Timestamp should be ISO-formatted
    assert "T" in m["artifact_expired_at"]


# ---------------------------------------------------------------------------
# 4. Idempotent — calling twice yields same result
# ---------------------------------------------------------------------------

def test_marking_is_idempotent(tmp_path):
    """Running ``mark_expired_spill_manifests`` twice produces identical
    results and only writes the file once (the second pass is a no-op)."""
    rel_path = "tmp/tool-results/vanished.json"
    manifest = _make_spill_manifest(spill_path=rel_path, artifact_state="available")

    entry = {"role": "tool", "content": [manifest]}
    _write_history(tmp_path, [entry])

    count1 = mark_expired_spill_manifests(tmp_path)
    count2 = mark_expired_spill_manifests(tmp_path)

    assert count1 == 1
    # Second call: already expired, no further changes → 0 new expirations
    # but the manifest is already "expired" so it's a no-op.
    assert count2 == 0

    history = _read_history(tmp_path)
    m = history[0]["content"][0]
    assert m["artifact_state"] == "expired"
    assert "artifact_expired_at" in m


# ---------------------------------------------------------------------------
# 5. is_spill_manifest still works on updated manifests
# ---------------------------------------------------------------------------

def test_recognizer_still_works(tmp_path):
    """``is_spill_manifest`` recognises manifests that now carry
    ``artifact_state`` and ``artifact_lifetime``."""
    available = _make_spill_manifest(
        spill_path="tmp/tool-results/a.json", artifact_state="available",
    )
    expired = _make_spill_manifest(
        spill_path="tmp/tool-results/b.json", artifact_state="expired",
    )
    expired["artifact_expired_at"] = "2025-01-01T00:00:00+00:00"

    assert is_spill_manifest(available)
    assert is_spill_manifest(expired)

    # A non-manifest dict should still be rejected
    assert not is_spill_manifest({"status": "ok", "data": "hello"})


# ---------------------------------------------------------------------------
# 7. No archive directory created
# ---------------------------------------------------------------------------

def test_no_archive_directory_created(tmp_path):
    """The implementation must NOT create an ``archive/tool-results``
    directory — the human explicitly rejected the durable-archive approach."""
    rel_path = "tmp/tool-results/to-expire.json"
    manifest = _make_spill_manifest(spill_path=rel_path, artifact_state="available")
    entry = {"role": "tool", "content": [manifest]}
    _write_history(tmp_path, [entry])

    mark_expired_spill_manifests(tmp_path)

    archive_dir = tmp_path / "archive"
    assert not archive_dir.exists(), (
        "archive/ directory must not be created — "
        "the durable-archive approach was rejected"
    )
    assert not (archive_dir / "tool-results").exists()


# ---------------------------------------------------------------------------
# Additional: legacy manifests without new fields get backfilled
# ---------------------------------------------------------------------------

def test_legacy_manifest_without_new_fields(tmp_path):
    """Old manifests persisted before #192 lack ``artifact_lifetime`` and
    ``artifact_state``.  ``mark_expired_spill_manifests`` should add them."""
    # Build a manifest that predates the new fields (no lifetime/state)
    rel_path = "tmp/tool-results/legacy.json"
    sidecar_dir = tmp_path / "tmp" / "tool-results"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "legacy.json").write_text('"data"', encoding="utf-8")

    legacy_manifest = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "source": "preventive",
        "warning": "old warning",
        "spill_path": rel_path,
        "spill_path_abs": f"/fake/{rel_path}",
        "tool_name": "bash",
        "tool_call_id": "tc-legacy",
        "original_char_count": 50000,
        "original_byte_count": 50000,
        "cap_chars": 10000,
        "timestamp": "2024-06-01T00:00:00+00:00",
        "preview": "old...",
    }
    # No artifact_lifetime or artifact_state keys
    assert "artifact_lifetime" not in legacy_manifest
    assert "artifact_state" not in legacy_manifest

    entry = {"role": "tool", "content": [legacy_manifest]}
    _write_history(tmp_path, [entry])

    expired = mark_expired_spill_manifests(tmp_path)
    assert expired == 0  # sidecar exists

    history = _read_history(tmp_path)
    m = history[0]["content"][0]
    # Fields should now be present
    assert m["artifact_lifetime"] == "ephemeral_tmp"
    assert m["artifact_state"] == "available"


def test_ensure_spill_manifest_fields_in_session_restore():
    """``_ensure_spill_manifest_fields`` backfills ephemeral fields on
    in-memory messages that bypassed lifecycle.py stale-marking."""
    from lingtai.kernel.session import _ensure_spill_manifest_fields

    manifest = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "spill_path": "tmp/tool-results/old.json",
        "original_char_count": 50000,
        "cap_chars": 10000,
    }
    messages = [
        {"role": "assistant", "content": "text"},
        {"role": "tool", "content": [manifest]},
    ]

    _ensure_spill_manifest_fields(messages)

    m = messages[1]["content"][0]
    assert m["artifact_lifetime"] == "ephemeral_tmp"
    assert m["artifact_state"] == "available"


def test_ensure_spill_manifest_fields_idempotent():
    """Backfilling is idempotent — existing values are not overwritten."""
    from lingtai.kernel.session import _ensure_spill_manifest_fields

    manifest = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "spill_path": "tmp/tool-results/x.json",
        "artifact_lifetime": "ephemeral_tmp",
        "artifact_state": "expired",
        "artifact_expired_at": "2025-01-01T00:00:00+00:00",
    }
    messages = [{"role": "tool", "content": [manifest]}]
    _ensure_spill_manifest_fields(messages)

    m = messages[0]["content"][0]
    assert m["artifact_state"] == "expired"  # not overwritten to "available"
    assert m["artifact_expired_at"] == "2025-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Fix 1 (BLOCKER): Expired manifests get updated warning text
# ---------------------------------------------------------------------------

def test_expired_manifest_warning_overwritten(tmp_path):
    """A legacy manifest whose warning instructs reading the spill path
    must have that warning overwritten with explicit expired guidance
    after ``mark_expired_spill_manifests`` runs."""
    rel_path = "tmp/tool-results/legacy-stale.json"
    # Do NOT create the file — it's missing.
    stale_warning = (
        "Tool result was too large and was written to "
        "tmp/tool-results/legacy-stale.json. Read it via the read tool."
    )
    manifest = _make_spill_manifest(spill_path=rel_path, artifact_state="available")
    manifest["warning"] = stale_warning

    entry = {"role": "tool", "content": [manifest]}
    _write_history(tmp_path, [entry])

    expired = mark_expired_spill_manifests(tmp_path)
    assert expired == 1

    history = _read_history(tmp_path)
    m = history[0]["content"][0]
    assert m["artifact_state"] == "expired"
    # The old warning must NOT survive — it told the agent to read a
    # path that no longer exists.
    assert "legacy-stale.json" not in m["warning"], (
        "Stale warning must not reference the missing spill path"
    )
    assert "read it via" not in m["warning"].lower(), (
        "Stale instruction to read the spill path must be replaced"
    )
    # Must include explicit expired guidance.
    assert "EXPIRED" in m["warning"]
    assert "rerun" in m["warning"].lower()


def test_expired_warning_is_idempotent(tmp_path):
    """Once the warning is set to the expired wording, a second
    ``mark_expired_spill_manifests`` call does not rewrite it again."""
    rel_path = "tmp/tool-results/vanished2.json"
    stale_warning = (
        "Tool result was too large and was written to "
        "tmp/tool-results/vanished2.json. Read it via the read tool."
    )
    manifest = _make_spill_manifest(spill_path=rel_path, artifact_state="available")
    manifest["warning"] = stale_warning

    entry = {"role": "tool", "content": [manifest]}
    _write_history(tmp_path, [entry])

    mark_expired_spill_manifests(tmp_path)
    history1 = _read_history(tmp_path)
    warning1 = history1[0]["content"][0]["warning"]

    # Second call — should not touch anything.
    mark_expired_spill_manifests(tmp_path)
    history2 = _read_history(tmp_path)
    warning2 = history2[0]["content"][0]["warning"]

    assert warning1 == warning2


# ---------------------------------------------------------------------------
# Fix 2 (Medium): artifact_state="unavailable" when sidecar write fails
# ---------------------------------------------------------------------------

def test_spill_unavailable_when_working_dir_is_none():
    """When ``working_dir`` is None, the manifest must set
    ``artifact_state=\"unavailable\"`` instead of ``\"available\"``."""
    big = "X" * 30_000
    manifest = spill_oversized_result(
        big,
        max_chars=10_000,
        tool_name="read",
        tool_call_id="tc-none-wd",
        working_dir=None,
    )
    assert isinstance(manifest, dict)
    assert manifest["spill_path"] is None
    assert manifest["artifact_state"] == "unavailable", (
        "working_dir=None must produce artifact_state='unavailable'"
    )
    assert "spill_error" in manifest


def test_spill_unavailable_when_write_fails(tmp_path, monkeypatch):
    """When the sidecar write raises an OSError, the manifest must set
    ``artifact_state=\"unavailable\"``."""
    big = "Y" * 30_000

    # Monkeypatch Path.mkdir to simulate a write failure.
    original_mkdir = Path.mkdir

    def failing_mkdir(self, *args, **kwargs):
        if "tool-results" in str(self):
            raise OSError("simulated disk full")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)

    manifest = spill_oversized_result(
        big,
        max_chars=10_000,
        tool_name="bash",
        tool_call_id="tc-write-fail",
        working_dir=tmp_path,
    )
    assert isinstance(manifest, dict)
    assert manifest["spill_path"] is None
    assert manifest["artifact_state"] == "unavailable", (
        "Sidecar write failure must produce artifact_state='unavailable'"
    )
    assert "spill_error" in manifest
    assert "simulated disk full" in manifest["spill_error"]
