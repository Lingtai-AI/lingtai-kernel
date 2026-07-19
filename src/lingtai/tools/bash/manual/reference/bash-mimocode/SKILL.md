---
name: bash-mimocode
description: >
  Nested shell-manual reference for MiMo Code CLI. Read this when you need to run,
  validate, or document `mimocode / mimo` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/mimocode/SKILL.md
maintenance: |
  Tracks the MiMo Code CLI backend/topic behavior it documents; update when that integration changes.
---

# MiMo Code CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Use for Xiaomi MiMo Code subprocesses. Keep provider/model credential discovery in `swiss-knife` xiaomi-mimo; this page only owns shell execution hygiene.

## Command shape

```bash
mimocode <prompt> (daemon backend uses the tested command shape in daemon docs)
```
