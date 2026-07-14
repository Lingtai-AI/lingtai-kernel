"""Core-owned outbound Port for spawning the detached refresh-watcher process.

This boundary lets Core hand off a typed relaunch request to a process
supervisor that outlives the current process, without knowing the concrete
process/OS mechanism (interpreter invocation, stream detachment, session
grouping). It exposes only the observable ``spawn_detached`` operation that
``_perform_refresh`` depends on to hand off relaunch supervision after the
``.refresh``/``.refresh.taken`` handshake completes. The concrete process
mechanism lives entirely in an outside adapter that Core never imports or
names; this module deliberately carries no ``subprocess``, ``os``, POSIX, or
interpreter-path vocabulary. The watcher program's own source is rendered by
``lingtai.kernel.refresh_watcher.watcher_program`` from a
``RefreshWatcherRequest``, not built ad hoc by callers.

``encode_request``/``decode_request`` give a transport a compact,
deterministic JSON wire shape for a ``RefreshWatcherRequest`` — the data a
concrete adapter carries across a process boundary (e.g. as a single
argument to an ``-m``-invoked entrypoint module) instead of raw generated
program source. Both are pure and technology-neutral: they know only the
request's field shape, never how a transport delivers the encoded string
(argv, a file, stdin, ...).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RefreshWatcherRequest:
    """Immutable data a caller hands to ``RefreshWatcherPort.spawn_detached``.

    Carries exactly the data the watcher program needs to reproduce current
    relaunch semantics (handshake paths, relaunch command, an identity-fields
    snapshot for redacted event logging, and the env-overwrite policy signal)
    — no raw program source and no caller-supplied full environment. The
    Port/adapter render the actual program text and process environment from
    this request; Core never builds either directly.

    ``frozen=True`` alone only prevents attribute *reassignment*; it does not
    make a mutable container attribute's contents immutable, and it does
    nothing at all for a *nested* mutable value reachable through an
    otherwise-immutable container (a tuple of ``(key, value)`` pairs is still
    only shallowly immutable — a pair whose value is itself a `dict`, such as
    the runtime-identity payload's nested ``kernel_runtime`` sub-dict, still
    aliases and exposes that live mutable object). ``cmd`` is a plain
    ``tuple[str, ...]``, which is sufficient because its elements are
    strings — an already-immutable leaf type. ``identity_fields`` cannot use
    the same tuple-of-pairs shape safely, because
    ``runtime_identity_event_fields()`` returns a dict whose ``kernel_runtime``
    value is itself a nested dict (and is the *same object* as the module's
    process-wide identity cache — not even a copy). It is instead carried as
    ``identity_fields_json: str``, a JSON object snapshot serialized once at
    the construction boundary (see ``base_agent.lifecycle._perform_refresh``).
    A JSON string is a genuinely immutable leaf value at any nesting depth: no
    later mutation of the source dict (nested or not) can reach back through
    an already-serialized string. ``render_watcher_script`` decodes and
    validates it back to a dict before embedding the same
    ``identity_fields = {...!r}`` literal the rendered program always used;
    an invalid or non-object snapshot fails loudly there rather than silently
    producing broken generated source.
    """

    taken_path: str
    lock_path: str
    events_path: str
    stderr_log: str
    working_dir: str
    cmd: tuple[str, ...]
    agent_name: str
    address: str
    identity_fields_json: str = "{}"
    env_overwrite: bool = True


_REQUEST_FIELDS = (
    "taken_path",
    "lock_path",
    "events_path",
    "stderr_log",
    "working_dir",
    "cmd",
    "agent_name",
    "address",
    "identity_fields_json",
    "env_overwrite",
)


def encode_request(request: RefreshWatcherRequest) -> str:
    """Serialize ``request`` to a compact, deterministic JSON string.

    This is the technology-neutral wire shape a transport (e.g. the POSIX
    adapter's ``-m`` entrypoint invocation) carries across a process
    boundary in place of raw generated program source. ``cmd`` — a tuple in
    the dataclass so it is genuinely immutable (see the field's docstring
    above) — becomes a JSON array; JSON has no tuple type, so
    ``decode_request`` restores it to a tuple on the way back. Field order is
    fixed (``_REQUEST_FIELDS``) and ``sort_keys`` is not used, so the same
    request always encodes to the same bytes, making the wire payload
    directly diffable/testable rather than dict-iteration-order-dependent.
    """
    payload = asdict(request)
    ordered = {name: payload[name] for name in _REQUEST_FIELDS}
    return json.dumps(ordered, separators=(",", ":"))


def decode_request(payload: str) -> RefreshWatcherRequest:
    """Decode+validate an ``encode_request`` payload back to a request.

    Fails loudly (``ValueError``) on invalid JSON, a non-object top-level
    value, a missing/extra field, or a field of the wrong shape, rather than
    silently constructing a malformed request the rendered watcher program
    would then embed. This is the one place a transport's decoded wire data
    is trusted to become a typed ``RefreshWatcherRequest`` again.
    """
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"RefreshWatcherRequest payload is not valid JSON: {payload!r}") from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            "RefreshWatcherRequest payload must decode to a JSON object, "
            f"got {type(decoded).__name__}: {payload!r}"
        )
    missing = [name for name in _REQUEST_FIELDS if name not in decoded]
    if missing:
        raise ValueError(f"RefreshWatcherRequest payload missing fields: {missing!r}")
    extra = [name for name in decoded if name not in _REQUEST_FIELDS]
    if extra:
        raise ValueError(f"RefreshWatcherRequest payload has unexpected fields: {extra!r}")
    cmd = decoded["cmd"]
    if not isinstance(cmd, list) or not all(isinstance(item, str) for item in cmd):
        raise ValueError(f"RefreshWatcherRequest payload 'cmd' must be a list of strings, got {cmd!r}")
    for name in (
        "taken_path", "lock_path", "events_path", "stderr_log", "working_dir",
        "agent_name", "address", "identity_fields_json",
    ):
        if not isinstance(decoded[name], str):
            raise ValueError(
                f"RefreshWatcherRequest payload {name!r} must be a string, got {decoded[name]!r}"
            )
    if not isinstance(decoded["env_overwrite"], bool):
        raise ValueError(
            f"RefreshWatcherRequest payload 'env_overwrite' must be a bool, got {decoded['env_overwrite']!r}"
        )
    return RefreshWatcherRequest(
        taken_path=decoded["taken_path"],
        lock_path=decoded["lock_path"],
        events_path=decoded["events_path"],
        stderr_log=decoded["stderr_log"],
        working_dir=decoded["working_dir"],
        cmd=tuple(cmd),
        agent_name=decoded["agent_name"],
        address=decoded["address"],
        identity_fields_json=decoded["identity_fields_json"],
        env_overwrite=decoded["env_overwrite"],
    )


class RefreshWatcherPort(ABC):
    """Detached process-supervision boundary owned by Core.

    An adapter translates a concrete process-launch mechanism into this one
    technology-neutral operation. Core receives an instance and never
    constructs, imports, or names a concrete adapter, and never sees the
    mechanism's interpreter path, stream wiring, or session/group identifiers
    through this Port.

    There is no disabled/no-op watcher — a consumer that receives a refresh
    watcher receives a real detached-process capability.
    """

    @abstractmethod
    def spawn_detached(self, request: RefreshWatcherRequest) -> None:
        """Launch the watcher program described by ``request`` as a detached
        process supervising relaunch.

        The adapter encodes ``request`` using the Core-owned,
        technology-neutral ``encode_request`` wire shape, then launches its
        owned entrypoint using adapter-specific process/environment mechanics
        this Port does not name. The entrypoint decodes the request and renders
        the program via ``watcher_program.render_watcher_script`` (see
        ``lingtai.kernel.refresh_watcher.watcher_program``). The launched
        process MUST survive
        the caller's exit and MUST NOT inherit the caller's stdio. The call
        returns once the process has been started; it does not wait for the
        process to complete and does not return the process identity. The
        Port owns exactly this one operation and adds no wait, poll, signal,
        or process-identity query.
        """
        ...


__all__ = ["RefreshWatcherPort", "RefreshWatcherRequest", "encode_request", "decode_request"]
