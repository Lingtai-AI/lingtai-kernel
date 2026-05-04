# lingtai_kernel

The minimal agent runtime. This is the **coding-agent entrance** to the kernel's anatomy tree. The matching LingTai-agent entrance is the `lingtai-kernel-anatomy` skill — same destination, different doorway.

## What an `ANATOMY.md` is

An `ANATOMY.md` file is the structural description of one folder of code. It is **not** docs, not a contract, not a usage guide — it is a code-cited map of what exists in this folder, how the parts connect, and where state lives. Every claim it makes is grounded in a `file:line` reference into the code; the code is the truth, the anatomy is the navigation aid.

The shape of every `ANATOMY.md`:

- **What this is** — one paragraph.
- **Components** — files / functions / classes here, with `file:line` citations and one-line purposes.
- **Connections** — what calls in, what this folder calls out, what data flows through.
- **Composition** — parent folder, subfolders (each linked to its own `ANATOMY.md`).
- **State** — persistent state written by this folder, schema versions, ephemeral state managed.
- **Notes** — bounded section for rationale, history, gotchas not visible in code.

A folder gets an `ANATOMY.md` when a competent agent could do useful reasoning about it as a unit without reading its siblings first. Trivial leaves do not.

## Use anatomy as navigator, not grep

You are a coding agent. Reading 200 lines of code is one tool call; greping a symbol gives you 50 hits each costing their own evaluation. For **structural** questions ("what shape is this part of the kernel, where does behavior X live, what does Y connect to") descend `ANATOMY.md` files top-down — three reads will usually take you deeper than fifty grep hits. For **enumeration** questions ("every callsite of this function, every file matching this pattern") grep is still right.

| Question type | Tool |
|---|---|
| Structural | Descend the anatomy tree |
| Enumeration | grep |

## Maintenance is part of reading

Every agent that reads anatomy is also a maintainer. The contract:

- **Code matches anatomy:** read on, no action.
- **Code disagrees with anatomy:** the code is almost always right. Update the anatomy to match before you leave the file. If you believe the code itself is wrong, report the bug — and note that anatomy and code disagreed, because that disagreement is itself a clue.
- **Anatomy missing or empty:** if you understood the folder well enough to do your task, write the anatomy. Components, connections, state. ~80 lines cap; less is better.

This is the immune-response mechanism. Without it, anatomy rots like every other documentation scheme.

## Cross-references

`ANATOMY.md` files cross-reference each other by **relative path from the kernel root** (this directory). A child cites its parent and any structural neighbors it depends on; references are sparse and one-directional. The kernel-root anatomy (this file) is the only one that holds a complete child enumeration.

## Components — files in `src/lingtai_kernel/`

The kernel root holds the coordinator (`base_agent.py`) plus a flat collection of supporting modules. Most are self-contained leaves; subfolders are concept-boundary units with their own anatomy.

- `base_agent.py` — `BaseAgent` (the kernel coordinator, ~2400 lines). Turn loop, 5-state lifecycle, signal consumption, tool dispatch, intrinsic wiring, mailbox glue, soul/molt orchestration. The single largest file in the kernel.
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

## Composition — subfolders

Each subfolder below has (or will have) its own `ANATOMY.md`. Descend into the one that holds your question.

- [`intrinsics/`](intrinsics/ANATOMY.md) — kernel-built-in tools. Four intrinsics: `system`, `psyche`, `soul`, `email`. Always present, never removable.
- [`llm/`](llm/ANATOMY.md) — LLM service ABC, adapter registry, chat interface, streaming protocol. Provider adapters live in the wrapper package, not here.
- [`services/`](services/ANATOMY.md) — kernel-side service implementations: filesystem mailbox (`mail.py`), JSONL event log (`logging.py`).
- [`migrate/`](migrate/ANATOMY.md) — versioned, append-only migrations for kernel-managed on-disk state. Each migration is `m<NNN>_<name>.py`.
- [`i18n/`](i18n/ANATOMY.md) — three-locale message catalog (en / zh / wen). Loaded by `t(language, key)` in the intrinsics.

## Connections — into and out of the kernel

- **Kernel must never import from the wrapper.** `lingtai_kernel` is standalone; `lingtai` (the wrapper at `src/lingtai/`) depends on it strictly one-directionally.
- The kernel exposes its public surface through `__init__.py`. Anything not re-exported there is implementation detail.
- The wrapper layer registers LLM adapters into `llm.service` at import time, registers capabilities into `Agent` (which subclasses `BaseAgent`), and provides MCP, FileIO, Vision, Search, and the CLI.

## State — what the kernel writes

The kernel only writes inside the agent's working directory (`<workdir>/`). Per-folder anatomy files name the specific files each subsystem writes; this root only catalogs the top-level layout:

- `history/chat_history.jsonl` — wire history (one line per role+content entry).
- `history/snapshots/` — periodic git-tracked snapshots.
- `system/` — kernel-managed durable state (pad, soul records, summaries, rules).
- `logs/events.jsonl` — structured event log (the JSONL service).
- `logs/token_ledger.jsonl` — per-call token usage.
- `mailbox/{inbox,outbox,sent}/` — filesystem mailbox.
- `.agent.json`, `.agent.heartbeat`, `.status.json` — manifest, liveness signal, runtime snapshot.
- Signal files (`.prompt`, `.inquiry`, `.sleep`, `.suspend`, `.clear`, `.rules`) — consumed by `base_agent.py` heartbeat ticks.

## Notes

- **This anatomy tree is being populated.** Most subfolder `ANATOMY.md` files do not yet exist as of v3.0.0 of the convention. When you do work in a folder that lacks one, write it before leaving — the next agent will thank you.
- **No leaf stubs.** Empty placeholders are clutter; missing files are an honest signal that the folder hasn't been mapped yet.
- The matching LingTai-agent entrance is the `lingtai-kernel-anatomy` skill at `src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md`. Same content, different doorway.
