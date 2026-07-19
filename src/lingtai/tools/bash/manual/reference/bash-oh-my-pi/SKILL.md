---
name: bash-oh-my-pi
description: >
  Nested shell-manual reference for Oh-My-Pi CLI. Read this when you need to run,
  validate, or document `omp` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/oh-my-pi/SKILL.md
maintenance: |
  Tracks the Oh-My-Pi CLI backend/topic behavior it documents; update when that integration changes.
---

# Oh-My-Pi CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Use for Pi Coding Agent / Oh-My-Pi subprocesses. Daemon ask/resume uses `--session <id>` once the runner has captured the first session id.

## Command shape

```bash
omp --mode json --approval-mode yolo <prompt>
```
