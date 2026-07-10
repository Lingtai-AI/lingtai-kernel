from __future__ import annotations

import json
from pathlib import Path

from tools.mcp import get_description as mcp_description
from tools.mcp import get_schema as mcp_schema


def _i18n(lang: str) -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / "src" / "tools" / "i18n" / f"{lang}.json"
    return json.loads(path.read_text())


def test_skills_and_knowledge_descriptions_are_explicit_signposts() -> None:
    en = _i18n("en")
    assert en["skills.description"].startswith("SIGNPOST ONLY:")
    assert "does not author, pin, publish, install, or execute skills" in en["skills.description"]
    assert en["knowledge.description"].startswith("SIGNPOST ONLY:")
    assert "does not create, edit, search, or load knowledge entries" in en["knowledge.description"]


def test_mcp_description_and_actions_are_explicit_signposts() -> None:
    assert mcp_description().startswith("SIGNPOST ONLY:")
    assert "does not register, activate, configure, or troubleshoot MCP servers" in mcp_description()
    assert "`info` only re-reads the registry" in mcp_description()
    assert "`manual` returns the mcp-manual body" in mcp_description()
    prop = mcp_schema()["properties"]["action"]
    assert prop["enum"] == ["info", "manual"]
    action = prop["description"]
    assert "info: signpost-only action" in action
    assert "without the manual body" in action
    assert "manual: return only the mcp-manual skill body" in action
    assert "Neither action mutates MCP configuration" in action
