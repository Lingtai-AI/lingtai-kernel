# intrinsics/soul

Inner voice and mechanical soul-flow. Three agent-callable actions
(`inquiry`, `config`, `voice`) plus one mechanical action (`flow`) that
fires on a wall-clock timer.

## Components

- `soul/__init__.py` — public intrinsic surface. `get_schema`, `get_description`, `handle` (the dispatcher). Re-exports all names consumed by `base_agent.py` and tests for backward compatibility.
- `soul/config.py` — config and voice handling. `_handle_config` and `_handle_voice` dispatch `action='config'` and `action='voice'`. `_build_soul_system_prompt` resolves voice profiles to system prompts. `_persist_soul_config`, `_persist_soul_voice`, `_atomic_write_init` write to `manifest.soul.*` in `init.json`.
- `soul/consultation.py` — mechanical soul-flow pipeline. Loads snapshots (`_load_snapshot_interface`), fits to window (`_fit_interface_to_window`), renders diary cue (`_render_current_diary`), runs consultation with refusal-loop (`_run_consultation`), orchestrates parallel batch (`_run_consultation_batch`), builds synthetic pair (`build_consultation_pair`). Also contains shared helpers `_send_with_timeout` and `_write_soul_tokens`.
- `soul/inquiry.py` — synchronous mirror session. `soul_inquiry` clones conversation (text+thinking only), sends question, returns answer.

## Connections

- `__init__.py` imports from `config` and `inquiry` for dispatch; imports from `consultation` for re-exports.
- `inquiry.py` imports `_build_soul_system_prompt` from `config` and `_send_with_timeout` + `_write_soul_tokens` from `consultation`.
- `config.py` and `consultation.py` are leaves — no intra-package imports.
- All modules use `i18n.t()` for localized strings.
- `consultation.py` reads snapshots written by `psyche._write_molt_snapshot` and uses `llm.interface` block types.

## State

- `config.py` mutates `init.json` (manifest.soul.*) for cadence and voice config.
- `consultation.py` reads `history/snapshots/snapshot_*.json` and `logs/events.jsonl` (diary cue), writes token-ledger entries.
- No new state files introduced by the package split.

## Composition

- **Parent:** `src/lingtai_kernel/intrinsics/` (see `intrinsics/ANATOMY.md`).
- **Siblings:** `system.py`, `psyche.py`, `email.py` (flat files in the parent folder).
