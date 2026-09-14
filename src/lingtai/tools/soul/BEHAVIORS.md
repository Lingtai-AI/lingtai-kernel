---
name: soul-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/soul/CONTRACT.md
  - src/lingtai/tools/soul/ANATOMY.md
  - src/lingtai/tools/soul/__init__.py
  - src/lingtai/tools/soul/flow.py
  - src/lingtai/tools/soul/settings.py
  - tests/test_soul_settings.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a soul
  tool behavior clause changes, update the guarding LABT here in the same
  change.
---
# Soul Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/soul/CONTRACT.md` (flow opt-in gate, disabled/ongoing paths
return before spawning, manual no-op, five-field settings SHOW, envelope
enforcement). Pinned pytest commands must run from the repo root with the
project's Python.

## Behavior SU001 — flow's disabled and ongoing paths return before any fire thread, and manual touches no soul state

- **id**: SU001
- **title**: flow's disabled and ongoing paths return before any fire thread, and manual touches no soul state
- **guards**: `soul-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; `LINGTAI_SOUL_FLOW_ENABLED` unset in the probe environment
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_soul.py tests/test_soul_consultation.py -q` and capture the outcome.
2. With `LINGTAI_SOUL_FLOW_ENABLED` unset, call `soul(action="flow", input={}, reasoning="probe")` and record the result; then set the variable and call `flow` twice in quick succession and record both results.
3. Call `soul(action="manual", input={}, reasoning="probe")` and confirm no timer, lock, consultation, config, voice, or notification state changed.

### Expected evidence
- [ ] Step 1: the soul suites pass, pinning inquiry/flow/config/voice/dismiss/settings/manual behavior and consultation pair building.
- [ ] Step 2: with the env opt-out, `flow` returns `{status: "disabled", enabled: False, env_var, message}` and spawns no fire thread; a second `flow` while a fire is in flight returns `{error: "soul flow ongoing, request rejected"}` before spawning.
- [ ] Step 3: `manual` reads the installed manual and touches no soul state; a cross-action input key fails with `INVALID_ARGUMENT` before any handler I/O.

### Pass / Fail
Pass when the suites pass and the gate/no-op observations hold. Fail on a fire thread spawning when disabled or ongoing, on a `manual` that touches soul state, or on an accepted cross-action input; record the evidence trail in the task report.

## Behavior SU002 — settings shows five owner rows without mutation or partial truth

- **id**: SU002
- **title**: settings shows five owner rows without mutation or partial truth
- **guards**: `soul-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>` and its repository virtual environment
- **estimate**: ≈ 5 minutes

### Steps
1. From `<repo>`, run `python -m pytest -q tests/test_soul_settings.py tests/test_tool_settings_contract.py` and capture the outcome.
2. Inspect the successful Soul inventory fixture and the unavailable-current fixture in `tests/test_soul_settings.py`.
3. Inspect `soul-manual` and resolve each returned `comment` fragment to its exact settings section.

### Expected evidence
- [ ] Step 1: the suites pass and the shared exact opt-in assertion identifies only System and Soul on this independent owner branch.
- [ ] Step 2: success has exactly the five ordered Soul keys and exactly `key`/`current`/`default`/`configurable`/`comment`; the prompt is redacted, strict non-empty input fails, ordinary dismiss is unchanged, and unavailable current truth produces one fixed no-row failure.
- [ ] Step 3: all five comments target manual sections containing meaning, accepted values, source/precedence, canonical env/config keys, timing, sensitivity/authorization notes, and the existing real change procedures.

### Pass / Fail
Pass when both suites pass, System and Soul are the exact opted-in official set, and every projected comment resolves. Fail on mutation through SHOW, an extra or missing row field, secret disclosure, partial-row unavailable output, a missing manual target, or any unrelated family opt-in.
