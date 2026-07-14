"""
BaseAgent — generic agent kernel with intrinsic tools and capability dispatch.

Key concepts:
    - **5-state lifecycle**: ACTIVE, IDLE, STUCK, ASLEEP, SUSPENDED.
    - **Persistent LLM session**: each agent keeps its chat session across messages.
    - **2-layer tool dispatch**: intrinsics (built-in) + capability handlers.
    - **Opaque context**: the host app can pass any context object — the agent
      stores it but never introspects it.
    - **4 optional services**: LLM, FileIO, Mail, Event Journal —
      missing service auto-disables the intrinsics it backs.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ..config import AgentConfig
from ..event_journal import EventJournalPort
from ..state import AgentState
from ..workdir import WorkingDir
from ..workdir_lease import WorkdirLeasePort
from ..notification_store import NotificationStorePort
from ..agent_presence import AgentPresenceStorePort
from ..lifecycle_clock import LifecycleClockPort
from ..refresh_watcher import RefreshWatcherPort
from ..snapshot import SnapshotPort, SourceRevisionPort
from ..message import Message
from ..prompt import SystemPromptManager
from ..llm import (
    FunctionSchema,
    LLMService,
    ToolCall,
)
from ..logging import get_logger
from ..meta_block import (
    TOOL_META_CONTEXT_EVENT_PENDING_KEY,
    TOOL_META_CONTEXT_PENDING_KEY,
    build_meta,
    build_tool_meta_token_usage,
    build_notification_payload,
    build_notification_persistent_payload,
    formal_tool_result_preview,
    formal_tool_result_visible_len,
    record_notification_persistent_delivery,
    sanitize_email_notification_after_persistent,
    sanitize_feishu_notification_after_persistent,
    sanitize_telegram_notification_after_persistent,
    sanitize_whatsapp_notification_after_persistent,
    sanitize_wechat_notification_after_persistent,
)
from ..session import SessionManager
from ..tc_inbox import TCInbox
from ..token_ledger import append_token_entry
from .._fsutil import atomic_write_json, atomic_write_text, read_json
from ..trace_redaction import redact_for_trajectory
from ..runtime_identity import runtime_identity_event_fields

logger = get_logger()

# Private MCP tool name for the kernel-driven Telegram Task Card reverse channel.
# It is intentionally unlisted by the Telegram server's ``list_tools`` so the
# model can neither see nor call it, and the server forces the task-card action
# server-side — the kernel therefore sends no ``action`` here. Mirrors
# ``_PRIVATE_TASK_CARD_TOOL`` in ``lingtai.mcp_servers.telegram.server``; it is a
# literal (not an import) because the kernel must not depend on ``mcp_servers``.
# Keep the two in sync.
_TASK_CARD_TOOL = "_lingtai_telegram_task_card"

# Env var controlling how many ordinary tool rows the automatic Task Card shows
# as a rolling window (Jason Telegram 7096).  Read from ``os.environ`` on each
# call (no import-time caching), so it reflects the process's current
# environment; changing an already-running agent's value still requires the
# usual restart/re-exec that changes a process environment.  No config file,
# flag, or persistent state.
_TASK_CARD_MAX_TOOL_ROWS_ENV = "LINGTAI_TASK_CARD_MAX_TOOL_ROWS"
_TASK_CARD_DEFAULT_MAX_TOOL_ROWS = 1
_TASK_CARD_MIN_PERSISTED_TOOL_ROWS = 1
_TASK_CARD_MAX_PERSISTED_TOOL_ROWS = 10


def _task_card_max_tool_rows(working_dir: Path | None = None) -> int:
    """Return the rolling normal-row window for the automatic Task Card.

    A valid agent-local ``telegram/taskcard.json`` ``normal_rows`` value (1-10)
    takes precedence so ``/taskcard N`` applies without a restart. Legacy agents
    without that field retain the existing environment-variable behavior: a
    positive ``LINGTAI_TASK_CARD_MAX_TOOL_ROWS`` value is accepted without an
    additional clamp, and invalid or missing values fall back to 1.
    """
    if working_dir is not None:
        try:
            data = read_json(Path(working_dir) / "telegram" / "taskcard.json", expect=dict)
            persisted = data.get("normal_rows")
            if (
                type(persisted) is int
                and _TASK_CARD_MIN_PERSISTED_TOOL_ROWS
                <= persisted
                <= _TASK_CARD_MAX_PERSISTED_TOOL_ROWS
            ):
                return persisted
        except (OSError, ValueError, TypeError):
            pass

    raw = os.environ.get(_TASK_CARD_MAX_TOOL_ROWS_ENV)
    if raw is None:
        return _TASK_CARD_DEFAULT_MAX_TOOL_ROWS
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return _TASK_CARD_DEFAULT_MAX_TOOL_ROWS
    return value if value > 0 else _TASK_CARD_DEFAULT_MAX_TOOL_ROWS


def _block_type_name(block: object) -> str:
    """Return a compact, safe block type label for diagnostics."""
    try:
        data = block.to_dict()  # type: ignore[attr-defined]
        btype = data.get("type") if isinstance(data, dict) else None
        if isinstance(btype, str) and btype:
            return btype
    except Exception:
        pass
    name = type(block).__name__
    if name.endswith("Block"):
        name = name[:-5]
    return name[:80]


def _pending_tool_call_diagnostics(iface, *, tail_limit: int = 3) -> dict:
    """Bounded, argument-free diagnostics for a pending tool-call tail."""
    entries = list(getattr(iface, "entries", None) or [])
    tail_entries = entries[-tail_limit:]
    tail = entries[-1] if entries else None
    pending_calls = []
    if getattr(tail, "role", None) == "assistant":
        pending_calls = [
            block
            for block in getattr(tail, "content", []) or []
            if hasattr(block, "id") and hasattr(block, "name") and hasattr(block, "args")
        ]

    return {
        "pending_tool_call_count": len(pending_calls),
        "pending_tool_call_ids": [getattr(call, "id", None) for call in pending_calls],
        "pending_tool_names": [getattr(call, "name", None) for call in pending_calls],
        "pending_tail_roles": [getattr(entry, "role", None) for entry in tail_entries],
        "pending_tail_block_types": [
            [_block_type_name(block) for block in (getattr(entry, "content", []) or [])]
            for entry in tail_entries
        ],
    }


# Issue #164 — event types that count as "the agent made forward
# progress." Bumping ``_last_progress_at`` on these gives the ACTIVE-
# without-progress watchdog a single, robust signal that survives
# refactors of individual call sites: every progress event already calls
# ``_log()``. Each entry's value is the active-turn ``kind`` to record
# (``None`` means "leave kind alone").
_PROGRESS_EVENTS: dict[str, str | None] = {
    "wake": "wake",
    "tc_wake_continue": "wake",
    "llm_call": "llm_call",
    "llm_response": None,  # progress, but turn kind stays "llm_call"
    "tool_call": "tool_call",
    "tool_result": None,
    "notification_pair_injected": "notification_injection",
    "turn_cancelled_post_tool": None,
}


# ---------------------------------------------------------------------------
# Identity prompt section (curated prose)
# ---------------------------------------------------------------------------



def _build_identity_section(manifest_data: dict, mailbox_name: str | None = None) -> str:
    """Render the agent's identity as curated prose for the system prompt.

    Stable across turns (no transient runtime state) so it sits in the
    cacheable prefix without invalidating cache. The `state` field is
    explicitly omitted upstream — it changes every turn.

    Returns a markdown paragraph. Empty/missing fields are silently
    omitted so the prose stays clean for minimal manifests.
    """
    name = manifest_data.get("agent_name") or "(unnamed)"
    nickname = manifest_data.get("nickname") or ""
    agent_id = manifest_data.get("agent_id") or ""
    address = manifest_data.get("address") or ""
    created = manifest_data.get("created_at") or ""
    started = manifest_data.get("started_at") or ""
    admin = manifest_data.get("admin") or {}
    soul_delay = manifest_data.get("soul_delay")
    molt_count = manifest_data.get("molt_count", 0)

    lines: list[str] = []

    # Lead — name, nickname, id, address.
    lead = f"You are **{name}**"
    if nickname:
        lead += f" — \"{nickname}\""
    if agent_id:
        lead += f" (id `{agent_id}`)"
    lead += "."
    lines.append(lead)
    if address:
        lines.append(f"Your address is `{address}`.")

    # Origins — birth, awakening, molts.
    origins: list[str] = []
    if created:
        origins.append(f"born {created}")
    if started:
        origins.append(f"woken {started} for this session")
    if origins:
        lines.append("You were " + ", ".join(origins) + ".")
    if molt_count > 0:
        lines.append(
            f"You have undergone {molt_count} molt"
            f"{'s' if molt_count != 1 else ''} since birth."
        )

    # Admin role.
    if admin:
        flags = [k for k, v in admin.items() if v]
        if flags:
            if "nirvana" in flags:
                lines.append(
                    "You hold both **karma** and **nirvana** privileges — "
                    "you can manage and destroy other agents in this network."
                )
            elif "karma" in flags:
                lines.append(
                    "You hold **karma** privilege — "
                    "you can lull / suspend / cpr / clear other agents."
                )
            else:
                lines.append(f"You hold admin flags: {', '.join(flags)}.")

    # Resources.
    if soul_delay is not None:
        lines.append(f"Your soul flow fires {soul_delay}s after you go idle.")
    if mailbox_name:
        lines.append(f"You receive messages via {mailbox_name}.")

    # Runtime LLM identity — provider/model/endpoint as the agent runs.
    # Sourced from `manifest_data["llm"]` (sanitized at build time —
    # see identity.py `_safe_llm_from_service` and wrapper `Agent._build_manifest`).
    # Rendered as a single line so it sits in the cacheable prefix without
    # adding much weight; missing fields are silently skipped.
    llm = manifest_data.get("llm") or {}
    if isinstance(llm, dict):
        model = _identity_scalar(llm.get("model"))
        provider = _identity_scalar(llm.get("provider"))
        base_url = _identity_scalar(llm.get("base_url"))
        if provider or model:
            bits = []
            if model:
                bits.append(f"model `{model}`")
            if provider:
                bits.append(f"provider `{provider}`")
            if base_url:
                bits.append(f"endpoint `{base_url}`")
            if bits:
                lines.append("You are running on " + ", ".join(bits) + ".")

    # Active preset — only the wrapper agent has a preset surface, so this
    # block is silent for bare BaseAgent instances. Reports the active path
    # plus the default if the two differ (lets the agent see when it's on a
    # non-default preset). Allowed list is intentionally omitted from the
    # prompt — it's structural metadata, not identity prose.
    preset = manifest_data.get("preset") or {}
    if isinstance(preset, dict):
        active = _identity_scalar(preset.get("active"))
        default = _identity_scalar(preset.get("default"))
        if active:
            if default and default != active:
                lines.append(
                    f"Your active preset is `{active}` "
                    f"(default `{default}`)."
                )
            else:
                lines.append(f"Your active preset is `{active}`.")

    return "\n".join(lines)


def _identity_scalar(value) -> str:
    """Return prompt-safe scalar text for identity metadata, else empty string."""
    if isinstance(value, str):
        return value if value else ""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------


def _release_acquired_workdir_lease_on_init_failure(initializer: Callable) -> Callable:
    """Roll back a successfully acquired lease without hiding the boot error."""

    @functools.wraps(initializer)
    def guarded(self, *args, **kwargs):
        try:
            return initializer(self, *args, **kwargs)
        except BaseException:
            if getattr(self, "_workdir_lease_acquired", False):
                with contextlib.suppress(Exception):
                    self._workdir_lease.release()
                self._workdir_lease_acquired = False
            raise

    return guarded


class BaseAgent:
    """Generic research agent with intrinsic tools and MCP tool dispatch.

    Required dependencies:
        - ``workdir_lease`` (WorkdirLeasePort): Exclusive claim on the working
          directory, acquired at construction and released at teardown. It has no
          unlocked/no-op form — omitting it fails loudly at construction.
        - ``notification_store`` (NotificationStorePort): Persistence for
          ``.notification/`` channel mirrors. Required on every supported
          agent; there is no nullable/no-op path.
        - ``agent_presence`` (AgentPresenceStorePort): Own-heartbeat publish/
          withdraw and foreign-address presence observation, bound to this
          agent's working directory. Required and explicit; there is no
          nullable/no-op path and Core never constructs the concrete adapter.
        - ``lifecycle_clock`` (LifecycleClockPort): The two lifecycle time
          sources — wall-clock seconds for persisted/cross-process timestamps
          and ages, monotonic seconds for process-local elapsed intervals.
          Required and explicit; there is no default/no-op/optional path and
          Core never constructs the concrete adapter.
        - ``snapshot_port`` (SnapshotPort): Best-effort workdir initialization,
          capture, and maintenance used by lifecycle policy.
        - ``source_revision_port`` (SourceRevisionPort): Bounded running-source
          revision and tracked-dirty queries used by identity and drift policy.

    Conditionally required:
        - ``refresh_watcher`` (RefreshWatcherPort | None): Detached-process
          handoff for the generated relaunch watcher script, used by
          ``_perform_refresh`` after the ``.refresh``/``.refresh.taken``
          handshake completes. Composition roots (``lingtai.Agent``,
          ``lingtai.cli``) always inject the production adapter; there is no
          no-op watcher and Core never constructs the concrete adapter. A raw
          ``BaseAgent`` built without one (e.g. most non-refresh tests)
          constructs successfully — omitting it only fails loudly inside
          ``_perform_refresh``, and only once a real launch command exists,
          before any handshake or shutdown mutation. The no-launch-cmd path
          (``_build_launch_cmd()`` returns ``None``) works without it.

    Services (all optional):
        - ``service`` (LLMService): The brain — thinking, generating text.
        - ``file_io`` (FileIOService): File access — backs read/edit/write/glob/grep.
        - ``mail_service`` (MailTransportPort): Message transport — backs mail intrinsic.
        - ``event_journal`` (EventJournalPort): Durable structured event append.

    Missing service = intrinsics backed by it are auto-disabled.

    Subclasses customize behavior via:
        - ``_pre_request(msg)`` — transform message before LLM send
        - ``_post_request(msg, result)`` — side effects after LLM responds
        - ``_handle_message(msg)`` — message routing (must call super for processing)
        - ``_get_guard_limits()`` — per-agent loop guard limits
        - ``_PARALLEL_SAFE_TOOLS`` — set of tool names safe for concurrent execution
    """

    agent_type: str = ""

    # Tools safe for concurrent execution
    _PARALLEL_SAFE_TOOLS: set[str] = set()

    # Inbox polling interval (seconds)
    _inbox_timeout: float = 1.0

    @_release_acquired_workdir_lease_on_init_failure
    def __init__(
        self,
        service: LLMService,
        *,
        agent_name: str | None = None,
        working_dir: str | Path,
        workdir_lease: WorkdirLeasePort,
        notification_store: "NotificationStorePort",
        agent_presence: AgentPresenceStorePort,
        lifecycle_clock: LifecycleClockPort,
        snapshot_port: SnapshotPort,
        source_revision_port: SourceRevisionPort,
        refresh_watcher: RefreshWatcherPort | None = None,
        intrinsics: "Mapping[str, Mapping[str, Any]] | None" = None,
        file_io: Any | None = None,
        mail_service: Any | None = None,
        event_journal: EventJournalPort | None = None,
        config: AgentConfig | None = None,
        context: Any = None,
        admin: dict | None = None,
        streaming: bool = False,
        covenant: str = "",
        principle: str = "",
        substrate: str = "",
        procedures: str = "",
        brief: str = "",
        pad: str = "",
        comment: str = "",
    ):
        self.agent_name = agent_name  # true name (真名) — immutable once set
        self.nickname: str | None = None  # mutable alias (别名)
        self.service = service
        self._config = config or AgentConfig()
        # Preset-loader hook: Agent wrapper composes it; None on a bare BaseAgent so `load_preset` fails loud.
        self._preset_loader: Callable[..., dict] | None = None
        self._context = context
        self._admin = admin or {}
        # Core receives the lifecycle clock as a required Port and binds it
        # before the first monotonic/wall sample below. Core never imports or
        # constructs the concrete adapter; the wall/monotonic domains stay
        # distinct (see kernel/lifecycle_clock/CONTRACT.md).
        self._lifecycle_clock = lifecycle_clock
        self._cancel_event = threading.Event()
        self._state = AgentState.IDLE
        self._idle_since_monotonic: float | None = self._lifecycle_clock.monotonic_seconds()
        self._started_at: str = ""
        self._last_usage = None  # UsageMetadata from last LLM call, for ledger
        self._created_at: str = ""
        self._uptime_anchor: float | None = None  # set in start(), None means not started
        # Core receives both snapshot/revision capabilities as required Ports.
        self._snapshot_port = snapshot_port
        self._source_revision_port = source_revision_port
        # Core receives the refresh-watcher Port; the concrete detached-process
        # mechanism (a POSIX subprocess adapter today) is composed outside.
        # There is no no-op fallback, but construction itself does not require
        # it: composition roots always inject the production adapter, while a
        # raw BaseAgent (most non-refresh tests) may omit it and construct
        # successfully. `_perform_refresh` fails loudly if it is absent, but
        # only once a real launch command exists and before any handshake or
        # shutdown mutation (see kernel/refresh_watcher/CONTRACT.md).
        self._refresh_watcher = refresh_watcher
        self._runtime_identity_event_fields = runtime_identity_event_fields(
            self._source_revision_port
        )

        # Working directory (caller-owned path)
        self._workdir = WorkingDir(working_dir)
        self._working_dir = self._workdir.path

        # Core receives the journal Port; concrete storage is composed outside.
        self._event_journal = event_journal

        # Core receives the workdir-lease Port; the concrete exclusion mechanism
        # (a POSIX flock today) is composed outside. This is a required, explicit
        # dependency: there is no unlocked or no-op fallback.
        self._workdir_lease = workdir_lease

        # Acquire the working-directory lease (10s grace for prior process
        # cleanup) through the injected Port.
        self._workdir_lease_acquired = False
        self._workdir_lease.acquire(10)
        self._workdir_lease_acquired = True

        # Core receives the notification-store Port; the concrete persistence
        # mechanism (a POSIX filesystem adapter today) is composed outside.
        # This is a required, explicit dependency: there is no no-op fallback.
        self._notification_store = notification_store

        # Core receives the agent-presence Port bound to this working directory;
        # the concrete filesystem mechanism (a POSIX .agent.json/.agent.heartbeat
        # adapter today) is composed outside. The heartbeat loop publishes and
        # teardown withdraws liveness through it. Required and explicit: there is
        # no no-op fallback and Core never constructs the concrete adapter.
        self._agent_presence = agent_presence

        # --- Wire services ---
        # FileIOService: optional, provided by Agent or host
        self._file_io = file_io

        # MailService: None means mail intrinsic disabled
        self._mail_service = mail_service

        # Covenant, principle, substrate, procedures, brief, and pad file paths
        system_dir = self._working_dir / "system"
        pad_file = system_dir / "pad.md"
        covenant_file = system_dir / "covenant.md"
        principle_file = system_dir / "principle.md"
        substrate_file = system_dir / "substrate.md"
        procedures_file = system_dir / "procedures.md"
        brief_file = system_dir / "brief.md"

        system_dir.mkdir(exist_ok=True)

        # The kernel-owned section mirrors (principle/substrate/procedures) may
        # carry skill-style YAML frontmatter on disk — developer-facing metadata
        # that must never reach the LLM prompt. Strip it on read so the section
        # the prompt manager renders is body-only. Covenant mirrors are operator
        # content with no frontmatter, but stripping is a no-op there too.
        from .._frontmatter import strip_frontmatter as _strip_frontmatter

        # Covenant: constructor value wins, then fall back to file on disk
        if covenant:
            covenant_file.write_text(covenant)
        elif covenant_file.is_file():
            covenant = _strip_frontmatter(covenant_file.read_text(encoding="utf-8"))

        # Principle: constructor value wins, then fall back to file on disk
        if principle:
            principle_file.write_text(principle)
        elif principle_file.is_file():
            principle = _strip_frontmatter(principle_file.read_text(encoding="utf-8"))

        # Substrate: lower-level BaseAgent seed/fallback. The init.json
        # contract is enforced by lingtai.agent.Agent, where substrate is
        # kernel-owned and not an external override.
        if substrate:
            substrate_file.write_text(substrate)
        elif substrate_file.is_file():
            substrate = _strip_frontmatter(substrate_file.read_text(encoding="utf-8"))

        # Procedures: same pattern as covenant/principle
        if procedures:
            procedures_file.write_text(procedures)
        elif procedures_file.is_file():
            procedures = _strip_frontmatter(procedures_file.read_text(encoding="utf-8"))

        # Brief: disk-owned context (normally written by secretary/briefing
        # flows). Init.json brief overrides are retired at the Agent wrapper.
        if brief and not brief_file.is_file():
            brief_file.write_text(brief)
        elif brief_file.is_file():
            brief = brief_file.read_text(encoding="utf-8")

        # Pad: constructor value seeds the file if it doesn't exist
        if pad and not pad_file.is_file():
            pad_file.write_text(pad)

        # Auto-load pad from file into prompt manager
        loaded_pad = ""
        if pad_file.is_file():
            loaded_pad = pad_file.read_text(encoding="utf-8")

        # System prompt manager
        self._prompt_manager = SystemPromptManager()
        if principle:
            self._prompt_manager.write_section("principle", principle, protected=True)
        if covenant:
            self._prompt_manager.write_section("covenant", covenant, protected=True)
        if substrate:
            self._prompt_manager.write_section("substrate", substrate, protected=True)
        if procedures:
            self._prompt_manager.write_section("procedures", procedures, protected=True)
        if brief:
            self._prompt_manager.write_section("brief", brief, protected=True)
        # Load existing rules from system/rules.md (survives molts, refreshes, and resumes)
        rules_md = system_dir / "rules.md"
        if rules_md.is_file():
            try:
                rules_content = rules_md.read_text(encoding="utf-8").strip()
                if rules_content:
                    self._prompt_manager.write_section("rules", rules_content, protected=True)
            except OSError:
                pass
        if loaded_pad.strip():
            self._prompt_manager.write_section("pad", loaded_pad)
        if comment:
            self._prompt_manager.write_section("comment", comment)

        # Soul delay — needed before manifest build
        self._soul_delay = max(1.0, self._config.soul_delay)

        # Agent ID, created_at, and molt_count — persistent state restored
        from datetime import datetime, timezone
        import secrets
        existing = self._workdir.read_full_manifest()
        self._agent_id: str = existing.get("agent_id", "")
        self._created_at: str = existing.get("created_at", "")
        self._molt_count: int = existing.get("molt_count", 0)
        if not self._agent_id or not self._created_at:
            now = datetime.now(timezone.utc)
            if not self._agent_id:
                self._agent_id = now.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            if not self._created_at:
                self._created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Write manifest — identity + construction recipe (no runtime state)
        self._started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        from .identity import _build_manifest
        manifest_data = _build_manifest(self)
        self._workdir.write_manifest(manifest_data)

        # Auto-inject identity into system prompt from manifest
        self._prompt_manager.write_section(
            "identity",
            _build_identity_section(
                manifest_data,
                mailbox_name=getattr(self, "_mailbox_name", None),
            ),
            protected=True,
        )

        self._nap_wake = threading.Event()  # signalled to wake nap early
        self._nap_wake_reason = ""  # why the nap was woken

        # Mailbox identity — capabilities override these to change notification text.
        self._mailbox_name = "email box"
        self._mailbox_tool = "email"

        # Non-intrinsic tool handlers (capabilities, MCP, add_tool)
        self._tool_handlers: dict[str, Callable[[dict], dict]] = {}
        self._tool_schemas: list[FunctionSchema] = []

        # --- Wire intrinsic tools ---
        # Intrinsics are injected by the composing layer (``lingtai.Agent``
        # passes ``lingtai.tools.registry.INTRINSICS``). The kernel owns the tool
        # machinery, not the concrete tools: a bare ``BaseAgent`` with no
        # intrinsics is legal and intentional — it is pure machinery with an
        # empty tool surface. ``_intrinsic_modules`` maps name → the intrinsic
        # module (used by schema build / dispatch / boot / kernel hook lookup);
        # ``_intrinsics`` maps name → the bound handler closure.
        self._intrinsic_registry: Mapping[str, Mapping[str, Any]] = intrinsics or {}
        self._intrinsic_modules: dict[str, Any] = {}
        self._intrinsics: dict[str, Callable[[dict], dict]] = {}
        self._wire_intrinsics()

        # Inbox — text-channel notifications (mail, daemon, user input)
        self.inbox: queue.Queue[Message] = queue.Queue()

        # Involuntary tool-call inbox
        self._tc_inbox: TCInbox = TCInbox()

        # Tracks the most recent in-history call_id for each "single-slot" source.
        self._appendix_ids_by_source: dict[str, str] = {}

        # _pending_mail_notifications removed — email arrivals now use
        # single-slot unread-digest (email.unread) instead of per-arrival
        # notification pairs. Bounce/MCP/soul events publish their own
        # `.notification/*.json` files and don't need per-ref tracking.

        # LLM worker poison state. Set when WorkerStillRunningError means the
        # current in-memory ChatInterface may still be mutated by a worker
        # thread. Process-local only; refresh/relaunch restores from disk.
        self._llm_worker_interface_poisoned: bool = False
        self._llm_worker_poison_reason: str | None = None
        self._llm_worker_poison_artifact: str | None = None
        self._llm_worker_poisoned_at: str | None = None
        self._llm_worker_poison_turn_entry: str | None = None
        self._llm_worker_refresh_requested: bool = False
        self._llm_worker_refresh_source: str | None = None

        # Notification sync state (filesystem-as-protocol redesign).
        # _notification_fp: last-seen `.notification/` fingerprint for
        #   change-detection between heartbeat ticks.
        # _notification_block_id: call_id of the most recently injected
        #   synthesized pair — kept for informational/molt-reset purposes;
        #   no longer used for remove_pair_by_call_id (pairs are now
        #   skeletonized in-place, not deleted).
        # See notifications.py and notification-filesystem-redesign.md.
        self._notification_fp: tuple = ()
        # System-channel RMW serialization is owned by the injected
        # NotificationStorePort through compare_update_channel.
        # Last ACTIVE-state notification fingerprint that has already emitted
        # ``notification_deferred_active``.  This is intentionally separate
        # from ``_notification_fp``: ACTIVE must keep the delivery fingerprint
        # uncommitted so the next IDLE boundary retries, but the log should not
        # repeat the same status echo on every heartbeat.
        self._notification_deferred_log_fp: tuple = ()
        self._notification_block_id: str | None = None
        # Monotonic counter ensuring every synthesized notification pair
        # carries unique tokens (timestamp + seq) even when the underlying
        # payload repeats — defeats DeepSeek's cache fast-path empty-completion
        # failure mode on byte-identical synthetic pairs.
        self._notification_inject_seq: int = 0
        # Unified live notification holder — points to whichever dict
        # currently carries the live notification payload.  May be:
        #   * a normal tool-result content dict (ACTIVE path), or
        #   * a synthesized pair's result content dict (IDLE path).
        # Only ONE holder exists at a time.  When a new holder is
        # registered, the old one is skeletonized in-place so history
        # never accumulates stale notification data across results.
        # See `meta_block.skeletonize_notification_holder` and
        # `meta_block.attach_active_notifications`.
        #
        # The notification payload is SPARSE / update-driven (mirrors the #618
        # `agent_meta` cadence), not latest-result-only: while notifications stay
        # active but their material content is unchanged, the payload is NOT
        # chased onto every newest tool result — the prior holder keeps it.  It
        # only moves/re-stamps when the notification payload materially changes,
        # or when the target is a deliberate `notification(action="check")` read.
        self._notification_live_holder: dict | None = None
        # Material signature of the last emitted `_meta.notifications` payload;
        # the change gate for the sparse notification attach above.  ``None``
        # until the first active payload, and reset to ``None`` whenever
        # notifications go empty so a later reappearance attaches afresh.
        self._notification_payload_signature: str | None = None
        # Per-IM-channel persistent communication-context lane.  These IDs
        # track which messages have already been emitted in
        # `_meta.notification_persistent.mcp.<channel>.messages` for the
        # current provider-visible context, so later deliveries can be deltas
        # with a `previous_block` hook pointing back to the previous block.
        # Reset on context molt. Snapshot-only IM lanes (currently WhatsApp) do
        # not keep agent-side delivery state.
        self._notification_persistent_telegram_message_ids: list[str] = []
        self._notification_persistent_telegram_last_tool_id: str | None = None
        self._notification_persistent_wechat_message_ids: list[str] = []
        self._notification_persistent_wechat_last_tool_id: str | None = None
        self._notification_persistent_feishu_message_ids: list[str] = []
        self._notification_persistent_feishu_last_tool_id: str | None = None

        # Telegram Task Card turn-local context (kernel-driven route B).
        # Set when a Telegram notification wakes the agent; cleared at turn end.
        # None → no-op for non-Telegram turns.
        self._telegram_task_card_context: dict | None = None

        # Provider-visible tool result currently carrying the live `_meta.agent_meta`
        # / `_meta.guidance` blocks (kernel runtime state + guidance ref).
        # `agent_meta` is SPARSE / update-driven, not latest-result-only: it is
        # (re)attached only when the material agent snapshot changes since the
        # last emitted `agent_meta` (tracked by `_agent_meta_signature`).  When
        # the snapshot is unchanged it is NOT chased onto the newest result; the
        # prior holder keeps it as a historical update point.  When it changes,
        # the prior *live* holder sheds its blocks and the newer result takes
        # over.  See `meta_block.attach_active_runtime` / `agent_meta_signature`.
        self._runtime_live_holder: dict | None = None
        # Material signature of the last emitted `_meta.agent_meta`; the change
        # gate for the sparse attach above.  ``None`` until the first snapshot.
        self._agent_meta_signature: str | None = None

        # Large-result hint threshold (chars).  When a main-agent tool result's
        # serialized length exceeds this value it is treated as "large": the
        # ToolExecutor stamps a tool_meta.comment.overflow hint, and the result
        # is surfaced for summarization through
        # _meta.agent_meta.current_tool_result_chars.top_results.  Large results
        # no longer raise a `large_tool_result` system notification — see
        # meta_block.current_tool_result_chars and _maybe_notify_large_tool_result.
        # Default: 3000 chars.  Configurable only via manifest.summarize_notification_threshold
        # in init.json + refresh — runtime mutation is not supported.
        self._summarize_notification_threshold: int = 3000

        # Lifecycle
        self._shutdown = threading.Event()
        self._asleep = threading.Event()   # set when entering ASLEEP; cleared on wake
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._state = AgentState.IDLE
        self._sealed = False

        # Soul — inner voice
        self._soul_prompt = ""       # non-empty during inquiry
        self._soul_oneshot = False    # True during pending inquiry
        self._soul_timer: threading.Timer | None = None
        # Held while a soul flow consultation fire is running. Voluntary
        # soul(action='flow') calls try-acquire non-blocking — if held,
        # the call is rejected with "soul flow ongoing".
        self._soul_fire_lock: threading.Lock = threading.Lock()
        self._insight_turn_counter: int = 0

        # Heartbeat — always-on health monitor
        self._heartbeat: float = 0.0
        self._heartbeat_thread: threading.Thread | None = None
        # Final-stop signal for the heartbeat cadence. It stays distinct from
        # _shutdown because heartbeat remains live throughout teardown.
        self._heartbeat_stop = threading.Event()
        self._aed_start: float | None = None

        # Issue #164 — ACTIVE-without-progress watchdog.
        #
        # ``_state_changed_at`` records when the agent last transitioned
        # state (wall-clock seconds, ``self._lifecycle_clock.wall_seconds()``).
        # ``_last_progress_at``
        # is bumped by any of the kernel's progress events — ``wake``,
        # ``tc_wake_continue``, ``llm_call``, ``llm_response``, ``tool_call``,
        # ``tool_result``, ``notification_pair_injected``, and state
        # transitions themselves. The heartbeat tick reads both: when
        # ``state == ACTIVE`` and no progress event has fired for longer
        # than ``LINGTAI_ACTIVE_STUCK_THRESHOLD_S`` (default 600s, ~10min),
        # we log ``active_without_progress`` once per condition so the
        # symptom Jason reported (ACTIVE wedged + notification_deferred
        # storm with no turn ever starting) is diagnosable from the event
        # log instead of requiring forensic cross-referencing.
        #
        # The watchdog deliberately does NOT auto-restart the agent — the
        # safest action across the failure modes we've seen is "make it
        # visible and let admin or .clear handle recovery." Auto-restart
        # without understanding the underlying race could mask real bugs
        # behind retries.
        now_wall = self._lifecycle_clock.wall_seconds()
        self._state_changed_at: float = now_wall
        self._last_progress_at: float = now_wall
        self._active_turn_kind: str | None = None
        self._active_turn_started_at: float | None = None
        self._active_turn_id: str | None = None
        #: Counts repeated ``notification_deferred_active`` events since
        #: the last successful injection. Reset on
        #: ``notification_pair_injected``. Surfaced in ``.status.json`` so
        #: the deferral storm in #164 shows up before the user notices.
        self._deferred_notifications_count: int = 0
        self._deferred_notifications_oldest_at: float | None = None
        #: One-shot latch so the watchdog logs exactly once per stuck
        #: episode. Cleared on any state transition out of ACTIVE.
        self._active_stuck_logged: bool = False

        # Snapshot — periodic git commits (Time Machine)
        self._last_snapshot: float = 0.0
        self._last_gc: float = 0.0

        # Auto-fallback state
        self._preset_fallback_attempted = False

        # Sent message tracker — dedup + idle-after-send for external channels
        from ..sent_message_tracker import SentMessageTracker
        self._sent_tracker = SentMessageTracker()

        # Session manager — LLM session, token tracking, compaction
        self._session = SessionManager(
            llm_service=service,
            config=self._config,
            agent_name=agent_name,
            streaming=streaming,
            build_system_prompt_fn=self._build_system_prompt,
            build_tool_schemas_fn=self._build_tool_schemas,
            logger_fn=self._log,
            build_system_batches_fn=self._build_system_prompt_batches,
            tool_result_recovery_lookup_fn=self._recover_pending_tool_result,
        )

        # Boot intrinsics that define an optional ``boot(agent)`` hook. Order
        # follows the injected registry; the two intrinsics that historically
        # booted (psyche, email) both define ``boot`` and run here without
        # name special-casing. Absent-intrinsic = nothing to boot.
        for name in self._intrinsics:
            module = self._intrinsic_modules.get(name)
            boot_fn = getattr(module, "boot", None) if module is not None else None
            if boot_fn is not None:
                boot_fn(self)

    # ------------------------------------------------------------------
    # Intrinsic wiring
    # ------------------------------------------------------------------

    def _wire_intrinsics(self) -> None:
        """Wire injected intrinsic tool handlers onto the tool surface.

        Iterates the registry injected at construction (``intrinsics=`` — the
        composing layer passes ``lingtai.tools.registry.INTRINSICS``). Each value has
        the shape ``{"module": <module>}``. ``_intrinsic_modules`` keeps the
        module for schema/description/boot/kernel-hook lookup; ``_intrinsics``
        holds the bound handler closure the dispatcher calls.
        """
        for name, info in self._intrinsic_registry.items():
            module = info["module"]
            self._intrinsic_modules[name] = module
            handle_fn = module.handle
            self._intrinsics[name] = lambda args, fn=handle_fn: fn(self, args)

    def _intrinsic_hook(self, intrinsic: str, name: str):
        """Resolve a kernel-facing hook function from an injected intrinsic.

        The kernel used to reach into intrinsic modules by import (e.g.
        ``from ..intrinsics.soul.flow import _start_soul_timer``). After the
        tools consolidation the kernel cannot import ``tools``, so every such
        touchpoint resolves through the injected registry instead: the
        intrinsic package re-exports its kernel-facing functions from its
        package ``__init__`` as its documented hook surface.

        Returns the bound function, or ``None`` when the intrinsic is absent
        (bare ``BaseAgent``) or does not export the hook — callers no-op.
        """
        module = self._intrinsic_modules.get(intrinsic)
        if module is None:
            return None
        return getattr(module, name, None)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_idle(self) -> bool:
        return self._idle.is_set()

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def agent_id(self) -> str:
        """Permanent birth certificate — never changes across restarts or moves."""
        return self._agent_id

    @property
    def working_dir(self) -> Path:
        """The agent's working directory."""
        return self._workdir.path

    @property
    def _chat(self) -> Any:
        """Proxy to SessionManager's chat session."""
        return self._session.chat

    @_chat.setter
    def _chat(self, value: Any) -> None:
        self._session.chat = value

    @property
    def _streaming(self) -> bool:
        """Proxy to SessionManager's streaming flag."""
        return self._session.streaming

    @property
    def _token_decomp_dirty(self) -> bool:
        """Proxy to SessionManager's token decomp dirty flag."""
        return self._session.token_decomp_dirty

    @_token_decomp_dirty.setter
    def _token_decomp_dirty(self, value: bool) -> None:
        self._session.token_decomp_dirty = value

    @property
    def _interaction_id(self) -> str | None:
        """Proxy to SessionManager's interaction ID."""
        return self._session.interaction_id

    @_interaction_id.setter
    def _interaction_id(self, value: str | None) -> None:
        self._session.interaction_id = value

    @property
    def _intermediate_text_streamed(self) -> bool:
        """Proxy to SessionManager's intermediate text streamed flag."""
        return self._session.intermediate_text_streamed

    @_intermediate_text_streamed.setter
    def _intermediate_text_streamed(self, value: bool) -> None:
        self._session.intermediate_text_streamed = value

    # ------------------------------------------------------------------
    # Naming (pass-throughs to identity.py)
    # ------------------------------------------------------------------

    def set_name(self, name: str) -> None:
        from .identity import _set_name
        _set_name(self, name)

    def set_nickname(self, nickname: str) -> None:
        from .identity import _set_nickname
        _set_nickname(self, nickname)

    def _update_identity(self) -> None:
        from .identity import _update_identity
        _update_identity(self)

    # ------------------------------------------------------------------
    # Lifecycle (pass-throughs to lifecycle.py + direct methods)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the agent's main loop thread."""
        from .lifecycle import _start
        _start(self)

    def _reset_uptime(self) -> None:
        """Reset the uptime anchor for runtime uptime tracking."""
        from .lifecycle import _reset_uptime
        _reset_uptime(self)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal shutdown and wait for the agent thread to exit."""
        from .lifecycle import _stop
        _stop(self, timeout)

    def _set_state(self, new_state: AgentState, reason: str = "") -> None:
        """Transition to a new state.

        Drives the soul cadence timer: the timer runs only while the
        agent is IDLE.  Entering IDLE starts a fresh ``soul_delay``-second
        timer; leaving IDLE (to ACTIVE, STUCK, ASLEEP, or SUSPENDED)
        cancels it.  The timer does NOT reschedule itself after firing —
        the next IDLE transition starts a fresh countdown.
        """
        _start_soul_timer = self._intrinsic_hook("soul", "_start_soul_timer")
        _cancel_soul_timer = self._intrinsic_hook("soul", "_cancel_soul_timer")

        old = self._state
        if old == new_state:
            return
        self._state = new_state
        if new_state == AgentState.ACTIVE:
            self._idle.clear()
        else:
            self._idle.set()

        # Soul timer + hidden idle-timeout bookkeeping: IDLE-only.  Start on
        # entering IDLE, cancel/clear on leaving. No-op when soul is absent.
        if new_state == AgentState.IDLE:
            self._idle_since_monotonic = self._lifecycle_clock.monotonic_seconds()
            if _start_soul_timer is not None:
                _start_soul_timer(self)
        elif old == AgentState.IDLE:
            self._idle_since_monotonic = None
            if _cancel_soul_timer is not None:
                _cancel_soul_timer(self)

        # Issue #164 — watchdog bookkeeping. A state transition is itself
        # forward progress, so reset the no-progress clock. The
        # one-shot stuck-logged latch is cleared whenever we leave ACTIVE
        # so the next stuck episode can be reported.
        now_wall = self._lifecycle_clock.wall_seconds()
        self._state_changed_at = now_wall
        self._last_progress_at = now_wall
        if new_state == AgentState.ACTIVE:
            # The kernel doesn't know yet what kind of turn this will be —
            # the next progress event (``wake``, ``tc_wake_continue``,
            # ``llm_call``, ``tool_call``) refines this. We seed with a
            # "pending" marker so .status.json never claims a turn is
            # already in flight when only the state flipped.
            self._active_turn_kind = "pending"
            self._active_turn_started_at = now_wall
            self._active_turn_id = None
        else:
            self._active_turn_kind = None
            self._active_turn_started_at = None
            self._active_turn_id = None
            self._active_stuck_logged = False

        self._log("agent_state", old=old.value, new=new_state.value, reason=reason)
        self._workdir.write_manifest(self._build_manifest())

    def _wake_nap(self, reason: str) -> None:
        """Signal the nap to wake up with a given reason."""
        self._nap_wake_reason = reason
        self._nap_wake.set()

    def _note_notification_deferred_active(self, fp: tuple, *, sources: list[str]) -> None:
        """Record ACTIVE notification deferral without per-heartbeat log spam.

        ACTIVE deliberately leaves ``_notification_fp`` uncommitted so delivery
        retries at the next IDLE boundary.  Heartbeat ticks therefore rediscover
        the same filesystem fingerprint.  Keep watchdog counters accurate for
        every tick, but emit ``notification_deferred_active`` only once per
        distinct notification fingerprint.
        """
        self._deferred_notifications_count += 1
        if self._deferred_notifications_oldest_at is None:
            self._deferred_notifications_oldest_at = self._lifecycle_clock.wall_seconds()

        if fp == getattr(self, "_notification_deferred_log_fp", ()):
            return

        self._log(
            "notification_deferred_active",
            sources=sources,
            _deferred_counter_already_updated=True,
        )
        self._notification_deferred_log_fp = fp

    def _log(self, event_type: str, **fields) -> None:
        """Write a structured event to the logging service, if configured.

        Also updates issue #164 watchdog bookkeeping: known progress
        events bump ``_last_progress_at`` and may refine the active-turn
        kind/id, and ``notification_deferred_active`` events update the
        deferred-notification counters.
        """
        deferred_counter_already_updated = bool(
            fields.pop("_deferred_counter_already_updated", False)
        )

        # Watchdog bookkeeping — done before the actual log write so the
        # bookkeeping is in place even if the log service raises.
        if event_type in _PROGRESS_EVENTS:
            self._last_progress_at = self._lifecycle_clock.wall_seconds()
            kind = _PROGRESS_EVENTS[event_type]
            if kind is not None:
                self._active_turn_kind = kind
                self._active_turn_started_at = self._last_progress_at
            # ToolExecutor emits provider IDs as tool_call_id; older/manual
            # event producers may still use call_id. Surface either one so
            # status snapshots can tie back to events.jsonl.
            call_id = fields.get("tool_call_id") or fields.get("call_id")
            if isinstance(call_id, str):
                self._active_turn_id = call_id
        elif event_type == "notification_deferred_active":
            if not deferred_counter_already_updated:
                self._deferred_notifications_count += 1
                if self._deferred_notifications_oldest_at is None:
                    self._deferred_notifications_oldest_at = self._lifecycle_clock.wall_seconds()
        elif event_type == "agent_state":
            # Successful injection / state transitions reset the deferral
            # storm counter — the very next state change after a deferral
            # storm is exactly the recovery signal we want to note.
            if self._deferred_notifications_count:
                self._deferred_notifications_count = 0
                self._deferred_notifications_oldest_at = None

        if self._event_journal is not None:
            self._event_journal.append({
                "type": event_type,
                "address": self._working_dir.name,
                "agent_name": self.agent_name,
                "ts": self._lifecycle_clock.wall_seconds(),
                **self._runtime_identity_event_fields,
                **fields,
            })

    def wake(self, reason: str) -> None:
        """Wake the agent from nap. Call when external input arrives."""
        self._wake_nap(reason)

    def log(self, event_type: str, **fields) -> None:
        """Write a structured event to the agent's event log."""
        self._log(event_type, **fields)

    # ------------------------------------------------------------------
    # Public addon API (pass-throughs)
    # ------------------------------------------------------------------

    def _on_mail_received(self, payload: dict) -> None:
        from .messaging import _on_mail_received
        _on_mail_received(self, payload)

    def _on_normal_mail(self, payload: dict) -> None:
        from .messaging import _on_normal_mail
        _on_normal_mail(self, payload)

    def _enqueue_system_notification(
        self,
        *,
        source: str,
        ref_id: str,
        body: str,
        skip_if_ref_id_exists: bool = False,
        idempotency_key: str | None = None,
        skip_if_idempotency_key_exists: bool = False,
        priority: str = "normal",
        extra: dict | None = None,
    ) -> str:
        from .messaging import _enqueue_system_notification
        return _enqueue_system_notification(
            self,
            source=source,
            ref_id=ref_id,
            body=body,
            skip_if_ref_id_exists=skip_if_ref_id_exists,
            idempotency_key=idempotency_key,
            skip_if_idempotency_key_exists=skip_if_idempotency_key_exists,
            priority=priority,
            extra=extra,
        )

    def notify(self, sender: str, text: str) -> None:
        from .messaging import _notify
        _notify(self, sender, text)

    def _rescan_large_tool_results(self) -> int:
        from .messaging import _rescan_large_tool_results
        return _rescan_large_tool_results(self)

    # ------------------------------------------------------------------
    # Soul (pass-throughs to soul_flow.py)
    # ------------------------------------------------------------------

    def _start_soul_timer(self) -> None:
        fn = self._intrinsic_hook("soul", "_start_soul_timer")
        if fn is not None:
            fn(self)

    def _cancel_soul_timer(self) -> None:
        fn = self._intrinsic_hook("soul", "_cancel_soul_timer")
        if fn is not None:
            fn(self)

    def _soul_whisper(self) -> None:
        fn = self._intrinsic_hook("soul", "_soul_whisper")
        if fn is not None:
            fn(self)

    def _drain_tc_inbox(self) -> None:
        """Splice queued involuntary tool-call pairs at a safe boundary.

        Also (re)installs the pre-request drain hook on the active chat
        session — see :meth:`_install_drain_hook` for the rationale.
        Called from two paths today: the entry drain at request start
        (``base_agent/turn.py:_handle_request``) and the dedicated TC
        wake handler (``_handle_tc_wake``). The pre-request hook itself
        adds a third path: drain fires once per LLM round-trip inside
        the tool-call loop, so mail notifications and soul.flow voices
        splice into the wire mid-task instead of waiting for the outer
        turn to end.
        """
        from .worker_recovery import is_worker_interface_poisoned
        if is_worker_interface_poisoned(self):
            self._log(
                "tc_inbox_drain_skipped_poisoned_interface",
                artifact=getattr(self, "_llm_worker_poison_artifact", None),
            )
            return
        if self._chat is None:
            try:
                self._session.ensure_session()
            except Exception:
                return
        # Idempotent — re-installing the same hook on the same session
        # is a no-op. Cheap to call on every drain so a session created
        # via _rebuild_session (AED recovery) gets the hook automatically
        # without the AED path needing to know about it.
        self._install_drain_hook()
        result = self._tc_inbox.drain_into(
            self._chat.interface,
            self._appendix_ids_by_source,
        )
        if result.count > 0:
            self._log("tc_inbox_drain", count=result.count, sources=result.sources)
            self._save_chat_history()

    def _install_drain_hook(self) -> None:
        """Install the mid-turn tc_inbox drain hook on the active chat session.

        The hook fires inside each adapter's ``send()`` after the message
        has been committed to the canonical ChatInterface but before the
        API call — at that moment the wire tail is ``user[tool_results]``
        or ``user[text]``, so ``has_pending_tool_calls()`` returns False
        and the splicer can safely append a new ``(call, result)`` pair.

        Wire-state semantic, in two regimes:

        * **Canonical-interface adapters** (anthropic, openai-CC,
          codex-Responses, deepseek): the hook splices into the same
          interface the adapter is about to serialize for the wire, so
          the spliced pair appears in the *current* API request.
          Mail notifications enqueued during a long bash chain reach
          the LLM within one tool round.

        * **Server-state adapters** (OpenAIResponsesSession, both
          GeminiChatSession and InteractionsChatSession): the hook
          splices into the canonical interface, but the wire payload
          for the current request is built from server-side state
          (``previous_response_id`` / ``previous_interaction_id``) or
          the genai SDK's own chat history. The spliced pair is only
          visible to the LLM on the *next* turn after the agent
          re-syncs. The agent-side persistence and inspection paths
          (chat_history.jsonl, .status.json, /codex view) update
          immediately either way.

        Subtle semantic for ``replace_in_history=True`` (soul.flow):
        when the hook fires mid-turn, splicing in a replacement pair
        removes the prior pair of the same source from the interface.
        This is *almost* identical to the turn-boundary behavior that
        already exists today, with one nuance: the LLM's reasoning in
        the *current* turn was conditioned on a wire that contained
        the prior pair, but its next API call (or its in-flight
        reasoning continuation) may serialize a wire that doesn't.
        For soul.flow's reflective voices this is harmless — they
        don't drive tool calls and the model isn't building a chain
        of reasoning that depends on the prior voice's exact text.
        For any future producer that uses ``replace_in_history=True``
        with content the agent might cite mid-turn, this is a
        consideration; flagged here rather than buried in commit
        history.

        Idempotent: re-assigning the same callable to the same session
        attribute is a no-op. Called from :meth:`_drain_tc_inbox` so
        sessions created via ``_rebuild_session`` (AED recovery) pick
        up the hook on the next drain without a separate code path.
        """
        if self._chat is None:
            return
        if not hasattr(self._chat, "pre_request_hook"):
            return
        # Bind via lambda so the hook captures self, not the chat session.
        # The drain method itself rebinds to self._chat.interface, so the
        # hook ignores the interface argument the adapter passes in.
        self._chat.pre_request_hook = lambda _iface: self._drain_tc_inbox_for_hook()

    def _drain_tc_inbox_for_hook(self) -> None:
        """Hook-callable variant of _drain_tc_inbox without re-installing.

        The pre-request hook is called from inside an adapter's send(),
        which means we're already inside a session.send() call. Calling
        the full _drain_tc_inbox would try to re-install the hook (cheap
        but pointless) and could in pathological cases recurse if a
        future producer enqueues during drain. This variant just splices
        and returns.
        """
        from .worker_recovery import is_worker_interface_poisoned
        if is_worker_interface_poisoned(self):
            self._log(
                "tc_inbox_drain_skipped_poisoned_interface",
                artifact=getattr(self, "_llm_worker_poison_artifact", None),
                from_hook=True,
            )
            return
        if self._chat is None:
            return
        result = self._tc_inbox.drain_into(
            self._chat.interface,
            self._appendix_ids_by_source,
        )
        if result.count > 0:
            self._log(
                "tc_inbox_drain",
                count=result.count,
                sources=result.sources,
                from_hook=True,
            )
            self._save_chat_history()

    # ------------------------------------------------------------------
    # Notification sync — filesystem-as-protocol replacement for tc_inbox.
    # See notifications.py for the notification filesystem design rationale.
    # ------------------------------------------------------------------

    def _sync_notifications(self) -> None:
        """Sync `.notification/` state into the wire.

        Computes the current fingerprint; if unchanged, no-op.  On change:
        1. Skeletonize the current live holder (if any) in-place — does NOT
           remove synthesized pairs from history.  Synthesized pairs are kept
           as placeholder skeletons; only normal tool-result dicts have their
           notification keys stripped.
        2. If the new collection is empty, commit the empty fingerprint and
           return.
        3. Otherwise, inject a new block appropriate for current state:

           * IDLE → splice ``(call, result)`` pair (impersonates a
             voluntary ``notification(action="check")`` call from the
             agent's perspective), post ``MSG_TC_WAKE`` so the run loop
             unblocks and ``_handle_tc_wake`` drives the next inference
             round off the existing wire — no fake user input, no meta
             prefix.
           * ACTIVE → defer without touching the wire or committing the
             fingerprint; the next IDLE boundary retries delivery via
             the ordinary synthetic pair path.
           * ASLEEP → wake to IDLE, splice the pair, post
             ``MSG_TC_WAKE``.

        Invariant: at most one result block is tracked as the current LIVE
        notification holder at any time. Old synthesized pairs become skeleton
        placeholders but are never deleted; normal tool results keep old
        payload copies as historical timely state. The conversation structure is
        preserved, and model-facing serialization does not strip timely-transient
        keys from older holders; only the latest holder per family is current
        state.

        The fingerprint is committed only when injection succeeds (or
        when in a state that cannot inject — STUCK/SUSPENDED/empty).
        If injection is blocked (e.g. ``has_pending_tool_calls()``),
        the fingerprint stays at its prior value and the next heartbeat
        tick retries.
        """
        from ..notifications import is_channel_allowed
        from ..meta_block import skeletonize_notification_holder
        from .worker_recovery import (
            is_worker_interface_poisoned,
            request_worker_hang_refresh,
        )

        def _skip_poisoned_sync(*, phase: str) -> bool:
            """Fail closed: never touch a poisoned interface; request refresh."""
            if not is_worker_interface_poisoned(self):
                return False
            artifact = getattr(self, "_llm_worker_poison_artifact", None)
            self._log(
                "notification_sync_skipped_poisoned_interface",
                phase=phase,
                artifact=artifact,
                action="refresh_requested",
            )
            request_worker_hang_refresh(
                self,
                artifact_relpath=artifact,
                source="notification_sync",
            )
            return True

        store = self._notification_store

        def _allow(channel: str) -> bool:
            return is_channel_allowed(channel)

        fp = store.fingerprint(_allow)
        if fp == self._notification_fp:
            return

        if _skip_poisoned_sync(phase="before_collect"):
            return

        notifications = store.snapshot(_allow)

        if not notifications:
            if _skip_poisoned_sync(phase="before_empty_skeletonize"):
                return
            # All channels cleared.  Skeletonize the current live holder
            # (whether it is a normal tool-result dict or a synthesized
            # pair content dict) so no history block keeps advertising
            # stale notification state.  Synthesized pairs remain in
            # history as placeholders; they are never deleted.
            skeletonize_notification_holder(self)
            self._notification_fp = fp
            self._notification_deferred_log_fp = ()
            return

        # --- Inject new block based on current state ---
        from ..state import AgentState

        inject_ok = False

        if self._state == AgentState.ASLEEP:
            if _skip_poisoned_sync(phase="asleep_before_wake"):
                return
            # Notification arrival wakes the agent, then inject as IDLE.
            # The synthesized (call, result) pair impersonates a
            # voluntary notification(action="check") call; MSG_TC_WAKE
            # unblocks the run loop so _handle_tc_wake drives one
            # inference round off the existing wire (no fake user
            # input, no meta prefix).
            #
            # If the wire has pending tool_calls left over from an
            # earlier turn that exited mid-sequence (e.g. AED-exhausted
            # ASLEEP after a stuck LLM call), `_inject_notification_pair`
            # would refuse the append to preserve alternation. Heal the
            # wire first by closing those pending calls with synthetic
            # error results, then retry. If injection STILL fails after
            # healing, fall through to the degraded path below: stay
            # IDLE, deliver a degraded `MSG_REQUEST` that points the
            # agent at the recovery handles, and commit the fingerprint
            # so the same failure does not replay until on-disk state
            # changes.
            self._asleep.clear()
            self._cancel_event.clear()
            self._set_state(AgentState.IDLE, reason="notification_arrival")
            self._reset_uptime()
            # Old synthesized pairs are kept in history as placeholder
            # skeletons, not deleted.  Do not skeletonize the current holder
            # until this new injection succeeds; otherwise a blocked append
            # would discard the only live payload even though _notification_fp
            # remains uncommitted for retry.
            if _skip_poisoned_sync(phase="asleep_before_inject"):
                return
            inject_ok = self._inject_notification_pair(notifications)
            if not inject_ok:
                if _skip_poisoned_sync(phase="asleep_before_heal"):
                    return
                self._heal_pending_tool_calls(reason="wake_inject_blocked")
                if _skip_poisoned_sync(phase="asleep_before_reinject"):
                    return
                inject_ok = self._inject_notification_pair(notifications)
            if inject_ok:
                if _skip_poisoned_sync(phase="asleep_before_wake_enqueue"):
                    return
                from ..message import _make_message, MSG_TC_WAKE
                try:
                    wake_msg = _make_message(MSG_TC_WAKE, "system", "")
                    self.inbox.put(wake_msg)
                    self._wake_nap("notification_arrival")
                except Exception:
                    pass
            else:
                # Could not inject even after healing. Reverting to ASLEEP
                # without committing the fingerprint produced a livelock:
                # the next heartbeat tick saw the same .notification/
                # state, woke us again, failed inject again, reverted
                # again — forever (Jason's MCP/WeChat wake report).
                # Instead, stay IDLE and deliver a degraded MSG_REQUEST
                # that explains the situation and tells the agent how to
                # read the notification state directly. Commit the
                # fingerprint so the same failure does not replay.
                sources = sorted(notifications.keys())
                from ..message import _make_message, MSG_REQUEST
                degraded_text = (
                    "[system] Notification delivery could not be injected onto "
                    f"the wire after a heal attempt. Affected source(s): "
                    f"{', '.join(sources)}. Please query the current state by "
                    "calling notification(action=\"check\") or read the "
                    "producer files under .notification/ directly, then decide "
                    "whether to act. The kernel will not retry this delivery "
                    "until the on-disk state changes."
                )
                try:
                    self.inbox.put(_make_message(MSG_REQUEST, "system", degraded_text))
                    self._wake_nap("notification_arrival_degraded")
                except Exception:
                    pass
                self._log(
                    "notification_wake_degraded",
                    reason="inject_failed_after_heal",
                    sources=sources,
                )
                self._notification_fp = fp

        elif self._state == AgentState.IDLE:
            if _skip_poisoned_sync(phase="idle_before_inject"):
                return
            # Skeletonize + reinject AND post MSG_TC_WAKE.  IDLE is
            # "between turns, run loop blocked on inbox.get()" — without
            # a wake message the loop sits forever, the wire pair never
            # goes to the LLM, and the agent appears unresponsive even
            # though the notification arrived.
            #
            # _handle_tc_wake (post-rewrite) drives the wire forward
            # without appending anything: the (call, result) pair we
            # just spliced IS the new turn from the agent's perspective.
            # No fake user input, no meta prefix.
            #
            # Same heal-and-retry as the ASLEEP branch: if the wire has
            # dangling tool_calls, close them synthetically and retry,
            # otherwise the IDLE inbox stays dead.
            # Old synthesized pairs are kept in history as placeholder
            # skeletons, not deleted.  Do not skeletonize the current holder
            # until this new injection succeeds; otherwise a blocked append
            # would discard the only live payload even though _notification_fp
            # remains uncommitted for retry.
            inject_ok = self._inject_notification_pair(notifications)
            if not inject_ok:
                if _skip_poisoned_sync(phase="idle_before_heal"):
                    return
                self._heal_pending_tool_calls(reason="idle_inject_blocked")
                if _skip_poisoned_sync(phase="idle_before_reinject"):
                    return
                inject_ok = self._inject_notification_pair(notifications)
            if inject_ok:
                if _skip_poisoned_sync(phase="idle_before_wake_enqueue"):
                    return
                from ..message import _make_message, MSG_TC_WAKE
                try:
                    wake_msg = _make_message(MSG_TC_WAKE, "system", "")
                    self.inbox.put(wake_msg)
                    self._wake_nap("notification_sync")
                except Exception:
                    pass

        elif self._state == AgentState.ACTIVE:
            # Do not mutate unrelated tool results while a turn is active.
            # Leave the fingerprint uncommitted so the same on-disk
            # notification state is retried once the run loop transitions
            # to IDLE at the post-turn boundary.
            self._note_notification_deferred_active(
                fp,
                sources=list(notifications.keys()),
            )

        # STUCK / SUSPENDED — no injection.  The on-disk state is
        # observed; we just can't act on it until state recovers.

        # --- Commit fingerprint only if injection succeeded ---
        # ACTIVE deliberately defers without committing; only
        # STUCK/SUSPENDED commit here (they can't inject at all).
        if _skip_poisoned_sync(phase="before_fingerprint_commit"):
            return
        if inject_ok:
            self._notification_fp = fp
            self._notification_deferred_log_fp = ()
        elif self._state in (AgentState.STUCK, AgentState.SUSPENDED):
            self._notification_fp = fp
            self._notification_deferred_log_fp = ()

    def _heal_pending_tool_calls(self, *, reason: str) -> bool:
        """Close unanswered tool_calls so subsequent appends respect pairing.

        The close path first replays any matching durable real tool results
        from ``logs/events.jsonl``; calls without recorded results still get the
        existing synthetic error results.

        Used by the notification-sync wake path: if a previous turn
        exited mid-tool-sequence (AED-exhausted, kernel exception, etc.)
        and left dangling tool_calls, ``_inject_notification_pair``
        refuses to append. Without healing, the agent is stuck —
        notifications keep arriving, the inject keeps failing, and the
        run loop never gets a MSG_TC_WAKE. Heal once on wake so the
        retry can succeed.

        Returns True if anything was closed, False if the wire was
        already clean (or the session isn't ready, in which case there's
        nothing we can do here).
        """
        from .worker_recovery import is_worker_interface_poisoned
        if is_worker_interface_poisoned(self):
            self._log(
                "heal_pending_tool_calls_skipped_poisoned_interface",
                reason=reason,
                artifact=getattr(self, "_llm_worker_poison_artifact", None),
            )
            return False
        if self._chat is None:
            return False
        iface = self._chat.interface
        try:
            iface.tool_result_recovery_lookup = self._recover_pending_tool_result
        except Exception:
            pass
        if not iface.has_pending_tool_calls():
            return False
        diagnostics = _pending_tool_call_diagnostics(iface)
        try:
            iface.close_pending_tool_calls(reason=f"heal:{reason}")
        except Exception as e:
            self._log(
                "heal_pending_tool_calls_failed",
                reason=reason,
                error=str(e)[:200],
                **diagnostics,
            )
            return False
        self._log("heal_pending_tool_calls", reason=reason, **diagnostics)
        try:
            self._save_chat_history(ledger_source="heal")
        except Exception:
            pass
        return True

    def _recover_pending_tool_result(self, tool_call):
        from ..tool_result_recovery import recover_tool_result_block_from_events

        return recover_tool_result_block_from_events(
            self._working_dir,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            logger_fn=self._log,
        )

    def _inject_notification_pair(self, notifications: dict) -> bool:
        """Inject a synthetic (call, result) pair for IDLE / ASLEEP states.

        Builds ``notification(action="check")`` / ``<JSON dict>`` and
        appends to the wire interface.  Records the call_id for later
        stripping.

        The synthesized pair is byte-shape-identical to a voluntary
        ``notification(action="check")`` read so the LLM cannot distinguish
        a kernel-injected delivery from one it issued itself; the
        ``_synthesized: true`` body flag remains the only marker.

        The assistant turn carries only the synthetic ``ToolCallBlock``.
        The model-visible notification details and guidance live in the
        matching ``ToolResultBlock`` body, so notification wakes do not
        surface as transcript text / diary-like synthesized summaries.

        The ``ToolResultBlock`` is created with ``synthesized=True``
        (the existing flag the kernel already uses for heal-path
        placeholders) and its ``content`` is a mutable dict (not a JSON
        string).  All adapters serialize dict content correctly via
        ``json.dumps``.  Storing a dict enables in-place skeletonization
        later: when the live payload moves to a newer result, the dict
        is mutated to the skeleton placeholder shape — the pair stays in
        history but carries no live data.  The ``_synthesized: true``
        field in the body lets the agent distinguish kernel-injected
        reads from voluntary calls when reading conversation history.

        Both call.args and result.content carry safe notification freshness
        fields from build_meta plus a monotonic injection_seq. Internal tool-meta
        transit keys are stripped below before the synthesized pair reaches the wire.
        This makes
        every synthesized pair tokenize uniquely even when the underlying
        notification payload repeats — a protection layer against the
        DeepSeek cache fast-path empty-response failure without needing a
        visible assistant text prefix.

        Returns True if injection succeeded, False if it had to abort
        (e.g. pending tool_calls block append).  When False is returned,
        the caller MUST NOT update ``_notification_fp`` — otherwise the
        change would be silently dropped instead of retried.
        """
        import secrets
        from ..llm.interface import ToolCallBlock, ToolResultBlock
        from .worker_recovery import is_worker_interface_poisoned

        if is_worker_interface_poisoned(self):
            self._log(
                "notification_inject_skipped_poisoned_interface",
                sources=list(notifications.keys()),
                artifact=getattr(self, "_llm_worker_poison_artifact", None),
            )
            return False

        if self._chat is None:
            try:
                self._session.ensure_session()
            except Exception as e:
                self._log("notification_inject_aborted",
                          reason="ensure_session_failed", error=str(e)[:200])
                return False
            if self._chat is None:
                self._log("notification_inject_aborted",
                          reason="chat_still_none_after_ensure")
                return False

        iface = self._chat.interface
        # If the wire has unanswered tool_calls, appending a user-role
        # result entry would violate the alternation invariant.  Defer.
        if iface.has_pending_tool_calls():
            # Issue #126 diagnostic: log the tail shape so we can trace
            # why tool results were not detected as committed.
            tail_info = ""
            if iface._entries:
                last = iface._entries[-1]
                tail_info = f" tail_role={last.role} tail_blocks={len(last.content)}"
                if last.role == "assistant":
                    tc_ids = [b.id[:20] for b in last.content
                              if hasattr(b, 'id') and hasattr(b, 'name')]
                    tail_info += f" tc_ids={tc_ids}"
            self._log("notification_inject_aborted",
                      reason="pending_tool_calls",
                      sources=list(notifications.keys()),
                      _tail=tail_info)
            return False

        call_id = f"notif_{int(time.time()*1000):x}_{secrets.token_hex(2)}"

        # Meta freshness fields — same build_meta current-state hints real tool
        # results use for runtime state snapshots, embedded in BOTH call.args
        # and result.content so every synthesized pair tokenizes
        # uniquely even when the notification payload repeats. The monotonic
        # injection_seq is added on top to guarantee novelty within the same
        # second (heal+retry tight loops, time-blind agents).
        # Defensive getattr covers test doubles that bypass __init__ and
        # don't carry the full agent attribute surface.
        self._notification_inject_seq = getattr(self, "_notification_inject_seq", 0) + 1
        try:
            meta = build_meta(self)
        except (AttributeError, TypeError):
            meta = {}
        # ``current_time`` and the ``_tool_meta_*`` transit keys are
        # permanent per-tool-result fields consumed by ToolExecutor.
        # Notification injections are synthesized pairs, not real tool results,
        # and already have injection_seq for freshness/novelty; never flatten
        # internal tool-meta transit payloads onto the model-visible wire.
        meta.pop("current_time", None)
        meta.pop(TOOL_META_CONTEXT_PENDING_KEY, None)
        meta.pop(TOOL_META_CONTEXT_EVENT_PENDING_KEY, None)
        meta["injection_seq"] = self._notification_inject_seq

        notifications_with_guidance = build_notification_payload(notifications)
        # Keep log-only source counts from the raw canonical payload before the
        # transient lanes are sanitized for model visibility.  For example,
        # email's model-visible hook drops count and keeps only email_ids, but
        # the operational injection log should still say "1 email".
        notification_summary_counts: dict[str, object] = {}
        raw_notifications = notifications_with_guidance.get("notifications")
        if isinstance(raw_notifications, dict):
            for raw_source, raw_payload in raw_notifications.items():
                raw_count = None
                if isinstance(raw_payload, dict):
                    raw_data = raw_payload.get("data") or {}
                    if isinstance(raw_data, dict):
                        raw_count = raw_data.get("count")
                        if raw_count is None and isinstance(
                            raw_data.get("events"), list
                        ):
                            raw_count = len(raw_data["events"])
                        if raw_count is None and isinstance(
                            raw_data.get("voices"), list
                        ):
                            raw_count = len(raw_data["voices"])
                notification_summary_counts[raw_source] = raw_count

        # Nest the canonical notification payload under the unified ``_meta``
        # envelope so the synthesized pair presents notifications the same way
        # an ACTIVE tool result does (``_meta.notifications`` +
        # ``_meta.notification_guidance``).
        notification_persistent_payload = build_notification_persistent_payload(
            self, notifications_with_guidance
        )
        # Move (not duplicate): curated durable IM context now lives in
        # persistent lanes, so strip it from the model-visible ephemeral lane
        # before it is nested into the synthesized pair's _meta (and the
        # summary/logging envelope built from the same payload below).  This runs
        # even when no new persistent block is emitted, because the transient lane
        # must still remain routing-only on deliberate notification checks.
        # `notifications_with_guidance` is freshly built for this delivery cycle,
        # so in-place trimming cannot mutate producer-owned state.
        sanitize_telegram_notification_after_persistent(notifications_with_guidance)
        sanitize_wechat_notification_after_persistent(notifications_with_guidance)
        sanitize_feishu_notification_after_persistent(notifications_with_guidance)
        sanitize_whatsapp_notification_after_persistent(notifications_with_guidance)
        sanitize_email_notification_after_persistent(notifications_with_guidance)
        body_meta = dict(notifications_with_guidance)
        if notification_persistent_payload:
            body_meta.update(notification_persistent_payload)
        body = {
            "_synthesized": True,
            "_meta": body_meta,
        }
        # Flatten the remaining safe build_meta fields into body top-level —
        # these are the synthesized pair's own freshness/uniqueness fields
        # (for example injection_seq), distinct from the tool-result metadata
        # blocks under ``_meta``.
        body.update(meta)
        # Store body as a dict (not a JSON string) so it can be mutated
        # in-place when this pair is skeletonized later.  All adapters
        # already handle dict content via isinstance checks — see
        # interface_converters.py and anthropic/adapter.py.
        content_dict = body

        # Build a per-source summary: "3 email, 1 soul, 0 system".
        # Counts come from data.count / len(data.events) / len(data.voices)
        # depending on the producer; fall back to "?" if unparseable.
        summary_parts = []
        for source, payload in notifications_with_guidance["notifications"].items():
            count = None
            if isinstance(payload, dict):
                data = payload.get("data") or {}
                if isinstance(data, dict):
                    count = data.get("count")
                    if count is None and isinstance(data.get("events"), list):
                        count = len(data["events"])
                    if count is None and isinstance(data.get("voices"), list):
                        count = len(data["voices"])
            if count is None:
                raw_count = notification_summary_counts.get(source)
                if isinstance(raw_count, int):
                    count = raw_count
            if count is None and source == "email" and isinstance(
                notification_persistent_payload, dict
            ):
                persistent = notification_persistent_payload.get(
                    "notification_persistent"
                )
                if isinstance(persistent, dict):
                    email_context = persistent.get("email")
                    if isinstance(email_context, dict):
                        persistent_count = email_context.get("count")
                        if isinstance(persistent_count, int):
                            count = persistent_count
            summary_parts.append(f"{count if count is not None else '?'} {source}")
        guidance_text = (
            "Notice: this is kernel-synchronized state from notification channels, "
            "not necessarily a human instruction. Identify the source, interpret "
            "the relevant channel payload, and verify intent before deciding "
            "whether to act. If it contains an identifiable human message whose "
            "preview is truncated, ambiguous, includes media, or needs exact "
            "anchoring, first use the producer channel's normal read action; if "
            "a human is waiting, acknowledge directly before long work."
        )
        summary_text = (
            f"[synthesized — kernel notification sync] "
            f"Notification received: {', '.join(summary_parts)}. {guidance_text}"
            if summary_parts
            else f"[synthesized — kernel notification sync] Notification received. {guidance_text}"
        )

        # ``summary_text`` is log-only.  Do not place it in a TextBlock on the
        # wire: successful notification sync should be a structured
        # notification(action="check") call/result pair, not a visible
        # synthesized diary/text-input row.
        # call.args carries injection_seq only — real tool calls don't have
        # runtime freshness fields in their args (those live in results).
        # The seq is enough to defeat byte-equality on the assistant turn.
        call_block = ToolCallBlock(
            id=call_id,
            name="notification",
            args={
                "action": "check",
                "injection_seq": self._notification_inject_seq,
            },
        )
        result_block = ToolResultBlock(
            id=call_id,
            name="notification",
            content=content_dict,  # dict, not JSON string — mutable for skeletonization
            synthesized=True,
        )

        iface.add_assistant_message(content=[call_block])
        iface.add_tool_results([result_block])

        # The append succeeded.  Now skeletonize the previous live holder
        # (if any) before registering this synthesized pair as the new live
        # holder.  Doing it after append preserves the old live payload if
        # injection had to abort because of pending tool calls.
        prior_holder = getattr(self, "_notification_live_holder", None)
        if prior_holder is not None and prior_holder is not content_dict:
            try:
                from ..meta_block import skeletonize_notification_holder
                self._notification_live_holder = prior_holder
                skeletonize_notification_holder(self)
            except Exception:
                pass

        # Register content_dict as the live holder so future
        # skeletonize_notification_holder / attach_active_notifications calls
        # can mutate it in-place without touching conversation history.
        # _notification_block_id is retained for informational / molt-reset
        # purposes; it is no longer used for remove_pair_by_call_id.
        self._notification_live_holder = content_dict
        self._notification_block_id = call_id
        if notification_persistent_payload:
            record_notification_persistent_delivery(
                self,
                notification_persistent_payload,
                tool_call_id=call_id,
            )

        self._save_chat_history(ledger_source="notification_sync")
        self._log(
            "notification_pair_injected",
            call_id=call_id,
            sources=list(notifications.keys()),
            summary=summary_text,
            meta=meta,
        )
        # Reconstruct the full four-block ``_meta`` envelope for the durable
        # snapshot so the TUI /notification view shows the same ``_meta.*``
        # blocks (tool_meta/agent_meta/guidance/notifications/
        # notification_guidance) a synthesized pair would carry.  The live wire
        # body keeps its notification-only ``_meta``; this is logging-side only.
        from ..meta_block import build_synthetic_meta_envelope
        synthetic_envelope = build_synthetic_meta_envelope(
            self,
            notifications_with_guidance,
            call_id=call_id,
        )
        if notification_persistent_payload:
            synthetic_envelope.update(notification_persistent_payload)
        self._log_notification_block_injected(
            synthetic_envelope,
            mode="synthetic_notification_pair",
            call_id=call_id,
        )
        return True

    def _log_notification_block_injected(
        self,
        meta_envelope: dict,
        *,
        mode: str,
        call_id: str | None = None,
    ) -> None:
        """Persist a durable notification_block_injected event capturing the
        full ``_meta`` envelope the model saw.

        Best-effort: any exception is swallowed so callers are never broken by a
        logging failure.  ``meta_envelope`` is the complete four-block envelope
        — ``tool_meta``, ``agent_meta``, ``guidance``, plus ``notifications`` and
        ``notification_guidance`` — exactly as it appears under the tool result's
        ``_meta`` key (ACTIVE) or as reconstructed for the synthesized pair
        (IDLE/ASLEEP, via ``build_synthetic_meta_envelope``).

        The envelope is persisted under a top-level ``_meta`` field on the event
        so the TUI ``/notification`` view renders ``_meta.tool_meta`` /
        ``_meta.agent_meta`` / ``_meta.guidance`` / ``_meta.notification_guidance``
        / ``_meta.notifications`` directly.  A deep copy is stored so later
        in-place skeletonization or nested mutation of the live holder does not
        corrupt the logged snapshot.
        """
        try:
            notifications = meta_envelope.get("notifications", {})
            sources = sorted(notifications.keys()) if isinstance(notifications, dict) else []
            self._log(
                "notification_block_injected",
                mode=mode,
                call_id=call_id or "",
                sources=sources,
                _meta=copy.deepcopy(meta_envelope),
            )
        except Exception:
            pass

    def _persist_soul_entry(self, result: dict, mode: str = "flow", source: str = "agent") -> None:
        fn = self._intrinsic_hook("soul", "_persist_soul_entry")
        if fn is not None:
            fn(self, result, mode=mode, source=source)

    def _append_soul_flow_record(self, record: dict) -> None:
        fn = self._intrinsic_hook("soul", "_append_soul_flow_record")
        if fn is not None:
            fn(self, record)

    def _run_inquiry(self, question: str, source: str = "agent") -> None:
        fn = self._intrinsic_hook("soul", "_run_inquiry")
        if fn is not None:
            fn(self, question, source=source)

    def _flatten_v3_for_pair(self, voice: dict) -> dict:
        fn = self._intrinsic_hook("soul", "_flatten_v3_for_pair")
        if fn is None:
            return voice
        return fn(self, voice)

    def _run_consultation_fire(self) -> None:
        fn = self._intrinsic_hook("soul", "_run_consultation_fire")
        if fn is not None:
            fn(self)

    def _rehydrate_appendix_tracking(self) -> None:
        fn = self._intrinsic_hook("soul", "_rehydrate_appendix_tracking")
        if fn is not None:
            fn(self)

    # ------------------------------------------------------------------
    # Heartbeat (pass-throughs to lifecycle.py)
    # ------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        from .lifecycle import _start_heartbeat
        _start_heartbeat(self)

    def _stop_heartbeat(self) -> None:
        from .lifecycle import _stop_heartbeat
        _stop_heartbeat(self)

    def _heartbeat_loop(self) -> None:
        from .lifecycle import _heartbeat_loop
        _heartbeat_loop(self)

    # ------------------------------------------------------------------
    # Main loop (pass-throughs to turn.py)
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        from .turn import _run_loop
        _run_loop(self)

    def _concat_queued_messages(self, msg: Message) -> Message:
        from .turn import _concat_queued_messages
        return _concat_queued_messages(self, msg)

    def _handle_message(self, msg: Message) -> None:
        from .turn import _handle_message
        _handle_message(self, msg)

    def _handle_request(self, msg: Message) -> None:
        from .turn import _handle_request
        _handle_request(self, msg)

    def _handle_tc_wake(self, msg: Message) -> None:
        from .turn import _handle_tc_wake
        _handle_tc_wake(self, msg)

    def _get_guard_limits(self) -> tuple[int, int, int]:
        from .turn import _get_guard_limits
        return _get_guard_limits(self)

    def _process_response(self, response, *, ledger_source: str = "main") -> dict:
        from .turn import _process_response
        return _process_response(self, response, ledger_source=ledger_source)

    # ------------------------------------------------------------------
    # Refresh / preset (pass-throughs to lifecycle.py)
    # ------------------------------------------------------------------

    def _perform_refresh(
        self,
        *,
        skip_chat_history_save: bool = False,
        skip_save_reason: str | None = None,
    ) -> None:
        from .lifecycle import _perform_refresh
        _perform_refresh(
            self,
            skip_chat_history_save=skip_chat_history_save,
            skip_save_reason=skip_save_reason,
        )

    def load_preset(self, name: str, working_dir: "str | Path | None" = None) -> dict:
        """Load a preset through the composed preset-loader hook.

        The surface daemon/system tools call so they never import Core
        ``load_preset`` or construct an adapter — the wrapper sets ``_preset_loader``.
        Fails loud on a bare BaseAgent. ``working_dir`` defaults to this agent's workdir.
        """
        if self._preset_loader is None:
            raise RuntimeError(
                f"preset loader not composed on {type(self).__name__}; the Agent "
                "wrapper must set _preset_loader"
            )
        wd = working_dir if working_dir is not None else self._working_dir
        return self._preset_loader(name, wd)

    def _activate_preset(self, name: str) -> None:
        """Swap to a named preset — override in subclasses that support presets.

        BaseAgent raises NotImplementedError; Agent (lingtai.agent) overrides
        this with the real implementation.
        """
        raise NotImplementedError(
            f"_activate_preset not supported on {type(self).__name__}"
        )

    def _can_fallback_preset(self) -> bool:
        from .lifecycle import _can_fallback_preset
        return _can_fallback_preset(self)

    def _activate_default_preset(self) -> None:
        """Override hook — Agent subclass implements via _activate_preset(default).
        BaseAgent stub raises NotImplementedError."""
        raise NotImplementedError(
            "_activate_default_preset must be implemented by Agent subclass"
        )

    def _build_launch_cmd(self) -> list[str] | None:
        """Return the command to relaunch this agent. Override in subclasses."""
        return None

    # ------------------------------------------------------------------
    # Tool dispatch (pass-throughs to tools.py)
    # ------------------------------------------------------------------

    def _dispatch_tool(self, tc: ToolCall) -> dict:
        from .tools import _dispatch_tool
        return _dispatch_tool(self, tc)

    def _refresh_tool_inventory_section(self) -> None:
        from .tools import _refresh_tool_inventory_section
        _refresh_tool_inventory_section(self)

    def _build_tool_schemas(self) -> list[FunctionSchema]:
        from .tools import _build_tool_schemas
        return _build_tool_schemas(self)

    def has_capability(self, name: str) -> bool:
        from .tools import _has_capability
        return _has_capability(self, name)

    def add_tool(
        self,
        name: str,
        *,
        schema: dict | None = None,
        handler: Callable[[dict], dict] | None = None,
        description: str = "",
        system_prompt: str = "",
        glossary_package: str | None = None,
    ) -> None:
        from .tools import _add_tool
        _add_tool(self, name, schema=schema, handler=handler, description=description, system_prompt=system_prompt, glossary_package=glossary_package)

    def remove_tool(self, name: str) -> None:
        from .tools import _remove_tool
        _remove_tool(self, name)

    def override_intrinsic(self, name: str) -> Callable[[dict], dict]:
        from .tools import _override_intrinsic
        return _override_intrinsic(self, name)

    # ------------------------------------------------------------------
    # Prompt (pass-throughs to prompt.py)
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        from .prompt import _build_system_prompt
        return _build_system_prompt(self)

    def _build_system_prompt_batches(self) -> list[str]:
        from .prompt import _build_system_prompt_batches
        return _build_system_prompt_batches(self)

    def _flush_system_prompt(self) -> None:
        from .prompt import _flush_system_prompt
        _flush_system_prompt(self)

    def update_system_prompt(
        self, section: str, content: str, *, protected: bool = False
    ) -> None:
        from .prompt import _update_system_prompt
        _update_system_prompt(self, section, content, protected=protected)

    def _check_rules_file(self) -> None:
        from .lifecycle import _check_rules_file
        _check_rules_file(self)

    # ------------------------------------------------------------------
    # Identity / status (pass-throughs to identity.py)
    # ------------------------------------------------------------------

    def _build_manifest(self) -> dict:
        from .identity import _build_manifest
        return _build_manifest(self)

    def status(self) -> dict:
        from .identity import _status
        return _status(self)

    def _write_status_snapshot(self) -> None:
        """Write .status.json — live runtime snapshot consumed by TUI/portal."""
        try:
            atomic_write_json(
                self._working_dir / ".status.json",
                self.status(),
                preserve_existing_mode=True,
            )
        except Exception as e:
            logger.warning(f"[{self.agent_name}] Failed to write .status.json: {e}")

    # ------------------------------------------------------------------
    # Messaging (pass-throughs)
    # ------------------------------------------------------------------

    def mail(self, address: str, message: str, subject: str = "") -> dict:
        from .messaging import _mail
        return _mail(self, address, message, subject)

    def send(self, content: str | dict, sender: str = "user") -> None:
        from .messaging import _send
        _send(self, content, sender)

    # ------------------------------------------------------------------
    # Session persistence (delegates to SessionManager)
    # ------------------------------------------------------------------

    def get_token_usage(self) -> dict:
        """Return token usage summary (delegates to SessionManager)."""
        if not hasattr(self, "_session"):
            return {
                "input_tokens": 0, "output_tokens": 0,
                "thinking_tokens": 0, "cached_tokens": 0,
                "total_tokens": 0, "api_calls": 0,
                "ctx_system_tokens": 0, "ctx_tools_tokens": 0,
                "ctx_history_tokens": 0, "ctx_total_tokens": 0,
            }
        return self._session.get_token_usage()

    def get_runtime_session_token_usage(self) -> dict:
        """Return RUNTIME-SESSION token usage DELTAS — since last refresh/process start.

        Delegates to :meth:`SessionManager.get_runtime_session_token_usage`.
        "Runtime session" = the live process, counted since it last started or
        refreshed. This is NOT the source of the injected
        ``_meta.tool_meta.token_usage.session`` half: that half is "since last
        molt" and reads cumulative :meth:`get_token_usage` totals (which survive
        refresh), so it is not zeroed on refresh. This runtime getter's baseline
        resets on every refresh, so it is used only for since-refresh diagnostics.
        """
        if not hasattr(self, "_session"):
            return {
                "api_calls": 0,
                "input_tokens": 0,
                "cached_tokens": 0,
                "session_cache_rate": 0.0,
                "avg_input_tokens_per_api_call": 0,
            }
        return self._session.get_runtime_session_token_usage()

    def get_current_session_token_usage(self) -> dict:
        """DEPRECATED compat alias for :meth:`get_runtime_session_token_usage`.

        The ``current_session`` name was ambiguous (it read like "since last
        molt" but always meant "since last refresh"). Retained only for external
        callers; new code must use :meth:`get_runtime_session_token_usage`.
        """
        return self.get_runtime_session_token_usage()

    def runtime_session(self):
        """Return the current RUNTIME-SESSION object (live lifecycle segment).

        No id; a fresh empty object per process start / refresh / restart / molt.
        See :meth:`SessionManager.runtime_session` and
        docs/references/runtime-vs-agent-session-objects.md.
        """
        return self._session.runtime_session()

    def agent_session(self):
        """Return the current AGENT-SESSION object (molt generation), or ``None``.

        Keyed by ``molt_count`` (no new id). Rebuilt from the durable trajectory
        at start/refresh by :meth:`rebuild_agent_session`. ``None`` before the
        first rebuild is installed.
        """
        return self._session.agent_session()

    def rebuild_agent_session(self):
        """(Re)build the AGENT-SESSION for the current ``molt_count`` and install it.

        Uses the optimized rebuild path (indexed ``log.sqlite`` → bounded reverse
        JSONL scan → full scan last resort; see
        :func:`agent_session.rebuild_agent_session_from_events`), so the normal
        case does NOT full-scan a large ``events.jsonl``. The rebuilt since-molt
        aggregate is installed on the session manager so the injected
        ``token_usage.session`` half and other since-molt consumers can read a
        single owner. Returns the rebuilt :class:`AgentSession`.

        Never raises for a missing/empty trajectory — a brand-new agent yields a
        zeroed boot session at the current ``molt_count``.
        """
        from ..agent_session import rebuild_agent_session_from_events

        session = rebuild_agent_session_from_events(
            self._working_dir,
            molt_count=int(getattr(self, "_molt_count", 0) or 0),
            logger_fn=self._log,
        )
        self._session.install_agent_session(session)
        return session

    def get_chat_state(self) -> dict:
        """Serialize current chat session for persistence."""
        return self._session.get_chat_state()

    def restore_chat(self, state: dict) -> None:
        """Restore or create a chat session from saved state."""
        self._session.restore_chat(state)

    def restore_token_state(self, state: dict) -> None:
        """Restore cumulative token counters from a saved session."""
        self._session.restore_token_state(state)

    def _save_chat_history(self, *, ledger_source: str = "main") -> None:
        """Write chat history and token usage to disk (no git commit).

        Called after every completed interaction for crash resilience.
        Git commits are handled by the periodic snapshot system. The persisted
        chat history is intentionally redacted; after process restart, restored
        history likewise contains redacted placeholders rather than raw secrets.

        ``ledger_source`` tags any token-ledger entry written for the
        most recent LLM round-trip. Default ``"main"`` covers the bulk
        of callers. Set to ``"tc_wake"`` from involuntary splice paths
        so consultation cadence does not double-count splices as main turns.
        """
        history_dir = self._working_dir / "history"
        history_dir.mkdir(exist_ok=True)
        try:
            state = self.get_chat_state()
            if state and state.get("messages"):
                redacted_messages = redact_for_trajectory(state["messages"])
                lines = [json.dumps(entry, ensure_ascii=False) for entry in redacted_messages]
                atomic_write_text(history_dir / "chat_history.jsonl", "\n".join(lines) + "\n")
        except Exception as e:
            logger.warning(f"[{self.agent_name}] Failed to save chat history: {e}")
        # Update .agent.json with current state
        try:
            self._workdir.write_manifest(self._build_manifest())
        except Exception as e:
            logger.warning(f"[{self.agent_name}] Failed to update manifest: {e}")
        self._write_status_snapshot()
        # Append per-call token usage to ledger
        usage, self._last_usage = self._last_usage, None
        if usage is not None:
            try:
                ledger_path = self._working_dir / "logs" / "token_ledger.jsonl"
                model = getattr(self._session, "_model", None) or getattr(self.service, "model", None)
                endpoint = getattr(self.service, "_base_url", None)
                ledger_extra = {"source": ledger_source}
                usage_extra = getattr(usage, "extra", None)
                if isinstance(usage_extra, dict):
                    ledger_extra.update(
                        {k: v for k, v in usage_extra.items() if v is not None}
                    )
                append_token_entry(
                    ledger_path,
                    input=usage.input_tokens,
                    output=usage.output_tokens,
                    thinking=usage.thinking_tokens,
                    cached=usage.cached_tokens,
                    model=model,
                    endpoint=endpoint,
                    extra=ledger_extra,
                )
            except Exception as e:
                logger.warning(f"[{self.agent_name}] Failed to append token ledger: {e}")

    # ------------------------------------------------------------------
    # Hooks (overridable by subclasses)
    # ------------------------------------------------------------------

    def _cpr_agent(self, address: str) -> "BaseAgent | None":
        """Resuscitate a suspended agent at *address*.

        Returns the resuscitated agent, or None if not supported.
        Override in subclasses (e.g. lingtai's Agent) to provide
        full reconstruction from persisted working dir state.
        """
        return None

    def _pre_request(self, msg: Message) -> str:
        """Transform message content before sending to LLM.

        Returns the content string to send.
        """
        return msg.content if isinstance(msg.content, str) else json.dumps(msg.content)

    def _post_request(self, msg: Message, result: dict) -> None:
        """Called after _process_response.

        Override in subclasses for post-processing.
        """
        # Clean up turn-local Telegram Task Card context.
        self._teardown_telegram_task_card()

    def _on_tool_result_hook(
        self,
        tool_name: str,
        tool_args: dict,
        result: dict,
        *,
        tool_call_id: str | None = None,
    ) -> str | None:
        """Hook called after each tool execution.

        If this returns a non-None string, the current request processing
        returns immediately with that string as the result text.

        ``tool_call_id`` is the provider-assigned id for this tool call,
        passed directly by ToolExecutor so no heuristic scan is needed.

        For the live Task Card this freezes the matching row (final whole-second
        elapsed + done marker) while other active rows keep ticking.  It runs on
        the orchestrating thread in input order in both the sequential and
        parallel paths, so it never touches a tool result — it only observes
        completion — and always returns ``None`` for the card path.

        Large tool results no longer raise a ``large_tool_result`` system
        notification here.  They are ranked instead through
        ``_meta.agent_meta.current_tool_result_chars.top_results`` and digested
        via ``system(action="summarize")`` (see meta_block.current_tool_result_chars).
        """
        self._freeze_task_card_row(tool_call_id)
        return None

    def _freeze_task_card_row(self, tool_call_id: str | None) -> None:
        """Freeze the completed row for ``tool_call_id`` (best-effort, fail-open).

        Records the row's final whole-second elapsed and marks it done, then
        re-renders so the completed row shows its frozen value while any still
        -active parallel rows keep ticking.  Never raises into tool execution.
        """
        ctx = self._telegram_task_card_context
        if ctx is None:
            return
        try:
            with ctx["_lock"]:
                rows = ctx.get("rows", [])
                now = self._task_card_clock(ctx)()
                frozen = False
                for r in rows:
                    if r["call_id"] == tool_call_id and not r["done"]:
                        r["elapsed_s"] = self._task_card_elapsed(now, r["started"])
                        r["done"] = True
                        frozen = True
                        break
                if frozen:
                    self._render_task_card(ctx)
        except Exception:
            self._log_task_card_reverse_exception("freeze", tool_name="")

    # Rolling window: the automatic card shows the latest N ordinary tool rows,
    # where N comes from ``_task_card_max_tool_rows()`` (env-configurable, default
    # 1).  Rows are appended in actual pre-dispatch order and the oldest displayed
    # tool is dropped when a newer one enters, so sequential completed rows are
    # NOT cleared down to only the current tool.  The cap is on ordinary tool rows
    # only — the single API-error row keeps its own lifecycle.

    def _on_tool_pre_dispatch_hook(
        self,
        tool_name: str,
        tool_args: dict,
        *,
        tool_call_id: str | None = None,
    ) -> None:
        """Pre-dispatch hook: appends to the live Telegram Task Card window.

        One row per tool call, appended in actual pre-dispatch order (parallel
        pre-dispatch callbacks are serialized by ToolExecutor before any future
        starts).  The card keeps a rolling window of the newest
        ``_task_card_max_tool_rows()`` tool rows (env-configurable, default 1):
        once the window is full, a new tool drops the oldest displayed tool
        rather than clearing the completed rows down to only the current tool.  A
        new epoch (a tool arriving with no active tool predecessor) bumps the
        generation so a stale heartbeat can't overwrite the window, and
        (re)starts the heartbeat.  The card is created lazily on the first tool
        call; direct-answer turns produce no card.
        """
        ctx = self._telegram_task_card_context
        if ctx is None:
            return
        # Recursion guard: never fire the hook for the private reverse-channel
        # tool itself (it is unlisted, so the model can never dispatch it, but a
        # guard keeps the invariant explicit).
        if tool_name == _TASK_CARD_TOOL:
            return
        reasoning = tool_args.get("_reasoning", "")
        if not reasoning:
            return

        action = tool_args.get("action", "")
        with ctx["_lock"]:
            rows = ctx.setdefault("rows", [])
            # A batch is "active" while any *tool* row is not yet done.  A tool
            # that arrives with no active tool predecessor opens a new epoch:
            # bump the generation (so a stale heartbeat from the previous epoch
            # can't overwrite this one) and (re)start the heartbeat.  API-error
            # rows are excluded from this active check.  Unlike before, completed
            # *tool* rows are NOT discarded — they stay in the rolling window and
            # are only evicted by the newest-N cap below.  A lingering
            # API-error row IS superseded when a new epoch opens, because a fresh
            # tool batch means the LLM has responded past the error (its existing
            # lifecycle); it is only ever dropped here, never by the tool cap.
            batch_active = any(
                not r["done"] for r in rows if r.get("kind") != "api_error"
            )
            if not batch_active:
                if any(r.get("kind") == "api_error" for r in rows):
                    rows[:] = [r for r in rows if r.get("kind") != "api_error"]
                ctx["generation"] = ctx.get("generation", 0) + 1
                self._start_task_card_heartbeat(ctx)
            rows.append({
                "call_id": tool_call_id,
                "tool": tool_name,
                "tool_action": action,
                "reasoning": reasoning,
                # Monotonic start for elapsed; wall-clock local instant captured
                # once here and frozen into an immutable display string so
                # heartbeats never change it and parallel rows keep their own.
                "started": self._task_card_clock(ctx)(),
                "started_at": self._capture_task_card_started_at(ctx),
                "elapsed_s": 0,
                "done": False,
            })
            self._cap_task_card_tool_rows(rows)
            self._render_task_card(ctx)

    def _cap_task_card_tool_rows(self, rows: list) -> None:
        """Evict oldest tool rows in place, keeping the newest window.

        Caps ordinary tool rows to ``_task_card_max_tool_rows()`` (env-configurable,
        default 1) while retaining every API-error row (``kind='api_error'``) and
        preserving relative order.  Only the oldest surplus *tool* rows are
        removed; an API-error row is never discarded to satisfy the tool-row cap.
        Mutates ``rows`` in place so the heartbeat/render always see the same list
        object.  Caller holds the lock.
        """
        tool_indices = [
            i for i, r in enumerate(rows) if r.get("kind") != "api_error"
        ]
        surplus = len(tool_indices) - _task_card_max_tool_rows(
            getattr(self, "_working_dir", None)
        )
        if surplus <= 0:
            return
        # Drop the oldest surplus tool rows (lowest indices) in place.
        drop = set(tool_indices[:surplus])
        rows[:] = [r for i, r in enumerate(rows) if i not in drop]

    def _capture_task_card_started_at(self, ctx: dict) -> str:
        """Capture the local start instant as an immutable display string.

        Fail-open: a wall-clock error must never block a tool from dispatching,
        so any failure yields ``""`` (the render simply omits the stamp).
        """
        try:
            return self._format_task_card_timestamp(self._task_card_wall_clock(ctx)())
        except Exception:
            return ""

    def _task_card_metadata(self) -> dict:
        """Project canonical, bounded session telemetry for Task Card rendering."""
        try:
            token_usage = build_tool_meta_token_usage(self) or {}
            session = token_usage.get("session")
            if not isinstance(session, dict):
                return {}
            metadata: dict[str, int | float] = {}
            for key in (
                "api_calls",
                "cache_miss_tokens",
                "cache_miss_budget",
                "context_tokens",
                "context_window",
            ):
                value = session.get(key)
                if type(value) is int and value >= 0:
                    metadata[key] = value
            for key in ("session_cache_rate", "context_usage"):
                value = session.get(key)
                if (
                    type(value) in {int, float}
                    and not isinstance(value, bool)
                    and value >= 0
                ):
                    metadata[key] = float(value)
            return metadata
        except Exception:
            return {}

    def _render_task_card(self, ctx: dict) -> None:
        """Send the current batch rows to the card (create lazily, else edit).

        Caller holds ``ctx["_lock"]``.  Fail-open and observable: a reverse-call
        failure keeps the existing card id and is logged content-free, never
        raising into tool execution.  A recovery re-create adopts the new id.
        """
        payload_rows = self._task_card_payload_rows(ctx["rows"])
        metadata = self._task_card_metadata()
        if ctx.get("card_message_id") is None:
            try:
                result = ctx["mcp_client"].call_tool(_TASK_CARD_TOOL, {
                    "sub_action": "create",
                    "account": ctx["account"],
                    "chat_id": ctx["chat_id"],
                    "rows": payload_rows,
                    "metadata": metadata,
                }, timeout=5.0)
            except Exception:
                self._log_task_card_reverse_exception("create", "batch")
                return
            if self._task_card_result_suppressed(result):
                return
            message_id = self._task_card_result_message_id(result)
            if message_id is None:
                self._log_task_card_reverse_failure("create", "batch", result)
            else:
                ctx["card_message_id"] = message_id
            if self._task_card_result_partial_failure(result):
                self._log_task_card_reverse_partial("create", "batch", result)
        else:
            try:
                result = ctx["mcp_client"].call_tool(_TASK_CARD_TOOL, {
                    "sub_action": "update",
                    "account": ctx["account"],
                    "chat_id": ctx["chat_id"],
                    "card_message_id": ctx["card_message_id"],
                    "rows": payload_rows,
                    "metadata": metadata,
                }, timeout=5.0)
            except Exception:
                self._log_task_card_reverse_exception("update", "batch")
                return
            if self._task_card_result_suppressed(result):
                return
            new_id = self._task_card_result_message_id(result)
            if new_id is None:
                self._log_task_card_reverse_failure("update", "batch", result)
            elif new_id != ctx["card_message_id"]:
                ctx["card_message_id"] = new_id
            if self._task_card_result_partial_failure(result):
                self._log_task_card_reverse_partial("update", "batch", result)

    @staticmethod
    def _task_card_clock(ctx: dict):
        """Return the context's monotonic clock, defaulting to ``time.monotonic``.

        Production always injects ``clock`` in ``_setup_telegram_task_card``; the
        default keeps the hooks robust for a context built without one.
        """
        clock = ctx.get("clock")
        if clock is not None:
            return clock
        import time
        return time.monotonic

    @staticmethod
    def _task_card_elapsed(now: float, started: float) -> int:
        """Monotonic elapsed whole seconds, floored and clamped to >=0.

        Floor semantics (``int``) so the 0.5s heartbeat shows integer seconds
        without a decimal point — half-second frames read ``0s, 0s, 1s, 1s, 2s``
        and a final 8.01s freezes as ``8s``.
        """
        return max(0, int(now - started))

    @staticmethod
    def _task_card_wall_clock(ctx: dict):
        """Return the context's wall clock, defaulting to local-aware now.

        Captures the *local* start instant once per tool (separate from the
        monotonic ``clock`` used for elapsed).  ``datetime.now().astimezone()``
        yields a tz-aware value carrying the machine's local UTC offset.
        Injectable so tests fix the instant instead of asserting a real clock.
        """
        wall = ctx.get("wall_clock")
        if wall is not None:
            return wall
        from datetime import datetime

        def _local_now():
            return datetime.now().astimezone()

        return _local_now

    @staticmethod
    def _format_task_card_timestamp(dt) -> str:
        """Format a captured local instant as ``HH:MM:SS UTC±HH`` (hour-only).

        Final UI contract: time of day plus a signed two-digit local UTC hour,
        with no date, no ``Started`` label, no regional abbreviation, and no
        minute component on the offset (a fractional-hour zone like ``UTC+05:30``
        is intentionally shown as ``UTC+05``).  Returns ``""`` for a value with no
        usable offset so the render simply omits the stamp rather than raising.
        """
        offset = getattr(dt, "utcoffset", lambda: None)()
        if offset is None:
            return ""
        total = offset.total_seconds()
        sign = "-" if total < 0 else "+"
        hours = int(abs(total) // 3600)
        return f"{dt.strftime('%H:%M:%S')} UTC{sign}{hours:02d}"

    @staticmethod
    def _task_card_payload_rows(rows: list) -> list:
        """Project internal row state into the manager's render payload.

        Only the display fields cross the reverse channel — the monotonic
        ``started`` timestamp and the internal ``call_id`` stay in the kernel.
        A sanitized API-error row carries only its safe machine summary
        (``status``/``code``/``state``/attempt counts), never the raw exception.
        """
        payload: list[dict] = []
        for r in rows:
            if r.get("kind") == "api_error":
                payload.append({
                    "kind": "api_error",
                    "status": r.get("status"),
                    "code": r.get("code"),
                    "error_type": r.get("error_type"),
                    "provider": r.get("provider"),
                    "model": r.get("model"),
                    "state": r.get("state"),
                    "attempt": r.get("attempt"),
                    "max_attempts": r.get("max_attempts"),
                    "done": r["done"],
                })
            else:
                payload.append({
                    "tool": r["tool"],
                    "tool_action": r["tool_action"],
                    "reasoning": r["reasoning"],
                    "elapsed_s": r["elapsed_s"],
                    "done": r["done"],
                    # Immutable local start stamp (tool rows only); the monotonic
                    # ``started`` and internal ``call_id`` stay in the kernel.
                    "started_at": r.get("started_at", ""),
                })
        return payload

    # ------------------------------------------------------------------
    # LLM/provider API-error reporting into the automatic Task Card
    # ------------------------------------------------------------------

    # Internal sentinel call-id for the single stable API-error row per turn.
    _TASK_CARD_API_ROW_ID = "__api_error__"

    # Allow-list of provider machine error codes safe to display verbatim. A
    # code outside this set is dropped (only the numeric status shows), so an
    # untrusted/free-form code can never leak arbitrary provider text. Keep this
    # curated and conservative — add a code only when it is a known, non-secret
    # machine identifier.
    _TASK_CARD_SAFE_API_CODES = frozenset({
        "usage_limit_reached",
        "rate_limit_exceeded",
        "rate_limit_error",
        "insufficient_quota",
        "quota_exceeded",
        "context_length_exceeded",
        "overloaded_error",
        "server_error",
        "service_unavailable",
        "api_error",
        "timeout",
    })

    @staticmethod
    def _task_card_api_status(exc: object) -> int | None:
        """Return a structured HTTP status from a provider exception, if valid.

        Reads only integer attributes (``status_code``/``status``/``code``, or
        ``response.status_code``) — never the message/body — and accepts only the
        HTTP status range 100-599. Boolean and out-of-range values are omitted.
        """
        for attr in ("status_code", "status", "code"):
            value = getattr(exc, attr, None)
            if type(value) is int and 100 <= value <= 599:
                return value
        response = getattr(exc, "response", None)
        if response is not None:
            value = getattr(response, "status_code", None)
            if type(value) is int and 100 <= value <= 599:
                return value
        return None

    @classmethod
    def _task_card_api_code(cls, exc: object) -> str | None:
        """Extract a provider machine error code, strictly allow-listed.

        Only structured attributes are inspected (``code`` string, or a
        ``body``/``error`` dict's ``code``/``type``), and the candidate must be
        an exact member of ``_TASK_CARD_SAFE_API_CODES`` — otherwise ``None``.
        Never derived from ``str(exc)`` or a free-form message, so arbitrary
        provider text, URLs, tokens, or paths can never reach the card.
        """
        candidates: list[object] = []
        code_attr = getattr(exc, "code", None)
        if isinstance(code_attr, str):
            candidates.append(code_attr)
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                candidates.append(err.get("code"))
                candidates.append(err.get("type"))
            candidates.append(body.get("code"))
        for cand in candidates:
            if isinstance(cand, str) and cand in cls._TASK_CARD_SAFE_API_CODES:
                return cand
        return None

    @staticmethod
    def _task_card_safe_identifier(value: object, *, limit: int = 64) -> str | None:
        """Return a bounded ASCII machine identifier, never free-form text."""
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > limit:
            return None
        safe_punctuation = frozenset("._:/-")
        if not all(ch.isascii() and (ch.isalnum() or ch in safe_punctuation) for ch in value):
            return None
        return value

    def _task_card_api_identity(self) -> tuple[str | None, str | None]:
        """Return bounded public provider/model fields from the live service."""
        try:
            service = getattr(self, "service", None)
            provider = getattr(service, "provider", None)
            model = getattr(service, "model", None)
        except Exception:
            return None, None
        return (
            self._task_card_safe_identifier(provider, limit=48),
            self._task_card_safe_identifier(model, limit=80),
        )

    def _report_task_card_api_error(
        self,
        exc: object,
        *,
        attempt: int | None = None,
        max_attempts: int | None = None,
        terminal: bool = False,
    ) -> None:
        """Surface an LLM/provider API failure onto the automatic Task Card.

        Observe-only and fail-open: this only ever reports; it never changes the
        original error, the retry/fallback decision, an eventual success, a final
        failure, token accounting, or any user-facing semantics.  A no-op when no
        Telegram Task Card context exists (non-Telegram/no-route turn).

        One stable API-error row per turn/retry sequence is upserted into the
        current batch and the same card is created (lazily) or edited — repeated
        failures update the row rather than sending a card per error.  The row
        carries only bounded machine fields (exception type, public provider/model,
        valid HTTP status, allow-listed code, and retry state); externally supplied
        opaque identifiers are deliberately omitted.
        ``terminal`` freezes it as the concrete last behavior.
        """
        ctx = self._telegram_task_card_context
        if ctx is None:
            return
        try:
            status = self._task_card_api_status(exc)
            code = self._task_card_api_code(exc)
            error_type = self._task_card_safe_identifier(type(exc).__name__, limit=48)
            provider, model = self._task_card_api_identity()
            with ctx["_lock"]:
                rows = ctx.setdefault("rows", [])
                row = self._find_api_error_row(rows)
                if row is None:
                    row = {
                        "call_id": self._TASK_CARD_API_ROW_ID,
                        "kind": "api_error",
                    }
                    rows.append(row)
                row["status"] = status
                row["code"] = code
                row["error_type"] = error_type
                row["provider"] = provider
                row["model"] = model
                row["attempt"] = attempt
                row["max_attempts"] = max_attempts
                row["state"] = "error" if terminal else "retrying"
                row["done"] = bool(terminal)
                self._render_task_card(ctx)
        except Exception:
            # Fail-open, but observable: reporting must never raise into the turn.
            self._log_task_card_reverse_exception("api_error", tool_name="")

    def _recover_task_card_api_error(self) -> None:
        """Mark a previously-reported API-error row ``recovered`` (fail-open).

        Preserves the fact that an error happened (the row stays, frozen) while
        recording that the turn ultimately succeeded.  A no-op when no API-error
        row was reported this turn or no card context exists.
        """
        ctx = self._telegram_task_card_context
        if ctx is None:
            return
        try:
            with ctx["_lock"]:
                rows = ctx.get("rows", [])
                row = self._find_api_error_row(rows)
                if row is None:
                    return
                row["state"] = "recovered"
                row["done"] = True
                self._render_task_card(ctx)
        except Exception:
            self._log_task_card_reverse_exception("api_error", tool_name="")

    @classmethod
    def _find_api_error_row(cls, rows: list) -> dict | None:
        for r in rows:
            if r.get("kind") == "api_error":
                return r
        return None

    def _task_card_heartbeat_tick(self, generation: int | None = None) -> None:
        """One heartbeat step: refresh active-row elapsed and edit the card.

        Best-effort and race-safe.  A no-op when the context is gone, when
        ``generation`` names an older batch (a stale timer), or when every row
        is already frozen (so the frozen last-behavior state is never
        overwritten).  Uses the injected monotonic clock; never sends a new card.
        """
        ctx = self._telegram_task_card_context
        if ctx is None:
            return
        with ctx["_lock"]:
            if generation is not None and generation != ctx.get("generation"):
                return
            rows = ctx.get("rows", [])
            # Only TOOL rows have a monotonic ``started`` and a ticking elapsed.
            # API-error rows (``kind='api_error'``) carry no ``started`` and are
            # rendered by their own explicit state transitions, so the heartbeat
            # must never read ``r['started']`` on them (else a retrying API row,
            # which is ``done=False``, would KeyError and kill the timer thread).
            active_tool_rows = [
                r for r in rows if not r["done"] and r.get("kind") != "api_error"
            ]
            if not active_tool_rows:
                return
            now = self._task_card_clock(ctx)()
            for r in active_tool_rows:
                r["elapsed_s"] = self._task_card_elapsed(now, r["started"])
            self._render_task_card(ctx)

    # Mechanical heartbeat cadence (seconds) while any tool row is active. 0.5s
    # so the card stays lively; elapsed is floored to whole seconds by
    # ``_task_card_elapsed``, so half-second frames read 0s, 0s, 1s, 1s, 2s.
    _TASK_CARD_HEARTBEAT_INTERVAL = 0.5

    def _start_task_card_heartbeat(self, ctx: dict) -> None:
        """Start the half-second heartbeat (one thread for the whole turn).

        Idempotent: a single turn-scoped thread is reused across batches — each
        tick reads the *current* generation/rows, so a new batch is picked up
        without spawning another thread, and the generation guard still stops a
        stale write.  Spawning is gated on ``heartbeat_enabled`` so unit tests can
        drive ticks deterministically without a real thread or real ``sleep``.
        Caller holds ``ctx["_lock"]``.
        """
        if not ctx.get("heartbeat_enabled"):
            return
        if ctx.get("timer_thread") is not None:
            return  # one heartbeat thread per turn is enough
        import threading

        stop_event = ctx.get("stop_event") or threading.Event()
        ctx["stop_event"] = stop_event
        sleep = ctx.get("sleep") or stop_event.wait

        def _loop() -> None:
            # Exit as soon as the turn ends (stop_event) or the context is torn
            # down/replaced.  Sleep first so the initial render (done by the
            # pre-dispatch hook) is not immediately duplicated.  Each tick reads
            # the current generation, so a stale write is impossible even as the
            # batch turns over under the thread.
            while not stop_event.is_set():
                if sleep(self._TASK_CARD_HEARTBEAT_INTERVAL):
                    return  # stop_event was set during the wait
                if self._telegram_task_card_context is not ctx:
                    return
                self._task_card_heartbeat_tick()

        thread = threading.Thread(
            target=_loop, daemon=True, name="telegram-task-card-heartbeat",
        )
        ctx["timer_thread"] = thread
        thread.start()

    @staticmethod
    def _stop_task_card_heartbeat(ctx: dict) -> None:
        """Signal the heartbeat thread to stop promptly and join briefly.

        Idempotent and best-effort; safe to call on a context that never started
        a heartbeat (direct-answer turns, unit-test contexts).
        """
        stop_event = ctx.get("stop_event")
        if stop_event is not None:
            stop_event.set()
        thread = ctx.get("timer_thread")
        if thread is not None:
            thread.join(timeout=2.0)

    @staticmethod
    def _task_card_result_error(result: object) -> bool:
        """True if an MCP reverse-call result reports a tool-level error.

        The MCP client surfaces tool-level failures as an error *dict* rather
        than raising (see ``lingtai.services.mcp.MCPClient.call_tool``), so the
        Task Card hook must inspect the payload instead of relying on
        exceptions. ``stale_delete_failed`` is specifically the Telegram
        manager's pre-send error result; treating it as an error also fails
        closed if a malformed payload contradicts that contract with ``ok``.
        """
        return isinstance(result, dict) and (
            result.get("status") == "error"
            or result.get("stale_delete_failed") is True
        )

    @staticmethod
    def _task_card_result_suppressed(result: object) -> bool:
        """True for deliberate Telegram presentation suppression, not failure."""
        return (
            isinstance(result, dict)
            and result.get("status") == "ok"
            and result.get("suppressed") is True
            and result.get("taskcard") is False
        )

    @staticmethod
    def _task_card_result_message_id(result: object) -> str | None:
        """Extract a card ``message_id`` from a successful reverse-call result.

        Returns ``None`` unless the manager's successful ``status: ok`` contract
        carries a usable id. In particular, ``stale_delete_failed`` is a
        pre-send error, so it cannot authorize adoption even in a contradictory
        payload that also supplies an id.
        """
        if (
            not isinstance(result, dict)
            or result.get("status") != "ok"
            or result.get("stale_delete_failed") is True
        ):
            return None
        message_id = result.get("message_id")
        return message_id if isinstance(message_id, str) and message_id else None

    @staticmethod
    def _task_card_result_partial_failure(result: object) -> bool:
        """Whether a successful result exposes post-send persistence failure."""
        return (
            isinstance(result, dict)
            and result.get("status") == "ok"
            and result.get("stale_delete_failed") is not True
            and result.get("resident_persist_failed") is True
            and BaseAgent._task_card_result_message_id(result) is not None
        )

    @staticmethod
    def _log_task_card_reverse_partial(
        phase: str, tool_name: str, result: object,
    ) -> None:
        """Log the content-free post-send persistence partial result."""
        flags = ",".join(
            flag for flag in ("resident_persist_failed",)
            if isinstance(result, dict) and result.get(flag) is True
        )
        logger.warning(
            "telegram task-card reverse call partial phase=%s tool=%s flags=%s",
            phase, tool_name, flags or "unknown",
        )

    @staticmethod
    def _log_task_card_reverse_failure(
        phase: str, tool_name: str, result: object,
    ) -> None:
        """Emit a content-free warning for a failed Task Card reverse call.

        Redaction by construction: only the phase (create/update), the driving
        tool name, and the result *status* are logged. The reasoning excerpt,
        chat id, account, card id, and the provider error text are deliberately
        never included, so the observable signal cannot leak user content,
        credentials, or routing identifiers.
        """
        status = result.get("status") if isinstance(result, dict) else type(result).__name__
        logger.warning(
            "telegram task-card reverse call failed phase=%s tool=%s status=%s",
            phase, tool_name, status,
        )

    @staticmethod
    def _log_task_card_reverse_exception(phase: str, tool_name: str) -> None:
        """Emit a content-free warning when a Task Card reverse call raises.

        Call only from inside the ``except`` block. Redaction by construction:
        the exception *class* name is read from ``sys.exc_info`` and only the
        phase, driving tool name, and that class are logged — never the exception
        message, reasoning, chat id, account, card id, or provider text.
        """
        import sys
        exc_type = sys.exc_info()[0]
        exc_name = exc_type.__name__ if exc_type is not None else "unknown"
        logger.warning(
            "telegram task-card reverse call raised phase=%s tool=%s exc=%s",
            phase, tool_name, exc_name,
        )

    def _setup_telegram_task_card(self) -> None:
        """Capture current Telegram inbound route; card created lazily on first tool call.

        Reads the high-attention notification payload, derives (account, chat_id)
        from the first preview's message_ref compound id, looks up the Telegram
        MCP client from ``_mcp_clients_by_tool``.  Dedup guard prevents re-arming
        on subsequent heartbeats for the same fingerprint.
        """
        import threading

        from ..notifications import is_channel_allowed

        store = self._notification_store
        fp = store.fingerprint(is_channel_allowed)
        last_fp = getattr(self, "_last_telegram_card_fingerprint", None)
        if fp == last_fp:
            return

        notifications = store.snapshot(is_channel_allowed)
        telegram_data = notifications.get("mcp.telegram")
        if not telegram_data or not isinstance(telegram_data, dict):
            return
        data = telegram_data.get("data", {})
        previews = data.get("previews", []) if isinstance(data, dict) else []
        if not previews:
            return
        first = previews[0] if isinstance(previews, list) and previews else {}
        if not isinstance(first, dict):
            return
        message_ref = first.get("message_ref", "")
        if not message_ref or not isinstance(message_ref, str):
            return
        parts = message_ref.split(":", 2)
        if len(parts) < 2:
            return
        account, chat_id_str = parts[0], parts[1]
        try:
            chat_id = int(chat_id_str)
        except (ValueError, TypeError):
            return

        telegram_client = getattr(self, "_mcp_clients_by_tool", {}).get("telegram")
        if telegram_client is None:
            return

        import time as _time
        from datetime import datetime as _datetime

        self._telegram_task_card_context = {
            "mcp_client": telegram_client,
            "account": account,
            "chat_id": chat_id,
            "card_message_id": None,
            # Re-entrant: the pre-dispatch/result hooks and the heartbeat thread
            # all take this lock, and the hooks nest the render under it.
            "_lock": threading.RLock(),
            # Monotonic clock for elapsed measurement; injectable for tests.
            "clock": _time.monotonic,
            # Wall clock for the immutable local start stamp per tool row
            # (separate from the monotonic elapsed clock); injectable for tests.
            "wall_clock": lambda: _datetime.now().astimezone(),
            "rows": [],
            "generation": 0,
            # Production arms the real 1s heartbeat thread; unit tests leave this
            # unset and drive ticks deterministically.
            "heartbeat_enabled": True,
            "stop_event": threading.Event(),
        }
        self._last_telegram_card_fingerprint = fp

    def _teardown_telegram_task_card(self) -> None:
        """Stop the heartbeat and freeze the resident card's last-behavior state.

        Idempotent: safe to call multiple times.  If no card was ever created
        (direct-answer turn), just clears the context silently.  The resident
        card is left showing its concrete last batch — the tool rows with their
        completed markers and final elapsed values — as a last-behavior record,
        not a generic overall ``DONE`` headline (the normal assistant reply
        communicates overall completion).  Any row still active at turn end is
        frozen at its current elapsed so the card never keeps ticking.
        """
        ctx = self._telegram_task_card_context
        if ctx is None:
            return
        try:
            self._stop_task_card_heartbeat(ctx)
            if ctx.get("card_message_id") is not None:
                with ctx["_lock"]:
                    now = self._task_card_clock(ctx)()
                    for r in ctx.get("rows", []):
                        # Only freeze TOOL rows — API-error rows have no
                        # ``started`` and their terminal/recovered state is set
                        # explicitly, so reading ``r['started']`` here would abort
                        # finalization inside the outer fail-open wrapper.
                        if not r["done"] and r.get("kind") != "api_error":
                            r["elapsed_s"] = self._task_card_elapsed(now, r["started"])
                            r["done"] = True
                    payload_rows = self._task_card_payload_rows(ctx.get("rows", []))
                    metadata = self._task_card_metadata()
                # Reverse-call the private tool name (no ``action`` — the server
                # forces the task-card action). Send the frozen rows so the card
                # freezes on the concrete last behavior.
                result = ctx["mcp_client"].call_tool(_TASK_CARD_TOOL, {
                    "sub_action": "finalize",
                    "account": ctx["account"],
                    "chat_id": ctx["chat_id"],
                    "card_message_id": ctx["card_message_id"],
                    "rows": payload_rows,
                    "metadata": metadata,
                }, timeout=5.0)
                # A tool-level failure returns an error dict; surface it so a
                # card left un-finalized is observable (still never blocking).
                if self._task_card_result_error(result):
                    self._log_task_card_reverse_failure("finalize", "", result)
                elif self._task_card_result_partial_failure(result):
                    self._log_task_card_reverse_partial("finalize", "", result)
        except Exception:
            # Fail-open, but observable: the finalize reverse call itself raised.
            self._log_task_card_reverse_exception("finalize", "")
        finally:
            self._telegram_task_card_context = None

    def _maybe_notify_large_tool_result(
        self,
        tool_name: str,
        result: object,
        *,
        tool_call_id: str | None = None,
    ) -> None:
        """Retained no-op: large tool results no longer raise a notification.

        Large results used to publish a ``source="large_tool_result"`` system
        notification (gated by a total-length threshold) so the agent would be
        reminded to summarize them.  That reminder has been removed: large
        results are surfaced as a ranked list under
        ``_meta.agent_meta.current_tool_result_chars.top_results`` (see
        :func:`meta_block.current_tool_result_chars`) and digested via
        ``system(action="summarize")``.  The result still flows into normal
        tool-result history and the char-ranking; it simply creates no
        ``.notification/system.json`` event.

        This method is kept as a stable, overridable seam (subclasses/tests
        may still reference it) but is intentionally inert.
        """
        return None
