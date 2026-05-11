"""Mimo (Xiaomi) adapter — strip reasoning_content from replayed assistant turns.

Mimo speaks the OpenAI Chat Completions protocol, so the default OpenAI adapter
nominally works. The wrinkle is that ``interface_converters.to_openai`` echoes
prior assistant ``reasoning_content`` back on every request (added for
DeepSeek's thinking-mode contract — see ``deepseek/adapter.py`` and issue #9).

Mimo treats that echoed reasoning as authoritative prior thought and emits it
verbatim again on the next turn, producing a self-reinforcing loop where the
same thinking block accumulates in history and is parroted back indefinitely
(~129 thinking tokens per turn, byte-identical). Eventually the LLM hang
watchdog (120s) suspends the agent.

The fix is provider-specific: for Mimo, the canonical ``ThinkingBlock`` is
still captured into history on the response path (useful for telemetry and
intra-turn context), but on the replay path the assistant message's
``reasoning_content`` field is dropped before the request goes out. Mimo does
not require it for any protocol contract, so dropping it is safe.
"""
from __future__ import annotations

from ..openai.adapter import OpenAIAdapter, OpenAIChatSession


class MimoChatSession(OpenAIChatSession):
    """Chat session that strips ``reasoning_content`` from outgoing replays.

    Mimo loops if its own prior reasoning is fed back on subsequent turns.
    Drop the field after the canonical converter has produced the message
    list — the ThinkingBlock remains in the ChatInterface for in-process use.
    """

    def _build_messages(self) -> list[dict]:
        messages = super()._build_messages()
        for msg in messages:
            if msg.get("role") == "assistant":
                msg.pop("reasoning_content", None)
        return messages


class MimoAdapter(OpenAIAdapter):
    """OpenAI-compat adapter pinned to Mimo that drops replayed reasoning."""

    _session_class = MimoChatSession
