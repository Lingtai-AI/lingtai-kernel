"""DeepSeek-local reasoning emission — two axes (mode + effort), two wires.

DeepSeek V4 does not fit the generic OpenAI-compatible reasoning helpers, and
routing it through them is lossy in both directions:

* the generic Chat mapper in ``../openai/adapter.py`` emits ``"high"`` only for
  an exact ``"high"`` and collapses every other value to ``"low"`` — so ``max``
  silently becomes ``low`` and ``none`` becomes ``low`` instead of *disabling*
  thinking;
* the generic ``_responses_reasoning_kwargs`` validates against the global
  ``THINKING_LEVELS`` tuple, which has no ``max``.

So DeepSeek owns its emission here. The two wires spell the same two axes
differently:

======================  ==========================================================
Chat Completions        mode ``thinking: {"type": "enabled"|"disabled"}`` plus, when
                        enabled, the flat canonical ``reasoning_effort`` ∈
                        ``low|high|max``. ``thinking`` is not an OpenAI SDK
                        parameter, so it rides in ``extra_body`` — the same
                        mechanism ``_adapter_extra_body`` uses for OpenRouter.
Responses (Flash only)  one nested ``reasoning: {"effort": ...}`` accepting the
                        exact upstream seven; ``none`` encodes disabled.
======================  ==========================================================

Omission is a first-class state: ``None`` / the ``"default"`` sentinel emit
NOTHING, so DeepSeek's documented default (thinking enabled, effort high)
applies. Omission must mean omission rather than an accidental explicit high.

Vocabulary tuples and the source-dated capability table live in
``lingtai.kernel.config`` so the manifest/preset validators — which must not
import from ``lingtai.llm`` — share one definition with this emitter. This
module is deliberately DeepSeek-only: no generic resolver, no catalogue.
"""

from __future__ import annotations

from lingtai.kernel.config import (
    DEEPSEEK_CAPABILITY_SOURCE,
    DEEPSEEK_CHAT_THINKING_LEVELS,
    DEEPSEEK_RESPONSES_MODELS,
    DEEPSEEK_RESPONSES_THINKING_LEVELS,
    THINKING_DEFAULT_SENTINEL,
    deepseek_responses_model_supported,
)

__all__ = [
    "deepseek_chat_reasoning_kwargs",
    "deepseek_responses_model_supported",
    "deepseek_responses_reasoning_kwargs",
    "deepseek_thinking_is_omitted",
]


def deepseek_thinking_is_omitted(thinking: object) -> bool:
    """Return whether *thinking* is the omission state (no fields on the wire)."""
    return thinking is None or thinking == THINKING_DEFAULT_SENTINEL


def deepseek_chat_reasoning_kwargs(thinking: object) -> dict:
    """Return DeepSeek Chat Completions request kwargs for *thinking*.

    Omission yields ``{}``. ``none`` sets the mode axis to disabled and sends NO
    effort field (``none`` is not an effort tier). Every other canonical value
    sets mode enabled and the same exact flat ``reasoning_effort``. Aliases
    (``minimal``/``medium``/``xhigh``), case variants, and non-strings raise —
    Chat normalizes nothing silently.
    """
    if deepseek_thinking_is_omitted(thinking):
        return {}
    if not isinstance(thinking, str) or thinking not in DEEPSEEK_CHAT_THINKING_LEVELS:
        raise ValueError(
            "DeepSeek Chat Completions thinking must be one of "
            f"{', '.join(DEEPSEEK_CHAT_THINKING_LEVELS)}, or default "
            f"(got {thinking!r}; capability source {DEEPSEEK_CAPABILITY_SOURCE})"
        )
    if thinking == "none":
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": thinking,
    }


def deepseek_responses_reasoning_kwargs(thinking: object, model: object = None) -> dict:
    """Return DeepSeek Responses request kwargs for *thinking* on *model*.

    Omission yields ``{}``. Otherwise the exact upstream seven map to a nested
    ``reasoning.effort`` (``none`` disables). When *model* is given it must be a
    documented Responses model — Pro is rejected, never coerced. The vocabulary
    check runs first so a bad value on Pro reports the value.

    The model guard is scoped to an EXPLICIT effort on purpose: under omission
    no reasoning field is sent at all, and native daemons construct their
    sessions with the ``"default"`` sentinel, so raising there would convert an
    existing Pro+Responses daemon into a hard local crash that this slice never
    promised to introduce.
    """
    if deepseek_thinking_is_omitted(thinking):
        return {}
    if not isinstance(thinking, str) or thinking not in DEEPSEEK_RESPONSES_THINKING_LEVELS:
        raise ValueError(
            "DeepSeek Responses thinking must be one of "
            f"{', '.join(DEEPSEEK_RESPONSES_THINKING_LEVELS)}, or default "
            f"(got {thinking!r}; capability source {DEEPSEEK_CAPABILITY_SOURCE})"
        )
    if model is not None and not deepseek_responses_model_supported(model):
        raise ValueError(
            "DeepSeek Responses currently supports only "
            f"{', '.join(DEEPSEEK_RESPONSES_MODELS)}; "
            f"model {model!r} has no documented Responses support "
            f"(capability source {DEEPSEEK_CAPABILITY_SOURCE}). "
            "Use the Chat Completions wire for this model."
        )
    return {"reasoning": {"effort": thinking}}
