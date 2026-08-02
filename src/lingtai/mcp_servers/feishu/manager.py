"""FeishuManager — tool dispatch + filesystem persistence.

Storage layout:
    working_dir/feishu/{alias}/inbox/{uuid}/message.json
    working_dir/feishu/{alias}/sent/{uuid}/message.json
    working_dir/feishu/{alias}/contacts.json   open_id -> {alias, name, chat_id}
    working_dir/feishu/{alias}/read.json       list of read compound IDs
    working_dir/feishu/{alias}/state.json      bot_info

Compound message ID format: {alias}:{chat_id}:{feishu_message_id}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

from lingtai.kernel._frontmatter import strip_frontmatter
from lingtai.kernel.trace_redaction import redact_text
from lingtai.mcp_servers.local_commands import (
    LocalCommandCore,
    TaskCardSettingsPort,
)
from lingtai.mcp_servers.task_card import (
    TaskCardEventProjection,
    TaskCardResident,
    TaskCardResidentTransport,
    TaskCardRoute,
)

from .. import _skill
from . import _family
from ._errors import FeishuOperationError, failure_result
from .account import (
    FeishuInboundCardAction,
    FeishuInboundChannelEvent,
    FeishuInboundEvent,
)
from .control_cards import FeishuControlCards, FeishuControlEventStore
from .task_card import (
    FeishuProgrammableTaskCardPoller,
    FeishuTaskCardJournal,
    FeishuTaskCardStore,
)

if TYPE_CHECKING:
    from .service import FeishuService

log = logging.getLogger(__name__)


def _load_notification_header_template() -> str:
    text = resources.files(__package__).joinpath("notification_header.md").read_text(
        encoding="utf-8"
    )
    return strip_frontmatter(text)


_NOTIFICATION_HEADER_TEMPLATE = _load_notification_header_template()

# Bundled usage manual (skill format) — SKILL.md ships in this package folder.
# action='manual' reads the full body; the YAML frontmatter name/description are
# injected into the tool schema as a progressive-disclosure catalog entry.
_SKILL_NAME = "feishu-mcp-manual"
_SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH = _skill.load_skill(__package__)

# Emoji reactions for different states
# Feishu supported emoji types: OK, THUMBSUP, SMILE, HEART, THANKS, etc.
REACTION_SEEN = "OK"        # Message received — "got it"
REACTION_DONE = "THUMBSUP"  # Response sent — "done"

# Conversation-context window for LICC notification previews/metadata: how many
# recent messages (inbox + sent, one chat) ride along with an incoming-message
# event.  The same window feeds both the markdown preview body and the
# structured `recent_messages` metadata, and it matches the kernel's
# NOTIFICATION_PERSISTENT_FEISHU_MIN_CONTEXT seed/delta boundary.
_CONVERSATION_PREVIEW_MESSAGES = 10
# Per-message text cap inside structured `recent_messages` items.  Structured
# metadata must stay within the kernel inbox's 20k-JSON structured-field cap
# (inbox._PREVIEW_STRUCTURED_META_JSON_CAP) or it is silently dropped; capped
# messages carry text_truncated=true so the agent knows to feishu.read for the
# exact producer state.
_STRUCTURED_MESSAGE_TEXT_CAP = 500
_STRUCTURED_MESSAGE_MENTION_CAP = 20
_STRUCTURED_MESSAGE_ATTACHMENT_CAP = 8
_STRUCTURED_ATTACHMENT_PATH_CAP = 1024
SYNTHETIC_EVENTS_CHAT_ID = "events"
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600

_ATTACHMENT_TYPES = {"image", "file", "audio", "video", "sticker"}
_ATTACHMENT_SUFFIXES = {
    "image": ".jpg",
    "file": ".bin",
    "audio": ".ogg",
    "video": ".mp4",
    "sticker": ".png",
}
_UNSAFE_ATTACHMENT_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
_OUTBOUND_CONTENT_FIELDS = {
    "text": "text",
    "markdown": "markdown",
    "post": "post",
}
_OUTBOUND_MEDIA_TYPES = {"image", "file", "audio", "video"}
_EDITABLE_CONTENT_TYPES = frozenset({*_OUTBOUND_CONTENT_FIELDS, "card"})
_PLACEHOLDER_CONTENT_TYPES = frozenset(_OUTBOUND_CONTENT_FIELDS)


def _post_preview(post: dict[str, Any]) -> str:
    """Build a readable persisted preview without rewriting the post AST."""
    documents = [post] if "content" in post else [
        value for value in post.values() if isinstance(value, dict)
    ]
    parts: list[str] = []
    for document in documents[:1]:
        title = document.get("title")
        if isinstance(title, str) and title:
            parts.append(title)
        rows = document.get("content") or document.get("content_v2") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            elements = row if isinstance(row, list) else [row]
            line: list[str] = []
            for element in elements:
                if not isinstance(element, dict):
                    continue
                value = element.get("text")
                if not isinstance(value, str):
                    value = element.get("content")
                if isinstance(value, str) and value:
                    line.append(value)
            if line:
                parts.append("".join(line))
    return "\n".join(parts)


def _card_preview(card: dict[str, Any]) -> str:
    """Extract visible CardKit text without traversing callback values."""
    parts: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("text", "content"):
            text = value.get(key)
            if isinstance(text, str) and text and text not in parts[-1:]:
                parts.append(text)
        for key in (
            "header", "title", "body", "elements", "columns", "items", "text",
        ):
            visit(value.get(key))

    visit(card)
    return "\n".join(parts) or "[interactive card]"


def _native_progress_card(
    content: dict[str, Any], preview: str,
) -> tuple[dict[str, Any], str]:
    """Wrap one meaningful phase update in a native schema-2.0 card."""
    phase = (
        content.get("markdown")
        if content.get("type") == "markdown"
        else preview
    )
    if not isinstance(phase, str) or not phase.strip():
        phase = "…"
    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "LingTai"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": f"⏳ {phase.strip()}"},
            ],
        },
    }
    return card, _card_preview(card)


def _automatic_task_card(frame: str) -> dict[str, Any]:
    """Render the shared resident frame as one updateable Feishu card."""
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "LingTai Task Card"},
            "template": "blue",
        },
        "body": {"elements": [{"tag": "markdown", "content": frame}]},
    }


def _normalize_outbound_content(
    args: dict[str, Any], *, editable: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Validate ``text XOR content`` and return record/SDK/preview/wire type."""
    has_text = "text" in args
    has_content = "content" in args
    if has_text == has_content:
        raise ValueError("exactly one of text or content is required")

    if has_text:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("text is required")
        content = {"type": "text", "text": text}
        return content, {"text": text}, text, "text"

    raw = args.get("content")
    if not isinstance(raw, dict):
        raise ValueError("content must be an object")
    content_type = raw.get("type")
    if not isinstance(content_type, str):
        raise ValueError("content.type must be a supported outbound type")
    if editable and content_type not in _EDITABLE_CONTENT_TYPES:
        raise ValueError("edit only supports text, markdown, post, or card content")
    field = _OUTBOUND_CONTENT_FIELDS.get(content_type)
    if field is not None:
        if set(raw) != {"type", field}:
            raise ValueError("content must be one strict text, markdown, or post value")
        value = raw.get(field)
        if content_type in {"text", "markdown"}:
            if not isinstance(value, str) or not value:
                raise ValueError(f"content.{field} is required")
            preview = value
        else:
            if not isinstance(value, dict) or not value:
                raise ValueError("content.post must be a non-empty object")
            preview = _post_preview(value)
        content = dict(raw)
        sdk_message = {field: value}
        wire_type = "text" if content_type == "text" else "post"
        return content, sdk_message, preview, wire_type

    if content_type == "card":
        if set(raw) != {"type", "card"}:
            raise ValueError("content must be one strict card value")
        card = raw.get("card")
        if (
            not isinstance(card, dict)
            or card.get("schema") != "2.0"
            or len(card) < 2
        ):
            raise ValueError("content.card must be a non-empty schema 2.0 card")
        return dict(raw), {"card": card}, _card_preview(card), "interactive"

    if content_type in _OUTBOUND_MEDIA_TYPES:
        optional = {"caption"} if content_type in {"image", "video"} else set()
        if content_type == "file":
            optional.add("file_name")
        if set(raw) - {"type", "source", *optional} or "source" not in raw:
            raise ValueError(f"invalid {content_type} content fields")
        source = raw.get("source")
        if not isinstance(source, dict) or len(source) != 2:
            raise ValueError("content.source must be one strict path or key value")
        source_type = source.get("type")
        source_field = "path" if source_type == "path" else "key"
        if source_type not in {"path", "key"} or set(source) != {
            "type", source_field,
        }:
            raise ValueError("content.source must be one strict path or key value")
        source_value = source.get(source_field)
        if not isinstance(source_value, str) or not source_value:
            raise ValueError(f"content.source.{source_field} is required")
        if source_value.lower().startswith(("http://", "https://")):
            raise ValueError("content.source does not support URLs")
        if source_type == "path":
            path = Path(source_value)
            if not path.is_absolute():
                raise ValueError("content.source.path must be absolute")
            if not path.is_file():
                raise ValueError("content.source.path must name a readable file")
        caption = raw.get("caption")
        if caption is not None and not isinstance(caption, str):
            raise ValueError("content.caption must be a string")
        file_name = raw.get("file_name")
        if file_name is not None and (
            not isinstance(file_name, str) or not file_name
        ):
            raise ValueError("content.file_name must be a non-empty string")
        sdk_spec: dict[str, Any] = {"source": source_value}
        if file_name:
            sdk_spec["file_name"] = file_name
        sdk_message = {content_type: sdk_spec}
        if caption:
            sdk_message["caption"] = caption
        filename = file_name or (
            Path(source_value).name if source_type == "path" else ""
        )
        preview = caption or (
            f"[{content_type}: {filename}]" if filename else f"[{content_type}]"
        )
        wire_type = (
            "post" if caption and content_type in {"image", "video"}
            else content_type
        )
        return dict(raw), sdk_message, preview, wire_type

    scalar_fields = {
        "share_chat": "chat_id",
        "share_user": "user_id",
        "sticker": "file_key",
    }
    scalar_field = scalar_fields.get(content_type)
    if scalar_field is None or set(raw) != {"type", scalar_field}:
        raise ValueError("content must be one strict supported outbound value")
    scalar_value = raw.get(scalar_field)
    if not isinstance(scalar_value, str) or not scalar_value:
        raise ValueError(f"content.{scalar_field} is required")
    sdk_message = {content_type: {scalar_field: scalar_value}}
    preview = {
        "share_chat": "[shared chat]",
        "share_user": "[shared user]",
        "sticker": "[sticker]",
    }[content_type]
    return dict(raw), sdk_message, preview, content_type


def _outbound_media_summary(content: dict[str, Any]) -> dict[str, Any] | None:
    content_type = content.get("type")
    if content_type not in _OUTBOUND_MEDIA_TYPES | {"sticker"}:
        return None
    summary: dict[str, Any] = {"type": content_type}
    source = content.get("source")
    if isinstance(source, dict) and source.get("type") == "path":
        path = Path(source.get("path") or "")
        summary["filename"] = content.get("file_name") or path.name
        try:
            summary["size"] = path.stat().st_size
        except OSError:
            pass
    elif content.get("file_name"):
        summary["filename"] = content["file_name"]
    return summary


def _normalized_content_payload(content: object, message_type: str) -> dict:
    """Project one SDK content union without duplicating its raw wire body."""
    if is_dataclass(content):
        value = asdict(content)
    elif isinstance(content, dict):
        value = dict(content)
    else:
        value = {}
    value.pop("raw", None)
    value.setdefault("kind", message_type or "unknown")
    return value


def _normalized_mentions_payload(mentions: object) -> list[dict]:
    result: list[dict] = []
    if not isinstance(mentions, (list, tuple)):
        return result
    for mention in mentions:
        if is_dataclass(mention):
            item = asdict(mention)
        elif isinstance(mention, dict):
            item = dict(mention)
        else:
            item = {
                key: getattr(mention, key, None)
                for key in (
                    "key", "open_id", "union_id", "user_id", "name",
                    "is_bot", "tenant_key",
                )
            }
        result.append({key: value for key, value in item.items() if value is not None})
    return result


def _legacy_envelope_payload(value: object) -> object:
    """JSON-safe fallback for pre-normalized callback fixtures/callers."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _legacy_envelope_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_legacy_envelope_payload(item) for item in value]
    if is_dataclass(value):
        return _legacy_envelope_payload(asdict(value))
    fields = getattr(value, "__dict__", None)
    if isinstance(fields, dict):
        return {
            key: _legacy_envelope_payload(item)
            for key, item in fields.items()
            if not key.startswith("_")
        }
    return str(value)


def _resource_descriptor_payload(resource: object) -> dict[str, Any] | None:
    """Project one SDK resource descriptor into a stable JSON shape."""
    if is_dataclass(resource):
        value = asdict(resource)
    elif isinstance(resource, dict):
        value = dict(resource)
    else:
        value = {
            key: getattr(resource, key, None)
            for key in (
                "type",
                "file_key",
                "file_name",
                "duration_ms",
                "cover_image_key",
            )
        }

    resource_type = str(value.get("type") or "").lower()
    if resource_type == "media":
        resource_type = "video"
    file_key = value.get("file_key")
    if resource_type not in _ATTACHMENT_TYPES or not isinstance(file_key, str):
        return None
    if not file_key:
        return None

    result: dict[str, Any] = {
        "type": resource_type,
        "file_key": file_key,
    }
    for key in ("file_name", "duration_ms", "cover_image_key"):
        if value.get(key) is not None:
            result[key] = value[key]
    return result


def _message_resource_descriptors(
    resources: object,
    message_type: str,
    content: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return downloadable resources, retaining a legacy-content fallback."""
    descriptors: list[dict[str, Any]] = []
    if isinstance(resources, (list, tuple)):
        for resource in resources:
            projected = _resource_descriptor_payload(resource)
            if projected is not None:
                descriptors.append(projected)

    if not descriptors:
        logical_type = "video" if message_type == "media" else message_type
        if logical_type in _ATTACHMENT_TYPES:
            if logical_type == "image":
                file_key = content.get("image_key") or content.get("file_key")
            else:
                file_key = content.get("file_key")
            if isinstance(file_key, str) and file_key:
                fallback: dict[str, Any] = {
                    "type": logical_type,
                    "file_key": file_key,
                }
                if content.get("file_name") is not None:
                    fallback["file_name"] = content["file_name"]
                duration_ms = content.get("duration_ms", content.get("duration"))
                if duration_ms is not None:
                    fallback["duration_ms"] = duration_ms
                if logical_type == "video" and content.get("image_key"):
                    fallback["cover_image_key"] = content["image_key"]
                descriptors.append(fallback)

    expanded: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for descriptor in descriptors:
        key = (descriptor["type"], descriptor["file_key"])
        if key not in seen:
            seen.add(key)
            expanded.append(descriptor)
        cover_key = descriptor.get("cover_image_key")
        cover_identity = ("image", cover_key)
        if isinstance(cover_key, str) and cover_key and cover_identity not in seen:
            seen.add(cover_identity)
            expanded.append({
                "type": "image",
                "file_key": cover_key,
                "role": "cover",
                "parent_file_key": descriptor["file_key"],
            })
    return expanded


def _safe_attachment_filename(
    candidate: object,
    *,
    resource_type: str,
    index: int,
) -> str:
    """Make a provider filename safe under one message's attachments dir."""
    value = str(candidate or "").replace("\\", "/").rsplit("/", 1)[-1]
    value = _UNSAFE_ATTACHMENT_CHARS.sub("_", value).strip().rstrip(" .")
    if value in {"", ".", ".."}:
        value = f"{resource_type}-{index}{_ATTACHMENT_SUFFIXES[resource_type]}"
    if resource_type in {"image", "audio", "video", "sticker"}:
        if not Path(value).suffix:
            value += _ATTACHMENT_SUFFIXES[resource_type]
    if len(value) > 180:
        suffix = Path(value).suffix[:16]
        stem_budget = max(1, 180 - len(suffix))
        value = f"{Path(value).stem[:stem_budget]}{suffix}"
    return value


def _unique_attachment_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    counter = 2
    while candidate.exists():
        suffix = Path(filename).suffix
        stem = Path(filename).stem
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _private_mkdir(path: Path) -> None:
    """Create one Feishu state directory and keep it owner-only."""
    path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIRECTORY_MODE)
    try:
        path.chmod(_PRIVATE_DIRECTORY_MODE)
    except OSError as exc:
        log.warning(
            "Failed to restrict a Feishu state directory (%s)",
            type(exc).__name__,
        )


def _write_private_atomic(path: Path, content: bytes) -> None:
    """Atomically replace one Feishu state file with owner-only mode."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_private_json(
    path: Path,
    payload: object,
    *,
    ensure_ascii: bool = True,
) -> None:
    body = json.dumps(
        payload,
        indent=2,
        ensure_ascii=ensure_ascii,
        default=str,
    ).encode("utf-8")
    _write_private_atomic(path, body)


def _harden_existing_state(root: Path) -> None:
    """Best-effort migration of the bounded Feishu state tree to private modes."""
    _private_mkdir(root)
    failures = 0
    for current, directory_names, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in directory_names:
            child = current_path / name
            if child.is_symlink():
                continue
            safe_directories.append(name)
            try:
                child.chmod(_PRIVATE_DIRECTORY_MODE)
            except OSError:
                failures += 1
        directory_names[:] = safe_directories
        for name in filenames:
            child = current_path / name
            if child.is_symlink():
                continue
            try:
                child.chmod(_PRIVATE_FILE_MODE)
            except OSError:
                failures += 1
    if failures:
        log.warning(
            "Failed to restrict %d existing Feishu state entries",
            failures,
        )


def _safe_attachment_error(error: object) -> str:
    if isinstance(error, BaseException):
        fallback = type(error).__name__
        value = str(error)
    else:
        fallback = "unknown"
        value = str(error or "")
    normalized = " ".join(redact_text(value).split())
    return (normalized or fallback)[:200]


def _legacy_media_from_attachments(attachments: list[dict[str, Any]]) -> dict | None:
    primary = next(
        (item for item in attachments if item.get("role") != "cover"),
        attachments[0] if attachments else None,
    )
    if primary is None:
        return None
    result = {
        key: primary[key]
        for key in ("type", "filename", "path", "size", "file_key")
        if primary.get(key) is not None
    }
    if primary.get("status") == "failed":
        result["download_error"] = primary.get("error", "download failed")
    return result


def _structured_attachments_payload(
    attachments: object,
) -> tuple[list[dict[str, Any]], bool]:
    """Return bounded, secret-safe local attachment refs for the current event.

    Provider file keys and parent resource keys stay in the durable Feishu
    record/read surface.  The notification projection carries only enough
    local state for the agent to inspect a successfully downloaded resource or
    understand that a download/transcription stage failed.
    """
    if not isinstance(attachments, list):
        return [], False

    projected: list[dict[str, Any]] = []
    for attachment in attachments[:_STRUCTURED_MESSAGE_ATTACHMENT_CAP]:
        if not isinstance(attachment, dict):
            continue
        item = {
            key: attachment[key]
            for key in (
                "type",
                "status",
                "size",
                "duration_ms",
                "role",
                "transcription_status",
            )
            if attachment.get(key) is not None
        }
        filename = attachment.get("filename") or attachment.get("file_name")
        if isinstance(filename, str) and filename:
            item["filename"] = filename[:180]
        path = attachment.get("path")
        if isinstance(path, str) and path:
            item["path"] = path[:_STRUCTURED_ATTACHMENT_PATH_CAP]
            if len(path) > _STRUCTURED_ATTACHMENT_PATH_CAP:
                item["path_truncated"] = True
        for source, target in (
            ("error", "error"),
            ("transcription_error", "transcription_error"),
        ):
            value = attachment.get(source)
            if isinstance(value, str) and value:
                item[target] = value[:200]
        if item:
            projected.append(item)
    return projected, len(attachments) > _STRUCTURED_MESSAGE_ATTACHMENT_CAP


class TypingIndicatorManager:
    """Manages automatic typing feedback for Feishu chats.

    Feishu exposes transient processing presence as the native ``Typing``
    reaction on the incoming message. The exact reaction id is retained only
    until the response/progress card is sent, then removed best-effort.
    """

    def __init__(self) -> None:
        self._active_chats: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def start_typing(
        self,
        account: Any,
        chat_id: str,
        message_id: str,
        receive_id: str,
        receive_id_type: str,
    ) -> str | None:
        """Add a native Typing reaction and return its reaction id.

        Args:
            account: FeishuAccount instance.
            chat_id: The chat_id used to serialize presence cleanup.
            message_id: Incoming Feishu message receiving the reaction.
            receive_id: The response receive_id (open_id or chat_id).
            receive_id_type: "open_id" or "chat_id".
        """
        key = (account.alias, chat_id)
        with self._lock:
            if key in self._active_chats:
                return None  # Already typing
            try:
                reaction_id = account.add_typing_reaction(message_id)
                if not reaction_id:
                    return None
                self._active_chats[key] = {
                    "message_id": message_id,
                    "reaction_id": reaction_id,
                    "receive_id": receive_id,
                    "receive_id_type": receive_id_type,
                }
                return reaction_id
            except Exception as e:
                log.debug("Typing indicator failed for %s:%s: %s",
                          account.alias, chat_id, e)
                return None

    def stop_typing(self, account: Any, chat_id: str) -> bool:
        """Remove the native Typing reaction for a chat.

        Returns True when an active typing entry existed and deletion was
        attempted, even if the best-effort reaction removal itself failed.
        """
        key = (account.alias, chat_id)
        with self._lock:
            info = self._active_chats.pop(key, None)
        if info and info.get("message_id") and info.get("reaction_id"):
            try:
                account.remove_reaction(
                    info["message_id"], info["reaction_id"],
                )
            except Exception as e:
                log.debug("Failed to remove typing reaction for %s:%s: %s",
                          account.alias, chat_id, e)
            return True
        return False

    def stop_typing_by_receive(
        self, account: Any, receive_id: str, receive_id_type: str,
    ) -> None:
        """Fallback cleanup when the chat_id key isn't known.

        Used by _send on p2p (open_id) sends that fail before the chat_id
        comes back from the API, so the indicator started under the real
        chat_id at receive-time still gets cleaned up. Best-effort and
        non-failing.
        """
        with self._lock:
            matching = [
                key for key, info in self._active_chats.items()
                if key[0] == account.alias
                and info.get("receive_id") == receive_id
                and info.get("receive_id_type") == receive_id_type
            ]
            removed = [(key, self._active_chats.pop(key)) for key in matching]
        for key, info in removed:
            message_id = info.get("message_id")
            reaction_id = info.get("reaction_id")
            if not message_id or not reaction_id:
                continue
            try:
                account.remove_reaction(message_id, reaction_id)
            except Exception as e:
                log.debug(
                    "Failed to remove typing reaction for %s (receive_id=%s): %s",
                    key, receive_id, e,
                )

    def stop_all(self, accounts: dict | None = None) -> None:
        """Stop all typing indicators and remove their native reactions.

        Args:
            accounts: Optional dict of alias -> FeishuAccount for cleanup.
                      If provided, reactions are removed before clearing.
                      If None, just clears the tracking dict (best-effort).
        """
        with self._lock:
            chats = dict(self._active_chats)
            self._active_chats.clear()

        if accounts:
            for (alias, _chat_id), info in chats.items():
                message_id = info.get("message_id")
                reaction_id = info.get("reaction_id")
                if message_id and reaction_id:
                    acct = accounts.get(alias)
                    if acct:
                        try:
                            acct.remove_reaction(message_id, reaction_id)
                        except Exception as e:
                            log.debug(
                                "Failed to remove typing reaction %s on shutdown: %s",
                                reaction_id, e,
                            )


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
                "faster-whisper is required for Feishu voice transcription; "
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
        error = _safe_attachment_error(e)
        log.warning("Voice transcription failed: %s", error)
        return {"error": error}

DESCRIPTION = (
    "Feishu (Lark) bot client — interact with Feishu users and group chats. "
    "MCP OWNERSHIP: this MCP belongs to the orchestrator (admin). If you are "
    "an avatar (your admin block is empty or all admin privileges are false), "
    "do not attempt to configure or reconfigure this MCP — your orchestrator "
    "manages it, and if the network needs this MCP to reach you the wiring "
    "is propagated to your session automatically. "
    "Use 'send' for outgoing text, markdown, post, card, media, share, or sticker messages "
    "(specify receive_id + receive_id_type and exactly one of text/content). "
    "'check' to see recent conversations with unread counts. "
    "'read' to read messages from a specific chat (returns compound message IDs). "
    "'reply' to respond to a message and follow its topic by default "
    "(use compound ID from read results). "
    "'react' to add or remove a reaction on a message. "
    "'search' to find messages by keyword or regex. "
    "'delete' to delete a bot message (message_id). "
    "'edit' to edit a sent text/post/card message (message_id, text or content; "
    "media is not editable through this action). "
    "'contacts' to manage saved contacts (open_id aliases). "
    "'accounts' to list configured app accounts. "
    "Voice/audio messages are automatically transcribed using Whisper (local) "
    "and delivered as text. "
    "Rich feedback: automatic 'seen' (OK) and transient native Typing reactions "
    "on message receipt, 'done' (THUMBSUP) after response is sent, and native "
    "schema-2.0 progress cards for long-running tasks."
)

# Public callers receive the strict LTP-v2 family schema. Manager dispatch
# remains the internal flat action boundary after family validation.
SCHEMA = _family.FEISHU_SCHEMA


class FeishuManager:
    """Tool handler + filesystem manager for the Feishu addon."""

    def __init__(
        self,
        service: "FeishuService",
        *,
        working_dir: Path,
        on_inbound: "Callable[[dict], None]",
        local_command_core: LocalCommandCore | None = None,
    ) -> None:
        self._service = service
        self._working_dir = Path(working_dir)
        self._on_inbound = on_inbound
        # Duplicate send protection: (alias, receive_id, normalized content) -> count
        self._last_sent: dict[tuple[str, str, str], int] = {}
        self._dup_free_passes = 2
        # Incoming event dedupe: per-account FIFO of recently-seen
        # feishu_message_id values. Protects against Feishu SDK WS
        # reconnect redelivery (issue #5). Bounded; oldest evicted first.
        self._seen_msg_ids: dict[str, OrderedDict[str, None]] = {}
        self._seen_channel_event_ids: dict[str, OrderedDict[str, None]] = {}
        self._dedupe_lock = threading.Lock()
        self._dedupe_limit = 1000
        self._card_action_locks: dict[tuple[str, str], threading.Lock] = {}
        self._card_action_locks_guard = threading.Lock()
        self._local_commands = local_command_core or LocalCommandCore(
            self._working_dir
        )
        self._control_event_store = FeishuControlEventStore(
            self._working_dir / "feishu" / "control_callbacks.json"
        )
        self._control_cards = FeishuControlCards(
            self._local_commands,
            TaskCardSettingsPort(
                enabled=self._raw_taskcard_enabled,
                set_enabled=getattr(self._service, "set_taskcard_enabled", None),
                normal_rows=self._taskcard_normal_rows,
                set_normal_rows=getattr(
                    self._service, "set_taskcard_normal_rows", None
                ),
            ),
            on_normal_rows_changed=self._on_taskcard_normal_rows_changed,
        )
        self._task_card_store = FeishuTaskCardStore(
            self._working_dir / "feishu" / "task_cards.json"
        )
        self._task_card_last_messages: dict[str, str] = {}
        self._task_card_last_messages_lock = threading.Lock()
        self._task_card_active = False
        self._resident = TaskCardResident(
            enabled=self._raw_taskcard_enabled(),
            transport=TaskCardResidentTransport(
                get_resident=lambda route: self._task_card_store.get(route),
                matches_route=lambda route, resident_id: (
                    self._resident_id_matches_route(route, resident_id)
                ),
                is_superseded=lambda route, resident_id: (
                    self._resident_is_superseded(route, resident_id)
                ),
                edit=lambda resident_id, frame: self._edit_resident_task_card(
                    resident_id, frame
                ),
                delete=lambda resident_id: self._delete_resident_task_card(
                    resident_id
                ),
                send=lambda route, frame: self._send_resident_task_card(
                    route, frame
                ),
                persist=lambda route, resident_id: self._task_card_store.set(
                    route, resident_id
                ),
            ),
        )
        self._task_card_journal = FeishuTaskCardJournal(
            self._working_dir / "logs" / "events.jsonl",
            self._broadcast_automatic_task_card,
        )
        self._programmable_task_card_poller = FeishuProgrammableTaskCardPoller(
            self._working_dir,
            on_active=self._broadcast_programmable_task_card,
            on_inactive=self._clear_programmable_task_card,
        )
        listener = getattr(self._service, "set_taskcard_listener", None)
        if callable(listener):
            listener(self._on_taskcard_changed)

    def _account_dir(self, alias: str) -> Path:
        return self._working_dir / "feishu" / alias

    def _resolve_account(self, args: dict) -> str:
        return args.get("account") or self._service.default_account.alias

    @staticmethod
    def _parse_compound_id(compound_id: str) -> tuple[str, str, str]:
        """Parse '{alias}:{chat_id}:{feishu_message_id}' -> (alias, chat_id, msg_id)."""
        parts = compound_id.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid Feishu message ID format: {compound_id!r}")
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _download_inbound_attachments(
        account: Any,
        message_id: str,
        message_dir: Path,
        resources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Download all declared message resources without losing failures."""
        if not resources:
            return []

        attachment_dir = message_dir / "attachments"
        results: list[dict[str, Any]] = []
        for index, resource in enumerate(resources, start=1):
            result = dict(resource)
            if account is None:
                result.update({"status": "failed", "error": "account unavailable"})
                results.append(result)
                continue
            try:
                response_name, content = account.get_message_resource(
                    message_id,
                    resource["file_key"],
                    resource["type"],
                )
                if not isinstance(content, (bytes, bytearray)):
                    raise TypeError("resource response is not bytes")
                _private_mkdir(attachment_dir)
                filename = _safe_attachment_filename(
                    response_name or resource.get("file_name"),
                    resource_type=resource["type"],
                    index=index,
                )
                path = _unique_attachment_path(attachment_dir, filename)
                body = bytes(content)
                _write_private_atomic(path, body)
                result.update({
                    "status": "downloaded",
                    "filename": path.name,
                    "path": str(path),
                    "size": len(body),
                })
            except Exception as exc:
                error = _safe_attachment_error(exc)
                result.update({
                    "status": "failed",
                    "error": error,
                })
                log.warning(
                    "Failed to download inbound Feishu %s resource: %s",
                    resource.get("type", "unknown"),
                    error,
                )
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        _harden_existing_state(self._working_dir / "feishu")
        self._service.start()
        try:
            self._task_card_active = True
            self._task_card_journal.start()
            self._programmable_task_card_poller.start()
        except Exception:
            self._task_card_active = False
            self._programmable_task_card_poller.stop()
            self._task_card_journal.stop()
            self._service.stop()
            raise

    def stop(self) -> None:
        self._task_card_active = False
        self._programmable_task_card_poller.stop()
        self._task_card_journal.stop()
        # Clean up any orphan typing indicator messages before stopping
        try:
            _typing_manager.stop_all(
                {alias: self._service.get_account(alias)
                 for alias in self._service.list_accounts()}
            )
        except Exception:
            pass
        self._service.stop()

    # ------------------------------------------------------------------
    # Automatic resident Task Card
    # ------------------------------------------------------------------

    def _raw_taskcard_enabled(self) -> bool:
        getter = getattr(self._service, "taskcard_enabled", None)
        return bool(getter()) if callable(getter) else True

    def _taskcard_normal_rows(self) -> int:
        getter = getattr(self._service, "taskcard_normal_rows", None)
        value = getter() if callable(getter) else None
        if type(value) is not int or not 1 <= value <= 10:
            return TaskCardEventProjection.DEFAULT_NORMAL_ROWS
        return value

    def _on_taskcard_changed(self, enabled: bool) -> None:
        if self._resident.set_enabled(enabled) and enabled:
            self._broadcast_automatic_task_card()
            try:
                self._programmable_task_card_poller.poll_once()
            except Exception as exc:  # noqa: BLE001
                log.debug("Feishu programmable Task Card re-enable failed: %s", exc)

    def _on_taskcard_normal_rows_changed(self) -> None:
        self._broadcast_automatic_task_card()

    def _note_task_card_message(
        self, route: TaskCardRoute, compound_id: str,
    ) -> None:
        """Record a route's latest observed message for this process only."""
        if not compound_id:
            return
        with self._task_card_last_messages_lock:
            self._task_card_last_messages[route.key] = compound_id

    def _last_task_card_message(self, route: TaskCardRoute) -> str | None:
        with self._task_card_last_messages_lock:
            return self._task_card_last_messages.get(route.key)

    def _resident_id_matches_route(
        self, route: TaskCardRoute, resident_id: str,
    ) -> bool:
        try:
            account, chat_id, _message_id = self._parse_compound_id(resident_id)
        except ValueError:
            return False
        return (
            account == route.account
            and chat_id == str(route.chat_id)
            and self._task_card_store.get(route) == resident_id
        )

    def _resident_is_superseded(
        self, route: TaskCardRoute, resident_id: str,
    ) -> bool:
        """Rotate only after this process observes a different later message."""
        latest = self._last_task_card_message(route)
        return latest is not None and latest != resident_id

    @staticmethod
    def _task_card_target_gone(exc: Exception) -> bool:
        return isinstance(exc, FeishuOperationError) and exc.error_code in {
            "NOT_FOUND",
            "TARGET_GONE",
            "TARGET_REVOKED",
        }

    def _edit_resident_task_card(
        self, resident_id: str, frame: str,
    ) -> tuple[str, str | None]:
        try:
            account, _chat_id, message_id = self._parse_compound_id(resident_id)
            self._service.get_account(account).update_content(
                message_id, {"card": _automatic_task_card(frame)}
            )
        except Exception as exc:
            if self._task_card_target_gone(exc):
                return TaskCardResident.EDIT_IMPOSSIBLE, None
            return TaskCardResident.EDIT_FAILED, "Failed to update Feishu Task Card"
        return TaskCardResident.EDIT_OK, None

    def _delete_resident_task_card(self, resident_id: str) -> str:
        tracked = self._task_card_store.contains(resident_id)
        if tracked is None:
            return TaskCardResident.DELETE_FAILED
        if not tracked:
            # A peer already replaced the persisted resident. The shared core
            # will re-read and adopt that exact card before considering a send.
            return TaskCardResident.DELETE_MISSING
        try:
            account, _chat_id, message_id = self._parse_compound_id(resident_id)
            self._service.get_account(account).delete_message(message_id)
        except Exception as exc:
            if self._task_card_target_gone(exc):
                return TaskCardResident.DELETE_MISSING
            return TaskCardResident.DELETE_FAILED
        return TaskCardResident.DELETE_OK

    def _send_resident_task_card(
        self, route: TaskCardRoute, frame: str,
    ) -> dict[str, Any] | None:
        account = self._service.get_account(route.account)
        card = _automatic_task_card(frame)
        reply_to: str | None = None
        reply_in_thread: bool | None = None
        try:
            if route.thread_id is None:
                result = account.send_content(
                    str(route.chat_id), "chat_id", {"card": card}
                )
            else:
                anchor = self._last_task_card_message(route)
                if not anchor:
                    return None
                anchor_account, anchor_chat, anchor_message = self._parse_compound_id(
                    anchor
                )
                if (
                    anchor_account != route.account
                    or anchor_chat != str(route.chat_id)
                ):
                    return None
                result = account.reply_content(
                    anchor_message,
                    str(route.chat_id),
                    {"card": card},
                    reply_in_thread=True,
                )
                result["thread_id"] = result.get("thread_id") or str(
                    route.thread_id
                )
                reply_to = anchor
                reply_in_thread = True
        except Exception:
            return {"status": TaskCardResident.SEND_INDETERMINATE}

        try:
            response = self._persist_outbound(
                account=route.account,
                result=result,
                to={
                    "receive_id": str(route.chat_id),
                    "receive_id_type": "chat_id",
                },
                content={"type": "card", "card": card},
                text=frame,
                message_type="interactive",
                reply_to=reply_to,
                reply_in_thread=reply_in_thread,
                task_card=True,
            )
        except Exception:
            return {"status": TaskCardResident.SEND_INDETERMINATE}
        return {
            "status": TaskCardResident.SEND_OK,
            "message_id": response["message_id"],
        }

    def _automatic_task_card_frame(self) -> str:
        groups, metadata = self._task_card_journal.snapshot()
        return TaskCardEventProjection.render_event_groups(
            groups,
            normal_rows=TaskCardEventProjection.DEFAULT_NORMAL_ROWS,
            metadata=metadata,
        )

    def _ensure_automatic_task_card(self, route: TaskCardRoute) -> dict[str, Any]:
        return self._resident.ensure(
            route.account,
            route.chat_id,
            self._automatic_task_card_frame(),
            error="Failed to ensure Feishu Task Card",
            thread_id=route.thread_id,
        )

    def _broadcast_automatic_task_card(self) -> None:
        if not self._task_card_active:
            return
        frame = self._automatic_task_card_frame()
        for route, _resident_id in self._task_card_store.routes():
            try:
                self._resident.project(
                    route.account,
                    route.chat_id,
                    "automatic",
                    frame,
                    error="Failed to update Feishu Task Card",
                    thread_id=route.thread_id,
                )
            except Exception as exc:
                log.debug("Feishu automatic Task Card projection failed: %s", exc)

    def _task_card_channel_frame(
        self,
        route: TaskCardRoute,
        channel: str,
    ) -> str | None:
        return self._resident.frames.get(route.key, {}).get(channel)

    def _broadcast_programmable_task_card(self, frame: str) -> None:
        """Project one valid intrinsic body without disturbing automatic slots."""
        if not self._task_card_active:
            return
        for route, _resident_id in self._task_card_store.routes():
            try:
                with self._resident.delivery_lock(
                    route.account,
                    route.chat_id,
                    route.thread_id,
                ):
                    if self._task_card_channel_frame(route, "programmable") == frame:
                        continue
                    self._resident.project(
                        route.account,
                        route.chat_id,
                        "programmable",
                        frame,
                        error="Failed to update Feishu programmable Task Card",
                        thread_id=route.thread_id,
                    )
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "Feishu programmable Task Card projection failed: %s",
                    exc,
                )

    def _clear_programmable_task_card(self) -> None:
        """Clear only the committed programmable slot on every resident route."""
        if not self._task_card_active:
            return
        for route, _resident_id in self._task_card_store.routes():
            try:
                with self._resident.delivery_lock(
                    route.account,
                    route.chat_id,
                    route.thread_id,
                ):
                    if self._task_card_channel_frame(route, "programmable") is None:
                        continue
                    self._resident.project(
                        route.account,
                        route.chat_id,
                        "programmable",
                        None,
                        error="Failed to clear Feishu programmable Task Card",
                        thread_id=route.thread_id,
                    )
            except Exception as exc:  # noqa: BLE001
                log.debug("Feishu programmable Task Card clear failed: %s", exc)

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    def handle(self, args: dict) -> dict:
        # Keep the standalone manager surface in parity with the public family
        # while retaining the flat internal boundary used by the family's own
        # child dispatch and existing manager tests. Family validation occurs
        # before any manager action I/O; child dispatch re-enters here with a
        # flat action mapping exactly once.
        if isinstance(args, dict) and {"input", "reasoning"}.issubset(args):
            return _family.handle_feishu(self, args)
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
            elif action == "react":
                return self._react(args)
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
            else:
                return failure_result(
                    f"Unknown feishu action: {action!r}",
                    error_code="ACTION_REQUIRED",
                )
        except Exception as e:
            return failure_result(e)

    def _manual(self) -> dict:
        # The manual lives in this package's bundled SKILL.md (standard skill
        # format: YAML frontmatter + markdown body), loaded at import time.
        # action='manual' returns the full skill markdown plus parsed metadata
        # and the resolved path; the frontmatter is also injected into the
        # schema's 'manual' action description as a catalog entry. Bundled
        # asset/reference sidecars, if any, are documented inside SKILL.md and
        # are not returned as structured tool fields.
        return _skill.manual_payload(
            _SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH, _SKILL_NAME
        )

    # ------------------------------------------------------------------
    # Incoming messages — called by FeishuService via on_message callback
    # ------------------------------------------------------------------

    def _is_duplicate_event(self, account_alias: str, feishu_msg_id: str) -> bool:
        """Record `feishu_msg_id` for `account_alias` and report whether
        it was already seen. Bounded FIFO per account; oldest evicted.
        """
        with self._dedupe_lock:
            seen = self._seen_msg_ids.get(account_alias)
            if seen is None:
                seen = OrderedDict()
                self._seen_msg_ids[account_alias] = seen
            if feishu_msg_id in seen:
                return True
            seen[feishu_msg_id] = None
            while len(seen) > self._dedupe_limit:
                seen.popitem(last=False)
            return False

    def _is_duplicate_channel_event(
        self, account_alias: str, event_id: str
    ) -> bool:
        with self._dedupe_lock:
            seen = self._seen_channel_event_ids.get(account_alias)
            if seen is None:
                seen = OrderedDict()
                self._seen_channel_event_ids[account_alias] = seen
            if event_id in seen:
                return True
            seen[event_id] = None
            while len(seen) > self._dedupe_limit:
                seen.popitem(last=False)
            return False

    @staticmethod
    def _event_operator_payload(operator: object) -> dict[str, Any]:
        return {
            key: value
            for key in ("open_id", "user_id", "name")
            if (value := getattr(operator, key, None)) is not None
        }

    @classmethod
    def _channel_event_projection(
        cls, event_type: str, event: object
    ) -> dict[str, Any]:
        if event_type == "reaction":
            return {
                "message_id": getattr(event, "message_id", "") or "",
                "operator": cls._event_operator_payload(
                    getattr(event, "operator", None)
                ),
                "emoji_type": getattr(event, "emoji_type", "") or "",
                "action": getattr(event, "action", "") or "",
                "chat_id": getattr(event, "chat_id", None),
                "chat_type": getattr(event, "chat_type", None),
                "action_time": getattr(event, "action_time", None),
            }
        if event_type == "message_read":
            return {
                "reader": cls._event_operator_payload(
                    getattr(event, "reader", None)
                ),
                "message_ids": list(getattr(event, "message_ids", None) or []),
            }
        result: dict[str, Any] = {
            "chat_id": getattr(event, "chat_id", "") or "",
            "operator": cls._event_operator_payload(
                getattr(event, "operator", None)
            ),
        }
        if event_type == "bot_added":
            result["chat_name"] = getattr(event, "chat_name", None)
            result["external"] = getattr(event, "external", None)
        return result

    @staticmethod
    def _channel_event_id(
        raw: dict[str, Any], event_type: str, projection: dict[str, Any]
    ) -> str:
        header = raw.get("header")
        if isinstance(header, dict):
            value = header.get("event_id")
            if isinstance(value, str) and value:
                return value
        for key in ("uuid", "event_id"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
        canonical = json.dumps(
            {"event_type": event_type, "event": projection, "feishu": raw},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return f"derived-{hashlib.sha256(canonical).hexdigest()[:24]}"

    @staticmethod
    def _channel_event_date(raw: dict[str, Any], event: object) -> str:
        header = raw.get("header")
        candidates = [getattr(event, "action_time", None)]
        if isinstance(header, dict):
            candidates.append(header.get("create_time"))
        candidates.append(raw.get("ts"))
        for candidate in candidates:
            try:
                timestamp = float(candidate)
                while timestamp > 10_000_000_000:
                    timestamp /= 1000
                return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except (TypeError, ValueError, OSError, OverflowError):
                continue
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def on_channel_event(
        self, account_alias: str, data: FeishuInboundChannelEvent
    ) -> None:
        """Persist a passive channel event in the reserved events conversation."""
        try:
            if not isinstance(data, FeishuInboundChannelEvent):
                return
            event_type = data.event_type
            event = data.event
            raw_value = _legacy_envelope_payload(getattr(event, "raw", {}))
            raw = raw_value if isinstance(raw_value, dict) else {}
            projection = self._channel_event_projection(event_type, event)
            event_id = self._channel_event_id(raw, event_type, projection)
            if self._is_duplicate_channel_event(account_alias, event_id):
                return

            actor_key = "reader" if event_type == "message_read" else "operator"
            actor = projection.get(actor_key) or {}
            actor_open_id = actor.get("open_id", "")
            source_chat_id = projection.get("chat_id")
            date_str = self._channel_event_date(raw, event)
            compound_id = f"{account_alias}:{SYNTHETIC_EVENTS_CHAT_ID}:{event_id}"
            payload = {
                "id": compound_id,
                "feishu_event_id": event_id,
                "chat_id": SYNTHETIC_EVENTS_CHAT_ID,
                "chat_type": "synthetic",
                "thread_id": None,
                "message_type": "event",
                "event_type": event_type,
                "from_open_id": actor_open_id,
                "from_name": actor.get("name"),
                "text": f"[{event_type}]",
                "date": date_str,
                "synthetic": True,
                "source_chat_id": source_chat_id,
                "event": projection,
                "feishu": raw,
            }
            msg_dir = (
                self._account_dir(account_alias) / "inbox" / str(uuid4())
            )
            _private_mkdir(msg_dir)
            _write_private_json(msg_dir / "message.json", payload)
        except Exception as exc:
            log.warning(
                "Feishu channel event projection failed (%s): %s",
                account_alias,
                exc,
            )

    def _card_action_lock(
        self, account_alias: str, chat_id: str,
    ) -> threading.Lock:
        key = (account_alias, chat_id)
        with self._card_action_locks_guard:
            return self._card_action_locks.setdefault(key, threading.Lock())

    def _card_action_already_persisted(
        self, account_alias: str, event_id: str,
    ) -> bool:
        return any(
            message.get("event_type") == "card_action"
            and message.get("feishu_event_id") == event_id
            for message in self._list_messages(account_alias, "inbox")
        )

    @classmethod
    def _card_action_projection(cls, action_event: object) -> dict[str, Any]:
        action = getattr(action_event, "action", None)
        return {
            "source_message_id": getattr(action_event, "message_id", "") or "",
            "chat_id": getattr(action_event, "chat_id", "") or "",
            "operator": cls._event_operator_payload(
                getattr(action_event, "operator", None)
            ),
            "action": {
                key: _legacy_envelope_payload(getattr(action, key, None))
                for key in (
                    "tag", "value", "name", "option", "form_value",
                    "input_value", "options", "checked",
                )
                if getattr(action, key, None) is not None
            },
        }

    @staticmethod
    def _card_action_thread_id(raw: dict[str, Any]) -> str:
        event = raw.get("event")
        context = event.get("context") if isinstance(event, dict) else None
        if not isinstance(context, dict):
            return ""
        value = context.get("open_thread_id") or context.get("thread_id")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _card_action_text(projection: dict[str, Any]) -> str:
        action = projection.get("action") or {}
        tag = action.get("tag") or "unknown"
        value = json.dumps(
            action.get("value"), ensure_ascii=False, sort_keys=True, default=str,
        )
        text = f"[card action: {tag}] {value}"
        return text if len(text) <= 2000 else text[:1999] + "…"

    @staticmethod
    def _local_command_context(
        data: object,
    ) -> tuple[str, str, str, str, str] | None:
        if isinstance(data, FeishuInboundEvent):
            inbound = data.message
            conversation = getattr(inbound, "conversation", None)
            if conversation is None:
                return None
            text = getattr(inbound, "body_text", None)
            if not isinstance(text, str):
                text = getattr(inbound, "content_text", "") or ""
            return (
                text,
                getattr(inbound, "id", "") or "",
                getattr(conversation, "chat_id", "") or "",
                getattr(conversation, "chat_type", "unknown") or "unknown",
                getattr(conversation, "thread_id", "") or "",
            )
        event = getattr(data, "event", None)
        message = getattr(event, "message", None) if event is not None else None
        if message is None:
            return None
        try:
            content = json.loads(getattr(message, "content", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            content = {}
        return (
            content.get("text", "") if isinstance(content, dict) else "",
            getattr(message, "message_id", "") or "",
            getattr(message, "chat_id", "") or "",
            getattr(message, "chat_type", "unknown") or "unknown",
            getattr(message, "thread_id", "") or "",
        )

    def _handle_local_command(self, account_alias: str, data: object) -> bool:
        context = self._local_command_context(data)
        if context is None:
            return False
        text, message_id, chat_id, _chat_type, thread_id = context
        if self._control_cards.parse(text) is None:
            return False
        if not message_id or not chat_id:
            return True
        if self._is_duplicate_event(account_alias, message_id):
            return True
        route = TaskCardRoute(account_alias, chat_id, thread_id or None)
        self._note_task_card_message(
            route, f"{account_alias}:{chat_id}:{message_id}"
        )
        try:
            card = self._control_cards.render(text)
            account = self._service.get_account(account_alias)
            response = account.reply_content(
                message_id,
                chat_id,
                {"card": card},
                reply_in_thread=bool(thread_id),
            )
            response_id = response.get("message_id")
            if isinstance(response_id, str) and response_id:
                if not self._control_event_store.register_source(
                    account_alias, chat_id, response_id,
                ):
                    log.warning(
                        "Feishu control-card source registration failed (%s)",
                        account_alias,
                    )
                self._note_task_card_message(
                    route, f"{account_alias}:{chat_id}:{response_id}"
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Feishu local command failed (%s): %s", account_alias, exc
            )
        return True

    def _handle_control_callback(
        self,
        account_alias: str,
        projection: dict[str, Any],
        text: str,
    ) -> None:
        source_message_id = projection.get("source_message_id")
        if not isinstance(source_message_id, str) or not source_message_id:
            return
        card = self._control_cards.render(text)
        account = self._service.get_account(account_alias)
        account.update_content(source_message_id, {"card": card})

    def on_card_action(
        self, account_alias: str, data: FeishuInboundCardAction,
    ) -> None:
        """Handle a local control callback or persist a business callback."""
        try:
            if not isinstance(data, FeishuInboundCardAction):
                return
            raw = data.feishu if isinstance(data.feishu, dict) else {}
            projection = self._card_action_projection(data.action)
            chat_id = projection["chat_id"]
            actor = projection["operator"]
            actor_open_id = actor.get("open_id", "")
            if not chat_id or not actor_open_id:
                return
            event_id = self._channel_event_id(raw, "card_action", projection)
            action = projection.get("action") or {}
            control_text = self._control_cards.callback_text(action.get("value"))

            with self._card_action_lock(account_alias, chat_id):
                if control_text is not None and (
                    self._control_event_store.is_trusted_source(
                        account_alias,
                        chat_id,
                        projection["source_message_id"],
                    )
                ):
                    if not self._control_event_store.claim(account_alias, event_id):
                        return
                    self._handle_control_callback(
                        account_alias, projection, control_text
                    )
                    return
                if self._card_action_already_persisted(account_alias, event_id):
                    return
                date_str = self._channel_event_date(raw, data.action)
                thread_id = self._card_action_thread_id(raw)
                compound_id = f"{account_alias}:{chat_id}:callback-{event_id}"
                source_message_id = projection["source_message_id"]
                source_compound_id = (
                    f"{account_alias}:{chat_id}:{source_message_id}"
                    if source_message_id else None
                )
                text = self._card_action_text(projection)
                payload = {
                    "id": compound_id,
                    "feishu_event_id": event_id,
                    "chat_id": chat_id,
                    "chat_type": "unknown",
                    "thread_id": thread_id or None,
                    "message_type": "card_action",
                    "event_type": "card_action",
                    "from_open_id": actor_open_id,
                    "from_name": actor.get("name"),
                    "sender_type": "user",
                    "sender_is_bot": False,
                    "text": text,
                    "date": date_str,
                    "source_message_id": source_message_id or None,
                    "source_message_ref": source_compound_id,
                    "content": {
                        "kind": "card_action",
                        **projection,
                    },
                    "card_action": projection,
                    "feishu": raw,
                }
                msg_dir = (
                    self._account_dir(account_alias) / "inbox" / str(uuid4())
                )
                _private_mkdir(msg_dir)
                _write_private_json(
                    msg_dir / "message.json",
                    payload,
                    ensure_ascii=False,
                )
                self._upsert_contact(account_alias, actor_open_id, chat_id)

                preview, preview_metadata = (
                    self._build_conversation_preview_and_metadata(
                        account_alias, chat_id, compound_id,
                    )
                )
                display_name = (
                    actor.get("name")
                    or self._get_contact_name(account_alias, actor_open_id)
                    or actor_open_id
                )
                self._on_inbound({
                    "from": display_name,
                    "subject": (
                        f"feishu card action from {display_name} "
                        f"via {account_alias}"
                    ),
                    "body": preview,
                    "metadata": {
                        "message_id": compound_id,
                        "account": account_alias,
                        "chat_id": chat_id,
                        "chat_type": "unknown",
                        "thread_id": thread_id or None,
                        "from_open_id": actor_open_id,
                        "platform": "feishu",
                        "conversation_ref": f"{account_alias}:{chat_id}",
                        "message_ref": compound_id,
                        "message_type": "card_action",
                        **preview_metadata,
                    },
                    "wake": True,
                })
        except Exception as exc:
            log.warning(
                "Feishu card action processing failed (%s): %s",
                account_alias,
                exc,
            )

    def on_incoming(self, account_alias: str, data: object) -> None:
        """Persist an incoming Feishu message event to disk and notify agent."""
        if self._handle_local_command(account_alias, data):
            return
        try:
            if isinstance(data, FeishuInboundEvent):
                inbound = data.message
                conversation = getattr(inbound, "conversation", None)
                sender = getattr(inbound, "sender", None)
                if conversation is None or sender is None:
                    return
                feishu_msg_id = getattr(inbound, "id", "") or ""
                chat_id = getattr(conversation, "chat_id", "") or ""
                chat_type = getattr(conversation, "chat_type", "unknown") or "unknown"
                thread_id = getattr(conversation, "thread_id", None) or ""
                msg_type = (
                    getattr(inbound, "raw_content_type", "")
                    or getattr(getattr(inbound, "content", None), "kind", "")
                    or "unknown"
                )
                create_time = str(getattr(inbound, "create_time", 0) or "")
                raw_message = getattr(inbound, "raw", None)
                raw_message = raw_message if isinstance(raw_message, dict) else {}
                parent_id = raw_message.get("parent_id") or ""
                root_id = raw_message.get("root_id") or ""
                if not parent_id:
                    reply = getattr(inbound, "reply", None)
                    parent_id = getattr(reply, "message_id", "") if reply else ""
                open_id = getattr(sender, "open_id", "") or ""
                user_id = getattr(sender, "user_id", None)
                union_id = getattr(sender, "union_id", None)
                sender_name = getattr(sender, "display_name", None)
                sender_type = getattr(sender, "sender_type", None)
                sender_is_bot = bool(getattr(sender, "is_bot", False))
                content_obj = getattr(inbound, "content", None)
                content_data = getattr(content_obj, "raw", None)
                content_data = content_data if isinstance(content_data, dict) else {}
                normalized_content = _normalized_content_payload(content_obj, msg_type)
                mentions = _normalized_mentions_payload(
                    getattr(inbound, "mentions", None)
                )
                body_text = getattr(inbound, "body_text", None)
                text = (
                    body_text
                    if isinstance(body_text, str)
                    else (getattr(inbound, "content_text", "") or "")
                )
                resource_source = getattr(inbound, "resources", None)
                raw_envelope = data.feishu
            else:
                # Backward-compatible callback shape used by older callers and
                # fixtures. Live SDK delivery uses FeishuInboundEvent above.
                event = getattr(data, "event", None)
                if event is None:
                    return
                message = getattr(event, "message", None)
                sender = getattr(event, "sender", None)
                if message is None or sender is None:
                    return
                feishu_msg_id = getattr(message, "message_id", "") or ""
                chat_id = getattr(message, "chat_id", "") or ""
                chat_type = getattr(message, "chat_type", "p2p") or "p2p"
                thread_id = getattr(message, "thread_id", "") or ""
                msg_type = getattr(message, "message_type", "text") or "text"
                content_str = getattr(message, "content", "{}") or "{}"
                create_time = getattr(message, "create_time", "") or ""
                parent_id = getattr(message, "parent_id", "") or ""
                root_id = getattr(message, "root_id", "") or ""
                sender_id = getattr(sender, "sender_id", None)
                open_id = (
                    (getattr(sender_id, "open_id", "") or "") if sender_id else ""
                )
                user_id = getattr(sender_id, "user_id", None) if sender_id else None
                union_id = getattr(sender_id, "union_id", None) if sender_id else None
                sender_name = None
                sender_type = getattr(sender, "sender_type", None)
                sender_is_bot = sender_type in {"bot", "app"}
                try:
                    content_data = json.loads(content_str)
                except (json.JSONDecodeError, AttributeError):
                    content_data = {}
                normalized_content = {
                    "kind": msg_type,
                    **content_data,
                }
                mentions = _normalized_mentions_payload(
                    getattr(message, "mentions", None)
                )
                resource_source = []
                raw_envelope = _legacy_envelope_payload(data)

                if msg_type == "text":
                    text = content_data.get("text", "")
                elif msg_type == "audio":
                    text = ""
                elif msg_type == "image":
                    text = content_data.get("text", "") or "[Image]"
                elif msg_type == "file":
                    text = content_data.get("text", "") or "[File]"
                elif msg_type == "sticker":
                    text = "[Sticker]"
                elif msg_type == "interactive":
                    text = content_data.get("text", "") or "[Interactive card]"
                elif msg_type == "post":
                    post_content = content_data.get("content", {})
                    title = content_data.get("title", "")
                    text_parts = [title] if title else []
                    if isinstance(post_content, dict):
                        for paragraphs in post_content.values():
                            if isinstance(paragraphs, list):
                                for para in paragraphs:
                                    if isinstance(para, list):
                                        for elem in para:
                                            if (
                                                isinstance(elem, dict)
                                                and elem.get("tag") == "text"
                                            ):
                                                text_parts.append(elem.get("text", ""))
                    text = " ".join(text_parts).strip() or "[Rich text message]"
                else:
                    text = content_data.get("text", "") or f"[{msg_type} message]"

            if feishu_msg_id and self._is_duplicate_event(
                account_alias, feishu_msg_id,
            ):
                log.debug(
                    "feishu dedupe: dropping replayed event %s on %s",
                    feishu_msg_id, account_alias,
                )
                return
            resource_descriptors = _message_resource_descriptors(
                resource_source,
                msg_type,
                content_data,
            )

            if create_time:
                try:
                    ts = int(create_time) / 1000
                    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                except (ValueError, OSError):
                    date_str = datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
            else:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            compound_id = f"{account_alias}:{chat_id}:{feishu_msg_id}"

            payload = {
                "id": compound_id,
                "feishu_message_id": feishu_msg_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "thread_id": thread_id or None,
                "message_type": msg_type,
                "from_open_id": open_id,
                "from_user_id": user_id,
                "from_union_id": union_id,
                "from_name": sender_name,
                "sender_type": sender_type,
                "sender_is_bot": sender_is_bot,
                "text": text,
                "date": date_str,
                "parent_id": parent_id,
                "root_id": root_id,
                "reply_to": (
                    f"{account_alias}:{chat_id}:{parent_id}" if parent_id else None
                ),
                "mentions": mentions,
                "content": normalized_content,
                "attachments": [],
                "media": None,
                "voice_transcript": None,
                "feishu": raw_envelope,
            }

            msg_uuid = str(uuid4())
            acct_dir = self._account_dir(account_alias)
            msg_dir = acct_dir / "inbox" / msg_uuid
            _private_mkdir(msg_dir)

            # Rich feedback: Add "seen" reaction (OK emoji) immediately
            account = None
            try:
                account = self._service.get_account(account_alias)
            except (KeyError, Exception) as e:
                log.warning("Failed to get account %s for feedback: %s",
                            account_alias, e)

            if account and feishu_msg_id:
                try:
                    account.add_reaction(feishu_msg_id, REACTION_SEEN)
                except Exception as e:
                    log.debug("Failed to add 'seen' reaction: %s", e)

            # Rich feedback: Start typing indicator
            if account and chat_id:
                # Determine receive_id for sending the typing indicator
                if chat_type == "p2p":
                    typing_receive_id = open_id
                    typing_receive_id_type = "open_id"
                else:
                    typing_receive_id = chat_id
                    typing_receive_id_type = "chat_id"
                _typing_manager.start_typing(
                    account,
                    chat_id,
                    feishu_msg_id,
                    typing_receive_id,
                    typing_receive_id_type,
                )

            attachments = self._download_inbound_attachments(
                account,
                feishu_msg_id,
                msg_dir,
                resource_descriptors,
            )
            payload["attachments"] = attachments
            payload["media"] = _legacy_media_from_attachments(attachments)

            # Preserve the established local voice transcription path, but
            # keep the downloadable resource descriptor when either stage
            # fails instead of replacing it with an untraceable error string.
            if msg_type == "audio":
                audio = next(
                    (item for item in attachments if item.get("type") == "audio"),
                    None,
                )
                if audio and audio.get("status") == "downloaded":
                    log.info("Transcribing Feishu voice message on %s", account_alias)
                    transcript = _transcribe_voice(str(audio["path"]))
                    if "error" not in transcript:
                        text = transcript.get("text", "")
                        payload["text"] = text
                        payload["voice_transcript"] = {
                            "text": text,
                            "language": transcript.get("language"),
                            "duration": transcript.get("duration"),
                            "segments": transcript.get("segments"),
                        }
                        audio["transcription_status"] = "completed"
                        log.info(
                            "Feishu voice transcription successful: %s chars",
                            len(text),
                        )
                    else:
                        error = _safe_attachment_error(transcript.get("error"))
                        audio.update({
                            "transcription_status": "failed",
                            "transcription_error": error,
                        })
                        log.warning("Feishu voice transcription failed: %s", error)
                elif audio:
                    audio["transcription_status"] = "skipped"

            # Persist to disk
            _write_private_json(msg_dir / "message.json", payload)

            if open_id:
                self._upsert_contact(account_alias, open_id, chat_id)

            route = TaskCardRoute(
                account_alias,
                str(chat_id),
                str(thread_id) if thread_id else None,
            )
            self._note_task_card_message(route, compound_id)

        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "on_incoming processing error (%s): %s", account_alias, exc
            )
            return

        # Forward to host via LICC. Body is a conversation preview showing
        # the last 10 rounds with a guidance header; agent uses
        # feishu(action="check"|"read") for the full conversation. Metadata
        # carries routing keys plus the structured recent_messages /
        # latest_incoming context the kernel moves into the persistent
        # notification lane.
        display_name = (
            payload.get("from_name")
            or self._get_contact_name(account_alias, open_id)
            or open_id
        )
        try:
            preview, preview_metadata = self._build_conversation_preview_and_metadata(
                account_alias, chat_id, compound_id,
            )
        except Exception as exc:
            log.warning("_build_conversation_preview_and_metadata failed: %s", exc)
            preview = text[:300].replace("\n", " ") if text else ""
            if len(text or "") > 300:
                preview += "..."
            preview_metadata = {}

        if self._task_card_active and chat_id:
            try:
                self._ensure_automatic_task_card(route)
            except Exception as exc:
                # Task Card transport is fail-open for the actual inbound
                # delivery and must never prevent the agent wake.
                log.debug("Failed to ensure inbound Feishu Task Card: %s", exc)

        log.info(
            "feishu_received account=%s sender=%r id=%s",
            account_alias, display_name, compound_id,
        )

        # Enhance subject for voice messages
        subject = f"feishu message from {display_name} via {account_alias}"
        if payload.get("voice_transcript"):
            subject = f"feishu voice message from {display_name} via {account_alias} (transcribed)"

        try:
            self._on_inbound({
                "from": display_name,
                "subject": subject,
                "body": preview if preview else "(no text — see media or callback)",
                "metadata": {
                    "message_id": compound_id,
                    "account": account_alias,
                    "chat_id": chat_id,
                    "chat_type": chat_type,
                    "thread_id": thread_id or None,
                    "from_open_id": open_id,
                    # Generic LICC preview metadata copied into
                    # .notification/mcp.feishu.json.  Keep both the legacy
                    # Feishu-specific keys above and the generic chat keys
                    # below so the kernel's persistent notification lane gets
                    # stable routing hooks without re-reading Feishu state.
                    "platform": "feishu",
                    "conversation_ref": f"{account_alias}:{chat_id}",
                    "message_ref": compound_id,
                    "preview_truncated": len(text or "") > 300,
                    "full_length": len(text or ""),
                    "has_media": payload.get("media") is not None,
                    "is_voice_transcript": payload.get("voice_transcript") is not None,
                    "voice_duration": (
                        payload.get("voice_transcript", {}).get("duration")
                        if payload.get("voice_transcript") else None
                    ),
                    "message_type": msg_type,
                    **preview_metadata,
                },
                "wake": True,
            })
        except Exception as e:
            log.error("on_inbound callback failed for feishu msg %s: %s",
                      compound_id, e)
        # Note: typing indicator continues until _send() is called by the agent.
        # _send() stops typing when it sends the response.

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
                    if data.get("task_card") is True:
                        continue
                    data["_dir"] = str(msg_dir)
                    messages.append(data)
                except (json.JSONDecodeError, OSError):
                    continue
        messages.sort(key=lambda m: m.get("date") or m.get("sent_at") or "", reverse=True)
        return messages

    @staticmethod
    def _relative_time(date_str: str, *, now: datetime) -> str:
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

    def _conversation_messages(
        self,
        account_alias: str,
        chat_id: str,
        max_messages: int = _CONVERSATION_PREVIEW_MESSAGES,
    ) -> list[dict]:
        """Load the last *max_messages* messages of one chat, date ascending.

        Scans inbox/ and sent/ dirs for messages matching *chat_id*; each
        message dict gains a transient ``_folder`` marker used for direction
        and sender rendering.
        """
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
                if data.get("task_card") is True:
                    continue
                if data.get("chat_id") != chat_id:
                    continue
                data["_folder"] = folder
                messages.append(data)

        messages.sort(key=lambda m: m.get("date") or m.get("sent_at") or "")
        return messages[-max_messages:]

    def _conversation_sender_name(self, account_alias: str, message: dict) -> str:
        if message.get("_folder") == "sent":
            return "me"
        open_id = message.get("from_open_id", "") or ""
        return (
            message.get("from_name")
            or self._get_contact_name(account_alias, open_id)
            or open_id
            or "unknown"
        )

    @staticmethod
    def _message_display_text(message: dict) -> str:
        text = message.get("text", "") or ""
        if message.get("media") and not text:
            media_type = message["media"].get("type", "media")
            text = f"[{media_type}]"
        return text

    def _structured_message(
        self,
        account_alias: str,
        message: dict,
        *,
        current_compound_id: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Render one persisted message as a structured LICC metadata item.

        Mirrors the Telegram producer's structured-message shape (id,
        direction, sender, date, relative_time, bounded text + text_truncated,
        is_current, media subset, reply refs) so the kernel's persistent
        notification lane treats both IM channels uniformly.
        """
        now = now or datetime.now(timezone.utc)
        cid = str(message.get("id", ""))
        text = self._message_display_text(message)
        text_truncated = len(text) > _STRUCTURED_MESSAGE_TEXT_CAP
        if text_truncated:
            text = text[: _STRUCTURED_MESSAGE_TEXT_CAP - 1] + "…"
        direction = "outgoing" if message.get("_folder") == "sent" else "incoming"
        date_str = message.get("date") or message.get("sent_at") or ""
        item: dict = {
            "id": cid,
            "direction": direction,
            "sender": self._conversation_sender_name(account_alias, message),
            "date": date_str,
            "relative_time": self._relative_time(date_str, now=now),
            "text": text,
            "text_truncated": text_truncated,
        }
        message_type = message.get("message_type")
        if message_type:
            item["type"] = message_type
        thread_id = message.get("thread_id")
        if thread_id:
            item["thread_id"] = thread_id
        mentions = message.get("mentions")
        if isinstance(mentions, list) and mentions:
            item["mentions"] = mentions[:_STRUCTURED_MESSAGE_MENTION_CAP]
            if len(mentions) > _STRUCTURED_MESSAGE_MENTION_CAP:
                item["mentions_truncated"] = True
        if current_compound_id and cid == current_compound_id:
            item["is_current"] = True
        if message.get("media"):
            media = message["media"] or {}
            item["media"] = {
                key: media[key]
                for key in ("type", "filename", "size", "duration", "mime_type")
                if key in media and media[key] is not None
            }
        if current_compound_id and cid == current_compound_id:
            attachments, attachments_truncated = _structured_attachments_payload(
                message.get("attachments")
            )
            if attachments:
                item["attachments"] = attachments
                if attachments_truncated:
                    item["attachments_truncated"] = True
                downloaded = any(
                    attachment.get("status") == "downloaded"
                    and attachment.get("path")
                    for attachment in attachments
                )
                failed = any(
                    attachment.get("status") == "failed"
                    for attachment in attachments
                )
                guidance: list[str] = []
                if downloaded:
                    guidance.append(
                        "Current Feishu attachments were downloaded locally; "
                        "inspect the listed paths before answering when the "
                        "human's intent depends on the media."
                    )
                if failed:
                    guidance.append(
                        "Some Feishu attachments failed to download; call "
                        "feishu.read for the exact preserved descriptor and "
                        "report the limitation instead of guessing."
                    )
                if guidance:
                    item["comment"] = " ".join(guidance)
        parent_id = message.get("parent_id")
        if parent_id:
            item["parent_id"] = parent_id
            id_parts = cid.split(":", 2)
            if len(id_parts) == 3:
                item["reply_to"] = f"{id_parts[0]}:{id_parts[1]}:{parent_id}"
        if message.get("voice_transcript"):
            item["is_voice_transcript"] = True
        return item

    def _render_conversation_preview(
        self,
        account_alias: str,
        messages: list[dict],
        *,
        chat_id: str,
        now: datetime,
    ) -> str:
        """Render the markdown conversation preview for the LICC body.

        Prepends a guidance header that tells the receiving agent how to
        interpret the preview. Reply lines are quoted beneath their parent
        (truncated to 50 chars).
        """
        by_id: dict[str, dict] = {m.get("id", ""): m for m in messages}

        lines: list[str] = []
        for m in messages:
            cid = m.get("id", "")
            rel = self._relative_time(m.get("date") or m.get("sent_at") or "", now=now)
            sender = self._conversation_sender_name(account_alias, m)
            text_display = self._message_display_text(m).replace("\n", " ")

            line = f"[{rel}] #{cid} {sender}: {text_display}"
            lines.append(line)

            parent_id = m.get("parent_id")
            if parent_id:
                id_parts = cid.split(":", 2)
                if len(id_parts) == 3:
                    parent_compound = f"{id_parts[0]}:{id_parts[1]}:{parent_id}"
                    orig = by_id.get(parent_compound)
                    if orig:
                        orig_rel = self._relative_time(orig.get("date", ""), now=now)
                        orig_text = orig.get("text", "") or ""
                        orig_snippet = orig_text[:50]
                        if len(orig_text) > 50:
                            orig_snippet += "…"
                        lines.append(
                            f"  ↳ [{orig_rel}] #{parent_compound}: {orig_snippet}"
                        )

        header = _NOTIFICATION_HEADER_TEMPLATE.format(channel="Feishu").rstrip("\n")
        tail = f"**Conversation — last {len(messages)} messages (chat {chat_id})**"
        prefix = f"{header}\n\n{tail}"
        conversation = "\n".join(lines)
        body = f"{prefix}\n{conversation}" if conversation else prefix
        if len(body) > 10000:
            budget = 10000 - len(prefix) - len("\n…\n")
            if budget > 0:
                conversation = "…\n" + conversation[-budget:]
                body = f"{prefix}\n{conversation}"
            else:
                body = body[:9997] + "…"
        return body

    def _build_conversation_preview_and_metadata(
        self,
        account_alias: str,
        chat_id: str,
        current_compound_id: str,
        max_messages: int = _CONVERSATION_PREVIEW_MESSAGES,
    ) -> tuple[str, dict]:
        """Build the markdown preview plus structured Feishu context metadata.

        The metadata carries the curated structured fields the kernel inbox
        allowlists for IM producers (``recent_messages`` + ``latest_incoming``)
        so the agent-facing persistent notification lane
        (``_meta.agent_meta.notifications.persistent.mcp.feishu``) gets structured message
        objects instead of having to parse the markdown transcript.
        """
        messages = self._conversation_messages(account_alias, chat_id, max_messages)
        now = datetime.now(timezone.utc)
        preview = self._render_conversation_preview(
            account_alias, messages, chat_id=chat_id, now=now,
        )
        structured = [
            self._structured_message(
                account_alias, m, current_compound_id=current_compound_id, now=now,
            )
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
        metadata: dict = {"recent_messages": structured}
        if latest_incoming is not None:
            metadata["latest_incoming"] = latest_incoming
        return preview, metadata

    def _build_conversation_preview(
        self,
        account_alias: str,
        chat_id: str,
        current_compound_id: str,
        max_messages: int = _CONVERSATION_PREVIEW_MESSAGES,
    ) -> str:
        """Markdown-preview-only compatibility wrapper."""
        preview, _metadata = self._build_conversation_preview_and_metadata(
            account_alias, chat_id, current_compound_id, max_messages,
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
        _private_mkdir(acct_dir)
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
        _private_mkdir(acct_dir)
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

    def _upsert_contact(
        self, account: str, open_id: str, chat_id: str = ""
    ) -> None:
        contacts = self._load_contacts(account)
        existing = contacts.get(open_id, {})
        if not existing.get("chat_id") and chat_id:
            existing["chat_id"] = chat_id
        contacts[open_id] = existing
        self._save_contacts(account, contacts)

    def _get_contact_name(self, account: str, open_id: str) -> str:
        contacts = self._load_contacts(account)
        info = contacts.get(open_id, {})
        return info.get("name") or info.get("alias") or ""

    def _find_message_record(self, account: str, compound_id: str) -> dict:
        for folder in ("inbox", "sent"):
            for message in self._list_messages(account, folder):
                if message.get("id") == compound_id:
                    return message
        return {}

    def _persist_outbound(
        self,
        *,
        account: str,
        result: dict[str, Any],
        to: dict[str, str],
        content: dict[str, Any],
        text: str,
        message_type: str,
        status: str = "sent",
        reply_to: str | None = None,
        reply_in_thread: bool | None = None,
        task_card: bool = False,
    ) -> dict[str, Any]:
        chat_id = result.get("chat_id") or to.get("receive_id") or ""
        feishu_message_ids = [
            value for value in result.get("message_ids", [])
            if isinstance(value, str) and value
        ]
        if not feishu_message_ids and result.get("message_id"):
            feishu_message_ids = [result["message_id"]]
        if not feishu_message_ids:
            raise RuntimeError("Feishu send succeeded without a message_id")

        compound_ids = [
            f"{account}:{chat_id}:{message_id}"
            for message_id in feishu_message_ids
        ]
        chunks = [
            {"index": index, "message_id": message_id}
            for index, message_id in enumerate(compound_ids, start=1)
        ]
        sent_uuid = str(uuid4())
        sent_dir = self._account_dir(account) / "sent" / sent_uuid
        _private_mkdir(sent_dir)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sent_record: dict[str, Any] = {
            "id": compound_ids[0],
            "feishu_message_id": feishu_message_ids[0],
            "feishu_message_ids": feishu_message_ids,
            "message_ids": compound_ids,
            "chunks": chunks,
            "to": to,
            "chat_id": chat_id,
            "message_type": message_type,
            "content": content,
            "text": text,
            "sent_at": now_iso,
            "date": now_iso,
            "status": status,
        }
        if status == "placeholder":
            sent_record["placeholder"] = True
        if task_card:
            sent_record["task_card"] = True
        media = _outbound_media_summary(content)
        if media is not None:
            sent_record["media"] = media
        for key in ("root_id", "parent_id", "thread_id"):
            value = result.get(key)
            if value:
                sent_record[key] = value
        if reply_to:
            sent_record["reply_to"] = reply_to
        if reply_in_thread is not None:
            sent_record["reply_in_thread"] = reply_in_thread
        _write_private_json(
            sent_dir / "message.json",
            sent_record,
            ensure_ascii=False,
        )
        self._note_task_card_message(
            TaskCardRoute(
                account,
                str(chat_id),
                str(sent_record["thread_id"])
                if sent_record.get("thread_id")
                else None,
            ),
            compound_ids[-1],
        )

        response: dict[str, Any] = {
            "status": "sent",
            "message_id": compound_ids[0],
            "message_ids": compound_ids,
            "chunk_count": len(compound_ids),
            "chunks": chunks,
            "content_type": content["type"],
        }
        if sent_record.get("thread_id"):
            response["thread_id"] = sent_record["thread_id"]
        if reply_in_thread is not None:
            response["reply_in_thread"] = reply_in_thread
        return response

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _send(self, args: dict) -> dict:
        account = self._resolve_account(args)
        receive_id = args.get("receive_id", "")
        receive_id_type = args.get("receive_id_type") or "open_id"
        placeholder = bool(args.get("placeholder", False))

        if not receive_id:
            return failure_result(
                "receive_id is required", error_code="INVALID_ARGUMENT",
            )
        content, sdk_message, text, message_type = _normalize_outbound_content(args)
        if placeholder and content["type"] not in _PLACEHOLDER_CONTENT_TYPES:
            return failure_result(
                "placeholder only supports text, markdown, or post",
                error_code="INVALID_ARGUMENT",
            )
        if placeholder:
            card, text = _native_progress_card(content, text)
            content = {"type": "card", "card": card}
            sdk_message = {"card": card}
            message_type = "interactive"

        content_key = json.dumps(content, ensure_ascii=False, sort_keys=True)
        dup_key = (account, receive_id, content_key)
        count = self._last_sent.get(dup_key, 0)
        if count >= self._dup_free_passes:
            result = failure_result(
                "Identical message already sent. Think twice before repeating.",
                error_code="DUPLICATE_BLOCKED",
            )
            result["warning"] = result["error"]
            return result

        acct = self._service.get_account(account)
        # chat_id for typing cleanup — resolved after send, but if
        # receive_id_type is "chat_id" we already know it.
        chat_id: str = receive_id if receive_id_type == "chat_id" else ""

        try:
            result = acct.send_content(
                receive_id,
                receive_id_type,
                sdk_message,
            )

            self._last_sent[dup_key] = count + 1
            chat_id = result.get("chat_id") or chat_id
            response = self._persist_outbound(
                account=account,
                result=result,
                to={"receive_id": receive_id, "receive_id_type": receive_id_type},
                content=content,
                text=text,
                message_type=message_type,
                status="placeholder" if placeholder else "sent",
            )
            if placeholder:
                response["placeholder"] = True
                response["hint"] = (
                    f"Native progress card sent — call feishu(action='edit', "
                    f"message_id='{response['message_id']}', "
                    "text=<next meaningful phase>) only when the phase changes. "
                    "Send the final answer separately with send or reply."
                )

            return response
        finally:
            # Always clean up typing indicator, even if send_text or
            # downstream logic throws. For chat_id-type receives we
            # already know the key; for open_id we get it from the result
            # (or fall back to a receive_id-based lookup if send failed
            # before the API returned the real chat_id).
            if chat_id:
                _typing_manager.stop_typing(acct, chat_id)
            else:
                _typing_manager.stop_typing_by_receive(
                    acct, receive_id, receive_id_type,
                )

    @staticmethod
    def _is_outgoing_record(m: dict) -> bool:
        return "to" in m or m.get("status") in {"sent", "placeholder"}

    def _check(self, args: dict) -> dict:
        account = self._resolve_account(args)
        # Merge inbox + sent so post-molt agents see their own replies and
        # don't re-send. Sort newest first so the first entry per chat is
        # the most recent — that drives `last_*` fields.
        inbox = self._list_messages(account, "inbox")
        sent = self._list_messages(account, "sent")
        messages = inbox + sent
        messages.sort(key=lambda m: m.get("date") or m.get("sent_at") or "", reverse=True)
        read_ids = self._read_ids(account)

        conversations: dict[str, dict] = {}
        for msg in messages:
            cid = msg.get("chat_id", "")
            if cid not in conversations:
                if self._is_outgoing_record(msg):
                    last_from_open_id = ""
                    name = "me"
                else:
                    last_from_open_id = msg.get("from_open_id", "")
                    name = self._get_contact_name(account, last_from_open_id)
                conversations[cid] = {
                    "chat_id": cid,
                    "chat_type": msg.get("chat_type", "p2p"),
                    "last_thread_id": msg.get("thread_id"),
                    "last_from_open_id": last_from_open_id,
                    "last_from_name": name,
                    "last_text": (msg.get("text") or "")[:100],
                    "last_date": msg.get("date", ""),
                    "total": 0,
                    "unread": 0,
                }
            conversations[cid]["total"] += 1
            # Only inbound messages can be unread.
            if (
                not self._is_outgoing_record(msg)
                and msg.get("id")
                and msg["id"] not in read_ids
            ):
                conversations[cid]["unread"] += 1

        return {
            "status": "ok",
            "total": len(messages),
            "conversations": list(conversations.values()),
        }

    def _read(self, args: dict) -> dict:
        account = self._resolve_account(args)
        chat_id = args.get("chat_id", "")
        limit = args.get("limit", 10)

        if not chat_id:
            return failure_result(
                "chat_id is required", error_code="INVALID_ARGUMENT",
            )

        # Merge inbox + sent so post-molt agents see their own outgoing
        # replies and avoid duplicate sends.
        inbox = self._list_messages(account, "inbox")
        sent = self._list_messages(account, "sent")
        combined = inbox + sent
        combined.sort(key=lambda m: m.get("date") or m.get("sent_at") or "", reverse=True)
        filtered = [m for m in combined if m.get("chat_id") == chat_id]
        recent = filtered[:limit]

        # Only mark inbound messages as read; sent records have no unread state.
        compound_ids = [
            m["id"] for m in recent if m.get("id") and not self._is_outgoing_record(m)
        ]
        if compound_ids:
            self._mark_read(account, compound_ids)

        cleaned = []
        for m in recent:
            outgoing = self._is_outgoing_record(m)
            name = (
                "me" if outgoing
                else (
                    m.get("from_name")
                    or self._get_contact_name(account, m.get("from_open_id", ""))
                )
            )
            cleaned.append({
                "id": m.get("id"),
                "feishu_message_id": m.get("feishu_message_id"),
                "message_ids": m.get("message_ids"),
                "chunks": m.get("chunks"),
                "chat_id": m.get("chat_id"),
                "chat_type": m.get("chat_type"),
                "thread_id": m.get("thread_id"),
                "from_open_id": m.get("from_open_id"),
                "from_user_id": m.get("from_user_id"),
                "from_union_id": m.get("from_union_id"),
                "from_name": name,
                "sender_type": m.get("sender_type"),
                "sender_is_bot": m.get("sender_is_bot"),
                "to": m.get("to"),
                "message_type": m.get("message_type"),
                "text": m.get("text"),
                "date": m.get("date"),
                "edited_at": m.get("edited_at"),
                "parent_id": m.get("parent_id"),
                "root_id": m.get("root_id"),
                "reply_to": m.get("reply_to"),
                "reply_in_thread": m.get("reply_in_thread"),
                "mentions": m.get("mentions", []),
                "content": m.get("content"),
                "attachments": m.get("attachments", []),
                "media": m.get("media"),
                "voice_transcript": m.get("voice_transcript"),
                "event_type": m.get("event_type"),
                "feishu_event_id": m.get("feishu_event_id"),
                "synthetic": m.get("synthetic", False),
                "source_chat_id": m.get("source_chat_id"),
                "source_message_id": m.get("source_message_id"),
                "source_message_ref": m.get("source_message_ref"),
                "card_action": m.get("card_action"),
                "event": m.get("event"),
                "feishu": m.get("feishu"),
                "_direction": "outgoing" if outgoing else "incoming",
            })

        return {"status": "ok", "messages": cleaned}

    def _reply(self, args: dict) -> dict:
        compound_id = args.get("message_id", "")
        if not compound_id:
            return failure_result(
                "message_id is required", error_code="INVALID_ARGUMENT",
            )
        content, sdk_message, text, message_type = _normalize_outbound_content(args)

        alias, _chat_id, feishu_msg_id = self._parse_compound_id(compound_id)
        acct = self._service.get_account(alias)
        original = self._find_message_record(alias, compound_id)
        original_thread_id = original.get("thread_id") or ""
        if "reply_in_thread" in args:
            reply_in_thread = args["reply_in_thread"]
            if type(reply_in_thread) is not bool:
                raise ValueError("reply_in_thread must be a boolean")
        else:
            reply_in_thread = bool(original_thread_id)

        try:
            result = acct.reply_content(
                feishu_msg_id,
                _chat_id,
                sdk_message,
                reply_in_thread=reply_in_thread,
            )
            if reply_in_thread and not result.get("thread_id"):
                result["thread_id"] = original_thread_id
            response = self._persist_outbound(
                account=alias,
                result=result,
                to={
                    "receive_id": result.get("chat_id") or _chat_id,
                    "receive_id_type": "chat_id",
                },
                content=content,
                text=text,
                message_type=message_type,
                reply_to=compound_id,
                reply_in_thread=reply_in_thread,
            )

            # Rich feedback: Add "done" reaction (THUMBSUP) to the original message
            try:
                acct.add_reaction(feishu_msg_id, REACTION_DONE)
            except Exception as e:
                log.debug("Failed to add 'done' reaction: %s", e)

            return response
        finally:
            # Always clean up typing indicator, even if reply_text or
            # downstream logic throws. Some historical compound IDs can have
            # an empty chat_id segment, leaving no usable cleanup key.
            if not _chat_id:
                log.debug(
                    "Skipping reply typing cleanup with no chat_id for %s",
                    compound_id,
                )
            elif not _typing_manager.stop_typing(acct, _chat_id):
                log.debug(
                    "No reply typing indicator found for %s:%s:%s",
                    alias, _chat_id, feishu_msg_id,
                )

    def _react(self, args: dict) -> dict:
        compound_id = args.get("message_id", "")
        operation = args.get("operation", "")
        if not compound_id:
            return failure_result(
                "message_id is required", error_code="INVALID_ARGUMENT",
            )
        alias, _chat_id, feishu_msg_id = self._parse_compound_id(compound_id)
        acct = self._service.get_account(alias)
        if operation == "add":
            emoji_type = args.get("emoji_type", "")
            if not emoji_type:
                return failure_result(
                    "emoji_type is required for reaction add",
                    error_code="INVALID_ARGUMENT",
                )
            reaction_id = acct.add_reaction_with_id(
                feishu_msg_id, emoji_type,
            )
            return {
                "status": "added",
                "message_id": compound_id,
                "reaction_id": reaction_id,
                "emoji_type": emoji_type,
            }
        if operation == "remove":
            reaction_id = args.get("reaction_id", "")
            if not reaction_id:
                return failure_result(
                    "reaction_id is required for reaction remove",
                    error_code="INVALID_ARGUMENT",
                )
            acct.remove_reaction(feishu_msg_id, reaction_id)
            return {
                "status": "removed",
                "message_id": compound_id,
                "reaction_id": reaction_id,
            }
        return failure_result(
            "operation must be add or remove", error_code="INVALID_ARGUMENT",
        )

    def _search(self, args: dict) -> dict:
        query = args.get("query", "")
        if not query:
            return failure_result(
                "query is required", error_code="INVALID_ARGUMENT",
            )
        account = self._resolve_account(args)
        target_chat = args.get("chat_id", "")

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return failure_result(
                f"Invalid regex: {e}", error_code="INVALID_ARGUMENT",
            )

        messages = self._list_messages(account, "inbox")
        matches = []
        for msg in messages:
            if target_chat and msg.get("chat_id") != target_chat:
                continue
            name = (
                msg.get("from_name")
                or self._get_contact_name(account, msg.get("from_open_id", ""))
            )
            searchable = " ".join([
                msg.get("from_open_id", ""),
                name,
                msg.get("text", ""),
                msg.get("event_type", ""),
                json.dumps(msg.get("event") or {}, ensure_ascii=False, default=str),
            ])
            if pattern.search(searchable):
                matches.append({
                    "id": msg.get("id"),
                    "from_open_id": msg.get("from_open_id"),
                    "from_name": name,
                    "chat_id": msg.get("chat_id"),
                    "thread_id": msg.get("thread_id"),
                    "date": msg.get("date"),
                    "text": msg.get("text"),
                    "parent_id": msg.get("parent_id"),
                    "root_id": msg.get("root_id"),
                    "reply_to": msg.get("reply_to"),
                    "mentions": msg.get("mentions", []),
                    "content": msg.get("content"),
                    "attachments": msg.get("attachments", []),
                    "media": msg.get("media"),
                    "voice_transcript": msg.get("voice_transcript"),
                    "event_type": msg.get("event_type"),
                    "synthetic": msg.get("synthetic", False),
                    "source_chat_id": msg.get("source_chat_id"),
                    "event": msg.get("event"),
                    "feishu": msg.get("feishu"),
                })

        return {"status": "ok", "total": len(matches), "messages": matches}

    def _delete(self, args: dict) -> dict:
        compound_id = args.get("message_id", "")
        if not compound_id:
            return failure_result(
                "message_id is required", error_code="INVALID_ARGUMENT",
            )
        alias, _chat_id, feishu_msg_id = self._parse_compound_id(compound_id)
        acct = self._service.get_account(alias)
        acct.delete_message(feishu_msg_id)
        return {"status": "deleted", "message_id": compound_id}

    def _edit(self, args: dict) -> dict:
        compound_id = args.get("message_id", "")
        if not compound_id:
            return failure_result(
                "message_id is required", error_code="INVALID_ARGUMENT",
            )
        alias, _chat_id, feishu_msg_id = self._parse_compound_id(compound_id)
        record = self._find_message_record(alias, compound_id)
        is_progress = bool(
            record.get("placeholder") or record.get("status") == "placeholder"
        )
        content, sdk_message, text, message_type = _normalize_outbound_content(
            args, editable=True,
        )
        if is_progress:
            if content["type"] not in _PLACEHOLDER_CONTENT_TYPES:
                raise ValueError(
                    "progress card edits only support text, markdown, or post"
                )
            card, text = _native_progress_card(content, text)
            content = {"type": "card", "card": card}
            sdk_message = {"card": card}
            message_type = "interactive"
        acct = self._service.get_account(alias)
        acct.update_content(feishu_msg_id, sdk_message)

        record_dir = record.pop("_dir", "") if record else ""
        if record_dir:
            record.update({
                "content": content,
                "message_type": message_type,
                "text": text,
                "status": "placeholder" if is_progress else "sent",
                "edited_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            })
            if is_progress:
                record["placeholder"] = True
            _write_private_json(
                Path(record_dir) / "message.json",
                record,
                ensure_ascii=False,
            )
        response = {
            "status": "edited",
            "message_id": compound_id,
            "content_type": content["type"],
        }
        if is_progress:
            response.update({
                "placeholder": True,
                "hint": (
                    "Progress card updated. Update again only at a meaningful "
                    "phase change; send the final answer separately with send "
                    "or reply."
                ),
            })
        return response

    def _contacts(self, args: dict) -> dict:
        account = self._resolve_account(args)
        return {"status": "ok", "contacts": self._load_contacts(account)}

    def _add_contact(self, args: dict) -> dict:
        account = self._resolve_account(args)
        open_id = args.get("open_id", "")
        alias = args.get("alias", "")
        if not open_id:
            return failure_result(
                "open_id is required", error_code="INVALID_ARGUMENT",
            )
        if not alias:
            return failure_result(
                "alias is required", error_code="INVALID_ARGUMENT",
            )
        contacts = self._load_contacts(account)
        contacts[open_id] = {
            "alias": alias,
            "name": args.get("name", alias),
            "chat_id": args.get("chat_id", ""),
        }
        self._save_contacts(account, contacts)
        return {"status": "added", "open_id": open_id, "alias": alias}

    def _remove_contact(self, args: dict) -> dict:
        account = self._resolve_account(args)
        open_id = args.get("open_id", "")
        alias = args.get("alias", "")
        contacts = self._load_contacts(account)

        if open_id and open_id in contacts:
            del contacts[open_id]
            self._save_contacts(account, contacts)
            return {"status": "removed", "open_id": open_id}
        elif alias:
            to_remove = [
                oid for oid, v in contacts.items() if v.get("alias") == alias
            ]
            for oid in to_remove:
                del contacts[oid]
            if to_remove:
                self._save_contacts(account, contacts)
                return {"status": "removed", "open_ids": to_remove}
        return failure_result("Contact not found", error_code="NOT_FOUND")

    def _accounts(self) -> dict:
        return {
            "status": "ok",
            "accounts": self._service.list_accounts(),
            "details": self._service.account_details(),
            "identity_path": str(self._service.identity_path()),
        }
