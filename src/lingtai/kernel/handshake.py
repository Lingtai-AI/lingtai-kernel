"""Handshake utility — pure address resolution for the agent network.

Agent **presence and liveness** (``is_agent`` / ``is_human`` / ``is_alive`` and
manifest observation) moved to the Core-owned
``lingtai.kernel.agent_presence.AgentPresenceStorePort`` and its production
``PosixAgentPresenceStoreAdapter``; callers observe a target-bound presence store
and apply Core policy instead of importing presence functions from here. This
module now owns only ``resolve_address`` — pure path arithmetic with no file,
JSON, symlink, or clock access — used by mail routing, karma/avatar/cpr address
resolution, and the read-only network topology crawler.
"""
from __future__ import annotations

from pathlib import Path


def resolve_address(address: str | Path, base_dir: str | Path) -> Path:
    """Resolve an agent address to an absolute Path.

    Relative names (e.g. "本我") are joined with base_dir.
    Absolute paths are returned as-is.
    """
    p = Path(address)
    if p.is_absolute():
        return p
    return Path(base_dir) / address
