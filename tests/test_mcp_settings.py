"""MCP-owned five-field settings inventory and source semantics."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lingtai.tools.mcp import DECLARATION, _build_family, get_schema
from lingtai.tools.mcp.settings import MCPSettingsProvider

_REQUIRED = {
    "manifest": {"llm": {"provider": "mock", "model": "mock-model"}},
    "covenant": "operator contract",
    "pad": "durable state",
}
_FIELDS = ["key", "current", "default", "configurable", "comment"]
_ANCHOR = "mcp-manual#configuration-settings"


def _write_init(workdir: Path, *, addons: list[str], marker: str) -> bytes:
    raw = json.dumps({
        **_REQUIRED,
        "addons": addons,
        "mcp": {
            "private": {
                "type": "stdio",
                "command": "private-runner",
                "env": {"PRIVATE_VALUE": marker},
            },
        },
    }, indent=2).encode()
    (workdir / "init.json").write_bytes(raw)
    return raw


def _family(workdir: Path):
    host = SimpleNamespace(workdir=SimpleNamespace(path=workdir))
    return _build_family(host)


def _show(workdir: Path) -> dict:
    return _family(workdir).handle({
        "action": "settings",
        "input": {},
        "reasoning": "inspect MCP configuration",
    })


def test_declaration_and_schema_expose_settings_immediately_before_manual():
    assert DECLARATION.settings is True
    assert DECLARATION.public_actions == ("info", "settings", "manual")
    assert get_schema()["properties"]["action"]["enum"] == [
        "info", "settings", "manual",
    ]
    assert _build_family(None).child_names == DECLARATION.public_actions


def test_settings_has_exact_rows_order_fields_and_fresh_effective_current(tmp_path):
    first = _write_init(tmp_path, addons=["imap"], marker="first-hidden-marker")
    result = _show(tmp_path)

    assert list(result) == ["settings"]
    assert [row["key"] for row in result["settings"]] == [
        "init.addons", "init.mcp",
    ]
    assert [list(row) for row in result["settings"]] == [_FIELDS, _FIELDS]
    assert result["settings"] == [
        {
            "key": "init.addons",
            "current": ["imap"],
            "default": [],
            "configurable": True,
            "comment": _ANCHOR,
        },
        {
            "key": "init.mcp",
            "current": "<redacted>",
            "default": "<redacted>",
            "configurable": True,
            "comment": _ANCHOR,
        },
    ]
    assert (tmp_path / "init.json").read_bytes() == first
    assert "first-hidden-marker" not in repr(result)
    assert "private-runner" not in repr(result)

    second = _write_init(tmp_path, addons=["telegram"], marker="second-hidden-marker")
    refreshed = _show(tmp_path)
    assert refreshed["settings"][0]["current"] == ["telegram"]
    assert refreshed["settings"][1]["current"] == "<redacted>"
    assert (tmp_path / "init.json").read_bytes() == second
    assert "second-hidden-marker" not in repr(refreshed)


def test_sensitive_row_preserves_mapping_type_before_projection(tmp_path):
    _write_init(tmp_path, addons=[], marker="never-project-this-marker")
    rows = MCPSettingsProvider(tmp_path)()

    assert [row.key for row in rows] == ["init.addons", "init.mcp"]
    assert isinstance(rows[1].current, dict)
    assert isinstance(rows[1].default, dict)
    assert rows[1]._sensitive is True
    assert "never-project-this-marker" not in repr(rows)


def test_failed_canonical_read_is_one_fixed_whole_inventory_failure(tmp_path):
    (tmp_path / "init.json").write_text("{not valid json", encoding="utf-8")
    assert _show(tmp_path) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_settings_is_strict_show_only_and_manual_anchor_exists_in_canonical_manual(tmp_path):
    _write_init(tmp_path, addons=[], marker="private-marker")
    family = _family(tmp_path)
    for invalid in (None, [], {"set": True}):
        result = family.handle({
            "action": "settings",
            "input": invalid,
            "reasoning": "invalid mutation attempt",
        })
        assert result["status"] == "failed"

    root = Path(__file__).parents[1]
    packaged = root / "src/lingtai/tools/mcp/skills/mcp-manual/SKILL.md"
    assert packaged.is_file()
    assert "## Configuration settings" in packaged.read_text(encoding="utf-8")


def test_real_agent_starts_mounts_settings_and_builds_complete_prompt(tmp_path):
    from lingtai.agent import Agent
    from tests._service_helpers import make_gemini_mock_service

    workdir = tmp_path / "agent"
    workdir.mkdir()
    _write_init(workdir, addons=[], marker="prompt-private-marker")
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="mcp-settings-real-agent",
        working_dir=workdir,
        capabilities={"mcp": {}},
        addons=[],
    )
    try:
        assert agent.official_tool_plugins["mcp"] is DECLARATION
        schema = next(item for item in agent._tool_schemas if item.name == "mcp")
        assert schema.parameters["properties"]["action"]["enum"] == [
            "info", "settings", "manual",
        ]
        shown = agent._tool_handlers["mcp"]({
            "action": "settings",
            "input": {},
            "reasoning": "verify mounted inventory",
        })
        assert [row["key"] for row in shown["settings"]] == [
            "init.addons", "init.mcp",
        ]
        complete_prompt = agent._build_system_prompt()
        assert isinstance(complete_prompt, str) and complete_prompt
        assert "prompt-private-marker" not in complete_prompt
    finally:
        agent.stop(timeout=1.0)
