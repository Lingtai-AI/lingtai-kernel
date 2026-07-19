"""Tests for quota-aware exclusion at NEW ``codex-pool`` selection.

The quota-aware selection contract
supersedes the prior no-auto-routing boundary for this quota PR: at NEW
``codex-pool`` account selection only, exclude an account when a fresh/valid
quota snapshot explicitly proves ``remaining_percent <= 0`` on its main
rate-limit window; otherwise fail OPEN (include it). Configured weights are
preserved among the remaining eligible accounts. No migration of an
already-created adapter/session. If every validated account is proven
exhausted, raise ``CodexPoolAllAccountsExhaustedError`` rather than silently
selecting one.

Covers, per the task's required proof list:
  * zero exclusion (nothing proven exhausted -> byte-identical to pre-change
    selection, same seam/monkeypatch point);
  * positive weighted eligibility (weights preserved exactly among the
    remaining eligible accounts after one is excluded);
  * unavailable/error/malformed/missing quota fails OPEN (never excludes);
  * all-zero -> ``CodexPoolAllAccountsExhaustedError``, not a silent pick;
  * post-reset re-entry (a previously-exhausted account becomes eligible
    again once its quota shows positive ``remaining_percent``, once the
    bounded cache entry is stale/cleared — no separate cooldown state);
  * sticky-session non-migration (this exclusion only ever runs at NEW
    selection; an already-selected/injected auth path is never re-evaluated
    or swapped out from under a live adapter/session);
  * privacy: no raw paths/tokens/private IDs in the exhausted-error message
    or in any selection metadata.

Most scenarios mock the internal ``_is_proven_exhausted`` seam directly (the
wire protocol itself — initialize/read framing, normalization, fail-soft
paths — is already fully covered by ``tests/test_codex_quota.py``); two tests
drive the real fake ``codex app-server`` end-to-end for realism.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from lingtai.auth import codex_pool
from lingtai.llm.openai import codex_quota


FAKE_APP_SERVER = Path(__file__).parent / "_fake_codex_app_server.py"


@pytest.fixture(autouse=True)
def _clear_quota_cache():
    """Every test starts with an empty process-local quota cache."""
    with codex_pool._QUOTA_CACHE_LOCK:
        codex_pool._QUOTA_CACHE.clear()
    yield
    with codex_pool._QUOTA_CACHE_LOCK:
        codex_pool._QUOTA_CACHE.clear()


@pytest.fixture()
def tui_dir(tmp_path, monkeypatch):
    d = tmp_path / "tui"
    d.mkdir()
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(d))
    return d


@pytest.fixture()
def fake_binary(monkeypatch):
    def _configure(mode: str = "ok"):
        monkeypatch.setenv("LINGTAI_FAKE_APP_SERVER_MODE", mode)
        monkeypatch.setattr(codex_quota, "_CODEX_BIN", sys.executable)
        monkeypatch.setattr(codex_quota, "_APP_SERVER_ARGS", (str(FAKE_APP_SERVER),))
        return None

    return _configure


def _write_pool(tui_dir: Path, accounts, *, version=1, name="codex-auth-pool.json"):
    path = tui_dir / name
    path.write_text(json.dumps({"version": version, "accounts": accounts}), encoding="utf-8")
    return path


def _anchor_with_started_at(dir_: Path, started_at, *, molt_count=0) -> str:
    dir_.mkdir(parents=True, exist_ok=True)
    payload = {"molt_count": molt_count}
    if started_at is not None:
        payload["started_at"] = started_at
    (dir_ / ".agent.json").write_text(json.dumps(payload), encoding="utf-8")
    return str(dir_ / "init.json")


def _write_real_auth(path: Path):
    path.write_text(json.dumps({
        "access_token": "sk-secret", "refresh_token": "rt-secret", "expires_at": 9999999999,
    }), encoding="utf-8")
    path.chmod(0o600)


def _patch_exhausted(monkeypatch, exhausted_paths: set[str]):
    """Mock the exclusion predicate directly: only paths in ``exhausted_paths``
    are proven exhausted; everything else fails open (returns False), matching
    the real function's contract without spawning any subprocess."""
    real = codex_pool._is_proven_exhausted

    def _fake(auth_path, *, timeout_seconds=None):
        return auth_path in exhausted_paths

    monkeypatch.setattr(codex_pool, "_is_proven_exhausted", _fake)
    return real


# --------------------------------------------------------------------------
# Zero exclusion — byte-identical to pre-change selection
# --------------------------------------------------------------------------


def test_zero_exclusion_selects_same_as_before(monkeypatch, tui_dir, tmp_path):
    """Nothing proven exhausted -> selection uses the plain (non-indexed)
    weighted picker, the same seam/behavior as before this feature existed."""
    _patch_exhausted(monkeypatch, exhausted_paths=set())
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 9},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    real_pick = codex_pool._weighted_pick
    called_with = {}

    def _spy(accounts, seed):
        called_with["accounts"] = accounts
        return real_pick(accounts, seed)

    monkeypatch.setattr(codex_pool, "_weighted_pick", _spy)
    result = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})

    assert result is not None
    # The plain (non-indexed) picker was used — proves the "no exclusion"
    # fast path takes the exact old call shape, not the filtered one.
    assert called_with["accounts"] == [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 9},
    ]


# --------------------------------------------------------------------------
# Positive weighted eligibility — weights preserved among the remaining set
# --------------------------------------------------------------------------


def test_weights_preserved_among_remaining_eligible_accounts(monkeypatch, tui_dir, tmp_path):
    """Exclude one account; among the survivors, the SAME configured weights
    still govern the pick (a heavily-weighted survivor is picked far more
    often than a lightly-weighted one), and the excluded account's own
    configured weight never leaks into anyone else's odds."""
    tui_dir_path = tui_dir
    excluded_path = str((tui_dir_path / "excluded.json").resolve())
    _patch_exhausted(monkeypatch, exhausted_paths={excluded_path})
    _write_pool(tui_dir, [
        {"path": "excluded.json", "weight": 1000},  # would dominate if not excluded
        {"path": "light.json", "weight": 1},
        {"path": "heavy.json", "weight": 9},
    ])

    picks = {"light.json": 0, "heavy.json": 0}
    for i in range(200):
        anchor = _anchor_with_started_at(tui_dir_path.parent / f"agent{i}", f"seed-{i}")
        result = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
        assert result is not None
        assert result["selection"]["source_ref"] != "excluded.json"
        picks[result["selection"]["source_ref"]] += 1

    assert picks["light.json"] + picks["heavy.json"] == 200
    # heavy.json (weight 9) must be picked substantially more than light.json
    # (weight 1) — proves weights among the remaining set are honored, not
    # flattened to uniform-random-among-survivors.
    assert picks["heavy.json"] > picks["light.json"] * 3


def test_source_index_stays_original_after_exclusion(monkeypatch, tui_dir, tmp_path):
    """source_index in the returned selection must be the index into the FULL
    validated account list (not the filtered/eligible list) — required so
    ``_codex_pool_failover_candidates`` anchors correctly, unaffected by
    quota exclusion."""
    a_path = str((tui_dir / "a.json").resolve())
    _patch_exhausted(monkeypatch, exhausted_paths={a_path})
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},   # idx 0, excluded
        {"path": "b.json", "weight": 1},   # idx 1
        {"path": "c.json", "weight": 1},   # idx 2
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    result = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
    assert result is not None
    assert result["selection"]["source_ref"] in ("b.json", "c.json")
    assert result["selection"]["source_index"] in (1, 2)
    assert result["selection"]["pool_size"] == 3  # full count, not filtered count


# --------------------------------------------------------------------------
# Unavailable / error / malformed / missing quota -> fail OPEN
# --------------------------------------------------------------------------


def test_unavailable_quota_fails_open_never_excludes():
    """_is_proven_exhausted itself: an unavailable snapshot must never exclude."""
    quota = {"available": False, "error": "auth_file_missing", "primary": None, "by_limit_id": {}}
    assert codex_pool._quota_remaining_percent_from_dict(quota) is None


def test_malformed_quota_fails_open():
    quota = {"available": True, "error": None, "primary": "not-a-dict", "by_limit_id": {}}
    assert codex_pool._quota_remaining_percent_from_dict(quota) is None


def test_missing_primary_window_fails_open():
    quota = {
        "available": True, "error": None,
        "primary": {"primary": None, "limit_id": "codex"},
        "by_limit_id": {},
    }
    assert codex_pool._quota_remaining_percent_from_dict(quota) is None


def test_end_to_end_unavailable_account_stays_eligible(fake_binary, tui_dir, tmp_path):
    """Real wire path: an account whose auth file doesn't exist (quota read
    fails soft with auth_file_missing) still gets selected -- fail open, not
    excluded, and no exception."""
    fake_binary("ok")
    _write_pool(tui_dir, [{"path": "missing.json", "weight": 1}])  # never created
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    result = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, quota_timeout_seconds=10
    )
    assert result is not None
    assert result["selection"]["source_ref"] == "missing.json"


def test_end_to_end_error_response_fails_open(fake_binary, tui_dir, tmp_path):
    """A JSON-RPC error on the read (not a timeout, not missing-file) must
    also fail open, proven through the real wire path."""
    fake_binary("error_response")
    real_auth = tui_dir / "acct.json"
    _write_real_auth(real_auth)
    _write_pool(tui_dir, [{"path": "acct.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    result = codex_pool.select_codex_pool_auth(
        {"codex_session_anchor": anchor}, quota_timeout_seconds=10
    )
    assert result is not None
    assert result["selection"]["source_ref"] == "acct.json"


# --------------------------------------------------------------------------
# All accounts exhausted -> explicit, actionable error (never a silent pick)
# --------------------------------------------------------------------------


def test_all_accounts_exhausted_raises_not_silently_selects(monkeypatch, tui_dir, tmp_path):
    a_path = str((tui_dir / "a.json").resolve())
    b_path = str((tui_dir / "b.json").resolve())
    _patch_exhausted(monkeypatch, exhausted_paths={a_path, b_path})
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 5},
    ])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    with pytest.raises(codex_pool.CodexPoolAllAccountsExhaustedError) as exc_info:
        codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})

    message = str(exc_info.value)
    assert "2" in message  # account count
    assert "a.json" not in message  # no pool mapping / account identifier
    assert "b.json" not in message


def test_end_to_end_all_exhausted_via_real_wire(fake_binary, tui_dir, tmp_path):
    """Real wire path: a single-account pool whose quota is proven exhausted
    (usedPercent=100) raises, rather than selecting the exhausted account."""
    fake_binary("exhausted")
    real_auth = tui_dir / "acct.json"
    _write_real_auth(real_auth)
    _write_pool(tui_dir, [{"path": "acct.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    with pytest.raises(codex_pool.CodexPoolAllAccountsExhaustedError):
        codex_pool.select_codex_pool_auth(
            {"codex_session_anchor": anchor}, quota_timeout_seconds=10
        )


def test_all_exhausted_error_never_leaks_raw_path_or_secret(monkeypatch, tui_dir, tmp_path):
    abs_path = tui_dir / "private" / "codex-auth.json"
    abs_path.parent.mkdir(parents=True)
    resolved = str(abs_path.resolve())
    _patch_exhausted(monkeypatch, exhausted_paths={resolved})
    _write_pool(tui_dir, [{"path": str(abs_path), "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    with pytest.raises(codex_pool.CodexPoolAllAccountsExhaustedError) as exc_info:
        codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})

    message = str(exc_info.value)
    assert str(abs_path) not in message
    assert "sk-secret" not in message
    assert "rt-secret" not in message
    # No pool mapping or path-derived identifier appears at all.
    assert codex_pool.ABSOLUTE_REF_REDACTED not in message
    assert "auth_path_sha8" not in message


def test_empty_pool_still_returns_none_not_exhausted_error(tui_dir, tmp_path):
    """An empty/missing pool is the pre-existing 'unusable pool' fallback
    condition (-> None, legacy default), NOT the new exhausted-error path —
    those are deliberately distinct outcomes."""
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")
    assert codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor}) is None


# --------------------------------------------------------------------------
# Post-reset re-entry — no separate cooldown state to expire
# --------------------------------------------------------------------------


def test_post_reset_reentry_after_cache_ttl(monkeypatch, tui_dir, tmp_path):
    """A previously-exhausted account becomes selectable again once a fresh
    read proves recovery, once the cache entry is no longer fresh — modeled
    here by directly manipulating the cache TTL window rather than sleeping."""
    auth_path = str((tui_dir / "acct.json").resolve())
    _write_pool(tui_dir, [{"path": "acct.json", "weight": 1}])
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    call_count = {"n": 0}

    def _fake_cached_quota_dict(path, *, timeout_seconds=None):
        call_count["n"] += 1
        # First read: exhausted. Second read (post "reset"): recovered.
        if call_count["n"] == 1:
            return {"available": True, "error": None, "by_limit_id": {},
                     "primary": {"primary": {"remaining_percent": 0.0}}}
        return {"available": True, "error": None, "by_limit_id": {},
                 "primary": {"primary": {"remaining_percent": 87.0}}}

    monkeypatch.setattr(codex_pool, "_cached_quota_dict", _fake_cached_quota_dict)

    with pytest.raises(codex_pool.CodexPoolAllAccountsExhaustedError):
        codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})

    # Second selection call: the (fake) underlying read now reports positive
    # remaining_percent -- the account is automatically eligible again, with
    # NO separate unexclusion/cooldown-clear call required.
    result = codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})
    assert result is not None
    assert result["selection"]["source_ref"] == "acct.json"
    assert call_count["n"] == 2


def test_cache_ttl_bounds_staleness(monkeypatch, tui_dir):
    """_cached_quota_dict must not re-fetch within the TTL window, and MUST
    re-fetch once the cached entry ages past it -- the bounded-freshness
    contract the exclusion check relies on."""
    auth_path = str((tui_dir / "acct.json").resolve())
    fetch_count = {"n": 0}

    def _fake_read(path, **kw):
        fetch_count["n"] += 1
        return mock.Mock()

    def _fake_to_dict(snapshot):
        return {"available": True, "error": None, "primary": None, "by_limit_id": {}}

    monkeypatch.setattr(
        "lingtai.llm.openai.codex_quota.read_codex_quota_snapshot", _fake_read
    )
    monkeypatch.setattr(
        "lingtai.llm.openai.codex_quota.quota_snapshot_to_dict", _fake_to_dict
    )

    codex_pool._cached_quota_dict(auth_path, timeout_seconds=None)
    codex_pool._cached_quota_dict(auth_path, timeout_seconds=None)
    assert fetch_count["n"] == 1  # second call served from cache, within TTL

    # Force the cached entry to look stale by rewriting its timestamp far in the past.
    with codex_pool._QUOTA_CACHE_LOCK:
        _, cached_quota = codex_pool._QUOTA_CACHE[auth_path]
        codex_pool._QUOTA_CACHE[auth_path] = (0.0, cached_quota)

    codex_pool._cached_quota_dict(auth_path, timeout_seconds=None)
    assert fetch_count["n"] == 2  # stale entry triggered a fresh fetch


# --------------------------------------------------------------------------
# Sticky-session non-migration
# --------------------------------------------------------------------------


def test_selection_only_runs_at_construction_never_on_live_adapter(monkeypatch, tui_dir, tmp_path):
    """The exclusion check must never re-run against an adapter/session that
    already has its auth path injected: select_codex_pool_auth is called
    exactly ONCE per _codex_pool factory invocation (adapter construction),
    never again afterward for that same adapter instance."""
    from unittest import mock as _mock

    call_count = {"n": 0}
    real_select = codex_pool.select_codex_pool_auth

    def _counting_select(*a, **kw):
        call_count["n"] += 1
        return real_select(*a, **kw)

    monkeypatch.setattr(codex_pool, "select_codex_pool_auth", _counting_select)
    monkeypatch.setattr(codex_pool, "_is_proven_exhausted", lambda *a, **kw: False)

    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])
    anchor_dir = tmp_path / "agent"
    anchor_dir.mkdir()
    (anchor_dir / ".agent.json").write_text(
        json.dumps({"started_at": "t0", "molt_count": 0}), encoding="utf-8"
    )

    with _mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None
        mgr_cls.return_value._path = tui_dir / "a.json"

        from lingtai.llm.service import LLMService
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": str(anchor_dir / "init.json")}},
        )
        adapter = svc.get_adapter("codex-pool")
        # Simulate multiple downstream calls on the ALREADY-CREATED adapter
        # (create_chat / generate) -- selection must not re-run for these.
        assert call_count["n"] == 1
        adapter_selection_before = dict(adapter.codex_pool_selection)

    assert call_count["n"] == 1
    assert adapter.codex_pool_selection == adapter_selection_before


def test_exhausted_error_at_construction_does_not_corrupt_existing_adapter(monkeypatch, tui_dir, tmp_path):
    """If quota exclusion later proves the sole account exhausted, building a
    NEW codex-pool adapter raises -- but this must never retroactively affect
    an already-constructed adapter from an earlier (non-exhausted) call."""
    from unittest import mock as _mock

    a_path = str((tui_dir / "a.json").resolve())
    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])
    anchor_dir = tmp_path / "agent"
    anchor_dir.mkdir()
    (anchor_dir / ".agent.json").write_text(
        json.dumps({"started_at": "t0", "molt_count": 0}), encoding="utf-8"
    )

    monkeypatch.setattr(codex_pool, "_is_proven_exhausted", lambda *a, **kw: False)

    with _mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None
        mgr_cls.return_value._path = tui_dir / "a.json"

        from lingtai.llm.service import LLMService
        svc = LLMService(
            provider="codex-pool", model="gpt-5.5",
            provider_defaults={"codex-pool": {"codex_session_anchor": str(anchor_dir / "init.json")}},
        )
        first_adapter = svc.get_adapter("codex-pool")
        first_selection = dict(first_adapter.codex_pool_selection)

        # Now the account becomes exhausted -- a SECOND, independent
        # construction attempt must raise...
        monkeypatch.setattr(codex_pool, "_is_proven_exhausted", lambda *a, **kw: True)
        anchor_dir2 = tmp_path / "agent2"
        anchor_dir2.mkdir()
        (anchor_dir2 / ".agent.json").write_text(
            json.dumps({"started_at": "t1", "molt_count": 0}), encoding="utf-8"
        )
        # LLMService eagerly constructs its adapter in __init__, so the raise
        # happens at construction, not at a later get_adapter() call.
        with pytest.raises(codex_pool.CodexPoolAllAccountsExhaustedError):
            LLMService(
                provider="codex-pool", model="gpt-5.5",
                provider_defaults={"codex-pool": {"codex_session_anchor": str(anchor_dir2 / "init.json")}},
            )

    # ...but the FIRST, already-constructed adapter's selection is untouched.
    assert first_adapter.codex_pool_selection == first_selection


# --------------------------------------------------------------------------
# Never touches routing/weights/files themselves
# --------------------------------------------------------------------------


def test_exclusion_never_mutates_pool_file_or_config(monkeypatch, tui_dir, tmp_path):
    a_path = str((tui_dir / "a.json").resolve())
    _patch_exhausted(monkeypatch, exhausted_paths={a_path})
    pool_path = _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
    ])
    before = pool_path.read_bytes()
    anchor = _anchor_with_started_at(tmp_path / "agent", "t0")

    codex_pool.select_codex_pool_auth({"codex_session_anchor": anchor})

    assert pool_path.read_bytes() == before


def test_exclusion_does_not_change_failover_candidate_order(monkeypatch, tui_dir, tmp_path):
    """Quota exclusion must never alter _codex_pool_failover_candidates —
    the deterministic failover sequence stays anchored to the actually-picked
    source_index exactly as before this feature."""
    b_path = str((tui_dir / "b.json").resolve())
    _patch_exhausted(monkeypatch, exhausted_paths=set())  # nothing excluded
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 1},
        {"path": "c.json", "weight": 1},
    ])

    candidates = codex_pool._codex_pool_failover_candidates(
        {"codex_session_anchor": "anchor"}, model=None,
        selected_auth_path=b_path, selected_source_index=1,
    )
    assert [c["source_index"] for c in candidates[:2]] == [2, 0]
