---
related_files:
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/nudge/__init__.py
  - src/lingtai/kernel/nudge/init_config.py
  - src/lingtai/kernel/nudge/goal.py
  - src/lingtai/kernel/nudge/kernel_version.py
  - src/lingtai/kernel/release_manifest.py
  - scripts/lib/release_manifest.py
  - src/lingtai/kernel/nudge/source_drift.py
  - src/lingtai/kernel/nudge/prompts.py
  - src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/workdir.py
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
  `upsert` also enforces the hard `INLINE_MAX_CHARS=10_000` inline-payload cap
  via `_cap_inline_payload`: at or below the cap the assembled entry (producer
  body plus shared policy fields and `kind`) is unchanged; above it, the full
  entry is written verbatim to a content-addressed sidecar file under
  `WorkdirLayout.nudge_findings_dir` (an ordinary agent temp path,
  `<working_dir>/tmp/nudge-findings/`) and the wire entry is replaced with a
  compact summary plus the sidecar's absolute path, exact char/byte counts,
  and SHA-256. `kind` is validated against a bounded filesystem-safe pattern
  before any file naming; an invalid `kind` or a sidecar write that does not
  durably succeed raises `NudgeExternalizationError` (bounded static message,
  never producer content) instead of ever persisting the oversized body or a
  compact placeholder — `upsert` calls this before its `.notification/nudge.json`
  mutation, so a raise leaves prior notification state untouched for retry on
  a later heartbeat (`src/lingtai/kernel/nudge/__init__.py:295-488`).
- `init_config.py` — consumes the last structured real-reader outcome and
  publishes/clears the typed configuration-shape finding; it never reads
  `init.json` independently (`src/lingtai/kernel/nudge/init_config.py:1-74`).
- `kernel_version.py` — read-only installed/running observation plus bounded
  GitHub/Gitee release-manifest comparison; it does not own a product repeat
  cadence (`src/lingtai/kernel/nudge/kernel_version.py:91-229`).
- `source_drift.py` — read-only runtime/source comparison, skipped for editable
  or source runtimes (`src/lingtai/kernel/nudge/source_drift.py:21-111`).
- `goal.py` — IDLE-only protected-goal reminder projected into the ordinary
  `system` notification channel, gated by an in-memory 10-second check
  cadence (`src/lingtai/kernel/nudge/goal.py:20-75`).
- `prompts.py` — typed producer-fact to agent-facing payload renderer, including
  installer and mirror-mismatch guidance (`src/lingtai/kernel/nudge/prompts.py:17-154`).
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

Findings that exceed `INLINE_MAX_CHARS` also persist their complete original
body to `<working_dir>/tmp/nudge-findings/<kind>-<sha256>.json`
(`WorkdirLayout.nudge_findings_dir`, `src/lingtai/kernel/workdir.py`) — the
ordinary agent temp namespace, not `.notification/`, matching the existing
`tmp/tool-results/` spill convention exactly. Owner-only (`0o700` dir, `0o600`
file) is (re-)enforced on every write, including when the directory
pre-existed with looser permissions, and the write is atomic
sibling-temp-then-replace. The filename is content-addressed by the SHA-256
of the exact persisted UTF-8 bytes, so re-upserting the same finding on a
later heartbeat reuses the file instead of writing a new one every cycle.
These sidecar files are local to one agent run and are not auto-cleaned.

## Notes

Every emitted entry includes the effective `LINGTAI_NUDGE_ENABLED` and
`LINGTAI_NUDGE_REPEAT_INTERVAL` values, both names, and the environment catalogue
route. `FULLY_EFFECTIVE`, ignored-field, and failed init-reader outcomes are a
separate axis from Nudge action; a dismissed finding is not resolved until the
same real reader reports no finding. The old per-kind daily/fingerprint state is
not consulted for repeat behavior.

The kernel-version producer reads only the exact
`lingtai-kernel-release-manifest.json` release asset from the GitHub/Gitee latest
release endpoints; it does not interpret release prose or package-index JSON.
When both manifests are usable, their version, manifest bytes, and declared
artifact-hash digest must agree before an update version is considered. A local
mismatch recommends refresh only when the installed PEP 440 version is
semantically newer than the running version; reverse, invalid, and unknown pairs
remain local diagnostics for interpreter/import-path inspection.

Dismissal-fingerprint identity is computed once in `upsert`, before capping,
from the full pre-cap entry facts (`title`/`detail`/`source`/etc.), then
stamped onto a capped entry as `_dismiss_fingerprint` so `record_dismissal`
recovers the same identity from the persisted compact shape rather than
re-deriving it from the rewritten compact `title`/`detail`. An uncapped small
entry never carries `_dismiss_fingerprint` — the wire shape for the common
case is unchanged from before the cap existed.

Externalization is fail-loud, not fail-open: an invalid `kind` (bounded
filesystem-safe pattern, checked before any file naming) or a sidecar write
that does not durably succeed raises `NudgeExternalizationError` from inside
`upsert`, before either persistent side effect of a successful upsert runs —
its `.notification/nudge.json` mutation and its `.notification/.nudge_state.json`
dismissal-clear (`_clear_dismissal`) both happen only after externalization
has completed (or determined no externalization is needed). The oversized
body is never persisted inline as a fallback, and no compact placeholder with
a null path is written either — prior state in both files is left completely
untouched so a later heartbeat can retry. The exception message is a bounded
static string; it never includes the producer's `kind`, title, detail, or any
finding content, so a caller that logs `str(exc)` cannot leak the oversized
body or an escape-heavy `kind`. `run_checks`' existing per-producer
`_run_one` try/except already catches, bounds (`str(e)[:200]`), and logs any
producer exception, so this raise surfaces as the existing
`nudge_check_error` diagnostic rather than crashing the heartbeat.
