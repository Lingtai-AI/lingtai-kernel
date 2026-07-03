"""system(action='summarize') — agent-authored context summarization.

Replaces the context-visible content of prior main-agent tool-result blocks
with a compact agent-authored summary, while preserving the original payload
in the durable event log (events.jsonl) for later retrieval by tool_call_id.

This is purely a context-budget operation: the agent says "I have digested
this result; replace the active version with my summary, keep the full
original traceable."  It does NOT delete or rewrite event traces.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
from typing import Any

from ...meta_block import formal_tool_result_visible_len


# Stable marker stamped on every summarized replacement block so future
# passes (and idempotency checks) can detect them without heuristics.
SUMMARIZE_MARKER = "lingtai_agent_summarized_result"


def _is_already_summarized(content: Any) -> bool:
    """Return True iff *content* is a summarize replacement produced here."""
    return isinstance(content, dict) and content.get("artifact") == SUMMARIZE_MARKER


def _find_tool_result_block(agent, tool_call_id: str):
    """Walk live chat history and return the ToolResultBlock for *tool_call_id*.

    Returns ``(entry, block_index, block)`` or ``(None, -1, None)`` when not found.
    Excludes blocks already carrying a synthesized heal placeholder.
    """
    from ...llm.interface import ToolResultBlock  # local import — no circular dep

    chat = getattr(agent, "_chat", None)
    if chat is None:
        return None, -1, None
    iface = getattr(chat, "interface", None)
    if iface is None:
        return None, -1, None
    entries = getattr(iface, "_entries", [])
    for entry in entries:
        if entry.role != "user":
            continue
        for idx, block in enumerate(entry.content):
            if isinstance(block, ToolResultBlock) and block.id == tool_call_id:
                return entry, idx, block
    return None, -1, None


def _visible_len(content: Any) -> int:
    """Return visible length of the formal tool-result payload only.

    Kernel/runtime metadata such as ``_meta.notifications`` and
    ``_meta.guidance`` is channel guidance/state, not the substantive result
    being summarized.
    """
    return formal_tool_result_visible_len(content)


def _truthy_flag(value: Any) -> bool:
    """Return True only for explicit boolean-ish true values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


# Rough chars-per-token divisor for the clearly-labeled token estimate in the
# dynamic pending-summary totals. This is a display heuristic only — the exact
# token counts live in _meta.tool_meta.token_usage; the char-derived figure is
# a rough guide so the agent can weigh a rebuild.
_CHARS_PER_TOKEN_ESTIMATE = 4


def _summary_chars_by_id(agent) -> dict:
    """Map every SUMMARIZE_MARKER block in live history to its char counts.

    Returns ``{tool_call_id: (original_visible_chars, summary_chars)}`` for each
    summarize replacement block currently present in local history. Marker blocks
    remain in history until the next molt/refresh even after a rebuild has applied
    them to provider context, so this map is a lookup table keyed by id — it is
    NOT itself the pending set. Callers intersect it with the actual pending ids
    (:func:`_read_pending_summary_ids`) so already-applied markers are not
    double-counted as still-pending.
    """
    from ...llm.interface import ToolResultBlock  # local import — no circular dep

    chat = getattr(agent, "_chat", None)
    iface = getattr(chat, "interface", None) if chat is not None else None
    out: dict[str, tuple[int, int]] = {}
    if iface is None:
        return out
    for entry in getattr(iface, "_entries", []):
        if getattr(entry, "role", None) != "user":
            continue
        for block in getattr(entry, "content", []):
            if not (isinstance(block, ToolResultBlock) and _is_already_summarized(block.content)):
                continue
            content = block.content
            try:
                tool_call_id = content.get("tool_call_id")
                original = int(content.get("original_visible_chars", 0) or 0)
                summary = int(content.get("summary_chars", 0) or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if isinstance(tool_call_id, str) and tool_call_id:
                out[tool_call_id] = (original, summary)
    return out


def _read_pending_summary_ids(agent):
    """Return the adapter's delayed-pending summarized-id set, or ``None``.

    Reads ``chat.pending_summary_ids()`` — the summaries recorded but not yet
    applied to provider context. ``None`` means the adapter has no delayed-pending
    tracking (e.g. continuation disabled / rebuild-every-request), in which case
    the caller must fall back conservatively to the CURRENT call's ids rather than
    assuming any historical marker is still pending. A returned set (possibly
    empty) is authoritative.
    """
    chat = getattr(agent, "_chat", None)
    hook = getattr(chat, "pending_summary_ids", None)
    if not callable(hook):
        return None
    try:
        ids = hook()
    except Exception:
        return None
    if ids is None:
        return None
    try:
        return {str(i) for i in ids}
    except TypeError:
        return None


def _pending_summary_totals(agent, pending_ids, char_map) -> dict:
    """Aggregate pending-summary totals over a SPECIFIC set of pending ids.

    ``pending_ids`` is the set of summarized ids whose compacted form has NOT yet
    reached provider context (the current action's affected set). ``char_map`` is
    :func:`_summary_chars_by_id`. Only ids present in BOTH are counted — an id with
    no marker in history is skipped rather than invented. This is the honest total:
    already-applied historical markers are excluded because they are not in
    ``pending_ids``. Returns pending original/summary char totals, net char
    reduction, a rough estimated token reduction (clearly labeled), and the count.
    """
    original_chars = 0
    summary_chars = 0
    count = 0
    for tool_call_id in pending_ids:
        entry = char_map.get(tool_call_id)
        if entry is None:
            continue
        original, summary = entry
        original_chars += original
        summary_chars += summary
        count += 1
    net_chars = original_chars - summary_chars
    return {
        "pending_summaries": count,
        "pending_original_chars": original_chars,
        "pending_summary_chars": summary_chars,
        "net_chars": net_chars,
        "est_tokens": net_chars // _CHARS_PER_TOKEN_ESTIMATE,
    }


def _current_context_snapshot(agent) -> dict:
    """Return current context usage/tokens/window when resolvable, else Nones.

    Reuses the same meta_block sources the tool-meta token_usage block uses so the
    figures reported here match ``_meta.tool_meta.token_usage.session``.
    """
    from ...meta_block import _current_context_usage, _session_context_window

    usage = None
    tokens = None
    window = None
    try:
        raw_usage = float(_current_context_usage(agent))
        if raw_usage >= 0:
            usage = raw_usage
    except Exception:
        usage = None
    try:
        window = int(_session_context_window(agent)) or None
    except Exception:
        window = None
    try:
        raw = agent.get_token_usage()
        tokens = int(raw.get("ctx_total_tokens", 0)) or None
    except Exception:
        tokens = None
    return {"usage": usage, "tokens": tokens, "window": window}


def _context_line(snapshot: dict) -> str:
    """One-line current-context prefix for the reconstruction comments."""
    usage = snapshot.get("usage")
    tokens = snapshot.get("tokens")
    window = snapshot.get("window")
    if usage is not None and tokens and window:
        return f"Current context: {usage:.2f} ({tokens}/{window} tokens). "
    if usage is not None:
        return f"Current context: {usage:.2f}. "
    return ""


def _pending_totals_line(totals, *, applied: bool) -> str:
    """One-line dynamic pending-summary totals for the reconstruction comments.

    ``applied=False`` (summarize-only) frames the totals as what a rebuild WOULD
    replace; ``applied=True`` (rebuild success) frames them as what was
    submitted to the rebuild path before pending state clears. ``totals`` is
    ``None`` when the pending set is unknown (adapter without tracking, pure
    rebuild) — no totals line is emitted in that case.
    """
    if not totals or totals.get("pending_summaries", 0) <= 0:
        return ""
    original = totals["pending_original_chars"]
    summary = totals["pending_summary_chars"]
    net = totals["net_chars"]
    est = totals["est_tokens"]
    if applied:
        return (
            f"Pending summaries applied to the rebuild path: {original} visible chars "
            f"replaced with {summary} summary chars, a reduction of {net} chars "
            f"(~{est} tokens, rough estimate). "
        )
    return (
        f"Pending summaries would replace {original} visible chars with {summary} "
        f"summary chars after rebuild, an estimated reduction of {net} chars "
        f"(~{est} tokens, rough estimate). "
    )


def _request_rebuild(agent, *, reason: str) -> bool:
    """Ask the chat session to rebuild the provider context. Returns success.

    Raises nothing; returns False when there is no session, no hook, or the hook
    declines/errors. Callers surface the boolean as ``rebuild_requested``.
    """
    chat = getattr(agent, "_chat", None)
    if chat is None:
        return False
    hook = getattr(chat, "request_history_rebuild", None)
    if not callable(hook):
        return False
    try:
        requested = bool(hook(reason=reason))
    except TypeError:
        requested = bool(hook())
    except Exception as exc:  # pragma: no cover - defensive hook isolation
        try:
            agent._log("history_rebuild_request_failed", error=type(exc).__name__)
        except Exception:
            pass
        return False
    try:
        agent._log("history_rebuild_requested", requested=requested, reason=reason)
    except Exception:
        pass
    return requested


_UNSET = object()


def _totals_for_action(agent, *, new_ids, prior_pending=_UNSET):
    """Compute honest pending-summary totals for the CURRENT action.

    ``new_ids`` are the ids recorded by this call (empty for a pure rebuild). The
    affected pending set is the adapter's existing delayed-pending ids UNION
    ``new_ids``:

      * When the adapter exposes pending tracking, prior recorded-but-not-applied
        summaries plus the ones this call just recorded are all still pending, so
        both contribute.
      * When the adapter has NO tracking (``pending_summary_ids()`` -> ``None``),
        we fall back conservatively to ``new_ids`` only — never claiming that old
        historical marker blocks (which may already be applied) are still pending.
        If there are also no ``new_ids`` (a pure rebuild on such an adapter), the
        pending set is genuinely unknown and this returns ``None`` so the caller
        omits dynamic totals rather than reporting a misleading zero.

    ``prior_pending`` may be passed in when the caller has already snapshotted the
    adapter set BEFORE ``on_history_summarized`` ran (which can clear it on a
    same-turn auto-release); when omitted it is read now. It is either a set or
    ``None`` (no tracking), matching :func:`_read_pending_summary_ids`.
    """
    if prior_pending is _UNSET:
        prior_pending = _read_pending_summary_ids(agent)
    new_set = {str(i) for i in new_ids}
    if prior_pending is None:
        if not new_set:
            return None  # genuinely unknown — omit totals
        affected = new_set
    else:
        affected = set(prior_pending) | new_set
    char_map = _summary_chars_by_id(agent)
    return _pending_summary_totals(agent, affected, char_map)


def _pure_rebuild_result(agent, *, current_threshold: int) -> dict:
    """Handle rebuild=true with no items — a pure rebuild of pending summaries.

    No new summaries are recorded; the already-pending recorded summaries are
    submitted to the provider-context rebuild path.
    """
    if getattr(agent, "_chat", None) is None:
        return {
            "status": "error",
            "reason": "no_chat_session",
            "message": "No active chat session — cannot request a context rebuild.",
            "notification_threshold_chars": current_threshold,
        }
    # Totals BEFORE the rebuild request, over the existing pending set only (no
    # new items). None => adapter has no pending tracking => totals unknown.
    totals = _totals_for_action(agent, new_ids=())
    snapshot = _current_context_snapshot(agent)
    requested = _request_rebuild(agent, reason="summarize_rebuild_only")
    result = {
        "status": "ok",
        "mode": "rebuild",
        "summarized": 0,
        "failed": 0,
        "items": [],
        "cleared_reminders": [],
        "rebuild_requested": requested,
        "context": snapshot,
        "notification_threshold_chars": current_threshold,
        "reconstruction": _build_rebuild_reconstruction(
            snapshot, totals, requested=requested
        ),
    }
    if totals is not None:
        result["pending_summary_totals"] = totals
    return result


def _build_rebuild_reconstruction(snapshot: dict, totals: dict, *, requested: bool) -> str:
    """Category B comment: summarize+rebuild=true success (or attempted rebuild)."""
    if not requested:
        return (
            "Rebuild requested, but this chat backend has no explicit rebuild hook "
            "(or continuation is disabled), so there may be no provider-context action "
            "to take. Recorded summaries remain pending until the automatic 95% "
            "reconstruction. See meta_guidance, substrate, and summarize-manual."
        )
    return (
        f"{_context_line(snapshot)}Rebuild successful: the recorded summaries have been "
        f"applied to the provider-context rebuild path and will take effect on the next "
        f"model request. {_pending_totals_line(totals, applied=True)}Next round, inspect "
        f"_meta.tool_meta.token_usage.session.context_usage and the reconstruction "
        f"metadata to decide whether context recovered. If it remains above the 0.6 "
        f"recovery target, tend durable stores and molt rather than repeating rebuild. "
        f"Be tactical with token efficiency — do not loop rebuild/summarize. See "
        f"meta_guidance, substrate, and summarize-manual."
    )


def _build_summarize_only_reconstruction(snapshot: dict, totals: dict) -> str:
    """Category A comment: summarize-only (rebuild=false, the default)."""
    return (
        f"Summaries recorded in runtime history. This does NOT itself rebuild the active "
        f"provider context: it may still contain the old raw result until rebuild. "
        f"{_context_line(snapshot)}{_pending_totals_line(totals, applied=False)}Two ways to "
        f"apply them: wait for automatic delayed reconstruction once context reaches 0.95 "
        f"of the window, OR make one tactical system(action='summarize', rebuild=true) call "
        f"proactively — preferably when context is high (>=0.75 / the runtime rebuild hint) "
        f"or a fresh context is worth the cache-miss cost. Be tactical with token "
        f"efficiency: do not loop rebuild/summarize. If rebuild cannot recover below the "
        f"0.6 recovery target, tend durable stores and molt. See meta_guidance, substrate, "
        f"and summarize-manual."
    )


def _summarize(agent, args: dict) -> dict:
    """Handle system(action='summarize').

    Expected args shape::

        {
          "action": "summarize",
          "items": [
            {"tool_call_id": "toolu_...", "summary": "Agent-authored text ..."},
            ...
          ],
          "rebuild": false  # default; true also requests a provider-context rebuild
        }

    ``rebuild`` (boolean, default false) controls the provider-context rebuild:

      * ``rebuild=false`` with items — record summaries only (category A comment).
      * ``rebuild=true`` with items — record summaries, then request a rebuild
        that applies them to the provider context (category B comment).
      * ``rebuild=true`` with no items — pure rebuild using already-pending
        summaries (category B comment).
      * no items and ``rebuild=false`` — invalid no-op (``missing_items`` error).

    Returns a dict with per-item results (``"items"`` list), aggregate counts
    (``"summarized"``, ``"failed"``), the current threshold
    (``"notification_threshold_chars"``), and — on success — ``mode``
    (``"summarize"`` or ``"rebuild"``), ``pending_summary_totals`` (aggregated
    over ALL pending summaries), and ``context`` (current usage/tokens/window).

    Note: ``notification_threshold_chars`` is NOT accepted at runtime.  The
    threshold is set exclusively via ``manifest.summarize_notification_threshold``
    in init.json and takes effect after a refresh.  Passing this field returns
    an error so callers discover the policy change loudly.
    """
    current_threshold = getattr(agent, "_summarize_notification_threshold", 3000)

    # --- Reject runtime threshold mutation ---
    if args.get("notification_threshold_chars") is not None:
        return {
            "status": "error",
            "reason": "runtime_threshold_change_not_supported",
            "message": (
                "The summarize notification threshold cannot be changed at runtime. "
                "It is configured via manifest.summarize_notification_threshold in "
                "init.json and takes effect after system(action='refresh'). "
                "To handle pending large-result notifications without changing the "
                "threshold: summarize/digest all pending large-result cases in one "
                "deliberate batch using system(action='summarize', items=[...]), or "
                "tolerate the repeated reminders until you update the persistent "
                "config and refresh."
            ),
            "notification_threshold_chars": current_threshold,
        }

    rebuild = _truthy_flag(args.get("rebuild"))
    items_arg = args.get("items")
    has_items = isinstance(items_arg, list) and len(items_arg) > 0

    # rebuild=true with no items → pure rebuild using already-pending summaries.
    if rebuild and not has_items:
        return _pure_rebuild_result(agent, current_threshold=current_threshold)

    # No items and rebuild=false → invalid no-op.
    if not has_items:
        return {
            "status": "error",
            "reason": "missing_items",
            "message": (
                "system(action='summarize') requires a non-empty 'items' list, "
                "each with 'tool_call_id' and 'summary'. To rebuild provider "
                "context using already-pending summaries without recording new "
                "ones, call system(action='summarize', rebuild=true) with no items. "
                "rebuild=false with no items is an invalid no-op."
            ),
            "notification_threshold_chars": current_threshold,
        }

    item_results: list[dict] = []
    summarized_count = 0
    failed_count = 0
    summarized_ids: list[str] = []

    now_utc = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    chat = getattr(agent, "_chat", None)

    for item in items_arg:
        if not isinstance(item, dict):
            item_results.append({
                "status": "error",
                "reason": "invalid_item",
                "message": "Each item must be a dict with 'tool_call_id' and 'summary'.",
                "item": repr(item)[:200],
            })
            failed_count += 1
            continue

        tool_call_id = item.get("tool_call_id")
        summary = item.get("summary")

        if not tool_call_id or not isinstance(tool_call_id, str):
            item_results.append({
                "status": "error",
                "reason": "missing_tool_call_id",
                "message": "Item is missing 'tool_call_id'.",
            })
            failed_count += 1
            continue

        if summary is None or not isinstance(summary, str):
            item_results.append({
                "status": "error",
                "reason": "missing_summary",
                "tool_call_id": tool_call_id,
                "message": "Item is missing 'summary' (must be a string).",
            })
            failed_count += 1
            continue

        if chat is None:
            item_results.append({
                "status": "error",
                "reason": "no_chat_session",
                "tool_call_id": tool_call_id,
                "message": "No active chat session — cannot mutate history.",
            })
            failed_count += 1
            continue

        entry, idx, block = _find_tool_result_block(agent, tool_call_id)

        if block is None:
            item_results.append({
                "status": "error",
                "reason": "not_found",
                "tool_call_id": tool_call_id,
                "message": (
                    f"No main-agent tool-result block found for tool_call_id={tool_call_id!r}. "
                    "Daemon results, unknown ids, and ids from previous sessions "
                    "cannot be summarized."
                ),
            })
            failed_count += 1
            continue

        if _is_already_summarized(block.content):
            item_results.append({
                "status": "error",
                "reason": "already_summarized",
                "tool_call_id": tool_call_id,
                "message": (
                    f"tool_call_id={tool_call_id!r} has already been summarized. "
                    "Re-summarization is blocked for now to preserve idempotency; "
                    "keep the existing summary or retrieve the original from logs/events."
                ),
            })
            failed_count += 1
            continue

        # Capture original visible length before replacing.
        original_visible_len = _visible_len(block.content)

        # Build the replacement — visible in context, not a secret.
        replacement: dict[str, Any] = {
            "artifact": SUMMARIZE_MARKER,
            "tool_call_id": tool_call_id,
            "tool_name": block.name,
            "agent_summary": summary,
            "summarized_at": now_utc,
            "summary_chars": len(summary),
            "original_visible_chars": original_visible_len,
            "retrieval_hint": (
                f"This is your own agent-authored summary of the original tool result. "
                f"The summary is NOT canonical — it reflects your understanding at the "
                f"time of summarization and may be incomplete or inaccurate. "
                f"To retrieve the full original, grep events.jsonl by tool_call_id:\n"
                f"  grep '{tool_call_id}' <workdir>/logs/events.jsonl\n"
                f"  # or use: lingtai-agent log query (see sqlite-log-query manual)"
            ),
        }

        # Mutate the block content in place — pairing, id, name, synthesized
        # flag are untouched so provider wire alternation stays valid.
        entry.content[idx].content = replacement

        agent._log(
            "tool_result_summarized",
            tool_call_id=tool_call_id,
            tool_name=block.name,
            summary_chars=len(summary),
            original_visible_chars=original_visible_len,
        )

        item_results.append({
            "status": "ok",
            "tool_call_id": tool_call_id,
            "tool_name": block.name,
            "summary_chars": len(summary),
            "original_visible_chars": original_visible_len,
        })
        summarized_count += 1
        summarized_ids.append(tool_call_id)

    # Snapshot the adapter's delayed-pending set BEFORE on_history_summarized may
    # clear it (a same-turn auto-release at >=0.95 clears the set). This is the
    # prior recorded-but-not-applied set that, unioned with THIS call's ids, is
    # what the current result's pending totals must reflect.
    prior_pending = _read_pending_summary_ids(agent) if summarized_count > 0 else _UNSET

    # Persist history so summarization survives refresh/molt.
    if summarized_count > 0:
        save_fn = getattr(agent, "_save_chat_history", None)
        if callable(save_fn):
            try:
                save_fn(ledger_source="summarize")
            except Exception as exc:
                # Non-fatal: summarization already applied in memory.
                agent._log(
                    "tool_result_summarize_save_failed",
                    error=str(exc),
                )
        hook = getattr(chat, "on_history_summarized", None)
        if callable(hook):
            try:
                hook(list(summarized_ids))
            except Exception as exc:  # pragma: no cover - defensive hook isolation
                agent._log(
                    "history_summarize_hook_failed",
                    error=type(exc).__name__,
                )

    # A successful summarize is the sanctioned discharge path for the
    # matching large-result reminder: clear it automatically.  Generic
    # dismiss refuses these reminders, so this is the only way they go away.
    cleared_reminder_ref_ids: list[str] = []
    if summarized_ids and getattr(agent, "_working_dir", None) is not None:
        try:
            from ...notifications import clear_large_result_reminders
            cleared_reminder_ref_ids = clear_large_result_reminders(
                agent, summarized_ids
            )
        except Exception as exc:
            # Non-fatal: summarization already applied; the rescan/dedup
            # logic will reconcile the reminder on a later turn.
            try:
                agent._log(
                    "large_result_reminder_clear_failed",
                    error=str(exc),
                )
            except Exception:
                pass

    overall_status = "ok" if failed_count == 0 else ("partial" if summarized_count > 0 else "error")
    result: dict[str, Any] = {
        "status": overall_status,
        "summarized": summarized_count,
        "failed": failed_count,
        "items": item_results,
        "cleared_reminders": cleared_reminder_ref_ids,
        "notification_threshold_chars": current_threshold,
    }

    # The final result-comment has exactly two successful categories, keyed on the
    # `rebuild` flag (Jason, Telegram 4093/4095/4097):
    #
    #   A. summarize-only (rebuild=false, default) — summaries are recorded in
    #      runtime history but the active provider context may still contain the old
    #      raw result until a rebuild. Recording a summary does NOT by itself rebuild
    #      the active provider context, even above 0.75. The reconstruction comment
    #      reports current context + the pending-summary totals and explains the two
    #      ways to apply them (automatic 0.95 delayed reconstruction, or one tactical
    #      rebuild=true call), warning against looping.
    #   B. summarize+rebuild=true — after recording, the pending summaries are
    #      submitted to the provider-context rebuild path; the comment reports the
    #      pending totals that were applied and tells the agent next round's _meta
    #      decides recovery vs molt.
    #
    # Dynamic totals cover the pending set THIS action affects — the adapter's
    # existing delayed-pending ids (snapshotted above as `prior_pending`, before
    # on_history_summarized could clear it) UNION this call's ids — NOT every
    # historical marker block (already-applied markers stay in history but are not
    # pending). Totals are computed BEFORE requesting the rebuild.
    if summarized_count > 0:
        totals = _totals_for_action(
            agent, new_ids=summarized_ids, prior_pending=prior_pending
        )
        snapshot = _current_context_snapshot(agent)
        if totals is not None:
            result["pending_summary_totals"] = totals
        result["context"] = snapshot
        if rebuild:
            requested = _request_rebuild(agent, reason="summarize_rebuild_only")
            result["mode"] = "rebuild"
            result["rebuild_requested"] = requested
            result["reconstruction"] = _build_rebuild_reconstruction(
                snapshot, totals, requested=requested
            )
        else:
            result["mode"] = "summarize"
            result["reconstruction"] = _build_summarize_only_reconstruction(
                snapshot, totals
            )

    return result
