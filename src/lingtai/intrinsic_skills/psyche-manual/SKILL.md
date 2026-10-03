---
name: psyche-manual
last_changed_at: 2026-09-29T00:00:00Z
description: >
  Compact router for Psyche's four durable domains (pad, lingtai, knowledge,
  skills), the shared file→rebuild model, redacted settings, and the separate
  fixed-instructions disclosure and retirement guidance.
related_files:
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/tools/psyche/ANATOMY.md
- src/lingtai/tools/psyche/settings.py
- src/lingtai/agent.py
- ENVIRONMENT_VARIABLES.md
- src/lingtai/intrinsic_skills/pad-manual/SKILL.md
- src/lingtai/intrinsic_skills/lingtai-manual/SKILL.md
- src/lingtai/tools/knowledge/manual/SKILL.md
- src/lingtai/tools/skills/manual/SKILL.md
- src/lingtai/tools/context/manual/SKILL.md
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/kernel/base_agent/__init__.py
- src/lingtai/tools/avatar/manual/SKILL.md
- src/lingtai/intrinsic_skills/psyche-manual/reference/settings/SKILL.md
- tests/test_psyche_family.py
- tests/test_avatar_rules.py
maintenance: |
  Keep this short router aligned with the psyche Contract/Anatomy, installed
  domain manuals, settings anchors, and retirement guidance. Put
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
| `psyche(action="instructions", input={}, reasoning="orientation")` | current loaded fixed instructions, including runtime and adapter rules; no ambient reread |
| `psyche(action="covenant", input={}, reasoning="load the effective Covenant")` | current loaded Covenant body, not a tool manual; custom source/mirror preserved |
| `psyche(action="settings", input={}, reasoning="inspect Psyche settings")` | redacted settings SHOW |
| `psyche(action="manual", input={}, reasoning="load the routing table")` | this router |

All eight actions require strict empty `input` and are read-only: no authoring,
editing, pinning, installing, migration, catalog rescan, or prompt reload. Keep
root `summarize=false` when exact guidance matters.

Read `covenant` on first orientation and before discussing duties, collaboration,
learning, or memory, not on every call. It returns the current effective body;
ambient edits do not change it until successful reconstruction. No installed
manual/default substitutes for a configured Covenant. Empty means none is loaded.

## One mutation model

Change durable content through `shell` on its owning path — a full rewrite, or
an exact replacement after verifying the old text exists exactly once — anchored
in the authorized working directory, and verify the result after writing. Then
leave it for the next natural molt or refresh to reconstruct; do not rebuild after
every edit. A filesystem mutation never hot-loads; there is no per-domain reload,
and Skills/Knowledge retain catalog ownership. Manual
`context(action="rebuild", input={})` is strongly discouraged (costly full replay
that can disturb prompt-prefix cache reuse); use one targeted call only when the
change must apply in this conversation and a molt is unsuitable. Platform
recipes live in `shell-manual`.

## Lifecycle and settings

`context` owns `molt`, `summarize`, and `rebuild`; `system` owns lifecycle
controls and mechanical names. LingTai owns self-authored character guidance.
`settings` is SHOW-only, fully redacted, and reports the last successful applied
snapshot. Read [the settings reference](reference/settings/SKILL.md) for grammar,
precedence, timing, rollback, and all six anchors.

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

## Retired comments and network rules

`comment`/`comment_file` are removed, including Avatar's spawn comment. Remove
these keys from old owner/config documents and put desired working instructions
in `system/pad.md` through an ordinary authorized edit. Pad remains writable
working memory, not a security boundary or broadcast mechanism.

Neither `.rules` nor `system/rules.md` is read, consumed, or injected on heartbeat,
boot, rebuild, refresh, or molt. Existing files are left untouched. There is no
compatibility reader, automatic rewrite, purge, or data migration.

## Loaded memory length reminder

After a successful molt or CLI refresh relaunch/start, `memory-length` reports
character counts for the **loaded** Pad (including pinned references), Character,
their total and threshold. It is not a token count, disk-size check or knowledge
catalog scan. The environment threshold defaults to 50000; discover its effective
value with `system(action="settings", input={})` under
`prompt.memory_length_warning_chars`, and read the canonical
[`environment registry`](../../../../ENVIRONMENT_VARIABLES.md).

A lifecycle boundary produces at most one warning; normal turns, heartbeat,
initial startup and `context.rebuild` do not produce new warnings. At/below the
threshold a subsequent successful lifecycle check clears only the current
notification; prior checks remain in `logs/events.jsonl`. Publication is best-effort
and never blocks molt/refresh. No Pad, pinned reference, Character, Knowledge or
history content is automatically removed, cropped or rewritten. Review stale and
duplicate content and move it to its proper durable owner through ordinary
scoped edits; a warning is not permission for unrelated cleanup.
