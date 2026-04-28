"""Unified per-turn metadata injection.

Single source of truth for "what the agent sees about its own runtime state
on every turn." Both injection sites — text-input prefix (in BaseAgent) and
tool-result stamp (in ToolExecutor) — read from here.

Curate carefully: every field added to `build_meta` ships on every text input
and every tool result.
"""
from __future__ import annotations

from .i18n import t as _t
from .time_veil import now_iso


def build_meta(agent) -> dict:
    """Return the current meta-data snapshot for the agent.

    Respects ``agent._config.time_awareness`` / ``timezone_awareness``
    internally; callers never need to special-case those flags.

    When the agent is time-blind and no other meta fields are curated in,
    returns ``{}``.

    Context-window fields (``system_tokens``, ``context_tokens``,
    ``context_usage``) are always emitted — the time veil does not cover
    token accounting. When the session's token decomposition has not yet
    run (dirty cache and no active chat), the three fields are emitted as
    ``-1`` / ``-1.0`` sentinels so callers can render "unknown" without
    ambiguity.
    """
    meta: dict = {}
    ts = now_iso(agent)
    if ts:
        meta["current_time"] = ts

    # Context-window decomposition. The decomposition needs the agent's
    # system prompt, tool schemas, and context section — all of which
    # are available via the builder callbacks without needing any LLM
    # call to have happened. If the cached values are dirty, refresh them
    # eagerly so the text-input prefix reports real numbers on the very
    # first call of the turn instead of "unknown".
    session = getattr(agent, "_session", None)
    chat_obj = getattr(session, "chat", None) if session is not None else None

    if session is not None and session._token_decomp_dirty:
        try:
            session._update_token_decomposition()
        except Exception:
            pass  # leave dirty; sentinels below

    decomp_ran = session is not None and not session._token_decomp_dirty

    if decomp_ran:
        sys_prompt = session._system_prompt_tokens
        tools = session._tools_tokens
        # "history" = in-memory turns (wire chat).
        # Derived from the server-reported wire count when available
        # (_latest_input_tokens - sys_prompt - tools). Before the first
        # LLM call of a session (e.g. right after start() rehydrates the
        # ChatInterface from chat_history.jsonl on cold start or refresh),
        # _latest_input_tokens is still 0, which would report "对话 0"
        # even though the wire chat has been restored. Fall back to the
        # interface's local estimate so the meta-line reflects the
        # restored history from turn 1.
        if session._latest_input_tokens > 0:
            history = max(
                0,
                session._latest_input_tokens - sys_prompt - tools,
            )
        elif chat_obj is not None:
            # interface.estimate_context_tokens() returns system + tools +
            # conversation. Subtract system + tools to isolate the history
            # portion — otherwise context_tokens would double-count them
            # when system_tokens is added back in the usage calculation,
            # diverging from session.get_context_pressure().
            try:
                history = max(
                    0,
                    chat_obj.interface.estimate_context_tokens() - sys_prompt - tools,
                )
            except Exception:
                history = 0
        else:
            history = 0

        system_tokens = sys_prompt + tools
        context_tokens = history

        # context_window comes from the live chat if available; otherwise
        # fall back to the agent's configured limit. On the very first
        # call of a turn (before ensure_session runs) chat_obj is None;
        # we still want real system/context tokens, just usage% may be
        # a sentinel if no limit is configured.
        if chat_obj is not None:
            limit = agent._config.context_limit or chat_obj.context_window()
        else:
            limit = agent._config.context_limit or 0
        usage = (system_tokens + context_tokens) / limit if limit > 0 else -1.0

        meta["system_tokens"] = system_tokens
        meta["context_tokens"] = context_tokens
        meta["context_usage"] = usage
    else:
        meta["system_tokens"] = -1
        meta["context_tokens"] = -1
        meta["context_usage"] = -1.0

    notif = _pending_notifications_summary(agent)
    if notif is not None:
        meta["pending_notifications"] = notif

    return meta


def _pending_notifications_summary(agent) -> dict | None:
    """Non-destructive snapshot of queued runtime notifications.

    Peeks at ``agent.inbox`` (a queue.Queue holding system notifications,
    soul whispers, addon notifies, etc.) without consuming. Returns None
    when the queue is empty.

    Surface shape:
        {"count": int, "previews": list[str]}
    where ``previews`` lists every queued entry, each truncated to 50 chars
    with newlines flattened. ``count`` mirrors len(previews).

    The agent will see this in the text-input prefix at turn start AND on
    every tool result via stamp_meta — giving them an early heads-up that
    notifications are waiting before the actual messages get drained at
    the next outer-loop boundary by ``_concat_queued_messages``.
    """
    inbox = getattr(agent, "inbox", None)
    if inbox is None:
        return None
    try:
        snapshot = list(inbox.queue)
    except (AttributeError, RuntimeError):
        return None
    if not snapshot:
        return None

    previews: list[str] = []
    for m in snapshot:
        content = getattr(m, "content", "")
        text = content if isinstance(content, str) else str(content)
        flat = text.replace("\n", " ")
        if len(flat) > 50:
            flat = flat[:50] + "..."
        previews.append(flat)

    return {"count": len(snapshot), "previews": previews}


def render_meta(agent, meta: dict) -> str:
    """Render the meta dict as the line prepended to text input.

    Returns '' when the meta dict is empty — callers should treat '' as
    "no prefix" and skip concatenation.

    Composes the existing ``system.current_time`` template (now
    extended with a context slot) plus a context fragment via
    ``system.context_breakdown`` (or ``system.context_unknown`` when the
    session has not yet computed its token decomposition). When pending
    runtime notifications are present, a second line is appended.
    """
    if not meta:
        return ""

    time_val = meta.get("current_time", "")
    ctx_val = _render_context_fragment(agent, meta)
    notif_line = _render_notifications_fragment(agent, meta)

    if time_val == "" and ctx_val == "" and notif_line == "":
        return ""

    head = _t(
        agent._config.language,
        "system.current_time",
        time=time_val,
        ctx=ctx_val,
    )
    if notif_line:
        return f"{head}\n{notif_line}"
    return head


def _render_notifications_fragment(agent, meta: dict) -> str:
    """Render the pending-notifications line for the text-input prefix.

    Returns '' when no pending notifications are present in ``meta``.
    Otherwise returns a localized i18n line summarizing count + previews.
    """
    notif = meta.get("pending_notifications")
    if not notif:
        return ""
    count = notif.get("count", 0)
    previews = notif.get("previews", [])
    if count <= 0 or not previews:
        return ""
    bullets = "\n".join(f"  - {p}" for p in previews)
    return _t(
        agent._config.language,
        "system.pending_notifications",
        count=count,
        previews=bullets,
    )


def _render_context_fragment(agent, meta: dict) -> str:
    """Render the context sub-fragment for the text-input prefix.

    Returns:
        - '' if `context_usage` is not present in ``meta``
        - the locale-specific "unknown" word when the sentinel (-1) is seen
        - the composed "{pct} (sys {sys} + ctx {ctx})" fragment otherwise
    """
    if "context_usage" not in meta:
        return ""
    usage = meta["context_usage"]
    if usage < 0:
        return _t(agent._config.language, "system.context_unknown")
    return _t(
        agent._config.language,
        "system.context_breakdown",
        pct=f"{usage * 100:.1f}%",
        sys=meta.get("system_tokens", 0),
        ctx=meta.get("context_tokens", 0),
    )


def stamp_meta(result: dict, meta: dict, elapsed_ms: int) -> dict:
    """Merge meta fields into a tool-result dict (in place) and return it.

    When ``meta`` is empty, neither the meta fields nor ``_elapsed_ms`` are
    written — matching the pre-existing behaviour of
    ``stamp_tool_result(time_awareness=False)`` exactly. This is deliberate:
    the spec originally claimed ``_elapsed_ms`` always writes, but preserving
    the old time-blind path means a time-blind agent's tool results stay
    free of any timing signal, not just wall-clock. Callers that want a
    timing-only stamp should pass a non-empty meta dict.

    ``_elapsed_ms`` lives here (rather than inside ``build_meta``) because
    it is a per-tool-call measurement — not per-turn agent state — and it
    would be wrong for the same value to appear on the text-input prefix.
    It is written unconditionally after the meta-key loop, so it always
    overrides any identically-named key in ``meta``.
    """
    if not meta:
        return result
    for k, v in meta.items():
        result[k] = v
    result["_elapsed_ms"] = elapsed_ms
    return result
