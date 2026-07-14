---
name: daemon-backend-mimocode
description: >
  Nested daemon-cli-backends reference for the MiMo Code (`mimocode` / `mimo`)
  daemon backend's flag surface. Read this only when a daemon task needs
  MiMo-specific CLI flags (model selection, provider switches): it routes you
  to the installed CLI's live help via bash and shows how to translate that
  help into the generic `backend_options` mechanism. It is not a flag catalog.
version: 0.2.0
last_changed_at: "2026-07-11T00:00:00-07:00"
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-mimocode/SKILL.md
maintenance: |
  Tracks the MiMo Code daemon backend flag-discovery topic it documents; update when that integration changes.
---

# MiMo Code Daemon Backend — Flag Discovery Entrypoint

This page is deliberately tiny: it only tells you where the knowledge lives.
The MiMo Code CLI (npm package `@mimo-ai/cli`, binary `mimo`) revs its flags
and accepted values between releases, so the installed CLI's own help output
is the authority — not this page, and not any flag list LingTai could ship.

## Backend identity and spawn shape

`mimo` is a short alias: the daemon canonicalizes it to `mimocode`, and
persisted daemon entries use the canonical name. The backend spawns
`mimo run --format json <prompt>`; converted `backend_options` tokens sit
between the harness flags and the trailing prompt positional.

## Discover flags from the installed CLI

1. Load `bash-manual` (its nested `reference/bash-mimocode/SKILL.md` has
   broader MiMo Code CLI context).
2. Run, in bash: `mimo --version`, `mimo --help`, and `mimo run --help`.
   The daemon backend wraps `mimo run`, so `mimo run --help` is the relevant
   flag surface. These are local read-only commands; no session is started.
3. Translate the long flags you found into `backend_options` using the
   generic conversion rules in the parent `reference/cli-backends/SKILL.md`
   (key→flag mapping, value handling, key safety, persistence). Nothing
   MiMo-specific is added to that contract here.

## Example: model selection via the generic route

```jsonc
{
  "backend": "mimo",  // canonicalizes to "mimocode"
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "model": "mimo-auto"
    }
  }]
}
// spawned argv: mimo run --format json --model mimo-auto <prompt>
```

The flag/value vocabulary belongs to the installed CLI — LingTai does not
validate, enumerate, or simulate it. A value passes through and the CLI
decides its semantics (or rejects it).

## Harness boundary

`mimocode` reserves `--format` (daemon progress/result extraction depends on
`--format json` JSONL events) plus its own session selectors — `--session`
(`-s`), `--continue` (`-c`), and `--fork` — MiMo-specifically. Passing any of
them in `backend_options` refuses the whole batch before any process is
spawned, because session/resume is harness-owned: `daemon(action="ask")`
asynchronously runs
`mimo run --session <mimocode_session_id> --format json <message>`, with the
session id captured from the first session-shaped JSON event into
`daemon.json`. Non-session `backend_options` (e.g. `--model`) still pass
through unchanged.

## Verified JSONL answer/error contract (MiMo Code 0.1.5)

MiMo tags every JSONL event with a `type` and carries a nested `part.text` on
many of them (`reasoning`, `tool`, `step`, `step-start`, ...), not only the
answer. LingTai surfaces the user-visible answer from **only** the
`type == "text"` event's `part.text` string; reasoning/tool/step `part.text`
is ignored and never becomes the daemon result. A structured
`type == "error"` event makes the run fail loudly even when the process exits
0; the human-visible detail is bounded (≤500 chars) and secret-redacted, not
the raw nested payload. The same answer/error contract applies to the `ask`
resume stream. Non-JSON stdout lines are still recorded as `cli_output`.

For source-reported usage, LingTai accepts only `type == "step_finish"` whose
nested `part.type == "step-finish"` has a non-empty string `part.id` and all
five non-negative, non-boolean integer counters: `tokens.input`, `output`, `reasoning`,
`cache.read`, and `cache.write`. It maps input directly, output directly,
reasoning to thinking, and the two cache counters to combined cached tokens.
Malformed or all-zero parts are ignored; each distinct `part.id` is counted
once across initial and resume streams, and the nested source `part` is kept in
the `cli_usage` event raw field. This is UI-only `cli_tokens` state: it never
writes either token ledger and does not infer provider or model.

Per-run `daemon_common` MCP injection is not wired for `mimocode` yet, so the
MCP completion contract is not enforced on this backend; parent MCP
registrations reach the run as prompt catalog only.

To debug what was actually sent, `daemon.json` records the raw
`backend_options` object alongside the resolved `backend_argv` /
`backend_harness_argv` token lists.
