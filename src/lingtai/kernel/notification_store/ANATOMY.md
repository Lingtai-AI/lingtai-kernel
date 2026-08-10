---
related_files:
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
  - tests/test_notification_store.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Notification Store Anatomy

The Notification Store is the Core-owned persistence boundary for current
`.notification/<channel>.json` mirrors and legacy large-result acknowledgements.

## Components

- `NotificationStorePort` defines exactly eight persistence families, with
  specialized pure channel, acknowledgement, and hook-manifest mutators
  (`src/lingtai/kernel/notification_store/__init__.py:115-254`). Family 8 (the
  hook registry) pairs read-only `load_hook_manifests` with atomic
  `update_hook_manifests` and adds read-only `stat_hook_registry() ->
  tuple[int, int] | None` — the cheap `(st_mtime_ns, st_size)` staleness
  fingerprint Core uses for out-of-band re-seed
  (`src/lingtai/kernel/notification_store/__init__.py:221-254`).
- `STORE_RESERVED_NON_CHANNEL_STEMS` is the Store-owned frozenset
  `{"hooks", "large_result_acks"}` naming registry / acknowledgement filenames
  that are never channels; Core validation rejects them as hook channels
  (`src/lingtai/kernel/notification_store/__init__.py:43-49`).
- `CompareUpdateResult`, `UpdateAckRefsResult`, and `UpdateHookManifestsResult`
  carry typed operational and policy evidence
  (`src/lingtai/kernel/notification_store/__init__.py:66-90`).
- `NotificationMutationLockPort` is the Store-private cross-process transaction
  seam (`src/lingtai/kernel/notification_store/_mutation_lock.py:1-13`). Its
  platform selector composes native POSIX and Windows implementations
  (`src/lingtai/adapters/notification_store_lock.py:1-28`).
- `PosixNotificationStoreAdapter` maps the Port onto the established
  `.notification/` layout and owns both in-process and native cross-process
  mutation serialization (`src/lingtai/adapters/posix/notification_store.py:69-310`).
- Notification Core owns channel policy, atomic acknowledgement union/purge, and
  current-payload dismiss decisions (`src/lingtai/kernel/notifications.py:129-186`,
  `src/lingtai/kernel/notifications.py:297-312`,
  `src/lingtai/kernel/notifications.py:475-860`).

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
depends inward on both, composes the platform-selected POSIX or Windows lock,
and outer roots inject the Store Port.

## State

Persistent protocol state is the existing `.notification/<channel>.json`, the
acknowledgement registry `.notification/large_result_acks.json`, and the
hook-manifest registry `.notification/hooks.json` (a single non-channel file,
invisible to snapshot/fingerprint; its `(st_mtime_ns, st_size)` stat is the
cheap staleness fingerprint Core consults for out-of-band re-seed). The
Store-owned non-channel stems (`hooks`, `large_result_acks`) are never channels:
adapters skip them in snapshot/fingerprint and Core validation rejects them as
hook channels. The adapter holds its workdir, an
in-process mutex, and a platform-selected mutation lock. Native adapters lock
`.notification/.store.lock` using `flock` on POSIX or byte 0 on Windows
(`src/lingtai/adapters/posix/notification_store_lock.py:1-29`,
`src/lingtai/adapters/windows/notification_store_lock.py:1-48`). Lock-file
existence is not authority; OS lock ownership serializes complete mutation
transactions and releases on process death. Core retains delivered fingerprints
and policy state on the agent, not in the adapter.

## Notes

The Store does not own channel allowlists, dismiss authority, envelope shape,
wake ordering, or producer semantics. Direct external `mcp.*` filesystem
producers retain the established path and envelope; the Store is not a generic
filesystem, KV, or service-locator abstraction.
