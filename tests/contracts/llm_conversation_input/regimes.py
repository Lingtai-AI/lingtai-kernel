"""Executable production session-return matrix for the conversation-input suite.

Three layers, all *data that is itself under test* — no hand-written name union
and no lower-level helper standing in for the real factory:

1. **Registry matrix** (``REGISTRY_EDGES``) — every registered provider name is
   built through the *real* ``LLMService`` end to end
   (``LLMService(provider=<exact name>, provider_defaults=...)`` -> ``__init__``
   -> ``_create_adapter`` -> registered factory -> ``create_session`` ->
   ``create_chat``), with the SDK client (and Codex token manager) mocked, and
   the returned adapter + session + ``_GatedSession`` **class** asserted. The
   union of the exact provider names built here equals the registry key set
   (``test_registry_matrix_covers_exactly_the_registered_providers``), so a new
   provider or a rebind to a different factory fails the matrix through the real
   route — not through expected prose. Where a row also carries a Responses
   **mode** (``expected_stateless_replay``), the built session's
   ``_stateless_replay`` bit is asserted too, so fresh-main #861's two Responses
   regimes — official/stateful ``openai.responses`` and custom/OpenAI-compatible
   stateless ``custom.responses.stateless`` — are distinct class-**plus**-mode
   rows, not one class-only row. Their divergent wires (delta +
   ``previous_response_id`` vs full replay + no resume id) are proven through the
   same real ``LLMService`` route in ``build_responses_mode_via_service`` /
   ``test_regime_inventory``.

2. **Custom-family schema cross-product** (``CUSTOM_SCHEMA_EDGES``) — the
   ``custom`` provider and the aliases ``grok`` / ``qwen`` / ``kimi`` (which
   share one ``_custom`` factory) across ``api_compat`` x ``wire_api``. Each row
   carries an explicit ``schema_accepts`` (checked against the real
   ``init_schema.validate_init``) *separately* from the concrete adapter/session
   class the accepted configuration builds through the real ``LLMService`` path
   (or the exact factory ``ValueError`` for a missing base URL). This makes the
   difference between *selectable* configurations and lower-level factory states
   explicit: non-``auto`` ``wire_api`` is schema-valid only for ``openai`` and
   ``custom`` + ``api_compat=openai``; the alias non-``auto`` rows are rejected.

3. **Behavior regimes** (``ALL_REGIMES``) — the small set of concrete
   ``ChatSession`` **classes** with distinct common-input wire behavior. Each
   builds a *real* session with a *mocked transport*; the parametrized tests drive
   the two inputs the kernel ``ChatSession`` ABC declares (``send(str)`` /
   ``send(list[ToolResultBlock])``), assert the exact provider wire, AND assert
   the returned ``LLMResponse`` + concrete ``UsageMetadata``. The subclasses that
   override ``_build_messages`` (DeepSeek / MiMo / Zhipu) are built as their real
   classes, Codex runs both inputs through its own REST machinery, and
   ``_GatedSession`` is characterized wrapping a real concrete session. This layer
   is keyed by session *class* on a single common-input turn; the stateful vs
   stateless **mode** split within ``OpenAIResponsesSession`` — which shares one
   class — is NOT a distinct row here. That #861 boundary is owned by Layer 1's
   ``expected_stateless_replay`` assertion and the two-turn wire proofs in
   ``build_responses_mode_via_service`` (both through the real ``LLMService``); the
   ``openai_responses`` behavior row below therefore exercises the class's default
   (stateful) common-input rendering only, and does not claim to cover both modes.

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

# A base_url the OpenAI/Anthropic-compat factory paths require (they raise
# without one). No network is used — the SDK client is always mocked.
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
    ``enforce_tool_pairing`` strips the result as an orphan. Server-state regimes
    that do not use the canonical interface for the wire (OpenAI Responses, Gemini
    Interactions, Gemini Chat) ignore this seed harmlessly.
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


def _gemini_interactions_raw():
    """A minimal Interactions result: one text step + concrete usage."""
    step = SimpleNamespace(
        type="model_output",
        content=[SimpleNamespace(type="text", text="ok")],
    )
    return SimpleNamespace(
        id="int_char_1",
        steps=[step],
        usage=SimpleNamespace(
            total_input_tokens=10,
            total_output_tokens=5,
            total_thought_tokens=0,
            total_cached_tokens=0,
        ),
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
    # For subclasses whose wire transform only shows on the paired continuation
    # (DeepSeek/MiMo inject reasoning_content; asserted via this extra check).
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


def _build_openai_compatible_subclass(session_class) -> tuple[Any, Any]:
    """Build one of the OpenAI-Chat subclasses (DeepSeek/MiMo/Zhipu) as its real
    class with a mocked Chat Completions client."""
    client = _mock_openai_completions_client()
    session = session_class(
        client=client,
        model="subclass-char",
        interface=ChatInterface(),
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )
    return session, client


def _build_deepseek_chat() -> tuple[Any, Any]:
    from lingtai.llm.deepseek.adapter import DeepSeekChatSession

    return _build_openai_compatible_subclass(DeepSeekChatSession)


def _build_mimo_chat() -> tuple[Any, Any]:
    from lingtai.llm.mimo.adapter import MimoChatSession

    return _build_openai_compatible_subclass(MimoChatSession)


def _build_zhipu_chat() -> tuple[Any, Any]:
    from lingtai.llm.zhipu.adapter import ZhipuChatSession

    return _build_openai_compatible_subclass(ZhipuChatSession)


def _assert_reasoning_content_injected(wire) -> None:
    """DeepSeek/MiMo inject ``reasoning_content`` on the assistant tool-call turn;
    the base ``OpenAIChatSession`` never does. Proves the subclass wire transform
    coexists with the canonical tool-result rendering."""
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

    def _fake_invoke(prompt, model):
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


def _build_gemini_interactions() -> tuple[Any, Any]:
    from lingtai.llm.gemini.adapter import InteractionsChatSession

    client = MagicMock()
    client.interactions.create.return_value = _gemini_interactions_raw()
    session = InteractionsChatSession(
        client=client,
        model="gemini-char",
        config_kwargs={},
        interface=ChatInterface(),
    )
    return session, client


def _gemini_interactions_sent(client) -> list[dict]:
    """The Interactions ``input`` array of the last request."""
    return client.interactions.create.call_args.kwargs["input"]


def _build_openai_responses() -> tuple[Any, Any]:
    from lingtai.llm.openai.adapter import OpenAIResponsesSession

    client = MagicMock()
    client.responses.create.return_value = _responses_raw()
    session = OpenAIResponsesSession(
        client=client,
        model="gpt-char",
        instructions="system",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
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
    # DeepSeek/MiMo: base OpenAI-Chat tool wire PLUS injected reasoning_content on
    # the paired assistant tool-call turn (thinking-mode replay).
    Regime(
        name="deepseek_chat",
        build=_build_deepseek_chat,
        sent_wire=_openai_completions_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        expected_tool_result_wire=_OPENAI_TOOL_WIRE,
        extra_tool_result_assert=_assert_reasoning_content_injected,
    ),
    Regime(
        name="mimo_chat",
        build=_build_mimo_chat,
        sent_wire=_openai_completions_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        expected_tool_result_wire=_OPENAI_TOOL_WIRE,
        extra_tool_result_assert=_assert_reasoning_content_injected,
    ),
    # Zhipu: merges consecutive same-role messages (asserted directly in
    # test_send_tool_results.test_zhipu_merges_consecutive_same_role_messages).
    Regime(
        name="zhipu_chat",
        build=_build_zhipu_chat,
        sent_wire=_openai_completions_sent,
        expected_text_wire=_OPENAI_TEXT_WIRE,
        expected_tool_result_wire=_OPENAI_TOOL_WIRE,
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
    Regime(
        name="gemini_interactions",
        build=_build_gemini_interactions,
        sent_wire=_gemini_interactions_sent,
        expected_tool_result_wire={
            "type": "function_result",
            "call_id": TOOL_CALL_ID,
            "result": TOOL_CONTENT_WIRE,
            "name": TOOL_NAME,
        },
    ),
    # openai_responses is the OpenAIResponsesSession class on its DEFAULT
    # (stateful, stateless_replay=False) common-input rendering. The custom
    # stateless mode of the SAME class is proven separately through the real
    # LLMService (Layer 1 mode bit + build_responses_mode_via_service wire proof),
    # so this single row is not claimed to cover both #861 modes.
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


# Non-conforming / dormant regime — asserted for *actual* current behavior, never
# listed as satisfying a MUST.


def _build_gemini_chat() -> tuple[Any, Any]:
    """The json_schema-only Chat-API path (documented dormant defect).

    ``GeminiChatSession.send`` forwards ``message`` straight to the genai SDK's
    ``chat.send_message`` with no conversion. Mock that chat object and capture
    the raw argument to characterize what the SDK actually receives.
    """
    from lingtai.llm.gemini.adapter import GeminiChatSession

    chat = MagicMock()
    chat.send_message.return_value = SimpleNamespace(
        text="ok",
        candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            cached_content_token_count=0,
            thoughts_token_count=0,
        ),
        function_calls=[],
    )
    session = GeminiChatSession(chat=chat, interface=ChatInterface())
    return session, chat


def _gemini_chat_sent(chat) -> Any:
    """The raw argument handed to genai ``chat.send_message`` last."""
    return chat.send_message.call_args.args[0]


# GeminiChatSession.send forwards the list to genai chat.send_message unconverted
# — it cannot serialize a canonical ToolResultBlock. Reachable only via
# create_chat(json_schema=...) (proven in the registry matrix); no current
# production create_session caller sets json_schema, so it is dormant. The fix (a
# native genai.types.Part converter) is a named follow-up out of this slice.
GEMINI_CHAT_REGIME = Regime(
    name="gemini_chat",
    build=_build_gemini_chat,
    sent_wire=_gemini_chat_sent,
    conforms=False,
)


ALL_REGIMES: list[Regime] = CONFORMING_REGIMES + [GEMINI_CHAT_REGIME]

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
    ``api_compat`` / ``wire_api`` live here). ``base_url`` is passed to
    ``LLMService`` separately, exactly like the agent-boot path (the manifest
    ``base_url`` is not a provider-default pass-through). ``json_schema`` is
    forwarded to ``create_session`` (selects the Gemini Chat path).
    ``adapter_class`` / ``session_class`` are the expected concrete classes;
    ``gated`` is whether ``create_chat`` returns a ``_GatedSession`` proxy.
    ``expected_stateless_replay`` pins the Responses mode bit on the returned
    ``OpenAIResponsesSession`` (``False`` = official/stateful ``previous_response_id``
    chain; ``True`` = custom/OpenAI-compatible stateless full replay). ``None``
    means the row does not carry a Responses mode (any other session class), so the
    bit is not asserted. This is the #861 stateful/stateless boundary made an
    executable class-plus-mode assertion, not a class-only row.
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
# where a configuration changes the returned session (OpenAI chat/responses,
# Gemini interactions/chat, MiniMax default gate, normal max_rpm gate). Gemini
# ignores base_url, so those rows omit it (api_key comes from the resolver).
REGISTRY_EDGES: list[RegistryEdge] = [
    # --- OpenAI: chat (default) vs responses (wire_api) ------------------
    # openai + wire_api=responses is OFFICIAL/STATEFUL: previous_response_id
    # chain, stateless_replay=False. The mode bit is asserted, so this row fails
    # if the official factory ever flips to stateless replay.
    RegistryEdge("openai", "OpenAIAdapter", "OpenAIChatSession", label="openai.chat"),
    RegistryEdge(
        "openai", "OpenAIAdapter", "OpenAIResponsesSession",
        defaults={"wire_api": "responses"}, label="openai.responses",
        expected_stateless_replay=False,
    ),
    # --- OpenAI-compatible single-session providers ---------------------
    RegistryEdge("openrouter", "OpenRouterAdapter", "OpenAIChatSession"),
    RegistryEdge("deepseek", "DeepSeekAdapter", "DeepSeekChatSession"),
    # MiMo defaults to the native Responses wire; Chat Completions remains an explicit escape hatch.
    RegistryEdge("mimo", "MimoAdapter", "MimoResponsesSession"),
    RegistryEdge("glm", "ZhipuAdapter", "ZhipuChatSession"),
    RegistryEdge("zhipu", "ZhipuAdapter", "ZhipuChatSession"),
    # --- Custom family (each shares the _custom factory) ----------------
    RegistryEdge("custom", "OpenAIAdapter", "OpenAIChatSession",
                 defaults={"api_compat": "openai"}),
    # custom + api_compat=openai + wire_api=responses is the #861 CUSTOM/STATELESS
    # regime: same OpenAIResponsesSession class as openai.responses, but the
    # _custom factory routes through create_custom_adapter, which sets
    # responses_stateless_replay=True — so the returned session replays full
    # canonical history and sends no previous_response_id. Asserting the mode bit
    # (not just the class) is what distinguishes it from the official/stateful row;
    # a class-only assertion stays green if the factory drops stateless_replay.
    RegistryEdge("custom", "OpenAIAdapter", "OpenAIResponsesSession",
                 defaults={"api_compat": "openai", "wire_api": "responses"},
                 label="custom.responses.stateless",
                 expected_stateless_replay=True),
    RegistryEdge("grok", "OpenAIAdapter", "OpenAIChatSession",
                 defaults={"api_compat": "openai"}),
    RegistryEdge("qwen", "OpenAIAdapter", "OpenAIChatSession",
                 defaults={"api_compat": "openai"}),
    RegistryEdge("kimi", "OpenAIAdapter", "OpenAIChatSession",
                 defaults={"api_compat": "openai"}),
    # --- Anthropic: bare vs gated (normal max_rpm composition) ----------
    RegistryEdge("anthropic", "AnthropicAdapter", "AnthropicChatSession",
                 label="anthropic.bare"),
    RegistryEdge(
        "anthropic", "AnthropicAdapter", "AnthropicChatSession",
        gated=True, defaults={"max_rpm": 60}, label="anthropic.gated",
    ),
    # --- MiniMax: gated by its own default max_rpm=120, no host override -
    RegistryEdge("minimax", "MiniMaxAdapter", "AnthropicChatSession",
                 gated=True, defaults={}, base_url=None),
    # --- Gemini: interactions (default) vs chat (json_schema) -----------
    RegistryEdge("gemini", "GeminiAdapter", "InteractionsChatSession",
                 base_url=None, label="gemini.interactions"),
    RegistryEdge(
        "gemini", "GeminiAdapter", "GeminiChatSession", base_url=None,
        json_schema={"title": "t", "type": "object"}, label="gemini.chat",
    ),
    # --- Claude Code (both registered spellings) ------------------------
    RegistryEdge("claude-code", "ClaudeCodeAdapter", "ClaudeCodeChatSession",
                 base_url=None),
    RegistryEdge("claude_code", "ClaudeCodeAdapter", "ClaudeCodeChatSession",
                 base_url=None),
    # --- Codex (all three registered spellings) — token manager mocked --
    RegistryEdge("codex", "CodexOpenAIAdapter", "CodexResponsesSession"),
    RegistryEdge("codex-pool", "CodexOpenAIAdapter", "CodexResponsesSession"),
    RegistryEdge("codex_pool", "CodexOpenAIAdapter", "CodexResponsesSession"),
]

# Providers whose factory constructs a real CodexTokenManager (reads a local
# token file / may refresh over the network). Mock it so the matrix asserts
# routing without touching auth.
_CODEX_PROVIDERS = frozenset({"codex", "codex-pool", "codex_pool"})


@contextlib.contextmanager
def _mocked_sdk_clients(provider: str):
    """Patch every provider SDK client (and, for Codex, the token manager) to
    no-op mocks so building through the real ``LLMService`` uses no network or
    credentials. Only the adapter/session *classes* are under test."""
    stack = contextlib.ExitStack()
    stack.enter_context(patch("openai.OpenAI", MagicMock()))
    stack.enter_context(patch("anthropic.Anthropic", MagicMock()))
    stack.enter_context(_patched_genai_client())
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
# Layer 2 — Custom-family schema cross-product (schema-selectable vs factory)
# ===========================================================================

# ``custom`` plus the aliases ``grok`` / ``qwen`` / ``kimi`` share one ``_custom``
# factory, so the same edges apply to all four names identically.
CUSTOM_FAMILY = ("custom", "grok", "qwen", "kimi")


def schema_accepts(provider: str, api_compat: str | None, wire_api: str | None) -> bool:
    """Whether ``init_schema.validate_init`` accepts this manifest, computed from
    the exact source rule (init_schema.py): a non-``auto`` ``wire_api`` is scoped
    to ``provider == "openai"`` or ``provider == "custom" and api_compat == "openai"``;
    ``auto``/absent ``wire_api`` is accepted for every provider/compat. The rule is
    *per provider* — so a non-``auto`` openai row is selectable for ``custom`` but
    NOT for the aliases ``grok``/``qwen``/``kimi`` (they are never ``provider ==
    openai``). ``test_custom_schema_selectability_matches_validate_init`` proves
    this predicate against the real validator for every family name.
    """
    if wire_api in (None, "auto"):
        return True
    effective_compat = (api_compat or "openai")
    return provider == "openai" or (provider == "custom" and effective_compat == "openai")


@dataclass
class SchemaEdge:
    """One custom-family ``(api_compat, wire_api)`` configuration.

    The *factory result* (``adapter_class`` / ``session_class``) is identical
    across the whole custom family (they share one ``_custom`` factory). The
    *selectability* boundary is per-provider, so it is NOT stored here — it is
    computed by ``schema_accepts(provider, ...)`` and checked against the real
    ``validate_init`` separately from the factory result, so the two are never
    conflated. ``factory_raises`` marks a configuration the factory refuses (e.g.
    a missing base URL) even though the schema accepts it. ``json_schema`` selects
    the Gemini Chat path.
    """

    api_compat: str | None
    wire_api: str | None
    adapter_class: str = ""
    session_class: str = ""
    factory_raises: bool = False
    needs_base_url: bool = True
    json_schema: dict[str, Any] | None = None

    def label(self) -> str:
        return (
            f"api_compat={self.api_compat} wire_api={self.wire_api}"
            + (" +json_schema" if self.json_schema else "")
            + (" [no_base_url]" if self.factory_raises else "")
        )


def _custom_schema_edges() -> list[SchemaEdge]:
    """Generate the custom-family schema cross-product from the source factory rule.

    Factory rule (create_custom_adapter): openai/anthropic require base_url; gemini
    ignores it; only ``api_compat=openai`` + ``wire_api=responses`` yields the
    Responses session — everything else on openai yields Chat; an unknown
    ``api_compat`` falls into the OpenAI branch.
    """
    WIRE_APIS = (None, "auto", "chat_completions", "responses")
    edges: list[SchemaEdge] = []
    for compat in ("openai", "anthropic", "gemini", "grpc"):  # grpc = unknown fallback
        for wire in WIRE_APIS:
            if compat == "openai":
                adapter_cls, session_cls, needs_url = (
                    "OpenAIAdapter",
                    "OpenAIResponsesSession" if wire == "responses" else "OpenAIChatSession",
                    True,
                )
            elif compat == "anthropic":
                adapter_cls, session_cls, needs_url = (
                    "AnthropicAdapter", "AnthropicChatSession", True,
                )
            elif compat == "gemini":
                adapter_cls, session_cls, needs_url = (
                    "GeminiAdapter", "InteractionsChatSession", False,
                )
            else:  # grpc / unknown -> OpenAI fallback branch
                adapter_cls, session_cls, needs_url = (
                    "OpenAIAdapter", "OpenAIChatSession", True,
                )
            edges.append(
                SchemaEdge(
                    api_compat=compat,
                    wire_api=wire,
                    adapter_class=adapter_cls,
                    session_class=session_cls,
                    needs_base_url=needs_url,
                )
            )
    # Gemini Chat via json_schema (the dormant path made factory-reachable).
    edges.append(
        SchemaEdge(
            api_compat="gemini", wire_api=None,
            adapter_class="GeminiAdapter", session_class="GeminiChatSession",
            needs_base_url=False, json_schema={"title": "t", "type": "object"},
        )
    )
    # Factory error edges: openai/anthropic with NO base_url raise (the schema is
    # fine about a missing base_url — it is the factory that refuses).
    edges.append(SchemaEdge(api_compat="openai", wire_api=None,
                            factory_raises=True, needs_base_url=False))
    edges.append(SchemaEdge(api_compat="anthropic", wire_api=None,
                            factory_raises=True, needs_base_url=False))
    return edges


CUSTOM_SCHEMA_EDGES: list[SchemaEdge] = _custom_schema_edges()


def custom_manifest(provider: str, edge: SchemaEdge) -> dict:
    """A minimal ``validate_init``-shaped manifest for this custom-family edge."""
    llm: dict[str, Any] = {"provider": provider, "model": "m"}
    if edge.api_compat is not None:
        llm["api_compat"] = edge.api_compat
    if edge.wire_api is not None:
        llm["wire_api"] = edge.wire_api
    if edge.needs_base_url:
        llm["base_url"] = FAKE_BASE_URL
    return {"manifest": {"llm": llm}, "covenant": "", "pad": ""}


def build_custom_schema_edge(provider: str, edge: SchemaEdge):
    """Build a schema-accepted custom-family edge through the REAL path:
    ``build_provider_defaults_from_manifest_llm`` -> ``LLMService(provider=<name>)``
    -> ``create_session``. base_url is passed to ``LLMService`` separately (as the
    agent-boot path does). Returns ``(adapter, session)``. Raises if the factory
    refuses the configuration (the caller asserts that for ``factory_raises``).
    """
    from lingtai.llm.service import LLMService, build_provider_defaults_from_manifest_llm

    manifest_llm = custom_manifest(provider, edge)["manifest"]["llm"]
    defaults = build_provider_defaults_from_manifest_llm(dict(manifest_llm), max_rpm=0)
    base_url = FAKE_BASE_URL if edge.needs_base_url else None
    with _mocked_sdk_clients(provider):
        service = LLMService(
            provider=provider,
            model="char-model",
            api_key="k",
            base_url=base_url,
            provider_defaults=defaults,
        )
        session = service.create_session(
            "system", tools=None, tracked=False, json_schema=edge.json_schema
        )
    adapter = service.get_adapter(provider, base_url)
    return adapter, session


# ===========================================================================
# Layer 2b — Responses stateful/stateless WIRE proof through the real LLMService
# ===========================================================================
#
# The registry matrix asserts the #861 mode BIT (``_stateless_replay``) on the
# session the real ``LLMService`` returns. This layer proves the mode's actual
# WIRE consequence on a two-turn continuation, still built through the real
# service route (``LLMService`` -> registered factory -> ``create_chat``), with a
# controllable Responses transport injected in place of the OpenAI SDK client:
#
#   * official/stateful (``openai`` + ``wire_api=responses``): turn 2 sends only
#     the new input item AND a ``previous_response_id`` pointing at turn 1's id.
#   * custom/stateless (``custom`` + ``api_compat=openai`` + ``wire_api=responses``):
#     turn 2 replays the FULL canonical history and sends NO ``previous_response_id``.
#
# This does not duplicate ``tests/test_custom_responses_stateless.py`` (which
# exhaustively characterizes the stateless session in isolation); it pins the one
# thing the ledger must own — that the *production selection* through
# ``LLMService`` yields these two distinct wires.


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
    registered factory reads provider defaults, applies ``responses_stateless_replay``
    via ``create_custom_adapter`` for the custom family, and builds the session).
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
# Optional-SDK guard (google-genai is a core dependency, but keep a helper so a
# future optional-extra move has one place to gate on).
# ---------------------------------------------------------------------------


def _patched_genai_client():
    """Patch the genai client the Gemini adapter constructs."""
    from google import genai  # noqa: F401  (core dependency — import must succeed)

    return patch("google.genai.Client", MagicMock())


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
