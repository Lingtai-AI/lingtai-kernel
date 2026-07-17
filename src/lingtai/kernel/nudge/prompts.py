"""Agent-facing payloads for kernel nudge situations.

Nudge producers own observation and cadence. This module owns the wording and
payload shape that turns those facts into an agent-facing nudge, so a producer
cannot accidentally invent an update instruction or choose a release mirror.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


INSTALL_ROUTE = "https://lingtai.ai/install.sh"
# Kept as a compatibility name for callers that imported the old constant;
# update guidance now routes only to the stable installer entry point.
SKILL_ROUTE = INSTALL_ROUTE


class NudgeSituation(str, Enum):
    """The kernel nudge situations currently rendered for agents."""

    INSTALLED_RUNTIME_MISMATCH = "installed_runtime_mismatch"
    RUNTIME_MISMATCH_DIAGNOSTIC = "runtime_mismatch_diagnostic"
    PACKAGE_UPDATE_AVAILABLE = "package_update_available"
    MIRROR_MISMATCH = "mirror_mismatch"
    SOURCE_DRIFT = "source_drift"


@dataclass(frozen=True, slots=True)
class NudgeFacts:
    """Facts collected by a producer and consumed by the nudge renderer."""

    running: str | None = None
    installed: str | None = None
    latest: str | None = None
    checked_at_date: str | None = None
    source: str | None = None
    mirror_mismatch: Mapping[str, Mapping[str, str]] | None = None
    startup_fingerprint: Mapping[str, Any] | None = None
    disk_fingerprint: Mapping[str, Any] | None = None
    drift_signals: tuple[str, ...] = ()


def render_nudge_payload(
    situation: NudgeSituation, facts: NudgeFacts
) -> dict[str, Any]:
    """Render one stable, agent-facing nudge payload from typed facts."""
    if situation is NudgeSituation.INSTALLED_RUNTIME_MISMATCH:
        return _render_installed_runtime_mismatch(facts)
    if situation is NudgeSituation.RUNTIME_MISMATCH_DIAGNOSTIC:
        return _render_runtime_mismatch_diagnostic(facts)
    if situation is NudgeSituation.PACKAGE_UPDATE_AVAILABLE:
        return _render_package_update(facts)
    if situation is NudgeSituation.MIRROR_MISMATCH:
        return _render_mirror_mismatch(facts)
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
            "kernel, so the newer bytes are already on disk. This is a local "
            "refresh opportunity, not a release download or migration. Obtain "
            "explicit human/config-owner authorization for refresh; this nudge "
            "is not authorization. Finish or checkpoint active work, then call "
            "system(action='refresh') only when that authorized relaunch is safe."
        ),
        "running": facts.running,
        "installed": facts.installed,
        "latest": None,
        "source": "installed-distribution",
        "checked_at_date": facts.checked_at_date,
        "suggested_action": "refresh-installed-runtime-if-authorized-and-safe",
    }


def _render_runtime_mismatch_diagnostic(facts: NudgeFacts) -> dict[str, Any]:
    running = facts.running or "unknown"
    installed = facts.installed or "unknown"
    return {
        "title": f"LingTai kernel runtime mismatch: {running} (running) vs {installed} (installed)",
        "detail": (
            "The running and installed LingTai kernel versions cannot safely be "
            "treated as newer on-disk bytes. Inspect the runtime interpreter, "
            "distribution metadata, module import paths, and lingtai.__file__ / "
            "lingtai.kernel.__file__ with read-only diagnostics. Do not refresh, "
            "downgrade, install, or choose a release from this finding; obtain "
            "explicit human/config-owner authority before any mutation or refresh."
        ),
        "running": facts.running,
        "installed": facts.installed,
        "latest": None,
        "source": "installed-distribution-diagnostic",
        "checked_at_date": facts.checked_at_date,
        "suggested_action": "inspect-runtime-interpreter-and-import-paths",
    }


def _render_package_update(facts: NudgeFacts) -> dict[str, Any]:
    installed = facts.installed or "unknown"
    latest = facts.latest or "unknown"
    source = facts.source or "release-manifest"
    return {
        "title": f"LingTai kernel update available: {installed} -> {latest}",
        "detail": (
            "The official LingTai release manifest reports a newer kernel. "
            f"Use Shell to execute the official installer at {INSTALL_ROUTE} "
            "with --help; do not read or paste the script source into context. "
            "Then execute its update --help mode to inspect the exact runtime, "
            "migration, mirror, hash, and refresh contract. Before the real "
            "update, the agent must obtain explicit human/config-owner "
            "authorization; this nudge and help output are not authorization."
        ),
        "running": facts.running,
        "installed": facts.installed,
        "latest": facts.latest,
        "source": source,
        "checked_at_date": facts.checked_at_date,
        "suggested_action": "execute-installer-help-then-ask-human",
        "skill": INSTALL_ROUTE,
        "install_url": INSTALL_ROUTE,
    }


def _render_mirror_mismatch(facts: NudgeFacts) -> dict[str, Any]:
    return {
        "title": "LingTai kernel release mirrors disagree",
        "detail": (
            "The GitHub and Gitee official release manifests disagree on "
            "version, content, or artifact hashes. Report the mirror mismatch "
            "and do not choose the higher version or update from either mirror "
            "until the release owner resolves it."
        ),
        "running": facts.running,
        "installed": facts.installed,
        "latest": None,
        "source": "release-manifest-mirror-mismatch",
        "checked_at_date": facts.checked_at_date,
        "mirror_mismatch": facts.mirror_mismatch or {},
        "suggested_action": "report-release-manifest-mirror-mismatch",
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


__all__ = [
    "INSTALL_ROUTE",
    "NudgeFacts",
    "NudgeSituation",
    "SKILL_ROUTE",
    "render_nudge_payload",
]
