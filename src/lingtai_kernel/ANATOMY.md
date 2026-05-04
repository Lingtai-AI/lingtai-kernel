# lingtai_kernel

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues (mail or `discussions/<name>-patch.md`); do not silently fix.

The minimal agent runtime: turn loop, lifecycle, signal consumption, tool dispatch, intrinsic wiring, mailbox glue, soul/molt orchestration. The kernel is standalone — the wrapper package `lingtai` (at `src/lingtai/`) depends on it strictly one-directionally.

> **What is an `ANATOMY.md`?** See the canonical convention at `src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md`. This file follows the same 6-section template as every other anatomy in the tree.

## Components

The kernel root holds the coordinator (`base_agent/`) plus a flat collection of supporting modules. Most are self-contained leaves; subfolders are concept-boundary units with their own anatomy.

- `base_agent/` — `BaseAgent`, the kernel coordinator (package of 7 modules). `__init__.py` defines `BaseAgent` (~550 lines: constructor, properties, state machine, hooks, cross-cutting stubs, pass-throughs to submodules). Submodules: `lifecycle.py` (start/stop/heartbeat/signals/refresh), `turn.py` (main loop/message dispatch/AED/response processing), `soul_flow.py` (soul timer/drain/consultation fire/persist), `tools.py` (tool schemas/dispatch/registry), `identity.py` (naming/manifest/status), `prompt.py` (system prompt building/flushing), `messaging.py` (mail/notifications/outbound). See `base_agent/ANATOMY.md`.
- `session.py` — `SessionManager`. LLM session lifecycle, token bookkeeping, chat history persistence, AED (auto-error-recovery) retry path.
- `tool_executor.py` — `ToolExecutor`. Synchronous tool dispatch, reasoning-parameter injection, timing, error capture.
- `tool_timing.py` — small helper for tool execution timing records.
- `tc_inbox.py` — `TCInbox` and `InvoluntaryToolCall`. Queue for synthetic `(call, result)` pairs that the kernel splices into the wire at safe boundaries (system notifications, soul flow voices, daemon emanations).
- `prompt.py` — `SystemPromptManager` plus `build_system_prompt` / `build_system_prompt_batches`. Composes the system prompt from identity, capabilities, intrinsics, pad, rules.
- `meta_block.py` — meta-block rendering (the structured prefix the kernel injects into LLM messages with state, time, stamina, etc.).
- `message.py` — `_make_message`, message-type sentinels (`MSG_REQUEST`, `MSG_TC_WAKE`). The wire format for the agent's inbox queue.
- `state.py` — `AgentState` enum (ACTIVE / IDLE / STUCK / ASLEEP / SUSPENDED).
- `config.py` — `AgentConfig` dataclass. Constructor-time options (stamina, soul cadences, max RPM, etc.).
- `workdir.py` — `WorkingDir`. Filesystem layout under the agent's working directory; manifest read/write; git operations.
- `handshake.py` — agent-discovery primitives (`is_agent`, `is_alive`) used by the TUI/portal to scan `.lingtai/` directories.
- `token_counter.py` — token counting helper (used for diary-cue cap, system prompt sizing).
- `token_ledger.py` — append-only per-call token usage log (`logs/token_ledger.jsonl`).
- `time_veil.py` — coarse-time rendering for state-aware prompts.
- `loop_guard.py` — guard against infinite tool loops.
- `logging.py` — logger configuration (separate from the `services/logging.py` event-log service).
- `llm_utils.py` — small shared helpers used by adapter implementations.
- `types.py` — shared type aliases.

## Connections

- **Kernel must never import from the wrapper.** `lingtai_kernel` is standalone; `lingtai` (the wrapper at `src/lingtai/`) depends on it strictly one-directionally.
- The kernel exposes its public surface through `__init__.py`. Anything not re-exported there is implementation detail.
- The wrapper layer registers LLM adapters into `llm.service` at import time, registers capabilities into `Agent` (which subclasses `BaseAgent`), and provides MCP, FileIO, Vision, Search, and the CLI.

## Composition

This file is the top of the kernel anatomy tree. Each subfolder below has its own `ANATOMY.md` — descend into the one that holds your question.

- [`base_agent/`](base_agent/ANATOMY.md) — `BaseAgent` class (the kernel coordinator). 7 submodules: identity, lifecycle, turn, soul_flow, tools, prompt, messaging.
- [`intrinsics/`](intrinsics/ANATOMY.md) — kernel-built-in tools. Four intrinsics: `system`, `psyche`, `soul`, `email`. Always present, never removable.
- [`llm/`](llm/ANATOMY.md) — LLM service ABC, adapter registry, chat interface, streaming protocol. Provider adapters live in the wrapper package, not here.
- [`services/`](services/ANATOMY.md) — kernel-side service implementations: filesystem mailbox (`mail.py`), JSONL event log (`logging.py`).
- [`migrate/`](migrate/ANATOMY.md) — versioned, append-only migrations for kernel-managed on-disk state. Each migration is `m<NNN>_<name>.py`.
- [`i18n/`](i18n/ANATOMY.md) — three-locale message catalog (en / zh / wen). Loaded by `t(language, key)` in the intrinsics.

## State

The kernel only writes inside the agent's working directory (`<workdir>/`). Per-folder anatomy files name the specific files each subsystem writes; this root only catalogs the top-level layout:

- `history/chat_history.jsonl` — wire history (one line per role+content entry).
- `history/snapshots/` — periodic git-tracked snapshots.
- `system/` — kernel-managed durable state (pad, soul records, summaries, rules).
- `logs/events.jsonl` — structured event log (the JSONL service).
- `logs/token_ledger.jsonl` — per-call token usage.
- `mailbox/{inbox,outbox,sent}/` — filesystem mailbox.
- `.agent.json`, `.agent.heartbeat`, `.status.json` — manifest, liveness signal, runtime snapshot.
- Signal files (`.prompt`, `.inquiry`, `.sleep`, `.suspend`, `.clear`, `.rules`) — consumed by `base_agent/lifecycle.py` heartbeat ticks.

## Notes

- **The anatomy tree is being populated.** Every existing subfolder anatomy is listed in Composition; deeper anatomies will appear as agents do work in those folders. When you do work in a folder that lacks one, write it before leaving — see the convention skill for the writing checklist.
