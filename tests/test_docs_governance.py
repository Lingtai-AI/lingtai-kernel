"""Docs YAML governance validator tests (V6).

related_files.target_policy is owner_validated_relative_link: generic
validation is syntax-only, never existence. Tests here prove both that
syntax violations are still caught AND that an owner-relative logical
link (e.g. a prompt-catalog crawl-graph entry style path) passes generic
validation without requiring it to resolve to a real file.

Every test passes an explicit repo root or none at all where none is
needed (syntax-only checks need no filesystem). Imports
scripts/check_docs_governance.py so pytest exercises the exact same logic
as `python scripts/check_docs_governance.py --check`. The canonical rationale
and owner-specific resolution boundary live in docs.yaml.
"""

from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_docs_governance as checker  # noqa: E402


def test_docs_yaml_validates_against_its_own_contract():
    contract = checker.load_docs_contract()
    failures = checker.validate_metadata_mapping("docs.yaml", contract, contract)
    assert not failures, failures


def test_contract_has_no_path_or_content_exemptions():
    contract = checker.load_docs_contract()
    assert "exempt" not in contract
    assert "exempt_paths" not in contract
    assert "placeholder_exempt_fields" not in contract
    assert contract["field_constraints"]["related_files"]["target_policy"] == "owner_validated_relative_link"
    assert "must_resolve_to_repo_file" not in contract["field_constraints"]["related_files"]


def test_discovery_unions_tracked_and_untracked_via_git_files(monkeypatch):
    contract = checker.load_docs_contract()
    calls = []

    def fake_git_files(*args):
        calls.append(args)
        return ["a.md", "shared.md"] if args == () else ["b.md", "shared.md"]

    monkeypatch.setattr(checker, "_git_files", fake_git_files)
    result = checker.discover_doc_paths(contract)
    rels = sorted(str(p.relative_to(ROOT)) for p in result)
    assert rels == ["a.md", "b.md", "shared.md"]
    assert () in calls
    assert ("--others", "--exclude-standard") in calls


def test_every_in_scope_doc_has_required_fields():
    contract = checker.load_docs_contract()
    failures: list[str] = []
    for path in checker.discover_doc_paths(contract):
        failures.extend(checker.check_one_document_path(path, contract, repo_root=ROOT))
    assert not failures, "\n".join(failures)


def test_root_anatomy_and_contract_pass_generic_check_without_weakening_architecture_tests():
    contract = checker.load_docs_contract()
    for name in ("ANATOMY.md", "CONTRACT.md"):
        failures = checker.check_one_document_path(ROOT / name, contract, repo_root=ROOT)
        assert not failures, (name, failures)
    sys.path.insert(0, str(ROOT / "tests"))
    import test_architecture_documents as arch_tests  # noqa: E402
    arch_tests.test_root_architecture_documents_are_reciprocal_and_well_formed()


def test_owner_relative_logical_link_passes_generic_syntax_validation():
    """POSITIVE test (V5): an owner-specific logical/crawl-graph-style
    relative link — modeled on the real
    reference/substrate-manual/SKILL.md-style entries already present in
    src/lingtai/prompts/*.yaml related_files — must pass the generic
    syntax-only check even though it does not resolve as a repo-root
    physical path. This directly proves target_policy:
    owner_validated_relative_link does not re-impose the old universal
    existence rule."""
    contract = checker.load_docs_contract()
    meta = {
        "related_files": ["reference/substrate-manual/SKILL.md"],
        "maintenance": "Owner-specific crawl-graph link; resolved by the prompt catalog loader, not this generic contract.",
    }
    failures = checker.validate_metadata_mapping("some/owner/doc.md", meta, contract)
    assert not failures, failures


def test_preexisting_dangling_i18n_reference_does_not_fail_generic_check():
    """Models the real, pre-existing src/lingtai/kernel/i18n/ANATOMY.md
    related_files entries (src/lingtai/i18n/ANATOMY.md,
    src/lingtai/i18n/__init__.py) which do not resolve to real files at
    the current repository layout. The generic contract must not fail
    this — it is a pre-existing drift issue for Anatomy-specific tooling
    to report, not something docs.yaml's generic syntax layer re-flags."""
    contract = checker.load_docs_contract()
    meta = {
        "related_files": ["src/lingtai/i18n/ANATOMY.md", "src/lingtai/i18n/__init__.py"],
        "maintenance": "Pre-existing owner-specific anatomy links; syntax-valid, generically unresolved by design.",
    }
    failures = checker.validate_metadata_mapping("src/lingtai/kernel/i18n/ANATOMY.md", meta, contract)
    assert not failures, failures


def test_missing_required_field_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text("---\nmaintenance: Update when this test fixture changes.\n---\nbody\n", encoding="utf-8")
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("related_files" in f for f in failures)


def test_duplicate_yaml_key_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text(
        "---\nmaintenance: a\nmaintenance: b\nrelated_files:\n  - x.md\n---\nbody\n",
        encoding="utf-8",
    )
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("duplicate" in f.lower() or "parse error" in f.lower() for f in failures)


def test_empty_maintenance_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text('---\nmaintenance: ""\nrelated_files:\n  - y.md\n---\n', encoding="utf-8")
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("at least 20" in f for f in failures)


@pytest.mark.parametrize("value", ["x", "TODO", "tbd", "FIXME", "xxx", "N/A", "none"])
def test_exact_placeholder_maintenance_rejected(value):
    contract = checker.load_docs_contract()
    failures = checker.validate_metadata_mapping(
        "x.md",
        {"maintenance": value, "related_files": ["y.md"]},
        contract,
    )
    assert any("forbidden exact placeholder" in f for f in failures)


def test_maintenance_minimum_boundary_is_exact():
    contract = checker.load_docs_contract()
    minimum = contract["field_constraints"]["maintenance"]["min_length"]
    assert minimum == 20

    too_short = checker.validate_metadata_mapping(
        "x.md",
        {"maintenance": "a" * (minimum - 1), "related_files": ["y.md"]},
        contract,
    )
    assert any(f"at least {minimum}" in f for f in too_short)

    exact = checker.validate_metadata_mapping(
        "x.md",
        {"maintenance": "a" * minimum, "related_files": ["y.md"]},
        contract,
    )
    assert not exact, exact


def test_concise_real_maintenance_rule_is_valid():
    contract = checker.load_docs_contract()
    failures = checker.validate_metadata_mapping(
        "x.md",
        {"maintenance": "Update when the API changes.", "related_files": ["y.md"]},
        contract,
    )
    assert not failures, failures


def test_duplicate_related_path_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text(
        "---\nmaintenance: Update when this test fixture changes.\nrelated_files:\n  - y.md\n  - y.md\n---\n",
        encoding="utf-8",
    )
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("duplicate" in f for f in failures)


def test_self_reference_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text("---\nmaintenance: Update when this test fixture changes.\nrelated_files:\n  - x.md\n---\n", encoding="utf-8")
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("self-reference" in f for f in failures)


def test_absolute_related_path_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text(
        "---\nmaintenance: Update when this test fixture changes.\nrelated_files:\n  - /etc/passwd\n---\n", encoding="utf-8"
    )
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("absolute" in f or "invalid entry" in f for f in failures)


def test_dot_dot_segment_rejected(tmp_path):
    """V5 no longer has a generic 'outside repo' EXISTENCE check (that
    would require resolving the path); '..' segments are still rejected
    on SYNTAX grounds alone, via _is_clean_repo_relative_posix_path."""
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text(
        "---\nmaintenance: Update when this test fixture changes.\nrelated_files:\n  - ../outside.md\n---\n", encoding="utf-8"
    )
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("invalid entry" in f for f in failures)


@pytest.mark.parametrize("bad_path", ["./child.md", "dir/./child.md", "dir//child.md"])
def test_dot_or_empty_path_segment_rejected(tmp_path, bad_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text(
        f"---\nmaintenance: Update when this test fixture changes.\nrelated_files:\n  - {bad_path}\n---\n",
        encoding="utf-8",
    )
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("invalid entry" in f for f in failures)


@pytest.mark.parametrize("bad_path", ["C:/outside.md", "C:relative.md", r"C:\outside.md"])
def test_windows_drive_designator_path_rejected(bad_path):
    error = checker._is_clean_repo_relative_posix_path(bad_path)
    assert error is not None
    assert "drive designator" in error


def test_non_string_unhashable_related_files_item_does_not_crash(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text(
        "---\nmaintenance: Update when this test fixture changes.\nrelated_files:\n  - [nested, list]\n---\n",
        encoding="utf-8",
    )
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("not a string" in f or "invalid entry" in f for f in failures)


def test_placeholder_in_maintenance_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text(
        "---\nmaintenance: Update this document when [FILL_IN] changes.\nrelated_files:\n  - y.md\n---\n", encoding="utf-8"
    )
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("placeholder" in f for f in failures)


def test_malformed_frontmatter_rejected(tmp_path):
    contract = checker.load_docs_contract()
    doc = tmp_path / "x.md"
    doc.write_text("---\nmaintenance: a\n", encoding="utf-8")
    failures = checker.check_one_document_path(doc, contract, repo_root=tmp_path)
    assert any("closing fence" in f for f in failures)


def test_malformed_html_comment_rejected():
    contract = dict(checker.load_docs_contract())
    contract["metadata_mode_overrides"] = {"x.md": "html_comment"}
    text = "<!--\nmaintenance: a\n"  # no closing -->
    meta, error = checker.parse_doc_metadata_text("x.md", text, contract)
    assert meta is None
    assert "closing -->" in error


def test_unsupported_mode_override_rejected():
    contract = dict(checker.load_docs_contract())
    contract["metadata_mode_overrides"] = {"x.md": "not_a_real_mode"}
    meta, error = checker.parse_doc_metadata_text("x.md", "anything", contract)
    assert meta is None
    assert "no supported metadata mode" in error


def test_wrong_required_fields_order_rejected():
    bad = dict(checker.load_docs_contract())
    bad["required_fields"] = ["maintenance", "related_files"]
    with pytest.raises(checker.ContractError, match="required_fields"):
        checker.validate_docs_contract_shape(bad)


def test_missing_field_constraint_key_rejected():
    bad = dict(checker.load_docs_contract())
    bad["field_constraints"] = {"related_files": bad["field_constraints"]["related_files"]}
    with pytest.raises(checker.ContractError, match="field_constraints"):
        checker.validate_docs_contract_shape(bad)


def test_noncanonical_maintenance_forbidden_values_rejected():
    bad = dict(checker.load_docs_contract())
    bad["field_constraints"] = dict(bad["field_constraints"])
    bad["field_constraints"]["maintenance"] = dict(
        bad["field_constraints"]["maintenance"]
    )
    bad["field_constraints"]["maintenance"]["forbidden_exact_values"] = ["TODO"]
    with pytest.raises(checker.ContractError, match="forbidden_exact_values"):
        checker.validate_docs_contract_shape(bad)


def test_wrong_target_policy_rejected():
    bad = dict(checker.load_docs_contract())
    bad["field_constraints"] = dict(bad["field_constraints"])
    bad["field_constraints"]["related_files"] = dict(bad["field_constraints"]["related_files"])
    bad["field_constraints"]["related_files"]["target_policy"] = "must_resolve_to_repo_file"
    with pytest.raises(checker.ContractError, match="target_policy"):
        checker.validate_docs_contract_shape(bad)


def test_wrong_discovery_type_rejected():
    bad = dict(checker.load_docs_contract())
    bad["discovery"] = {"tracked": "yes", "untracked_not_ignored": True}
    with pytest.raises(checker.ContractError, match="discovery"):
        checker.validate_docs_contract_shape(bad)


def test_unknown_mode_override_target_rejected():
    bad = dict(checker.load_docs_contract())
    bad["metadata_mode_overrides"] = {"some/other.md": "html_comment"}
    with pytest.raises(checker.ContractError, match="metadata_mode_overrides"):
        checker.validate_docs_contract_shape(bad)


def test_invalid_placeholder_pattern_rejected():
    bad = dict(checker.load_docs_contract())
    bad["placeholder_patterns"] = ["[unclosed"]
    with pytest.raises(checker.ContractError, match="placeholder pattern"):
        checker.validate_docs_contract_shape(bad)


def test_unsupported_version_rejected():
    bad = dict(checker.load_docs_contract())
    bad["version"] = 999
    with pytest.raises(checker.ContractError, match="version"):
        checker.validate_docs_contract_shape(bad)


def test_unrecognized_top_level_key_rejected():
    bad = dict(checker.load_docs_contract())
    bad["bogus_extra_key"] = 1
    with pytest.raises(checker.ContractError, match="unrecognized"):
        checker.validate_docs_contract_shape(bad)


def test_pr_template_visible_body_byte_identical_and_metadata_hidden():
    contract = checker.load_docs_contract()
    path = ROOT / ".github/PULL_REQUEST_TEMPLATE.md"
    text = path.read_text(encoding="utf-8")
    visible = text.split("-->\n", 1)[1]
    expected_visible = (
        "## Summary\n"
        "\n"
        "- TODO\n"
        "\n"
        "## Validation\n"
        "\n"
        "- [ ] `git diff --check`\n"
        "- [ ] Relevant tests or documentation checks:\n"
        "\n"
        "## Notes\n"
        "\n"
        "- Link related issues or context here.\n"
        "- Confirm that logs, screenshots, and examples do not contain secrets.\n"
    )
    assert visible == expected_visible
    failures = checker.check_one_document_path(path, contract, repo_root=ROOT)
    assert not failures, failures


def test_all_four_notification_managers_preserve_exact_runtime_body():
    sys.path.insert(0, str(ROOT / "src"))
    from lingtai.mcp_servers.feishu import manager as m1
    from lingtai.mcp_servers.telegram import manager as m2
    from lingtai.mcp_servers.wechat import manager as m3
    from lingtai.mcp_servers.whatsapp import manager as m4

    expected = {
        m1.__name__: (
            987,
            "8065f55c16561adedf8b71d788efa29d80ff1b9a1196ffab38f239bf06302364",
        ),
        m2.__name__: (
            2154,
            "992e88cb55d8c54598883a47c895a278b1a1797f1725720a583923f6a921199c",
        ),
        m3.__name__: (
            987,
            "8065f55c16561adedf8b71d788efa29d80ff1b9a1196ffab38f239bf06302364",
        ),
        m4.__name__: (
            1245,
            "e671f269c783a6a68b9d2294f0de1eb8e397ce22e13cd73b6cd9426453b8cb9e",
        ),
    }
    for mod in (m1, m2, m3, m4):
        template = mod._NOTIFICATION_HEADER_TEMPLATE
        expected_length, expected_sha256 = expected[mod.__name__]
        assert len(template) == expected_length
        assert hashlib.sha256(template.encode("utf-8")).hexdigest() == expected_sha256
        assert template.startswith("**How to read this {channel} conversation preview")
        assert not template.startswith("\n")
        assert template.endswith("\n")
        assert not template.endswith("\n\n")
        assert template.count("{channel}") == 1
        assert not template.startswith("---")
        assert "related_files:" not in template


def test_glossary_owner_preserves_rendered_body_without_metadata():
    sys.path.insert(0, str(ROOT / "src"))
    from lingtai.kernel import tool_glossary

    before = tool_glossary.load_tool_glossary("lingtai.tools.read", "zh")
    assert before.strip()
    assert "kind:" not in before
    assert "related_files:" not in before


def test_dev_guide_skill_exact_schema():
    contract_text = (ROOT / "dev-guide-skill/SKILL.md").read_text(encoding="utf-8")
    assert contract_text.startswith("---\n")
    end = contract_text.index("\n---\n", 4)
    fm = yaml.safe_load(contract_text[4:end])
    assert list(fm) == ["name", "description", "related_files", "maintenance"]
