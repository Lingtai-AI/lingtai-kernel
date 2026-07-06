"""Regression tests: sanitize sender-controlled attachment filenames on read.

An inbound email's MIME filename is attacker-controlled; without sanitization
a name like ``../../evil.txt`` (or an absolute path, which a pathlib join
replaces entirely) escapes the ``imap/{address}/{folder}/{uid}/`` persist dir.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingtai.mcp_servers.imap.manager import (
    IMAPMailManager,
    _dedupe_name,
    _safe_attachment_name,
)


class FakeAccount:
    address = "me@example.com"

    def __init__(self, attachments_raw: list[dict]) -> None:
        self._attachments_raw = attachments_raw

    def fetch_full(self, folder: str, uid: str) -> dict:
        return {
            "from": "Sender <sender@example.com>",
            "from_address": "sender@example.com",
            "subject": "Hello",
            "body": "See attached.",
            "attachments_raw": self._attachments_raw,
        }


class FakeService:
    def __init__(self, account: FakeAccount) -> None:
        self.default_account = account
        self._account = account

    def get_account(self, address: str | None):
        return self._account


def _read(tmp_path: Path, attachments_raw: list[dict]) -> tuple[dict, Path]:
    manager = IMAPMailManager(
        FakeService(FakeAccount(attachments_raw)),
        working_dir=tmp_path,
        tcp_alias="/tmp/imap-bridge",
        on_inbound=lambda payload: None,
    )
    result = manager.handle({
        "action": "read",
        "email_id": "me@example.com:INBOX:42",
    })
    persist_dir = tmp_path / "imap" / "me@example.com" / "INBOX" / "42"
    return result, persist_dir


def _all_files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# _safe_attachment_name unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("report.pdf", "report.pdf"),
    ("../../../evil.txt", "evil.txt"),
    ("/tmp/evil.txt", "evil.txt"),
    ("C:\\evil.txt", "evil.txt"),
    ("..\\..\\evil.txt", "evil.txt"),
    ("nested/dir/file.txt", "file.txt"),
    ("", "attachment"),
    (".", "attachment"),
    ("..", "attachment"),
    ("../", "attachment"),
    (".bashrc", ".bashrc"),
])
def test_safe_attachment_name(raw: str, expected: str):
    assert _safe_attachment_name(raw) == expected


def test_dedupe_name_suffixes_before_extension():
    assert _dedupe_name("report.pdf", set()) == "report.pdf"
    assert _dedupe_name("report.pdf", {"report.pdf"}) == "report (1).pdf"
    assert _dedupe_name(
        "report.pdf", {"report.pdf", "report (1).pdf"}
    ) == "report (2).pdf"
    assert _dedupe_name("attachment", {"attachment"}) == "attachment (1)"


# ---------------------------------------------------------------------------
# _read integration tests
# ---------------------------------------------------------------------------

def test_traversal_filename_is_confined(tmp_path: Path):
    result, persist_dir = _read(tmp_path, [{
        "filename": "../../../evil.txt",
        "content_type": "text/plain",
        "data": b"payload",
    }])

    assert result["status"] == "ok"
    assert (persist_dir / "evil.txt").read_bytes() == b"payload"
    # Nothing escaped the persist dir.
    for f in _all_files(tmp_path):
        assert f.is_relative_to(persist_dir)
    saved = result["emails"][0]["attachments"][0]
    assert saved["path"] == str(persist_dir / "evil.txt")


def test_absolute_filename_is_confined(tmp_path: Path):
    result, persist_dir = _read(tmp_path, [{
        "filename": "/tmp/evil.txt",
        "content_type": "text/plain",
        "data": b"abs",
    }])

    assert result["status"] == "ok"
    assert (persist_dir / "evil.txt").read_bytes() == b"abs"
    assert not Path("/tmp/evil.txt").exists() or Path(
        "/tmp/evil.txt"
    ).read_bytes() != b"abs"


def test_backslash_traversal_is_confined(tmp_path: Path):
    result, persist_dir = _read(tmp_path, [{
        "filename": "..\\..\\evil.txt",
        "content_type": "text/plain",
        "data": b"win",
    }])

    assert result["status"] == "ok"
    assert (persist_dir / "evil.txt").read_bytes() == b"win"
    for f in _all_files(tmp_path):
        assert f.is_relative_to(persist_dir)


def test_empty_and_dot_filenames_fall_back(tmp_path: Path):
    result, persist_dir = _read(tmp_path, [
        {"filename": "", "content_type": "text/plain", "data": b"a"},
        {"filename": ".", "content_type": "text/plain", "data": b"b"},
        {"filename": "..", "content_type": "text/plain", "data": b"c"},
        {"filename": "../", "content_type": "text/plain", "data": b"d"},
    ])

    assert result["status"] == "ok"
    assert (persist_dir / "attachment").read_bytes() == b"a"
    assert (persist_dir / "attachment (1)").read_bytes() == b"b"
    assert (persist_dir / "attachment (2)").read_bytes() == b"c"
    assert (persist_dir / "attachment (3)").read_bytes() == b"d"


def test_duplicate_filenames_do_not_overwrite(tmp_path: Path):
    result, persist_dir = _read(tmp_path, [
        {"filename": "report.pdf", "content_type": "application/pdf",
         "data": b"first"},
        {"filename": "report.pdf", "content_type": "application/pdf",
         "data": b"second"},
    ])

    assert result["status"] == "ok"
    assert (persist_dir / "report.pdf").read_bytes() == b"first"
    assert (persist_dir / "report (1).pdf").read_bytes() == b"second"


def test_subdirectory_filename_does_not_error(tmp_path: Path):
    # Regression: 'nested/dir/file.txt' used to raise FileNotFoundError
    # (parent dir missing) and abort the whole read call.
    result, persist_dir = _read(tmp_path, [{
        "filename": "nested/dir/file.txt",
        "content_type": "text/plain",
        "data": b"deep",
    }])

    assert result["status"] == "ok"
    assert (persist_dir / "file.txt").read_bytes() == b"deep"


def test_saved_metadata_reflects_disk_name(tmp_path: Path):
    result, persist_dir = _read(tmp_path, [{
        "filename": "../../../evil.txt",
        "content_type": "text/plain",
        "data": b"meta",
    }])

    saved = result["emails"][0]["attachments"][0]
    assert saved["filename"] == Path(saved["path"]).name == "evil.txt"
    assert saved["original_filename"] == "../../../evil.txt"
    assert saved["size"] == 4
    assert saved["content_type"] == "text/plain"
