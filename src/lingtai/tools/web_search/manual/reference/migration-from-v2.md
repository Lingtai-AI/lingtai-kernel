---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/operation-contract.md
maintenance: |
  Keep this historical note bounded. Current Web action and schema promises
  belong to the parent manual and operation contract, not this migration note.
---
# Historical migration note

The old v2 utility names were consolidated into the installed `web-manual`
bundle. The current public surface is one `web` capability with strict
`search`, `browse`, `settings`, and `manual` actions; the retained external
tiers and helper scripts are procedures, not additional actions.

For current input keys, defaults, output delivery, provider routing, and refusal
semantics use [the operation contract](operation-contract.md). For a helper's
legacy CLI flags and compatibility details, inspect
`../scripts/extract_page.py` directly; do not infer public `web` behavior from
historical v2 names.
