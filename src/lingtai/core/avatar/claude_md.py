"""Generate CLAUDE.md for Claude Code avatar agents.

The CLAUDE.md is the primary integration mechanism — it teaches a
Claude Code session how to participate as a persistent peer in the
lingtai network via filesystem-based email and codex access.
"""
from __future__ import annotations

from ..email_contract import CODEX_CONTRACT, EMAIL_CONTRACT, LIBRARY_CONTRACT
from ..codex.bridge import build_codex_catalog


def build_avatar_claude_md(
    *,
    agent_name: str,
    agent_id: str,
    parent_address: str,
    lingtai_root: str,
    mission: str,
    codex_path: str = "",
    comment: str = "",
) -> str:
    """Build CLAUDE.md for a Claude Code avatar."""
    parts = [
        f"# Agent: {agent_name}",
        "",
        f"You are **{agent_name}**, a persistent agent in a lingtai network.",
        f"Your agent ID is `{agent_id}`.",
        f"Your parent agent is `{parent_address}`.",
        "",
    ]

    if comment:
        parts += [f"**Note from admin**: {comment}", ""]

    parts += [
        "## Your Mission",
        "",
        mission,
        "",
        "## Network Location",
        "",
        f"- **Your working directory**: (current directory)",
        f"- **Network root**: `{lingtai_root}`",
        f"- **Your mailbox**: `./mailbox/inbox/` (incoming) and `./mailbox/sent/` (outgoing)",
        f"- **Your codex**: `./codex/codex.json`",
        f"- **Parent's mailbox**: `{lingtai_root}/{parent_address}/mailbox/inbox/`",
        "",
        "## Communication — Email",
        "",
        EMAIL_CONTRACT,
        "",
        "When sending mail, use these values for your identity block:",
        f"- `agent_id`: `{agent_id}`",
        f"- `agent_name`: `{agent_name}`",
        f"- `address`: `{agent_name}`",
        f'- `via`: `"claude-code"`',
        "",
        "## Knowledge — Codex",
        "",
        CODEX_CONTRACT,
        "",
    ]

    # Inject current codex catalog if path is available
    if codex_path:
        catalog = build_codex_catalog(codex_path)
        parts += [
            "### Current Codex Contents",
            "",
            catalog,
            "",
        ]

    parts += [
        "## Skill Library",
        "",
        LIBRARY_CONTRACT,
        "",
        "## Lifecycle",
        "",
        "- You are a persistent agent — you will receive mail and prompts over time",
        "- When you complete your mission, email your parent with the results",
        "- If you encounter problems you cannot resolve, email your parent for help",
        "- Do NOT exit or terminate — your wrapper process manages your lifecycle",
        "",
    ]
    return "\n".join(parts)
