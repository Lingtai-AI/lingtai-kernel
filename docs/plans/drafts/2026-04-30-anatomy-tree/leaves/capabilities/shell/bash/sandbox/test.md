---
timeout: 180
---

# Scenario: bash / sandbox — working directory containment

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 5

---

## Setup

- You are a shallow avatar with `bash` capability (default policy).
- Your working directory is set by the kernel (verify with `bash(command="pwd")`).

---

## Steps

1. **Confirm default works.** Call `bash(command="pwd")` — verify `status: "ok"` and stdout matches your working directory.

2. **Test valid subdirectory.** Call `bash(command="mkdir -p _sandbox_test && pwd", working_dir="<agent_dir>/_sandbox_test")` — replacing `<agent_dir>` with your actual working directory. Verify `status: "ok"`.

3. **Test outside-sandbox path.** Call `bash(command="pwd", working_dir="/tmp")` — verify `status: "error"` with message containing `"working_dir must be under agent working directory"`.

4. **Test another outside path.** Call `bash(command="pwd", working_dir="/")` — verify `status: "error"` with same sandbox message.

5. **Cleanup.** Call `bash(command="rm -rf _sandbox_test")`.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable — no LLM judgment required.

| # | Criterion | Check |
|---|---|---|
| 1 | Default working dir passes | `pwd` with no `working_dir` returns `status: "ok"` |
| 2 | Subdirectory passes | `working_dir` set to subdir under agent dir returns `status: "ok"` |
| 3 | `/tmp` blocked | Returns `status: "error"` with sandbox message |
| 4 | `/` blocked | Returns `status: "error"` with sandbox message |
| 5 | Error message format | All sandbox errors contain `"working_dir must be under agent working directory"` |

**Status:**
- **PASS** — all 5 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (missing capability, environment error).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: bash / sandbox — working directory containment
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
Path(cwd).resolve() must equal or start with Path(agent_working_dir).resolve(). Any path outside this prefix returns status: "error" with sandbox message. Symlinks are resolved before comparison.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- test-result.md (this file)
```
