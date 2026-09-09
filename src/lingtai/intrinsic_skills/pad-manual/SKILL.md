---
name: pad-manual
description: |
  Read before editing the living Pad, pinning references, or preparing Pad state
  for rebuild/molt.
version: 2.0.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/pad/__init__.py
- src/lingtai/tools/pad/_pad.py
- src/lingtai/tools/pad/CONTRACT.md
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
- src/lingtai/tools/context/manual/SKILL.md
maintenance: |
  Keep the manual-only Psyche route, generic file ownership, no-hot-load rule,
  delayed activation, pinned-reference hazards, and context-manual route aligned
  with Pad code and Contract.
---

# Pad Manual

Pad is the living index in `system/pad.md` plus the pinned read-only paths in
`system/pad_append.json`.

## Public call

```text
psyche(action="pad", input={}, reasoning="load Pad guidance")
```

This is the only public Pad call and only returns this manual. It has no append,
edit, load, or reload action and no compatibility alias.

## Edit Pad sources

Use the generic `file` owner: rewrite with
`file(action="write", input={"file_path": "system/pad.md", "content": "..."}, reasoning="rewrite Pad")`
or make a bounded exact change with
`file(action="edit", input={"file_path": "system/pad.md", "old_string": "...", "new_string": "...", "replace_all": null}, reasoning="edit Pad")`.
Neither call hot-loads the prompt. Apply one
`context(action="rebuild", input={}, reasoning="apply Pad change")` when immediate
activation is needed.

Keep Pad to current goal, state, next action, blockers, collaborators, and useful
pointers; archive completed narrative in knowledge.

## Pinned references

`system/pad_append.json` is an ordinary JSON array of workdir-relative or
absolute paths. Edit it with the same full `file` envelope, for example:

```text
file(action="write", input={"file_path": "system/pad_append.json",
  "content": "[\"notes/design.md\", \"src/api.py\"]"}, reasoning="pin references")
```

Write `[]` to clear it. File writes do not validate the pinned list or its
aggregate size; confirm the paths and UTF-8 contents yourself. Reconstruction
rereads every listed file and appends its
contents to Pad, so list edits and pinned-file edits appear only after rebuild,
refresh, or molt. A missing path is reported as `append_not_found` at compose time,
not rejected at write time; pinned text also consumes context budget, so keep the
list short and text-only.

Before molt, make durable Pad state accurate, rebuild only if needed in the
current context, then follow `context-manual` for journal/summary/molt procedure.
Leave root `summarize` false for exact guidance.
