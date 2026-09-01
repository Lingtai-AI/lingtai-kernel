"""Target-only Telegram Agent switching for the owner Bot.

Simple V1 is deliberately narrow: an admitted human in a private chat can select
one verified avatar descendant, route one plain-text message to it, and let that
target reply through the static ``channel_reply`` capability.  The owner Bot
retains every Telegram credential and destination.  Targets receive only the
current body plus an opaque grant/proof pair.

This is cooperative same-UID isolation, not protection from a hostile sibling
process with arbitrary filesystem access.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import logging
import os
import secrets
import stat
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from lingtai.adapters.channel_reply_state_lock import select_channel_reply_state_lock
from lingtai.adapters.posix.agent_presence import PosixAgentPresenceStoreAdapter
from lingtai.kernel.agent_presence import observe_alive
from lingtai.kernel.channel_reply import (
    CAPABILITY_MARKER,
    PROTOCOL_VERSION,
    ChannelReplyFileStore,
    ChannelReplyTargetCapsule,
    OwnerReplyGrant,
)
from lingtai.mcp_servers.telegram.channel_reply import TelegramChannelReplyAdapter
from lingtai.services.mcp_licc import LICC_VERSION, push_inbox_event

log = logging.getLogger(__name__)

_FEATURE_KEY = "agent_switching"
_ROUTE_MCP_NAME = "telegram-agent-switching"
_MENU_PREFIX = "as:"
_MENU_TTL_SECONDS = 300
_MAX_TEXT_CHARS = 4000
_MAX_LEDGER_BYTES = 1_048_576
_MAX_LEDGER_LINES = 4096
_MAX_MANIFEST_BYTES = 65_536
_MAX_STATE_BYTES = 65_536
_DRAIN_INTERVAL_SECONDS = 0.5
_CLEANUP_INTERVAL_SECONDS = 300.0
_CLEANUP_BUDGET = 128
_RETENTION_SECONDS = 7 * 24 * 60 * 60
_EDIT_UNSUPPORTED_TEXT = "Edited messages are not supported for Agent routing."
_AGENT_COMMAND = {"command": "agent", "description": "Choose the target Agent"}
_FORWARDED_MESSAGE_FIELDS = frozenset(
    {
        "forward_origin",
        "forward_from",
        "forward_from_chat",
        "forward_from_message_id",
        "forward_signature",
        "forward_sender_name",
        "forward_date",
        "is_automatic_forward",
    }
)


@dataclass(frozen=True, slots=True)
class AgentDirective:
    kind: str
    name: str | None = None
    body: str | None = None


@dataclass(frozen=True, slots=True)
class EligibleTarget:
    name: str
    agent_id: str
    workdir: Path
    manifest_digest: str
    ledger_chain_digest: str
    protocol_version: int = PROTOCOL_VERSION


def _admin_text(text: str) -> str:
    return f"[admin] {text}"


class _CleanupEnumerationFailure(OSError):
    """Operational enumeration failure with conservatively known charged work."""

    def __init__(self, cause: BaseException, *, charged: int) -> None:
        super().__init__(str(cause))
        self.charged = max(0, int(charged))
        self.__cause__ = cause


@dataclass(frozen=True, slots=True)
class SelectionLoadResult:
    """Explicit persisted-selection truth at the routing boundary."""

    status: str
    record: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"absent", "valid", "unavailable"}:
            raise ValueError("invalid_selection_status")
        if (self.status == "valid") != (self.record is not None):
            raise ValueError("invalid_selection_result")


@dataclass(frozen=True, slots=True)
class OriginalOwnershipLoadResult:
    """Body-free durable truth for one original Telegram message identity."""

    status: str

    def __post_init__(self) -> None:
        if self.status not in {"absent", "owned", "unavailable"}:
            raise ValueError("invalid_original_ownership_status")


# Executable state inventory.  Proof-free no-remint decisions are permanent;
# picker files and raw quarantine are cadence-cleaned; unavailable tombstones
# remain until explicit reset or a valid replacement selection.
TELEGRAM_AGENT_SWITCHING_STATE_INVENTORY = (
    ("owner", "state/selections/*.json", "atomic-replace", "until-reset-or-reselection"),
    ("owner", "state/selection-unavailable/*.json", "atomic-create-once", "until-reset-or-reselection"),
    ("owner", "state/original-ownership/*.json", "atomic-create/replace", "seven-days"),
    ("owner", "state/edit-rejections/*.json", "atomic-create/replace", "seven-days"),
    ("owner", "state/menus/*.json", "atomic-replace", "until-expiry"),
    ("owner", "state/.dead/*.dead", "atomic-replace-quarantine", "seven-days"),
    ("owner", "state/cleanup-progress.json", "atomic-replace", "durable-fairness-cursor"),
    ("target", ".telegram-agent-switching/router-decisions/*.json", "atomic-create/replace", "permanent-no-republish"),
)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return _format_utc(datetime.now(timezone.utc))


def _future(seconds: int, *, base: str | None = None) -> str:
    origin = _parse_utc(base) if base is not None else datetime.now(timezone.utc)
    if origin is None:
        raise ValueError("invalid_base_timestamp")
    return _format_utc(origin + timedelta(seconds=seconds))


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_agent_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in value)
    )


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def account_switching_enabled(config: Mapping[str, Any]) -> bool:
    raw = config.get(_FEATURE_KEY)
    if raw is True:
        return True
    return isinstance(raw, Mapping) and raw.get("enabled") is True


def compose_agent_commands(
    commands: Sequence[Mapping[str, Any]] | None,
    *,
    enabled: bool,
    defaults: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]] | None:
    """Compose one ``/agent`` command without changing disabled semantics.

    ``None`` means use defaults.  Explicit ``[]`` remains an explicit empty
    command menu.  A conflicting custom ``agent`` description is preserved
    unchanged; typed ``/agent`` is still handled locally.
    """
    if not enabled:
        if commands is None:
            return None
        return [dict(item) for item in commands]
    base = [dict(item) for item in defaults] if commands is None else [dict(item) for item in commands]
    if not base:
        return []
    existing = [item for item in base if item.get("command") == "agent"]
    if existing:
        return base
    base.append(dict(_AGENT_COMMAND))
    return base


def prepare_account_configs(
    accounts: Sequence[Mapping[str, Any]],
    *,
    default_commands: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in accounts:
        cfg = dict(raw)
        if account_switching_enabled(cfg):
            existing = cfg.get("commands") if "commands" in cfg else None
            cfg["commands"] = compose_agent_commands(
                existing,
                enabled=True,
                defaults=default_commands,
            )
        result.append(cfg)
    return result


def _is_start_command(text: object, *, bot_username: str | None = None) -> bool:
    """Recognize only Telegram's exact local /start command forms."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped.split(maxsplit=1)[0]
    if first == "/start":
        return True
    command, marker, addressed = first.partition("@")
    return (
        command == "/start"
        and bool(marker and addressed and bot_username)
        and addressed.casefold() == bot_username.lstrip("@").casefold()
    )


def parse_agent_text(text: object, *, bot_username: str | None = None) -> AgentDirective:
    if not isinstance(text, str):
        return AgentDirective("ordinary")
    stripped = text.strip()
    if not stripped:
        return AgentDirective("ordinary")
    if stripped == "@":
        return AgentDirective("list")

    # Python's no-separator split follows the same Unicode whitespace boundary
    # as ``strip``.  It removes only the outer selector/command delimiter run;
    # ordinary text is returned byte-for-byte and selector bodies retain all
    # internal whitespace.
    parts = stripped.split(maxsplit=1)
    first = parts[0]
    has_body = len(parts) == 2
    remainder = parts[1] if has_body else ""
    if first.startswith("/agent"):
        command, marker, addressed = first.partition("@")
        # Every /agent... prefix is switching-control syntax. Malformed command
        # names (including /agentx) fail locally rather than leaking to an Agent.
        if command != "/agent":
            return AgentDirective("invalid")
        if marker:
            if (
                not addressed
                or not bot_username
                or addressed.casefold() != bot_username.lstrip("@").casefold()
            ):
                return AgentDirective("invalid")
        arg = remainder.strip() if has_body else ""
        if not arg:
            return AgentDirective("list")
        if arg == "status":
            return AgentDirective("status")
        if arg == "reset":
            return AgentDirective("reset")
        return AgentDirective("invalid")

    if not first.startswith("@"):
        return AgentDirective("ordinary", body=text)
    name = first[1:]
    if name == "current" and not has_body:
        return AgentDirective("status")
    if name in {"admin", "default"} and not has_body:
        return AgentDirective("reset")
    if not _valid_agent_name(name):
        return AgentDirective("invalid")
    if not has_body:
        return AgentDirective("select", name=name)
    body = remainder.strip()
    if not body:
        return AgentDirective("invalid")
    return AgentDirective("route_once", name=name, body=body)


def is_supported_agent_switching_message(message: object) -> bool:
    """Return whether one Telegram message is routable plain text in Simple V1.

    The one predicate owns both the non-empty text rule and all current/legacy
    Bot API forwarding evidence.  Callers decide whether unsupported input is a
    local switching error or remains ordinary admin traffic when no directive or
    saved selection applies.
    """
    if not isinstance(message, Mapping):
        return False
    text = message.get("text")
    return (
        isinstance(text, str)
        and bool(text.strip())
        and _FORWARDED_MESSAGE_FIELDS.isdisjoint(message)
    )


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise OSError("unsafe_state_directory")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise OSError("foreign_state_directory")
    os.chmod(path, 0o700)


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise OSError("unsafe_state_directory")
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("short_state_write")
        offset += written


def _encode_private_json(record: Mapping[str, Any]) -> bytes:
    data = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(data) > _MAX_STATE_BYTES:
        raise ValueError("state_record_too_large")
    return data


def _atomic_private_json(path: Path, record: Mapping[str, Any]) -> None:
    _private_dir(path.parent)
    data = _encode_private_json(record)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    fd: int | None = None
    try:
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("unsafe_state_temp")
        os.close(fd)
        fd = None
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _create_private_json_once(path: Path, record: Mapping[str, Any]) -> bool:
    """Durably create one canonical record without replacing an occupant."""
    _private_dir(path.parent)
    data = _encode_private_json(record)
    fd: int | None = None
    try:
        try:
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            return False
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("unsafe_state_record")
        os.close(fd)
        fd = None
        _fsync_dir(path.parent)
        return True
    except Exception:
        if fd is not None:
            os.close(fd)
            fd = None
        try:
            path.unlink()
            _fsync_dir(path.parent)
        except OSError:
            pass
        raise


def _read_private_json(path: Path) -> dict[str, Any] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or st.st_size > _MAX_STATE_BYTES
            or hasattr(os, "getuid")
            and st.st_uid != os.getuid()
        ):
            raise ValueError("unsafe_state_record")
        chunks: list[bytes] = []
        remaining = _MAX_STATE_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if len(raw) > _MAX_STATE_BYTES:
        raise ValueError("state_record_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("bad_state_record")
    return value


def _quarantine(path: Path, dead_dir: Path) -> None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or hasattr(os, "getuid")
        and before.st_uid != os.getuid()
    ):
        return
    _private_dir(dead_dir)
    try:
        after = path.lstat()
    except FileNotFoundError:
        return
    if (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size) != (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
        before.st_size,
    ):
        return
    target = dead_dir / f"{path.name}.{uuid.uuid4().hex}.dead"
    try:
        os.replace(path, target)
        _fsync_dir(path.parent)
        if dead_dir != path.parent:
            _fsync_dir(dead_dir)
    except OSError:
        return


def _identity_digest(*parts: object) -> str:
    material = "\0".join(str(part) for part in parts).encode()
    return hashlib.sha256(material).hexdigest()


class AgentSwitchingStateStore:
    """Small owner-local durable state; never stores routed message bodies."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._selections = self.root / "selections"
        self._selection_unavailable = self.root / "selection-unavailable"
        self._original_ownership = self.root / "original-ownership"
        self._edit_rejections = self.root / "edit-rejections"
        self._menus = self.root / "menus"
        self._dead = self.root / ".dead"
        self._cleanup_progress = self.root / "cleanup-progress.json"
        self._lock = threading.RLock()
        for directory in (
            self.root,
            self._selections,
            self._selection_unavailable,
            self._original_ownership,
            self._edit_rejections,
            self._menus,
            self._dead,
        ):
            _private_dir(directory)

    @staticmethod
    def _selection_key(account_alias: str, chat_id: int, user_id: int) -> str:
        return _identity_digest("selection-v1", account_alias, chat_id, user_id)

    def _selection_path(self, account_alias: str, chat_id: int, user_id: int) -> Path:
        return self._selections / f"{self._selection_key(account_alias, chat_id, user_id)}.json"

    def _selection_unavailable_path(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
    ) -> Path:
        key = self._selection_key(account_alias, chat_id, user_id)
        return self._selection_unavailable / f"{key}.json"

    @staticmethod
    def _original_ownership_key(
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
    ) -> str:
        return _identity_digest(
            "original-ownership-v1",
            account_alias,
            chat_id,
            user_id,
            message_id,
        )

    def _original_ownership_path(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
    ) -> Path:
        key = self._original_ownership_key(
            account_alias, chat_id, user_id, message_id
        )
        return self._original_ownership / f"{key}.json"

    @staticmethod
    def _validate_original_ownership_record(
        record: Mapping[str, Any],
        *,
        identity_digest: str,
        retention_seconds: int,
    ) -> tuple[datetime, datetime]:
        if set(record) != {
            "version", "identity_digest", "created_at", "expires_at",
        }:
            raise ValueError("bad_original_ownership_fields")
        created = _parse_utc(record.get("created_at"))
        expires = _parse_utc(record.get("expires_at"))
        if (
            record.get("version") != 1
            or record.get("identity_digest") != identity_digest
            or created is None
            or expires is None
            or expires != created + timedelta(seconds=max(0, int(retention_seconds)))
        ):
            raise ValueError("bad_original_ownership_record")
        return created, expires

    def read_original_ownership(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
        *,
        now: str,
        retention_seconds: int = _RETENTION_SECONDS,
    ) -> OriginalOwnershipLoadResult:
        """Return absent, owned, or unavailable without scanning or fail-open repair."""
        current = _parse_utc(now)
        if current is None:
            raise ValueError("invalid_ownership_timestamp")
        key = self._original_ownership_key(
            account_alias, chat_id, user_id, message_id
        )
        path = self._original_ownership_path(
            account_alias, chat_id, user_id, message_id
        )
        with self._lock:
            try:
                record = _read_private_json(path)
                if record is None:
                    return OriginalOwnershipLoadResult("absent")
                created, expires = self._validate_original_ownership_record(
                    record,
                    identity_digest=key,
                    retention_seconds=retention_seconds,
                )
                if created > current:
                    return OriginalOwnershipLoadResult("unavailable")
                if expires <= current:
                    return OriginalOwnershipLoadResult("absent")
                return OriginalOwnershipLoadResult("owned")
            except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
                # Preserve malformed/inaccessible evidence in place. Quarantine or
                # best-effort repair would turn an owned message into apparent absence.
                return OriginalOwnershipLoadResult("unavailable")

    def claim_original_ownership(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
        *,
        now: str,
        retention_seconds: int = _RETENTION_SECONDS,
    ) -> None:
        """Commit exact body-free ownership before any target-visible route state."""
        current = _parse_utc(now)
        if current is None:
            raise ValueError("invalid_ownership_timestamp")
        retention = max(0, int(retention_seconds))
        key = self._original_ownership_key(
            account_alias, chat_id, user_id, message_id
        )
        path = self._original_ownership_path(
            account_alias, chat_id, user_id, message_id
        )
        record = {
            "version": 1,
            "identity_digest": key,
            "created_at": now,
            "expires_at": _future(retention, base=now),
        }
        with self._lock:
            if _create_private_json_once(path, record):
                return
            existing = _read_private_json(path)
            if existing is None:
                raise OSError("original_ownership_disappeared")
            created, expires = self._validate_original_ownership_record(
                existing,
                identity_digest=key,
                retention_seconds=retention,
            )
            if created > current:
                raise ValueError("future_original_ownership")
            if expires <= current:
                _atomic_private_json(path, record)

    @staticmethod
    def _edit_rejection_key(
        account_alias: str,
        update_id: int,
        chat_id: int,
        user_id: int,
        message_id: int,
    ) -> str:
        return _identity_digest(
            "edit-rejection-v1",
            account_alias,
            update_id,
            chat_id,
            user_id,
            message_id,
        )

    def _edit_rejection_path(
        self,
        account_alias: str,
        update_id: int,
        chat_id: int,
        user_id: int,
        message_id: int,
    ) -> Path:
        key = self._edit_rejection_key(
            account_alias, update_id, chat_id, user_id, message_id
        )
        return self._edit_rejections / f"{key}.json"

    @staticmethod
    def _validate_edit_rejection_record(
        record: Mapping[str, Any],
        *,
        event_digest: str,
        retention_seconds: int,
    ) -> tuple[datetime, datetime]:
        if set(record) != {
            "version", "event_digest", "created_at", "expires_at",
        }:
            raise ValueError("bad_edit_rejection_fields")
        created = _parse_utc(record.get("created_at"))
        expires = _parse_utc(record.get("expires_at"))
        if (
            record.get("version") != 1
            or record.get("event_digest") != event_digest
            or created is None
            or expires is None
            or expires != created + timedelta(seconds=max(0, int(retention_seconds)))
        ):
            raise ValueError("bad_edit_rejection_record")
        return created, expires

    def reserve_edit_rejection(
        self,
        account_alias: str,
        update_id: object,
        chat_id: int,
        user_id: int,
        message_id: int,
        *,
        now: str,
        retention_seconds: int = _RETENTION_SECONDS,
    ) -> str:
        """Return new/existing/unavailable for one exact body-free edit decision."""
        current = _parse_utc(now)
        if current is None or not _is_positive_int(update_id):
            return "unavailable"
        retention = max(0, int(retention_seconds))
        numeric_update_id = int(update_id)
        key = self._edit_rejection_key(
            account_alias, numeric_update_id, chat_id, user_id, message_id
        )
        path = self._edit_rejection_path(
            account_alias, numeric_update_id, chat_id, user_id, message_id
        )
        record = {
            "version": 1,
            "event_digest": key,
            "created_at": now,
            "expires_at": _future(retention, base=now),
        }
        try:
            with self._lock:
                if _create_private_json_once(path, record):
                    return "new"
                existing = _read_private_json(path)
                if existing is None:
                    return "unavailable"
                created, expires = self._validate_edit_rejection_record(
                    existing,
                    event_digest=key,
                    retention_seconds=retention,
                )
                if created > current:
                    return "unavailable"
                if expires <= current:
                    _atomic_private_json(path, record)
                    return "new"
                return "existing"
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            # At-most-once local rejection prefers possible silence over a duplicate
            # after ambiguous/corrupt state. Privacy still fails closed as handled.
            return "unavailable"

    @staticmethod
    def _validate_selection_record(
        record: Mapping[str, Any],
        *,
        account_alias: str,
        chat_id: int,
        user_id: int,
    ) -> None:
        if set(record) != {
            "version", "account_alias", "chat_id", "user_id",
            "target_name", "target_agent_id", "manifest_digest",
            "ledger_chain_digest", "protocol_version", "updated_at",
        }:
            raise ValueError("bad_selection_fields")
        if (
            record.get("version") != 1
            or record.get("account_alias") != account_alias
            or record.get("chat_id") != chat_id
            or record.get("user_id") != user_id
            or not _valid_agent_name(record.get("target_name"))
            or not isinstance(record.get("target_agent_id"), str)
            or not record.get("target_agent_id")
            or not _valid_digest(record.get("manifest_digest"))
            or not _valid_digest(record.get("ledger_chain_digest"))
            or record.get("protocol_version") != PROTOCOL_VERSION
            or _parse_utc(record.get("updated_at")) is None
        ):
            raise ValueError("bad_selection_record")

    def _write_selection_unavailable(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
    ) -> bool:
        path = self._selection_unavailable_path(account_alias, chat_id, user_id)
        return _create_private_json_once(
            path,
            {
                "version": 1,
                "selection_key": self._selection_key(account_alias, chat_id, user_id),
                "unavailable_at": _utc_now(),
            },
        )

    def _read_selection_unavailable(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
    ) -> bool:
        record = _read_private_json(
            self._selection_unavailable_path(account_alias, chat_id, user_id)
        )
        if record is None:
            return False
        if (
            set(record) != {"version", "selection_key", "unavailable_at"}
            or record.get("version") != 1
            or record.get("selection_key")
            != self._selection_key(account_alias, chat_id, user_id)
            or _parse_utc(record.get("unavailable_at")) is None
        ):
            raise ValueError("bad_selection_unavailable_record")
        return True

    def read_selection(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
    ) -> SelectionLoadResult:
        """Return absent, valid, or unavailable without fail-open conflation."""
        path = self._selection_path(account_alias, chat_id, user_id)
        with self._lock:
            try:
                record = _read_private_json(path)
                if record is not None:
                    self._validate_selection_record(
                        record,
                        account_alias=account_alias,
                        chat_id=chat_id,
                        user_id=user_id,
                    )
                    # A valid canonical atomic replacement is authoritative over
                    # any stale unavailability tombstone left by interrupted repair.
                    return SelectionLoadResult("valid", record)
                if self._read_selection_unavailable(account_alias, chat_id, user_id):
                    return SelectionLoadResult("unavailable")
                return SelectionLoadResult("absent")
            except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
                # Persist fail-closed truth before removing readable corrupt bytes.
                # If the marker cannot be committed, leave the canonical occupant
                # in place so the next read still fails unavailable rather than
                # falling through to admin as an apparently absent selection.
                marked = False
                try:
                    marked = self._write_selection_unavailable(
                        account_alias, chat_id, user_id
                    )
                except (OSError, ValueError):
                    pass
                if marked or self._selection_unavailable_path(
                    account_alias, chat_id, user_id
                ).exists():
                    _quarantine(path, self._dead)
                return SelectionLoadResult("unavailable")

    def load_selection(self, account_alias: str, chat_id: int, user_id: int) -> dict[str, Any] | None:
        """Compatibility view; routing code must use explicit ``read_selection``."""
        result = self.read_selection(account_alias, chat_id, user_id)
        return dict(result.record) if result.status == "valid" and result.record is not None else None

    def save_selection(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
        target: EligibleTarget,
    ) -> None:
        record = {
            "version": 1,
            "account_alias": account_alias,
            "chat_id": chat_id,
            "user_id": user_id,
            "target_name": target.name,
            "target_agent_id": target.agent_id,
            "manifest_digest": target.manifest_digest,
            "ledger_chain_digest": target.ledger_chain_digest,
            "protocol_version": target.protocol_version,
            "updated_at": _utc_now(),
        }
        with self._lock:
            _atomic_private_json(self._selection_path(account_alias, chat_id, user_id), record)
            marker = self._selection_unavailable_path(account_alias, chat_id, user_id)
            try:
                marker.unlink()
                _fsync_dir(marker.parent)
            except FileNotFoundError:
                pass
            except OSError:
                # The validated canonical replacement is authoritative. A stale
                # marker is ignored by reads and can be retried by reset/reselect.
                pass

    def clear_selection(self, account_alias: str, chat_id: int, user_id: int) -> bool:
        paths = (
            self._selection_path(account_alias, chat_id, user_id),
            self._selection_unavailable_path(account_alias, chat_id, user_id),
        )
        removed = False
        failures: list[OSError] = []
        with self._lock:
            for path in paths:
                try:
                    path.unlink()
                    _fsync_dir(path.parent)
                    removed = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    failures.append(exc)
        if failures:
            raise OSError("selection_reset_incomplete") from failures[0]
        return removed

    def create_menu(
        self,
        *,
        account_alias: str,
        chat_id: int,
        user_id: int,
        targets: Sequence[EligibleTarget],
        page: int,
    ) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(18)
        record = {
            "version": 1,
            "account_alias": account_alias,
            "chat_id": chat_id,
            "user_id": user_id,
            "bot_message_id": None,
            "page": page,
            "targets": [
                {
                    "name": item.name,
                    "agent_id": item.agent_id,
                    "manifest_digest": item.manifest_digest,
                    "ledger_chain_digest": item.ledger_chain_digest,
                    "protocol_version": item.protocol_version,
                }
                for item in targets
            ],
            "decision": "active",
            "selected_index": None,
            "selected_at": None,
            "result_text": None,
            "created_at": _utc_now(),
            "expires_at": _future(_MENU_TTL_SECONDS),
        }
        with self._lock:
            _atomic_private_json(self._menus / f"{token}.json", record)
        return token, record

    def bind_menu_message(self, token: str, bot_message_id: int) -> dict[str, Any] | None:
        with self._lock:
            record = self.load_menu(token)
            if record is None:
                return None
            record["bot_message_id"] = bot_message_id
            _atomic_private_json(self._menus / f"{token}.json", record)
            return record

    def save_menu(self, token: str, record: Mapping[str, Any]) -> None:
        with self._lock:
            _atomic_private_json(self._menus / f"{token}.json", dict(record))

    def load_menu(self, token: str) -> dict[str, Any] | None:
        if not isinstance(token, str) or len(token) > 40 or not token:
            return None
        if not all(ch.isalnum() or ch in "_-" for ch in token):
            return None
        path = self._menus / f"{token}.json"
        with self._lock:
            try:
                record = _read_private_json(path)
                if record is None:
                    return None
                expected = {
                    "version", "account_alias", "chat_id", "user_id", "bot_message_id",
                    "page", "targets", "decision", "selected_index", "selected_at",
                    "result_text", "created_at", "expires_at",
                }
                if set(record) != expected or record.get("version") != 1:
                    raise ValueError("bad_menu_fields")
                if (
                    not isinstance(record.get("account_alias"), str)
                    or not _is_positive_int(record.get("chat_id"))
                    or not _is_positive_int(record.get("user_id"))
                    or record.get("bot_message_id") is not None
                    and not _is_positive_int(record.get("bot_message_id"))
                    or not isinstance(record.get("page"), int)
                    or isinstance(record.get("page"), bool)
                    or record.get("page") < 0
                    or not isinstance(record.get("targets"), list)
                    or record.get("decision") not in {"active", "selected"}
                    or record.get("selected_index") is not None
                    and (
                        not isinstance(record.get("selected_index"), int)
                        or isinstance(record.get("selected_index"), bool)
                        or record.get("selected_index") < 0
                    )
                    or record.get("selected_at") is not None
                    and _parse_utc(record.get("selected_at")) is None
                    or record.get("result_text") is not None
                    and not isinstance(record.get("result_text"), str)
                    or _parse_utc(record.get("created_at")) is None
                    or _parse_utc(record.get("expires_at")) is None
                ):
                    raise ValueError("bad_menu_record")
                if record["decision"] == "active" and any(
                    record[key] is not None
                    for key in ("selected_index", "selected_at", "result_text")
                ):
                    raise ValueError("active_menu_has_terminal_fields")
                if record["decision"] == "selected" and (
                    record["selected_index"] is None
                    or record["selected_index"] >= len(record["targets"])
                    or record["selected_at"] is None
                    or not record["result_text"]
                ):
                    raise ValueError("selected_menu_missing_terminal_fields")
                for item in record["targets"]:
                    if (
                        not isinstance(item, dict)
                        or set(item) != {
                            "name", "agent_id", "manifest_digest",
                            "ledger_chain_digest", "protocol_version",
                        }
                        or not _valid_agent_name(item.get("name"))
                        or not isinstance(item.get("agent_id"), str)
                        or not item.get("agent_id")
                        or not _valid_digest(item.get("manifest_digest"))
                        or not _valid_digest(item.get("ledger_chain_digest"))
                        or item.get("protocol_version") != PROTOCOL_VERSION
                    ):
                        raise ValueError("bad_menu_target")
                return record
            except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
                _quarantine(path, self._dead)
                return None

    @staticmethod
    def _default_cleanup_progress() -> dict[str, Any]:
        return {
            "version": 4,
            "next_class": "menus",
            "menus_cursor": {"position": 0, "pending": []},
            "dead_cursor": {"position": 0, "pending": []},
            "original_ownership_cursor": {"position": 0, "pending": []},
            "edit_rejections_cursor": {"position": 0, "pending": []},
            "targets_after": "",
        }

    @staticmethod
    def _validate_enumeration_cursor(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {"position", "pending"}:
            raise ValueError("bad_cleanup_cursor_shape")
        position = value.get("position")
        pending = value.get("pending")
        if (
            not isinstance(position, int)
            or isinstance(position, bool)
            or position < 0
            or not isinstance(pending, list)
            or len(pending) > 32
            or any(not isinstance(name, str) or not name or len(name) > 255 for name in pending)
        ):
            raise ValueError("bad_cleanup_cursor")
        return {"position": position, "pending": list(pending)}

    def _read_cleanup_progress(self) -> dict[str, Any]:
        default = self._default_cleanup_progress()
        try:
            record = _read_private_json(self._cleanup_progress)
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            _quarantine(self._cleanup_progress, self._dead)
            return default
        if record is None:
            return default
        try:
            expected = {
                "version", "next_class", "menus_cursor", "dead_cursor",
                "original_ownership_cursor", "edit_rejections_cursor",
                "targets_after",
            }
            if (
                set(record) != expected
                or record.get("version") != 4
                or record.get("next_class")
                not in {"menus", "dead", "original_ownership", "edit_rejections"}
                or not isinstance(record.get("targets_after"), str)
            ):
                raise ValueError("bad_cleanup_progress")
            return {
                "version": 4,
                "next_class": record["next_class"],
                "menus_cursor": self._validate_enumeration_cursor(record["menus_cursor"]),
                "dead_cursor": self._validate_enumeration_cursor(record["dead_cursor"]),
                "original_ownership_cursor": self._validate_enumeration_cursor(
                    record["original_ownership_cursor"]
                ),
                "edit_rejections_cursor": self._validate_enumeration_cursor(
                    record["edit_rejections_cursor"]
                ),
                "targets_after": record["targets_after"],
            }
        except (TypeError, ValueError):
            _quarantine(self._cleanup_progress, self._dead)
            return default

    def select_cleanup_target_ids(self, agent_ids: Sequence[str], *, max_items: int) -> list[str]:
        """Durably rotate current target roots across insertion/removal/restart."""
        keys = sorted(set(agent_ids))
        limit = max(0, int(max_items))
        if not keys or limit == 0:
            return []
        with self._lock:
            progress = self._read_cleanup_progress()
            after = progress["targets_after"]
            ordered = [key for key in keys if key > after] + [key for key in keys if key <= after]
            selected = ordered[:limit]
            progress["targets_after"] = selected[-1]
            _atomic_private_json(self._cleanup_progress, progress)
            return selected

    @staticmethod
    def _bounded_names(
        directory: Path,
        *,
        suffix: str,
        cursor: Mapping[str, Any],
        inspections: int,
    ) -> tuple[list[str], dict[str, Any], int]:
        """Return one Darwin directory-cookie page without materializing the directory."""
        limit = max(0, int(inspections))
        position = int(cursor["position"])
        pending = list(cursor["pending"])
        selected: list[str] = []
        charged = 0
        if limit == 0:
            return selected, {"position": position, "pending": pending}, charged
        if sys.platform != "darwin":
            raise OSError("bounded switching-state enumeration requires Darwin")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(directory, flags)
        try:
            opened = os.fstat(fd)
            named = os.stat(directory, follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != 0o700
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                or hasattr(os, "geteuid") and opened.st_uid != os.geteuid()
            ):
                raise OSError("switching cleanup directory identity invalid")
            os.lseek(fd, position, os.SEEK_SET)
            libc = ctypes.CDLL(None, use_errno=True)
            primitive = getattr(libc, "getdirentries", None)
            if primitive is None:
                raise OSError(errno.ENOSYS, "getdirentries unavailable")
            primitive.argtypes = [
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_longlong),
            ]
            primitive.restype = ctypes.c_int
            eof = False
            while charged < limit:
                if not pending:
                    buffer = ctypes.create_string_buffer(272)
                    base = ctypes.c_longlong(position)
                    ctypes.set_errno(0)
                    count = primitive(fd, buffer, len(buffer), ctypes.byref(base))
                    if count < 0:
                        code = ctypes.get_errno() or errno.EIO
                        raise OSError(code, os.strerror(code))
                    position = os.lseek(fd, 0, os.SEEK_CUR)
                    if count == 0:
                        eof = True
                        break
                    offset = 0
                    raw = buffer.raw[:count]
                    while offset + 8 <= len(raw):
                        _ino, record_length, _kind, name_length = struct.unpack_from(
                            "=I H B B", raw, offset
                        )
                        if record_length < 8 or offset + record_length > len(raw):
                            raise OSError(errno.EIO, "invalid Darwin directory record")
                        name = os.fsdecode(raw[offset + 8 : offset + 8 + name_length])
                        if name not in {".", ".."}:
                            pending.append(name)
                        offset += record_length
                    if offset != len(raw):
                        raise OSError(errno.EIO, "truncated Darwin directory record")
                    if not pending:
                        continue
                name = pending.pop(0)
                charged += 1
                if name.endswith(suffix):
                    selected.append(name)
            if eof and not pending:
                position = 0
            return selected, {"position": position, "pending": pending}, charged
        except (OSError, ValueError, UnicodeError) as exc:
            raise _CleanupEnumerationFailure(exc, charged=charged) from exc
        finally:
            os.close(fd)

    def cleanup_retained(
        self,
        *,
        now: str,
        retention_seconds: int = _RETENTION_SECONDS,
        max_items: int = _CLEANUP_BUDGET,
    ) -> int:
        """Fairly bound menu/quarantine/ownership/edit enumeration with durable cookies."""
        current = _parse_utc(now)
        if current is None:
            raise ValueError("invalid_cleanup_timestamp")
        retention = max(0, int(retention_seconds))
        budget = max(0, int(max_items))
        removed = 0
        cutoff = current.timestamp() - retention
        class_order = ("menus", "dead", "original_ownership", "edit_rejections")
        with self._lock:
            progress = self._read_cleanup_progress()
            first_index = class_order.index(progress["next_class"])
            classes = class_order[first_index:] + class_order[:first_index]
            quotas = {name: 0 for name in class_order}
            for index in range(budget):
                quotas[classes[index % len(classes)]] += 1
            progress["next_class"] = classes[budget % len(classes)]

            for class_name in classes:
                quota = quotas[class_name]
                if quota <= 0:
                    continue
                if class_name == "menus":
                    suffix = ".json"
                    directory = self._menus
                    cursor_key = "menus_cursor"
                elif class_name == "dead":
                    suffix = ".dead"
                    directory = self._dead
                    cursor_key = "dead_cursor"
                elif class_name == "original_ownership":
                    suffix = ".json"
                    directory = self._original_ownership
                    cursor_key = "original_ownership_cursor"
                else:
                    suffix = ".json"
                    directory = self._edit_rejections
                    cursor_key = "edit_rejections_cursor"
                try:
                    names, next_cursor, charged = self._bounded_names(
                        directory,
                        suffix=suffix,
                        cursor=progress[cursor_key],
                        inspections=quota,
                    )
                    if charged < 0 or charged > quota:
                        raise OSError("switching cleanup enumeration exceeded class quota")
                except (OSError, ValueError, UnicodeError) as exc:
                    # A failed class reserves all assigned work and never lends
                    # unknowable partial budget to another retention class.
                    known_charged = getattr(exc, "charged", 0)
                    if (
                        not isinstance(known_charged, int)
                        or isinstance(known_charged, bool)
                        or known_charged < 0
                        or known_charged > quota
                    ):
                        raise OSError("switching cleanup failure charge invalid") from exc
                    continue
                progress[cursor_key] = next_cursor
                for name in names:
                    path = directory / name
                    try:
                        if class_name == "menus":
                            token = name[:-5]
                            if not token or not all(
                                ch.isascii() and (ch.isalnum() or ch in "_-") for ch in token
                            ):
                                continue
                            record = self.load_menu(token)
                            if record is None:
                                continue
                            expires = _parse_utc(record.get("expires_at"))
                            if expires is not None and expires <= current:
                                try:
                                    path.unlink()
                                    _fsync_dir(path.parent)
                                    removed += 1
                                except FileNotFoundError:
                                    pass
                            continue
                        if class_name in {"original_ownership", "edit_rejections"}:
                            key = name[:-5]
                            if not _valid_digest(key):
                                continue
                            before = path.lstat()
                            if (
                                not stat.S_ISREG(before.st_mode)
                                or before.st_nlink != 1
                                or hasattr(os, "getuid") and before.st_uid != os.getuid()
                            ):
                                continue
                            record = _read_private_json(path)
                            if record is None:
                                continue
                            if class_name == "original_ownership":
                                _created, expires = self._validate_original_ownership_record(
                                    record,
                                    identity_digest=key,
                                    retention_seconds=retention,
                                )
                            else:
                                _created, expires = self._validate_edit_rejection_record(
                                    record,
                                    event_digest=key,
                                    retention_seconds=retention,
                                )
                            if expires > current:
                                continue
                            after_stat = path.lstat()
                            if (
                                before.st_dev,
                                before.st_ino,
                                before.st_mtime_ns,
                                before.st_size,
                            ) != (
                                after_stat.st_dev,
                                after_stat.st_ino,
                                after_stat.st_mtime_ns,
                                after_stat.st_size,
                            ):
                                continue
                            path.unlink()
                            _fsync_dir(path.parent)
                            removed += 1
                            continue
                        before = path.lstat()
                        if (
                            not stat.S_ISREG(before.st_mode)
                            or before.st_nlink != 1
                            or hasattr(os, "getuid") and before.st_uid != os.getuid()
                            or before.st_mtime > cutoff
                        ):
                            continue
                        after_stat = path.lstat()
                        if (before.st_dev, before.st_ino) != (after_stat.st_dev, after_stat.st_ino):
                            continue
                        path.unlink()
                        _fsync_dir(path.parent)
                        removed += 1
                    except FileNotFoundError:
                        pass
                    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                        # Preserve unsafe/corrupt ownership evidence so a later edit
                        # remains unavailable rather than silently becoming admin.
                        continue
            _atomic_private_json(self._cleanup_progress, progress)
        return removed

    @staticmethod
    def _menu_binding_matches(
        record: Mapping[str, Any],
        *,
        account_alias: str,
        chat_id: int,
        user_id: int,
        bot_message_id: int,
    ) -> bool:
        expires = _parse_utc(record.get("expires_at"))
        return (
            record.get("account_alias") == account_alias
            and record.get("chat_id") == chat_id
            and record.get("user_id") == user_id
            and record.get("bot_message_id") == bot_message_id
            and expires is not None
            and expires > datetime.now(timezone.utc)
        )

    def set_menu_page(
        self,
        token: str,
        *,
        account_alias: str,
        chat_id: int,
        user_id: int,
        bot_message_id: int,
        page: int,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self.load_menu(token)
            if record is None or not self._menu_binding_matches(
                record,
                account_alias=account_alias,
                chat_id=chat_id,
                user_id=user_id,
                bot_message_id=bot_message_id,
            ):
                return None
            if record["decision"] == "selected":
                return record
            page_count = max(1, (len(record["targets"]) + 7) // 8)
            record["page"] = min(max(0, page), page_count - 1)
            _atomic_private_json(self._menus / f"{token}.json", record)
            return record

    def commit_menu_selection(
        self,
        token: str,
        *,
        account_alias: str,
        chat_id: int,
        user_id: int,
        bot_message_id: int,
        index: int,
        target: EligibleTarget,
    ) -> tuple[dict[str, Any], bool] | None:
        """Apply one menu choice idempotently; the first committed index wins."""
        with self._lock:
            record = self.load_menu(token)
            if record is None or not self._menu_binding_matches(
                record,
                account_alias=account_alias,
                chat_id=chat_id,
                user_id=user_id,
                bot_message_id=bot_message_id,
            ):
                return None
            if record["decision"] == "selected":
                return record, False
            if index < 0 or index >= len(record["targets"]):
                return None
            item = record["targets"][index]
            if item != {
                "name": target.name,
                "agent_id": target.agent_id,
                "manifest_digest": target.manifest_digest,
                "ledger_chain_digest": target.ledger_chain_digest,
                "protocol_version": target.protocol_version,
            }:
                return None
            # Selection first, then terminal menu decision. A crash between the
            # two is safe: replay writes the same pinned selection and commits.
            self.save_selection(account_alias, chat_id, user_id, target)
            result_text = _admin_text(
                f"Selected @{target.name}. Current target: @{target.name}."
            )
            record["decision"] = "selected"
            record["selected_index"] = index
            record["selected_at"] = _utc_now()
            record["result_text"] = result_text
            _atomic_private_json(self._menus / f"{token}.json", record)
            return record, True


class TelegramAgentSwitchingRouter:
    """Owner-local selector/router with target-only LICC and reply drains."""

    def __init__(
        self,
        *,
        owner_workdir: str | Path,
        service: Any,
        accounts_config: Sequence[Mapping[str, Any]],
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        cleanup_interval_seconds: float = _CLEANUP_INTERVAL_SECONDS,
        cleanup_budget: int = _CLEANUP_BUDGET,
        retention_seconds: int = _RETENTION_SECONDS,
    ) -> None:
        self.owner_workdir = Path(owner_workdir)
        self.network_root = self.owner_workdir.parent
        self._service = service
        self._wall_time = wall_time
        self._monotonic = monotonic
        self._cleanup_interval_seconds = max(1.0, float(cleanup_interval_seconds))
        self._cleanup_budget = max(1, int(cleanup_budget))
        self._retention_seconds = max(0, int(retention_seconds))
        self._next_cleanup_at = 0.0
        self._enabled = {
            str(cfg.get("alias", "default"))
            for cfg in accounts_config
            if account_switching_enabled(cfg)
        }
        self._root = self.owner_workdir / "telegram" / "agent_switching"
        self._state = AgentSwitchingStateStore(self._root / "state")
        self._reply_root = self._root / "channel_reply_owner"
        self._grant_store = ChannelReplyFileStore(
            self._reply_root,
            mutation_lock=select_channel_reply_state_lock(),
        )
        self._lock = threading.RLock()
        self._route_lock = threading.RLock()
        self._adapters: dict[str, tuple[EligibleTarget, TelegramChannelReplyAdapter]] = {}
        self._stop = threading.Event()
        self._drain_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._enabled)

    def start(self) -> None:
        if not self.enabled or self._drain_thread is not None:
            return
        for target in self.list_targets():
            self._register_adapter(target)
        self._stop.clear()
        self._next_cleanup_at = 0.0
        self._drain_thread = threading.Thread(
            target=self._drain_loop,
            name="telegram-agent-switching-drain",
            daemon=True,
        )
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="telegram-agent-switching-cleanup",
            daemon=True,
        )
        self._drain_thread.start()
        self._cleanup_thread.start()

    def stop(self) -> None:
        self._stop.set()
        failures: list[str] = []
        for name, attr in (
            ("drain", "_drain_thread"),
            ("cleanup", "_cleanup_thread"),
        ):
            thread = getattr(self, attr)
            if thread is None:
                continue
            # ``start()`` may fail after assigning both Thread objects but before
            # the second worker is started. Joining that unstarted object raises
            # forever on every manager cleanup retry. It owns no worker and can
            # be forgotten; a started/live worker must remain referenced until a
            # later retry proves that it stopped.
            if thread.ident is None:
                setattr(self, attr, None)
                continue
            try:
                thread.join(timeout=2.0)
            except Exception:
                failures.append(name)
                continue
            if thread.is_alive():
                failures.append(name)
                continue
            setattr(self, attr, None)
        if failures:
            raise RuntimeError("agent switching workers did not stop: " + ",".join(failures))

    def _drain_loop(self) -> None:
        while not self._stop.wait(_DRAIN_INTERVAL_SECONDS):
            self._drain_registered_adapters_once()

    def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_cleanup_if_due()
            except Exception as exc:
                # A cleanup defect cannot terminate polling or reply drains.
                log.warning(
                    "telegram agent switching: cleanup cycle failed: %s",
                    type(exc).__name__,
                )
            wait_for = max(0.05, min(1.0, self._next_cleanup_at - self._monotonic()))
            self._stop.wait(wait_for)

    def _run_cleanup_if_due(self) -> bool:
        monotonic_now = self._monotonic()
        if monotonic_now < self._next_cleanup_at:
            return False
        # Advance before work so a failed surface cannot create a tight loop.
        self._next_cleanup_at = monotonic_now + self._cleanup_interval_seconds
        now = _utc_now()
        surfaces: list[tuple[str, Callable[[], object]]] = [
            (
                "switching-state",
                lambda: self._state.cleanup_retained(
                    now=now,
                    retention_seconds=self._retention_seconds,
                    max_items=self._cleanup_budget,
                ),
            ),
            (
                "owner-channel-reply",
                lambda: self._grant_store.cleanup_retained(
                    now=now,
                    retention_seconds=self._retention_seconds,
                    max_records=1,
                ),
            ),
        ]
        with self._lock:
            by_id = dict(self._adapters)
        # Preserve the configured target-root service count. Owner Core cleanup is
        # one additional fixed record, so total Core work is predictably
        # ``cleanup_budget + 1`` rather than an unbounded multiple per root.
        target_budget = self._cleanup_budget
        selected_ids = self._state.select_cleanup_target_ids(
            list(by_id), max_items=target_budget
        )
        adapters = [by_id[agent_id] for agent_id in selected_ids if agent_id in by_id]
        for target, adapter in adapters:
            surfaces.append(
                (
                    f"target-channel-reply:{target.name}",
                    lambda target=target, adapter=adapter: adapter.cleanup_target_state(
                        target.workdir,
                        now=now,
                        retention_seconds=self._retention_seconds,
                        max_records=1,
                    ),
                )
            )
        for label, cleanup in surfaces:
            try:
                cleanup()
            except Exception as exc:
                log.warning(
                    "telegram agent switching: cleanup surface %s failed: %s",
                    label,
                    type(exc).__name__,
                )
        # Telegram target router decisions are deliberately permanent proof-free
        # no-republish truth and therefore are governed by non-deletion.
        return True

    def _target_pin_is_current(self, target: EligibleTarget) -> bool:
        """Freshly revalidate every field in one exact reply-authority pin."""
        refreshed, _ambiguous = self._resolve_name(target.name)
        return refreshed == target

    def _drain_registered_adapters_once(self) -> None:
        """Drain once, terminalize stale requests, and retire obsolete caches."""
        with self._lock:
            adapters = list(self._adapters.values())
        for target, adapter in adapters:
            try:
                adapter.drain_target_outbox(target.workdir, max_items=100)
            except Exception as exc:
                log.warning(
                    "telegram agent switching: target outbox drain failed for %s: %s",
                    target.name,
                    type(exc).__name__,
                )
            try:
                current = self._target_pin_is_current(target)
            except Exception:
                current = False
            if not current:
                with self._lock:
                    cached = self._adapters.get(target.agent_id)
                    if (
                        cached is not None
                        and cached[0] == target
                        and cached[1] is adapter
                    ):
                        self._adapters.pop(target.agent_id, None)
        # A temporarily unavailable target may later become eligible again. New
        # adapters always carry a fresh exact pin and can drain still-valid work.
        try:
            current_targets = self.list_targets()
        except Exception as exc:
            log.warning(
                "telegram agent switching: adapter refresh failed: %s",
                type(exc).__name__,
            )
            return
        for target in current_targets:
            self._register_adapter(target)

    def _register_adapter(self, target: EligibleTarget) -> TelegramChannelReplyAdapter:
        with self._lock:
            existing = self._adapters.get(target.agent_id)
            if existing is not None and existing[0] == target:
                return existing[1]
            # The same canonical name/workdir with a different identity or pin is
            # replacement, not continuity. Never retain that cached authority.
            for agent_id, (pinned, _adapter) in list(self._adapters.items()):
                if pinned.name == target.name or pinned.workdir == target.workdir:
                    self._adapters.pop(agent_id, None)
            adapter = TelegramChannelReplyAdapter(
                state_root=self._reply_root,
                service=self._service,
                target_agent_id=target.agent_id,
                target_agent_name=target.name,
                validate_target_eligibility=(
                    lambda pinned=target: self._target_pin_is_current(pinned)
                ),
            )
            self._adapters[target.agent_id] = (target, adapter)
            return adapter

    def _read_ledger(self, parent: Path) -> list[dict[str, Any]]:
        path = parent / "delegates" / "ledger.jsonl"
        try:
            st = path.lstat()
        except FileNotFoundError:
            return []
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_LEDGER_BYTES:
            return []
        records: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    if index >= _MAX_LEDGER_LINES:
                        break
                    if len(line) > 8192:
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        records.append(value)
        except (OSError, UnicodeDecodeError):
            return []
        return records

    def _validated_manifest(
        self,
        child: Path,
        expected_name: str,
    ) -> tuple[dict[str, Any], str, str] | None:
        try:
            root = self.network_root.resolve(strict=True)
            if child.parent.resolve(strict=True) != root:
                return None
            child_st = child.lstat()
            if stat.S_ISLNK(child_st.st_mode) or not stat.S_ISDIR(child_st.st_mode):
                return None
            if child.resolve(strict=True) != child:
                return None
            manifest_path = child / ".agent.json"
            st = manifest_path.lstat()
            if (
                stat.S_ISLNK(st.st_mode)
                or not stat.S_ISREG(st.st_mode)
                or st.st_nlink != 1
                or st.st_size > _MAX_MANIFEST_BYTES
            ):
                return None
            raw = manifest_path.read_bytes()
            if len(raw) > _MAX_MANIFEST_BYTES:
                return None
            manifest = json.loads(raw)
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(manifest, dict):
            return None
        agent_id = manifest.get("agent_id")
        if (
            expected_name != child.name
            or manifest.get("agent_name") != expected_name
            or manifest.get("address") != expected_name
            or not isinstance(agent_id, str)
            or not agent_id
            or len(agent_id) > 160
        ):
            return None
        # ``.agent.json`` republishes mutable runtime state (for example
        # active/idle) throughout one Agent instance. Pin only the validated
        # instance and governed reply-capability identity, so ordinary state
        # publication cannot look like replacement while a new ``agent_id`` or
        # incompatible capability still fails closed.
        capabilities = manifest.get("route_capabilities")
        channel_reply = (
            capabilities.get("channel_reply")
            if isinstance(capabilities, Mapping)
            else None
        )
        stable_identity = {
            "agent_name": manifest.get("agent_name"),
            "address": manifest.get("address"),
            "agent_id": agent_id,
            "channel_reply": (
                dict(channel_reply) if isinstance(channel_reply, Mapping) else None
            ),
        }
        stable_raw = json.dumps(
            stable_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return manifest, agent_id, hashlib.sha256(stable_raw).hexdigest()

    @staticmethod
    def _marker_supported(manifest: Mapping[str, Any]) -> bool:
        capabilities = manifest.get("route_capabilities")
        marker = capabilities.get("channel_reply") if isinstance(capabilities, Mapping) else None
        return (
            isinstance(marker, Mapping)
            and dict(marker)
            == {
                "marker": CAPABILITY_MARKER,
                "version": PROTOCOL_VERSION,
                "submit": "target-local-filesystem-capsule",
            }
        )

    def list_targets(self) -> list[EligibleTarget]:
        root_chain = _identity_digest("ledger-root-v1", self.owner_workdir.name)
        try:
            canonical_owner = self.owner_workdir.resolve(strict=True)
        except OSError:
            return []
        # Keep up to two independent path states per Agent. Two are sufficient
        # to prove ambiguity, and propagating both states makes every descendant
        # reached through an ambiguous ancestor fail closed as well.
        queue: list[tuple[Path, str, tuple[Path, ...]]] = [
            (canonical_owner, root_chain, (canonical_owner,))
        ]
        path_counts: dict[Path, int] = {canonical_owner: 1}
        edge_cache: dict[
            Path,
            list[tuple[str, Path, Mapping[str, Any], str, str, Mapping[str, Any]]],
        ] = {}
        by_name: dict[str, list[EligibleTarget]] = {}
        while queue:
            parent, parent_chain, ancestors = queue.pop(0)
            try:
                canonical_parent = parent.resolve(strict=True)
            except OSError:
                continue
            edges = edge_cache.get(canonical_parent)
            if edges is None:
                edges = []
                for record in self._read_ledger(canonical_parent):
                    if (
                        record.get("event") != "avatar"
                        or record.get("boot_status") != "ok"
                        or not _valid_agent_name(record.get("name"))
                        or record.get("working_dir") != record.get("name")
                    ):
                        continue
                    name = str(record["name"])
                    child = self.network_root / name
                    validated = self._validated_manifest(child, name)
                    if validated is None:
                        continue
                    manifest, agent_id, manifest_digest = validated
                    edges.append(
                        (name, child, manifest, agent_id, manifest_digest, record)
                    )
                edge_cache[canonical_parent] = edges
            for name, child, manifest, agent_id, manifest_digest, record in edges:
                if child in ancestors:
                    continue
                if path_counts.get(child, 0) >= 2:
                    continue
                path_counts[child] = path_counts.get(child, 0) + 1
                edge_digest = hashlib.sha256(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                chain_digest = _identity_digest(
                    "ledger-chain-v1",
                    parent_chain,
                    canonical_parent.name,
                    name,
                    edge_digest,
                    manifest_digest,
                )
                queue.append((child, chain_digest, (*ancestors, child)))
                if not self._marker_supported(manifest):
                    continue
                try:
                    alive = observe_alive(
                        PosixAgentPresenceStoreAdapter(child),
                        self._wall_time(),
                    )
                except Exception:
                    alive = False
                if not alive:
                    continue
                by_name.setdefault(name, []).append(
                    EligibleTarget(
                        name=name,
                        agent_id=agent_id,
                        workdir=child,
                        manifest_digest=manifest_digest,
                        ledger_chain_digest=chain_digest,
                    )
                )
        # Even two paths pointing at the same live directory are ambiguous: an
        # exact unique descendant chain, not name or directory coincidence, is
        # the authority.
        result = [items[0] for items in by_name.values() if len(items) == 1]
        return sorted(result, key=lambda item: item.name)

    def _resolve_name(self, name: str) -> tuple[EligibleTarget | None, bool]:
        matches = [target for target in self.list_targets() if target.name == name]
        if len(matches) == 1:
            return matches[0], False
        return None, len(matches) > 1

    @staticmethod
    def _target_matches_record(target: EligibleTarget, record: Mapping[str, Any]) -> bool:
        return (
            target.name == record.get("target_name")
            and target.agent_id == record.get("target_agent_id")
            and target.manifest_digest == record.get("manifest_digest")
            and target.ledger_chain_digest == record.get("ledger_chain_digest")
            and target.protocol_version == record.get("protocol_version")
        )

    def _bot_username(self, account_alias: str) -> str | None:
        try:
            identity = self._service.get_account(account_alias).public_identity()
        except Exception:
            return None
        for key in ("bot_username", "username"):
            value = identity.get(key) if isinstance(identity, Mapping) else None
            if isinstance(value, str) and value:
                return value.lstrip("@")
        return None

    @staticmethod
    def _basic_message_context(
        update: Mapping[str, Any],
        *,
        branch: str = "message",
    ) -> tuple[int, int, int] | None:
        message = update.get(branch)
        if not isinstance(message, Mapping):
            return None
        chat = message.get("chat")
        actor = message.get("from")
        if (
            not isinstance(chat, Mapping)
            or chat.get("type") != "private"
            or not _is_positive_int(chat.get("id"))
            or not isinstance(actor, Mapping)
            or not _is_positive_int(actor.get("id"))
            or actor.get("is_bot") is True
            or not _is_positive_int(message.get("message_id"))
        ):
            return None
        return int(chat["id"]), int(actor["id"]), int(message["message_id"])

    @classmethod
    def _message_context(cls, update: Mapping[str, Any]) -> tuple[int, int, int, str] | None:
        basic = cls._basic_message_context(update)
        message = update.get("message")
        if basic is None or not isinstance(message, Mapping):
            return None
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        return *basic, text

    @staticmethod
    def _callback_context(update: Mapping[str, Any]) -> tuple[int, int, int, str] | None:
        query = update.get("callback_query")
        if not isinstance(query, Mapping):
            return None
        actor = query.get("from")
        message = query.get("message")
        chat = message.get("chat") if isinstance(message, Mapping) else None
        if (
            not isinstance(actor, Mapping)
            or not _is_positive_int(actor.get("id"))
            or actor.get("is_bot") is True
            or not isinstance(message, Mapping)
            or not _is_positive_int(message.get("message_id"))
            or not isinstance(chat, Mapping)
            or chat.get("type") != "private"
            or not _is_positive_int(chat.get("id"))
            or not isinstance(query.get("data"), str)
        ):
            return None
        return int(chat["id"]), int(actor["id"]), int(message["message_id"]), str(query["data"])

    def handle(self, account_alias: str, update: Mapping[str, Any], branch: str) -> bool:
        if account_alias not in self._enabled:
            return False
        if branch == "callback_query":
            context = self._callback_context(update)
            if context is None or not context[3].startswith(_MENU_PREFIX):
                return False
            self._handle_callback(account_alias, *context)
            return True
        if branch == "edited_message":
            basic = self._basic_message_context(update, branch="edited_message")
            if basic is None:
                return False
            chat_id, user_id, message_id = basic
            message = update.get("edited_message")
            text = message.get("text") if isinstance(message, Mapping) else None
            if not isinstance(text, str):
                text = message.get("caption") if isinstance(message, Mapping) else None
            bot_username = self._bot_username(account_alias)
            directive = parse_agent_text(text, bot_username=bot_username)
            selection_result = self._state.read_selection(
                account_alias, chat_id, user_id
            )
            ownership_result = self._state.read_original_ownership(
                account_alias,
                chat_id,
                user_id,
                message_id,
                now=_utc_now(),
                retention_seconds=self._retention_seconds,
            )
            if (
                directive.kind == "ordinary"
                and selection_result.status == "absent"
                and ownership_result.status == "absent"
            ):
                return False
            rejection = self._state.reserve_edit_rejection(
                account_alias,
                update.get("update_id"),
                chat_id,
                user_id,
                message_id,
                now=_utc_now(),
                retention_seconds=self._retention_seconds,
            )
            if rejection == "new":
                self._local_reply(
                    account_alias,
                    chat_id,
                    message_id,
                    _EDIT_UNSUPPORTED_TEXT,
                )
            elif rejection == "unavailable":
                log.warning(
                    "telegram agent switching: edit rejection decision unavailable"
                )
            return True
        if branch != "message":
            return False
        context = self._message_context(update)
        if context is None:
            basic = self._basic_message_context(update)
            if basic is None:
                return False
            chat_id, user_id, message_id = basic
            message = update.get("message")
            caption = message.get("caption") if isinstance(message, Mapping) else None
            selector_like = isinstance(caption, str) and caption.lstrip().startswith(("@", "/agent"))
            selection_result = self._state.read_selection(account_alias, chat_id, user_id)
            if selection_result.status == "unavailable":
                self._selection_state_error(account_alias, chat_id, message_id)
                return True
            if selection_result.status == "absent" and not selector_like:
                return False
            self._local_reply(
                account_alias,
                chat_id,
                message_id,
                "Agent routing supports non-empty plain text only.",
            )
            return True
        chat_id, user_id, message_id, text = context
        bot_username = self._bot_username(account_alias)
        directive = parse_agent_text(text, bot_username=bot_username)
        # /start remains the account's local setup/admin command even when a
        # persistent Agent selection exists. The switching router makes no
        # reply, wake, grant, or selection mutation for all exact command forms.
        if _is_start_command(text, bot_username=bot_username):
            return False
        message = update.get("message")
        if not is_supported_agent_switching_message(message):
            selection_result = self._state.read_selection(account_alias, chat_id, user_id)
            if selection_result.status == "unavailable":
                self._selection_state_error(account_alias, chat_id, message_id)
                return True
            if directive.kind == "ordinary" and selection_result.status == "absent":
                # Forwarded ordinary text with no switching directive/selection
                # preserves today's admin behavior. Once switching applies, the
                # owner handles the unsupported result locally and wakes no Agent.
                return False
            self._local_reply(
                account_alias,
                chat_id,
                message_id,
                "Agent routing supports non-forwarded plain text messages only.",
            )
            return True
        if directive.kind == "ordinary":
            selection_result = self._state.read_selection(account_alias, chat_id, user_id)
            if selection_result.status == "absent":
                return False
            if selection_result.status == "unavailable" or selection_result.record is None:
                self._selection_state_error(account_alias, chat_id, message_id)
                return True
            self._route_selected(
                account_alias,
                chat_id,
                user_id,
                message_id,
                update,
                selection_result.record,
                text,
            )
            return True
        if directive.kind == "list":
            self._send_menu(account_alias, chat_id, user_id, message_id)
            return True
        if directive.kind == "status":
            self._send_status(account_alias, chat_id, user_id, message_id)
            return True
        if directive.kind == "reset":
            try:
                self._state.clear_selection(account_alias, chat_id, user_id)
            except OSError:
                self._selection_state_error(account_alias, chat_id, message_id)
                return True
            self._local_reply(
                account_alias, chat_id, message_id,
                "Target reset to admin. Current target: @admin.",
            )
            return True
        if directive.kind == "select" and directive.name is not None:
            target, _ambiguous = self._resolve_name(directive.name)
            if target is None:
                self._local_reply(
                    account_alias, chat_id, message_id,
                    "That Agent is not currently eligible.",
                )
                return True
            self._state.save_selection(account_alias, chat_id, user_id, target)
            self._register_adapter(target)
            self._local_reply(
                account_alias, chat_id, message_id,
                f"Selected @{target.name}. Current target: @{target.name}.",
            )
            return True
        if directive.kind == "route_once" and directive.name and directive.body is not None:
            target, _ambiguous = self._resolve_name(directive.name)
            if target is None:
                self._local_reply(
                    account_alias, chat_id, message_id,
                    "That Agent is not currently eligible.",
                )
                return True
            self._route(account_alias, chat_id, user_id, message_id, update, target, directive.body)
            return True
        self._local_reply(account_alias, chat_id, message_id, "Invalid Agent command.")
        return True

    def _selection_state_error(
        self,
        account_alias: str,
        chat_id: int,
        reply_to: int,
    ) -> None:
        self._local_reply(
            account_alias,
            chat_id,
            reply_to,
            "Saved Agent selection is unavailable. Use /agent reset or select an Agent again.",
        )

    def _local_reply(self, account_alias: str, chat_id: int, reply_to: int, text: str, *, reply_markup=None) -> dict[str, Any] | None:
        try:
            return self._service.get_account(account_alias).send_message(
                chat_id,
                _admin_text(text),
                reply_markup=reply_markup,
                reply_to_message_id=reply_to,
            )
        except Exception as exc:
            log.warning("telegram agent switching: local reply failed: %s", type(exc).__name__)
            return None

    def _send_status(self, account_alias: str, chat_id: int, user_id: int, reply_to: int) -> None:
        selection_result = self._state.read_selection(account_alias, chat_id, user_id)
        if selection_result.status == "unavailable":
            self._selection_state_error(account_alias, chat_id, reply_to)
            return
        if selection_result.status == "absent":
            self._local_reply(account_alias, chat_id, reply_to, "Current target: @admin.")
            return
        selection = selection_result.record
        target, _ = self._resolve_name(str(selection["target_name"]))
        if target is None:
            self._local_reply(
                account_alias,
                chat_id,
                reply_to,
                f"Current target: @{selection['target_name']} is unavailable; selection was kept.",
            )
            return
        if not self._target_matches_record(target, selection):
            self._state.clear_selection(account_alias, chat_id, user_id)
            self._local_reply(
                account_alias,
                chat_id,
                reply_to,
                f"@{target.name} was replaced; selection was cleared. Current target: @admin.",
            )
            return
        self._local_reply(account_alias, chat_id, reply_to, f"Current target: @{target.name}.")

    @staticmethod
    def _page_markup(token: str, targets: Sequence[Mapping[str, Any]], page: int) -> dict[str, Any]:
        page_size = 8
        page_count = max(1, (len(targets) + page_size - 1) // page_size)
        page = min(max(0, page), page_count - 1)
        start = page * page_size
        rows = [
            [{"text": f"@{item['name']}", "callback_data": f"{_MENU_PREFIX}{token}:s{index}"}]
            for index, item in enumerate(targets[start:start + page_size], start=start)
        ]
        if page_count > 1:
            nav: list[dict[str, str]] = []
            if page > 0:
                nav.append({"text": "<", "callback_data": f"{_MENU_PREFIX}{token}:p{page - 1}"})
            nav.append({"text": f"{page + 1}/{page_count}", "callback_data": f"{_MENU_PREFIX}{token}:p{page}"})
            if page + 1 < page_count:
                nav.append({"text": ">", "callback_data": f"{_MENU_PREFIX}{token}:p{page + 1}"})
            rows.append(nav)
        return {"inline_keyboard": rows}

    def _send_menu(self, account_alias: str, chat_id: int, user_id: int, reply_to: int) -> None:
        targets = self.list_targets()
        if not targets:
            self._local_reply(account_alias, chat_id, reply_to, "No eligible Agents are currently available.")
            return
        token, record = self._state.create_menu(
            account_alias=account_alias,
            chat_id=chat_id,
            user_id=user_id,
            targets=targets,
            page=0,
        )
        result = self._local_reply(
            account_alias,
            chat_id,
            reply_to,
            "Choose an Agent:",
            reply_markup=self._page_markup(token, record["targets"], 0),
        )
        message_id = result.get("message_id") if isinstance(result, Mapping) else None
        if _is_positive_int(message_id):
            self._state.bind_menu_message(token, int(message_id))

    def _handle_callback(self, account_alias: str, chat_id: int, user_id: int, bot_message_id: int, data: str) -> None:
        try:
            prefix, token, action = data.split(":", 2)
        except ValueError:
            return
        if prefix != "as" or len(data.encode()) > 64:
            return
        record = self._state.load_menu(token)
        if record is None:
            self._local_reply(account_alias, chat_id, bot_message_id, "This Agent menu is no longer valid.")
            return
        expires = _parse_utc(record["expires_at"])
        if (
            record["account_alias"] != account_alias
            or record["chat_id"] != chat_id
            or record["user_id"] != user_id
            or record["bot_message_id"] != bot_message_id
            or expires is None
            or expires <= datetime.now(timezone.utc)
        ):
            self._local_reply(account_alias, chat_id, bot_message_id, "This Agent menu is no longer valid.")
            return
        if record["decision"] == "selected":
            try:
                self._service.get_account(account_alias).edit_message(
                    chat_id,
                    bot_message_id,
                    str(record["result_text"]),
                    reply_markup={"inline_keyboard": []},
                )
            except Exception:
                pass
            return
        if action.startswith("p") and action[1:].isdigit():
            updated = self._state.set_menu_page(
                token,
                account_alias=account_alias,
                chat_id=chat_id,
                user_id=user_id,
                bot_message_id=bot_message_id,
                page=int(action[1:]),
            )
            if updated is None:
                self._local_reply(account_alias, chat_id, bot_message_id, "This Agent menu is no longer valid.")
                return
            try:
                self._service.get_account(account_alias).edit_message(
                    chat_id,
                    bot_message_id,
                    "Choose an Agent:",
                    reply_markup=self._page_markup(
                        token,
                        updated["targets"],
                        int(updated["page"]),
                    ),
                )
            except Exception:
                pass
            return
        if not action.startswith("s") or not action[1:].isdigit():
            return
        index = int(action[1:])
        if index < 0 or index >= len(record["targets"]):
            return
        chosen = record["targets"][index]
        target, ambiguous = self._resolve_name(str(chosen["name"]))
        expected = {
            "name": target.name,
            "agent_id": target.agent_id,
            "manifest_digest": target.manifest_digest,
            "ledger_chain_digest": target.ledger_chain_digest,
            "protocol_version": target.protocol_version,
        } if target is not None else None
        if target is None or ambiguous or chosen != expected:
            self._local_reply(account_alias, chat_id, bot_message_id, "That Agent is no longer eligible.")
            return
        committed = self._state.commit_menu_selection(
            token,
            account_alias=account_alias,
            chat_id=chat_id,
            user_id=user_id,
            bot_message_id=bot_message_id,
            index=index,
            target=target,
        )
        if committed is None:
            self._local_reply(account_alias, chat_id, bot_message_id, "This Agent menu is no longer valid.")
            return
        terminal, _created = committed
        self._register_adapter(target)
        try:
            self._service.get_account(account_alias).edit_message(
                chat_id,
                bot_message_id,
                str(terminal["result_text"]),
                reply_markup={"inline_keyboard": []},
            )
        except Exception:
            pass

    def _route_selected(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
        update: Mapping[str, Any],
        selection: Mapping[str, Any],
        body: str,
    ) -> None:
        target, _ = self._resolve_name(str(selection["target_name"]))
        if target is None:
            self._local_reply(
                account_alias,
                chat_id,
                message_id,
                f"@{selection['target_name']} is unavailable; selection was kept.",
            )
            return
        if not self._target_matches_record(target, selection):
            self._state.clear_selection(account_alias, chat_id, user_id)
            self._local_reply(
                account_alias, chat_id, message_id,
                f"@{target.name} was replaced; selection was cleared. Current target: @admin.",
            )
            return
        self._route(account_alias, chat_id, user_id, message_id, update, target, body)

    @staticmethod
    def _route_event_id(
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
        update: Mapping[str, Any],
    ) -> str:
        return "tg-" + _identity_digest(
            "telegram-agent-switching-v1",
            account_alias,
            update.get("update_id", "missing"),
            chat_id,
            user_id,
            message_id,
        )[:48]

    def _target_decision_path(self, target: EligibleTarget, route_event_id: str) -> Path:
        # Telegram producer decisions are not Core channel_reply state. Keep the
        # strict schema in a separate target-local namespace so Core never scans
        # or interprets producer-owned files.
        return (
            target.workdir
            / ".telegram-agent-switching"
            / "router-decisions"
            / f"{route_event_id}.json"
        )

    @staticmethod
    def _payload_digest(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _reserve_target_route(
        self,
        target: EligibleTarget,
        *,
        route_event_id: str,
        grant_ref: str,
        event_id: str,
        body_digest: str,
        payload_digest: str,
        now: str,
    ) -> tuple[bool, dict[str, Any]]:
        path = self._target_decision_path(target, route_event_id)
        record = {
            "version": 1,
            "route_event_id": route_event_id,
            "target_name": target.name,
            "target_agent_id": target.agent_id,
            "manifest_digest": target.manifest_digest,
            "ledger_chain_digest": target.ledger_chain_digest,
            "protocol_version": target.protocol_version,
            "grant_ref": grant_ref,
            "event_id": event_id,
            "body_digest": body_digest,
            "payload_digest": payload_digest,
            "status": "reserved",
            "created_at": now,
            "updated_at": now,
        }
        if _create_private_json_once(path, record):
            return True, record
        existing = _read_private_json(path)
        expected_fields = set(record)
        if (
            existing is None
            or set(existing) != expected_fields
            or existing.get("status") not in {"reserved", "published", "failed"}
            or any(
                existing.get(key) != record[key]
                for key in expected_fields - {"status", "updated_at"}
            )
            or _parse_utc(existing.get("updated_at")) is None
        ):
            raise ValueError("route_decision_conflict")
        return False, existing

    def _mark_target_route(
        self,
        target: EligibleTarget,
        decision: Mapping[str, Any],
        status_value: str,
        now: str,
    ) -> dict[str, Any]:
        if status_value not in {"published", "failed"}:
            raise ValueError("invalid_route_status")
        path = self._target_decision_path(target, str(decision["route_event_id"]))
        current = _read_private_json(path)
        if current is None or set(current) != set(decision):
            raise ValueError("missing_route_decision")
        for key in set(decision) - {"status", "updated_at"}:
            if current.get(key) != decision.get(key):
                raise ValueError("route_decision_conflict")
        current_status = current.get("status")
        if current_status in {"published", "failed"}:
            if current_status != status_value:
                raise ValueError("route_terminal_conflict")
            return current
        if current_status != "reserved":
            raise ValueError("route_decision_conflict")
        updated = dict(current)
        updated["status"] = status_value
        updated["updated_at"] = now
        _atomic_private_json(path, updated)
        return updated

    def _target_event_path(self, target: EligibleTarget, event_id: str) -> Path:
        return target.workdir / ".mcp_inbox" / _ROUTE_MCP_NAME / f"{event_id}.json"

    def _target_event_matches(self, target: EligibleTarget, decision: Mapping[str, Any]) -> bool:
        path = self._target_event_path(target, str(decision["event_id"]))
        try:
            st = path.lstat()
            if (
                stat.S_ISLNK(st.st_mode)
                or not stat.S_ISREG(st.st_mode)
                or st.st_nlink != 1
                or st.st_size > _MAX_STATE_BYTES
            ):
                return False
            payload = json.loads(path.read_bytes())
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return False
        return isinstance(payload, dict) and self._payload_digest(payload) == decision.get("payload_digest")

    def _route(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
        update: Mapping[str, Any],
        target: EligibleTarget,
        body: str,
    ) -> None:
        with self._route_lock:
            self._route_locked(
                account_alias,
                chat_id,
                user_id,
                message_id,
                update,
                target,
                body,
            )

    def _route_locked(
        self,
        account_alias: str,
        chat_id: int,
        user_id: int,
        message_id: int,
        update: Mapping[str, Any],
        target: EligibleTarget,
        body: str,
    ) -> None:
        if len(body) > _MAX_TEXT_CHARS:
            self._local_reply(account_alias, chat_id, message_id, "Message is too long for Agent routing.")
            return
        refreshed, _ = self._resolve_name(target.name)
        if refreshed is None or not (
            refreshed.agent_id == target.agent_id
            and refreshed.manifest_digest == target.manifest_digest
            and refreshed.ledger_chain_digest == target.ledger_chain_digest
            and refreshed.protocol_version == target.protocol_version
        ):
            self._local_reply(account_alias, chat_id, message_id, "That Agent is no longer eligible.")
            return
        target = refreshed
        route_event_id = self._route_event_id(
            account_alias, chat_id, user_id, message_id, update,
        )
        now = _utc_now()
        expires_at = _future(7200, base=now)

        def grant_factory() -> tuple[OwnerReplyGrant, str]:
            return OwnerReplyGrant.issue(
                target_agent_id=target.agent_id,
                target_agent_name=target.name,
                target_protocol_version=PROTOCOL_VERSION,
                channel="telegram",
                anchor={
                    "account_alias": account_alias,
                    "chat_id": chat_id,
                    "reply_to_message_id": message_id,
                },
                created_at=now,
                expires_at=expires_at,
                route_event_id=route_event_id,
            )

        try:
            grant, proof, grant_created = self._grant_store.issue_or_reuse_grant(
                route_event_id=route_event_id,
                grant_factory=grant_factory,
                now=now,
            )
        except Exception as exc:
            log.warning("telegram agent switching: grant issue failed: %s", type(exc).__name__)
            self._local_reply(account_alias, chat_id, message_id, "Agent routing is temporarily unavailable.")
            return
        if grant is None or proof is None:
            self._local_reply(account_alias, chat_id, message_id, "Agent routing is temporarily unavailable.")
            return

        body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        event_id = "as-" + route_event_id
        licc_body = (
            "A Telegram message was routed directly to you by the owner Bot.\n"
            "Reply only with channel_reply(action='submit'). Use these exact "
            "owner-supplied authority fields:\n"
            f"version: {PROTOCOL_VERSION}\n"
            f"grant_ref: {grant.grant_ref}\n"
            f"proof: {proof}\n"
            "Generate created_at as the current UTC timestamp at the moment you submit "
            "this concrete request. It is target-authored request time, not the grant's "
            "route or issuance time.\n"
            "Use a fresh target-local request_id. Do not use the Telegram MCP.\n\n"
            "Human message:\n"
            f"{body}"
        )
        metadata = {
            "route": "telegram-agent-switching/v1",
            "delivery_semantics": "at-most-once/v1",
            "delivery_id": route_event_id,
            "target_agent_name": target.name,
            "target_agent_id": target.agent_id,
            "reply_grant_ref": grant.grant_ref,
            "channel_reply_version": PROTOCOL_VERSION,
            "body_sha256": body_digest,
            "expires_at": grant.expires_at,
        }
        # Hash the exact canonical LICC document that `push_inbox_event`
        # publishes, not the producer function's differently named arguments.
        payload = {
            "licc_version": LICC_VERSION,
            "from": f"telegram/{account_alias}",
            "subject": f"Telegram message routed to @{target.name}",
            "body": licc_body,
            "metadata": metadata,
            "wake": True,
            "received_at": grant.created_at,
        }
        payload_digest = self._payload_digest(payload)
        try:
            self._state.claim_original_ownership(
                account_alias,
                chat_id,
                user_id,
                message_id,
                now=now,
                retention_seconds=self._retention_seconds,
            )
        except Exception as exc:
            if grant_created:
                self._grant_store.revoke_grant(grant.grant_ref)
            log.warning(
                "telegram agent switching: original ownership commit failed: %s",
                type(exc).__name__,
            )
            self._local_reply(
                account_alias,
                chat_id,
                message_id,
                "Agent routing is temporarily unavailable.",
            )
            return
        try:
            ChannelReplyTargetCapsule.create(
                target_workdir=target.workdir,
                target_agent_id=target.agent_id,
                target_agent_name=target.name,
                created_at=grant.created_at,
                expires_at=grant.expires_at,
                mutation_lock=select_channel_reply_state_lock(),
            )
            reserved, decision = self._reserve_target_route(
                target,
                route_event_id=route_event_id,
                grant_ref=grant.grant_ref,
                event_id=event_id,
                body_digest=body_digest,
                payload_digest=payload_digest,
                now=now,
            )
        except Exception as exc:
            if grant_created:
                self._grant_store.revoke_grant(grant.grant_ref)
            log.warning("telegram agent switching: target reservation failed: %s", type(exc).__name__)
            self._local_reply(account_alias, chat_id, message_id, "Agent routing is temporarily unavailable.")
            return
        self._register_adapter(target)
        if reserved and not grant_created:
            # Stage 1 says this route authority already existed, so absence of
            # the target decision cannot authorize publication again. The
            # original target may already have consumed the event and still
            # needs its grant to reply.
            try:
                self._mark_target_route(target, decision, "failed", now)
            except Exception as exc:
                log.warning("telegram agent switching: reused reservation retirement failed: %s", type(exc).__name__)
            self._local_reply(account_alias, chat_id, message_id, "Agent routing is temporarily unavailable.")
            return
        if not reserved:
            if decision["status"] == "failed":
                self._local_reply(account_alias, chat_id, message_id, "This message could not be routed.")
                return
            if decision["status"] == "published":
                return
            # A crash can leave `reserved` either before publication or after
            # the target already consumed and unlinked the event. Never repush:
            # at-most-once delivery prefers a possible loss over a duplicate
            # target task, and preserving the grant allows a delivered task to
            # reply. A conflicting file is proof of corruption, not delivery.
            event_path = self._target_event_path(target, event_id)
            if event_path.exists() and not self._target_event_matches(target, decision):
                try:
                    self._mark_target_route(target, decision, "failed", now)
                    self._grant_store.revoke_grant(grant.grant_ref)
                except Exception:
                    pass
                self._local_reply(account_alias, chat_id, message_id, "This message could not be routed.")
                return
            try:
                self._mark_target_route(target, decision, "published", now)
            except Exception as exc:
                log.warning("telegram agent switching: reserved recovery failed: %s", type(exc).__name__)
            return

        event_path = self._target_event_path(target, event_id)
        try:
            preexisting = event_path.lstat()
        except FileNotFoundError:
            preexisting = None
        except OSError:
            preexisting = object()
        if preexisting is not None:
            try:
                self._mark_target_route(target, decision, "failed", now)
                self._grant_store.revoke_grant(grant.grant_ref)
            except Exception:
                pass
            self._local_reply(account_alias, chat_id, message_id, "Agent routing is temporarily unavailable.")
            return
        try:
            pushed = push_inbox_event(
                sender=payload["from"],
                subject=payload["subject"],
                body=payload["body"],
                metadata=payload["metadata"],
                wake=True,
                received_at=payload["received_at"],
                agent_dir=target.workdir,
                mcp_name=_ROUTE_MCP_NAME,
                event_id=event_id,
            )
        except Exception as exc:
            log.warning("telegram agent switching: target LICC push raised: %s", type(exc).__name__)
            pushed = False
        if not pushed:
            try:
                self._mark_target_route(target, decision, "failed", now)
                self._grant_store.revoke_grant(grant.grant_ref)
            except Exception as exc:
                log.warning("telegram agent switching: route failure finalization failed: %s", type(exc).__name__)
            self._local_reply(account_alias, chat_id, message_id, "Agent routing is temporarily unavailable.")
            return
        try:
            self._mark_target_route(target, decision, "published", now)
        except Exception as exc:
            # Publication already happened. Leave the durable reservation and
            # active grant for the next redelivery to recover without repush.
            log.warning("telegram agent switching: route publish finalization failed: %s", type(exc).__name__)


def build_agent_switching_router(
    *,
    owner_workdir: str | Path,
    service: Any,
    accounts_config: Sequence[Mapping[str, Any]],
) -> TelegramAgentSwitchingRouter | None:
    if not any(account_switching_enabled(cfg) for cfg in accounts_config):
        return None
    return TelegramAgentSwitchingRouter(
        owner_workdir=owner_workdir,
        service=service,
        accounts_config=accounts_config,
    )
