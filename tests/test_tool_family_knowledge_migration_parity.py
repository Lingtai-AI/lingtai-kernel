"""Knowledge catalog behavior parity after the psyche migration.

The ``knowledge`` public root and its ``info`` action are gone: the one public
root is ``psyche``, whose read-only ``psyche(action='knowledge')`` returns
the knowledge manual. Envelope, schema, and dispatch evidence for that root
lives in ``tests/test_psyche_family.py``.

What this file pins is the part that did NOT move — the private catalog
lifecycle. Reconciliation still re-scans and reconciles the catalog and reports
health (root/count/problems) without loading bodies or mutating entries, the
legacy JSON migration still runs from setup/refresh only, and the manual is
still returned without any catalog side effect.
"""
from __future__ import annotations

from pathlib import Path

from lingtai.agent import Agent
from lingtai.tools import knowledge as knowledge_tool
from tests._service_helpers import make_gemini_mock_service as make_mock_service


def _mk_agent(tmp_path: Path):
    workdir = tmp_path / "agent"
    agent = Agent(
        service=make_mock_service(),
        agent_name="knowledge-family-test",
        working_dir=workdir,
        capabilities={"knowledge": {}},
    )
    return agent, workdir


def _write_entry(folder: Path, name: str, desc: str = "test entry", body: str = "Body.") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "KNOWLEDGE.md"
    path.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n")
    return path


def _manual(agent, **root):
    args = {"action": "knowledge", "input": {}, "reasoning": "load knowledge guidance"}
    args.update(root)
    return agent._intrinsics["psyche"](args)


# ---------------------------------------------------------------------------
# Catalog lifecycle (formerly the `info` action)
# ---------------------------------------------------------------------------

def test_reconcile_rescans_and_reports_exact_count_and_problems(tmp_path):
    """An entry authored after setup is picked up on the next reconciliation,
    and a malformed entry is reported in ``problems`` — behavior unchanged by
    the tool retirement."""
    agent, workdir = _mk_agent(tmp_path)
    try:
        first = knowledge_tool._reconcile(agent)
        assert first["status"] == "ok"
        assert first["knowledge_dir"] == str(workdir / "knowledge")
        assert first["catalog_size"] == 0
        assert first["problems"] == []

        _write_entry(workdir / "knowledge" / "alpha", "alpha")
        _write_entry(workdir / "knowledge" / "beta", "beta")
        broken = workdir / "knowledge" / "broken"
        broken.mkdir(parents=True, exist_ok=True)
        (broken / "KNOWLEDGE.md").write_text("no frontmatter here\n")

        second = knowledge_tool._reconcile(agent)
        assert second["status"] == "ok"
        assert second["catalog_size"] == 2
        assert len(second["problems"]) == 1
        assert set(second) == {"status", "knowledge_dir", "catalog_size", "problems"}
    finally:
        agent.stop(timeout=1.0)


def test_reconcile_does_not_load_bodies_or_mutate_entries(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    try:
        secret = "SENTINEL-BODY-must-not-be-returned"
        path = _write_entry(workdir / "knowledge" / "alpha", "alpha", body=secret)
        before = path.read_bytes()

        result = knowledge_tool._reconcile(agent)
        assert result["catalog_size"] == 1
        assert secret not in repr(result)
        # No entry mutation: the capability is pure presentation.
        assert path.read_bytes() == before
    finally:
        agent.stop(timeout=1.0)


def test_full_context_rebuild_recomposes_the_catalog(tmp_path):
    """A full rebuild re-reads the catalog — one of the four durable domains."""
    agent, workdir = _mk_agent(tmp_path)
    try:
        _write_entry(workdir / "knowledge" / "authored-later", "authored-later")
        # Writing alone does not hot-load the section.
        assert "authored-later" not in (
            agent._prompt_manager.read_section("knowledge") or ""
        )
        agent._reconstruct_context()
        assert "authored-later" in agent._prompt_manager.read_section("knowledge")
    finally:
        agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Migration stays in the private setup/refresh lifecycle
# ---------------------------------------------------------------------------

def test_catalog_recompose_never_runs_the_legacy_migration(tmp_path, monkeypatch):
    """``_compose_catalog`` is pure read: rebuild must not migrate."""
    agent, _ = _mk_agent(tmp_path)
    try:
        def _boom(*_a, **_k):  # pragma: no cover - must never run
            raise AssertionError("catalog recompose ran the legacy migration")

        monkeypatch.setattr(knowledge_tool, "_migrate_legacy_json", _boom)
        result = knowledge_tool._compose_catalog(agent)
        assert result["status"] == "ok"
        # And the full reconstruction path uses that same pure-read composer.
        agent._reconstruct_context()
    finally:
        agent.stop(timeout=1.0)


def test_setup_lifecycle_is_what_runs_the_legacy_migration(tmp_path):
    """``_reconcile`` (setup/refresh) migrates; the composer alone does not."""
    import json

    agent, workdir = _mk_agent(tmp_path)
    try:
        legacy = workdir / "knowledge" / "knowledge.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({
            "entries": [{"id": "1", "title": "Legacy", "summary": "A legacy note."}]
        }))

        # A pure catalog recompose leaves the legacy file untouched.
        knowledge_tool._compose_catalog(agent)
        assert legacy.is_file()

        # The setup/refresh lifecycle migrates it exactly once.
        knowledge_tool._reconcile(agent)
        assert not legacy.is_file()
        migrated = list((workdir / "knowledge").glob("*/KNOWLEDGE.md"))
        assert len(migrated) == 1
        assert "Legacy" in migrated[0].read_text(encoding="utf-8")
    finally:
        agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# The manual, now reached through psyche
# ---------------------------------------------------------------------------

def test_manual_returns_body_and_path_without_rescanning(tmp_path, monkeypatch):
    agent, workdir = _mk_agent(tmp_path)
    try:
        manual_path = (
            workdir / ".library" / "intrinsic" / "capabilities" / "knowledge" / "SKILL.md"
        )
        expected = manual_path.read_text(encoding="utf-8")

        def _boom(*_a, **_k):  # pragma: no cover - must never run
            raise AssertionError("manual rescanned the catalog")

        monkeypatch.setattr(knowledge_tool, "_reconcile", _boom)
        monkeypatch.setattr(knowledge_tool, "_compose_catalog", _boom)
        monkeypatch.setattr(knowledge_tool, "_migrate_legacy_json", _boom)

        result = _manual(agent)
        assert result["status"] == "ok"
        assert result["manual"] == expected
        assert result["manual_path"] == str(manual_path)
        # No health fields, and no double wrap.
        for health_only in ("catalog_size", "problems", "knowledge_dir"):
            assert health_only not in result
        assert "content" not in result and "structuredContent" not in result
    finally:
        agent.stop(timeout=1.0)


def test_manual_missing_is_degraded_and_still_no_rescan(tmp_path, monkeypatch):
    agent, workdir = _mk_agent(tmp_path)
    try:
        manual_path = (
            workdir / ".library" / "intrinsic" / "capabilities" / "knowledge" / "SKILL.md"
        )
        manual_path.unlink()

        def _boom(*_a, **_k):  # pragma: no cover - must never run
            raise AssertionError("degraded manual rescanned the catalog")

        monkeypatch.setattr(knowledge_tool, "_reconcile", _boom)
        monkeypatch.setattr(knowledge_tool, "_compose_catalog", _boom)

        result = _manual(agent)
        assert result["status"] == "degraded"
        assert result["manual"] == ""
        assert result["manual_path"] == str(manual_path)
        assert "knowledge manual missing" in result["error"]
    finally:
        agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# The signpost contract still holds on the surviving root
# ---------------------------------------------------------------------------

def test_no_authoring_search_or_edit_capability_survives():
    """No public action creates, edits, searches, or loads knowledge entries."""
    from lingtai.tools import psyche as psyche_tool

    schema = psyche_tool.get_schema()
    actions = set(schema["properties"]["action"]["enum"])
    forbidden = {
        "create", "write", "edit", "update", "delete", "search", "query",
        "load", "view", "submit", "consolidate", "info", "append",
    }
    assert actions.isdisjoint(forbidden)
    # And no child accepts an input field at all, so no authoring payload can
    # be smuggled through one.
    for branch in schema["properties"]["input"]["anyOf"]:
        assert branch["properties"] == {}


def test_knowledge_registers_no_tool_and_is_not_a_summarize_family(tmp_path):
    from lingtai.kernel.tool_result_summary import _LTP_V2_MIGRATED_FAMILIES

    assert "knowledge" not in _LTP_V2_MIGRATED_FAMILIES
    agent, _ = _mk_agent(tmp_path)
    try:
        assert "knowledge" not in agent._tool_handlers
        assert "knowledge" not in [s.name for s in agent._build_tool_schemas()]
    finally:
        agent.stop(timeout=1.0)
