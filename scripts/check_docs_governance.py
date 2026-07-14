#!/usr/bin/env python3
"""Standalone docs-governance validator (not a hosted CI workflow — this
repository has no PR-triggered pytest workflow today; only wheels.yml
exists, release-triggered, no test step). Runnable locally/anywhere and
imported by tests/test_docs_governance.py so pytest exercises identical
logic.

docs.yaml is mechanically authoritative. related_files.target_policy is
owner_validated_relative_link: this checker validates syntax (non-empty,
unique, clean repo-relative POSIX-ish string, no self-reference, no
placeholder) for EVERY document's related_files, but does NOT require
universal existence — many existing owners (Anatomy/Contract twins, the
prompt-source progressive-disclosure crawl graph, the molt-gate
session-journal convention) already encode owner-specific logical/crawl
link semantics that predate this contract and remain validated by their
own specialized systems, not by this generic layer.

Usage:
    python scripts/check_docs_governance.py --check
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS_YAML = ROOT / "docs.yaml"

EXPECTED_TOP_LEVEL_KEYS = frozenset(
    {
        "name", "version", "extensions", "discovery", "required_fields",
        "field_constraints", "metadata_modes", "metadata_mode_overrides",
        "placeholder_patterns", "related_files", "maintenance",
    }
)
EXPECTED_CONTRACT_VERSION = 6
EXPECTED_CONTRACT_NAME = "docs-governance-contract"
EXPECTED_EXTENSIONS = [".md"]
EXPECTED_DISCOVERY = {"tracked": True, "untracked_not_ignored": True}
EXPECTED_REQUIRED_FIELDS = ["related_files", "maintenance"]
EXPECTED_FIELD_CONSTRAINT_KEYS = frozenset({"related_files", "maintenance"})
EXPECTED_RELATED_FILES_CONSTRAINT_KEYS = frozenset(
    {"type", "min_items", "unique", "no_self_reference", "target_policy"}
)
EXPECTED_TARGET_POLICIES = frozenset({"owner_validated_relative_link"})
EXPECTED_MAINTENANCE_CONSTRAINT_KEYS = frozenset(
    {"type", "min_length", "forbidden_exact_values"}
)
EXPECTED_METADATA_MODE_NAMES = frozenset({"frontmatter", "html_comment"})
EXPECTED_METADATA_MODE_OVERRIDES = {".github/PULL_REQUEST_TEMPLATE.md": "html_comment"}


class ContractError(RuntimeError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_no_duplicates(loader, node, deep=False):
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(None, None, "expected a mapping node", node.start_mark)
    mapping = {}
    for key_node, value_node in node.value:
        try:
            key = loader.construct_object(key_node, deep=deep)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                None, None, f"key is not constructible: {exc}", key_node.start_mark
            ) from exc
        try:
            hash(key)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                None, None, f"key is unhashable: {key!r}", key_node.start_mark
            ) from exc
        value = loader.construct_object(value_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key: {key!r}", key_node.start_mark)
        mapping[key] = value
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_no_duplicates
)


def safe_load_unique(text: str):
    return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506


def validate_docs_contract_shape(contract: object) -> dict:
    if not isinstance(contract, dict):
        raise ContractError("docs.yaml: top level is not a mapping")
    actual_keys = set(contract.keys())
    if actual_keys != EXPECTED_TOP_LEVEL_KEYS:
        extra = actual_keys - EXPECTED_TOP_LEVEL_KEYS
        missing = EXPECTED_TOP_LEVEL_KEYS - actual_keys
        parts = []
        if extra:
            parts.append(f"unrecognized top-level keys: {sorted(map(str, extra))}")
        if missing:
            parts.append(f"missing top-level keys: {sorted(missing)}")
        raise ContractError(f"docs.yaml: {'; '.join(parts)}")
    if contract.get("name") != EXPECTED_CONTRACT_NAME:
        raise ContractError(f"docs.yaml: name must be {EXPECTED_CONTRACT_NAME!r}")
    if contract.get("version") != EXPECTED_CONTRACT_VERSION:
        raise ContractError(f"docs.yaml: unsupported version {contract.get('version')!r}")
    if contract.get("extensions") != EXPECTED_EXTENSIONS:
        raise ContractError("docs.yaml: extensions must be exactly ['.md']")
    if contract.get("discovery") != EXPECTED_DISCOVERY:
        raise ContractError(f"docs.yaml: discovery must be exactly {EXPECTED_DISCOVERY!r}")
    if contract.get("required_fields") != EXPECTED_REQUIRED_FIELDS:
        raise ContractError(f"docs.yaml: required_fields must be exactly {EXPECTED_REQUIRED_FIELDS!r}")

    fc = contract.get("field_constraints")
    if not isinstance(fc, dict) or set(fc) != EXPECTED_FIELD_CONSTRAINT_KEYS:
        raise ContractError("docs.yaml: field_constraints has the wrong key set")
    rf = fc["related_files"]
    if not isinstance(rf, dict) or set(rf) != EXPECTED_RELATED_FILES_CONSTRAINT_KEYS:
        raise ContractError("docs.yaml: field_constraints.related_files has the wrong key set")
    if rf.get("type") != "list" or not isinstance(rf.get("min_items"), int) or isinstance(rf.get("min_items"), bool) or rf["min_items"] < 1:
        raise ContractError("docs.yaml: field_constraints.related_files has an invalid type/min_items")
    if rf.get("unique") is not True:
        raise ContractError("docs.yaml: field_constraints.related_files.unique must be true")
    if rf.get("no_self_reference") is not True:
        raise ContractError("docs.yaml: field_constraints.related_files.no_self_reference must be true")
    if rf.get("target_policy") not in EXPECTED_TARGET_POLICIES:
        raise ContractError(
            f"docs.yaml: field_constraints.related_files.target_policy must be one of "
            f"{sorted(EXPECTED_TARGET_POLICIES)}"
        )
    maint = fc["maintenance"]
    if not isinstance(maint, dict) or set(maint) != EXPECTED_MAINTENANCE_CONSTRAINT_KEYS:
        raise ContractError("docs.yaml: field_constraints.maintenance has the wrong key set")
    if maint.get("type") != "string" or not isinstance(maint.get("min_length"), int) or isinstance(maint.get("min_length"), bool) or maint["min_length"] < 1:
        raise ContractError("docs.yaml: field_constraints.maintenance has an invalid type/min_length")
    forbidden = maint.get("forbidden_exact_values")
    if not isinstance(forbidden, list) or not forbidden or not all(
        isinstance(item, str) and item for item in forbidden
    ):
        raise ContractError(
            "docs.yaml: field_constraints.maintenance.forbidden_exact_values "
            "must be a non-empty list of strings"
        )
    normalized_forbidden = [item.strip().casefold() for item in forbidden]
    if forbidden != normalized_forbidden or len(forbidden) != len(set(forbidden)):
        raise ContractError(
            "docs.yaml: field_constraints.maintenance.forbidden_exact_values "
            "must contain unique, stripped, casefolded values"
        )

    modes = contract.get("metadata_modes")
    if not isinstance(modes, dict) or set(modes) != EXPECTED_METADATA_MODE_NAMES:
        raise ContractError("docs.yaml: metadata_modes has the wrong key set")
    defaults = [n for n, m in modes.items() if isinstance(m, dict) and m.get("applies_to") == "*"]
    if len(defaults) != 1:
        raise ContractError(f"docs.yaml: expected exactly one default metadata mode, found {len(defaults)}")

    overrides = contract.get("metadata_mode_overrides")
    if overrides != EXPECTED_METADATA_MODE_OVERRIDES:
        raise ContractError(f"docs.yaml: metadata_mode_overrides must be exactly {EXPECTED_METADATA_MODE_OVERRIDES!r}")
    for path, mode_name in overrides.items():
        if mode_name not in modes:
            raise ContractError(f"docs.yaml: metadata_mode_overrides[{path!r}] references unsupported mode {mode_name!r}")

    patterns = contract.get("placeholder_patterns")
    if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) for p in patterns):
        raise ContractError("docs.yaml: placeholder_patterns must be a non-empty list of strings")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ContractError(f"docs.yaml: bad placeholder pattern {pattern!r}: {exc}") from exc

    return contract


def load_docs_contract() -> dict:
    text = DOCS_YAML.read_text(encoding="utf-8")
    try:
        contract = safe_load_unique(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"docs.yaml: YAML parse error: {exc}") from exc
    return validate_docs_contract_shape(contract)


def compiled_placeholder_patterns(contract: dict) -> list:
    return [re.compile(p) for p in contract["placeholder_patterns"]]


def _is_clean_repo_relative_posix_path(rp) -> str | None:
    if not isinstance(rp, str):
        return f"not a string: {rp!r}"
    if not rp:
        return "empty path"
    if rp != rp.strip():
        return f"has leading/trailing whitespace: {rp!r}"
    if re.match(r"^[A-Za-z]:", rp):
        return f"contains a Windows drive designator: {rp!r}"
    if "\\" in rp:
        return f"contains a backslash: {rp!r}"
    if rp.startswith("/") or rp.endswith("/"):
        return f"absolute or trailing-slash path: {rp!r}"
    # Validate raw segments instead of normalizing with PurePosixPath: path
    # normalization would silently erase './' and repeated-slash segments.
    for segment in rp.split("/"):
        if segment in ("", ".", ".."):
            return f"contains an empty/'.'/'..' segment: {rp!r}"
    return None


def validate_metadata_mapping(rel: str, meta, contract: dict) -> list[str]:
    """SYNTAX-ONLY generic validation for related_files under
    target_policy: owner_validated_relative_link — no repo_root, no
    existence check. This is intentional: see module docstring."""
    if not isinstance(meta, dict):
        return [f"{rel}: metadata did not parse to a mapping"]
    failures: list[str] = []
    patterns = compiled_placeholder_patterns(contract)
    constraints = contract["field_constraints"]
    for field in contract["required_fields"]:
        if field not in meta:
            failures.append(f"{rel}: missing required field {field!r}")
            continue
        value = meta[field]
        if field == "related_files":
            c = constraints["related_files"]
            if not isinstance(value, list):
                failures.append(f"{rel}: related_files must be a list, got {type(value).__name__}")
                continue
            if len(value) < c["min_items"]:
                failures.append(f"{rel}: related_files must have at least {c['min_items']} item(s)")
                continue
            hashable_items = []
            for item in value:
                path_error = _is_clean_repo_relative_posix_path(item)
                if path_error is not None:
                    failures.append(f"{rel}: related_files invalid entry: {path_error}")
                    continue
                hashable_items.append(item)
            if c["unique"] and len(hashable_items) != len(set(hashable_items)):
                failures.append(f"{rel}: related_files contains duplicate paths")
            if c["no_self_reference"] and rel in hashable_items:
                failures.append(f"{rel}: related_files self-reference")
            for rp in hashable_items:
                for pat in patterns:
                    if pat.search(rp):
                        failures.append(f"{rel}: placeholder left unfilled in related_files: {rp!r}")
        elif field == "maintenance":
            c = constraints["maintenance"]
            if not isinstance(value, str):
                failures.append(f"{rel}: maintenance must be a string, got {type(value).__name__}")
                continue
            normalized_value = value.strip().casefold()
            if normalized_value in c["forbidden_exact_values"]:
                failures.append(
                    f"{rel}: maintenance is a forbidden exact placeholder value: {value!r}"
                )
                continue
            if len(value.strip()) < c["min_length"]:
                failures.append(
                    f"{rel}: maintenance must contain at least "
                    f"{c['min_length']} non-whitespace characters"
                )
                continue
            for pat in patterns:
                if pat.search(value):
                    failures.append(f"{rel}: placeholder left unfilled in maintenance: {value!r}")
    return failures


def _mode_for(rel: str, contract: dict) -> str | None:
    override = contract["metadata_mode_overrides"].get(rel)
    if override is not None:
        return override if override in contract["metadata_modes"] else None
    for name, mode in contract["metadata_modes"].items():
        if isinstance(mode, dict) and mode.get("applies_to") == "*":
            return name
    return None


def parse_doc_metadata_text(rel: str, text: str, contract: dict):
    mode = _mode_for(rel, contract)
    if mode is None:
        return None, f"{rel}: no supported metadata mode resolved (contract drift)"
    if mode == "html_comment":
        if not text.lstrip().startswith("<!--"):
            return None, f"{rel}: missing HTML-comment metadata block"
        start = text.index("<!--") + 4
        if "-->" not in text[start:]:
            return None, f"{rel}: malformed HTML-comment metadata block (no closing -->)"
        end = text.index("-->", start)
        raw = text[start:end]
    elif mode == "frontmatter":
        if not text.startswith("---\n"):
            return None, f"{rel}: missing or unparseable frontmatter (no --- fence)"
        lines = text.splitlines(keepends=True)
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].rstrip("\n") == "---":
                end_idx = i
                break
        if end_idx is None:
            return None, f"{rel}: frontmatter opening fence with no closing fence"
        raw = "".join(lines[1:end_idx])
    else:
        return None, f"{rel}: unsupported metadata mode {mode!r} (contract drift)"
    try:
        meta = safe_load_unique(raw)
    except yaml.YAMLError as exc:
        return None, f"{rel}: YAML parse error: {exc}"
    if not isinstance(meta, dict):
        return None, f"{rel}: metadata did not parse to a mapping"
    return meta, None


def _git_files(*args: str) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in proc.stdout.splitlines() if line]


def discover_doc_paths(contract: dict) -> list[Path]:
    exts = tuple(contract["extensions"])
    tracked = _git_files() if contract["discovery"].get("tracked") else []
    untracked = (
        _git_files("--others", "--exclude-standard")
        if contract["discovery"].get("untracked_not_ignored") else []
    )
    all_paths = sorted(set(tracked) | set(untracked))
    return [ROOT / p for p in all_paths if p.endswith(exts)]


def check_one_document_path(path: Path, contract: dict, *, repo_root: Path) -> list[str]:
    rel = path.relative_to(repo_root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}: could not read file: {exc}"]
    meta, error = parse_doc_metadata_text(rel, text, contract)
    if error is not None:
        return [error]
    return validate_metadata_mapping(rel, meta, contract)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 1

    try:
        contract = load_docs_contract()
    except ContractError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    all_failures: list[str] = []
    paths = discover_doc_paths(contract)
    for path in paths:
        all_failures.extend(check_one_document_path(path, contract, repo_root=ROOT))
    # docs.yaml validates its OWN related_files/maintenance via the same
    # shared function — never fed through Markdown/comment parsing.
    all_failures.extend(validate_metadata_mapping("docs.yaml", contract, contract))

    if all_failures:
        print("FAIL: docs governance validation found violations:", file=sys.stderr)
        for failure in all_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"OK: {len(paths)} documents (+ docs.yaml itself) validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
