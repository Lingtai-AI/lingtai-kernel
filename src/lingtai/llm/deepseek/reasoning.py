"""DeepSeek-local reasoning-effort contract.

This module is the single owner of everything DeepSeek-specific about
reasoning effort: which models exist, which effort levels each model really
has on each wire, what an omitted value means, which compatibility aliases are
officially documented and what they normalize to, and the exact payload shape
for each wire. The generic OpenAI-compatible transport stays neutral; it only
invokes the controller below and carries its immutable result.

There is deliberately NO ``DeepSeekAdapter`` subclass and no cross-provider
semantic matrix — DeepSeek owns its route, and nothing else does.

Official contract (https://api-docs.deepseek.com, verified 2026-08-09):

Wire-shape note (Chat): ``reasoning_effort`` is a native parameter of the
OpenAI SDK's ``chat.completions.create``, but ``thinking`` is a DeepSeek body
extension and is NOT — the installed SDK declares no such parameter and no
``**kwargs``, so passing it directly raises ``TypeError`` before any request.
It therefore travels in ``extra_body``, which the SDK merges into the top level
of the request JSON, giving DeepSeek exactly the body it documents.

  Chat Completions (``/api/create-chat-completion/``)
    * ``thinking.type`` accepts ``["enabled", "disabled"]``; default enabled.
    * ``reasoning_effort`` accepts ``["low", "high", "max"]``; default ``high``.
    * "currently only ``deepseek-v4-flash`` supports the three effort levels"
    * "``deepseek-v4-pro`` temporarily supports only ``high`` and ``max``
      (``low`` is treated as ``high``, ``xhigh`` is treated as ``max``), and is
      expected to support all three levels in early August 2026"
    * "For compatibility, ``medium`` and ``xhigh`` are mapped to ``high``."

  Responses (``/guides/responses_api/``)
    * "The Responses API currently only supports the ``deepseek-v4-flash``
      model, and does not yet support the ``deepseek-v4-pro`` model."
    * ``reasoning`` is partially supported: ``effort`` is supported, ``summary``
      is accepted but never generated. No compatibility alias and no
      thinking-disable is documented for this wire.

Two deliberate fail-closed decisions, both driven by the product contract that
the effort control must offer all and only a model's REAL canonical choices:

  1. ``low`` on ``deepseek-v4-pro`` is rejected rather than silently rewritten
     to ``high``. The server-side "treated as high" leniency is not a
     documented compatibility alias, and advertising ``low`` on Pro would
     promise a tier the model does not have. (When Pro gains real three-level
     support, move ``low`` into its canonical tuple — one line.)
  2. Responses accepts nothing outside ``low|high|max``. The guide notes that
     unsupported parameters may be silently ignored, so an undocumented value
     must fail locally instead of being sent and quietly dropped.

Related files:
  - src/lingtai/llm/openai/reasoning_control.py — the neutral carrier/protocol
  - src/lingtai/llm/_register.py — injects this controller into the deepseek route
  - src/lingtai/init_schema.py, src/lingtai/kernel/presets.py — ingress validation
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ..openai.reasoning_control import ReasoningApplication


PROVIDER = "deepseek"

WIRE_CHAT = "chat_completions"
WIRE_RESPONSES = "responses"

#: Internal omission sentinel. Never emitted on the wire — it means "let
#: DeepSeek apply its own provider default".
OMITTED = "default"

#: Explicit Chat-only level meaning ``thinking.type = disabled``. It is a real
#: requested value, NOT an omission.
DISABLED = "none"

FLASH = "deepseek-v4-flash"
PRO = "deepseek-v4-pro"


@dataclass(frozen=True)
class DeepSeekEffortCapability:
    """The real effort surface of one (model, wire) route.

    ``canonical`` is the advertised choice list — exactly what an effort
    control may offer. ``aliases`` are accepted on ingress and normalized, but
    are never advertised as choices.
    """

    model: str
    wire: str
    canonical: tuple[str, ...]
    aliases: Mapping[str, str]


_CAPABILITIES: Mapping[tuple[str, str], DeepSeekEffortCapability] = MappingProxyType({
    (FLASH, WIRE_CHAT): DeepSeekEffortCapability(
        model=FLASH,
        wire=WIRE_CHAT,
        canonical=("none", "low", "high", "max"),
        aliases=MappingProxyType({"medium": "high", "xhigh": "high"}),
    ),
    (PRO, WIRE_CHAT): DeepSeekEffortCapability(
        model=PRO,
        wire=WIRE_CHAT,
        # ``low`` is intentionally absent: not a real Pro capability today.
        canonical=("none", "high", "max"),
        aliases=MappingProxyType({"medium": "high", "xhigh": "max"}),
    ),
    (FLASH, WIRE_RESPONSES): DeepSeekEffortCapability(
        model=FLASH,
        wire=WIRE_RESPONSES,
        canonical=("low", "high", "max"),
        aliases=MappingProxyType({}),
    ),
    # (PRO, WIRE_RESPONSES) is absent on purpose — Responses does not support Pro.
})

#: Models DeepSeek is known to publish today. Membership makes an absent
#: capability entry a KNOWN-unsupported route (rejected even when effort is
#: omitted) rather than a merely unrecognized one (omission still allowed).
#: This is a DeepSeek-local set, not a cross-provider registry.
_KNOWN_MODELS: frozenset[str] = frozenset({FLASH, PRO})


def owns_provider(provider: Any) -> bool:
    """Return whether *provider* is the DeepSeek route this module owns."""
    return isinstance(provider, str) and provider.strip().lower() == PROVIDER


def wire_for(llm: Mapping[str, Any]) -> str:
    """Return the wire a manifest/preset ``llm`` block selects for DeepSeek.

    Mirrors ``OpenAIAdapter._should_use_responses()`` for the deepseek factory:
    the route always has a configured ``base_url``, so only an explicit
    ``wire_api: responses`` reaches the Responses wire.
    """
    if str(llm.get("wire_api") or "").strip().lower() == WIRE_RESPONSES:
        return WIRE_RESPONSES
    return WIRE_CHAT


def canonical_levels(*, model: str, wire: str) -> tuple[str, ...]:
    """Return the advertised canonical choices for a route.

    Compatibility aliases are deliberately excluded — they are accepted input,
    not capability levels. Raises ``ValueError`` for an unsupported route.
    """
    cap = _CAPABILITIES.get((model, wire))
    if cap is None:
        raise ValueError(_unsupported_route_message(model, wire))
    return cap.canonical


def accepted_values(*, model: str, wire: str) -> tuple[str, ...]:
    """Return every raw value the route accepts (canonical plus aliases)."""
    cap = _CAPABILITIES.get((model, wire))
    if cap is None:
        raise ValueError(_unsupported_route_message(model, wire))
    return cap.canonical + tuple(sorted(cap.aliases))


def resolve_configured_thinking(value: Any) -> Any:
    """Map a configured value to what the DeepSeek route should receive.

    Constructor/config omission and an explicit ``None`` both mean Auto: they
    become the omission sentinel, never the legacy cross-provider ``high``.
    Every other value — including the empty string, ``False`` and ``0`` — is
    passed through verbatim so the contract below can reject it.
    """
    return OMITTED if value is None else value


def _unsupported_route_message(model: Any, wire: str) -> str:
    supported = sorted({m for (m, w) in _CAPABILITIES if w == wire})
    return (
        f"provider deepseek does not support reasoning effort for "
        f"model {model!r} on the {wire} wire; "
        f"supported models on this wire: {', '.join(supported) or 'none'}"
    )


def apply_reasoning(*, model: str, wire: str, thinking: Any) -> ReasoningApplication:
    """Resolve one DeepSeek reasoning decision, or raise before dispatch.

    A KNOWN model on a route DeepSeek does not serve (Pro on Responses) raises
    first, whether or not an effort was configured — omitting the level cannot
    make an impossible route work.

    Otherwise an omitted/``default`` value yields an empty payload, including
    for an unknown future model: omission stays omission and never adds
    reasoning fields. Any explicit value on a route with no capability entry
    raises.
    """
    if wire not in (WIRE_CHAT, WIRE_RESPONSES):
        raise ValueError(f"unknown DeepSeek wire {wire!r}")

    # A KNOWN model on a route DeepSeek does not serve (today: Pro on
    # Responses) is impossible regardless of effort, so it fails here — before
    # the omission short-circuit and before any adapter/session is built.
    # Omitting the effort cannot make an unsupported route work.
    #
    # This is deliberately narrower than "no capability entry": an UNKNOWN
    # future model keeps omission/no-fields (and still rejects explicit
    # effort below), so a new DeepSeek model works on Auto without a release.
    if model in _KNOWN_MODELS and (model, wire) not in _CAPABILITIES:
        raise ValueError(_unsupported_route_message(model, wire))

    if thinking is None or thinking == OMITTED:
        return ReasoningApplication(
            provider=PROVIDER,
            wire=wire,
            requested=OMITTED,
            normalized=OMITTED,
            emitted="omitted",
            provenance="omitted",
        )

    if not isinstance(thinking, str):
        raise ValueError(
            f"provider deepseek: thinking must be a string, "
            f"got {type(thinking).__name__} ({thinking!r}) "
            f"for model {model!r} on the {wire} wire"
        )

    cap = _CAPABILITIES.get((model, wire))
    if cap is None:
        raise ValueError(
            _unsupported_route_message(model, wire)
            + f"; refusing explicit effort {thinking!r}"
        )

    if thinking in cap.canonical:
        normalized, provenance = thinking, "explicit_config"
    elif thinking in cap.aliases:
        normalized, provenance = cap.aliases[thinking], "compat_alias"
    else:
        raise ValueError(
            f"provider deepseek: {thinking!r} is not a supported reasoning "
            f"effort for model {model!r} on the {wire} wire; "
            f"canonical levels: {', '.join(cap.canonical)}"
            + (
                f" (official compatibility aliases: {', '.join(sorted(cap.aliases))})"
                if cap.aliases
                else ""
            )
        )

    if wire == WIRE_RESPONSES:
        payload: Mapping[str, Any] = MappingProxyType(
            {"reasoning": {"effort": normalized}}
        )
        emitted = normalized
    elif normalized == DISABLED:
        # Chat ``none`` disables thinking and carries no effort at all.
        payload = MappingProxyType({"extra_body": {"thinking": {"type": "disabled"}}})
        emitted = "disabled"
    else:
        payload = MappingProxyType(
            {
                "reasoning_effort": normalized,
                "extra_body": {"thinking": {"type": "enabled"}},
            }
        )
        emitted = normalized

    return ReasoningApplication(
        provider=PROVIDER,
        wire=wire,
        requested=thinking,
        normalized=normalized,
        emitted=emitted,
        provenance=provenance,
        payload=payload,
    )


#: Generic knobs that this route has taken ownership of. Configuring one on a
#: DeepSeek block is an error, not a no-op: it would look like it controls the
#: effort surface while doing nothing.
_SUPERSEDED_GENERIC_KEYS: Mapping[str, str] = MappingProxyType({
    "reasoning_effort_vocab": (
        "DeepSeek's reasoning-effort surface is provider-owned (per model and "
        "per wire), so the generic effort-vocabulary projection is not used on "
        "this route. Remove the field; set manifest.llm.thinking to one of the "
        "model's real levels instead."
    ),
})


def validate_supported_config(llm: Mapping[str, Any]) -> None:
    """Reject DeepSeek config keys this route has superseded.

    Fails closed with an actionable message rather than silently ignoring a
    setting the operator believes is doing something.
    """
    for key, reason in _SUPERSEDED_GENERIC_KEYS.items():
        if key in llm:
            raise ValueError(
                f"provider deepseek: {key!r} is not supported on the DeepSeek "
                f"route. {reason}"
            )


def validate_configured_thinking(*, model: Any, wire: str, thinking: Any) -> None:
    """Validate a manifest/preset value for a DeepSeek route.

    Ingress delegates here instead of testing the kernel-global level tuple, so
    a manifest can only carry what this exact route really accepts.
    """
    apply_reasoning(model=model, wire=wire, thinking=thinking)


class DeepSeekReasoningController:
    """The provider-local hook injected into the generic OpenAI transport."""

    __slots__ = ()

    def resolve_configured_thinking(self, thinking: Any) -> Any:
        """Own what an omitted configured level means on this route."""
        return resolve_configured_thinking(thinking)

    def apply(self, *, model: str, wire: str, thinking: Any) -> ReasoningApplication:
        return apply_reasoning(model=model, wire=wire, thinking=thinking)
