---
name: daemon-cleanup
description: >
  Nested daemon-manual reference for scope boundaries and daemon footprint
  cleanup: what the manual does not cover, reclaim persistence, and safe cleanup
  of old daemon artifacts.
version: 1.0.1
last_changed_at: "2026-09-04T00:00:00Z"
related_files:
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/tools/daemon/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/forensics/SKILL.md
maintenance: |
  Tracks the daemon footprint cleanup topic it documents; update when that integration changes.
---

# Daemon Cleanup Reference

Nested daemon-manual reference. Open this for daemon footprint audits, old
artifact cleanup, and scope boundaries.

## What the manual does NOT cover

- Provider routing / LLM presets — deferred to a separate spec.
- Cross-process recovery — if your kernel restarted mid-daemon, the folder may show `state=running` indefinitely. Compare `now()` vs `.heartbeat` mtime to detect orphans.
- Automatic folder cleanup — there is none, and molt does not wipe the working
  directory or any `daemons/em-*` run folder; see "Cleanup / Footprint" below
  for the actual, consent-gated deletion procedure.

## Cleanup / Footprint

Daemon runs are intentionally persistent forensic records. Each emanation leaves
`daemons/em-*` under the parent agent, including `daemon.json`, events,
transcript/history, result files, and token ledgers. Do not delete an active
run, and do not delete a run you still need for a report, review, or cost audit.

Footprint check: load the [shared inspection recipe](../../../../skills/manual/reference/cleanup-footprint-contract.md#shared-footprint-check-recipe)
through `skills-manual` → `reference/cleanup-footprint-contract.md`. Combine
its definitions with this tool-specific selection in one task-owned script;
the selection is not a standalone executable. Inspection writes nothing.
Appending `logs/cleanup.jsonl` is the separate, explicitly selected audit step
in that recipe; retain this manual's cleanup/approval rules below.

```python
agent = Path.cwd()  # the relevant agent directory, not a repository root
items = [p for p in (agent / "daemons").glob("em-*") if p.is_dir()]
rows, total = footprint_check(items, tool="daemon", top_n=20)
```

Recommended cadence: after daemon-heavy debugging sessions, before molt if a
large review generated many runs, and monthly for always-on orchestrators.
Cleanup is optional. Before deleting old completed `daemons/em-*` folders, show
the dry-run output to the user and get explicit consent; then append an `apply`
record to `logs/cleanup.jsonl` with the deleted paths/bytes.
