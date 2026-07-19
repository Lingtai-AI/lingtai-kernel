---
name: knowledge-manual
description: >
  Concise guide to the `knowledge` capability: private agent-owned memory in
  `<agent>/knowledge/<name>/KNOWLEDGE.md`, progressive disclosure through the
  prompt catalog, nested knowledge folders, routing/index entries with
  sub-knowledge children, and cross-references between entries. Read this when you
  need to create, organize, or load private knowledge, lay out a routing parent
  over related entries, or explain how knowledge differs from portable skills.
version: 1.0.0
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- src/lingtai/tools/knowledge/__init__.py
- src/lingtai/tools/knowledge/ANATOMY.md
- src/lingtai/tools/knowledge/CONTRACT.md
maintenance: |
  Tracks the routed source/resources it summarizes; update when the underlying capability or its sub-references change.
---

# The Knowledge Capability

Knowledge is an agent's private long-term memory. It is for facts, decisions, observations, local paths, mail context, and operational lessons that are useful to this agent but are not necessarily portable to every other agent.

The private-memory capability is named `knowledge`, and it registers exactly one
tool, also named `knowledge`.

## Knowledge vs skills

Knowledge and skills are **isomorphic in layout and progressive disclosure**:
each entry is a folder with a marker file (`KNOWLEDGE.md` here, `SKILL.md`
there), the prompt carries only the catalog, and a top-level entry may act as a
routing/index parent over nested children. They are physically separate stores
with different audiences:

| Term / path | Meaning |
|---|---|
| `knowledge` tool / capability | Private per-agent durable memory catalog. |
| `<agent>/knowledge/<name>/KNOWLEDGE.md` | One private knowledge entry. |
| `skills` tool / capability | Catalog of reusable portable procedures. |
| `.library/intrinsic`, `.library/custom`, `.library_shared` | Skill shelves holding `SKILL.md` files — never private `knowledge` entries. |

Knowledge is **private, local, and non-portable** by default: it may reference
agent-local paths, mail IDs, and logs. Skills are **reusable, shareable, and
portable**.

**Direction matters:** private knowledge may point outward to skills; shared
skills must not point inward to private knowledge paths, mail IDs, or local
logs. If content is a reusable how-to another agent could apply without your
private context, write a skill instead.

## Layout

Each entry is a folder under `<agent>/knowledge/` with a `KNOWLEDGE.md` file:

```text
<agent>/knowledge/
└── <name>/
    ├── KNOWLEDGE.md
    ├── references/
    ├── scripts/
    ├── assets/
    └── notes/
```

`KNOWLEDGE.md` starts with YAML frontmatter:

```markdown
---
name: <name>
description: One short sentence shown in the prompt catalog.
version: 1.0.0
---

# Title

Full notes live here.
```

Required fields are `name` and `description`. Supporting files are optional and can be any useful text, script, data sample, log, or asset.

## Progressive disclosure

The system prompt only receives a compact catalog: each entry's `name`, `description`, and `location`. The body of `KNOWLEDGE.md` and supporting files stay on disk until you explicitly read them. This keeps the prompt small while still making the memory discoverable.

Call `knowledge({"action": "info"})` to rescan the catalog and refresh the prompt section, then use `read` on the listed `location` when an entry becomes relevant.

## Nesting and sub-knowledge

A top-level entry may act as a **routing/index parent** with nested children that
hold the substance — exactly the nested-reference pattern documented in the
skills manual.

For the routing/index parent pattern — when to use it, how the parent stays a
short scannable index, how children carry the detail, relative child paths,
keeping the catalog in sync — **read the skills manual's nested reference
section** (`.library/intrinsic/capabilities/skills/SKILL.md`, "Nested
skill/reference pattern for umbrella manuals") and apply the same shape with
`KNOWLEDGE.md` in place of `SKILL.md`.

Compact example of a routing parent with sub-knowledge children — a project's
incident log:

```text
knowledge/project-x-incidents/
├── KNOWLEDGE.md                                   # routing/index ONLY
├── 2026-05-13-cache-stampede/KNOWLEDGE.md         # child — the detail
└── 2026-05-21-token-leak/KNOWLEDGE.md             # child
```

The parent `KNOWLEDGE.md` is a short index: one line per child with a hook and the
child's relative path; each child holds the full write-up. Keep names
filesystem-safe and descriptive, point at children by relative path, and use
nesting to group related entries, not to hide information.

## Cross-references

Knowledge entries may reference one another by relative path or by catalog name. Prefer links that remain valid if the whole agent directory moves:

```markdown
See also: ../architecture/KNOWLEDGE.md
See also: ../../people/reviewers/KNOWLEDGE.md
```

Knowledge may also reference skills when a reusable procedure exists:

```markdown
For the repeatable workflow, read `.library/intrinsic/capabilities/skills/SKILL.md`.
```

## When to create knowledge

Create or update a knowledge entry when the information is useful beyond the current turn but is not a portable procedure:

- project-specific decisions and rationale;
- collaborator preferences and review history;
- local repo paths, branch relationships, and known gotchas;
- incident notes and debugging evidence;
- conclusions from research that are specific to this agent's work.

## Cleanup / Footprint

Knowledge entries live under `knowledge/<name>/KNOWLEDGE.md` plus supporting
files. They are durable memory, not cache. Cleanup usually means consolidation,
renaming, or archiving stale entries after review; never delete knowledge just to
save space unless the user explicitly agrees after a dry-run report and the content is backed up or no
longer useful.

Footprint check (read-only, records the audit):

```bash
python3 - <<'PY'
import json, time
from pathlib import Path
agent = Path.cwd(); root = agent / "knowledge"
entries = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
def size(p): return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
rows = [(p, size(p)) for p in entries]
total = sum(s for _, s in rows)
print(f"knowledge entries: {len(rows)}; bytes: {total}")
for p, s in sorted(rows, key=lambda r: r[1], reverse=True)[:30]: print(f"{s:>12}  {p.name}")
log = agent / "logs" / "cleanup.jsonl"; log.parent.mkdir(parents=True, exist_ok=True)
log.open("a", encoding="utf-8").write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": "knowledge", "dry_run": True, "candidates": len(rows), "bytes": total, "human_approved": False, "summary": "knowledge footprint audit"}) + "\n")
PY
```

Recommended cadence: before molt if knowledge sprawl is confusing, after major
projects, and monthly for long-lived agents. If cleanup is approved with explicit user consent, record the
entries consolidated/removed in `logs/cleanup.jsonl` and update the catalog with
`knowledge(action="info")`.
