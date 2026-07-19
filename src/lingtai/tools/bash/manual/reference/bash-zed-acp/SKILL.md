---
name: bash-zed-acp
description: >
  Nested shell-manual reference for Zed / ACP agent bridge. Read this when you need to run,
  validate, or document `ACP servers` as a long-running shell subprocess or LingTai
  daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/bash/_async_supervisor.py
maintenance: |
  Tracks the Zed / ACP agent bridge backend/topic behavior it documents; update when that integration changes.
---

# Zed / ACP agent bridge

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Ecosystem bridge rather than a daemon backend by itself. Document only unless there is a direct headless ACP client command to wrap.

## Command shape

```bash
agent-specific ACP command
```

## Validation — ACP-specific step before the baseline checklist

ACP is a protocol, not a standalone `ACP` binary, so there is no single command
to probe. First identify the specific ACP-compatible client or server command
you intend to wrap, then run the baseline checklist against *that* command
(using its documented equivalent when it has no `--help`).
