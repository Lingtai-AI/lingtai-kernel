"""Focused owner proofs for Email's five-field settings inventory."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lingtai.tools import email as email_tool
from lingtai.tools.email import manager, primitives
from lingtai.tools.email.settings import (
    EMAIL_BODY_CHAR_LIMIT,
    EMAIL_CHECK_RESULT_TOKEN_LIMIT,
    EMAIL_DUPLICATE_FREE_PASSES,
    EMAIL_UNREAD_MAX_ENTRIES,
)


class _Runtime:
    def __init__(self, subscriptions=("/private/applied-subscription",)) -> None:
        self.calls = []
        self.subscriptions = subscriptions

    def handle_email(self, request):
        self.calls.append(request)
        return {"status": "ok", "contacts": []}

    def read_pseudo_agent_subscriptions(self):
        if isinstance(self.subscriptions, Exception):
            raise self.subscriptions
        return self.subscriptions


def _family(runtime: _Runtime):
    host = SimpleNamespace(workdir=Path("unused-manual-workdir"), email_runtime=runtime)
    return email_tool._build_bound_family(host)


def _settings(family, action_input):
    return family.handle(
        {"action": "settings", "input": action_input, "reasoning": "audit"}
    )


def test_declaration_opts_in_immediately_before_manual():
    public = (*email_tool.DECLARATION.actions, "settings", "manual")
    assert email_tool.DECLARATION.settings is True
    assert email_tool.DECLARATION.public_actions == public
    assert tuple(email_tool.get_schema()["properties"]["action"]["enum"]) == public


def test_inventory_is_exact_source_backed_and_fully_redacts_path_lists():
    private_marker = "/private/owner-address-and-content-marker"
    runtime = _Runtime((private_marker,))

    result = _settings(_family(runtime), {})

    assert result == {
        "settings": [
            {
                "key": "send.body_char_limit",
                "current": EMAIL_BODY_CHAR_LIMIT,
                "default": EMAIL_BODY_CHAR_LIMIT,
                "configurable": False,
                "comment": "email-manual#send-body-character-limit",
            },
            {
                "key": "send.duplicate_free_passes",
                "current": EMAIL_DUPLICATE_FREE_PASSES,
                "default": EMAIL_DUPLICATE_FREE_PASSES,
                "configurable": False,
                "comment": "email-manual#duplicate-send-loop-guard",
            },
            {
                "key": "check.result_token_limit",
                "current": EMAIL_CHECK_RESULT_TOKEN_LIMIT,
                "default": EMAIL_CHECK_RESULT_TOKEN_LIMIT,
                "configurable": False,
                "comment": "email-manual#check-result-token-limit",
            },
            {
                "key": "unread.max_entries",
                "current": EMAIL_UNREAD_MAX_ENTRIES,
                "default": EMAIL_UNREAD_MAX_ENTRIES,
                "configurable": False,
                "comment": "email-manual#unread-notification-entry-limit",
            },
            {
                "key": "manifest.pseudo_agent_subscriptions",
                "current": "<redacted>",
                "default": "<redacted>",
                "configurable": True,
                "comment": "email-manual#pseudo-agent-subscriptions",
            },
        ]
    }
    fields = ["key", "current", "default", "configurable", "comment"]
    assert all(list(row) == fields for row in result["settings"])
    assert private_marker not in repr(result)
    assert "../human" not in repr(result)
    assert "_sensitive" not in repr(result)
    assert runtime.calls == []


def test_every_comment_targets_its_exact_manual_heading():
    comments = [row["comment"] for row in _settings(_family(_Runtime()), {})["settings"]]
    manual = (Path(email_tool.__file__).with_name("manual") / "SKILL.md").read_text(
        encoding="utf-8"
    )
    expected = {
        "email-manual#send-body-character-limit": "Send body character limit",
        "email-manual#duplicate-send-loop-guard": "Duplicate send loop guard",
        "email-manual#check-result-token-limit": "Check result token limit",
        "email-manual#unread-notification-entry-limit": (
            "Unread notification entry limit"
        ),
        "email-manual#pseudo-agent-subscriptions": "Pseudo-agent subscriptions",
    }
    assert comments == list(expected)
    for comment, heading in expected.items():
        assert comment.startswith("email-manual#")
        assert f"### {heading}\n" in manual


def test_unavailable_current_fails_whole_inventory_without_private_detail():
    private_detail = "private unavailable-subscription detail"
    runtime = _Runtime(RuntimeError(private_detail))

    result = _settings(_family(runtime), {})

    assert result == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }
    assert "settings" not in result
    assert private_detail not in repr(result)
    assert runtime.calls == []


def test_settings_is_show_only_and_contacts_action_is_unchanged():
    runtime = _Runtime()
    family = _family(runtime)

    refused = _settings(
        family, {"set": "send.body_char_limit", "value": 1}
    )
    assert refused["status"] == "failed"
    assert "settings" not in refused
    assert runtime.calls == []

    ordinary = family.handle(
        {"action": "contacts", "input": {}, "reasoning": "ordinary call"}
    )
    assert ordinary == {"status": "ok", "contacts": []}
    assert len(runtime.calls) == 1
    assert runtime.calls[0].action == "contacts"
    assert dict(runtime.calls[0].input) == {}


def test_posix_source_is_the_applied_constructor_snapshot(tmp_path):
    from lingtai.adapters.posix.mail import PosixFilesystemMailAdapter

    configured = ["../private-pseudo-agent"]
    service = PosixFilesystemMailAdapter(
        tmp_path / "agent", pseudo_agent_subscriptions=configured
    )
    first = service.pseudo_agent_subscriptions
    configured.append("../later-edit")

    assert first == service.pseudo_agent_subscriptions
    assert len(first) == 1
    assert Path(first[0]).is_absolute()


def test_operational_constants_are_the_inventory_constants():
    assert primitives.EMAIL_BODY_CHAR_LIMIT == EMAIL_BODY_CHAR_LIMIT
    assert primitives.EMAIL_UNREAD_MAX_ENTRIES == EMAIL_UNREAD_MAX_ENTRIES
    assert manager.EMAIL_BODY_CHAR_LIMIT == EMAIL_BODY_CHAR_LIMIT
    assert manager.EMAIL_DUPLICATE_FREE_PASSES == EMAIL_DUPLICATE_FREE_PASSES
    assert manager.EMAIL_CHECK_RESULT_TOKEN_LIMIT == EMAIL_CHECK_RESULT_TOKEN_LIMIT

def test_manual_routes_each_show_anchor_without_repeating_action_schema():
    root = Path(email_tool.__file__).with_name('manual')
    text = (root / 'SKILL.md').read_text()
    assert '## Nested reference catalog' in text
    assert '| Action | Input' not in text
    for heading in ('Send body character limit', 'Duplicate send loop guard',
                    'Check result token limit', 'Unread notification entry limit',
                    'Pseudo-agent subscriptions'):
        section = text.split(f'### {heading}\n', 1)[1].split('\n### ', 1)[0]
        assert 'reference/settings-reference/SKILL.md#' in section
    for topic in ('addressing-and-replies', 'actions-and-storage',
                  'notifications-and-delivery', 'settings-reference'):
        assert f'location: reference/{topic}/SKILL.md' in text


def test_manual_preserves_observed_delivery_limits_and_explicit_dismiss():
    root = Path(email_tool.__file__).with_name('manual')
    routing = (root / 'reference/addressing-and-replies/SKILL.md').read_text()
    delivery = (root / 'reference/notifications-and-delivery/SKILL.md').read_text()
    actions = (root / 'reference/actions-and-storage/SKILL.md').read_text()
    settings = (root / 'reference/settings-reference/SKILL.md').read_text()
    assert 'do not mark the source inbox ID read' in routing
    assert 'Contract discrepancy' in routing
    assert '`dismiss`' in routing
    assert 'not a restart-resilient queue' in delivery
    assert 'under two' not in delivery
    assert 'before that call\'s unified sent record' in delivery
    assert 'self-send bypasses this adapter' in actions
    assert 'does not validate or copy attachments' in actions
    assert 'subject, attachment paths, and mode are not compared' in settings
    assert 'in-process `_setup_from_init()`' in settings
