from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROOT_ANATOMY = ROOT / "ANATOMY.md"
ROOT_CONTRACT = ROOT / "CONTRACT.md"
ROOT_README = ROOT / "README.md"
ROOT_DEV_SKILL = ROOT / "dev-guide-skill/SKILL.md"
ANATOMY_SKILL = ROOT / "src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md"

CHILD_ANATOMY_MAINTENANCE = """Keep related_files repo-relative, duplicate-free, and linked to real files.
Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
parent/child anatomy links bidirectional. Code is the structural source of
truth: update this anatomy in the same change that moves files, symbols,
connections, composition, or state. Verify every changed citation and run the
architecture-document validation before merge.
"""

CHILD_CONTRACT_MAINTENANCE = """This component contract is governed by the root CONTRACT.md. Keep
related_files complete and repo-relative: the paired ANATOMY.md, Port, every
production Adapter, contract tests, and directly relevant component contracts
belong here. Re-read this contract whenever a linked boundary changes. Update
the Port, affected Adapters, contract tests, and this contract in the same
change; update the paired Anatomy when structure or composition also changes;
bump contract_version for a breaking Port-contract change. If code and contract
disagree, treat the disagreement as a defect—do not silently rewrite the
normative contract to match the implementation.
"""


def _read_document(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    _, frontmatter, body = text.split("---\n", 2)
    meta = yaml.safe_load(frontmatter)
    assert isinstance(meta, dict)
    return meta, body


def _heading_order(body: str) -> list[str]:
    visible_parts = []
    in_fence = False
    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible_parts.append(line)
    return re.findall(r"^## .+$", "\n".join(visible_parts), flags=re.MULTILINE)


def _assert_repo_file(path: str) -> None:
    assert path == path.strip(), path
    assert "\\" not in path, path
    raw_parts = path.split("/")
    assert raw_parts and all(part not in {"", ".", ".."} for part in raw_parts), path
    rel = Path(*raw_parts)
    assert not rel.is_absolute(), path
    target = (ROOT / rel).resolve(strict=True)
    assert target.is_relative_to(ROOT.resolve()), path
    assert target.is_file(), path


def _assert_related_files(meta: dict) -> None:
    related = meta["related_files"]
    assert isinstance(related, list)
    assert related
    assert len(related) == len(set(related))
    for path in related:
        _assert_repo_file(path)


def test_root_architecture_documents_are_reciprocal_and_well_formed() -> None:
    anatomy_meta, _ = _read_document(ROOT_ANATOMY)
    contract_meta, _ = _read_document(ROOT_CONTRACT)

    assert list(anatomy_meta) == ["related_files", "maintenance"]
    assert list(contract_meta) == [
        "name",
        "contract_version",
        "related_files",
        "maintenance",
    ]
    assert contract_meta["name"] == "component-contract-convention"
    assert contract_meta["contract_version"] == 1

    _assert_related_files(anatomy_meta)
    _assert_related_files(contract_meta)

    assert "CONTRACT.md" in anatomy_meta["related_files"]
    assert "ANATOMY.md" in contract_meta["related_files"]
    for required in [
        "CONTRIBUTING.md",
        "README.md",
        "dev-guide-skill/SKILL.md",
        "src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md",
        "tests/test_architecture_documents.py",
    ]:
        assert required in anatomy_meta["related_files"]
    for required in [
        "README.md",
        "dev-guide-skill/SKILL.md",
        "tests/test_architecture_documents.py",
    ]:
        assert required in contract_meta["related_files"]


def test_root_anatomy_defines_the_distributed_navigation_system() -> None:
    _, body = _read_document(ROOT_ANATOMY)

    assert _heading_order(body) == [
        "## Purpose",
        "## Navigation model",
        "## Frontmatter convention",
        "## Body convention",
        "## Link and pairing semantics",
        "## Components",
        "## Root files",
        "## Composition",
        "## State",
        "## Maintenance",
        "## Template",
    ]
    for anchor in [
        "ANATOMY is the distributed code navigation system",
        "anatomy of anatomy",
        "distributed code interface definition system",
        "each other exactly once",
        "structural source of truth",
        "enters the paired governed system",
        "Orphans, missing targets, duplicate links",
    ]:
        assert anchor in body


def test_root_contract_defines_the_distributed_interface_system() -> None:
    _, body = _read_document(ROOT_CONTRACT)

    assert _heading_order(body) == [
        "## Purpose",
        "## Architecture foundation",
        "## Behavior",
        "## Frontmatter contract",
        "## Body contract",
        "## Link semantics",
        "## Maintenance contract",
        "## Validation",
        "## Template",
    ]
    for anchor in [
        "CONTRACT is the distributed code interface definition system",
        "contract of contract",
        "Core / Use Cases",
        "Ports / Contracts",
        "Adapters",
        "Adapter -> Port <- Core",
        "inbound port",
        "outbound port",
        "Composition Root",
        "Core owns Ports",
        "Components MAY be nested",
        "Concrete technology belongs only in the Adapter",
        "A component migration is complete only when",
        "expected-agent-behavior agreement",
        "Behavior defines what agents",
        "jointly maintained",
        "Ports are earned by architectural boundaries",
        "one real boundary/vertical slice",
        "Non-normative wall-socket analogy",
    ]:
        assert anchor in body


def test_governed_child_contracts_have_reciprocal_anatomy_pairs() -> None:
    root_meta, _ = _read_document(ROOT_CONTRACT)
    child_contracts = [
        path
        for path in root_meta["related_files"]
        if path.endswith("/CONTRACT.md") and path != "CONTRACT.md"
    ]

    names = set()
    for contract_path in child_contracts:
        contract_meta, contract_body = _read_document(ROOT / contract_path)
        assert list(contract_meta) == [
            "name",
            "contract_version",
            "root_contract",
            "related_files",
            "maintenance",
        ]
        assert re.fullmatch(
            r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", contract_meta["name"]
        )
        assert contract_meta["name"] not in names
        names.add(contract_meta["name"])
        assert isinstance(contract_meta["contract_version"], int)
        assert contract_meta["contract_version"] > 0
        assert contract_meta["root_contract"] == "CONTRACT.md"
        _assert_repo_file(contract_meta["root_contract"])
        assert contract_meta["maintenance"] == CHILD_CONTRACT_MAINTENANCE
        _assert_related_files(contract_meta)
        assert _heading_order(contract_body) == [
            "## Purpose",
            "## Behavior",
            "## Port",
            "## Adapters",
            "## Contract rules",
            "## Contract tests",
            "## Maintenance",
        ]

        anatomy_path = str(Path(contract_path).with_name("ANATOMY.md"))
        assert anatomy_path in contract_meta["related_files"]
        anatomy_meta, anatomy_body = _read_document(ROOT / anatomy_path)
        assert list(anatomy_meta) == ["related_files", "maintenance"]
        assert anatomy_meta["maintenance"] == CHILD_ANATOMY_MAINTENANCE
        _assert_related_files(anatomy_meta)
        assert contract_path in anatomy_meta["related_files"]
        assert _heading_order(anatomy_body) == [
            "## Components",
            "## Connections",
            "## Composition",
            "## State",
            "## Notes",
        ]


def test_governed_cross_document_links_are_reciprocal() -> None:
    root_contract_meta, _ = _read_document(ROOT_CONTRACT)
    child_contracts = {
        path
        for path in root_contract_meta["related_files"]
        if path.endswith("/CONTRACT.md") and path != "CONTRACT.md"
    }

    governed_anatomies = {"ANATOMY.md"}
    for contract_path in child_contracts:
        contract_meta, _ = _read_document(ROOT / contract_path)
        anatomy_path = str(Path(contract_path).with_name("ANATOMY.md"))
        governed_anatomies.add(anatomy_path)

        linked_contracts = {
            path for path in contract_meta["related_files"] if path in child_contracts
        }
        for linked in linked_contracts:
            linked_meta, _ = _read_document(ROOT / linked)
            assert contract_path in linked_meta["related_files"]

    for anatomy_path in governed_anatomies:
        anatomy_meta, _ = _read_document(ROOT / anatomy_path)
        linked_anatomies = {
            path for path in anatomy_meta["related_files"] if path in governed_anatomies
        }
        for linked in linked_anatomies:
            linked_meta, _ = _read_document(ROOT / linked)
            assert anatomy_path in linked_meta["related_files"]


def test_public_and_agent_entry_points_route_to_the_local_network() -> None:
    readme = ROOT_README.read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    claude_entry = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    dev_meta, dev_skill = _read_document(ROOT_DEV_SKILL)
    anatomy_skill = ANATOMY_SKILL.read_text(encoding="utf-8")

    assert list(dev_meta) == ["name", "description"]
    assert dev_meta["name"] == "lingtai-kernel-dev"
    assert "Use this before changing code" in dev_meta["description"]
    assert "repository’s local dev guide skill" in readme
    for path in ["ANATOMY.md", "CONTRACT.md", "CONTRIBUTING.md"]:
        assert f"]({path})" in readme
    for anchor in [
        "Read this skill before every development task",
        "ANATOMY.md",
        "CONTRACT.md",
        "tests/test_architecture_documents.py",
        "canonical Anatomy drift checker in --check mode",
        "repository agent dev kit",
        "scripts/",
        "references/",
        "assets/",
        "never push directly to `main`",
    ]:
        assert anchor in dev_skill
    assert "repository’s dev guide skill" in contributing
    assert "repository-local dev guide" in claude_entry
    assert "before every development task" in claude_entry
    assert "ANATOMY.md" in contributing
    assert "CONTRACT.md" in contributing
    assert "distributed code navigation system" in anatomy_skill
    assert "distributed code interface definition system" in anatomy_skill
    assert "anatomy of anatomy" in anatomy_skill
    assert "src/lingtai/kernel/ANATOMY.md" in anatomy_skill
