# src/lingtai/addons/telegram/manager.py
"""TelegramManager — tool dispatch + filesystem persistence.

Storage layout:
    working_dir/telegram/{account}/inbox/{uuid}/message.json
    working_dir/telegram/{account}/inbox/{uuid}/attachments/
    working_dir/telegram/{account}/sent/{uuid}/message.json
    working_dir/telegram/{account}/contacts.json
    working_dir/telegram/{account}/read.json

Mirrors IMAPMailManager patterns with Telegram-specific adaptations.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

import logging
import threading

from lingtai.adapters.posix.agent_presence import PosixAgentPresenceStoreAdapter
from lingtai.kernel._frontmatter import strip_frontmatter
from lingtai.kernel.agent_presence import observe_alive
from lingtai.kernel.state import AgentState
from lingtai.mcp_servers.task_card import (
    TaskCardEventProjection,
    TaskCardResident,
    TaskCardResidentTransport,
    TaskCardRoute,
)

from .. import _skill
from . import _family
from . import updates as tg_updates
from .account import TelegramRateLimitError

if TYPE_CHECKING:
    from lingtai.kernel.notification_store import NotificationStorePort
    from .service import TelegramService

log = logging.getLogger(__name__)


def _load_notification_header_template() -> str:
    text = resources.files(__package__).joinpath("notification_header.md").read_text(
        encoding="utf-8"
    )
    return strip_frontmatter(text)


# ---------------------------------------------------------------------------
# Bundled usage manual (skill format) — SKILL.md ships in this package folder.
# action='manual' reads the full body; the YAML frontmatter is parsed and the
# name/description are injected into the tool schema as a progressive-disclosure
# catalog entry, while the full body stays behind action='manual'.
# ---------------------------------------------------------------------------

_SKILL_NAME = "telegram-mcp-manual"
_SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH = _skill.load_skill(__package__)


_NOTIFICATION_HEADER_TEMPLATE = _load_notification_header_template()
_NOTIFICATION_CHANNEL = "mcp.telegram"
_COMPOUND_ID_RE = re.compile(r"#([^\s:#]+:-?\d+:\d+)\b")
_CONVERSATION_PREVIEW_MESSAGES = 20
# Keep 20 structured Telegram messages below the MCP inbox structured metadata cap.
_STRUCTURED_MESSAGE_TEXT_CAP = 500
# Max serialized chars for a full raw envelope attached to a structured
# message (current/referenced only — older window entries carry a compact
# telegram_ref). Leaves headroom under the 20k LICC structured-family cap so
# attaching the envelope cannot knock out the whole recent_messages family.
# Oversize envelopes degrade to an exact recoverable reference (event id +
# read instructions); the durable inbox record always keeps the full raw.
_STRUCTURED_ENVELOPE_JSON_CAP = 12_000
_DOCUMENT_DOWNLOAD_REASON_CAP = 200
_TASK_CARD_BACKEND_REASON_CAP = 200
_TELEGRAM_API_ERROR_PREFIX = "Telegram API error: "

# Task Card edit outcomes are deliberately narrower than generic transport
# success/failure. Telegram reports an identical edit as a 400 no-op; that means
# the resident already carries the proposed content and must never trigger a
# replacement. Only the exact Bot API conditions which prove the message itself
# cannot be edited permit replacement; every unknown/network/provider failure
# fails loud and leaves the resident and committed slot state untouched.
_TASK_CARD_EDIT_OK = TaskCardResident.EDIT_OK
_TASK_CARD_EDIT_IMPOSSIBLE = TaskCardResident.EDIT_IMPOSSIBLE
_TASK_CARD_EDIT_FAILED = TaskCardResident.EDIT_FAILED
_TASK_CARD_EDIT_UNCHANGED = "bad request: message is not modified"
_TASK_CARD_EDIT_IMPOSSIBLE_DESCRIPTIONS = frozenset({
    "bad request: message to edit not found",
    "bad request: message can't be edited",
    "bad request: message can not be edited",
})
_TASK_CARD_DELETE_OK = TaskCardResident.DELETE_OK
_TASK_CARD_DELETE_MISSING = TaskCardResident.DELETE_MISSING
_TASK_CARD_DELETE_NONDELETABLE = TaskCardResident.DELETE_NONDELETABLE
_TASK_CARD_DELETE_FAILED = TaskCardResident.DELETE_FAILED
_TASK_CARD_DELETE_MISSING_DESCRIPTIONS = frozenset({
    "bad request: message to delete not found",
})
_TASK_CARD_DELETE_NONDELETABLE_DESCRIPTIONS = frozenset({
    "bad request: message can't be deleted for everyone",
    "bad request: message can not be deleted for everyone",
})

# Fixed human warning shown on every Task Card render (running and frozen
# last-behavior). Jason: never reply to the card; point directly to the local
# command that controls its delivery. Kept short so it always fits under the
# Telegram message-size bound even under multi-row length pressure. The
# "current: X" suffix is appended per-render from the manager's live
# normal-row setting; see ``_task_card_footer``.
_TASK_CARD_FOOTER = TaskCardEventProjection.FOOTER
_TASK_CARD_DEFAULT_NORMAL_ROWS = TaskCardEventProjection.DEFAULT_NORMAL_ROWS
_TASK_CARD_METADATA_MAX_CHARS = TaskCardEventProjection.METADATA_MAX_CHARS
_TASK_CARD_METADATA_MAX_LINES = TaskCardEventProjection.METADATA_MAX_LINES

# Canonical AgentState values that render without a /refresh hint; "stuck" is
# the exact same enum plus the hint, and "offline" is not an AgentState value
# at all — it is this footer's own name for a stale heartbeat overriding an
# active/idle/asleep snapshot (see ``_task_card_agent_lifecycle_status``).
_TASK_CARD_AGENT_STATES = TaskCardEventProjection.AGENT_STATES

# Card-level "last updated" line prefix.  The automatic channel's final
# standalone line reports when that channel's event-tail snapshot was last
# rendered (not any row's start instant, and not a wall clock that advances on
# unrelated programmable-channel edits) as ``Last Updated: HH:MM:SS UTC±HH``,
# always present — unlike the retired started_at-derived line, it never
# depends on any row carrying a stamp.
_TASK_CARD_TIME_PREFIX = TaskCardEventProjection.TIME_PREFIX


def _task_card_footer(normal_rows: int) -> str:
    """Build the fixed footer with the live normal-row setting appended.

    ``normal_rows`` is trusted to already be validated to ``1-10`` by the
    caller (``TelegramManager._taskcard_normal_rows``); this only formats it.
    """
    return TaskCardEventProjection.footer(normal_rows)


def _format_task_card_current_time(now: datetime) -> str:
    """Render a render-time instant as ``HH:MM:SS UTC±HH`` (hour-only offset).

    Mirrors the kernel's per-row ``_format_task_card_timestamp`` shape so the
    bottom line and each row's own stamp read consistently. Returns ``""`` for
    a naive ``datetime`` (no usable offset) so the render simply omits the
    line rather than raising.
    """
    return TaskCardEventProjection.format_current_time(now)


def _safe_document_download_reason(exc: Exception) -> str:
    """Return a bounded provider reason without retaining arbitrary exception text."""
    detail = str(exc)
    if detail.startswith(_TELEGRAM_API_ERROR_PREFIX):
        description = " ".join(detail[len(_TELEGRAM_API_ERROR_PREFIX):].split())
        if description:
            return description[:_DOCUMENT_DOWNLOAD_REASON_CAP]

    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    exc_class = type(exc).__name__
    if isinstance(status, int) and not isinstance(status, bool):
        return f"{exc_class} (HTTP {status})"
    return exc_class


def _safe_task_card_backend_reason(exc: Exception) -> str:
    """Return one redacted, bounded Telegram transport reason for controller use."""
    from lingtai.kernel.trace_redaction import redact_text

    reason = " ".join(redact_text(_safe_document_download_reason(exc)).split())
    return reason[:_TASK_CARD_BACKEND_REASON_CAP] or type(exc).__name__


def _document_download_failure_notice(reason: str) -> str:
    if reason.casefold() == "bad request: file is too big":
        guidance = (
            "Ask the sender to split the document into parts no larger than 20 MB "
            "or use another transfer method."
        )
    else:
        guidance = (
            "Ask the sender to resend the document or use another transfer method."
        )
    return f"[Document download failed: {reason}. {guidance}]"


def _looks_like_compound_id(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 3 or not parts[0]:
        return False
    try:
        int(parts[1])
        int(parts[2])
    except ValueError:
        return False
    return True


def _mirror_identity_account(value: str) -> str | None:
    """Read-state-only identity validator for notification-mirror clearing.

    Accepts a normal numeric Telegram compound ID ``account:chat:message`` or
    exactly the reserved synthetic events-bucket form
    ``account:updates:<update_id>`` and returns the account segment needed to
    look up ``read.json``; returns ``None`` for malformed/arbitrary strings.
    Deliberately separate from the strict ``_parse_compound_id``, which keeps
    rejecting the reserved bucket for reply/edit/delete/outbound targeting —
    clearing a mirror after a documented read is read-state bookkeeping, not
    an outbound operation.
    """
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 3 or not parts[0]:
        return None
    if parts[1] != tg_updates.SYNTHETIC_EVENTS_CHAT_ID:
        try:
            int(parts[1])
        except ValueError:
            return None
    try:
        int(parts[2])
    except ValueError:
        return None
    return parts[0]


# Emoji reactions for different states (Bot API 7.0+)
REACTION_SEEN = [{"type": "emoji", "emoji": "👀"}]      # Message received
REACTION_DONE = [{"type": "emoji", "emoji": "✅"}]       # Response sent


class TypingIndicatorManager:
    """Manages automatic typing indicators for Telegram chats.

    Sends typing indicator immediately, then re-sends every 5 seconds
    (Telegram auto-expires them). Best-effort — never blocks or fails.
    """

    def __init__(self) -> None:
        self._active_chats: dict[tuple[str, int], threading.Event] = {}
        self._lock = threading.Lock()

    def start_typing(self, account: Any, chat_id: int) -> None:
        """Start sending typing indicators for a chat."""
        key = (account.alias, chat_id)
        with self._lock:
            if key in self._active_chats:
                return  # Already typing
            stop_event = threading.Event()
            self._active_chats[key] = stop_event

        def _typing_loop() -> None:
            while not stop_event.is_set():
                try:
                    account.send_chat_action(chat_id, "typing")
                except Exception as e:
                    log.debug("Typing indicator failed for %s:%s: %s",
                              account.alias, chat_id, e)
                # Wait 4 seconds (Telegram expires at 5s)
                stop_event.wait(4.0)
            # Clean up
            with self._lock:
                self._active_chats.pop(key, None)

        thread = threading.Thread(
            target=_typing_loop,
            daemon=True,
            name=f"typing-{account.alias}-{chat_id}",
        )
        thread.start()

    def stop_typing(self, account: Any, chat_id: int) -> None:
        """Stop sending typing indicators for a chat."""
        key = (account.alias, chat_id)
        with self._lock:
            stop_event = self._active_chats.get(key)
        if stop_event:
            stop_event.set()

    def stop_all(self) -> None:
        """Stop all typing indicators."""
        with self._lock:
            for stop_event in self._active_chats.values():
                stop_event.set()
            self._active_chats.clear()


# Global typing indicator manager
_typing_manager = TypingIndicatorManager()

# Module-level cache for WhisperModel instances to avoid reloading weights
_whisper_model_cache: dict[str, Any] = {}


def _get_whisper_model(model_name: str) -> Any:
    """Get or create a cached WhisperModel instance."""
    if model_name not in _whisper_model_cache:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper is required for Telegram voice transcription; "
                "reinstall lingtai so its required dependencies are present"
            ) from e
        _whisper_model_cache[model_name] = WhisperModel(
            model_name, device="cpu", compute_type="int8"
        )
    return _whisper_model_cache[model_name]


def _transcribe_voice(audio_path: str, model_name: str = "base") -> dict:
    """Transcribe a voice/audio file using faster-whisper.

    Returns a dict with 'text' (transcript) and metadata, or an error dict.
    Uses cached WhisperModel to avoid reloading weights on every call.
    """
    try:
        whisper_model = _get_whisper_model(model_name)
        segments_iter, info = whisper_model.transcribe(audio_path)
        segments_list = list(segments_iter)

        transcript_segments = []
        for seg in segments_list:
            entry = {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            }
            transcript_segments.append(entry)

        full_text = " ".join(s["text"] for s in transcript_segments).strip()

        return {
            "text": full_text,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(info.duration, 2),
            "segments": transcript_segments,
        }
    except Exception as e:
        log.warning("Voice transcription failed: %s", e)
        return {"error": str(e)}

SUPPORTED_SEND_MEDIA_TYPES = ("photo", "document")


SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "send", "check", "read", "reply", "search",
                "delete", "edit",
                "contacts", "add_contact", "remove_contact",
                "accounts", "manual",
            ],
            "description": (
                "send: send message to a chat (chat_id, text, rendering_mode; optional media, reply_markup, placeholder, chat_action, entities). "
                "For charts, reports, generated artifacts, and other files the user should open intact, prefer media.type='document'; use media.type='photo' only when an inline Telegram photo preview is desired, because photo previews may crop, compress, or display poorly for text-heavy graphics. "
                "If chat_action is set and no text/media is provided, sends a typing "
                "indicator (auto-expires after 5s) instead of a message. "
                "check: list recent conversations with unread counts (optional account). "
                "read: read messages from a chat (chat_id; optional limit). "
                "reply: reply to a specific message (message_id from read results, text, rendering_mode; optional entities). "
                "search: search messages (query; optional account, chat_id). "
                "delete: delete a bot message (message_id). "
                "edit: edit a bot message (message_id, text, rendering_mode; optional reply_markup, entities). "
                "contacts: list saved contacts. "
                "add_contact: save a chat alias (chat_id, alias); this does not grant inbound permission. "
                "To receive messages from that user, their Telegram user ID must also be in allowed_users. "
                "remove_contact: remove a contact (alias or chat_id). "
                "accounts: list configured bot accounts. "
                + _skill.manual_action_description(_SKILL_FRONTMATTER, _SKILL_NAME)
            ),
        },
        "account": {
            "type": "string",
            "description": "Bot account alias (optional — defaults to first configured account)",
        },
        "chat_id": {
            # Integer Telegram chat ID, or exactly the one reserved synthetic
            # events-bucket identifier for read/search recovery of non-chat
            # updates. Arbitrary strings remain schema-invalid.
            "anyOf": [
                {"type": "integer"},
                {"type": "string", "enum": [tg_updates.SYNTHETIC_EVENTS_CHAT_ID]},
            ],
            "description": (
                "Telegram chat ID (integer). read/search also accept the "
                f"reserved '{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}' bucket, "
                "which holds non-chat update events (reactions, polls, "
                "member/boost/business events, inline-only callbacks, unknown "
                "branches); send requires a real numeric chat ID."
            ),
        },
        "text": {
            "type": "string",
            "description": "Message text",
        },
        "message_id": {
            "type": "string",
            "description": "Compound message ID: {account}:{chat_id}:{message_id}",
        },
        "media": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": list(SUPPORTED_SEND_MEDIA_TYPES)},
                "path": {"type": "string"},
            },
            "description": (
                "Media attachment: {type: 'photo'|'document', path: '/path/to/file'}. "
                "For charts, HTML/SVG/PNG reports, CSVs, PDFs, and other generated artifacts that should arrive as an intact file, use type='document'. "
                "Use type='photo' only for native inline photo previews; Telegram photo delivery can crop, compress, thumbnail, or otherwise display text-heavy charts poorly. "
                "Do not paste local file paths in message text as a substitute for attaching the file."
            ),
        },
        "reply_markup": {
            "type": "object",
            "description": "Inline keyboard markup",
        },
        "rendering_mode": {
            "type": "string",
            "enum": ["plain_text", "HTML", "MarkdownV2", "Markdown", "entities"],
            "description": (
                "Required for every content-bearing send/reply/edit. Choose "
                "plain_text, HTML, Markdown, MarkdownV2, or entities; there is no "
                "default. The runtime maps the choice to Telegram parse_mode or "
                "MessageEntity data."
            ),
        },
        "entities": {
            "type": "array",
            "description": "Telegram MessageEntity[] for message text formatting (send/reply/edit).",
        },
        "caption_entities": {
            "type": "array",
            "description": "Telegram MessageEntity[] for media captions.",
        },
        "link_preview_options": {
            "type": "object",
            "description": "Telegram LinkPreviewOptions for text messages.",
        },
        "disable_web_page_preview": {
            "type": "boolean",
            "description": "Compatibility shortcut to disable link previews for text messages.",
        },
        "placeholder": {
            "type": "boolean",
            "description": (
                "send only — send 'text' as a live-status placeholder message "
                "immediately and return its compound message_id so the agent can "
                "edit that same message later with updated status. Also fires a "
                "typing chat action so the user sees 'is typing…' while the agent "
                "works. Use for long-running responses (>5s) to avoid the "
                "perception of silence. Edit the placeholder at meaningful phase "
                "changes to show progress; the final answer must be sent as a "
                "separate durable send/reply message — the placeholder is "
                "progress-only. Automatic Task Card progress is separate from "
                "these durable send/reply messages."
            ),
            "default": False,
        },
        "limit": {
            "type": "integer",
            "description": "Max messages to return (for read, default 10)",
            "default": 10,
        },
        "query": {
            "type": "string",
            "description": "Search query (regex pattern)",
        },
        "alias": {
            "type": "string",
            "description": "Contact alias for add_contact/remove_contact",
        },
        "chat_action": {
            "type": "string",
            "enum": ["typing", "upload_photo", "upload_document", "upload_voice", ""],
            "description": (
                "For send action only. When set and no text/media is provided, "
                "sends a chat action indicator (e.g. 'typing...') instead of a "
                "message. Auto-expires after 5 seconds — re-send periodically "
                "during long tasks to keep it visible. Omit or pass an empty "
                "string for no chat action."
            ),
        },
    },
    "required": ["action"],
}

DESCRIPTION = (
    "Telegram bot client — interact with Telegram users via Bot API. "
    "MCP OWNERSHIP: this MCP belongs to the orchestrator (admin). If you are "
    "an avatar (your admin block is empty or all admin privileges are false), "
    "do not attempt to configure or reconfigure this MCP — your orchestrator "
    "manages it, and if the network needs this MCP to reach you the wiring "
    "is propagated to your session automatically. "
    "Use 'send' for outgoing messages (text, photos, documents, inline keyboards, rich formatting). "
    "'check' to see recent conversations. "
    "'read' to read messages from a specific chat. "
    "'reply' to respond to a message (use compound ID from read results). "
    "'search' to find messages by text/sender. "
    "'delete'/'edit' to modify bot messages. "
    "'contacts' to manage saved contacts. "
    "'accounts' to list configured bot accounts. "
    "Voice messages are automatically transcribed using Whisper (local) and delivered as text. "
    "Rich feedback: automatic typing indicators, emoji reactions (👀 seen, ✅ done), "
    "and live-status messages for long-running tasks (placeholder + edit-in-place). "
    "Automatic Task Card progress is separate: when the current agent setting is "
    "`taskcard: True`, every tool call with an explicit `reasoning` argument may be "
    "projected into a live task card during Telegram-originated turns — the agent "
    "does not manage this card."
)

# Public callers receive the strict LTP-v2 family schema. Manager dispatch
# remains the internal flat action boundary after family validation.
SCHEMA = _family.TELEGRAM_SCHEMA


class TelegramManager:
    """Tool handler + filesystem manager for the Telegram addon."""

    def __init__(
        self,
        service: "TelegramService",
        *,
        working_dir: Path,
        notification_store: "NotificationStorePort",
        on_inbound: "Callable[[dict], None]",
    ) -> None:
        self._service = service
        self._working_dir = Path(working_dir)
        self._notification_store = notification_store
        self._on_inbound = on_inbound
        # Duplicate send protection: (account, chat_id, text) → count
        self._last_sent: dict[tuple[str, int, str], int] = {}
        self._dup_free_passes = 2
        # Resident Task Card composition (Jason #7258/#7259): one tracked resident
        # target per account+chat, composed from two fully independent channels —
        # "automatic" (the agent-event-tail broadcast) and "programmable" (the
        # intrinsic taskcard/ artifact projected read-only by Telegram).
        # ``TaskCardResident`` owns the
        # frames, per-route locks, and atomic enablement. Updating one channel
        # never reads, advances, or overrides the other's frame.
        self._resident = TaskCardResident(
            enabled=self._raw_taskcard_enabled(),
            transport=TaskCardResidentTransport(
                get_resident=lambda route: self._get_resident_task_card(
                    route.account,
                    route.chat_id,
                ),
                matches_route=lambda route, resident_id: (
                    self._resident_id_matches_route(route, resident_id)
                ),
                is_superseded=lambda route, resident_id: self._resident_superseded(
                    route.account,
                    route.chat_id,
                    resident_id,
                ),
                edit=lambda resident_id, text: self._try_update_progress_message(
                    resident_id,
                    text,
                ),
                delete=lambda resident_id: self._delete_task_card_message_outcome(
                    resident_id,
                ),
                send=lambda route, text: self.send_progress_message(
                    route.account,
                    route.chat_id,
                    text,
                ),
                persist=lambda route, resident_id: self._set_resident_task_card(
                    route.account,
                    route.chat_id,
                    resident_id,
                ),
            ),
        )
        listener = getattr(self._service, "set_taskcard_listener", None)
        if callable(listener):
            listener(self._on_taskcard_changed)
        # Automatic Task Card event-tail state (agent-behavior broadcast). See
        # ``## Automatic Task Card event tail`` below for the full contract; kept
        # as plain instance attributes (not a helper object) so no second durable
        # source of truth can accidentally form around it.
        self._task_card_event_path: Path | None = None
        self._task_card_event_offset = 0
        self._task_card_event_size = 0
        self._task_card_event_inode: int | None = None
        # Portable "is this still the same file?" token — the inode where the
        # platform supplies a real one, else the creation timestamp. ``None``
        # always means *unknown*, never *changed*. See ``_event_file_identity``.
        self._task_card_event_identity: tuple[str, float | int] | None = None
        # Grouped by provider call; the compatibility row view is derived.
        self._task_card_event_groups: list[dict] = []
        # The current telemetry snapshot is carried only by the latest final
        # ``notification_block_injected`` event. ``None`` means no such carrier has been seen;
        # an empty dict is a seen-but-malformed carrier and deliberately clears
        # any older snapshot.
        self._task_card_event_metadata: dict | None = None
        self._task_card_event_lock = threading.Lock()
        self._task_card_tail_thread: threading.Thread | None = None
        self._task_card_tail_stop = threading.Event()
        self._programmable_task_card_thread: threading.Thread | None = None
        self._programmable_task_card_stop = threading.Event()

    @property
    def _task_card_channels(self) -> dict[str, dict[str, str]]:
        return self._resident.frames

    @_task_card_channels.setter
    def _task_card_channels(self, value: dict[str, dict[str, str]]) -> None:
        self._resident.frames = value

    @property
    def _task_card_delivery_locks(self) -> dict[str, threading.RLock]:
        return self._resident.locks

    @_task_card_delivery_locks.setter
    def _task_card_delivery_locks(self, value: dict[str, threading.RLock]) -> None:
        self._resident.locks = value

    def _on_taskcard_changed(self, enabled: bool) -> None:
        """Apply one durable setting transition; reproject once when enabled."""
        if self._resident.set_enabled(enabled) and enabled:
            self._broadcast_task_card_event_window()
            self._broadcast_programmable_task_card_file()

    def _account_dir(self, account: str) -> Path:
        return self._working_dir / "telegram" / account

    def _resolve_account(self, args: dict) -> str:
        """Get account alias from args, defaulting to first account."""
        return args.get("account") or self._service.default_account.alias

    def _raw_taskcard_enabled(self) -> bool:
        """Read the durable setting without crossing the resident boundary."""
        getter = getattr(self._service, "taskcard_enabled", None)
        return bool(getter()) if callable(getter) else True

    def _taskcard_enabled(self) -> bool:
        """Read resident state, synchronizing narrow service doubles."""
        self._resident.set_enabled(self._raw_taskcard_enabled())
        return self._resident.enabled()

    def _taskcard_normal_rows(self) -> int:
        """Read the current normal-row setting (1-10) at projection time.

        The fallback preserves compatibility for narrow test/third-party service
        doubles; the production TelegramService always provides the durable getter.
        """
        getter = getattr(self._service, "taskcard_normal_rows", None)
        if not callable(getter):
            return _TASK_CARD_DEFAULT_NORMAL_ROWS
        value = getter()
        if type(value) is not int or not 1 <= value <= 10:
            return _TASK_CARD_DEFAULT_NORMAL_ROWS
        return value

    @staticmethod
    def _parse_compound_id(compound_id: str) -> tuple[str, int, int]:
        """Parse '{account}:{chat_id}:{message_id}' → (account, chat_id, message_id)."""
        parts = compound_id.split(":")
        if len(parts) != 3:
            raise ValueError(f"Invalid message ID format: {compound_id}")
        if parts[1] == tg_updates.SYNTHETIC_EVENTS_CHAT_ID:
            raise ValueError(
                f"{compound_id!r} is a synthetic events-bucket record; it has "
                "no real chat to reply/edit/delete into (read/search-only)"
            )
        return parts[0], int(parts[1]), int(parts[2])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._service.start()
        self._start_task_card_tail()
        self._start_programmable_task_card_poller()

    def stop(self) -> None:
        self._stop_programmable_task_card_poller()
        self._stop_task_card_tail()
        self._service.stop()

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _invalid_chat_id_error(args: dict) -> dict | None:
        """Reject a ``chat_id`` the public SCHEMA declares invalid.

        The schema's ``anyOf`` allows an integer or exactly the reserved
        synthetic events bucket, and its comment states that arbitrary strings
        remain invalid. Official SDK v2's low-level server advertises that
        schema but never applies it, so without this check an arbitrary string
        would fall through to a silently empty read/search instead of an error.
        """
        if "chat_id" not in args:
            return None
        chat_id = args["chat_id"]
        if isinstance(chat_id, bool):
            # bool is an int subclass; a boolean is not a chat ID.
            return {"error": f"chat_id must be an integer Telegram chat ID; got {chat_id!r}"}
        if isinstance(chat_id, int):
            return None
        if chat_id == tg_updates.SYNTHETIC_EVENTS_CHAT_ID:
            return None
        return {
            "error": (
                "chat_id must be an integer Telegram chat ID, or exactly the "
                f"reserved '{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}' bucket; "
                f"got {chat_id!r}"
            ),
        }

    def handle(self, args: dict) -> dict:
        # Keep the standalone manager surface in parity with the public family.
        # The flat internal boundary also remains for retained legacy private-
        # reverse compatibility code and existing manager tests; the current
        # Telegram server exposes no private ``task_card`` route. Family
        # validation occurs before any manager action I/O; child dispatch
        # re-enters here with a flat action mapping exactly once.
        if isinstance(args, dict) and {"input", "reasoning"}.issubset(args):
            return _family.handle_telegram(self, args)
        action = args.get("action")
        chat_id_error = self._invalid_chat_id_error(args)
        if chat_id_error is not None:
            return chat_id_error
        try:
            if action == "send":
                return self._send(args)
            elif action == "check":
                return self._check(args)
            elif action == "read":
                return self._read(args)
            elif action == "reply":
                return self._reply(args)
            elif action == "search":
                return self._search(args)
            elif action == "delete":
                return self._delete(args)
            elif action == "edit":
                return self._edit(args)
            elif action == "contacts":
                return self._contacts(args)
            elif action == "add_contact":
                return self._add_contact(args)
            elif action == "remove_contact":
                return self._remove_contact(args)
            elif action == "accounts":
                return self._accounts()
            elif action == "manual":
                return self._manual()
            elif action == "_task_card_update":
                return self._handle_task_card_update(args)
            else:
                return {"error": f"Unknown telegram action: {action}"}
        except TelegramRateLimitError as exc:
            result: dict[str, Any] = {
                "status": "error",
                "error": str(exc),
                "error_code": exc.error_code,
                "auto_retry": False,
                "guidance": (
                    "Do not retry this Telegram action automatically; Telegram "
                    "did not supply a valid retry_after."
                ),
            }
            if exc.retry_after is not None:
                result["retryable"] = True
                result["retry_after"] = exc.retry_after
                result["guidance"] = (
                    f"Wait at least {exc.retry_after} seconds before starting a new "
                    "Telegram action; do not retry it automatically."
                )
            return result
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Incoming messages — called by TelegramService via on_message
    # ------------------------------------------------------------------

    def on_incoming(self, account_alias: str, update: dict) -> None:
        """Persist incoming update to disk and notify agent."""
        msg_id = str(uuid4())
        acct_dir = self._account_dir(account_alias)
        msg_dir = acct_dir / "inbox" / msg_id
        msg_dir.mkdir(parents=True, exist_ok=True)

        # Issue #8: Rich intermediate feedback
        # Get account and chat_id for typing indicator and reactions
        try:
            account = self._service.get_account(account_alias)
        except (KeyError, Exception) as e:
            log.warning("Failed to get account %s for feedback: %s", account_alias, e)
            account = None
        chat_id = None
        tg_message_id = None

        # Classify the update once. The raw envelope built here is the
        # authoritative lossless record for the whole inbound path: it rides
        # on the persisted payload, the structured persistent lane, and the
        # read/search projections. The normalized keys below stay the
        # backward-compatible concise view.
        branch, branch_obj = tg_updates.classify_update(update)
        if branch is None:
            try:
                msg_dir.rmdir()
            except OSError:
                pass
            return  # nothing but an update_id — no branch object to record
        actor = tg_updates.resolve_update_actor(update)
        envelope = tg_updates.build_envelope(
            account_alias, update, branch=branch, actor=actor,
        )
        persist_dir = msg_dir

        # Extract message data based on update type
        if "message" in update:
            tg_msg = update["message"]
            chat_id = tg_msg["chat"]["id"]
            tg_message_id = tg_msg["message_id"]
            compound_id = f"{account_alias}:{chat_id}:{tg_message_id}"
            sender = tg_msg.get("from", {})
            payload = {
                "id": compound_id,
                "from": sender,
                "chat": tg_msg.get("chat", {}),
                "date": datetime.fromtimestamp(
                    tg_msg.get("date", 0), tz=timezone.utc,
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "text": tg_msg.get("text") or tg_msg.get("caption") or "",
                "media": None,
                "reply_to_message_id": None,
                "callback_query": None,
            }
            # Handle reply_to
            if tg_msg.get("reply_to_message"):
                payload["reply_to_message_id"] = tg_msg["reply_to_message"]["message_id"]
            # Handle media
            self._download_media(account_alias, tg_msg, msg_dir, payload)

            # Get username before voice transcription (needed for logging)
            username = sender.get("username") or sender.get("first_name", "unknown")

            # Issue #8: Start typing indicator immediately
            if account:
                _typing_manager.start_typing(account, chat_id)

            # Issue #8: Add "seen" reaction (👀)
            if account:
                try:
                    account.set_message_reaction(chat_id, tg_message_id, REACTION_SEEN)
                except Exception as e:
                    log.debug("Failed to add 'seen' reaction: %s", e)

            # Issue #6: Transcribe voice messages
            if payload.get("media") and payload["media"].get("type") in ("voice", "audio"):
                audio_path = payload["media"].get("path")
                if audio_path and Path(audio_path).exists():
                    log.info("Transcribing voice message from %s:%s", account_alias, username)
                    transcript = _transcribe_voice(audio_path)
                    if "error" not in transcript:
                        payload["text"] = transcript.get("text", "")
                        payload["voice_transcript"] = {
                            "text": transcript.get("text", ""),
                            "language": transcript.get("language"),
                            "duration": transcript.get("duration"),
                            "segments": transcript.get("segments"),
                        }
                        log.info("Voice transcription successful: %s chars", len(payload["text"]))
                    else:
                        # Graceful fallback: indicate transcription failed
                        payload["text"] = f"[Voice message received — transcription failed: {transcript.get('error', 'unknown error')}]"
                        log.warning("Voice transcription failed: %s", transcript.get("error"))

        elif "callback_query" in update:
            cq = update["callback_query"]
            tg_msg = cq.get("message", {})
            sender = cq.get("from", {})
            chat = tg_msg.get("chat", {})
            chat_id = chat.get("id", 0)
            tg_message_id = tg_msg.get("message_id", 0)
            compound_id = f"{account_alias}:{chat_id}:{tg_message_id}"
            if not tg_msg:
                # Inline-mode callback (inline_message_id only, no chat
                # message): give it a real discoverable identity in the
                # synthetic events bucket instead of the degenerate
                # "<alias>:0:0". Full seven-field CallbackQuery is in the
                # envelope either way.
                compound_id = (
                    f"{account_alias}:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:"
                    f"{update.get('update_id')}"
                )
                chat = {
                    "id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
                    "type": "synthetic",
                    "synthetic": True,
                }
                chat_id = None
            payload = {
                "id": compound_id,
                "from": sender,
                "chat": chat,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "text": "",
                "media": None,
                "reply_to_message_id": None,
                "callback_query": cq.get("data"),
            }
            username = sender.get("username") or sender.get("first_name", "unknown")

            # Issue #8: Start typing indicator for callback queries
            if chat_id and account:
                _typing_manager.start_typing(account, chat_id)

        elif branch in tg_updates.EDIT_BRANCHES:
            tg_msg = branch_obj if isinstance(branch_obj, dict) else {}
            chat = tg_msg.get("chat", {}) or {}
            compound_id = f"{account_alias}:{chat.get('id')}:{tg_msg.get('message_id')}"
            sender = tg_msg.get("from", {}) or {}
            username = sender.get("username") or sender.get("first_name") or "unknown"
            edited_text = tg_msg.get("text") or tg_msg.get("caption") or ""
            edit_date_iso = datetime.fromtimestamp(
                tg_msg.get("edit_date", tg_msg.get("date", 0)), tz=timezone.utc,
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            existing: dict | None = None
            existing_dir = self._find_inbox_by_compound_id(account_alias, compound_id)
            if existing_dir is not None:
                try:
                    existing = json.loads(
                        (existing_dir / "message.json").read_text(encoding="utf-8"),
                    )
                except (json.JSONDecodeError, OSError):
                    existing = None

            if existing is not None:
                # Merge the edit into the original record: the normalized
                # text/date track the latest version (legacy behavior) while
                # previously stored media/reply/callback context is preserved
                # and the raw edit event is appended to the original
                # envelope's append-only history — the original raw update is
                # never destroyed by an edit.
                payload = existing
                payload.pop("_dir", None)
                payload.pop("_folder", None)
                payload["text"] = edited_text
                payload["date"] = edit_date_iso
                env = payload.get("telegram")
                if isinstance(env, dict):
                    env.setdefault("edits", []).append({
                        "event_id": envelope["event_id"],
                        "update_id": envelope["update_id"],
                        "branch": branch,
                        "update": envelope["update"],
                    })
                    # The merged record's last-applied inbound event is now
                    # this edit: advance the additive current identity so a
                    # warm persistent lane (original already delivered)
                    # re-delivers the record with its raw edit evidence. The
                    # immutable root event_id stays untouched.
                    env["current_event_id"] = envelope["event_id"]
                else:
                    # Original stored before envelope support — this edit's
                    # own envelope becomes the record's root; envelope.branch
                    # says it is an edit event.
                    payload["telegram"] = envelope
                persist_dir = existing_dir
                try:
                    msg_dir.rmdir()
                except OSError as exc:
                    log.debug(
                        "failed to remove unused %s dir %s: %s", branch, msg_dir, exc,
                    )
            else:
                # Unmatched edit: keep the event visible as its own record
                # instead of silently dropping it (still no wake).
                log.info(
                    "telegram unmatched %s account=%s id=%s; recording standalone edit record",
                    branch, account_alias, compound_id,
                )
                payload = {
                    "id": compound_id,
                    "from": sender,
                    "chat": chat,
                    "date": edit_date_iso,
                    "text": edited_text,
                    "media": None,
                    "reply_to_message_id": None,
                    "callback_query": None,
                    "unmatched_edit": True,
                }
            if branch != "edited_message":
                payload["update_type"] = branch
        elif branch in tg_updates.MESSAGE_TYPED_BRANCHES:
            # Non-edit Message-typed branches beyond plain "message":
            # channel_post, business_message, guest_message. Normalized like a
            # message (real chat/message identity, media download convenience)
            # with the branch recorded in update_type.
            tg_msg = branch_obj if isinstance(branch_obj, dict) else {}
            chat = tg_msg.get("chat", {}) or {}
            chat_id = chat.get("id")
            tg_message_id = tg_msg.get("message_id")
            compound_id = f"{account_alias}:{chat_id}:{tg_message_id}"
            sender = tg_msg.get("from", {}) or {}
            payload = {
                "id": compound_id,
                "from": sender,
                "chat": chat,
                "date": datetime.fromtimestamp(
                    tg_msg.get("date", 0), tz=timezone.utc,
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "text": tg_msg.get("text") or tg_msg.get("caption") or "",
                "media": None,
                "reply_to_message_id": None,
                "callback_query": None,
                "update_type": branch,
            }
            if tg_msg.get("reply_to_message"):
                payload["reply_to_message_id"] = tg_msg["reply_to_message"]["message_id"]
            self._download_media(account_alias, tg_msg, msg_dir, payload)
            username = (
                sender.get("username") or sender.get("first_name")
                or chat.get("title") or f"telegram:{branch}"
            )
        else:
            # Every other current branch (reactions, polls, member/boost/
            # business/payment events, …) plus unknown future branches: the
            # open fallback. The complete raw update is the envelope; the
            # normalized view is a synthetic events-bucket record, explicitly
            # flagged so derived routing is never mistaken for Telegram data.
            obj = branch_obj if isinstance(branch_obj, dict) else {}
            sender = {}
            for key in ("from", "user"):
                candidate = obj.get(key)
                if isinstance(candidate, dict):
                    sender = candidate
                    break
            date_raw = obj.get("date")
            if isinstance(date_raw, (int, float)) and not isinstance(date_raw, bool):
                date_iso = datetime.fromtimestamp(
                    date_raw, tz=timezone.utc,
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            compound_id = (
                f"{account_alias}:{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}:"
                f"{update.get('update_id')}"
            )
            payload = {
                "id": compound_id,
                "from": sender,
                "chat": {
                    "id": tg_updates.SYNTHETIC_EVENTS_CHAT_ID,
                    "type": "synthetic",
                    "synthetic": True,
                },
                "date": date_iso,
                "text": "",
                "media": None,
                "reply_to_message_id": None,
                "callback_query": None,
                "update_type": branch,
                "synthetic": True,
            }
            username = (
                sender.get("username") or sender.get("first_name")
                or f"telegram:{branch}"
            )

        # The authoritative raw envelope rides on every persisted record.
        # (Matched edits already carry their original envelope + history.)
        payload.setdefault("telegram", envelope)

        (persist_dir / "message.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8",
        )

        # Forward to host via LICC. Body is a conversation preview showing the
        # last 20 messages. The agent uses telegram(action="check"|"read") to
        # fetch the full conversation; metadata carries routing keys plus a
        # structured recent-message view for _meta.agent_meta.notifications.persistent.
        text = payload.get("text", "") or payload.get("callback_query", "") or ""
        preview_metadata: dict[str, Any] = {}
        try:
            preview, preview_metadata = self._build_conversation_preview_and_metadata(
                account_alias,
                payload.get("chat", {}).get("id"),
                compound_id,
            )
        except Exception as exc:
            log.warning("_build_conversation_preview failed: %s", exc)
            preview = text[:300].replace("\n", " ")
            if len(text) > 300:
                preview += "..."
            preview = (
                f"[taskcard: {self._taskcard_enabled()}] "
                f"{preview or '(no text — see media or callback)'}"
            )

        log.info(
            "telegram_received account=%s sender=%r id=%s",
            account_alias, username, payload.get("id"),
        )

        # Update type lets agents dispatch (e.g. button press vs free text).
        # It is now the exact Update branch name (open set: unknown future
        # branches carry their own name).
        update_type = branch

        # Issue #5: Don't wake the agent for edited messages — they are
        # typically trivial corrections (typo fixes) and not worth a wake.
        # The inbox entry is still updated in-place so the agent sees the
        # latest content on next read. Generalized: only new human message
        # content and button presses wake (see updates.WAKE_BRANCHES);
        # edits, channel broadcasts, and service/aggregate events are
        # recorded without a wake.
        should_wake = branch in tg_updates.WAKE_BRANCHES

        # A real inbound message establishes the account+chat resident before
        # the first provider round.  This is intentionally the only route input
        # here: no latest-chat inference or durable route index is introduced.
        if (
            update_type == "message"
            and self._taskcard_enabled()
            and isinstance(chat_id, int)
            and not isinstance(chat_id, bool)
            and callable(getattr(account, "get_task_card", None))
            and callable(getattr(account, "set_task_card", None))
        ):
            try:
                self._ensure_task_card_resident(account_alias, chat_id)
            except Exception as exc:
                # Task Card is fail-open for the actual inbound delivery; the
                # agent still receives the message when Telegram card transport
                # is unavailable.
                log.debug("Failed to ensure inbound Task Card resident: %s", exc)

        # Issue #6: Enhance subject for voice messages
        subject = f"telegram {update_type} from {username} via {account_alias}"
        if payload.get("voice_transcript"):
            subject = f"telegram voice message from {username} via {account_alias} (transcribed)"

        try:
            self._on_inbound({
                "from": username,
                "subject": subject,
                "body": preview if preview else "(no text — see media or callback)",
                "metadata": {
                    "type": update_type,
                    # Stable per-update event identity (account + update_id),
                    # valid even for events with no chat/message, plus the
                    # actor-policy classification for auditability.
                    "event_id": envelope["event_id"],
                    "update_id": envelope["update_id"],
                    "actor_kind": actor.get("kind"),
                    "message_id": payload.get("id"),
                    "account": account_alias,
                    "chat_id": payload.get("chat", {}).get("id"),
                    # LICC preview metadata copied into .notification/mcp.telegram.json.
                    # Keep both the legacy Telegram-specific keys above and the
                    # generic chat keys below so the producer can later clear a
                    # handled notification mirror without re-reading Telegram.
                    "platform": "telegram",
                    "conversation_ref": f"{account_alias}:{payload.get('chat', {}).get('id')}",
                    # Callback queries reuse the message_id of the inline-keyboard
                    # message, so the compound ID is not unique per callback event.
                    # Leave those mirrors for explicit handling rather than
                    # auto-clearing a fresh callback because an older callback on
                    # the same Telegram message was already read.
                    "message_ref": payload.get("id") if update_type != "callback_query" else None,
                    "has_media": payload.get("media") is not None,
                    "has_callback": payload.get("callback_query") is not None,
                    "callback_data": payload.get("callback_query"),
                    "is_voice_transcript": payload.get("voice_transcript") is not None,
                    "voice_duration": payload.get("voice_transcript", {}).get("duration") if payload.get("voice_transcript") else None,
                    **preview_metadata,
                },
                "wake": should_wake,
            })
        except Exception as e:
            log.error("on_inbound callback failed for telegram msg %s: %s",
                      payload.get("id"), e)
        # Note: typing indicator continues until _send() is called by the agent.
        # _send() stops typing when it sends the response.

    def _download_media(
        self, account_alias: str, tg_msg: dict, msg_dir: Path, payload: dict,
    ) -> None:
        """Download photo/document/voice/audio attachments from a Telegram message."""
        file_id = None
        media_type = None
        media_meta: dict = {}
        document_meta: dict = {}

        if tg_msg.get("photo"):
            # Photos come as array of sizes — take the largest
            file_id = tg_msg["photo"][-1]["file_id"]
            media_type = "photo"
        elif tg_msg.get("document"):
            document = tg_msg["document"]
            file_id = document["file_id"]
            media_type = "document"
            document_meta = {
                key: document[key]
                for key in (
                    "file_name",
                    "file_size",
                    "file_id",
                    "file_unique_id",
                    "mime_type",
                )
                if document.get(key) is not None
            }
        elif tg_msg.get("voice"):
            # Voice messages: .oga format, typically short recordings
            file_id = tg_msg["voice"]["file_id"]
            media_type = "voice"
            media_meta = {
                "duration": tg_msg["voice"].get("duration", 0),
                "mime_type": tg_msg["voice"].get("mime_type", "audio/ogg"),
            }
        elif tg_msg.get("audio"):
            # Audio files: music, longer recordings, etc.
            file_id = tg_msg["audio"]["file_id"]
            media_type = "audio"
            media_meta = {
                "duration": tg_msg["audio"].get("duration", 0),
                "mime_type": tg_msg["audio"].get("mime_type", "audio/mpeg"),
                "title": tg_msg["audio"].get("title"),
                "performer": tg_msg["audio"].get("performer"),
            }

        if file_id is None:
            return

        try:
            acct = self._service.get_account(account_alias)
            filename, data = acct.get_file(file_id)
            att_dir = msg_dir / "attachments"
            att_dir.mkdir(parents=True, exist_ok=True)
            filepath = att_dir / filename
            filepath.write_bytes(data)
            payload["media"] = {
                "type": media_type,
                "filename": filename,
                "path": str(filepath),
                "size": len(data),
                **media_meta,
            }
        except Exception as exc:
            if media_type != "document":
                logging.getLogger(__name__).warning(
                    "Failed to download media: %s", exc,
                )
                return

            reason = _safe_document_download_reason(exc)
            payload["media"] = {
                "type": "document",
                **document_meta,
                "download_error": reason,
            }
            failure_notice = _document_download_failure_notice(reason)
            existing_text = str(payload.get("text") or "")
            payload["text"] = (
                f"{existing_text}\n\n{failure_notice}" if existing_text else failure_notice
            )
            log.warning(
                "Failed to download inbound Telegram document (%s); "
                "preserved metadata without path",
                reason,
            )

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------

    def _list_messages(self, account: str, folder: str = "inbox") -> list[dict]:
        """Load all messages from a folder, sorted by date (newest first)."""
        folder_dir = self._account_dir(account) / folder
        if not folder_dir.is_dir():
            return []
        messages = []
        for msg_dir in folder_dir.iterdir():
            msg_file = msg_dir / "message.json"
            if msg_dir.is_dir() and msg_file.is_file():
                try:
                    data = json.loads(msg_file.read_text(encoding="utf-8"))
                    data["_dir"] = str(msg_dir)
                    messages.append(data)
                except (json.JSONDecodeError, OSError):
                    continue
        messages.sort(key=lambda m: m.get("date", ""), reverse=True)
        return messages

    def _find_inbox_by_compound_id(self, account: str, compound_id: str) -> Path | None:
        """Find an existing inbox message dir by compound ID. Returns dir Path or None."""
        inbox_dir = self._account_dir(account) / "inbox"
        if not inbox_dir.is_dir():
            return None
        for msg_dir in inbox_dir.iterdir():
            msg_file = msg_dir / "message.json"
            if msg_dir.is_dir() and msg_file.is_file():
                try:
                    data = json.loads(msg_file.read_text(encoding="utf-8"))
                    if data.get("id") == compound_id:
                        return msg_dir
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def _conversation_messages(
        self,
        account_alias: str,
        chat_id: int | None,
        max_messages: int = _CONVERSATION_PREVIEW_MESSAGES,
    ) -> list[dict]:
        """Return recent Telegram messages for *chat_id* sorted oldest -> newest."""
        if chat_id is None:
            return []
        try:
            target_chat_id: int | str = int(chat_id)
        except (TypeError, ValueError):
            # Synthetic buckets (e.g. "updates" for non-chat events) use
            # non-numeric conversation ids.
            target_chat_id = str(chat_id)

        acct_dir = self._account_dir(account_alias)
        messages: list[dict] = []
        for folder in ("inbox", "sent"):
            folder_dir = acct_dir / folder
            if not folder_dir.is_dir():
                continue
            for msg_dir in folder_dir.iterdir():
                msg_file = msg_dir / "message.json"
                if not (msg_dir.is_dir() and msg_file.is_file()):
                    continue
                try:
                    data = json.loads(msg_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                msg_chat_id: int | str | None = None
                msg_id = data.get("id", "")
                if msg_id:
                    parts = msg_id.split(":")
                    if len(parts) == 3:
                        try:
                            msg_chat_id = int(parts[1])
                        except ValueError:
                            msg_chat_id = parts[1]
                if msg_chat_id != target_chat_id:
                    continue
                data["_folder"] = folder
                messages.append(data)

        messages.sort(key=lambda m: m.get("date") or "")
        return messages[-max_messages:]

    def _find_message_by_compound_id(
        self, account_alias: str, compound_id: str,
    ) -> dict | None:
        """Load a full stored Telegram message (inbox or sent) by compound ID.

        Returns the raw message dict with ``_folder`` set so it can be rendered
        as a structured message, or ``None`` if no local copy exists.
        """
        acct_dir = self._account_dir(account_alias)
        for folder in ("inbox", "sent"):
            folder_dir = acct_dir / folder
            if not folder_dir.is_dir():
                continue
            for msg_dir in folder_dir.iterdir():
                msg_file = msg_dir / "message.json"
                if not (msg_dir.is_dir() and msg_file.is_file()):
                    continue
                try:
                    data = json.loads(msg_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if data.get("id") == compound_id:
                    data["_folder"] = folder
                    return data
        return None

    def _referenced_messages_for_current(
        self,
        account_alias: str,
        current_compound_id: str,
        structured: list[dict],
        *,
        now: datetime | None = None,
    ) -> list[dict]:
        """Return full referenced Telegram messages missing from *structured*.

        If the current/new message replies to a Telegram message whose compound
        ID is not already present in the structured last-20 window, load the full
        referenced message from local inbox/sent storage and return it as a
        structured message so the persistent block can carry the full referenced
        message (not a snippet). Returns an empty list when there is no reply, no
        target compound ID, the target is already present, or no local copy
        exists.
        """
        if not current_compound_id:
            return []
        current = next(
            (item for item in structured if item.get("id") == current_compound_id),
            None,
        )
        if current is None:
            return []
        reply_target = current.get("reply_to")
        if not isinstance(reply_target, str) or not reply_target:
            return []
        present_ids = {item.get("id") for item in structured}
        if reply_target in present_ids:
            return []
        stored = self._find_message_by_compound_id(account_alias, reply_target)
        if stored is None:
            return []
        item = self._structured_message(
            stored,
            current_compound_id=current_compound_id,
            now=now,
            truncate_text=False,
        )
        item["source"] = "reply_target"
        return [item]

    @staticmethod
    def _relative_time(date_str: str, *, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            return date_str or "?"
        delta = (now - dt).total_seconds()
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{int(delta // 60)} min ago"
        if delta < 86400:
            return f"{int(delta // 3600)} hr ago"
        if delta < 172800:
            return "yesterday"
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def _sender_name(message: dict) -> str:
        if message.get("_folder") == "sent":
            return "me"
        frm = message.get("from") or {}
        return frm.get("username") or frm.get("first_name") or "unknown"

    @staticmethod
    def _message_text(message: dict) -> str:
        text = message.get("text", "") or message.get("callback_query", "") or ""
        if message.get("media"):
            media_type = message["media"].get("type", "media")
            text = text or f"[{media_type}]"
        if not text and message.get("update_type") not in (None, "message"):
            # Generic update events (reactions, polls, member changes, …)
            # have no text; label them by branch for previews.
            text = f"[{message['update_type']}]"
        return str(text).replace("\n", " ")

    @staticmethod
    def _truncate_structured_text(
        text: str,
        *,
        cap: int | None = _STRUCTURED_MESSAGE_TEXT_CAP,
    ) -> tuple[str, bool]:
        if cap is None or len(text) <= cap:
            return text, False
        return text[: cap - 1] + "…", True

    def _structured_message(
        self,
        message: dict,
        *,
        current_compound_id: str | None = None,
        now: datetime | None = None,
        truncate_text: bool = True,
    ) -> dict[str, Any]:
        cid = str(message.get("id", ""))
        text, text_truncated = self._truncate_structured_text(
            self._message_text(message),
            cap=_STRUCTURED_MESSAGE_TEXT_CAP if truncate_text else None,
        )
        direction = "outgoing" if message.get("_folder") == "sent" else "incoming"
        item: dict[str, Any] = {
            "id": cid,
            "direction": direction,
            "sender": self._sender_name(message),
            "date": message.get("date") or "",
            "relative_time": self._relative_time(message.get("date", ""), now=now),
            "text": text,
            "text_truncated": text_truncated,
            "taskcard": self._taskcard_enabled(),
        }
        if current_compound_id and cid == current_compound_id:
            item["is_current"] = True
        if message.get("media"):
            media = message["media"] or {}
            item["media"] = {
                key: media[key]
                for key in ("type", "filename", "size", "duration", "mime_type")
                if key in media and media[key] is not None
            }
        reply_id_raw = message.get("reply_to_message_id")
        if reply_id_raw:
            item["reply_to_message_id"] = reply_id_raw
            id_parts = cid.split(":")
            if len(id_parts) == 3:
                item["reply_to"] = f"{id_parts[0]}:{id_parts[1]}:{reply_id_raw}"
        if message.get("callback_query"):
            item["has_callback"] = True
        for flag in ("update_type", "synthetic", "unmatched_edit"):
            if message.get(flag):
                item[flag] = message[flag]
        env = message.get("telegram")
        if isinstance(env, dict):
            # Additive per-update identity beside the legacy compound id, so
            # downstream persistent dedup/delivery can distinguish events
            # (e.g. repeated callbacks) that share one compound message id.
            # For merged edited records the *current* (last-applied edit)
            # identity is used so a warm persistent lane re-delivers the
            # record with its appended raw edit evidence; the immutable root
            # event_id remains inside the envelope.
            env_event_id = env.get("current_event_id") or env.get("event_id")
            if isinstance(env_event_id, str) and env_event_id:
                item["event_id"] = env_event_id
            ref = {
                "event_id": env.get("event_id"),
                "branch": env.get("branch"),
                "update_id": env.get("update_id"),
            }
            include_full = (not truncate_text) or bool(
                current_compound_id and cid == current_compound_id
            )
            env_chars: int | None = None
            if include_full:
                try:
                    env_chars = len(
                        json.dumps(env, ensure_ascii=False, separators=(",", ":")),
                    )
                except (TypeError, ValueError):
                    env_chars = None
            if include_full and env_chars is not None and (
                env_chars <= _STRUCTURED_ENVELOPE_JSON_CAP
            ):
                # Current/referenced message: the full authoritative raw
                # envelope rides in the persistent structured lane.
                item["telegram"] = env
            elif include_full:
                # Exact recoverable representation instead of silent loss:
                # the durable inbox record keeps the full envelope and
                # telegram.read returns it verbatim.
                item["telegram_ref"] = {
                    **ref,
                    "oversize": True,
                    "json_chars": env_chars,
                    "recovery": (
                        "telegram(action='read', chat_id=<this chat>) returns "
                        "the full raw envelope from the durable inbox record"
                    ),
                }
            else:
                # Older window entries stay compact; the envelope is one
                # read away via the same durable record.
                item["telegram_ref"] = ref
        return item

    def _render_conversation_preview(
        self,
        messages: list[dict],
        *,
        chat_id: int | None,
        current_compound_id: str,
    ) -> str:
        """Render a markdown conversation preview for notification previews."""
        now = datetime.now(timezone.utc)
        taskcard = self._taskcard_enabled()
        by_id: dict[str, dict] = {m.get("id", ""): m for m in messages}
        lines: list[str] = []

        for m in messages:
            cid = m.get("id", "")
            rel = self._relative_time(m.get("date", ""), now=now)
            sender = self._sender_name(m)
            text_display = self._message_text(m)
            direction = "outgoing" if m.get("_folder") == "sent" else "incoming"
            marker = "[NEW]" if cid == current_compound_id else "[context]"
            lines.append(
                f"{marker}[{direction}][{rel}][taskcard: {taskcard}] "
                f"#{cid} {sender}: {text_display}"
            )

            reply_id_raw = m.get("reply_to_message_id")
            if reply_id_raw:
                id_parts = cid.split(":")
                if len(id_parts) == 3:
                    reply_compound = f"{id_parts[0]}:{id_parts[1]}:{reply_id_raw}"
                    orig = by_id.get(reply_compound)
                    if orig:
                        orig_rel = self._relative_time(orig.get("date", ""), now=now)
                        orig_text = orig.get("text", "") or orig.get("callback_query", "") or ""
                        orig_snippet = orig_text[:50]
                        if len(orig_text) > 50:
                            orig_snippet += "…"
                        lines.append(
                            f"  ↳ [{orig_rel}][taskcard: {taskcard}] "
                            f"#{reply_compound}: {orig_snippet}"
                        )

        header = _NOTIFICATION_HEADER_TEMPLATE.format(channel="Telegram").rstrip("\n")
        tail = f"**Conversation — last {len(messages)} messages (chat {chat_id})**"
        prefix = f"{header}\n\n{tail}"
        conversation = "\n".join(lines)
        body = f"{prefix}\n{conversation}" if conversation else prefix
        if len(body) > 10000:
            # Keep the guidance header and the newest end of the conversation.
            budget = 10000 - len(prefix) - len("\n…\n")
            if budget > 0:
                tail = conversation[-budget:]
                if len(conversation) > budget:
                    # Avoid presenting a cut message-line fragment without the
                    # explicit current state. Prefer the next complete line; for
                    # a single overlong message, label the retained fragment.
                    first_newline = tail.find("\n")
                    if first_newline >= 0:
                        tail = tail[first_newline + 1:]
                    elif f"taskcard: {taskcard}" not in tail:
                        label = f"[taskcard: {taskcard}] …"
                        remaining = max(0, budget - len(label))
                        tail = label + (tail[-remaining:] if remaining else "")
                conversation = "…\n" + tail
                body = f"{prefix}\n{conversation}"
            else:
                body = body[:9997] + "…"
        return body

    def _build_conversation_preview_and_metadata(
        self,
        account_alias: str,
        chat_id: int | None,
        current_compound_id: str,
        max_messages: int = _CONVERSATION_PREVIEW_MESSAGES,
    ) -> tuple[str, dict[str, Any]]:
        """Build markdown preview plus structured Telegram context metadata."""
        messages = self._conversation_messages(account_alias, chat_id, max_messages)
        preview = self._render_conversation_preview(
            messages,
            chat_id=chat_id,
            current_compound_id=current_compound_id,
        )
        now = datetime.now(timezone.utc)
        structured = [
            self._structured_message(m, current_compound_id=current_compound_id, now=now)
            for m in messages
        ]
        latest_incoming = next(
            (
                item
                for item in reversed(structured)
                if item.get("direction") == "incoming"
                and (item.get("id") == current_compound_id or not current_compound_id)
            ),
            None,
        ) or next(
            (item for item in reversed(structured) if item.get("direction") == "incoming"),
            None,
        )
        metadata: dict[str, Any] = {"recent_messages": structured}
        if latest_incoming is not None:
            metadata["latest_incoming"] = latest_incoming
        referenced = self._referenced_messages_for_current(
            account_alias, current_compound_id, structured, now=now,
        )
        if referenced:
            metadata["referenced_messages"] = referenced
        return preview, metadata

    def _build_conversation_preview(
        self,
        account_alias: str,
        chat_id: int | None,
        current_compound_id: str,
        max_messages: int = _CONVERSATION_PREVIEW_MESSAGES,
    ) -> str:
        """Build a markdown conversation preview of recent Telegram messages.

        Scans inbox/ and sent/ dirs for messages matching *chat_id*, sorts by
        date ascending, takes the tail, and formats each line as:

            [NEW|context][direction][relative_time][taskcard: True|False] #compound_id sender_name: text

        If a message has reply_to_message_id the quoted message is shown
        indented beneath it (truncated to 50 chars).
        """
        preview, _metadata = self._build_conversation_preview_and_metadata(
            account_alias,
            chat_id,
            current_compound_id,
            max_messages,
        )
        return preview

    def _read_ids(self, account: str) -> set[str]:
        path = self._account_dir(account) / "read.json"
        if path.is_file():
            try:
                return set(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                return set()
        return set()

    def _mark_read(self, account: str, compound_ids: list[str]) -> None:
        ids = self._read_ids(account)
        ids.update(compound_ids)
        acct_dir = self._account_dir(account)
        acct_dir.mkdir(parents=True, exist_ok=True)
        target = acct_dir / "read.json"
        fd, tmp = tempfile.mkstemp(dir=str(acct_dir), suffix=".tmp")
        try:
            os.write(fd, json.dumps(sorted(ids)).encode())
            os.close(fd)
            os.replace(tmp, str(target))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _notification_message_ids(self, payload: dict) -> set[str] | None:
        """Return Telegram compound IDs referenced by an MCP notification mirror.

        New Telegram events publish ``message_ref`` in LICC preview metadata.
        Older notifications only have the bounded conversation preview body,
        whose lines include ``#account:chat:message`` anchors; parse those as a
        best-effort migration path so stale mirrors can be cleared after read.

        Returns ``None`` when any preview entry lacks an identifiable Telegram
        message ID. In that case clearing the coalesced notification could hide
        another unread event, so the mirror is left for explicit handling.
        """
        data = payload.get("data") if isinstance(payload, dict) else None
        previews = data.get("previews") if isinstance(data, dict) else None
        if not isinstance(previews, list) or not previews:
            return None

        ids: set[str] = set()
        for preview in previews:
            if not isinstance(preview, dict):
                return None

            subject = preview.get("subject")
            if isinstance(subject, str) and "callback_query" in subject:
                # Telegram callback queries reuse the message_id of the inline
                # keyboard message, so a compound ID is not a unique event ID.
                # Keep these mirrors for explicit handling rather than clearing
                # a fresh callback just because an older callback on that
                # message was read.
                return None

            ref = preview.get("message_ref")
            # Accepts numeric compound IDs and the reserved synthetic
            # events-bucket form, so admitted actorless/service/unknown
            # updates can complete the documented read → clear lifecycle.
            if isinstance(ref, str) and _mirror_identity_account(ref) is not None:
                ids.add(ref)
                continue

            # Backward compatibility for notification files produced before
            # Telegram populated the generic LICC ``message_ref`` field.
            body_preview = preview.get("preview")
            matches = (
                [
                    match
                    for match in _COMPOUND_ID_RE.findall(body_preview)
                    if _looks_like_compound_id(match)
                ]
                if isinstance(body_preview, str)
                else []
            )
            if not matches:
                return None
            ids.update(matches)
        return ids

    def _clear_notification_if_handled(self) -> None:
        """Atomically clear only the current fully handled Telegram mirror."""
        from lingtai.kernel.notification_store import UNCONDITIONAL

        read_by_account = tuple(
            (account, frozenset(self._read_ids(account)))
            for account in self._service.list_accounts()
        )

        def _mutator(current_payload: dict):
            notification_ids = self._notification_message_ids(current_payload)
            if notification_ids is None:
                return current_payload, False, ()
            for compound_id in notification_ids:
                # Read-state-only validation: unlike the strict outbound
                # _parse_compound_id, this accepts the reserved synthetic
                # events bucket so reading it can clear its mirror.
                account = _mirror_identity_account(compound_id)
                if account is None:
                    return current_payload, False, ()
                read_ids = next(
                    (ids for alias, ids in read_by_account if alias == account),
                    frozenset(),
                )
                if compound_id not in read_ids:
                    return current_payload, False, ()
            handled_ids = tuple(sorted(notification_ids))
            if not handled_ids:
                return current_payload, False, ()
            return None, True, handled_ids

        try:
            result = self._notification_store.compare_update_channel(
                _NOTIFICATION_CHANNEL, UNCONDITIONAL, _mutator
            )
        except Exception as exc:
            log.debug("failed to update Telegram notification mirror: %s", exc)
            return

        handled_ids = result.value if isinstance(result.value, tuple) else ()
        if result.changed and result.cleared and handled_ids:
            log.info(
                "telegram notification mirror cleared after read: ids=%s",
                list(handled_ids),
            )

    def _load_contacts(self, account: str) -> dict:
        path = self._account_dir(account) / "contacts.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_contacts(self, account: str, contacts: dict) -> None:
        acct_dir = self._account_dir(account)
        acct_dir.mkdir(parents=True, exist_ok=True)
        target = acct_dir / "contacts.json"
        fd, tmp = tempfile.mkstemp(dir=str(acct_dir), suffix=".tmp")
        try:
            os.write(fd, json.dumps(contacts, indent=2).encode())
            os.close(fd)
            os.replace(tmp, str(target))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ------------------------------------------------------------------
    # Rich Feedback Helpers (Issue #8)
    # ------------------------------------------------------------------

    def send_progress_message(
        self,
        account_alias: str,
        chat_id: int,
        text: str = "Working on it...",
        reply_to_message_id: int | None = None,
    ) -> dict | None:
        """Send a progress message that can be edited later.

        Returns one of:

        - ``{"status": "sent", "message_id": <compound>}`` ONLY when the provider
          returned a real, positive, non-boolean integer message id; the compound
          id is formed from that validated id.
        - ``{"status": "indeterminate_send"}`` when the send did not raise but
          returned no usable message id (a malformed result under top-level
          ``ok=true``): a card may be visible but its exact id is unknown, so a
          fake id (e.g. ``:0``) is never formed, adopted, persisted, or deleted.
        - ``None`` when the send raised (no card was sent).

        Best-effort — never blocks or fails the main task.
        """
        try:
            acct = self._service.get_account(account_alias)
            result = acct.send_message(
                chat_id, text,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as e:
            log.debug("Failed to send progress message: %s", e)
            return None
        tg_message_id = self._sent_message_id_or_none(result)
        if tg_message_id is None:
            # Top-level ``ok`` may be true while the result carries no usable id.
            # Never invent a fake id: report an explicit indeterminate send so
            # callers fail closed instead of adopting/persisting an unknown card.
            log.warning(
                "Task card send returned no valid message id; treating as "
                "indeterminate (no id adopted/persisted/deleted)")
            return {"status": "indeterminate_send"}
        compound_id = f"{account_alias}:{chat_id}:{tg_message_id}"
        return {"status": "sent", "message_id": compound_id}

    @staticmethod
    def _sent_message_id_or_none(result: object) -> int | None:
        """Extract a real positive Telegram message id from a send result.

        Returns ``None`` for any malformed shape — missing, non-dict, ``bool``,
        non-``int`` (float/str), zero, or negative — so a fake resident id can
        never be formed at the transport boundary.
        """
        if not isinstance(result, dict):
            return None
        mid = result.get("message_id")
        if isinstance(mid, bool) or not isinstance(mid, int) or mid <= 0:
            return None
        return mid

    @staticmethod
    def _task_card_edit_error_outcome(exc: Exception) -> str:
        """Classify only provider-confirmed edit semantics; unknowns fail closed."""
        detail = str(exc)
        if not detail.startswith(_TELEGRAM_API_ERROR_PREFIX):
            return _TASK_CARD_EDIT_FAILED
        description = " ".join(
            detail[len(_TELEGRAM_API_ERROR_PREFIX):].split()
        ).casefold()
        if description.startswith(_TASK_CARD_EDIT_UNCHANGED):
            return _TASK_CARD_EDIT_OK
        if description in _TASK_CARD_EDIT_IMPOSSIBLE_DESCRIPTIONS:
            return _TASK_CARD_EDIT_IMPOSSIBLE
        return _TASK_CARD_EDIT_FAILED

    @staticmethod
    def _task_card_delete_error_outcome(exc: Exception) -> str:
        """Classify only explicit terminal delete semantics; unknowns fail closed."""
        detail = str(exc)
        if not detail.startswith(_TELEGRAM_API_ERROR_PREFIX):
            return _TASK_CARD_DELETE_FAILED
        description = " ".join(
            detail[len(_TELEGRAM_API_ERROR_PREFIX):].split()
        ).casefold()
        if description in _TASK_CARD_DELETE_MISSING_DESCRIPTIONS:
            return _TASK_CARD_DELETE_MISSING
        if description in _TASK_CARD_DELETE_NONDELETABLE_DESCRIPTIONS:
            return _TASK_CARD_DELETE_NONDELETABLE
        return _TASK_CARD_DELETE_FAILED

    def _try_update_progress_message(
        self,
        compound_id: str,
        text: str,
    ) -> tuple[str, str | None]:
        """Edit once; return its semantic outcome and a safe failure reason."""
        try:
            account, chat_id, tg_msg_id = self._parse_compound_id(compound_id)
            acct = self._service.get_account(account)
            acct.edit_message(chat_id, tg_msg_id, text)
            return _TASK_CARD_EDIT_OK, None
        except Exception as exc:
            outcome = self._task_card_edit_error_outcome(exc)
            if outcome == _TASK_CARD_EDIT_OK:
                log.debug("Task card edit was already current; keeping resident id")
            elif outcome == _TASK_CARD_EDIT_IMPOSSIBLE:
                log.warning("Task card resident is not editable; replacement required")
            else:
                log.warning(
                    "Task card edit failed; resident retained (error_type=%s)",
                    type(exc).__name__,
                )
            reason = (
                _safe_task_card_backend_reason(exc)
                if outcome == _TASK_CARD_EDIT_FAILED
                else None
            )
            return outcome, reason

    def update_progress_message(
        self,
        compound_id: str,
        text: str,
    ) -> bool:
        """Compatibility bool: true for an applied edit or identical-content no-op."""
        outcome, _reason = self._try_update_progress_message(compound_id, text)
        return outcome == _TASK_CARD_EDIT_OK

    # ------------------------------------------------------------------
    # Private Task Card helpers (internally driven by the automatic event-tail
    # broadcaster and the intrinsic-artifact projector, not LLM-exposed)
    # ------------------------------------------------------------------

    # Reasoning cap (Unicode code points) after secret redaction.
    _TASK_CARD_REASONING_CAP = TaskCardEventProjection.REASONING_CAP
    # Overall render ceiling, safely below Telegram's 4096-char message limit.
    _TASK_CARD_TEXT_LIMIT = TaskCardEventProjection.TEXT_LIMIT
    # Header shown at the top of every card.
    _TASK_CARD_HEADER = TaskCardEventProjection.HEADER
    # The two composed channels of the single resident card (Jason #7258/#7259).
    _TASK_CARD_CHANNELS = ("automatic", "programmable")
    _TASK_CARD_DEFAULT_CHANNEL = "automatic"
    # Header for the appended programmable section; keeps the composed message
    # legible when both channels are present. English-only (Jason #7175/#7205).
    _TASK_CARD_PROGRAMMABLE_HEADER = "— WATCH —"
    # Terminal presentation delivered when clearing a programmable-ONLY resident
    # would otherwise compose to empty text. Telegram cannot edit a message to
    # empty text, so a stable, nonempty, English-only marker is shown instead,
    # leaving the one resident message reusable by a later automatic or
    # programmable frame. It is presentation-only: the committed programmable slot
    # is still cleared, so it never persists as stored channel state.
    _TASK_CARD_WATCH_STOPPED = "— WATCH STOPPED —"

    def _channel_key(self, account: str, chat_id: int) -> str:
        return self._resident.key(account, chat_id)

    def _set_channel_frame(
        self, account: str, chat_id: int, channel: str, frame: str | None,
    ) -> None:
        """Commit a channel frame through the resident owner."""
        self._resident.set_frame(account, chat_id, channel, frame)

    def _compose_channels(
        self, account: str, chat_id: int,
        *, channel: str | None = None, frame: str | None = None,
    ) -> str:
        """Compose a proposed frame through the resident owner."""
        return self._resident.compose(
            account, chat_id, channel=channel, frame=frame,
        )

    def _task_card_delivery_lock(self, account: str, chat_id: int) -> threading.RLock:
        """Return the resident owner's stable route lock."""
        return self._resident.delivery_lock(account, chat_id)

    def _deliver_channel_frame(
        self, account: str, chat_id: int, channel: str, frame: str | None,
        *, error: str, resident_id: str | None = None,
        empty_fallback: str | None = None,
    ) -> dict:
        """Project via the single resident owner."""
        return self._resident.project(
            account, chat_id, channel, frame, error=error,
            resident_id=resident_id, empty_fallback=empty_fallback,
        )

    def _deliver_channel_frame_locked(
        self, account: str, chat_id: int, channel: str, frame: str | None,
        *, error: str, resident_id: str | None = None,
        empty_fallback: str | None = None,
    ) -> dict:
        """Compatibility wrapper around the shared serialized state machine."""
        return self._resident.deliver_locked(
            account,
            chat_id,
            channel,
            frame,
            error=error,
            resident_id=resident_id,
            empty_fallback=empty_fallback,
        )

    @classmethod
    def _format_programmable_card_text(
        cls, card: dict, *, now: datetime | None = None,
    ) -> str:
        """Render retained legacy programmable-card JSON for compatibility tests.

        The retired Telegram-owned controller supplied this validated schema
        object. The current public intrinsic instead emits a full text/Markdown
        body through the agent-local file artifact, and Telegram's read-only file
        projector does not use this JSON formatter. When retained compatibility
        code invokes it, secret redaction still runs on every free-text field
        before the render ceiling is applied. All copy is English-only
        (Jason #7175/#7205).

        A non-empty frame always ends with its own ``Last Updated: ...`` line —
        the instant this programmable frame was accepted/rendered for delivery.
        This is independent of the automatic channel's own ``Last Updated`` line;
        neither channel's timestamp is derived from or advances the other's.
        ``now`` is the render instant (injectable for deterministic tests).
        """
        parts: list[str] = []
        title = str(card.get("title", "")).strip()
        if title:
            parts.append(
                TaskCardEventProjection.sanitize_public_text(title)[
                    :cls._TASK_CARD_REASONING_CAP
                ]
            )
        for line in card.get("lines", []) or []:
            if not isinstance(line, str):
                continue
            rendered = TaskCardEventProjection.sanitize_public_text(line)[
                :cls._TASK_CARD_REASONING_CAP
            ]
            parts.append(f"• {rendered}")
        footer = str(card.get("footer", "")).strip()
        if footer:
            parts.append(
                TaskCardEventProjection.sanitize_public_text(footer)[
                    :cls._TASK_CARD_REASONING_CAP
                ]
            )
        if not parts:
            return ""
        parts.append(f"{_TASK_CARD_TIME_PREFIX}{cls._task_card_render_time(now)}")
        text = "\n".join(parts)
        if len(text) > cls._TASK_CARD_TEXT_LIMIT:
            text = text[:cls._TASK_CARD_TEXT_LIMIT]
        return text

    # ------------------------------------------------------------------
    # Automatic Task Card event tail (agent-behavior broadcast)
    # ------------------------------------------------------------------
    #
    # The automatic slot tails ``logs/events.jsonl`` and broadcasts one bounded
    # agent-behavior view. Only public ``diary`` text and validated ``tool_call``
    # name, redacted/capped ``_reasoning``, and timestamp are projected; raw
    # action/arguments/results are excluded. Rows group by provider ``api_call_id``.
    # Latest final-carrier session telemetry is projected separately. There is no
    # durable cursor: startup and log replacement rehydrate from the bounded tail.
    _TASK_CARD_EVENT_WINDOW = TaskCardEventProjection.EVENT_WINDOW
    _TASK_CARD_EVENT_POLL_INTERVAL = 1.0
    _TASK_CARD_EVENT_TAIL_CHUNK = 65536
    _TASK_CARD_EVENT_REASONING_CAP = TaskCardEventProjection.EVENT_REASONING_CAP
    _TASK_CARD_EVENT_TEXT_CAP = TaskCardEventProjection.EVENT_TEXT_CAP
    _TASK_CARD_MAX_EVENTS_PER_CALL = TaskCardEventProjection.MAX_EVENTS_PER_CALL
    # The same quiet horizontal rule used by the TUI between provider calls.
    _TASK_CARD_API_CALL_DIVIDER = TaskCardEventProjection.API_CALL_DIVIDER

    def _task_card_events_path(self) -> Path:
        return self._working_dir / "logs" / "events.jsonl"

    def _event_tail_offset(self) -> int:
        with self._task_card_event_lock:
            return self._task_card_event_offset

    def _task_card_event_window(self) -> list[dict]:
        with self._task_card_event_lock:
            return self._flatten_task_card_groups(self._task_card_event_groups)

    def _task_card_event_groups_snapshot(self) -> list[dict]:
        """Return bounded provider-call groups for Task Card rendering."""
        with self._task_card_event_lock:
            return [
                {"api_call_id": group.get("api_call_id"),
                 "events": [dict(event) for event in group.get("events", [])]}
                for group in self._task_card_event_groups
            ]

    def _task_card_event_metadata_snapshot(self) -> dict | None:
        with self._task_card_event_lock:
            metadata = self._task_card_event_metadata
            snapshot = dict(metadata) if isinstance(metadata, dict) else {}
        lifecycle = self._task_card_agent_lifecycle_status()
        if lifecycle is not None:
            snapshot["agent_lifecycle"] = lifecycle
        return snapshot or None

    def _task_card_agent_lifecycle_status(self) -> str | None:
        """Read this agent's own canonical lifecycle for the footer.

        Sources ``.status.json``'s ``runtime.state`` — the same
        ``AgentState`` value ``BaseAgent._write_status_snapshot`` writes on
        every turn — and, only for the non-terminal ``active``/``idle``/
        ``asleep`` states, falls back to the canonical presence liveness
        policy (``kernel.agent_presence.observe_alive`` via
        ``PosixAgentPresenceStoreAdapter``) to catch a process that died
        without ever writing ``stuck``. ``stuck`` and ``suspended`` are
        trusted as-is: both are the process explicitly reporting its own
        state, and a stale heartbeat proves nothing more in either case.
        Missing, malformed, non-UTF-8, or non-object status data degrades to
        ``None`` — no line is rendered rather than fabricating a state.
        """
        try:
            raw = (self._working_dir / ".status.json").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        runtime = data.get("runtime")
        if not isinstance(runtime, dict):
            return None
        state = runtime.get("state")
        if state not in _TASK_CARD_AGENT_STATES:
            return None
        if state in (AgentState.SUSPENDED.value, AgentState.STUCK.value):
            return state
        try:
            alive = observe_alive(
                PosixAgentPresenceStoreAdapter(self._working_dir), time.time(),
            )
        except Exception:
            return state
        return state if alive else "offline"

    @staticmethod
    def _project_agent_text_event(event: dict) -> dict | None:
        return TaskCardEventProjection.project_agent_text_event(
            event,
            text_cap=TelegramManager._TASK_CARD_EVENT_TEXT_CAP,
        )

    @staticmethod
    def _project_task_card_event(event: dict) -> dict | None:
        return TaskCardEventProjection.project_event(
            event,
            text_cap=TelegramManager._TASK_CARD_EVENT_TEXT_CAP,
            reasoning_cap=TelegramManager._TASK_CARD_EVENT_REASONING_CAP,
        )

    @staticmethod
    def _event_group_id(event: dict, fallback: int) -> str:
        return TaskCardEventProjection.event_group_id(event, fallback)

    def _group_task_card_events(self, projected: list[tuple[dict, dict]]) -> list[dict]:
        return TaskCardEventProjection.group_events(
            projected,
            window=self._TASK_CARD_EVENT_WINDOW,
            max_events_per_call=self._TASK_CARD_MAX_EVENTS_PER_CALL,
        )

    @staticmethod
    def _flatten_task_card_groups(
        groups: list[dict], *, include_group_id: bool = False,
    ) -> list[dict]:
        return TaskCardEventProjection.flatten_groups(
            groups, include_group_id=include_group_id,
        )

    @staticmethod
    def _project_tool_call_row(event: dict) -> dict | None:
        return TaskCardEventProjection.project_tool_call_row(
            event,
            reasoning_cap=TelegramManager._TASK_CARD_EVENT_REASONING_CAP,
        )

    @staticmethod
    def _project_final_carrier_metadata(event: dict) -> dict | None:
        return TaskCardEventProjection.project_final_carrier_metadata(event)

    @staticmethod
    def _format_task_card_row_timestamp(ts: object) -> str:
        return TaskCardEventProjection.format_row_timestamp(ts)

    @staticmethod
    def _event_file_identity(stat_result: os.stat_result) -> tuple[str, float | int] | None:
        """Return a best-effort "same file?" token, or ``None`` when unknown.

        POSIX: ``st_ino`` is authoritative and never ``0`` for a real file.

        Windows: ``st_ino`` is usually the real NTFS file index, but CPython
        falls back to an attribute-only stat (yielding ``st_ino == 0``) whenever
        it cannot open a handle to the file — a writer holding it without full
        share rights, an AV scanner, some network/virtualised filesystems. A
        ``0`` therefore means *unknown*, and must not be read as *replaced*.

        ``st_mtime`` is the wrong fallback: appending to the log is precisely
        what moves mtime, so comparing it classifies every single append as a
        file replacement and forces a full rehydrate + rebroadcast on every
        poll. Creation time (``st_birthtime`` where the platform exposes it,
        otherwise Windows' ``st_ctime``, which *is* the creation time there)
        changes when the log is recreated and stays put on append, so it is the
        correct stand-in. POSIX ``st_ctime`` is inode-change time — it moves on
        append — so it is deliberately never used here.
        """
        inode = getattr(stat_result, "st_ino", None)
        if isinstance(inode, int) and not isinstance(inode, bool) and inode:
            return ("ino", inode)
        birthtime = getattr(stat_result, "st_birthtime", None)
        if type(birthtime) in (int, float):
            return ("btime", birthtime)
        if os.name == "nt":
            ctime = getattr(stat_result, "st_ctime", None)
            if type(ctime) in (int, float):
                return ("btime", ctime)
        return None

    def _init_event_tail(self) -> None:
        """Rehydrate the latest-N window and forward offset from the file tail.

        No durable checkpoint is read or written — every restart (including
        refresh/molt) re-derives state purely from ``logs/events.jsonl`` itself,
        by reverse-tailing in bounded chunks until enough complete matching
        rows are found or the file start is reached.
        """
        # Resident ids remain in the existing account state map; this hook lets
        # the Telegram-owned boundary rebuild its in-memory channel view before
        # the event projection is rehydrated.
        self._resident.rehydrate()
        path = self._task_card_events_path()
        try:
            stat = path.stat()
        except OSError:
            with self._task_card_event_lock:
                self._task_card_event_path = path
                self._task_card_event_offset = 0
                self._task_card_event_size = 0
                self._task_card_event_inode = None
                self._task_card_event_identity = None
                self._task_card_event_groups = []
                self._task_card_event_metadata = None
            return

        result = self._reverse_tail_latest_rows(path, stat.st_size)
        if result is None:
            # A read/stat failure mid-scan proves nothing was actually
            # consumed. Fail closed at offset 0 rather than advancing to EOF
            # as if history had been rehydrated — the next poll's stat-based
            # truncation check would otherwise never retry this file.
            with self._task_card_event_lock:
                self._task_card_event_path = path
                self._task_card_event_offset = 0
                self._task_card_event_size = 0
                self._task_card_event_inode = None
                self._task_card_event_identity = None
                self._task_card_event_groups = []
                self._task_card_event_metadata = None
            return
        rows, offset, metadata = result
        with self._task_card_event_lock:
            self._task_card_event_path = path
            self._task_card_event_offset = offset
            self._task_card_event_size = stat.st_size
            self._task_card_event_inode = getattr(stat, "st_ino", None)
            self._task_card_event_identity = self._event_file_identity(stat)
            projected = [({"api_call_id": row.get("group_id")}, dict(row)) for row in rows]
            self._task_card_event_groups = self._group_task_card_events(projected)
            self._task_card_event_metadata = metadata

    def _reverse_tail_latest_rows(
        self, path: Path, size: int,
    ) -> tuple[list[dict], int, dict | None] | None:
        """Reverse-scan bounded chunks from EOF to collect the latest-N matches.

        Reads growing chunks backward from the end of the file until either
        ``_TASK_CARD_EVENT_WINDOW`` matching rows are found or the file start is
        reached — never a full read of a large (e.g. multi-hundred-MB) log.
        The tail chunk may start mid-line; the leading partial fragment is
        discarded (its predecessor chunk will complete it on the next round).

        Returns ``(rows, offset, metadata)`` where ``offset`` is the forward
        byte offset the poller should resume from and ``metadata`` is the latest
        final-carrier session projection (or ``None`` when no carrier exists)
        — ``size`` unless the file's final line
        has no trailing newline yet (writer mid-append), in which case it is
        the start of that incomplete tail so the poller re-reads it whole once
        it is completed, instead of treating it as an already-consumed row.
        Returns ``None`` (fail closed) on any read/stat error, so the caller
        never advances the offset past bytes that were never actually read.
        """
        window = self._TASK_CARD_EVENT_WINDOW
        projected_events: list[tuple[dict, dict]] = []
        latest_metadata: dict | None = None
        tail_offset = size
        try:
            with open(path, "rb") as f:
                end = size
                chunk_size = self._TASK_CARD_EVENT_TAIL_CHUNK
                carry = b""
                first_chunk = True
                # Reverse order means the first recognized carrier in the
                # bounded tail is the latest one available to this rehydrate.
                # Keep the existing latest-row bound; a log without a nearby
                # carrier must not turn startup into an unbounded full scan.
                while end > 0 and len({self._event_group_id(event, i) for i, (event, _row) in enumerate(projected_events)}) < window:
                    start = max(0, end - chunk_size)
                    f.seek(start)
                    data = f.read(end - start)
                    end = start
                    buf = data + carry
                    if first_chunk:
                        # The file's very last line may have no trailing
                        # newline yet (writer mid-append). Exclude that
                        # unterminated tail from both matches and the
                        # resulting offset so it is re-read whole later.
                        last_newline = buf.rfind(b"\n")
                        if last_newline == -1:
                            tail_offset = start
                            buf = b""
                        else:
                            tail_offset = start + last_newline + 1
                            buf = buf[: last_newline + 1]
                        first_chunk = False
                    lines = buf.split(b"\n")
                    # The first fragment may be a partial continuation of an
                    # earlier (still unread) chunk; keep it as carry unless we
                    # are already at the start of the file.
                    carry = lines[0] if start > 0 else b""
                    complete = lines[1:] if start > 0 else lines
                    round_projected: list[tuple[dict, dict]] = []
                    round_metadata: dict | None = None
                    for raw in complete:
                        event = self._decode_event_line(raw)
                        if event is None:
                            continue
                        row = self._project_task_card_event(event)
                        if row is not None:
                            round_projected.append((event, row))
                        candidate = self._project_final_carrier_metadata(event)
                        if candidate is not None:
                            # ``complete`` is oldest-to-newest within this
                            # chunk; the last candidate is the newest here.
                            round_metadata = candidate
                    if latest_metadata is None and round_metadata is not None:
                        latest_metadata = round_metadata
                    projected_events = round_projected + projected_events
                    chunk_size *= 2
        except OSError:
            return None
        # Chunks were prepended above, so projected events are already in
        # journal order before grouping; one API call receives one divider.
        groups = self._group_task_card_events(projected_events)
        return self._flatten_task_card_groups(
            groups, include_group_id=True,
        ), tail_offset, latest_metadata

    @staticmethod
    def _decode_event_line(raw: bytes) -> dict | None:
        return TaskCardEventProjection.decode_event_line(raw)

    @staticmethod
    def _decode_and_project_line(raw: bytes) -> dict | None:
        event = TelegramManager._decode_event_line(raw)
        return TelegramManager._project_tool_call_row(event) if event is not None else None

    def _poll_event_tail(self) -> None:
        """Read any newly appended complete lines and broadcast on change.

        Detects truncation/replacement (current size smaller than the tracked
        offset, or a changed inode) and reinitializes from the new tail rather
        than seeking into now-invalid byte positions.
        """
        with self._task_card_event_lock:
            path = self._task_card_event_path
        if path is None:
            self._init_event_tail()
            with self._task_card_event_lock:
                rehydrated_rows = bool(self._task_card_event_groups)
                rehydrated_metadata = self._task_card_event_metadata is not None
            if rehydrated_rows or rehydrated_metadata:
                self._broadcast_task_card_event_window()
            return

        try:
            stat = path.stat()
        except OSError:
            return

        with self._task_card_event_lock:
            offset = self._task_card_event_offset
            tracked_identity = self._task_card_event_identity

        current_inode = getattr(stat, "st_ino", None)
        current_identity = self._event_file_identity(stat)
        # Only a *proven* identity change counts as replacement. Two tokens of
        # different kinds are not comparable (a stat that degraded from inode to
        # creation time says nothing about the file), and an unknown token on
        # either side leaves the size check as the sole signal. Fail closed
        # toward "same file" so a plain append is never read as a rewrite.
        replaced = (
            tracked_identity is not None
            and current_identity is not None
            and tracked_identity[0] == current_identity[0]
            and tracked_identity != current_identity
        )
        truncated = stat.st_size < offset or replaced
        if truncated:
            # Truncation/replacement is itself the signal of change: the file
            # content the resident cards were showing no longer exists, so the
            # rehydrated window — even an empty one — must still be broadcast
            # rather than leaving a stale non-empty render displayed.
            self._init_event_tail()
            changed = True
        elif stat.st_size > offset:
            changed = self._append_new_lines(path, offset, stat.st_size)
            with self._task_card_event_lock:
                self._task_card_event_inode = current_inode
                # Refresh the token whenever this stat produced one, so a single
                # degraded stat (Windows handing back ``st_ino == 0``) cannot
                # strand an old token and fabricate a replacement later.
                if current_identity is not None:
                    self._task_card_event_identity = current_identity
        else:
            changed = False

        if changed:
            self._broadcast_task_card_event_window()

    def _append_new_lines(self, path: Path, offset: int, size: int) -> bool:
        """Seek to ``offset`` and consume only complete new lines.

        A trailing partial line (the writer mid-append) is left unconsumed —
        the offset only advances past bytes that formed a complete line, so
        the same partial bytes are safely re-read (and, once completed,
        consumed) on the next poll.
        """
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(size - offset)
        except OSError:
            return False
        if not data:
            return False

        last_newline = data.rfind(b"\n")
        if last_newline == -1:
            return False  # no complete line yet
        complete, _partial = data[:last_newline + 1], data[last_newline + 1:]
        new_offset = offset + len(complete)

        projected_events: list[tuple[dict, dict]] = []
        latest_metadata: dict | None = None
        tool_results: dict[str, dict] = {}
        for raw in complete.split(b"\n"):
            event = self._decode_event_line(raw)
            if event is None:
                continue
            call_id = event.get("tool_call_id")
            if event.get("type") == "tool_result" and isinstance(call_id, str) and call_id:
                tool_results[call_id] = event
            row = self._project_task_card_event(event)
            if row is not None:
                projected_events.append((event, row))
            candidate = self._project_final_carrier_metadata(event)
            if candidate is not None:
                # Forward append order is oldest-to-newest, so the last
                # candidate is the only current snapshot.
                latest_metadata = candidate

        with self._task_card_event_lock:
            metadata_changed = (
                latest_metadata is not None
                and latest_metadata != self._task_card_event_metadata
            )
            if latest_metadata is not None:
                self._task_card_event_metadata = latest_metadata
            self._task_card_event_offset = new_offset
            self._task_card_event_size = size
            if projected_events:
                existing = self._task_card_event_groups
                combined: list[tuple[dict, dict]] = []
                for group in existing:
                    group_id = group.get("api_call_id")
                    for event_row in group.get("events", []):
                        combined.append(({"api_call_id": group_id}, event_row))
                combined.extend(projected_events)
                self._task_card_event_groups = self._group_task_card_events(combined)
            result_changed = TaskCardEventProjection.apply_tool_results(
                self._task_card_event_groups,
                tool_results,
            )
        return bool(projected_events) or metadata_changed or result_changed

    def _resident_task_card_targets(self) -> list[tuple[str, int]]:
        """Enumerate every ``(account, chat_id)`` with a resident Task Card.

        Reads only the existing persisted account/manager state (each
        account's durable ``task_cards`` map); no new durable index is
        introduced. Cross-chat visibility is deliberately not filtered by
        route — this is a broadcast of agent behavior, not per-chat routing.
        """
        targets: list[tuple[str, int]] = []
        for alias in self._service.list_accounts():
            try:
                acct = self._service.get_account(alias)
                lister = getattr(acct, "list_task_card_chats", None)
                if not callable(lister):
                    continue
                for chat_id in lister():
                    targets.append((alias, chat_id))
            except Exception as e:
                log.debug("Failed to enumerate task card chats for %s: %s", alias, e)
        return targets

    def _taskcard_status_path(self) -> Path:
        return self._working_dir / "taskcard" / "status"

    def _taskcard_body_path(self) -> Path:
        return self._working_dir / "taskcard" / "taskcard.md"

    def _read_taskcard_status(self) -> str | None:
        try:
            return self._taskcard_status_path().read_text(encoding="utf-8")
        except OSError:
            return None

    def _read_programmable_task_card_body(self) -> str | None:
        """Read the intrinsic Task Card body (caller has already gated status)."""
        try:
            body = self._taskcard_body_path().read_text(encoding="utf-8")
        except OSError:
            return None
        body = TaskCardEventProjection.sanitize_public_text(body)
        if not body.strip():
            return None
        if len(body) > self._TASK_CARD_TEXT_LIMIT:
            body = body[: self._TASK_CARD_TEXT_LIMIT]
        return body

    def _broadcast_programmable_task_card_file(self) -> None:
        """Project the current intrinsic Task Card intent onto every resident.

        Telegram owns the resident message, its automatic/mechanical content,
        and delivery; the agent owns only the programmable frame plus its
        ``status`` intent. Exact ``active`` with a nonempty body composes/
        updates the programmable frame. Exact ``inactive`` excludes only the
        programmable frame from composition and updates the same resident with
        its Telegram-owned automatic content intact -- it never deletes/hides
        the resident message, never deletes the local body, and never pauses
        automatic updates. Missing, unreadable, or any other status/body state
        is unchanged: a no-op that preserves whatever programmable frame is
        already committed.
        """
        status = self._read_taskcard_status()
        if status == "inactive":
            self._clear_programmable_task_card_frame()
            return
        if status != "active":
            return
        body = self._read_programmable_task_card_body()
        if body is None:
            return
        for account, chat_id in self._resident_task_card_targets():
            try:
                current = self._task_card_channels.get(f"{account}:{chat_id}", {}).get(
                    "programmable"
                )
                if current == body:
                    continue
                self._deliver_channel_frame(
                    account,
                    chat_id,
                    "programmable",
                    body,
                    error="Failed to broadcast programmable task card",
                )
            except Exception as e:
                log.debug(
                    "Programmable task card broadcast failed for %s:%s: %s",
                    account,
                    chat_id,
                    e,
                )

    def _clear_programmable_task_card_frame(self) -> None:
        """Exclude the programmable frame from every resident, idempotently.

        Only the programmable slot is cleared; the automatic/mechanical
        content Telegram owns is untouched and still gets recomposed into the
        same resident message (never deleted/hidden). A resident with no
        committed programmable frame is already the target state, so repeated
        ``inactive`` handling delivers nothing further.
        """
        for account, chat_id in self._resident_task_card_targets():
            try:
                current = self._task_card_channels.get(f"{account}:{chat_id}", {}).get(
                    "programmable"
                )
                if current is None:
                    continue
                self._deliver_channel_frame(
                    account,
                    chat_id,
                    "programmable",
                    None,
                    error="Failed to clear programmable task card",
                    empty_fallback=self._TASK_CARD_WATCH_STOPPED,
                )
            except Exception as e:
                log.debug(
                    "Programmable task card clear failed for %s:%s: %s",
                    account,
                    chat_id,
                    e,
                )

    def _broadcast_task_card_event_window(self) -> None:
        """Project the current bounded window to every resident Task Card.

        Update-first per target (same discipline as ``_task_card_create``):
        edits the tracked resident in place, sending/deleting only as
        fail-open recovery. A delivery failure for one target never blocks
        another target's broadcast.
        """
        if not self._taskcard_enabled():
            return
        normal_rows = self._taskcard_normal_rows()
        automatic = TaskCardEventProjection.render_event_groups(
            self._task_card_event_groups_snapshot(),
            metadata=self._task_card_event_metadata_snapshot(),
            normal_rows=normal_rows,
        )
        for account, chat_id in self._resident_task_card_targets():
            try:
                self._deliver_channel_frame(
                    account, chat_id, "automatic", automatic,
                    error="Failed to broadcast task card",
                )
            except Exception as e:
                log.debug(
                    "Automatic task card broadcast failed for %s:%s: %s",
                    account, chat_id, e,
                )

    def _start_task_card_tail(self) -> None:
        """Start the one manager-owned tail worker, idempotently.

        Joined with the Telegram MCP manager lifecycle: ``start()``/``stop()``
        are the only callers, so exactly one worker runs per manager instance
        regardless of how many times ``start()`` is called.
        """
        if self._task_card_tail_thread is not None and self._task_card_tail_thread.is_alive():
            return
        self._init_event_tail()
        self._task_card_tail_stop.clear()

        def _loop() -> None:
            while not self._task_card_tail_stop.is_set():
                try:
                    self._poll_event_tail()
                except Exception as e:
                    log.debug("Automatic task card event tail poll failed: %s", e)
                if self._task_card_tail_stop.wait(self._TASK_CARD_EVENT_POLL_INTERVAL):
                    return

        thread = threading.Thread(
            target=_loop, name="telegram-task-card-event-tail", daemon=True,
        )
        self._task_card_tail_thread = thread
        thread.start()

    def _stop_task_card_tail(self) -> None:
        self._task_card_tail_stop.set()
        thread = self._task_card_tail_thread
        if thread is not None:
            thread.join(timeout=5.0)
        self._task_card_tail_thread = None

    def _start_programmable_task_card_poller(self) -> None:
        if (
            self._programmable_task_card_thread is not None
            and self._programmable_task_card_thread.is_alive()
        ):
            return
        self._programmable_task_card_stop.clear()

        def _loop() -> None:
            while not self._programmable_task_card_stop.is_set():
                try:
                    self._broadcast_programmable_task_card_file()
                except Exception as e:
                    log.debug("Programmable task card poll failed: %s", e)
                if self._programmable_task_card_stop.wait(1.0):
                    return

        thread = threading.Thread(
            target=_loop,
            name="telegram-task-card-programmable-poller",
            daemon=True,
        )
        self._programmable_task_card_thread = thread
        thread.start()

    def _stop_programmable_task_card_poller(self) -> None:
        self._programmable_task_card_stop.set()
        thread = self._programmable_task_card_thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._programmable_task_card_thread = None

    def _handle_task_card_update(self, args: dict) -> dict:
        """Private internal action — internally-driven Task Card projection
        (the automatic event tail and the intrinsic-artifact projector).

        Sub-actions:
          - create:  Project the resident 📋 TASK CARD for the current batch —
                     update-first, editing the persisted resident in place (same
                     id) and sending/deleting only as fail-open recovery.
          - update:  Edit the same card to show the current batch.
          - finalize: Freeze the card on its concrete last batch (legacy scalar
                     form marks ✅ TASK CARD · DONE).

        One tracked resident target per account+chat, composed from the
        "automatic" and "programmable" channels (Jason #7258/#7259); unknown
        historical orphan cards are not enumerated or deleted. Not in SCHEMA —
        LLM cannot call.
        """
        sub_action = args.get("sub_action", "update")
        channel = args.get("channel", self._TASK_CARD_DEFAULT_CHANNEL)
        if channel not in self._TASK_CARD_CHANNELS:
            return {"status": "error", "error": f"Unknown channel: {channel}"}
        if sub_action not in {"create", "update", "finalize"}:
            return {"status": "error", "error": f"Unknown sub_action: {sub_action}"}
        self._resident.set_enabled(self._raw_taskcard_enabled())
        try:
            if channel == "programmable":
                return self._task_card_programmable(sub_action, args)
            if sub_action == "create":
                return self._task_card_create(args)
            elif sub_action == "update":
                return self._task_card_update(args)
            elif sub_action == "finalize":
                return self._task_card_finalize(args)
            else:
                return {"status": "error", "error": f"Unknown sub_action: {sub_action}"}
        except Exception as e:
            log.debug("Task card update failed: %s", e)
            return {"status": "error", "error": str(e)}

    def _ensure_task_card_resident(self, account: str, chat_id: int) -> dict:
        """Ensure the resident target for an established inbound chat."""
        automatic = self._format_task_card_text(
            "", "", "", rows=[], metadata=None,
            normal_rows=self._taskcard_normal_rows(),
        )
        return self._resident.ensure(
            account, chat_id, automatic, error="Failed to ensure task card resident",
        )

    def _task_card_create(self, args: dict) -> dict:
        """Project the resident Task Card for (account, chat), singleton per chat.

        Update-first (Jason #6894/#6899): the automatic BaseAgent task-card
        context is turn/request-local, so every new tool batch/turn re-issues
        ``create``.  This must NOT re-send and delete a card each time — that is
        the flicker.  When a valid persisted resident already exists, edit it in
        place through Telegram and return the SAME compound id, sending nothing
        new and deleting nothing.

        Replacement is fail-loud and old-first: if there is no persisted resident
        (first card of the chat), send and persist the first card normally. Otherwise
        the last committed render is used for a same-content existence probe when
        available; after a cold in-memory start, exact delete is the probe. Before any
        replacement send, the old id must be confirmed deleted or explicitly missing.
        Unknown probe or delete failure returns an error without sending. A send
        failure after a confirmed old delete may leave zero cards and reports that
        state explicitly.
        Persistence failure retains the new in-process id and surfaces a partial
        durability failure. Unknown historical orphan cards are never guessed at or
        deleted.
        """
        account = args["account"]
        chat_id = args["chat_id"]
        automatic = self._format_task_card_text(
            args.get("tool", ""), args.get("tool_action", ""), args.get("reasoning", ""),
            rows=args.get("rows"), metadata=args.get("metadata"),
            normal_rows=self._taskcard_normal_rows())
        # Compose with the proposed automatic frame + the live programmable slot,
        # deliver, and commit the automatic frame only once the edit/send/replace
        # succeeds (a failed edit must not poison the stored channel state).
        return self._deliver_channel_frame(
            account, chat_id, "automatic", automatic, error="Failed to send task card")

    def _recover_task_card_by_replacement(
        self, account: str, chat_id: int, stale_id: str, text: str, *, error: str,
    ) -> dict:
        """Replace a provider-confirmed edit-impossible resident, old-first.

        The failed edit is the exact-id existence probe.  Before injecting a new
        card, confirm that deleting the tracked old resident succeeded or that
        Telegram explicitly reports it already missing.  Unknown delete failure
        aborts the send.  A replacement-send failure after a confirmed old delete
        may therefore leave zero cards, which is reported explicitly rather than
        manufacturing a duplicate resident.
        """
        return self._replace_task_card_after_probe(
            account, chat_id, stale_id, text, error=error
        )

    def _replace_task_card_after_probe(
        self, account: str, chat_id: int, stale_id: str, text: str, *, error: str,
    ) -> dict:
        """Compatibility wrapper around shared old-first replacement."""
        return self._resident.replace_after_probe(
            self._resident.route(account, chat_id),
            stale_id,
            text,
            error=error,
        )

    def _get_last_message_id(self, account: str, chat_id: int) -> int | None:
        """Read the chat's latest observed message id from the account; fail-open.

        Returns ``None`` when the owning layer does not know the latest message
        id (unknown after a refresh, or a narrow test/third-party account double
        without the accessor). ``None`` is deliberately conservative — it is not
        evidence that the resident card is or is not the last message.
        """
        try:
            getter = getattr(
                self._service.get_account(account), "get_last_message_id", None)
            if not callable(getter):
                return None
            value = getter(chat_id)
        except Exception as e:
            log.debug("Failed to read latest chat message id: %s", e)
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _resident_superseded(
        self, account: str, chat_id: int, resident_id: str,
    ) -> bool:
        """True only when a newer chat message is *known* to sit below the card.

        Fail-closed on every uncertainty: a malformed resident id (unparseable),
        or an unknown latest-message id, both return ``False`` so the caller edits
        in place and never deletes. Deletion authorization requires a deterministic
        ``latest > resident`` — nothing weaker.
        """
        try:
            card_account, card_chat_id, card_tg_id = self._parse_compound_id(
                resident_id)
        except Exception:
            return False
        if card_account != account or card_chat_id != chat_id:
            return False
        latest = self._get_last_message_id(account, chat_id)
        if latest is None:
            return False
        return latest > card_tg_id

    def _resident_id_matches_route(
        self,
        route: TaskCardRoute,
        resident_id: str,
    ) -> bool:
        """Validate one provider id against its exact Telegram resident route."""
        if route.thread_id is not None:
            return False
        try:
            account, chat_id, _ = self._parse_compound_id(resident_id)
        except Exception:
            return False
        return account == route.account and chat_id == route.chat_id

    def _rotate_task_card_to_latest(
        self, account: str, chat_id: int, stale_id: str, text: str, *, error: str,
    ) -> dict:
        """Compatibility wrapper around shared conservative rotation."""
        return self._resident.rotate_to_latest(
            self._resident.route(account, chat_id),
            stale_id,
            text,
            error=error,
        )

    def _get_resident_task_card(self, account: str, chat_id: int) -> str | None:
        """Read the live resident card id for (account, chat); fail-open.

        One host commonly runs several Telegram MCP server processes against the
        same bot and route (one per agent/daemon runtime). Each holds its own
        ``TelegramAccount`` whose ``task_cards`` map was loaded once, at
        construction: when a peer process rotates or replaces the resident card
        it persists the new id to ``state.json``, and every other process keeps
        believing in the id it remembers. Editing that remembered id then fails
        with "message to edit not found", which the delivery path reads as
        "resident is gone → send a replacement", and each process injects its
        own card. That is the duplicate-Task-Card bug.

        So the durable file is consulted alongside the in-memory value and the
        newer of the two wins. Telegram message ids increase monotonically per
        chat, so "newer" is well defined and can never walk the resident
        backwards onto a card a peer has already deleted. Every failure mode
        (no state path, unreadable/malformed file, a test double without one)
        degrades to the in-memory value rather than raising.
        """
        try:
            acct = self._service.get_account(account)
        except Exception as e:
            log.debug("Failed to read resident task card: %s", e)
            return None
        try:
            tracked = acct.get_task_card(chat_id)
        except Exception as e:
            log.debug("Failed to read resident task card: %s", e)
            tracked = None
        durable = self._durable_resident_task_card(acct, chat_id)
        return self._newer_resident_id(account, chat_id, tracked, durable)

    @staticmethod
    def _durable_resident_task_card(account_obj: object, chat_id: int) -> str | None:
        """Read one chat's resident id straight from the account's state file.

        Strictly read-only and best-effort. Every writer replaces that file
        atomically (``tempfile`` + ``os.replace``), so a torn read is not
        possible; anything else — no state path (test doubles), missing file,
        malformed JSON, wrong types — simply yields ``None`` so the caller falls
        back to its in-memory view.
        """
        path_getter = getattr(account_obj, "_state_path", None)
        if not callable(path_getter):
            return None
        try:
            path = path_getter()
            if path is None:
                return None
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as e:
            log.debug("Failed to read durable resident task card: %s", e)
            return None
        cards = data.get("task_cards") if isinstance(data, dict) else None
        if not isinstance(cards, dict):
            return None
        value = cards.get(str(chat_id))
        return value if isinstance(value, str) and value else None

    def _newer_resident_id(
        self, account: str, chat_id: int, tracked: str | None, durable: str | None,
    ) -> str | None:
        """Pick the higher-numbered of two resident ids for this exact route.

        Only ids that parse and are bound to this precise account+chat are
        comparable. When neither qualifies the in-memory value is returned
        unchanged, so a corrupt/cross-bound id still reaches the delivery
        path's own authorization check instead of silently becoming ``None``
        (which would authorize an untracked send).
        """
        best: str | None = None
        best_tg = -1
        for candidate in (tracked, durable):
            if not isinstance(candidate, str) or not candidate:
                continue
            try:
                cand_account, cand_chat_id, cand_tg_id = self._parse_compound_id(
                    candidate)
            except Exception:
                continue
            if cand_account != account or cand_chat_id != chat_id:
                continue
            if cand_tg_id > best_tg:
                best, best_tg = candidate, cand_tg_id
        if best is None:
            return tracked if tracked else durable
        return best

    def _set_resident_task_card(
        self, account: str, chat_id: int, compound_id: str,
    ) -> bool:
        """Persist the newly sent resident id and acknowledge durable success."""
        try:
            self._service.get_account(account).set_task_card(chat_id, compound_id)
            return True
        except Exception as e:
            log.warning("Failed to persist resident task card id: %s", e)
            return False

    def _delete_task_card_message_outcome(self, compound_id: str) -> str:
        """Delete the exact tracked old card, distinguishing explicit absence.

        ``missing`` and ``nondeletable`` are returned only for Telegram's exact
        terminal responses; malformed ids and every other failure remain ``failed``
        so callers cannot inject a second card on a guess.
        """
        try:
            account, chat_id, tg_msg_id = self._parse_compound_id(compound_id)
            acct = self._service.get_account(account)
            acct.delete_message(chat_id=chat_id, message_id=tg_msg_id)
            return _TASK_CARD_DELETE_OK
        except Exception as exc:
            outcome = self._task_card_delete_error_outcome(exc)
            if outcome == _TASK_CARD_DELETE_MISSING:
                log.debug("Prior task card was already missing")
            elif outcome == _TASK_CARD_DELETE_NONDELETABLE:
                log.debug("Prior task card cannot be deleted for everyone")
            else:
                log.debug(
                    "Failed to delete prior task card message (error_type=%s)",
                    type(exc).__name__,
                )
            return outcome

    def _delete_task_card_message(self, compound_id: str) -> bool:
        """Compatibility bool for exact tracked-card deletion."""
        return self._delete_task_card_message_outcome(compound_id) == _TASK_CARD_DELETE_OK

    def _task_card_update(self, args: dict) -> dict:
        card_message_id = args["card_message_id"]
        card_account, card_chat_id, _ = self._parse_compound_id(card_message_id)
        # Current kernel callers provide the explicit route; legacy/private test
        # callers may omit it, in which case the compound id remains the route.
        account = args.get("account", card_account)
        chat_id = args.get("chat_id", card_chat_id)
        if card_account != account or card_chat_id != chat_id:
            return {"status": "error", "error": "Failed to update task card"}
        automatic = self._format_task_card_text(
            args.get("tool", ""), args.get("tool_action", ""), args.get("reasoning", ""),
            rows=args.get("rows"), metadata=args.get("metadata"),
            normal_rows=self._taskcard_normal_rows())
        # All automatic mutations share the same edit-first delivery discipline:
        # identical content is success, unknown transport failure fails loud, and
        # only a provider-confirmed edit-impossible condition may replace.
        return self._deliver_channel_frame(
            account,
            chat_id,
            "automatic",
            automatic,
            error="Failed to update task card",
            resident_id=card_message_id,
        )

    def _task_card_finalize(self, args: dict) -> dict:
        """Freeze the resident card on its last behavior.

        With ``rows`` (the batched form) the card keeps its concrete last batch —
        tool rows, completed markers, and final elapsed — as a last-behavior
        record; there is intentionally no generic overall ``DONE`` subject.  The
        legacy scalar form (no rows) retains the historical ``✅ TASK CARD · DONE``
        marker for backward compatibility with single-step callers.
        """
        card_message_id = args.get("card_message_id")
        if card_message_id:
            rows = args.get("rows")
            if rows is not None:
                automatic = self._format_task_card_text(
                    "", "", "", rows=rows, metadata=args.get("metadata"),
                    normal_rows=self._taskcard_normal_rows(),
                )
            else:
                tool = args.get("tool", "")
                if tool:
                    automatic = self._format_task_card_text(
                        tool, args.get("tool_action", ""), args.get("reasoning", ""))
                    automatic += "\n\n✅ TASK CARD · DONE"
                else:
                    automatic = "✅ TASK CARD · DONE"
            card_account, card_chat_id, _ = self._parse_compound_id(card_message_id)
            # Backward compatibility: older internal callers supplied only the
            # compound id. Current callers' explicit route, when present, must
            # match it exactly before any edit or delete is permitted.
            account = args.get("account", card_account)
            chat_id = args.get("chat_id", card_chat_id)
            if card_account != account or card_chat_id != chat_id:
                return {"status": "error", "error": "Failed to finalize task card"}
            return self._deliver_channel_frame(
                account,
                chat_id,
                "automatic",
                automatic,
                error="Failed to finalize task card",
                resident_id=card_message_id,
            )
        return {"status": "ok"}

    def _task_card_programmable(self, sub_action: str, args: dict) -> dict:
        """Legacy direct update/clear path for the programmable channel.

        Current runtime projection reads the intrinsic ``task_card`` artifact
        from ``<workdir>/taskcard/status`` and ``taskcard/taskcard.md`` and calls
        the shared resident delivery path read-only. This helper is retained for
        historical compatibility tests and cleanup semantics; it is not a public
        Telegram-owned Task Card controller endpoint.

        Sub-actions:
          - create / update:  render the validated ``card`` object into the
                              programmable frame, compose, and edit the resident.
          - finalize:         clear the programmable frame, compose, and edit the
                              resident so the automatic channel remains. When the
                              programmable slot is the ONLY resident content, the
                              cleared compose is empty and a nonempty
                              ``_TASK_CARD_WATCH_STOPPED`` terminal marker is
                              delivered instead (Telegram cannot edit to empty),
                              leaving the resident reusable while the slot is still
                              committed clear on success.

        The caller supplies ``account`` and ``chat_id`` so both channels resolve
        to the same resident id; the current public producer remains
        ``lingtai.tools.task_card``.
        """
        account = args["account"]
        chat_id = args["chat_id"]
        empty_fallback: str | None = None
        if sub_action == "finalize":
            frame: str | None = None
            empty_fallback = self._TASK_CARD_WATCH_STOPPED
        elif sub_action in ("create", "update"):
            card = args.get("card")
            if not isinstance(card, dict):
                return {"status": "error", "error": "programmable card must be an object"}
            frame = self._format_programmable_card_text(card)
        else:
            return {"status": "error", "error": f"Unknown sub_action: {sub_action}"}

        # Deliver the proposed programmable frame and commit it only on success:
        # a failed edit must leave the last delivered programmable frame in place
        # so a subsequent automatic compose cannot resurrect an unsent frame.
        return self._deliver_channel_frame(
            account, chat_id, "programmable", frame,
            error="Failed to send task card", empty_fallback=empty_fallback)

    @classmethod
    def _format_task_card_text(
        cls, tool: str, action: str, reasoning: str,
        *, rows: list | None = None, metadata: dict | None = None,
        normal_rows: int = _TASK_CARD_DEFAULT_NORMAL_ROWS,
        now: datetime | None = None,
    ) -> str:
        """Render a Task Card: header, one line per tool row, fixed footer.

        When ``rows`` is supplied (the batched multi-row form) each parallel or
        sequential call renders as its own row showing ``tool.action``, its
        redacted reasoning excerpt, its own captured start stamp, its own
        whole-second elapsed, and a ``✓`` marker once it has completed.  The
        scalar ``tool``/``action``/``reasoning`` path is retained for
        backward-compatible single-tool callers and does not render the footer
        (it is the legacy transient-step form).

        ``normal_rows`` is the live operator setting echoed in the footer
        (defaults to the manager's default when a caller omits it, e.g. narrow
        tests exercising the render in isolation). ``now`` is the render
        instant used for the bottom ``Last Updated:`` line (defaults to the
        real local time; injectable so tests stay deterministic).

        Secret redaction always runs on each row's reasoning *before* any
        excerpt or length trim, so a secret can never survive truncation, and
        every row is always represented even under length pressure — rows are
        never dropped to fit; only per-row excerpts shrink.  The
        ``_TASK_CARD_TEXT_LIMIT`` budget governs that reasoning-excerpt
        shrinkage only; it is not a guarantee that the whole render stays under
        the limit.  Fixed per-row scaffolding is unbounded in the number of
        rows, so many selected rows can still produce a render above the budget
        (and above Telegram's transport limit).  The durable ``/taskcard N``
        control bounds the latest API-call groups to 1-10; it does not truncate
        fixed row scaffolding.  See ``_format_rows_task_card_text``.
        """
        return TaskCardEventProjection.format_task_card_text(
            tool,
            action,
            reasoning,
            rows=rows,
            metadata=metadata,
            normal_rows=normal_rows,
            now=now,
        )

    @classmethod
    def _format_scalar_task_card_text(cls, tool: str, action: str, reasoning: str) -> str:
        return TaskCardEventProjection.format_scalar_task_card_text(
            tool, action, reasoning,
        )

    @staticmethod
    def _format_task_card_count(value: object) -> str | None:
        return TaskCardEventProjection.format_count(value)

    @classmethod
    def _format_task_card_metadata(cls, metadata: object) -> list[str]:
        """Render at most two compact session lines within a 150-char budget."""
        return TaskCardEventProjection.format_metadata(metadata)

    @classmethod
    def _format_rows_task_card_text(
        cls, rows: list, *, metadata: dict | None = None,
        normal_rows: int = _TASK_CARD_DEFAULT_NORMAL_ROWS,
        now: datetime | None = None,
    ) -> str:
        return TaskCardEventProjection.format_rows_task_card_text(
            rows,
            metadata=metadata,
            normal_rows=normal_rows,
            now=now,
        )

    @staticmethod
    def _task_card_render_time(now: datetime | None) -> str:
        """Resolve the render-time stamp, defaulting to the real local instant."""
        return TaskCardEventProjection.render_time(now)

    @staticmethod
    def _task_card_machine_identifier(value: object, *, limit: int) -> str | None:
        return TaskCardEventProjection.machine_identifier(value, limit=limit)

    @classmethod
    def _format_api_error_line(cls, row: dict) -> str:
        """Render a sanitized LLM/provider API-error row.

        Shows only bounded machine identifiers supplied by the kernel (exception
        type, public provider/model, valid HTTP status, allow-listed code) plus
        lifecycle state. Opaque external identifiers and raw exception text are
        deliberately absent, so there is no free-form field to leak.
        """
        return TaskCardEventProjection.format_api_error_line(row)

    @staticmethod
    def _format_elapsed(value: object) -> str:
        """Render a row's elapsed seconds as whole seconds (no decimal point).

        The heartbeat still ticks every 0.5s, but elapsed is floored to whole
        seconds by the kernel, so half-second frames read ``0s, 0s, 1s, 1s, 2s``.
        This coerces + floors defensively (a float payload is floored, junk
        degrades to ``0``) so the render never raises.
        """
        return TaskCardEventProjection.format_elapsed(value)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    _PARSE_MODES = {"HTML", "MarkdownV2", "Markdown"}
    _RENDERING_MODES = _PARSE_MODES | {"plain_text", "entities"}

    @staticmethod
    def _normalize_chat_action(value: Any) -> Any:
        """Treat an empty chat_action as omitted/no typing indicator.

        Optional enum-like tool arguments may be serialized as ``""`` by some
        callers.  Telegram only needs chat_action when the caller explicitly
        asks for one, so normalize an empty string before action dispatch.
        """
        if value == "":
            return None
        return value

    @classmethod
    def _rendering_mode(cls, args: dict) -> str:
        """Return the explicit rendering choice, with an internal plain fallback.

        The public ToolFamily requires ``rendering_mode``.  Manager internals
        also send text (for example automatic progress) without passing through
        that model-facing schema, so those internal calls retain plain-text
        behavior without creating a public default.
        """
        value = args.get("rendering_mode")
        return "plain_text" if value is None else value

    @classmethod
    def _bot_parse_mode(cls, args: dict) -> str | None:
        mode = cls._rendering_mode(args)
        return mode if mode in cls._PARSE_MODES else None

    @classmethod
    def _rendering_error(cls, mode: str, *, has_entities: bool) -> str | None:
        if mode not in cls._RENDERING_MODES:
            return (
                "rendering_mode must be one of: plain_text, HTML, MarkdownV2, "
                "Markdown, entities"
            )
        if mode == "entities" and not has_entities:
            return "rendering_mode='entities' requires entities or caption_entities"
        if mode != "entities" and has_entities:
            return (
                f"rendering_mode='{mode}' cannot be combined with entities; "
                "choose rendering_mode='entities'"
            )
        return None

    def _rich_text_options(self, args: dict) -> tuple[dict[str, Any], str | None]:
        """Extract Bot API options for a text message from rendering_mode.

        ``rendering_mode`` is required at the public family boundary.  Missing
        values here are only for manager-owned internal sends and mean plain
        text; the model-facing schema never relies on that fallback.
        """
        opts: dict[str, Any] = {}
        entities = args.get("entities")
        has_entities = entities is not None or args.get("caption_entities") is not None
        mode = self._rendering_mode(args)
        error = self._rendering_error(mode, has_entities=has_entities)
        if error:
            return {}, error
        if mode == "entities":
            opts["entities"] = entities
        elif mode in self._PARSE_MODES:
            opts["parse_mode"] = mode
        if args.get("link_preview_options") is not None:
            opts["link_preview_options"] = args.get("link_preview_options")
        if args.get("disable_web_page_preview") is not None:
            opts["disable_web_page_preview"] = bool(args.get("disable_web_page_preview"))
        return opts, None

    def _caption_options(self, args: dict) -> tuple[dict[str, Any], str | None]:
        """Extract Bot API options for a media caption from rendering_mode."""
        opts: dict[str, Any] = {}
        caption_entities = args.get("caption_entities")
        if caption_entities is None:
            caption_entities = args.get("entities")
        mode = self._rendering_mode(args)
        error = self._rendering_error(mode, has_entities=caption_entities is not None)
        if error:
            return {}, error
        if mode == "entities":
            opts["caption_entities"] = caption_entities
        elif mode in self._PARSE_MODES:
            opts["parse_mode"] = mode
        return opts, None

    def _send(self, args: dict) -> dict:
        account = self._resolve_account(args)
        chat_id = args.get("chat_id")
        text = args.get("text", "")
        media = args.get("media")
        # Some tool-call frontends serialize optional object fields as an empty
        # attachment object for text-only sends, e.g.
        # {"type": "document", "path": ""}. Treat that shape as absent
        # media so text-only sends do not try to upload/open an empty path.
        if media and isinstance(media, dict) and not (media.get("path") or "").strip():
            media = None
        reply_markup = args.get("reply_markup")
        chat_action = self._normalize_chat_action(args.get("chat_action"))
        placeholder = bool(args.get("placeholder", False))
        rich_text_options, rich_text_error = self._rich_text_options(args)
        caption_options, caption_error = self._caption_options(args)
        if rich_text_error or caption_error:
            return {"error": rich_text_error or caption_error}

        if not chat_id:
            return {"error": "chat_id is required"}
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            # The reserved synthetic events bucket is read/search-only; there
            # is no real chat behind it to deliver a message to.
            return {
                "error": (
                    "chat_id must be a numeric Telegram chat ID for send; the "
                    f"reserved '{tg_updates.SYNTHETIC_EVENTS_CHAT_ID}' events "
                    "bucket is read/search-only"
                ),
            }

        # Chat action shortcut: when chat_action is set and no text/media is
        # provided, send the typing indicator instead of a message. Skips
        # duplicate-protection and sent/ persistence — chat actions are
        # ephemeral (Telegram auto-expires them after 5 seconds).
        if chat_action and not text and not media:
            acct = self._service.get_account(account)
            acct.send_chat_action(chat_id, chat_action)
            return {"status": "ok", "chat_action": chat_action}

        if not text and not media:
            return {"error": "text or media is required"}

        # Duplicate send protection
        dup_key = (account, chat_id, text)
        count = self._last_sent.get(dup_key, 0)
        if count >= self._dup_free_passes:
            return {
                "status": "blocked",
                "warning": "Identical message already sent. Think twice before repeating.",
            }

        acct = self._service.get_account(account)
        # Resolve the reply target from any of the accepted inputs: the private
        # `_reply_to_message_id` (set by `_reply`), the public/raw
        # `reply_to_message_id`, or a compound `message_id` (account:chat:msgid).
        reply_to = args.get("_reply_to_message_id")
        if reply_to is None:
            reply_to = args.get("reply_to_message_id")
        if reply_to is None and args.get("message_id"):
            try:
                _account, _chat_id, reply_to = self._parse_compound_id(str(args["message_id"]))
            except Exception:
                reply_to = None

        # Placeholder mode: fire a typing action before sending so the user
        # sees "is typing…" alongside the placeholder text. Best-effort —
        # never block or fail the send if the chat action call errors.
        if placeholder:
            try:
                acct._request("sendChatAction", json={
                    "chat_id": chat_id, "action": "typing",
                })
            except TelegramRateLimitError:
                raise
            except Exception as e:
                log.warning(
                    "sendChatAction (placeholder typing) failed for %s:%s: %s",
                    account, chat_id, e,
                )

        # Send via Bot API
        if media:
            media_type = media.get("type")
            media_path = media.get("path", "")
            media_file = Path(media_path)
            if not media_file.is_file() or media_file.stat().st_size == 0:
                return {
                    "error": (
                        "media.path does not point to a readable, non-empty "
                        f"file: {media_path}"
                    )
                }
            if media_type == "photo":
                result = acct.send_photo(
                    chat_id, media_path, caption=text or None,
                    reply_to_message_id=reply_to,
                    **caption_options,
                )
            elif media_type == "document":
                result = acct.send_document(
                    chat_id, media_path, caption=text or None,
                    reply_to_message_id=reply_to,
                    **caption_options,
                )
            else:
                return {"error": f"Unknown media type: {media_type}"}
        else:
            result = acct.send_message(
                chat_id, text, reply_markup=reply_markup,
                reply_to_message_id=reply_to,
                **rich_text_options,
            )

        # Track for duplicate detection
        self._last_sent[dup_key] = count + 1

        # Persist to sent/
        sent_id = str(uuid4())
        sent_dir = self._account_dir(account) / "sent" / sent_id
        sent_dir.mkdir(parents=True, exist_ok=True)
        tg_message_id = result.get("message_id", 0)
        compound_id = f"{account}:{chat_id}:{tg_message_id}"
        sent_record = {
            "id": compound_id,
            "to": {"chat_id": chat_id},
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "text": text,
            "media": media,
            "reply_markup": reply_markup,
            "reply_to_message_id": reply_to,
            "rendering_mode": self._rendering_mode(args),
            "parse_mode": self._bot_parse_mode(args),
            "entities": args.get("entities"),
            "caption_entities": args.get("caption_entities"),
            "link_preview_options": args.get("link_preview_options"),
            "disable_web_page_preview": args.get("disable_web_page_preview"),
            "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "placeholder" if placeholder else "sent",
        }
        (sent_dir / "message.json").write_text(
            json.dumps(sent_record, indent=2, default=str), encoding="utf-8",
        )

        response: dict[str, Any] = {
            "status": "sent",
            "message_id": compound_id,
        }
        if placeholder:
            response["placeholder"] = True
            response["hint"] = (
                "Live-status placeholder sent — edit it at "
                "meaningful phase changes to show progress: "
                f"telegram(action='edit', message_id='{compound_id}', "
                "text=<updated status>). Send the final answer as a "
                "separate durable `action='send'` or `action='reply'`."
            )

        # Issue #8: Add "done" reaction (✅) to the original message if reply_to
        if reply_to:
            try:
                acct.set_message_reaction(chat_id, reply_to, REACTION_DONE)
            except Exception as e:
                log.debug("Failed to add 'done' reaction: %s", e)

        # Issue #8: Stop typing indicator now that response is sent
        _typing_manager.stop_typing(acct, chat_id)

        return response

    def _check(self, args: dict) -> dict:
        account = self._resolve_account(args)
        inbox = self._list_messages(account, "inbox")
        sent = self._list_messages(account, "sent")
        messages = inbox + sent
        messages.sort(key=lambda m: m.get("date", ""), reverse=True)
        read_ids = self._read_ids(account)
        taskcard = self._taskcard_enabled()

        # Group by chat_id for conversation view
        conversations: dict[int, dict] = {}
        for msg in messages:
            # Extract chat_id from inbox-style or sent-style records
            chat = msg.get("chat")
            if isinstance(chat, dict):
                cid = chat.get("id", 0)
            else:
                to = msg.get("to")
                cid = to.get("chat_id", 0) if isinstance(to, dict) else 0

            if cid not in conversations:
                conversations[cid] = {
                    "chat_id": cid,
                    "chat_type": msg.get("chat", {}).get("type", "private") if isinstance(msg.get("chat"), dict) else "private",
                    "last_from": msg.get("from") or {"is_bot": True},
                    "last_text": (msg.get("text") or "")[:100],
                    "last_date": msg.get("date", ""),
                    "total": 0,
                    "unread": 0,
                    "taskcard": taskcard,
                }
            conversations[cid]["total"] += 1
            if msg.get("id") and msg["id"] not in read_ids:
                conversations[cid]["unread"] += 1

        return {
            "status": "ok",
            "taskcard": taskcard,
            "total": len(messages),
            "messages": list(conversations.values()),
        }

    def _read(self, args: dict) -> dict:
        account = self._resolve_account(args)
        chat_id = args.get("chat_id")
        limit = args.get("limit", 10)

        if not chat_id:
            return {"error": "chat_id is required"}

        # Merge inbox and sent messages so post-molt agents can see their
        # own outgoing messages and avoid duplicate sends.
        inbox = self._list_messages(account, "inbox")
        sent = self._list_messages(account, "sent")
        combined = inbox + sent
        combined.sort(key=lambda m: m.get("date", ""), reverse=True)

        def _chat_id_of(m: dict) -> int | None:
            """Extract chat_id from inbox-style or sent-style records."""
            chat = m.get("chat")
            if isinstance(chat, dict):
                return chat.get("id")
            to = m.get("to")
            if isinstance(to, dict):
                return to.get("chat_id")
            return None

        filtered = [m for m in combined if _chat_id_of(m) == chat_id]
        recent = filtered[:limit]

        # Mark as read
        compound_ids = [m["id"] for m in recent if m.get("id")]
        if compound_ids:
            self._mark_read(account, compound_ids)
            self._clear_notification_if_handled()

        # Strip internal fields and derive current presentation state at read time.
        taskcard = self._taskcard_enabled()
        cleaned = []
        for m in recent:
            entry = {
                "id": m.get("id"),
                "from": m.get("from"),
                "to": m.get("to"),
                "chat": m.get("chat"),
                "date": m.get("date"),
                "text": m.get("text"),
                "media": m.get("media"),
                "callback_query": m.get("callback_query"),
                "reply_to_message_id": m.get("reply_to_message_id"),
                "_direction": "outgoing" if m.get("to") else "incoming",
                "taskcard": taskcard,
                # Authoritative lossless raw Update envelope (None for
                # outgoing/pre-envelope records).
                "telegram": m.get("telegram"),
            }
            for extra in (
                "update_type", "synthetic", "unmatched_edit", "voice_transcript",
            ):
                if m.get(extra) is not None:
                    entry[extra] = m[extra]
            cleaned.append(entry)

        return {"status": "ok", "taskcard": taskcard, "messages": cleaned}

    def _reply(self, args: dict) -> dict:
        compound_id = args.get("message_id", "")
        text = args.get("text", "")
        if not compound_id:
            return {"error": "message_id is required"}
        if not text:
            return {"error": "text is required"}

        account, chat_id, tg_msg_id = self._parse_compound_id(compound_id)
        result = self._send({
            "account": account,
            "chat_id": chat_id,
            "text": text,
            "media": args.get("media"),
            "reply_markup": args.get("reply_markup"),
            "rendering_mode": self._rendering_mode(args),
            "entities": args.get("entities"),
            "caption_entities": args.get("caption_entities"),
            "link_preview_options": args.get("link_preview_options"),
            "disable_web_page_preview": args.get("disable_web_page_preview"),
            # We need to pass reply_to_message_id through
            "_reply_to_message_id": tg_msg_id,
        })
        if result.get("status") == "sent":
            self._mark_read(account, [compound_id])
            self._clear_notification_if_handled()
        return result

    def _search(self, args: dict) -> dict:
        query = args.get("query", "")
        if not query:
            return {"error": "query is required"}
        account = self._resolve_account(args)
        target_chat = args.get("chat_id")

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}

        messages = self._list_messages(account, "inbox")
        taskcard = self._taskcard_enabled()
        matches = []
        for msg in messages:
            if target_chat and msg.get("chat", {}).get("id") != target_chat:
                continue
            searchable = " ".join([
                str(msg.get("from", {}).get("username", "")),
                str(msg.get("from", {}).get("first_name", "")),
                msg.get("text", "") or "",
                str(msg.get("update_type") or ""),
            ])
            if pattern.search(searchable):
                match = {
                    "id": msg.get("id"),
                    "from": msg.get("from"),
                    "chat": msg.get("chat"),
                    "date": msg.get("date"),
                    "text": msg.get("text"),
                    "taskcard": taskcard,
                    # Authoritative lossless raw Update envelope for the
                    # matched message.
                    "telegram": msg.get("telegram"),
                }
                for extra in ("update_type", "synthetic", "unmatched_edit"):
                    if msg.get(extra) is not None:
                        match[extra] = msg[extra]
                matches.append(match)

        return {
            "status": "ok",
            "taskcard": taskcard,
            "total": len(matches),
            "messages": matches,
        }

    def _delete(self, args: dict) -> dict:
        compound_id = args.get("message_id", "")
        if not compound_id:
            return {"error": "message_id is required"}
        account, chat_id, tg_msg_id = self._parse_compound_id(compound_id)
        acct = self._service.get_account(account)
        acct.delete_message(chat_id=chat_id, message_id=tg_msg_id)
        return {"status": "deleted", "message_id": compound_id}

    def _edit(self, args: dict) -> dict:
        compound_id = args.get("message_id", "")
        text = args.get("text", "")
        if not compound_id:
            return {"error": "message_id is required"}
        if not text:
            return {"error": "text is required"}
        account, chat_id, tg_msg_id = self._parse_compound_id(compound_id)
        reply_markup = args.get("reply_markup")
        acct = self._service.get_account(account)

        # Detect if original message had media (caption edit vs text edit)
        is_caption = False
        sent_dir = self._account_dir(account) / "sent"
        if sent_dir.is_dir():
            for msg_dir in sent_dir.iterdir():
                msg_file = msg_dir / "message.json"
                if msg_dir.is_dir() and msg_file.is_file():
                    try:
                        data = json.loads(msg_file.read_text(encoding="utf-8"))
                        if data.get("id") == compound_id and data.get("media"):
                            is_caption = True
                            break
                    except (json.JSONDecodeError, OSError):
                        continue

        if is_caption:
            edit_options, rendering_error = self._caption_options(args)
        else:
            edit_options, rendering_error = self._rich_text_options(args)
        if rendering_error:
            return {"error": rendering_error}

        acct.edit_message(
            chat_id=chat_id, message_id=tg_msg_id, text=text,
            reply_markup=reply_markup, is_caption=is_caption,
            **edit_options,
        )
        return {"status": "edited", "message_id": compound_id}

    def _contacts(self, args: dict) -> dict:
        account = self._resolve_account(args)
        return {"status": "ok", "contacts": self._load_contacts(account)}

    def _add_contact(self, args: dict) -> dict:
        account = self._resolve_account(args)
        chat_id = args.get("chat_id")
        alias = args.get("alias", "")
        if not chat_id:
            return {"error": "chat_id is required"}
        if not alias:
            return {"error": "alias is required"}
        contacts = self._load_contacts(account)
        contacts[alias] = {
            "chat_id": chat_id,
            "username": args.get("username", ""),
            "first_name": args.get("first_name", ""),
        }
        self._save_contacts(account, contacts)
        return {"status": "added", "alias": alias}

    def _remove_contact(self, args: dict) -> dict:
        account = self._resolve_account(args)
        alias = args.get("alias", "")
        chat_id = args.get("chat_id")
        contacts = self._load_contacts(account)
        if alias and alias in contacts:
            del contacts[alias]
            self._save_contacts(account, contacts)
            return {"status": "removed", "alias": alias}
        elif chat_id:
            to_remove = [k for k, v in contacts.items() if v.get("chat_id") == chat_id]
            for k in to_remove:
                del contacts[k]
            if to_remove:
                self._save_contacts(account, contacts)
                return {"status": "removed", "aliases": to_remove}
        return {"error": "Contact not found"}

    def _accounts(self) -> dict:
        return {
            "status": "ok",
            "accounts": self._service.list_accounts(),
            "details": self._service.account_details(),
            "identity_path": str(self._service.identity_path()),
        }

    # ------------------------------------------------------------------
    # Manual — progressive-disclosure usage guidance
    # ------------------------------------------------------------------
    #
    # The manual lives in this package's bundled SKILL.md (standard skill
    # format: YAML frontmatter + markdown body), loaded at import time above.
    # action='manual' returns the full skill markdown plus parsed metadata and
    # the resolved path; the frontmatter is also injected into the schema's
    # 'manual' action description as a catalog entry. Bundled assets/references,
    # if any, are documented inside SKILL.md and are not returned as a structured
    # tool-side list; do not add assets/references fields here.

    def _manual(self) -> dict:
        return _skill.manual_payload(
            _SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH, _SKILL_NAME
        )
