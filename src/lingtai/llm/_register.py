"""Register all built-in LLM adapter factories with LLMService.

Each factory uses lazy imports so provider SDKs are only loaded when first used.
Each factory receives (model, defaults, **kw) from _create_adapter() and maps
to the adapter's actual constructor signature.
"""
from __future__ import annotations

# Official Codex REST endpoint. Used as the default ``base_url`` for the
# ``codex`` provider when the manifest/provider-defaults do not configure one.
# A configured ``base_url`` (the generic provider convention) overrides it;
# account selection remains inside the one native Codex adapter.
CODEX_OFFICIAL_BASE_URL = "https://chatgpt.com/backend-api/codex"


# ---------------------------------------------------------------------------
# service_tier normalization — Codex common boundary
# ---------------------------------------------------------------------------

# Valid user-facing values and their wire (OpenAI/Codex) equivalents.
_SERVICE_TIER_WIRE: dict[str, str | None] = {
    "fast": "priority",  # the only supported alias in v1
}


def _normalize_service_tier(raw: object) -> str | None:
    """Normalize a user-configured ``service_tier`` to its wire value.

    Returns the wire value, or ``None`` to omit the field.
    Raises ``ValueError`` for unsupported or invalid values.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(
            f"service_tier must be a string, got {type(raw).__name__}"
        )
    val = raw.strip()
    if not val:
        return None
    wire = _SERVICE_TIER_WIRE.get(val)
    if wire is not None:
        return wire
    # Unknown value — reject loudly.
    raise ValueError(
        f"Unsupported service_tier value {val!r}; "
        f"supported: {sorted(_SERVICE_TIER_WIRE.keys())}"
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all_adapters() -> None:
    from lingtai.llm.service import LLMService

    def _gemini(*, model=None, defaults=None, api_key=None, max_rpm=0, **kw):
        from .gemini.adapter import GeminiAdapter
        adapter_kw: dict = {}
        if api_key is not None:
            adapter_kw["api_key"] = api_key
        if max_rpm > 0:
            adapter_kw["max_rpm"] = max_rpm
        if model:
            adapter_kw["default_model"] = model
        if kw.get("default_headers") is not None:
            adapter_kw["default_headers"] = kw["default_headers"]
        return GeminiAdapter(**adapter_kw)

    def _anthropic(*, model=None, defaults=None, **kw):
        from .anthropic.adapter import AnthropicAdapter
        kw.pop("model", None)
        return AnthropicAdapter(**{k: v for k, v in kw.items() if v is not None})

    def _openai(*, model=None, defaults=None, **kw):
        from .openai.adapter import OpenAIAdapter
        kw.pop("model", None)
        # Honor a host-configured Responses-API compaction threshold. Absent
        # from defaults -> let OpenAIAdapter's 100k constructor default stand;
        # explicit None -> disable Responses context_management.
        adapter_kw = {k: v for k, v in kw.items() if v is not None}
        d = defaults or {}
        if "compact_threshold" in d:
            # Preserve explicit None after the general None-pruning pass above.
            adapter_kw["compact_threshold"] = d["compact_threshold"]
        # Canonical ``wire_api`` and the legacy ``use_responses_api`` preference
        # are independent and both may be present (as in the openai DEFAULTS).
        # Pass each when present — do NOT ``elif`` them — so ``auto`` can delegate
        # to the legacy flag while an explicit value wins over it inside
        # ``_should_use_responses()``.
        if "wire_api" in d:
            adapter_kw["wire_api"] = d["wire_api"]
        if "use_responses_api" in d:
            adapter_kw["use_responses"] = d["use_responses_api"]
        return OpenAIAdapter(**adapter_kw)

    def _minimax(*, model=None, defaults=None, **kw):
        from .minimax.adapter import MiniMaxAdapter
        kw.pop("model", None)
        return MiniMaxAdapter(**{k: v for k, v in kw.items() if v is not None})

    def _openrouter(*, model=None, defaults=None, **kw):
        from .openrouter.adapter import OpenRouterAdapter
        kw.pop("model", None)
        return OpenRouterAdapter(**{k: v for k, v in kw.items() if v is not None})

    def _custom(*, model=None, defaults=None, **kw):
        from .custom.adapter import create_custom_adapter
        kw.pop("model", None)
        d = defaults or {}
        compat = d.get("api_compat", "openai")
        adapter_kw = {k: v for k, v in kw.items() if v is not None}
        # Canonical ``wire_api`` and the legacy ``use_responses_api`` preference
        # are independent and both may be present. Pass each when present — do NOT
        # ``elif`` them — so ``auto`` can delegate to the legacy flag while an
        # explicit value wins over it inside ``_should_use_responses()``.
        if "wire_api" in d:
            adapter_kw["wire_api"] = d["wire_api"]
        if "use_responses_api" in d:
            adapter_kw["use_responses"] = d["use_responses_api"]
        return create_custom_adapter(api_compat=compat, **adapter_kw)

    LLMService.register_adapter("gemini", _gemini)
    LLMService.register_adapter("anthropic", _anthropic)
    LLMService.register_adapter("openai", _openai)
    LLMService.register_adapter("minimax", _minimax)
    LLMService.register_adapter("openrouter", _openrouter)
    LLMService.register_adapter("custom", _custom)

    # -- codex ----------------------------------------------------------------

    def _codex(*, model=None, defaults=None, **kw):
        """Build the one native Codex provider, including account selection."""
        from .openai.adapter import CodexOpenAIAdapter
        from lingtai.auth.codex import CodexTokenManager
        from lingtai.auth.codex_account_source import FixedAccountSource, WeightedAccountSource
        from lingtai.auth.codex_pool import (
            legacy_codex_token_path,
            resolve_codex_pool_path,
            resolve_codex_tui_dir,
        )

        kw.pop("model", None)
        kw.pop("api_key", None)
        configured_base_url = kw.pop("base_url", None)
        codex_base_url = (
            configured_base_url.strip()
            if isinstance(configured_base_url, str) and configured_base_url.strip()
            else CODEX_OFFICIAL_BASE_URL
        )
        d = defaults or {}
        codex_id_kw: dict = {}
        for cfg_key in ("codex_session_anchor", "codex_thread_salt"):
            val = d.get(cfg_key)
            if val is not None:
                codex_id_kw[cfg_key] = val
        for cfg_key in ("codex_base_urls", "codex_molt_count"):
            val = d.get(cfg_key)
            if val is not None:
                codex_id_kw[cfg_key] = val
        compact_token_limit = d.get("codex_compact_token_limit")
        if compact_token_limit is not None:
            codex_id_kw["codex_compact_token_limit"] = compact_token_limit
        service_tier = _normalize_service_tier(d.get("service_tier"))
        if service_tier is not None:
            codex_id_kw["codex_service_tier"] = service_tier

        auth_path = d.get("codex_auth_path")
        auth_path = auth_path.strip() if isinstance(auth_path, str) and auth_path.strip() else None
        fallback_path = auth_path or str(legacy_codex_token_path())

        # The ordinary codex provider owns both the fixed and weighted source
        # paths. Binding is deferred until create_chat/request time, so a pool
        # does not require the legacy default credential to exist at boot.
        if auth_path:
            source = FixedAccountSource(auth_path)
        else:
            pool_path = resolve_codex_pool_path(d)
            tui_dir = resolve_codex_tui_dir()
            source = WeightedAccountSource(pool_path, tui_dir, model=model)

        return CodexOpenAIAdapter(
            api_key="__lingtai_codex_deferred__",
            base_url=codex_base_url,
            use_responses=True,
            force_responses=True,
            codex_account_source=source,
            codex_token_manager_factory=CodexTokenManager,
            codex_fallback_auth_path=fallback_path,
            **codex_id_kw,
        )

    # ``codex-pool`` remains only a configuration-level spelling.  All names
    # resolve to this same factory and the same native Codex adapter; there is
    # no pool-specific chat/session/retry implementation.
    for name in ("codex", "codex-pool", "codex_pool"):
        LLMService.register_adapter(name, _codex)

    def _claude_code(*, model=None, defaults=None, **kw):
        from .claude_code.adapter import ClaudeCodeAdapter
        kw.pop("model", None)
        kw.pop("api_key", None)
        kw.pop("base_url", None)
        kw.pop("default_headers", None)
        return ClaudeCodeAdapter(model=model, **{k: v for k, v in kw.items() if v is not None})

    for name in ("claude-code", "claude_code"):
        LLMService.register_adapter(name, _claude_code)

    def _deepseek(*, model=None, defaults=None, **kw):
        from .deepseek.adapter import DeepSeekAdapter
        kw.pop("model", None)
        return DeepSeekAdapter(**{k: v for k, v in kw.items() if v is not None})

    LLMService.register_adapter("deepseek", _deepseek)

    def _zhipu(*, model=None, defaults=None, **kw):
        from .zhipu.adapter import ZhipuAdapter
        kw.pop("model", None)
        return ZhipuAdapter(**{k: v for k, v in kw.items() if v is not None})

    for name in ("glm", "zhipu"):
        LLMService.register_adapter(name, _zhipu)

    def _mimo(*, model=None, defaults=None, **kw):
        from .mimo.adapter import MimoAdapter
        kw.pop("model", None)
        adapter_kw = {k: v for k, v in kw.items() if v is not None}
        d = defaults or {}
        if "wire_api" in d:
            adapter_kw["wire_api"] = d["wire_api"]
        compact_token_limit = d.get("mimo_compact_token_limit")
        if compact_token_limit is not None:
            adapter_kw["compact_token_limit"] = compact_token_limit
        return MimoAdapter(**adapter_kw)

    LLMService.register_adapter("mimo", _mimo)

    for name in ("grok", "qwen", "kimi"):
        LLMService.register_adapter(name, _custom)
