"""Vision capability for one-image understanding via ``VisionService``.

The public action-separated ``vision`` root exposes ``analyze``, ``check``,
``list``, ``settings``, and ``manual``. The operational action values are unchanged;
generic composition adds the new
reserved ``settings`` action before ``manual``. ``analyze`` uses one selected
route, while a non-null ``preset`` explicitly borrows an authorized preset for
one call. Relative image paths use the workdir and a null question selects the
default prompt.

Routes never silently choose a provider, model, credential, preset, MCP, or CLI.
Local and MLX routes are explicit; Claude vision points to an explicit
``claude -p`` action. Unsupported setup and request failures return sanitized
manual guidance.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Mapping

from lingtai.kernel.tool_plugin import BoundToolPlugin, ToolPluginDeclaration, ToolPluginDeclarationError

from ..tool_family import ChildTool, SettingRow, SettingsProvider, ToolFamily
from ..tool_family.manual import MANUAL_INPUT_SCHEMA, build_manual_child
from .settings import DEFAULT_LOCAL_BASE_URL, LocalVisionSettings, SettingsError


if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent
    from lingtai.kernel.tool_plugin import ActiveProviderPort, ToolPluginHost, WorkdirPort
    from lingtai.services.vision import VisionService


def _setup_failure(provider: str, exc: BaseException) -> str:
    """Build explicit manual guidance without exposing exception contents."""
    return (
        f"Direct vision setup failed for provider {provider!r} "
        f"({type(exc).__name__}); use vision(action='manual', input={{}}, "
        f"reasoning='direct vision setup failed, load the manual route')."
    )


def _consent_guidance() -> str:
    """Build the setup-with-human-consent guidance for a vision failure.

    Installing a local vision server, pulling a model, or editing
    settings/vision.json / the capability manifest are external side effects:
    the agent must obtain explicit human consent before performing them. The
    full steps live in the vision manual skill.
    """
    return (
        "To enable vision, load the vision manual skill for the setup steps: "
        "vision(action='manual', input={}, reasoning='vision is not set up, "
        "load the setup steps'); then ask the human for consent before "
        "installing a local vision server, pulling a model, or editing "
        "settings/vision.json / the capability manifest."
    )


_CODEX_FAMILY = {"codex"}

# Claude Code CLI vision: both spellings identify the claude backend whose
# vision route is the operator-installed Claude Code CLI (``claude -p``).
# LingTai does not proxy the CLI's auth, so these providers return explicit
# guidance instead of constructing a service. ``claude-p`` is the explicit
# vision-route alias beside the LLM registry's ``claude-code`` provider.
_CLAUDE_CLI_FAMILY = {"claude-p", "claude-code"}

# The two API provider families with a direct Vision service. Each accepts any
# compatible endpoint through ``base_url``; the default route inherits the
# active provider's effective endpoint, model, and credential.
_API_FAMILIES = {"openai", "anthropic"}

_VISION_SETTING_KEYS = (
    "provider",
    "base_url",
    "model",
    "api_key",
    "api_key_env",
    "max_tokens",
    "wire_api",
    "default_headers",
    "token_path",
    "instructions",
    "max_output_tokens",
    "timeout",
)
_VISION_SENSITIVE_SETTINGS = {
    "base_url",
    "api_key",
    "api_key_env",
    "default_headers",
    "token_path",
    "instructions",
}
_MODEL_DEFAULTS = {
    "mlx": "mlx-community/paligemma2-3b-ft-docci-448-8bit",
}
_BASE_URL_DEFAULTS = {
    "local": DEFAULT_LOCAL_BASE_URL,
    "codex": "https://chatgpt.com/backend-api/codex",
}
_MAX_TOKENS_DEFAULTS = {
    "local": 1024,
    "openai": 1024,
    "anthropic": 1024,
    "mlx": 512,
}


@dataclass(frozen=True, slots=True)
class _VisionSettingsSnapshot:
    """One applied bind snapshot containing no raw sensitive values."""

    current: tuple[Any, ...]
    default: tuple[Any, ...]
    sensitive: tuple[bool, ...]


def _same_codex_family(requested: str, active: str) -> bool:
    """Return whether both names identify the native Codex provider."""
    return requested in _CODEX_FAMILY and active in _CODEX_FAMILY


def _vision_endpoint(provider: str | None) -> str:
    """Classify a provider's vision endpoint for the mechanical ``list`` action.

    Pure string classification — never constructs a service, reads a
    credential, or touches the network.
    """
    key = (provider or "").lower()
    if key in _CODEX_FAMILY:
        return "responses"
    if key in _CLAUDE_CLI_FAMILY:
        return "claude-cli"
    if key == "local":
        return "openai-compatible-local"
    if key == "mlx":
        return "mlx-on-device"
    if key in _API_FAMILIES:
        return "provider-service"
    return "unknown"


def _responses_vision(provider: str | None) -> bool:
    """Return whether a provider routes vision through the Responses API."""
    return bool(provider and provider.lower() in _CODEX_FAMILY)


def _canonical_preset_path(ref: str, working_dir: Path) -> str:
    """Return the canonical physical path a preset reference denotes.

    ``~/x.json``, its expanded absolute spelling, and a working-dir-relative
    spelling all name one file, so ``list`` keys its rows on this value (the
    same normalization the kernel's allowed-preset membership test applies).
    A reference that cannot be resolved keys on its own spelling: it is never
    dropped here, and it stays exactly as authorization-bounded as before.
    """
    try:
        path = Path(ref).expanduser()
        if not path.is_absolute():
            path = Path(working_dir) / path
        return str(path.resolve(strict=False))
    except (ValueError, OSError, RuntimeError):
        return ref


def _normalize_codex_auth_path(raw: object) -> str | None:
    """Return a trimmed nonblank Codex auth path, or ``None``.

    Mirrors the canonical Codex factory (``lingtai/llm/_register.py`` ``_codex``),
    which strips ``codex_auth_path`` before constructing ``FixedAccountSource``,
    so a space-padded or whitespace-only value is never forwarded as
    ``token_path``.
    """
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed:
            return trimmed
    return None


def _same_provider_identity(requested: str, active: str) -> bool:
    """Return whether two provider names identify the same current route."""
    if requested == active:
        return True
    if {requested, active} <= _CLAUDE_CLI_FAMILY:
        return True
    return _same_codex_family(requested, active)


def _effective_openai_wire(wire_api: object) -> str | None:
    """Resolve the ``openai`` wire selector; reject unknown protocols.

    ``responses`` selects the Responses API; ``chat_completions``, the legacy
    ``auto``, blank, and omission all select Chat Completions (the ``openai``
    provider's own default). Any other value returns ``None`` so the route
    stays manual-only.
    """
    normalized = wire_api.strip().lower() if isinstance(wire_api, str) else wire_api
    if normalized is None or normalized in {"", "auto", "chat_completions"}:
        return "chat_completions"
    if normalized == "responses":
        return "responses"
    return None


def _plain_service_value(service: Any, *names: str) -> Any:
    """Read one already-applied scalar without reaching into a client."""
    for name in names:
        try:
            value = getattr(service, name)
        except (AttributeError, TypeError):
            continue
        if isinstance(value, (str, int, float, bool)):
            return value
    return None


def _active_effective_base_url(service: Any) -> str | None:
    """Return the endpoint the active provider service actually talks to.

    ``LLMService.effective_base_url`` resolves the adapter's own default when
    the manifest omitted ``base_url`` (the official OpenAI/Anthropic endpoint
    or an SDK-level override), so a credential inherited from the active
    service is only ever sent to that same endpoint. Falls back to the raw
    configured ``_base_url`` for services that expose no effective endpoint.
    """
    for name in ("effective_base_url", "_base_url"):
        try:
            value = getattr(service, name)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _endpoint_key(url: object) -> str | None:
    """Normalize an endpoint for same-endpoint comparison only."""
    if not isinstance(url, str) or not url.strip():
        return None
    return url.strip().rstrip("/").lower()


def _model_is_path_like(value: str) -> bool:
    """Return whether a model value has an explicit filesystem-path shape."""
    candidate = value.strip()
    return (
        candidate.startswith(("~/", "~\\", "./", ".\\", "../", "..\\", "file://"))
        or PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
    )


def _vision_service_kind(provider: str) -> str | None:
    """Name the concrete Vision service boundary selected by the resolver."""
    if provider in _CODEX_FAMILY:
        return "codex"
    if provider in {"mlx", "local"} or provider in _API_FAMILIES:
        return provider
    return None


def _vision_settings_snapshot(
    configuration: VisionConfiguration,
    provider: str | None,
    active_service: Any,
    vision_service: Any,
    local_settings: LocalVisionSettings | None,
) -> _VisionSettingsSnapshot:
    """Project the exact successful bind inputs without retaining secrets."""
    if vision_service is None or not isinstance(provider, str) or not provider.strip():
        raise SettingsError("vision route is unavailable")

    provider = provider.strip()
    provider_key = provider.lower()
    service_kind = _vision_service_kind(provider_key)
    if service_kind is None:
        raise SettingsError("vision route is unavailable")
    kwargs = dict(configuration.kwargs)
    active_name = getattr(active_service, "provider", "")
    active_key = active_name.lower() if isinstance(active_name, str) else ""
    same_provider = _same_provider_identity(provider_key, active_key)
    defaults = getattr(active_service, "_provider_defaults", None) if same_provider else None
    bucket = defaults.get(active_key) if isinstance(defaults, dict) else None
    bucket = bucket if isinstance(bucket, dict) else {}

    active_model = _plain_service_value(active_service, "_model") if same_provider else None
    active_base_url = (
        _active_effective_base_url(active_service) if same_provider else None
    )

    if provider_key == "local":
        base_url = (
            kwargs.get("base_url")
            or (local_settings.base_url if local_settings is not None else None)
            or DEFAULT_LOCAL_BASE_URL
        )
        model = (
            kwargs.get("model")
            or (local_settings.model if local_settings is not None else None)
        )
        max_tokens = kwargs.get("max_tokens")
        if max_tokens is None and local_settings is not None:
            max_tokens = local_settings.max_tokens
    else:
        base_url = (
            kwargs.get("base_url")
            or active_base_url
            or bucket.get("base_url")
            or _BASE_URL_DEFAULTS.get(provider_key)
        )
        model = kwargs.get("model") or active_model or bucket.get("model")
        max_tokens = kwargs.get("max_tokens")

    applied_model = _plain_service_value(
        vision_service, "_model_name", "_model", "model"
    )
    model = applied_model or model or _MODEL_DEFAULTS.get(provider_key)
    if not isinstance(model, str) or not model.strip():
        raise SettingsError("vision model is unavailable")
    model = model.strip()
    model_is_sensitive = _model_is_path_like(model)
    if model_is_sensitive:
        model = True

    applied_base_url = _plain_service_value(vision_service, "_base_url")
    if applied_base_url is not None:
        base_url = applied_base_url

    applied_max_tokens = _plain_service_value(vision_service, "_max_tokens")
    if applied_max_tokens is not None:
        max_tokens = applied_max_tokens
    if max_tokens is None:
        max_tokens = _MAX_TOKENS_DEFAULTS.get(provider_key)
        if max_tokens is None:
            max_tokens = _MAX_TOKENS_DEFAULTS.get(service_kind)

    applied_wire = _plain_service_value(vision_service, "_wire_api")
    if isinstance(applied_wire, str):
        wire_api = applied_wire
    elif service_kind == "codex":
        wire_api = "responses"
    elif service_kind in {"openai", "local"}:
        wire_api = _effective_openai_wire(
            kwargs.get("wire_api") or bucket.get("wire_api"),
        )
    else:
        wire_api = None

    token_manager = getattr(vision_service, "_token_manager", None)
    token_path = (
        kwargs.get("token_path")
        or bucket.get("codex_auth_path")
        or _plain_service_value(token_manager, "_path")
    )
    instructions = (
        _plain_service_value(vision_service, "_instructions")
        or kwargs.get("instructions")
    )
    max_output_tokens = _plain_service_value(
        vision_service, "_max_output_tokens"
    )
    if max_output_tokens is None:
        max_output_tokens = kwargs.get("max_output_tokens")
    timeout = _plain_service_value(vision_service, "_timeout")
    if timeout is None:
        timeout = kwargs.get("timeout")

    uses_api_key = service_kind in {"openai", "anthropic", "local"}
    uses_headers = service_kind in _API_FAMILIES
    current = {
        "provider": provider,
        "base_url": bool(base_url) if service_kind != "mlx" else None,
        "model": model,
        "api_key": True if uses_api_key else None,
        "api_key_env": True if uses_api_key and configuration.api_key_env else None,
        "max_tokens": max_tokens if service_kind != "codex" else None,
        "wire_api": wire_api,
        "default_headers": (
            True
            if uses_headers
            and bool(kwargs.get("default_headers") or bucket.get("default_headers"))
            else None
        ),
        "token_path": True if service_kind == "codex" and token_path else None,
        "instructions": True if service_kind == "codex" and instructions else None,
        "max_output_tokens": max_output_tokens if service_kind == "codex" else None,
        "timeout": timeout if service_kind == "codex" else None,
    }
    default_wire = None
    if service_kind == "codex":
        default_wire = "responses"
    elif service_kind in {"openai", "local"}:
        default_wire = "chat_completions"
    default = {
        "provider": None,
        "base_url": bool(_BASE_URL_DEFAULTS.get(provider_key)) or None,
        "model": _MODEL_DEFAULTS.get(provider_key),
        "api_key": True if provider_key == "local" else None,
        "api_key_env": None,
        "max_tokens": (
            None
            if service_kind == "codex"
            else _MAX_TOKENS_DEFAULTS.get(
                provider_key, _MAX_TOKENS_DEFAULTS.get(service_kind)
            )
        ),
        "wire_api": default_wire,
        "default_headers": None,
        "token_path": None,
        "instructions": True if service_kind == "codex" else None,
        "max_output_tokens": None,
        "timeout": 120.0 if service_kind == "codex" else None,
    }
    return _VisionSettingsSnapshot(
        current=tuple(current[key] for key in _VISION_SETTING_KEYS),
        default=tuple(default[key] for key in _VISION_SETTING_KEYS),
        sensitive=tuple(
            key in _VISION_SENSITIVE_SETTINGS
            or (key == "model" and model_is_sensitive)
            for key in _VISION_SETTING_KEYS
        ),
    )


def _vision_settings_provider(
    configuration: VisionConfiguration,
    provider: str | None,
    active_service: Any,
    vision_service: Any,
    local_settings: LocalVisionSettings | None,
    failure: Exception | None,
) -> SettingsProvider:
    """Bind one SHOW-only provider to the already-applied Vision route."""
    try:
        if failure is not None:
            raise failure
        snapshot = _vision_settings_snapshot(
            configuration,
            provider,
            active_service,
            vision_service,
            local_settings,
        )
    except Exception:
        return _unavailable_vision_settings

    def provide() -> tuple[SettingRow, ...]:
        return tuple(
            SettingRow(
                key=key,
                current=current,
                default=default,
                configurable=True,
                comment=f"vision-manual#setting-{key.replace('_', '-')}",
                _sensitive=sensitive,
            )
            for key, current, default, sensitive in zip(
                _VISION_SETTING_KEYS,
                snapshot.current,
                snapshot.default,
                snapshot.sensitive,
            )
        )

    return provide


def _unavailable_vision_settings() -> tuple[SettingRow, ...]:
    """Fail closed when no complete applied owner snapshot was bound."""
    raise RuntimeError("vision settings unavailable")


PROVIDERS = {
    "providers": [
        "openai", "anthropic", "codex", "claude-p", "claude-code", "local",
    ],
    "default": None,
    "fallback_on_inherit": None,  # no agnostic fallback for vision
}

_ANALYZE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "image_path": {
            "type": "string",
            "description": "Existing image file path; relative paths use the workdir",
        },
        "question": {
            # Strict OpenAI object branches express an optional field as a
            # required nullable property. Null means absent, and the analyze
            # handler then applies the same default prompt it always has.
            "type": ["string", "null"],
            "description": "Question about the image; null uses the default prompt",
        },
        "preset": {
            "type": ["string", "null"],
            "description": "Authorized manifest.preset.allowed route to borrow once; null uses default",
        },
    },
    "required": ["image_path", "question"],
    "additionalProperties": False,
}

def _unused(_input: Mapping[str, Any]) -> dict[str, Any]:
    raise AssertionError("the module-level schema-only ToolFamily never dispatches")


_CHECK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "preset": {
            "type": ["string", "null"],
            "description": "Authorized preset to check; null checks default without an image",
        },
    },
    "required": ["preset"],
    "additionalProperties": False,
}

_LIST_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class VisionConfiguration:
    """The capability setup input supplied through the configuration port.

    It contains exactly the public ``setup()`` arguments, never an Agent. The
    static declaration owns its validation and interpretation at bind time;
    keeping the value immutable makes a refresh bind from one coherent snapshot.
    The kernel ``ConfigurationPort`` carries a copied mapping (the same shape
    Shell earned); :meth:`port_values` and :meth:`from_port_values` are the
    only translation between that mapping and this typed snapshot.
    """

    vision_service: Any | None
    provider: str | None
    api_key: str | None
    api_key_env: str | None
    kwargs: Mapping[str, Any]

    _PORT_FIELDS = ("vision_service", "provider", "api_key", "api_key_env", "kwargs")

    def port_values(self) -> dict[str, Any]:
        """The mapping handed to the host's ``configuration`` port for this bind."""
        return {
            "vision_service": self.vision_service,
            "provider": self.provider,
            "api_key": self.api_key,
            "api_key_env": self.api_key_env,
            "kwargs": dict(self.kwargs),
        }

    @classmethod
    def from_port_values(cls, values: Any) -> "VisionConfiguration":
        """Rebuild the snapshot from the granted port; refuse any other shape."""
        if not isinstance(values, Mapping) or set(values) != set(cls._PORT_FIELDS):
            raise ToolPluginDeclarationError(
                "vision requires a VisionConfiguration snapshot supplied by "
                "capability setup through its configuration port"
            )
        kwargs = values["kwargs"]
        if not isinstance(kwargs, Mapping):
            raise ToolPluginDeclarationError(
                "vision configuration kwargs must be a mapping"
            )
        return cls(
            vision_service=values["vision_service"],
            provider=values["provider"],
            api_key=values["api_key"],
            api_key_env=values["api_key_env"],
            kwargs=dict(kwargs),
        )


_DESCRIPTION = (
    "Analyze one image: vision(action='analyze', "
    "input={'image_path': '...', 'question': null}, reasoning='...'). Paths use the "
    "workdir and null uses the default image prompt. Use check, list, settings, "
    "or manual for route identity, declarations, the read-only applied snapshot, "
    "or guidance. A non-null preset must name an authorized "
    "manifest.preset.allowed borrow and uses that preset's own identity. "
    "Failures are sanitized; no provider, model, credential, preset, or MCP "
    "fallback is automatic."
)


def _build_family(
    analyze_handler: Any = _unused,
    check_handler: Any = _unused,
    list_handler: Any = _unused,
    manual_child: ChildTool | None = None,
    settings_provider: SettingsProvider = _unavailable_vision_settings,
) -> ToolFamily:
    """Build Vision's declared family from its one static declaration.

    The module-level schema-only family and each host-bound dispatcher derive
    their public name, operational schemas, and reserved manual slot from
    :data:`DECLARATION`. This prevents the advertised action inventory from
    drifting away from the declaration the kernel reserves.
    """
    return ToolFamily(
        DECLARATION.name,
        [
            ChildTool(
                action,
                DECLARATION.input_schemas[action],
                handler,
                title=f"{action} input",
            )
            for action, handler in (
                ("analyze", analyze_handler),
                ("check", check_handler),
                ("list", list_handler),
            )
        ]
        + [
            manual_child
            or ChildTool("manual", DECLARATION.manual_input_schema, _unused, title="manual input")
        ],
        settings_provider=settings_provider,
    )


def get_description(lang: str = "en") -> str:
    return _DESCRIPTION


def get_schema(lang: str = "en") -> dict:
    # Composed by generic ToolFamily infrastructure from the declaration-derived
    # schema-only family, never hand-assembled.
    return _FAMILY.build_schema()


class VisionManager:
    """Host-bound Vision dispatcher with no reference to the live Agent."""

    def __init__(
        self,
        workdir: "WorkdirPort",
        active_provider: "ActiveProviderPort",
        vision_service: "VisionService | None",
        manual_reason: str = "",
        settings_provider: SettingsProvider = _unavailable_vision_settings,
    ) -> None:
        self._workdir = workdir
        self._active_provider = active_provider
        self._vision_service = vision_service
        self._manual_reason = manual_reason
        # The declaration-derived child registry gets only this dispatcher's
        # handlers and the workdir-bound packaged manual child.
        self._family = _build_family(
            self._dispatch_analyze,
            self._dispatch_check,
            self._dispatch_list,
            build_manual_child(workdir, DECLARATION.manual),
            settings_provider,
        )

    def __call__(self, args: dict | None) -> dict:
        """Make the manager itself the registrar-published handler."""
        return self.handle(args)

    def _build_service_from_preset(self, preset_ref: str) -> tuple[Any, str]:
        """Borrow another preset's vision service for one call.

        The preset must appear in ``manifest.preset.allowed`` (same
        authorization surface as preset swapping). Its ``manifest.llm`` plus
        ``manifest.capabilities.vision`` provide the provider/model/credential
        identity; ``_resolve_direct_service`` is invoked with an identity shim
        built from that preset so the borrowed provider resolves its own route
        and credentials (e.g. a ``codex`` preset with its own
        ``codex_auth_path``) instead of inheriting the active provider's.

        Returns ``(VisionService | None, manual_reason, identity)`` where
        ``identity`` is a dict of the resolved provider/model identity (empty
        on failure).
        """
        import json as _json

        from lingtai.kernel.presets import load_preset, resolve_allowed_presets

        init_path = Path(self._workdir.path) / "init.json"
        if not init_path.is_file():
            return None, "No init.json is available to resolve manifest.preset.allowed.", {}
        try:
            init_data = _json.loads(init_path.read_text(encoding="utf-8"))
        except Exception:
            return None, "init.json could not be parsed while resolving preset.allowed.", {}
        manifest = init_data.get("manifest") or {}
        allowed_paths = {str(p) for p in resolve_allowed_presets(manifest, self._workdir.path)}
        raw_allowed = set(manifest.get("preset", {}).get("allowed") or [])
        resolved_ref = str(Path(preset_ref).expanduser())
        if preset_ref not in raw_allowed and resolved_ref not in allowed_paths:
            return None, (
                f"Preset {preset_ref!r} is not in manifest.preset.allowed; "
                "only authorized presets may be borrowed for vision."
            ), {}
        try:
            preset = load_preset(
                preset_ref,
                working_dir=self._workdir.path,
                # Read-only preset loading: no migration surface for a borrow.
                run_migrations=lambda _path: None,
            )
        except Exception as exc:
            return None, f"Failed to load preset {preset_ref!r}: {type(exc).__name__}.", {}
        pm = preset.get("manifest") or {}
        llm = pm.get("llm") or {}
        vision_cap = (pm.get("capabilities") or {}).get("vision") or {}
        provider = vision_cap.get("provider") or llm.get("provider")
        if not provider:
            return None, f"Preset {preset_ref!r} declares no vision provider.", {}
        identity = {
            "provider": llm.get("provider") or provider,
            "model": llm.get("model"),
            "base_url": llm.get("base_url"),
        }

        preset_provider = llm.get("provider") or provider
        # Carry the preset's own Codex identity into the identity bucket so a
        # borrowed ``codex`` preset binds its ``codex_auth_path`` rather than
        # the default token file.
        preset_defaults: dict = {}
        preset_auth_path = _normalize_codex_auth_path(llm.get("codex_auth_path"))
        if preset_auth_path and isinstance(preset_provider, str):
            preset_defaults[preset_provider.lower()] = {"codex_auth_path": preset_auth_path}

        class _PresetIdentity:
            provider = preset_provider
            _model = llm.get("model")
            _base_url = llm.get("base_url")
            # A preset that omits ``base_url`` uses its provider's default
            # endpoint; the vision service then applies the same SDK default.
            effective_base_url = llm.get("base_url")
            api_key = None
            _provider_defaults: dict = preset_defaults

        kwargs = dict(vision_cap)
        for key in ("model", "base_url", "api_key_env", "wire_api"):
            if key in llm and key not in kwargs:
                kwargs[key] = llm[key]
        api_key = kwargs.pop("api_key", None)
        api_key_env = kwargs.pop("api_key_env", None)
        # ``provider`` is passed positionally below; drop the capability copy so
        # ``_resolve_direct_service`` never receives it twice (TypeError).
        kwargs.pop("provider", None)
        service, service_reason = _resolve_direct_service(
            self._workdir,
            self._active_provider,
            provider,
            api_key=api_key,
            api_key_env=api_key_env,
            identity_service=_PresetIdentity(),
            **kwargs,
        )
        return service, service_reason, identity

    def _dispatch_analyze(self, action_input: Mapping[str, Any]) -> dict[str, Any]:
        """Run the one direct analyze operation on already-validated input.

        The body is the pre-migration ``handle()`` analyze path unchanged:
        same missing-service guard, relative-path resolution, existence check,
        default prompt, and success/failure result shapes. When the call
        carries the optional ``preset`` option, a borrowed service is built for
        this call from that preset's vision configuration instead of the
        default route.
        """
        preset_ref = action_input.get("preset") if isinstance(action_input, Mapping) else None
        if preset_ref:
            borrowed, borrow_reason, _identity = self._build_service_from_preset(preset_ref)
            if borrowed is None:
                return {
                    "status": "error",
                    "message": (
                        f"{borrow_reason} Load the vision manual skill for the "
                        "borrowing steps: vision(action='manual', input={}, "
                        "reasoning='preset vision unavailable, load the setup "
                        "steps'); then ask the human for consent before "
                        "changing preset authorization."
                    ),
                }
            service = borrowed
        else:
            service = self._vision_service
        if service is None:
            reason = self._manual_reason or (
                "Direct vision is unavailable; call vision(action='manual', "
                "input={}, reasoning='no direct vision route, load the "
                "manual alternatives')."
            )
            return {
                "status": "error",
                "message": f"{reason} {_consent_guidance()}",
            }
        image_path = action_input.get("image_path") or ""
        question = action_input.get("question")
        if question is None:
            question = "Describe what you see in this image."

        if not image_path:
            return {"status": "error", "message": "Provide image_path"}

        path = Path(image_path)
        if not path.is_absolute():
            path = self._workdir.path / path

        if not path.is_file():
            return {"status": "error", "message": f"Image file not found: {path}"}

        try:
            analysis = service.analyze_image(str(path), prompt=question)
            if not analysis:
                return {
                    "status": "error",
                    "message": "Vision analysis returned no response.",
                }
            return {"status": "ok", "analysis": analysis}
        except Exception as e:
            if preset_ref:
                route = "borrowed preset vision route"
                hint = (
                    "The borrowed preset's vision service failed for this "
                    "image; verify the preset is authorized and its provider "
                    "supports images."
                )
            else:
                route = "default vision route"
                hint = (
                    "The default route is the current provider's own "
                    "endpoint and model, which may not support images."
                )
            return {
                "status": "error",
                "message": (
                    f"Vision analysis failed on the {route} ({type(e).__name__}). "
                    f"{hint} Alternative vision may be available: the current "
                    "provider's MCP, a borrowed preset via the analyze "
                    "preset option, or a local OpenAI-compatible vision "
                    "server via provider='local'. Load the vision manual skill "
                    "for the setup alternatives: vision(action='manual', "
                    "input={}, reasoning='vision failed, load the setup "
                    "alternatives'); then ask the human for consent "
                    "before setting one up."
                ),
            }

    def _dispatch_check(self, action_input: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve which vision route actually works without sending an image.

        With the optional ``preset`` field, this borrows that preset's vision
        service the same way ``analyze`` would (authorization check, preset
        load, provider identity) and reports the resolved provider/model; the
        service is constructed but no image request is made, so it never costs
        a provider call. Without ``preset``, it reports whether the default
        route (configured service or the active LLM provider's own endpoint)
        is available. A failure returns a sanitized error pointing at the
        manual.
        """
        preset_ref = action_input.get("preset") if isinstance(action_input, Mapping) else None
        if preset_ref:
            borrowed, borrow_reason, identity = self._build_service_from_preset(preset_ref)
            if borrowed is None:
                return {
                    "status": "error",
                    "message": (
                        f"{borrow_reason} Load the vision manual skill for the "
                        "borrowing steps: vision(action='manual', input={}, "
                        "reasoning='preset vision unavailable, load the setup "
                        "steps'); then ask the human for consent before "
                        "changing preset authorization."
                    ),
                }
            return {
                "status": "ok",
                "route": f"preset:{preset_ref}",
                "provider": identity.get("provider"),
                "model": identity.get("model"),
            }
        if self._vision_service is None:
            reason = self._manual_reason or (
                "Direct vision is unavailable; call vision(action='manual', "
                "input={}, reasoning='no direct vision route, load the "
                "manual alternatives')."
            )
            return {
                "status": "error",
                "message": f"{reason} {_consent_guidance()}",
            }
        active_service = self._active_provider.service
        return {
            "status": "ok",
            "route": "default",
            "provider": getattr(active_service, "provider", None),
            "model": getattr(active_service, "model", None),
        }

    def _dispatch_list(self, action_input: Mapping[str, Any]) -> dict[str, Any]:
        # mechanical enumeration; never constructs a service or makes a provider call
        import json as _json
        from lingtai.kernel.presets import load_preset

        active_service = self._active_provider.service
        active_provider = getattr(active_service, "provider", None)
        active_model = getattr(active_service, "_model", None)
        default_endpoint = _vision_endpoint(active_provider)
        default = {
            "provider": active_provider,
            "model": active_model,
            "configured": self._vision_service is not None,
            "supports_vision": bool(self._vision_service is not None or default_endpoint != "unknown"),
            "endpoint": default_endpoint,
            "responses_vision": _responses_vision(active_provider),
        }
        allowed: list[str] = []
        init_path = Path(self._workdir.path) / "init.json"
        if init_path.is_file():
            try:
                init_data = _json.loads(init_path.read_text(encoding="utf-8"))
                manifest = init_data.get("manifest") or {}
                # One row per physical preset. A raw ``~/x.json`` entry and
                # its expanded absolute path are the same file, so key on the
                # canonical path and keep the first declared spelling, which
                # is exactly what ``analyze``/``check`` accept as ``preset``.
                by_path: dict[str, str] = {}
                for ref in manifest.get("preset", {}).get("allowed") or []:
                    if isinstance(ref, str) and ref:
                        by_path.setdefault(_canonical_preset_path(ref, self._workdir.path), ref)
                allowed = sorted(by_path.values())
            except Exception:
                allowed = []
        presets: list[dict[str, Any]] = []
        for ref in allowed:
            try:
                preset = load_preset(ref, working_dir=self._workdir.path, run_migrations=lambda _path: None)
            except Exception:
                continue
            pm = preset.get("manifest") or {}
            llm = pm.get("llm") or {}
            vision_cap = (pm.get("capabilities") or {}).get("vision") or {}
            provider = vision_cap.get("provider") or llm.get("provider")
            if not provider:
                continue
            presets.append({
                "preset": ref,
                "provider": provider,
                "model": vision_cap.get("model") or llm.get("model"),
                "endpoint": _vision_endpoint(provider),
                "responses_vision": _responses_vision(provider),
            })
        return {"status": "ok", "default": default, "presets": presets, "count": len(presets)}

    def _adapt_manual_result(self, mcp_result: dict[str, Any]) -> dict[str, Any]:
        # Host-owned flattening of the manual child's canonical result into
        # vision's pre-migration ``status``/``action``/``manual`` shape (plus
        # the loader's ``manual_path``). See ``handle()`` for the ordering rule.
        flat: dict[str, Any] = {
            "status": mcp_result.get("status", "ok"),
            "action": "manual",
            "manual": mcp_result["content"][0]["text"],
            "manual_path": mcp_result["structuredContent"]["manual_path"],
        }
        if "error" in mcp_result:
            flat["error"] = mcp_result["error"]
        return flat

    def manual(self) -> dict:
        """Return only installed guidance; never inspect config or invoke a backend.

        Retained as the family's own public manual entry point (callers and
        tests use it directly). Performs no provider construction, no
        credential read, and no analyze operation.
        """
        return self._adapt_manual_result(self._family.handle({"action": "manual", "input": {}}))

    def handle(self, args: dict | None) -> dict:
        # Canonical statement of this family's dispatch/presentation ordering.
        # The generic ``ToolFamily`` dispatcher validates ``action``,
        # type-checks and strips root ``summarize``, rejects unknown root
        # fields, and rejects ``input`` keys outside the selected action's own
        # declared schema (schema conformance alone is not the dispatch-time
        # authorization boundary — see ``tools/CONTRACT.md`` "Dispatch and
        # actions") before ``_dispatch_analyze`` or the registered ``manual``
        # child's handler ever runs, so every envelope failure lands before any
        # provider I/O. ``self._family.handle`` returns the ``manual`` child's
        # canonical ``content``/``structuredContent`` result verbatim (no double
        # wrap); flattening it to vision's public shape is this method's own
        # Host job, done strictly after dispatch, never inside a registered
        # child. Envelope failures are normalized to vision's long-standing
        # ``{"status": "error", "message": ...}`` shape here, rather than by
        # changing the generic dispatcher's canonical error result.
        action = args.get("action") if isinstance(args, Mapping) else None
        result = self._family.handle(args)
        if action == "manual" and "content" in result:
            return self._adapt_manual_result(result)
        if (
            action != "settings"
            and result.get("status") == "failed"
            and "error_code" in result
        ):
            return {"status": "error", "message": result["message"]}
        return result



def _bind(host: "ToolPluginHost") -> BoundToolPlugin:
    """Compose Vision against only its granted ports; mount nothing.

    Provider resolution retains the previous active-provider behavior, but all
    Agent reads flow through ``workdir`` and ``active_provider``. Explicit
    capability kwargs arrive as one opaque configuration port rather than by
    reaching through the Agent. Construction creates no transport, process, or
    prompt side effect; the kernel registrar alone activates and mounts.
    """
    configuration = VisionConfiguration.from_port_values(host.configuration.values)
    vision_service = configuration.vision_service
    provider = configuration.provider
    manual_reason = ""
    active_service = host.active_provider.service if vision_service is None else None
    if vision_service is None and provider is None:
        active_name = getattr(active_service, "provider", "")
        if isinstance(active_name, str) and active_name.strip():
            provider = active_name

    local_settings: LocalVisionSettings | None = None
    settings_failure: Exception | None = None
    resolved_api_key = configuration.api_key
    resolved_route = vision_service is None
    if resolved_route and configuration.api_key_env:
        from lingtai.kernel.config_resolve import resolve_env

        resolved_api_key = resolve_env(
            configuration.api_key, configuration.api_key_env
        )
    if resolved_route and isinstance(provider, str) and provider.lower() == "local":
        from .settings import read_local_settings

        try:
            local_settings = read_local_settings(host.workdir)
        except SettingsError as exc:
            settings_failure = exc
    if vision_service is None and provider is not None:
        vision_service, manual_reason = _resolve_direct_service(
            host.workdir,
            host.active_provider,
            provider,
            api_key=resolved_api_key,
            identity_service=active_service,
            local_settings=local_settings,
            local_settings_error=(
                settings_failure if isinstance(settings_failure, SettingsError) else None
            ),
            **dict(configuration.kwargs),
        )
    elif vision_service is None:
        manual_reason = (
            "No direct vision provider was configured; use vision(action='manual', "
            "input={}, reasoning='no direct vision provider is configured')."
        )
    manager = VisionManager(
        host.workdir,
        host.active_provider,
        vision_service=vision_service,
        manual_reason=manual_reason,
        settings_provider=_vision_settings_provider(
            configuration,
            provider if resolved_route else None,
            active_service,
            vision_service,
            local_settings,
            settings_failure,
        ),
    )
    return BoundToolPlugin(
        name=DECLARATION.name,
        schema=get_schema(),
        handler=manager,
        description=DECLARATION.description,
        glossary_package=DECLARATION.glossary_package,
    )


#: Static official declaration. The schema-only family below and every bound
#: manager derive identity, action schemas, and installed manual destination
#: from this one object; the kernel verifies their advertised actions at bind.
DECLARATION = ToolPluginDeclaration(
    name="vision",
    actions=("analyze", "check", "list"),
    input_schemas={
        "analyze": _ANALYZE_INPUT_SCHEMA,
        "check": _CHECK_INPUT_SCHEMA,
        "list": _LIST_INPUT_SCHEMA,
    },
    manual_input_schema=MANUAL_INPUT_SCHEMA,
    manual="vision",
    description=_DESCRIPTION,
    binder=_bind,
    requires=("workdir", "active_provider", "configuration"),
    glossary_package=__package__,
    settings=True,
)


#: Import-time schema-only composition catches a malformed fixed child registry
#: before an Agent exists. Runtime binding builds the same declaration-derived
#: family with real handlers and its installed manual child.
_FAMILY = _build_family()


def _resolve_direct_service(
    workdir: "WorkdirPort",
    active_provider: "ActiveProviderPort",
    provider: str,
    api_key: str | None = None,
    api_key_env: str | None = None,
    *,
    identity_service: Any = None,
    local_settings: LocalVisionSettings | None = None,
    local_settings_error: SettingsError | None = None,
    **kwargs: Any,
) -> tuple["VisionService | None", str]:
    """Resolve a direct VisionService from provider + kwargs.

    Supported routes are exactly the four LLM families plus the explicit local
    pseudo-providers: ``openai`` / ``anthropic`` (any compatible endpoint),
    ``codex`` (one OAuth identity), ``claude-code`` / ``claude-p`` (manual
    ``claude -p`` guidance), ``local`` (a local OpenAI-compatible server), and
    ``mlx`` (on-device). Every other provider name is manual-only; there is no
    fallback provider.

    ``identity_service`` overrides the active-provider port's service used for
    provider identity, model/endpoint/credential inheritance, and the
    provider-default bucket. Preset borrowing passes a lightweight identity
    shim built from the borrowed preset's ``manifest.llm`` so the borrowed
    provider resolves its own route and credentials instead of the active
    provider's.
    """
    vision_service: "VisionService | None" = None
    manual_reason = ""
    # ``api_compat`` belonged to the retired ``custom`` provider; a legacy
    # capability/preset value is accepted and ignored.
    kwargs.pop("api_compat", None)
    if api_key_env:
        from lingtai.kernel.config_resolve import resolve_env
        api_key = resolve_env(api_key, api_key_env)
    provider_key = provider.strip().lower()
    active_service = identity_service if identity_service is not None else active_provider.service
    active_provider_name = getattr(active_service, "provider", "")
    active_provider_key = (
        active_provider_name.lower() if isinstance(active_provider_name, str) else ""
    )
    same_provider = _same_provider_identity(provider_key, active_provider_key)
    active_model = getattr(active_service, "_model", None) if same_provider else None
    active_base_url = getattr(active_service, "_base_url", None) if same_provider else None
    if provider_key == "mlx":
        # Native Apple-MLX on-device vision is an explicit pseudo-provider:
        # keep it out of PROVIDERS/check-caps, but preserve the documented
        # opt-in route. Its constructor accepts only model/max_tokens and
        # needs no key.
        mlx_kwargs = {
            key: kwargs[key]
            for key in ("model", "max_tokens")
            if key in kwargs and kwargs[key] is not None
        }
        from lingtai.services.vision import create_vision_service
        try:
            vision_service = create_vision_service(
                "mlx",
                api_key=None,
                **mlx_kwargs,
            )
        except Exception as exc:
            manual_reason = _setup_failure(provider, exc)
    elif provider_key == "local":
        # Local is a generic OpenAI-compatible vision server on this
        # machine (Ollama, LM Studio, vLLM, llama.cpp, ...). The endpoint
        # is operator-owned and configured in settings/vision.json
        # (base_url/model/api_key/max_tokens); capability kwargs override
        # the file. base_url defaults to the standard local port. model is
        # REQUIRED — no hardcoded default, because a silently assumed
        # model masks misconfiguration; when it is missing we surface
        # guided setup steps instead. api_key is optional: local servers
        # ignore it, so a placeholder satisfies the OpenAI SDK.
        from lingtai.services.vision.openai import OpenAIVisionService
        from .settings import read_local_settings
        try:
            if local_settings_error is not None:
                raise local_settings_error
            if local_settings is None:
                local_settings = read_local_settings(workdir)
        except SettingsError as exc:
            manual_reason = (
                f"Local vision settings are invalid: {exc}; fix "
                "settings/vision.json or pass provider='local' with "
                "base_url/model kwargs; see vision(action='manual', input={}, "
                "reasoning='local vision settings are invalid')."
            )
        else:
            local_base_url = (
                kwargs.get("base_url")
                or local_settings.base_url
                or DEFAULT_LOCAL_BASE_URL
            )
            local_model = kwargs.get("model") or local_settings.model
            if not local_model:
                manual_reason = (
                    "Local vision needs an explicit model. Load the vision "
                    "manual skill: vision(action='manual', input={}, "
                    "reasoning='local vision setup'); then ask the human "
                    "for consent before setting it up."
                )
            else:
                local_key = api_key or local_settings.api_key or "local"
                local_wire = _effective_openai_wire(kwargs.get("wire_api"))
                svc_kwargs: dict = {
                    "api_key": local_key,
                    "model": local_model,
                    "base_url": local_base_url,
                }
                if local_wire:
                    svc_kwargs["wire_api"] = local_wire
                cap_max_tokens = kwargs.get("max_tokens")
                if cap_max_tokens is None:
                    cap_max_tokens = local_settings.max_tokens
                if cap_max_tokens is not None:
                    svc_kwargs["max_tokens"] = cap_max_tokens
                try:
                    vision_service = OpenAIVisionService(**svc_kwargs)
                except Exception as exc:
                    manual_reason = _setup_failure(provider, exc)
    elif provider_key in _CLAUDE_CLI_FAMILY:
        # The claude backend uses the Claude Code CLI for vision. LingTai
        # does not proxy the CLI's own authentication (claude.ai
        # subscription, API key, configured provider), so there is no
        # direct service to construct: the agent is told to run
        # ``claude -p`` and read the vision manual for the exact steps.
        manual_reason = (
            "You are using claude as backend, therefore to use vision run "
            "`claude -p`; see the vision manual for more details: "
            "vision(action='manual', input={}, reasoning='claude vision "
            "details')."
        )
    elif provider_key in _CODEX_FAMILY:
        # Codex vision is a standalone Responses request. It may share
        # the active Codex provider's model and endpoint, but never
        # inherits those from an unrelated main provider. The OAuth
        # identity mirrors the canonical Codex factory: an explicit
        # ``token_path``, else the active bucket's ``codex_auth_path``,
        # else (active Codex service only) the default token file.
        if same_provider:
            if active_model:
                kwargs.setdefault("model", active_model)
            if active_base_url:
                kwargs.setdefault("base_url", active_base_url)
        codex_base_url = kwargs.get("base_url")

        defaults = getattr(active_service, "_provider_defaults", None) if same_provider else None
        bucket = defaults.get(active_provider_key) if isinstance(defaults, dict) else None
        if not isinstance(bucket, dict):
            bucket = {}
        # Normalize an explicit capability identity. This preserves a valid
        # independent token path while ensuring a whitespace-only value
        # cannot bypass the fail-closed branch.
        explicit_token_path = _normalize_codex_auth_path(kwargs.pop("token_path", None))
        token_path = explicit_token_path or _normalize_codex_auth_path(
            bucket.get("codex_auth_path")
        )
        if not token_path and same_provider:
            from lingtai.auth.codex import default_codex_token_path

            token_path = str(default_codex_token_path())
        if not kwargs.get("model"):
            manual_reason = f"Provider {provider!r} has no resolved current model for direct vision; use vision(action='manual', input={{}}, reasoning='no resolved current model for direct vision')."
        elif token_path:
            kwargs["token_path"] = token_path
        else:
            # An unrelated active provider never supplies a Codex identity.
            manual_reason = "Codex vision has no explicit current OAuth identity; use vision(action='manual', input={}, reasoning='Codex vision has no explicit current OAuth identity')."
        kwargs.pop("base_url", None)
        if codex_base_url:
            kwargs["base_url"] = codex_base_url
        if not manual_reason:
            from lingtai.services.vision import create_vision_service
            try:
                vision_service = create_vision_service("codex", api_key=None, **kwargs)
            except Exception as exc:
                manual_reason = _setup_failure(provider, exc)
    elif provider_key in _API_FAMILIES:
        vision_service, manual_reason = _resolve_api_family_service(
            provider,
            provider_key,
            api_key=api_key,
            active_service=active_service,
            active_provider_key=active_provider_key,
            same_provider=same_provider,
            kwargs=kwargs,
        )
    else:
        manual_reason = f"No direct vision route is supported for provider {provider!r}; use vision(action='manual', input={{}}, reasoning='this provider has no supported direct vision route')."
    return vision_service, manual_reason


def _resolve_api_family_service(
    provider: str,
    provider_key: str,
    *,
    api_key: str | None,
    active_service: Any,
    active_provider_key: str,
    same_provider: bool,
    kwargs: Mapping[str, Any],
) -> tuple["VisionService | None", str]:
    """Build the ``openai``/``anthropic`` Vision service for one route.

    On the active provider's own family the route inherits that service's
    effective endpoint (``effective_base_url`` — the adapter's own default when
    the manifest omitted ``base_url``), model, credential, provider-default
    headers, and (``openai``) wire. Explicit capability values win. The active
    credential is sent ONLY to the active effective endpoint: a capability that
    names a different ``base_url`` must supply its own ``api_key``/
    ``api_key_env``. A different active family lends nothing.
    """
    bucket: Mapping[str, Any] = {}
    if same_provider:
        defaults = getattr(active_service, "_provider_defaults", None)
        if isinstance(defaults, dict):
            candidate = defaults.get(active_provider_key)
            if isinstance(candidate, dict):
                bucket = candidate
    active_model = getattr(active_service, "_model", None) if same_provider else None
    active_endpoint = (
        _active_effective_base_url(active_service) or bucket.get("base_url")
        if same_provider
        else None
    )
    active_api_key = getattr(active_service, "api_key", None) if same_provider else None

    cap_base_url = kwargs.get("base_url")
    base_url = cap_base_url or active_endpoint
    model = kwargs.get("model") or active_model or bucket.get("model")
    own_key = api_key if isinstance(api_key, str) and api_key.strip() else None
    resolved_key = own_key
    leak_blocked = False
    if resolved_key is None and isinstance(active_api_key, str) and active_api_key:
        if cap_base_url and _endpoint_key(cap_base_url) != _endpoint_key(active_endpoint):
            leak_blocked = True
        else:
            resolved_key = active_api_key
    headers = kwargs.get("default_headers") or bucket.get("default_headers")
    max_tokens = kwargs.get("max_tokens")

    wire_api: str | None = None
    if provider_key == "openai":
        wire_selector = kwargs.get("wire_api")
        if wire_selector is None:
            wire_selector = bucket.get("wire_api")
        wire_api = _effective_openai_wire(wire_selector)
        if wire_api is None:
            return None, (
                "The active OpenAI-compatible wire is not implemented by the "
                "direct vision service; use vision(action='manual', input={}, "
                "reasoning='the active OpenAI-compatible wire has no direct "
                "vision route')."
            )
    if not isinstance(model, str) or not model.strip():
        return None, (
            f"Provider {provider!r} has no resolved current model for direct "
            "vision; use vision(action='manual', input={}, reasoning='no "
            "resolved current model for direct vision')."
        )
    if resolved_key is None:
        detail = (
            " (the active credential is only sent to the active endpoint; "
            "give this vision endpoint its own api_key/api_key_env)"
            if leak_blocked
            else ""
        )
        return None, (
            f"Provider {provider!r} has no resolved current credential for "
            f"direct vision{detail}; use vision(action='manual', input={{}}, "
            "reasoning='no resolved current credential for direct vision')."
        )
    svc_kwargs: dict[str, Any] = {"model": model}
    if base_url:
        svc_kwargs["base_url"] = base_url
    if headers:
        svc_kwargs["default_headers"] = headers
    if wire_api is not None:
        svc_kwargs["wire_api"] = wire_api
    if max_tokens is not None:
        svc_kwargs["max_tokens"] = max_tokens
    # Lazy import: the provider service lives in ``lingtai.services``.
    from lingtai.services.vision import create_vision_service
    try:
        return (
            create_vision_service(provider_key, api_key=resolved_key, **svc_kwargs),
            "",
        )
    except Exception as exc:
        return None, _setup_failure(provider, exc)


def setup(
    agent: "BaseAgent",
    vision_service: "VisionService | None" = None,
    provider: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    **kwargs: Any,
) -> VisionManager:
    """Register Vision through its declared host-plugin route.

    ``vision`` remains always registered. Its public capability kwargs are
    carried as a configuration port; the binder resolves the default active
    provider through its one narrow read port, then the registrar mounts the
    resulting handler under the kernel-reserved ``vision`` name. No generic
    ``Agent.add_tool`` path is available to this official family.
    """
    from lingtai.adapters.tool_plugin_host import (
        StaticConfigurationAdapter,
        register_agent_tool_plugins,
    )

    configuration = StaticConfigurationAdapter(
        VisionConfiguration(
            vision_service=vision_service,
            provider=provider,
            api_key=api_key,
            api_key_env=api_key_env,
            kwargs=dict(kwargs),
        ).port_values()
    )
    (bound,) = register_agent_tool_plugins(
        agent,
        [DECLARATION],
        # The snapshot is granted to this declaration alone, through the same
        # setup-selected seam Shell uses; it is never added to the standard
        # table for every family.
        extra_ports_for=lambda declaration: (
            {"configuration": configuration} if declaration is DECLARATION else {}
        ),
    )
    if not isinstance(bound.handler, VisionManager):  # pragma: no cover - declaration invariant
        raise ToolPluginDeclarationError("vision declaration bound a non-Vision handler")
    return bound.handler
