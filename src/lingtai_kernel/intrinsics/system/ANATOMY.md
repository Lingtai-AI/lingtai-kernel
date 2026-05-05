# intrinsics/system

System intrinsic — runtime, lifecycle, and synchronization. Provides the agent with nap (pause execution), refresh (hot-reload config/presets), karma-gated lifecycle actions on other agents (sleep, lull, suspend, cpr, interrupt, clear, nirvana), preset listing, voluntary notification reads (`action="notification"`), and a deprecated `dismiss` no-op shim. The system module is also the **conceptual home** of the notification surface — it re-exports `publish_notification` / `clear_notification` from the kernel-root `notifications.py` so any in-process producer (intrinsic, capability, or wired-in MCP server) submits through one canonical entry point.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

- `__init__.py` — Package surface. Re-exports all public API for backward compatibility. Contains:
  - `get_description` / `get_schema` (re-exported from `schema.py`) — tool registration.
  - `_dismiss` (re-exported from `notification.py`) — deprecation shim retained so back-compat callers and old chat histories don't crash. Returns `{"status":"ok","note":"...deprecated..."}`.
  - **`publish_notification` / `clear_notification`** (re-exported from `lingtai_kernel.notifications` as `submit` / `clear`) — canonical producer entry point. Importable as `from lingtai_kernel.intrinsics.system import publish_notification, clear_notification`. The system module owns the notification surface conceptually; the implementation lives at the kernel root so non-intrinsic callers (and external scripts) can import it without going through the intrinsic surface.
  - All handler functions re-exported from sub-modules for backward compatibility.
  - `handle()` (`__init__.py:87-115`) — main dispatcher with explicit dispatch table. The `notification` action takes a fast path that returns `collect_notifications(agent._working_dir)` directly without going through the dispatch table — voluntary reads of the agent's own `.notification/` state.

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

- `notification.py` — `dismiss` deprecation shim. The `.notification/` filesystem-as-protocol redesign retired the per-id dismiss lifecycle: producers manage their own state (delete `.notification/email.json` when unread hits 0; rewrite `.notification/system.json` as events expire). This module is a back-compat no-op kept until Phase 3 deletes it.
  - `_dismiss()` (`notification.py:19-46`) — logs `system_dismiss_deprecated` and returns `{"status": "ok", "note": "...deprecated..."}`. Does NOT touch the wire or any queue.
  - Producer-side notification submission lives in `notifications.py` at the kernel root and is re-exported by this package's `__init__.py` as `publish_notification` / `clear_notification`. See root `ANATOMY.md` "Notifications" for the full architecture.

- `schema.py` — Tool registration.
  - `get_description()` (`schema.py:5-7`) — returns localized tool description.
  - `get_schema()` (`schema.py:10-47`) — returns JSON schema for the system tool. Action enum includes both legacy `dismiss` (for back-compat) and the new `notification` action.

## Connections

- **Inbound:** `handle()` is called by the tool dispatcher (via `base_agent._dispatch_tool`).
- **Inbound (cross-module):** `publish_notification` is imported by `base_agent/messaging.py` (both `_rerender_unread_digest` and `_enqueue_system_notification`) and by `intrinsics/soul/flow.py:_run_consultation_fire`. `clear_notification` is imported by the same call sites for the empty-state path. `_dismiss` is no longer called from `email/manager.py` — email arrivals use the single-slot unread-digest pattern, and dismiss is a no-op shim regardless.
- **Outbound:** Depends on `...notifications` (canonical `submit`/`clear`/`collect_notifications`), `...i18n` (translations), `...handshake` (`resolve_address`, `is_agent`, `is_alive`), `...state` (`AgentState`), `lingtai.presets` (preset loading), `lingtai.preset_connectivity` (connectivity probing).
- **Data flow:** Karma actions write signal files (`.sleep`, `.suspend`, `.interrupt`, `.clear`) into target agent working directories. Preset swap reads/writes `init.json` manifest. The `notification` action reads `.notification/*.json` (read-only); `publish_notification` re-export writes them via `tmp + rename`.

## Key invariants

- `handle()` uses an explicit dispatch table (`dict.get()`) rather than `globals().get()`, so it works correctly across sub-modules.
- The `notification` action is now agent-callable: it returns the bare `collect_notifications(workdir)` dict (no `_synthesized` envelope, since the call wasn't synthesized). Kernel-synthesized notification reads happen via the wire-injection path in `BaseAgent._inject_notification_pair` and carry `_synthesized: true` in their JSON body.
- Karma gate checks resolve addresses through `_check_karma_gate()` which validates admin flags before any filesystem mutation.
- `_dismiss` is a deprecation no-op: it always returns `{"status": "ok", "note": "..."}` regardless of input. Logs every call as `system_dismiss_deprecated` so unintended invocations surface in agent logs.
- `_nap` clears stale wake signals before sleeping and uses a TOCTOU-safe clear-then-wait pattern.
- Preset swap has two guards: authorization (allowed list) and context-fit (current tokens ≤ target context_limit).
- Producer notification writes (`publish_notification`) are atomic (`tmp + rename` inside `notifications.publish`) — readers never see a half-written file.
