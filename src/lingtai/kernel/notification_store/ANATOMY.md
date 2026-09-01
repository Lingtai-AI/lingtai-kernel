---
related_files:
  - src/lingtai/kernel/notification_store/BEHAVIORS.md
  - src/lingtai/kernel/notification_store/CONTRACT.md
  - src/lingtai/kernel/ANATOMY.md
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
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/tools/daemon/supervisor_runtime.py
  - src/lingtai/kernel/refresh_watcher/watcher_program.py
  - tests/test_notification_delay_alarm.py
  - tests/test_notification_store.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# Notification Store Anatomy

The Notification Store is the Core-owned persistence boundary for current
`.notification/<channel>.json` mirrors and legacy large-result acknowledgements.

## Components

- `NotificationStorePort` defines exactly eight persistence families plus the
  read-only composed `mutation_lock` Port used only by Core's private delay
  state/alarm transaction, with
  specialized pure channel, acknowledgement, and hook-manifest mutators
  (`src/lingtai/kernel/notification_store/__init__.py:139-274`). Family 8 (the
  hook registry) pairs read-only `load_hook_manifests` with atomic
  `update_hook_manifests` and adds read-only `stat_hook_registry() ->
  tuple[int, int] | None` — the cheap `(st_mtime_ns, st_size)` staleness
  fingerprint Core uses for out-of-band re-seed
  (`src/lingtai/kernel/notification_store/__init__.py:238-274`).
- `STORE_RESERVED_NON_CHANNEL_STEMS` is the Store-owned frozenset
  `{"hooks", "large_result_acks"}` naming registry / acknowledgement filenames
  that are never channels; Core validation rejects them as hook channels
  (`src/lingtai/kernel/notification_store/__init__.py:43-49`).
- `CompareUpdateResult`, `UpdateAckRefsResult`, and `UpdateHookManifestsResult`
  carry typed operational and policy evidence
  (`src/lingtai/kernel/notification_store/__init__.py:66-90`).
- `NotificationMutationLockPort` is the Store-private resource transaction
  seam (`src/lingtai/kernel/notification_store/_mutation_lock.py:54-59`). Its
  scope helpers map channel, daemon-run, daemon-control, ack, hook, and delay
  resources to bounded sanitized-plus-SHA-256 filenames under `.locks/`, while
  the Core-owned `exclusive_notification_mutation` coordinator adds a
  process-wide RLock by canonical path before the injected native lock, closing
  `flock`'s same-process/open-file gap
  (`src/lingtai/kernel/notification_store/_mutation_lock.py:62-93`).
- `PosixNotificationStoreAdapter` maps the Port onto the established
  `.notification/` layout and owns resource-scoped serialization
  (`src/lingtai/adapters/posix/notification_store.py`). Non-daemon channels
  remain root mirrors; daemon writes use locked
  `.notification/daemon/<daemon-id>.json` mini-channels plus durable
  `.notification/daemon/.tombstone` control state. Owner append holds its run
  plus control scope but does not scan an aggregate or rebuild a report;
  snapshot/fingerprint derive the aggregate only for their read. The sibling
  `.notification/daemon.json` is a non-authoritative compatibility report, not
  an event file.
- Notification Core owns channel policy, atomic acknowledgement union/purge, and
  current-payload dismiss decisions (`src/lingtai/kernel/notifications.py:122-219`,
  `src/lingtai/kernel/notifications.py:704-788`,
  `src/lingtai/kernel/notifications.py:923-1341`).

## Connections

`BaseAgent` receives the Port as a required constructor dependency and uses it
for sync and delivery (`src/lingtai/kernel/base_agent/__init__.py:304-368`).
`Agent` and the CLI construct the filesystem adapter at outer composition roots
(`src/lingtai/agent.py:142-151`, `src/lingtai/cli.py:127-140`). The Telegram MCP
server and each detached daemon supervisor separately compose Store instances
(`src/lingtai/mcp_servers/telegram/server.py:655-663`,
`src/lingtai/tools/daemon/supervisor_runtime.py:600-620`). Those independent
processes share the native mutation lock for each complete transaction.

## Composition

The parent map is `src/lingtai/kernel/ANATOMY.md`; the paired normative interface
is `src/lingtai/kernel/notification_store/CONTRACT.md`. The Store and
mutation-lock Port definitions are Core-owned. The filesystem Store adapter
depends inward on both, composes and exposes the platform-selected POSIX or
Windows lock through the Store Port, and outer roots inject the Store.

## State

Persistent protocol state is the existing `.notification/<channel>.json` for
all non-daemon channels; independent daemon mini-channels
`.notification/daemon/<daemon-id>.json`; durable daemon control/tombstones at
`.notification/daemon/.tombstone`; the non-authoritative compatibility report
`.notification/daemon.json`; the acknowledgement registry
`.notification/large_result_acks.json`; and the hook-manifest registry
`.notification/hooks.json` (a single non-channel file, invisible to
snapshot/fingerprint; its `(st_mtime_ns, st_size)` stat is the cheap staleness
fingerprint Core consults for out-of-band re-seed). The control record contains
aggregate visibility cuts, batch/alarm state, and a process-crash-safe pending
append receipt so a crash between receipt and mini-file/write compaction has a
deterministic logical projection. Committed clear/dismiss cuts fsync before
compaction. It is validated on every daemon aggregate read; corruption becomes
a bounded high-priority daemon control-error projection rather than an empty
channel or an outage of unrelated channels, while daemon mutation paths refuse.
The Store-owned
non-channel stems (`hooks`, `large_result_acks`) are never channels: adapters
skip them in snapshot/fingerprint and Core validation rejects them as hook
channels. The daemon aggregate fingerprint changes for logical mini-file
additions, removals, and same-file appends; the root report never participates
in that fingerprint. Channel/event/ref dismissals use aggregate CAS, tombstone
only selected event keys, commit a visibility cut, and only then compact
mini-files best effort.

The adapter holds its workdir, a process-wide per-canonical-lock-path RLock,
and a platform-selected native resource lock. POSIX takes shared
`.notification/.store.lock` for one release with exclusive scoped
`.notification/.locks/<bounded-label>-<sha20>.lock`; legacy writers remain
exclusive on `.store.lock`. Windows uses only scoped byte-range locks and
requires quiesced old-writer cutover. Neither live lock filename is deleted.
The refresh watcher's generated terminal publisher computes the same `system`
scope filename and uses POSIX shared legacy + scoped exclusive locking before
merging `refresh_failed_permanent` into `system.json`, with bounded fail-open
behavior so a wedged holder does not drop the alert. Lock-file existence is not
authority; OS lock ownership serializes complete resource transactions and
releases on process death. Core retains delivered fingerprints and policy state
on the agent, not in the adapter.

## Notes

The Store does not own channel allowlists, dismiss authority, envelope shape,
wake ordering, or producer semantics. Direct external `mcp.*` filesystem
producers retain the established path and envelope; the Store is not a generic
filesystem, KV, or service-locator abstraction.
