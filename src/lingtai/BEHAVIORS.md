---
name: init-reader-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/CONTRACT.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/init_reader.py
  - src/lingtai/init_schema.py
  - tests/test_init_schema.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  reader behavior or compatibility clause changes, update the guarding LABT
  here in the same change.
---
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
1. From `<repo>`, run `python -m pytest tests/test_init_reader.py tests/test_init_schema.py -q` and capture the outcome.
2. In `<scratch>`, write an `init.json` containing a deprecated/ignored field and boot the reader path (boot CLI or refresh `Agent`); hash `<scratch>/init.json` before and after and compare.
3. Inspect the returned outcome: confirm it reports `FULLY_EFFECTIVE` / `READ_OK_WITH_IGNORED_FIELDS` / `READ_FAILED` with a typed shape decision and the ignored paths, and that the redacted `system/manifest.resolved.json` artifact was produced.

### Expected evidence
- [ ] Step 1: the init-reader/schema suites pass, pinning JSONC parsing,
      identical boot/refresh outcomes, ignored-path reporting, structured
      failure evidence, typed `manifest.disable` entries, and rejection of
      non-finite canonical numbers.
- [ ] Step 2: the bytes of `<scratch>/init.json` are unchanged after the read (no strip, no canonicalization, no rewrite).
- [ ] Step 3: the outcome status and shape decision are typed and truthful; failures carry stage, location when available, safe excerpt, behavior, and a next repair step.

### Pass / Fail
Pass when the suite passes, `init.json` is untouched, and the outcome is typed rather than fabricated. Fail on any auto-rewrite of `init.json`, on a claimed success for an ignored-field read, or on a missing redacted manifest artifact; record the evidence trail in the task report.
