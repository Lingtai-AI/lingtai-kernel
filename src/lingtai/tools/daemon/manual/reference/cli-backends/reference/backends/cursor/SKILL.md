---
name: daemon-backend-cursor
description: >
  Nested daemon-cli-backends reference for the Cursor daemon backend's flag
  surface. Read this only when a daemon task needs Cursor-specific CLI flags
  (model selection, output/tooling switches): it routes you to the installed
  CLI's live help via shell and shows how to translate that help into the
  generic `backend_options` mechanism. It is not a flag catalog.
version: 0.1.0
last_changed_at: "2026-07-09T19:23:56-07:00"
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-cursor-agent/SKILL.md
maintenance: |
  Tracks the Cursor daemon backend flag-discovery topic it documents; update when that integration changes.
---

# Cursor Daemon Backend — Flag Discovery Entrypoint

This page is deliberately tiny: it only tells you where the knowledge lives.
The Cursor Agent CLI revs its flags and accepted values, so installed CLI help
is the authority — not this page, and not any flag list LingTai could ship.

## Discover flags from the installed CLI

1. Load `shell-manual` (its nested `reference/bash-cursor-agent/SKILL.md` has
   broader Cursor Agent CLI context).
2. Run `agent --version` and `agent --help` in bash. The daemon spawns root
   `agent` directly — `agent -p --force --output-format stream-json <prompt>` —
   so root help is authoritative (open a subcommand only if root routes there).
   These are read-only checks; keychain errors happen before help on some builds.
3. Translate the long flags you found into `backend_options` using the
   generic conversion rules in the parent `reference/cli-backends/SKILL.md`
   (key→flag mapping, value handling, key safety, persistence). Nothing
   Cursor-specific is added to that contract here.

## Example: model selection via the generic route

`backend_options` keys become long flags, inserted after the harness-owned
flags and before the task prompt:

```jsonc
{
  "backend": "cursor",
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "model": "opus"
    }
  }]
}
// argv: --model opus
```

The flag and model vocabulary belong to the installed CLI — LingTai does
not validate, enumerate, or simulate them. Confirm `--model` and its
accepted values in your installed `agent --help` before relying on this;
an unknown flag or value is the CLI's error, not the daemon's.

## Source-pinned stream-json usage
For installed `agent-cli@2026.05.28-a70ca7c`, accept usage only from `type=result`, `subtype=success`, `is_error=false` with all four non-negative integer fields: `inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`.
`inputTokens` is already net; UI-only `cli_tokens` adds read + write as cached, preserves raw usage, ignores invalid/all-zero blocks, and counts duplicate terminal results once.
Join model only from a preceding matching `system/init` by `session_id`; provider stays unknown because this source emits no provider identity (do not infer it from `apiKeySource`, model, backend, credentials, or environment). This is version-pinned, not a cross-release claim.

## Harness boundary

Cursor currently declares no reserved-flag list at the validation layer, so
nothing is refused for this backend beyond the generic key/value safety
rules. Still, do not re-set harness-owned surfaces: `-p` (non-interactive
print mode), `--force` (allows file modifications in print mode),
`--output-format stream-json` (one JSON event per stdout line — the daemon's
progress/result parser and the session-id capture depend on it), and
`--resume` (owned by `daemon(action='ask')` follow-ups, which replay the
captured session id).

Session and completion behavior, source-verified: the first stream event
carrying a session-id-shaped field is stored in `daemon.json` as
`cursor_session_id`; `ask` resumes with
`agent -p --force --resume <cursor_session_id> --output-format stream-json
<message>` (async, one follow-up in flight per session). Per-run MCP
injection — including the `daemon_common` completion MCP — is not wired for
this backend yet, so there are no MCP loader flags to collide with and no
`finish` contract: success comes from the stream's final result event and
the process exit code.

`backend_options` is honored only at `emanate` time; `ask` follow-ups reuse
the session without re-passing it. To debug what was actually sent,
`daemon.json` records the raw `backend_options` object alongside the
resolved `backend_argv` token list.
