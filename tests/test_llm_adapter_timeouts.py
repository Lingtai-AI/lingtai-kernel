"""Tests for explicit per-phase HTTP timeout construction in adapters."""
from __future__ import annotations

import httpx

from lingtai.llm.openai.adapter import _build_http_timeout as openai_timeout
from lingtai.llm.anthropic.adapter import _build_http_timeout as anthropic_timeout


def _assert_timeout(t: httpx.Timeout) -> None:
    assert isinstance(t, httpx.Timeout)
    assert t.connect == 30.0
    assert t.read == 60.0
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


def test_timeout_none_passthrough():
    assert openai_timeout(None) is None
    assert anthropic_timeout(None) is None
