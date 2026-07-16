---
name: daemon-architecture-capability-contract
description: >
  Architecture capability contract for daemon backends: selected skills
  progressive-disclosure context, one-run MCP registration propagation,
  daemon_common completion signaling, support-status honesty, and redacted run
  artifacts across LingTai and CLI daemon architectures.
status: active
contract_version: 3
last_changed_at: "2026-07-10"
related_files:
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/__init__.py
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
  - tests/test_daemon_contract_doc.py
  - tests/test_daemon.py
  - tests/test_daemon_backend_options.py
  - tests/test_daemon_claude_p_background_guard.py
  - tests/test_daemon_opencode_backend.py
  - tests/test_daemon_cursor_backend.py
  - tests/test_daemon_claude_interactive_backend.py
  - tests/test_daemon_run_dir.py
  - tests/test_daemon_codex_usage.py
  - src/lingtai/llm/openai/ANATOMY.md
  - tests/test_codex_standalone_compaction.py
review_triggers:
  - src/lingtai/tools/daemon/__init__.py
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
  Keep this file in the same maintenance graph as the daemon ANATOMY.md and
  manual files listed under related_files. If any review_triggers path changes
  daemon backend routing, selected skills catalog/path semantics, MCP
  registration redaction or native mounting, daemon_common completion
  enforcement, backend support status, terminal notification receipts, or run
  artifact shape, re-read this architecture capability contract in the same
  change and either update it or explicitly state in the PR why the daemon
  capability contract still holds.
---
# Daemon Architecture Capability Contract

> **Maintenance trigger:** any change to a path listed in `review_triggers` must
> re-check this contract in the same change. The PR should either update this
> document or say why the daemon architecture capability contract still holds.

## What this is

This document is the architecture capability invariant for every daemon backend
and backend family LingTai exposes. It is not primarily a per-task input
contract. The durable requirement is that any daemon architecture preserves the
same selected-skill discovery semantics, one-run MCP registration semantics,
completion signaling, backend support honesty, and reviewable artifact boundary.

The public daemon task object is still the carrier for `skills`, `mcp`,
`system_prompt`, `tools`, `backend_options`, and backend routing
(`src/lingtai/tools/daemon/__init__.py:672-713`). This contract governs what
each backend must do with those capabilities after routing.

Scope:

- Selected `skills` progressive-disclosure catalog and path semantics.
- Parent-provided one-run MCP registrations: prompt catalog, redaction, and
  native mounting where the backend has a source-proven run-scoped loader.
- Built-in LingTai daemon MCP (`daemon_common`) availability for MCP-capable
  daemon backends and its `finish(status, summary?, reason?, artifacts?)`
  terminal signal.
- Backend support matrix and acceptance expectations when adding or changing a
  backend.
- Prompt/native-config/artifact redaction boundary sufficient for review without
  leaking secrets.
- Terminal notification reliability: every terminal daemon outcome remains
  retryable until a compact system notification is durably published.

Non-scope: claiming new backend MCP support before implementation, changing
third-party MCP protocols, or broad daemon scheduling/timeout behavior except
where those changes affect the capability invariants here.

## External CLI process boundary

The Stage 4 production slice routes Codex, Cursor, the shared OpenCode/MiMo/Oh-My-Pi
family, and the Qwen/Kimi raw one-shot initial `emanate` runners through the
daemon-local process Port. Qwen and Kimi remain Manager-owned text-capture
backends and do not gain `ask` support from this boundary.
`DaemonProcessCommand` is
an immutable argv/cwd/environment value; policy receives only an opaque handle
and a `DaemonProcessExit` containing the raw return code and optional local
termination reason. `PosixDaemonProcessPort` owns POSIX session creation,
stdout iteration (including ask deadlines), stderr draining, bounded
TERM-then-KILL escalation, group/all ownership, and idempotent release.

Release is non-blocking: it unregisters only a terminal/reaped child. A live
child remains owned after failed quiescence so later group/all sweeps can retry;
release never performs an unbounded wait. A concurrently blocked waiter reads
the final first-writer-wins local termination cause, and group/all sweeps return
the number of targeted children for truthful lifecycle reporting. If a terminal
owner releases one handle after a sweep snapshot, the adapter skips that stale
handle and still terminates every later live sibling in the snapshot.

The Port does not construct backend argv, parse Codex JSONL, write
`DaemonRunDir`, publish notifications, choose timeout versus cancellation, or
classify success/failure. Watchdog group timeout and lifecycle reclaim/agent
stop sweep Port-owned Codex, Cursor, OpenCode-family, Qwen, and Kimi processes as well
as the transitional legacy CLI registry used by other backends. Windows
adapters and interactive Claude PTY/ConPTY remain pending.

## Interactive Claude transport status

The hidden interactive Claude compatibility route now has a bounded POSIX-first
transport slice: `InteractiveTerminalPort` and
`PosixInteractiveTerminalAdapter` own only PTY allocation, 120x40 sizing, raw
master byte I/O, child session/process-group termination, reaping, and terminal
resource release. `DaemonManager` injects one adapter and sweeps its group/all
ownership during watchdog timeout, reclaim, and parent stop. The bridge retains
all terminal and result policy. This does not add ConPTY, a pipe-only Windows
substitute, or a public backend name; native Windows interactive support remains
deferred until a genuine ConPTY adapter and native acceptance lane exist.

## Capability Invariants

### 1. Selected skills are progressive-disclosure catalog entries

Every daemon backend must preserve selected `skills` as discoverable workflow
context, not as copied skill bodies. The runtime resolves each supplied skill
directory or direct `SKILL.md` path, parses frontmatter, and renders only
`name`, `location`, and `description` into the daemon context
(`src/lingtai/tools/daemon/__init__.py:1103-1162`). The model reads the referenced
`SKILL.md` only when relevant. A backend must not paste full SKILL bodies into
the prompt or hide the path needed for progressive disclosure.

### 2. Parent-provided MCP registrations have two lanes

The prompt lane is universal: parent-provided MCP registrations are normalized
as one-run registration objects, rendered as a prompt-visible catalog, and
redacted for `env` and `headers` values while preserving names, transports,
keys, and non-secret shape (`src/lingtai/tools/daemon/__init__.py:1165-1256`).

The native lane is backend-specific: a backend may mount those MCP registrations
as actual tools only when its daemon runner has a verified run-scoped native MCP
config or client path. The LingTai backend starts task-scoped MCP clients directly
(`src/lingtai/tools/daemon/__init__.py:1419-1479`). CLI backends must not claim
native MCP availability from the prompt catalog alone.

### 3. daemon_common is the completion capability for MCP-capable backends

MCP-capable daemon backends receive the built-in `daemon_common` MCP before any
parent registrations (`src/lingtai/tools/daemon/__init__.py:1259-1284`). The
oneshot context tells the model to call `finish` exactly once with `done`,
`failed`, or `incomplete` (`src/lingtai/tools/daemon/__init__.py:1287-1305`).
The MCP server writes `daemon_completion.json` with
`status`, optional `summary`, optional `reason` (required by validation when
`status` is `failed` or `incomplete`), and optional `artifacts`
(`src/lingtai/mcp_servers/daemon_common/server.py:20-139`).

When `daemon_common` is loaded, a conversational final answer is not enough.
Success requires a validated `finish(status="done")`; missing completion,
invalid JSON, invalid status, run-id mismatch, `failed`, or `incomplete` must
prevent terminal `done` (`src/lingtai/tools/daemon/__init__.py:1329-1415`).

### 4. Artifacts separate review evidence from secret-bearing config

Run artifacts must make the daemon contract reviewable without leaking secrets.
`DaemonRunDir` owns the run folder and persistent artifact set
(`src/lingtai/tools/daemon/run_dir.py`): its constructor creates the run
folder/history/log directories and state object (`src/lingtai/tools/daemon/run_dir.py:41-126`),
while methods in the same module persist `daemon.json`, `.prompt`, `.heartbeat`,
`history/chat_history.jsonl`, `logs/events.jsonl`, `logs/token_ledger.jsonl`,
`result.txt`, and `artifacts.json`.
`daemon.json.call_parameters` and `.prompt` may contain task surface,
selected-skill catalog/path context, and redacted MCP registrations. Secret
MCP values belong only in native run-scoped launch plumbing where a backend
needs them to mount tools (`src/lingtai/tools/daemon/__init__.py:3080-3264`).

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
mounting is claimed only for backends whose source-proven config schema supports
it. Other CLI backends keep HTTP prompt-only until a backend-specific path is
implemented and tested.

### 6. Terminal notifications use published receipts, not attempted claims

Every terminal daemon outcome (`done`, `failed`, `cancelled`, `timeout`) must
surface through `.notification/system.json` rather than ordinary parent request
text. The run directory may write a temporary
`daemon.json.terminal_notification_claim` before publication to suppress
concurrent callbacks, but `daemon.json.terminal_notified=true` is a receipt and
may be written only after `_publish_daemon_notification` succeeds or an
idempotent retry observes an already-published system event
(`src/lingtai/tools/daemon/__init__.py:2649-2730`,
`src/lingtai/tools/daemon/__init__.py:5942-5957`,
`src/lingtai/tools/daemon/run_dir.py:887-962`).

Failed enqueue must clear the pending claim and leave the terminal run
retryable. Startup reconciliation retries only new-schema terminal run dirs that
explicitly carry `terminal_notified=false`, including stale pending claims left
by a crash. Legacy records with `terminal_notified=true` or with the key absent
are treated conservatively as already handled, not retroactively replayed
(`src/lingtai/tools/daemon/__init__.py:985-1021`). The system event idempotency key is
stable per terminal run (`daemon-terminal:<run_id>`), so a crash after
publication but before receipt persistence does not create a duplicate event on
restart while the original event remains in the capped 20-event `system.json`
window. If that event is dismissed or evicted before recovery records the
receipt, startup may safely republish: the contract is at-least-once delivery
without false durable success.

### 7. Per-task `context_token_limit` is a Codex/native-mimo-only, lingtai-backend-only capability

The daemon task object also carries an optional per-task `context_token_limit`
(positive integer; bool rejected) — a context-token compaction threshold,
never cumulative spend (`src/lingtai/tools/daemon/__init__.py`, schema
property and `_handle_emanate` pre-flight gate). This capability is narrowly
scoped and does not join the general skills/MCP/completion/backend-support
invariants above:

- Effective ONLY for `backend="lingtai"` tasks whose resolved provider is
  Codex (`codex`/`codex-pool`) or the native `mimo` LLM provider, threaded
  through `_daemon_provider_defaults` as `codex_compact_token_limit` /
  `mimo_compact_token_limit` respectively. Every other provider and every
  external CLI backend (including the `codex` CLI backend and the `mimocode`
  CLI backend — an entirely separate thing from the native `mimo` LLM
  provider, see the Backend Support Matrix below) never receives it and is
  behaviorally unchanged — a CLI-backend task carrying the field never even
  reaches the LingTai-backend pre-flight gate (the CLI early-return in
  `_handle_emanate` precedes it).
- Omitted, the value inherits the parent service's resolved context window as
  the threshold; an explicit task value always wins.
- When the threshold is reached (a PROJECTED provider-visible token count,
  not a raw reactive-only check — see below), the Codex or native-MiMo
  Responses session compacts prior context via that provider's standalone
  `POST /responses/compact` endpoint and continues the SAME tool loop; neither
  ever uses the generic OpenAI Responses `context_management` axis (Codex's
  backend rejects it; MiMo's documented Responses API marks it explicitly
  incompatible).
- **Failure policy differs by provider.** A standalone compact call/parse
  failure is NON-FATAL for Codex — that turn's compaction is skipped and the
  loop continues on full (uncompacted) history, since compaction there is an
  optimization, not a correctness requirement. For the native `mimo` provider
  the SAME class of failure is a HARD failure: it propagates to the caller
  instead of being swallowed, instead of silently continuing on the original
  full history, and instead of silently falling back to the Chat Completions
  wire. This is because MiMo's Responses API gives LingTai no generic
  `context_management` fallback and no server-side state to lean on (`store`,
  `previous_response_id`, and `conversation` are all documented-unsupported),
  so a MiMo session that silently kept replaying ever-growing full history
  past its configured threshold would have no safety net.
- The full trigger/boundary/invalidation mechanics (compaction boundary
  selection via `ChatInterface.find_compaction_boundary` so the live turn
  that triggers compaction always rides as a verbatim trailing item rather
  than being folded into the opaque summary, projected-token calibration,
  and invalidation on history rewrite) are shared Responses adapter/session
  internals (`_StandaloneCompactionMixin`), not daemon-owned — see
  `src/lingtai/llm/openai/ANATOMY.md` ("Standalone Codex compaction") and
  `src/lingtai/llm/mimo/ANATOMY.md` for that mechanism and the MiMo failure-
  policy divergence. This contract states only the daemon-task-object
  capability boundary above; it does not restate adapter internals.
- A LingTai daemon task may explicitly request the no-argument `compact` tool
  in its `tools` list. The tool is exposed only when the resolved provider is
  native Codex (`codex`/`codex-pool`) or native MiMo; it invokes the same live
  session's standalone compaction immediately, without restarting the daemon
  or mutating parent state. It returns `success` when a new replay basis was
  installed and `unsupported` when no safe/new turn boundary exists. Codex
  provider failures return `failure` (matching its non-fatal automatic policy);
  native MiMo preserves its hard-failure behavior. Other providers and all
  external CLI backends do not receive this tool.

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
`_write_claude_mcp_config`, and `_cli_backend_loads_common_mcp`
(`src/lingtai/tools/daemon/__init__.py:173-260`,
`src/lingtai/tools/daemon/__init__.py:1307-1327`,
`src/lingtai/tools/daemon/__init__.py:3222-3246`). If a backend is not in that
loaded set, this contract treats it as prompt-catalog-only until code and tests
prove otherwise.

### Execution ownership: one detached supervisor per run

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
2. Parent MCP registrations appear in prompt context and durable call
   parameters with `env` and `headers` values redacted.
3. Native MCP config includes parent registrations only for transports and
   backends with a verified run-scoped loader; unsupported transports are
   omitted or reported honestly.
4. `daemon_common` is available for MCP-capable daemon backends, and terminal
   success is gated by valid `finish(status="done")`.
5. Unsupported backends remain documented as prompt-catalog-only or fail
   explicitly; they must not imply tool availability from prompt text alone.
6. `.prompt`, `daemon.json`, native config files/env/argv/settings,
   `result.txt`, `events.jsonl`, heartbeat, and artifact manifests remain
   inspectable within the daemon run boundary while secret-bearing native config
   is not copied into review artifacts.
7. Terminal notification tests prove failure retry, restart reconciliation,
   concurrent done-callback idempotency, crash-window idempotency, legacy
   `terminal_notified=true` and missing-key compatibility, and absence of a
   caller-facing notification toggle.

## Review Triggers

Re-check this contract when touching:

- `src/lingtai/tools/daemon/__init__.py` backend routing, selected-skill catalog
  assembly, MCP registration handling, native config writers, or completion
  enforcement.
- `src/lingtai/tools/daemon/run_dir.py` artifact paths, `daemon.json`
  `call_parameters`, redaction-sensitive fields, terminal markers,
  terminal-notification receipt fields, or manifests.
- `src/lingtai/tools/daemon/manual/` daemon argument semantics, backend status,
  MCP capability guidance, or completion guidance.
- `src/lingtai/mcp_servers/daemon_common/` finish schema, payload file, or
  server behavior.
- `tests/test_daemon*.py` coverage that proves backend options, CLI native MCP,
  daemon_common completion, OpenCode-family routing, Qwen settings, Claude print
  MCP config, run-dir artifacts, prompt redaction, or selected-skill catalog
  preservation. **Exception:** `tests/test_codex_standalone_compaction.py`
  does not match this glob but is the coverage for capability invariant 7
  (per-task `context_token_limit`) and its daemon-side wiring/pre-flight
  validation — re-check it alongside this contract for the same reason.
