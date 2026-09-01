"""Channel-neutral reply capability.

Core owns this module's Port, domain request/receipt types, and durable
state-machine semantics. Channel adapters hold concrete destinations and submit
through this boundary; targets only present opaque grant references and bounded
plain text.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
import fnmatch
import json
import hashlib
import hmac
import os
import re
import secrets
import stat
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ._mutation_lock import (
    ChannelReplyMutationLockPort,
    ChannelReplyMutationSession,
    ChannelReplyObjectIdentity,
    ChannelReplyRootIdentity,
    DirectoryScanBudget as SessionDirectoryScanBudget,
    DirectoryScanCursor,
    OwnedRemovalBudget as SessionOwnedRemovalBudget,
)

PROTOCOL_VERSION = 1
CAPABILITY_MARKER = "channel_reply/v1"
CAPABILITY_MANIFEST_KEY = "channel_reply"
DEFAULT_GRANT_TTL_SECONDS = 2 * 60 * 60
MIN_GRANT_TTL_SECONDS = 60
MAX_GRANT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_REQUEST_MAX_AGE_SECONDS = 10 * 60
DEFAULT_REQUEST_FUTURE_SKEW_SECONDS = 2 * 60
DEFAULT_RETENTION_SECONDS = 7 * 24 * 60 * 60
# Route IDs remain non-authority tombstones beyond the ordinary authority/state
# retention window so delayed PR2 redeliveries cannot mint a fresh grant.
DEFAULT_ROUTE_EVENT_TOMBSTONE_SECONDS = 30 * 24 * 60 * 60
MAX_REPLY_TEXT_CHARS = 4000
MAX_RECEIPT_MESSAGE_CHARS = 240
MAX_RECORD_BYTES = 64 * 1024
MAX_OUTBOX_TEXT_BYTES = 32 * 1024

AUTHORITY_FIELDS = {
    "account",
    "account_id",
    "token",
    "bot_token",
    "chat",
    "chat_id",
    "user",
    "user_id",
    "message",
    "message_id",
    "update_id",
    "path",
    "agent_dir",
    "target",
    "destination",
    "parse_mode",
    "rendering_mode",
    "entities",
    "media",
    "reply_markup",
    "retry",
    "backoff",
    "attempt",
    "attempts",
}

REQUEST_FIELDS = {"version", "grant_ref", "request_id", "created_at", "text", "proof"}
RECEIPT_FIELDS = {"version", "status", "grant_ref", "request_id", "message", "public_ref"}
GRANT_FIELDS = {
    "version",
    "grant_id",
    "grant_ref",
    "proof_digest",
    "target_agent_id",
    "target_agent_name",
    "target_protocol_version",
    "channel",
    "anchor",
    "created_at",
    "expires_at",
    "revoked",
    "claimed_request_id",
    "consumed_request_id",
    "route_event_id",
}
REQUEST_RECORD_FIELDS = {
    "version",
    "grant_id",
    "request_id",
    "target_agent_id",
    "status",
    "created_at",
    "prepared_at",
    "claim_token",
    "receipt",
}
ROUTE_EVENT_FIELDS = {
    "version",
    "route_event_id",
    "grant_id",
    "grant_ref",
    "created_at",
    "expires_at",
    "decision",
    "proof",
    "proof_digest",
}
CAPSULE_FIELDS = {
    "version",
    "capsule_id",
    "target_agent_id",
    "target_agent_name",
    "outbox_dir",
    "receipt_dir",
    "created_at",
    "expires_at",
    "capability_marker",
}
OUTBOX_FIELDS = {"version", "request", "submitted_at"}
TARGET_RECEIPT_FIELDS = {"version", "receipt", "committed_at"}
TARGET_CLAIM_FIELDS = {
    "version",
    "state",
    "request",
    "submitted_at",
    "claimed_at",
    "dispatching_at",
}
TARGET_CONSUMED_FIELDS = {
    "version",
    "grant_id",
    "request_id",
    "identity_digest",
    "status",
    "finalized_at",
}
TARGET_DEAD_FIELDS = {"version", "identity_digest", "reason", "failed_at"}
OWNER_DEAD_FIELDS = {
    "version",
    "source",
    "identity_digest",
    "record_kind",
    "reason",
    "failed_at",
}
ROUTE_DECISION_FIELDS = {
    "version",
    "route_event_id",
    "route_event_digest",
    "authority_digest",
    "decision",
    "created_at",
    "updated_at",
}


@dataclass(frozen=True, slots=True)
class ChannelReplyStateKindInventory:
    """Mechanically checked persistence, sensitivity, and recovery contract."""

    kind: str
    scope: str
    directory: str
    canonical_pattern: str
    owned_temp_pattern: str
    sensitivity: str
    writer_algorithm: str
    interruption_cuts: tuple[str, ...]
    recovery_owner: str
    terminalization: str
    retention: str


@dataclass(slots=True)
class OwnedRemovalBudget:
    """Bound one no-follow owned-state cleanup pass."""

    inspections: int = 512
    removals: int = 128
    max_depth: int = 64


@dataclass(frozen=True, slots=True)
class OwnedRemovalResult:
    state: str
    inspections: int = 0
    removals: int = 0
    error: str | None = None


_REPLACE_CUTS = (
    "hidden-created",
    "hidden-complete-before-file-fsync",
    "hidden-fsynced-before-replace",
    "canonical-replaced-before-directory-fsync",
    "canonical-directory-fsynced",
)
_CREATE_CUTS = (
    "hidden-created",
    "hidden-complete-before-file-fsync",
    "hidden-fsynced-before-link",
    "hard-link-published-before-directory-fsync",
    "hard-link-directory-fsynced-before-hidden-unlink",
    "hidden-unlinked-before-final-directory-fsync",
)
_OWNED_TEMP_PATTERN = r"^\.(?P<canonical>.+)\.(?P<pid>[0-9]+)\.(?P<nonce>[0-9a-f]{32})\.tmp$"


def _state_inventory(
    kind: str,
    scope: str,
    directory: str,
    canonical_pattern: str,
    sensitivity: str,
    writer_algorithm: str,
    recovery_owner: str,
    terminalization: str,
    retention: str,
) -> ChannelReplyStateKindInventory:
    return ChannelReplyStateKindInventory(
        kind=kind,
        scope=scope,
        directory=directory,
        canonical_pattern=canonical_pattern,
        owned_temp_pattern=_OWNED_TEMP_PATTERN,
        sensitivity=sensitivity,
        writer_algorithm=writer_algorithm,
        interruption_cuts=_CREATE_CUTS if writer_algorithm == "atomic-create-hard-link" else _REPLACE_CUTS,
        recovery_owner=recovery_owner,
        terminalization=terminalization,
        retention=retention,
    )


# This is the single executable inventory for every channel-reply state writer.
# Runtime writer validation and recovery both consume it; tests compare it with
# every created directory and atomic call so a new state kind cannot be silent.
CHANNEL_REPLY_STATE_INVENTORY = (
    ChannelReplyStateKindInventory(
        kind="owner_mutation_lock",
        scope="owner",
        directory=".",
        canonical_pattern=r"^\.channel-reply\.lock$",
        owned_temp_pattern=r"(?!)",
        sensitivity="proof-free cooperative process-lock metadata",
        writer_algorithm="native-advisory-lock",
        interruption_cuts=("lock-file-opened", "lock-acquired", "lock-released"),
        recovery_owner="platform lock adapter",
        terminalization="not authority and never a queue candidate",
        retention="may persist with the owner state root",
    ),
    ChannelReplyStateKindInventory(
        kind="target_mutation_lock",
        scope="target",
        directory=".",
        canonical_pattern=r"^\.channel-reply\.lock$",
        owned_temp_pattern=r"(?!)",
        sensitivity="proof-free cooperative process-lock metadata",
        writer_algorithm="native-advisory-lock",
        interruption_cuts=("lock-file-opened", "lock-acquired", "lock-released"),
        recovery_owner="platform lock adapter",
        terminalization="not authority and never a queue candidate",
        retention="may persist with the target state root",
    ),
    _state_inventory(
        "owner_grant", "owner", "grants", r"^[A-Za-z0-9_-]+\.json$",
        "destination-independent authority; proof digest but no raw proof or reply text",
        "atomic-replace", "owner store", "revoked/consumed grant is never reusable",
        "strictly parse; malformed is sanitized immediately; valid expired records age out",
    ),
    _state_inventory(
        "owner_request", "owner", "requests", r"^[0-9a-f]{64}\.json$",
        "reply text is not retained; terminal receipt and claim authority may be present",
        "atomic-replace", "owner store", "terminal receipt is immutable replay truth",
        "strictly parse; malformed is sanitized immediately; valid records age out",
    ),
    _state_inventory(
        "owner_route_event", "owner", "route_events", r"^[A-Za-z0-9_-]+\.json$",
        "active records may contain raw proof; terminal records are proof-free",
        "atomic-replace", "owner store", "proof is stripped after any terminal decision",
        "malformed is sanitized; terminal canonical hints may age out only after decision exists",
    ),
    _state_inventory(
        "owner_route_decision", "owner", "route_decisions", r"^[A-Za-z0-9_-]+\.json$",
        "proof-free identity and authority digests only",
        "atomic-replace", "owner store", "first no-remint decision is authoritative",
        "retained indefinitely; cleanup must never reopen authority minting",
    ),
    _state_inventory(
        "owner_dead", "owner", ".dead/<source>", r"^.+\.[0-9a-f]{32}\.dead$",
        "proof-free sanitized metadata only", "atomic-replace", "owner store",
        "never restores rejected backing content", "strictly parse and age out",
    ),
    _state_inventory(
        "owner_maintenance", "owner", ".", r"^owner-maintenance-progress\.json$",
        "proof-free directory-cookie temp-reconciliation metadata", "atomic-replace", "owner store",
        "never authorizes or deletes canonical decisions", "retained with owner state root",
    ),
    _state_inventory(
        "owner_cleanup_progress", "owner", ".", r"^owner-cleanup-progress\.json$",
        "proof-free fair-class and directory-cookie cleanup metadata", "atomic-replace", "owner store",
        "never authorizes or deletes canonical decisions", "retained with owner state root",
    ),
    _state_inventory(
        "target_maintenance", "target", ".", r"^target-maintenance-progress\.json$",
        "proof-free directory-cookie temp-reconciliation metadata", "atomic-replace", "target transports",
        "never authorizes dispatch or submission", "retained with target state root",
    ),
    _state_inventory(
        "target_cleanup_progress", "target", ".", r"^target-cleanup-progress\.json$",
        "proof-free fair-class and directory-cookie cleanup metadata", "atomic-replace", "target transports",
        "never authorizes dispatch or submission", "retained with target state root",
    ),
    _state_inventory(
        "target_capsule", "target", ".", r"^active_capsule\.json$",
        "destination-independent capsule authority; no destination or raw proof",
        "atomic-replace", "target submitter and owner transport", "expiry closes submission",
        "strictly parse and remove only after expiry plus retention",
    ),
    _state_inventory(
        "target_outbox", "target", "outbox", r"^[0-9a-f]{64}\.json$",
        "raw proof and reply text", "atomic-create-hard-link", "owner transport",
        "claim then terminal receipt/consumed marker; never republish a stale hidden name",
        "reconcile owned temps first; strict malformed cleanup; valid records age out",
    ),
    _state_inventory(
        "target_claim", "target", "claims", r"^[0-9a-f]{64}\.json$",
        "raw proof and reply text; dispatch boundary state", "atomic-replace", "owner transport",
        "pre-send rolls back; possible-send becomes immutable ambiguity",
        "recover before drain/cleanup; terminal state ages out",
    ),
    _state_inventory(
        "target_receipt", "target", "receipts", r"^[0-9a-f]{64}\.json$",
        "proof-free target-visible terminal receipt", "atomic-replace", "owner transport",
        "first valid terminal receipt wins", "strictly parse and age out",
    ),
    _state_inventory(
        "target_consumed", "target", "consumed", r"^[0-9a-f]{64}\.json$",
        "proof-free terminal tuple marker", "atomic-replace", "owner transport",
        "prevents requeue when receipt repair is needed", "strictly parse and age out",
    ),
    _state_inventory(
        "target_dead", "target", ".dead", r"^[0-9a-f]{64}\.[0-9a-f]{32}\.json$",
        "proof-free sanitized metadata only", "atomic-replace", "owner transport",
        "never restores rejected proof/text", "strictly parse and age out",
    ),
)
CHANNEL_REPLY_STATE_BY_KIND = {item.kind: item for item in CHANNEL_REPLY_STATE_INVENTORY}
OWNER_STATE_DIRECTORIES = frozenset({"grants", "requests", "route_events", "route_decisions", ".dead"})
TARGET_STATE_DIRECTORIES = frozenset({"outbox", "claims", "receipts", "consumed", ".dead"})


class ChannelReplyStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PREPARED = "prepared"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"
    AMBIGUOUS = "ambiguous"


TERMINAL_STATUSES = {
    ChannelReplyStatus.SENT,
    ChannelReplyStatus.FAILED,
    ChannelReplyStatus.DEAD,
    ChannelReplyStatus.AMBIGUOUS,
}


@dataclass(frozen=True, slots=True)
class ChannelReplySubmitRequest:
    """Minimal target-authored request accepted by the Core port."""

    version: int
    grant_ref: str
    request_id: str
    created_at: str
    text: str
    proof: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ChannelReplySubmitRequest":
        unknown_authority = sorted(AUTHORITY_FIELDS.intersection(data))
        if unknown_authority:
            raise ValueError("authority_fields_not_allowed")
        unknown = set(data) - REQUEST_FIELDS
        if unknown:
            raise ValueError("unsupported_request_field")
        version = data.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != PROTOCOL_VERSION:
            raise ValueError("unsupported_version")
        grant_ref = _bounded_token(data.get("grant_ref"), "grant_ref", 256)
        request_id = _bounded_token(data.get("request_id"), "request_id", 160)
        _safe_name(request_id)
        created_at = _canonical_utc_text(data.get("created_at"), "created_at")
        proof = _bounded_token(data.get("proof"), "proof", 256)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text_required")
        if len(text) > MAX_REPLY_TEXT_CHARS or len(text.encode("utf-8")) > MAX_OUTBOX_TEXT_BYTES:
            raise ValueError("text_too_large")
        return cls(PROTOCOL_VERSION, grant_ref, request_id, created_at, text, proof)


@dataclass(frozen=True, slots=True)
class ChannelReplyReceipt:
    status: str
    grant_ref: str
    request_id: str
    message: str
    public_ref: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "grant_ref": self.grant_ref,
            "request_id": self.request_id,
            "message": _bounded_message(self.message),
        }
        if self.public_ref is not None:
            out["public_ref"] = _validated_public_ref(self.public_ref)
        return out


class ChannelReplySubmitPort(Protocol):
    def submit_channel_reply(self, request: ChannelReplySubmitRequest) -> ChannelReplyReceipt:
        """Submit one reply request through an owner-held grant."""


class ClosedChannelReplySubmitPort:
    """Default port: statically composed, normally closed, no side effects."""

    def __init__(
        self,
        message: str = "channel_reply is closed: no owner grant adapter is configured",
    ) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("closed_channel_reply_message_invalid")
        self._message = _bounded_message(message)

    def submit_channel_reply(self, request: ChannelReplySubmitRequest) -> ChannelReplyReceipt:
        return ChannelReplyReceipt(
            status=ChannelReplyStatus.DEAD.value,
            grant_ref=request.grant_ref,
            request_id=request.request_id,
            message=self._message,
        )


class PreSendChannelReplyError(RuntimeError):
    """Raised when no external send could have occurred."""


@dataclass(frozen=True, slots=True)
class OwnerReplyGrant:
    grant_id: str
    grant_ref: str
    proof_digest: str
    target_agent_id: str
    target_agent_name: str
    target_protocol_version: int
    channel: str
    anchor: Mapping[str, Any]
    created_at: str
    expires_at: str
    revoked: bool = False
    claimed_request_id: str | None = None
    consumed_request_id: str | None = None
    route_event_id: str | None = None

    @classmethod
    def issue(
        cls,
        *,
        target_agent_id: str,
        target_agent_name: str,
        target_protocol_version: int,
        channel: str,
        anchor: Mapping[str, Any],
        created_at: str,
        expires_at: str | None = None,
        ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
        route_event_id: str | None = None,
    ) -> tuple["OwnerReplyGrant", str]:
        created = _parse_utc(_canonical_utc_text(created_at, "created_at"))
        ttl = _bounded_ttl(ttl_seconds)
        expiry = _canonical_utc(expires_at) if expires_at is not None else _format_utc(created + timedelta(seconds=ttl))
        grant_id = secrets.token_urlsafe(24)
        proof = secrets.token_urlsafe(32)
        grant = cls(
            grant_id=grant_id,
            grant_ref=f"channel-reply-v1:{grant_id}",
            proof_digest=_digest(proof),
            target_agent_id=_bounded_token(target_agent_id, "target_agent_id", 160),
            target_agent_name=_bounded_target_name(target_agent_name),
            target_protocol_version=_exact_int(target_protocol_version, "target_protocol_version"),
            channel=_bounded_token(channel, "channel", 64),
            anchor=dict(anchor),
            created_at=_format_utc(created),
            expires_at=expiry,
            route_event_id=(_safe_name(route_event_id) if route_event_id is not None else None),
        )
        return grant, proof

    def to_record(self) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "grant_id": self.grant_id,
            "grant_ref": self.grant_ref,
            "proof_digest": self.proof_digest,
            "target_agent_id": self.target_agent_id,
            "target_agent_name": self.target_agent_name,
            "target_protocol_version": self.target_protocol_version,
            "channel": self.channel,
            "anchor": dict(self.anchor),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "claimed_request_id": self.claimed_request_id,
            "consumed_request_id": self.consumed_request_id,
            "route_event_id": self.route_event_id,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "OwnerReplyGrant":
        _require_exact_keys(record, GRANT_FIELDS, "grant")
        version = record.get("version")
        if isinstance(version, bool) or version != PROTOCOL_VERSION:
            raise ValueError("unsupported_grant_version")
        revoked = record["revoked"]
        if not isinstance(revoked, bool):
            raise ValueError("grant_revoked_invalid")
        claimed = _optional_safe(record["claimed_request_id"], "claimed_request_id", 160)
        consumed = _optional_safe(record["consumed_request_id"], "consumed_request_id", 160)
        route_event_id = _optional_safe(record["route_event_id"], "route_event_id", 160)
        anchor = record["anchor"]
        if not isinstance(anchor, Mapping):
            raise ValueError("anchor_invalid")
        grant = cls(
            grant_id=_safe_name(str(record["grant_id"])),
            grant_ref=_bounded_token(record["grant_ref"], "grant_ref", 256),
            proof_digest=_bounded_token(record["proof_digest"], "proof_digest", 128),
            target_agent_id=_bounded_token(record["target_agent_id"], "target_agent_id", 160),
            target_agent_name=_bounded_target_name(record["target_agent_name"]),
            target_protocol_version=_exact_int(record["target_protocol_version"], "target_protocol_version"),
            channel=_bounded_token(record["channel"], "channel", 64),
            anchor=dict(anchor),
            created_at=_canonical_utc_text(record["created_at"], "created_at"),
            expires_at=_canonical_utc_text(record["expires_at"], "expires_at"),
            revoked=revoked,
            claimed_request_id=claimed,
            consumed_request_id=consumed,
            route_event_id=route_event_id,
        )
        expected_ref = f"channel-reply-v1:{grant.grant_id}"
        if grant.grant_ref != expected_ref:
            raise ValueError("grant_ref_mismatch")
        if grant.target_protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported_target_protocol")
        return grant


@dataclass(frozen=True, slots=True)
class ReplyRequestRecord:
    grant_id: str
    request_id: str
    target_agent_id: str
    status: ChannelReplyStatus
    created_at: str
    receipt: ChannelReplyReceipt | None = None
    prepared_at: str | None = None
    claim_token: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "grant_id": self.grant_id,
            "request_id": self.request_id,
            "target_agent_id": self.target_agent_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "prepared_at": self.prepared_at,
            "claim_token": self.claim_token,
            "receipt": _receipt_record(self.receipt),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ReplyRequestRecord":
        _require_exact_keys(record, REQUEST_RECORD_FIELDS, "request")
        version = record.get("version")
        if isinstance(version, bool) or version != PROTOCOL_VERSION:
            raise ValueError("unsupported_request_version")
        receipt_data = record.get("receipt")
        receipt = None
        if receipt_data is not None:
            if not isinstance(receipt_data, Mapping):
                raise ValueError("receipt_invalid")
            receipt = _receipt_from_record(receipt_data)
        prepared_at = record["prepared_at"]
        if prepared_at is not None:
            prepared_at = _canonical_utc_text(prepared_at, "prepared_at")
        status = ChannelReplyStatus(str(record["status"]))
        claim_token = _optional_safe(record["claim_token"], "claim_token", 160)
        if status in TERMINAL_STATUSES:
            if receipt is None:
                raise ValueError("terminal_receipt_required")
            if claim_token is not None:
                raise ValueError("terminal_claim_token_forbidden")
        elif receipt is not None:
            raise ValueError("nonterminal_receipt_forbidden")
        if status in {
            ChannelReplyStatus.CLAIMED,
            ChannelReplyStatus.PREPARED,
            ChannelReplyStatus.SENDING,
        } and claim_token is None:
            raise ValueError("active_claim_token_required")
        if status is ChannelReplyStatus.PENDING and claim_token is not None:
            raise ValueError("pending_claim_token_forbidden")
        parsed = cls(
            grant_id=_safe_name(str(record["grant_id"])),
            request_id=_safe_name(str(record["request_id"])),
            target_agent_id=_bounded_token(record["target_agent_id"], "target_agent_id", 160),
            status=status,
            created_at=_canonical_utc_text(record["created_at"], "created_at"),
            receipt=receipt,
            prepared_at=prepared_at,
            claim_token=claim_token,
        )
        if receipt is not None:
            if receipt.grant_ref != f"channel-reply-v1:{parsed.grant_id}":
                raise ValueError("request_receipt_grant_mismatch")
            if receipt.request_id != parsed.request_id:
                raise ValueError("request_receipt_id_mismatch")
            if receipt.status != parsed.status.value:
                raise ValueError("request_receipt_status_mismatch")
        return parsed


@dataclass(frozen=True, slots=True)
class _SessionDirEntry:
    """The no-follow subset of ``os.DirEntry`` used by Core scans."""

    name: str
    kind: str

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return self.kind == "directory" and not follow_symlinks


class _SessionFilesystem:
    """Lexical Path compatibility over one verified root-relative session.

    Paths remain domain identifiers only.  Every observation or mutation is
    performed through an opaque directory token supplied by the session; no
    mutable root path is resolved or reopened while this facade is active.
    """

    _REQUIRED_METHODS = (
        "verify",
        "open_directory",
        "inspect",
        "scan",
        "read_bytes",
        "atomic_write_bytes",
        "move_entry",
        "remove_owned_entry",
        "fsync_directory",
    )

    def __init__(self, root: Path, session: ChannelReplyMutationSession) -> None:
        if getattr(session, "protocol_marker", None) != "channel-reply-mutation-session/v1":
            raise OSError("channel_reply mutation lock yielded an invalid session marker")
        if getattr(session, "root", None) is None or getattr(session, "root_identity", None) is None:
            raise OSError("channel_reply mutation lock yielded an incomplete session")
        if any(not callable(getattr(session, name, None)) for name in self._REQUIRED_METHODS):
            raise OSError("channel_reply mutation lock yielded an incomplete session")
        self.root_path = root
        self.session = session
        self._directories: dict[tuple[str, ...], Any] = {(): session.root}
        # A failed strict read pins the exact prevalidated object for any
        # immediate quarantine.  A same-UID replacement must never become the
        # object removed merely because it inherited the canonical name.
        self._failed_read_identities: dict[tuple[str, ...], Any] = {}
        session.verify()

    def _relative_parts(self, path: Path) -> tuple[str, ...]:
        try:
            relative = path.relative_to(self.root_path)
        except ValueError as exc:
            raise ValueError("path_escape") from exc
        parts = relative.parts
        if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts):
            raise ValueError("path_escape")
        return parts

    def contains(self, path: Path) -> bool:
        try:
            self._relative_parts(path)
        except ValueError:
            return False
        return True

    def directory(self, path: Path, *, create_private: bool = False):
        parts = self._relative_parts(path)
        token = self.session.root
        prefix: tuple[str, ...] = ()
        if not parts:
            self.session.verify()
            return token
        for index, part in enumerate(parts):
            prefix += (part,)
            cached = self._directories.get(prefix)
            if cached is None:
                cached = self.session.open_directory(
                    token,
                    part,
                    create_private=create_private and index == len(parts) - 1,
                )
                self._directories[prefix] = cached
            token = cached
        return token

    def inspect(self, path: Path):
        parts = self._relative_parts(path)
        if not parts:
            self.session.verify()
            return None
        parent = self.directory(path.parent)
        return self.session.inspect(parent, path.name)

    def lexists(self, path: Path) -> bool:
        if path == self.root_path:
            self.session.verify()
            return True
        try:
            return self.inspect(path) is not None
        except FileNotFoundError:
            return False

    def require_private_directory(self, path: Path) -> None:
        self.directory(path)

    def scan_page(
        self,
        directory: Path,
        *,
        max_inspections: int,
        max_candidates: int | None = None,
        cursor: DirectoryScanCursor | None = None,
    ) -> tuple[tuple[_SessionDirEntry, ...], int, bool, DirectoryScanCursor | None]:
        """Return one descriptor-relative inventory page and its continuation."""
        inspections = max(0, int(max_inspections))
        candidates = inspections if max_candidates is None else max(0, int(max_candidates))
        batch = self.session.scan(
            self.directory(directory),
            budget=SessionDirectoryScanBudget(
                inspections=inspections,
                candidates=candidates,
            ),
            cursor=cursor,
        )
        if batch.inspections < 0 or batch.inspections > inspections:
            raise OSError("channel_reply mutation session exceeded scan budget")
        if len(batch.entries) > candidates:
            raise OSError("channel_reply mutation session exceeded candidate budget")
        if batch.complete and batch.next_cursor is not None:
            raise OSError("channel_reply mutation session returned invalid terminal cursor")
        if not batch.complete and batch.next_cursor is None:
            raise OSError("channel_reply mutation session omitted scan continuation")
        return (
            tuple(_SessionDirEntry(entry.name, entry.kind) for entry in batch.entries),
            batch.inspections,
            batch.complete,
            batch.next_cursor,
        )

    def scan(self, directory: Path, *, max_entries: int = 512) -> tuple[_SessionDirEntry, ...]:
        """Compatibility exhaustive scan implemented as bounded native pages."""
        result: list[_SessionDirEntry] = []
        cursor: DirectoryScanCursor | None = None
        while True:
            page, _inspections, complete, cursor = self.scan_page(
                directory,
                max_inspections=max(1, max_entries),
                max_candidates=max(1, max_entries),
                cursor=cursor,
            )
            result.extend(page)
            if complete:
                return tuple(result)

    def glob(self, directory: Path, pattern: str) -> tuple[Path, ...]:
        return tuple(
            directory / entry.name
            for entry in self.scan(directory)
            if fnmatch.fnmatchcase(entry.name, pattern)
        )

    def read_json(self, path: Path, *, max_bytes: int) -> Any:
        parts = self._relative_parts(path)
        info = self.inspect(path)
        if info is None:
            raise FileNotFoundError(path)
        try:
            if not info.private_regular_single_link:
                raise ValueError("record_not_private_regular_single_link")
            if info.size > max_bytes:
                raise ValueError("record_too_large")
            data = self.session.read_bytes(
                self.directory(path.parent),
                path.name,
                max_bytes=max_bytes,
                expected=info.identity,
            )
            if len(data) > max_bytes:
                raise ValueError("record_too_large")
            value = json.loads(
                data.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except Exception:
            self._failed_read_identities[parts] = info.identity
            raise
        self._failed_read_identities.pop(parts, None)
        return value

    def write_json(self, path: Path, payload: Mapping[str, Any], *, create: bool) -> None:
        algorithm = "atomic-create-hard-link" if create else "atomic-replace"
        _state_kind_for_path(path, writer_algorithm=algorithm)
        parent = self.directory(path.parent)
        data = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        result = self.session.atomic_write_bytes(
            parent,
            path.name,
            data,
            mode=algorithm,
        )
        if create and result.state == "exists":
            raise FileExistsError(path)
        if result.state not in ({"created"} if create else {"created", "replaced"}):
            raise OSError("channel_reply atomic write result invalid")
        if result.entry is None or not result.entry.private_regular_single_link:
            raise OSError("channel_reply atomic write publication invalid")

    def remove(
        self,
        path: Path,
        *,
        budget: OwnedRemovalBudget | None = None,
    ) -> OwnedRemovalResult:
        local = budget or OwnedRemovalBudget()
        parts = self._relative_parts(path)
        expected = self._failed_read_identities.pop(parts, None)
        if expected is None:
            try:
                info = self.inspect(path)
            except FileNotFoundError:
                return OwnedRemovalResult("absent")
            if info is None:
                return OwnedRemovalResult("absent")
            expected = info.identity
        result = self.session.remove_owned_entry(
            self.directory(path.parent),
            path.name,
            budget=SessionOwnedRemovalBudget(
                inspections=local.inspections,
                removals=local.removals,
                max_depth=local.max_depth,
                candidates=max(64, local.removals),
            ),
            expected=expected,
        )
        return OwnedRemovalResult(result.state, result.inspections, result.removals, result.error)

    def move(self, source: Path, destination: Path, *, replace_destination: bool) -> None:
        info = self.inspect(source)
        if info is None:
            raise FileNotFoundError(source)
        if not info.private_regular_single_link:
            raise OSError("channel_reply move source rejected")
        result = self.session.move_entry(
            self.directory(source.parent),
            source.name,
            self.directory(destination.parent),
            destination.name,
            expected_source=info.identity,
            disposition=(
                "replace-destination-entry"
                if replace_destination
                else "destination-must-be-absent"
            ),
        )
        if result.state != "moved" or result.entry is None:
            raise OSError(f"channel_reply move failed closed: {result.state}")

    def fsync(self, directory: Path) -> None:
        self.session.fsync_directory(self.directory(directory))


_ACTIVE_SESSION_FILESYSTEM: ContextVar[_SessionFilesystem | None] = ContextVar(
    "channel_reply_active_session_filesystem",
    default=None,
)


@contextlib.contextmanager
def _bind_mutation_session(root: Path, session: ChannelReplyMutationSession):
    facade = _SessionFilesystem(root, session)
    token = _ACTIVE_SESSION_FILESYSTEM.set(facade)
    try:
        try:
            yield facade
        finally:
            session.verify()
    finally:
        _ACTIVE_SESSION_FILESYSTEM.reset(token)


def _session_filesystem(path: Path | None = None) -> _SessionFilesystem | None:
    facade = _ACTIVE_SESSION_FILESYSTEM.get()
    if facade is not None and path is not None and not facade.contains(path):
        raise ValueError("path_escape")
    return facade



def _glob_paths(directory: Path, pattern: str) -> tuple[Path, ...]:
    facade = _session_filesystem(directory)
    if facade is not None:
        return facade.glob(directory, pattern)
    return tuple(directory.glob(pattern))


def _bounded_matching_paths(
    directory: Path,
    *,
    pattern: str,
    cursor_key: str,
    cursors: dict[str, DirectoryScanCursor | None],
    max_inspections: int,
) -> tuple[tuple[Path, ...], int]:
    """Charge one descriptor-relative page; continue across mutation sessions."""
    facade = _session_filesystem(directory)
    if facade is None:
        raise OSError("channel_reply bounded inventory requires mutation session")
    limit = max(0, int(max_inspections))
    if limit == 0:
        return (), 0
    cursor = cursors.get(cursor_key)
    try:
        entries, inspected, complete, next_cursor = facade.scan_page(
            directory,
            max_inspections=limit,
            max_candidates=limit,
            cursor=cursor,
        )
    except OSError as exc:
        if cursor is None or "cursor identity mismatch" not in str(exc):
            raise
        entries, inspected, complete, next_cursor = facade.scan_page(
            directory,
            max_inspections=limit,
            max_candidates=limit,
            cursor=None,
        )
    cursors[cursor_key] = None if complete else next_cursor
    return tuple(
        directory / entry.name
        for entry in entries
        if fnmatch.fnmatchcase(entry.name, pattern)
    ), inspected


def _move_path(source: Path, destination: Path, *, replace_destination: bool) -> None:
    facade = _session_filesystem(source)
    if facade is not None:
        if not facade.contains(destination):
            raise ValueError("path_escape")
        facade.move(source, destination, replace_destination=replace_destination)
        return
    if not replace_destination and _path_lexists(destination):
        raise FileExistsError(destination)
    os.replace(source, destination)


def _remove_file_or_empty_directory(path: Path) -> OwnedRemovalResult:
    """Remove rejected leaves without recursively destroying an obstruction."""
    facade = _session_filesystem(path)
    if facade is not None:
        try:
            # One charged outer candidate includes one bounded descriptor-relative
            # removal attempt. A regular file or empty directory needs one removal
            # inspection; a nonempty directory exhausts that inspection budget
            # before touching its first child and is restored intact.
            result = facade.remove(
                path,
                budget=OwnedRemovalBudget(inspections=1, removals=1, max_depth=1),
            )
            if result.state in {"removed", "absent"}:
                return result
            return OwnedRemovalResult(
                "rejected",
                result.inspections,
                result.removals,
                result.error,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            return OwnedRemovalResult("retryable", error=type(exc).__name__)
    try:
        st = path.lstat()
        if stat.S_ISDIR(st.st_mode):
            path.rmdir()
        else:
            path.unlink()
        _fsync_dir(path.parent)
        return OwnedRemovalResult("removed", 1, 1)
    except FileNotFoundError:
        return OwnedRemovalResult("absent", 1, 0)
    except OSError as exc:
        return OwnedRemovalResult("retryable", 1, 0, type(exc).__name__)


def _record_kind(path: Path) -> str:
    facade = _session_filesystem(path)
    if facade is not None:
        info = facade.inspect(path)
        if info is None:
            raise FileNotFoundError(path)
        if info.kind == "symlink":
            return "symlink"
        if info.kind == "regular":
            return "hardlink" if info.link_count != 1 else "regular"
        if info.kind == "directory":
            return "directory"
        return "nonregular"
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISREG(st.st_mode):
        return "hardlink" if getattr(st, "st_nlink", 1) != 1 else "regular"
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    return "nonregular"


class ChannelReplyFileStore:
    """Durable owner-side grant/request/receipt state.

    The store derives every path from canonical identifiers, quarantines invalid
    authority records under `.dead`, and relies on an injected mutation lock for
    cross-process transaction boundaries.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        mutation_lock: ChannelReplyMutationLockPort,
        max_record_bytes: int = MAX_RECORD_BYTES,
    ) -> None:
        self.root = _prepare_root(Path(root))
        self._lock = threading.RLock()
        self._mutation_lock = mutation_lock
        self._root_identity: ChannelReplyRootIdentity | None = None
        self._max_record_bytes = max_record_bytes
        self._cleanup_cursors: dict[str, str] = {}
        self._scan_cursors: dict[str, DirectoryScanCursor | None] = {}
        self.last_cleanup_inspections = 0
        self._grants_dir = self.root / CHANNEL_REPLY_STATE_BY_KIND["owner_grant"].directory
        self._requests_dir = self.root / CHANNEL_REPLY_STATE_BY_KIND["owner_request"].directory
        self._events_dir = self.root / CHANNEL_REPLY_STATE_BY_KIND["owner_route_event"].directory
        self._decisions_dir = self.root / CHANNEL_REPLY_STATE_BY_KIND["owner_route_decision"].directory
        self._dead_dir = self.root / ".dead"
        self._maintenance_progress = self.root / "owner-maintenance-progress.json"
        self._cleanup_progress = self.root / "owner-cleanup-progress.json"
        for directory in (
            self._grants_dir,
            self._requests_dir,
            self._events_dir,
            self._decisions_dir,
            self._dead_dir,
        ):
            _ensure_private_dir(directory)
        self.recover_nonterminal()

    def save_grant(self, grant: OwnerReplyGrant) -> None:
        with self._transaction():
            self._write_json(self._grant_path(grant.grant_id), grant.to_record())

    def issue_or_reuse_grant(
        self,
        *,
        route_event_id: str,
        grant_factory: Callable[[], tuple[OwnerReplyGrant, str]],
        now: str,
    ) -> tuple[OwnerReplyGrant | None, str | None, bool]:
        """Create authority once, guarded first by a proof-free no-remint decision."""
        route_event_id = _safe_name(route_event_id)
        now = _canonical_utc_text(now, "now")
        with self._transaction():
            decision, invalid_decision = self._read_route_decision(route_event_id)
            if invalid_decision:
                # An existing malformed decision name is itself fail-closed. Never
                # remove it and later turn absence into permission to call a factory.
                return None, None, False
            if decision is None and _path_lexists(self._event_path(route_event_id)):
                self._migrate_route_event_decision(route_event_id, now=now)
                decision, invalid_decision = self._read_route_decision(route_event_id)
                if invalid_decision or decision is None:
                    return None, None, False
            if decision is not None:
                if decision["decision"] != "active":
                    return None, None, False
                existing, invalid_existing = self._read_route_event(route_event_id)
                if invalid_existing or existing is None or existing["decision"] != "active":
                    self._terminalize_route(
                        route_event_id,
                        decision="quarantined",
                        now=now,
                        event=existing,
                    )
                    return None, None, False
                grant = self._read_grant_by_id(str(existing["grant_id"]))
                terminal: str | None = None
                expected_authority = _route_authority_digest(
                    route_event_id,
                    str(existing["grant_id"]),
                    str(existing["grant_ref"]),
                    str(existing["proof_digest"]),
                )
                if not hmac.compare_digest(str(decision["authority_digest"]), expected_authority):
                    terminal = "quarantined"
                elif grant is None:
                    terminal = "missing"
                elif grant.route_event_id != route_event_id:
                    terminal = "quarantined"
                elif grant.grant_ref != existing["grant_ref"]:
                    terminal = "quarantined"
                elif grant.proof_digest != existing["proof_digest"]:
                    terminal = "quarantined"
                elif grant.revoked:
                    terminal = "revoked"
                elif grant.consumed_request_id is not None:
                    terminal = "retired"
                elif not _not_expired(grant.expires_at, now):
                    terminal = "expired"
                proof = str(existing["proof"])
                if terminal is None and (
                    not proof or not hmac.compare_digest(_digest(proof), grant.proof_digest)
                ):
                    terminal = "quarantined"
                if terminal is not None:
                    self._terminalize_route(
                        route_event_id,
                        decision=terminal,
                        now=now,
                        event=existing,
                    )
                    return None, None, False
                return grant, proof, False

            # Durable reservation is established before executing arbitrary factory
            # code. A crash here can lose an opportunity, but can never remint.
            self._write_route_decision(
                route_event_id,
                decision="reserved",
                now=now,
                authority_digest=None,
            )
            grant, proof = grant_factory()
            if grant.route_event_id != route_event_id:
                grant = replace(grant, route_event_id=route_event_id)
            if grant.grant_ref != f"channel-reply-v1:{grant.grant_id}":
                self._terminalize_route(route_event_id, decision="quarantined", now=now)
                raise ValueError("grant_ref_mismatch")
            if not hmac.compare_digest(grant.proof_digest, _digest(proof)):
                self._terminalize_route(route_event_id, decision="quarantined", now=now)
                raise ValueError("grant_proof_mismatch")
            authority_digest = _route_authority_digest(
                route_event_id,
                grant.grant_id,
                grant.grant_ref,
                grant.proof_digest,
            )
            self._write_json(grant_path := self._grant_path(grant.grant_id), grant.to_record())
            _assert_contained(self.root, grant_path)
            self._write_route_decision(
                route_event_id,
                decision="active",
                now=now,
                authority_digest=authority_digest,
            )
            event = _route_event_record(route_event_id=route_event_id, grant=grant, proof=proof)
            try:
                self._write_json(self._event_path(route_event_id), event)
            except (OSError, ValueError):
                # Canonical route storage is only a proof-bearing replay hint. The
                # independent proof-free decision remains authoritative.
                self._terminalize_route(
                    route_event_id,
                    decision="quarantined",
                    now=now,
                    event=event,
                )
                return None, None, False
            return grant, proof, True

    def get_grant(self, grant_ref: str) -> OwnerReplyGrant | None:
        grant_id = _grant_id_from_ref(grant_ref)
        if not grant_id:
            return None
        with self._transaction():
            return self._read_grant_by_id(grant_id)

    def revoke_grant(self, grant_ref: str) -> bool:
        grant_id = _grant_id_from_ref(grant_ref)
        if not grant_id:
            return False
        with self._transaction():
            grant = self._read_grant_by_id(grant_id)
            if grant is None:
                return False
            revoked = replace(grant, revoked=True)
            self._write_json(self._grant_path(grant.grant_id), revoked.to_record())
            if grant.route_event_id is not None:
                event, invalid_existing = self._read_route_event(grant.route_event_id)
                decision = "quarantined" if invalid_existing else "revoked"
                if event is not None and event.get("grant_id") != grant.grant_id:
                    decision = "quarantined"
                self._terminalize_route(
                    grant.route_event_id,
                    decision=decision,
                    now=_utc_now(),
                    event=event,
                )
            return True

    def get_request(self, grant_id: str, request_id: str) -> ReplyRequestRecord | None:
        with self._transaction():
            return self._read_request(_safe_name(grant_id), _safe_name(request_id))

    def put_request(self, record: ReplyRequestRecord) -> None:
        with self._transaction():
            self._write_json(self._request_path(record.grant_id, record.request_id), record.to_record())

    def claim_request(
        self,
        *,
        grant_ref: str,
        request: ChannelReplySubmitRequest,
        target_agent_id: str,
        now: str,
        request_max_age_seconds: int,
        future_skew_seconds: int,
    ) -> tuple[OwnerReplyGrant | None, ChannelReplyReceipt | None, str | None]:
        with self._transaction():
            grant_id = _grant_id_from_ref(grant_ref)
            if not grant_id or grant_ref != request.grant_ref:
                return None, _fail_receipt(request, "grant unavailable"), None
            grant = self._read_grant_by_id(grant_id)
            if grant is None:
                return None, _fail_receipt(request, "grant unavailable"), None
            existing = self._read_request(grant.grant_id, request.request_id)
            if existing is not None:
                if existing.receipt is not None:
                    if existing.status in {
                        ChannelReplyStatus.SENT,
                        ChannelReplyStatus.AMBIGUOUS,
                    }:
                        if grant.consumed_request_id is None:
                            grant = replace(
                                grant,
                                claimed_request_id=grant.claimed_request_id or existing.request_id,
                                consumed_request_id=existing.request_id,
                            )
                            self._write_json(self._grant_path(grant.grant_id), grant.to_record())
                        self._retire_route_event_for_grant(grant)
                    return grant, existing.receipt, None
                if existing.status in {
                    ChannelReplyStatus.CLAIMED,
                    ChannelReplyStatus.PREPARED,
                }:
                    # Another caller owns the dispatch token.  A duplicate may
                    # observe pending state, but can never fall through to send.
                    return grant, _pending_receipt(request), None
                if existing.status is ChannelReplyStatus.SENDING:
                    receipt = _ambiguous_receipt(request)
                    current = self._read_grant_by_id(grant.grant_id)
                    self._write_json(
                        self._request_path(grant.grant_id, request.request_id),
                        replace(
                            existing,
                            status=ChannelReplyStatus.AMBIGUOUS,
                            receipt=receipt,
                            claim_token=None,
                        ).to_record(),
                    )
                    if current is not None:
                        current = replace(
                            current,
                            claimed_request_id=request.request_id,
                            consumed_request_id=request.request_id,
                        )
                        self._write_json(self._grant_path(grant.grant_id), current.to_record())
                    self._retire_route_event_for_grant(current or grant)
                    return grant, receipt, None
            if grant.consumed_request_id == request.request_id or (
                grant.claimed_request_id == request.request_id and existing is None
            ):
                receipt = _ambiguous_receipt(request)
                self._write_json(
                    self._request_path(grant.grant_id, request.request_id),
                    ReplyRequestRecord(
                        grant_id=grant.grant_id,
                        request_id=request.request_id,
                        target_agent_id=target_agent_id,
                        status=ChannelReplyStatus.AMBIGUOUS,
                        created_at=request.created_at,
                        receipt=receipt,
                    ).to_record(),
                )
                if grant.consumed_request_id != request.request_id:
                    grant = replace(
                        grant,
                        claimed_request_id=grant.claimed_request_id or request.request_id,
                        consumed_request_id=request.request_id,
                    )
                    self._write_json(self._grant_path(grant.grant_id), grant.to_record())
                self._retire_route_event_for_grant(grant)
                return grant, receipt, None
            reason = _claim_rejection(
                grant,
                request,
                target_agent_id=target_agent_id,
                now=now,
                request_max_age_seconds=request_max_age_seconds,
                future_skew_seconds=future_skew_seconds,
            )
            if reason is not None:
                receipt = _fail_receipt(request, reason)
                self._write_json(
                    self._request_path(grant.grant_id, request.request_id),
                    ReplyRequestRecord(
                        grant_id=grant.grant_id,
                        request_id=request.request_id,
                        target_agent_id=target_agent_id,
                        status=ChannelReplyStatus.DEAD,
                        created_at=request.created_at,
                        receipt=receipt,
                    ).to_record(),
                )
                return grant, receipt, None
            if grant.claimed_request_id and grant.claimed_request_id != request.request_id:
                receipt = _fail_receipt(request, "grant already claimed")
                self._write_json(
                    self._request_path(grant.grant_id, request.request_id),
                    ReplyRequestRecord(
                        grant_id=grant.grant_id,
                        request_id=request.request_id,
                        target_agent_id=target_agent_id,
                        status=ChannelReplyStatus.DEAD,
                        created_at=request.created_at,
                        receipt=receipt,
                    ).to_record(),
                )
                return grant, receipt, None
            if grant.consumed_request_id and grant.consumed_request_id != request.request_id:
                receipt = _fail_receipt(request, "grant already consumed")
                self._write_json(
                    self._request_path(grant.grant_id, request.request_id),
                    ReplyRequestRecord(
                        grant_id=grant.grant_id,
                        request_id=request.request_id,
                        target_agent_id=target_agent_id,
                        status=ChannelReplyStatus.DEAD,
                        created_at=request.created_at,
                        receipt=receipt,
                    ).to_record(),
                )
                return grant, receipt, None
            claim_token = secrets.token_urlsafe(24)
            claimed_grant = replace(grant, claimed_request_id=request.request_id)
            claimed = ReplyRequestRecord(
                grant_id=grant.grant_id,
                request_id=request.request_id,
                target_agent_id=target_agent_id,
                status=ChannelReplyStatus.CLAIMED,
                created_at=request.created_at,
                claim_token=claim_token,
            )
            self._write_json(self._grant_path(grant.grant_id), claimed_grant.to_record())
            self._write_json(self._request_path(grant.grant_id, request.request_id), claimed.to_record())
            return claimed_grant, None, claim_token

    def mark_prepared(
        self,
        grant: OwnerReplyGrant,
        request: ChannelReplySubmitRequest,
        now: str,
        claim_token: str,
    ) -> bool:
        return self._transition_request(
            grant,
            request,
            expected=ChannelReplyStatus.CLAIMED,
            status=ChannelReplyStatus.PREPARED,
            claim_token=claim_token,
            prepared_at=now,
        )

    def mark_sending(
        self,
        grant: OwnerReplyGrant,
        request: ChannelReplySubmitRequest,
        claim_token: str,
    ) -> bool:
        return self._transition_request(
            grant,
            request,
            expected=ChannelReplyStatus.PREPARED,
            status=ChannelReplyStatus.SENDING,
            claim_token=claim_token,
        )

    def current_receipt(self, request: ChannelReplySubmitRequest) -> ChannelReplyReceipt:
        grant_id = _grant_id_from_ref(request.grant_ref)
        if grant_id is None:
            return _fail_receipt(request, "grant unavailable")
        with self._transaction():
            record = self._read_request(grant_id, request.request_id)
            if record is not None and record.receipt is not None:
                return record.receipt
            return _pending_receipt(request)

    def finish_request(
        self,
        grant: OwnerReplyGrant,
        request: ChannelReplySubmitRequest,
        *,
        status: ChannelReplyStatus,
        receipt: ChannelReplyReceipt,
        consume: bool,
        claim_token: str,
    ) -> ChannelReplyReceipt:
        with self._transaction():
            record = self._read_request(grant.grant_id, request.request_id)
            if record is not None and record.receipt is not None:
                if record.status in {
                    ChannelReplyStatus.SENT,
                    ChannelReplyStatus.AMBIGUOUS,
                }:
                    current = self._read_grant_by_id(grant.grant_id) or grant
                    if current.consumed_request_id is None:
                        current = replace(
                            current,
                            claimed_request_id=current.claimed_request_id or request.request_id,
                            consumed_request_id=request.request_id,
                        )
                        self._write_json(self._grant_path(current.grant_id), current.to_record())
                    self._retire_route_event_for_grant(current)
                return record.receipt
            if record is None:
                # The caller may already have crossed the send boundary. Missing
                # accounting can only be represented as durable ambiguity.
                receipt = _ambiguous_receipt(request)
                status = ChannelReplyStatus.AMBIGUOUS
                consume = True
                record = ReplyRequestRecord(
                    grant_id=grant.grant_id,
                    request_id=request.request_id,
                    target_agent_id=grant.target_agent_id,
                    status=status,
                    created_at=request.created_at,
                    receipt=receipt,
                )
            elif record.claim_token != claim_token:
                return _pending_receipt(request)
            else:
                record = replace(
                    record,
                    status=status,
                    receipt=receipt,
                    claim_token=None,
                )
            current = self._read_grant_by_id(grant.grant_id)
            # Terminal tuple truth is persisted in request then grant order. Route
            # retirement is only best-effort proof cleanup and must never prevent
            # conservative accounting after an external send may have occurred.
            self._write_json(
                self._request_path(grant.grant_id, request.request_id),
                record.to_record(),
            )
            persisted_grant = current or grant
            if current is not None:
                consumed = request.request_id if consume else current.consumed_request_id
                if status in {ChannelReplyStatus.SENT, ChannelReplyStatus.AMBIGUOUS}:
                    consumed = request.request_id
                persisted_grant = replace(
                    current,
                    claimed_request_id=current.claimed_request_id or request.request_id,
                    consumed_request_id=consumed,
                )
                self._write_json(self._grant_path(grant.grant_id), persisted_grant.to_record())
            if status in {ChannelReplyStatus.SENT, ChannelReplyStatus.AMBIGUOUS}:
                self._retire_route_event_for_grant(persisted_grant)
            return receipt

    def recover_nonterminal(self) -> None:
        with self._transaction():
            self._recover_route_decisions(now=_utc_now())
            for path in sorted(_glob_paths(self._requests_dir, "*.json")):
                record = self._read_request_path(path)
                if record is None:
                    continue
                if record.status in {
                    ChannelReplyStatus.SENT,
                    ChannelReplyStatus.AMBIGUOUS,
                }:
                    grant = self._read_grant_by_id(record.grant_id)
                    if grant is not None:
                        if grant.consumed_request_id is None:
                            grant = replace(
                                grant,
                                claimed_request_id=grant.claimed_request_id or record.request_id,
                                consumed_request_id=record.request_id,
                            )
                            self._write_json(self._grant_path(record.grant_id), grant.to_record())
                        self._retire_route_event_for_grant(grant)
                    continue
                if record.status is ChannelReplyStatus.SENDING:
                    receipt = _ambiguous_receipt_ref(record.grant_id, record.request_id)
                    self._write_json(
                        path,
                        replace(
                            record,
                            status=ChannelReplyStatus.AMBIGUOUS,
                            receipt=receipt,
                            claim_token=None,
                        ).to_record(),
                    )
                    grant = self._read_grant_by_id(record.grant_id)
                    if grant is not None:
                        grant = replace(
                            grant,
                            claimed_request_id=grant.claimed_request_id or record.request_id,
                            consumed_request_id=grant.consumed_request_id or record.request_id,
                        )
                        self._write_json(self._grant_path(record.grant_id), grant.to_record())
                        self._retire_route_event_for_grant(grant)
                elif record.status in {
                    ChannelReplyStatus.PENDING,
                    ChannelReplyStatus.CLAIMED,
                    ChannelReplyStatus.PREPARED,
                }:
                    grant = self._read_grant_by_id(record.grant_id)
                    if grant is not None and grant.claimed_request_id == record.request_id:
                        self._write_json(
                            self._grant_path(record.grant_id),
                            replace(grant, claimed_request_id=None).to_record(),
                        )
                    self._write_json(
                        path,
                        replace(
                            record,
                            status=ChannelReplyStatus.PENDING,
                            prepared_at=None,
                            claim_token=None,
                        ).to_record(),
                    )

    def cleanup_retained(
        self,
        *,
        now: str,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        route_event_tombstone_seconds: int = DEFAULT_ROUTE_EVENT_TOMBSTONE_SECONDS,
        max_records: int | None = None,
    ) -> int:
        """Prune strict state without ever deleting the no-remint decision ledger.

        ``max_records`` is a hard candidate-inspection budget for scheduled cleanup.
        The default preserves the exhaustive maintenance API used by explicit Core
        callers; Telegram's cadence always supplies a finite value.
        """
        current = _parse_utc(now)
        cutoff = current - timedelta(seconds=max(0, retention_seconds))
        tombstone_cutoff = current - timedelta(seconds=max(0, route_event_tombstone_seconds))
        removed = 0
        remaining = None if max_records is None else max(0, int(max_records))
        self.last_cleanup_inspections = 0
        surface_names = ("events", "requests", "grants", "dead", "temp-reconcile")
        surface_quotas: dict[str, int] | None = None

        def candidates(directory: Path, pattern: str, surface: str) -> tuple[Path, ...]:
            nonlocal remaining
            group = "dead" if surface.startswith("dead:") else surface
            if remaining == 0 or surface_quotas is not None and surface_quotas[group] == 0:
                return ()
            if remaining is None:
                paths = sorted(_glob_paths(directory, pattern))
                self.last_cleanup_inspections += len(paths)
                return tuple(paths)
            limit = min(remaining, surface_quotas[group])
            selected, inspected = _bounded_matching_paths(
                directory,
                pattern=pattern,
                cursor_key=f"owner-cleanup:{surface}",
                cursors=self._scan_cursors,
                max_inspections=limit,
            )
            self.last_cleanup_inspections += inspected
            remaining -= inspected
            surface_quotas[group] -= inspected
            return selected

        with self._transaction(reconcile=remaining is None):
            if remaining is not None:
                turn, restored = _read_cleanup_progress(
                    self._cleanup_progress,
                    scope="owner",
                    class_count=len(surface_names),
                    cursor_keys=_OWNER_CLEANUP_CURSOR_KEYS,
                )
                self._scan_cursors.update(restored)
                order = surface_names[turn:] + surface_names[:turn]
                surface_quotas = {name: 0 for name in surface_names}
                for index in range(remaining):
                    surface_quotas[order[index % len(order)]] += 1
                next_turn = (turn + 1) % len(surface_names)
            if remaining is not None and surface_quotas["temp-reconcile"]:
                temp_limit = min(remaining, surface_quotas["temp-reconcile"])
                inspected = self._reconcile_owned_temps(max_inspections=temp_limit)
                self.last_cleanup_inspections += inspected
                remaining -= inspected
                surface_quotas["temp-reconcile"] -= inspected
            # Exhaustive recovery remains available to explicit callers. Scheduled
            # bounded cleanup folds recovery into the inspected event candidates
            # below so recovery cannot independently scan the whole state tree.
            if remaining is None:
                self._recover_route_decisions(now=now)
            for path in candidates(self._events_dir, "*.json", "events"):
                if not _canonical_name_matches("owner_route_event", path.name):
                    continue
                event_id = path.stem
                decision_record, invalid_decision = self._read_route_decision(event_id)
                event, invalid_existing = self._read_route_event(event_id)
                if invalid_decision:
                    continue
                if decision_record is None:
                    self._migrate_route_event_decision(event_id, now=now)
                    decision_record, invalid_decision = self._read_route_decision(event_id)
                if invalid_decision or decision_record is None:
                    continue
                terminal: str | None = None
                if invalid_existing:
                    terminal = "quarantined"
                elif decision_record["decision"] == "active":
                    if event is None or event["decision"] != "active":
                        terminal = "quarantined"
                    else:
                        grant = self._read_grant_by_id(str(event["grant_id"]))
                        expected_authority = _route_authority_digest(
                            event_id,
                            str(event["grant_id"]),
                            str(event["grant_ref"]),
                            str(event["proof_digest"]),
                        )
                        if not hmac.compare_digest(
                            str(decision_record["authority_digest"]), expected_authority
                        ):
                            terminal = "quarantined"
                        elif grant is None:
                            terminal = "missing"
                        elif grant.route_event_id != event_id:
                            terminal = "quarantined"
                        elif grant.revoked:
                            terminal = "revoked"
                        elif not _not_expired(grant.expires_at, now):
                            terminal = "expired"
                        elif grant.consumed_request_id is not None:
                            terminal = "retired"
                        elif grant.proof_digest != event["proof_digest"]:
                            terminal = "quarantined"
                if terminal is not None:
                    self._terminalize_route(
                        event_id,
                        decision=terminal,
                        now=now,
                        event=event,
                    )
                    decision_record, _ = self._read_route_decision(event_id)
                elif decision_record["decision"] != "active" and event is not None:
                    self._best_effort_terminal_event(
                        event_id,
                        decision=str(decision_record["decision"]),
                        event=event,
                        now=now,
                    )
                if (
                    decision_record is not None
                    and decision_record["decision"] != "active"
                    and _parse_utc(str(decision_record["created_at"])) < tombstone_cutoff
                ):
                    try:
                        outcome = _remove_owned_path(path)
                        if outcome.state in {"absent", "removed"}:
                            removed += 1
                    except OSError:
                        pass

            # Parse canonical authority/state with the actual schemas. Missing,
            # non-string, or noncanonical timestamps are malformed and therefore
            # sanitized immediately rather than retained by a permissive `continue`.
            for path in candidates(self._requests_dir, "*.json", "requests"):
                if not _canonical_name_matches("owner_request", path.name):
                    continue
                existed = _path_lexists(path)
                record = self._read_request_path(path)
                if record is None:
                    if existed and not _path_lexists(path):
                        removed += 1
                    continue
                if _parse_utc(record.created_at) < cutoff:
                    if _remove_owned_path(path).state in {"absent", "removed", "progress"}:
                        removed += 1
            for path in candidates(self._grants_dir, "*.json", "grants"):
                if not _canonical_name_matches("owner_grant", path.name):
                    continue
                existed = _path_lexists(path)
                grant = self._read_grant_by_id(path.stem)
                if grant is None:
                    if existed and not _path_lexists(path):
                        removed += 1
                    continue
                if _parse_utc(grant.expires_at) < cutoff:
                    if _remove_owned_path(path).state in {"absent", "removed", "progress"}:
                        removed += 1
            # Owner quarantine contains only generated proof-free metadata. Scan
            # every source subdirectory without following links; unknown legacy
            # raw records have no valid metadata timestamp and are removed
            # immediately rather than retaining possible bearer material.
            dead_sources = (
                _nofollow_directory_children(self._dead_dir)
                if remaining is None
                else tuple(
                    path
                    for name in ("grants", "requests", "route_events", "route_decisions")
                    if _path_lexists(path := self._dead_dir / name)
                )
            )
            for source_dir in dead_sources:
                for path in candidates(source_dir, "*", f"dead:{source_dir.name}"):
                    try:
                        if _record_kind(path) == "directory":
                            raise ValueError("owner_dead_directory")
                        data = _strict_read_json(path, max_bytes=self._max_record_bytes)
                        _require_exact_keys(data, OWNER_DEAD_FIELDS, "owner_dead")
                        if isinstance(data["version"], bool) or data["version"] != PROTOCOL_VERSION:
                            raise ValueError("unsupported_owner_dead_version")
                        _bounded_token(data["source"], "owner_dead_source", 64)
                        digest = _bounded_token(
                            data["identity_digest"],
                            "owner_dead_identity_digest",
                            64,
                        )
                        if not _is_hex_digest(digest):
                            raise ValueError("owner_dead_identity_digest_invalid")
                        if data["record_kind"] not in {
                            "regular",
                            "hardlink",
                            "symlink",
                            "directory",
                            "nonregular",
                        }:
                            raise ValueError("owner_dead_record_kind_invalid")
                        if data["reason"] != "invalid_record":
                            raise ValueError("owner_dead_reason_invalid")
                        old = _parse_utc(
                            _canonical_utc_text(data["failed_at"], "failed_at")
                        ) < cutoff
                    except (
                        OSError,
                        UnicodeError,
                        json.JSONDecodeError,
                        TypeError,
                        ValueError,
                        KeyError,
                    ):
                        old = True
                    if old:
                        if _remove_owned_path(path).state in {"absent", "removed", "progress"}:
                            removed += 1
                _fsync_dir(source_dir)
            if removed:
                for directory in (
                    self._events_dir,
                    self._decisions_dir,
                    self._requests_dir,
                    self._grants_dir,
                    self._dead_dir,
                ):
                    _fsync_dir(directory)
            if remaining is not None:
                _write_cleanup_progress(
                    self._cleanup_progress,
                    scope="owner",
                    next_class=next_turn,
                    cursor_keys=_OWNER_CLEANUP_CURSOR_KEYS,
                    cursors=self._scan_cursors,
                )
        return removed

    def _retire_route_event_for_grant(self, grant: OwnerReplyGrant) -> None:
        """Best-effort proof cleanup after terminal request/grant truth is durable."""
        route_event_id = grant.route_event_id
        if route_event_id is None:
            return
        try:
            event, invalid_existing = self._read_route_event(route_event_id)
            decision = "retired"
            if invalid_existing or event is None:
                decision = "quarantined"
            elif (
                event["grant_id"] != grant.grant_id
                or event["grant_ref"] != grant.grant_ref
                or event["proof_digest"] != grant.proof_digest
            ):
                decision = "quarantined"
            self._terminalize_route(
                route_event_id,
                decision=decision,
                now=_utc_now(),
                event=event,
            )
        except (OSError, ValueError, TypeError, KeyError):
            # Retirement can run after an external send. It must be impossible for
            # malformed canonical route state to unwind terminal accounting.
            return

    def _recover_route_decisions(self, *, now: str) -> None:
        now = _canonical_utc_text(now, "now")
        for path in sorted(_glob_paths(self._events_dir, "*.json")):
            if not _canonical_name_matches("owner_route_event", path.name):
                continue
            decision, invalid_decision = self._read_route_decision(path.stem)
            if decision is None and not invalid_decision:
                self._migrate_route_event_decision(path.stem, now=now)
        for path in sorted(_glob_paths(self._decisions_dir, "*.json")):
            if not _canonical_name_matches("owner_route_decision", path.name):
                continue
            decision, invalid_decision = self._read_route_decision(path.stem)
            if invalid_decision or decision is None or decision["decision"] != "active":
                continue
            event, invalid_event = self._read_route_event(path.stem)
            if invalid_event or event is None or event["decision"] != "active":
                self._terminalize_route(
                    path.stem,
                    decision="quarantined",
                    now=now,
                    event=event,
                )
                continue
            expected = _route_authority_digest(
                path.stem,
                str(event["grant_id"]),
                str(event["grant_ref"]),
                str(event["proof_digest"]),
            )
            if not hmac.compare_digest(str(decision["authority_digest"]), expected):
                self._terminalize_route(
                    path.stem,
                    decision="quarantined",
                    now=now,
                    event=event,
                )

    def _migrate_route_event_decision(self, route_event_id: str, *, now: str) -> None:
        """Bind a legacy canonical event to the independent decision ledger."""
        route_event_id = _safe_name(route_event_id)
        existing, invalid_decision = self._read_route_decision(route_event_id)
        if existing is not None or invalid_decision:
            return
        event, invalid_event = self._read_route_event(route_event_id)
        if invalid_event or event is None:
            self._write_route_decision(
                route_event_id,
                decision="quarantined",
                now=now,
                authority_digest=None,
            )
            self._best_effort_terminal_event(
                route_event_id,
                decision="quarantined",
                event=event,
                now=now,
            )
            return
        authority_digest = None
        if event["grant_id"] is not None:
            authority_digest = _route_authority_digest(
                route_event_id,
                str(event["grant_id"]),
                str(event["grant_ref"]),
                str(event["proof_digest"]),
            )
        decision = str(event["decision"])
        self._write_route_decision(
            route_event_id,
            decision=decision,
            now=now,
            authority_digest=authority_digest,
        )

    def _terminalize_route(
        self,
        route_event_id: str,
        *,
        decision: str,
        now: str,
        event: Mapping[str, Any] | None = None,
    ) -> None:
        if decision in {"active", "reserved"}:
            raise ValueError("terminal_route_decision_required")
        # The proof-free decision is always committed before touching the
        # potentially obstructed proof-bearing canonical event path.
        committed = self._write_route_decision(
            route_event_id,
            decision=decision,
            now=now,
            authority_digest=None,
        )
        self._best_effort_terminal_event(
            route_event_id,
            decision=str(committed["decision"]),
            event=event,
            now=now,
        )

    def _best_effort_terminal_event(
        self,
        route_event_id: str,
        *,
        decision: str,
        event: Mapping[str, Any] | None,
        now: str,
    ) -> None:
        try:
            if event is None:
                event = _empty_route_event_tombstone(
                    route_event_id=route_event_id,
                    created_at=now,
                    decision=decision,
                )
            else:
                event = _terminal_route_event_record(event, decision=decision)
            self._write_json(self._event_path(route_event_id), event)
        except (OSError, ValueError, TypeError, KeyError):
            # A nonempty/nested directory or other rejected inode may remain.
            # It is never read as authority because the ledger is checked first.
            pass

    def _read_route_decision(
        self,
        route_event_id: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        route_event_id = _safe_name(route_event_id)
        path = self._decision_path(route_event_id)
        existed = _path_lexists(path)
        try:
            data = self._read_raw(path)
            _require_exact_keys(data, ROUTE_DECISION_FIELDS, "route_decision")
            if isinstance(data["version"], bool) or data["version"] != PROTOCOL_VERSION:
                raise ValueError("unsupported_route_decision_version")
            embedded = _safe_name(str(data["route_event_id"]))
            if embedded != route_event_id or path.name != f"{route_event_id}.json":
                raise ValueError("route_decision_filename_identity_mismatch")
            digest = _bounded_token(data["route_event_digest"], "route_event_digest", 64)
            if not _is_hex_digest(digest) or not hmac.compare_digest(
                digest, _route_event_identity_digest(route_event_id)
            ):
                raise ValueError("route_decision_identity_digest_mismatch")
            decision = str(data["decision"])
            if decision not in {
                "reserved",
                "active",
                "expired",
                "revoked",
                "missing",
                "quarantined",
                "retired",
            }:
                raise ValueError("route_decision_invalid")
            authority_digest = data["authority_digest"]
            if authority_digest is not None:
                authority_digest = _bounded_token(
                    authority_digest, "authority_digest", 64
                )
                if not _is_hex_digest(authority_digest):
                    raise ValueError("route_authority_digest_invalid")
            if decision == "active" and authority_digest is None:
                raise ValueError("active_route_authority_digest_required")
            data["route_event_id"] = embedded
            data["route_event_digest"] = digest
            data["authority_digest"] = authority_digest
            data["decision"] = decision
            data["created_at"] = _canonical_utc_text(data["created_at"], "created_at")
            data["updated_at"] = _canonical_utc_text(data["updated_at"], "updated_at")
            return dict(data), False
        except FileNotFoundError:
            return None, existed
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            # Never remove an invalid decision name: its continued presence is a
            # conservative permanent no-remint barrier.
            return None, existed

    def _write_route_decision(
        self,
        route_event_id: str,
        *,
        decision: str,
        now: str,
        authority_digest: str | None,
    ) -> dict[str, Any]:
        route_event_id = _safe_name(route_event_id)
        now = _canonical_utc_text(now, "now")
        existing, invalid_existing = self._read_route_decision(route_event_id)
        if invalid_existing:
            raise ValueError("route_decision_obstructed")
        terminal = {"expired", "revoked", "missing", "quarantined", "retired"}
        if existing is not None and existing["decision"] in terminal:
            return existing
        if decision not in {"reserved", "active", *terminal}:
            raise ValueError("route_decision_invalid")
        if existing is not None and existing["decision"] == "active" and decision == "reserved":
            raise ValueError("route_decision_regression")
        if decision == "active":
            if authority_digest is None or not _is_hex_digest(authority_digest):
                raise ValueError("route_authority_digest_required")
        elif authority_digest is None and existing is not None:
            authority_digest = existing["authority_digest"]
        record = {
            "version": PROTOCOL_VERSION,
            "route_event_id": route_event_id,
            "route_event_digest": _route_event_identity_digest(route_event_id),
            "authority_digest": authority_digest,
            "decision": decision,
            "created_at": existing["created_at"] if existing is not None else now,
            "updated_at": now,
        }
        _require_exact_keys(record, ROUTE_DECISION_FIELDS, "route_decision")
        self._write_json(self._decision_path(route_event_id), record)
        return record

    def _transition_request(
        self,
        grant: OwnerReplyGrant,
        request: ChannelReplySubmitRequest,
        *,
        expected: ChannelReplyStatus,
        status: ChannelReplyStatus,
        claim_token: str,
        prepared_at: str | None = None,
    ) -> bool:
        with self._transaction():
            record = self._read_request(grant.grant_id, request.request_id)
            if (
                record is None
                or record.status is not expected
                or record.claim_token != claim_token
                or record.receipt is not None
            ):
                return False
            current_grant = self._read_grant_by_id(grant.grant_id)
            if (
                current_grant is None
                or current_grant.claimed_request_id != request.request_id
                or current_grant.consumed_request_id not in {None, request.request_id}
            ):
                return False
            self._write_json(
                self._request_path(grant.grant_id, request.request_id),
                replace(
                    record,
                    status=status,
                    prepared_at=prepared_at or record.prepared_at,
                ).to_record(),
            )
            return True

    @contextlib.contextmanager
    def _transaction(self, *, reconcile: bool = True):
        with self._lock:
            with self._mutation_lock.exclusive(
                self.root, expected_root=self._root_identity
            ) as session:
                with _bind_mutation_session(self.root, session):
                    if self._root_identity is None:
                        self._root_identity = session.root_identity
                    if reconcile:
                        self._reconcile_owned_temps()
                    yield session

    def _reconcile_owned_temps(self, *, max_inspections: int = 1) -> int:
        surfaces = (
            ("root", self.root, ("owner_maintenance", "owner_cleanup_progress")),
            ("grants", self._grants_dir, ("owner_grant",)),
            ("requests", self._requests_dir, ("owner_request",)),
            ("events", self._events_dir, ("owner_route_event",)),
            ("decisions", self._decisions_dir, ("owner_route_decision",)),
            ("dead-grants", self._dead_dir / "grants", ("owner_dead",)),
            ("dead-requests", self._dead_dir / "requests", ("owner_dead",)),
            ("dead-events", self._dead_dir / "route_events", ("owner_dead",)),
            ("dead-decisions", self._dead_dir / "route_decisions", ("owner_dead",)),
        )
        existing = tuple(item for item in surfaces if _path_lexists(item[1]))
        return _reconcile_owned_temps_page(
            self._maintenance_progress,
            scope="owner",
            surfaces=existing,
            max_inspections=max_inspections,
        )

    def _read_grant_by_id(self, grant_id: str) -> OwnerReplyGrant | None:
        grant_id = _safe_name(grant_id)
        path = self._grant_path(grant_id)
        try:
            grant = OwnerReplyGrant.from_record(self._read_raw(path))
            if grant.grant_id != grant_id or grant.grant_ref != f"channel-reply-v1:{grant_id}":
                raise ValueError("grant_filename_identity_mismatch")
            return grant
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            self._quarantine(path)
            return None

    def _read_route_event(
        self,
        route_event_id: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Return ``(record, invalid_existing)`` for canonical event identity."""
        route_event_id = _safe_name(route_event_id)
        path = self._event_path(route_event_id)
        existed = _path_lexists(path)
        try:
            data = self._read_raw(path)
            _require_exact_keys(data, ROUTE_EVENT_FIELDS, "route_event")
            if isinstance(data["version"], bool) or data["version"] != PROTOCOL_VERSION:
                raise ValueError("unsupported_route_event_version")
            embedded_event_id = _safe_name(str(data["route_event_id"]))
            if embedded_event_id != route_event_id or path.name != f"{route_event_id}.json":
                raise ValueError("route_event_filename_identity_mismatch")
            decision = str(data["decision"])
            if decision not in {
                "active",
                "expired",
                "revoked",
                "missing",
                "quarantined",
                "retired",
            }:
                raise ValueError("route_event_decision_invalid")
            data["created_at"] = _canonical_utc_text(data["created_at"], "created_at")
            data["expires_at"] = _canonical_utc_text(data["expires_at"], "expires_at")
            grant_id = data["grant_id"]
            grant_ref = data["grant_ref"]
            proof_digest = data["proof_digest"]
            proof = data["proof"]
            if decision == "quarantined" and grant_id is None:
                if grant_ref is not None or proof_digest is not None or proof != "":
                    raise ValueError("route_event_empty_tombstone_invalid")
            else:
                grant_id = _safe_name(str(grant_id))
                grant_ref = _bounded_token(grant_ref, "grant_ref", 256)
                if grant_ref != f"channel-reply-v1:{grant_id}":
                    raise ValueError("route_event_grant_ref_mismatch")
                proof_digest = _bounded_token(proof_digest, "proof_digest", 128)
                if not isinstance(proof, str) or len(proof) > 256:
                    raise ValueError("route_event_proof_invalid")
                if proof and not hmac.compare_digest(_digest(proof), proof_digest):
                    raise ValueError("route_event_proof_digest_mismatch")
                if decision == "active" and not proof:
                    raise ValueError("route_event_active_proof_required")
            data["grant_id"] = grant_id
            data["grant_ref"] = grant_ref
            data["proof_digest"] = proof_digest
            data["proof"] = proof
            data["decision"] = decision
            return dict(data), False
        except FileNotFoundError:
            return None, existed
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            self._quarantine(path)
            return None, existed

    def _read_request(self, grant_id: str, request_id: str) -> ReplyRequestRecord | None:
        grant_id = _safe_name(grant_id)
        request_id = _safe_name(request_id)
        return self._read_request_path(
            self._request_path(grant_id, request_id),
            expected_grant_id=grant_id,
            expected_request_id=request_id,
        )

    def _read_request_path(
        self,
        path: Path,
        *,
        expected_grant_id: str | None = None,
        expected_request_id: str | None = None,
    ) -> ReplyRequestRecord | None:
        try:
            record = ReplyRequestRecord.from_record(self._read_raw(path))
            if expected_grant_id is not None and record.grant_id != expected_grant_id:
                raise ValueError("request_grant_identity_mismatch")
            if expected_request_id is not None and record.request_id != expected_request_id:
                raise ValueError("request_id_identity_mismatch")
            if path.name != self._request_path(record.grant_id, record.request_id).name:
                raise ValueError("request_filename_identity_mismatch")
            return record
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            self._quarantine(path)
            return None

    def _read_raw(self, path: Path) -> Any:
        _assert_contained(self.root, path)
        return _strict_read_json(path, max_bytes=self._max_record_bytes)

    def _write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        _assert_contained(self.root, path)
        _atomic_private_json(path, payload)

    def _quarantine(self, path: Path) -> None:
        """Discard untrusted backing content and retain proof-free metadata only."""
        try:
            _assert_contained(self.root, path)
            record_kind = _record_kind(path)
            removal = _remove_file_or_empty_directory(path)
            if removal.state not in {"absent", "removed"}:
                return
            _fsync_dir(path.parent)
        except (FileNotFoundError, OSError, ValueError):
            # Never rename, chmod, read, or preserve an unsafe backing inode.
            return

        try:
            dest_dir = self._dead_dir / path.parent.name
            _ensure_private_dir(dest_dir)
            dead = {
                "version": PROTOCOL_VERSION,
                "source": path.parent.name,
                "identity_digest": hashlib.sha256(path.name.encode("utf-8")).hexdigest(),
                "record_kind": record_kind,
                "reason": "invalid_record",
                "failed_at": _utc_now(),
            }
            _require_exact_keys(dead, OWNER_DEAD_FIELDS, "owner_dead")
            dest = dest_dir / f"{path.name}.{uuid.uuid4().hex}.dead"
            _atomic_private_json(dest, dead)
            _fsync_dir(dest_dir)
        except (OSError, ValueError):
            # Quarantine evidence is best-effort; unsafe authority is already gone.
            pass

    def _grant_path(self, grant_id: str) -> Path:
        return self._grants_dir / f"{_safe_name(grant_id)}.json"

    def _request_path(self, grant_id: str, request_id: str) -> Path:
        return self._requests_dir / f"{_request_identity_digest(grant_id, request_id)}.json"

    def _event_path(self, route_event_id: str) -> Path:
        return self._events_dir / f"{_safe_name(route_event_id)}.json"

    def _decision_path(self, route_event_id: str) -> Path:
        return self._decisions_dir / f"{_safe_name(route_event_id)}.json"


@dataclass(frozen=True, slots=True)
class ChannelReplyTargetCapsule:
    """Owner-created target-local capsule that names the derived outbox only."""

    capsule_id: str
    target_agent_id: str
    target_agent_name: str
    outbox_dir: str
    receipt_dir: str
    created_at: str
    expires_at: str
    capability_marker: str = CAPABILITY_MARKER

    @classmethod
    def create(
        cls,
        *,
        target_workdir: str | Path,
        target_agent_id: str,
        target_agent_name: str,
        created_at: str,
        expires_at: str,
        mutation_lock: ChannelReplyMutationLockPort,
    ) -> "ChannelReplyTargetCapsule":
        root = _prepare_root(Path(target_workdir) / ".channel_reply")
        outbox = root / CHANNEL_REPLY_STATE_BY_KIND["target_outbox"].directory
        receipts = root / CHANNEL_REPLY_STATE_BY_KIND["target_receipt"].directory
        for directory in (
            outbox,
            receipts,
            root / CHANNEL_REPLY_STATE_BY_KIND["target_claim"].directory,
            root / CHANNEL_REPLY_STATE_BY_KIND["target_consumed"].directory,
            root / CHANNEL_REPLY_STATE_BY_KIND["target_dead"].directory,
        ):
            _ensure_private_dir(directory)
        lock = mutation_lock
        capsule = cls(
            capsule_id=secrets.token_urlsafe(18),
            target_agent_id=_bounded_token(target_agent_id, "target_agent_id", 160),
            target_agent_name=_bounded_target_name(target_agent_name),
            outbox_dir=outbox.name,
            receipt_dir=receipts.name,
            created_at=_canonical_utc_text(created_at, "created_at"),
            expires_at=_canonical_utc_text(expires_at, "expires_at"),
        )
        with lock.exclusive(root, expected_root=None) as session:
            with _bind_mutation_session(root, session):
                _reconcile_target_state_temps(
                    root,
                    progress_path=root / "target-maintenance-progress.json",
                    max_inspections=1,
                )
                _atomic_private_json(root / "active_capsule.json", capsule.to_record())
        return capsule

    def to_record(self) -> dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "capsule_id": self.capsule_id,
            "target_agent_id": self.target_agent_id,
            "target_agent_name": self.target_agent_name,
            "outbox_dir": self.outbox_dir,
            "receipt_dir": self.receipt_dir,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "capability_marker": self.capability_marker,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ChannelReplyTargetCapsule":
        _require_exact_keys(record, CAPSULE_FIELDS, "capsule")
        version = record.get("version")
        if isinstance(version, bool) or version != PROTOCOL_VERSION:
            raise ValueError("unsupported_capsule_version")
        marker = _bounded_token(record["capability_marker"], "capability_marker", 64)
        if marker != CAPABILITY_MARKER:
            raise ValueError("unsupported_capability_marker")
        outbox_dir = _safe_name(str(record["outbox_dir"]))
        receipt_dir = _safe_name(str(record["receipt_dir"]))
        if outbox_dir != "outbox" or receipt_dir != "receipts":
            raise ValueError("capsule_directory_mismatch")
        return cls(
            capsule_id=_safe_name(str(record["capsule_id"])),
            target_agent_id=_bounded_token(record["target_agent_id"], "target_agent_id", 160),
            target_agent_name=_bounded_target_name(record["target_agent_name"]),
            outbox_dir=outbox_dir,
            receipt_dir=receipt_dir,
            created_at=_canonical_utc_text(record["created_at"], "created_at"),
            expires_at=_canonical_utc_text(record["expires_at"], "expires_at"),
            capability_marker=marker,
        )


class ChannelReplyTargetFileSubmitPort(ChannelReplySubmitPort):
    """Inert target-local filesystem submitter with terminal receipt lookup."""

    def __init__(
        self,
        target_workdir: str | Path,
        *,
        mutation_lock: ChannelReplyMutationLockPort,
        now=None,
    ) -> None:
        # Construction validates but never chmods the caller-owned workdir and
        # never creates `.channel_reply`; absent authority stays wholly inert.
        self.target_workdir = _existing_directory_root(Path(target_workdir))
        self._state_root = self.target_workdir / ".channel_reply"
        self._now = now or _utc_now
        self._thread_lock = threading.RLock()
        self._mutation_lock = mutation_lock
        self._root_identity: ChannelReplyRootIdentity | None = None
        self._maintenance_progress = self._state_root / "target-maintenance-progress.json"

    @contextlib.contextmanager
    def _transaction(self):
        with self._thread_lock:
            with self._mutation_lock.exclusive(
                self._state_root, expected_root=self._root_identity
            ) as session:
                with _bind_mutation_session(self._state_root, session):
                    if self._root_identity is None:
                        self._root_identity = session.root_identity
                    _reconcile_target_state_temps(
                        self._state_root,
                        progress_path=self._maintenance_progress,
                        max_inspections=1,
                    )
                    yield session

    def submit_channel_reply(self, request: ChannelReplySubmitRequest) -> ChannelReplyReceipt:
        try:
            if not _path_lexists(self._state_root):
                raise ValueError("capsule_unavailable")
            with self._transaction():
                capsule = self._read_capsule()
            now = _canonical_utc_text(self._now(), "now")
        except (OSError, ValueError):
            return _fail_receipt(request, "channel_reply capsule unavailable")
        if not _not_expired(capsule.expires_at, now):
            return _fail_receipt(request, "channel_reply capsule expired")
        grant_id = _grant_id_from_ref(request.grant_ref)
        if grant_id is None:
            return _fail_receipt(request, "grant unavailable")
        identity = _request_identity_digest(grant_id, request.request_id)
        receipts = self._state_root / capsule.receipt_dir
        outbox = self._state_root / capsule.outbox_dir
        claims = self._state_root / CHANNEL_REPLY_STATE_BY_KIND["target_claim"].directory
        consumed = self._state_root / CHANNEL_REPLY_STATE_BY_KIND["target_consumed"].directory
        dead = self._state_root / CHANNEL_REPLY_STATE_BY_KIND["target_dead"].directory
        try:
            with self._transaction():
                for directory in (
                    self._state_root,
                    receipts,
                    outbox,
                    claims,
                    consumed,
                    dead,
                ):
                    _require_private_dir(directory)
                receipt_path = receipts / f"{identity}.json"
                if _path_lexists(receipt_path):
                    return _read_target_receipt(
                        receipt_path,
                        expected_grant_id=grant_id,
                        expected_request_id=request.request_id,
                    )
                consumed_path = consumed / f"{identity}.json"
                if _path_lexists(consumed_path):
                    _read_target_consumed(
                        consumed_path,
                        expected_grant_id=grant_id,
                        expected_request_id=request.request_id,
                    )
                    # Receipt is committed before the consumed marker. Recheck it to
                    # close a target/owner race; a missing receipt after a valid
                    # consumed marker is fail-closed terminal state, never a requeue.
                    if _path_lexists(receipt_path):
                        return _read_target_receipt(
                            receipt_path,
                            expected_grant_id=grant_id,
                            expected_request_id=request.request_id,
                        )
                    return _fail_receipt(request, "reply request is terminal; receipt unavailable")
                marker_digest = hashlib.sha256(
                    b"target-dead-v1\0" + identity.encode("ascii")
                ).hexdigest()[:32]
                dead_path = dead / f"{identity}.{marker_digest}.json"
                if _path_lexists(dead_path):
                    _read_target_dead(dead_path, expected_identity=identity)
                    return _fail_receipt(request, "reply request was rejected by owner transport")
                claim_path = claims / f"{identity}.json"
                if _path_lexists(claim_path):
                    return _pending_receipt(request)
                payload = {
                    "version": PROTOCOL_VERSION,
                    "request": request_to_record(request),
                    "submitted_at": now,
                }
                _require_exact_keys(payload, OUTBOX_FIELDS, "outbox")
                path = outbox / f"{identity}.json"
                try:
                    _atomic_create_private_json(path, payload)
                except FileExistsError:
                    existing = _read_outbox_request(path, expected_identity=identity)
                    if existing.grant_ref != request.grant_ref or existing.request_id != request.request_id:
                        raise ValueError("outbox_identity_mismatch")
                return ChannelReplyReceipt(
                    status=ChannelReplyStatus.PENDING.value,
                    grant_ref=request.grant_ref,
                    request_id=request.request_id,
                    message="reply request queued for owner delivery",
                )
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, ValueError):
            return _fail_receipt(request, "channel_reply transport unavailable")

    def _read_capsule(self) -> ChannelReplyTargetCapsule:
        path = self._state_root / "active_capsule.json"
        try:
            _assert_contained(self.target_workdir, path)
            data = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
            return ChannelReplyTargetCapsule.from_record(data)
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            raise ValueError("capsule_unavailable") from exc


class ChannelReplyOwnerFileTransport:
    """Owner-side claimer/drainer for one target's local reply outbox.

    Queue ownership is acquired by an atomic rename before parsing. A raw or
    `claimed` file is safe to roll back before dispatch; `dispatching` is a
    durable possible-send boundary and recovers only to terminal ambiguity.
    """

    def __init__(
        self,
        target_workdir: str | Path,
        *,
        submit_port: ChannelReplySubmitPort,
        mutation_lock: ChannelReplyMutationLockPort,
        now=None,
        recover_on_init: bool = True,
    ) -> None:
        self.target_workdir = _existing_directory_root(Path(target_workdir))
        self.root = self.target_workdir / ".channel_reply"
        self._outbox = self.root / CHANNEL_REPLY_STATE_BY_KIND["target_outbox"].directory
        self._receipts = self.root / CHANNEL_REPLY_STATE_BY_KIND["target_receipt"].directory
        self._claims = self.root / CHANNEL_REPLY_STATE_BY_KIND["target_claim"].directory
        self._consumed = self.root / CHANNEL_REPLY_STATE_BY_KIND["target_consumed"].directory
        self._dead = self.root / CHANNEL_REPLY_STATE_BY_KIND["target_dead"].directory
        for directory in (
            self.root,
            self._outbox,
            self._receipts,
            self._claims,
            self._consumed,
            self._dead,
        ):
            _require_private_dir(directory)
        self._submit_port = submit_port
        self._now = now or _utc_now
        self._thread_lock = threading.RLock()
        self._cleanup_cursors: dict[str, str] = {}
        self._scan_cursors: dict[str, DirectoryScanCursor | None] = {}
        self._cleanup_surface_turn = 0
        self.last_cleanup_inspections = 0
        self._mutation_lock = mutation_lock
        self._root_identity: ChannelReplyRootIdentity | None = None
        self._maintenance_progress = self.root / "target-maintenance-progress.json"
        self._cleanup_progress = self.root / "target-cleanup-progress.json"
        with self._transaction():
            capsule = ChannelReplyTargetCapsule.from_record(
                _strict_read_json(self.root / "active_capsule.json", max_bytes=MAX_RECORD_BYTES)
            )
            expected_target = getattr(submit_port, "target_agent_id", None)
            if expected_target is not None and capsule.target_agent_id != expected_target:
                raise ValueError("capsule_target_identity_mismatch")
            expected_name = getattr(submit_port, "target_agent_name", None)
            if expected_name is not None and capsule.target_agent_name != expected_name:
                raise ValueError("capsule_target_name_mismatch")
        if recover_on_init:
            self.recover_claims()

    @contextlib.contextmanager
    def _transaction(self, *, reconcile: bool = True):
        with self._thread_lock:
            with self._mutation_lock.exclusive(
                self.root, expected_root=self._root_identity
            ) as session:
                with _bind_mutation_session(self.root, session):
                    if self._root_identity is None:
                        self._root_identity = session.root_identity
                    if reconcile:
                        _reconcile_target_state_temps(
                            self.root,
                            progress_path=self._maintenance_progress,
                            max_inspections=1,
                        )
                    yield session

    def _recover_claim_path(self, path: Path) -> None:
        identity = path.stem
        try:
            data = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
            if isinstance(data, Mapping) and set(data) == OUTBOX_FIELDS:
                request, _submitted_at = _parse_outbox_record(data, expected_identity=identity)
                self._rollback_claim(path, request)
                return
            request, state = _parse_target_claim_record(data, expected_identity=identity)
            if state == "claimed":
                self._rollback_claim(path, request)
                return
            self._commit_target_terminal(path, request, _ambiguous_receipt(request))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            self._dead_claim(path, identity, "invalid_claim")

    def recover_claims(self, *, max_records: int | None = None) -> int:
        """Rollback/terminalize claims through bounded descriptor-relative pages."""
        limit = 512 if max_records is None else max(0, int(max_records))
        with self._transaction():
            paths, inspected = _bounded_matching_paths(
                self._claims,
                pattern="*.json",
                cursor_key="claim-recovery-api",
                cursors=self._scan_cursors,
                max_inspections=limit,
            )
            for path in paths:
                self._recover_claim_path(path)
        return inspected

    def drain(self, *, max_items: int = 100) -> list[ChannelReplyReceipt]:
        receipts: list[ChannelReplyReceipt] = []
        for _ in range(max(0, max_items)):
            receipt = self.drain_once()
            if receipt is None:
                break
            receipts.append(receipt)
        return receipts

    def drain_once(self) -> ChannelReplyReceipt | None:
        with self._transaction():
            candidates, _inspected = _bounded_matching_paths(
                self._outbox,
                pattern="*.json",
                cursor_key="drain-outbox",
                cursors=self._scan_cursors,
                max_inspections=1,
            )
            if not candidates:
                # One page can contain only the persistent lock/progress entries.
                # Continue bounded pages within this one-record drain attempt until
                # a queue candidate appears or a complete sweep proves no work.
                for _ in range(8):
                    candidates, _inspected = _bounded_matching_paths(
                        self._outbox,
                        pattern="*.json",
                        cursor_key="drain-outbox",
                        cursors=self._scan_cursors,
                        max_inspections=1,
                    )
                    if candidates or self._scan_cursors.get("drain-outbox") is None:
                        break
            if not candidates:
                return None
            source = candidates[0]
            identity = source.stem
            if not _is_hex_digest(identity):
                self._dead_claim(source, identity, "invalid_outbox_filename")
                return None
            claim = self._claims / source.name
            if _path_lexists(claim):
                # Recovery owns the stale claim before a new queue record can be
                # considered. Never overwrite an unaccounted claim.
                return None
            if not _is_private_regular_file_path(source):
                self._dead_claim(source, identity, "invalid_outbox_shape")
                return None
            _move_path(source, claim, replace_destination=False)
            _fsync_dir(self._outbox)
            _fsync_dir(self._claims)
            if not _is_private_regular_file_path(claim):
                self._dead_claim(claim, identity, "invalid_outbox_shape")
                return None
            try:
                data = _strict_read_json(claim, max_bytes=MAX_RECORD_BYTES)
                request, submitted_at = _parse_outbox_record(
                    data,
                    expected_identity=identity,
                )
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                self._dead_claim(claim, identity, "invalid_outbox")
                return None
            receipt_path = self._receipts / f"{identity}.json"
            if _path_lexists(receipt_path):
                try:
                    receipt = _read_target_receipt(
                        receipt_path,
                        expected_grant_id=_grant_id_from_ref(request.grant_ref) or "",
                        expected_request_id=request.request_id,
                    )
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                    KeyError,
                ):
                    # A tuple-named but malformed/mismatched receipt can never
                    # authorize dispatch. Preserve only a sanitized dead marker
                    # and commit a bounded terminal receipt for target lookup.
                    self._dead_claim(receipt_path, identity, "invalid_receipt")
                    receipt = _fail_receipt(request, "target receipt rejected")
                self._commit_target_terminal(claim, request, receipt)
                return receipt
            claimed_at = _canonical_utc_text(self._now(), "claimed_at")
            dispatch = {
                "version": PROTOCOL_VERSION,
                "state": "dispatching",
                "request": request_to_record(request),
                "submitted_at": submitted_at,
                "claimed_at": claimed_at,
                "dispatching_at": claimed_at,
            }
            _require_exact_keys(dispatch, TARGET_CLAIM_FIELDS, "target_claim")
            _atomic_private_json(claim, dispatch)

        # Never hold the target queue lock across an external adapter call. The
        # canonical claim file prevents a second drainer from dispatching it.
        receipt = self._submit_port.submit_channel_reply(request)
        if receipt.status not in {status.value for status in TERMINAL_STATUSES}:
            # A concurrent owner caller may be sending this tuple. This drainer
            # crossed its durable dispatch boundary, so only ambiguity is safe.
            receipt = _ambiguous_receipt(request)
        with self._transaction():
            receipt = self._commit_target_terminal(claim, request, receipt)
        return receipt

    def cleanup_retained(
        self,
        *,
        now: str,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        max_records: int | None = None,
    ) -> int:
        """Fairly rotate recovery, retention surfaces, capsule, and temp inventory."""
        remaining = None if max_records is None else max(0, int(max_records))
        cutoff = _parse_utc(now) - timedelta(seconds=max(0, retention_seconds))
        removed = 0
        self.last_cleanup_inspections = 0
        classes = (
            "claim-recovery",
            "outbox",
            "claims",
            "receipts",
            "consumed",
            "dead",
            "capsule",
            "temp-reconcile",
        )
        if remaining is None:
            order = classes
            quotas = {name: None for name in classes}
        else:
            # The finite order and cursors are restored only after acquiring the
            # target root lock below; process-local defaults are not fair enough.
            order = classes
            quotas = {name: 0 for name in classes}

        directories = {
            "outbox": (self._outbox, "target_outbox"),
            "claims": (self._claims, "target_claim"),
            "receipts": (self._receipts, "target_receipt"),
            "consumed": (self._consumed, "target_consumed"),
            "dead": (self._dead, "target_dead"),
        }
        with self._transaction(reconcile=False):
            if remaining is not None:
                start, restored = _read_cleanup_progress(
                    self._cleanup_progress,
                    scope="target",
                    class_count=len(classes),
                    cursor_keys=_TARGET_CLEANUP_CURSOR_KEYS,
                )
                self._scan_cursors.update(restored)
                order = classes[start:] + classes[:start]
                for index in range(remaining):
                    quotas[order[index % len(order)]] += 1
                # Persist one-class rotation even for a zero budget, preventing a
                # restarted transport from favoring claim recovery forever.
                next_turn = (start + 1) % len(classes)
            for class_name in order:
                quota = quotas[class_name]
                if quota == 0:
                    continue
                if class_name == "temp-reconcile":
                    charged = _reconcile_target_state_temps(
                        self.root,
                        progress_path=self._maintenance_progress,
                        max_inspections=1 if quota is None else quota,
                    )
                    self.last_cleanup_inspections += charged
                    continue
                if class_name == "capsule":
                    self.last_cleanup_inspections += 1
                    capsule_path = self.root / "active_capsule.json"
                    if _path_lexists(capsule_path):
                        try:
                            capsule = ChannelReplyTargetCapsule.from_record(
                                _strict_read_json(capsule_path, max_bytes=MAX_RECORD_BYTES)
                            )
                            old = _parse_utc(capsule.expires_at) < cutoff
                        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                            old = True
                        if old and _remove_owned_path(capsule_path).state in {
                            "absent", "removed", "progress"
                        }:
                            removed += 1
                            _fsync_dir(self.root)
                    continue

                directory, state_kind = (
                    (self._claims, "target_claim")
                    if class_name == "claim-recovery"
                    else directories[class_name]
                )
                limit = 512 if quota is None else quota
                paths, charged = _bounded_matching_paths(
                    directory,
                    pattern="*.json",
                    cursor_key=f"target-cleanup:{class_name}",
                    cursors=self._scan_cursors,
                    max_inspections=limit,
                )
                self.last_cleanup_inspections += charged
                for path in paths:
                    if class_name == "claim-recovery":
                        self._recover_claim_path(path)
                        continue
                    if not _canonical_name_matches(state_kind, path.name):
                        continue
                    if state_kind == "target_dead" and self._dead_marker_pinned_by_source(path):
                        continue
                    try:
                        ts = _strict_target_cleanup_timestamp(path, state_kind)
                        old = _parse_utc(ts) < cutoff
                    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                        old = True
                    if old and _remove_owned_path(path).state in {"absent", "removed", "progress"}:
                        removed += 1
                _fsync_dir(directory)
            if remaining is not None:
                _write_cleanup_progress(
                    self._cleanup_progress,
                    scope="target",
                    next_class=next_turn,
                    cursor_keys=_TARGET_CLEANUP_CURSOR_KEYS,
                    cursors=self._scan_cursors,
                )
        return removed

    def _rollback_claim(self, path: Path, request: ChannelReplySubmitRequest) -> None:
        grant_id = _grant_id_from_ref(request.grant_ref)
        if grant_id is None:
            raise ValueError("grant_ref_invalid")
        expected = _request_identity_digest(grant_id, request.request_id)
        if path.stem != expected:
            raise ValueError("claim_filename_identity_mismatch")
        destination = self._outbox / path.name
        # The older claimed payload wins over a target duplicate queued during a
        # crash window. Replacing is safe because neither crossed dispatch.
        _move_path(path, destination, replace_destination=True)
        _fsync_dir(self._claims)
        _fsync_dir(self._outbox)

    def _commit_target_terminal(
        self,
        claim_path: Path,
        request: ChannelReplySubmitRequest,
        receipt: ChannelReplyReceipt,
    ) -> ChannelReplyReceipt:
        """Compare-and-preserve one tuple's first valid terminal decision."""
        grant_id = _grant_id_from_ref(request.grant_ref)
        if grant_id is None:
            raise ValueError("grant_ref_invalid")
        identity = _request_identity_digest(grant_id, request.request_id)
        if claim_path.stem != identity:
            raise ValueError("claim_filename_identity_mismatch")
        if receipt.grant_ref != request.grant_ref or receipt.request_id != request.request_id:
            raise ValueError("target_receipt_identity_mismatch")
        if receipt.status not in {status.value for status in TERMINAL_STATUSES}:
            raise ValueError("target_receipt_not_terminal")

        receipt_path = self._receipts / f"{identity}.json"
        consumed_path = self._consumed / f"{identity}.json"
        committed: ChannelReplyReceipt | None = None
        if _path_lexists(receipt_path):
            # A target-visible valid receipt is the oldest protocol decision
            # because receipt publication precedes consumed bookkeeping.
            committed = _read_target_receipt(
                receipt_path,
                expected_grant_id=grant_id,
                expected_request_id=request.request_id,
            )

        consumed_status: ChannelReplyStatus | None = None
        if _path_lexists(consumed_path):
            consumed_status = _read_target_consumed(
                consumed_path,
                expected_grant_id=grant_id,
                expected_request_id=request.request_id,
            )
        if committed is None and consumed_status is not None:
            # A proof-free consumed marker is still a durable decision. Repair
            # its missing receipt without changing the recorded status.
            committed = ChannelReplyReceipt(
                status=consumed_status.value,
                grant_ref=request.grant_ref,
                request_id=request.request_id,
                message="reply request terminal outcome was already committed",
            )
        if committed is None:
            committed = receipt

        now = _canonical_utc_text(self._now(), "committed_at")
        if not _path_lexists(receipt_path):
            receipt_payload = {
                "version": PROTOCOL_VERSION,
                "receipt": _receipt_record(committed),
                "committed_at": now,
            }
            _atomic_private_json(receipt_path, receipt_payload)
        if consumed_status is None or consumed_status.value != committed.status:
            consumed = {
                "version": PROTOCOL_VERSION,
                "grant_id": grant_id,
                "request_id": request.request_id,
                "identity_digest": identity,
                "status": committed.status,
                "finalized_at": now,
            }
            _require_exact_keys(consumed, TARGET_CONSUMED_FIELDS, "target_consumed")
            _atomic_private_json(consumed_path, consumed)
        claim_removal = _remove_owned_path(claim_path)
        if claim_removal.state not in {"absent", "removed"}:
            raise OSError("channel_reply terminal claim cleanup incomplete")
        # A target duplicate can race the owner claim; terminal receipt wins and
        # the duplicate body/proof is no longer needed.
        duplicate_removal = _remove_owned_path(self._outbox / f"{identity}.json")
        if duplicate_removal.state not in {"absent", "removed"}:
            raise OSError("channel_reply terminal duplicate cleanup incomplete")
        for directory in (self._claims, self._outbox, self._receipts, self._consumed):
            _fsync_dir(directory)
        return committed

    def _dead_claim(self, path: Path, identity: str, reason: str) -> None:
        safe_identity = identity if _is_hex_digest(identity) else hashlib.sha256(
            identity.encode("utf-8", errors="replace")
        ).hexdigest()
        marker_digest = hashlib.sha256(
            b"target-dead-v1\0" + safe_identity.encode("ascii")
        ).hexdigest()[:32]
        marker = self._dead / f"{safe_identity}.{marker_digest}.json"
        if _path_lexists(marker):
            try:
                _read_target_dead(marker, expected_identity=safe_identity)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                _remove_owned_path(marker)
                _fsync_dir(self._dead)
                return
        else:
            dead = {
                "version": PROTOCOL_VERSION,
                "identity_digest": safe_identity,
                "reason": _bounded_message(reason),
                "failed_at": _canonical_utc_text(self._now(), "failed_at"),
            }
            _require_exact_keys(dead, TARGET_DEAD_FIELDS, "target_dead")
            _atomic_private_json(marker, dead)
            _fsync_dir(self._dead)
        _remove_file_or_empty_directory(path)
        _fsync_dir(path.parent)

    def _dead_marker_pinned_by_source(self, path: Path) -> bool:
        try:
            identity = path.name.split(".", 1)[0]
            if not _is_hex_digest(identity):
                return False
            expected = hashlib.sha256(
                b"target-dead-v1\0" + identity.encode("ascii")
            ).hexdigest()[:32]
            if path.name != f"{identity}.{expected}.json":
                return False
            return _path_lexists(self._outbox / f"{identity}.json") or _path_lexists(
                self._claims / f"{identity}.json"
            )
        except (OSError, ValueError):
            return True


class OwnerChannelReplyController(ChannelReplySubmitPort):
    """Validate grants and execute a single owner-bound send callback."""

    def __init__(
        self,
        *,
        store: ChannelReplyFileStore,
        target_agent_id: str,
        target_agent_name: str | None = None,
        send_plain_text_reply,
        validate_pre_send=None,
        now=None,
        request_max_age_seconds: int = DEFAULT_REQUEST_MAX_AGE_SECONDS,
        future_skew_seconds: int = DEFAULT_REQUEST_FUTURE_SKEW_SECONDS,
    ) -> None:
        self.store = store
        self.target_agent_id = _bounded_token(target_agent_id, "target_agent_id", 160)
        self.target_agent_name = target_agent_name
        self._send_plain_text_reply = send_plain_text_reply
        self._validate_pre_send = validate_pre_send
        self._now = now or _utc_now
        self._request_max_age_seconds = request_max_age_seconds
        self._future_skew_seconds = future_skew_seconds

    def submit_channel_reply(self, request: ChannelReplySubmitRequest) -> ChannelReplyReceipt:
        now = self._now()
        grant, terminal, claim_token = self.store.claim_request(
            grant_ref=request.grant_ref,
            request=request,
            target_agent_id=self.target_agent_id,
            now=now,
            request_max_age_seconds=self._request_max_age_seconds,
            future_skew_seconds=self._future_skew_seconds,
        )
        if terminal is not None:
            return terminal
        if grant is None or claim_token is None:
            return _fail_receipt(request, "grant unavailable")
        if self.target_agent_name is not None and grant.target_agent_name != self.target_agent_name:
            receipt = _fail_receipt(request, "grant target identity mismatch")
            return self.store.finish_request(
                grant,
                request,
                status=ChannelReplyStatus.DEAD,
                receipt=receipt,
                consume=False,
                claim_token=claim_token,
            )
        if not self.store.mark_prepared(grant, request, self._now(), claim_token):
            return self.store.current_receipt(request)
        # Eligibility/authority validation belongs immediately before the
        # persisted sending barrier. A definite rejection remains a terminal
        # pre-send failure and no external callback is invoked.
        if self._validate_pre_send is not None:
            try:
                self._validate_pre_send(grant, request.text)
            except PreSendChannelReplyError:
                receipt = ChannelReplyReceipt(
                    status=ChannelReplyStatus.FAILED.value,
                    grant_ref=request.grant_ref,
                    request_id=request.request_id,
                    message="reply could not be prepared",
                )
                return self.store.finish_request(
                    grant,
                    request,
                    status=ChannelReplyStatus.FAILED,
                    receipt=receipt,
                    consume=False,
                    claim_token=claim_token,
                )
        if not self.store.mark_sending(grant, request, claim_token):
            return self.store.current_receipt(request)
        try:
            _owner_private_ref = self._send_plain_text_reply(grant, request.text)
        except PreSendChannelReplyError:
            receipt = ChannelReplyReceipt(
                status=ChannelReplyStatus.FAILED.value,
                grant_ref=request.grant_ref,
                request_id=request.request_id,
                message="reply could not be prepared",
            )
            return self.store.finish_request(
                grant,
                request,
                status=ChannelReplyStatus.FAILED,
                receipt=receipt,
                consume=False,
                claim_token=claim_token,
            )
        except Exception:
            receipt = _ambiguous_receipt(request)
            return self.store.finish_request(
                grant,
                request,
                status=ChannelReplyStatus.AMBIGUOUS,
                receipt=receipt,
                consume=True,
                claim_token=claim_token,
            )
        receipt = ChannelReplyReceipt(
            status=ChannelReplyStatus.SENT.value,
            grant_ref=request.grant_ref,
            request_id=request.request_id,
            message="reply sent",
            public_ref=f"channel-reply:{secrets.token_urlsafe(12)}",
        )
        return self.store.finish_request(
            grant,
            request,
            status=ChannelReplyStatus.SENT,
            receipt=receipt,
            consume=True,
            claim_token=claim_token,
        )


def make_fail_closed_receipt(raw: Mapping[str, Any] | None, reason: str) -> dict[str, Any]:
    grant_ref = ""
    request_id = ""
    if isinstance(raw, Mapping):
        grant_ref = str(raw.get("grant_ref") or "")[:256]
        request_id = str(raw.get("request_id") or "")[:160]
    return ChannelReplyReceipt(
        status=ChannelReplyStatus.DEAD.value,
        grant_ref=grant_ref,
        request_id=request_id,
        message=reason,
    ).to_public_dict()


def request_to_record(request: ChannelReplySubmitRequest) -> dict[str, Any]:
    return {
        "version": request.version,
        "grant_ref": request.grant_ref,
        "request_id": request.request_id,
        "created_at": request.created_at,
        "text": request.text,
        "proof": request.proof,
    }


def _route_event_record(
    *,
    route_event_id: str,
    grant: OwnerReplyGrant,
    proof: str | None,
) -> dict[str, Any]:
    proof_text = proof or ""
    if not proof_text or not hmac.compare_digest(_digest(proof_text), grant.proof_digest):
        raise ValueError("route_event_proof_mismatch")
    return {
        "version": PROTOCOL_VERSION,
        "route_event_id": route_event_id,
        "grant_id": grant.grant_id,
        "grant_ref": grant.grant_ref,
        "created_at": grant.created_at,
        "expires_at": grant.expires_at,
        "decision": "active",
        "proof": proof_text,
        "proof_digest": grant.proof_digest,
    }


def _terminal_route_event_record(
    event: Mapping[str, Any],
    *,
    decision: str,
) -> dict[str, Any]:
    if decision == "active":
        raise ValueError("terminal_route_event_decision_required")
    record = dict(event)
    record["decision"] = decision
    record["proof"] = ""
    return record


def _empty_route_event_tombstone(
    *,
    route_event_id: str,
    created_at: str,
    decision: str,
) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "route_event_id": route_event_id,
        "grant_id": None,
        "grant_ref": None,
        "created_at": created_at,
        "expires_at": created_at,
        "decision": decision,
        "proof": "",
        "proof_digest": None,
    }


def channel_reply_capability_marker() -> dict[str, Any]:
    return {
        "marker": CAPABILITY_MARKER,
        "version": PROTOCOL_VERSION,
        "submit": "target-local-filesystem-capsule",
    }


def _claim_rejection(
    grant: OwnerReplyGrant,
    request: ChannelReplySubmitRequest,
    *,
    target_agent_id: str,
    now: str,
    request_max_age_seconds: int,
    future_skew_seconds: int,
) -> str | None:
    if request.grant_ref != grant.grant_ref:
        return "grant reference mismatch"
    if grant.target_agent_id != target_agent_id:
        return "grant target mismatch"
    if grant.target_protocol_version != PROTOCOL_VERSION:
        return "grant protocol mismatch"
    if grant.revoked:
        return "grant revoked"
    if grant.consumed_request_id and grant.consumed_request_id != request.request_id:
        return "grant already consumed"
    if not _not_expired(grant.expires_at, now):
        return "grant expired"
    if not hmac.compare_digest(grant.proof_digest, _digest(request.proof)):
        return "grant proof rejected"
    try:
        current = _parse_utc(now)
        request_time = _parse_utc(request.created_at)
        grant_created = _parse_utc(grant.created_at)
    except ValueError:
        return "timestamp invalid"
    skew = timedelta(seconds=max(0, future_skew_seconds))
    max_age = timedelta(seconds=max(0, request_max_age_seconds))
    if current + skew < grant_created:
        return "clock rollback detected"
    if request_time > current + skew:
        return "request timestamp in future"
    if current - request_time > max_age:
        return "request timestamp too old"
    if request_time + skew < grant_created:
        return "request predates grant"
    return None


def _validated_public_ref(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"channel-reply:[A-Za-z0-9_-]{16}", value) is None:
        raise ValueError("public_ref_invalid")
    return value


def _receipt_record(receipt: ChannelReplyReceipt | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    public_ref = None if receipt.public_ref is None else _validated_public_ref(receipt.public_ref)
    return {
        "version": PROTOCOL_VERSION,
        "status": receipt.status,
        "grant_ref": receipt.grant_ref,
        "request_id": receipt.request_id,
        "message": _bounded_message(receipt.message),
        "public_ref": public_ref,
    }


def _receipt_from_record(record: Mapping[str, Any]) -> ChannelReplyReceipt:
    _require_exact_keys(record, RECEIPT_FIELDS, "receipt")
    version = record.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ValueError("unsupported_receipt_version")
    public_ref = record["public_ref"]
    if public_ref is not None:
        public_ref = _validated_public_ref(public_ref)
    status = ChannelReplyStatus(str(record["status"]))
    return ChannelReplyReceipt(
        status=status.value,
        grant_ref=_bounded_token(record["grant_ref"], "grant_ref", 256),
        request_id=_safe_name(str(record["request_id"])),
        message=_bounded_message(str(record["message"])),
        public_ref=public_ref,
    )


def _pending_receipt(request: ChannelReplySubmitRequest) -> ChannelReplyReceipt:
    return ChannelReplyReceipt(
        status=ChannelReplyStatus.PENDING.value,
        grant_ref=request.grant_ref,
        request_id=request.request_id,
        message="reply request is already pending owner delivery",
    )


def _fail_receipt(request: ChannelReplySubmitRequest, message: str) -> ChannelReplyReceipt:
    return ChannelReplyReceipt(
        status=ChannelReplyStatus.DEAD.value,
        grant_ref=request.grant_ref,
        request_id=request.request_id,
        message=message,
    )


def _ambiguous_receipt(request: ChannelReplySubmitRequest) -> ChannelReplyReceipt:
    return ChannelReplyReceipt(
        status=ChannelReplyStatus.AMBIGUOUS.value,
        grant_ref=request.grant_ref,
        request_id=request.request_id,
        message="reply outcome is ambiguous after a possible send; it was not resent",
    )


def _ambiguous_receipt_ref(grant_id: str, request_id: str) -> ChannelReplyReceipt:
    return ChannelReplyReceipt(
        status=ChannelReplyStatus.AMBIGUOUS.value,
        grant_ref=f"channel-reply-v1:{grant_id}",
        request_id=request_id,
        message="reply outcome is ambiguous after restart; it was not resent",
    )


def _digest(proof: str) -> str:
    return hashlib.sha256(proof.encode("utf-8")).hexdigest()


def _framed_digest(*values: str) -> str:
    framed = bytearray()
    for value in values:
        raw = value.encode("utf-8")
        framed.extend(len(raw).to_bytes(4, "big"))
        framed.extend(raw)
    return hashlib.sha256(bytes(framed)).hexdigest()


def _route_event_identity_digest(route_event_id: str) -> str:
    return _framed_digest("channel-reply-route-event-v1", _safe_name(route_event_id))


def _route_authority_digest(
    route_event_id: str,
    grant_id: str,
    grant_ref: str,
    proof_digest: str,
) -> str:
    return _framed_digest(
        "channel-reply-route-authority-v1",
        _safe_name(route_event_id),
        _safe_name(grant_id),
        _bounded_token(grant_ref, "grant_ref", 256),
        _bounded_token(proof_digest, "proof_digest", 128),
    )


def _safe_name(value: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not isinstance(value, str) or not value or any(ch not in allowed for ch in value):
        raise ValueError("unsafe_identifier")
    return value


def _optional_safe(value: Any, name: str, max_chars: int) -> str | None:
    if value is None:
        return None
    token = _bounded_token(value, name, max_chars)
    return _safe_name(token)


def _bounded_target_name(value: Any) -> str:
    token = _bounded_token(value, "target_agent_name", 128)
    if "/" in token or "\\" in token or token in {".", ".."} or token.startswith("."):
        raise ValueError("target_agent_name_invalid")
    return token


def _grant_id_from_ref(grant_ref: str) -> str | None:
    prefix = "channel-reply-v1:"
    if not isinstance(grant_ref, str) or not grant_ref.startswith(prefix):
        return None
    grant_id = grant_ref[len(prefix):]
    try:
        return _safe_name(grant_id)
    except ValueError:
        return None


def _request_identity_digest(grant_id: str, request_id: str) -> str:
    """Collision-safe canonical digest for the full idempotency tuple."""
    grant = _safe_name(grant_id).encode("utf-8")
    request = _safe_name(request_id).encode("utf-8")
    framed = (
        len(grant).to_bytes(4, "big")
        + grant
        + len(request).to_bytes(4, "big")
        + request
    )
    return hashlib.sha256(framed).hexdigest()


def _is_hex_digest(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _parse_outbox_record(
    data: Mapping[str, Any],
    *,
    expected_identity: str,
) -> tuple[ChannelReplySubmitRequest, str]:
    _require_exact_keys(data, OUTBOX_FIELDS, "outbox")
    version = data.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ValueError("unsupported_outbox_version")
    raw_request = data["request"]
    if not isinstance(raw_request, Mapping):
        raise ValueError("outbox_request_invalid")
    request = ChannelReplySubmitRequest.from_mapping(raw_request)
    grant_id = _grant_id_from_ref(request.grant_ref)
    if grant_id is None:
        raise ValueError("outbox_grant_ref_invalid")
    if _request_identity_digest(grant_id, request.request_id) != expected_identity:
        raise ValueError("outbox_filename_identity_mismatch")
    submitted_at = _canonical_utc_text(data["submitted_at"], "submitted_at")
    return request, submitted_at


def _read_outbox_request(path: Path, *, expected_identity: str) -> ChannelReplySubmitRequest:
    data = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
    request, _submitted_at = _parse_outbox_record(
        data,
        expected_identity=expected_identity,
    )
    return request


def _parse_target_claim_record(
    data: Mapping[str, Any],
    *,
    expected_identity: str,
) -> tuple[ChannelReplySubmitRequest, str]:
    _require_exact_keys(data, TARGET_CLAIM_FIELDS, "target_claim")
    version = data.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ValueError("unsupported_target_claim_version")
    state = str(data["state"])
    if state not in {"claimed", "dispatching"}:
        raise ValueError("target_claim_state_invalid")
    raw_request = data["request"]
    if not isinstance(raw_request, Mapping):
        raise ValueError("target_claim_request_invalid")
    request = ChannelReplySubmitRequest.from_mapping(raw_request)
    grant_id = _grant_id_from_ref(request.grant_ref)
    if grant_id is None or _request_identity_digest(grant_id, request.request_id) != expected_identity:
        raise ValueError("target_claim_filename_identity_mismatch")
    _canonical_utc_text(data["submitted_at"], "submitted_at")
    _canonical_utc_text(data["claimed_at"], "claimed_at")
    dispatching_at = data["dispatching_at"]
    if state == "dispatching":
        _canonical_utc_text(dispatching_at, "dispatching_at")
    elif dispatching_at is not None:
        raise ValueError("claimed_dispatching_at_forbidden")
    return request, state


def _read_target_receipt(
    path: Path,
    *,
    expected_grant_id: str,
    expected_request_id: str,
) -> ChannelReplyReceipt:
    expected_identity = _request_identity_digest(
        expected_grant_id,
        expected_request_id,
    )
    if path.name != f"{expected_identity}.json":
        raise ValueError("target_receipt_filename_identity_mismatch")
    data = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
    _require_exact_keys(data, TARGET_RECEIPT_FIELDS, "target_receipt")
    version = data.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ValueError("unsupported_target_receipt_version")
    _canonical_utc_text(data["committed_at"], "committed_at")
    raw_receipt = data["receipt"]
    if not isinstance(raw_receipt, Mapping):
        raise ValueError("target_receipt_invalid")
    receipt = _receipt_from_record(raw_receipt)
    if receipt.grant_ref != f"channel-reply-v1:{expected_grant_id}":
        raise ValueError("target_receipt_grant_mismatch")
    if receipt.request_id != expected_request_id:
        raise ValueError("target_receipt_request_mismatch")
    if receipt.status not in {status.value for status in TERMINAL_STATUSES}:
        raise ValueError("target_receipt_not_terminal")
    return receipt


def _read_target_consumed(
    path: Path,
    *,
    expected_grant_id: str,
    expected_request_id: str,
) -> ChannelReplyStatus:
    expected_identity = _request_identity_digest(
        expected_grant_id,
        expected_request_id,
    )
    if path.name != f"{expected_identity}.json":
        raise ValueError("target_consumed_filename_identity_mismatch")
    data = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
    _require_exact_keys(data, TARGET_CONSUMED_FIELDS, "target_consumed")
    version = data.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ValueError("unsupported_target_consumed_version")
    grant_id = _safe_name(str(data["grant_id"]))
    request_id = _safe_name(str(data["request_id"]))
    identity = _bounded_token(data["identity_digest"], "identity_digest", 64)
    status = ChannelReplyStatus(str(data["status"]))
    if grant_id != expected_grant_id or request_id != expected_request_id:
        raise ValueError("target_consumed_tuple_mismatch")
    if identity != expected_identity or not _is_hex_digest(identity):
        raise ValueError("target_consumed_digest_mismatch")
    if status not in TERMINAL_STATUSES:
        raise ValueError("target_consumed_status_invalid")
    _canonical_utc_text(data["finalized_at"], "finalized_at")
    return status


def _read_target_dead(path: Path, *, expected_identity: str) -> None:
    parts = path.name.split(".")
    if (
        len(parts) != 3
        or parts[0] != expected_identity
        or len(parts[1]) != 32
        or any(ch not in "0123456789abcdef" for ch in parts[1])
        or parts[2] != "json"
    ):
        raise ValueError("target_dead_filename_identity_mismatch")
    data = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
    _require_exact_keys(data, TARGET_DEAD_FIELDS, "target_dead")
    version = data.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ValueError("unsupported_target_dead_version")
    identity = _bounded_token(data["identity_digest"], "identity_digest", 64)
    if identity != expected_identity or not _is_hex_digest(identity):
        raise ValueError("target_dead_digest_mismatch")
    reason = data["reason"]
    if (
        not isinstance(reason, str)
        or not reason
        or len(reason) > MAX_RECEIPT_MESSAGE_CHARS
        or _bounded_message(reason) != reason
    ):
        raise ValueError("target_dead_reason_invalid")
    _canonical_utc_text(data["failed_at"], "failed_at")


def _strict_target_cleanup_timestamp(path: Path, state_kind: str) -> str:
    data = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
    if state_kind == "target_outbox":
        _parse_outbox_record(data, expected_identity=path.stem)
    elif state_kind == "target_claim":
        _parse_target_claim_record(data, expected_identity=path.stem)
    elif state_kind == "target_receipt":
        _require_exact_keys(data, TARGET_RECEIPT_FIELDS, "target_receipt")
        if isinstance(data["version"], bool) or data["version"] != PROTOCOL_VERSION:
            raise ValueError("unsupported_target_receipt_version")
        receipt = _receipt_from_record(data["receipt"])
        grant_id = _grant_id_from_ref(receipt.grant_ref)
        if grant_id is None or _request_identity_digest(grant_id, receipt.request_id) != path.stem:
            raise ValueError("target_receipt_filename_identity_mismatch")
    elif state_kind == "target_consumed":
        _require_exact_keys(data, TARGET_CONSUMED_FIELDS, "target_consumed")
        grant_id = _safe_name(str(data["grant_id"]))
        request_id = _safe_name(str(data["request_id"]))
        _read_target_consumed(
            path,
            expected_grant_id=grant_id,
            expected_request_id=request_id,
        )
    elif state_kind == "target_dead":
        expected_identity = path.name.split(".", 1)[0]
        _read_target_dead(path, expected_identity=expected_identity)
    else:
        raise ValueError("target_cleanup_state_kind_invalid")
    return _target_record_timestamp(data)


def _target_record_timestamp(data: Mapping[str, Any]) -> str:
    keys = set(data) if isinstance(data, Mapping) else set()
    if keys == OUTBOX_FIELDS:
        return _canonical_utc_text(data["submitted_at"], "submitted_at")
    if keys == TARGET_CLAIM_FIELDS:
        value = data["dispatching_at"] or data["claimed_at"]
        return _canonical_utc_text(value, "claim_timestamp")
    if keys == TARGET_RECEIPT_FIELDS:
        return _canonical_utc_text(data["committed_at"], "committed_at")
    if keys == TARGET_CONSUMED_FIELDS:
        return _canonical_utc_text(data["finalized_at"], "finalized_at")
    if keys == TARGET_DEAD_FIELDS:
        return _canonical_utc_text(data["failed_at"], "failed_at")
    raise ValueError("target_record_schema_invalid")


def _bounded_token(value: Any, name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ValueError(f"{name}_invalid")
    return value


def _exact_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}_invalid")
    return value


def _bounded_ttl(value: Any) -> int:
    ttl = _exact_int(value, "ttl_seconds")
    if ttl < MIN_GRANT_TTL_SECONDS or ttl > MAX_GRANT_TTL_SECONDS:
        raise ValueError("ttl_seconds_out_of_bounds")
    return ttl


def _bounded_message(message: str) -> str:
    clean = " ".join(str(message).split())
    return clean[:MAX_RECEIPT_MESSAGE_CHARS]


def _utc_now() -> str:
    return _format_utc(datetime.now(timezone.utc))


def _not_expired(expires_at: str, now: str) -> bool:
    try:
        expiry = _parse_utc(expires_at)
        current = _parse_utc(now)
    except ValueError:
        return False
    return current <= expiry


def _canonical_utc(value: str) -> str:
    return _format_utc(_parse_utc(value))


def _canonical_utc_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError(f"{name}_invalid")
    return _canonical_utc(value)


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp_must_be_utc_z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("timestamp_tz_required")
    parsed = parsed.astimezone(timezone.utc)
    if _format_utc(parsed) != value:
        raise ValueError("timestamp_not_canonical")
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_exact_keys(record: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(record, Mapping):
        raise TypeError(f"{label}_record_not_object")
    keys = set(record)
    if keys != expected:
        raise ValueError(f"{label}_keys_invalid")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate_json_key")
        out[key] = value
    return out


def _prepare_root(root: Path) -> Path:
    root = root.expanduser()
    if root.exists() or root.is_symlink():
        if root.is_symlink():
            raise ValueError("state_root_symlink")
        _ensure_private_dir(root)
    else:
        _ensure_private_dir(root)
    return root.resolve(strict=True)


def _existing_directory_root(root: Path) -> Path:
    """Validate a caller-owned root without mutating its permissions."""
    root = root.expanduser()
    st = root.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ValueError("working_directory_invalid")
    return root.resolve(strict=True)


def _ensure_private_dir(path: Path) -> None:
    facade = _session_filesystem(path)
    if facade is not None:
        facade.directory(path, create_private=True)
        return
    if path.exists() or path.is_symlink():
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError("state_directory_invalid")
    else:
        path.mkdir(parents=True, mode=0o700)
    _chmod_private_dir(path)


def _require_private_dir(path: Path) -> None:
    facade = _session_filesystem(path)
    if facade is not None:
        facade.require_private_directory(path)
        return
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ValueError("state_directory_invalid")
    if os.name == "posix":
        if stat.S_IMODE(st.st_mode) != 0o700:
            raise ValueError("state_directory_mode_invalid")
        if hasattr(os, "geteuid") and hasattr(st, "st_uid") and st.st_uid != os.geteuid():
            raise ValueError("state_directory_owner_invalid")


def _validate_private_file_stat(st: os.stat_result) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("record_not_regular")
    if getattr(st, "st_nlink", 1) != 1:
        raise ValueError("record_link_count_invalid")
    if os.name == "posix":
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise ValueError("record_mode_invalid")
        if hasattr(os, "geteuid") and hasattr(st, "st_uid") and st.st_uid != os.geteuid():
            raise ValueError("record_owner_invalid")


def _regular_lstat(path: Path) -> os.stat_result:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise ValueError("record_not_regular")
    _validate_private_file_stat(st)
    return st


def _stat_identity(st: os.stat_result) -> tuple[Any, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_size,
        getattr(st, "st_mtime_ns", None),
        getattr(st, "st_ctime_ns", None),
    )


def _path_lexists(path: Path) -> bool:
    facade = _session_filesystem(path)
    if facade is not None:
        return facade.lexists(path)
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _assert_contained(root: Path, path: Path) -> None:
    facade = _session_filesystem(path)
    if facade is not None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("path_escape") from exc
        if not facade.contains(path):
            raise ValueError("path_escape")
        return
    resolved_root = root.resolve(strict=True)
    parent = path.parent.resolve(strict=True)
    if parent != resolved_root and resolved_root not in parent.parents:
        raise ValueError("path_escape")


def _strict_read_json(path: Path, *, max_bytes: int) -> Any:
    facade = _session_filesystem(path)
    if facade is not None:
        return facade.read_json(path, max_bytes=max_bytes)
    before = _regular_lstat(path)
    if before.st_size > max_bytes:
        raise ValueError("record_too_large")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    fd = os.open(str(path), flags)
    try:
        opened_before = os.fstat(fd)
        _validate_private_file_stat(opened_before)
        if _stat_identity(before) != _stat_identity(opened_before):
            raise ValueError("record_descriptor_mismatch")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        opened_after = os.fstat(fd)
        _validate_private_file_stat(opened_after)
        if _stat_identity(opened_before) != _stat_identity(opened_after):
            raise ValueError("record_changed_during_read")
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise ValueError("record_too_large")
    after = _regular_lstat(path)
    if _stat_identity(opened_after) != _stat_identity(after):
        raise ValueError("record_path_replaced_during_read")
    return json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)


def _canonical_name_matches(state_kind: str, name: str) -> bool:
    spec = CHANNEL_REPLY_STATE_BY_KIND.get(state_kind)
    return spec is not None and re.fullmatch(spec.canonical_pattern, name) is not None


def _state_kind_for_path(path: Path, *, writer_algorithm: str) -> str:
    parent = path.parent.name
    if path.parent.parent.name == ".dead":
        kind = "owner_dead"
    elif path.name == "owner-maintenance-progress.json":
        kind = "owner_maintenance"
    elif path.name == "owner-cleanup-progress.json":
        kind = "owner_cleanup_progress"
    elif path.name == "target-maintenance-progress.json":
        kind = "target_maintenance"
    elif path.name == "target-cleanup-progress.json":
        kind = "target_cleanup_progress"
    elif path.name == "active_capsule.json":
        kind = "target_capsule"
    elif parent == "grants":
        kind = "owner_grant"
    elif parent == "requests":
        kind = "owner_request"
    elif parent == "route_events":
        kind = "owner_route_event"
    elif parent == "route_decisions":
        kind = "owner_route_decision"
    elif parent == "outbox":
        kind = "target_outbox"
    elif parent == "claims":
        kind = "target_claim"
    elif parent == "receipts":
        kind = "target_receipt"
    elif parent == "consumed":
        kind = "target_consumed"
    elif parent == ".dead":
        kind = "target_dead"
    else:
        raise ValueError("uninventoried_channel_reply_state_path")
    spec = CHANNEL_REPLY_STATE_BY_KIND[kind]
    if spec.writer_algorithm != writer_algorithm:
        raise ValueError("state_writer_algorithm_mismatch")
    if not _canonical_name_matches(kind, path.name):
        raise ValueError("state_canonical_filename_mismatch")
    return kind


def _owned_temp_canonical(name: str, state_kinds: tuple[str, ...]) -> str | None:
    for state_kind in state_kinds:
        spec = CHANNEL_REPLY_STATE_BY_KIND[state_kind]
        match = re.fullmatch(spec.owned_temp_pattern, name)
        if match is None:
            continue
        canonical = match.group("canonical")
        if _canonical_name_matches(state_kind, canonical):
            return canonical
    return None


def _private_regular_for_recovery(st: os.stat_result) -> bool:
    if not stat.S_ISREG(st.st_mode):
        return False
    if os.name == "posix":
        if stat.S_IMODE(st.st_mode) != 0o600:
            return False
        if hasattr(os, "geteuid") and hasattr(st, "st_uid") and st.st_uid != os.geteuid():
            return False
    return True


def _is_private_regular_file_path(path: Path) -> bool:
    facade = _session_filesystem(path)
    if facade is not None:
        try:
            info = facade.inspect(path)
            return info is not None and info.private_regular_single_link
        except (FileNotFoundError, OSError, ValueError):
            return False
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            return False
        _validate_private_file_stat(st)
        fd = os.open(
            str(path),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except (OSError, ValueError):
        return False
    try:
        opened = os.fstat(fd)
        _validate_private_file_stat(opened)
        if _stat_identity(st) != _stat_identity(opened):
            return False
        after = path.lstat()
        if _stat_identity(opened) != _stat_identity(after):
            return False
        return True
    except (OSError, ValueError):
        return False
    finally:
        os.close(fd)


def _bounded_scandir(directory: Path, *, max_entries: int = 64) -> tuple[Any, ...]:
    facade = _session_filesystem(directory)
    if facade is not None:
        return facade.scan(directory, max_entries=max_entries)
    entries: list[os.DirEntry[str]] = []
    try:
        _require_private_dir(directory)
        with os.scandir(directory) as iterator:
            for entry in iterator:
                entries.append(entry)
                if len(entries) >= max_entries:
                    break
    except (FileNotFoundError, OSError, ValueError):
        return ()
    return tuple(entries)


def _remove_owned_path(
    path: Path,
    *,
    budget: OwnedRemovalBudget | None = None,
) -> OwnedRemovalResult:
    facade = _session_filesystem(path)
    if facade is not None:
        return facade.remove(path, budget=budget)
    budget = budget or OwnedRemovalBudget()
    try:
        _require_private_dir(path.parent)
        name = path.name
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
            return OwnedRemovalResult("rejected", error="invalid_name")
        fd = os.open(
            str(path.parent),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return OwnedRemovalResult("absent")
    except (OSError, ValueError) as exc:
        return OwnedRemovalResult("retryable", error=type(exc).__name__)
    try:
        state, inspections, removals, error = _remove_owned_name_fd(
            fd,
            name,
            budget=budget,
            depth=0,
        )
        return OwnedRemovalResult(state, inspections, removals, error)
    finally:
        os.close(fd)


def _remove_owned_name_fd(
    parent_fd: int,
    name: str,
    *,
    budget: OwnedRemovalBudget,
    depth: int,
) -> tuple[str, int, int, str | None]:
    if budget.inspections <= 0:
        return "retryable", 0, 0, "inspection_budget_exhausted"
    if depth > budget.max_depth:
        return "retryable", 0, 0, "depth_budget_exhausted"
    budget.inspections -= 1
    inspections = 1
    removals = 0
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "absent", inspections, removals, None
    except OSError as exc:
        return "retryable", inspections, removals, type(exc).__name__
    if not stat.S_ISDIR(st.st_mode):
        if budget.removals <= 0:
            return "retryable", inspections, removals, "removal_budget_exhausted"
        try:
            os.unlink(name, dir_fd=parent_fd)
            budget.removals -= 1
            removals += 1
            os.fsync(parent_fd)
            return "removed", inspections, removals, None
        except FileNotFoundError:
            return "absent", inspections, removals, None
        except OSError as exc:
            return "retryable", inspections, removals, type(exc).__name__
    if os.name == "posix":
        if hasattr(os, "geteuid") and hasattr(st, "st_uid") and st.st_uid != os.geteuid():
            return "retryable", inspections, removals, "directory_owner_invalid"
    try:
        child_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        return "retryable", inspections, removals, type(exc).__name__
    try:
        opened = os.fstat(child_fd)
        if opened.st_dev != st.st_dev or opened.st_ino != st.st_ino:
            return "retryable", inspections, removals, "directory_replaced"
        try:
            with os.scandir(child_fd) as iterator:
                for entry in iterator:
                    child_state, child_inspections, child_removals, child_error = _remove_owned_name_fd(
                        child_fd,
                        entry.name,
                        budget=budget,
                        depth=depth + 1,
                    )
                    inspections += child_inspections
                    removals += child_removals
                    if child_state in {"progress", "retryable", "rejected"}:
                        return (
                            "progress" if removals else child_state,
                            inspections,
                            removals,
                            child_error,
                        )
                    if budget.inspections <= 0 or budget.removals <= 0:
                        return "progress" if removals else "retryable", inspections, removals, None
        except OSError as exc:
            return "retryable", inspections, removals, type(exc).__name__
        if budget.removals <= 0:
            return "progress" if removals else "retryable", inspections, removals, None
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if current.st_dev != opened.st_dev or current.st_ino != opened.st_ino:
                return "progress" if removals else "retryable", inspections, removals, "directory_replaced"
            os.rmdir(name, dir_fd=parent_fd)
            budget.removals -= 1
            removals += 1
            os.fsync(parent_fd)
            return "removed", inspections, removals, None
        except FileNotFoundError:
            return "absent", inspections, removals, None
        except OSError as exc:
            return "progress" if removals else "retryable", inspections, removals, type(exc).__name__
    finally:
        os.close(child_fd)


def _nofollow_directory_children(directory: Path) -> tuple[Path, ...]:
    """Compatibility inventory for explicit exhaustive maintenance only."""
    facade = _session_filesystem(directory)
    if facade is not None:
        return tuple(
            directory / entry.name
            for entry in facade.scan(directory)
            if entry.is_dir(follow_symlinks=False)
        )
    children: list[Path] = []
    try:
        _require_private_dir(directory)
        entries = _bounded_scandir(directory)
    except (FileNotFoundError, OSError, ValueError):
        return ()
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                children.append(directory / entry.name)
        except OSError:
            continue
    return tuple(children)


def _cursor_to_record(cursor: DirectoryScanCursor | None) -> dict[str, Any] | None:
    if cursor is None:
        return None
    return {
        "scheme": cursor.scheme,
        "volume": cursor.volume,
        "object_id": cursor.object_id,
        "position": cursor.position,
        "pending": list(cursor.pending),
    }


def _cursor_from_record(value: Any) -> DirectoryScanCursor | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "scheme", "volume", "object_id", "position", "pending"
    }:
        raise ValueError("maintenance_cursor_shape_invalid")
    scheme = value["scheme"]
    volume = value["volume"]
    object_id = value["object_id"]
    position = value["position"]
    pending = value["pending"]
    if (
        not isinstance(scheme, str)
        or not scheme
        or not isinstance(volume, (int, str, bytes))
        or isinstance(volume, bool)
        or not isinstance(object_id, (int, str, bytes))
        or isinstance(object_id, bool)
        or not isinstance(position, int)
        or isinstance(position, bool)
        or position < 0
        or not isinstance(pending, list)
        or len(pending) > 32
        or any(not isinstance(item, str) or not item or len(item) > 255 for item in pending)
    ):
        raise ValueError("maintenance_cursor_invalid")
    return DirectoryScanCursor(scheme, volume, object_id, position, tuple(pending))


_OWNER_CLEANUP_CURSOR_KEYS = (
    "owner-cleanup:events",
    "owner-cleanup:requests",
    "owner-cleanup:grants",
    "owner-cleanup:dead:grants",
    "owner-cleanup:dead:requests",
    "owner-cleanup:dead:route_events",
    "owner-cleanup:dead:route_decisions",
)
_TARGET_CLEANUP_CURSOR_KEYS = (
    "target-cleanup:claim-recovery",
    "target-cleanup:outbox",
    "target-cleanup:claims",
    "target-cleanup:receipts",
    "target-cleanup:consumed",
    "target-cleanup:dead",
)


def _read_cleanup_progress(
    path: Path,
    *,
    scope: str,
    class_count: int,
    cursor_keys: tuple[str, ...],
) -> tuple[int, dict[str, DirectoryScanCursor | None]]:
    default_cursors = {key: None for key in cursor_keys}
    try:
        record = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
        if not isinstance(record, Mapping) or set(record) != {
            "version", "scope", "next_class", "cursors"
        }:
            raise ValueError("cleanup_progress_shape_invalid")
        next_class = record["next_class"]
        raw_cursors = record["cursors"]
        if (
            record["version"] != 1
            or record["scope"] != scope
            or not isinstance(next_class, int)
            or isinstance(next_class, bool)
            or not 0 <= next_class < class_count
            or not isinstance(raw_cursors, Mapping)
            or set(raw_cursors) != set(cursor_keys)
        ):
            raise ValueError("cleanup_progress_invalid")
        cursors = {key: _cursor_from_record(raw_cursors[key]) for key in cursor_keys}
        return next_class, cursors
    except FileNotFoundError:
        return 0, default_cursors
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        # Progress is proof-free and non-authoritative. Sanitize only the exact
        # object that failed validation, then restart fair traversal from zero.
        _remove_file_or_empty_directory(path)
        return 0, default_cursors


def _write_cleanup_progress(
    path: Path,
    *,
    scope: str,
    next_class: int,
    cursor_keys: tuple[str, ...],
    cursors: Mapping[str, DirectoryScanCursor | None],
) -> None:
    _atomic_private_json(
        path,
        {
            "version": 1,
            "scope": scope,
            "next_class": next_class,
            "cursors": {key: _cursor_to_record(cursors.get(key)) for key in cursor_keys},
        },
    )


def _default_maintenance_progress(scope: str, first_surface: str) -> dict[str, Any]:
    return {
        "version": 1,
        "scope": scope,
        "surface": first_surface,
        "cursor": None,
    }


def _read_maintenance_progress(
    path: Path,
    *,
    scope: str,
    surfaces: tuple[str, ...],
) -> tuple[str, DirectoryScanCursor | None]:
    default = _default_maintenance_progress(scope, surfaces[0])
    try:
        record = _strict_read_json(path, max_bytes=MAX_RECORD_BYTES)
    except FileNotFoundError:
        record = default
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        # Maintenance metadata is never authority. Remove only the exact object
        # whose strict read failed; a replacement is left untouched by the facade.
        _remove_file_or_empty_directory(path)
        record = default
    if not isinstance(record, Mapping) or set(record) != {"version", "scope", "surface", "cursor"}:
        _remove_file_or_empty_directory(path)
        record = default
    if (
        record.get("version") != 1
        or record.get("scope") != scope
        or record.get("surface") not in surfaces
    ):
        _remove_file_or_empty_directory(path)
        record = default
    try:
        cursor = _cursor_from_record(record.get("cursor"))
    except ValueError:
        _remove_file_or_empty_directory(path)
        return surfaces[0], None
    return str(record["surface"]), cursor


def _write_maintenance_progress(
    path: Path,
    *,
    scope: str,
    surface: str,
    cursor: DirectoryScanCursor | None,
) -> None:
    _atomic_private_json(
        path,
        {
            "version": 1,
            "scope": scope,
            "surface": surface,
            "cursor": _cursor_to_record(cursor),
        },
    )


def _reconcile_owned_temps_page(
    progress_path: Path,
    *,
    scope: str,
    surfaces: tuple[tuple[str, Path, tuple[str, ...]], ...],
    max_inspections: int,
) -> int:
    """Inspect at most one charged page and durably rotate owned-temp surfaces."""
    budget = max(0, int(max_inspections))
    if budget == 0 or not surfaces:
        return 0
    facade = _session_filesystem(progress_path)
    if facade is None:
        # Mutation paths always bind a production session. There is deliberately
        # no path fallback for resumable reconciliation.
        raise OSError("channel_reply resumable reconciliation requires mutation session")
    names = tuple(item[0] for item in surfaces)
    surface, cursor = _read_maintenance_progress(progress_path, scope=scope, surfaces=names)
    index = names.index(surface)
    _surface_name, directory, state_kinds = surfaces[index]
    try:
        entries, inspected, complete, next_cursor = facade.scan_page(
            directory,
            max_inspections=budget,
            max_candidates=budget,
            cursor=cursor,
        )
    except OSError as exc:
        if cursor is None or "cursor identity mismatch" not in str(exc):
            raise
        # Directory replacement invalidates only proof-free progress. The bound
        # session has already verified the replacement as a private child of the
        # pinned root; restart that surface without using the stale cookie.
        entries, inspected, complete, next_cursor = facade.scan_page(
            directory,
            max_inspections=budget,
            max_candidates=budget,
            cursor=None,
        )
    for entry in entries:
        if _owned_temp_canonical(entry.name, state_kinds) is not None:
            _remove_file_or_empty_directory(directory / entry.name)
    facade.fsync(directory)
    if complete:
        index = (index + 1) % len(surfaces)
        surface = names[index]
        next_cursor = None
    _write_maintenance_progress(
        progress_path,
        scope=scope,
        surface=surface,
        cursor=next_cursor,
    )
    return inspected


def _reconcile_target_state_temps(
    root: Path,
    *,
    progress_path: Path,
    max_inspections: int,
) -> int:
    surfaces = ((
        "root",
        root,
        ("target_capsule", "target_maintenance", "target_cleanup_progress"),
    ),) + tuple(
        (
            kind,
            root / CHANNEL_REPLY_STATE_BY_KIND[kind].directory,
            (kind,),
        )
        for kind in (
            "target_outbox",
            "target_claim",
            "target_receipt",
            "target_consumed",
            "target_dead",
        )
    )
    return _reconcile_owned_temps_page(
        progress_path,
        scope="target",
        surfaces=surfaces,
        max_inspections=max_inspections,
    )


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    facade = _session_filesystem(path)
    if facade is not None:
        facade.write_json(path, payload, create=False)
        return
    _state_kind_for_path(path, writer_algorithm="atomic-replace")
    _ensure_private_dir(path.parent)
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _chmod_private_file(path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _atomic_create_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish one complete immutable queue inode without replacement.

    The fsynced hidden sibling is not a queue candidate. ``os.link`` supplies
    no-replace name publication where the local filesystem honors that contract.
    Callers hold the target mutation lock through publication and hidden-name
    removal; recovery normalizes every adjacent interruption cut before reading.
    POSIX behavior is tested natively. Native Windows/NTFS durability and process-
    death semantics remain an explicit acceptance gate, not an inferred claim.
    """
    facade = _session_filesystem(path)
    if facade is not None:
        facade.write_json(path, payload, create=True)
        return
    _state_kind_for_path(path, writer_algorithm="atomic-create-hard-link")
    _require_private_dir(path.parent)
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    data = text.encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short queue write")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    else:
        os.close(fd)
    try:
        # Hard-link creation is the portable stdlib no-replace primitive. The
        # source is our newly created regular file, never an untrusted symlink.
        os.link(tmp, path)
        _fsync_dir(path.parent)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
        _fsync_dir(path.parent)


def _chmod_private_dir(path: Path) -> None:
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _chmod_private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _fsync_dir(path: Path) -> None:
    facade = _session_filesystem(path)
    if facade is not None:
        facade.fsync(path)
        return
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
