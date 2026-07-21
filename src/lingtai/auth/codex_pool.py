"""Codex auth pool file parsing — non-secret path/weight resolution only.

Thin-wrapper refactor (spec v3):
  * Pool file resolution + validation lives here — non-secret path/weight parsing.
  * Account selection moved to :mod:`lingtai.auth.codex_account_source`.
  * STRUCTURAL error classification (429 + ``usage_limit_reached``) lives in
    :mod:`lingtai.auth.codex` (:func:`lingtai.auth.codex._is_usage_limit_reached_error`)
    because Codex core owns the failure/AED decision, not this pool-parsing module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Manifest / provider-defaults override for the pool file location.
POOL_PATH_KEY = "codex_auth_pool_path"

# Default pool file name inside the TUI dir.
_DEFAULT_POOL_FILENAME = "codex-auth-pool.json"

# Legacy single-token default, used as fallback when the pool is unusable.
_LEGACY_TOKEN_FILENAME = "codex-auth.json"

# Sentinel emitted for absolute source refs.
ABSOLUTE_REF_REDACTED = "<absolute-path-redacted>"


def resolve_codex_tui_dir() -> Path:
    """Return the LingTai TUI base directory (``$LINGTAI_TUI_DIR`` or default)."""
    tui_dir = os.environ.get("LINGTAI_TUI_DIR", "~/.lingtai-tui")
    return Path(tui_dir).expanduser()


def resolve_codex_pool_path(defaults: dict | None = None) -> Path:
    """Resolve the pool file path.

    Priority:
      1. Explicit ``codex_auth_pool_path`` in provider defaults.
      2. ``$LINGTAI_TUI_DIR/codex-auth-pool.json``.
    """
    tui_dir = resolve_codex_tui_dir()
    override = (defaults or {}).get(POOL_PATH_KEY)
    if isinstance(override, str) and override.strip():
        return _resolve_relative_to_tui(override.strip(), tui_dir)
    return tui_dir / _DEFAULT_POOL_FILENAME


def legacy_codex_token_path() -> Path:
    """The legacy single-token default (``<tui_dir>/codex-auth.json``)."""
    return resolve_codex_tui_dir() / _LEGACY_TOKEN_FILENAME


def _resolve_relative_to_tui(raw: str, tui_dir: Path) -> Path:
    """Resolve a pool ``path``: ``~``/absolute honored, else relative to TUI dir."""
    if raw.startswith("~"):
        return Path(raw).expanduser()
    p = Path(raw)
    if p.is_absolute():
        return p
    return tui_dir / p


def load_codex_auth_pool(pool_path: Path, model: str | None = None) -> list[dict]:
    """Parse the pool file into a list of validated, enabled accounts.

    Each returned entry is ``{"path": <str>, "weight": <positive int>}``.
    Invalid/disabled/blank-path/bad-weight entries are dropped silently.
    A model-classified (v2) file has a top-level ``models`` dict — only the
    exact, case-sensitive ``model`` category is eligible.  A missing file or
    unparseable JSON yields ``[]``.
    """
    return _load_pool_entries(pool_path, model)[0]


def _load_pool_entries(pool_path: Path, model: str | None) -> tuple[list[dict], bool]:
    """Parse + validate the pool file; return ``(accounts, classified)``."""
    try:
        raw = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    if not isinstance(raw, dict):
        return [], False
    models = raw.get("models")
    if isinstance(models, dict):
        entries = models.get(model)
        classified = True
    else:
        entries = raw.get("accounts")
        classified = False
    if not isinstance(entries, list):
        return [], classified

    valid: list[dict] = []
    for acct in entries:
        if not isinstance(acct, dict):
            continue
        if acct.get("enabled", True) is False:
            continue
        path = acct.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        weight = _coerce_weight(acct.get("weight", 1))
        if weight is None:
            continue
        valid.append({"path": path.strip(), "weight": weight})
    return valid, classified


def _coerce_weight(raw) -> int | None:
    """Return a strictly-positive int weight, or ``None`` for invalid input."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float):
        if raw > 0 and raw.is_integer():
            return int(raw)
        return None
    return None


def _safe_source_ref(raw_ref: str) -> str:
    """Return a non-secret source_ref: verbatim if relative, else redacted."""
    expanded = os.path.expanduser(raw_ref) if raw_ref.startswith("~") else raw_ref
    if os.path.isabs(expanded):
        return ABSOLUTE_REF_REDACTED
    return raw_ref
