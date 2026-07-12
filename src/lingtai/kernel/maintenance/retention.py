"""Dry-run retention reporter for kernel-owned runtime files.

This module is intentionally report-only. It classifies stale, low-risk
filesystem artifacts but never deletes, archives, or rewrites them. The
allowlist is narrow:

* terminal daemon run directories under ``daemons/``;
* historical sent-mail copies under ``mailbox/sent/``;
* archive mail only when explicitly requested for reporting; and
* rebuildable ``logs/log.sqlite`` sidecar indexes.

Operational queues, unread/actionable inbox mail, authoritative JSONL logs,
notification state, recovery logs, and lifecycle-protected agents are reported
as protected instead of candidates. The report also includes high-footprint
observations for portal replay, agent logs, and agent history. Those footprint
items carry risk and recommendation fields, but they are never cleanup
candidates.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TERMINAL_DAEMON_STATES = frozenset({"done", "failed", "cancelled", "timeout"})
PROTECTED_AGENT_STATES = frozenset({"active", "asleep", "suspended"})
DEFAULT_LIVE_HEARTBEAT_SECONDS = 10.0
SAMPLE_LIMIT = 10

CATEGORY_DAEMON = "terminal_daemon_run"
CATEGORY_SENT = "sent_mail"
CATEGORY_ARCHIVE = "archive_mail"
CATEGORY_LOG_SQLITE = "rebuildable_log_index"

FOOTPRINT_PORTAL_TOPOLOGY = "portal_topology_replay"
FOOTPRINT_LOG_SQLITE = "agent_log_index"
FOOTPRINT_EVENTS = "agent_authoritative_events_log"
FOOTPRINT_TOKEN_LEDGER = "agent_authoritative_token_ledger"
FOOTPRINT_SOUL_FLOW = "agent_authoritative_soul_flow"
FOOTPRINT_REFRESH_RELAUNCH = "agent_refresh_relaunch_log"
FOOTPRINT_HISTORY_ARCHIVE = "agent_history_archive"
FOOTPRINT_HISTORY_SNAPSHOTS = "agent_history_snapshots"

FOOTPRINT_CATEGORIES = (
    FOOTPRINT_PORTAL_TOPOLOGY,
    FOOTPRINT_LOG_SQLITE,
    FOOTPRINT_EVENTS,
    FOOTPRINT_TOKEN_LEDGER,
    FOOTPRINT_SOUL_FLOW,
    FOOTPRINT_REFRESH_RELAUNCH,
    FOOTPRINT_HISTORY_ARCHIVE,
    FOOTPRINT_HISTORY_SNAPSHOTS,
)


class TargetError(ValueError):
    """Raised when a requested target is not a supported retention root."""


@dataclass(frozen=True)
class RetentionOptions:
    """Options for one retention report scan."""

    older_than_days: int = 30
    include_archive: bool = False
    live_heartbeat_seconds: float = DEFAULT_LIVE_HEARTBEAT_SECONDS


@dataclass
class RetentionCandidate:
    """One report-only retention candidate."""

    agent: str
    category: str
    path: Path
    reason: str
    bytes: int
    age_seconds: float | None = None
    age_days: float | None = None
    timestamp: str | None = None
    age_source: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProtectedItem:
    """One protected path/count reported for operator visibility."""

    agent: str
    category: str
    path: Path
    reason: str
    count: int | None = None


@dataclass
class SkippedItem:
    """One stale-like item skipped because its metadata is unsafe."""

    agent: str
    category: str
    path: Path
    reason: str


@dataclass
class FootprintItem:
    """One read-only high-space observation."""

    agent: str | None
    category: str
    path: Path
    bytes: int
    risk: str
    recommendation: str
    count: int = 1
    timestamp: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentReport:
    """Per-agent report section."""

    agent: str
    path: Path
    is_human: bool
    status_state: str | None
    protected_agent: bool
    protected_reasons: list[str] = field(default_factory=list)
    candidates: list[RetentionCandidate] = field(default_factory=list)
    protected: list[ProtectedItem] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    footprints: list[FootprintItem] = field(default_factory=list)


@dataclass
class RetentionReport:
    """Top-level dry-run retention report."""

    target: Path
    target_kind: str
    mode: str
    cutoff_before: datetime
    options: RetentionOptions
    agents: list[AgentReport]
    root_footprints: list[FootprintItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def scan_retention(
    target: Path,
    options: RetentionOptions | None = None,
    *,
    _now: datetime | None = None,
) -> RetentionReport:
    """Scan *target* and return a dry-run retention report.

    Supported targets are either a single agent workdir containing
    ``.agent.json`` or a direct ``.lingtai`` root. A ``.lingtai`` root scan only
    inspects direct child agent directories; it never recursively searches
    arbitrary parent trees.
    """

    options = options or RetentionOptions()
    if options.older_than_days < 1:
        raise ValueError("older_than_days must be >= 1")
    now = _now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=options.older_than_days)
    target_kind, root, agents = _resolve_target(Path(target))
    root_footprints = _scan_root_footprints(root, target_kind)
    agent_reports = [
        _scan_agent(agent, root, options, cutoff, now) for agent in agents
    ]
    warnings = [
        "dry-run only: no files were deleted, archived, moved, or rewritten"
    ]
    if options.include_archive:
        warnings.append(
            "archive mail is included only as a report candidate; archive data "
            "can feed older history and replay views"
        )
    return RetentionReport(
        target=root,
        target_kind=target_kind,
        mode="dry_run",
        cutoff_before=cutoff,
        options=options,
        agents=agent_reports,
        root_footprints=root_footprints,
        warnings=warnings,
    )


def report_to_dict(report: RetentionReport) -> dict[str, Any]:
    """Convert a retention report into stable JSON-serializable data."""

    candidates = [
        _candidate_to_dict(c)
        for agent in report.agents
        for c in agent.candidates
    ]
    protected = [
        _protected_to_dict(p)
        for agent in report.agents
        for p in agent.protected
    ]
    skipped = [
        _skipped_to_dict(s)
        for agent in report.agents
        for s in agent.skipped
    ]
    footprints = [_footprint_to_dict(f) for f in report.root_footprints]
    footprints.extend(
        _footprint_to_dict(f)
        for agent in report.agents
        for f in agent.footprints
    )

    classes: dict[str, dict[str, Any]] = {}
    for category in (
        CATEGORY_DAEMON,
        CATEGORY_SENT,
        CATEGORY_ARCHIVE,
        CATEGORY_LOG_SQLITE,
    ):
        class_candidates = [c for c in candidates if c["category"] == category]
        class_protected = [p for p in protected if p["category"] == category]
        class_skipped = [s for s in skipped if s["category"] == category]
        classes[category] = {
            "candidates": len(class_candidates),
            "protected": len(class_protected),
            "skipped": len(class_skipped),
            "bytes": sum(int(c["bytes"]) for c in class_candidates),
            "samples": class_candidates[:SAMPLE_LIMIT],
        }

    footprint_classes: dict[str, dict[str, Any]] = {}
    for category in FOOTPRINT_CATEGORIES:
        class_footprints = [f for f in footprints if f["category"] == category]
        footprint_classes[category] = {
            "items": len(class_footprints),
            "count": sum(int(f["count"]) for f in class_footprints),
            "bytes": sum(int(f["bytes"]) for f in class_footprints),
            "samples": class_footprints[:SAMPLE_LIMIT],
        }

    total_bytes = sum(int(c["bytes"]) for c in candidates)
    footprint_bytes = sum(int(f["bytes"]) for f in footprints)
    agents = []
    for agent in report.agents:
        agents.append(
            {
                "agent": agent.agent,
                "path": str(agent.path),
                "is_human": agent.is_human,
                "status_state": agent.status_state,
                "protected_agent": agent.protected_agent,
                "protected_reasons": list(agent.protected_reasons),
                "candidates": len(agent.candidates),
                "protected": len(agent.protected),
                "skipped": len(agent.skipped),
                "candidate_bytes": sum(c.bytes for c in agent.candidates),
                "footprints": len(agent.footprints),
                "footprint_bytes": sum(f.bytes for f in agent.footprints),
            }
        )

    return {
        "status": "ok",
        "mode": report.mode,
        "target_kind": report.target_kind,
        "target": str(report.target),
        "cutoff": {
            "older_than_days": report.options.older_than_days,
            "before": _iso(report.cutoff_before),
        },
        "totals": {
            "agents": len(report.agents),
            "candidates": len(candidates),
            "protected": len(protected),
            "skipped": len(skipped),
            "candidate_bytes": total_bytes,
            "footprints": len(footprints),
            "footprint_bytes": footprint_bytes,
        },
        "classes": classes,
        "footprint_classes": footprint_classes,
        "candidates": candidates,
        "footprints": footprints,
        "protected": protected[:SAMPLE_LIMIT * 2],
        "skipped": skipped[:SAMPLE_LIMIT * 2],
        "agents": agents,
        "warnings": list(report.warnings),
    }


def _resolve_target(target: Path) -> tuple[str, Path, list[Path]]:
    if target.is_symlink():
        raise TargetError(f"refusing symlink target root: {target}")
    try:
        resolved = target.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TargetError(f"target does not exist: {target}") from exc
    if not resolved.is_dir():
        raise TargetError(f"target is not a directory: {resolved}")
    if _is_agent_dir(resolved):
        return "agent", resolved, [resolved]

    is_lingtai_root = resolved.name == ".lingtai" or (resolved / "meta.json").is_file()
    if is_lingtai_root:
        agents = [
            child
            for child in sorted(resolved.iterdir(), key=lambda p: p.name)
            if child.is_dir() and not child.is_symlink() and _is_agent_dir(child)
        ]
        if not agents:
            raise TargetError(f"no direct agent directories found under {resolved}")
        return "lingtai_root", resolved, agents

    raise TargetError(
        "target is neither an agent workdir (.agent.json) nor a .lingtai root "
        f"(meta.json): {resolved}"
    )


def _scan_root_footprints(root: Path, target_kind: str) -> list[FootprintItem]:
    if target_kind != "lingtai_root":
        return []
    topology = root / ".portal" / "topology.jsonl"
    if not topology.is_file() or topology.is_symlink():
        return []
    if not _contained(topology, root):
        return []
    return [
        _footprint(
            None,
            FOOTPRINT_PORTAL_TOPOLOGY,
            topology,
            _file_size(topology),
            "rotation_or_compression_only",
            "Rotate or compress the portal topology replay; do not delete it "
            "without a replay-retention design.",
            count=1,
            detail={"owner": "portal", "format": "jsonl"},
        )
    ]


def _scan_agent(
    agent_dir: Path,
    scan_root: Path,
    options: RetentionOptions,
    cutoff: datetime,
    now: datetime,
) -> AgentReport:
    manifest = _read_json(agent_dir / ".agent.json")
    is_human = _is_human_manifest(manifest)
    status_state = _status_state(agent_dir)
    protected_reasons = _agent_protected_reasons(
        agent_dir, status_state, options.live_heartbeat_seconds, now
    )
    report = AgentReport(
        agent=agent_dir.name,
        path=agent_dir,
        is_human=is_human,
        status_state=status_state,
        protected_agent=bool(protected_reasons) and not is_human,
        protected_reasons=[] if is_human else protected_reasons,
    )

    _add_always_protected(report, agent_dir)
    _scan_agent_footprints(report, agent_dir, scan_root)
    if report.protected_agent:
        _add_protected_agent_classes(report, agent_dir, scan_root)
        return report

    _scan_daemons(report, agent_dir, scan_root, options, cutoff, now)
    _scan_mail_folder(report, agent_dir, scan_root, "sent", CATEGORY_SENT, cutoff, now)
    if options.include_archive:
        _scan_mail_folder(
            report, agent_dir, scan_root, "archive", CATEGORY_ARCHIVE, cutoff, now
        )
    else:
        archive = agent_dir / "mailbox" / "archive"
        if archive.is_dir() and not archive.is_symlink():
            report.protected.append(
                ProtectedItem(
                    agent=report.agent,
                    category=CATEGORY_ARCHIVE,
                    path=archive,
                    reason="archive_mail_protected_by_default",
                    count=_count_children(archive),
                )
            )
    _scan_log_sqlite(report, agent_dir, scan_root, cutoff, now)
    return report


def _scan_agent_footprints(
    report: AgentReport,
    agent_dir: Path,
    scan_root: Path,
) -> None:
    logs = agent_dir / "logs"
    if logs.is_dir() and not logs.is_symlink():
        for name, category, risk, recommendation in (
            (
                "log.sqlite",
                FOOTPRINT_LOG_SQLITE,
                "rebuildable_low_risk",
                "Rebuild or compact this SQLite sidecar from authoritative logs; "
                "stale copies remain governed by the existing cutoff candidate.",
            ),
            (
                "events.jsonl",
                FOOTPRINT_EVENTS,
                "authoritative_do_not_delete",
                "Preserve as the authoritative recovery log; consider compression "
                "or indexed compaction design, not direct deletion.",
            ),
            (
                "token_ledger.jsonl",
                FOOTPRINT_TOKEN_LEDGER,
                "authoritative_do_not_delete",
                "Preserve token-accounting rows; consider compression or rollup "
                "only with an accounting contract.",
            ),
            (
                "soul_flow.jsonl",
                FOOTPRINT_SOUL_FLOW,
                "authoritative_do_not_delete",
                "Preserve the soul-flow record; consider compression or archive "
                "policy, not blind deletion.",
            ),
            (
                "refresh_relaunch.log",
                FOOTPRINT_REFRESH_RELAUNCH,
                "diagnostic_review_before_truncate",
                "Rotate or truncate only after a recovery window; keep enough "
                "recent relaunch diagnostics.",
            ),
        ):
            path = logs / name
            if path.is_file() and not path.is_symlink() and _contained(path, scan_root):
                report.footprints.append(
                    _footprint(
                        report.agent,
                        category,
                        path,
                        _file_size(path),
                        risk,
                        recommendation,
                        count=1,
                    )
                )

    history = agent_dir / "history"
    archive = history / "chat_history_archive.jsonl"
    if archive.is_file() and not archive.is_symlink() and _contained(archive, scan_root):
        report.footprints.append(
            _footprint(
                report.agent,
                FOOTPRINT_HISTORY_ARCHIVE,
                archive,
                _file_size(archive),
                "history_do_not_blind_delete",
                "Compress or archive conversation history by policy; do not "
                "blind delete it.",
                count=1,
                detail={"format": "jsonl"},
            )
        )

    snapshots = history / "snapshots"
    if snapshots.is_dir() and not snapshots.is_symlink() and _contained(snapshots, scan_root):
        files = [
            path
            for path in sorted(snapshots.glob("*.json"), key=lambda p: p.name)
            if path.is_file() and not path.is_symlink() and _contained(path, scan_root)
        ]
        if files:
            report.footprints.append(
                _footprint(
                    report.agent,
                    FOOTPRINT_HISTORY_SNAPSHOTS,
                    snapshots,
                    sum(_file_size(path) for path in files),
                    "history_do_not_blind_delete",
                    "Design keep-latest-N plus compression/archive for snapshots; "
                    "do not blind delete them.",
                    count=len(files),
                    timestamp=_iso(_latest_mtime(files)),
                    detail={"pattern": "*.json"},
                )
            )


def _scan_daemons(
    report: AgentReport,
    agent_dir: Path,
    scan_root: Path,
    options: RetentionOptions,
    cutoff: datetime,
    now: datetime,
) -> None:
    daemons = agent_dir / "daemons"
    if not daemons.is_dir() or daemons.is_symlink():
        return
    for run_dir in sorted(daemons.iterdir(), key=lambda p: p.name):
        if run_dir.is_symlink():
            report.skipped.append(
                SkippedItem(report.agent, CATEGORY_DAEMON, run_dir, "symlink")
            )
            continue
        if not run_dir.is_dir():
            continue
        if not _contained(run_dir, scan_root):
            report.skipped.append(
                SkippedItem(report.agent, CATEGORY_DAEMON, run_dir, "outside_target")
            )
            continue

        state, age_dt, age_source, error = _daemon_state_and_age(run_dir)
        if error:
            report.skipped.append(SkippedItem(report.agent, CATEGORY_DAEMON, run_dir, error))
            continue
        if state not in TERMINAL_DAEMON_STATES:
            report.protected.append(
                ProtectedItem(
                    report.agent,
                    CATEGORY_DAEMON,
                    run_dir,
                    f"daemon_state_not_terminal:{state}",
                )
            )
            continue
        if age_dt is None:
            report.skipped.append(
                SkippedItem(report.agent, CATEGORY_DAEMON, run_dir, "no_age")
            )
            continue
        if not age_dt < cutoff:
            report.protected.append(
                ProtectedItem(
                    report.agent,
                    CATEGORY_DAEMON,
                    run_dir,
                    "newer_than_cutoff",
                )
            )
            continue
        if _fresh_mtime(run_dir / ".heartbeat", options.live_heartbeat_seconds, now):
            report.protected.append(
                ProtectedItem(
                    report.agent,
                    CATEGORY_DAEMON,
                    run_dir,
                    "daemon_heartbeat_fresh",
                )
            )
            continue

        report.candidates.append(
            _candidate(
                report.agent,
                CATEGORY_DAEMON,
                run_dir,
                "terminal_state_and_older_than_cutoff",
                _dir_size(run_dir),
                age_dt,
                age_source,
                now,
                detail={"daemon_state": state},
            )
        )


def _scan_mail_folder(
    report: AgentReport,
    agent_dir: Path,
    scan_root: Path,
    folder_name: str,
    category: str,
    cutoff: datetime,
    now: datetime,
) -> None:
    folder = agent_dir / "mailbox" / folder_name
    if not folder.is_dir() or folder.is_symlink():
        return
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if entry.is_symlink():
            report.skipped.append(SkippedItem(report.agent, category, entry, "symlink"))
            continue
        if not entry.is_dir():
            continue
        if not _contained(entry, scan_root):
            report.skipped.append(
                SkippedItem(report.agent, category, entry, "outside_target")
            )
            continue
        age_dt, age_source = _mail_age(entry)
        if age_dt is None:
            report.skipped.append(SkippedItem(report.agent, category, entry, "no_age"))
            continue
        if not age_dt < cutoff:
            report.protected.append(
                ProtectedItem(report.agent, category, entry, "newer_than_cutoff")
            )
            continue
        report.candidates.append(
            _candidate(
                report.agent,
                category,
                entry,
                "older_than_cutoff",
                _dir_size(entry),
                age_dt,
                age_source,
                now,
            )
        )


def _scan_log_sqlite(
    report: AgentReport,
    agent_dir: Path,
    scan_root: Path,
    cutoff: datetime,
    now: datetime,
) -> None:
    sqlite = agent_dir / "logs" / "log.sqlite"
    if not sqlite.exists():
        return
    if sqlite.is_symlink():
        report.skipped.append(SkippedItem(report.agent, CATEGORY_LOG_SQLITE, sqlite, "symlink"))
        return
    if not sqlite.is_file():
        return
    if not _contained(sqlite, scan_root):
        report.skipped.append(
            SkippedItem(report.agent, CATEGORY_LOG_SQLITE, sqlite, "outside_target")
        )
        return
    age_dt = _mtime_dt(sqlite)
    if age_dt is None:
        report.skipped.append(SkippedItem(report.agent, CATEGORY_LOG_SQLITE, sqlite, "no_age"))
        return
    if not age_dt < cutoff:
        report.protected.append(
            ProtectedItem(report.agent, CATEGORY_LOG_SQLITE, sqlite, "newer_than_cutoff")
        )
        return
    report.candidates.append(
        _candidate(
            report.agent,
            CATEGORY_LOG_SQLITE,
            sqlite,
            "rebuildable_sidecar_older_than_cutoff",
            _file_size(sqlite),
            age_dt,
            "mtime",
            now,
        )
    )


def _add_always_protected(report: AgentReport, agent_dir: Path) -> None:
    protected_dirs = {
        "inbox_mail": agent_dir / "mailbox" / "inbox",
        "outbox_mail": agent_dir / "mailbox" / "outbox",
        "scheduled_mail": agent_dir / "mailbox" / "schedules",
        "history": agent_dir / "history",
        "notifications": agent_dir / ".notification",
        "tool_result_artifacts": agent_dir / "tmp" / "tool-results",
    }
    for category, path in protected_dirs.items():
        if path.is_dir() and not path.is_symlink():
            report.protected.append(
                ProtectedItem(
                    report.agent,
                    category,
                    path,
                    f"{category}_protected",
                    count=_count_children(path),
                )
            )

    logs = agent_dir / "logs"
    if logs.is_dir() and not logs.is_symlink():
        for name in ("events.jsonl", "token_ledger.jsonl", "refresh_failed_permanent.json"):
            path = logs / name
            if path.exists() and not path.is_symlink():
                report.protected.append(
                    ProtectedItem(
                        report.agent,
                        "authoritative_log",
                        path,
                        "authoritative_or_recovery_log_protected",
                    )
                )


def _add_protected_agent_classes(
    report: AgentReport,
    agent_dir: Path,
    scan_root: Path,
) -> None:
    report.protected.append(
        ProtectedItem(
            report.agent,
            "agent",
            agent_dir,
            "agent_lifecycle_protected:" + ",".join(report.protected_reasons),
        )
    )
    for category, folder in (
        (CATEGORY_DAEMON, agent_dir / "daemons"),
        (CATEGORY_SENT, agent_dir / "mailbox" / "sent"),
        (CATEGORY_ARCHIVE, agent_dir / "mailbox" / "archive"),
    ):
        if folder.is_dir() and not folder.is_symlink() and _contained(folder, scan_root):
            report.protected.append(
                ProtectedItem(
                    report.agent,
                    category,
                    folder,
                    "agent_lifecycle_protected",
                    count=_count_children(folder),
                )
            )
    sqlite = agent_dir / "logs" / "log.sqlite"
    if sqlite.exists() and not sqlite.is_symlink() and _contained(sqlite, scan_root):
        report.protected.append(
            ProtectedItem(
                report.agent,
                CATEGORY_LOG_SQLITE,
                sqlite,
                "agent_lifecycle_protected",
            )
        )


def _agent_protected_reasons(
    agent_dir: Path,
    status_state: str | None,
    heartbeat_seconds: float,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    if _lock_held(agent_dir / ".agent.lock") is True:
        reasons.append("held_agent_lock")
    if _fresh_agent_heartbeat(agent_dir / ".agent.heartbeat", heartbeat_seconds, now):
        reasons.append("fresh_agent_heartbeat")
    if status_state in PROTECTED_AGENT_STATES:
        reasons.append(f"status_state:{status_state}")
    return reasons


def _daemon_state_and_age(
    run_dir: Path,
) -> tuple[str | None, datetime | None, str | None, str | None]:
    data = _read_json(run_dir / "daemon.json")
    if data is None:
        return None, None, None, "missing_or_corrupt_daemon_json"
    state = data.get("state") if isinstance(data, dict) else None
    finished_at = _parse_iso(data.get("finished_at") if isinstance(data, dict) else None)
    if finished_at is not None:
        return state, finished_at, "daemon_finished_at", None
    run_id_time = _parse_run_id_time(run_dir.name)
    if run_id_time is not None:
        return state, run_id_time, "run_id", None
    return state, _mtime_dt(run_dir), "mtime", None


def _candidate(
    agent: str,
    category: str,
    path: Path,
    reason: str,
    size: int,
    age_dt: datetime | None,
    age_source: str | None,
    now: datetime,
    *,
    detail: dict[str, Any] | None = None,
) -> RetentionCandidate:
    age_seconds = None
    age_days = None
    if age_dt is not None:
        age_seconds = max(0.0, (now - age_dt).total_seconds())
        age_days = round(age_seconds / 86400.0, 3)
    return RetentionCandidate(
        agent=agent,
        category=category,
        path=path,
        reason=reason,
        bytes=size,
        age_seconds=age_seconds,
        age_days=age_days,
        timestamp=_iso(age_dt),
        age_source=age_source,
        detail=detail or {},
    )


def _footprint(
    agent: str | None,
    category: str,
    path: Path,
    size: int,
    risk: str,
    recommendation: str,
    *,
    count: int = 1,
    timestamp: str | None = None,
    detail: dict[str, Any] | None = None,
) -> FootprintItem:
    return FootprintItem(
        agent=agent,
        category=category,
        path=path,
        bytes=size,
        risk=risk,
        recommendation=recommendation,
        count=count,
        timestamp=timestamp if timestamp is not None else _iso(_mtime_dt(path)),
        detail=detail or {},
    )


def _candidate_to_dict(candidate: RetentionCandidate) -> dict[str, Any]:
    return {
        "agent": candidate.agent,
        "category": candidate.category,
        "path": str(candidate.path),
        "reason": candidate.reason,
        "age_days": candidate.age_days,
        "age_seconds": candidate.age_seconds,
        "timestamp": candidate.timestamp,
        "age_source": candidate.age_source,
        "bytes": candidate.bytes,
        "detail": dict(candidate.detail),
    }


def _footprint_to_dict(item: FootprintItem) -> dict[str, Any]:
    return {
        "agent": item.agent,
        "scope": "agent" if item.agent is not None else "lingtai_root",
        "category": item.category,
        "path": str(item.path),
        "bytes": item.bytes,
        "count": item.count,
        "risk": item.risk,
        "recommendation": item.recommendation,
        "timestamp": item.timestamp,
        "detail": dict(item.detail),
    }


def _protected_to_dict(item: ProtectedItem) -> dict[str, Any]:
    data = {
        "agent": item.agent,
        "category": item.category,
        "path": str(item.path),
        "reason": item.reason,
    }
    if item.count is not None:
        data["count"] = item.count
    return data


def _skipped_to_dict(item: SkippedItem) -> dict[str, Any]:
    return {
        "agent": item.agent,
        "category": item.category,
        "path": str(item.path),
        "reason": item.reason,
    }


def _is_agent_dir(path: Path) -> bool:
    return (path / ".agent.json").is_file()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_human_manifest(manifest: dict[str, Any] | None) -> bool:
    if not isinstance(manifest, dict):
        return False
    return "admin" not in manifest or manifest.get("admin") is None


def _status_state(agent_dir: Path) -> str | None:
    data = _read_json(agent_dir / ".status.json")
    runtime = data.get("runtime") if isinstance(data, dict) else None
    state = runtime.get("state") if isinstance(runtime, dict) else None
    return str(state).lower() if state else None


def _contained(path: Path, root: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        resolved_path = path.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_path != resolved_root


def _lock_held(lock_path: Path) -> bool | None:
    """Read-only observation of whether ``.agent.lock`` is held by a live agent.

    This is a NON-AUTHORITATIVE diagnostic observer, not a lease consumer. It
    probes the lock with a transient ``flock`` and immediately releases it to
    answer a retention question — it never acquires the Core-owned
    ``WorkdirLeasePort`` and never claims the directory. Lock *ownership*
    (acquiring/holding the lease) lives behind that Port in
    ``src/lingtai/adapters/posix/workdir_lease.py``; this function only looks.
    """
    if not lock_path.is_file() or lock_path.is_symlink():
        return False
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX fallback
        return None
    try:
        fd = os.open(str(lock_path), os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _fresh_agent_heartbeat(path: Path, threshold: float, now: datetime) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        timestamp = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return now.timestamp() - timestamp < threshold


def _fresh_mtime(path: Path, threshold: float, now: datetime) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return now.timestamp() - mtime < threshold


def _mail_age(entry: Path) -> tuple[datetime | None, str | None]:
    name_time = _parse_mail_id_time(entry.name)
    if name_time is not None:
        return name_time, "mail_id"
    return _mtime_dt(entry), "mtime"


def _parse_mail_id_time(name: str) -> datetime | None:
    head = name.split("-", 1)[0]
    try:
        return datetime.strptime(head, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_run_id_time(run_id: str) -> datetime | None:
    parts = run_id.split("-")
    if len(parts) < 4:
        return None
    try:
        return datetime.strptime(parts[-3] + parts[-2], "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _mtime_dt(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _latest_mtime(paths: list[Path]) -> datetime | None:
    mtimes = [dt for path in paths if (dt := _mtime_dt(path)) is not None]
    return max(mtimes) if mtimes else None


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            file_path = Path(root) / name
            if file_path.is_symlink():
                continue
            total += _file_size(file_path)
    return total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _count_children(path: Path) -> int:
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return 0


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
