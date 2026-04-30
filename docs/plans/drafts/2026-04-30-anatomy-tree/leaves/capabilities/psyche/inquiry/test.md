---
timeout: 180
---

# psyche/inquiry — test

## Setup

Requires a running agent with the `soul` intrinsic active and a valid LLM
backend configured. The inquiry is synchronous, so results appear in the
tool response.

## Steps

1. `soul({action: "inquiry", inquiry: "What is one thing I should focus on next?"})` — basic inquiry.
2. `soul({action: "inquiry", inquiry: ""})` — empty question.
3. `bash({command: "cat logs/soul_inquiry.jsonl | tail -1"})` — check persistence.
4. `bash({command: "cat logs/token_ledger.jsonl | grep soul | tail -1"})` — check token accounting.

## Pass criteria

- **Step 1**: Returns `{status: "ok", voice: "<non-empty string>"}`.
- **Step 2**: Returns an error containing `"inquiry is required"`.
- **Step 3**: `soul_inquiry.jsonl` has a line with `"mode": "inquiry"`,
  `"prompt"` matching the question from step 1, `"voice"` non-empty.
- **Step 4**: Token ledger has at least one entry with `"source": "soul"`.
- **INCONCLUSIVE**: If LLM backend is unreachable, step 1 may return
  `{status: "ok", voice: "(silence)"}` or timeout.

## Output template

```
### psyche/inquiry
- [ ] Step 1 — returns ok with non-empty voice
- [ ] Step 2 — empty inquiry returns error
- [ ] Step 3 — soul_inquiry.jsonl persisted
- [ ] Step 4 — token ledger has soul entry
```
