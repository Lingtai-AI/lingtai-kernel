# intrinsics/system

System intrinsic — runtime, lifecycle, and synchronization. Provides the agent with nap (pause execution), refresh (hot-reload config/presets), karma-gated lifecycle actions on other agents (sleep, lull, suspend, cpr, interrupt, clear, nirvana), preset listing, and notification dismissal.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

- `__init__.py` — Package surface. Re-exports all public API for backward compatibility. Contains:
  - `get_description` / `get_schema` (re-exported from `schema.py`) — tool registration.
  - `_dismiss` (re-exported from `notification.py`) — cross-module import target for `email/manager.py`.
  - All handler functions re-exported from sub-modules for backward compatibility.
  - `handle()` (`__init__.py:104-132`) — main dispatcher with explicit dispatch table (not `globals()`).

- `nap.py` — Nap action.
  - `_nap()` (`nap.py:12-56`) — pause execution; polls for wake signals (cancel, nap_wake) or timeout. Max wait capped at 300s.

- `preset.py` — Preset management and refresh.
  - `_preset_ref_in()` (`preset.py:13-33`) — normalized membership test for preset path strings (~/foo vs absolute).
  - `_check_context_fits()` (`preset.py:36-64`) — verify agent's current context fits within target preset's context_limit.
  - `_refresh()` (`preset.py:67-148`) — stop, reload config + MCP servers, restart. Handles preset swap (named or revert) with authorization gate and context-limit guard.
  - `_presets()` (`preset.py:151-232`) — list available presets with LLM connectivity probing.

- `karma.py` — Karma-gated lifecycle actions.
  - `_KARMA_ACTIONS` / `_NIRVANA_ACTIONS` (`karma.py:13-14`) — gate mapping sets.
  - `_check_karma_gate()` (`karma.py:17-36`) — authorization gate: validates karma/nirvana admin flags, resolves target address, rejects self-targeting.
  - `_sleep()` (`karma.py:39-51`) — self-sleep (no karma needed).
  - `_lull()` (`karma.py:54-64`) — put another agent to sleep.
  - `_suspend()` (`karma.py:67-77`) — suspend another agent.
  - `_cpr()` (`karma.py:80-92`) — resuscitate a suspended agent.
  - `_interrupt()` (`karma.py:95-105`) — interrupt a running agent's current turn.
  - `_clear()` (`karma.py:108-128`) — force a full molt on another agent.
  - `_nirvana()` (`karma.py:131-149`) — permanently destroy an agent's working directory.

- `notification.py` — Notification dismissal.
  - `_dismiss()` (`notification.py:11-73`) — idempotent removal of notification pairs from both `_tc_inbox` queue and `_session.chat` wire, with reverse-lookup cleanup of `_pending_mail_notifications`.

- `schema.py` — Tool registration.
  - `get_description()` (`schema.py:6-8`) — returns localized tool description.
  - `get_schema()` (`schema.py:11-48`) — returns JSON schema for the system tool.

## Connections

- **Inbound:** `handle()` is called by the tool dispatcher (via `base_agent._dispatch_tool`).
- **Inbound (cross-module):** `_dismiss` is called by `email/manager.py:790-791` via `from .. import system as _system; _system._dismiss(...)` — auto-dismisses notification pairs on `email.read`.
- **Outbound:** Depends on `...i18n` (translations), `...handshake` (`resolve_address`, `is_agent`, `is_alive`), `...state` (`AgentState`), `lingtai.presets` (preset loading), `lingtai.preset_connectivity` (connectivity probing).
- **Data flow:** Karma actions write signal files (`.sleep`, `.suspend`, `.interrupt`, `.clear`) into target agent working directories. Preset swap reads/writes `init.json` manifest.

## Key invariants

- `handle()` uses an explicit dispatch table (`dict.get()`) rather than `globals().get()`, so it works correctly across sub-modules.
- The `notification` action is explicitly blocked in `handle()` — it is kernel-synthesized only.
- Karma gate checks resolve addresses through `_check_karma_gate()` which validates admin flags before any filesystem mutation.
- `_dismiss` is idempotent: unknown notif_ids are silently no-op'd and reported as `"not_found"`.
- `_nap` clears stale wake signals before sleeping and uses a TOCTOU-safe clear-then-wait pattern.
- Preset swap has two guards: authorization (allowed list) and context-fit (current tokens ≤ target context_limit).
