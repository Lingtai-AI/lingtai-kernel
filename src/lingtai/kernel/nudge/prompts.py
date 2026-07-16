"""Agent-facing payloads for kernel nudge situations.

Nudge producers own observation and cadence.  This module owns the wording and
payload shape that turns those facts into an agent-facing nudge, so a producer
cannot accidentally invent a refresh/update instruction for one situation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


SKILL_ROUTE = "https://lingtai.ai/skill.md"


class NudgeSituation(str, Enum):
    """The kernel nudge situations currently rendered for agents."""

    INSTALLED_RUNTIME_MISMATCH = "installed_runtime_mismatch"
    PACKAGE_UPDATE_AVAILABLE = "package_update_available"
    SOURCE_DRIFT = "source_drift"


@dataclass(frozen=True, slots=True)
class NudgeFacts:
    """Facts collected by a producer and consumed by the nudge renderer."""

    running: str | None = None
    installed: str | None = None
    latest: str | None = None
    checked_at_date: str | None = None
    startup_fingerprint: Mapping[str, Any] | None = None
    disk_fingerprint: Mapping[str, Any] | None = None
    drift_signals: tuple[str, ...] = ()


def render_nudge_payload(
    situation: NudgeSituation, facts: NudgeFacts
) -> dict[str, Any]:
    """Render one stable, agent-facing nudge payload from typed facts."""
    if situation is NudgeSituation.INSTALLED_RUNTIME_MISMATCH:
        return _render_installed_runtime_mismatch(facts)
    if situation is NudgeSituation.PACKAGE_UPDATE_AVAILABLE:
        return _render_package_update(facts)
    if situation is NudgeSituation.SOURCE_DRIFT:
        return _render_source_drift(facts)
    raise ValueError(f"unsupported nudge situation: {situation!r}")


def _render_installed_runtime_mismatch(facts: NudgeFacts) -> dict[str, Any]:
    running = facts.running or "unknown"
    installed = facts.installed or "unknown"
    return {
        "title": f"LingTai kernel refresh available: {running} -> {installed}",
        "detail": (
            "The installed LingTai kernel differs from the currently running "
            "kernel. Read "
            f"{SKILL_ROUTE} first to identify the authoritative release source, "
            "then determine the applicable release migrations. Obtain explicit "
            "human/config-owner authorization for EVERY migration/config write "
            "and for refresh; this nudge and the route are not authorization. "
            "Apply only authorized writes, validate the resulting configuration, "
            "and refresh last with system(action='refresh') only if that "
            "authorized refresh is safe."
        ),
        "running": facts.running,
        "installed": facts.installed,
        "latest": None,
        "source": "installed-distribution",
        "cadence": "at-most-once-per-utc-day",
        "checked_at_date": facts.checked_at_date,
        "suggested_action": "read-runtime-update-skill-then-refresh-if-safe",
        "skill": SKILL_ROUTE,
    }


def _render_package_update(facts: NudgeFacts) -> dict[str, Any]:
    installed = facts.installed or "unknown"
    latest = facts.latest or "unknown"
    return {
        "title": f"LingTai kernel update available: {installed} -> {latest}",
        "detail": (
            "A newer LingTai kernel package is available. Read "
            f"{SKILL_ROUTE} first to identify the authoritative release source, "
            "then determine the applicable release migrations. Tell the human "
            "what changed and ask whether they want to update through their "
            "normal LingTai runtime/TUI upgrade path. Obtain explicit "
            "human/config-owner authorization for EVERY migration/config write "
            "and for refresh, plus confirmation for the download/update, before "
            "acting; this nudge and the route are not authorization. Apply only "
            "authorized writes, validate the resulting configuration, and "
            "refresh last. Do not download, update, or refresh without human "
            "confirmation."
        ),
        "running": facts.running,
        "installed": facts.installed,
        "latest": facts.latest,
        "source": "pypi-json",
        "cadence": "at-most-once-per-utc-day",
        "checked_at_date": facts.checked_at_date,
        "suggested_action": "read-runtime-update-skill-and-ask-human",
        "skill": SKILL_ROUTE,
    }


def _render_source_drift(facts: NudgeFacts) -> dict[str, Any]:
    drift = "; ".join(facts.drift_signals) or "runtime fingerprint changed"
    return {
        "title": "Source drift detected — running code is stale",
        "detail": (
            "On-disk source has changed since this process started. "
            f"Drift: {drift}. No urgency — finish the current task first, then "
            "obtain explicit human/config-owner authorization for refresh. Once "
            "authorized, use system(action='refresh') when convenient to relaunch "
            "with the latest code. This source-drift reminder does not by itself "
            "imply a release migration or grant refresh authority."
        ),
        "suggested_action": "system(action='refresh')",
        "startup_fingerprint": facts.startup_fingerprint,
        "disk_fingerprint": facts.disk_fingerprint,
    }


__all__ = ["NudgeFacts", "NudgeSituation", "SKILL_ROUTE", "render_nudge_payload"]
