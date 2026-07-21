from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "src/lingtai/tools/daemon/CONTRACT.md"
DOC_REL = "src/lingtai/tools/daemon/CONTRACT.md"
REQUIRED_RELATED = [
    "src/lingtai/tools/daemon/ANATOMY.md",
    "src/lingtai/tools/daemon/__init__.py",
    "src/lingtai/tools/daemon/system_prompt.py",
    "src/lingtai/tools/daemon/run_dir.py",
    "src/lingtai/tools/daemon/manual/SKILL.md",
    "src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md",
    "src/lingtai/mcp_servers/daemon_common/server.py",
    "tests/test_daemon.py",
    "tests/test_daemon_backend_options.py",
    "tests/test_daemon_claude_p_background_guard.py",
    "tests/test_daemon_opencode_backend.py",
    "tests/test_daemon_run_dir.py",
]
REQUIRED_TRIGGERS = [
    "src/lingtai/tools/daemon/__init__.py",
    "src/lingtai/tools/daemon/system_prompt.py",
    "src/lingtai/tools/daemon/run_dir.py",
    "src/lingtai/tools/daemon/ANATOMY.md",
    "src/lingtai/tools/daemon/manual/",
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
    assert meta["contract_version"] == 7
    related = set(meta["related_files"])
    triggers = set(meta["review_triggers"])
    for rel in REQUIRED_RELATED:
        assert rel in related
    for trigger in REQUIRED_TRIGGERS:
        assert trigger in triggers
    for rel in related:
        assert (ROOT / rel).exists(), rel


def test_daemon_contract_is_linked_from_anatomy_and_manual():
    anatomy = ROOT / "src/lingtai/tools/daemon/ANATOMY.md"
    meta = _frontmatter(anatomy)
    assert DOC_REL in meta.get("related_files", [])
    anatomy_text = anatomy.read_text(encoding="utf-8")
    assert "daemon/CONTRACT.md" in anatomy_text

    manual = ROOT / "src/lingtai/tools/daemon/manual/SKILL.md"
    manual_text = manual.read_text(encoding="utf-8")
    assert DOC_REL in manual_text
    assert "unified daemon contract" in manual_text


def test_daemon_contract_locks_architecture_capability_terms():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    required_phrases = [
        "Daemon Architecture Capability Contract",
        "not primarily a per-task input contract",
        "architecture capability invariant",
        "selected skills catalog/path",
        "progressive-disclosure catalog",
        "Parent-provided MCP registrations",
        "daemon_common",
        "final prompt/context",
        "redacted for `env` and `headers`",
        "native MCP config",
        "prompt-catalog-only",
        "finish(status=\"done\")",
        "native HTTP mounting is claimed only for backends whose source-proven config schema supports it",
        "Backend Support Matrix",
        "Review Triggers",
        "Acceptance Gate",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_daemon_contract_does_not_claim_unwired_native_mcp_support():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert "| `mimocode` / `mimo` | Yes. | Not wired in this slice; prompt catalog only." in text
    assert "| `oh-my-pi` / `omp` | Yes. | Not verified; prompt catalog only." in text
    assert "| `kimicode` / `kimi` | Yes. | Yes for stdio and HTTP via run-private `$KIMI_CODE_HOME/mcp.json`." in text
    assert "| `cursor` | Yes. | Not verified; prompt catalog only." in text
