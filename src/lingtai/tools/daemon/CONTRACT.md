---
name: daemon-contract
tool: daemon
description: >
  Unified daemon contract for the public tool surface, backend architecture
  capability invariants, selected-skills context, one-run MCP propagation,
  daemon_common completion signaling, support-status honesty, run artifacts,
  terminal notifications, and compaction boundaries.
status: active
contract_version: 5
last_changed_at: "2026-07-16"
related_files:
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/daemon/system_prompt.py
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/llm/interface_converters.py
  - src/lingtai/tools/daemon/process_port.py
  - src/lingtai/tools/daemon/interactive_terminal/__init__.py
  - src/lingtai/tools/daemon/interactive_terminal/CONTRACT.md
  - src/lingtai/tools/daemon/interactive_terminal/ANATOMY.md
  - src/lingtai/adapters/posix/interactive_terminal.py
  - src/lingtai/tools/daemon/posix_process.py
  - src/lingtai/tools/daemon/run_dir.py
  - src/lingtai/tools/daemon/manual/SKILL.md
  - src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
  - src/lingtai/mcp_servers/daemon_common/server.py
  - src/lingtai/llm/openai/ANATOMY.md
  - src/lingtai/llm/mimo/ANATOMY.md
  - tests/test_daemon_contract_doc.py
  - tests/test_daemon.py
  - tests/test_daemon_backend_options.py
  - tests/test_daemon_claude_p_background_guard.py
  - tests/test_daemon_opencode_backend.py
  - tests/test_daemon_cursor_backend.py
  - tests/test_daemon_claude_interactive_backend.py
  - tests/test_daemon_run_dir.py
  - tests/test_daemon_codex_usage.py
  - tests/test_codex_standalone_compaction.py
review_triggers:
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/daemon/system_prompt.py
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/llm/interface_converters.py
  - src/lingtai/tools/daemon/run_dir.py
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/manual/
  - src/lingtai/mcp_servers/daemon_common/
  - tests/test_daemon.py
  - tests/test_daemon_backend_options.py
  - tests/test_daemon_claude_p_background_guard.py
  - tests/test_daemon_opencode_backend.py
  - tests/test_daemon_cursor_backend.py
  - tests/test_daemon_claude_interactive_backend.py
  - tests/test_daemon_run_dir.py
  - tests/test_daemon_codex_usage.py
  - tests/test_codex_standalone_compaction.py
maintenance: |
  Keep this unified daemon Contract in the same maintenance graph as the daemon
  ANATOMY.md and manual files listed under related_files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Daemon Contract

`daemon` dispatches and manages ephemeral subagents (emanations 分身之念).
This file is the single authoritative contract for both the public tool
surface and the daemon architecture capability invariant formerly documented
as the separate Daemon Architecture Capability Contract. The implementation
lives in `src/lingtai/tools/daemon/`; code remains the source of truth.

> **Maintenance trigger:** any change to a path listed in `review_triggers`
> must re-check this contract in the same change. The PR should either update
> this document or say why the daemon contract still holds.

## Routing Card

**Use this when:**

- You are editing the daemon tool schema, `handle` action dispatch, per-action
  success/error shapes, backend selection, run-dir storage, or tool-surface
  behavior an agent sees.
- You are changing backend architecture, selected-skills disclosure,
  parent-provided MCP propagation, daemon_common completion, support status,
  terminal notifications, daemon compact behavior, or run artifacts.

**Do not use this for:**

- Code navigation only: read `src/lingtai/tools/daemon/ANATOMY.md`.
- Independent peer agents that outlive the parent: use `avatar` (see
  `src/lingtai/tools/avatar/CONTRACT.md`). An emanation's lifecycle is bounded
  by the parent; an avatar's is not.

**Fast paths:** action schema -> §Tool Surface; backend names -> §Scope;
backend capability guarantees -> §Capability Invariants; support table ->
§Backend Support Matrix; run-dir layout -> §State & Storage; process and PTY
ownership -> §Process and Terminal Boundaries.

## Scope

- Canonical tool name: `daemon`.
- The parent `daemon` tool exposes five actions: `emanate`, `list`, `ask`, `check`,
  `reclaim`; `action` is required. Every LingTai emanation additionally receives
  the intrinsic `compact` tool, whose required `action` is explicit `run`
  (non-terminal reset) or `manual` (read-only procedures); omission is refused.
- Backends (`backend`, default `lingtai`): schema enum is `lingtai`, `claude-p`,
  `claude-code`, `codex`, `opencode`, `mimocode`, `mimo`, `qwen-code`, `qwen`,
  `oh-my-pi`, `omp`, `kimicode`, `kimi`, `cursor`. Aliases collapse via
  `_normalize_backend`: `mimo→mimocode`, `qwen→qwen-code`, `omp→oh-my-pi`,
  `kimi→kimicode`; `claude-code` is a compatibility alias for `claude-p`.
  `claude` / `claude-interactive` are hidden (not schema-advertised). Some CLI
  backends do not support `ask` yet (e.g. qwen-code, kimicode) and return an
  explicit unsupported message.
- The architecture capability invariant applies to every daemon backend and
  backend family LingTai exposes. It is not primarily a per-task input contract:
  the durable requirement is that any daemon architecture preserves the same
  selected-skill discovery semantics, one-run MCP registration semantics,
  completion signaling, backend support honesty, and reviewable artifact
  boundary.

Non-scope: claiming new backend MCP support before implementation, changing
third-party MCP protocols, or broad daemon scheduling/timeout behavior except
where those changes affect the invariants here.

## Tool Surface

Schema `required: ["action"]`. Relevant properties: `tasks[]` (each requires
`task` + `tools`; optional `skills`, `mcp`, `preset`, `backend_options`,
`prompt`, `context_token_limit`), `id`, `message`, `last`, `truncate`,
`contains`, `status`, `include_done`, `max_turns`, `timeout`, `backend`,
`summary`.

`tasks[].task` is required and is the complete parent-controlled daemon system
instruction for `backend="lingtai"`: objective, role, constraints, tool policy,
collaboration boundaries, and safety posture all go there. Optional
`tasks[].prompt` is LingTai's first ordinary user message. Missing, empty, or
whitespace-only `prompt` defaults exactly to `Begin the assigned daemon task.`;
any nonblank string is sent and stored byte-for-byte, including leading and
trailing whitespace. `prompt` is not appended to the system task, and the task
is not duplicated into user[0]. `system_prompt` is removed with no alias:
callers must put the complete system instruction in `task`, and preflight
rejects the obsolete field before run-dir creation or scheduling. External CLI
backend tasks reject `prompt` before run-dir creation; CLI behavior remains
task-as-CLI-prompt.

Per-task `context_token_limit` (positive integer; bool rejected) is a
context-token compaction threshold — rendered/provider-context tokens, never
cumulative spend — effective only for `backend="lingtai"` tasks whose resolved
provider is Codex (`codex`/`codex-pool`) or the native `mimo` LLM provider
(`manifest.llm.provider="mimo"` — distinct from the `backend` enum's
`mimo`/`mimocode` alias above, which drives the external `mimo` CLI as a
subprocess and never consults this field); every other provider and every
external CLI backend ignores it. Omitted, it inherits the parent service's
resolved context window as the threshold; an explicit value wins. Native
`mimo` defaults to the stateless OpenAI Responses wire (full-history replay;
never `store`/`previous_response_id`/`conversation`/generic
`context_management`) — an explicit `wire_api="chat_completions"` on the
preset selects the Chat Completions escape hatch instead. **Failure policy
differs by provider:** a standalone-compaction failure is non-fatal for Codex
(that turn's compaction is skipped; the loop continues on full history) but a
HARD failure for native `mimo` (propagates to the caller; never silently
continues on full history and never falls back to a different wire).

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `emanate` | `tasks[]` (each `task`+`tools`) | `backend`, `max_turns`, `timeout`, per-task `prompt` (LingTai only), `skills`/`mcp`/`preset`/`backend_options`/`context_token_limit` | `{status: "dispatched", count, ids: [...], group_id, handoff}`; `handoff` tells the model it may go idle or call `system(action='sleep')` while waiting for the terminal notification, and conditionally says that if Telegram is connected and a Task Card is available for the current turn, the model should use it to report progress via `telegram(action='manual')` and that manual's `Programmable Task Card` section; read `daemon-manual` and `notification-manual` for details | `{status: "error", message}` — obsolete `system_prompt` migration, CLI `prompt`, bad limits, or tool-surface/preset failure |
| `list` | — | `contains`, `status`, `include_done` (default true), `last` | `{...}` list blob of matching emanations (running + persisted history) | `{status: "error", message}` |
| `ask` | `id`, `message` | — | `{status: "sent", id, output}` (CLI ask returns immediately; `{status: "sent", id, async: true, ...}`) | `{status: "error", id, message}` — unknown/absent id, backend `ask` unsupported, or busy |
| `check` | `id` | `last` (default 20), `truncate` (default 500) | `{id, run_id, state, backend, path, turn, current_tool, elapsed_s, finished_at, tokens, result_preview, result_path, last_output, error, events: [...]}` | `{status: "error", message}` — unknown id, no run_dir, invalid `last`/`truncate`, or read failure |
| `reclaim` | — | — | `{status: "reclaimed", cancelled: <n>}` (or `{status: "shutdown", ...}` on lifecycle shutdown) | — |

`emanate` returns immediately after dispatch; terminal state (`done` /
`timeout` / `cancelled` / `failed`) reaches the parent via a `source="daemon"`
system notification per emanation. `check` classifies terminal state from the
recorded run-dir snapshot first (see `_classify_terminal_state`).

Agents following an `emanate` success `handoff` MUST treat Task Card guidance as
conditional: use the Task Card only when Telegram is connected and a Task Card
is available for the current turn, and read `telegram(action='manual')` for the
`Programmable Task Card` details. Daemon does not create or require a watcher
and does not import or call Telegram/Task Card runtime code.

## Capability Invariants

### 1. Selected skills are progressive-disclosure catalog entries

Every daemon backend must preserve selected `skills` as discoverable workflow
context, not as copied skill bodies. The runtime resolves each supplied skill
directory or direct `SKILL.md` path, parses frontmatter, and renders only
`name`, `location`, and `description` into the daemon context. The model reads
the referenced `SKILL.md` only when relevant. A backend must not paste full
SKILL bodies into the prompt or hide the path needed for progressive
disclosure. The selected skills catalog/path contract is part of the final
prompt/context for every supported daemon architecture.

### 2. Parent-provided MCP registrations have two lanes

The prompt lane is universal: parent-provided MCP registrations are normalized
as one-run registration objects, rendered as a prompt-visible catalog, and
redacted for `env` and `headers` values while preserving names, transports,
keys, and non-secret shape.

The native lane is backend-specific: a backend may mount those MCP
registrations as actual tools only when its daemon runner has a verified
run-scoped native MCP config or client path. The LingTai backend starts
task-scoped MCP clients directly. CLI backends must not claim native MCP
availability from the prompt catalog alone.

### 3. daemon_common is the completion capability for MCP-capable backends

MCP-capable daemon backends receive the built-in `daemon_common` MCP before any
parent registrations. The oneshot context tells the model to call `finish`
exactly once with `done`, `failed`, or `incomplete`. The MCP server writes
`daemon_completion.json` with `status`, optional `summary`, optional `reason`
(required by validation when `status` is `failed` or `incomplete`), and optional
`artifacts`.

When `daemon_common` is loaded, a conversational final answer is not enough.
Success requires a validated `finish(status="done")`; missing completion,
invalid JSON, invalid status, run-id mismatch, `failed`, or `incomplete` must
prevent terminal `done`.

### 4. Artifacts separate review evidence from secret-bearing config

Run artifacts must make the daemon contract reviewable without leaking secrets.
`DaemonRunDir` owns the run folder and persistent artifact set: `daemon.json`,
`.prompt`, `.heartbeat`, `history/chat_history.jsonl`, `logs/events.jsonl`,
`logs/token_ledger.jsonl`, `result.txt`, and `artifacts.json`.
`daemon.json.call_parameters` and `.prompt` may contain task surface,
selected-skill catalog/path context, and redacted MCP registrations. Secret MCP
values belong only in native run-scoped launch plumbing where a backend needs
them to mount tools.

External CLI usage is a separate, UI-only lane. A source-reported Codex
`turn.completed` usage object is accepted only when its contract fields are
non-negative integer counts (`input_tokens`, `cached_input_tokens`, and
`output_tokens`); the persisted `cli_tokens.input` is the disjoint
`max(input_tokens - cached_input_tokens, 0)`, while `cached` and `output` are
preserved and `calls` increments once for the terminal event. The raw usage
object is retained in the append-only `cli_usage` event. Missing, malformed,
and all-zero usage is silent, duplicate terminal events do not add another
call, and neither ledger receives a row. No Codex thinking/reasoning field is
projected because this event contract does not prove one.

### 5. Unsupported support status is an explicit capability state

An unsupported backend or transport must stay honest: prompt-catalog-only is not
native tool availability, and unsupported native MCP paths must be omitted or
reported explicitly rather than malformed into a fake-success launch. HTTP MCP
registrations are accepted for the prompt catalog today, and native HTTP
mounting is claimed only for backends whose source-proven config schema
supports it. Other CLI backends keep HTTP prompt-only until a backend-specific
path is implemented and tested.

### 6. Terminal notifications use published receipts, not attempted claims

Every terminal daemon outcome (`done`, `failed`, `cancelled`, `timeout`) must
surface through `.notification/system.json` rather than ordinary parent request
text. The run directory may write a temporary
`daemon.json.terminal_notification_claim` before publication to suppress
concurrent callbacks, but `daemon.json.terminal_notified=true` is a receipt and
may be written only after `_publish_daemon_notification` succeeds or an
idempotent retry observes an already-published system event.

Failed enqueue must clear the pending claim and leave the terminal run
retryable. Startup reconciliation retries only new-schema terminal run dirs that
explicitly carry `terminal_notified=false`, including stale pending claims left
by a crash. Legacy records with `terminal_notified=true` or with the key absent
are treated conservatively as already handled, not retroactively replayed. The
system event idempotency key is stable per terminal run, so a crash after
publication but before receipt persistence does not create a duplicate event on
restart while the original event remains in the capped 20-event `system.json`
window. If that event is dismissed or evicted before recovery records the
receipt, startup may safely republish: the contract is at-least-once delivery
without false durable success.

### 7. LingTai task mapping and self-compact are separate from provider compaction

`task` is the complete parent-controlled daemon system instruction. Optional
`prompt` is only LingTai's first ordinary user message, defaulting exactly to
`Begin the assigned daemon task.` when omitted, empty, or whitespace-only; any
nonblank prompt is preserved byte-for-byte. It is never appended to the system
task or sent to external CLI backends. `system_prompt` is removed with no alias:
callers must migrate the complete instruction into `task`, and preflight
rejects the obsolete field before a run directory is created.

LingTai's final daemon system prompt is composed by the package-owned
`system_prompt.py` from one concise operating contract, the available host-tool
names, parent-provided one-run context, and the complete `task`. The provider's
tool schemas remain authoritative and full tool descriptions are not duplicated
inside the prompt. The operating contract requires progressive disclosure: read
the relevant manual before first using a tool or workflow that has one; use a
visible result tool's `summary=true` only for predictably bulky output whose
exact raw text is unnecessary; and use daemon `compact`, never the unavailable
parent `system.summarize`, for same-run context reset. The complete rendered
string MUST be no more than 20,000 Python string characters. If task or selected
skill/MCP context would exceed that budget, prompt construction fails before the
LLM is scheduled and MUST NOT silently truncate any parent constraint.

Every LingTai daemon receives `compact` automatically, independent of provider.
Its `action` is required and accepts only explicit `run` or `manual`. Execution
uses `compact(action="run", _reason="...")` as the sole assistant-batch tool call;
its canonical `_reason` must be a non-empty, complete self-contained handoff. The
sole compact call/result pair survives a same-run provider-context reset beside
the rebuilt system prompt; the result contains status, resume instruction, and
exact run/state/history/event paths. It is repeatable and non-terminal. The
explicit `manual` action returns read-only procedures, does not compact, and may
be used without `_reason`. External CLI backends never receive `compact`.

### 8. Daemon agent metadata and context warning

The LingTai daemon's final model-visible `ToolResultBlock` in each tool batch
carries the canonical `_meta.agent_meta` sidecar. Its `agent_state` contains only
that daemon's runtime identity/round counters, current-call and session token
counters, and provider-context token/window/ratio state. It deliberately omits
the parent agent's notification and communication state; only the latest
`agent_meta` snapshot is current and older snapshots are historical traces.
Once daemon context usage reaches or exceeds 90%, every subsequent daemon round
carries the exact sentence `context warning, consider compact! see compact.manual for procedures`
in `agent_state.context.warning`. Use `compact(action="manual")` for the
read-only procedures and explicit `compact(action="run", _reason="...")` as the
sole-call reset. The surviving successful reset result is stamped from the fresh
retained context, so the pre-reset warning is absent when usage falls below 90%.

### 9. Per-task `context_token_limit` is Codex/native-mimo-only and lingtai-backend-only

The daemon task object also carries an optional per-task `context_token_limit`
(positive integer; bool rejected) — a context-token compaction threshold, never
cumulative spend. This capability is narrowly scoped and does not join the
general skills/MCP/completion/backend-support invariants above:

- Effective ONLY for `backend="lingtai"` tasks whose resolved provider is Codex
  (`codex`/`codex-pool`) or the native `mimo` LLM provider, threaded through
  `_daemon_provider_defaults` as `codex_compact_token_limit` /
  `mimo_compact_token_limit` respectively. Every other provider and every
  external CLI backend never receives it.
- Omitted, the value inherits the parent service's resolved context window as
  the threshold; an explicit task value always wins.
- When the threshold is reached, the Codex or native-MiMo Responses session
  compacts prior context via that provider's standalone `POST /responses/compact`
  endpoint and continues the same tool loop; neither uses the generic OpenAI
  Responses `context_management` axis.
- **Failure policy differs by provider.** A standalone compact call/parse
  failure is non-fatal for Codex; for the native `mimo` provider the same class
  of failure is a hard failure.
- Trigger/boundary/invalidation mechanics are shared Responses adapter/session
  internals (`_StandaloneCompactionMixin`), not daemon-owned. This contract
  states only the daemon-task-object capability boundary.

## Backend Support Matrix

Current source-backed status:

| Backend / architecture | Selected skills catalog/path | Parent MCP native mounting | `daemon_common` native completion |
|---|---|---|---|
| `lingtai` | Yes, in the daemon prompt/context. | Yes, task-scoped stdio and HTTP MCP clients. | Yes, task-scoped MCP; `finish(done)` is enforced. |
| `claude-p` / `claude-code` | Yes. | Yes for stdio via per-run `--mcp-config`; HTTP omitted. | Yes, same per-run config. |
| `codex` | Yes. | Yes for stdio via `-c mcp_servers.*`; HTTP omitted. | Yes, same config override path. |
| `opencode` | Yes. | Yes for stdio via `OPENCODE_CONFIG_CONTENT`; HTTP omitted. | Yes, same per-process config content. |
| `qwen-code` / `qwen` | Yes. | Yes for stdio via per-run Qwen settings; HTTP omitted. | Yes, same settings file. |
| `mimocode` / `mimo` | Yes. | Not wired in this slice; prompt catalog only. | Not wired; do not claim MCP-capable completion. |
| `oh-my-pi` / `omp` | Yes. | Not verified; prompt catalog only. | Not wired; do not claim MCP-capable completion. |
| `kimicode` / `kimi` | Yes. | Yes for stdio and HTTP via run-private `$KIMI_CODE_HOME/mcp.json`. | Yes, same run-private config. |
| `cursor` | Yes. | Not verified; prompt catalog only. | Not wired; do not claim MCP-capable completion. |

The native stdio/helper set is source-owned by `_codex_mcp_argv`,
`_opencode_mcp_env`, `_write_qwen_mcp_settings`, `_write_kimicode_mcp_config`,
`_write_claude_mcp_config`, and `_cli_backend_loads_common_mcp`. If a backend is
not in that loaded set, this contract treats it as prompt-catalog-only until
code and tests prove otherwise.

## State & Storage

All paths are relative to the parent agent working directory (`<parent>/`):

```text
<parent>/daemons/<handle>-<YYYYMMDD-HHMMSS>-<hash6>/   # one dir per run (run_id)
  daemon.json                  # identity card + live status (state, turn, tokens, ...)
  .prompt                      # system prompt verbatim
  .heartbeat                   # mtime-touched on activity
  history/chat_history.jsonl   # session transcript
  logs/token_ledger.jsonl      # per-call tokens, daemon-scoped (source="daemon")
  logs/events.jsonl            # tool_call / tool_result / cli_output / cli_usage / daemon_*
  result.txt                   # full terminal result when available

<parent>/logs/token_ledger.jsonl   # ALSO receives each daemon token row, tagged
                                    # source="daemon" + em_id + run_id (dual-ledger)
```

Token accounting is dual-ledger: every daemon call appends to the daemon's own
`logs/token_ledger.jsonl` and to the parent's `logs/token_ledger.jsonl`, both
rows tagged `source="daemon"` so `sum_token_ledger(scope="main_agent")`
excludes daemon spend while `scope="all"` includes it. On daemon-manager startup,
stale `running`/`active` `daemon.json` records whose `parent_pid` is dead are
reaped to `failed`.

## Process and Terminal Boundaries

### External CLI process boundary

Codex, Cursor, the shared OpenCode/MiMo/Oh-My-Pi family, and the Qwen/Kimi raw
one-shot initial `emanate` runners route through the daemon-local process Port.
Qwen and Kimi remain Manager-owned text-capture backends and do not gain `ask`
support from this boundary. `DaemonProcessCommand` is an immutable
argv/cwd/environment value; policy receives only an opaque handle and a
`DaemonProcessExit` containing the raw return code and optional local
termination reason. `PosixDaemonProcessPort` owns POSIX session creation,
stdout iteration, stderr draining, bounded TERM-then-KILL escalation, group/all
ownership, and idempotent release.

Release is non-blocking: it unregisters only a terminal/reaped child. A live
child remains owned after failed quiescence so later group/all sweeps can retry;
release never performs an unbounded wait. A concurrently blocked waiter reads
the final first-writer-wins local termination cause, and group/all sweeps return
the number of targeted children for truthful lifecycle reporting.

### Interactive Claude transport status

The hidden interactive Claude compatibility route has a bounded POSIX-first
transport slice: `InteractiveTerminalPort` and `PosixInteractiveTerminalAdapter`
own only PTY allocation, 120x40 sizing, raw master byte I/O, child
session/process-group termination, reaping, and terminal resource release.
`DaemonManager` injects one adapter and sweeps its group/all ownership during
watchdog timeout, reclaim, and parent stop. The bridge retains all terminal and
result policy. This does not add ConPTY, a pipe-only Windows substitute, or a
public backend name; native Windows interactive support remains deferred until
a genuine ConPTY adapter and native acceptance lane exist.

### POSIX invariants

DOCUMENT ONLY — do not change these assumptions and do not propose Windows work.

- On POSIX, ordinary in-process `DaemonManager` composition keeps
  `start_new_session=True`: its Port owns the private child process group,
  tracked per batch by `group_id`, and stamps the first local reason before
  TERM/KILL so signal return codes are attributed. Detached execution is
  different: the execution child already owns the session/group, so its headless
  and interactive Ports use `start_new_session=False` and carry the explicit
  `INHERITED_SUPERVISOR_GROUP` termination scope. A detached Port
  `terminate`, `terminate_group`, or `terminate_all` signals/reaps only each
  exact `Popen` child; it never sends a group signal to the execution host or
  caller. Only supervisor exact-run reclaim may signal the inherited run PGID.
- The hidden interactive Claude backend uses a POSIX PTY. Native interactive
  support remains explicitly deferred until a ConPTY adapter exists and is
  accepted.
- The LingTai backend spawns no CLI process; its watchdog only flips
  cancel/timeout events for in-thread run loops.

## Execution Ownership: One Detached Supervisor per Run

Every backend is created under one detached supervisor process at emanation
birth. The parent `DaemonManager` validates the request, writes a secret-free
manifest, launches the POSIX entrypoint, and retains only a durable submit /
inspect / control facade. `execution_host.py` composes the existing
`DaemonManager` and `_BackendSpec` execution units inside the supervisor, so
all backend parsers, option/session behavior, native MCP setup, skills/preset
setup, and completion gates remain single-source production code. The
supervisor owns the exact child process group, deadline, run-owned diagnostics,
terminal state, result/artifact files, and one idempotent terminal notification.

Agent stop and `system.refresh` shut down only parent-local resources; they do
not inspect or terminate a detached supervisor or its backend child. Explicit
`daemon(action="reclaim")` is the only parent control that requests run
cancellation. `daemon(action="ask")` uses the run-local control spool and is
accepted only while durable state is running. The ownership transition is
unconditional; `LINGTAI_DAEMON_DETACHED_SUPERVISOR` is not a production gate.

## Acceptance Gate

Any new daemon backend, backend-family reuse, or contract-impacting daemon
change must prove all applicable items:

1. Selected skills catalog/path context is visible in the final prompt/context
   without pasting SKILL.md bodies.
2. LingTai ToolResultBlocks carry daemon-local `_meta.agent_meta` runtime,
   token, and context state; parent notification/communication state is absent,
   the latest snapshot is current, and the exact warning is present on every
   round whose current context usage is >=90% and absent below that threshold.
3. `compact(action="manual")` is read-only; `action` is required, omission is
   refused without state change, and explicit `compact(action="run", _reason="...")`
   remains a repeatable non-terminal sole-call reset whose surviving result
   reflects the fresh retained context.
4. Parent MCP registrations appear in prompt context and durable call
   parameters with `env` and `headers` values redacted.
5. Native MCP config includes parent registrations only for transports and
   backends with a verified run-scoped loader; unsupported transports are
   omitted or reported honestly.
6. `daemon_common` is available for MCP-capable daemon backends, and terminal
   success is gated by valid `finish(status="done")`.
7. Unsupported backends remain documented as prompt-catalog-only or fail
   explicitly; they must not imply tool availability from prompt text alone.
8. `.prompt`, `daemon.json`, native config files/env/argv/settings,
   `result.txt`, `events.jsonl`, heartbeat, and artifact manifests remain
   inspectable within the daemon run boundary while secret-bearing native config
   is not copied into review artifacts.
9. Terminal notification tests prove failure retry, restart reconciliation,
   concurrent done-callback idempotency, crash-window idempotency, legacy
   `terminal_notified=true` and missing-key compatibility, and absence of a
   caller-facing notification toggle.

## Review Triggers

Re-check this contract when touching:

- `src/lingtai/tools/daemon/__init__.py` backend routing, selected-skill catalog
  assembly, MCP registration handling, native config writers, compact handling,
  or completion enforcement.
- `src/lingtai/tools/daemon/run_dir.py` artifact paths, `daemon.json`
  `call_parameters`, redaction-sensitive fields, terminal markers,
  terminal-notification receipt fields, or manifests.
- `src/lingtai/tools/daemon/manual/` daemon argument semantics, backend status,
  MCP capability guidance, compact guidance, or completion guidance.
- `src/lingtai/mcp_servers/daemon_common/` finish schema, payload file, or
  server behavior.
- `tests/test_daemon*.py` coverage that proves backend options, CLI native MCP,
  daemon_common completion, OpenCode-family routing, Qwen settings, Claude print
  MCP config, run-dir artifacts, prompt redaction, selected-skill catalog
  preservation, prompt mapping, or compact context reset behavior.
- `tests/test_codex_standalone_compaction.py` for per-task
  `context_token_limit` wiring/pre-flight validation.

## Anchored Claims

| Claim | Source | Test |
|---|---|---|
| `handle` dispatches the five actions; unknown actions error | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py` (dispatch), `tests/test_daemon_check.py::test_check_unknown_id_returns_error` |
| Default `max_emanations` is 100 and the override reaches the manager | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py::test_daemon_default_max_emanations_is_100`, `::test_daemon_max_emanations_override_reaches_manager` |
| Backend schema enum matches the ordered alias contract | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon_backend_options.py::test_backend_schema_enum_matches_ordered_contract`, `::test_backend_metadata_consistency_keeps_hidden_legacy_claude` |
| `check` returns state + events, honors `last`/`truncate`, validates inputs | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon_check.py` |
| CLI-backend `ask` returns immediately and enforces its own timeout | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py::test_ask_codex_returns_immediately_when_subprocess_hangs`, `::test_ask_codex_silent_subprocess_enforces_timeout` |
| Token rows are written to both the daemon and parent ledgers, tagged | `src/lingtai/tools/daemon/run_dir.py` | `tests/test_daemon_run_dir.py::test_append_tokens_writes_daemon_ledger`, `::test_append_tokens_writes_parent_ledger_tagged` |
| `context_token_limit` is validated, reaches Codex and native `mimo`, and is inert for every other provider and every external CLI backend | `src/lingtai/tools/daemon/__init__.py` | `tests/test_codex_standalone_compaction.py`, `tests/test_mimo_responses_compaction.py` |
| LingTai daemon tool results carry daemon-local `_meta.agent_meta`, omit parent notifications/guidance, and carry the exact warning only while current usage is >=90% | `src/lingtai/tools/daemon/__init__.py`, `src/lingtai/kernel/meta_block.py` | `tests/test_daemon.py::test_daemon_agent_meta_is_local_and_warning_tracks_current_usage` |
| `compact.action` is required; `manual` is read-only, omission is refused, and explicit `run` resets with fresh post-compact metadata | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py::test_compact_schema_requires_explicit_run_or_manual_action`, `::test_compact_missing_action_is_refused_without_reset`, `::test_compact_success_prunes_to_system_call_and_result` |
| `reclaim` cancels running emanations; agent stop shuts the daemon down first | `src/lingtai/tools/daemon/__init__.py` | `tests/test_lifecycle_daemon_shutdown.py::test_agent_stop_shuts_down_daemon_before_heartbeat_and_lock` |

## Verification Matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Action dispatch + per-action shapes are stable | `tests/test_daemon.py`, `tests/test_daemon_check.py` | `emanate` a trivial task, then `check` its id | Agents cannot dispatch or inspect subagents |
| Backend enum/alias contract stays consistent | `tests/test_daemon_backend_options.py::test_backend_schema_enum_matches_ordered_contract` | Pass an alias (`mimo`) and confirm it normalizes | Backend selection drifts from advertised names |
| Terminal state is classified from the recorded snapshot | `tests/test_daemon_check.py::test_check_includes_terminal_event_for_done_emanation` | Run to completion, confirm `state=done` in `check` | Parent mis-reads timeout/cancel as success |
| CLI `ask` never blocks the caller's tool thread | `tests/test_daemon.py::test_ask_codex_returns_immediately_when_subprocess_hangs` | `ask` a hung CLI daemon, confirm immediate return | Parent loop stalls on a hung subprocess |
| Reclaim kills the right process group / batch | `tests/test_daemon_cli_watchdog_scope.py`, `tests/test_lifecycle_daemon_shutdown.py` | Emanate two batches, reclaim, confirm scoped kill | A batch kills an unrelated newer batch's procs |
| Dual-ledger token accounting stays correct | `tests/test_daemon_run_dir.py::test_append_tokens_writes_parent_ledger_tagged` | Inspect both token_ledger.jsonl files after a run | Daemon spend double-counted or lost in totals |
| `context_token_limit` stays Codex/native-mimo-only and inert everywhere else; native `mimo` compaction failure is a HARD failure | `tests/test_codex_standalone_compaction.py`, `tests/test_mimo_responses_compaction.py` | Emanate a `backend='lingtai'` Codex task with an explicit `context_token_limit`, then repeat with native `mimo` | A bad value silently breaks unrelated providers/backends or swallows a hard MiMo failure |

Run before merging daemon tool-surface changes:

```bash
python -m pytest tests/test_daemon.py tests/test_daemon_check.py tests/test_daemon_backend_options.py tests/test_daemon_run_dir.py tests/test_lifecycle_daemon_shutdown.py tests/test_codex_standalone_compaction.py tests/test_mimo_responses_compaction.py -q
```

## Schema and Glossary Ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
