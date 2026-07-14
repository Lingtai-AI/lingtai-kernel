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
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


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

        The adapter renders the program source from ``request`` via the
        Core-owned, technology-neutral
        ``watcher_program.render_watcher_script`` (see
        ``lingtai.kernel.refresh_watcher.watcher_program``), then builds the
        concrete process environment and launches it using adapter-owned
        mechanism this Port does not name. The launched process MUST survive
        the caller's exit and MUST NOT inherit the caller's stdio. The call
        returns once the process has been started; it does not wait for the
        process to complete and does not return the process identity. The
        Port owns exactly this one operation and adds no wait, poll, signal,
        or process-identity query.
        """
        ...


__all__ = ["RefreshWatcherPort", "RefreshWatcherRequest"]
