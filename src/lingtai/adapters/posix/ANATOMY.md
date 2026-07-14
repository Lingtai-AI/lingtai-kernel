---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/kernel/event_journal/ANATOMY.md
  - src/lingtai/kernel/event_journal/CONTRACT.md
  - src/lingtai/kernel/mail_transport/ANATOMY.md
  - src/lingtai/kernel/services/ANATOMY.md
  - src/lingtai/services/ANATOMY.md
  - src/lingtai/adapters/posix/__init__.py
  - src/lingtai/adapters/posix/event_journal.py
  - src/lingtai/adapters/posix/git_cli.py
  - src/lingtai/adapters/posix/mail.py
  - src/lingtai/adapters/posix/workdir_lease.py
  - src/lingtai/adapters/posix/refresh_watcher.py
  - src/lingtai/adapters/posix/notification_store.py
  - src/lingtai/adapters/posix/agent_presence.py
  - src/lingtai/adapters/posix/migration_workspace.py
  - src/lingtai/kernel/agent_presence/ANATOMY.md
  - src/lingtai/kernel/workdir_lease/ANATOMY.md
  - src/lingtai/kernel/workdir_lease/CONTRACT.md
  - src/lingtai/kernel/snapshot/ANATOMY.md
  - src/lingtai/kernel/snapshot/CONTRACT.md
  - src/lingtai/kernel/migrate/ANATOMY.md
  - src/lingtai/kernel/migrate/CONTRACT.md
  - src/lingtai/kernel/services/logging.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# POSIX Adapter Anatomy

This narrow package contains production filesystem and process adapters for
Core-owned Ports: the structured event journal, mail transport, notification
store, workdir lease, refresh watcher, agent presence, the fixed-command
snapshot/source-revision Git capability, and the migration workspace. It is an
implementation-only Anatomy with no independent local Contract; for the
Anatomy/Contract pairing rule its unique owning component Contract is
`src/lingtai/kernel/event_journal/CONTRACT.md` (this Anatomy is listed only in
that Contract's `related_files`). Each adapter implements a Core Port rather than
defining a separate behavioral promise; the mail adapter's promises are owned by
`src/lingtai/kernel/mail_transport/CONTRACT.md`, the workdir-lease adapter's by
`src/lingtai/kernel/workdir_lease/CONTRACT.md`, the refresh-watcher adapter's by
`src/lingtai/kernel/refresh_watcher/CONTRACT.md`, and the notification-store
adapter's by `src/lingtai/kernel/notification_store/CONTRACT.md`, each of which
links its adapter code file directly. Port structure is navigated via the
co-located ANATOMY.md files for each component.

## Components

- `PosixJsonlEventJournalAdapter` constructs the existing JSONL primary and
  SQLite sidecar primitives under `<working_dir>/logs/`
  (`src/lingtai/adapters/posix/event_journal.py:15-36`).
- `append()` delegates the ordered/redacted durable write and translates storage
  metadata into `JournalPosition`
  (`src/lingtai/adapters/posix/event_journal.py:38-45`).
- `close()` delegates resource release to the composed logging service
  (`src/lingtai/adapters/posix/event_journal.py:47-48`).
- `PosixFilesystemMailAdapter` implements `MailTransportPort` by delivering
  messages as files into a recipient's inbox and polling its own inbox plus
  subscribed pseudo-agent outboxes (`src/lingtai/adapters/posix/mail.py:34-69`).
- `send()` handshakes, injects mailbox metadata, copies attachments, and writes
  `message.json` atomically (`src/lingtai/adapters/posix/mail.py:84-162`);
  `listen()`/`stop()` own the 0.5-second daemon poll loop with pseudo-outbox
  priority and per-phase `OSError` isolation
  (`src/lingtai/adapters/posix/mail.py:168-219`, `src/lingtai/adapters/posix/mail.py:425-430`).
- `PosixWorkdirLeaseAdapter` implements `WorkdirLeasePort` by holding an exclusive
  non-blocking `fcntl.flock` on `<workdir>/.agent.lock`
  (`src/lingtai/adapters/posix/workdir_lease.py:27-95`); `acquire()` polls at
  250 ms to a monotonic deadline and raises the exact contention `RuntimeError`,
  `release()` unlocks then guarantees the handle is closed in a `finally` (even if
  the explicit `LOCK_UN` raises) before a best-effort unlink, swallows the
  specified `OSError`s, resets its handle, and is idempotent.
- `PosixRefreshWatcherAdapter` implements `RefreshWatcherPort` by rendering
  the Core-owned watcher program text from a `RefreshWatcherRequest`
  (`watcher_program.render_watcher_script`), building the process environment
  via its own `build_watcher_env` (`src/lingtai/adapters/posix/refresh_watcher.py:30-40`:
  `os.environ` capture plus `LINGTAI_REFRESH_ENV_OVERWRITE=1` when
  `request.env_overwrite`), and launching `[sys.executable, "-c", script]` via
  `subprocess.Popen` with all three standard streams set to `DEVNULL` and
  `start_new_session=True`
  (`src/lingtai/adapters/posix/refresh_watcher.py:54-64`); the call returns
  once the process has been started and does not wait for or track it.
- `PosixGitCliAdapter` implements both `SnapshotPort` and `SourceRevisionPort`
  through fixed Git command families. Separate composed instances target the
  agent workdir and running source; no arbitrary argv/process/result object is
  exposed.
- `PosixNotificationStoreAdapter` implements all seven `NotificationStorePort`
  families on `.notification/<channel>.json`, including typed compare-update and
  atomic acknowledgement-set mutation
  (`src/lingtai/adapters/posix/notification_store.py:57-240`). Its internal lock
  spans each complete mutation; atomic writes use the shared `_fsutil` primitive.
- `PosixAgentPresenceStoreAdapter` implements all four `AgentPresenceStorePort`
  operations on one working directory's `.agent.json` / `.agent.heartbeat`
  (`src/lingtai/adapters/posix/agent_presence.py`): tri-state manifest/heartbeat
  observation, byte-exact `str(wall_seconds)`-no-newline heartbeat publication,
  and best-effort idempotent withdrawal. Bound to a `WorkdirLayout` at
  construction; holds no long-lived handle or lock.
- `PosixMigrationWorkspaceAdapter`
  (`src/lingtai/adapters/posix/migration_workspace.py`) implements all seven
  `MigrationWorkspacePort` families for one bound `MigrationDomain`/root: availability,
  entry→path mapping, raw reads, preset enumeration, PID-suffixed atomic replace
  (every replacement, incl. preset m001/m002), version files, `system/migrations/`
  archive + SHA-256 evidence, and best-effort `logs/events.jsonl` audit.

## Connections

The event-journal adapter depends inward on `EventJournalPort` and
`JournalPosition`, and on the existing logging primitives for byte serialization,
redaction, primary-first ordering, and SQLite fail-open behavior
(`src/lingtai/adapters/posix/event_journal.py:7-12`). The mail adapter depends
inward on `MailTransportPort`, Core `agent_presence` liveness policy,
`handshake.resolve_address`, and the kernel-owned `_new_mailbox_id`
(`src/lingtai/adapters/posix/mail.py:27-33`). The workdir-lease adapter depends
inward on the kernel-owned `workdir_layout` for the `.agent.lock` path and on
`WorkdirLeasePort` (`src/lingtai/adapters/posix/workdir_lease.py:23-24`). The
notification-store adapter depends inward on `NotificationStorePort` and the
kernel `_fsutil.atomic_write_json` helper
(`src/lingtai/adapters/posix/notification_store.py:13-25`). The migration-workspace
adapter depends inward on `MigrationWorkspacePort` and the migrate value objects and
reuses the Core `meta_filename()` name. It is imported by
explicit composition modules, not exported from the package facade. Agent, CLI,
and Telegram-server roots construct these adapters; the CLI `load_init`, the
wrapper `load_preset` / `_run_preset_library_migrations`, and `Agent._read_init`
build a domain/root-bound `PosixMigrationWorkspaceAdapter` for the Core runners.
Core never imports this package.

## Composition

- **Parent wrapper:** `src/lingtai/ANATOMY.md`.
- **Port components:** `src/lingtai/kernel/event_journal/ANATOMY.md`,
  `src/lingtai/kernel/mail_transport/ANATOMY.md`,
  `src/lingtai/kernel/workdir_lease/ANATOMY.md`,
  `src/lingtai/kernel/snapshot/ANATOMY.md`,
  `src/lingtai/kernel/notification_store/ANATOMY.md`,
  `src/lingtai/kernel/agent_presence/ANATOMY.md`, and
  `src/lingtai/kernel/migrate/ANATOMY.md`.
- **Storage primitives:** `src/lingtai/kernel/services/ANATOMY.md`.

## State

The event-journal adapter owns the open primary handle and derived-index
lifecycle through its composite
(`src/lingtai/adapters/posix/event_journal.py:24-36`); it writes
`logs/events.jsonl` and the rebuildable `logs/log.sqlite` sidecar. The mail
adapter owns the daemon poll thread and the in-memory `_seen` set, and writes
`mailbox/{inbox,outbox,sent}/<id>/message.json` plus `attachments/`
(`src/lingtai/adapters/posix/mail.py:67-69`). The workdir-lease adapter owns the
open `.agent.lock` file handle while the lease is held; release resets adapter
state, attempts unlock and close, and unlinks only after closure is confirmed so
an uncertain live descriptor cannot create split-inode authority
(`src/lingtai/adapters/posix/workdir_lease.py:38-96`). The notification-store
adapter owns the internal `threading.Lock` and the workdir path, and writes
`.notification/<channel>.json` plus `.notification/large_result_acks.json`
(`src/lingtai/adapters/posix/notification_store.py:63-66`, `src/lingtai/adapters/posix/notification_store.py:210-240`).
The migration-workspace adapter owns only its bound `(domain, root)` pair and
writes the domain's `_kernel_meta.json` version file, `system/migrations/` archive
artifacts, and best-effort `logs/events.jsonl` audit through PID-suffixed temp + replace; it holds no long-lived handle or lock.

## Notes

These are the only production adapters for their respective Ports. The package
contains no adapter registry, default factory, query surface, rebuild policy, or
network sink. Notification channel and acknowledgement transaction locks are
Store-owned; no concrete notification persistence remains in Core. The mail
adapter is a faithful move of the former
`kernel/services/mail.py` mechanism; no concrete mail transport remains in Core.
The workdir-lease adapter is a faithful move of the former
`WorkingDir.acquire_lock`/`release_lock` flock mechanism; no concrete lock
authority remains in Core, and platform selection with fail-loud unsupported
handling lives in `src/lingtai/adapters/workdir_lease.py`.
