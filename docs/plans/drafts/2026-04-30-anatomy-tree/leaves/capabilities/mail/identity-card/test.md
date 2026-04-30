---
timeout: 180
---

# Scenario: capabilities / mail / identity-card

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 6

---

## Setup

- You are an agent with the `email` capability.
- Your working directory has a `mailbox/` structure.
- You know your own `agent_id` and `agent_name` (from identity or `system(action="show")`).
- You have the `bash` and `read` tools.

---

## Steps

1. **Send a self-addressed email.** Call `email(action="send", address="<your own address>", subject="identity test", message="checking identity card fields")`.
2. **Read the raw message from disk.** Use `bash` to find the inbox entry: `ls mailbox/inbox/` to get the UUID, then `read` on `mailbox/inbox/<uuid>/message.json`.
3. **Verify the `identity` object.** In the raw `message.json`, confirm:
   - `identity.agent_id` equals your agent_id
   - `identity.agent_name` equals your agent_name
   - `identity.address` is present
   - `identity.admin` is present (dict for AI agents, null for humans)
   - `identity.language` is present
4. **Check via email tool (summary view).** Call `email(action="check")` — verify the returned email summary contains:
   - `sender_name` matching your agent_name
   - `sender_agent_id` matching your agent_id
   - `sender_language` matching your language
   - `is_human` is a boolean
5. **Read via email tool (full view).** Call `email(action="read", email_id=["<uuid>"])` — verify identity fields are present in the full output.
6. **Check sent record.** Use `bash`: `cat mailbox/sent/*/message.json | python3 -c "import sys,json; d=json.load(sys.stdin); print('identity' in d)"` — should print `True`.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable or tool-return-observable.

| # | Criterion | Check |
|---|---|---|
| 1 | `identity` object present in raw message.json | `message.json["identity"]` is a dict |
| 2 | `identity.agent_id` matches your ID | Value equals your `agent_id` |
| 3 | `identity.agent_name` matches your name | Value equals your `agent_name` |
| 4 | `identity.admin` is present | Field exists (dict or null) |
| 5 | `email(action="check")` returns `sender_name` | Summary has `sender_name` matching your agent_name |
| 6 | `email(action="check")` returns `sender_agent_id` | Summary has `sender_agent_id` matching your agent_id |
| 7 | `email(action="check")` returns `is_human` | Summary has `is_human` field (boolean) |
| 8 | Sent record also carries identity | `mailbox/sent/*/message.json["identity"]` is a dict |

**Status:**
- **PASS** — all 8 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute.

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: capabilities / mail / identity-card
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
Every outgoing message carries an `identity` field populated by `_build_manifest()`. The email check/read commands surface sender_name, sender_agent_id, sender_nickname, sender_language, and is_human via `_inject_identity()`.

## Observed
<verbatim tool outputs, message.json identity section>

## Verdict reasoning
<one paragraph — reference specific criterion numbers>

## Artifacts
- mailbox/inbox/<uuid>/message.json (raw identity section)
- mailbox/sent/<uuid>/message.json (sent copy with identity)
```
