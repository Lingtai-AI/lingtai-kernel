"""Pure lifecycle-ledger and shadow guardian policy.

Core owns evidence vocabulary and decisions only.  Concrete files, locks,
clocks, sleeping, and process observation live behind the Ports below.  There
is deliberately no recovery/launch/signal Port in this capability.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol


LIFECYCLE_EVENT_SCHEMA = "lingtai.agent_lifecycle_event/v1"
LIFECYCLE_EVENT_VERSION = 1
GUARDIAN_OUTPUT_SCHEMA = "lingtai.agent_guardian_verdict/v1"
GUARDIAN_OUTPUT_VERSION = 1
GUARDIAN_HEARTBEAT_THRESHOLD_SECONDS = 120.0
GUARDIAN_CONFIRMATION_INTERVAL_SECONDS = 2.0
GUARDIAN_LOOP_INTERVAL_SECONDS = 30.0
GUARDIAN_CHECKPOINT_SECONDS = 86400.0
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_LEDGER_RECORD_BYTES = 64 * 1024
MAX_LEDGER_RECORDS = 4096
# A signed 32-bit ceiling is valid for Darwin's ``ctypes.c_int`` process APIs
# and is a conservative subset of the Windows DWORD PID domain.
MAX_PROCESS_ID = 2_147_483_647

EVENT_KINDS = frozenset({
    "boot_registered", "suspend_requested", "cpr_requested", "guardian_verdict",
})
VERDICTS = frozenset({"alive", "frozen", "dead", "unknown"})
RECOVERY_PLANS = frozenset({
    "none", "hold_explicit_suspend", "would_sigcont", "would_launch", "observe_only",
})
PROCESS_RESULTS = frozenset({
    "exact_running", "exact_stopped", "absent", "identity_mismatch",
    "command_mismatch", "executable_mismatch", "unavailable",
})
LEASE_RESULTS = frozenset({"held", "free", "unknown"})
HEARTBEAT_RESULTS = frozenset({"fresh", "stale", "missing", "unreadable"})
MANIFEST_RESULTS = frozenset({"valid", "malformed", "absent"})


class LifecycleLedgerError(RuntimeError):
    """Bounded fail-loud ledger error safe for mechanical callers."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class LifecycleLedgerCorruption(LifecycleLedgerError):
    """The ledger cannot safely determine current explicit intent."""


class GuardianAlreadyRunning(RuntimeError):
    """A different process already owns this agent's guardian lease."""


class GuardianLeaseUnavailable(LifecycleLedgerError):
    """The guardian lifetime lease could not be created, opened, or locked."""


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc_timestamp(value: object) -> datetime:
    text = _evidence_text(value, "invalid_recorded_at", limit=64)
    if not text.endswith("Z"):
        raise LifecycleLedgerCorruption("invalid_recorded_at")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise LifecycleLedgerCorruption("invalid_recorded_at") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise LifecycleLedgerCorruption("invalid_recorded_at")
    return parsed


def _text(value: object, code: str, *, limit: int = 4096) -> str:
    """Return bounded UTF-8 scalar text, allowing ordinary human whitespace."""
    if not isinstance(value, str) or not value or len(value) > limit or "\0" in value:
        raise LifecycleLedgerCorruption(code)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LifecycleLedgerCorruption(code) from exc
    return value


def _evidence_text(value: object, code: str, *, limit: int = 4096) -> str:
    text = _text(value, code, limit=limit)
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in text):
        raise LifecycleLedgerCorruption(code)
    return text


def _choice(
    value: object,
    choices: frozenset[str] | set[str],
    code: str,
    *,
    limit: int = 64,
) -> str:
    text = _evidence_text(value, code, limit=limit)
    if text not in choices:
        raise LifecycleLedgerCorruption(code)
    return text


def _canonical_path_evidence(value: object, code: str) -> str:
    text = _evidence_text(value, code)
    try:
        path = Path(text)
        if not path.is_absolute() or str(path.resolve()) != text:
            raise LifecycleLedgerCorruption(code)
    except LifecycleLedgerCorruption:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise LifecycleLedgerCorruption(code) from exc
    return text


def _uuid(value: object, code: str) -> str:
    text = _text(value, code, limit=64)
    try:
        uuid.UUID(text)
    except ValueError as exc:
        raise LifecycleLedgerCorruption(code) from exc
    return text


def validate_guardian_payload_semantics(payload: Mapping[str, object]) -> None:
    """Enforce exactly the durable truth table emitted by ``evaluate_presence``."""
    required = {
        "guardian_id", "runtime_id", "verdict", "recovery_plan", "confirmation",
        "evidence_digest", "policy_fingerprint", "process", "agent_lease", "intent",
        "heartbeat_age_seconds", "agent_manifest",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise LifecycleLedgerCorruption("invalid_guardian_payload")
    verdict = _choice(payload["verdict"], VERDICTS, "invalid_guardian_decision")
    recovery_plan = _choice(
        payload["recovery_plan"],
        RECOVERY_PLANS,
        "invalid_guardian_decision",
    )
    confirmation = _choice(
        payload["confirmation"],
        {"not_required", "confirmed", "changed", "unavailable"},
        "invalid_confirmation",
    )
    process = _choice(payload["process"], PROCESS_RESULTS, "invalid_guardian_evidence")
    lease = _choice(payload["agent_lease"], LEASE_RESULTS, "invalid_guardian_evidence")
    manifest = _choice(
        payload["agent_manifest"],
        MANIFEST_RESULTS,
        "invalid_guardian_evidence",
    )
    intent = _choice(payload["intent"], {"none", "active"}, "invalid_guardian_intent")
    age = payload["heartbeat_age_seconds"]
    if age is not None:
        if isinstance(age, bool) or not isinstance(age, (int, float)):
            raise LifecycleLedgerCorruption("invalid_heartbeat_age")
        try:
            valid_age = math.isfinite(age) and age >= 0
        except OverflowError:
            valid_age = False
        if not valid_age:
            raise LifecycleLedgerCorruption("invalid_heartbeat_age")
    expected_plan = (
        "hold_explicit_suspend"
        if intent == "active"
        else {
            "alive": "none",
            "frozen": "would_sigcont",
            "dead": "would_launch",
            "unknown": "observe_only",
        }[verdict]
    )
    if recovery_plan != expected_plan:
        raise LifecycleLedgerCorruption("invalid_guardian_semantics")

    fresh = age is not None and age <= GUARDIAN_HEARTBEAT_THRESHOLD_SECONDS
    stale_or_missing = age is None or age > GUARDIAN_HEARTBEAT_THRESHOLD_SECONDS

    if verdict == "alive":
        coherent = (
            payload["runtime_id"] is not None
            and process == "exact_running"
            and lease == "held"
            and manifest == "valid"
            and fresh
            and confirmation == "not_required"
        )
    elif verdict == "frozen":
        coherent = (
            payload["runtime_id"] is not None
            and process == "exact_stopped"
            and lease == "held"
            and manifest == "valid"
            and (
                (fresh and confirmation == "not_required")
                or (stale_or_missing and confirmation == "confirmed")
            )
        )
    elif verdict == "dead":
        coherent = (
            payload["runtime_id"] is not None
            and process == "absent"
            and lease == "free"
            and manifest == "valid"
            and stale_or_missing
            and confirmation == "confirmed"
        )
    else:
        decisive = (
            manifest == "valid"
            and (
                (process == "exact_running" and lease == "held" and fresh)
                or (process == "exact_stopped" and lease == "held" and fresh)
            )
        )
        coherent = confirmation in {"changed", "unavailable"} and not decisive
    if not coherent:
        raise LifecycleLedgerCorruption("invalid_guardian_semantics")


def validate_lifecycle_event(value: object) -> dict:
    """Validate one exact v1 event envelope and payload."""
    if not isinstance(value, dict):
        raise LifecycleLedgerCorruption("record_not_object")
    required = {
        "schema", "schema_version", "event_id", "event", "recorded_at",
        "agent_address", "actor", "reason", "payload",
    }
    if set(value) != required:
        raise LifecycleLedgerCorruption("record_fields_unsupported")
    if (
        value["schema"] != LIFECYCLE_EVENT_SCHEMA
        or isinstance(value["schema_version"], bool)
        or not isinstance(value["schema_version"], int)
        or value["schema_version"] != LIFECYCLE_EVENT_VERSION
    ):
        raise LifecycleLedgerCorruption("schema_unsupported")
    _uuid(value["event_id"], "invalid_event_id")
    kind = _choice(value["event"], EVENT_KINDS, "event_unsupported")
    parse_utc_timestamp(value["recorded_at"])
    _evidence_text(value["agent_address"], "invalid_agent_address", limit=512)
    _text(value["reason"], "invalid_reason", limit=512)
    actor = value["actor"]
    if not isinstance(actor, dict) or set(actor) != {"kind", "id"}:
        raise LifecycleLedgerCorruption("invalid_actor")
    actor_kind = _choice(
        actor["kind"],
        {"runtime", "agent", "operator", "guardian", "unknown"},
        "invalid_actor",
    )
    actor_id = _evidence_text(actor["id"], "invalid_actor", limit=512)
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise LifecycleLedgerCorruption("invalid_payload")

    if kind == "boot_registered":
        expected = {"runtime_id", "pid", "start_identity", "working_dir", "executable", "command"}
        if set(payload) != expected:
            raise LifecycleLedgerCorruption("invalid_boot_payload")
        _uuid(payload["runtime_id"], "invalid_runtime_id")
        if (
            isinstance(payload["pid"], bool)
            or not isinstance(payload["pid"], int)
            or not 0 < payload["pid"] <= MAX_PROCESS_ID
        ):
            raise LifecycleLedgerCorruption("invalid_pid")
        _evidence_text(payload["start_identity"], "invalid_start_identity", limit=512)
        working_dir = _canonical_path_evidence(payload["working_dir"], "invalid_working_dir")
        _canonical_path_evidence(payload["executable"], "invalid_executable")
        command = payload["command"]
        if not isinstance(command, dict) or set(command) != {"program", "subcommand", "agent_dir"}:
            raise LifecycleLedgerCorruption("invalid_command_evidence")
        _evidence_text(command["program"], "invalid_command_evidence", limit=512)
        if command["subcommand"] != "run":
            raise LifecycleLedgerCorruption("invalid_command_evidence")
        command_agent_dir = _canonical_path_evidence(
            command["agent_dir"], "invalid_command_evidence"
        )
        if command_agent_dir != working_dir:
            raise LifecycleLedgerCorruption("boot_agent_dir_mismatch")
    elif kind == "suspend_requested":
        if set(payload) != {"intent_id"}:
            raise LifecycleLedgerCorruption("invalid_suspend_payload")
        _uuid(payload["intent_id"], "invalid_intent_id")
    elif kind == "cpr_requested":
        if set(payload) != {"clears_intent_id"}:
            raise LifecycleLedgerCorruption("invalid_clear_payload")
        _uuid(payload["clears_intent_id"], "invalid_intent_id")
    else:
        expected = {
            "guardian_id", "runtime_id", "verdict", "recovery_plan", "confirmation",
            "evidence_digest", "policy_fingerprint", "process", "agent_lease", "intent",
            "heartbeat_age_seconds", "agent_manifest",
        }
        if set(payload) != expected:
            raise LifecycleLedgerCorruption("invalid_guardian_payload")
        if actor_kind != "guardian":
            raise LifecycleLedgerCorruption("invalid_guardian_actor")
        guardian_id = _uuid(payload["guardian_id"], "invalid_guardian_id")
        if actor_id != guardian_id:
            raise LifecycleLedgerCorruption("guardian_actor_id_mismatch")
        if payload["runtime_id"] is not None:
            _uuid(payload["runtime_id"], "invalid_runtime_id")
        for field in ("evidence_digest", "policy_fingerprint"):
            digest = _evidence_text(payload[field], "invalid_guardian_digest", limit=64)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise LifecycleLedgerCorruption("invalid_guardian_digest")
        validate_guardian_payload_semantics(payload)
    return value


def make_lifecycle_event(
    kind: str,
    *,
    event_id: str,
    recorded_at: str,
    agent_address: str,
    actor_kind: str,
    actor_id: str,
    reason: str,
    payload: Mapping[str, object],
) -> dict:
    if not isinstance(payload, Mapping):
        raise LifecycleLedgerCorruption("invalid_payload")
    return validate_lifecycle_event(
        {
            "schema": LIFECYCLE_EVENT_SCHEMA,
            "schema_version": LIFECYCLE_EVENT_VERSION,
            "event_id": event_id,
            "event": kind,
            "recorded_at": recorded_at,
            "agent_address": agent_address,
            "actor": {"kind": actor_kind, "id": actor_id},
            "reason": reason,
            "payload": dict(payload),
        }
    )


@dataclass(frozen=True)
class LedgerSnapshot:
    records: tuple[dict, ...]
    active_intent_id: str | None
    latest_boot: dict | None
    latest_guardian: dict | None
    physical_record_count: int = 0


def reduce_lifecycle_events(
    records: list[dict],
    *,
    expected_agent_address: str | None = None,
) -> LedgerSnapshot:
    """Derive intent without TTL, boot clearing, repair, or truncation."""
    unique: list[dict] = []
    by_id: dict[str, str] = {}
    active: str | None = None
    agent_address: str | None = None
    latest_boot = latest_guardian = None
    for raw in records:
        record = validate_lifecycle_event(raw)
        if (
            expected_agent_address is not None
            and record["agent_address"] != expected_agent_address
        ):
            raise LifecycleLedgerCorruption("agent_address_mismatch")
        if agent_address is not None and record["agent_address"] != agent_address:
            raise LifecycleLedgerCorruption("agent_address_mismatch")
        agent_address = record["agent_address"]
        encoded = stable_json(record)
        prior = by_id.get(record["event_id"])
        if prior is not None:
            if prior != encoded:
                raise LifecycleLedgerCorruption("event_id_conflict")
            continue
        by_id[record["event_id"]] = encoded
        unique.append(record)
        kind, payload = record["event"], record["payload"]
        if kind == "suspend_requested":
            intent_id = payload["intent_id"]
            if active is not None and active != intent_id:
                raise LifecycleLedgerCorruption("overlapping_suspend_intent")
            active = intent_id
        elif kind == "cpr_requested":
            if active is None or payload["clears_intent_id"] != active:
                raise LifecycleLedgerCorruption("intent_clear_mismatch")
            active = None
        elif kind == "boot_registered":
            if active is not None:
                raise LifecycleLedgerCorruption("boot_while_suspend_active")
            latest_boot = record
        elif kind == "guardian_verdict":
            expected_intent = "active" if active else "none"
            if payload["intent"] != expected_intent:
                raise LifecycleLedgerCorruption("guardian_intent_mismatch")
            expected_runtime = latest_boot["payload"]["runtime_id"] if latest_boot else None
            if payload["runtime_id"] != expected_runtime:
                raise LifecycleLedgerCorruption("guardian_runtime_mismatch")
            latest_guardian = record
    return LedgerSnapshot(
        tuple(unique),
        active,
        latest_boot,
        latest_guardian,
        physical_record_count=len(records),
    )


class LifecycleLedgerPort(Protocol):
    def read_snapshot(self) -> LedgerSnapshot: ...
    def register_boot(self, *, agent_address: str, working_dir: str) -> dict: ...
    def request_suspend(self, *, agent_address: str, actor_id: str, reason: str) -> str: ...
    def request_cpr(self, *, agent_address: str, actor_id: str, reason: str) -> str | None: ...
    def append_guardian_verdict(self, *, agent_address: str, actor_id: str, reason: str, payload: Mapping[str, object]) -> dict: ...


@dataclass(frozen=True)
class PresenceSample:
    sampled_at: float
    runtime_id: str | None
    pid: int | None
    expected_start_identity: str | None
    observed_start_identity: str | None
    process: str
    agent_lease: str
    agent_manifest: str
    heartbeat: str
    heartbeat_age_seconds: float | None
    command_match: bool | None
    executable_match: bool | None
    registered_workdir_match: bool | None
    issues: tuple[str, ...] = ()

    def ownership(self) -> dict:
        return {
            "runtime_id": self.runtime_id,
            "pid": self.pid,
            "expected_start_identity": self.expected_start_identity,
            "observed_start_identity": self.observed_start_identity,
            "process": self.process,
            "agent_lease": self.agent_lease,
            "agent_manifest": self.agent_manifest,
            "command_match": self.command_match,
            "executable_match": self.executable_match,
            "registered_workdir_match": self.registered_workdir_match,
            "issues": list(self.issues),
        }

    def evidence_digest(self) -> str:
        evidence = {**self.ownership(), "heartbeat": self.heartbeat, "heartbeat_age_seconds": self.heartbeat_age_seconds}
        return hashlib.sha256(stable_json(evidence).encode("utf-8")).hexdigest()


def validate_presence_sample(sample: PresenceSample) -> PresenceSample:
    """Validate one Core sample before it can influence a guardian decision."""
    if not isinstance(sample, PresenceSample):
        raise LifecycleLedgerError("invalid_presence_sample")
    try:
        if (
            isinstance(sample.sampled_at, bool)
            or not isinstance(sample.sampled_at, (int, float))
            or not math.isfinite(sample.sampled_at)
        ):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        runtime_id = None
        if sample.runtime_id is not None:
            runtime_id = _uuid(sample.runtime_id, "invalid_presence_sample")
        if sample.pid is not None and (
            isinstance(sample.pid, bool)
            or not isinstance(sample.pid, int)
            or not 0 < sample.pid <= MAX_PROCESS_ID
        ):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        expected_identity = None
        if sample.expected_start_identity is not None:
            expected_identity = _evidence_text(
                sample.expected_start_identity,
                "invalid_presence_sample",
                limit=512,
            )
        observed_identity = None
        if sample.observed_start_identity is not None:
            observed_identity = _evidence_text(
                sample.observed_start_identity,
                "invalid_presence_sample",
                limit=512,
            )
        process = _choice(sample.process, PROCESS_RESULTS, "invalid_presence_sample")
        _choice(sample.agent_lease, LEASE_RESULTS, "invalid_presence_sample")
        manifest = _choice(sample.agent_manifest, MANIFEST_RESULTS, "invalid_presence_sample")
        heartbeat = _choice(sample.heartbeat, HEARTBEAT_RESULTS, "invalid_presence_sample")
        for fact in (
            sample.command_match,
            sample.executable_match,
            sample.registered_workdir_match,
        ):
            if fact is not None and not isinstance(fact, bool):
                raise LifecycleLedgerCorruption("invalid_presence_sample")
        if not isinstance(sample.issues, tuple):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        for issue in sample.issues:
            _evidence_text(issue, "invalid_presence_sample", limit=512)

        age = sample.heartbeat_age_seconds
        if heartbeat in {"fresh", "stale"}:
            if isinstance(age, bool) or not isinstance(age, (int, float)):
                raise LifecycleLedgerCorruption("invalid_presence_sample")
            try:
                finite_age = math.isfinite(age) and age >= 0
            except OverflowError:
                finite_age = False
            if not finite_age or (heartbeat == "fresh") != (
                age <= GUARDIAN_HEARTBEAT_THRESHOLD_SECONDS
            ):
                raise LifecycleLedgerCorruption("invalid_presence_sample")
        elif age is not None:
            raise LifecycleLedgerCorruption("invalid_presence_sample")

        has_boot = runtime_id is not None
        if has_boot != (sample.pid is not None and expected_identity is not None):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        if not has_boot and (
            process != "unavailable"
            or observed_identity is not None
            or sample.registered_workdir_match is not None
        ):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        if process in {"exact_running", "exact_stopped"} and not (
            has_boot
            and manifest == "valid"
            and observed_identity == expected_identity
            and sample.command_match is True
            and sample.executable_match is True
            and sample.registered_workdir_match is True
        ):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        if process == "absent" and (
            not has_boot
            or observed_identity is not None
            or sample.command_match is not None
            or sample.executable_match is not None
        ):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        if process == "identity_mismatch" and not (
            has_boot
            and observed_identity is not None
            and observed_identity != expected_identity
        ):
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        if process == "command_mismatch" and sample.command_match is not False:
            raise LifecycleLedgerCorruption("invalid_presence_sample")
        if process == "executable_mismatch" and sample.executable_match is not False:
            raise LifecycleLedgerCorruption("invalid_presence_sample")
    except (LifecycleLedgerCorruption, OverflowError) as exc:
        raise LifecycleLedgerError("invalid_presence_sample") from exc
    return sample


class GuardianHostPort(Protocol):
    def acquire_guardian_lease(self) -> None: ...
    def release_guardian_lease(self) -> None: ...
    def wall_time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def sample(self, boot_record: dict | None) -> PresenceSample: ...


@dataclass(frozen=True)
class GuardianDecision:
    verdict: str
    recovery_plan: str
    confirmation: str
    sample: PresenceSample
    confirmation_sample: PresenceSample | None
    active_intent_id: str | None

    def evidence_digest(self) -> str:
        evidence = {
            "first": self.sample.evidence_digest(),
            "second": self.confirmation_sample.evidence_digest() if self.confirmation_sample else None,
        }
        return hashlib.sha256(stable_json(evidence).encode("utf-8")).hexdigest()

    def policy_fingerprint(self) -> str:
        value = {
            "verdict": self.verdict,
            "recovery_plan": self.recovery_plan,
            "confirmation": self.confirmation,
            "runtime_id": self.sample.runtime_id,
            "expected_start_identity": self.sample.expected_start_identity,
            "observed_start_identity": self.sample.observed_start_identity,
            "process": self.sample.process,
            "agent_lease": self.sample.agent_lease,
            "agent_manifest": self.sample.agent_manifest,
            "intent_id": self.active_intent_id,
            "issues": list(self.sample.issues),
        }
        return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()

    def event_payload(self, guardian_id: str) -> dict:
        process = self.sample.process
        if self.verdict == "unknown" and self.sample.issues and process in {
            "exact_running",
            "exact_stopped",
        }:
            process = "unavailable"
        return {
            "guardian_id": guardian_id,
            "runtime_id": self.sample.runtime_id,
            "verdict": self.verdict,
            "recovery_plan": self.recovery_plan,
            "confirmation": self.confirmation,
            "evidence_digest": self.evidence_digest(),
            "policy_fingerprint": self.policy_fingerprint(),
            "process": process,
            "agent_lease": self.sample.agent_lease,
            "agent_manifest": self.sample.agent_manifest,
            "intent": "active" if self.active_intent_id else "none",
            "heartbeat_age_seconds": self.sample.heartbeat_age_seconds,
        }


def needs_confirmation(sample: PresenceSample) -> bool:
    validate_presence_sample(sample)
    return not (
        sample.heartbeat == "fresh"
        and sample.process in {"exact_running", "exact_stopped"}
        and sample.agent_lease == "held"
        and not sample.issues
    )


def evaluate_presence(
    first: PresenceSample,
    second: PresenceSample | None,
    *,
    active_intent_id: str | None,
) -> GuardianDecision:
    """Classify exact evidence; an explicit intent changes only the plan."""
    validate_presence_sample(first)
    if second is not None:
        validate_presence_sample(second)
    verdict, confirmation = "unknown", "unavailable"
    exact_pair = (
        second is not None
        and first.ownership() == second.ownership()
        and second.heartbeat in {"stale", "missing"}
    )
    if (
        not first.issues
        and first.process == "exact_running"
        and first.agent_lease == "held"
        and first.agent_manifest == "valid"
        and first.heartbeat == "fresh"
    ):
        verdict, confirmation = "alive", "not_required"
    elif (
        not first.issues
        and first.process == "exact_stopped"
        and first.agent_lease == "held"
        and first.agent_manifest == "valid"
    ):
        if first.heartbeat == "fresh":
            verdict, confirmation = "frozen", "not_required"
        elif first.heartbeat in {"stale", "missing"} and exact_pair:
            verdict, confirmation = "frozen", "confirmed"
    elif (
        not first.issues
        and first.process == "absent"
        and first.agent_lease == "free"
        and first.agent_manifest == "valid"
    ):
        if first.heartbeat in {"stale", "missing"} and exact_pair:
            verdict, confirmation = "dead", "confirmed"
    if verdict == "unknown" and second is not None and first.ownership() != second.ownership():
        confirmation = "changed"

    if active_intent_id is not None:
        plan = "hold_explicit_suspend"
    else:
        plan = {
            "alive": "none",
            "frozen": "would_sigcont",
            "dead": "would_launch",
            "unknown": "observe_only",
        }[verdict]
    return GuardianDecision(verdict, plan, confirmation, first, second, active_intent_id)
