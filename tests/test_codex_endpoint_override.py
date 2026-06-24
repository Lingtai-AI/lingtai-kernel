"""Tests for the configurable Codex endpoint / ``base_url`` override.

The ``codex`` provider historically hardcoded the official endpoint
``https://chatgpt.com/backend-api/codex`` and discarded any caller-supplied
``base_url``. To support a future local ``lingtai-codex-pool`` endpoint, the
factory now honors an explicit ``base_url`` (already plumbed generically by
``LLMService._create_adapter`` from ``manifest.llm['base_url']`` /
``provider_defaults['base_url']``) while keeping the official endpoint as the
default when none is configured.

These tests make NO network calls — the OAuth ``CodexTokenManager`` is mocked,
and we inspect the constructed adapter's OpenAI client ``base_url`` (the real
end-to-end value, the same one ``BaseAgent`` records via ``service._base_url``).
Codex per-agent identity (``codex_session_anchor``) and token-file selection
(``codex_auth_path``) behavior must remain intact.
"""

from __future__ import annotations

from unittest import mock

import lingtai  # noqa: F401  (registers adapters / loads service module)
from lingtai.llm.service import LLMService

# The official Codex REST endpoint — the default when nothing is configured.
_OFFICIAL_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _client_base_url(adapter) -> str:
    """The adapter's effective endpoint, trailing slash normalized away.

    The OpenAI SDK appends a trailing slash to ``base_url``; strip it so the
    assertion compares the configured endpoint, not an SDK formatting quirk.
    """
    return str(adapter._client.base_url).rstrip("/")


def test_codex_defaults_to_official_endpoint_when_base_url_absent():
    """No configured base_url -> Codex adapter still uses the official endpoint."""
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None

        svc = LLMService(provider="codex", model="gpt-5.5")
        adapter = svc.get_adapter("codex")

        assert _client_base_url(adapter) == _OFFICIAL_CODEX_BASE_URL


def test_codex_honors_configured_base_url():
    """An explicit base_url reaches CodexOpenAIAdapter for provider codex.

    This is the seam the future local ``lingtai-codex-pool`` endpoint needs:
    point the existing ``codex`` provider at a local pool URL instead of the
    hardcoded official endpoint.
    """
    pool_url = "http://127.0.0.1:8810/backend-api/codex"
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None

        svc = LLMService(provider="codex", model="gpt-5.5", base_url=pool_url)
        adapter = svc.get_adapter("codex", pool_url)

        assert _client_base_url(adapter) == pool_url


def test_codex_base_url_from_provider_defaults_reaches_adapter():
    """base_url supplied via provider_defaults (manifest convention) is honored.

    ``LLMService._create_adapter`` resolves ``effective_url = base_url or
    defaults['base_url']``; the codex factory must consume that the same way the
    generic providers do.
    """
    pool_url = "http://127.0.0.1:8810/backend-api/codex"
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None

        svc = LLMService(
            provider="codex",
            model="gpt-5.5",
            provider_defaults={"codex": {"base_url": pool_url}},
        )
        adapter = svc.get_adapter("codex")

        assert _client_base_url(adapter) == pool_url


def test_codex_configured_base_url_preserves_session_anchor_identity():
    """Pointing at a local pool must NOT change the per-agent cache identity.

    The pool routes later using the existing ``prompt_cache_key`` /
    ``session_id`` / ``thread_id`` identity emitted by the Codex adapter. A
    configured base_url alongside a ``codex_session_anchor`` must leave that
    identity (the 8-char agent-path hash) untouched.
    """
    from lingtai.llm.openai.adapter import _codex_session_id

    pool_url = "http://127.0.0.1:8810/backend-api/codex"
    anchor = "/agents/alice/init.json"
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None

        svc = LLMService(
            provider="codex",
            model="gpt-5.5",
            base_url=pool_url,
            provider_defaults={"codex": {"codex_session_anchor": anchor}},
        )
        adapter = svc.get_adapter("codex", pool_url)

        # Endpoint is the pool; identity is still the pure agent-path hash.
        assert _client_base_url(adapter) == pool_url
        sid, tid = adapter._resolve_codex_ids("gpt-5.5")
        assert sid == tid == _codex_session_id(anchor)


def test_codex_configured_base_url_preserves_auth_path_selection():
    """A configured base_url must not disturb ``codex_auth_path`` token-file wiring."""
    pool_url = "http://127.0.0.1:8810/backend-api/codex"
    auth_path = "/secrets/alice/codex-auth.json"
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None

        svc = LLMService(
            provider="codex",
            model="gpt-5.5",
            base_url=pool_url,
            provider_defaults={"codex": {"codex_auth_path": auth_path}},
        )
        adapter = svc.get_adapter("codex", pool_url)

        # base_url reached the adapter and the per-agent token file is still used.
        assert _client_base_url(adapter) == pool_url
        mgr_cls.assert_called_once_with(token_path=auth_path)


def test_codex_configured_base_url_reaches_websocket_url():
    """The configured endpoint also drives the Codex websocket URL.

    Codex WS is on by default, so a local pool must be reachable over WS too:
    the per-session ``_ws_base_url`` carries the configured HTTP base and
    ``_codex_ws_url`` derives ``ws://`` for an http (localhost) pool. This is a
    pure URL-derivation check — no socket is opened.
    """
    from lingtai.llm.openai.adapter import _codex_ws_url

    pool_url = "http://127.0.0.1:8810/backend-api/codex"
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None

        svc = LLMService(provider="codex", model="gpt-5.5", base_url=pool_url)
        adapter = svc.get_adapter("codex", pool_url)
        chat = adapter.create_chat(
            "gpt-5.5", "system prompt", tools=None,
            force_tool_call=False, thinking="high",
        )

        # Sessions may be gate-wrapped; the Codex session is the inner object.
        session = getattr(chat, "_session", chat)
        assert session._ws_base_url == pool_url
        assert (
            _codex_ws_url(session._ws_base_url)
            == "ws://127.0.0.1:8810/backend-api/codex/responses"
        )


def test_codex_factory_strips_configured_base_url() -> None:
    pool_url = "http://127.0.0.1:51999/backend-api/codex"
    configured_url = f"  {pool_url}  "
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        mgr_cls.return_value.get_account_id.return_value = None

        svc = LLMService(provider="codex", model="gpt-5.5", base_url=configured_url)
        adapter = svc.get_adapter("codex", configured_url)

        assert _client_base_url(adapter) == pool_url
