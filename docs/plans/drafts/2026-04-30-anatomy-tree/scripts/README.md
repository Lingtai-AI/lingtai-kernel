# Anatomy Reference Verification

Quick check that all `file:line` references in anatomy leaf READMEs still point to valid locations in the kernel source.

## Usage

```bash
# From the anatomy tree root:
python3 scripts/verify_references.py

# Custom kernel root:
python3 scripts/verify_references.py --kernel-root /path/to/lingtai-kernel

# Verbose (show valid refs too):
python3 scripts/verify_references.py -v

# Machine-readable output:
python3 scripts/verify_references.py --json
```

## What it checks

1. **File existence** — resolves each referenced source file path against the kernel root, handling multiple naming conventions (`lingtai_kernel/`, `lingtai/`, `src/`, re-export wrappers)
2. **Line bounds** — verifies that the referenced line range falls within the file's actual line count
3. **Re-export detection** — when a path resolves to both `lingtai/` (small re-export wrapper) and `lingtai_kernel/` (real implementation), prefers the kernel version

## Exit codes

- `0` — all references valid
- `1` — stale references found (details in report)

## CI integration

Add to a pre-commit hook or CI step:

```bash
python3 docs/plans/drafts/2026-04-30-anatomy-tree/scripts/verify_references.py
```

Non-zero exit blocks the pipeline. Source changes that shift line numbers will be caught before merging.
