---
timeout: 180
tier: self-contained
---

# Scenario: mail-protocol / send / peer-send

> **Leaf:** [README.md](./README.md)

## Setup

- You are a shallow avatar with a fresh working directory.
- You have the `email` capability.
- Your working directory has a `mailbox/` structure (created by kernel on startup).

---

## Steps

1. **Send to a nonexistent peer.** Call `email(action="send", address="nonexistent-agent-xyz", subject="peer-send test", message="testing handshake failure")`.
2. **Check inbox for bounce notification.** Call `email(action="check")` — a system bounce notification should appear (Mailman injects it on delivery failure).
3. **Read the bounce.** Call `email(action="read", email_id=[<bounce_id>])` — verify it mentions "No agent" or the failure reason.
4. **Inspect outbox is empty.** Use `bash` to run `ls mailbox/outbox/` — the Mailman thread cleaned up after itself.
5. **Inspect sent record exists.** Use `bash` to run `ls mailbox/sent/` — the wrapper archived the sent record even though delivery failed.

---

## Pass criteria

| # | Criterion | Check |
|---|---|---|
| 1 | Bounce notification in inbox | `mailbox/inbox/*/message.json` contains a message whose `from` is `"system"` |
| 2 | Bounce mentions failure reason | `message` field contains the address `"nonexistent-agent-xyz"` |
| 3 | Outbox is empty | `mailbox/outbox/` has no subdirectories |
| 4 | Sent record exists | `mailbox/sent/*/message.json` exists (wrapper always archives) |
| 5 | No `message.json.tmp` in outbox | No `.tmp` residue in outbox |

**Status:** PASS if all 5 met. FAIL if any violated. INCONCLUSIVE if cannot execute.

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: mail-protocol / send / peer-send
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>

## Expected (per anatomy)
Sending to a nonexistent peer should: (1) trigger handshake failure, (2) Mailman injects a bounce notification into the sender's inbox, (3) outbox is cleaned up, (4) wrapper archives sent record regardless of delivery outcome.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<why PASS / FAIL / INCONCLUSIVE — reference criterion numbers>

## Artifacts
- mailbox/inbox/<bounce-uuid>/message.json
- mailbox/sent/<sent-uuid>/message.json
- mailbox/outbox/ (empty)
```
