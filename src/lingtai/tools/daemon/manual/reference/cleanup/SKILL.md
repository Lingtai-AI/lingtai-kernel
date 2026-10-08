---
name: daemon-cleanup
description: >
  Daemon footprint scope, reclaim persistence, and consent-gated cleanup of
  old artifacts.
version: 1.0.2
last_changed_at: "2026-09-08T00:00:00Z"
related_files:
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/tools/daemon/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/forensics/SKILL.md
maintenance: |
  Keep this reference aligned with the daemon artifact layout and the shared
  footprint recipe; update the owner route when either changes.
---

# Daemon Cleanup Reference

Use this reference for a footprint audit or explicitly authorized deletion.
It does not own provider routing or cross-process recovery. There is no
automatic folder cleanup: `reclaim` stops processes but leaves evidence, and
molt clears conversation context while retaining durable stores, including
daemon run folders.

## Cleanup / Footprint

Each run leaves `daemons/em-*` with `daemon.json`, events, transcript/history,
result files, and token records. Do not delete an active run or evidence still
needed for a report, review, or cost audit.

Call `psyche(action="skills", input={}, reasoning="read cleanup guidance")`,
then read `reference/cleanup-footprint-contract.md` beside that installed manual.
Use its shared inspection recipe; combine
its definitions with this tool-specific selection in one task-owned script.
Inspection writes nothing. Appending `logs/cleanup.jsonl` is a separate,
explicitly selected audit step.

```python
agent = Path.cwd()
items = [p for p in (agent / "daemons").glob("em-*") if p.is_dir()]
rows, total = footprint_check(items, tool="daemon", top_n=20)
```

Before deleting old completed folders, show the dry-run output and obtain
explicit human consent. Then append an `apply` record to
`logs/cleanup.jsonl` with the deleted paths and bytes. Never delete outside the
approved selection or use cleanup to hide a failed run.
