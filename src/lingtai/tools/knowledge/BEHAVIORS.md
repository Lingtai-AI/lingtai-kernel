---
name: knowledge-behavior-tests
behavior_version: 2
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
  knowledge capability behavior clause changes, update the guarding LABT here in the
  same change.
---
# Knowledge Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/knowledge/CONTRACT.md` (no model-facing tool; read-only
guidance via `psyche(action="knowledge")`; private setup/reconciliation
lifecycle; private agent-owned catalog). Pinned pytest commands must run from
the repo root with the project's Python.

## Behavior KN001 — knowledge registers no model-facing action; guidance is read-only via psyche and the private lifecycle reconciles the catalog

- **id**: KN001
- **title**: knowledge registers no model-facing action; guidance is read-only via psyche and the private lifecycle reconciles the catalog
- **guards**: `knowledge-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with a `knowledge/` entry
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_knowledge.py tests/test_tool_family_knowledge_migration_parity.py tests/test_psyche_family.py -q` and capture the outcome.
2. Boot an agent with `capabilities={"knowledge": {}}` on `<scratch>`; confirm `knowledge` is absent from the tool handlers and the built tool schemas while the protected `knowledge` prompt section exists. Call `psyche(action="knowledge", input={}, reasoning="load knowledge guidance")` and record the result; confirm it returns the manual body at `manual` and its path at `manual_path` with no health fields (`catalog_size`, `problems`, `knowledge_dir`).
3. Call `psyche(action="info", input={}, reasoning="probe a removed action")` — the retired `info` action name against the current Psyche root — and record the result; confirm the typed unknown-action failure and that no scan or mutation happened.
4. Author a new entry at `<scratch>/knowledge/<name>/KNOWLEDGE.md` via `shell`, then apply one `context(action="rebuild", input={}, reasoning="refresh knowledge catalog")`; confirm the entry's name and description appear in the `knowledge` prompt section while its body does not.

### Expected evidence
- [ ] Step 1: the knowledge and psyche suites pass, pinning the absent tool surface, the psyche manual routing, the private reconciliation/migration split, and the knowledge/skill boundary.
- [ ] Step 2: no `knowledge` tool exists; `psyche(action="knowledge")` returns exactly `{status, manual, manual_path}` and never rescans, reconciles, or migrates the catalog.
- [ ] Step 3: every retired knowledge action fails before any I/O with the generic unknown-action envelope and mutates nothing.
- [ ] Step 4: the catalog reaches the prompt only through setup/refresh or an explicit rebuild; bodies stay out of the prompt catalog.

### Pass / Fail
Pass when the suites pass and the no-tool/read-only-routing observations hold. Fail on any registered `knowledge` tool, on a `psyche(action="knowledge")` result carrying health fields or side effects, on a silently accepted retired action, or on a mutating manual load; record the evidence trail in the task report.
