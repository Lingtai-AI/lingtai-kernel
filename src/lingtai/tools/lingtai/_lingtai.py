"""Private LingTai composer for canonical full-context reconstruction.

``_lingtai_load`` is the single canonical writer of the ``character`` prompt
section, composed from ``system/lingtai.md`` alone. Durable mutation goes through
Shell; this module has no public mutator.
"""
from __future__ import annotations


def _lingtai_load(agent, _args: dict, *, publish: bool = True) -> dict:
    """Compose system/lingtai.md into the protected `character` prompt section.

    ``publish=False`` is used only by the canonical full-context reconstruction
    path so every section is composed before one final prompt publication.

    This is the single canonical writer of `character` — the agent's
    self-authored identity (灵台). It is deliberately distinct from the
    operator-supplied `covenant` section (covenant.md, written by
    `Agent._reload_prompt_sections`) and from the mechanical `identity`
    section (name/nickname/manifest, written by BaseAgent). An empty or
    missing lingtai.md deletes the section.
    """
    system_dir = agent._working_dir / "system"
    lingtai_path = system_dir / "lingtai.md"

    character = lingtai_path.read_text(encoding="utf-8") if lingtai_path.is_file() else ""

    if character.strip():
        agent._prompt_manager.write_section(
            "character", character, protected=True,
        )
    else:
        agent._prompt_manager.delete_section("character")
    agent._token_decomp_dirty = True
    if publish:
        agent._flush_system_prompt()

    agent._log("psyche_lingtai_load", size_bytes=len(character.encode("utf-8")))

    return {
        "status": "ok",
        "size_bytes": len(character.encode("utf-8")),
        "content_preview": character[:200],
    }
