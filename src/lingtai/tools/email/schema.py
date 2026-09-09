"""Description and the legacy flat schema for the email intrinsic tool.

``get_description`` remains the registered intrinsic description.

``get_schema`` below is the **legacy flat** schema. Since the ToolFamily
migration it is no longer the model-facing schema: the composed LTP v2 family
schema lives in ``__init__.py::get_schema``, and this one now describes the
*internal* ``EmailManager.handle`` argument shape (the same seam ``shell``
kept for ``ShellManager``). ``__init__.py`` re-exports it as
``get_flat_schema``. Retained rather than deleted because it is the honest
description of that still-live internal interface — see the keep/remove ledger
in the migration report.
"""
from __future__ import annotations

from .primitives import mode_field


def get_description(lang: str = "en") -> str:
    return ("Internal .lingtai mailbox only, not internet email (use imap for external mail). "
            "Use the closed action/input/reasoning envelope and only selected-action fields. "
            "Reply on the arrival channel with reply/reply_all, never in text output; use sender_nickname, else "
            "sender_name. Unread bodies are injected in full into persistent Email notifications; prefer "
            "dismiss after handling; use read "
            "for source records or attachments. email(action='manual', input={}) loads the router.")


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "send", "check", "read", "dismiss", "reply", "reply_all",
                    "search", "archive", "delete",
                    "contacts", "add_contact", "remove_contact", "edit_contact",
                    "manual",
                ],
                "description": ("Choose one action and put only its fields in input. send: new "
                                "internal message (address/message required; body max 50,000). "
                                "check: list/filter; read: fetch IDs and mark read; dismiss: mark "
                                "handled IDs read without bodies; reply/reply_all: answer on the "
                                "arrival channel. search: regex; archive/delete: move/remove mail; "
                                "contacts manage the private book; settings is read-only; manual "
                                "returns this procedure without mailbox I/O."),
            },
            "address": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": 'Peer name/path for send; abs needs explicit authorization.',
            },
            "cc": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Visible CC addresses.',
            },
            "bcc": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Hidden BCC addresses.',
            },
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Authorized source paths to attach.',
            },
            "subject": {"type": "string", "description": 'Subject.'},
            "message": {"type": "string", "description": 'Body; max 50,000 Unicode characters.'},
            "email_id": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'ID from this mailbox; replies use one ID.',
            },
            "n": {
                "type": "integer",
                "description": 'Max messages for check; default 10.',
                "default": 10,
            },
            "query": {
                "type": "string",
                "description": 'Regex query over sender, subject, and body.',
            },
            "folder": {
                "type": "string",
                "enum": ["inbox", "sent", "archive"],
                "description": "Folder; check defaults inbox, search inbox+sent, read all; sent is read-only.",
            },
            "delay": {
                "type": "integer",
                "description": 'Delivery delay in seconds; default 0.',
            },
            "mode": mode_field(lang),
            "type": {
                "type": "string",
                "enum": ["normal"],
                "description": 'Send type; default normal.',
            },
            "name": {
                "type": "string",
                "description": "Contact name.",
            },
            "note": {
                "type": "string",
                "description": "Contact note.",
            },
            "filter": {
                "type": "object",
                "description": "Optional check filters; see email-manual for fields and defaults.",
                "properties": {
                    "sort": {
                        "type": "string",
                        "enum": ["newest", "oldest"],
                        "description": "Sort newest (default) or oldest.",
                    },
                    "from": {
                        "type": "string",
                        "description": "Case-insensitive sender substring.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Case-insensitive subject substring.",
                    },
                    "contains": {
                        "type": "string",
                        "description": "Case-insensitive body substring.",
                    },
                    "after": {
                        "type": "string",
                        "description": "Only messages after an ISO 8601 timestamp.",
                    },
                    "before": {
                        "type": "string",
                        "description": "Only messages before an ISO 8601 timestamp.",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only unread messages.",
                    },
                    "has_attachments": {
                        "type": "boolean",
                        "description": "Only messages with attachments.",
                    },
                    "truncate": {
                        "type": "integer",
                        "description": "Preview characters; default 500, 0 means full body.",
                        "default": 500,
                    },
                },
            },
        },
        "required": [],
    }
