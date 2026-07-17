"""Mechanical completeness and reciprocal-link contract for the env catalogue."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


_REPO = Path(__file__).resolve().parents[1]
_CATALOGUE = Path(
    "src/lingtai/intrinsic_skills/system-manual/reference/"
    "environment-variables/SKILL.md"
)
_ENV_NAME = re.compile(r"\bLINGTAI_[A-Z0-9_]+\b")
_CODE_SUFFIXES = {".go", ".py", ".rs", ".sh"}


def _related_files(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"missing frontmatter in {path}"
    frontmatter = text.split("---\n", 2)[1]
    meta = yaml.safe_load(frontmatter)
    return set(meta.get("related_files", []))


def _code_sources() -> list[Path]:
    sources = [
        path
        for base in (_REPO / "src" / "lingtai", _REPO / "tests")
        for path in base.rglob("*")
        if path.is_file() and path.suffix in _CODE_SUFFIXES
    ]
    setup = _REPO / "setup.py"
    if setup.exists():
        sources.append(setup)
    return sources


def _nearest_anatomy(source: Path) -> Path:
    for parent in (source.parent, *source.parents):
        candidate = parent / "ANATOMY.md"
        if candidate.exists():
            return candidate
        if parent == _REPO:
            break
    raise AssertionError(f"no owning ANATOMY.md for {source.relative_to(_REPO)}")


def test_environment_catalogue_covers_code_and_links_every_owning_anatomy():
    catalogue_path = _REPO / _CATALOGUE
    catalogue_text = catalogue_path.read_text(encoding="utf-8")
    catalogue_names = set(_ENV_NAME.findall(catalogue_text))
    source_names: set[str] = set()
    owning_anatomies: set[str] = set()

    for source in _code_sources():
        names = set(_ENV_NAME.findall(source.read_text(encoding="utf-8")))
        if not names:
            continue
        source_names.update(names)
        owning_anatomies.add(str(_nearest_anatomy(source).relative_to(_REPO)))

    assert source_names <= catalogue_names, (
        "environment catalogue is missing code-visible names: "
        f"{sorted(source_names - catalogue_names)}"
    )

    catalogue_related = _related_files(catalogue_path)
    assert owning_anatomies <= catalogue_related, (
        "environment catalogue is missing owning Anatomy links: "
        f"{sorted(owning_anatomies - catalogue_related)}"
    )

    anatomy_links = {
        related for related in catalogue_related if related.endswith("ANATOMY.md")
    }
    for anatomy in anatomy_links:
        assert str(_CATALOGUE) in _related_files(_REPO / anatomy), (
            f"{anatomy} must link back to {_CATALOGUE}"
        )
