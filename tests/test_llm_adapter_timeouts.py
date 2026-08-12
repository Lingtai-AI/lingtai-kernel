"""Tests for explicit per-phase HTTP timeout construction in adapters."""
from __future__ import annotations

import httpx
import pytest

from lingtai.llm.openai.adapter import _build_http_timeout as openai_timeout
from lingtai.llm.anthropic.adapter import _build_http_timeout as anthropic_timeout


@pytest.fixture(autouse=True)
def _clear_read_timeout_env(monkeypatch):
    """Default tests must be immune to a developer's local env var."""
    monkeypatch.delenv("LINGTAI_LLM_READ_TIMEOUT", raising=False)


def _assert_timeout(t: httpx.Timeout) -> None:
    assert isinstance(t, httpx.Timeout)
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
