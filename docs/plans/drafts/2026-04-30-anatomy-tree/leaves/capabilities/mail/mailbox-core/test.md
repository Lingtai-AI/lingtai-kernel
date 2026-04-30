---
timeout: 180
---

# Scenario: capabilities / mail / mailbox-core

> **Timeout:** 2 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 5

---

## Setup

- You are an agent with a working directory containing `mailbox/` (created by the kernel on startup or by the `email` capability setup).
- You have the `email` capability and `bash` capability.

---

## Steps

1. **Verify mailbox root exists.** Use `bash` to run `ls -la mailbox/` — confirm the directory exists.
2. **Verify key metadata files exist (or are creatable).** Use `bash` to run `test -f mailbox/read.json && echo "exists" || echo "missing"` and `test -f mailbox/contacts.json && echo "exists" || echo "missing"`. (Both may be absent on a fresh agent — that is valid lazy-creation.)
3. **Verify subdirectory structure.** Use `bash` to run `ls -d mailbox/inbox mailbox/outbox mailbox/sent mailbox/archive 2>/dev/null | wc -l`. Note: these may not all exist on a fresh agent (lazy-created). Confirm that at minimum `mailbox/` itself exists.
4. **Send a self-addressed email to trigger directory creation.** Call `email(action="send", address="<your own address>", subject="mailbox-core test", message="verifying structure")`.
5. **Verify post-send structure.** Use `bash` to run `ls -d mailbox/inbox mailbox/outbox mailbox/sent 2>/dev/null` — after sending, at least `inbox/` and `sent/` should exist. Use `bash` to run `ls mailbox/inbox/` to confirm a UUID directory with `message.json`.
6. **Verify contacts.json is valid JSON (if exists).** Use `bash` to run `test -f mailbox/contacts.json && python3 -c "import json; json.load(open('mailbox/contacts.json'))" && echo "valid" || echo "absent"`.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable.

| # | Criterion | Check |
|---|-----------|-------|
| 1 | `mailbox/` directory exists | `ls mailbox/` exits 0 |
| 2 | After send, `mailbox/inbox/` exists | `ls mailbox/inbox/` exits 0 |
| 3 | After send, `mailbox/sent/` exists | `ls mailbox/sent/` exits 0 |
| 4 | Inbox contains UUID dir with `message.json` | `mailbox/inbox/*/message.json` exists and is valid JSON |
| 5 | `read.json` is valid JSON (if present) | `python3 -c "import json; json.load(open('mailbox/read.json'))"` exits 0 (or file absent) |
| 6 | `contacts.json` is valid JSON (if present) | Same check for contacts |

**Status:**
- **PASS** — all 6 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (e.g. no email capability).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: capabilities / mail / mailbox-core
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
2. <what you did>
...

## Expected (per anatomy)
mailbox/ root exists after email capability setup; inbox/, sent/ created on first send; each message is a UUID directory containing message.json; metadata files are valid JSON when present.

## Observed
<verbatim tool outputs, ls output>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- mailbox/inbox/<uuid>/message.json
- mailbox/sent/<uuid>/message.json
```
