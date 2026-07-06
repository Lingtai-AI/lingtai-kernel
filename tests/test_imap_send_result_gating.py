"""Regression tests: IMAP send/reply bookkeeping must gate on the send result.

`IMAPAccount.send_email` returns an error string (not None) on failure instead
of raising, so the manager must inspect the return value before recording any
post-send state. Two bugs are covered here:

  * `_send` recorded the duplicate-detection counter unconditionally, so a
    failed delivery still counted toward the two-free-passes block and could
    lock out the retry that would have succeeded.
  * `_reply` set the original message's `\\Answered` flag unconditionally, so a
    failed reply still marked the source mail answered — a success envelope
    that outruns the real result.
"""
from __future__ import annotations

from pathlib import Path

from lingtai.mcp_servers.imap.manager import IMAPMailManager


class FakeAccount:
    address = "me@example.com"

    def __init__(self, send_results: list[str | None]) -> None:
        # Successive return values for send_email: an error string means the
        # delivery failed; None means it succeeded.
        self._send_results = list(send_results)
        self.send_calls = 0
        self.stored_flags: list[tuple[str, str, list[str]]] = []

    def fetch_full(self, folder: str, uid: str) -> dict:
        return {
            "from": "Sender <sender@example.com>",
            "from_address": "sender@example.com",
            "subject": "Question",
            "message_id": "<orig@example.com>",
            "references": "<parent@example.com>",
        }

    def send_email(self, **kwargs):
        result = self._send_results[self.send_calls]
        self.send_calls += 1
        return result

    def store_flags(self, folder: str, uid: str, flags: list[str]) -> bool:
        self.stored_flags.append((folder, uid, flags))
        return True


class FakeService:
    def __init__(self, account: FakeAccount) -> None:
        self.default_account = account
        self._account = account

    def get_account(self, address: str | None):
        return self._account


def _manager(account: FakeAccount) -> IMAPMailManager:
    return IMAPMailManager(
        FakeService(account),
        working_dir=Path("/tmp/imap-send-result-workdir"),
        tcp_alias="/tmp/imap-bridge",
        on_inbound=lambda payload: None,
    )


def _send(mgr: IMAPMailManager, message: str) -> dict:
    return mgr.handle({
        "action": "send",
        "address": "peer@example.com",
        "subject": "hi",
        "message": message,
    })


def test_failed_send_does_not_count_toward_duplicate_block():
    # Two failed delivery attempts, then a success. Failed sends must not
    # consume the two free passes, so the third (successful) send must deliver.
    account = FakeAccount(send_results=["smtp: temporary failure", "smtp: temporary failure", None])
    mgr = _manager(account)

    first = _send(mgr, "please retry")
    assert first["status"] == "error"
    second = _send(mgr, "please retry")
    assert second["status"] == "error"

    third = _send(mgr, "please retry")
    assert third["status"] == "delivered"
    assert third["to"] == ["peer@example.com"]


def test_two_successful_identical_sends_then_block():
    # Successful identical sends still count and the third is blocked.
    account = FakeAccount(send_results=[None, None, None])
    mgr = _manager(account)

    assert _send(mgr, "Done.")["status"] == "delivered"
    assert _send(mgr, "Done.")["status"] == "delivered"

    blocked = _send(mgr, "Done.")
    assert blocked["status"] == "blocked"
    assert "peer@example.com" in blocked["warning"]


def test_reply_failure_does_not_mark_answered():
    account = FakeAccount(send_results=["smtp: connection reset"])
    mgr = _manager(account)

    result = mgr.handle({
        "action": "reply",
        "email_id": "me@example.com:INBOX:42",
        "message": "here is my reply",
    })

    assert result["status"] == "error"
    # The original message must NOT be flagged answered when the reply failed.
    assert account.stored_flags == []


def test_reply_success_marks_answered():
    account = FakeAccount(send_results=[None])
    mgr = _manager(account)

    result = mgr.handle({
        "action": "reply",
        "email_id": "me@example.com:INBOX:42",
        "message": "here is my reply",
    })

    assert result["status"] == "delivered"
    assert account.stored_flags == [("INBOX", "42", ["\\Answered"])]
