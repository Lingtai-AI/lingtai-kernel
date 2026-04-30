---
timeout: 180
---

# Scenario: init / init-schema

> **Timeout:** 2 minutes
> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 5

---

## Setup

- You have a working LingTai agent with `bash` capability.
- The kernel source is available at the path in your `init.json` or you can import `lingtai.init_schema` from your Python environment.

---

## Steps

1. **Locate the validator.** Use `bash` to run `python3 -c "from lingtai.init_schema import validate_init; print('OK')"` — confirm the module is importable.
2. **Test missing required field.** Use `bash` to run a Python snippet that passes `{"manifest": {}}` (missing `manifest.llm`) to `validate_init()` and catches the `ValueError`. Verify the error message mentions `manifest.llm`.
3. **Test unknown field produces warning.** Use `bash` to run a Python snippet that passes a valid `init.json` dict with an extra top-level key `"unknown_field": 1`. Verify `validate_init()` returns a non-empty warning list containing `"unknown_field"`.
4. **Test bool rejection for numeric field.** Use `bash` to run a Python snippet that sets `manifest.stamina = True`. Verify `validate_init()` raises `ValueError` mentioning "expected number, got bool".
5. **Test text-pair requirement.** Use `bash` to run a Python snippet that omits both `principle` and `principle_file`. Verify `ValueError` mentions "principle".

---

## Pass criteria

All of the following must hold. Each is observable from tool output.

| # | Criterion | Check |
|---|-----------|-------|
| 1 | Module importable | `python3 -c "from lingtai.init_schema import validate_init"` exits 0 |
| 2 | Missing `manifest.llm` raises `ValueError` | stderr contains "manifest.llm" in error message |
| 3 | Unknown field returns warning | `validate_init()` returns list with string containing "unknown_field" |
| 4 | Bool in numeric field raises `ValueError` | stderr contains "expected number, got bool" |
| 5 | Missing text pair raises `ValueError` | stderr contains "missing required field: principle" |

**Status:**
- **PASS** — all 5 criteria met.
- **FAIL** — any criterion violated.
- **INCONCLUSIVE** — test could not execute.

---

## Output template

Write `test-result.md` in your working directory:

```markdown
# Scenario: init / init-schema
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** README.md (sibling)
**Run:** <ISO timestamp>
**Avatar:** <your agent_id>

## Steps taken
1. <what you did>
2. <what you did>
...

## Expected (per anatomy)
validate_init() raises ValueError for missing required fields or wrong types, returns warning list for unknown fields, rejects bool for numeric fields.

## Observed
<verbatim tool outputs>

## Verdict reasoning
<one paragraph: why PASS / FAIL / INCONCLUSIVE — reference specific criterion numbers>

## Artifacts
- N/A (no filesystem artifacts; all checks via stdout/stderr)
```
