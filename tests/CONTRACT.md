# Test Methodology Charter

> **This is a methodology/workflow charter, not a governed component Contract.**
> It is deliberately **not** a root-governed Ports-and-Adapters child of the
> distributed [`CONTRACT.md`](../CONTRACT.md) system: it is **not** listed in the
> root contract's `related_files`, has **no** paired `tests/ANATOMY.md`, carries
> **no** governed frontmatter, and does **not** follow the governed 7-heading
> template. `tests/` is a validation surface, not an architectural component with
> its own Port/Adapters promise. This file records *how we test*; it does not
> define an interface anyone implements.

It also does not restate the change workflow. The **how** lives in the workflow
owners; read them first and apply them:

- [`dev-guide-skill/SKILL.md`](../dev-guide-skill/SKILL.md) — the mandatory
  repository-local development and validation workflow (baseline, Anatomy/Contract
  systems, narrow-to-broad validation, PR gates).
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — the public contribution route and its
  pointer to the full coding-agent and test reference.

This charter states the testing principles those workflows assume, once, so a
coding agent writing or running kernel tests shares one standard.

## Why this exists

Tests are the repository's conformance evidence. Anatomy says where code is,
Contract says what it promises, and **tests prove the promise holds**. A test
that passes for the wrong reason — a swallowed timeout, an unpinned import, a
mock that no longer resembles the product — is worse than no test, because it
launders a defect as evidence. The principles below keep test results
trustworthy.

## Principles

### 1. Know the test layer, and keep each test at one

- **Unit / pure** — a function or small object with no threads, filesystem, or
  network. Fast, exhaustive on edge cases.
- **Integration / lifecycle** — a real `BaseAgent`, real threads, real
  start/stop, with injected in-memory Port test doubles (leases, snapshot,
  notification store, mock LLM service). This is where run-loop, heartbeat,
  teardown, and ordering contracts are proven.
- **Contract-conformance** — the shared tests a Port's every Adapter must pass
  (owned by each governed component's `CONTRACT.md`), plus the
  architecture-document validation.

Name the layer before writing the test; do not smuggle integration behavior into
a "unit" test or stub away the very behavior an integration test exists to prove.

### 2. Isolation

Each test owns its own state. Use `tmp_path` for any workdir; never write into a
shared or real agent directory. Do not depend on another test's side effects,
ordering, or leftover files. Threads a test starts are the test's to stop: join
or otherwise wind down every thread/heartbeat/loop it launches (a `finally` that
clears the loop predicate and releases any gate), so a failing assertion cannot
leak a live thread into the next test.

### 3. Realism

Test against the real production code path, not a re-implementation of it. Drive
the actual `_run_loop`, the actual `_stop`, the actual heartbeat loop. Test
doubles substitute only at true Port boundaries (the injected lease / snapshot /
store / LLM service), and a double must stay faithful to the contract it stands
in for — if the product would block, the double blocks; if the product returns a
shape, the double returns that shape. A test that only exercises a mock proves
only the mock.

### 4. Source pinning

Run the kernel from this checkout, not from an installed wheel, so the test
exercises the code under review. Pin the source and the interpreter explicitly:

```bash
PYTHONPATH="$PWD/src" /path/to/project/.venv/bin/python -m pytest -q <targets>
```

An unpinned run can silently import a different installed `lingtai`, turning a
green bar into a lie about the working tree.

### 5. Deterministic validation

A test must pass or fail for one identifiable reason, every run. Prefer
Events, queues, and explicit gates to encode the exact condition under test.
When adding a regression for a bug, make it **fail first** on the unfixed code
for the *specific* missing behavior (not a generic threshold), then pass once the
fix lands — that is what proves the test can detect the defect at all. Avoid
assertions that can flake under load or pass by coincidence.

### 6. Observable waits over arbitrary sleeps

Do not `sleep(n)` and hope the thing happened. Wait on an **observable signal** —
`Event.wait(timeout)`, a gated queue that pulses when entered, a state
transition, a file appearing — with a timeout generous enough to absorb
scheduling jitter, and assert the signal actually fired. Arbitrary sleeps make
tests both slow and flaky; a gate makes the intent, and the failure, precise.
(Worked example in this directory: `test_lifecycle_stop_wake.py` gates on inbox
`get`-entry and on recorded cadence-wait outcomes instead of timing the stop.)

### 7. Narrow-to-broad gates

Run the narrowest decisive check first, then widen. The new/changed test, then
the adjacent focused module(s) it touches (lifecycle / heartbeat / agent /
notification …), then the architecture-document validation and the Anatomy drift
check when either distributed graph changed, then `git diff --check`. Broaden
only after the narrow layer is green; a broad run started before the narrow gate
just hides which layer broke. The full suite is the *last* gate, not the first;
run it only after focused validation is decisive.

### 8. A fail, hang, or marked slowness is an immediate investigation, not a note

If a test fails, hangs, or a run is flagged as unexpectedly slow, stop and
investigate the cause **before** doing anything else — do not retry-until-green,
skip, loosen the assertion, or defer it as a follow-up. The failure is the
signal you asked for; discarding it discards the evidence. Diagnose whether it is
a real product defect, a stale test, or a harness issue (next principle) and
resolve *that*.

### 9. Classify every failure as harness vs. product

Before acting on a red result, decide which side broke:

- **Product** — the code under test violates its contract. Fix the code (or, only
  under explicit authorization, the contract); never weaken the test to hide it.
- **Harness** — the test, fixture, mock, or environment is wrong (e.g. a mock
  that is not JSON-serializable emitting a benign best-effort warning). Fix the
  harness, and **state the classification with its evidence** so a reader is not
  left thinking a product bug was waved away. Record known-benign harness noise
  next to the result rather than deleting it.

Do not let a harness artifact mask a product failure, or vice versa.

### 10. A timeout or interruption is never a pass

A suite that timed out, was interrupted, or was killed did **not** pass — report
it as incomplete with its exit status and the point it stopped. Only a run that
completed and reported success is success. Never launder a non-zero exit,
truncated output, or a hung run into a green claim. Inspect every non-zero exit
code.

### 11. Preserve evidence

Keep the exact command, interpreter, source pinning, and verbatim output for
decisive runs — especially failing-first captures and before/after comparisons —
in clearly named `tmp/` logs. Do not overwrite, prune, or "clean up" existing
evidence artifacts; add new, distinctly named ones. Evidence is what lets a
reviewer re-derive the conclusion instead of trusting a summary.

### 12. Measured performance work needs before/after wall-time **and** correctness

A performance change is not proven by a plausible story. Measure the target
metric (e.g. wall-clock, call-phase time) **before** and **after** the change on
the same pinned setup, preserve both numbers, and in the same breath prove the
behavior is unchanged — the regression tests and adjacent focused suites still
pass. A faster run that quietly changed semantics is a regression, not a win. Do
not lower a permanent interval/timeout to "go faster" when the real fix is to
wake the wait; prove the cadence/interval is preserved.

## Maintenance

This charter is prose guidance, not a governed interface, so it has no
contract-version, no shared conformance test, and no reciprocal Anatomy twin.
Update it when the *testing methodology* itself changes. If a rule here would
start duplicating the change workflow, move the detail to
[`dev-guide-skill/SKILL.md`](../dev-guide-skill/SKILL.md) and link to it instead
(progressive disclosure). Do not promote this file into the governed
`CONTRACT.md` graph or add it to the root contract's `related_files` unless
`tests/` genuinely becomes a component with an implemented Port — at which point
it would need a real paired `ANATOMY.md` and governed frontmatter, which it does
not have today.
