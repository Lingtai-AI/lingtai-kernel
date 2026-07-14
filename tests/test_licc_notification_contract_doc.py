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
        "_meta.notifications.mcp.<channel>",
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


def test_licc_notification_contract_meta_block_citations_are_accurate():
    """Ownership/citation validator: the doc names real functions AND its cited
    ``meta_block.py:LINE`` references must point at that function's actual
    ``def`` line — not merely that the Markdown text contains a plausible
    string. Catches drift like the stale 2589-2655/2939/1857-2489 citations
    this test replaces.
    """
    import inspect
    import re

    from lingtai.kernel import meta_block

    text = DOC.read_text(encoding="utf-8")

    cited_functions = {
        "attach_active_notifications": meta_block.attach_active_notifications,
        "_sanitize_im_notification_after_persistent": (
            meta_block._sanitize_im_notification_after_persistent
        ),
        "build_notification_persistent_payload": (
            meta_block.build_notification_persistent_payload
        ),
        "_build_email_notification_persistent_payload": (
            meta_block._build_email_notification_persistent_payload
        ),
    }

    for name, func in cited_functions.items():
        assert callable(func), f"{name} is not callable on lingtai.kernel.meta_block"
        real_line = inspect.getsourcelines(func)[1]

        # The doc cites each function once, at its first/canonical mention,
        # then refers to it by bare name in later prose describing other code
        # paths that interact with it (e.g. the context-molt bypass, startup
        # reconciliation reuse). Only the canonical citation site is checked;
        # later bare mentions are not fresh ownership claims to re-cite.
        name_match = re.search(re.escape(f"`{name}`"), text)
        assert name_match is not None, f"`{name}` not mentioned in {DOC_REL}"
        window = text[name_match.end(): name_match.end() + 800]
        cite_match = re.search(r"meta_block\.py:(\d+)(?:-(\d+))?", window)
        assert cite_match is not None, (
            f"no meta_block.py:LINE citation found near first `{name}` mention in {DOC_REL}"
        )
        cited_start = int(cite_match.group(1))
        cited_end = int(cite_match.group(2)) if cite_match.group(2) else cited_start
        assert cited_start <= real_line <= cited_end, (
            f"{DOC_REL} cites {name} at meta_block.py:{cite_match.group(0).split(':')[1]}, "
            f"but its real def line is {real_line}"
        )
