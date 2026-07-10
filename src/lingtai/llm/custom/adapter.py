"""Custom adapter — named provider aliases with OpenAI, Anthropic, or Gemini compat.

Any provider name not matching a built-in (openai, anthropic, gemini, minimax)
routes here. The api_compat field selects the underlying adapter:

  "openai"    — OpenAI-compatible API with auto wire selection (default)
  "anthropic" — Anthropic Messages API
  "gemini"    — Google Gemini (requires api_key, ignores base_url)

Usage in config.json providers section:
  "openrouter":  {"api_compat": "openai",    "base_url": "https://openrouter.ai/api/v1", ...}
  "bedrock":     {"api_compat": "anthropic",  "base_url": "https://...", ...}
  "vertex":      {"api_compat": "gemini",     ...}
"""
from lingtai.llm.base import LLMAdapter

from .defaults import DEFAULTS  # noqa: F401 — re-exported for consumers


def _normalize_service_tier(value) -> str | None:
    if value is None or value is False:
        return None
    text = str(value).strip()
    return text or None


def create_custom_adapter(
    api_key: str | None = None,
    api_compat: str = "openai",
    wire_api: str = "auto",
    base_url: str | None = None,
    default_headers: dict | None = None,
    **kwargs,
) -> LLMAdapter:
    """Factory: creates adapter based on api_compat.

    ``default_headers`` is forwarded to the underlying SDK client for all
    compat paths that expose HTTP header configuration.
    """
    service_tier = _normalize_service_tier(kwargs.pop("service_tier", None))
    response_controls: dict = {}
    for key in ("use_responses_api", "use_responses", "force_responses"):
        if key in kwargs:
            target = "use_responses" if key == "use_responses_api" else key
            response_controls[target] = kwargs.pop(key)
    if api_compat == "gemini":
        from ..gemini.adapter import GeminiAdapter
        if default_headers is not None:
            kwargs["default_headers"] = default_headers
        return GeminiAdapter(api_key=api_key, **kwargs)
    elif api_compat == "anthropic":
        if not base_url:
            raise ValueError("Anthropic-compat provider requires a base_url")
        from ..anthropic.adapter import AnthropicAdapter
        if default_headers is not None:
            kwargs["default_headers"] = default_headers
        return AnthropicAdapter(api_key=api_key, base_url=base_url, **kwargs)
    else:
        if not base_url:
            raise ValueError("OpenAI-compat provider requires a base_url")
        from ..openai.adapter import OpenAIAdapter
        oa_kwargs: dict = dict(kwargs)
        if service_tier:
            oa_kwargs["service_tier"] = service_tier
        if wire_api == "responses":
            oa_kwargs["use_responses"] = True
            oa_kwargs["force_responses"] = True
        elif wire_api == "chat_completions":
            oa_kwargs.setdefault("use_responses", False)
            oa_kwargs.setdefault("force_responses", False)
        elif wire_api != "auto":
            raise ValueError(
                "OpenAI-compat custom provider wire_api must be "
                "'chat_completions', 'responses', or 'auto'"
            )
        oa_kwargs.update(response_controls)
        if default_headers is not None:
            oa_kwargs["default_headers"] = default_headers
        return OpenAIAdapter(api_key=api_key, base_url=base_url, **oa_kwargs)
