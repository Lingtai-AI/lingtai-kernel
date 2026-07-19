---
name: daemon-backend-kimicode
description: >
  Nested daemon-cli-backends reference for the Kimi Code daemon backend's flag
  surface. Read this only when a daemon task needs Kimi-specific CLI flags
  (model selection, skills/workspace directories): it routes you to the
  installed CLI's live help via shell and shows how to translate that help into
  the generic `backend_options` mechanism. It is not a flag catalog.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-kimicode/SKILL.md
maintenance: |
  Tracks the Kimi Code daemon backend flag-discovery topic it documents; update when that integration changes.
---

# Kimi Code Daemon Backend — Flag Discovery Entrypoint

The installed CLI's own help is the authority for Kimi Code flags; this page is
only the entrypoint. Conversion rules, key safety, and persistence live in the
parent [`reference/cli-backends/SKILL.md`](../../../SKILL.md). `kimi` is the
accepted short alias; persisted daemon entries use the canonical backend name
`kimicode`.

## Discover flags from the installed CLI

1. Load `shell-manual` (its nested `reference/bash-kimicode/SKILL.md` has
   broader Kimi Code CLI context: per-run environment contract, MCP config
   evidence, validation checklist).
2. Run, in bash: `kimi --version` and `kimi --help`. The daemon backend wraps
   the top-level one-shot mode (`kimi --prompt <prompt> --output-format
   text`), so the top-level help is the relevant flag surface — there is no
   `exec`-style wrapper subcommand. Run `kimi <subcommand> --help` only when a
   task actually needs one of the listed subcommands. These are local
   read-only commands; no session is started.
3. Translate what you found into `backend_options` with the parent's generic
   conversion rules. Nothing Kimi-specific is added to that contract here.

## Example: model selection

`kimi --help` lists `-m, --model <model>` for per-invocation model choice.
Through `backend_options`, a string value becomes `--flag <value>`:

```jsonc
{
  "backend": "kimicode",   // or the accepted alias "kimi"
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "model": "kimi-for-coding"
    }
  }]
}
// argv: kimi --model kimi-for-coding --prompt <prompt> --output-format text
```

The model vocabulary belongs to the installed CLI and its provider
configuration — LingTai does not validate, enumerate, or simulate model names.

## Harness boundary

Kimi Code declares a reserved-flag list at the validation layer; passing any
of these in `backend_options` refuses the whole batch before spawn:
`--prompt` / `-p`, `--output-format`, `--yolo` / `-y`, `--session` / `-S`,
`--continue` / `-c`. LingTai owns `--prompt` and `--output-format` (they
drive the non-interactive text-capture harness), forbids `--yolo` (the CLI
refuses `--prompt` combined with `--yolo`), and reserves the
session/continue flags because resume is not wired for this backend: no
stable machine-readable session-id output was verified, so
`daemon(action="ask")` returns an explicit unsupported-backend error — start
a new kimicode emanation instead.

Free-form options are inserted between `kimi` and the owned flags (the
prompt travels via `--prompt`, never as a trailing positional). Output is
plain text, not a JSON event stream: stdout is recorded verbatim, line by
line, as `cli_output` events, no session id is captured, and the joined
stdout becomes the result. The run-private MCP loader is not argv-based —
the daemon writes `daemon_common` plus parent stdio and HTTP registrations
to `<run>/kimi-code-home/mcp.json` (path recorded in `daemon.json` under
`backend_harness_files.kimicode_mcp_config`); secret env/header values stay
out of prompts and logs.
