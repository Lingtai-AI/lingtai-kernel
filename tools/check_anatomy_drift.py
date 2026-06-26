#!/usr/bin/env python3
"""Cheap, advisory ANATOMY drift checker (issue #509).

ANATOMY.md files are the kernel's navigation map. The most common drift mode is
mechanically detectable:

**Citation rot** — a `file.py:line` citation points at a missing file or a line
past the end of the file (after a refactor moved/shrank the code).

This does NOT prove semantic correctness — a citation can be in-range yet point
at the wrong code. An agent still has to open the cited line and confirm the
claim (see the `lingtai-kernel-anatomy` skill). This checker only catches the
*obvious* citation drift cheaply, so it can run in CI or pre-commit as an
advisory gate.

Usage:
    python tools/check_anatomy_drift.py            # report, exit 0 unless --check
    python tools/check_anatomy_drift.py --check    # exit 1 if any drift found
    python tools/check_anatomy_drift.py --root src/lingtai_kernel
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# `file.py` or `file.py:123` or `file.py:123-456`, optionally backticked.
_CITATION_RE = re.compile(r"([A-Za-z0-9_./-]+\.py):(\d+)(?:-(\d+))?")


def find_anatomy_files(root: Path) -> list[Path]:
    return sorted(root.rglob("ANATOMY.md"))


def resolve_path(rel: str, anatomy: Path, repo_root: Path) -> Path | None:
    """Resolve a cited path by searching upward from the anatomy's directory.

    Anatomy citations are written relative to some ancestor folder (often the
    kernel root), e.g. `base_agent/__init__.py` cited from
    `.../lingtai_kernel/base_agent/ANATOMY.md`. Try the anatomy's directory and
    each ancestor up to the repo root; also honour explicit `src/`-rooted paths.
    """
    if rel.startswith("src/"):
        cand = repo_root / rel
        return cand if cand.is_file() else None
    base = anatomy.parent
    while True:
        cand = base / rel
        if cand.is_file():
            return cand
        if base == repo_root or base.parent == base:
            return None
        base = base.parent


def line_count(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for _ in f)


def check_citations(anatomy: Path, repo_root: Path) -> list[str]:
    problems: list[str] = []
    text = anatomy.read_text(encoding="utf-8")
    for m in _CITATION_RE.finditer(text):
        rel, start, end = m.group(1), int(m.group(2)), m.group(3)
        target = resolve_path(rel, anatomy, repo_root)
        if target is None:
            problems.append(f"missing citation target {rel}:{start}")
            continue
        n = line_count(target)
        hi = int(end) if end else start
        if hi > n:
            problems.append(f"out-of-range citation {rel}:{m.group(0).split(':',1)[1]} > {n} lines")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="src",
        help="directory to scan for ANATOMY.md files (default: src)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if any drift is found (for CI / pre-commit)",
    )
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    root = Path(args.root)
    if not root.is_absolute():
        root = repo_root / root

    anatomy_files = find_anatomy_files(root)
    if not anatomy_files:
        print(f"no ANATOMY.md files found under {root}", file=sys.stderr)
        return 0

    total = 0
    for anatomy in anatomy_files:
        problems = check_citations(anatomy, repo_root)
        if problems:
            rel = anatomy.relative_to(repo_root) if anatomy.is_relative_to(repo_root) else anatomy
            print(f"\n{rel}:")
            for p in problems:
                print(f"  - {p}")
            total += len(problems)

    if total:
        print(f"\n{total} anatomy drift item(s) found across {len(anatomy_files)} file(s).")
        return 1 if args.check else 0
    print(f"No anatomy drift found across {len(anatomy_files)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
