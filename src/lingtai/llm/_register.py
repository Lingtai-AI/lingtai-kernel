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


def _maybe_install_codex_pool_failover(
    *,
    chat,
    model,
    base_defaults,
    selected_auth_path,
    selected_source_index,
    build_isolated_adapter,
    create_chat_args,
    create_chat_kwargs,
):
    """Wrap ``chat.send``/``send_stream`` with request-scoped usage-limit failover.

    Codex-pool only. When (and only when) a provider send fails with a structural
    ``429`` whose structured error code is exactly ``usage_limit_reached``, switch
    to the next Codex account in the pool candidate SEQUENCE (validated pool order
    anchored to the ACTUAL selected occurrence via ``selected_source_index`` — the
    exact index weighted selection chose, so duplicate/aliased entries anchor to
    the picked occurrence, not merely the first path match; walked verbatim with
    NO realpath/alias dedup — repeated entries, aliases, and revisits/wraps to the
    same credential are each attempted, since a usage limit may be transient/soft)
    and retry within this same call, up to 10 ACTUAL switches, then re-raise the
    terminal provider error on exhaustion. The budget is a switch/retry budget, not a
    distinct-account budget: the primary attempt is not a switch, so the primary
    plus up to 10 switched alternates run; the 10th switched attempt runs and only
    ITS qualifying failure exhausts. Everything else (ordinary 429s, network
    errors, timeouts, other errors) propagates unchanged.

    **Single leaf drive (no nested double-drive).** The real
    ``CodexResponsesSession.send`` delegates to ``self.send_stream``. Both wrapper
    entrypoints route through ONE ``_drive`` that invokes the LEAF the session
    actually dispatches through, captured as the ORIGINAL bound method, so a
    ``chat.send(...)`` can never re-enter the wrapped ``send_stream`` and run a
    second failover pass (which would exceed the 10-switch budget).

    **Exact dual-snapshot ownership (S0 / H).** The canonical
    ``ChatInterface`` is shared across the primary and every alternate. Before the
    primary attempt, the wrapper captures S0: the caller-owned interface state
    that must be restored exactly if every eligible account is exhausted. The
    primary's real ``send_stream`` then stages the request and runs a one-shot
    wrapper around the real ``pre_request_hook`` (the kernel notification/tc-wake
    splice seam); after that hook runs exactly once, the wrapper captures H: the
    exact request-plus-hook retry wire. Each alternate restores H by exact slice
    replacement and replays with ``message=None`` and no hook, so it neither
    duplicates the request nor runs the hook twice. Everything a failed attempt
    appends after H is attempt-owned and intentionally dropped before the next
    alternate. If the primary raises before the hook fires, H falls back to S0.
    If all eligible accounts fail with the qualifying error, S0 is restored
    exactly: an ordinary failed request and its hook additions disappear, while a
    caller-prestaged ``send(None)`` tool-call/result pair survives.

    **Supported concurrency boundary.** A ``ChatSession`` and its
    ``ChatInterface`` are single-request objects (one per ``SessionManager``).
    Exact S0/H replacement deliberately does not merge or preserve concurrent
    appends: this feature adds no transaction/locking framework and does not make
    one interface safe for truly parallel mutation.

    **Streaming partial-output safety.** If a qualifying 429 arrives AFTER an
    ``on_chunk`` delta was already emitted, switching would mix/duplicate prefixes
    from two accounts, so the wrapper fails loud with the original error instead
    of retrying. A 429 before the first chunk (the normal case) still switches.

    Isolation: each alternate runs on its OWN freshly-built ``CodexOpenAIAdapter``
    (fresh client / token manager / account-id) via ``build_isolated_adapter``,
    which uses the same ``_codex`` builder as the primary — so it adds no adapter
    resources the primary did not have (``_codex`` constructs no ``APICallGate``
    for primary or alternate; this feature adds no gate/thread/executor). The
    shared cached adapter is never mutated, so concurrent requests can't observe
    each other's switched account and a fresh session drops any prior account's
    continuation state. The alternate's chat is stamped with the switched
    account's own non-secret selection (``source_ref`` redacted if absolute).

    A no-op (send path byte-identical to plain ``codex``) when the pool did not
    select a real account (legacy fallback / unusable pool).
    """
    from lingtai.auth.codex_pool import (
        _codex_pool_failover_candidates,
        _is_usage_limit_reached_error,
    )

    if not selected_auth_path:
        return  # legacy fallback — nothing to fail over to
    candidates = _codex_pool_failover_candidates(
        base_defaults, model, selected_auth_path, selected_source_index
    )
    if not candidates:
        return  # empty/unusable pool — nothing to fail over through

    # The live canonical interface shared by the primary and every alternate.
    shared_interface = getattr(chat, "interface", None)

    def _snapshot():
        if shared_interface is None:
            return None
        return list(shared_interface.entries)

    def _restore(target):
        # Exact slice replacement: restore the SAME interface object to exactly
        # ``target`` (same entry objects, same order). No identity-aware merge —
        # a failed attempt owns nothing beyond ``target``, so anything the failed
        # send appended past it (artifacts) is intentionally dropped.
        if shared_interface is None or target is None:
            return
        shared_interface.entries[:] = target

    def _build_retry_chat(account):
        retry_adapter = build_isolated_adapter(account["auth_path"])
        retry_kwargs = dict(create_chat_kwargs)
        if shared_interface is not None:
            retry_kwargs["interface"] = shared_interface
        retry_chat = retry_adapter.create_chat(*create_chat_args, **retry_kwargs)
        # Truthful non-secret attribution for the serving attempt: the switched
        # account's pool ref (redacted if absolute) / index / size / weight /
        # sha8, plus the ``failover`` marker so events/ledger show a switch.
        retry_chat.codex_pool_selection = {
            "source_ref": account["source_ref"],
            "source_index": account["source_index"],
            "pool_size": account["pool_size"],
            "weight": account["weight"],
            "auth_path_sha8": account["auth_path_sha8"],
            "model_scope": account.get("model_scope"),
            "failover": "usage_limit_reached",
        }
        # An alternate never re-runs the primary's pre_request_hook: the hook's
        # canonical additions are already in the restored baseline, and the
        # alternate replays with message=None. Leave its hook unset (None).
        return retry_chat

    # Capture the ORIGINAL leaf send_stream before overwriting the entrypoints.
    # Every attempt (primary + alternates) calls a raw ``send_stream`` — the leaf
    # the real ``CodexResponsesSession.send`` itself dispatches through — never the
    # wrappers, so no attempt can re-enter the failover loop (no nested drive).
    _leaf_send_stream = chat.send_stream

    def _drive(message, on_chunk):
        # Read the caller's live hook at drive time — the kernel installs
        # ``pre_request_hook`` on the chat AFTER create_chat returns, so it is not
        # yet set when this wrapper is installed. We temporarily replace it with a
        # one-shot capturing wrapper below and restore it in ``finally``.
        original_hook = getattr(chat, "pre_request_hook", None)
        # The prefix-mix hazard exists ONLY when the caller supplied a real
        # ``on_chunk`` observer (streaming): if a qualifying 429 arrives after a
        # delta was already surfaced to the caller, switching would replay/mix
        # prefixes from two accounts, so we must NOT switch. A non-streaming
        # ``send`` (on_chunk is None) surfaces nothing partial, so it always may
        # switch. We therefore only wrap ``on_chunk`` when one was given, and only
        # then track emission.
        emitted = {"chunk": False}

        if on_chunk is None:
            leaf_cb = None
        else:
            def leaf_cb(delta):
                emitted["chunk"] = True
                on_chunk(delta)

        # Two canonical baselines with distinct ownership (S0 / H):
        #   S0 = the EXACT pre-attempt canonical list — caller-owned terminal
        #        state. For ``send(None)`` it holds the pre-staged tool call/result
        #        (it predates this call and must survive terminal exhaustion); for
        #        an ordinary ``send(message)`` it does NOT hold that new request.
        #   H  = the EXACT post-primary-hook retry-wire baseline — S0 plus the
        #        request the real session staged (if any) plus the additions the
        #        real ``pre_request_hook`` made. It carries request+hook to every
        #        alternate exactly once.
        s0 = _snapshot()
        h = {"entries": s0}  # fallback to S0 if the primary raises before the hook

        def _capturing_hook(iface):
            # Run the caller's real hook EXACTLY ONCE (it may splice the
            # notification tool_call/result pair), THEN capture the post-hook
            # interface as H.
            if original_hook is not None:
                original_hook(iface)
            h["entries"] = list(iface.entries)

        # Install the one-shot capturing hook on the primary chat for this call
        # only; restore the original on the way out (success or failure).
        chat.pre_request_hook = _capturing_hook
        try:
            # First attempt: the primary chat's real leaf send_stream.
            try:
                return _leaf_send_stream(message, on_chunk=leaf_cb)
            except Exception as exc:  # noqa: BLE001 - re-raised unless we fail over
                if not _is_usage_limit_reached_error(exc) or emitted["chunk"]:
                    raise
                last_exc = exc

            # Switch attempts: each on its own isolated adapter/chat, restored to
            # H EXACTLY (request + hook pair, no failed-attempt artifacts) and
            # replayed with message=None (H already contains the request) and no
            # hook re-run.
            for account in candidates:
                _restore(h["entries"])
                emitted["chunk"] = False
                retry_chat = _build_retry_chat(account)
                try:
                    return retry_chat.send_stream(None, on_chunk=leaf_cb)
                except Exception as exc:  # noqa: BLE001
                    if not _is_usage_limit_reached_error(exc) or emitted["chunk"]:
                        raise
                    last_exc = exc
            # Eligible pool exhausted — restore the caller-owned S0 EXACTLY (drops
            # the ordinary failed request AND hook additions made only for this
            # failed logical call, while preserving a pre-staged send(None) pair)
            # and fail loud with the terminal provider error.
            _restore(s0)
            raise last_exc
        finally:
            chat.pre_request_hook = original_hook

    def _failover_send(message):
        # Mirror CodexResponsesSession.send: dispatch through the leaf send_stream
        # with no chunk observer.
        return _drive(message, None)

    def _failover_send_stream(message, on_chunk=None):
        return _drive(message, on_chunk)

    chat.send = _failover_send
    chat.send_stream = _failover_send_stream


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

    def _codex(*, model=None, defaults=None, **kw):
        from .openai.adapter import CodexOpenAIAdapter
        from lingtai.auth.codex import CodexTokenManager
        kw.pop("model", None)
        kw.pop("api_key", None)  # ignore env-resolved key
        # Honor an explicitly configured endpoint (manifest ``base_url`` /
        # ``provider_defaults['base_url']``, already resolved into ``base_url``
        # by LLMService._create_adapter) so a future local ``lingtai-codex-pool``
        # can front this provider. Absent/blank -> the official Codex endpoint.
        # The pool routes later off the unchanged ``prompt_cache_key`` /
        # ``session_id`` / ``thread_id`` identity emitted below; the OAuth bearer
        # may reach localhost in that use case, which is acceptable here.
        configured_base_url = kw.pop("base_url", None)
        codex_base_url = (
            configured_base_url.strip()
            if isinstance(configured_base_url, str) and configured_base_url.strip()
            else CODEX_OFFICIAL_BASE_URL
        )
        # Per-agent Codex REST cache-affinity header config (issue #378). The
        # host wiring (service.build_provider_defaults_from_manifest_llm) passes
        # down the agent path as ``codex_session_anchor`` by default; the adapter
        # hashes it together with the current molt_count into one 8-char value
        # used byte-identically for session_id, thread_id, and prompt_cache_key,
        # so a normal Codex agent sends per-agent headers. ``codex_thread_salt``
        # is forwarded only as a legacy pass-through (it no longer derives a
        # separate thread). The adapter has no per-agent identity of its own;
        # absent these keys (e.g. a bare service built in a test) it sends no
        # session/thread headers.
        d = defaults or {}
        codex_id_kw: dict = {}
        for cfg_key in ("codex_session_anchor", "codex_thread_salt"):
            val = d.get(cfg_key)
            if val is not None:
                codex_id_kw[cfg_key] = val
        # Optional Codex-only endpoint POOL (molt-boundary shuffle). When
        # ``codex_base_urls`` carries 2+ valid endpoints, the adapter chooses one
        # at request time by (stable per-agent offset + current ``molt_count``
        # from ``<working_dir>/.agent.json``); the choice is stable within a molt
        # segment and rotates only at a molt boundary. Empty/blank -> single
        # ``base_url`` behavior above (PR #495). ``codex_molt_count`` is an
        # explicit override (tests/hosts) used instead of reading ``.agent.json``.
        # Neither affects the ``session_id`` / ``thread_id`` / ``prompt_cache_key``
        # identity the pool routes off.
        for cfg_key in ("codex_base_urls", "codex_molt_count"):
            val = d.get(cfg_key)
            if val is not None:
                codex_id_kw[cfg_key] = val
        # Standalone Codex compaction threshold (daemon task
        # ``context_token_limit``). Codex-only and orthogonal to the generic
        # ``compact_threshold``/``context_management`` this factory never sets
        # for Codex (see ``CodexOpenAIAdapter._create_responses_session``).
        # Omitted -> the session falls back to its own resolved
        # ``context_window()`` at check time.
        compact_token_limit = d.get("codex_compact_token_limit")
        if compact_token_limit is not None:
            codex_id_kw["codex_compact_token_limit"] = compact_token_limit
        # Per-agent Codex OAuth token file (true multiple Codex accounts). When a
        # manifest/preset sets ``codex_auth_path`` to a non-empty path, read that
        # token file instead of the shared default ``~/.lingtai-tui/codex-auth.json``.
        # Blank/whitespace is treated as omitted -> legacy default-path behavior.
        # The path is non-secret; token contents are never logged.
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
            # The user's own ChatGPT account id (sent as the ``ChatGPT-Account-ID``
            # header when present). Read from their OAuth auth data; ``None`` when
            # unavailable. Does NOT impersonate the official Codex CLI.
            codex_account_id=mgr.get_account_id(),
            codex_auth_path_sha8=codex_auth_path_sha8,
            codex_auth_path_source=codex_auth_path_source,
            **codex_id_kw,
        )
        # Store the token manager so we can refresh before each API call.
        # The openai SDK's client.api_key is mutable — we update it in-place.
        adapter._codex_token_mgr = mgr
        def _refresh_codex_auth():
            # Keep the access token current (refreshes on disk if near expiry) and
            # re-read the account id so a refresh that changes it stays current on
            # sessions built afterwards. No token/account value is logged.
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

    def _codex_pool(*, model=None, defaults=None, **kw):
        # ``codex-pool``: same request/adapter/refresh logic as ``codex`` — we
        # only pick WHICH Codex OAuth token file to read and inject it as the
        # ordinary ``codex_auth_path``, then delegate to ``_codex``. The choice is
        # sticky per agent session (weighted across the non-secret pool file); a
        # model-classified pool restricts eligibility to the exact configured
        # model's category. A missing/empty/invalid pool (or no exact category)
        # returns None here, so defaults pass through unchanged and
        # ``CodexTokenManager`` uses its legacy default token path.
        # Provider ``codex`` is never affected — it does not read the pool file.
        from lingtai.auth.codex_pool import select_codex_pool_auth
        base_defaults = defaults  # the pre-injection defaults (for failover reuse)
        selected = select_codex_pool_auth(defaults, model=model)
        if selected:
            defaults = dict(defaults or {})
            defaults["codex_auth_path"] = selected["auth_path"]
        adapter = _codex(model=model, defaults=defaults, **kw)
        # Non-secret source-attribution breadcrumb (pool ref / index / size /
        # weight + short hash of the resolved token path — never tokens, never
        # auth-file contents): stamped on the adapter and on every chat it
        # creates, so the kernel's ``llm_call`` events can record which pool
        # source handled the call. Fallback runs carry an explicit marker
        # instead of silently looking like an unpooled ``codex`` agent.
        selection = (
            selected["selection"] if selected else {"fallback": "legacy_default"}
        )
        adapter.codex_pool_selection = selection
        selected_auth_path = selected["auth_path"] if selected else None
        _orig_pool_create_chat = adapter.create_chat
        def _selection_stamping_create_chat(*a, **kwa):
            chat = _orig_pool_create_chat(*a, **kwa)
            chat.codex_pool_selection = selection
            # Request-scoped usage-limit account failover (codex-pool only).
            # Installed only when the pool actually selected a real account; a
            # legacy fallback / unusable pool has nothing to fail over through, so
            # the chat's send path stays byte-identical to plain ``codex``. When
            # installed, the candidate SEQUENCE is walked verbatim (no realpath/
            # alias dedup, revisits/wraps permitted) for up to 10 actual switches.
            #
            # Alternates are built by the SAME ``_codex`` builder with the SAME
            # ``**kw`` as the primary — so they add no adapter resources the
            # primary did not already have. ``_codex`` does not forward ``max_rpm``
            # to ``CodexOpenAIAdapter``, so neither the primary nor any alternate
            # constructs an ``APICallGate``: this feature introduces no gate,
            # thread, or executor. (Whether ordinary Codex SHOULD be rate-gated is
            # a separate, pre-existing question outside this codex-pool
            # usage-limit contract and is intentionally not changed here.)
            _maybe_install_codex_pool_failover(
                chat=chat,
                model=model,
                base_defaults=base_defaults,
                selected_auth_path=selected_auth_path,
                # The AUTHORITATIVE anchor: the exact occurrence weighted
                # selection chose (``selection["source_index"]``). Read safely —
                # the legacy-fallback ``selection`` dict has no ``source_index``,
                # so ``.get`` yields ``None`` and the helper uses its path-scan
                # fallback (moot there, since ``selected_auth_path`` is also None).
                selected_source_index=selection.get("source_index"),
                build_isolated_adapter=lambda auth_path: _codex(
                    model=model,
                    defaults={**(base_defaults or {}), "codex_auth_path": auth_path},
                    **kw,
                ),
                create_chat_args=a,
                create_chat_kwargs=kwa,
            )
            return chat
        adapter.create_chat = _selection_stamping_create_chat
        return adapter

    # Register both spellings: LLMService does no dash/underscore normalization,
    # and a saved ``codex_pool`` preset must build the same provider.
    for name in ("codex-pool", "codex_pool"):
        LLMService.register_adapter(name, _codex_pool)

    def _claude_code(*, model=None, defaults=None, **kw):
        # Drive the local `claude` CLI as the agent brain on a Claude
        # subscription. The CLI owns auth (stored OAuth / CLAUDE_CODE_OAUTH_TOKEN),
        # so there is no api_key/base_url — drop any env-resolved ones.
        from .claude_code.adapter import ClaudeCodeAdapter
        kw.pop("model", None)
        kw.pop("api_key", None)
        kw.pop("base_url", None)
        kw.pop("default_headers", None)
        return ClaudeCodeAdapter(model=model, **{k: v for k, v in kw.items() if v is not None})

    # Register both the dash and underscore spellings: there is no dash/underscore
    # normalization in LLMService, and preset_connectivity treats both as the same
    # local CLI-login provider — so a saved `claude_code` preset must build too.
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
        return MimoAdapter(**{k: v for k, v in kw.items() if v is not None})

    LLMService.register_adapter("mimo", _mimo)

    # Providers routed through the generic custom adapter
    for name in ("grok", "qwen", "kimi"):
        LLMService.register_adapter(name, _custom)
