"""OpenAI web search — uses OpenAI's canonical Responses API web search tool."""
from __future__ import annotations

from lingtai.kernel.logging import get_logger

from . import SearchProviderError, SearchResult, SearchService

logger = get_logger()

# Every worked Responses-API web_search code sample in OpenAI's official
# "Web search" doc (fetched 2026-07-29; local evidence at
# tmp/tool-results/20260729T034836-web-browse-b2fb15.txt) uses this model
# name (26 occurrences across javascript/python/curl/C#/Ruby examples); the
# doc's own comparison-table prose cell says "gpt-5.5" in one place (5
# occurrences, table text only, never a runnable example) — the code samples
# are the more concrete, repeated, and currently-runnable evidence, so this
# default follows them. If OpenAI's docs are updated, re-fetch and re-cite
# before changing this default; do not guess.
DEFAULT_MODEL = "gpt-5.6"


class OpenAISearchError(SearchProviderError):
    """The canonical OpenAI Responses web search request failed at runtime.

    Carries a bounded, secret-free failure class so callers (the ``web``
    capability's fallback policy) can report provenance without leaking SDK
    exception text that may embed request/response bodies. Subclasses the
    shared :class:`SearchProviderError`; the ``web`` capability's OpenAI-only
    DuckDuckGo fallback still keys on this exact subclass, not the shared
    base, so Anthropic provider failures never trigger that fallback.
    """

    def __init__(self, failure_class: str) -> None:
        super().__init__("openai", failure_class)


class OpenAISearchService(SearchService):
    """Web search via OpenAI's canonical Responses API ``web_search`` tool.

    Sends a one-shot Responses API request with ``tools=[{"type":
    "web_search"}]`` enabled — the current first-party integration path,
    replacing the retired ``gpt-4o-search-preview`` Chat Completions route.

    Args:
        api_key: OpenAI API key.
        model: Model to use (default :data:`DEFAULT_MODEL`).
    """

    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._model = model

    def search(self, query: str, max_results: int | None = 5) -> list[SearchResult]:
        import openai as openai_sdk

        client = openai_sdk.OpenAI(api_key=self._api_key)
        try:
            raw = client.responses.create(
                model=self._model,
                tools=[{"type": "web_search"}],
                input=query,
            )
        except Exception as e:
            logger.warning("OpenAI web search failed: %s", type(e).__name__)
            raise OpenAISearchError(type(e).__name__) from e

        return _extract_results(raw, max_results)


def _extract_results(raw: object, max_results: int) -> list[SearchResult]:
    # Official Responses shape (evidence doc lines 145-184): raw.output is a
    # list of items; the assistant "message" item's content[0] is an
    # output_text block whose annotations carry url_citation entries with
    # real source url/title. Prefer those — they are the official per-source
    # citation fields, never invented — over the single synthesized-text
    # blob so a successful search is actually visible/browsable to the Agent.
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    output = getattr(raw, "output", None) or []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", None) or []:
            for annotation in getattr(block, "annotations", None) or []:
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                url = getattr(annotation, "url", None)
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = getattr(annotation, "title", None) or "OpenAI Web Search"
                text = getattr(block, "text", None) or ""
                results.append(SearchResult(title=title, url=url, snippet=text))
                if len(results) >= max_results:
                    return results

    if results:
        return results

    # No structured citations were present (e.g. the model answered without
    # actually invoking web_search this turn), but the official API can
    # still legally return a nonempty search-grounded narrative with no URL.
    # Preserve exactly one bounded narrative result rather than silently
    # discarding real output — never fabricate a link_ref for it.
    text = getattr(raw, "output_text", None) or ""
    if not text:
        return []
    return [SearchResult(title="OpenAI Web Search", url="", snippet=text)]
