---
timeout: 180
tier: self-contained
---

# Scenario: mail-protocol / send / self-send

> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 7

---

## Setup

- You are a shallow avatar with a fresh working directory.
- You have the `email` capability.
- Your working directory has a `mailbox/` structure (created by the kernel on startup).

---

## Steps

1. **Send a self-addressed email.** Call `email(action="send", address="<your own address>", subject="self-send test", message="anatomy verification")`.
2. **Check your inbox.** Call `email(action="check")` — verify the message appears in the listing.
3. **Inspect the filesystem.** Use `bash` to run `ls mailbox/inbox/` and confirm a UUID directory exists containing `message.json`.
4. **Read the message file.** Use `read` to open `mailbox/inbox/<uuid>/message.json` — verify the schema matches the received-message format (has `_mailbox_id`, `from`, `to`, `received_at`, `identity`).
5. **Verify no outbox residue.** Use `bash` to run `ls mailbox/outbox/` — confirm it is empty (the Mailman thread cleaned up after itself).
6. **Verify sent record exists.** Use `bash` to run `ls mailbox/sent/` — confirm a UUID directory exists containing `message.json` (the wrapper always archives sent records, even for self-send).

---

## Pass criteria

All of the following must hold. Each is filesystem-observable — no LLM judgment required.

| # | Criterion | Check |
|---|---|---|
| 1 | Message arrived in inbox | `mailbox/inbox/*/message.json` exists and is valid JSON |
| 2 | `from` matches self | `from` field in `message.json` equals your own address |
| 3 | `to` contains self | `to` array contains your own address |
| 4 | `received_at` is present | `received_at` field exists and is ISO-8601 |
| 5 | `identity` is present | `identity` object exists with `agent_id` and `agent_name` |
| 6 | `_mailbox_id` matches directory name | `message.json["_mailbox_id"]` equals the UUID directory name |
| 7 | Outbox is empty | `mailbox/outbox/` contains no subdirectories |
| 8 | No `message.json.tmp` in inbox | No `.tmp` files exist under `mailbox/inbox/` (self-send does not use atomic write) |
| 9 | Sent record exists | `mailbox/sent/*/message.json` exists (wrapper always archives sent) |

**Status:**
- **PASS** — all 9 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (missing capability, environment error).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: mail-protocol / send / self-send
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
2. <what you did>
...

## Expected (per anatomy)
Self-send bypasses FilesystemMailService transport, writes directly to mailbox/inbox/{uuid}/message.json, and calls _wake_nap("mail_arrived") with zero polling latency.

## Observed
<verbatim tool outputs, file contents, ls output>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- mailbox/inbox/<uuid>/message.json
- mailbox/outbox/ (empty)
```
