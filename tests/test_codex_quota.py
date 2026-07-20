"""Tests for :func:`lingtai.llm.openai.codex_quota.read_remaining_percent`.

No real Codex binary required — ``_fake_codex_app_server.py`` drives the
wire protocol via ``sys.executable``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lingtai.llm.openai import codex_quota


FAKE_APP_SERVER = Path(__file__).parent / "_fake_codex_app_server.py"


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
    p.write_text(json.dumps({
        "access_token": "sk-should-never-appear-in-any-assertion-or-log",
        "refresh_token": "rt-should-never-appear-either",
        "expires_at": 9999999999,
    }), encoding="utf-8")
    p.chmod(0o600)
    return p


def test_positive_remaining_percent(fake_binary, auth_file):
    fake_binary("ok")
    remaining = codex_quota.read_remaining_percent(auth_file)
    assert remaining == 90.0


def test_zero_remaining_percent(fake_binary, auth_file):
    fake_binary("exhausted")
    remaining = codex_quota.read_remaining_percent(auth_file)
    assert remaining == 0.0
