---
name: bash-gemini-cli
description: >
  Nested shell-manual reference for Gemini CLI. Read this when you need to run,
  validate, or document `gemini` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/bash/_async_supervisor.py
maintenance: |
  Tracks the Gemini CLI backend/topic behavior it documents; update when that integration changes.
---

# Gemini CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Candidate harness. Public docs indicate non-interactive prompt mode, YOLO approval, and resume support; add daemon backend only after exact command/session parsing is verified.

## Command shape

```bash
gemini -p "<prompt>" / gemini --prompt "<prompt>"
```
