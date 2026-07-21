"""Tests for Codex pool file parsing, resolution, and provider error classification.

Thin-wrapper refactor (spec v3): account selection moved to
:mod:`lingtai.auth.codex_account_source` (tested separately in
``test_codex_account_source.py``).  These tests exercise:
  * pool file resolution (default + override + LINGTAI_TUI_DIR);
  * schema validation (disabled / blank-path / bad-weight accounts dropped);
  * model-classified (v2) pool parsing;
  * STRUCTURAL error classification (429 + ``usage_limit_reached``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import lingtai  # noqa: F401
from lingtai.auth import codex_pool


# ===========================================================================
# Helpers
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
# Pool file resolution
# ===========================================================================


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


# ===========================================================================
# Schema validation
# ===========================================================================


def test_load_pool_filters_invalid_accounts(tui_dir):
    pool = _write_pool(tui_dir, [
        {"path": "a.json", "weight": 1},
        {"path": "b.json", "weight": 2, "enabled": True},
        {"path": "disabled.json", "weight": 5, "enabled": False},
        {"path": "  ", "weight": 1},
        {"path": "zero.json", "weight": 0},
        {"path": "neg.json", "weight": -3},
        {"path": "bad.json", "weight": "lots"},
        {"path": "boolw.json", "weight": True},
        {"weight": 1},
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
    pool = _write_pool(tui_dir, [{"path": "only-path.json"}])
    assert codex_pool.load_codex_auth_pool(pool) == [{"path": "only-path.json", "weight": 1}]


# ===========================================================================
# Model-classified (v2) pool parsing
# ===========================================================================


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
    assert codex_pool.load_codex_auth_pool(pool, model=None) == []
    assert codex_pool.load_codex_auth_pool(pool) == []


def test_v2_load_models_is_sole_source_of_truth(tui_dir):
    pool = _write_pool_v2(
        tui_dir,
        {"gpt-5.6-sol": [{"path": "sol.json", "weight": 1}]},
    )
    # Just write the models dict — the old tests had a mixed file concept.
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.6-sol") == [
        {"path": "sol.json", "weight": 1},
    ]
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.5") == []
    assert codex_pool.load_codex_auth_pool(pool) == []


def test_v2_load_per_category_validation(tui_dir):
    pool = _write_pool_v2(tui_dir, {
        "gpt-5.6-sol": [
            {"path": "a.json", "weight": 1},
            {"path": "disabled.json", "weight": 5, "enabled": False},
            {"path": "  ", "weight": 1},
            {"path": "zero.json", "weight": 0},
            {"path": "boolw.json", "weight": True},
            {"path": "noweight.json"},
            "not-a-dict",
        ],
        "gpt-5.5": "not-a-list",
    })
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.6-sol") == [
        {"path": "a.json", "weight": 1},
        {"path": "noweight.json", "weight": 1},
    ]
    assert codex_pool.load_codex_auth_pool(pool, model="gpt-5.5") == []


# ===========================================================================
# Source ref redaction
# ===========================================================================


def test_safe_source_ref_relative(tui_dir):
    assert codex_pool._safe_source_ref("codex-auth/a.json") == "codex-auth/a.json"


def test_safe_source_ref_absolute(tui_dir, tmp_path):
    abs_path = str(tmp_path / "secret" / "token.json")
    assert codex_pool._safe_source_ref(abs_path) == codex_pool.ABSOLUTE_REF_REDACTED


# ===========================================================================
# STRUCTURAL error classification — 429 + usage_limit_reached
# ===========================================================================


class _FakeStatusError(Exception):
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
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(
        status_code=429,
        body={"error": {"type": "rate_limit", "code": "usage_limit_reached"}},
    )
    assert _is_usage_limit_reached_error(exc) is True


def test_recognizer_true_for_429_with_error_type():
    """429 + body.error.type == usage_limit_reached -> switch (Codex may nest here)."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(
        status_code=429,
        body={"error": {"type": "usage_limit_reached", "message": "quota"}},
    )
    assert _is_usage_limit_reached_error(exc) is True


def test_recognizer_true_for_429_with_top_level_body_code():
    """429 + top-level body.code == usage_limit_reached -> switch."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(status_code=429, body={"code": "usage_limit_reached"})
    assert _is_usage_limit_reached_error(exc) is True


def test_recognizer_true_for_429_with_code_attribute():
    """429 + exc.code (string attribute) == usage_limit_reached -> switch."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(status_code=429, code="usage_limit_reached")
    assert _is_usage_limit_reached_error(exc) is True


def test_recognizer_status_from_response_object():
    """Status extracted structurally from exc.response.status_code."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(
        response=_FakeResponse(429),
        body={"error": {"code": "usage_limit_reached"}},
    )
    assert _is_usage_limit_reached_error(exc) is True


def test_recognizer_false_for_429_other_code():
    """An ordinary 429 with a different code must NOT switch."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(
        status_code=429,
        body={"error": {"code": "rate_limit_exceeded"}},
    )
    assert _is_usage_limit_reached_error(exc) is False


def test_recognizer_false_for_429_no_code():
    """A 429 with no structured code must NOT switch."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(status_code=429, body={"error": {"message": "slow down"}})
    assert _is_usage_limit_reached_error(exc) is False


def test_recognizer_false_for_usage_limit_code_but_not_429():
    """usage_limit_reached at status 500 must NOT switch — 429 is required."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(
        status_code=500,
        body={"error": {"code": "usage_limit_reached"}},
    )
    assert _is_usage_limit_reached_error(exc) is False


def test_recognizer_false_when_only_in_message_string():
    """String mentions alone are NOT sufficient — must be a structured code."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(
        "429 usage_limit_reached: you are out of quota",
        status_code=429,
        body={"error": {"code": "rate_limit_exceeded"}},
    )
    assert _is_usage_limit_reached_error(exc) is False


def test_recognizer_false_when_status_only_in_message():
    """A body-only usage_limit_reached with no structured 429 does NOT switch."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(
        "got a 429 back",
        body={"error": {"code": "usage_limit_reached"}},
    )
    assert _is_usage_limit_reached_error(exc) is False


def test_recognizer_false_for_plain_exception():
    """A network error / timeout / arbitrary exception must NOT switch."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    assert _is_usage_limit_reached_error(TimeoutError("read timed out")) is False
    assert _is_usage_limit_reached_error(ValueError("nope")) is False


def test_recognizer_never_raises_on_weird_shapes():
    """Malformed body / non-dict error must be swallowed to False, never raise."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    for body in (None, [], "string-body", {"error": "not-a-dict"}, {"error": None}):
        exc = _FakeStatusError(status_code=429, body=body)
        assert _is_usage_limit_reached_error(exc) is False


def test_recognizer_bool_status_code_rejected():
    """A bool status_code (True==1 in Python) must not be read as 429."""
    from lingtai.auth.codex import _is_usage_limit_reached_error
    exc = _FakeStatusError(status_code=True, body={"error": {"code": "usage_limit_reached"}})
    assert _is_usage_limit_reached_error(exc) is False
