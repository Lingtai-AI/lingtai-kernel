"""DeepSeek adapter — OpenAI-compat wrapper that satisfies the
reasoning_content round-trip contract for thinking mode, on both the
Chat Completions and Responses wires.

Chat Completions
----------------

DeepSeek V4 thinking mode rejects requests missing ``reasoning_content``
on assistant turns once thinking has been triggered. Omitting it returns
HTTP 400:

    "The `reasoning_content` in the thinking mode must be passed back
     to the API."

The actual contract (determined empirically — the docs understate it):

    Once any assistant turn in the conversation has tool_calls, ALL
    subsequent assistant turns (tool-call AND plain-text) must carry
    reasoning_content when replayed.

Assistant turns BEFORE the first tool_call don't need it. After the
first tool_call, every assistant turn needs it — including the final
plain-text reply that followed the tool loop.

Real reasoning is preserved end-to-end now. The OpenAI adapter captures
``reasoning_content`` into a ThinkingBlock on every assistant turn
(``openai/adapter.py``); ``interface_converters.to_openai`` emits the
ThinkingBlock back as ``reasoning_content`` on replay. The historical
"byte-identical placeholder" approach (commits afc7ddc → 86c2a3d)
caused DeepSeek's cache fast-path to collapse onto the placeholder
string, producing empty responses (issue #9). Real per-turn reasoning
is byte-different by construction and avoids the collapse.

The only remaining responsibility of this adapter is the **fallback**:
if an assistant turn replayed from history has no captured ThinkingBlock
(e.g. chat_history.jsonl entries written before this fix shipped, or
entries where the provider returned no reasoning text at all), inject a
per-turn-unique stub so DeepSeek's field-presence validator is satisfied
without re-introducing the cache-collapse pattern.

Responses API
-------------

``DeepSeekAdapter`` now forwards the same wire-selection flags as the
OpenAI-compatible family (``wire_api`` / ``use_responses`` /
``force_responses`` / ``compact_threshold`` /
``responses_stateless_replay``), so a DeepSeek endpoint that serves
``/responses`` (native or via an OpenAI-compatible gateway) can be
opted in. The Responses wire has no ``reasoning_content`` field; its
equivalent is the ``reasoning`` input item:

    {"type": "reasoning", "summary": [{"type": "summary_text", "text": ...}]}

``to_responses_input`` already emits those from captured ThinkingBlocks.
``DeepSeekResponsesSession`` adds the fallback: after the first
``function_call`` item, any later assistant turn that has no reasoning
item gets a per-turn-unique ``reasoning`` item injected — the Responses
analogue of ``DeepSeekChatSession._build_messages``. Sessions are
stateless (no ``previous_response_id``) and never send generic
``context_management`` (``compact_threshold=None`` default), matching
the MiMo pattern for endpoints without server-side response storage.

Everything else inherits from ``OpenAIAdapter`` unchanged via the
``_build_messages`` and ``_session_class`` hook points on the parent.
"""

from __future__ import annotations

from ..openai.adapter import OpenAIAdapter, OpenAIChatSession, OpenAIResponsesSession


_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _fallback_reasoning_for(msg: dict, turn_idx: int) -> str:
    """Build a per-turn-unique reasoning stub for an assistant message.

    Used only when an assistant turn carries no real reasoning_content
    (typically: history entries that predate the ThinkingBlock-preservation
    fix, or turns where the provider returned no reasoning text). The
    string must be byte-different per turn — a constant placeholder
    triggers DeepSeek's cache fast-path to collapse onto it and emit
    empty responses (issue #9).

    DeepSeek validates field presence, not content, so any non-empty
    string is accepted. We inline tool names and the call ids to keep
    the stub naturally unique per turn.
    """
    tool_calls = msg.get("tool_calls") or []
    if tool_calls:
        names = ",".join(
            (tc.get("function", {}) or {}).get("name", "") for tc in tool_calls
        )
        ids = ",".join(tc.get("id", "") for tc in tool_calls)
        return f"call {names} [{ids}] (turn {turn_idx})"
    # Plain-text post-tool-call turn — content is per-turn unique by nature.
    content = msg.get("content") or ""
    snippet = content[:64].replace("\n", " ")
    return f"reply [{snippet}] (turn {turn_idx})"


def _fallback_responses_reasoning_item(role_text: str, turn_idx: int) -> dict:
    """Build a per-turn-unique Responses ``reasoning`` input item.

    ``role_text`` is the assistant plain-text content for the turn being
    repaired (empty for a tool-call turn). The stub is inline-suffixed
    with the turn index so consecutive missing turns stay byte-different.
    """
    snippet = (role_text or "")[:64].replace("\n", " ")
    label = f"reply [{snippet}]" if snippet else "call"
    return {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": f"{label} (turn {turn_idx})"}],
    }


def _inject_responses_reasoning_fallback(items: list[dict]) -> list[dict]:
    """Inject DeepSeek reasoning-item fallbacks into a Responses input list.

    Responses items are a flat list; a single assistant turn can span
    multiple items (reasoning, text, function_call). After the first
    ``function_call`` item we must ensure every later assistant turn
    carries a ``reasoning`` item — the Responses analogue of
    ``DeepSeekChatSession._build_messages``. We walk the list, track
    whether a ``function_call`` has been seen, and for each ``assistant``
    text item that follows a call without an immediately-preceding
    reasoning item, insert one before it.
    """
    seen_function_call = False
    turn_idx = 0
    out: list[dict] = []
    for item in items:
        if item.get("type") == "function_call":
            seen_function_call = True
            out.append(item)
            continue
        if seen_function_call and item.get("role") == "assistant":
            turn_idx += 1
            # Insert a fallback reasoning item before this assistant text
            # item unless the immediately preceding item already carries
            # reasoning (real thinking was preserved).
            if not (out and out[-1].get("type") == "reasoning"):
                out.append(
                    _fallback_responses_reasoning_item(
                        item.get("content") or "", turn_idx
                    )
                )
            out.append(item)
            continue
        out.append(item)
    return out


class DeepSeekChatSession(OpenAIChatSession):
    """Chat session that satisfies DeepSeek's reasoning_content round-trip contract.

    Real reasoning_content is emitted by ``interface_converters.to_openai``
    when the canonical interface has a ThinkingBlock on the assistant turn.
    This subclass only injects a per-turn-unique fallback on assistant
    turns that lack one — typically rehydrated history entries from before
    the fix shipped.
    """

    def _build_messages(self) -> list[dict]:
        messages = super()._build_messages()
        # Field-presence requirement only kicks in once thinking mode has
        # been invoked by an assistant tool_call. Earlier plain-text turns
        # must NOT carry reasoning_content — DeepSeek rejects that.
        seen_tool_call = False
        turn_idx = 0
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            if msg.get("tool_calls"):
                seen_tool_call = True
            if seen_tool_call:
                turn_idx += 1
                if not msg.get("reasoning_content"):
                    msg["reasoning_content"] = _fallback_reasoning_for(msg, turn_idx)
        return messages


class DeepSeekResponsesSession(OpenAIResponsesSession):
    """Stateless Responses session that satisfies DeepSeek's reasoning round-trip.

    Replays the full canonical interface as Responses input items every
    turn (no ``previous_response_id``, no server-side store) and injects a
    per-turn-unique ``reasoning`` item fallback on assistant turns after
    the first ``function_call`` that lack preserved thinking — the
    Responses analogue of ``DeepSeekChatSession._build_messages``.
    """

    def _replay_input_items(self) -> list[dict]:
        items = super()._replay_input_items()
        return _inject_responses_reasoning_fallback(items)


class DeepSeekAdapter(OpenAIAdapter):
    """OpenAI-compat adapter pinned to DeepSeek with reasoning_content round-trip.

    Wire selection is configurable: by default Chat Completions
    (``DeepSeekChatSession``); set ``wire_api="responses"`` (or the legacy
    ``use_responses``/``force_responses`` flags) to use the Responses wire
    via ``DeepSeekResponsesSession`` for endpoints that serve ``/responses``.
    """

    _session_class = DeepSeekChatSession

    def _default_prompt_cache_key(self, model: str) -> str:
        # Fixed provider identity — use a clean ``lingtai-deepseek`` namespace
        # rather than the base_url host. DeepSeek Chat Completions accepts
        # ``prompt_cache_key`` (compat probe); a stable key lets successive
        # turns hit the cross-request prompt cache.
        return f"lingtai-deepseek:{model}:v1"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout_ms: int = 300_000,
        max_rpm: int = 0,
        default_headers: dict | None = None,
        wire_api: str | None = None,
        use_responses: bool | None = None,
        force_responses: bool | None = None,
        compact_threshold: int | None = None,
        responses_stateless_replay: bool = True,
    ):
        # Forward wire-selection flags so ``_should_use_responses()`` can
        # opt DeepSeek into the Responses API. Defaults preserve today's
        # Chat Completions behavior (no flags). ``compact_threshold``
        # defaults to None on the Responses wire (no generic
        # ``context_management`` — MiMo/Codex precedent) and is only
        # non-None when explicitly configured; Chat Completions ignores
        # it entirely.
        kwargs: dict = {}
        if wire_api is not None:
            kwargs["wire_api"] = wire_api
        if use_responses is not None:
            kwargs["use_responses"] = use_responses
        if force_responses is not None:
            kwargs["force_responses"] = force_responses
        kwargs["compact_threshold"] = compact_threshold
        kwargs["responses_stateless_replay"] = responses_stateless_replay
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEEPSEEK_BASE_URL,
            timeout_ms=timeout_ms,
            max_rpm=max_rpm,
            default_headers=default_headers,
            **kwargs,
        )

    def _chat_route_id(self) -> str:
        # DeepSeek Chat route owner: inherits the generic collapse mapper
        # under its own replaceable route identity.
        return "deepseek/chat"

    def _responses_route_id(self) -> str:
        # DeepSeek Responses route owner: byte-identical to the generic
        # Responses mapping today, independently replaceable later.
        return "deepseek/responses"

    def _create_responses_session(
        self,
        model: str,
        system_prompt: str,
        tools=None,
        json_schema: dict | None = None,
        force_tool_call: bool = False,
        interface=None,
        thinking: str = "default",
        context_window: int = 0,
    ) -> DeepSeekResponsesSession:
        from ..openai.adapter import _build_responses_tools

        if interface is None:
            from lingtai.kernel.llm.interface import ChatInterface
            interface = ChatInterface()
            from lingtai.kernel.llm.base import FunctionSchema
            interface.add_system(system_prompt, tools=FunctionSchema.list_to_dicts(tools))

        ds_tools = _build_responses_tools(tools)
        tool_choice: str | None = None
        if force_tool_call and ds_tools:
            tool_choice = "required"

        extra_kwargs: dict = {}
        if json_schema is not None:
            extra_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("title", "response"),
                    "strict": True,
                    "schema": json_schema,
                },
            }
        construction = self._responses_reasoning_construction(thinking)
        extra_kwargs.update(construction.materialize_json())

        session = DeepSeekResponsesSession(
            client=self._client,
            model=model,
            instructions=system_prompt,
            tools=ds_tools,
            tool_choice=tool_choice,
            extra_kwargs=extra_kwargs,
            previous_response_id=None,
            compact_threshold=self._compact_threshold,
            interface=interface,
            prompt_cache_key=self._resolve_prompt_cache_key(model),
            context_window=context_window,
            stateless_replay=self._responses_stateless_replay,
        )
        session.reasoning_emission = construction.emission()
        return session
