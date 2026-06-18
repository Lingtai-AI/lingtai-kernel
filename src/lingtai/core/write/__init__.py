"""Write capability — create or overwrite a file.

Usage: Agent(capabilities=["write"]) or capabilities=["file"]
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...i18n import t

if TYPE_CHECKING:
    from lingtai_kernel.base_agent import BaseAgent


def get_description(lang: str = "en") -> str:
    return t(lang, "write.description")


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": t(lang, "write.file_path")},
            "content": {"type": "string", "description": t(lang, "write.content")},
        },
        "required": ["file_path", "content"],
    }



def make_handler(agent: "BaseAgent"):
    """Build the ``write`` tool handler bound to *agent*.

    Single source of truth for the write behavior: both ``setup()`` (the normal
    capability-registration path) and the SDK file-mutation bundle bridge
    (``lingtai.core.file_bundle``) wire this same closure, so the bundle-hosted
    tool runs byte-identical logic to the registered tool — including the same
    overwrite (``agent._file_io.write``) side effect against the same
    ``agent._working_dir`` path resolution.
    """

    def handle_write(args: dict) -> dict:
        path = args.get("file_path", "")
        content = args.get("content", "")
        if not path:
            return {"status": "error", "message": "file_path is required"}
        if not Path(path).is_absolute():
            path = str(agent._working_dir / path)
        try:
            agent._file_io.write(path, content)
            return {"status": "ok", "path": path, "bytes": len(content.encode("utf-8"))}
        except Exception as e:
            return {"status": "error", "message": f"Cannot write {path}: {e}"}

    return handle_write


def setup(agent: "BaseAgent") -> None:
    """Set up the write capability on an agent."""
    lang = agent._config.language
    agent.add_tool(
        "write",
        schema=get_schema(lang),
        handler=make_handler(agent),
        description=get_description(lang),
    )
