"""Messaging — mail arrival, notifications, and outbound messaging."""
from __future__ import annotations

import time

from ..message import _make_message, MSG_REQUEST, MSG_TC_WAKE
from ..i18n import t as _t
from ..time_veil import veil


def _on_mail_received(agent, payload: dict) -> None:
    """Callback for MailService — route incoming mail to inbox.

    This method is never replaced — it is the stable entry point for all
    incoming mail. Lifecycle control (interrupt, sleep, lull, cpr, nirvana)
    is handled by the system intrinsic via signal files, not mail.
    """
    _on_normal_mail(agent, payload)


def _on_normal_mail(agent, payload: dict) -> None:
    """Handle a normal mail — rerender the unread digest in the wire chat.

    The message is already persisted to mailbox/inbox/ by MailService.
    Mail arrival triggers a single splice of an ``email(action="unread")``
    digest pair (replacing any prior pair for source="email.unread").
    Reads, archives, and deletes do NOT trigger a rerender — the wire
    notification is a snapshot of what was unread at the latest arrival,
    not a live unread mirror. Stale-after-read is acceptable; the agent
    can call ``email(action="check")`` for a fresh view.

    Capabilities still set ``_mailbox_name`` / ``_mailbox_tool`` for
    digest rendering.
    """
    address = payload.get("from", "unknown")
    subject = payload.get("subject") or "(no subject)"

    agent._wake_nap("mail_arrived")
    agent._log("mail_received", address=address, subject=subject,
               message=payload.get("message", ""))

    _rerender_unread_digest(agent)


def _rerender_unread_digest(agent) -> str | None:
    """Splice the current-unread digest into the wire chat.

    Computes the unread set, renders the digest prose, builds a synthetic
    ``email(action="unread")`` tool-call pair, and enqueues it on
    ``tc_inbox`` with ``coalesce=True, replace_in_history=True`` and
    ``source="email.unread"``. The drain replaces any prior digest pair
    in the wire with this one.

    Returns the call_id of the enqueued pair, or None if there's nothing
    unread (no enqueue happens — caller's responsibility to know whether
    that means "leave prior digest stale" or "explicitly clear it").

    The current trigger point (``_on_normal_mail``) only fires after a
    mail has been persisted to the inbox, so by construction count >= 1
    when this is called from arrival. The ``count == 0`` short-circuit
    is defensive for future non-arrival callers.
    """
    import secrets
    from datetime import datetime, timezone
    from ..llm.interface import ToolCallBlock, ToolResultBlock
    from ..tc_inbox import InvoluntaryToolCall
    from ..intrinsics.email.primitives import _render_unread_digest

    body, count, newest_ts = _render_unread_digest(agent)
    if count == 0:
        return None

    call_id = f"un_{int(time.time()*1000):x}_{secrets.token_hex(2)}"
    received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    call = ToolCallBlock(
        id=call_id,
        name="email",
        args={
            "action": "unread",
            "count": count,
            "received_at": received_at,
        },
    )
    result = ToolResultBlock(id=call_id, name="email", content=body)
    item = InvoluntaryToolCall(
        call=call,
        result=result,
        source="email.unread",
        enqueued_at=time.time(),
        coalesce=True,
        replace_in_history=True,
    )
    agent._tc_inbox.enqueue(item)

    agent._log(
        "email_unread_digest_enqueued",
        call_id=call_id,
        count=count,
        newest_received_at=newest_ts,
    )

    # Wake the run loop so the digest is drained
    agent._wake_nap("email_unread_digest_enqueued")
    try:
        wake_msg = _make_message(MSG_TC_WAKE, "system", "")
        agent.inbox.put(wake_msg)
    except Exception as e:
        agent._log(
            "tc_wake_post_error",
            source="email.unread",
            error=str(e)[:200],
        )

    return call_id


def _enqueue_system_notification(agent, *, source: str, ref_id: str, body: str) -> str:
    """Synthesize a ``system(action="notification")`` tool-call pair and
    enqueue it on ``tc_inbox`` for splicing at the next safe boundary.

    Args:
        agent: The agent instance.
        source: "email", "email.bounce", "daemon", "mcp.<name>", etc.
        ref_id: External reference (mail_id for email arrival, etc.).
        body: The localized prose that becomes the ``ToolResultBlock`` content.

    Returns:
        The ``notif_id`` (stable, agent-facing handle).
    """
    import secrets
    from datetime import datetime, timezone
    from ..llm.interface import ToolCallBlock, ToolResultBlock
    from ..tc_inbox import InvoluntaryToolCall

    notif_id = f"notif_{int(time.time()*1000):x}_{secrets.token_hex(3)}"
    call_id = f"sn_{int(time.time()*1000):x}_{secrets.token_hex(2)}"
    received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    call = ToolCallBlock(
        id=call_id,
        name="system",
        args={
            "action": "notification",
            "notif_id": notif_id,
            "source": source,
            "ref_id": ref_id,
            "received_at": received_at,
        },
    )
    result = ToolResultBlock(id=call_id, name="system", content=body)
    item = InvoluntaryToolCall(
        call=call,
        result=result,
        source=f"system.notification:{notif_id}",
        enqueued_at=time.time(),
        coalesce=False,
        replace_in_history=False,
    )
    agent._tc_inbox.enqueue(item)

    agent._log(
        "system_notification_enqueued",
        notif_id=notif_id,
        call_id=call_id,
        source=source,
        ref_id=ref_id,
    )
    agent._wake_nap("system_notification_enqueued")
    try:
        wake_msg = _make_message(MSG_TC_WAKE, "system", "")
        agent.inbox.put(wake_msg)
    except Exception as e:
        agent._log(
            "tc_wake_post_error",
            source=source,
            ref_id=ref_id,
            error=str(e)[:200],
        )

    return notif_id


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
