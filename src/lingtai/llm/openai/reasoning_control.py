"""Transport-neutral reasoning application carrier and hook protocol.

The generic OpenAI-compatible transport owns *invocation* only. It knows that
a provider route may install a controller which decides what reasoning fields
a request carries, and it knows how to carry the resulting immutable decision
on the session so observation can report the patch that was really applied.

It deliberately knows nothing about any provider's model names, effort levels,
compatibility aliases, defaults, or payload shapes — those live inside the
provider's own package (see ``lingtai/llm/deepseek/reasoning.py``, the only
current implementor).

Related files:
  - src/lingtai/llm/openai/adapter.py — invokes the controller, captures the result
  - src/lingtai/llm/deepseek/reasoning.py — the DeepSeek implementation
  - src/lingtai/kernel/session.py — renders ``observation_fields()`` onto llm_call
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable


_EMPTY: Mapping[str, Any] = MappingProxyType({})


def _freeze(value: Any) -> Any:
    """Return a recursively immutable view of a plain request-kwargs value.

    Structural only — this helper carries no provider semantics; it does not
    know or care what any key means. Mappings become read-only proxies and
    sequences become tuples, all the way down, so a captured application
    cannot be edited through a nested container.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    """Return a plain, deeply mutable copy of a frozen request-kwargs value.

    The inverse of :func:`_freeze`. The OpenAI SDK expects ordinary ``dict``/
    ``list`` objects, and callers must be free to mutate what they are handed
    without reaching back into the captured application.
    """
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class ReasoningApplication:
    """One immutable reasoning decision, captured at session construction.

    Every field is a bounded plain string except ``payload``, which is the
    exact set of request kwargs the wire builder merges. The carrier is
    created once per session and consumed once by the request builder;
    observation reads the SAME object rather than recomputing anything from
    raw config.

    Attributes:
        provider: Provider identity (e.g. ``deepseek``), never the generic wire owner.
        wire: ``chat_completions`` or ``responses``.
        requested: The raw configured value — canonical level, official alias,
            or the ``default`` omission sentinel.
        normalized: The value after the provider's own alias normalization.
        emitted: What actually went on the wire (``omitted``/``disabled``/level).
        provenance: ``omitted``, ``explicit_config``, or ``compat_alias``.
        payload: Exact request kwargs, deeply frozen at construction (empty for
            an omission). Read it for inspection/observation; call
            ``request_kwargs()`` to get something a caller may mutate.
    """

    provider: str
    wire: str
    requested: str
    normalized: str
    emitted: str
    provenance: str
    payload: Mapping[str, Any] = field(default=_EMPTY)

    def __post_init__(self) -> None:
        # Deep-freeze whatever the provider handed us, so "immutable applied
        # result" holds all the way down rather than only at the top level.
        # ``object.__setattr__`` is the standard frozen-dataclass escape.
        object.__setattr__(self, "payload", _freeze(self.payload))

    def request_kwargs(self) -> dict[str, Any]:
        """Return a fresh, deeply mutable copy of the request kwargs.

        The OpenAI SDK needs plain ``dict``s, and the wire builder merges this
        into a per-session kwargs dict that other code may still edit. Handing
        out a copy keeps the captured application — and therefore observation —
        authoritative and untouched no matter what happens downstream.
        """
        return _thaw(self.payload)

    def observation_fields(self) -> dict[str, str]:
        """Return the bounded, allowlisted observation fields for this result.

        Plain strings only — never payload contents, prompts, or credentials.
        """
        return {
            "provider": self.provider,
            "wire": self.wire,
            "effort_requested": self.requested,
            "effort_normalized": self.normalized,
            "effort_emitted": self.emitted,
            "effort_provenance": self.provenance,
        }


@runtime_checkable
class ReasoningController(Protocol):
    """Provider-local owner of the reasoning decision for one request."""

    def apply(
        self, *, model: str, wire: str, thinking: Any
    ) -> ReasoningApplication:
        """Return the application result, or raise ``ValueError`` before dispatch."""
        ...
