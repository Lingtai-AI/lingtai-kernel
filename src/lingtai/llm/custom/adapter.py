"""Custom adapter — named provider aliases with OpenAI, Anthropic, or Gemini compat.

Any provider name not matching a built-in (openai, anthropic, gemini, minimax)
routes here. The api_compat field selects the underlying adapter:

  "openai"    — OpenAI Chat Completions (default)
  "anthropic" — Anthropic Messages API
  "gemini"    — Google Gemini (requires api_key, ignores base_url)

Usage in config.json providers section:
  "openrouter":  {"api_compat": "openai",    "base_url": "https://openrouter.ai/api/v1", ...}
  "bedrock":     {"api_compat": "anthropic",  "base_url": "https://...", ...}
  "vertex":      {"api_compat": "gemini",     ...}
"""
from lingtai.llm.base import LLMAdapter

from .defaults import DEFAULTS  # noqa: F401 — re-exported for consumers


def create_custom_adapter(
    api_key: str | None = None,
    api_compat: str = "openai",
    base_url: str | None = None,
    default_headers: dict | None = None,
    **kwargs,
) -> LLMAdapter:
    """Factory: creates adapter based on api_compat.

    ``default_headers`` is forwarded to the underlying SDK client for all
    compat paths that expose HTTP header configuration.

    ``wire_api`` is scoped to OpenAI-compatible wire semantics; it is only
    forwarded when ``api_compat="openai"`` and ignored (not silently applied)
    for Anthropic or Gemini backends.
    """
    wire_api = kwargs.pop("wire_api", None)
    use_responses = kwargs.pop("use_responses", None)
    force_responses = kwargs.pop("force_responses", None)

    if api_compat == "gemini":
        if wire_api is not None and wire_api != "auto":
            raise ValueError(
                "wire_api is scoped to OpenAI-compatible providers; "
                f"cannot use wire_api={wire_api!r} with api_compat=gemini"
            )
        from ..gemini.adapter import GeminiAdapter
        if default_headers is not None:
            kwargs["default_headers"] = default_headers
        return GeminiAdapter(api_key=api_key, **kwargs)
    elif api_compat == "anthropic":
        if wire_api is not None and wire_api != "auto":
            raise ValueError(
                "wire_api is scoped to OpenAI-compatible providers; "
                f"cannot use wire_api={wire_api!r} with api_compat=anthropic"
            )
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
        if default_headers is not None:
            oa_kwargs["default_headers"] = default_headers
        if wire_api is not None:
            oa_kwargs["wire_api"] = wire_api
        if use_responses is not None:
            oa_kwargs["use_responses"] = use_responses
        if force_responses is not None:
            oa_kwargs["force_responses"] = force_responses
        oa_kwargs["responses_stateless_replay"] = True
        return OpenAIAdapter(api_key=api_key, base_url=base_url, **oa_kwargs)
