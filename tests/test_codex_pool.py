"""Tests for the ``codex-pool`` auth pool provider.

``codex-pool`` load-balances a Codex agent across several existing Codex OAuth
token files with a *sticky-per-agent-session* weighted choice, WITHOUT changing
provider ``codex``. These tests exercise:

  * pool file resolution (default location + ``codex_auth_pool_path`` override,
    ``$LINGTAI_TUI_DIR`` honored);
  * schema validation (disabled / blank-path / bad-weight accounts dropped);
  * weighted selection landing on the higher-weight account;
  * stickiness — same anchor + same ``.agent.json`` ``started_at`` -> same path
    across repeated selection AND across ``molt_count`` changes;
  * seed sensitivity — changing ``started_at`` can change the selection;
  * fallback to the legacy default token path when the pool is unusable;
  * provider ``codex`` is unaffected and never reads the pool file;
  * the ``codex-pool`` factory injects the selected path as ``codex_auth_path``
    into the reused Codex adapter / ``CodexTokenManager``.

No network calls: ``CodexTokenManager`` is mocked where an adapter is built, and
all token/pool files are real temp files. No token contents are read or logged.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

import lingtai  # noqa: F401  (registers adapters / loads service module)
from lingtai.auth import codex_pool
from lingtai.llm.service import LLMService


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


@pytest.fixture()
def tui_dir(tmp_path, monkeypatch):
    """A temp TUI dir wired via ``LINGTAI_TUI_DIR``."""
    d = tmp_path / "tui"
    d.mkdir()
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(d))
    return d


def _write_pool(tui_dir: Path, accounts, *, version=1, name="codex-auth-pool.json"):
    path = tui_dir / name
    path.write_text(json.dumps({"version": version, "accounts": accounts}), encoding="utf-8")
    return path


def _anchor_with_started_at(dir_: Path, started_at, *, molt_count=0) -> str:
    """Write ``<dir>/.agent.json`` and return the anchor (``init.json`` path)."""
    dir_.mkdir(parents=True, exist_ok=True)
    payload = {"molt_count": molt_count}
    if started_at is not None:
        payload["started_at"] = started_at
    (dir_ / ".agent.json").write_text(json.dumps(payload), encoding="utf-8")
    return str(dir_ / "init.json")


def _mock_mgr():
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    cls.return_value.get_access_token.return_value = "fake-token"
    cls.return_value.get_account_id.return_value = None
    return mgr, cls


# --------------------------------------------------------------------------
# Pool file resolution
# --------------------------------------------------------------------------


def test_resolve_pool_path_default_uses_tui_dir(tui_dir):
    assert codex_pool.resolve_codex_pool_path() == tui_dir / "codex-auth-pool.json"


def test_resolve_pool_path_override_relative_to_tui(tui_dir):
    defaults = {"codex_auth_pool_path": "pools/custom.json"}
    assert codex_pool.resolve_codex_pool_path(defaults) == tui_dir / "pools/custom.json"


def test_resolve_pool_path_override_absolute(tui_dir, tmp_path):
    abs_path = tmp_path / "elsewhere" / "pool.json"
    defaults = {"codex_auth_pool_path": str(abs_path)}
    assert codex_pool.resolve_codex_pool_path(defaults) == abs_path


def test_resolve_pool_path_override_tilde(tui_dir):
    defaults = {"codex_auth_pool_path": "~/custom-pool.json"}
    assert codex_pool.resolve_codex_pool_path(defaults) == Path("~/custom-pool.json").expanduser()


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------


def test_load_pool_filters_invalid_accounts(tui_dir):
    pool = _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 2, "enabled": True},
        {"path": "disabled.json", "weight": 5, "enabled": False},
        {"path": "  ", "weight": 1},               # blank path
        {"path": "zero.json", "weight": 0},         # non-positive
        {"path": "neg.json", "weight": -3},         # negative
        {"path": "bad.json", "weight": "lots"},     # non-numeric
        {"path": "boolw.json", "weight": True},     # bool rejected
        {"weight": 1},                              # missing path
        "not-a-dict",
    ])
    accounts = codex_pool.load_codex_auth_pool(pool)
    assert accounts == [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 2},
    ]


def test_load_pool_missing_file_returns_empty(tui_dir):
    assert codex_pool.load_codex_auth_pool(tui_dir / "nope.json") == []


def test_load_pool_malformed_json_returns_empty(tui_dir):
    p = tui_dir / "codex-auth-pool.json"
    p.write_text("{not json", encoding="utf-8")
    assert codex_pool.load_codex_auth_pool(p) == []


def test_load_pool_non_dict_root_returns_empty(tui_dir):
    p = tui_dir / "codex-auth-pool.json"
    p.write_text(json.dumps([{"path": "a.json", "weight": 1}]), encoding="utf-8")
    assert codex_pool.load_codex_auth_pool(p) == []


def test_load_pool_accepts_whole_float_weight(tui_dir):
    pool = _write_pool(tui_dir, [{"path": "a.json", "weight": 2.0}])
    assert codex_pool.load_codex_auth_pool(pool) == [{"path": "a.json", "weight": 2}]


def test_load_pool_missing_weight_defaults_to_one(tui_dir):
    """A hand-edited account with a path but no ``weight`` is kept at weight 1.

    The TUI always writes an explicit weight, but the parser must not drop a
    path-only account — a missing weight defaults to 1 (GLM review C1).
    """
    pool = _write_pool(tui_dir, [{"path": "only-path.json"}])
    assert codex_pool.load_codex_auth_pool(pool) == [{"path": "only-path.json", "weight": 1}]


def test_missing_weight_account_is_selectable(tui_dir, tmp_path):
    """A sole path-only (weight-defaulted) account is actually selected."""
    _write_pool(tui_dir, [{"path": "only-path.json"}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    chosen = codex_pool.select_codex_pool_auth_path({"codex_session_anchor": anchor})
    assert chosen == str(tui_dir / "only-path.json")


# --------------------------------------------------------------------------
# Weighted selection + path resolution
# --------------------------------------------------------------------------


def test_selection_resolves_relative_and_absolute(tui_dir, tmp_path):
    _write_pool(tui_dir, [{"path": "codex-auth/work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    chosen = codex_pool.select_codex_pool_auth_path({"codex_session_anchor": anchor})
    assert chosen == str(tui_dir / "codex-auth/work.json")


def test_selection_honors_absolute_account_path(tui_dir, tmp_path):
    abs_token = tmp_path / "custom" / "codex-token.json"
    _write_pool(tui_dir, [{"path": str(abs_token), "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    chosen = codex_pool.select_codex_pool_auth_path({"codex_session_anchor": anchor})
    assert chosen == str(abs_token)


def test_weighted_selection_favors_heavier_account(tui_dir, tmp_path):
    """Across many distinct seeds the heavy-weighted account wins the majority."""
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    heavy = 0
    total = 200
    for i in range(total):
        anchor = _anchor_with_started_at(tui_dir / f"agent{i}", f"start-{i}")
        chosen = codex_pool.select_codex_pool_auth_path({"codex_session_anchor": anchor})
        if chosen == str(tui_dir / "heavy.json"):
            heavy += 1
    # weight 9/10 -> expect a large majority; a loose bound keeps this stable.
    assert heavy > total * 0.7


# --------------------------------------------------------------------------
# Stickiness
# --------------------------------------------------------------------------


def test_sticky_same_anchor_and_started_at(tui_dir, tmp_path):
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "2026-07-04T00:00:00Z")
    defaults = {"codex_session_anchor": anchor}
    first = codex_pool.select_codex_pool_auth_path(defaults)
    for _ in range(5):
        assert codex_pool.select_codex_pool_auth_path(defaults) == first


def test_sticky_across_molt_count_changes(tui_dir, tmp_path):
    """molt_count must NOT affect auth-pool selection."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ])
    agent_dir = tmp_path / "agent"
    anchor = _anchor_with_started_at(agent_dir, "fixed-start", molt_count=0)
    defaults = {"codex_session_anchor": anchor}
    first = codex_pool.select_codex_pool_auth_path(defaults)
    for molt in (1, 2, 7, 42):
        _anchor_with_started_at(agent_dir, "fixed-start", molt_count=molt)
        assert codex_pool.select_codex_pool_auth_path(defaults) == first


def test_started_at_change_can_change_selection(tui_dir, tmp_path):
    """Different ``started_at`` produces a different seed; at least one differs."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
        {"path": "d.json", "weight": 1},
    ])
    agent_dir = tmp_path / "agent"
    anchor = _anchor_with_started_at(agent_dir, "start-A")
    defaults = {"codex_session_anchor": anchor}
    base = codex_pool.select_codex_pool_auth_path(defaults)
    seen_change = False
    for i in range(20):
        _anchor_with_started_at(agent_dir, f"start-{i}")
        if codex_pool.select_codex_pool_auth_path(defaults) != base:
            seen_change = True
            break
    assert seen_change


def test_seed_fallback_agent_id_when_no_started_at(tui_dir, tmp_path):
    """No ``started_at`` -> agent_id seeds the (still deterministic) choice."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", None)  # no started_at
    defaults = {"codex_session_anchor": anchor, "agent_id": "agent-123"}
    first = codex_pool.select_codex_pool_auth_path(defaults)
    assert first is not None
    # Deterministic across repeats.
    assert codex_pool.select_codex_pool_auth_path(defaults) == first


# --------------------------------------------------------------------------
# Fallback when pool unusable
# --------------------------------------------------------------------------


def test_missing_pool_returns_none(tui_dir, tmp_path):
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    assert codex_pool.select_codex_pool_auth_path({"codex_session_anchor": anchor}) is None


def test_empty_accounts_returns_none(tui_dir, tmp_path):
    _write_pool(tui_dir, [])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    assert codex_pool.select_codex_pool_auth_path({"codex_session_anchor": anchor}) is None


def test_all_invalid_accounts_returns_none(tui_dir, tmp_path):
    _write_pool(tui_dir, [
        {"path": "x.json", "weight": 0},
        {"path": "  ", "weight": 1},
        {"path": "y.json", "enabled": False, "weight": 3},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    assert codex_pool.select_codex_pool_auth_path({"codex_session_anchor": anchor}) is None


# --------------------------------------------------------------------------
# Factory integration: codex-pool injects codex_auth_path into the reused adapter
# --------------------------------------------------------------------------


def _codex_pool_adapter(provider, defaults):
    svc = LLMService(
        provider=provider, model="gpt-5.5",
        provider_defaults={provider: defaults},
    )
    return svc.get_adapter(provider)


def test_codex_pool_factory_injects_selected_path(tui_dir, tmp_path):
    token = tui_dir / "work.json"
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        _codex_pool_adapter("codex-pool", {"codex_session_anchor": anchor})
        # The reused CodexTokenManager is constructed with the pool-selected path.
        cls.assert_called_with(token_path=str(token))
    finally:
        mgr.stop()


def test_codex_pool_alias_underscore_registered(tui_dir, tmp_path):
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        _codex_pool_adapter("codex_pool", {"codex_session_anchor": anchor})
        cls.assert_called_with(token_path=str(tui_dir / "work.json"))
    finally:
        mgr.stop()


def test_codex_pool_missing_pool_falls_back_to_default_path(tui_dir, tmp_path):
    """No pool file -> factory injects no path -> manager uses legacy default."""
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        _codex_pool_adapter("codex-pool", {"codex_session_anchor": anchor})
        # No token_path kwarg -> CodexTokenManager() legacy default behavior.
        cls.assert_called_with()
    finally:
        mgr.stop()


# --------------------------------------------------------------------------
# Provider ``codex`` is unchanged and never reads the pool file
# --------------------------------------------------------------------------


def test_codex_provider_ignores_pool_file(tui_dir, tmp_path):
    """A populated pool file must NOT affect provider ``codex``."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        _codex_pool_adapter("codex", {"codex_session_anchor": anchor})
        # codex with no codex_auth_path -> legacy default (no token_path kwarg).
        cls.assert_called_with()
    finally:
        mgr.stop()


def test_codex_provider_honors_explicit_auth_path(tui_dir, tmp_path):
    """Existing ``codex_auth_path`` behavior is unchanged."""
    explicit = str(tmp_path / "explicit.json")
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        _codex_pool_adapter("codex", {
            "codex_session_anchor": anchor,
            "codex_auth_path": explicit,
        })
        cls.assert_called_with(token_path=explicit)
    finally:
        mgr.stop()
