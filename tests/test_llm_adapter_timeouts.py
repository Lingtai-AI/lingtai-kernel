"""Tests for explicit per-phase HTTP timeout construction in adapters.

Maintenance premise (read before committing a lockfile): the SDK-acceptance
tests below (``test_*_timeout_accepted_by_installed_sdk``) and the openai
forward-guard (``test_openai_timeout_is_httpx_timeout`` +
``test_openai_timeout_accepted_by_installed_sdk``) only track *reality* because
``uv.lock`` is not committed and CI resolves the SDK fresh. If a PR ever commits
a lockfile, this group silently freezes on the locked SDK version and its
forward-guarding value degrades in lock-step; such a PR must also add an
explicit SDK version matrix (anthropic/openai across the supported range).
The same premise is what catches the ``getattr(..., "Timeout", httpx.Timeout)``
fallback's failure mode (a future SDK dropping the re-export while still sitting
on httpx2 would fall back to the class that happens to be rejected).
"""
from __future__ import annotations

import anthropic
import httpx
import openai
import pytest

from lingtai.llm.openai.adapter import _build_http_timeout as openai_timeout
from lingtai.llm.anthropic.adapter import _build_http_timeout as anthropic_timeout


@pytest.fixture(autouse=True)
def _clear_read_timeout_env(monkeypatch):
    """Default tests must be immune to a developer's local env var."""
    monkeypatch.delenv("LINGTAI_LLM_READ_TIMEOUT", raising=False)


def _assert_timeout(t) -> None:
    # Duck-type the per-phase caps. The concrete class is the SDK's *own* Timeout
    # (``httpx.Timeout`` for older SDKs, ``httpx2.Timeout`` for SDKs that moved to
    # the httpx2 fork), not necessarily ``httpx.Timeout`` — asserting that exact
    # class here is what let the httpx2 incompatibility pass CI (see
    # test_*_timeout_accepted_by_installed_sdk).
    assert t.connect == 30.0
    assert t.read == 300.0
    assert t.write == 30.0
    assert t.pool == 10.0


def test_openai_timeout_caps_read_phase():
    _assert_timeout(openai_timeout(300.0))


def test_anthropic_timeout_caps_read_phase():
    _assert_timeout(anthropic_timeout(300.0))


def test_timeout_respects_shorter_retry_timeout():
    t = openai_timeout(10.0)
    assert t.connect == 10.0
    assert t.read == 10.0
    assert t.write == 10.0
    assert t.pool == 10.0


def test_timeout_read_cap_allows_thinking_models():
    # Thinking models (DeepSeek/GLM extended thinking) can take 60-180s; the
    # read phase must stay under the watchdog's retry_timeout (300s), not a
    # 60s cap that kills mid-thought.
    t = openai_timeout(300.0)
    assert t.read == 300.0
    assert anthropic_timeout(300.0).read == 300.0


def test_timeout_read_cap_default_when_env_unset():
    assert openai_timeout(300.0).read == 300.0
    assert anthropic_timeout(300.0).read == 300.0


def test_timeout_read_cap_env_override():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LINGTAI_LLM_READ_TIMEOUT", "120")
    try:
        assert openai_timeout(300.0).read == 120.0
        assert anthropic_timeout(300.0).read == 120.0
    finally:
        monkeypatch.undo()


def test_timeout_read_cap_env_cannot_exceed_request_timeout():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LINGTAI_LLM_READ_TIMEOUT", "120")
    try:
        # request_timeout is the upper bound; the env cap only lowers it.
        assert openai_timeout(60.0).read == 60.0
        assert anthropic_timeout(60.0).read == 60.0
    finally:
        monkeypatch.undo()


def test_timeout_read_cap_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("LINGTAI_LLM_READ_TIMEOUT", "not-a-number")
    assert openai_timeout(300.0).read == 300.0
    assert anthropic_timeout(300.0).read == 300.0


def test_timeout_none_passthrough():
    assert openai_timeout(None) is None
    assert anthropic_timeout(None) is None


# ---------------------------------------------------------------------------
# SDK-acceptance regression (the httpx2 incompatibility).
#
# The tests above only inspect the constructed object's attributes; they never
# hand it to the SDK, so they stayed green while a real turn broke. anthropic/
# openai SDKs that moved to the ``httpx2`` fork hard-reject a foreign
# ``httpx.Timeout`` with a TypeError *before any network I/O*, on both the
# client-construction path and the per-request ``with_options`` path that
# ``SessionManager.send`` -> ``send_with_timeout`` drives. These lock the
# adapter's output against that rejection for whatever SDK version CI installs.
# They make no network call (constructing a client and copying options with a
# dummy key does not touch the network).
# ---------------------------------------------------------------------------


def test_anthropic_timeout_is_sdk_native_class():
    # Positive type guard: a regression that returns a plain float (or any wrong
    # type) would still satisfy the duck-typed attribute checks above, so assert
    # the concrete class IS the anthropic SDK's own Timeout — httpx2.Timeout on
    # SDKs that moved to the fork, httpx.Timeout on older ones.
    # NOTE (self-reference): the expected class is derived with the SAME getattr
    # expression the implementation uses, so this cannot catch the getattr
    # approach itself being wrong — it only catches a wrong/absent return type.
    # The real oracle for "is the approach correct" is
    # test_anthropic_timeout_accepted_by_installed_sdk below.
    assert type(anthropic_timeout(300.0)) is getattr(anthropic, "Timeout", httpx.Timeout)


def test_openai_timeout_is_httpx_timeout():
    # openai is intentionally NOT migrated in this change: openai 3.x accepts a
    # foreign httpx.Timeout (no fail-fast reject could be reproduced). If a future
    # openai adds the reject, test_openai_timeout_accepted_by_installed_sdk (below)
    # goes red and flags that a symmetric migration is then warranted.
    assert isinstance(openai_timeout(300.0), httpx.Timeout)


def test_anthropic_timeout_accepted_by_installed_sdk():
    t = anthropic_timeout(300.0)
    # client-construction path
    client = anthropic.Anthropic(api_key="test-no-network-call", timeout=t)
    # per-request path (adapter sets ``_request_timeout`` -> timeout kwarg)
    client.with_options(timeout=t)


def test_openai_timeout_accepted_by_installed_sdk():
    t = openai_timeout(300.0)
    client = openai.OpenAI(api_key="test-no-network-call", timeout=t)
    client.with_options(timeout=t)


def test_sdk_accepts_numeric_timeout_fallback():
    # A bare float is the documented fallback if per-phase construction is ever
    # unavailable; confirm both installed SDKs accept it on both paths.
    anthropic.Anthropic(api_key="test-no-network-call", timeout=300.0).with_options(
        timeout=300.0
    )
    openai.OpenAI(api_key="test-no-network-call", timeout=300.0).with_options(
        timeout=300.0
    )
