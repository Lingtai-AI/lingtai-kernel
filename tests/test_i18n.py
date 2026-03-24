"""Tests for lingtai_kernel.i18n."""
from lingtai_kernel.i18n import t


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

    def test_wen_key(self):
        result = t("wen", "system.current_time", time="2026-03-19T00:00:00Z")
        assert "2026-03-19T00:00:00Z" in result

    def test_wen_template(self):
        result = t("wen", "system.current_time", time="2026-03-19T00:00:00Z")
        assert "此时" in result

    def test_unknown_lang_falls_back_to_en(self):
        result = t("xx", "system.current_time", time="now")
        assert "now" in result

    def test_unknown_key_returns_key(self):
        result = t("en", "nonexistent.key")
        assert result == "nonexistent.key"
