"""Converters between canonical ChatInterface and provider-specific formats.

Naming convention:
- to_<provider>(iface) -> provider message list
- from_<provider>(messages, ...) -> ChatInterface
"""

from __future__ import annotations

import copy
import json
from typing import Any

from lingtai.kernel.llm.interface import (
    ContentBlock,
    ChatInterface,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)


_RUNTIME_ROOTS = frozenset({"tool_meta", "agent_meta"})
_HISTORICAL_ROOTS = frozenset({
    "tool_meta", "agent_meta", "notifications", "notification_persistent",
    "guidance", "notification_guidance",
})


def _normalize_runtime_envelope(raw: Any) -> dict | None:
    """Normalize runtime ownership without classifying business ``_meta``."""
    if not isinstance(raw, dict) or not set(raw).intersection(_HISTORICAL_ROOTS):
        return None
    tool_meta = copy.deepcopy(raw.get("tool_meta")) if isinstance(raw.get("tool_meta"), dict) else {}
    old_agent = raw.get("agent_meta")
    agent = copy.deepcopy(old_agent) if isinstance(old_agent, dict) else {}
    has_agent_axis = bool(agent) or any(k in raw for k in (
        "agent_meta", "notifications", "notification_persistent", "guidance", "notification_guidance"
    ))
    agent_state = agent.get("agent_state")
    if not isinstance(agent_state, dict):
        agent_state = {}
    # Historical kernel envelopes put state in tool_meta and/or directly in
    # agent_meta. Move ownership only in the projected copy; canonical history
    # remains byte/value-equivalent.
    for key in ("current_time", "token_usage", "context"):
        if key in tool_meta:
            agent_state.setdefault(key, copy.deepcopy(tool_meta.pop(key)))
    for key in ("current_tool_result_chars", "adapter_comment", "active_turn_tool_calls"):
        if key in agent and key not in {"agent_state", "notifications", "guidance", "instruction"}:
            agent_state.setdefault(key, copy.deepcopy(agent.pop(key)))
    if "reconstruction" in tool_meta:
        agent_state.setdefault("events", {})["reconstruction"] = copy.deepcopy(tool_meta.pop("reconstruction"))
    if not has_agent_axis:
        return {"tool_meta": tool_meta}
    agent["agent_state"] = agent_state
    agent.setdefault("instruction", "Only the latest agent_meta in conversation is current; older ones are historical traces.")
    daemon_local_state = isinstance(agent_state.get("daemon"), dict)
    has_notification_axis = (
        "notifications" in agent
        or "notifications" in raw
        or "notification_persistent" in raw
    )
    if daemon_local_state and not has_notification_axis:
        # Daemon agent_meta deliberately carries no parent-only notification or
        # communication axis. Preserve that omission through provider projection
        # instead of manufacturing the main-agent's empty compatibility object.
        agent.pop("notifications", None)
    else:
        notifications = agent.get("notifications")
        if not isinstance(notifications, dict):
            notifications = {}
        if "notifications" in raw:
            notifications.setdefault("attention", copy.deepcopy(raw["notifications"]))
        if "notification_persistent" in raw:
            notifications.setdefault("persistent", copy.deepcopy(raw["notification_persistent"]))
        agent["notifications"] = notifications
    has_guidance_axis = (
        "guidance" in agent
        or "guidance" in raw
        or "notification_guidance" in raw
    )
    if daemon_local_state and not has_guidance_axis:
        # A daemon does not carry the main agent's resident meta_guidance section,
        # so an absent guidance axis must stay absent rather than point nowhere.
        agent.pop("guidance", None)
    else:
        guidance = agent.get("guidance")
        if not isinstance(guidance, dict):
            guidance = {}
        if "guidance" in raw:
            guidance.setdefault("persistent", copy.deepcopy(raw["guidance"]))
        if "notification_guidance" in raw:
            guidance.setdefault("transient", copy.deepcopy(raw["notification_guidance"]))
        agent["guidance"] = guidance
    return {"tool_meta": tool_meta, "agent_meta": agent}


def _project_tool_result(block: ToolResultBlock) -> Any:
    """Project runtime metadata without merging handler-owned ``_meta``."""
    if isinstance(block.content, dict):
        projected = dict(block.content)
        has_business_meta = "_meta" in projected
        business_meta = projected.get("_meta")
        envelope = _normalize_runtime_envelope(business_meta)
        if isinstance(block.metadata, dict) and block.metadata:
            envelope = _normalize_runtime_envelope(block.metadata) or {}
            if has_business_meta:
                # Keep the handler dictionary intact inside the established
                # result sidecar shape; otherwise its own ``_meta`` would be
                # mistaken for the runtime envelope and overwritten.
                return {"result": block.content, "_meta": envelope}
            projected.pop("_meta", None)
        elif envelope is not None:
            projected.pop("_meta", None)
        if business_meta is not None or envelope is not None:
            if envelope is not None:
                projected["_meta"] = envelope
        return projected
    if block.metadata:
        envelope = _normalize_runtime_envelope(block.metadata)
        return {"result": block.content, "_meta": envelope}
    return block.content


def _restore_projected_result(value: Any) -> tuple[Any, dict]:
    """Recover a sidecar when a provider round-trip returns the projected JSON."""
    if isinstance(value, dict) and isinstance(value.get("_meta"), dict):
        body = dict(value)
        metadata = _normalize_runtime_envelope(body.get("_meta"))
        if metadata is None:
            return value, {}
        body.pop("_meta")
        if set(body) == {"result"}:
            return body["result"], metadata
        return body, metadata
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value, {}
        if isinstance(parsed, dict) and isinstance(parsed.get("_meta"), dict):
            body = dict(parsed)
            metadata = _normalize_runtime_envelope(body.get("_meta"))
            if metadata is None:
                return value, {}
            body.pop("_meta")
            if set(body) == {"result"}:
                return body["result"], metadata
            return body, metadata
    return value, {}


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def to_anthropic(iface: ChatInterface) -> list[dict]:
    """Convert canonical interface to Anthropic message list.
    System entries excluded (Anthropic passes system separately).
    Every historical ``ToolResultBlock``'s content is serialized as-is,
    including any ``_meta.agent_meta`` / ``guidance`` / ``notifications`` /
    ``notification_guidance`` it carries — full-history conversion does not
    strip these keys from any holder. String content passes through
    unchanged; dict content is re-serialized to JSON (equal, not
    byte-identical). A ``summarize``-replaced body is the only historical
    tool-result body a rebuild replaces (see ``lingtai.kernel.meta_block``
    for the latest-holder-is-current-state reading rule).
    """
    messages: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            continue
        if entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                blocks = [_to_anthropic_block(b) for b in entry.content]
                messages.append({"role": "user", "content": blocks})
            elif len(entry.content) == 1 and isinstance(entry.content[0], TextBlock):
                messages.append({"role": "user", "content": entry.content[0].text})
            else:
                messages.append({"role": "user", "content": [_to_anthropic_block(b) for b in entry.content]})
        elif entry.role == "assistant":
            messages.append({"role": "assistant", "content": [_to_anthropic_block(b) for b in entry.content]})
    return messages


def _to_anthropic_block(block: ContentBlock) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    elif isinstance(block, ToolCallBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.args}
    elif isinstance(block, ToolResultBlock):
        content = _project_tool_result(block)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": content if isinstance(content, str) else json.dumps(content, default=str),
        }
    elif isinstance(block, ThinkingBlock):
        d: dict = {"type": "thinking", "thinking": block.text}
        sig = block.provider_data.get("anthropic", {}).get("signature")
        if sig:
            d["signature"] = sig
        return d
    raise ValueError(f"Unknown block type: {type(block)}")


def from_anthropic(messages: list[dict], system_prompt: str | None = None) -> ChatInterface:
    """Convert Anthropic message list to canonical interface."""
    iface = ChatInterface()
    if system_prompt:
        iface.add_system(system_prompt)
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        if role == "user":
            if isinstance(content, str):
                iface.add_user_message(content)
            elif isinstance(content, list):
                if all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    iface.add_tool_results([_from_anthropic_tool_result(b) for b in content])
                else:
                    blocks = [_from_anthropic_block(b) for b in content]
                    iface.add_user_blocks(blocks)
        elif role == "assistant":
            if isinstance(content, str):
                iface.add_assistant_message([TextBlock(text=content)])
            elif isinstance(content, list):
                iface.add_assistant_message([_from_anthropic_block(b) for b in content])
    return iface


def _from_anthropic_tool_result(b: dict) -> ToolResultBlock:
    content, metadata = _restore_projected_result(b.get("content", ""))
    return ToolResultBlock(id=b["tool_use_id"], name=b.get("name", ""), content=content, metadata=metadata)


def _from_anthropic_block(b: dict) -> ContentBlock:
    btype = b.get("type", "")
    if btype == "text":
        return TextBlock(text=b["text"])
    elif btype == "tool_use":
        return ToolCallBlock(id=b["id"], name=b["name"], args=b.get("input", {}))
    elif btype == "tool_result":
        return _from_anthropic_tool_result(b)
    elif btype == "thinking":
        pd = {}
        sig = b.get("signature")
        if sig:
            pd = {"anthropic": {"signature": sig}}
        return ThinkingBlock(text=b.get("thinking", ""), provider_data=pd)
    return TextBlock(text=str(b))


# ---------------------------------------------------------------------------
# OpenAI (Chat Completions)
# ---------------------------------------------------------------------------


def to_openai(iface: ChatInterface) -> list[dict]:
    """Convert canonical interface to OpenAI Chat Completions message list.
    System entries become role=system.  Tool results become separate role=tool
    messages.  Every historical tool result's content is serialized as-is,
    including any ``_meta.agent_meta``/``guidance``/``notifications``/
    ``notification_guidance`` it carries — full-history conversion does not
    strip these keys from any holder. String content passes through
    unchanged; dict content is re-serialized to JSON (equal, not
    byte-identical). A ``summarize``-replaced body is the only historical
    tool-result body a rebuild replaces.
    """
    messages: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            messages.append({"role": "system", "content": entry.content[0].text})
        elif entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                for block in entry.content:
                    if isinstance(block, ToolResultBlock):
                        content = _project_tool_result(block)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": block.id,
                            "content": content if isinstance(content, str) else json.dumps(content, default=str),
                        })
            elif len(entry.content) == 1 and isinstance(entry.content[0], TextBlock):
                messages.append({"role": "user", "content": entry.content[0].text})
            else:
                messages.append({"role": "user", "content": [_to_openai_block(b) for b in entry.content]})
        elif entry.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant"}
            text_parts, tool_calls, thinking_parts = [], [], []
            for block in entry.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolCallBlock):
                    tool_calls.append({
                        "id": block.id, "type": "function",
                        "function": {"name": block.name, "arguments": json.dumps(block.args)},
                    })
                elif isinstance(block, ThinkingBlock):
                    if block.text:
                        thinking_parts.append(block.text)
            if text_parts:
                msg["content"] = "\n".join(text_parts)
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if not text_parts and not tool_calls:
                msg["content"] = ""
            # Real reasoning_content if captured. DeepSeek's thinking-mode
            # contract requires this on every assistant turn after the first
            # tool_call; other OpenAI-compat providers ignore the field.
            # Preserving the real text (instead of a byte-identical placeholder)
            # avoids DeepSeek's cache fast-path collapsing onto the placeholder
            # and emitting empty responses. See lingtai-kernel issue #9.
            if thinking_parts:
                msg["reasoning_content"] = "\n".join(thinking_parts)
            messages.append(msg)
    return messages


def _to_openai_block(block: ContentBlock) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    return {"type": "text", "text": str(block)}


# ---------------------------------------------------------------------------
# OpenAI Responses API (input items)
# ---------------------------------------------------------------------------


_RESPONSES_ORPHAN_OUTPUT_PLACEHOLDER = (
    "[synthesized placeholder — real tool result was not in context at send time]"
)


def _pair_responses_orphan_function_calls(items: list[dict]) -> list[dict]:
    """Wire-layer guard for the Responses API input list.

    Walks the list and, for any ``function_call`` item whose ``call_id``
    has no matching ``function_call_output`` item (in any position),
    appends a synthesized ``function_call_output`` placeholder at the END
    of the list. The placeholders are emitted as one contiguous tail block,
    in ``function_call`` order, so the serialization stays stable across
    continuation turns (see the in-body comment for why tail placement beats
    interleaving). The canonical interface is not mutated — this repair is
    local to the serialization and re-runs on the next send.

    Mirrors :func:`lingtai.llm.openai.adapter.OpenAIChatSession._pair_orphan_tool_calls`
    which provides the same guarantee for OpenAI Chat Completions. The
    Responses API rejects an input that carries a ``function_call`` with
    no matching ``function_call_output`` with the 400 error
    ``"No tool output found for function call …"`` (issue #170). The
    guard exists so that a half-committed tool loop — typically caused by
    a continuation send that failed AFTER local tool execution and was
    rolled back by the adapter, or a session restored from disk
    mid-tool-loop — does not brick the next continuation request.
    """
    # Collect every ``function_call_output.call_id`` already present in the
    # list.  Position doesn't matter for the Responses API — strict
    # adjacency is only enforced by Chat Completions ``role=tool`` runs.
    output_ids: set[str] = {
        it["call_id"]
        for it in items
        if it.get("type") == "function_call_output" and it.get("call_id")
    }
    # Append synthesized placeholders at the END of the list, after every real
    # item, rather than interleaving each one immediately after its
    # ``function_call``. The Responses API does not require adjacency, so the tail
    # position is equally valid — and it keeps the serialization STABLE across
    # continuation turns. ``to_responses_input`` already emits all of an assistant
    # entry's ``function_call``s contiguously and all real
    # ``function_call_output``s afterwards, so when a multi-call turn resolves
    # incrementally the real outputs land in a fixed order. Interleaving each
    # placeholder right after its call instead made the placeholder positions
    # drift relative to where the real outputs eventually appear, which broke the
    # Codex strict-prefix continuation and forced a ``*_full`` request every turn
    # (the observed ``prefix_mismatch`` with ``function_call_output`` vs
    # ``function_call``). Placing placeholders contiguously at the tail lets the
    # baseline recorder strip them as one block and keeps the real prefix stable.
    patched: list[dict] = list(items)
    seen: set[str] = set(output_ids)
    for item in items:
        if item.get("type") != "function_call":
            continue
        call_id = item.get("call_id")
        if not call_id or call_id in seen:
            continue
        patched.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": _RESPONSES_ORPHAN_OUTPUT_PLACEHOLDER,
            }
        )
        seen.add(call_id)
    return patched


def to_responses_input(iface: ChatInterface) -> list[dict]:
    """Convert canonical interface to OpenAI Responses API ``input`` items.

    System entries are excluded (the Responses API takes the system prompt
    via the ``instructions`` kwarg, not as an input item).

    Item shapes per the Responses API:
      * user text       -> ``{"role": "user", "content": <str>}``
      * assistant text  -> ``{"role": "assistant", "content": <str>}``
      * assistant call  -> ``{"type": "function_call", "call_id", "name", "arguments": <json-str>}``
      * assistant thought -> ``{"type": "reasoning", "summary": [{"type": "summary_text", "text": <str>}]}``
      * tool result     -> ``{"type": "function_call_output", "call_id", "output": <str>}``

    Used by stateless Responses sessions (e.g. Codex) that must replay the
    full conversation each turn instead of relying on ``previous_response_id``.

    Before returning, the wire-layer guard
    :func:`_pair_responses_orphan_function_calls` synthesizes a
    placeholder ``function_call_output`` for every ``function_call``
    without a matching output. This prevents the provider's 400
    ``"No tool output found for function call …"`` rejection when the
    canonical history carries a tool_call whose result was lost — for
    example after a continuation send that failed AFTER local tool
    execution and was rolled back by the adapter (issue #170).

    Every historical tool result's content is serialized as-is, including any
    ``_meta.agent_meta`` / ``guidance`` / ``notifications`` /
    ``notification_guidance`` it carries — full-history conversion does not
    strip these keys from any holder. String content passes through
    unchanged; dict content is re-serialized to JSON (equal, not
    byte-identical). A ``summarize``-replaced body is the only historical
    tool-result body a rebuild replaces. On the Codex WS path the
    per-``call_id`` freeze (``lingtai.llm.openai.adapter._freeze_responses_outputs``)
    keeps already-sent outputs byte-identical STRINGS within an epoch for
    reasons unrelated to this preservation (in-place canonical rewrites such
    as summarize marker/status flips); a fresh replay after an epoch reset
    re-serializes through this converter, which still emits every historical
    holder's content.
    """
    items: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            continue
        if entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                for block in entry.content:
                    if isinstance(block, ToolResultBlock):
                        content = _project_tool_result(block)
                        output = (
                            content
                            if isinstance(content, str)
                            else json.dumps(content, default=str)
                        )
                        items.append({
                            "type": "function_call_output",
                            "call_id": block.id,
                            "output": output,
                        })
            else:
                text_parts = [
                    b.text for b in entry.content if isinstance(b, TextBlock)
                ]
                items.append({
                    "role": "user",
                    "content": "\n".join(text_parts) if text_parts else "",
                })
        elif entry.role == "assistant":
            text_parts: list[str] = []
            reasoning_items: list[dict] = []
            tool_calls: list[dict] = []
            for block in entry.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ThinkingBlock):
                    raw_item = block.provider_data.get("openai_responses_reasoning_item")
                    encrypted_content = (
                        raw_item.get("encrypted_content")
                        if isinstance(raw_item, dict) else None
                    )
                    if (
                        isinstance(raw_item, dict)
                        and raw_item.get("type") == "reasoning"
                        and isinstance(encrypted_content, str)
                        and encrypted_content != "<REDACTED:secret>"
                    ):
                        # The OpenAI SDK/request pipeline may normalize or mutate
                        # request dictionaries. Replay a deep copy so the
                        # persisted provider_data raw reasoning state remains an
                        # immutable cache anchor across turns. If durable history
                        # redacted the opaque provider blob, fall back to summary_text.
                        reasoning_items.append(copy.deepcopy(raw_item))
                    elif block.text:
                        reasoning_items.append({
                            "type": "reasoning",
                            "summary": [
                                {"type": "summary_text", "text": block.text},
                            ],
                        })
                elif isinstance(block, ToolCallBlock):
                    tool_calls.append({
                        "type": "function_call",
                        "call_id": block.id,
                        "name": block.name,
                        "arguments": json.dumps(block.args),
                    })
            # Preserve the model's original output order: reasoning first,
            # visible assistant text second, tool calls last.  Responses API
            # output reasoning items may carry encrypted state when replaying
            # byte-identical API output, but the input schema also accepts
            # summary_text-only reasoning items for manually managed context.
            items.extend(reasoning_items)
            if text_parts:
                joined = "\n".join(text_parts)
                if joined:
                    items.append({"role": "assistant", "content": joined})
            items.extend(tool_calls)
    return _pair_responses_orphan_function_calls(items)


# ---------------------------------------------------------------------------
# Gemini (Interactions API TurnParam format)
# ---------------------------------------------------------------------------


def to_gemini(iface: ChatInterface) -> list[dict]:
    """Convert canonical interface to Gemini Interactions TurnParam list.
    System entries excluded (Gemini uses system_instruction parameter).
    Every historical tool result's content is serialized as-is, including any
    ``_meta.agent_meta`` / ``guidance`` / ``notifications`` /
    ``notification_guidance`` it carries — full-history conversion does not
    strip these keys from any holder. String content passes through
    unchanged; dict content is re-serialized to JSON (equal, not
    byte-identical). A ``summarize``-replaced body is the only historical
    tool-result body a rebuild replaces.
    """
    turns: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            continue
        role = "model" if entry.role == "assistant" else "user"
        turns.append({"role": role, "content": [_to_gemini_block(b) for b in entry.content]})
    return turns


def _to_gemini_block(block: ContentBlock) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    elif isinstance(block, ToolCallBlock):
        return {"type": "function_call", "id": block.id, "name": block.name, "arguments": block.args}
    elif isinstance(block, ToolResultBlock):
        content = _project_tool_result(block)
        return {
            "type": "function_result",
            "call_id": block.id,
            "result": content if isinstance(content, str) else json.dumps(content, default=str),
            "name": block.name,
        }
    elif isinstance(block, ThinkingBlock):
        d: dict = {"type": "thought"}
        if block.text:
            d["summary"] = [{"type": "text", "text": block.text}]
        return d
    return {"type": "text", "text": str(block)}


def from_gemini(turns: list[dict], system_prompt: str | None = None) -> ChatInterface:
    """Convert Gemini TurnParam list to canonical interface."""
    iface = ChatInterface()
    if system_prompt:
        iface.add_system(system_prompt)
    for turn in turns:
        role = turn.get("role", "user")
        blocks = [_from_gemini_block(c) for c in turn.get("content", [])]
        if role == "model":
            iface.add_assistant_message(blocks)
        else:
            if blocks and isinstance(blocks[0], ToolResultBlock):
                iface.add_tool_results([b for b in blocks if isinstance(b, ToolResultBlock)])
            elif len(blocks) == 1 and isinstance(blocks[0], TextBlock):
                iface.add_user_message(blocks[0].text)
            else:
                iface.add_user_blocks(blocks)
    return iface


def _from_gemini_block(b: dict) -> ContentBlock:
    btype = b.get("type", "")
    if btype == "text":
        return TextBlock(text=b["text"])
    elif btype == "function_call":
        return ToolCallBlock(id=b.get("id", ""), name=b["name"], args=b.get("arguments", {}))
    elif btype == "function_result":
        content, metadata = _restore_projected_result(b.get("result", ""))
        return ToolResultBlock(id=b.get("call_id", ""), name=b.get("name", ""), content=content, metadata=metadata)
    elif btype == "thought":
        text = ""
        for s in b.get("summary", []):
            if s.get("type") == "text":
                text = s.get("text", "")
                break
        return ThinkingBlock(text=text)
    return TextBlock(text=str(b))
