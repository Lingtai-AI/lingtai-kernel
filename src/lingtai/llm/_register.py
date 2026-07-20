"""Register all built-in LLM adapter factories with LLMService.

Each factory uses lazy imports so provider SDKs are only loaded when first used.
Each factory receives (model, defaults, **kw) from _create_adapter() and maps
to the adapter's actual constructor signature.
"""
from __future__ import annotations

import hashlib

# Official Codex REST endpoint. Used as the default ``base_url`` for the
# ``codex`` provider when the manifest/provider-defaults do not configure one.
# A configured ``base_url`` (the generic provider convention) overrides it so a
# future local ``lingtai-codex-pool`` endpoint can front the same provider
# without a separate adapter.
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
        if api_key is not None: adapter_kw["api_key"] = api_key
        if max_rpm > 0: adapter_kw["max_rpm"] = max_rpm
        if model: adapter_kw["default_model"] = model
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
        from .openai.adapter import CodexOpenAIAdapter
        from lingtai.auth.codex import CodexTokenManager
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

        # --- service_tier: fast → priority (common Codex boundary) ----------
        service_tier_raw = d.get("service_tier")
        try:
            service_tier = _normalize_service_tier(service_tier_raw)
        except ValueError:
            raise  # fail loud
        if service_tier is not None:
            codex_id_kw["codex_service_tier"] = service_tier

        auth_path = d.get("codex_auth_path")
        mgr_kw: dict = {}
        if isinstance(auth_path, str) and auth_path.strip():
            mgr_kw["token_path"] = auth_path
        mgr = CodexTokenManager(**mgr_kw)
        auth_path = getattr(mgr, "_path", None)
        codex_auth_path_sha8 = (
            hashlib.sha256(str(auth_path).encode("utf-8", "replace")).hexdigest()[:8]
            if auth_path
            else None
        )
        codex_auth_path_source = "configured" if mgr_kw.get("token_path") else "legacy_default"
        adapter = CodexOpenAIAdapter(
            api_key=mgr.get_access_token(),
            base_url=codex_base_url,
            use_responses=True,
            force_responses=True,
            codex_account_id=mgr.get_account_id(),
            codex_auth_path_sha8=codex_auth_path_sha8,
            codex_auth_path_source=codex_auth_path_source,
            **codex_id_kw,
        )
        adapter._codex_token_mgr = mgr
        def _refresh_codex_auth():
            adapter._client.api_key = mgr.get_access_token()
            adapter.codex_account_id = mgr.get_account_id()
        _orig_create_chat = adapter.create_chat
        def _refreshing_create_chat(*a, **kwa):
            _refresh_codex_auth()
            return _orig_create_chat(*a, **kwa)
        adapter.create_chat = _refreshing_create_chat
        _orig_generate = adapter.generate
        def _refreshing_generate(*a, **kwa):
            _refresh_codex_auth()
            return _orig_generate(*a, **kwa)
        adapter.generate = _refreshing_generate
        return adapter

    LLMService.register_adapter("codex", _codex)

    # -- codex-pool -----------------------------------------------------------

    def _codex_pool(*, model=None, defaults=None, **kw):
        """``codex-pool``: wraps ``_codex`` with a ``WeightedAccountSource``.

        Source-agnostic Codex attempt lifecycle: the pool supplies candidates
        only. Every ``create_chat`` call — including each one AED triggers via
        ``SessionManager._rebuild_session`` when a prior attempt on this same
        cached adapter failed — re-queries live quota and re-selects a fresh
        candidate from the source, excluding any identity a *previous* attempt
        on this adapter proved to be exhausted. A real provider failure is
        classified once, its identity recorded, and the exception re-raised
        unchanged so it enters the existing Codex/AED retry owner exactly once
        (``base_agent/turn.py``'s AED loop) — the pool itself never loops or
        retries a request. Codex core (``_codex``) owns token refresh, quota
        network I/O, REST/WS transport, and ledger attribution; this factory
        only binds one attempt's candidate before delegating to it.
        """
        from lingtai.auth.codex_pool import (
            resolve_codex_pool_path,
            resolve_codex_tui_dir,
        )
        from lingtai.auth.codex_account_source import (
            WeightedAccountSource,
            NoCandidateError,
        )
        from lingtai.auth.codex import _is_usage_limit_reached_error
        from lingtai.llm.openai.codex_quota import read_remaining_percent

        d = defaults or {}
        tui_dir = resolve_codex_tui_dir()
        pool_path = resolve_codex_pool_path(defaults)
        source = WeightedAccountSource(pool_path, tui_dir, model=model)

        def _build_selection(cand, *, pool_size: int, failover: bool = False):
            sel = {
                "source_ref": cand.source_ref,
                "source_index": cand.source_index,
                "pool_size": pool_size,
                "weight": cand.weight,
                "auth_path_sha8": cand.auth_path_sha8,
                "model_scope": model if model else None,
            }
            if failover:
                sel["failover"] = "usage_limit_reached"
            return sel

        def _build_quota_snapshot(
            exclude: set[str],
            pool_snapshot,
        ) -> tuple[dict[str, float] | None, set[str], bool]:
            """Query ``read_remaining_percent`` for every eligible account.

            Uses the immutable pool snapshot captured by the owning
            ``create_chat`` call, so this scan and the ``select`` that follows
            cannot observe different live pool-file states.

            Returns ``(snapshot, zero_sha8s, had_targets)``. ``snapshot`` is
            ``None`` when ANY eligible result is missing, invalid, or
            non-comparable — the whole draw falls back to static — but
            ``zero_sha8s`` still collects every identity independently proven
            to have ≤0 remaining during the full scan (the scan never
            early-returns before checking every target, so proven-zero
            identities are never silently discarded even when the draw itself
            goes static).
            """
            targets = source.quota_targets(
                exclude=exclude,
                snapshot=pool_snapshot,
            )
            snapshot: dict[str, float] = {}
            zero_sha8s: set[str] = set()
            saw_unusable = False
            for auth_ref, sha8 in targets:
                try:
                    pct = read_remaining_percent(auth_ref)
                except Exception:
                    pct = None  # fail-open: treat like "unavailable"
                usable = (
                    isinstance(pct, (int, float))
                    and not isinstance(pct, bool)
                    and pct == pct  # not NaN
                    and 0.0 <= float(pct) <= 100.0
                )
                if not usable:
                    saw_unusable = True
                    continue
                pct_f = float(pct)
                if pct_f <= 0.0:
                    zero_sha8s.add(sha8)
                    snapshot[sha8] = 0.0
                else:
                    snapshot[sha8] = pct_f / 100.0
            return (None if saw_unusable else snapshot, zero_sha8s, bool(targets))

        # ``exclude`` spans one AED attempt chain. Since ``get_adapter`` caches
        # this adapter, later AED-triggered ``create_chat`` calls share it and
        # avoid identities proven exhausted earlier in that chain. A successful
        # provider send ends the chain and clears the set so exclusions do not
        # leak into later turns or outlive an account's quota reset.
        exclude: set[str] = set()

        def _pool_create_chat(*a, **kwa):
            pool_snapshot = source.snapshot()
            quota_snapshot, zero_sha8s, had_targets = _build_quota_snapshot(
                exclude=exclude,
                pool_snapshot=pool_snapshot,
            )
            live_exclude = exclude | zero_sha8s

            if had_targets and quota_snapshot is not None and not (
                set(quota_snapshot) - zero_sha8s
            ):
                raise NoCandidateError(
                    "All eligible accounts have zero remaining quota"
                )

            try:
                cand = source.select(
                    exclude=live_exclude or None,
                    quota_left_snapshot=quota_snapshot,
                    snapshot=pool_snapshot,
                )
            except NoCandidateError:
                if pool_snapshot:
                    raise
                legacy_defaults = dict(d)
                legacy_defaults.pop("codex_auth_path", None)
                attempt_adapter = _codex(model=model, defaults=legacy_defaults, **kw)
                chat = attempt_adapter.create_chat(*a, **kwa)
                chat.codex_pool_selection = {"fallback": "legacy_default"}
                return chat

            nd = dict(d)
            nd["codex_auth_path"] = cand.auth_ref
            attempt_adapter = _codex(model=model, defaults=nd, **kw)
            sel = _build_selection(
                cand,
                pool_size=len(pool_snapshot),
                failover=bool(exclude),
            )
            chat = attempt_adapter.create_chat(*a, **kwa)
            chat.codex_pool_selection = sel

            _leaf_send_stream = chat.send_stream

            def _bound_send_stream(message, on_chunk=None):
                try:
                    result = _leaf_send_stream(message, on_chunk=on_chunk)
                except Exception as exc:
                    if _is_usage_limit_reached_error(exc):
                        exclude.add(cand.auth_path_sha8)
                    raise
                exclude.clear()
                return result

            chat.send_stream = _bound_send_stream
            chat.send = lambda msg: _bound_send_stream(msg, on_chunk=None)
            return chat

        placeholder_defaults = dict(d)
        placeholder_defaults.pop("codex_auth_path", None)
        adapter = _codex(model=model, defaults=placeholder_defaults, **kw)
        adapter.codex_pool_selection = {"fallback": "legacy_default"}
        adapter.create_chat = _pool_create_chat
        return adapter

    for name in ("codex-pool", "codex_pool"):
        LLMService.register_adapter(name, _codex_pool)

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
