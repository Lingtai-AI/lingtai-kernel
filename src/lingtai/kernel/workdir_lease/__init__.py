"""Core-owned outbound Port for exclusive agent working-directory leasing.

This boundary lets Core claim exclusive use of an agent's working directory —
so two processes never drive the same directory at once — without knowing the
concrete exclusion mechanism. It exposes only the observable ``acquire``/
``release`` semantics that ``BaseAgent`` construction and the SQLite event-index
rebuild depend on. The concrete mechanism, its identifiers, and its wait cadence
live entirely in an outside adapter that Core never imports or names; this module
deliberately carries no filesystem or platform vocabulary.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class WorkdirLeasePort(ABC):
    """Exclusive working-directory lease boundary owned by Core.

    An adapter translates a concrete exclusion mechanism into these
    technology-neutral operations. Core receives an instance and never
    constructs, imports, or names a concrete adapter, and never sees the
    mechanism's identifiers through this Port.

    A lease is single-holder: one adapter instance guards one directory. There
    is no disabled/no-op lease — a consumer that receives a lease receives a
    real exclusion promise.
    """

    @abstractmethod
    def acquire(self, timeout_seconds: float = 0) -> None:
        """Acquire the exclusive lease, waiting up to ``timeout_seconds``.

        ``timeout_seconds`` is the maximum time to wait for a currently-held
        lease to become available; ``0`` (the default) makes exactly one attempt
        and fails immediately if the directory is already leased. On failure to
        acquire within the deadline the implementation raises ``RuntimeError``.
        The wait/poll mechanism and the exact monotonic-deadline behavior are the
        adapter's; the Port promises only the observable acquire-or-raise result.
        """
        ...

    @abstractmethod
    def release(self) -> None:
        """Release a held lease; idempotent and safe to call when not held."""
        ...


__all__ = ["WorkdirLeasePort"]
