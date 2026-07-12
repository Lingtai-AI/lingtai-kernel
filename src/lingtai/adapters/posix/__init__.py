"""Narrow POSIX filesystem adapters."""

from typing import Any

from .event_journal import PosixJsonlEventJournalAdapter
from .mail import PosixFilesystemMailAdapter

__all__ = [
    "PosixJsonlEventJournalAdapter",
    "PosixFilesystemMailAdapter",
    "PosixWorkdirLeaseAdapter",
]


def __getattr__(name: str) -> Any:
    """Lazily expose ``PosixWorkdirLeaseAdapter`` without importing ``fcntl``.

    The lease adapter imports ``fcntl`` at module top, which is absent on
    non-POSIX platforms. Eagerly importing it here would make loading this
    package (or its portable siblings ``event_journal``/``mail``) fail on such a
    platform *before* ``select_workdir_lease`` can raise its explicit
    ``NotImplementedError``. Deferring the import keeps unsupported-platform
    failure owned by the selector, and still lets
    ``from lingtai.adapters.posix import PosixWorkdirLeaseAdapter`` work on POSIX.
    """
    if name == "PosixWorkdirLeaseAdapter":
        from .workdir_lease import PosixWorkdirLeaseAdapter

        return PosixWorkdirLeaseAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
