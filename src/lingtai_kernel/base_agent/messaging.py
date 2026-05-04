"""Messaging — mail arrival, notifications, and outbound messaging."""
from __future__ import annotations

import time

from ..message import _make_message, MSG_REQUEST, MSG_TC_WAKE
from ..i18n import t as _t


def _on_mail_received(agent, payload: dict) -> None:
    """Callback for MailService — route incoming mail to inbox.

    This method is never replaced — it is the stable entry point for all
    incoming mail. Lifecycle control (interrupt, sleep, lull, cpr, nirvana)
    is handled by the system intrinsic via signal files, not mail.
    """
    _on_normal_mail(agent, payload)


def _on_normal_mail(agent, payload: dict) -> None:
    """Handle a normal mail — notify agent via inbox.

    The message is already persisted to mailbox/inbox/ by MailService.
    This method signals arrival and sends a uniform push notification.
    Capabilities configure ``_mailbox_name`` and ``_mailbox_tool``
    to change the notification text (e.g. "email box" / "email").
    """
    from ..intrinsics.email import _new_mailbox_id

    email_id = payload.get("_mailbox_id") or _new_mailbox_id()
    address = payload.get("from", "unknown")
    identity = payload.get("identity")
    name = address
    if identity and identity.get("agent_name"):
        name = identity["agent_name"]
    subject = payload.get("subject", "(no subject)")
    message = payload.get("message", "")
    sent_at = payload.get("sent_at") or payload.get("time") or ""

    agent._wake_nap("mail_arrived")

    if len(message) > 500:
        preview = message[:500].replace("\n", " ") + f"... ({len(message) - 500} more chars)"
    else:
        preview = message.replace("\n", " ")
    notification = _t(
        agent._config.language, "system.new_mail",
        box=agent._mailbox_name, address=address, name=name, subject=subject,
        sent_at=sent_at, preview=preview, tool=agent._mailbox_tool,
    )

    agent._log("mail_received", address=address, name=name, subject=subject, message=message)
    # Route the arrival as a synthetic system(action="notification") pair
    # via tc_inbox. Replaces the older MSG_REQUEST text-channel delivery.
    _enqueue_system_notification(
        agent,
        source="email",
        ref_id=email_id,
        body=notification,
    )


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

    # Track for action-coupled auto-dismiss (email source only).
    if source == "email":
        agent._pending_mail_notifications[ref_id] = notif_id

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
