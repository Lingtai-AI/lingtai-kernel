---
related_files:
- src/lingtai/tools/skills/manual/SKILL.md
maintenance: |
  Cleanup-footprint contract referenced from skills/manual/SKILL.md's cleanup section; update it whenever the footprint/consent rules for skill installation cleanup change.
---

# Cleanup / Footprint Contract for Tool Manuals

Every tool/capability manual that owns persistent files MUST include a section named
`Cleanup / Footprint`. The point is not to force cleanup. The point is to make each
tool responsible for declaring its own footprint and safe cleanup ritual.

## Required fields

A compliant `Cleanup / Footprint` section MUST state:

1. **What this tool leaves behind** — concrete files/directories, caches, logs,
   external scheduler entries, downloaded attachments, subprocess run folders,
   or registry records.
2. **What must never be deleted blindly** — secrets, message records,
   knowledge/skills/system state, active subprocess state, or anything needed
   for recovery/audit.
3. **Footprint check script** — a copy/pasteable command or script that reports
   count and size without deleting anything. The default mode MUST be read-only.
4. **Recommended audit cadence** — when agents should run the footprint check
   (for example after a daemon-heavy session, weekly for chat addons, or before
   retiring a cron job).
5. **Cleanup protocol** — a safe procedure for deleting or archiving artifacts.
   Destructive cleanup MUST require explicit user consent after a dry-run report.
6. **Cleanup record** — every cleanup/audit script run SHOULD append a JSONL
   record to `logs/cleanup.jsonl` under the relevant agent/workdir, including at
   minimum: timestamp, tool/manual name, dry-run vs apply, candidate count,
   bytes, paths or glob summary, and whether human approval was obtained.

## Consent rule

Cleanup is never mandatory and never implicit. A tool manual may recommend a
cleanup, but before deletion the agent must:

1. run/show the read-only footprint report,
2. explain what would be removed and what would be kept,
3. ask the user for explicit consent, and
4. only then run the destructive step.

If the user is unavailable, stop after the dry-run report.

## Self-audit rule

When an agent reads a tool manual for setup, troubleshooting, long-running
operation, or anything involving disk/privacy footprint, it should run (or at
least consider running) that manual's footprint check. If the footprint is
large, stale, or privacy-sensitive, report it and ask whether to clean.

## Standard cleanup record snippet

Manuals may adapt this snippet in their own footprint scripts:

```python
import json, time
from pathlib import Path

def append_cleanup_record(agent_dir: Path, *, tool: str, dry_run: bool,
                          candidates: int, bytes_total: int,
                          human_approved: bool, summary: str) -> None:
    log = agent_dir / "logs" / "cleanup.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.open("a", encoding="utf-8").write(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool,
        "dry_run": dry_run,
        "candidates": candidates,
        "bytes": bytes_total,
        "human_approved": human_approved,
        "summary": summary,
    }, ensure_ascii=False) + "\n")
```
