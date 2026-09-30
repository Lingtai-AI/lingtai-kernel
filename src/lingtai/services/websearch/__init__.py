"""SearchService — abstract web search and provider implementations.

Providers:
- DuckDuckGoSearchService — zero-API-key search via ddgs package.
- AnthropicSearchService — Anthropic native web search tool.
- OpenAISearchService — OpenAI canonical Responses API web search tool.

MiniMax and Zhipu were retired from this factory 2026-07-28 (Jason authorized
deletion, issue 11114): they are no longer built-in web search providers.
Wire either through a third-party MCP server instead — see
src/lingtai/tools/mcp/skills/mcp-manual/reference/third-party-and-legacy.md.
The Gemini Google Search grounding service was removed with the Gemini LLM
provider (LingTai keeps four LLM provider families: openai, anthropic, codex,
claude-code).

Factory:
    create_search_service(provider, api_key) — instantiate by provider name.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str


class SearchProviderError(RuntimeError):
    """A canonical provider's own search request failed at runtime.

    Carries a bounded, secret-free failure class and the provider name so
    callers (the ``web`` capability's use-case policy) can report typed,
    actionable failure without ever logging or returning raw SDK exception
    text, request bodies, or credentials. Earned once, shared by both
    canonical adapters (``anthropic.py``, ``openai.py``) rather than
    near-identical private classes or one speculative cross-tool error
    hierarchy.
    """

    def __init__(self, provider: str, failure_class: str) -> None:
        self.provider = provider[:32]
        self.failure_class = failure_class[:64]
        super().__init__(f"{self.provider}: {self.failure_class}")


class SearchService(ABC):
    """Abstract web search service.

    Backs the web_search capability. Implementations provide search
    via LLM grounding, dedicated search APIs, or other backends.
    """

    @abstractmethod
    def search(self, query: str, max_results: int | None = 5) -> list[SearchResult]:
        """Search the web and return results.

        Args:
            query: Search query string.
            max_results: Maximum number of results to request from the
                provider, or ``None`` for no LingTai-imposed count cap (the
                provider's own native finite result count still applies,
                where the provider has one).

        Returns:
            List of search results.
        """
        ...


def create_search_service(
    provider: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> SearchService:
    """Factory — create a SearchService for the given provider.

    Args:
        provider: Provider name (``"duckduckgo"``, ``"anthropic"``,
                  ``"openai"``).
        api_key: API key for the provider (required for all except duckduckgo).
        model: Optional model override.

    Returns:
        A configured SearchService instance.

    Raises:
        ValueError: If *provider* is not recognized.
        RuntimeError: If *api_key* is required but missing or empty.
    """
    name = provider.lower()

    def _require_key() -> str:
        if not api_key:
            raise RuntimeError(
                f"Search provider {provider!r} requires an api_key."
            )
        return api_key

    if name == "duckduckgo":
        from .duckduckgo import DuckDuckGoSearchService
        return DuckDuckGoSearchService()

    if name == "anthropic":
        from .anthropic import AnthropicSearchService
        kwargs: dict = {"api_key": _require_key()}
        if model:
            kwargs["model"] = model
        return AnthropicSearchService(**kwargs)

    if name == "openai":
        from .openai import OpenAISearchService
        kwargs = {"api_key": _require_key()}
        if model:
            kwargs["model"] = model
        return OpenAISearchService(**kwargs)

    raise ValueError(
        f"Unknown web search provider: {provider!r}. "
        f"Supported: duckduckgo, anthropic, openai."
    )
