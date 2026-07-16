"""Per-agent nudges — periodic checks that emit a notification when
something needs the agent's attention.

Each check is a self-contained module exposing ``check(agent) -> None``.
Checks may use a bounded in-memory probe gate, but shared enabled/repeat and
finding-dismissal semantics live here. Most nudges upsert/remove entries in the
shared ``.notification/nudge.json`` payload. Goal reminders are a separately
classified system notification: they read protected ``.notification/goal.json``
and publish short ``goal.reminder`` events into ``.notification/system.json``;
they are not declared Nudge kinds.

Channel ``.notification/nudge.json`` carries a list of active nudges:

    {
      "header": "<rendered by _render_header — e.g. '2 nudges'>",
      "icon": "🔔",
      "priority": "low",
      "instructions": "Call notification(action='dismiss_channel', channel='nudge') ...",
      "data": {"nudges": [{"kind": "kernel_version", ...}, ...]}
    }

Each check identifies its slot by a unique ``kind`` string. ``upsert``
replaces (or appends) one entry; ``remove`` deletes one. When the last
entry leaves, the channel file is cleared so the agent's wire surface
drops the notification entirely. The agent dismisses everything at once
with ``notification(action='dismiss_channel', channel='nudge')``.

To add a new nudge: drop ``nudge/<name>.py`` exposing ``check(agent)``,
then add an import + dispatch line to :func:`run_checks` below. No
registry, no protocol — keep the surface flat.

Concurrency: each RMW upsert/remove is one Store-owned atomic channel update.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import init_config, kernel_version, goal, source_drift


DEFAULT_ENABLED = True
DEFAULT_REPEAT_INTERVAL_SECONDS = 24 * 60 * 60
ENABLED_ENV = "LINGTAI_NUDGE_ENABLED"
REPEAT_INTERVAL_ENV = "LINGTAI_NUDGE_REPEAT_INTERVAL"
ENVIRONMENT_MANUAL = "system-manual/reference/environment-variables/SKILL.md"


@dataclass(frozen=True, slots=True)
class NudgePolicy:
    """The shared policy read by every Nudge producer."""

    enabled: bool = DEFAULT_ENABLED
    repeat_interval_seconds: float = DEFAULT_REPEAT_INTERVAL_SECONDS
    enabled_value: str = "on"
    repeat_interval_value: str = "24h"
    invalid_values: tuple[str, ...] = ()

    def message(self) -> str:
        return (
            f"Nudge policy: {ENABLED_ENV}={self.enabled_value} (effective "
            f"{'on' if self.enabled else 'off'}); {REPEAT_INTERVAL_ENV}="
            f"{self.repeat_interval_value} (effective repeat-after-dismiss "
            f"{_format_duration(self.repeat_interval_seconds)}). "
            f"Read {ENVIRONMENT_MANUAL} for scope, reload timing, and invalid-value behavior."
        )

    def payload(self) -> dict[str, Any]:
        return {
            "enabled": "on" if self.enabled else "off",
            "repeat_after_dismiss": _format_duration(self.repeat_interval_seconds),
            "env": {
                "enabled": ENABLED_ENV,
                "repeat_interval": REPEAT_INTERVAL_ENV,
            },
            "documentation": ENVIRONMENT_MANUAL,
        }


def effective_policy(environ: Mapping[str, str] | None = None) -> NudgePolicy:
    """Read both global controls at the start of each Nudge operation.

    Accepted values are case-insensitive ``on``/``off`` for the switch and a
    positive duration such as ``30m``, ``24h``, or ``2d`` for the interval.
    Invalid or missing values fail safe to the documented defaults and are
    reported in ``invalid_values``; a process restart is not required.
    """
    env = os.environ if environ is None else environ
    invalid: list[str] = []
    raw_enabled = str(env.get(ENABLED_ENV, "on")).strip().lower()
    if raw_enabled in {"on", "true", "1"}:
        enabled = True
        enabled_value = "on"
    elif raw_enabled in {"off", "false", "0"}:
        enabled = False
        enabled_value = "off"
    else:
        enabled = DEFAULT_ENABLED
        enabled_value = "on"
        invalid.append(f"{ENABLED_ENV}={raw_enabled!r}")

    raw_interval = str(env.get(REPEAT_INTERVAL_ENV, "24h")).strip().lower()
    seconds = _parse_duration(raw_interval)
    if seconds is None:
        seconds = float(DEFAULT_REPEAT_INTERVAL_SECONDS)
        repeat_value = "24h"
        invalid.append(f"{REPEAT_INTERVAL_ENV}={raw_interval!r}")
    else:
        repeat_value = _format_duration(seconds)

    return NudgePolicy(
        enabled=enabled,
        repeat_interval_seconds=seconds,
        enabled_value=enabled_value,
        repeat_interval_value=repeat_value,
        invalid_values=tuple(invalid),
    )


def _parse_duration(value: str) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(s|m|h|d)", value)
    if not match:
        return None
    amount = float(match.group(1))
    if amount <= 0:
        return None
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]


def _format_duration(seconds: float) -> str:
    # Preserve the product default's documented spelling (`24h`) while using
    # day units for intervals longer than one day.
    if seconds % 3600 == 0 and seconds > 86400:
        return f"{int(seconds // 86400)}d"
    if seconds % 3600 == 0:
        return f"{int(seconds // 3600)}h"
    if seconds % 60 == 0:
        return f"{int(seconds // 60)}m"
    return f"{seconds:g}s"


__all__ = [
    "DEFAULT_ENABLED",
    "DEFAULT_REPEAT_INTERVAL_SECONDS",
    "ENABLED_ENV",
    "ENVIRONMENT_MANUAL",
    "NudgePolicy",
    "REPEAT_INTERVAL_ENV",
    "effective_policy",
    "record_dismissal",
    "run_checks",
    "run_system_notifications",
    "upsert",
    "remove",
]


def run_checks(agent) -> None:
    """Run declared Nudge producers under one policy gate.

    The global switch is evaluated before producer observation gates. Goal
    reminders are dispatched separately by :func:`run_system_notifications` and
    are not part of this Nudge catalogue.
    """
    policy = effective_policy()
    if policy.invalid_values:
        _safe_log(agent, "nudge_policy_invalid", values=list(policy.invalid_values))
    if not policy.enabled:
        # Clear stale visible findings immediately, even when a producer's own
        # bounded probe gate would otherwise return without touching the store.
        _modify(agent, lambda entries: [])
        return

    _run_one(agent, "kernel_version", kernel_version.check)
    _run_one(agent, "source_drift", source_drift.check)
    _run_one(agent, "init_config_shape", init_config.check)


def run_system_notifications(agent) -> None:
    """Dispatch protected system reminders that are not Nudge findings."""
    _run_one(agent, "goal_system_notification", goal.check)


def _run_one(agent, name: str, fn) -> None:
    try:
        fn(agent)
    except Exception as e:
        try:
            agent._log("nudge_check_error", kind=name, error=str(e)[:200])
        except Exception:
            pass


def upsert(agent, kind: str, body: dict) -> None:
    """Replace or append one finding under the shared global Nudge policy."""
    policy = effective_policy()
    if policy.invalid_values:
        _safe_log(agent, "nudge_policy_invalid", values=list(policy.invalid_values))
    if not policy.enabled:
        _modify(agent, lambda entries: [e for e in entries if e.get("kind") != kind])
        return

    entry = dict(body)
    entry["policy"] = policy.payload()
    entry["policy_message"] = policy.message()
    entry["detail"] = (
        f"{entry.get('detail', '')}\n\n{policy.message()}".strip()
    )
    fingerprint = _finding_fingerprint(kind, entry)
    if _dismissed_until(agent, fingerprint) > time.time():
        return
    _clear_dismissal(agent, fingerprint)
    _modify(agent, lambda entries: _replace_kind(entries, kind, entry))


def remove(agent, kind: str) -> None:
    """Drop the nudge entry for ``kind``; resolved findings are not re-emitted."""
    _modify(agent, lambda entries: [e for e in entries if e.get("kind") != kind])


def record_dismissal(agent) -> None:
    """Record dismissal of the currently displayed findings, not resolution.

    Notification remains the transport and calls this policy hook before it
    clears the shared channel.  The local state stores only finding identity
    and an expiry; it is not a migration registry or per-kind cadence.
    """
    policy = effective_policy()
    entries = _current_entries(agent)
    if not entries:
        return
    state = _load_policy_state(agent)
    now = time.time()
    dismissed = state.setdefault("dismissed", {})
    for entry in entries:
        kind = str(entry.get("kind") or "")
        if not kind:
            continue
        fingerprint = _finding_fingerprint(kind, entry)
        dismissed[fingerprint] = {
            "kind": kind,
            "dismissed_at": now,
            "until": now + policy.repeat_interval_seconds,
        }
    _save_policy_state(agent, state)
    _safe_log(
        agent,
        "nudge_dismissed",
        findings=len(entries),
        repeat_interval_seconds=policy.repeat_interval_seconds,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_STATE_FILE = Path(".notification") / ".nudge_state.json"


def _finding_fingerprint(kind: str, body: dict) -> str:
    """Hash stable finding facts, excluding policy display bookkeeping."""
    stable = {
        "kind": kind,
        "title": body.get("title"),
        "detail": body.get("detail"),
        "source": body.get("source"),
        "running": body.get("running"),
        "installed": body.get("installed"),
        "latest": body.get("latest"),
        "drift_signals": body.get("drift_signals"),
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _state_path(agent) -> Path | None:
    working_dir = getattr(agent, "_working_dir", None)
    if working_dir is None:
        return None
    return Path(working_dir) / _STATE_FILE


def _load_policy_state(agent) -> dict[str, Any]:
    path = _state_path(agent)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_policy_state(agent, state: dict[str, Any]) -> None:
    path = _state_path(agent)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _dismissed_until(agent, fingerprint: str) -> float:
    state = _load_policy_state(agent)
    record = (state.get("dismissed") or {}).get(fingerprint)
    if not isinstance(record, dict):
        return 0.0
    try:
        return float(record.get("until") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clear_dismissal(agent, fingerprint: str) -> None:
    state = _load_policy_state(agent)
    dismissed = state.get("dismissed")
    if not isinstance(dismissed, dict) or fingerprint not in dismissed:
        return
    dismissed.pop(fingerprint, None)
    _save_policy_state(agent, state)


def _current_entries(agent) -> list[dict]:
    try:
        from ..notifications import _get_allow_predicate
        snapshot = agent._notification_store.snapshot(_get_allow_predicate())
        payload = snapshot.get("nudge")
        data = payload.get("data") if isinstance(payload, dict) else None
        entries = data.get("nudges") if isinstance(data, dict) else None
        return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []
    except Exception:
        return []


def _safe_log(agent, event: str, **fields: Any) -> None:
    try:
        agent._log(event, **fields)
    except Exception:
        pass


def _replace_kind(entries: list, kind: str, body: dict) -> list:
    out = [e for e in entries if e.get("kind") != kind]
    entry = dict(body)
    entry["kind"] = kind
    out.append(entry)
    return out


def _modify(agent, mutate) -> None:
    """Apply one pure current-payload nudge mutation atomically."""
    from datetime import datetime, timezone
    from ..notification_store import UNCONDITIONAL

    published_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _mutator(current_payload: dict):
        current = current_payload if isinstance(current_payload, dict) else {}
        data = current.get("data")
        existing = data.get("nudges", []) if isinstance(data, dict) else []
        existing = existing if isinstance(existing, list) else []
        new_entries = mutate(list(existing))
        if new_entries == existing:
            return current_payload, False, None
        if not new_entries:
            return None, True, None
        return {
            "header": _render_header(new_entries),
            "icon": "\U0001f514",
            "priority": "low",
            "published_at": published_at,
            "instructions": (
                "Call notification(action='dismiss_channel', channel='nudge') to "
                "acknowledge and clear ALL nudges at once. Individual nudges "
                "may also describe a specific action to take (e.g. "
                "system(action='refresh') for a kernel upgrade)."
            ),
            "data": {"nudges": new_entries},
        }, True, None

    agent._notification_store.compare_update_channel(
        "nudge", UNCONDITIONAL, _mutator
    )


def _render_header(entries: list) -> str:
    n = len(entries)
    return f"{n} nudge{'s' if n != 1 else ''}"
