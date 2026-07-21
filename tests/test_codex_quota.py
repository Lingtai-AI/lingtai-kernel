"""Tests for :func:`lingtai.llm.openai.codex_quota.read_remaining_percent`.

No real Codex binary required — ``_fake_codex_app_server.py`` drives the
wire protocol via ``sys.executable``.
"""

from __future__ import annotations

import base64
import json
import stat
import sys
from pathlib import Path

import pytest

from lingtai.llm.openai import codex_quota


FAKE_APP_SERVER = Path(__file__).parent / "_fake_codex_app_server.py"


def _fake_jwt(payload: dict) -> str:
    def _segment(value: dict) -> str:
        encoded = base64.urlsafe_b64encode(json.dumps(value).encode("utf-8"))
        return encoded.rstrip(b"=").decode("ascii")

    return f"{_segment({'alg': 'none'})}.{_segment(payload)}.signature"


@pytest.fixture()
def fake_binary(monkeypatch):
    def _configure(mode: str = "ok"):
        monkeypatch.setenv("LINGTAI_FAKE_APP_SERVER_MODE", mode)
        monkeypatch.setattr(codex_quota, "_CODEX_BIN", sys.executable)
        monkeypatch.setattr(codex_quota, "_APP_SERVER_ARGS", (str(FAKE_APP_SERVER),))
        return None

    return _configure


@pytest.fixture()
def auth_file(tmp_path):
    p = tmp_path / "codex-auth.json"
    p.write_text(
        json.dumps(
            {
                "access_token": _fake_jwt(
                    {
                        "iat": 1_700_000_000,
                        "https://api.openai.com/auth": {
                            "chatgpt_account_id": "acct-test"
                        },
                    }
                ),
                "refresh_token": "rt-should-never-appear-either",
                "expires_at": 9999999999,
            }
        ),
        encoding="utf-8",
    )
    p.chmod(0o600)
    return p


def test_temp_home_materializes_native_codex_auth_without_mutating_source(auth_file):
    source_before = auth_file.read_bytes()

    home, tmpdir = codex_quota._prepare_temp_codex_home(auth_file)
    try:
        native_path = home / "auth.json"
        native = json.loads(native_path.read_text(encoding="utf-8"))
        source = json.loads(source_before)

        assert native == {
            "OPENAI_API_KEY": None,
            "auth_mode": "chatgpt",
            "last_refresh": "2023-11-14T22:13:20+00:00",
            "tokens": {
                "access_token": source["access_token"],
                "refresh_token": source["refresh_token"],
                "account_id": "acct-test",
                "id_token": source["access_token"],
            },
        }
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert stat.S_IMODE(native_path.stat().st_mode) == 0o600
        assert auth_file.read_bytes() == source_before
    finally:
        tmpdir.cleanup()


def test_positive_remaining_percent(fake_binary, auth_file):
    fake_binary("ok")
    remaining = codex_quota.read_remaining_percent(auth_file)
    assert remaining == 90.0


def test_zero_remaining_percent(fake_binary, auth_file):
    fake_binary("exhausted")
    remaining = codex_quota.read_remaining_percent(auth_file)
    assert remaining == 0.0
