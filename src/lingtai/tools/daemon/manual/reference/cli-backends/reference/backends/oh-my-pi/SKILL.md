---
name: daemon-backend-oh-my-pi
description: >
  Nested daemon-cli-backends reference for the Oh-My-Pi (`omp`) daemon
  backend's flag surface. Read this only when a daemon task needs
  Oh-My-Pi-specific CLI flags (model selection, tool or provider switches):
  it routes you to the installed CLI's live help via shell and shows how to
  translate that help into the generic `backend_options` mechanism. It is
  not a flag catalog.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-oh-my-pi/SKILL.md
maintenance: |
  Tracks the Oh-My-Pi daemon backend flag-discovery topic it documents; update when that integration changes.
---

# Oh-My-Pi Daemon Backend — Flag Discovery Entrypoint

The installed CLI's own help is the authority for Oh-My-Pi (npm package
`@oh-my-pi/pi-coding-agent`, binary `omp`) flags; this page is only the
entrypoint. Conversion rules, key safety, and persistence live in the parent
[`reference/cli-backends/SKILL.md`](../../../SKILL.md). `omp` is an accepted
backend alias that canonicalizes to `oh-my-pi`; persisted daemon entries use the
canonical name.

## Discover flags from the installed CLI

1. Load `shell-manual` (its nested `reference/bash-oh-my-pi/SKILL.md` has
   broader Oh-My-Pi CLI context).
2. Run, in bash: `omp --version` and `omp --help`. The daemon backend wraps
   the root `omp` invocation (no subcommand), so the root help is the
   relevant flag surface. These are local read-only commands; no session is
   started. Run `omp <command> --help` only on demand for a subcommand the
   installed root help itself lists — subcommands are outside the daemon
   wrapper.
3. Translate what you found into `backend_options` with the parent's generic
   conversion rules. Nothing Oh-My-Pi-specific is added to that contract here.

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

The model vocabulary belongs to the installed CLI and its configured providers —
LingTai does not validate, enumerate, or simulate model ids. Use the CLI's own
discovery surface (root `--help`) and pass the id through.

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
