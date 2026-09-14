---
name: plugin-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/plugin/CONTRACT.md
  - src/lingtai/tools/plugin/ANATOMY.md
  - src/lingtai/tools/plugin/__init__.py
  - src/lingtai/tools/plugin/settings.py
  - tests/test_plugin_tool.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  plugin tool behavior clause changes, update the guarding LABT here in the same
  change.
---
# Plugin Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/plugin/CONTRACT.md` (read-only info/settings/manual, skipped
entries explain non-mounting). Pinned pytest commands must run
from the repo root with the project's Python.

## Behavior PL001 — info reports health and every non-mounting component explains itself in skipped

- **id**: PL001
- **title**: info reports health and every non-mounting component explains itself in skipped
- **guards**: `plugin-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with one declared plugin
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_plugin_tool.py -q` and capture the outcome.
2. Call `plugin(action="info", input={}, reasoning="probe")` and record the result; confirm `registered`, `discovered`, `paths`, and `problems` are present and that no file was created or modified.
3. Declare a plugin with an escaping path or a server name outside the registry grammar and re-run `info`; confirm the plugin lands in `skipped` with a `{component, reason}` entry.
4. Call `plugin(action="settings", input={}, reasoning="probe policy")`; confirm the one `manifest.plugins` row has exactly `key`, `current`, `default`, `configurable`, and `comment`, both values are redacted, and the comment targets `plugin-manual#plugin-registration-roots`. Confirm non-empty input fails and a second `info` still returns the unchanged snapshot.

### Expected evidence
- [ ] Step 1: the plugin-tool suite passes, pinning the two-tier mount contract, lifecycle, and read-only info/settings/manual surface.
- [ ] Step 2: `info` returns the health snapshot and performs zero writes (the capability owns no state).
- [ ] Step 3: every non-mounting component appears in `skipped` with its reason; no plugin silently vanishes from the report.
- [ ] Step 4: settings reveals no local roots, advertises the authorized manual procedure, accepts only `{}`, and leaves the ordinary `info` behavior unchanged.

### Pass / Fail
Pass when the suite passes and the read-only/self-explaining observations hold.
Fail on a mutating action, settings path disclosure or extra row field, a silent
non-mount (no `skipped` entry), or a missing `paths`/`problems` report; record
the evidence trail in the task report.
