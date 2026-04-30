#!/usr/bin/env python3
"""Verify file:line references in anatomy README.md leaves against kernel source.

Scans all README.md files under the anatomy tree, extracts source references
in the ## Source tables and inline prose, then checks:
1. File existence (resolved against kernel source root)
2. Line range within file bounds
3. Optional: keyword presence near the referenced line (for def/class/constant refs)

Usage:
    python3 verify_references.py [--kernel-root PATH] [--anatomy-root PATH] [--fix-hints]

Exit codes:
    0 — all references valid
    1 — stale references found
    2 — script error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Reference:
    """A single file:line reference extracted from a leaf."""
    leaf: Path          # path to the README.md that contains the reference
    leaf_line: int      # line number in README.md where the reference appears
    file_path: str      # the source file path as written (may be relative)
    line_start: int     # first line number (1-based)
    line_end: int       # last line number (same as start for single-line)
    context: str        # surrounding text (for diagnostics)
    format: str         # 'table', 'inline_lines', 'inline_line', 'compact'


@dataclass
class VerificationResult:
    """Result of verifying one reference."""
    ref: Reference
    resolved_path: Path | None  # None if file not found
    file_exists: bool
    line_in_bounds: bool
    actual_line_count: int      # 0 if file not found
    error: str | None = None


# ─── Path normalization ────────────────────────────────────────────

# Kernel source roots to try when resolving a path from a leaf.
# The leaves use several conventions:
#   - `lingtai_kernel/base_agent.py`  (installed package name)
#   - `lingtai/agent.py`             (package source)
#   - `src/lingtai_kernel/...`       (explicit src prefix)
#   - `core/email/__init__.py`       (relative to lingtai package)
#   - `intrinsics/mail.py`           (relative to lingtai_kernel)
#   - `base_agent.py`                (bare filename in lingtai_kernel)

_PREFIXES_TO_STRIP = [
    "src/lingtai_kernel/",
    "src/lingtai/",
    "lingtai_kernel/",
    "lingtai/",
]

# Map known relative roots
_RELATIVE_ROOTS: dict[str, Path] = {
    # intrinsics/mail.py → lingtai_kernel/intrinsics/mail.py
    "intrinsics": Path("lingtai_kernel") / "intrinsics",
    # core/email/__init__.py → lingtai/core/email/__init__.py
    "core": Path("lingtai") / "core",
    # config.py → lingtai_kernel/config.py  OR lingtai/config.py
}


def resolve_source_path(raw_path: str, kernel_root: Path) -> Path | None:
    """Resolve a source file path from a leaf's Source table against the kernel root.

    Tries multiple conventions and returns the first that exists.
    """
    cleaned = raw_path.strip().strip("`")

    # Strategy 1: try as-is (absolute or relative to kernel root)
    candidate = kernel_root / cleaned
    if candidate.is_file():
        return candidate

    # Strategy 2: try as-is under src/
    candidate = kernel_root / "src" / cleaned
    if candidate.is_file():
        return candidate

    # Strategy 3: strip known prefixes and try
    for prefix in _PREFIXES_TO_STRIP:
        if cleaned.startswith(prefix):
            stripped = cleaned[len(prefix):]
            # If prefix was lingtai_kernel, try that first
            if "kernel" in prefix:
                candidate = kernel_root / "src" / "lingtai_kernel" / stripped
                if candidate.is_file():
                    return candidate
                candidate = kernel_root / "src" / "lingtai" / stripped
                if candidate.is_file():
                    return candidate
            else:
                candidate = kernel_root / "src" / "lingtai" / stripped
                if candidate.is_file():
                    return candidate
                candidate = kernel_root / "src" / "lingtai_kernel" / stripped
                if candidate.is_file():
                    return candidate

    # Strategy 4: try under src/lingtai/ and src/lingtai_kernel/
    # If the lingtai/ version is a tiny re-export, prefer lingtai_kernel/
    candidate_lingtai = kernel_root / "src" / "lingtai" / cleaned
    candidate_kernel = kernel_root / "src" / "lingtai_kernel" / cleaned

    if candidate_lingtai.is_file() and candidate_kernel.is_file():
        # Prefer kernel if lingtai version looks like a re-export
        lt_lines = count_lines(candidate_lingtai)
        if lt_lines <= 10 and count_lines(candidate_kernel) > lt_lines:
            return candidate_kernel
        return candidate_lingtai
    if candidate_lingtai.is_file():
        return candidate_lingtai
    if candidate_kernel.is_file():
        return candidate_kernel

    # Strategy 6: for known relative roots (intrinsics/, core/)
    for rel_prefix, rel_root in _RELATIVE_ROOTS.items():
        if cleaned.startswith(rel_prefix + "/") or cleaned == rel_prefix:
            suffix = cleaned[len(rel_prefix):].lstrip("/")
            if suffix:
                candidate = kernel_root / "src" / rel_root / suffix
                if candidate.is_file():
                    return candidate

    # Strategy 6: bare filename — search in lingtai_kernel/
    bare = Path(cleaned).name
    for search_dir in [
        kernel_root / "src" / "lingtai_kernel",
        kernel_root / "src" / "lingtai",
    ]:
        if search_dir.is_dir():
            for match in search_dir.rglob(bare):
                if match.is_file():
                    # Use only if the path structure matches
                    rel = str(match.relative_to(search_dir))
                    if cleaned.endswith(rel) or cleaned == bare:
                        return match

    return None


# ─── Reference extraction ──────────────────────────────────────────

# Table row: | desc | `path` | NNN-NNN |
_TABLE_RE = re.compile(
    r"^\|\s*.+?\s*\|\s*`([^`]+\.py)`\s*\|\s*(\d+)(?:-(\d+))?\s*\|"
)

# Inline: `file.py` lines NNN-NNN  or  `file.py` line NNN
_INLINE_LINES_RE = re.compile(
    r"`([^`]+\.py)`\s+(?:lines?|Lines?)\s+(\d+)(?:-(\d+))?"
)

# Compact: file.py:NNN  or  path/file.py:NNN
_COMPACT_RE = re.compile(
    r"(?:^|[\s(])([\w/\\]+\.py):(\d+)(?:-(\d+))?"
)


def extract_references(readme_path: Path) -> list[Reference]:
    """Extract all file:line references from a README.md."""
    refs: list[Reference] = []
    text = readme_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for i, line in enumerate(lines, 1):
        # Table rows
        m = _TABLE_RE.search(line)
        if m:
            refs.append(Reference(
                leaf=readme_path, leaf_line=i,
                file_path=m.group(1),
                line_start=int(m.group(2)),
                line_end=int(m.group(3)) if m.group(3) else int(m.group(2)),
                context=line.strip()[:120],
                format="table",
            ))
            continue

        # Inline lines
        m = _INLINE_LINES_RE.search(line)
        if m:
            refs.append(Reference(
                leaf=readme_path, leaf_line=i,
                file_path=m.group(1),
                line_start=int(m.group(2)),
                line_end=int(m.group(3)) if m.group(3) else int(m.group(2)),
                context=line.strip()[:120],
                format="inline_lines",
            ))
            continue

        # Compact file:line
        m = _COMPACT_RE.search(line)
        if m:
            refs.append(Reference(
                leaf=readme_path, leaf_line=i,
                file_path=m.group(1),
                line_start=int(m.group(2)),
                line_end=int(m.group(3)) if m.group(3) else int(m.group(2)),
                context=line.strip()[:120],
                format="compact",
            ))

    return refs


# ─── Verification ──────────────────────────────────────────────────

def count_lines(path: Path) -> int:
    """Count lines in a file efficiently."""
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def verify_reference(ref: Reference, kernel_root: Path) -> VerificationResult:
    """Verify a single reference against kernel source."""
    resolved = resolve_source_path(ref.file_path, kernel_root)

    if resolved is None:
        return VerificationResult(
            ref=ref, resolved_path=None, file_exists=False,
            line_in_bounds=False, actual_line_count=0,
            error=f"file not found: {ref.file_path}",
        )

    line_count = count_lines(resolved)
    in_bounds = ref.line_start >= 1 and ref.line_end <= line_count

    error = None
    if not in_bounds:
        error = (
            f"line range {ref.line_start}-{ref.line_end} exceeds "
            f"file length ({line_count} lines)"
        )

    return VerificationResult(
        ref=ref, resolved_path=resolved, file_exists=True,
        line_in_bounds=in_bounds, actual_line_count=line_count,
        error=error,
    )


# ─── Report ────────────────────────────────────────────────────────

def print_report(results: list[VerificationResult], verbose: bool = False) -> int:
    """Print verification report. Returns number of failures."""
    failures = [r for r in results if r.error]
    successes = [r for r in results if not r.error]

    print(f"\n{'='*72}")
    print(f"  Anatomy Reference Verification Report")
    print(f"{'='*72}")
    print(f"  Total references: {len(results)}")
    print(f"  Valid:            {len(successes)}")
    print(f"  Stale/Invalid:    {len(failures)}")

    if failures:
        print(f"\n{'─'*72}")
        print(f"  FAILURES")
        print(f"{'─'*72}")
        # Group by leaf
        by_leaf: dict[Path, list[VerificationResult]] = {}
        for r in failures:
            by_leaf.setdefault(r.ref.leaf, []).append(r)

        for leaf, leaf_failures in sorted(by_leaf.items()):
            leaf_rel = leaf  # relative path
            print(f"\n  📄 {leaf_rel}")
            for r in leaf_failures:
                line_ref = f"L{r.ref.leaf_line}"
                src_ref = f"{r.ref.file_path}:{r.ref.line_start}"
                if r.ref.line_end != r.ref.line_start:
                    src_ref += f"-{r.ref.line_end}"
                resolved = str(r.resolved_path) if r.resolved_path else "NOT FOUND"
                actual = f"({r.actual_line_count} lines)" if r.actual_line_count else ""
                print(f"    {line_ref}: {src_ref}")
                print(f"      ✗ {r.error}")
                if r.resolved_path:
                    print(f"        resolved: {resolved} {actual}")

    if verbose and successes:
        print(f"\n{'─'*72}")
        print(f"  VALID REFERENCES (sample)")
        print(f"{'─'*72}")
        for r in successes[:20]:
            src_ref = f"{r.ref.file_path}:{r.ref.line_start}"
            if r.ref.line_end != r.ref.line_start:
                src_ref += f"-{r.ref.line_end}"
            print(f"  ✓ {r.ref.leaf.name}:{r.ref.leaf_line} → {src_ref}")
        if len(successes) > 20:
            print(f"  ... and {len(successes) - 20} more")

    print()
    return len(failures)


# ─── Main ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kernel-root",
        default="/Users/huangzesen/Documents/GitHub/lingtai-kernel",
        help="Path to lingtai-kernel repo root",
    )
    parser.add_argument(
        "--anatomy-root",
        default=None,
        help="Path to anatomy tree root (auto-detected if under kernel-root)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show valid references too",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    kernel_root = Path(args.kernel_root).resolve()
    if not kernel_root.is_dir():
        print(f"ERROR: kernel root not found: {kernel_root}", file=sys.stderr)
        return 2

    # Auto-detect anatomy root
    if args.anatomy_root:
        anatomy_root = Path(args.anatomy_root).resolve()
    else:
        # Look for the anatomy tree
        candidates = [
            kernel_root / "docs" / "plans" / "drafts" / "2026-04-30-anatomy-tree" / "leaves",
            kernel_root / "anatomy" / "leaves",
        ]
        anatomy_root = None
        for c in candidates:
            if c.is_dir():
                anatomy_root = c
                break
        if anatomy_root is None:
            print("ERROR: anatomy leaves directory not found. Use --anatomy-root.", file=sys.stderr)
            return 2

    print(f"Kernel root:  {kernel_root}")
    print(f"Anatomy root: {anatomy_root}")

    # Collect all README.md files
    readmes = sorted(anatomy_root.rglob("README.md"))
    print(f"Leaves found: {len(readmes)}")

    # Extract and verify
    all_results: list[VerificationResult] = []
    for readme in readmes:
        refs = extract_references(readme)
        for ref in refs:
            result = verify_reference(ref, kernel_root)
            all_results.append(result)

    if args.json:
        output = []
        for r in all_results:
            output.append({
                "leaf": str(r.ref.leaf),
                "leaf_line": r.ref.leaf_line,
                "file_path": r.ref.file_path,
                "line_start": r.ref.line_start,
                "line_end": r.ref.line_end,
                "resolved": str(r.resolved_path) if r.resolved_path else None,
                "file_exists": r.file_exists,
                "line_in_bounds": r.line_in_bounds,
                "actual_lines": r.actual_line_count,
                "error": r.error,
            })
        json.dump(output, sys.stdout, indent=2)
        print()
        return 1 if any(r.error for r in all_results) else 0

    return print_report(all_results, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
