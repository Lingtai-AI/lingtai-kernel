---
related_files:
- src/lingtai/tools/bash/manual/reference/debugging-cleanup/SKILL.md
- src/lingtai/tools/mcp/skills/mcp-manual/SKILL.md
- src/lingtai/tools/knowledge/manual/SKILL.md
- src/lingtai/tools/email/manual/SKILL.md
- src/lingtai/tools/daemon/manual/reference/cleanup/SKILL.md
- src/lingtai/tools/avatar/manual/SKILL.md
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
3. **Footprint check route** — a copy/pasteable command or script, OR a
   concise pointer to the shared recipe below with this tool's own target
   glob/paths filled in, that reports count and size. The default mode MUST
   NEVER delete, move, or mutate any inspected artifact. A manual is not
   required to embed a full standalone script when the shared recipe already
   covers it — restate only what is tool-specific (target paths/glob, labels).
   If a check also appends its own audit record (item 6), label that write
   explicitly. Only the inspection-only path may be called read-only; audit
   logging is a separate, explicitly selected step.
4. **Recommended audit cadence** — when agents should run the footprint check
   (for example after a daemon-heavy session, weekly for chat addons, or before
   retiring a cron job).
5. **Cleanup protocol** — a safe procedure for deleting or archiving artifacts.
   Destructive cleanup MUST require explicit user consent after a dry-run report.
   This consent requirement is the actual deletion authorization boundary; item
   3's inspection-only default does not create a blanket no-delete policy.
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

## Shared footprint-check recipe

Load this once, then combine these definitions with the target-selection block
from the tool's `Cleanup / Footprint` section in one task-owned Python script.
These are documentation examples, not an importable LingTai API. The inspected
roots, retention rules and approval boundary remain owned by each tool manual.
Inspect only the selected relevant roots; do not scan the entire machine/network
merely because this recipe exists. This POSIX/Windows-neutral Python example
uses installed `rg` for file enumeration (including hidden/ignored files), not
an unbounded Python recursive walk. It does not follow directory symlinks.
Missing `rg`, inaccessible data or failed enumeration is an incomplete audit,
not permission to report zero bytes. A live directory may change during a scan;
the report is an observation, never proof that its contents are disposable.

```python
import json, subprocess, time
from pathlib import Path

def footprint_check(items, *, tool, top_n=20):
    """Inspect explicit selected paths; write nothing and delete nothing."""
    rows = []
    for path in items:
        path = Path(path)
        if path.is_file():
            size = path.stat().st_size
        else:
            result = subprocess.run(
                ["rg", "--files", "--hidden", "--no-ignore", "-0", "--", str(path)],
                capture_output=True, check=False,
            )
            if result.returncode not in (0, 1) or result.stderr:
                raise RuntimeError(f"Incomplete footprint enumeration: {path}")
            size = sum(Path(raw.decode()).stat().st_size
                       for raw in result.stdout.split(b"\0") if raw)
        rows.append((path, size))
    total = sum(size for _, size in rows)
    print(f"{tool} candidates: {len(rows)}; bytes: {total}")
    for path, size in sorted(rows, key=lambda row: row[1], reverse=True)[:top_n]:
        print(f"{size:>12}  {path}")
    return rows, total

def append_cleanup_record(agent_dir, *, tool, dry_run, candidates,
                          bytes_total, human_approved, summary):
    """Explicit audit write; never modifies inspected artifacts."""
    log = Path(agent_dir) / "logs" / "cleanup.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as out:
        out.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool": tool, "dry_run": dry_run, "candidates": candidates,
            "bytes": bytes_total, "human_approved": human_approved,
            "summary": summary,
        }, ensure_ascii=False) + "\n")
```

Run the selected tool's block after these definitions. If recording the audit
is intended, explicitly add the following with that tool's label and a reviewed
non-secret path/glob summary; omitting it leaves the inspection strictly read-only:

```python
append_cleanup_record(agent, tool="<selected tool>", dry_run=True,
                      candidates=len(rows), bytes_total=total,
                      human_approved=False, summary="<reviewed paths/glob summary>")
```

An approved apply step is separate from this recipe: use the tool-owned cleanup
procedure, then record `dry_run=False` and the actual approved paths/count/bytes.
This recipe supplies no destructive command and grants no deletion authority.
