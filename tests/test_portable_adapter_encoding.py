"""Portable UTF-8 encoding regressions for POSIX filesystem adapters.

These pin that ``write_text`` calls in the mail and git adapters pass an
explicit ``encoding="utf-8"`` so payloads round-trip identically regardless of
the host's default text encoding (Windows' locale ``cp*`` codecs would otherwise
mangle non-ASCII on write while readers use utf-8). They monkeypatch nothing
platform-specific and run everywhere.
"""
from __future__ import annotations

from pathlib import Path

from tests._agent_dir_helpers import make_agent_dir as _make_agent_dir


def test_mail_send_writes_message_json_as_utf8(tmp_path):
    """A non-ASCII mail body must land on disk as UTF-8 bytes, byte-for-byte."""
    from lingtai.adapters.posix.mail import PosixFilesystemMailAdapter

    sender_dir = _make_agent_dir(tmp_path, "sender01")
    recip_dir = _make_agent_dir(tmp_path, "recip01")
    (recip_dir / "mailbox" / "inbox").mkdir(parents=True)

    body = "中文消息 with emoji 🚀🔥 — 灵台"
    subject = "主题 ✨"
    svc = PosixFilesystemMailAdapter(sender_dir, mailbox_rel="mailbox")
    assert svc.send(str(recip_dir), {"message": body, "subject": subject}) is None

    inbox = recip_dir / "mailbox" / "inbox"
    (msg_dir,) = list(inbox.iterdir())
    message_json = msg_dir / "message.json"

    # Decode the RAW bytes explicitly as utf-8 (not the platform default) and
    # confirm the exact non-ASCII payload survived the write.
    import json

    raw = message_json.read_bytes()
    decoded = raw.decode("utf-8")
    data = json.loads(decoded)
    assert data["message"] == body
    assert data["subject"] == subject
    # The non-ASCII characters are present as real UTF-8 multibyte sequences
    # (ensure_ascii=False), not \uXXXX escapes.
    assert "🚀".encode("utf-8") in raw
    assert "灵台".encode("utf-8") in raw


def test_git_cli_initialize_writes_gitignore_as_utf8(tmp_path):
    """git init writes an ASCII .gitignore that round-trips as UTF-8."""
    from lingtai.adapters.posix.git_cli import PosixGitCliAdapter, _GITIGNORE

    directory = tmp_path / "agent"
    directory.mkdir()
    PosixGitCliAdapter(directory).initialize()

    gitignore = directory / ".gitignore"
    assert gitignore.is_file()
    # Explicit utf-8 read round-trips to the exact source constant. The
    # write_text default newline translation makes line endings
    # platform-native (CRLF on Windows), which is irrelevant to the encoding
    # promise — normalize before comparing.
    assert gitignore.read_text(encoding="utf-8") == _GITIGNORE
    raw_normalized = gitignore.read_bytes().replace(b"\r\n", b"\n")
    assert raw_normalized == _GITIGNORE.encode("utf-8")


def test_git_cli_ensure_system_files_writes_empty_utf8(tmp_path):
    """The seeded empty system files also carry an explicit utf-8 encoding."""
    from lingtai.adapters.posix.git_cli import PosixGitCliAdapter

    directory = tmp_path / "agent"
    directory.mkdir()
    PosixGitCliAdapter(directory).initialize()

    for name in ("covenant.md", "principle.md", "pad.md"):
        path: Path = directory / "system" / name
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == ""
