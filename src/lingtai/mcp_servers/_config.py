"""Shared config loading helpers for curated MCP server wrappers.

These helpers factor out the common env-var to path to strict-JSON sequence
used by the simple addon loaders. Each addon keeps its public ``load_config()``
wrapper and return type.

WeChat is intentionally not a client: it has a two-candidate compatibility
resolver, status diagnostics, and a second credentials file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["resolve_config_path", "load_config_file"]


def resolve_config_path(
    env_name: str,
    *,
    label: str,
    missing_env_msg: str | None = None,
) -> Path:
    """Resolve the config path named by ``env_name``.

    Relative paths resolve against ``LINGTAI_AGENT_DIR`` or the current working
    directory when that env var is unset.
    """
    raw = os.environ.get(env_name)
    if not raw:
        raise ValueError(
            missing_env_msg
            or f"{env_name} env var not set — point it at your {label} "
            "config JSON file"
        )

    path = Path(raw).expanduser()
    if not path.is_absolute():
        base = Path(os.environ.get("LINGTAI_AGENT_DIR", os.getcwd()))
        path = base / path
    if not path.is_file():
        raise FileNotFoundError(f"{label} config not found: {path}")
    return path


def load_config_file(
    env_name: str,
    *,
    label: str,
    missing_env_msg: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Resolve and load a strict JSON config file."""
    path = resolve_config_path(
        env_name,
        label=label,
        missing_env_msg=missing_env_msg,
    )
    return json.loads(path.read_text(encoding="utf-8")), path
