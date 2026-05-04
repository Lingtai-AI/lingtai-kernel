"""Snapshot and summary persistence for the molt machinery.

Provides helpers to serialize the pre-molt ChatInterface and persist
agent-authored retrospectives. Both are best-effort — a failure here
must not block the molt itself.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


SNAPSHOT_SCHEMA_VERSION = 1


def _write_molt_summary(
    agent,
    *,
    summary: str,
    source: str,
    molt_count: int,
    before_tokens: int,
    after_tokens: int,
) -> Path | None:
    """Persist the molt summary to system/summaries/ as a durable retrospective.

    Best-effort — a failed write must not block the molt. Returns the path
    on success, or None if the write failed.

    Filename: molt_<molt_count>_<unix_ts>.md — molt_count first so directory
    listings sort chronologically without parsing.

    Format: a small YAML-ish frontmatter block followed by the summary prose.
    Frontmatter is human-readable (so `cat` is useful) and machine-parseable
    (any future digest-injection layer can split on the leading `---`).

    Complementary to `history/snapshots/snapshot_<count>_<ts>.json`:
    - snapshot = frozen substrate (full ChatInterface for past-self consultation)
    - summary  = curated retrospective (agent-authored prose)
    Both share molt_count so they can be paired by index.
    """
    try:
        summaries_dir = agent._working_dir / "system" / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)

        unix_ts = int(time.time())
        path = summaries_dir / f"molt_{molt_count}_{unix_ts}.md"

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        agent_name = getattr(agent, "agent_name", None) or ""

        frontmatter = (
            "---\n"
            f"molt_count: {molt_count}\n"
            f"created_at: {created_at}\n"
            f"source: {source}\n"
            f"agent_name: {agent_name}\n"
            f"before_tokens: {before_tokens}\n"
            f"after_tokens: {after_tokens}\n"
            f"tokens_shed: {max(0, before_tokens - after_tokens)}\n"
            "---\n\n"
        )

        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(frontmatter + summary, encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as e:
        try:
            agent._log("summary_write_failed", error=str(e))
        except Exception:
            pass
        return None


def _write_molt_snapshot(
    agent,
    iface_pre,
    *,
    before_tokens: int,
    summary: str,
    source: str,
    molt_count: int,
    exclude_trailing_call_id: str | None = None,
) -> Path | None:
    """Serialize the pre-molt ChatInterface to a discrete snapshot file.

    The snapshot is the substrate a future "past self" consultation can
    load — full message history at the moment the agent decided to molt,
    minus the molt's own tool_call (which is meta about the molting
    process, not part of the past self's mind). Returns the snapshot
    path on success, or None if the write failed (best-effort — a
    failed snapshot must not block the molt itself).

    Filename: snapshot_<molt_count>_<unix_ts>.json — molt_count first
    so directory listings sort chronologically without parsing.

    ``exclude_trailing_call_id``: if set, the trailing assistant entry
    whose only ToolCallBlock has this id is dropped from the serialized
    interface. Used by the agent-initiated molt path where the molt's
    own tool_call already sits in the tail entry of iface_pre.
    """
    try:
        snapshots_dir = agent._working_dir / "history" / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        entries = iface_pre.to_dict()
        if exclude_trailing_call_id and entries:
            tail = entries[-1]
            if tail.get("role") == "assistant":
                content = tail.get("content") or []
                if (
                    len(content) == 1
                    and content[0].get("type") == "tool_call"
                    and content[0].get("id") == exclude_trailing_call_id
                ):
                    entries = entries[:-1]

        unix_ts = int(time.time())
        path = snapshots_dir / f"snapshot_{molt_count}_{unix_ts}.json"

        payload = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "molt_count": molt_count,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "before_tokens": before_tokens,
            "agent_name": getattr(agent, "agent_name", None),
            "agent_id": getattr(agent, "_agent_id", None),
            "molt_summary": summary,
            "molt_source": source,
            "interface": entries,
        }

        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as e:
        try:
            agent._log("snapshot_write_failed", error=str(e))
        except Exception:
            pass
        return None
