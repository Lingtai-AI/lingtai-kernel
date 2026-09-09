---
name: daemon-cli-backends
description: >
  Nested daemon-manual reference for backend choice, native versus prompt-only
  MCP, CLI options, list/ask behavior, and per-backend live-help routes.
version: 1.18.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/SKILL.md
- src/lingtai/tools/daemon/CONTRACT.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/lingtai/SKILL.md
- src/lingtai/tools/daemon/manual/reference/dispatch-ledger/SKILL.md
maintenance: |
  Keep this route aligned with the backend enum, native MCP/completion loaders,
  resume behavior, and nested page locations. Keep installed CLI flags in live
  help rather than maintaining a duplicate catalog.
---

# Daemon CLI Backend Reference

Open this only when selecting a backend, passing `backend_options`, or relying
on native MCP, `ask`, or completion. The main `daemon-manual` owns task
authority, immutable `task_files`, terminal notification, compact, and the
capability-versus-permission boundary.

## Choose the execution model

`lingtai` is the native in-process ChatSession backend. It resolves an explicit
preset, curates tools, mounts task-scoped MCP clients, and enforces
`daemon_common.finish(status="done")` when that MCP is present. It has no CLI
and ignores `backend_options`; read its child page for preset/context details.

Every other advertised backend launches an external CLI. Its `task` supplies the
CLI prompt input; runners may add harness context. `prompt` is rejected and `tools` does not grant CLI capabilities.
Selected `skills` remain a compact catalog. Parent MCP registrations are
prompt-visible for all backends, but native tools exist only where this table
has a verified loader. Never infer native checkpoint or completion from prompt
text.

| Backend (aliases) | Initial runner / native MCP | `ask` / completion |
|---|---|---|
| `lingtai` | in-process; task-scoped stdio + HTTP | live-only ask, no terminal resume; `finish(done)` enforced |
| `claude-p` (`claude-code`) | `claude --print ... --mcp-config ... --strict-mcp-config` (stdio) | async resume; checkpoint + `finish(done)` |
| `codex` | `codex exec --json` with `mcp_servers.*` overrides (stdio) | async resume; checkpoint + `finish(done)` |
| `opencode` | `opencode run --format json`, `OPENCODE_CONFIG_CONTENT` (stdio) | async resume; checkpoint + `finish(done)` |
| `qwen-code` (`qwen`) | `qwen --yolo -p`, per-run settings (stdio) | active checkpoint queue; terminal ask unsupported; `finish(done)` |
| `kimicode` (`kimi`) | text mode, run-private `$KIMI_CODE_HOME/mcp.json` (stdio + HTTP) | active checkpoint queue; terminal ask unsupported; `finish(done)` |
| `mimocode` (`mimo`) | `mimo run --format json`; prompt catalog only | async resume; no native checkpoint/completion |
| `oh-my-pi` (`omp`) | `omp --mode json --approval-mode yolo`; prompt catalog only | async resume; no native checkpoint/completion |
| `cursor` | `agent -p --force --output-format stream-json`; prompt catalog only | async resume; no native checkpoint/completion |
| `deepseek` | `dsh --profile headless`; prompt catalog only | terminal ask unsupported; no native checkpoint/completion |

Aliases normalize to the canonical names shown above. Hidden legacy
`claude`/`claude-interactive` entries are not selectable for new work. Native
status is source-backed, not a promise that the vendor CLI is installed or
authenticated; an unavailable CLI fails rather than silently falling back.

MCP-capable workers must call `finish` exactly once before their final report.
`checkpoint` is live-only, bounded, cooperative, and nonterminal: it records
progress and drains queued parent messages at the next checkpoint. It is not chat,
stdin injection, preemption, cancellation, or a completion receipt. A missing
or invalid finish makes the run failed; inspect its durable result and trace.

## Nested reference catalog

The historical `cli-backends` directory also owns the native LingTai child.
The nested catalog is a progressive-disclosure index. Each child keeps only
installed live-help commands, option translation, one-shot/non-interactive
hazards, reserved harness flags, auth hygiene, and proven MCP/resume status.
It is not a maintained vendor flag catalog.

```yaml
- name: daemon-backend-codex
  location: reference/backends/codex/SKILL.md
  description: Codex live-help route for model, effort, and config options.
- name: daemon-backend-opencode
  location: reference/backends/opencode/SKILL.md
  description: OpenCode live-help route for model, variant, agent, and harness flags.
- name: daemon-backend-claude-p
  location: reference/backends/claude-p/SKILL.md
  description: Claude print-mode route for model, fallback, harness, resume, and auth hygiene.
- name: daemon-backend-mimocode
  location: reference/backends/mimocode/SKILL.md
  description: MiMo Code route for CLI options, reserved session flags, and JSONL status.
- name: daemon-backend-qwen-code
  location: reference/backends/qwen-code/SKILL.md
  description: Qwen Code route for options, reserved harness flags, and no terminal ask.
- name: daemon-backend-kimicode
  location: reference/backends/kimicode/SKILL.md
  description: Kimi Code route for options, private MCP config, and resume limits.
- name: daemon-backend-cursor
  location: reference/backends/cursor/SKILL.md
  description: Cursor Agent route for live help, stream-json, usage, and resume.
- name: daemon-backend-deepseek
  location: reference/backends/deepseek/SKILL.md
  description: DeepSeek Harness route for headless profile, patch, and no resume.
- name: daemon-backend-oh-my-pi
  location: reference/backends/oh-my-pi/SKILL.md
  description: Oh-My-Pi route for live help, reserved JSON/session flags, and ask.
- name: daemon-backend-lingtai
  location: reference/backends/lingtai/SKILL.md
  description: Native LingTai route for preset, tools, skills, MCP, context, and finish.
```

## Routing table

| Need | Read |
|---|---|
| Codex flags, config/reasoning, auth, or style choice | `reference/backends/codex/SKILL.md` |
| OpenCode flags, auth, warm server, or agent choice | `reference/backends/opencode/SKILL.md` |
| Claude model/fallback, reserved flags, resume, or auth-env hygiene | `reference/backends/claude-p/SKILL.md` |
| MiMo Code flags, JSONL answer/error, or usage | `reference/backends/mimocode/SKILL.md` |
| Qwen flags, settings path, or no terminal resume | `reference/backends/qwen-code/SKILL.md` |
| Kimi flags, private MCP config, or no resume | `reference/backends/kimicode/SKILL.md` |
| Cursor flags, stream-json usage, or resume | `reference/backends/cursor/SKILL.md` |
| DeepSeek headless launcher, `--patch`, or no resume | `reference/backends/deepseek/SKILL.md` |
| Oh-My-Pi flags, session, or MCP status | `reference/backends/oh-my-pi/SKILL.md` |
| Native preset/tools/skills/MCP/context/finish | `reference/backends/lingtai/SKILL.md` |

## `list`, options, and durable progress

`daemon(action="list", input={})` reads the append-order dispatch ledger, not a
lifetime folder scan. Omitted `last` returns the newest 1000 records; explicit
positive values are honored. `contains` searches the visible index and `status`
filters it. A missing list item is not proof of deletion: use exact
`daemon(action="check", input={"id": "<run_id>"})` for a known legacy run; see
the ledger route for warning codes and non-repair behavior.

For an external CLI, `backend_options` is accepted only inside `emanate`.
Verify the installed command's `--help` in a safe shell before using it; this
manual does not promise vendor flags, install commands, account switching, or
credential repair. A boolean emits a flag, a scalar emits `--flag value`, a
scalar list repeats the flag, and false/null omit it. Unsafe keys, nested values,
and backend-reserved harness flags reject the whole batch before spawn. Keys
use letters/digits and hyphen/underscore separators; underscores emit hyphens. `env`
is the only nested object: it maps valid environment names to string values and
emits no argv. It is merged last; never put secrets in prompts or logs.

Options are persisted as `backend_options`/`backend_argv`, separately from
`backend_harness_argv`. They are not re-passed by `ask`; do not assume every
vendor flag or environment overlay persists across a resumed process.
`lingtai` ignores them. CLI cwd is the granted parent workdir, not the run
artifact directory; an artifact folder is not workspace isolation.
Never override a backend's output, MCP, prompt, approval, or session flags: the
harness owns those boundaries so result parsing and completion cannot be faked.

CLI `ask` returns immediately with `async: true` for resumable sessions; progress
and the final follow-up notice are in `check` and the daemon notification. Only one
follow-up per session may be in flight; a concurrent second ask is `busy`. Qwen,
Kimi, and DeepSeek have no terminal resume contract, though Qwen/Kimi may queue
one active checkpoint correction where the table says so.

## Validation and auth boundary

Before relying on a CLI, confirm its binary and live help, then run only an
appropriately bounded disposable test. Keep vendor subscriptions, login state,
API keys, profiles, and config files under their owner; this manual grants no
login/logout, credential read/copy, account switch, installation, or repair
authority. Never print credential values. Backend pages link official docs and
show the minimum auth model without copying vendor manuals.

## Backend promotion gate

A new backend needs a deterministic non-interactive start, a tested machine-
readable output/session contract where claimed, and tests for launch, parsing,
resume, and reserved options. If a native MCP path is not proven, document
prompt-catalog-only and do not claim `checkpoint` or `finish`.
