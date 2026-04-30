---
timeout: 180
---

# Scenario: codex — persistent knowledge store

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 10

---

## Setup

- You are a shallow avatar with the `codex` capability.
- Your codex starts empty (fresh working directory).

---

## Steps

1. **Verify empty codex.** Call `codex(action="filter")` — verify `status: "ok"`, `entries` is an empty list.

2. **Submit an entry.** Call `codex(action="submit", title="Test Entry", summary="A test codex entry", content="This is the full content of the test entry.")` — verify `status: "ok"`, `id` is an 8-char hex string, `entries` is 1, `max` is 20.

3. **Filter for the entry.** Call `codex(action="filter", pattern="Test")` — verify one entry returned with matching title.

4. **View the entry.** Call `codex(action="view", ids=["<id from step 2>"])` — verify `status: "ok"`, entry has all fields: `id`, `title`, `summary`, `content`.

5. **View with supplementary depth.** Call `codex(action="view", ids=["<id>"], depth="supplementary")` — verify `supplementary` field is present (empty string).

6. **Submit a second entry.** Call `codex(action="submit", title="Second Entry", summary="Another entry", content="Second content.")` — verify `entries` is 2.

7. **Consolidate both entries.** Call `codex(action="consolidate", ids=["<id1>", "<id2>"], title="Consolidated", summary="Merged entries", content="Combined content from both.")` — verify `status: "ok"`, `removed` is 2.

8. **Verify consolidation.** Call `codex(action="filter")` — verify exactly 1 entry with title "Consolidated".

9. **Delete the consolidated entry.** Call `codex(action="delete", ids=["<new_id>"])` — verify `status: "ok"`, `removed` is 1.

10. **Verify codex is empty again.** Call `codex(action="filter")` — verify `entries` is empty.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable — no LLM judgment required.

| # | Criterion | Check |
|---|---|---|
| 1 | Empty codex starts clean | `codex/codex.json` does not exist or has empty entries list |
| 2 | Submit creates file | `codex/codex.json` exists after submit, is valid JSON with `version: 1` |
| 3 | Entry has all fields | After view, entry has `id` (8-char hex), `title`, `summary`, `content`, `created_at` (ISO-8601) |
| 4 | Filter works | Filter with pattern returns matching entries |
| 5 | Consolidate removes old, creates new | After consolidate, old IDs are gone, new entry exists |
| 6 | Delete removes entry | After delete, `codex/codex.json` has empty entries list |
| 7 | Atomic write | `codex/codex.json` is valid JSON (no `.tmp` files left behind) |

**Status:**
- **PASS** — all 7 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (missing capability, environment error).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: codex — persistent knowledge store
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
Codex persists entries to codex/codex.json with atomic write. Submit generates 8-char SHA-256 prefix IDs. Filter searches title/summary/content with regex. Consolidate removes old entries and creates a new merged one. Delete removes by ID. Max 20 entries.

## Observed
<verbatim tool outputs, file contents>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- codex/codex.json
- test-result.md (this file)
```
