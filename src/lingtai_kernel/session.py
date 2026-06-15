"""SessionManager — LLM session lifecycle, token tracking, and compaction.

Extracted from BaseAgent to isolate LLM communication concerns.
BaseAgent delegates all session operations here.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import math
import uuid
from typing import Any, Callable, TYPE_CHECKING

from .config import AgentConfig
from .llm import (
    ChatSession,
    FunctionSchema,
    LLMResponse,
    LLMService,
)
from .llm_utils import (
    send_with_timeout,
    send_with_timeout_stream,
    track_llm_usage,
)
from .logging import get_logger
from .token_counter import count_tokens, count_tool_tokens

logger = get_logger()

if TYPE_CHECKING:
    from .llm.interface import ChatInterface


class SessionManager:
    """Manages LLM session lifecycle, token tracking, and context compaction.

    Receives callback functions for building system prompts and tool schemas
    so it has no reference to BaseAgent.
    """

    def __init__(
        self,
        *,
        llm_service: LLMService,
        config: AgentConfig,
        agent_name: str | None = None,
        streaming: bool,
        build_system_prompt_fn: Callable[[], str],
        build_tool_schemas_fn: Callable[[], list[FunctionSchema]],
        logger_fn: Callable[..., None] | None,
        build_system_batches_fn: Callable[[], list[str]] | None = None,
        compact_history_fn: Callable[..., Any] | None = None,
        force_context_forget_fn: Callable[..., dict] | None = None,
        save_local_state_fn: Callable[..., None] | None = None,
    ):
        self._llm_service = llm_service
        self._config = config
        self._agent_name = agent_name
        self._display_name = agent_name or "agent"
        self._streaming = streaming
        self._build_system_prompt_fn = build_system_prompt_fn
        self._build_tool_schemas_fn = build_tool_schemas_fn
        self._logger_fn = logger_fn
        self._compact_history_fn = compact_history_fn
        self._force_context_forget_fn = force_context_forget_fn
        self._save_local_state_fn = save_local_state_fn
        # Optional batched system-prompt builder. When provided, adapters
        # that support per-block caching receive mutation-frequency batches
        # and can place cache breakpoints between them. When absent, the
        # string builder is used for everything.
        self._build_system_batches_fn = build_system_batches_fn
        # Persistent LLM session
        self._chat: ChatSession | None = None
        self._interaction_id: str | None = None

        # Token tracking
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_thinking_tokens = 0
        self._total_cached_tokens = 0
        self._api_calls = 0
        self._last_tool_context = "send_message"
        self._system_prompt_tokens = 0
        self._tools_tokens = 0
        self._token_decomp_dirty = True
        self._token_fallback_warned = False
        self._latest_input_tokens = 0

        # Streaming state
        self._text_already_streamed = False
        self._intermediate_text_streamed = False
        self._message_seq = 0

        # Timeout pool for LLM calls
        self._timeout_pool = ThreadPoolExecutor(max_workers=1)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def chat(self) -> ChatSession | None:
        """The current LLM chat session (or None if not yet created)."""
        return self._chat

    @chat.setter
    def chat(self, value: ChatSession | None) -> None:
        self._chat = value

    @property
    def token_decomp_dirty(self) -> bool:
        return self._token_decomp_dirty

    @token_decomp_dirty.setter
    def token_decomp_dirty(self, value: bool) -> None:
        self._token_decomp_dirty = value

    @property
    def streaming(self) -> bool:
        return self._streaming

    @property
    def interaction_id(self) -> str | None:
        return self._interaction_id

    @interaction_id.setter
    def interaction_id(self, value: str | None) -> None:
        self._interaction_id = value

    @property
    def intermediate_text_streamed(self) -> bool:
        return self._intermediate_text_streamed

    @intermediate_text_streamed.setter
    def intermediate_text_streamed(self, value: bool) -> None:
        self._intermediate_text_streamed = value

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self, event_type: str, **fields) -> None:
        """Delegate logging to the injected logger function."""
        if self._logger_fn is not None:
            self._logger_fn(event_type, **fields)

    # ------------------------------------------------------------------
    # LLM communication
    # ------------------------------------------------------------------

    def ensure_session(self) -> ChatSession:
        """Ensure a persistent LLM session exists, creating one if needed."""
        if self._chat is None:
            self._chat = self._llm_service.create_session(
                system_prompt=self._build_system_prompt_fn(),
                tools=self._build_tool_schemas_fn() or None,
                model=self._config.model or self._llm_service.model,
                thinking="high",
                agent_type=self._display_name,
                tracked=True,
                interaction_id=self._interaction_id,
                provider=self._config.provider,
            )
        return self._chat

    def _rebuild_session(
        self, interface: "ChatInterface", tracked: bool = True,
    ) -> None:
        """Create a new chat session with current config, preserving history."""
        self._chat = self._llm_service.create_session(
            system_prompt=self._build_system_prompt_fn(),
            tools=self._build_tool_schemas_fn() or None,
            model=self._config.model or self._llm_service.model,
            thinking="high",
            agent_type=self._display_name,
            tracked=tracked,
            provider=self._config.provider,
            interface=interface,
        )

    def _health_check(self, message: Any) -> None:
        """Pre-send invariant checks on the canonical interface.

        Runs after system-prompt/tools refresh and before dispatch. Each
        check is a one-liner so future invariants land here, not scattered
        across adapters. Non-destructive: synthesizes placeholders rather
        than mutating committed history. Every heal logs a structured
        ``health_check`` event so operators can audit how often each
        invariant fires and trace the upstream callsite.
        """
        # Tail tool-call pairing: if we're about to append a user-text
        # message but the tail is assistant[tool_calls] (e.g. the prior
        # turn's tool_results never landed because of a timeout, daemon
        # crash, or partial AED recovery), close the dangling calls with
        # synthesized [aborted: ...] tool_results. The next add_user_message
        # will then succeed, and the model sees the abort reason on the
        # next turn.
        if isinstance(message, str) and self._chat.interface.has_pending_tool_calls():
            self._chat.interface.close_pending_tool_calls(
                reason="health_check:pre_send_pairing"
            )
            self._log("health_check", check="pre_send_pairing", action="auto_heal")

    def _message_kind(self, message: Any) -> str:
        if message is None:
            return "none"
        if isinstance(message, str):
            return "text"
        if isinstance(message, list):
            return "tool_results"
        return type(message).__name__

    def _estimate_pending_tokens(self, message: Any) -> int:
        """Estimate tokens that ``message`` will append before provider dispatch.

        The canonical interface estimate covers committed history, system,
        and tools.  Pre-send gating also has to account for the message that
        the adapter is about to append: request text, explicit tool results,
        or nothing for wire-drive ``send(None)`` continuations.
        """
        if message is None:
            return 0
        if isinstance(message, str):
            return count_tokens(message)
        if isinstance(message, list):
            total = 0
            for block in message:
                content = getattr(block, "content", block)
                content_str = content if isinstance(content, str) else json.dumps(content, default=str)
                # Include the tool name/id as a small overhead proxy so list
                # estimates are closer to the canonical serialized wire.
                name = getattr(block, "name", "")
                block_id = getattr(block, "id", "")
                total += count_tokens(f"{name}:{block_id}:{content_str}")
            return total
        return count_tokens(json.dumps(message, default=str))

    def estimate_context_tokens_with_pending(self, message: Any = None) -> int:
        """Return local context estimate for current wire plus pending message."""
        if self._chat is None:
            return 0
        return self._chat.interface.estimate_context_tokens() + self._estimate_pending_tokens(message)

    def _hard_gate_limit(self) -> tuple[int, float, int]:
        """Return (context_window, threshold_fraction, hard_token_limit)."""
        if self._chat is None:
            return 0, 0.0, 0
        ctx_window = self._config.context_limit or self._chat.context_window()
        raw_threshold = getattr(self._config, "context_hard_pressure", 0.98)
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            threshold = 0.98
        if not math.isfinite(threshold) or threshold <= 0 or threshold > 1:
            self._log(
                "context_hard_gate",
                action="invalid_threshold_defaulted",
                configured_threshold=repr(raw_threshold),
                default_threshold=0.98,
            )
            threshold = 0.98
        if ctx_window <= 0:
            return ctx_window, threshold, 0
        hard_limit = max(1, int(ctx_window * threshold))
        return ctx_window, threshold, hard_limit

    def _is_tool_result_message(self, message: Any) -> bool:
        if not isinstance(message, list):
            return False
        try:
            from .llm.interface import ToolResultBlock
        except Exception:
            return False
        return bool(message) and all(isinstance(item, ToolResultBlock) for item in message)

    def _canonical_has_tool_results(self, ids: set[str]) -> bool:
        if self._chat is None or not ids:
            return False
        try:
            from .llm.interface import ToolResultBlock
            for entry in self._chat.interface.entries:
                if entry.role != "user":
                    continue
                for block in entry.content:
                    if isinstance(block, ToolResultBlock) and block.id in ids:
                        ids.discard(block.id)
                        if not ids:
                            return True
        except Exception:
            return False
        return not ids

    def _commit_blocked_tool_results(self, message: Any) -> None:
        """Commit completed tool results before a local hard-gate block.

        A post-tool continuation can be blocked after local side effects already
        happened. Preserve those real results through the session API first so
        adapters that keep provider-private state can update it, then fall back
        to the canonical interface if the adapter implementation was a no-op.
        """
        if not self._is_tool_result_message(message) or self._chat is None:
            return
        ids = {str(getattr(block, "id", "")) for block in message if getattr(block, "id", None)}
        try:
            self._chat.commit_tool_results(message)
        except Exception as exc:  # noqa: BLE001 - preserve canonical truth below
            self._log(
                "context_hard_gate",
                action="commit_tool_results_failed",
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
        if not self._canonical_has_tool_results(set(ids)):
            self._chat.interface.add_tool_results(message)

    def _context_block_response(
        self,
        message: Any,
        *,
        estimated_tokens: int,
        context_window: int,
        threshold: float,
        hard_limit: int,
        stage: str,
    ) -> LLMResponse:
        """Return a local terminal response without calling the provider."""
        self._commit_blocked_tool_results(message)

        pressure = estimated_tokens / context_window if context_window > 0 else 0.0
        self._log(
            "context_hard_gate",
            action="blocked",
            stage=stage,
            message_kind=self._message_kind(message),
            estimated_tokens=estimated_tokens,
            context_window=context_window,
            hard_limit_tokens=hard_limit,
            threshold=threshold,
            pressure=pressure,
        )
        text = (
            "[system] The runtime stopped this turn before calling the LLM "
            "because the estimated context would exceed the configured hard "
            "context ceiling. Oversized historical tool results were compacted "
            "once first; if the turn contained completed tool results, those "
            "results were committed locally so side effects are not repeated. "
            "Please molt/clear context or reduce the pending input before "
            "continuing."
        )
        from .llm.interface import TextBlock

        if self._chat is not None:
            try:
                self._chat.interface.add_assistant_message([TextBlock(text=text)])
                # Provider-private/server-side sessions may have seen a prior
                # assistant tool-call without the locally blocked continuation.
                # Rebuild from canonical history so the next send starts from
                # the truthful local wire rather than stale provider state.
                self._rebuild_session(self._chat.interface)
            except Exception as exc:  # noqa: BLE001 - terminal response still returns
                self._log(
                    "context_hard_gate",
                    action="local_history_record_failed",
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
        if self._save_local_state_fn is not None:
            try:
                self._save_local_state_fn(ledger_source="context_hard_gate")
            except Exception as exc:  # noqa: BLE001 - best-effort persistence
                self._log(
                    "context_hard_gate",
                    action="local_state_save_failed",
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )

        return LLMResponse(
            text=text,
            usage=None,
            raw={
                "context_hard_gate": {
                    "action": "blocked",
                    "stage": stage,
                    "estimated_tokens": estimated_tokens,
                    "context_window": context_window,
                    "hard_limit_tokens": hard_limit,
                    "threshold": threshold,
                    "pressure": pressure,
                    "message_kind": self._message_kind(message),
                }
            },
        )

    def _enforce_context_hard_gate(self, message: Any) -> LLMResponse | None:
        """Enforce the pre-send hard context ceiling before any provider call.

        Recovery ladder is deliberately LLM-free and bounded:
        1. estimate current wire + pending message;
        2. if over budget, run one deterministic tool-result compaction pass;
        3. if still over and the pending message can survive a context reset,
           force a system-authored ``context_forget(source="hard_ceiling")``;
        4. if still over (or pending tool-results would be invalidated by a
           reset), return a local terminal response without logging ``llm_call``.
        """
        if self._chat is None:
            return None
        context_window, threshold, hard_limit = self._hard_gate_limit()
        if hard_limit <= 0:
            return None

        estimated = self.estimate_context_tokens_with_pending(message)
        if estimated <= hard_limit:
            return None

        pressure = estimated / context_window if context_window > 0 else 0.0
        self._log(
            "context_hard_gate",
            action="compact_attempt",
            stage="initial",
            message_kind=self._message_kind(message),
            estimated_tokens=estimated,
            context_window=context_window,
            hard_limit_tokens=hard_limit,
            threshold=threshold,
            pressure=pressure,
        )

        if self._compact_history_fn is not None:
            try:
                self._compact_history_fn(source="hard_ceiling")
            except Exception as exc:  # noqa: BLE001 - gate must remain local
                self._log(
                    "context_hard_gate",
                    action="compaction_failed",
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )

        estimated = self.estimate_context_tokens_with_pending(message)
        if estimated <= hard_limit:
            self._log(
                "context_hard_gate",
                action="allow_after_compaction",
                message_kind=self._message_kind(message),
                estimated_tokens=estimated,
                context_window=context_window,
                hard_limit_tokens=hard_limit,
                threshold=threshold,
                pressure=estimated / context_window if context_window > 0 else 0.0,
            )
            return None

        if (
            self._force_context_forget_fn is not None
            and isinstance(message, str)
        ):
            try:
                result = self._force_context_forget_fn(source="hard_ceiling")
                self._log(
                    "context_hard_gate",
                    action="forced_context_forget",
                    message_kind=self._message_kind(message),
                    estimated_tokens_before_forget=estimated,
                    context_window=context_window,
                    hard_limit_tokens=hard_limit,
                    threshold=threshold,
                    molt_count=result.get("molt_count") if isinstance(result, dict) else None,
                )
            except Exception as exc:  # noqa: BLE001 - fall through to block
                self._log(
                    "context_hard_gate",
                    action="forced_context_forget_failed",
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
            else:
                # context_forget swaps the live chat; refresh hard-limit data
                # and only proceed if the fresh wire + pending message now fits.
                context_window, threshold, hard_limit = self._hard_gate_limit()
                estimated = self.estimate_context_tokens_with_pending(message)
                if hard_limit > 0 and estimated <= hard_limit:
                    self._log(
                        "context_hard_gate",
                        action="allow_after_forced_context_forget",
                        message_kind=self._message_kind(message),
                        estimated_tokens=estimated,
                        context_window=context_window,
                        hard_limit_tokens=hard_limit,
                        threshold=threshold,
                        pressure=estimated / context_window if context_window > 0 else 0.0,
                    )
                    return None

        return self._context_block_response(
            message,
            estimated_tokens=estimated,
            context_window=context_window,
            threshold=threshold,
            hard_limit=hard_limit,
            stage="after_recovery",
        )

    def send(self, message: Any) -> LLMResponse:
        """Send a message to the LLM, reusing the persistent chat session.

        Single attempt — no retry. Raises on any failure; the caller
        (BaseAgent._run_loop AED loop) handles recovery.
        """
        self.ensure_session()

        # Rebuild system prompt and tools every turn — they may have changed
        # (e.g. memory loaded, identity updated, capabilities added after refresh).
        # If the content is identical, this is a no-op at the LLM level.
        # Prefer the batched form so adapters can place per-block cache
        # breakpoints between mutation-frequency tiers. The default
        # update_system_prompt_batches concatenates and delegates to
        # update_system_prompt, so providers without per-block caching
        # see the exact same byte stream.
        if self._build_system_batches_fn is not None:
            self._chat.update_system_prompt_batches(self._build_system_batches_fn())
        else:
            self._chat.update_system_prompt(self._build_system_prompt_fn())
        self._chat.update_tools(self._build_tool_schemas_fn() or None)

        self._health_check(message)
        blocked_response = self._enforce_context_hard_gate(message)
        if blocked_response is not None:
            return blocked_response

        api_call_id = f"api_{uuid.uuid4().hex[:12]}"
        self._log(
            "llm_call",
            model=self._config.model or self._llm_service.model or "unknown",
            api_call_id=api_call_id,
        )

        retry_timeout = self._config.retry_timeout

        if self._streaming:
            response = self._send_streaming(message, retry_timeout)
        else:
            response = send_with_timeout(
                chat=self._chat,
                message=message,
                timeout_pool=self._timeout_pool,
                retry_timeout=retry_timeout,
                agent_name=self._display_name,
                logger=logger,
            )

        response.api_call_id = api_call_id
        self._track_usage(response)
        # Preserve interaction ID for session reuse
        if hasattr(self._chat, "interaction_id") and self._chat.interaction_id:
            self._interaction_id = self._chat.interaction_id
        return response

    def _send_streaming(
        self, message: Any, retry_timeout: float
    ) -> LLMResponse:
        """Streaming LLM send via send_stream."""
        self._message_seq += 1

        response = send_with_timeout_stream(
            chat=self._chat,
            message=message,
            timeout_pool=self._timeout_pool,
            retry_timeout=retry_timeout,
            agent_name=self._display_name,
            logger=logger,
        )

        if response.text:
            if response.tool_calls:
                self._intermediate_text_streamed = True
            else:
                self._text_already_streamed = True

        return response

    # ------------------------------------------------------------------
    # Compaction
    # ------------------------------------------------------------------

    def get_context_pressure(self) -> float:
        """Return context usage as fraction (0.0 to 1.0). Returns 0.0 if unknown."""
        if self._chat is None:
            return 0.0
        # Use configured context_limit if set, otherwise model default
        ctx_window = self._config.context_limit or self._chat.context_window()
        if ctx_window <= 0:
            return 0.0
        # Use local estimate (reflects current wire state including the
        # message about to be sent) as the primary source.  Fall back to
        # server-reported input tokens from the last response only when
        # the local estimate is unavailable (e.g. empty interface).
        tokens = self._chat.interface.estimate_context_tokens()
        if tokens <= 0:
            tokens = self._latest_input_tokens
        return tokens / ctx_window if tokens > 0 else 0.0

    # ------------------------------------------------------------------
    # Token tracking
    # ------------------------------------------------------------------

    def _update_token_decomposition(self) -> None:
        """Recompute cached system prompt and tools token counts."""
        self._system_prompt_tokens = count_tokens(self._build_system_prompt_fn())
        self._tools_tokens = count_tool_tokens(self._build_tool_schemas_fn())
        self._token_decomp_dirty = False

    def _track_usage(self, response: LLMResponse) -> None:
        """Accumulate token usage from an LLMResponse.

        If the provider returns all-zero usage, falls back to the local
        tokenizer (tiktoken / gemini / char estimate) and sets
        ``token_fallback_used`` so the TUI can warn the user.
        """
        if self._token_decomp_dirty:
            self._update_token_decomposition()

        # Detect zero-usage responses and estimate locally
        usage = response.usage
        fallback = False
        if usage and usage.input_tokens == 0 and usage.output_tokens == 0:
            estimated_output = count_tokens(response.text or "")
            # Estimate input from interface history (last user message + system prompt)
            estimated_input = self._system_prompt_tokens + self._tools_tokens
            if self._chat and self._chat.interface:
                last_entries = self._chat.interface.entries[-2:]  # last user + assistant
                for entry in last_entries:
                    for block in entry.content:
                        if hasattr(block, "text"):
                            estimated_input += count_tokens(block.text)
            from .llm.base import UsageMetadata
            usage = UsageMetadata(
                input_tokens=estimated_input,
                output_tokens=estimated_output,
            )
            response = LLMResponse(
                text=response.text,
                tool_calls=response.tool_calls,
                usage=usage,
                thoughts=response.thoughts,
                raw=response.raw,
                api_call_id=response.api_call_id,
            )
            fallback = True
            if not self._token_fallback_warned:
                self._token_fallback_warned = True
                logger.warning(
                    f"[{self._display_name}] Provider returned 0 tokens — "
                    f"using local tokenizer estimate"
                )
                self._log("token_fallback", reason="provider returned 0 tokens")

        token_state = {
            "input": self._total_input_tokens,
            "output": self._total_output_tokens,
            "thinking": self._total_thinking_tokens,
            "cached": self._total_cached_tokens,
            "api_calls": self._api_calls,
        }
        track_llm_usage(
            response=response,
            token_state=token_state,
            agent_name=self._display_name,
            last_tool_context=self._last_tool_context,
            system_tokens=self._system_prompt_tokens,
            tools_tokens=self._tools_tokens,
        )
        self._total_input_tokens = token_state["input"]
        self._total_output_tokens = token_state["output"]
        self._total_thinking_tokens = token_state["thinking"]
        self._total_cached_tokens = token_state["cached"]
        self._api_calls = token_state["api_calls"]
        if response.usage:
            self._latest_input_tokens = response.usage.input_tokens
            self._log(
                "llm_response",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                thinking_tokens=response.usage.thinking_tokens,
                cached_tokens=response.usage.cached_tokens,
                estimated=fallback,
                api_call_id=response.api_call_id,
            )

    def get_token_usage(self) -> dict:
        """Return token usage summary."""
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "thinking_tokens": self._total_thinking_tokens,
            "cached_tokens": self._total_cached_tokens,
            "total_tokens": (
                self._total_input_tokens
                + self._total_output_tokens
                + self._total_thinking_tokens
            ),
            "api_calls": self._api_calls,
            "ctx_system_tokens": self._system_prompt_tokens,
            "ctx_tools_tokens": self._tools_tokens,
            "ctx_history_tokens": max(
                0,
                self._latest_input_tokens
                - self._system_prompt_tokens
                - self._tools_tokens,
            ),
            "ctx_total_tokens": self._latest_input_tokens,
        }

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def get_chat_state(self) -> dict:
        """Serialize current chat session for persistence."""
        if self._chat is None:
            return {}
        try:
            return {"messages": self._chat.interface.to_dict()}
        except Exception:
            return {}

    def restore_chat(self, state: dict) -> None:
        """Restore chat history with current system prompt and tools.

        Heals two classes of on-disk corruption before building the session:
        1. Set-level orphans (tool_call without result or vice versa) —
           handled by enforce_tool_pairing(), which strips them (except tail
           assistant[tool_calls], which is deferred).
        2. Positional violations (assistant[tool_calls] at tail with no
           matching tool_results) — handled by close_pending_tool_calls(),
           which synthesizes placeholder error results so the next send is
           well-formed for strict providers (DeepSeek, OpenAI).
        """
        from .llm.interface import ChatInterface
        messages = state.get("messages")
        if messages:
            try:
                interface = ChatInterface.from_dict(messages)
                interface.enforce_tool_pairing()
                if interface.has_pending_tool_calls():
                    interface.close_pending_tool_calls(
                        reason="restored from disk — prior session ended mid-tool-loop"
                    )
                self._rebuild_session(interface)
                return
            except Exception as e:
                logger.warning(
                    f"[{self._display_name}] Failed to restore chat: {e}. Starting fresh.",
                    exc_info=True,
                )
        self.ensure_session()

    def restore_token_state(self, state: dict) -> None:
        """Restore cumulative token counters from a saved session."""
        self._total_input_tokens = state.get("input_tokens", 0)
        self._total_output_tokens = state.get("output_tokens", 0)
        self._total_thinking_tokens = state.get("thinking_tokens", 0)
        self._total_cached_tokens = state.get("cached_tokens", 0)
        self._api_calls = state.get("api_calls", 0)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Shut down the timeout pool."""
        self._timeout_pool.shutdown(wait=False)
