---
timeout: 180
---

# psyche/soul-flow — test

## Setup

Requires a running agent with the `soul` intrinsic active and a valid LLM
backend configured. The test checks filesystem artifacts produced by the
soul flow mechanism.

## Steps

1. `system({action: "show"})` — confirm agent is IDLE or ACTIVE.
2. End the turn (no tool call) — go IDLE. Wait `soul_delay + 5` seconds.
3. `bash({command: "cat logs/soul_flow.jsonl | tail -1"})` — check for a new entry.
4. `bash({command: "cat history/soul_history.jsonl | wc -l"})` — check session persistence.
5. `bash({command: "cat history/soul_cursor.json"})` — check cursor state.
6. `soul({action: "delay", delay: 10})` — shorten delay.
7. End the turn again. Wait 15 seconds.
8. `bash({command: "cat logs/soul_flow.jsonl | wc -l"})` — confirm new entry added.

## Pass criteria

- **Step 3**: `soul_flow.jsonl` contains a JSON line with `"mode": "flow"`,
  `"voice"` (non-empty string), `"prompt"` (non-empty string).
- **Step 4**: `soul_history.jsonl` exists and has ≥1 line.
- **Step 5**: `soul_cursor.json` contains `{"cursor": <positive integer>}`.
- **Step 7**: After the second idle period, the file length increases by ≥1
  compared to step 3.
- **INCONCLUSIVE**: If LLM backend is unreachable (soul calls time out),
  `soul_flow.jsonl` may remain empty. Check `logs/events.jsonl` for
  `soul_whisper_error`.

## Output template

```
### psyche/soul-flow
- [ ] Step 3 — soul_flow.jsonl has new entry with mode="flow"
- [ ] Step 4 — soul_history.jsonl exists with ≥1 line
- [ ] Step 5 — soul_cursor.json has positive cursor
- [ ] Step 8 — second idle produces additional entry
```
