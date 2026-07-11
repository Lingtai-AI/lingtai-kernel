"""Docs/routing contract for the Oh-My-Pi daemon-backend submanual.

The Oh-My-Pi child under the daemon CLI-backend router is a small
progressive-disclosure entrypoint: it routes agents to the installed CLI's
live help (via bash) and shows how to translate that help into the generic
``backend_options`` mechanism. It must never grow into a maintained flag
catalog. The generic conversion behavior itself is covered by
``tests/test_daemon_backend_options.py`` (see e.g.
``test_oh_my_pi_rejects_harness_owned_backend_options``,
``test_oh_my_pi_cmd_includes_mode_json_and_session_id_from_header``,
``test_oh_my_pi_ask_resume_uses_session_flag``) and is not re-tested here.
"""

import re
from pathlib import Path

import yaml

from lingtai.tools.daemon import (
    _BACKEND_ALIASES,
    _OH_MY_PI_RESERVED_BACKEND_FLAGS,
    _cli_backend_loads_common_mcp,
)

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md"
CHILD = (
    ROOT
    / "src/lingtai/tools/daemon/manual/reference/cli-backends"
    / "reference/backends/oh-my-pi/SKILL.md"
)
CHILD_LOCATION = "reference/backends/oh-my-pi/SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2]


def test_router_has_yaml_catalog_entry_for_oh_my_pi_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Nested reference catalog" in text
    catalog_section = text.split("## Nested reference catalog", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", catalog_section, re.DOTALL)
    assert match, "nested reference catalog must be a fenced YAML block"
    entries = yaml.safe_load(match.group(1))
    omp_entries = [e for e in entries if e.get("location") == CHILD_LOCATION]
    assert len(omp_entries) == 1
    assert omp_entries[0]["name"] == "daemon-backend-oh-my-pi"
    assert omp_entries[0]["description"].strip()


def test_router_routing_table_points_to_oh_my_pi_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Routing table" in text
    table_rows = [
        line for line in text.splitlines()
        if line.startswith("|") and CHILD_LOCATION in line
    ]
    assert table_rows, "routing table must map Oh-My-Pi flag needs to the child"


def test_oh_my_pi_child_frontmatter_and_location():
    assert CHILD.is_file()
    meta = _frontmatter(CHILD)
    assert meta["name"] == "daemon-backend-oh-my-pi"
    assert meta["description"].strip()
    assert meta["last_changed_at"]


def test_oh_my_pi_child_routes_to_live_help_and_generic_backend_options():
    body = _body(CHILD)
    # Live installed help is the authority — the child must send agents there,
    # and only to source-proven subcommand help on demand.
    for phrase in (
        "bash-manual",
        "omp --version",
        "omp --help",
        "omp <command> --help",
    ):
        assert phrase in body, phrase
    # Translation goes through the existing generic mechanism, and the
    # high-value model-selection example uses the plain long-flag route.
    assert "backend_options" in body
    assert "--model" in body
    # The CLI/providers own the value vocabulary; no LingTai-side enum.
    assert "not validate, enumerate, or simulate" in body


def test_oh_my_pi_child_documents_alias_and_exact_reserved_flags():
    body = _body(CHILD)
    # Alias contract matches source.
    assert _BACKEND_ALIASES["omp"] == "oh-my-pi"
    assert "canonicalizes to" in body
    # Every harness-reserved flag from source appears verbatim; no stale
    # extras are claimed as reserved beyond the documented harness argv.
    for flag in _OH_MY_PI_RESERVED_BACKEND_FLAGS:
        assert f"`{flag}`" in body, flag
    documented = set(re.findall(r"`(--[a-z-]+)`", body))
    non_reserved_mentions = {
        "--mode", "--approval-mode", "--session",  # documented harness argv
        "--model", "--help",  # example + discovery surfaces
    }
    assert documented - _OH_MY_PI_RESERVED_BACKEND_FLAGS <= non_reserved_mentions


def test_oh_my_pi_child_mcp_status_matches_source():
    # Source truth: oh-my-pi does not load the per-run daemon_common MCP yet.
    assert not _cli_backend_loads_common_mcp("oh-my-pi")
    body = _body(CHILD)
    assert "not wired yet" in body


def test_oh_my_pi_child_stays_tiny_not_a_flag_catalog():
    line_count = len(CHILD.read_text(encoding="utf-8").splitlines())
    assert line_count <= 90, (
        "the Oh-My-Pi backend submanual is a tiny entrypoint to live CLI help; "
        f"{line_count} lines suggests it is growing into a flag catalog"
    )
