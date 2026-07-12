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
  - src/lingtai/adapters/posix/mail.py
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

This narrow package contains production filesystem adapters for Core-owned Ports:
the structured event journal and mail transport. It is an implementation-only
Anatomy with no independent local Contract; for the Anatomy/Contract pairing rule
its unique owning component Contract is
`src/lingtai/kernel/event_journal/CONTRACT.md` (this Anatomy is listed only in
that Contract's `related_files`). Each adapter implements a Core Port rather than
defining a separate behavioral promise; the mail adapter's promises are owned by
`src/lingtai/kernel/mail_transport/CONTRACT.md`, which links the adapter code file
directly, and its Port structure is navigated via
`src/lingtai/kernel/mail_transport/ANATOMY.md`.

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

## Connections

The event-journal adapter depends inward on `EventJournalPort` and
`JournalPosition`, and on the existing logging primitives for byte serialization,
redaction, primary-first ordering, and SQLite fail-open behavior
(`src/lingtai/adapters/posix/event_journal.py:7-12`). The mail adapter depends
inward on `MailTransportPort`, kernel `handshake` liveness helpers, and the
kernel-owned `_new_mailbox_id`
(`src/lingtai/adapters/posix/mail.py:27-29`). Outer wrapper and CLI composition
roots inject both; Core never imports this package.

## Composition

- **Parent wrapper:** `src/lingtai/ANATOMY.md`.
- **Port components:** `src/lingtai/kernel/event_journal/ANATOMY.md` and
  `src/lingtai/kernel/mail_transport/ANATOMY.md`.
- **Storage primitives:** `src/lingtai/kernel/services/ANATOMY.md`.

## State

The event-journal adapter owns the open primary handle and derived-index
lifecycle through its composite
(`src/lingtai/adapters/posix/event_journal.py:24-36`); it writes
`logs/events.jsonl` and the rebuildable `logs/log.sqlite` sidecar. The mail
adapter owns the daemon poll thread and the in-memory `_seen` set, and writes
`mailbox/{inbox,outbox,sent}/<id>/message.json` plus `attachments/`
(`src/lingtai/adapters/posix/mail.py:67-69`).

## Notes

These are the only production adapters for their respective Ports. The package
contains no adapter registry, default factory, query surface, rebuild policy, or
network sink. The mail adapter is a faithful move of the former
`kernel/services/mail.py` mechanism; no concrete mail transport remains in Core.
