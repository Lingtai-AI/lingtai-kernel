---
timeout: 180
---

# Scenario: capabilities / mail / atomic-write

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 6

---

## Setup

- You are an agent with the `email` capability.
- Your working directory has a `mailbox/` structure.
- A second agent (sibling) exists in the same `.lingtai/` network.
- You have the `bash` tool for filesystem inspection.

---

## Steps

1. **Send an email to the sibling.** Call `email(action="send", address="<sibling>", subject="atomic test", message="checking atomic write")`.
2. **Immediately check for `.tmp` files.** Use `bash` on the sibling's working directory: `find sibling_dir/mailbox/inbox -name "*.tmp"`. There should be none — the atomic rename is complete by the time the tool returns.
3. **Verify the message exists.** Use `bash`: `ls sibling_dir/mailbox/inbox/` — exactly one UUID directory with `message.json`.
4. **Verify the message is valid JSON.** Use `read` on `sibling_dir/mailbox/inbox/<uuid>/message.json` — it must parse and contain `from`, `to`, `subject`, `message`, `_mailbox_id`, `received_at`.
5. **Check your outbox.** Use `bash`: `ls mailbox/outbox/` — should be empty (mailman cleaned up).
6. **Check your sent folder.** Use `bash`: `ls mailbox/sent/` — one UUID directory with `message.json`.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable.

| # | Criterion | Check |
|---|---|---|
| 1 | No `.tmp` files remain in recipient inbox | `find ... -name "*.tmp"` returns empty |
| 2 | Exactly one inbox entry exists | `ls recipient/mailbox/inbox/ \| wc -l` equals 1 |
| 3 | `message.json` is valid JSON | Parsed by `read` tool without error |
| 4 | `_mailbox_id` matches directory name | `message.json["_mailbox_id"]` equals the UUID dir name |
| 5 | `received_at` is present and ISO-8601 | `received_at` field exists |
| 6 | Outbox is empty | `mailbox/outbox/` has no subdirectories |

**Status:**
- **PASS** — all 6 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute.

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: capabilities / mail / atomic-write
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
Delivery uses tmp+os.replace pattern. No .tmp files remain after delivery. The polling listener only ever sees complete message.json files.

## Observed
<verbatim tool outputs, find results, file contents>

## Verdict reasoning
<one paragraph — reference specific criterion numbers>

## Artifacts
- recipient/mailbox/inbox/<uuid>/message.json
- mailbox/sent/<uuid>/message.json
```
