# Audit Directory — Index

§Source reference audits for the anatomy tree. Each file verifies that the `## Source` tables in leaf README.md files point to real code at real line numbers.

| Audit | Leaves | Result | Date |
|-------|--------|--------|------|
| `audit-shell-codex.md` | shell/bash (4 leaves), shell/codex (2 leaves) | 46 ✅ / 3 ⚠️ / 0 ❌ | 2026-04-30 |
| `audit-core-init.md` | core/*, init/* | — | — |
| `audit-daemon-mcp.md` | daemon/*, mcp/* | — | — |
| `audit-file-tools.md` | file/* | — | — |
| `audit-llm.md` | llm/* | — | — |

## Procedural Note

`verify-source-references.md` — a reusable procedure for auditing §Source tables. Distilled from the shell/codex audit experience. Key lessons:

- Read the full source file once, then verify all line references against it (batch, don't per-row).
- Check **1 line before** the claimed start — off-by-one hides `def`/`@decorator` lines.
- Check **1 line after** the claimed end — off-by-one hides closing `"""`, `}`, `)`.
- Use `find` by basename when a short path doesn't resolve directly.

## Test Results

`test-results/` contains behavioral test output from the conductor/leaf-test phase (separate from §Source audits).
