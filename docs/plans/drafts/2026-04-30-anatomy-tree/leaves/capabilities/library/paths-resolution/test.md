---
timeout: 180
---

# library/paths-resolution — test

## Setup

Uses the agent's working directory. Creates a temporary skill to verify
scanning and catalog injection.

## Steps

1. `bash({command: "mkdir -p .library/custom/_test_skill"})` — create temp skill dir.
2. `write({file_path: ".library/custom/_test_skill/SKILL.md", content: "---\nname: _test_skill\ndescription: Temporary test skill for anatomy validation.\nversion: 0.1\n---\n\n# Test\n"})`
3. `library({action: "info"})` — re-scan and check catalog.
4. `bash({command: "rm -rf .library/custom/_test_skill"})` — cleanup.
5. `library({action: "info"})` — re-scan after cleanup.

## Pass criteria

- **Step 3**: Response contains `catalog_size` ≥ 1. The `problems` array does
  NOT contain an entry for `_test_skill`. The XML catalog in the system prompt
  includes `<name>_test_skill</name>`.
- **Step 5**: After cleanup, `_test_skill` no longer appears in the catalog.
  `catalog_size` returns to its pre-test value.

**Verifiable via filesystem:**

- `bash({command: "grep '_test_skill' .lingtai/*/system/prompt_sections/library*"})
  — if the library section is persisted, the skill name should appear in step 3
  and disappear in step 5.

## Output template

```
### library/paths-resolution
- [ ] Step 3 — _test_skill appears in catalog
- [ ] Step 5 — _test_skill removed after cleanup
```
