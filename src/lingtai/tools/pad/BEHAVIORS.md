---
name: pad-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/pad/CONTRACT.md
  - src/lingtai/tools/pad/ANATOMY.md
  - src/lingtai/tools/pad/__init__.py
  - src/lingtai/tools/pad/_pad.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a pad
  tool behavior clause changes, update the guarding LABT here in the same
  change.
---
# Pad Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/pad/CONTRACT.md` (append/ manual only, validation before
persistence, no hot load, delayed activation). Pinned pytest commands must run
from the repo root with the project's Python.

## Behavior PD001 — append validates every path and the aggregate limit before persisting, and never hot-loads the prompt

- **id**: PD001
- **title**: append validates every path and the aggregate limit before persisting, and never hot-loads the prompt
- **guards**: `pad-contract` § Public port
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with existing UTF-8 text files
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_pad.py -q` and capture the outcome.
2. Call `pad(action="append", input={"files": ["<scratch>/a.txt"]}, reasoning="probe")` and record the receipt; confirm `system/pad_append.json` was written and `prompt_reload` is `false`.
3. Call `pad(action="append", input={"files": ["<scratch>/missing.txt"]}, reasoning="probe")` and confirm the invalid path is rejected before persistence; call `pad(action="edit", input={...}, reasoning="probe")` and confirm it fails as an unknown action.

### Expected evidence
- [ ] Step 1: the pad suite passes, pinning the exact action set, strict retirement, validation-before-persistence, and delayed activation.
- [ ] Step 2: the append receipt contains `status`, `files`, `count`, `prompt_reload: false`, and `takes_effect`; the persisted list is `system/pad_append.json`.
- [ ] Step 3: an invalid path fails before any write; retired `edit`/`load` fail loudly before I/O; no prompt flush or prompt-manager section change occurs.

### Pass / Fail
Pass when the suite passes and the validate-before-persist/no-hot-load observations hold. Fail on a persisted invalid path, on a working retired action, or on any prompt reload side effect from append; record the evidence trail in the task report.
