"""Minimal session contracts for the LingTai SDK facade.

A ``SessionRef`` is a cheap handle to an agent's working directory / session id.
``SessionStore`` is a structural protocol hosts can implement to persist refs;
``InMemorySessionStore`` is a trivial reference implementation for tests and
simple hosts. None of this couples to runtime session internals
(``lingtai_kernel.session``) — it is a host-facing bookkeeping contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionRef:
    """A handle identifying an agent session by id and working directory."""

    session_id: str
    working_dir: str | Path | None = None


@runtime_checkable
class SessionStore(Protocol):
    """Structural contract for persisting and retrieving :class:`SessionRef`."""

    def save(self, ref: SessionRef) -> None: ...

    def load(self, session_id: str) -> SessionRef | None: ...

    def list(self) -> list[SessionRef]: ...


class InMemorySessionStore:
    """A dict-backed :class:`SessionStore` for tests and simple hosts."""

    def __init__(self) -> None:
        self._refs: dict[str, SessionRef] = {}

    def save(self, ref: SessionRef) -> None:
        self._refs[ref.session_id] = ref

    def load(self, session_id: str) -> SessionRef | None:
        return self._refs.get(session_id)

    def list(self) -> list[SessionRef]:
        return list(self._refs.values())
