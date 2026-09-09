---
name: psyche-network-rules-reference
last_changed_at: 2026-09-09T00:00:00Z
description: >
  Deep reference for the separate `.rules` heartbeat signal: authorized atomic
  writes, consumption, replacement, persistence, verification, and boundaries.
related_files:
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/kernel/base_agent/__init__.py
- src/lingtai/tools/avatar/manual/SKILL.md
- tests/test_avatar_rules.py
maintenance: |
  Keep this reference synchronized with `_check_rules_file` and Avatar's
  signpost. `.rules` is not a Psyche action; preserve its distinct mechanism,
  consumption, and authorization boundary.
---

# Psyche network-rules reference

`.rules` is a heartbeat signal, not a Psyche action or generic instruction API.
The agent heartbeat (`_check_rules_file`) consumes it, unlike ordinary durable
edits, which require file→`context.rebuild`.

## Write and consume

Write the complete approved UTF-8 body atomically to the explicitly authorized
agent workdir root (POSIX example; use the active shell’s equivalent elsewhere):

```sh
target='/absolute/path/to/authorized-agent'
body='/absolute/path/to/approved-rules.txt'
tmp=$(mktemp "$target/.rules.XXXXXX") &&
  cat "$body" > "$tmp" && mv "$tmp" "$target/.rules"
```

A failed command requires inspection of that exact temporary file; do not blindly
replay or announce success. Confirm each target separately—there is no descendant
broadcast. Do not substitute `system/rules.md` for this live signal.

The next runnable heartbeat reads and unlinks `.rules` before deciding whether to
apply it. Read/unlink failure leaves it unconsumed and stops processing, so disappearance
alone is not proof of success. A nonempty body completely replaces
`system/rules.md` and the protected `rules` section; whitespace-only content is a
consumed no-op. Identical content is consumed without rewrite or flush. Changed
content persists and flushes, logging `rules_loaded`; a canonical-file write failure
aborts before prompt mutation and logs `rules_write_error`.

Boot and every full rebuild/refresh/molt reread `system/rules.md` independently;
a missing or empty canonical file removes the section. Identical comparison
ignores leading/trailing whitespace. A pending `.rules` file is not yet applied,
and an empty signal cannot clear existing rules.
Verify the canonical file on disk and the effective protected section only after
that agent's heartbeat processed the signal (or after later reconstruction).

## Boundary

Writing another reachable agent's `.rules` file is an ordinary filesystem write,
not a dedicated avatar authorization path. Reachability is not permission: only
actual human scope and the target's real accessibility authorize the write. Keep
this distinction visible when operating across agents.
