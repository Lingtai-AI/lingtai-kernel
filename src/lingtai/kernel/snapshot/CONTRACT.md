---
name: snapshot-source-revision
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/snapshot/ANATOMY.md
  - src/lingtai/kernel/snapshot/__init__.py
  - src/lingtai/adapters/posix/git_cli.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/runtime_identity.py
  - src/lingtai/kernel/nudge/source_drift.py
  - src/lingtai/kernel/workdir.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - tests/_snapshot_helpers.py
  - tests/test_snapshot.py
  - tests/test_git_init.py
  - tests/test_heartbeat.py
  - tests/test_architecture_documents.py
  - tests/test_runtime_identity.py
  - tests/test_source_drift.py
  - tests/test_deep_refresh.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Snapshot + Source Revision

## Purpose

This component owns Core's two outbound capabilities for whole-workdir snapshots
and bounded read-only source revision inspection. It separates lifecycle and
identity policy from the fixed Git CLI mechanism.

## Behavior

Snapshot support remains opt-in. Initialization is idempotent, uses the fixed
agent identity and exact secret/signal exclusions, creates required system files
even when Git fails, and makes an initial baseline commit. Capture stages all
additions, changes, and deletions, returns `None` for a clean tree or operational
failure, otherwise commits a UTC-stamped snapshot and returns native-short HEAD.
Maintenance is best-effort and bounded to 60 seconds. Heartbeat first-eligible,
interval, daily-maintenance, and clock-advance semantics are unchanged.

Runtime identity requests a 12-character revision and tracked-only dirty
tri-state with 0.5-second deadlines. Source drift requests native-short revision
with a 2-second deadline while curated hashing and timestamp policy remain in
Core. Missing, failed, or timed-out revision queries return `None` without
crashing. Refresh watchers reuse parent-captured identity fields.

## Port

`SnapshotPort` exposes exactly `initialize()`, `snapshot() -> str | None`, and
`collect_garbage()`. `SourceRevisionPort` exposes exactly
`current_revision(short_length: int | None, timeout_seconds: float) -> str |
None` and `is_dirty(timeout_seconds: float) -> bool | None`. Abbreviation and
deadlines are caller policy. Dirty means tracked-file state; untracked files are
ignored. No argv, process/completed-process value, Git result object, or service
locator crosses either Port.

## Adapters

`PosixGitCliAdapter` in `src/lingtai/adapters/posix/git_cli.py` is the one
production adapter class implementing both Ports through fixed commands. Two
instances may target the agent workdir and running source directory. The shared
fake implementations live in `tests/_snapshot_helpers.py`.

## Contract rules

1. `BaseAgent` requires both capabilities and never imports or constructs the
   concrete adapter.
2. `Agent` and CLI are outer composition roots and inject distinct workdir and
   running-source adapter instances.
3. `WorkingDir` owns layout and manifest behavior only; all five historical Git
   methods, including zero-caller `diff` and `diff_and_commit`, are retired.
4. Lifecycle owns opt-in and cadence policy; runtime identity owns classification,
   fallback stamp, and cache policy; source drift owns digest/comparison/nudge
   policy.
5. Generated refresh-watcher code receives already-captured identity fields and
   performs no fresh adapter construction or source revision query.
6. Daemon worktree Git is a separate component and remains outside this boundary.

## Contract tests

`tests/test_snapshot.py` runs shared fake/production conformance and locks fixed
initialization, exact exclusions, stage-all capture, clean no-op, failure
translation, bounded maintenance, revision formatting, tracked-only dirty state,
and lifecycle cadence. `tests/test_runtime_identity.py`,
`tests/test_source_drift.py`, and `tests/test_deep_refresh.py` lock identity,
source digest, safe synthetic drift, and captured watcher identity handoff.
Architecture tests reject mechanism leakage into the Core Ports.

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes require
synchronized Ports, adapter, tests, and contract updates; structural or
composition changes also update the paired Anatomy and reciprocal parents.
