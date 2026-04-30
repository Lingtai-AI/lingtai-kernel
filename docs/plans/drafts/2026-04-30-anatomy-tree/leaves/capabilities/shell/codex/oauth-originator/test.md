---
timeout: 180
---

# Scenario: codex / oauth-originator — token management & entry provenance

> **Timeout:** 3 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 6

---

## Setup

- You are a shallow avatar with the `codex` capability.
- You will test codex entry ID generation and provenance (lack of originator tracking).

---

## Steps

1. **Submit an entry and record its ID.** Call `codex(action="submit", title="Provenance Test", summary="Testing ID generation", content="Unique content alpha.")` — record the returned `id`.

2. **Submit identical content at a different time.** Call `codex(action="submit", title="Provenance Test", summary="Testing ID generation", content="Unique content alpha.")` again — verify the returned `id` is **different** from step 1 (because `created_at` differs).

3. **Verify no originator field.** Call `codex(action="view", ids=["<id from step 1>"])` — inspect the returned entry: it should have `id`, `title`, `summary`, `content`, `created_at`. There should be **no** `originator`, `source`, `created_by`, or `agent_id` field.

4. **Verify both entries coexist.** Call `codex(action="filter", pattern="Provenance Test")` — verify 2 entries are returned (no dedup).

5. **Check codex.json on disk.** Use `read` to open `codex/codex.json` — verify the JSON structure: `{"version": 1, "entries": [...]}` with both entries present, each having the 5 standard fields and no extra provenance fields.

6. **Cleanup.** Call `codex(action="delete", ids=["<id1>", "<id2>"])` — clean up test entries.

---

## Pass criteria

All of the following must hold. Each is filesystem-observable — no LLM judgment required.

| # | Criterion | Check |
|---|---|---|
| 1 | IDs are 8-char hex | Both returned IDs match `[0-9a-f]{8}` |
| 2 | Identical content gets different IDs | ID from step 1 ≠ ID from step 2 |
| 3 | No originator field in entry | View response has no `originator`, `source`, `created_by`, or `agent_id` key |
| 4 | No dedup | Filter returns 2 entries for identical content |
| 5 | codex.json schema | File has `version: 1` and `entries` array with standard fields only |
| 6 | Cleanup succeeds | Delete returns `removed: 2` |

**Status:**
- **PASS** — all 6 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute (missing capability, environment error).

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: codex / oauth-originator — token management & entry provenance
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
...

## Expected (per anatomy)
Codex entries have no originator/source tracking. IDs are sha256(title+content+created_at)[:8] — identical content at different times produces different IDs. No dedup by content hash. The CodexTokenManager (auth/codex.py) manages OAuth tokens for the "codex" LLM provider, which is separate from the knowledge store.

## Observed
<verbatim tool outputs, file contents>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- codex/codex.json
- test-result.md (this file)
```
