---
timeout: 180
---

# Scenario: bash / yolo — unrestricted execution

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 5

---

## Setup

- You are a shallow avatar with `bash` capability configured with `yolo: true`.
- Confirm yolo mode: call `system(action="show")` and verify your capabilities include `bash` with `yolo: true`.

---

## Steps

1. **Confirm yolo is active.** Call `bash(command="echo yolo-ok")` — should return `status: "ok"`, `stdout` contains `"yolo-ok"`.

2. **Test normally-denied command passes.** Call `bash(command="which sudo")` — should return `status: "ok"` (the command itself may exit non-zero if `sudo` isn't installed, but it must NOT be blocked by policy).

3. **Test another denylisted command.** Call `bash(command="which kill")` — should return `status: "ok"` (not blocked by policy).

4. **Test that sandbox still applies.** Call `bash(command="echo test", working_dir="/tmp")` — should return `status: "error"` because `/tmp` is outside the agent's working directory (sandbox enforcement is independent of yolo).

5. **Test destructive command syntax is accepted.** Call `bash(command="echo 'rm -rf / would run'")` — verify `status: "ok"` (policy does not block; we don't actually run destructive commands).

---

## Pass criteria

All of the following must hold. Each is filesystem-observable — no LLM judgment required.

| # | Criterion | Check |
|---|---|---|
| 1 | Basic command works | `echo` returns `status: "ok"` |
| 2 | `which sudo` not blocked | Returns `status: "ok"` (exit_code may be non-zero if sudo not installed, but no policy error) |
| 3 | `which kill` not blocked | Returns `status: "ok"` (no policy error message) |
| 4 | Sandbox still enforced | `working_dir="/tmp"` returns `status: "error"` with sandbox message |
| 5 | No policy description in response | No `"Command not allowed by policy"` appears in any response |

**Status:**
- **PASS** — all 5 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (yolo not actually enabled, environment error).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: bash / yolo — unrestricted execution
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
BashPolicy.yolo() creates a policy with allow=None, deny=None. is_allowed() returns True unconditionally. Sandbox validation (working_dir under agent dir) is a separate layer and still enforced.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- test-result.md (this file)
```
