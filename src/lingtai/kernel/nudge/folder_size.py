"""Nudge when the agent working directory grows past a threshold.

The recursive size walk runs at most once per UTC day (bounded observation
cost); upsert/remove are re-evaluated every heartbeat against the persisted
observation and current ``LINGTAI_NUDGE_FOLDER_SIZE_GB`` (default ``5``
decimal GB), so global dismiss/repeat/enable/retry semantics stay live.
Threshold is advisory only; invalid/non-finite values fall back to 5.
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
_BYTES_PER_GB = 1_000_000_000


def check(agent) -> None:
    """Evaluate the folder-size nudge on every heartbeat (walk max once/day)."""
    persistent = _load_persistent_state(agent)
    folder_state = persistent.setdefault(_KIND, {})

    limit_gb, invalid = _read_limit_gb()
    if invalid:
        _safe_log(agent, "folder_size_limit_invalid", value=invalid)

    if folder_state.get("last_check_date") != _today_utc():
        try:
            total_bytes = _dir_size(Path(agent._working_dir))
        except Exception as e:  # pragma: no cover - defensive
            _safe_log(agent, "folder_size_probe_error", error=str(e)[:200])
            return
        folder_state.update({"last_check_date": _today_utc(), "size_bytes": total_bytes, "limit_gb": limit_gb})
        _save_persistent_state(agent, persistent)

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
                "cleanup; remove or archive files only with existing owner/human "
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
    """Read GB threshold; invalid/non-finite/missing fall back to ``5``."""
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
    """Recursively sum regular-file sizes, skipping symlinks (cycle-safe)."""
    total = 0
    for root, dirs, files in os.walk(path, topdown=True):
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        for name in files:
            try:
                p = Path(root) / name
                if not p.is_symlink():
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
