---
name: bash-openai-codex
description: >
  Nested shell-manual reference for OpenAI Codex CLI. Manual (not a tool) for OpenAI Codex CLI — OpenAI's coding agent that runs
  locally from your terminal. Built in Rust for speed and efficiency. Supports
  headless remote control, Vim editing, plugin management, hooks, and Chrome
  browser integration. Read this when the human asks to use OpenAI Codex CLI,
  wants to compare it with Claude Code, or needs help with installation and
  configuration.
version: 1.1.0
tags: [cli, code, delegation, openai, codex]
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/codex/SKILL.md
maintenance: |
  Tracks the OpenAI Codex CLI backend/topic behavior it documents; update when that integration changes.
---

# OpenAI Codex CLI

> Ownership: this CLI-agent reference now lives under `shell-manual`
> because the workflow is executed as a long-running shell subprocess.
> It was moved from `swiss-knife` during the bash harness migration.

> **OpenAI's coding agent — run locally from your terminal.**
> Built in Rust for speed. Open source. ~4 million weekly active users (as of April 2026).

## CLI vs Daemon — Codex-specific notes

The general CLI-vs-daemon choice, the "a CLI has no LingTai job protocol of its
own" rule, and the responsiveness practices (short explicitly-timed synchronous
calls, checkpoint before delegating, split large tasks) live in `shell-manual`
`## Coding-CLI harness baseline`. Read that first.

**Codex vs Claude Code — a different axis.** CLI-vs-daemon is about the *shape*
of the work; Codex-vs-Claude-Code is about its *style*:

- **Codex** — tightly-scoped diffs, deterministic refactors, mechanical
  validation sweeps. More conservative; the right choice when the change is
  well-specified and the scope is clear.
- **Claude Code** — exploratory code reading, multi-file edits, skill/doc work,
  PR composition.

The daemon form is the LingTai `daemon` capability with `backend="codex"`. See
`utilities/lingtai-dev-guide/reference/contributing/SKILL.md` for the full
orchestrator/daemon convention.

**Inline CLI examples:**

```bash
# Rename a symbol across a small file
codex exec "rename the function foo() to fooBar() in utils/helpers.py"

# Mechanical lint-style pass
codex exec --model gpt-5.5 "remove unused imports from src/main.py"

# Quick scoped fix
codex exec --dir /path/to/project "fix the off-by-one in parse_range()"
```

## Installation

```bash
npm install -g @openai/codex@0.130.0
```

Update existing installation:
```bash
codex update
# or
npm i -g @openai/codex@latest
```

## Configuration

### API Key
Set your OpenAI API key:
```bash
export OPENAI_API_KEY="your-api-key"
```

Or configure in `~/.codex/config.toml`:
```toml
[api]
key = "your-api-key"
```

### Models
Codex CLI supports multiple models:
- GPT-5.5 (latest, recommended)
- GPT-5.4
- GPT-5.3-Codex (specialized for coding)

Configure in `config.toml`:
```toml
[model]
default = "gpt-5.5"
```

### Bedrock Auth
For AWS Bedrock, use console-login credentials:
```bash
aws login
codex exec "your prompt"
```

## Key Features

### 1. Remote Control
New in 0.130.0 — headless, remotely controllable app-server:
```bash
codex remote-control
codex connect localhost:8080
```
- Start a headless app-server
- Control Codex remotely
- Page large threads with different view modes (unloaded/summary/full)

### 2. Vim Editing
Full Vim modal editing in the TUI:
```bash
codex exec "your prompt"
# In TUI:
/vim                    # Toggle Vim mode
:set default-mode=insert  # Set default mode
```

### 3. Plugin Management
Workspace sharing and marketplace:
```bash
codex plugins list      # List installed plugins
codex plugins install @openai/plugin-name  # Install from marketplace
codex plugins share     # Share with workspace
```

Features:
- Workspace sharing with access controls
- Source filtering and local share path tracking
- Marketplace removal/upgrades
- Remote bundle sync
- Admin-disabled status handling

### 4. Hooks
Browseable and toggleable hooks:
```bash
codex hooks list        # List available hooks
codex hooks toggle      # Toggle hook on/off
codex exec --hooks my-hook "<prompt>"
```

Capabilities:
- Before/after compaction support
- PreToolUse context injection
- Codex Apps auth integration
- MCP elicitations through TUI/Guardian flows

### 5. Chrome Extension
Browser integration without takeover: works in parallel across tabs, operates in
the background, and the user controls which websites Codex may use. Install from
the Chrome Web Store.

### 6. App-Server
Thread management and pagination inside the TUI: resume/fork picker, raw
scrollback mode, `/ide` context injection, and `/diff` workspace-aware diffing.

## Codex vs Claude Code — capability differences

Both ship a LingTai daemon backend and a bash subskill. The *style* axis is
above under "CLI vs Daemon"; these are the capability facts behind it:

| Feature | OpenAI Codex CLI | Claude Code |
|---------|------------------|-------------|
| Language | Rust | TypeScript |
| Open Source | ✅ Yes | ❌ No |
| Vim Support | ✅ Native | ❌ No |
| Browser Extension | ✅ Chrome | ❌ No |
| Remote Control | ✅ Yes (headless app-server) | ❌ No |
| Plugin Marketplace | ✅ Rich | ❌ Limited |
| Local file operations | ✅ Excellent | ✅ Good |
| Complex reasoning | ✅ Good | ✅ Excellent |
| Cost | API usage | Claude Max subscription |

So reach for Codex CLI specifically for browser automation (Chrome extension),
remote/headless development (`codex remote-control`), the plugin marketplace, or
native Vim editing; reach for Claude Code for deep multi-step analysis and
subscription-based cost.

## Troubleshooting

### Common Issues

1. **Installation fails**
   ```bash
   # Clear npm cache
   npm cache clean --force
   # Reinstall
   npm install -g @openai/codex@0.130.0
   ```

2. **API key not found**
   ```bash
   # Check environment variable
   echo $OPENAI_API_KEY
   # Or check config file
   cat ~/.codex/config.toml
   ```

3. **Plugin installation fails**
   ```bash
   # Check marketplace connectivity
   codex plugins search
   # Clear plugin cache
   rm -rf ~/.codex/plugins/cache
   ```

4. **Agent appears stuck while `codex exec` runs**
   - You likely used synchronous CLI for work that should have been daemon-backed or supervised in the background.
   - Inspect the child process and worktree. If needed, kill the child so the blocking bash call returns.
   - Resume with the LingTai daemon Codex backend or a supervised background wrapper that records logs, timeout, cancellation path, and recovery notes.

## Resources

- **GitHub**: https://github.com/openai/codex
- **Documentation**: https://developers.openai.com/codex
- **Changelog**: https://developers.openai.com/codex/changelog
- **Chrome Extension**: Available on Chrome Web Store

## Version History

- **0.130.0** (May 8, 2026): Remote control, plugin hooks, Bedrock auth
- **0.129.0** (May 7, 2026): Vim editing, Chrome extension, plugin management
- **0.128.0** (May 5, 2026): /goal command, Ralph loop

---
> **Found a bug or issue?** If you encounter any problems with this skill, load the `lingtai-issue-report` skill and follow its instructions to report it.
