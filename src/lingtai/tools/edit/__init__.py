"""Edit capability — exact string replacement in a file.

Usage: Agent(capabilities=["edit"]) or capabilities=["file"]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .._file_paths import resolve_workdir_path

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent


def get_description(lang: str = "en") -> str:
    return 'Replace an exact string in a file. Fails if old_string is not found or is ambiguous.'


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": 'Absolute path to the file to edit'},
            "old_string": {"type": "string", "description": 'The exact text to find and replace'},
            "new_string": {"type": "string", "description": 'The replacement text'},
            "replace_all": {"type": "boolean", "description": 'Replace all occurrences', "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    }



def setup(agent: "BaseAgent") -> None:
    """Set up the edit capability on an agent."""

    def handle_edit(args: dict) -> dict:
        path = args.get("file_path", "")
        if not path:
            return {"status": "error", "message": "file_path is required"}
        path = resolve_workdir_path(agent, path)
        old = args.get("old_string", "")
        new = args.get("new_string", "")
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
