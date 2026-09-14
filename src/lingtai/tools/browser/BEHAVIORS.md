---
name: browser-internal-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/browser/CONTRACT.md
  - src/lingtai/tools/browser/ANATOMY.md
  - src/lingtai/tools/browser/core.py
  - src/lingtai/tools/browser/port.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  browse subcomponent behavior clause changes, update the guarding LABT here in
  the same change.
---
# Internal Browse Subcomponent Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/browser/CONTRACT.md` (structured success/typed failure
payloads, SSRF-safe redirects, complete-document delivery). Pinned pytest
commands must run from the repo root with the project's Python.

## Behavior BR001 — a fresh browse success never exposes only a first page or partial document

- **id**: BR001
- **title**: a fresh browse success never exposes only a first page or partial document
- **guards**: `browser-internal` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; the `web` capability tests available locally
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_browser_capability.py -q` and capture the outcome.
2. Inspect `WebManager._deliver_browse` and confirm the unified parent always overrides the engine's internal paginated page with the complete joined `snapshot.blocks` text before returning.
3. Confirm a transport success whose body fails to decode to readable text yields no blocks and raises the `NO_TEXT_BLOCKS` extract failure with HTTP provenance (decode-replacement dominated case), and that cleanly decoded text is never reclassified.

### Expected evidence
- [ ] Step 1: the browser capability suite passes (policy/cursor-edge and transport coverage included).
- [ ] Step 2: the parent replaces the internal paginated shape with the complete joined text; the public contract exposes the full document for a fresh success.
- [ ] Step 3: dominated replacement characters plus raw control bytes yield no blocks and the typed extract failure; cleanly decoded text is returned unchanged.

### Pass / Fail
Pass when the suite passes and the complete-document/typed-failure observations hold. Fail on a partial first-page success, on a decode-damaged body being returned as usable content, or on clean text being reclassified; record the evidence trail in the task report.
