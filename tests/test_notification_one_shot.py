"""One-shot notification delivery.

The approved contract: the same notification event is attached automatically
once (through either the ACTIVE tool-result path or the IDLE/ASLEEP synthesized
pair, which share one delivered identity); a new or changed event is attached
and wakes again; delivery never clears notification files or producer state;
unsuccessful / no-carrier / unstable delivery never counts as delivered; and an
explicit ``notification(action="check")`` stays a deliberate full read.

Delivery code under test: ``kernel/meta_block.py`` (``attach_active_notifications``,
``pending_notification_payloads``, ``commit_delivered_notification_sources``) and
``BaseAgent._sync_notifications``.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import lingtai.kernel.notifications as notifications_module
from lingtai.kernel.llm.interface import ToolResultBlock
from lingtai.kernel.meta_block import (
    attach_active_notifications,
    reset_delivered_notification_sources,
    _notification_records,
    _pending_record_payload,
)
from tests._notification_store_helpers import (
    fingerprint_notifications,
    publish_test_payload,
)
from tests.test_meta_block import (
    _notif_agent,
    _telegram_message,
    _write_email_notif,
    _write_telegram_notif,
)
from tests.test_notification_sync import _make_stub_agent_for_block_log


def _block(tool_id: str, **content) -> ToolResultBlock:
    block = ToolResultBlock(id=tool_id, name="x", content=content or {"ok": True})
    # Model the immutable correlation stamp produced by the real ToolExecutor.
    block.metadata["tool_meta"] = {"id": tool_id}
    return block


def _attention(block: ToolResultBlock) -> dict:
    return block.metadata["agent_meta"]["notifications"]["attention"]


def _has_notifications(block: ToolResultBlock) -> bool:
    return "notifications" in (block.metadata.get("agent_meta") or {})


def _system_payload(*event_ids: str) -> dict:
    return {
        "header": f"{len(event_ids)} system notifications",
        "data": {
            "events": [
                {"event_id": event_id, "source": "daemon", "ref_id": event_id, "body": event_id}
                for event_id in event_ids
            ]
        },
    }


# ---------------------------------------------------------------------------
# ACTIVE path: attach once, deliver changes, never touch producer bytes/history.
# ---------------------------------------------------------------------------


def test_unchanged_event_attaches_once_across_batches_and_history_is_intact(
    tmp_path: Path,
) -> None:
    _write_email_notif(tmp_path)
    email_path = tmp_path / ".notification" / "email.json"
    producer_bytes = email_path.read_bytes()
    agent = _notif_agent(tmp_path)

    first = _block("t1")
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert holder is first and _has_notifications(first)
    first_snapshot = copy.deepcopy(first.metadata)

    for index in range(2, 6):
        later = _block(f"t{index}")
        assert attach_active_notifications(agent, [later], prior_holder=holder) is holder
        # Neither the attention hook nor the persistent body nor the guidance.
        assert not _has_notifications(later)
        assert "guidance" not in later.metadata.get("agent_meta", {})

    # Old canonical history is untouched; producer bytes and the mirror survive.
    assert first.metadata == first_snapshot
    assert email_path.read_bytes() == producer_bytes


def test_changed_event_is_delivered_and_new_channel_does_not_repeat_old_one(
    tmp_path: Path,
) -> None:
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)
    first = _block("t1")
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert set(_attention(first)) == {"email"}

    publish_test_payload(tmp_path, "cron", {"header": "new cron event"})
    second = _block("t2")
    holder = attach_active_notifications(agent, [second], prior_holder=holder)

    assert holder is second
    assert set(_attention(second)) == {"cron"}
    assert "email" not in second.metadata["agent_meta"]["notifications"].get("persistent", {})
    assert second.metadata["agent_meta"]["guidance"]["transient"]["sources"] == ["cron"]

    # A changed email event is delivered again with its new body.
    _write_email_notif(tmp_path, message="second body", email_id="email-2", count=2)
    third = _block("t3")
    assert attach_active_notifications(agent, [third], prior_holder=holder) is third
    assert set(_attention(third)) == {"email"}
    assert third.metadata["agent_meta"]["notifications"]["persistent"]["email"]["emails"][0][
        "message"
    ] == "second body"


def test_system_aggregate_new_event_is_not_starved_by_old_and_old_is_not_repeated(
    tmp_path: Path,
) -> None:
    publish_test_payload(tmp_path, "system", _system_payload("evt_a"))
    agent = _notif_agent(tmp_path)
    first = _block("t1")
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert [e["event_id"] for e in _attention(first)["system"]["data"]["events"]] == ["evt_a"]

    # Unchanged aggregate: nothing.
    quiet = _block("t2")
    assert attach_active_notifications(agent, [quiet], prior_holder=holder) is holder
    assert not _has_notifications(quiet)

    # New event shares the aggregate with the old one: only the new one attaches.
    publish_test_payload(tmp_path, "system", _system_payload("evt_a", "evt_b"))
    third = _block("t3")
    assert attach_active_notifications(agent, [third], prior_holder=holder) is third
    assert [e["event_id"] for e in _attention(third)["system"]["data"]["events"]] == ["evt_b"]

    # Removing an event (producer-side) is not a new event.
    publish_test_payload(tmp_path, "system", _system_payload("evt_b"))
    fourth = _block("t4")
    assert attach_active_notifications(agent, [fourth], prior_holder=third) is third
    assert not _has_notifications(fourth)


def test_explicit_check_returns_current_mirrors_even_after_automatic_delivery(
    tmp_path: Path,
) -> None:
    _write_email_notif(tmp_path)
    producer_bytes = (tmp_path / ".notification" / "email.json").read_bytes()
    agent = _notif_agent(tmp_path)
    holder = attach_active_notifications(agent, [_block("t1")], prior_holder=None)
    assert attach_active_notifications(agent, [_block("t2")], prior_holder=holder) is holder

    check = ToolResultBlock(
        id="t3",
        name="notification",
        content={"_notification_placeholder": True, "message": "voluntary check"},
    )
    assert attach_active_notifications(agent, [check], prior_holder=holder) is check
    assert _attention(check)["email"]["data"] == {"email_ids": ["email-1"]}
    assert check.metadata["agent_meta"]["notifications"]["persistent"]["email"]["emails"][0][
        "message"
    ] == "Full email body"
    # Reading never clears anything and is not an automatic replay afterwards.
    assert (tmp_path / ".notification" / "email.json").read_bytes() == producer_bytes
    after = _block("t4")
    assert attach_active_notifications(agent, [after], prior_holder=check) is check
    assert not _has_notifications(after)


def test_multi_call_batch_delivers_only_to_final_carrier_once(tmp_path: Path) -> None:
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    earlier, middle, final = _block("t1"), _block("t2"), _block("t3")
    holder = attach_active_notifications(agent, [earlier, middle, final], prior_holder=None)
    assert holder is final
    assert not _has_notifications(earlier) and not _has_notifications(middle)
    assert _has_notifications(final)

    again = [_block("t4"), _block("t5"), _block("t6")]
    assert attach_active_notifications(agent, again, prior_holder=holder) is holder
    assert not any(_has_notifications(block) for block in again)


# ---------------------------------------------------------------------------
# Unsuccessful delivery never counts as delivered.
# ---------------------------------------------------------------------------


def test_no_final_carrier_does_not_mark_delivered(tmp_path: Path) -> None:
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    assert attach_active_notifications(agent, [], prior_holder=None) is None
    assert not getattr(agent, "_notification_delivered_source_signatures", {})
    assert agent._notification_fp == ()

    carrier = _block("t1")
    assert attach_active_notifications(agent, [carrier], prior_holder=None) is carrier
    assert _has_notifications(carrier)


def test_unstable_read_does_not_mark_delivered(tmp_path: Path, monkeypatch) -> None:
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)
    real_read = notifications_module.coherent_attention_read

    def unstable(store, allow, workdir):
        return real_read(store, allow, workdir)._replace(stable=False)

    monkeypatch.setattr(notifications_module, "coherent_attention_read", unstable)
    skipped = _block("t1")
    prior = _block("prior")
    assert attach_active_notifications(agent, [skipped], prior_holder=prior) is prior
    assert not _has_notifications(skipped)
    assert not getattr(agent, "_notification_delivered_source_signatures", {})
    assert agent._notification_fp == ()

    monkeypatch.setattr(notifications_module, "coherent_attention_read", real_read)
    delivered = _block("t2")
    assert attach_active_notifications(agent, [delivered], prior_holder=prior) is delivered
    assert _has_notifications(delivered)


def test_concurrent_publication_is_not_marked_delivered_by_a_later_reread(
    tmp_path: Path, monkeypatch
) -> None:
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)
    real_read = notifications_module.coherent_attention_read
    raced = {"done": False}

    def racing(store, allow, workdir):
        observation = real_read(store, allow, workdir)
        if not raced["done"]:
            raced["done"] = True
            publish_test_payload(tmp_path, "cron", {"header": "published mid-delivery"})
        return observation

    monkeypatch.setattr(notifications_module, "coherent_attention_read", racing)
    first = _block("t1")
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert set(_attention(first)) == {"email"}
    # The committed fingerprint describes what was delivered, not the later write.
    assert agent._notification_fp != fingerprint_notifications(tmp_path)

    monkeypatch.setattr(notifications_module, "coherent_attention_read", real_read)
    second = _block("t2")
    assert attach_active_notifications(agent, [second], prior_holder=holder) is second
    assert set(_attention(second)) == {"cron"}


def test_consumer_delay_suppresses_then_expiry_delivers_once(
    tmp_path: Path, monkeypatch
) -> None:
    _write_email_notif(tmp_path)
    publish_test_payload(tmp_path, "cron", {"header": "cron"})
    agent = _notif_agent(tmp_path)

    monkeypatch.setattr(
        notifications_module, "delayed_notification_target", lambda workdir, store: "email"
    )
    first = _block("t1")
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert set(_attention(first)) == {"cron"}

    monkeypatch.setattr(
        notifications_module, "delayed_notification_target", lambda workdir, store: None
    )
    second = _block("t2")
    holder = attach_active_notifications(agent, [second], prior_holder=holder)
    assert set(_attention(second)) == {"email"}

    third = _block("t3")
    assert attach_active_notifications(agent, [third], prior_holder=holder) is holder
    assert not _has_notifications(third)


@pytest.mark.parametrize("initiator", ["agent", "system"])
@pytest.mark.parametrize("delivered_before", [True, False])
def test_real_molt_retains_delivery_identity_and_retries_undelivered(
    tmp_path: Path, initiator: str, delivered_before: bool,
) -> None:
    from tests.test_post_molt_notification import (
        _make_agent_with_context, _setup_mock_chat, _build_molt_call_entry,
    )
    from tests._molt_helpers import write_session_journal
    from lingtai.tools.context._molt import _context_molt, context_forget

    agent = _make_agent_with_context(tmp_path)
    # Exercise the real lifecycle engines on a constructed Agent/session,
    # without a concurrent heartbeat/run loop racing automatic delivery.
    try:
        workdir = agent._working_dir
        _write_email_notif(workdir)
        producer = (workdir / ".notification/email.json").read_bytes()
        if delivered_before:
            first = _block("before-molt")
            attach_active_notifications(agent, [first])
            history = copy.deepcopy(first.metadata)
        else:
            # A real no-carrier attempt must remain pending across molt.
            attach_active_notifications(agent, [])
        interface = _setup_mock_chat(agent)
        if initiator == "agent":
            _build_molt_call_entry(interface, "molt-call", "retain task facts")
            result = _context_molt(agent, {
                "summary": "retain task facts", "_tc_id": "molt-call",
                "session_journal_path": write_session_journal(agent),
            })
        else:
            result = context_forget(agent, source="warning_ladder")
        assert result["status"] == "ok"
        after = _block("after-molt")
        holder = attach_active_notifications(agent, [after])
        assert "post-molt" in _attention(after)
        assert ("email" in _attention(after)) is (not delivered_before)
        assert (workdir / ".notification/email.json").read_bytes() == producer
        if delivered_before:
            assert first.metadata == history
        quiet = _block("quiet-after-molt")
        assert attach_active_notifications(agent, [quiet], prior_holder=holder) is holder
        assert not _has_notifications(quiet)
        # New producer state after molt remains deliverable, old mail does not replay.
        from tests.test_notification_persistent_cap import _email, _email_payload
        old = json.loads(producer)["data"]["emails"][0]
        new = _email(2, message="new after molt")
        publish_test_payload(workdir, "email", _email_payload([old, new])["notifications"]["email"])
        changed = _block("new-after-molt")
        attach_active_notifications(agent, [changed], prior_holder=holder)
        assert [m["id"] for m in changed.metadata["agent_meta"]["notifications"]["persistent"]["email"]["emails"]] == [new["id"]]
        assert set(_attention(changed)) == {"email"}
    finally:
        agent.stop()


# ---------------------------------------------------------------------------
# IM lane: body/routing intact on first delivery, never repeated, delta on new.
# ---------------------------------------------------------------------------


def test_telegram_body_and_routing_delivered_once_then_only_new_message(
    tmp_path: Path,
) -> None:
    messages = [_telegram_message(i) for i in range(1, 21)]
    _write_telegram_notif(tmp_path, messages)
    agent = _notif_agent(tmp_path)

    first = _block("t1")
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    telegram = first.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    assert [m["id"] for m in telegram["messages"]][-1] == "main:123:20"
    assert telegram["messages"][-1]["text"] == "message 20"
    assert telegram["events"][0]["conversation_ref"] == "main:123"
    assert telegram["events"][0]["message_ref"] == "main:123:20"
    assert _attention(first)["mcp.telegram"]["data"] == {"message_ids": ["main:123:20"]}

    repeat = _block("t2")
    assert attach_active_notifications(agent, [repeat], prior_holder=holder) is holder
    assert not _has_notifications(repeat)

    _write_telegram_notif(tmp_path, [_telegram_message(i) for i in range(2, 22)])
    new = _block("t3")
    assert attach_active_notifications(agent, [new], prior_holder=holder) is new
    delta = new.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    assert [m["id"] for m in delta["messages"]] == ["main:123:21"]
    assert delta["events"][0]["message_ref"] == "main:123:21"
    # The earlier delivered body is still in the first holder, unmodified.
    assert first.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"][
        "messages"
    ][-1]["text"] == "message 20"


# ---------------------------------------------------------------------------
# IDLE / ACTIVE transitions share one delivered identity (no double delivery).
# ---------------------------------------------------------------------------


def test_idle_delivery_is_not_repeated_by_next_active_batch(tmp_path: Path) -> None:
    agent = _make_stub_agent_for_block_log(tmp_path)
    publish_test_payload(tmp_path, "email", {"count": 1, "data": {"count": 1}})
    producer_bytes = (tmp_path / ".notification" / "email.json").read_bytes()

    agent._sync_notifications()
    entries = agent._chat_stub.interface.entries
    assert len(entries) == 2
    pair_snapshot = copy.deepcopy(entries[1].content[0].metadata)
    pair = agent._notification_live_holder

    later = _block("t1")
    assert attach_active_notifications(agent, [later], prior_holder=pair) is pair
    assert not _has_notifications(later)
    assert len(agent._chat_stub.interface.entries) == 2
    assert entries[1].content[0].metadata == pair_snapshot
    assert (tmp_path / ".notification" / "email.json").read_bytes() == producer_bytes

    # A genuinely new event still reaches the next ACTIVE batch.
    publish_test_payload(tmp_path, "cron", {"header": "new"})
    fresh = _block("t2")
    assert attach_active_notifications(agent, [fresh], prior_holder=pair) is fresh
    assert set(_attention(fresh)) == {"cron"}


def test_active_delivery_is_not_repeated_by_next_idle_sync(tmp_path: Path) -> None:
    agent = _make_stub_agent_for_block_log(tmp_path)
    publish_test_payload(tmp_path, "email", {"count": 1, "data": {"count": 1}})

    carrier = _block("t1")
    attach_active_notifications(agent, [carrier], prior_holder=None)
    assert _has_notifications(carrier)

    agent._sync_notifications()
    assert len(agent._chat_stub.interface.entries) == 0

    # Even with the wake fingerprint forgotten, the shared delivered identity
    # stops a second automatic delivery and re-commits the fingerprint.
    agent._notification_fp = ()
    agent._sync_notifications()
    assert len(agent._chat_stub.interface.entries) == 0
    assert agent.inbox.empty()
    assert agent._notification_fp == fingerprint_notifications(tmp_path)

    # A new event still wakes and injects exactly the new channel.
    publish_test_payload(tmp_path, "cron", {"header": "new"})
    agent._sync_notifications()
    entries = agent._chat_stub.interface.entries
    assert len(entries) == 2
    assert set(entries[1].content[0].metadata["agent_meta"]["notifications"]["attention"]) == {
        "cron"
    }
    assert not agent.inbox.empty()


def test_idle_injection_failure_does_not_ack_and_is_retried(tmp_path: Path) -> None:
    agent = _make_stub_agent_for_block_log(tmp_path)
    publish_test_payload(tmp_path, "email", {"count": 1, "data": {"count": 1}})
    agent._inject_notification_pair = lambda notifications: False
    agent._heal_pending_tool_calls = lambda **kwargs: False

    agent._sync_notifications()

    assert len(agent._chat_stub.interface.entries) == 0
    assert not getattr(agent, "_notification_delivered_source_signatures", {})
    assert agent._notification_fp == ()

    del agent._inject_notification_pair
    agent._sync_notifications()
    assert len(agent._chat_stub.interface.entries) == 2
    assert agent._notification_delivered_source_signatures


def test_idle_sync_delivers_only_changed_channels(tmp_path: Path) -> None:
    agent = _make_stub_agent_for_block_log(tmp_path)
    publish_test_payload(tmp_path, "email", {"count": 1, "data": {"count": 1}})
    agent._sync_notifications()
    publish_test_payload(tmp_path, "system", _system_payload("evt_a"))
    agent._sync_notifications()
    publish_test_payload(tmp_path, "system", _system_payload("evt_a", "evt_b"))
    agent._sync_notifications()

    entries = agent._chat_stub.interface.entries
    assert len(entries) == 6
    delivered = [
        entries[index].content[0].metadata["agent_meta"]["notifications"]["attention"]
        for index in (1, 3, 5)
    ]
    assert [set(item) for item in delivered] == [{"email"}, {"system"}, {"system"}]
    assert [e["event_id"] for e in delivered[2]["system"]["data"]["events"]] == ["evt_b"]


def test_delivery_never_mutates_producer_files(tmp_path: Path) -> None:
    agent = _make_stub_agent_for_block_log(tmp_path)
    publish_test_payload(tmp_path, "email", {"count": 1, "data": {"count": 1}})
    publish_test_payload(tmp_path, "system", _system_payload("evt_a"))
    notification_dir = tmp_path / ".notification"
    def _bytes() -> dict[str, bytes]:
        return {
            path.name: path.read_bytes()
            for path in sorted(notification_dir.iterdir())
            if path.is_file() and path.suffix == ".json" and not path.name.startswith(".")
        }

    before = _bytes()
    assert {"email.json", "system.json"} <= set(before)

    agent._sync_notifications()
    attach_active_notifications(agent, [_block("t1")], prior_holder=None)
    publish_test_payload(tmp_path, "cron", {"header": "x"})
    before["cron.json"] = (notification_dir / "cron.json").read_bytes()
    agent._sync_notifications()

    after = _bytes()
    assert {name: after[name] for name in before} == before


def test_reset_helper_is_idempotent_on_partial_doubles(tmp_path: Path) -> None:
    agent = _notif_agent(tmp_path)
    reset_delivered_notification_sources(agent)
    reset_delivered_notification_sources(agent)
    assert agent._notification_delivered_source_signatures == {}
    assert agent._notification_delivered_system_events == {}


# Native producer projections, not whole-channel hashes or synthetic-only schemas.

def test_native_email_aggregate_delivers_only_new_then_check_reads_full(tmp_path):
    from tests.test_messaging_notification_format import _persist_inbox
    from lingtai.tools.email.primitives import _rerender_unread_digest

    agent = _notif_agent(tmp_path)
    agent._config = SimpleNamespace(language="en", time_awareness=False, timezone_awareness=False)
    agent._log = lambda *args, **kwargs: None
    old_id = _persist_inbox(tmp_path, {"from": "human", "to": ["test"], "subject": "first", "message": "old full body", "received_at": "2026-08-10T07:00:00Z"})
    _rerender_unread_digest(agent)
    first = _block("native-email-1")
    holder = attach_active_notifications(agent, [first])
    historical = copy.deepcopy(first.metadata)
    old_source = (tmp_path / "mailbox/inbox" / old_id / "message.json").read_bytes()
    new_id = _persist_inbox(tmp_path, {"from": "second-human", "to": ["test"], "subject": "second", "message": "new full body", "received_at": "2026-08-10T08:00:00Z"})
    _rerender_unread_digest(agent)
    producer = (tmp_path / ".notification/email.json").read_bytes()
    second = _block("native-email-2")
    holder = attach_active_notifications(agent, [second], prior_holder=holder)
    emails = second.metadata["agent_meta"]["notifications"]["persistent"]["email"]["emails"]
    assert [mail["id"] for mail in emails] == [new_id]
    assert emails[0]["from"] == "second-human"
    assert emails[0]["to"] == ["test"]
    assert emails[0]["message"] == "new full body"
    assert _attention(second)["email"]["data"] == {"email_ids": [new_id]}
    assert first.metadata == historical
    assert (tmp_path / ".notification/email.json").read_bytes() == producer
    assert (tmp_path / "mailbox/inbox" / old_id / "message.json").read_bytes() == old_source
    # Deliberate mirror reread includes both, not just the automatic delta.
    check = _block("native-email-check", _notification_placeholder=True)
    attach_active_notifications(agent, [check], prior_holder=holder)
    assert set(check.metadata["agent_meta"]["notifications"]["persistent"]["email"]["email_ids"]) == {old_id, new_id}
    assert (tmp_path / ".notification/email.json").read_bytes() == producer
    quiet = _block("native-email-quiet")
    attach_active_notifications(agent, [quiet], prior_holder=check)
    assert not _has_notifications(quiet)


def test_native_daemon_aggregate_old_and_new_across_active_idle(tmp_path):
    from lingtai.kernel.base_agent.messaging import _enqueue_system_notification

    agent = _make_stub_agent_for_block_log(tmp_path)
    def publish(run):
        return _enqueue_system_notification(agent, source="daemon", ref_id=run, body=f"{run} done", channel="daemon", extra={"kind": "daemon_terminal", "status": "done"})
    old_id = publish("em-old")
    first = _block("daemon-active")
    attach_active_notifications(agent, [first])
    historical = copy.deepcopy(first.metadata)
    new_id = publish("em-new")
    files = {p.name: p.read_bytes() for p in (tmp_path / ".notification/daemon").glob("*.json")}
    agent._sync_notifications()
    entries = agent._chat_stub.interface.entries
    assert len(entries) == 2
    events = entries[-1].content[0].metadata["agent_meta"]["notifications"]["attention"]["daemon"]["data"]["events"]
    assert [event["event_id"] for event in events] == [new_id]
    assert events[0]["ref_id"] == "em-new"
    assert events[0]["body"] == "em-new done"
    assert first.metadata == historical
    assert old_id != new_id
    assert {p.name: p.read_bytes() for p in (tmp_path / ".notification/daemon").glob("*.json")} == files
    later = _block("daemon-next-active")
    attach_active_notifications(agent, [later], prior_holder=agent._notification_live_holder)
    assert not _has_notifications(later)
    assert len(entries) == 2


def test_native_telegram_current_churn_delta_and_referenced_reply(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from lingtai.mcp_servers.telegram.manager import TelegramManager

    manager = TelegramManager.__new__(TelegramManager)
    monkeypatch.setattr(manager, "_taskcard_enabled", lambda: False)
    now = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)
    old = {"id": "main:123:1", "from": {"first_name": "Jason"}, "text": "old full text", "date": "2026-08-10T08:00:00Z"}
    new = {"id": "main:123:2", "from": {"first_name": "Jason"}, "text": "new reply", "date": "2026-08-10T09:00:00Z", "reply_to_message_id": 1}
    def payload(raw, current, when):
        records = [manager._structured_message(message, current_compound_id=current, now=when) for message in raw]
        return {"data": {"count": 1, "previews": [{"from": "Jason", "platform": "telegram", "conversation_ref": "main:123", "message_ref": current, "recent_messages": records, "latest_incoming": records[-1]}]}}
    agent = _notif_agent(tmp_path)
    first_raw = payload([old], old["id"], now)
    publish_test_payload(tmp_path, "mcp.telegram", first_raw)
    first = _block("native-tg-1")
    holder = attach_active_notifications(agent, [first])
    historical = copy.deepcopy(first.metadata)
    # Native relative ages and derived current flags change without new content.
    churn = payload([old], "main:123:other", now + timedelta(minutes=10))
    churn["data"]["count"] = 99
    churn["data"]["cursor"] = 1234
    publish_test_payload(tmp_path, "mcp.telegram", churn)
    quiet = _block("native-tg-churn")
    assert attach_active_notifications(agent, [quiet], prior_holder=holder) is holder
    assert not _has_notifications(quiet)
    aggregate = payload([old, new], new["id"], now + timedelta(minutes=11))
    # A reply target already delivered is context, and must survive projection.
    aggregate["data"]["previews"][0]["referenced_messages"] = [manager._structured_message(old, now=now)]
    publish_test_payload(tmp_path, "mcp.telegram", aggregate)
    producer = (tmp_path / ".notification/mcp.telegram.json").read_bytes()
    second = _block("native-tg-2")
    attach_active_notifications(agent, [second], prior_holder=holder)
    lane = second.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    assert [message["id"] for message in lane["messages"]] == [new["id"]]
    assert lane["messages"][0]["reply_to"] == old["id"]
    assert lane["messages"][0]["sender"] == "Jason"
    assert lane["referenced_messages"][0]["text"] == "old full text"
    assert lane["events"][0]["conversation_ref"] == "main:123"
    assert lane["events"][0]["message_ref"] == new["id"]
    assert _attention(second)["mcp.telegram"]["data"] == {"message_ids": [new["id"]]}
    assert (tmp_path / ".notification/mcp.telegram.json").read_bytes() == producer
    assert first.metadata == historical
    # Same-ID content edits remain material even with provider-context ID caches.
    new["text"] = "edited new reply"
    publish_test_payload(tmp_path, "mcp.telegram", payload([old, new], new["id"], now))
    edited = _block("native-tg-edit")
    attach_active_notifications(agent, [edited], prior_holder=second)
    assert edited.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]["messages"][0]["text"] == "edited new reply"


def test_native_whatsapp_latest_to_history_does_not_repeat_old_message(tmp_path, monkeypatch):
    from lingtai.mcp_servers.whatsapp.manager import WhatsAppManager

    manager = WhatsAppManager.__new__(WhatsAppManager)
    old = {"id": "wa-old", "from": "15550001", "direction": "inbox", "body": "old whatsapp", "timestamp": 1, "type": "chat"}
    new = {"id": "wa-new", "from": "15550001", "direction": "inbox", "body": "new whatsapp", "timestamp": 2, "type": "chat"}
    history = [old]
    monkeypatch.setattr(manager, "_iter_messages", lambda *args, **kwargs: list(history))
    def payload(latest):
        preview = manager._conversation_context("15550001", latest)
        preview.update({"from": "15550001", "message_ref": latest["id"]})
        return {"data": {"count": 1, "previews": [preview]}}
    agent = _notif_agent(tmp_path)
    publish_test_payload(tmp_path, "mcp.whatsapp", payload(old))
    first = _block("native-wa-1")
    holder = attach_active_notifications(agent, [first])
    historical = copy.deepcopy(first.metadata)
    history.append(new)
    publish_test_payload(tmp_path, "mcp.whatsapp", payload(new))
    producer = (tmp_path / ".notification/mcp.whatsapp.json").read_bytes()
    second = _block("native-wa-2")
    attach_active_notifications(agent, [second], prior_holder=holder)
    lane = second.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["whatsapp"]
    assert [message["id"] for message in lane["messages"]] == [new["id"]]
    assert lane["messages"][0]["text"] == "new whatsapp"
    assert lane["messages"][0]["from"] == "15550001"
    assert lane["events"][0]["conversation_ref"] == "whatsapp:15550001"
    assert lane["events"][0]["message_ref"] == new["id"]
    assert first.metadata == historical
    assert (tmp_path / ".notification/mcp.whatsapp.json").read_bytes() == producer


@pytest.mark.parametrize("source", ["mcp.telegram", "mcp.wechat", "mcp.feishu"])
def test_im_delta_before_seed_threshold_and_changing_preview_sender(tmp_path, source):
    from tests.test_notification_persistent_cap import _telegram_payload
    old = _telegram_message(1)
    new = _telegram_message(2)
    new["sender"] = "Other human"
    agent = _notif_agent(tmp_path)
    first = _block("small-window-1")
    raw = _telegram_payload([old])["notifications"]["mcp.telegram"]
    publish_test_payload(tmp_path, source, raw)
    holder = attach_active_notifications(agent, [first])
    raw = _telegram_payload([old, new])["notifications"]["mcp.telegram"]
    raw["data"]["previews"][0]["from"] = "Other human"
    publish_test_payload(tmp_path, source, raw)
    second = _block("small-window-2")
    attach_active_notifications(agent, [second], prior_holder=holder)
    lane = second.metadata["agent_meta"]["notifications"]["persistent"]["mcp"][source.split(".")[1]]
    assert [m["id"] for m in lane["messages"]] == [new["id"]]
    assert lane["messages"][0]["sender"] == "Other human"


def test_aggregate_concurrent_publication_commits_exact_observation(tmp_path, monkeypatch):
    from tests.test_notification_persistent_cap import _email, _email_payload
    old, new = _email(1, message="first"), _email(2, message="second")
    agent = _notif_agent(tmp_path)
    publish_test_payload(tmp_path, "email", _email_payload([old])["notifications"]["email"])
    read = notifications_module.coherent_attention_read
    raced = False
    def racing(*args):
        nonlocal raced
        observation = read(*args)
        if not raced:
            raced = True
            publish_test_payload(tmp_path, "email", _email_payload([old, new])["notifications"]["email"])
        return observation
    monkeypatch.setattr(notifications_module, "coherent_attention_read", racing)
    first = _block("aggregate-race-1")
    holder = attach_active_notifications(agent, [first])
    assert set(agent._notification_delivered_events["email"]) == {old["id"]}
    second = _block("aggregate-race-2")
    attach_active_notifications(agent, [second], prior_holder=holder)
    assert second.metadata["agent_meta"]["notifications"]["persistent"]["email"]["email_ids"] == [new["id"]]


def test_aggregate_no_carrier_and_failed_injection_leave_new_record_pending(tmp_path):
    from tests.test_notification_persistent_cap import _email, _email_payload
    old, new = _email(1, message="first"), _email(2, message="second")
    agent = _make_stub_agent_for_block_log(tmp_path)
    publish_test_payload(tmp_path, "email", _email_payload([old])["notifications"]["email"])
    attach_active_notifications(agent, [_block("retry-1")])
    committed = copy.deepcopy(agent._notification_delivered_events)
    publish_test_payload(tmp_path, "email", _email_payload([old, new])["notifications"]["email"])
    attach_active_notifications(agent, [])
    assert agent._notification_delivered_events == committed
    inject = agent._inject_notification_pair
    agent._inject_notification_pair = lambda notifications: False
    agent._heal_pending_tool_calls = lambda **kwargs: False
    agent._sync_notifications()
    assert agent._notification_delivered_events == committed
    agent._inject_notification_pair = inject
    agent._sync_notifications()
    events = agent._chat_stub.interface.entries[-1].content[0].metadata["agent_meta"]["notifications"]
    assert events["persistent"]["email"]["email_ids"] == [new["id"]]


def test_redacted_resync_preserves_delivered_records_but_not_new_arrivals(tmp_path, monkeypatch):
    from lingtai.kernel.base_agent import BaseAgent
    from lingtai.kernel.llm.interface import ToolCallBlock
    import lingtai.kernel.tool_result_recovery as recovery
    from tests.test_notification_persistent_cap import _email, _email_payload

    old, new = _email(1, message="first"), _email(2, message="second")
    agent = _notif_agent(tmp_path)
    agent._log = lambda *args, **kwargs: None
    publish_test_payload(tmp_path, "email", _email_payload([old])["notifications"]["email"])
    first = _block("resync-first")
    attach_active_notifications(agent, [first])
    redacted = ToolResultBlock(id="lost", name="x", content={}, synthesized=True)
    redacted.metadata["redacted"] = True
    monkeypatch.setattr(recovery, "recover_tool_result_block_from_events", lambda *args, **kwargs: redacted)
    assert BaseAgent._recover_pending_tool_result(agent, ToolCallBlock(id="lost", name="x", args={})) is redacted
    publish_test_payload(tmp_path, "email", _email_payload([old, new])["notifications"]["email"])
    second = _block("resync-next")
    attach_active_notifications(agent, [second])
    assert second.metadata["agent_meta"]["notifications"]["persistent"]["email"]["email_ids"] == [new["id"]]


def test_new_agent_restart_boundary_can_deliver_surviving_mirror_again(tmp_path):
    _write_email_notif(tmp_path)
    first = _block("old-process")
    attach_active_notifications(_notif_agent(tmp_path), [first])
    restarted = _block("new-process")
    attach_active_notifications(_notif_agent(tmp_path), [restarted])
    assert _has_notifications(restarted)  # honest process-local guarantee, no persistence


def test_shipped_producer_strings_do_not_require_plain_generic_dismiss():
    import ast
    import re
    root = Path(__file__).resolve().parents[1] / "src/lingtai"
    prohibited = re.compile(r"when\s+handled,?\s+dismiss\s+(?:this|the)\s+notification", re.I)
    for path in root.rglob("*"):
        if path.suffix == ".py":
            tree = ast.parse(path.read_text(encoding="utf-8"))
            texts = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        elif path.suffix == ".md":
            texts = [path.read_text(encoding="utf-8")]
        else:
            continue
        assert not any(prohibited.search(text) for text in texts), path


def test_real_prompt_reconstruction_retains_delivered_identity(tmp_path):
    from tests.test_context_ownership_redesign import _agent
    from tests.test_notification_persistent_cap import _email, _email_payload

    agent = _agent(tmp_path, capabilities=["context"])
    try:
        workdir = agent._working_dir
        old, new = _email(1, message="first"), _email(2, message="second")
        publish_test_payload(workdir, "email", _email_payload([old])["notifications"]["email"])
        first = _block("rebuild-before")
        attach_active_notifications(agent, [first])
        history = copy.deepcopy(first.metadata)
        producer = (workdir / ".notification/email.json").read_bytes()
        # Actual canonical prompt reconstruction used by rebuild/molt/refresh.
        # No provider request is needed to exercise this lifecycle boundary.
        agent._reconstruct_context()
        quiet = _block("rebuild-quiet")
        attach_active_notifications(agent, [quiet])
        assert not _has_notifications(quiet)
        assert first.metadata == history
        assert (workdir / ".notification/email.json").read_bytes() == producer
        publish_test_payload(workdir, "email", _email_payload([old, new])["notifications"]["email"])
        second = _block("rebuild-after")
        attach_active_notifications(agent, [second])
        assert second.metadata["agent_meta"]["notifications"]["persistent"]["email"]["email_ids"] == [new["id"]]
    finally:
        agent.stop(timeout=1.0)


def test_oversize_legacy_im_seed_does_not_ack_omitted_history(tmp_path):
    messages = [_telegram_message(i) for i in range(1, 22)]
    _write_telegram_notif(tmp_path, messages)
    agent = _notif_agent(tmp_path)
    first = _block("bounded-native-window")
    attach_active_notifications(agent, [first])
    lane = first.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    assert [m["id"] for m in lane["messages"]] == [m["id"] for m in messages[-20:]]
    assert set(agent._notification_delivered_events["mcp.telegram"]) == {m["id"] for m in messages[-20:]}
    assert messages[0]["id"] not in agent._notification_delivered_events["mcp.telegram"]
    # A deliberate check can reread the complete current mirror, including
    # legacy extra context; automatic delivery is scoped to the native window.
    check = _block("bounded-check", _notification_placeholder=True)
    attach_active_notifications(agent, [check], prior_holder=first)
    assert [m["id"] for m in check.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]["messages"]] == [m["id"] for m in messages]


def test_pending_im_reuses_extracted_values_and_keeps_unidentified_previews(monkeypatch):
    import lingtai.kernel.meta_block as meta_block

    source = "mcp.telegram"
    delivered = {"latest_incoming": {"id": "delivered", "text": "already sent"}}
    omission = {"latest_incoming": {"licc_structured_omitted": True, "reason": "size"}}
    idless = {"recent_messages": [{"text": "no producer identity"}]}
    payload = {"data": {"count": 3, "previews": [delivered, omission, idless], "cursor": "c1"}}
    before = copy.deepcopy(payload)
    records = _notification_records(source, payload)
    agent = SimpleNamespace(_notification_delivered_events={source: records})

    extract = meta_block._im_persistent_messages_from_notifications
    calls = 0

    def counted_extract(*args, **kwargs):
        nonlocal calls
        calls += 1
        return extract(*args, **kwargs)

    monkeypatch.setattr(meta_block, "_im_persistent_messages_from_notifications", counted_extract)
    projected = _pending_record_payload(agent, source, payload)

    assert projected["data"]["previews"] == [omission, idless]
    assert projected["data"]["count"] == 2
    assert projected["data"]["cursor"] == "c1"
    assert payload == before
    # _notification_records and the pending projection each extract once per preview.
    assert calls == 6


def test_pending_im_filters_by_event_identity_and_keeps_pending_context_unchanged():
    source = "mcp.telegram"
    latest = {"id": "compound-message", "event_id": "event-new", "text": "new callback"}
    preview = {
        "platform": "telegram",
        "conversation_ref": "chat-9",
        "message_ref": "thread-target",
        "event_id": "route-event",
        "recent_messages": [
            {"id": "compound-message", "event_id": "event-old", "text": "old callback"},
            latest,
        ],
        "latest_incoming": latest,
        "referenced_messages": [{"id": "reply-target", "text": "target context"}],
        "custom_metadata": {"keep": True},
    }
    payload = {"header": "telegram", "data": {"count": 1, "previews": [preview], "cursor": "c2"}}
    before = copy.deepcopy(payload)
    records = _notification_records(source, payload)
    agent = SimpleNamespace(
        _notification_delivered_events={source: {"event-old": records["event-old"]}}
    )

    projected = _pending_record_payload(agent, source, payload)
    retained = projected["data"]["previews"][0]

    assert [item["event_id"] for item in retained["recent_messages"]] == ["event-new"]
    assert retained["recent_messages"][0]["id"] == "compound-message"
    assert retained["latest_incoming"] == latest
    assert retained["referenced_messages"] == preview["referenced_messages"]
    assert retained["conversation_ref"] == preview["conversation_ref"]
    assert retained["platform"] == preview["platform"]
    assert retained["message_ref"] == preview["message_ref"]
    assert retained["event_id"] == preview["event_id"]
    assert retained["custom_metadata"] == preview["custom_metadata"]
    assert projected["data"]["cursor"] == "c2"
    assert payload == before


@pytest.mark.parametrize("message_ref", ["provider-message", None])
def test_pending_im_legacy_preview_fallback_preserves_only_real_message_ref(message_ref):
    source = "mcp.telegram"
    preview = {"preview": "legacy body", "conversation_ref": "chat-legacy", "event_id": "route-event"}
    if message_ref is not None:
        preview["message_ref"] = message_ref
    payload = {"data": {"count": 1, "previews": [preview]}}
    before = copy.deepcopy(payload)
    agent = SimpleNamespace(_notification_delivered_events={source: {}})

    projected = _pending_record_payload(agent, source, payload)
    retained = projected["data"]["previews"][0]
    fallback = retained["recent_messages"][0]

    assert fallback["source"] == "notification_preview"
    assert fallback["text"] == "legacy body"
    assert fallback["id"] == message_ref if message_ref is not None else fallback["id"].startswith("notification-preview:")
    assert retained.get("message_ref") == message_ref if message_ref is not None else "message_ref" not in retained
    assert "event_id" not in retained
    assert "latest_incoming" not in retained
    assert payload == before
