"""Core-owned outbound Port for spawning the detached refresh-watcher process.

This boundary lets Core hand off a generated relaunch script to a process
supervisor that outlives the current process, without knowing the concrete
process/OS mechanism (interpreter invocation, stream detachment, session
grouping). It exposes only the observable ``spawn_detached`` operation that
``_perform_refresh`` depends on to hand off relaunch supervision after the
``.refresh``/``.refresh.taken`` handshake completes. The concrete process
mechanism lives entirely in an outside adapter that Core never imports or
names; this module deliberately carries no ``subprocess``, ``os``, POSIX, or
interpreter-path vocabulary.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping


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
    def spawn_detached(self, script: str, *, env: Mapping[str, str]) -> None:
        """Launch ``script`` as a detached process supervising relaunch.

        ``script`` is a complete, self-contained program source; ``env`` is
        the full environment the launched process runs with. The launched
        process MUST survive the caller's exit and MUST NOT inherit the
        caller's stdio. The call returns once the process has been started;
        it does not wait for the process to complete and does not return
        the process identity. The Port owns exactly this one operation and
        adds no wait, poll, signal, or process-identity query.
        """
        ...


__all__ = ["RefreshWatcherPort"]
