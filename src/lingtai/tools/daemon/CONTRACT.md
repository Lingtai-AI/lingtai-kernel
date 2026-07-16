---
name: daemon-contract
tool: daemon
contract_version: 1
related_files:
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/daemon/process_port.py
  - src/lingtai/tools/daemon/interactive_terminal/__init__.py
  - src/lingtai/tools/daemon/interactive_terminal/CONTRACT.md
  - src/lingtai/tools/daemon/interactive_terminal/ANATOMY.md
  - src/lingtai/adapters/posix/interactive_terminal.py
  - src/lingtai/tools/daemon/posix_process.py
  - src/lingtai/tools/daemon/DAEMON_CONTRACT.md
  - src/lingtai/tools/daemon/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Daemon tool-surface contract

`daemon` dispatches and manages ephemeral subagents (emanations 分身之念). This
file documents the **tool surface** — the actions an agent invokes — under the
uniform tool-contract template. The deeper backend/architecture invariants live
in the sibling `src/lingtai/tools/daemon/DAEMON_CONTRACT.md` (contract_version 2); this
file does not restate them. The implementation lives in `src/lingtai/tools/daemon/`; the
code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the daemon tool schema, the `handle` action dispatch, or the
  per-action success/error shapes an agent sees.
- You are reviewing backend selection (aliases, enum) at the tool boundary or
  the storage layout a daemon run produces.

**Do not use this for:**
- Backend architecture, MCP completion signaling, selected-skills disclosure,
  or CLI-backend routing: read `src/lingtai/tools/daemon/DAEMON_CONTRACT.md`.
- Code navigation only: read `src/lingtai/tools/daemon/ANATOMY.md`.
- Independent peer agents that outlive the parent: use `avatar` (see
  `src/lingtai/tools/avatar/CONTRACT.md`). An emanation's lifecycle is bounded by the
  parent; an avatar's is not.

**Fast paths:** action schema -> §Tool surface; backend names -> §Scope;
run-dir layout -> §State & storage; process-group/PTY kill -> §Cross-platform
invariants.

## Scope

- Canonical tool name: `daemon`.
- One tool exposes five actions: `emanate`, `list`, `ask`, `check`, `reclaim`.
  `action` is required.
- Backends (`backend`, default `lingtai`): schema enum is `lingtai`, `claude-p`,
  `claude-code`, `codex`, `opencode`, `mimocode`, `mimo`, `qwen-code`, `qwen`,
  `oh-my-pi`, `omp`, `kimicode`, `kimi`, `cursor`. Aliases collapse via
  `_normalize_backend`: `mimo→mimocode`, `qwen→qwen-code`, `omp→oh-my-pi`,
  `kimi→kimicode`; `claude-code` is a compatibility alias for `claude-p`.
  `claude` / `claude-interactive` are hidden (not schema-advertised). Some CLI
  backends do not support `ask` yet (e.g. qwen-code, kimicode) and return an
  explicit unsupported message.

**Non-goals (at this layer):** this file does not specify how each backend runs
its process, materializes lingtai/tools/skills, or signals completion — that is
`DAEMON_CONTRACT.md`. It documents only what the agent passes in and gets back.

## Tool surface

Schema `required: ["action"]`. Relevant properties: `tasks[]` (each requires
`task` + `tools`; optional `skills`, `mcp`, `preset`, `backend_options`,
`system_prompt`, `context_token_limit`), `id`, `message`, `last`, `truncate`,
`contains`, `status`, `include_done`, `max_turns`, `timeout`, `backend`,
`summary`.

Per-task `context_token_limit` (positive integer; bool rejected) is a
context-token compaction threshold — rendered/provider-context tokens, never
cumulative spend — effective only for `backend="lingtai"` tasks whose
resolved provider is Codex (`codex`/`codex-pool`) or the native `mimo` LLM
provider (`manifest.llm.provider="mimo"` — distinct from the `backend` enum's
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
continues on full history and never falls back to a different wire). See
`src/lingtai/tools/daemon/DAEMON_CONTRACT.md` (capability invariant 7) for the
lingtai-backend-only capability boundary and `src/lingtai/llm/openai/ANATOMY.md`
("Standalone Codex compaction") / `src/lingtai/llm/mimo/ANATOMY.md` for the
Codex vs. native-MiMo Responses session mechanics and failure-policy
divergence this threshold triggers.

For a running LingTai daemon whose resolved provider is native Codex
(`codex`/`codex-pool`) or native MiMo, `tools: ["compact"]` additionally grants
a no-argument self-compact tool. It calls that daemon session's existing
standalone compaction seam immediately and reports `success` or
`unsupported`; Codex call failures report `failure`, while MiMo retains its
hard-failure behavior. The tool is absent for other providers and external
CLI backends.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `emanate` | `tasks[]` (each `task`+`tools`) | `backend`, `max_turns`, `timeout`, per-task `skills`/`mcp`/`preset`/`backend_options`/`system_prompt`/`context_token_limit` | `{status: "dispatched", count, ids: [...], group_id, handoff}`; `handoff` tells the model it may go idle or call `system(action='sleep')` while waiting for the terminal notification, and conditionally says that if Telegram is connected and a Task Card is available for the current turn, the model should use it to report progress via `telegram(action='manual')` and that manual's `Programmable Task Card` section; read `daemon-manual` and `notification-manual` for details | `{status: "error", message}` — `No tasks provided`, bad `max_turns`/`timeout`/`context_token_limit`, tool-surface/preset build failure |
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

## State & storage

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
`logs/token_ledger.jsonl` **and** to the parent's `logs/token_ledger.jsonl`,
both rows tagged `source="daemon"` so `sum_token_ledger(scope="main_agent")`
excludes daemon spend while `scope="all"` includes it. On daemon-manager startup,
stale `running`/`active` `daemon.json` records whose `parent_pid` is dead are
reaped to `failed`.

## Interactive terminal capability

The retained hidden `claude` / `claude-interactive` route uses the separate
capability-local `InteractiveTerminalPort` and injected persistent POSIX adapter.
It carries raw bidirectional bytes with explicit 120x40 dimensions and owns
PTY/session, process-group termination, reaping, and terminal-only release. The
Claude bridge never constructs a private adapter: runtime composition must inject
the Port, and missing injection fails before managed-workspace, harness, or spawn
work. Its final `release()` is non-killing, so a stubborn live handle remains
manager-owned for later group/all sweeps. The bridge continues to own probes,
hooks, prompt framing, auth/trust, transcript/result projection, and
notification/state policy. No interactive name is re-advertised, ordinary pipes
are not relabeled as terminals, and no Windows ConPTY adapter is claimed by this
slice. See the reciprocal `interactive_terminal/CONTRACT.md` and
`interactive_terminal/ANATOMY.md`.

## Cross-platform invariants

DOCUMENT ONLY — do not change these assumptions and do not propose Windows work.

- On POSIX, ordinary in-process `DaemonManager` composition keeps
  `start_new_session=True`: its Port owns the private child process group,
  tracked per batch by `group_id`, and stamps the first local reason before
  TERM/KILL so signal return codes are attributed. Detached execution is
  different: the execution child already owns the session/group, so its
  headless and interactive Ports use `start_new_session=False` and carry the
  explicit `INHERITED_SUPERVISOR_GROUP` termination scope. A detached Port
  `terminate`, `terminate_group`, or `terminate_all` signals/reaps only each
  exact `Popen` child; it never sends a group signal to the execution host or
  caller. Only supervisor exact-run reclaim may signal the inherited run PGID.
  The POSIX adapters may synchronously publish an immutable
  `DaemonProcessObservation` (PID, PGID, start identity, and termination scope)
  to the detached owner immediately after registration and before stream I/O;
  the owner records `cli_pid`/`child_pid`, inherited PGID, identity, and bounded
  history, and closes the cancel-before-registration race without exposing
  `Popen` to Core. If that observation/state-publication callback raises, the
  adapter first TERM/KILLs and reaps the exact new child under its explicit
  scope, removes its registry entry, closes any PTY master, and then re-raises;
  no inaccessible child or descriptor is left behind.
- The hidden interactive Claude backend uses a POSIX PTY. Native interactive
  support remains explicitly deferred until a ConPTY adapter exists and is
  accepted.
- The LingTai backend spawns no CLI process; its watchdog only flips
  cancel/timeout events for in-thread run loops.

These POSIX process-group, signal, and PTY assumptions are load-bearing for
correct cancellation and attribution. Deeper backend-launch details are in
`DAEMON_CONTRACT.md`.

The daemon-local process Port (`process_port.py`) is the mechanism boundary for
Codex, Claude print-mode initial plus ask/resume, the shared OpenCode/MiMo/Oh-My-Pi
family, and the Qwen/Kimi initial one-shot runners: commands are immutable direct argv values,
handles are opaque, and exits preserve the raw return code plus an optional
LingTai reason. The POSIX adapter owns spawn/session creation, stdout deadline
iteration, stderr draining, bounded TERM/KILL escalation, group ownership, and
reaping. Backend parsing, run-dir writes, completion sentinels, notifications,
and timeout-versus-cancel classification remain manager policy. Release is
non-blocking and removes only a terminal/reaped child; a live child remains
owned for later group/all shutdown retries. The first local termination cause
wins across concurrent wait/watchdog/lifecycle paths, and group/all sweeps
return targeted-process counts so shutdown telemetry covers both Port-owned and
transitional legacy children. A terminal handle released after a sweep snapshot
is skipped without aborting termination of later live siblings.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `handle` dispatches the five actions; unknown actions error | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py` (dispatch), `tests/test_daemon_check.py::test_check_unknown_id_returns_error` |
| Default `max_emanations` is 100 and the override reaches the manager | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py::test_daemon_default_max_emanations_is_100`, `::test_daemon_max_emanations_override_reaches_manager` |
| Backend schema enum matches the ordered alias contract | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon_backend_options.py::test_backend_schema_enum_matches_ordered_contract`, `::test_backend_metadata_consistency_keeps_hidden_legacy_claude` |
| `check` returns state + events, honors `last`/`truncate`, validates inputs | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon_check.py::test_check_running_emanation_returns_state_and_events`, `::test_check_respects_last_parameter`, `::test_check_truncate_limits_string_fields`, `::test_check_rejects_zero_or_negative_last` |
| `check` surfaces a terminal event for a done emanation and failure error | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon_check.py::test_check_includes_terminal_event_for_done_emanation`, `::test_check_includes_failure_error` |
| CLI-backend `ask` returns immediately and enforces its own timeout | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py::test_ask_codex_returns_immediately_when_subprocess_hangs`, `::test_ask_codex_silent_subprocess_enforces_timeout` |
| Token rows are written to both the daemon and parent ledgers, tagged | `src/lingtai/tools/daemon/run_dir.py` | `tests/test_daemon_run_dir.py::test_append_tokens_writes_daemon_ledger`, `::test_append_tokens_writes_parent_ledger_tagged` |
| `context_token_limit` is validated (positive int, bool rejected), reaches Codex and native `mimo` via `_daemon_provider_defaults` (`codex_compact_token_limit` / `mimo_compact_token_limit`), and is inert for every other provider and every external CLI backend | `src/lingtai/tools/daemon/__init__.py` | `tests/test_codex_standalone_compaction.py::test_daemon_schema_rejects_bool_context_token_limit`, `::test_daemon_schema_rejects_zero_context_token_limit`, `::test_daemon_provider_defaults_injects_codex_compact_token_limit`, `::test_external_cli_backend_ignores_context_token_limit`; `tests/test_mimo_responses_compaction.py::test_daemon_provider_defaults_injects_mimo_compact_token_limit`, `::test_unrelated_providers_never_receive_mimo_compact_token_limit` |
| `reclaim` cancels running emanations; agent stop shuts the daemon down first | `src/lingtai/tools/daemon/__init__.py` | `tests/test_lifecycle_daemon_shutdown.py::test_agent_stop_shuts_down_daemon_before_heartbeat_and_lock` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Action dispatch + per-action shapes are stable | `tests/test_daemon.py`, `tests/test_daemon_check.py` | `emanate` a trivial task, then `check` its id | Agents cannot dispatch or inspect subagents |
| Backend enum/alias contract stays consistent | `tests/test_daemon_backend_options.py::test_backend_schema_enum_matches_ordered_contract` | Pass an alias (`mimo`) and confirm it normalizes | Backend selection drifts from advertised names |
| Terminal state is classified from the recorded snapshot | `tests/test_daemon_check.py::test_check_includes_terminal_event_for_done_emanation` | Run to completion, confirm `state=done` in `check` | Parent mis-reads timeout/cancel as success |
| CLI `ask` never blocks the caller's tool thread | `tests/test_daemon.py::test_ask_codex_returns_immediately_when_subprocess_hangs` | `ask` a hung CLI daemon, confirm immediate return | Parent loop stalls on a hung subprocess |
| Reclaim kills the right process group / batch | `tests/test_daemon_cli_watchdog_scope.py`, `tests/test_lifecycle_daemon_shutdown.py` | Emanate two batches, reclaim, confirm scoped kill | A batch kills an unrelated newer batch's procs |
| Dual-ledger token accounting stays correct | `tests/test_daemon_run_dir.py::test_append_tokens_writes_parent_ledger_tagged` | Inspect both token_ledger.jsonl files after a run | Daemon spend double-counted or lost in totals |
| `context_token_limit` stays Codex/native-mimo-only and inert everywhere else; native `mimo` compaction failure is a HARD failure (unlike Codex's non-fatal skip) | `tests/test_codex_standalone_compaction.py`, `tests/test_mimo_responses_compaction.py` | Emanate a `backend='lingtai'` Codex task with an explicit `context_token_limit`, confirm compaction fires only past that threshold and a provider error is swallowed non-fatally; repeat with `manifest.llm.provider="mimo"` and confirm a provider error instead propagates to the caller | A bad value silently breaks unrelated providers/backends, Codex/MiMo requests start sending `context_management`, or a MiMo compaction failure is silently swallowed/falls back instead of failing loud |

Run before merging daemon tool-surface changes:

```bash
python -m pytest tests/test_daemon.py tests/test_daemon_check.py tests/test_daemon_backend_options.py tests/test_daemon_run_dir.py tests/test_lifecycle_daemon_shutdown.py tests/test_codex_standalone_compaction.py tests/test_mimo_responses_compaction.py -q
```

## Schema and glossary ownership

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
