"""Codex auth POOL selection (provider ``codex-pool``).

This module load-balances a Codex agent across several existing Codex OAuth
token files with a *sticky-per-agent-session* choice. It never touches provider
``codex``: it only computes which ``codex-auth.json``-shaped token file the new
``codex-pool`` provider should read, and the caller injects that as the ordinary
``codex_auth_path`` so the existing Codex adapter / ``CodexTokenManager`` handle
the token itself.

Design constraints (Jason's spec):

  * The pool file is NON-SECRET — it lists token *paths* and weights only. Token
    contents are never read or logged here; we only stat/parse the pool file.
  * Selection is weighted (cumulative weights, no giant list expansion) and
    *sticky* within one agent wake/session: the seed is derived from the stable
    ``codex_session_anchor`` plus the agent's ``.agent.json`` ``started_at`` and
    deliberately EXCLUDES ``molt_count`` — an auth account must not rotate on a
    molt (that is what the endpoint pool does, not this).
  * Missing / empty / invalid pool -> return ``None`` so the caller falls back to
    the legacy default Codex token path for the ``codex-pool`` provider.

Public helpers:

  * :func:`resolve_codex_tui_dir`      -> the ``~/.lingtai-tui`` base dir.
  * :func:`resolve_codex_pool_path`    -> the pool file path (override-aware).
  * :func:`load_codex_auth_pool`       -> validated list of enabled accounts.
  * :func:`select_codex_pool_auth_path`-> chosen token path, or ``None``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# Manifest / provider-defaults override for the pool file location. Parallels the
# existing ``codex_auth_path`` naming used for a single token file.
POOL_PATH_KEY = "codex_auth_pool_path"

# Default pool file name inside the TUI dir.
_DEFAULT_POOL_FILENAME = "codex-auth-pool.json"

# Legacy single-token default, used as the fallback when the pool is unusable.
_LEGACY_TOKEN_FILENAME = "codex-auth.json"


def resolve_codex_tui_dir() -> Path:
    """Return the LingTai TUI base directory (``$LINGTAI_TUI_DIR`` or default).

    Relative account paths in the pool file resolve against this directory.
    """
    tui_dir = os.environ.get("LINGTAI_TUI_DIR", "~/.lingtai-tui")
    return Path(tui_dir).expanduser()


def resolve_codex_pool_path(defaults: dict | None = None) -> Path:
    """Resolve the pool file path.

    Priority:
      1. An explicit, non-blank ``codex_auth_pool_path`` in provider defaults —
         ``~``/absolute honored; a relative path resolves against the TUI dir.
      2. ``$LINGTAI_TUI_DIR/codex-auth-pool.json`` (or ``~/.lingtai-tui/...``).

    The path is non-secret; nothing here reads its contents.
    """
    tui_dir = resolve_codex_tui_dir()
    override = (defaults or {}).get(POOL_PATH_KEY)
    if isinstance(override, str) and override.strip():
        return _resolve_relative_to_tui(override.strip(), tui_dir)
    return tui_dir / _DEFAULT_POOL_FILENAME


def legacy_codex_token_path() -> Path:
    """The legacy single-token default (``<tui_dir>/codex-auth.json``).

    Mirrors ``CodexTokenManager``'s own default so the ``codex-pool`` fallback
    lands on exactly the same file the manager would have used with no path.
    """
    return resolve_codex_tui_dir() / _LEGACY_TOKEN_FILENAME


def _resolve_relative_to_tui(raw: str, tui_dir: Path) -> Path:
    """Resolve a pool ``path``: ``~``/absolute honored, else relative to TUI dir."""
    if raw.startswith("~"):
        return Path(raw).expanduser()
    p = Path(raw)
    if p.is_absolute():
        return p
    return tui_dir / p


def load_codex_auth_pool(pool_path: Path) -> list[dict]:
    """Parse the pool file into a list of validated, enabled accounts.

    Each returned entry is ``{"path": <str>, "weight": <positive int>}``. An
    account is dropped when:
      * ``enabled`` is explicitly ``False``;
      * ``path`` is missing / not a non-blank string;
      * ``weight`` is present but non-numeric or not strictly positive. A
        missing ``weight`` defaults to ``1`` (a hand-edited pool file may omit
        it; the TUI always writes an explicit weight).

    A missing file, unreadable file, malformed JSON, or a non-dict / non-list
    structure yields ``[]`` (no exception) so the caller falls back cleanly.
    Token contents are never read here — only the pool file's own JSON.
    """
    try:
        raw = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    accounts = raw.get("accounts")
    if not isinstance(accounts, list):
        return []

    valid: list[dict] = []
    for acct in accounts:
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
    return valid


def _coerce_weight(raw) -> int | None:
    """Return a strictly-positive int weight, or ``None`` for invalid input.

    Bools are rejected (``True``/``False`` are not meaningful weights). Floats
    that are whole positive numbers are accepted (e.g. ``2.0`` -> ``2``).
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float):
        if raw > 0 and raw.is_integer():
            return int(raw)
        return None
    return None


def _selection_seed(defaults: dict | None, tui_dir: Path) -> str:
    """Build the stable per-agent-session selection seed.

    Combines the ``codex_session_anchor`` with the agent's ``started_at`` (read
    from ``<anchor-dir>/.agent.json``) so the choice is stable across requests
    and molts within one wake and changes on a new agent session. Deliberately
    EXCLUDES ``molt_count``. When ``started_at`` is unavailable, falls back to
    ``agent_id`` -> ``address`` -> the anchor path itself.
    """
    d = defaults or {}
    anchor = d.get("codex_session_anchor")
    anchor_str = anchor if isinstance(anchor, str) and anchor else str(tui_dir)

    session_marker = _read_started_at(anchor_str)
    if session_marker is None:
        for key in ("agent_id", "address"):
            val = d.get(key)
            if isinstance(val, str) and val:
                session_marker = val
                break
    if session_marker is None:
        session_marker = anchor_str

    return f"{anchor_str}\0{session_marker}"


def _read_started_at(anchor_str: str) -> str | None:
    """Read ``started_at`` from ``<anchor-dir>/.agent.json``; ``None`` on failure.

    The ``.agent.json`` sits next to the anchor (``init.json``). A missing /
    malformed file or absent ``started_at`` yields ``None`` — no exception, no
    token content touched.
    """
    try:
        agent_json = Path(anchor_str).parent / ".agent.json"
        data = json.loads(agent_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("started_at")
    if val is None:
        return None
    return str(val)


def _weighted_pick(accounts: list[dict], seed: str) -> dict:
    """Deterministically pick one account by cumulative weight from ``seed``.

    Hashes ``seed`` into a stable integer and maps it into ``[0, total_weight)``
    via cumulative weights — no per-unit list expansion, so a huge weight stays
    O(len(accounts)). The same seed + same accounts always picks the same one.
    """
    total = sum(a["weight"] for a in accounts)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    point = int.from_bytes(digest[:8], "big") % total
    cumulative = 0
    for acct in accounts:
        cumulative += acct["weight"]
        if point < cumulative:
            return acct
    # Unreachable (point < total == final cumulative) — defensive fallback.
    return accounts[-1]


def select_codex_pool_auth_path(defaults: dict | None = None) -> str | None:
    """Select the Codex token path for the ``codex-pool`` provider.

    Returns the resolved token file path (a filesystem string) chosen stickily
    for this agent session by weighted selection, or ``None`` when the pool file
    is missing / has no valid enabled accounts — in which case the caller falls
    back to the legacy default Codex token path.

    Pure path computation: no token file is read and nothing secret is logged.
    """
    tui_dir = resolve_codex_tui_dir()
    pool_path = resolve_codex_pool_path(defaults)
    accounts = load_codex_auth_pool(pool_path)
    if not accounts:
        return None

    seed = _selection_seed(defaults, tui_dir)
    chosen = _weighted_pick(accounts, seed)
    return str(_resolve_relative_to_tui(chosen["path"], tui_dir))
