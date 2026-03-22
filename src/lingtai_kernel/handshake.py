"""Handshake utility — validate agent presence and liveness by working dir path.

Used by FilesystemMailService (mail delivery), system intrinsic (karma/nirvana
actions), and lingtai's revive logic.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def is_agent(path: str | Path) -> bool:
    """Check if an agent exists at *path* (has .agent.json)."""
    return (Path(path) / ".agent.json").is_file()


def is_alive(path: str | Path, threshold: float = 2.0) -> bool:
    """Check if the agent at *path* has a fresh heartbeat.

    Returns False if heartbeat file is missing, unreadable, or older
    than *threshold* seconds.
    """
    hb = Path(path) / ".agent.heartbeat"
    if not hb.is_file():
        return False
    try:
        ts = float(hb.read_text().strip())
    except (ValueError, OSError):
        return False
    return time.time() - ts < threshold


def manifest(path: str | Path) -> dict:
    """Read and return .agent.json contents.

    Raises FileNotFoundError if .agent.json does not exist.
    Raises json.JSONDecodeError if file is not valid JSON.
    """
    agent_json = Path(path) / ".agent.json"
    if not agent_json.is_file():
        raise FileNotFoundError(f"No .agent.json at {path}")
    return json.loads(agent_json.read_text())
