"""Notification management — dismiss synthetic notification pairs."""
from __future__ import annotations


# ---------------------------------------------------------------------------
# dismiss — voluntarily remove one or more synthetic notification pairs
# ---------------------------------------------------------------------------

def _dismiss(agent, args: dict) -> dict:
    """Dismiss one or more notifications by notif_id.

    Idempotent: unknown ids are silently no-op'd. Returns a per-id status
    so the agent gets honest feedback (which were dismissed, which were
    already gone) without an error path. Empty/missing ``ids`` is an error
    (the call has no semantic meaning).

    Removes the matching pair from BOTH stores:
      - ``_tc_inbox``: in case the pair is still queued (race with arrival)
      - ``_session.chat``: in case the pair has been spliced into the wire
    Whichever store holds the pair returns True; the other returns False.
    Both False means the notif_id is unknown — reported as "not_found".
    """
    raw_ids = args.get("ids")
    if isinstance(raw_ids, str):
        # Defensive: agent passed a single id as string.
        raw_ids = [raw_ids]
    if raw_ids is None:
        return {"status": "error", "message": "dismiss: 'ids' is required (list of notif_id strings)"}
    if not isinstance(raw_ids, list):
        return {"status": "error", "message": "dismiss: 'ids' must be a list of notif_id strings"}
    if len(raw_ids) == 0:
        return {"status": "error", "message": "dismiss: 'ids' must be a non-empty list"}

    results: dict[str, str] = {}
    for raw in raw_ids:
        if not isinstance(raw, str):
            results[str(raw)] = "invalid_id"
            continue
        notif_id = raw

        removed_from_queue = agent._tc_inbox.remove_by_notif_id(notif_id)
        # The chat-side helper lives on ChatInterface, not ChatSession.
        # Production hierarchy: agent._session.chat is a ChatSession (e.g.
        # OpenAIChatSession) wrapping the provider adapter; the interface
        # (where remove_pair_by_notif_id is defined) is at .chat.interface.
        chat = getattr(getattr(agent, "_session", None), "chat", None)
        iface = getattr(chat, "interface", None) if chat is not None else None
        removed_from_chat = (
            iface.remove_pair_by_notif_id(notif_id) if iface is not None else False
        )

        if removed_from_queue or removed_from_chat:
            results[notif_id] = "dismissed"
        else:
            results[notif_id] = "not_found"

        agent._log(
            "system_notification_dismissed",
            notif_id=notif_id,
            removed_from_queue=removed_from_queue,
            removed_from_chat=removed_from_chat,
            invoked_by=args.get("_invoked_by", "agent"),
        )

    return {"status": "ok", "results": results}
