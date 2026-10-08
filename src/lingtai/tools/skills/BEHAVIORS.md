---
name: skills-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/skills/CONTRACT.md
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/skills/__init__.py
  - src/lingtai/tools/_catalog.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  skills tool behavior clause changes, update the guarding LABT here in the
  same change.
---
# Skills Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/skills/CONTRACT.md` (info/manual only, strict-empty input,
canonical degraded shape, action separation). Pinned pytest commands must run
from the repo root with the project's Python.

## Behavior SK001 — info only refreshes the catalogue and manual only reads the installed manual

- **id**: SK001
- **title**: info only refreshes the catalogue and manual only reads the installed manual
- **guards**: `skills-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with a skills catalog
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_skills.py -q` and capture the outcome.
2. Call `skills(action="info", input={}, reasoning="probe")` and record the result; confirm it reconciles the catalog and re-injects the prompt without authoring, pinning, publishing, installing, or executing a skill.
3. Call `skills(action="manual", input={}, reasoning="probe")` and record the result; confirm no catalogue scan or prompt injection occurred, and that an extra `input` key fails with `INVALID_ARGUMENT` before any handler I/O.

### Expected evidence
- [ ] Step 1: the skills suite passes, pinning catalog behavior, prompt injection, and the skills/knowledge boundary.
- [ ] Step 2: `info` returns `{status, skills_dir, library_dir, catalog_size, paths, problems}` with no manual body and performs no side effect beyond reconciliation.
- [ ] Step 3: `manual` returns `{status, skills_manual, library_manual, manual_path}` (or the degraded shape when the manual is missing); any `input` key is rejected before handler I/O; unknown actions yield `ACTION_REQUIRED`.

### Pass / Fail
Pass when the suite passes and the action-separation observations hold. Fail on `info` installing/executing a skill, on `manual` performing a catalogue scan, or on an accepted input field; record the evidence trail in the task report.
