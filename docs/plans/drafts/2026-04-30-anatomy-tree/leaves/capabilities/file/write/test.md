---
timeout: 180
---

# write — test

## Setup

None. Uses the agent's working directory.

## Steps

1. `write({file_path: "_test_write/a/b.txt", content: "hello world"})`
2. `read({file_path: "_test_write/a/b.txt"})` — confirm content.
3. `write({file_path: "_test_write/a/b.txt", content: "overwritten"})` — overwrite.
4. `read({file_path: "_test_write/a/b.txt"})` — confirm replacement.
5. `write({file_path: "_test_write.txt", content: ""})` — empty content.
6. `bash({command: "rm -rf _test_write _test_write.txt"})`

## Pass criteria

- **Step 1**: return contains `"status": "ok"`, `bytes` == 11, `path` ends
  with `"_test_write/a/b.txt"`.
- **Step 2**: `content` contains `"hello world"`.
- **Step 3**: return `bytes` == 11.
- **Step 4**: `content` contains `"overwritten"` and does NOT contain
  `"hello world"`.
- **Step 5**: return `status` == `"ok"`, `bytes` == 0.
- **Step 6**: neither `_test_write/` nor `_test_write.txt` exist.

## Output template

```
### write
- [ ] Step 1 — nested write: status=ok, bytes=11, parent dirs created
- [ ] Step 2 — read back: content matches "hello world"
- [ ] Step 3 — overwrite: status=ok
- [ ] Step 4 — read back: old content gone, new content present
- [ ] Step 5 — empty write: bytes=0
- [ ] Step 6 — cleanup: all test artifacts removed
```
