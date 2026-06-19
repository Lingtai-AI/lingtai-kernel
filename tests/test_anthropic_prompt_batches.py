"""Tests for Anthropic per-batch cache breakpoints."""

from lingtai.kernel.prompt import SystemPromptManager, build_system_prompt_batches
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


def _prompt_manager_with_stable_sections(*, pad: str | None) -> SystemPromptManager:
    mgr = SystemPromptManager()
    mgr.write_section("principle", "Core principle.", protected=True)
    mgr.write_section("tools", "### bash\nRun commands.", protected=True)
    mgr.write_section("rules", "No deleting files.", protected=True)
    mgr.write_section("skills", "- name: bash-manual", protected=True)
    mgr.write_section("identity", "You are test-agent.", protected=True)
    mgr.write_section("character", "Meticulous archivist.", protected=True)
    if pad is not None:
        mgr.write_section("pad", pad)
    return mgr


def test_anthropic_real_prompt_batches_cache_stable_prefix_when_pad_non_empty():
    batches = build_system_prompt_batches(
        _prompt_manager_with_stable_sections(pad="Volatile working notes."),
        language="en",
    )

    assert len(batches) == 3
    assert "Core principle." in batches[0]
    assert "No deleting files." in batches[1]
    assert "Volatile working notes." in batches[2]

    blocks = _build_system_batches_with_cache(batches)
    assert [b["cache_control"] for b in blocks[:2]] == [
        {"type": "ephemeral"},
        {"type": "ephemeral"},
    ]
    assert "Volatile working notes." in blocks[2]["text"]
    assert "cache_control" not in blocks[2]


def test_anthropic_real_prompt_batches_cache_batch1_when_pad_empty_tail_filtered():
    batches = build_system_prompt_batches(
        _prompt_manager_with_stable_sections(pad=None),
        language="en",
    )

    assert len(batches) == 3
    assert "Core principle." in batches[0]
    assert "No deleting files." in batches[1]
    assert batches[2] == ""

    blocks = _build_system_batches_with_cache(batches)
    assert [b["text"] for b in blocks] == [batches[0], batches[1]]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_single_non_empty_batch_keeps_legacy_cache_marker():
    blocks = _build_system_batches_with_cache(["stable-only", "", ""])

    assert blocks == [
        {
            "type": "text",
            "text": "stable-only",
            "cache_control": {"type": "ephemeral"},
        }
    ]
