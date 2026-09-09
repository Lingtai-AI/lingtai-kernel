---
name: knowledge-manual
description: >
  Read before creating or organizing private durable knowledge
  (`knowledge/<name>/KNOWLEDGE.md`) or cross-references; use it for local memory,
  not portable procedures.
version: 1.0.0
last_changed_at: "2026-09-09T00:00:00Z"
related_files:
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/tools/knowledge/__init__.py
- src/lingtai/tools/knowledge/ANATOMY.md
- src/lingtai/tools/knowledge/CONTRACT.md
maintenance: |
  Keep the routed private-store/source ownership, catalog rebuild timing,
  file-operation examples, nesting rule, and cleanup consent route aligned with
  the Knowledge capability.
---

# Knowledge Manual

Knowledge is private, local long-term memory: facts, decisions, observations,
paths, mail context, and operational lessons. It has no public capability tool;
`psyche` only returns this signpost. Skills are portable procedures, while
knowledge may contain private paths, IDs, and logs. Reusable how-to belongs in a
skill; shared skills must not point inward to private knowledge.

## Layout and catalog

Each entry is `<agent>/knowledge/<name>/KNOWLEDGE.md`, optionally with
`references/`, `scripts/`, `assets/`, or `notes/`. Its YAML needs `name` and
`description`. The prompt receives only each entry's name, description, and
location; read the body on demand. Setup/refresh and every full rebuild rescan
the store and recompose the catalog.

A top-level entry may be a short routing/index parent with nested children. Keep
one hook and relative child path per child, and put the substance in the child;
use the Skills manual's nested reference pattern for the catalog shape.

## Public call and first action

```text
psyche(action="knowledge", input={}, reasoning="load knowledge guidance")
```

The strict-empty call creates, edits, searches, rescans, and loads nothing. For an
ordinary schema-sufficient call no ritual manual reload is needed; read this page
when authoring or reorganizing an unfamiliar store.

## Author, edit, read, apply

Use the generic `file` owner with complete envelopes:

```text
file(action="write", input={"file_path": "knowledge/<name>/KNOWLEDGE.md", "content": "---\nname: <name>\ndescription: ...\n---\n..."}, reasoning="write knowledge")
file(action="edit", input={"file_path": "knowledge/<name>/KNOWLEDGE.md", "old_string": "...", "new_string": "...", "replace_all": null}, reasoning="edit knowledge")
file(action="read", input={"file_path": "<location>", "offset": null, "limit": null, "max_chars": null}, reasoning="read knowledge")
```

Writing does not hot-load the catalog. Apply one
`context(action="rebuild", input={}, reasoning="refresh knowledge catalog")`, or
let passive refresh/molt reconstruction apply it. Psyche's
`settings/psyche.json` does not configure Knowledge and there is no Knowledge
settings, set, reset, migration, or writeback action.

## Cross-references and cleanup

Prefer move-stable relative links such as `../architecture/KNOWLEDGE.md`. A
knowledge entry may point outward to a reusable Skills manual, never the reverse.

Knowledge is durable memory, not cache: do not delete it just to save space.
Cleanup normally consolidates, renames, or archives stale entries. For a footprint
report, use `skills-manual` → `reference/cleanup-footprint-contract.md` and
select only `knowledge/`; inspection writes nothing. The shared recipe’s optional
audit append is a separate write, not an automatic side effect of inspection. Show the dry-run,
explain what would change, obtain explicit user consent, and record an approved
cleanup in `logs/cleanup.jsonl` before rebuilding the catalog.
