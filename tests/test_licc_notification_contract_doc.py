from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "src/lingtai/core/mcp/LICC_NOTIFICATION_CONTRACT.md"
DOC_REL = "src/lingtai/core/mcp/LICC_NOTIFICATION_CONTRACT.md"
REQUIRED_ANATOMIES = [
    "src/lingtai_kernel/ANATOMY.md",
    "src/lingtai_kernel/base_agent/ANATOMY.md",
    "src/lingtai_kernel/intrinsics/notification/ANATOMY.md",
    "src/lingtai/core/mcp/ANATOMY.md",
    "src/lingtai/mcp_servers/ANATOMY.md",
]
REQUIRED_TRIGGERS = [
    "src/lingtai/core/mcp/inbox.py",
    "src/lingtai/core/mcp/licc.py",
    "src/lingtai_kernel/meta_block.py",
    "src/lingtai_kernel/notifications.py",
    "src/lingtai_kernel/base_agent/__init__.py",
    "src/lingtai_kernel/base_agent/turn.py",
    "src/lingtai_kernel/base_agent/messaging.py",
    "src/lingtai/mcp_servers/telegram/manager.py",
    "src/lingtai/mcp_servers/feishu/manager.py",
    "src/lingtai/mcp_servers/wechat/manager.py",
    "src/lingtai/mcp_servers/whatsapp/manager.py",
    "src/lingtai_kernel/intrinsics/email/",
    "src/lingtai_kernel/intrinsics/notification/",
]


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML frontmatter"
    _blank, frontmatter, _body = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def test_licc_notification_contract_frontmatter_lists_review_triggers():
    meta = _frontmatter(DOC)
    assert meta["name"] == "licc-notification-contract"
    assert meta["status"] == "active"
    related = set(meta["related_files"])
    triggers = set(meta["review_triggers"])
    for anatomy in REQUIRED_ANATOMIES:
        assert anatomy in related
    for trigger in REQUIRED_TRIGGERS:
        assert trigger in triggers
    for rel in related:
        assert (ROOT / rel).exists(), rel


def test_licc_notification_contract_is_linked_from_relevant_anatomies():
    for rel in REQUIRED_ANATOMIES:
        path = ROOT / rel
        meta = _frontmatter(path)
        assert DOC_REL in meta.get("related_files", []), rel
        body = path.read_text(encoding="utf-8")
        assert "LICC_NOTIFICATION_CONTRACT.md" in body, rel
