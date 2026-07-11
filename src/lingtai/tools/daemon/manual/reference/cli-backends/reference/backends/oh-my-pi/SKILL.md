---
name: daemon-backend-oh-my-pi
description: >
  Nested daemon-cli-backends reference for the Oh-My-Pi (`omp`) daemon
  backend's flag surface. Read this only when a daemon task needs
  Oh-My-Pi-specific CLI flags (model selection, tool or provider switches):
  it routes you to the installed CLI's live help via bash and shows how to
  translate that help into the generic `backend_options` mechanism. It is
  not a flag catalog.
version: 0.1.0
last_changed_at: "2026-07-09T19:24:35-07:00"
---

# Oh-My-Pi Daemon Backend — Flag Discovery Entrypoint

This page is deliberately tiny: it only tells you where the knowledge lives.
The Oh-My-Pi CLI (npm package `@oh-my-pi/pi-coding-agent`, binary `omp`) revs
its flags and accepted values between releases, so the installed CLI's own
help output is the authority — not this page, and not any flag list LingTai
could ship. `omp` is an accepted backend alias that canonicalizes to
`oh-my-pi`; persisted daemon entries use the canonical name.

## Discover flags from the installed CLI

1. Load `bash-manual` (its nested `reference/bash-oh-my-pi/SKILL.md` has
   broader Oh-My-Pi CLI context).
2. Run, in bash: `omp --version` and `omp --help`. The daemon backend wraps
   the root `omp` invocation (no subcommand), so the root help is the
   relevant flag surface. These are local read-only commands; no session is
   started. Run `omp <command> --help` only on demand for a subcommand the
   installed root help itself lists — subcommands are outside the daemon
   wrapper.
3. Translate the long flags you found into `backend_options` using the
   generic conversion rules in the parent `reference/cli-backends/SKILL.md`
   (key→flag mapping, value handling, key safety, persistence). Nothing
   Oh-My-Pi-specific is added to that contract here.

## Example: model selection

```jsonc
{
  "backend": "oh-my-pi",
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "model": "<model id from the installed CLI>"
    }
  }]
}
// argv: --model <model id from the installed CLI>
```

The model vocabulary belongs to the installed CLI and its configured
providers — LingTai does not validate, enumerate, or simulate model ids. Use
the CLI's own discovery surface (root `--help`) and
pass the id through; the CLI decides its semantics (or rejects it).

## Harness boundary: reserved flags

The daemon owns Oh-My-Pi's non-interactive JSON harness
(`omp --mode json --approval-mode yolo <task>`). `backend_options` refuses
exactly these reserved flags before spawn (one bad key refuses the whole
batch):

`--mode`, `--print`, `--auto-approve`, `--yolo`, `--approval-mode`,
`--session`, `--resume`, `--continue`, `--no-session`, `--session-dir`

Keys are written without leading dashes, and underscores become dashes, so
`{"approval_mode": "write"}` targets `--approval-mode` and is refused.
Overriding these would break JSON event capture, re-enable interactive
prompting, or hijack session ownership.

## Session, ask, and MCP status

- The first `type:session` JSON header line carries the resumable session id;
  the OpenCode-family parser stores it as `oh_my_pi_session_id` in
  `daemon.json`.
- `ask` is async and resumes via
  `omp --mode json --approval-mode yolo --session <id> <message>`;
  emanate-time `backend_options` persist for the session and are not
  re-passed.
- Per-run `daemon_common` MCP injection is **not wired yet** for this backend
  (pending source evidence of an accepted config/env path), so the MCP
  `finish` completion contract does not apply here — and `backend_options`
  is not the place to hand-wire MCP.
- To debug what was actually sent, `daemon.json` records the raw
  `backend_options` object alongside the resolved `backend_argv` /
  `backend_harness_argv` token lists.
