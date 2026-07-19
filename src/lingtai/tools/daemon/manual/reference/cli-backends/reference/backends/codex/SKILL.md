---
name: daemon-backend-codex
description: >
  Nested daemon-cli-backends reference for the Codex daemon backend's flag
  surface. Read this only when a daemon task needs Codex-specific CLI flags
  (model selection, reasoning effort, config overrides): it routes you to the
  installed CLI's live help via shell and shows how to translate that help into
  the generic `backend_options` mechanism. It is not a flag catalog.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-openai-codex/SKILL.md
maintenance: |
  Tracks the Codex daemon backend flag-discovery topic it documents; update when that integration changes.
---

# Codex Daemon Backend — Flag Discovery Entrypoint

The installed CLI's own help is the authority for Codex flags; this page is only
the entrypoint. Conversion rules, key safety, and persistence live in the parent
[`reference/cli-backends/SKILL.md`](../../../SKILL.md).

## Discover flags from the installed CLI

1. Load `shell-manual` (its nested `reference/bash-openai-codex/SKILL.md` has
   broader Codex CLI context).
2. Run, in bash: `codex --version`, `codex --help`, and `codex exec --help`.
   The daemon backend wraps `codex exec`, so `codex exec --help` is the
   relevant flag surface. These are local read-only commands; no session is
   started.
3. Translate what you found into `backend_options` with the parent's generic
   conversion rules. Nothing Codex-specific is added to that contract here.

## Example: reasoning effort via the generic `config` route

Codex exposes most of its tunables as repeated `-c, --config <key=value>`
overrides (see `codex exec --help` for the key=value syntax). Through
`backend_options`, a list value repeats the flag once per item:

```jsonc
{
  "backend": "codex",
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "config": ["model_reasoning_effort=\"ultra\""]
    }
  }]
}
// argv: --config model_reasoning_effort="ultra"
```

The effort vocabulary belongs to the installed CLI and the selected model —
LingTai does not validate, enumerate, or simulate effort levels. A value like
`ultra` passes through and the CLI/model decides its semantics (or rejects it).

## Harness boundary

Codex currently declares no reserved-flag list at the validation layer, so
nothing is refused for this backend beyond the generic key/value safety rules.
Still, do not re-set harness-owned surfaces (`--json`, sandbox/approval
bypass, or `mcp_servers.daemon_common.*` config keys): breaking them silently
breaks progress/result extraction and completion enforcement.
