---
name: bash-claude-code
description: >
  Nested shell-manual reference for Claude Code CLI. Delegate code implementation, patch writing, documentation, and refactoring to
  Claude Code CLI (Anthropic's coding agent). Runs non-interactively from bash,
  uses the human's Claude Max subscription (no additional API costs), and supports
  quality/effort/budget controls. Use this when you need to write code, generate
  patches, refactor files, create documentation, or do any multi-file code work
  that would be faster delegated than done manually.
version: 1.1.0
tags: [cli, code, delegation, claude, implementation]
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/claude-p/SKILL.md
maintenance: |
  Tracks the Claude Code CLI backend/topic behavior it documents; update when that integration changes.
---

# Claude Code CLI — Code Delegation

> Ownership: this CLI-agent reference now lives under `shell-manual`
> because the workflow is executed as a long-running shell subprocess.
> It was moved from `swiss-knife` during the bash harness migration.

Delegate code work to [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic's coding agent — running non-interactively from bash.

## Prerequisites

- Claude Code installed: `which claude` → `${HOME}/.local/bin/claude`
- Uses the human's **Claude Max subscription** — no additional API costs
- Rate limit tier: `default_claude_max_20x` (effectively unlimited for typical use)

## Quick Usage

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

This runs Claude Code in non-interactive mode (`-p` = print and exit), skipping permission checks for automation.

`claude -p` is itself synchronous: it starts a subprocess, waits, prints stdout,
and exits. Long synchronous runs in an agent's main turn are strongly
discouraged — see `shell-manual` `## Coding-CLI harness baseline`.

### Weekly-limit smoke test

If `claude` reports `You've hit your weekly limit` from inside LingTai but the human recently refreshed Claude Code OAuth credentials, first rule out a stale inherited env token before concluding the subscription is truly exhausted:

```bash
# Do not print token values. This only removes the stale override for the child.
env -u CLAUDE_CODE_OAUTH_TOKEN claude -p 'Reply exactly OK' --allowedTools Read -c
```

If this succeeds while plain `claude -p ...` fails, use the sanitized `env -u ...` wrapper above (and prefer the daemon `claude-code` backend, which strips the override automatically).

> **Why the `env -u …` prefix?** If `ANTHROPIC_API_KEY` (or related `ANTHROPIC_*` variables) is set in the agent environment, the `claude` CLI **prefers the API-key billing path over the Claude Max subscription/OAuth token**. That path can fail with `Credit balance is too low` and bills the API key instead of using the subscription. Separately, a stale inherited `CLAUDE_CODE_OAUTH_TOKEN` can override a refreshed `~/.claude/.credentials.json` and make Claude Code falsely report `You've hit your weekly limit`. Unsetting these variables for the child process forces Claude Code onto the current first-party OAuth/subscription credentials. If you've confirmed your environment has no auth overrides, you can drop the `env -u …` prefix; when in doubt, keep it. **Never echo the variable values while diagnosing — they are secrets.**

### Find and remove the stale-token source

The smoke test above proves a child process can work when the bad override is removed. To make the fix durable, find where the variable is being exported and remove or comment out that source. Common places are shell startup files (`~/.zshrc`, `~/.zprofile`, `~/.bashrc`, `~/.bash_profile`) or launch-service environment configuration.

Safe diagnostic commands:

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

If the variable is hard-coded in a shell startup file, comment out only that export line and keep a backup. A plain `claude` process can then use Claude Code's own refreshed local OAuth credentials instead of a stale environment override. Already-running LingTai agents may still have inherited the old environment until they are refreshed or restarted; for those current processes, keep using the `env -u ...` child-process wrapper.

## CLI vs Daemon — Claude-specific notes

The general CLI-vs-daemon choice, the "a CLI has no LingTai job protocol of its
own" rule, and the short-and-explicitly-timed discipline live in `shell-manual`
`## Coding-CLI harness baseline`. Read that first. What is specific to Claude
Code:

- The daemon form is the LingTai `daemon` capability with `backend="claude-code"`
  (alias of `claude-p`). It runs in its own worktree, context window, and branch.
- **Claude Code daemons** suit exploratory code reading, multi-file edits,
  skill/doc work, and PR composition; Codex daemons suit tightly-scoped
  deterministic diffs. See `../bash-openai-codex/SKILL.md`.
- Claude Code has **no built-in timeout** — the bash tool's timeout is the only
  bound on an inline `claude -p`.
- `--max-budget-usd` bounds spend on ambiguous tasks; there is no daemon-side
  equivalent, so use it whenever the scope is unknown.
- Claude Code reads the repo context itself: give it the goal, constraints, and
  acceptance criteria rather than dumping the codebase into the prompt.

> **Inside `claude -p` (print mode), the inner Claude's own background jobs never notify it back.** `run_in_background`, `&`, and wait-loops are interactive-session affordances; in `--print` there is no second prompt and no `<task-notification>` re-entry, so the model is never woken when the job finishes. If you are the Claude running inside a `claude -p` daemon, **do not background a job and then end your turn waiting for its completion** — run validation synchronously with an adequate explicit timeout and read the result in the same turn, or report a blocker. (The LingTai `claude-p` daemon backend enforces this: a run that ends while awaiting a background-job notification is marked failed, not done.)

The orchestrator/daemon split is the project's default discipline — see
`utilities/lingtai-dev-guide/reference/contributing/SKILL.md` for the full
convention.

## Key Flags

| Flag | Purpose |
|------|---------|
| `-p` / `--print` | Non-interactive mode — run, print result, exit |
| `--dangerously-skip-permissions` | Skip permission prompts (required for automation) |
| `--effort max` | Maximum reasoning effort for complex tasks |
| `--model opus` | Use Opus model for highest quality |
| `--model sonnet` | Use Sonnet model for speed (default) |
| `--max-budget-usd N` | Spending limit per call |
| `--allowedTools "Bash Edit Read Write"` | Restrict which tools Claude can use |
| `--system-prompt "..."` | Custom system prompt |
| `--add-dir /path/to/dir` | Grant access to additional directories |
| `-d /path/to/repo` | Set working directory |

## Recommended Patterns

```bash
# Simple task, default quality
claude -p "fix the typo in README.md" --dangerously-skip-permissions

# Bounded implementation at max quality — run inside a bash tool call with an
# explicit timeout, e.g. timeout=300, and only when blocking the turn is
# acceptable. PR-sized or exploratory work belongs in a daemon.
claude -p "implement the small caching helper described in DESIGN.md" \
  --dangerously-skip-permissions --effort max --model opus

# Budget-capped for an ambiguous scope
claude -p "refactor the auth module" \
  --dangerously-skip-permissions --effort max --model opus --max-budget-usd 5.0

# Target a specific repo
claude -p "add unit tests for the parser module" \
  --dangerously-skip-permissions -d /path/to/repo

# Restrict the tool surface (safer)
claude -p "generate a patch for issue #42" \
  --dangerously-skip-permissions --allowedTools "Bash Edit Read Write"
```

## Best Practices

The generic ones — keep synchronous calls short and explicitly timed, prefer a
daemon or supervised wrapper for long/PR-sized work, checkpoint before
delegating, split large tasks — are in the baseline. Claude-specific:

1. **Use `--effort max` for complex work**: This tells Claude to think harder. Worth it for architecture, refactoring, and multi-file changes — but complexity is also a signal to avoid synchronous `claude -p` in the main turn.
2. **Use `--model opus` for quality**: Opus produces better code for complex logic. Use Sonnet (default) for simple tasks.

## Workflow for Patch/PR Creation

Design a clear spec → choose the execution shape per the baseline → delegate
with explicit constraints and a recovery checkpoint → review the output and run
tests → create the branch, commit, and push as a PR.

## What to Delegate

- **Code implementation**: New features, bug fixes, refactoring
- **Patch generation**: Multi-file changes, API migrations
- **Documentation**: READMEs, docstrings, API docs
- **Test writing**: Unit tests, integration tests
- **Code review**: Ask Claude to review a PR or diff

## What NOT to Delegate

- **Simple one-line edits**: Use the `edit` tool directly
- **File reading/searching**: Use `read`/`grep`/`glob` directly
- **Shell commands**: Use `bash` directly for non-code tasks
- **Tasks requiring your full context**: Claude Code doesn't share your conversation history

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Timeout after 30s | For a genuinely short inline task, set an explicit modest bash timeout (for example 300s). For long/complex work, prefer a daemon or supervised background wrapper instead of blocking the agent turn. |
| Agent appears stuck while `claude -p` runs | You likely used synchronous CLI for work that should have been daemon-backed or supervised in the background. Inspect/kill the child if needed, then resume with a non-blocking wrapper. |
| Claude Code not found | Check `which claude` → `${HOME}/.local/bin/claude` |
| Permission errors | Always include `--dangerously-skip-permissions` |
| Output truncated | Check if Claude hit the budget limit |
| Rate limited | Wait and retry; Max tier has generous limits |
| `Credit balance is too low` despite Claude Code subscription/OAuth being authenticated | `ANTHROPIC_API_KEY` (or another `ANTHROPIC_*` variable) is set and is overriding the OAuth/subscription path. Wrap the call with `env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL -u ANTHROPIC_MODEL -u ANTHROPIC_SMALL_FAST_MODEL claude …` so the child process uses the OAuth/subscription path. Do **not** print the variable values while diagnosing — only their presence/length. |

---
> **Found a bug or issue?** If you encounter any problems with this skill, load the `lingtai-issue-report` skill and follow its instructions to report it.
