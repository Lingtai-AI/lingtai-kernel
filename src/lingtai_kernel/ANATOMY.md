# lingtai_kernel

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues (mail or `discussions/<name>-patch.md`); do not silently fix.

The minimal agent runtime: turn loop, lifecycle, signal consumption, tool dispatch, intrinsic wiring, mailbox glue, soul/molt orchestration. The kernel is standalone — the wrapper package `lingtai` (at `src/lingtai/`) depends on it strictly one-directionally.

> **What is an `ANATOMY.md`?** See the canonical convention at `src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md`. This file follows the same 6-section template as every other anatomy in the tree.

## Components

The kernel root holds the coordinator (`base_agent/`) plus a flat collection of supporting modules. Most are self-contained leaves; subfolders are concept-boundary units with their own anatomy.

- `base_agent/` — `BaseAgent`, the kernel coordinator (package of 6 modules). `__init__.py` defines `BaseAgent` (~920 lines: constructor, properties, state machine, hooks, cross-cutting stubs, pass-throughs to submodules). Submodules: `lifecycle.py` (start/stop/heartbeat/signals/refresh), `turn.py` (main loop/message dispatch/AED/response processing), `tools.py` (tool schemas/dispatch/registry), `identity.py` (naming/manifest/status), `prompt.py` (system prompt building/flushing), `messaging.py` (mail/notifications/outbound). Soul-flow domain logic lives in `intrinsics/soul/flow.py`; splice logic lives in `tc_inbox.TCInbox.drain_into()`. See `base_agent/ANATOMY.md`.
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

## Involuntary tool-call pairs (notifications, soul voices, scheduled events)

Out-of-band events — mail arrival, soul-flow firings, daemon emanations, MCP webhooks, scheduled wakeups — surface in the agent's wire chat as **synthetic `(ToolCallBlock, ToolResultBlock)` pairs** that look identical to tool calls the agent itself made. The LLM has no native concept of "the world poked the agent," so the kernel masquerades external events as the existing tool-use shape.

```
assistant: tool_call(name="system", args={action:"notification", source:"email", ...})
user:      tool_result(id=…, content="📧 New mail from alice — 'meeting Thurs?'")
```

### Producer hook (the "wire X to notify the agent" entry point)

Any subsystem (capability, daemon, MCP server, scheduler) calls **`agent._enqueue_system_notification(source, ref_id, body)`** (`base_agent/messaging.py:63`). Three string args, returns a `notif_id`:

```python
notif_id = agent._enqueue_system_notification(
    source="daemon.cron",                     # dotted tag, freeform
    ref_id="job_42",                          # external handle (your choice)
    body="⏰ Cron 'morning standup' is due.",  # what the agent literally reads
)
```

Side-effects beyond the enqueue (all automatic):
- `agent._wake_nap("system_notification_enqueued")` — wakes IDLE/ASLEEP agents.
- Posts `MSG_TC_WAKE` to `agent.inbox` so the run loop unblocks immediately.
- Logs `system_notification_enqueued` to `events.jsonl` with `notif_id`, `call_id`, `source`, `ref_id`.

Callable from any thread — `tc_inbox` is lock-protected.

### Queue + drain (`tc_inbox.py`)

`TCInbox` is a thread-safe queue of `InvoluntaryToolCall` items. `_drain_tc_inbox()` (called at every turn boundary, `base_agent/turn.py:311`) splices queued pairs into `chat.interface.entries` only when the wire tail has no unanswered tool_calls — splicing into a mid-pair tail would create an orphan the LLM provider rejects. If the boundary is unsafe, items stay queued and the next turn retries.

Two opt-in flags on `InvoluntaryToolCall` for non-notification producers:
- `coalesce=True` — replace any prior queued item with the same `source` key (latest-wins, used by soul flow so multiple firings during a busy stretch collapse to one reflection).
- `replace_in_history=True` — also remove the prior pair of the same `source` from `entries` before splicing (single-slot wire history, used by soul consultation).

The notification helper picks `coalesce=False, replace_in_history=False` because every event deserves its own slot. To opt in, build `InvoluntaryToolCall` directly and call `agent._tc_inbox.enqueue(item)` — soul flow does this at `intrinsics/soul/flow.py:216`.

### Dismiss path (if your event has lifecycle)

If "the agent acted on this notification → it's no longer relevant," removal has two cases the kernel handles for you via `intrinsics/system/_dismiss`:

- **Still queued** (not yet spliced): `agent._tc_inbox.remove_by_notif_id(notif_id)` — only matches notification-shape items.
- **Already spliced**: `agent._chat.interface.remove_pair_by_call_id(call_id)`.

Mail does both: `intrinsics/email/manager.py` calls `_system._dismiss(...)` on `email.read` to auto-clear the notification when the agent reads the message.

### Producers in the kernel today

| Producer | Source key | Trigger |
|---|---|---|
| Mail arrival | `system.notification:<notif_id>` | `base_agent/messaging.py:_on_normal_mail` |
| Mail bounce | `system.notification:<notif_id>` | `intrinsics/email/primitives.py:280` |
| Soul flow | `soul.flow` | `intrinsics/soul/flow.py:216` (uses `coalesce`+`replace_in_history`) |

New producers (daemon scheduler, MCP webhooks, etc.) wire in by calling `agent._enqueue_system_notification(...)` from their event handler. No splicing logic, no wake logic, no dispatch registration needed.

### Adjacent: healing mid-pair tails

Distinct primitive — `interface.close_pending_tool_calls(reason)` (`llm/interface.py:344`) synthesizes `tool_result` placeholders for orphan tool_calls when the wire chat itself ends mid-pair (process killed mid-turn, snapshot saved mid-turn). Marks them `synthesized=True`; if a real result arrives later for the same id, `add_tool_results` overwrites the placeholder so the wire stays honest. Used in `base_agent/turn.py:202, 446, 461` after exceptions, and at snapshot save time in `intrinsics/psyche/_snapshots.py`.

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
