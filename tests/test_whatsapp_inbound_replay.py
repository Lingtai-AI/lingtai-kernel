"""Regression tests for inbound WhatsApp webhook replay / dedup.

Bug: ``WhatsAppManager.ingest_webhook`` landed every inbound ``message`` event
under a fresh ``uuid4()`` directory and fired ``on_inbound(..., wake=True)``
unconditionally, keyed by nothing stable. Meta's webhook delivery is
at-least-once (it re-delivers the same payload — same ``messages[].id`` /
wamid — whenever it does not receive a timely HTTP 200), so a single user
message landed in the inbox multiple times and woke the agent repeatedly.

These tests pin the idempotency guard that suppresses such replays by the
stable upstream wamid while preserving genuinely new messages, mirroring the
WeChat manager's ``inbox_seen.json`` guard.
"""
from __future__ import annotations

import json
from pathlib import Path

from lingtai.mcp_servers.whatsapp import manager as wa_manager
from lingtai.mcp_servers.whatsapp.manager import WhatsAppManager


def _manager(tmp_path: Path, events: list[dict]) -> WhatsAppManager:
    return WhatsAppManager(
        accounts_config=[{
            "alias": "biz",
            "phone_number_id": "PNID123",
            "access_token": "test-token",
        }],
        working_dir=tmp_path,
        on_inbound=events.append,
    )


def _payload(*, wamid: str | None, wa_id: str = "15551230000", text: str = "hi") -> dict:
    """A Meta-shaped inbound-message webhook payload."""
    message: dict = {"from": wa_id, "timestamp": "1700000000", "type": "text",
                     "text": {"body": text}}
    if wamid is not None:
        message["id"] = wamid
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "PNID123"},
                    "messages": [message],
                },
            }],
        }],
    }


def _inbox_count(tmp_path: Path, alias: str = "biz") -> int:
    inbox = tmp_path / "whatsapp" / alias / "inbox"
    if not inbox.exists():
        return 0
    return sum(1 for d in inbox.iterdir() if (d / "message.json").is_file())


# ── Replay suppression (the bug) ──────────────────────────────────────────

def test_duplicate_webhook_delivery_lands_once(tmp_path):
    """Same wamid delivered twice (Meta retry) lands ONCE and wakes ONCE,
    even though a fresh local UUID would otherwise be minted each time."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    ev1 = mgr.ingest_webhook("biz", _payload(wamid="wamid.AAAA"))
    ev2 = mgr.ingest_webhook("biz", _payload(wamid="wamid.AAAA"))

    assert _inbox_count(tmp_path) == 1, "replay must not create a 2nd inbox entry"
    assert len(events) == 1, "replay must not trigger a 2nd LICC wake"
    # Return value (extracted events) is unchanged on the suppressed call.
    assert len(ev1) == 1 and len(ev2) == 1


def test_seen_state_survives_restart(tmp_path):
    """The guard is durable: a fresh manager over the same working_dir loads
    inbox_seen.json and still suppresses the replay."""
    events1: list[dict] = []
    mgr1 = _manager(tmp_path, events1)
    mgr1.ingest_webhook("biz", _payload(wamid="wamid.PERSIST"))
    assert (tmp_path / "whatsapp" / "inbox_seen.json").is_file()

    events2: list[dict] = []
    mgr2 = _manager(tmp_path, events2)  # == relaunch after refresh
    mgr2.ingest_webhook("biz", _payload(wamid="wamid.PERSIST"))

    assert _inbox_count(tmp_path) == 1
    assert events2 == [], "replay after relaunch must not re-wake the host"


# ── Genuine messages must NOT be dropped ──────────────────────────────────

def test_distinct_wamids_both_land(tmp_path):
    """Two payloads differing only in wamid both land and both wake."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    mgr.ingest_webhook("biz", _payload(wamid="wamid.1", text="ok"))
    mgr.ingest_webhook("biz", _payload(wamid="wamid.2", text="ok"))

    assert _inbox_count(tmp_path) == 2
    assert len(events) == 2


def test_same_text_different_wamids_not_deduped(tmp_path):
    """Identical text + wa_id but different wamids are two real messages
    (guards against over-aggressive content-based keying)."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    mgr.ingest_webhook("biz", _payload(wamid="wamid.a", text="same words"))
    mgr.ingest_webhook("biz", _payload(wamid="wamid.b", text="same words"))

    assert _inbox_count(tmp_path) == 2
    assert len(events) == 2


def test_missing_message_id_never_suppressed(tmp_path):
    """An event with no upstream wamid has no stable identity, so it is never
    deduped — both deliveries land (better a rare dup than a dropped message)."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    mgr.ingest_webhook("biz", _payload(wamid=None, text="no id"))
    mgr.ingest_webhook("biz", _payload(wamid=None, text="no id"))

    assert _inbox_count(tmp_path) == 2
    assert len(events) == 2


# ── Eviction / ordering ───────────────────────────────────────────────────

def test_seen_keys_fifo_eviction(tmp_path, monkeypatch):
    """Beyond SEEN_KEYS_MAX the oldest key is evicted, so replaying the oldest
    lands again while replaying the newest is still suppressed."""
    monkeypatch.setattr(wa_manager, "SEEN_KEYS_MAX", 3)
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    for n in range(1, 5):  # 4 distinct messages, window is 3
        mgr.ingest_webhook("biz", _payload(wamid=f"wamid.{n}", text=f"m{n}"))
    assert _inbox_count(tmp_path) == 4

    # wamid.1 was evicted -> replaying it lands again.
    mgr.ingest_webhook("biz", _payload(wamid="wamid.1", text="m1"))
    assert _inbox_count(tmp_path) == 5

    # wamid.4 is still in the window -> replay suppressed.
    before = len(events)
    mgr.ingest_webhook("biz", _payload(wamid="wamid.4", text="m4"))
    assert _inbox_count(tmp_path) == 5
    assert len(events) == before


def test_record_after_store_ordering(tmp_path):
    """If _store_message raises, the key is NOT recorded, so a later
    successful ingest of the same payload still lands the message."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    orig_store = mgr._store_message
    boom = {"fail": True}

    def flaky(alias, folder, msg):
        if boom["fail"]:
            raise RuntimeError("disk full")
        return orig_store(alias, folder, msg)

    mgr._store_message = flaky  # type: ignore[method-assign]

    try:
        mgr.ingest_webhook("biz", _payload(wamid="wamid.CRASH"))
    except RuntimeError:
        pass
    assert _inbox_count(tmp_path) == 0
    assert events == []

    boom["fail"] = False
    mgr.ingest_webhook("biz", _payload(wamid="wamid.CRASH"))
    assert _inbox_count(tmp_path) == 1, "key must not have been recorded on the failed store"
    assert len(events) == 1


# ── Corruption tolerance ──────────────────────────────────────────────────

def test_corrupt_seen_file_degrades_gracefully(tmp_path):
    """A corrupt inbox_seen.json must not crash construction; the guard resets
    to empty and ingest still works."""
    (tmp_path / "whatsapp").mkdir(parents=True, exist_ok=True)
    (tmp_path / "whatsapp" / "inbox_seen.json").write_text("{not json", encoding="utf-8")

    events: list[dict] = []
    mgr = _manager(tmp_path, events)  # must not raise
    mgr.ingest_webhook("biz", _payload(wamid="wamid.OK"))

    assert _inbox_count(tmp_path) == 1
    assert len(events) == 1


# ── Provenance ────────────────────────────────────────────────────────────

def test_landed_message_records_stable_key_provenance(tmp_path):
    """The landed message.json carries stable_key so a suppressed duplicate is
    always traceable to its original landing (no silent loss)."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    mgr.ingest_webhook("biz", _payload(wamid="wamid.TRACE", wa_id="15551239999"))

    inbox = tmp_path / "whatsapp" / "biz" / "inbox"
    data = json.loads((next(inbox.iterdir()) / "message.json").read_text(encoding="utf-8"))
    assert data["stable_key"] == "biz|15551239999|mid:wamid.TRACE"
