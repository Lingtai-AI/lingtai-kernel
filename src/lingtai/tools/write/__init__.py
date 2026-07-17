"""Write capability — create or overwrite a file.

Usage: Agent(capabilities=["write"]) or capabilities=["file"]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .._file_paths import resolve_workdir_path
from .._manual import load_installed_manual

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent


def get_description(lang: str = "en") -> str:
    return "Create or overwrite a file with the given content. Parent directories are created automatically. Use this for creating new files or complete rewrites. For small changes to existing files, prefer edit. Call write(action='manual') to return the installed file-manual skill."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["manual"], "description": "Use action='manual' to return the installed file-manual skill without writing a file."},
            "file_path": {"type": "string", "description": "Absolute path to the file to write. Required for ordinary writes; omit for action='manual'."},
            "content": {"type": "string", "description": "Content to write. Required for ordinary writes; omit for action='manual'."},
        },
        "required": [],
    }



def setup(agent: "BaseAgent") -> None:
    """Set up the write capability on an agent."""

    def handle_write(args: dict) -> dict:
        if args.get("action") == "manual":
            return load_installed_manual(agent, "file-manual")
        path = args.get("file_path", "")
        if not path:
            return {"status": "error", "message": "file_path is required"}
        if "content" not in args:
            return {"status": "error", "message": "content is required"}
        content = args["content"]
        path = resolve_workdir_path(agent, path)
        try:
            agent._file_io.write(path, content)
            return {"status": "ok", "path": path, "bytes": len(content.encode("utf-8"))}
        except Exception as e:
            return {"status": "error", "message": f"Cannot write {path}: {e}"}

    agent.add_tool("write", schema=get_schema(), handler=handle_write, description=get_description(), glossary_package=__package__)
