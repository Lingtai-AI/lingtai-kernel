---
name: daemon-backend-opencode
description: >
  Nested daemon-cli-backends reference for the OpenCode daemon backend's flag
  surface. Read this only when a daemon task needs OpenCode-specific CLI flags
  (model selection, provider-specific reasoning variants, agent choice): it
  routes you to the installed CLI's live help via shell and shows how to
  translate that help into the generic `backend_options` mechanism. It is not
  a flag catalog.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-opencode/SKILL.md
maintenance: |
  Tracks the OpenCode daemon backend flag-discovery topic it documents; update when that integration changes.
---

# OpenCode Daemon Backend — Flag Discovery Entrypoint

The installed CLI's own help is the authority for OpenCode flags; this page is
only the entrypoint. Conversion rules, key safety, and persistence live in the
parent [`reference/cli-backends/SKILL.md`](../../../SKILL.md).

## Discover flags from the installed CLI

1. Load `shell-manual` (its nested `reference/bash-opencode/SKILL.md` has
   broader OpenCode CLI context: providers, agents, config files).
2. Run, in bash: `opencode --version`, `opencode --help`, and
   `opencode run --help`. The daemon backend wraps `opencode run`, so
   `opencode run --help` is the relevant flag surface. These are local
   read-only commands; no session is started.
3. Translate what you found into `backend_options` with the parent's generic
   conversion rules. Nothing OpenCode-specific is added to that contract here.

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

The model and variant vocabularies belong to the installed CLI and the selected
provider — LingTai does not validate, enumerate, or simulate them.

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

In `daemon.json`, OpenCode's `backend_harness_argv` holds a sentinel token pair
that the runner converts into the `OPENCODE_CONFIG_CONTENT` environment variable
rather than real argv flags.
