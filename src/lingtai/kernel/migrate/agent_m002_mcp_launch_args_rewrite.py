"""agent m002 — rewrite legacy curated MCP launch module args.

The curated MCP implementations now live only under
``lingtai.mcp_servers.<name>``. Older agent workdirs may still carry
``["-m", "lingtai_<name>"]`` in ``mcp_registry.jsonl`` or ``init.json``.
This migration rewrites those launch args in-place before MCP subprocesses are
started.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .migrate import MigrationWorkspacePort

_LEGACY_TO_CANONICAL = {
    "lingtai_imap": "lingtai.mcp_servers.imap",
    "lingtai_telegram": "lingtai.mcp_servers.telegram",
    "lingtai_feishu": "lingtai.mcp_servers.feishu",
    "lingtai_wechat": "lingtai.mcp_servers.wechat",
    "lingtai_whatsapp": "lingtai.mcp_servers.whatsapp",
}


def _rewrite_args(args: Any) -> bool:
    """Rewrite legacy ``-m lingtai_<name>`` module args in-place."""
    if not isinstance(args, list):
        return False
    changed = False
    for idx, value in enumerate(args):
        if value in _LEGACY_TO_CANONICAL and idx > 0 and args[idx - 1] == "-m":
            args[idx] = _LEGACY_TO_CANONICAL[value]
            changed = True
    return changed


def _rewrite_registry(workspace: MigrationWorkspacePort) -> int:
    from .migrate import MCP_REGISTRY_REF

    text = workspace.read_entry(MCP_REGISTRY_REF)
    if text is None:
        return 0
    lines = text.splitlines()
    out: list[str] = []
    changed_count = 0
    changed_file = False
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if isinstance(record, dict) and _rewrite_args(record.get("args")):
            changed_count += 1
            changed_file = True
            out.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
        else:
            out.append(line)
    if changed_file:
        workspace.atomic_replace_entry(
            MCP_REGISTRY_REF, "\n".join(out) + ("\n" if lines else "")
        )
    return changed_count


def _rewrite_init(workspace: MigrationWorkspacePort) -> int:
    from .migrate import INIT_DOCUMENT_REF

    text = workspace.read_entry(INIT_DOCUMENT_REF)
    if text is None:
        return 0
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("init.json did not contain a JSON object")
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        return 0
    changed_count = 0
    for record in mcp.values():
        if isinstance(record, dict) and _rewrite_args(record.get("args")):
            changed_count += 1
    if changed_count:
        workspace.atomic_replace_entry(
            INIT_DOCUMENT_REF, json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        )
    return changed_count


def migrate_mcp_launch_args_rewrite(workspace: MigrationWorkspacePort) -> None:
    """Rewrite stale curated MCP ``python -m lingtai_<name>`` launch args."""
    registry_rewrites = _rewrite_registry(workspace)
    init_rewrites = _rewrite_init(workspace)
    if registry_rewrites or init_rewrites:
        workspace.append_audit(
            "mcp_launch_args_rewrite_migrated",
            {
                "registry_rewrites": registry_rewrites,
                "init_rewrites": init_rewrites,
            },
        )
