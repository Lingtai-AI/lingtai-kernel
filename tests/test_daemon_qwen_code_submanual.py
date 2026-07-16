"""Docs/routing contract for the Qwen Code daemon-backend submanual.

The Qwen Code child under the daemon CLI-backend router is a small
progressive-disclosure entrypoint: it routes agents to the installed CLI's
live help (via bash) and shows how to translate that help into the generic
``backend_options`` mechanism. It must never grow into a maintained flag
catalog. The runtime behavior it documents is covered elsewhere and is not
re-tested here: generic conversion and qwen argv placement in
``tests/test_daemon_backend_options.py`` (see
``test_qwen_code_cmd_appends_backend_argv_before_prompt``,
``test_qwen_code_rejects_harness_owned_backend_options``,
``test_qwen_code_ask_is_explicitly_unsupported``).
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md"
CHILD = (
    ROOT
    / "src/lingtai/tools/daemon/manual/reference/cli-backends"
    / "reference/backends/qwen-code/SKILL.md"
)
CHILD_LOCATION = "reference/backends/qwen-code/SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2]


def test_router_has_yaml_catalog_entry_for_qwen_code_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Nested reference catalog" in text
    catalog_section = text.split("## Nested reference catalog", 1)[1]
    match = re.search(r"```yaml\n(.*?)```", catalog_section, re.DOTALL)
    assert match, "nested reference catalog must be a fenced YAML block"
    entries = yaml.safe_load(match.group(1))
    qwen_entries = [e for e in entries if e.get("location") == CHILD_LOCATION]
    assert len(qwen_entries) == 1
    assert qwen_entries[0]["name"] == "daemon-backend-qwen-code"
    assert qwen_entries[0]["description"].strip()


def test_router_routing_table_points_to_qwen_code_child():
    text = ROUTER.read_text(encoding="utf-8")
    assert "## Routing table" in text
    table_rows = [
        line for line in text.splitlines()
        if line.startswith("|") and CHILD_LOCATION in line
    ]
    assert table_rows, "routing table must map Qwen Code flag needs to the child"


def test_qwen_code_child_frontmatter_and_location():
    assert CHILD.is_file()
    meta = _frontmatter(CHILD)
    assert meta["name"] == "daemon-backend-qwen-code"
    assert meta["description"].strip()
    assert meta["last_changed_at"]


def test_qwen_code_child_routes_to_live_help_and_generic_backend_options():
    body = _body(CHILD)
    # Live installed help is the authority — the child must send agents there.
    # The daemon wraps the top-level `qwen` binary (no subcommand), so only
    # top-level help is cited.
    for phrase in (
        "shell-manual",
        "qwen --version",
        "qwen --help",
        "no subcommand",
    ):
        assert phrase in body, phrase
    # Translation goes through the existing generic mechanism, and the
    # high-value model-selection example matches the tested argv placement.
    assert "backend_options" in body
    assert "qwen3-coder-plus" in body
    assert "qwen --yolo --model qwen3-coder-plus -p <prompt>" in body
    # The CLI/provider owns the value vocabulary; no LingTai-side enum.
    assert "not validate, enumerate, or simulate" in body


def test_qwen_code_child_states_harness_boundaries():
    body = _body(CHILD)
    # Reserved headless flags are named so agents don't burn a batch on them.
    for phrase in ("--prompt", "--yolo", "--approval-mode"):
        assert phrase in body, phrase
    # Per-run MCP settings injection and the no-resume planning consequence.
    assert "QWEN_CODE_SYSTEM_SETTINGS_PATH" in body
    assert "qwen-daemon-settings.json" in body
    assert "daemon(action='ask')" in body


def test_qwen_code_child_stays_tiny_not_a_flag_catalog():
    line_count = len(CHILD.read_text(encoding="utf-8").splitlines())
    assert line_count <= 90, (
        "the Qwen Code backend submanual is a tiny entrypoint to live CLI "
        f"help; {line_count} lines suggests it is growing into a flag catalog"
    )
