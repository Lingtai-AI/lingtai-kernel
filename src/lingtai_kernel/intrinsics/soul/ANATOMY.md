# intrinsics/soul

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues (mail or `discussions/<name>-patch.md`); do not silently fix.

Inner voice and mechanical soul-flow. Three agent-callable actions
(`inquiry`, `config`, `voice`) plus one mechanical action (`flow`) that
fires on a wall-clock timer.

## Components

- `soul/__init__.py` — public intrinsic surface. `get_schema` (`soul/__init__.py:70-106`), `get_description` (`soul/__init__.py:65-67`), `handle` (`soul/__init__.py:109-151`) (the dispatcher). Re-exports constants from `config` (`soul/__init__.py:24-31`), private helpers from `config` (`soul/__init__.py:33-41`), consultation pipeline from `consultation` (`soul/__init__.py:43-60`), `soul_inquiry` and `_run_inquiry` from `inquiry` (`soul/__init__.py:62-63`), and flow functions from `flow` (`soul/__init__.py:65-72`).
- `soul/config.py` — config and voice handling. `_handle_config` (`soul/config.py:27-113`) and `_handle_voice` (`soul/config.py:115-220`) dispatch `action='config'` and `action='voice'`. `_build_soul_system_prompt` (`soul/config.py:339-361`) resolves voice profiles to system prompts. `_persist_soul_config` (`soul/config.py:223-262`) and `_persist_soul_voice` (`soul/config.py:265-307`) write to `manifest.soul.*` in `init.json`. `_atomic_write_init` (`soul/config.py:310-336`) is the shared atomic JSON write helper. Constants: `SOUL_DELAY_MIN_SECONDS = 30.0` (`soul/config.py:11`), `CONSULTATION_PAST_COUNT_MIN = 0` (`soul/config.py:14`), `CONSULTATION_PAST_COUNT_MAX = 5` (`soul/config.py:15`), `SOUL_VOICE_BUILTINS` (`soul/config.py:20`), `SOUL_VOICE_PROMPT_MAX = 4000` (`soul/config.py:24`).
- `soul/consultation.py` — LLM call mechanics for past-self consultations. Constants: `_CONSULTATION_SYSTEM_PROMPT` (`soul/consultation.py:13-21`), `_CONSULTATION_TOOL_REFUSAL` (`soul/consultation.py:23-26`), `_CONSULTATION_MAX_ROUNDS = 3` (`soul/consultation.py:28`), `_DIARY_CUE_TOKEN_CAP = 10_000` (`soul/consultation.py:29`). `_send_with_timeout` (`soul/consultation.py:32-59`) wraps LLM calls with a daemon thread. `_render_current_diary` (`soul/consultation.py:62-121`) builds the time-anchored diary cue. `_write_soul_tokens` (`soul/consultation.py:124-145`) appends token-ledger entries. `_load_snapshot_interface` (`soul/consultation.py:147-177`) loads a `ChatInterface` from a snapshot file. `_fit_interface_to_window` (`soul/consultation.py:179-265`) tail-trims a `ChatInterface` to a token budget. `_kind_for_source` (`soul/consultation.py:268-272`) maps source labels to prompt kinds. `_build_consultation_cue` (`soul/consultation.py:275-298`) builds localized cue prompts. `_run_consultation` (`soul/consultation.py:301-399`) runs one substrate+spark consultation with refusal loop. `_list_snapshot_paths` (`soul/consultation.py:402-410`) lists snapshot files. `_run_consultation_batch` (`soul/consultation.py:413-484`) orchestrates parallel 1+K consultations. `build_consultation_pair` (`soul/consultation.py:487-519`) builds the synthetic `(ToolCallBlock, ToolResultBlock)` pair.
- `soul/inquiry.py` — on-demand inquiry. `soul_inquiry` (`soul/inquiry.py:9-61`) clones conversation (text+thinking only), sends question, returns answer. `_run_inquiry` (`soul/inquiry.py:64-77`) runs `soul_inquiry` and persists the result via `flow._persist_soul_entry`.
- `soul/flow.py` — mechanical soul-flow cadence. This is the trunk of the soul package — both `inquiry.py` and the kernel hooks depend on it. `_start_soul_timer` (`soul/flow.py:26-43`), `_cancel_soul_timer` (`soul/flow.py:46-51`), `_soul_whisper` (`soul/flow.py:54-73`), `_persist_soul_entry` (`soul/flow.py:76-93`), `_append_soul_flow_record` (`soul/flow.py:96-102`), `_flatten_v3_for_pair` (`soul/flow.py:105-133`), `_run_consultation_fire` (`soul/flow.py:136-233`), `_rehydrate_appendix_tracking` (`soul/flow.py:236-271`).

## Connections

- `flow.py` is the trunk: both `inquiry.py` (imports `_persist_soul_entry` from `.flow`) and the kernel hooks (`_start_soul_timer`, `_cancel_soul_timer`, `_run_consultation_fire`, `_rehydrate_appendix_tracking`) depend on it.
- `__init__.py` imports from `config`, `inquiry`, `consultation`, and `flow` for dispatch and re-exports.
- `inquiry.py` imports `_build_soul_system_prompt` from `config`, `_send_with_timeout` + `_write_soul_tokens` from `consultation`, and `_persist_soul_entry` from `flow`.
- `flow.py` imports `_render_current_diary`, `_run_consultation_batch`, `build_consultation_pair` from `consultation` (sibling, within `flow._run_consultation_fire`).
- `config.py` and `consultation.py` are leaves — no intra-package imports.
- All modules use `i18n.t()` for localized strings.
- `consultation.py` reads snapshots written by `psyche._write_molt_snapshot` via `_load_snapshot_interface` and uses `llm.interface` block types throughout.

## State

- `config.py` mutates `init.json` (manifest.soul.*) for cadence and voice config via `_atomic_write_init`.
- `consultation.py` reads `history/snapshots/snapshot_*.json` and `logs/events.jsonl` for the diary cue, writes token-ledger entries via `_write_soul_tokens`.
- `flow.py` writes `logs/soul_flow.jsonl` (`soul/flow.py:96-102`) and `logs/soul_inquiry.jsonl` (via `_persist_soul_entry`), enqueues on `tc_inbox` (via `_run_consultation_fire`). Soul is the **canonical advanced producer** for involuntary tool-call pairs — it builds `InvoluntaryToolCall` directly with `coalesce=True` (latest voice wins when the timer fires repeatedly during a busy stretch) and `replace_in_history=True` (single-slot wire history — the prior pair is removed before the new one is spliced). Simple notification producers (mail, daemon, MCP) use the higher-level `agent._enqueue_system_notification(...)` helper instead. See root `ANATOMY.md` "Involuntary tool-call pairs".

## Composition

- **Parent:** `src/lingtai_kernel/intrinsics/` (see `intrinsics/ANATOMY.md`).
- **Siblings:** `system.py`, `psyche.py`, `email.py` (flat files in the parent folder).
- **Kernel hooks:** `base_agent/__init__.py` calls `_start_soul_timer`/`_cancel_soul_timer`/`_run_consultation_fire`/`_rehydrate_appendix_tracking` from `flow.py` and `_run_inquiry` from `inquiry.py` at lifecycle moments.
