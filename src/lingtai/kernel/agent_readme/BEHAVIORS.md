---
name: agent-readme-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/agent_readme/CONTRACT.md
  - src/lingtai/kernel/agent_readme/ANATOMY.md
  - src/lingtai/kernel/agent_readme/__init__.py
  - src/lingtai/kernel/agent_readme/README.md.tpl
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when the
  agent-readme role/ownership/generation rules change, update the guarding LABT
  here in the same change.
---
# Agent Readme Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/agent_readme/CONTRACT.md` (the fixed `README.md` /
`substrate.md` role split, ownership, and generation rules). Pinned pytest
commands must run from the repo root with the project's Python.

## Behavior AR001 — the generated agent-root README links system/substrate.md and carries no agent name or live values

- **id**: AR001
- **title**: the generated agent-root README links system/substrate.md and carries no agent name or live values
- **guards**: `agent-readme` § 1. 角色
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_agent_readme.py -q` and capture the outcome.
2. Create `<scratch>` with an `init.json` and boot an agent there (or run the README generation path used by refresh/molt/BaseAgent construction), then inspect `<scratch>/README.md`.
3. Grep `<scratch>/README.md` for a relative link to `system/substrate.md` and for any agent-name or live dynamic value (provider/model/heartbeat tokens).

### Expected evidence
- [ ] Step 1: the agent-readme suite passes, pinning generation, staleness re-generation, and template consistency.
- [ ] Step 2: after initialization/refresh/molt, `<scratch>/README.md` exists at the agent root.
- [ ] Step 3: README.md contains a relative path to `system/substrate.md` and contains no agent-name placeholder and no live dynamic value (no provider/model/heartbeat runtime tokens).

### Pass / Fail
Pass when the README is present, links `system/substrate.md`, and contains no agent name or live dynamic values. Fail on any missing link or any runtime value leaked into the README; record the evidence trail in the task report.
