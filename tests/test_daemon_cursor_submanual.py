"""Docs/routing contract for the Cursor daemon-backend submanual.

The Cursor child under the daemon CLI-backend router is a small
progressive-disclosure entrypoint: it routes agents to the installed `agent`
CLI's live help (via bash) and shows how to translate that help into the
generic ``backend_options`` mechanism. It must never grow into a maintained
flag catalog. The generic conversion behavior itself is covered by
``tests/test_daemon_backend_options.py`` and the Cursor runner/ask behavior
by ``tests/test_daemon_cursor_backend.py``; neither is re-tested here.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md"
CHILD = (
    ROOT
    / "src/lingtai/tools/daemon/manual/reference/cli-backends"
    / "reference/backends/cursor/SKILL.md"
)
CHILD_LOCATION = "reference/backends/cursor/SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2]


def test_router_has_yaml_catalog_entry_for_cursor_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Nested reference catalog" in text
    catalog_section = text.split("## Nested reference catalog", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", catalog_section, re.DOTALL)
    assert match, "nested reference catalog must be a fenced YAML block"
    entries = yaml.safe_load(match.group(1))
    cursor_entries = [e for e in entries if e.get("location") == CHILD_LOCATION]
    assert len(cursor_entries) == 1
    assert cursor_entries[0]["name"] == "daemon-backend-cursor"
    assert cursor_entries[0]["description"].strip()


def test_router_routing_table_points_to_cursor_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Routing table" in text
    table_rows = [
        line for line in text.splitlines()
        if line.startswith("|") and CHILD_LOCATION in line
    ]
    assert table_rows, "routing table must map Cursor flag needs to the child"


def test_cursor_child_frontmatter_and_location():
    assert CHILD.is_file()
    meta = _frontmatter(CHILD)
    assert meta["name"] == "daemon-backend-cursor"
    assert meta["description"].strip()
    assert meta["last_changed_at"]


def test_cursor_child_routes_to_live_help_and_generic_backend_options():
    body = _body(CHILD)
    # Live installed help is the authority — the child must send agents there.
    for phrase in (
        "shell-manual",
        "reference/bash-cursor-agent/SKILL.md",
        "agent --version",
        "agent --help",
    ):
        assert phrase in body, phrase
    # The harness-owned spawn shape and session/resume contract stay stated
    # exactly as the runner implements them.
    assert "agent -p --force --output-format stream-json" in body
    assert "cursor_session_id" in body
    assert "--resume" in body
    # Translation goes through the existing generic mechanism only.
    assert "backend_options" in body
    # The CLI/model owns the value vocabulary; no LingTai-side enum.
    assert "not validate, enumerate, or simulate" in body
    # MCP wiring status must be stated, not implied.
    assert "not wired" in body


def test_cursor_child_stays_tiny_not_a_flag_catalog():
    line_count = len(CHILD.read_text(encoding="utf-8").splitlines())
    assert line_count <= 90, (
        "the Cursor backend submanual is a tiny entrypoint to live CLI help; "
        f"{line_count} lines suggests it is growing into a flag catalog"
    )
