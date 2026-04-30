---
timeout: 180
---

# Scenario: core / wake-mechanisms

> **Timeout:** 2 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 6

---

## Setup

- You are an agent with `bash` capability.
- Your agent is currently running (not suspended).
- The kernel source is available for grep inspection.

---

## Steps

1. **Verify `_wake_nap` exists in source.** Use `bash` to run `grep -n '_wake_nap' <kernel-src>/lingtai_kernel/base_agent.py | head -5`. Confirm the function definition appears.
2. **Verify `_nap_wake` is a `threading.Event`.** Use `bash` to run `grep -n '_nap_wake' <kernel-src>/lingtai_kernel/base_agent.py | head -5`. Confirm `threading.Event()` appears in the initialization.
3. **Verify self-send calls `_wake_nap`.** Use `bash` to run `grep -n '_wake_nap' <kernel-src>/lingtai_kernel/intrinsics/mail.py | head -5`. Confirm the call appears in the self-send branch.
4. **Verify MCP inbox poller calls `_wake_nap`.** Use `bash` to run `grep -n '_wake_nap' <kernel-src>/lingtai/core/mcp/inbox.py | head -5`. Confirm the call exists.
5. **Verify ASLEEP state handling in run loop.** Use `bash` to run `grep -n 'asleep' <kernel-src>/lingtai_kernel/base_agent.py | head -10`. Confirm the ASLEEP branch exists in `_run_loop()`.
6. **Verify polling listener exists.** Use `bash` to run `grep -n 'poll\|listener\|_on_normal_mail\|on_message' <kernel-src>/lingtai_kernel/base_agent.py | head -10`. Confirm mail callback registration.

---

## Pass criteria

All of the following must hold. Each is observable from tool output (grep matches).

| # | Criterion | Check |
|---|-----------|-------|
| 1 | `_wake_nap` function defined | `grep 'def _wake_nap' base_agent.py` returns a match |
| 2 | `_nap_wake` is `threading.Event()` | `grep '_nap_wake.*Event' base_agent.py` returns a match |
| 3 | Self-send path calls `_wake_nap` | `grep '_wake_nap' intrinsics/mail.py` returns a match |
| 4 | MCP poller calls `_wake_nap` | `grep '_wake_nap' core/mcp/inbox.py` returns a match |
| 5 | ASLEEP handling in run loop | `grep` for asleep state handling in `base_agent.py` returns matches |
| 6 | Mail callback (`_on_normal_mail` or equivalent) | `grep` for mail arrival callback returns matches |

**Status:**
- **PASS** — all 6 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (kernel source not accessible).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: core / wake-mechanisms
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
2. <what you did>
...

## Expected (per anatomy)
_wake_nap() exists at base_agent.py:621, uses threading.Event.set(), is called from self-send path (intrinsics/mail.py), MCP inbox poller (core/mcp/inbox.py), and mail polling listener callback. ASLEEP state is handled in _run_loop() with inbox.get(timeout=1.0).

## Observed
<verbatim grep output, line numbers>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- N/A (all checks via source grep)
```
