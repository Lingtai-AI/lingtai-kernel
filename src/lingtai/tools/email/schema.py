"""Schema and description for the email intrinsic tool."""
from __future__ import annotations

from .primitives import mode_field


def get_description(lang: str = "en") -> str:
    return "LingTai email protocol within your .lingtai/ network — NOT real internet email (for Gmail/Outlook use the imap tool). Addresses are bare paths under .lingtai/ with no @ signs (e.g. human for the operator). Reply discipline: always reply on the channel the message arrived on; prefer reply over send. Never reply via text output — that is your private diary, not a comms channel. Always address people by sender_nickname if set, else sender_name. Call email(action='manual') to return the installed email-manual skill."


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
                "description": 'send: send with optional cc/bcc (requires address, message; message body max 50,000 chars because unread bodies are injected in full into persistent notifications). check: list mailbox with preview of each email (up to 500 chars). read: fetch inbox emails by ID list (email_id=[id1, id2, ...]) AND marks each as read; ordinary unread content is already injected in notification_persistent.email, so prefer dismiss when you only need to clear handled mail. dismiss: same read-state effect as read but returns no bodies — preferred after handling content visible in persistent notification. reply: reply to email (requires email_id, message). reply_all: reply to all recipients. search: regex search mailbox. archive/delete: move/remove from inbox or archive. contacts/add_contact/remove_contact/edit_contact manage contacts. manual returns the installed email-manual skill without reading or changing mailbox state.',
            },
            "address": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": 'Target address(es) for send',
            },
            "cc": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'CC addresses — visible to all recipients',
            },
            "bcc": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'BCC addresses — hidden from other recipients',
            },
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'File paths to attach (for send)',
            },
            "subject": {"type": "string", "description": 'Email subject line'},
            "message": {"type": "string", "description": 'Email body (max 50,000 chars; longer internal emails are rejected because unread bodies are injected in full into persistent notifications).'},
            "email_id": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'List of email IDs for read. For reply/reply_all, pass a single-element list.',
            },
            "n": {
                "type": "integer",
                "description": 'Max recent emails to show (for check, default 10)',
                "default": 10,
            },
            "query": {
                "type": "string",
                "description": 'Regex pattern for search (matches from, subject, message)',
            },
            "folder": {
                "type": "string",
                "enum": ["inbox", "sent", "archive"],
                "description": "Folder for check/search/read/delete. Default: inbox for check, both for search. Note: 'sent' is read-only — delete only works on inbox or archive.",
            },
            "delay": {
                "type": "integer",
                "description": 'Delay in seconds before delivery (default: 0). Use for scheduled or deferred sends.',
            },
            "mode": mode_field(lang),
            "type": {
                "type": "string",
                "enum": ["normal"],
                "description": "Email type (for send). Defaults to 'normal'.",
            },
            "name": {
                "type": "string",
                "description": "Contact's human-readable name (for add_contact, edit_contact)",
            },
            "note": {
                "type": "string",
                "description": 'Free-text note about the contact (for add_contact, edit_contact)',
            },
            "filter": {
                "type": "object",
                "description": 'Optional filter object for check. Pass filter={sort, from, subject, contains, after, before, unread_only, has_attachments, truncate} to narrow and control results.',
                "properties": {
                    "sort": {
                        "type": "string",
                        "enum": ["newest", "oldest"],
                        "description": "'newest' (default) or 'oldest'.",
                    },
                    "from": {
                        "type": "string",
                        "description": 'Filter by sender (case-insensitive substring match).',
                    },
                    "subject": {
                        "type": "string",
                        "description": 'Filter by subject (case-insensitive substring match).',
                    },
                    "contains": {
                        "type": "string",
                        "description": 'Filter by message body content (case-insensitive substring match).',
                    },
                    "after": {
                        "type": "string",
                        "description": 'Only show emails after this ISO 8601 timestamp (e.g. 2026-04-01T00:00:00Z).',
                    },
                    "before": {
                        "type": "string",
                        "description": 'Only show emails before this ISO 8601 timestamp.',
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": 'Only show unread emails.',
                    },
                    "has_attachments": {
                        "type": "boolean",
                        "description": 'Only show emails that have attachments.',
                    },
                    "truncate": {
                        "type": "integer",
                        "description": 'Max characters for message preview (default 500). Set to 0 for full message body.',
                        "default": 500,
                    },
                },
            },
        },
        "required": [],
    }
