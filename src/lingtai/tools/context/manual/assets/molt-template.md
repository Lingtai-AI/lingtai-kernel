---
related_files:
- src/lingtai/tools/context/manual/SKILL.md
- tests/test_skills.py
maintenance: |
  Optional consequential-molt handoff scaffold referenced by context-manual; keep its recovery fields aligned with the owner manual without making the scaffold mandatory.
---

# Consequential Molt Handoff Template

Use this optional scaffold when a task is unfamiliar, long-running, collaborative, externally committed, or otherwise hard to reconstruct. A routine molt may use a few precise sentences. Write only useful facts; do not fill absent fields with `None`, and do not target a fixed length.

Include whichever of these fields reduce recovery risk:

- **Active task and state** — the exact objective, current status, and first next action.
- **Authority and collaborators** — who may decide what, pending approvals/replies, and the originating channel.
- **Verified work and open work** — completed results, blockers, unresolved questions, and the evidence that supports them.
- **Live work** — active job/thread/daemon IDs, their owners, and how to check or stop them.
- **Paths and lessons** — absolute artifact/log/report paths, relevant journal/knowledge/skill paths, and essential gotchas.
- **Boundaries** — side effects still forbidden, secrets to avoid, and any exact command or recipient constraint.

## Before calling molt

- Update only durable stores whose content changed.
- Write the required session-journal child first and add its relative path to the parent index.
- Pass that child path as `session_journal_path`.
- Make the next agent's first few minutes obvious, then call `context(action="molt", ...)`.

The kernel validates the journal before shedding context. Durable files remain recoverable; the handoff is a pointer and decision aid, not a replacement for those sources.
