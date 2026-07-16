"""Vision capability — image understanding via VisionService.

Adds the ability to analyze images. Requires a VisionService instance,
created either explicitly or via the ``provider``/``api_key`` factory.

Usage:
    agent.add_capability("vision", vision_service=my_svc)
    agent.add_capability("vision", provider="anthropic", api_key="sk-...")

The local mlx-vlm pseudo-provider remains available through explicit
``add_capability(..., provider="local")`` opt-in, but it is intentionally not
advertised in ``PROVIDERS`` or first-run/check-caps surfaces yet.
"""
from __future__ import annotations

from pathlib import Path
from importlib import resources
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent
    from lingtai.services.vision import VisionService


def _setup_failure(provider: str, exc: BaseException) -> str:
    """Build explicit manual guidance without exposing exception contents."""
    return (
        f"Direct vision setup failed for provider {provider!r} "
        f"({type(exc).__name__}); use vision(action='manual')."
    )


def _same_provider_identity(requested: str, active: str) -> bool:
    """Return whether two provider names identify the same current route."""
    if requested == active:
        return True
    return {requested, active} <= {"glm", "zhipu"} or {
        requested,
        active,
    } <= {"codex-pool", "codex_pool"}


def _effective_openai_wire(
    wire_api: str | None,
    *,
    use_responses_api: bool,
    base_url: str | None,
) -> str | None:
    """Resolve a supported canonical wire; reject unknown protocols."""
    normalized = wire_api.strip().lower() if isinstance(wire_api, str) else wire_api
    if isinstance(normalized, str):
        if normalized in {"chat_completions", "responses"}:
            return normalized
        if normalized in {"", "auto"}:
            return "responses" if use_responses_api and not base_url else "chat_completions"
    elif normalized is None:
        return "responses" if use_responses_api and not base_url else "chat_completions"
    return None


PROVIDERS = {
    "providers": [
        "gemini", "anthropic", "openai", "openrouter", "custom", "deepseek",
        "minimax", "mimo", "glm", "zhipu", "grok", "qwen", "kimi",
        "codex", "codex-pool", "codex_pool", "claude-code", "claude_code",
    ],
    "default": None,
    "fallback_on_inherit": None,  # no agnostic fallback for vision
}

def get_description(lang: str = "en") -> str:
    return "Analyze an image with the active preset when directly supported. Use action='manual' for read-only guidance when unsupported or after a direct failure. No provider or MCP fallback is automatic."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": 'Path to the image file'},
            "question": {
                "type": "string",
                "description": 'Question about the image',
                "default": "Describe this image.",
            },
            "action": {
                "type": "string",
                "enum": ["analyze", "manual"],
                "default": "analyze",
                "description": "manual returns bundled read-only vision guidance; analyze performs the direct request.",
            },
        },
        "required": [],
    }



class VisionManager:
    """Handles vision tool calls via a VisionService."""

    def __init__(
        self,
        agent: "BaseAgent",
        vision_service: VisionService | None,
        manual_reason: str = "",
    ) -> None:
        self._agent = agent
        self._vision_service = vision_service
        self._manual_reason = manual_reason

    def handle(self, args: dict) -> dict:
        if args.get("action", "analyze") == "manual":
            return self.manual()
        if self._vision_service is None:
            return {"status": "error", "message": self._manual_reason or "Direct vision is unavailable; call vision(action='manual')."}
        image_path = args.get("image_path", "")
        question = args.get("question", "Describe what you see in this image.")

        if not image_path:
            return {"status": "error", "message": "Provide image_path"}

        path = Path(image_path)
        if not path.is_absolute():
            path = self._agent._working_dir / path

        if not path.is_file():
            return {"status": "error", "message": f"Image file not found: {path}"}

        try:
            analysis = self._vision_service.analyze_image(str(path), prompt=question)
            if not analysis:
                return {
                    "status": "error",
                    "message": "Vision analysis returned no response.",
                }
            return {"status": "ok", "analysis": analysis}
        except Exception as e:
            return {"status": "error", "message": f"Vision analysis failed ({type(e).__name__}). Call vision(action='manual') for the explicit manual route."}

    def manual(self) -> dict:
        """Return only bundled guidance; never inspect config or invoke a backend."""
        try:
            body = resources.files(__package__).joinpath("manual/SKILL.md").read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError, AttributeError):
            return {"status": "degraded", "action": "manual", "manual": "", "error": "vision manual missing"}
        return {"status": "ok", "action": "manual", "manual": body}


def setup(
    agent: "BaseAgent",
    vision_service: VisionService | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    **kwargs: Any,
) -> VisionManager:
    """Set up the vision capability on an agent.

    Requires either ``vision_service`` or ``provider`` + ``api_key`` for a
    direct route. Without one, the tool is still registered for manual guidance.
    """
    manual_reason = ""
    if vision_service is None and provider is not None:
        if api_key_env:
            from lingtai.kernel.config_resolve import resolve_env
            api_key = resolve_env(api_key, api_key_env)
        provider_key = provider.lower()
        active_service = getattr(agent, "service", None)
        active_provider = getattr(active_service, "provider", "")
        active_provider_key = active_provider.lower() if isinstance(active_provider, str) else ""
        same_provider = _same_provider_identity(provider_key, active_provider_key)
        active_model = getattr(active_service, "_model", None) if same_provider else None
        active_base_url = getattr(active_service, "_base_url", None) if same_provider else None
        active_api_key = getattr(active_service, "api_key", None) if same_provider else None
        if provider_key == "local":
            # Local vision is an explicit pseudo-provider: keep it out of
            # PROVIDERS/check-caps, but preserve the documented opt-in route.
            # Its constructor accepts only model/max_tokens and needs no key.
            local_kwargs = {
                key: kwargs[key]
                for key in ("model", "max_tokens")
                if key in kwargs and kwargs[key] is not None
            }
            from lingtai.services.vision import create_vision_service
            try:
                vision_service = create_vision_service(
                    "local",
                    api_key=None,
                    **local_kwargs,
                )
            except Exception as exc:
                manual_reason = _setup_failure(provider, exc)
        elif provider_key not in PROVIDERS["providers"]:
            # No dedicated VisionService for this provider (custom relay,
            # OpenRouter, an anthropic-compat local proxy, ...). Route vision
            # through the OpenAI- or Anthropic-compatible service, picking the
            # wire protocol and endpoint from, in order:
            #   1. capability kwargs — explicit init.json override. This lets a
            #      user point vision at a *different*, vision-capable model
            #      (e.g. Kimi-K2.6 on a multi-model proxy) while the main LLM
            #      stays on a text-only model (e.g. GLM-5.1).
            #   2. the main LLM: api_compat from service._provider_defaults
            #      (shaped {provider_name: defaults_dict}), base_url/model from
            #      service._base_url / service._model.
            # If the relay or model can't actually do vision, the call fails at
            # runtime — capability registration never pre-checks.
            bucket = {}
            api_compat = (kwargs.get("api_compat") or "").lower()
            if not api_compat:
                defaults = getattr(active_service, "_provider_defaults", None) if same_provider else None
                if isinstance(defaults, dict):
                    # _provider_defaults is dict[provider_name, defaults_dict];
                    # read only the active provider's bucket, never another
                    # provider's credential/transport configuration.
                    bucket = defaults.get(active_provider_key)
                    if isinstance(bucket, dict):
                        api_compat = (bucket.get("api_compat") or "").lower()

            cap_model = kwargs.get("model")
            cap_base_url = kwargs.get("base_url")
            cap_max_tokens = kwargs.get("max_tokens")
            bucket = bucket if isinstance(bucket, dict) else {}
            llm_base_url = cap_base_url or active_base_url or bucket.get("base_url")
            llm_model = cap_model or active_model or bucket.get("model")
            api_key = api_key or active_api_key
            headers = kwargs.get("default_headers") or bucket.get("default_headers")
            wire_api = _effective_openai_wire(
                kwargs.get("wire_api") or bucket.get("wire_api"),
                use_responses_api=bucket.get("use_responses_api") is True,
                base_url=llm_base_url,
            )

            if api_compat == "openai":
                from lingtai.services.vision.openai import OpenAIVisionService
                svc_kwargs: dict = {
                    "api_key": api_key,
                    "model": llm_model,
                    "base_url": llm_base_url,
                }
                if headers:
                    svc_kwargs["default_headers"] = headers
                if wire_api and wire_api != "auto":
                    svc_kwargs["wire_api"] = wire_api
                if cap_max_tokens is not None:
                    svc_kwargs["max_tokens"] = cap_max_tokens
                if wire_api is None:
                    manual_reason = "The active OpenAI-compatible wire is not implemented by the direct vision service; use vision(action='manual')."
                elif not llm_model:
                    manual_reason = f"Provider {provider!r} has no resolved current model for direct vision; use vision(action='manual')."
                elif not api_key:
                    manual_reason = f"Provider {provider!r} has no resolved current credential for direct vision; use vision(action='manual')."
                else:
                    try:
                        vision_service = OpenAIVisionService(**svc_kwargs)
                    except Exception as exc:
                        manual_reason = _setup_failure(provider, exc)
            elif api_compat == "anthropic":
                from lingtai.services.vision.anthropic import AnthropicVisionService
                svc_kwargs = {
                    "api_key": api_key,
                    "model": llm_model,
                    "base_url": llm_base_url,
                }
                if headers:
                    svc_kwargs["default_headers"] = headers
                if cap_max_tokens is not None:
                    svc_kwargs["max_tokens"] = cap_max_tokens
                if not llm_model:
                    manual_reason = f"Provider {provider!r} has no resolved current model for direct vision; use vision(action='manual')."
                elif not api_key:
                    manual_reason = f"Provider {provider!r} has no resolved current credential for direct vision; use vision(action='manual')."
                else:
                    try:
                        vision_service = AnthropicVisionService(**svc_kwargs)
                    except Exception as exc:
                        manual_reason = _setup_failure(provider, exc)
            else:
                manual_reason = f"No direct vision route is supported for provider {provider!r}; use vision(action='manual')."
        else:
            if provider_key in {"codex", "codex-pool", "codex_pool"}:
                # Codex vision is a standalone Responses request. It may share
                # the active Codex family's model and endpoint, but never
                # inherits those from an unrelated main provider.
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
                if not kwargs.get("model"):
                    manual_reason = f"Provider {provider!r} has no resolved current model for direct vision; use vision(action='manual')."
                elif provider_key == "codex":
                    token_path = kwargs.pop("token_path", None) or bucket.get("codex_auth_path")
                    if token_path:
                        kwargs["token_path"] = token_path
                    else:
                        manual_reason = "Codex vision has no explicit current OAuth identity; use vision(action='manual')."
                else:
                    # The pool selector is the single owner of deterministic
                    # account choice. It reads only the current pool's
                    # non-secret file; an unrelated active provider is ignored.
                    if same_provider:
                        from lingtai.auth.codex_pool import select_codex_pool_auth
                        selection = select_codex_pool_auth(
                            bucket,
                            model=kwargs.get("model"),
                        )
                        if selection:
                            kwargs["token_path"] = selection["auth_path"]
                    if not kwargs.get("token_path"):
                        manual_reason = "Codex pool vision has no selected current OAuth identity; use vision(action='manual')."
                kwargs.pop("api_compat", None)
                kwargs.pop("base_url", None)
                if codex_base_url:
                    kwargs["base_url"] = codex_base_url
                if not manual_reason:
                    from lingtai.services.vision import create_vision_service
                    try:
                        vision_service = create_vision_service("codex", api_key=None, **kwargs)
                    except Exception as exc:
                        manual_reason = _setup_failure(provider, exc)
            else:
                service_provider = provider_key
                defaults = getattr(active_service, "_provider_defaults", {}) if same_provider else {}
                bucket = defaults.get(active_provider_key, {}) if isinstance(defaults, dict) else {}
                active_base_url = active_base_url or (bucket.get("base_url") if isinstance(bucket, dict) else None)
                active_headers = bucket.get("default_headers") if isinstance(bucket, dict) else None
                active_compat = kwargs.get("api_compat") or (bucket.get("api_compat") if isinstance(bucket, dict) else "") or ""
                wire_api = _effective_openai_wire(
                    kwargs.get("wire_api") or (bucket.get("wire_api") if isinstance(bucket, dict) else None),
                    use_responses_api=isinstance(bucket, dict) and bucket.get("use_responses_api") is True,
                    base_url=kwargs.get("base_url") or active_base_url,
                )
                if service_provider in {"openrouter", "deepseek", "zhipu", "glm", "grok", "qwen", "kimi"}:
                    service_provider = "anthropic" if active_compat.lower() == "anthropic" else "openai"
                elif service_provider == "custom":
                    service_provider = "anthropic" if active_compat.lower() == "anthropic" else "openai"

                # Provider-specific kwarg injection. Each branch is opt-in because
                # vision services have heterogeneous constructor signatures.
                if service_provider == "minimax":
                    service_provider = "anthropic"
                if service_provider in {"openai", "anthropic", "gemini", "mimo"}:
                    if same_provider and active_model:
                        kwargs.setdefault("model", active_model)
                    if (
                        service_provider in {"openai", "anthropic"}
                        and same_provider
                        and active_base_url
                    ):
                        kwargs.setdefault("base_url", active_base_url)
                if service_provider == "mimo" and same_provider and active_base_url:
                    kwargs.setdefault("base_url", active_base_url)
                if service_provider in {"openai", "mimo"} and wire_api is None:
                    manual_reason = "The active OpenAI-compatible wire is not implemented by the direct vision service; use vision(action='manual')."
                elif service_provider == "mimo" and wire_api != "chat_completions":
                    manual_reason = "The active MiMo wire is not implemented by the direct vision service; use vision(action='manual')."
                if service_provider in {"openai", "mimo"} and active_compat == "anthropic":
                    manual_reason = "The active preset uses an Anthropic wire that this vision route cannot safely adapt; use vision(action='manual')."
                    vision_service = None
                if service_provider == "anthropic" and active_headers:
                    kwargs.setdefault("default_headers", active_headers)
                elif service_provider == "openai":
                    if active_headers:
                        kwargs.setdefault("default_headers", active_headers)
                    if wire_api not in (None, "auto"):
                        kwargs.setdefault("wire_api", wire_api)
                elif service_provider == "mimo":
                    # MiMo's standalone constructor intentionally accepts only
                    # api_key/model/base_url/max_tokens. Its current direct
                    # route is Chat Completions; other wires stay manual-only.
                    kwargs.pop("default_headers", None)
                    kwargs.pop("wire_api", None)
                resolved_api_key = api_key or active_api_key
                if service_provider not in {"codex", "local"} and not kwargs.get("model"):
                    manual_reason = f"Provider {provider!r} has no resolved current model for direct vision; use vision(action='manual')."
                elif service_provider not in {"codex", "local"} and not resolved_api_key:
                    manual_reason = f"Provider {provider!r} has no resolved current credential for direct vision; use vision(action='manual')."
                # Dedicated vision services do not consume the LLM adapter's
                # transport selector.
                kwargs.pop("api_compat", None)
                if service_provider not in {"openai", "anthropic", "mimo"}:
                    kwargs.pop("base_url", None)
                # Lazy import: the provider service lives in ``lingtai.services``.
                from lingtai.services.vision import create_vision_service
                if vision_service is None and not manual_reason:
                    try:
                        vision_service = create_vision_service(
                            service_provider,
                            api_key=resolved_api_key,
                            **kwargs,
                        )
                    except Exception as exc:
                        manual_reason = _setup_failure(provider, exc)
    elif vision_service is None:
        manual_reason = "No direct vision provider was configured; use vision(action='manual')."

    mgr = VisionManager(agent, vision_service=vision_service, manual_reason=manual_reason)
    agent.add_tool("vision", schema=get_schema(), handler=mgr.handle, description=get_description(), glossary_package=__package__)
    return mgr
