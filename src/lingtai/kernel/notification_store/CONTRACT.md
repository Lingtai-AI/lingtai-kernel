---
name: notification-store
contract_version: 3
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/notification_store/ANATOMY.md
  - src/lingtai/kernel/base_agent/CONTRACT.md
  - src/lingtai/kernel/notification_store/__init__.py
  - src/lingtai/kernel/notification_store/_mutation_lock.py
  - src/lingtai/adapters/notification_store_lock.py
  - src/lingtai/adapters/posix/notification_store.py
  - src/lingtai/adapters/posix/notification_store_lock.py
  - src/lingtai/adapters/windows/notification_store_lock.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/tools/daemon/supervisor_runtime.py
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
Core construction, ninth operation family, or caller-held transaction lock.

## Port

`NotificationStorePort` has exactly eight operation families:

1. `snapshot(allow_channel)`;
2. `fingerprint(allow_channel)`;
3. `publish(channel, payload)`;
4. `clear(channel) -> bool`;
5. `compare_update_channel(channel, expected_version, pure_core_mutator)`;
6. read-only `load_ack_refs() -> set[str]`;
7. `update_ack_refs(pure_core_set_mutator) -> UpdateAckRefsResult`;
8. read-only `load_hook_manifests() -> list[dict]`,
   `update_hook_manifests(pure_core_manifest_mutator) -> UpdateHookManifestsResult`,
   and read-only `stat_hook_registry() -> tuple[int, int] | None`.

`UNCONDITIONAL` is distinct from `None`: `None` means expected absence. A
fingerprint tuple means the exact delivered version. Channel mutators return
`(payload_or_none, changed, value)`; acknowledgement mutators return
`(set, changed, value)`; hook-manifest mutators return
`(list[dict], changed, value)`. `CompareUpdateResult` exposes `applied`,
`conflict`, `changed`, `cleared`, `value`, `current_version`, and
`previous_version`.

## Adapters

`PosixNotificationStoreAdapter` is the production filesystem adapter. Each
instance owns an in-process mutex and composes the selected native
`NotificationMutationLockPort`; together they serialize channel, acknowledgement,
and hook-manifest mutations across threads and independently composed processes.
The selector provides `flock` on POSIX and byte-range locking on Windows. Agent,
CLI, daemon supervisor, and Telegram server composition roots construct the Store
adapter. External LICC/direct `mcp.*` producers keep the same filesystem path and
envelope.

## Contract rules

- Snapshot and fingerprint skip missing, malformed, or unreadable entries and
  apply the live Core allow-predicate; fingerprints are sorted SHA-256 entries of
  filename, byte size, and bytes, not mtime.
- Publish is atomic sibling-temp replacement. Publish and clear hold the Store's
  in-process and cross-process mutation locks. Clear returns `False` only for
  absence; other clear and write errors propagate unless a Core best-effort
  wrapper explicitly preserves legacy suppression.
- Compare-update reads payload and version under the same whole-transaction
  cross-process Store serialization. Only
  `FileNotFoundError` is absence; every other read error propagates. Readable
  malformed/non-dict JSON retains its version and presents `{}` to Core, so it
  cannot satisfy expected absence.
- A compare conflict does not call the mutator and carries no policy value. A
  matched guard runs the mutator once. `changed=False` performs no write;
  `payload=None` clears; a dict publishes atomically. Operational result fields
  report the resulting version and actual clear outcome, while `value` carries
  all Core response/log policy evidence.
- Ack load preserves legacy best effort: absent, malformed, or unreadable state
  yields an empty set. Atomic ack update holds the same in-process and
  cross-process Store locks across that read, one pure Core set mutation, and
  store-or-clear. `changed=False` performs
  no write. Non-empty write failures propagate. Empty-set clear preserves legacy
  best effort by swallowing every unlink `OSError`; typed `changed/value`
  evidence still returns, with `changed=False` when no unlink succeeds.
- Hook-manifest load distinguishes an absent registry from a corrupt or
  unreadable one: an absent `hooks.json` yields an empty list; an invalid-JSON
  or unreadable registry raises, which the tool layer surfaces as a structured
  `hook_registry_load_failed` error so "registry broken" is never reported as
  "nothing registered". Atomic hook-manifest update
  holds the same in-process and cross-process Store locks across that read, one
  pure Core list mutation, and store-or-clear. `changed=False` performs no
  write. Non-empty write failures propagate. Empty-list clear preserves legacy
  best effort by swallowing every unlink `OSError`; typed `changed/value`
  evidence still returns, with `changed=False` when no unlink succeeds.
- `stat_hook_registry()` returns the cheap `(st_mtime_ns, st_size)` staleness
  fingerprint of the hook-registry file, or `None` when absent. Core uses it to
  re-seed its in-memory hook mirror when another process (sibling CLI, Telegram
  server, hook installer) wrote `hooks.json` out-of-band, without re-reading the
  file on every sync tick.
- `STORE_RESERVED_NON_CHANNEL_STEMS` is `{"hooks", "large_result_acks"}`: the
  Store-owned registry and acknowledgement files are never channels. Core
  validation rejects these stems as hook channels, so a registered hook can
  never publish over or clear Store-owned files. Adapters MUST keep their
  snapshot/fingerprint skip lists in sync with this set.
- Core hook add/edit/drop MUST use family 8, never split family 8's read from a
  later write. The registry file `.notification/hooks.json` is a single
  non-channel registry, invisible to snapshot/fingerprint and to the allow
  predicate except through Core's registered-hook mirror.
- Core acknowledgement union and purge MUST use family 7, never split family 6
  read from a later write. System, nudge, Telegram, and daemon-terminal mutations
  decide from the current payload inside compare-update; force uses
  `UNCONDITIONAL`, while non-force dismiss uses the delivered fingerprint entry
  including explicit absence.
- `.notification/.store.lock` is coordination metadata, not notification state or
  authority. POSIX and Windows adapters MUST use native OS locks whose ownership,
  not file existence, defines exclusion and whose release follows process death.
  Snapshot and fingerprint continue to expose only allowed JSON channel files.

## Contract tests

Shared conformance covers the eight-family surface, expected absence versus
unconditional updates, malformed/unreadable/error behavior, typed policy values,
atomic same-process and spawned-process channel updates, atomic acknowledgement
union/purge, atomic hook-manifest append/clear, required injection, outer
composition, stale dismiss refusal,
unrelated-event survival,
nudge updates, and Telegram current-mirror clearing. Production adapter tests
must use only an explicitly authorized persistent scratch path when deletion is
separately authorized.

## Maintenance

Read the paired Anatomy for locations and composition. Port, adapter, Core
callers, shared conformance tests, and this contract change together. Breaking
Port or semantic changes bump `contract_version`; implementation drift is a
defect, not permission to weaken this contract.
