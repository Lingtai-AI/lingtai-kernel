---
name: psyche-manual
last_changed_at: 2026-09-09T00:00:00Z
description: >
  Compact router for Psyche's four durable domains (pad, lingtai, knowledge,
  skills), the shared file→rebuild model, redacted settings, and the separate
  `.rules` heartbeat reference.
related_files:
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/tools/psyche/ANATOMY.md
- src/lingtai/tools/psyche/settings.py
- src/lingtai/agent.py
- src/lingtai/intrinsic_skills/pad-manual/SKILL.md
- src/lingtai/intrinsic_skills/lingtai-manual/SKILL.md
- src/lingtai/tools/knowledge/manual/SKILL.md
- src/lingtai/tools/skills/manual/SKILL.md
- src/lingtai/tools/context/manual/SKILL.md
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/kernel/base_agent/__init__.py
- src/lingtai/tools/avatar/manual/SKILL.md
- src/lingtai/intrinsic_skills/psyche-manual/reference/settings/SKILL.md
- src/lingtai/intrinsic_skills/psyche-manual/reference/network-rules/SKILL.md
- tests/test_psyche_family.py
- tests/test_avatar_rules.py
maintenance: |
  Keep this short router aligned with the psyche Contract/Anatomy, installed
  domain manuals, settings anchors, and the `.rules` lifecycle signpost. Put
  detailed procedure in the focused references rather than expanding this entry.
---

# Psyche

Psyche is the read-only public root for **pad + lingtai + knowledge + skills**.
Routine schema-sufficient calls may go directly to their action. For unfamiliar
or consequential work, read the matching domain manual first; use this router
only if the domain is unclear. There is no required router-then-domain double read.

## Routing table

| Call (strict `input={}`) | Returns / owner |
|---|---|
| `psyche(action="pad", input={}, reasoning="load Pad guidance")` | Pad manual: `system/pad.md`, `system/pad_append.json` |
| `psyche(action="lingtai", input={}, reasoning="load identity guidance")` | LingTai manual: `system/lingtai.md` |
| `psyche(action="knowledge", input={}, reasoning="load knowledge guidance")` | Knowledge manual: `knowledge/<name>/KNOWLEDGE.md` |
| `psyche(action="skills", input={}, reasoning="load skills guidance")` | Skills manual: `.library/{intrinsic,custom}/` and configured paths |
| `psyche(action="settings", input={}, reasoning="inspect Psyche settings")` | redacted settings SHOW |
| `psyche(action="manual", input={}, reasoning="load the routing table")` | this router |

All six actions require strict empty `input` and are read-only: no authoring,
editing, pinning, installing, migration, catalog rescan, or prompt reload. Keep
root `summarize=false` when exact guidance matters.

## One mutation model

Change durable content with `file.write` (full rewrite) or `file.edit` (exact
replacement) on its owning path, then apply one
`context(action="rebuild", input={}, reasoning="apply durable changes")` (or let
refresh/molt reconstruct). File mutation never hot-loads; there is no per-domain
reload, and Skills/Knowledge retain catalog ownership.

## Lifecycle and settings

`context` owns `molt`, `summarize`, and `rebuild`; `system` owns lifecycle
controls and mechanical names. LingTai owns self-authored character guidance.
`settings` is SHOW-only, fully redacted, and reports the last successful applied
snapshot. Read [the settings reference](reference/settings/SKILL.md) for grammar,
precedence, timing, rollback, and all eight anchors.

### Setting pad
[settings reference](reference/settings/SKILL.md#setting-pad)

### Setting pad file
[settings reference](reference/settings/SKILL.md#setting-pad-file)

### Setting base prompt
[settings reference](reference/settings/SKILL.md#setting-base-prompt)

### Setting base prompt file
[settings reference](reference/settings/SKILL.md#setting-base-prompt-file)

### Setting covenant
[settings reference](reference/settings/SKILL.md#setting-covenant)

### Setting covenant file
[settings reference](reference/settings/SKILL.md#setting-covenant-file)

### Setting comment
[settings reference](reference/settings/SKILL.md#setting-comment)

### Setting comment file
[settings reference](reference/settings/SKILL.md#setting-comment-file)

## Network rules protocol (`.rules`)

`.rules` is a separate heartbeat signal, not a Psyche action or rebuild API. Read
the [network-rules reference](reference/network-rules/SKILL.md) before using it;
its atomic write, consumption, replacement, and verification hazards differ from
ordinary file→rebuild.
