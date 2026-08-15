"""Contract tests for the Codex active-route effort descriptor + K1a main-agent
process-local live reasoning-effort vertical (issue #1197).

Scope of the ONE native integration under test:

  * integration — LingTai main-agent native ``codex`` provider path;
  * provider aliases ``codex`` / ``codex-pool`` / ``codex_pool`` share the one
    adapter, so they resolve the SAME route identity;
  * exact model ``gpt-5.6-sol`` only;
  * every non-empty endpoint selected for that native Codex session, with the
    normalized actual endpoint retained in its endpoint-sensitive fingerprint;
  * wire — OpenAI Responses REST plus the Codex WebSocket ``response.create``
    frame derived from the SAME request kwargs;
  * exact values ``low | medium | high | xhigh | max | ultra``; ``none`` and
    ``minimal`` are NOT part of this route;
  * THREE separate default-ish facts, never collapsed: the provider catalog
    default evidence ``low``; the adapter's fixed policy for an omitted or
    ``default`` thinking level, ``xhigh``; and the session's ACTUAL normalized
    construction baseline, which for a stock main agent
    (``AgentConfig().thinking == "high"``) is ``high``;
  * ``clear`` removes the runtime override and restores that session's own
    construction baseline — not ``xhigh``, and not the provider value ``none``.
    Merely owning a controller must never move the wire.

The capability source is deliberately NOT the provider-global kernel
``THINKING_LEVELS`` tuple (which has no ``ultra`` and does carry
``none``/``minimal``); a Codex provider-local descriptor owns it, and unknown or
conflicted routes fail closed with no wire mutation.

These are pure/mock tests — no network, no OAuth, no daemon, no filesystem
control protocol. Durability across process refresh/restart/molt is K1b and is
explicitly out of scope here: the controller is process-local by design.

New production symbols are imported INSIDE test bodies (``importlib``/``getattr``)
so this module always collects; a missing implementation must fail as a test
failure, never as a collection error.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from lingtai.kernel.config import THINKING_LEVELS
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.llm.base import _GatedSession
from lingtai.llm.api_gate import APICallGate
from lingtai.llm.openai.adapter import (
    CodexOpenAIAdapter,
    OpenAIAdapter,
    _codex_incremental_diagnose,
    _CodexLastResponse,
)
from lingtai.llm.service import LLMService

CODEX_OFFICIAL_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_PROXY_BASE_URL = "https://codex-proxy.example.test/v1"
CODEX_CUSTOM_BASE_URL = "http://codex-gateway.internal.test/responses"
CODEX_ROUTE_MODEL = "gpt-5.6-sol"
CODEX_ROUTE_VALUES = ("low", "medium", "high", "xhigh", "max", "ultra")
#: What ``CodexOpenAIAdapter`` constructs for an OMITTED/``default`` thinking
#: level. It is a fixed adapter policy — NOT "the baseline of every session".
OMITTED_DEFAULT_POLICY = "xhigh"
#: What a real main agent actually constructs today: ``AgentConfig().thinking``
#: is ``"high"`` and ``SessionManager`` passes ``self._config.thinking or
#: "high"``, so the unchanged construction wire is ``reasoning.effort="high"``.
DEFAULT_AGENT_CONSTRUCTION = "high"
PROVIDER_CATALOG_DEFAULT = "low"


# ---------------------------------------------------------------------------
# Deferred imports of the not-yet-existing production surface
# ---------------------------------------------------------------------------


def _codex_effort():
    """The Codex provider-local active-route descriptor module."""
    return importlib.import_module("lingtai.llm.openai.codex_effort")


def _reasoning_effort():
    """The neutral kernel-owned reasoning-effort controller module."""
    return importlib.import_module("lingtai.kernel.llm.reasoning_effort")


def _descriptor(
    model: str = CODEX_ROUTE_MODEL,
    base_url: str | None = CODEX_OFFICIAL_BASE_URL,
    construction_effort: str | None = OMITTED_DEFAULT_POLICY,
):
    """Resolve the active route for a session built with ``construction_effort``.

    The descriptor is per-session, not per-model: it must carry the effort the
    session ACTUALLY constructed with, so binding a controller can never change
    the unchanged wire on its own.
    """
    return _codex_effort().resolve_codex_effort_descriptor(
        model=model, base_url=base_url, construction_effort=construction_effort
    )


def _controller_for_route():
    """A neutral controller already bound to the real Codex route capability."""
    controller = _reasoning_effort().ReasoningEffortController()
    controller.bind_capability(_descriptor().to_capability())
    return controller


# ---------------------------------------------------------------------------
# Fakes — scripted provider turns, no network
# ---------------------------------------------------------------------------


@dataclass
class Event:
    type: str
    delta: str | None = None
    item: object | None = None
    response: object | None = None


def _usage(input_tokens: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=10,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def text_turn(resp_id: str = "resp_1") -> list[Event]:
    return [Event("response.completed", response=SimpleNamespace(id=resp_id, usage=_usage()))]


def reasoning_turn(resp_id: str, item_id: str = "rs_1") -> list[Event]:
    """A turn that returns an encrypted reasoning item (recorded for replay)."""
    return [
        Event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="reasoning",
                id=item_id,
                summary=[],
                content=[],
                encrypted_content="OPAQUE_BLOB",
            ),
        ),
        Event("response.completed", response=SimpleNamespace(id=resp_id, usage=_usage())),
    ]


class ScriptedResponses:
    """Fake ``client.responses`` — a queue of scripted turns.

    ``on_create`` runs before each turn is served, so a test can mutate live
    controller state DURING an in-flight dispatch.
    """

    def __init__(self, turns, on_create=None):
        self._turns = list(turns)
        self._idx = 0
        self.create_calls: list[dict] = []
        self.compact_calls: list[dict] = []
        self._on_create = on_create

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self._on_create is not None:
            self._on_create(len(self.create_calls), kwargs)
        assert self._idx < len(self._turns), (
            f"ScriptedResponses ran out of scripted turns at call {self._idx + 1}"
        )
        turn = self._turns[self._idx]
        self._idx += 1
        if isinstance(turn, Exception):
            raise turn
        return iter(turn)

    def compact(self, **kwargs):
        self.compact_calls.append(kwargs)
        return SimpleNamespace(output=[])


class FakeClient:
    def __init__(self, turns, on_create=None):
        self.responses = ScriptedResponses(turns, on_create=on_create)
        self.api_key = "fake"


class EncryptedContentError(Exception):
    """Mimics the Codex 400 that triggers the encrypted-reasoning self-heal."""

    def __init__(self):
        super().__init__(
            "The encrypted content for item rs_1 could not be verified"
        )


def _make_codex_session(
    *,
    model: str = CODEX_ROUTE_MODEL,
    base_url: str | None = CODEX_OFFICIAL_BASE_URL,
    thinking: str = "default",
    turns=None,
    on_create=None,
    max_rpm: int = 0,
    context_window: int = 100_000,
    compact_token_limit: int | None = None,
    codex_base_urls=None,
):
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url=base_url,
        use_responses=True,
        force_responses=True,
        max_rpm=max_rpm,
        codex_compact_token_limit=compact_token_limit,
        codex_base_urls=codex_base_urls,
    )
    fake_client = FakeClient(turns or [], on_create=on_create)
    adapter._client = fake_client
    session = adapter.create_chat(
        model,
        "system prompt",
        tools=None,
        thinking=thinking,
        context_window=context_window,
    )
    # A selected pool endpoint may repoint the adapter during ``create_chat``;
    # keep the resulting session hermetic after descriptor construction.
    session._client = fake_client
    return session


def _sent_effort(create_kwargs: dict) -> str | None:
    reasoning = create_kwargs.get("reasoning")
    if not isinstance(reasoning, dict):
        return None
    return reasoning.get("effort")


def _bind_controller(session, controller) -> bool:
    """Install the controller's snapshot provider on a session, K1a style."""
    return session.set_reasoning_effort_policy(controller.snapshot)


# ---------------------------------------------------------------------------
# Invariant 1 — exact active-route descriptor; unknown routes fail closed
# ---------------------------------------------------------------------------


def test_descriptor_exposes_exact_route_values_and_two_distinct_defaults():
    descriptor = _descriptor()

    assert descriptor is not None
    assert descriptor.model == CODEX_ROUTE_MODEL
    assert tuple(descriptor.values) == CODEX_ROUTE_VALUES
    assert "ultra" in descriptor.values
    assert "none" not in descriptor.values
    assert "minimal" not in descriptor.values
    # Provider catalog default evidence, the adapter's omitted/``default``
    # policy, and this session's actual construction baseline are THREE
    # different facts and must never be merged into one "default".
    assert descriptor.provider_default == PROVIDER_CATALOG_DEFAULT
    assert descriptor.omitted_default_policy == OMITTED_DEFAULT_POLICY
    assert descriptor.construction_baseline == OMITTED_DEFAULT_POLICY
    assert descriptor.provider_default != descriptor.construction_baseline
    assert _descriptor(construction_effort="high").construction_baseline == "high"
    assert descriptor.emitted_field == "reasoning.effort"
    assert descriptor.fingerprint


def test_descriptor_normalizes_actual_custom_endpoint_and_fingerprints_it():
    proxy = _descriptor(base_url=f"  {CODEX_PROXY_BASE_URL}///  ")
    custom = _descriptor(base_url=f"{CODEX_CUSTOM_BASE_URL}/")

    assert proxy is not None and custom is not None
    assert proxy.endpoint == CODEX_PROXY_BASE_URL
    assert custom.endpoint == CODEX_CUSTOM_BASE_URL
    assert proxy.fingerprint == _descriptor(base_url=CODEX_PROXY_BASE_URL).fingerprint
    assert proxy.fingerprint != custom.fingerprint


def test_default_and_normalized_official_endpoint_are_equivalent():
    descriptors = (
        _descriptor(base_url=None),
        _descriptor(base_url=f"{CODEX_OFFICIAL_BASE_URL}/"),
        _descriptor(base_url=f"  {CODEX_OFFICIAL_BASE_URL}///  "),
    )

    assert all(descriptor is not None for descriptor in descriptors)
    assert {descriptor.endpoint for descriptor in descriptors} == {CODEX_OFFICIAL_BASE_URL}
    assert len({descriptor.fingerprint for descriptor in descriptors}) == 1


@pytest.mark.parametrize("base_url", ["", "  \t\n  "])
def test_empty_endpoint_fails_closed(base_url):
    assert _descriptor(base_url=base_url) is None


def test_descriptor_is_not_derived_from_global_thinking_levels():
    """The capability source is provider-local, not the kernel-global tuple."""
    # Guard against a regression that widens the shared vocabulary instead.
    assert "ultra" not in THINKING_LEVELS
    assert "none" in THINKING_LEVELS and "minimal" in THINKING_LEVELS

    values = set(_descriptor().values)
    assert values != set(THINKING_LEVELS)


@pytest.mark.parametrize("model", ["gpt-5.5", "gpt-5.6-sol-preview"])
def test_unknown_model_fails_closed(model):
    assert _descriptor(model=model, base_url=CODEX_PROXY_BASE_URL) is None


def test_unknown_wire_fails_closed():
    resolve = _codex_effort().resolve_codex_effort_descriptor
    assert resolve(
        model=CODEX_ROUTE_MODEL,
        base_url=CODEX_OFFICIAL_BASE_URL,
        construction_effort=OMITTED_DEFAULT_POLICY,
        wire="chat_completions",
    ) is None


# ---------------------------------------------------------------------------
# Invariant 2 — revisioned self get/set/clear; invalid set does not mutate
# ---------------------------------------------------------------------------


def test_get_set_clear_is_revisioned_and_clear_restores_construction_baseline():
    controller = _controller_for_route()

    start = controller.status()
    assert start["available"] is True
    assert start["effective"] == OMITTED_DEFAULT_POLICY
    assert start["override"] is None
    assert start["baseline"] == OMITTED_DEFAULT_POLICY
    assert start["provider_default"] == PROVIDER_CATALOG_DEFAULT
    assert list(start["values"]) == list(CODEX_ROUTE_VALUES)

    result = controller.set("ultra")
    assert result.ok is True
    after_set = controller.status()
    assert after_set["override"] == "ultra"
    assert after_set["effective"] == "ultra"
    assert after_set["revision"] > start["revision"]

    cleared = controller.clear()
    assert cleared.ok is True
    after_clear = controller.status()
    assert after_clear["override"] is None
    # ``clear`` restores the descriptor-owned LingTai baseline, NOT ``none``.
    assert after_clear["effective"] == OMITTED_DEFAULT_POLICY
    assert after_clear["revision"] > after_set["revision"]


@pytest.mark.parametrize("bad", ["none", "minimal", "default", "", "ULTRA", None, 3])
def test_invalid_set_does_not_mutate_state(bad):
    controller = _controller_for_route()
    controller.set("max")
    before = controller.status()

    result = controller.set(bad)

    assert result.ok is False
    assert controller.status() == before


def test_unavailable_route_is_get_only():
    controller = _reasoning_effort().ReasoningEffortController()
    status = controller.status()
    assert status["available"] is False
    assert status["effective"] is None

    result = controller.set("ultra")
    assert result.ok is False
    assert controller.status()["override"] is None


# ---------------------------------------------------------------------------
# Invariant 3 — construction xhigh today; a runtime set moves the NEXT request
# ---------------------------------------------------------------------------


def test_without_controller_next_request_still_emits_construction_xhigh():
    session = _make_codex_session(turns=[text_turn("r1")])

    session.send("hello")

    assert _sent_effort(session._client.responses.create_calls[0]) == OMITTED_DEFAULT_POLICY


def test_runtime_set_changes_the_next_rest_request_only():
    session = _make_codex_session(turns=[text_turn("r1"), text_turn("r2"), text_turn("r3")])
    controller = _controller_for_route()
    assert _bind_controller(session, controller) is True

    session.send("first")
    controller.set("ultra")
    session.send("second")
    controller.clear()
    session.send("third")

    calls = session._client.responses.create_calls
    assert _sent_effort(calls[0]) == OMITTED_DEFAULT_POLICY
    assert _sent_effort(calls[1]) == "ultra"
    assert _sent_effort(calls[2]) == OMITTED_DEFAULT_POLICY


def test_dispatch_never_mutates_the_aliased_construction_kwargs():
    """The request must copy ``reasoning``; the session baseline is immutable."""
    session = _make_codex_session(turns=[text_turn("r1"), text_turn("r2")])
    controller = _controller_for_route()
    _bind_controller(session, controller)

    session.send("first")
    controller.set("low")
    session.send("second")

    assert session._extra_kwargs["reasoning"] == {"effort": OMITTED_DEFAULT_POLICY}
    calls = session._client.responses.create_calls
    assert calls[0]["reasoning"] is not calls[1]["reasoning"]
    assert calls[0]["reasoning"] is not session._extra_kwargs["reasoning"]


# ---------------------------------------------------------------------------
# Invariant 4 — the WS frame derives the same effort and forces a full frame
# ---------------------------------------------------------------------------


def test_ws_frame_derives_same_effort_and_marks_full_transition():
    session = _make_codex_session(turns=[text_turn("r1"), text_turn("r2")])
    controller = _controller_for_route()
    _bind_controller(session, controller)

    session.send("first")
    controller.set("max")
    response = session.send("second")

    calls = session._client.responses.create_calls
    # The WebSocket ``response.create`` frame is derived from the SAME request
    # kwargs, so it carries the identical effort without a second seam.
    frame = session._ws_frame_request(calls[1], calls[1]["input"])
    assert frame["reasoning"]["effort"] == "max"

    # Changing a non-input field is a full-frame transition by construction.
    prev_frame = session._ws_frame_request(calls[0], calls[0]["input"])
    delta, diag = _codex_incremental_diagnose(
        prev_frame,
        _CodexLastResponse(response_id="r1", items_added=[]).items_added,
        frame,
        allow_empty_delta=True,
    )
    assert delta is None
    assert diag["reason"] == "non_input_fields_changed"
    assert "reasoning" in diag["changed_fields"]
    # The real REST dispatch agrees: the changed turn is labelled full.
    assert response.usage.extra["codex_request_mode"] == "rest_full"


# ---------------------------------------------------------------------------
# Invariant 5 — an in-flight dispatch and its retry keep ONE snapshot
# ---------------------------------------------------------------------------


def test_mid_flight_set_does_not_affect_the_running_dispatch():
    controller = _controller_for_route()
    controller.set("low")

    def mutate_during_first_call(call_index, _kwargs):
        if call_index == 1:
            controller.set("ultra")

    session = _make_codex_session(
        turns=[text_turn("r1"), text_turn("r2")],
        on_create=mutate_during_first_call,
    )
    _bind_controller(session, controller)

    session.send("first")
    session.send("second")

    calls = session._client.responses.create_calls
    assert _sent_effort(calls[0]) == "low"      # in-flight change ignored
    assert _sent_effort(calls[1]) == "ultra"    # next dispatch picks it up


def test_self_heal_retry_reuses_the_same_snapshot():
    """The encrypted-reasoning retry must not re-read the controller."""
    controller = _controller_for_route()
    controller.set("max")

    session = _make_codex_session(
        turns=[reasoning_turn("r1"), EncryptedContentError(), text_turn("r2")],
    )
    _bind_controller(session, controller)

    session.send("first")

    def mutate_before_retry(call_index, _kwargs):
        if call_index == 2:
            controller.set("low")

    session._client.responses._on_create = mutate_before_retry
    session.send("second")

    calls = session._client.responses.create_calls
    assert len(calls) == 3  # first turn, rejected turn, self-heal retry
    assert _sent_effort(calls[1]) == "max"
    assert _sent_effort(calls[2]) == "max"  # retry reuses the captured snapshot


# ---------------------------------------------------------------------------
# Invariant 6 — an in-process rebuild keeps the override and rebinds the hook
# ---------------------------------------------------------------------------


class _ForwardingLLMService:
    """Production-shaped LLMService stand-in.

    Critically it FORWARDS ``SessionManager``'s real ``thinking`` argument to
    ``CodexOpenAIAdapter.create_chat``, exactly as the real ``LLMService`` does.
    An earlier stub discarded it and built every session at the omitted/default
    ``xhigh``, which hid the fact that a real agent constructs ``high``.
    """

    def __init__(self, *, gated: bool = False, model: str = CODEX_ROUTE_MODEL):
        self.model = model
        self.provider = "codex"
        self.sessions: list = []
        self.thinking_seen: list = []
        self._gated = gated

    def create_session(self, **kwargs):
        thinking = kwargs.get("thinking", "default")
        self.thinking_seen.append(thinking)
        session = _make_codex_session(
            model=kwargs.get("model") or self.model,
            thinking=thinking,
            turns=[text_turn(f"r{len(self.sessions) + 1}")],
        )
        if self._gated:
            session = _GatedSession(session, APICallGate(600))
        self.sessions.append(session)
        return session


def _make_session_manager(*, gated: bool = False, thinking: str | None = None):
    """A real ``SessionManager`` over the production-shaped service above.

    ``thinking=None`` uses the stock ``AgentConfig()``, i.e. exactly what a
    main agent with no manifest ``thinking`` setting runs with today.
    """
    from lingtai.kernel.config import AgentConfig
    from lingtai.kernel.session import SessionManager

    service = _ForwardingLLMService(gated=gated)
    config = AgentConfig() if thinking is None else AgentConfig(thinking=thinking)
    manager = SessionManager(
        llm_service=service,
        config=config,
        agent_name="tester",
        streaming=False,
        build_system_prompt_fn=lambda: "system prompt",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    return manager, service


def _last_wire_effort(session) -> str | None:
    inner = getattr(session, "_inner", session)
    return _sent_effort(inner._client.responses.create_calls[-1])


def test_in_process_rebuild_keeps_override_and_rebinds_snapshot_callback():
    manager, service = _make_session_manager()
    manager.ensure_session()

    assert manager.set_reasoning_effort("ultra").ok is True
    assert manager.reasoning_effort_status()["effective"] == "ultra"

    manager._rebuild_session(ChatInterface())

    assert len(service.sessions) == 2
    # The process-local override survives the in-process rebuild ...
    assert manager.reasoning_effort_status()["effective"] == "ultra"
    # ... and the NEW session emits it on its next real request.
    manager.send("after rebuild")
    rebuilt = service.sessions[1]
    assert _sent_effort(rebuilt._client.responses.create_calls[0]) == "ultra"


def test_policy_binding_survives_the_rate_gate_proxy():
    """A gate-wrapped session must not silently drop the policy binding."""
    manager, service = _make_session_manager(gated=True)
    manager.ensure_session()
    manager.set_reasoning_effort("max")

    manager.send("gated turn")

    gated = service.sessions[0]
    inner = gated._inner
    assert _sent_effort(inner._client.responses.create_calls[0]) == "max"


# ---------------------------------------------------------------------------
# Invariant 3 (real construction baseline) — binding a controller must not
# change the unchanged wire of a production-shaped session
# ---------------------------------------------------------------------------
#
# A real main agent has no manifest ``thinking``, so ``AgentConfig().thinking``
# is ``"high"`` and ``SessionManager`` constructs ``reasoning.effort="high"``.
# A capability baseline hardcoded to the adapter's omitted/``default`` policy
# (``xhigh``) would silently move that next no-override dispatch high -> xhigh.
# The baseline must be the session's ACTUAL normalized construction effort.


def test_production_shaped_default_agent_keeps_its_high_construction():
    manager, service = _make_session_manager()
    manager.ensure_session()

    # The service really did receive SessionManager's thinking argument.
    assert service.thinking_seen == [DEFAULT_AGENT_CONSTRUCTION]
    session = service.sessions[0]
    assert session._extra_kwargs["reasoning"] == {"effort": DEFAULT_AGENT_CONSTRUCTION}

    status = manager.reasoning_effort_status()
    assert status["available"] is True
    assert status["baseline"] == DEFAULT_AGENT_CONSTRUCTION
    assert status["effective"] == DEFAULT_AGENT_CONSTRUCTION
    assert status["override"] is None

    manager.send("no override")

    # The decisive assertion: merely owning a controller changed nothing.
    assert _last_wire_effort(session) == DEFAULT_AGENT_CONSTRUCTION


def test_set_then_clear_returns_to_the_real_construction_baseline():
    manager, service = _make_session_manager()
    manager.ensure_session()
    session = service.sessions[0]
    session._client.responses._turns.extend([text_turn("r2"), text_turn("r3")])

    manager.send("baseline turn")
    assert _last_wire_effort(session) == DEFAULT_AGENT_CONSTRUCTION

    assert manager.set_reasoning_effort("ultra").ok is True
    manager.send("override turn")
    assert _last_wire_effort(session) == "ultra"

    assert manager.clear_reasoning_effort().ok is True
    assert manager.reasoning_effort_status()["effective"] == DEFAULT_AGENT_CONSTRUCTION
    manager.send("cleared turn")
    # ``clear`` restores the session's OWN construction baseline, not xhigh.
    assert _last_wire_effort(session) == DEFAULT_AGENT_CONSTRUCTION


def test_explicit_xhigh_construction_reports_and_restores_xhigh():
    manager, service = _make_session_manager(thinking="xhigh")
    manager.ensure_session()
    session = service.sessions[0]
    session._client.responses._turns.append(text_turn("r2"))

    assert manager.reasoning_effort_status()["baseline"] == "xhigh"

    manager.set_reasoning_effort("low")
    manager.send("override turn")
    assert _last_wire_effort(session) == "low"

    manager.clear_reasoning_effort()
    manager.send("cleared turn")
    assert _last_wire_effort(session) == "xhigh"


def test_omitted_default_policy_is_a_separate_fact_from_the_session_baseline():
    """Three distinct facts; none of them may be collapsed into the others."""
    module = _codex_effort()
    # The adapter's fixed policy for an omitted/``default`` thinking level.
    assert module.CODEX_EFFORT_OMITTED_DEFAULT_POLICY == OMITTED_DEFAULT_POLICY

    high_session = _make_codex_session(thinking="high", turns=[text_turn("r1")])
    capability = high_session.reasoning_effort_capability()
    assert capability.available is True
    assert capability.baseline == "high"                      # actual construction
    assert capability.provider_default == PROVIDER_CATALOG_DEFAULT  # catalog evidence
    assert capability.baseline != OMITTED_DEFAULT_POLICY
    assert capability.baseline != capability.provider_default

    # An omitted/``default`` session really does construct the policy value.
    default_session = _make_codex_session(thinking="default", turns=[text_turn("r1")])
    assert default_session.reasoning_effort_capability().baseline == OMITTED_DEFAULT_POLICY


def test_capability_fingerprint_includes_the_construction_baseline():
    """Otherwise a rebuild at another construction effort would keep an
    override chosen against a different baseline."""
    high = _make_codex_session(thinking="high", turns=[]).reasoning_effort_capability()
    xhigh = _make_codex_session(thinking="xhigh", turns=[]).reasoning_effort_capability()

    assert high.fingerprint and xhigh.fingerprint
    assert high.fingerprint != xhigh.fingerprint

    controller = _reasoning_effort().ReasoningEffortController()
    controller.bind_capability(high)
    controller.set("ultra")
    assert controller.status()["override"] == "ultra"
    # Route drift: the override is dropped rather than re-applied.
    controller.bind_capability(xhigh)
    assert controller.status()["override"] is None
    assert controller.status()["effective"] == "xhigh"


def test_endpoint_fingerprint_drift_drops_a_prior_override():
    controller = _reasoning_effort().ReasoningEffortController()
    proxy = _descriptor(base_url=CODEX_PROXY_BASE_URL).to_capability()
    custom = _descriptor(base_url=CODEX_CUSTOM_BASE_URL).to_capability()

    controller.bind_capability(proxy)
    assert controller.set("ultra").ok is True
    assert controller.status()["override"] == "ultra"

    assert controller.bind_capability(custom) is False
    assert controller.status()["override"] is None
    assert controller.status()["effective"] == OMITTED_DEFAULT_POLICY


@pytest.mark.parametrize("thinking", ["none", "minimal"])
def test_construction_outside_the_route_values_fails_closed(thinking):
    """A construction effort this route does not authorize must not be
    replaced by a fabricated xhigh — the route is simply unavailable."""
    session = _make_codex_session(thinking=thinking, turns=[text_turn("r1")])

    assert session._extra_kwargs["reasoning"] == {"effort": thinking}
    assert session.reasoning_effort_capability().available is False
    assert session.set_reasoning_effort_policy(lambda: None) is False

    session.send("hello")
    # The unsupported construction value still rides the wire untouched.
    assert _sent_effort(session._client.responses.create_calls[0]) == thinking


def test_descriptor_rejects_an_unsupported_construction_effort():
    for bad in ("none", "minimal", "default", "", None, 3):
        assert _descriptor(construction_effort=bad) is None


# ---------------------------------------------------------------------------
# Invariant 7 — dispatch-start and completion observability agree, and a
# rejected attempt keeps truthful evidence
# ---------------------------------------------------------------------------


def test_dispatch_start_and_completion_evidence_agree():
    session = _make_codex_session(turns=[text_turn("r1")])
    controller = _controller_for_route()
    _bind_controller(session, controller)
    controller.set("ultra")
    expected_revision = controller.status()["revision"]

    response = session.send("hello")

    dispatch = session.last_reasoning_effort_dispatch()
    assert dispatch["effective"] == "ultra"
    assert dispatch["source"] == "override"
    assert dispatch["revision"] == expected_revision
    assert dispatch["completed"] is True

    extra = response.usage.extra
    assert extra["codex_reasoning_effort"] == "ultra"
    assert extra["codex_reasoning_effort_source"] == "override"
    assert extra["codex_reasoning_effort_revision"] == str(expected_revision)


def test_rejected_dispatch_keeps_truthful_attempt_evidence():
    session = _make_codex_session(turns=[RuntimeError("provider rejected effort")])
    controller = _controller_for_route()
    _bind_controller(session, controller)
    controller.set("ultra")

    with pytest.raises(RuntimeError):
        session.send("hello")

    dispatch = session.last_reasoning_effort_dispatch()
    assert dispatch is not None
    assert dispatch["effective"] == "ultra"
    assert dispatch["source"] == "override"
    # A failed attempt never claims completion, and is never auto-downgraded.
    assert dispatch["completed"] is False
    assert _sent_effort(session._client.responses.create_calls[0]) == "ultra"


def test_effort_evidence_is_allowlisted_for_the_kernel_event_seam():
    from lingtai.kernel.session import _safe_usage_extra_for_event

    safe = _safe_usage_extra_for_event({
        "codex_reasoning_effort": "ultra",
        "codex_reasoning_effort_source": "override",
        "codex_reasoning_effort_revision": "3",
        "codex_secret_prompt": "must not leak",
    })

    assert safe["codex_reasoning_effort"] == "ultra"
    assert safe["codex_reasoning_effort_source"] == "override"
    assert safe["codex_reasoning_effort_revision"] == "3"
    assert "codex_secret_prompt" not in safe


# ---------------------------------------------------------------------------
# Invariant 8 — everything outside the exact route is unchanged/unexposed
# ---------------------------------------------------------------------------


def test_generic_openai_responses_session_exposes_no_effort_capability():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True, force_responses=True)
    session = adapter.create_chat("gpt-5.1", "system prompt", tools=None)

    capability = session.reasoning_effort_capability()
    assert capability.available is False
    assert session.set_reasoning_effort_policy(lambda: None) is False


def test_unknown_codex_model_is_unavailable_and_never_mutates_the_wire():
    session = _make_codex_session(model="gpt-5.5", turns=[text_turn("r1")])
    controller = _reasoning_effort().ReasoningEffortController()
    controller.bind_capability(session.reasoning_effort_capability())

    assert session.reasoning_effort_capability().available is False
    assert _bind_controller(session, controller) is False
    assert controller.set("ultra").ok is False

    # No new self-facing query evidence exists before a send ...
    assert session.last_reasoning_effort_dispatch() is None

    response = session.send("hello")
    # Construction behavior is byte-identical to today — on the wire ...
    assert _sent_effort(session._client.responses.create_calls[0]) == OMITTED_DEFAULT_POLICY
    # ... in the usage-extra seam (which rides into events.jsonl via the kernel
    # allowlist) ...
    leaked = [k for k in response.usage.extra if k.startswith("codex_reasoning_effort")]
    assert leaked == []
    # ... and in the self-facing dispatch query, which must stay absent rather
    # than start reporting a record for a route that does not exist.
    assert session.last_reasoning_effort_dispatch() is None


@pytest.mark.parametrize(
    "base_url,codex_base_urls,normalized_endpoint",
    [
        (f"{CODEX_PROXY_BASE_URL}/", None, CODEX_PROXY_BASE_URL),
        (CODEX_OFFICIAL_BASE_URL, [CODEX_CUSTOM_BASE_URL], CODEX_CUSTOM_BASE_URL),
    ],
)
def test_custom_endpoint_session_applies_and_observes_runtime_override(
    base_url, codex_base_urls, normalized_endpoint
):
    session = _make_codex_session(
        base_url=base_url,
        codex_base_urls=codex_base_urls,
        turns=[text_turn("r1")],
    )
    capability = session.reasoning_effort_capability()
    controller = _reasoning_effort().ReasoningEffortController()

    assert capability.available is True
    assert session._effort_descriptor.endpoint == normalized_endpoint
    controller.bind_capability(capability)
    assert controller.set("ultra").ok is True
    assert _bind_controller(session, controller) is True

    response = session.send("hello")

    assert _sent_effort(session._client.responses.create_calls[0]) == "ultra"
    revision = controller.status()["revision"]
    assert {
        key: value
        for key, value in response.usage.extra.items()
        if key.startswith("codex_reasoning_effort")
    } == {
        "codex_reasoning_effort": "ultra",
        "codex_reasoning_effort_source": "override",
        "codex_reasoning_effort_revision": str(revision),
    }
    dispatch = session.last_reasoning_effort_dispatch()
    assert dispatch["effective"] == "ultra"
    assert dispatch["source"] == "override"
    assert dispatch["revision"] == revision
    assert dispatch["completed"] is True


def test_existing_pre_request_hook_still_fires_exactly_once():
    session = _make_codex_session(turns=[text_turn("r1")])
    controller = _controller_for_route()
    _bind_controller(session, controller)

    fired: list[int] = []
    session.pre_request_hook = lambda _iface: fired.append(1)

    session.send("hello")

    assert fired == [1]
    assert session.pre_request_hook is not None


def test_compaction_request_carries_no_reasoning_field():
    """A REAL standalone compact call must stay on its own request shape."""
    session = _make_codex_session(
        turns=[text_turn(f"r{i}") for i in range(6)],
        context_window=200,
        compact_token_limit=60,
    )
    controller = _controller_for_route()
    _bind_controller(session, controller)
    controller.set("ultra")

    for i in range(6):
        session.send(f"turn {i} with enough words to grow the projected context")

    compact_calls = session._client.responses.compact_calls
    # Guard against a vacuous assertion: compaction must actually have fired.
    assert len(compact_calls) >= 1
    for call in compact_calls:
        assert "reasoning" not in call
    # ... while the ordinary requests still carry the runtime override.
    assert _sent_effort(session._client.responses.create_calls[-1]) == "ultra"


# ---------------------------------------------------------------------------
# Invariant 9 — aliases share the one implementation and one route identity
# ---------------------------------------------------------------------------


def test_codex_aliases_share_one_factory_and_one_route_identity():
    from lingtai.llm._register import register_all_adapters

    register_all_adapters()
    registry = LLMService._adapter_registry
    factories = {registry[name] for name in ("codex", "codex-pool", "codex_pool")}
    assert len(factories) == 1

    descriptor = _descriptor()
    # The route identity is the native adapter route, never the configuration
    # spelling that happened to build it.
    assert descriptor.provider_route == "codex"
    assert descriptor.fingerprint == _descriptor().fingerprint
