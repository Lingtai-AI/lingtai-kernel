"""Tests for issue #192 — lightweight expired spill artifact messaging.

Verifies that:
- New spill manifests include ``artifact_lifetime`` and ``artifact_state``
  at FIRST CONSTRUCTION (``spill_oversized_result`` — a fresh-result
  decision, not a rebuild/replay mutation)
- ``is_spill_manifest`` recognises manifests carrying those fields
- The read tool's "Spill artifact expired" message is a live filesystem
  classification, independent of any persisted manifest state
- Non-spill missing files keep the generic "File not found" behaviour
- ``artifact_state="unavailable"`` when the sidecar write itself fails

B3 (bounded repair v3): the restore/startup rewriter
``mark_expired_spill_manifests`` (previously invoked from
``base_agent/lifecycle.py::_start`` on every restore) and the session-level
backfill ``_ensure_spill_manifest_fields`` (previously invoked from
``SessionManager.restore_chat``) were REMOVED. Both rewrote historical
``ToolResultBlock.content`` already committed to ``chat_history.jsonl``
based on CURRENT sidecar file-existence — a restore-conditioned canonical
mutation with no explicit ``summarize`` replacement, which the
provider-context replay invariant forbids. See
``tests/test_spill_manifest_restore_preserves_history.py`` for the
byte/value-preservation regression proving restore no longer rewrites
persisted manifests, and the ``tool_result_artifacts.py`` module docstring
for the removal rationale.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from lingtai.kernel.tool_result_artifacts import (
    ARTIFACT_MARKER,
    is_spill_manifest,
    spill_oversized_result,
)


# ---------------------------------------------------------------------------
# 1. New manifests carry ephemeral fields at first construction
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
# 2. is_spill_manifest recognises manifests carrying the new fields
# ---------------------------------------------------------------------------

def test_recognizer_still_works(tmp_path):
    """``is_spill_manifest`` recognises manifests that carry
    ``artifact_state`` and ``artifact_lifetime``."""
    available = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "spill_path": "tmp/tool-results/a.json",
        "artifact_lifetime": "ephemeral_tmp",
        "artifact_state": "available",
    }
    expired_shape = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "spill_path": "tmp/tool-results/b.json",
        "artifact_lifetime": "ephemeral_tmp",
        "artifact_state": "expired",
        "artifact_expired_at": "2025-01-01T00:00:00+00:00",
    }

    assert is_spill_manifest(available)
    assert is_spill_manifest(expired_shape)

    # A non-manifest dict should still be rejected
    assert not is_spill_manifest({"status": "ok", "data": "hello"})


# ---------------------------------------------------------------------------
# 3. Read tool's "Spill artifact expired" message is a live, independent
#    filesystem classification — no persisted manifest state involved.
# ---------------------------------------------------------------------------

def test_generic_missing_file_still_generic(tmp_path):
    """A missing file NOT under ``tmp/tool-results/`` must produce the
    standard ``File not found`` message, not the spill-aware one."""
    from lingtai.tools.read import setup as read_setup

    mock_agent = MagicMock()
    mock_agent._working_dir = tmp_path
    mock_agent._config.language = "en"

    def fake_read(path):
        raise FileNotFoundError(f"No such file: {path}")

    mock_agent._file_io.read = fake_read

    captured_handler = {}

    def fake_add_tool(name, *, schema, handler, description, **kwargs):
        captured_handler[name] = handler

    mock_agent.add_tool = fake_add_tool
    read_setup(mock_agent)

    handler = captured_handler["read"]

    # Generic missing file — not under tmp/tool-results/
    result = handler({"file_path": str(tmp_path / "nonexistent.txt")})
    assert result["status"] == "error"
    assert "File not found" in result["message"]
    assert "spill" not in result["message"].lower()

    # Spill-artifact missing file: classified purely by path shape + current
    # filesystem absence, with NO chat_history.jsonl in tmp_path at all —
    # proving this message needs no persisted manifest state.
    assert not (tmp_path / "history" / "chat_history.jsonl").exists()
    spill_path = str(tmp_path / "tmp" / "tool-results" / "gone.json")
    result = handler({"file_path": spill_path})
    assert result["status"] == "error"
    assert "Spill artifact expired" in result["message"]


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


# ---------------------------------------------------------------------------
# Fix 3 (Low): Normalize read-tool path before classifying as spill
# ---------------------------------------------------------------------------

def test_read_tool_path_traversal_not_classified_as_spill(tmp_path):
    """A path like ``tmp/tool-results/../not-a-spill.txt`` must NOT be
    classified as a spill artifact — the ``..`` escapes the spill
    directory.  The read handler must return the generic File not found."""
    from lingtai.tools.read import setup as read_setup

    mock_agent = MagicMock()
    mock_agent._working_dir = tmp_path
    mock_agent._config.language = "en"

    def fake_read(path):
        raise FileNotFoundError(f"No such file: {path}")

    mock_agent._file_io.read = fake_read

    captured_handler = {}

    def fake_add_tool(name, *, schema, handler, description, **kwargs):
        captured_handler[name] = handler

    mock_agent.add_tool = fake_add_tool
    read_setup(mock_agent)

    handler = captured_handler["read"]

    # Path traverses out of tmp/tool-results/ via ".."
    traversal_path = str(tmp_path / "tmp" / "tool-results" / ".." / "not-a-spill.txt")
    result = handler({"file_path": traversal_path})
    assert result["status"] == "error"
    assert "Spill artifact expired" not in result["message"], (
        "Traversal path must not be classified as a spill artifact"
    )
    assert "File not found" in result["message"]
