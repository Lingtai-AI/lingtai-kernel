"""One-shot ``query`` against a fake, offline runtime.

The public :func:`lingtai_sdk.query` / :class:`lingtai_sdk.LingTaiClient` facade
is *runtime-agnostic*: it drives any object implementing the
:class:`lingtai_sdk.runtime.Runtime` contract. The default runtime is the
:class:`~lingtai_sdk.NativeRuntime`, which boots a real wrapper ``Agent`` and
therefore needs an LLM provider + key. To keep this example **offline and
deterministic**, we inject a tiny echo runtime instead — exactly how the SDK's
own test-suite exercises the facade.

Run it directly::

    python docs/sdk/examples/01_query_offline.py
"""
from __future__ import annotations

from pathlib import Path

from lingtai_sdk import LingTaiClient, query
from lingtai_sdk.runtime import (
    Runtime,
    RuntimeEvent,
    RuntimeMessage,
    RuntimeOptions,
    RuntimeSession,
    RuntimeState,
)


class EchoSession(RuntimeSession):
    """A contract-conformant session that echoes whatever it is sent.

    Note the ``events()`` contract: it is a *non-draining cumulative snapshot* —
    every call returns **all** events so far, in order, and reading never clears
    them. A backend that drained on read would silently break ``query()``.
    """

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
    """A factory for :class:`EchoSession`."""

    id = "echo"

    def create_session(self, options: RuntimeOptions) -> RuntimeSession:
        return EchoSession(options)


def main() -> None:
    options = RuntimeOptions(working_dir="/tmp/lingtai-sdk-example")

    # Object-oriented form: construct a client over an injected runtime.
    client = LingTaiClient(runtime=EchoRuntime())
    result = client.query("hello", options=options)
    print("client.query text:", result.text)
    print("client.query events:", [e.kind.value for e in result.events])

    # Module-level one-shot form: identical result, no client to hold.
    same = query("hello again", options=options, runtime=EchoRuntime())
    print("query() text:", same.text)


if __name__ == "__main__":
    main()
