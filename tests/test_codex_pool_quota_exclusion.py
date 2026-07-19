"""Tests for quota-aware exclusion at NEW ``codex-pool`` selection.

At NEW ``codex-pool`` account selection only, exclude an account when a
fresh quota read explicitly proves ``remaining_percent <= 0``; otherwise
fail OPEN (include it). Configured weights are preserved among the
remaining eligible accounts, and ``source_index`` stays anchored to the
full validated list. If every validated account is proven exhausted,
raise ``CodexPoolAllAccountsExhaustedError`` rather than silently
selecting one. Every call reads fresh (no cache), so a since-reset
account re-enters on the very next call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.auth import codex_pool
from lingtai.llm.openai import codex_quota


@pytest.fixture()
def tui_dir(tmp_path, monkeypatch):
    d = tmp_path / "tui"
    d.mkdir()
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(d))
    return d


def _write_pool(tui_dir: Path, accounts):
    path = tui_dir / "codex-auth-pool.json"
    path.write_text(json.dumps({"version": 1, "accounts": accounts}), encoding="utf-8")
    return path


def _anchor_with_started_at(dir_: Path, started_at) -> str:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / ".agent.json").write_text(
        json.dumps({"started_at": started_at, "molt_count": 0}), encoding="utf-8"
    )
    return str(dir_ / "init.json")


def _patch_exhausted(monkeypatch, exhausted_paths: set):
    monkeypatch.setattr(
        codex_pool, "_is_proven_exhausted", lambda auth_path: auth_path in exhausted_paths
    )


# Unavailable / error / malformed / missing quota -> fail OPEN


def test_unavailable_quota_fails_open(monkeypatch, tui_dir, tmp_path):
    monkeypatch.setattr(codex_quota, "read_remaining_percent", lambda _: None)
    _write_pool(tui_dir, [{"path": "acct.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    result = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
    assert result is not None
    assert result["selection"]["source_ref"] == "acct.json"


# Weights preserved among survivors; source_index anchored to the FULL list


def test_weights_preserved_and_source_index_stays_original(monkeypatch, tui_dir, tmp_path):
    excluded_path = str((tui_dir / "excluded.json").resolve())
    _patch_exhausted(monkeypatch, exhausted_paths={excluded_path})
    _write_pool(tui_dir, [
        {"path": "excluded.json", "weight": 1000},  # idx 0, would dominate if not excluded
        {"path": "light.json", "weight": 1},         # idx 1
        {"path": "heavy.json", "weight": 9},          # idx 2
    ])

    picks = {"light.json": 0, "heavy.json": 0}
    for i in range(200):
        anchor = _anchor_with_started_at(tui_dir.parent / f"agent{i}", f"seed-{i}")
        result = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
        assert result is not None
        ref = result["selection"]["source_ref"]
        assert ref != "excluded.json"
        assert result["selection"]["source_index"] in (1, 2)
        assert result["selection"]["pool_size"] == 3  # full count, not filtered count
        picks[ref] += 1

    assert picks["light.json"] + picks["heavy.json"] == 200
    assert picks["heavy.json"] > picks["light.json"] * 3


# All accounts exhausted -> explicit error


def test_all_accounts_exhausted_raises(monkeypatch, tui_dir, tmp_path):
    a_path = str((tui_dir / "a.json").resolve())
    b_path = str((tui_dir / "b.json").resolve())
    _patch_exhausted(monkeypatch, exhausted_paths={a_path, b_path})
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 5},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    with pytest.raises(codex_pool.CodexPoolAllAccountsExhaustedError):
        codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})


# Post-reset re-entry — no cache, so recovery is immediate


def test_post_reset_reentry_is_immediate(monkeypatch, tui_dir, tmp_path):
    _write_pool(tui_dir, [{"path": "acct.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    call_count = {"n": 0}

    def _fake_is_proven_exhausted(auth_path):
        call_count["n"] += 1
        return call_count["n"] == 1  # exhausted first call, recovered thereafter

    monkeypatch.setattr(codex_pool, "_is_proven_exhausted", _fake_is_proven_exhausted)

    with pytest.raises(codex_pool.CodexPoolAllAccountsExhaustedError):
        codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})

    result = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
    assert result is not None
    assert result["selection"]["source_ref"] == "acct.json"
    assert call_count["n"] == 2
