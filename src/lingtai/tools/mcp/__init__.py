"""MCP capability — per-agent registry of MCP servers (pure presentation).

Symmetric to the ``knowledge`` / ``skills`` capabilities:

- Per-agent registry lives at ``<agent>/mcp_registry.jsonl`` (sibling to
  ``init.json``). One JSON record per line.
- The capability scans the registry on setup, validates each line, and renders
  the registry as XML into the system prompt's ``mcp`` section.
- Boot-time decompression: any name in ``init.json``'s ``addons: [...]`` list
  that isn't already in the registry gets appended from the kernel-shipped
  catalog (``lingtai/mcp_catalog.json``). Append-only, idempotent.
- All registry mutations (register, deregister, update) happen via file
  operations from the agent (``write``, ``edit``). The capability provides
  guidance via the umbrella SKILL.md, with ``info`` re-rendering the prompt
  section and reporting health while ``manual`` returns the manual body.

Tool surface: ``info`` returns the current registry and a runtime health
snapshot without the manual body; ``manual`` returns the umbrella manual body on
demand.

Ownership: this module is the agent-callable *tool* slice only. The registry
machinery it renders (validation, JSONL I/O, catalog load, identity projection,
addon decompression, XML build) is a service and lives at
``lingtai/services/mcp_registry.py``; it is imported lazily inside ``setup`` and
the handlers, per the ``lingtai.tools → lingtai`` lazy-back-edge rule.

Usage: ``Agent(capabilities=["mcp"])`` or via init.json.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from lingtai.kernel.tool_dispatch import dispatch_action

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent

PROVIDERS = {"providers": [], "default": "builtin"}


# ---------------------------------------------------------------------------
# Reconciliation (shared by setup and the ``info`` action)
# ---------------------------------------------------------------------------

def _registered_entry(record: dict, identity: dict | None) -> dict:
    """Build one ``registered`` entry, attaching identity only when present."""
    entry = {"name": record["name"], "summary": record["summary"]}
    if identity and identity.get("accounts"):
        entry["identity"] = identity
    return entry


def _reconcile(agent: "BaseAgent") -> dict:
    """Read registry, render into prompt, return health snapshot."""
    from lingtai.services.mcp_registry import (
        read_registry,
        read_identities,
        _build_registry_xml,
        _registry_path,
    )

    working_dir = agent._working_dir
    records, problems = read_registry(working_dir)
    identities = read_identities(working_dir)

    xml = _build_registry_xml(records, identities)
    agent.update_system_prompt("mcp", xml, protected=True)

    # Health: the umbrella manual must be present.
    intrinsic_dir = working_dir / ".library" / "intrinsic"
    manual_path = intrinsic_dir / "capabilities" / "mcp" / "SKILL.md"
    result = {
        "status": "ok",
        "registry_path": str(_registry_path(working_dir)),
        "registered_count": len(records),
        "registered": [
            _registered_entry(r, identities.get(r["name"]))
            for r in records
        ],
        "problems": problems,
    }
    return result


def _manual(agent: "BaseAgent") -> dict:
    manual_path = agent._working_dir / ".library" / "intrinsic" / "capabilities" / "mcp" / "SKILL.md"
    if not manual_path.is_file():
        return {
            "status": "degraded",
            "mcp_manual": "",
            "manual_path": str(manual_path),
            "error": "mcp manual missing — initializer may have failed or capability not installed correctly",
        }
    return {
        "status": "ok",
        "mcp_manual": manual_path.read_text(encoding="utf-8"),
        "manual_path": str(manual_path),
    }


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

_DESCRIPTION = (
    "SIGNPOST ONLY: this tool does not register, activate, configure, or "
    "troubleshoot MCP servers by itself. `info` only re-reads the registry and "
    "returns registry health; `manual` returns the mcp-manual body. "
    "Your per-agent MCP server registry. The <registered_mcp> catalog in your "
    "system prompt lists every MCP server currently registered. Before using "
    "this tool (registering, deregistering, updating, or troubleshooting MCP "
    "servers), read the `mcp-manual` skill — call `manual` to fetch its body "
    "(registration contract, file paths, schema), and call `info` for the current "
    "registry health snapshot; no exceptions. To register, deregister, or update MCPs, edit "
    "mcp_registry.jsonl directly with write/edit and call "
    "system(action=\"refresh\")."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["info", "manual"],
            "description": (
                "info: signpost-only action; re-reads the registry and returns "
                "a runtime health snapshot (registry contents, problems, registry path) "
                "without the manual body. manual: return only the mcp-manual skill body. "
                "Neither action mutates MCP configuration."
            ),
        },
    },
    "required": ["action"],
}


def get_description(lang: str = "en") -> str:
    return _DESCRIPTION


def get_schema(lang: str = "en") -> dict:
    return _SCHEMA


def setup(agent: "BaseAgent", **_ignored) -> None:
    """Set up the mcp capability.

    The capability is pure presentation: it reads the registry from disk and
    renders it into the system prompt. Decompression of init.json's addons:
    field happens in the Agent initializer via
    ``lingtai.services.mcp_registry.decompress_addons()`` before setup is called.
    """
    _reconcile(agent)

    def handle_mcp(args: dict) -> dict:
        return dispatch_action(
            args,
            {
                "info": lambda _args: _reconcile(agent),
                "manual": lambda _args: _manual(agent),
            },
            unknown=lambda action: {
                "status": "error",
                "message": f"unknown action: {action!r}, only 'info' or 'manual' is supported",
            },
        )

    agent.add_tool(
        "mcp",
        schema=get_schema(),
        handler=handle_mcp,
        description=get_description(),
        glossary_package=__package__,
    )
