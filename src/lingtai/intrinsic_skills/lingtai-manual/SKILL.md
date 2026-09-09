---
name: lingtai-manual
description: |
  Read before changing character/identity in `system/lingtai.md`, choosing forced
  versus self-evolve mode, or applying durable identity edits.
version: 2.0.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/lingtai/__init__.py
- src/lingtai/tools/lingtai/_lingtai.py
- src/lingtai/tools/lingtai/CONTRACT.md
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
- src/lingtai/tools/context/manual/SKILL.md
maintenance: |
  Keep the manual-only Psyche route, generic file ownership, no-hot-load rule,
  identity modes, and context reconstruction route aligned with code.
---

# LingTai Manual

LingTai (`灵台`) is the character that distinguishes this agent from others.
Its durable source is `system/lingtai.md`; reconstruction renders it into the
protected `character` prompt section.

## Public call

```text
psyche(action="lingtai", input={}, reasoning="load identity guidance")
```

This is the only public LingTai call and only returns this manual. It performs no
disk or prompt mutation; there is no update, load, or reload action or alias.

## Change durable identity

Rewrite with
`file(action="write", input={"file_path": "system/lingtai.md", "content": "..."}, reasoning="rewrite identity")`
or make a bounded exact change with
`file(action="edit", input={"file_path": "system/lingtai.md", "old_string": "...", "new_string": "...", "replace_all": null}, reasoning="edit identity")`.
Preserve all character content you intend to keep when rewriting.
Neither hot-loads the prompt; use one
`context(action="rebuild", input={}, reasoning="apply identity change")`.

- **Self-evolve:** an absent or empty **resolved** `lingtai` value (inline or
  from `lingtai_file`) preserves and composes the self-authored file. These
  inputs belong to top-level `init.json`, not `settings/psyche.json`.
- **Forced:** a nonempty configured identity materializes into the file on every
  reconstruction, so a file edit is replaced at the next rebuild, refresh, or
  molt.

Keep character separate from operator `covenant`, third-party `base_prompt`, and
mechanical `identity`; names remain `system(action="name_set"|"name_nickname")`.
Before molt, update identity only when the task's lessons genuinely changed who
you are, then use `context-manual` for the journal/summary/molt procedure.
