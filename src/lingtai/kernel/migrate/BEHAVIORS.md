---
name: migration-workspace-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/migrate/CONTRACT.md
  - src/lingtai/kernel/migrate/ANATOMY.md
  - src/lingtai/kernel/migrate/migrate.py
  - src/lingtai/kernel/migrate/__init__.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  migration-workspace behavior clause changes, update the guarding LABT here in
  the same change.
---
# Migration Workspace Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/migrate/CONTRACT.md` (contiguous append-only registry,
forward-only movement, version-after-success durability, atomic replacement).
Pinned pytest commands must run from the repo root with the project's Python.

## Behavior MG001 — the version counter advances only after a transform succeeds, so a failure at N+1 leaves the persisted version at N

- **id**: MG001
- **title**: the version counter advances only after a transform succeeds, so a failure at N+1 leaves the persisted version at N
- **guards**: `migration-workspace` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch migration workspace `<scratch>`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_kernel_migrate.py -q` and capture the outcome.
2. Drive a failing transform at N+1 in `<scratch>` (a transform whose write-back fails) and read back the persisted version file (`_kernel_meta.json`); confirm it still reads N.
3. Confirm every replacement (preset m001/m002 included) uses the PID-suffixed atomic sibling-temp mechanism and that a malformed document is skipped with a warning, not a crash.

### Expected evidence
- [ ] Step 1: the kernel-migrate conformance suite passes, pinning registry contiguity, version-after-success durability, retry resume, and PID-suffixed version writes.
- [ ] Step 2: after the N+1 failure the persisted version is exactly N and the next launch resumes there (forward-only, no rollback/downgrade).
- [ ] Step 3: every replacement is atomic via the sibling-temp mechanism; malformed documents continue with a warning and the exact current serialization is written back on success.

### Pass / Fail
Pass when the suite passes and version-after-success durability holds. Fail on a version advancing before its transform succeeds, on rollback/downgrade behavior, or on a non-atomic replacement; record the evidence trail in the task report.
