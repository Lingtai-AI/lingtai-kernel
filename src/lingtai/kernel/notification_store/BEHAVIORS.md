---
name: notification-store-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/notification_store/CONTRACT.md
  - src/lingtai/kernel/notification_store/ANATOMY.md
  - src/lingtai/kernel/notification_store/__init__.py
  - src/lingtai/kernel/notification_store/_mutation_lock.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  notification-store behavior clause changes, update the guarding LABT here in
  the same change.
---
# Notification Store Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/notification_store/CONTRACT.md` (eight operation families,
atomic publish/clear, compare-update semantics, stale dismiss refusal). Pinned
pytest commands must run from the repo root with the project's Python.

## Behavior NS001 — a compare conflict never calls the mutator, and changed=False performs no write

- **id**: NS001
- **title**: a compare conflict never calls the mutator, and changed=False performs no write
- **guards**: `notification-store` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch working directory `<scratch>`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_notification_store.py -q` and capture the outcome.
2. In `<scratch>`, publish a channel payload, then issue a compare-update with a stale expected version; record whether the pure Core mutator ran and what `CompareUpdateResult` reports (`applied`/`conflict`/`changed`).
3. Issue a compare-update whose mutator returns an unchanged payload and confirm no write occurs (file mtime and bytes unchanged).

### Expected evidence
- [ ] Step 1: the notification-store conformance suite passes, pinning the eight-family surface, expected-absence versus unconditional updates, atomic updates, and stale-dismiss refusal.
- [ ] Step 2: on a version conflict the mutator was NOT invoked, `conflict` is reported, and no policy value is carried.
- [ ] Step 3: `changed=False` performs no write — the `.notification/<channel>.json` file is untouched.
- [ ] Step 4: non-force dismiss against a stale fingerprint is refused (no unrelated current events lost).

### Pass / Fail
Pass when the suite passes and the conflict/no-write observations hold. Fail if a conflict invokes the mutator, if a no-op update rewrites the file, or if unrelated current events are dropped during an event/ref update; record the evidence trail in the task report.
