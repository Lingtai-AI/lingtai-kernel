# intrinsics

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues (mail or `discussions/<name>-patch.md`); do not silently fix.

Kernel-built-in tools — the four primitives every agent always has, never removable. Each is a sub-package with a uniform public shape: `get_schema(lang)`, `get_description(lang)`, `handle(agent, args)`, and (optionally) `boot(agent)`. `ALL_INTRINSICS` registers the four modules consumed by `BaseAgent` (`intrinsics/__init__.py:8-15`).

This file is a navigation hub. Each sub-package has its own `ANATOMY.md` with concrete file:line references; descend into the relevant one rather than expecting full coverage here.

## Components

- `intrinsics/__init__.py` — registry. Imports the four sub-packages and exposes `ALL_INTRINSICS = {"email": email, "system": system, "psyche": psyche, "soul": soul}`. `BaseAgent._wire_intrinsics()` (`base_agent/__init__.py:409`) consumes this dict and binds each module's `handle()` into the agent's tool surface.

- [`intrinsics/email/`](email/ANATOMY.md) — filesystem mailbox. Inbox/outbox/sent/archive folders, contacts, recurring schedules, mail delivery via daemon threads, system-notification synthesis on arrival. Decomposed in `d229efe` from a 1,530-line `email.py` into a 5-module sub-package (`__init__.py`, `primitives.py`, `schema.py`, `manager.py`).

- [`intrinsics/psyche/`](psyche/ANATOMY.md) — durable self and context lifecycle. The "bare essentials" of agent identity: lingtai (canonical character), pad (working notes), context molt (shed-and-reload), name handling. Decomposed in `1195f55` from a 946-line `psyche.py` into `_lingtai.py`, `_pad.py`, `_snapshots.py`, `_molt.py`, plus `__init__.py` with explicit `_DISPATCH` table (replaced the old `globals().get()` pattern). Snapshot orphan-tool-call closure landed in `704731b`.

- [`intrinsics/soul/`](soul/ANATOMY.md) — inner voice and mechanical soul-flow. Three agent-callable actions (`inquiry`, `config`, `voice`) plus one timer-driven action (`flow`). Decomposed earlier into `config.py`, `consultation.py`, `inquiry.py`, `flow.py`, `__init__.py`. The flow trunk (`flow.py`) owns the wall-clock timer, builds synthetic `(call, result)` pairs, and enqueues them on `tc_inbox` for splice into the wire chat.

- [`intrinsics/system/`](system/ANATOMY.md) — runtime, lifecycle, synchronization. Nap, refresh (preset hot-reload + authorization gate), karma-gated lifecycle actions on other agents (sleep, lull, suspend, cpr, interrupt, clear, nirvana), preset listing, notification dismissal. Decomposed in `e206dbc` from a 641-line `system.py` into `nap.py`, `preset.py`, `karma.py`, `notification.py`, `schema.py`, `__init__.py` with explicit dispatch table.

## Connections

- `BaseAgent._wire_intrinsics()` imports `ALL_INTRINSICS` and binds each module's `handle()` callback. Boot hooks are special-cased: `BaseAgent` calls `psyche.boot(agent)` and `email.boot(agent)` during construction (the soul and system intrinsics have no boot hook).
- Cross-intrinsic flows worth knowing about:
  - **soul → psyche state**: `_run_consultation_batch` reads `history/snapshots/snapshot_*.json` written by `psyche._write_molt_snapshot` as past-self substrate.
  - **email → system**: `email._read()` calls `system._dismiss(notif_id)` to auto-clear the kernel-synthesized notification pair when the agent reads a mail.
  - **email and others → kernel**: producers call `agent._enqueue_system_notification(source, ref_id, body)` (`base_agent/messaging.py:63`) to surface out-of-band events as synthetic tool-call pairs. See root `ANATOMY.md` "Involuntary tool-call pairs" for the contract.
- All four intrinsics use `i18n.t()` for localized descriptions and schemas.

## Composition

- **Parent:** `src/lingtai_kernel/` (see `src/lingtai_kernel/ANATOMY.md`).
- **Sub-packages:** all four intrinsics are now packages (post-`d229efe`/`1195f55`/`e206dbc`). There are no flat-file intrinsics remaining.
- **Siblings:** `llm/` for canonical block/session types, `services/` for mailbox/logging service implementations, `i18n/` for localized strings, `base_agent/` for the coordinator that wires intrinsics in.

## State

Detailed file/path lists belong in each sub-anatomy's State section. High-level summary:

- `email/` writes `mailbox/{inbox,outbox,sent,archive}/<id>/message.json`, `mailbox/read.json`, `mailbox/contacts.json`, `mailbox/schedules/<id>/schedule.json`.
- `psyche/` writes `system/lingtai.md`, `system/pad.md`, `system/pad_append.json`, `system/summaries/molt_<count>_<ts>.md`, and `history/snapshots/snapshot_<count>_<ts>.json`.
- `soul/` writes `logs/soul_flow.jsonl`, `logs/soul_inquiry.jsonl`, mutates `init.json` (manifest.soul.* for cadence/voice config), and writes token-ledger entries for soul LLM calls.
- `system/` mutates process/lifecycle state; karma actions write signal files (`.sleep`, `.suspend`, `.interrupt`, `.clear`) into target agent working directories; nirvana removes target working directories entirely.

## Notes

- **Intrinsics are kernel primitives, not optional capabilities.** Capabilities (in the wrapper layer at `lingtai/core/`) may wrap or override them via `BaseAgent.override_intrinsic()` (`base_agent/__init__.py:759`).
- **Uniform public shape**: every intrinsic exposes `get_schema(lang)`, `get_description(lang)`, `handle(agent, args)`. Boot hooks are optional (`psyche.boot`, `email.boot`).
- **`notification` action is kernel-synthesized only**: `system.handle()` explicitly rejects agent-initiated `system(action="notification")` calls. The real producer hook is `agent._enqueue_system_notification` (kernel-side).
- **Decomposition rationale**: each of the four hit a complexity threshold where its internal subsystems no longer fit cleanly in one file (mailbox I/O vs delivery vs scheduling for email; molt vs pad vs snapshot for psyche; flow vs consultation vs config for soul; nap vs preset vs karma vs dismiss for system). Sub-anatomies document the per-package internal layout.
