# intrinsics

Kernel-built-in tools. Every intrinsic is a flat Python module with the same public shape: `get_schema(lang)`, `get_description(lang)`, and `handle(agent, args)` (`intrinsics/__init__.py:1-7`). `ALL_INTRINSICS` registers the four always-present modules: `email`, `system`, `psyche`, and `soul` (`intrinsics/__init__.py:8-15`).

## Components

- `intrinsics/__init__.py` — imports the four modules and exposes the registry consumed by `BaseAgent` (`intrinsics/__init__.py:8-15`).
- `intrinsics/system.py` — runtime/lifecycle control. Its schema enumerates agent-callable actions (`nap`, `refresh`, `sleep`, `lull`, `interrupt`, `suspend`, `cpr`, `clear`, `nirvana`, `presets`, `dismiss`) (`intrinsics/system.py:41-78`); `handle()` rejects kernel-only `notification` and dispatches through a handler map (`intrinsics/system.py:81-111`). Karma and nirvana gates are declared near the action helpers (`intrinsics/system.py:424-428`).
- `intrinsics/psyche.py` — durable self and context management. It writes molt snapshots with `SNAPSHOT_SCHEMA_VERSION = 1` (`intrinsics/psyche.py:40`, `intrinsics/psyche.py:103-164`), schemas four objects (`lingtai`, `pad`, `context`, `name`) (`intrinsics/psyche.py:178-220`), routes by `(object, action)` (`intrinsics/psyche.py:233-254`), performs agent-initiated molt in `_context_molt()` (`intrinsics/psyche.py:516-753`), system-forced molt in `context_forget()` (`intrinsics/psyche.py:785-929`), and boot-loads lingtai+pad (`intrinsics/psyche.py:937-945`).
- `intrinsics/soul/` — inner voice and mechanical soul-flow. A package of four modules; see `intrinsics/soul/ANATOMY.md`. `__init__.py` re-exports the public intrinsic surface (`get_schema`, `get_description`, `handle`) plus all names consumed by `base_agent.py` and tests so that `from .intrinsics.soul import X` paths are unchanged.
- `intrinsics/email.py` — filesystem mailbox intrinsic. Its module docstring defines the mailbox layout (`intrinsics/email.py:7-13`). `EmailManager` owns tool dispatch and scheduling (`intrinsics/email.py:490`, `intrinsics/email.py:639-1514`); module-level `handle()` delegates to the manager (`intrinsics/email.py:1505-1515`); `boot()` installs the manager and starts the scheduler (`intrinsics/email.py:1517-1530`). Lower-level helpers persist inbox/outbox records and hand off delivery to `_mailman()` (`intrinsics/email.py:191-298`).

## Connections

- `BaseAgent` imports `ALL_INTRINSICS` (`base_agent.py:29`) and binds each module `handle()` in `_wire_intrinsics()` (`base_agent.py:444-448`).
- Boot hooks are special-cased: `BaseAgent` calls `psyche.boot(self)` and `email.boot(self)` during construction (`base_agent.py:432-438`).
- Intrinsics depend on sibling kernel services/types: system resolves peers through `handshake.resolve_address` (`intrinsics/system.py:34`); psyche and soul use canonical LLM blocks (`intrinsics/psyche.py:37`, `intrinsics/soul/consultation.py:159`); email emits inbox wake messages through `_make_message` (`intrinsics/email.py:36`).
- Soul consumes psyche state: `_write_molt_snapshot()` writes `history/snapshots/` (`intrinsics/psyche.py:131-164`), and `_load_snapshot_interface()` reads those snapshots as past-self substrate (`intrinsics/soul/consultation.py:147-177`).
- All four modules use `i18n.t()` for localized descriptions and schemas (`intrinsics/system.py:37-42`, `intrinsics/psyche.py:173-179`, `intrinsics/soul/__init__.py:65-72`, `intrinsics/email.py:35-41`).

## Composition

- **Parent:** `src/lingtai_kernel/` (see `src/lingtai_kernel/ANATOMY.md`).
- **Subfolders:** `soul/` is now a package (4 modules, see its ANATOMY.md). `system.py`, `psyche.py`, and `email.py` remain flat files.
- **Siblings:** `llm/` for canonical block/session types, `services/` for mailbox/logging service implementations, and `i18n/` for localized strings.

## State

- `psyche.py` writes `system/lingtai.md`, `system/pad.md`, `system/pad_append.json`, `system/summaries/molt_<count>_<ts>.md`, and `history/snapshots/snapshot_<count>_<ts>.json` (`intrinsics/psyche.py:43-164`, `intrinsics/psyche.py:311-515`).
- `email.py` writes `mailbox/{inbox,outbox,sent,archive}/<id>/message.json`, `mailbox/read.json`, `mailbox/contacts.json`, and `mailbox/schedules/<id>/schedule.json` (`intrinsics/email.py:7-13`).
- `soul/` mutates `init.json` for soul cadence/profile config through `_persist_soul_config` and `_persist_soul_voice` (`intrinsics/soul/config.py:223-307`) and writes token-ledger entries for soul LLM calls through `_write_soul_tokens()` (`intrinsics/soul/consultation.py:124-145`).
- `system.py` mostly mutates process/lifecycle state; destructive actions can create `.sleep`/`.suspend` signals or remove target working directories through their handlers (`intrinsics/system.py:449-574`).

## Notes

- `soul/` is the first per-intrinsic subdirectory; `system.py`, `psyche.py`, and `email.py` remain flat files today.
- `soul.py` was split into a package because it contained both agent-callable inquiry/config/voice actions and the mechanical consultation pipeline; the latter connects snapshots, diary logs, LLM sessions, and `tc_inbox` synthetic pairs. See `intrinsics/soul/ANATOMY.md` for the internal layout.
- Intrinsics are kernel primitives, not optional capabilities. Capabilities may wrap/override them via `BaseAgent.override_intrinsic()` (`base_agent.py:2285-2295`).
