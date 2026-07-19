---
name: bash-goose
description: >
  Nested shell-manual reference for Goose CLI. Read this when you need to run,
  validate, or document `goose` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/bash/_async_supervisor.py
maintenance: |
  Tracks the Goose CLI backend/topic behavior it documents; update when that integration changes.
---

# Goose CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Candidate harness. Docs mention start/resume sessions and `--no-session`; exact headless command should be verified before backend implementation.

## Command shape

```bash
goose run/session commands (verify current `goose --help`)
```
