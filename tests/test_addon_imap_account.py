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
