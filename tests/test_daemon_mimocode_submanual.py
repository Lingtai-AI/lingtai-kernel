"""Docs/routing contract for the MiMo Code daemon-backend submanual.

The MiMo Code child under the daemon CLI-backend router is a small
progressive-disclosure entrypoint: it routes agents to the installed CLI's
live help (via bash) and shows how to translate that help into the generic
``backend_options`` mechanism. It must never grow into a maintained flag
catalog. The runtime behavior itself (alias canonicalization, argv placement,
reserved ``--format``, session capture) is covered by
``tests/test_daemon_backend_options.py`` (see e.g.
``test_mimocode_alias_dispatches_to_canonical_backend``,
``test_mimocode_cmd_appends_backend_argv_before_prompt``) and is not
re-tested here.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md"
CHILD = (
    ROOT
    / "src/lingtai/tools/daemon/manual/reference/cli-backends"
    / "reference/backends/mimocode/SKILL.md"
)
CHILD_LOCATION = "reference/backends/mimocode/SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2]


def test_router_has_yaml_catalog_entry_for_mimocode_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Nested reference catalog" in text
    catalog_section = text.split("## Nested reference catalog", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", catalog_section, re.DOTALL)
    assert match, "nested reference catalog must be a fenced YAML block"
    entries = yaml.safe_load(match.group(1))
    mimocode_entries = [e for e in entries if e.get("location") == CHILD_LOCATION]
    assert len(mimocode_entries) == 1
    assert mimocode_entries[0]["name"] == "daemon-backend-mimocode"
    assert mimocode_entries[0]["description"].strip()


def test_router_routing_table_points_to_mimocode_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Routing table" in text
    table_rows = [
        line for line in text.splitlines()
        if line.startswith("|") and CHILD_LOCATION in line
    ]
    assert table_rows, "routing table must map MiMo flag needs to the child"


def test_mimocode_child_frontmatter_and_location():
    assert CHILD.is_file()
    meta = _frontmatter(CHILD)
    assert meta["name"] == "daemon-backend-mimocode"
    assert meta["description"].strip()
    assert meta["last_changed_at"]


def test_mimocode_child_routes_to_live_help_and_generic_backend_options():
    body = _body(CHILD)
    # Live installed help is the authority — the child must send agents there.
    for phrase in (
        "bash-manual",
        "mimo --version",
        "mimo --help",
        "mimo run --help",
    ):
        assert phrase in body, phrase
    # Translation goes through the existing generic mechanism.
    assert "backend_options" in body
    # The CLI owns the value vocabulary; no LingTai-side enum.
    assert "not\nvalidate, enumerate, or simulate" in body or (
        "not validate, enumerate, or simulate" in " ".join(body.split())
    )


def test_mimocode_child_states_verified_backend_contract():
    body = _body(CHILD)
    # Canonical name / alias, exact spawn shape, reserved harness flag, and
    # current MCP status — all verified against src/lingtai/tools/daemon.
    assert "canonicalizes it to `mimocode`" in body
    assert "mimo run --format json <prompt>" in body
    assert "`--format`" in body
    assert "mimo run --session <mimocode_session_id> --format json" in body
    assert "not wired for `mimocode` yet" in body


def test_mimocode_child_stays_tiny_not_a_flag_catalog():
    line_count = len(_body(CHILD).splitlines())
    assert line_count <= 90, (
        "the MiMo Code backend submanual is a tiny entrypoint to live CLI "
        f"help; {line_count} lines suggests it is growing into a flag catalog"
    )
