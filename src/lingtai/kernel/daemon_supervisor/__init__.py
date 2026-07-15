"""Core-owned outbound Port for spawning a detached daemon-run supervisor.

This boundary lets a daemon-emanating caller hand a run manifest to a process
supervisor that outlives the caller, without the caller knowing the concrete
process/OS mechanism (interpreter invocation, stream detachment, session
grouping). It exposes only the observable ``spawn_detached`` operation
``DaemonManager._handle_emanate`` depends on to hand off run execution after
the run directory and manifest are durably written. The concrete process
mechanism lives entirely in an outside adapter this module never imports or
names — mirrors the sibling ``lingtai.kernel.refresh_watcher`` Port for the
same reason: a supervised process must survive the spawning process's exit,
and that mechanism is identical (detached POSIX session, sanitized stdio).

This is a distinct capability from ``refresh_watcher`` (different owned
entrypoint module, different request shape, different failure semantics —
a daemon supervisor owns one emanation's terminal truth, not a relaunch
handshake) so it gets its own Port rather than being force-fit into the
existing one. See root CONTRACT.md "Capability-native interfaces": Port
vocabulary MAY differ across capabilities.

``encode_request``/``decode_request`` give a transport a compact,
deterministic JSON wire shape for a ``DaemonSupervisorRequest`` — the data a
concrete adapter carries across a process boundary (as a single argument to
an ``-m``-invoked entrypoint module) instead of raw generated program source
or a live object graph.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DaemonSupervisorRequest:
    """Immutable data a caller hands to ``DaemonSupervisorPort.spawn_detached``.

    Carries only what the supervisor entrypoint needs to locate the run's
    durable manifest and interpreter — no raw program source, no live object
    references, no caller-supplied environment. The manifest itself (written
    by the caller to ``run_dir/supervisor_manifest.json`` before this request
    is issued) carries the actual daemon-scoped runtime inputs (task, tools,
    llm config, timeout, ...); this request only carries the pointer to it
    plus identity fields needed before the manifest can be read.
    """

    run_id: str
    manifest_path: str
    python_executable: str


_REQUEST_FIELDS = ("run_id", "manifest_path", "python_executable")


def encode_request(request: DaemonSupervisorRequest) -> str:
    """Serialize *request* to a compact, deterministic JSON string.

    Field order is fixed (``_REQUEST_FIELDS``) and ``sort_keys`` is not used,
    so the same request always encodes to the same bytes.
    """
    payload = asdict(request)
    ordered = {name: payload[name] for name in _REQUEST_FIELDS}
    return json.dumps(ordered, separators=(",", ":"))


def decode_request(payload: str) -> DaemonSupervisorRequest:
    """Decode+validate an ``encode_request`` payload back to a request.

    Fails loudly (``ValueError``) on invalid JSON, a non-object top-level
    value, a missing/extra field, or a field of the wrong shape, rather than
    silently constructing a malformed request the entrypoint would then act
    on.
    """
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"DaemonSupervisorRequest payload is not valid JSON: {payload!r}"
        ) from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            "DaemonSupervisorRequest payload must decode to a JSON object, "
            f"got {type(decoded).__name__}: {payload!r}"
        )
    missing = [name for name in _REQUEST_FIELDS if name not in decoded]
    if missing:
        raise ValueError(f"DaemonSupervisorRequest payload missing fields: {missing!r}")
    extra = [name for name in decoded if name not in _REQUEST_FIELDS]
    if extra:
        raise ValueError(f"DaemonSupervisorRequest payload has unexpected fields: {extra!r}")
    for name in _REQUEST_FIELDS:
        if not isinstance(decoded[name], str):
            raise ValueError(
                f"DaemonSupervisorRequest payload {name!r} must be a string, "
                f"got {decoded[name]!r}"
            )
    return DaemonSupervisorRequest(
        run_id=decoded["run_id"],
        manifest_path=decoded["manifest_path"],
        python_executable=decoded["python_executable"],
    )


class DaemonSupervisorPort(ABC):
    """Detached process-supervision boundary for one daemon emanation run.

    An adapter translates a concrete process-launch mechanism into this one
    technology-neutral operation. The caller receives an instance and never
    constructs, imports, or names a concrete adapter, and never sees the
    mechanism's interpreter path, stream wiring, or session/group identifiers
    through this Port.

    There is no disabled/no-op supervisor — a consumer that receives a
    daemon supervisor Port receives a real detached-process capability.
    """

    @abstractmethod
    def spawn_detached(self, request: DaemonSupervisorRequest) -> None:
        """Launch the supervisor process described by *request*.

        The adapter encodes *request* using the technology-neutral
        ``encode_request`` wire shape, then launches its owned entrypoint
        using adapter-specific process/environment mechanics this Port does
        not name. The entrypoint decodes the request, reads the run manifest
        at ``request.manifest_path``, and runs
        ``lingtai.tools.daemon.supervisor_runtime.run_supervisor``. The
        launched process MUST survive the caller's exit and MUST NOT inherit
        the caller's stdio. The call returns once the process has been
        started; it does not wait for the process to complete, does not
        return the process identity, and adds no wait/poll/signal operation.
        The supervisor itself is responsible for recording its own PID into
        the run's ``daemon.json`` once it starts (see ``supervisor.py``) so a
        caller-side startup handshake can observe successful launch without
        this Port returning one.
        """
        ...


__all__ = [
    "DaemonSupervisorPort",
    "DaemonSupervisorRequest",
    "encode_request",
    "decode_request",
]
