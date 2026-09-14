---
name: snapshot-source-revision-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/snapshot/CONTRACT.md
  - src/lingtai/kernel/snapshot/ANATOMY.md
  - src/lingtai/kernel/snapshot/__init__.py
  - src/lingtai/adapters/posix/git_cli.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  snapshot/source-revision behavior clause changes, update the guarding LABT
  here in the same change.
---
# Snapshot + Source Revision Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/snapshot/CONTRACT.md` (opt-in idempotent initialization,
stage-all capture, clean-tree no-op, bounded maintenance, tracked-only dirty).
Pinned pytest commands must run from the repo root with the project's Python.

## Behavior SN001 — snapshot capture stages all changes and returns None for a clean tree or operational failure

- **id**: SN001
- **title**: snapshot capture stages all changes and returns None for a clean tree or operational failure
- **guards**: `snapshot-source-revision` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch git workdir `<scratch>`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_snapshot.py -q` and capture the outcome.
2. In `<scratch>` (a git workdir), initialize snapshots twice and confirm the second initialization is a no-op; create, modify, and delete files, then capture a snapshot and record the returned revision.
3. With a clean tree, capture again and confirm `None`; confirm runtime identity returns a 12-character revision and tracked-only dirty tri-state within 0.5-second deadlines.

### Expected evidence
- [ ] Step 1: the snapshot conformance suite passes, pinning fixed initialization, exact exclusions, stage-all capture, clean no-op, failure translation, bounded maintenance, and revision formatting.
- [ ] Step 2: initialization is idempotent and creates the required system files even when Git fails; the snapshot returns native-short HEAD with a UTC-stamped commit.
- [ ] Step 3: a clean tree returns `None`; dirty means tracked-file state only (untracked files ignored); missing/failed/timed-out revision queries return `None` without crashing.

### Pass / Fail
Pass when the suite passes and the capture/no-op observations hold. Fail on a non-idempotent initialization, on a snapshot of a clean tree returning a revision, or on a revision query that crashes instead of returning `None`; record the evidence trail in the task report.
