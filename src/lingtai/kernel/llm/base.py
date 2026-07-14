"""Provider-agnostic types and session ABC for the LLM protocol layer.

All agent code should depend on these types, never on provider-specific SDKs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lingtai.kernel.logging import get_logger

from .interface import ChatInterface, ToolResultBlock

logger = get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single function/tool invocation extracted from the LLM response.

    Attributes:
        name: Tool/function name.
        args: Parsed arguments dict.
        id: Provider-assigned call ID (e.g. ``call_xxxxx`` for OpenAI,
            ``toolu_xxxxx`` for Anthropic).  None for Gemini which doesn't
            use explicit tool-call IDs.
    """

    name: str
    args: dict
    id: str | None = None


@dataclass
class UsageMetadata:
    """Normalized token counts plus optional per-call ledger metadata."""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0
    # Optional safe, provider-specific metadata to merge into token_ledger.jsonl.
    # Do not place request bodies, API keys, or other secrets here.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Provider-agnostic response from an LLM call.

    Attributes:
        text: Concatenated text output (excludes thinking text).
        tool_calls: Extracted function/tool calls.
        usage: Token usage for this call.
        thoughts: List of thinking/reasoning text blocks (for verbose logging).
        raw: The original provider-specific response object. Use for escape
            hatches (e.g. Gemini grounding metadata, multimodal parts).
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: UsageMetadata = field(default_factory=UsageMetadata)
    thoughts: list[str] = field(default_factory=list)
    raw: Any = None
    # Stable identifier for this kernel-level LLM API round-trip.
    # SessionManager assigns it before logging llm_call/llm_response;
    # BaseAgent/ToolExecutor propagate it to every tool event produced from
    # the same assistant response so UI/replay code can group tool batches.
    api_call_id: str | None = None


# The single wire-facing description for registered ``FunctionSchema`` tools.
# Provider payload builders send this constant as those tools' top-level
# description; the full ``FunctionSchema.description`` prose renders only into
# the system prompt's
# ``## tools`` section (base_agent/tools.py:_refresh_tool_inventory_section).
# Parameter/property descriptions inside ``parameters`` are never touched.
WIRE_TOOL_DESCRIPTION = "See the system prompt for tool usage guidance."


@dataclass
class FunctionSchema:
    """Wraps a tool/function schema dict for type clarity.

    The ``parameters`` dict is already JSON-schema-shaped and provider-agnostic.

    ``description`` holds the full tool prose. It is rendered into the system
    prompt's ``## tools`` section and stored in canonical ChatInterface tool
    snapshots; provider wire payloads carry ``WIRE_TOOL_DESCRIPTION`` instead.

    ``glossary_package`` is an optional non-wire metadata field naming the
    importable resource package that owns the tool's ``glossary-{lang}.md``
    files.  The ``## tools`` renderer uses it to append a localized terminology
    body; it is never serialized into provider payloads (``to_dict`` excludes
    it alongside ``system_prompt``).
    """

    name: str
    description: str
    parameters: dict
    system_prompt: str = ""
    glossary_package: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    @staticmethod
    def list_to_dicts(schemas: list[FunctionSchema] | None) -> list[dict] | None:
        """Convert a list of FunctionSchema to dicts, or None if empty/None."""
        if not schemas:
            return None
        return [s.to_dict() for s in schemas]

    @classmethod
    def from_dicts(cls, dicts: list[dict] | None) -> list["FunctionSchema"] | None:
        """Convert tool dicts (as stored in ChatInterface) back to FunctionSchema objects."""
        if not dicts:
            return None
        return [
            cls(
                name=d["name"],
                description=d.get("description", ""),
                parameters=d.get("parameters", {}),
            )
            for d in dicts
        ]


# ---------------------------------------------------------------------------
# ChatSession ABC
# ---------------------------------------------------------------------------


class ChatSession(ABC):
    """Abstract multi-turn chat session."""

    # lingtai-assigned session ID, set by LLMService
    session_id: str = ""
    # Session metadata for get_state()
    _agent_type: str = ""
    _tracked: bool = True

    # Optional pre-request hook fired after the message is committed to the
    # canonical ChatInterface but before the API call is made. The kernel
    # installs ``_drain_tc_inbox`` here so involuntary tool-call pairs
    # (mail notifications, soul.flow voices) splice into the wire chat
    # mid-turn — between tool rounds within a single _handle_request —
    # rather than waiting for the outer turn to finish.
    #
    # Wire-state contract: at the moment the hook fires, the interface
    # tail must be ``user[tool_results]`` or ``user[text]`` — i.e.
    # ``has_pending_tool_calls()`` must return False, so the splicer can
    # safely append a new ``(call, result)`` pair without violating the
    # provider's strict pair-validation invariant.
    #
    # Sessions that don't use the canonical ChatInterface for wire
    # serialization (OpenAIResponsesSession, GeminiChatSession via
    # genai SDK) still call the hook for the agent-side drain, but the
    # spliced pair is only visible to the LLM on the *next* turn (when
    # the agent re-syncs from interface). For canonical-interface
    # adapters (anthropic, openai-CC, codex-Responses, deepseek), the
    # spliced pair is visible in the same API call as the triggering
    # tool_results.
    #
    # Default ``None`` — adapters that don't install a hook treat the
    # call as a no-op, preserving the legacy zero-hook behavior.
    pre_request_hook: "Callable[[ChatInterface], None] | None" = None

    def adapter_comment(self):
        """Optional legacy combined adapter note for ``_meta.agent_meta``.

        New adapters should prefer the explicit partitioned methods below:
        ``static_adapter_comment`` for resident, rule-like system-prompt text
        and ``dynamic_adapter_comment`` for per-turn tail state.  This legacy
        method remains for compatibility with adapters/tests that still expose
        one combined note.
        """
        return None

    def static_adapter_comment(self):
        """Optional adapter-authored static note for resident ``meta_guidance``.

        Return only durable/rule-like system-prompt content here.  Dynamic
        counters, ledgers, run state, and per-turn measurements belong in
        ``dynamic_adapter_comment`` so the kernel does not have to guess which
        adapter keys are static.
        """
        return None

    def dynamic_adapter_comment(self):
        """Optional adapter-authored dynamic note for tail ``_meta.agent_meta``.

        Return only per-turn/runtime state here.  The kernel may still perform
        generic size trimming (for example, dropping verbose ledger rows), but
        it should not need adapter-specific static-key blocklists.
        """
        return None
    def on_history_summarized(self, summarized_ids: list[str]) -> None:
        """Hook called after `system(action='summarize')` mutates chat history."""

        return None

    def request_history_rebuild(self, reason: str = "summarize_rebuild_only") -> bool:
        """Request a provider-context rebuild without mutating chat history.

        Used by ``system(action='summarize', rebuild=true)`` (the ``reason``
        default remains the internal ``summarize_rebuild_only`` epoch-reset label).
        Adapters with continuation/cache state can start a fresh full replay on the
        next model request and return True; adapters that always rebuild or have no
        such state may leave the default False.
        """

        return False

    def take_pending_reconstruction_event(self) -> dict | None:
        """Pop the one-shot delayed-summarize reconstruction event, if any.

        Adapters that perform an automatic provider-context rebuild when
        summarized history is pending and context crosses the reconstruction
        threshold (codex's ``_reset_ws_epoch("summarize_delayed")``) record a
        compact before-context (A) event here. The kernel consumes it exactly
        once and attaches it to the next visible tool result's
        ``_meta.tool_meta`` (permanent evidence). Default: no reconstruction
        machinery, so no event. One-shot semantics: returns the event and clears
        it, so a second call returns ``None``.
        """
        return None

    def context_overflow_status(self) -> dict | None:
        """Return the persistent hard-boundary overflow status, or ``None``.

        A session with an automatic one-shot forced provider-context rebuild
        (codex's ``_reset_ws_epoch("summarize_delayed")``) returns a small status
        dict ``{"usage": <float>}`` when that rebuild has already fired for the
        current continuous provider-usage ``>= 1.0`` episode, its first
        post-rebuild provider response has been observed, and current
        provider-reported usage is still STRICTLY above ``1.0`` — i.e. the forced
        rebuild failed to clear the overflow. The kernel renders the fixed
        human-authored ``Forced Rebuilt Failed`` warning from ``usage`` and keeps
        it on every ``_meta.tool_meta.context.molt`` result while active (a pure,
        idempotent read — unlike the one-shot
        :meth:`take_pending_reconstruction_event`). Default: no such machinery, so
        ``None``.
        """
        return None

    def on_notification_dismissed(self, channel: str | None = None) -> None:
        """Hook called after a notification dismiss/cleanup mutates the surface.

        A dismiss rewrites the resident notification meta on prior tool results,
        so — like ``on_history_summarized`` — adapters that reuse remote state
        (e.g. Codex WS) use this to start a fresh ws_full epoch. Default no-op.
        """

        return None

    @property
    @abstractmethod
    def interface(self) -> ChatInterface:
        """The canonical ChatInterface for this session."""

    @abstractmethod
    def send(self, message) -> LLMResponse:
        """Send a user message or tool results and return the model response.

        ``message`` can be:
        - A string (user text message)
        - A list of ToolResultBlock (canonical tool results)
        """

    def reset_provider_turn_state(self) -> None:
        """Reset transient provider turn state before a new user text turn.

        Most providers have no extra turn-scoped transport state. Adapters that
        do (for example Codex Responses-over-WebSocket turn-state headers) may
        override this hook. The kernel calls it only before string user-text
        messages, not before tool-result continuations.
        """

    def get_history(self) -> list[dict]:
        """Return serializable conversation history (canonical format)."""
        return self.interface.to_dict()

    def get_state(self) -> dict:
        """Return the full session state dict.

        Format: {"session_id": str, "messages": [...], "metadata": {...}}
        """
        return {
            "session_id": self.session_id,
            "messages": self.interface.to_dict(),
            "metadata": {
                "agent_type": self._agent_type,
                "created_at": self.interface.entries[0].timestamp if self.interface.entries else 0.0,
                "tracked": self._tracked,
            },
        }

    def total_usage(self) -> dict:
        """Sum tokens and count API calls across all messages."""
        return self.interface.total_usage()

    def usage_by_model(self) -> dict[str, dict]:
        """Breakdown of usage per model name."""
        return self.interface.usage_by_model()

    def send_stream(
        self,
        message,
        on_chunk: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Send a message with optional streaming callback for text chunks.

        If the session supports streaming, calls ``on_chunk(text_delta)``
        as text tokens arrive.  Always returns the complete ``LLMResponse``
        at the end.

        Default implementation falls back to non-streaming ``send()``.
        """
        response = self.send(message)
        if on_chunk and response.text:
            on_chunk(response.text)
        return response

    def commit_tool_results(self, tool_results: list) -> None:
        """Append tool results to history without an API call.

        Used when tool execution is intercepted (e.g., clarification_needed
        terminal tool) but the tool_use/tool_result pairing must be preserved
        in history for subsequent messages.

        Default is a no-op for adapters that don't need it (e.g., server-managed
        history).
        """

    def update_tools(self, tools: list[FunctionSchema] | None) -> None:
        """Replace the tool schemas for subsequent calls in this session.

        Used by the tool-store pattern: the orchestrator starts with
        meta-tools only and dynamically loads more as the model requests.

        Default: no-op. Override in session types that support it.
        """

    def update_system_prompt(self, system_prompt: str) -> None:
        """Replace the system prompt for subsequent calls in this session.

        Default: no-op. Override in session types that support it.
        """

    def update_system_prompt_batches(self, batches: list[str]) -> None:
        """Replace the system prompt using mutation-frequency batches.

        ``batches`` is the ordered output of
        ``build_system_prompt_batches``: each element is a contiguous
        chunk whose content tends to change at a different cadence
        (e.g. immovable / rarely-mutated / per-idle). Adapters that
        support per-block prompt caching (Anthropic's ``cache_control``)
        can place cache breakpoints at batch boundaries so only the
        volatile tail pays for re-caching.

        Default: concatenate to a string and delegate to
        ``update_system_prompt`` — providers without per-block caching
        see no behaviour change.
        """
        joined = "\n\n".join(b for b in batches if b)
        self.update_system_prompt(joined)

    def reset(self) -> None:
        """Reset the session's HTTP connection while preserving conversation state.

        Called after persistent API errors (e.g. 3+ consecutive 500s) to get a
        fresh connection.  History, tools, and system prompt are preserved —
        only the underlying HTTP client is recreated.

        Default: no-op.  Override in session types backed by a persistent
        HTTP client (Anthropic, OpenAI).  Gemini sessions with server-side
        state (Interactions API) cannot be meaningfully reset this way.
        """

    @property
    def interaction_id(self) -> str | None:
        """Return the current Interactions API interaction ID, or None.

        Only meaningful for Gemini ``InteractionsChatSession`` which chains
        calls via ``previous_interaction_id``.  Other session types return None.
        """
        return None

    def context_window(self) -> int:
        """Total context window in tokens for this session's model. 0 = unknown."""
        return 0

    # -----------------------------------------------------------------------
    # Context-overflow fail-loud detection (shared across all providers)
    # -----------------------------------------------------------------------
    #
    # When the provider rejects a request because the context exceeds its
    # hard token limit, this is a genuinely too-large wire — the kernel has
    # no license to silently discard historical canonical entries to fix
    # that (only an explicit ``summarize`` replacement may replace a
    # historical tool-result body; see ``lingtai.tools.system.summarize``
    # and the provider-context rebuild/replay invariant in
    # ``lingtai.llm.interface_converters``). ``_is_context_overflow_error()``
    # only classifies the error for logging/diagnostics; canonical and
    # rendered history are never trimmed here. The error propagates to the
    # existing AED over-window recovery path
    # (``lingtai.kernel.base_agent.turn._is_over_window_error`` /
    # ``aed_over_window_detected`` / ``aed_exhausted``), which is
    # deterministic, fully logged, and requires an explicit agent- or
    # operator-driven summarize/molt to actually recover.
    #
    # This lives on ChatSession (not LLMAdapter) because each provider only
    # needs to implement ``_is_context_overflow_error()`` to opt in to the
    # classification; the call sites that invoke ``_run_with_overflow_recovery``
    # wrap the provider-specific ``send()`` / ``send_stream()`` call.

    @staticmethod
    def _is_context_overflow_error(exc: Exception) -> bool:
        """Return True if *exc* is a provider context-length-exceeded error.

        Default returns False.  Override in subclasses that want this
        classified/logged distinctly. Never triggers trimming or a retry —
        see the module comment above.
        """
        return False

    def _run_with_overflow_recovery(self, do_call):
        """Run an API call, classifying and propagating context-overflow errors.

        ``do_call`` is a zero-arg callable performing one full attempt
        (build kwargs from current interface state + invoke the API).

        Returns ``(result, total_dropped, rounds)`` with ``total_dropped``
        and ``rounds`` always 0 on success — no entries are ever trimmed.
        On a provider context-overflow error, logs it as such and
        re-raises immediately (no retry with a shortened wire); the caller
        propagates into the existing AED over-window recovery. On any
        other error, re-raises immediately.
        """
        try:
            result = do_call()
            return result, 0, 0
        except Exception as exc:
            if self._is_context_overflow_error(exc):
                logger.warning(
                    "[overflow] provider context-length-exceeded error; "
                    "preserving full canonical history and propagating "
                    "(no trim, no retry): %s",
                    str(exc)[:300],
                )
            raise



