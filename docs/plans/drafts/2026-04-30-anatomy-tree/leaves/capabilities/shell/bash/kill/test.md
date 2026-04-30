---
timeout: 180
---

# Scenario: bash / kill — timeout behavior

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 4

---

## Setup

- You are a shallow avatar with `bash` capability.
- You will test timeout behavior by running commands that exceed a short timeout.

---

## Steps

1. **Test short timeout triggers error.** Call `bash(command="sleep 10", timeout=2)` — verify `status: "error"` and message contains `"timed out after 2s"`.

2. **Test command within timeout succeeds.** Call `bash(command="echo fast", timeout=5)` — verify `status: "ok"`, `exit_code: 0`.

3. **Test default timeout (30s).** Call `bash(command="echo within-default")` — verify `status: "ok"`. (This confirms the default doesn't cause immediate timeout.)

4. **Verify no stdout/stderr on timeout.** Read the response from step 1 carefully — timeout errors return only `{"status": "error", "message": "..."}` with no `stdout` or `stderr` fields.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable — no LLM judgment required.

| # | Criterion | Check |
|---|---|---|
| 1 | Timeout returns error status | `sleep 10` with `timeout=2` returns `status: "error"` |
| 2 | Timeout message format | Message is `"Command timed out after 2s"` |
| 3 | No stdout/stderr on timeout | Timeout response has no `stdout` or `stderr` keys |
| 4 | Fast command succeeds | `echo fast` with `timeout=5` returns `status: "ok"` |
| 5 | Default timeout works | `echo within-default` (no timeout arg) returns `status: "ok"` |

**Status:**
- **PASS** — all 5 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (missing capability, environment error).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: bash / kill — timeout behavior
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
subprocess.run(timeout=N) raises TimeoutExpired after N seconds. The handler catches it and returns {status: "error", message: "Command timed out after Ns"}. No SIGTERM/SIGKILL escalation — Python sends SIGKILL directly to the child. Grandchild processes may survive.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- test-result.md (this file)
```
