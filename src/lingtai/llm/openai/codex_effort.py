"""Codex provider-local active-route descriptor for runtime reasoning effort.

This module owns the ONE native integration where LingTai can currently change
effort at runtime, and nothing else:

  * integration — the LingTai main-agent native ``codex`` provider path
    (``CodexOpenAIAdapter`` / ``CodexResponsesSession``). The ``codex``,
    ``codex-pool``, and ``codex_pool`` provider names are configuration
    spellings of that ONE adapter (``lingtai/llm/_register.py``), so they share
    this descriptor and its fingerprint rather than naming three routes;
  * model — exactly ``gpt-5.6-sol``;
  * endpoint — every non-empty endpoint selected for the native Codex session;
    ``None`` means the official default, while explicit endpoints are normalized
    and retained as part of the session descriptor/fingerprint;
  * wire — OpenAI Responses. The Codex WebSocket ``response.create`` frame is
    derived from the same request kwargs, so it is the same wire for this
    purpose, not a second route;
  * exact values — ``low | medium | high | xhigh | max | ultra``.

**Why not ``THINKING_LEVELS``.** The kernel-global vocabulary
(``lingtai.kernel.config.THINKING_LEVELS``) is a provider-agnostic preset
vocabulary: it has no ``ultra`` and it does carry ``none``/``minimal``, which
this route does not accept. Deriving capability from it would either under-serve
the route or authorize values on models that reject them. Effort capability is a
per-route fact and lives here, provider-local.

**Three different "default"-ish facts, deliberately never merged.**

1. ``CODEX_EFFORT_PROVIDER_DEFAULT`` (``low``) — evidence about what the
   provider's own catalog would do if the field were omitted.
2. ``CODEX_EFFORT_OMITTED_DEFAULT_POLICY`` (``xhigh``) — LingTai's fixed adapter
   policy for an OMITTED or ``default`` thinking level: rather than omitting the
   field and inheriting the lower backend default, ``CodexOpenAIAdapter`` sends
   an explicit ``xhigh``. This is a property of the *adapter*, not of any
   particular session.
3. ``CodexEffortDescriptor.construction_baseline`` — the effort THIS session
   actually constructed with, already normalized by
   ``_responses_reasoning_kwargs``. A stock main agent has no manifest
   ``thinking``, so ``AgentConfig().thinking`` is ``"high"`` and
   ``SessionManager`` constructs ``reasoning.effort="high"`` — **not** ``xhigh``.

Only (3) is the capability baseline, and it is part of the fingerprint. Treating
(2) as every session's baseline would make merely binding a controller change the
next unchanged dispatch from ``high`` to ``xhigh`` — a silent behavior change
this route must never make. ``clear`` restores (3): the session's own
construction effort, not ``xhigh`` and not the provider value ``none``.

A construction effort outside the exact authorized values (``none`` /
``minimal``, or anything unrecognized) makes the route resolve to ``None``: fail
closed rather than fabricate a baseline the session never constructed with.

Unknown or conflicted routes resolve to ``None`` — fail closed, no capability,
no wire mutation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from lingtai.kernel.llm.reasoning_effort import ReasoningEffortCapability
from lingtai.llm._register import CODEX_OFFICIAL_BASE_URL

#: The one native Codex integration this descriptor describes.
CODEX_EFFORT_INTEGRATION = "lingtai_main_agent_native"

#: The route's stable provider identity — the native adapter, NOT the
#: configuration alias (``codex-pool`` / ``codex_pool``) that built it.
CODEX_EFFORT_PROVIDER_ROUTE = "codex"

#: Exact model. Nothing else in the current catalog is authorized here.
CODEX_EFFORT_MODEL = "gpt-5.6-sol"

#: Exact wire. The WebSocket frame is derived from the same Responses kwargs.
CODEX_EFFORT_WIRE = "openai_responses"

#: Exact ordered values for this route.
CODEX_EFFORT_VALUES: tuple[str, ...] = (
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)

#: What the provider catalog reports as its own default for this model.
CODEX_EFFORT_PROVIDER_DEFAULT = "low"

#: LingTai's fixed adapter policy for an OMITTED / ``default`` thinking level.
#: A property of the adapter, NOT the baseline of every session — see the module
#: docstring's "three different defaults".
CODEX_EFFORT_OMITTED_DEFAULT_POLICY = "xhigh"

#: The single request field this route actually emits.
CODEX_EFFORT_FIELD = "reasoning.effort"

#: Bumped when the recorded route evidence above changes.
CODEX_EFFORT_EVIDENCE_REVISION = 1


def _normalize_endpoint(base_url: str | None) -> str:
    """Normalize a configured base URL to a comparable endpoint identity.

    ``None`` means "the factory default", which is the official endpoint.
    Explicit values lose surrounding whitespace and trailing slashes; a value
    that normalizes empty is rejected by the resolver.
    """
    if base_url is None:
        return CODEX_OFFICIAL_BASE_URL
    return str(base_url).strip().rstrip("/")


@dataclass(frozen=True)
class CodexEffortDescriptor:
    """The immutable description of one authorized active route for one session.

    Per-SESSION, not per-model: ``construction_baseline`` records the effort the
    session actually constructed with, so a controller bound to this descriptor
    reproduces the unchanged wire until something explicitly overrides it.
    """

    integration: str
    provider_route: str
    model: str
    endpoint: str
    wire: str
    values: tuple[str, ...]
    provider_default: str
    omitted_default_policy: str
    construction_baseline: str
    emitted_field: str
    evidence_revision: int

    @property
    def fingerprint(self) -> str:
        """Stable identity of (integration, route, model, endpoint, wire,
        values, provider default, omitted-default policy, construction
        baseline, evidence revision).

        Alias-independent, so ``codex`` / ``codex-pool`` / ``codex_pool``
        produce the SAME fingerprint and a controller bound through one spelling
        stays valid through another. It DOES include the construction baseline:
        a rebuilt session at a different construction effort is a different
        route identity, so the controller drops the override rather than
        re-applying a value chosen against another baseline.
        """
        parts = "\0".join(
            (
                self.integration,
                self.provider_route,
                self.model,
                self.endpoint,
                self.wire,
                ",".join(self.values),
                self.provider_default,
                self.omitted_default_policy,
                self.construction_baseline,
                self.emitted_field,
                str(self.evidence_revision),
            )
        )
        return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:16]

    def supports(self, value: object) -> bool:
        return isinstance(value, str) and value in self.values

    def to_capability(self) -> ReasoningEffortCapability:
        """Project this descriptor onto the neutral kernel capability type.

        The capability baseline is the session's ACTUAL construction effort, so
        binding a controller can never move the wire on its own.
        """
        return ReasoningEffortCapability(
            available=True,
            route=f"{self.provider_route}:{self.model}",
            values=self.values,
            provider_default=self.provider_default,
            baseline=self.construction_baseline,
            settable=True,
            fingerprint=self.fingerprint,
            evidence_revision=self.evidence_revision,
            reason=None,
        )


def resolve_codex_effort_descriptor(
    *,
    model: str | None,
    base_url: str | None,
    construction_effort: str | None,
    wire: str = CODEX_EFFORT_WIRE,
) -> CodexEffortDescriptor | None:
    """Return the descriptor for this exact route/session, or ``None``.

    Every invariant axis must match: the exact model, a non-empty normalized
    endpoint selected for this native Codex session, the Responses wire, **and**
    a ``construction_effort`` that is one of this route's exact authorized
    values. ``construction_effort`` is the ALREADY-NORMALIZED effort the session
    was built with (what ``_responses_reasoning_kwargs`` put into
    ``reasoning.effort``), not the raw ``thinking`` level — the omitted/
    ``default`` sentinel has already become ``xhigh`` by the time it arrives.

    An unknown model, empty endpoint, another wire, or a construction effort this
    route does not authorize (``none`` / ``minimal`` / anything unrecognized)
    all yield ``None``. That leaves the request kwargs untouched and the
    capability truthfully unavailable — never a fabricated baseline the session
    did not construct with.
    """
    if not isinstance(model, str) or model.strip() != CODEX_EFFORT_MODEL:
        return None
    if wire != CODEX_EFFORT_WIRE:
        return None
    endpoint = _normalize_endpoint(base_url)
    if not endpoint:
        return None
    if not isinstance(construction_effort, str) or construction_effort not in CODEX_EFFORT_VALUES:
        return None
    return CodexEffortDescriptor(
        integration=CODEX_EFFORT_INTEGRATION,
        provider_route=CODEX_EFFORT_PROVIDER_ROUTE,
        model=CODEX_EFFORT_MODEL,
        endpoint=endpoint,
        wire=CODEX_EFFORT_WIRE,
        values=CODEX_EFFORT_VALUES,
        provider_default=CODEX_EFFORT_PROVIDER_DEFAULT,
        omitted_default_policy=CODEX_EFFORT_OMITTED_DEFAULT_POLICY,
        construction_baseline=construction_effort,
        emitted_field=CODEX_EFFORT_FIELD,
        evidence_revision=CODEX_EFFORT_EVIDENCE_REVISION,
    )
