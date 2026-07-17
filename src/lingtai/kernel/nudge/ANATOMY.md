---
related_files:
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/nudge/__init__.py
  - src/lingtai/kernel/nudge/init_config.py
  - src/lingtai/kernel/nudge/goal.py
  - src/lingtai/kernel/nudge/kernel_version.py
  - src/lingtai/kernel/nudge/source_drift.py
  - src/lingtai/kernel/nudge/prompts.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/CONTRACT.md
  - tests/test_nudge_prompts.py
  - tests/test_kernel_version_nudge.py
  - src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# Nudge

Nudge is the kernel policy layer for periodic, read-only findings. It renders
producer facts, applies two global environment controls, and publishes through
the ordinary Notification Store channel; it does not create a second transport.

## Components

- `__init__.py` — `run_checks`, `upsert`, `remove`, `effective_policy`, and
  `record_dismissal` provide the shared policy, finding identity, dismissal mute,
  and `.notification/nudge.json` mutation (`src/lingtai/kernel/nudge/__init__.py:1-360`).
- `init_config.py` — consumes the last structured real-reader outcome and
  publishes/clears the typed configuration-shape finding; it never reads
  `init.json` independently (`src/lingtai/kernel/nudge/init_config.py:1-80`).
- `kernel_version.py` — read-only installed/running/package observation; it does
  not own a product repeat cadence (`src/lingtai/kernel/nudge/kernel_version.py:59-201`).
- `source_drift.py` — read-only runtime/source comparison, skipped for editable
  or source runtimes (`src/lingtai/kernel/nudge/source_drift.py:21-111`).
- `goal.py` — IDLE-only protected-goal reminder projected into the ordinary
  `system` notification channel (`src/lingtai/kernel/nudge/goal.py:20-75`).
- `prompts.py` — typed producer-fact to agent-facing payload renderer
  (`src/lingtai/kernel/nudge/prompts.py:17-125`).
- `notifications.py` — ordinary Notification transport invokes Nudge's dismissal
  policy hook before clearing the nudge channel (`src/lingtai/kernel/notifications.py:469-880`).

## Connections

`base_agent/lifecycle.py:_heartbeat_loop` calls `run_checks` once per heartbeat;
protected goal reminders are dispatched separately by
`run_system_notifications`. Producer checks call `upsert`/`remove`; the shared
`NotificationStorePort` persists `nudge.json`. `notification(action="dismiss_channel", channel="nudge")` remains
the only transport-facing dismissal path and calls `record_dismissal` so dismiss
means mute, not resolved. Effective config is reread on every Nudge operation;
invalid values fail safe to defaults and are diagnostic-only.

## Composition

Parent: `src/lingtai/kernel/ANATOMY.md`. The Nudge policy composes with the
ordinary notification Core; it does not own or duplicate Notification wire
injection. Detailed environment semantics route to
`src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md`.

## State

Persistent state is the shared `.notification/nudge.json` transport payload and
an internal `.notification/.nudge_state.json` dismissal map keyed by a stable
finding hash with an expiry. The latter stores no migration version, progress
chain, per-kind UTC date, or process cadence. Producer observation throttles are
bounded implementation cost only; global enabled/repeat values are product
semantics. Goal source state remains protected `.notification/goal.json` and its
reminder remains in `system.json`.

## Notes

Every emitted entry includes the effective `LINGTAI_NUDGE_ENABLED` and
`LINGTAI_NUDGE_REPEAT_INTERVAL` values, both names, and the environment catalogue
route. `FULLY_EFFECTIVE`, ignored-field, and failed init-reader outcomes are a
separate axis from Nudge action; a dismissed finding is not resolved until the
same real reader reports no finding. The old per-kind daily/fingerprint state is
not consulted for repeat behavior.
