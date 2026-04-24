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

DeepSeek validates field presence, not content: the server doesn't
fingerprint or diff the string — it just wants *something* there to
confirm the client is thinking-mode aware. This adapter therefore does
not try to preserve the actual reasoning text across restarts; it injects
a stable placeholder on every affected assistant turn. Reasoning is
scratch work, not durable state — the agent's real memory lives in the
system prompt, pad, and conversation. Round-tripping reasoning is a
protocol artifact, not an information-flow need.

Everything else inherits from ``OpenAIAdapter`` / ``OpenAIChatSession``
unchanged via the ``_build_messages`` and ``_session_class`` hook points
on the parent.
"""

from __future__ import annotations

from ..openai.adapter import OpenAIAdapter, OpenAIChatSession


_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


# Human-readable placeholder sent as reasoning_content on every assistant
# turn once thinking mode has been invoked by a prior tool_call. DeepSeek
# validates field presence, not content — any non-empty string is accepted.
# A readable string is preferred over empty for wire-debugging clarity.
#
# DeepSeek V4's cache-hit fast-path (verified empirically: high cache hit +
# low thinking_tokens ~= 10) echoes the last reasoning_content it saw in
# context verbatim, rather than generating fresh reasoning. This causes the
# kernel's 'thinking' event to log this exact string as the agent's thought.
# The strip-on-parse in DeepSeekChatSession.send/send_stream filters these
# exact-match echoes out of response.thoughts.
_REASONING_PLACEHOLDER = "(reasoning omitted — not preserved across turns)"


class DeepSeekChatSession(OpenAIChatSession):
    """Chat session that injects a reasoning_content placeholder on tool-call turns."""

    def _build_messages(self) -> list[dict]:
        messages = super()._build_messages()
        # Inject placeholder on every assistant turn from the FIRST tool_call
        # onward. DeepSeek's server validates that reasoning_content is present
        # on all assistant turns once thinking mode has been invoked by a tool
        # call, even on plain-text replies following the tool loop.
        seen_tool_call = False
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            if msg.get("tool_calls"):
                seen_tool_call = True
            if seen_tool_call:
                msg["reasoning_content"] = _REASONING_PLACEHOLDER
        return messages

    def send(self, message):
        response = super().send(message)
        _strip_placeholder_echoes(response)
        return response

    def send_stream(self, message, on_chunk=None):
        response = super().send_stream(message, on_chunk)
        _strip_placeholder_echoes(response)
        return response


def _strip_placeholder_echoes(response) -> None:
    """Strip our placeholder string from the start of each thought.

    DeepSeek V4, seeing our placeholder on every recent assistant turn in
    context, prepends that exact string to its own fresh reasoning on the
    next response. Result: thoughts like
        "(reasoning omitted — not preserved across turns)发现 args 检查失败..."
    We chop the placeholder prefix off so the kernel's 'thinking' event log
    shows just the real reasoning.

    Pure-echo responses (where thought == placeholder with no tail) become
    empty strings and are dropped entirely — there's no reasoning to keep.
    """
    if not getattr(response, "thoughts", None):
        return
    cleaned: list[str] = []
    for t in response.thoughts:
        if t and t.startswith(_REASONING_PLACEHOLDER):
            tail = t[len(_REASONING_PLACEHOLDER):].lstrip()
            if tail:
                cleaned.append(tail)
        else:
            cleaned.append(t)
    response.thoughts = cleaned


class DeepSeekAdapter(OpenAIAdapter):
    """OpenAI-compat adapter pinned to DeepSeek with reasoning_content round-trip."""

    _session_class = DeepSeekChatSession

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
