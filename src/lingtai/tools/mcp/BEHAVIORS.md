---
name: mcp-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/mcp/CONTRACT.md
  - src/lingtai/tools/mcp/ANATOMY.md
  - src/lingtai/tools/mcp/__init__.py
  - src/lingtai/tools/mcp/settings.py
  - tests/test_mcp_settings.py
  - src/lingtai/mcp_catalog.json
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when an mcp
  tool behavior clause changes, update the guarding LABT here in the same
  change.
---
# MCP Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/mcp/CONTRACT.md` (info/settings/manual, strict-empty input,
identity surfaced only when present, degraded manual shape). Pinned pytest
commands must run from the repo root with the project's Python.

## Behavior MC001 — info is read-only and any input field fails before the registry is re-read

- **id**: MC001
- **title**: info is read-only and any input field fails before the registry is re-read
- **guards**: `mcp-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with an `mcp_registry.jsonl`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_mcp_settings.py tests/test_mcp_capability.py tests/test_mcp_skill_manuals.py -q` and capture the outcome.
2. Call `mcp(action="info", input={}, reasoning="probe")` and record the result; confirm it reconciles the registry, re-injects prompt XML, and returns `{status, registry_path, registered_count, registered, problems}`.
3. Call `mcp(action="info", input={"extra": 1}, reasoning="probe")` and confirm rejection happens before the registry is re-read or the manual is loaded.
4. Call `mcp(action="settings", input={}, reasoning="probe")`; confirm exact row order `init.addons`, `init.mcp`, exact five-field order, and full current/default redaction for `init.mcp`. Confirm the init file bytes are unchanged.

### Expected evidence
- [ ] Step 1: the mcp capability and skill-manual suites pass, pinning envelope validation, dispatch, and the canonical manual shape.
- [ ] Step 2: `info` returns the health/registry result; each `registered` entry carries `identity` only when a matching identity record with non-empty `accounts` exists.
- [ ] Step 3: any extra `input` key yields `INVALID_ARGUMENT` (`unsupported mcp input field`) before registry I/O; unknown actions render the exact Host-owned pre-migration envelope.
- [ ] Step 4: settings is a bounded whole-inventory SHOW with no writer and no nested MCP projection.

### Pass / Fail
Pass when the suites pass and the read-only/no-input observations hold. Fail on an accepted input field, identity or configuration leakage, a partial inventory, or mutation; record the evidence trail in the task report.
