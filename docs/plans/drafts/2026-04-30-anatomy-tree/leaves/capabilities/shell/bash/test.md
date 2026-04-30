---
timeout: 180
---

# Scenario: bash — shell execution

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 6

---

## Setup

- You are a shallow avatar with a fresh working directory.
- You have the `bash` capability (default policy — denylist from `bash_policy.json`).

---

## Steps

1. **Run a simple command.** Call `bash(command="echo hello")` — verify it returns `status: "ok"`, `exit_code: 0`, `stdout: "hello\n"`.

2. **Run a command with a non-zero exit.** Call `bash(command="ls /nonexistent_path_xyz_12345")` — verify `status: "ok"`, `exit_code != 0`, stderr is non-empty.

3. **Check working directory default.** Call `bash(command="pwd")` — verify stdout matches the agent's working directory.

4. **Check policy enforcement (denied command).** Call `bash(command="sudo whoami")` — verify `status: "error"` with message containing `"Command not allowed by policy"`.

5. **Check empty command rejection.** Call `bash(command="")` — verify `status: "error"` with message `"command is required"`.

6. **Verify output file creation.** Call `bash(command="touch /tmp/bash-test-$$ && echo ok")` — verify `stdout` contains `"ok"`. Then call `bash(command="ls /tmp/bash-test-*")` to confirm the file existed (cleanup: `bash(command="rm /tmp/bash-test-*")`).

---

## Pass criteria

All of the following must hold. Each is filesystem-observable — no LLM judgment required.

| # | Criterion | Check |
|---|---|---|
| 1 | Simple command returns ok | Response has `status: "ok"`, `exit_code: 0`, `stdout` contains `"hello"` |
| 2 | Non-zero exit captured | Failed `ls` has `exit_code != 0` and `stderr` is non-empty string |
| 3 | Default working dir is agent dir | `pwd` stdout matches agent's working directory path |
| 4 | Denied command blocked | `sudo` returns `status: "error"` with policy denial message |
| 5 | Empty command rejected | Empty string returns `status: "error"` |
| 6 | File creation works | `touch` + `ls` confirms file existed in `/tmp` |

**Status:**
- **PASS** — all 6 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (missing capability, environment error).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: bash — shell execution
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
2. <what you did>
...

## Expected (per anatomy)
Bash executes commands via subprocess.run(shell=True), returns {status, exit_code, stdout, stderr}, enforces policy via BashPolicy.is_allowed() which parses pipes/chains/subshells, defaults to agent working directory, and truncates output at 50k chars.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- test-result.md (this file)
```
