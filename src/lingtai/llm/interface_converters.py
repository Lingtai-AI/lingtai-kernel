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


# ---------------------------------------------------------------------------
# Timely transient ``_meta`` filtering (shared model-facing serialization)
# ---------------------------------------------------------------------------


# The four timely transient ``_meta`` blocks (kernel names, see
# ``lingtai.kernel.meta_block``), grouped by the family that moves together:
# sparse/update-driven current-state hints whose old copies are historical
# traces, not current state. Canonical history keeps them (no retroactive
# strip — Jason #4307); model-facing full-history serialization instead
# presents only the NEWEST occurrence per family and omits the stale copies,
# without rewriting recorded history. The durable delta-lane
# ``notification_persistent`` blocks (Telegram/WeChat/Feishu ``previous_block``
# continuity) are deliberately NOT listed here — see
# ``newest_email_snapshot_holder`` below for the separate, narrower
# whole-snapshot filtering that applies only to
# ``notification_persistent.email``.
TIMELY_TRANSIENT_META_FAMILIES: dict[str, tuple[str, ...]] = {
    "agent_meta": ("agent_meta", "guidance"),
    "notifications": ("notifications", "notification_guidance"),
}


def _timely_transient_families(content: Any) -> tuple[str, ...]:
    """Timely transient families present in a ``ToolResultBlock.content`` value.

    Accepts the canonical content shapes — a dict or a JSON string; anything
    else (unparseable JSON, non-dict JSON, no dict ``_meta``) carries no
    families.
    """
    if isinstance(content, str):
        if "_meta" not in content:
            return ()
        try:
            content = json.loads(content)
        except ValueError:
            return ()
    if not isinstance(content, dict):
        return ()
    meta = content.get("_meta")
    if not isinstance(meta, dict):
        return ()
    return tuple(
        family
        for family, keys in TIMELY_TRANSIENT_META_FAMILIES.items()
        if any(key in meta for key in keys)
    )


def timely_transient_newest_holders(
    iface: ChatInterface,
) -> dict[str, ToolResultBlock]:
    """Newest canonical ``ToolResultBlock`` per timely transient family.

    Walks ``iface.entries`` in order, so the LAST block carrying a family's
    keys wins — that occurrence is current state; every earlier one is a
    historical trace that model-facing serialization omits.
    """
    newest: dict[str, ToolResultBlock] = {}
    for entry in iface.entries:
        for block in entry.content or []:
            if isinstance(block, ToolResultBlock):
                for family in _timely_transient_families(block.content):
                    newest[family] = block
    return newest


def _load_meta(content: Any) -> tuple[dict | None, dict | None, bool]:
    """Parse ``content`` into ``(parsed, meta, was_str)``, or ``(None, None, was_str)``.

    Shared by :func:`filter_stale_timely_transient`,
    :func:`newest_email_snapshot_holder`, and :func:`_drop_stale_email_snapshot`
    so all three stay lenient about the same canonical content shapes (dict or
    JSON string; anything unparseable or non-dict yields no ``_meta``).
    """
    was_str = isinstance(content, str)
    if was_str:
        if "_meta" not in content:
            return None, None, was_str
        try:
            parsed = json.loads(content)
        except ValueError:
            return None, None, was_str
    else:
        parsed = content
    if not isinstance(parsed, dict):
        return None, None, was_str
    meta = parsed.get("_meta")
    if not isinstance(meta, dict):
        return None, None, was_str
    return parsed, meta, was_str


_EMAIL_NOT_APPLICABLE = object()  # sentinel: caller opted out of email projection entirely


def _filter_stale_transient_core(
    block: ToolResultBlock,
    newest: dict[str, ToolResultBlock],
    newest_email_snapshot,
) -> Any:
    """Shared core for both the public and internal full-history filters.

    ``newest_email_snapshot`` is either ``_EMAIL_NOT_APPLICABLE`` (the public
    two-argument :func:`filter_stale_timely_transient` — never touches
    ``notification_persistent.email`` at all) or the actual
    :func:`newest_email_snapshot_holder` result, including a legitimate
    ``None`` meaning "no email snapshot anywhere in this history" (the
    internal :func:`_render_full_history_result`).
    """
    content = block.content
    stale_keys = tuple(
        key
        for family, keys in TIMELY_TRANSIENT_META_FAMILIES.items()
        if newest.get(family) is not block
        for key in keys
    )
    parsed, meta, was_str = _load_meta(content)
    if parsed is None:
        return content
    has_stale_family_keys = any(key in meta for key in stale_keys)
    new_meta = meta
    if has_stale_family_keys:
        new_meta = {key: value for key, value in meta.items() if key not in stale_keys}
    email_changed = False
    if newest_email_snapshot is not _EMAIL_NOT_APPLICABLE:
        new_meta, email_changed = _drop_stale_email_snapshot(
            new_meta, block, newest_email_snapshot
        )
    if not has_stale_family_keys and not email_changed:
        return content
    filtered = dict(parsed)
    if new_meta:
        filtered["_meta"] = new_meta
    else:
        filtered.pop("_meta")
    return json.dumps(filtered, default=str) if was_str else filtered


def filter_stale_timely_transient(
    block: ToolResultBlock, newest: dict[str, ToolResultBlock]
) -> Any:
    """Return ``block.content`` with stale timely transient ``_meta`` keys removed.

    This is the ESTABLISHED two-argument public helper — unchanged signature
    and behavior for any existing caller (in-tree or not): it filters ONLY
    the timely-transient families (``agent_meta``/``guidance``,
    ``notifications``/``notification_guidance``) and never touches
    ``notification_persistent`` at all, exactly as before the email
    whole-snapshot feature existed.

    ``newest`` is the map from :func:`timely_transient_newest_holders` computed
    over the SAME full history the caller is serializing. A family's keys
    survive only on that family's newest holder (compared by object identity);
    every older copy is omitted. If ``_meta`` carried only removed keys, the
    now-empty envelope is omitted too.

    Non-mutating by construction: canonical ``ToolResultBlock.content`` /
    ``ChatInterface`` entries / durable history are never touched — string
    content is parsed into a fresh object, dict content is copied at the
    rewritten levels. When there is nothing to remove the ORIGINAL content
    object is returned unchanged, so unaffected results stay byte-identical
    across re-serializations (summary markers, ``tool_meta``, delta-lane
    ``notification_persistent`` blocks, and ordinary payloads pass through).

    The five in-tree model-facing full-history renderers (``to_anthropic``,
    ``to_openai``, ``to_responses_input``, ``to_gemini``, and Claude Code's
    ``_render_conversation``) do NOT call this function directly — they call
    the internal :func:`_render_full_history_result`, which additionally
    projects the email whole-snapshot state. This keeps the established
    public two-argument contract intact for any other caller while still
    giving those five renderers no way to silently skip the email filter.
    """
    return _filter_stale_transient_core(block, newest, _EMAIL_NOT_APPLICABLE)


def _render_full_history_result(
    block: ToolResultBlock,
    newest: dict[str, ToolResultBlock],
    newest_email_snapshot: ToolResultBlock | None,
) -> Any:
    """Internal full-history rendering primitive — the ONLY entry point the
    five model-facing full-history renderers use.

    Applies both stale-copy filters a full-history render must apply: the
    timely-transient family strip (same rule as the public
    :func:`filter_stale_timely_transient`) AND the email whole-snapshot
    projection (:func:`_drop_stale_email_snapshot`, keyed off
    ``newest_email_snapshot`` — see :func:`newest_email_snapshot_holder`).

    This is deliberately a DISTINCT internal function rather than an optional
    third argument on the public helper: every one of the five renderers
    below calls this directly, so there is no silent per-renderer bypass, but
    the public ``filter_stale_timely_transient(block, newest)`` two-argument
    contract remains exactly what it was before email whole-snapshot
    filtering existed — restoring compatibility for any caller outside this
    module (see Terra repair-v2 review, blocker 3).

    Non-mutating by construction, same guarantees as
    :func:`filter_stale_timely_transient`.
    """
    return _filter_stale_transient_core(block, newest, newest_email_snapshot)


# ---------------------------------------------------------------------------
# ``notification_persistent.email`` whole-snapshot filtering (shared
# model-facing serialization)
# ---------------------------------------------------------------------------
#
# Email is a producer-owned ATOMIC snapshot lane (see
# ``lingtai.kernel.meta_block`` / ``LICC_NOTIFICATION_CONTRACT.md``): every
# stamped ``notification_persistent.email`` child is the producer's entire
# current unread state (or an explicit ``{"cleared": True, ...}`` tombstone
# once unread count reaches zero), never an incremental/independent set of
# per-id records. Correlated fields (``count``, ``newest_received_at``,
# ``context_comment``, ``email_ids``, ``emails``) describe ONE snapshot as a
# whole and must never be spliced against a different snapshot. Full-history
# replay must therefore keep the newest whole child intact and remove every
# older child in full — never merge/select individual ids/fields across
# snapshots.


def newest_email_snapshot_holder(iface: ChatInterface) -> ToolResultBlock | None:
    """The single ``ToolResultBlock`` holding the newest authoritative email state.

    Walks ``iface.entries`` in wire order and remembers the LAST block whose
    ``_meta.notification_persistent.email`` is a dict — whether that dict is a
    live nonempty snapshot or an explicit clear tombstone
    (``{"cleared": True, ...}``). Only that last occurrence is authoritative;
    every earlier block (nonempty or clear) is superseded and must lose its
    entire ``.email`` child in full-history replay, whole-block, never
    per-id. Returns ``None`` when no block in the history carries an email
    snapshot at all — callers must then leave every block's (nonexistent)
    email child untouched.

    Guards every intermediate value with ``isinstance`` so a malformed
    ``notification_persistent`` (``None``, a string, a list) or malformed
    ``email`` value is simply skipped rather than raising.
    """
    newest: ToolResultBlock | None = None
    for entry in iface.entries:
        for block in entry.content or []:
            if not isinstance(block, ToolResultBlock):
                continue
            _, meta, _ = _load_meta(block.content)
            if meta is None:
                continue
            persistent = meta.get("notification_persistent")
            if not isinstance(persistent, dict):
                continue
            email = persistent.get("email")
            if isinstance(email, dict):
                newest = block
    return newest


def _drop_stale_email_snapshot(
    meta: dict,
    block: ToolResultBlock,
    newest_email_snapshot: ToolResultBlock | None,
) -> tuple[dict, bool]:
    """Return ``(meta, changed)`` with a stale whole email child removed.

    Removes the ENTIRE ``notification_persistent.email`` child (never a
    partial id/field subset) unless ``block`` IS
    ``newest_email_snapshot`` (compared by identity) — that block alone may
    keep its email state, whether a live snapshot or a clear tombstone. Only
    rewrites the ``notification_persistent``/``email`` sub-levels; sibling
    keys (``mcp`` delta lanes, other ``_meta`` blocks) stay the same objects.
    ``meta`` may be the original (unmutated) dict when nothing changes —
    callers must not assume a fresh copy came back.
    """
    persistent = meta.get("notification_persistent")
    if not isinstance(persistent, dict):
        return meta, False
    email = persistent.get("email")
    if not isinstance(email, dict):
        return meta, False
    if newest_email_snapshot is block:
        return meta, False

    new_persistent = {k: v for k, v in persistent.items() if k != "email"}
    new_meta = dict(meta)
    if new_persistent:
        new_meta["notification_persistent"] = new_persistent
    else:
        new_meta.pop("notification_persistent", None)
    return new_meta, True


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def to_anthropic(iface: ChatInterface) -> list[dict]:
    """Convert canonical interface to Anthropic message list.
    System entries excluded (Anthropic passes system separately).
    Stale timely transient ``_meta`` copies are omitted from tool results
    (newest per family kept) — see ``filter_stale_timely_transient``.
    """
    newest = timely_transient_newest_holders(iface)
    newest_email_snapshot = newest_email_snapshot_holder(iface)
    messages: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            continue
        if entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                blocks = [_to_anthropic_block(b, newest, newest_email_snapshot) for b in entry.content]
                messages.append({"role": "user", "content": blocks})
            elif len(entry.content) == 1 and isinstance(entry.content[0], TextBlock):
                messages.append({"role": "user", "content": entry.content[0].text})
            else:
                messages.append({"role": "user", "content": [_to_anthropic_block(b, newest, newest_email_snapshot) for b in entry.content]})
        elif entry.role == "assistant":
            messages.append({"role": "assistant", "content": [_to_anthropic_block(b, newest, newest_email_snapshot) for b in entry.content]})
    return messages


def _to_anthropic_block(
    block: ContentBlock,
    newest: dict[str, ToolResultBlock],
    newest_email_snapshot: ToolResultBlock | None,
) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    elif isinstance(block, ToolCallBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.args}
    elif isinstance(block, ToolResultBlock):
        content = _render_full_history_result(block, newest, newest_email_snapshot)
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
    Stale timely transient ``_meta`` copies are omitted from tool results
    (newest per family kept) — see ``filter_stale_timely_transient``.
    """
    newest = timely_transient_newest_holders(iface)
    newest_email_snapshot = newest_email_snapshot_holder(iface)
    messages: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            messages.append({"role": "system", "content": entry.content[0].text})
        elif entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                for block in entry.content:
                    if isinstance(block, ToolResultBlock):
                        content = _render_full_history_result(block, newest, newest_email_snapshot)
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

    Stale timely transient ``_meta`` copies are omitted from tool results
    (newest per family kept) — see ``filter_stale_timely_transient``. On the
    Codex WS path the per-``call_id`` freeze
    (``lingtai.llm.openai.adapter._freeze_responses_outputs``) keeps
    already-sent outputs byte-identical within an epoch; a fresh replay after
    an epoch reset re-serializes through this converter and so sheds the stale
    copies.
    """
    newest = timely_transient_newest_holders(iface)
    newest_email_snapshot = newest_email_snapshot_holder(iface)
    items: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            continue
        if entry.role == "user":
            if entry.content and isinstance(entry.content[0], ToolResultBlock):
                for block in entry.content:
                    if isinstance(block, ToolResultBlock):
                        content = _render_full_history_result(block, newest, newest_email_snapshot)
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
    Stale timely transient ``_meta`` copies are omitted from tool results
    (newest per family kept) — see ``filter_stale_timely_transient``.
    """
    newest = timely_transient_newest_holders(iface)
    newest_email_snapshot = newest_email_snapshot_holder(iface)
    turns: list[dict] = []
    for entry in iface.entries:
        if entry.role == "system":
            continue
        role = "model" if entry.role == "assistant" else "user"
        turns.append({"role": role, "content": [_to_gemini_block(b, newest, newest_email_snapshot) for b in entry.content]})
    return turns


def _to_gemini_block(
    block: ContentBlock,
    newest: dict[str, ToolResultBlock],
    newest_email_snapshot: ToolResultBlock | None,
) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    elif isinstance(block, ToolCallBlock):
        return {"type": "function_call", "id": block.id, "name": block.name, "arguments": block.args}
    elif isinstance(block, ToolResultBlock):
        content = _render_full_history_result(block, newest, newest_email_snapshot)
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
        return ToolResultBlock(id=b.get("call_id", ""), name=b.get("name", ""), content=b.get("result", ""))
    elif btype == "thought":
        text = ""
        for s in b.get("summary", []):
            if s.get("type") == "text":
                text = s.get("text", "")
                break
        return ThinkingBlock(text=text)
    return TextBlock(text=str(b))
