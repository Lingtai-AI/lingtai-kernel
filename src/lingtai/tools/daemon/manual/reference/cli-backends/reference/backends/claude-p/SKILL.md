---
name: daemon-backend-claude-p
description: >
  Nested daemon-cli-backends reference for the claude-p (alias claude-code)
  daemon backend's flag surface. Read this only when a daemon task needs
  Claude Code-specific CLI flags (model selection, fallback model, tool
  restrictions): it routes you to the installed CLI's live help via shell and
  shows how to translate that help into the generic `backend_options`
  mechanism. Also owns Claude Code's operational core: the `env -u` auth
  hygiene wrapper, weekly-limit smoke test, stale-token diagnosis, and the
  budget/timeout/print-mode background caveats. It is not a flag catalog.
version: 0.6.0
last_changed_at: 2026-09-30T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
maintenance: |
  Tracks the claude-p daemon backend flag-discovery and operational-core topics it documents; update when that integration changes.
---

# claude-p Daemon Backend — Flag Discovery Entrypoint

The installed CLI's own help is the authority for Claude Code flags; this page is
only the entrypoint. Conversion rules and persistence live in the parent
[`reference/cli-backends/SKILL.md`](../../../SKILL.md). `claude-p` is the
canonical print-mode backend id; `claude-code` is a compatibility alias.

## Discover flags from the installed CLI

1. Confirm the CLI is installed: `which claude` → `${HOME}/.local/bin/claude`.
2. Run `claude --version` and `claude --help` in bash. The daemon wraps
   `claude --print`; the print-mode flags are the relevant surface.
3. Translate what you found into `backend_options` with the parent's generic
   conversion rules. Nothing Claude-specific is added to that contract here.

## Key flags at a glance

The installed CLI's `--help` is still the authority — this table only shortcuts
the flags you will most often translate; it is not a catalog. Long flags
translate through `backend_options` underscore keys per the parent's conversion
rules (`{"fallback_model": "claude-sonnet-5"}` → `--fallback-model
claude-sonnet-5`); the example below shows one full translation.

| Flag | Purpose |
|------|---------|
| `-p` / `--print` | Non-interactive mode — run, print result, exit. Harness-owned: the daemon already passes it (reserved). |
| `--dangerously-skip-permissions` | Skip permission prompts (required for automation). The harness already passes it for every run. |
| `--effort max` | Maximum reasoning effort for complex tasks |
| `--model opus` / `--model sonnet` | Model choice (Sonnet is the default) |
| `--max-budget-usd N` | Spending limit per call — the only spend bound; see Harness boundary |
| `--allowedTools "Bash Edit Read Write"` | Restrict which tools Claude can use |
| `--system-prompt "..."` | Custom system prompt |
| `--add-dir /path/to/dir` | Grant access to additional directories |
| `-d /path/to/repo` | Set working directory |

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

The model-name vocabulary belongs to the installed CLI and the provider account —
LingTai does not validate, enumerate, or simulate model names.

## Profile selection via `backend_options.env`

`backend_options` reserves one non-flag key, `env` (string → string), injected
into the spawned CLI subprocess. Here it picks which Claude profile the run
authenticates as, via `CLAUDE_CONFIG_DIR`:

```jsonc
"backend_options": {
  "env": {"CLAUDE_CONFIG_DIR": "/Users/me/.claude-profiles/phai-labs/config"}
}
// emits no argv token; the variable is set on the spawn instead
```

Applied after the daemon's auth decision (below), so it wins. The value is used
verbatim (`$HOME`/`~` are not expanded — pass an absolute path). Verified profiles live under `~/.claude-profiles/`; inspect one
with read-only `claude auth status` or `claude -p '/usage'` only — never logout,
re-authenticate, or copy credential files between profiles.

## Subscription & auth

Uses the Claude subscription with the `claude-code` provider's auth order (see
the `llm-adapters` manual): a setup-token (parent's `api_key_env`, else
`CLAUDE_CODE_OAUTH_TOKEN`) in a private `CLAUDE_CONFIG_DIR`, else the local
login, else a failed run. `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_BASE_URL`, and `CLAUDE_CODE_USE_*` are always stripped.

### Manual shell calls: the `env -u` auth-hygiene wrapper

Shell remains a supported way to run `claude` — but a *manual* `claude -p`
invocation is not protected by the daemon's env stripping, so wrap it:

```bash
env \
  -u CLAUDE_CODE_OAUTH_TOKEN \
  -u ANTHROPIC_API_KEY \
  -u ANTHROPIC_AUTH_TOKEN \
  -u ANTHROPIC_BASE_URL \
  -u ANTHROPIC_MODEL \
  -u ANTHROPIC_SMALL_FAST_MODEL \
  claude -p "your prompt here" --dangerously-skip-permissions
```

> **Why the `env -u …` prefix?** If `ANTHROPIC_API_KEY` (or related
> `ANTHROPIC_*` variables) is set in the agent environment, the `claude` CLI
> **prefers the API-key billing path over the Claude Max subscription/OAuth
> token** — that path can fail with `Credit balance is too low` and bills the
> API key instead of using the subscription. Separately, a stale inherited
> `CLAUDE_CODE_OAUTH_TOKEN` can override a refreshed
> `~/.claude/.credentials.json` and make Claude Code falsely report `You've hit
> your weekly limit`. Unsetting these variables for the child forces Claude Code
> onto the current first-party OAuth/subscription credentials. When in doubt,
> keep the prefix. **Never echo the variable values — they are secrets.**

### Weekly-limit smoke test

If `claude` reports `You've hit your weekly limit` from inside LingTai but the
human recently refreshed Claude Code OAuth credentials, first rule out a stale
inherited env token before concluding the subscription is truly exhausted:

```bash
# Do not print token values. This only removes the stale override for the child.
env -u CLAUDE_CODE_OAUTH_TOKEN claude -p 'Reply exactly OK' --allowedTools Read -c
```

If this succeeds while plain `claude -p ...` fails, the problem is a stale env
override, not an exhausted subscription — keep using the sanitized `env -u ...`
wrapper. The daemon uses only the resolved setup-token: replace a stale one
(`claude setup-token`) or remove it to fall back to the local login.

### Find and remove the stale-token source

The smoke test proves a child process can work when the bad override is
removed. To make the fix durable, find where the variable is being exported and
remove or comment out that source. Common places are shell startup files
(`~/.zshrc`, `~/.zprofile`, `~/.bashrc`, `~/.bash_profile`) or launch-service
environment configuration. Safe diagnostic commands (names, never values):

```bash
# 1. Check whether macOS launchd is injecting it. Do not print token values.
if launchctl getenv CLAUDE_CODE_OAUTH_TOKEN >/dev/null 2>&1; then
  echo "launchctl may define CLAUDE_CODE_OAUTH_TOKEN"
fi

# 2. Search shell startup files for the variable name, not the value.
grep -n 'CLAUDE_CODE_OAUTH_TOKEN\|ANTHROPIC_API_KEY\|ANTHROPIC_AUTH_TOKEN' \
  ~/.zshenv ~/.zprofile ~/.zshrc ~/.bash_profile ~/.bashrc ~/.profile 2>/dev/null

# 3. Verify a clean future shell does not recreate the variable.
env -u CLAUDE_CODE_OAUTH_TOKEN /bin/zsh -lc \
  'test -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && echo NOT_SET || echo STILL_SET'
```

If the variable is hard-coded in a shell startup file, comment out only that
export line and keep a backup. Already-running LingTai agents may still have
inherited the old environment until they are refreshed or restarted; for those
current processes, keep using the `env -u ...` child-process wrapper.

Official docs: https://platform.claude.com/docs/en/docs/claude-code

## Harness boundary

The harness spawns `claude --print --dangerously-skip-permissions
--output-format stream-json --verbose --name <em_id>`, then your
`backend_options` argv, then harness-owned MCP flags, with the task prompt as
the trailing positional argument. Validation refuses the harness-owned flags
`--settings`, `--print`, `--output-format`, `--mcp-config`, and
`--strict-mcp-config` in `backend_options` before spawn: breaking stream-json
output or the per-run MCP config silently breaks progress/result extraction
and completion enforcement.
Related run-scoped behavior you should not fight through flags:

- MCP: the harness writes stdio registrations (including `daemon_common`) to
  the run's `claude-mcp-config.json` and appends `--mcp-config <path>
  --strict-mcp-config` itself as `backend_harness_argv`.
- Safe mode: `--safe-mode` disables customizations including MCP servers; do
  not use it because claude-p terminal success requires the injected
  `daemon_common.finish(status="done")`. For read-only runs, keep MCP enabled
  and combine a read-only brief with the live-help `--allowedTools` surface.
- Resume: `daemon(action="ask", input={"id": ..., "message": ...})` runs `claude --resume <claude_session_id>
  --print ...` against the session id persisted to `daemon.json.claude_session_id`;
  `backend_options` are not re-passed on ask — emanate-time flags persist for
  the session's life.
- No built-in timeout: Claude Code has **no built-in timeout** — nothing in the
  CLI bounds a run. `--max-budget-usd N` is the only spend bound and there is
  **no daemon-side equivalent**: pass it via `backend_options` whenever the
  scope is unknown, so a runaway task hits a budget cap instead of running
  indefinitely.
- Print-mode background jobs never notify back: inside `claude -p`, the inner
  Claude's own background jobs (`run_in_background`, `&`, and wait-loops) are
  interactive-session affordances; in `--print` there is no second prompt and
  no `<task-notification>` re-entry, so the model is never woken when the job
  finishes. If you are the Claude running inside a `claude -p` daemon, **do not
  background a job and then end your turn waiting for its completion** — run
  validation synchronously with an adequate explicit timeout and read the
  result in the same turn, or report a blocker. The `claude-p` backend enforces
  this: a run that ends while awaiting a background-job notification is marked
  failed, not done.
- Auth-env hygiene: the daemon spawn strips `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, and `CLAUDE_CODE_OAUTH_TOKEN` so refreshed OAuth wins
  over stale inherited tokens (a stale token surfaces as a false "weekly
  limit" — the smoke test above distinguishes it from a truly exhausted
  subscription). Manual shell calls need the full `env -u` wrapper in
  Subscription & auth above. Never print token values.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Timeout after 30s | For a genuinely short inline task, set an explicit modest bash timeout (for example 300s). For long/complex work, prefer the claude-p daemon backend or a supervised background wrapper instead of blocking the agent turn. |
| Agent appears stuck while `claude -p` runs | You likely used synchronous CLI for work that should have been daemon-backed or supervised in the background. Inspect/kill the child if needed, then resume with a non-blocking wrapper. |
| Claude Code not found | Check `which claude` → `${HOME}/.local/bin/claude` |
| Output truncated | Check whether the run hit the budget limit (`--max-budget-usd N`) |
| Rate limited | Wait and retry; the Claude Max tier is generous (rate-limit tier `default_claude_max_20x`, effectively unlimited for typical use) |
| `Credit balance is too low` | An `ANTHROPIC_*` env override is bypassing the subscription — see the `env -u` wrapper above |
