"""A live, multi-message session via :class:`lingtai_sdk.LingTaiSession`.

``query()`` is one-shot: send a message, drain events, stop. When you want to
keep a session open — send several messages, poll events incrementally, then
close — use :meth:`LingTaiClient.open_session` (or the module-level
:func:`lingtai_sdk.open_session`).

The key ergonomic difference from the raw runtime contract: ``LingTaiSession``
keeps its **own read cursor**, so each ``events()`` / ``text()`` call returns
only what arrived *since the previous read* — an incremental view over the
underlying non-draining snapshot. The raw cumulative snapshot is still reachable
via ``raw_session``.

This example defines its own offline echo runtime so it is fully self-contained.

Run it directly::

    python docs/sdk/examples/02_session_offline.py
"""
from __future__ import annotations

from pathlib import Path

from lingtai_sdk import LingTaiClient
from lingtai_sdk.runtime import (
    Runtime,
    RuntimeEvent,
    RuntimeMessage,
    RuntimeOptions,
    RuntimeSession,
    RuntimeState,
)


class EchoSession(RuntimeSession):
    """A contract-conformant, non-draining session that echoes its input."""

    def __init__(self, options: RuntimeOptions) -> None:
        self._options = options
        self._events: list[RuntimeEvent] = []

    @property
    def state(self) -> RuntimeState:
        return RuntimeState.IDLE

    @property
    def working_dir(self) -> Path:
        return Path(self._options.working_dir)

    def start(self) -> None:
        self._events.append(RuntimeEvent.state(RuntimeState.ACTIVE))

    def send(self, message: RuntimeMessage | str) -> None:
        content = message if isinstance(message, str) else message.content
        self._events.append(RuntimeEvent.text(f"echo: {content}"))

    def events(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)

    def stop(self, timeout: float = 5.0) -> None:
        self._events.append(RuntimeEvent.state(RuntimeState.STOPPED))


class EchoRuntime(Runtime):
    id = "echo"

    def create_session(self, options: RuntimeOptions) -> RuntimeSession:
        return EchoSession(options)


def main() -> None:
    options = RuntimeOptions(working_dir="/tmp/lingtai-sdk-session")
    client = LingTaiClient(runtime=EchoRuntime(), options=options)

    # `with` starts the session and closes it on exit.
    with client.open_session() as session:
        session.send("first")
        # Incremental read: only events since the last read.
        print("after first:", session.text())

        session.send("second")
        print("after second:", session.text())

        # The full cumulative snapshot is always available on the raw session.
        print("full snapshot:", [e.kind.value for e in session.raw_session.events()])


if __name__ == "__main__":
    main()
