"""Tests for the kernel-owned tool glossary loader, grammar, and append primitive.

Verifies:
- Language normalization and allowlist enforcement.
- The single shared strict grammar (``parse_glossary``) truly rejects
  duplicate keys, malformed frontmatter, wrong types, and wrong identity.
- Runtime fail-open/warning/cache/concurrency behavior.
- All-18 canonical invariance (identical schema for every language).
- Normal + daemon rendering/collector propagation.
- Provider non-leakage (glossary never appears in wire payloads).
"""

from __future__ import annotations

import copy
import textwrap
import threading
import warnings
from types import SimpleNamespace

import pytest

import lingtai.kernel.tool_glossary as tool_glossary
from lingtai.kernel.base_agent.tools import _refresh_tool_inventory_section
from lingtai.kernel.llm.base import WIRE_TOOL_DESCRIPTION
from lingtai.kernel.tool_glossary import (
    GlossaryValidationError,
    TOOL_GLOSSARY_BODY_POLICY,
    _cache,
    _warned,
    _lock,
    append_tool_glossary,
    load_tool_glossary,
    normalize_tool_glossary_language,
    parse_glossary,
)
from lingtai.kernel.llm.base import FunctionSchema
from lingtai.tools.registry import BUILTIN_TOOLS, INTRINSICS


# ---------------------------------------------------------------------------
# Fixtures: clear module-global cache/warn-once state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the module-global cache and warn-once set before each test."""
    with _lock:
        _cache.clear()
        _warned.clear()
    yield
    with _lock:
        _cache.clear()
        _warned.clear()


# ---------------------------------------------------------------------------
# Language normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_exact_tags(self):
        assert normalize_tool_glossary_language("en") == "en"
        assert normalize_tool_glossary_language("zh") == "zh"
        assert normalize_tool_glossary_language("wen") == "wen"

    def test_casefold(self):
        assert normalize_tool_glossary_language("EN") == "en"
        assert normalize_tool_glossary_language("ZH") == "zh"
        assert normalize_tool_glossary_language("WEN") == "wen"

    def test_underscore_to_dash(self):
        assert normalize_tool_glossary_language("ZH_cn") == "zh"
        assert normalize_tool_glossary_language("en_US") == "en"

    def test_primary_subtag(self):
        assert normalize_tool_glossary_language("en-US") == "en"
        assert normalize_tool_glossary_language("wen-Hant") == "wen"
        assert normalize_tool_glossary_language("zh-CN") == "zh"

    def test_unsupported_falls_to_en(self):
        assert normalize_tool_glossary_language("fr") == "en"
        assert normalize_tool_glossary_language("ja") == "en"
        assert normalize_tool_glossary_language("de-DE") == "en"

    def test_non_string_falls_to_en(self):
        assert normalize_tool_glossary_language(None) == "en"
        assert normalize_tool_glossary_language(123) == "en"
        assert normalize_tool_glossary_language("") == "en"

    def test_no_lzh_alias(self):
        assert normalize_tool_glossary_language("lzh") == "en"


# ---------------------------------------------------------------------------
# Strict grammar — parse_glossary mutation tests
# ---------------------------------------------------------------------------


_GOOD_FM = textwrap.dedent("""\
    ---
    kind: tool-glossary
    schema_version: 1
    tool_package: lingtai.tools.test
    language: zh
    related_files:
      - docs.yaml
    maintenance: |
      Test fixture glossary for parse_glossary unit tests.
      Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
    ---
    body text""")


class TestStrictGrammar:
    def test_valid_frontmatter_returns_body(self):
        body = parse_glossary(_GOOD_FM, tool_package="lingtai.tools.test", language="zh")
        assert body.strip() == "body text"

    def test_maintenance_policy_sentence_required(self):
        text = _GOOD_FM.replace(f"  {TOOL_GLOSSARY_BODY_POLICY}\n", "")
        with pytest.raises(GlossaryValidationError, match="maintenance.*GLOSSARY.md"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_maintenance_policy_sentence_accepts_canonical_constant(self):
        assert TOOL_GLOSSARY_BODY_POLICY in _GOOD_FM
        body = parse_glossary(_GOOD_FM, tool_package="lingtai.tools.test", language="zh")
        assert body.strip() == "body text"

    def test_duplicate_key_rejected(self):
        text = _GOOD_FM.replace(
            "kind: tool-glossary\n",
            "kind: tool-glossary\nkind: other\n",
        )
        with pytest.raises(GlossaryValidationError, match="duplicate"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_missing_closing_fence_rejected(self):
        text = "---\nkind: tool-glossary\n"
        with pytest.raises(GlossaryValidationError, match="closing"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_no_opening_fence_rejected(self):
        with pytest.raises(GlossaryValidationError, match="first physical line"):
            parse_glossary("no fence here", tool_package="lingtai.tools.test", language="zh")

    @pytest.mark.parametrize("opening", ["---garbage", " ---", "--- "])
    def test_opening_fence_must_be_exact(self, opening):
        text = _GOOD_FM.replace("---\n", f"{opening}\n", 1)
        with pytest.raises(GlossaryValidationError, match="exactly"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_closing_fence_must_be_exact(self):
        text = _GOOD_FM.replace("\n---\nbody text", "\n ---\nbody text")
        with pytest.raises(GlossaryValidationError, match="closing"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_unhashable_mapping_key_is_validation_error(self):
        text = _GOOD_FM.replace(
            "kind: tool-glossary",
            "? [not, hashable]\n: value\nkind: tool-glossary",
        )
        with pytest.raises(GlossaryValidationError, match="not hashable"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_wrong_kind_rejected(self):
        text = _GOOD_FM.replace("tool-glossary", "wrong-kind")
        with pytest.raises(GlossaryValidationError, match="kind"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_wrong_schema_version_type_rejected(self):
        text = _GOOD_FM.replace("schema_version: 1", 'schema_version: "1"')
        with pytest.raises(GlossaryValidationError, match="schema_version"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_bool_schema_version_rejected(self):
        text = _GOOD_FM.replace("schema_version: 1", "schema_version: true")
        with pytest.raises(GlossaryValidationError, match="schema_version"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_wrong_package_identity_rejected(self):
        with pytest.raises(GlossaryValidationError, match="tool_package"):
            parse_glossary(_GOOD_FM, tool_package="lingtai.tools.other", language="zh")

    def test_wrong_language_identity_rejected(self):
        with pytest.raises(GlossaryValidationError, match="language"):
            parse_glossary(_GOOD_FM, tool_package="lingtai.tools.test", language="wen")

    def test_extra_field_rejected(self):
        text = _GOOD_FM.replace(
            "language: zh\n",
            "language: zh\nextra: field\n",
        )
        with pytest.raises(GlossaryValidationError, match="unknown"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_missing_field_rejected(self):
        text = _GOOD_FM.replace("language: zh\n", "")
        with pytest.raises(GlossaryValidationError, match="missing"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_non_mapping_frontmatter_rejected(self):
        text = "---\n- a list\n- not a map\n---\nbody"
        with pytest.raises(GlossaryValidationError, match="mapping"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")


# ---------------------------------------------------------------------------
# Runtime loader — fail-open, fallback, warning, caching
# ---------------------------------------------------------------------------


class TestLoadToolGlossary:
    def test_english_body_is_empty(self):
        body = load_tool_glossary("lingtai.tools.read", "en")
        assert body == ""

    def test_chinese_body_is_non_empty(self):
        body = load_tool_glossary("lingtai.tools.read", "zh")
        assert body.strip()
        assert "read" in body

    def test_wen_body_is_non_empty(self):
        body = load_tool_glossary("lingtai.tools.read", "wen")
        assert body.strip()

    def test_wen_uses_classical_chinese_not_zh(self):
        """wen must use classical Chinese vocabulary, not be identical to zh."""
        zh = load_tool_glossary("lingtai.tools.read", "zh")
        wen = load_tool_glossary("lingtai.tools.read", "wen")
        assert zh != wen, "wen body must not be identical to zh body"

    def test_frontmatter_never_in_body(self):
        for lang in ("zh", "wen"):
            body = load_tool_glossary("lingtai.tools.read", lang)
            assert "---" not in body
            assert "kind:" not in body
            assert "tool_package:" not in body
            assert "schema_version:" not in body

    def test_unknown_package_returns_empty(self):
        with pytest.warns(UserWarning, match="ModuleNotFoundError") as caught:
            body = load_tool_glossary("lingtai.tools.nonexistent", "zh")
        assert body == ""
        assert len(caught) == 2  # selected zh plus English fallback

    def test_none_package_returns_empty(self):
        body = load_tool_glossary("", "zh")
        assert body == ""

    def test_non_string_package_returns_empty(self):
        body = load_tool_glossary(None, "zh")
        assert body == ""

    def test_missing_file_falls_back_to_english(self):
        """A language with no resource falls back to English (empty)."""
        body = load_tool_glossary("lingtai.tools.read", "fr")
        assert body == ""  # fr normalizes to en -> empty English body

    def test_unimportable_package_warns_exactly_once_per_language(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(5):
                assert load_tool_glossary("lingtai.tools.nonexistent_pkg", "zh") == ""
        glossary_warnings = [
            str(item.message)
            for item in caught
            if "tool glossary" in str(item.message).lower()
        ]
        assert len(glossary_warnings) == 2  # selected zh + English fallback
        assert all("ModuleNotFoundError" in message for message in glossary_warnings)
        assert any("glossary-zh.md" in message for message in glossary_warnings)
        assert any("glossary-en.md" in message for message in glossary_warnings)

    @pytest.mark.parametrize(
        "exc_factory, marker",
        [
            (lambda: FileNotFoundError("resource missing"), "missing-resource"),
            (lambda: ImportError("import failed"), "ImportError"),
            (lambda: OSError("resource I/O failed"), "OSError"),
            (lambda: TypeError("bad traversable"), "TypeError"),
            (
                lambda: UnicodeDecodeError(
                    "utf-8", b"\\xff", 0, 1, "invalid start byte"
                ),
                "invalid start byte",
            ),
        ],
    )
    def test_resource_errors_fail_open_warn_once_and_cache(
        self, monkeypatch, exc_factory, marker
    ):
        calls = 0

        def fail_files(_package):
            nonlocal calls
            calls += 1
            raise exc_factory()

        monkeypatch.setattr(tool_glossary.importlib_resources, "files", fail_files)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(6):
                assert load_tool_glossary("lingtai.tools.synthetic_failure", "zh") == ""

        messages = [str(item.message) for item in caught]
        assert calls == 2  # zh plus English fallback, each read once
        assert len(messages) == 2
        assert all(marker in message for message in messages)
        assert ("lingtai.tools.synthetic_failure", "zh") in _cache
        assert ("lingtai.tools.synthetic_failure", "en") in _cache


# ---------------------------------------------------------------------------
# Cache and concurrency
# ---------------------------------------------------------------------------


class TestCaching:
    def test_results_are_cached(self):
        b1 = load_tool_glossary("lingtai.tools.read", "zh")
        b2 = load_tool_glossary("lingtai.tools.read", "zh")
        assert b1 == b2
        assert b1  # non-empty

    def test_cache_hit_does_not_reload(self):
        """After first load, the cache key must be present."""
        load_tool_glossary("lingtai.tools.bash", "wen")
        assert ("lingtai.tools.bash", "wen") in _cache

    def test_concurrent_success_reads_resource_once(self, monkeypatch):
        """A simultaneous cache miss performs one package-resource read."""
        original = tool_glossary._read_resource
        calls = 0
        calls_lock = threading.Lock()
        start = threading.Barrier(9)
        results: list[str] = []
        errors: list[BaseException] = []

        def counted_read(package, language):
            nonlocal calls
            with calls_lock:
                calls += 1
            return original(package, language)

        monkeypatch.setattr(tool_glossary, "_read_resource", counted_read)

        def worker():
            try:
                start.wait()
                results.append(load_tool_glossary("lingtai.tools.read", "zh"))
            except BaseException as exc:  # surface thread assertion/barrier failures
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join()

        assert not errors
        assert len(results) == 8 and all(result.strip() for result in results)
        assert calls == 1

    def test_concurrent_failure_warns_and_reads_once_per_language(self, monkeypatch):
        calls = 0
        calls_lock = threading.Lock()
        start = threading.Barrier(9)
        results: list[str] = []

        def missing(_package):
            nonlocal calls
            with calls_lock:
                calls += 1
            raise FileNotFoundError("synthetic missing resource")

        monkeypatch.setattr(tool_glossary.importlib_resources, "files", missing)

        def worker():
            start.wait()
            results.append(load_tool_glossary("lingtai.tools.concurrent_missing", "zh"))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join()

        assert results == [""] * 8
        assert calls == 2  # zh plus English fallback, not 8×2
        assert len(caught) == 2
        assert {key[1] for key in _warned} == {"zh", "en"}


# ---------------------------------------------------------------------------
# Append primitive
# ---------------------------------------------------------------------------


class TestAppendToolGlossary:
    def test_english_no_append(self):
        result = append_tool_glossary(
            "Base description.", tool_package="lingtai.tools.read", language="en"
        )
        assert result == "Base description."

    def test_chinese_appends_body(self):
        result = append_tool_glossary(
            "Base description.", tool_package="lingtai.tools.read", language="zh"
        )
        assert "Base description." in result
        assert result.startswith("Base description.\n\n")

    def test_none_package_no_append(self):
        result = append_tool_glossary("Base.", tool_package=None, language="zh")
        assert result == "Base."

    def test_empty_package_no_append(self):
        result = append_tool_glossary("Base.", tool_package="", language="zh")
        assert result == "Base."

    def test_rstrip_base(self):
        result = append_tool_glossary("Base.  \n\n", tool_package=None, language="en")
        assert result == "Base."

    def test_fail_open_for_bad_package(self):
        with pytest.warns(UserWarning, match="ModuleNotFoundError") as caught:
            result = append_tool_glossary(
                "Base.", tool_package="lingtai.tools.does_not_exist", language="zh"
            )
        assert result == "Base."
        assert len(caught) == 2


# ---------------------------------------------------------------------------
# All-18 canonical invariance
# ---------------------------------------------------------------------------


_ALL_PACKAGES = sorted(
    {path.rsplit(".", 1)[-1] for path in BUILTIN_TOOLS.values()} | set(INTRINSICS)
)
assert len(_ALL_PACKAGES) == 18


class TestAllEighteenInvariance:
    @pytest.mark.parametrize("pkg", _ALL_PACKAGES)
    def test_english_body_empty(self, pkg):
        assert load_tool_glossary(f"lingtai.tools.{pkg}", "en") == ""

    @pytest.mark.parametrize("pkg", _ALL_PACKAGES)
    def test_zh_body_non_empty(self, pkg):
        assert load_tool_glossary(f"lingtai.tools.{pkg}", "zh").strip()

    @pytest.mark.parametrize("pkg", _ALL_PACKAGES)
    def test_wen_body_non_empty(self, pkg):
        assert load_tool_glossary(f"lingtai.tools.{pkg}", "wen").strip()

    @pytest.mark.parametrize("pkg", _ALL_PACKAGES)
    def test_zh_and_wen_differ(self, pkg):
        """zh and wen must be distinct — wen is not just traditionalized zh."""
        zh = load_tool_glossary(f"lingtai.tools.{pkg}", "zh")
        wen = load_tool_glossary(f"lingtai.tools.{pkg}", "wen")
        assert zh != wen, f"{pkg}: zh and wen bodies are identical"

    @pytest.mark.parametrize("pkg", _ALL_PACKAGES)
    def test_frontmatter_stripped(self, pkg):
        for lang in ("zh", "wen"):
            body = load_tool_glossary(f"lingtai.tools.{pkg}", lang)
            assert "kind:" not in body
            assert "schema_version:" not in body


# ---------------------------------------------------------------------------
# Schema invariance — glossary never affects schema/dispatch/provider
# ---------------------------------------------------------------------------


class TestSchemaInvariance:
    def test_function_schema_glossary_package_not_serialized(self):
        schema = FunctionSchema(
            name="test",
            description="desc",
            parameters={},
            system_prompt="",
            glossary_package="lingtai.tools.test",
        )
        d = schema.to_dict()
        assert "glossary_package" not in d
        assert "system_prompt" not in d
        assert set(d.keys()) == {"name", "description", "parameters"}

    def test_default_glossary_package_is_none(self):
        schema = FunctionSchema(name="t", description="d", parameters={})
        assert schema.glossary_package is None

    def test_glossary_does_not_affect_identifiers(self):
        """Glossary lookup cannot change names, properties, enums, required."""
        from lingtai.tools.read import get_schema

        base = get_schema()
        props = base["properties"]
        assert "file_path" in props
        assert "offset" in props
        assert "limit" in props
        assert base["required"] == ["file_path"]

    def test_normal_and_daemon_resident_tools_render_selected_glossary(self):
        zh_body = load_tool_glossary("lingtai.tools.read", "zh")
        schema = FunctionSchema(
            name="read",
            description="Read text files.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute file path.",
                    }
                },
                "required": ["file_path"],
            },
            glossary_package="lingtai.tools.read",
        )
        sections: dict[str, str] = {}
        agent = SimpleNamespace(
            _config=SimpleNamespace(language="zh"),
            _intrinsics={},
            _intrinsic_modules={},
            _tool_schemas=[schema],
            _prompt_manager=SimpleNamespace(
                write_section=lambda name, text, protected=False: sections.__setitem__(
                    name, text
                )
            ),
        )

        _refresh_tool_inventory_section(agent)
        assert sections["tools"] == f"### read\nRead text files.\n\n{zh_body}"

        from lingtai.tools.daemon import DaemonManager

        daemon_prompt = DaemonManager._build_emanation_prompt(
            SimpleNamespace(_agent=agent), "Inspect one file", [schema]
        )
        assert "Read text files." in daemon_prompt
        assert zh_body in daemon_prompt
        assert WIRE_TOOL_DESCRIPTION not in daemon_prompt

    def test_daemon_intrinsic_collector_preserves_glossary_owner(self):
        import lingtai.tools.email as email_tool
        from lingtai.tools.daemon import DaemonManager

        manager = DaemonManager.__new__(DaemonManager)
        manager._agent = SimpleNamespace(
            _intrinsics={"email": object()},
            _intrinsic_modules={"email": email_tool},
        )
        schemas, _handlers = manager._daemon_intrinsic_surface()
        assert schemas["email"].glossary_package == "lingtai.tools.email"
        assert schemas["email"].description == email_tool.get_description()

    def test_glossary_metadata_and_body_never_reach_provider_wire(self):
        from lingtai.llm.anthropic.adapter import _build_tools as build_anthropic_tools
        from lingtai.llm.openai.adapter import (
            _build_responses_tools,
            _build_tools as build_openai_tools,
        )

        body = load_tool_glossary("lingtai.tools.read", "zh")
        parameters = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute file path.",
                }
            },
            "required": ["file_path"],
        }
        schema = FunctionSchema(
            name="read",
            description="Read text files.",
            parameters=copy.deepcopy(parameters),
            glossary_package="lingtai.tools.read",
        )

        openai_chat = build_openai_tools([schema])[0]["function"]
        openai_responses = _build_responses_tools([schema])[0]
        anthropic = build_anthropic_tools([schema], cache_tools=False)[0]
        for payload in (openai_chat, openai_responses, anthropic):
            assert payload["description"] == WIRE_TOOL_DESCRIPTION
            assert body not in repr(payload)
            assert "glossary_package" not in payload
        assert openai_chat["parameters"] == parameters
        assert openai_responses["parameters"] == parameters
        assert anthropic["input_schema"] == parameters
        assert schema.parameters == parameters
        assert schema.glossary_package == "lingtai.tools.read"


# ---------------------------------------------------------------------------
# Source-tree validation
# ---------------------------------------------------------------------------


class TestSourceValidation:
    def test_root_glossary_owns_policy_and_templates(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        root_glossary = repo_root / "GLOSSARY.md"
        text = root_glossary.read_text(encoding="utf-8")
        compact_text = " ".join(text.split())
        assert root_glossary.is_file()
        assert TOOL_GLOSSARY_BODY_POLICY in text
        for phrase in (
            "Glossary of Glossaries",
            "not human UI",
            "not schema",
            "not a manual",
            "not a Contract",
            "not Anatomy",
            "glossary-en.md",
            "glossary-zh.md",
            "glossary-wen.md",
            "English",
            "Simplified Chinese",
            "Classical Chinese",
            "Review checklist",
            "next glossary-governance PR",
        ):
            assert phrase in compact_text

    def test_validator_passes(self):
        """The validator module should pass on the current source tree."""
        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "lingtai.tools.glossary_validator",
                "--check",
            ],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(repo_root / "src"), "PATH": ""},
        )
        assert result.returncode == 0, f"Validator failed:\n{result.stderr}"
        assert "54" in result.stdout

    def test_no_repo_root_tools_directory(self):
        """Root tools/ directory must not exist (anatomy hygiene)."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        assert not (repo_root / "tools").is_dir(), "stale repo-root tools/"

    @pytest.mark.parametrize(
        "mutation, expected",
        [
            ("missing", "missing glossary-zh.md"),
            ("extra", "unsupported glossary language: fr"),
            ("duplicate", "duplicate frontmatter key"),
            ("wrong_type", "schema_version"),
            ("wrong_package", "tool_package"),
            ("wrong_language", "language"),
            ("extra_field", "unknown fields"),
        ],
    )
    def test_validator_resource_mutations(self, monkeypatch, mutation, expected):
        import lingtai.tools.glossary_validator as validator

        def text_for(lang):
            body = "" if lang == "en" else "localized body"
            return textwrap.dedent(
                f"""\
                ---
                kind: tool-glossary
                schema_version: 1
                tool_package: lingtai.tools.fake
                language: {lang}
                related_files:
                  - docs.yaml
                maintenance: Fake fixture glossary for validator mutation tests.
                ---
                {body}"""
            )

        files = {f"glossary-{lang}.md": text_for(lang) for lang in ("en", "zh", "wen")}
        if mutation == "missing":
            files.pop("glossary-zh.md")
        elif mutation == "extra":
            files["glossary-fr.md"] = text_for("zh").replace(
                "language: zh", "language: fr"
            )
        elif mutation == "duplicate":
            files["glossary-zh.md"] = files["glossary-zh.md"].replace(
                "kind: tool-glossary\n", "kind: tool-glossary\nkind: duplicate\n"
            )
        elif mutation == "wrong_type":
            files["glossary-zh.md"] = files["glossary-zh.md"].replace(
                "schema_version: 1", 'schema_version: "1"'
            )
        elif mutation == "wrong_package":
            files["glossary-zh.md"] = files["glossary-zh.md"].replace(
                "tool_package: lingtai.tools.fake", "tool_package: lingtai.tools.other"
            )
        elif mutation == "wrong_language":
            files["glossary-zh.md"] = files["glossary-zh.md"].replace(
                "language: zh", "language: wen"
            )
        elif mutation == "extra_field":
            files["glossary-zh.md"] = files["glossary-zh.md"].replace(
                "language: zh\n", "language: zh\nextra: field\n"
            )

        class FakeResource:
            def __init__(self, name, text):
                self.name = name
                self._text = text

            def is_file(self):
                return True

            def read_text(self, encoding="utf-8"):
                assert encoding == "utf-8"
                return self._text

        root = SimpleNamespace(
            iterdir=lambda: [FakeResource(name, text) for name, text in files.items()]
        )
        monkeypatch.setattr(validator.importlib_resources, "files", lambda _pkg: root)
        errors = validator.validate_package("fake")
        assert any(expected in error for error in errors), errors


# ---------------------------------------------------------------------------
# related_files / maintenance — docs-governance fields (V5)
# ---------------------------------------------------------------------------


class TestDocsGovernanceFields:
    def test_missing_related_files_rejected(self):
        text = _GOOD_FM.replace("related_files:\n  - docs.yaml\n", "")
        with pytest.raises(GlossaryValidationError, match="missing"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_empty_related_files_rejected(self):
        text = _GOOD_FM.replace(
            "related_files:\n  - docs.yaml\n", "related_files: []\n"
        )
        with pytest.raises(GlossaryValidationError, match="related_files"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_missing_maintenance_rejected(self):
        text = _GOOD_FM.replace(
            "maintenance: |\n"
            "  Test fixture glossary for parse_glossary unit tests.\n"
            f"  {TOOL_GLOSSARY_BODY_POLICY}\n",
            "",
        )
        with pytest.raises(GlossaryValidationError, match="missing"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_empty_maintenance_rejected(self):
        text = _GOOD_FM.replace(
            "maintenance: |\n"
            "  Test fixture glossary for parse_glossary unit tests.\n"
            f"  {TOOL_GLOSSARY_BODY_POLICY}\n",
            "maintenance: \"\"\n",
        )
        with pytest.raises(GlossaryValidationError, match="maintenance"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_extra_field_beyond_six_still_rejected(self):
        text = _GOOD_FM.replace(
            "maintenance: |\n"
            "  Test fixture glossary for parse_glossary unit tests.\n"
            f"  {TOOL_GLOSSARY_BODY_POLICY}\n",
            "maintenance: |\n"
            "  Test fixture glossary for parse_glossary unit tests.\n"
            f"  {TOOL_GLOSSARY_BODY_POLICY}\n"
            "extra: field\n",
        )
        with pytest.raises(GlossaryValidationError, match="unknown"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")

    def test_non_string_related_files_item_rejected_not_crashed(self):
        text = _GOOD_FM.replace(
            "related_files:\n  - docs.yaml\n",
            "related_files:\n  - docs.yaml\n  - [nested, list]\n",
        )
        with pytest.raises(GlossaryValidationError, match="related_files"):
            parse_glossary(text, tool_package="lingtai.tools.test", language="zh")
