"""Register the four built-in LLM adapter factories with LLMService.

LingTai ships exactly four LLM provider families:

* ``openai``      — any OpenAI-compatible endpoint (official OpenAI by default);
                    ``wire_api`` selects ``chat_completions`` (default) or
                    ``responses`` (always stateless full-history replay).
* ``anthropic``   — any Anthropic-compatible (Messages API) endpoint.
* ``codex``       — Codex / ChatGPT OAuth (one account; pooling is external).
* ``claude-code`` — the local Claude Code CLI login.

Other vendors and subscriptions are reached by pointing ``openai`` or
``anthropic`` at that vendor's compatible endpoint, or at an external pool
(sub2api / subs-pool). Each factory uses lazy imports so provider SDKs are only
loaded when first used, and receives ``(model, defaults, **kw)`` from
``LLMService._create_adapter()``.
"""
from __future__ import annotations

# Official Codex REST endpoint. Used as the default ``base_url`` for the
# ``codex`` provider when the manifest/provider-defaults do not configure one.
# A configured ``base_url`` (the generic provider convention) overrides it;
# account selection remains inside the one native Codex adapter.
CODEX_OFFICIAL_BASE_URL = "https://chatgpt.com/backend-api/codex"

#: The registered LLM provider families, in documentation order.
LLM_PROVIDERS = ("openai", "anthropic", "codex", "claude-code")


# ---------------------------------------------------------------------------
# service_tier normalization — the one boundary for ``openai`` and ``codex``
# ---------------------------------------------------------------------------

#: Standard ``service_tier`` values forwarded verbatim on the wire.
SERVICE_TIER_VALUES = ("auto", "default", "flex", "priority")

#: User-facing aliases and their wire values.
_SERVICE_TIER_ALIASES: dict[str, str] = {
    "fast": "priority",
}


def _normalize_service_tier(raw: object) -> str | None:
    """Normalize a user-configured ``service_tier`` to its wire value.

    ``fast`` maps to the wire value ``priority``; the standard values
    ``auto``/``default``/``flex``/``priority`` pass through verbatim; absent or
    blank returns ``None`` (the field is omitted). Any other value raises
    ``ValueError`` — ``init_schema.validate_init`` applies this same function so
    a bad tier fails at init, never silently at the wire.
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
    alias = _SERVICE_TIER_ALIASES.get(val)
    if alias is not None:
        return alias
    if val in SERVICE_TIER_VALUES:
        return val
    raise ValueError(
        f"Unsupported service_tier value {val!r}; supported: "
        f"{', '.join(sorted(_SERVICE_TIER_ALIASES) + list(SERVICE_TIER_VALUES))}"
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all_adapters() -> None:
    from lingtai.llm.service import LLMService

    def _openai(*, model=None, defaults=None, **kw):
        """Build the generic OpenAI-compatible adapter.

        ``base_url`` is optional (the official endpoint when omitted);
        ``wire_api`` selects ``chat_completions`` (default; legacy ``auto`` is
        treated as omitted) or ``responses`` (stateless full-history replay).
        """
        from .openai.adapter import OpenAIAdapter
        kw.pop("model", None)
        adapter_kw = {k: v for k, v in kw.items() if v is not None}
        d = defaults or {}
        service_tier = _normalize_service_tier(d.get("service_tier"))
        if service_tier is not None:
            adapter_kw["service_tier"] = service_tier
        if d.get("wire_api") is not None:
            adapter_kw["wire_api"] = d["wire_api"]
        # Generic, provider-neutral OpenAI-compatible knobs lifted from the
        # manifest ``llm`` block so they are not dead schema.
        for _k in ("inject_reasoning_fallback", "prompt_cache_namespace"):
            if _k in d:
                adapter_kw[_k] = d[_k]
        return OpenAIAdapter(**adapter_kw)

    def _anthropic(*, model=None, defaults=None, **kw):
        """Build the generic Anthropic-compatible (Messages API) adapter."""
        from .anthropic.adapter import AnthropicAdapter
        kw.pop("model", None)
        return AnthropicAdapter(**{k: v for k, v in kw.items() if v is not None})

    LLMService.register_adapter("openai", _openai)
    LLMService.register_adapter("anthropic", _anthropic)

    # -- codex ----------------------------------------------------------------

    def _codex(*, model=None, defaults=None, **kw):
        """Build the native single-account Codex provider."""
        from .openai.adapter import CodexOpenAIAdapter
        from lingtai.auth.codex import CodexTokenManager, default_codex_token_path
        from lingtai.auth.codex_account_source import FixedAccountSource

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
        codex_id_kw["codex_allow_credits"] = d.get("codex_allow_credits", False)
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

        # One account: an explicit ``codex_auth_path`` or the default
        # ``<tui_dir>/codex-auth.json``. Binding is deferred until
        # create_chat/request time. Account pooling is external (subs-pool).
        auth_path = d.get("codex_auth_path")
        auth_path = auth_path.strip() if isinstance(auth_path, str) and auth_path.strip() else None
        source = FixedAccountSource(auth_path or str(default_codex_token_path()))

        return CodexOpenAIAdapter(
            api_key="__lingtai_codex_deferred__",
            base_url=codex_base_url,
            wire_api="responses",
            codex_account_source=source,
            codex_token_manager_factory=CodexTokenManager,
            **codex_id_kw,
        )

    LLMService.register_adapter("codex", _codex)

    # -- claude-code ----------------------------------------------------------

    def _claude_code(*, model=None, defaults=None, **kw):
        from .claude_code.adapter import ClaudeCodeAdapter
        kw.pop("model", None)
        kw.pop("api_key", None)
        kw.pop("base_url", None)
        kw.pop("default_headers", None)
        return ClaudeCodeAdapter(model=model, **{k: v for k, v in kw.items() if v is not None})

    LLMService.register_adapter("claude-code", _claude_code)
