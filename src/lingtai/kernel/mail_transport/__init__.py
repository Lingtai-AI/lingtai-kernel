"""Core-owned outbound Port for fire-and-forget agent message transport.

This boundary lets Core send peer messages and receive inbox arrivals without
knowing the concrete transport (a POSIX filesystem mailbox today). It exposes
only the observable ``send``/``listen``/``stop``/``address`` semantics the mail
intrinsic and agent lifecycle depend on; concrete addressing, storage paths, and
polling live in an outside adapter (see
``src/lingtai/adapters/posix/mail.py``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class MailTransportPort(ABC):
    """Fire-and-forget message transport boundary owned by Core.

    An adapter translates a concrete transport (filesystem, IMAP, …) into these
    technology-neutral operations. Core receives an instance and never
    constructs or names a concrete transport.
    """

    @abstractmethod
    def send(
        self,
        address: str,
        message: dict,
        *,
        mode: str = "peer",
    ) -> str | None:
        """Deliver ``message`` to ``address``; return ``None`` on success.

        Fire-and-forget — it does not wait for a response. On failure it returns
        a human-readable error string (never raises for an addressing/liveness
        failure). ``mode`` selects how the transport interprets ``address``:
        ``"peer"`` (default) or ``"abs"``. The ``address`` vocabulary is the
        transport's; Core passes it through opaquely.
        """
        ...

    @abstractmethod
    def listen(self, on_message: Callable[[dict], None]) -> None:
        """Start delivering incoming messages to ``on_message``.

        Non-blocking: the transport runs its own background delivery. Received
        payloads are passed to ``on_message`` according to the adapter's
        delivery semantics; the Port does not promise end-to-end exactly-once
        delivery across process crashes or restarts.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop background delivery and release resources; safe to call twice."""
        ...

    @property
    @abstractmethod
    def address(self) -> str:
        """This transport's own address as an opaque string."""
        ...


__all__ = ["MailTransportPort"]
