"""Tests for Anthropic per-batch cache breakpoints."""

from lingtai.llm.anthropic.adapter import _build_system_batches_with_cache


def test_anthropic_marks_stable_batches_when_tail_batch_is_empty():
    blocks = _build_system_batches_with_cache(["stable-0", "stable-1", ""])

    assert [b["text"] for b in blocks] == ["stable-0", "stable-1"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_leaves_non_empty_tail_uncached():
    blocks = _build_system_batches_with_cache(["stable-0", "stable-1", "volatile-pad"])

    assert [b["text"] for b in blocks] == ["stable-0", "stable-1", "volatile-pad"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[2]


def test_anthropic_single_non_empty_batch_keeps_legacy_cache_marker():
    blocks = _build_system_batches_with_cache(["stable-only", "", ""])

    assert blocks == [
        {
            "type": "text",
            "text": "stable-only",
            "cache_control": {"type": "ephemeral"},
        }
    ]
