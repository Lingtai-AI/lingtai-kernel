---
name: daemon-backend-opencode
description: >
  Nested daemon-cli-backends reference for the OpenCode daemon backend's flag
  surface. Read this only when a daemon task needs OpenCode-specific CLI flags
  (model selection, provider-specific reasoning variants, agent choice): it
  routes you to the installed CLI's live help via bash and shows how to
  translate that help into the generic `backend_options` mechanism. It is not
  a flag catalog.
version: 0.1.0
last_changed_at: "2026-07-09T19:22:21-07:00"
---

# OpenCode Daemon Backend — Flag Discovery Entrypoint

This page is deliberately tiny: it only tells you where the knowledge lives.
The OpenCode CLI revs its flags and accepted values between releases, so the
installed CLI's own help output is the authority — not this page, and not any
flag list LingTai could ship.

## Discover flags from the installed CLI

1. Load `bash-manual` (its nested `reference/bash-opencode/SKILL.md` has
   broader OpenCode CLI context: providers, agents, config files).
2. Run, in bash: `opencode --version`, `opencode --help`, and
   `opencode run --help`. The daemon backend wraps `opencode run`, so
   `opencode run --help` is the relevant flag surface. These are local
   read-only commands; no session is started.
3. Translate the long flags you found into `backend_options` using the
   generic conversion rules in the parent `reference/cli-backends/SKILL.md`
   (key→flag mapping, value handling, key safety, persistence). Nothing
   OpenCode-specific is added to that contract here.

## Example: model and reasoning variant

OpenCode selects models as `provider/model` via `-m, --model`, and exposes
provider-specific reasoning effort as `--variant` (see `opencode run --help`
for both). Through `backend_options`, plain scalars become `--flag <value>`:

```jsonc
{
  "backend": "opencode",
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "model": "anthropic/claude-sonnet-4-5",
      "variant": "high"
    }
  }]
}
// argv: --model anthropic/claude-sonnet-4-5 --variant high
```

The model and variant vocabularies belong to the installed CLI and the
selected provider — LingTai does not validate, enumerate, or simulate them. A
value passes through and the CLI/provider decides its semantics (or rejects
it).

## Harness boundary

OpenCode reserves `--format` at the validation layer: the daemon owns
`opencode run --format json` so its per-line JSON event parsing keeps working,
and passing `--format` in `backend_options` refuses the whole batch before
spawn. Beyond that, do not re-set harness-owned surfaces: session flags
(`--session` / `--continue`) belong to `daemon(action="ask")` resume
(`opencode run --session <opencode_session_id> --format json ...`), and the
completion MCP is injected through the `OPENCODE_CONFIG_CONTENT` environment
variable — not argv — so breaking either silently breaks progress/result
extraction and completion enforcement.

To debug what was actually sent, `daemon.json` records the raw
`backend_options` object alongside the resolved `backend_argv` /
`backend_harness_argv` token lists. For OpenCode, `backend_harness_argv`
holds a sentinel token pair that the runner converts into the
`OPENCODE_CONFIG_CONTENT` environment variable rather than real argv flags.
