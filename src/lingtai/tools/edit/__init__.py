"""Edit capability — exact string replacement in a file.

Usage: Agent(capabilities=["edit"]) or capabilities=["file"]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .._file_paths import resolve_workdir_path
from .._manual import load_installed_manual

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent


def get_description(lang: str = "en") -> str:
    return "Replace an exact string in a file. Normal edits are the primary operation: omit action for the legacy ordinary call or use action='edit' explicitly. Fails if old_string is not found or is ambiguous. Use action='manual' once to return the installed file-manual skill; after the manual result, continue the original ordinary edit instead of repeating manual, because repeated identical manual calls are an error loop."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["edit", "manual"], "description": "Omit action for the legacy ordinary edit, use action='edit' for an explicit ordinary edit, or use action='manual' once for the installed file-manual skill."},
            "file_path": {"type": "string", "description": "Absolute path to the file to edit. Required for ordinary edits; omit for action='manual'."},
            "old_string": {"type": "string", "description": "The exact text to find and replace. Required for ordinary edits; omit for action='manual'."},
            "new_string": {"type": "string", "description": "The replacement text. Required for ordinary edits; omit for action='manual'."},
            "replace_all": {"type": "boolean", "description": 'Replace all occurrences', "default": False},
        },
        "required": [],
    }



def setup(agent: "BaseAgent") -> None:
    """Set up the edit capability on an agent."""

    def handle_edit(args: dict) -> dict:
        action = args.get("action")
        if action == "manual":
            return load_installed_manual(agent, "file-manual")
        if action is not None and action != "edit":
            return {"status": "error", "message": f"Unsupported action for edit: {action!r}"}
        path = args.get("file_path", "")
        if not path:
            return {"status": "error", "message": "file_path is required"}
        if "old_string" not in args:
            return {"status": "error", "message": "old_string is required"}
        if "new_string" not in args:
            return {"status": "error", "message": "new_string is required"}
        path = resolve_workdir_path(agent, path)
        old = args["old_string"]
        new = args["new_string"]
        replace_all = args.get("replace_all", False)
        try:
            content = agent._file_io.read(path)
        except FileNotFoundError:
            return {"status": "error", "message": f"File not found: {path}"}
        except Exception as e:
            return {"status": "error", "message": f"Cannot read {path}: {e}"}
        count = content.count(old)
        if count == 0:
            return {"status": "error", "message": f"old_string not found in {path}"}
        if count > 1 and not replace_all:
            return {"status": "error", "message": f"old_string found {count} times — use replace_all=true or provide more context"}
        if replace_all:
            updated = content.replace(old, new)
        else:
            updated = content.replace(old, new, 1)
        try:
            agent._file_io.write(path, updated)
        except Exception as e:
            return {"status": "error", "message": f"Cannot write {path}: {e}"}
        return {"status": "ok", "replacements": count if replace_all else 1}

    agent.add_tool("edit", schema=get_schema(), handler=handle_edit, description=get_description(), glossary_package=__package__)
