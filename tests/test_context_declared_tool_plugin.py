"""Focused vertical evidence for Context's declared host-plugin recut."""
from __future__ import annotations

from pathlib import Path

import pytest

from lingtai.agent import Agent
import lingtai.intrinsic_skills as intrinsic_skills_pkg
import lingtai.tools as tools_pkg
from lingtai.kernel.tool_plugin import ToolPluginHost
from lingtai.tools.context import DECLARATION, get_schema
from tests._service_helpers import make_gemini_mock_service


@pytest.fixture
def context_agent(tmp_path):
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="declared-context",
        working_dir=tmp_path / "agent",
        capabilities={},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


def _bare_manual_installer(tmp_path):
    """Minimal owner for exercising Agent's file-only manual installation seam."""
    agent = object.__new__(Agent)
    agent._working_dir = tmp_path / "agent"
    agent._capabilities = []
    return agent


def _write_skill(path: Path, body: str) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(body, encoding="utf-8")



def test_unallowlisted_same_name_manual_collision_fails_loudly(tmp_path, monkeypatch):
    tools_root = tmp_path / "tools"
    skills_root = tmp_path / "intrinsic_skills"
    _write_skill(tools_root / "future" / "manual", "canonical future\n")
    _write_skill(skills_root / "future", "unrelated future skill\n")
    monkeypatch.setattr(tools_pkg, "__file__", str(tools_root / "__init__.py"))
    monkeypatch.setattr(intrinsic_skills_pkg, "__file__", str(skills_root / "__init__.py"))

    with pytest.raises(RuntimeError, match="collision"):
        _bare_manual_installer(tmp_path)._install_intrinsic_manuals()


def test_context_declaration_is_static_and_derives_its_public_surface():
    assert DECLARATION.name == "context"
    assert DECLARATION.actions == ("molt", "summarize", "rebuild")
    assert DECLARATION.public_actions == (
        "molt", "summarize", "rebuild", "settings", "manual",
    )
    assert DECLARATION.settings is True
    assert DECLARATION.requires == ("workdir", "context_runtime")
    assert get_schema()["properties"]["action"]["enum"] == list(DECLARATION.public_actions)


def test_context_host_receives_only_its_declared_runtime_port():
    host = ToolPluginHost("context", {"context_runtime": object()})
    assert host.granted == ("context_runtime",)
    with pytest.raises(AttributeError, match="workdir"):
        host.workdir


def test_official_context_mount_preserves_one_surface_and_package_manual(context_agent):
    assert context_agent.official_tool_plugins["context"] is DECLARATION
    assert [schema.name for schema in context_agent._tool_schemas].count("context") == 1
    assert [schema.name for schema in context_agent._build_tool_schemas()].count("context") == 1

    manual = context_agent._tool_handlers["context"](
        {"action": "manual", "input": {}, "reasoning": "read procedure"}
    )
    packaged = Path(__file__).resolve().parents[1] / "src/lingtai/tools/context/manual/SKILL.md"
    assert manual["status"] == "ok"
    assert manual["manual_path"].endswith("capabilities/context-manual/SKILL.md")
    assert manual["manual"] == packaged.read_text(encoding="utf-8")
