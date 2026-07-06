"""Messaging — mail arrival, notifications, and outbound messaging."""
from __future__ import annotations

import time

from ..message import _make_message, MSG_REQUEST

# Default large-result *hint* threshold (chars).  A tool result whose effective
# length exceeds this is treated as "large": the ToolExecutor stamps a
# tool_meta.comment.overflow hint and the result is surfaced for summarization
# through ``_meta.agent_meta.current_tool_result_chars`` (see
# meta_block.current_tool_result_chars).  Large results no longer raise a
# ``large_tool_result`` system notification.  Configurable via
# ``manifest.summarize_notification_threshold`` in init.json + refresh.  Imported
# by tool_executor.py for the overflow-hint default.
DEFAULT_SUMMARIZE_NOTIFICATION_THRESHOLD = 3000


def _on_mail_received(agent, payload: dict) -> None:
    """Callback for MailService — route incoming mail to inbox.

    This method is never replaced — it is the stable entry point for all
    incoming mail. Lifecycle control (interrupt, sleep, lull, cpr, nirvana)
    is handled by the system intrinsic via signal files, not mail.
    """
    _on_normal_mail(agent, payload)


def _on_normal_mail(agent, payload: dict) -> None:
    """Handle a normal mail — republish the unread email notification to ``.notification/email.json``.

    The message is already persisted to mailbox/inbox/ by MailService.
    Mail arrival triggers a fresh write of ``.notification/email.json``;
    the kernel's notification sync mechanism (see
    base_agent/__init__.py:_sync_notifications) detects the fingerprint
    change on the next heartbeat tick and updates the wire's
    notification block accordingly.

    Reads, dismisses, archives, and deletes also trigger this rerender
    through ``EmailManager._rerender_unread_digest`` after they mutate
    read/inbox state, so ``email.json`` remains a mirror of current
    unread mail rather than a stale arrival snapshot.

    The ``_wake_nap`` call is preserved for sub-second latency: it
    nudges the heartbeat loop so notification sync runs within ~1 tick
    instead of waiting for the next periodic poll.  No ``MSG_TC_WAKE``
    here — the sync mechanism owns wake transitions; this just shortens
    the latency on an already-awake agent.
    """
    address = payload.get("from", "unknown")
    subject = payload.get("subject") or "(no subject)"

    agent._wake_nap("mail_arrived")
    agent._log("mail_received", address=address, subject=subject,
               message=payload.get("message", ""))

    _rerender_unread_digest(agent)


def _rerender_unread_digest(agent) -> str | None:
    """Publish (or clear) ``.notification/email.json`` per current unread state.

    Computes the unread set via ``_render_unread_digest``.  When count
    is positive, submits the raw unread mirror via ``system.publish_notification``;
    the model-visible persistent lane carries full unread email bodies. When
    count drops to 0, clears the file so the kernel's sync strips the wire's
    notification block.

    Returns ``"email"`` when published, ``None`` when cleared.  The
    caller doesn't typically use the return value — the side-effect on
    ``.notification/`` is the contract.
    """
    from ..intrinsics.system import publish_notification, clear_notification
    from ..intrinsics.email.primitives import (
        _render_unread_digest,
        _unread_notification_context,
    )

    body, count, newest_ts = _render_unread_digest(agent)

    if count == 0:
        clear_notification(agent._working_dir, "email")
        agent._log("email_notification_cleared")
        return None

    email_items, email_ids = _unread_notification_context(agent)

    publish_notification(
        agent._working_dir, "email",
        header=f"{count} unread email{'s' if count != 1 else ''}",
        icon="📧",
        instructions=(
            "Unread email bodies are injected in full into "
            "_meta.notification_persistent.email. You do not need "
            "email.read merely to see ordinary message content. After you "
            "handle a mail, prefer email(action='dismiss', "
            "email_id=[id1, id2, ...]) to mark it read and clear the "
            "notification without re-fetching content. Use email.read when "
            "you need to refresh source-of-truth mailbox state, inspect "
            "attachments/metadata, or intentionally fetch the producer record; "
            "use email.reply/reply_all to answer. Read and dismiss both accept "
            "lists, so process multiple mails in one call. Sending refuses "
            "bodies over 50,000 characters because unread bodies are injected "
            "without notification-layer truncation. Until you read, dismiss, "
            "archive, or delete a mail, this notification will keep reminding "
            "you about it. IDs can become stale if already handled elsewhere; "
            "if read/dismiss returns not_found, call email(action='check', "
            "filter={'unread_only': true}) to see what is still pending. "
            "See email-manual."
        ),
        data={
            "count": count,
            "newest_received_at": newest_ts,
            "email_ids": email_ids,
            "emails": email_items,
        },
    )

    agent._log(
        "email_notification_published",
        count=count,
        newest_received_at=newest_ts,
    )
    return "email"


def _enqueue_system_notification(
    agent,
    *,
    source: str,
    ref_id: str,
    body: str,
    skip_if_ref_id_exists: bool = False,
    priority: str = "normal",
    extra: dict | None = None,
) -> str:
    """Append a system event to ``.notification/system.json``.

    The system intrinsic owns this single file and multiplexes its
    event types inside (mail bounces, daemon notices, MCP-bridged
    events, future kernel events).  Each call merges a new event into
    the existing list, capped at the 20 most recent entries so a noisy
    producer can't blow the agent's context window.

    The merge is read-modify-write on the same file, so concurrent
    arrivals (e.g. a burst of bounces) need a per-agent lock to avoid
    losing writes.  The lock is initialized by ``BaseAgent``; only
    ``system.json`` needs it because ``email.json`` and ``soul.json``
    recompute full state on every publish (no merge).

    Args:
        agent: The agent instance.
        source: "email", "email.bounce", "daemon", "mcp.<name>", etc.
        ref_id: External reference (mail_id for email arrival, etc.).
        body: The localized prose for the agent to read.
        skip_if_ref_id_exists: When True, skip publishing if an event with
            the same ref_id already exists in system.json.  Used by the
            large-result rescan path to avoid duplicate notifications.
            Returns "" (empty string) when skipped.
        priority: Notification envelope priority. Defaults to ``"normal"``.
            ``"high"`` (or any event carrying a high severity/priority) makes
            the published envelope high priority so frontends surface it.
        extra: Optional structured event fields merged into this event only
            (e.g. severity, artifact path, recommended_action).

    Returns:
        An identifier for the event (for logging and back-compat with
        callers that expected a notif_id; not actually used for any
        per-id lifecycle under the new model).  Returns "" when skipped
        due to skip_if_ref_id_exists.
    """
    import secrets
    from datetime import datetime, timezone
    from ..notifications import collect_notifications
    from ..intrinsics.system import publish_notification

    event_id = f"evt_{int(time.time()*1000):x}_{secrets.token_hex(2)}"
    received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lock = agent._system_notification_lock

    with lock:
        current = collect_notifications(agent._working_dir).get("system", {})
        events = list(current.get("data", {}).get("events", []))

        if skip_if_ref_id_exists:
            for ev in events:
                if ev.get("ref_id") == ref_id:
                    return ""

        event = {
            "event_id": event_id,
            "source": source,
            "ref_id": ref_id,
            "body": body,
            "at": received_at,
        }
        if isinstance(extra, dict):
            event.update(extra)
        events.append(event)
        # Cap at the 20 most recent.
        events = events[-20:]

        # Envelope priority is high if this call asked for it, or if any
        # retained event carries a high severity/priority field.
        envelope_priority = (
            "high"
            if priority == "high" or any(
                isinstance(ev, dict)
                and (ev.get("severity") == "high" or ev.get("priority") == "high")
                for ev in events
            )
            else "normal"
        )

        publish_notification(
            agent._working_dir, "system",
            header=(
                f"{len(events)} system notification"
                f"{'s' if len(events) != 1 else ''}"
            ),
            icon="🔔",
            priority=envelope_priority,
            data={"events": events},
        )

    agent._log(
        "system_notification_published",
        event_id=event_id,
        source=source,
        ref_id=ref_id,
    )
    # Sub-second sync latency: nudge the heartbeat.  Wake transitions
    # are owned by the kernel notification sync mechanism.
    try:
        agent._wake_nap("system_notification_published")
    except Exception as e:
        agent._log(
            "system_notification_wake_error",
            source=source,
            ref_id=ref_id,
            error=str(e)[:200],
        )

    return event_id


def _rescan_large_tool_results(agent) -> int:
    """Retained no-op: large tool results no longer trigger notifications.

    This turn-boundary hook used to scan live chat history for large
    unsummarized ``ToolResultBlock``s and publish a ``large_tool_result``
    system notification for each pending case (gated by a combined
    total-length threshold).  That mechanism has been removed: large results
    are surfaced as a ranked list under
    ``_meta.agent_meta.current_tool_result_chars.top_results`` (see
    :func:`meta_block.current_tool_result_chars`) and digested via
    ``system(action="summarize")``.  Nothing is published or injected here.

    The function is kept (and still called from the turn-boundary
    housekeeping trio) as a stable seam so the call sites and their error
    handling are unchanged; it simply returns 0.
    """
    return 0


def _notify(agent, sender: str, text: str) -> None:
    """Put a system notification into the agent's inbox.

    This is the primary way addons inform the agent about external events.
    The message appears in the agent's conversation as a system message.
    """
    msg = _make_message(MSG_REQUEST, sender, text)
    agent.inbox.put(msg)


def _mail(agent, address: str, message: str, subject: str = "") -> dict:
    """Send a message to another agent (public API). Requires MailService.

    Routes through the email intrinsic (renamed from mail in 0.7.5).
    """
    return agent._intrinsics["email"]({"action": "send", "address": address, "message": message, "subject": subject})


def _send(agent, content: str | dict, sender: str = "user") -> None:
    """Send a message to the agent (fire-and-forget).

    Args:
        agent: The agent instance.
        content: Message content.
        sender: Message sender.
    """
    msg = _make_message(MSG_REQUEST, sender, content)
    agent.inbox.put(msg)
    agent._wake_nap("message_received")
