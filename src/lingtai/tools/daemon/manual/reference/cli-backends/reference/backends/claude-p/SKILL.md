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
  budget/timeout/SDK background caveats. It is not a flag catalog.
version: 0.6.0
last_changed_at: 2026-10-13T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
maintenance: |
  Tracks the claude-p daemon backend flag-discovery and operational-core topics it documents; update when that integration changes.
---

# claude-p Daemon Backend — Flag Discovery Entrypoint

The installed CLI's own help is the authority for Claude Code flags; this page is
only the entrypoint. Conversion rules and persistence live in the parent
[`reference/cli-backends/SKILL.md`](../../../SKILL.md). `claude-p` is the
canonical official-SDK backend id; `claude-code` is a compatibility alias.

## Discover flags from the installed CLI

1. Confirm the CLI is installed: `which claude` → `${HOME}/.local/bin/claude`.
2. Run `claude --version` and `claude --help` in bash for context — the CLI's
   own help still documents the full flag surface, but this harness exposes
   only the closed subset in the table below via `backend_options`. Every
   other flag (`-p`/`--print`, `--dangerously-skip-permissions`,
   `--allowedTools`, `--system-prompt`, `-d`, and anything else) is
   harness-owned: the SDK sets its typed equivalent directly, and
   `backend_options` cannot reach or override it. See Harness boundary.
3. Translate what you need into `backend_options` using the table below; the
   underscore→dash key rule still applies for display, but the daemon
   translates these to typed SDK fields internally, not raw argv.

## Key flags at a glance

Only these `backend_options` keys reach the SDK
(`claude_agent_sdk_runtime.SUPPORTED_SDK_FLAGS`); anything else fails before
any run directory is created.

| `backend_options` key | SDK field | Purpose |
|------|---------|---------|
| `effort` | `effort` | Reasoning effort: `low`/`medium`/`high`/`xhigh`/`max` |
| `model` | `model` | Model choice (the installed CLI's default when omitted) |
| `fallback_model` | `fallback_model` | Fallback model if the primary fails |
| `max_budget_usd` | `max_budget_usd` | Spending limit for the run — the only spend bound; see Harness boundary |
| `tools` | `tools` | Native whitelist: omitted is unrestricted; `[]` disables native tools; an array selects them. Not task-level LingTai `tools` or permission auto-approval `allowed_tools`. |
| `disallowed_tools` | `disallowed_tools` | Native denylist, also applied when a whitelist is present |
| `add_dir` | `add_dirs` (repeatable) | Grant access to additional directories |

## Example: automatic fallback model for a long run

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

Applied after the daemon's env stripping, so it wins over the inherited
environment. The value is used verbatim (`$HOME`/`~` are not expanded — pass an
absolute path). Verified profiles live under `~/.claude-profiles/`; inspect one
with read-only `claude auth status` or `claude -p '/usage'` only — never logout,
re-authenticate, or copy credential files between profiles.

## Subscription & auth

Uses the human's **Claude subscription** (Pro/Max) via `claude login` OAuth
(`~/.claude/.credentials.json`) — no additional API costs. The daemon spawn
strips `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_OAUTH_TOKEN`
so refreshed OAuth wins over stale inherited tokens.

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
> onto the current first-party OAuth/subscription credentials. If you've
> confirmed your environment has no auth overrides you can drop the prefix;
> when in doubt, keep it. **Never echo the variable values while diagnosing —
> they are secrets.**

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
wrapper (the daemon backend strips the override automatically).

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

The harness uses only the official `claude_agent_sdk.ClaudeSDKClient`: one
persistent connection per live run with sequential, non-overlapping per-turn
readers. The SDK spawns the verified `shutil.which("claude")` CLI, never its
bundled fallback. Hidden PTY Claude compatibility is unchanged, not this route or a new feature.
Harness-owned `--settings`, `--print`, `--output-format`, `--mcp-config`,
and `--strict-mcp-config` cannot be overridden via `backend_options`. Typed
`ClaudeAgentOptions` sets `permission_mode="bypassPermissions"`, the
`claude_code` system-prompt preset, and `strict_mcp_config=True`.
Only the table's closed allowlist is accepted. `tools` preserves omitted versus
`[]`; `allowed_tools` is auto-approval, not a whitelist, and it and
`system_prompt` are rejected before run creation.

- MCP: parent-provided MCP registrations are translated in-process into
  `ClaudeAgentOptions.mcp_servers` (stdio only; HTTP stays prompt-catalog-only)
  — no native config file is ever written to the run directory for this
  backend.
- No mandatory finish/checkpoint tool: this backend mounts no `daemon_common`.
  The ordinary reply and per-turn SDK result (success/error and why) signal
  completion; neither `finish(status="done")` nor `checkpoint` is required.
- Ask: a **live** message is atomically queued for the owned connection's next
  turn boundary (`delivery: "turn_boundary"`). `interrupt: true` additionally
  requests only an open current query's interrupt (`delivery: "interrupt"`);
  `daemon_interrupt_delivered`, not acceptance, proves the SDK call ran.
  Between turns input stays queued, cannot interrupt a successor, and needs no checkpoint.
  A **terminal** ask opens a fresh SDK connection with
  `ClaudeAgentOptions(resume=<claude_session_id>, ...)`, re-applying the same
  validated `backend_options`, including model, effort, whitelist, and denylist.
  `daemon.json.claude_session_id` captures the earliest SDK ID (usually init);
  dispatch returning does not guarantee the ID is already persisted.
- Bounds: the supervisor owns the daemon wall-clock deadline; terminal resume
  has its own bounded owner generation. Neither is a spending cap. Use
  `backend_options.max_budget_usd` for the SDK/CLI spend bound.
- Background jobs are not tracked or awaited. With no pending follow-up, the
  runner disconnects after its query result; later job notifications are not
  guaranteed. Injected results never satisfy a pending query. Never background
  work and end expecting later re-entry.
- Auth-env hygiene: the SDK `env` option overlays but cannot unset inherited
  keys, so the daemon strips the three auth variables listed above before
  connecting. Manual shell calls need the full `env -u` wrapper; never print secrets.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Timeout after 30s | For a genuinely short inline task, set an explicit modest bash timeout (for example 300s). For long/complex work, prefer the claude-p daemon backend or a supervised background wrapper instead of blocking the agent turn. |
| Agent appears stuck while `claude -p` runs | You likely used synchronous CLI for work that should have been daemon-backed or supervised in the background. Inspect/kill the child if needed, then resume with a non-blocking wrapper. |
| Claude Code not found | Check `which claude` → `${HOME}/.local/bin/claude` |
| Output truncated | Check whether the run hit the budget limit (`--max-budget-usd N`) |
| Rate limited | Wait and retry; the Claude Max tier is generous (rate-limit tier `default_claude_max_20x`, effectively unlimited for typical use) |
| `Credit balance is too low` | An `ANTHROPIC_*` env override is bypassing the subscription — see the `env -u` wrapper above |
