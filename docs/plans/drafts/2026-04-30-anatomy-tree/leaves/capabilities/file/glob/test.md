---
timeout: 180
---

# glob — test

## Setup

None. Uses the agent's working directory.

## Steps

1. `write({file_path: "_test_glob/a/one.py", content: ""})`
2. `write({file_path: "_test_glob/a/two.txt", content: ""})`
3. `write({file_path: "_test_glob/b/three.py", content: ""})`
4. `glob({pattern: "**/*.py", path: "_test_glob"})` — recursive Python files.
5. `glob({pattern: "*.txt", path: "_test_glob/a"})` — non-recursive txt.
6. `glob({pattern: "*.xyz", path: "_test_glob"})` — no matches.
7. `bash({command: "rm -rf _test_glob"})`

## Pass criteria

- **Step 4**: `count` == 2. Both matches end with `".py"`. Matches are sorted.
  Each match is an absolute path containing `"_test_glob"`.
- **Step 5**: `count` == 1. The single match ends with `"two.txt"`.
- **Step 6**: `count` == 0. `matches` is an empty list.
- **Step 7**: `_test_glob/` does not exist.

## Output template

```
### glob
- [ ] Step 4 — recursive **.py: count=2, both .py, sorted
- [ ] Step 5 — non-recursive *.txt: count=1
- [ ] Step 6 — no matches: count=0, empty list
- [ ] Step 7 — cleanup: directory removed
```
