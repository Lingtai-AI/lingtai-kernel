from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md"
DOC_REL = "src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md"
REQUIRED_ANATOMIES = [
    "src/lingtai/kernel/ANATOMY.md",
    "src/lingtai/kernel/base_agent/ANATOMY.md",
    "src/lingtai/tools/notification/ANATOMY.md",
    "src/lingtai/tools/mcp/ANATOMY.md",
    "src/lingtai/mcp_servers/ANATOMY.md",
]
REQUIRED_TRIGGERS = [
    "src/lingtai/services/mcp_inbox.py",
    "src/lingtai/services/mcp_licc.py",
    "src/lingtai/kernel/meta_block.py",
    "src/lingtai/kernel/notifications.py",
    "src/lingtai/kernel/base_agent/__init__.py",
    "src/lingtai/kernel/base_agent/turn.py",
    "src/lingtai/kernel/base_agent/messaging.py",
    "src/lingtai/mcp_servers/telegram/manager.py",
    "src/lingtai/mcp_servers/feishu/manager.py",
    "src/lingtai/mcp_servers/wechat/manager.py",
    "src/lingtai/mcp_servers/whatsapp/manager.py",
    "src/lingtai/tools/email/",
    "src/lingtai/tools/notification/",
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


def test_licc_notification_contract_locks_new_channel_two_lane_gate():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    required_phrases = [
        "New human-message LICC channel acceptance gate",
        "_meta.agent_meta.notifications.attention.mcp.<channel>",
        "_meta.notification_persistent",
        "data.message_ids",
        "transient hook is identity-only",
        "content/context lands in the persistent lane",
        "producer tool/store",
        "bounded previews may remain",
        "two-lane contract is not active yet",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_licc_notification_contract_locks_telegram_taskcard_message_field():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    for phrase in (
        "explicit current-agent boolean `taskcard`",
        "`recent_messages`, `latest_incoming`, and `referenced_messages`",
        "not that automatic or programmable mechanics have stopped",
        "transient identity hook",
    ):
        assert phrase in text
