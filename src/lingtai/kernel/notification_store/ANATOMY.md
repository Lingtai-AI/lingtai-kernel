---
related_files:
  - src/lingtai/kernel/notification_store/CONTRACT.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/notification_store/__init__.py
  - src/lingtai/adapters/posix/notification_store.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - src/lingtai/mcp_servers/telegram/server.py
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

- `NotificationStorePort` defines exactly seven persistence families, with
  specialized pure channel and acknowledgement mutators
  (`src/lingtai/kernel/notification_store/__init__.py:115-197`).
- `CompareUpdateResult` and `UpdateAckRefsResult` carry typed operational and
  policy evidence (`src/lingtai/kernel/notification_store/__init__.py:59-75`).
- `PosixNotificationStoreAdapter` maps the Port onto the established
  `.notification/` layout and owns mutation serialization
  (`src/lingtai/adapters/posix/notification_store.py:57-240`).
- Notification Core owns channel policy, atomic acknowledgement union/purge, and
  current-payload dismiss decisions (`src/lingtai/kernel/notifications.py:129-186`,
  `src/lingtai/kernel/notifications.py:297-312`,
  `src/lingtai/kernel/notifications.py:475-860`).

## Connections

`BaseAgent` receives the Port as a required constructor dependency and uses it
for sync and delivery (`src/lingtai/kernel/base_agent/__init__.py:304-368`).
`Agent` and the CLI construct the POSIX adapter at outer composition roots
(`src/lingtai/agent.py:142-151`, `src/lingtai/cli.py:127-140`). The Telegram MCP
server separately composes one adapter for its manager
(`src/lingtai/mcp_servers/telegram/server.py:655-663`).

## Composition

The parent map is `src/lingtai/kernel/ANATOMY.md`; the paired normative interface
is `src/lingtai/kernel/notification_store/CONTRACT.md`. Core depends inward on
the Port. The POSIX adapter depends on that Port, and outer roots inject it.

## State

Persistent state is the existing `.notification/<channel>.json` protocol plus
`.notification/large_result_acks.json`. The POSIX adapter holds only its workdir
and an in-process mutation lock (`src/lingtai/adapters/posix/notification_store.py:63-66`).
Core retains delivered fingerprints and policy state on the agent, not in the
adapter.

## Notes

The Store does not own channel allowlists, dismiss authority, envelope shape,
wake ordering, or producer semantics. Direct external `mcp.*` filesystem
producers retain the established path and envelope; the Store is not a generic
filesystem, KV, or service-locator abstraction.
