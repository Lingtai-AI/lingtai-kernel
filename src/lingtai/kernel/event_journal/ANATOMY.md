---
related_files:
  - src/lingtai/kernel/event_journal/CONTRACT.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/event_journal/__init__.py
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/services/ANATOMY.md
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/adapters/posix/event_journal.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
---
# Structured Event Journal Anatomy

This Core component owns the technology-neutral append boundary and authoritative
source-position value used by agent event producers.

## Components

- `JournalPosition` carries the authoritative source file and starting byte
  offset (`src/lingtai/kernel/event_journal/__init__.py:9-14`).
- `EventJournalPort` defines append and close, with no read-side operations
  (`src/lingtai/kernel/event_journal/__init__.py:17-26`).

## Connections

`BaseAgent` receives the Port and stores it as `_event_journal`
(`src/lingtai/kernel/base_agent/__init__.py:278-320`), appends fully assembled
runtime events through it (`src/lingtai/kernel/base_agent/__init__.py:938-946`),
and closes it best-effort after session and mail teardown but before final
liveness withdrawal (`src/lingtai/kernel/base_agent/lifecycle.py:266-290`).

The outer `Agent` wrapper constructs the POSIX adapter when callers omit the Port
(`src/lingtai/agent.py:115-141`). The CLI boot path injects that adapter explicitly
before later config hydration (`src/lingtai/cli.py:125-141`).

## Composition

- **Parent:** `src/lingtai/kernel/ANATOMY.md`.
- **Production adapter:** `src/lingtai/adapters/posix/ANATOMY.md`.
- **Core consumer:** `src/lingtai/kernel/base_agent/ANATOMY.md`.

## State

The Core Port and position value own no storage. Persistent JSONL and SQLite state
is owned by the POSIX adapter and its existing logging primitives.

## Notes

Query and rebuild do not pass through this component; they remain read-side
operations in `kernel/services/logging.py`.
