"""Tests for lingtai.kernel.i18n."""
import json
import logging
from pathlib import Path

import lingtai.tools.i18n  # noqa: F401  (registers the tool catalog into the kernel cache)
from lingtai.kernel.i18n import register_strings, t


_KERNEL_I18N_DIR = Path(__file__).resolve().parents[1] / "src" / "lingtai" / "kernel" / "i18n"
_TOOLS_I18N_DIR = Path(__file__).resolve().parents[1] / "src" / "lingtai" / "tools" / "i18n"


class TestT:

    def test_simple_key(self):
        result = t("en", "system.current_time", time="2026-03-19T00:00:00Z", ctx="CTX")
        assert "[Current time: 2026-03-19T00:00:00Z | context: CTX]" in result

    def test_chinese_key(self):
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


class TestLiteralBraceTemplates:
    """t() must never raise on templates with literal braces or positional fields.

    Templates are translator-authored prose that may embed literal braces
    (e.g. JSON-ish examples). ``str.format_map`` mis-parses those as format
    fields, so rendering must fall back to the raw template instead of raising
    (issue #744). The shipped catalogs no longer contain such templates (tool
    descriptions moved to package glossaries), so these tests register a
    brace-bearing template directly.
    """

    def test_literal_brace_template_returns_raw_on_kwargs(self):
        register_strings("en", {"test.literal_brace": "returns {status:'disabled', enabled:false} on failure"})
        result = t("en", "test.literal_brace", foo="bar")
        assert result == "returns {status:'disabled', enabled:false} on failure"

    def test_positional_field_template_returns_raw_on_kwargs(self):
        register_strings("en", {"test.positional": "first {0} second {1}"})
        result = t("en", "test.positional", foo="bar")
        assert result == "first {0} second {1}"

    def test_attribute_access_template_returns_raw_on_kwargs(self):
        register_strings("en", {"test.attr_access": "value {a.b}"})
        result = t("en", "test.attr_access", a="x")
        assert result == "value {a.b}"

    def test_no_kwargs_path_unchanged(self):
        register_strings("en", {"test.literal_brace": "returns {status:'disabled', enabled:false} on failure"})
        result = t("en", "test.literal_brace")
        assert result == "returns {status:'disabled', enabled:false} on failure"

    def test_failed_render_logs_warning_with_key(self, caplog):
        register_strings("en", {"test.literal_brace": "returns {status:'disabled', enabled:false} on failure"})
        with caplog.at_level(logging.WARNING, logger="lingtai.kernel.i18n"):
            t("en", "test.literal_brace", foo="bar")
        assert any("test.literal_brace" in record.getMessage() for record in caplog.records)

    def test_substitution_still_works_after_hardening(self):
        result = t("en", "system.current_time", time="T", ctx="CTX")
        assert result == "[Current time: T | context: CTX]"


class TestEveryShippedTemplateRenders:
    """Every shipped template must render through the public t() with kwargs.

    Guards against translators introducing unescaped braces into any shipped
    catalog: a crash here means a template would raise inside the agent loop
    the moment a kwarg is passed for that key (issue #744).
    """

    def test_every_shipped_template_renders_with_dummy_kwargs(self):
        for directory in (_KERNEL_I18N_DIR, _TOOLS_I18N_DIR):
            for path in sorted(directory.glob("*.json")):
                table = json.loads(path.read_text(encoding="utf-8"))
                for key in table:
                    result = t(path.stem, key, dummy="x")
                    assert isinstance(result, str), (path, key, result)
