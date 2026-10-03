---
name: skills-behavior-tests
behavior_version: 2
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/skills/CONTRACT.md
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/skills/__init__.py
  - src/lingtai/tools/skills/manual/SKILL.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/psyche/__init__.py
  - tests/test_skills.py
  - tests/test_psyche_family.py
maintenance: |
  Keep this file reciprocal with CONTRACT.md and ANATOMY.md (tridirectional
  loop): when the Skills capability behavior or its read-only Psyche route
  changes, update the guarding LABT here in the same change. The capability
  itself owns no model-facing ToolFamily.
---

# Skills Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/skills/CONTRACT.md`: no model-facing `skills` root, read-only
manual guidance through `psyche(action="skills")`, and private catalog
reconciliation. Pinned pytest commands must run from the repo root with the
project's Python.

## Behavior SK001 — the private lifecycle reconciles the catalog and Psyche only reads the installed manual

- **id**: SK001
- **title**: the private lifecycle reconciles the catalog and Psyche only reads the installed manual
- **guards**: `skills-contract` § Public guidance and § Private lifecycle
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with an installed Skills manual and catalog
- **estimate**: ≈ 15 minutes

### Steps

1. From `<repo>`, run `python -m pytest tests/test_skills.py tests/test_psyche_family.py -q` and capture the outcome.
2. Boot an agent with `capabilities={"skills": {}}` on `<scratch>`; confirm `skills` is absent from tool handlers and built schemas while the protected `skills` prompt section is composed by setup.
3. Call `psyche(action="skills", input={}, reasoning="probe")` and record the result. Confirm it returns the installed manual as `manual` with `manual_path`, or the documented degraded result when the file is missing; confirm no catalog health fields are present and no catalog scan or prompt injection occurs.
4. Add a valid `SKILL.md` under a configured path and trigger the private lifecycle through setup/refresh or `context(action="rebuild", input={}, reasoning="rescan skills catalog")`. Confirm the new name and description appear in the protected catalog while the body remains out of the prompt.
5. Inspect the booted agent's handlers and schemas again to confirm no alias or wrapper exposes the former `skills` root; then call `psyche(action="info", input={}, reasoning="probe a removed action")` and confirm the retired `info` action name is rejected on the current Psyche root before any catalog I/O.

### Expected evidence

- [ ] Step 1: the Skills/Psyche suites pass, pinning the absent root, manual routing, catalog lifecycle, and side-effect boundary.
- [ ] Step 2: no `skills` provider tool exists; setup still preserves the private capability and its protected catalog section.
- [ ] Step 3: Psyche returns exactly the flat manual shape (`status`, `manual`, `manual_path`, plus `error` only when degraded) and does not return private health fields.
- [ ] Step 4: reconciliation updates the catalog; only `name`, `description`, and `location` metadata is prompt-visible.
- [ ] Step 5: the former `skills` root has no alias or compatibility wrapper; the retired `info` action name is rejected by Psyche; guidance is read through `psyche(action="skills")`.

### Pass / Fail

Pass when the suites pass and the no-tool/read-only-routing observations hold.
Fail on any registered `skills` tool, on manual loading rescanning or mutating the
catalog, on private reconciliation disappearing, or on a catalog body entering
the prompt. Record the evidence trail in the task report.
