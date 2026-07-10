"""Docs/routing contract for the claude-p daemon-backend submanual.

The claude-p child under the daemon CLI-backend router is a small
progressive-disclosure entrypoint: it routes agents to the installed CLI's
live help (via bash) and shows how to translate that help into the generic
``backend_options`` mechanism. It must never grow into a maintained flag
catalog. The generic conversion behavior itself is covered by
``tests/test_daemon_backend_options.py`` (see e.g.
``test_argv_underscore_key_becomes_dash``,
``test_argv_mixed_options_preserve_key_order``,
``test_claude_code_cmd_appends_backend_argv_before_task``,
``test_emanate_cli_rejects_bad_backend_options``,
``test_emanate_cli_persists_resolved_options``) and is not re-tested here.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "src/tools/daemon/manual/reference/cli-backends/SKILL.md"
CHILD = (
    ROOT
    / "src/tools/daemon/manual/reference/cli-backends"
    / "reference/backends/claude-p/SKILL.md"
)
CHILD_LOCATION = "reference/backends/claude-p/SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2]


def test_router_has_yaml_catalog_entry_for_claude_p_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Nested reference catalog" in text
    catalog_section = text.split("## Nested reference catalog", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", catalog_section, re.DOTALL)
    assert match, "nested reference catalog must be a fenced YAML block"
    entries = yaml.safe_load(match.group(1))
    claude_p_entries = [e for e in entries if e.get("location") == CHILD_LOCATION]
    assert len(claude_p_entries) == 1
    assert claude_p_entries[0]["name"] == "daemon-backend-claude-p"
    assert claude_p_entries[0]["description"].strip()


def test_router_routing_table_points_to_claude_p_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Routing table" in text
    table_rows = [
        line for line in text.splitlines()
        if line.startswith("|") and CHILD_LOCATION in line
    ]
    assert table_rows, "routing table must map Claude Code flag needs to the child"


def test_claude_p_child_frontmatter_and_location():
    assert CHILD.is_file()
    meta = _frontmatter(CHILD)
    assert meta["name"] == "daemon-backend-claude-p"
    assert meta["description"].strip()
    assert meta["last_changed_at"]


def test_claude_p_child_routes_to_live_help_and_generic_backend_options():
    body = _body(CHILD)
    # Live installed help is the authority — the child must send agents there.
    for phrase in (
        "bash-manual",
        "claude --version",
        "claude --help",
    ):
        assert phrase in body, phrase
    # Translation goes through the existing generic mechanism, and the
    # high-value fallback-model example uses the underscore→dash rule.
    assert "backend_options" in body
    assert '"fallback_model"' in body
    assert "--fallback-model" in body
    # The CLI/provider owns the value vocabulary; no LingTai-side enum.
    assert "not validate, enumerate, or simulate" in body


def test_claude_p_child_states_alias_and_harness_boundary():
    body = _body(CHILD)
    # The alias relationship is part of the source contract.
    assert "claude-code" in body
    assert "compatibility alias" in body
    # Source-verified harness-owned flags refused in backend_options
    # (_CLAUDE_COMMON_RESERVED_BACKEND_FLAGS).
    for flag in (
        "--settings",
        "--print",
        "--output-format",
        "--mcp-config",
        "--strict-mcp-config",
    ):
        assert flag in body, flag
    # Resume + auth-env hygiene are stated, not re-specified.
    assert "claude_session_id" in body
    for env_var in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        assert env_var in body, env_var


def test_claude_p_child_stays_tiny_not_a_flag_catalog():
    line_count = len(CHILD.read_text(encoding="utf-8").splitlines())
    assert line_count <= 90, (
        "the claude-p backend submanual is a tiny entrypoint to live CLI help; "
        f"{line_count} lines suggests it is growing into a flag catalog"
    )
