---
name: structured-event-journal
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/event_journal/ANATOMY.md
  - src/lingtai/kernel/event_journal/__init__.py
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/adapters/posix/event_journal.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/services/logging.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - tests/test_event_journal.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v1 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Structured Event Journal

## Purpose

The structured event journal is Core's outbound append-only boundary for durable
agent events. It separates event production and lifecycle from the current POSIX
JSONL and SQLite storage mechanisms.

## Behavior

Agents and coding agents MUST preserve the authoritative JSONL-first ordering,
redaction-before-storage guarantee, returned byte provenance, and fail-open
derived-index behavior. They MUST NOT infer query, rebuild, network-sink, schema,
or adapter-selection authority from this Port. A raw `BaseAgent` without an
injected journal has journaling disabled and MUST NOT construct storage implicitly.

## Port

`EventJournalPort.append(event) -> JournalPosition | None` appends one structured
event. A successful authoritative append returns `JournalPosition(source_file:
str, source_offset: int)` identifying the exact JSONL file and starting byte
offset. `None` means no authoritative append occurred, including after close.
Primary append failures propagate. `close()` releases resources and is idempotent.

The Port owns append and close only. Query and rebuild are explicitly outside its
scope and remain read-side operations over durable storage.

## Adapters

`PosixJsonlEventJournalAdapter` is the only production adapter. It composes the
existing `JSONLLoggingService`, `SQLiteEventIndex`, and
`CompositeLoggingService`: redaction happens before either durable write; JSONL
is written and flushed first; SQLite receives the same redacted event with JSONL
provenance only after primary success.

## Contract rules

1. JSONL is authoritative and append order is preserved.
2. Every durable copy is redacted before storage.
3. A successful append returns the authoritative file and exact byte offset.
4. SQLite is derived and best-effort; its first failure disables the sidecar while
   later JSONL appends continue.
5. A primary failure propagates and MUST NOT create a sidecar-only fact.
6. Append after close creates neither a primary nor sidecar fact; close is
   idempotent.
7. Core imports, receives, invokes, and closes only the Port. Concrete POSIX
   construction belongs to outer composition roots.

## Contract tests

`tests/test_event_journal.py` runs the production adapter through a shared factory
and proves order, exact byte offsets, immediate flush visibility, close behavior,
redaction in both stores, primary-failure ordering, SQLite fail-open behavior,
raw-Core non-composition, outer-wrapper defaults, CLI injection, and negative
source architecture constraints.

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes require
synchronized Port, adapter, contract-test, and contract updates; structural or
composition changes also update the paired Anatomy and reciprocal parents.
