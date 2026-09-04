---
name: psyche-behavior-tests
behavior_version: 3
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/psyche/ANATOMY.md
  - src/lingtai/tools/psyche/__init__.py
  - src/lingtai/tools/psyche/settings.py
  - src/lingtai/tools/psyche/prompt.py
  - src/lingtai/tools/psyche/glossary-en.md
  - src/lingtai/agent.py
  - tests/test_psyche_family.py
  - tests/test_psyche_prompt_settings.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  psyche behavior clause changes, update the guarding LABT here in the same
  change.
---
# Psyche Tool Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/psyche/CONTRACT.md` (five read-only manual actions, eight-row
redacted settings SHOW, and durable changes via file + explicit rebuild).
Pinned pytest commands must run from the repo root with the project's Python.

## Behavior PY001 — Psyche manuals and settings are read-only; durable changes apply only through file + rebuild

- **id**: PY001
- **title**: Psyche manuals and settings are read-only; durable changes apply only through file + rebuild
- **guards**: `psyche-tool-contract` § Behavior
- **runner**: any LingTai agent with `shell` and `file` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest -q tests/test_psyche_family.py tests/test_psyche_prompt_settings.py` and capture the outcome.
2. Call `pad | lingtai | knowledge | skills | manual` with strict empty input;
   confirm each returns its intended manual.
3. Call `settings` with strict empty input and record the eight rows. Edit the
   Psyche owner document without reconstructing, then make it malformed;
   confirm SHOW remains available and unchanged. Restore a valid owner document,
   reconstruct, and confirm the focused provider assertion sees the newly
   applied snapshot. Call settings with any input key and record the rejection.
   Hash all prompt/source files around each SHOW call.
4. Make a durable change with `file.edit` on the domain's own source and confirm the prompt section does not change until one explicit `context(action="rebuild", input={}, reasoning="...")` (or passive refresh/molt) is applied.
5. Inspect the focused prompt-plan tests and confirm the three static sections are
   composed in order as one immutable candidate, startup passes the exact same
   object through reconstruction, and a failed final flush restores that plan
   with the prior generation's existing rollback state.

### Expected evidence
- [ ] Step 1: both focused Psyche suites pass, pinning exact manual routing,
      settings discovery, strict owner parsing, and read-only behavior.
- [ ] Step 2: the five manual bodies are nonempty and distinct.
- [ ] Step 3: success is exactly `pad`, `pad_file`, `base_prompt`,
      `base_prompt_file`, `covenant`, `covenant_file`, `comment`, then
      `comment_file`; each row has exactly `key,current,default,configurable,comment`,
      both values are `<redacted>`, an ambient source edit or malformed owner
      document cannot change/make SHOW
      unavailable before successful reconstruction, and invalid input fails
      without rows. No `psyche` action authors, edits, pins, installs, migrates,
      rescans, writes, or reloads — hashes are unchanged.
- [ ] Step 4: file mutation never hot-loads the prompt; the prompt section updates only after an explicit rebuild or passive reconstruction.
- [ ] Step 5: the static plan is read/applied once per reconstruction, cannot be
      mutated through its dataclass/tuple fields, and a failed final flush
      restores the prior plan and the existing rollback state.

### Pass / Fail
Pass when the suite passes, SHOW stays bound to the last applied reconstruction,
and the read-only/no-hot-load observations hold. Fail on ambient-source SHOW
drift, any mutating `psyche` action, or a prompt that reloads from a plain file
edit; record the evidence trail in the task report.
