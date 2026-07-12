---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/kernel/event_journal/ANATOMY.md
  - src/lingtai/kernel/event_journal/CONTRACT.md
  - src/lingtai/kernel/services/ANATOMY.md
  - src/lingtai/adapters/posix/__init__.py
  - src/lingtai/adapters/posix/event_journal.py
  - src/lingtai/kernel/services/logging.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
---
# POSIX Adapter Anatomy

This narrow package contains the production filesystem adapter for the Core-owned
structured event journal Port.

## Components

- `PosixJsonlEventJournalAdapter` constructs the existing JSONL primary and
  SQLite sidecar primitives under `<working_dir>/logs/`
  (`src/lingtai/adapters/posix/event_journal.py:15-36`).
- `append()` delegates the ordered/redacted durable write and translates storage
  metadata into `JournalPosition`
  (`src/lingtai/adapters/posix/event_journal.py:38-45`).
- `close()` delegates resource release to the composed logging service
  (`src/lingtai/adapters/posix/event_journal.py:47-48`).

## Connections

The adapter depends inward on `EventJournalPort` and `JournalPosition`, and on the
existing logging primitives for byte serialization, redaction, primary-first
ordering, and SQLite fail-open behavior
(`src/lingtai/adapters/posix/event_journal.py:7-12`). Outer wrapper and CLI
composition roots inject it; Core never imports this package.

## Composition

- **Parent wrapper:** `src/lingtai/ANATOMY.md`.
- **Port component:** `src/lingtai/kernel/event_journal/ANATOMY.md`.
- **Storage primitives:** `src/lingtai/kernel/services/ANATOMY.md`.

## State

The adapter owns the open primary handle and derived-index lifecycle through its
composite (`src/lingtai/adapters/posix/event_journal.py:24-36`). It writes
`logs/events.jsonl` and the rebuildable `logs/log.sqlite` sidecar.

## Notes

This is the only production event-journal adapter. The package contains no
adapter registry, default factory, query surface, rebuild policy, or network sink.
