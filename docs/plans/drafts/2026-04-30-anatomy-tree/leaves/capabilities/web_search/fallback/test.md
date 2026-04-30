---
timeout: 180
---

# web_search/fallback — test

## Setup

Requires a running agent with the `web_search` capability active. DuckDuckGo
works without API keys, so this test can run with minimal configuration.

## Steps

1. `web_search({query: "LingTai AI agent framework"})` — basic search.
2. `web_search({query: ""})` — empty query.
3. `web_search({})` — missing query parameter.
4. `bash({command: "grep 'capability_fallback' logs/events.jsonl | tail -1 2>/dev/null || echo 'no fallback events'"})` — check if fallback was triggered during setup.

## Pass criteria

- **Step 1**: Returns `{status: "ok", results: "<non-empty string>"}`. Results
  contain at least one entry with `**<title>**` format.
- **Step 2**: Returns error containing `"Missing required parameter"` or
  `"query"` (empty string treated as missing).
- **Step 3**: Returns error containing `"Missing required parameter"`.
- **Step 4**: Informational only — if the agent's LLM provider is not in the
  web_search providers list, a `capability_fallback` event should exist showing
  the fallback to `duckduckgo`.
- **INCONCLUSIVE**: If network is unavailable, step 1 may fail with a timeout
  or connection error. Check `logs/events.jsonl` for the specific error.

## Output template

```
### web_search/fallback
- [ ] Step 1 — search returns results
- [ ] Step 2 — empty query returns error
- [ ] Step 3 — missing query returns error
- [ ] Step 4 — fallback event logged (if applicable)
```
