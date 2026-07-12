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
    into the reused Codex adapter / ``CodexTokenManager``;
  * model-classified pools (v2 ``models`` map): exact case-sensitive category
    lookup by the configured model, category-relative selection/metadata
    (``model_scope``), legacy fallback when no exact category exists, and
    byte-identical v1 behavior regardless of the ``model`` argument.

No network calls: ``CodexTokenManager`` is mocked where an adapter is built, and
all token/pool files are real temp files. No token contents are read or logged.
"""

from __future__ import annotations

import hashlib
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


def _write_pool_v2(tui_dir: Path, models, *, version=2, accounts=None, name="codex-auth-pool.json"):
    """Write a model-classified (v2) pool file: top-level ``models`` map.

    ``accounts`` writes an additional flat v1 list beside ``models`` (a mixed
    file) — used to pin that ``models`` is the sole source of truth.
    """
    payload: dict = {"version": version, "models": models}
    if accounts is not None:
        payload["accounts"] = accounts
    path = tui_dir / name
    path.write_text(json.dumps(payload), encoding="utf-8")
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


def _codex_mgr_factory(*a, token_path=None, **kw):
    """A per-``token_path`` fake ``CodexTokenManager`` used by the failover
    harnesses: ``._path`` echoes the injected path, the access token is
    ``tok:<path>`` (so a faked ``openai.OpenAI`` can route by api_key), and the
    account id is ``None``. Shared to avoid re-declaring this in every harness."""
    m = mock.MagicMock()
    m._path = token_path or "LEGACY_DEFAULT"
    m.get_access_token.return_value = f"tok:{token_path}"
    m.get_account_id.return_value = None
    return m


def _start_codex_mgr_mock():
    """Start (and return) a ``CodexTokenManager`` patcher whose instances come
    from :func:`_codex_mgr_factory`. Caller stops the returned patcher."""
    import lingtai.auth.codex as codex_mod
    p = mock.patch.object(codex_mod, "CodexTokenManager")
    p.start().side_effect = _codex_mgr_factory
    return p


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
# Selection metadata (non-secret source attribution)
# --------------------------------------------------------------------------


def test_select_auth_returns_nonsecret_selection_metadata(tui_dir, tmp_path):
    _write_pool(tui_dir, [
        {"path": "codex-auth/a.json", "weight": 1},
        {"path": "codex-auth/b.json", "weight": 3},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    defaults = {"codex_session_anchor": anchor}

    sel = codex_pool.select_codex_pool_auth(defaults)
    assert sel is not None
    assert set(sel) == {"auth_path", "selection"}
    # Same choice as the path-only helper (one selection, two views).
    assert sel["auth_path"] == codex_pool.select_codex_pool_auth_path(defaults)

    meta = sel["selection"]
    assert set(meta) == {
        "source_ref", "source_index", "pool_size", "weight", "auth_path_sha8",
        "model_scope",
    }
    refs = ["codex-auth/a.json", "codex-auth/b.json"]
    assert meta["source_ref"] in refs
    assert meta["source_index"] == refs.index(meta["source_ref"])
    assert meta["pool_size"] == 2
    assert meta["weight"] == (1 if meta["source_ref"] == refs[0] else 3)
    expected_hash = hashlib.sha256(sel["auth_path"].encode("utf-8")).hexdigest()[:8]
    assert meta["auth_path_sha8"] == expected_hash
    # A flat (v1) pool is not model-classified.
    assert meta["model_scope"] is None


def test_select_auth_missing_pool_returns_none(tui_dir, tmp_path):
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    assert codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}) is None


def test_selection_metadata_contains_no_secrets(tui_dir, tmp_path):
    """The selection dict never carries token contents or resolved absolute paths."""
    secret = "SECRET-token-value-do-not-log"
    token = tui_dir / "work.json"
    token.write_text(json.dumps({"access_token": secret}), encoding="utf-8")
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
    dumped = json.dumps(sel["selection"])
    assert secret not in dumped
    assert "access_token" not in dumped
    # A relative pool ref stays relative in the metadata — the resolved
    # absolute path lives only in ``auth_path`` (injected, not logged).
    assert str(tui_dir) not in dumped


def test_selection_primary_redacts_absolute_source_ref(tui_dir, tmp_path):
    """An ABSOLUTE primary account ref is redacted in the selection metadata too
    (consistent with failover alternates); identity via auth_path_sha8."""
    abs_token = tmp_path / "private" / "codex-auth.json"
    _write_pool(tui_dir, [{"path": str(abs_token), "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
    meta = sel["selection"]
    # auth_path (for injection) is the real resolved path; source_ref is redacted.
    assert sel["auth_path"] == str(abs_token)
    assert meta["source_ref"] == codex_pool.ABSOLUTE_REF_REDACTED
    assert str(abs_token) not in json.dumps(meta)
    assert str(tmp_path) not in json.dumps(meta)
    assert meta["auth_path_sha8"] == hashlib.sha256(
        str(abs_token).encode("utf-8")
    ).hexdigest()[:8]


# --------------------------------------------------------------------------
# Model-classified pools (v2 ``models`` map)
# --------------------------------------------------------------------------


def test_v2_load_returns_exact_category(tui_dir):
    pool = _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 3}],
        "gpt-5.5": [{"path": "old.json", "weight": 1}],
    })
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.6-sol") == [
        {"path": "sol.json", "weight": 3},
    ]
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.5") == [
        {"path": "old.json", "weight": 1},
    ]


def test_v2_load_no_category_or_no_model_returns_empty(tui_dir):
    pool = _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 1}],
    })
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.6-terra") == []
    # ``model`` unavailable (bare harness): a classified pool has no category
    # for "no model" — legacy fallback, never a merged/arbitrary category.
    assert codex_pool.load_codex_auth_pool(pool, model=None) == []
    assert codex_pool.load_codex_auth_pool(pool) == []


def test_v2_load_models_is_sole_source_of_truth(tui_dir):
    """A mixed file: ``models`` wins; the flat ``accounts`` list is ignored."""
    pool = _write_pool_v2(
        tui_dir,
        {"gpt-5.6-sol": [{"path": "sol.json", "weight": 1}]},
        accounts=[{"path": "flat.json", "weight": 5}],
    )
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.6-sol") == [
        {"path": "sol.json", "weight": 1},
    ]
    # Non-matching model never falls back to ``accounts``.
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.5") == []
    assert codex_pool.load_codex_auth_pool(pool) == []


def test_v2_load_per_category_validation(tui_dir):
    """The existing per-entry validation applies inside a category."""
    pool = _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "a.json", "weight": 1},
            {"path": "disabled.json", "weight": 5, "enabled": False},
            {"path": "  ", "weight": 1},            # blank path
            {"path": "zero.json", "weight": 0},      # non-positive
            {"path": "boolw.json", "weight": True},  # bool rejected
            {"path": "noweight.json"},               # defaults to 1
            "not-a-dict",
        ],
        "gpt-5.5": "not-a-list",
    })
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.6-sol") == [
        {"path": "a.json", "weight": 1},
        {"path": "noweight.json", "weight": 1},
    ]
    # An invalid category value behaves like a missing category.
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.5") == []


def test_v2_selection_stays_inside_the_exact_category(tui_dir, tmp_path):
    """Across many seeds, selection never leaves the configured model's category."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "sol-a.json", "weight": 1},
            {"path": "sol-b.json", "weight": 1},
        ],
        "gpt-5.5": [{"path": "old.json", "weight": 100}],
    })
    sol_paths = {str(tui_dir / "sol-a.json"), str(tui_dir / "sol-b.json")}
    for i in range(30):
        anchor = _anchor_with_started_at(tmp_path / f"agent{i}", f"start-{i}")
        chosen = codex_pool.select_codex_pool_auth_path(
            {"codex_session_anchor": anchor}, model="gpt-5.6-sol",
        )
        assert chosen in sol_paths


def test_v2_no_exact_category_returns_none(tui_dir, tmp_path):
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 1}],
        "gpt-5.5": [{"path": "old.json", "weight": 1}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    defaults = {"codex_session_anchor": anchor}
    assert codex_pool.select_codex_pool_auth(defaults, model="gpt-5.6-terra") is None
    assert codex_pool.select_codex_pool_auth_path(defaults, model="gpt-5.6-terra") is None


def test_v2_no_fuzzy_matching(tui_dir, tmp_path):
    """Exact, case-sensitive equality only — no prefix/family/normalization."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 1}],
        "gpt-5.5": [{"path": "old.json", "weight": 1}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    defaults = {"codex_session_anchor": anchor}
    for near_miss in ("gpt-5.6", "gpt-5.5 ", " gpt-5.5", "GPT-5.5", ""):
        assert codex_pool.select_codex_pool_auth(defaults, model=near_miss) is None


def test_v2_all_invalid_category_returns_none(tui_dir, tmp_path):
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "x.json", "weight": 0},
            {"path": "y.json", "enabled": False, "weight": 3},
        ],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    defaults = {"codex_session_anchor": anchor}
    assert codex_pool.select_codex_pool_auth(defaults, model="gpt-5.6-sol") is None


def test_v1_selection_unchanged_by_model_argument(tui_dir, tmp_path):
    """Zero churn for flat pools: any ``model`` value picks the same account."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 2},
        {"path": "c.json", "weight": 1},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    defaults = {"codex_session_anchor": anchor}
    base = codex_pool.select_codex_pool_auth(defaults)
    assert base is not None
    for model in (None, "gpt-5.5", "gpt-5.6-sol", "anything"):
        sel = codex_pool.select_codex_pool_auth(defaults, model=model)
        assert sel["auth_path"] == base["auth_path"]
        assert sel["selection"]["model_scope"] is None


def test_v2_per_model_weights_within_category(tui_dir, tmp_path):
    """One account may carry different weights per category; each category's
    distribution follows its own weights (mirrors the v1 weighted test)."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "shared.json", "weight": 9},
            {"path": "other.json", "weight": 1},
        ],
        "gpt-5.5": [
            {"path": "shared.json", "weight": 1},
            {"path": "other.json", "weight": 9},
        ],
    })
    total = 200
    for model, heavy_path in (
        ("gpt-5.6-sol", str(tui_dir / "shared.json")),
        ("gpt-5.5", str(tui_dir / "other.json")),
    ):
        heavy = 0
        for i in range(total):
            anchor = _anchor_with_started_at(tui_dir / f"agent-{model}-{i}", f"start-{i}")
            chosen = codex_pool.select_codex_pool_auth_path(
                {"codex_session_anchor": anchor}, model=model,
            )
            if chosen == heavy_path:
                heavy += 1
        # weight 9/10 -> expect a large majority; a loose bound keeps this stable.
        assert heavy > total * 0.7


def test_v2_sticky_within_category_across_molts(tui_dir, tmp_path):
    """Same anchor + started_at -> same pick, molt-independent, per category."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "a.json", "weight": 1},
            {"path": "b.json", "weight": 1},
            {"path": "c.json", "weight": 1},
        ],
    })
    agent_dir = tmp_path / "agent"
    anchor = _anchor_with_started_at(agent_dir, "fixed-start", molt_count=0)
    defaults = {"codex_session_anchor": anchor}
    first = codex_pool.select_codex_pool_auth_path(defaults, model="gpt-5.6-sol")
    assert first is not None
    for molt in (1, 2, 7, 42):
        _anchor_with_started_at(agent_dir, "fixed-start", molt_count=molt)
        assert codex_pool.select_codex_pool_auth_path(defaults, model="gpt-5.6-sol") == first


def test_v2_selection_metadata_shape_and_scope(tui_dir, tmp_path):
    """``model_scope`` is the exact category key; index/size are category-relative."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "codex-auth/a.json", "weight": 1},
            {"path": "codex-auth/b.json", "weight": 3},
        ],
        "gpt-5.5": [
            {"path": "codex-auth/x.json", "weight": 1},
            {"path": "codex-auth/y.json", "weight": 1},
            {"path": "codex-auth/z.json", "weight": 1},
        ],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, model="gpt-5.6-sol",
    )
    assert sel is not None
    meta = sel["selection"]
    assert set(meta) == {
        "source_ref", "source_index", "pool_size", "weight", "auth_path_sha8",
        "model_scope",
    }
    assert meta["model_scope"] == "gpt-5.6-sol"
    refs = ["codex-auth/a.json", "codex-auth/b.json"]
    assert meta["source_ref"] in refs
    assert meta["source_index"] == refs.index(meta["source_ref"])
    # Category-relative, NOT the file-wide account count (2, not 5).
    assert meta["pool_size"] == 2


def test_v2_selection_metadata_contains_no_secrets(tui_dir, tmp_path):
    """The v2 selection dict never carries token contents or absolute paths."""
    secret = "SECRET-token-value-do-not-log"
    token = tui_dir / "work.json"
    token.write_text(json.dumps({"access_token": secret}), encoding="utf-8")
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "work.json", "weight": 1}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    sel = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, model="gpt-5.6-sol",
    )
    dumped = json.dumps(sel["selection"])
    assert secret not in dumped
    assert "access_token" not in dumped
    assert str(tui_dir) not in dumped


# --------------------------------------------------------------------------
# Factory integration: codex-pool injects codex_auth_path into the reused adapter
# --------------------------------------------------------------------------


def _codex_pool_adapter(provider, defaults, model="gpt-5.5"):
    svc = LLMService(
        provider=provider, model=model,
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


def test_codex_pool_factory_stamps_selection_on_adapter_and_chat(tui_dir, tmp_path):
    """The factory stamps the non-secret selection on the adapter and each chat."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 2}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    fake_chat = mock.MagicMock(name="chat")
    try:
        with mock.patch(
            "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
            return_value=fake_chat,
        ):
            adapter = _codex_pool_adapter("codex-pool", {"codex_session_anchor": anchor})
            selection = adapter.codex_pool_selection
            assert selection == {
                "source_ref": "work.json",
                "source_index": 0,
                "pool_size": 1,
                "weight": 2,
                "auth_path_sha8": mock.ANY,
                "model_scope": None,
            }
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
            assert chat is fake_chat
            assert chat.codex_pool_selection == selection
    finally:
        mgr.stop()


def test_codex_pool_default_thinking_sends_xhigh(tui_dir, tmp_path):
    """codex-pool reuses the Codex adapter, so omitted thinking maps to xhigh."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        adapter = _codex_pool_adapter("codex-pool", {"codex_session_anchor": anchor})
        chat = adapter.create_chat("gpt-5.5", "system prompt")
        assert chat._extra_kwargs.get("reasoning") == {"effort": "xhigh"}
    finally:
        mgr.stop()


def test_codex_pool_explicit_thinking_passes_through(tui_dir, tmp_path):
    """An explicit thinking level on codex-pool is sent as-is, not overridden."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        adapter = _codex_pool_adapter("codex-pool", {"codex_session_anchor": anchor})
        chat = adapter.create_chat("gpt-5.5", "system prompt", thinking="low")
        assert chat._extra_kwargs.get("reasoning") == {"effort": "low"}
    finally:
        mgr.stop()


def test_codex_pool_factory_stamps_fallback_marker_without_pool(tui_dir, tmp_path):
    """No usable pool -> the breadcrumb says the legacy default token was used."""
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        adapter = _codex_pool_adapter("codex-pool", {"codex_session_anchor": anchor})
        assert adapter.codex_pool_selection == {"fallback": "legacy_default"}
    finally:
        mgr.stop()


def test_codex_factory_has_no_pool_selection_attr(tui_dir, tmp_path):
    """Provider ``codex`` never grows the pool breadcrumb."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        adapter = _codex_pool_adapter("codex", {"codex_session_anchor": anchor})
        assert not hasattr(adapter, "codex_pool_selection")
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


# --------------------------------------------------------------------------
# Factory integration: model-classified (v2) pools
# --------------------------------------------------------------------------


def test_codex_pool_factory_selects_within_configured_models_category(tui_dir, tmp_path):
    """The configured model reaches selection: the category's path is injected."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 1}],
        "gpt-5.5": [{"path": "old.json", "weight": 1}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    for provider in ("codex-pool", "codex_pool"):
        mgr, cls = _mock_mgr()
        try:
            _codex_pool_adapter(
                provider, {"codex_session_anchor": anchor}, model="gpt-5.6-sol",
            )
            cls.assert_called_with(token_path=str(tui_dir / "sol.json"))
        finally:
            mgr.stop()


def test_codex_pool_factory_stamps_model_scope_breadcrumb(tui_dir, tmp_path):
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 2}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        adapter = _codex_pool_adapter(
            "codex-pool", {"codex_session_anchor": anchor}, model="gpt-5.6-sol",
        )
        assert adapter.codex_pool_selection == {
            "source_ref": "sol.json",
            "source_index": 0,
            "pool_size": 1,
            "weight": 2,
            "auth_path_sha8": mock.ANY,
            "model_scope": "gpt-5.6-sol",
        }
    finally:
        mgr.stop()


def test_codex_pool_factory_unclassified_model_falls_back_to_legacy(tui_dir, tmp_path):
    """No exact category for the configured model -> legacy default token path,
    with the existing explicit fallback breadcrumb — a pooled agent never half-pools."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 1}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        adapter = _codex_pool_adapter(
            "codex-pool", {"codex_session_anchor": anchor}, model="gpt-5.6-terra",
        )
        # No token_path kwarg -> CodexTokenManager() legacy default behavior.
        cls.assert_called_with()
        assert adapter.codex_pool_selection == {"fallback": "legacy_default"}
    finally:
        mgr.stop()


def test_codex_provider_ignores_v2_pool_file(tui_dir, tmp_path):
    """A populated model-classified pool must NOT affect provider ``codex``."""
    _write_pool_v2(tui_dir, {
        "gpt-5.5": [{"path": "work.json", "weight": 1}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    mgr, cls = _mock_mgr()
    try:
        _codex_pool_adapter("codex", {"codex_session_anchor": anchor}, model="gpt-5.5")
        cls.assert_called_with()
    finally:
        mgr.stop()


# --------------------------------------------------------------------------
# usage_limit_reached recognizer — STRUCTURAL only (429 + machine code)
# --------------------------------------------------------------------------


class _FakeStatusError(Exception):
    """A minimal openai-APIStatusError-shaped exception for structural tests.

    Mirrors the real SDK surface used by the recognizer: a numeric
    ``status_code`` and a decoded ``body`` dict (``code`` populated only from a
    TOP-LEVEL body key by the SDK, the structured code nested under
    ``body['error']``). No network, no real SDK.
    """

    def __init__(self, message="boom", *, status_code=None, code=None, body=None,
                 response=None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if body is not None:
            self.body = body
        if response is not None:
            self.response = response


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_recognizer_true_for_429_with_error_code():
    """429 + body.error.code == usage_limit_reached -> switch."""
    exc = _FakeStatusError(
        status_code=429,
        body={"error": {"type": "rate_limit", "code": "usage_limit_reached"}},
    )
    assert codex_pool._is_usage_limit_reached_error(exc) is True


def test_recognizer_true_for_429_with_error_type():
    """429 + body.error.type == usage_limit_reached -> switch (Codex may nest here)."""
    exc = _FakeStatusError(
        status_code=429,
        body={"error": {"type": "usage_limit_reached", "message": "quota"}},
    )
    assert codex_pool._is_usage_limit_reached_error(exc) is True


def test_recognizer_true_for_429_with_top_level_body_code():
    """429 + top-level body.code == usage_limit_reached -> switch."""
    exc = _FakeStatusError(status_code=429, body={"code": "usage_limit_reached"})
    assert codex_pool._is_usage_limit_reached_error(exc) is True


def test_recognizer_true_for_429_with_code_attribute():
    """429 + exc.code (string attribute) == usage_limit_reached -> switch."""
    exc = _FakeStatusError(status_code=429, code="usage_limit_reached")
    assert codex_pool._is_usage_limit_reached_error(exc) is True


def test_recognizer_status_from_response_object():
    """Status extracted structurally from exc.response.status_code."""
    exc = _FakeStatusError(
        response=_FakeResponse(429),
        body={"error": {"code": "usage_limit_reached"}},
    )
    assert codex_pool._is_usage_limit_reached_error(exc) is True


def test_recognizer_false_for_429_other_code():
    """An ordinary 429 with a different code must NOT switch."""
    exc = _FakeStatusError(
        status_code=429,
        body={"error": {"code": "rate_limit_exceeded"}},
    )
    assert codex_pool._is_usage_limit_reached_error(exc) is False


def test_recognizer_false_for_429_no_code():
    """A 429 with no structured code must NOT switch."""
    exc = _FakeStatusError(status_code=429, body={"error": {"message": "slow down"}})
    assert codex_pool._is_usage_limit_reached_error(exc) is False


def test_recognizer_false_for_usage_limit_code_but_not_429():
    """usage_limit_reached at status 500 must NOT switch — 429 is required."""
    exc = _FakeStatusError(
        status_code=500,
        body={"error": {"code": "usage_limit_reached"}},
    )
    assert codex_pool._is_usage_limit_reached_error(exc) is False


def test_recognizer_false_when_only_in_message_string():
    """String mentions alone are NOT sufficient — must be a structured code."""
    exc = _FakeStatusError(
        "429 usage_limit_reached: you are out of quota",
        status_code=429,
        body={"error": {"code": "rate_limit_exceeded"}},
    )
    assert codex_pool._is_usage_limit_reached_error(exc) is False


def test_recognizer_false_when_status_only_in_message():
    """A body-only usage_limit_reached with no structured 429 does NOT switch.

    The number 429 appearing only in the free-form message must not be read as
    the status — status must come from a structured integer field.
    """
    exc = _FakeStatusError(
        "got a 429 back",
        body={"error": {"code": "usage_limit_reached"}},
    )
    assert codex_pool._is_usage_limit_reached_error(exc) is False


def test_recognizer_false_for_plain_exception():
    """A network error / timeout / arbitrary exception must NOT switch."""
    assert codex_pool._is_usage_limit_reached_error(TimeoutError("read timed out")) is False
    assert codex_pool._is_usage_limit_reached_error(ValueError("nope")) is False


def test_recognizer_never_raises_on_weird_shapes():
    """Malformed body / non-dict error must be swallowed to False, never raise."""
    for body in (None, [], "string-body", {"error": "not-a-dict"}, {"error": None}):
        exc = _FakeStatusError(status_code=429, body=body)
        assert codex_pool._is_usage_limit_reached_error(exc) is False


def test_recognizer_bool_status_code_rejected():
    """A bool status_code (True==1 in Python) must not be read as 429."""
    exc = _FakeStatusError(status_code=True, body={"error": {"code": "usage_limit_reached"}})
    assert codex_pool._is_usage_limit_reached_error(exc) is False


# --------------------------------------------------------------------------
# Failover candidate SEQUENCE — request-scoped, anchored to the SELECTED auth
# path, walked VERBATIM with NO realpath/alias dedup and NO suppression by
# resolved identity. This is a switch/retry budget (10 actual switches), not a
# distinct-account budget: repeated entries, aliases, and revisits/wraps back to
# the selected account are all emitted in order. Redaction is preserved.
# --------------------------------------------------------------------------


def _selected_path(tui_dir, tmp_path, model="gpt-5.5"):
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model=model)
    return anchor, (sel["auth_path"] if sel else None)


def test_failover_candidates_anchor_to_selected_path(tui_dir, tmp_path):
    """The sequence starts AFTER the SELECTED account, walking the validated pool
    order and wrapping — the selected account IS revisited on wrap (no dedup)."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    # Exactly MAX_FAILOVER_SWITCHES candidates, walking as many wraps as needed.
    assert len(cands) == codex_pool.MAX_FAILOVER_SWITCHES == 10
    # The FIRST candidate is the pool entry immediately after the selected one
    # (validated order, wrapping) — never the selected account itself first.
    names = ["a.json", "b.json", "c.json"]
    sel_idx = next(i for i, n in enumerate(names) if str(tui_dir / n) == sel_path)
    expected_first = names[(sel_idx + 1) % 3]
    assert Path(cands[0]["auth_path"]).name == expected_first
    # The selected account IS reached again on wrap (revisit allowed, not suppressed).
    assert any(c["auth_path"] == sel_path for c in cands)
    # The full first-lap order after the anchor matches the pool sequence.
    lap = [Path(c["auth_path"]).name for c in cands[:3]]
    assert lap == [names[(sel_idx + 1) % 3], names[(sel_idx + 2) % 3], names[(sel_idx + 3) % 3]]


def test_failover_candidates_anchor_to_authoritative_source_index(tui_dir, tmp_path):
    """B1 regression (candidate-order correctness, HAND-AUTHORED oracle): when the
    same resolved path appears at MULTIPLE occurrences and weighted selection chose
    a LATER one, ``selected_source_index`` is authoritative — the sequence begins
    with the siblings configured right AFTER that occurrence, not after the first
    path match.

    Pool (15 entries):  idx 0:S, 1..7:a1..a7, idx 8:S (the SELECTED occurrence),
    9..14:b1..b6. With source_index=8 the ten-switch walk is, verbatim,
    b1,b2,b3,b4,b5,b6, then wrap S,a1,a2,a3 — NOT the first-match walk a1..a7,S,b1,b2
    that anchoring to idx 0 would (wrongly) produce, which omits b3-b6 from budget.
    """
    accounts = [{"path": "S.json", "weight": 1}]
    accounts += [{"path": f"a{i}.json", "weight": 1} for i in range(1, 8)]  # idx 1..7
    accounts += [{"path": "S.json", "weight": 1}]                            # idx 8 (selected)
    accounts += [{"path": f"b{i}.json", "weight": 1} for i in range(1, 7)]   # idx 9..14
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    selected_path = str(tui_dir / "S.json")

    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None,
        selected_auth_path=selected_path, selected_source_index=8,
    )
    got = [Path(c["auth_path"]).name for c in cands]

    # Hand-authored expected order (NOT derived from the helper).
    expected = [
        "b1.json", "b2.json", "b3.json", "b4.json", "b5.json", "b6.json",
        "S.json", "a1.json", "a2.json", "a3.json",
    ]
    assert got == expected
    # b3..b6 (the later configured siblings) are inside the 10-switch budget.
    for i in (3, 4, 5, 6):
        assert f"b{i}.json" in got
    # The old first-match (anchor=idx 0) sequence is NOT produced.
    old_first_match = [
        "a1.json", "a2.json", "a3.json", "a4.json", "a5.json", "a6.json",
        "a7.json", "S.json", "b1.json", "b2.json",
    ]
    assert got != old_first_match
    # The first switch is index 9 (b1) — the sibling right after the actual pick.
    assert cands[0]["source_index"] == 9


def test_failover_candidates_source_index_absent_uses_path_scan_fallback(tui_dir, tmp_path):
    """When ``selected_source_index`` is absent/invalid (a direct/internal caller
    that did not thread it), the DEFENSIVE path-scan fallback is preserved: anchor
    to the FIRST resolved-path match. For the duplicate-S pool that first match is
    idx 0, so the fallback walk is a1..a7,S,b1,b2 (the pre-index behavior)."""
    accounts = [{"path": "S.json", "weight": 1}]
    accounts += [{"path": f"a{i}.json", "weight": 1} for i in range(1, 8)]
    accounts += [{"path": "S.json", "weight": 1}]
    accounts += [{"path": f"b{i}.json", "weight": 1} for i in range(1, 7)]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    selected_path = str(tui_dir / "S.json")

    expected_pathscan = [
        "a1.json", "a2.json", "a3.json", "a4.json", "a5.json", "a6.json",
        "a7.json", "S.json", "b1.json", "b2.json",
    ]
    # index=None -> path-scan fallback.
    none_seq = [
        Path(c["auth_path"]).name
        for c in codex_pool._codex_pool_failover_candidates(
            {"codex_session_anchor": anchor}, model=None,
            selected_auth_path=selected_path, selected_source_index=None,
        )
    ]
    assert none_seq == expected_pathscan
    # Out-of-range index -> same defensive fallback.
    oor_seq = [
        Path(c["auth_path"]).name
        for c in codex_pool._codex_pool_failover_candidates(
            {"codex_session_anchor": anchor}, model=None,
            selected_auth_path=selected_path, selected_source_index=999,
        )
    ]
    assert oor_seq == expected_pathscan
    # A bool (int subclass) is rejected as a non-index -> defensive fallback.
    bool_seq = [
        Path(c["auth_path"]).name
        for c in codex_pool._codex_pool_failover_candidates(
            {"codex_session_anchor": anchor}, model=None,
            selected_auth_path=selected_path, selected_source_index=True,
        )
    ]
    assert bool_seq == expected_pathscan
    # Default (param omitted entirely) also uses the path-scan fallback.
    default_seq = [
        Path(c["auth_path"]).name
        for c in codex_pool._codex_pool_failover_candidates(
            {"codex_session_anchor": anchor}, model=None,
            selected_auth_path=selected_path,
        )
    ]
    assert default_seq == expected_pathscan


def test_failover_candidates_no_dedup_aliased_paths(tui_dir, tmp_path):
    """Aliased refs to the SAME file are NOT collapsed — each alias entry is walked
    in order as its own candidate (a usage limit may be soft; retry each ref)."""
    _write_pool(tui_dir, [
        {"path": "primary.json", "weight": 100},   # weight makes it the near-certain primary
        {"path": "same.json", "weight": 1},
        {"path": "./same.json", "weight": 1},          # alias of same.json
        {"path": str(tui_dir / "same.json"), "weight": 1},  # absolute alias
        {"path": "other.json", "weight": 1},
    ])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    resolved = [c["auth_path"] for c in cands]
    same_resolved = str(tui_dir / "same.json")
    # The three same.json aliases each appear (once per lap), so at least the
    # first lap contributes 3 same.json entries — never deduped to one.
    assert resolved[:4].count(same_resolved) == 3
    # Source refs preserve each alias exactly as configured — relative refs stay
    # verbatim, and the ABSOLUTE alias is redacted (the preserved redaction
    # invariant), all pointing at the same resolved auth_path.
    first_lap_same_refs = [
        c["source_ref"] for c in cands[:4] if c["auth_path"] == same_resolved
    ]
    assert first_lap_same_refs == [
        "same.json", "./same.json", codex_pool.ABSOLUTE_REF_REDACTED,
    ]


def test_failover_candidates_no_dedup_symlink_aliases(tui_dir, tmp_path):
    """A symlink and its target are NOT collapsed: both are walked as separate
    candidates (no realpath dedup). Skipped where symlinks are unavailable.

    Uses an additive temp symlink under the temp TUI dir; no shared path is
    created, deleted, or overwritten."""
    target = tui_dir / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tui_dir / "link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        import pytest as _pytest
        _pytest.skip("symlinks unavailable in this environment")

    _write_pool(tui_dir, [
        {"path": "primary.json", "weight": 100},
        {"path": "target.json", "weight": 1},
        {"path": "link.json", "weight": 1},   # symlink alias of target.json
    ])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    # BOTH the link and its target appear as candidates by their own configured
    # (unresolved) path — realpath is NOT applied, so they are not merged.
    first_lap_names = [Path(c["auth_path"]).name for c in cands[:2]]
    assert set(first_lap_names) == {"target.json", "link.json"}


def test_failover_candidates_include_selected_on_wrap_even_when_aliased(tui_dir, tmp_path):
    """The selected account (and its aliases) ARE reached on wrap — never
    suppressed by resolved identity."""
    _write_pool(tui_dir, [
        {"path": "dup.json", "weight": 100},   # near-certain selected primary
        {"path": "./dup.json", "weight": 1},   # alias of the selected file
        {"path": "real-other.json", "weight": 1},
    ])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    resolved = [c["auth_path"] for c in cands]
    # The selected file's resolved path DOES appear among candidates (via its
    # own entry reached on wrap and/or its ./dup.json alias — both resolve to it).
    assert sel_path in resolved


def test_failover_candidates_cap_at_ten_switches(tui_dir, tmp_path):
    """Exactly MAX_FAILOVER_SWITCHES (=10) candidates — the switch/retry budget —
    regardless of how many distinct accounts exist."""
    _write_pool(tui_dir, [{"path": f"a{i}.json", "weight": 1} for i in range(25)])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    assert len(cands) == codex_pool.MAX_FAILOVER_SWITCHES == 10


def test_failover_candidates_single_account_revisits(tui_dir, tmp_path):
    """A one-account pool yields the SAME credential MAX_FAILOVER_SWITCHES times
    (revisit/wrap allowed — a usage limit may clear on a later attempt)."""
    _write_pool(tui_dir, [{"path": "solo.json", "weight": 1}])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    assert len(cands) == codex_pool.MAX_FAILOVER_SWITCHES == 10
    assert all(Path(c["auth_path"]).name == "solo.json" for c in cands)


def test_failover_candidates_only_aliases_of_selected_still_revisit(tui_dir, tmp_path):
    """A pool that is only the selected file under aliases still produces a full
    switch sequence (each alias/revisit attempted — NOT collapsed to empty)."""
    _write_pool(tui_dir, [
        {"path": "solo.json", "weight": 1},
        {"path": "./solo.json", "weight": 1},
        {"path": str(tui_dir / "solo.json"), "weight": 1},
    ])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    # All three aliases resolve to the one file; none are suppressed.
    assert len(cands) == codex_pool.MAX_FAILOVER_SWITCHES == 10
    solo_resolved = str(tui_dir / "solo.json")
    assert all(c["auth_path"] == solo_resolved for c in cands)


def test_failover_candidates_empty_pool_is_empty(tui_dir, tmp_path):
    """An unusable/empty pool yields no candidates (caller fails loud)."""
    _write_pool(tui_dir, [])
    anchor, _ = _selected_path(tui_dir, tmp_path)
    assert codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=None,
    ) == []


def test_failover_candidates_v2_stays_in_category(tui_dir, tmp_path):
    """For a model-classified pool, candidates stay inside the model's category."""
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "sol-a.json", "weight": 1},
            {"path": "sol-b.json", "weight": 1},
            {"path": "sol-c.json", "weight": 1},
        ],
        "gpt-5.5": [{"path": "old.json", "weight": 100}],
    })
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, model="gpt-5.6-sol",
    )
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model="gpt-5.6-sol",
        selected_auth_path=sel["auth_path"],
    )
    sol_paths = {str(tui_dir / n) for n in ("sol-a.json", "sol-b.json", "sol-c.json")}
    resolved = {c["auth_path"] for c in cands}
    assert resolved <= sol_paths  # never leaks into gpt-5.5's category
    assert str(tui_dir / "old.json") not in resolved
    # The full 10-switch budget is drawn entirely from the model's category.
    assert len(cands) == codex_pool.MAX_FAILOVER_SWITCHES == 10


def test_failover_candidates_redact_absolute_source_ref(tui_dir, tmp_path):
    """An absolute account ref is NOT exposed as source_ref; identity via sha8."""
    abs_alt = tmp_path / "private" / "codex-auth-alt.json"
    _write_pool(tui_dir, [
        {"path": "primary.json", "weight": 9},
        {"path": str(abs_alt), "weight": 1},
    ])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    # Find the candidate for the absolute alt (by resolved path).
    abs_resolved = str(abs_alt)
    abs_cands = [c for c in cands if c["auth_path"] == abs_resolved]
    if abs_cands:  # present only when primary != abs_alt (weight 9 vs 1 makes this near-certain)
        c = abs_cands[0]
        assert str(abs_alt) not in json.dumps(c["source_ref"])
        assert str(tmp_path) not in json.dumps(c["source_ref"])
        # The sha8 provides stable identity instead.
        assert c["auth_path_sha8"] == hashlib.sha256(
            abs_resolved.encode("utf-8")
        ).hexdigest()[:8]


def test_failover_candidates_keep_relative_source_ref(tui_dir, tmp_path):
    """A relative account ref stays relative in source_ref (safe)."""
    _write_pool(tui_dir, [
        {"path": "primary.json", "weight": 9},
        {"path": "codex-auth/rel.json", "weight": 1},
    ])
    anchor, sel_path = _selected_path(tui_dir, tmp_path)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel_path,
    )
    rel_cands = [c for c in cands if c["auth_path"] == str(tui_dir / "codex-auth/rel.json")]
    if rel_cands:
        assert rel_cands[0]["source_ref"] == "codex-auth/rel.json"


# --------------------------------------------------------------------------
# Request-scoped failover driver (codex-pool only) — the wired send wrapper
# --------------------------------------------------------------------------
#
# These exercise the REAL ``_codex_pool`` + ``_codex`` factory path with
# ``CodexTokenManager`` mocked and ``CodexOpenAIAdapter.create_chat`` replaced by
# a per-account fake chat. Each fake chat's ``send``/``send_stream`` is scripted
# by the account's token file basename, so we can drive a ``usage_limit_reached``
# 429 on one account and success on the next — no network, no real SDK.


class _FakeUsageLimitError(Exception):
    """openai.RateLimitError-shaped: structural 429 + usage_limit_reached."""

    def __init__(self, message="usage limit"):
        super().__init__(message)
        self.status_code = 429
        self.body = {"error": {"type": "rate_limit", "code": "usage_limit_reached"}}


class _FakeOrdinary429(Exception):
    def __init__(self, message="slow down"):
        super().__init__(message)
        self.status_code = 429
        self.body = {"error": {"code": "rate_limit_exceeded"}}


# Module-level provider-call counter: increments once per LEAF send_stream call
# (the real ``responses.create()`` analogue). ``send`` delegates to ``send_stream``
# EXACTLY as the real ``CodexResponsesSession`` does, so a wrapper that nests
# ``send``→``send_stream`` would double-count here — this is what catches the
# nested double-drive regression.
_PROVIDER_CALLS: list[str] = []


class _FakeChat:
    """A controllable chat double keyed to one account (token basename).

    ``script`` maps an account basename to either an exception to raise or a
    value to return on the LEAF ``send_stream``. ``send`` delegates to
    ``send_stream`` (matching the real ``CodexResponsesSession.send``), so the
    module counter ``_PROVIDER_CALLS`` records one entry per real provider call
    regardless of which entrypoint the caller used.

    ``on_chunk``, when provided, is invoked with a delta BEFORE the scripted
    action so streaming-partial-output safety can be exercised.
    """

    def __init__(self, account, interface, script, *, emit_chunk=False):
        self.account = account
        self.interface = interface
        self._script = script
        self._emit_chunk = emit_chunk
        # Stamped by the factory's create_chat wrapper.
        self.codex_pool_selection = None

    def send(self, message):
        # Real CodexResponsesSession.send delegates to send_stream(on_chunk=None).
        return self.send_stream(message, on_chunk=None)

    def send_stream(self, message, on_chunk=None):
        _PROVIDER_CALLS.append(self.account)
        if self._emit_chunk and on_chunk is not None:
            on_chunk(f"partial:{self.account}")
        action = self._script.get(self.account)
        if isinstance(action, BaseException):
            raise action
        from lingtai.kernel.llm.base import LLMResponse
        return LLMResponse(text=f"ok:{self.account}")


class _FailoverHarness:
    """Patches CodexTokenManager + CodexOpenAIAdapter.create_chat so each built
    adapter yields a fake chat scripted by its account (token basename)."""

    def __init__(self, script, interface, *, emit_chunk=False):
        self._script = script
        self._interface = interface
        self._emit_chunk = emit_chunk
        self.built_accounts = []      # order of account basenames whose chats were created
        self._patchers = []

    def __enter__(self):
        from lingtai.llm.openai.adapter import CodexOpenAIAdapter

        _PROVIDER_CALLS.clear()

        # Mock the token manager: any token_path is accepted; token/account are fake.
        mgr_patcher = _start_codex_mgr_mock()
        self._patchers.append(mgr_patcher)

        harness = self

        def _fake_create_chat(self_adapter, *a, **kw):
            # The account basename comes from the resolved token path the adapter
            # was built with (via its mocked token manager).
            token_path = getattr(getattr(self_adapter, "_codex_token_mgr", None), "_path", None)
            account = Path(str(token_path)).name if token_path else "unknown"
            harness.built_accounts.append(account)
            return _FakeChat(
                account, harness._interface, harness._script,
                emit_chunk=harness._emit_chunk,
            )

        cc_patcher = mock.patch.object(CodexOpenAIAdapter, "create_chat", _fake_create_chat)
        cc_patcher.start()
        self._patchers.append(cc_patcher)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False


def _build_pool_chat(tui_dir, tmp_path, script, *, accounts, model="gpt-5.5",
                     provider="codex-pool"):
    """Build a codex-pool adapter + its (wrapped) primary chat under the harness.

    Returns ``(adapter, chat, harness, interface)``. The interface is a real
    ChatInterface shared across all fake chats.
    """
    from lingtai.kernel.llm.interface import ChatInterface
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    svc = LLMService(
        provider=provider, model=model,
        provider_defaults={provider: {"codex_session_anchor": anchor}},
    )
    adapter = svc.get_adapter(provider)
    chat = adapter.create_chat(model=model, system_prompt="s", interface=interface)
    return adapter, chat, harness, interface


def _primary_and_other(tui_dir, tmp_path, accounts, model="gpt-5.5"):
    """Write the pool, return (anchor, primary_basename, other_basename)."""
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model=model)
    primary_name = Path(sel["auth_path"]).name
    names = [a["path"] for a in accounts]
    other = next(n for n in names if n != primary_name)
    return anchor, primary_name, other


def _pool_svc(anchor, provider="codex-pool", model="gpt-5.5"):
    return LLMService(
        provider=provider, model=model,
        provider_defaults={provider: {"codex_session_anchor": anchor}},
    )


def test_failover_switches_on_usage_limit_and_succeeds(tui_dir, tmp_path):
    """A usage_limit_reached 429 on the primary switches to the next account
    and the retry succeeds; the response comes from the switched account."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    anchor, primary_name, other_name = _primary_and_other(tui_dir, tmp_path, accounts)
    interface = ChatInterface()
    script = {primary_name: _FakeUsageLimitError(), other_name: "ok"}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send("hello")
        assert resp.text == f"ok:{other_name}"
        # Exactly two provider calls: the primary (limited) + one switch (ok).
        assert _PROVIDER_CALLS == [primary_name, other_name]
    finally:
        harness.__exit__()


def test_failover_send_entrypoint_no_nested_double_drive(tui_dir, tmp_path):
    """Real CodexResponsesSession.send delegates to send_stream. The wrapper must
    NOT nest send→send_stream into two failover passes: a 3-account all-limited
    pool driven via ``chat.send`` makes EXACTLY 1 + MAX_FAILOVER_SWITCHES == 11
    provider calls (primary + 10 switches, wrapping the 3-account sequence),
    never MORE — a nested double-drive would explode far past the budget."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    script = {n: _FakeUsageLimitError(n) for n in ("a.json", "b.json", "c.json")}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send("hello")  # the DEFAULT (non-streaming) entrypoint
        # Exactly primary + 10 switches == 11 provider calls — no double-drive
        # (which would nest and produce far more than the flat budget).
        assert len(_PROVIDER_CALLS) == 1 + codex_pool.MAX_FAILOVER_SWITCHES == 11
    finally:
        harness.__exit__()


def test_failover_ordinary_429_does_not_switch(tui_dir, tmp_path):
    """An ordinary 429 (no usage_limit_reached) must propagate, no switch."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    anchor, primary_name, _ = _primary_and_other(tui_dir, tmp_path, accounts)
    interface = ChatInterface()
    script = {primary_name: _FakeOrdinary429()}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeOrdinary429):
            chat.send("hello")
        # Exactly one provider call — the primary — and no switch.
        assert _PROVIDER_CALLS == [primary_name]
    finally:
        harness.__exit__()


def test_failover_network_error_does_not_switch(tui_dir, tmp_path):
    """A network error / timeout must propagate unchanged, no switch."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    anchor, primary_name, _ = _primary_and_other(tui_dir, tmp_path, accounts)
    interface = ChatInterface()
    script = {primary_name: TimeoutError("read timed out")}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(TimeoutError):
            chat.send("hello")
        assert _PROVIDER_CALLS == [primary_name]
    finally:
        harness.__exit__()


def test_failover_exhaustion_reraises_terminal_error(tui_dir, tmp_path):
    """When every attempt hits the usage limit, the terminal provider error from
    the LAST (10th) switched attempt is re-raised (no fake success, no infinite
    loop). Driven via send_stream. Also proves the 10th switch RUNS and only its
    failure exhausts."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    # Distinct error object per account so we can assert WHICH one is terminal.
    errs = {n: _FakeUsageLimitError(n) for n in ("a.json", "b.json", "c.json")}
    # The last switched candidate is candidates[-1]; its error is the terminal one.
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel["auth_path"],
    )
    last_name = Path(cands[-1]["auth_path"]).name
    harness = _FailoverHarness(errs, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError) as ei:
            chat.send_stream("hello")
        # The re-raised error is the LAST switched attempt's error object.
        assert ei.value is errs[last_name]
        # Primary + 10 switches == 11 provider calls; the 10th switch ran.
        assert len(_PROVIDER_CALLS) == 1 + codex_pool.MAX_FAILOVER_SWITCHES == 11
    finally:
        harness.__exit__()


def test_failover_caps_at_ten_switches(tui_dir, tmp_path):
    """With a big all-limited pool of DISTINCT accounts, EXACTLY initial + 10
    switches (11 provider calls), never more (the switch budget) — regardless of
    entrypoint. The 11th switch is NOT attempted."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": f"a{i}.json", "weight": 1} for i in range(25)]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    script = {f"a{i}.json": _FakeUsageLimitError(str(i)) for i in range(25)}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send("hello")
        assert len(_PROVIDER_CALLS) == 1 + codex_pool.MAX_FAILOVER_SWITCHES == 11
    finally:
        harness.__exit__()


# --------------------------------------------------------------------------
# No-dedup / revisit / sequence — BEHAVIORAL proof through the wired driver
# (not just candidate-helper counts): the exact provider-call order is observed.
# --------------------------------------------------------------------------


def _ordered_pool_names(tui_dir, tmp_path, accounts):
    """Write the pool, return (anchor, selected_basename, candidate switch order).

    IMPORTANT — SCOPE: this derives the expected order by CALLING
    ``_codex_pool_failover_candidates`` itself, so tests built on it prove only
    that the DRIVER faithfully REPLAYS the candidate list (order + revisits) the
    helper produced — they do NOT independently verify the helper's candidate
    ORDER is correct (that would be tautological on the anchor axis). Candidate-
    ORDER correctness (e.g. anchoring to the actual weighted occurrence with
    duplicate entries) is proved separately with HAND-AUTHORED expected sequences
    (see ``test_failover_candidates_anchor_to_authoritative_source_index`` and
    ``test_failover_driver_anchors_to_later_duplicate_primary_occurrence``)."""
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None,
        selected_auth_path=sel["auth_path"],
        selected_source_index=sel["selection"]["source_index"],
    )
    switch_names = [Path(c["auth_path"]).name for c in cands]
    return anchor, Path(sel["auth_path"]).name, switch_names


def test_failover_repeated_path_entries_attempted_independently_in_order(tui_dir, tmp_path):
    """Duplicate/aliased pool entries are each attempted in order through the
    driver (behavioral): a pool whose siblings repeat produces provider calls in
    the exact validated-sequence order, aliases included, none suppressed."""
    from lingtai.kernel.llm.interface import ChatInterface
    # 'dup.json' appears twice (two literal entries) + one alias — all distinct
    # entries in the validated order, all must be attempted.
    accounts = [
        {"path": "primary.json", "weight": 100},   # near-certain primary
        {"path": "dup.json", "weight": 1},
        {"path": "dup.json", "weight": 1},          # literal repeat
        {"path": "./dup.json", "weight": 1},        # alias
        {"path": "tail.json", "weight": 1},
    ]
    anchor, primary_name, switch_names = _ordered_pool_names(tui_dir, tmp_path, accounts)
    interface = ChatInterface()
    # Everything usage-limits, so every scheduled switch is attempted in order.
    script = {n: _FakeUsageLimitError(n) for n in {"primary.json", "dup.json", "tail.json"}}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send("hello")
        # Provider calls == primary then the exact candidate sequence (dup.json
        # attempted repeatedly across its two entries + alias + wraps, never deduped).
        assert _PROVIDER_CALLS == [primary_name] + switch_names
        # dup.json is attempted more than once (repeat + alias + wrap), proving no dedup.
        assert _PROVIDER_CALLS.count("dup.json") >= 2
    finally:
        harness.__exit__()


def test_failover_driver_anchors_to_later_duplicate_primary_occurrence(tui_dir, tmp_path):
    """B1 regression through the REAL driver (HAND-AUTHORED oracle, not the helper):
    when weighted selection picks a LATER duplicate occurrence of the same resolved
    path, the FIRST provider switch is the sibling configured right after THAT
    occurrence, and the full switch prefix follows configured order.

    Pool: idx 0:S, 1..7:a1..a7, idx 8:S (weighted pick forced here), 9..14:b1..b6.
    The factory threads ``selection["source_index"]`` (== 8) as the authoritative
    anchor, so the driver's provider calls are:
        S (primary) , b1,b2,b3,b4,b5,b6, S, a1,a2,a3
    — first switch b1 (idx 9), NEVER a1 (which anchoring to idx 0 would give)."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "S.json", "weight": 1}]
    accounts += [{"path": f"a{i}.json", "weight": 1} for i in range(1, 8)]
    accounts += [{"path": "S.json", "weight": 1}]                            # idx 8
    accounts += [{"path": f"b{i}.json", "weight": 1} for i in range(1, 7)]   # idx 9..14
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    # Everything usage-limits so the whole 10-switch sequence is walked in order.
    names = ["S.json"] + [f"a{i}.json" for i in range(1, 8)] + [f"b{i}.json" for i in range(1, 7)]
    script = {n: _FakeUsageLimitError(n) for n in set(names)}

    # Force weighted selection to choose the SECOND S occurrence (index 8).
    real_pick = codex_pool._weighted_pick

    def _forced_pick(accts, seed):
        # Only override for the real pool (len 15); leave any other call intact.
        if len(accts) == len(accounts):
            return 8, accts[8]
        return real_pick(accts, seed)

    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        with mock.patch.object(codex_pool, "_weighted_pick", _forced_pick):
            adapter = _pool_svc(anchor).get_adapter("codex-pool")
            # Sanity: the selection recorded the actual later occurrence (index 8).
            assert adapter.codex_pool_selection["source_index"] == 8
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
            with pytest.raises(_FakeUsageLimitError):
                chat.send("hello")
        # HAND-AUTHORED expected provider-call order (primary + 10 switches).
        expected_calls = [
            "S.json",                                             # primary (idx 8)
            "b1.json", "b2.json", "b3.json", "b4.json", "b5.json", "b6.json",  # idx 9..14
            "S.json", "a1.json", "a2.json", "a3.json",            # wrap idx 0,1,2,3
        ]
        assert _PROVIDER_CALLS == expected_calls
        # The very first SWITCH is b1 (the configured sibling after the actual pick),
        # never a1 (what anchoring to the first S occurrence, idx 0, would produce).
        assert _PROVIDER_CALLS[1] == "b1.json"
        assert _PROVIDER_CALLS[1] != "a1.json"
    finally:
        harness.__exit__()


class _SequencedFakeChat(_FakeChat):
    """A fake chat whose action is drawn from a PER-ACCOUNT queue consumed in
    order, so the SAME credential can fail on early attempts and succeed on a
    later revisit. ``script[account]`` is a list of actions (exception or 'ok');
    once exhausted, the last action repeats."""

    def send_stream(self, message, on_chunk=None):
        _PROVIDER_CALLS.append(self.account)
        if self._emit_chunk and on_chunk is not None:
            on_chunk(f"partial:{self.account}")
        queue = self._script.get(self.account)
        if isinstance(queue, list):
            action = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else "ok")
        else:
            action = queue
        if isinstance(action, BaseException):
            raise action
        from lingtai.kernel.llm.base import LLMResponse
        return LLMResponse(text=f"ok:{self.account}")


class _SequencedHarness(_FailoverHarness):
    """_FailoverHarness that yields _SequencedFakeChat instances."""

    def __enter__(self):
        from lingtai.llm.openai.adapter import CodexOpenAIAdapter
        _PROVIDER_CALLS.clear()
        self._patchers.append(_start_codex_mgr_mock())
        harness = self

        def _fake_create_chat(self_adapter, *a, **kw):
            token_path = getattr(getattr(self_adapter, "_codex_token_mgr", None), "_path", None)
            account = Path(str(token_path)).name if token_path else "unknown"
            harness.built_accounts.append(account)
            return _SequencedFakeChat(
                account, harness._interface, harness._script,
                emit_chunk=harness._emit_chunk,
            )

        cc_patcher = mock.patch.object(CodexOpenAIAdapter, "create_chat", _fake_create_chat)
        cc_patcher.start()
        self._patchers.append(cc_patcher)
        return self


def test_failover_revisit_same_credential_succeeds_after_earlier_usage_limit(tui_dir, tmp_path):
    """A usage limit can be transient/soft: the SAME credential usage-limits on
    its first attempt but SUCCEEDS when the pool wraps back to it later. The
    driver must attempt the revisit and accept its success (no suppression)."""
    from lingtai.kernel.llm.interface import ChatInterface
    # Two accounts; both usage-limit on their FIRST attempt, but the sibling
    # 'other' succeeds on its SECOND attempt (reached on wrap). This proves a
    # revisit to a previously-usage-limited credential is attempted and accepted.
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    anchor, primary_name, switch_names = _ordered_pool_names(tui_dir, tmp_path, accounts)
    other_name = "b.json" if primary_name == "a.json" else "a.json"
    interface = ChatInterface()
    # 'other' fails once, then succeeds on the revisit; primary always fails.
    script = {
        primary_name: [_FakeUsageLimitError(primary_name)],
        other_name: [_FakeUsageLimitError(other_name), "ok"],
    }
    harness = _SequencedHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send("hello")
        # Succeeded on the REVISIT of 'other' (its 2nd attempt), after both its
        # first attempt AND the primary usage-limited.
        assert resp.text == f"ok:{other_name}"
        # Sequence: primary(fail), other(fail, 1st switch), primary(fail, 2nd
        # switch, wrap), other(OK, 3rd switch, revisit).
        assert _PROVIDER_CALLS == [primary_name] + switch_names[:3]
        assert _PROVIDER_CALLS.count(other_name) == 2   # revisited and succeeded
    finally:
        harness.__exit__()


def test_failover_success_on_tenth_switched_attempt_is_accepted(tui_dir, tmp_path):
    """The 10th (last budgeted) switched attempt RUNS and its success is accepted:
    primary + 9 switches usage-limit, the 10th switch succeeds."""
    from lingtai.kernel.llm.interface import ChatInterface
    # Enough distinct accounts that the 10th switch lands on a fresh basename we
    # can single out as the success case.
    accounts = [{"path": f"s{i}.json", "weight": 1} for i in range(11)]
    anchor, primary_name, switch_names = _ordered_pool_names(tui_dir, tmp_path, accounts)
    assert len(switch_names) == codex_pool.MAX_FAILOVER_SWITCHES == 10
    tenth_name = switch_names[9]           # the 10th switched attempt's account
    interface = ChatInterface()
    # Everything usage-limits EXCEPT the account served on the 10th switch.
    script = {f"s{i}.json": _FakeUsageLimitError(str(i)) for i in range(11)}
    script[tenth_name] = "ok"
    # If tenth_name also appears earlier in the sequence (wrap), it would succeed
    # early; guard the test to the distinct-account case where it appears once.
    assert switch_names.count(tenth_name) == 1, "10th account must be first seen at the 10th switch"
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send("hello")
        assert resp.text == f"ok:{tenth_name}"
        # Primary + 10 switches ran; the 10th switch is where success landed.
        assert len(_PROVIDER_CALLS) == 1 + codex_pool.MAX_FAILOVER_SWITCHES == 11
        assert _PROVIDER_CALLS[-1] == tenth_name
    finally:
        harness.__exit__()


def test_failover_shared_adapter_state_unmutated_after_success(tui_dir, tmp_path):
    """The cached adapter's client/account/selection are byte-for-byte unchanged
    after a successful failover — no leaked switched account on shared state."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    primary_sel = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, model="gpt-5.5",
    )
    primary_name = Path(primary_sel["auth_path"]).name
    other_name = "b.json" if primary_name == "a.json" else "a.json"
    script = {primary_name: _FakeUsageLimitError(), other_name: "ok"}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        selection_before = dict(adapter.codex_pool_selection)
        client_before = adapter._client
        account_before = adapter.codex_account_id
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        chat.send("hello")
        # Shared adapter identity/selection restored/untouched.
        assert adapter.codex_pool_selection == selection_before
        assert adapter._client is client_before
        assert adapter.codex_account_id == account_before
    finally:
        harness.__exit__()


def test_failover_switched_response_carries_truthful_attribution(tui_dir, tmp_path):
    """The retry chat is stamped with the SWITCHED account's non-secret selection
    (truthful attribution for the call that actually served the response)."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    primary_sel = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, model="gpt-5.5",
    )
    primary_name = Path(primary_sel["auth_path"]).name
    other_name = "b.json" if primary_name == "a.json" else "a.json"

    # Capture every built chat so we can inspect the switched one's stamp.
    built_chats = []
    orig_send_stream = _FakeChat.send_stream

    def _capturing_send_stream(self, message, on_chunk=None):
        if self not in built_chats:
            built_chats.append(self)
        return orig_send_stream(self, message, on_chunk=on_chunk)

    script = {primary_name: _FakeUsageLimitError(), other_name: "ok"}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        with mock.patch.object(_FakeChat, "send_stream", _capturing_send_stream):
            adapter = _pool_svc(anchor).get_adapter("codex-pool")
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
            chat.send("hello")
        switched_chat = next(c for c in built_chats if c.account == other_name)
        sel = switched_chat.codex_pool_selection
        assert isinstance(sel, dict)
        # Relative refs stay relative and are safe.
        assert sel.get("source_ref") == other_name
        assert sel.get("failover") == "usage_limit_reached"
        # Non-secret: no token contents, no resolved absolute path.
        dumped = json.dumps(sel)
        assert "tok:" not in dumped
        assert str(tui_dir) not in dumped
    finally:
        harness.__exit__()


def test_failover_switched_attribution_redacts_absolute_ref(tui_dir, tmp_path):
    """When a switched-to account is configured with an ABSOLUTE path, its stamped
    source_ref is redacted (never the absolute path); identity is auth_path_sha8.

    Deterministic regardless of which account the seed selects as primary: whoever
    is primary usage-limits, and every distinct alternate is scripted to fail too,
    so the absolute-path account is ALWAYS reached and stamped as an alternate."""
    from lingtai.kernel.llm.interface import ChatInterface
    abs_alt = tmp_path / "private" / "codex-auth-alt.json"
    _write_pool(tui_dir, [
        {"path": "rel-a.json", "weight": 1},
        {"path": "rel-b.json", "weight": 1},
        {"path": str(abs_alt), "weight": 1},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    abs_name = abs_alt.name
    # Everything usage-limits, so every distinct account (incl. the absolute alt)
    # is built and stamped before terminal exhaustion.
    script = {
        "rel-a.json": _FakeUsageLimitError(),
        "rel-b.json": _FakeUsageLimitError(),
        abs_name: _FakeUsageLimitError(),
    }

    built_chats = []
    orig_send_stream = _FakeChat.send_stream

    def _capturing_send_stream(self, message, on_chunk=None):
        if self not in built_chats:
            built_chats.append(self)
        return orig_send_stream(self, message, on_chunk=on_chunk)

    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        with mock.patch.object(_FakeChat, "send_stream", _capturing_send_stream):
            adapter = _pool_svc(anchor).get_adapter("codex-pool")
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
            with pytest.raises(_FakeUsageLimitError):
                chat.send("hello")
        # The absolute-path account was reached as an ALTERNATE and stamped.
        abs_chats = [c for c in built_chats if c.account == abs_name and c.codex_pool_selection]
        # It is only stamped when it was a switched-to alternate (not the primary,
        # which carries the sticky selection stamp instead). If the seed made it
        # primary, another account is the redaction subject — but with all-relative
        # siblings, an absolute alternate is always present unless abs was primary.
        target = None
        for c in built_chats:
            sel = c.codex_pool_selection or {}
            if sel.get("source_ref") == codex_pool.ABSOLUTE_REF_REDACTED:
                target = sel
                break
        assert target is not None, "the absolute alternate must be stamped+redacted"
        dumped = json.dumps(target)
        assert str(abs_alt) not in dumped
        assert str(tmp_path) not in dumped
        assert target["auth_path_sha8"] == hashlib.sha256(
            str(abs_alt).encode("utf-8")
        ).hexdigest()[:8]
    finally:
        harness.__exit__()


def test_failover_single_account_pool_revisits_same_credential(tui_dir, tmp_path):
    """A single-account pool REVISITS its one credential across the switch budget
    (a usage limit may be transient/soft — retrying the same account can clear it).
    When every attempt still fails, the terminal error propagates after primary +
    10 switched revisits; no distinct-account requirement suppresses the revisits."""
    from lingtai.kernel.llm.interface import ChatInterface
    _write_pool(tui_dir, [{"path": "solo.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    script = {"solo.json": _FakeUsageLimitError()}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send("hello")
        # The one credential is revisited across the full budget: primary + 10
        # switched revisits == 11 fresh isolated builds, all of the same account.
        assert harness.built_accounts.count("solo.json") == 1 + codex_pool.MAX_FAILOVER_SWITCHES == 11
        assert set(harness.built_accounts) == {"solo.json"}
        assert _PROVIDER_CALLS == ["solo.json"] * 11
    finally:
        harness.__exit__()


def test_failover_single_account_pool_revisit_succeeds_when_limit_clears(tui_dir, tmp_path):
    """The soft-limit case for a single-account pool: the sole credential
    usage-limits on its first attempts but SUCCEEDS on a later revisit — the
    driver must accept that success rather than suppressing the revisit."""
    from lingtai.kernel.llm.interface import ChatInterface
    _write_pool(tui_dir, [{"path": "solo.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    # Fail on the primary + first two switched revisits, succeed on the third.
    script = {"solo.json": [
        _FakeUsageLimitError(), _FakeUsageLimitError(), _FakeUsageLimitError(), "ok",
    ]}
    harness = _SequencedHarness(script, interface)
    harness.__enter__()
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send("hello")
        assert resp.text == "ok:solo.json"
        # Primary(fail) + 3 switched revisits (2 fail, 3rd OK) == 4 attempts total.
        assert _PROVIDER_CALLS == ["solo.json"] * 4
    finally:
        harness.__exit__()


def test_failover_absent_for_legacy_fallback(tui_dir, tmp_path):
    """No usable pool -> legacy fallback -> no failover wrapper; the send path
    behaves exactly like plain codex (error propagates, no pool logic)."""
    from lingtai.kernel.llm.interface import ChatInterface
    # No pool file at all -> select returns None -> fallback marker.
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    script = {"LEGACY_DEFAULT": _FakeUsageLimitError()}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        assert adapter.codex_pool_selection == {"fallback": "legacy_default"}
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send("hello")
        assert harness.built_accounts == ["LEGACY_DEFAULT"]
    finally:
        harness.__exit__()


def test_failover_concurrent_requests_isolated(tui_dir, tmp_path):
    """Two concurrent sends over the SAME cached adapter each fail over on their
    own isolated chain; neither observes the other's switched account, and the
    shared adapter is unmutated afterward."""
    import threading
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    primary_sel = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, model="gpt-5.5",
    )
    primary_name = Path(primary_sel["auth_path"]).name
    other_name = "b.json" if primary_name == "a.json" else "a.json"
    # Primary always usage-limits; the other always succeeds.
    script = {primary_name: _FakeUsageLimitError(), other_name: "ok"}

    # Each request has its OWN interface; the adapter is shared (built once).
    iface1 = ChatInterface()
    iface2 = ChatInterface()
    harness = _FailoverHarness(script, iface1)  # interface overridden per create_chat
    harness.__enter__()
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        client_before = adapter._client
        selection_before = dict(adapter.codex_pool_selection)
        results = {}
        errors = {}

        def _run(key, iface):
            try:
                chat = adapter.create_chat(
                    model="gpt-5.5", system_prompt="s", interface=iface,
                )
                results[key] = chat.send("hello")
            except Exception as e:  # pragma: no cover - failure is asserted below
                errors[key] = e

        t1 = threading.Thread(target=_run, args=("r1", iface1))
        t2 = threading.Thread(target=_run, args=("r2", iface2))
        t1.start(); t2.start(); t1.join(); t2.join()

        assert not errors, errors
        assert results["r1"].text == f"ok:{other_name}"
        assert results["r2"].text == f"ok:{other_name}"
        # Shared adapter never mutated by either request.
        assert adapter._client is client_before
        assert adapter.codex_pool_selection == selection_before
    finally:
        harness.__exit__()


# --------------------------------------------------------------------------
# Attempt-level snapshot/restore of canonical history (incl. send(None))
# --------------------------------------------------------------------------


class _HistoryMutatingFakeChat(_FakeChat):
    """A fake whose leaf send mutates the shared interface like the real Codex
    path: it drops the trailing user entry on failure (the destructive heuristic)
    BEFORE raising. Used to prove the wrapper's attempt-level snapshot/restore
    puts caller-staged entries back before each alternate and terminal re-raise.
    """

    def send_stream(self, message, on_chunk=None):
        _PROVIDER_CALLS.append(self.account)
        action = self._script.get(self.account)
        if isinstance(action, BaseException):
            # Mimic CodexResponsesSession's destructive trailing-user drop.
            self.interface.drop_trailing(lambda e: e.role == "user")
            raise action
        from lingtai.kernel.llm.base import LLMResponse
        self.interface.add_assistant_message(
            [__import__("lingtai.kernel.llm.interface", fromlist=["TextBlock"]).TextBlock(text="ok")],
            model="gpt-5.5", provider="codex",
        )
        return LLMResponse(text=f"ok:{self.account}")


def _stage_prestaged_toolpair(interface):
    """Pre-stage an (assistant tool_call, user tool_result) pair, as the kernel
    does before a ``send(None)`` notification wake."""
    from lingtai.kernel.llm.interface import ToolCallBlock, ToolResultBlock
    interface.add_assistant_message(
        [ToolCallBlock(id="call_x", name="notification", args={"action": "check"})],
        model="gpt-5.5", provider="codex",
    )
    interface.add_tool_results(
        [ToolResultBlock(id="call_x", name="notification", content={"ok": True})]
    )


def _install_history_mutating_harness(script, interface):
    """Like _FailoverHarness but yields _HistoryMutatingFakeChat instances."""
    from lingtai.llm.openai.adapter import CodexOpenAIAdapter

    _PROVIDER_CALLS.clear()
    patchers = [_start_codex_mgr_mock()]

    def _fake_create_chat(self_adapter, *a, **kw):
        token_path = getattr(getattr(self_adapter, "_codex_token_mgr", None), "_path", None)
        account = Path(str(token_path)).name if token_path else "unknown"
        return _HistoryMutatingFakeChat(account, interface, script)

    cc_patcher = mock.patch.object(CodexOpenAIAdapter, "create_chat", _fake_create_chat)
    cc_patcher.start()
    patchers.append(cc_patcher)
    return patchers


def _stop(patchers):
    for p in reversed(patchers):
        p.stop()


def test_failover_send_none_restores_prestaged_history_then_succeeds(tui_dir, tmp_path):
    """A ``send(None)`` with a pre-staged (assistant tool_call, user tool_result)
    pair: primary usage-limits and destructively drops the staged user entry; the
    wrapper restores the snapshot before the alternate, which succeeds. The staged
    pair survives and the alternate sees a well-formed interface."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    primary_name = Path(sel["auth_path"]).name
    other_name = "b.json" if primary_name == "a.json" else "a.json"

    interface = ChatInterface()
    _stage_prestaged_toolpair(interface)
    roles_before = [e.role for e in interface.entries]
    assert roles_before == ["assistant", "user"]

    script = {primary_name: _FakeUsageLimitError(), other_name: "ok"}
    patchers = _install_history_mutating_harness(script, interface)
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send_stream(None)   # continue-from-wire
        assert resp.text == f"ok:{other_name}"
        # The staged (assistant tool_call, user tool_result) pair is intact, plus
        # the alternate's appended assistant turn.
        roles = [e.role for e in interface.entries]
        assert roles[:2] == ["assistant", "user"]
        assert roles[-1] == "assistant"
    finally:
        _stop(patchers)


def test_failover_send_none_terminal_restores_prestaged_history(tui_dir, tmp_path):
    """When every account usage-limits on a ``send(None)``, the pre-staged pair is
    restored EXACTLY before the terminal re-raise — history is not corrupted."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    _stage_prestaged_toolpair(interface)

    script = {n: _FakeUsageLimitError(n) for n in ("a.json", "b.json", "c.json")}
    patchers = _install_history_mutating_harness(script, interface)
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send_stream(None)
        # Interface restored to exactly the pre-staged pair — no dangling call, no loss.
        roles = [e.role for e in interface.entries]
        assert roles == ["assistant", "user"]
        # Primary + 10 switches ran (the 3-account sequence wrapped); every call
        # hit one of the three accounts, and the restore held across all of them.
        assert len(_PROVIDER_CALLS) == 1 + codex_pool.MAX_FAILOVER_SWITCHES == 11
        assert set(_PROVIDER_CALLS) == {"a.json", "b.json", "c.json"}
    finally:
        _stop(patchers)


def test_failover_alternate_on_alternate_failure_restores_before_each(tui_dir, tmp_path):
    """Primary usage-limits, then a first alternate succeeds. Each attempt starts
    from the restored snapshot (no accumulated corruption): the single staged user
    turn survives every restore and exactly one assistant reply is appended."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    primary = Path(sel["auth_path"]).name
    interface = ChatInterface()
    interface.add_user_message("hi")  # a normal user turn as the staged entry

    # The FIRST switched candidate succeeds; the primary usage-limits. (The other
    # accounts are irrelevant — the driver stops at the first success.)
    cands = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": anchor}, model=None, selected_auth_path=sel["auth_path"],
    )
    first_switch_name = Path(cands[0]["auth_path"]).name
    script = {n: _FakeUsageLimitError(n) for n in ("a.json", "b.json", "c.json")}
    script[first_switch_name] = "ok"
    patchers = _install_history_mutating_harness(script, interface)
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send_stream("hi")
        assert resp.text == f"ok:{first_switch_name}"
        # The single user turn survived the restore; exactly one assistant appended.
        roles = [e.role for e in interface.entries]
        assert roles.count("user") == 1
        assert roles.count("assistant") == 1
    finally:
        _stop(patchers)


# --------------------------------------------------------------------------
# Resource hygiene: failover adds NO gate/thread relative to existing _codex
# --------------------------------------------------------------------------


def test_failover_adds_no_rate_gate_relative_to_codex(tui_dir, tmp_path):
    """Honest invariant (NOT a fictional 'one logical gate'): the existing
    ``_codex`` builder does not forward ``max_rpm`` to ``CodexOpenAIAdapter``, so
    even with ``max_rpm=60`` the PRIMARY codex-pool adapter has no ``APICallGate``
    — and neither do the throwaway alternates (same builder, same kw). This
    feature therefore introduces zero new gate threads/executors. We do NOT fix
    the pre-existing ordinary-Codex ungated behavior here; we only prove failover
    adds nothing."""
    import threading
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()
    script = {n: _FakeUsageLimitError(n) for n in ("a.json", "b.json", "c.json")}
    harness = _FailoverHarness(script, interface)
    harness.__enter__()
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor, "max_rpm": 60}},
        )
        adapter = svc.get_adapter("codex-pool")
        # The primary Codex adapter is ungated today (documents the reality the
        # prior review's leak finding got wrong — there is no primary gate).
        assert getattr(adapter, "_gate", None) is None
        gate_threads_before = sum(1 for t in threading.enumerate() if t.name == "api-gate")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send("hello")
        gate_threads_after = sum(1 for t in threading.enumerate() if t.name == "api-gate")
        # Failover created no gate thread at all (before == after, and 0 new).
        assert gate_threads_after == gate_threads_before
    finally:
        harness.__exit__()


# --------------------------------------------------------------------------
# Streaming partial-output safety: no retry after a chunk was emitted
# --------------------------------------------------------------------------


def test_failover_no_switch_after_partial_chunk_emitted(tui_dir, tmp_path):
    """If a usage-limit 429 arrives AFTER an on_chunk delta was already emitted,
    the wrapper must NOT switch (which would mix/duplicate prefixes) — it fails
    loud with the original error instead."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    primary_name = Path(sel["auth_path"]).name
    other_name = "b.json" if primary_name == "a.json" else "a.json"
    interface = ChatInterface()
    # Primary emits a chunk THEN raises the usage-limit; other would succeed.
    script = {primary_name: _FakeUsageLimitError(), other_name: "ok"}
    harness = _FailoverHarness(script, interface, emit_chunk=True)
    harness.__enter__()
    chunks = []
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(_FakeUsageLimitError):
            chat.send_stream("hello", on_chunk=chunks.append)
        # A chunk was emitted, so no switch happened — only the primary was called.
        assert chunks == [f"partial:{primary_name}"]
        assert _PROVIDER_CALLS == [primary_name]
    finally:
        harness.__exit__()


def test_failover_switches_when_429_precedes_any_chunk(tui_dir, tmp_path):
    """A usage-limit 429 BEFORE any chunk still switches (the normal case)."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    primary_name = Path(sel["auth_path"]).name
    other_name = "b.json" if primary_name == "a.json" else "a.json"
    interface = ChatInterface()
    # emit_chunk=False -> the 429 precedes any chunk; switching is safe.
    script = {primary_name: _FakeUsageLimitError(), other_name: "ok"}
    harness = _FailoverHarness(script, interface, emit_chunk=False)
    harness.__enter__()
    chunks = []
    try:
        adapter = _pool_svc(anchor).get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send_stream("hello", on_chunk=chunks.append)
        assert resp.text == f"ok:{other_name}"
        assert _PROVIDER_CALLS == [primary_name, other_name]
    finally:
        harness.__exit__()


# --------------------------------------------------------------------------
# SessionManager-level attribution: llm_call = initial, usage = serving account
# --------------------------------------------------------------------------


def test_failover_session_manager_attribution_split(tui_dir, tmp_path):
    """Through a real SessionManager: the pre-send ``llm_call`` names the initial
    primary attempt; the served ``LLMResponse.usage.extra`` identifies the switched
    account. No raw token path leaks in either."""
    from types import SimpleNamespace
    import openai as openai_mod
    from lingtai.kernel.llm.interface import ChatInterface
    from lingtai.kernel.session import SessionManager
    from lingtai.kernel.config import AgentConfig

    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    primary_key = f"tok:{sel['auth_path']}"

    class _RealShapedUsageLimit(Exception):
        def __init__(self):
            super().__init__("usage limit reached")
            self.status_code = 429
            self.body = {"error": {"code": "usage_limit_reached"}}

    def _events():
        return [
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_ok",
                    usage=SimpleNamespace(
                        input_tokens=5, output_tokens=7,
                        input_tokens_details=SimpleNamespace(cached_tokens=0),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                    ),
                ),
                delta=None, item=None, item_id=None, text=None,
            ),
        ]

    class _PrimaryResponses:
        def create(self, **kwargs):
            raise _RealShapedUsageLimit()

    class _SwitchResponses:
        def create(self, **kwargs):
            return iter(_events())

    def _fake_openai(**kwargs):
        client = SimpleNamespace(api_key=kwargs.get("api_key"))
        client.responses = (
            _PrimaryResponses() if kwargs.get("api_key") == primary_key else _SwitchResponses()
        )
        return client

    mgr_patcher = _start_codex_mgr_mock()
    oai_patcher = mock.patch.object(openai_mod, "OpenAI", _fake_openai)
    oai_patcher.start()

    events_log = []
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        sm = SessionManager(
            llm_service=svc,
            config=AgentConfig(provider="codex-pool", model="gpt-5.5"),
            build_system_prompt_fn=lambda: "system",
            build_tool_schemas_fn=lambda: [],
            agent_name="test-agent",
            logger_fn=lambda ev, **f: events_log.append((ev, f)),
            streaming=False,
        )
        resp = sm.send("please answer")
        assert resp.text is not None
        # llm_call names the INITIAL primary account (emitted before any switch).
        llm_calls = [f for ev, f in events_log if ev == "llm_call"]
        assert llm_calls, "expected an llm_call event"
        primary_pool = llm_calls[0].get("codex_pool")
        assert isinstance(primary_pool, dict)
        assert primary_pool.get("source_ref") == Path(sel["auth_path"]).name
        assert "failover" not in primary_pool  # the initial attempt is not a failover
        # The SERVED usage identifies the switched account with the failover marker.
        extra = resp.usage.extra
        assert extra.get("codex_pool_failover") == "usage_limit_reached"
        # No raw token path anywhere in the emitted event/usage.
        dumped = json.dumps(llm_calls[0], default=str) + json.dumps(extra, default=str)
        assert str(tui_dir) not in dumped
        assert "tok:" not in dumped
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()


# --------------------------------------------------------------------------
# Integration: REAL CodexResponsesSession revert-then-retry preserves history
# --------------------------------------------------------------------------


def test_failover_real_session_preserves_canonical_history(tui_dir, tmp_path):
    """End-to-end with REAL Codex sessions (SDK client faked per account).

    The primary account's SDK raises a structural usage_limit_reached 429; the
    Codex session reverts its trailing user entry and re-raises; the driver
    switches to the next account whose SDK succeeds. Afterwards the SHARED
    canonical interface holds exactly one user turn + one assistant turn — the
    reverted-then-retried message is not double-recorded.
    """
    from types import SimpleNamespace
    import openai as openai_mod
    from lingtai.kernel.llm.interface import ChatInterface

    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    interface = ChatInterface()

    primary_sel = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, model="gpt-5.5",
    )
    primary_name = Path(primary_sel["auth_path"]).name

    # A structural usage-limit 429 (openai.RateLimitError-shaped enough for the
    # recognizer: numeric 429 + body.error.code).
    class _RealShapedUsageLimit(Exception):
        def __init__(self):
            super().__init__("usage limit reached")
            self.status_code = 429
            self.body = {"error": {"code": "usage_limit_reached"}}

    def _success_events():
        # One assistant text output + a completed response with usage.
        return [
            SimpleNamespace(type="response.output_text.delta", delta="hello back",
                            item=None, response=None, item_id=None, text=None),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_ok",
                    usage=SimpleNamespace(
                        input_tokens=5, output_tokens=7,
                        input_tokens_details=SimpleNamespace(cached_tokens=0),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                    ),
                ),
                delta=None, item=None, item_id=None, text=None,
            ),
        ]

    class _PrimaryResponses:
        def create(self, **kwargs):
            raise _RealShapedUsageLimit()

    class _SwitchResponses:
        def __init__(self):
            self.kwargs = []
        def create(self, **kwargs):
            self.kwargs.append(kwargs)
            return iter(_success_events())

    # Each _codex-built isolated adapter constructs a real openai.OpenAI(...) with
    # api_key f"tok:{token_path}". Patch the client class to route by that key:
    # the primary account raises the limit; the switched account succeeds.
    primary_key = f"tok:{primary_sel['auth_path']}"

    def _fake_openai(**kwargs):
        api_key = kwargs.get("api_key")
        client = SimpleNamespace()
        if api_key == primary_key:
            client.responses = _PrimaryResponses()
        else:
            client.responses = _SwitchResponses()
        client.api_key = api_key
        return client

    mgr_patcher = _start_codex_mgr_mock()
    oai_patcher = mock.patch.object(openai_mod, "OpenAI", _fake_openai)
    oai_patcher.start()
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        # Sanity: primary is the account we scripted to fail.
        assert primary_name in (a["path"] for a in accounts)
        resp = chat.send_stream("please answer")
        assert resp.text == "hello back"
        # Canonical history: exactly one user + one assistant entry (no double).
        roles = [e.role for e in interface.entries]
        assert roles.count("user") == 1
        assert roles.count("assistant") == 1
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()


# --------------------------------------------------------------------------
# Hook-preservation: post-hook baseline survives failover (rereview blocker)
# --------------------------------------------------------------------------


def _codex_success_events(text="ok"):
    from types import SimpleNamespace
    return [
        SimpleNamespace(type="response.output_text.delta", delta=text,
                        item=None, response=None, item_id=None, text=None),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="resp_ok",
                usage=SimpleNamespace(
                    input_tokens=5, output_tokens=7,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                ),
            ),
            delta=None, item=None, item_id=None, text=None,
        ),
    ]


def _install_real_codex_openai(tui_dir, sel_auth_path, *, fail_keys, capture):
    """Patch CodexTokenManager + openai.OpenAI so each account's real
    CodexResponsesSession dispatches to a faked client. ``fail_keys`` is the set
    of resolved auth paths whose client raises a structural usage-limit 429;
    others succeed. ``capture`` maps api_key -> list of wire ``input`` payloads."""
    import openai as openai_mod

    class _UL(Exception):
        def __init__(self):
            super().__init__("usage limit reached")
            self.status_code = 429
            self.body = {"error": {"code": "usage_limit_reached"}}

    class _Responses:
        def __init__(self, api_key, fail):
            self._api_key = api_key
            self._fail = fail
        def create(self, **kwargs):
            capture.setdefault(self._api_key, []).append(kwargs.get("input"))
            if self._fail:
                raise _UL()
            return iter(_codex_success_events())

    fail_toks = {f"tok:{p}" for p in fail_keys}

    def _fake_openai(**kwargs):
        from types import SimpleNamespace
        api_key = kwargs.get("api_key")
        c = SimpleNamespace(api_key=api_key)
        c.responses = _Responses(api_key, api_key in fail_toks)
        return c

    mgr_patcher = _start_codex_mgr_mock()
    oai_patcher = mock.patch.object(openai_mod, "OpenAI", _fake_openai)
    oai_patcher.start()
    return mgr_patcher, oai_patcher


def _wire_types(input_items):
    """Types of a Codex Responses ``input`` payload (None-safe)."""
    return [
        (i.get("type") if isinstance(i, dict) else None)
        for i in (input_items or [])
    ]


def test_failover_preserves_pre_request_hook_additions_on_switch(tui_dir, tmp_path):
    """The rereview blocker: a real primary CodexResponsesSession whose
    pre_request_hook splices an (assistant tool_call, user tool_result) pair
    before dispatch, then a usage-limit 429. The hook-added pair MUST appear in
    the primary wire, the alternate wire, and the final canonical interface —
    exactly once — and the hook MUST NOT run again on the alternate."""
    from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, ToolResultBlock

    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    primary_path = sel["auth_path"]

    interface = ChatInterface()
    capture: dict = {}

    hook_calls = {"n": 0}

    def _hook(iface):
        # Splice a notification (assistant tool_call, user tool_result) pair,
        # exactly as the kernel tc-wake seam does before dispatch.
        hook_calls["n"] += 1
        iface.add_assistant_message(
            [ToolCallBlock(id="call_notif", name="notification", args={"action": "check"})],
            model="gpt-5.5", provider="codex",
        )
        iface.add_tool_results(
            [ToolResultBlock(id="call_notif", name="notification", content={"ok": True})]
        )

    mgr_patcher, oai_patcher = _install_real_codex_openai(
        tui_dir, primary_path, fail_keys={primary_path}, capture=capture,
    )
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        chat.pre_request_hook = _hook
        resp = chat.send("please answer")
        assert resp.text == "ok"

        # The hook ran exactly ONCE (not re-run on the alternate).
        assert hook_calls["n"] == 1

        # Primary wire contained the hook-spliced function_call + output.
        primary_wire = capture[f"tok:{primary_path}"][0]
        assert "function_call" in _wire_types(primary_wire)
        assert "function_call_output" in _wire_types(primary_wire)

        # The ALTERNATE wire also contained them (baseline preserved the hook adds).
        alt_key = next(k for k in capture if k != f"tok:{primary_path}")
        alt_wire = capture[alt_key][0]
        assert "function_call" in _wire_types(alt_wire)
        assert "function_call_output" in _wire_types(alt_wire)

        # Final canonical interface retains the pair exactly once (+ the user turn
        # and the alternate's assistant reply).
        tool_calls = [
            b for e in interface.entries for b in e.content
            if isinstance(b, ToolCallBlock) and b.name == "notification"
        ]
        tool_results = [
            b for e in interface.entries for b in e.content
            if isinstance(b, ToolResultBlock) and b.name == "notification"
        ]
        assert len(tool_calls) == 1
        assert len(tool_results) == 1
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()


def test_failover_hook_additions_on_wire_but_dropped_on_terminal(tui_dir, tmp_path):
    """S0/H ownership: hook additions made FOR a failed logical call ride the
    retry wire (H) to every alternate, but on terminal exhaustion the interface
    restores to S0 — here empty — so the hook pair (produced only for the failed
    call) is correctly DROPPED, not committed. The hook still runs exactly once."""
    from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, ToolResultBlock

    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")

    interface = ChatInterface()  # S0 is empty
    capture: dict = {}
    hook_calls = {"n": 0}

    def _hook(iface):
        hook_calls["n"] += 1
        iface.add_assistant_message(
            [ToolCallBlock(id="call_notif", name="notification", args={"action": "check"})],
            model="gpt-5.5", provider="codex",
        )
        iface.add_tool_results(
            [ToolResultBlock(id="call_notif", name="notification", content={"ok": True})]
        )

    all_paths = {str(tui_dir / n) for n in ("a.json", "b.json", "c.json")}
    mgr_patcher, oai_patcher = _install_real_codex_openai(
        tui_dir, sel["auth_path"], fail_keys=all_paths, capture=capture,
    )
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        chat.pre_request_hook = _hook

        with pytest.raises(Exception):
            chat.send("please answer")

        # The hook ran exactly once (not re-run per alternate).
        assert hook_calls["n"] == 1
        # Every alternate wire carried the hook pair (H rode the retry wire).
        for key, wires in capture.items():
            assert "function_call" in _wire_types(wires[0])
            assert "function_call_output" in _wire_types(wires[0])
        # Three distinct accounts were tried.
        assert len(capture) == 3
        # Terminal restore to S0 (empty): the failed call's request AND its hook
        # additions are dropped — nothing committed.
        assert list(interface.entries) == []
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()


def test_failover_ordinary_send_no_duplicate_user_message(tui_dir, tmp_path):
    """A plain ``send('hello')`` that switches once must not duplicate the user
    message: the alternate replays the post-hook baseline (message included) with
    message=None, so exactly one user turn exists at the end."""
    from lingtai.kernel.llm.interface import ChatInterface, TextBlock

    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")

    interface = ChatInterface()
    capture: dict = {}
    mgr_patcher, oai_patcher = _install_real_codex_openai(
        tui_dir, sel["auth_path"], fail_keys={sel["auth_path"]}, capture=capture,
    )
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send("hello")
        assert resp.text == "ok"
        user_turns = [
            e for e in interface.entries
            if e.role == "user" and any(
                isinstance(b, TextBlock) and b.text == "hello" for b in e.content
            )
        ]
        assert len(user_turns) == 1
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()


# --------------------------------------------------------------------------
# S0 / H ownership model (final rereview blockers)
# --------------------------------------------------------------------------
#   S0 = exact pre-attempt canonical list (caller-owned terminal state).
#   H  = exact post-primary-hook retry-wire baseline (S0 + staged request + hook).
# Retry restores H exactly and replays message=None; terminal restores S0 exactly.


def _install_real_codex_openai_artifact(tui_dir, *, fail_keys, capture,
                                        interface, artifact_key, artifact_text):
    """Like _install_real_codex_openai, but the client for ``artifact_key`` (a
    resolved auth path) appends an assistant ``artifact_text`` entry to
    ``interface`` (after the post-hook baseline) and THEN raises the usage-limit
    error — reproducing a failed-attempt artifact left in canonical history."""
    import openai as openai_mod
    import lingtai.auth.codex as codex_mod
    from lingtai.kernel.llm.interface import TextBlock

    class _UL(Exception):
        def __init__(self):
            super().__init__("usage limit reached")
            self.status_code = 429
            self.body = {"error": {"code": "usage_limit_reached"}}

    artifact_tok = f"tok:{artifact_key}"
    fail_toks = {f"tok:{p}" for p in fail_keys}

    class _Responses:
        def __init__(self, api_key, fail):
            self._api_key = api_key
            self._fail = fail
        def create(self, **kwargs):
            capture.setdefault(self._api_key, []).append(kwargs.get("input"))
            if self._api_key == artifact_tok:
                interface.add_assistant_message(
                    [TextBlock(text=artifact_text)], model="gpt-5.5", provider="codex",
                )
            if self._fail:
                raise _UL()
            return iter(_codex_success_events())

    def _fake_openai(**kwargs):
        from types import SimpleNamespace
        api_key = kwargs.get("api_key")
        c = SimpleNamespace(api_key=api_key)
        c.responses = _Responses(api_key, api_key in fail_toks)
        return c

    mgr_patcher = _start_codex_mgr_mock()
    oai_patcher = mock.patch.object(openai_mod, "OpenAI", _fake_openai)
    oai_patcher.start()
    return mgr_patcher, oai_patcher


def test_failover_terminal_ordinary_send_restores_empty_S0(tui_dir, tmp_path):
    """Reproduction 1: empty interface + all accounts usage-limited + ordinary
    ``send('terminal-user')`` re-raises AND leaves the interface EMPTY (the failed
    request is a failed-send artifact, not caller-owned state)."""
    from lingtai.kernel.llm.interface import ChatInterface
    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    interface = ChatInterface()  # S0 is empty
    capture: dict = {}
    all_paths = {str(tui_dir / n) for n in ("a.json", "b.json", "c.json")}
    mgr_patcher, oai_patcher = _install_real_codex_openai(
        tui_dir, sel["auth_path"], fail_keys=all_paths, capture=capture,
    )
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(Exception):
            chat.send("terminal-user")
        # S0 restored EXACTLY: empty. No failed request committed.
        assert list(interface.entries) == []
        # Three distinct accounts tried.
        assert len(capture) == 3
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()


def test_failover_terminal_ordinary_send_restores_nonempty_S0_identity(tui_dir, tmp_path):
    """Nonempty S0 + ordinary send terminal -> exact original S0 object
    identities/order, with no staged request/hook/artifact left behind."""
    from lingtai.kernel.llm.interface import ChatInterface, TextBlock
    accounts = [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    interface = ChatInterface()
    # A prior completed turn — this IS caller-owned S0 and must survive.
    interface.add_user_message("earlier")
    interface.add_assistant_message([TextBlock(text="earlier-reply")], model="gpt-5.5", provider="codex")
    s0_objs = list(interface.entries)

    capture: dict = {}
    all_paths = {str(tui_dir / n) for n in ("a.json", "b.json", "c.json")}
    mgr_patcher, oai_patcher = _install_real_codex_openai(
        tui_dir, sel["auth_path"], fail_keys=all_paths, capture=capture,
    )
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        with pytest.raises(Exception):
            chat.send("terminal-user")
        # EXACT S0 object identities and order — no 'terminal-user', no artifact.
        assert list(interface.entries) == s0_objs
        assert all(a is b for a, b in zip(interface.entries, s0_objs))
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()


def test_failover_failed_attempt_artifact_never_crosses_to_alternate(tui_dir, tmp_path):
    """Reproduction 2: a failed-attempt assistant artifact appended AFTER the
    post-hook baseline must NOT cross into the alternate wire or final history."""
    from lingtai.kernel.llm.interface import ChatInterface, TextBlock
    accounts = [{"path": "a.json", "weight": 1}, {"path": "b.json", "weight": 1}]
    _write_pool(tui_dir, accounts)
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    sel = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}, model="gpt-5.5")
    primary_path = sel["auth_path"]
    interface = ChatInterface()
    capture: dict = {}
    # Primary appends FAILED_ATTEMPT_ARTIFACT then raises; the alternate succeeds.
    mgr_patcher, oai_patcher = _install_real_codex_openai_artifact(
        tui_dir, fail_keys={primary_path}, capture=capture, interface=interface,
        artifact_key=primary_path, artifact_text="FAILED_ATTEMPT_ARTIFACT",
    )
    try:
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex-pool")
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s", interface=interface)
        resp = chat.send("artifact-case")
        assert resp.text == "ok"
        # The artifact is absent from the ALTERNATE wire.
        alt_key = next(k for k in capture if k != f"tok:{primary_path}")
        alt_wire = capture[alt_key][0]
        alt_texts = [
            b.get("text")
            for i in (alt_wire or []) if isinstance(i, dict)
            for b in (i.get("content") or []) if isinstance(b, dict)
        ]
        assert "FAILED_ATTEMPT_ARTIFACT" not in (alt_texts or [])
        # The artifact is absent from FINAL canonical history.
        final_texts = [
            b.text for e in interface.entries for b in e.content
            if isinstance(b, TextBlock)
        ]
        assert "FAILED_ATTEMPT_ARTIFACT" not in final_texts
        # The served response and the request survive.
        assert "artifact-case" in final_texts
    finally:
        oai_patcher.stop()
        mgr_patcher.stop()
