"""Tests for the native Codex single-account source seam.

The source layer owns only the safe account identity and exclusion of the one
configured token file. Account pooling is not a kernel concern (it is provided
by the external subs-pool proxy). Factory checks prove the one registered
``codex`` spelling builds the lazy native adapter over a ``FixedAccountSource``;
request-time account binding, AED ownership, and partial-stream safety live in
``test_codex_native_multiaccount.py``.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
from unittest import mock

import pytest

import lingtai  # noqa: F401  (registers adapters)
from lingtai.auth import codex_account_source
from lingtai.auth.codex_account_source import (
    AccountCandidate,
    FixedAccountSource,
    NoCandidateError,
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


def _sha8(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]


# ===========================================================================
# AccountCandidate
# ===========================================================================


def test_candidate_sha8_is_stable():
    c = AccountCandidate(auth_ref="/tmp/token.json")
    assert len(c.auth_path_sha8) == 8
    assert c.auth_path_sha8 == _sha8("/tmp/token.json")
    assert AccountCandidate(auth_ref="/tmp/token.json") == c


def test_candidate_sha8_differs_by_path():
    c1 = AccountCandidate(auth_ref="/a")
    c2 = AccountCandidate(auth_ref="/b")
    assert c1.auth_path_sha8 != c2.auth_path_sha8


def test_candidate_is_frozen_and_sha8_is_derived_not_injected():
    c = AccountCandidate(auth_ref="/tmp/token.json")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.auth_ref = "/tmp/other.json"  # type: ignore[misc]
    with pytest.raises(TypeError):
        AccountCandidate(auth_ref="/tmp/token.json", auth_path_sha8="deadbeef")  # type: ignore[call-arg]


def test_candidate_carries_no_pool_fields():
    """Only the token path and its derived safe identity remain."""
    names = {f.name for f in dataclasses.fields(AccountCandidate)}
    assert names == {"auth_ref", "auth_path_sha8"}


# ===========================================================================
# FixedAccountSource
# ===========================================================================


def test_fixed_always_returns_same():
    src = FixedAccountSource("/tmp/t.json")
    c1 = src.select()
    c2 = src.select()
    assert c1 == c2
    assert c1.auth_ref == "/tmp/t.json"
    assert c1.auth_path_sha8 == _sha8("/tmp/t.json")


def test_fixed_with_exclude_raises():
    src = FixedAccountSource("/tmp/t.json")
    c = src.select()
    with pytest.raises(NoCandidateError):
        src.select(exclude={c.auth_path_sha8})


def test_fixed_ignores_empty_and_unrelated_exclusions():
    src = FixedAccountSource("/tmp/t.json")
    expected = src.select()
    assert src.select(exclude=None) == expected
    assert src.select(exclude=set()) == expected
    assert src.select(exclude={_sha8("/tmp/other.json")}) == expected


# ===========================================================================
# NoCandidateError
# ===========================================================================


def test_no_candidate_error_carries_no_diagnostics():
    """The single-account source has no pool/quota counts to report."""
    exc = NoCandidateError("Codex account is excluded")
    assert exc.args == ("Codex account is excluded",)
    assert exc.diagnostic_fields() == {}
    assert not hasattr(exc, "with_diagnostics")


def test_excluded_fixed_source_error_has_empty_diagnostics():
    src = FixedAccountSource("/tmp/t.json")
    with pytest.raises(NoCandidateError) as excinfo:
        src.select(exclude={_sha8("/tmp/t.json")})
    assert excinfo.value.diagnostic_fields() == {}
    assert "/tmp/t.json" not in str(excinfo.value)


# ===========================================================================
# Module surface: the in-kernel pool is gone
# ===========================================================================


def test_pool_symbols_are_removed_from_account_source():
    for name in (
        "WeightedAccountSource",
        "AccountSource",
        "_is_comparable_fraction",
        "_uniform_float",
    ):
        assert not hasattr(codex_account_source, name), name


@pytest.mark.parametrize(
    "module", ["lingtai.auth.codex_pool", "lingtai.llm.openai.codex_quota"]
)
def test_pool_and_quota_modules_are_removed(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_account_source_imports_are_clean():
    """Prove the module does not import network/quota/retry/chat/transport."""
    import ast
    import inspect

    src = inspect.getsource(codex_account_source)
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)

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


def test_service_tier_non_string_raises():
    with pytest.raises(ValueError, match="string"):
        _normalize_service_tier(123)


# ===========================================================================
# Factory integration: the one Codex spelling uses the FixedAccountSource seam
# ===========================================================================


def _codex_adapter(provider, defaults, model="gpt-5.5"):
    from lingtai.llm.service import LLMService

    svc = LLMService(
        provider=provider,
        model=model,
        provider_defaults={provider: defaults},
    )
    return svc.get_adapter(provider)


def test_codex_factory_binds_default_fixed_source_lazily(tui_dir):
    """No auth path: the default ``<tui_dir>/codex-auth.json``, no eager draw."""
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        adapter = _codex_adapter("codex", {})

        assert isinstance(adapter._codex_account_source, FixedAccountSource)
        assert adapter._codex_account_source.select().auth_ref == str(
            tui_dir / "codex-auth.json"
        )
        assert not hasattr(adapter, "_codex_current_selection")
        mgr_cls.assert_not_called()


def test_codex_factory_binds_explicit_auth_path_lazily(tui_dir):
    auth_path = str(tui_dir / "work.json")
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        adapter = _codex_adapter("codex", {"codex_auth_path": f"  {auth_path}  "})

        assert isinstance(adapter._codex_account_source, FixedAccountSource)
        assert adapter._codex_account_source.select().auth_ref == auth_path
        mgr_cls.assert_not_called()


@pytest.mark.parametrize("provider", ["codex-pool", "codex_pool"])
def test_removed_codex_pool_spellings_are_not_registered(provider):
    from lingtai.llm.service import LLMService

    assert provider not in LLMService._adapter_registry
    with pytest.raises(RuntimeError, match="No adapter registered"):
        _codex_adapter(provider, {})


def test_service_tier_fast_flows_to_adapter():
    """service_tier: fast reaches the one adapter as wire-level priority."""
    adapter = _codex_adapter("codex", {"service_tier": "fast"})
    assert adapter._codex_service_tier == "priority"


def test_service_tier_absent_omits():
    """No service_tier config leaves the native adapter tier unset."""
    adapter = _codex_adapter("codex", {})
    assert adapter._codex_service_tier is None


def test_service_tier_invalid_fails_loud():
    """Unsupported service_tier values fail at the common factory boundary."""
    with pytest.raises(ValueError, match="Unsupported service_tier"):
        _normalize_service_tier("turbo")
    with pytest.raises(ValueError, match="Unsupported service_tier"):
        _codex_adapter("codex", {"service_tier": "turbo"})


@pytest.mark.parametrize("value", ["auto", "default", "flex", "priority"])
def test_service_tier_standard_values_reach_codex_verbatim(value):
    adapter = _codex_adapter("codex", {"service_tier": value})
    assert adapter._codex_service_tier == value
