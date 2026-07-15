---
name: daemon-backend-claude-p
description: >
  Nested daemon-cli-backends reference for the claude-p (alias claude-code)
  daemon backend's flag surface. Read this only when a daemon task needs
  Claude Code-specific CLI flags (model selection, fallback model, tool
  restrictions): it routes you to the installed CLI's live help via bash and
  shows how to translate that help into the generic `backend_options`
  mechanism. It is not a flag catalog.
version: 0.1.0
last_changed_at: "2026-07-14T19:04:52-07:00"
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-claude-code/SKILL.md
maintenance: |
  Tracks the claude-p daemon backend flag-discovery topic it documents; update when that integration changes.
---

# claude-p Daemon Backend — Flag Discovery Entrypoint

This page is deliberately tiny: the Claude Code CLI revs its flags between
releases, so the installed CLI's own help output is the authority — not this
page. `claude-p` is the canonical print-mode backend id; `claude-code` is a
compatibility alias with the same runner and reserved-flag set.

## Discover flags from the installed CLI

1. Load `bash-manual` (its nested `reference/bash-claude-code/SKILL.md` has
   broader Claude Code CLI context).
2. Run, in bash: `claude --version` and `claude --help`. The daemon backend
   wraps `claude --print`, so the print-mode flags in `claude --help` are the
   relevant surface. These are local read-only commands; no session is
   started.
3. Translate the long flags you found into `backend_options` using the
   generic conversion rules in the parent `reference/cli-backends/SKILL.md`
   (key→flag mapping, value handling, key safety, persistence). Nothing
   Claude-specific is added to that contract here.

## Example: automatic fallback model for a long print run

Through `backend_options`, an underscore key becomes a dashed long flag:

```jsonc
{
  "backend": "claude-p",
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "fallback_model": "claude-sonnet-5"
    }
  }]
}
// argv: --fallback-model claude-sonnet-5
```

The model-name vocabulary belongs to the installed CLI and the provider
account — LingTai does not validate, enumerate, or simulate model names. A
value passes through and the CLI decides its semantics (or rejects it).

## Harness boundary

The harness spawns `claude --print --dangerously-skip-permissions
--output-format stream-json --verbose --name <em_id>`, then your
`backend_options` argv, then harness-owned MCP flags, with the task prompt as
the trailing positional argument. Validation therefore refuses the
harness-owned flags `--settings`, `--print`, `--output-format`,
`--mcp-config`, and `--strict-mcp-config` in `backend_options` before spawn:
breaking stream-json output or the per-run MCP config silently breaks
progress/result extraction and completion enforcement.

Related run-scoped behavior you should not fight through flags:

- MCP: the harness writes stdio registrations (including `daemon_common`) to
  the run's `claude-mcp-config.json` and appends `--mcp-config <path>
  --strict-mcp-config` itself as `backend_harness_argv`.
- Safe mode: `--safe-mode` disables customizations including MCP servers; do
  not use it because claude-p terminal success requires the injected
  `daemon_common.finish(status="done")`. For read-only runs, keep MCP enabled
  and combine a read-only brief with the live-help `--allowedTools` surface.
- Resume: `daemon(action="ask")` runs `claude --resume <claude_session_id>
  --print ...` against the session id persisted to
  `daemon.json.claude_session_id`; `backend_options` are not re-passed on
  ask — emanate-time flags persist for the session's life.
- Auth-env hygiene: the spawn environment strips `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, and `CLAUDE_CODE_OAUTH_TOKEN` so the CLI's own
  OAuth credentials win; do not re-inject auth overrides via flags.

To debug what was actually sent, `daemon.json` records the raw
`backend_options` alongside the resolved `backend_argv` / `backend_harness_argv`.
