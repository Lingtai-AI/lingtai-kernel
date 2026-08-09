"""Nudge: warn the agent when its working directory grows past a threshold.

The agent loop runs this check once per heartbeat. The recursive directory
walk is throttled to one probe per UTC day (a bounded observation cost), while
``upsert``/``remove`` are re-evaluated on every heartbeat against the persisted
last observation and the current ``LINGTAI_NUDGE_FOLDER_SIZE_GB`` value. That
keeps the shared global Nudge policy live: a dismissed finding returns once the
global repeat interval expires, ``LINGTAI_NUDGE_ENABLED=off`` then ``on``
restores the still-current finding the same day, and a transient store failure
is retried on the next heartbeat without an extra directory walk.

The threshold is read from the environment at every evaluation, so a changed
value applies immediately without a restart; invalid or non-finite values fail
safe to the documented default of 5 GB and are reported as a bounded
diagnostic log. The finding is advisory only: it never grants deletion or
cleanup authority.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_KIND = "folder_size"
_STATE_FILE = Path(".notification") / ".nudge_state.json"
DEFAULT_LIMIT_GB = 5.0
LIMIT_ENV = "LINGTAI_NUDGE_FOLDER_SIZE_GB"
# Decimal gigabytes (10**9 bytes) to match the environment name and registry;
# GiB (2**30) would silently shift a configured 5 GB threshold by ~7.4%.
_BYTES_PER_GB = 1_000_000_000


def check(agent) -> None:
    """Evaluate the folder-size nudge for ``agent`` on every heartbeat.

    The recursive walk runs at most once per UTC day; the upsert/remove decision
    is re-evaluated every call against the persisted last observation so global
    enable/dismiss/repeat/retry semantics stay live.
    """
    persistent = _load_persistent_state(agent)
    folder_state = persistent.setdefault(_KIND, {})

    limit_gb, invalid = _read_limit_gb()
    if invalid:
        _safe_log(agent, "folder_size_limit_invalid", value=invalid)

    # Bounded observation gate: walk at most once per UTC day.
    if folder_state.get("last_check_date") != _today_utc():
        try:
            total_bytes = _dir_size(Path(agent._working_dir))
        except Exception as e:  # pragma: no cover - defensive: nudge must be inert
            _safe_log(agent, "folder_size_probe_error", error=str(e)[:200])
            return
        folder_state.update(
            {
                "last_check_date": _today_utc(),
                "size_bytes": total_bytes,
                "limit_gb": limit_gb,
            }
        )
        _save_persistent_state(agent, persistent)

    # No observation yet (first walk failed); nothing to evaluate.
    if "size_bytes" not in folder_state:
        return

    from . import remove, upsert

    total_bytes = folder_state["size_bytes"]
    if total_bytes <= limit_gb * _BYTES_PER_GB:
        remove(agent, _KIND)
        return

    upsert(
        agent,
        _KIND,
        {
            "title": f"Agent working directory exceeds {_format_gb(limit_gb)} limit",
            "detail": (
                f"Working directory {agent._working_dir} is {_format_bytes(total_bytes)} "
                f"({_format_gb(total_bytes / _BYTES_PER_GB)}), above the "
                f"{_format_gb(limit_gb)} threshold from {LIMIT_ENV}. "
                "This finding is advisory only and does not authorize deletion or "
                "cleanup; remove or archive files only with the existing owner/human "
                "authorization. Once back under the threshold, this nudge clears "
                "on the next evaluation."
            ),
            "source": "working-directory-walk",
            "local_path": str(agent._working_dir),
            "size_bytes": total_bytes,
            "size_gb": round(total_bytes / _BYTES_PER_GB, 2),
            "limit_gb": limit_gb,
            "checked_at_date": folder_state.get("last_check_date"),
        },
    )


def _read_limit_gb(environ: Mapping[str, str] | None = None) -> tuple[float, str | None]:
    """Read the GB threshold; invalid, non-finite, or missing values fall back to ``5``."""
    env = os.environ if environ is None else environ
    raw = str(env.get(LIMIT_ENV, "")).strip()
    if not raw:
        return DEFAULT_LIMIT_GB, None
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LIMIT_GB, raw
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_LIMIT_GB, raw
    return value, None


def _dir_size(path: Path) -> int:
    """Recursively sum regular-file sizes under ``path`` (best-effort)."""
    total = 0
    for root, dirs, files in os.walk(path, topdown=True):
        # Never descend through symlinked directories (cycle protection) and
        # skip symlinked files (avoid double counting outside the tree).
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        for name in files:
            try:
                p = Path(root) / name
                if p.is_symlink():
                    continue
                total += p.stat().st_size
            except OSError:
                continue
    return total


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        value /= 1024
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TiB"


def _format_gb(value: float) -> str:
    return f"{value:g} GB"


def _persistent_path(agent) -> Path:
    return Path(agent._working_dir) / _STATE_FILE


def _load_persistent_state(agent) -> dict[str, Any]:
    path = _persistent_path(agent)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_persistent_state(agent, state: dict[str, Any]) -> None:
    path = _persistent_path(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _safe_log(agent, event: str, **fields: Any) -> None:
    try:
        agent._log(event, **fields)
    except Exception:
        return


__all__ = ["DEFAULT_LIMIT_GB", "LIMIT_ENV", "check"]
