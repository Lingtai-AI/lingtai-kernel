---
name: daemon-backend-codex
description: >
  Nested daemon-cli-backends reference for the Codex daemon backend's flag
  surface. Read this only when a daemon task needs Codex-specific CLI flags
  (model selection, reasoning effort, config overrides): it routes you to the
  installed CLI's live help via shell and shows how to translate that help into
  the generic `backend_options` mechanism. It is not a flag catalog.
version: 0.1.0
last_changed_at: "2026-07-09T18:55:23-07:00"
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-openai-codex/SKILL.md
maintenance: |
  Tracks the Codex daemon backend flag-discovery topic it documents; update when that integration changes.
---

# Codex Daemon Backend — Flag Discovery Entrypoint

This page is deliberately tiny: it only tells you where the knowledge lives.
The Codex CLI revs its flags and accepted values between releases, so the
installed CLI's own help output is the authority — not this page, and not any
flag list LingTai could ship.

## Discover flags from the installed CLI

1. Load `shell-manual` (its nested `reference/bash-openai-codex/SKILL.md` has
   broader Codex CLI context).
2. Run, in bash: `codex --version`, `codex --help`, and `codex exec --help`.
   The daemon backend wraps `codex exec`, so `codex exec --help` is the
   relevant flag surface. These are local read-only commands; no session is
   started.
3. Translate the long flags you found into `backend_options` using the
   generic conversion rules in the parent `reference/cli-backends/SKILL.md`
   (key→flag mapping, value handling, key safety, persistence). Nothing
   Codex-specific is added to that contract here.

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
`ultra` passes through and the CLI/model decides its semantics (or rejects
it).

## Harness boundary

Codex currently declares no reserved-flag list at the validation layer, so
nothing is refused for this backend beyond the generic key/value safety rules.
Still, do not re-set harness-owned surfaces (`--json`, sandbox/approval
bypass, or `mcp_servers.daemon_common.*` config keys): breaking them silently
breaks progress/result extraction and completion enforcement.

To debug what was actually sent, `daemon.json` records the raw
`backend_options` object alongside the resolved `backend_argv` /
`backend_harness_argv` token lists.
