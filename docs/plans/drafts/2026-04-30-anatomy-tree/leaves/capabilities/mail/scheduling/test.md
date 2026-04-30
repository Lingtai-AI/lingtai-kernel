---
timeout: 180
---

# Scenario: capabilities / mail / scheduling

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 8

---

## Setup

- You are an agent with the `email` capability.
- Your working directory has a `mailbox/` structure.
- You have the `bash` tool for filesystem inspection.

---

## Steps

1. **Create a schedule.** Call `email(schedule={action: "create", interval: 60, count: 3}, address="<your own address>", subject="sched test", message="scheduled message")`.
2. **Verify the schedule file exists.** Use `bash`: find `mailbox/schedules/<schedule_id>/schedule.json` (use the returned `schedule_id`).
3. **Read and verify the schedule record.** Use `read` on the `schedule.json` — check `status == "active"`, `sent == 0`, `count == 3`, `interval == 60`.
4. **List schedules.** Call `email(schedule={action: "list"})` — verify the schedule appears with `status: "active"`.
5. **Cancel the schedule.** Call `email(schedule={action: "cancel", schedule_id: "<id>"})`.
6. **Verify cancellation on disk.** Use `read` on the same `schedule.json` — check `status == "inactive"`.
7. **List again.** Call `email(schedule={action: "list"})` — verify status is `inactive`.
8. **Reactivate.** Call `email(schedule={action: "reactivate", schedule_id: "<id>"})`.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable or tool-return-observable.

| # | Criterion | Check |
|---|---|---|
| 1 | Schedule file created | `mailbox/schedules/<id>/schedule.json` exists |
| 2 | Initial status is `active` | `schedule.json` has `status: "active"` |
| 3 | Initial `sent` is 0 | `schedule.json` has `sent: 0` |
| 4 | `count` matches create arg | `schedule.json` has `count: 3` |
| 5 | `interval` matches create arg | `schedule.json` has `interval: 60` |
| 6 | Cancel sets status to `inactive` | After cancel, `schedule.json` has `status: "inactive"` |
| 7 | List reflects inactive | `email(schedule={action: "list"})` shows `status: "inactive"` |
| 8 | Reactivate sets status to `active` | After reactivate, `schedule.json` has `status: "active"` |
| 9 | `send_payload` preserved | `schedule.json` contains `send_payload` with correct `address`, `subject`, `message` |

**Status:**
- **PASS** — all 9 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute.

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: capabilities / mail / scheduling
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
Schedule created on disk with status=active. Cancel flips to inactive. Reactivate flips back to active. All state changes are persisted to schedule.json via atomic write.

## Observed
<verbatim tool outputs, file contents>

## Verdict reasoning
<one paragraph — reference specific criterion numbers>

## Artifacts
- mailbox/schedules/<id>/schedule.json (at each state)
```
