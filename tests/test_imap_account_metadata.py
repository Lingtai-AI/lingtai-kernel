"""Regression tests for account-scoped IMAP response metadata."""
from __future__ import annotations

from pathlib import Path

from lingtai.mcp_servers.imap.manager import IMAPMailManager


PRIMARY = "primary@example.com"
SECONDARY = "secondary@example.com"


class FakeAccount:
    def __init__(self, address: str) -> None:
        self.address = address
        self.check_calls: list[tuple[str, int]] = []
        self.read_calls: list[tuple[str, str]] = []
        self.flag_calls: list[tuple[str, str, list[str], str]] = []

    def fetch_envelopes(self, folder: str, n: int) -> list[dict]:
        self.check_calls.append((folder, n))
        return [{
            "email_id": f"{self.address}:{folder}:7",
            "subject": self.address,
        }]

    def fetch_full(self, folder: str, uid: str) -> dict:
        self.read_calls.append((folder, uid))
        return {
            "from": "sender@example.com",
            "subject": "Synthetic message",
            "body": "synthetic body",
            "attachments_raw": [],
        }

    def store_flags(
        self,
        folder: str,
        uid: str,
        flags: list[str],
        action: str = "+FLAGS",
    ) -> bool:
        self.flag_calls.append((folder, uid, flags, action))
        return True


class FakeService:
    def __init__(self) -> None:
        self.accounts = [FakeAccount(PRIMARY), FakeAccount(SECONDARY)]
        self.default_account = self.accounts[0]
        self._account_map = {account.address: account for account in self.accounts}

    def get_account(self, address: str | None):
        if address is None:
            return self.default_account
        return self._account_map.get(address)


def _manager(tmp_path: Path) -> tuple[IMAPMailManager, FakeService]:
    service = FakeService()
    manager = IMAPMailManager(
        service,
        working_dir=tmp_path,
        tcp_alias="synthetic-bridge",
        on_inbound=lambda payload: None,
    )
    return manager, service


def test_check_reports_requested_account_and_account_scoped_id(tmp_path: Path):
    manager, service = _manager(tmp_path)

    result = manager.handle({
        "action": "check",
        "account": SECONDARY,
        "folder": "INBOX",
    })

    assert result["status"] == "ok"
    assert result["account"] == SECONDARY
    assert result["emails"][0]["email_id"] == f"{SECONDARY}:INBOX:7"
    assert service.accounts[0].check_calls == []
    assert service.accounts[1].check_calls == [("INBOX", 10)]


def test_read_reports_requested_account_and_preserves_compound_id(tmp_path: Path):
    manager, service = _manager(tmp_path)
    email_id = f"{SECONDARY}:INBOX:42"

    result = manager.handle({
        "action": "read",
        "account": SECONDARY,
        "email_id": email_id,
    })

    assert result["status"] == "ok"
    assert result["account"] == SECONDARY
    assert result["emails"][0]["email_id"] == email_id
    assert service.accounts[0].read_calls == []
    assert service.accounts[1].read_calls == [("INBOX", "42")]
    assert (tmp_path / "imap" / SECONDARY / "INBOX" / "42" / "message.json").is_file()


def test_flag_reports_requested_account_and_preserves_compound_id(tmp_path: Path):
    manager, service = _manager(tmp_path)
    email_id = f"{SECONDARY}:INBOX:42"

    result = manager.handle({
        "action": "flag",
        "account": SECONDARY,
        "email_id": email_id,
        "flags": {"seen": True},
    })

    assert result["status"] == "ok"
    assert result["account"] == SECONDARY
    assert result["results"] == [{"email_id": email_id, "flagged": True}]
    assert service.accounts[0].flag_calls == []
    assert service.accounts[1].flag_calls == [
        ("INBOX", "42", ["\\Seen"], "+FLAGS"),
    ]
