"""Grep capability — search file contents by regex.

Usage: Agent(capabilities=["grep"]) or capabilities=["file"]
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._file_paths import resolve_workdir_path
from .._manual import load_installed_manual

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent


def get_description(lang: str = "en") -> str:
    return "Search file contents for lines matching a regex pattern. Returns matching lines with file path and line number. Searches recursively when given a directory. Use the glob filter to narrow to specific file types. Call grep(action='manual') to return the installed file-manual skill."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["manual"], "description": "Use action='manual' to return the installed file-manual skill without searching."},
            "pattern": {"type": "string", "description": "Regex pattern to search for. Required for ordinary searches; omit for action='manual'."},
            "path": {"type": "string", "description": 'File or directory to search in'},
            "glob": {"type": "string", "description": "File glob filter (e.g., '*.py')", "default": "*"},
            "max_matches": {"type": "integer", "description": 'Maximum matches to return', "default": 200},
            "summary": {"type": "boolean", "description": 'Optional. Default false. When true, this tool runs normally and the raw result is preserved in the durable log (retrievable by tool_call_id), but before the result enters your context it is replaced by an LLM-generated summary driven by your `reasoning` field — so make `reasoning` specific about what to retain. Set true only when the output is expected to be large (>10k chars) and you do NOT need the exact raw text. Leave false when you need exact line/file/diff/stderr text. The summary is non-canonical; if the raw exceeds 500,000 chars no summary is generated and you get a refusal pointing at the preserved raw.', "default": False},
        },
        "required": [],
    }



def setup(agent: "BaseAgent") -> None:
    """Set up the grep capability on an agent."""

    def handle_grep(args: dict) -> dict:
        if args.get("action") == "manual":
            return load_installed_manual(agent, "file-manual")
        pattern = args.get("pattern", "")
        if not pattern:
            return {"status": "error", "message": "pattern is required"}
        search_path = args.get("path", str(agent._working_dir))
        search_path = resolve_workdir_path(agent, search_path)
        max_matches = args.get("max_matches", 200)
        glob_filter = args.get("glob", "*")
        try:
            # Push the glob filter into the service so excluded files are
            # pruned *before* stat / read, instead of scanning every file
            # under the search root and post-filtering the matches. ``"*"``
            # is the schema default and means "no filter".
            service_glob = None if glob_filter in (None, "", "*") else glob_filter
            raw_results = agent._file_io.grep(
                pattern,
                path=search_path,
                max_results=max_matches,
                glob_filter=service_glob,
            )
            matches = [{"file": r.path, "line": r.line_number, "text": r.line} for r in raw_results]
            # truncated: true when the (already glob-pruned) scan hit its
            # cap — there may be more matching files beyond what was
            # scanned.
            truncated = len(raw_results) >= max_matches
            result: dict[str, Any] = {
                "matches": matches,
                "count": len(matches),
                "truncated": truncated,
            }
            # Issue #164: surface traversal budget / exclusion info so the
            # LLM can react to partial results instead of treating them
            # as definitive ("no matches found anywhere").
            stats = getattr(agent._file_io, "last_traversal", None)
            if stats is not None and stats.truncated_reason is not None:
                result["truncated"] = True
                result["truncated_reason"] = stats.truncated_reason
                result["traversal"] = {
                    "visited": stats.visited,
                    "elapsed_ms": stats.elapsed_ms,
                    "dirs_pruned": stats.dirs_pruned,
                    "files_skipped_size": stats.files_skipped_size,
                    "files_skipped_binary": stats.files_skipped_binary,
                }
            return result
        except Exception as e:
            return {"status": "error", "message": f"Grep failed: {e}"}

    agent.add_tool("grep", schema=get_schema(), handler=handle_grep, description=get_description(), glossary_package=__package__)
