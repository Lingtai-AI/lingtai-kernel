#!/usr/bin/env python3
"""
verify-links.py — Validate internal cross-references in lingtai-kernel-anatomy.

Checks every markdown link of the form `[text](path)` in all .md files
under the anatomy skill directory. Reports:
  - BROKEN: target file does not exist relative to the source file
  - ANCHOR: link contains #anchor — warns that anchor isn't checked
  - EXTERNAL: link starts with http(s) — skipped
  - OK: target resolves

Usage:
  python3 verify-links.py [--root <anatomy-root>] [--verbose]

Defaults to the standard anatomy root path.
Exit code 0 if no broken links, 1 if any broken.
"""

import re
import os
import sys
import argparse
from pathlib import Path

LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

def find_md_files(root: Path):
    return sorted(root.rglob('*.md'))

def extract_links(filepath: Path):
    """Yield (line_number, display_text, target, raw_line) for each markdown link."""
    with open(filepath, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            for m in LINK_RE.finditer(line):
                yield lineno, m.group(1), m.group(2), line.rstrip()

def check_link(source: Path, target: str, root: Path):
    """Return ('OK'|'BROKEN'|'EXTERNAL'|'ANCHOR', detail_string)."""
    # External links
    if target.startswith('http://') or target.startswith('https://'):
        return 'EXTERNAL', target

    # Split anchor
    if '#' in target:
        path_part, anchor = target.rsplit('#', 1)
        if not path_part:
            return 'ANCHOR', f'self-anchor #{anchor}'
    else:
        path_part = target
        anchor = None

    if not path_part:
        return 'ANCHOR', f'self-anchor #{anchor}'

    # Resolve relative to source directory
    source_dir = source.parent if source.is_file() else source
    resolved = (source_dir / path_part).resolve()

    # Check if it's a directory (means README.md inside it, in the tree structure)
    if resolved.is_dir():
        readme = resolved / 'README.md'
        if readme.exists():
            status = 'OK'
        else:
            status = 'BROKEN'
        detail = f'{path_part} → {resolved}/' + ('README.md' if status == 'OK' else ' (no README.md)')
        if anchor and status == 'OK':
            detail += f' #{anchor}'
            status = 'ANCHOR'
        return status, detail

    if resolved.exists():
        status = 'OK'
        detail = str(resolved.relative_to(root))
        if anchor:
            detail += f' #{anchor}'
            status = 'ANCHOR'
        return status, detail

    return 'BROKEN', f'{path_part} → {resolved} (not found)'

def main():
    parser = argparse.ArgumentParser(description='Verify internal cross-references in anatomy .md files')
    parser.add_argument('--root', type=str,
                        default=os.path.expanduser(
                            '/Users/huangzesen/Documents/GitHub/lingtai-kernel/src/lingtai/'
                            'intrinsic_skills/lingtai-kernel-anatomy'),
                        help='Root of the anatomy skill directory')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show OK and ANCHOR links too, not just BROKEN')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'ERROR: root directory not found: {root}', file=sys.stderr)
        sys.exit(2)

    md_files = find_md_files(root)
    if not md_files:
        print(f'WARNING: no .md files found under {root}', file=sys.stderr)
        sys.exit(0)

    broken_count = 0
    anchor_count = 0
    ok_count = 0
    external_count = 0
    broken_details = []

    for md_file in md_files:
        rel = md_file.relative_to(root)
        for lineno, text, target, raw_line in extract_links(md_file):
            status, detail = check_link(md_file, target, root)
            if status == 'BROKEN':
                broken_count += 1
                broken_details.append(f'  BROKEN: {rel}:{lineno} → [{text}]({target})')
                broken_details.append(f'          {detail}')
            elif status == 'ANCHOR':
                anchor_count += 1
                if args.verbose:
                    print(f'  ANCHOR: {rel}:{lineno} → [{text}]({target}) — {detail}')
            elif status == 'OK':
                ok_count += 1
                if args.verbose:
                    print(f'  OK:     {rel}:{lineno} → [{text}]({target})')
            elif status == 'EXTERNAL':
                external_count += 1
                if args.verbose:
                    print(f'  EXT:    {rel}:{lineno} → [{text}]({target})')

    # Summary
    print(f'\n=== Link Verification Report ===')
    print(f'Root:    {root}')
    print(f'Files:   {len(md_files)} .md files scanned')
    print(f'OK:      {ok_count}')
    print(f'ANCHOR:  {anchor_count} (anchor targets not checked — manual or future)')
    print(f'EXT:     {external_count} (external links — skipped)')
    print(f'BROKEN:  {broken_count}')

    if broken_details:
        print(f'\n--- Broken Links ---')
        for line in broken_details:
            print(line)
        print(f'\nResult: FAIL ({broken_count} broken link(s))')
        sys.exit(1)
    else:
        print(f'\nResult: PASS (no broken internal links)')
        sys.exit(0)

if __name__ == '__main__':
    main()
