---
timeout: 180
---

# read — test

## Setup

None. Uses the agent's working directory.

## Steps

1. `write({file_path: "_test_read.txt", content: "alpha\nbeta\ngamma\ndelta\nepsilon"})`
2. `read({file_path: "_test_read.txt"})` — full read.
3. `read({file_path: "_test_read.txt", offset: 2, limit: 2})` — paginated.
4. `read({file_path: "_test_read.txt", offset: 99, limit: 10})` — beyond EOF.
5. `read({file_path: "_test_nonexistent.txt"})` — missing file.
6. `bash({command: "rm _test_read.txt"})`

## Pass criteria

- **Step 2**: `total_lines` == 5. `lines_shown` == 5. `content` contains
  `"1\talpha\n"` and `"5\tepsilon"`.
- **Step 3**: `lines_shown` == 2. `content` starts with `"2\tbeta\n"` and
  contains `"3\tgamma"`. Does NOT contain `"alpha"`.
- **Step 4**: `lines_shown` == 0. `total_lines` == 5.
- **Step 5**: return contains `"status": "error"` and message includes
  `"File not found"`.
- **Step 6**: `_test_read.txt` does not exist after cleanup.

## Output template

```
### read
- [ ] Step 2 — full read: total_lines=5, lines_shown=5, content starts with "1\talpha"
- [ ] Step 3 — pagination: lines_shown=2, starts at line 2
- [ ] Step 4 — beyond EOF: lines_shown=0
- [ ] Step 5 — missing file: error returned
- [ ] Step 6 — cleanup: file removed
```
