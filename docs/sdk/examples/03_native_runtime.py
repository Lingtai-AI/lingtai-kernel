"""Configuring the :class:`lingtai_sdk.NativeRuntime` (no network, no boot).

:class:`~lingtai_sdk.NativeRuntime` is the default backend: it wraps the
batteries-included wrapper ``Agent`` unchanged, translating a backend-neutral
:class:`~lingtai_sdk.runtime.RuntimeOptions` into ``Agent`` constructor kwargs.

Constructing a ``NativeRuntime`` and a session is import-pure and free of
provider SDKs — **the wrapper ``Agent`` and its heavy deps load lazily, only
when a session is actually started**. So this example stops short of
``session.start()``: it shows the configuration surface and the lazy boundary
without needing an API key or making a network call.

To actually run an agent you would set ``provider`` / ``model`` (and supply a
key via ``api_key`` or the manifest), then call ``session.start()`` — at which
point the wrapper and the chosen provider SDK are imported and a real agent
boots. That path needs credentials and is therefore not exercised here.

Run it directly::

    python docs/sdk/examples/03_native_runtime.py
"""
from __future__ import annotations

from lingtai_sdk import NativeRuntime
from lingtai_sdk.runtime import RuntimeOptions, RuntimeState


def main() -> None:
    runtime = NativeRuntime()

    # A fully-specified, backend-neutral options object. `capabilities` and
    # `provider`/`model` mirror what `Agent` / init.json consume today.
    options = RuntimeOptions(
        working_dir="/tmp/lingtai-sdk-native",
        agent_name="demo",
        provider="anthropic",
        model="claude-opus-4-8",
        capabilities=["file", "web_search"],
        # api_key=...  # omitted here — start() is not called, so none is needed
    )

    session = runtime.create_session(options)

    # Before start(), the session is PENDING and no Agent has been built.
    print("state before start:", session.state.value)
    assert session.state is RuntimeState.PENDING
    print("working_dir:", session.working_dir)

    # NOTE: we intentionally do NOT call session.start() — that would import the
    # wrapper and require real LLM credentials. See the module docstring.
    print("Configured a NativeRuntime session without booting an agent.")


if __name__ == "__main__":
    main()
