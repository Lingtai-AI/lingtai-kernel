"""Cloud Mail LICC notification carries the routing id it needs, so the
agent is not forced into an extra check/search just to recover a
``compound_id`` already delivered with the current notification.

Cloud Mail has no persistent notification lane (unlike telegram/wechat/
feishu/whatsapp/email): its events go through the generic LICC preview path
in ``lingtai.services.mcp_inbox``, which only lifts the fixed
``_PREVIEW_META_FIELDS`` keys (``conversation_ref``, ``message_ref``,
``platform``, ``event_id``) out of ``event["metadata"]`` into
``data.previews[*]``. Cloud Mail's LICC event must therefore populate those
exact keys — this is a plugin-local fix; ``mcp_inbox.py`` itself is untouched.

No real network: reuses the in-process ``httpx.MockTransport`` router from
``test_cloud_mail_addon.py``.
"""
from __future__ import annotations

from lingtai.mcp_servers.cloud_mail._family import CLOUD_MAIL_SCHEMA
from lingtai.mcp_servers.cloud_mail.manager import DESCRIPTION, CloudMailManager
from lingtai.services.mcp_inbox import _PREVIEW_META_FIELDS, _consume_event, validate_event

from tests.test_cloud_mail_addon import _row, make_router


def _build_one_pushed_event(tmp_path, *, email_id=4, **row_kwargs):
    """Poll a fresh account to seed, add one new row, poll again, capture the
    single pushed LICC event dict (what manager._push_licc hands to
    ``on_inbound``, i.e. exactly what ``push_inbox_event`` would write)."""
    events = []
    rows = [_row(3), _row(2), _row(1)]
    transport, _ = make_router(rows=rows)
    mgr = CloudMailManager(
        accounts=[{
            "alias": "cloudmail",
            "base_url": "https://mail.example.com",
            "admin_email": "admin@example.com",
            "admin_password": "adminpw",
        }],
        working_dir=tmp_path,
        on_inbound=events.append,
        transport=transport,
    )
    acct = mgr.default_account
    mgr.poll_once(acct)  # seed silently at emailId=3
    rows.insert(0, _row(email_id, **row_kwargs))
    pushed = mgr.poll_once(acct)
    mgr.stop()
    assert pushed == 1
    assert len(events) == 1
    return events[0]


def test_licc_event_metadata_uses_generic_routing_keys(tmp_path):
    """The event's metadata must carry the keys mcp_inbox actually extracts."""
    event = _build_one_pushed_event(tmp_path, sender="new@x.com", subject="fresh", text="brand new")
    md = event["metadata"]
    assert md["platform"] == "cloud_mail"
    assert md["conversation_ref"] == "cloudmail"
    assert md["message_ref"] == "cloudmail:4"
    # Legacy/custom keys stay for any other consumer; they are additive, not replaced.
    assert md["compound_id"] == "cloudmail:4"
    assert md["account"] == "cloudmail"
    assert md["email_id"] == 4


def test_licc_event_is_schema_valid(tmp_path):
    event = _build_one_pushed_event(tmp_path)
    ok, err = validate_event(event)
    assert ok, err


def test_generic_preview_surfaces_exact_read_id_without_reread(tmp_path):
    """End-to-end through the real mcp_inbox extractor: the id needed for a
    later ``read``/reply is present in ``data.previews[*]`` on the very
    notification that announces the mail, not only recoverable via a fresh
    ``check``/``search`` call."""
    event = _build_one_pushed_event(tmp_path, sender="new@x.com", subject="fresh", text="brand new")

    class _StubAgent:
        def _log(self, *args, **kwargs):
            pass

    wake, preview = _consume_event(_StubAgent(), "cloud_mail", event)
    assert wake is True
    assert preview["from"] == "new@x.com"
    assert preview["subject"] == "fresh"
    assert preview["preview"] == "brand new"
    assert preview["preview_truncated"] is False
    # These are exactly the generic keys mcp_inbox is willing to surface;
    # cloud_mail's metadata must match that vocabulary to be seen at all.
    assert set(_PREVIEW_META_FIELDS) & preview.keys() == {"conversation_ref", "message_ref", "platform"}
    assert preview["message_ref"] == "cloudmail:4"
    assert preview["conversation_ref"] == "cloudmail"
    assert preview["platform"] == "cloud_mail"


def test_full_short_body_is_not_marked_truncated(tmp_path):
    """A short body that fits entirely in the preview cap must not be
    reported as truncated — a full current body is not 'missing' content,
    so no forced reread is implied for it."""
    event = _build_one_pushed_event(tmp_path, text="short and complete")

    class _StubAgent:
        def _log(self, *args, **kwargs):
            pass

    _, preview = _consume_event(_StubAgent(), "cloud_mail", event)
    assert preview["preview"] == "short and complete"
    assert preview["preview_truncated"] is False


def test_root_descriptor_reinforces_no_reread_and_message_ref():
    """Both advertised descriptions teach conditional no-reread and recovery,
    not the false claim that every bounded preview contains the full body."""
    action_description = CLOUD_MAIL_SCHEMA["properties"]["action"]["description"]
    for description in (DESCRIPTION, action_description):
        assert "message_ref" in description
        assert "complete" in description
        assert "do not call check, search, or read" in description
        assert "truncated or missing" in description
        assert "with its full body" not in description


def test_oversized_body_is_marked_truncated(tmp_path):
    """A body longer than the shared LICC preview cap must be flagged
    truncated so the agent knows a full ``read`` recovery is warranted."""
    long_text = "x" * 20_000
    event = _build_one_pushed_event(tmp_path, text=long_text)

    class _StubAgent:
        def _log(self, *args, **kwargs):
            pass

    _, preview = _consume_event(_StubAgent(), "cloud_mail", event)
    assert preview["preview_truncated"] is True
    assert len(preview["preview"]) < len(long_text)
