"""Mimo (Xiaomi) adapter — satisfies MiMo thinking-mode's reasoning_content
round-trip contract, analogous to DeepSeek.

MiMo speaks the OpenAI Chat Completions protocol. Once thinking mode has
been invoked by an assistant ``tool_calls`` turn, MiMo (like DeepSeek)
requires every subsequent assistant turn — tool-call AND plain-text —
to carry ``reasoning_content`` on replay. Assistant turns BEFORE the
first tool_call must NOT carry it.

Earlier, this adapter stripped ``reasoning_content`` from every replayed
assistant turn as a workaround for a model loop: when the *same* thinking
block was echoed back unchanged on every turn, MiMo treated it as
authoritative and parroted it verbatim, eventually tripping the 120s LLM
hang watchdog. Real per-turn reasoning from ``ThinkingBlock``s is
byte-different by construction (and so is the shared per-turn-unique
fallback), which avoids that pathology while satisfying the protocol.

Real reasoning is preserved end-to-end now: ``OpenAIChatSession`` captures
``reasoning_content`` into a ``ThinkingBlock`` on each assistant turn, and
``interface_converters.to_openai`` emits the block back as
``reasoning_content`` on replay. This adapter only opts into the shared
per-turn-unique fallback for rehydrated/historical assistant turns that have
no captured ``ThinkingBlock`` (e.g. ``chat_history.jsonl`` entries written
before this fix, or turns where the provider returned no reasoning text).
"""
from __future__ import annotations

from ..openai.adapter import OpenAIAdapter, OpenAIChatSession


class MimoChatSession(OpenAIChatSession):
    """Chat session that satisfies MiMo's reasoning_content round-trip contract.

    Real ``reasoning_content`` produced by ``interface_converters.to_openai``
    from captured ``ThinkingBlock``s is preserved verbatim. Assistant turns
    after the first tool_call that lack a ThinkingBlock opt into
    OpenAIChatSession's per-turn-unique fallback. Pre-tool-call plain-text
    assistant turns are left alone.
    """

    _requires_reasoning_content_after_tool_call = True


class MimoAdapter(OpenAIAdapter):
    """OpenAI-compat adapter pinned to MiMo with reasoning_content round-trip."""

    _session_class = MimoChatSession

    def _default_prompt_cache_key(self, model: str) -> str:
        # Fixed provider identity — use a clean ``lingtai-mimo`` namespace
        # rather than the base_url host. MiMo Chat Completions accepts
        # ``prompt_cache_key`` (compat probe); a stable key lets successive
        # turns hit the cross-request prompt cache.
        return f"lingtai-mimo:{model}:v1"
