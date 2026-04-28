"""Tests for the imapclient-based IMAPAccount.

Mock IMAPClient at the class level so we can drive the listener loop
with synthetic IDLE responses.
"""
from __future__ import annotations

import socket
import threading
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


def test_fetch_full_returns_body_and_attachments(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    raw = (
        b"From: a@b.com\r\nTo: alice@example.com\r\n"
        b"Subject: hello\r\nDate: Mon, 1 Jan 2026 00:00:00 +0000\r\n"
        b"Message-ID: <abc@xyz>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Hello body.\r\n"
    )
    instance.fetch.return_value = {
        42: {b"FLAGS": (b"\\Seen",), b"RFC822": raw},
    }
    account.connect()

    full = account.fetch_full("INBOX", "42")
    assert full is not None
    assert full["uid"] == "42"
    assert full["from"] == "a@b.com"
    assert full["body"].strip() == "Hello body."
    assert full["message_id"] == "<abc@xyz>"
    assert full["flags"] == ["\\Seen"]
    assert full["email_id"] == "alice@example.com:INBOX:42"


def test_fetch_full_returns_none_when_uid_missing(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.fetch.return_value = {}
    account.connect()
    assert account.fetch_full("INBOX", "42") is None


def test_search_returns_uids(mock_imapclient_class, account: IMAPAccount) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.search.return_value = [10, 20, 30]
    account.connect()

    uids = account.search("INBOX", "from:bob@x.com unseen")
    instance.search.assert_called_with([b"FROM", b"bob@x.com", b"UNSEEN"])
    assert uids == ["10", "20", "30"]


def test_store_flags_add_seen(mock_imapclient_class, account: IMAPAccount) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    account.connect()

    assert account.store_flags("INBOX", "42", ["\\Seen"]) is True
    instance.add_flags.assert_called_with([42], [b"\\Seen"])


def test_store_flags_remove_seen(mock_imapclient_class, account: IMAPAccount) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    account.connect()

    assert account.store_flags("INBOX", "42", ["\\Seen"], action="-FLAGS") is True
    instance.remove_flags.assert_called_with([42], [b"\\Seen"])


def test_move_message_uses_move_when_supported(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE", b"MOVE")
    instance.list_folders.return_value = []
    account.connect()

    assert account.move_message("INBOX", "42", "Archive") is True
    instance.move.assert_called_with([42], "Archive")


def test_move_message_falls_back_to_copy_delete(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)  # no MOVE
    instance.list_folders.return_value = []
    account.connect()

    assert account.move_message("INBOX", "42", "Archive") is True
    instance.copy.assert_called_with([42], "Archive")
    instance.add_flags.assert_called_with([42], [b"\\Deleted"])
    instance.expunge.assert_called()


def test_delete_message_moves_to_trash_when_available(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE", b"MOVE")
    instance.list_folders.return_value = [
        ((b"\\Trash",), b"/", "Trash"),
    ]
    account.connect()

    assert account.delete_message("INBOX", "42") is True
    instance.move.assert_called_with([42], "Trash")


def test_fetch_full_handles_non_ascii_keyword_flag(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """fetch_full must not crash on non-ASCII keyword flags."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.fetch.return_value = {
        7: {
            b"FLAGS": (b"\\Seen", b"\xe2\x98\x85important"),
            b"RFC822": b"From: a@b.com\r\nSubject: hi\r\n\r\nbody\r\n",
        },
    }
    account.connect()

    full = account.fetch_full("INBOX", "7")
    assert full is not None
    assert "\\Seen" in full["flags"]
    assert all(isinstance(f, str) for f in full["flags"])


def test_move_message_uses_uid_expunge_when_uidplus_available(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """When MOVE absent but UIDPLUS present, fallback uses uid_expunge."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE", b"UIDPLUS")  # no MOVE
    instance.list_folders.return_value = []
    account.connect()

    assert account.move_message("INBOX", "42", "Archive") is True
    instance.copy.assert_called_with([42], "Archive")
    instance.add_flags.assert_called_with([42], [b"\\Deleted"])
    instance.uid_expunge.assert_called_with([42])
    instance.expunge.assert_not_called()


def test_delete_message_uses_uid_expunge_when_uidplus_in_trash(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """delete_message in trash with UIDPLUS uses uid_expunge."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE", b"UIDPLUS")
    # Make INBOX the only folder and trash absent so we hit the in-place path.
    instance.list_folders.return_value = []
    account.connect()

    assert account.delete_message("INBOX", "42") is True
    instance.add_flags.assert_called_with([42], [b"\\Deleted"])
    instance.uid_expunge.assert_called_with([42])
    instance.expunge.assert_not_called()


def test_search_invalid_date_skipped(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """Malformed since: date is logged and skipped, not raised."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.search.return_value = []
    account.connect()

    # Malformed date — should not raise
    out = account.search("INBOX", "since:not-a-date from:bob@x.com")
    # Only the from: term made it into the criteria
    instance.search.assert_called_with([b"FROM", b"bob@x.com"])
    assert out == []


def test_store_flags_rejects_unknown_action(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    account.connect()

    assert account.store_flags("INBOX", "42", ["\\Seen"], action="bogus") is False
    instance.add_flags.assert_not_called()
    instance.remove_flags.assert_not_called()
    instance.set_flags.assert_not_called()


def test_store_flags_rejects_non_ascii_flag(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    account.connect()

    assert account.store_flags("INBOX", "42", ["重要"]) is False
    instance.add_flags.assert_not_called()


def test_send_email_rejects_empty(account: IMAPAccount) -> None:
    err = account.send_email(to=["x@y.com"], subject="", body="")
    assert err is not None and "empty" in err.lower()


def test_send_email_signature(account: IMAPAccount) -> None:
    """Validate kwargs accepted by send_email — guards against accidental signature drift."""
    import inspect
    sig = inspect.signature(account.send_email)
    params = sig.parameters
    assert "to" in params
    assert "subject" in params
    assert "body" in params
    assert "cc" in params
    assert "bcc" in params
    assert "attachments" in params
    assert "in_reply_to" in params
    assert "references" in params


def test_reconcile_first_run_bootstraps_from_uidnext_and_unseen(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """First run: no state file. Bootstrap delivers current UNSEEN once,
    then sets watermark to current UIDNEXT. UNSEEN flag is consulted only here."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 12345, b"UIDNEXT": 5000}
    instance.search.return_value = [4998, 4999]  # currently UNSEEN
    instance.fetch.return_value = {
        4998: {b"FLAGS": (), b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
               b"From: a@b.com\r\nSubject: one\r\n"},
        4999: {b"FLAGS": (), b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
               b"From: c@d.com\r\nSubject: two\r\n"},
    }
    account.connect()

    delivered = account.reconcile("INBOX")

    instance.search.assert_called_with(b"UNSEEN")
    assert {e["uid"] for e in delivered} == {"4998", "4999"}
    # Watermark persisted at UIDNEXT - 1
    assert account._watermark.load() == {
        "INBOX": {"uidvalidity": 12345, "last_delivered_uid": 4999}
    }


def test_reconcile_normal_path_uses_uid_range(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """Subsequent runs: watermark exists, search by UID range, deliver new only."""
    # Pre-seed watermark
    account._watermark.save({"INBOX": {"uidvalidity": 12345, "last_delivered_uid": 4999}})

    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 12345, b"UIDNEXT": 5003}
    instance.search.return_value = [5001, 5002]
    instance.fetch.return_value = {
        5001: {b"FLAGS": (), b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
               b"From: a@b.com\r\nSubject: new1\r\n"},
        5002: {b"FLAGS": (), b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
               b"From: c@d.com\r\nSubject: new2\r\n"},
    }
    account.connect()

    delivered = account.reconcile("INBOX")

    instance.search.assert_called_with([b"UID", b"5000:*"])
    assert {e["uid"] for e in delivered} == {"5001", "5002"}
    assert account._watermark.load()["INBOX"]["last_delivered_uid"] == 5002


def test_reconcile_uidvalidity_change_resets_and_delivers_nothing(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """UIDVALIDITY mismatch → reset watermark, deliver nothing this round."""
    account._watermark.save({"INBOX": {"uidvalidity": 12345, "last_delivered_uid": 5000}})

    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 99999, b"UIDNEXT": 200}
    account.connect()

    delivered = account.reconcile("INBOX")

    assert delivered == []
    assert account._watermark.load() == {
        "INBOX": {"uidvalidity": 99999, "last_delivered_uid": 199}
    }


def test_reconcile_no_new_mail_is_noop(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    account._watermark.save({"INBOX": {"uidvalidity": 12345, "last_delivered_uid": 5000}})

    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 12345, b"UIDNEXT": 5001}
    instance.search.return_value = []
    account.connect()

    assert account.reconcile("INBOX") == []
    # Watermark untouched
    assert account._watermark.load()["INBOX"]["last_delivered_uid"] == 5000


def test_reconcile_bootstrap_pins_watermark_at_uidnext_minus_one(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """Bootstrap with UNSEEN well below UIDNEXT must NOT cause re-delivery
    of old read mail on the next reconcile call.

    Pre-fix bug: watermark was set to max(unseen_uids), so a mailbox with
    UNSEEN=[10, 20] and UIDNEXT=100 would set watermark=20, and the next
    reconcile would search UID 21:* and re-deliver the already-read mail
    in UIDs 21..99.
    """
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 7, b"UIDNEXT": 100}
    instance.search.return_value = [10, 20]  # UNSEEN — both well below UIDNEXT
    instance.fetch.return_value = {
        10: {b"FLAGS": (), b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
             b"From: a@b.com\r\nSubject: ten\r\n"},
        20: {b"FLAGS": (), b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
             b"From: c@d.com\r\nSubject: twenty\r\n"},
    }
    account.connect()

    delivered = account.reconcile("INBOX")

    # Bootstrap delivered UIDs 10 and 20
    assert {e["uid"] for e in delivered} == {"10", "20"}
    # CRITICAL: watermark must be UIDNEXT-1 = 99, NOT max(unseen) = 20
    state = account._watermark.load()
    assert state["INBOX"]["last_delivered_uid"] == 99
    assert state["INBOX"]["uidvalidity"] == 7


def test_reconcile_bootstrap_with_no_unseen_pins_at_uidnext_minus_one(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """Bootstrap with empty UNSEEN should still set watermark to UIDNEXT-1."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 1, b"UIDNEXT": 5000}
    instance.search.return_value = []  # no unseen
    account.connect()

    delivered = account.reconcile("INBOX")

    assert delivered == []
    assert account._watermark.load() == {
        "INBOX": {"uidvalidity": 1, "last_delivered_uid": 4999}
    }


def test_listener_idle_exists_triggers_reconcile(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """One IDLE slice returns EXISTS → loop calls idle_done, reconcile, idle again.

    NOTE: Uses UID 101 (above UIDNEXT=100 watermark baseline) so the
    reconcile UID-range filter (> last=99) accepts it. The original spec
    used UID 99 which is at-or-below the watermark and would be silently
    filtered out, making the delivery assertion unreachable.
    """
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 1, b"UIDNEXT": 100}
    instance.search.side_effect = [
        [],            # bootstrap UNSEEN search → empty
        [101],         # second search: UID range query for new mail (filtered)
    ]
    # idle_check returns: first call → EXISTS; second call → empty (slice expired)
    instance.idle_check.side_effect = [
        [(101, b"EXISTS")],
        [],
    ]
    instance.fetch.return_value = {
        101: {b"FLAGS": (), b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]":
              b"From: a@b.com\r\nSubject: hi\r\n"},
    }

    received: list[dict] = []
    stop = threading.Event()

    def on_message(headers: list[dict]) -> None:
        received.extend(headers)
        stop.set()  # exit loop after first delivery

    account._stop_event = stop
    account._run_listener_loop(folder="INBOX", on_message=on_message,
                               max_iterations=3)

    instance.idle.assert_called()
    instance.idle_done.assert_called()
    assert any(e["uid"] == "101" for e in received)


def test_listener_silent_slice_runs_noop_keepalive(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 1, b"UIDNEXT": 100}
    instance.search.return_value = []
    # idle_check always returns nothing (silent slice) — callable so it
    # never raises StopIteration regardless of how many times it's called
    instance.idle_check.side_effect = None
    instance.idle_check.return_value = []

    stop = threading.Event()

    def on_message(headers: list[dict]) -> None:
        pass

    account._stop_event = stop
    # Stop after a tight loop
    threading.Timer(0.05, stop.set).start()
    account._run_listener_loop(folder="INBOX", on_message=on_message,
                               max_iterations=3)

    instance.noop.assert_called()


def test_listener_reconnects_on_idle_check_error(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 1, b"UIDNEXT": 100}
    instance.search.return_value = []
    # First idle_check raises (triggers reconnect); all subsequent calls
    # return [] so the loop runs cleanly until stop fires
    _idle_check_calls = [0]

    def _idle_check_side_effect(**kwargs: object) -> list:
        _idle_check_calls[0] += 1
        if _idle_check_calls[0] == 1:
            raise socket.error("connection reset")
        return []

    instance.idle_check.side_effect = _idle_check_side_effect

    stop = threading.Event()

    def on_message(headers: list[dict]) -> None:
        pass

    threading.Timer(0.1, stop.set).start()
    account._stop_event = stop
    account._run_listener_loop(folder="INBOX", on_message=on_message,
                               max_iterations=3, backoff_override=0.0)

    # connect was called more than once (initial + reconnect)
    assert mock_imapclient_class.call_count >= 2


def test_listener_stop_event_exits_cleanly(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []
    instance.folder_status.return_value = {b"UIDVALIDITY": 1, b"UIDNEXT": 100}
    instance.search.return_value = []
    instance.idle_check.return_value = []

    stop = threading.Event()
    stop.set()  # already set — loop should not enter even one full slice
    account._stop_event = stop

    account._run_listener_loop(folder="INBOX", on_message=lambda h: None,
                               max_iterations=3)

    # idle_check should NOT have been called when stop was set before entry
    instance.idle_check.assert_not_called()


def test_stop_listening_calls_idle_done_to_unblock(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """stop_listening must send DONE on the listener socket so a long-running
    idle_check returns promptly instead of waiting up to 9 minutes."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []

    # Pretend the listener has connected and is in IDLE
    account._listen_imap = instance
    account._listen_in_idle = True
    account._stop_event = threading.Event()

    account.stop_listening()

    assert account._stop_event.is_set()
    instance.idle_done.assert_called_once()


def test_stop_listening_skips_idle_done_if_not_in_idle(
    mock_imapclient_class, account: IMAPAccount,
) -> None:
    """If the listener isn't in IDLE, stop_listening should not call idle_done."""
    instance = mock_imapclient_class.return_value
    instance.capabilities.return_value = (b"IDLE",)
    instance.list_folders.return_value = []

    account._listen_imap = instance
    account._listen_in_idle = False
    account._stop_event = threading.Event()

    account.stop_listening()

    assert account._stop_event.is_set()
    instance.idle_done.assert_not_called()
