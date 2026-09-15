"""Curated-MCP plugin packaging invariants for the WhatsApp curated MCP.

Mirrors ``tests/test_curated_mcp_plugin_package.py`` (the Telegram reference
slice) for the WhatsApp package: ``lingtai.mcp_servers._plugin.CuratedMcpPlugin``
binds the same three facts for WhatsApp — registry name, bundled ``SKILL.md``,
and the stdio MCP declaration the curated catalog publishes — and enforces the
same reserved ``manual`` promise. These tests pin the packaging promise and the
WhatsApp-owned ``settings`` opt-in around it, without spinning up the Node
bridge or a live account.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers import _plugin
from lingtai.mcp_servers._plugin import CuratedMcpPlugin, CuratedMcpPluginError
from lingtai.mcp_servers.whatsapp import _family, server as whatsapp_server
from lingtai.mcp_servers.whatsapp import manager as whatsapp_mgr
from lingtai.mcp_servers.whatsapp.plugin import (
    WHATSAPP_ACTIONS,
    WHATSAPP_DECLARED_ACTIONS,
    WHATSAPP_PLUGIN,
)
from lingtai.services import mcp_registry
from lingtai.tools.tool_family import ChildTool
from tests._tool_family_schema_helpers import (
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _RecordingManager:
    """Stands in for WhatsAppManager; records every flat action it is handed."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


# ---------------------------------------------------------------------------
# The package ships its own MCP declaration
# ---------------------------------------------------------------------------

def test_whatsapp_package_declaration_matches_the_shipped_curated_catalog_entry():
    """The package owns its launcher; mcp_catalog.json publishes exactly it."""
    catalog = json.loads(
        (_REPO_ROOT / "src/lingtai/mcp_catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["whatsapp"] == WHATSAPP_PLUGIN.mcp_declaration()


def test_declaration_launches_the_declaring_package_and_validates_as_a_record():
    declaration = WHATSAPP_PLUGIN.mcp_declaration()
    assert declaration["transport"] == "stdio"
    assert declaration["command"] == _plugin.PYTHON_PLACEHOLDER
    assert declaration["args"] == ["-m", "lingtai.mcp_servers.whatsapp"]
    assert declaration["source"] == _plugin.CURATED_SOURCE
    # The host's registry validator — unchanged — still accepts the record.
    assert mcp_registry.validate_record(declaration) == (True, None)


def test_catalog_loading_is_unchanged_and_still_the_runtime_source():
    """The descriptor documents the record; it does not replace catalog I/O."""
    assert mcp_registry.load_catalog()["whatsapp"] == WHATSAPP_PLUGIN.mcp_declaration()


# ---------------------------------------------------------------------------
# `manual` is mandatory, reserved, and sourced from the packaged skill
# ---------------------------------------------------------------------------

def test_package_declares_neither_reserved_action_and_plugin_appends_both():
    assert _plugin.MANUAL_ACTION not in WHATSAPP_DECLARED_ACTIONS
    assert "settings" not in WHATSAPP_DECLARED_ACTIONS
    assert WHATSAPP_PLUGIN.settings is True
    assert WHATSAPP_ACTIONS == (*WHATSAPP_DECLARED_ACTIONS, "settings", "manual")
    assert WHATSAPP_ACTIONS[-2] == "settings"
    assert WHATSAPP_ACTIONS[-1] == "manual"


@pytest.mark.parametrize(
    "compose",
    [
        pytest.param(lambda: WHATSAPP_PLUGIN.actions(["send", "manual"]), id="actions"),
        pytest.param(
            lambda: WHATSAPP_PLUGIN.action_input_schemas({"send": {}, "manual": {}}),
            id="schemas",
        ),
        pytest.param(
            lambda: WHATSAPP_PLUGIN.build_family(
                [
                    ChildTool("send", {"type": "object"}, lambda _i: {}),
                    ChildTool("manual", {"type": "object"}, lambda _i: {"hijacked": True}),
                ]
            ),
            id="family",
        ),
    ],
)
def test_a_package_cannot_declare_re_schema_or_rebind_the_reserved_manual(compose):
    with pytest.raises(CuratedMcpPluginError, match="reserved 'manual'"):
        compose()


def test_composed_family_always_carries_a_manual_child_with_a_strict_empty_input():
    family = _family.build_whatsapp_family(None)
    assert family.has_manual()
    assert family.child_names == WHATSAPP_ACTIONS
    assert _family._whatsapp_input_schemas()["manual"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_manual_answers_from_the_packaged_skill_without_entering_the_manager():
    manager = _RecordingManager()
    result = _family.handle_whatsapp(
        manager, {"action": "manual", "input": {}, "reasoning": "read the manual"}
    )
    assert manager.calls == []
    assert result == WHATSAPP_PLUGIN.manual_payload()
    assert result["status"] == "ok"
    assert result["action"] == "manual"
    assert result["skill"] == "whatsapp-mcp-manual"
    skill_path = Path(result["path"])
    assert skill_path.is_absolute() and skill_path.name == "SKILL.md"
    assert result["manual"] == WHATSAPP_PLUGIN.skill_body


def test_manual_payload_is_the_same_document_the_legacy_manager_action_returns():
    """Routing manual through the plugin preserves the existing public result."""
    bare = object.__new__(whatsapp_mgr.WhatsAppManager)
    assert bare._manual({}) == WHATSAPP_PLUGIN.manual_payload()


def test_manual_still_requires_root_reasoning_like_every_other_action():
    manager = _RecordingManager()
    rejected = _family.handle_whatsapp(manager, {"action": "manual", "input": {}})
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "INVALID_ARGUMENT"
    assert rejected["message"] == "reasoning is required"
    # ... and its input stays strictly empty.
    assert _family.handle_whatsapp(
        manager, {"action": "manual", "input": {"topic": "send"}, "reasoning": "x"}
    )["status"] == "failed"
    assert manager.calls == []


# ---------------------------------------------------------------------------
# The public envelope keeps its strict shape while adding owner settings
# ---------------------------------------------------------------------------

def test_public_schema_keeps_the_strict_action_family_shape():
    schema = _family.WHATSAPP_SCHEMA
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == list(WHATSAPP_ACTIONS)
    assert "whatsapp-mcp-manual" in schema["properties"]["action"]["description"]
    # One root ``oneOf`` branch per action: declared actions, then the
    # owner-opted ``settings``, then ``manual`` — discriminated by const.
    assert_compact_envelope(schema, list(WHATSAPP_ACTIONS))
    assert branch_actions(schema) == [*WHATSAPP_DECLARED_ACTIONS, "settings", "manual"]
    branches = action_input_schemas(schema)
    canonical = _family._whatsapp_input_schemas()
    for action in WHATSAPP_DECLARED_ACTIONS:
        assert branches[action] == canonical[action], action
    assert branches["manual"] == canonical["manual"]
    assert branches["settings"] == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_declared_actions_still_dispatch_flat_into_the_manager():
    manager = _RecordingManager()
    result = _family.handle_whatsapp(
        manager,
        {"action": "send", "input": {"to": "1", "text": "hi"}, "reasoning": "probe"},
    )
    assert result["status"] == "ok"
    assert manager.calls == [{"action": "send", "to": "1", "text": "hi"}]


# ---------------------------------------------------------------------------
# The server advertises the packaged identity, not a hand-copied one
# ---------------------------------------------------------------------------

def test_server_lists_the_packaged_registry_name_as_the_tool_name():
    server = whatsapp_server.build_server(None)
    assert server is not None
    # The listed tool name and the call-tool routing gate both come from the
    # same descriptor, so they cannot silently disagree.
    assert WHATSAPP_PLUGIN.name == "whatsapp"
    assert WHATSAPP_PLUGIN.server_name == "lingtai-whatsapp"


def test_resources_manifest_actions_are_sourced_from_the_plugin_descriptor():
    from lingtai.mcp_servers.whatsapp.resources import manifest

    profile = manifest()
    assert profile["tools"]["name"] == WHATSAPP_PLUGIN.name
    assert profile["tools"]["actions"] == list(WHATSAPP_ACTIONS)
    assert profile["name"] == WHATSAPP_PLUGIN.server_name


# ---------------------------------------------------------------------------
# Descriptor defects fail loudly at import time
# ---------------------------------------------------------------------------

def test_descriptor_rejects_a_package_that_is_not_its_own_module():
    with pytest.raises(CuratedMcpPluginError, match="must be the 'whatsapp' module"):
        CuratedMcpPlugin(
            name="whatsapp",
            package="lingtai.mcp_servers.feishu",
            server_name="lingtai-whatsapp",
            summary="s",
            homepage="h",
            skill_name="whatsapp-mcp-manual",
        )


def test_descriptor_rejects_a_manual_that_is_not_the_packaged_skill():
    with pytest.raises(CuratedMcpPluginError, match="declares name"):
        CuratedMcpPlugin(
            name="whatsapp",
            package="lingtai.mcp_servers.whatsapp",
            server_name="lingtai-whatsapp",
            summary="s",
            homepage="h",
            skill_name="somebody-elses-manual",
        )
