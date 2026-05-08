"""Converters between canonical ChatInterface and provider-specific formats.

Naming convention:
- to_<provider>(iface) -> provider message list
- from_<provider>(messages, ...) -> ChatInterface
"""

from __future__ import annotations

import json
from typing import Any

from lingtai_kernel.llm.interface import (
    ContentBlock,
    ChatInterface,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def to_anthropic(iface: ChatInterface) -> list[dict]:
    """Convert canonical interface to Anthropic message list.
    System entries excluded (Anthropic passes system separately).
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
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": block.content if isinstance(block.content, str) else json.dumps(block.content, default=str),
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
    return ToolResultBlock(id=b["tool_use_id"], name=b.get("name", ""), content=b.get("content", ""))


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
    System entries become role=system.  Tool results become separate role=tool messages.
    """
    messages: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            messages.append({"role": "system", "content": entry.content[0].text})
        elif entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                for block in entry.content:
                    if isinstance(block, ToolResultBlock):
                        messages.append({
                            "role": "tool",
                            "tool_call_id": block.id,
                            "content": block.content if isinstance(block.content, str) else json.dumps(block.content, default=str),
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


def to_responses_input(iface: ChatInterface) -> list[dict]:
    """Convert canonical interface to OpenAI Responses API ``input`` items.

    System entries are excluded (the Responses API takes the system prompt
    via the ``instructions`` kwarg, not as an input item).

    Item shapes per the Responses API:
      * user text       -> ``{"role": "user", "content": <str>}``
      * assistant text  -> ``{"role": "assistant", "content": <str>}``
      * assistant call  -> ``{"type": "function_call", "call_id", "name", "arguments": <json-str>}``
      * tool result     -> ``{"type": "function_call_output", "call_id", "output": <str>}``

    Used by stateless Responses sessions (e.g. Codex) that must replay the
    full conversation each turn instead of relying on ``previous_response_id``.
    """
    items: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            continue
        if entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                for block in entry.content:
                    if isinstance(block, ToolResultBlock):
                        output = (
                            block.content
                            if isinstance(block.content, str)
                            else json.dumps(block.content, default=str)
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
            tool_calls: list[dict] = []
            for block in entry.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolCallBlock):
                    tool_calls.append({
                        "type": "function_call",
                        "call_id": block.id,
                        "name": block.name,
                        "arguments": json.dumps(block.args),
                    })
                # ThinkingBlocks dropped: the Responses API expects encrypted
                # reasoning items, which we don't carry through the canonical
                # interface. Stateless replay simply omits past reasoning.
            if text_parts:
                joined = "\n".join(text_parts)
                if joined:
                    items.append({"role": "assistant", "content": joined})
            items.extend(tool_calls)
    return items

