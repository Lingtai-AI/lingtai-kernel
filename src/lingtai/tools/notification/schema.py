"""Schema — tool registration for the standalone ``notification`` tool.

The notification tool exposes ``check``, the three atomic dismiss verbs
(``dismiss_channel``, ``dismiss_event``, ``dismiss_ref``), and the strictly
read-only progressive-disclosure action ``manual``. ``summarize`` is *not* here
— it remains a ``system`` action. Schema prose is canonical English; ``lang`` is
accepted for source compatibility and does not select localized aliases.
"""
from __future__ import annotations

LARGE_RESULT_DISMISS_ACTION_NOTE = (
    "Legacy: the kernel no longer raises large_tool_result reminders — large "
    "results are ranked under _meta.agent_meta.agent_state.current_tool_result_chars and "
    "compacted via system(action=summarize). Any large_tool_result event still "
    "present (e.g. persisted before this change or pre-molt) can be dismissed "
    "as an escape hatch. Dismissal only clears the notification surface; the "
    "original result stays in chat history and events.jsonl. See "
    "notification-manual."
)

LARGE_RESULT_FORCE_NOTE = (
    "Does not affect large_tool_result reminder dismissal; that escape hatch "
    "is always allowed and clears only the reminder surface."
)


def get_description(lang: str = "en") -> str:
    return "Notification surface — read and clear the agent's notification channels. Self-actions, no permissions needed.\n\nThis is the only tool that exposes notification verbs; the system tool no longer offers notification or dismiss aliases.\n\nUse check to read all channels, dismiss_channel to clear one channel whole, and dismiss_event / dismiss_ref to remove a single system event by event_id / ref_id. Use notification(action='manual') to return the installed notification manual; this action is strictly read-only and does not change notification state. To compress a large tool result, use system(action=summarize)."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check", "dismiss_channel", "dismiss_event", "dismiss_ref", "manual"],
                "description": "check: read all notification channels. Returns a placeholder; the live payload is stamped onto this same result under `_meta.agent_meta.notifications.attention` and `_meta.agent_meta.guidance.transient`. Replace-only — do not call voluntarily after handling; dismiss instead. Prefer coalescing the dismiss with other tool work you already need this turn when safe; dismiss alone only when there is no useful coalesced work or safety requires it.\n\ndismiss_channel: clear one notification channel whole (channel=<name>). Use producer-specific verbs first (for email, use email(read/dismiss)); guarded channels require force=true only for stale mirrors. Rejects event_id/ref_id — use dismiss_event/dismiss_ref for those.\n\ndismiss_event: remove a single system event by event_id from .notification/system.json (channel defaults to 'system').\n\ndismiss_ref: remove system event(s) by ref_id from .notification/system.json (channel defaults to 'system').\n\nmanual: call notification(action='manual') to return the installed notification-manual skill body. This action is strictly read-only and does not read or change notification state." + "\n\n" + LARGE_RESULT_DISMISS_ACTION_NOTE,
            },
            "channel": {
                "type": "string",
                "description": "Notification channel to act on (e.g. soul, system, mcp.telegram). Required for dismiss_channel; for dismiss_event/dismiss_ref it defaults to 'system'. For producer-owned channels like email, prefer the producer's own verb (email(read/dismiss)).",
            },
            "force": {
                "type": "boolean",
                "description": 'Optional for dismiss verbs. When true, bypasses a producer-registered generic-dismiss guard and the stale-channel-version refusal. Use only when knowingly clearing a stale mirror; producer-owned state is never changed.' + " " + LARGE_RESULT_FORCE_NOTE,
            },
            "event_id": {
                "type": "string",
                "description": 'For dismiss_event: remove only the matching system notification event_id from .notification/system.json instead of the whole channel.',
            },
            "ref_id": {
                "type": "string",
                "description": 'For dismiss_ref: remove system notification event(s) carrying this producer ref_id from .notification/system.json instead of the whole channel.',
            },
            "reason": {
                "type": "string",
                "description": "Optional acknowledgement reason, logged to the event log. Required when dismissing the post-molt continuation channel (use reason='<continue|defer|obsolete>: ...').",
            },
        },
        "required": ["action"],
    }
