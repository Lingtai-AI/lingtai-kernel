---
name: bash-qwen-code
description: >
  Nested shell-manual reference for Qwen Code CLI. Read this when you need to run,
  validate, or document `qwen-code / qwen` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/qwen-code/SKILL.md
maintenance: |
  Tracks the Qwen Code CLI backend/topic behavior it documents; update when that integration changes.
---

# Qwen Code CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Use for Qwen Code subprocesses. Verify installed CLI help before passing backend_options; do not confuse provider setup with shell execution.

## Command shape

```bash
qwen-code <prompt> (daemon backend uses the tested command shape in daemon docs)
```
