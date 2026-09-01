"""System-owned read-only settings inventory and runtime-policy resolution.

System is the kernel-level catch-all: a genuine LingTai setting belongs here
when no other concrete ToolPlugin owns it. The public provider remains SHOW
only and deliberately reuses the runtime's canonical readers and resolvers.

Two closed document versions share ``<workdir>/settings/system.json``:

* **v1** — ``{"schema_version": 1, "cache_miss_budget": <positive int>}``.
  Parsed by :func:`_parse_settings`, which is deliberately unchanged: a valid
  v1 document keeps resolving byte-for-byte as before and v1 is never widened.
* **v2** — ``{"schema_version": 2, ...}`` carrying any subset of the ordinary
  runtime-policy fields in :data:`RUNTIME_POLICY_FIELDS`. Parsed by
  :func:`_parse_runtime_policy_v2`; one invalid field rejects the whole
  document so malformed System JSON can never partially override runtime
  values.

Ordinary boot/refresh fields resolve once through
:func:`resolve_runtime_policy` as ``valid env > valid v2 field > fixed
default``. Two documented exceptions keep their live resolvers:
the cache-miss budget (``env > v1/v2 > 2_000_000``; legacy
``manifest.cache_miss_budget`` is never a source) and the notification cap
(Core parses ``LINGTAI_NOTIFICATION_MAX_CHARS`` itself, then asks the outer
Agent for the v2 file value through :func:`resolve_notification_max_chars`,
then its own 10,000 default; the 2048/10,000 clamp stays in Core).

Kernel-fixed context-pressure safety thresholds (0.85 / 1.0 / 3 rounds /
0.75) and the legacy ``molt_*`` fields are not System settings: any such key
makes a v2 document invalid.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from lingtai.tools.tool_family import SettingRow, SettingsProvider

CACHE_MISS_BUDGET_ENV = "LINGTAI_CACHE_MISS_BUDGET"
DEFAULT_CACHE_MISS_BUDGET = 2_000_000
SYSTEM_SETTINGS_RELATIVE_PATH = Path("settings") / "system.json"
_SYSTEM_SETTINGS_SCHEMA_VERSION = 1
_CACHE_MISS_BUDGET_COMMENT = "system-manual#cache-miss-budget"
_INIT_COMMENT = "system-manual/reference/settings-inventory#root-and-manifest-inputs"
_LLM_COMMENT = "system-manual/reference/settings-inventory#llm-and-provider-inputs"
_ENV_COMMENT = "system-manual/reference/settings-inventory#kernel-environment-controls"
_RUNTIME_POLICY_COMMENT = "system-manual#runtime-policy-v2"
_MISSING = object()


@dataclass(frozen=True, slots=True)
class _InitSettingSpec:
    key: str
    pointer: str
    default: Any
    comment: str
    configurable: bool = True
    sensitive: bool = False


def _init(
    key: str,
    pointer: str,
    default: Any,
    *,
    configurable: bool = True,
    sensitive: bool = False,
    comment: str = _INIT_COMMENT,
) -> _InitSettingSpec:
    return _InitSettingSpec(
        key=key,
        pointer=pointer,
        default=default,
        comment=comment,
        configurable=configurable,
        sensitive=sensitive,
    )


# Stable public order. These are effective root/manifest inputs with no other
# concrete ToolPlugin owner. Prompt, credential, header, authorization, and
# path-bearing values use SettingRow's private full-redaction switch.
SYSTEM_INIT_SETTING_SPECS: tuple[_InitSettingSpec, ...] = (
    _init("env_file", "/env_file", None, sensitive=True),
    _init("venv_path", "/venv_path", None, sensitive=True),
    _init("base_prompt", "/base_prompt", "", sensitive=True),
    _init("base_prompt_file", "/base_prompt_file", None, sensitive=True),
    _init("covenant", "/covenant", None, sensitive=True),
    _init("covenant_file", "/covenant_file", None, sensitive=True),
    _init("comment", "/comment", "", sensitive=True),
    _init("comment_file", "/comment_file", None, sensitive=True),
    _init("agent_name", "/manifest/agent_name", None, configurable=False),
    _init("language", "/manifest/language", "en"),
    _init("disable", "/manifest/disable", []),
    _init("admin", "/manifest/admin", {}, sensitive=True),
    _init("time_awareness", "/manifest/time_awareness", True),
    _init("timezone_awareness", "/manifest/timezone_awareness", True),
    _init("preset.active", "/manifest/preset/active", None, sensitive=True),
    _init("preset.default", "/manifest/preset/default", None, sensitive=True),
    _init("preset.allowed", "/manifest/preset/allowed", [], sensitive=True),
    _init(
        "summarize_notification_threshold",
        "/manifest/summarize_notification_threshold",
        3_000,
    ),
    _init("llm.provider", "/manifest/llm/provider", None, comment=_LLM_COMMENT),
    _init("llm.model", "/manifest/llm/model", None, comment=_LLM_COMMENT),
    _init(
        "llm.api_key",
        "/manifest/llm/api_key",
        None,
        comment=_LLM_COMMENT,
        sensitive=True,
    ),
    _init(
        "llm.api_key_env",
        "/manifest/llm/api_key_env",
        None,
        comment=_LLM_COMMENT,
        sensitive=True,
    ),
    _init(
        "llm.base_url",
        "/manifest/llm/base_url",
        None,
        comment=_LLM_COMMENT,
        sensitive=True,
    ),
    _init(
        "llm.compact_threshold",
        "/manifest/llm/compact_threshold",
        None,
        comment=_LLM_COMMENT,
    ),
    _init("llm.wire_api", "/manifest/llm/wire_api", None, comment=_LLM_COMMENT),
    _init(
        "llm.inject_reasoning_fallback",
        "/manifest/llm/inject_reasoning_fallback",
        True,
        comment=_LLM_COMMENT,
    ),
    _init(
        "llm.reasoning_effort_vocab",
        "/manifest/llm/reasoning_effort_vocab",
        None,
        comment=_LLM_COMMENT,
    ),
    _init(
        "llm.prompt_cache_namespace",
        "/manifest/llm/prompt_cache_namespace",
        None,
        comment=_LLM_COMMENT,
    ),
    _init(
        "llm.service_tier",
        "/manifest/llm/service_tier",
        None,
        comment=_LLM_COMMENT,
    ),
    _init("llm.thinking", "/manifest/llm/thinking", None, comment=_LLM_COMMENT),
    _init("llm.api_compat", "/manifest/llm/api_compat", None, comment=_LLM_COMMENT),
    _init(
        "llm.codex_session_anchor",
        "/manifest/llm/codex_session_anchor",
        None,
        comment=_LLM_COMMENT,
        configurable=False,
        sensitive=True,
    ),
    _init(
        "llm.codex_auth_path",
        "/manifest/llm/codex_auth_path",
        None,
        comment=_LLM_COMMENT,
        sensitive=True,
    ),
    _init(
        "llm.codex_auth_pool_path",
        "/manifest/llm/codex_auth_pool_path",
        None,
        comment=_LLM_COMMENT,
        sensitive=True,
    ),
    _init(
        "llm.codex_base_urls",
        "/manifest/llm/codex_base_urls",
        None,
        comment=_LLM_COMMENT,
        sensitive=True,
    ),
    _init(
        "llm.default_headers",
        "/manifest/llm/default_headers",
        {},
        comment=_LLM_COMMENT,
        sensitive=True,
    ),
)


# Explicit owner-local schema classification. Focused tests compare these
# sets with the canonical init schema so new inputs cannot silently disappear.
SYSTEM_INIT_CONCRETE_TOOL_EXCLUSIONS = frozenset(
    {
        "/addons",
        "/mcp",
        # Psyche owns and exposes the live Pad inputs, fully redacted; System
        # must not stack duplicate rows.
        "/pad",
        "/pad_file",
        "/lingtai",
        "/lingtai_file",
        "/manifest/capabilities",
        "/manifest/plugins",
        # Email owns these mail-adapter subscription paths. Its owner-local
        # discovery row fully redacts both current and default path lists.
        "/manifest/pseudo_agent_subscriptions",
        "/manifest/soul",
    }
)
SYSTEM_INIT_INERT_OR_COMPATIBILITY_EXCLUSIONS = frozenset(
    {
        "/soul",
        "/soul_file",
        "/principle",
        "/principle_file",
        "/procedures",
        "/procedures_file",
        "/substrate",
        "/substrate_file",
        "/brief",
        "/brief_file",
        "/manifest/activeness",
        "/manifest/aed_timeout",
        "/manifest/context_limit",
        "/manifest/max_aed_attempts",
        "/manifest/max_rpm",
        "/manifest/snapshot_interval",
        "/manifest/streaming",
        "/manifest/context_rebuild_every_n_idles",
        "/manifest/context_serialization_enabled",
        "/manifest/max_turns",
        "/manifest/molt_notice",
        "/manifest/molt_pressure",
        "/manifest/molt_prompt",
        "/manifest/molt_urgency",
        "/manifest/stamina",
        "/manifest/llm/codex_thread_salt",
        "/manifest/llm/context_limit",
    }
)


CONTEXT_LIMIT_ENV = "LINGTAI_CONTEXT_LIMIT"
MAX_RPM_ENV = "LINGTAI_MAX_RPM"
STREAMING_ENV = "LINGTAI_STREAMING"
AED_TIMEOUT_ENV = "LINGTAI_AED_TIMEOUT"
MAX_AED_ATTEMPTS_ENV = "LINGTAI_MAX_AED_ATTEMPTS"
SNAPSHOT_INTERVAL_ENV = "LINGTAI_SNAPSHOT_INTERVAL"
ACTIVENESS_ENV = "LINGTAI_ACTIVENESS"
RUNTIME_POLICY_ENV = {
    "context_limit": CONTEXT_LIMIT_ENV,
    "max_rpm": MAX_RPM_ENV,
    "streaming": STREAMING_ENV,
    "aed_timeout": AED_TIMEOUT_ENV,
    "max_aed_attempts": MAX_AED_ATTEMPTS_ENV,
    "snapshot_interval": SNAPSHOT_INTERVAL_ENV,
    "activeness": ACTIVENESS_ENV,
}


@dataclass(frozen=True, slots=True)
class _EnvironmentSettingSpec:
    key: str
    names: tuple[str, ...]
    default: Any
    resolver: str
    sensitive: bool = False


# Existing environment names only. ``LINGTAI_CODEX_WS`` is one compatibility
# alias of the canonical transport selector and therefore never gets a second
# row. ``llm.inject_reasoning_fallback`` is already an init-backed row above.
SYSTEM_ENVIRONMENT_SETTING_SPECS: tuple[_EnvironmentSettingSpec, ...] = (
    _EnvironmentSettingSpec(
        "nudge.enabled", ("LINGTAI_NUDGE_ENABLED",), True, "nudge_enabled"
    ),
    _EnvironmentSettingSpec(
        "nudge.repeat_interval_seconds",
        ("LINGTAI_NUDGE_REPEAT_INTERVAL",),
        86_400.0,
        "nudge_repeat",
    ),
    _EnvironmentSettingSpec(
        "nudge.folder_size_gb",
        ("LINGTAI_NUDGE_FOLDER_SIZE_GB",),
        5.0,
        "nudge_folder_size",
    ),
    _EnvironmentSettingSpec(
        "lifecycle.active_stuck_threshold_seconds",
        ("LINGTAI_ACTIVE_STUCK_THRESHOLD_S",),
        600.0,
        "active_stuck",
    ),
    _EnvironmentSettingSpec(
        "lifecycle.agent_alive_threshold_seconds",
        ("LINGTAI_AGENT_ALIVE_THRESHOLD_SEC",),
        10.0,
        "agent_alive",
    ),
    _EnvironmentSettingSpec(
        "prompt.tool_prose_section_enabled",
        ("LINGTAI_TOOL_PROSE_SECTION_ENABLED",),
        False,
        "tool_prose",
    ),
    _EnvironmentSettingSpec(
        "prompt.system_prompt_pressure_ratio",
        ("LINGTAI_SYSTEM_PROMPT_PRESSURE_RATIO",),
        0.4,
        "prompt_pressure",
    ),
    _EnvironmentSettingSpec(
        "session_stats.refresh_seconds",
        ("LINGTAI_SESSION_STATS_REFRESH_SECONDS",),
        5.0,
        "session_stats_refresh",
    ),
    _EnvironmentSettingSpec(
        "session_stats.daemon_limit",
        ("LINGTAI_SESSION_STATS_DAEMON_LIMIT",),
        1_000,
        "session_stats_daemon_limit",
    ),
    _EnvironmentSettingSpec(
        "security.risky_action_gate",
        ("LINGTAI_RISKY_ACTION_GATE",),
        False,
        "risky_action_gate",
    ),
    _EnvironmentSettingSpec(
        "logging.console_debug",
        ("LINGTAI_VERBOSE",),
        False,
        "console_debug",
    ),
    _EnvironmentSettingSpec(
        "runtime.tool_batch_memory_relief",
        ("LINGTAI_DAEMON_MEMORY_RELIEF",),
        False,
        "tool_batch_memory_relief",
    ),
    _EnvironmentSettingSpec(
        "llm.codex_tui_dir",
        ("LINGTAI_TUI_DIR",),
        "~/.lingtai-tui",
        "codex_tui_dir",
        sensitive=True,
    ),
    _EnvironmentSettingSpec(
        "llm.codex_transport",
        ("LINGTAI_CODEX_TRANSPORT", "LINGTAI_CODEX_WS"),
        "rest",
        "codex_transport",
    ),
    _EnvironmentSettingSpec(
        "llm.codex_ws_epoch_reset_turns",
        ("LINGTAI_CODEX_WS_EPOCH_RESET_TURNS",),
        0,
        "codex_ws_epoch",
    ),
    _EnvironmentSettingSpec(
        "llm.codex_responses_trace",
        ("LINGTAI_CODEX_RESPONSES_TRACE",),
        False,
        "codex_trace",
    ),
    _EnvironmentSettingSpec(
        "llm.codex_responses_trace_path",
        ("LINGTAI_CODEX_RESPONSES_TRACE_PATH",),
        None,
        "codex_trace_path",
        sensitive=True,
    ),
    _EnvironmentSettingSpec(
        "llm.read_timeout_seconds",
        ("LINGTAI_LLM_READ_TIMEOUT",),
        300.0,
        "llm_read_timeout",
    ),
)

SYSTEM_ENVIRONMENT_SETTING_OWNERS = {
    name: spec.key
    for spec in SYSTEM_ENVIRONMENT_SETTING_SPECS
    for name in spec.names
}
SYSTEM_ENVIRONMENT_SETTING_OWNERS.update(
    {environment: key for key, environment in RUNTIME_POLICY_ENV.items()}
)
SYSTEM_ENVIRONMENT_SETTING_OWNERS[CACHE_MISS_BUDGET_ENV] = "cache_miss_budget"
SYSTEM_ENVIRONMENT_SETTING_OWNERS["LINGTAI_INJECT_REASONING_FALLBACK"] = (
    "llm.inject_reasoning_fallback"
)

# One owner-local structured classification is authoritative for environment
# coverage. The manual projects these names for people; tests never infer
# ownership by parsing prose headings.
SYSTEM_ENVIRONMENT_CLASSIFICATION: dict[str, frozenset[str]] = {
    "system": frozenset(SYSTEM_ENVIRONMENT_SETTING_OWNERS),
    "concrete_tool": frozenset(
        {
            "LINGTAI_CLAUDE_INTERACTIVE_FIFO",
            "LINGTAI_CLAUDE_MANAGED_ROOT",
            "LINGTAI_CLOUD_MAIL_CONFIG",
            "LINGTAI_DAEMON_MANAGER_POOL_SIZE",
            "LINGTAI_DAEMON_MAX_TURNS",
            "LINGTAI_DAEMON_SYSTEM_PROMPT_BUDGET_CHARS",
            "LINGTAI_FEISHU_CONFIG",
            "LINGTAI_FILE_IO_BACKEND",
            "LINGTAI_FILE_IO_SIDECAR",
            "LINGTAI_IMAP_CONFIG",
            "LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS",
            "LINGTAI_NOTIFICATION_MAX_CHARS",
            "LINGTAI_SEARCH_SIDECAR",
            "LINGTAI_SHELL",
            "LINGTAI_SOUL_FLOW_ENABLED",
            "LINGTAI_TASKCARD_POLL_INTERVAL",
            "LINGTAI_TELEGRAM_CONFIG",
            "LINGTAI_TOOL_TIMEOUT_MAX_SECONDS",
            "LINGTAI_WEB_ENGINE",
            "LINGTAI_WEB_MAX_CHARS",
            "LINGTAI_WECHAT_CONFIG",
            "LINGTAI_WHATSAPP_CONFIG",
            "LINGTAI_WHATSAPP_SESSION_DIR",
        }
    ),
    "injected_or_handoff": frozenset(
        {
            "LINGTAI_AGENT_DIR",
            "LINGTAI_DAEMON_CAPSULE_FD",
            "LINGTAI_DAEMON_CAPSULE_HANDLE",
            "LINGTAI_DAEMON_COMPLETION_FILE",
            "LINGTAI_DAEMON_MANAGER_TOKEN",
            "LINGTAI_DAEMON_RUN_ID",
            "LINGTAI_DAEMON_RUN_DIR",
            "LINGTAI_DERIVED_AVATAR_EXECUTION",
            "LINGTAI_DRIVER_AUTHORITY_FD",
            "LINGTAI_MCP_NAME",
            "LINGTAI_REFRESH_ENV_OVERWRITE",
            "LINGTAI_RUNTIME_PYTHON",
            "LINGTAI_RUNTIME_VENV",
        }
    ),
    "build_only": frozenset(
        {
            "LINGTAI_REQUIRE_RUST_BUILD",
            "LINGTAI_SKIP_RUST_BUILD",
        }
    ),
    "test_only": frozenset(
        {
            "LINGTAI_AVATAR_BOOT_WAIT_SECONDS",
            "LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM",
            "LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH",
            "LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SCENARIO",
            "LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP",
            "LINGTAI_FAKE_APP_SERVER_MODE",
            "LINGTAI_FAKE_CLI_REPORT",
            "LINGTAI_RUN_LIVE_KIMI_CODE",
            "LINGTAI_TEST_CONFIG",
            "LINGTAI_TEST_FAKE_CLAUDE_SIGNAL_RECORD",
        }
    ),
}

# Closed v2 runtime-policy document.
RUNTIME_POLICY_SCHEMA_VERSION = 2
RUNTIME_POLICY_FIELDS = (
    "context_limit",
    "max_rpm",
    "streaming",
    "aed_timeout",
    "max_aed_attempts",
    "snapshot_interval",
    "activeness",
    "cache_miss_budget",
    "notification_max_chars",
)
# Ordinary boot/refresh-time fields (the two live exceptions are excluded).
ORDINARY_POLICY_FIELDS = (
    "context_limit",
    "max_rpm",
    "streaming",
    "aed_timeout",
    "max_aed_attempts",
    "snapshot_interval",
    "activeness",
)
# Environment spelling that turns snapshots off explicitly (case-insensitive).
SNAPSHOT_INTERVAL_OFF = "off"
# Legacy default for agents whose manifest predates ``max_rpm``; matches
# ``AgentConfig.max_rpm`` and the historical ``m.get("max_rpm", 60)`` reads.
DEFAULT_MAX_RPM = 60

SOURCE_ENV = "env"
SOURCE_SYSTEM = "system"
SOURCE_DEFAULT = "default"


def _positive_int(value: Any) -> int | None:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except (TypeError, ValueError):
            return None
        if parsed > 0:
            return parsed
    return None


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _parse_settings(text: str) -> int | None:
    try:
        data = json.loads(text, object_pairs_hook=_closed_object)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "cache_miss_budget",
    }:
        return None
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != _SYSTEM_SETTINGS_SCHEMA_VERSION
    ):
        return None
    budget = data["cache_miss_budget"]
    return budget if type(budget) is int and budget > 0 else None


# --- v2 field validators -----------------------------------------------------
#
# Each validator returns ``(ok, normalized_value)``. ``ok`` is False for any
# domain violation, including booleans masquerading as numbers and non-finite
# floats (Python's json accepts ``NaN``/``Infinity`` literals).


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_finite_number(value: Any) -> bool:
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


def _validate_context_limit(value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, None
    return (_is_int(value) and value > 0), value


def _validate_max_rpm(value: Any) -> tuple[bool, Any]:
    return (_is_int(value) and value >= 0), value


def _validate_streaming(value: Any) -> tuple[bool, Any]:
    return (type(value) is bool), value


def _validate_aed_timeout(value: Any) -> tuple[bool, Any]:
    return (_is_finite_number(value) and value > 0), value


def _validate_max_aed_attempts(value: Any) -> tuple[bool, Any]:
    return (_is_int(value) and value >= 1), value


def _validate_snapshot_interval(value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, None
    return (_is_finite_number(value) and value > 0), value


def _validate_activeness(value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, None
    return (isinstance(value, str) and bool(value.strip())), value


def _validate_positive_int(value: Any) -> tuple[bool, Any]:
    return (_is_int(value) and value > 0), value


_V2_VALIDATORS = {
    "context_limit": _validate_context_limit,
    "max_rpm": _validate_max_rpm,
    "streaming": _validate_streaming,
    "aed_timeout": _validate_aed_timeout,
    "max_aed_attempts": _validate_max_aed_attempts,
    "snapshot_interval": _validate_snapshot_interval,
    "activeness": _validate_activeness,
    "cache_miss_budget": _validate_positive_int,
    "notification_max_chars": _validate_positive_int,
}


def _parse_runtime_policy_v2(text: str) -> dict[str, Any] | None:
    """Return the present, valid v2 fields, or ``None`` for any invalid document.

    Presence-aware: an absent key is not in the mapping, while an explicit JSON
    ``null`` on a nullable field (``context_limit``, ``snapshot_interval``,
    ``activeness``) is present with value ``None``. Unknown keys, duplicate
    keys, a wrong ``schema_version``, and any field-domain violation reject the
    whole document.
    """
    try:
        data = json.loads(text, object_pairs_hook=_closed_object)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "schema_version" not in data:
        return None
    version = data["schema_version"]
    if type(version) is not int or version != RUNTIME_POLICY_SCHEMA_VERSION:
        return None
    if not set(data) - {"schema_version"} <= set(RUNTIME_POLICY_FIELDS):
        return None
    fields: dict[str, Any] = {}
    for key in RUNTIME_POLICY_FIELDS:
        if key not in data:
            continue
        ok, value = _V2_VALIDATORS[key](data[key])
        if not ok:
            return None
        fields[key] = value
    return fields


def _read_settings_text(working_dir: Any) -> str | None:
    path = Path(working_dir) / SYSTEM_SETTINGS_RELATIVE_PATH
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def read_runtime_policy_document(working_dir: Any) -> dict[str, Any]:
    """Return the valid v2 field mapping for *working_dir*, else ``{}``.

    A missing, unreadable, malformed, v1, or otherwise invalid document yields
    an empty mapping so nothing is partially applied.
    """
    text = _read_settings_text(working_dir)
    if text is None:
        return {}
    fields = _parse_runtime_policy_v2(text)
    return fields if fields is not None else {}


# --- environment parsers ------------------------------------------------------
#
# Each parser returns ``(ok, value)``; ``ok`` False means "unset or invalid,
# fall through to the next layer". Values are always strings from the
# process environment, so no boolean-masquerade guard is needed.

_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}


def _env_raw(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw if raw else None


def _env_positive_int(name: str) -> tuple[bool, Any]:
    raw = _env_raw(name)
    if raw is None:
        return False, None
    try:
        value = int(raw)
    except ValueError:
        return False, None
    return (value > 0), value


def _env_nonnegative_int(name: str) -> tuple[bool, Any]:
    raw = _env_raw(name)
    if raw is None:
        return False, None
    try:
        value = int(raw)
    except ValueError:
        return False, None
    return (value >= 0), value


def _env_min_int(name: str, minimum: int) -> tuple[bool, Any]:
    raw = _env_raw(name)
    if raw is None:
        return False, None
    try:
        value = int(raw)
    except ValueError:
        return False, None
    return (value >= minimum), value


def _env_positive_number(name: str) -> tuple[bool, Any]:
    raw = _env_raw(name)
    if raw is None:
        return False, None
    try:
        value: int | float = int(raw)
    except ValueError:
        try:
            value = float(raw)
        except ValueError:
            return False, None
    if not _is_finite_number(value) or value <= 0:
        return False, None
    return True, value


def _env_bool(name: str) -> tuple[bool, Any]:
    raw = _env_raw(name)
    if raw is None:
        return False, None
    word = raw.lower()
    if word in _TRUE_WORDS:
        return True, True
    if word in _FALSE_WORDS:
        return True, False
    return False, None


def _env_snapshot_interval(name: str) -> tuple[bool, Any]:
    raw = _env_raw(name)
    if raw is None:
        return False, None
    if raw.lower() == SNAPSHOT_INTERVAL_OFF:
        return True, None
    return _env_positive_number(name)


def _env_activeness(name: str) -> tuple[bool, Any]:
    raw = _env_raw(name)
    if raw is None:
        return False, None
    return True, raw


_ENV_PARSERS = {
    "context_limit": lambda: _env_positive_int(CONTEXT_LIMIT_ENV),
    "max_rpm": lambda: _env_nonnegative_int(MAX_RPM_ENV),
    "streaming": lambda: _env_bool(STREAMING_ENV),
    "aed_timeout": lambda: _env_positive_number(AED_TIMEOUT_ENV),
    "max_aed_attempts": lambda: _env_min_int(MAX_AED_ATTEMPTS_ENV, 1),
    "snapshot_interval": lambda: _env_snapshot_interval(SNAPSHOT_INTERVAL_ENV),
    "activeness": lambda: _env_activeness(ACTIVENESS_ENV),
}


# --- resolved policy -----------------------------------------------------------


def _policy_defaults() -> dict[str, Any]:
    from lingtai.kernel.config import AgentConfig

    defaults = AgentConfig()
    return {
        "context_limit": defaults.context_limit,
        "max_rpm": DEFAULT_MAX_RPM,
        "streaming": False,
        "aed_timeout": defaults.aed_timeout,
        "max_aed_attempts": defaults.max_aed_attempts,
        "snapshot_interval": defaults.snapshot_interval,
        "activeness": defaults.activeness,
    }


@dataclass(frozen=True)
class ResolvedRuntimePolicy:
    """Effective ordinary runtime policy plus per-field provenance.

    ``sources[field]`` is one of ``"env"``, ``"system"``, or ``"default"``.
    """

    context_limit: int | None
    max_rpm: int
    streaming: bool
    aed_timeout: float
    max_aed_attempts: int
    snapshot_interval: float | None
    activeness: str | None
    sources: Mapping[str, str] = field(default_factory=dict)

    def as_overrides(self) -> dict[str, Any]:
        """Return the ordinary fields as a plain mapping (no provenance)."""
        return {name: getattr(self, name) for name in ORDINARY_POLICY_FIELDS}


def resolve_runtime_policy(working_dir: Any) -> ResolvedRuntimePolicy:
    """Resolve the ordinary boot/refresh fields once for boot and refresh.

    Per field: valid env > valid v2 System field > fixed default.  Agent
    ``init.json`` is deliberately not an input: stale ordinary runtime knobs
    remain compatibility-known but cannot affect boot or refresh.
    """
    system_fields = read_runtime_policy_document(working_dir)
    defaults = _policy_defaults()
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name in ORDINARY_POLICY_FIELDS:
        ok, env_value = _ENV_PARSERS[name]()
        if ok:
            values[name], sources[name] = env_value, SOURCE_ENV
        elif name in system_fields:
            values[name], sources[name] = system_fields[name], SOURCE_SYSTEM
        else:
            values[name], sources[name] = defaults[name], SOURCE_DEFAULT
    return ResolvedRuntimePolicy(sources=sources, **values)

def _pointer_value(data: Mapping[str, Any], pointer: str) -> Any:
    current: Any = data
    for segment in pointer[1:].split("/"):
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _effective_init(root: Path) -> dict[str, Any]:
    """Fresh-read the same materialized/resolved object used by boot/refresh."""
    from lingtai.agent import load_preset
    from lingtai.init_reader import read_init, reader_callbacks

    materialize, prepare = reader_callbacks(root, load_preset=load_preset)
    outcome = read_init(root, materialize=materialize, prepare=prepare)
    if not outcome.ok or outcome.data is None:
        raise RuntimeError("System settings current init state is unavailable")
    return outcome.data


def _resolved_prompt_value(data: Mapping[str, Any], key: str) -> Any:
    """Resolve the canonical file-over-inline precedence without exposing text."""
    from lingtai.kernel.config_resolve import resolve_file

    return resolve_file(data.get(key), data.get(f"{key}_file"))


def _openai_adapter_default(parameter: str) -> Any:
    """Read an effective constructor default from the canonical adapter."""
    from inspect import Parameter, signature

    from lingtai.llm.openai.adapter import OpenAIAdapter

    default = signature(OpenAIAdapter.__init__).parameters[parameter].default
    if default is Parameter.empty:
        raise RuntimeError("OpenAI adapter setting has no constructor default")
    return default


def _custom_adapter_default(parameter: str) -> Any:
    """Read an effective constructor default from the canonical custom adapter."""
    from inspect import Parameter, signature

    from lingtai.llm.custom.adapter import create_custom_adapter

    default = signature(create_custom_adapter).parameters[parameter].default
    if default is Parameter.empty:
        raise RuntimeError("custom adapter setting has no constructor default")
    return default


@dataclass(frozen=True, slots=True)
class _SelectedLLMRoute:
    factory: str
    api_compat: Any = None


# This narrow classifier covers only selected-factory LLM axes; it reads the
# actual registered factory identities and never constructs an adapter or
# reads credentials. That naturally keeps every alias bound to _custom/_codex
# on the same route without maintaining a second alias registry here.
_SELECTED_FACTORY_LLM_SETTING_KEYS = frozenset(
    {
        "llm.compact_threshold",
        "llm.wire_api",
        "llm.inject_reasoning_fallback",
        "llm.reasoning_effort_vocab",
        "llm.prompt_cache_namespace",
        "llm.service_tier",
        "llm.api_compat",
    }
)


def _normalized_selected_provider_defaults(
    data: Mapping[str, Any], root: Path, *, max_rpm: int
) -> Mapping[str, Any]:
    """Apply the runtime's canonical manifest-to-provider normalization once."""
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    manifest = data["manifest"]
    llm = manifest["llm"]
    provider = str(llm["provider"]).lower()
    normalized = build_provider_defaults_from_manifest_llm(
        dict(llm),
        max_rpm=max_rpm,
        working_dir=root,
    )
    if normalized is None:
        return {}
    selected = normalized.get(provider, {})
    if not isinstance(selected, Mapping):
        raise RuntimeError("selected provider defaults are malformed")
    return selected


def _selected_llm_route(
    llm: Mapping[str, Any], normalized: Mapping[str, Any]
) -> _SelectedLLMRoute:
    """Classify the selected registered factory without constructing a client."""
    from lingtai.llm.service import LLMService

    provider = str(llm.get("provider") or "").lower()
    factories = LLMService._adapter_registry
    selected = factories.get(provider)
    if selected is None:
        raise RuntimeError("selected LLM provider has no registered factory")
    if selected is factories.get("openai"):
        return _SelectedLLMRoute("openai")
    if selected is factories.get("custom"):
        # Runtime drops authored api_compat=null before _custom. Omitted/null
        # therefore both take its OpenAI default and forward preserved compact
        # null plus any authored non-null reasoning vocabulary.
        effective_compat = normalized.get(
            "api_compat", _custom_adapter_default("api_compat")
        )
        if effective_compat == "openai":
            return _SelectedLLMRoute("custom_openai", effective_compat)
        if effective_compat == "anthropic" or effective_compat == "gemini":
            return _SelectedLLMRoute("custom_other", effective_compat)
        # create_custom_adapter sends every other admitted value (including
        # non-lowercase/structured values) to OpenAIAdapter, but _register's
        # _custom does not forward compact/reasoning axes unless compat is the
        # exact lowercase string "openai".
        return _SelectedLLMRoute("custom_openai_fallback", "openai")
    if selected is factories.get("deepseek"):
        return _SelectedLLMRoute("deepseek")
    if selected is factories.get("codex"):
        return _SelectedLLMRoute("codex")
    if selected is factories.get("mimo"):
        return _SelectedLLMRoute("mimo")
    return _SelectedLLMRoute("ignored")


def _effective_nullable_llm_values(
    key: str,
    llm: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> tuple[Any, Any]:
    """Return selected-route ``(current, default)`` without constructing clients."""
    route = _selected_llm_route(llm, normalized)

    if key == "llm.compact_threshold":
        if route.factory == "deepseek":
            # _register._deepseek consumes a positive authored value and
            # otherwise pins the generic OpenAI adapter to disabled/null.
            return normalized.get("compact_threshold"), None
        if route.factory in {"openai", "custom_openai"}:
            default = _openai_adapter_default("compact_threshold")
            # _register preserves explicit None on these two forwarding routes.
            current = normalized.get("compact_threshold", default)
            return current, default
        if route.factory == "custom_openai_fallback":
            default = _openai_adapter_default("compact_threshold")
            return default, default
        return None, None

    if key == "llm.reasoning_effort_vocab":
        if route.factory in {"openai", "custom_openai"}:
            default = _openai_adapter_default("reasoning_effort_vocab")
            return normalized.get("reasoning_effort_vocab", default), default
        if route.factory == "custom_openai_fallback":
            default = _openai_adapter_default("reasoning_effort_vocab")
            return default, default
        # DeepSeek installs a provider-owned reasoning policy; every other
        # non-OpenAI factory ignores this generic vocabulary setting.
        return None, None

    if key == "llm.inject_reasoning_fallback":
        default = True
        if route.factory in {
            "openai",
            "custom_openai",
            "custom_openai_fallback",
        }:
            if route.factory != "custom_openai_fallback":
                authored = normalized.get("inject_reasoning_fallback", _MISSING)
                if authored is not _MISSING:
                    return authored, default
            from lingtai.llm.openai.adapter import _env_bool

            return (
                _env_bool("LINGTAI_INJECT_REASONING_FALLBACK", default=default),
                default,
            )
        if route.factory == "deepseek":
            return normalized.get("inject_reasoning_fallback", default), default
        return None, None

    if key == "llm.prompt_cache_namespace":
        if route.factory in {"openai", "custom_openai"}:
            default = _openai_adapter_default("prompt_cache_namespace")
            return normalized.get("prompt_cache_namespace", default), default
        if route.factory == "deepseek":
            return normalized.get("prompt_cache_namespace", "deepseek"), "deepseek"
        return None, None

    if key == "llm.api_compat":
        # _register._custom is the only factory that consumes api_compat. Its
        # default remains OpenAI even while an authored non-OpenAI route is live.
        if route.factory in {
            "custom_openai",
            "custom_openai_fallback",
            "custom_other",
        }:
            return route.api_compat, _custom_adapter_default("api_compat")
        return None, None

    raise RuntimeError("unknown nullable LLM setting")


def _effective_wire_api_values(
    llm: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> tuple[Any, Any]:
    """Return the selected factory's effective selector and omitted default."""
    route = _selected_llm_route(llm, normalized)
    authored = normalized.get("wire_api", _MISSING)

    # The Codex factory ignores the generic selector and forces Responses.
    if route.factory == "codex":
        return "responses", "responses"
    # MiMo changes OpenAIAdapter's omitted selector to Responses but forwards
    # an explicit value, including ``auto``, unchanged.
    if route.factory == "mimo":
        default = "responses"
        return (default if authored is _MISSING else authored), default
    # These factories forward an explicit selector. With omission, their real
    # runtime construction has no legacy Responses preference and therefore
    # selects Chat Completions.
    if route.factory in {
        "openai",
        "custom_openai",
        "custom_openai_fallback",
        "deepseek",
    }:
        default = "chat_completions"
        return (default if authored is _MISSING else authored), default
    return None, None


def _effective_service_tier_values(
    llm: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> tuple[Any, Any]:
    """Return the Codex-only public tier after its canonical validation."""
    route = _selected_llm_route(llm, normalized)
    if route.factory != "codex":
        return None, None

    raw = normalized.get("service_tier")
    from lingtai.llm._register import _normalize_service_tier

    wire_value = _normalize_service_tier(raw)
    if wire_value is None:
        return None, None
    # The public setting is the authored vocabulary (``fast``); ``priority``
    # is the private wire normalization owned by the Codex factory.
    return str(raw).strip(), None


def _init_current(spec: _InitSettingSpec, data: dict[str, Any], root: Path) -> Any:
    manifest = data["manifest"]
    llm = manifest["llm"]

    if spec.key in {"base_prompt", "covenant", "comment"}:
        value = _resolved_prompt_value(data, spec.key)
        return spec.default if value is None else value

    if spec.key in {
        "language",
        "time_awareness",
        "timezone_awareness",
        "llm.thinking",
    }:
        from lingtai.agent import build_agent_config

        max_rpm = manifest.get("max_rpm", 60)
        hydrated = build_agent_config(manifest, max_rpm=max_rpm)
        attribute = {"llm.thinking": "thinking"}.get(spec.key, spec.key)
        return getattr(hydrated, attribute)

    if spec.key == "llm.api_key":
        from lingtai.kernel.config_resolve import resolve_env_checked

        return resolve_env_checked(
            llm.get("api_key"),
            llm.get("api_key_env"),
            context="manifest.llm.api_key_env",
            warn=lambda _message: None,
        )

    if spec.key == "llm.codex_session_anchor":
        configured = llm.get("codex_session_anchor")
        if configured is not None:
            return configured
        if str(llm.get("provider") or "").lower() in {
            "codex",
            "codex-pool",
            "codex_pool",
        }:
            return str((root / "init.json").resolve())
        return None

    value = _pointer_value(data, spec.pointer)
    return spec.default if value is _MISSING else value


def _init_values(
    spec: _InitSettingSpec,
    data: dict[str, Any],
    root: Path,
    normalized_llm: Mapping[str, Any],
) -> tuple[Any, Any]:
    """Project selected-route current/default values for one init-backed row."""
    if spec.key in _SELECTED_FACTORY_LLM_SETTING_KEYS:
        if spec.key == "llm.wire_api":
            return _effective_wire_api_values(
                data["manifest"]["llm"], normalized_llm
            )
        if spec.key == "llm.service_tier":
            return _effective_service_tier_values(
                data["manifest"]["llm"], normalized_llm
            )
        return _effective_nullable_llm_values(
            spec.key, data["manifest"]["llm"], normalized_llm
        )
    if spec.key == "llm.thinking":
        from lingtai.agent import build_agent_config

        manifest = data["manifest"]
        llm_without_thinking = dict(manifest["llm"])
        llm_without_thinking.pop("thinking", None)
        default_manifest = {**manifest, "llm": llm_without_thinking}
        default = build_agent_config(
            default_manifest, max_rpm=manifest.get("max_rpm", 60)
        ).thinking
        return _init_current(spec, data, root), default
    return _init_current(spec, data, root), spec.default


def _environment_current(resolver: str, root: Path) -> Any:
    if resolver in {"nudge_enabled", "nudge_repeat"}:
        from lingtai.kernel.nudge import effective_policy

        policy = effective_policy()
        return (
            policy.enabled
            if resolver == "nudge_enabled"
            else policy.repeat_interval_seconds
        )
    if resolver == "nudge_folder_size":
        from lingtai.kernel.nudge.folder_size import _read_limit_gb

        return _read_limit_gb()[0]
    if resolver == "active_stuck":
        from lingtai.kernel.base_agent.lifecycle import _active_stuck_threshold_s

        return _active_stuck_threshold_s()
    if resolver == "agent_alive":
        from lingtai.kernel.config import HEARTBEAT_LIVENESS_SECONDS

        return HEARTBEAT_LIVENESS_SECONDS
    if resolver == "tool_prose":
        from lingtai.kernel.config import tool_prose_section_enabled

        return tool_prose_section_enabled()
    if resolver == "prompt_pressure":
        from lingtai.kernel.config import system_prompt_pressure_ratio

        return system_prompt_pressure_ratio()
    if resolver in {"session_stats_refresh", "session_stats_daemon_limit"}:
        from lingtai.kernel.session_stats import (
            session_stats_daemon_limit,
            session_stats_refresh_seconds,
        )

        return (
            session_stats_refresh_seconds()
            if resolver == "session_stats_refresh"
            else session_stats_daemon_limit()
        )
    if resolver == "risky_action_gate":
        from lingtai.kernel.risky_action_gate import load_gate_config

        return load_gate_config(root) is not None
    if resolver == "console_debug":
        return os.environ.get("LINGTAI_VERBOSE") == "1"
    if resolver == "tool_batch_memory_relief":
        from lingtai.kernel.malloc_relief import enabled

        return enabled()
    if resolver == "codex_tui_dir":
        from lingtai.auth.codex_pool import resolve_codex_tui_dir

        return str(resolve_codex_tui_dir())
    if resolver in {
        "codex_transport",
        "codex_ws_epoch",
        "codex_trace",
        "codex_trace_path",
        "llm_read_timeout",
    }:
        from lingtai.llm.openai.adapter import (
            _codex_responses_trace_path,
            _codex_transport_from_env,
            _codex_ws_epoch_reset_turns,
            _read_timeout_cap,
        )

        if resolver == "codex_transport":
            return _codex_transport_from_env()
        if resolver == "codex_ws_epoch":
            return _codex_ws_epoch_reset_turns()
        if resolver == "codex_trace":
            return _codex_responses_trace_path() is not None
        if resolver == "codex_trace_path":
            path = _codex_responses_trace_path()
            return None if path is None else str(path)
        return _read_timeout_cap()
    raise RuntimeError("unknown System environment setting resolver")


_RUNTIME_POLICY_INVENTORY_ORDER = (
    "context_limit",
    "snapshot_interval",
    "max_rpm",
    "max_aed_attempts",
    "aed_timeout",
    "streaming",
    "activeness",
)
_RUNTIME_POLICY_INSERT_INDEX = next(
    index + 1
    for index, spec in enumerate(SYSTEM_INIT_SETTING_SPECS)
    if spec.key == "disable"
)
SYSTEM_SETTING_KEYS = (
    "cache_miss_budget",
    *(spec.key for spec in SYSTEM_INIT_SETTING_SPECS[:_RUNTIME_POLICY_INSERT_INDEX]),
    *_RUNTIME_POLICY_INVENTORY_ORDER,
    *(spec.key for spec in SYSTEM_INIT_SETTING_SPECS[_RUNTIME_POLICY_INSERT_INDEX:]),
    *(spec.key for spec in SYSTEM_ENVIRONMENT_SETTING_SPECS),
)


def _runtime_policy_inventory_values(
    key: str,
    runtime_policy: ResolvedRuntimePolicy,
    policy_defaults: Mapping[str, Any],
) -> tuple[Any, Any]:
    """Project effective SHOW values without changing runtime-policy semantics."""
    current = getattr(runtime_policy, key)
    default = policy_defaults[key]
    if key == "context_limit":
        from lingtai.llm.service import CONSERVATIVE_CONTEXT_WINDOW

        # ``None`` means no configured cap; LLMService then uses its conservative
        # effective window. SHOW reports that effective truth, not the sentinel.
        current = CONSERVATIVE_CONTEXT_WINDOW if current is None else current
        default = CONSERVATIVE_CONTEXT_WINDOW
    return current, default


def _resolve_inventory_cache_miss_budget(root: Path) -> int:
    current = _positive_int(os.environ.get(CACHE_MISS_BUDGET_ENV))
    if current is not None:
        return current

    path = root / SYSTEM_SETTINGS_RELATIVE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_CACHE_MISS_BUDGET
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("System settings current value is unavailable") from exc
    current = _parse_settings(text)
    if current is not None:
        return current
    fields = _parse_runtime_policy_v2(text)
    if fields is None:
        raise ValueError("System settings current value is unavailable")
    return fields.get("cache_miss_budget", DEFAULT_CACHE_MISS_BUDGET)


def system_settings_provider(workdir: Path | None) -> SettingsProvider:
    """Build one fresh, complete SHOW provider bound to an agent workdir."""
    root = None if workdir is None else Path(workdir)

    def provide() -> tuple[SettingRow, ...]:
        if root is None:
            raise ValueError("schema-only System settings provider has no workdir")

        rows: list[SettingRow] = [
            SettingRow(
                key="cache_miss_budget",
                current=_resolve_inventory_cache_miss_budget(root),
                default=DEFAULT_CACHE_MISS_BUDGET,
                configurable=True,
                comment=_CACHE_MISS_BUDGET_COMMENT,
            )
        ]

        runtime_policy = resolve_runtime_policy(root)
        policy_defaults = _policy_defaults()
        data = _effective_init(root)
        normalized_llm = _normalized_selected_provider_defaults(
            data, root, max_rpm=runtime_policy.max_rpm
        )
        for key in _RUNTIME_POLICY_INVENTORY_ORDER:
            current, default = _runtime_policy_inventory_values(
                key, runtime_policy, policy_defaults
            )
            rows.append(
                SettingRow(
                    key=key,
                    current=current,
                    default=default,
                    configurable=True,
                    comment=_RUNTIME_POLICY_COMMENT,
                )
            )

        for spec in SYSTEM_INIT_SETTING_SPECS:
            current, default = _init_values(spec, data, root, normalized_llm)
            rows.append(
                SettingRow(
                    key=spec.key,
                    current=current,
                    default=default,
                    configurable=spec.configurable,
                    comment=spec.comment,
                    _sensitive=spec.sensitive,
                )
            )

        for spec in SYSTEM_ENVIRONMENT_SETTING_SPECS:
            rows.append(
                SettingRow(
                    key=spec.key,
                    current=_environment_current(spec.resolver, root),
                    default=spec.default,
                    configurable=True,
                    comment=_ENV_COMMENT,
                    _sensitive=spec.sensitive,
                )
            )

        rows_by_key = {row.key: row for row in rows}
        if len(rows_by_key) != len(rows):
            raise RuntimeError("System settings inventory contains duplicate keys")
        rows = [rows_by_key[key] for key in SYSTEM_SETTING_KEYS]
        if len(SYSTEM_SETTING_KEYS) != len(set(SYSTEM_SETTING_KEYS)):
            raise RuntimeError("System settings inventory contains duplicate keys")
        return tuple(rows)

    return provide


def resolve_cache_miss_budget(agent: Any) -> int:
    """Resolve live env, then System JSON (v1 or v2), then the fixed default.

    ``manifest.cache_miss_budget`` is deliberately not a source.
    """
    env_budget = _positive_int(os.environ.get(CACHE_MISS_BUDGET_ENV))
    if env_budget is not None:
        return env_budget

    text = _read_settings_text(agent._working_dir)
    budget = _parse_settings(text) if text is not None else None
    if budget is None and text is not None:
        fields = _parse_runtime_policy_v2(text)
        if fields is not None:
            budget = fields.get("cache_miss_budget")
    return budget if budget is not None else DEFAULT_CACHE_MISS_BUDGET


def resolve_notification_max_chars(agent: Any) -> int | None:
    """Return the v2 ``notification_max_chars`` file value, or ``None``.

    Core owns ``LINGTAI_NOTIFICATION_MAX_CHARS`` (higher precedence), the
    2048/10,000 clamp, and the 10,000 default; this resolver only supplies the
    System-owned file layer between them.
    """
    return read_runtime_policy_document(agent._working_dir).get(
        "notification_max_chars"
    )
