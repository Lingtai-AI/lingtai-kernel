"""Tests for _on_normal_mail notification prose formatting.

Covers lingtai #28: the prose body that ends up inside the synthetic
system(action="notification") ToolResultBlock had two blank fields —
subject (when sender wrote subject="") and sent_at (always, since no
sender populates it). The fix:
  - coerce falsy subject to a localized "(no subject)" placeholder
  - fall back to received_at when sent_at/time are missing
  - run the timestamp through time_veil.veil() for time-blind agents
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import lingtai_kernel.base_agent.messaging as messaging
from lingtai_kernel.base_agent.messaging import _on_normal_mail


def _make_agent(time_awareness=True, lang="en"):
    """Minimal mock agent for _on_normal_mail."""
    agent = MagicMock()
    agent._config = SimpleNamespace(
        language=lang,
        time_awareness=time_awareness,
    )
    agent._mailbox_name = "email box"
    agent._mailbox_tool = "email"
    agent._wake_nap = MagicMock()
    agent._log = MagicMock()
    return agent


def _capture_body(monkeypatch):
    """Patch _enqueue_system_notification and capture the body it receives."""
    captured = {}

    def fake_enqueue(a, *, source, ref_id, body):
        captured["body"] = body
        captured["source"] = source
        captured["ref_id"] = ref_id
        return "notif_x"

    monkeypatch.setattr(messaging, "_enqueue_system_notification", fake_enqueue)
    return captured


def test_notification_uses_received_at_when_sent_at_missing(monkeypatch):
    """The TUI/kernel sender path doesn't populate sent_at, only
    received_at. The notification must still surface a timestamp."""
    agent = _make_agent()
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        "subject": "hello",
        "message": "hi",
        "received_at": "2026-05-04T10:00:00Z",
        # no sent_at, no time
    })

    assert "2026-05-04T10:00:00Z" in captured["body"]


def test_notification_subject_placeholder_when_empty(monkeypatch):
    """An empty-string subject should render as the localized
    '(no subject)' placeholder, not as a bare label with nothing after it.

    .get("subject", default) only fires when the key is missing — but
    TUI/portal senders write subject="" — so the default never applied
    before the fix."""
    agent = _make_agent(lang="en")
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        "subject": "",  # empty, not missing
        "message": "hi",
        "received_at": "2026-05-04T10:00:00Z",
    })

    assert "(no subject)" in captured["body"]


def test_notification_subject_placeholder_when_missing(monkeypatch):
    """Same fallback fires when the subject key is absent entirely
    (defensive — covers external addons that don't include the key)."""
    agent = _make_agent(lang="en")
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        # no subject at all
        "message": "hi",
        "received_at": "2026-05-04T10:00:00Z",
    })

    assert "(no subject)" in captured["body"]


def test_notification_subject_placeholder_localized_zh(monkeypatch):
    """zh locale uses （无主题）."""
    agent = _make_agent(lang="zh")
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        "subject": "",
        "message": "hi",
        "received_at": "2026-05-04T10:00:00Z",
    })

    assert "（无主题）" in captured["body"]


def test_notification_subject_placeholder_localized_wen(monkeypatch):
    """wen (classical) locale uses （无题）."""
    agent = _make_agent(lang="wen")
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        "subject": "",
        "message": "hi",
        "received_at": "2026-05-04T10:00:00Z",
    })

    assert "（无题）" in captured["body"]


def test_notification_timestamp_blank_when_time_blind(monkeypatch):
    """time_awareness=False must blank the timestamp — even when
    received_at is populated, the agent should not see it."""
    agent = _make_agent(time_awareness=False)
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        "subject": "hello",
        "message": "hi",
        "received_at": "2026-05-04T10:00:00Z",
    })

    assert "2026-05-04T10:00:00Z" not in captured["body"]


def test_notification_prefers_sent_at_over_received_at(monkeypatch):
    """If a sender populates both sent_at AND received_at, sent_at wins —
    it represents authorial intent. External addons (IMAP, telegram) may
    rely on this distinction (composed-at vs delivered-at)."""
    agent = _make_agent()
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        "subject": "hello",
        "message": "hi",
        "sent_at": "2026-05-04T09:00:00Z",      # earlier — original send
        "received_at": "2026-05-04T10:00:00Z",  # later — delivery
    })

    assert "2026-05-04T09:00:00Z" in captured["body"]
    assert "2026-05-04T10:00:00Z" not in captured["body"]


def test_notification_prefers_time_over_received_at(monkeypatch):
    """Legacy `time` field beats received_at in the fallback chain."""
    agent = _make_agent()
    captured = _capture_body(monkeypatch)

    _on_normal_mail(agent, {
        "_mailbox_id": "m1",
        "from": "alice",
        "subject": "hello",
        "message": "hi",
        "time": "2026-05-04T08:00:00Z",
        "received_at": "2026-05-04T10:00:00Z",
    })

    assert "2026-05-04T08:00:00Z" in captured["body"]
    assert "2026-05-04T10:00:00Z" not in captured["body"]
