---
related_files:
- src/lingtai/tools/context/manual/SKILL.md
- src/lingtai/tools/context/_session_journal.py
- src/lingtai/tools/context/ANATOMY.md
maintenance: |
  Session-journal / molt-history scaffold whose required frontmatter marker and path convention are enforced by the Context molt gate; keep it aligned when that validator changes.
---

# Session-Journal / Molt-History Entry Template

Write one child entry **before every agent-initiated molt** and pass its path as `session_journal_path`:

`knowledge/session-journal/<YYYY-MM-DD>-molt-<molt-count>-<slug>/KNOWLEDGE.md`

The parent `knowledge/session-journal/KNOWLEDGE.md` is routing-only. Add one concise relative-path hook there after writing the child. Use the current pre-molt count from the resident identity section; the entry describes the segment that is ending.

The kernel checks the path inside the workdir, the per-segment `KNOWLEDGE.md` filename, nonempty UTF-8, valid YAML frontmatter with `name` and `description`, and either `type: session-journal` or `session_journal: true`. It performs this check before any context is shed or molt count changes. Keep the body concise and factual; use only the headings that help recovery, such as current task/state, verified results, decisions, artifacts/evidence, open tasks, collaborators, and gotchas. Do not write a transcript or pad missing sections with `None`.

```markdown
---
name: <YYYY-MM-DD>-molt-<molt-count>-<slug>
description: >-
  One-sentence hook — what this session segment did and where it left off.
date: <YYYY-MM-DD>
molt_count: <current count before calling context(action='molt')>
type: session-journal
---

## Current task and state
What mattered, what changed, and the next action.

## Evidence and open work
Paths, IDs, decisions, blockers, pending authority, and essential lessons.
```

The block-scalar `description` form keeps a colon followed by a space valid YAML. The journal preserves the session story; the molt summary should carry only the shortest handoff needed to resume.
