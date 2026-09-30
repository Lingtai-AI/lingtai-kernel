import json
import pytest
from lingtai.init_schema import _check_type, validate_init


def _valid_init() -> dict:
    """Return a minimal valid init.json dict."""
    return {
        "manifest": {
            "agent_name": "alice",
            "language": "en",
            "llm": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "api_key": None,
                "base_url": None,
            },
            "capabilities": {},
            "soul": {"delay": 120},
            "stamina": 3600,
            "max_turns": 50,
            "admin": {"karma": True},
        },
        "pad": "",
        "lingtai": "",
        "soul": "",
    }


def test_valid_init_passes():
    validate_init(_valid_init())  # should not raise


def test_missing_top_level_key():
    data = _valid_init()
    del data["pad"]
    with pytest.raises(ValueError, match="pad"):
        validate_init(data)


def test_missing_manifest_field():
    """Only manifest.llm is truly required — other fields are optional."""
    data = _valid_init()
    del data["manifest"]["llm"]
    with pytest.raises(ValueError, match="manifest.llm"):
        validate_init(data)


def test_minimal_init_passes():
    """Bare-minimum init.json: only manifest.llm with provider+model."""
    data = {
        "manifest": {
            "llm": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
            },
        },
        "pad": "",
        "lingtai": "",
        "soul": "",
    }
    validate_init(data)  # should not raise


def test_missing_llm_field():
    data = _valid_init()
    del data["manifest"]["llm"]["provider"]
    with pytest.raises(ValueError, match="manifest.llm.provider"):
        validate_init(data)


@pytest.mark.parametrize(
    "key",
    [
        "base_prompt", "base_prompt_file",
        "covenant", "covenant_file",
        "comment", "comment_file",
    ],
)
def test_legacy_psyche_prompt_inputs_are_known_but_inert(key):
    data = _valid_init()
    data[key] = {"not": "type-checked or honored"}

    warnings = validate_init(data)

    assert all(key not in warning for warning in warnings)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("one hour", id="wrong-type"),
        pytest.param(True, id="bool"),
    ],
)
def test_legacy_manifest_stamina_wrong_type_is_ignored(value):
    data = _valid_init()
    data["manifest"]["stamina"] = value
    warnings = validate_init(data)  # legacy ignored field
    assert all("stamina" not in warning for warning in warnings)


def test_summarize_notification_threshold_rejects_negative():
    data = _valid_init()
    data["manifest"]["summarize_notification_threshold"] = -1
    with pytest.raises(ValueError, match="summarize_notification_threshold"):
        validate_init(data)


def test_summarize_notification_threshold_rejects_bool():
    data = _valid_init()
    data["manifest"]["summarize_notification_threshold"] = True
    with pytest.raises(ValueError, match="summarize_notification_threshold.*bool"):
        validate_init(data)


def test_summarize_notification_threshold_allows_zero():
    data = _valid_init()
    data["manifest"]["summarize_notification_threshold"] = 0
    validate_init(data)  # 0 intentionally disables large-result notifications.


@pytest.mark.parametrize("legacy_value", [500_000, 0, -5, True, "500000", None])
def test_cache_miss_budget_is_schema_unknown_legacy_data(legacy_value):
    data = _valid_init()
    data["manifest"]["cache_miss_budget"] = legacy_value

    warnings = validate_init(data)

    assert "unknown field: manifest.cache_miss_budget" in warnings


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_limit", "not-an-int"), ("max_rpm", object()),
        ("streaming", "yes"), ("aed_timeout", -1),
        ("max_aed_attempts", 0), ("snapshot_interval", False),
        ("activeness", 3),
    ],
)
def test_ordinary_runtime_manifest_fields_are_legacy_ignored(field, value):
    data = _valid_init()
    data["manifest"][field] = value
    warnings = validate_init(data)
    assert not any(field in warning for warning in warnings)


@pytest.mark.parametrize("value", [[], ["shell"], ["", "plugin", "web"]])
def test_manifest_disable_accepts_only_string_lists(value):
    data = _valid_init()
    data["manifest"]["disable"] = value

    assert validate_init(data) == []
    assert data["manifest"]["disable"] is value


@pytest.mark.parametrize("value", [None, "shell", {"shell": True}, ("shell",)])
def test_manifest_disable_rejects_non_lists(value):
    data = _valid_init()
    data["manifest"]["disable"] = value

    with pytest.raises(ValueError, match=r"^manifest\.disable: expected list"):
        validate_init(data)


@pytest.mark.parametrize(
    "value,index,type_name",
    [
        (["shell", 1], 1, "int"),
        ([False], 0, "bool"),
        (["web", None], 1, "NoneType"),
        ([["nested"]], 0, "list"),
    ],
)
def test_manifest_disable_rejects_every_non_string_entry(value, index, type_name):
    data = _valid_init()
    data["manifest"]["disable"] = value

    with pytest.raises(
        ValueError,
        match=rf"^manifest\.disable\[{index}\]: expected str, got {type_name}$",
    ):
        validate_init(data)


def test_int_field_rejects_bool_via_check_type():
    data = _valid_init()
    data["manifest"]["summarize_notification_threshold"] = True
    with pytest.raises(
        ValueError, match=r"manifest\.summarize_notification_threshold.*bool"
    ):
        validate_init(data)


def test_retired_llm_compact_threshold_is_ignored_without_warning():
    data = _valid_init()
    data["manifest"]["llm"]["compact_threshold"] = True
    warnings = validate_init(data)
    assert not any("compact_threshold" in w for w in warnings)


def test_retired_llm_codex_auth_pool_path_is_ignored_without_warning():
    """The in-kernel Codex pool was removed; a stale ``codex_auth_pool_path``
    on a single-account ``codex`` block is recognized-and-ignored, silently."""
    from lingtai.init_schema import LLM_LEGACY_IGNORED, LLM_PASS_THROUGH_KNOWN

    assert "codex_auth_pool_path" in LLM_LEGACY_IGNORED
    assert "codex_auth_pool_path" not in LLM_PASS_THROUGH_KNOWN

    data = _valid_init()
    data["manifest"]["llm"]["provider"] = "codex"
    data["manifest"]["llm"]["model"] = "gpt-5.5"
    data["manifest"]["llm"]["codex_auth_pool_path"] = "/tokens/codex-pool.json"

    assert validate_init(data) == []


def test_check_type_bool_allowed_when_listed():
    # Escape hatch: a field that explicitly accepts bool | int is not rejected.
    _check_type(True, (int, bool), "x")  # should not raise
    _check_type(3, (int, bool), "x")  # should not raise


def test_max_turns_is_legacy_ignored():
    """max_turns is recognized-and-ignored: build_agent_config deliberately
    ignores it (tool-loop safety is kernel-owned), so the schema must not
    advertise it as a live typed knob (issue #736)."""
    from lingtai.init_schema import MANIFEST_LEGACY_IGNORED, MANIFEST_OPTIONAL

    assert "max_turns" in MANIFEST_LEGACY_IGNORED
    assert "max_turns" not in MANIFEST_OPTIONAL

    data = _valid_init()  # already contains max_turns: 50
    warnings = validate_init(data)
    assert not any("max_turns" in w for w in warnings)

    # A stale non-int value is tolerated — nothing reads it.
    data["manifest"]["max_turns"] = "999"
    validate_init(data)


def test_wrong_type_capabilities():
    data = _valid_init()
    data["manifest"]["capabilities"] = ["shell", "bash"]
    with pytest.raises(ValueError, match="manifest.capabilities.*object"):
        validate_init(data)


# --- optional fields ---


def test_env_file_optional():
    data = _valid_init()
    validate_init(data)  # no env_file — should pass
    data["env_file"] = "~/.lingtai/.env"
    validate_init(data)  # with env_file — should pass


def test_env_file_wrong_type():
    data = _valid_init()
    data["env_file"] = 123
    with pytest.raises(ValueError, match="env_file.*str"):
        validate_init(data)


def test_api_key_env_optional():
    data = _valid_init()
    data["manifest"]["llm"]["api_key_env"] = "MY_KEY"
    data["env_file"] = ".env"  # required when api_key_env is used without api_key
    validate_init(data)


def test_api_key_env_wrong_type():
    data = _valid_init()
    data["manifest"]["llm"]["api_key_env"] = 123
    with pytest.raises(ValueError, match="api_key_env.*str"):
        validate_init(data)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_key_evn", "ANTHROPIC_API_KEY"),
        ("base-url", "https://example.test/v1"),
        ("context_limit", 200_000),
        ("max_rpm", 60),
    ],
)
def test_unknown_manifest_llm_fields_warn(key, value):
    data = _valid_init()
    data["manifest"]["llm"][key] = value

    warnings = validate_init(data)

    assert f"unknown field in manifest.llm: {key}" in warnings


def test_legacy_manifest_soul_block_is_tolerated_without_warning():
    """``manifest.soul`` belongs to the removed Soul subsystem: any shape an
    older agent wrote still validates, silently, and is never type-checked."""
    data = _valid_init()
    data["manifest"]["soul"]["voice_promt"] = "speak plainly"
    data["manifest"]["soul"]["delay"] = "not-a-number"

    assert validate_init(data) == []

    data["manifest"]["soul"] = "inner"
    assert validate_init(data) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("default_headers", {"x-test": "1"}),
        ("codex_session_anchor", "/agents/alice/init.json"),
        ("codex_thread_salt", "alice"),
        ("codex_auth_path", "/tokens/alice/codex-auth.json"),
        ("codex_base_urls", ["https://codex-a.example.test/v1"]),
        ("service_tier", "fast"),
    ],
)
def test_known_manifest_llm_pass_through_fields_do_not_warn(key, value):
    data = _valid_init()
    data["manifest"]["llm"][key] = value

    warnings = validate_init(data)

    assert all(f"unknown field in manifest.llm: {key}" != w for w in warnings)


# --- Retired per-vendor routing keys: recognized-and-ignored ---------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_compat", "anthropic"),
        ("api_compat", {"nested": float("nan")}),
        ("reasoning_effort_vocab", "seven_tier"),
        ("reasoning_effort_vocab", 7),
        ("use_responses_api", True),
        ("use_responses_api", "yes"),
    ],
)
def test_retired_llm_routing_keys_are_ignored_without_warning(key, value):
    """``api_compat``, ``reasoning_effort_vocab`` and ``use_responses_api`` are
    legacy keys: known (no warning), never type-checked, never forwarded."""
    from lingtai.init_schema import LLM_LEGACY_IGNORED, LLM_OPTIONAL, LLM_PASS_THROUGH_KNOWN
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    assert key in LLM_LEGACY_IGNORED
    assert key not in LLM_OPTIONAL
    assert key not in LLM_PASS_THROUGH_KNOWN

    data = _valid_init()
    data["manifest"]["llm"][key] = value
    assert validate_init(data) == []
    assert build_provider_defaults_from_manifest_llm(
        {"provider": "openai", key: value}, max_rpm=0
    ) is None


# --- Standard ``thinking``: one vocabulary for all four families -----------

_THINKING_LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
_FAMILY_PATCHES = [
    {"provider": "openai"},
    {"provider": "openai", "wire_api": "responses"},
    {"provider": "openai", "base_url": "https://compat.example/v1", "wire_api": "chat_completions"},
    {"provider": "anthropic"},
    {"provider": "anthropic", "base_url": "https://compat.example"},
    {"provider": "codex", "model": "gpt-5.5"},
    {"provider": "claude-code", "model": "sonnet"},
]


@pytest.mark.parametrize("llm_patch", _FAMILY_PATCHES)
@pytest.mark.parametrize("value", _THINKING_LEVELS)
def test_llm_thinking_valid_for_every_family(llm_patch, value):
    data = _valid_init()
    data["manifest"]["llm"].update(llm_patch)
    data["manifest"]["llm"]["thinking"] = value
    validate_init(data)


@pytest.mark.parametrize("llm_patch", _FAMILY_PATCHES)
@pytest.mark.parametrize("value", ["default", "ultra", 1, None])
def test_llm_thinking_invalid_values_for_every_family(llm_patch, value):
    data = _valid_init()
    data["manifest"]["llm"].update(llm_patch)
    data["manifest"]["llm"]["thinking"] = value
    with pytest.raises(ValueError, match="manifest.llm.thinking"):
        validate_init(data)


def test_thinking_scope_tables_are_gone():
    """No per-vendor thinking lists remain in the kernel config."""
    import lingtai.kernel.config as config

    for name in (
        "THINKING_PROVIDERS",
        "THINKING_NATIVE_PROVIDERS",
        "THINKING_OWNED_PROVIDERS",
        "llm_supports_thinking",
    ):
        assert not hasattr(config, name), name


# --- Standard ``service_tier``: one normalizer for openai and codex ---------


@pytest.mark.parametrize("provider", ["openai", "codex", "anthropic", "claude-code"])
@pytest.mark.parametrize("value", ["fast", "auto", "default", "flex", "priority", " fast "])
def test_llm_service_tier_standard_values_validate(provider, value):
    data = _valid_init()
    data["manifest"]["llm"]["provider"] = provider
    data["manifest"]["llm"]["service_tier"] = value
    assert validate_init(data) == []


@pytest.mark.parametrize("provider", ["openai", "codex"])
@pytest.mark.parametrize("value", ["turbo", "scale", "Fast", "unsupported"])
def test_llm_service_tier_invalid_value_fails_validation(provider, value):
    data = _valid_init()
    data["manifest"]["llm"]["provider"] = provider
    data["manifest"]["llm"]["service_tier"] = value
    with pytest.raises(ValueError, match=r"^manifest\.llm\.service_tier: "):
        validate_init(data)


def test_llm_service_tier_wrong_type_fails_validation():
    data = _valid_init()
    data["manifest"]["llm"]["service_tier"] = 3
    with pytest.raises(ValueError, match="manifest.llm.service_tier"):
        validate_init(data)


# --- ``wire_api`` belongs to ``openai`` only --------------------------------


@pytest.mark.parametrize("value", ["auto", "chat_completions", "responses"])
def test_llm_wire_api_values_accepted_for_openai(value):
    data = _valid_init()
    data["manifest"]["llm"].update({"provider": "openai", "wire_api": value})
    validate_init(data)


@pytest.mark.parametrize("provider", ["anthropic", "codex", "claude-code"])
def test_llm_explicit_wire_api_rejected_for_non_openai(provider):
    data = _valid_init()
    data["manifest"]["llm"].update({"provider": provider, "wire_api": "responses"})
    with pytest.raises(ValueError, match="only for provider openai"):
        validate_init(data)


@pytest.mark.parametrize("provider", ["anthropic", "codex", "claude-code"])
def test_llm_legacy_auto_wire_api_is_inert_for_every_family(provider):
    data = _valid_init()
    data["manifest"]["llm"].update({"provider": provider, "wire_api": "auto"})
    validate_init(data)


# --- Removed LLM providers fail loudly with a replacement pointer -----------

_REMOVED = [
    "deepseek", "zhipu", "glm", "mimo", "minimax", "openrouter", "grok",
    "qwen", "kimi", "gemini", "kimi-code", "kimi_code", "custom",
    "claude_code", "codex-pool", "codex_pool",
]


def test_removed_llm_provider_set_is_exact():
    from lingtai.init_schema import REMOVED_LLM_PROVIDERS

    assert REMOVED_LLM_PROVIDERS == frozenset(_REMOVED)


@pytest.mark.parametrize("provider", _REMOVED + ["DeepSeek", "Gemini", " custom ", "Codex-Pool"])
def test_llm_removed_provider_raises_with_replacement_pointer(provider):
    data = _valid_init()
    data["manifest"]["llm"]["provider"] = provider
    data["manifest"]["llm"]["model"] = "some-model"

    with pytest.raises(ValueError) as excinfo:
        validate_init(data)

    message = str(excinfo.value)
    assert message.startswith(
        f"manifest.llm.provider: provider {provider!r} was removed from LingTai;"
    )
    assert "provider openai (OpenAI-compatible: base_url + wire_api responses|chat_completions)" in message
    assert "anthropic (Anthropic-compatible: base_url)" in message
    assert "sub2api/subs-pool" in message


@pytest.mark.parametrize("provider", ["codex-pool", "codex_pool"])
def test_llm_removed_codex_pool_provider_keeps_single_account_pointer(provider):
    data = _valid_init()
    data["manifest"]["llm"]["provider"] = provider
    data["manifest"]["llm"]["thinking"] = "xhigh"

    with pytest.raises(ValueError) as excinfo:
        validate_init(data)
    message = str(excinfo.value)
    assert "subs-pool" in message
    assert "provider codex for a single account" in message


def test_llm_removed_claude_code_underscore_points_at_hyphen_spelling():
    data = _valid_init()
    data["manifest"]["llm"]["provider"] = "claude_code"
    with pytest.raises(ValueError, match="use provider claude-code"):
        validate_init(data)


@pytest.mark.parametrize("cap", ["vision", "daemon", "listen", "avatar"])
@pytest.mark.parametrize("provider", ["gemini", "mimo", "Custom", "codex-pool", "claude_code"])
def test_capability_removed_llm_provider_raises_with_capability_path(cap, provider):
    data = _valid_init()
    data["manifest"]["capabilities"] = {cap: {"provider": provider, "model": "m"}}

    with pytest.raises(ValueError) as excinfo:
        validate_init(data)

    message = str(excinfo.value)
    assert message.startswith(
        f"manifest.capabilities.{cap}.provider: provider {provider!r} was removed from LingTai;"
    )


@pytest.mark.parametrize(
    ("cap", "provider"),
    [
        ("vision", "openai"),
        ("vision", "anthropic"),
        ("vision", "codex"),
        ("vision", "claude-code"),
        ("vision", "local"),
        ("vision", "mlx"),
        ("vision", "inherit"),
        ("web", "duckduckgo"),
        ("web", "openai"),
        ("web_search", "duckduckgo"),
        ("listen", "whisper"),
    ],
)
def test_capability_non_removed_providers_are_accepted(cap, provider):
    data = _valid_init()
    data["manifest"]["capabilities"] = {cap: {"provider": provider}}
    validate_init(data)


@pytest.mark.parametrize("provider", ["gemini", "minimax", "zhipu", "codex-pool"])
@pytest.mark.parametrize("cap", ["web", "web_search"])
def test_web_engine_names_are_not_llm_routes_and_are_not_rejected(cap, provider):
    """``web``/``web_search`` ``provider`` values name search engines, whose
    retired names keep the capability's own legacy runtime handling."""
    data = _valid_init()
    data["manifest"]["capabilities"] = {cap: {"provider": provider}}
    validate_init(data)


def test_llm_thinking_known_field_does_not_warn():
    data = _valid_init()
    data["manifest"]["llm"]["provider"] = "codex"
    data["manifest"]["llm"]["thinking"] = "high"

    warnings = validate_init(data)

    assert "unknown field in manifest.llm: thinking" not in warnings


# --- addons (list of curated MCP names; mcp capability handles the rest) ---


def test_addons_optional():
    data = _valid_init()
    validate_init(data)  # no addons — should pass


def test_addons_list_of_names_valid():
    data = _valid_init()
    data["addons"] = ["imap", "telegram", "feishu"]
    validate_init(data)


def test_addons_empty_list_valid():
    data = _valid_init()
    data["addons"] = []
    validate_init(data)


def test_addons_dict_shape_rejected():
    """Legacy dict shape was removed in v0.7.3; the migration converts."""
    data = _valid_init()
    data["addons"] = {"imap": {"config": "imap.json"}}
    with pytest.raises(ValueError, match="addons.*list"):
        validate_init(data)


def test_addons_non_string_entries_warn():
    data = _valid_init()
    data["addons"] = ["imap", 42]
    warnings = validate_init(data)
    assert any("strings" in w for w in warnings)


def test_mcp_section_optional():
    data = _valid_init()
    validate_init(data)  # no mcp — should pass


def test_mcp_section_dict_valid():
    data = _valid_init()
    data["mcp"] = {
        "imap": {
            "type": "stdio",
            "command": "/usr/bin/python",
            "args": ["-m", "lingtai.mcp_servers.imap"],
        },
    }
    validate_init(data)


def test_mcp_section_wrong_type_rejected():
    data = _valid_init()
    data["mcp"] = ["imap"]
    with pytest.raises(ValueError, match="mcp.*object"):
        validate_init(data)


@pytest.mark.parametrize("field", ["time_awareness", "timezone_awareness"])
def test_time_awareness_field_valid_bool(field):
    data = _valid_init()
    data["manifest"][field] = False
    warnings = validate_init(data)
    assert all(field not in warning for warning in warnings)


@pytest.mark.parametrize("field", ["time_awareness", "timezone_awareness"])
def test_time_awareness_field_wrong_type_raises(field):
    data = _valid_init()
    data["manifest"][field] = "yes"
    with pytest.raises(ValueError):
        validate_init(data)


# --- schema self-consistency (drift prevention) ---
#
# The schema maintains two parallel structures per scope: an OPTIONAL dict
# (field name -> expected type, used for type validation) and a KNOWN set
# (used to suppress "unknown field" warnings). When a new field is added,
# both must be updated. These tests catch the common drift where one is
# updated and the other is forgotten.


def test_manifest_optional_fields_all_in_known():
    """Every optional manifest field must also be in MANIFEST_KNOWN,
    otherwise a valid use of that field would produce a spurious
    'unknown field' warning."""
    from lingtai.init_schema import MANIFEST_OPTIONAL, MANIFEST_KNOWN
    missing = set(MANIFEST_OPTIONAL) - MANIFEST_KNOWN
    assert not missing, (
        f"Fields in MANIFEST_OPTIONAL but not in MANIFEST_KNOWN "
        f"(would trigger unknown-field warning): {sorted(missing)}"
    )


def test_manifest_required_fields_all_in_known():
    """Every required manifest field must also be in MANIFEST_KNOWN."""
    from lingtai.init_schema import MANIFEST_REQUIRED, MANIFEST_KNOWN
    missing = set(MANIFEST_REQUIRED) - MANIFEST_KNOWN
    assert not missing, (
        f"Fields in MANIFEST_REQUIRED but not in MANIFEST_KNOWN: {sorted(missing)}"
    )


def test_manifest_known_fields_all_typed():
    """Every field in MANIFEST_KNOWN must appear in either MANIFEST_REQUIRED
    or MANIFEST_OPTIONAL, otherwise a user-supplied value passes without
    any type check.

    Exception: MANIFEST_LEGACY_IGNORED fields are intentionally untyped —
    they are recognized-and-ignored (tolerated on old init.json, never
    honored), so type-checking them would be meaningless."""
    from lingtai.init_schema import (
        MANIFEST_OPTIONAL,
        MANIFEST_REQUIRED,
        MANIFEST_KNOWN,
        MANIFEST_LEGACY_IGNORED,
    )
    typed = set(MANIFEST_OPTIONAL) | set(MANIFEST_REQUIRED)
    untyped = MANIFEST_KNOWN - typed - MANIFEST_LEGACY_IGNORED
    assert not untyped, (
        f"Fields in MANIFEST_KNOWN but not type-checked (missing from "
        f"MANIFEST_OPTIONAL or MANIFEST_REQUIRED): {sorted(untyped)}"
    )


def test_hydrator_manifest_keys_are_schema_known():
    """The hydrator (build_agent_config) and the validation schema must not
    drift: every manifest key the hydrator reads must be schema-known AND
    type-checked, so a honored field can never validate with only an
    'unknown field' warning while its value goes unchecked (issue #736)."""
    import ast
    import inspect

    from lingtai.agent import build_agent_config
    from lingtai.init_schema import (
        MANIFEST_KNOWN,
        MANIFEST_OPTIONAL,
        MANIFEST_REQUIRED,
    )

    tree = ast.parse(inspect.getsource(build_agent_config))
    keys = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "manifest"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    # Guard against silent no-ops: if build_agent_config stops reading keys
    # via manifest.get(...), this test must fail loudly, not match nothing.
    assert keys, (
        "build_agent_config no longer reads manifest keys via manifest.get — "
        "update this drift guard"
    )
    assert keys <= MANIFEST_KNOWN, (
        f"hydrator reads manifest keys unknown to init_schema: "
        f"{sorted(keys - MANIFEST_KNOWN)}"
    )
    typed = set(MANIFEST_OPTIONAL) | set(MANIFEST_REQUIRED)
    assert keys <= typed, (
        f"hydrator reads manifest keys that are not type-checked: "
        f"{sorted(keys - typed)}"
    )


def test_legacy_molt_fields_tolerated_but_ignored():
    """Stale init.json molt_notice/molt_pressure/molt_urgency/molt_prompt fields
    must not break validation (no error, no 'unknown field' warning) — they are
    recognized-and-ignored now that molt thresholds are kernel-fixed and the
    context.molt message is hardcoded in meta_block.build_molt_context."""
    from lingtai.init_schema import MANIFEST_LEGACY_IGNORED

    data = _valid_init()
    # Legacy values, including a deliberately out-of-range / wrong-feeling one,
    # must be tolerated since they are no longer honored or type-checked.
    data["manifest"]["molt_notice"] = 0.3
    data["manifest"]["molt_pressure"] = 0.8
    data["manifest"]["molt_urgency"] = 0.99
    data["manifest"]["molt_prompt"] = "ignored custom message"
    warnings = validate_init(data)  # must not raise
    for field in MANIFEST_LEGACY_IGNORED:
        assert not any(field in w for w in warnings), (
            f"legacy field {field} should be silently tolerated, got warnings: {warnings}"
        )


def test_top_optional_fields_all_in_known():
    """Every optional top-level field must also be in TOP_KNOWN."""
    from lingtai.init_schema import TOP_OPTIONAL, TOP_KNOWN
    missing = set(TOP_OPTIONAL) - TOP_KNOWN
    assert not missing, (
        f"Fields in TOP_OPTIONAL but not in TOP_KNOWN "
        f"(would trigger unknown-field warning): {sorted(missing)}"
    )


def test_llm_known_fields_compose_from_schema_sets():
    from lingtai.init_schema import (
        LLM_KNOWN,
        LLM_LEGACY_IGNORED,
        LLM_OPTIONAL,
        LLM_PASS_THROUGH_KNOWN,
        LLM_REQUIRED,
        LLM_SPECIAL_KNOWN,
    )

    expected = (
        set(LLM_REQUIRED)
        | set(LLM_OPTIONAL)
        | LLM_SPECIAL_KNOWN
        | LLM_PASS_THROUGH_KNOWN
        | LLM_LEGACY_IGNORED
    )
    assert LLM_KNOWN == expected


def test_manifest_soul_is_legacy_ignored_not_optional():
    from lingtai import init_schema

    assert "soul" in init_schema.MANIFEST_LEGACY_IGNORED
    assert "soul" not in init_schema.MANIFEST_OPTIONAL
    assert not hasattr(init_schema, "SOUL_OPTIONAL")
    assert not hasattr(init_schema, "SOUL_KNOWN")


def test_provider_default_manifest_llm_keys_are_known():
    from lingtai.init_schema import LLM_KNOWN, LLM_LEGACY_IGNORED
    from lingtai.llm import service as llm_service

    pass_through = set(llm_service._PROVIDER_DEFAULTS_PASS_THROUGH_KEYS) | {
        "default_headers"
    }

    assert pass_through <= LLM_KNOWN
    # Retired keys are known (no warning) but never forwarded to adapters.
    assert not (pass_through & LLM_LEGACY_IGNORED)


def test_manifest_accepts_pseudo_agent_subscriptions():
    data = _valid_init()
    data["manifest"]["pseudo_agent_subscriptions"] = ["../human", "../announcements"]
    warnings = validate_init(data)
    # No warnings related to this field.
    for w in warnings:
        assert "pseudo_agent_subscriptions" not in w, f"unexpected warning: {w}"


def test_manifest_rejects_non_list_pseudo_agent_subscriptions():
    import pytest
    data = _valid_init()
    data["manifest"]["pseudo_agent_subscriptions"] = "../human"  # string, not list
    with pytest.raises(ValueError, match="pseudo_agent_subscriptions"):
        validate_init(data)


def test_preset_block_minimum():
    """manifest.preset with active + default + allowed is valid."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "allowed": ["minimax"],
    }
    validate_init(data)  # should not raise


def test_preset_block_allowed_with_multiple_entries():
    """manifest.preset.allowed with multiple paths is valid as long as
    default and active both appear in the list."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "allowed": ["minimax", "zhipu", "deepseek"],
    }
    validate_init(data)  # should not raise


def test_preset_block_missing_active_raises():
    """`manifest.preset` without `active` raises."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "default": "minimax",
        "allowed": ["minimax"],
    }
    with pytest.raises(ValueError, match="manifest.preset.active"):
        validate_init(data)


def test_preset_block_missing_default_raises():
    """`manifest.preset` without `default` raises."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "allowed": ["minimax"],
    }
    with pytest.raises(ValueError, match="manifest.preset.default"):
        validate_init(data)


def test_preset_block_active_wrong_type_raises():
    """`manifest.preset.active` must be a string."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": 42,
        "default": "minimax",
        "allowed": ["minimax"],
    }
    with pytest.raises(ValueError, match="manifest.preset.active"):
        validate_init(data)


def test_preset_block_default_wrong_type_raises():
    """`manifest.preset.default` must be a string."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": 42,
        "allowed": ["minimax"],
    }
    with pytest.raises(ValueError, match="manifest.preset.default"):
        validate_init(data)


def test_preset_block_missing_allowed_raises():
    """`manifest.preset` without `allowed` raises — there is no implicit
    library scan."""
    data = _valid_init()
    data["manifest"]["preset"] = {"active": "minimax", "default": "minimax"}
    with pytest.raises(ValueError, match="manifest.preset.allowed"):
        validate_init(data)


def test_preset_block_allowed_must_be_list():
    """`manifest.preset.allowed` must be a list."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "allowed": "minimax",  # string, not list
    }
    with pytest.raises(ValueError, match="manifest.preset.allowed"):
        validate_init(data)


def test_preset_block_allowed_empty_raises():
    """`manifest.preset.allowed` must contain at least one entry."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "allowed": [],
    }
    with pytest.raises(ValueError, match="manifest.preset.allowed"):
        validate_init(data)


def test_preset_block_allowed_rejects_non_string_element():
    """Inside `allowed`, every entry must be a non-empty string."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "allowed": ["minimax", 42],
    }
    with pytest.raises(ValueError, match=r"manifest.preset.allowed\[1\]"):
        validate_init(data)


def test_preset_block_default_must_be_in_allowed():
    """`default` must appear in `allowed`."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "zhipu",
        "default": "minimax",
        "allowed": ["zhipu"],
    }
    with pytest.raises(ValueError, match="manifest.preset.default"):
        validate_init(data)


def test_preset_block_active_must_be_in_allowed():
    """`active` must appear in `allowed`."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "zhipu",
        "allowed": ["zhipu"],
    }
    with pytest.raises(ValueError, match="manifest.preset.active"):
        validate_init(data)


def test_preset_block_unknown_field_warns():
    """Unknown fields inside manifest.preset produce a warning, not an error."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "allowed": ["minimax"],
        "extra_key": "foo",
    }
    warnings = validate_init(data)
    assert any("unknown field in manifest.preset" in w for w in warnings)


def test_preset_block_old_path_field_warns_as_unknown():
    """The retired `path` field is now an unknown key, so it warns rather
    than being silently accepted."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "allowed": ["minimax"],
        "path": "/some/legacy/lib",
    }
    warnings = validate_init(data)
    assert any("unknown field in manifest.preset: path" in w for w in warnings)


def test_preset_block_unmigrated_init_points_at_canonical_agent_edit():
    """A legacy preset path gets explicit Agent-edit guidance, never migration advice."""
    data = _valid_init()
    data["manifest"]["preset"] = {
        "active": "minimax",
        "default": "minimax",
        "path": "~/.lingtai-tui/presets",
    }
    with pytest.raises(ValueError, match="not rewritten automatically") as exc_info:
        validate_init(data)
    assert "manifest.preset.allowed" in str(exc_info.value)

def test_lingtai_seed_is_optional() -> None:
    data = _valid_init()
    data.pop("lingtai")

    assert validate_init(data) == []


def test_lingtai_seed_type_checked_when_present() -> None:
    data = _valid_init()
    data["lingtai"] = 123

    with pytest.raises(ValueError, match="lingtai: expected str"):
        validate_init(data)


def test_legacy_prompt_is_not_reintroduced_as_lingtai_alias() -> None:
    data = _valid_init()
    data.pop("lingtai")
    data["prompt"] = "legacy seed"

    warnings = validate_init(data)

    assert "unknown top-level field: prompt" in warnings


def test_vendor_endpoint_via_openai_wire_api_responses_allowed():
    """A vendor's OpenAI-compatible endpoint is configured through ``openai``
    (base_url + wire_api); the vendor name itself is no longer a provider."""
    data = _valid_init()
    data["manifest"]["llm"]["provider"] = "openai"
    data["manifest"]["llm"]["model"] = "deepseek-v4-flash"
    data["manifest"]["llm"]["base_url"] = "https://api.deepseek.com"
    data["manifest"]["llm"]["wire_api"] = "responses"
    validate_init(data)  # should not raise


def test_removed_provider_pointer_survives_init_reader_redaction():
    """The init reader's safe error excerpt redacts every quoted substring, so
    the replacement pointer is written unquoted and stays actionable."""
    from lingtai.init_reader import _safe_error
    from lingtai.init_schema import removed_provider_message

    excerpt = _safe_error(removed_provider_message("manifest.llm.provider", "deepseek"))
    assert "deepseek" not in excerpt  # the user value itself is redacted
    assert "use provider openai (OpenAI-compatible" in excerpt
    assert "or anthropic (Anthropic-compatible: base_url)" in excerpt
    assert "sub2api/subs-pool" in excerpt


# ---------------------------------------------------------------------------
# manifest.llm.model: optional only for the CLI-backed claude-code provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [None, "", "opus"], ids=["null", "empty", "set"])
def test_claude_code_model_is_optional(model):
    data = _valid_init()
    data["manifest"]["llm"] = {"provider": "claude-code", "model": model}
    validate_init(data)
    del data["manifest"]["llm"]["model"]
    validate_init(data)


def test_claude_code_model_must_still_be_a_string_when_set():
    data = _valid_init()
    data["manifest"]["llm"] = {"provider": "claude-code", "model": 7}
    with pytest.raises(ValueError, match="manifest.llm.model"):
        validate_init(data)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "codex"])
def test_model_stays_required_for_api_families(provider):
    data = _valid_init()
    data["manifest"]["llm"] = {"provider": provider}
    with pytest.raises(ValueError, match="missing required field: manifest.llm.model"):
        validate_init(data)


def test_claude_code_api_key_env_is_an_optional_credential():
    """The TUI Claude template declares ``api_key_env`` even for local-login
    users; without an ``env_file`` that is not a validation error for
    ``claude-code`` (env fallback, then local login) but still is elsewhere."""
    data = _valid_init()
    data["manifest"]["llm"] = {
        "provider": "claude-code",
        "api_key": None,
        "api_key_env": "CLAUDE_CODE_OAUTH_TOKEN",
    }
    data.pop("env_file", None)
    validate_init(data)
    data["manifest"]["llm"]["api_key_env"] = ""
    validate_init(data)

    data["manifest"]["llm"] = {
        "provider": "anthropic",
        "model": "m",
        "api_key": None,
        "api_key_env": "ANTHROPIC_API_KEY",
    }
    with pytest.raises(ValueError, match="no env_file provided"):
        validate_init(data)
