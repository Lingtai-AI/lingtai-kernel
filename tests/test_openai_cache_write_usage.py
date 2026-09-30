"""OpenAI-compatible usage carries the wire cache-write count when reported.

Codex and other OpenAI-compatible backends may report
``input_tokens_details.cache_write_tokens`` (Responses) or
``prompt_tokens_details.cache_write_tokens`` (Chat Completions). The adapter
keeps it as ``UsageMetadata.cache_write_tokens`` so the Telegram price line
can price the ``↑`` cache-miss input exactly; an absent field stays unknown.
"""

from __future__ import annotations

from types import SimpleNamespace

import openai.types.responses as responses_types

from lingtai.llm.openai.adapter import _wire_cache_write_tokens


def test_reported_cache_write_count_is_kept():
    assert _wire_cache_write_tokens(SimpleNamespace(cached_tokens=5, cache_write_tokens=120)) == 120
    assert _wire_cache_write_tokens(SimpleNamespace(cached_tokens=5, cache_write_tokens=0)) == 0


def test_absent_or_invalid_cache_write_count_stays_unknown():
    assert _wire_cache_write_tokens(None) is None
    assert _wire_cache_write_tokens(SimpleNamespace(cached_tokens=5)) is None
    for bad in (-1, True, 1.5, "3", None):
        assert _wire_cache_write_tokens(SimpleNamespace(cache_write_tokens=bad)) is None


def test_sdk_usage_model_keeps_the_extra_cache_write_field():
    usage = responses_types.ResponseUsage.model_validate({
        "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
        "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 4},
        "output_tokens_details": {"reasoning_tokens": 0},
    })
    assert _wire_cache_write_tokens(usage.input_tokens_details) == 4
