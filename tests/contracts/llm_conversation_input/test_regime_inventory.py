"""Executable provider/configuration -> adapter/session-class matrix.

Every selectable production edge is *built through the real ``LLMService``
factory* with the SDK client mocked, and the returned adapter/session/proxy
**class** is asserted. So the mapping is the data under test — a wrong
``(provider, config)`` -> class row makes the build return a different class and
the test fails, and rebinding a registry provider to a different factory fails
the matrix (proven by the mutation tests below), which a name-set union cannot do.

Layers (see ``regimes.py``):

* **Registry matrix** — every registered provider name built through the real
  ``LLMService`` (``test_registry_matrix_*``). The union of built provider names
  equals the registry key set.
* **Custom-family schema cross-product** — schema selectability
  (``validate_init``) checked *separately* from the concrete factory result
  (``test_custom_schema_*``).
* **Mutation/counterexample proofs** — rebinding registry providers to the wrong
  factory fails the real matrix (``test_rebinding_*``).
* Gemini Chat factory-reachability, the canonical-fixture factory-shape anchor,
  and the one non-conforming regime flag.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from lingtai.init_schema import validate_init
from lingtai.kernel.llm.interface import ToolResultBlock
from lingtai.llm.base import _GatedSession
from lingtai.llm.service import LLMService
from tests.contracts.llm_conversation_input import regimes


# ---------------------------------------------------------------------------
# Layer 1 — Registry matrix: real LLMService, asserted classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "edge", regimes.REGISTRY_EDGES, ids=lambda e: e.id()
)
def test_registry_matrix_builds_expected_classes(edge: regimes.RegistryEdge) -> None:
    """Each registered provider built via the real ``LLMService`` resolves to the
    exact adapter + session class the matrix declares, wrapped in
    ``_GatedSession`` iff a gate applies (MiniMax default 120 rpm; normal
    ``max_rpm``). The build is the exact production route, so a rebind or a
    factory class change fails here."""
    adapter, session = regimes.build_registry_edge(edge)
    with regimes.shutdown_gate(adapter):
        assert type(adapter).__name__ == edge.adapter_class, (
            f"{edge.id()}: expected adapter {edge.adapter_class}, "
            f"got {type(adapter).__name__}"
        )
        if edge.gated:
            assert isinstance(session, _GatedSession), (
                f"{edge.id()}: expected a _GatedSession, got {type(session).__name__}"
            )
            inner = session._inner
            assert type(inner).__name__ == edge.session_class, (
                f"{edge.id()}: expected gated inner {edge.session_class}, "
                f"got {type(inner).__name__}"
            )
        else:
            assert not isinstance(session, _GatedSession), (
                f"{edge.id()}: expected a bare session, got a _GatedSession"
            )
            assert type(session).__name__ == edge.session_class, (
                f"{edge.id()}: expected {edge.session_class}, "
                f"got {type(session).__name__}"
            )


def test_registry_matrix_covers_exactly_the_registered_providers() -> None:
    """The union of exact provider names the registry matrix builds equals the
    current registry key set. Adding a provider (a new registry key) or dropping
    one from the matrix fails here — this is factory coverage, not a name union
    that stays green when a provider is mapped to the wrong regime."""
    registered = regimes.registered_provider_names()
    built = regimes.registry_edge_provider_names()

    missing = registered - built
    extra = built - registered
    assert not missing, (
        f"registered providers with no real-build registry edge: {sorted(missing)}. "
        "Add a RegistryEdge in regimes.py when a new provider is registered."
    )
    assert not extra, f"registry matrix lists unregistered providers: {sorted(extra)}"


# ---------------------------------------------------------------------------
# Layer 2 — Custom-family schema cross-product (selectability vs factory)
# ---------------------------------------------------------------------------


# The (provider, edge) cross-product: every custom-family name x every generated
# schema edge. Selectability is per-provider, so this is the unit under test.
_SCHEMA_ROWS = [
    (provider, edge)
    for edge in regimes.CUSTOM_SCHEMA_EDGES
    for provider in regimes.CUSTOM_FAMILY
]


def _schema_row_id(row) -> str:
    provider, edge = row
    return f"{provider}:{edge.label()}"


@pytest.mark.parametrize("row", _SCHEMA_ROWS, ids=_schema_row_id)
def test_custom_schema_selectability_matches_validate_init(row) -> None:
    """``validate_init`` accepts/rejects each ``(provider, config)`` manifest exactly
    as the ``schema_accepts`` predicate declares. This is the *selectability*
    boundary — checked independently of the factory result — and it is per-provider:
    a non-``auto`` openai row is selectable for ``custom`` but rejected for the
    aliases ``grok``/``qwen``/``kimi``."""
    provider, edge = row
    data = regimes.custom_manifest(provider, edge)
    accepts = regimes.schema_accepts(provider, edge.api_compat, edge.wire_api)
    if accepts:
        validate_init(data)  # must not raise
    else:
        with pytest.raises(ValueError, match="OpenAI-compatible"):
            validate_init(data)


@pytest.mark.parametrize(
    "row",
    [r for r in _SCHEMA_ROWS if not r[1].factory_raises
     and regimes.schema_accepts(r[0], r[1].api_compat, r[1].wire_api)],
    ids=_schema_row_id,
)
def test_custom_schema_accepted_rows_build_expected_classes(row) -> None:
    """Every schema-accepted ``(provider, config)`` builds the exact adapter +
    session class through the real ``LLMService`` path. This couples the
    schema-selectable configuration to the concrete production result, so a wrong
    row or a route change fails. (grpc is an unknown api_compat that the schema
    leaves unvalidated; its auto/absent rows build the OpenAI fallback.)"""
    provider, edge = row
    adapter, session = regimes.build_custom_schema_edge(provider, edge)
    assert type(adapter).__name__ == edge.adapter_class, (
        f"{provider} {edge.label()}: expected adapter {edge.adapter_class}, "
        f"got {type(adapter).__name__}"
    )
    assert type(session).__name__ == edge.session_class, (
        f"{provider} {edge.label()}: expected session {edge.session_class}, "
        f"got {type(session).__name__}"
    )


@pytest.mark.parametrize(
    "row",
    [r for r in _SCHEMA_ROWS if r[1].factory_raises],
    ids=_schema_row_id,
)
def test_custom_schema_factory_error_rows_raise(row) -> None:
    """A schema-accepted-but-factory-refused configuration (openai/anthropic with
    no base_url) raises ``ValueError`` from the real factory — proving the schema
    boundary and the factory boundary are distinct."""
    provider, edge = row
    with pytest.raises(ValueError):
        regimes.build_custom_schema_edge(provider, edge)


def test_custom_family_aliases_share_the_same_factory() -> None:
    """``custom``/``grok``/``qwen``/``kimi`` bind to one ``_custom`` factory, so the
    schema cross-product applies to all four identically. (The build tests above
    already exercise all four; this pins the shared-factory invariant directly.)"""
    registered = regimes.registered_provider_names()
    for name in regimes.CUSTOM_FAMILY:
        assert name in registered, f"{name} is not a registered provider"
    factories = {
        name: LLMService._adapter_registry[name]  # type: ignore[attr-defined]
        for name in regimes.CUSTOM_FAMILY
    }
    unique = {id(f) for f in factories.values()}
    assert len(unique) == 1, (
        f"custom-family names must share one factory; got distinct factories: "
        f"{ {k: id(v) for k, v in factories.items()} }"
    )


# ---------------------------------------------------------------------------
# Mutation / counterexample proofs — a rebind fails the REAL matrix
# ---------------------------------------------------------------------------


class _rebound_registry:
    """Context manager: rebind registry provider names to another factory, then
    restore. This is the exact drift a name-set union cannot detect — the matrix
    must fail because the real build returns a different class.

    ``build_registry_edge`` re-runs the idempotent ``register_all_adapters`` on
    every build (restoring the true bindings), so while the rebind is active we
    also patch that function to a no-op — otherwise the mutation would be reverted
    before the build reads the registry.
    """

    def __init__(self, names, target_provider: str):
        self._names = tuple(names)
        self._target_provider = target_provider

    def __enter__(self):
        registry = LLMService._adapter_registry  # type: ignore[attr-defined]
        if self._target_provider not in registry:
            regimes.registered_provider_names()  # populate only if empty
        # Snapshot the WHOLE registry so exit restores the EXACT prior bindings
        # (same factory objects, not just the rebound names) — no identity drift
        # leaks to later tests.
        self._snapshot = dict(registry)
        target = registry[self._target_provider]
        for n in self._names:
            registry[n] = target
        # Freeze registration so build_registry_edge's idempotent
        # register_all_adapters() call does not restore the bindings mid-test.
        self._patch = patch(
            "lingtai.llm._register.register_all_adapters", lambda: None
        )
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        registry = LLMService._adapter_registry  # type: ignore[attr-defined]
        registry.clear()
        registry.update(self._snapshot)
        return False


def _registry_edge(provider: str, **overrides) -> regimes.RegistryEdge:
    return next(
        e for e in regimes.REGISTRY_EDGES
        if e.provider == provider
        and all(getattr(e, k) == v for k, v in overrides.items())
    )


def test_rebinding_custom_family_to_anthropic_fails_the_matrix() -> None:
    """Rebinding ALL of ``custom``/``grok``/``qwen``/``kimi`` to the Anthropic
    factory makes their real-build openai edge return an ``AnthropicChatSession``
    instead of ``OpenAIChatSession`` — so the matrix assertion fails. (Under the
    old name-set union this rebind stayed green.)"""
    edge = _registry_edge("custom")
    # Baseline: the real matrix passes unmutated.
    test_registry_matrix_builds_expected_classes(edge)
    with _rebound_registry(regimes.CUSTOM_FAMILY, target_provider="anthropic"):
        for provider in regimes.CUSTOM_FAMILY:
            mutated = _registry_edge(provider)
            with pytest.raises(AssertionError):
                test_registry_matrix_builds_expected_classes(mutated)


def test_rebinding_openrouter_to_anthropic_fails_the_matrix() -> None:
    """Rebinding ``openrouter`` to the Anthropic factory changes its real-build
    class from ``OpenRouterAdapter``/``OpenAIChatSession`` to Anthropic — the
    matrix assertion fails. (The old coverage check stayed green.)"""
    edge = _registry_edge("openrouter")
    test_registry_matrix_builds_expected_classes(edge)  # baseline passes
    with _rebound_registry(("openrouter",), target_provider="anthropic"):
        with pytest.raises(AssertionError):
            test_registry_matrix_builds_expected_classes(edge)


def test_rebinding_minimax_to_bare_anthropic_drops_the_gate() -> None:
    """MiniMax gates by its own default 120 rpm. Rebinding ``minimax`` to the plain
    ``anthropic`` factory (no default gate) makes the real build return a bare
    ``AnthropicChatSession`` — so the ``gated=True`` matrix row fails."""
    edge = _registry_edge("minimax")
    assert edge.gated, "minimax matrix row must expect a gate"
    test_registry_matrix_builds_expected_classes(edge)  # baseline: gated, passes
    with _rebound_registry(("minimax",), target_provider="anthropic"):
        with pytest.raises(AssertionError):
            test_registry_matrix_builds_expected_classes(edge)


# ---------------------------------------------------------------------------
# Gemini Chat reachability + canonical fixture anchoring + non-conforming flag
# ---------------------------------------------------------------------------


def test_gemini_chat_is_reachable_only_via_json_schema() -> None:
    """The dormant ``GeminiChatSession`` is factory-reachable through the real
    ``LLMService`` — but only via ``create_session(json_schema=...)``; without it
    the Gemini provider returns ``InteractionsChatSession``. This is the executable
    reachability proof for the non-conforming regime (dormant because no production
    caller sets json_schema, not because the path is dead)."""
    interactions = _registry_edge("gemini", label="gemini.interactions")
    chat = _registry_edge("gemini", label="gemini.chat")
    _a1, s1 = regimes.build_registry_edge(interactions)
    _a2, s2 = regimes.build_registry_edge(chat)
    assert type(s1).__name__ == "InteractionsChatSession"
    assert type(s2).__name__ == "GeminiChatSession"


def test_canonical_fixture_matches_adapter_factory_shape() -> None:
    """The directly-constructed canonical fixture is byte-identical to what a real
    adapter's ``make_tool_result_message`` produces for the same id — anchoring the
    fixture to production shape (every adapter returns the identical block for an
    explicit tool_call_id). No network — the adapter constructor makes no calls."""
    from lingtai.llm.openai.adapter import OpenAIAdapter

    adapter = OpenAIAdapter(api_key="test-key", base_url=regimes.FAKE_BASE_URL)
    produced = adapter.make_tool_result_message(
        regimes.TOOL_NAME, dict(regimes.TOOL_CONTENT), tool_call_id=regimes.TOOL_CALL_ID
    )
    assert isinstance(produced, ToolResultBlock)
    fixture = regimes.canonical_tool_result()
    assert produced.id == fixture.id == regimes.TOOL_CALL_ID
    assert produced.name == fixture.name == regimes.TOOL_NAME
    assert produced.content == fixture.content == regimes.TOOL_CONTENT


def test_behavior_regime_names_are_unique() -> None:
    names = [r.name for r in regimes.ALL_REGIMES]
    assert len(names) == len(set(names)), f"duplicate regime names in {names}"


def test_only_gemini_chat_is_non_conforming_and_every_regime_builds() -> None:
    """The one non-conforming regime (gemini_chat) is flagged and never presented
    as satisfying the common input surface; every regime has a real builder (no
    build=None + conforms=True escape)."""
    non_conforming = [r.name for r in regimes.ALL_REGIMES if not r.conforms]
    assert non_conforming == ["gemini_chat"], "unexpected non-conforming set"
    for regime in regimes.ALL_REGIMES:
        assert regime.build is not None, f"{regime.name} has no real builder"
