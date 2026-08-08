"""Kimi Code configured-effort descriptor.

One owner for the Kimi side of `manifest.llm.thinking`: the exact accepted
vocabulary, the model capability gate, the omitted/explicit decision, the
environment fragment that decision produces, and the safe fields that decision
contributes to the `llm_call` record.

Deliberately provider-local and deliberately small. There is no generic
resolver or capability catalogue here: the Kimi coding service's
`KIMI_MODEL_THINKING_EFFORT` vocabulary is a Kimi fact, and other providers
must not inherit it (Responses has no `max`; Kimi has no `none`/`minimal`).
Note the wire shape differs from a CLI-flag provider — effort travels on the
per-invocation private environment, not in argv, so it never appears in a
process listing next to the prompt.

The result is frozen at chat creation and reused by every subprocess of that
session, so queueing, retries, and overflow recovery cannot blur which effort a
given logical send used.
"""
from __future__ import annotations

from dataclasses import dataclass

from lingtai.kernel.config import KIMI_THINKING_LEVELS, THINKING_OMITTED

# Provider-local names for the schema-shared vocabulary. Re-exported rather
# than restated so the init/preset gate and this wire emitter cannot drift.
KIMI_EFFORT_LEVELS = KIMI_THINKING_LEVELS
KIMI_EFFORT_OMITTED = THINKING_OMITTED

# The one environment variable this contract owns.
KIMI_EFFORT_ENV_VAR = "KIMI_MODEL_THINKING_EFFORT"

# Stable id for the evidence behind KIMI_EFFORT_LEVELS. This records *which*
# capability statement produced the vocabulary; it is not a claim that every
# model or account accepts every level (`model_verified=false`).
KIMI_EFFORT_CAPABILITY_SOURCE = "kimi_coding_service_models_page_2026-08-05"

# Model ids documented as exposing an effort dimension. The gate is an
# allowlist, not a denylist: an id absent from BOTH tuples below is unknown,
# and unknown fails closed rather than optimistically emitting an env var a
# model may reject.
KIMI_EFFORT_CAPABLE_MODELS = ("k3", "k3-256k")

# Model ids documented as always-thinking. They have no effort dimension at
# all, so an explicit level is a configuration error rather than a no-op.
KIMI_ALWAYS_THINKING_MODELS = ("kimi-for-coding", "kimi-for-coding-highspeed")

# Observability token for "no effort was requested and none was sent".
OMITTED_TOKEN = "omitted"

_SOURCE_OMITTED = "lingtai_kimi_omitted"
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
class KimiEffort:
    """An immutable configured-effort decision for one Kimi chat session."""

    #: Exact level to publish, or None for "publish no variable".
    level: str | None
    #: Exact requested token, or OMITTED_TOKEN.
    requested: str
    #: Provenance: omitted-by-LingTai vs explicitly configured.
    source: str

    @property
    def env(self) -> dict[str, str]:
        """The environment fragment this decision contributes to a `kimi` run."""
        return {KIMI_EFFORT_ENV_VAR: self.level} if self.level else {}

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
            "reasoning_capability_source": KIMI_EFFORT_CAPABILITY_SOURCE,
        }


#: The omission decision — byte-identical to the pre-contract invocation.
OMITTED_EFFORT = KimiEffort(level=None, requested=OMITTED_TOKEN, source=_SOURCE_OMITTED)


def normalize_kimi_effort(thinking: object, model: object = None) -> KimiEffort:
    """Resolve a generic `thinking` input into a frozen Kimi effort decision.

    ``None`` and the internal ``"default"`` omission sentinel mean *omitted* —
    no ``KIMI_MODEL_THINKING_EFFORT`` variable at all. LingTai does not encode
    the service's own upstream default as a LingTai baseline, and does not claim
    default restoration. Omission is valid for **every** model, including the
    always-thinking ones, so the model gate below is never consulted for it.

    Any other value must be exactly one of ``KIMI_EFFORT_LEVELS``. Case
    variants, whitespace aliases, empty strings, booleans, non-strings, the
    gateway's compatibility aliases (``medium``/``xhigh``), and
    ``"default"``-as-a-user-literal all raise ``ValueError`` here — before any
    subprocess is dispatched.

    The vocabulary is checked *before* the model gate deliberately, so a bad
    value reports the vocabulary it violated rather than whichever model
    happened to be configured.
    """
    if thinking is None or thinking == KIMI_EFFORT_OMITTED:
        return OMITTED_EFFORT
    if not isinstance(thinking, str) or thinking not in KIMI_EFFORT_LEVELS:
        raise ValueError(
            "kimi-code effort must be exactly one of "
            f"{', '.join(KIMI_EFFORT_LEVELS)} "
            "(omit the field for no KIMI_MODEL_THINKING_EFFORT variable); "
            f"got {_safe_repr(thinking)}"
        )

    name = str(model or "").strip().lower()
    if name in KIMI_ALWAYS_THINKING_MODELS:
        raise ValueError(
            f"kimi-code model {name!r} is always-thinking and has no effort "
            "dimension; omit manifest.llm.thinking for this model"
        )
    if name not in KIMI_EFFORT_CAPABLE_MODELS:
        raise ValueError(
            "kimi-code effort capability is unknown for model "
            f"{_safe_repr(model)}; failing closed. Known effort-capable "
            f"models: {', '.join(KIMI_EFFORT_CAPABLE_MODELS)}"
        )
    return KimiEffort(level=thinking, requested=thinking, source=_SOURCE_EXPLICIT)
