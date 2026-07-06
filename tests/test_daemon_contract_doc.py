from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "src/lingtai/core/daemon/DAEMON_CONTRACT.md"
DOC_REL = "src/lingtai/core/daemon/DAEMON_CONTRACT.md"
REQUIRED_RELATED = [
    "src/lingtai/core/daemon/ANATOMY.md",
    "src/lingtai/core/daemon/__init__.py",
    "src/lingtai/core/daemon/run_dir.py",
    "src/lingtai/core/daemon/manual/SKILL.md",
    "src/lingtai/core/daemon/manual/reference/cli-backends/SKILL.md",
    "src/lingtai/mcp_servers/daemon_common/server.py",
    "tests/test_daemon.py",
    "tests/test_daemon_backend_options.py",
    "tests/test_daemon_claude_p_background_guard.py",
    "tests/test_daemon_opencode_backend.py",
    "tests/test_daemon_run_dir.py",
]
REQUIRED_TRIGGERS = [
    "src/lingtai/core/daemon/__init__.py",
    "src/lingtai/core/daemon/run_dir.py",
    "src/lingtai/core/daemon/ANATOMY.md",
    "src/lingtai/core/daemon/manual/",
    "src/lingtai/mcp_servers/daemon_common/",
    "tests/test_daemon_backend_options.py",
    "tests/test_daemon_claude_p_background_guard.py",
    "tests/test_daemon_opencode_backend.py",
    "tests/test_daemon_run_dir.py",
]


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def test_daemon_contract_frontmatter_lists_related_files_and_triggers():
    meta = _frontmatter(DOC)
    assert meta["name"] == "daemon-contract"
    assert meta["status"] == "active"
    related = set(meta["related_files"])
    triggers = set(meta["review_triggers"])
    for rel in REQUIRED_RELATED:
        assert rel in related
    for trigger in REQUIRED_TRIGGERS:
        assert trigger in triggers
    for rel in related:
        assert (ROOT / rel).exists(), rel


def test_daemon_contract_is_linked_from_anatomy_and_manual():
    anatomy = ROOT / "src/lingtai/core/daemon/ANATOMY.md"
    meta = _frontmatter(anatomy)
    assert DOC_REL in meta.get("related_files", [])
    anatomy_text = anatomy.read_text(encoding="utf-8")
    assert "DAEMON_CONTRACT.md" in anatomy_text

    manual = ROOT / "src/lingtai/core/daemon/manual/SKILL.md"
    manual_text = manual.read_text(encoding="utf-8")
    assert DOC_REL in manual_text
    assert "cross-backend daemon task/context/MCP/completion contract" in manual_text


def test_daemon_contract_locks_context_mcp_and_completion_terms():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    required_phrases = [
        "selected skills catalog/path",
        "MCP registrations",
        "daemon_common",
        "final prompt/context",
        "prompt redaction",
        "native MCP config",
        "prompt-catalog-only unsupported backends",
        "finish(status=\"done\")",
        "HTTP remains prompt catalog context",
        "Review Triggers",
        "Acceptance Gate",
    ]
    for phrase in required_phrases:
        assert phrase in text
