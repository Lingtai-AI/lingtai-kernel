"""Tests for Codex adapter quota / usage-limit diagnostics.

These tests verify that ``CodexResponsesSession._codex_quota_diagnostics``
correctly extracts safe, non-secret diagnostic metadata from various Codex API
error shapes, and that ``CodexOpenAIAdapter.is_quota_error`` extends the base
``openai.RateLimitError`` check to cover Codex-specific usage-limit messages.

All tests are pure and offline — no network, no secrets, no real SDK clients.
"""

from __future__ import annotations

import pytest
import openai

from lingtai.llm.openai.adapter import (
    CodexOpenAIAdapter,
    CodexResponsesSession,
)


# ---------------------------------------------------------------------------
# _codex_quota_diagnostics
# ---------------------------------------------------------------------------


class TestCodexQuotaDiagnostics:
    """Unit tests for ``CodexResponsesSession._codex_quota_diagnostics``."""

    def test_rate_limit_error(self):
        """Standard openai.RateLimitError (429) is flagged as quota."""
        exc = openai.RateLimitError(
            message="Rate limit exceeded",
            response=_fake_httpx_response(429, headers={
                "x-ratelimit-remaining-requests": "0",
                "x-ratelimit-reset-requests": "60s",
            }),
            body={"error": {"code": "rate_limit_exceeded", "type": "rate_limit"}},
        )
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        assert diag["is_quota"] is True
        assert diag["is_auth_error"] is False
        assert diag["status_code"] == 429
        assert diag["error_type"] == "RateLimitError"
        assert diag.get("error_code") == "rate_limit_exceeded"
        assert "rate limit" in diag.get("quota_message_fragments", [])

    def test_auth_error_401(self):
        """401 AuthenticationError is flagged as auth error."""
        exc = openai.AuthenticationError(
            message="Invalid API key",
            response=_fake_httpx_response(401),
            body={"error": {"code": "invalid_api_key", "type": "auth"}},
        )
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        assert diag["is_auth_error"] is True
        assert diag["status_code"] == 401
        assert diag["error_type"] == "AuthenticationError"

    def test_permission_denied_with_usage_limit(self):
        """403 PermissionDeniedError with 'usage limit' is flagged as quota."""
        exc = openai.PermissionDeniedError(
            message="You have exceeded your usage limit for this period",
            response=_fake_httpx_response(403),
            body={"error": {"code": "usage_limit_exceeded", "type": "permission"}},
        )
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        assert diag["is_quota"] is True
        assert diag["is_auth_error"] is True  # 403 is also auth
        assert "usage limit" in diag.get("quota_message_fragments", [])

    def test_bad_request_not_quota(self):
        """400 BadRequestError is neither quota nor auth."""
        exc = openai.BadRequestError(
            message="Invalid request",
            response=_fake_httpx_response(400),
            body={"error": {"code": "invalid_request", "type": "bad_request"}},
        )
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        assert diag["is_quota"] is False
        assert diag["is_auth_error"] is False

    def test_server_error_not_quota(self):
        """500 InternalServerError is neither quota nor auth."""
        exc = openai.InternalServerError(
            message="Internal server error",
            response=_fake_httpx_response(500),
            body={"error": {"code": "server_error", "type": "server"}},
        )
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        assert diag["is_quota"] is False
        assert diag["is_auth_error"] is False

    def test_generic_exception(self):
        """A plain non-SDK exception is handled gracefully."""
        exc = RuntimeError("some error")
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        assert diag["error_type"] == "RuntimeError"
        assert diag["is_quota"] is False
        assert diag["is_auth_error"] is False

    def test_no_secret_leakage(self):
        """Diagnostic dict contains only safe scalar keys."""
        exc = openai.RateLimitError(
            message="Rate limit exceeded with sentinel SHOULD_NOT_LEAK",
            response=_fake_httpx_response(429, headers={
                "x-ratelimit-remaining-requests": "0",
                "authorization": "SHOULD_NOT_LEAK_AUTH_HEADER",
            }),
            body={"error": {"code": "rate_limit_exceeded", "type": "rate_limit"}},
        )
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        # The diagnostic dict must NOT contain the full message or headers
        diag_str = str(diag)
        assert "SHOULD_NOT_LEAK" not in diag_str
        # authorization is not a tracked rate-limit header
        assert "authorization" not in diag_str.lower()

    def test_rate_limit_headers_extracted(self):
        """Rate-limit headers are included when present."""
        exc = openai.RateLimitError(
            message="Too many requests",
            response=_fake_httpx_response(429, headers={
                "x-ratelimit-limit-requests": "100",
                "x-ratelimit-remaining-requests": "0",
                "x-ratelimit-reset-requests": "60s",
                "retry-after": "30",
            }),
            body={},
        )
        diag = CodexResponsesSession._codex_quota_diagnostics(exc)
        rl = diag.get("rate_limit_headers", {})
        assert rl.get("x-ratelimit-limit-requests") == "100"
        assert rl.get("x-ratelimit-remaining-requests") == "0"
        assert rl.get("retry-after") == "30"

    def test_quota_fragments_detected(self):
        """Various quota-related message fragments are detected."""
        fragments = [
            "usage limit exceeded",
            "quota exceeded",
            "rate limit hit",
            "too many requests",
            "insufficient quota",
        ]
        for msg in fragments:
            exc = Exception(msg)
            diag = CodexResponsesSession._codex_quota_diagnostics(exc)
            assert diag["is_quota"] is True, f"Expected is_quota for: {msg}"


# ---------------------------------------------------------------------------
# CodexOpenAIAdapter.is_quota_error
# ---------------------------------------------------------------------------


class TestCodexOpenAIAdapterIsQuotaError:
    """Test that ``CodexOpenAIAdapter.is_quota_error`` extends the base check."""

    def test_standard_rate_limit_error(self):
        """openai.RateLimitError is detected as quota."""
        adapter = _make_bare_codex_adapter()
        exc = openai.RateLimitError(
            message="Rate limit",
            response=_fake_httpx_response(429),
            body={},
        )
        assert adapter.is_quota_error(exc) is True

    def test_codex_usage_limit_message(self):
        """Codex 'usage limit' in a 403 is detected as quota."""
        adapter = _make_bare_codex_adapter()
        exc = openai.PermissionDeniedError(
            message="You have exceeded your usage limit",
            response=_fake_httpx_response(403),
            body={},
        )
        assert adapter.is_quota_error(exc) is True

    def test_non_quota_error(self):
        """A generic error is not detected as quota."""
        adapter = _make_bare_codex_adapter()
        exc = openai.BadRequestError(
            message="Invalid request",
            response=_fake_httpx_response(400),
            body={},
        )
        assert adapter.is_quota_error(exc) is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_httpx_response(status_code: int, headers: dict | None = None):
    """Build a minimal fake httpx.Response for openai SDK error construction."""
    import httpx

    _headers = {"content-type": "application/json"}
    if headers:
        _headers.update(headers)
    return httpx.Response(
        status_code=status_code,
        headers=_headers,
        content=b"{}",
        request=httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses"),
    )


def _make_bare_codex_adapter() -> CodexOpenAIAdapter:
    """Build a CodexOpenAIAdapter with a dummy API key (no network)."""
    return CodexOpenAIAdapter(
        api_key="test-dummy-key-not-real",
        use_responses=True,
        force_responses=True,
    )
