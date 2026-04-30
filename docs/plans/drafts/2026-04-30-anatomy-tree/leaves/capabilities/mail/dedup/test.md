---
timeout: 180
---

# Scenario: capabilities / mail / dedup

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 7

---

## Setup

- You are an agent with the `email` capability.
- Your working directory has a `mailbox/` structure (created by kernel on startup).
- A second agent (sibling) exists in the same `.lingtai/` network with a fresh working directory and `email` capability.
- You have the `bash` tool for filesystem inspection.

---

## Steps

1. **Send an email to the sibling.** Call `email(action="send", address="<sibling>", subject="dedup test", message="identical payload for dedup check")`.
2. **Send the same email again.** Identical `address`, `subject`, `message` — this is the 2nd send (still within the free-pass window).
3. **Send the same email a 3rd time.** Identical payload. This call should return `{"status": "blocked", "warning": "..."}` because `count ≥ _dup_free_passes`.
4. **Check the return value.** Verify the 3rd call returns `status: "blocked"`.
5. **Inspect the sibling's inbox.** Use `bash` on the sibling's working directory: count the number of `mailbox/inbox/*/message.json` files. There should be exactly 2.
6. **Inspect your sent folder.** Use `bash`: count `mailbox/sent/*/message.json`. There should be exactly 2 (the wrapper archives sent records only for successful sends, not blocked ones).

---

## Pass criteria

All of the following must hold. Each is filesystem-observable or tool-return-observable.

| # | Criterion | Check |
|---|---|---|
| 1 | 1st send succeeds | Return value has `status: "sent"` |
| 2 | 2nd send succeeds | Return value has `status: "sent"` |
| 3 | 3rd send blocked | Return value has `status: "blocked"` with a `warning` string |
| 4 | Sibling inbox has exactly 2 entries | `ls sibling_dir/mailbox/inbox/ \| wc -l` equals 2 |
| 5 | Your sent folder has exactly 2 entries | `ls mailbox/sent/ \| wc -l` equals 2 |
| 6 | No outbox residue | `mailbox/outbox/` is empty or non-existent |

**Status:**
- **PASS** — all 6 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (missing capability, environment error, no sibling available).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: capabilities / mail / dedup
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
2. <what you did>
...

## Expected (per anatomy)
Identical consecutive messages to the same recipient are allowed up to 2 times (free passes). The 3rd identical send is blocked with status "blocked" and a warning. The blocked send produces no outbox entry and no sent record.

## Observed
<verbatim tool outputs, file contents, ls output>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- mailbox/sent/ (2 entries)
- Sibling mailbox/inbox/ (2 entries)
```
