#!/usr/bin/env python3
"""Print an installed MCP package README, preferring editable source then wheel
``METADATA``. Exit 2 means no local README; use the registry homepage with
Web ``browse``. The README is authoritative for install, config fields, env vars,
and troubleshooting; this utility performs no installation or network access.

Usage: ``find_readme.py <distribution>`` or
``find_readme.py --module <importable-module>``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import importlib.metadata as md


def _find_editable_readme(dist: md.Distribution) -> tuple[str, str] | None:
    """Return an editable-install README and its source label, or ``None``."""
    try:
        durl_text = dist.read_text("direct_url.json")
    except (FileNotFoundError, OSError):
        return None
    if not durl_text:
        return None
    try:
        durl = json.loads(durl_text)
    except ValueError:
        return None
    if not durl.get("dir_info", {}).get("editable"):
        return None
    url = durl.get("url", "")
    if not url.startswith("file://"):
        return None
    repo = Path(url[len("file://"):])
    for cand in ("README.md", "README.rst", "README.txt", "README"):
        p = repo / cand
        if p.exists():
            try:
                return p.read_text(encoding="utf-8"), f"editable:{p}"
            except OSError:
                continue
    return None


def _find_metadata_readme(dist: md.Distribution, pkg_name: str) -> tuple[str, str] | None:
    """Return the wheel ``METADATA`` README, or ``None``."""
    meta = dist.metadata
    body: str | None = None
    if hasattr(meta, "get_payload"):
        try:
            body = meta.get_payload()
        except Exception:
            body = None
    if not body:
        try:
            raw = dist.read_text("METADATA")
        except (FileNotFoundError, OSError):
            raw = None
        if raw and "\n\n" in raw:
            body = raw.split("\n\n", 1)[1]
    if body and body.strip():
        return body, f"dist-info:{pkg_name} METADATA"
    return None


def find_readme(pkg_name: str) -> tuple[str | None, str]:
    """Return ``(content, source-or-error)`` using local README sources only."""
    try:
        dist = md.distribution(pkg_name)
    except md.PackageNotFoundError:
        return None, f"package not installed: {pkg_name}"

    found = _find_editable_readme(dist)
    if found is not None:
        return found

    found = _find_metadata_readme(dist, pkg_name)
    if found is not None:
        return found

    return None, f"no README found locally for {pkg_name}"


def _resolve_module_to_dist(module_name: str) -> str | None:
    """Resolve an importable module to its installed distribution, if any."""
    try:
        dists = md.packages_distributions().get(module_name, [])
    except Exception:
        dists = []
    if dists:
        return dists[0]

    # Editable-install fallback: try `module_name` and `module_name.replace("_", "-")`.
    for candidate in (module_name, module_name.replace("_", "-")):
        try:
            md.distribution(candidate)
            return candidate
        except md.PackageNotFoundError:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("name", help="distribution name (default) or module name with --module")
    ap.add_argument(
        "--module",
        action="store_true",
        help="treat <name> as an importable module name; resolve to the owning distribution",
    )
    args = ap.parse_args()

    pkg = args.name
    if args.module:
        resolved = _resolve_module_to_dist(args.name)
        if resolved is None:
            print(f"ERROR: cannot resolve module '{args.name}' to an installed distribution", file=sys.stderr)
            return 1
        pkg = resolved

    content, source = find_readme(pkg)
    if content is None:
        print(f"ERROR: {source}", file=sys.stderr)
        print("HINT: fall back to web_read on the registry's <homepage> URL", file=sys.stderr)
        return 2

    print(f"# Source: {source}", file=sys.stderr)
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
