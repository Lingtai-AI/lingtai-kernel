"""Curated-MCP plugin packaging invariants, proven on the IMAP slice.

Mirrors ``tests/test_curated_mcp_plugin_package.py`` (the Telegram reference
slice) for IMAP: the shipped ``mcp_catalog.json`` entry must equal
``IMAP_PLUGIN.mcp_declaration()``, the reserved ``settings``/``manual`` actions
must be composed by the plugin rather than declared by the package, and the
server must advertise the packaged identity rather than a hand-copied one.
"""
from __future__ import annotations

import json
from pathlib import Path

from lingtai.mcp_servers import _plugin
from lingtai.mcp_servers.imap import _family
from lingtai.mcp_servers.imap import manager as imap_mgr
from lingtai.mcp_servers.imap import server as imap_server
from lingtai.mcp_servers.imap.plugin import IMAP_ACTIONS, IMAP_DECLARED_ACTIONS, IMAP_PLUGIN
from lingtai.services import mcp_registry
from tests._tool_family_schema_helpers import (
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _RecordingManager:
    """Stands in for IMAPMailManager; records every flat action it is handed."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


# ---------------------------------------------------------------------------
# The package ships its own MCP declaration
# ---------------------------------------------------------------------------

def test_imap_package_declaration_matches_the_shipped_curated_catalog_entry():
    catalog = json.loads(
        (_REPO_ROOT / "src/lingtai/mcp_catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["imap"] == IMAP_PLUGIN.mcp_declaration()


def test_declaration_launches_the_declaring_package_and_validates_as_a_record():
    declaration = IMAP_PLUGIN.mcp_declaration()
    assert declaration["transport"] == "stdio"
    assert declaration["command"] == _plugin.PYTHON_PLACEHOLDER
    assert declaration["args"] == ["-m", "lingtai.mcp_servers.imap"]
    assert declaration["source"] == _plugin.CURATED_SOURCE
    assert mcp_registry.validate_record(declaration) == (True, None)


def test_catalog_loading_is_unchanged_and_still_the_runtime_source():
    assert mcp_registry.load_catalog()["imap"] == IMAP_PLUGIN.mcp_declaration()


# ---------------------------------------------------------------------------
# `manual` is mandatory, reserved, and sourced from the packaged skill
# ---------------------------------------------------------------------------

def test_package_declares_neither_reserved_action_and_plugin_orders_both():
    assert "settings" not in IMAP_DECLARED_ACTIONS
    assert _plugin.MANUAL_ACTION not in IMAP_DECLARED_ACTIONS
    assert IMAP_ACTIONS == (*IMAP_DECLARED_ACTIONS, "settings", "manual")
    assert IMAP_ACTIONS[-2] == "settings"
    assert IMAP_ACTIONS[-1] == "manual"


def test_composed_family_carries_both_reserved_children_with_strict_empty_input():
    family = _family.build_imap_family(None)
    assert family.has_manual()
    assert family.child_names == IMAP_ACTIONS
    assert _family._imap_input_schemas()["manual"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert _family._imap_input_schemas()["settings"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_manual_answers_from_the_packaged_skill_without_entering_the_manager():
    manager = _RecordingManager()
    result = _family.handle_imap(
        manager, {"action": "manual", "input": {}, "reasoning": "read the manual"}
    )
    assert manager.calls == []
    assert result == IMAP_PLUGIN.manual_payload()
    assert result["status"] == "ok"
    assert result["action"] == "manual"
    assert result["skill"] == "imap-mcp-manual"
    skill_path = Path(result["path"])
    assert skill_path.is_absolute() and skill_path.name == "SKILL.md"
    assert result["manual"] == IMAP_PLUGIN.skill_body


def test_manual_payload_is_the_same_document_the_legacy_manager_action_returns():
    """Routing manual through the plugin preserves the existing public result."""
    bare = object.__new__(imap_mgr.IMAPMailManager)
    assert bare._manual() == IMAP_PLUGIN.manual_payload()


def test_manual_still_requires_root_reasoning_like_every_other_action():
    manager = _RecordingManager()
    rejected = _family.handle_imap(manager, {"action": "manual", "input": {}})
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "INVALID_ARGUMENT"
    assert rejected["message"] == "reasoning is required"
    assert _family.handle_imap(
        manager, {"action": "manual", "input": {"topic": "send"}, "reasoning": "x"}
    )["status"] == "failed"
    assert manager.calls == []


# ---------------------------------------------------------------------------
# The server advertises the packaged identity, not a hand-copied one
# ---------------------------------------------------------------------------

def test_server_profile_is_sourced_from_the_plugin_descriptor():
    profile = imap_server.lingtai_profile(None)
    assert profile["server"]["name"] == IMAP_PLUGIN.name == "imap"
    assert profile["server"]["homepage"] == IMAP_PLUGIN.homepage
    assert profile["interfaces"]["agent_entrypoints"]["tools"] == [IMAP_PLUGIN.name]


# ---------------------------------------------------------------------------
# The public envelope shape is unchanged by the packaging
# ---------------------------------------------------------------------------

def test_public_schema_keeps_the_strict_action_family_shape():
    schema = _family.IMAP_SCHEMA
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == list(IMAP_ACTIONS)
    assert "imap-mcp-manual" in schema["properties"]["action"]["description"]
    # One root ``oneOf`` branch per action in plugin order, discriminated by
    # its action const and carrying that action's own canonical input.
    assert_compact_envelope(schema, list(IMAP_ACTIONS))
    assert branch_actions(schema) == [*IMAP_DECLARED_ACTIONS, "settings", "manual"]
    branches = action_input_schemas(schema)
    canonical = _family._imap_input_schemas()
    for action in IMAP_DECLARED_ACTIONS:
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
    result = _family.handle_imap(
        manager,
        {"action": "send", "input": {"address": "a@b.com", "message": "hi"}, "reasoning": "probe"},
    )
    assert result["status"] == "ok"
    assert manager.calls == [{"action": "send", "address": "a@b.com", "message": "hi"}]
