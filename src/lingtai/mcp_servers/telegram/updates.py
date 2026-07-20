"""Telegram Bot API Update branch catalog, actor policy, and raw envelope.

One shared policy source for TelegramAccount (admission / allowed_users
enforcement) and TelegramManager (persistence / projection), so the two
layers can never disagree about what a branch is or who triggered it.

Actor-resolution policy
-----------------------

For every inbound Update we resolve the *triggering actor* by probing only
the **top level** of the single branch object:

* ``from`` / ``user``  → a triggering Telegram ``User``  (kind ``"user"``).
  ``allowed_users`` is enforced against exactly this id.
* ``sender_chat`` / ``actor_chat`` / ``voter_chat`` → a chat acted (channel
  posts, anonymous reactions, chat poll votes)          (kind ``"chat"``).
* neither on a known branch → actorless service/aggregate event
  (kind ``"none"``); on an unknown future branch → kind ``"unknown"``.

The probe is deliberately shallow and non-recursive: users nested inside
``reply_to_message``, ``quote``, ``entities``, boost sources, etc. are
*never* treated as the actor, so a quoted or replied-to user can neither
grant nor lose admission (anti-confusion requirement).

Admission policy under a configured ``allowed_users`` list:

* kind ``"user"``  — admitted only if the resolved user id is allowed.
* kind ``"chat"`` / ``"none"`` — admitted. Telegram only delivers these
  events because of the bot's own deliberate placement (bot is a member /
  administrator of the chat, the poll or invoice was sent by the bot, the
  business account was connected by its owner), so the trust boundary is
  the bot placement itself, not a per-user allowlist.
* kind ``"unknown"`` — admitted so future protocol branches are not
  silently dropped, with the uncertainty recorded on the envelope
  (``actor.kind == "unknown"``) so agents/tests can surface it. If a
  future branch does carry a top-level ``from``/``user``, the generic
  probe resolves it and the allowlist is enforced as usual.
"""
from __future__ import annotations

import copy
from typing import Any

# Additive envelope schema version; bump on shape changes.
TELEGRAM_ENVELOPE_SCHEMA = 1

# All current official optional Update branches, in Bot API table order
# (fetched 2026-07-20; ``update_id`` + these 26 = 27 Update fields).
KNOWN_UPDATE_BRANCHES: tuple[str, ...] = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "guest_message",
    "message_reaction",
    "message_reaction_count",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "purchased_paid_media",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "chat_boost",
    "removed_chat_boost",
    "managed_bot",
    "subscription",
)

# Branches whose payload object is a full Bot API ``Message``.
MESSAGE_TYPED_BRANCHES: frozenset[str] = frozenset({
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
    "guest_message",
})

# Message-typed branches that are edits of an already-known message.
EDIT_BRANCHES: frozenset[str] = frozenset({
    "edited_message",
    "edited_channel_post",
    "edited_business_message",
})

# Non-edit human Message-typed branches whose text is eligible for local
# slash-command interception (/kanban, /system, …). Channel posts are
# excluded deliberately: they are broadcast content, not operator console
# input, and a local command reply would post into the channel.
LOCAL_COMMAND_BRANCHES: frozenset[str] = frozenset({
    "message",
    "business_message",
    "guest_message",
})

# Branches that wake the agent: new human-authored message content plus
# button presses (existing behavior for message/callback_query). Edits,
# channel broadcasts, and service/aggregate events are recorded without a
# wake, matching the current edited-message policy.
WAKE_BRANCHES: frozenset[str] = frozenset({
    "message",
    "business_message",
    "guest_message",
    "callback_query",
})

# Synthetic conversation bucket for admitted updates that have no real
# chat/message identity (inline queries, polls, boosts, unknown branches…).
# The bucket id is deliberately non-numeric so it can never collide with a
# real Telegram chat id, and the normalized chat record is explicitly
# flagged synthetic — invented routing data never enters the raw envelope.
SYNTHETIC_EVENTS_CHAT_ID = "updates"

_USER_ACTOR_KEYS = ("from", "user")
_CHAT_ACTOR_KEYS = ("sender_chat", "actor_chat", "voter_chat")


def classify_update(update: dict) -> tuple[str | None, Any]:
    """Return ``(branch_name, branch_object)`` for a raw Update dict.

    Known branches are matched in official table order. If no known branch
    is present, the first non-``update_id`` key is returned verbatim as an
    open fallback so unknown future branches are preserved, not dropped.
    Returns ``(None, None)`` for an Update carrying nothing but its id.
    """
    if not isinstance(update, dict):
        return None, None
    for branch in KNOWN_UPDATE_BRANCHES:
        if branch in update:
            return branch, update[branch]
    for key, value in update.items():
        if key != "update_id":
            return key, value
    return None, None


def resolve_update_actor(update: dict) -> dict[str, Any]:
    """Resolve the triggering actor for a raw Update (shallow, top-level only)."""
    branch, obj = classify_update(update)
    actor: dict[str, Any] = {"branch": branch}
    if isinstance(obj, dict):
        for key in _USER_ACTOR_KEYS:
            user = obj.get(key)
            if isinstance(user, dict) and isinstance(user.get("id"), int):
                actor.update({
                    "kind": "user",
                    "user_id": user["id"],
                    "source_field": key,
                })
                return actor
        for key in _CHAT_ACTOR_KEYS:
            chat = obj.get(key)
            if isinstance(chat, dict) and chat.get("id") is not None:
                actor.update({
                    "kind": "chat",
                    "chat_id": chat["id"],
                    "source_field": key,
                })
                return actor
    actor["kind"] = "none" if branch in KNOWN_UPDATE_BRANCHES else "unknown"
    return actor


def is_admitted(actor: dict[str, Any], allowed_users: list[int] | None) -> bool:
    """Apply the module-level admission policy documented above."""
    if allowed_users is None:
        return True
    if actor.get("kind") == "user":
        return actor.get("user_id") in allowed_users
    return True


def event_id_for(account_alias: str, update: dict) -> str:
    """Stable event identity: account + update_id, independent of chat/message."""
    return f"{account_alias}:update:{update.get('update_id')}"


def build_envelope(account_alias: str, update: dict, *, branch: str | None = None,
                   actor: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the authoritative lossless ``telegram`` envelope for one Update.

    ``update`` is deep-copied verbatim — every branch, nested object, and
    unknown future field survives byte-for-byte after JSON round-trip. All
    derived metadata (event identity, branch, actor policy result) lives
    beside ``update``, never inside it.

    ``event_id`` is the immutable identity of the root update that created
    the record. ``current_event_id`` is the additive *last-applied inbound
    event* identity: it starts equal to ``event_id`` and is advanced to each
    matched edit's event id when the edit is appended to ``edits`` — so
    structured projection / persistent delivery tracking can treat a merged
    edited record as new again while the root identity stays untouched.
    """
    if branch is None:
        branch, _ = classify_update(update)
    if actor is None:
        actor = resolve_update_actor(update)
    event_id = event_id_for(account_alias, update)
    return {
        "schema": TELEGRAM_ENVELOPE_SCHEMA,
        "event_id": event_id,
        "current_event_id": event_id,
        "account": account_alias,
        "branch": branch,
        "update_id": update.get("update_id") if isinstance(update, dict) else None,
        "actor": actor,
        "update": copy.deepcopy(update),
    }
