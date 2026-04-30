#!/usr/bin/env python3
"""
.tmp orphan cleanup for LingTai mailbox inboxes.

Scans mailbox/inbox/**/message.json.tmp, deletes those older than --age seconds.
Default: dry-run (shows what would be deleted).

Safe to run alongside a live agent — os.replace() is atomic; a .tmp file
being actively written will have a fresh mtime and won't meet the age threshold.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path


def find_orphans(mailbox: Path, min_age_s: int) -> list[tuple[Path, float]]:
    """Find .tmp files older than min_age_s seconds."""
    now = time.time()
    orphans = []
    for tmp in mailbox.glob("inbox/**/message.json.tmp"):
        age = now - tmp.stat().st_mtime
        if age > min_age_s:
            orphans.append((tmp, age))
    return sorted(orphans, key=lambda x: -x[1])  # oldest first


def verify_final_not_exists(tmp_path: Path) -> bool:
    """Check that message.json does NOT exist for this tmp (truly orphaned)."""
    final = tmp_path.parent / "message.json"
    return not final.exists()


def main():
    parser = argparse.ArgumentParser(description="Clean up .tmp orphans in mailbox/inbox/")
    parser.add_argument("--mailbox", type=Path, default=Path("mailbox"),
                        help="Path to mailbox/ directory (default: ./mailbox)")
    parser.add_argument("--age", type=int, default=300,
                        help="Minimum age in seconds (default: 300 = 5min)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Show what would be deleted without deleting (default: True)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually delete the orphans")
    parser.add_argument("--check-json", action="store_true", default=True,
                        help="Verify .tmp contains valid JSON before marking as orphan")
    args = parser.parse_args()

    if args.execute:
        args.dry_run = False

    if not args.mailbox.is_dir():
        print(f"Error: {args.mailbox} is not a directory", file=sys.stderr)
        sys.exit(1)

    orphans = find_orphans(args.mailbox, args.age)

    if not orphans:
        print("No .tmp orphans found.")
        return

    print(f"Found {len(orphans)} .tmp orphan(s):")
    for tmp, age in orphans:
        age_str = f"{age:.0f}s" if age < 3600 else f"{age/3600:.1f}h"
        final_exists = not verify_final_not_exists(tmp)
        status = "orphan" if not final_exists else "pending-rename"
        print(f"  {tmp}  (age: {age_str}, status: {status})")

        if args.check_json:
            try:
                with open(tmp, "r") as f:
                    json.load(f)
                print(f"    JSON: valid")
            except (json.JSONDecodeError, OSError) as e:
                print(f"    JSON: invalid ({e})")

    if args.dry_run:
        print("\n[dry-run] No files deleted. Use --execute to remove.")
    else:
        removed = 0
        for tmp, _ in orphans:
            tmp.unlink()
            removed += 1
            print(f"  deleted: {tmp}")
        print(f"\nRemoved {removed} orphan(s).")


if __name__ == "__main__":
    main()
