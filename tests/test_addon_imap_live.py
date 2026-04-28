"""End-to-end live smoke test for the IMAP addon.

Runs only when IMAP_LIVE_TEST=1. Requires a real Gmail (or compatible)
test account with IMAP enabled and an app password.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

LIVE = os.getenv("IMAP_LIVE_TEST") == "1"
EMAIL = os.getenv("IMAP_LIVE_EMAIL", "")
PASSWORD = os.getenv("IMAP_LIVE_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not (LIVE and EMAIL and PASSWORD),
    reason="set IMAP_LIVE_TEST=1, IMAP_LIVE_EMAIL, IMAP_LIVE_PASSWORD",
)


def test_connect_and_check_inbox(tmp_path: Path) -> None:
    from lingtai.addons.imap.account import IMAPAccount

    acct = IMAPAccount(
        email_address=EMAIL,
        email_password=PASSWORD,
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
        working_dir=tmp_path,
    )
    acct.connect()
    assert acct.connected
    envelopes = acct.fetch_envelopes("INBOX", n=3)
    print(f"{len(envelopes)} envelopes fetched")
    acct.disconnect()


def test_reconcile_round_trip(tmp_path: Path) -> None:
    """Reconcile twice — second call should find zero new (no test mail sent)."""
    from lingtai.addons.imap.account import IMAPAccount

    acct = IMAPAccount(
        email_address=EMAIL,
        email_password=PASSWORD,
        working_dir=tmp_path,
    )
    acct.connect()
    first = acct.reconcile("INBOX")
    print(f"bootstrap delivered {len(first)} envelopes")
    second = acct.reconcile("INBOX")
    assert second == [], "second reconcile should be empty (no new mail expected)"
    acct.disconnect()
