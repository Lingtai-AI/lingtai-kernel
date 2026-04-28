"""Tests for the imapclient-based IMAPAccount.

Mock IMAPClient at the class level so we can drive the listener loop
with synthetic IDLE responses.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingtai.addons.imap.account import IMAPAccount


@pytest.fixture
def mock_imapclient_class():
    """Patch IMAPClient at the location it's imported in account.py."""
    with patch("lingtai.addons.imap.account.IMAPClient") as cls:
        yield cls


@pytest.fixture
def account(tmp_path: Path) -> IMAPAccount:
    return IMAPAccount(
        email_address="alice@example.com",
        email_password="appsecret",
        imap_host="imap.example.com",
        imap_port=993,
        smtp_host="smtp.example.com",
        smtp_port=587,
        working_dir=tmp_path,
        poll_interval=30,
    )


def test_connect_logs_in_and_caches_capabilities(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IMAP4REV1", b"IDLE", b"MOVE", b"UIDPLUS")
    instance.list_folders.return_value = [
        ((b"\\HasNoChildren",), b"/", "INBOX"),
        ((b"\\HasNoChildren", b"\\Sent"), b"/", "Sent"),
    ]

    account.connect()

    mock_imapclient_class.assert_called_once_with(
        "imap.example.com", port=993, ssl=True,
    )
    instance.login.assert_called_once_with("alice@example.com", "appsecret")
    assert account.has_idle is True
    assert account.has_move is True
    assert account.has_uidplus is True
    assert account.connected is True


def test_disconnect_logs_out_and_marks_disconnected(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    account.connect()

    account.disconnect()

    instance.logout.assert_called_once()
    assert account.connected is False


def test_connected_reports_false_when_noop_fails(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    account.connect()

    instance.noop.side_effect = OSError("connection reset")

    assert account.connected is False


def test_connect_is_idempotent(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """Double connect() must not open a second client."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []

    account.connect()
    account.connect()  # second call

    # IMAPClient(...) called exactly once, login called exactly once
    assert mock_imapclient_class.call_count == 1
    instance.login.assert_called_once()


def test_connected_clears_dead_pointer_so_next_call_reconnects(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """After NOOP fails, _tool_imap must be cleared so _ensure_connected reconnects."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []

    account.connect()
    assert mock_imapclient_class.call_count == 1

    # Simulate dead connection
    instance.noop.side_effect = OSError("connection reset")
    assert account.connected is False

    # Next access via _ensure_connected should trigger a fresh connect
    instance.noop.side_effect = None  # let the next NOOP succeed
    account.connect()
    assert mock_imapclient_class.call_count == 2


def test_fetch_envelopes_returns_n_most_recent(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.search.return_value = [101, 102, 103, 104, 105]
    instance.fetch.return_value = {
        103: {
            b"FLAGS": (b"\\Seen",),
            b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
                b"From: a@b.com\r\nTo: alice@example.com\r\n"
                b"Subject: hello\r\nDate: Mon, 1 Jan 2026 00:00:00 +0000\r\n",
        },
        104: {
            b"FLAGS": (),
            b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
                b"From: c@d.com\r\nSubject: world\r\n",
        },
        105: {
            b"FLAGS": (b"\\Flagged",),
            b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
                b"From: e@f.com\r\nSubject: !\r\n",
        },
    }
    account.connect()

    envelopes = account.fetch_envelopes("INBOX", n=3)

    instance.select_folder.assert_called_with("INBOX", readonly=True)
    instance.search.assert_called_with("ALL")
    # Fetch was called with last 3 UIDs
    fetch_call = instance.fetch.call_args
    assert sorted(fetch_call[0][0]) == [103, 104, 105]
    # Result includes uid, from, subject, flags, email_id
    assert len(envelopes) == 3
    by_uid = {e["uid"]: e for e in envelopes}
    assert by_uid["103"]["from"] == "a@b.com"
    assert by_uid["103"]["subject"] == "hello"
    assert "\\Seen" in by_uid["103"]["flags"]
    assert by_uid["103"]["email_id"] == "alice@example.com:INBOX:103"


def test_fetch_envelopes_handles_empty_folder(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.search.return_value = []
    account.connect()

    assert account.fetch_envelopes("INBOX", n=10) == []


def test_fetch_headers_by_uids(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.fetch.return_value = {
        42: {
            b"FLAGS": (b"\\Seen",),
            b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
                b"From: x@y.com\r\nSubject: hi\r\n",
        },
    }
    account.connect()

    out = account.fetch_headers_by_uids("INBOX", ["42"])
    assert len(out) == 1
    assert out[0]["uid"] == "42"
    assert out[0]["from"] == "x@y.com"


def test_envelope_handles_non_ascii_keyword_flag(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """Custom keyword flags with non-ASCII bytes must not crash the parser."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.fetch.return_value = {
        7: {
            b"FLAGS": (b"\\Seen", b"\xe2\x98\x85important"),  # star-prefixed UTF-8
            b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
                b"From: a@b.com\r\nSubject: hi\r\n",
        },
    }
    account.connect()

    out = account.fetch_headers_by_uids("INBOX", ["7"])
    # Did not raise. Flag list contains the seen flag and a (possibly
    # mojibake'd) keyword flag — both are strings, no exception.
    assert len(out) == 1
    assert "\\Seen" in out[0]["flags"]
    assert all(isinstance(f, str) for f in out[0]["flags"])
