---
related_files:
  - tests/test_agents_memory.py
  - src/lingtai/tools/pad/BEHAVIORS.md
  - src/lingtai/tools/pad/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/context/ANATOMY.md
  - src/lingtai/tools/pad/__init__.py
  - src/lingtai/tools/pad/_pad.py
  - src/lingtai/tools/pad/glossary-en.md
  - src/lingtai/tools/pad/glossary-zh.md
  - src/lingtai/tools/pad/glossary-wen.md
  - src/lingtai/intrinsic_skills/pad-manual/SKILL.md
  - src/lingtai/kernel/project/interactive_creation.py
  - src/lingtai/adapters/project_interactive_workspace.py
  - tests/test_project_interactive_creation.py
  - src/lingtai/kernel/project/CONTRACT.md
maintenance: |
  Keep paths real, repo-relative, duplicate-free, and reciprocal with the paired
  Contract and parent/neighbor anatomies. Update this graph when symbols,
  connections, state ownership, or composition move.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# tools/pad

Private owner of unified injected memory and pinned references; public guidance
is the read-only `psyche.pad` manual. No public Pad tool or append action exists.

## Components

- `src/lingtai/tools/pad/_pad.py:66` — `_seed_pad` seeds only a missing/empty Pad before legacy merge.
- `src/lingtai/tools/pad/_pad.py:76` — `_legacy_memory_seed` resolves old inline/file
  init input once, before setup teardown; marked memory never reopens it.
- `src/lingtai/tools/pad/_pad.py:97` — `_merge_legacy_memory` preserves legacy character bytes once into
  Pad, then records `system/lingtai-merged-into-pad` (LABT PD001).
- `src/lingtai/tools/pad/_pad.py:147` — `_pad_load` reads the unified body plus `pad_append.json` references
  into the derived `pad` section; it clears any stale `character` slot.
- `src/lingtai/tools/pad/__init__.py:31` exports only the private composer, not a tool declaration.

## Connections

Psyche boot calls `_pad_load`; Agent setup seeds Pad and preserves disk/configured
legacy memory before that boot. Staged-init direct boot defers migration until
canonical setup/rebuild has its input mapping. Then
`Agent._reload_prompt_sections` uses the same seed/composer during canonical
rebuild, refresh and the single post-molt hook. Shell edits remain disk-only.
Migration writes no legacy source and never rereads project instruction files.

## Composition

Parent: [tools](../ANATOMY.md). Promise: [Contract](CONTRACT.md).
Procedure: [Pad manual](../../intrinsic_skills/pad-manual/SKILL.md).

## State

`system/pad.md` and `system/pad_append.json` remain the private memory sources.
The one-time marker survives Pad rewrites and deep Avatar copies. Legacy
`system/lingtai.md` is retained untouched; it is no longer an active source once
marked. Prompt `pad` and `system/system.md` are derived.

## Notes

See [PD001](BEHAVIORS.md#behavior-pd001) for lossless/idempotent migration and
real init/rebuild/molt acceptance. No instruction-file bodies are auto-injected.

The fresh interactive Project Core seed carries optional Recipe comment content
as typed out-of-band creation data, not an init field. Its publication adapter
calls the existing `_seed_pad` only for absent/empty canonical Pad; nonempty
memory survives. No public action or hot-load path is added. The linked Project
Contract owns the creation transaction and PRJ011 guards this publication seam.
