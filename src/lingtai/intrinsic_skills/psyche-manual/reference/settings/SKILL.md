---
name: psyche-settings-reference
last_changed_at: 2026-09-09T00:00:00Z
description: >
  Deep reference for Psyche's redacted settings SHOW, strict owner-document
  grammar, precedence, timing, rollback, and eight stable anchors.
related_files:
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
- src/lingtai/tools/psyche/settings.py
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/agent.py
- tests/test_psyche_family.py
- tests/test_psyche_prompt_settings.py
maintenance: |
  Keep this focused reference aligned with the applied-snapshot provider,
  `settings/psyche.json` parser, reconstruction rollback, redaction, and stable
  `psyche-manual#setting-*` anchors. Keep the parent as a short router.
---

# Psyche settings reference

This page owns settings grammar and application details; the public `settings`
action remains SHOW-only.

## Settings SHOW

```text
psyche(action="settings", input={}, reasoning="inspect Psyche prompt configuration")
```

The result has exactly eight rows in this order: `pad`, `pad_file`,
`base_prompt`, `base_prompt_file`, `covenant`, `covenant_file`, `comment`,
`comment_file`. Every row has exactly `key`, `current`, `default`, `configurable`, and `comment`; both values are always `<redacted>`, including
empty/null defaults. It reports the last successfully applied reconstruction,
not ambient edits. All eight rows are configurable, but SHOW is not a content
comparison: even after a successful change it remains fully redacted. A failed
reconstruction preserves that snapshot; provider failure returns only the fixed
`SETTINGS_UNAVAILABLE` failure, never partial rows or parser details. The complete
SHOW response also has the generic 65,536-byte UTF-8 bound.

## Owner document

`settings/psyche.json` is optional. If present, it is a stable-read UTF-8 JSON
object beginning `{"schema_version": 1, ...}` with only the six string fields
`base_prompt`, `base_prompt_file`, `covenant`, `covenant_file`, `comment`, and
`comment_file`. Duplicate/unknown keys, wrong versions/types, invalid
UTF-8/JSON, Boolean versions, symlinks/non-regular files, reads over 64 KiB, races, and I/O
failures reject the candidate before prompt publication. There is no environment
layer, mutation action, migration, or writeback.

For each pair, a readable `*_file` wins; a missing file falls back to inline.
`~` expands and relative pointers resolve against the agent workdir. Edit this
owner with `file.write`/`file.edit`, then apply atomically with `context.rebuild`
(or refresh/molt), after explicit configuration authorization. Legacy top-level
init spellings for **these six fields** are inert; external writers must emit
this owner document. Before an authorized upgrade/reconstruction, preserve and
transfer any still-needed legacy values into it: the runtime never migrates them.
Base/covenant may fall back to existing mirrors, while comment has no mirror and
is removed when absent. Invalid owner input is rejected before refresh teardown;
failed final publication restores the prior prompt generation, mirrors and SHOW.

Pad seeds are different: top-level `init.json` `pad`/`pad_file` remain their owner;
they are not fields in `settings/psyche.json` (which rejects them). There is no
environment layer for them. Edit the authorized seed source or its referenced
file; full reconstruction resolves it again. To change a nonempty durable Pad,
edit `system/pad.md` instead—changing its configured seed will not overwrite it.

### Setting pad
Configured initial Pad seed; default `""`. `pad_file` wins when readable,
otherwise inline `pad`; reconstruction seeds `system/pad.md` only when missing or
empty and never overwrites a nonempty durable Pad.

### Setting pad file
Initial Pad pointer; default `null`, resolved against workdir. It is still only a
seed for a missing/empty Pad.

### Setting base prompt
Optional third-party prompt body; default `""`; nonempty content mirrors to
`system/base_prompt.md`.

### Setting base prompt file
Optional pointer; default `null`; a readable file wins over inline.

### Setting covenant
Optional protected operator contract; default `""`; nonempty content mirrors to
`system/covenant.md`.

### Setting covenant file
Optional pointer; default `null`; a readable file wins over inline.

### Setting comment
Optional unprotected comment; default `""`; unlike the other prompt bodies it has
no `system/*.md` mirror.

### Setting comment file
Optional pointer; default `null`; a readable file wins over inline and remains
redacted.
