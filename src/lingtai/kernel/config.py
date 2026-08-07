"""Agent configuration, runtime constants, and environment-backed policy helpers."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass


# Accepted manifest.llm.thinking values, mirroring the upstream Responses
# ``reasoning.effort`` payload values in ascending effort order. Explicit
# ``"none"`` is a real payload value (effort none), distinct from an *omitted*
# field — omitted stays the internal ``"default"`` sentinel and the Codex
# adapter maps it to ``"xhigh"``.
THINKING_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")

# Codex-family providers that accept manifest.llm.thinking. ``codex-pool``
# reuses the Codex adapter (both dash/underscore spellings). Custom
# OpenAI-compatible Responses is the other supported scope; use
# ``llm_supports_thinking`` so validators share the complete rule.
THINKING_PROVIDERS = ("codex", "codex-pool", "codex_pool")

# Registered spellings of the native Kimi Code CLI route. Both already resolve
# to the same ``KimiCodeAdapter`` in ``lingtai/llm/_register.py``, so both
# accept the same explicit thinking vocabulary.
KIMI_THINKING_PROVIDERS = ("kimi-code", "kimi_code")

# Accepted manifest.llm.thinking values for the Kimi Code route. This is the
# K3 coding service's own native effort vocabulary and is deliberately NOT the
# Responses tuple above: Kimi has no ``none``/``minimal``, and Responses has no
# ``max``. The compatibility aliases the coding gateway may itself accept
# (``medium`` → high, ``xhigh`` → max) are deliberately NOT surfaced here — a
# LingTai manifest names the level the provider actually documents. The
# provider-local descriptor (``lingtai/llm/kimi_code/effort.py``) re-exports
# this tuple rather than restating it, so the schema gate and the wire emitter
# cannot drift apart. Capability status is ``model_verified=false``: the
# env-var contract and vocabulary are documented, per-model/account acceptance
# and installed-CLI-version behavior are not verified.
KIMI_THINKING_LEVELS = ("low", "high", "max")

# Internal omission sentinel for manifest.llm.thinking. It is never a
# user-configurable literal: it means "the field was omitted", and each provider
# adapter owns what omission means on its own wire (Codex maps it to
# ``reasoning.effort = "xhigh"``; Kimi Code emits no environment variable at
# all). Validators reject it as an explicit value.
THINKING_OMITTED = "default"

# Legacy cross-provider main-session default for a constructor-omitted
# ``AgentConfig.thinking``. Providers that own their omission (Codex, Kimi
# Code) opt out of it via ``THINKING_OMITTED``.
DEFAULT_THINKING = "high"


def thinking_levels_for_llm(llm: dict) -> tuple[str, ...] | None:
    """Return the explicit thinking vocabulary a manifest LLM block accepts.

    ``None`` means the block is out of scope entirely. Callers must validate an
    explicit value against the *returned* tuple rather than a single global
    tuple — the accepted vocabulary is provider-specific, so ``max`` stays
    rejected on Responses and ``none``/``minimal``/``medium``/``xhigh`` stay
    rejected on Kimi Code.
    """
    provider = str(llm.get("provider") or "").lower()
    if provider in THINKING_PROVIDERS:
        return THINKING_LEVELS
    if provider in KIMI_THINKING_PROVIDERS:
        return KIMI_THINKING_LEVELS
    if (
        provider == "custom"
        and str(llm.get("api_compat") or "").lower() == "openai"
        and str(llm.get("wire_api") or "").lower() == "responses"
    ):
        return THINKING_LEVELS
    return None


def llm_supports_thinking(llm: dict) -> bool:
    """Return whether a manifest LLM block accepts explicit thinking effort."""
    return thinking_levels_for_llm(llm) is not None


def ancillary_session_thinking(provider: str | None) -> str:
    """Provider-scoped thinking level for a LingTai-internal ancillary session.

    Ancillary sessions (soul inquiry/consultation mirrors) are created by
    LingTai's own code, not by an operator's ``manifest.llm.thinking``. A
    provider that owns its own omission semantics (Kimi Code) must not receive
    the legacy cross-provider ``DEFAULT_THINKING`` here either: the
    always-thinking default model would reject an effort LingTai injected
    itself. Such providers get ``THINKING_OMITTED`` (the adapter then emits no
    ``KIMI_MODEL_THINKING_EFFORT`` at all); every other provider keeps
    ``DEFAULT_THINKING``, byte-identical to today.
    """
    if str(provider or "").lower() in KIMI_THINKING_PROVIDERS:
        return THINKING_OMITTED
    return DEFAULT_THINKING

# Molt context-pressure thresholds are kernel-fixed runtime constants — NOT
# agent-configurable. An agent must not be able to raise its own molt
# thresholds (or defeat them entirely) to avoid molting under pressure, so the
# stage boundaries are owned by the kernel. Legacy ``init.json`` /
# resolved-manifest ``molt_notice`` / ``molt_pressure`` / ``molt_urgency``
# fields are tolerated for backward compatibility (old agents still validate)
# but are ignored — they no longer override these values. See
# ``lingtai/agent.py`` (config reload) and ``lingtai/init_schema.py``
# (MANIFEST_LEGACY_IGNORED).
MOLT_NOTICE_THRESHOLD = 0.75  # legacy name; now the molt RECOVERY TARGET (see below)

# Sustained context-pressure / manual-rebuild / molt-warning constants
# (kernel-fixed).
#
# The warning surfaced in ``_meta.agent_meta.agent_state.context.molt`` is no
# longer an immediate trip-wire.  It is a *sustained-pressure*
# signal, while provider-context reconstruction is a separate, rarer event:
#
#   * CONTEXT_PRESSURE_HIGH_RATIO (0.85) — a fresh provider round whose context
#     usage is at/above this fraction counts as a "high" round.  The same
#     inclusive threshold (``usage >= 0.85``) also continuously stamps
#     ``_meta.agent_meta.agent_state.context.rebuild`` with permission to manually rebuild via
#     ``context(action='rebuild')``.  It does NOT force an
#     automatic provider-context rebuild — it is the proactive hint boundary.
#   * CONTEXT_PRESSURE_FORCED_REBUILD_RATIO (1.0) — the HARD boundary. Once
#     context usage reaches this inclusive threshold, the runtime forces a
#     provider-context rebuild / fresh replay on the next model request
#     REGARDLESS of whether pending summaries exist: if pending markers exist, they
#     are applied and marked done; ``summarize`` is the only historical
#     tool-result body replacement a rebuild applies — the fresh replay
#     otherwise preserves each historical timely-transient holder and does not
#     strip its agent_meta/guidance or notifications/notification_guidance keys
#     in shared model-facing serialization (only the LATEST holder per family
#     is current state; older holders are historical traces). A one-shot
#     unified warning is ALWAYS emitted after this forced rebuild.
#     (``CONTEXT_PRESSURE_RECONSTRUCTION_RATIO`` is a back-compat alias.)
#   * CONTEXT_PRESSURE_WARN_AFTER_ROUNDS (3) — the resident ``context.molt``
#     warning begins on the THIRD consecutive high round; earlier high rounds get
#     the manual-rebuild hint but not the stronger molt reminder.
#   * CONTEXT_PRESSURE_RECOVERY_TARGET (0.75) — if summarize/rebuild cannot bring
#     context below this fraction of the window, molt becomes the recommended
#     action.  This is the new meaning of the legacy 0.75 constant: a recovery
#     target, not an immediate trip-wire.
CONTEXT_PRESSURE_HIGH_RATIO = 0.85
CONTEXT_PRESSURE_FORCED_REBUILD_RATIO = 1.0
# Back-compat alias for the pre-1.0 name (was 0.95, "delayed reconstruction");
# the boundary is now the hard 1.0 forced rebuild.
CONTEXT_PRESSURE_RECONSTRUCTION_RATIO = CONTEXT_PRESSURE_FORCED_REBUILD_RATIO
CONTEXT_PRESSURE_WARN_AFTER_ROUNDS = 3
CONTEXT_PRESSURE_RECOVERY_TARGET = MOLT_NOTICE_THRESHOLD  # 0.75

MOLT_PRESSURE_THRESHOLD = MOLT_NOTICE_THRESHOLD  # legacy alias; not a separate stage
MOLT_URGENCY_THRESHOLD = MOLT_NOTICE_THRESHOLD  # legacy alias; not a separate stage
DEFAULT_SOUL_DELAY_SECONDS = 999999999.0

# Rendered system-prompt size pressure — distinct from the CONTEXT_PRESSURE_*
# family above (which measures system + tools + history against the window).
# This ratio gates a warning on the rendered system prompt ALONE against the
# effective context window. It is deliberately read at snapshot-render time so
# the main agent and daemon share live process-environment behavior.
DEFAULT_SYSTEM_PROMPT_PRESSURE_RATIO = 0.4
SYSTEM_PROMPT_PRESSURE_RATIO_ENV = "LINGTAI_SYSTEM_PROMPT_PRESSURE_RATIO"


def system_prompt_pressure_ratio() -> float:
    """Return the valid current environment ratio, or the default."""
    raw = os.environ.get(SYSTEM_PROMPT_PRESSURE_RATIO_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_SYSTEM_PROMPT_PRESSURE_RATIO
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SYSTEM_PROMPT_PRESSURE_RATIO
    if not math.isfinite(value) or not 0 < value < 1:
        return DEFAULT_SYSTEM_PROMPT_PRESSURE_RATIO
    return value

# Hidden runtime housekeeping: an agent that remains IDLE for this long is moved
# to ASLEEP. This is deliberately kernel-fixed and not surfaced in init.json,
# prompts, status, or tool metadata.
IDLE_SLEEP_TIMEOUT_SECONDS = 86400.0


class _OmittedThinking:
    """Module-private marker for a constructor-omitted ``thinking`` field.

    Compared by IDENTITY only. It exists because ``AgentConfig.thinking``'s
    historical default (``"high"``) is indistinguishable from an explicitly
    configured ``"high"``, and the Kimi Code route must not turn a silent
    default into a real ``KIMI_MODEL_THINKING_EFFORT=high`` — which the
    always-thinking default model would reject outright. ``__post_init__``
    replaces it with a concrete string, so no caller ever observes this object.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<AgentConfig.thinking omitted>"


_THINKING_OMITTED_SENTINEL = _OmittedThinking()


@dataclass
class AgentConfig:
    """Configuration for a BaseAgent instance.

    The host app reads its own config files and passes resolved values here.
    No file-based config reading inside lingtai.
    """
    max_turns: int = 50
    provider: str | None = None  # None = use LLMService's provider
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    retry_timeout: float = 300.0  # LLM call watchdog (seconds). Bumped from 120s — modern thinking models (GLM-5.1, DeepSeek V4 thinking, Anthropic extended-thinking) routinely take 60–180s for high-context turns; 120s spuriously fired on slow-but-successful calls and triggered AED cascades. 300s catches truly-hung connections without false positives on normal responses.
    aed_timeout: float = 360.0   # max seconds in STUCK before ASLEEP
    max_aed_attempts: int = 3   # max AED retry attempts per inbox message turn
    max_rpm: int = 60  # API requests-per-minute cap for this agent's provider; 0 = no gating. Shared across all agents in the same process that use the same (provider, base_url) pair (adapter cache key).
    thinking_budget: int | None = None
    # Reasoning/thinking tier passed to the main persistent LLM session. The
    # declared default is the module-private omitted sentinel, not a value:
    # ``__post_init__`` resolves it per provider so a route that owns its own
    # omission semantics (Kimi Code) keeps ``THINKING_OMITTED`` instead of
    # silently acquiring the legacy cross-provider ``"high"``. Every other
    # provider still sees exactly ``"high"``.
    thinking: str = _THINKING_OMITTED_SENTINEL  # type: ignore[assignment]
    data_dir: str | None = None  # for cache files (e.g., model context windows)
    soul_delay: float = DEFAULT_SOUL_DELAY_SECONDS  # seconds idle before soul whispers; large value = effectively off
    language: str = "en"  # legacy language field retained for compatibility; prompt.py no longer injects prose from it
    activeness: str | None = "balanced"  # legacy responsiveness posture field; prompt.py no longer injects text from it
    stamina: float = IDLE_SLEEP_TIMEOUT_SECONDS  # legacy ignored constructor field; hidden idle timeout uses the kernel constant above
    time_awareness: bool = True  # experimental: False strips LLM-visible timestamps (perception nerf)
    timezone_awareness: bool = True  # when True, now_iso emits OS local time; when False, UTC
    context_limit: int | None = None  # max context tokens; None = use model default
    # Soft since-last-molt cache-miss token budget. Once the cumulative/restored
    # cache-miss (uncached input) total reaches or exceeds this value, a "molt
    # now" reminder is restamped into _meta.agent_meta.agent_state.context.molt (see
    # meta_block.build_cache_miss_budget_context) and the budget value is surfaced
    # under _meta.agent_meta.agent_state.context. It is a soft cap — nothing is blocked; the
    # agent is expected to molt. The cache-miss total is read from the cumulative
    # get_token_usage() totals (which SURVIVE restore_token_state), so a refresh
    # does NOT reset the remaining budget; a successful molt starts a new
    # since-last-molt session/budget cycle. It is deliberately NOT the
    # since-refresh get_runtime_session_token_usage delta. Validated as a positive
    # int (bool and <= 0 rejected) in lingtai/init_schema.py and hydrated from
    # manifest.cache_miss_budget by lingtai/agent.py build_agent_config.
    #
    # The effective budget may also be overridden at runtime by the
    # LINGTAI_CACHE_MISS_BUDGET env var (see meta_block._resolve_cache_miss_budget):
    # a positive-int env value wins over this config/default at every budget
    # resolution (live-read, like the nudge env vars — no restart). This lets the
    # operator or the agent itself (via its env_file + refresh) tune the budget
    # without editing init.json. An invalid env value falls back here SILENTLY
    # (no bounded diagnostic, unlike the nudge vars).
    cache_miss_budget: int = 1_000_000
    # Legacy molt-threshold fields, retained ONLY for backward compatibility
    # (old AgentConfig constructions / serialized state still set them). They are
    # NOT the active warning threshold and are no longer read by the warning
    # path: the sustained-pressure warning (meta_block.build_molt_context) is
    # driven by the SessionManager streak and the kernel constants
    # CONTEXT_PRESSURE_* (see top of this module), not by these fields. Legacy
    # init.json molt_notice/molt_pressure/molt_urgency values remain ignored.
    # The 0.75 default here now corresponds to the molt RECOVERY TARGET
    # (CONTEXT_PRESSURE_RECOVERY_TARGET), not an immediate trip-wire.
    molt_notice: float = MOLT_NOTICE_THRESHOLD  # legacy/compat only; == recovery target (0.75), not a trip-wire
    molt_pressure: float = MOLT_PRESSURE_THRESHOLD  # legacy alias; unused by the warning path
    molt_urgency: float = MOLT_URGENCY_THRESHOLD  # legacy alias; unused by the warning path
    ensure_ascii: bool = False  # JSON output: False = readable unicode, True = \uXXXX escapes
    insights_interval: int = 0  # turns between auto-insights; 0 = off
    consultation_past_count: int = 0  # K random past-snapshot consultations per fire; default 0 = current-context soul flow only
    soul_voice: str = "inner"  # consultation prompt profile — "inner" (terse, "you are the soul, speak as inner voice"), "observer" (structured stepped-back hook framing), or "custom" (use soul_voice_prompt). One unified prompt per profile; the per-fire cue text differentiates insights (current diary) vs past (future-self diary).
    soul_voice_prompt: str = ""  # custom voice prompt — only used when soul_voice == "custom". Set/cleared by the agent via soul(action="voice", set="custom", prompt="..."). Length-capped at SOUL_VOICE_PROMPT_MAX in soul.py.
    snapshot_interval: float | None = None  # seconds between git snapshots; None = off

    def __post_init__(self):
        # Resolve the constructor-omitted ``thinking`` sentinel before anything
        # reads the field. ``thinking_omitted`` preserves the provenance for
        # SessionManager, which knows the *effective* provider (this config's
        # ``provider`` may be None, meaning "use the LLMService provider").
        self.thinking_omitted = self.thinking is _THINKING_OMITTED_SENTINEL
        if self.thinking_omitted:
            provider = str(self.provider or "").lower()
            self.thinking = (
                THINKING_OMITTED
                if provider in KIMI_THINKING_PROVIDERS
                else DEFAULT_THINKING
            )

        # Clamp max_aed_attempts to at least 1.  A value of 0 or negative
        # causes the AED retry loop in turn.py to spin forever: aed_attempts
        # starts at 1 (incremented before the equality check) and never equals
        # 0 or a negative max.  See issue #654.
        if self.max_aed_attempts < 1:
            self.max_aed_attempts = 1
