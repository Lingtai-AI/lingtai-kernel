"""Custom adapter — named provider aliases with OpenAI or Anthropic compat.

Any provider name not matching a built-in (openai, anthropic, minimax)
routes here. The api_compat field selects the underlying adapter:

  "openai"    — OpenAI Chat Completions (default)
  "anthropic" — Anthropic Messages API

Usage in config.json providers section:
  "openrouter":  {"api_compat": "openai",    "base_url": "https://openrouter.ai/api/v1", ...}
  "bedrock":     {"api_compat": "anthropic",  "base_url": "https://...", ...}
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

    ``default_headers`` is forwarded to the underlying SDK client when it
    is supported (currently only the OpenAI-compat path). Other api_compat
    branches silently drop it for now — extend here when needed.
    """
    if api_compat == "anthropic":
        if not base_url:
            raise ValueError("Anthropic-compat provider requires a base_url")
        from ..anthropic.adapter import AnthropicAdapter
        return AnthropicAdapter(api_key=api_key, base_url=base_url, **kwargs)
    else:
        if not base_url:
            raise ValueError("OpenAI-compat provider requires a base_url")
        from ..openai.adapter import OpenAIAdapter
        oa_kwargs: dict = dict(kwargs)
        if default_headers is not None:
            oa_kwargs["default_headers"] = default_headers
        return OpenAIAdapter(api_key=api_key, base_url=base_url, **oa_kwargs)
