---
timeout: 180
---

# psyche/core-memories — test

## Setup

Uses the agent's working directory. No external dependencies beyond the
psyche capability being active (which it is by default).

## Steps

1. `psyche({object: "lingtai", action: "update", content: "Test identity: I am a test agent."})`
2. `bash({command: "cat system/lingtai.md"})`
3. `psyche({object: "pad", action: "edit", content: "Test pad note."})`
4. `bash({command: "cat system/pad.md"})`
5. `write({file_path: "_test_pin.txt", content: "pinned reference data"})`
6. `psyche({object: "pad", action: "append", files: ["_test_pin.txt"]})`
7. `bash({command: "cat system/pad_append.json"})`
8. `psyche({object: "context", action: "molt", summary: "Test molt: carrying forward identity test."})`
9. `bash({command: "cat system/lingtai.md && echo '---' && cat system/pad.md && echo '---' && test -f history/chat_history_archive.jsonl && echo 'archived' || echo 'no archive'"})`
10. `bash({command: "rm -f _test_pin.txt"})` — cleanup.

## Pass criteria

- **Step 2**: `lingtai.md` contains `"Test identity"`.
- **Step 4**: `pad.md` contains `"Test pad note"`.
- **Step 7**: `pad_append.json` is a JSON array containing `"_test_pin.txt"`.
- **Step 9**: Three things verified:
  - `lingtai.md` still contains `"Test identity"` (survived molt).
  - `pad.md` still contains `"Test pad note"` (survived molt).
  - Output contains `"archived"` (pre-molt history preserved).
- **Step 10**: Cleanup file removed.

## Output template

```
### psyche/core-memories
- [ ] Step 2 — lingtai.md written with identity
- [ ] Step 4 — pad.md written with note
- [ ] Step 7 — pad_append.json tracks pinned file
- [ ] Step 9 — identity + pad survive molt; history archived
- [ ] Step 10 — cleanup done
```
