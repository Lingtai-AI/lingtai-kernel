"""LICC v1 — LingTai Inbox Callback Contract.

A filesystem-based inbox that lets out-of-process MCP servers push events
into the agent's inbox. The contract:

- MCPs write JSON event files to:
      <agent_working_dir>/.mcp_inbox/<mcp_name>/<event_id>.json
- Atomic write: write to ``<event_id>.json.tmp``, fsync, then rename.
- Schema (v1):
      {
        "licc_version": 1,
        "from": "human-readable sender (required)",
        "subject": "one-line summary (required, max 200 chars)",
        "body": "full message body (required)",
        "metadata": {...optional},
        "wake": true,                 // optional, default true
        "received_at": "ISO 8601"     // optional producer timestamp for lag telemetry
      }
- The kernel polls all subdirs at the same cadence as the mailbox listener
  (0.5s), validates each file, dispatches to the agent's inbox via
  _make_message, calls _wake_nap when wake=true, and deletes the file.
- Invalid files (parse error, missing required fields, unknown version)
  are moved to a sibling ``.dead/`` directory with a ``.error.json``
  describing the failure. Dead-letters are never auto-deleted.

The MCP subprocess learns where to write via two env vars injected by the
kernel's MCP loader:
    LINGTAI_AGENT_DIR  — absolute path of the agent's working directory.
    LINGTAI_MCP_NAME   — the MCP's registry name.

Both vars are injected automatically for every MCP the kernel spawns, so
any MCP that wants to use LICC just needs to read them and write files.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent

log = logging.getLogger(__name__)

# Keep these contract constants and validate_event() lightweight at module
# import time: the client-side producer (licc.py) imports/re-exports them so
# out-of-process MCP servers can share the receiver's source of truth without
# starting the poller or pulling in heavy runtime machinery.
INBOX_DIRNAME = ".mcp_inbox"
DEAD_DIRNAME = ".dead"
TMP_SUFFIX = ".json.tmp"
EVENT_SUFFIX = ".json"
LICC_VERSION = 1

POLL_INTERVAL = 0.5  # seconds; matches FilesystemMailService poll cadence
MAX_EVENTS_PER_CYCLE = 100
_MAX_SUBJECT_LEN = 200

# Notification preview cap. The preview is the first N chars of the message
# body — the content-bearing snippet that lets the agent triage what arrived
# without calling read() on every event. The full body still stays behind the
# MCP's read action so the agent has one source of truth (issue #37 invariant
# preserved). Sender and subject are NOT capped here — sender is bounded by
# upstream MCP construction (usernames, email addresses) and subject is
# already validated <= _MAX_SUBJECT_LEN by validate_event. One preview entry
# per consumed event — list length is naturally bounded by MAX_EVENTS_PER_CYCLE.
# 10000 chars ≈ 2500 tokens — generous cap for IM conversations (telegram, etc.)
# that need to show recent message history inline in the notification.
_PREVIEW_FIELD_CAP = 10000  # chars of body to inline as the snippet (raised for IM conversations)

# Optional metadata fields that MCP servers may attach to a LICC event under
# the top-level `metadata` dict. When present and well-formed, these get
# copied into the per-event preview so the agent can see *which*
# conversation / message / platform an event came from without calling
# read() on the MCP. Strictly additive: legacy events without metadata
# behave identically; non-string or empty values are silently ignored.
# Capped at the same 200 chars as `subject` — these are refs/handles, not
# message bodies, so they should be naturally short. A misbehaving MCP that
# stuffs a kilobyte into `conversation_ref` won't bloat the notification.
_PREVIEW_META_FIELDS = ("conversation_ref", "message_ref", "platform")
_PREVIEW_META_FIELD_CAP = 200

# Optional structured metadata that curated IM producers can attach under
# event["metadata"]. These fields are copied into data.previews[*] after a
# JSON-serializability and size check, so agents get stable structure without
# letting arbitrary external MCP payloads bloat notification state.
_PREVIEW_STRUCTURED_META_FIELDS = {
    "latest_incoming": dict,
    "recent_messages": list,
    # Full referenced Telegram messages (e.g. a reply target older than the
    # last-20 window) that the persistent lane should carry in full rather than
    # snippet. Copied under the same JSON-safe size cap as the other structured
    # IM fields.
    "referenced_messages": list,
}
_PREVIEW_STRUCTURED_META_JSON_CAP = 20_000
_TELEMETRY_ROUND_DIGITS = 3


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_event(event: dict) -> tuple[bool, str | None]:
    """Validate a parsed LICC event. Returns (is_valid, error)."""
    if not isinstance(event, dict):
        return False, "event must be a JSON object"

    version = event.get("licc_version", LICC_VERSION)
    if version != LICC_VERSION:
        return False, f"unsupported licc_version: {version!r} (this kernel speaks {LICC_VERSION})"

    sender = event.get("from")
    if not isinstance(sender, str) or not sender:
        return False, "missing or empty field: from"

    subject = event.get("subject")
    if not isinstance(subject, str) or not subject:
        return False, "missing or empty field: subject"
    if len(subject) > _MAX_SUBJECT_LEN:
        return False, f"subject too long ({len(subject)} > {_MAX_SUBJECT_LEN} chars)"

    body = event.get("body")
    if not isinstance(body, str):
        return False, "missing or non-string field: body"

    metadata = event.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return False, "metadata must be a JSON object when present"

    wake = event.get("wake", True)
    if not isinstance(wake, bool):
        return False, "wake must be a boolean when present"

    return True, None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _format_notification_summary(mcp_name: str, count: int, has_human_messages: bool = False) -> str:
    """Render a count-only signal notification body for MCP events.

    .. deprecated::
        This function is retained for backward compatibility but is no
        longer called by ``_dispatch_summary``.  The notification is now
        published via the kernel's ``.notification/`` filesystem-as-protocol
        (see ``_dispatch_summary``).

    Issue #47: Human messages (telegram, email, etc.) are prioritized
    in the notification summary to help agents distinguish between
    human messages and system events.
    """
    plural = "" if count == 1 else "s"
    priority_hint = " [HUMAN]" if has_human_messages else ""
    return (
        f"[system] New event from MCP '{mcp_name}': "
        f"{count} unread message{plural}{priority_hint}. "
        f"Call the MCP's read/check action to fetch."
    )


def _copy_structured_preview_meta(value: object) -> object | None:
    """Return a JSON-safe structured preview field, or None if unsafe/too large."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    if len(encoded) > _PREVIEW_STRUCTURED_META_JSON_CAP:
        return None
    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        return None


def _extract_preview_meta(event: dict) -> dict:
    """Pull optional IM/chat metadata fields off a LICC event for the preview.

    Returns a dict containing supported scalar routing keys
    (``conversation_ref``, ``message_ref``, ``platform``) whose values are
    non-empty strings, each truncated to ``_PREVIEW_META_FIELD_CAP``. Curated
    IM producers may also attach bounded JSON-safe structured fields such as
    ``latest_incoming`` and ``recent_messages`` so agents do not need to parse
    a Markdown conversation transcript to find the actionable message.

    Strictly additive: events without ``metadata`` or with non-dict /
    non-string / wrong-type / oversized values yield only the supported safe
    subset. No validation errors raised — a malformed metadata payload just
    produces no extra preview fields.
    """
    meta = event.get("metadata")
    if not isinstance(meta, dict):
        return {}

    out: dict = {}
    for key in _PREVIEW_META_FIELDS:
        val = meta.get(key)
        if isinstance(val, str) and val:
            out[key] = val[:_PREVIEW_META_FIELD_CAP]

    for key, expected_type in _PREVIEW_STRUCTURED_META_FIELDS.items():
        val = meta.get(key)
        if isinstance(val, expected_type):
            safe_val = _copy_structured_preview_meta(val)
            if safe_val is not None:
                out[key] = safe_val
    return out

def _bounded_elapsed_s(start_ts: float, end_ts: float) -> float:
    """Return a rounded nonnegative elapsed duration in seconds."""
    return round(max(0.0, end_ts - start_ts), _TELEMETRY_ROUND_DIGITS)


def _parse_received_at_ts(value: object) -> float | None:
    """Parse a producer supplied top-level received_at timestamp.

    LICC producers emit timezone-aware ISO 8601 strings. Accept the common
    ``Z`` UTC suffix plus explicit offsets; return None for missing, naive,
    or malformed values so legacy/sloppy events still dispatch.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.timestamp()


def _event_telemetry_fields(
    event: dict,
    *,
    queued_at_ts: float | None,
    consumed_at_ts: float,
) -> dict:
    """Build additive per-event timing fields for the mcp_inbox_event log."""
    baseline_ts = consumed_at_ts if queued_at_ts is None else queued_at_ts
    fields = {
        "drain_latency_s": _bounded_elapsed_s(baseline_ts, consumed_at_ts),
    }
    producer_ts = _parse_received_at_ts(event.get("received_at"))
    if producer_ts is not None:
        fields["producer_lag_s"] = _bounded_elapsed_s(producer_ts, consumed_at_ts)
    return fields


def _consume_event(
    agent: "BaseAgent",
    mcp_name: str,
    event: dict,
    *,
    queued_at_ts: float | None = None,
    consumed_at_ts: float | None = None,
) -> tuple[bool, dict]:
    """Record the per-event log entry; return (wake, preview).

    The preview surfaces sender, subject, and a bounded body preview so the
    agent can triage what arrived without calling read() on every event.
    ``from`` and ``subject`` pass through as supplied by the upstream MCP
    event construction; ``preview`` is truncated at ``_PREVIEW_FIELD_CAP``
    because bodies can be arbitrarily large (multi-paragraph emails, OCR
    text, voice transcripts, chat conversation digests).

    The preview body is included once in structured notification data
    (``data.previews[*].preview``) and is not duplicated into the coalesced
    notification's top-level ``instructions`` text. The MCP's read action
    remains the source of truth for exact/full message content.

    Optional IM/chat metadata fields (``conversation_ref``, ``message_ref``,
    ``platform``) from ``event["metadata"]`` are copied into the preview
    when present as non-empty strings. Legacy events without metadata
    produce the exact same preview shape as before.

    Per-event traceability flows to the agent log so operators can audit
    individual deliveries; the user-visible coalesced notification is
    dispatched once per MCP per sweep by ``_dispatch_summary``.
    """
    wake = bool(event.get("wake", True))
    sender = event["from"]
    subject = event["subject"]
    body = event["body"]
    consumed_at = time.time() if consumed_at_ts is None else consumed_at_ts
    agent._log(
        "mcp_inbox_event",
        mcp=mcp_name,
        sender=sender,
        subject=subject,
        wake=wake,
        **_event_telemetry_fields(
            event,
            queued_at_ts=queued_at_ts,
            consumed_at_ts=consumed_at,
        ),
    )
    preview_text = body[:_PREVIEW_FIELD_CAP]
    preview = {
        "from": sender,
        "subject": subject,
        "preview": preview_text,
        "preview_truncated": len(body) > _PREVIEW_FIELD_CAP,
    }
    preview.update(_extract_preview_meta(event))
    return wake, preview


def _dispatch_summary(
    agent: "BaseAgent",
    mcp_name: str,
    count: int,
    wake: bool,
    has_human_messages: bool = False,
    previews: list[dict] | None = None,
) -> None:
    """Publish one coalesced notification covering ``count`` events from ``mcp_name``.

    Each preview entry carries the sender, subject, a bounded body preview,
    and optional lightweight routing metadata. Body preview text is kept in
    structured ``data.previews[*].preview`` only; top-level ``instructions``
    contains read/check guidance plus sender/subject/metadata context, not a
    second copy of the body preview.

    Uses the kernel's canonical ``.notification/`` filesystem-as-protocol
    instead of the legacy inbox queue.  The notification file is written as
    ``.notification/mcp.<mcp_name>.json`` and surfaces in the agent's
    ``notification(action="check")`` wire block alongside email, soul,
    and system events.

    No explicit wake is needed — ``_sync_notifications`` detects the
    fingerprint change on the next heartbeat tick and handles the
    IDLE→MSG_TC_WAKE transition.
    """
    from lingtai.kernel.notifications import submit as publish_notification

    plural = "" if count == 1 else "s"
    header = f"{count} new event{plural} from MCP '{mcp_name}'"
    priority = "high" if has_human_messages else "normal"
    previews = previews or []

    instructions_lines = [
        f"Call the MCP '{mcp_name}' read/check action to fetch "
        f"the {count} new event{plural} when the structured preview is "
        f"truncated, ambiguous, media/callback-heavy, or exact anchoring is "
        f"needed. If data.previews clearly identifies the latest actionable "
        f"incoming message and preview_truncated is false, the agent may reply "
        f"directly from the preview without an extra read. Structured previews "
        f"are available in data.previews with sender, subject, "
        f"preview_truncated, optional routing metadata, optional IM context, "
        f"and up to {_PREVIEW_FIELD_CAP} chars of body text."
    ]
    if previews:
        instructions_lines.append("")
        instructions_lines.append("Previews:")
        for i, p in enumerate(previews, start=1):
            instructions_lines.append(f"  {i}. {p['from']} — {p['subject']}")
            meta_bits = [f"{k}={p[k]}" for k in _PREVIEW_META_FIELDS if k in p]
            if p.get("preview_truncated"):
                meta_bits.append("preview_truncated=true")
            if "latest_incoming" in p:
                meta_bits.append("latest_incoming=structured")
            if "recent_messages" in p:
                meta_bits.append("recent_messages=structured")
            if meta_bits:
                instructions_lines.append(f"     [{', '.join(meta_bits)}]")

    publish_notification(
        agent._working_dir,
        f"mcp.{mcp_name}",
        header=header,
        icon="💬",
        priority=priority,
        instructions="\n".join(instructions_lines),
        data={
            "count": count,
            "source": mcp_name,
            "has_human_messages": has_human_messages,
            "previews": previews,
        },
    )


# ---------------------------------------------------------------------------
# Dead-letter
# ---------------------------------------------------------------------------

def _dead_letter(file_path: Path, error: str) -> None:
    """Move *file_path* into a ``.dead/`` subdir with a sibling error file."""
    dead_dir = file_path.parent / DEAD_DIRNAME
    try:
        dead_dir.mkdir(parents=True, exist_ok=True)
        target = dead_dir / file_path.name
        error_target = dead_dir / (file_path.stem + ".error.json")
        # Move the bad event, write the error report alongside it.
        shutil.move(str(file_path), str(target))
        error_target.write_text(
            json.dumps({"error": error, "timestamp": _now_iso()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.warning("mcp_inbox: dead-lettered %s: %s", target, error)
    except OSError as e:
        log.error("mcp_inbox: failed to dead-letter %s: %s (original error: %s)",
                  file_path, e, error)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scan_once(agent: "BaseAgent", inbox_root: Path) -> int:
    """One sweep through .mcp_inbox/<mcp_name>/*.json. Returns count dispatched.

    Per MCP per sweep: consume up to ``MAX_EVENTS_PER_CYCLE`` valid events
    (logging each individually), then post a single coalesced notification.
    The notification includes lightweight instructions plus structured
    preview entries; body preview text stays in ``data.previews[*].preview``
    rather than being duplicated into the top-level ``instructions`` string.
    """
    if not inbox_root.is_dir():
        return 0

    dispatched = 0
    for mcp_dir in inbox_root.iterdir():
        if not mcp_dir.is_dir():
            continue
        if mcp_dir.name.startswith("."):
            # Skip our own .dead and any other dotted dirs.
            continue

        mcp_name = mcp_dir.name
        # Bound work per cycle to avoid pathological backlog blocking.
        events_this_mcp = 0
        any_wake = False
        # One preview per consumed event — naturally bounded by
        # MAX_EVENTS_PER_CYCLE. Each preview is sender + truncated subject.
        previews: list[dict] = []
        # Issue #47: Track if this MCP has human messages
        # Human messages come from messaging MCPs (telegram, email, feishu, wechat)
        has_human_messages = False
        for entry in sorted(mcp_dir.iterdir()):
            if events_this_mcp >= MAX_EVENTS_PER_CYCLE:
                break
            if not entry.is_file():
                continue
            name = entry.name
            if name.endswith(TMP_SUFFIX):
                # Half-written; ignore until rename completes.
                continue
            if not name.endswith(EVENT_SUFFIX):
                continue

            # Read + parse + validate.
            try:
                raw = entry.read_text(encoding="utf-8")
            except OSError as e:
                _dead_letter(entry, f"read failed: {e}")
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError as e:
                _dead_letter(entry, f"invalid JSON: {e}")
                continue

            ok, err = validate_event(event)
            if not ok:
                _dead_letter(entry, f"validation failed: {err}")
                continue

            # Consume (log per-event + collect wake intent + preview) and delete on success.
            try:
                try:
                    queued_at_ts = entry.stat().st_mtime
                except OSError:
                    queued_at_ts = None
                wake, preview = _consume_event(
                    agent,
                    mcp_name,
                    event,
                    queued_at_ts=queued_at_ts,
                    consumed_at_ts=time.time(),
                )
            except Exception as e:
                _dead_letter(entry, f"dispatch failed: {e}")
                continue
            any_wake = any_wake or wake
            previews.append(preview)

            # Issue #47: Detect human messages
            # Human messages come from messaging MCPs (telegram, email, feishu, wechat)
            # and have a "from" field that looks like a username (not a system sender)
            if not has_human_messages:
                sender = event.get("from", "")
                # Check if this is a human message (not from system/soul/etc.)
                # Human senders typically have usernames or first names
                if sender and not sender.startswith("system") and not sender.startswith("soul"):
                    has_human_messages = True

            try:
                entry.unlink()
            except OSError as e:
                # Won't double-deliver because the file's still there for
                # next cycle to re-attempt; we logged the dispatch already
                # though. Edge case worth noting in the agent log.
                log.warning("mcp_inbox: dispatched but failed to delete %s: %s", entry, e)

            dispatched += 1
            events_this_mcp += 1

        # One coalesced [system] notification per MCP per sweep — keeps the
        # wake-up signal but eliminates the duplicate-content footgun (#37).
        if events_this_mcp > 0:
            try:
                _dispatch_summary(agent, mcp_name, events_this_mcp, any_wake,
                                  has_human_messages=has_human_messages,
                                  previews=previews)
            except Exception as e:
                log.error(
                    "mcp_inbox: failed to post summary for %s (count=%d): %s",
                    mcp_name, events_this_mcp, e,
                )

    return dispatched


# ---------------------------------------------------------------------------
# Poller — owned by the Agent
# ---------------------------------------------------------------------------

class MCPInboxPoller:
    """Polls the agent's .mcp_inbox/ tree. One per agent. Daemon thread."""

    def __init__(self, agent: "BaseAgent"):
        self._agent = agent
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._inbox_root = Path(agent._working_dir) / INBOX_DIRNAME

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        # Pre-create the root so MCPs can write before any event lands.
        try:
            self._inbox_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("mcp_inbox: cannot create %s: %s", self._inbox_root, e)
            return

        agent_label = self._agent.agent_name or self._agent._working_dir.name

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    _scan_once(self._agent, self._inbox_root)
                except Exception as e:
                    log.error("mcp_inbox: scan failed for %s: %s", agent_label, e)
                self._stop.wait(POLL_INTERVAL)

        self._thread = threading.Thread(
            target=_loop,
            daemon=True,
            name=f"mcp-inbox-{agent_label}",
        )
        self._thread.start()
        log.info("mcp_inbox: poller started for %s (root=%s)",
                 agent_label, self._inbox_root)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
