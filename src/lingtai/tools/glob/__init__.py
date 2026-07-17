"""Glob capability — find files by pattern.

Usage: Agent(capabilities=["glob"]) or capabilities=["file"]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .._file_paths import resolve_workdir_path
from .._manual import load_installed_manual

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent


def get_description(lang: str = "en") -> str:
    return "Find files matching a glob pattern. Normal searches are the primary operation: omit action for the legacy ordinary call or use action='glob' explicitly. Use '**/' for recursive search (e.g. '**/*.py' finds all Python files). Returns sorted list of matching file paths. Use action='manual' once to return the installed file-manual skill; after the manual result, continue the original ordinary glob instead of repeating manual, because repeated identical manual calls are an error loop."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["glob", "manual"], "description": "Omit action for the legacy ordinary glob, use action='glob' for an explicit ordinary search, or use action='manual' once for the installed file-manual skill."},
            "pattern": {"type": "string", "description": "Glob pattern (e.g., '**/*.py'). Required for ordinary searches; omit for action='manual'."},
            "path": {"type": "string", "description": 'Directory to search in'},
            "summary": {"type": "boolean", "description": 'Optional. Default false. When true, this tool runs normally and the raw result is preserved in the durable log (retrievable by tool_call_id), but before the result enters your context it is replaced by an LLM-generated summary driven by your `reasoning` field — so make `reasoning` specific about what to retain. Set true only when the output is expected to be large (>10k chars) and you do NOT need the exact raw text. Leave false when you need exact line/file/diff/stderr text. The summary is non-canonical; if the raw exceeds 500,000 chars no summary is generated and you get a refusal pointing at the preserved raw.', "default": False},
        },
        "required": [],
    }



def setup(agent: "BaseAgent") -> None:
    """Set up the glob capability on an agent."""

    def handle_glob(args: dict) -> dict:
        action = args.get("action")
        if action == "manual":
            return load_installed_manual(agent, "file-manual")
        if action is not None and action != "glob":
            return {"status": "error", "message": f"Unsupported action for glob: {action!r}"}
        pattern = args.get("pattern", "")
        if not pattern:
            return {"status": "error", "message": "pattern is required"}
        search_dir = args.get("path", str(agent._working_dir))
        search_dir = resolve_workdir_path(agent, search_dir)
        try:
            matches = agent._file_io.glob(pattern, root=search_dir)
            result: dict = {"matches": matches, "count": len(matches)}
            # Issue #164: surface traversal budget / exclusion info so the
            # LLM can react to partial results instead of treating them
            # as definitive ("no files found anywhere").
            stats = getattr(agent._file_io, "last_traversal", None)
            if stats is not None and stats.truncated_reason is not None:
                result["truncated"] = True
                result["truncated_reason"] = stats.truncated_reason
                result["traversal"] = {
                    "visited": stats.visited,
                    "elapsed_ms": stats.elapsed_ms,
                    "dirs_pruned": stats.dirs_pruned,
                }
            return result
        except Exception as e:
            return {"status": "error", "message": f"Glob failed: {e}"}

    agent.add_tool("glob", schema=get_schema(), handler=handle_glob, description=get_description(), glossary_package=__package__)
