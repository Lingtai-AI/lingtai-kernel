---
name: bash-openhands
description: >
  Nested shell-manual reference for OpenHands CLI. Read this when you need to run,
  validate, or document `openhands` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/bash/_async_supervisor.py
maintenance: |
  Tracks the OpenHands CLI backend/topic behavior it documents; update when that integration changes.
---

# OpenHands CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Candidate harness. Docs mention headless mode with `--task`/`--file` and JSONL; dependency footprint is heavier, so document first unless tests can mock it safely.

## Command shape

```bash
openhands --task "<prompt>" --json
```
