"""Tests for lingtai.kernel.i18n."""
from lingtai.kernel.i18n import t


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


class TestOperationalFallbackToEnglish:
    """Operational tool-catalog keys still fall back to English.

    Model-facing schema/description keys were removed from the centralized
    i18n catalog and inlined as canonical English constants in each tool
    module (see ``src/lingtai/tools/<pkg>/__init__.py``). Only operational keys
    (result prose, preambles, runtime prompts) remain — these still use the
    ``t()`` fallback chain.
    """

    def test_zh_resolves_operational_knowledge(self):
        result = t("zh", "knowledge.preamble")
        assert result and result != "knowledge.preamble"

    def test_wen_resolves_operational_knowledge(self):
        result = t("wen", "knowledge.preamble")
        assert result and result != "knowledge.preamble"

    def test_zh_resolves_operational_skills(self):
        result = t("zh", "skills.preamble")
        assert result and result != "skills.preamble"

    def test_removed_schema_key_returns_raw_key(self):
        """Removed schema keys are no longer in the catalog."""
        assert t("en", "notification_tool.action_description") == "notification_tool.action_description"
        assert t("en", "tool.reasoning_description") == "tool.reasoning_description"
        assert t("en", "read.description") == "read.description"
