from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROOT_BEHAVIORS = ROOT / "BEHAVIORS.md"


# A LABT id is either the default B### scheme or a per-family prefix followed
# by three digits (e.g. D003, FE001, ACP001).
LABT_ID_RE = re.compile(r"^[A-Z]{1,3}\d{3}$")

REQUIRED_FRONTMATTER_KEYS = {
    "name",
    "behavior_version",
    "labt_version",
    "contract",
    "anatomy",
    "related_files",
    "maintenance",
}
REQUIRED_LABT_FIELDS = {"id", "title", "guards", "runner", "prerequisites", "estimate"}
OPTIONAL_LABT_FIELDS = {"supersedes"}
REQUIRED_LABT_SECTIONS = {"Steps", "Expected evidence", "Pass / Fail"}


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
    assert parts and all(part not in {"", "."} for part in parts), path
    relative = Path(*parts)
    assert not relative.is_absolute(), path
    target = (ROOT / relative).resolve(strict=True)
    assert target.is_relative_to(ROOT.resolve()), path
    assert target.is_file(), path


def _assert_dir_relative_file(base_dir: Path, path: str) -> None:
    """Require a path relative to a child BEHAVIORS.md directory.

    Frontmatter `contract:`/`anatomy:` are relative to the BEHAVIORS.md file's
    own directory (e.g. `CONTRACT.md`, `ANATOMY.md`, `../CONTRACT.md`).
    """
    assert path == path.strip(), path
    assert "\\" not in path, path
    parts = path.split("/")
    assert parts and all(part not in {"", "."} for part in parts), path
    relative = Path(*parts)
    assert not relative.is_absolute(), path
    target = (base_dir / relative).resolve(strict=True)
    assert target.is_relative_to(ROOT.resolve()), path
    assert target.is_file(), path


def _tracked_files() -> set[str]:
    """Every tracked path in the repository, as repo-relative POSIX strings."""
    import subprocess

    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in proc.stdout.splitlines() if line}


def _normalize(text: str) -> str:
    """Normalize a heading or clause name for loose equality checks."""
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _clause_tokens(text: str, truncate: bool = False) -> list[str]:
    """Meaningful lowercase alphanumeric tokens from a clause reference.

    When `truncate` is true (clause side only), annotation prose in
    parentheses or after an em/en dash is dropped so it cannot create false
    failures. The contract body side is tokenized WITHOUT truncation.
    """
    if truncate:
        for marker in ("(", "—", "–"):
            if marker in text:
                text = text.split(marker, 1)[0]
    tokens = re.findall(r"[a-z0-9]{3,}", text.lower())
    # Drop pure connective/number words that add no clause identity.
    stop = {
        "and", "the", "for", "with", "from", "that", "this", "must", "not",
        "shall", "rule", "rules", "section", "clause", "behavior", "contract",
        "ltp",
    }
    return [token for token in tokens if token not in stop]


def _contract_name_map() -> dict[str, str]:
    """Map repo-relative CONTRACT.md paths to their frontmatter `name:`."""
    result: dict[str, str] = {}
    for path in _tracked_files():
        if not path.endswith("/CONTRACT.md") and path != "CONTRACT.md":
            continue
        try:
            meta, _ = _read_document(ROOT / path)
        except Exception:
            continue
        name = meta.get("name")
        if isinstance(name, str):
            result[path] = name
    return result


def _child_behavior_paths(root_meta: dict) -> list[str]:
    """Child BEHAVIORS.md paths linked from the root, excluding the root itself."""
    related = root_meta.get("related_files")
    assert isinstance(related, list) and related
    return [
        path
        for path in related
        if path.endswith("BEHAVIORS.md") and path != "BEHAVIORS.md"
    ]


def _parse_labts(body: str, path: Path) -> list[tuple[str, dict, str]]:
    """Split a BEHAVIORS.md body into LABT sections.

    Returns `(id, fields_dict, section_text)` for each `## Behavior <id>` section.
    The fields dict maps `**field**` bullets to their values; section_text is the
    whole section body used for `### Steps` / `### Expected evidence` checks.
    """
    # Split on top-level `## Behavior <id> — <title>` headings.
    parts = re.split(r"(?m)^## Behavior (\S+)\s+[—-]?\s*(.*)$", body)
    labts: list[tuple[str, dict, str]] = []
    # parts[0] is the preamble; then id/title/body triplets follow.
    idx = 1
    while idx + 2 < len(parts):
        labt_id, title, section = parts[idx], parts[idx + 1], parts[idx + 2]
        fields: dict[str, str] = {}
        for match in re.finditer(
            r"^\s*[-*]?\s*\*\*([a-z_]+)\*\*:\s*(.*)$",
            section,
            flags=re.MULTILINE,
        ):
            fields[match.group(1)] = match.group(2).strip()
        labts.append((labt_id, fields, section))
        idx += 3
    return labts


def test_root_behaviors_frontmatter() -> None:
    """The root BEHAVIORS.md owns the LABT spec and is itself well-formed.

    The root is the meta-behavior file (it has no sibling CONTRACT.md of its
    own), so it is exempt from the `contract`/`anatomy` keys required of
    children; it must still declare labt_version and link every child.
    """
    meta, _ = _read_document(ROOT_BEHAVIORS)
    required = REQUIRED_FRONTMATTER_KEYS - {"contract", "anatomy"}
    assert required <= set(meta), meta.keys()
    assert meta["labt_version"] >= 1
    related = meta["related_files"]
    assert isinstance(related, list) and related
    for path in related:
        _assert_repo_file(path)


def test_child_behaviors_are_linked_and_parse() -> None:
    """Every child BEHAVIORS.md parses with full frontmatter and matches root."""
    root_meta, _ = _read_document(ROOT_BEHAVIORS)
    root_labt_version = root_meta["labt_version"]
    children = _child_behavior_paths(root_meta)
    assert children, "root BEHAVIORS.md must link at least one child"

    for path in children:
        child_dir = (ROOT / path).parent
        meta, body = _read_document(ROOT / path)
        missing = REQUIRED_FRONTMATTER_KEYS - set(meta)
        assert not missing, f"{path}: missing frontmatter keys {sorted(missing)}"
        assert (
            meta["labt_version"] == root_labt_version
        ), f"{path}: labt_version {meta['labt_version']} != root {root_labt_version}"
        # contract may be a single relative path or a list; anatomy single.
        contract_keys = (
            meta["contract"] if isinstance(meta["contract"], list) else [meta["contract"]]
        )
        for contract in contract_keys:
            _assert_dir_relative_file(child_dir, contract)
        _assert_dir_relative_file(child_dir, meta["anatomy"])
        related = meta["related_files"]
        assert isinstance(related, list) and related, f"{path}: empty related_files"
        assert len(related) == len(set(related)), f"{path}: duplicate related_files"
        for related_path in related:
            _assert_repo_file(related_path)
        assert body.strip(), f"{path}: empty body"


def test_child_labts_have_required_fields_and_sections() -> None:
    """Every LABT is self-contained: required fields plus Steps/Evidence/Pass-Fail."""
    root_meta, _ = _read_document(ROOT_BEHAVIORS)
    children = _child_behavior_paths(root_meta)
    seen_ids: dict[str, str] = {}

    for path in children:
        _, body = _read_document(ROOT / path)
        labts = _parse_labts(body, ROOT / path)
        assert labts, f"{path}: no `## Behavior <id>` sections found"
        for labt_id, fields, section in labts:
            assert LABT_ID_RE.match(labt_id), (
                f"{path}: id {labt_id!r} does not match LABT_ID_RE"
            )
            # GitHub anchors any `## Behavior <id>` heading as #behavior-<id-lower>.
            anchor = f"behavior-{labt_id.lower()}"
            assert f"## Behavior {labt_id}" in body, (
                f"{path}: section for {labt_id} must be addressable as #{anchor}"
            )
            prev = seen_ids.get(labt_id.lower())
            assert prev is None, f"{path}: duplicate id {labt_id} (also in {prev})"
            seen_ids[labt_id.lower()] = path

            missing = REQUIRED_LABT_FIELDS - set(fields)
            assert not missing, f"{path} {labt_id}: missing fields {sorted(missing)}"
            assert fields["id"] == labt_id, f"{path}: field id != heading id"
            assert fields["title"], f"{path} {labt_id}: empty title"
            assert fields["guards"], f"{path} {labt_id}: empty guards"
            assert fields["runner"], f"{path} {labt_id}: empty runner"
            # runner must describe an agent shape, not a pytest invocation.
            assert "pytest" not in fields["runner"].lower(), (
                f"{path} {labt_id}: runner must be an agent shape, not a pytest command"
            )

            for section_name in REQUIRED_LABT_SECTIONS:
                assert f"### {section_name}" in section, (
                    f"{path} {labt_id}: missing `### {section_name}` section"
                )

            supersedes = fields.get("supersedes")
            if supersedes:
                # Each superseded entry is a repo-relative pytest file, optionally
                # with a `::test_selector` suffix (strip it before resolving).
                for entry in re.findall(r"`([^`]+)`", supersedes):
                    file_part = entry.split("::", 1)[0]
                    _assert_repo_file(file_part)


def test_labt_guards_reference_real_contracts() -> None:
    """Each LABT guards a clause of a real contract.

    The guards value has the form `<contract-name> § <clause heading>`. The
    contract name must match the frontmatter `name:` of some tracked
    CONTRACT.md, and the clause heading must appear (normalized) in that
    contract's body so the backlink resolves to a real clause.
    """
    root_meta, _ = _read_document(ROOT_BEHAVIORS)
    children = _child_behavior_paths(root_meta)
    name_to_path = {name: path for path, name in _contract_name_map().items()}

    failures: list[str] = []
    for path in children:
        meta, body = _read_document(ROOT / path)
        # Contracts declared by this file (single or list).
        declared = (
            meta["contract"] if isinstance(meta["contract"], list) else [meta["contract"]]
        )
        declared_names = {
            name
            for contract in declared
            for contract_path, name in _contract_name_map().items()
            if contract_path == contract
        }
        # Fall back to the full repo map for cross-module guards (tool_family).
        for labt_id, fields, _ in _parse_labts(body, ROOT / path):
            guards = fields.get("guards", "")
            if "§" not in guards:
                failures.append(f"{path} {labt_id}: guards missing '§': {guards!r}")
                continue
            contract_name, clause = (part.strip() for part in guards.split("§", 1))
            # The name is a backtick-quoted contract name or path; if the guards
            # value carries extra prose after the backtick token, take that token.
            quoted = re.match(r"^`([^`]+)`", contract_name)
            if quoted:
                contract_name = quoted.group(1)
            else:
                contract_name = contract_name.strip("`")
            # Target may be a frontmatter name, a repo-relative CONTRACT.md path,
            # or (when the guards value embeds a markdown link) the linked file.
            target = name_to_path.get(contract_name)
            if target is None and contract_name.endswith("CONTRACT.md"):
                target = contract_name
            if target is None:
                # Fall back to a markdown link embedded in the guards value
                # (`[label](../../path/file.md#anchor)`), which names the real
                # file even when no contract frontmatter name matches (e.g. a
                # SKILL.md guard when a module has no root contract yet). The
                # link is relative to the child BEHAVIORS.md directory.
                linked = re.search(r"\[[^\]]+\]\(([^)]+)\)", guards)
                if linked:
                    candidate = linked.group(1).split("#", 1)[0]
                    if candidate.endswith(("CONTRACT.md", "SKILL.md")):
                        resolved = (ROOT / path).parent / candidate
                        target = str(resolved.relative_to(ROOT)).replace("\\", "/")
            if target is None:
                failures.append(
                    f"{path} {labt_id}: guards contract {contract_name!r} not found in repo"
                )
                continue
            try:
                if target.endswith("SKILL.md"):
                    # Manuals do not carry YAML frontmatter; read the whole text.
                    contract_body = (ROOT / target).read_text(encoding="utf-8")
                else:
                    _, contract_body = _read_document(ROOT / target)
            except Exception:
                contract_body = ""
            body_tokens = set(_clause_tokens(contract_body))
            clause_tokens = _clause_tokens(clause, truncate=True)
            if clause_tokens:
                missing_tokens = [
                    token for token in clause_tokens if token not in body_tokens
                ]
                if missing_tokens:
                    failures.append(
                        f"{path} {labt_id}: guards clause {clause!r} not found in {target} "
                        f"(missing tokens {missing_tokens})"
                    )

    assert not failures, "\n".join(failures)


def test_contract_clauses_link_guarding_labts() -> None:
    """Contract → behaviors edge: important clauses carry LABT links.

    This is the half of the tridirectional loop this convention is about: every
    CONTRACT.md that has a sibling BEHAVIORS.md must carry at least one
    `[B###](BEHAVIORS.md#behavior-b###)`-style link on its observable behavior
    clauses. We require the link form for every tracked CONTRACT.md that sits in
    a directory with a BEHAVIORS.md child.
    """
    import subprocess

    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = {line for line in proc.stdout.splitlines() if line}

    link_re = re.compile(
        r"\[([A-Z]{1,3}\d{3})\]\(([^)]*BEHAVIORS\.md#behavior-([a-z0-9]{1,3}\d{3}))\)"
    )
    failures: list[str] = []
    for path in tracked:
        if not path.endswith("/CONTRACT.md"):
            continue
        contract_dir = path[: path.rindex("/")]
        behaviors = f"{contract_dir}/BEHAVIORS.md"
        if behaviors not in tracked:
            continue  # no sibling behaviors file: nothing to link
        try:
            _, contract_body = _read_document(ROOT / path)
        except Exception as exc:
            failures.append(f"{path}: unparseable ({exc})")
            continue
        links = link_re.findall(contract_body)
        assert links, f"{path}: sibling {behaviors} exists but no LABT links found"
        # Each linked id must exist (case-insensitive) in the BEHAVIORS.md file
        # the link actually resolves to: the sibling for same-directory links,
        # or the declared cross-module file when a contract clause is guarded by
        # LABTs that live in another module (e.g. the kernel K-LABTs guarding
        # context/notification clauses).
        for label, url, anchor_id in links:
            target_path, _, _ = url.partition("#")
            target = (ROOT / Path(path).parent / target_path).resolve()
            try:
                _, behaviors_body = _read_document(target)
            except Exception as exc:
                failures.append(f"{path}: link target {url!r} unparseable ({exc})")
                continue
            behavior_ids = {
                labt_id.lower() for labt_id, _, _ in _parse_labts(behaviors_body, target)
            }
            if anchor_id not in behavior_ids:
                failures.append(
                    f"{path}: link [{label}]({url}) anchors to missing id {anchor_id} "
                    f"in {target}"
                )

    assert not failures, "\n".join(failures)
