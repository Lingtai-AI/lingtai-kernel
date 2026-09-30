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
* **Wire-selector schema cross-product** — schema selectability
  (``validate_init``) for every family x ``wire_api`` and every removed provider
  name, checked *separately* from the concrete factory result
  (``test_wire_schema_*``).
* **Mutation/counterexample proofs** — rebinding registry providers to the wrong
  factory fails the real matrix (``test_rebinding_*``).
* The canonical-fixture factory-shape anchor and the all-regimes-conform flag.
"""
from __future__ import annotations

import json
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
    ``_GatedSession`` iff a gate applies (a normal ``max_rpm``). The build is the exact production route, so a rebind or a
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
        # Responses mode bit: for a row that pins it, assert the concrete
        # stateless mode on the built session (unwrapping a gate if any). A
        # class-only row would stay green if a factory ever built the
        # non-replay mode of the same OpenAIResponsesSession class.
        if edge.expected_stateless_replay is not None:
            target = session._inner if isinstance(session, _GatedSession) else session
            assert getattr(target, "_stateless_replay") is edge.expected_stateless_replay, (
                f"{edge.id()}: expected _stateless_replay="
                f"{edge.expected_stateless_replay}, got "
                f"{getattr(target, '_stateless_replay', '<missing>')!r}"
            )


def test_registry_matrix_covers_exactly_the_registered_providers() -> None:
    """A fresh ``register_all_adapters()`` registry exactly matches the matrix.

    Clear/re-register proves startup freshness rather than inheriting import-time
    state; snapshot/restore keeps this stateful assertion isolated from siblings.
    This is factory coverage, not a name union that stays green when a provider
    is mapped to the wrong regime.
    """
    from lingtai.llm._register import register_all_adapters

    registry = LLMService._adapter_registry  # type: ignore[attr-defined]
    snapshot = dict(registry)
    try:
        registry.clear()
        register_all_adapters()
        registered = set(registry)
    finally:
        registry.clear()
        registry.update(snapshot)

    built = regimes.registry_edge_provider_names()
    missing = registered - built
    extra = built - registered
    assert not missing, (
        f"registered providers with no real-build registry edge: {sorted(missing)}. "
        "Add a RegistryEdge in regimes.py when a new provider is registered."
    )
    assert not extra, f"registry matrix lists unregistered providers: {sorted(extra)}"


# ---------------------------------------------------------------------------
# Layer 2 — Wire-selector schema cross-product (selectability vs factory)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", regimes.WIRE_SCHEMA_ROWS, ids=lambda r: r.label())
def test_wire_schema_selectability_matches_validate_init(row) -> None:
    """``validate_init`` accepts/rejects each ``(provider, wire_api)`` manifest
    exactly as ``schema_accepts`` declares: non-``auto`` ``wire_api`` only for
    ``openai``, and every removed provider name rejected with a pointer to the
    ``openai``/``anthropic`` replacements."""
    from lingtai.init_schema import REMOVED_LLM_PROVIDERS

    data = regimes.schema_manifest(row)
    if regimes.schema_accepts(row.provider, row.wire_api):
        validate_init(data)  # must not raise
    elif row.provider in REMOVED_LLM_PROVIDERS:
        with pytest.raises(ValueError, match="was removed from LingTai"):
            validate_init(data)
    else:
        with pytest.raises(ValueError, match="only for provider openai"):
            validate_init(data)


@pytest.mark.parametrize(
    "row",
    [r for r in regimes.WIRE_SCHEMA_ROWS
     if r.adapter_class and regimes.schema_accepts(r.provider, r.wire_api)],
    ids=lambda r: r.label(),
)
def test_wire_schema_accepted_openai_rows_build_expected_classes(row) -> None:
    """Every schema-accepted ``openai`` wire row builds the exact adapter +
    session class through the real ``LLMService`` path: ``responses`` builds
    the (stateless) Responses session; omitted, ``auto``, and
    ``chat_completions`` build Chat Completions."""
    adapter, session = regimes.build_schema_row(row)
    assert type(adapter).__name__ == row.adapter_class
    assert type(session).__name__ == row.session_class
    if row.session_class == "OpenAIResponsesSession":
        assert session._stateless_replay is True


def test_removed_provider_names_are_not_registered() -> None:
    """A fresh registry holds exactly the four families and no removed name.

    Clear/re-register (snapshot/restore) so another test's hermetic
    registration cannot leak in, exactly like the registry-matrix check.
    """
    from lingtai.init_schema import REMOVED_LLM_PROVIDERS
    from lingtai.llm._register import LLM_PROVIDERS, register_all_adapters

    registry = LLMService._adapter_registry  # type: ignore[attr-defined]
    snapshot = dict(registry)
    try:
        registry.clear()
        register_all_adapters()
        registered = set(registry)
    finally:
        registry.clear()
        registry.update(snapshot)
    assert not (registered & set(REMOVED_LLM_PROVIDERS))
    assert registered == set(regimes.LLM_FAMILIES) == set(LLM_PROVIDERS)


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


def test_rebinding_openai_to_anthropic_fails_the_matrix() -> None:
    """Rebinding ``openai`` to the Anthropic factory makes its real-build chat
    edge return an ``AnthropicAdapter``/``AnthropicChatSession`` — so the matrix
    assertion fails. (A name-set union would stay green.)"""
    edge = _registry_edge("openai", label="openai.chat")
    test_registry_matrix_builds_expected_classes(edge)  # baseline passes
    with _rebound_registry(("openai",), target_provider="anthropic"):
        with pytest.raises(AssertionError):
            test_registry_matrix_builds_expected_classes(edge)


def test_rebinding_anthropic_to_openai_fails_the_matrix() -> None:
    edge = _registry_edge("anthropic", label="anthropic.bare")
    test_registry_matrix_builds_expected_classes(edge)  # baseline passes
    with _rebound_registry(("anthropic",), target_provider="openai"):
        with pytest.raises(AssertionError):
            test_registry_matrix_builds_expected_classes(edge)


# ---------------------------------------------------------------------------
# Canonical fixture anchoring + regime flags
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Responses is stateless on every endpoint — mode bit AND wire, via LLMService
# ---------------------------------------------------------------------------


def test_official_and_compatible_responses_share_the_stateless_mode() -> None:
    """Both ``openai`` Responses rows — the official endpoint and a compatible
    ``base_url`` — pin the same stateless mode bit on the same class."""
    official = _registry_edge("openai", label="openai.official.responses")
    compat = _registry_edge("openai", label="openai.responses")
    assert official.session_class == compat.session_class == "OpenAIResponsesSession"
    assert official.expected_stateless_replay is True
    assert compat.expected_stateless_replay is True


@pytest.mark.parametrize("label", ["openai.official.responses", "openai.responses"])
def test_responses_wire_replays_full_history_without_resume_id(label: str) -> None:
    """``openai`` Responses built through the real ``LLMService`` is stateless
    on every endpoint: turn 2 replays the FULL canonical history (user 1,
    assistant 1, user 2) and sends NO ``previous_response_id``; no server-side
    resume id is exposed."""
    edge = _registry_edge("openai", label=label)
    session, transport = regimes.build_responses_mode_via_service(edge)
    assert session._stateless_replay is True

    session.send("first")
    session.send("second")

    assert "previous_response_id" not in transport.kwargs[0]
    assert "previous_response_id" not in transport.kwargs[1]
    # Turn 2 replays the whole conversation, not a delta: user 1, the recorded
    # assistant turn 1 (its exact item shape — projected text or the replayed
    # raw provider output item — is owned by the converter tests), user 2.
    replay = transport.kwargs[1]["input"]
    assert len(replay) == 3
    assert replay[0] == {"role": "user", "content": "first"}
    assert replay[2] == {"role": "user", "content": "second"}
    assert "ok" in json.dumps(replay[1])
    assert session.session_resume_id is None


def test_every_regime_conforms_and_builds() -> None:
    """Every behavior regime conforms to the common input surface and has a
    real builder (no build=None + conforms=True escape)."""
    assert all(r.conforms for r in regimes.ALL_REGIMES)
    for regime in regimes.ALL_REGIMES:
        assert regime.build is not None, f"{regime.name} has no real builder"
