from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROOT_ANATOMY = ROOT / "ANATOMY.md"
ROOT_CONTRACT = ROOT / "CONTRACT.md"
ROOT_GLOSSARY = ROOT / "GLOSSARY.md"


def _read_document(path: Path) -> tuple[dict, str]:
    """Parse a Markdown document's YAML frontmatter and return its body."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    _, frontmatter, body = text.split("---\n", 2)
    meta = yaml.safe_load(frontmatter)
    assert isinstance(meta, dict), path
    return meta, body


def _assert_repo_file(path: str) -> None:
    """Require a normalized, repository-relative path to a regular file."""
    assert path == path.strip(), path
    assert "\\" not in path, path
    parts = path.split("/")
    assert parts and all(part not in {"", ".", ".."} for part in parts), path
    relative = Path(*parts)
    assert not relative.is_absolute(), path
    target = (ROOT / relative).resolve(strict=True)
    assert target.is_relative_to(ROOT.resolve()), path
    assert target.is_file(), path


def _assert_related_files(meta: dict) -> None:
    related = meta.get("related_files")
    assert isinstance(related, list) and related
    assert len(related) == len(set(related))
    for path in related:
        assert isinstance(path, str)
        _assert_repo_file(path)


def _root_governed_contracts() -> set[str]:
    root_meta, _ = _read_document(ROOT_CONTRACT)
    _assert_related_files(root_meta)
    return {
        path
        for path in root_meta["related_files"]
        if path.endswith("/CONTRACT.md") and path != "CONTRACT.md"
    }


def test_root_architecture_documents_are_reciprocal_and_well_formed() -> None:
    anatomy_meta, _ = _read_document(ROOT_ANATOMY)
    contract_meta, _ = _read_document(ROOT_CONTRACT)
    glossary_meta, _ = _read_document(ROOT_GLOSSARY)
    _assert_related_files(anatomy_meta)
    _assert_related_files(contract_meta)
    _assert_related_files(glossary_meta)
    assert "CONTRACT.md" in anatomy_meta["related_files"]
    assert "GLOSSARY.md" in anatomy_meta["related_files"]
    assert "ANATOMY.md" in contract_meta["related_files"]
    assert "GLOSSARY.md" in contract_meta["related_files"]
    assert "ANATOMY.md" in glossary_meta["related_files"]
    assert "CONTRACT.md" in glossary_meta["related_files"]


def test_governed_child_contracts_have_reciprocal_anatomy_pairs() -> None:
    for contract_path in _root_governed_contracts():
        contract_meta, _ = _read_document(ROOT / contract_path)
        _assert_related_files(contract_meta)
        assert contract_meta.get("root_contract") == "CONTRACT.md"
        _assert_repo_file(contract_meta["root_contract"])

        anatomy_path = str(Path(contract_path).with_name("ANATOMY.md"))
        assert anatomy_path in contract_meta["related_files"], contract_path
        anatomy_meta, _ = _read_document(ROOT / anatomy_path)
        _assert_related_files(anatomy_meta)
        assert contract_path in anatomy_meta["related_files"], anatomy_path


def test_governed_cross_document_links_are_reciprocal() -> None:
    child_contracts = _root_governed_contracts()
    governed_anatomies = {"ANATOMY.md"}
    contract_metas: dict[str, dict] = {}

    for contract_path in child_contracts:
        meta, _ = _read_document(ROOT / contract_path)
        contract_metas[contract_path] = meta
        governed_anatomies.add(str(Path(contract_path).with_name("ANATOMY.md")))

    for contract_path, meta in contract_metas.items():
        for linked in meta["related_files"]:
            if linked in child_contracts:
                linked_meta, _ = _read_document(ROOT / linked)
                assert contract_path in linked_meta["related_files"], (
                    contract_path,
                    linked,
                )

    for anatomy_path in governed_anatomies:
        meta, _ = _read_document(ROOT / anatomy_path)
        for linked in meta["related_files"]:
            if linked in governed_anatomies:
                linked_meta, _ = _read_document(ROOT / linked)
                assert anatomy_path in linked_meta["related_files"], (
                    anatomy_path,
                    linked,
                )

    owner_links: dict[str, list[str]] = {}
    for contract_path, meta in contract_metas.items():
        for linked in meta["related_files"]:
            if linked.endswith("/ANATOMY.md") and linked not in governed_anatomies:
                owner_links.setdefault(linked, []).append(contract_path)

    for anatomy_path, owners in owner_links.items():
        anatomy_meta, _ = _read_document(ROOT / anatomy_path)
        local_contract = Path(anatomy_path).with_name("CONTRACT.md")
        assert len(owners) == 1, (anatomy_path, owners)
        assert not (ROOT / local_contract).is_file(), anatomy_path
        assert owners[0] in anatomy_meta["related_files"], (
            anatomy_path,
            owners[0],
        )
