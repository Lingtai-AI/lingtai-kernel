"""Executable production session-return matrix for the conversation-input suite.

Three layers, all *data that is itself under test* — no hand-written name union
and no lower-level helper standing in for the real factory:

1. **Registry matrix** (``REGISTRY_EDGES``) — every registered provider name
   (exactly the four families ``openai``, ``anthropic``, ``codex``,
   ``claude-code``) is built through the *real* ``LLMService`` end to end
   (``LLMService(provider=<exact name>, provider_defaults=...)`` -> ``__init__``
   -> ``_create_adapter`` -> registered factory -> ``create_session`` ->
   ``create_chat``), with the SDK client (and Codex token manager) mocked, and
   the returned adapter + session + ``_GatedSession`` **class** asserted. The
   union of the exact provider names built here equals the registry key set
   (``test_registry_matrix_covers_exactly_the_registered_providers``), so a new
   provider or a rebind to a different factory fails the matrix through the real
   route — not through expected prose. Every ``openai`` Responses row also pins
   the built session's ``_stateless_replay`` bit to ``True``: the Responses wire
   is ALWAYS stateless full-history replay, for the official endpoint and a
   compatible ``base_url`` alike. The wire consequence (full replay, no
   ``previous_response_id``) is proven through the same real ``LLMService``
   route in ``build_responses_mode_via_service`` / ``test_regime_inventory``.

2. **Wire-selector schema cross-product** (``WIRE_SCHEMA_ROWS``) — every
   registered family across ``wire_api`` values, plus every removed provider
   name. Each row carries an explicit ``schema_accepts`` (checked against the
   real ``init_schema.validate_init``) *separately* from the concrete
   adapter/session class the accepted configuration builds through the real
   ``LLMService`` path. Non-``auto`` ``wire_api`` is schema-valid only for
   ``openai``; every removed provider name is rejected with a pointer to
   ``openai``/``anthropic``.

3. **Behavior regimes** (``ALL_REGIMES``) — the small set of concrete
   ``ChatSession`` **classes/configurations** with distinct common-input wire
   behavior. Each builds a *real* session with a *mocked transport*; the
   parametrized tests drive the two inputs the kernel ``ChatSession`` ABC
   declares (``send(str)`` / ``send(list[ToolResultBlock])``), assert the exact
   provider wire, AND assert the returned ``LLMResponse`` + concrete
   ``UsageMetadata``. The reasoning-fallback-configured ``OpenAIChatSession``
   (``inject_reasoning_fallback=True``, the generic thinking-mode replay knob) is
   built with its real construction, Codex runs both inputs through its own REST
   machinery, the plain Responses row runs the production stateless mode, and
   ``_GatedSession`` is characterized wrapping a real concrete session.

This is a prerequisite characterization layer, not a governed component — see the
package ``__init__``. Source line numbers are deliberately omitted; the tests
assert the actual classes/behavior, so they cannot drift out of sync.
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock, patch

from lingtai.kernel.llm.base import LLMResponse, UsageMetadata
from lingtai.kernel.llm.interface import (
    ChatInterface,
    ToolCallBlock,
    ToolResultBlock,
)

# A compatible-endpoint base_url for the ``openai``/``anthropic`` families. No
# network is used — the SDK client is always mocked.
FAKE_BASE_URL = "https://compat.invalid/v1"


# ---------------------------------------------------------------------------
# Canonical tool-result fixture
# ---------------------------------------------------------------------------

# A stable canonical tool-result payload. The wire assertions key off ``id`` and
# ``content`` so the per-regime expected shapes below can be derived exactly.
TOOL_CALL_ID = "call_char_0001"
TOOL_NAME = "bash"
TOOL_CONTENT = {"stdout": "hello", "exit_code": 0}
TOOL_CONTENT_WIRE = json.dumps(TOOL_CONTENT, default=str)

USER_TEXT = "characterization text turn"


def canonical_tool_result() -> ToolResultBlock:
    """The canonical block the kernel hands to ``send(list[...])``.

    Constructed directly for a stable fixture; ``test_regime_inventory``'s
    ``test_canonical_fixture_matches_adapter_factory_shape`` separately proves a
    real adapter's ``make_tool_result_message`` produces the byte-identical block
    for this id, anchoring the fixture to production shape.
    """
    return ToolResultBlock(id=TOOL_CALL_ID, name=TOOL_NAME, content=dict(TOOL_CONTENT))


def seed_matching_tool_call(session) -> None:
    """Stage the ``assistant[tool_call]`` the tool result answers.

    ``send(list[ToolResultBlock])`` is a *continuation*: the canonical interface
    already holds the assistant tool-call these results close. Without it,
    ``enforce_tool_pairing`` strips the result as an orphan.
    """
    session.interface.add_assistant_message(
        content=[ToolCallBlock(id=TOOL_CALL_ID, name=TOOL_NAME, args={})]
    )


# ---------------------------------------------------------------------------
# Minimal parseable raw responses per transport
# ---------------------------------------------------------------------------


def _openai_chat_raw():
    """A minimal object shaped like an OpenAI ChatCompletion (text 'ok')."""
    msg = SimpleNamespace(content="ok", reasoning_content=None, tool_calls=[])
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
        model="test-model",
    )


def _responses_raw():
    """A minimal object shaped like a non-streaming Responses API result."""
    text_block = SimpleNamespace(type="output_text", text="ok")
    message = SimpleNamespace(type="message", content=[text_block])
    return SimpleNamespace(
        id="resp_char_1",
        output=[message],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


def _anthropic_raw():
    """A minimal object shaped like an anthropic Messages response (text 'ok')."""
    block = SimpleNamespace(type="text", text="ok")
    return SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            thinking_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        id="resp_char_1",
        model="claude-char-test",
        role="assistant",
        stop_reason="end_turn",
    )


# ---------------------------------------------------------------------------
# Expected response envelope (shared assertion target)
# ---------------------------------------------------------------------------


@dataclass
class ExpectedResponse:
    """The minimum ``LLMResponse`` shape a conforming regime must return.

    Every mocked transport feeds these exact token counts, so asserting them is a
    real drift check (a parser that silently zeroed usage would fail), not a
    tautology. ``text`` is per-regime because the text-less paths parse to ``""``.
    """

    text: str = "ok"
    input_tokens: int = 10
    output_tokens: int = 5
    thinking_tokens: int = 0
    cached_tokens: int = 0


# ---------------------------------------------------------------------------
# Behavior-regime descriptor
# ---------------------------------------------------------------------------


@dataclass
class Regime:
    """One production session return regime (a distinct common-input behavior).

    ``build`` returns ``(session, transport)`` where ``session`` is a *real*
    concrete ``ChatSession`` (or one wrapped in ``_GatedSession``) and
    ``transport`` is the mock the session calls. ``sent_wire`` reads the exact
    input the transport received for the most recent ``send``.
    ``expected_text_wire`` / ``expected_tool_result_wire`` are the provider shapes
    a conforming regime must produce; ``expected_response`` is the envelope the
    send must return. ``conforms`` False marks a documented current defect/dormant
    path (asserted for its actual behavior, never a MUST).
    """

    name: str
    build: Callable[[], tuple[Any, Any]]
    sent_wire: Callable[[Any], Any]
    expected_response: ExpectedResponse = field(default_factory=ExpectedResponse)
    conforms: bool = True
    expected_text_wire: Any = None
    expected_tool_result_wire: Any = None
    # For constructions whose wire transform only shows on the paired
    # continuation (the reasoning-fallback-configured session injects
    # reasoning_content; asserted via this extra check).
    extra_tool_result_assert: Callable[[Any], None] | None = None


# ---------------------------------------------------------------------------
# Per-regime builders (real session + mocked transport)
# ---------------------------------------------------------------------------


def _mock_openai_completions_client():
    client = MagicMock()
    client.chat.completions.create.return_value = _openai_chat_raw()
    return client


def _openai_completions_sent(client) -> list[dict]:
    """The full Chat Completions ``messages`` array of the last request."""
    return client.chat.completions.create.call_args.kwargs["messages"]


def _build_openai_chat() -> tuple[Any, Any]:
    from lingtai.llm.openai.adapter import OpenAIChatSession

    client = _mock_openai_completions_client()
    session = OpenAIChatSession(
        client=client,
        model="test-model",
        interface=ChatInterface(),
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )
    return session, client


def _build_reasoning_fallback_chat() -> tuple[Any, Any]:
    """``OpenAIChatSession`` with the generic ``inject_reasoning_fallback`` knob
    on — the thinking-mode replay configuration any ``openai`` endpoint gets."""
    from lingtai.llm.openai.adapter import OpenAIChatSession

    client = _mock_openai_completions_client()
    session = OpenAIChatSession(
        client=client,
        model="thinking-compat-model",
        interface=ChatInterface(),
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        client_kwargs={},
        inject_reasoning_fallback=True,
    )
    return session, client


def _assert_reasoning_content_injected(wire) -> None:
    """The reasoning-fallback-configured session injects ``reasoning_content`` on
    the assistant tool-call turn; the unconfigured base ``OpenAIChatSession``
    never does. Proves the configured construction coexists with the canonical
    tool-result rendering."""
    assistant_msgs = [
        m for m in wire if isinstance(m, dict) and m.get("role") == "assistant"
    ]
    assert assistant_msgs, f"no assistant message on wire: {wire!r}"
    assert any(m.get("reasoning_content") for m in assistant_msgs), (
        f"expected an injected non-empty reasoning_content on an assistant "
        f"message, got {assistant_msgs!r}"
    )


def _build_anthropic() -> tuple[Any, Any]:
    from lingtai.llm.anthropic.adapter import AnthropicChatSession

    client = MagicMock()
    client.messages.create.return_value = _anthropic_raw()
    session = AnthropicChatSession(
        client=client,
        model="claude-char-test",
        system_prompt="system",
        interface=ChatInterface(),
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )
    return session, client


def _anthropic_sent(client) -> list[dict]:
    """The full Anthropic ``messages`` array of the last request."""
    return client.messages.create.call_args.kwargs["messages"]


def _build_claude_code() -> tuple[Any, Any]:
    from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter, ClaudeCodeChatSession

    adapter = ClaudeCodeAdapter(model="claude-char")
    # ClaudeCodeChatSession renders the interface to one CLI prompt and calls
    # ``adapter._invoke(prompt, model)``. Mock only that seam; capture the prompt.
    calls: list[str] = []

    def _fake_invoke(prompt, model, **kwargs):
        calls.append(prompt)
        return (
            {"action": "final", "text": "ok"},
            UsageMetadata(input_tokens=10, output_tokens=5),
            {"raw": "ok"},
        )

    adapter._invoke = _fake_invoke  # type: ignore[assignment]
    session = ClaudeCodeChatSession(
        adapter=adapter,
        model="claude-char",
        system_prompt="system",
        interface=ChatInterface(),
        tools=None,
        context_window=0,
    )
    return session, SimpleNamespace(prompts=calls)


def _claude_code_sent(transport) -> str:
    """The rendered CLI prompt string of the last request."""
    return transport.prompts[-1]


def _build_openai_responses() -> tuple[Any, Any]:
    from lingtai.llm.openai.adapter import OpenAIResponsesSession

    client = MagicMock()
    client.responses.create.return_value = _responses_raw()
    # The production mode: ``OpenAIAdapter`` always builds stateless replay.
    session = OpenAIResponsesSession(
        client=client,
        model="gpt-char",
        instructions="system",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        stateless_replay=True,
    )
    return session, client


def _openai_responses_sent(client) -> list[dict]:
    """The Responses ``input`` array of the last request."""
    return client.responses.create.call_args.kwargs["input"]


# --- Codex: real REST machinery, mocked streaming transport ---------------
#
# ``CodexResponsesSession`` funnels BOTH ``send(str)`` and
# ``send(list[ToolResultBlock])`` through ``send_stream`` ->
# ``self._client.responses.create(stream=True)``; the fake ``create`` records the
# wire kwargs and yields streamed events. REST is the default transport, so no
# websocket is opened and no network is used. Pairing for the tool-result path is
# satisfied by the canonical-interface seed, so this stub just streams text; the
# paired wire still carries the real ``function_call_output``.


@dataclass
class _CodexEvent:
    type: str
    delta: str | None = None
    item: object | None = None
    response: object | None = None


def _codex_usage():
    return SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


class _CodexRestResponses:
    """``responses.create`` stub: streams a text delta 'ok' + a completed event
    carrying a concrete usage, recording the wire kwargs each turn."""

    def __init__(self):
        self.kwargs: list[dict] = []
        self._counter = 0

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        self._counter += 1
        rid = f"resp_char_{self._counter}"

        def _gen():
            yield _CodexEvent("response.output_text.delta", delta="ok")
            yield _CodexEvent(
                "response.completed",
                response=SimpleNamespace(id=rid, usage=_codex_usage()),
            )

        return _gen()


class _CodexRestClient:
    def __init__(self):
        self.responses = _CodexRestResponses()


def _build_codex() -> tuple[Any, Any]:
    from lingtai.llm.openai.adapter import CodexResponsesSession

    client = _CodexRestClient()
    session = CodexResponsesSession(
        client=client,
        model="gpt-char",
        instructions="system",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        session_id="sess-char",
        thread_id="sess-char",
        transport="rest",  # REST default; no websocket, no network
    )
    return session, client


def _codex_sent(client) -> list[dict]:
    """The Responses ``input`` array of the last Codex REST request."""
    return client.responses.kwargs[-1]["input"]


# --- Gated: a real concrete session wrapped in _GatedSession ---------------


class _SyncGate:
    """A synchronous stand-in for ``APICallGate``: runs the submitted call inline,
    so ``_GatedSession`` forwarding is deterministic with no threads and no
    teardown. ``_GatedSession`` calls exactly ``gate.submit(fn)``."""

    def __init__(self):
        self.calls = 0

    def submit(self, fn):
        self.calls += 1
        return fn()


def _build_gated_openai_chat() -> tuple[Any, Any]:
    """A real ``OpenAIChatSession`` wrapped in a real ``_GatedSession`` (sync
    gate). The proxy forwards ``send``/``send_stream`` through the gate to the
    inner session; the wire is captured on the inner session's mocked client."""
    from lingtai.llm.base import _GatedSession

    inner, client = _build_openai_chat()
    gated = _GatedSession(inner, _SyncGate())
    return gated, client


# ---------------------------------------------------------------------------
# The behavior-regime inventory
# ---------------------------------------------------------------------------

# Common wire shapes reused across the OpenAI-Chat family.
_OPENAI_TEXT_WIRE = {"role": "user", "content": USER_TEXT}
_OPENAI_TOOL_WIRE = {
    "role": "tool",
    "tool_call_id": TOOL_CALL_ID,
    "content": TOOL_CONTENT_WIRE,
}

CONFORMING_REGIMES: list[Regime] = [
    Regime(
        name="openai_chat",
        build=_build_openai_chat,
        sent_wire=_openai_completions_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        expected_tool_result_wire=_OPENAI_TOOL_WIRE,
    ),
    # Reasoning-fallback-configured OpenAI Chat: base tool wire PLUS injected
    # reasoning_content on the paired assistant tool-call turn (thinking-mode
    # replay for any ``openai`` endpoint).
    Regime(
        name="openai_chat_reasoning_fallback",
        build=_build_reasoning_fallback_chat,
        sent_wire=_openai_completions_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        expected_tool_result_wire=_OPENAI_TOOL_WIRE,
        extra_tool_result_assert=_assert_reasoning_content_injected,
    ),
    Regime(
        name="anthropic",
        build=_build_anthropic,
        sent_wire=_anthropic_sent,
        expected_response=ExpectedResponse(text="ok", output_tokens=2),
        # Anthropic groups tool_result blocks inside one user message.
        expected_tool_result_wire={
            "type": "tool_result",
            "tool_use_id": TOOL_CALL_ID,
            "content": TOOL_CONTENT_WIRE,
        },
    ),
    Regime(
        name="claude_code",
        build=_build_claude_code,
        sent_wire=_claude_code_sent,
        # ClaudeCode renders to a single CLI text prompt; the tool result is a
        # ``TOOL_RESULT [name]: content`` line.
        expected_tool_result_wire=f"TOOL_RESULT [{TOOL_NAME}]: {TOOL_CONTENT_WIRE}",
    ),
    # openai_responses is the OpenAIResponsesSession class in its production
    # stateless full-replay mode (the only mode ``OpenAIAdapter`` builds). The
    # production selection through the real LLMService is proven separately
    # (Layer 1 mode bit + build_responses_mode_via_service wire proof).
    Regime(
        name="openai_responses",
        build=_build_openai_responses,
        sent_wire=_openai_responses_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        # After the fix, the canonical block converts to function_call_output.
        expected_tool_result_wire={
            "type": "function_call_output",
            "call_id": TOOL_CALL_ID,
            "output": TOOL_CONTENT_WIRE,
        },
    ),
    # Codex serializes the canonical block to function_call_output through its own
    # REST machinery (distinct from the plain Responses path).
    Regime(
        name="codex_responses",
        build=_build_codex,
        sent_wire=_codex_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        expected_tool_result_wire={
            "type": "function_call_output",
            "call_id": TOOL_CALL_ID,
            "output": TOOL_CONTENT_WIRE,
        },
    ),
    # _GatedSession forwards both inputs to the inner OpenAIChatSession, so the
    # wire is identical to the ungated regime.
    Regime(
        name="gated_openai_chat",
        build=_build_gated_openai_chat,
        sent_wire=_openai_completions_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        expected_tool_result_wire=_OPENAI_TOOL_WIRE,
    ),
]


ALL_REGIMES: list[Regime] = list(CONFORMING_REGIMES)

# All regimes build a real session (no build=None escape); split by whether their
# common-input surface conforms.
CONFORMING_BUILDABLE: list[Regime] = [r for r in ALL_REGIMES if r.conforms]


# ===========================================================================
# Layer 1 — Registry matrix: every provider built through the REAL LLMService
# ===========================================================================


@dataclass
class RegistryEdge:
    """A registered provider built via the real ``LLMService`` registry factory.

    ``defaults`` is the provider-defaults dict the factory reads (``max_rpm`` /
    ``wire_api`` live here). ``base_url`` is passed to ``LLMService``
    separately, exactly like the agent-boot path (the manifest ``base_url`` is
    not a provider-default pass-through); ``None`` selects the family's official
    endpoint. ``json_schema`` is forwarded to ``create_session``.
    ``adapter_class`` / ``session_class`` are the expected concrete classes;
    ``gated`` is whether ``create_chat`` returns a ``_GatedSession`` proxy.
    ``expected_stateless_replay`` pins the Responses mode bit on the returned
    ``OpenAIResponsesSession`` (always ``True`` for ``openai``: stateless full
    replay, never a ``previous_response_id`` chain). ``None`` means the row does
    not carry a Responses mode (any other session class), so the bit is not
    asserted.
    """

    provider: str
    adapter_class: str
    session_class: str
    gated: bool = False
    defaults: dict[str, Any] = field(default_factory=dict)
    base_url: str | None = FAKE_BASE_URL
    json_schema: dict[str, Any] | None = None
    label: str = ""
    expected_stateless_replay: bool | None = None

    def id(self) -> str:
        return self.label or self.provider


# Every registered provider name has at least one row here. Mode rows are added
# where a configuration changes the returned session (OpenAI chat/responses on
# the official and a compatible endpoint, the legacy ``auto`` selector, and the
# normal max_rpm gate).
REGISTRY_EDGES: list[RegistryEdge] = [
    # --- OpenAI: chat (default) vs responses (wire_api) ------------------
    RegistryEdge("openai", "OpenAIAdapter", "OpenAIChatSession", label="openai.chat"),
    RegistryEdge("openai", "OpenAIAdapter", "OpenAIChatSession",
                 base_url=None, label="openai.official.chat"),
    RegistryEdge("openai", "OpenAIAdapter", "OpenAIChatSession",
                 defaults={"wire_api": "auto"}, label="openai.legacy_auto.chat"),
    # The Responses wire is ALWAYS stateless full replay — for a compatible
    # endpoint and for the official endpoint alike. The mode bit is asserted,
    # so either row fails if the factory ever builds a stateful session.
    RegistryEdge(
        "openai", "OpenAIAdapter", "OpenAIResponsesSession",
        defaults={"wire_api": "responses"}, label="openai.responses",
        expected_stateless_replay=True,
    ),
    RegistryEdge(
        "openai", "OpenAIAdapter", "OpenAIResponsesSession",
        defaults={"wire_api": "responses"}, base_url=None,
        label="openai.official.responses", expected_stateless_replay=True,
    ),
    # --- Anthropic: bare vs gated (normal max_rpm composition) ----------
    RegistryEdge("anthropic", "AnthropicAdapter", "AnthropicChatSession",
                 label="anthropic.bare"),
    RegistryEdge("anthropic", "AnthropicAdapter", "AnthropicChatSession",
                 base_url=None, label="anthropic.official"),
    RegistryEdge(
        "anthropic", "AnthropicAdapter", "AnthropicChatSession",
        gated=True, defaults={"max_rpm": 60}, label="anthropic.gated",
    ),
    # --- Claude Code (the one registered spelling) ----------------------
    RegistryEdge("claude-code", "ClaudeCodeAdapter", "ClaudeCodeChatSession",
                 base_url=None),
    # --- Codex (single registered spelling) — token manager mocked -------
    RegistryEdge("codex", "CodexOpenAIAdapter", "CodexResponsesSession"),
]

# Providers whose factory constructs a real CodexTokenManager (reads a local
# token file / may refresh over the network). Mock it so the matrix asserts
# routing without touching auth.
_CODEX_PROVIDERS = frozenset({"codex"})


@contextlib.contextmanager
def _mocked_sdk_clients(provider: str):
    """Patch every provider SDK client (and, for Codex, the token manager) to
    no-op mocks so building through the real ``LLMService`` uses no network or
    credentials. Only the adapter/session *classes* are under test."""
    stack = contextlib.ExitStack()
    stack.enter_context(patch("openai.OpenAI", MagicMock()))
    stack.enter_context(patch("anthropic.Anthropic", MagicMock()))
    if provider in _CODEX_PROVIDERS:
        stack.enter_context(
            patch("lingtai.auth.codex.CodexTokenManager", MagicMock())
        )
    with stack:
        yield


def build_registry_edge(edge: RegistryEdge):
    """Build a registered provider through a REAL ``LLMService`` end to end
    (``__init__`` -> ``_create_adapter`` -> registered factory -> ``create_session``
    -> ``create_chat``), with the SDK client mocked. This exercises the exact
    production seam that reads provider defaults and applies ``_wrap_with_gate``.
    Returns ``(adapter, session)`` where ``session`` may be a ``_GatedSession``.
    """
    from lingtai.llm._register import register_all_adapters
    from lingtai.llm.service import LLMService

    register_all_adapters()
    with _mocked_sdk_clients(edge.provider):
        service = LLMService(
            provider=edge.provider,
            model="char-model",
            api_key="k",
            base_url=edge.base_url,
            provider_defaults={edge.provider: dict(edge.defaults)} if edge.defaults else None,
        )
        session = service.create_session(
            "system", tools=None, tracked=False, json_schema=edge.json_schema
        )
    adapter = service.get_adapter(edge.provider, edge.base_url)
    return adapter, session


@contextlib.contextmanager
def shutdown_gate(adapter):
    """Shut down the real ``APICallGate`` daemon thread a gated edge spins up."""
    try:
        yield
    finally:
        gate = getattr(adapter, "_gate", None)
        if gate is not None and hasattr(gate, "shutdown"):
            gate.shutdown()


def registered_provider_names() -> set[str]:
    """The exact provider names registered at import time (source of truth)."""
    from lingtai.llm._register import register_all_adapters
    from lingtai.llm.service import LLMService

    register_all_adapters()  # idempotent — re-binds the same names.
    return set(LLMService._adapter_registry)  # type: ignore[attr-defined]


def registry_edge_provider_names() -> set[str]:
    """The exact provider names the registry matrix builds."""
    return {e.provider for e in REGISTRY_EDGES}


# ===========================================================================
# Layer 2 — Wire-selector schema cross-product (schema-selectable vs factory)
# ===========================================================================

#: The four registered LLM provider families.
LLM_FAMILIES = ("openai", "anthropic", "codex", "claude-code")

WIRE_APIS = (None, "auto", "chat_completions", "responses")


def schema_accepts(provider: str, wire_api: str | None) -> bool:
    """Whether ``init_schema.validate_init`` accepts this manifest, computed from
    the exact source rule (init_schema.py): a non-``auto`` ``wire_api`` is scoped
    to ``provider == "openai"``; ``auto``/absent is accepted for every family;
    every removed provider name is rejected outright.
    ``test_wire_schema_selectability_matches_validate_init`` proves this
    predicate against the real validator for every row.
    """
    from lingtai.init_schema import REMOVED_LLM_PROVIDERS

    if provider in REMOVED_LLM_PROVIDERS:
        return False
    if wire_api in (None, "auto"):
        return True
    return provider == "openai"


@dataclass
class SchemaRow:
    """One ``(provider, wire_api)`` configuration.

    Selectability (``schema_accepts``) is checked against the real
    ``validate_init`` separately from the factory result
    (``adapter_class`` / ``session_class``), which is asserted only for accepted
    ``openai`` rows (the wire selector's one owner).
    """

    provider: str
    wire_api: str | None
    adapter_class: str = ""
    session_class: str = ""

    def label(self) -> str:
        return f"{self.provider}:wire_api={self.wire_api}"


def _wire_schema_rows() -> list[SchemaRow]:
    from lingtai.init_schema import REMOVED_LLM_PROVIDERS

    rows: list[SchemaRow] = []
    for provider in LLM_FAMILIES:
        for wire in WIRE_APIS:
            if provider == "openai":
                rows.append(SchemaRow(
                    provider, wire, "OpenAIAdapter",
                    "OpenAIResponsesSession" if wire == "responses" else "OpenAIChatSession",
                ))
            else:
                rows.append(SchemaRow(provider, wire))
    for provider in sorted(REMOVED_LLM_PROVIDERS):
        rows.append(SchemaRow(provider, None))
    return rows


WIRE_SCHEMA_ROWS: list[SchemaRow] = _wire_schema_rows()


def schema_manifest(row: SchemaRow) -> dict:
    """A minimal ``validate_init``-shaped manifest for this row."""
    llm: dict[str, Any] = {"provider": row.provider, "model": "m"}
    if row.wire_api is not None:
        llm["wire_api"] = row.wire_api
    if row.provider in {"openai", "anthropic"}:
        llm["base_url"] = FAKE_BASE_URL
    return {"manifest": {"llm": llm}, "covenant": "", "pad": ""}


def build_schema_row(row: SchemaRow):
    """Build a schema-accepted ``openai`` row through the REAL path:
    ``build_provider_defaults_from_manifest_llm`` -> ``LLMService(provider=...)``
    -> ``create_session``. Returns ``(adapter, session)``.
    """
    from lingtai.llm.service import LLMService, build_provider_defaults_from_manifest_llm

    manifest_llm = schema_manifest(row)["manifest"]["llm"]
    defaults = build_provider_defaults_from_manifest_llm(dict(manifest_llm), max_rpm=0)
    with _mocked_sdk_clients(row.provider):
        service = LLMService(
            provider=row.provider,
            model="char-model",
            api_key="k",
            base_url=FAKE_BASE_URL,
            provider_defaults=defaults,
        )
        session = service.create_session("system", tools=None, tracked=False)
    adapter = service.get_adapter(row.provider, FAKE_BASE_URL)
    return adapter, session


# ===========================================================================
# Layer 2b — Responses stateless WIRE proof through the real LLMService
# ===========================================================================
#
# The registry matrix asserts the mode BIT (``_stateless_replay``) on the
# session the real ``LLMService`` returns. This layer proves the mode's actual
# WIRE consequence on a two-turn continuation, still built through the real
# service route (``LLMService`` -> registered factory -> ``create_chat``), with a
# controllable Responses transport injected in place of the OpenAI SDK client:
# for the official endpoint and a compatible ``base_url`` alike, turn 2 replays
# the FULL canonical history and sends NO ``previous_response_id``.
#
# This does not duplicate ``tests/test_openai_responses_stateless.py`` (which
# exhaustively characterizes the stateless session in isolation); it pins the one
# thing the ledger must own — that the *production selection* through
# ``LLMService`` yields this wire.


class _ResponsesTransport:
    """A minimal ``client.responses`` stub: records each ``create`` kwargs and
    returns a parseable non-streaming Responses result with a stable id."""

    def __init__(self):
        self.kwargs: list[dict] = []
        self._counter = 0

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        self._counter += 1
        raw = _responses_raw()
        raw.id = f"resp_char_{self._counter}"
        return raw


def build_responses_mode_via_service(edge: "RegistryEdge"):
    """Build a Responses ``RegistryEdge`` through the REAL ``LLMService`` with a
    controllable Responses transport, so the returned session's send() wire can be
    inspected across turns. Returns ``(session, transport)``.

    The SDK client is patched to a stub whose ``.responses`` is a
    ``_ResponsesTransport``; every other seam is the real production path (the
    registered factory reads provider defaults and builds the session).
    """
    from lingtai.llm._register import register_all_adapters
    from lingtai.llm.service import LLMService

    register_all_adapters()
    transport = _ResponsesTransport()
    fake_client = SimpleNamespace(responses=transport)
    with patch("openai.OpenAI", MagicMock(return_value=fake_client)):
        service = LLMService(
            provider=edge.provider,
            model="char-model",
            api_key="k",
            base_url=edge.base_url,
            provider_defaults={edge.provider: dict(edge.defaults)} if edge.defaults else None,
        )
        session = service.create_session("system", tools=None, tracked=False)
    return session, transport


# ---------------------------------------------------------------------------
# Shared response-envelope assertion
# ---------------------------------------------------------------------------


def assert_response_envelope(response: Any, expected: ExpectedResponse, label: str) -> None:
    """Assert a real ``LLMResponse`` with the expected text and concrete usage.

    Every mocked transport feeds fixed token counts, so these are real drift
    checks: a parser that dropped usage to a default or returned a mock instead of
    an ``LLMResponse`` would fail here.
    """
    assert isinstance(response, LLMResponse), (
        f"{label}: expected LLMResponse, got {type(response)!r}"
    )
    assert response.text == expected.text, (
        f"{label}: expected text {expected.text!r}, got {response.text!r}"
    )
    usage = response.usage
    assert isinstance(usage, UsageMetadata), (
        f"{label}: expected UsageMetadata usage, got {type(usage)!r}"
    )
    for field_name, want in (
        ("input_tokens", expected.input_tokens),
        ("output_tokens", expected.output_tokens),
        ("thinking_tokens", expected.thinking_tokens),
        ("cached_tokens", expected.cached_tokens),
    ):
        got = getattr(usage, field_name)
        assert isinstance(got, int), f"{label}: usage.{field_name} not int: {got!r}"
        assert got == want, f"{label}: usage.{field_name} expected {want}, got {got}"
