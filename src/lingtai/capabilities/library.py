"""Library capability — shared skill store with registration and catalog injection.

Skills are Markdown files (SKILL.md) with YAML frontmatter that teach the
agent specialized behaviors.  All skills live in a shared git-tracked store
at ``.lingtai/.library/<skill-name>/SKILL.md`` — a sibling directory to the
agent working dirs, accessible to every agent in the same network.

Individual skill folders may themselves be git repos (cloned from a remote).
The outer ``.library`` repo tracks the files and ignores inner ``.git`` dirs.

Usage: Agent(capabilities=["library"])

Tool actions:
    register — validate all skill folders, git add + commit changes.
    refresh  — rescan the store and re-inject the XML catalog into the
               system prompt so newly added skills become available.

SKILL.md format::

    ---
    name: my-skill
    description: One-line description of what this skill does
    version: 1.0.0
    ---

    Full instructions in Markdown…

Required frontmatter: name, description.
Optional frontmatter: version, author, tags (list[str]).
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..i18n import t

if TYPE_CHECKING:
    from lingtai_kernel.base_agent import BaseAgent

log = logging.getLogger(__name__)

PROVIDERS = {"providers": [], "default": "builtin"}

# ---------------------------------------------------------------------------
# Frontmatter parser (minimal, no PyYAML dependency)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_KV_RE = re.compile(r"^(\w[\w-]*)\s*:\s*(.+)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML-like frontmatter from a SKILL.md file.

    Only handles simple ``key: value`` lines (no nesting, no lists).
    Returns a dict of string key→value pairs.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    return {kv.group(1): kv.group(2).strip() for kv in _KV_RE.finditer(block)}


def _resolve_library_dir(agent: "BaseAgent") -> Path:
    """Resolve the shared skill library directory for this agent's network.

    Skills live at ``.lingtai/.library/`` — a sibling to agent working dirs.
    Agent working dirs are ``<network>/<agent-name>/``, so the library dir
    is ``<network>/.library/``.
    """
    return agent._working_dir.parent / ".library"


# ---------------------------------------------------------------------------
# XML catalog builder
# ---------------------------------------------------------------------------

def _escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _build_catalog_xml(skills: list[dict], lang: str) -> str:
    """Build the XML skill catalog for system prompt injection.

    Each *skill* dict has keys: name, description, path.
    """
    if not skills:
        return ""

    lines = [
        t(lang, "library.preamble"),
        "",
        "<available_skills>",
    ]
    for sk in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{_escape_xml(sk['name'])}</name>")
        lines.append(f"    <description>{_escape_xml(sk['description'])}</description>")
        lines.append(f"    <location>{_escape_xml(sk['path'])}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill scanner
# ---------------------------------------------------------------------------

def _parse_skill_file(
    skill_file: Path,
    label: str,
) -> tuple[dict | None, dict | None]:
    """Parse a single SKILL.md. Returns (skill_dict, None) or (None, problem_dict)."""
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError as e:
        return None, {"folder": label, "reason": f"cannot read SKILL.md: {e}"}

    fm = _parse_frontmatter(text)
    name = fm.get("name", "")
    description = fm.get("description", "")
    if not name:
        return None, {"folder": label, "reason": "SKILL.md missing required frontmatter field: name"}
    if not description:
        return None, {"folder": label, "reason": "SKILL.md missing required frontmatter field: description"}

    return {
        "name": name,
        "description": description,
        "version": fm.get("version", ""),
        "path": str(skill_file),
    }, None


def _scan_library_recursive(
    directory: Path,
    valid: list[dict],
    problems: list[dict],
    prefix: str = "",
) -> None:
    """Recursively scan a directory for skills.

    A directory is classified as:
    - **Skill folder**: contains ``SKILL.md`` → parse it, stop recursing.
    - **Group folder**: no ``SKILL.md``, contains only subdirectories → recurse.
    - **Corrupted**: no ``SKILL.md``, contains loose non-directory files → refuse.
    """
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue

        label = f"{prefix}{child.name}" if prefix else child.name
        skill_file = child / "SKILL.md"

        # Skill folder — has SKILL.md, parse and stop.
        if skill_file.is_file():
            sk, prob = _parse_skill_file(skill_file, label)
            if sk:
                valid.append(sk)
            if prob:
                problems.append(prob)
            continue

        # No SKILL.md — check if it's a valid group folder.
        children = list(child.iterdir())
        has_loose_files = any(
            not c.is_dir() and not c.name.startswith(".")
            for c in children
        )
        if has_loose_files:
            problems.append({
                "folder": label,
                "reason": "not a skill (no SKILL.md) and has loose files — corrupted",
            })
            continue

        # Pure group folder — recurse.
        _scan_library_recursive(child, valid, problems, prefix=f"{label}/")


def _scan_library(library_dir: Path) -> tuple[list[dict], list[dict]]:
    """Scan ``library_dir`` for skill folders (recursive).

    Supports arbitrary nesting: a directory with ``SKILL.md`` is a skill;
    a directory containing only subdirectories is a group folder (recurse);
    a directory with loose files but no ``SKILL.md`` is corrupted.

    Returns (valid_skills, problems).
    """
    if not library_dir.is_dir():
        return [], []

    valid: list[dict] = []
    problems: list[dict] = []
    _scan_library_recursive(library_dir, valid, problems)
    return valid, problems


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(library_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in the library directory."""
    return subprocess.run(
        ["git", *args],
        cwd=str(library_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _ensure_git_repo(library_dir: Path) -> None:
    """Ensure the library directory exists and is a git repo."""
    library_dir.mkdir(parents=True, exist_ok=True)

    gitdir = library_dir / ".git"
    if not gitdir.exists():
        _git(library_dir, "init")
        # Ignore inner .git dirs (skills that are themselves git repos)
        gitignore = library_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("**/.git\n")
        _git(library_dir, "add", ".gitignore")
        _git(library_dir, "commit", "-m", "init skill library")


# ---------------------------------------------------------------------------
# Tool actions
# ---------------------------------------------------------------------------

def _action_register(agent: "BaseAgent", library_dir: Path) -> dict:
    """Validate skill folders, git add + commit changes."""
    _ensure_git_repo(library_dir)
    valid, problems = _scan_library(library_dir)

    # Stage all changes (new files, modifications, deletions)
    _git(library_dir, "add", "-A")

    # Check if there's anything to commit
    status = _git(library_dir, "status", "--porcelain")
    staged = status.stdout.strip()

    commit_msg = ""
    if staged:
        # Build commit message from skill names
        skill_names = [s["name"] for s in valid]
        msg = f"register: {', '.join(skill_names)}" if skill_names else "register: update skills"
        result = _git(library_dir, "commit", "-m", msg)
        if result.returncode == 0:
            commit_msg = msg
        else:
            commit_msg = f"commit failed: {result.stderr.strip()}"

    # Re-inject catalog (or clear if no valid skills remain)
    lang = agent._config.language
    catalog_xml = _build_catalog_xml(valid, lang)
    if catalog_xml:
        agent.update_system_prompt("library", catalog_xml, protected=True)
    else:
        agent.update_system_prompt("library", "", protected=True)

    return {
        "status": "ok",
        "library_dir": str(library_dir),
        "registered": [
            {"name": s["name"], "description": s["description"], "version": s["version"]}
            for s in valid
        ],
        "problems": problems,
        "committed": commit_msg,
    }


def _action_refresh(agent: "BaseAgent", library_dir: Path) -> dict:
    """Rescan skills and re-inject the XML catalog into system prompt."""
    valid, problems = _scan_library(library_dir)

    lang = agent._config.language
    catalog_xml = _build_catalog_xml(valid, lang)
    if catalog_xml:
        agent.update_system_prompt("library", catalog_xml, protected=True)
    else:
        agent.update_system_prompt("library", "", protected=True)

    return {
        "status": "ok",
        "library_dir": str(library_dir),
        "loaded": [
            {"name": s["name"], "description": s["description"], "version": s["version"]}
            for s in valid
        ],
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# Capability setup
# ---------------------------------------------------------------------------

def get_description(lang: str = "en") -> str:
    return t(lang, "library.description")


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["register", "refresh"],
                "description": t(lang, "library.action"),
            },
        },
        "required": ["action"],
    }


def setup(agent: "BaseAgent") -> None:
    """Set up the library capability — tool + initial catalog injection."""
    lang = agent._config.language
    library_dir = _resolve_library_dir(agent)

    def handle_library(args: dict) -> dict:
        action = args.get("action", "")
        if action == "register":
            return _action_register(agent, library_dir)
        elif action == "refresh":
            return _action_refresh(agent, library_dir)
        else:
            return {"status": "error", "message": f"unknown action: {action!r}, use 'register' or 'refresh'"}

    agent.add_tool(
        "library",
        schema=get_schema(lang),
        handler=handle_library,
        description=get_description(lang),
    )

    # Initial catalog injection — scan and inject on startup
    valid, _ = _scan_library(library_dir)
    if valid:
        catalog_xml = _build_catalog_xml(valid, lang)
        agent.update_system_prompt("library", catalog_xml, protected=True)
        log.info("library: injected %d skill(s) from %s", len(valid), library_dir)
    else:
        log.debug("library: no skills found in %s", library_dir)
