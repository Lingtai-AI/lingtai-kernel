#!/usr/bin/env python3
"""Verify source references in anatomy leaf READMEs.

Checks every `## Source` table in leaf READMEs against the kernel source:
  1. Does the referenced file exist?
  2. Are the line numbers within bounds?
  3. Does the referenced line range still contain the expected identifier(s)?

Usage:
    python3 verify-source-refs.py [leaves_dir] [src_root]

Defaults:
    leaves_dir = docs/plans/drafts/2026-04-30-anatomy-tree/leaves/capabilities/mail/
    src_root   = src/  (relative to repo root)

Exit codes:
    0  All references valid
    1  One or more broken references found
    2  Script error
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

DRIFT_TOLERANCE = 10  # lines of tolerance for identifier search
IDENTIFIER_PATTERN = re.compile(
    r"[a-zA-Z_][a-zA-Z0-9_]*"
)  # Python identifier


@dataclass
class Ref:
    """A single source reference parsed from a leaf README."""
    leaf: str          # leaf directory name (e.g. "dedup")
    what: str          # description of what this references
    file: str          # source file path (relative to src_root)
    line_start: int
    line_end: int
    readme_path: Path  # path to the README.md that contains this ref


@dataclass
class Result:
    """Verification result for one reference."""
    ref: Ref
    ok: bool
    issues: list[str] = field(default_factory=list)


# ── Parsing ─────────────────────────────────────────────────────────────────

def parse_source_table(readme_path: Path, leaf_name: str) -> list[Ref]:
    """Parse the ## Source table from a README.md and return Ref objects."""
    text = readme_path.read_text(encoding="utf-8")
    
    # Find ## Source section
    source_match = re.search(r"^## Source\s*$", text, re.MULTILINE)
    if not source_match:
        return []
    
    # Extract until next ## or end of file
    start = source_match.end()
    next_heading = re.search(r"^##\s", text[start:], re.MULTILINE)
    section = text[start : start + next_heading.start()] if next_heading else text[start:]
    
    refs = []
    # Match table rows: | What | `file` | line(s) |
    # The file column may or may not be wrapped in backticks
    # Line column can use en-dash (–), em-dash (—), or hyphen (-)
    for m in re.finditer(
        r"^\|\s*(.+?)\s*\|\s*`?([^`|]+?)`?\s*\|\s*(\d+)\s*[–—-]\s*(\d+)\s*\|",
        section,
        re.MULTILINE,
    ):
        what = m.group(1).strip()
        file_path = m.group(2).strip()
        line_start = int(m.group(3))
        line_end = int(m.group(4))
        refs.append(Ref(
            leaf=leaf_name,
            what=what,
            file=file_path,
            line_start=line_start,
            line_end=line_end,
            readme_path=readme_path,
        ))
    
    # Also match single-line references: | What | `file` | 123 |
    for m in re.finditer(
        r"^\|\s*(.+?)\s*\|\s*`?([^`|]+?)`?\s*\|\s*(\d+)\s*\|",
        section,
        re.MULTILINE,
    ):
        what = m.group(1).strip()
        file_path = m.group(2).strip()
        line_num = int(m.group(3))
        # Avoid double-counting ranges already captured
        if not any(r.file == file_path and r.line_start == line_num for r in refs):
            refs.append(Ref(
                leaf=leaf_name,
                what=what,
                file=file_path,
                line_start=line_num,
                line_end=line_num,
                readme_path=readme_path,
            ))
    
    return refs


# ── Verification ────────────────────────────────────────────────────────────

def extract_identifiers(what: str) -> list[str]:
    """Extract code-like identifiers from a 'What' description.

    Conservative — only returns identifiers we're confident are actual code:
    1. Backticked tokens: `message.json`, `os.replace()`, `_last_sent`
    2. Underscore-starting tokens from prose: _build_manifest, _inject_identity
    3. Tokens containing underscores: is_agent, is_alive

    Deliberately excludes CamelCase prose words (Mailman, Gate, Wrapper)
    that aren't backticked — they're descriptions, not identifiers.
    """
    ids = []
    seen: set[str] = set()

    # 1. Backticked tokens — always high-confidence
    for token in re.findall(r"`([^`]+)`", what):
        for part in token.split("."):
            part = part.strip()
            if part and IDENTIFIER_PATTERN.fullmatch(part) and part not in seen:
                ids.append(part)
                seen.add(part)

    # 2. Remove backticks for prose scan
    clean = re.sub(r"[`*_\[\](){}]", " ", what)

    for token in IDENTIFIER_PATTERN.findall(clean):
        if len(token) < 4 or token in seen:
            continue
        # Only keep underscore-containing or private-starting tokens
        if "_" in token or token.startswith("_"):
            ids.append(token)
            seen.add(token)

    return ids


def verify_ref(ref: Ref, src_root: Path) -> Result:
    """Verify a single source reference."""
    issues = []
    
    # Try multiple path resolutions: as-is, under lingtai/, under lingtai_kernel/
    candidates = [src_root / ref.file]
    if not ref.file.startswith("lingtai/") and not ref.file.startswith("lingtai_kernel/"):
        candidates.append(src_root / "lingtai" / ref.file)
        candidates.append(src_root / "lingtai_kernel" / ref.file)
    
    file_path = None
    for c in candidates:
        if c.is_file():
            file_path = c
            break
    
    if file_path is None:
        return Result(ref=ref, ok=False, issues=[f"File not found: {ref.file}"])
    
    # 2. Line bounds
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return Result(ref=ref, ok=False, issues=[f"Cannot read {ref.file}: {e}"])
    
    total_lines = len(lines)
    if ref.line_start > total_lines:
        issues.append(f"Line {ref.line_start} exceeds file length ({total_lines} lines)")
        return Result(ref=ref, ok=False, issues=issues)
    if ref.line_end > total_lines:
        issues.append(f"Line {ref.line_end} exceeds file length ({total_lines} lines) — was {ref.line_end}, file has {total_lines}")
    
    # 3. Identifier presence check (with drift tolerance)
    identifiers = extract_identifiers(ref.what)
    if identifiers:
        # Search within [line_start - tolerance, line_end + tolerance]
        search_start = max(0, ref.line_start - DRIFT_TOLERANCE - 1)
        search_end = min(total_lines, ref.line_end + DRIFT_TOLERANCE)
        search_block = "\n".join(lines[search_start:search_end])
        
        missing = [id_ for id_ in identifiers if id_ not in search_block]
        if missing:
            issues.append(
                f"Identifiers not found in lines {search_start+1}-{search_end}: "
                f"{', '.join(missing)} (tolerance ±{DRIFT_TOLERANCE})"
            )
    
    ok = len(issues) == 0
    return Result(ref=ref, ok=ok, issues=issues)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        leaves_dir = Path(sys.argv[1])
    else:
        leaves_dir = Path(__file__).parent / "leaves" / "capabilities" / "mail"
    
    if len(sys.argv) > 2:
        src_root = Path(sys.argv[2])
    else:
        # Default: repo root / src (contains lingtai/ and lingtai_kernel/)
        src_root = Path(__file__).parent.parent.parent.parent.parent / "src"
    
    if not leaves_dir.is_dir():
        print(f"ERROR: Leaves directory not found: {leaves_dir}", file=sys.stderr)
        sys.exit(2)
    if not src_root.is_dir():
        print(f"ERROR: Source root not found: {src_root}", file=sys.stderr)
        sys.exit(2)
    
    # Collect all READMEs (leaf-level and index)
    readmes = sorted(leaves_dir.rglob("README.md"))
    
    all_refs: list[Ref] = []
    for readme in readmes:
        # Derive leaf name from directory structure
        rel = readme.relative_to(leaves_dir)
        leaf_name = str(rel.parent) if str(rel.parent) != "." else "(index)"
        refs = parse_source_table(readme, leaf_name)
        all_refs.extend(refs)
    
    if not all_refs:
        print("No source references found.")
        sys.exit(0)
    
    # Verify
    results = [verify_ref(ref, src_root) for ref in all_refs]
    passed = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    
    # Report
    print(f"Verified {len(results)} source references across {len(readmes)} READMEs")
    print(f"  ✓ {len(passed)} OK")
    print(f"  ✗ {len(failed)} broken")
    print()
    
    if failed:
        for r in failed:
            print(f"  FAIL [{r.ref.leaf}] {r.ref.what}")
            print(f"       {r.ref.file}:{r.ref.line_start}–{r.ref.line_end}")
            for issue in r.issues:
                print(f"       → {issue}")
            print()
        sys.exit(1)
    else:
        print("All references valid.")
        sys.exit(0)


if __name__ == "__main__":
    main()
