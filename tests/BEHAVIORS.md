---
name: tests-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - tests/CONTRACT.md
  - tests/ANATOMY.md
  - tests/test_architecture_documents.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with tests/CONTRACT.md (the methodology charter): when a testing
  principle changes, update the guarding LABT here in the same change. This
  charter is deliberately not a governed component contract; this file is its
  behavior-evidence sibling only.
---
# Test Methodology Charter Behavior Tests

Self-contained agent behavior tasks guarding the observable methodology
principles of `tests/CONTRACT.md` (deterministic validation, observable waits,
harness-vs-product classification, evidence preservation). These tasks inspect
how the repository's own tests behave rather than asserting a runtime interface.

## Behavior TS001 — tests wait on observable signals instead of arbitrary sleeps, and every failure is classified as harness or product with evidence

- **id**: TS001
- **title**: tests wait on observable signals instead of arbitrary sleeps, and every failure is classified as harness or product with evidence
- **guards**: `tests/CONTRACT.md` § Principles
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_labt_validation.py tests/test_architecture_documents.py -q` and capture the outcome.
2. Grep the `tests/` tree for bare `sleep(` calls in test files that are not gated on an observable signal (Event/gate/state transition/file appearance) and list any that look like arbitrary sleeps.
3. Review the last failing run's report: confirm every failure was classified as harness vs product and that the exact command, interpreter, source pinning, and verbatim output were preserved as evidence (e.g. in a `tmp/` log).

### Expected evidence
- [ ] Step 1: the validation suites run to completion; a timeout or interruption is reported as incomplete, never as a pass.
- [ ] Step 2: no test relies on an arbitrary sleep to observe a condition that has a gated/observable signal available (any found sleep is justified or replaced by a gate).
- [ ] Step 3: the failure report classifies each failure as harness or product with evidence, and non-zero exits are never laundered into green claims.

### Pass / Fail
Pass when the validation suites complete, no arbitrary sleep substitutes for an observable wait, and failures are classified with preserved evidence. Fail on a bare sleep used as the sole synchronization, on an unclassified failure, or on a non-zero exit reported as success; record the evidence trail in the task report.
