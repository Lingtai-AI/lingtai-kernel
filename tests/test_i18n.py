"""Tests for lingtai_kernel.i18n."""
import pytest

import lingtai_kernel.i18n
from lingtai_kernel.i18n import register_strings, t


class TestT:

    def test_simple_key(self):
        result = t("en", "system.current_time", time="2026-03-19T00:00:00Z", ctx="CTX")
        assert "[Current time: 2026-03-19T00:00:00Z | context: CTX]" in result

    def test_chinese_key(self):
        result = t("zh", "system.current_time", time="2026-03-19T00:00:00Z", ctx="CTX")
        assert "2026-03-19T00:00:00Z" in result

    def test_template_substitution(self):
        result = t("en", "system.current_time", time="2026-03-19T00:00:00Z", ctx="CTX")
        assert "[Current time: 2026-03-19T00:00:00Z | context: CTX]" in result

    def test_chinese_template(self):
        result = t("zh", "system.current_time", time="2026-03-19T00:00:00Z", ctx="CTX")
        assert "2026-03-19T00:00:00Z" in result

    def test_wen_key(self):
        result = t("wen", "system.current_time", time="2026-03-19T00:00:00Z", ctx="CTX")
        assert "2026-03-19T00:00:00Z" in result

    def test_unknown_lang_falls_back_to_en(self):
        result = t("xx", "system.current_time", time="now", ctx="CTX")
        assert "now" in result

    def test_unknown_key_returns_key(self):
        result = t("en", "nonexistent.key")
        assert result == "nonexistent.key"


    def test_stuck_revive_uses_err_desc_placeholder(self):
        for lang in ("en", "zh", "wen"):
            result = t(lang, "system.stuck_revive", ts="T", err_desc="ERR")
            assert "T" in result
            assert "ERR" in result
            assert "{tool_calls}" not in result
            assert "{err_desc}" not in result


class TestContextBreakdownKeys:
    def test_context_breakdown_en(self):
        result = t("en", "system.context_breakdown", pct="7.1%", sys=4720, ctx=9450)
        assert result == "7.1% (sys 4720 + ctx 9450)"

    def test_context_unknown_en(self):
        assert t("en", "system.context_unknown") == "unavailable"

    def test_current_time_en_extended(self):
        result = t("en", "system.current_time", time="T", ctx="CTX")
        assert result == "[Current time: T | context: CTX]"


class TestFallbackToEnglish:
    """Tool-schema / operating-instruction keys fall back to English."""

    def test_zh_falls_back_for_notification_tool(self):
        result = t("zh", "notification_tool.action_description")
        assert result == t("en", "notification_tool.action_description")

    def test_wen_falls_back_for_notification_tool(self):
        result = t("wen", "notification_tool.action_description")
        assert result == t("en", "notification_tool.action_description")

    def test_zh_falls_back_for_system_tool(self):
        result = t("zh", "system_tool.action_description")
        assert result == t("en", "system_tool.action_description")

    def test_wen_falls_back_for_system_tool(self):
        result = t("wen", "system_tool.action_description")
        assert result == t("en", "system_tool.action_description")

    def test_zh_falls_back_for_email_schema(self):
        result = t("zh", "email.description")
        assert result == t("en", "email.description")

    def test_wen_falls_back_for_email_schema(self):
        result = t("wen", "email.description")
        assert result == t("en", "email.description")

    def test_zh_falls_back_for_psyche_schema(self):
        result = t("zh", "psyche.object_description")
        assert result == t("en", "psyche.object_description")

    def test_wen_falls_back_for_psyche_schema(self):
        result = t("wen", "psyche.object_description")
        assert result == t("en", "psyche.object_description")

    def test_zh_falls_back_for_soul_schema(self):
        result = t("zh", "soul.action_description")
        assert result == t("en", "soul.action_description")

    def test_wen_falls_back_for_soul_schema(self):
        result = t("wen", "soul.action_description")
        assert result == t("en", "soul.action_description")

    def test_zh_falls_back_for_tool_reasoning_schema(self):
        result = t("zh", "tool.reasoning_description")
        assert result == t("en", "tool.reasoning_description")

    def test_wen_falls_back_for_tool_reasoning_schema(self):
        result = t("wen", "tool.reasoning_description")
        assert result == t("en", "tool.reasoning_description")


class TestRegisterStrings:
    """register_strings() must merge into the shipped table, not mask it."""

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        """Reset both i18n caches so ordering can be controlled per test."""
        import lingtai.i18n

        lingtai_kernel.i18n._CACHE.clear()
        lingtai.i18n._CACHE.clear()
        yield
        lingtai_kernel.i18n._CACHE.clear()
        lingtai.i18n._CACHE.clear()

    def test_register_strings_before_t_keeps_shipped_translations(self):
        register_strings("zh", {"custom.key": "自定义"})
        assert t("zh", "system.context_unknown") == "未知"
        assert t("zh", "custom.key") == "自定义"

    def test_register_strings_overrides_shipped_key(self):
        register_strings("zh", {"system.context_unknown": "OVERRIDE"})
        assert t("zh", "system.context_unknown") == "OVERRIDE"

    def test_register_strings_for_unshipped_language(self):
        register_strings("xx", {"system.context_unknown": "XX"})
        assert t("xx", "system.context_unknown") == "XX"
        # Unregistered keys still fall back to English.
        assert t("xx", "system.stuck_revive", ts="T", err_desc="E") == t(
            "en", "system.stuck_revive", ts="T", err_desc="E"
        )

    @pytest.mark.parametrize("lang", ["zh", "wen"])
    def test_lingtai_t_first_does_not_mask_kernel_translations(self, lang):
        """lingtai.i18n.t() first (via _sync_to_kernel) must not mask
        the kernel-shipped table for that language."""
        import lingtai.i18n

        lingtai.i18n.t(lang, "read.description")
        assert t(lang, "system.context_unknown") == "未知"
