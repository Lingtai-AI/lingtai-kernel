---
timeout: 180
---

# Scenario: core / molt-protocol

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 7

---

## Setup

- You are an agent with `bash` capability.
- Your working directory has the standard post-startup layout (`.agent.json`, `system/`, `history/`, `mailbox/`, `codex/`).
- **Constraint:** Triggering an actual molt is destructive and irreversible within a session. This test verifies observable pre/post state files without forcing a molt.

---

## Steps

1. **Verify `.agent.json` contains `molt_count`.** Use `bash` to run `python3 -c "import json; d=json.load(open('.agent.json')); print(d.get('molt_count', 'MISSING'))"`. Record the current value.
2. **Verify `history/chat_history.jsonl` exists (or is absent on fresh agent).** Use `bash` to run `test -f history/chat_history.jsonl && echo "exists" || echo "absent"`.
3. **Verify `history/chat_history_archive.jsonl` exists (or is absent).** Use `bash` to run `test -f history/chat_history_archive.jsonl && echo "exists" || echo "absent"`.
4. **Verify durable stores exist on disk.** Use `bash` to check each:
   - `system/lingtai.md` (or absent if inline in init.json)
   - `system/pad.md` (or absent if inline)
   - `codex/codex.json` (or absent if no entries)
   - `mailbox/` directory exists
   - `.library/` directory exists
5. **Verify molt_pressure is configured.** Use `bash` to run `python3 -c "import json; d=json.load(open('init.json')); m=d.get('manifest',{}); print(m.get('molt_pressure', 0.7))"`. Confirm it returns a number.
6. **Verify the psyche tool supports context/molt.** Use `bash` to grep for "molt" in the psyche capability source or check that `psyche` tool schema accepts `object=context, action=molt`.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable.

| # | Criterion | Check |
|---|-----------|-------|
| 1 | `.agent.json` exists and contains `molt_count` key | `python3 -c "import json; d=json.load(open('.agent.json')); assert 'molt_count' in d"` exits 0 |
| 2 | `.agent.json` `molt_count` is an integer ≥ 0 | `python3 -c "import json; d=json.load(open('.agent.json')); assert isinstance(d['molt_count'], int) and d['molt_count'] >= 0"` exits 0 |
| 3 | At least one durable store directory exists | At least one of: `system/`, `mailbox/`, `.library/`, `codex/` exists |
| 4 | `molt_pressure` is a number between 0 and 1 | `python3` check exits 0 |
| 5 | No forced-molt residue on disk | `logs/events.jsonl` does not contain `"hard_ceiling"` at tail — or if it does, `molt_count` has increased accordingly |

**Status:**
- **PASS** — all 5 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute.

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: core / molt-protocol
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
2. <what you did>
...

## Expected (per anatomy)
.agent.json tracks molt_count as a non-negative integer; durable stores (lingtai, pad, codex, library, mailbox) exist on disk; molt_pressure is configured; no spurious forced-molt residue.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- .agent.json (molt_count field)
- logs/events.jsonl (tail)
```
