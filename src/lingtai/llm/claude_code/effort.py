"""Claude Code configured-effort descriptor.

One owner for the Claude side of `manifest.llm.thinking`: the exact accepted
vocabulary, the omitted/explicit decision, the argv fragment that decision
produces, and the safe fields that decision contributes to the `llm_call`
record.

Deliberately provider-local and deliberately small. There is no generic
resolver or capability catalogue here: the installed `claude` CLI's
`--effort <level>` vocabulary is a Claude fact, and other providers must not
inherit it (Responses has no `max`; Claude has no `none`/`minimal`).

The result is frozen at chat creation and reused by every subprocess of that
session, so queueing, retries, and overflow recovery cannot blur which effort a
given logical send used.
"""
from __future__ import annotations

from dataclasses import dataclass

from lingtai.kernel.config import CLAUDE_THINKING_LEVELS, THINKING_OMITTED

# Provider-local names for the schema-shared vocabulary. Re-exported rather
# than restated so the init/preset gate and this wire emitter cannot drift.
CLAUDE_EFFORT_LEVELS = CLAUDE_THINKING_LEVELS
CLAUDE_EFFORT_OMITTED = THINKING_OMITTED

# Stable id for the evidence behind CLAUDE_EFFORT_LEVELS. This records *which*
# capability statement produced the vocabulary; it is not a claim that every
# model or account accepts every level (`model_verified=false`).
CLAUDE_EFFORT_CAPABILITY_SOURCE = "claude_cli_2.1.220_help"

# Observability token for "no effort was requested and none was sent".
OMITTED_TOKEN = "omitted"

_SOURCE_OMITTED = "lingtai_claude_omitted"
_SOURCE_EXPLICIT = "explicit_config"

_MAX_ECHOED_VALUE_CHARS = 32


def _safe_repr(value: object) -> str:
    """Bounded description of a rejected value for an error message.

    Never echoes an unbounded blob: a long or non-string value is described by
    type only, so a misconfigured field cannot smuggle arbitrary config text
    into an exception that may be logged.
    """
    if isinstance(value, str) and len(value) <= _MAX_ECHOED_VALUE_CHARS:
        return repr(value)
    return f"<{type(value).__name__}>"


@dataclass(frozen=True)
class ClaudeEffort:
    """An immutable configured-effort decision for one Claude chat session."""

    #: Exact CLI level to emit, or None for "emit no flag".
    level: str | None
    #: Exact requested token, or OMITTED_TOKEN.
    requested: str
    #: Provenance: omitted-by-LingTai vs explicitly configured.
    source: str

    @property
    def argv(self) -> list[str]:
        """The argv fragment this decision contributes to a `claude` command."""
        return ["--effort", self.level] if self.level else []

    def observability_fields(self) -> dict[str, str]:
        """Safe, provider-neutral `llm_call` fields derived from this decision.

        No credential, session id, prompt content, or raw argv is included.
        """
        actual = self.level or OMITTED_TOKEN
        return {
            "reasoning_requested": self.requested,
            "reasoning_normalized": actual,
            "reasoning_actual": actual,
            "reasoning_source": self.source,
            "reasoning_capability_source": CLAUDE_EFFORT_CAPABILITY_SOURCE,
        }


#: The omission decision — byte-identical to the pre-contract command.
OMITTED_EFFORT = ClaudeEffort(
    level=None, requested=OMITTED_TOKEN, source=_SOURCE_OMITTED
)


def normalize_claude_effort(thinking: object) -> ClaudeEffort:
    """Resolve a generic `thinking` input into a frozen Claude effort decision.

    ``None`` and the internal ``"default"`` omission sentinel mean *omitted* —
    no ``--effort`` flag at all. LingTai does not encode the CLI's own upstream
    default as a LingTai baseline, and does not claim default restoration.

    Any other value must be exactly one of ``CLAUDE_EFFORT_LEVELS``. Case
    variants, whitespace aliases, empty strings, booleans, non-strings, and
    ``"default"``-as-a-user-literal all raise ``ValueError`` here — before any
    subprocess is dispatched.
    """
    if thinking is None or thinking == CLAUDE_EFFORT_OMITTED:
        return OMITTED_EFFORT
    if isinstance(thinking, str) and thinking in CLAUDE_EFFORT_LEVELS:
        return ClaudeEffort(
            level=thinking, requested=thinking, source=_SOURCE_EXPLICIT
        )
    raise ValueError(
        "claude-code effort must be exactly one of "
        f"{', '.join(CLAUDE_EFFORT_LEVELS)} "
        f"(omit the field for no --effort flag); got {_safe_repr(thinking)}"
    )
