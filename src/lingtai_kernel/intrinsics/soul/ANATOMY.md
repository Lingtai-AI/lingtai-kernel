# intrinsics/soul

Inner voice and mechanical soul-flow. Three agent-callable actions
(`inquiry`, `config`, `voice`) plus one mechanical action (`flow`) that
fires on a wall-clock timer.

## Components

- `soul/__init__.py` — public intrinsic surface. `get_schema` (`soul/__init__.py:70-106`), `get_description` (`soul/__init__.py:65-67`), `handle` (`soul/__init__.py:109-151`) (the dispatcher). Re-exports constants from `config` (`soul/__init__.py:24-31`), private helpers from `config` (`soul/__init__.py:33-41`), consultation pipeline from `consultation` (`soul/__init__.py:43-60`), and `soul_inquiry` from `inquiry` (`soul/__init__.py:62`).
- `soul/config.py` — config and voice handling. `_handle_config` (`soul/config.py:27-113`) and `_handle_voice` (`soul/config.py:115-220`) dispatch `action='config'` and `action='voice'`. `_build_soul_system_prompt` (`soul/config.py:339-361`) resolves voice profiles to system prompts. `_persist_soul_config` (`soul/config.py:223-262`) and `_persist_soul_voice` (`soul/config.py:265-307`) write to `manifest.soul.*` in `init.json`. `_atomic_write_init` (`soul/config.py:310-336`) is the shared atomic JSON write helper. Constants: `SOUL_DELAY_MIN_SECONDS = 30.0` (`soul/config.py:11`), `CONSULTATION_PAST_COUNT_MIN = 0` (`soul/config.py:14`), `CONSULTATION_PAST_COUNT_MAX = 5` (`soul/config.py:15`), `SOUL_VOICE_BUILTINS` (`soul/config.py:20`), `SOUL_VOICE_PROMPT_MAX = 4000` (`soul/config.py:24`).
- `soul/consultation.py` — mechanical soul-flow pipeline. Constants: `_CONSULTATION_SYSTEM_PROMPT` (`soul/consultation.py:13-21`), `_CONSULTATION_TOOL_REFUSAL` (`soul/consultation.py:23-26`), `_CONSULTATION_MAX_ROUNDS = 3` (`soul/consultation.py:28`), `_DIARY_CUE_TOKEN_CAP = 10_000` (`soul/consultation.py:29`). `_send_with_timeout` (`soul/consultation.py:32-59`) wraps LLM calls with a daemon thread. `_render_current_diary` (`soul/consultation.py:62-121`) builds the time-anchored diary cue. `_write_soul_tokens` (`soul/consultation.py:124-145`) appends token-ledger entries. `_load_snapshot_interface` (`soul/consultation.py:147-177`) loads a `ChatInterface` from a snapshot file. `_fit_interface_to_window` (`soul/consultation.py:179-265`) tail-trims a `ChatInterface` to a token budget. `_kind_for_source` (`soul/consultation.py:268-272`) maps source labels to prompt kinds. `_build_consultation_cue` (`soul/consultation.py:275-298`) builds localized cue prompts. `_run_consultation` (`soul/consultation.py:301-399`) runs one substrate+spark consultation with refusal loop. `_list_snapshot_paths` (`soul/consultation.py:402-410`) lists snapshot files. `_run_consultation_batch` (`soul/consultation.py:413-484`) orchestrates parallel 1+K consultations. `build_consultation_pair` (`soul/consultation.py:487-519`) builds the synthetic `(ToolCallBlock, ToolResultBlock)` pair.
- `soul/inquiry.py` — synchronous mirror session. `soul_inquiry` (`soul/inquiry.py:9-55`) clones conversation (text+thinking only), sends question, returns answer.

## Connections

- `__init__.py` imports from `config` (`soul/__init__.py:24-41`) and `inquiry` (`soul/__init__.py:62`) for dispatch; imports from `consultation` (`soul/__init__.py:43-60`) for re-exports.
- `inquiry.py` imports `_build_soul_system_prompt` from `config` (`soul/inquiry.py:16`) and `_send_with_timeout` + `_write_soul_tokens` from `consultation` (`soul/inquiry.py:17`).
- `config.py` and `consultation.py` are leaves — no intra-package imports.
- All modules use `i18n.t()` for localized strings: `config.py` via `_build_soul_system_prompt` (`soul/config.py:347`), `consultation.py` via `_build_consultation_cue` (`soul/consultation.py:286`) and `build_consultation_pair` (`soul/consultation.py:496`), `__init__.py` via `get_schema`/`get_description` (`soul/__init__.py:66,71`).
- `consultation.py` reads snapshots written by `psyche._write_molt_snapshot` via `_load_snapshot_interface` (`soul/consultation.py:147-177`) and uses `llm.interface` block types (`ChatInterface`, `ToolResultBlock`, `ToolCallBlock`, `TextBlock`) throughout.

## State

- `config.py` mutates `init.json` (manifest.soul.*) for cadence (`soul/config.py:223-262`) and voice config (`soul/config.py:265-307`) via `_atomic_write_init` (`soul/config.py:310-336`).
- `consultation.py` reads `history/snapshots/snapshot_*.json` (`soul/consultation.py:147-177`) and `logs/events.jsonl` for the diary cue (`soul/consultation.py:78-121`), writes token-ledger entries via `_write_soul_tokens` (`soul/consultation.py:124-145`).
- No new state files introduced by the package split.

## Composition

- **Parent:** `src/lingtai_kernel/intrinsics/` (see `intrinsics/ANATOMY.md`).
- **Siblings:** `system.py`, `psyche.py`, `email.py` (flat files in the parent folder).
