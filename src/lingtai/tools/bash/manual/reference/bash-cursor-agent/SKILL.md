---
name: bash-cursor-agent
description: >
  Nested shell-manual reference for Cursor Agent CLI. Read this when you need to run,
  validate, or document `cursor-agent` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/cursor/SKILL.md
maintenance: |
  Tracks the Cursor Agent CLI backend/topic behavior it documents; update when that integration changes.
---

# Cursor Agent CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Use for Cursor Agent CLI subprocesses and daemon backend checks. Prefer async bash; verify `cursor-agent --help` because CLI flags vary.

## Command shape

```bash
cursor-agent --print <prompt>
```
