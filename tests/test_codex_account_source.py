"""Tests for the native Codex AccountSource seam.

The source layer owns pool parsing, safe identities, exclusion, and static/dynamic
weight arithmetic.  Factory checks prove every Codex provider spelling builds
the same lazy native adapter; request-time account binding, AED ownership, and
partial-stream safety live in ``test_codex_native_multiaccount.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

import lingtai  # noqa: F401  (registers adapters)
from lingtai.auth.codex_account_source import (
    AccountCandidate,
    FixedAccountSource,
    NoCandidateError,
    WeightedAccountSource,
    _is_comparable_fraction,
)
from lingtai.llm._register import _normalize_service_tier


# ===========================================================================
# helpers
# ===========================================================================


@pytest.fixture()
def tui_dir(tmp_path, monkeypatch):
    d = tmp_path / "tui"
    d.mkdir()
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(d))
    return d


def _write_pool(tui_dir: Path, accounts, *, name="codex-auth-pool.json"):
    path = tui_dir / name
    path.write_text(json.dumps({"version": 1, "accounts": accounts}), encoding="utf-8")
    return path


def _write_pool_v2(tui_dir: Path, models, *, name="codex-auth-pool.json"):
    path = tui_dir / name
    path.write_text(json.dumps({"version": 2, "models": models}), encoding="utf-8")
    return path


# ===========================================================================
# AccountCandidate
# ===========================================================================


def test_candidate_sha8_is_stable():
    c = AccountCandidate(
        auth_ref="/tmp/token.json", source_ref="token.json",
        source_index=0, weight=1,
    )
    assert len(c.auth_path_sha8) == 8
    c2 = AccountCandidate(
        auth_ref="/tmp/token.json", source_ref="token.json",
        source_index=0, weight=99,
    )
    assert c.auth_path_sha8 == c2.auth_path_sha8


def test_candidate_sha8_differs_by_path():
    c1 = AccountCandidate(auth_ref="/a", source_ref="a", source_index=0, weight=1)
    c2 = AccountCandidate(auth_ref="/b", source_ref="b", source_index=0, weight=1)
    assert c1.auth_path_sha8 != c2.auth_path_sha8


# ===========================================================================
# FixedAccountSource
# ===========================================================================


def test_fixed_always_returns_same():
    src = FixedAccountSource("/tmp/t.json")
    c1 = src.select()
    c2 = src.select()
    assert c1 == c2
    assert c1.auth_ref == "/tmp/t.json"
    assert c1.weight == 1


def test_fixed_with_exclude_raises():
    src = FixedAccountSource("/tmp/t.json")
    c = src.select()
    with pytest.raises(NoCandidateError):
        src.select(exclude={c.auth_path_sha8})


# ===========================================================================
# WeightedAccountSource — pool parsing / no-candidate
# ===========================================================================


def test_pool_parse_and_aliases(tui_dir):
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 2},
        {"path": "  ", "weight": 1},
        {"path": "zero.json", "weight": 0},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    assert src.pool_size == 2


def test_empty_pool_raises_no_candidate(tui_dir):
    _write_pool(tui_dir, [])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    with pytest.raises(NoCandidateError):
        src.select()


def test_missing_pool_returns_zero_size(tui_dir):
    src = WeightedAccountSource(tui_dir / "nope.json", tui_dir)
    assert src.pool_size == 0


# ===========================================================================
# Exclusion
# ===========================================================================


def test_exclude_by_sha8(tui_dir):
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    # Find the sha8 for a.json.
    a_path = str(tui_dir / "a.json")
    import hashlib
    a_sha8 = hashlib.sha256(a_path.encode()).hexdigest()[:8]

    for _ in range(30):
        c = src.select(exclude={a_sha8})
        assert c.auth_ref != a_path


def test_exclude_all_eligible_raises(tui_dir):
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    sha8s = {
        hashlib.sha256(str(tui_dir / f"{n}.json").encode()).hexdigest()[:8]
        for n in ("a", "b")
    }
    with pytest.raises(NoCandidateError):
        src.select(exclude=sha8s)


def test_duplicate_aliases_excluded_together(tui_dir):
    """Both aliases pointing at the same file are excluded by one sha8."""
    _write_pool(tui_dir, [
        {"path": "dup.json", "weight": 1},
        {"path": "./dup.json", "weight": 1},   # alias
        {"path": "other.json", "weight": 100},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    dup_sha8 = hashlib.sha256(str(tui_dir / "dup.json").encode()).hexdigest()[:8]
    for _ in range(30):
        c = src.select(exclude={dup_sha8})
        # Neither alias should be selected.
        assert Path(c.auth_ref).name == "other.json"


def test_source_index_anchors_to_full_list_not_filtered_position(tui_dir):
    """``source_index`` is the position in the FULL validated pool list, not
    a re-numbered index among the surviving (post-exclude) entries."""
    _write_pool(tui_dir, [
        {"path": "excluded.json", "weight": 1000},  # idx 0
        {"path": "light.json", "weight": 1},          # idx 1
        {"path": "heavy.json", "weight": 9},           # idx 2
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    excluded_sha8 = hashlib.sha256(
        str(tui_dir / "excluded.json").encode()
    ).hexdigest()[:8]

    for _ in range(30):
        c = src.select(exclude={excluded_sha8})
        assert c.source_index in (1, 2)  # never 0, and never re-numbered to (0, 1)
        if Path(c.auth_ref).name == "light.json":
            assert c.source_index == 1
        else:
            assert c.source_index == 2


def test_weights_preserved_among_survivors_after_exclusion(tui_dir):
    """Excluding one account does not distort the relative weights of the
    remaining ones — a 1:9 ratio between survivors stays 1:9, regardless of
    how large the excluded account's own weight was."""
    _write_pool(tui_dir, [
        {"path": "excluded.json", "weight": 1000},  # would dominate if not excluded
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    excluded_sha8 = hashlib.sha256(
        str(tui_dir / "excluded.json").encode()
    ).hexdigest()[:8]

    picks = {"light.json": 0, "heavy.json": 0}
    for _ in range(300):
        c = src.select(exclude={excluded_sha8})
        name = Path(c.auth_ref).name
        assert name != "excluded.json"
        picks[name] += 1

    assert picks["light.json"] + picks["heavy.json"] == 300
    # Roughly 1:9 among survivors (generous bound against sampling noise).
    assert picks["heavy.json"] > picks["light.json"] * 3


def test_select_rereads_pool_file_fresh_every_call(tui_dir):
    """No caching at any layer: a pool-file edit between two ``select`` calls
    is observed on the very next call — proves the fix for the __init__
    caching bug (accounts were previously frozen at construction time)."""
    _write_pool(tui_dir, [{"path": "only.json", "weight": 1}])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    assert src.pool_size == 1
    first = src.select()
    assert Path(first.auth_ref).name == "only.json"

    # Edit the pool file in place — no new WeightedAccountSource constructed.
    _write_pool(tui_dir, [
        {"path": "only.json", "weight": 1},
        {"path": "added.json", "weight": 1},
    ])
    assert src.pool_size == 2  # observed immediately, same source instance

    names = {Path(src.select().auth_ref).name for _ in range(30)}
    assert "added.json" in names  # newly-added entry is selectable right away


def test_explicit_snapshot_keeps_quota_and_selection_on_one_live_state(tui_dir):
    """One logical draw can span quota scan + select without mixing a pool
    edit between them; the next unsnapshotted operation still sees the edit."""
    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    snapshot = src.snapshot()

    _write_pool(tui_dir, [{"path": "b.json", "weight": 1}])

    targets = src.quota_targets(snapshot=snapshot)
    assert [Path(auth_ref).name for auth_ref, _ in targets] == ["a.json"]
    assert Path(src.select(snapshot=snapshot).auth_ref).name == "a.json"
    assert Path(src.select().auth_ref).name == "b.json"


# ===========================================================================
# Static deterministic draw
# ===========================================================================


def test_static_weighted_draw_is_deterministic_with_mock(tui_dir, monkeypatch):
    """With a mocked uniform draw, verify the static path produces expected
    raw weights."""
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)

    # Force point just above weight 1 boundary -> heavy wins.
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=1.0 / 10.0 + 0.001
    ):
        c = src.select()
        assert Path(c.auth_ref).name == "heavy.json"

    # Force point just below weight 1 boundary -> light wins.
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.5 / 10.0
    ):
        c = src.select()
        assert Path(c.auth_ref).name == "light.json"


def test_static_weighted_heavier_wins_majority(tui_dir):
    """Across many draws the heavier account wins the majority."""
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    heavy = sum(1 for _ in range(200) if Path(src.select().auth_ref).name == "heavy.json")
    assert heavy > 140  # 9:1 ratio → expect ~180, loose bound


# ===========================================================================
# Dynamic linear formula
# ===========================================================================


def test_dynamic_scales_weights_linearly(tui_dir):
    """raw_i = weight_i * quota_left_i, then normalize."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 9},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    a_sha8 = hashlib.sha256(str(tui_dir / "a.json").encode()).hexdigest()[:8]
    b_sha8 = hashlib.sha256(str(tui_dir / "b.json").encode()).hexdigest()[:8]

    # a at 80%, b at 40%:  raw_a=0.8, raw_b=3.6 -> p_a=0.8/4.4 ≈ 18.2%
    snapshot = {a_sha8: 0.8, b_sha8: 0.4}

    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.1
    ):
        c = src.select(quota_left_snapshot=snapshot)
        # 0.1 < 0.8 → a wins
        assert Path(c.auth_ref).name == "a.json"

    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.19
    ):
        c = src.select(quota_left_snapshot=snapshot)
        # 0.19 > 0.8/4.4≈0.1818 → b wins
        assert Path(c.auth_ref).name == "b.json"


def test_dynamic_with_equal_base_weights_is_quota_proportion(tui_dir):
    """All base=1 + dynamic → pure quota-left proportion."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    a_sha8 = hashlib.sha256(str(tui_dir / "a.json").encode()).hexdigest()[:8]
    b_sha8 = hashlib.sha256(str(tui_dir / "b.json").encode()).hexdigest()[:8]

    # a:80%, b:40% → raw: 0.8, 0.4 → p_a = 0.8/1.2 ≈ 66.7%
    snapshot = {a_sha8: 0.8, b_sha8: 0.4}
    a_count = 0
    for _ in range(100):
        c = src.select(quota_left_snapshot=snapshot)
        if Path(c.auth_ref).name == "a.json":
            a_count += 1
    assert 50 <= a_count <= 85  # ~66.7% expected


def test_dynamic_zero_quota_becomes_zero_weight(tui_dir):
    """quota_left=0 → raw=0 → that account is never selected."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    a_sha8 = hashlib.sha256(str(tui_dir / "a.json").encode()).hexdigest()[:8]
    b_sha8 = hashlib.sha256(str(tui_dir / "b.json").encode()).hexdigest()[:8]
    snapshot = {a_sha8: 0.0, b_sha8: 1.0}
    for _ in range(30):
        c = src.select(quota_left_snapshot=snapshot)
        assert Path(c.auth_ref).name == "b.json"


def test_dynamic_raises_when_all_zero(tui_dir):
    """All quota=0 → total raw=0 → NoCandidateError."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    a_sha8 = hashlib.sha256(str(tui_dir / "a.json").encode()).hexdigest()[:8]
    b_sha8 = hashlib.sha256(str(tui_dir / "b.json").encode()).hexdigest()[:8]
    snapshot = {a_sha8: 0.0, b_sha8: 0.0}
    with pytest.raises(NoCandidateError):
        src.select(quota_left_snapshot=snapshot)


# ===========================================================================
# Static fallback for incomplete snapshot
# ===========================================================================


def test_incomplete_snapshot_falls_back_to_static(tui_dir):
    """Missing/non-comparable entry → whole draw static."""
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    light_sha8 = hashlib.sha256(str(tui_dir / "light.json").encode()).hexdigest()[:8]
    # Only provide snapshot for 'light' — missing 'heavy' → fall back static
    snapshot = {light_sha8: 0.5}

    heavy = sum(
        1 for _ in range(100)
        if Path(src.select(quota_left_snapshot=snapshot).auth_ref).name == "heavy.json"
    )
    assert heavy > 60  # static 1:9 ratio


def test_none_value_in_snapshot_falls_back_to_static(tui_dir):
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    light_sha8 = hashlib.sha256(str(tui_dir / "light.json").encode()).hexdigest()[:8]
    heavy_sha8 = hashlib.sha256(str(tui_dir / "heavy.json").encode()).hexdigest()[:8]
    # None is not a comparable fraction.
    snapshot: dict = {light_sha8: 0.5, heavy_sha8: None}
    heavy = sum(
        1 for _ in range(100)
        if Path(src.select(quota_left_snapshot=snapshot).auth_ref).name == "heavy.json"
    )
    assert heavy > 60


def test_bool_in_snapshot_is_not_comparable(tui_dir):
    """Bools are rejected as comparable fractions."""
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    light_sha8 = hashlib.sha256(str(tui_dir / "light.json").encode()).hexdigest()[:8]
    heavy_sha8 = hashlib.sha256(str(tui_dir / "heavy.json").encode()).hexdigest()[:8]
    # True is a bool → not comparable → fallback.
    snapshot: dict = {light_sha8: 0.5, heavy_sha8: True}
    heavy = sum(
        1 for _ in range(100)
        if Path(src.select(quota_left_snapshot=snapshot).auth_ref).name == "heavy.json"
    )
    assert heavy > 60


def test_nan_in_snapshot_not_comparable(tui_dir):
    _write_pool(tui_dir, [
        {"path": "x.json", "weight": 1},
        {"path": "y.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    x_sha8 = hashlib.sha256(str(tui_dir / "x.json").encode()).hexdigest()[:8]
    y_sha8 = hashlib.sha256(str(tui_dir / "y.json").encode()).hexdigest()[:8]
    snapshot: dict = {x_sha8: float("nan"), y_sha8: 0.5}
    # Should fall back to static (both equal weight 1) — no crash.
    c = src.select(quota_left_snapshot=snapshot)
    assert c.auth_ref in (str(tui_dir / "x.json"), str(tui_dir / "y.json"))


# ===========================================================================
# Weighted source has no network/quota/retry/chat/transport deps
# ===========================================================================


def test_weighted_source_imports_are_clean():
    """Prove the module does not import network/quota/retry/chat/transport."""
    import ast
    import inspect
    from lingtai.auth import codex_account_source as m

    src = inspect.getsource(m)
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)

    # Allowed: stdlib + codex_pool (pool parsing only)
    suspicious = {
        "httpx", "requests", "aiohttp", "openai",
        "lingtai.llm", "lingtai.kernel",
    }
    found_suspicious = imports & suspicious
    assert not found_suspicious, f"Unexpected imports: {found_suspicious}"


# ===========================================================================
# service_tier normalization
# ===========================================================================


def test_service_tier_fast_normalizes_to_priority():
    assert _normalize_service_tier("fast") == "priority"


def test_service_tier_none_omits_field():
    assert _normalize_service_tier(None) is None


def test_service_tier_empty_string_omits():
    assert _normalize_service_tier("") is None


def test_service_tier_whitespace_only_omits():
    assert _normalize_service_tier("  ") is None


def test_service_tier_unsupported_raises():
    with pytest.raises(ValueError, match="auto"):
        _normalize_service_tier("auto")


def test_service_tier_non_string_raises():
    with pytest.raises(ValueError, match="string"):
        _normalize_service_tier(123)


# ===========================================================================
# is_comparable_fraction
# ===========================================================================


def test_comparable_fraction_valid():
    assert _is_comparable_fraction(0.0) is True
    assert _is_comparable_fraction(1.0) is True
    assert _is_comparable_fraction(0.5) is True
    assert _is_comparable_fraction(0) is True
    assert _is_comparable_fraction(1) is True


def test_comparable_fraction_invalid():
    assert _is_comparable_fraction(-0.1) is False
    assert _is_comparable_fraction(1.1) is False
    assert _is_comparable_fraction(float("nan")) is False
    assert _is_comparable_fraction(float("inf")) is False
    assert _is_comparable_fraction(True) is False
    assert _is_comparable_fraction(None) is False
    assert _is_comparable_fraction("0.5") is False


# ===========================================================================
# Model-classified (v2) pool: exact-category parsing
# ===========================================================================


def test_v2_pool_exact_category(tui_dir):
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 3}],
        "gpt-5.5": [{"path": "old.json", "weight": 1}],
    })
    src = WeightedAccountSource(
        tui_dir / "codex-auth-pool.json", tui_dir, model="gpt-5.6-sol",
    )
    assert src.pool_size == 1
    c = src.select()
    assert c.auth_ref == str(tui_dir / "sol.json")


def test_v2_no_exact_category_empty(tui_dir):
    _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [{"path": "sol.json", "weight": 1}],
    })
    src = WeightedAccountSource(
        tui_dir / "codex-auth-pool.json", tui_dir, model="gpt-5.6-terra",
    )
    assert src.pool_size == 0


# ===========================================================================
# Factory integration: all Codex spellings use the native AccountSource seam
# ===========================================================================


def _codex_adapter(provider, defaults, model="gpt-5.5"):
    from lingtai.llm.service import LLMService

    svc = LLMService(
        provider=provider,
        model=model,
        provider_defaults={provider: defaults},
    )
    return svc.get_adapter(provider)


@pytest.mark.parametrize("provider", ["codex", "codex-pool", "codex_pool"])
def test_codex_alias_factories_bind_weighted_source_lazily(tui_dir, provider):
    """Every spelling builds the same native adapter and consumes no eager draw."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 2}])
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        adapter = _codex_adapter(provider, {})

        assert isinstance(adapter._codex_account_source, WeightedAccountSource)
        assert adapter._codex_account_source.pool_size == 1
        assert adapter._codex_current_selection == {}
        mgr_cls.assert_not_called()


def test_codex_pool_selection_is_per_call_not_session_static(tui_dir):
    """Repeated source draws can choose different candidates; there is no stickiness."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    names = {Path(src.select().auth_ref).name for _ in range(50)}
    assert len(names) > 1, f"Expected multiple accounts, got only {names!r}"


def test_service_tier_fast_flows_to_adapter(tui_dir):
    """service_tier: fast reaches the one adapter as wire-level priority."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    adapter = _codex_adapter("codex-pool", {"service_tier": "fast"})
    assert adapter._codex_service_tier == "priority"


def test_service_tier_absent_omits():
    """No service_tier config leaves the native adapter tier unset."""
    adapter = _codex_adapter("codex", {})
    assert adapter._codex_service_tier is None


def test_service_tier_invalid_fails_loud():
    """Unsupported service_tier values fail at the common factory boundary."""
    with pytest.raises(ValueError, match="auto"):
        _normalize_service_tier("auto")

def test_no_candidate_error_diagnostic_fields_are_safe_and_structured():
    plain = NoCandidateError("No eligible account remaining")
    assert plain.args == ("No eligible account remaining",)
    assert plain.diagnostic_fields() == {}

    exc = NoCandidateError(
        "No eligible account remaining",
        reason="all_zero_quota",
        diagnostics={
            "codex_account_source": "weighted",
            "codex_account_pool_size": 2,
            "codex_account_zero_quota_count": 2,
            "secret_path": "/tmp/token.json",
            "codex_account_auth_ref": "/tmp/token.json",
            "codex_account_freeform": "token-value",
            "no_candidate_token": "secret-token-value",
        },
    )

    fields = exc.diagnostic_fields()
    assert fields == {
        "no_candidate_reason": "all_zero_quota",
        "codex_account_source": "weighted",
        "codex_account_pool_size": 2,
        "codex_account_zero_quota_count": 2,
    }
    assert "source=weighted" in str(exc)
    assert "pool=2" in str(exc)
    assert "zero_quota=2" in str(exc)
    for leaked in (
        "secret_path",
        "auth_ref",
        "/tmp/token.json",
        "freeform",
        "token-value",
        "no_candidate_token",
        "secret-token-value",
    ):
        assert leaked not in str(exc)
        assert leaked not in repr(fields)


def test_no_candidate_error_rejects_freeform_reason_and_source_text():
    exc = NoCandidateError(
        "No eligible account remaining",
        reason="/tmp/token.json",
        diagnostics={
            "codex_account_source": "/tmp/token.json",
            "codex_account_pool_size": 1,
        },
    )

    fields = exc.diagnostic_fields()
    assert fields == {
        "no_candidate_reason": "unknown",
        "codex_account_pool_size": 1,
    }
    assert "/tmp/token.json" not in str(exc)
    assert "/tmp/token.json" not in repr(fields)


def test_weighted_no_candidate_error_reports_empty_pool(tui_dir):
    pool = _write_pool(tui_dir, [])
    source = WeightedAccountSource(pool, tui_dir)

    with pytest.raises(NoCandidateError) as excinfo:
        source.select()

    fields = excinfo.value.diagnostic_fields()
    assert fields["no_candidate_reason"] == "no_eligible_after_exclude"
    assert fields["codex_account_source"] == "weighted"
    assert fields["codex_account_pool_size"] == 0
    assert fields["codex_account_excluded_count"] == 0
    assert fields["codex_account_eligible_count"] == 0


def test_weighted_no_candidate_error_reports_all_excluded(tui_dir):
    pool = _write_pool(tui_dir, [{"path": "one.json", "weight": 1}])
    source = WeightedAccountSource(pool, tui_dir)
    snapshot = source.snapshot()

    with pytest.raises(NoCandidateError) as excinfo:
        source.select(exclude={snapshot[0].sha8}, snapshot=snapshot)

    fields = excinfo.value.diagnostic_fields()
    assert fields["no_candidate_reason"] == "no_eligible_after_exclude"
    assert fields["codex_account_pool_size"] == 1
    assert fields["codex_account_excluded_count"] == 1
    assert fields["codex_account_eligible_count"] == 0


def test_weighted_no_candidate_error_reports_all_zero_quota(tui_dir):
    pool = _write_pool(
        tui_dir,
        [
            {"path": "one.json", "weight": 1},
            {"path": "two.json", "weight": 1},
        ],
    )
    source = WeightedAccountSource(pool, tui_dir)
    snapshot = source.snapshot()
    quota = {account.sha8: 0.0 for account in snapshot}

    with pytest.raises(NoCandidateError) as excinfo:
        source.select(quota_left_snapshot=quota, snapshot=snapshot)

    fields = excinfo.value.diagnostic_fields()
    assert fields["no_candidate_reason"] == "zero_effective_weight"
    assert fields["codex_account_pool_size"] == 2
    assert fields["codex_account_eligible_count"] == 2
    assert fields["codex_account_quota_snapshot_present"] is True
    assert fields["codex_account_quota_snapshot_count"] == 2
    assert fields["codex_account_zero_effective_weight_count"] == 2
