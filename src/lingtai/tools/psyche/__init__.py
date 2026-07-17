"""Psyche intrinsic — bare essentials of agent self.

Objects:
    pad — edit/load system/pad.md (agent's working notes), append pinned files
    context — molt (shed context, keep a briefing)
    name — set true name (once), set/clear nickname
    lingtai — update/load system/lingtai.md (self-authored identity → `character` section)

Sub-modules:
    _snapshots.py — Snapshot and summary persistence for the molt machinery.
    _pad.py       — Pad CRUD and append-file management.
    _lingtai.py   — Lingtai identity/character management.
    _molt.py      — Context molt core, name handlers, system-initiated molt.

Internal:
    boot — boot-time hook: load lingtai + pad into prompt, register post-molt
        reload. Called from base_agent.__init__ after intrinsics are wired.
"""
from __future__ import annotations

# --- Re-exports from sub-modules for backward compatibility ---

# Snapshots (used by consultation, inquiry, etc.)
from ._snapshots import SNAPSHOT_SCHEMA_VERSION, _write_molt_snapshot, _write_molt_summary  # noqa: F401

# Pad (used by boot, and cross-referenced by lingtai/append)
from ._pad import _pad_edit, _pad_load, _pad_append  # noqa: F401

# Lingtai (used by boot, and cross-referenced by pad)
from ._lingtai import _lingtai_update, _lingtai_load  # noqa: F401

# Molt (the public surface)
from ._molt import _context_molt, _name_set, _name_nickname, context_forget  # noqa: F401
from .._manual import load_installed_manual


# ---------------------------------------------------------------------------
# Schema / description
# ---------------------------------------------------------------------------


def get_description(lang: str = "en") -> str:
    return 'Identity, pad, and context management — three objects, all molt-surviving. lingtai: your 灵台 (character) — update after significant work or before molt. pad: system-prompt sketchboard (system/pad.md) — plans, tasks, notes. context: molt (凝蜕) — shed conversation, keep stores. name: set true name (once) or change nickname. Call psyche(action="manual") to return the installed psyche-manual skill.'


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "object": {
                "type": "string",
                "enum": ["pad", "context", "name", "lingtai"],
                "description": 'lingtai: your 灵台 — what distinguishes you from every other agent.\npad: your sketchboard in your system prompt (system/pad.md).\ncontext: your conversation context window.\nname: your true name (set once) and nickname (mutable).',
            },
            "action": {
                "type": "string",
                "description": 'lingtai: update | load. update auto-loads.\npad: edit | load | append. edit auto-loads. append pins files as read-only reference.\ncontext: molt. Requires `summary` — tend the four stores BEFORE molting. See psyche-manual.\nname: set (true name, once) | nickname (display name, mutable).\nmanual: return the installed psyche-manual skill; object is omitted.',
            },
            "content": {
                "type": "string",
                "description": 'Text content. For lingtai update: your full identity (replaces entirely). For pad edit: written as-is to pad.md. For name set/nickname: your chosen name.',
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'File paths (text files only). For pad append: pins files as read-only reference in your system prompt — re-read on every load including after molt. Pass files=[] to clear. Max 100k tokens total. Paths relative to working directory. See psyche-manual for detailed usage.',
            },
            "summary": {
                "type": "string",
                "description": 'For context molt: your session retrospective (~10,000 tokens). Write as a record — what happened, what you learned, what remains. The four stores must be tended BEFORE molt. Saved to `system/summaries/molt_<count>_<ts>.md` and replayed to the next you. See psyche-manual for full writing guidance.',
            },
            "keep_tool_calls": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Optional list of tool-call IDs to replay across the molt, in your chosen order. If any ID is not found, molt is refused. Keep short — durable stores are primary persistence. See psyche-manual.',
            },
            "keep_last": {
                "type": "integer",
                "description": 'Optional integer (default: 20). Number of recent conversation entries to replay into the fresh session. Pass 0 to archive everything. Overlapping entries with keep_tool_calls are deduplicated. See psyche-manual.',
            },
            "session_journal_path": {
                "type": "string",
                "description": 'REQUIRED for context molt. The path to the session-journal entry you wrote for the just-finished segment BEFORE molting: knowledge/session-journal/<entry>/KNOWLEDGE.md (a per-segment sub-entry, NOT the parent index). Must be inside your workdir, exist, be non-empty UTF-8, have valid YAML frontmatter with `name` and `description`, and identify itself as session knowledge via `type: session-journal` or `session_journal: true`. The molt is refused before any context is shed if this is missing or invalid. See psyche-manual §4.',
            },
        },
        "required": ["action"],
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_VALID_ACTIONS: dict[str, set[str]] = {
    "lingtai": {"update", "load"},
    "pad": {"edit", "load", "append"},
    "context": {"molt"},
    "name": {"set", "nickname"},
}

# Explicit dispatch table — replaces the old globals().get(method_name) pattern
# so it works across sub-modules.
_DISPATCH: dict[str, dict[str, object]] = {
    "lingtai": {"update": _lingtai_update, "load": _lingtai_load},
    "pad": {"edit": _pad_edit, "load": _pad_load, "append": _pad_append},
    "context": {"molt": _context_molt},
    "name": {"set": _name_set, "nickname": _name_nickname},
}


def handle(agent, args: dict) -> dict:
    """Handle psyche tool — dispatch to (object, action) handler."""
    obj = args.get("object", "")
    action = args.get("action", "")

    if action == "manual":
        return load_installed_manual(agent, "psyche-manual")

    valid = _VALID_ACTIONS.get(obj)
    if valid is None:
        return {
            "error": f"Unknown object: {obj!r}. "
                     f"Must be one of: {', '.join(sorted(_VALID_ACTIONS))}."
        }
    if action not in valid:
        return {
            "error": f"Invalid action {action!r} for {obj}. "
                     f"Valid actions: {', '.join(sorted(valid))}."
        }

    handler = _DISPATCH.get(obj, {}).get(action)
    if handler is None:
        return {"error": f"Internal: handler for ({obj}, {action}) not found."}
    return handler(agent, args)


# ---------------------------------------------------------------------------
# Boot hook
# ---------------------------------------------------------------------------


def boot(agent) -> None:
    """Boot-time hook: load lingtai + pad into the prompt, register post-molt
    reload. Called from base_agent.__init__ after intrinsics are wired."""
    _pad_load(agent, {})
    _lingtai_load(agent, {})
    if not hasattr(agent, "_post_molt_hooks"):
        agent._post_molt_hooks = []
    agent._post_molt_hooks.append(
        lambda: (_lingtai_load(agent, {}), _pad_load(agent, {}))
    )
