"""Regression tests for the WhatsApp inbound sender allow-list (issue #727).

A WhatsApp business number is publicly reachable: any WhatsApp user who messages
it produces a correctly Meta-signed webhook, so the HMAC check authenticates the
*platform*, not the *sender*. Telegram (`accounts[].allowed_users`) and WeChat
(`allowed_users`) both enforce an operator-controlled sender allow-list before an
inbound message can wake the agent or land in its inbox; these tests lock in the
same trust layer for WhatsApp via `accounts[].allowed_wa_ids`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lingtai.mcp_servers.whatsapp.manager import WhatsAppManager


def _message_payload(*, wa_id: str, text: str = "hi", wamid: str = "wamid.TEST") -> dict[str, Any]:
    """Shape a webhook payload the way `extract_events` parses inbound messages."""
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "PNID"},
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": wamid,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def _status_payload(*, recipient_id: str) -> dict[str, Any]:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "PNID"},
                            "statuses": [
                                {
                                    "recipient_id": recipient_id,
                                    "id": "wamid.STATUS",
                                    "status": "delivered",
                                    "timestamp": "1700000000",
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.calls.append(event)


def _manager(tmp_path: Path, account: dict[str, Any], recorder: _Recorder) -> WhatsAppManager:
    base = {"alias": "biz", "phone_number_id": "PNID", "access_token": "T", "app_secret": "S", "verify_token": "V"}
    base.update(account)
    return WhatsAppManager(accounts_config=[base], working_dir=tmp_path, on_inbound=recorder)


def _inbox_files(tmp_path: Path, alias: str = "biz") -> list[Path]:
    return list((tmp_path / "whatsapp" / alias / "inbox").glob("*/message.json"))


def test_ingest_allows_listed_sender(tmp_path: Path) -> None:
    rec = _Recorder()
    mgr = _manager(tmp_path, {"allowed_wa_ids": ["15551230001"]}, rec)
    mgr.ingest_webhook("biz", _message_payload(wa_id="15551230001"))
    assert len(_inbox_files(tmp_path)) == 1
    assert len(rec.calls) == 1
    assert rec.calls[0]["wake"] is True
    assert rec.calls[0]["metadata"]["wa_id"] == "15551230001"


def test_ingest_drops_unlisted_sender(tmp_path: Path) -> None:
    rec = _Recorder()
    mgr = _manager(tmp_path, {"allowed_wa_ids": ["15551230001"]}, rec)
    events = mgr.ingest_webhook("biz", _message_payload(wa_id="19998887777"))
    assert _inbox_files(tmp_path) == []
    assert rec.calls == []
    assert events[0].get("filtered") is True


def test_no_filter_when_option_absent(tmp_path: Path) -> None:
    rec = _Recorder()
    mgr = _manager(tmp_path, {}, rec)
    mgr.ingest_webhook("biz", _message_payload(wa_id="19998887777"))
    assert len(_inbox_files(tmp_path)) == 1
    assert len(rec.calls) == 1


def test_no_filter_when_option_empty_list(tmp_path: Path) -> None:
    rec = _Recorder()
    mgr = _manager(tmp_path, {"allowed_wa_ids": []}, rec)
    mgr.ingest_webhook("biz", _message_payload(wa_id="19998887777"))
    assert len(_inbox_files(tmp_path)) == 1
    assert len(rec.calls) == 1


def test_wa_id_normalization(tmp_path: Path) -> None:
    """Allow-list entries and inbound wa_ids are normalized identically so an
    operator who writes `+1 5551230001` is not silently locked out."""
    rec = _Recorder()
    mgr = _manager(tmp_path, {"allowed_wa_ids": ["+1 (555) 123-0001"]}, rec)
    mgr.ingest_webhook("biz", _message_payload(wa_id="15551230001"))
    assert len(_inbox_files(tmp_path)) == 1
    assert len(rec.calls) == 1


def test_wa_id_normalization_accepts_int_entry(tmp_path: Path) -> None:
    rec = _Recorder()
    mgr = _manager(tmp_path, {"allowed_wa_ids": [15551230001]}, rec)
    mgr.ingest_webhook("biz", _message_payload(wa_id="15551230001"))
    assert len(_inbox_files(tmp_path)) == 1


def test_status_events_not_filtered(tmp_path: Path) -> None:
    """Status/delivery events carry `recipient_id`, are never forwarded to the
    agent, and must not be dropped by the sender filter (would break outbound
    delivery bookkeeping if ever added)."""
    rec = _Recorder()
    mgr = _manager(tmp_path, {"allowed_wa_ids": ["15551230001"]}, rec)
    events = mgr.ingest_webhook("biz", _status_payload(recipient_id="19998887777"))
    assert _inbox_files(tmp_path) == []
    assert rec.calls == []
    assert events[0]["kind"] == "status"
    assert "filtered" not in events[0]


def test_multi_account_isolation(tmp_path: Path) -> None:
    rec = _Recorder()
    accounts = [
        {"alias": "locked", "phone_number_id": "P1", "access_token": "T", "app_secret": "S", "verify_token": "V", "allowed_wa_ids": ["15551230001"]},
        {"alias": "open", "phone_number_id": "P2", "access_token": "T", "app_secret": "S", "verify_token": "V"},
    ]
    mgr = WhatsAppManager(accounts_config=accounts, working_dir=tmp_path, on_inbound=rec)
    mgr.ingest_webhook("open", _message_payload(wa_id="19998887777"))
    assert len(_inbox_files(tmp_path, "open")) == 1
    mgr.ingest_webhook("locked", _message_payload(wa_id="19998887777"))
    assert _inbox_files(tmp_path, "locked") == []


def test_account_details_reports_count(tmp_path: Path) -> None:
    rec = _Recorder()
    accounts = [
        {"alias": "locked", "phone_number_id": "P1", "access_token": "T", "app_secret": "S", "verify_token": "V", "allowed_wa_ids": ["15551230001"]},
        {"alias": "open", "phone_number_id": "P2", "access_token": "T", "app_secret": "S", "verify_token": "V"},
    ]
    mgr = WhatsAppManager(accounts_config=accounts, working_dir=tmp_path, on_inbound=rec)
    details = {d["alias"]: d for d in mgr.account_details()}
    assert details["locked"]["allowed_wa_ids_count"] == 1
    assert "allowed_wa_ids_count" not in details["open"]


def test_redact_account_never_leaks_wa_ids() -> None:
    from lingtai.mcp_servers.whatsapp.redaction import redact_account

    out = redact_account(
        {"alias": "biz", "phone_number_id": "P", "allowed_wa_ids": ["15551230001", "15551230002"]}
    )
    serialized = repr(out)
    assert "15551230001" not in serialized
    assert "15551230002" not in serialized
    assert out["allowed_wa_ids_count"] == 2
