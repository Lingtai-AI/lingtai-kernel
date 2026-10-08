---
name: lifecycle-clock-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/lifecycle_clock/CONTRACT.md
  - src/lingtai/kernel/lifecycle_clock/ANATOMY.md
  - src/lingtai/kernel/lifecycle_clock/__init__.py
  - src/lingtai/adapters/lifecycle_clock.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  lifecycle-clock behavior clause changes, update the guarding LABT here in the
  same change.
---
# Lifecycle Clock Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/lifecycle_clock/CONTRACT.md` (two distinct time domains;
no disabled clock; raw wall float passed to presence). Pinned pytest commands
must run from the repo root with the project's Python.

## Behavior LC001 — wall and monotonic domains stay distinct and the raw wall float reaches presence unchanged

- **id**: LC001
- **title**: wall and monotonic domains stay distinct and the raw wall float reaches presence unchanged
- **guards**: `lifecycle-clock` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; the `FakeLifecycleClock` helper available in `tests/_lifecycle_clock_helpers.py`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_lifecycle_clock.py -q` and capture the outcome.
2. Using `FakeLifecycleClock` with independently controlled wall and monotonic values, drive an agent heartbeat loop and record the float handed to the injected presence port's `publish_heartbeat`.
3. Confirm monotonic values are never persisted and wall jumps do not alter monotonic elapsed behavior (and vice versa).

### Expected evidence
- [ ] Step 1: the lifecycle-clock suite passes, pinning the two-operation Port shape, raw float forwarding, shared sampling, and monotonic/wall independence.
- [ ] Step 2: the presence port received the exact raw wall float unchanged (byte-exact `str(value)`-no-newline conformance is the presence contract's, not asserted here).
- [ ] Step 3: no monotonic value becomes persisted state, and a wall jump leaves monotonic elapsed intervals unchanged.

### Pass / Fail
Pass when the suite passes and both domains behave independently with the raw wall float forwarded unchanged. Fail on domain leakage, on a disabled/no-op clock being accepted, or on a modified wall value reaching presence; record the evidence trail in the task report.
