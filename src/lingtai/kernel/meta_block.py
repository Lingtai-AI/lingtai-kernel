"""Unified per-turn metadata injection.

Single source of truth for "what the agent sees about its own runtime state
on every turn." Both injection sites — text-input prefix (in BaseAgent) and
tool-result stamp (in ToolExecutor) — read from here.

Curate carefully: every field added to `build_meta` ships on every text input
and every tool result.

All four tool-result metadata blocks live under a single ``_meta`` envelope on
the result dict:

- ``_meta.tool_meta`` — permanent per-result identity facts, written once by
  ``ToolExecutor._attach_tool_block`` and never moved.
- ``_meta.agent_meta`` — SPARSE / update-driven agent/current-state snapshot.
  Attached to a tool result only when the *material* snapshot changes since the
  last emitted ``agent_meta`` (not re-stamped onto every latest result when
  unchanged).  Older emitted snapshots stay in history as update points.
- ``_meta.guidance`` — a lightweight ref/hook pointing at the resident
  ``meta_guidance`` system-prompt section (built by ``build_meta_guidance``),
  where the full kernel guidance sections, the ``_meta`` readme, and any static
  adapter runtime rules now live.  The full ordered appendix is no longer
  re-stamped on every tail result.  It rides with ``agent_meta`` and is
  attached/moved on the same sparse update cadence.
- ``_meta.notifications`` / ``_meta.notification_guidance`` — SPARSE /
  update-driven channel-owned notification payloads plus kernel safety framing.
  Attached on first appearance and re-attached only when the notification
  payload *materially* changes (or on a deliberate ``notification(action=check)``
  read) — NOT re-stamped onto every newest tool result when unchanged.  The
  prior holder keeps the payload as the current-state carrier between updates.

Channel encoding:
- Tool-result channel: ``stamp_meta`` records a per-tool runtime snapshot,
  which ``attach_active_runtime`` promotes into ``_meta.agent_meta`` plus
  ``_meta.guidance`` — but only when the material snapshot changed since the
  last emitted one (sparse; on no change nothing is attached/moved and the prior
  holder keeps its snapshot).  ``attach_active_notifications`` promotes the
  channel-owned notification payload into ``_meta.notifications`` /
  ``_meta.notification_guidance`` on the same sparse/update-driven cadence.
- Text-input channel: `render_meta` formats the same dict into a prose
  prefix line. Inbox content is NOT rendered here — it lives in the
  user-turn body, drained by ``_concat_queued_messages`` upstream.

As of 2026-05-02, the meta block no longer carries inbox-drained
notifications. System-source notifications (mail arrival, bounce, future
MCP events) are now delivered as synthetic notification(action="check")
tool-call pairs spliced by ``BaseAgent._inject_notification_pair`` (the
legacy ``tc_inbox`` splice path is dormant); see
docs/plans/2026-05-02-system-notification-as-tool-call.md.
"""
from __future__ import annotations

import hashlib as _hashlib
import json as _json
import time as _time
from collections.abc import Mapping
from typing import NamedTuple

from .config import (
    CONTEXT_PRESSURE_HIGH_RATIO,
    CONTEXT_PRESSURE_FORCED_REBUILD_RATIO,
    CONTEXT_PRESSURE_RECONSTRUCTION_RATIO,  # back-compat alias == FORCED_REBUILD_RATIO
    CONTEXT_PRESSURE_RECOVERY_TARGET,
)
from .i18n import t as _t
from .reminders.context_pressure import (
    current_molt_emission_descriptor,
    render_current_molt_context,
    render_forced_rebuild_failed_warning,
    render_forced_rebuild_warning,
    render_reconstruction_molt,
)
from .time_veil import now_iso

# ---------------------------------------------------------------------------
# The single ``_meta`` envelope key and its four nested blocks.  Every dict
# tool result carries ``result["_meta"]``; the blocks beneath it are:
#   * ``tool_meta``            — permanent, per-result (every tool result)
#   * ``agent_meta``           — sparse/update-driven agent/current state
#   * ``guidance``             — sparse/update-driven kernel guidance ref
#                                (rides with agent_meta)
#   * ``notifications`` +
#     ``notification_guidance``— sparse/update-driven channel payloads
# ---------------------------------------------------------------------------
META_ENVELOPE_KEY = "_meta"
TOOL_META_KEY = "tool_meta"
AGENT_META_KEY = "agent_meta"
GUIDANCE_KEY = "guidance"
NOTIFICATIONS_KEY = "notifications"
NOTIFICATION_GUIDANCE_KEY = "notification_guidance"
NOTIFICATION_PERSISTENT_KEY = "notification_persistent"
# Telegram lives under an `mcp` namespace level to mirror the ephemeral
# `notifications.mcp.telegram` shape and match Jason #6148: the required path is
# `_meta.notification_persistent.mcp.telegram` (NOT `...notification_persistent.telegram`).
NOTIFICATION_PERSISTENT_MCP_KEY = "mcp"
NOTIFICATION_PERSISTENT_TELEGRAM_CHANNEL = "telegram"
# Full dotted path used in hook comments / docs so both the string and the
# structure stay in sync.
NOTIFICATION_PERSISTENT_TELEGRAM_PATH = (
    f"_meta.{NOTIFICATION_PERSISTENT_KEY}."
    f"{NOTIFICATION_PERSISTENT_MCP_KEY}.{NOTIFICATION_PERSISTENT_TELEGRAM_CHANNEL}"
)
NOTIFICATION_PERSISTENT_TELEGRAM_MIN_CONTEXT = 20
NOTIFICATION_PERSISTENT_TELEGRAM_SEEN_LIMIT = 200

NOTIFICATION_PERSISTENT_EMAIL_CHANNEL = "email"
NOTIFICATION_PERSISTENT_EMAIL_PATH = (
    f"_meta.{NOTIFICATION_PERSISTENT_KEY}.{NOTIFICATION_PERSISTENT_EMAIL_CHANNEL}"
)

# WeChat mirrors the Telegram persistent lane at
# `_meta.notification_persistent.mcp.wechat`. Its producer preview window is
# 10 messages (vs Telegram's 20), so the seed/delta boundary matches that
# producer window instead of Telegram's.
NOTIFICATION_PERSISTENT_WECHAT_CHANNEL = "wechat"
NOTIFICATION_PERSISTENT_WECHAT_PATH = (
    f"_meta.{NOTIFICATION_PERSISTENT_KEY}."
    f"{NOTIFICATION_PERSISTENT_MCP_KEY}.{NOTIFICATION_PERSISTENT_WECHAT_CHANNEL}"
)
NOTIFICATION_PERSISTENT_WECHAT_MIN_CONTEXT = 10
NOTIFICATION_PERSISTENT_WECHAT_SEEN_LIMIT = 200

# Feishu mirrors the Telegram/WeChat persistent lane at
# `_meta.notification_persistent.mcp.feishu`. The Feishu producer's structured
# preview carries the last 10 conversation messages
# (FeishuManager._build_conversation_preview_and_metadata), so the seed/delta
# boundary matches that window rather than Telegram's 20.
NOTIFICATION_PERSISTENT_FEISHU_CHANNEL = "feishu"
NOTIFICATION_PERSISTENT_FEISHU_PATH = (
    f"_meta.{NOTIFICATION_PERSISTENT_KEY}."
    f"{NOTIFICATION_PERSISTENT_MCP_KEY}.{NOTIFICATION_PERSISTENT_FEISHU_CHANNEL}"
)
NOTIFICATION_PERSISTENT_FEISHU_MIN_CONTEXT = 10
NOTIFICATION_PERSISTENT_FEISHU_SEEN_LIMIT = 200

# WhatsApp lives at `_meta.notification_persistent.mcp.whatsapp` but runs the
# shared IM lane in snapshot mode (email-style): every block carries the
# producer's current bounded context in full, with no delivered-id delta
# tracking and no previous_block hook, so it has no min-context/seen-limit
# tuning knobs.
NOTIFICATION_PERSISTENT_WHATSAPP_CHANNEL = "whatsapp"
NOTIFICATION_PERSISTENT_WHATSAPP_PATH = (
    f"_meta.{NOTIFICATION_PERSISTENT_KEY}."
    f"{NOTIFICATION_PERSISTENT_MCP_KEY}.{NOTIFICATION_PERSISTENT_WHATSAPP_CHANNEL}"
)

# Concise English comments attached to the Telegram persistent block so the
# agent can read the block without re-deriving structure. Kept as module-level
# constants so tests and docs can assert the exact wording.
NOTIFICATION_PERSISTENT_TELEGRAM_BURST_COMMENT = (
    "Multiple new Telegram messages arrived together; treat them as one burst "
    "and answer the combined intent."
)
NOTIFICATION_PERSISTENT_TELEGRAM_SELF_OUTGOING_COMMENT = (
    "This is the agent's own recent outgoing message, included for continuity."
)
NOTIFICATION_PERSISTENT_TELEGRAM_TRUNCATED_COMMENT = (
    "This message is truncated; call telegram.read for the exact full producer "
    "state."
)
NOTIFICATION_PERSISTENT_TELEGRAM_REFERENCED_COMMENT = (
    "This is the full Telegram message referenced by the current reply; "
    "included because it is not present in messages."
)

# WeChat mirrors the Telegram comment set with channel-appropriate wording.
# The truncation comment points at wechat.read because the WeChat producer's
# local inbox/sent records are the exact source-of-truth state.
NOTIFICATION_PERSISTENT_WECHAT_BURST_COMMENT = (
    "Multiple new WeChat messages arrived together; treat them as one burst "
    "and answer the combined intent."
)
NOTIFICATION_PERSISTENT_WECHAT_SELF_OUTGOING_COMMENT = (
    "This is the agent's own recent outgoing message, included for continuity."
)
NOTIFICATION_PERSISTENT_WECHAT_TRUNCATED_COMMENT = (
    "This message is truncated; call wechat.read for the exact full producer "
    "state."
)

# Feishu mirrors the Telegram comment set with channel-appropriate wording.
# The truncation comment points at feishu.read because the Feishu producer's
# local store is the exact source-of-truth state.
NOTIFICATION_PERSISTENT_FEISHU_BURST_COMMENT = (
    "Multiple new Feishu messages arrived together; treat them as one burst "
    "and answer the combined intent."
)
NOTIFICATION_PERSISTENT_FEISHU_SELF_OUTGOING_COMMENT = (
    "This is the agent's own recent outgoing message, included for continuity."
)
NOTIFICATION_PERSISTENT_FEISHU_TRUNCATED_COMMENT = (
    "This message is truncated; call feishu.read for the exact full producer "
    "state."
)

# Concise English comments attached to the WhatsApp persistent block. The
# WhatsApp lane runs in snapshot mode (email-style): each block carries the
# producer's current structured context in full, with no delivered-id delta
# tracking, so the comments focus on producer authority and the Cloud API
# reply rules rather than block-to-block continuity.
NOTIFICATION_PERSISTENT_WHATSAPP_CONTEXT_COMMENT = (
    "Durable WhatsApp context moved here from _meta.notifications.mcp.whatsapp. "
    "The whatsapp tool remains the source of truth: building this block marks "
    "nothing read — use whatsapp.read/check for exact producer state. Reply on "
    "WhatsApp when the message arrived through WhatsApp (whatsapp.reply with "
    "the compound message id, or whatsapp.send); free-form business replies "
    "are allowed only inside the 24-hour customer-service window — outside it "
    "use an approved WhatsApp message template."
)
NOTIFICATION_PERSISTENT_WHATSAPP_SELF_OUTGOING_COMMENT = (
    "This is the agent's own recent outgoing message, included for continuity."
)
NOTIFICATION_PERSISTENT_WHATSAPP_TRUNCATED_COMMENT = (
    "This message is truncated; call whatsapp.read with the compound message "
    "id for the exact full producer state."
)
NOTIFICATION_PERSISTENT_WHATSAPP_MEDIA_COMMENT = (
    "Non-text WhatsApp message; only type/id metadata is stored locally — use "
    "whatsapp.read for the exact stored producer state."
)

NOTIFICATION_PERSISTENT_EMAIL_CONTEXT_COMMENT = (
    "Unread email content moved here from _meta.notifications.email. Bodies "
    "are injected in full up to the 50,000 character send-layer limit; prefer "
    "email.dismiss after handling content, and use email.read/reply for "
    "source-of-truth actions."
)
NOTIFICATION_PERSISTENT_EMAIL_TRUNCATED_COMMENT = (
    "This legacy email body exceeded the current 50,000 character send-layer "
    "limit and was capped in the persistent notification lane. New oversize "
    "email sends are rejected."
)

# Email is a snapshot lane (no `previous_block`): each stamped block is the
# producer's ENTIRE current unread state, not an increment. When unread count
# drops to zero the producer clears `.notification/email.json` and no new
# email payload is ever built again — without an explicit marker, the last
# nonempty snapshot would remain the only email evidence in history forever,
# and full-history replay would keep presenting it as current (see
# `lingtai.llm.interface_converters`). `NOTIFICATION_PERSISTENT_EMAIL_CLEARED_KEY`
# is that marker: a small durable tombstone stamped in place of the email
# child, carrying no message content, recording only that the snapshot ended.
NOTIFICATION_PERSISTENT_EMAIL_CLEARED_KEY = "cleared"
NOTIFICATION_PERSISTENT_EMAIL_CLEARED_AT_KEY = "cleared_at"

# Per-result machine-generated guidance nested under ``tool_meta``.  ``comment``
# is a small map of topic-keyed hints; today the only topic is ``overflow`` — a
# hint stamped on capped/large visible tool results pointing the agent at the
# preserved original and the cleanup action.  It is guidance, not a
# notification, not global guidance, and not a strict state machine: a quiet
# per-result note that rides on the permanent ``tool_meta`` block.
TOOL_META_COMMENT_KEY = "comment"
TOOL_META_COMMENT_OVERFLOW_KEY = "overflow"
TOOL_META_TOKEN_USAGE_KEY = "token_usage"
TOOL_META_TOKEN_USAGE_PENDING_KEY = "_tool_meta_token_usage"
# The two nested halves of ``tool_meta.token_usage``.  ``current_call`` carries
# ONLY this result's own provider-call token/cache/output fields; ``session``
# carries the since-last-molt cumulative aggregate (surviving refresh) plus the
# current context state.  Splitting them into named sub-objects (vs the former
# single flat dict) removes the confusing flat ``input`` vs ``input_tokens``
# adjacency — see :func:`build_tool_meta_token_usage`.
TOKEN_USAGE_CURRENT_CALL_KEY = "current_call"
TOKEN_USAGE_SESSION_KEY = "session"
TOOL_META_CURRENT_TIME_KEY = "current_time"
# Current sustained-pressure molt reminder — permanent per-result metadata at
# ``tool_meta.context.molt`` (moved here from the former sparse
# ``agent_meta.context.molt`` so the reminder persists on every result while the
# warning is active).  ``build_meta`` stashes the reminder under the transit key
# and carries the emission-event descriptor under the event transit key while
# active; ``ToolExecutor._attach_tool_block`` pops both — promoting the reminder
# into the permanent ``tool_meta.context`` block and logging with per-round dedup.
TOOL_META_CONTEXT_KEY = "context"
TOOL_META_CONTEXT_PENDING_KEY = "_tool_meta_context"
TOOL_META_CONTEXT_EVENT_PENDING_KEY = "_tool_meta_context_event"
TOOL_META_CONTEXT_REBUILD_KEY = "rebuild"

# Cache-miss budget guard — the two compact numeric fields surfaced under
# ``tool_meta.context`` alongside the ``molt`` warning when the current-session
# cache-miss total reaches/exceeds the configured budget (see
# :func:`build_cache_miss_budget_context`).  They ride the SAME
# ``_tool_meta_context`` transit sub-object as the sustained-pressure ``molt``
# reminder, so ``ToolExecutor._attach_tool_block`` promotes them into the
# permanent ``tool_meta.context`` block in one step.
TOOL_META_CONTEXT_CACHE_MISS_BUDGET_KEY = "cache_miss_budget"
TOOL_META_CONTEXT_CACHE_MISS_TOKENS_KEY = "cache_miss_tokens"

# Always-on since-last-molt cache-miss/budget telemetry surfaced inside the
# ``session`` (since-last-molt cumulative) half of ``tool_meta.token_usage`` (see
# :func:`_build_session_token_economy`).  Unlike the ``tool_meta.context`` guard
# above — which appears ONLY once the session cache-miss total reaches/exceeds
# the budget — these three fields ride on EVERY result whenever the session
# aggregate token usage is available, so an agent can always read its current
# cumulative cache miss and how much budget remains without recomputing
# ``input_tokens - cached_tokens`` or remembering the default budget:
#   * ``cache_miss_tokens``            = max(input_tokens - cached_tokens, 0)
#   * ``cache_miss_budget``            = agent._config.cache_miss_budget
#   * ``cache_miss_remaining_tokens``  = max(cache_miss_budget - cache_miss_tokens, 0)
# The two budget-derived fields are omitted (never invented) when no positive-int
# budget is resolvable from the agent config; ``cache_miss_tokens`` — derivable
# from session data alone — is always emitted with the session half.
TOKEN_USAGE_CACHE_MISS_TOKENS_KEY = "cache_miss_tokens"
TOKEN_USAGE_CACHE_MISS_BUDGET_KEY = "cache_miss_budget"
TOKEN_USAGE_CACHE_MISS_REMAINING_KEY = "cache_miss_remaining_tokens"

# Current context state carried under the ``session`` half of
# ``tool_meta.token_usage`` (moved off ``current_call``, since context usage is
# current session/context state, not this provider call's own facts).  Emitted
# only when resolvable: ``context_tokens`` from the cumulative
# ``get_token_usage().ctx_total_tokens``; ``context_window`` from the provider
# snapshot's ``context_window`` or the configured/live window; ``context_usage``
# = ``context_tokens / context_window`` when both are positive.
TOKEN_USAGE_CONTEXT_TOKENS_KEY = "context_tokens"
TOKEN_USAGE_CONTEXT_WINDOW_KEY = "context_window"
TOKEN_USAGE_CONTEXT_USAGE_KEY = "context_usage"


def build_tool_meta_overflow_comment(tool_call_id: str | None) -> dict:
    """Return the ``tool_meta.comment.overflow`` hint for a capped/large result.

    Stamped only when the model-visible payload is capped or large (the caller
    decides; see :meth:`ToolExecutor._attach_tool_block`).  LingTai preserves the
    full, un-capped original in the durable runtime log, so the hint points there
    by ``tool_call_id`` rather than at any external sidecar/saved-path file.

    There is deliberately exactly one comment topic for this feature —
    ``overflow``.  All guidance (what happened, where the original is, how to
    retrieve it, what to do after consuming it) lives under this single key, not
    split across parallel ``comment.retrieval`` / ``comment.summarize`` headings.
    """
    call_id = tool_call_id or "<unknown>"
    return {
        "summary": (
            "The model-visible context for this tool result is capped or large; "
            "what you see here may be a preview or compacted form, not the full payload."
        ),
        "full_original": (
            f"The full original is preserved in logs/events.jsonl under "
            f"tool_call_id={call_id}."
        ),
        "how_to_retrieve": (
            f"Retrieve it from the durable log by tool_call_id: "
            f"grep '{call_id}' <workdir>/logs/events.jsonl, or use "
            f"`lingtai-agent log query` (see the sqlite-log-query manual). For a "
            f"broad extraction, delegate to a daemon/subagent with the "
            f"tool_call_id and the exact question instead of pulling the whole "
            f"original back into your own context."
        ),
        "after_consuming": (
            "After you have consumed what you need, call "
            "system(action=\"summarize\") for this tool_call_id to replace the "
            "visible payload with your own agent-authored summary."
        ),
    }

# Keys that are kernel/runtime scaffolding, not the formal tool-result payload.
# Summarize and the current_tool_result_chars char-ranking must ignore these so
# notification or guidance text is not treated as result content to be summarized
# or counted toward a result's size.
FORMAL_TOOL_RESULT_EXCLUDED_KEYS = frozenset({
    META_ENVELOPE_KEY,
    "_runtime_pending",
    "_advisory",
    "active_turn_tool_calls",
    "active_turn_tool_call_notice",
})


def formal_tool_result_content(content):
    """Return the formal tool-result payload, excluding kernel metadata.

    The ``_meta`` envelope can contain notifications and guidance that are
    channel/runtime state, not the payload returned by the tool.  Context
    summarization and the ``current_tool_result_chars`` char-ranking operate on
    this formal body only, so notification contents are neither size-counted nor
    summarized as if they were the result.
    """
    if not isinstance(content, dict):
        return content
    return {
        key: value
        for key, value in content.items()
        if key not in FORMAL_TOOL_RESULT_EXCLUDED_KEYS
    }


def _visible_content_text(content) -> str:
    if isinstance(content, str):
        return content
    try:
        return _json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def formal_tool_result_visible_len(content) -> int:
    """Visible character length of the formal tool-result payload only."""
    return len(_visible_content_text(formal_tool_result_content(content)))


def formal_tool_result_preview(content, limit: int = 200) -> str:
    """Preview string for the formal tool-result payload only."""
    if limit <= 0:
        return ""
    return _visible_content_text(formal_tool_result_content(content))[:limit]



def _is_tool_result_block(block) -> bool:
    """Best-effort duck-typing for ToolResultBlock without a hard import cycle."""
    return block.__class__.__name__ == "ToolResultBlock" and hasattr(block, "content")


def _iter_history_tool_result_blocks(agent):
    session = getattr(agent, "_session", None)
    chat = getattr(session, "chat", None)
    interface = getattr(chat, "interface", None)
    entries = getattr(interface, "_entries", None)
    if not entries:
        return
    for entry in entries:
        for block in getattr(entry, "content", ()) or ():
            if _is_tool_result_block(block):
                yield block


def adapter_comment(agent):
    """Return an optional adapter-authored, agent-facing runtime note."""

    session = getattr(agent, "_session", None)
    chat = getattr(session, "chat", None)
    comment_fn = getattr(chat, "adapter_comment", None)
    if not callable(comment_fn):
        return None
    try:
        return comment_fn()
    except Exception:
        # `_meta.agent_meta` must never be made unavailable by an adapter note.
        return None


def static_adapter_comment(agent):
    """Return the adapter's static/rule-like runtime note (no dynamic state).

    The static comment is the durable explanation of how the active adapter's
    continuation/caching/summarize machinery behaves; it does not change turn to
    turn.  It is rendered once into the resident ``meta_guidance`` system-prompt
    section rather than re-stamped onto every tail ``_meta``.  Adapters expose it
    via a ``static_adapter_comment`` method; adapters without one simply
    contribute nothing to ``meta_guidance``.  Prefer the service/adapter-level
    hook because the first prompt build happens before a ChatSession exists; the
    chat-level hook remains as a compatibility fallback.
    """
    service = getattr(agent, "service", None)
    comment_fn = getattr(service, "static_adapter_comment", None)
    if callable(comment_fn):
        try:
            comment = comment_fn()
        except Exception:
            comment = None
        if comment:
            return comment

    session = getattr(agent, "_session", None)
    chat = getattr(session, "chat", None)
    comment_fn = getattr(chat, "static_adapter_comment", None)
    if not callable(comment_fn):
        return None
    try:
        return comment_fn()
    except Exception:
        return None


def dynamic_adapter_comment(agent: AgentState) -> Mapping[str, Any] | None:
    """Return adapter-owned dynamic tail state for ``_meta.agent_meta``.

    Adapters that can separate static guidance from dynamic runtime state should
    implement ``dynamic_adapter_comment``.  For legacy adapters, fall back to the
    combined ``adapter_comment`` payload; the generic tail slimmer will only
    trim oversized structures, not guess adapter-specific static keys.
    """
    session = getattr(agent, "_session", None)
    chat = getattr(session, "chat", None)
    comment_fn = getattr(chat, "dynamic_adapter_comment", None)
    if callable(comment_fn):
        try:
            comment = comment_fn()
        except Exception:
            comment = None
        if comment:
            if not isinstance(comment, Mapping):
                return {"note": str(comment)}
            return dict(comment)
    return adapter_comment(agent)


def slim_adapter_comment_for_tail(
    comment: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Trim dynamic adapter tail payload without guessing static keys.

    Static-vs-dynamic partitioning is owned by the adapter via
    ``static_adapter_comment`` / ``dynamic_adapter_comment``.  The kernel only
    removes verbose dynamic structures that are too heavy for every-turn tail
    metadata and adds a hook back to the resident ``meta_guidance`` section.
    """
    if not comment:
        return None
    if not isinstance(comment, Mapping):
        return {"note": str(comment)}

    slim: dict[str, Any] = dict(comment)
    ledger = slim.pop("cache_ledger", None)
    if isinstance(ledger, Mapping):
        summary = ledger.get("summary")
        if isinstance(summary, Mapping) and "cache_ledger_summary" not in slim:
            slim["cache_ledger_summary"] = dict(summary)
        last_full = ledger.get("last_full")
        if isinstance(last_full, Mapping):
            slim.setdefault("last_full_api_calls_ago", last_full.get("api_calls_ago"))
            slim.setdefault("last_full_reason", last_full.get("reason"))
        last_ws_full = ledger.get("last_ws_full")
        if isinstance(last_ws_full, Mapping):
            slim.setdefault(
                "last_ws_full_api_calls_ago",
                last_ws_full.get("api_calls_ago"),
            )
            slim.setdefault("last_ws_full_reason", last_ws_full.get("reason"))

    hint = slim.get("maintenance_hint")
    if isinstance(hint, Mapping):
        compact_hint = dict(hint)
        compact_hint.pop("reason", None)
        if compact_hint:
            slim["maintenance_hint"] = compact_hint
        else:
            slim.pop("maintenance_hint", None)

    return slim or None


TOOL_RESULT_CHARS_TOP_N = 5
TOOL_RESULT_CHARS_MIN_TOP_CHARS = 1000
# Fallback large-result hint threshold (chars) used by current_tool_result_chars
# when the agent has no ``_summarize_notification_threshold`` set.  Mirrors
# BaseAgent's default and messaging.DEFAULT_SUMMARIZE_NOTIFICATION_THRESHOLD;
# kept local to avoid a base_agent import cycle.
DEFAULT_LARGE_RESULT_THRESHOLD = 3000
TOOL_RESULT_CHARS_README = (
    "listing top 5 tool results over 1000 chars by char count "
    "(id, tool_name, chars; no preview); no need to summarize this helper "
    "(it rides on agent_meta, which is sparse/update-driven — re-emitted on a "
    "later result when the material snapshot changes, so read the most recent "
    "emitted agent_meta for the current list); these are summarize candidates, "
    "not a directive to summarize "
    "every entry: prefer summarizing prior results that are already "
    "consumed/digested and useless, irrelevant, obsolete, or no longer needed "
    "in full, weighing context pressure, recoverability from logs, and future "
    "reuse/token savings, and batch them by the listed ids/tool names; if an "
    "adapter comment is present, follow its adapter-specific summarize rules too"
)


def _tool_result_id(block) -> str:
    return str(getattr(block, "id", None) or getattr(block, "tool_call_id", None) or "")


def _tool_result_name(block) -> str:
    return str(getattr(block, "name", None) or getattr(block, "tool_name", None) or "")


def current_tool_result_chars(agent, extra_results=()) -> dict:
    """Return current context-visible formal tool-result char summary.

    The count is intentionally based on formal result payloads rather than
    runtime metadata.  ``_meta`` notifications/guidance, transient scaffolding,
    and other non-payload fields are excluded by
    ``formal_tool_result_visible_len``.  ``extra_results`` lets latest-result
    stamping include the just-created tool-result batch before those blocks are
    appended to chat history.

    The returned dict also carries ``threshold`` (the agent's configured
    large-result hint threshold in chars) and ``over_threshold_count`` (how many
    in-context formal results exceed it).  Together with ``top_results`` these
    let the agent see what counts as "large" and how many candidates exist —
    the context the removed ``large_tool_result`` notification used to carry —
    so it can decide what to ``system(action="summarize")``.
    """
    threshold = getattr(
        agent, "_summarize_notification_threshold", DEFAULT_LARGE_RESULT_THRESHOLD
    )
    total = 0
    over_threshold_count = 0
    top: list[dict] = []
    seen: set[int] = set()

    def visit(block) -> None:
        nonlocal total, over_threshold_count
        seen.add(id(block))
        content = getattr(block, "content", "")
        chars = formal_tool_result_visible_len(content)
        total += chars
        if isinstance(threshold, int) and threshold > 0 and chars > threshold:
            over_threshold_count += 1
        if chars > TOOL_RESULT_CHARS_MIN_TOP_CHARS:
            top.append(
                {
                    "id": _tool_result_id(block),
                    "tool_name": _tool_result_name(block),
                    "chars": chars,
                }
            )

    for block in _iter_history_tool_result_blocks(agent) or ():
        visit(block)
    for block in extra_results or ():
        if not _is_tool_result_block(block) or id(block) in seen:
            continue
        visit(block)

    top.sort(key=lambda item: item["chars"], reverse=True)
    return {
        "total_chars": total,
        "threshold": threshold,
        "over_threshold_count": over_threshold_count,
        "top_results": top[:TOOL_RESULT_CHARS_TOP_N],
    }


def _meta_block(result: dict) -> dict:
    """Return ``result["_meta"]``, creating an empty dict if absent.

    Centralizes the envelope so the per-result ``tool_meta`` writer and the
    sparse ``agent_meta``/``guidance`` updater and the sparse/update-driven
    notification mover all share one container.
    """
    meta = result.get(META_ENVELOPE_KEY)
    if not isinstance(meta, dict):
        meta = {}
        result[META_ENVELOPE_KEY] = meta
    return meta


def build_meta_readme() -> dict:
    """Self-describing readme for the five ``_meta`` blocks.

    This readme is rendered once into the resident ``meta_guidance``
    system-prompt section (via :func:`build_meta_guidance`), not stamped onto
    every tool result; the tail ``_meta.guidance`` carries only a lightweight
    ref back to that section.  Each entry states what the block is for and
    whether it is per-result, sparse/update-driven, or current-state — no policy,
    just structural orientation.
    """
    return {
        TOOL_META_KEY: (
            "Per-result tool/call metadata (id, timestamp, optional current_time, "
            "char_count, elapsed_ms, optional token_usage, optional context). "
            "Present on every tool result; "
            "permanent. context, when present, may carry context.rebuild — a "
            "lightweight line stamped continuously once context is >= 0.75 saying "
            "the agent may manually rebuild via summarize(rebuild=true). It "
            "may also carry the SUSTAINED-pressure context.molt reminder string — "
            "a stronger warning that appears only after context has been high "
            "(>= 0.75) for several consecutive fresh provider rounds and clears "
            "when pressure drops. The context block lives here (permanent, "
            "restamped on every result while active) rather than in the sparse "
            "agent_meta so the reminder persists. context also carries the "
            "cache-miss budget guard: a soft per-molt/session cap on total "
            "cache-miss (uncached input) tokens for the CURRENT runtime session "
            "(default 1,000,000). Once the session cache-miss total reaches/exceeds "
            "cache_miss_budget, context.molt carries a 'cache miss budget {N} "
            "reached, molt now' warning and context.cache_miss_budget / "
            "context.cache_miss_tokens report the configured budget and the current "
            "cache-miss total. When the sustained-pressure warning is also active, "
            "both warnings are preserved in context.molt (the budget line is "
            "appended). The action when warned is to molt. token_usage is the single token-diagnostics block "
            "(see meta_guidance.token_efficiency). It is NESTED into two explicitly "
            "named halves (not one flat dict): current_call — ONLY this provider "
            "call's own token/cache/output facts, keys input, cache_miss, cache_rate, "
            "output, thinking (context state is NOT here); and session — the "
            "SINCE-LAST-MOLT cumulative aggregate, keys session_cache_rate, api_calls, "
            "input_tokens, cached_tokens, avg_input_tokens_per_api_call, the current "
            "context state context_tokens/context_window/context_usage (when "
            "resolvable), plus ALWAYS-ON since-last-molt cache-miss/budget telemetry: "
            "cache_miss_tokens (since-last-molt cumulative cache miss = "
            "max(input_tokens - cached_tokens, 0)), cache_miss_budget (the configured "
            "budget), and cache_miss_remaining_tokens (max(cache_miss_budget - "
            "cache_miss_tokens, 0)). The nesting removes the confusing flat "
            "current_call.input vs session.input_tokens adjacency. These three "
            "cache-miss fields ride under session on EVERY result whenever session "
            "aggregate token usage is available (cache_miss_budget/"
            "cache_miss_remaining_tokens are present only when a budget is "
            "configured), so you can always read your current cumulative cache miss "
            "and remaining budget here without recomputing input_tokens - "
            "cached_tokens or remembering the default budget — distinct from the "
            "context.* guard above, which appears only once you have reached/exceeded "
            "the budget. If you have reached or are nearing the cache-miss budget, do "
            "NOT use summarize to reconstruct context because reconstruction itself "
            "will create a large cache miss; molt proactively. The session-half "
            "fields are SINCE-LAST-MOLT cumulative/restored totals — they SURVIVE a "
            "refresh/restart (they read the durable cumulative counters, NOT the "
            "since-refresh runtime-session deltas), so a refresh does not zero them "
            "and cache_miss_remaining_tokens does not reset. The "
            "block also carries a short top-level ref sentence ('See "
            "meta_guidance.token_efficiency for details.') hooking the guidance "
            "subsection that explains how to act on it. Each "
            "half (current_call, session) appears only "
            "when its source data is available (an empty half is omitted, not left "
            "empty); missing inner values are omitted, not "
            "invented. Copied here so agents can inspect historical high-context "
            "summarize/rebuild costs after newer results arrive. May also "
            "carry a one-shot 'reconstruction' event when the runtime just "
            "rebuilt provider context at the 1.0 forced boundary or for a manual "
            "rebuild: it records the event type (delayed_summarize_reconstruction or "
            "summarize_rebuild_only_reconstruction), the before (A) and after (B) "
            "context tokens/usage, context_window, trigger_threshold (1.0 hard "
            "forced-rebuild boundary), threshold_high (0.75 manual/high-context "
            "hint), and recovery_target (0.6). A 1.0 forced-rebuild event ALWAYS "
            "carries a single unified reconstruction.warning (before->after change, "
            "proactive 0.75-rebuild advice, and the conditional 'if still above 0.6, "
            "molt' instruction, no high/low branching). Manual rebuild events do "
            "not carry that warning; if B is still at/above the recovery target they "
            "instead include a natural-language molt reminder at reconstruction.molt "
            "(a one-shot; distinct from the sustained-pressure tool_meta.context.molt "
            "above). This is permanent evidence of a past event, not current state."
        ),
        AGENT_META_KEY: (
            "Agent/current-state snapshot (elapsed_ms, active_turn_tool_calls, "
            "current_tool_result_chars, optional "
            "adapter_comment). Numeric context/token diagnostics are deliberately "
            "not duplicated here: the per-call token/cache facts and the "
            "since-last-molt session aggregate (including current context state "
            "context_tokens/context_window/context_usage) live permanently in "
            "tool_meta.token_usage instead (see "
            "meta_guidance.token_efficiency). The sustained-pressure context.molt "
            "reminder is NOT here either — it now lives in permanent "
            "tool_meta.context.molt so it persists on every result while active. "
            "SPARSE / "
            "update-driven: agent_meta is attached to a tool result only when its "
            "MATERIAL snapshot changes since the last emitted agent_meta — it is "
            "NOT re-stamped onto the newest tool result merely because that result "
            "is the latest when nothing material changed. Volatile bookkeeping "
            "(elapsed_ms, active_turn_tool_calls, current_time, and the running "
            "current_tool_result_chars.total_chars) does not count as a change. "
            "So the most recent agent_meta may sit on an EARLIER result than the "
            "newest one; scan backward for the last-emitted snapshot, and read "
            "each emitted agent_meta as the agent state at that update point. "
            "agent_meta is a timely runtime/current-state hint: older emitted "
            "snapshots stay in historical context and logs as historical traces "
            "(they are not retroactively removed), and if several appear, only "
            "the NEWEST one is current — older snapshots are past state, not "
            "current state. Model-facing full-history serialization / a fresh "
            "provider replay preserves every historical agent_meta/guidance "
            "holder's content — replay does not strip or remove old copies. "
            "Only the LATEST holder in history represents current agent state; "
            "every older holder is a historical trace, not a current instruction "
            "or a fact to act on. "
            "agent_meta carries NO token diagnostics: all token/cache "
            "facts — both this call's own facts and the since-last-molt session "
            "aggregate — live "
            "permanently in tool_meta.token_usage instead (see "
            "meta_guidance.token_efficiency). "
            "current_tool_result_chars is a compact dict with total_chars, "
            "threshold (the large-result hint size in chars), "
            "over_threshold_count (how many in-context formal results exceed it), "
            "and top_results (id, tool_name, chars; no preview) for "
            "proactive summarization candidates. adapter_comment is a small "
            "provider/adapter-authored note carrying only dynamic per-turn "
            "runtime scalars; the adapter's static "
            "rules live in the system-prompt section meta_guidance."
        ),
        GUIDANCE_KEY: (
            "Lightweight ref/hook to the resident system-prompt section "
            "meta_guidance, where the full kernel guidance sections, this "
            "_meta envelope readme, and any static adapter runtime rules live. "
            "Rides with agent_meta on the same sparse/update-driven cadence "
            "(attached only when agent_meta is re-emitted); carries no full "
            "guidance body."
        ),
        NOTIFICATION_GUIDANCE_KEY: (
            "Kernel safety framing for channel notification handling. Rides with "
            "notifications on the same sparse/update-driven cadence (attached only "
            "when notifications is (re)attached)."
        ),
        NOTIFICATIONS_KEY: (
            "Channel notification payloads. Static safety framing lives under "
            "notification_guidance/meta_guidance; per-channel duplicate guidance is omitted. "
            "SPARSE / update-driven and channel-owned: attached on first "
            "appearance and re-attached only when the notification payload "
            "MATERIALLY changes (or on a deliberate notification(action=check) "
            "read) — NOT re-stamped onto the newest tool result merely because "
            "that result is the latest when the payload is unchanged. The most "
            "recent notifications may therefore sit on an EARLIER result than the "
            "newest one; scan backward for the last-emitted payload and read it "
            "as the current channel state. "
            "Notification payloads are timely/current-state hints: older payloads "
            "stay in historical context and logs as historical traces (they are "
            "not retroactively removed), and if several appear, only the NEWEST "
            "one is current — older payloads are not current instructions or "
            "unhandled events; act on new messages through the producer channel "
            "(telegram.read, email.read, ...), which remains the source of truth "
            "for actionable channel content. Model-facing full-history "
            "serialization / a fresh provider replay preserves every historical "
            "notifications/notification_guidance holder's content — replay "
            "does not strip or remove old copies. Only the LATEST holder in "
            "history represents the current channel state; every older holder "
            "is a historical trace, not a current instruction or an unhandled "
            "event to act on. "
            "Not part of the formal tool-result payload; do not summarize "
            "notification contents as the result body."
        ),
        NOTIFICATION_PERSISTENT_KEY: (
            "Sparse communication-context lane, currently the curated IM "
            "producers (Telegram, WeChat, Feishu, WhatsApp) and built-in email. "
            "All IM channels share one typed lane primitive and carry "
            "structured recent/new messages under "
            f"{NOTIFICATION_PERSISTENT_TELEGRAM_PATH}.messages / "
            f"{NOTIFICATION_PERSISTENT_WECHAT_PATH}.messages / "
            f"{NOTIFICATION_PERSISTENT_FEISHU_PATH}.messages / "
            f"{NOTIFICATION_PERSISTENT_WHATSAPP_PATH}.messages, "
            "event/routing hooks under `.events`, and concise English machine "
            "comments. Delta lanes (Telegram, WeChat, Feishu) additionally "
            "carry a previous_block hook "
            "pointing to the prior block for the same channel (and an optional "
            "human-readable comment), plus `.context_comment` (a seed block's "
            "historical id range plus the current/new message id) and "
            "`.burst_comment` (multiple new incoming messages arrived together "
            "— one burst), and `.referenced_messages` (Telegram only: the "
            "full reply target with a per-item `comment` when the current reply "
            "points at a message absent from `.messages`). The snapshot lane "
            "(WhatsApp, email-style) carries a standing `.context_comment` "
            "(producer authority + reply rules) on every block with no "
            "previous_block hook and no delivered-id delta tracking. "
            "Per-message `comment`s mark the agent's own outgoing continuity "
            "messages, truncated messages, and (WhatsApp) non-text/media "
            "messages — all of which point to the producer read tool "
            "(telegram.read / wechat.read / feishu.read / whatsapp.read) for "
            "exact full producer state. This is the durable source of truth for "
            "IM conversation context and routing details — the "
            "ephemeral _meta.notifications.mcp.<channel> lane is only a short "
            "high-attention hook carrying message_ids, not a holder for message "
            "text, sender/subject, routing refs, counts, or content-location "
            "pointers. Unread email content lives under "
            f"{NOTIFICATION_PERSISTENT_EMAIL_PATH}. It is not a "
            "notification/action/dismiss channel and is not part of the formal "
            "tool-result payload; older delta-lane blocks intentionally remain "
            "in history so later deltas can refer to them via their "
            "previous_block hook (snapshot lanes re-emit the current window)."
        ),
    }


def now_iso_plain() -> str:
    """Return the current UTC time as a plain ISO-8601 string (no agent needed).

    Used by ``_meta.tool_meta`` block stamping where no agent context is available.
    Always returns UTC with a Z suffix, e.g. ``2026-06-20T12:34:56Z``.
    Falls back to empty string on any error.
    """
    try:
        import datetime as _dt
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Runtime guidance catalog — prompt package resource, loaded once.
# ---------------------------------------------------------------------------

_GUIDANCE_CACHE: dict | None = None

# Allowed values for the small fixed-vocabulary fields. Kept permissive on
# purpose: the kernel must not reject a future render strategy it does not yet
# know about, only structurally malformed payloads.
_GUIDANCE_REQUIRED_TOP_KEYS = ("schema_version", "guidance_version", "priority", "render_mode", "sections")


class GuidanceSchemaError(ValueError):
    """Raised when the runtime guidance payload does not match the expected shape.

    A structural problem in the *packaged* resource is a build/authoring error,
    not a runtime condition, so this is surfaced loudly to ``validate_runtime_guidance``
    callers (and the test suite). The live loader (``build_runtime_guidance``)
    degrades to ``{}`` rather than crashing an agent on a bad ship.
    """



META_README_SECTION_ID = "meta_readme"


def build_meta_readme_section() -> Dict[str, str]:
    """Return the guidance section that explains the `_meta` envelope.

    This readme is one ordered section among the kernel guidance sections; both
    are rendered into the resident ``meta_guidance`` system-prompt section (see
    :func:`build_meta_guidance`).  The tail ``_meta.guidance`` on tool results is
    only a lightweight ref back to that section, never the full body.
    """
    readme = build_meta_readme()
    body_lines = [
        "This section explains the `_meta` envelope carried on tool results.",
        "These explanations are resident here in the `meta_guidance` system-prompt section; the tail `_meta.guidance` on each tool result carries only a lightweight ref back to this section, not the full body.",
        "",
    ]
    body_lines.extend(f"- `{key}`: {value}" for key, value in readme.items())
    return {
        "id": META_README_SECTION_ID,
        "title": "_meta envelope readme",
        "body": "\n".join(body_lines),
    }


def build_guidance_with_meta_readme(base_guidance: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Return runtime guidance with the `_meta` readme appended as a section."""
    source = build_runtime_guidance() if base_guidance is None else base_guidance
    guidance = dict(source or {})
    # Preserve packaged guidance keys when available, but keep the fallback shape
    # valid too: even if the guidance catalog cannot be loaded, guidance remains the
    # same system-prompt-like structure with a single meta_readme section.
    guidance.setdefault("schema_version", 1)
    guidance.setdefault("guidance_version", "runtime-meta-readme")
    guidance.setdefault("priority", "high")
    guidance.setdefault("render_mode", "latest_tool_result_only")
    sections = []
    for section in guidance.get("sections") or []:
        if not isinstance(section, dict):
            continue
        if section.get("id") == META_README_SECTION_ID:
            continue
        sections.append(dict(section))
    sections.append(build_meta_readme_section())
    guidance["sections"] = sections
    return guidance

# ---------------------------------------------------------------------------
# meta_guidance — resident system-prompt section.
#
# The static, rule-like content that used to ride in every tail
# ``_meta.guidance`` (the runtime guidance sections + the ``_meta`` readme) and
# in the adapter's ``adapter_comment`` (the long full-epoch/summarize prose) is
# rendered once here and appended as the final, always-resident system-prompt
# section named ``meta_guidance``.  The tail ``_meta`` then carries only a
# lightweight ref pointing back at this section.
# ---------------------------------------------------------------------------

META_GUIDANCE_SECTION_ID = "meta_guidance"

# Short hook the unified ``tool_meta.token_usage`` block carries back to the
# resident guidance subsection that explains how to read/act on it. A short
# sentence (not a bare path) pointing at the ``token_efficiency`` subsection of
# the ``meta_guidance`` system-prompt section.
TOKEN_USAGE_GUIDANCE_REF = (
    f"See {META_GUIDANCE_SECTION_ID}.token_efficiency for details."
)


def build_meta_guidance_ref() -> dict:
    """Return the lightweight ``_meta.guidance`` hook for a sparse runtime block."""
    return {"ref": META_GUIDANCE_SECTION_ID}

def _render_guidance_sections_markdown(guidance: dict) -> list[str]:
    """Render guidance.sections (incl. meta_readme) as Markdown subsections."""
    lines: list[str] = []
    for section in (guidance or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        title = section.get("title") or section.get("id") or ""
        body = section.get("body") or ""
        if title:
            lines.append(f"### {title}")
        if body:
            lines.append(body)
        lines.append("")
    return lines


def _render_adapter_comment_markdown(comment: dict) -> list[str]:
    """Render a static adapter_comment dict as a Markdown subsection."""
    if not isinstance(comment, dict) or not comment:
        return []
    adapter = comment.get("adapter") or "adapter"
    lines = [f"### {adapter} runtime rules"]
    for key, value in comment.items():
        if key == "adapter":
            continue
        if isinstance(value, str) and value:
            lines.append(f"- `{key}`: {value}")
    lines.append("")
    return lines


def build_meta_guidance(agent) -> str:
    """Render the resident ``meta_guidance`` system-prompt section body.

    Combines the static, rule-like material that previously rode on every tail
    ``_meta``:

      * the runtime guidance sections from the Markdown guidance catalog (e.g.
        summarize/molt best practice);
      * the ``_meta`` envelope readme (which blocks exist and whether each is
        per-result, sparse/update-driven, or current-state);
      * the active adapter's *static* runtime rules (from
        :func:`static_adapter_comment`), if any.

    Dynamic per-result / sparse state (tool_meta, current context/molt hints,
    notifications, current_tool_result_chars, adapter epoch counters, cache
    ledger summary, …) is deliberately NOT rendered here — it stays in the tail
    ``_meta`` so this section can remain a stable, cache-friendly prefix.

    Returns the Markdown body (no ``## meta_guidance`` header — the prompt
    manager adds the section header).  Returns ``""`` only if nothing renders.
    """
    guidance = build_guidance_with_meta_readme()
    lines: list[str] = [
        "Resident kernel guidance for reading runtime metadata. This is the "
        "static, rule-like material; dynamic per-turn state stays in the tail "
        "`_meta` block on tool results (which points back here via "
        "`_meta.guidance.ref`).",
        "",
    ]
    lines.extend(_render_guidance_sections_markdown(guidance))
    static_comment = static_adapter_comment(agent)
    lines.extend(_render_adapter_comment_markdown(static_comment))
    body = "\n".join(lines).strip()
    return body


def validate_runtime_guidance(data) -> dict:
    """Validate the guidance payload shape, returning it unchanged on success.

    Raises :class:`GuidanceSchemaError` on any structural violation:
      * top-level must be a dict with ``schema_version`` (int), ``guidance_version``
        (str), ``priority`` (str), ``render_mode`` (str), and ``sections`` (list);
      * each section must be a dict with non-empty string ``id``, ``title``, ``body``;
      * section ``id`` and ``title`` must each be unique across the list.

    This is intentionally strict and independently testable so a malformed
    packaged resource is caught by the test suite rather than silently shipping
    empty guidance to production agents.
    """
    if not isinstance(data, dict):
        raise GuidanceSchemaError(f"guidance must be a JSON object, got {type(data).__name__}")
    for key in _GUIDANCE_REQUIRED_TOP_KEYS:
        if key not in data:
            raise GuidanceSchemaError(f"guidance missing required key: {key!r}")
    if not isinstance(data["schema_version"], int) or isinstance(data["schema_version"], bool):
        raise GuidanceSchemaError("guidance.schema_version must be an int")
    for str_key in ("guidance_version", "priority", "render_mode"):
        if not isinstance(data[str_key], str) or not data[str_key]:
            raise GuidanceSchemaError(f"guidance.{str_key} must be a non-empty string")
    sections = data["sections"]
    if not isinstance(sections, list) or not sections:
        raise GuidanceSchemaError("guidance.sections must be a non-empty list")

    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for idx, section in enumerate(sections):
        if not isinstance(section, dict):
            raise GuidanceSchemaError(f"guidance.sections[{idx}] must be an object")
        for field in ("id", "title", "body"):
            value = section.get(field)
            if not isinstance(value, str) or not value:
                raise GuidanceSchemaError(
                    f"guidance.sections[{idx}].{field} must be a non-empty string"
                )
        sid = section["id"]
        stitle = section["title"]
        if sid in seen_ids:
            raise GuidanceSchemaError(f"duplicate guidance section id: {sid!r}")
        if stitle in seen_titles:
            raise GuidanceSchemaError(f"duplicate guidance section title: {stitle!r}")
        seen_ids.add(sid)
        seen_titles.add(stitle)
    return data


def build_runtime_guidance() -> dict:
    """Load, validate, and return the runtime guidance payload.

    Sourced from the skill-style Markdown catalog under
    ``lingtai/prompts/meta_guidance/catalog/`` (``INDEX.md`` + one ``<id>.md`` per section),
    assembled by :func:`lingtai.kernel.prompt_catalog.load_guidance_catalog` into
    the same dict shape the kernel has always consumed (``schema_version`` int,
    ordered ``sections`` with stable ``id``/``title``/``body``). The return type
    stays a ``dict`` so it can both feed ``build_meta_guidance`` and back the
    derived ``system/guidance.json`` mirror the TUI/Portal read.

    Cached after first successful load. The assembled payload is schema-checked
    via :func:`validate_runtime_guidance`; on a missing/unreadable catalog, a
    malformed file, or a schema violation the loader returns an empty dict so a
    live agent degrades (no guidance) rather than crashing. Tests should call
    :func:`validate_runtime_guidance` directly to assert the *packaged* catalog
    is well-formed — that path raises, this one does not.
    """
    global _GUIDANCE_CACHE
    if _GUIDANCE_CACHE is not None:
        return _GUIDANCE_CACHE
    try:
        from .prompt_catalog import load_guidance_catalog

        parsed = load_guidance_catalog()
        validate_runtime_guidance(parsed)
        _GUIDANCE_CACHE = parsed
        return parsed
    except Exception:
        return {}



def build_molt_context(agent, usage: float) -> str | None:
    # NOTE: the lighter 75% manual-rebuild hint is built by
    # ``build_context_rebuild_hint`` below.  This function remains the stronger
    # sustained-pressure molt reminder.
    """Return the sustained-pressure molt reminder string, or ``None``.

    The returned text is attached to PERMANENT ``_meta.tool_meta.context.molt``
    (``build_meta`` routes it there via a transit key so it persists on every
    result while the warning is active — it is NOT the sparse ``agent_meta``).
    The contract (channel B)
    replaces the old immediate ``usage >= 0.60`` trip-wire with a
    *sustained-pressure* signal: the reminder appears only once context has been
    high (>= the 0.75 reconstruction ratio) for
    ``CONTEXT_PRESSURE_WARN_AFTER_ROUNDS`` consecutive *fresh provider rounds*,
    tracked by ``SessionManager.note_context_pressure_round``. The first two
    high rounds are the window in which the automatic delayed-summarize
    reconstruction (and any agent summarize) is expected to relieve pressure; a
    drop below the threshold resets the streak and clears the reminder.

    Keep this agent-facing value sentence-like. The agent needs a clear reminder
    about why it appeared and what to do, not a tag soup of ``stage`` /
    ``threshold`` / ``action`` fields.
    """
    if "psyche" not in getattr(agent, "_intrinsics", set()):
        return None

    session = getattr(agent, "_session", None)
    if session is None:
        return None
    # The warning decision + prose live in ``ContextPressureReminder``; the
    # psyche-intrinsic gate and session lookup stay here (they are agent/session
    # concerns, not reminder concerns). Prefer the real reminder object; fall
    # back to the session's compat streak/active surface so lightweight test
    # stand-ins (a SimpleNamespace with only context_pressure_* attributes) still
    # render identical prose.
    reminder = getattr(session, "context_pressure_reminder", None)
    if reminder is not None:
        return reminder.current_molt_context(usage)

    if not getattr(session, "context_pressure_warning_active", False):
        return None
    streak = int(getattr(session, "context_pressure_streak", 0))
    return render_current_molt_context(streak=streak, usage=usage)


def build_context_rebuild_hint(agent, usage: float) -> str | None:
    """Return the lightweight 75% manual provider-context rebuild hint.

    This is not a molt warning and not an event route.  It is a current-state line
    stamped under ``_meta.tool_meta.context.rebuild`` whenever context is at/above
    ``CONTEXT_PRESSURE_HIGH_RATIO`` and the system intrinsic is available, so the
    agent may explicitly request a rebuild via
    ``system(action='summarize', rebuild=true)`` instead of letting the
    1.0 hard boundary force one.
    """
    if "system" not in getattr(agent, "_intrinsics", set()):
        return None
    try:
        pressure = float(usage)
    except (TypeError, ValueError):
        return None
    if pressure < CONTEXT_PRESSURE_HIGH_RATIO:
        return None
    return (
        "context now above 75%: recording summaries does NOT itself rebuild the "
        "active provider context. If recorded summaries are worth making active "
        "sooner, you MAY pay for a provider-context rebuild via "
        "system(action='summarize', rebuild=true) (with or without new items). This "
        "is a permitted option, not a requirement; if you do nothing, the runtime "
        "forces a rebuild at the 1.0 hard boundary (full context) regardless. "
        "Preferring a proactive rebuild here avoids the emergency forced path. Keep "
        "summarizing digested results to shrink recorded history either way. See "
        "meta_guidance for details."
    )


def build_context_overflow_warning(agent) -> str | None:
    """Return the persistent post-forced-rebuild overflow warning, or ``None``.

    Distinct from the sustained-pressure reminder (:func:`build_molt_context`,
    the 3-round streak) and the one-shot reconstruction event
    (:func:`build_reconstruction_tool_meta`, a historical A→B rebuild record):
    this is the human-authored hard-boundary warning that stays on EVERY
    ``_meta.tool_meta.context.molt`` result while the automatic one-shot forced
    provider-context rebuild has already fired for the current ``>= 1.0`` episode,
    its first post-rebuild provider response has been observed, and current
    provider-reported usage is still STRICTLY above 1.0 (the forced rebuild failed
    to clear the overflow).

    The adapter owns the one-shot latch + verification state and exposes the
    numeric status via ``session.chat.context_overflow_status()`` (forwarded
    through the gate proxy); this function only renders the fixed sentence with the
    measured percentage. Returns ``None`` whenever the status is absent — it never
    invents a warning.
    """
    session = getattr(agent, "_session", None)
    if session is None:
        return None
    chat = getattr(session, "chat", None)
    status_fn = getattr(chat, "context_overflow_status", None)
    if not callable(status_fn):
        return None
    try:
        status = status_fn()
    except Exception:
        return None
    if not isinstance(status, dict):
        return None
    try:
        usage = float(status.get("usage"))
    except (TypeError, ValueError):
        return None
    return render_forced_rebuild_failed_warning(usage)


def _resolve_cache_miss_budget(agent) -> int | None:
    """Return the configured positive-int cache-miss budget, or ``None``.

    Reads ``agent._config.cache_miss_budget``.  ``bool`` is an ``int`` subclass,
    so it is rejected explicitly (a ``True`` budget must never mean ``1``); any
    non-int or non-positive value disables the budget-derived telemetry.  Shared
    by :func:`build_cache_miss_budget_context` (the at/above-budget guard) and
    :func:`_build_session_token_economy` (the always-on session-half fields) so
    both read the budget with identical semantics.
    """
    config = getattr(agent, "_config", None)
    budget = getattr(config, "cache_miss_budget", None)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        return None
    return budget


def build_cache_miss_budget_context(agent) -> dict | None:
    """Return the cache-miss budget guard sub-object, or ``None``.

    A soft since-last-molt cap on total cache-miss (uncached input) tokens.  The
    cache-miss total is derived from ``agent.get_token_usage()`` — the
    CUMULATIVE / restored totals, which SURVIVE ``restore_token_state`` — so a
    refresh does NOT reset the guard (Jason FINAL; matches the always-on
    ``session`` telemetry in :func:`_build_session_token_economy`, both on the
    same cumulative basis) as::

        cache_miss = max(input_tokens - cached_tokens, 0)

    When ``cache_miss >= agent._config.cache_miss_budget`` (inclusive), return a
    dict destined for the SAME ``_tool_meta_context`` transit sub-object as the
    sustained-pressure ``molt`` reminder::

        {
            "molt": "cache miss budget {budget} reached, molt now",
            "cache_miss_budget": <budget>,
            "cache_miss_tokens": <cache_miss>,
        }

    ``ToolExecutor._attach_tool_block`` promotes the whole sub-object into the
    permanent ``tool_meta.context`` block, so the warning persists (restamped on
    every result) at ``tool_meta.context.molt`` and the budget value is surfaced
    at ``tool_meta.context.cache_miss_budget`` while the guard is tripped.

    Returns ``None`` (no guard) when: the ``psyche`` intrinsic is absent (matching
    :func:`build_molt_context`, since ``molt`` presupposes the molt action), the
    budget is not a positive int, the cumulative-usage getter is missing/raising,
    or the cache-miss total is below the budget.  It is a soft signal only —
    nothing is blocked — and NOT a new event route (no emission-event payload).
    """
    if "psyche" not in getattr(agent, "_intrinsics", set()):
        return None

    # Defensive: only a positive int arms the guard (shared with the always-on
    # session-half telemetry so both read the budget identically).
    budget = _resolve_cache_miss_budget(agent)
    if budget is None:
        return None

    # Since-last-molt basis: read the cumulative/restored totals so a refresh
    # does not reset the guard (identical source to the always-on session-half
    # cache-miss telemetry).
    usage_fn = getattr(agent, "get_token_usage", None)
    if not callable(usage_fn):
        return None
    try:
        usage = usage_fn()
    except Exception:
        return None
    if not isinstance(usage, Mapping):
        return None

    input_tokens = _non_negative_int(usage.get("input_tokens"))
    cached_tokens = _non_negative_int(usage.get("cached_tokens"))
    cache_miss = max(input_tokens - cached_tokens, 0)
    if cache_miss < budget:
        return None

    return {
        "molt": f"cache miss budget {budget} reached, molt now",
        TOOL_META_CONTEXT_CACHE_MISS_BUDGET_KEY: budget,
        TOOL_META_CONTEXT_CACHE_MISS_TOKENS_KEY: cache_miss,
    }


def _current_molt_emission_event(agent, *, usage, message) -> dict | None:
    """Return the current-molt emission-event descriptor, or ``None``.

    Pure / side-effect-free: it only builds the ``{event_name, payload}``
    descriptor from the session's reminder state (the values that produced
    ``message``).  It does NOT decide whether to log — the DEDUP happens at the
    real emission site (``ToolExecutor._attach_tool_block``), keyed by the
    payload's ``last_round_id``, so this render-path call never mutates agent
    state (``build_meta`` runs both for the text-input prefix and per tool-result
    stamp; a side effect here would desync the dedup).

    Returns ``None`` only when no real reminder object is available (compat
    session stand-ins that expose just ``context_pressure_*`` attributes carry no
    round id / debug state to build a meaningful event from).
    """
    session = getattr(agent, "_session", None)
    reminder = getattr(session, "context_pressure_reminder", None)
    if reminder is None:
        return None
    try:
        return current_molt_emission_descriptor(reminder, usage=usage, message=message)
    except Exception:
        return None


def build_reconstruction_tool_meta(agent) -> dict | None:
    """Build the one-shot delayed-summarize reconstruction event (channel A).

    Permanent per-result evidence, destined for ``_meta.tool_meta.reconstruction``.
    Distinct from :func:`build_molt_context` (channel B, current-state reminder
    routed to permanent ``tool_meta.context.molt``): this records a *historical event* — the runtime actually rebuilt the
    provider context around the compacted history when context crossed the 0.75
    reconstruction threshold.

    The adapter supplies the before-context (A) and fixed trigger/recovery
    metadata via ``session.chat.take_pending_reconstruction_event()`` (one-shot:
    the adapter clears it on read). This function fills the after-context (B).

    Call order makes B honest: ``SessionManager.send`` runs ``_track_usage``
    (which sets ``_latest_input_tokens`` from the post-reconstruction provider
    request's reported input) BEFORE the resulting tool calls reach the
    ToolExecutor that stamps this event. So at attach time ``_latest_input_tokens``
    already holds the provider-reported size of the rebuilt context. B therefore
    **prefers** ``_latest_input_tokens / context_window`` (``source:
    provider_input_tokens``) and only falls back to the local compacted-history
    estimate (``source: local_estimate``) when the provider input is unavailable
    (0, e.g. a provider that returned no usage). The delayed-reconstruction
    threshold is itself provider-input based, so this keeps B on the same ruler.

    If B is still at/above the 0.6 recovery target, a natural-language molt
    reminder is attached saying summarize/reconstruction was attempted and
    pressure remains above the recovery target, so consider molt. If B < 0.6,
    the A->B event is returned without a reminder.

    Returns ``None`` when no reconstruction is pending (the common case).
    """
    session = getattr(agent, "_session", None)
    if session is None:
        return None
    chat = getattr(session, "chat", None)
    take = getattr(chat, "take_pending_reconstruction_event", None)
    if not callable(take):
        # Fall back to a session-level hook if the adapter exposes it there.
        take = getattr(session, "take_pending_reconstruction_event", None)
    if not callable(take):
        return None
    raw = take()
    if not raw:
        return None

    # Context window: prefer the value the adapter captured at reconstruction
    # time; fall back to the configured/live window so B can be computed even if
    # the event omitted it.
    ctx_window = 0
    try:
        ctx_window = int(raw.get("context_window") or 0)
    except Exception:
        ctx_window = 0
    if ctx_window <= 0:
        ctx_window = _fallback_context_window(agent)
        if ctx_window <= 0:
            ctx_window = 0

    # After-context (B): prefer the provider-reported input from the
    # post-reconstruction request; fall back to the local compacted-history
    # estimate only when that is unavailable.
    after_tokens = None
    after_usage = -1.0
    after_source = None
    try:
        provider_input = int(getattr(session, "_latest_input_tokens", 0) or 0)
    except Exception:
        provider_input = 0
    if provider_input > 0 and ctx_window > 0:
        after_tokens = provider_input
        after_usage = provider_input / ctx_window
        after_source = "provider_input_tokens"
    else:
        # Local fallback: reuse the same local (system + history) / window math
        # used for current-state context-pressure warnings.  The value is no
        # longer serialized into agent_meta, but reconstruction events still need
        # it when provider input tokens are unavailable.
        try:
            local_usage = float(_current_context_usage(agent))
        except Exception:
            local_usage = -1.0
        if local_usage >= 0:
            after_usage = local_usage
            after_source = "local_estimate"
            if ctx_window > 0:
                after_tokens = int(round(local_usage * ctx_window))

    event = {
        "type": raw.get("type", "delayed_summarize_reconstruction"),
        "reason": raw.get("reason", "delayed_summarize_reconstruction"),
        "trigger_threshold": raw.get(
            "trigger_threshold", CONTEXT_PRESSURE_FORCED_REBUILD_RATIO
        ),
        "threshold_high": raw.get("threshold_high", CONTEXT_PRESSURE_HIGH_RATIO),
        "recovery_target": raw.get("recovery_target", CONTEXT_PRESSURE_RECOVERY_TARGET),
        "context_window": raw.get("context_window"),
        "before": raw.get("before", {}),
        "after": {
            "context_tokens": after_tokens,
            "usage": round(after_usage, 5) if after_usage >= 0 else after_usage,
            "source": after_source,
        },
    }

    recovery_target = event["recovery_target"]

    if event["type"] == "delayed_summarize_reconstruction":
        # 1.0 HARD forced rebuild: ALWAYS attach the one unified warning,
        # regardless of whether the rebuilt context dropped low or stayed high. It
        # folds the before→after change, the proactive-rebuild advice, and the
        # conditional "if still above 0.6, molt" instruction into a single string —
        # no after-high/low branching.
        before = event.get("before") if isinstance(event.get("before"), dict) else {}
        event["warning"] = render_forced_rebuild_warning(
            before_tokens=before.get("context_tokens"),
            before_usage=before.get("usage"),
            after_tokens=after_tokens,
            after_usage=after_usage,
            trigger_threshold=event.get(
                "trigger_threshold", CONTEXT_PRESSURE_FORCED_REBUILD_RATIO
            ),
            high_threshold=event.get("threshold_high", CONTEXT_PRESSURE_HIGH_RATIO),
            recovery_target=recovery_target,
        )
        return event

    # Manual rebuild=true reconstruction (summarize_rebuild_only_reconstruction):
    # the agent already acted proactively, so no forced-rebuild warning. Keep the
    # recovery molt reminder (channel A) when the rebuilt context is still above the
    # recovery target. Delegate to the session's reminder when present, falling back
    # to the pure renderer for session stand-ins without one.
    reminder = getattr(session, "context_pressure_reminder", None)
    if reminder is not None:
        molt = reminder.annotate_reconstruction(
            after_usage, recovery_target=recovery_target
        )
    else:
        molt = render_reconstruction_molt(
            after_usage=after_usage,
            recovery_target=recovery_target,
            reconstruction_ratio=event.get("threshold_high", CONTEXT_PRESSURE_HIGH_RATIO),
        )
    if molt:
        event["molt"] = molt
    return event


def _build_provider_round_token_usage(agent) -> dict:
    """Return the ``current_call`` (provider-round) half of the token_usage block.

    ``current_call`` is ONLY this provider call's own token/cache/output facts.
    Reads ``SessionManager.latest_token_usage_snapshot()`` — the full
    provider-round record kept for internal logging (scope, api-call index/id,
    cached/context tokens, estimated flag, ...) — and projects only the per-result
    evidence agents need: ``input``/``cache_miss``/``cache_rate``/``output``/
    ``thinking``, mapped from the snapshot's long field names.

    Current CONTEXT state (``context_usage``/``window``/context tokens) is NOT
    part of this call's own facts — it is current session/context state and now
    lives in the ``session`` half (see :func:`_build_session_token_economy`), so
    it is deliberately dropped here along with the other noisy/invalid/duplicate
    fields (scope, api_call_id, context_tokens, estimated, the provider-round
    cached_tokens). Missing fields are omitted rather than invented; existing
    numeric zero/sentinel values are preserved. Returns ``{}`` when no snapshot
    exists.
    """
    session = getattr(agent, "_session", None)
    snapshot_fn = getattr(session, "latest_token_usage_snapshot", None)
    if callable(snapshot_fn):
        try:
            snapshot = snapshot_fn()
        except Exception:
            snapshot = None
    else:
        snapshot = getattr(session, "_latest_token_usage_snapshot", None)
    if not isinstance(snapshot, Mapping):
        return {}
    # Map full snapshot field names -> compact injected keys. Only emit a key
    # when the source field is present, so the injected object stays robust to
    # partial snapshots without inventing values. NOTE: context_usage/window are
    # intentionally absent — they moved to the session half.
    field_map = (
        ("input", "input_tokens"),
        ("cache_miss", "cache_miss_tokens"),
        ("cache_rate", "cache_rate"),
        ("output", "output_tokens"),
        ("thinking", "thinking_tokens"),
    )
    return {
        out_key: snapshot[src_key]
        for out_key, src_key in field_map
        if src_key in snapshot
    }


def _session_context_window(agent) -> int:
    """Return the context window for the ``session`` context state, or ``0``.

    Prefers the latest provider-round snapshot's ``context_window`` (the value the
    provider actually served the current context against); falls back to the
    configured/live window via :func:`_fallback_context_window` (config
    ``context_limit`` then ``chat.context_window()``).  Returns ``0`` when no
    positive window is resolvable, so callers omit the context-state fields
    rather than dividing by an unknown window.
    """
    session = getattr(agent, "_session", None)
    snapshot_fn = getattr(session, "latest_token_usage_snapshot", None)
    snapshot = None
    if callable(snapshot_fn):
        try:
            snapshot = snapshot_fn()
        except Exception:
            snapshot = None
    else:
        snapshot = getattr(session, "_latest_token_usage_snapshot", None)
    if isinstance(snapshot, Mapping):
        window = _non_negative_int(snapshot.get("context_window"))
        if window > 0:
            return window
    fallback = _fallback_context_window(agent)
    return fallback if isinstance(fallback, int) and fallback > 0 else 0


def _build_session_token_economy(agent) -> dict:
    """Return the ``session`` (since-last-molt) half of the token_usage block.

    Sources the aggregate from the AGENT-SESSION object when available
    (``agent.agent_session_token_usage()``), falling back to
    ``agent.get_token_usage()`` for agents/stubs that expose no agent-session
    accessor.  Both read the CUMULATIVE / restored ``_total_*``/``_api_calls``
    counters, which SURVIVE ``restore_token_state`` (refresh/restart) — and, since
    the startup restore is now seeded from the rebuilt agent session's since-molt
    totals (see ``lifecycle._start``), those counters are genuinely
    since-current-molt across a refresh rather than lifetime.  This is the
    "since last molt" contract: the injected ``token_usage.session`` must NOT
    reset on refresh, so it deliberately reads these totals rather than
    ``get_runtime_session_token_usage()`` (the since-refresh deltas, which zero
    out on every restart — that was the #679 defect).  The since-refresh runtime
    getter is never consulted here.

    Routing the numbers through the agent-session view keeps a single owner for
    the since-molt aggregate (the ``AgentSession``), per Jason's same-PR wiring:
    the numbers are identical to ``get_token_usage`` (same counters), but the
    agent-session object is now the named source.  The current CONTEXT state
    (``ctx_total_tokens``) still comes from ``get_token_usage`` since it is live
    context, not part of the since-molt token aggregate.

    Projects the aggregate counters agents act on now: ``session_cache_rate``
    (cached/input clamped to a 0-1 fraction), ``api_calls``,
    ``input_tokens``/``cached_tokens``, and ``avg_input_tokens_per_api_call``,
    deriving the rates from the raw counters.

    It also carries the current CONTEXT state (moved off ``current_call``, since
    context usage is current session/context state, not this call's own facts),
    when resolvable:

    * ``context_tokens`` from ``get_token_usage().ctx_total_tokens``;
    * ``context_window`` from the provider snapshot or configured window (see
      :func:`_session_context_window`);
    * ``context_usage`` = ``context_tokens / context_window`` when both positive.

    And the ALWAYS-ON since-last-molt cache-miss/budget telemetry so an agent
    never has to recompute ``input_tokens - cached_tokens`` or remember the
    default budget (contrast the ``tool_meta.context`` guard, which appears only
    at/above budget):

    * ``cache_miss_tokens`` = ``max(input_tokens - cached_tokens, 0)`` — the
      since-last-molt cumulative cache miss, on the same cumulative basis as
      :func:`build_cache_miss_budget_context`, so a refresh does not reset it.
      Always emitted here, since it needs only the aggregate counters.
    * ``cache_miss_budget`` = ``agent._config.cache_miss_budget`` and
      ``cache_miss_remaining_tokens`` = ``max(cache_miss_budget - cache_miss_tokens, 0)``
      — emitted only when a positive-int budget is resolvable from the agent
      config (see :func:`_resolve_cache_miss_budget`); omitted, never invented,
      for config-less stubs.

    Returns ``{}`` when no aggregate usage is available; numeric zeros are preserved.
    """
    usage_fn = getattr(agent, "get_token_usage", None)
    if not callable(usage_fn):
        return {}
    try:
        usage = usage_fn()
    except Exception:
        return {}
    if not isinstance(usage, Mapping):
        return {}

    # Prefer the AGENT-SESSION view for the since-molt token aggregate (single
    # owner), falling back to the raw cumulative counters for stubs/agents that
    # expose no agent-session accessor. The numbers are the same counters; this
    # only routes them through the named object per the same-PR wiring.
    agg = usage
    agent_session_usage_fn = getattr(agent, "agent_session_token_usage", None)
    if callable(agent_session_usage_fn):
        try:
            candidate = agent_session_usage_fn()
        except Exception:
            candidate = None
        if isinstance(candidate, Mapping):
            agg = candidate

    api_calls = _non_negative_int(agg.get("api_calls"))
    input_tokens = _non_negative_int(agg.get("input_tokens"))
    cached_tokens = _non_negative_int(agg.get("cached_tokens"))
    avg_input = int(round(input_tokens / api_calls)) if api_calls > 0 else 0
    session_cache_rate = (
        round(min(cached_tokens / input_tokens, 1.0), 5)
        if input_tokens > 0
        else 0.0
    )
    cache_miss = max(input_tokens - cached_tokens, 0)
    economy = {
        "session_cache_rate": session_cache_rate,
        "api_calls": api_calls,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "avg_input_tokens_per_api_call": avg_input,
        # Always-on: derivable from the cumulative counters alone.
        TOKEN_USAGE_CACHE_MISS_TOKENS_KEY: cache_miss,
    }

    # Current context state — only when resolvable (never invented).
    if "ctx_total_tokens" in usage:
        context_tokens = _non_negative_int(usage.get("ctx_total_tokens"))
        economy[TOKEN_USAGE_CONTEXT_TOKENS_KEY] = context_tokens
        window = _session_context_window(agent)
        if window > 0:
            economy[TOKEN_USAGE_CONTEXT_WINDOW_KEY] = window
            economy[TOKEN_USAGE_CONTEXT_USAGE_KEY] = round(
                context_tokens / window, 5
            )

    budget = _resolve_cache_miss_budget(agent)
    if budget is not None:
        economy[TOKEN_USAGE_CACHE_MISS_BUDGET_KEY] = budget
        economy[TOKEN_USAGE_CACHE_MISS_REMAINING_KEY] = max(budget - cache_miss, 0)
    return economy


def build_tool_meta_token_usage(agent) -> dict | None:
    """Return the token diagnostics block for permanent ``tool_meta``.

    ALL token-related diagnostics live in ONE ``_meta.tool_meta.token_usage``
    block — there is no separate ``tool_meta.token_efficiency`` nor
    ``agent_meta.token_efficiency``.  The block is NESTED into two explicitly
    named halves so the confusing flat ``input`` vs ``input_tokens`` adjacency is
    gone; each half keeps its own local key convention:

    * ``current_call`` — ONLY this tool result's own provider-call token/cache/
      output facts: ``input``, ``cache_miss``, ``cache_rate``, ``output``,
      ``thinking`` (see :func:`_build_provider_round_token_usage`).  Current
      context state is NOT here — it moved to the ``session`` half.
    * ``session`` — the SINCE-LAST-MOLT cumulative aggregate: ``session_cache_rate``,
      ``api_calls``, ``input_tokens``, ``cached_tokens``,
      ``avg_input_tokens_per_api_call``, the current context state
      ``context_tokens`` / ``context_window`` / ``context_usage`` (when
      resolvable), plus the always-on cache-miss/budget telemetry
      ``cache_miss_tokens`` and (when a positive-int budget is configured)
      ``cache_miss_budget`` / ``cache_miss_remaining_tokens``.  These are
      cumulative/restored totals that SURVIVE refresh (NOT the since-refresh
      runtime-session deltas); see :func:`_build_session_token_economy`.

    Each half is emitted only when its source data is available (an empty half is
    omitted entirely, not left as an empty sub-object); missing inner values are
    omitted, not invented; numeric zero/sentinel values are preserved.  When the
    block exists it always carries a single top-level ``ref`` hook
    (:data:`TOKEN_USAGE_GUIDANCE_REF`) — shared across both halves, never
    duplicated inside them — back to the resident guidance subsection.  Returns
    ``None`` when neither half has any data (never an empty block).
    """
    current_call = _build_provider_round_token_usage(agent)
    session = _build_session_token_economy(agent)
    if not current_call and not session:
        return None
    block: dict = {}
    if current_call:
        block[TOKEN_USAGE_CURRENT_CALL_KEY] = current_call
    if session:
        block[TOKEN_USAGE_SESSION_KEY] = session
    block["ref"] = TOKEN_USAGE_GUIDANCE_REF
    return block

def _current_context_usage(agent) -> float:
    """Return the current context-window usage ratio for warnings/events.

    This helper owns the local (system + history) / window estimate that used to
    be serialized under ``agent_meta.context``.  The number is still needed for
    current-state decisions such as ``context.molt`` and reconstruction event
    fallbacks, but it is no longer exposed in ``agent_meta`` because
    ``tool_meta.token_usage`` is the permanent token-diagnostics carrier.
    """
    session = getattr(agent, "_session", None)
    chat_obj = getattr(session, "chat", None) if session is not None else None

    if session is not None and getattr(session, "_token_decomp_dirty", True):
        try:
            session._update_token_decomposition()
        except Exception:
            pass  # leave dirty; sentinel below

    decomp_ran = session is not None and not getattr(session, "_token_decomp_dirty", True)
    if not decomp_ran:
        return -1.0

    sys_prompt = getattr(session, "_system_prompt_tokens", 0)
    tools = getattr(session, "_tools_tokens", 0)

    # "history" = in-memory turns (wire chat).  Prefer the provider-reported
    # wire count after a call; before the first post-restore call, fall back to
    # the interface's local estimate so current-state warnings use restored
    # history rather than reporting zero.
    latest_input = getattr(session, "_latest_input_tokens", 0) or 0
    if latest_input > 0:
        history = max(0, latest_input - sys_prompt - tools)
    elif chat_obj is not None:
        try:
            history = max(0, chat_obj.interface.estimate_context_tokens() - sys_prompt - tools)
        except Exception:
            history = 0
    else:
        history = 0

    system_tokens = sys_prompt + tools
    history_tokens = history

    if chat_obj is not None:
        limit = getattr(agent._config, "context_limit", 0) or chat_obj.context_window()
    else:
        limit = getattr(agent._config, "context_limit", 0) or 0
    return (system_tokens + history_tokens) / limit if limit > 0 else -1.0

def build_meta(agent) -> dict:
    """Return the current meta-data snapshot for the agent.

    Respects ``agent._config.time_awareness`` / ``timezone_awareness``
    internally; callers never need to special-case those flags.

    Shape::

        {
            "current_time": "<iso>",          # transient; promoted into tool_meta
            "_tool_meta_context": {           # transient; promoted into tool_meta.context
                "rebuild": str,               # 75%+ manual rebuild permission hint
                "molt": str,                  # sustained-pressure and/or cache-miss-budget reminder
                "cache_miss_budget": int,     # present only when the budget guard is tripped
                "cache_miss_tokens": int,     # present only when the budget guard is tripped
            },
            "_tool_meta_context_event": {...},# transient; deduped current-molt emission event
            "current_tool_result_chars": dict,# total + top formal tool results >1000 chars
        }

    ``current_time`` and the two ``_tool_meta_context*`` keys are transient
    transit keys: ``ToolExecutor._attach_tool_block`` promotes ``current_time``
    and the sustained-pressure ``molt`` reminder into the PERMANENT per-result
    ``tool_meta`` block (``tool_meta.current_time`` / ``tool_meta.context.molt``),
    and logs ``context_pressure_current_molt_reminder_emitted`` from the
    ``_tool_meta_context_event`` payload — deduped there to once per provider
    round (this function is side-effect-free and carries the payload on every
    build while the warning is active, since it also runs for the text-input
    prefix).  The molt reminder is therefore permanent per-result metadata, not
    the sparse ``agent_meta``.  Numeric context/token diagnostics are not
    duplicated in ``agent_meta``; provider-round ``context_usage``/``window`` and
    session token stats live in ``tool_meta.token_usage``.

    The ``_tool_meta_context`` sub-object is emitted when the lightweight 75%+
    manual-rebuild hint is active, OR the sustained-pressure warning is active,
    OR the cache-miss budget guard is tripped
    (:func:`build_cache_miss_budget_context`).  When warning paths fire together,
    both warnings are
    preserved in ``molt`` (the budget line is appended on its own line, never
    replacing the context-pressure prose) and the budget fields
    (``cache_miss_budget`` / ``cache_miss_tokens``) ride alongside.  The budget
    guard is a soft signal only and NOT a new event route — it never attaches a
    ``_tool_meta_context_event``, and the context-pressure event still hashes only
    its own pure message.

    """
    meta: dict = {}
    ts = now_iso(agent)
    if ts:
        meta["current_time"] = ts

    usage = _current_context_usage(agent)

    rebuild_hint = build_context_rebuild_hint(agent, usage)
    if rebuild_hint:
        meta[TOOL_META_CONTEXT_PENDING_KEY] = {
            TOOL_META_CONTEXT_REBUILD_KEY: rebuild_hint
        }

    # Sustained-pressure molt reminder — now PERMANENT per-result metadata at
    # ``tool_meta.context.molt`` (moved off the sparse ``agent_meta.context`` so
    # it persists on every result while the warning is active).  It rides via a
    # transit key that ``ToolExecutor._attach_tool_block`` promotes into the
    # permanent ``tool_meta.context`` block.  Numeric context/token diagnostics
    # stay in ``tool_meta.token_usage``.
    molt = build_molt_context(agent, usage)
    if molt:
        existing_context = meta.get(TOOL_META_CONTEXT_PENDING_KEY)
        if isinstance(existing_context, dict):
            existing_context["molt"] = molt
        else:
            meta[TOOL_META_CONTEXT_PENDING_KEY] = {"molt": molt}
        # The channel-B emission event is built from the PURE sustained-pressure
        # message (before the budget line is appended below), so its
        # ``message_hash`` and per-round dedup semantics stay unchanged even when
        # both warnings are active.
        event = _current_molt_emission_event(agent, usage=usage, message=molt)
        if event is not None:
            meta[TOOL_META_CONTEXT_EVENT_PENDING_KEY] = event

    # Persistent post-forced-rebuild overflow warning — the human-authored hard
    # boundary sentence, routed to the SAME permanent ``tool_meta.context.molt``
    # channel.  It is a current-state warning (the adapter owns the one-shot latch
    # + verification), NOT a new event route, so it never attaches a
    # ``_tool_meta_context_event``.  When the sustained-pressure reminder is also
    # active, PRESERVE both: append the overflow line on its own newline rather
    # than replacing the sustained-pressure prose (the cache-miss budget line, if
    # any, is appended after this below).
    overflow_warning = build_context_overflow_warning(agent)
    if overflow_warning:
        existing_context = meta.get(TOOL_META_CONTEXT_PENDING_KEY)
        if isinstance(existing_context, dict):
            prior_molt = existing_context.get("molt")
            existing_context["molt"] = (
                f"{prior_molt}\n{overflow_warning}" if prior_molt else overflow_warning
            )
        else:
            meta[TOOL_META_CONTEXT_PENDING_KEY] = {"molt": overflow_warning}

    # Cache-miss budget guard — rides the SAME ``_tool_meta_context`` transit
    # sub-object as the sustained-pressure reminder.  When both are active we
    # PRESERVE both warnings: the budget line is appended to ``molt`` on a new
    # line (never replacing the context-pressure prose), and the budget fields
    # are merged in alongside.  This is a soft signal, not a new event route, so
    # no ``_tool_meta_context_event`` is emitted for it.
    budget_ctx = build_cache_miss_budget_context(agent)
    if budget_ctx:
        existing = meta.get(TOOL_META_CONTEXT_PENDING_KEY)
        if isinstance(existing, dict):
            prior_molt = existing.get("molt")
            budget_molt = budget_ctx["molt"]
            existing["molt"] = (
                f"{prior_molt}\n{budget_molt}" if prior_molt else budget_molt
            )
            existing[TOOL_META_CONTEXT_CACHE_MISS_BUDGET_KEY] = budget_ctx[
                TOOL_META_CONTEXT_CACHE_MISS_BUDGET_KEY
            ]
            existing[TOOL_META_CONTEXT_CACHE_MISS_TOKENS_KEY] = budget_ctx[
                TOOL_META_CONTEXT_CACHE_MISS_TOKENS_KEY
            ]
        else:
            meta[TOOL_META_CONTEXT_PENDING_KEY] = budget_ctx

    tool_meta_token_usage = build_tool_meta_token_usage(agent)
    if tool_meta_token_usage:
        meta[TOOL_META_TOKEN_USAGE_PENDING_KEY] = tool_meta_token_usage

    meta["current_tool_result_chars"] = current_tool_result_chars(agent)

    comment = dynamic_adapter_comment(agent)
    if comment:
        # Only the slim dynamic view rides on the tail; the static adapter rules
        # are resident in the ``meta_guidance`` system-prompt section.
        meta["adapter_comment"] = slim_adapter_comment_for_tail(comment)

    # Notifications are deliberately NOT included here. Active-state
    # notification payload is a moving live block attached SPARSELY /
    # update-driven — on first appearance and re-attached only on a material
    # change (or a deliberate notification(action=check) read) — by
    # ``attach_active_notifications`` at the tool-batch boundary.  Putting it in
    # ``build_meta`` would stamp it onto every tool result and accumulate
    # forever in history. The IDLE-state synthesized notification pair and the
    # ACTIVE-state tool-result holder both use the same canonical
    # ``notifications`` payload shape instead.

    return meta


# ---------------------------------------------------------------------------
# Active-state notification stamping — sparse/update-driven canonical payload.
# ---------------------------------------------------------------------------


def build_notification_payload(notifications: dict) -> dict:
    """Return active notification payload plus a compact guidance hook.

    Producers own the per-channel envelope under ``notifications``.  Static
    safety/provenance framing lives in resident
    ``meta_guidance.notification_handling``, so the per-result ``_meta`` block
    carries only active sources and channel-owned dynamic payloads.
    """
    sources = [str(source) for source in notifications.keys()]
    payloads: dict = {}
    for source, payload in notifications.items():
        if isinstance(payload, dict):
            payload_for_wire = dict(payload)
        else:
            payload_for_wire = {"data": payload}
        payload_for_wire.pop(NOTIFICATION_GUIDANCE_KEY, None)
        payloads[str(source)] = payload_for_wire

    return {
        NOTIFICATION_GUIDANCE_KEY: {
            "ref": "meta_guidance.notification_handling",
            "sources": sources,
        },
        NOTIFICATIONS_KEY: payloads,
    }




class _ImPersistentLane(NamedTuple):
    """Per-channel parameters for the shared IM persistent-notification lane.

    The preview/fallback/annotate/sanitize machinery is identical across
    curated IM producers; only the channel identity, the producer preview
    window, the agent-side delivery-tracking attributes, the English comment
    wording, and the delivery ``mode`` differ.  Telegram is the reference
    instance; WeChat and Feishu mirror it.

    ``mode`` selects one of two delivery shapes:

    - ``"delta"`` — seed/delta blocks with in-memory delivered-id tracking and
      a ``previous_block`` hook to the prior block (Telegram, WeChat, Feishu).
    - ``"snapshot"`` — email-style: every block carries the producer's current
      bounded context in full under a standing ``snapshot_context_comment``;
      no delivered-id state, no ``previous_block``, no burst/seed comments
      (WhatsApp, whose producer re-sends the last-10 window per event and
      whose replies are gated by the Cloud API 24-hour window).

    ``referenced_comment`` is ``None`` for producers that never attach
    ``referenced_messages`` (reply targets outside the preview window).
    ``media_comment`` is set for producers whose local store keeps only
    type/id metadata for non-text messages.
    """

    channel: str            # e.g. "telegram" — key under notification_persistent.mcp
    source_key: str         # e.g. "mcp.telegram" — key under _meta.notifications
    path: str               # full dotted persistent path, for hooks/comments
    display_name: str       # e.g. "Telegram" — English comment wording
    mode: str               # "delta" or "snapshot" (see class docstring)
    self_outgoing_comment: str
    truncated_comment: str
    # Delta-mode fields (unused for snapshot lanes).
    min_context: int = 0     # seed/delta boundary == producer preview window
    seen_limit: int = 0      # delivered-id cache cap
    delivered_ids_attr: str | None = None  # agent attr: delivered message-id list
    last_tool_id_attr: str | None = None   # agent attr: prior block's tool id
    burst_comment: str | None = None
    referenced_comment: str | None = None
    # Snapshot-mode field: standing context comment on every block.
    snapshot_context_comment: str | None = None
    # Optional per-message hint for non-text messages (any mode).
    media_comment: str | None = None


_TELEGRAM_PERSISTENT_LANE = _ImPersistentLane(
    channel=NOTIFICATION_PERSISTENT_TELEGRAM_CHANNEL,
    source_key="mcp.telegram",
    path=NOTIFICATION_PERSISTENT_TELEGRAM_PATH,
    display_name="Telegram",
    mode="delta",
    min_context=NOTIFICATION_PERSISTENT_TELEGRAM_MIN_CONTEXT,
    seen_limit=NOTIFICATION_PERSISTENT_TELEGRAM_SEEN_LIMIT,
    delivered_ids_attr="_notification_persistent_telegram_message_ids",
    last_tool_id_attr="_notification_persistent_telegram_last_tool_id",
    burst_comment=NOTIFICATION_PERSISTENT_TELEGRAM_BURST_COMMENT,
    self_outgoing_comment=NOTIFICATION_PERSISTENT_TELEGRAM_SELF_OUTGOING_COMMENT,
    truncated_comment=NOTIFICATION_PERSISTENT_TELEGRAM_TRUNCATED_COMMENT,
    referenced_comment=NOTIFICATION_PERSISTENT_TELEGRAM_REFERENCED_COMMENT,
)

_WECHAT_PERSISTENT_LANE = _ImPersistentLane(
    channel=NOTIFICATION_PERSISTENT_WECHAT_CHANNEL,
    source_key="mcp.wechat",
    path=NOTIFICATION_PERSISTENT_WECHAT_PATH,
    display_name="WeChat",
    mode="delta",
    min_context=NOTIFICATION_PERSISTENT_WECHAT_MIN_CONTEXT,
    seen_limit=NOTIFICATION_PERSISTENT_WECHAT_SEEN_LIMIT,
    delivered_ids_attr="_notification_persistent_wechat_message_ids",
    last_tool_id_attr="_notification_persistent_wechat_last_tool_id",
    burst_comment=NOTIFICATION_PERSISTENT_WECHAT_BURST_COMMENT,
    self_outgoing_comment=NOTIFICATION_PERSISTENT_WECHAT_SELF_OUTGOING_COMMENT,
    truncated_comment=NOTIFICATION_PERSISTENT_WECHAT_TRUNCATED_COMMENT,
    # The WeChat producer has no reply-target threading, so it never attaches
    # referenced_messages; the referenced-message pass is skipped for this lane.
    referenced_comment=None,
)

_FEISHU_PERSISTENT_LANE = _ImPersistentLane(
    channel=NOTIFICATION_PERSISTENT_FEISHU_CHANNEL,
    source_key="mcp.feishu",
    path=NOTIFICATION_PERSISTENT_FEISHU_PATH,
    display_name="Feishu",
    mode="delta",
    min_context=NOTIFICATION_PERSISTENT_FEISHU_MIN_CONTEXT,
    seen_limit=NOTIFICATION_PERSISTENT_FEISHU_SEEN_LIMIT,
    delivered_ids_attr="_notification_persistent_feishu_message_ids",
    last_tool_id_attr="_notification_persistent_feishu_last_tool_id",
    burst_comment=NOTIFICATION_PERSISTENT_FEISHU_BURST_COMMENT,
    self_outgoing_comment=NOTIFICATION_PERSISTENT_FEISHU_SELF_OUTGOING_COMMENT,
    truncated_comment=NOTIFICATION_PERSISTENT_FEISHU_TRUNCATED_COMMENT,
    # The Feishu producer threads replies via per-message `reply_to` refs and
    # never attaches out-of-window `referenced_messages`; the referenced pass
    # is skipped for this lane.
    referenced_comment=None,
)

_WHATSAPP_PERSISTENT_LANE = _ImPersistentLane(
    channel=NOTIFICATION_PERSISTENT_WHATSAPP_CHANNEL,
    source_key="mcp.whatsapp",
    path=NOTIFICATION_PERSISTENT_WHATSAPP_PATH,
    display_name="WhatsApp",
    # Snapshot lane (email-style): full bounded context per block, no
    # delivered-id delta state, no previous_block hook — see the class
    # docstring for why WhatsApp deliberately differs from the delta lanes.
    mode="snapshot",
    self_outgoing_comment=NOTIFICATION_PERSISTENT_WHATSAPP_SELF_OUTGOING_COMMENT,
    truncated_comment=NOTIFICATION_PERSISTENT_WHATSAPP_TRUNCATED_COMMENT,
    snapshot_context_comment=NOTIFICATION_PERSISTENT_WHATSAPP_CONTEXT_COMMENT,
    # The WhatsApp local store keeps only type/id metadata for media messages.
    media_comment=NOTIFICATION_PERSISTENT_WHATSAPP_MEDIA_COMMENT,
)

# Ordered registry of IM channels sharing the persistent lane machinery.
_IM_PERSISTENT_LANES = (
    _TELEGRAM_PERSISTENT_LANE,
    _WECHAT_PERSISTENT_LANE,
    _FEISHU_PERSISTENT_LANE,
    _WHATSAPP_PERSISTENT_LANE,
)


def _im_preview_list(notification_payload: dict, source_key: str) -> list[dict]:
    """Return IM notification preview entries from the canonical payload."""
    notifications = notification_payload.get(NOTIFICATIONS_KEY)
    if not isinstance(notifications, dict):
        return []
    channel = notifications.get(source_key)
    if not isinstance(channel, dict):
        return []
    data = channel.get("data")
    if not isinstance(data, dict):
        return []
    previews = data.get("previews")
    if not isinstance(previews, list):
        return []
    return [preview for preview in previews if isinstance(preview, dict)]


def _im_fallback_message_from_preview(preview: dict) -> dict | None:
    """Build a persistent message from a legacy IM preview-only event."""
    preview_text = preview.get("preview")
    if not isinstance(preview_text, str) or not preview_text:
        return None

    msg_id = preview.get("message_ref")
    if not isinstance(msg_id, str) or not msg_id:
        digest_src = "|".join(
            str(preview.get(key, ""))
            for key in ("conversation_ref", "from", "subject", "preview")
        )
        msg_id = "notification-preview:" + _hashlib.sha1(
            digest_src.encode("utf-8", errors="replace")
        ).hexdigest()[:16]

    sender = preview.get("from")
    item: dict = {
        "id": msg_id,
        "direction": "incoming",
        "sender": sender if isinstance(sender, str) and sender else "unknown",
        "text": preview_text,
        "text_truncated": bool(preview.get("preview_truncated")),
        "source": "notification_preview",
    }
    for key in ("subject", "conversation_ref", "platform"):
        value = preview.get(key)
        if isinstance(value, str) and value:
            item[key] = value
    return item


def _im_persistent_event_from_preview(preview: dict) -> dict | None:
    """Move IM event/routing hook metadata into the persistent lane."""
    event: dict = {}
    for key in ("from", "subject", "conversation_ref", "message_ref", "platform"):
        value = preview.get(key)
        if isinstance(value, str) and value:
            event[key] = value
    if not event:
        return None
    return event


def _im_persistent_events_from_notifications(
    notification_payload: dict, source_key: str
) -> list[dict]:
    """Extract IM event/routing hooks from notification preview metadata."""
    events: list[dict] = []
    for preview in _im_preview_list(notification_payload, source_key):
        event = _im_persistent_event_from_preview(preview)
        if event is not None:
            events.append(event)
    return events


def _im_notification_event_count(notification_payload: dict, source_key: str) -> int:
    """Return the IM notification event count when the producer reports it."""
    notifications = notification_payload.get(NOTIFICATIONS_KEY)
    if not isinstance(notifications, dict):
        return 0
    channel = notifications.get(source_key)
    if not isinstance(channel, dict):
        return 0
    data = channel.get("data")
    if not isinstance(data, dict):
        return 0
    count = data.get("count")
    return count if isinstance(count, int) and count > 0 else 0


def _im_persistent_messages_from_notifications(
    notification_payload: dict, source_key: str
) -> list[dict]:
    """Extract ordered IM message objects from notification preview metadata.

    Prefer the curated structured ``recent_messages`` / ``latest_incoming``
    fields.  If an older or degraded IM notification has only the bounded
    body preview, move that preview into the persistent lane as a fallback
    message so the transient notification never carries IM content.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for preview in _im_preview_list(notification_payload, source_key):
        candidates: list[object] = []
        has_structured = False
        recent = preview.get("recent_messages")
        if isinstance(recent, list):
            candidates.extend(recent)
            has_structured = True
        latest = preview.get("latest_incoming")
        if isinstance(latest, dict):
            candidates.append(latest)
            has_structured = True
        if not has_structured:
            fallback = _im_fallback_message_from_preview(preview)
            if fallback is not None:
                candidates.append(fallback)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            msg_id = candidate.get("id")
            if not isinstance(msg_id, str) or not msg_id:
                continue
            if msg_id not in by_id:
                order.append(msg_id)
            by_id[msg_id] = dict(candidate)
    return [by_id[msg_id] for msg_id in order if msg_id in by_id]


def _im_referenced_messages_from_notifications(
    notification_payload: dict, source_key: str
) -> list[dict]:
    """Extract full referenced IM messages (reply targets) from previews.

    Curated producers (currently only Telegram) attach the full referenced
    message under ``referenced_messages`` when the current reply targets a
    message outside the preview window. De-duplicate by message ID, preserving
    first-seen order.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for preview in _im_preview_list(notification_payload, source_key):
        referenced = preview.get("referenced_messages")
        if not isinstance(referenced, list):
            continue
        for candidate in referenced:
            if not isinstance(candidate, dict):
                continue
            msg_id = candidate.get("id")
            if not isinstance(msg_id, str) or not msg_id:
                continue
            if msg_id not in by_id:
                order.append(msg_id)
            by_id[msg_id] = dict(candidate)
    return [by_id[msg_id] for msg_id in order if msg_id in by_id]


def _im_display_message_number(compound_id: object) -> str:
    """Return a robust human-facing message number from a producer message ID.

    Telegram compound IDs are ``account:chat:message``; the trailing segment is
    the Telegram message id.  Other producers (WeChat local UUIDs) and
    degraded/fallback ids fall back to the raw value so the range comment never
    crashes on an unexpected shape.
    """
    if not isinstance(compound_id, str) or not compound_id:
        return "?"
    parts = compound_id.split(":")
    if len(parts) == 3 and parts[2]:
        return parts[2]
    return compound_id


def _im_range_context_comment(messages: list[dict], display_name: str) -> str | None:
    """Build the English historical-range comment for a seeded context block.

    Identifies the current/new message (``is_current`` when present, else the
    last incoming message) and describes the remaining messages as historical
    context using robust ids drawn from the producer ids.  Returns ``None`` when
    there is no historical range to describe (e.g. a single-message block).
    """
    if len(messages) < 2:
        return None
    current = next((m for m in messages if m.get("is_current")), None)
    if current is None:
        current = next(
            (m for m in reversed(messages) if m.get("direction") == "incoming"),
            None,
        )
    if current is None:
        current = messages[-1]
    current_id = current.get("id")
    historical = [m for m in messages if m.get("id") != current_id]
    if not historical:
        return None
    first_num = _im_display_message_number(historical[0].get("id"))
    last_num = _im_display_message_number(historical[-1].get("id"))
    current_num = _im_display_message_number(current_id)
    if first_num == last_num:
        span = f"Message {first_num} is historical context"
    else:
        span = f"Messages {first_num}–{last_num} are historical context"
    return (
        f"{span} from the recent {display_name} conversation. "
        f"The current/new message is {current_num}."
    )


def _annotate_im_message(message: dict, lane: _ImPersistentLane) -> dict:
    """Return a copy of *message* with per-message continuity/truncation hints.

    Adds the self-outgoing continuity comment to the agent's own outgoing
    messages, the truncation comment to truncated messages, and (for lanes whose
    local store keeps only type/id metadata) the media comment to non-text
    messages. When several apply, the comments are joined so no signal is
    dropped. Media metadata already on the message is preserved untouched.
    """
    annotated = dict(message)
    hints: list[str] = []
    if annotated.get("direction") == "outgoing":
        hints.append(lane.self_outgoing_comment)
    if annotated.get("text_truncated"):
        hints.append(lane.truncated_comment)
    if lane.media_comment is not None:
        message_type = annotated.get("type")
        if (
            isinstance(message_type, str)
            and message_type not in ("", "text")
            and not annotated.get("text")
        ):
            hints.append(lane.media_comment)
    if hints:
        existing = annotated.get("comment")
        if isinstance(existing, str) and existing:
            hints = [existing, *hints]
        annotated["comment"] = " ".join(hints)
    return annotated


def _email_notification_data(notification_payload: dict) -> dict:
    notifications = notification_payload.get(NOTIFICATIONS_KEY)
    if not isinstance(notifications, dict):
        return {}
    email = notifications.get("email")
    if not isinstance(email, dict):
        return {}
    data = email.get("data")
    return data if isinstance(data, dict) else {}


def _email_notification_email_ids(notification_payload: dict) -> list[str]:
    data = _email_notification_data(notification_payload)
    ids: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            ids.append(value)

    raw_ids = data.get("email_ids")
    if isinstance(raw_ids, list):
        for value in raw_ids:
            add(value)
    raw_emails = data.get("emails")
    if isinstance(raw_emails, list):
        for item in raw_emails:
            if isinstance(item, dict):
                add(item.get("id"))
    return ids


def _email_persistent_emails(notification_payload: dict) -> list[dict]:
    data = _email_notification_data(notification_payload)
    raw_emails = data.get("emails")
    if not isinstance(raw_emails, list):
        return []
    emails: list[dict] = []
    for item in raw_emails:
        if not isinstance(item, dict):
            continue
        email = dict(item)
        if (
            email.get("message_truncated") or email.get("preview_truncated")
        ) and not email.get("comment"):
            email["comment"] = NOTIFICATION_PERSISTENT_EMAIL_TRUNCATED_COMMENT
        emails.append(email)
    return emails


def _build_email_notification_persistent_payload(agent, notification_payload: dict) -> dict | None:
    data = _email_notification_data(notification_payload)
    if not data:
        return None

    email_ids = _email_notification_email_ids(notification_payload)
    emails = _email_persistent_emails(notification_payload)
    count = data.get("count")
    newest_received_at = data.get("newest_received_at")
    if not (email_ids or emails):
        return None

    payload: dict = {
        "context_comment": NOTIFICATION_PERSISTENT_EMAIL_CONTEXT_COMMENT,
    }
    if email_ids:
        payload["email_ids"] = email_ids
    if isinstance(count, int):
        payload["count"] = count
    if isinstance(newest_received_at, str) and newest_received_at:
        payload["newest_received_at"] = newest_received_at
    if emails:
        payload["emails"] = emails

    return payload


def _email_persistent_child(holder) -> dict | None:
    """Return the ``notification_persistent.email`` child of a stamped holder.

    ``holder`` is a tool-result content value: a dict, a JSON string (the
    canonical wire shape a restored ``ToolResultBlock.content`` may carry),
    or ``None``. Guards every intermediate level with ``isinstance`` so a
    malformed/foreign shape never raises; returns ``None`` unless the child
    is itself a dict.
    """
    if isinstance(holder, str):
        try:
            holder = _json.loads(holder)
        except ValueError:
            return None
    if not isinstance(holder, dict):
        return None
    meta = holder.get(META_ENVELOPE_KEY)
    if not isinstance(meta, dict):
        return None
    persistent = meta.get(NOTIFICATION_PERSISTENT_KEY)
    if not isinstance(persistent, dict):
        return None
    email = persistent.get(NOTIFICATION_PERSISTENT_EMAIL_CHANNEL)
    return email if isinstance(email, dict) else None


def _email_persistent_child_is_live_snapshot(email: dict | None) -> bool:
    """Whether an email persistent child is a live (non-cleared) snapshot."""
    return isinstance(email, dict) and not email.get(
        NOTIFICATION_PERSISTENT_EMAIL_CLEARED_KEY
    )


def build_email_persistent_cleared_marker() -> dict:
    """The durable tombstone stamped when unread email transitions to zero.

    Carries no message content — only the fact that the snapshot ended and
    when. This is the sole model-facing evidence of the empty transition;
    without it, full-history replay would keep presenting the last nonempty
    snapshot as current (see ``lingtai.llm.interface_converters``).
    """
    return {
        NOTIFICATION_PERSISTENT_EMAIL_CLEARED_KEY: True,
        NOTIFICATION_PERSISTENT_EMAIL_CLEARED_AT_KEY: now_iso_plain(),
    }


def newest_email_snapshot_holder(iface):
    """The single tool-result-like block holding the newest authoritative
    email state.

    Walks ``iface.entries`` in wire order and remembers the LAST block whose
    ``_meta.notification_persistent.email`` is a dict — whether that dict is a
    live nonempty snapshot or an explicit clear tombstone
    (``{"cleared": True, ...}``). Only that last occurrence is authoritative.
    Earlier blocks (nonempty or clear) are superseded **by reading order**,
    not by deletion: full-history replay serializes every holder's
    ``.email`` child exactly as recorded — no wire strip, no per-id or
    whole-block removal (see the provider-context rebuild/replay invariant
    in ``lingtai.llm.interface_converters``). Callers use this helper only
    to decide which child is CURRENT (e.g. whether to append a new
    snapshot/tombstone), never to remove or alter another block's child.
    Returns ``None`` when no block in the history carries an email snapshot
    at all.

    Kernel-owned (moved here from ``lingtai.llm.interface_converters`` —
    ``lingtai.kernel`` may not import the outer ``lingtai.llm`` package; see
    ``tests/test_kernel_isolation.py``): both
    :func:`reconcile_email_persistent_history` and
    :func:`reconcile_email_startup_marker_before_empty_commit` need this
    same traversal at the kernel layer. ``interface_converters`` does NOT
    import or re-export this symbol — its five direct full-history
    renderers serialize ``ToolResultBlock.content`` directly with no
    filtering/newest-holder step of their own (see
    ``tests/test_timely_transient_serialization.py::test_content_is_read_directly_without_intermediate_helpers``,
    which asserts ``newest_email_snapshot_holder`` is absent from
    ``interface_converters``). "Newest wins" here is a reading convention
    the MODEL applies over the full, unfiltered replay, not a converter-side
    selection. Reuses :func:`_email_persistent_child` for
    the per-block parse so the accepted content shapes (dict or JSON
    string; anything unparseable/non-dict yields no email child) stay
    identical to every other email-persistent-child reader in this module —
    ``_is_tool_result_block`` duck-typing avoids the same hard import a
    direct ``ToolResultBlock`` ``isinstance`` check would need.

    Guards every intermediate value with ``isinstance`` (via
    :func:`_email_persistent_child`) so a malformed ``notification_persistent``
    (``None``, a string, a list) or malformed ``email`` value is simply
    skipped rather than raising.
    """
    newest = None
    for entry in iface.entries:
        for block in entry.content or []:
            if not _is_tool_result_block(block):
                continue
            if isinstance(_email_persistent_child(block.content), dict):
                newest = block
    return newest


# ---------------------------------------------------------------------------
# Email whole-snapshot clear-transition owner
#
# A live email persistent snapshot can become superseded-but-unwitnessed in
# two distinct ways, handled by two DIFFERENT mechanisms because they have
# different correctness deadlines:
#
# 1. In-process (deadline: the next dict-shaped tool result). While the
#    process keeps running, `attach_active_notifications` may see email drop
#    out of the live payload but have no dict-shaped tool result this batch
#    to carry the tombstone (or the batch is a context-molt batch that
#    bypasses this function entirely — see `base_agent/turn.py`). There is
#    always a LATER real tool-result carrier in this case (or the process
#    exits, in which case mechanism 2 below takes over on the next start), so
#    `agent._email_pending_clear` — a single bounded flag, not a log — is
#    sufficient: `attach_active_notifications` is the sole consumer, on
#    whatever dict-shaped result next becomes available.
# 2. Cross-restart (deadline: the FIRST full-history render after restart,
#    which can happen before any tool call at all — a flag alone cannot meet
#    this deadline because nothing reads it until a tool result exists).
#    `reconcile_email_persistent_history` runs once, synchronously, right
#    after chat-history restore and before the main message-loop thread is
#    created (`agent._thread`, created later in `base_agent/lifecycle.py::
#    _start`) — so before anything could drive a `session.send()`/render.
#    The heartbeat thread is already started by this point, but it only
#    writes liveness (`agent._heartbeat_runtime_ready` is still `False`) and
#    does not yet run notification sync or signal handling, so it cannot
#    race this reconciliation. When restored history's newest email child
#    does not already match the CURRENT authoritative producer state (read
#    once at this lifecycle boundary — never queried from a converter,
#    compared at the SAME redaction-normalization boundary
#    `_save_chat_history` applies, see `reconcile_email_persistent_history`'s
#    own docstring), it appends exactly one well-paired, narrow startup
#    reconciliation record — NOT byte-shape-identical to a real
#    `_inject_notification_pair` delivery; see
#    `_append_email_reconciliation_pair`'s docstring for exactly what it
#    carries and why the difference is intentional — directly into the
#    restored `ChatInterface` and best-effort persists it via
#    `_save_chat_history` (a save failure is not fatal: the append already
#    happened in memory, so this process's own renders stay correct, and an
#    unsaved restart simply re-derives and re-appends the same conclusion
#    next time). It does NOT touch `agent._notification_live_holder`/
#    `_notification_fp`/`_notification_payload_signature` — those remain the
#    live sync loop's own bookkeeping, established fresh by the first real
#    `_sync_notifications` tick or tool-result batch, exactly as before this
#    function existed; no live-holder/fingerprint/attention/freshness
#    bookkeeping is needed for a fresh process with no live holder to
#    register against and no poisoned prior interface to protect.
# ---------------------------------------------------------------------------


def _note_email_pending_clear(agent) -> None:
    try:
        agent._email_pending_clear = True
    except Exception:
        pass


def _consume_email_pending_clear(agent) -> bool:
    """Return and clear the pending-clear flag (default ``False`` if absent)."""
    pending = bool(getattr(agent, "_email_pending_clear", False))
    try:
        agent._email_pending_clear = False
    except Exception:
        pass
    return pending


def note_email_clear_intent_before_holder_destroyed(agent) -> None:
    """Preserve the clear obligation before a seam that releases the live holder.

    ``skeletonize_notification_holder`` releases tracking of a SYNTHESIZED
    holder — the live IDLE/ASLEEP wake path's own pair shape
    (``_inject_notification_pair``), which can carry a live email persistent
    snapshot exactly like an ordinary tool-result holder can — WITHOUT
    mutating its historical content; the holder's dict stays exactly as
    recorded in canonical history. Losing the LIVE reference still means no
    future code will read this holder as authoritative going forward, so the
    email clear obligation (if any) must be captured here, before that
    release, or it is lost. ``attach_active_notifications`` already captures
    ``was_email_live`` from ``prior_holder`` BEFORE it calls
    ``skeletonize_notification_holder``, so that caller is safe on its own.
    The context-molt path in ``base_agent/turn.py`` is the one caller that
    calls ``skeletonize_notification_holder`` directly with no such capture —
    call this function immediately before that skeletonize call (and ONLY
    there; ``attach_active_notifications`` must not call this, since it
    already performs — and then locally consumes — the equivalent capture
    itself, and calling this too would re-set the flag right after that
    consume).

    Content-free by design: only notes a boolean intent, never copies the
    live holder's email body/ids into any new state. If the flag is later
    consumed while a fresh live email snapshot is already present again,
    the consumer (``attach_active_notifications``) is itself gated on
    ``not email_present_this_round``, so this never manufactures a false
    clear — it can only ever confirm a clear that current producer state
    still supports at consumption time.
    """
    holder = getattr(agent, "_notification_live_holder", None)
    if not (isinstance(holder, dict) and holder.get("_synthesized")):
        return
    if _email_persistent_child_is_live_snapshot(_email_persistent_child(holder)):
        _note_email_pending_clear(agent)


def _build_email_reconciliation_pair_content(current_email_child: dict) -> dict:
    """Build the synthesized pair body carrying the current email state.

    Mirrors the canonical shape ``_inject_notification_pair`` uses for the
    live IDLE/ASLEEP wake path (``{"_synthesized": True, "_meta": {...}}``),
    narrowed to the email lane only: this is a startup reconciliation event,
    not a full multi-channel notification delivery, so it carries only
    ``_meta.notification_persistent.email`` — no ``_meta.notifications``
    high-attention hook (there is nothing new to alert the agent about; the
    email state was already true, just not yet represented in wire history).
    """
    return {
        "_synthesized": True,
        "_meta": {NOTIFICATION_PERSISTENT_KEY: {NOTIFICATION_PERSISTENT_EMAIL_CHANNEL: current_email_child}},
    }


def _append_email_reconciliation_pair(interface, current_email_child: dict) -> bool:
    """Append one well-paired, NARROW startup reconciliation record.

    This is a specialized ``(assistant ToolCallBlock, user ToolResultBlock)``
    pair shaped like the live sync loop's synthesized ``notification`` pair
    (same block types/names, ``synthesized=True`` on the result, appended via
    the same canonical ``iface.add_assistant_message``/``iface.add_tool_results``
    primitives — never a fake user-text message, never a rewrite of an
    existing block), but it is intentionally NOT byte-shape-identical to a
    real ``_inject_notification_pair`` delivery and does not need to be: it
    carries only ``_synthesized`` plus
    ``_meta.notification_persistent.email`` — no ``_meta.notifications``
    high-attention hook, no ``notification_guidance``, no ``build_meta``
    freshness/``injection_seq`` fields, no poison/session-ensure handling,
    and no live-holder/fingerprint/logging side effects. Those all exist in
    the real injector to make a LIVE wake indistinguishable from a voluntary
    agent read and to drive the run loop; this is a narrow, one-shot startup
    correction of already-true state, not a new attention event, so none of
    that bookkeeping is needed — a fresh process has no live holder to
    register against and no poisoned prior interface to protect.

    Refuses to append (returns ``False``) if the wire has unanswered
    tool_calls, exactly like ``_inject_notification_pair`` does, to preserve
    the tool-call/tool-result alternation invariant; the caller treats that
    as "could not reconcile this pass" and leaves history untouched rather
    than risk a malformed wire.
    """
    import secrets

    from .llm.interface import ToolCallBlock, ToolResultBlock

    if interface.has_pending_tool_calls():
        return False

    # A random suffix (not just a timestamp) guarantees a unique id even
    # across repeated calls within the same wall-clock second — two
    # identical ids would make `add_tool_results` treat the second result as
    # a same-id "heal" replacement of the first pair's synthesized result
    # (see `ChatInterface.add_tool_results`), silently merging two distinct
    # reconciliation events into one entry instead of appending a second one.
    call_id = f"notif_reconcile_{secrets.token_hex(8)}"
    call_block = ToolCallBlock(
        id=call_id,
        name="notification",
        args={"action": "check"},
    )
    result_block = ToolResultBlock(
        id=call_id,
        name="notification",
        content=_build_email_reconciliation_pair_content(current_email_child),
        synthesized=True,
    )
    interface.add_assistant_message(content=[call_block])
    interface.add_tool_results([result_block])
    return True


# `reconcile_email_persistent_history` outcomes. The pre-render policy is:
# `RECONCILE_UNRESOLVED` means the caller (`base_agent/lifecycle.py::_start`)
# could not establish canonical email state before the first render — this
# is a FAIL-CLOSED barrier, not merely logged: `_start()` raises
# `EmailReconciliationUnresolvedError` before `agent._thread` is created,
# rolling back the one background resource already running (heartbeat) via
# `_stop_heartbeat`, so `agent.start()` itself fails and no render is
# possible for that process. There is no retry loop; the standing
# `agent._email_pending_clear` bit plus the next in-process carrier remain
# the one bounded fallback for the narrow pending-tool-call case (see the
# module comment above), which is already sufficient without inventing new
# machinery.
RECONCILE_RECONCILED = "reconciled"  # a record was appended (save may still fail; see docstring)
RECONCILE_NOOP = "noop"  # history already matches current producer state
RECONCILE_UNRESOLVED = "unresolved"  # could not read state or could not append


# Private sentinel distinguishing "read/parse failed" from a genuine, valid
# empty snapshot in :func:`_collect_active_notifications_payload_or_failure`.
# Only :func:`reconcile_email_persistent_history` needs this distinction (a
# failure there must never be treated as authoritative zero and must never
# drive a false clear tombstone); the existing best-effort ACTIVE-turn caller
# (:func:`attach_active_notifications`) keeps its unchanged two-way contract
# via the thin wrapper below — collapsing the sentinel back to ``None`` is
# correct there since "no payload to stamp" is its entire existing behavior
# for both cases, not a new distinction it needs. This does NOT change or fix
# the lower POSIX per-file malformed-skip characterization
# (``NotificationStorePort.snapshot`` silently skips an individual malformed
# channel file and returns whatever else parsed) — that pre-existing,
# documented behavior is out of scope here; this sentinel only distinguishes
# the case where the ``.snapshot()`` CALL ITSELF raises.
_NOTIFICATION_COLLECTION_FAILED = object()


def _collect_active_notifications_payload_or_failure(agent):
    """Return the canonical active notification payload, or the failure sentinel.

    Reads ``.notification/*.json`` via the agent's notification store and wraps
    it with the same guidance fields used by the synthesized notification pair.
    Returns ``None`` for a genuine, successfully-read empty snapshot (no active
    channels), or :data:`_NOTIFICATION_COLLECTION_FAILED` when the read/parse
    itself raised — the two are NOT interchangeable: only the former is
    authoritative "currently no unread state."
    """
    try:
        from .notifications import is_channel_allowed

        notifications = agent._notification_store.snapshot(is_channel_allowed)
        if not notifications:
            return None
        return build_notification_payload(notifications)
    except Exception:
        return _NOTIFICATION_COLLECTION_FAILED


def reconcile_email_persistent_history(agent) -> str:
    """Idempotent first-post-startup/refresh bridge for the email lane.

    Must run once, synchronously, after chat history is restored and BEFORE
    the first full-history render (called from
    ``base_agent/lifecycle.py::_start``, right after ``restore_chat`` and
    before ``agent._thread`` — the main message loop — is created). A fresh
    process has no live ``_notification_live_holder``, and — critically —
    the very first model-facing render can happen before any tool call at
    all, so a mere in-memory flag consumed by ``attach_active_notifications``
    is NOT sufficient here (that mechanism remains correct for later
    in-process transitions — see the module comment above). This function
    instead makes the authoritative email state ALREADY present in canonical
    wire history before any renderer runs, by appending a well-paired,
    NARROW startup reconciliation record when needed — see
    :func:`_append_email_reconciliation_pair` for exactly what it carries and
    how it honestly differs from a real ``_inject_notification_pair``
    delivery (it is not byte-shape-identical, and does not need to be).

    Returns one of :data:`RECONCILE_RECONCILED`, :data:`RECONCILE_NOOP`, or
    :data:`RECONCILE_UNRESOLVED` — see those constants' docstrings for the
    pre-render policy. Never silently claims success on a source-read
    failure or a pending-tool-call append refusal. Absent durable
    ``history/chat_history.jsonl`` proves absent HISTORICAL email state only
    — it does NOT prove absent CURRENT state, since the producer's
    ``.notification/email.json`` is independent of whether this agent has
    ever exchanged a message. A brand-new agent is therefore still checked
    against current producer state before any no-op decision; if current
    unread exists, a chat session is bootstrapped (via the same
    ``ensure_session()`` primitive :func:`attach_active_notifications`'s
    live IDLE/ASLEEP wake path already uses) so the snapshot lands in the
    SAME canonical interface the first provider send serializes from, not
    merely a disk-only record. Only when current state is ALSO genuinely
    empty is a brand-new agent a true no-op. A history file that DOES exist
    but produced no restored interface is unresolved instead of a no-op,
    since real historical email state may be sitting unseen on disk.

    Comparison: the newest historical ``notification_persistent.email``
    child (:func:`newest_email_snapshot_holder`) versus the CURRENT
    authoritative producer state
    (:func:`_collect_active_notifications_payload_or_failure` — never a
    converter/disk query; its private failure sentinel is checked before any
    no-op/clear/append decision, so a read/build failure is never mistaken
    for authoritative zero), both normalized through
    ``trace_redaction.redact_for_trajectory`` before comparing. A historical
    child was necessarily redacted by the prior ``_save_chat_history`` call;
    the fresh current child is raw. Comparing them raw would churn a new
    record on every restart for any secret-shaped unread body, since raw and
    redacted forms of the same content never match. Redacting the fresh side
    the same deterministic, idempotent way makes the comparison stable
    without a second redactor, weaker redaction, or a stored raw fingerprint.
    No email anywhere (history or current) is a no-op; an exact match after
    normalization is a no-op; otherwise exactly one record — the current live
    snapshot, or an explicit clear tombstone when current is absent — is
    appended and best-effort saved (a save failure only affects durability
    across a FURTHER restart before this process's own next successful save;
    it does not affect this process's own renders, which are already correct
    in memory, and a still-unsaved restart simply re-derives the same
    conclusion next time).
    """
    try:
        from .trace_redaction import redact_for_trajectory

        chat = getattr(agent, "_chat", None)
        interface = getattr(chat, "interface", None)
        history_file = None
        try:
            working_dir = getattr(agent, "_working_dir", None)
            if working_dir is not None:
                history_file = working_dir / "history" / "chat_history.jsonl"
        except Exception:
            history_file = None
        history_file_exists = history_file is not None and history_file.is_file()

        if interface is None and history_file_exists:
            # A history file DOES exist but produced no restored interface
            # (restore raised and was caught upstream in
            # ``lifecycle.py::_start``): real historical email state may be
            # sitting unseen on disk — genuinely unresolved, not safe to
            # treat as "nothing to do."
            return RECONCILE_UNRESOLVED

        # No durable ``history/chat_history.jsonl`` (or a chat session
        # already exists): there is no HISTORICAL email state to compare
        # against either way. But absent history does not prove absent
        # CURRENT state — the producer's ``.notification/email.json`` is
        # independent of whether this agent has ever exchanged a message,
        # so current state must still be inspected before any first render
        # is possible. Read it before deciding whether a session needs
        # bootstrapping at all, so a genuinely idle producer never pays for
        # an unnecessary session creation.
        payload = _collect_active_notifications_payload_or_failure(agent)
        if payload is _NOTIFICATION_COLLECTION_FAILED:
            # A source-read/parse failure is NOT authoritative zero: unlike the
            # best-effort ACTIVE-turn path, reconciliation must never let a
            # failed read masquerade as "current unread state is empty" and
            # append a false clear tombstone over a real historical live
            # snapshot. Bail out before any no-op/clear/append decision.
            return RECONCILE_UNRESOLVED
        current_email = (
            _build_email_notification_persistent_payload(agent, payload)
            if isinstance(payload, dict)
            else None
        )

        if interface is None:
            if current_email is None:
                # No history, and current producer state is genuinely
                # empty: nothing to compare or bootstrap. No tombstone is
                # necessary for state that was never live in this agent's
                # canonical history at all.
                return RECONCILE_NOOP
            # Current unread exists before this agent has ever restored or
            # created a chat session. Bootstrap the SAME session
            # `_handle_request`'s first `agent._session.send(...)` will
            # reuse (`ensure_session()` is idempotent — a later call from
            # the turn loop is a no-op once `agent._chat` is set), then
            # append into that session's interface via the existing
            # reconciliation-record path below, so the current snapshot is
            # already in the canonical wire history the first provider
            # send serializes from — not a disk-only record nothing
            # rehydrates. Mirrors `_inject_notification_pair`'s own
            # on-demand `ensure_session()` call (the live IDLE/ASLEEP wake
            # path), reusing the same primitive rather than a second one.
            agent._session.ensure_session()
            interface = getattr(agent._chat, "interface", None)
            if interface is None:
                # ensure_session() itself failed to produce a usable
                # interface (should not happen in practice — mirrors
                # _inject_notification_pair's own defensive check).
                return RECONCILE_UNRESOLVED

        historical_email = _email_persistent_child(
            getattr(newest_email_snapshot_holder(interface), "content", None)
        )
    except Exception:
        return RECONCILE_UNRESOLVED

    if historical_email is None and current_email is None:
        return RECONCILE_NOOP  # never manufacture a pair out of nothing

    historical_is_live = _email_persistent_child_is_live_snapshot(historical_email)
    redacted_current_email = None
    if current_email is not None:
        # Current authoritative state is a live snapshot. No-op only when the
        # newest historical child is ALREADY exactly this live snapshot
        # (idempotent re-run / already reconciled). Any other historical
        # state (absent, a clear tombstone, or a different/older live
        # snapshot) needs the current snapshot appended so it becomes the
        # newest wire-visible state.
        #
        # Compare at the SAME durable-normalization boundary
        # `_save_chat_history` already applies (`redact_for_trajectory`),
        # not raw equality: `historical_email` was read back from disk
        # AFTER that redaction ran on the previous save, while
        # `current_email` is freshly rebuilt raw from the live producer
        # store. A secret-shaped substring (token/bearer/password-like)
        # would compare unequal forever otherwise -- appending a new pair,
        # re-saving another redacted copy, on every single restart, even
        # though the producer's authoritative unread state never changed.
        # `redact_for_trajectory` is a pure, deterministic function of the
        # value (idempotent on already-redacted input), so redacting the
        # fresh current snapshot the same way makes the two sides directly
        # comparable without inventing a second redactor, weakening
        # redaction, or persisting/hashing any raw secret.
        redacted_current_email = redact_for_trajectory(current_email)
        if historical_is_live and redacted_current_email == historical_email:
            return RECONCILE_NOOP
        to_append = current_email
    else:
        # Current authoritative state is "no unread email". No-op when
        # history already ends on a clear tombstone (or has no email child
        # at all, handled above). Only a historical LIVE snapshot needs the
        # explicit clear appended.
        if not historical_is_live:
            return RECONCILE_NOOP  # already clear (or no email ever existed)
        to_append = build_email_persistent_cleared_marker()

    if not _append_email_reconciliation_pair(interface, to_append):
        # Wire has unanswered tool_calls at this lifecycle point (should not
        # happen for a freshly restored, quiescent history, but refuse
        # rather than risk a malformed pairing). Fall back to the pending
        # flag so the next in-process valid carrier still resolves the
        # live-to-absent case; a genuinely new live snapshot will still be
        # picked up correctly by the ordinary first-active-payload attach.
        if current_email is None:
            _note_email_pending_clear(agent)
        return RECONCILE_UNRESOLVED

    if redacted_current_email is not None:
        # Record what was just appended (redaction-normalized, matching what
        # a restored/re-read historical child would look like) so the first
        # real `_inject_notification_pair` delivery after this loop starts
        # can tell "current producer state is unchanged since reconciliation
        # already recorded it" and skip re-appending the SAME email snapshot
        # as a second persistent-history record — see
        # `consume_email_startup_reconciled_snapshot`. Only the redundant
        # email re-append is skipped; that first delivery's normal transient
        # attention/wake still fires for any other channel.
        agent._email_startup_reconciled_snapshot = redacted_current_email

    try:
        agent._save_chat_history()
    except Exception:
        pass  # best-effort; see docstring's save-failure note
    return RECONCILE_RECONCILED


def consume_email_startup_reconciled_snapshot(
    agent, notification_persistent_payload: dict | None
) -> None:
    """Drop a redundant ``email`` child this delivery already recorded at startup.

    ``reconcile_email_persistent_history`` may append the current
    authoritative email snapshot directly into restored history before the
    main loop exists (see that function's docstring). If producer state is
    STILL exactly that same snapshot by the time the first real
    ``_inject_notification_pair`` delivery fires, re-including ``email`` in
    ``notification_persistent_payload`` would duplicate it as a second
    persistent-history record for state the agent already has.

    Genuinely ONE-SHOT: this is the sole consumer of ``agent._email_startup_
    reconciled_snapshot`` and always clears it — matched or not — on the
    FIRST live-sync handoff attempt, whether that attempt carries a matching
    email, a different/changed email, no email at all, or only sibling
    persistent lanes. A marker left alive after a mismatch would otherwise
    survive to wrongly suppress a LATER delivery that happens to return to
    the original startup snapshot (e.g. state moves A -> B -> A again), even
    though canonical history had already moved past it. Only the email-drop
    decision itself is conditional: the child is removed from
    ``notification_persistent_payload`` (mutated in place; every other key —
    ``mcp`` IM lanes, or a DIFFERENT/newer email snapshot — is untouched)
    only when it matches the consumed marker at the same redaction-normalized
    boundary reconciliation used to build it; the normal transient
    attention/wake this delivery fires is unaffected either way.
    """
    marker = getattr(agent, "_email_startup_reconciled_snapshot", None)
    try:
        agent._email_startup_reconciled_snapshot = None
    except Exception:
        pass
    if marker is None or not notification_persistent_payload:
        return
    persistent = notification_persistent_payload.get(NOTIFICATION_PERSISTENT_KEY)
    if not isinstance(persistent, dict):
        return
    current_email = persistent.get(NOTIFICATION_PERSISTENT_EMAIL_CHANNEL)
    if not isinstance(current_email, dict):
        return

    from .trace_redaction import redact_for_trajectory

    if redact_for_trajectory(current_email) != marker:
        return

    new_persistent = {k: v for k, v in persistent.items() if k != NOTIFICATION_PERSISTENT_EMAIL_CHANNEL}
    if new_persistent:
        notification_persistent_payload[NOTIFICATION_PERSISTENT_KEY] = new_persistent
    else:
        notification_persistent_payload.pop(NOTIFICATION_PERSISTENT_KEY, None)


def reconcile_email_startup_marker_before_empty_commit(agent) -> bool:
    """Advance a still-armed startup email marker before an empty-collection
    fingerprint commit — the ONLY other seam (besides
    :func:`consume_email_startup_reconciled_snapshot`) that must resolve
    ``agent._email_startup_reconciled_snapshot`` before it can be considered
    handled.

    ``reconcile_email_persistent_history`` may append startup snapshot A into
    canonical history and arm the marker before the main loop exists. If, by
    the time ``_sync_notifications`` next runs, the CURRENT authoritative
    collection is already empty (this function's caller has ALREADY read
    that fact — see below), that call takes the empty-collection branch,
    which never drives ``_inject_notification_pair``/
    :func:`consume_email_startup_reconciled_snapshot` at all. Left alone,
    canonical history would keep ending on live A forever with the marker
    stuck armed: the next render shows stale unread A as current, and a
    LATER real live-A delivery could even be wrongly suppressed by the
    stranded marker.

    Caller contract: ``_sync_notifications`` calls this ONLY inside its own
    ``if not notifications:`` branch, i.e. only after it has ALREADY
    observed the authoritative collection to be empty via its own
    ``store.snapshot()`` read this tick. This function never re-reads the
    producer itself — the caller's already-observed empty snapshot is the
    input fact, so a second disk read cannot race and observe a DIFFERENT
    state than the one the caller is about to commit the fingerprint for.

    Returns ``True`` when it is safe for the caller to commit the empty
    fingerprint (nothing was pending, or the pending marker was resolved:
    consumed and, if a live snapshot was still canonical, replaced by
    exactly one clear tombstone). Returns ``False`` when the wire has
    pending tool calls and the tombstone could not be appended — the caller
    must NOT commit the fingerprint (nor consider the marker resolved) so a
    later valid tick retries against the same still-armed marker, exactly
    like :func:`reconcile_email_persistent_history`'s own pending-tool-calls
    refusal. Never appends a false clear: only a canonical newest email
    child that is still a LIVE (non-cleared) snapshot triggers an append;
    an already-cleared or absent newest child is a pure no-op that still
    consumes the marker (nothing further to do — the marker predates state
    that has already resolved itself).
    """
    marker = getattr(agent, "_email_startup_reconciled_snapshot", None)
    if marker is None:
        return True

    chat = getattr(agent, "_chat", None)
    interface = getattr(chat, "interface", None)
    if interface is None:
        # No live interface to reconcile against (should not happen once a
        # marker is armed, since arming it requires one — defensive only).
        agent._email_startup_reconciled_snapshot = None
        return True

    historical_email = _email_persistent_child(
        getattr(newest_email_snapshot_holder(interface), "content", None)
    )
    if not _email_persistent_child_is_live_snapshot(historical_email):
        # Canonical history already ends on a clear tombstone (or never had
        # an email child at all) — the empty collection this tick observed
        # is already reflected; nothing to append.
        agent._email_startup_reconciled_snapshot = None
        return True

    if not _append_email_reconciliation_pair(interface, build_email_persistent_cleared_marker()):
        # Pending tool calls at this lifecycle point: leave the marker
        # armed and let the caller retry on a later tick rather than
        # silently claiming the empty state was recorded.
        return False

    agent._email_startup_reconciled_snapshot = None
    try:
        agent._save_chat_history()
    except Exception:
        pass  # best-effort; matches reconcile_email_persistent_history's own note
    return True


def _build_snapshot_im_persistent_payload(
    notification_payload: dict,
    lane: _ImPersistentLane,
    candidates: list[dict],
    events: list[dict],
) -> dict:
    """Build a snapshot (email-style) persistent payload for one IM lane.

    Every block carries the producer's current bounded conversation context in
    full under a standing ``context_comment``.  There is no delivered-id delta
    tracking, no ``previous_block`` hook, and no burst/seed comments: the
    snapshot lane re-emits the producer's current window each material update
    and the producer tool remains the source of truth (building this block marks
    nothing read).  Per-message continuity/truncation/media comments are applied
    via the shared ``_annotate_im_message`` helper.
    """
    annotated = [_annotate_im_message(message, lane) for message in candidates]
    payload: dict = {
        "context_comment": lane.snapshot_context_comment,
        "messages": annotated,
    }
    count = _im_notification_event_count(notification_payload, lane.source_key)
    if count:
        payload["count"] = count
    if events:
        payload["events"] = events
    return payload


def _build_im_notification_persistent_payload(
    agent, notification_payload: dict, lane: _ImPersistentLane
) -> dict | None:
    """Build the `_meta.notification_persistent` payload for one IM lane.

    ``mode == "delta"`` lanes (Telegram, WeChat, Feishu) use the seed/delta
    shape: the first delivery after startup/molt (or when fewer than the minimum
    number of messages has been delivered into the current provider context)
    carries the recent context snapshot, and later material notification updates
    only carry messages whose producer message IDs have not been delivered yet,
    plus a ``previous_block`` hook pointing at the prior block for this lane.

    ``mode == "snapshot"`` lanes (WhatsApp, email-style) re-emit the producer's
    current bounded context in full on every material update, with no
    delivered-id state and no ``previous_block`` hook; see
    ``_build_snapshot_im_persistent_payload``.
    """
    candidates = _im_persistent_messages_from_notifications(
        notification_payload, lane.source_key
    )
    events = _im_persistent_events_from_notifications(
        notification_payload, lane.source_key
    )
    if not candidates and not events:
        return None

    if lane.mode == "snapshot":
        return _build_snapshot_im_persistent_payload(
            notification_payload, lane, candidates, events
        )

    delivered = getattr(agent, lane.delivered_ids_attr, [])
    if not isinstance(delivered, (list, tuple, set)):
        delivered = []
    delivered_ids = {msg_id for msg_id in delivered if isinstance(msg_id, str)}
    previous_tool_id = getattr(agent, lane.last_tool_id_attr, None)
    has_previous_block = isinstance(previous_tool_id, str)
    # Provider context can be fresh after molt/restart even when an in-memory
    # delivered-id cache survived.  Only treat delivered_ids as enough recent
    # context when the current provider context also has a previous persistent
    # block for this lane to link to.
    has_recent_context = (
        has_previous_block
        and len(delivered_ids) >= lane.min_context
    )

    is_seed_block = False
    if candidates and has_recent_context:
        messages = [
            message
            for message in candidates
            if isinstance(message.get("id"), str) and message["id"] not in delivered_ids
        ]
    elif candidates:
        messages = candidates[-lane.min_context:]
        is_seed_block = True
    else:
        messages = []

    if not messages and not events:
        return None

    # Newly-arrived (not previously delivered) incoming messages drive the burst
    # hint; annotate per-message continuity/truncation comments before the count.
    new_incoming = 0
    annotated_messages: list[dict] = []
    for message in messages:
        annotated_messages.append(_annotate_im_message(message, lane))
        msg_id = message.get("id")
        if (
            message.get("direction") == "incoming"
            and isinstance(msg_id, str)
            and msg_id not in delivered_ids
        ):
            new_incoming += 1
    messages = annotated_messages

    lane_payload: dict = {"messages": messages}

    # Seed blocks describe the historical range and the current/new message so
    # the agent does not have to re-derive which id is new from the raw list.
    if is_seed_block:
        range_comment = _im_range_context_comment(messages, lane.display_name)
        if range_comment:
            lane_payload["context_comment"] = range_comment

    # Burst: multiple genuinely new incoming messages arrived together.  Seed
    # blocks carry historical preview-window context, so do not count those
    # historical messages as a burst unless the producer's notification count
    # says multiple new events triggered this block.
    event_count = _im_notification_event_count(notification_payload, lane.source_key)
    if (not is_seed_block and new_incoming >= 2) or event_count >= 2:
        lane_payload["burst_comment"] = lane.burst_comment

    # Full referenced reply target(s) missing from the messages list — only for
    # lanes whose producer attaches referenced_messages (currently Telegram).
    if lane.referenced_comment is not None:
        referenced = _im_referenced_messages_from_notifications(
            notification_payload, lane.source_key
        )
        present_ids = {
            message.get("id")
            for message in messages
            if isinstance(message.get("id"), str)
        }
        referenced_out: list[dict] = []
        for ref in referenced:
            if ref.get("id") in present_ids:
                continue
            annotated = _annotate_im_message(ref, lane)
            existing = annotated.get("comment")
            if isinstance(existing, str) and existing:
                annotated["comment"] = f"{lane.referenced_comment} {existing}"
            else:
                annotated["comment"] = lane.referenced_comment
            referenced_out.append(annotated)
        if referenced_out:
            lane_payload["referenced_messages"] = referenced_out

    if events:
        lane_payload["events"] = events

    # Every persistent block carries an explicit hook to the previous block for
    # its lane (Jason #6148). The first block after startup/molt has no
    # predecessor: it is marked `is_first_block: true` with `tool_result_id: null`.
    # Later blocks point `tool_result_id` at the prior tool result id.
    is_first_block = not has_previous_block
    previous_block: dict = {
        "path": lane.path,
        "tool_result_id": previous_tool_id if has_previous_block else None,
    }
    if is_first_block:
        previous_block["is_first_block"] = True
    else:
        previous_block["comment"] = (
            f"For earlier {lane.display_name} context, see tool result "
            f"{previous_tool_id} at {lane.path}."
        )
    lane_payload["previous_block"] = previous_block

    return lane_payload


def build_notification_persistent_payload(agent, notification_payload: dict) -> dict | None:
    persistent: dict = {}

    email_payload = _build_email_notification_persistent_payload(
        agent, notification_payload
    )
    if email_payload is not None:
        persistent[NOTIFICATION_PERSISTENT_EMAIL_CHANNEL] = email_payload

    for lane in _IM_PERSISTENT_LANES:
        lane_payload = _build_im_notification_persistent_payload(
            agent, notification_payload, lane
        )
        if lane_payload is not None:
            persistent.setdefault(NOTIFICATION_PERSISTENT_MCP_KEY, {})[
                lane.channel
            ] = lane_payload

    if not persistent:
        return None
    return {NOTIFICATION_PERSISTENT_KEY: persistent}


def _record_im_persistent_delivery(
    agent,
    lane_payload: dict,
    lane: _ImPersistentLane,
    *,
    tool_call_id: str | None,
) -> None:
    """Record one IM lane's delivered message ids and previous-block hook.

    Snapshot lanes (``delivered_ids_attr`` / ``last_tool_id_attr`` is ``None``)
    keep no in-memory delivery state and are skipped.
    """
    if lane.delivered_ids_attr is None or lane.last_tool_id_attr is None:
        return
    messages = lane_payload.get("messages")
    if not isinstance(messages, list):
        return

    existing = getattr(agent, lane.delivered_ids_attr, [])
    if not isinstance(existing, list):
        existing = list(existing) if isinstance(existing, (tuple, set)) else []
    seen = set(existing)
    for message in messages:
        if not isinstance(message, dict):
            continue
        msg_id = message.get("id")
        if isinstance(msg_id, str) and msg_id and msg_id not in seen:
            existing.append(msg_id)
            seen.add(msg_id)
    if len(existing) > lane.seen_limit:
        existing = existing[-lane.seen_limit:]
    try:
        setattr(agent, lane.delivered_ids_attr, existing)
        if tool_call_id:
            setattr(agent, lane.last_tool_id_attr, tool_call_id)
    except Exception:
        pass


def record_notification_persistent_delivery(
    agent,
    notification_persistent_payload: dict | None,
    *,
    tool_call_id: str | None,
) -> None:
    """Record persistent notification context delivered to provider context."""
    if not notification_persistent_payload:
        return
    persistent = notification_persistent_payload.get(NOTIFICATION_PERSISTENT_KEY)
    if not isinstance(persistent, dict):
        return

    mcp = persistent.get(NOTIFICATION_PERSISTENT_MCP_KEY)
    if not isinstance(mcp, dict):
        return
    for lane in _IM_PERSISTENT_LANES:
        lane_payload = mcp.get(lane.channel)
        if isinstance(lane_payload, dict):
            _record_im_persistent_delivery(
                agent, lane_payload, lane, tool_call_id=tool_call_id
            )


def _im_notification_message_ids(
    notification_payload: dict, source_key: str
) -> list[str]:
    """Return stable IM event IDs for the transient high-attention hook."""
    message_ids: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            message_ids.append(value)

    for event in _im_persistent_events_from_notifications(
        notification_payload, source_key
    ):
        if isinstance(event, dict):
            add(event.get("message_ref"))

    notifications = notification_payload.get(NOTIFICATIONS_KEY)
    channel = notifications.get(source_key) if isinstance(notifications, dict) else None
    data = channel.get("data") if isinstance(channel, dict) else None
    if not isinstance(data, dict):
        return message_ids

    # Fallback for older/partial payloads that have structured messages but no
    # event hook.  Keep only IDs; content and routing details stay persistent.
    for preview in _im_preview_list(notification_payload, source_key):
        if isinstance(preview, dict):
            add(preview.get("message_ref"))
            latest = preview.get("latest_incoming")
            if isinstance(latest, dict):
                add(latest.get("id"))

    return message_ids


def _sanitize_im_notification_after_persistent(
    notification_payload: dict, lane: _ImPersistentLane
) -> None:
    """Reduce one IM lane's ephemeral block to a minimal event identity hook.

    Message text, structured context, routing hooks, sender/subject, platform,
    counts, and summaries live under the lane's
    ``_meta.notification_persistent.mcp.<channel>`` path.  The transient
    ``_meta.notifications.mcp.<channel>`` block remains only a short
    high-attention/progressive-disclosure hook that names the producer event IDs
    requiring explicit handling through the producer tool.

    No-op when there is no notification for this lane. Safe to call
    unconditionally.
    """
    notifications = notification_payload.get(NOTIFICATIONS_KEY)
    if not isinstance(notifications, dict):
        return
    channel = notifications.get(lane.source_key)
    if not isinstance(channel, dict):
        return

    minimal_data: dict = {
        "message_ids": _im_notification_message_ids(
            notification_payload, lane.source_key
        )
    }

    # Preserve generic notification scaffolding (icon / priority / published_at)
    # but replace all channel content/summary fields with the standard LICC
    # transient hook: event identity in data, content/context in persistent.
    channel["header"] = f"{lane.display_name} event"
    channel["data"] = minimal_data
    channel["instructions"] = (
        f"High-attention {lane.display_name} hook: use notification_persistent "
        "for content/context; when handled, dismiss this notification."
    )


def sanitize_telegram_notification_after_persistent(notification_payload: dict) -> None:
    """Reduce Telegram's ephemeral lane to a minimal event identity hook."""
    _sanitize_im_notification_after_persistent(
        notification_payload, _TELEGRAM_PERSISTENT_LANE
    )


def sanitize_wechat_notification_after_persistent(notification_payload: dict) -> None:
    """Reduce WeChat's ephemeral lane to a minimal event identity hook."""
    _sanitize_im_notification_after_persistent(
        notification_payload, _WECHAT_PERSISTENT_LANE
    )


def sanitize_feishu_notification_after_persistent(notification_payload: dict) -> None:
    """Reduce Feishu's ephemeral lane to a minimal event identity hook."""
    _sanitize_im_notification_after_persistent(
        notification_payload, _FEISHU_PERSISTENT_LANE
    )


def sanitize_whatsapp_notification_after_persistent(notification_payload: dict) -> None:
    """Reduce WhatsApp's ephemeral lane to a minimal event identity hook."""
    _sanitize_im_notification_after_persistent(
        notification_payload, _WHATSAPP_PERSISTENT_LANE
    )


def _result_tool_call_id(result: dict) -> str | None:
    meta = result.get("_meta")
    if not isinstance(meta, dict):
        return None
    tool_meta = meta.get(TOOL_META_KEY)
    if not isinstance(tool_meta, dict):
        return None
    call_id = tool_meta.get("id")
    return call_id if isinstance(call_id, str) and call_id else None


def build_synthetic_tool_meta(
    call_id: str,
    *,
    char_count: int = 0,
    elapsed_ms: int = 0,
) -> dict:
    """Return a minimal synthetic ``tool_meta`` block for the IDLE/ASLEEP pair.

    The synthesized ``notification(action="check")`` pair has no real tool
    execution, so :class:`ToolExecutor._attach_tool_block` never stamps a
    ``_meta.tool_meta`` block on it.  The ``/notification`` history view still
    wants a ``tool_meta`` block to render, so this builds a parallel one carrying
    the same identity fields a real ``tool_meta`` has (id/timestamp/char_count/
    elapsed_ms) plus a ``synthetic: True`` marker that distinguishes it from a
    real tool result's permanent block.
    """
    return {
        "id": call_id or "<unknown>",
        "timestamp": now_iso_plain(),
        "char_count": int(char_count),
        "elapsed_ms": int(elapsed_ms),
        "synthetic": True,
    }


def build_synthetic_meta_envelope(
    agent,
    notification_payload: dict,
    *,
    call_id: str,
) -> dict:
    """Assemble the full ``_meta`` envelope for a synthesized notification pair.

    Produces the same ``_meta`` envelope an ACTIVE tool result persists:

      * ``tool_meta``            — synthetic identity (see
        :func:`build_synthetic_tool_meta`)
      * ``agent_meta``           — current ``build_meta`` snapshot
      * ``guidance``             — lightweight ref to the resident
        ``meta_guidance`` system-prompt section (see
        :func:`build_meta_guidance_ref`)
      * ``notifications`` +
        ``notification_guidance``— from ``notification_payload`` (the dict
        returned by :func:`build_notification_payload`)

    Used only for the durable ``notification_block_injected`` snapshot so the TUI
    ``/notification`` view shows the same ``_meta.*`` blocks for synthesized
    pairs as for ACTIVE tool results.  The live wire body keeps its own
    (notification-only) ``_meta`` — this is a logging-side reconstruction.
    """
    try:
        agent_meta = build_meta(agent)
        # Token diagnostics never ride on agent_meta — pull the unified
        # token_usage block out of the transit key so it can be stamped onto the
        # synthetic tool_meta instead (Jason FINAL: all token diagnostics live in
        # tool_meta.token_usage only).
        token_usage = agent_meta.pop(TOOL_META_TOKEN_USAGE_PENDING_KEY, None)
        # Tool-meta context/reminder transit keys are consumed only by real
        # ToolExecutor tool-result stamping.  Synthetic notification snapshots
        # are log-side reconstructions, so do not expose internal transit
        # payloads as agent_meta.
        agent_meta.pop(TOOL_META_CONTEXT_PENDING_KEY, None)
        agent_meta.pop(TOOL_META_CONTEXT_EVENT_PENDING_KEY, None)
    except (AttributeError, TypeError):
        agent_meta = {}
        token_usage = None

    tool_meta = build_synthetic_tool_meta(call_id)
    if isinstance(token_usage, dict) and token_usage:
        tool_meta[TOOL_META_TOKEN_USAGE_KEY] = token_usage

    envelope: dict = {
        TOOL_META_KEY: tool_meta,
        AGENT_META_KEY: agent_meta,
        GUIDANCE_KEY: build_meta_guidance_ref(),
    }
    # notifications + notification_guidance from the canonical payload.
    envelope.update(notification_payload)
    return envelope


def _collect_active_notifications_payload(agent) -> dict | None:
    """Return the canonical active notification payload.

    Best-effort two-way contract for the ACTIVE-turn stamping path
    (:func:`attach_active_notifications`): returns ``None`` when there are no
    active channels OR when the read/parse failed — callers on this path
    treat ``None`` as "do not stamp" either way, exactly as before. Startup
    reconciliation needs the sharper distinction and calls
    :func:`_collect_active_notifications_payload_or_failure` directly instead.
    """
    result = _collect_active_notifications_payload_or_failure(agent)
    return None if result is _NOTIFICATION_COLLECTION_FAILED else result


def _last_dict_result(tool_results: list) -> dict | None:
    """Return the dict carried by the latest tool-result block in ``tool_results``.

    Adapter-built ToolResultBlocks store the tool's return value in
    ``.content``. The notification stamp is only meaningful when that content
    is a dict (the JSON shape the agent already parses); other shapes
    (e.g. a string from a tool that returned text) are skipped. Walks
    backward from the tail so the freshest dict result wins even when
    later tools returned non-dicts.
    """
    for block in reversed(tool_results):
        content = getattr(block, "content", None)
        if isinstance(content, dict):
            return content
    return None


def _stamp_email_cleared_marker_on_target(meta: dict) -> None:
    """Stamp the durable email-cleared tombstone directly into a ``_meta`` dict.

    Shared by both :func:`_stamp_email_cleared_marker_if_possible` (which
    resolves ``meta`` from a batch's dict result first) and
    :func:`attach_active_notifications`'s material-change branch (which
    already has the ``_meta`` dict in hand via :func:`_meta_block`).
    """
    persistent = meta.get(NOTIFICATION_PERSISTENT_KEY)
    if not isinstance(persistent, dict):
        persistent = {}
        meta[NOTIFICATION_PERSISTENT_KEY] = persistent
    persistent[NOTIFICATION_PERSISTENT_EMAIL_CHANNEL] = (
        build_email_persistent_cleared_marker()
    )


def _stamp_email_cleared_marker_if_possible(tool_results: list) -> bool:
    """Stamp the durable email-cleared tombstone on this batch's dict result.

    Used from the "no active notifications at all" branch of
    :func:`attach_active_notifications`. If no dict-shaped tool result is
    available this batch, returns ``False`` and stamps nothing — the CALLER
    is responsible for retaining the pending-clear intent
    (``_note_email_pending_clear``) rather than discarding it, so a later
    batch with a dict result (or a future restart's
    ``reconcile_email_persistent_history``) still sees and consumes it.
    Returns whether the marker was stamped.
    """
    target = _last_dict_result(tool_results)
    if target is None:
        return False
    _stamp_email_cleared_marker_on_target(_meta_block(target))
    return True


def skeletonize_notification_holder(agent) -> None:
    """Release the live notification holder without mutating its history.

    The live holder (``agent._notification_live_holder``) is a dict that is
    shared by reference with a historical ``ToolResultBlock.content`` already
    appended to canonical ``ChatInterface`` entries — possibly already sent to
    a provider. Both normal tool-result holders and synthesized pair holders
    are simply RELEASED from live tracking here: this function never mutates
    the dict's keys. Notification payloads are timely transient state (Jason
    #4307): canonical history is never retroactively stripped or rewritten
    when the payload moves or disappears; only the newest emitted holder
    (by wire/reading order — see ``newest_email_snapshot_holder`` for the
    email lane's own instance of this convention) is current. Model-facing
    full-history serialization preserves every holder's content unchanged,
    synthesized or not (see ``lingtai.llm.interface_converters``).

    After this call ``agent._notification_live_holder`` is ``None``.
    Called by:
    * The IDLE/ASLEEP inject path before stamping the new synthesized pair.
    * The ACTIVE path in ``attach_active_notifications`` when moving payload
      to a newer normal tool result (via ``prior_holder`` arg).
    * The notifications-cleared path so no holder reference lingers.
    """
    agent._notification_live_holder = None


# Keep the old name as an alias so external callers (if any) don't break.
# Internal code should prefer skeletonize_notification_holder.
def clear_active_notification_holder(agent) -> None:
    """Legacy alias for :func:`skeletonize_notification_holder`.

    Maintained for backward compatibility.  New code should call
    ``skeletonize_notification_holder`` directly.
    """
    skeletonize_notification_holder(agent)


def sanitize_email_notification_after_persistent(notification_payload: dict) -> None:
    notifications = notification_payload.get(NOTIFICATIONS_KEY)
    if not isinstance(notifications, dict):
        return
    email = notifications.get("email")
    if not isinstance(email, dict):
        return
    email_ids = _email_notification_email_ids(notification_payload)
    sanitized = {
        key: value
        for key, value in email.items()
        if key not in {"data", "header", "instructions"}
    }
    sanitized["header"] = "Email event"
    sanitized["data"] = {"email_ids": email_ids}
    sanitized["instructions"] = (
        "High-attention email hook: full unread content lives in "
        "notification_persistent.email. Prefer email.dismiss after handling; "
        "use email.read/reply for source-of-truth mailbox actions. When "
        "handled through the email tool, the producer mirror updates or "
        "clears this notification."
    )
    notifications["email"] = sanitized


def notification_payload_signature(payload: Mapping[str, Any] | None) -> str:
    """Return a stable signature of the *material* notification payload.

    ``_meta.notifications`` is **sparse / update-driven** (mirrors the #618
    ``agent_meta`` cadence): while notifications stay active but their material
    content is unchanged, the payload is NOT chased onto every newest tool
    result — the prior holder keeps it.  This signature is the change detector
    used by :func:`attach_active_notifications`.

    The whole ``build_notification_payload`` output is signed — the per-channel
    ``notifications`` payloads *and* the ``notification_guidance`` (whose
    ``sources`` list changes when a channel appears or disappears).  A channel
    coming or going is a material change worth re-surfacing, so signing the full
    payload is the least-surprising definition.  Unlike ``agent_meta`` there is
    no volatile per-batch bookkeeping to exclude: the payload is channel-owned
    current state, so every field is material.
    """
    try:
        return _json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(sorted((payload or {}).items()))


def _is_notification_check_placeholder(content) -> bool:
    """Return True when ``content`` is a voluntary ``notification(action=check)``
    placeholder result.

    The ``notification`` intrinsic's ``check`` action returns a dict carrying
    ``_notification_placeholder: True`` (see
    ``tools/notification/__init__._check``).  A deliberate check is a read
    request: its result must receive the current notification payload even when
    the payload is materially unchanged, so the sparse change-gate is bypassed
    when the target is such a placeholder.  The IDLE/ASLEEP synthesized pair
    also carries this key but is built by ``_inject_notification_pair`` on its
    own fingerprint-gated path and never reaches here.
    """
    return isinstance(content, dict) and content.get("_notification_placeholder") is True


def _commit_notification_fp(agent) -> None:
    """Commit the current filesystem notification fingerprint onto the agent.

    Best-effort: a fingerprint failure must never break the caller.  Committing
    ``_notification_fp`` is the bridge that stops the IDLE-path synthesized pair
    from re-delivering state already represented by a tool-result holder — so
    even an unchanged / equivalently-rewritten payload commits it, preventing a
    forever-retry against the IDLE sync path.
    """
    try:
        from .notifications import is_channel_allowed

        agent._notification_fp = agent._notification_store.fingerprint(
            is_channel_allowed
        )
    except Exception:
        pass


def attach_active_notifications(
    agent,
    tool_results: list,
    *,
    prior_holder: dict | None = None,
) -> dict | None:
    """Attach the canonical notification payload — sparsely / update-driven.

    ``_meta.notifications`` is **sparse / update-driven**, not
    latest-result-only: while notifications stay active but their *material*
    payload is unchanged (tracked by ``agent._notification_payload_signature``
    via :func:`notification_payload_signature`), the payload is NOT moved onto
    the newest tool result merely because that result is the latest.  The prior
    holder keeps its payload as the current-state carrier, and ordinary
    unrelated tool results do not restamp it.  Mirrors the #618
    :func:`attach_active_runtime` change-gate, preserving notification
    semantics (channel-owned current state, just update-driven rather than
    newest-result-only).

    Contract:
        * When there are no active notifications, no stamping happens,
          ``_notification_fp`` is left untouched, ``prior_holder`` (if any) is
          released (a synthesized pair is skeletonized; a normal tool result
          RETAINS its payload as a historical trace),
          ``_notification_payload_signature`` is reset to ``None``
          (so a later reappearance of the same payload attaches afresh as the
          first active payload), and ``None`` is returned.
        * When active notifications exist but this batch has no dict-shaped
          result to receive them, the prior holder is kept intact,
          ``_notification_fp`` is left uncommitted, and ``prior_holder`` is
          returned — the state can still be delivered later.
        * When the payload's material signature is UNCHANGED and the target is
          an ordinary tool result, the payload is NOT moved/restamped and the
          prior holder stays the live holder; ``prior_holder`` is returned.  The
          fingerprint is still committed so equivalent rewrites / same-material
          payloads do not retry forever against the IDLE synthesized pair.
        * When the payload materially CHANGED, or the target is a deliberate
          ``notification(action="check")`` placeholder (a read request that must
          always receive the current payload), the prior holder is released (a
          synthesized pair is skeletonized; a normal tool result RETAINS its old
          payload as a historical trace — timely transient semantics, Jason
          #4307), the same ``notifications`` + ``notification_guidance`` payload
          shape used by the synthesized notification pair is stamped under
          ``_meta`` on the latest dict-shaped result, the fingerprint is
          committed, the new signature is recorded, and that dict is returned as
          the new holder.  Only the newest emitted payload is current;
          model-facing full-history serialization preserves every
          normal-result holder's content and does not strip ``notifications``
          or ``notification_guidance`` keys (see
          ``lingtai.llm.interface_converters``).

    ``post-molt`` is intentionally not special-cased here.  The dangerous race
    is narrower: the ``psyche.molt`` tool call writes ``post-molt.json`` before
    returning, so only that same molt-result batch must skip active stamping.
    Later ACTIVE batches may consume the post-molt notification normally; if no
    later ACTIVE batch happens, the IDLE/ASLEEP sync path wakes the agent.

    ``tool_results`` is the list of ToolResultBlock objects returned from
    ToolExecutor; their ``.content`` is shared by reference with the canonical
    ChatInterface entries that the adapters append, so mutating the dict here
    propagates to history without a separate write.

    Active-state delivery only: the IDLE-path synthesized notification pair is
    built by ``_inject_notification_pair`` directly, but both paths call
    ``build_notification_payload`` so the live notification payload shape stays
    identical. Committing ``_notification_fp`` here is the bridge that prevents
    the same notification state from being delivered twice (once via tool-result
    meta, again via the synthesized pair).

    Email clear-transition ownership: this function is the owner of
    IN-PROCESS email whole-snapshot clear transitions (see
    ``agent._email_pending_clear``). Every branch below that witnesses
    "email was live and is no longer" — or finds a standing pending-clear
    intent left by a prior batch/molt that had no dict carrier — attempts to
    stamp the durable tombstone on whatever dict-shaped result THIS batch
    offers. If none is available, the intent is retained (never discarded)
    for the next call to consume. The CROSS-RESTART case (the very first
    render after a process start, which can happen before any tool call
    exists for this function to consume a flag against) is owned separately
    by ``reconcile_email_persistent_history``, which appends the
    authoritative state directly into canonical history at startup rather
    than relying on this function or the flag at all — see that function's
    docstring and the module comment above.
    """
    payload = _collect_active_notifications_payload(agent)
    if not payload:
        # Underlying notification files are gone/empty. Release the prior
        # holder (synthesized pairs skeletonize; normal results keep their old
        # payload as a historical trace) and report no live holder remains.
        # Reset the sparse signature so a later reappearance of the same payload
        # attaches again as the first active payload.
        was_email_live = _email_persistent_child_is_live_snapshot(
            _email_persistent_child(prior_holder)
        )
        if prior_holder is not None:
            agent._notification_live_holder = prior_holder
            skeletonize_notification_holder(agent)
        try:
            agent._notification_payload_signature = None
        except Exception:
            pass
        pending = was_email_live or _consume_email_pending_clear(agent)
        if pending and not _stamp_email_cleared_marker_if_possible(tool_results):
            # No dict-shaped carrier this batch: retain the intent instead of
            # discarding it. The next batch (or a future restart's
            # reconciliation) will still see the same standing intent.
            _note_email_pending_clear(agent)
        return None

    target = _last_dict_result(tool_results)
    if target is None:
        # Active notifications exist, but this batch has no dict-shaped
        # result to receive the moving payload. Keep the prior live holder
        # (if any) intact and leave _notification_fp uncommitted so the
        # state can still be delivered later via another tool result or
        # the IDLE synthesized-pair path. A standing pending-clear intent
        # (this function's own transition or one left by reconciliation) is
        # untouched here — nothing to stamp it onto yet.
        return prior_holder

    # Sparse gate: attach/move only when the payload materially changed since the
    # last emitted one, OR the target is a deliberate notification(action=check)
    # read (which must always receive the current payload). A standing
    # pending-clear intent is consulted regardless of this gate, below.
    signature = notification_payload_signature(payload)
    is_check_read = _is_notification_check_placeholder(target)
    unchanged = signature == getattr(agent, "_notification_payload_signature", None)

    if unchanged and not is_check_read and prior_holder is not None:
        # No material change on an ordinary batch with an existing holder: do
        # not move/restamp and do not skeletonize the prior holder — it keeps
        # the payload as the current-state carrier.  Still commit the
        # fingerprint so equivalent rewrites / same-material payloads do not
        # retry forever against the IDLE-path synthesized pair.  If the holder
        # has somehow been lost, fall through and reattach so the payload stays
        # visible instead of committing an invisible state.
        _commit_notification_fp(agent)
        # A standing pending-clear intent (e.g. left by a molt batch, or by
        # startup reconciliation before this turn's first dict result) is
        # orthogonal to whether the LIVE payload changed — email may have
        # cleared while a different channel's unchanged payload took this
        # branch. Stamp it onto this ordinary result now rather than waiting
        # for a materially-changed batch that may never come.
        if _consume_email_pending_clear(agent):
            _stamp_email_cleared_marker_on_target(target)
        return prior_holder

    # Whether email was a live (non-cleared) snapshot on the outgoing holder,
    # captured BEFORE skeletonize/reassignment below so the clear-transition
    # check below always compares against the true previous state.
    was_email_live = _email_persistent_child_is_live_snapshot(
        _email_persistent_child(prior_holder)
    )
    # A standing pending-clear intent from a prior batch/molt/restart that had
    # no carrier is consumed here too — it is orthogonal to `was_email_live`,
    # which only looks at THIS function's own immediately-prior holder.
    pending_clear = _consume_email_pending_clear(agent)

    # Material change (or deliberate check read). Release the previous holder:
    # a synthesized pair is skeletonized; a normal tool result keeps its old
    # payload as a historical trace (only the newest emission is current).
    if prior_holder is not None:
        agent._notification_live_holder = prior_holder
        skeletonize_notification_holder(agent)

    # Nest the canonical notification payload under the result's _meta
    # envelope (alongside any tool_meta/agent_meta/guidance blocks).
    persistent_payload = build_notification_persistent_payload(agent, payload)
    # Move (not duplicate): curated durable IM fields are always stripped
    # from the model-visible ephemeral lane, even when every message id was
    # already delivered and no new persistent block is emitted this round.
    # `payload` is freshly materialized for this delivery cycle, so in-place
    # preview trimming cannot mutate producer-owned on-disk notification state.
    sanitize_telegram_notification_after_persistent(payload)
    sanitize_wechat_notification_after_persistent(payload)
    sanitize_feishu_notification_after_persistent(payload)
    sanitize_whatsapp_notification_after_persistent(payload)
    sanitize_email_notification_after_persistent(payload)
    meta_block = _meta_block(target)
    meta_block.update(payload)
    if persistent_payload:
        meta_block.update(persistent_payload)
        record_notification_persistent_delivery(
            agent,
            persistent_payload,
            tool_call_id=_result_tool_call_id(target),
        )
    # Email dropped out of this round's persistent payload (other channels may
    # still be active) while it was a live snapshot on the previous holder, OR
    # a standing pending-clear intent carried over from a prior batch/molt/
    # restart: stamp the durable clear tombstone here so full-history replay
    # has explicit evidence the unread snapshot ended, instead of silently
    # letting the last nonempty snapshot stand as the only email state
    # forever. A fresh live snapshot this round (email present) makes any
    # pending intent moot — the new snapshot is the newest authoritative
    # state either way, so no tombstone is needed on top of it.
    email_present_this_round = isinstance(persistent_payload, dict) and isinstance(
        persistent_payload.get(NOTIFICATION_PERSISTENT_KEY), dict
    ) and isinstance(
        persistent_payload[NOTIFICATION_PERSISTENT_KEY].get(
            NOTIFICATION_PERSISTENT_EMAIL_CHANNEL
        ),
        dict,
    )
    if (was_email_live or pending_clear) and not email_present_this_round:
        _stamp_email_cleared_marker_on_target(meta_block)
    # Register this dict as the new live holder.
    agent._notification_live_holder = target

    # Record the new signature so a subsequent unchanged batch is recognized.
    try:
        agent._notification_payload_signature = signature
    except Exception:
        pass

    # Commit the fingerprint so the IDLE-path `_sync_notifications` will
    # see fp == agent._notification_fp and skip the synthesized pair for
    # this same unchanged state.
    _commit_notification_fp(agent)

    return target



def render_meta(agent, meta: dict) -> str:
    """Render the meta dict as the line prepended to text input.

    Returns '' when the meta dict is empty — callers should treat '' as
    "no prefix" and skip concatenation.

    Composes the existing ``system.current_time`` template plus a context
    fragment via ``system.context_breakdown`` (or ``system.context_unknown``
    when the session has not yet computed its token decomposition).
    """
    if not meta:
        return ""

    time_val = meta.get("current_time", "")
    ctx_val = _render_context_fragment(agent, meta)

    if time_val == "" and ctx_val == "":
        return ""

    return _t(
        agent._config.language,
        "system.current_time",
        time=time_val,
        ctx=ctx_val,
    )


def _render_context_fragment(agent, meta: dict) -> str:
    """Render the context sub-fragment for the text-input prefix.

    Returns:
        - '' if `context` is not present in ``meta``
        - the locale-specific "unknown" word when the sentinel (-1) is seen
        - the composed "{pct} (sys {sys} + ctx {ctx})" fragment otherwise
    """
    ctx = meta.get("context")
    if not ctx:
        return ""
    if "usage" not in ctx:
        return ""
    usage = ctx.get("usage", -1.0)
    if usage < 0:
        return _t(agent._config.language, "system.context_unknown")
    return _t(
        agent._config.language,
        "system.context_breakdown",
        pct=f"{usage * 100:.1f}%",
        sys=ctx.get("system_tokens", 0),
        ctx=ctx.get("history_tokens", 0),
    )


def stamp_meta(result: dict, meta: dict, elapsed_ms: int) -> dict:
    """Record per-tool runtime ``meta`` on the result for the boundary holder.

    ``_meta.agent_meta`` / ``_meta.guidance`` are **sparse / update-driven**
    blocks: they are (re)attached only when the material agent snapshot changes.
    Stamping them on every result (the old behaviour) would leave stale
    snapshots in history, so this function records the per-tool ``meta`` snapshot
    and measured ``elapsed_ms`` under a transient ``_runtime_pending`` key, which
    :func:`attach_active_runtime` consumes at the tool-batch boundary (analogous
    to the notification holder), compares against the last-emitted snapshot, and
    then deletes.

    When ``meta`` is empty nothing is recorded — matching the pre-existing
    time-blind behaviour where no timing signal appears.

    ``_runtime_pending`` is internal scaffolding and never reaches the wire: the
    boundary holder strips it from every result it inspects.  The
    ``_meta.tool_meta`` block written by ``ToolExecutor._attach_tool_block`` is
    separate and permanent; ``stamp_meta`` does not touch it.
    """
    if not meta:
        return result
    pending: dict = dict(meta)
    pending["elapsed_ms"] = elapsed_ms
    result["_runtime_pending"] = pending
    return result


# ---------------------------------------------------------------------------
# agent_meta / guidance blocks — sparse/update-driven moving holder under _meta.
# Like the notification payload pattern in ``attach_active_notifications``, but
# gated: the holder moves only when the material agent snapshot changes, so an
# unchanged snapshot is not chased onto every latest tool result.
# ---------------------------------------------------------------------------


def _strip_runtime_pending(tool_results: list) -> None:
    """Remove the transient ``_runtime_pending`` scaffolding from every result.

    ``stamp_meta`` records a per-tool ``_runtime_pending`` snapshot on each
    dict result; only the latest result's snapshot is promoted into the real
    ``_meta.agent_meta`` / ``_meta.guidance`` blocks.  This clears the
    scaffolding from the rest so it never reaches the wire or lingers in
    history.
    """
    for block in tool_results:
        content = getattr(block, "content", None)
        if isinstance(content, dict):
            content.pop("_runtime_pending", None)


# Volatile agent_meta bookkeeping that ticks every batch regardless of whether
# the agent's material state changed.  These must NOT contribute to the
# sparse-attach signature: if they did, agent_meta would be forced onto every
# latest result and the "if no change, don't re-stamp" contract would never
# hold.  ``current_time`` is normally popped before promotion, but is listed
# defensively.
_AGENT_META_VOLATILE_KEYS = frozenset({
    "elapsed_ms",
    "active_turn_tool_calls",
    TOOL_META_CURRENT_TIME_KEY,
})

# Within ``current_tool_result_chars`` the running ``total_chars`` grows by a
# little every batch as results accumulate, so it is volatile.  The material
# signals — which large results exist (``top_results``), how many exceed the
# hint threshold (``over_threshold_count``), and the ``threshold`` itself — are
# kept in the signature so a genuinely new large result re-surfaces agent_meta.
_TOOL_RESULT_CHARS_VOLATILE_KEYS = frozenset({"total_chars"})


def agent_meta_signature(agent_meta: Mapping[str, Any]) -> str:
    """Return a stable signature of the *material* agent_meta content.

    ``_meta.agent_meta`` is sparse / update-driven: it is attached to a tool
    result only when the material snapshot changed since the last emitted one
    (see :func:`attach_active_runtime`).  This signature is the change detector.

    Volatile bookkeeping that ticks every batch — ``elapsed_ms``,
    ``active_turn_tool_calls``, ``current_time``, and the running
    ``current_tool_result_chars.total_chars`` — is deliberately excluded so it
    cannot defeat the "if no change" requirement by forcing agent_meta onto
    every result.  Material signals (changed dynamic ``adapter_comment`` scalars,
    a newly-large tool result in ``current_tool_result_chars.top_results`` / a
    changed ``over_threshold_count``) DO change the signature and re-surface
    agent_meta.  (The sustained-pressure molt reminder no longer rides on
    agent_meta — it is permanent ``tool_meta.context.molt`` now — so it is not a
    signal here.)
    """
    material: dict = {}
    for key, value in (agent_meta or {}).items():
        if key in _AGENT_META_VOLATILE_KEYS:
            continue
        if key == "current_tool_result_chars" and isinstance(value, Mapping):
            material[key] = {
                sub_key: sub_value
                for sub_key, sub_value in value.items()
                if sub_key not in _TOOL_RESULT_CHARS_VOLATILE_KEYS
            }
            continue
        material[key] = value
    try:
        return _json.dumps(material, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(sorted(material.items()))


def attach_active_runtime(
    agent,
    tool_results: list,
    *,
    prior_holder: dict | None = None,
) -> dict | None:
    """Attach the live ``agent_meta``/``guidance`` blocks — sparsely.

    ``_meta.agent_meta`` is **sparse / update-driven**, not latest-result-only:
    it is attached to a tool result only when the *material* agent snapshot has
    changed since the last emitted ``agent_meta`` (tracked by
    ``agent._agent_meta_signature`` via :func:`agent_meta_signature`).  When the
    snapshot has not materially changed, ``agent_meta`` is NOT attached to (nor
    moved onto) the newest result merely because it is the latest — the prior
    holder keeps its snapshot as a historical update point, and older emitted
    snapshots remain in history rather than being chased to the tail every batch.

    Mirrors :func:`attach_active_notifications`, but with the change gate:

      * Build the candidate ``agent_meta`` from the latest dict-shaped result's
        per-tool ``_runtime_pending`` snapshot (recorded by :func:`stamp_meta`):
        kernel runtime state — no token diagnostics, which live in
        ``tool_meta.token_usage`` — plus ``elapsed_ms`` + ``active_turn_tool_calls``
        + ``current_tool_result_chars`` + a slimmed dynamic ``adapter_comment``.
      * Compute its material signature.  **Only when it differs** from
        ``agent._agent_meta_signature`` do we: promote ``agent_meta`` + the
        ``_meta.guidance`` ref onto the new target, record the new signature,
        and return the new holder.  The prior holder RETAINS its snapshot as a
        historical trace — ``agent_meta`` is timely transient state (Jason
        #4307): canonical history is not retroactively stripped and only the
        newest emitted snapshot is current. Model-facing full-history
        serialization preserves every holder's content and does not strip
        ``agent_meta`` or ``guidance`` keys (see
        ``lingtai.llm.interface_converters``).
      * When the signature is **unchanged**, nothing is attached or moved and
        ``prior_holder`` is returned unchanged — its ``agent_meta`` stays put.
      * The transient ``_runtime_pending`` scaffolding is stripped from *all*
        results regardless of the change outcome.

    Volatile bookkeeping (``elapsed_ms``, ``active_turn_tool_calls``,
    ``current_time``, ``current_tool_result_chars.total_chars``) is excluded from
    the signature so it cannot force ``agent_meta`` onto every result; see
    :func:`agent_meta_signature`.

    ``active_turn_tool_calls`` is read from the agent's executor guard.
    ``elapsed_ms`` comes from the latest result's own ``_runtime_pending``
    snapshot.

    No live runtime is produced (and the prior holder is returned unchanged) when
    the batch has no dict-shaped target or the latest target carried no pending
    snapshot (e.g. a time-blind agent whose ``meta`` is empty).
    """
    target = _last_dict_result(tool_results)
    pending = target.pop("_runtime_pending", None) if target is not None else None

    # Clear scaffolding from every other result regardless of outcome.
    _strip_runtime_pending(tool_results)

    if target is None or not isinstance(pending, dict) or not pending:
        # No live runtime this batch: leave any prior holder (and its historical
        # agent_meta) untouched.
        return prior_holder

    agent_meta: dict = dict(pending)
    agent_meta.pop(TOOL_META_TOKEN_USAGE_PENDING_KEY, None)
    # Defensive backstop: normal ToolExecutor paths promote current_time into
    # tool_meta before the turn boundary.  Hand-built tests or future producers
    # should still not be able to reintroduce time into sparse agent_meta.
    agent_meta.pop(TOOL_META_CURRENT_TIME_KEY, None)
    # The sustained-pressure molt reminder is PERMANENT tool_meta metadata now:
    # its transit keys are promoted into tool_meta by ``_attach_tool_block`` and
    # must never leak into the sparse agent_meta (nor into its change signature).
    agent_meta.pop(TOOL_META_CONTEXT_PENDING_KEY, None)
    agent_meta.pop(TOOL_META_CONTEXT_EVENT_PENDING_KEY, None)
    calls = _active_turn_tool_calls(agent)
    if calls is not None:
        agent_meta["active_turn_tool_calls"] = calls
    agent_meta["current_tool_result_chars"] = current_tool_result_chars(
        agent, extra_results=tool_results
    )
    # The adapter_comment carries both dynamic per-turn scalars and static
    # rule-like prose plus a long cache ledger.  The static content is resident
    # in the ``meta_guidance`` system-prompt section, so the tail keeps only the
    # slim dynamic view plus a ref back to that section.
    comment = dynamic_adapter_comment(agent)
    if comment:
        agent_meta["adapter_comment"] = slim_adapter_comment_for_tail(comment)

    # Sparse gate: only attach/move agent_meta when its material content changed
    # since the last emitted snapshot.  Volatile bookkeeping is excluded so an
    # unchanged agent state does not chase agent_meta onto every latest result.
    signature = agent_meta_signature(agent_meta)
    if signature == getattr(agent, "_agent_meta_signature", None):
        # Unchanged: keep the prior holder's snapshot as a historical update
        # point; do not re-stamp the tail.
        return prior_holder

    # Material change: the new target receives agent_meta plus the lightweight
    # guidance ref.  The prior holder keeps its snapshot as a historical trace —
    # only the newest emission is current (timely transient semantics).
    meta = _meta_block(target)
    meta[AGENT_META_KEY] = agent_meta
    meta[GUIDANCE_KEY] = build_meta_guidance_ref()
    try:
        agent._agent_meta_signature = signature
    except Exception:
        pass
    return target


def _active_turn_tool_calls(agent) -> int | None:
    """Best-effort read of the ACTIVE-turn tool-call counter from the guard.

    Returns ``None`` (counter omitted) if the agent has no executor/guard or
    the attribute is unavailable, so a missing counter never breaks stamping.
    """
    try:
        guard = getattr(getattr(agent, "_executor", None), "guard", None)
        total = getattr(guard, "total_calls", None)
        return int(total) if total is not None else None
    except Exception:
        return None


def _non_negative_int(value, *, default: int = 0) -> int:
    """Best-effort conversion for agent-facing token counters."""
    try:
        if isinstance(value, bool):
            raise TypeError
        ivalue = int(value)
    except Exception:
        return default
    return ivalue if ivalue >= 0 else default


def _fallback_context_window(agent) -> int:
    """Return a best-effort context window for the reconstruction event."""
    try:
        config_limit = int(getattr(getattr(agent, "_config", None), "context_limit", 0) or 0)
    except Exception:
        config_limit = 0
    if config_limit > 0:
        return config_limit
    try:
        session = getattr(agent, "_session", None)
        chat = getattr(session, "chat", None)
        if chat is None:
            chat = getattr(session, "_chat", None)
        window_fn = getattr(chat, "context_window", None)
        if callable(window_fn):
            window = _non_negative_int(window_fn(), default=-1)
            return window if window > 0 else -1
    except Exception:
        pass
    return -1
