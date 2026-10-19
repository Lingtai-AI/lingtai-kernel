---
name: daemon-manual
description: >
  Use for unfamiliar daemon delegation, backend-specific work, stalled or
  failed runs, completion checks, or consent-gated cleanup. Routine calls can
  start from the advertised schema.
version: 0.19.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- tests/test_tool_decision_phase.py
- src/lingtai/kernel/tool_decision.py
- src/lingtai/adapters/tool_decision.py
- src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md
- src/lingtai/tools/daemon/CONTRACT.md
- src/lingtai/tools/daemon/ANATOMY.md
- src/lingtai/services/daemon.py
- src/lingtai/tools/web_search/__init__.py
- src/lingtai/tools/web_search/manual/SKILL.md
- src/lingtai/cli_daemon.py
- src/lingtai/tools/daemon/system_prompt.py
- src/lingtai/tools/daemon/settings.py
- src/lingtai/tools/daemon/execution_host.py
- src/lingtai/tools/daemon/shell_prompt_events.py
- src/lingtai/tools/daemon/manual/reference/forensics/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cleanup/SKILL.md
- src/lingtai/tools/daemon/manual/reference/dispatch-ledger/SKILL.md
- src/lingtai/tools/bash/manual/SKILL.md
maintenance: |
  Keep this as the shortest Daemon router. Put backend recipes, artifact
  forensics, ledger diagnosis, and cleanup detail in nested references.
  Preserve capability-versus-permission and durable-store boundaries.
---

# Daemon Manual — Router

Use the advertised schema for routine calls. Read one direct owner below for an
unfamiliar or high-consequence workflow. Daemons are disposable workers in the
parent working directory, not durable persona, hidden memory, or authority to
widen the parent task.

## Safe first dispatch

`task` is the complete parent-controlled objective, authority, approved paths,
safety/collaboration limits, tool policy, backend, and deliverable. `tools`
grants capability, not permission; never invent authority, broaden the task,
change credentials/configuration without approval, or infer daemon-manager
authority. Parent MCP tools are not inherited: pass complete one-run `mcp`
registrations when needed. Runtime redacts secret env/header values in prompt
context; do not replace required launch inputs with redaction placeholders.

`task_files` are UTF-8 text inputs below the parent workdir. Preflight follows
symlinks, enforces containment/encoding/size, and content-addresses bytes into
the immutable read-only `daemons/_task_files/` store before scheduling. Workers
receive manifest metadata and snapshot paths, never mutable originals or embedded
contents. Any malformed, missing, out-of-root, oversized, or non-UTF-8 entry
rejects the whole batch; there is no original-path fallback.

`backend="lingtai"` is the native in-process session: its explicit `preset`
comes from `system(action="presets")`, blank/omitted `prompt` means exactly
`Begin the assigned daemon task.`, and `finish(status="done")` is required
when `daemon_common` is present. External CLI backends use `task` as their
prompt input (the runner may add harness context), reject `prompt`, and do not
gain tools from `tools`; native MCP,
checkpoint, completion, and resume status is backend-specific. Use
`backend_options` only at `emanate` after checking the installed CLI `--help`.

```jsonc
daemon(action="emanate", input={
  "tasks": [{"task": "Do bounded work and leave reports/out.md.",
              "tools": ["shell"], "preset": "/approved/preset.json"}],
  "backend": "lingtai"
}, reasoning="bounded delegation")
```

## Actions and completion

`emanate` dispatches ids plus `group_id`; `list` inspects the index; `ask` sends
a backend-specific follow-up; `check` reads state/events/artifact metadata and
durable result/error paths; `reclaim` cancels all running work owned by this manager, not one id,
and leaves evidence. Confirm the whole affected scope before calling it. `list`, `check`, `settings`, and `manual` are read-only; `emanate`, `ask`, and `reclaim` are side-effectful.
Example: `daemon(action="reclaim", input={}, reasoning="cancel all confirmed manager-owned work")`.

Every terminal outcome is published on the per-run daemon channel. Do not poll
to remain active: after notification call `check`, then open its durable result
or error path. `checkpoint` is live-only, bounded, cooperative, and nonterminal;
when present, call `finish` exactly once and use `done` only after validating the
requested result. A missing-finish failure is a completion-contract failure, not necessarily
a task failure: inspect the run's trace/result and the run directory's physical
`result.txt` even if `result_path` is null. Do not
background required validation and end expecting re-entry. Native LingTai `ask`
reaches live runs only; terminal resume is a separate supported-CLI capability.

The optional root `summarize` boolean replaces the former flat `summary` field
and defaults to `true` on migrated roots. Specify what to retain in `reasoning`;
proactively set `summarize=false` for verbatim manuals and mandatory gates,
exact names/paths, field presence, source, diffs, or evidence. Summaries are
lossy and noncanonical; raw history and recovery locators remain preserved.
LingTai workers receive `compact`: `compact(action="manual")` is read-only and
`compact(action="run", _reason="...")` is a repeatable sole-call, nonterminal
context reset. Do not use the unavailable parent `system.summarize`.

## Settings inventory (SHOW only)

Call `daemon(action="settings", input={}, reasoning="inspect daemon settings")`.
Rows contain exactly `key`, `current`, `default`, `configurable`, and `comment`;
`SETTINGS_UNAVAILABLE` fails the whole action. `configurable: true` points to
an owner procedure and grants no mutation authority. SHOW never mutates settings. After an authorized source change, reconstruct the
owning manager at an approved safe boundary and verify SHOW again; editing a file
or environment does not update an existing manager. Never interrupt held work.

### Max turns

Anchor: `daemon-manual#max-turns`. Positive `LINGTAI_DAEMON_MAX_TURNS` overrides
explicit capability/setup `max_turns`, then `daemon/daemon.json`, then 5000.
Invalid file/env values fall back. `emanate.max_turns` narrows one batch only;
budget for exploration, action, and verification, not just the final edit.

### Manager pool size

Anchor: `daemon-manual#manager-pool-size`. Nonnegative
`LINGTAI_DAEMON_MANAGER_POOL_SIZE` overrides capability/setup `manager_pool_size`,
then `daemon/daemon.json`, then 100. Zero bypasses the central manager and uses per-run supervisors. Invalid file/env values fall back; this is not a per-run option.

### System prompt budget chars

Anchor: `daemon-manual#system-prompt-budget-chars`. Positive
`LINGTAI_DAEMON_SYSTEM_PROMPT_BUDGET_CHARS` overrides valid capability/setup
`system_prompt_budget_chars`, then `daemon/daemon.json`, then 20000. Invalid
values retain the previous valid layer. Oversized final prompts fail rather than
silently truncating the task; this character limit is not a context window.

### Timeout

Anchor: `daemon-manual#timeout`. Manager watchdog seconds come only from
capability/setup `timeout` (default 3600.0), not the daemon JSON or an environment
key. `emanate.timeout` overrides one batch, not this manager setting.

## Routing table

| Concrete need | Read one direct owner |
|---|---|
| backend choice, aliases, CLI options, native MCP, async ask | `reference/cli-backends/SKILL.md` |
| native LingTai preset, skills, MCP, context, completion | `reference/cli-backends/reference/backends/lingtai/SKILL.md` |
| stalled run, 143/SIGTERM, result/transcript/token evidence | `reference/forensics/SKILL.md` |
| ledger warnings or identity mismatch | `reference/dispatch-ledger/SKILL.md` |
| reclaim footprint, consent, deletion boundary | `reference/cleanup/SKILL.md` |
| Shell async events | `shell-manual` |
| programmatic/CI driver | `lingtai-agent daemon --help` |

## Standalone boundary

`DaemonService(state_root)` is not an Agent and owns no Agent lease, prompt,
identity, lifecycle, or manager-control authority. Native standalone LingTai
tasks require a directly loadable model preset. Standalone native API/CLI
omitted `tools` selects `shell` and `web` (DDG public search and static HTTP
browse, no provider credentials); explicit `tools=[]` grants no host tools,
and explicit lists stay narrow. A model-only preset needs no Shell/Web block.
Daemon completion/checkpoint/compact remain protocol tools, not host authority.
External CLIs retain their existing no-preset mechanics. Daemon capability does not authorize software installation, credential changes,
evidence deletion, or other work beyond the approved scope. `reclaim` stops work but does not clean
folders; molt clears conversation context and does not wipe durable stores or
daemon run folders.

## Native tool decision phase

Default **off**, independently of the parent Agent's review setting. Authorized
hosts may use `Agent(capabilities={"daemon": {"tool_decision_enabled": True}}, ...)`
or `DaemonService(state_root, tool_decision_enabled=True)` for standalone native
workers. Omit/false disables it. This is a host setup option, not an `emanate`
argument, CLI flag, environment setting or model-selectable permission bypass.
Use the existing boot/refresh procedure only with authorization; this document
is not activation authority. Detached native runs carry the typed boolean in
the existing supervisor manifest. No Shell/Web defaults or scheduling change.

Every completed native main API response is reviewed before any returned call
can execute. All siblings are reviewed first; refused calls stay in assistant
history and receive paired error results. The review uses that worker's actual
effective preset/service/model/endpoint, not the parent history or another
provider. Only fixed policy plus this call name/raw arguments is disclosed,
with no tools, schemas or conversation. Only `pass` gates execution; dangerous
and confidence are not additional thresholds. Review errors deny without a main
API retry. Existing deterministic guards still apply after receipt consumption.
This classification cannot establish human authority or guarantee a sandbox.

For accounting, inspect ordinary daemon dual-ledger rows (still
`source="daemon"`) and `tool_decision_usage` / `tool_decision` events in the run
log. Invalid/refused normalized responses count; thrown calls with no returned
usage remain unknown. Parent main-session counters do not include review calls.
External CLI internal tools outside the kernel are **not covered**.
[Strict output, supported transports and Core SDK injection](../../../intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md#opt-in-tool-decision-prototype).
