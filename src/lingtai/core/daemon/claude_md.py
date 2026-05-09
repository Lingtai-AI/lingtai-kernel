"""Generate CLAUDE.md for daemon CLI emanations.

Teaches a Claude Code session the minimum needed to participate in
the lingtai network: how to read/write mail, access parent's codex,
and report results.
"""
from __future__ import annotations

from ..email_contract import CODEX_CONTRACT, EMAIL_CONTRACT


def build_emanation_claude_md(
    *,
    emanation_id: str,
    parent_address: str,
    lingtai_root: str,
    codex_path: str,
    task: str,
) -> str:
    """Build CLAUDE.md content for a daemon emanation."""
    return f"""\
# Daemon Emanation {emanation_id}

You are an ephemeral subagent (daemon emanation) in a lingtai agent network.
Your parent is `{parent_address}`. You will be terminated when your task
completes — write findings to files, not just to stdout.

## Your Task

{task}

## Network Location

- Lingtai root: `{lingtai_root}`
- Parent address: `{parent_address}`
- Parent's mailbox: `{lingtai_root}/{parent_address}/mailbox/inbox/`
- Parent codex: `{codex_path}`

## Communication — Email

{EMAIL_CONTRACT}

When sending mail, use these values for your identity block:
- `agent_name`: `{emanation_id}`
- `address`: `{parent_address}`
- `via`: `claude-code`

## Knowledge — Codex (Read-Only)

You can read the parent's codex at `{codex_path}` for context about
what the parent has learned. **Do NOT write to the codex** — only the
parent agent manages it.

{CODEX_CONTRACT}

## Guidelines

- Complete your task thoroughly, then state "task done" and summarize
- Write detailed output to files if the results are substantial
- If you need to communicate with other agents, use email
- You are ephemeral — your session ends when the task is done
"""
