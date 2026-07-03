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


# Explicit lifecycle status stamped in each summarize marker block. A marker is
# `pending` from the moment it is recorded until the summaries it belongs to are
# actually applied to the provider context — either by a manual
# `system(action="summarize", rebuild=true)` or by the automatic delayed
# reconstruction at 0.95 — at which point it flips to `done`. Marker blocks stay
# in local history after being applied, so this status (NOT mere presence) is the
# source of truth for "still pending". Markers written before this field existed
# carry no status and are treated as done/unknown, never as pending-forever.
SUMMARY_STATUS_PENDING = "pending"
SUMMARY_STATUS_DONE = "done"


def _is_already_summarized(content: Any) -> bool:
    """Return True iff *content* is a summarize replacement produced here."""
    return isinstance(content, dict) and content.get("artifact") == SUMMARIZE_MARKER


def _iter_summarize_marker_blocks(iface):
    """Yield every SUMMARIZE_MARKER ToolResultBlock in an interface's history."""
    from ...llm.interface import ToolResultBlock  # local import — no circular dep

    if iface is None:
        return
    for entry in getattr(iface, "_entries", []):
        if getattr(entry, "role", None) != "user":
            continue
        for block in getattr(entry, "content", []):
            if isinstance(block, ToolResultBlock) and _is_already_summarized(block.content):
                yield block


def mark_pending_summaries_done(iface) -> list[str]:
    """Flip every ``status: pending`` summarize marker in *iface* to ``done``.

    Called when pending summaries are actually applied to the provider context:
    the manual ``rebuild=true`` path (via the intrinsic) and the automatic 0.95
    delayed reconstruction (via a small adapter/session hook). Returns the list of
    tool_call_ids that were flipped. Idempotent: markers already ``done`` or
    without an explicit status are left untouched (the latter are legacy markers
    treated as done/unknown, never pending). Safe to call on an interface with no
    markers.
    """
    flipped: list[str] = []
    for block in _iter_summarize_marker_blocks(iface):
        content = block.content
        if not isinstance(content, dict):
            continue
        if content.get("status") == SUMMARY_STATUS_PENDING:
            content["status"] = SUMMARY_STATUS_DONE
            tcid = content.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                flipped.append(tcid)
    return flipped


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


def _agent_interface(agent):
    """Return the agent's live ChatInterface, or ``None``."""
    chat = getattr(agent, "_chat", None)
    return getattr(chat, "interface", None) if chat is not None else None


def _summary_totals_over_pending(agent) -> dict:
    """Aggregate char/token totals over the ``status: pending`` marker blocks.

    Scans live history and sums ONLY markers explicitly marked
    ``status == "pending"`` — this is the current pending set at the moment the
    result comment is generated, so a summarize-only call naturally includes its
    own just-recorded (pending) markers plus any earlier still-pending ones.
    Markers already ``done`` (applied by a rebuild / the 0.95 automatic path) and
    legacy markers with no status are excluded — they are not pending. Returns
    pending count, original/summary char totals, net char reduction, and a rough
    estimated token reduction (clearly labeled).
    """
    return _summary_totals_over(
        agent, predicate=lambda content: content.get("status") == SUMMARY_STATUS_PENDING
    )


def _summary_totals_over(agent, *, predicate) -> dict:
    """Aggregate char/token totals over marker blocks matching *predicate*."""
    iface = _agent_interface(agent)
    original_chars = 0
    summary_chars = 0
    count = 0
    for block in _iter_summarize_marker_blocks(iface):
        content = block.content
        if not isinstance(content, dict) or not predicate(content):
            continue
        try:
            original_chars += int(content.get("original_visible_chars", 0) or 0)
            summary_chars += int(content.get("summary_chars", 0) or 0)
        except (TypeError, ValueError):
            continue
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


def _pending_count(totals) -> int:
    """Number of pending summaries in a totals dict (0 if none/absent)."""
    if not totals:
        return 0
    try:
        return int(totals.get("pending_summaries", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _pending_totals_line(totals, *, applied: bool) -> str:
    """One-line dynamic pending-summary totals for the reconstruction comments.

    ``applied=False`` (summarize-only) frames the totals as what a rebuild WOULD
    replace; ``applied=True`` (rebuild success) frames them as the pending batch
    that was applied/marked done. Emits nothing when the pending count is 0.
    """
    if _pending_count(totals) <= 0:
        return ""
    original = totals["pending_original_chars"]
    summary = totals["pending_summary_chars"]
    net = totals["net_chars"]
    est = totals["est_tokens"]
    if applied:
        return (
            f"Applied pending summaries: {original} visible chars replaced with "
            f"{summary} summary chars, a reduction of {net} chars "
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


def _pure_rebuild_result(agent, *, current_threshold: int) -> dict:
    """Handle rebuild=true with no items — a pure rebuild of pending summaries.

    No new summaries are recorded; the already-``status: pending`` markers are the
    applied batch: their totals are captured, they are flipped to ``done``, then
    the provider-context rebuild is requested.
    """
    if getattr(agent, "_chat", None) is None:
        return {
            "status": "error",
            "reason": "no_chat_session",
            "message": "No active chat session — cannot request a context rebuild.",
            "notification_threshold_chars": current_threshold,
        }
    # Applied totals = the pending batch BEFORE marking done. Then mark done so
    # future summarize-only comments no longer count these as pending.
    applied_totals = _summary_totals_over_pending(agent)
    marked_done = mark_pending_summaries_done(_agent_interface(agent))
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
        "applied_summary_totals": applied_totals,
        "marked_done": marked_done,
        "context": snapshot,
        "notification_threshold_chars": current_threshold,
        "reconstruction": _build_rebuild_reconstruction(
            snapshot, applied_totals, requested=requested
        ),
    }
    return result


def _build_rebuild_reconstruction(snapshot: dict, applied_totals, *, requested: bool) -> str:
    """Category B comment: summarize+rebuild=true success (or attempted rebuild)."""
    if not requested:
        return (
            "Rebuild requested, but this chat backend has no explicit rebuild hook "
            "(or continuation is disabled), so there may be no provider-context action "
            "to take. Any recorded summaries stay pending; if pending summarized history "
            "exists, the runtime applies it automatically when context reaches 0.95 of "
            "the window. See meta_guidance, substrate, and summarize-manual."
        )
    return (
        f"{_context_line(snapshot)}Rebuild successful: the pending summaries have been "
        f"applied to the provider-context rebuild path (their markers are now marked "
        f"done) and will take effect on the next model request. "
        f"{_pending_totals_line(applied_totals, applied=True)}Next round, inspect "
        f"_meta.tool_meta.token_usage.session.context_usage and the reconstruction "
        f"metadata to decide whether context recovered. If it remains above the 0.6 "
        f"recovery target, tend durable stores and molt rather than repeating rebuild. "
        f"Be tactical with token efficiency — do not loop rebuild/summarize. See "
        f"meta_guidance, substrate, and summarize-manual."
    )


def _build_summarize_only_reconstruction(snapshot: dict, totals: dict) -> str:
    """Category A comment: summarize-only (rebuild=false, the default).

    The 0.95 wording is CONDITIONAL on there being pending summarized history:
    waiting for 0.95 only helps when pending total > 0. When pending total is 0,
    there is nothing to apply at 0.95, so the agent is told to summarize more or
    molt instead of waiting.
    """
    prefix = (
        f"Summary recorded in runtime history (status: pending). This does NOT itself "
        f"rebuild the active provider context: it may still contain the old raw result "
        f"until the pending summaries are applied. "
        f"{_context_line(snapshot)}{_pending_totals_line(totals, applied=False)}"
    )
    if _pending_count(totals) > 0:
        body = (
            "Two ways to apply the pending summaries: if pending summarized history "
            "exists (it does now), the runtime applies it automatically once context "
            "reaches 0.95 of the window, OR make one tactical "
            "system(action='summarize', rebuild=true) call proactively — preferably when "
            "context is high (>=0.75 / the runtime rebuild hint) or a fresh context is "
            "worth the cache-miss cost. "
        )
    else:
        body = (
            "There is no pending summarized history right now, so waiting for the 0.95 "
            "automatic reconstruction would apply nothing and give no compaction benefit: "
            "summarize more digested results to create pending compaction, or molt. "
        )
    return (
        f"{prefix}{body}Be tactical with token efficiency: do not loop "
        f"rebuild/summarize. If rebuild cannot recover below the 0.6 recovery target, "
        f"tend durable stores and molt. See meta_guidance, substrate, and "
        f"summarize-manual."
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
    (``"summarize"`` or ``"rebuild"``) and ``context`` (current
    usage/tokens/window). Category A (``mode: summarize``) carries
    ``pending_summary_totals`` (over ``status: pending`` markers). Category B
    (``mode: rebuild``) carries ``applied_summary_totals`` (the pending batch
    applied, captured before flipping the markers ``done``) and ``marked_done``
    (the flipped tool_call_ids).

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
            # Pending until applied to provider context (manual rebuild=true or the
            # automatic 0.95 delayed reconstruction), at which point it flips to
            # "done". This explicit status — not mere marker presence — is the
            # source of truth for the dynamic pending totals.
            "status": SUMMARY_STATUS_PENDING,
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
    #   A. summarize-only (rebuild=false, default) — the new markers are recorded
    #      with status: pending; the active provider context may still contain the
    #      old raw result until they are applied. Dynamic pending totals scan ALL
    #      status: pending markers (this call's new ones plus any earlier still-
    #      pending ones). The comment's 0.95 wording is conditional on pending > 0.
    #   B. summarize+rebuild=true — the pending markers (this call's + any earlier
    #      pending) are the applied batch: their totals are captured, they are
    #      flipped to done, then the rebuild is requested. The comment reports the
    #      applied totals and defers the recover-vs-molt decision to next round.
    #
    # Truth is the marker status, not any adapter-private pending-id set. Totals
    # are read AFTER history mutation (so new pending markers are present) and,
    # for rebuild, BEFORE flipping them done.
    if summarized_count > 0:
        snapshot = _current_context_snapshot(agent)
        result["context"] = snapshot
        if rebuild:
            applied_totals = _summary_totals_over_pending(agent)
            marked_done = mark_pending_summaries_done(_agent_interface(agent))
            requested = _request_rebuild(agent, reason="summarize_rebuild_only")
            result["mode"] = "rebuild"
            result["rebuild_requested"] = requested
            result["applied_summary_totals"] = applied_totals
            result["marked_done"] = marked_done
            result["reconstruction"] = _build_rebuild_reconstruction(
                snapshot, applied_totals, requested=requested
            )
        else:
            totals = _summary_totals_over_pending(agent)
            result["mode"] = "summarize"
            result["pending_summary_totals"] = totals
            result["reconstruction"] = _build_summarize_only_reconstruction(
                snapshot, totals
            )

    return result
