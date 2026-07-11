"""Docs/routing contract for the Kimi Code daemon-backend submanual.

The Kimi Code child under the daemon CLI-backend router is a small
progressive-disclosure entrypoint: it routes agents to the installed CLI's
live help (via bash) and shows how to translate that help into the generic
``backend_options`` mechanism. It must never grow into a maintained flag
catalog. The generic conversion behavior and the kimicode runner/reserved
flag enforcement themselves are covered by
``tests/test_daemon_backend_options.py`` (see e.g.
``test_kimicode_alias_and_canonical_dispatch_to_backend``,
``test_kimicode_cmd_appends_backend_argv_before_owned_flags``,
``test_kimicode_rejects_harness_owned_backend_options``,
``test_kimicode_writes_run_private_mcp_json_for_common_and_parent_mcp``,
``test_kimicode_ask_is_explicitly_unsupported``) and are not re-tested here.
"""

import re
from pathlib import Path

import yaml

from lingtai.tools.daemon import (
    _BACKEND_ALIASES,
    _KIMICODE_RESERVED_BACKEND_FLAGS,
)

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md"
CHILD = (
    ROOT
    / "src/lingtai/tools/daemon/manual/reference/cli-backends"
    / "reference/backends/kimicode/SKILL.md"
)
CHILD_LOCATION = "reference/backends/kimicode/SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2]


def test_router_has_yaml_catalog_entry_for_kimicode_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Nested reference catalog" in text
    catalog_section = text.split("## Nested reference catalog", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", catalog_section, re.DOTALL)
    assert match, "nested reference catalog must be a fenced YAML block"
    entries = yaml.safe_load(match.group(1))
    kimicode_entries = [e for e in entries if e.get("location") == CHILD_LOCATION]
    assert len(kimicode_entries) == 1
    assert kimicode_entries[0]["name"] == "daemon-backend-kimicode"
    assert kimicode_entries[0]["description"].strip()


def test_router_routing_table_points_to_kimicode_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Routing table" in text
    table_rows = [
        line for line in text.splitlines()
        if line.startswith("|") and CHILD_LOCATION in line
    ]
    assert table_rows, "routing table must map Kimi flag needs to the child"


def test_kimicode_child_frontmatter_and_location():
    assert CHILD.is_file()
    meta = _frontmatter(CHILD)
    assert meta["name"] == "daemon-backend-kimicode"
    assert meta["description"].strip()
    assert meta["last_changed_at"]


def test_kimicode_child_routes_to_live_help_and_generic_backend_options():
    body = _body(CHILD)
    # Live installed help is the authority — the child must send agents there.
    for phrase in (
        "bash-manual",
        "reference/bash-kimicode/SKILL.md",
        "kimi --version",
        "kimi --help",
    ):
        assert phrase in body, phrase
    # Translation goes through the existing generic mechanism, and the
    # high-value model-selection example uses a plain string option.
    assert "backend_options" in body
    assert '"model": "kimi-for-coding"' in body
    # The CLI/provider owns the value vocabulary; no LingTai-side enum.
    assert "not validate, enumerate, or simulate" in body


def test_kimicode_child_names_canonical_alias_and_limitations():
    body = _body(CHILD)
    # Canonical/alias naming matches the backend alias table.
    assert _BACKEND_ALIASES["kimi"] == "kimicode"
    assert "kimicode" in body and '"kimi"' in body
    # Every source-reserved harness flag is documented, exactly.
    for flag in _KIMICODE_RESERVED_BACKEND_FLAGS:
        assert f"`{flag}`" in body, flag
    # Current limitations and MCP wiring stated as they are implemented.
    assert 'daemon(action="ask")' in body
    assert "unsupported" in body
    assert "kimi-code-home/mcp.json" in body
    assert "backend_harness_files.kimicode_mcp_config" in body


def test_kimicode_child_stays_tiny_not_a_flag_catalog():
    line_count = len(CHILD.read_text(encoding="utf-8").splitlines())
    assert line_count <= 90, (
        "the Kimi Code backend submanual is a tiny entrypoint to live CLI "
        f"help; {line_count} lines suggests it is growing into a flag catalog"
    )
