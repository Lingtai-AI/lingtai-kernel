"""Private Pad composition over the durable body and pinned reference list.

Everything here is internal. There is no public ``pad`` tool root and no
``append`` action; the pinned reference list is a durable *source* that this
module reads and composes, and that the agent edits through ``shell`` like any
other durable text.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Pad append-file reading
# ---------------------------------------------------------------------------

_APPEND_LIST_PATH = "system/pad_append.json"


def _append_list_file(agent) -> Path:
    return agent._working_dir / _APPEND_LIST_PATH


def _load_append_list(agent) -> list[str]:
    """Read the persisted append file list (empty list if missing)."""
    path = _append_list_file(agent)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(p) for p in data]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _resolve_path(agent, fpath: str) -> Path:
    if os.path.isabs(fpath):
        return Path(fpath)
    return agent._working_dir / fpath


def _read_append_content(agent, files: list[str]) -> tuple[str, list[str]]:
    """Read all append files. Returns (combined content, not_found list)."""
    parts: list[str] = []
    not_found: list[str] = []
    for fpath in files:
        resolved = _resolve_path(agent, fpath)
        if not resolved.is_file():
            not_found.append(fpath)
            continue
        parts.append(f"[append: {fpath}]\n{resolved.read_text(encoding='utf-8')}")
    return "\n\n".join(parts), not_found


# ---------------------------------------------------------------------------
# Pad composition
# ---------------------------------------------------------------------------


def _pad_load(agent, args: dict, *, publish: bool = True) -> dict:
    """Compose system/pad.md plus pinned references into the prompt.

    ``publish=False`` is used only by canonical full-context reconstruction so
    every section is composed before one final prompt publication.
    """
    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    pad_path = system_dir / "pad.md"
    if not pad_path.is_file():
        pad_path.write_text("", encoding="utf-8")

    content = pad_path.read_text(encoding="utf-8")
    size_bytes = len(content.encode("utf-8"))

    if content.strip():
        agent._prompt_manager.write_section("pad", content)
    else:
        agent._prompt_manager.delete_section("pad")

    # Append-files layering — pinned read-only reference appended to pad section
    append_files = _load_append_list(agent)
    append_meta: dict = {}
    if append_files:
        append_content, not_found = _read_append_content(agent, append_files)
        if append_content:
            existing = agent._prompt_manager.read_section("pad") or ""
            combined = existing + "\n\n---\n# 📎 Reference (read-only)\n\n" + append_content
            agent._prompt_manager.write_section("pad", combined)
        if not_found:
            append_meta["append_not_found"] = not_found
        append_meta["append_files"] = append_files
        append_meta["append_count"] = len(append_files)

    agent._token_decomp_dirty = True
    if publish:
        agent._flush_system_prompt()

    agent._log("psyche_pad_load", size_bytes=size_bytes)

    result: dict = {
        "status": "ok",
        "path": str(pad_path),
        "size_bytes": size_bytes,
        "content_preview": content[:200],
    }
    result.update(append_meta)
    return result
