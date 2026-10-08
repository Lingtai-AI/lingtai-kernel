"""A daemon run from a preset forwards every generic LLM knob to its adapter.

``DaemonManager._llm_defaults_from_manifest`` turns a preset's ``manifest.llm``
into the daemon-scoped ``LLMService`` provider-defaults bucket. It reuses the
main boot builder (``build_provider_defaults_from_manifest_llm``) so the two
paths cannot drift: ``service_tier``, ``wire_api``,
``inject_reasoning_fallback``, ``prompt_cache_namespace``,
``default_headers``, the ``codex_*`` identity/endpoint keys, ``base_url`` and
``max_rpm`` all reach the daemon's adapter factory. Retired keys never do.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.llm.service import LLMService, build_provider_defaults_from_manifest_llm
from lingtai.tools.daemon import DaemonManager


_FULL_OPENAI_LLM = {
    "provider": "openai",
    "model": "gpt-5.5",
    "api_key_env": "OPENAI_API_KEY",
    "base_url": "https://compat.example/v1",
    "wire_api": "responses",
    "service_tier": "fast",
    "inject_reasoning_fallback": False,
    "prompt_cache_namespace": "acme",
    "default_headers": {"X-Test": "1"},
    "max_rpm": 30,
    # Retired / not adapter-consulted keys must never be forwarded.
    "api_compat": "openai",
    "reasoning_effort_vocab": "seven_tier",
    "use_responses_api": True,
    "compact_threshold": 100_000,
    "thinking": "high",
    "context_limit": 400_000,
}


def test_preset_llm_forwards_every_generic_knob():
    bucket = DaemonManager._llm_defaults_from_manifest(dict(_FULL_OPENAI_LLM))

    assert bucket == {
        "base_url": "https://compat.example/v1",
        "wire_api": "responses",
        "service_tier": "fast",
        "inject_reasoning_fallback": False,
        "prompt_cache_namespace": "acme",
        "default_headers": {"X-Test": "1"},
        "max_rpm": 30,
    }


def test_preset_llm_bucket_matches_the_main_boot_builder():
    llm = dict(_FULL_OPENAI_LLM)
    boot = build_provider_defaults_from_manifest_llm(dict(llm), max_rpm=llm["max_rpm"])
    daemon = DaemonManager._llm_defaults_from_manifest(dict(llm))

    expected = dict(boot["openai"])
    expected["base_url"] = llm["base_url"]
    assert daemon == expected


def test_codex_preset_forwards_codex_keys_and_service_tier():
    llm = {
        "provider": "codex",
        "model": "gpt-5.5",
        "service_tier": "priority",
        "codex_auth_path": "/tokens/alice.json",
        "codex_base_urls": ["https://a.example/v1", "https://b.example/v1"],
        "codex_thread_salt": "salt",
    }
    bucket = DaemonManager._llm_defaults_from_manifest(llm)
    assert bucket == {
        "service_tier": "priority",
        "codex_auth_path": "/tokens/alice.json",
        "codex_base_urls": ["https://a.example/v1", "https://b.example/v1"],
        "codex_thread_salt": "salt",
    }


def test_none_values_and_non_positive_max_rpm_are_omitted():
    llm = {
        "provider": "openai",
        "model": "m",
        "base_url": None,
        "service_tier": None,
        "max_rpm": 0,
    }
    assert DaemonManager._llm_defaults_from_manifest(llm) == {}


def test_forwarded_bucket_reaches_the_openai_adapter():
    """End to end: the daemon bucket builds an adapter carrying the knobs."""
    bucket = DaemonManager._llm_defaults_from_manifest(dict(_FULL_OPENAI_LLM))
    service = LLMService(
        provider="openai",
        model="gpt-5.5",
        api_key="sk-test",
        base_url=bucket.get("base_url"),
        provider_defaults={"openai": bucket},
    )
    adapter = service.get_adapter("openai", bucket.get("base_url"))
    try:
        assert adapter._wire_api == "responses"
        assert adapter._service_tier == "priority"
        assert adapter._inject_reasoning_fallback is False
        assert adapter._prompt_cache_namespace == "acme"
        assert adapter.effective_base_url == "https://compat.example/v1"
        assert adapter._client_kwargs["default_headers"]["X-Test"] == "1"
    finally:
        gate = getattr(adapter, "_gate", None)
        if gate is not None and hasattr(gate, "shutdown"):
            gate.shutdown()


@pytest.mark.parametrize("provider", ["openai", "anthropic", "claude-code"])
def test_context_token_limit_is_codex_only(provider):
    manager = SimpleNamespace(
        _daemon_codex_session_anchor=DaemonManager._daemon_codex_session_anchor,
    )
    run_dir = SimpleNamespace(path=Path("/tmp/run"))
    out = DaemonManager._daemon_provider_defaults(
        manager, provider, {"wire_api": "responses"}, run_dir, context_token_limit=5000
    )
    assert out == {provider: {"wire_api": "responses"}}
    assert "mimo_compact_token_limit" not in out[provider]
    assert "codex_compact_token_limit" not in out[provider]


def test_codex_daemon_gets_run_anchor_and_compact_limit(tmp_path):
    manager = SimpleNamespace(
        _daemon_codex_session_anchor=DaemonManager._daemon_codex_session_anchor,
    )
    run_dir = SimpleNamespace(path=tmp_path)
    out = DaemonManager._daemon_provider_defaults(
        manager, "codex", {"service_tier": "fast"}, run_dir, context_token_limit=5000
    )
    assert out == {
        "codex": {
            "service_tier": "fast",
            "codex_session_anchor": str((tmp_path / "daemon.json").resolve()),
            "codex_compact_token_limit": 5000,
        }
    }
