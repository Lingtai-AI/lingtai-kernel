"""Zhipu (GLM) adapter — thin OpenAI-compat wrapper with two provider-local
deviations: same-role message merging, and a provider-owned reasoning contract.

1. **Same-role merge (error 1214).** Zhipu GLM rejects requests containing
   consecutive messages with the same role. The fix is a single
   ``_build_messages`` override on the session that post-processes the
   wire-format messages before they reach the API.

2. **Two-axis reasoning.** GLM expresses reasoning as two independent top-level
   Chat Completions fields (``thinking`` + ``reasoning_effort``), not as the
   generic flat effort scalar. ``_chat_reasoning_kwargs`` overrides the generic
   hook so the flat branch never fires for GLM, and ``effort.py`` owns the
   vocabulary, the fail-closed model gate, and the wire rendering.

Both are provider-specific constraints — the generic OpenAI adapter must not
carry either workaround. Everything else inherits from ``OpenAIAdapter`` /
``OpenAIChatSession`` unchanged via the ``_build_messages``, ``_session_class``,
and ``_chat_reasoning_kwargs`` hook points on the parent.
"""

from __future__ import annotations

import logging

from ..openai.adapter import OpenAIAdapter, OpenAIChatSession
from .effort import OMITTED_EFFORT, ZhipuEffort, normalize_zhipu_effort

logger = logging.getLogger(__name__)

# Construction-only carrier that hands the frozen reasoning descriptor from the
# adapter to the session. ``ZhipuChatSession`` strips it before the base class
# captures ``_extra_kwargs``, so it is never splatted into a request and never
# reaches the wire or a log line.
_EFFORT_CARRIER_KEY = "_lingtai_zhipu_effort"


def _extract_text(content) -> str:
    """Extract plain text from a content value (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def _merge_consecutive_same_role(messages: list[dict]) -> list[dict]:
    """Merge consecutive messages with the same role.

    Zhipu GLM (error 1214) rejects requests that contain consecutive
    messages with the same role.  This function merges adjacent
    same-role messages by concatenating their text content.

    Rules:
    - system messages are never merged (should be singular anyway).
    - tool messages are never merged (each has a distinct tool_call_id).
    - assistant messages: text content concatenated; tool_calls taken
      from the last message in the run that carries them.
    - user messages: text content concatenated.

    Idempotent — returns the list unchanged if no consecutive duplicates.
    """
    if len(messages) <= 1:
        return messages

    result: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        # Never merge system or tool messages.
        if role in ("system", "tool") or not result:
            result.append(msg)
            continue
        prev = result[-1]
        if prev.get("role") != role:
            result.append(msg)
            continue

        # --- merge into prev ---
        logger.warning(
            "[wire-sanitize] merging consecutive %s messages — "
            "GLM rejects same-role runs (error 1214)",
            role,
        )
        prev_content = prev.get("content")
        cur_content = msg.get("content")
        prev_is_list = isinstance(prev_content, list)
        cur_is_list = isinstance(cur_content, list)

        prev_text = _extract_text(prev_content)
        cur_text = _extract_text(cur_content)
        parts = [p for p in (prev_text, cur_text) if p]
        merged_text = "\n".join(parts) if parts else ""

        # Keep list format if either side used it.
        if prev_is_list or cur_is_list:
            prev["content"] = [{"type": "text", "text": merged_text}]
        else:
            prev["content"] = merged_text

        if role == "assistant":
            # Preserve tool_calls from the *last* message that has them.
            if msg.get("tool_calls"):
                prev["tool_calls"] = msg["tool_calls"]

    return result


class ZhipuChatSession(OpenAIChatSession):
    """Chat session that merges consecutive same-role messages for Zhipu GLM.

    GLM error 1214 fires when the wire-format message list has adjacent
    messages with the same role.  This override applies the merge as the
    last step of ``_build_messages``, after the parent has done all its
    standard formatting.
    """

    def __init__(self, *args, extra_kwargs: dict | None = None, **kwargs):
        # Strip the construction-only descriptor carrier BEFORE the base class
        # captures ``_extra_kwargs``: everything left in that dict is splatted
        # into every outgoing request.
        remaining = dict(extra_kwargs or {})
        self._zhipu_effort: ZhipuEffort = remaining.pop(
            _EFFORT_CARRIER_KEY, OMITTED_EFFORT
        )
        super().__init__(*args, extra_kwargs=remaining, **kwargs)

    def _build_messages(self) -> list[dict]:
        messages = super()._build_messages()
        return _merge_consecutive_same_role(messages)

    def reasoning_observability(self) -> dict:
        """Safe provider-neutral reasoning fields for this session's ``llm_call``.

        Derived from the same frozen descriptor that produced the wire bytes, so
        the log can never report an effort the request did not carry.
        """
        return self._zhipu_effort.observability_fields()


class ZhipuAdapter(OpenAIAdapter):
    """OpenAI-compat adapter for Zhipu GLM.

    Two provider-local deviations from the generic OpenAI Chat Completions
    adapter: the same-role message merge above (GLM error 1214), and the
    provider-owned two-axis reasoning contract below (``thinking`` +
    ``reasoning_effort``, see ``effort.py``).
    """

    _session_class = ZhipuChatSession

    def _chat_reasoning_kwargs(self, thinking: str, model: str) -> dict:
        """Emit GLM's two-axis reasoning payload instead of the generic flat one.

        Both fields ride a single ``extra_body`` dict — the SDK hoists its
        members to top-level JSON siblings — so the generic flat
        ``reasoning_effort`` branch never fires for GLM and can never disagree
        with what is actually serialized.

        Normalization happens here, before session construction, so the decision
        is frozen for every turn and every streaming call on the session.
        """
        effort = normalize_zhipu_effort(thinking, model)
        kwargs: dict = {_EFFORT_CARRIER_KEY: effort}
        payload = effort.extra_body
        if payload:
            kwargs["extra_body"] = payload
        return kwargs

    def _default_prompt_cache_key(self, model: str) -> str:
        # Fixed provider identity — use a clean ``lingtai-zhipu`` namespace
        # rather than the base_url host. Zhipu/GLM Chat Completions accepts
        # ``prompt_cache_key`` (compat probe); a stable key lets successive
        # turns hit the cross-request prompt cache.
        return f"lingtai-zhipu:{model}:v1"
