---
related_files:
  - src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
  - tests/test_notification_tool.py
  - tests/test_system_dismiss.py
  - src/lingtai/tools/notification/glossary-en.md
  - src/lingtai/tools/notification/glossary-zh.md
  - src/lingtai/tools/notification/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# intrinsics/notification

Standalone notification surface — the **only** agent-callable home for the
notification verbs, **mandatory-included** like `system`. It owns reading the
live notification surface (`check`) and clearing notification mirrors via three
**atomic** dismiss verbs (`dismiss_channel`, `dismiss_event`, `dismiss_ref`).
There is no kitchen-sink `dismiss`. The `system` tool exposes **no** notification
or dismiss verb — there are no compatibility aliases. `summarize` is **not** here:
it remains a `system` action (context hygiene, not a notification verb).

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

- `__init__.py` — dispatch over four actions.
  - `get_description` / `get_schema` (re-exported from `schema.py`) — tool registration.
  - `handle()` (`__init__.py:148-158`) — dispatcher over `check`, `dismiss_channel`, `dismiss_event`, `dismiss_ref`. Unknown actions return a `status="error"` dict.
  - `_check()` (`__init__.py:67-72`) — voluntary read of the notification surface. Returns a placeholder dict (`_notification_placeholder: True` + message). The live payload (`_meta.notifications` + `_meta.notification_guidance`) is stamped onto this same result by `meta_block.attach_active_notifications`, which walks backward for the freshest *dict-shaped* tool result (`_last_dict_result`, `src/lingtai/kernel/meta_block.py:2788`; consumed by `attach_active_notifications`, `src/lingtai/kernel/meta_block.py:2950`) — tool-name-agnostic, so `notification(action=check)` receives the identical stamp the old `system(action="notification")` placeholder did.
  - `_dismiss_channel()` (`__init__.py:75-104`) — whole-channel clear. Rejects `event_id`/`ref_id` (those are atomic-event verbs). Delegates to `notifications.dismiss_channel(..., invoked_by="notification")`.
  - `_dismiss_event()` (`__init__.py:107-122`) — remove one `system` event by `event_id`; `channel` defaults to `system`. Delegates to the same helper with `event_id=...`.
  - `_dismiss_ref()` (`__init__.py:125-140`) — remove `system` event(s) by `ref_id`; `channel` defaults to `system`. Delegates with `ref_id=...`.
  - All three dismiss verbs route into the single canonical `notifications.dismiss_channel`. The decision logic (allowlist, `post-molt` ack-reason, protected channels, generic-dismiss guard, stale-channel-version refusal, **legacy `large_tool_result` dismiss-and-ack escape hatch**, atomic `event_id`/`ref_id` removal) lives there; `invoked_by="notification"` only affects which provenance log line is emitted.

- `schema.py` — tool registration. Exposes `action` (`check`/`dismiss_channel`/`dismiss_event`/`dismiss_ref`) plus the params `channel`, `force`, `event_id`, `ref_id`, `reason`. All param descriptions use **notification-owned `notification_tool.*` i18n keys** (en/zh/wen). There is no `items` param and no `summarize` action — summarize lives on `system`.

## Connections

- `INTRINSICS["notification"]` (`src/lingtai/tools/registry.py:52`, inside the `INTRINSICS` dict at `src/lingtai/tools/registry.py:47` — the successor to the deleted kernel `intrinsics/__init__.py` `ALL_INTRINSICS`) → `BaseAgent._wire_intrinsics()` (`src/lingtai/kernel/base_agent/__init__.py:691`) binds `handle()` into every agent's tool surface. **Membership in `INTRINSICS` is the mandatory-include mechanism** — the wiring loop is unconditional, with no manifest gate, so this tool is always present like `system`.
- Delegates into the kernel-root `notifications.dismiss_channel` (`src/lingtai/kernel/notifications.py:560`). All #424 guards therefore hold through this tool by construction.
- The live-payload stamp is performed by `meta_block.attach_active_notifications`, called from `base_agent/turn.py`; see the kernel-root `ANATOMY.md` "Notifications" section.
- **`summarize` is not delegated here.** It stays on `system(action="summarize")` (`intrinsics/system/summarize.py`). The kernel no longer raises `large_tool_result` reminders — large results are ranked under `_meta.agent_meta.current_tool_result_chars` and digested via summarize. Legacy: a successful summarize still calls `notifications.clear_large_result_reminders` to auto-clear any leftover matching event, and notification dismiss verbs may also acknowledge/remove such an event as an escape hatch.

## Composition

- **Parent:** `src/lingtai/tools/` (see `src/lingtai/tools/ANATOMY.md`).
- **Siblings:** `system/` (owns `summarize` and the producer `publish_notification`/`clear_notification` entry points), `email/`, `soul/`, `psyche/`.

## State

- This intrinsic writes no state of its own. Through delegation it mutates `.notification/system.json` (event removal on dismiss) and clears `.notification/<channel>.json` files. Producer-owned canonical state (mailbox read-state, etc.) is never touched — mirror operations only clear the notification surface.

## Notes

- **Contract link:** notification tool verbs clear the high-attention hook/mirror, not producer source-of-truth state. Changes to dismiss/read semantics must check `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md`.
- **No `system` compatibility:** `system(action="notification"|"dismiss")` no longer exist. The notification tool is the sole agent-callable surface for these verbs. The kernel still *synthesizes* a notification delivery tool-call pair for IDLE/ASLEEP delivery — now shaped as `notification(action="check")` (`src/lingtai/kernel/base_agent/__init__.py:1688-1698`, inside `_inject_notification_pair` at `src/lingtai/kernel/base_agent/__init__.py:1461`), byte-shape-identical to a voluntary `check` so the LLM cannot tell a kernel-injected read from one it issued; the `_synthesized: true` body flag is the only marker. That synthesis is kernel plumbing, not an agent-callable operation.
- **Atomic, not aggregate:** dismissal is split by target (`channel` / `event_id` / `ref_id`) so the API states exactly what is being cleared. `dismiss_channel` refuses `event_id`/`ref_id`; `dismiss_event`/`dismiss_ref` require their target id.
- **Large-result escape hatch (legacy):** The kernel no longer produces `large_tool_result` reminders — large results are ranked under `_meta.agent_meta.current_tool_result_chars` and compacted via `system(action="summarize")`. Any `large_tool_result` event still present (persisted before this change, or pre-molt) can be discharged two ways: a successful `system(action="summarize")` of its `tool_call_id` clears it, or an atomic notification dismissal acknowledges and removes the reminder surface (including stale/pre-molt refs) without deleting or mutating the original tool result. Acknowledged refs persist in the ack store so they are not re-surfaced. Regression-anchored by `tests/test_notification_tool.py`, `tests/test_system_dismiss.py`, and `tests/test_large_result_rescan.py`.
