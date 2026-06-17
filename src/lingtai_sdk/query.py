"""query() — a conservative high-level convenience wrapper.

This mirrors the *shape* of the Anthropic Agent SDK's ``query`` (an async
iterator of events) but is intentionally limited in this release.

**Limitation (explicit):** LingTai's runtime loop is async-peer and
fire-and-forget — ``Agent.send`` enqueues a message and returns immediately;
there is no synchronous request/response primitive and no way to collect a
single assistant turn deterministically. So ``query`` does NOT stream assistant
turns. It constructs the agent, optionally starts it, sends the prompt, and
yields a small set of lifecycle events.

For programmatic control today, prefer the stable primitives
:meth:`LingTaiClient.build_agent_kwargs` and :meth:`LingTaiClient.create_agent`.
A full turn-loop ``query`` is a documented TODO pending a request/response
contract in the runtime.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from .client import LingTaiClient
from .options import LingTaiOptions


async def query(
    prompt: str,
    *,
    options: LingTaiOptions,
    service: Any | None = None,
    autostart: bool = True,
    connect_mcp: bool = False,
    client: LingTaiClient | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Construct an agent for *prompt* and yield lifecycle events.

    Yields dicts of the form ``{"type": ...}``:

    - ``{"type": "agent_created", "agent_name": ...}``
    - ``{"type": "started"}`` (only when *autostart*)
    - ``{"type": "message_sent", "prompt": ...}``
    - ``{"type": "stopped"}`` (only when *autostart*)
    - ``{"type": "note", "message": ...}`` — the no-turn-loop caveat

    The agent is created via *client* (or a fresh :class:`LingTaiClient` built
    from *options*). When *autostart* is true the agent loop is started, the
    prompt is sent, and the agent is stopped before the final event — note this
    does NOT wait for or collect the agent's reply.
    """
    cli = client or LingTaiClient(options)
    agent = cli.create_agent(service=service, connect_mcp=connect_mcp)
    yield {"type": "agent_created", "agent_name": getattr(agent, "agent_name", None)}

    started = False
    if autostart:
        agent.start()
        started = True
        yield {"type": "started"}

    agent.send(prompt, sender="user")
    yield {"type": "message_sent", "prompt": prompt}

    yield {
        "type": "note",
        "message": (
            "query() does not stream assistant turns: the LingTai runtime loop "
            "is async/fire-and-forget. Use LingTaiClient.create_agent for "
            "programmatic control."
        ),
    }

    if started:
        agent.stop()
        yield {"type": "stopped"}
