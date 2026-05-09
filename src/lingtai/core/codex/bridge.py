"""Codex bridge — standalone read/write for non-lingtai processes.

Provides codex access without requiring the lingtai kernel runtime.
Used by:
  1. Avatar wrapper processes (Claude Code avatars)
  2. Daemon CLAUDE.md generators (to inject current codex catalog)
  3. CLI: ``python -m lingtai.core.codex.bridge read <path>``
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def read_codex(codex_path: str | Path) -> dict:
    """Read codex entries from disk."""
    path = Path(codex_path)
    if not path.is_file():
        return {"version": 1, "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def read_entry(codex_path: str | Path, entry_id: str) -> dict | None:
    """Read a single codex entry by ID."""
    data = read_codex(codex_path)
    for entry in data.get("entries", []):
        if entry.get("id") == entry_id:
            return entry
    return None


def list_entries(codex_path: str | Path) -> list[dict]:
    """List all entries (id, title, summary only)."""
    data = read_codex(codex_path)
    return [
        {"id": e["id"], "title": e["title"], "summary": e["summary"]}
        for e in data.get("entries", [])
    ]


def write_entry(
    codex_path: str | Path,
    title: str,
    summary: str,
    content: str = "",
    supplementary: str = "",
    max_entries: int = 20,
) -> dict:
    """Add a new entry to the codex. Returns the new entry."""
    path = Path(codex_path)
    data = read_codex(path)

    entries = data.get("entries", [])
    if len(entries) >= max_entries:
        return {"error": f"Codex full ({len(entries)}/{max_entries}). Consolidate first."}

    entry_id = hashlib.sha256(
        f"{title}{content or summary}{time.time()}".encode()
    ).hexdigest()[:8]

    new_entry = {
        "id": entry_id,
        "title": title,
        "summary": summary,
        "content": content,
        "supplementary": supplementary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    entries.append(new_entry)
    data["entries"] = entries

    # Atomic write
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return new_entry


def build_codex_catalog(codex_path: str | Path) -> str:
    """Build a text catalog of current codex entries for CLAUDE.md injection."""
    data = read_codex(codex_path)
    entries = data.get("entries", [])

    if not entries:
        return "Your codex is empty. Use it to store durable knowledge.\n"

    lines = [f"Your codex has {len(entries)} entries:\n"]
    for e in entries:
        lines.append(f"- **[{e['id']}]** {e['title']}: {e['summary']}")
    lines.append("")
    lines.append("Use `cat ./codex/codex.json | python3 -m json.tool` to read full entries.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m lingtai.core.codex.bridge <read|list|write> <codex_path> [args]")
        sys.exit(1)

    action = sys.argv[1]
    path = sys.argv[2]

    if action == "read":
        print(json.dumps(read_codex(path), indent=2))
    elif action == "list":
        for e in list_entries(path):
            print(f"[{e['id']}] {e['title']}: {e['summary']}")
    elif action == "write":
        if len(sys.argv) < 5:
            print("Usage: ... write <path> <title> <summary> [content]")
            sys.exit(1)
        entry = write_entry(
            path, sys.argv[3], sys.argv[4],
            content=sys.argv[5] if len(sys.argv) > 5 else "",
        )
        print(json.dumps(entry, indent=2))
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
