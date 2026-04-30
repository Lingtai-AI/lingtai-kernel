---
timeout: 180
---

# edit — test

## Setup

None. Uses the agent's working directory.

## Steps

1. `write({file_path: "_test_edit.txt", content: "line one\nline two\nline three\nline two again"})`
2. `edit({file_path: "_test_edit.txt", old_string: "line two", new_string: "line TWO"})` — should fail (ambiguous).
3. `edit({file_path: "_test_edit.txt", old_string: "line two\nline three", new_string: "line TWO\nline THREE"})` — unique context.
4. `read({file_path: "_test_edit.txt"})` — verify.
5. `edit({file_path: "_test_edit.txt", old_string: "line TWO", new_string: "line two", replace_all: true})` — replace all.
6. `read({file_path: "_test_edit.txt"})` — verify.
7. `edit({file_path: "_test_edit.txt", old_string: "nonexistent", new_string: "x"})` — not found.
8. `bash({command: "rm _test_edit.txt"})`

## Pass criteria

- **Step 2**: return contains `"error"` and message includes `"found 2 times"`.
- **Step 3**: return contains `"status": "ok"`, `"replacements": 1`.
- **Step 4**: file contains `"line TWO"` and `"line THREE"`. Does NOT contain
  `"line two\nline three"`.
- **Step 5**: return `replacements` == 2 (two occurrences of `"line TWO"`).
- **Step 6**: file contains `"line two"` (both restored). No `"line TWO"`.
- **Step 7**: return contains `"error"` and `"old_string not found"`.
- **Step 8**: file removed.

## Output template

```
### edit
- [ ] Step 2 — ambiguous match: error with "found 2 times"
- [ ] Step 3 — unique context replacement: status=ok, replacements=1
- [ ] Step 4 — verify: new content present, old gone
- [ ] Step 5 — replace_all: replacements=2
- [ ] Step 6 — verify: all occurrences replaced
- [ ] Step 7 — not found: error returned
- [ ] Step 8 — cleanup: file removed
```
