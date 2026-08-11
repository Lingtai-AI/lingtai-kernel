"""Unit tests for claude-p result usage normalization (no external `claude`)."""
import pytest

from lingtai.tools.daemon import _normalize_claude_usage


def test_normalize_claude_usage_combines_cached_inputs():
    """cached = cache_read_input_tokens + cache_creation_input_tokens."""
    usage = {
        "input_tokens": 6950,
        "cache_creation_input_tokens": 3068,
        "cache_read_input_tokens": 15621,
        "output_tokens": 4,
        "server_tool_use": {"web_search_requests": 0},
        "cache_creation": {"ephemeral_5m_input_tokens": 3068},
        "iterations": [],
    }
    norm = _normalize_claude_usage(usage)
    assert norm == {
        "input": 6950,
        "output": 4,
        "cached": 15621 + 3068,
        "thinking": 0,
    }


def test_normalize_claude_usage_handles_missing_cache_fields():
    norm = _normalize_claude_usage({"input_tokens": 100, "output_tokens": 50})
    assert norm == {"input": 100, "output": 50, "cached": 0, "thinking": 0}


def test_normalize_claude_usage_requires_input_tokens():
    assert _normalize_claude_usage({"output_tokens": 7}) is None


def test_normalize_claude_usage_requires_output_tokens():
    assert _normalize_claude_usage({"input_tokens": 7}) is None


def test_normalize_claude_usage_rejects_cache_only_usage():
    assert _normalize_claude_usage({
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2,
    }) is None


def test_normalize_claude_usage_returns_none_for_non_dict():
    assert _normalize_claude_usage(None) is None
    assert _normalize_claude_usage("nope") is None
    assert _normalize_claude_usage([1, 2, 3]) is None


def test_normalize_claude_usage_returns_none_when_all_zero():
    assert _normalize_claude_usage({}) is None
    assert _normalize_claude_usage({
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }) is None


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1, "output_tokens": 7},
        {"input_tokens": 100, "output_tokens": True},
        {"input_tokens": True, "output_tokens": 7},
        {
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": -5,
        },
        {"input_tokens": "lots", "output_tokens": 7},
        {
            "input_tokens": "lots",
            "output_tokens": 7,
            "cache_read_input_tokens": None,
        },
    ],
)
def test_normalize_claude_usage_rejects_invalid_fields(usage):
    """Every consumed token field must be a non-negative, non-bool integer."""
    assert _normalize_claude_usage(usage) is None
