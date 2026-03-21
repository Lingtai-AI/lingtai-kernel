"""Tests for lingtai_kernel.i18n and soul prompt loading."""
from lingtai_kernel.i18n import t
from lingtai_kernel.prompt import get_soul_prompt


class TestT:

    def test_simple_key(self):
        result = t("en", "system.current_time", time="2026-03-19T00:00:00Z")
        assert "[Current time: 2026-03-19T00:00:00Z]" in result

    def test_chinese_key(self):
        result = t("zh", "system.current_time", time="2026-03-19T00:00:00Z")
        assert "2026-03-19T00:00:00Z" in result

    def test_template_substitution(self):
        result = t("en", "system.current_time", time="2026-03-19T00:00:00Z")
        assert "[Current time: 2026-03-19T00:00:00Z]" in result

    def test_chinese_template(self):
        result = t("zh", "system.current_time", time="2026-03-19T00:00:00Z")
        assert "2026-03-19T00:00:00Z" in result

    def test_unknown_lang_falls_back_to_en(self):
        result = t("xx", "system.current_time", time="now")
        assert "now" in result

    def test_unknown_key_returns_key(self):
        result = t("en", "nonexistent.key")
        assert result == "nonexistent.key"


class TestSoulPrompt:

    def test_english_soul_prompt(self):
        template = get_soul_prompt("en")
        assert "{seconds}" in template
        assert "initiative" in template

    def test_chinese_soul_prompt(self):
        template = get_soul_prompt("zh")
        assert "{seconds}" in template
        assert "主观能动性" in template

    def test_soul_prompt_format(self):
        template = get_soul_prompt("en")
        result = template.format(seconds=120)
        assert "120" in result
        assert "{" not in result

    def test_unknown_lang_falls_back_to_en(self):
        template = get_soul_prompt("xx")
        assert "initiative" in template
