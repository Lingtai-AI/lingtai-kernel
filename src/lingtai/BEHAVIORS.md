---
name: init-reader-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - tests/test_tool_decision_phase.py
  - src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md
  - src/lingtai/adapters/tool_decision.py
  - src/lingtai/kernel/tool_decision.py
  - tests/test_agents_memory.py
  - src/lingtai/CONTRACT.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/init_reader.py
  - src/lingtai/init_schema.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  reader behavior or compatibility clause changes, update the guarding LABT
  here in the same change.
---
> Test-reference maintenance (2026-10-12): `retired-test:` identifiers below name removed pytest coverage, not runnable selectors or replacement conformance. Product promises are unchanged; manual acceptance remains necessary where automated coverage was surrendered.

# Init Reader Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/CONTRACT.md` (one real read path for boot and refresh, typed
outcomes, no auto-mutation of `<workdir>/init.json`). Pinned pytest commands
must run from the repo root with the project's Python.

## Behavior IR001 — the reader never modifies init.json and returns typed outcomes instead of fabricated success

- **id**: IR001
- **title**: the reader never modifies init.json and returns typed outcomes instead of fabricated success
- **guards**: `init-reader` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with an `init.json`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, **Automated step unavailable: pytest recipe retired (2026-10-12); no replacement conformance claimed** and capture the outcome.
2. In `<scratch>`, write an `init.json` containing a deprecated/ignored field and boot the reader path (boot CLI or refresh `Agent`); hash `<scratch>/init.json` before and after and compare.
3. Run `.venv/bin/python -m pytest -q tests/test_agents_memory.py -k legacy_character_seed`
   from `<repo>`; both old seed spellings must remain accepted and preserve
   their content once into Pad while leaving init bytes unchanged. Inspect the returned outcome: confirm it reports `FULLY_EFFECTIVE` / `READ_OK_WITH_IGNORED_FIELDS` / `READ_FAILED` with a typed shape decision and the ignored paths, and that the redacted `system/manifest.resolved.json` artifact was produced.

### Expected evidence
- [ ] Step 1: the init-reader/schema suites pass, pinning JSONC parsing,
      identical boot/refresh outcomes, ignored-path reporting, structured
      failure evidence, typed `manifest.disable` entries, and rejection of
      non-finite canonical numbers.
- [ ] Step 2: the bytes of `<scratch>/init.json` are unchanged after the read (no strip, no canonicalization, no rewrite).
- [ ] Step 3: the outcome status and shape decision are typed and truthful; failures carry stage, location when available, safe excerpt, behavior, and a next repair step.

### Pass / Fail
Pass when the suite passes, `init.json` is untouched, and the outcome is typed rather than fabricated. Fail on any auto-rewrite of `init.json`, on a claimed success for an ignored-field read, or on a missing redacted manifest artifact; record the evidence trail in the task report.

## Behavior IR002

- **id**: IR002
- **title**: tool-decision init opt-in reaches the real Agent executor without rewriting raw init
- **guards**: `init-reader` § [Tool decision opt-in](CONTRACT.md#tool-decision-opt-in)
- **runner**: parent-authorized validation agent
- **prerequisites**: offline checkout with test dependencies
- **estimate**: ≈ 2 minutes

### Steps
1. Run `.venv/bin/python -m pytest -q tests/test_tool_decision_phase.py`.
2. Inspect the real init-reader → config hydration → real Agent minimal CLI-construction seam → `SessionManager.send` → `_make_tool_executor` test, and the disabled no-op regression.

### Expected evidence
- [ ] The default is off, and explicit true reaches reviewer composition.
- [ ] The raw init bytes, main prompt and main chat identity remain unchanged.
- [ ] Returned reviewer usage is attributed separately and the recording handler executes only after an allow.

### Pass / Fail
Pass requires successful offline tests, not authorship. Full live
`_setup_from_init`, provider transport and platform acceptance remain separate.
