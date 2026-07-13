#!/usr/bin/env python3
"""Validate first-party tool glossary package resources.

The expected owner set is the union of the canonical tool registry and every
installed ``tools.<package>`` resource directory that owns ``CONTRACT.md``.
Validation reads through :mod:`importlib.resources`, so the same command works
from a source checkout and from an installed wheel::

    python -m lingtai.tools.glossary_validator --check

The validator and runtime loader share
:func:`lingtai.kernel.tool_glossary.parse_glossary`; there is one strict grammar
for duplicate keys, required fields, types, and package/language identity.
"""

from __future__ import annotations

import argparse
import sys
from importlib import resources as importlib_resources

from lingtai.kernel.tool_glossary import (
    GlossaryValidationError,
    SUPPORTED_TOOL_GLOSSARY_LANGUAGES as SUPPORTED_LANGS,
    parse_glossary,
)

__all__ = ["discover_packages", "validate_package", "cross_check_registry", "main"]

_RESOURCE_ERRORS = (
    FileNotFoundError,
    ModuleNotFoundError,
    ImportError,
    OSError,
    TypeError,
    AttributeError,
)


def _registry_packages() -> set[str]:
    """Return package names from the canonical first-party tool registry."""
    from lingtai.tools.registry import BUILTIN_TOOLS, INTRINSICS

    packages = {path.rsplit(".", 1)[-1] for path in BUILTIN_TOOLS.values()}
    packages.update(INTRINSICS.keys())
    return packages


def _contract_packages() -> set[str]:
    """Return installed ``tools.<pkg>`` resource dirs owning ``CONTRACT.md``."""
    root = importlib_resources.files("lingtai.tools")
    packages: set[str] = set()
    for child in root.iterdir():
        if child.is_dir() and child.joinpath("CONTRACT.md").is_file():
            packages.add(child.name)
    return packages


# Packages that own a CONTRACT.md but are intentionally EXEMPT from the localized
# tool-glossary system. ``task_card`` is the public programmable-Task-Card tool:
# it is agent-only and English-only, so per the root CONTRACT.md ``## Design
# principles`` (no i18n on an agent-only surface without human confirmation) it
# ships no ``glossary-{en,zh,wen}.md``. It is registered by the composition root,
# not by ``registry.BUILTIN_TOOLS``, so it is never a registry glossary owner
# either — excluding it here keeps the validator from demanding a glossary the
# design principles deliberately withhold.
_GLOSSARY_EXEMPT = frozenset({"task_card"})


def discover_packages() -> list[str]:
    """Discover glossary owners from registry plus CONTRACT package resources.

    Glossary-exempt packages (``_GLOSSARY_EXEMPT`` — English-only, agent-only
    tools that carry no localized glossary) are excluded so the validator does not
    require ``glossary-{en,zh,wen}.md`` from a surface the root design principles
    keep out of the i18n system.
    """
    return sorted((_registry_packages() | _contract_packages()) - _GLOSSARY_EXEMPT)


def _validate_resource(resource, full_pkg: str, lang: str) -> list[str]:
    """Validate one package resource with the kernel-owned strict grammar."""
    label = f"{full_pkg}/glossary-{lang}.md"
    try:
        text = resource.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{label}: not valid UTF-8: {exc}"]
    except _RESOURCE_ERRORS as exc:
        return [
            f"{label}: could not read package resource: {type(exc).__name__}: {exc}"
        ]

    try:
        body = parse_glossary(text, tool_package=full_pkg, language=lang)
    except GlossaryValidationError as exc:
        return [f"{label}: {exc}"]

    if lang == "en" and body.strip():
        return [f"{label}: English body must be empty"]
    if lang != "en" and not body.strip():
        return [f"{label}: non-English body must be non-empty"]
    return []


def validate_package(pkg: str) -> list[str]:
    """Validate the exact glossary resource set for one importable package."""
    errors: list[str] = []
    full_pkg = f"lingtai.tools.{pkg}"
    try:
        root = importlib_resources.files(full_pkg)
        entries = list(root.iterdir())
    except _RESOURCE_ERRORS as exc:
        return [
            f"{full_pkg}: could not inspect package resources: "
            f"{type(exc).__name__}: {exc}"
        ]

    glossary_entries = {
        entry.name: entry
        for entry in entries
        if entry.is_file()
        and entry.name.startswith("glossary-")
        and entry.name.endswith(".md")
    }
    supported_names = {f"glossary-{lang}.md" for lang in SUPPORTED_LANGS}

    for name in sorted(set(glossary_entries) - supported_names):
        lang = name.removeprefix("glossary-").removesuffix(".md")
        errors.append(f"{pkg}: unsupported glossary language: {lang}")

    for lang in SUPPORTED_LANGS:
        name = f"glossary-{lang}.md"
        resource = glossary_entries.get(name)
        if resource is None:
            errors.append(f"{pkg}: missing {name}")
            continue
        errors.extend(_validate_resource(resource, full_pkg, lang))
    return errors


def cross_check_registry() -> list[str]:
    """Require every registry package to own an installed CONTRACT resource."""
    registry_packages = _registry_packages()
    contract_packages = _contract_packages()
    missing_contract = registry_packages - contract_packages
    if not missing_contract:
        return []
    return [
        f"registry packages without CONTRACT.md resource: {sorted(missing_contract)}"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate first-party tool glossary package resources."
    )
    parser.add_argument("--check", action="store_true", help="Run all checks.")
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 1

    errors: list[str] = []
    try:
        errors.extend(cross_check_registry())
        packages = discover_packages()
    except _RESOURCE_ERRORS as exc:
        packages = []
        errors.append(
            f"could not discover tool package resources: {type(exc).__name__}: {exc}"
        )

    for pkg in packages:
        errors.extend(validate_package(pkg))

    if errors:
        print("FAIL: tool glossary validation found violations:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    total = len(packages) * len(SUPPORTED_LANGS)
    print(f"OK: {total} glossary resources across {len(packages)} packages validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
