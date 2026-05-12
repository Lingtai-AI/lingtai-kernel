"""Tests for library capability — durable long-term knowledge."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from lingtai.agent import Agent


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def test_library_setup_registers_tool(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    assert "library" in agent._tool_handlers
    assert "codex" in agent._tool_handlers  # deprecated compatibility alias
    agent.stop(timeout=1.0)


def test_codex_capability_normalizes_to_library(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["codex"],
    )
    try:
        assert agent.get_capability("library") is not None
        assert agent.get_capability("codex") is None
        assert "library" in agent._tool_handlers
        assert "codex" in agent._tool_handlers
    finally:
        agent.stop(timeout=1.0)


def test_codex_tool_alias_uses_library_store(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    try:
        result = agent._tool_handlers["codex"]({
            "action": "submit",
            "title": "Compat",
            "summary": "Old codex tool writes to renamed library.",
        })
        assert result["status"] == "ok"
        prompt = agent._prompt_manager.read_section("library") or ""
        assert "Compat" in prompt
        assert (agent.working_dir / "codex" / "codex.json").is_file()
    finally:
        agent.stop(timeout=1.0)


def test_library_manager_accessible(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    assert mgr is not None
    agent.stop(timeout=1.0)


def test_library_independent_of_psyche(tmp_path):
    """Library is a separate capability; psyche is always-on as intrinsic."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    assert "psyche" in agent._intrinsics
    assert "library" in agent._tool_handlers
    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


def test_submit_creates_entry(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    result = mgr.handle({
        "action": "submit",
        "title": "TCP Retry Logic",
        "summary": "Covers retry backoff and failure modes.",
        "content": "The TCP mail service uses exponential backoff...",
    })
    assert result["status"] == "ok"
    assert "id" in result
    data = json.loads((agent.working_dir / "codex" / "codex.json").read_text())
    assert len(data["entries"]) == 1
    assert data["entries"][0]["title"] == "TCP Retry Logic"
    agent.stop(timeout=1.0)


def test_submit_requires_title(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    result = mgr.handle({"action": "submit", "summary": "s", "content": "c"})
    assert "error" in result
    agent.stop(timeout=1.0)


def test_submit_enforces_limit(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 2}},
    )
    mgr = agent.get_capability("library")
    mgr.handle({"action": "submit", "title": "A", "summary": "s", "content": "c"})
    mgr.handle({"action": "submit", "title": "B", "summary": "s", "content": "c"})
    result = mgr.handle({"action": "submit", "title": "C", "summary": "s", "content": "c"})
    assert "error" in result
    assert "full" in result["error"].lower()
    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Submit — content optional
# ---------------------------------------------------------------------------


def test_submit_without_content(tmp_path):
    """Title + summary alone is a valid entry — content is optional."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    result = mgr.handle({
        "action": "submit",
        "title": "A",
        "summary": "Summary alone is sometimes the whole nugget.",
    })
    assert result["status"] == "ok"
    assert "id" in result
    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


def test_view_returns_content(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    r = mgr.handle({"action": "submit", "title": "X", "summary": "s", "content": "full content here"})
    result = mgr.handle({"action": "view", "ids": [r["id"]]})
    assert result["status"] == "ok"
    assert result["entries"][0]["content"] == "full content here"
    # supplementary not returned by default
    assert "supplementary" not in result["entries"][0]
    agent.stop(timeout=1.0)


def test_view_with_include_supplementary(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    r = mgr.handle({
        "action": "submit", "title": "X", "summary": "s",
        "content": "main", "supplementary": "extra material",
    })
    result = mgr.handle({
        "action": "view", "ids": [r["id"]], "include_supplementary": True,
    })
    assert result["entries"][0]["content"] == "main"
    assert result["entries"][0]["supplementary"] == "extra material"
    agent.stop(timeout=1.0)


def test_view_invalid_id(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    result = mgr.handle({"action": "view", "ids": ["nope"]})
    assert "error" in result
    agent.stop(timeout=1.0)


def test_filter_and_export_actions_rejected(tmp_path):
    """Removed actions return error, not silent no-op."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    for action in ("filter", "export"):
        result = mgr.handle({"action": action})
        assert "error" in result, f"{action} should be rejected"
        assert "Unknown action" in result["error"]
    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Consolidate
# ---------------------------------------------------------------------------


def test_consolidate(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    r1 = mgr.handle({"action": "submit", "title": "A", "summary": "s1.", "content": "c1"})
    r2 = mgr.handle({"action": "submit", "title": "B", "summary": "s2.", "content": "c2"})
    result = mgr.handle({
        "action": "consolidate",
        "ids": [r1["id"], r2["id"]],
        "title": "AB Combined",
        "summary": "Merged A and B.",
        "content": "Combined content.",
    })
    assert result["status"] == "ok"
    assert result["removed"] == 2
    data = json.loads((agent.working_dir / "codex" / "codex.json").read_text())
    assert len(data["entries"]) == 1
    assert data["entries"][0]["title"] == "AB Combined"
    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete(tmp_path):
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"library": {"library_limit": 50}},
    )
    mgr = agent.get_capability("library")
    r1 = mgr.handle({"action": "submit", "title": "A", "summary": "s.", "content": "c"})
    r2 = mgr.handle({"action": "submit", "title": "B", "summary": "s.", "content": "c"})
    result = mgr.handle({"action": "delete", "ids": [r1["id"]]})
    assert result["status"] == "ok"
    assert result["removed"] == 1
    data = json.loads((agent.working_dir / "codex" / "codex.json").read_text())
    assert len(data["entries"]) == 1
    assert data["entries"][0]["id"] == r2["id"]
    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_has_all_fields():
    from lingtai.core.library import get_schema
    SCHEMA = get_schema("en")
    actions = SCHEMA["properties"]["action"]["enum"]
    assert set(actions) == {"submit", "view", "consolidate", "delete"}
    props = SCHEMA["properties"]
    assert "title" in props
    assert "summary" in props
    assert "content" in props
    assert "supplementary" in props
    assert "ids" in props
    assert "include_supplementary" in props
    # Removed properties must be gone — these fields no longer have any code path.
    assert "pattern" not in props
    assert "limit" not in props
    assert "depth" not in props


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def test_id_deterministic():
    from lingtai.core.library import LibraryManager
    id1 = LibraryManager._make_id("hello", "2026-03-16T00:00:00Z")
    id2 = LibraryManager._make_id("hello", "2026-03-16T00:00:00Z")
    assert id1 == id2
    assert len(id1) == 8


def test_id_differs_by_content():
    from lingtai.core.library import LibraryManager
    id1 = LibraryManager._make_id("hello", "2026-03-16T00:00:00Z")
    id2 = LibraryManager._make_id("world", "2026-03-16T00:00:00Z")
    assert id1 != id2
