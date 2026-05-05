"""Notification filesystem — `.notification/` dropbox + sync primitives.

Producers write JSON files; the kernel reads them and syncs the agent's
wire context to match.  This module provides the file-level helpers
(fingerprint, collect, publish, clear).  The sync-loop logic — strip +
reinject into the wire — lives on :class:`BaseAgent`.

Naming convention:

* Kernel intrinsics write ``<intrinsic_name>.json`` (e.g. ``email.json``,
  ``soul.json``, ``system.json``).
* MCP-loaded servers write ``mcp.<server_name>.json`` (e.g.
  ``mcp.imap.json``, ``mcp.telegram.json``).

The basename is the *tool* whose namespace owns the notification.

See ``discussions/notification-filesystem-redesign.md`` for the design
rationale and ``discussions/notification-filesystem-implementation-patch.md``
for the implementation specification.
"""
from __future__ import annotations

import json
from pathlib import Path


def notification_fingerprint(workdir: Path) -> tuple:
    """Compute a fingerprint of `.notification/*.json`.

    Returns a tuple of ``(name, mtime_ns, size)`` triples sorted by name.
    Empty tuple if the directory is absent or empty.  Used to detect
    whether any producer file has changed since the last poll.

    ``mtime_ns`` (nanosecond resolution) is used rather than ``mtime``
    so that rapid producer writes within a one-second window aren't
    mistaken for "no change" on filesystems with second-level mtime.
    """
    notif_dir = workdir / ".notification"
    if not notif_dir.is_dir():
        return ()
    return tuple(sorted(
        (f.name, f.stat().st_mtime_ns, f.stat().st_size)
        for f in notif_dir.iterdir()
        if f.is_file() and f.suffix == ".json"
    ))


def collect_notifications(workdir: Path) -> dict:
    """Read `.notification/*.json` and return a dict keyed by stem.

    Keys are filenames without extension (``email``, ``soul``,
    ``mcp.telegram``, …).  Sorted iteration produces deterministic
    ordering so the agent's mental model is stable across reads.

    Returns ``{}`` if the directory is absent, empty, or all files are
    unparseable.  Malformed files are silently skipped — a buggy
    producer should not break the agent.  (Producer authors see the
    skip in their own logs and fix.)
    """
    notif_dir = workdir / ".notification"
    if not notif_dir.is_dir():
        return {}
    out = {}
    for f in sorted(notif_dir.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_bytes())
        except (json.JSONDecodeError, OSError):
            continue
    return out


def publish(workdir: Path, tool_name: str, payload: dict) -> None:
    """Write a notification file atomically (tmp + rename).

    ``tool_name`` is the stem — ``email``, ``soul``, ``mcp.telegram``, etc.
    Overwrites any prior content for that source.

    The atomicity is important: a reader doing ``listdir`` + ``read_bytes``
    while a producer is mid-write would see truncated JSON.  ``tmp +
    rename`` makes the rename appear atomically to readers.
    """
    notif_dir = workdir / ".notification"
    notif_dir.mkdir(exist_ok=True)
    target = notif_dir / f"{tool_name}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.rename(target)


def clear(workdir: Path, tool_name: str) -> None:
    """Delete a producer's notification file.  Idempotent.

    Producers call this when their state empties (e.g. mail's unread
    count drops to 0).  Deletion changes the directory fingerprint, so
    the kernel's next sync tick will strip the wire's notification block.
    """
    target = workdir / ".notification" / f"{tool_name}.json"
    try:
        target.unlink()
    except (FileNotFoundError, OSError):
        pass
