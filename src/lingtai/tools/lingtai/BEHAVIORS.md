---
name: lingtai-tool-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/lingtai/CONTRACT.md
  - src/lingtai/tools/lingtai/ANATOMY.md
  - src/lingtai/tools/lingtai/__init__.py
  - src/lingtai/tools/lingtai/_lingtai.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  lingtai tool behavior clause changes, update the guarding LABT here in the
  same change.
---
# LingTai Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/lingtai/CONTRACT.md` (manual-only public inventory, retired
actions fail as unknown, protected character composition). Pinned pytest
commands must run from the repo root with the project's Python.

## Behavior LG001 — the public inventory is exactly manual, and retired update/load fail as unknown actions

- **id**: LG001
- **title**: the public inventory is exactly manual, and retired update/load fail as unknown actions
- **guards**: `lingtai-tool-contract` § Public port
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_context_ownership_redesign.py tests/test_pad_lingtai_split.py -q` and capture the outcome.
2. Call `lingtai(action="manual", input={}, reasoning="probe")` and record the flat `{status, manual, manual_path}` result.
3. Call `lingtai(action="update", input={}, reasoning="probe")` and `lingtai(action="load", input={}, reasoning="probe")`; record both results and confirm no file mutation occurred.

### Expected evidence
- [ ] Step 1: the context-ownership and pad/lingtai split suites pass, pinning the manual-only schema and dispatch and strict retired-action rejection.
- [ ] Step 2: `manual` resolves the `lingtai-manual` and is flattened once after generic ToolFamily dispatch.
- [ ] Step 3: retired `update`/`load` fail as unknown actions with no alias and no hidden mutation/reload path; schema and dispatch expose no hidden action on either provider wire.

### Pass / Fail
Pass when the suites pass and the manual-only/retired-rejection observations hold. Fail on a working `update`/`load`, on a hidden mutation path, or on a non-flat manual result; record the evidence trail in the task report.
