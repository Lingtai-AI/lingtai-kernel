"""init.json validation — required fields are strict, unknown fields warn."""
from __future__ import annotations

import logging
import math

from lingtai.kernel.config import (
    THINKING_LEVELS,
    THINKING_NATIVE_PROVIDERS,
    THINKING_PROVIDERS,
    llm_supports_thinking,
)

log = logging.getLogger(__name__)

# Schema tables lifted to module scope so tests can assert internal consistency
# (every optional field has a type, no known field is missing from the other).
# When adding a new manifest field, update BOTH MANIFEST_OPTIONAL and
# MANIFEST_KNOWN — test_init_schema.py enforces this.

TOP_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "env_file": str,
    "venv_path": str,
    # addons is a list of curated MCP names — looked up in the kernel
    # catalog and decompressed into mcp_registry.jsonl by the `mcp`
    # capability on agent boot.
    "addons": list,
    # mcp is the per-MCP activation map — see lingtai/tools/mcp/skills/mcp-manual/SKILL.md.
    # Keys must match registered names; values are subprocess specs.
    "mcp": dict,
}

# Top-level fields retired in past versions. The current production reader keeps
# them in the raw in-memory mapping long enough to report ignored paths and never
# calls strip_deprecated(); this helper remains only for explicit legacy callers
# and focused historical tests. Fields that need archive/event/version tracking
# belong to the retained test/maintenance migration surface.
DEPRECATED_TOP_FIELDS: set[str] = {
    # "soul" / "soul_file" — retired in v0.7.6. The soul-flow voice is
    # now owned by the agent via soul(action='voice') and stored under
    # manifest.soul.{voice,voice_prompt}.
    "soul", "soul_file",
    # The retired brief prompt and its file selector are tolerated as generic
    # deprecated input, but are never typed, resolved, or consumed.
    "brief", "brief_file",
}

# Legacy fields removed by version-controlled agent-domain migrations. They are
# known to validation only so stale/restored init.json files do not look like
# active supported schema fields and do not get type-checked as prompt sections.
#
# The configurable system-prompt surface is owned by Psyche's closed
# ``settings/psyche.json`` document. Its former top-level init fields are
# compatibility-known only: tolerated on old/restored init.json (no error, no
# warning and no type/path/content handling) but never honored. Kernel-owned
# prompt layers — `principle`, `procedures`, and `substrate` — follow the
# same inert treatment. Retired `brief` input is handled by the generic
# deprecated field set above rather than this historical migration-only set.
LEGACY_MIGRATED_TOP_FIELDS: set[str] = {
    "base_prompt", "base_prompt_file",
    "covenant", "covenant_file",
    "comment", "comment_file",
    "principle", "principle_file",
    "procedures", "procedures_file",
    "substrate", "substrate_file",
}

TOP_KNOWN: set[str] = {
    "manifest", "env_file", "venv_path", "addons", "mcp",
    "pad", "pad_file", "lingtai", "lingtai_file",
} | DEPRECATED_TOP_FIELDS | LEGACY_MIGRATED_TOP_FIELDS

MANIFEST_REQUIRED: dict[str, type | tuple[type, ...]] = {
    "llm": dict,
}

MANIFEST_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "agent_name": (str, type(None)),
    "language": str,
    "capabilities": dict,
    "disable": list,
    "soul": dict,
    # NOTE: molt_notice / molt_pressure / molt_urgency / molt_prompt are
    # deliberately NOT here. They were retired as agent-configurable fields —
    # molt thresholds are kernel-fixed runtime constants (see config.py
    # MOLT_*_THRESHOLD) and the context.molt message is now hardcoded in
    # meta_block.build_molt_context. See MANIFEST_LEGACY_IGNORED below.
    "admin": dict,
    "time_awareness": bool,
    "timezone_awareness": bool,
    "pseudo_agent_subscriptions": list,
    "preset": dict,
    # Large-result hint threshold.  `current_tool_result_chars` always ranks
    # tool results over a fixed 1000-char floor into
    # `_meta.agent_meta.agent_state.current_tool_result_chars.top_results` as summarize
    # candidates, regardless of this value (see `tools/system/ANATOMY.md`).
    # This field only gates `over_threshold_count` in that same block and the
    # inline `tool_meta.comment.overflow` hint in ToolExecutor. Default: 3000.
    # 0 disables the threshold comparison and the inline overflow hint (the
    # fixed-floor top_results ranking still runs). Runtime mutation via the
    # system tool is not supported — change this field and refresh.
    "summarize_notification_threshold": int,
    # plugins is the CANONICAL declaration list for Agent Plugins
    # (agent-plugins.org, v1.0.0): a list of plugin package directories,
    # absolute / tilde-prefixed / relative to the agent working dir. Each
    # declared plugin is registered on boot by
    # services.plugin_registry.register_plugins — its skills/ joins the skills
    # catalog and its mcp.json servers join mcp_registry.jsonl with
    # source="plugin:<name>". Removing an entry uninstalls it on the next
    # refresh. The alias manifest.capabilities.plugin.paths means the same
    # thing and is retained for configs written against PR #1232.
    "plugins": list,
}

# Manifest fields retired from the active schema but still tolerated on
# existing / restored init.json so old agents keep validating. They are
# recognized-and-ignored: kept out of MANIFEST_OPTIONAL (no type-check, not an
# honored override) but folded into MANIFEST_KNOWN so they raise no "unknown
# field" warning. The kernel no longer reads these values — see config.py
# (MOLT_*_THRESHOLD kernel constants), meta_block.py (hardcoded molt message),
# and agent.py (config reload ignores stale molt fields).
MANIFEST_LEGACY_IGNORED: set[str] = {
    "molt_notice", "molt_pressure", "molt_urgency", "molt_prompt",
    "stamina",
    # max_turns: deliberately ignored by build_agent_config (agent.py) —
    # ACTIVE-turn tool-call safety is kernel-owned in
    # lingtai.kernel.safety_limits. It used to be typed here as int even
    # though nothing read it; it is now recognized-and-ignored so stale
    # init.json values stay warning-free without being advertised as a live
    # typed knob (issue #736).
    "max_turns",
    # Ordinary runtime policy is owned only by environment and the closed v2
    # System document.  Existing init.json keys stay readable without a
    # schema/type failure, but no boot, refresh, or preset path reads them.
    "context_limit", "max_rpm", "streaming", "aed_timeout",
    "max_aed_attempts", "snapshot_interval", "activeness",
}

MANIFEST_KNOWN: set[str] = (
    set(MANIFEST_REQUIRED) | set(MANIFEST_OPTIONAL) | MANIFEST_LEGACY_IGNORED
)

NoneType = type(None)

SOUL_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "delay": (int, float),
    "consultation_past_count": int,
    "voice": str,
    "voice_prompt": str,
}
SOUL_KNOWN: set[str] = set(SOUL_OPTIONAL)

LLM_REQUIRED: dict[str, type | tuple[type, ...]] = {
    "provider": str,
    "model": str,
}
LLM_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "api_key": (str, NoneType),
    "api_key_env": str,
    "base_url": (str, NoneType),
    "compact_threshold": (int, NoneType),
    # OpenAI-compatible wire selection. ``auto`` preserves legacy behavior;
    # ``chat_completions``/``responses`` force the respective wire path even
    # for custom base URLs. Scoped to OpenAI-compatible providers.
    "wire_api": str,
    # Generic OpenAI-compatible ``reasoning_content`` round-trip fallback:
    # default-on per-turn-unique stub injection on assistant turns after the
    # first tool_call that lack real thinking (env LINGTAI_INJECT_REASONING_FALLBACK
    # to disable; explicit config wins). Honored by the openai, custom
    # (api_compat=openai), and deepseek factories; openrouter/zhipu/mimo
    # currently ignore these manifest keys.
    "inject_reasoning_fallback": (bool, NoneType),
    # Chat Completions ``reasoning_effort`` vocabulary: ``openai`` (high/low
    # mapping) or retained ``seven_tier`` kernel-level passthrough compatibility.
    "reasoning_effort_vocab": (str, NoneType),
    # Fixed provider namespace for the auto-derived ``prompt_cache_key``.
    "prompt_cache_namespace": (str, NoneType),
    # Common Codex service tier; the factory validates supported values.
    "service_tier": str,
}
LLM_SPECIAL_KNOWN: set[str] = {"thinking"}
LLM_PASS_THROUGH_KNOWN: set[str] = {
    "api_compat",
    "codex_session_anchor",
    "codex_thread_salt",
    "codex_auth_path",
    "codex_auth_pool_path",
    "codex_base_urls",
    "default_headers",
    "service_tier",
    "wire_api",
}
LLM_KNOWN: set[str] = (
    set(LLM_REQUIRED) | set(LLM_OPTIONAL) | LLM_SPECIAL_KNOWN | LLM_PASS_THROUGH_KNOWN
)


def _is_json_finite(value: object) -> bool:
    """Check finite floats with an iterative, cycle-safe canonical-container walk."""
    pending = [value]
    seen_containers: set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            return False
        if type(current) is dict:
            identity = id(current)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            for key, item in current.items():
                pending.extend((key, item))
        elif type(current) in (list, tuple):
            identity = id(current)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            pending.extend(current)
    return True


def strip_deprecated(data: dict) -> list[str]:
    """Remove deprecated top-level fields from *data* in-place.

    Returns the list of field names that were removed (empty if none).
    """
    removed: list[str] = []
    for key in DEPRECATED_TOP_FIELDS:
        if key in data:
            del data[key]
            removed.append(key)

    if removed:
        log.debug("stripped deprecated init.json fields: %s", ", ".join(sorted(removed)))
    return removed


def validate_init(data: dict) -> list[str]:
    """Validate an init.json dict.

    Raises ValueError for missing required fields or wrong types on known fields.
    Returns a list of warning strings for unknown/unexpected fields.
    """
    warnings: list[str] = []

    _require_keys(data, {
        "manifest": dict,
    }, prefix="")

    # Required text fields: inline value OR _file path (at least one required).
    #
    # `lingtai` is intentionally NOT required. It selects one of two modes:
    # a nonempty resolved value (inline or from `lingtai_file`) is a forced
    # identity value written to system/lingtai.md during each reconstruction;
    # an absent or empty value selects self-evolve mode and leaves that file
    # untouched. In both cases the internal LingTai composer renders the file as
    # the `character` prompt section during canonical context reconstruction. The field was renamed from `prompt` /
    # `prompt_file`; there is still NO legacy alias — a stale `prompt` remains an
    # unknown-field warning rather than being reintroduced.
    #
    # Note: "soul" / "soul_file" was removed in v0.7.6 — the soul-flow
    # voice lives at manifest.soul.{voice,voice_prompt} now. The legacy
    # fields are kept in TOP_KNOWN for silent ignore (no warning).
    for key in ("pad",):
        file_key = f"{key}_file"
        has_inline = key in data
        has_file = file_key in data
        if not has_inline and not has_file:
            raise ValueError(f"missing required field: {key} (or {file_key})")
        if has_inline and not isinstance(data[key], str):
            raise ValueError(f"{key}: expected str, got {type(data[key]).__name__}")
        if has_file and not isinstance(data[file_key], str):
            raise ValueError(f"{file_key}: expected str, got {type(data[file_key]).__name__}")

    # `lingtai` remains an optional init seed. Psyche's base_prompt, covenant,
    # and comment pairs are intentionally absent: their legacy init spellings
    # are known-but-inert and their strict schema lives in settings/psyche.json.
    for key in ("lingtai",):
        file_key = f"{key}_file"
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"{key}: expected str, got {type(data[key]).__name__}")
        if file_key in data and not isinstance(data[file_key], str):
            raise ValueError(f"{file_key}: expected str, got {type(data[file_key]).__name__}")

    # Optional top-level fields — check types for known ones
    _optional_keys(data, TOP_OPTIONAL, prefix="")

    # Warn about unknown top-level keys
    for key in data:
        if key not in TOP_KNOWN:
            warnings.append(f"unknown top-level field: {key}")

    manifest = data["manifest"]
    _require_keys(manifest, MANIFEST_REQUIRED, prefix="manifest")
    _optional_keys(manifest, MANIFEST_OPTIONAL, prefix="manifest")

    disable = manifest.get("disable")
    if isinstance(disable, list):
        for index, entry in enumerate(disable):
            if not isinstance(entry, str):
                raise ValueError(
                    f"manifest.disable[{index}]: expected str, "
                    f"got {type(entry).__name__}"
                )

    # Validate manifest.preset umbrella if present.
    #
    # Schema (post path→allowed redesign): {default, active, allowed}.
    # - default: path string (the agent's home preset; AED auto-fallback target)
    # - active: path string (currently materialized preset)
    # - allowed: list[str] of preset paths the agent may swap to at runtime
    #
    # Both `default` and `active` MUST be members of `allowed`. Listing them
    # there is the only place the agent's authorized preset surface is
    # declared — there is no implicit "everything in the library directory"
    # fallback.
    preset = manifest.get("preset")
    if preset is not None:
        if not isinstance(preset, dict):
            raise ValueError(f"manifest.preset: expected object, got {type(preset).__name__}")
        if not preset.get("active"):
            raise ValueError("manifest.preset.active is required when manifest.preset is set")
        if not preset.get("default"):
            raise ValueError("manifest.preset.default is required when manifest.preset is set")
        if not isinstance(preset["active"], str):
            raise ValueError(f"manifest.preset.active: expected str, got {type(preset['active']).__name__}")
        if not isinstance(preset["default"], str):
            raise ValueError(f"manifest.preset.default: expected str, got {type(preset['default']).__name__}")
        allowed = preset.get("allowed")
        if allowed is None:
            # The legacy `path` field was retired in the path→allowed
            # redesign. The reader is intentionally read-only: point the
            # Agent at the exact canonical edit instead of a TUI migration.
            hint = ""
            if "path" in preset:
                hint = (
                    " — legacy manifest.preset.path is not rewritten automatically; "
                    "have the Agent explicitly replace it with the canonical "
                    "manifest.preset.allowed list, then rerun the same reader"
                )
            raise ValueError(
                "manifest.preset.allowed is required when manifest.preset is set "
                "(list of preset paths this agent may use at runtime)" + hint
            )
        if not isinstance(allowed, list):
            raise ValueError(
                f"manifest.preset.allowed: expected list[str], got {type(allowed).__name__}"
            )
        if not allowed:
            raise ValueError(
                "manifest.preset.allowed must be non-empty — at minimum it "
                "must contain the default preset"
            )
        for i, entry in enumerate(allowed):
            if not isinstance(entry, str) or not entry:
                raise ValueError(
                    f"manifest.preset.allowed[{i}]: expected non-empty str, "
                    f"got {type(entry).__name__}"
                )
        if preset["default"] not in allowed:
            raise ValueError(
                f"manifest.preset.default ({preset['default']!r}) must appear "
                f"in manifest.preset.allowed"
            )
        if preset["active"] not in allowed:
            raise ValueError(
                f"manifest.preset.active ({preset['active']!r}) must appear "
                f"in manifest.preset.allowed"
            )
        # Warn on unknown keys
        for key in preset:
            if key not in {"active", "default", "allowed"}:
                warnings.append(f"unknown field in manifest.preset: {key}")

    for key in manifest:
        if key not in MANIFEST_KNOWN:
            warnings.append(f"unknown field: manifest.{key}")

    if "summarize_notification_threshold" in manifest:
        summarize_threshold = manifest["summarize_notification_threshold"]
        if summarize_threshold < 0:
            raise ValueError(
                "manifest.summarize_notification_threshold: expected non-negative int"
            )

    soul = manifest.get("soul")
    if soul is not None:
        _optional_keys(soul, SOUL_OPTIONAL, prefix="manifest.soul")
        for key in soul:
            if key not in SOUL_KNOWN:
                warnings.append(f"unknown field in manifest.soul: {key}")

    llm = manifest["llm"]
    _require_keys(llm, LLM_REQUIRED, prefix="manifest.llm")
    _optional_keys(llm, LLM_OPTIONAL, prefix="manifest.llm")
    if "api_compat" in llm and not _is_json_finite(llm["api_compat"]):
        raise ValueError(
            "manifest.llm.api_compat: expected recursively JSON-finite value"
        )
    if "compact_threshold" in llm:
        compact_threshold = llm["compact_threshold"]
        if isinstance(compact_threshold, int) and compact_threshold <= 0:
            raise ValueError(
                "manifest.llm.compact_threshold: expected positive int or null"
            )
    if "wire_api" in llm:
        wire_api = llm["wire_api"]
        allowed_wire_api = {"auto", "chat_completions", "responses"}
        if wire_api not in allowed_wire_api:
            raise ValueError(
                "manifest.llm.wire_api: expected one of "
                f"{', '.join(sorted(allowed_wire_api))}, got {wire_api!r}"
            )
        # The wire_api field is scoped to OpenAI-compatible wire semantics.
        # Non-auto values are allowed only for the official OpenAI provider,
        # DeepSeek (whose adapter now supports both wires), or a custom
        # OpenAI-compatible endpoint (api_compat omitted/openai).
        # Every other provider/compat is rejected — including Codex, which is
        # forcibly on Responses and out of scope here — so misuse fails loudly.
        # ``auto`` is harmless everywhere and is left through unchanged.
        if wire_api != "auto":
            provider = str(llm.get("provider", "")).lower()
            api_compat = str(llm.get("api_compat", "openai")).lower() or "openai"
            allowed_scope = provider in {"openai", "deepseek"} or (
                provider == "custom" and api_compat == "openai"
            )
            if not allowed_scope:
                raise ValueError(
                    "manifest.llm.wire_api is scoped to OpenAI-compatible "
                    "providers; it cannot be used with "
                    f"provider={llm.get('provider')!r}"
                    + (f" api_compat={llm.get('api_compat')!r}" if llm.get("api_compat") else "")
                )
    if "thinking" in llm:
        # DeepSeek owns its own effort surface: what a manifest may carry
        # depends on the exact model and wire, so validation delegates to the
        # DeepSeek module rather than to the kernel-global level tuple. That
        # tuple is deliberately NOT extended with DeepSeek vocabulary.
        from lingtai.llm.deepseek.policy import owns_provider, validate_llm_block

        if owns_provider(llm.get("provider")):
            validate_llm_block(llm)
        elif not llm_supports_thinking(llm):
            raise ValueError(
                "manifest.llm.thinking is supported only for "
                "thinking-capable providers — the Codex providers "
                f"({', '.join(THINKING_PROVIDERS)}), "
                f"{', '.join(THINKING_NATIVE_PROVIDERS)}, or any "
                "OpenAI-compatible block (api_compat=openai)"
            )
        else:
            thinking = llm["thinking"]
            if not isinstance(thinking, str) or thinking not in THINKING_LEVELS:
                raise ValueError(
                    "manifest.llm.thinking: expected one of "
                    f"{', '.join(THINKING_LEVELS)}"
                )
    for key in llm:
        if key not in LLM_KNOWN:
            warnings.append(f"unknown field in manifest.llm: {key}")

    # If api_key_env is set without api_key, env_file must be provided
    if llm.get("api_key_env") and not llm.get("api_key"):
        if not data.get("env_file"):
            raise ValueError(
                "manifest.llm.api_key_env is set but no env_file provided "
                "— the agent cannot resolve the API key without it"
            )

    # Validate addons: must be a list of curated MCP names. The `mcp`
    # capability validates each catalog record at decompression time, so
    # there's no per-name validation here.
    addons = data.get("addons")
    if isinstance(addons, list):
        if not all(isinstance(x, str) for x in addons):
            warnings.append("addons: all entries must be strings (curated MCP names)")

    # Validate manifest.plugins: the canonical Agent Plugins declaration list.
    # Each entry is a plugin package directory. Per-plugin validation (the
    # plugin.json $schema/name contract and §4.1 path containment) happens at
    # registration time, so there is no per-entry validation here — only the
    # shape, which is what makes a malformed entry a warning rather than a
    # boot failure.
    plugins = manifest.get("plugins")
    if isinstance(plugins, list):
        if not all(isinstance(x, str) and x for x in plugins):
            warnings.append(
                "manifest.plugins: all entries must be non-empty strings "
                "(Agent Plugin package directories)"
            )

    # Validate manifest.capabilities.skills shape if present.
    caps = manifest.get("capabilities") or {}
    if isinstance(caps, dict):
        cap_name = "skills"
        cfg = caps.get(cap_name)
        if cfg is not None:
            if not isinstance(cfg, dict):
                raise ValueError(
                    f"manifest.capabilities.{cap_name}: expected object, "
                    f"got {type(cfg).__name__}"
                )
            paths = cfg.get("paths")
            if paths is not None:
                if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                    raise ValueError(
                        f"manifest.capabilities.{cap_name}.paths: expected list[str]"
                    )
            for key in cfg:
                if key != "paths":
                    warnings.append(
                        f"unknown field in manifest.capabilities.{cap_name}: {key}"
                    )

    return warnings


def _require_keys(
    data: dict,
    schema: dict[str, type | tuple[type, ...]],
    prefix: str,
) -> None:
    """Check that all keys exist in data with correct types."""
    for key, expected_type in schema.items():
        path = f"{prefix}.{key}" if prefix else key

        if key not in data:
            raise ValueError(f"missing required field: {path}")

        _check_type(data[key], expected_type, path)


def _optional_keys(
    data: dict,
    schema: dict[str, type | tuple[type, ...]],
    prefix: str,
) -> None:
    """Check types for keys that are present but not required."""
    for key, expected_type in schema.items():
        if key not in data:
            continue
        path = f"{prefix}.{key}" if prefix else key
        _check_type(data[key], expected_type, path)


def _check_type(
    value: object,
    expected_type: type | tuple[type, ...],
    path: str,
) -> None:
    """Validate a single value's type."""
    expected = expected_type if isinstance(expected_type, tuple) else (expected_type,)
    # bool is a subclass of int in Python — reject bools for numeric fields
    # unless bool is explicitly one of the accepted types.
    if isinstance(value, bool) and int in expected and bool not in expected:
        raise ValueError(f"{path}: expected number, got bool")

    if not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            names = [t.__name__ for t in expected_type if t is not type(None)]
            type_str = (
                (" | ".join(names) + " | null")
                if type(None) in expected_type
                else " | ".join(names)
            )
        else:
            type_str = expected_type.__name__
            if expected_type is dict:
                type_str = "object"
        raise ValueError(
            f"{path}: expected {type_str}, got {type(value).__name__}"
        )
