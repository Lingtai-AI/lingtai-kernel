---
name: bash-kimicode
description: >
  Nested shell-manual reference for Kimi Code CLI. Read this when you need to run,
  validate, or document `kimicode / kimi` as a long-running shell subprocess or
  LingTai daemon harness candidate.
version: 0.1.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/kimicode/SKILL.md
maintenance: |
  Tracks the Kimi Code CLI backend/topic behavior it documents; update when that integration changes.
---

# Kimi Code CLI

Nested shell-manual reference. Read `shell-manual` `## Coding-CLI harness
baseline` first — it owns the shared run-`--help`-before-you-rely rule, the
CLI-vs-daemon choice, the candidate-harness promotion criteria, and the generic
validation checklist. This page adds only what is specific to this CLI.

## Status

Use for MoonshotAI Kimi Code subprocesses (official `MoonshotAI/kimi-code`
single binary `kimi`, version observed 0.22.3 for MCP config support). Keep
provider/model credential discovery elsewhere; this page only owns shell
execution hygiene.

## Command shape

```bash
kimi --prompt '<prompt>' --output-format text
```

- Output formats are `text` or `stream-json` via `--output-format` (note: it is
  `--output-format`, not `--format`).
- Model selection is `-m/--model <model>`.
- **Do not combine `--prompt` with `--yolo`** — the CLI refuses that pairing.
- The daemon backend owns `--prompt` / `--output-format` and forbids `--yolo`;
  free-form `backend_options` are inserted before those owned flags.

## Environment

The daemon backend sets per run (never logging secret values):

- `KIMI_CODE_HOME` — a run-private directory so concurrent runs don't share state.
- `KIMI_DISABLE_TELEMETRY=1`, `KIMI_CODE_NO_AUTO_UPDATE=1`.
- `KIMI_MODEL_API_KEY` — mapped from the first set of `KIMICODE_API_KEY` /
  `KIMI_API_KEY` / `MOONSHOT_API_KEY`, only when not already set.
- `KIMI_MODEL_NAME` / `KIMI_MODEL_PROVIDER_TYPE` / `KIMI_MODEL_BASE_URL` /
  `KIMI_MODEL_MAX_CONTEXT_SIZE` — provider defaults, applied only when absent.

## MCP config evidence

Kimi Code's installed 0.22.3 bundle lists MCP declaration targets at
`$KIMI_CODE_HOME/mcp.json`, project-root `.mcp.json`, and cwd-local
`.kimi-code/mcp.json`. Its MCP schema accepts `transport: "stdio"` with
`command`/`args`/`env` and `transport: "http"` with `url`/`headers` (plus SSE,
which LingTai daemon task registrations do not expose). The daemon uses only the
run-private `$KIMI_CODE_HOME/mcp.json` path so each emanation gets isolated
native MCP config.

## LingTai daemon notes

- The daemon start command is deterministic and non-interactive (one-shot
  `--prompt` + `--output-format text`).
- `ask`/resume is **not supported yet**: `-S/--session` and `-c/--continue`
  exist, but a stable machine-readable session-id output was not verified, so a
  reliable resume contract could not be source-cited. `daemon(action='ask')`
  returns an explicit unsupported-backend error.
- MCP arbitrary-server loading is wired through run-private
  `$KIMI_CODE_HOME/mcp.json`: `daemon_common` plus parent-provided stdio and HTTP
  MCP registrations are native tools for this backend. Prompt and durable
  `daemon.json` contexts still redact `env`/`headers` values; the unredacted
  values live only in the native per-run config.

## Validation — Kimi-specific steps

Run the baseline checklist, with these substitutions:

- Step 2 must additionally confirm that `--yolo` conflicts with `--prompt`.
- Before enabling `ask`, source-cite a stable session-id output plus a tested
  resume command from local help/code — do not guess.
