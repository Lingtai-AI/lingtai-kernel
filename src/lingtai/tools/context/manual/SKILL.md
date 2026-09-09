---
name: context-manual
description: |
  Use for an unfamiliar or consequential context operation, session journaling, handoff, or context-loss recovery; routine calls can follow the schema.
version: 2.2.0
last_changed_at: "2026-09-08T00:00:00Z"
related_files:
- src/lingtai/tools/context/__init__.py
- src/lingtai/tools/context/_molt.py
- src/lingtai/tools/context/_session_journal.py
- src/lingtai/tools/system/summarize.py
- src/lingtai/agent.py
- src/lingtai/tools/psyche/CONTRACT.md
maintenance: |
  This package is the canonical Context manual source and runtime-installed owner.
  Update it when the tool/capability behavior changes.
---

# Context Manual

Use the schema for routine calls; open this manual for unfamiliar workflows or recovery risk. Context has four operations:

- `summarize` records compact replacements for selected old tool results; it does not rebuild provider context.
- `rebuild` recomposes all canonical prompt sources, applies summaries, then requests provider replay. Bare `input={}` is valid; durable file edits do not hot-load.
- `molt` sheds conversation while retaining durable files and stores. It is an irreversible boundary, not housekeeping.
- `manual` returns the installed `context-manual` without changing context.

## Shortest useful path

When current `meta_guidance` says a consumed result needs a posteriori compaction, inspect it first, then use `context(action="summarize", input={"items": [...]})`; do not use this as routine cleanup. Use one `context(action="rebuild", input={})` to apply pending summaries or a durable prompt edit now. The [summarize reference](reference/summarize-manual/SKILL.md) owns selection, recovery, and pressure guidance.

Before a deliberate molt:

1. Make the task, authority, blockers, live IDs, evidence paths, and next action recoverable. Update only stores that changed.
2. Write the session-journal child and parent-index pointer. It must be `knowledge/session-journal/<entry>/KNOWLEDGE.md`, nonempty UTF-8, with valid `name`/`description` frontmatter and `type: session-journal` or `session_journal: true`. Use the [journal scaffold](assets/session-journal-entry-template.md) if useful.
3. Pass its path as `session_journal_path` and write the shortest sufficient handoff: active state, verified results, pending authority, next steps, live IDs, artifact/evidence paths, blockers, and lessons. Use the [optional handoff scaffold](assets/molt-template.md) only when it lowers real risk.
4. Call `context(action="molt", ...)`. The journal is validated before shedding; invalid input leaves molt count/history untouched. `keep_tool_calls` refuses unknown IDs; `keep_last` preserves a suffix and complete tool-call batches. The schema is authoritative.

Molt preserves durable files and source reconstruction: it archives/snapshots the old conversation, rebuilds the fresh prompt, and publishes a post-molt notification. It does not delete durable stores. Molt for sustained pressure, an explicit human reset, or confusion—not merely task completion. Tending stores need not create gratuitous edits or new skills.

## After a molt

Read the post-molt notification/`summary_path`, current prompt/Pad, journal index, and new producer messages. Treat the briefing as context, not a command to run blindly. Re-check IDs and evidence paths. After reorientation, explicitly choose `continue`, `defer`, or `obsolete` and dismiss the channel with a reason, for example `notification(action="dismiss_channel", input={"channel":"post-molt","force":null,"reason":"continue: ..."}, reasoning="...")`; use the Notification manual/schema for its exact fields. Resume through the originating channel/tool; a saved briefing or provider replay is not proof of delivery.

The action `summarize` records Context history; the optional root `summarize` boolean only presents results. They are not the same input. Use `psyche(action="manual", input={}, reasoning="load durable-store routes")` for store ownership; generic writes are through `file`, followed by one rebuild when needed.
