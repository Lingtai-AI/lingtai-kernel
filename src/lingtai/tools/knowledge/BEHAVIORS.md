---
name: knowledge-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/knowledge/CONTRACT.md
  - src/lingtai/tools/knowledge/ANATOMY.md
  - src/lingtai/tools/knowledge/__init__.py
  - src/lingtai/tools/_catalog.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  knowledge tool behavior clause changes, update the guarding LABT here in the
  same change.
---
# Knowledge Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/knowledge/CONTRACT.md` (info/manual only, strict-empty
input, health-only info, private catalog). Pinned pytest commands must run from
the repo root with the project's Python.

## Behavior KN001 — info returns health only and any input key is rejected before the handler runs

- **id**: KN001
- **title**: info returns health only and any input key is rejected before the handler runs
- **guards**: `knowledge-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with a `knowledge/` entry
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_knowledge.py -q` and capture the outcome.
2. Call `knowledge(action="info", input={}, reasoning="probe")` and record the result; confirm it reports `knowledge_dir`, `catalog_size`, and `problems` but no entry bodies.
3. Call `knowledge(action="info", input={"query": "x"}, reasoning="probe")` and record the result; confirm rejection happens before any scan or mutation.

### Expected evidence
- [ ] Step 1: the knowledge suite passes, pinning catalog scanning, prompt injection, and the knowledge/skill boundary.
- [ ] Step 2: `info` returns `{status: "ok", knowledge_dir, catalog_size, problems}` and never loads entry bodies into its result.
- [ ] Step 3: any `input` key is rejected with `INVALID_ARGUMENT` before the handler runs; unknown actions return the exact pre-migration envelope without mutating state.

### Pass / Fail
Pass when the suite passes and the health-only/no-argument observations hold. Fail on `info` returning entry bodies, on an accepted input field, or on a mutating `info`; record the evidence trail in the task report.
