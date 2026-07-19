"""Tests for the Codex OAuth quota/rate-limit read surface (issue #7938).

Covers:
  * successful initialize+read framing against a fake ``codex app-server``
    (newline-delimited JSON-RPC over stdio, matching the real CLI 0.144.3
    handshake this module was verified against);
  * normalized fields / ``remaining_percent = max(0, 100 - used_percent)``
    arithmetic;
  * sparse/optional data (nulls throughout) does not raise and normalizes to
    ``None``/empty as appropriate;
  * malformed result / JSON-RPC error response / timeout / missing binary all
    fail soft (``available=False``, safe ``error`` category, never raises);
  * no secret/path/raw-ID leakage: token contents, raw auth paths, and stderr
    never appear in the returned snapshot;
  * ``codex-pool`` per-account snapshot listing preserves pool order,
    duplicates, model-category scoping, and weight metadata;
  * routing/weights/selection/files are byte-for-byte unchanged by the new
    quota surface (existing ``codex-pool`` selection tests keep passing
    unmodified; this file adds an explicit proof).

No real network calls. No real Codex binary is required — a fake
``codex app-server``-shaped script drives the wire protocol; ``codex_quota``'s
``_CODEX_BIN`` seam is monkeypatched to invoke it via ``sys.executable``.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from lingtai.auth import codex_pool
from lingtai.llm.openai import codex_quota
from lingtai.llm.openai.adapter import CodexOpenAIAdapter
from lingtai.llm.service import LLMService


FAKE_APP_SERVER = Path(__file__).parent / "_fake_codex_app_server.py"


@pytest.fixture()
def fake_binary(monkeypatch):
    """Point ``codex_quota``'s subprocess launch at the fake app-server script."""

    def _configure(mode: str = "ok"):
        monkeypatch.setenv("LINGTAI_FAKE_APP_SERVER_MODE", mode)
        monkeypatch.setattr(codex_quota, "_CODEX_BIN", sys.executable)
        monkeypatch.setattr(codex_quota, "_APP_SERVER_ARGS", (str(FAKE_APP_SERVER),))
        # shutil.which(sys.executable) always resolves (it's the running interpreter).
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


# --------------------------------------------------------------------------
# Successful read + normalization
# --------------------------------------------------------------------------


def test_successful_initialize_and_read(fake_binary, auth_file):
    fake_binary("ok")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)

    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.primary is not None
    assert snapshot.primary.limit_id == "codex"
    assert snapshot.primary.plan_type == "pro"
    assert snapshot.primary.has_credits is False
    assert snapshot.primary.credits_balance == "0"

    assert "codex" in snapshot.by_limit_id
    assert "codex_bengalfox" in snapshot.by_limit_id
    assert snapshot.by_limit_id["codex_bengalfox"].limit_name == "GPT-5.3-Codex-Spark"


def test_remaining_percent_arithmetic(fake_binary, auth_file):
    fake_binary("ok")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)

    window = snapshot.primary.primary
    assert window.used_percent == 10.0
    assert window.remaining_percent == 90.0
    assert window.window_duration_mins == 10080
    assert window.resets_at == 1784955319

    # Second bucket: 0 used -> 100 remaining (upper bound sanity).
    other = snapshot.by_limit_id["codex_bengalfox"].primary
    assert other.used_percent == 0.0
    assert other.remaining_percent == 100.0


def test_remaining_percent_never_negative():
    # Direct unit check on the normalizer: an out-of-range usedPercent (>100)
    # must still clamp remaining_percent to 0, never negative.
    window = codex_quota._normalize_window({"usedPercent": 137, "windowDurationMins": 10, "resetsAt": 1})
    assert window.used_percent == 137.0
    assert window.remaining_percent == 0.0


def test_quota_snapshot_to_dict_shape(fake_binary, auth_file):
    fake_binary("ok")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)
    d = codex_quota.quota_snapshot_to_dict(snapshot)

    assert set(d.keys()) == {"available", "error", "primary", "by_limit_id"}
    assert d["available"] is True
    assert d["error"] is None
    assert set(d["primary"].keys()) == {
        "limit_id", "limit_name", "plan_type", "rate_limit_reached_type",
        "has_credits", "credits_unlimited", "credits_balance",
        "primary", "secondary",
    }
    assert set(d["primary"]["primary"].keys()) == {
        "used_percent", "remaining_percent", "window_duration_mins", "resets_at",
    }
    assert isinstance(d["by_limit_id"], dict)


# --------------------------------------------------------------------------
# Sparse / optional data
# --------------------------------------------------------------------------


def test_sparse_response_normalizes_without_raising(fake_binary, auth_file):
    fake_binary("sparse")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)

    assert snapshot.available is True
    assert snapshot.error is None
    # limitId/limitName/primary/secondary/credits/planType/reachedType all None.
    assert snapshot.primary.limit_id is None
    assert snapshot.primary.limit_name is None
    assert snapshot.primary.primary is None
    assert snapshot.primary.secondary is None
    assert snapshot.primary.has_credits is None
    assert snapshot.primary.plan_type is None
    assert snapshot.by_limit_id == {}


# --------------------------------------------------------------------------
# Fail-soft: malformed / error / timeout / no binary
# --------------------------------------------------------------------------


def test_malformed_result_fails_soft(fake_binary, auth_file):
    fake_binary("malformed")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)
    assert snapshot.available is False
    assert snapshot.error == "malformed_result"


def test_jsonrpc_error_response_fails_soft(fake_binary, auth_file):
    fake_binary("error_response")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)
    assert snapshot.available is False
    assert snapshot.error == "read_error"


def test_initialize_error_fails_soft(fake_binary, auth_file):
    fake_binary("init_error")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)
    assert snapshot.available is False
    assert snapshot.error == "initialize_error"


def test_timeout_fails_soft(fake_binary, auth_file):
    fake_binary("hang")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=1)
    assert snapshot.available is False
    assert snapshot.error in ("initialize_timeout_or_eof", "read_timeout_or_eof")


def test_missing_binary_fails_soft(monkeypatch, auth_file):
    monkeypatch.setattr(codex_quota, "_CODEX_BIN", "lingtai-codex-binary-does-not-exist-xyz")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=5)
    assert snapshot.available is False
    assert snapshot.error == "codex_binary_not_found"


def test_missing_auth_file_fails_soft(fake_binary, tmp_path):
    fake_binary("ok")
    missing = tmp_path / "nonexistent-auth.json"
    snapshot = codex_quota.read_codex_quota_snapshot(missing, timeout_seconds=5)
    assert snapshot.available is False
    assert snapshot.error == "auth_file_missing"


def test_read_never_raises_on_unexpected_error(monkeypatch, auth_file):
    def _boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(codex_quota, "_run_app_server_read", _boom)
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=5)
    assert snapshot.available is False
    assert snapshot.error is not None


# --------------------------------------------------------------------------
# Secret / path / raw-ID exclusion
# --------------------------------------------------------------------------


def test_no_secret_or_path_leakage_in_snapshot(fake_binary, auth_file):
    fake_binary("ok")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)
    d = codex_quota.quota_snapshot_to_dict(snapshot)
    serialized = json.dumps(d)

    assert "sk-should-never-appear" not in serialized
    assert "rt-should-never-appear-either" not in serialized
    assert str(auth_file) not in serialized
    assert str(auth_file.parent) not in serialized


def test_error_paths_never_leak_auth_path(fake_binary, auth_file):
    fake_binary("malformed")
    snapshot = codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)
    assert str(auth_file) not in (snapshot.error or "")


def test_temp_codex_home_is_owner_only_and_isolated(fake_binary, auth_file):
    """The temp $CODEX_HOME dir and auth copy get 0700/0600 perms and are
    cleaned up afterward; the real auth file is never mutated."""
    fake_binary("ok")
    original_mtime = auth_file.stat().st_mtime
    original_content = auth_file.read_bytes()

    captured = {}
    real_prepare = codex_quota._prepare_temp_codex_home

    def _spy(path):
        home, handle = real_prepare(path)
        captured["home"] = home
        captured["dest"] = home / "auth.json"
        # Assert perms while the dir still exists (inside the call).
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert stat.S_IMODE(captured["dest"].stat().st_mode) == 0o600
        return home, handle

    import lingtai.llm.openai.codex_quota as mod
    orig = mod._prepare_temp_codex_home
    mod._prepare_temp_codex_home = _spy
    try:
        codex_quota.read_codex_quota_snapshot(auth_file, timeout_seconds=10)
    finally:
        mod._prepare_temp_codex_home = orig

    assert captured, "spy was never invoked"
    assert not captured["home"].exists(), "temp CODEX_HOME must be cleaned up after the read"
    assert auth_file.stat().st_mtime == original_mtime
    assert auth_file.read_bytes() == original_content


# --------------------------------------------------------------------------
# codex-pool per-account snapshot listing
# --------------------------------------------------------------------------


@pytest.fixture()
def tui_dir(tmp_path, monkeypatch):
    d = tmp_path / "tui"
    d.mkdir()
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(d))
    return d


def _write_pool(tui_dir: Path, accounts, *, version=1, name="codex-auth-pool.json"):
    path = tui_dir / name
    path.write_text(json.dumps({"version": version, "accounts": accounts}), encoding="utf-8")
    return path


def _write_pool_v2(tui_dir: Path, models, *, version=2, name="codex-auth-pool.json"):
    path = tui_dir / name
    path.write_text(json.dumps({"version": version, "models": models}), encoding="utf-8")
    return path


def _write_real_auth(path: Path):
    path.write_text(json.dumps({
        "access_token": "sk-pool-secret",
        "refresh_token": "rt-pool-secret",
        "expires_at": 9999999999,
    }), encoding="utf-8")
    path.chmod(0o600)


def test_pool_quota_snapshots_preserve_order_and_duplicates(fake_binary, tui_dir):
    fake_binary("ok")
    a = tui_dir / "a.json"
    b = tui_dir / "b.json"
    _write_real_auth(a)
    _write_real_auth(b)
    _write_pool(tui_dir, [
        {"path": "a.json", "weight": 3},
        {"path": "b.json", "weight": 1},
        {"path": "a.json", "weight": 2},  # duplicate entry, preserved verbatim
    ])

    snapshots = codex_pool.list_codex_pool_quota_snapshots(timeout_seconds=10)
    assert len(snapshots) == 3
    assert [s["source_index"] for s in snapshots] == [0, 1, 2]
    assert [s["source_ref"] for s in snapshots] == ["a.json", "b.json", "a.json"]
    assert [s["weight"] for s in snapshots] == [3, 1, 2]
    # Duplicate paths get the SAME auth_path_sha8 (same resolved file), proving
    # no dedup happened while still being a stable non-secret identity.
    assert snapshots[0]["auth_path_sha8"] == snapshots[2]["auth_path_sha8"]
    assert snapshots[0]["auth_path_sha8"] != snapshots[1]["auth_path_sha8"]
    for s in snapshots:
        assert s["quota"]["available"] is True


def test_pool_quota_snapshots_model_category_scope(fake_binary, tui_dir):
    fake_binary("ok")
    x = tui_dir / "x.json"
    y = tui_dir / "y.json"
    _write_real_auth(x)
    _write_real_auth(y)
    _write_pool_v2(tui_dir, {
        "gpt-5-codex": [{"path": "x.json", "weight": 1}],
        "gpt-5-mini": [{"path": "y.json", "weight": 5}],
    })

    snaps = codex_pool.list_codex_pool_quota_snapshots(model="gpt-5-codex", timeout_seconds=10)
    assert len(snaps) == 1
    assert snaps[0]["source_ref"] == "x.json"
    assert snaps[0]["model_scope"] == "gpt-5-codex"

    snaps_other = codex_pool.list_codex_pool_quota_snapshots(model="gpt-5-mini", timeout_seconds=10)
    assert len(snaps_other) == 1
    assert snaps_other[0]["source_ref"] == "y.json"
    assert snaps_other[0]["weight"] == 5

    # No exact category -> empty, same fallback condition as selection.
    assert codex_pool.list_codex_pool_quota_snapshots(model="unknown-model", timeout_seconds=10) == []


def test_pool_quota_snapshots_empty_pool_returns_empty_list(tui_dir):
    # No pool file at all.
    assert codex_pool.list_codex_pool_quota_snapshots(timeout_seconds=5) == []


def test_pool_quota_snapshots_unqueryable_account_is_visibly_unavailable(fake_binary, tui_dir):
    fake_binary("ok")
    missing = tui_dir / "missing.json"  # never created
    _write_pool(tui_dir, [{"path": "missing.json", "weight": 1}])

    snaps = codex_pool.list_codex_pool_quota_snapshots(timeout_seconds=10)
    assert len(snaps) == 1
    assert snaps[0]["quota"]["available"] is False
    assert snaps[0]["quota"]["error"] == "auth_file_missing"
    # Still carries safe source metadata even though the account is unusable.
    assert snaps[0]["source_ref"] == "missing.json"
    assert snaps[0]["weight"] == 1


def test_pool_quota_absolute_ref_redacted(fake_binary, tui_dir):
    fake_binary("ok")
    abs_path = tui_dir / "abs.json"
    _write_real_auth(abs_path)
    _write_pool(tui_dir, [{"path": str(abs_path), "weight": 1}])

    snaps = codex_pool.list_codex_pool_quota_snapshots(timeout_seconds=10)
    assert snaps[0]["source_ref"] == codex_pool.ABSOLUTE_REF_REDACTED


def test_pool_quota_snapshots_never_leak_token_contents(fake_binary, tui_dir):
    fake_binary("ok")
    a = tui_dir / "a.json"
    _write_real_auth(a)
    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])

    snaps = codex_pool.list_codex_pool_quota_snapshots(timeout_seconds=10)
    serialized = json.dumps(snaps)
    assert "sk-pool-secret" not in serialized
    assert "rt-pool-secret" not in serialized
    assert str(a) not in serialized


# --------------------------------------------------------------------------
# Proof: quota reporting does not touch selection/routing/weights/files
# --------------------------------------------------------------------------


def test_quota_listing_does_not_mutate_selection_state(fake_binary, tui_dir):
    """Calling list_codex_pool_quota_snapshots must not change what
    select_codex_pool_auth picks, nor touch the pool file on disk."""
    fake_binary("ok")
    a = tui_dir / "a.json"
    b = tui_dir / "b.json"
    _write_real_auth(a)
    _write_real_auth(b)
    pool_path = _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 9},
    ])
    pool_bytes_before = pool_path.read_bytes()

    defaults = {"codex_session_anchor": str(tui_dir / "agent" / "init.json")}
    (tui_dir / "agent").mkdir()
    (tui_dir / "agent" / ".agent.json").write_text(
        json.dumps({"started_at": "2026-07-19T00:00:00Z", "molt_count": 0}), encoding="utf-8"
    )

    before = codex_pool.select_codex_pool_auth(defaults)
    codex_pool.list_codex_pool_quota_snapshots(defaults, timeout_seconds=10)
    after = codex_pool.select_codex_pool_auth(defaults)

    assert before == after
    assert pool_path.read_bytes() == pool_bytes_before


def test_quota_reads_use_isolated_temp_home_never_real_auth_dir(fake_binary, tui_dir, monkeypatch):
    """Each per-account probe must use its own throwaway $CODEX_HOME, never a
    shared/real one, and never write into the TUI dir."""
    fake_binary("ok")
    a = tui_dir / "a.json"
    _write_real_auth(a)
    _write_pool(tui_dir, [{"path": "a.json", "weight": 1}])

    before_listing = sorted(p.name for p in tui_dir.iterdir())
    codex_pool.list_codex_pool_quota_snapshots(timeout_seconds=10)
    after_listing = sorted(p.name for p in tui_dir.iterdir())

    assert before_listing == after_listing  # no stray files left in the TUI dir


# --------------------------------------------------------------------------
# Adapter-level wiring: CodexOpenAIAdapter.read_codex_quota
# --------------------------------------------------------------------------


def test_bare_adapter_has_no_auth_configured():
    """A directly-constructed adapter with no CodexTokenManager fails soft,
    never raises, and never spawns a subprocess."""
    adapter = CodexOpenAIAdapter(api_key="x", base_url="https://example.invalid")
    result = adapter.read_codex_quota()
    assert result == {
        "available": False,
        "error": "no_auth_configured",
        "primary": None,
        "by_limit_id": {},
    }


def test_codex_adapter_read_codex_quota_uses_token_manager_path(fake_binary, auth_file):
    """The real ``codex`` factory wires ``_codex_token_mgr``; ``read_codex_quota``
    must read THAT manager's auth path, proving the adapter-level surface is
    wired to the actual construction path (``_register.py``), not a stub."""
    fake_binary("ok")
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None
        mgr_cls.return_value._path = auth_file

        svc = LLMService(provider="codex", model="gpt-5-codex")
        adapter = svc.get_adapter("codex")
        assert isinstance(adapter, CodexOpenAIAdapter)

        result = adapter.read_codex_quota(timeout_seconds=10)

    assert result["available"] is True
    assert result["primary"]["limit_id"] == "codex"


def test_codex_pool_adapter_inherits_read_codex_quota(fake_binary, auth_file, tui_dir, tmp_path):
    """``codex-pool`` reuses ``CodexOpenAIAdapter`` unchanged, so it also gets
    ``read_codex_quota`` for its own (pool-selected) auth file, without any
    separate wiring — proving item 2/3 share one adapter surface."""
    fake_binary("ok")
    _write_pool(tui_dir, [{"path": str(auth_file), "weight": 1}])
    anchor_dir = tmp_path / "agent"
    anchor_dir.mkdir()
    (anchor_dir / ".agent.json").write_text(
        json.dumps({"started_at": "2026-07-19T00:00:00Z", "molt_count": 0}), encoding="utf-8"
    )

    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        def _make_mgr(*a, token_path=None, **kw):
            m = mock.MagicMock()
            m._path = Path(token_path) if token_path else auth_file
            m.get_access_token.return_value = "fake-token"
            m.get_account_id.return_value = None
            return m

        mgr_cls.side_effect = _make_mgr

        svc = LLMService(
            provider="codex-pool",
            model="gpt-5-codex",
            provider_defaults={
                "codex-pool": {"codex_session_anchor": str(anchor_dir / "init.json")}
            },
        )
        adapter = svc.get_adapter("codex-pool")
        assert isinstance(adapter, CodexOpenAIAdapter)

        result = adapter.read_codex_quota(timeout_seconds=10)

    assert result["available"] is True
    assert result["primary"]["limit_id"] == "codex"
