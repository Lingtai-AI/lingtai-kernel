---
name: workdir-lease-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/workdir_lease/CONTRACT.md
  - src/lingtai/kernel/workdir_lease/ANATOMY.md
  - src/lingtai/kernel/workdir_lease/__init__.py
  - src/lingtai/adapters/posix/workdir_lease.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  workdir-lease behavior clause changes, update the guarding LABT here in the
  same change.
---
# Workdir Lease Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/workdir_lease/CONTRACT.md` (exclusive claim, exact
contention error, idempotent release, lock-existence-is-not-authority). Pinned
pytest commands must run from the repo root with the project's Python.

## Behavior WL001 — a held lease excludes a second acquire with the exact contention error, and release is idempotent

- **id**: WL001
- **title**: a held lease excludes a second acquire with the exact contention error, and release is idempotent
- **guards**: `workdir-lease` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch working directory `<scratch>`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_workdir_lease.py -q` and capture the outcome.
2. In `<scratch>`, acquire the lease, then attempt a second `acquire(0)` and record the exception type and message.
3. Call `release()` twice, then acquire again and confirm success; on Windows-native runs, confirm the TUI byte-0/length-1 probe mapping (held→Block, released→Allow).

### Expected evidence
- [ ] Step 1: the workdir-lease suite passes, pinning collision, delayed-release-before-timeout, zero-timeout failure, expiry, idempotent release, and the exact contention text `Working directory '<path>' is already in use by another agent.`.
- [ ] Step 2: the second acquire raises `RuntimeError` with the exact text; lock-file existence alone is not authority.
- [ ] Step 3: repeated `release()` calls are safe, a subsequent acquire succeeds after release, and the close-before-unlink order holds.

### Pass / Fail
Pass when the suite passes and the exclusion/idempotency observations hold. Fail on a second acquire succeeding while the lease is held, on a missing or changed contention message, or on a non-idempotent release; record the evidence trail in the task report.
