"""Refresh-drain host identity: marker schema and process-start-identity probe.

A refresh-drain host is an already-running daemon-owning process that keeps
executing a fixed set of daemon runs after its owning agent's interactive
liveness (heartbeat/workdir lease) has been handed off to a successor process
started by `system.refresh`. This module owns exactly the identity primitives
that make that handoff verifiable without granting a live-PID-match exemption
to anything that merely happens to share a PID:

- ``ProcessStartIdentity`` / ``probe_process_start_identity`` — a mechanically
  reliable "this PID is still the same OS process it was, not a PID reused by
  an unrelated later process" check, backed by a native proc-table start-time
  source on both Linux (``/proc/<pid>/stat``) and Darwin (``proc_pidinfo``).
  Fails closed (returns ``None``) on any platform without such a source, and
  never falls back to a weaker signal (heartbeat freshness, lock-file
  existence, a boolean flag, a self-reported timestamp).
- ``RefreshHostMarker`` — the immutable per-host record persisted to
  ``daemons/.refresh-hosts/<generation>.json``, carrying PID + start identity +
  a durable allocated ``sequence`` + the fixed owned run-id set + generation/
  nonce for audit. Publication is exclusive (never overwrites), and every
  marker is validated through the same strict schema whether it is being
  built fresh or loaded back from disk.

No policy (duplicate-guard exemption, control-plane routing, drain-loop
behavior) lives here — this module answers only "is this marker well-formed
and does its claimed PID still mechanically match its claimed identity",
nothing about what a caller should do with that answer.
"""
from __future__ import annotations

import errno
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lingtai.kernel._fsutil import atomic_write_json, read_json
from lingtai.kernel.process_match import match_agent_run

MARKER_SCHEMA_VERSION = 1

_REFRESH_HOSTS_DIRNAME = ".refresh-hosts"
_SEQUENCE_DIRNAME = ".sequence"


def _fsync_dir(path: Path) -> None:
    """Fsync a directory's metadata (e.g. a just-created hard link's dirent).

    A file's own ``fsync`` durably persists its content but NOT the
    directory-entry metadata that makes it discoverable after a crash —
    POSIX requires a separate fsync of the containing directory for that.
    Opening a directory read-only and fsyncing its file descriptor is the
    standard, portable way to do this on Linux and Darwin.

    Fails closed (raises) on any I/O error, EXCEPT ``ENOTSUP``/``EINVAL``,
    which some filesystems (rare — e.g. certain network/overlay mounts)
    raise to mean "directory fsync is not a supported operation on this
    filesystem" rather than "this fsync failed." That specific, named
    exception is the one case this function tolerates; every other error
    (including a plain ``OSError`` with no more specific code) propagates.
    """
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)):
            raise
    finally:
        os.close(fd)

# The exact set of launch-form labels `process_match.match_agent_run` can
# return. Kept as a literal tuple here (not re-imported) because
# `process_match` intentionally exposes no importable constant for this set —
# duplicating the three short strings is preferable to reaching into that
# module's private literals.
_ALLOWED_COMMAND_LABELS = ("module", "console", "legacy")


def refresh_hosts_dir(parent_working_dir: Path) -> Path:
    """Return the per-workdir directory holding one marker file per generation."""
    return Path(parent_working_dir) / "daemons" / _REFRESH_HOSTS_DIRNAME


# ---------------------------------------------------------------------------
# Process-start identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessStartIdentity:
    """A mechanically-verifiable "this PID is still the same OS process" proof.

    ``start_ticks`` is an opaque, platform-native, equality-stable process-
    start identity value — immutable for the lifetime of the process it was
    read from, meaningful only for equality against a previously-recorded
    value from the same platform, never arithmetically and never across
    platforms. (It is deliberately NOT described as "monotonic": Darwin's
    value is a wall-clock epoch timestamp, not an assigned counter, and nothing
    in this module relies on ordering between two different PIDs' values —
    only on one PID's value staying exactly the same across two probes of
    the same still-live process.)

    - Linux: kernel-boot-relative start time from ``/proc/<pid>/stat`` field
      22 (parsed after the final ``)`` so an executable name containing
      spaces or parentheses cannot shift field indices — see proc(5)).
    - Darwin: microsecond-precision epoch timestamp
      (``pbi_start_tvsec * 1_000_000 + pbi_start_tvusec``) from
      ``proc_pidinfo(PROC_PIDTBSDINFO)``, a public libproc entry point
      reached via stdlib ``ctypes`` against ``libSystem.dylib`` — present on
      every macOS install, not an optional dependency.

    On any other platform, or if the native source is unavailable/denied,
    :func:`probe_process_start_identity` returns ``None`` rather than accept
    a weaker proxy (e.g. ``psutil`` create_time, which is not a dependency of
    this package, ``ps lstart`` at second granularity, or the process's own
    self-reported start timestamp, which a malicious/buggy process could
    fabricate).
    """

    pid: int
    start_ticks: int


def _parse_linux_stat_start_ticks(raw: str) -> Optional[int]:
    """Pure parser for one ``/proc/<pid>/stat`` line's field-22 start ticks.

    Field 2 is "(comm)" and may itself contain ')' or whitespace, so field
    boundaries are only reliable after the LAST ')' in the line — see
    proc(5). Field 22 (starttime) is the 20th field counting from there
    (fields 3..22 inclusive == index 19 in a 0-based split of the tail).

    Takes the raw line directly with zero ``/proc``/``sys.platform``
    dependency, so the parsing logic itself is testable on any platform.
    """
    close_idx = raw.rfind(")")
    if close_idx == -1:
        return None
    tail = raw[close_idx + 1:].split()
    if len(tail) < 20:
        return None
    try:
        return int(tail[19])
    except ValueError:
        return None


def _probe_linux_start_identity(pid: int) -> Optional[ProcessStartIdentity]:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8", errors="strict")
    except OSError:
        return None
    start_ticks = _parse_linux_stat_start_ticks(raw)
    if start_ticks is None:
        return None
    return ProcessStartIdentity(pid=pid, start_ticks=start_ticks)


class _ProcBsdInfo:
    """Lazily-built ``ctypes.Structure`` subclass for ``struct proc_bsdinfo``.

    Built lazily (not at import time) so importing this module on a
    non-Darwin platform never touches ``ctypes.Structure`` machinery it does
    not need.
    """

    _cls = None

    @classmethod
    def get(cls, ctypes_module):
        if cls._cls is None:
            class _Struct(ctypes_module.Structure):
                _fields_ = [
                    ("pbi_flags", ctypes_module.c_uint32),
                    ("pbi_status", ctypes_module.c_uint32),
                    ("pbi_xstatus", ctypes_module.c_uint32),
                    ("pbi_pid", ctypes_module.c_uint32),
                    ("pbi_ppid", ctypes_module.c_uint32),
                    ("pbi_uid", ctypes_module.c_uint32),
                    ("pbi_gid", ctypes_module.c_uint32),
                    ("pbi_ruid", ctypes_module.c_uint32),
                    ("pbi_rgid", ctypes_module.c_uint32),
                    ("pbi_svuid", ctypes_module.c_uint32),
                    ("pbi_svgid", ctypes_module.c_uint32),
                    ("rfu_1", ctypes_module.c_uint32),
                    ("pbi_comm", ctypes_module.c_char * 16),
                    ("pbi_name", ctypes_module.c_char * 32),
                    ("pbi_nfiles", ctypes_module.c_uint32),
                    ("pbi_pgid", ctypes_module.c_uint32),
                    ("pbi_pjobc", ctypes_module.c_uint32),
                    ("e_tdev", ctypes_module.c_uint32),
                    ("e_tpgid", ctypes_module.c_uint32),
                    ("pbi_nice", ctypes_module.c_int32),
                    ("pbi_start_tvsec", ctypes_module.c_uint64),
                    ("pbi_start_tvusec", ctypes_module.c_uint64),
                ]
            cls._cls = _Struct
        return cls._cls


_PROC_PIDTBSDINFO = 3


def _probe_darwin_start_identity(pid: int) -> Optional[ProcessStartIdentity]:
    """Native Darwin start identity via ``proc_pidinfo(PROC_PIDTBSDINFO)``.

    ``libSystem.dylib`` is the base C library present on every macOS install
    since OS X 10.0 (not an optional framework), and ``proc_pidinfo`` is a
    long-standing public, documented libproc entry point (used internally by
    ``ps``/``lsof``/Activity Monitor) — not a private symbol Apple has any
    incentive to remove. No new third-party dependency, no new build-time C
    extension: pure stdlib ``ctypes`` against a system library that is always
    present.

    ``argtypes``/``restype`` are set explicitly on the bound function object
    (rather than relying on ctypes' default ``int``-for-everything argument
    marshaling) so a 64-bit ``pid_t``/pointer/``size_t`` mismatch would raise
    or misbehave loudly rather than silently truncating/misinterpreting an
    argument on an ABI ctypes' defaults do not model correctly.

    The return value must equal ``sizeof(struct proc_bsdinfo)`` EXACTLY, not
    merely be positive — a partial/truncated read (which ``ret > 0`` alone
    would accept) could otherwise be treated as a complete, trustworthy
    struct. Any other return value (0, negative, or a positive value that
    does not match the expected struct size) returns ``None`` regardless of
    errno — including ``EPERM`` (cross-privilege-boundary PID, e.g. a
    root-owned process). Collapsing ``ESRCH`` ("does not exist") and
    ``EPERM`` ("exists, denied") into the same fail-closed ``None`` is
    deliberate: every marker's PID is a same-user LingTai daemon-host
    process, so a live-but-EPERM'd PID at verification time can only mean
    the original PID was reused by a different-privilege process — exactly
    the "reject, do not adopt" case the anti-PID-reuse invariant exists for.
    Treating EPERM as "alive" would let a reused PID be silently verified
    with no real identity check performed; treating it as "dead" would open
    a window for later adoption once the PID becomes same-user-accessible
    again.

    The struct is read by explicit ``ctypes.Structure`` field list (not raw
    offset arithmetic), with an internal ``pbi_pid == pid`` echo-check AND a
    sanity check on the returned start-time fields
    (``pbi_start_tvsec > 0`` — a real process cannot have started at or
    before the Unix epoch — and ``0 <= pbi_start_tvusec < 1_000_000``, the
    only valid microseconds-within-a-second range): if a future macOS ever
    changed the layout or this code's field list drifted from it, these
    checks would produce a visibly-wrong value (caught here, fails closed)
    rather than a silently wrong identity.
    """
    import ctypes
    import ctypes.util

    lib_path = ctypes.util.find_library("System")
    if not lib_path:
        return None

    struct_cls = _ProcBsdInfo.get(ctypes)

    try:
        lib = ctypes.CDLL(lib_path, use_errno=True)
    except OSError:
        return None

    lib.proc_pidinfo.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
    ]
    lib.proc_pidinfo.restype = ctypes.c_int

    info = struct_cls()
    expected_size = ctypes.sizeof(info)
    ret = lib.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), expected_size)
    if ret != expected_size:
        return None
    if info.pbi_pid != pid:
        return None
    if info.pbi_start_tvsec <= 0:
        return None
    if not (0 <= info.pbi_start_tvusec < 1_000_000):
        return None
    start_ticks = info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec
    return ProcessStartIdentity(pid=pid, start_ticks=start_ticks)


def probe_process_start_identity(pid: int) -> Optional[ProcessStartIdentity]:
    """Return a live PID's start identity, or ``None`` if it cannot be proven.

    Fails closed: any parse failure, missing native source, or unsupported
    platform returns ``None``. Callers MUST treat ``None`` as "identity
    unprovable" and refuse whatever exemption depended on it — never fall
    back to PID-only matching.
    """
    if pid <= 0:
        return None
    if sys.platform == "linux":
        return _probe_linux_start_identity(pid)
    if sys.platform == "darwin":
        return _probe_darwin_start_identity(pid)
    return None


# ---------------------------------------------------------------------------
# Marker schema
# ---------------------------------------------------------------------------

_GENERATION_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


def new_generation() -> str:
    """Return a monotonically-informative, unique generation id.

    Format ``<YYYYMMDD-HHMMSS>-<hex6>`` — sortable by wall time for forensic
    listing, disambiguated by a random suffix so two hosts prepared in the
    same wall-clock second never collide. Not itself a security boundary
    (the nonce is); see :func:`new_nonce`.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(3)}"


def new_nonce() -> str:
    """Return a fresh random 128-bit hex nonce for one marker."""
    return secrets.token_hex(16)


def _canonicalize_working_dir(working_dir: str) -> str:
    """Validate and canonicalize a marker's ``working_dir``.

    A marker's ``working_dir`` is a security-relevant identity field, not a
    user-convenience path: input must already be absolute (reject relative
    input outright rather than silently absolutizing it against an
    unpredictable cwd), and is canonicalized via ``Path(...).resolve()``
    since ``working_dir`` at marker-build time is always a real,
    already-existing agent working directory.
    """
    p = Path(working_dir)
    if not p.is_absolute():
        raise MarkerValidationError(
            "malformed_working_dir", f"working_dir must be absolute, got {working_dir!r}"
        )
    return str(p.resolve())


def _validate_owned_run_ids(owned_run_ids) -> tuple:
    """Validate a run-id set: non-empty strings, no duplicates, no path shapes.

    Run ids are meant to be existing ``DaemonRunDir`` folder-name-shaped ids
    (see ``run_dir.py``'s ``f"{handle}-{timestamp}-{hash6}"`` convention) — a
    plain single path component, never a separator or ``..`` segment.
    """
    ids = tuple(owned_run_ids)
    if not ids:
        raise MarkerValidationError("empty_run_ids", "owned_run_ids must be non-empty")
    for r in ids:
        if not isinstance(r, str) or not r.strip():
            raise MarkerValidationError(
                "malformed_run_ids", f"owned_run_ids entries must be non-empty strings, got {r!r}"
            )
        if os.path.basename(r) != r or r in (".", ".."):
            raise MarkerValidationError(
                "malformed_run_ids", f"owned_run_ids entry is not a plain path component: {r!r}"
            )
    if len(set(ids)) != len(ids):
        raise MarkerValidationError("malformed_run_ids", "owned_run_ids contains duplicates")
    return ids


def _validate_command_label(command_label: str) -> str:
    if not isinstance(command_label, str) or command_label not in _ALLOWED_COMMAND_LABELS:
        raise MarkerValidationError(
            "malformed_command_label",
            f"command_label must be one of {_ALLOWED_COMMAND_LABELS}, got {command_label!r}",
        )
    return command_label


def _validate_pid(pid: int) -> int:
    """Reject non-int, bool, or non-positive pid. ``bool`` is an ``int``
    subclass in Python, so ``isinstance(pid, int)`` alone would let
    ``True``/``False`` through as ``pid=1``/``pid=0``.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise MarkerValidationError("malformed_pid", f"pid must be a positive int, got {pid!r}")
    return pid


def _validate_start_ticks(start_ticks: int) -> int:
    """Reject non-int, bool, or non-positive start_ticks.

    Every real process-start identity source this module uses (Linux
    boot-relative ticks, Darwin microsecond epoch) is strictly positive for a
    genuinely-probed live process; zero can only mean "never actually
    probed," and a negative value can never come from either native source.
    """
    if not isinstance(start_ticks, int) or isinstance(start_ticks, bool) or start_ticks <= 0:
        raise MarkerValidationError(
            "malformed_start_ticks", f"start_ticks must be a positive int, got {start_ticks!r}"
        )
    return start_ticks


def _validate_prepared_at(prepared_at: str) -> str:
    """Require the exact canonical ``_now_iso()`` shape: a parseable UTC
    timestamp in ``%Y-%m-%dT%H:%M:%SZ`` form — no sub-second precision, no
    ``+00:00``-style offset, no other ISO-8601 variant. Byte-exact
    round-tripping through ``strptime``/``strftime`` catches both malformed
    strings and syntactically-valid-but-non-canonical shapes.
    """
    if not isinstance(prepared_at, str):
        raise MarkerValidationError("malformed_prepared_at", f"prepared_at is not a string: {prepared_at!r}")
    try:
        parsed = datetime.strptime(prepared_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise MarkerValidationError(
            "malformed_prepared_at", f"prepared_at is not a canonical UTC timestamp: {prepared_at!r}"
        ) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != prepared_at:
        raise MarkerValidationError(
            "malformed_prepared_at", f"prepared_at is not in canonical form: {prepared_at!r}"
        )
    return prepared_at


@dataclass(frozen=True)
class RefreshHostMarker:
    """Immutable record of one refresh-drain host, as persisted to disk.

    Every field is required at construction; there is no partially-built
    marker. ``owned_run_ids`` is fixed at commit time — a host may never
    accept new ``emanate`` work after :func:`commit_marker`, and no later
    mutation adds run ids to an existing marker file (a later interactive
    parent that creates its own runs gets a NEW generation instead, per the
    disjoint-run-set rule).

    ``generation`` is a unique, human-forensic-readable identifier (sorts
    usefully for a human skimming a directory listing) but is NOT
    ordering-authoritative — a wall-clock-derived string is not guaranteed
    monotonic (NTP step, clock adjustment, same-second collisions). ``sequence``
    is the true ordering field: a durable, exclusively-allocated, strictly
    increasing integer from :func:`allocate_sequence`.
    """

    schema_version: int
    generation: str
    nonce: str
    pid: int
    start_ticks: int
    sequence: int
    command_label: str
    working_dir: str
    owned_run_ids: tuple
    state: str
    prepared_at: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "nonce": self.nonce,
            "pid": self.pid,
            "start_ticks": self.start_ticks,
            "sequence": self.sequence,
            "command_label": self.command_label,
            "working_dir": self.working_dir,
            "owned_run_ids": list(self.owned_run_ids),
            "state": self.state,
            "prepared_at": self.prepared_at,
        }

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def build(
        cls,
        *,
        pid: int,
        start_ticks: int,
        command_label: str,
        working_dir: str,
        owned_run_ids,
    ) -> "RefreshHostMarker":
        """Construct a new, not-yet-committed marker for the current process.

        Validates ``pid``, ``start_ticks``, ``command_label``,
        canonicalizes/validates ``working_dir``, and validates
        ``owned_run_ids`` — ALL before construction and, critically, before
        :func:`allocate_sequence` — an invalid request raises
        :class:`MarkerValidationError` rather than silently storing malformed
        state or burning a durable sequence slot on a request that never
        produces a marker.
        """
        validated_pid = _validate_pid(pid)
        validated_start_ticks = _validate_start_ticks(start_ticks)
        canonical_working_dir = _canonicalize_working_dir(str(working_dir))
        validated_label = _validate_command_label(command_label)
        validated_run_ids = _validate_owned_run_ids(owned_run_ids)
        return cls(
            schema_version=MARKER_SCHEMA_VERSION,
            generation=new_generation(),
            nonce=new_nonce(),
            pid=validated_pid,
            start_ticks=validated_start_ticks,
            sequence=allocate_sequence(canonical_working_dir),
            command_label=validated_label,
            working_dir=canonical_working_dir,
            owned_run_ids=validated_run_ids,
            state="draining",
            prepared_at=cls._now_iso(),
        )


class MarkerValidationError(Exception):
    """A marker file is malformed, stale, or fails identity verification.

    Callers MUST treat this the same as "no marker" for duplicate-guard
    exemption purposes — never grant an exemption on the strength of a marker
    that raised this. The ``reason`` attribute is a short machine-stable tag
    for logging/tests, distinct from the human message.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class CommitAmbiguousError(Exception):
    """``commit_marker`` cannot durably confirm whether its target exists.

    Raised ONLY when a post-link durability failure occurs AND the rollback
    of this call's own just-created target (or the rollback's own
    confirming directory-fsync) also fails. In that state the on-disk
    authority of ``target_path`` is genuinely unknown — it may be a fully
    valid, durably-committed ``"draining"`` marker, or a half-rolled-back
    remnant, or absent — and this module cannot resolve that ambiguity by
    itself.

    This is deliberately NOT a :class:`MarkerValidationError`. Callers MUST
    NOT treat this the same as "no marker exists" (unlike
    ``MarkerValidationError``, which callers are required to treat that
    way) and MUST NOT silently resume ordinary interactive operation as if
    commit had cleanly failed. A caller that catches this must escalate —
    e.g. a future higher-level refresh-host lifecycle would need to
    independently re-probe ``target_path`` (via :func:`load_marker`) before
    deciding whether draining authority was actually granted — rather than
    assuming either outcome. This exception's existence and contract are
    documented now for that later wiring; this run does not perform any
    such escalation itself (out of scope per the bounded-foundation rules).

    ``target_path`` is the path this call attempted to publish.
    ``rollback_attempted``/``rollback_succeeded`` record what recovery was
    tried before giving up, for logging/diagnostics.
    """

    def __init__(self, target_path, *, rollback_attempted: bool, rollback_succeeded: bool, cause: BaseException):
        super().__init__(
            f"commit_marker could not durably confirm target state for {target_path!r} "
            f"after a post-link durability failure "
            f"(rollback_attempted={rollback_attempted}, rollback_succeeded={rollback_succeeded}): {cause!r}"
        )
        self.target_path = target_path
        self.rollback_attempted = rollback_attempted
        self.rollback_succeeded = rollback_succeeded
        self.cause = cause


_REQUIRED_MARKER_KEYS = (
    "schema_version", "generation", "nonce", "pid", "start_ticks", "sequence",
    "command_label", "working_dir", "owned_run_ids", "state", "prepared_at",
)


def _parse_marker_dict(data: object, *, source: str) -> RefreshHostMarker:
    """Strictly, non-coercively validate a raw marker dict.

    Every type check rejects on wrong type rather than coercing (no
    ``str(x)``/duck-typed acceptance) — a hand-edited or corrupted-on-disk
    file must be rejected exactly as strictly as a bad ``build()`` call.
    The required-key check is an EXACT set comparison, so an unexpected
    extra key is rejected too (a future schema-version bump is the only
    sanctioned way to add a field).
    """
    if not isinstance(data, dict):
        raise MarkerValidationError("not_a_dict", f"{source}: marker is not a JSON object")
    if data.get("schema_version") != MARKER_SCHEMA_VERSION:
        raise MarkerValidationError(
            "schema_mismatch",
            f"{source}: schema_version={data.get('schema_version')!r}, expected {MARKER_SCHEMA_VERSION}",
        )
    missing = [k for k in _REQUIRED_MARKER_KEYS if k not in data]
    if missing:
        raise MarkerValidationError("missing_fields", f"{source}: missing fields {missing}")
    extra = set(data.keys()) - set(_REQUIRED_MARKER_KEYS)
    if extra:
        raise MarkerValidationError("unexpected_fields", f"{source}: unexpected fields {sorted(extra)}")

    generation = data["generation"]
    if not isinstance(generation, str) or not _GENERATION_RE.match(generation):
        raise MarkerValidationError("malformed_generation", f"{source}: malformed generation {generation!r}")

    nonce = data["nonce"]
    if not isinstance(nonce, str) or len(nonce) != 32:
        raise MarkerValidationError("malformed_nonce", f"{source}: malformed nonce")
    try:
        int(nonce, 16)
    except ValueError:
        raise MarkerValidationError("malformed_nonce", f"{source}: nonce is not valid hex") from None

    pid = _validate_pid(data["pid"])

    start_ticks = _validate_start_ticks(data["start_ticks"])

    sequence = data["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise MarkerValidationError("malformed_sequence", f"{source}: malformed sequence {sequence!r}")

    command_label = data["command_label"]
    if not isinstance(command_label, str) or command_label not in _ALLOWED_COMMAND_LABELS:
        raise MarkerValidationError(
            "malformed_command_label", f"{source}: malformed command_label {command_label!r}"
        )

    working_dir = data["working_dir"]
    if not isinstance(working_dir, str):
        raise MarkerValidationError("malformed_working_dir", f"{source}: working_dir is not a string")
    canonical_working_dir = _canonicalize_working_dir(working_dir)
    if canonical_working_dir != working_dir:
        # build() always writes an already-canonical, already-resolved path;
        # a marker on disk whose working_dir differs from its own
        # canonicalization is itself evidence of a malformed/tampered file.
        raise MarkerValidationError(
            "malformed_working_dir", f"{source}: working_dir {working_dir!r} is not canonical"
        )

    owned_run_ids = data["owned_run_ids"]
    if not isinstance(owned_run_ids, list) or not all(isinstance(r, str) for r in owned_run_ids):
        raise MarkerValidationError("malformed_run_ids", f"{source}: malformed owned_run_ids")
    validated_run_ids = _validate_owned_run_ids(owned_run_ids)

    state = data["state"]
    if not isinstance(state, str):
        raise MarkerValidationError("malformed_state", f"{source}: state is not a string")
    if state != "draining":
        # This foundation parses and grants authority to exactly ONE state:
        # the single currently-defined "draining" (active, exemption-eligible)
        # state. Anything else — including a hypothetical future
        # host-exiting/host-lost archive state this schema does not yet
        # define — is rejected here, not accepted-as-a-record-then-denied-
        # exemption-separately. A later schema version that wants to model
        # non-authority-granting archive records is free to do so, but that
        # is out of scope for this primitives-only foundation.
        raise MarkerValidationError("not_draining", f"{source}: state={state!r}, not 'draining'")

    prepared_at = _validate_prepared_at(data["prepared_at"])

    return RefreshHostMarker(
        schema_version=MARKER_SCHEMA_VERSION,
        generation=generation,
        nonce=nonce,
        pid=pid,
        start_ticks=start_ticks,
        sequence=sequence,
        command_label=command_label,
        working_dir=canonical_working_dir,
        owned_run_ids=validated_run_ids,
        state=state,
        prepared_at=prepared_at,
    )


def allocate_sequence(parent_working_dir) -> int:
    """Return the next durable, exclusively-allocated, strictly-ordered integer.

    Implemented as one zero-byte file per allocated integer under
    ``daemons/.refresh-hosts/.sequence/<N>``, claimed via
    ``os.open(path, O_CREAT | O_EXCL | O_WRONLY)`` — no dependency on
    ``filelock`` (zero usage precedent elsewhere in ``kernel/``/``tools/``).

    Lists existing ``.sequence/*`` filenames to find the current highest
    claimed integer, then attempts ``N+1``, ``N+2``, ... , retrying on
    ``FileExistsError`` (another concurrent allocator raced ahead) until one
    exclusive-create succeeds. The highest claimed file on disk is the source
    of truth (not an in-memory counter), so this is durable across process
    restarts and safe under real concurrent processes — not just threads —
    because ``O_EXCL`` exclusivity is enforced by the kernel/filesystem.

    Matches the accepted PREPARE contract's durability requirement: the
    claim file's content (empty, but the fsync still forces its inode/size
    metadata to stable storage) is fsynced before close, and the
    ``.sequence`` directory itself is fsynced afterward so the new
    directory entry survives a crash immediately after this call returns —
    not just eventually, on the next unrelated flush.
    """
    seq_dir = refresh_hosts_dir(parent_working_dir) / _SEQUENCE_DIRNAME
    seq_dir.mkdir(parents=True, exist_ok=True)
    while True:
        existing = [int(p.name) for p in seq_dir.iterdir() if p.name.isdigit()]
        candidate = (max(existing) if existing else 0) + 1
        candidate_path = seq_dir / str(candidate)
        try:
            fd = os.open(str(candidate_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(seq_dir)
        return candidate


def commit_marker(parent_working_dir: Path, marker: RefreshHostMarker) -> Path:
    """Exclusively persist a new marker. Raises on any pre-commit failure.

    Per the required ordering (marker/tag commit happens BEFORE any
    interactive-liveness teardown), a raised exception here means ordinary
    interactive operation must continue unchanged — this function has no
    partial-success mode.

    The marker is round-tripped through :func:`_parse_marker_dict` (the same
    strict validator that will later re-parse it from disk) before any file
    is touched, so a marker that would fail to load can never be written in
    the first place.

    ``parent_working_dir`` is canonicalized and compared for EXACT equality
    against ``marker.working_dir`` before any directory or file is created —
    a marker built for a different working directory must never be
    persisted under this ``parent_working_dir``'s refresh-hosts tree, even
    though :func:`load_marker` would later reject it on read. Binding
    authority to the destination at commit time (not merely at load time)
    closes the window where a wrong-workdir marker sits on disk, readable
    and iterable by :func:`iter_marker_paths`, until something happens to
    load it with the right ``expected_working_dir``.

    Publication is truly exclusive: the JSON is written to a uniquely-named
    sibling temp file first, then ``os.link`` (hard-link) is used to publish
    it to the target path. Unlike ``os.replace``, ``os.link`` fails with
    ``FileExistsError`` if the target already exists and NEVER overwrites —
    this closes the TOCTOU window a ``target.exists()`` pre-check plus an
    unconditional rename would leave open under concurrent same-generation
    writers.

    Matches the accepted PREPARE contract's durability requirement (the
    marker must be durably committed before any interactive-liveness
    teardown, not merely written to a page cache that a crash could lose):
    the temp file's complete bytes are fsynced before the ``os.link``
    (``atomic_write_json(..., fsync=True)``), and the containing directory
    is fsynced after a successful link so the new marker's directory entry
    itself survives a crash immediately after this call returns.

    If that post-link directory fsync fails, the link it was meant to make
    durable is still exclusively ours (no other caller could have raced onto
    this exact ``target`` path — only one ``os.link`` can ever win a given
    generation). This function therefore rolls back ONLY that exact
    self-created target (never any other file) and fsyncs the directory
    again to durably confirm the rollback, before re-raising the original
    durability error — restoring the "no partial-success mode" contract:
    a caller that sees an exception must be able to trust no marker was
    left behind for THIS call, exactly as before this fix. If the rollback
    itself (the unlink, or its confirming fsync) also fails, this function
    raises :class:`CommitAmbiguousError` instead — a distinct, non-
    :class:`MarkerValidationError` exception a caller MUST NOT treat as
    "no marker exists," because in that state this function genuinely
    cannot tell whether ``target`` durably exists or not.
    """
    _parse_marker_dict(marker.to_dict(), source="<pre-commit validation>")
    canonical_parent = str(Path(parent_working_dir).resolve())
    if marker.working_dir != canonical_parent:
        raise MarkerValidationError(
            "working_dir_mismatch",
            f"marker working_dir {marker.working_dir!r} != commit target {canonical_parent!r}",
        )
    hosts_dir = refresh_hosts_dir(canonical_parent)
    hosts_dir.mkdir(parents=True, exist_ok=True)
    target = hosts_dir / f"{marker.generation}.json"
    # Each call gets its own temp path (pid + random suffix), never shared
    # across concurrent callers — including multiple threads in one process
    # racing to commit the SAME generation, which is exactly what this
    # function must handle correctly.
    tmp = atomic_write_json(
        hosts_dir / f".{marker.generation}.{os.getpid()}.{secrets.token_hex(6)}.tmp",
        marker.to_dict(),
        ensure_ascii=False,
        indent=2,
        fsync=True,
    )
    try:
        os.link(str(tmp), str(target))
    except FileExistsError:
        raise MarkerValidationError(
            "generation_collision", f"marker already exists for generation {marker.generation}"
        ) from None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        _fsync_dir(hosts_dir)
    except OSError as durability_error:
        # This call exclusively created `target` via the os.link above — no
        # other caller can legitimately hold it for this generation — so
        # unlinking it here can only ever remove OUR OWN just-created file,
        # never another writer's. This is the one narrow, explicitly
        # authorized exception to the module's no-delete posture.
        try:
            target.unlink()
            _fsync_dir(hosts_dir)
        except OSError as rollback_error:
            raise CommitAmbiguousError(
                target, rollback_attempted=True, rollback_succeeded=False, cause=rollback_error
            ) from durability_error
        if target.exists():
            raise CommitAmbiguousError(
                target, rollback_attempted=True, rollback_succeeded=False, cause=durability_error
            ) from durability_error
        raise
    return target


def load_marker(path: Path, *, expected_working_dir) -> RefreshHostMarker:
    """Load and validate one marker file. Raises :class:`MarkerValidationError`.

    ``expected_working_dir`` mechanically ties "the directory this marker was
    found under" to "the directory this marker claims authority over" — the
    one API boundary that matters, rather than trusting callers to remember
    to cross-check it themselves. Both sides are canonicalized identically
    before comparison.

    Beyond that content-level check, this function ALSO binds the marker's
    embedded ``generation`` to the exact filesystem location it was loaded
    from: ``path`` is resolved (``Path(path).resolve()``, following any
    symlinks) and its resolved parent directory must equal
    ``refresh_hosts_dir(expected_working_dir).resolve()`` exactly — this
    rejects a marker loaded from any directory that does not resolve to the
    real canonical hosts directory, but a directory that IS merely a
    symlink/alias TO the real hosts directory is accepted (resolution
    collapses the alias to the same canonical path, exactly as ``path``'s
    own resolution would for the real directory itself — a symlinked path
    to the correct location is not treated as a distinct, rejectable
    location; only a path that resolves somewhere ELSE is). ``path``'s
    (resolved) filename must also be exactly ``f"{marker.generation}.json"``
    (rejecting a marker whose content is genuine but which was found under
    a filename that does not match its own embedded generation — e.g. a
    copy, or a file placed under a stale/malicious name). Both checks fire
    only after content parsing succeeds, so a malformed file is still
    reported via its content-level reason first. This keeps
    :func:`commit_marker`'s own naming convention (``<generation>.json``
    directly under ``refresh_hosts_dir(...)``, never anything else) as the
    one shape :func:`iter_marker_paths` and ``load_marker`` agree is ever a
    valid marker location, regardless of which path (direct or through a
    symlink) was used to reach it.
    """
    try:
        data = read_json(path)
    except (OSError, ValueError) as e:
        raise MarkerValidationError("unreadable", f"{path}: {e}") from e
    marker = _parse_marker_dict(data, source=str(path))
    canonical_expected = str(Path(expected_working_dir).resolve())
    if marker.working_dir != canonical_expected:
        raise MarkerValidationError(
            "working_dir_mismatch",
            f"{path}: marker working_dir {marker.working_dir!r} != expected {canonical_expected!r}",
        )
    resolved_path = Path(path).resolve()
    expected_parent = refresh_hosts_dir(canonical_expected).resolve()
    if resolved_path.parent != expected_parent:
        raise MarkerValidationError(
            "wrong_directory",
            f"{path}: resolves under {resolved_path.parent!r}, expected exactly {expected_parent!r}",
        )
    expected_filename = f"{marker.generation}.json"
    if resolved_path.name != expected_filename:
        raise MarkerValidationError(
            "filename_generation_mismatch",
            f"{path}: filename {resolved_path.name!r} does not match embedded generation "
            f"(expected {expected_filename!r})",
        )
    return marker


def iter_marker_paths(parent_working_dir: Path):
    """Yield every marker file path under the refresh-hosts directory, sorted.

    Only ``*.json`` marker files — the ``.sequence/`` allocator directory and
    any in-flight ``.<generation>.<pid>.tmp`` publish artifacts are excluded
    by the glob pattern itself (neither matches ``*.json``).
    """
    hosts_dir = refresh_hosts_dir(parent_working_dir)
    if not hosts_dir.is_dir():
        return
    for p in sorted(hosts_dir.glob("*.json")):
        yield p


def verify_marker_live(marker: RefreshHostMarker) -> bool:
    """True only if ``marker``'s PID is alive, has the claimed start identity,
    AND still matches the canonical agent-run command shape for its working
    dir UNDER THE EXACT RECORDED LAUNCH FORM.

    This is the sole predicate a duplicate guard, a watcher stale-cleanup, or
    a control-plane router may use to decide a host is real. It requires ALL
    THREE of: live PID, matching start identity (not just "some process is
    running at this PID"), and an EXACT command-label match (not just "some
    process with this start identity matches SOME launch form") — any
    single-signal shortcut (PID alone, start-identity alone, a stale
    heartbeat, a user-controlled boolean, or accepting any non-``None``
    launch form regardless of which one) is explicitly rejected by the
    accepted design this implements.
    """
    identity = probe_process_start_identity(marker.pid)
    if identity is None:
        return False
    if identity.start_ticks != marker.start_ticks:
        return False
    cmdline = _read_cmdline(marker.pid)
    if cmdline is None:
        return False
    observed_label = match_agent_run(cmdline, marker.working_dir)
    return observed_label == marker.command_label


def _read_cmdline(pid: int) -> Optional[str]:
    """Best-effort flat command-line string for ``pid``, or ``None``.

    Deliberately mirrors the existing duplicate-guard's ``ps``-based signal
    shape (a flat string, not argv) rather than introducing a second command
    representation — see ``cli.py:_check_duplicate_process`` and
    ``kernel/process_match.py``'s documented "flat string" limitation, which
    this function inherits rather than silently strengthens.
    """
    if sys.platform == "linux":
        cmdline_path = Path("/proc") / str(pid) / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            pass
        else:
            parts = raw.split(b"\x00")
            text = b" ".join(p for p in parts if p).decode("utf-8", errors="replace")
            if text:
                return text
    try:
        import subprocess
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            stderr=subprocess.DEVNULL, text=True,
        )
    except Exception:
        return None
    out = out.strip()
    return out or None


# ---------------------------------------------------------------------------
# Verified-host discovery — the sole per-PID exemption predicate for callers
# ---------------------------------------------------------------------------


def verified_refresh_host_pids(parent_working_dir: Path) -> frozenset[int]:
    """Return the set of PIDs currently authorized as a verified refresh host
    for ``parent_working_dir``.

    This is the ONE discovery pass a caller (the CLI duplicate guard, the
    detached refresh watcher) may use to decide whether a same-workdir PID
    is exempt from ordinary duplicate-process/stale-cleanup treatment.
    Fail-closed and best-effort throughout: every marker this directory
    contains is loaded and validated independently, and any single marker's
    failure (malformed, wrong directory/filename/workdir, stale schema,
    dead PID, start-identity mismatch, command-label mismatch) excludes
    only that marker — it never raises out to the caller and never causes
    a different marker's valid claim to be rejected. This extends to
    enumeration itself: an inability to even list the hosts directory
    (permission error, transient I/O error, or any other unexpected
    exception from :func:`iter_marker_paths`) means no PID is authorized —
    "cannot enumerate" is exactly as fail-closed as "cannot validate."

    Ambiguity is fail-closed at the PID granularity, not the whole
    directory: if two (or more) distinct valid, live markers name the SAME
    PID, that PID is ambiguous and is excluded from the returned set
    entirely (a duplicate claim on one PID must never authorize it), but
    every OTHER PID with exactly one unambiguous valid live marker is still
    returned. This mirrors :func:`verify_marker_live`'s own fail-closed
    posture: "cannot prove clean" always means "not authorized" here, never
    "crash" and never "silently pick one."

    A marker naming a candidate PID does not by itself authorize that PID
    for any workdir/PID other than its own exact recorded claim — this
    function does not accept heartbeat freshness, PID-only liveness, a
    marker boolean, or any weaker signal; it is a thin fan-out over
    :func:`iter_marker_paths`, :func:`load_marker`, and
    :func:`verify_marker_live`, the same three accepted primitives, never a
    duplicate or weakened re-implementation of their logic.
    """
    try:
        marker_paths = list(iter_marker_paths(parent_working_dir))
    except Exception:
        # Enumeration itself failed (e.g. PermissionError/OSError stat-ing
        # or globbing the hosts directory) — no positive authorization is
        # possible, so the empty set is returned rather than raising out to
        # the CLI duplicate guard or the detached watcher.
        return frozenset()

    live_by_pid: dict[int, RefreshHostMarker] = {}
    ambiguous_pids: set[int] = set()
    for path in marker_paths:
        try:
            marker = load_marker(path, expected_working_dir=parent_working_dir)
        except Exception:
            # Any load-time failure — not only the documented
            # MarkerValidationError reasons, but also an unexpected
            # filesystem error surfaced during load (e.g. a symlink-
            # resolution I/O error) — excludes only this one marker.
            continue
        try:
            if not verify_marker_live(marker):
                continue
        except Exception:
            # Any unexpected observation error (e.g. a transient `ps`/proc
            # read failure) is exactly the "cannot prove clean" case this
            # module's contract requires callers to treat as absence of a
            # valid marker — never a positive authorization, never a raise
            # that could abort CLI boot or watcher protection.
            continue
        if marker.pid in live_by_pid:
            ambiguous_pids.add(marker.pid)
        else:
            live_by_pid[marker.pid] = marker
    return frozenset(pid for pid in live_by_pid if pid not in ambiguous_pids)


def is_verified_refresh_host(pid: int, parent_working_dir: Path) -> bool:
    """True only if ``pid`` is exactly, unambiguously authorized as a live
    refresh-drain host for ``parent_working_dir``.

    The sole per-PID predicate the CLI duplicate guard and the detached
    refresh watcher may use before exempting a same-workdir candidate PID
    from ordinary fatal-duplicate/stale-cleanup treatment. Delegates
    entirely to :func:`verified_refresh_host_pids` — see that function's
    docstring for the exact fail-closed/ambiguity semantics; this wrapper
    adds no additional logic of its own so there is exactly one place that
    logic can drift.
    """
    return pid in verified_refresh_host_pids(parent_working_dir)


# ---------------------------------------------------------------------------
# ExecutionOwner — the durable per-run ownership binding tagged onto each
# owned run's daemon.json, so a run record can prove which exact marker
# claims it (generation + nonce + PID + start identity + sequence), distinct
# from the marker file itself (which lists run ids but is not embedded into
# each run's own record). ``parent_pid`` on a run record remains provenance
# ("who created this run") — ``execution_owner`` is the current-authority
# binding a drain-host/successor protocol actually reasons about.
# ---------------------------------------------------------------------------

_REQUIRED_EXECUTION_OWNER_KEYS = (
    "schema_version", "generation", "nonce", "pid", "start_ticks", "sequence",
    "owned_run_ids",
)


@dataclass(frozen=True)
class ExecutionOwner:
    """Immutable per-run ownership tag, tagged onto exactly one run's
    ``daemon.json`` by :func:`tag_owned_runs` as part of committing one
    :class:`RefreshHostMarker`. Carries enough of the marker's own identity
    (generation, nonce, PID, start identity, sequence) plus the full
    ``owned_run_ids`` set for that marker to let a later reader prove, from
    the tag alone, that this exact run belongs to this exact marker — not
    merely "some marker with a plausible-looking generation string."
    """

    schema_version: int
    generation: str
    nonce: str
    pid: int
    start_ticks: int
    sequence: int
    owned_run_ids: tuple

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "nonce": self.nonce,
            "pid": self.pid,
            "start_ticks": self.start_ticks,
            "sequence": self.sequence,
            "owned_run_ids": list(self.owned_run_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionOwner":
        missing = [k for k in _REQUIRED_EXECUTION_OWNER_KEYS if k not in data]
        if missing:
            raise MarkerValidationError(
                "execution_owner_missing_fields", f"execution_owner missing fields {missing}"
            )
        extra = set(data.keys()) - set(_REQUIRED_EXECUTION_OWNER_KEYS)
        if extra:
            raise MarkerValidationError(
                "execution_owner_unexpected_fields",
                f"execution_owner unexpected fields {sorted(extra)}",
            )
        owned_run_ids = data["owned_run_ids"]
        if not isinstance(owned_run_ids, list) or not all(isinstance(r, str) for r in owned_run_ids):
            raise MarkerValidationError(
                "execution_owner_malformed_run_ids", "execution_owner owned_run_ids malformed"
            )
        return cls(
            schema_version=data["schema_version"],
            generation=data["generation"],
            nonce=data["nonce"],
            pid=data["pid"],
            start_ticks=data["start_ticks"],
            sequence=data["sequence"],
            owned_run_ids=tuple(owned_run_ids),
        )

    @classmethod
    def from_marker(cls, marker: RefreshHostMarker) -> "ExecutionOwner":
        return cls(
            schema_version=marker.schema_version,
            generation=marker.generation,
            nonce=marker.nonce,
            pid=marker.pid,
            start_ticks=marker.start_ticks,
            sequence=marker.sequence,
            owned_run_ids=marker.owned_run_ids,
        )

    def proves_membership_of(self, run_id: str, marker: RefreshHostMarker) -> bool:
        """True only if this tag was produced by exactly ``marker`` (same
        generation AND nonce — the nonce is the actual anti-forgery/anti-
        reuse identity; generation alone is only a human-forensic label)
        AND ``run_id`` is in the recorded owned set. A tag whose generation/
        nonce does not match the marker currently being checked against is
        never proof of anything for that marker — this is what stops a
        later host from silently inheriting an earlier host's run via a
        stale or mismatched tag.
        """
        return (
            self.generation == marker.generation
            and self.nonce == marker.nonce
            and run_id in self.owned_run_ids
        )


class OwnerTaggingAmbiguousError(Exception):
    """:func:`tag_owned_runs` cannot durably confirm every run's tag state.

    Raised only when a mid-sequence tag-write failure's own rollback (of an
    earlier, already-successfully-tagged run in the same call) also fails.
    In that state at least one run's on-disk ``execution_owner`` is unknown
    — it may still carry the tag, or the rollback may have partially
    applied — and the caller MUST NOT proceed to :func:`commit_marker`,
    because doing so would durably publish a marker claiming a run set this
    module can no longer prove is cleanly untagged/tagged. Mirrors
    :class:`CommitAmbiguousError`'s contract: this is deliberately not a
    :class:`MarkerValidationError`; callers must not treat it as "no tags
    applied."
    """

    def __init__(self, run_id: str, *, cause: BaseException):
        super().__init__(
            f"tag_owned_runs could not durably confirm execution_owner state "
            f"for run {run_id!r} after a rollback failure: {cause!r}"
        )
        self.run_id = run_id
        self.cause = cause


def tag_owned_runs(marker: RefreshHostMarker, run_dirs: dict) -> dict:
    """Atomically tag every run in ``marker.owned_run_ids`` with an
    :class:`ExecutionOwner` derived from ``marker``, all-or-nothing.

    ``run_dirs`` maps each owned run id to an object exposing
    ``set_execution_owner(dict)`` and ``clear_execution_owner_on_rollback()``
    (the real caller passes ``DaemonRunDir`` instances; tests may pass a
    lighter stand-in — this function only depends on that two-method
    surface, not on the concrete run-dir implementation).

    On success, returns ``{run_id: True}`` for every owned run id. On any
    tag-write failure, rolls back every run this call already tagged
    successfully (in the same order they were tagged, most-recent first),
    then re-raises the original exception — so a caller sees the same
    exception type/message it would have seen without any rollback logic,
    and no run is left claiming membership in a marker whose tagging never
    completed. If a rollback itself fails, raises
    :class:`OwnerTaggingAmbiguousError` instead (never continues, never
    silently drops the ambiguity) — this must always propagate, so a caller
    that catches it must not proceed to :func:`commit_marker`.

    A published marker plus a mismatched/incomplete run-tag set must never
    authorize execution — this function is the reason that invariant holds:
    :func:`commit_marker` (called strictly after this returns cleanly) is
    the only durable "the marker exists" signal, and it is never reached
    unless every owned run was successfully tagged first.
    """
    owner = ExecutionOwner.from_marker(marker)
    owner_dict = owner.to_dict()
    tagged_run_ids: list = []
    for run_id in marker.owned_run_ids:
        run_dir = run_dirs[run_id]
        try:
            run_dir.set_execution_owner(owner_dict)
        except Exception as tag_error:
            for rolled_back_id in reversed(tagged_run_ids):
                rolled_back_dir = run_dirs[rolled_back_id]
                try:
                    rolled_back_dir.clear_execution_owner_on_rollback()
                except Exception as rollback_error:
                    raise OwnerTaggingAmbiguousError(
                        rolled_back_id, cause=rollback_error
                    ) from tag_error
            raise
        tagged_run_ids.append(run_id)
    return {run_id: True for run_id in tagged_run_ids}


# ---------------------------------------------------------------------------
# Durable control request/ack plane — host-private, generation-bound files
# under ``daemons/.refresh-hosts/<generation>/control/{requests,acks}/``.
# Exclusive creation for both requests and acks gives idempotent duplicate
# handling "for free" at the filesystem layer: a second ``write_*`` call for
# the same id always raises ``FileExistsError`` rather than overwriting, so
# a retrying caller can distinguish "never sent"/"already sent, poll for the
# result" without any additional bookkeeping.
# ---------------------------------------------------------------------------

_CONTROL_DIRNAME = "control"
_CONTROL_REQUESTS_DIRNAME = "requests"
_CONTROL_ACKS_DIRNAME = "acks"

_ALLOWED_CONTROL_OPERATIONS = ("ask", "reclaim", "timeout")
_ALLOWED_ACK_STATUSES = ("accepted", "pending", "already-terminal", "rejected", "host-lost")

_REQUIRED_CONTROL_REQUEST_KEYS = (
    "schema_version", "request_id", "generation", "nonce", "target_run_ids",
    "operation", "payload", "requester_pid", "requester_start_ticks",
    "created_at", "deadline_at",
)
_REQUIRED_CONTROL_ACK_KEYS = (
    "schema_version", "request_id", "generation", "target_run_ids", "status",
    "responded_at", "detail",
)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def control_dir(parent_working_dir: Path, generation: str) -> Path:
    """Return the control-plane directory for one marker's ``generation``.

    Nested one level under that generation's own subdirectory (not directly
    under ``.refresh-hosts/``) so every control artifact for a given host is
    trivially generation-scoped by path alone — two hosts' control planes
    can never collide, and a caller can never accidentally address the
    wrong generation's requests/acks by a path-construction mistake.
    """
    return refresh_hosts_dir(parent_working_dir) / generation / _CONTROL_DIRNAME


def _requests_dir(parent_working_dir: Path, generation: str) -> Path:
    return control_dir(parent_working_dir, generation) / _CONTROL_REQUESTS_DIRNAME


def _acks_dir(parent_working_dir: Path, generation: str) -> Path:
    return control_dir(parent_working_dir, generation) / _CONTROL_ACKS_DIRNAME


def _validate_request_id(request_id: str) -> str:
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.match(request_id):
        raise MarkerValidationError(
            "malformed_request_id", f"request_id is not a plain safe token: {request_id!r}"
        )
    return request_id


@dataclass(frozen=True)
class ControlRequest:
    """One durable, host-private control request under a marker generation.

    Every field required; strict, non-coercive validation on both
    construction and load, mirroring :class:`RefreshHostMarker`'s posture —
    a hand-edited or corrupted file must be rejected exactly as strictly as
    a malformed constructor call.
    """

    schema_version: int
    request_id: str
    generation: str
    nonce: str
    target_run_ids: tuple
    operation: str
    payload: dict
    requester_pid: int
    requester_start_ticks: int
    created_at: str
    deadline_at: str

    def __post_init__(self):
        _validate_request_id(self.request_id)
        if self.operation not in _ALLOWED_CONTROL_OPERATIONS:
            raise MarkerValidationError(
                "malformed_operation",
                f"operation must be one of {_ALLOWED_CONTROL_OPERATIONS}, got {self.operation!r}",
            )
        if not isinstance(self.target_run_ids, tuple) or not self.target_run_ids:
            raise MarkerValidationError(
                "empty_target_run_ids", "target_run_ids must be a non-empty tuple"
            )
        if not isinstance(self.payload, dict):
            raise MarkerValidationError("malformed_payload", "payload must be a dict")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "generation": self.generation,
            "nonce": self.nonce,
            "target_run_ids": list(self.target_run_ids),
            "operation": self.operation,
            "payload": self.payload,
            "requester_pid": self.requester_pid,
            "requester_start_ticks": self.requester_start_ticks,
            "created_at": self.created_at,
            "deadline_at": self.deadline_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ControlRequest":
        if not isinstance(data, dict):
            raise MarkerValidationError("not_a_dict", "control request is not a JSON object")
        missing = [k for k in _REQUIRED_CONTROL_REQUEST_KEYS if k not in data]
        if missing:
            raise MarkerValidationError(
                "missing_fields", f"control request missing fields {missing}"
            )
        extra = set(data.keys()) - set(_REQUIRED_CONTROL_REQUEST_KEYS)
        if extra:
            raise MarkerValidationError(
                "unexpected_fields", f"control request unexpected fields {sorted(extra)}"
            )
        target_run_ids = data["target_run_ids"]
        if not isinstance(target_run_ids, list) or not all(isinstance(r, str) for r in target_run_ids):
            raise MarkerValidationError("malformed_run_ids", "target_run_ids malformed")
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            generation=data["generation"],
            nonce=data["nonce"],
            target_run_ids=tuple(target_run_ids),
            operation=data["operation"],
            payload=data["payload"],
            requester_pid=data["requester_pid"],
            requester_start_ticks=data["requester_start_ticks"],
            created_at=data["created_at"],
            deadline_at=data["deadline_at"],
        )


@dataclass(frozen=True)
class ControlAck:
    """One durable, host-published ack, bound to the same request/generation/
    run set it responds to. ``status`` is always exactly one of the five
    honest outcomes — an ack is never a bare boolean, and ``accepted`` is
    never conflated with "the work is already done" (see
    :data:`_ALLOWED_ACK_STATUSES` and the module docstring)."""

    schema_version: int
    request_id: str
    generation: str
    target_run_ids: tuple
    status: str
    responded_at: str
    detail: dict

    def __post_init__(self):
        _validate_request_id(self.request_id)
        if self.status not in _ALLOWED_ACK_STATUSES:
            raise MarkerValidationError(
                "malformed_status",
                f"status must be one of {_ALLOWED_ACK_STATUSES}, got {self.status!r}",
            )
        if not isinstance(self.target_run_ids, tuple) or not self.target_run_ids:
            raise MarkerValidationError(
                "empty_target_run_ids", "target_run_ids must be a non-empty tuple"
            )
        if not isinstance(self.detail, dict):
            raise MarkerValidationError("malformed_detail", "detail must be a dict")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "generation": self.generation,
            "target_run_ids": list(self.target_run_ids),
            "status": self.status,
            "responded_at": self.responded_at,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ControlAck":
        if not isinstance(data, dict):
            raise MarkerValidationError("not_a_dict", "control ack is not a JSON object")
        missing = [k for k in _REQUIRED_CONTROL_ACK_KEYS if k not in data]
        if missing:
            raise MarkerValidationError("missing_fields", f"control ack missing fields {missing}")
        extra = set(data.keys()) - set(_REQUIRED_CONTROL_ACK_KEYS)
        if extra:
            raise MarkerValidationError(
                "unexpected_fields", f"control ack unexpected fields {sorted(extra)}"
            )
        target_run_ids = data["target_run_ids"]
        if not isinstance(target_run_ids, list) or not all(isinstance(r, str) for r in target_run_ids):
            raise MarkerValidationError("malformed_run_ids", "target_run_ids malformed")
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            generation=data["generation"],
            target_run_ids=tuple(target_run_ids),
            status=data["status"],
            responded_at=data["responded_at"],
            detail=data["detail"],
        )


def write_control_request(parent_working_dir: Path, request: ControlRequest) -> Path:
    """Exclusively publish ``request`` under its own generation's
    ``control/requests/`` directory. Raises ``FileExistsError`` (never
    overwrites) if ``request.request_id`` was already submitted for this
    generation — the caller's idempotent-retry contract: a second attempt
    to submit the *same* id must be treated as "already sent, go read the
    ack," never as a silent no-op success or an unconditional resend.

    Also enforces the path/generation binding eagerly, matching
    :func:`commit_marker`'s own "bind authority to the destination, not
    merely at load time" posture: ``request.generation`` must name a real,
    loadable marker committed under ``parent_working_dir`` (not merely a
    syntactically well-formed generation string) before anything is
    written — a request naming a generation with no corresponding marker
    file has nowhere honest to be filed, since no host will ever poll a
    generation that was never actually committed. This does NOT require
    the marker to still be *live* (a dead/stale host is the request
    dispatcher's problem to detect via a timed-out ack, not this
    primitive's) — only that it durably exists. Raises
    :class:`MarkerValidationError` if it does not.
    """
    marker_path = refresh_hosts_dir(parent_working_dir) / f"{request.generation}.json"
    try:
        load_marker(marker_path, expected_working_dir=parent_working_dir)
    except MarkerValidationError as e:
        raise MarkerValidationError(
            "unknown_generation",
            f"control request generation {request.generation!r} has no valid committed "
            f"marker under {parent_working_dir!r}: {e}",
        ) from e
    requests_dir = _requests_dir(parent_working_dir, request.generation)
    requests_dir.mkdir(parents=True, exist_ok=True)
    target = requests_dir / f"{request.request_id}.json"
    tmp = atomic_write_json(
        requests_dir / f".{request.request_id}.{os.getpid()}.{secrets.token_hex(6)}.tmp",
        request.to_dict(),
        ensure_ascii=False,
        indent=2,
        fsync=True,
    )
    try:
        os.link(str(tmp), str(target))
    except FileExistsError:
        raise
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    _fsync_dir(requests_dir)
    return target


def read_pending_control_requests(parent_working_dir: Path, generation: str):
    """Yield every :class:`ControlRequest` under ``generation`` that has no
    published ack yet, sorted by request id. A request whose own file is
    malformed is skipped (never raises out of this scan — one bad request
    file must not block the host from seeing every other valid one), mirroring
    :func:`verified_refresh_host_pids`'s fail-closed-per-item posture.
    """
    requests_dir = _requests_dir(parent_working_dir, generation)
    if not requests_dir.is_dir():
        return
    acked_ids = set()
    acks_directory = _acks_dir(parent_working_dir, generation)
    if acks_directory.is_dir():
        acked_ids = {p.stem for p in acks_directory.glob("*.json")}
    for path in sorted(requests_dir.glob("*.json")):
        if path.stem in acked_ids:
            continue
        try:
            data = read_json(path)
            request = ControlRequest.from_dict(data)
        except Exception:
            continue
        yield request


def write_control_ack(parent_working_dir: Path, ack: ControlAck) -> Path:
    """Exclusively publish ``ack`` under its request's generation's
    ``control/acks/`` directory. Raises ``FileExistsError`` (never
    overwrites) if an ack for ``ack.request_id`` already exists — this is
    the host-side half of the same exclusive-create idempotency contract as
    :func:`write_control_request`: a host that (e.g. after a crash) is
    about to re-dispatch a request it already acked must instead see
    ``FileExistsError``, read the existing ack back, and never execute the
    underlying operation a second time.
    """
    acks_directory = _acks_dir(parent_working_dir, ack.generation)
    acks_directory.mkdir(parents=True, exist_ok=True)
    target = acks_directory / f"{ack.request_id}.json"
    tmp = atomic_write_json(
        acks_directory / f".{ack.request_id}.{os.getpid()}.{secrets.token_hex(6)}.tmp",
        ack.to_dict(),
        ensure_ascii=False,
        indent=2,
        fsync=True,
    )
    try:
        os.link(str(tmp), str(target))
    except FileExistsError:
        raise
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    _fsync_dir(acks_directory)
    return target
