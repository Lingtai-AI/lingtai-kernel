---
timeout: 120
tier: self-contained
---

# Scenario: mail-protocol / receive / polling-listener

> **Leaf:** [README.md](./README.md)

## Setup

- You are a shallow avatar with a fresh working directory.
- You have the `email` capability.
- Your mailbox polling listener is running (started by the kernel on agent init).

---

## Steps

1. **Self-send a message.** Call `email(action="send", address="<your own address>", subject="poll-listener test", message="testing seen-set snapshot")`. This message lands in `inbox/` immediately (self-send shortcut). The polling listener's `_seen` set should include this UUID on its next scan cycle.

2. **Wait briefly.** Use `system(action="nap", seconds=2)` to allow at least one poll cycle (0.5 s interval) to pass.

3. **Check that the message is readable.** Call `email(action="check")` — the message should appear (proving the listener didn't clobber it or re-deliver it).

4. **Read the message.** Call `email(action="read", email_id=[<id>])` — verify the full payload is intact.

5. **Check for duplicates.** Call `email(action="check")` again — there should still be exactly 1 message with this subject (the `_seen` set prevents re-notification; even if the listener re-scans the UUID, it won't fire `on_message` again).

---

## Pass criteria

| # | Criterion | Check |
|---|---|---|
| 1 | Message exists in inbox | `mailbox/inbox/*/message.json` with subject "poll-listener test" |
| 2 | `check` returns it once | Exactly 1 message with this subject in the check results |
| 3 | Second `check` returns same count | Still exactly 1 — no phantom duplicates |
| 4 | Message payload intact | `message` field equals "testing seen-set snapshot" |

**Status:** PASS if all 4 met. FAIL if any violated. INCONCLUSIVE if cannot execute.

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: mail-protocol / receive / polling-listener
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>

## Expected (per anatomy)
Polling listener snapshots existing inbox entries into _seen on startup (or on scan). Self-sent messages land directly in inbox. After one poll cycle, the listener's _seen set contains the UUID, preventing re-notification. Repeated check calls should not produce duplicates.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<why PASS / FAIL / INCONCLUSIVE — reference criterion numbers>

## Artifacts
- mailbox/inbox/<uuid>/message.json
```
