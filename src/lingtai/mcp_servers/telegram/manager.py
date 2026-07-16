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
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

import logging
import threading

from .. import _skill
from .task_card.resident import TaskCardResident

if TYPE_CHECKING:
    from lingtai.kernel.notification_store import NotificationStorePort
    from .service import TelegramService

log = logging.getLogger(__name__)


from lingtai.kernel._frontmatter import strip_frontmatter


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
_DOCUMENT_DOWNLOAD_REASON_CAP = 200
_TELEGRAM_API_ERROR_PREFIX = "Telegram API error: "

# Task Card edit outcomes are deliberately narrower than generic transport
# success/failure. Telegram reports an identical edit as a 400 no-op; that means
# the resident already carries the proposed content and must never trigger a
# replacement. Only the exact Bot API conditions which prove the message itself
# cannot be edited permit replacement; every unknown/network/provider failure
# fails loud and leaves the resident and committed slot state untouched.
_TASK_CARD_EDIT_OK = "ok"
_TASK_CARD_EDIT_IMPOSSIBLE = "edit_impossible"
_TASK_CARD_EDIT_FAILED = "failed"
_TASK_CARD_EDIT_UNCHANGED = "bad request: message is not modified"
_TASK_CARD_EDIT_IMPOSSIBLE_DESCRIPTIONS = frozenset({
    "bad request: message to edit not found",
    "bad request: message can't be edited",
    "bad request: message can not be edited",
})
_TASK_CARD_DELETE_OK = "ok"
_TASK_CARD_DELETE_MISSING = "missing"
_TASK_CARD_DELETE_FAILED = "failed"
_TASK_CARD_DELETE_MISSING_DESCRIPTIONS = frozenset({
    "bad request: message to delete not found",
})

# Fixed human warning shown on every Task Card render (running and frozen
# last-behavior). Jason: never reply to the card; point directly to the local
# command that controls its delivery. Kept short so it always fits under the
# Telegram message-size bound even under multi-row length pressure. The
# "current: X" suffix is appended per-render from the manager's live
# normal-row setting; see ``_task_card_footer``.
_TASK_CARD_FOOTER = (
    "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
    "/taskcard N sets normal rows (1-10"
)
_TASK_CARD_DEFAULT_NORMAL_ROWS = 1
_TASK_CARD_METADATA_MAX_CHARS = 150
_TASK_CARD_METADATA_MAX_LINES = 2

# Card-level "last updated" line prefix.  The automatic channel's final
# standalone line reports when that channel's event-tail snapshot was last
# rendered (not any row's start instant, and not a wall clock that advances on
# unrelated programmable-channel edits) as ``Last Updated: HH:MM:SS UTC±HH``,
# always present — unlike the retired started_at-derived line, it never
# depends on any row carrying a stamp.
_TASK_CARD_TIME_PREFIX = "Last Updated: "


def _task_card_footer(normal_rows: int) -> str:
    """Build the fixed footer with the live normal-row setting appended.

    ``normal_rows`` is trusted to already be validated to ``1-10`` by the
    caller (``TelegramManager._taskcard_normal_rows``); this only formats it.
    """
    return f"{_TASK_CARD_FOOTER}, current: {normal_rows})."


def _format_task_card_current_time(now: datetime) -> str:
    """Render a render-time instant as ``HH:MM:SS UTC±HH`` (hour-only offset).

    Mirrors the kernel's per-row ``_format_task_card_timestamp`` shape so the
    bottom line and each row's own stamp read consistently. Returns ``""`` for
    a naive ``datetime`` (no usable offset) so the render simply omits the
    line rather than raising.
    """
    offset = now.utcoffset()
    if offset is None:
        return ""
    total = offset.total_seconds()
    sign = "-" if total < 0 else "+"
    hours = int(abs(total) // 3600)
    return f"{now.strftime('%H:%M:%S')} UTC{sign}{hours:02d}"


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
                "send: send message to a chat (chat_id, text; optional media, reply_markup, placeholder, chat_action, parse_mode/entities). "
                "For charts, reports, generated artifacts, and other files the user should open intact, prefer media.type='document'; use media.type='photo' only when an inline Telegram photo preview is desired, because photo previews may crop, compress, or display poorly for text-heavy graphics. "
                "If chat_action is set and no text/media is provided, sends a typing "
                "indicator (auto-expires after 5s) instead of a message. "
                "check: list recent conversations with unread counts (optional account). "
                "read: read messages from a chat (chat_id; optional limit). "
                "reply: reply to a specific message (message_id from read results, text; optional parse_mode/entities). "
                "search: search messages (query; optional account, chat_id). "
                "delete: delete a bot message (message_id). "
                "edit: edit a bot message (message_id, text; optional reply_markup, parse_mode/entities). "
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
            "type": "integer",
            "description": "Telegram chat ID",
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
                "type": {"type": "string", "enum": ["photo", "document", "voice", "audio"]},
                "path": {"type": "string"},
            },
            "description": (
                "Media attachment: {type: 'photo'|'document'|'voice'|'audio', path: '/path/to/file'}. "
                "For charts, HTML/SVG/PNG reports, CSVs, PDFs, and other generated artifacts that should arrive as an intact file, use type='document'. "
                "Use type='photo' only for native inline photo previews; Telegram photo delivery can crop, compress, thumbnail, or otherwise display text-heavy charts poorly. "
                "Do not paste local file paths in message text as a substitute for attaching the file."
            ),
        },
        "reply_markup": {
            "type": "object",
            "description": "Inline keyboard markup",
        },
        "parse_mode": {
            "type": "string",
            "enum": ["HTML", "MarkdownV2", "Markdown", ""],
            "description": (
                "Telegram Bot API parse_mode for rich text (send/reply/edit, "
                "and media captions). Omit or pass an empty string for plain text."
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
        # public task_card renderer output). ``TaskCardResident`` owns the
        # frames, per-route locks, and atomic enablement. Updating one channel
        # never reads, advances, or overrides the other's frame.
        self._resident = TaskCardResident(
            enabled=self._raw_taskcard_enabled(),
            deliver=self._deliver_channel_frame_locked,
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
        return parts[0], int(parts[1]), int(parts[2])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._service.start()
        self._start_task_card_tail()

    def stop(self) -> None:
        self._stop_task_card_tail()
        self._service.stop()

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    def handle(self, args: dict) -> dict:
        action = args.get("action")
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

        elif "edited_message" in update:
            tg_msg = update["edited_message"]
            compound_id = f"{account_alias}:{tg_msg['chat']['id']}:{tg_msg['message_id']}"
            sender = tg_msg.get("from", {})
            payload = {
                "id": compound_id,
                "from": sender,
                "chat": tg_msg.get("chat", {}),
                "date": datetime.fromtimestamp(
                    tg_msg.get("edit_date", tg_msg.get("date", 0)), tz=timezone.utc,
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "text": tg_msg.get("text") or tg_msg.get("caption") or "",
                "media": None,
                "reply_to_message_id": None,
                "callback_query": None,
            }
            username = sender.get("username") or sender.get("first_name", "unknown")

            # Update existing inbox entry in-place if found
            existing_dir = self._find_inbox_by_compound_id(account_alias, compound_id)
            if existing_dir is not None:
                (existing_dir / "message.json").write_text(
                    json.dumps(payload, indent=2, default=str), encoding="utf-8",
                )
                # Clean up the unused new dir
                msg_dir.rmdir()
            else:
                log.info(
                    "telegram unmatched edited_message account=%s id=%s; skipping orphan inbox write",
                    account_alias,
                    compound_id,
                )
                try:
                    msg_dir.rmdir()
                except OSError as exc:
                    log.debug(
                        "failed to remove unused edited_message dir %s: %s",
                        msg_dir,
                        exc,
                    )
                return
        else:
            return  # unsupported update type

        # Persist (for message and callback_query types)
        if "edited_message" not in update:
            (msg_dir / "message.json").write_text(
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
        if "callback_query" in update:
            update_type = "callback_query"
        elif "edited_message" in update:
            update_type = "edited_message"
        else:
            update_type = "message"

        # Issue #5: Don't wake the agent for edited messages — they are
        # typically trivial corrections (typo fixes) and not worth a wake.
        # The inbox entry is still updated in-place so the agent sees the
        # latest content on next read.
        should_wake = update_type != "edited_message"

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
            target_chat_id = int(chat_id)
        except (TypeError, ValueError):
            return []

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
                msg_chat_id = None
                msg_id = data.get("id", "")
                if msg_id:
                    parts = msg_id.split(":")
                    if len(parts) == 3:
                        try:
                            msg_chat_id = int(parts[1])
                        except ValueError:
                            pass
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
            if isinstance(ref, str) and _looks_like_compound_id(ref):
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
                try:
                    account, _chat_id, _msg_id = self._parse_compound_id(compound_id)
                except ValueError:
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
        """Classify only explicit not-found as an already-absent old card."""
        detail = str(exc)
        if not detail.startswith(_TELEGRAM_API_ERROR_PREFIX):
            return _TASK_CARD_DELETE_FAILED
        description = " ".join(
            detail[len(_TELEGRAM_API_ERROR_PREFIX):].split()
        ).casefold()
        if description in _TASK_CARD_DELETE_MISSING_DESCRIPTIONS:
            return _TASK_CARD_DELETE_MISSING
        return _TASK_CARD_DELETE_FAILED

    def _try_update_progress_message(
        self,
        compound_id: str,
        text: str,
    ) -> str:
        """Edit once and preserve whether replacement is actually permissible."""
        try:
            account, chat_id, tg_msg_id = self._parse_compound_id(compound_id)
            acct = self._service.get_account(account)
            acct.edit_message(chat_id, tg_msg_id, text)
            return _TASK_CARD_EDIT_OK
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
            return outcome

    def update_progress_message(
        self,
        compound_id: str,
        text: str,
    ) -> bool:
        """Compatibility bool: true for an applied edit or identical-content no-op."""
        return self._try_update_progress_message(compound_id, text) == _TASK_CARD_EDIT_OK

    # ------------------------------------------------------------------
    # Private Task Card helpers (internally driven — by the kernel automatic
    # driver and the Telegram-owned programmable controller — not LLM-exposed)
    # ------------------------------------------------------------------

    # Reasoning cap (Unicode code points) after secret redaction.
    _TASK_CARD_REASONING_CAP = 500
    # Overall render ceiling, safely below Telegram's 4096-char message limit.
    _TASK_CARD_TEXT_LIMIT = 3500
    # Header shown at the top of every card.
    _TASK_CARD_HEADER = "📋 TASK CARD"
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
        """Deliver a proposed ``channel`` frame to the tracked resident target and
        commit it to the resident owner's frames **only after** the edit/send/
        replacement succeeds.

        The composed payload uses the proposed ``frame`` for ``channel`` and the
        last committed frame for the other slot. An identical-content Telegram
        no-op counts as success and retains the resident id. Replacement is allowed
        only after Telegram explicitly proves the resident is edit-impossible;
        unknown or transient edit failures fail loud without sending or committing.
        Thus a later automatic or programmable compose can never resurrect a frame
        that was never delivered. Shared by every automatic mutation and the
        programmable channel.

        ``empty_fallback`` supplies a nonempty terminal presentation for the case
        where the composed text would be empty (clearing a programmable-only
        resident). Telegram cannot edit/send empty text, so the fallback is
        transported instead — the proposed ``frame`` (``None`` for finalize) is
        still what gets committed on success, so the slot is really cleared and the
        marker never becomes stored channel state.

        The two channels are fully independent: this method never reads or
        refreshes the other slot's content, so a programmable edit can never
        advance, override, or propose automatic event-tail/session state, and
        an automatic update never touches the programmable frame.
        """
        text = self._compose_channels(account, chat_id, channel=channel, frame=frame)
        # Programmable-only finalize clears the slot: the composed text is empty
        # and Telegram cannot edit/send empty text, so substitute the nonempty
        # terminal marker for transport while ``frame`` (``None``) is still what
        # commits on success (#898 WATCH STOPPED).
        if not text and empty_fallback is not None:
            text = empty_fallback
        # Re-read the resident *inside* the route transaction. A concurrently
        # queued caller may carry the prior id while the first caller has already
        # rotated and persisted a new resident; durable/in-memory state is newer.
        tracked_resident = self._get_resident_task_card(account, chat_id)
        resident_id = tracked_resident or resident_id
        if resident_id:
            # A resident id is only authorized for its exact account+chat slot.
            # Corrupt/cross-bound state must not edit or delete some other chat's
            # message merely because that message id is older than this chat's
            # high-water mark.
            try:
                resident_account, resident_chat_id, _ = self._parse_compound_id(
                    resident_id)
            except Exception:
                return {"status": "error", "error": error}
            if resident_account != account or resident_chat_id != chat_id:
                return {"status": "error", "error": error}
            # Jason #5272/#5273/#5275: the resident card must reside as the chat's
            # LAST message. When we deterministically know a newer message exists
            # below the resident, rotate old-first: probe the exact resident,
            # require confirmed exact-old delete/missing, then send a fresh card so
            # it becomes last — instead of editing it in place (which would leave
            # it stranded above the newer message). When
            # the resident is still the last message, or the latest-message state
            # is unknown/malformed, we fall through to the in-place edit path
            # (#891) and never delete anything on unknown/transient state.
            if self._resident_superseded(account, chat_id, resident_id):
                rotated = self._rotate_task_card_to_latest(
                    account, chat_id, resident_id, text, error=error)
                if rotated.get("status") == "ok":
                    self._set_channel_frame(account, chat_id, channel, frame)
                return rotated
            edit_outcome = self._try_update_progress_message(resident_id, text)
            if edit_outcome == _TASK_CARD_EDIT_OK:
                self._set_channel_frame(account, chat_id, channel, frame)
                return {"status": "ok", "message_id": resident_id}
            if edit_outcome == _TASK_CARD_EDIT_FAILED:
                # Unknown, transient, network, and provider failures do not prove
                # that replacement is safe. Preserve both resident and slot state.
                return {"status": "error", "error": error}
            # The provider confirmed this exact message is missing/uneditable.
            # Confirm exact delete-or-missing before any replacement send.
            recovered = self._recover_task_card_by_replacement(
                account, chat_id, resident_id, text, error=error)
            if recovered.get("status") == "ok":
                self._set_channel_frame(account, chat_id, channel, frame)
            return recovered

        result = self.send_progress_message(account, chat_id, text)
        if result is None or result.get("status") != "sent":
            # No prior resident here, so nothing was deleted. A raised send is a
            # plain failure; an ``indeterminate_send`` means a card may be visible
            # with an unknown id — either way NEVER form/adopt/persist a fake id or
            # delete an unknown card. Surface the indeterminate case explicitly.
            outcome: dict = {"status": "error", "error": error}
            if result is not None and result.get("status") == "indeterminate_send":
                outcome["indeterminate_send"] = True
            return outcome
        new_id = result["message_id"]
        self._set_channel_frame(account, chat_id, channel, frame)
        persisted = self._set_resident_task_card(account, chat_id, new_id)
        outcome = {"status": "ok", "message_id": new_id}
        if not persisted:
            # The sent card remains visible and in-memory current, but the durable
            # resident write was not acknowledged. No prior resident exists here.
            outcome["resident_persist_failed"] = True
        return outcome

    @classmethod
    def _format_programmable_card_text(
        cls, card: dict, *, now: datetime | None = None,
    ) -> str:
        """Render a validated programmable Task Card JSON object to plain text.

        The manager is the single render owner: the public controller sends only
        a validated schema object (never code), and this method turns it into the
        programmable channel frame. Secret redaction runs on every free-text field
        before the render ceiling is applied, mirroring the automatic path. All
        copy is English-only (Jason #7175/#7205).

        A non-empty frame always ends with its own ``Last Updated: ...`` line —
        the instant this programmable frame was accepted/rendered for delivery.
        This is independent of the automatic channel's own ``Last Updated`` line;
        neither channel's timestamp is derived from or advances the other's.
        ``now`` is the render instant (injectable for deterministic tests).
        """
        from lingtai.kernel.trace_redaction import redact_text

        parts: list[str] = []
        title = str(card.get("title", "")).strip()
        if title:
            parts.append(redact_text(title)[:cls._TASK_CARD_REASONING_CAP])
        for line in card.get("lines", []) or []:
            if not isinstance(line, str):
                continue
            rendered = redact_text(line)[:cls._TASK_CARD_REASONING_CAP]
            parts.append(f"• {rendered}")
        footer = str(card.get("footer", "")).strip()
        if footer:
            parts.append(redact_text(footer)[:cls._TASK_CARD_REASONING_CAP])
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
    _TASK_CARD_EVENT_WINDOW = 10
    _TASK_CARD_EVENT_POLL_INTERVAL = 1.0
    _TASK_CARD_EVENT_TAIL_CHUNK = 65536
    _TASK_CARD_EVENT_REASONING_CAP = 300
    _TASK_CARD_EVENT_TEXT_CAP = 500
    _TASK_CARD_MAX_EVENTS_PER_CALL = 24
    # The same quiet horizontal rule used by the TUI between provider calls.
    _TASK_CARD_API_CALL_DIVIDER = TaskCardResident.API_CALL_DIVIDER

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
            return dict(metadata) if isinstance(metadata, dict) else None

    @staticmethod
    def _project_agent_text_event(event: dict) -> dict | None:
        """Project only canonical public agent text, never hidden internals.

        ``diary`` is the kernel's public response-text event. Thinking, system
        prompts, tool args/results, and runtime diagnostics are not accepted.
        """
        if event.get("type") != "diary":
            return None
        if event.get("hidden") is True or event.get("visibility") not in (None, "public"):
            return None
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        from lingtai.kernel.trace_redaction import redact_text
        text = redact_text(text).strip()
        cap = TelegramManager._TASK_CARD_EVENT_TEXT_CAP
        if len(text) > cap:
            text = text[: cap - 1] + "…"
        return {"kind": "text", "text": text}

    @staticmethod
    def _project_task_card_event(event: dict) -> dict | None:
        text = TelegramManager._project_agent_text_event(event)
        if text is not None:
            return text
        row = TelegramManager._project_tool_call_row(event)
        if row is not None:
            row["kind"] = "tool"
            return row
        return None

    @staticmethod
    def _event_group_id(event: dict, fallback: int) -> str:
        value = event.get("api_call_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Legacy rows predate the provider id. Treat each such public event as
        # one synthetic call so old persisted numeric windows remain readable.
        return f"legacy:{fallback}"

    def _group_task_card_events(self, projected: list[tuple[dict, dict]]) -> list[dict]:
        groups: list[dict] = []
        by_id: dict[str, dict] = {}
        for index, (event, row) in enumerate(projected):
            group_id = self._event_group_id(event, index)
            group = by_id.get(group_id)
            if group is None:
                group = {"api_call_id": group_id, "events": []}
                by_id[group_id] = group
                groups.append(group)
            events = group["events"]
            if len(events) < self._TASK_CARD_MAX_EVENTS_PER_CALL:
                events.append(row)
        return groups[-self._TASK_CARD_EVENT_WINDOW:]

    @staticmethod
    def _flatten_task_card_groups(
        groups: list[dict], *, include_group_id: bool = False,
    ) -> list[dict]:
        rows: list[dict] = []
        for group in groups:
            group_id = group.get("api_call_id")
            for event in group.get("events", []):
                row = dict(event)
                if include_group_id:
                    row["group_id"] = group_id
                else:
                    # Keep the long-standing public helper shape for tool rows;
                    # grouping metadata remains internal to the resident renderer.
                    row.pop("group_id", None)
                if row.get("kind") == "tool":
                    row.pop("kind", None)
                rows.append(row)
        return rows

    @staticmethod
    def _project_tool_call_row(event: dict) -> dict | None:
        """Extract the fixed safe-field allowlist from one ``tool_call`` event.

        Returns ``None`` (fail-closed) when the event does not carry the
        expected shape — a malformed row is skipped exactly like a
        non-whitelisted one, never partially trusted.
        """
        if event.get("type") != "tool_call":
            return None
        tool_name = event.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            return None
        tool_args = event.get("tool_args")
        if not isinstance(tool_args, dict):
            return None
        reasoning = tool_args.get("_reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = ""
        from lingtai.kernel.trace_redaction import redact_text

        reasoning = redact_text(reasoning)
        cap = TelegramManager._TASK_CARD_EVENT_REASONING_CAP
        if len(reasoning) > cap:
            # The ellipsis itself must stay inside the cap, not extend past it.
            reasoning = reasoning[:cap - 1] + "…"
        row = {"tool": tool_name, "reasoning": reasoning}
        started_at = TelegramManager._format_task_card_row_timestamp(event.get("ts"))
        if started_at:
            row["started_at"] = started_at
        return row

    @staticmethod
    def _project_final_carrier_metadata(event: dict) -> dict | None:
        """Project current session telemetry from one final-carrier event.

        ``agent_meta`` is a whole current snapshot: only the newest
        ``notification_block_injected`` carrier is consulted, and only its
        ``agent_state.token_usage.session`` fields cross into the Task Card.
        An empty dict is a recognized-but-malformed carrier, so it clears an
        older snapshot instead of leaving stale telemetry visible. ``None``
        means this event is not a final carrier and must not change state.
        """
        if event.get("type") != "notification_block_injected":
            return None
        envelope = event.get("_meta")
        if not isinstance(envelope, dict):
            return {}
        agent_meta = envelope.get("agent_meta")
        if not isinstance(agent_meta, dict):
            return {}
        state = agent_meta.get("agent_state")
        if not isinstance(state, dict):
            return {}
        token_usage = state.get("token_usage")
        if not isinstance(token_usage, dict):
            return {}
        session = token_usage.get("session")
        if not isinstance(session, dict):
            return {}
        supported = (
            "session_cache_rate",
            "cache_miss_tokens",
            "cache_miss_budget",
            "api_calls",
            "context_tokens",
            "context_window",
            "context_usage",
        )
        return {key: session[key] for key in supported if key in session}

    @staticmethod
    def _format_task_card_row_timestamp(ts: object) -> str:
        """Convert an event's canonical epoch ``ts`` to the row stamp shape.

        Mirrors ``_format_task_card_current_time``'s ``HH:MM:SS UTC±HH``
        format so a row's own stamp and the render-time line read
        consistently. Missing, non-numeric (incl. ``bool``), non-finite, or
        out-of-range ``ts`` values safely resolve to ``""`` (row renders with
        no inline stamp) rather than crashing or fabricating a timestamp.
        """
        if type(ts) not in (int, float):
            return ""
        if isinstance(ts, float) and not math.isfinite(ts):
            return ""
        try:
            local = datetime.fromtimestamp(ts).astimezone()
        except (OverflowError, OSError, ValueError):
            return ""
        return _format_task_card_current_time(local)

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
                self._task_card_event_groups = []
                self._task_card_event_metadata = None
            return
        rows, offset, metadata = result
        with self._task_card_event_lock:
            self._task_card_event_path = path
            self._task_card_event_offset = offset
            self._task_card_event_size = stat.st_size
            self._task_card_event_inode = getattr(stat, "st_ino", None)
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
        line = raw.strip()
        if not line:
            return None
        try:
            event = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return event if isinstance(event, dict) else None

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
            tracked_inode = self._task_card_event_inode

        current_inode = getattr(stat, "st_ino", None)
        truncated = stat.st_size < offset or (
            tracked_inode is not None
            and current_inode is not None
            and current_inode != tracked_inode
        )
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
        for raw in complete.split(b"\n"):
            event = self._decode_event_line(raw)
            if event is None:
                continue
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
        return bool(projected_events) or metadata_changed

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
        groups = self._task_card_event_groups_snapshot()[-normal_rows:]
        rows: list[dict] = []
        for group in groups:
            rows.append({"kind": "divider", "text": self._TASK_CARD_API_CALL_DIVIDER})
            rows.extend(group.get("events", []))
        automatic = self._format_task_card_text(
            "", "", "", rows=rows,
            metadata=self._task_card_event_metadata_snapshot(),
            normal_rows=normal_rows,
        )
        # Telegram transport and the resident contract share one bounded card
        # ceiling.  Group selection counts calls; this final cap truncates only
        # inside the selected group rather than selecting extra tool rows.
        if len(automatic) > self._TASK_CARD_TEXT_LIMIT:
            automatic = automatic[: self._TASK_CARD_TEXT_LIMIT]
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

    def _handle_task_card_update(self, args: dict) -> dict:
        """Private internal action — internally-driven Task Card projection
        (the kernel automatic driver and the Telegram-owned programmable controller).

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
        """Delete/missing-confirm the exact old resident, then send and persist."""
        delete_outcome = self._delete_task_card_message_outcome(stale_id)
        if delete_outcome == _TASK_CARD_DELETE_FAILED:
            return {
                "status": "error",
                "error": error,
                "stale_delete_failed": True,
            }

        result = self.send_progress_message(account, chat_id, text)
        if result is None or result.get("status") != "sent":
            # Replacement send failed or returned no usable id. Preserve the truth
            # that the exact old resident was already deleted (may leave zero
            # cards); an ``indeterminate_send`` additionally means a new card may be
            # visible with an unknown id. Fail closed either way — no fake id is
            # formed, adopted, persisted, or deleted.
            outcome: dict = {"status": "error", "error": error}
            if delete_outcome == _TASK_CARD_DELETE_OK:
                outcome["old_resident_deleted"] = True
            if result is not None and result.get("status") == "indeterminate_send":
                outcome["indeterminate_send"] = True
            return outcome

        new_id = result["message_id"]
        persisted = self._set_resident_task_card(account, chat_id, new_id)
        outcome = {"status": "ok", "message_id": new_id}
        if not persisted:
            # The new card is the only visible tracked candidate in this process;
            # surface the durability gap so callers never claim a clean result.
            outcome["resident_persist_failed"] = True
        return outcome

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

    def _rotate_task_card_to_latest(
        self, account: str, chat_id: int, stale_id: str, text: str, *, error: str,
    ) -> dict:
        """Probe the exact old resident, then delete/missing-confirm before send.

        When this process still knows the last committed render, a same-content
        edit/no-op is the remote existence probe required before injecting another
        card. A cold process may know only the persisted resident id; in that case
        the exact delete itself is the existence/removal probe. Its success or
        explicit not-found must be confirmed before a new send. Unknown probe or
        delete failures abort without sending.

        Deleting first intentionally chooses the hard at-most-one direction: a
        later send failure can leave zero resident cards, reported by
        ``old_resident_deleted``, but tracked rotation never deliberately creates
        two visible cards. Unknown historical orphans remain out of scope.
        """
        committed_text = self._compose_channels(account, chat_id)
        if committed_text:
            probe_outcome = self._try_update_progress_message(
                stale_id, committed_text
            )
            if probe_outcome == _TASK_CARD_EDIT_FAILED:
                return {"status": "error", "error": error}
        return self._replace_task_card_after_probe(
            account, chat_id, stale_id, text, error=error
        )

    def _get_resident_task_card(self, account: str, chat_id: int) -> str | None:
        """Read the persisted resident card id for (account, chat); fail-open."""
        try:
            return self._service.get_account(account).get_task_card(chat_id)
        except Exception as e:
            log.debug("Failed to read resident task card: %s", e)
            return None

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

        ``missing`` is returned only for Telegram's exact not-found response; all
        malformed ids, unknown provider responses, network errors, and permission
        failures are ``failed`` so callers cannot inject a second card on a guess.
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
        """Update or clear the programmable channel of the resident card.

        The programmable channel is the public ``task_card`` controller's output.
        It shares the tracked resident target and composes alongside the automatic
        channel (Jason #7258/#7259): updating it replaces only the programmable
        frame; ``finalize`` clears only the programmable frame and leaves the
        automatic channel — and the message itself — intact.

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
        to the same resident id; Telegram only ever receives validated data.
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
        rows, so an extreme operator-set ``LINGTAI_TASK_CARD_MAX_TOOL_ROWS`` can
        still produce a render above the budget (and above Telegram's transport
        limit).  See ``_format_rows_task_card_text``.
        """
        if rows is None:
            return cls._format_scalar_task_card_text(tool, action, reasoning)
        return cls._format_rows_task_card_text(
            rows, metadata=metadata, normal_rows=normal_rows, now=now)

    @classmethod
    def _format_scalar_task_card_text(cls, tool: str, action: str, reasoning: str) -> str:
        from lingtai.kernel.trace_redaction import redact_text

        redacted = redact_text(reasoning)
        if len(redacted) > cls._TASK_CARD_REASONING_CAP:
            excerpt = redacted[:cls._TASK_CARD_REASONING_CAP] + "…"
        else:
            excerpt = redacted
        label = f"{tool}.{action}" if action else tool
        if label:
            return f"{cls._TASK_CARD_HEADER}\n{label}: {excerpt}"
        return f"{cls._TASK_CARD_HEADER}\n{excerpt}" if excerpt else cls._TASK_CARD_HEADER

    @staticmethod
    def _format_task_card_count(value: object) -> str | None:
        """Format a non-negative count compactly without float overflow."""
        if type(value) is not int or value < 0:
            return None
        for threshold, suffix in (
            (1_000_000_000_000, "T"),
            (1_000_000_000, "B"),
            (1_000_000, "M"),
            (1_000, "k"),
        ):
            if value >= threshold:
                tenths = (value * 10 + threshold // 2) // threshold
                if suffix == "T":
                    tenths = min(tenths, 9_999)
                return f"{tenths // 10}.{tenths % 10}{suffix}"
        return str(value)

    @classmethod
    def _format_task_card_metadata(cls, metadata: object) -> list[str]:
        """Render at most two compact session lines within a 150-char budget."""
        if not isinstance(metadata, dict):
            return []

        session_parts: list[str] = []
        cache_rate = metadata.get("session_cache_rate")
        if (
            type(cache_rate) in {int, float}
            and not isinstance(cache_rate, bool)
            and 0 <= cache_rate <= 1
        ):
            session_parts.append(f"cache {float(cache_rate):.1%}")
        miss = cls._format_task_card_count(metadata.get("cache_miss_tokens"))
        budget = cls._format_task_card_count(metadata.get("cache_miss_budget"))
        if miss is not None:
            session_parts.append(f"miss {miss}/{budget}" if budget is not None else f"miss {miss}")
        calls = cls._format_task_card_count(metadata.get("api_calls"))
        if calls is not None:
            session_parts.append(f"calls {calls}")

        context_parts: list[str] = []
        context = cls._format_task_card_count(metadata.get("context_tokens"))
        window = cls._format_task_card_count(metadata.get("context_window"))
        if context is not None:
            context_parts.append(f"{context}/{window}" if window is not None else context)
        usage = metadata.get("context_usage")
        if (
            type(usage) in {int, float}
            and not isinstance(usage, bool)
            and 0 <= usage <= 1
        ):
            context_parts.append(f"{float(usage):.0%}")

        lines: list[str] = []
        if session_parts:
            lines.append("session · " + " · ".join(session_parts))
        if context_parts:
            lines.append("ctx · " + " · ".join(context_parts))
        lines = lines[:_TASK_CARD_METADATA_MAX_LINES]
        if not lines:
            return []

        joined = "\n".join(lines)
        if len(joined) <= _TASK_CARD_METADATA_MAX_CHARS:
            return lines
        # The field set is bounded, but keep the UI contract deterministic even
        # for pathological numeric inputs: preserve line 1, then fit line 2.
        first = lines[0][:_TASK_CARD_METADATA_MAX_CHARS]
        remaining = _TASK_CARD_METADATA_MAX_CHARS - len(first) - 1
        return [first] if remaining <= 0 or len(lines) == 1 else [first, lines[1][:remaining]]

    @classmethod
    def _format_rows_task_card_text(
        cls, rows: list, *, metadata: dict | None = None,
        normal_rows: int = _TASK_CARD_DEFAULT_NORMAL_ROWS,
        now: datetime | None = None,
    ) -> str:
        from lingtai.kernel.trace_redaction import redact_text

        footer = _task_card_footer(normal_rows)

        # Split tool rows (redacted, capped reasoning) from sanitized API-error
        # rows (fixed machine summary, no reasoning to redact).  Redact every tool
        # row's reasoning up front (before any excerpt/trim) and compute the
        # per-row reasoning-excerpt budget so the *reasoning* stays under the
        # ceiling while keeping every row visible.  NOTE: the budget bounds
        # excerpt shrinkage only, not the total render — fixed per-row scaffolding
        # (below) is unbounded in row count, so an extreme operator-set N can
        # still exceed the ceiling and Telegram's transport limit.  Each row
        # carries its own captured ``started_at`` inline; malformed/missing
        # values degrade to an empty suffix rather than raising.
        tool_prepared: list[tuple[int, str, str, str, bool, str]] = []
        text_prepared: list[tuple[int, str]] = []
        api_prepared: list[tuple[int, str]] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            if kind == "divider":
                api_prepared.append((idx, cls._TASK_CARD_API_CALL_DIVIDER))
                continue
            if kind == "text":
                text = redact_text(str(row.get("text", ""))).strip()
                if text:
                    text_prepared.append((idx, text[:cls._TASK_CARD_EVENT_TEXT_CAP]))
                continue
            if kind == "api_error":
                api_prepared.append((idx, cls._format_api_error_line(row)))
                continue
            tool = str(row.get("tool", ""))
            action = str(row.get("tool_action", ""))
            label = f"{tool}.{action}" if action else tool
            redacted = redact_text(str(row.get("reasoning", "")))
            elapsed = cls._format_elapsed(row.get("elapsed_s", 0))
            done = bool(row.get("done", False))
            started_at = row.get("started_at", "")
            started_at = started_at if isinstance(started_at, str) else ""
            tool_prepared.append((idx, label, redacted, elapsed, done, started_at))

        metadata_lines = cls._format_task_card_metadata(metadata)
        # The bottom time line always reflects the render instant, never a
        # row's own start instant, and is present even for an empty card.
        time_line = f"{_TASK_CARD_TIME_PREFIX}{cls._task_card_render_time(now)}"
        if not tool_prepared and not text_prepared and not api_prepared:
            lines = [cls._TASK_CARD_HEADER, "", footer]
            lines.extend(metadata_lines)
            lines.append(time_line)
            return "\n".join(lines)

        # Budget the reasoning excerpts against the render ceiling.  The fixed
        # cost is the header + footer + their newlines plus the *actual*
        # non-reasoning scaffolding of every row (marker, label, elapsed suffix,
        # own timestamp suffix, and each row's newline) and the time line —
        # measured, not a flat estimate.  ``fixed`` is subtracted from the
        # ceiling so the reasoning excerpts shrink first; when ``fixed`` itself
        # exceeds the ceiling (many rows and/or long labels), ``budget`` goes
        # negative and ``per_row_cap`` floors at 0 — every row still renders
        # with an empty excerpt, and the scaffolding alone can then exceed
        # ``_TASK_CARD_TEXT_LIMIT`` (and Telegram's transport limit).  We
        # deliberately do NOT drop rows or truncate the final string to fit:
        # the operator asked for N rows, so N rows are shown.  What remains of
        # the budget is shared evenly across tool rows so no single row crowds
        # the others out.
        api_scaffold = sum(len(line) + 1 for _, line in api_prepared)
        text_scaffold = sum(len(text) + 4 for _, text in text_prepared)
        tool_scaffold = 0
        for _, label, _redacted, elapsed, done, started_at in tool_prepared:
            marker = "✓ " if done else "• "
            prefix = f"{marker}{label}: " if label else marker
            stamp_suffix = f" · {started_at}" if started_at else ""
            # +1 newline, +1 for a possible truncation ellipsis (conservative).
            tool_scaffold += len(prefix) + len(f" ({elapsed}s)") + len(stamp_suffix) + 2
        fixed = (
            len(cls._TASK_CARD_HEADER) + 1  # header + newline
            + 1                              # blank line before footer
            + len(footer)
            + sum(len(line) + 1 for line in metadata_lines)
            + len(time_line) + 1             # time line + its newline
            + api_scaffold + text_scaffold + tool_scaffold
        )
        budget = cls._TASK_CARD_TEXT_LIMIT - fixed
        divisor = max(1, len(tool_prepared) + len(text_prepared))
        # Floor at 0 (not 16) so an over-budget batch trims reasoning to empty
        # excerpts (the most the excerpt budget can do); the remaining scaffolding
        # may still exceed the ceiling for extreme N — we do not truncate or drop
        # rows to force a fit.  A healthy card keeps a generous per-row excerpt.
        per_row_cap = max(0, min(cls._TASK_CARD_REASONING_CAP, budget // divisor))

        # Render in original row order so tool and API rows interleave correctly.
        by_idx: dict[int, str] = {}
        for idx, label, redacted, elapsed, done, started_at in tool_prepared:
            excerpt = redacted[:per_row_cap] + "…" if len(redacted) > per_row_cap else redacted
            marker = "✓ " if done else "• "
            prefix = f"{marker}{label}: " if label else marker
            stamp_suffix = f" · {started_at}" if started_at else ""
            by_idx[idx] = f"{prefix}{excerpt} ({elapsed}s){stamp_suffix}"
        for idx, text in text_prepared:
            excerpt = text[:per_row_cap] + "…" if len(text) > per_row_cap else text
            by_idx[idx] = f"• {excerpt}"
        for idx, line in api_prepared:
            by_idx[idx] = line

        lines = [cls._TASK_CARD_HEADER]
        lines.extend(by_idx[i] for i in sorted(by_idx))
        lines.append("")
        lines.append(footer)
        lines.extend(metadata_lines)
        lines.append(time_line)
        return "\n".join(lines)

    @staticmethod
    def _task_card_render_time(now: datetime | None) -> str:
        """Resolve the render-time stamp, defaulting to the real local instant."""
        if now is None:
            now = datetime.now().astimezone()
        return _format_task_card_current_time(now)

    @staticmethod
    def _task_card_machine_identifier(value: object, *, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > limit:
            return None
        safe_punctuation = frozenset("._:/-")
        if not all(
            ch.isascii() and (ch.isalnum() or ch in safe_punctuation)
            for ch in value
        ):
            return None
        return value

    @classmethod
    def _format_api_error_line(cls, row: dict) -> str:
        """Render a sanitized LLM/provider API-error row.

        Shows only bounded machine identifiers supplied by the kernel (exception
        type, public provider/model, valid HTTP status, allow-listed code) plus
        lifecycle state. Opaque external identifiers and raw exception text are
        deliberately absent, so there is no free-form field to leak.
        """
        state = row.get("state")
        parts = ["API error"]
        error_type = cls._task_card_machine_identifier(row.get("error_type"), limit=48)
        if error_type is not None:
            parts.append(error_type)
        provider = cls._task_card_machine_identifier(row.get("provider"), limit=48)
        model = cls._task_card_machine_identifier(row.get("model"), limit=80)
        if provider is not None and model is not None:
            parts.append(f"{provider}/{model}")
        elif provider is not None or model is not None:
            parts.append(provider or model or "")
        status = row.get("status")
        if type(status) is int and 100 <= status <= 599:
            parts.append(f"HTTP {status}")
        code = row.get("code")
        if isinstance(code, str) and code:
            parts.append(code)
        summary = " · ".join(parts)

        if state == "recovered":
            return f"✓ {summary} · recovered"
        if state == "error":
            return f"⚠️ {summary} · failed"
        # retrying (default)
        attempt = row.get("attempt")
        max_attempts = row.get("max_attempts")
        if (
            type(attempt) is int
            and type(max_attempts) is int
            and attempt > 0
            and max_attempts > 0
        ):
            return f"⚠️ {summary} · retrying {attempt}/{max_attempts}"
        if type(attempt) is int and attempt > 0:
            return f"⚠️ {summary} · retrying (attempt {attempt})"
        return f"⚠️ {summary} · retrying"

    @staticmethod
    def _format_elapsed(value: object) -> str:
        """Render a row's elapsed seconds as whole seconds (no decimal point).

        The heartbeat still ticks every 0.5s, but elapsed is floored to whole
        seconds by the kernel, so half-second frames read ``0s, 0s, 1s, 1s, 2s``.
        This coerces + floors defensively (a float payload is floored, junk
        degrades to ``0``) so the render never raises.
        """
        try:
            return str(max(0, int(float(value))))
        except (TypeError, ValueError):
            return "0"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    _PARSE_MODES = {"HTML", "MarkdownV2", "Markdown"}

    @staticmethod
    def _normalize_parse_mode(value: Any) -> Any:
        """Treat an empty parse_mode as omitted/plain text.

        Some tool callers serialize absent optional string fields as ``""``.
        Telegram Bot API itself omits parse_mode for plain text, so normalize
        the empty string before validation and payload persistence.
        """
        if value == "":
            return None
        return value

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

    def _rich_text_options(self, args: dict) -> tuple[dict[str, Any], str | None]:
        """Extract Bot API rich text options for text messages from tool args.

        Returns (options, error). When nothing relevant is supplied the
        options dict is empty, so existing plain-text callers behave exactly
        as before.
        """
        opts: dict[str, Any] = {}
        parse_mode = self._normalize_parse_mode(args.get("parse_mode"))
        if parse_mode is not None:
            if parse_mode not in self._PARSE_MODES:
                return {}, "parse_mode must be one of: HTML, MarkdownV2, Markdown"
            opts["parse_mode"] = parse_mode
        if args.get("entities") is not None:
            opts["entities"] = args.get("entities")
        if args.get("link_preview_options") is not None:
            opts["link_preview_options"] = args.get("link_preview_options")
        if args.get("disable_web_page_preview") is not None:
            opts["disable_web_page_preview"] = bool(args.get("disable_web_page_preview"))
        return opts, None

    def _caption_options(self, args: dict) -> tuple[dict[str, Any], str | None]:
        """Extract Bot API rich caption options for media sends from tool args.

        If ``caption_entities`` is omitted but ``entities`` is supplied, the
        latter is treated as caption entities for convenience.
        """
        opts: dict[str, Any] = {}
        parse_mode = self._normalize_parse_mode(args.get("parse_mode"))
        if parse_mode is not None:
            if parse_mode not in self._PARSE_MODES:
                return {}, "parse_mode must be one of: HTML, MarkdownV2, Markdown"
            opts["parse_mode"] = parse_mode
        caption_entities = args.get("caption_entities")
        if caption_entities is None:
            caption_entities = args.get("entities")
        if caption_entities is not None:
            opts["caption_entities"] = caption_entities
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
            "parse_mode": self._normalize_parse_mode(args.get("parse_mode")),
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
            cleaned.append({
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
            })

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
            "parse_mode": self._normalize_parse_mode(args.get("parse_mode")),
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
                msg.get("text", ""),
            ])
            if pattern.search(searchable):
                matches.append({
                    "id": msg.get("id"),
                    "from": msg.get("from"),
                    "date": msg.get("date"),
                    "text": msg.get("text"),
                    "taskcard": taskcard,
                })

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
        rich_text_options, rich_text_error = self._rich_text_options(args)
        caption_options, caption_error = self._caption_options(args)
        if rich_text_error or caption_error:
            return {"error": rich_text_error or caption_error}
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

        edit_options = caption_options if is_caption else rich_text_options
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
