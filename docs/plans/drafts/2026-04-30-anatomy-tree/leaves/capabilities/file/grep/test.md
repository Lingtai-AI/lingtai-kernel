---
timeout: 180
---

# grep — test

## Setup

None. Uses the agent's working directory.

## Steps

1. `write({file_path: "_test_grep/alpha.py", content: "import os\nimport sys\nprint('hello')\n"})`
2. `write({file_path: "_test_grep/beta.txt", content: "no imports here\njust text\n"})`
3. `write({file_path: "_test_grep/gamma.py", content: "import os\nx = 1\n"})`
4. `grep({pattern: "^import", path: "_test_grep"})` — search directory.
5. `grep({pattern: "^import", path: "_test_grep/gamma.py"})` — single file.
6. `grep({pattern: "nonexistent_xyzzy", path: "_test_grep"})` — no matches.
7. `grep({pattern: "^import", path: "_test_grep", max_matches: 2})` — truncation.
8. `bash({command: "rm -rf _test_grep"})`

## Pass criteria

- **Step 4**: `count` == 3. All matches have `text` starting with `"import"`.
  Each match object has `file` (string), `line` (integer ≥ 1), `text` (string).
  `truncated` is `false`.
- **Step 5**: `count` == 1. `matches[0].file` ends with `"gamma.py"`.
  `matches[0].line` == 1.
- **Step 6**: `count` == 0. `matches` is an empty list. `truncated` is `false`.
- **Step 7**: `count` == 2. `truncated` is `true`.
- **Step 8**: `_test_grep/` does not exist.

## Output template

```
### grep
- [ ] Step 4 — directory search: count=3, all match "^import", truncated=false
- [ ] Step 5 — single file: count=1, correct file and line
- [ ] Step 6 — no matches: count=0, empty list
- [ ] Step 7 — truncation: count=2, truncated=true
- [ ] Step 8 — cleanup: directory removed
```
