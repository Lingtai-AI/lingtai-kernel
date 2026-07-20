"""Tests for the thin AccountSource seam — FixedAccountSource + WeightedAccountSource.

Tests are minimal and focused per the codex-pool thin-wrapper v3 spec:
  * Pool parse / aliases / positive weights / no-candidate errors.
  * Exclusion by sha8 identity.
  * Static deterministic weighted draw.
  * Dynamic linear formula and deterministic draw.
  * Whole-draw static fallback for incomplete snapshot.
  * Fixed/weighted use the common Codex path (factory integration).
  * Selection metadata per-call rather than session-static.
  * Proof weighted source has no quota/network/retry/chat/transport deps.
  * service_tier normalization.
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
# Factory integration: codex-pool uses WeightedAccountSource
# ===========================================================================


def _codex_pool_adapter(provider, defaults, model="gpt-5.5"):
    from lingtai.llm.service import LLMService
    svc = LLMService(
        provider=provider, model=model,
        provider_defaults={provider: defaults},
    )
    return svc.get_adapter(provider)


def test_codex_pool_factory_does_not_select_eagerly(tui_dir, tmp_path):
    """Construction selects nothing — selection happens per ``create_chat``.

    The old sticky-session architecture selected once at factory-construction
    time and stamped it on the adapter. The spec explicitly moves selection to
    the attempt boundary, so at construction the pool file must not even be
    read for a candidate — only ``pool_size`` (via a fresh snapshot) matters
    for the empty-pool/legacy-fallback branch.
    """
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    try:
        cls.return_value.get_access_token.return_value = "fake-token"
        cls.return_value.get_account_id.return_value = None
        adapter = _codex_pool_adapter("codex-pool", {})
        # No account has been bound yet — construction never called
        # CodexTokenManager with the pool's token_path.
        cls.assert_called_with()
        assert adapter.codex_pool_selection == {"fallback": "legacy_default"}
    finally:
        mgr.stop()


def test_codex_pool_factory_stamps_selection_on_chat_not_adapter(tui_dir):
    """The real, non-placeholder selection lives on the CHAT ``create_chat``
    returns — never eagerly on the adapter (no session-static stickiness)."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 2}])
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    try:
        cls.return_value.get_access_token.return_value = "fake-token"
        cls.return_value.get_account_id.return_value = None
        with mock.patch(
            "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
            return_value=mock.MagicMock(),
        ):
            adapter = _codex_pool_adapter("codex-pool", {})
            # Placeholder — no real candidate is bound until create_chat.
            assert adapter.codex_pool_selection == {"fallback": "legacy_default"}
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
            selection = chat.codex_pool_selection
            assert selection["source_ref"] in ("work.json",)
            assert selection["pool_size"] == 1
            assert selection["weight"] == 2
            assert "auth_path_sha8" in selection
            assert "failover" not in selection  # first attempt, no prior failure
    finally:
        mgr.stop()


def test_codex_pool_selection_redacts_absolute_source_ref(tui_dir, tmp_path):
    """An absolute pool-entry path never reaches the ``codex_pool_selection``
    breadcrumb on the chat — it is redacted, same as the raw source-ref
    helper's own unit test, but proven end-to-end through the real factory
    and selection-stamping path a caller actually observes."""
    abs_path = str(tmp_path / "secret" / "account.json")
    _write_pool(tui_dir, [{"path": abs_path, "weight": 1}])
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    try:
        cls.return_value.get_access_token.return_value = "fake-token"
        cls.return_value.get_account_id.return_value = None
        with mock.patch(
            "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
            return_value=mock.MagicMock(),
        ):
            adapter = _codex_pool_adapter("codex-pool", {})
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
            selection = chat.codex_pool_selection
            assert selection["source_ref"] != abs_path
            assert "secret" not in selection["source_ref"]
            assert "<absolute-path-redacted>" == selection["source_ref"]
    finally:
        mgr.stop()


def test_codex_pool_fallback_with_empty_pool(tui_dir):
    """Empty pool → factory uses legacy default token path."""
    _write_pool(tui_dir, [])
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    try:
        cls.return_value.get_access_token.return_value = "fake-token"
        cls.return_value.get_account_id.return_value = None
        adapter = _codex_pool_adapter("codex-pool", {})
        # No token_path kwarg → legacy default
        cls.assert_called_with()
        assert adapter.codex_pool_selection == {"fallback": "legacy_default"}
    finally:
        mgr.stop()


def test_codex_pool_selection_is_per_call_not_session_static(tui_dir):
    """Multiple calls to select() can return different candidates — no stickiness."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    names = set()
    for _ in range(50):
        names.add(Path(src.select().auth_ref).name)
    assert len(names) > 1, "Expected multiple accounts, got only one (sticky?)"


def test_codex_provider_ignores_pool_file(tui_dir):
    """Provider ``codex`` must NOT read the pool file."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    try:
        cls.return_value.get_access_token.return_value = "fake-token"
        cls.return_value.get_account_id.return_value = None
        _codex_pool_adapter("codex", {})
        # codex factory with no codex_auth_path → legacy default
        cls.assert_called_with()
    finally:
        mgr.stop()


def test_service_tier_fast_flows_to_adapter(tui_dir):
    """service_tier: fast config reaches the adapter as codex_service_tier=priority."""
    _write_pool(tui_dir, [{"path": "work.json", "weight": 1}])
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    try:
        cls.return_value.get_access_token.return_value = "fake-token"
        cls.return_value.get_account_id.return_value = None
        adapter = _codex_pool_adapter("codex-pool", {"service_tier": "fast"})
        assert adapter._codex_service_tier == "priority"
    finally:
        mgr.stop()


def test_service_tier_absent_omits():
    """No service_tier config → no codex_service_tier on adapter."""
    mgr = mock.patch("lingtai.auth.codex.CodexTokenManager")
    cls = mgr.start()
    try:
        cls.return_value.get_access_token.return_value = "fake-token"
        cls.return_value.get_account_id.return_value = None
        adapter = _codex_pool_adapter("codex", {})
        assert adapter._codex_service_tier is None
    finally:
        mgr.stop()


def test_service_tier_invalid_fails_loud():
    """Unsupported service_tier value raises ValueError at factory time."""
    with pytest.raises(ValueError, match="auto"):
        _normalize_service_tier("auto")


# ===========================================================================
# Source-agnostic Codex attempt lifecycle (Codex/AED-owned failover)
#
# The pool no longer retries in-process (spec v3 deletes the pool-owned
# switch loop). Each ``create_chat`` binds exactly ONE candidate and
# delegates. A real provider failure is classified once, its identity is
# recorded on the cached adapter, and the exception is re-raised unchanged —
# it enters the existing Codex/AED retry owner exactly once
# (base_agent/turn.py's AED loop rebuilds the session and calls
# ``create_chat`` again). These tests simulate that "AED calls create_chat
# again after a failure" shape directly, since AED itself lives outside this
# module's boundary.
# ===========================================================================


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


_PROVIDER_CALLS: list[str] = []
_CREATE_CHAT_CALLS: list[tuple[tuple, dict]] = []


def _start_codex_mgr_mock():
    """Mock CodexTokenManager to return per-path fake tokens."""
    import lingtai.auth.codex as codex_mod

    def _factory(*a, token_path=None, **kw):
        m = mock.MagicMock()
        m._path = token_path or "LEGACY_DEFAULT"
        m.get_access_token.return_value = f"tok:{token_path}"
        m.get_account_id.return_value = None
        return m

    p = mock.patch.object(codex_mod, "CodexTokenManager")
    p.start().side_effect = _factory
    return p


def _fake_chat_factory(script):
    """Return a create_chat replacement that yields controllable fake chats.

    ``script`` maps an account basename to either an exception to raise or a
    string value to return from send_stream. Records every ``(args, kwargs)``
    it was called with (so tests can prove the caller's real args/kwargs —
    tools, thinking, json_schema, interface — reach the fresh per-attempt
    adapter unmodified, never a hardcoded ``system_prompt=""``) and forwards
    ``on_chunk`` so streaming-partial-output tests can observe chunks.
    """

    def _build(self_adapter, *a, **kw):
        _CREATE_CHAT_CALLS.append((a, dict(kw)))
        token_path = getattr(
            getattr(self_adapter, "_codex_token_mgr", None), "_path", None
        )
        account = Path(str(token_path)).name if token_path else "unknown"

        class _FakeChat:
            pass

        chat = _FakeChat()
        chat.interface = kw.get("interface")
        chat.pre_request_hook = None

        def _send_stream(message, on_chunk=None):
            _PROVIDER_CALLS.append(account)
            action = script.get(account)
            if isinstance(action, BaseException):
                raise action
            from lingtai.kernel.llm.base import LLMResponse
            return LLMResponse(text=f"ok:{account}")

        chat.send_stream = _send_stream
        chat.send = lambda msg: _send_stream(msg, None)
        return chat

    return _build


def test_attempt_binds_exactly_one_candidate_no_inprocess_switch(tui_dir, tmp_path):
    """A usage_limit_reached 429 on the bound attempt propagates unchanged —
    the pool makes exactly ONE provider call per ``create_chat``/``send``; it
    never internally switches to another account within the same call."""
    _PROVIDER_CALLS.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.01
    ):
        mgr_patch = _start_codex_mgr_mock()
        try:
            script = {"a.json": _FakeUsageLimitError(), "b.json": "ok"}
            with mock.patch(
                "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
                _fake_chat_factory(script),
            ):
                from lingtai.llm.service import LLMService
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")
                chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                with pytest.raises(_FakeUsageLimitError):
                    chat.send("hello")
                # Exactly one provider call — the bound candidate — no
                # in-process switch to "b.json" happened.
                assert _PROVIDER_CALLS == ["a.json"]
        finally:
            mgr_patch.stop()


def test_failure_excludes_identity_for_next_create_chat_call(tui_dir, tmp_path):
    """A real failure records the candidate's identity on the CACHED adapter;
    the NEXT ``create_chat`` call on that same adapter — exactly what AED's
    ``_rebuild_session`` triggers after the exception bubbles through it —
    excludes it and binds a different account. No pool-owned loop drives
    this: two independent ``create_chat`` calls simulate AED's two attempts.
    """
    _PROVIDER_CALLS.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.01
    ):
        mgr_patch = _start_codex_mgr_mock()
        try:
            script = {"a.json": _FakeUsageLimitError(), "b.json": "ok"}
            with mock.patch(
                "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
                _fake_chat_factory(script),
            ):
                from lingtai.llm.service import LLMService
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")

                # AED attempt 1: create_chat binds 'a', send fails.
                chat1 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                with pytest.raises(_FakeUsageLimitError):
                    chat1.send("hello")
                assert _PROVIDER_CALLS == ["a.json"]

                # AED attempt 2: the SAME cached adapter's create_chat is
                # called again (this is what _rebuild_session triggers) —
                # it must now exclude 'a' and bind 'b'.
                chat2 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                resp = chat2.send("hello")
                assert resp.text == "ok:b.json"
                assert _PROVIDER_CALLS == ["a.json", "b.json"]
                assert chat2.codex_pool_selection["failover"] == "usage_limit_reached"

                # Success ends this AED attempt chain. The cached adapter must
                # not permanently exclude 'a' from later independent turns.
                chat3 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                assert chat3.codex_pool_selection["auth_path_sha8"] == \
                    chat1.codex_pool_selection["auth_path_sha8"]
                assert "failover" not in chat3.codex_pool_selection
        finally:
            mgr_patch.stop()


def test_retry_ordinary_429_does_not_exclude(tui_dir, tmp_path):
    """An ordinary 429 (no usage_limit_reached) must propagate AND must not
    exclude the account — a non-quota rate limit isn't proof of exhaustion."""
    _PROVIDER_CALLS.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.01
    ):
        mgr_patch = _start_codex_mgr_mock()
        try:
            script = {"a.json": _FakeOrdinary429(), "b.json": "ok"}
            with mock.patch(
                "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
                _fake_chat_factory(script),
            ):
                from lingtai.llm.service import LLMService
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")
                chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                with pytest.raises(_FakeOrdinary429):
                    chat.send("hello")
                assert _PROVIDER_CALLS == ["a.json"]
                # A second create_chat is NOT excluding 'a' — it may bind it
                # again (deterministic mock always favors 'a' at point 0.01).
                chat2 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                assert chat2.codex_pool_selection["auth_path_sha8"] == \
                    chat.codex_pool_selection["auth_path_sha8"]
        finally:
            mgr_patch.stop()


def test_retry_network_error_does_not_exclude(tui_dir, tmp_path):
    """A network error / timeout must propagate unchanged and must not
    exclude the account (only a structural usage_limit_reached does)."""
    _PROVIDER_CALLS.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.01
    ):
        mgr_patch = _start_codex_mgr_mock()
        try:
            script = {"a.json": TimeoutError("read timed out"), "b.json": "ok"}
            with mock.patch(
                "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
                _fake_chat_factory(script),
            ):
                from lingtai.llm.service import LLMService
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")
                chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                with pytest.raises(TimeoutError):
                    chat.send("hello")
                assert _PROVIDER_CALLS == ["a.json"]
        finally:
            mgr_patch.stop()


def test_retry_via_send_stream_also_excludes(tui_dir, tmp_path):
    """The exclude-on-failure path is installed identically on ``send`` and
    ``send_stream`` — a streaming caller gets the same AED-visible behavior."""
    _PROVIDER_CALLS.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.01
    ):
        mgr_patch = _start_codex_mgr_mock()
        try:
            script = {"a.json": _FakeUsageLimitError(), "b.json": "ok"}
            with mock.patch(
                "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
                _fake_chat_factory(script),
            ):
                from lingtai.llm.service import LLMService
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")
                chat1 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                with pytest.raises(_FakeUsageLimitError):
                    chat1.send_stream("hello", on_chunk=lambda d: None)

                chat2 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                resp = chat2.send_stream("hello", on_chunk=None)
                assert resp.text == "ok:b.json"
        finally:
            mgr_patch.stop()


def test_create_chat_forwards_callers_real_args_and_kwargs(tui_dir, tmp_path):
    """The fresh per-attempt adapter's ``create_chat`` receives the caller's
    ACTUAL args/kwargs — tools, thinking, interface — never a hardcoded
    ``system_prompt=""`` substituted for what the caller really passed."""
    _CREATE_CHAT_CALLS.clear()
    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])
    mgr_patch = _start_codex_mgr_mock()
    try:
        script = {"a.json": "ok"}
        with mock.patch(
            "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
            _fake_chat_factory(script),
        ):
            from lingtai.llm.service import LLMService
            from lingtai.kernel.llm.interface import ChatInterface
            svc = LLMService(
                provider="codex-pool", model="gpt-5.5",
                provider_defaults={"codex-pool": {}},
            )
            adapter = svc.get_adapter("codex-pool")
            real_interface = ChatInterface()
            fake_tools = [{"type": "function", "name": "do_thing"}]
            adapter.create_chat(
                model="gpt-5.5",
                system_prompt="the real system prompt",
                tools=fake_tools,
                thinking="xhigh",
                interface=real_interface,
            )
            assert len(_CREATE_CHAT_CALLS) == 1
            args, kwargs = _CREATE_CHAT_CALLS[0]
            assert kwargs.get("system_prompt") == "the real system prompt"
            assert kwargs.get("tools") is fake_tools
            assert kwargs.get("thinking") == "xhigh"
            assert kwargs.get("interface") is real_interface
    finally:
        mgr_patch.stop()


def test_bound_send_does_not_touch_interface_or_hook(tui_dir, tmp_path):
    """The pool's send/send_stream wrapper only records a failed identity on
    exception — it must not snapshot/restore the interface or install any
    ``pre_request_hook`` capture (that S0/H machinery belonged to the deleted
    in-process retry loop; AED's own ``close_pending_tool_calls`` now owns
    interface recovery across attempts, unchanged by this factory)."""
    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])
    mgr_patch = _start_codex_mgr_mock()
    try:
        from lingtai.kernel.llm.interface import ChatInterface
        real_interface = ChatInterface()
        hook_calls = []
        script = {"a.json": "ok"}
        with mock.patch(
            "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
            _fake_chat_factory(script),
        ):
            from lingtai.llm.service import LLMService
            svc = LLMService(
                provider="codex-pool", model="gpt-5.5",
                provider_defaults={"codex-pool": {}},
            )
            adapter = svc.get_adapter("codex-pool")
            chat = adapter.create_chat(
                model="gpt-5.5", system_prompt="s", interface=real_interface,
            )
            # The caller's hook (installed after create_chat, exactly as the
            # kernel does) must survive untouched — the pool wrapper does not
            # overwrite it with its own capturing hook.
            chat.pre_request_hook = lambda iface: hook_calls.append(iface)
            chat.send("hello")
            assert chat.pre_request_hook is not None
            # The wrapper never called the hook itself (only the real send
            # path would, and this fake chat's send_stream doesn't call it) —
            # proving the pool wrapper adds no hook invocation of its own.
            assert hook_calls == []
            # The interface object identity is exactly what was passed in —
            # no snapshot/replace occurred.
            assert chat.interface is real_interface
    finally:
        mgr_patch.stop()


def test_duplicate_alias_entries_excluded_together_across_attempts(tui_dir, tmp_path):
    """Two pool entries resolving to the SAME identity (duplicate/alias) are
    excluded together: once 'a' fails, a re-selection must not pick either
    occurrence of 'a' even though the pool lists it twice."""
    _PROVIDER_CALLS.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "a.json", "weight": 1},  # duplicate entry, same identity
        {"path": "b.json", "weight": 1},
    ])
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.01
    ):
        mgr_patch = _start_codex_mgr_mock()
        try:
            script = {"a.json": _FakeUsageLimitError(), "b.json": "ok"}
            with mock.patch(
                "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
                _fake_chat_factory(script),
            ):
                from lingtai.llm.service import LLMService
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")
                chat1 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                with pytest.raises(_FakeUsageLimitError):
                    chat1.send("hello")

                # Within the same AED chain, both aliases are excluded and
                # the one remaining identity is selected for the next attempt.
                chat2 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                resp = chat2.send("hello")
                assert resp.text == "ok:b.json"
        finally:
            mgr_patch.stop()


def test_no_candidate_after_pool_fully_excluded_never_falls_back(tui_dir, tmp_path):
    """A configured, non-empty pool exhausted inside one AED chain raises
    ``NoCandidateError``; silently escaping to legacy credentials is forbidden."""
    _PROVIDER_CALLS.clear()
    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.01
    ):
        mgr_patch = _start_codex_mgr_mock()
        try:
            script = {"a.json": _FakeUsageLimitError(), "LEGACY_DEFAULT": "ok"}
            with mock.patch(
                "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
                _fake_chat_factory(script),
            ):
                from lingtai.llm.service import LLMService
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")
                chat1 = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                with pytest.raises(_FakeUsageLimitError):
                    chat1.send("hello")

                # The configured pool exists but its only identity is
                # excluded, so selection is terminal for this AED chain and
                # legacy credentials are never bound or called.
                with pytest.raises(NoCandidateError, match="No eligible"):
                    adapter.create_chat(model="gpt-5.5", system_prompt="s")
                assert _PROVIDER_CALLS == ["a.json"]
        finally:
            mgr_patch.stop()


# ===========================================================================
# Pre-request quota (dynamic preflight) — monkeypatched, no real auth/process
# ===========================================================================


_QUOTA_VALUES: dict[str, float | None] = {}
_LINGERING_PATCHES: list[mock._patch] = []


def _fake_read_remaining_percent(auth_path):
    """Monkeypatch target: returns a controllable per-path percent value."""
    p = str(auth_path)
    for key, val in _QUOTA_VALUES.items():
        if key in p:
            return val
    return None


def _setup_codex_pool_mocks(tui_dir, model="gpt-5.5", defaults=None):
    """Set up all mocks needed for codex-pool integration tests.

    Returns ``(adapter, mgr_patch, chat_patch, quota_patch)``.
    Caller must ``.stop()`` the patches afterwards.
    """
    from lingtai.llm.service import LLMService
    import lingtai.auth.codex as codex_mod

    def _mgr_factory(*a, token_path=None, **kw):
        m = mock.MagicMock()
        m._path = token_path or "LEGACY_DEFAULT"
        m.get_access_token.return_value = f"tok:{token_path}"
        m.get_account_id.return_value = None
        return m

    mgr_patch = mock.patch.object(codex_mod, "CodexTokenManager")
    mgr_cls = mgr_patch.start()
    mgr_cls.side_effect = _mgr_factory

    def _fake_create_chat(self_adapter, *a, **kw):
        class _FakeChat:
            pass
        chat = _FakeChat()
        chat.interface = kw.get("interface")
        chat._codex_source_retry_installed = False

        def _send_stream(message, on_chunk=None):
            from lingtai.kernel.llm.base import LLMResponse
            return LLMResponse(text="ok")
        chat.send_stream = _send_stream
        chat.send = lambda msg: _send_stream(msg, None)
        return chat

    chat_patch = mock.patch(
        "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
        _fake_create_chat,
    )
    chat_patch.start()

    quota_patch = mock.patch(
        "lingtai.llm.openai.codex_quota.read_remaining_percent",
        _fake_read_remaining_percent,
    )
    quota_patch.start()

    _LINGERING_PATCHES.extend([mgr_patch, chat_patch, quota_patch])

    svc = LLMService(
        provider="codex-pool", model=model,
        provider_defaults={"codex-pool": defaults or {}},
    )
    adapter = svc.get_adapter("codex-pool")
    return adapter, mgr_patch, chat_patch, quota_patch


def test_preflight_complete_values_drive_dynamic_ratios(tui_dir):
    """Complete 0..100 values → linear dynamic weights affect selection."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    import hashlib
    a_path = str(tui_dir / "a.json")
    b_path = str(tui_dir / "b.json")
    _QUOTA_VALUES[a_path] = 90.0   # a: 90% remaining → fraction 0.9
    _QUOTA_VALUES[b_path] = 10.0   # b: 10% remaining → fraction 0.1

    # Force draw point in the middle: with equal base weights,
    # raw weights are 0.9 and 0.1.  Draw point 0.05 < 0.9 → a wins.
    with mock.patch(
        "lingtai.auth.codex_account_source._uniform_float", return_value=0.05
    ):
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        try:
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
            sel = chat.codex_pool_selection
            assert isinstance(sel, dict)
            assert sel["auth_path_sha8"] == hashlib.sha256(a_path.encode()).hexdigest()[:8]
        finally:
            mgr_p.stop(); chat_p.stop(); quota_p.stop()


def test_preflight_zero_quota_excluded_before_request(tui_dir):
    """Zero quota → identity excluded; selection picks the remaining account."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    import hashlib
    a_path = str(tui_dir / "a.json")
    b_path = str(tui_dir / "b.json")
    _QUOTA_VALUES[a_path] = 0.0    # exhausted
    _QUOTA_VALUES[b_path] = 50.0   # available

    for _ in range(20):
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        try:
            chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
            sel = chat.codex_pool_selection
            assert isinstance(sel, dict)
            assert sel["auth_path_sha8"] == hashlib.sha256(b_path.encode()).hexdigest()[:8]
        finally:
            mgr_p.stop(); chat_p.stop(); quota_p.stop()


def test_preflight_unavailable_result_makes_draw_static(tui_dir):
    """One None/None-like quota result → snapshot=None → static fallback."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    light_path = str(tui_dir / "light.json")
    _QUOTA_VALUES[light_path] = 80.0   # available
    # heavy.json NOT in _QUOTA_VALUES → returns None → static fallback

    heavy_count = 0
    for _ in range(50):
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
        sel = chat.codex_pool_selection
        # Check if heavy was selected by looking at source_ref.
        if isinstance(sel, dict) and "heavy.json" in str(sel):
            heavy_count += 1

    # With static fallback (9:1), heavy should dominate.
    assert heavy_count > 30


def test_preflight_reader_failure_fail_open_static(tui_dir):
    """When read_remaining_percent returns None (fail-soft), draw stays static."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    # No quota values set → all return None → static fallback.

    heavy_count = 0
    for _ in range(50):
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
        heavy_count += 1 if "heavy.json" in str(chat.codex_pool_selection) else 0

    assert heavy_count > 30  # static 9:1


def test_preflight_reader_exception_fail_open_static(tui_dir):
    """When read_remaining_percent raises unexpectedly, draw stays static."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])

    def _raising_reader(_auth_path):
        raise RuntimeError("simulated crash")

    import lingtai.auth.codex as codex_mod

    def _mgr_factory(*a, token_path=None, **kw):
        m = mock.MagicMock()
        m._path = token_path or "LEGACY_DEFAULT"
        m.get_access_token.return_value = f"tok:{token_path}"
        m.get_account_id.return_value = None
        return m

    def _fake_create_chat(self_adapter, *a, **kw):
        class _FakeChat:
            pass
        chat = _FakeChat()
        chat.interface = kw.get("interface")
        chat._codex_source_retry_installed = False

        def _send_stream(message, on_chunk=None):
            from lingtai.kernel.llm.base import LLMResponse
            return LLMResponse(text="ok")
        chat.send_stream = _send_stream
        chat.send = lambda msg: _send_stream(msg, None)
        return chat

    from lingtai.llm.service import LLMService
    with mock.patch.object(codex_mod, "CodexTokenManager") as mgr_cls:
        mgr_cls.side_effect = _mgr_factory
        with mock.patch(
            "lingtai.llm.openai.adapter.CodexOpenAIAdapter.create_chat",
            _fake_create_chat,
        ):
            with mock.patch(
                "lingtai.llm.openai.codex_quota.read_remaining_percent",
                _raising_reader,
            ):
                svc = LLMService(
                    provider="codex-pool", model="gpt-5.5",
                    provider_defaults={"codex-pool": {}},
                )
                adapter = svc.get_adapter("codex-pool")
                # Should not crash — fail-open to static.
                chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
                assert chat.codex_pool_selection is not None


def test_preflight_all_zero_raises_no_candidate(tui_dir):
    """Every candidate proven zero → NoCandidateError before any provider request."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    _QUOTA_VALUES[str(tui_dir / "a.json")] = 0.0
    _QUOTA_VALUES[str(tui_dir / "b.json")] = 0.0

    with pytest.raises(NoCandidateError, match="zero"):
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        adapter.create_chat(model="gpt-5.5", system_prompt="s")


def test_preflight_send_and_send_stream_same_candidate(tui_dir):
    """Both send and send_stream paths preserve the same selected candidate."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
    ])
    a_path = str(tui_dir / "a.json")
    _QUOTA_VALUES[a_path] = 50.0

    import hashlib
    a_sha8 = hashlib.sha256(a_path.encode()).hexdigest()[:8]

    adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
    chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")

    sel = chat.codex_pool_selection
    assert sel["auth_path_sha8"] == a_sha8

    # send and send_stream on the same chat should both work and come from
    # the same adapter account (no re-selection mid-chat).
    resp1 = chat.send("hello")
    resp2 = chat.send_stream("hello", on_chunk=None)
    assert resp1 is not None
    assert resp2 is not None


def test_preflight_no_real_process_auth_used(tui_dir):
    """Prove tests never spawn a real app-server or use real credentials."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
    ])
    _QUOTA_VALUES[str(tui_dir / "a.json")] = 50.0

    # Track whether subprocess.Popen is ever called.
    with mock.patch("subprocess.Popen") as popen_mock:
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
        assert chat.codex_pool_selection is not None
        # The real codex_quota module would call subprocess.Popen only if
        # read_remaining_percent weren't monkeypatched. Our fake is used.
        popen_mock.assert_not_called()


def test_preflight_quota_targets_exclude(tui_dir):
    """quota_targets respects exclude."""
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    src = WeightedAccountSource(tui_dir / "codex-auth-pool.json", tui_dir)
    import hashlib
    a_sha8 = hashlib.sha256(str(tui_dir / "a.json").encode()).hexdigest()[:8]

    all_targets = src.quota_targets()
    assert len(all_targets) == 2

    filtered = src.quota_targets(exclude={a_sha8})
    assert len(filtered) == 1
    assert filtered[0][1] != a_sha8


def test_preflight_fixed_source_quota_targets():
    """FixedAccountSource.quota_targets returns single entry or empty."""
    src = FixedAccountSource("/tmp/t.json")
    targets = src.quota_targets()
    assert len(targets) == 1
    assert targets[0][0] == "/tmp/t.json"

    excluded = src.quota_targets(exclude={targets[0][1]})
    assert len(excluded) == 0


def test_preflight_invalid_pct_non_comparable(tui_dir):
    """A non-finite percent value → snapshot=None → static fallback."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    light_path = str(tui_dir / "light.json")
    heavy_path = str(tui_dir / "heavy.json")
    _QUOTA_VALUES[light_path] = 50.0
    _QUOTA_VALUES[heavy_path] = float("nan")  # non-comparable

    heavy_count = 0
    for _ in range(50):
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
        heavy_count += 1 if "heavy.json" in str(chat.codex_pool_selection) else 0

    assert heavy_count > 30  # static 9:1 fallback


def test_preflight_out_of_range_pct_static(tui_dir):
    """A percent outside [0, 100] → snapshot=None → static fallback."""
    _QUOTA_VALUES.clear()
    _write_pool(tui_dir, [
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])
    light_path = str(tui_dir / "light.json")
    heavy_path = str(tui_dir / "heavy.json")
    _QUOTA_VALUES[light_path] = 50.0
    _QUOTA_VALUES[heavy_path] = 150.0  # > 100

    heavy_count = 0
    for _ in range(50):
        adapter, mgr_p, chat_p, quota_p = _setup_codex_pool_mocks(tui_dir)
        chat = adapter.create_chat(model="gpt-5.5", system_prompt="s")
        heavy_count += 1 if "heavy.json" in str(chat.codex_pool_selection) else 0

    assert heavy_count > 30  # static 9:1 fallback


@pytest.fixture(autouse=True)
def _cleanup_preflight_patches():
    """Ensure quota patches from preflight tests don't leak into other tests."""
    yield
    while _LINGERING_PATCHES:
        try:
            _LINGERING_PATCHES.pop().stop()
        except RuntimeError:
            pass  # already stopped
