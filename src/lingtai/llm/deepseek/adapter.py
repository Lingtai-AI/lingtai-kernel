"""DeepSeek adapter — thin OpenAI-compat wrapper that satisfies the
reasoning_content round-trip contract for thinking mode.

DeepSeek V4 thinking mode rejects requests missing ``reasoning_content``
on assistant turns once thinking has been triggered. Omitting it returns
HTTP 400:

    "The `reasoning_content` in the thinking mode must be passed back
     to the API."

The actual contract (determined empirically — the docs understate it):

    Once any assistant turn in the conversation has tool_calls, ALL
    subsequent assistant turns (tool-call AND plain-text) must carry
    reasoning_content when replayed.

Assistant turns BEFORE the first tool_call don't need it. After the
first tool_call, every assistant turn needs it — including the final
plain-text reply that followed the tool loop.

Real reasoning is preserved end-to-end now. The OpenAI adapter captures
``reasoning_content`` into a ThinkingBlock on every assistant turn
(``openai/adapter.py``); ``interface_converters.to_openai`` emits the
ThinkingBlock back as ``reasoning_content`` on replay. The historical
"byte-identical placeholder" approach (commits afc7ddc → 86c2a3d)
caused DeepSeek's cache fast-path to collapse onto the placeholder
string, producing empty responses (issue #9). Real per-turn reasoning
is byte-different by construction and avoids the collapse.

The only provider-specific responsibility here is opting into the shared
fallback: if an assistant turn replayed from history has no captured
ThinkingBlock (e.g. chat_history.jsonl entries written before this fix
shipped, or entries where the provider returned no reasoning text at all),
``OpenAIChatSession`` injects a per-turn-unique stub so DeepSeek's
field-presence validator is satisfied without re-introducing the
cache-collapse pattern.

Everything else inherits from ``OpenAIAdapter`` / ``OpenAIChatSession``:
``_session_class`` selects the DeepSeek session subclass, while message
building remains in the shared parent implementation.
"""

from __future__ import annotations

from ..openai.adapter import OpenAIAdapter, OpenAIChatSession


_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekChatSession(OpenAIChatSession):
    """Chat session that satisfies DeepSeek's reasoning_content round-trip contract.

    Real reasoning_content is emitted by ``interface_converters.to_openai``
    when the canonical interface has a ThinkingBlock on the assistant turn.
    This subclass opts into OpenAIChatSession's per-turn-unique fallback on
    assistant turns that lack one — typically rehydrated history entries from
    before the fix shipped.
    """

    _requires_reasoning_content_after_tool_call = True


class DeepSeekAdapter(OpenAIAdapter):
    """OpenAI-compat adapter pinned to DeepSeek with reasoning_content round-trip."""

    _session_class = DeepSeekChatSession

    def _default_prompt_cache_key(self, model: str) -> str:
        # Fixed provider identity — use a clean ``lingtai-deepseek`` namespace
        # rather than the base_url host. DeepSeek Chat Completions accepts
        # ``prompt_cache_key`` (compat probe); a stable key lets successive
        # turns hit the cross-request prompt cache.
        return f"lingtai-deepseek:{model}:v1"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout_ms: int = 300_000,
        max_rpm: int = 0,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEEPSEEK_BASE_URL,
            timeout_ms=timeout_ms,
            max_rpm=max_rpm,
        )
