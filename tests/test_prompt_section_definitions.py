"""Contract tests for prompt-section-definition YAML under prompts/<section>/.

Each system-prompt section has a `<section>/<section>.yaml` semantic definition
(``kind: prompt-section-definition``) that fixes the meaning of the section
*name*: what it means, why it exists, what scope it owns, and how its content is
injected. These are for coding agents editing the kernel repo — never rendered
into the LLM prompt.

``related_files`` in these YAMLs is a progressive-disclosure crawl graph: peer
section YAMLs for boundary/overlap risk, plus the canonical implementation and
navigation files (owning prompt ANATOMY, section builder/registry modules) that
own a section's concrete construction rules when those rules live in code. It is
NOT a peer-only graph — a legitimate ``.py`` builder link is expected for a
generated/injected section, and a canonical manual/reference ``.md`` link (e.g. a
``system-manual`` reference such as ``substrate-manual/SKILL.md``) is expected
when that manual owns the section's expanded rules. The one restriction these
tests enforce is that a related path may not be a concrete prompt *body* ``.md``
under ``src/lingtai/prompts/`` (the body relation lives in
``injection_contract.content_source``); the owning ``ANATOMY.md`` and reference
manuals outside the prompt-source tree are allowed. This is distinct from the
``.md`` frontmatter ``related_files`` body graph validated in
``test_prompt_catalog.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Each section is a first-class directory under prompts/ holding its own
# <section>.yaml definition (and, for body-backed sections, <section>.md).
_PROMPTS_DIR = _REPO_ROOT / "src" / "lingtai" / "prompts"
_PROMPT_ANATOMY = "src/lingtai/prompts/ANATOMY.md"


def _yaml_path(section: str) -> Path:
    return _PROMPTS_DIR / section / f"{section}.yaml"


def _md_path(section: str) -> Path:
    return _PROMPTS_DIR / section / f"{section}.md"

# The first implementation set (plan + Jason's addendum): body-backed kernel
# sections plus YAML-only generated/injected sections.
_REQUIRED_SECTIONS = [
    "principle",
    "covenant",
    "substrate",
    "procedures",
    "meta_guidance",
    "comment",
    "tools",
    "rules",
    "brief",
    "mcp",
    "skills",
    "knowledge",
    "identity",
    "character",
    "pad",
]

# Sections that ship a packaged `<section>.md` body companion.
_BODY_BACKED_SECTIONS = {"principle", "substrate", "procedures"}

# The core five have a fully reciprocal peer-link subgraph; peripheral sections
# link to hub sections (procedures/substrate) one-directionally by design.
_CORE_FIVE = {"principle", "covenant", "substrate", "procedures", "meta_guidance"}

_REQUIRED_KEYS = {
    "kind",
    "section",
    "name_definition",
    "purpose",
    "scope",
    "injection_contract",
    "related_files",
    "maintenance",
}


def _load(section: str) -> dict:
    return yaml.safe_load(_yaml_path(section).read_text(encoding="utf-8"))


def _peer_yaml_names(defn: dict) -> list[str]:
    return [
        Path(rel["path"]).name.removesuffix(".yaml")
        for rel in defn["related_files"]
        if rel["path"].endswith(".yaml")
    ]


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_section_definition_yaml_exists_and_parses(section):
    path = _yaml_path(section)
    assert path.is_file(), f"missing section definition {path}"
    defn = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(defn, dict), f"{section}.yaml must parse to a mapping"


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_section_definition_has_required_shape(section):
    defn = _load(section)
    assert defn["kind"] == "prompt-section-definition"
    assert defn["section"] == section
    assert _REQUIRED_KEYS <= set(defn), (
        f"{section}.yaml missing keys: {_REQUIRED_KEYS - set(defn)}"
    )
    assert isinstance(defn["name_definition"], str) and defn["name_definition"].strip()
    assert isinstance(defn["purpose"], str) and defn["purpose"].strip()
    scope = defn["scope"]
    assert isinstance(scope, dict) and scope.get("owns") and scope.get("does_not_own")
    ic = defn["injection_contract"]
    assert isinstance(ic, dict)
    for key in ("defined_by", "injected_by", "content_source", "override_policy"):
        assert ic.get(key), f"{section}.yaml injection_contract missing {key}"
    assert isinstance(defn["maintenance"], list) and defn["maintenance"]


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_related_files_are_structured_objects_with_path_and_why(section):
    defn = _load(section)
    related = defn["related_files"]
    assert isinstance(related, list) and related
    for rel in related:
        assert isinstance(rel, dict), f"{section}: related_files entry must be an object"
        assert set(rel) >= {"path", "why"}, f"{section}: entry needs path and why"
        assert isinstance(rel["path"], str) and rel["path"].strip()
        assert isinstance(rel["why"], str) and rel["why"].strip()


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_related_paths_resolve_from_repo_root(section):
    defn = _load(section)
    for rel in defn["related_files"]:
        target = _REPO_ROOT / rel["path"]
        assert target.exists(), f"{section}: related path does not resolve: {rel['path']}"


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_related_files_link_owning_prompt_anatomy(section):
    defn = _load(section)
    paths = {rel["path"] for rel in defn["related_files"]}
    assert _PROMPT_ANATOMY in paths, f"{section}.yaml must link {_PROMPT_ANATOMY}"


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_related_files_link_at_least_one_peer_yaml(section):
    peers = _peer_yaml_names(_load(section))
    assert peers, f"{section}.yaml must link at least one peer section YAML"
    assert section not in peers, f"{section}.yaml must not list itself as a peer"


@pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
def test_related_files_forbid_prompt_body_md(section):
    """A related `.md` may be ANATOMY or a canonical manual/reference, but never a
    concrete prompt *body* under prompts/ (that relation is content_source)."""
    prompts_prefix = "src/lingtai/prompts/"
    for rel in _load(section)["related_files"]:
        path = rel["path"]
        if not path.endswith(".md"):
            continue
        if path.endswith("ANATOMY.md"):
            continue
        # Manuals/reference docs (outside the prompt-source tree) are allowed;
        # a `.md` living under prompts/ would be a concrete body and is not.
        assert not path.startswith(prompts_prefix), (
            f"{section}.yaml related path may not be a concrete prompt body .md: {path}"
        )


def test_core_five_peer_links_are_reciprocal():
    graph = {s: set(_peer_yaml_names(_load(s))) for s in _CORE_FIVE}
    for source, peers in graph.items():
        for peer in peers:
            if peer in _CORE_FIVE:
                assert source in graph[peer], (
                    f"{source} links {peer} within the core five, but not vice versa"
                )


def test_body_backed_sections_have_md_companion_via_content_source():
    for section in _BODY_BACKED_SECTIONS:
        ic = _load(section)["injection_contract"]
        expected = f"src/lingtai/prompts/{section}/{section}.md"
        assert ic["content_source"] == expected, (
            f"{section}.yaml content_source should point at its packaged body {expected}"
        )
        assert _md_path(section).is_file()


def test_yaml_only_sections_have_no_packaged_md_body():
    for section in set(_REQUIRED_SECTIONS) - _BODY_BACKED_SECTIONS:
        assert not _md_path(section).is_file(), (
            f"{section} is a YAML-only section and must not ship a body .md"
        )


def test_brief_section_is_marked_deprecated():
    defn = _load("brief")
    assert defn.get("deprecated") is True
    note = defn.get("deprecation_note", "")
    assert "Deprecated compatibility slot" in note
    assert "system/brief.md" in note


def test_covenant_body_source_is_external_not_packaged():
    ic = _load("covenant")["injection_contract"]
    assert ic["content_source"] == "operator_recipe_or_init_covenant_mirror"
    assert ic["injected_by"] == "init_recipe_or_operator_surface"
    assert ic["override_policy"] == "operator_recipe_may_supply_content"
    # It must not claim a packaged covenant/covenant.md body anywhere.
    blob = _yaml_path("covenant").read_text(encoding="utf-8")
    assert "covenant/covenant.md" not in blob or "no such body" in blob.lower() \
        or "no covenant/covenant.md" in blob


def test_meta_guidance_body_is_generated_from_guidance_catalog():
    ic = _load("meta_guidance")["injection_contract"]
    assert ic["content_source"] == "src/lingtai/prompts/meta_guidance/catalog/"
    assert ic["injected_by"] == "meta_guidance_builder"
    assert ic.get("derived_mirror") == "system/guidance.json"
    assert not _md_path("meta_guidance").is_file()


def test_packaged_section_definitions_are_readable_resources():
    """Section YAMLs are reachable as importlib resources (catches glob regressions)."""
    from importlib.resources import files

    root = files("lingtai.prompts")
    for section in _REQUIRED_SECTIONS:
        text = root.joinpath(f"{section}/{section}.yaml").read_text(encoding="utf-8")
        defn = yaml.safe_load(text)
        assert defn["section"] == section
