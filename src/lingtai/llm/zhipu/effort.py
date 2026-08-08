"""Zhipu (GLM) provider-owned reasoning normalizer and wire renderer.

GLM exposes reasoning as **two independent top-level Chat Completions fields**,
not as the OpenAI Responses nested ``reasoning: {"effort": ...}`` object:

    ``thinking``          -- mode axis, ``{"type": "enabled" | "disabled"}``
    ``reasoning_effort``  -- intensity axis, effective only while enabled

The documented cross-product has exactly four observable outcomes, so a single
manifest scalar enumerates them without flattening the two axes:

    ==========================  ====================  =================
    manifest.llm.thinking       ``thinking``          ``reasoning_effort``
    ==========================  ====================  =================
    omitted / "default" / None  *(absent)*            *(absent)*
    "none"                      {"type": "disabled"}  *(absent)*
    "high"                      {"type": "enabled"}   "high"
    "max"                       {"type": "enabled"}   "max"
    ==========================  ====================  =================

Omission stays omission: no field is emitted and GLM applies its documented
``enabled`` / ``max`` default. ``"default"`` is an internal sentinel only and is
never accepted as a user literal (the schema/preset validators reject it).
Python ``None`` is treated as omission **here** because at the direct adapter
boundary there is no way to distinguish "the user wrote null" from "a caller
passed None"; manifest ``null`` is rejected earlier by ``init_schema``.

``reasoning_effort`` is documented only for GLM-5.2, and a separate guide says
"GLM-5.2 and above" — that conflict is unresolved, so the gate **fails closed**:
explicit values are accepted only for the normalized model id ``glm-5.2``, while
omission stays valid for every model. Matching normalizes the spelling; the
configured spelling is preserved verbatim on the wire.

This module is the single source of truth for the GLM reasoning decision: the
same frozen descriptor renders both the wire bytes and the observability fields,
so a log line can never disagree with what was actually sent.

``clear_thinking`` is deliberately never emitted — it is a history-retention
axis, not an effort axis.
"""

from __future__ import annotations

from dataclasses import dataclass

# Provider-scoped manifest vocabulary, imported from the kernel so the schema /
# preset validators and this renderer can never drift apart. Deliberately NOT
# the global ``THINKING_LEVELS`` tuple.
from ...kernel.config import ZHIPU_THINKING_LEVELS

# Internal omission sentinel shared with the kernel/service layer.
ZHIPU_THINKING_OMITTED = "default"

# Dated capability metadata. The upstream docs were last read on 2026-08-05 and
# were NOT re-fetched when this slice landed, so the model set is carried as
# dated evidence rather than as a verified fact.
ZHIPU_EFFORT_CAPABLE_MODELS = ("glm-5.2",)
ZHIPU_EFFORT_CAPABILITY_SOURCE = "zhipu_docs_20260805"
ZHIPU_EFFORT_MODEL_VERIFIED = False

_SOURCE_OMITTED = "lingtai_zhipu_omitted"
_SOURCE_EXPLICIT = "explicit_config"


def _safe_repr(value, limit: int = 40) -> str:
    """Bounded repr for error echoes — never echo an unbounded caller value."""
    text = repr(value)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def normalize_model_for_capability(model: str | None) -> str:
    """Normalize a model id for capability matching only (never for the wire)."""
    return str(model or "").strip().lower()


@dataclass(frozen=True)
class ZhipuEffort:
    """One frozen GLM reasoning decision.

    Drives both the serialized request body (:attr:`extra_body`) and the log
    fields (:meth:`observability_fields`) so the two can never diverge.
    """

    requested: str          # "omitted" | "none" | "high" | "max"
    mode: str | None        # None | "disabled" | "enabled"
    effort: str | None      # None | "high" | "max"
    source: str             # _SOURCE_OMITTED | _SOURCE_EXPLICIT

    @property
    def extra_body(self) -> dict:
        """Exact GLM reasoning fields for this decision.

        Returned as an ``extra_body`` payload: the OpenAI SDK hoists these
        members to **top-level JSON siblings** and consumes the wrapper key.
        Both axes ride this one dict so a flat SDK kwarg can never silently
        disagree with an ``extra_body`` member of the same name.
        """
        if self.mode is None:
            return {}
        payload: dict = {"thinking": {"type": self.mode}}
        if self.effort is not None:
            payload["reasoning_effort"] = self.effort
        return payload

    @property
    def normalized(self) -> str:
        """``omitted`` | ``disabled`` | ``high`` | ``max``."""
        if self.mode is None:
            return "omitted"
        if self.mode == "disabled":
            return "disabled"
        return self.effort or "omitted"

    @property
    def actual(self) -> str:
        """The exact wire pair rendered compactly for logs."""
        if self.mode is None:
            return "omitted"
        if self.effort is None:
            return f"thinking={self.mode}"
        return f"thinking={self.mode},effort={self.effort}"

    def observability_fields(self) -> dict:
        """Safe, provider-neutral log fields derived from this decision.

        Carries no credential, base URL, prompt/body, session id, or raw
        provider payload.
        """
        return {
            "reasoning_requested": self.requested,
            "reasoning_normalized": self.normalized,
            "reasoning_actual": self.actual,
            "reasoning_source": self.source,
            "reasoning_capability_source": ZHIPU_EFFORT_CAPABILITY_SOURCE,
        }


#: The single omission descriptor — emits nothing and defers to GLM's default.
OMITTED_EFFORT = ZhipuEffort(
    requested="omitted", mode=None, effort=None, source=_SOURCE_OMITTED
)


def normalize_zhipu_effort(thinking, model: str | None) -> ZhipuEffort:
    """Normalize a configured thinking value into one frozen GLM decision.

    ``None`` and the internal ``"default"`` sentinel both mean omission. Every
    other value must be exactly one of :data:`ZHIPU_THINKING_LEVELS` and is
    accepted only for a capability-listed model; anything else raises
    ``ValueError`` before session construction, so no request is ever dispatched
    with a silently coerced effort.
    """
    if thinking is None or (
        isinstance(thinking, str) and thinking == ZHIPU_THINKING_OMITTED
    ):
        return OMITTED_EFFORT

    # ``isinstance(True, str)`` is False, so booleans and ints fall through to
    # the loud rejection below rather than comparing equal to a level token.
    if not isinstance(thinking, str) or thinking not in ZHIPU_THINKING_LEVELS:
        raise ValueError(
            "manifest.llm.thinking for provider 'zhipu'/'glm': expected one of "
            f"{', '.join(ZHIPU_THINKING_LEVELS)} "
            f"(got {_safe_repr(thinking)})"
        )

    normalized_model = normalize_model_for_capability(model)
    if normalized_model not in ZHIPU_EFFORT_CAPABLE_MODELS:
        raise ValueError(
            "manifest.llm.thinking for provider 'zhipu'/'glm': explicit "
            f"{', '.join(ZHIPU_THINKING_LEVELS)} requires one of "
            f"{', '.join(ZHIPU_EFFORT_CAPABLE_MODELS)} "
            f"(got model {_safe_repr(model)}; capability source "
            f"{ZHIPU_EFFORT_CAPABILITY_SOURCE}, model_verified="
            f"{str(ZHIPU_EFFORT_MODEL_VERIFIED).lower()}). Omit thinking to "
            "defer to the provider default."
        )

    if thinking == "none":
        return ZhipuEffort(
            requested="none", mode="disabled", effort=None, source=_SOURCE_EXPLICIT
        )
    return ZhipuEffort(
        requested=thinking, mode="enabled", effort=thinking, source=_SOURCE_EXPLICIT
    )
