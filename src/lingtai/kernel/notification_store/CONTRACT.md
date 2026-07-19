---
name: notification-store
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/notification_store/ANATOMY.md
  - src/lingtai/kernel/base_agent/CONTRACT.md
  - src/lingtai/kernel/notification_store/__init__.py
  - src/lingtai/adapters/posix/notification_store.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/server.py
  - tests/_notification_store_helpers.py
  - tests/test_notification_store.py
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
# Notification Store Contract

## Purpose

The Notification Store persists and observes current notification-channel
mirrors without owning notification policy. Core owns channel validation,
dismiss authority, stale decisions, envelopes, acknowledgement union/purge,
wake ordering, live-holder behavior, and model-visible lanes.

## Behavior

Runtime and coding agents MUST use the injected Port rather than construct
storage paths in Core. They MUST preserve the established external
`.notification/<channel>.json` protocol, treat non-force dismiss conflicts as
stale refusals, and retain unrelated current events during event/ref updates.
They MUST NOT add a nullable/no-op Store, Path-or-Port overload, locator, hidden
Core construction, eighth operation family, or caller-held transaction lock.

## Port

`NotificationStorePort` has exactly seven operation families:

1. `snapshot(allow_channel)`;
2. `fingerprint(allow_channel)`;
3. `publish(channel, payload)`;
4. `clear(channel) -> bool`;
5. `compare_update_channel(channel, expected_version, pure_core_mutator)`;
6. read-only `load_ack_refs() -> set[str]`;
7. `update_ack_refs(pure_core_set_mutator) -> UpdateAckRefsResult`.

`UNCONDITIONAL` is distinct from `None`: `None` means expected absence. A
fingerprint tuple means the exact delivered version. Channel mutators return
`(payload_or_none, changed, value)`; acknowledgement mutators return
`(set, changed, value)`. `CompareUpdateResult` exposes `applied`, `conflict`,
`changed`, `cleared`, `value`, `current_version`, and `previous_version`.

## Adapters

`PosixNotificationStoreAdapter` is the production adapter. One composed instance
owns in-process serialization for channel and acknowledgement mutations. Agent,
CLI, and Telegram server composition roots construct it and inject the Port.
External LICC/direct `mcp.*` producers keep the same filesystem path and envelope.

## Contract rules

- Snapshot and fingerprint skip missing, malformed, or unreadable entries and
  apply the live Core allow-predicate; fingerprints are sorted SHA-256 entries of
  filename, byte size, and bytes, not mtime.
- Publish is atomic sibling-temp replacement. Clear returns `False` only for
  absence; other clear and write errors propagate unless a Core best-effort
  wrapper explicitly preserves legacy suppression.
- Compare-update reads payload and version under Store serialization. Only
  `FileNotFoundError` is absence; every other read error propagates. Readable
  malformed/non-dict JSON retains its version and presents `{}` to Core, so it
  cannot satisfy expected absence.
- A compare conflict does not call the mutator and carries no policy value. A
  matched guard runs the mutator once. `changed=False` performs no write;
  `payload=None` clears; a dict publishes atomically. Operational result fields
  report the resulting version and actual clear outcome, while `value` carries
  all Core response/log policy evidence.
- Ack load preserves legacy best effort: absent, malformed, or unreadable state
  yields an empty set. Atomic ack update holds one Store lock across that same
  read, one pure Core set mutation, and store-or-clear. `changed=False` performs
  no write. Non-empty write failures propagate. Empty-set clear preserves legacy
  best effort by swallowing every unlink `OSError`; typed `changed/value`
  evidence still returns, with `changed=False` when no unlink succeeds.
- Core acknowledgement union and purge MUST use family 7, never split family 6
  read from a later write. System, nudge, and Telegram mutations decide from the
  current payload inside compare-update; force uses `UNCONDITIONAL`, while
  non-force dismiss uses the delivered fingerprint entry including explicit
  absence.

## Contract tests

Shared conformance covers the seven-family surface, expected absence versus
unconditional updates, malformed/unreadable/error behavior, typed policy values,
atomic concurrent channel updates, atomic acknowledgement union/purge, required
injection, outer composition, stale dismiss refusal, unrelated-event survival,
nudge updates, and Telegram current-mirror clearing. Production adapter tests
must use only an explicitly authorized persistent scratch path when deletion is
separately authorized.

## Maintenance

Read the paired Anatomy for locations and composition. Port, adapter, Core
callers, shared conformance tests, and this contract change together. Breaking
Port or semantic changes bump `contract_version`; implementation drift is a
defect, not permission to weaken this contract.
