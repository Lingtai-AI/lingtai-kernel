"""Notification management — deprecation shim for ``system(action='dismiss')``.

Under the legacy ``tc_inbox`` model, agents dismissed individual
notification pairs by ``notif_id`` to remove them from both the queue
and the wire chat.  The filesystem redesign replaces that lifecycle:
producers write `.notification/<tool>.json` files and clear them when
their state changes; the kernel keeps the wire in sync automatically.
There is no per-notification dismiss action under the new model.

This module survives as a back-compat shim so existing chat histories
that reference ``dismiss`` calls don't crash on replay.  The handler
returns a deprecation note and otherwise no-ops.

Full removal is scheduled for Phase 3.
"""
from __future__ import annotations


def _dismiss(agent, args: dict) -> dict:
    """Deprecated — producers manage their own state.

    Under the `.notification/` filesystem model, the agent never
    dismisses notifications.  Producers update their JSON files when
    their state changes (e.g. mail intrinsic deletes
    `.notification/email.json` when the unread count hits zero), and
    the kernel's sync mechanism strips the wire's notification block
    when the producer file disappears.

    Returns a deprecation notice.  Logs the call so unintended dismiss
    invocations surface in agent logs.
    """
    agent._log(
        "system_dismiss_deprecated",
        ids=args.get("ids"),
        invoked_by=args.get("_invoked_by", "agent"),
    )
    return {
        "status": "ok",
        "note": (
            "system(action='dismiss') is deprecated under the "
            ".notification/ filesystem model.  Producers manage their "
            "own state; notifications update automatically when "
            "producers change their .notification/<tool>.json files.  "
            "This call is a no-op."
        ),
    }
