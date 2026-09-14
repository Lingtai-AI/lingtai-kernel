---
name: structured-event-journal-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/event_journal/CONTRACT.md
  - src/lingtai/kernel/event_journal/ANATOMY.md
  - src/lingtai/kernel/event_journal/__init__.py
  - src/lingtai/adapters/posix/event_journal.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when an
  event-journal behavior clause changes, update the guarding LABT here in the
  same change.
---
# Structured Event Journal Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/event_journal/CONTRACT.md` (JSONL-first ordering,
redaction-before-storage, returned byte provenance, fail-open derived index).
Pinned pytest commands must run from the repo root with the project's Python.

## Behavior EJ001 — a successful append returns the exact JSONL file and byte offset, with every durable copy redacted before storage

- **id**: EJ001
- **title**: a successful append returns the exact JSONL file and byte offset, with every durable copy redacted before storage
- **guards**: `structured-event-journal` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch working directory `<scratch>`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_event_journal.py -q` and capture the outcome.
2. Append a structured event carrying a secret-shaped field through the production adapter bound to `<scratch>` and read back the JSONL line; note the byte offset reported by the append result.
3. Confirm the SQLite sidecar (derived index) received the same redacted event with JSONL provenance, and that a primary append failure created no sidecar-only fact.

### Expected evidence
- [ ] Step 1: the event-journal conformance suite passes, pinning order, exact byte offsets, immediate flush visibility, close behavior, and redaction in both stores.
- [ ] Step 2: the returned `JournalPosition` names the real JSONL file and the starting byte offset of the appended line; the persisted line contains no secret-shaped value.
- [ ] Step 3: SQLite contains only the redacted event after primary success; on primary failure no sidecar-only fact exists.

### Pass / Fail
Pass when every evidence item holds. Fail on out-of-order JSONL appends, on any unredacted durable copy, on an append result whose byte offset does not match the file, or on a sidecar-only fact after primary failure; record the evidence trail in the task report.
