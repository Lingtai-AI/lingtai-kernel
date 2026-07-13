from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROOT_ANATOMY = ROOT / "ANATOMY.md"
ROOT_CONTRACT = ROOT / "CONTRACT.md"
ROOT_README = ROOT / "README.md"
ROOT_DEV_SKILL = ROOT / "dev-guide-skill/SKILL.md"
ANATOMY_SKILL = ROOT / "src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md"

PAIRING_RULE_POINTER = (
    "Follow the root Anatomy/Contract pairing rule, report mismatches, and do not "
    "duplicate or auto-fix the rule here."
)

CHILD_ANATOMY_MAINTENANCE = """Keep related_files repo-relative, duplicate-free, and linked to real files.
Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
parent/child anatomy links bidirectional. Code is the structural source of
truth: update this anatomy in the same change that moves files, symbols,
connections, composition, or state. Verify every changed citation and run the
architecture-document validation before merge.
Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
"""

# The canonical child-contract Maintenance block is defined ONCE, inside the
# root CONTRACT.md `## Template` section, between explicit stable markers. It is
# NOT re-declared here as a divergent literal copy; the helpers below extract it
# from the root so the root remains the single source of truth. `_CANONICAL_*`
# regexes locate the block; `canonical_maintenance()` returns
# (version, text, sha256) derived from the extracted bytes.
_CANONICAL_BEGIN = re.compile(
    r"^(?P<indent>[ \t]*)<!-- CANONICAL-MAINTENANCE v(?P<version>\d+) BEGIN -->[ \t]*$",
    re.MULTILINE,
)
_CANONICAL_END = re.compile(
    r"^[ \t]*<!-- CANONICAL-MAINTENANCE END -->[ \t]*$",
    re.MULTILINE,
)


class CanonicalMaintenanceError(AssertionError):
    """Raised when the canonical Maintenance block cannot be extracted cleanly."""


def _extract_canonical_maintenance(source: str) -> tuple[int, str]:
    """Extract (version, block_text) between the canonical markers in `source`.

    The block text is dedented by the BEGIN marker's leading indentation and
    includes both marker lines plus a single trailing newline, matching how a
    YAML `maintenance: |` block scalar parses in a governed child contract.

    Hard-fails (never silently normalizes) on a missing, extra, or malformed
    marker so a mismatched or absent block can never pass as valid.
    """
    begins = list(_CANONICAL_BEGIN.finditer(source))
    ends = list(_CANONICAL_END.finditer(source))
    if len(begins) != 1 or len(ends) != 1:
        raise CanonicalMaintenanceError(
            "expected exactly one canonical-maintenance BEGIN and END marker, "
            f"found {len(begins)} BEGIN and {len(ends)} END"
        )
    begin, end = begins[0], ends[0]
    if end.start() <= begin.start():
        raise CanonicalMaintenanceError("END marker precedes BEGIN marker")
    version = int(begin.group("version"))
    indent = begin.group("indent")
    region = source[begin.start() : end.end()]
    dedented = []
    for line in region.split("\n"):
        if line.startswith(indent):
            dedented.append(line[len(indent) :])
        elif line.strip() == "":
            dedented.append("")
        else:
            raise CanonicalMaintenanceError(
                f"canonical-maintenance line not indented by template indent: {line!r}"
            )
    # Trailing newline mirrors a YAML `|` block scalar's parsed value.
    return version, "\n".join(dedented) + "\n"


def canonical_maintenance(source: str | None = None) -> tuple[int, str, str]:
    """Return (version, text, sha256_hex) for the canonical Maintenance block."""
    if source is None:
        source = ROOT_CONTRACT.read_text(encoding="utf-8")
    version, text = _extract_canonical_maintenance(source)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return version, text, digest


def _child_maintenance_version(value: str) -> int | None:
    """Return the version tag embedded in a child's `maintenance` value, or None."""
    match = _CANONICAL_BEGIN.search(value)
    return int(match.group("version")) if match else None


def _first_difference(expected: str, actual: str) -> int:
    """Return the first byte offset at which two strings differ (or len on a prefix)."""
    expected_bytes = expected.encode("utf-8")
    actual_bytes = actual.encode("utf-8")
    limit = min(len(expected_bytes), len(actual_bytes))
    for index in range(limit):
        if expected_bytes[index] != actual_bytes[index]:
            return index
    return limit


def check_canonical_maintenance(
    component: str,
    path: str,
    maintenance_value: str,
    *,
    source: str | None = None,
) -> dict | None:
    """Compare one child's `maintenance` value against the canonical block.

    Returns None on a byte-identical, same-version, same-hash match. On any
    mismatch returns a diagnostic report (never normalizes/auto-fixes) with at
    least: component, path, expected_version, actual_version, expected_hash,
    actual_hash, and first_difference (byte offset). The caller treats a
    non-None result as a hard failure that blocks the change.
    """
    expected_version, expected_text, expected_hash = canonical_maintenance(source)
    actual_version = _child_maintenance_version(maintenance_value)
    actual_hash = hashlib.sha256(maintenance_value.encode("utf-8")).hexdigest()
    if (
        maintenance_value == expected_text
        and actual_version == expected_version
        and actual_hash == expected_hash
    ):
        return None
    return {
        "component": component,
        "path": path,
        "expected_version": expected_version,
        "actual_version": actual_version,
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
        "first_difference": _first_difference(expected_text, maintenance_value),
    }


# Derived from the single source of truth so tests never carry a second, possibly
# divergent, copy of the canonical block.
CHILD_CONTRACT_MAINTENANCE = canonical_maintenance()[1]


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


def pairing_mismatch_report(
    *,
    component_or_directory: str,
    path: str,
    actual_pair_state: dict,
    violated_rule: str,
    expected_owner: str | None,
    link_issues: list[str],
) -> dict:
    """Return the required fail-loud diagnostic without mutating documents."""
    return {
        "component_or_directory": component_or_directory,
        "path": path,
        "actual_pair_state": actual_pair_state,
        "violated_rule": violated_rule,
        "expected_owner": expected_owner,
        "link_issues": link_issues,
        "suggested_action": (
            "Report the mismatch and ask the owner whether to restore the unique "
            "pair/owner links or stage a component migration; do not create, "
            "delete, move, normalize, or auto-fix files without authorization."
        ),
    }


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
    assert contract_meta["contract_version"] == 3

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
        "src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md",
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
        "single source for governed-component",
        "mutual progressive",
        "fail-loud mismatch reports",
        # Navigation counterpart of Design principle 4: a capability manual is a
        # graph target linked from BOTH owner twins, with the normative rule
        # routed back to root CONTRACT.md `## Design principles`.
        "navigation target linked from **both** owner twins",
        "both-edges requirement is owned by root",
    ]:
        assert anchor in body


def test_root_contract_defines_the_distributed_interface_system() -> None:
    _, body = _read_document(ROOT_CONTRACT)

    assert _heading_order(body) == [
        "## Design principles",
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
        "canonical Maintenance block",
        "CANONICAL-MAINTENANCE v<N> BEGIN",
        "Canonical Maintenance consistency check",
        "first differing byte position",
        "stays blocked until the child is corrected",
        "unit of pairing is a **governed architectural component**",
        "implementation, Adapter, or navigation-only Anatomy",
        "exactly one owning governed component Contract",
        "mutual progressive disclosure",
        "actual `ANATOMY.md` / `CONTRACT.md` pair",
        "suggested action",
    ]:
        assert anchor in body


def test_root_contract_states_the_design_principles_first() -> None:
    _, body = _read_document(ROOT_CONTRACT)

    # The design-principles section is the very first body section.
    assert _heading_order(body)[0] == "## Design principles"
    assert body.index("## Design principles") < body.index("## Purpose")

    # Each of the five normative principles is present and cannot silently
    # regress: i18n gate, progressive disclosure, every-capability-has-a-manual
    # (with the Contract-vs-manual distinction), the both-owner-twins manual
    # discoverability rule, and dev-guide enforcement.
    for anchor in [
        "User-facing-only i18n, gated by human confirmation",
        "MUST ask the human to confirm",
        "Progressive disclosure wherever possible",
        "agent-consumed",
        "Every capability is taught by a manual",
        "what to do, how it works, and why it is",
        "a Contract still defines the capability's obligations",
        "The dev guide enforces these principles",
    ]:
        assert anchor in body

    # Principle 4 requires BOTH owner edges — the corresponding capability
    # CONTRACT.md AND its paired ANATOMY.md — not general reachability via one
    # side. Lock the literal both-edges wording so it cannot regress to "either".
    for anchor in [
        "Manuals are discoverable from both owner twins",
        "both edges, not either one",
        "global reachability through only one side does not satisfy",
    ]:
        assert anchor in body
    # The detailed frontmatter and link-semantics rules carry the same both-twins
    # requirement (manual on the capability Contract and its paired Anatomy).
    assert "MUST link the same manual(s) so both owner twins" in body
    assert "Missing either edge is a defect" in body

    # Principle 3 makes the manual MANDATORY for every capability. The detailed
    # frontmatter and link-semantics rules must state that obligation without
    # optional/conditional escape hatches, and must not regress to "when the
    # capability has any" / "a capability that has a manual" optionality.
    assert "for every capability the governed component" in body
    assert "Every exposed capability MUST have such a manual" in body
    assert "Every capability's corresponding manual" in body
    assert "when the capability has any" not in body
    assert "a capability that has a manual" not in body


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
        # The child's Maintenance block is mechanically identical to the
        # canonical root block (byte text, version, and hash); any drift here is
        # a hard failure with a diagnostic, never a silent normalization.
        report = check_canonical_maintenance(
            contract_meta["name"], contract_path, contract_meta["maintenance"]
        )
        assert report is None, report
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
    contract_metas = {}
    for contract_path in child_contracts:
        contract_meta, _ = _read_document(ROOT / contract_path)
        contract_metas[contract_path] = contract_meta
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

    # A governed Contract may link implementation/navigation Anatomies that own
    # no independent promise. Each such Anatomy must have exactly one owner,
    # explain the absence of a local Contract, and link back. The validator emits
    # a complete report and never creates or rewrites documents to force a pass.
    owner_links: dict[str, list[str]] = {}
    for contract_path, contract_meta in contract_metas.items():
        for linked in contract_meta["related_files"]:
            if linked.endswith("/ANATOMY.md") and linked not in governed_anatomies:
                owner_links.setdefault(linked, []).append(contract_path)

    for anatomy_path, owners in owner_links.items():
        anatomy_meta, anatomy_body = _read_document(ROOT / anatomy_path)
        local_contract = str(Path(anatomy_path).with_name("CONTRACT.md"))
        local_contract_exists = (ROOT / local_contract).is_file()
        expected_owner = owners[0] if len(owners) == 1 else None
        normalized_body = " ".join(anatomy_body.split())
        link_issues = []
        if len(owners) != 1:
            link_issues.append(f"expected one governing Contract link, found {owners}")
        if local_contract_exists:
            link_issues.append(f"unexpected local Contract: {local_contract}")
        if expected_owner and expected_owner not in anatomy_meta["related_files"]:
            link_issues.append(f"Anatomy does not link back to owner: {expected_owner}")
        if expected_owner and expected_owner not in normalized_body:
            link_issues.append(f"Anatomy body does not name owner: {expected_owner}")
        if "no independent local Contract" not in normalized_body:
            link_issues.append("Anatomy body does not explain why no local Contract exists")
        if PAIRING_RULE_POINTER not in anatomy_meta["maintenance"]:
            link_issues.append("Anatomy Maintenance omits the root-rule pointer")

        report = pairing_mismatch_report(
            component_or_directory=str(Path(anatomy_path).parent),
            path=anatomy_path,
            actual_pair_state={
                "anatomy": True,
                "local_contract": local_contract if local_contract_exists else None,
                "owner_contracts": owners,
            },
            violated_rule="unique governed Contract ownership for implementation Anatomy",
            expected_owner=expected_owner,
            link_issues=link_issues,
        )
        assert not link_issues, report


def test_pairing_mismatch_report_has_required_fail_loud_fields() -> None:
    report = pairing_mismatch_report(
        component_or_directory="src/example/adapter",
        path="src/example/adapter/ANATOMY.md",
        actual_pair_state={"anatomy": True, "local_contract": None},
        violated_rule="missing unique owning component Contract",
        expected_owner="src/example/CONTRACT.md",
        link_issues=["missing reciprocal owner link"],
    )
    assert list(report) == [
        "component_or_directory",
        "path",
        "actual_pair_state",
        "violated_rule",
        "expected_owner",
        "link_issues",
        "suggested_action",
    ]
    assert "do not create" in report["suggested_action"]
    assert "auto-fix" in report["suggested_action"]


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
    assert "Repository structure/composition map" in readme
    assert "governed-component pairing/ownership rule" in readme
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
        "Classify the Anatomy/Contract relationship first",
        "root-defined mismatch fields",
        "Copy the canonical Maintenance block exactly",
        "byte-for-byte",
        "CANONICAL-MAINTENANCE v<N> BEGIN",
        "Stop and report that diagnostic",
        "Do not\n   silently normalize, hand-edit, or auto-fix",
        # The dev guide must strongly route to and enforce the root
        # design-principles section (root CONTRACT.md `## Design principles`),
        # including the both-twins manual-discoverability rule.
        "Design principles",
        "apply those principles to every change",
        "mandatory reading",
        "discoverable from **both** the corresponding",
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
    assert "unique owning governed Contract" in anatomy_skill
    assert "fail-loud reporting rule" in anatomy_skill


# ---------------------------------------------------------------------------
# Canonical Maintenance block — mechanism, fixtures, and counterexamples.
#
# These prove the validator's contract against synthetic inputs so the proof
# does not depend on the current governed-child set (which is legitimately
# empty at this convention-only stage). A synthetic "root" source embeds a
# controllable block; a synthetic "child" copies (or perturbs) it.
# ---------------------------------------------------------------------------

_FIXTURE_INDENT = "  "


def _make_root_source(version: int = 1, body: str = "Line one.\nLine two.") -> str:
    """Build a minimal source carrying one canonical block with the given body."""
    lines = [f"{_FIXTURE_INDENT}<!-- CANONICAL-MAINTENANCE v{version} BEGIN -->"]
    for line in body.split("\n"):
        lines.append(f"{_FIXTURE_INDENT}{line}" if line else "")
    lines.append(f"{_FIXTURE_INDENT}<!-- CANONICAL-MAINTENANCE END -->")
    return "prefix\n" + "\n".join(lines) + "\nsuffix\n"


def test_canonical_maintenance_extracts_from_real_root() -> None:
    version, text, digest = canonical_maintenance()
    assert version == 2
    assert text.startswith("<!-- CANONICAL-MAINTENANCE v2 BEGIN -->\n")
    assert PAIRING_RULE_POINTER in text
    assert text.endswith("<!-- CANONICAL-MAINTENANCE END -->\n")
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    # The extracted block equals the derived constant used elsewhere.
    assert text == CHILD_CONTRACT_MAINTENANCE


def test_real_child_template_carries_the_canonical_block() -> None:
    # The `## Template` block scalar, when parsed as YAML, must reproduce the
    # canonical block byte-for-byte — proving copy-the-template yields a PASS.
    contract_meta, _ = _read_document(ROOT_CONTRACT)
    template_text = ROOT_CONTRACT.read_text(encoding="utf-8")
    fence = template_text.split("```markdown\n", 1)[1].split("\n```", 1)[0]
    frontmatter = fence.split("---\n", 2)[1]
    template_meta = yaml.safe_load(frontmatter)
    report = check_canonical_maintenance(
        "template-example", "CONTRACT.md#Template", template_meta["maintenance"]
    )
    assert report is None, report
    # Per Design principles 3 and 4, the canonical child template must teach a
    # capability manual / manual-reference related_files slot so a copied child
    # carries the required Contract->manual edge; the terminology matches the
    # Frontmatter contract ("manual or manual reference").
    assert (
        "<repo-relative capability manual or manual reference>"
        in template_meta["related_files"]
    )


def test_byte_identical_child_passes() -> None:
    root = _make_root_source()
    _, canonical_text, _ = canonical_maintenance(root)
    report = check_canonical_maintenance(
        "widget", "src/widget/CONTRACT.md", canonical_text, source=root
    )
    assert report is None


def test_one_character_mismatch_hard_fails_with_diagnostic() -> None:
    root = _make_root_source()
    _, canonical_text, expected_hash = canonical_maintenance(root)
    version = 1
    # Flip exactly one character deep inside the body.
    idx = canonical_text.index("Line two.") + 5  # inside "two."
    perturbed = canonical_text[:idx] + ("X" if canonical_text[idx] != "X" else "Y") + canonical_text[idx + 1 :]
    report = check_canonical_maintenance(
        "widget", "src/widget/CONTRACT.md", perturbed, source=root
    )
    assert report is not None
    # Required diagnostic fields are all present.
    for field in [
        "component",
        "path",
        "expected_version",
        "actual_version",
        "expected_hash",
        "actual_hash",
        "first_difference",
    ]:
        assert field in report, field
    assert report["component"] == "widget"
    assert report["path"] == "src/widget/CONTRACT.md"
    assert report["expected_version"] == version
    # A single-char body edit keeps the marker/version but changes text+hash.
    assert report["actual_version"] == version
    assert report["expected_hash"] == expected_hash
    assert report["actual_hash"] != expected_hash
    assert report["first_difference"] == idx


def test_trailing_whitespace_mismatch_does_not_silently_pass() -> None:
    root = _make_root_source()
    _, canonical_text, _ = canonical_maintenance(root)
    report = check_canonical_maintenance(
        "widget", "src/widget/CONTRACT.md", canonical_text + " ", source=root
    )
    assert report is not None
    assert report["first_difference"] == len(canonical_text.encode("utf-8"))


def test_first_difference_is_a_utf8_byte_offset_not_a_character_index() -> None:
    # U+00E9 occupies two UTF-8 bytes, so the differing ASCII byte follows at
    # offset 2 even though its Python character index is 1.
    assert _first_difference("éx", "éy") == 2


def test_version_mismatch_cannot_silently_pass() -> None:
    root = _make_root_source(version=1)
    # Child text is byte-identical EXCEPT it advertises v2 in its marker.
    _, canonical_text, _ = canonical_maintenance(root)
    child = canonical_text.replace(
        "CANONICAL-MAINTENANCE v1 BEGIN", "CANONICAL-MAINTENANCE v2 BEGIN"
    )
    report = check_canonical_maintenance(
        "widget", "src/widget/CONTRACT.md", child, source=root
    )
    assert report is not None
    assert report["expected_version"] == 1
    assert report["actual_version"] == 2


def test_hash_mismatch_is_reported_even_when_version_matches() -> None:
    root = _make_root_source()
    _, canonical_text, expected_hash = canonical_maintenance(root)
    # Same version marker, different body -> hash differs.
    child = canonical_text.replace("Line one.", "Line ONE.")
    report = check_canonical_maintenance(
        "widget", "src/widget/CONTRACT.md", child, source=root
    )
    assert report is not None
    assert report["actual_version"] == 1
    assert report["expected_hash"] == expected_hash
    assert report["actual_hash"] != expected_hash


def test_missing_marker_child_cannot_pass() -> None:
    root = _make_root_source()
    # A child that paraphrased the block away entirely: no marker, no version.
    child = "Some hand-written maintenance text with no canonical markers.\n"
    report = check_canonical_maintenance(
        "widget", "src/widget/CONTRACT.md", child, source=root
    )
    assert report is not None
    assert report["actual_version"] is None
    assert report["expected_version"] == 1


def test_source_missing_marker_hard_fails_extraction() -> None:
    try:
        canonical_maintenance("no markers at all here\n")
    except CanonicalMaintenanceError:
        pass
    else:
        raise AssertionError("expected CanonicalMaintenanceError for missing marker")


def test_source_with_duplicate_markers_hard_fails_extraction() -> None:
    root = _make_root_source() + _make_root_source()  # two BEGIN + two END
    try:
        canonical_maintenance(root)
    except CanonicalMaintenanceError:
        pass
    else:
        raise AssertionError("expected CanonicalMaintenanceError for duplicate markers")


def test_end_before_begin_hard_fails_extraction() -> None:
    swapped = (
        "  <!-- CANONICAL-MAINTENANCE END -->\n"
        "  body\n"
        "  <!-- CANONICAL-MAINTENANCE v1 BEGIN -->\n"
    )
    try:
        canonical_maintenance(swapped)
    except CanonicalMaintenanceError:
        pass
    else:
        raise AssertionError("expected CanonicalMaintenanceError when END precedes BEGIN")


def test_validator_scans_real_governed_children_when_present(tmp_path) -> None:
    # The governed-child validator iterates root `related_files` CONTRACT.md
    # entries and applies `check_canonical_maintenance` to each. With zero
    # governed children today this is vacuously true, so we additionally prove
    # the checker runs against a real on-disk child file when one exists.
    child_dir = tmp_path / "widget"
    child_dir.mkdir()
    child_path = child_dir / "CONTRACT.md"
    _, canonical_text, _ = canonical_maintenance()
    # Emit the child with a YAML `maintenance: |` block so it parses back to the
    # canonical value byte-for-byte.
    indented = "\n".join(
        (f"  {line}" if line else "") for line in canonical_text.rstrip("\n").split("\n")
    )
    child_path.write_text(
        "---\nname: widget-contract\n"
        "contract_version: 1\nroot_contract: CONTRACT.md\n"
        "related_files:\n  - CONTRACT.md\n"
        f"maintenance: |\n{indented}\n---\n# Widget\n",
        encoding="utf-8",
    )
    meta, _ = _read_document(child_path)
    report = check_canonical_maintenance(
        meta["name"], "widget/CONTRACT.md", meta["maintenance"]
    )
    assert report is None, report

    # And a one-character on-disk drift is caught with a first-difference.
    drifted = child_path.read_text(encoding="utf-8").replace(
        "governed by the root", "governed by the ROOT", 1
    )
    child_path.write_text(drifted, encoding="utf-8")
    meta2, _ = _read_document(child_path)
    report2 = check_canonical_maintenance(
        meta2["name"], "widget/CONTRACT.md", meta2["maintenance"]
    )
    assert report2 is not None
    assert report2["actual_hash"] != report2["expected_hash"]


def test_governed_set_is_truthfully_reconciled() -> None:
    # Governed children come only from root `related_files`; legacy or staged
    # on-disk CONTRACT.md files are not governed until explicitly linked there.
    root_meta, _ = _read_document(ROOT_CONTRACT)
    governed = [
        path
        for path in root_meta["related_files"]
        if path.endswith("/CONTRACT.md") and path != "CONTRACT.md"
    ]
    # Whatever the governed set is (empty today), each governed child must pass
    # the canonical check — never silently skipped.
    for contract_path in governed:
        meta, _ = _read_document(ROOT / contract_path)
        assert (
            check_canonical_maintenance(meta["name"], contract_path, meta["maintenance"])
            is None
        )


def test_anatomy_points_to_the_normative_maintenance_rule_without_duplicating() -> None:
    _, body = _read_document(ROOT_ANATOMY)
    # Anatomy explains the uniform maintenance entry/backlink and points at the
    # normative root Contract + validator...
    assert "uniform `maintenance` frontmatter entry" in body
    assert "canonical Maintenance block from" in body
    assert "tests/test_architecture_documents.py" in body
    assert "does not restate a second, possibly\ndivergent, copy of it" in body
    assert PAIRING_RULE_POINTER in body
    # ...without embedding its own copy of the canonical block markers.
    assert "CANONICAL-MAINTENANCE" not in body
