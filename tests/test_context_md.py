"""Tests for context.md system prompt cache overhaul."""
import pytest
from lingtai_kernel.prompt import SystemPromptManager


class TestContextSection:
    def test_context_is_last_section(self):
        pm = SystemPromptManager()
        pm.write_section("pad", "my notes")
        pm.write_section("context", "### user [2026-04-20T10:00:00Z]\nhello")
        rendered = pm.render()
        pad_pos = rendered.index("## pad")
        context_pos = rendered.index("## context")
        assert context_pos > pad_pos

    def test_context_empty_not_rendered(self):
        pm = SystemPromptManager()
        pm.write_section("pad", "my notes")
        rendered = pm.render()
        assert "## context" not in rendered

    def test_context_deleted_disappears(self):
        pm = SystemPromptManager()
        pm.write_section("context", "some content")
        pm.delete_section("context")
        rendered = pm.render()
        assert "## context" not in rendered
