"""Unit tests for claude-p result usage normalization (no external `claude`)."""
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


def test_normalize_claude_usage_rejects_non_int_fields():
    """A malformed (string/None) token count invalidates the whole event,
    matching the strict Codex/Cursor normalizer contract."""
    assert _normalize_claude_usage({
        "input_tokens": "lots", "output_tokens": 7,
        "cache_read_input_tokens": None,
    }) is None


def test_normalize_claude_usage_rejects_bool_fields():
    """bool is an int subclass but is not a token count."""
    assert _normalize_claude_usage({
        "input_tokens": True, "output_tokens": 7,
    }) is None


def test_normalize_claude_usage_rejects_negative_fields():
    assert _normalize_claude_usage({
        "input_tokens": -1, "output_tokens": 7,
    }) is None
