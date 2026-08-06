"""Immutable description of every current reasoning-effort wiring route.

This module is a capability catalogue, not an output mapper: each
``ReasoningRoute`` mechanically records what the current production wiring
admits, defaults, emits, omits, or drops for one route, keyed by the identity
dimensions that actually select behavior (integration, provider/alias, model
predicate, endpoint/API-compat identity, selected wire, and CLI phase). The
route-local mappers at the real construction sites own the output logic; this
catalogue owns the facts, so a later repair can replace one route owner
without touching another provider or this description's admission projection.

``llm_supports_thinking`` is the manifest-admission projection over these
records; its accepted/rejected set is exactly the historical gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


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

# Stable current-defect IDs owned by the manifest-admission description
# itself: G-1 (explicit thinking admitted only for the Codex family plus
# custom OpenAI-compatible Responses) and G-2 (the exact six-value manifest
# vocabulary with caller-owned init/preset error text and ordering). Later
# PR A1 may delete these only when route descriptors own validation and
# preserve the exact init_schema/preset messages and order. Both IDs ride
# every manifest-admitted record's ``defects`` so the live defect union of
# this catalogue carries them.
ADMISSION_DEFECTS: tuple[str, ...] = ("G-1", "G-2")


@dataclass(frozen=True, slots=True)
class RouteFact:
    """One mechanical current-wiring fact: input category -> disposition.

    ``input`` is a category (``default``, ``None``, one of the six explicit
    values, ``explicit``/``other``/``any``/``invalid``); ``value`` uses the
    ``"<input>"`` sentinel where the input passes through verbatim.
    """

    input: str
    disposition: str  # emitted | omitted | dropped | error | forced | raw
    path: str | None = None
    value: str | None = None
    coupled: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ReasoningRoute:
    """Immutable description of one current reasoning-effort route."""

    route_id: str
    integration: str  # main | native-daemon | external-daemon
    providers: tuple[str, ...]
    # Endpoint identity/predicate: which endpoint family selects this route
    # (``official`` SaaS endpoint, ``custom-base-url``, ``provider-fixed``
    # pinned endpoint, ``codex-backend``, ``google-api``, ``local-cli``,
    # ``in-process``).
    endpoint: str = ""
    api_compat: str | None = None
    wire: str | None = None
    phase: str | None = None
    model_predicate: str | None = None
    # Evidence route id emitted by this route's local mapper (None where the
    # route constructs no session, e.g. daemon argv handoffs).
    wire_route: str | None = None
    admission: str = "rejected"
    # Manifest (provider, api_compat, wire_api) keys admitted by the gate;
    # ``None`` entries are wildcards. Empty for non-admitted routes.
    admission_keys: tuple[tuple[str | None, str | None, str | None], ...] = ()
    error_owner: str | None = None
    baseline: str = ""
    facts: tuple[RouteFact, ...] = ()
    defects: tuple[str, ...] = field(default=())


_MAIN_HIGH_BASELINE = "main omission injects 'high' (resolve_main_thinking)"
_MANIFEST_ERROR_OWNER = (
    "caller-owned messages in init_schema.py / kernel/presets.py"
)
_RESPONSES_ERROR_OWNER = (
    "adapter ValueError: 'OpenAI Responses thinking must be one of "
    "none, minimal, low, medium, high, xhigh, or default'"
)

_CHAT_COLLAPSE_FACTS = (
    RouteFact("default", "omitted"),
    RouteFact("high", "emitted", "reasoning_effort", "high"),
    RouteFact("None", "emitted", "reasoning_effort", "low"),
    RouteFact("other", "emitted", "reasoning_effort", "low"),
)

_RESPONSES_FACTS = (
    RouteFact("default", "omitted"),
    RouteFact("None", "omitted"),
    RouteFact("explicit", "emitted", "reasoning.effort", "<input>"),
    RouteFact("invalid", "error"),
)

_MESSAGES_FACTS = (
    RouteFact(
        "high", "emitted", "thinking.budget_tokens", "16384",
        coupled=(("thinking.type", "enabled"), ("max_tokens", "32768")),
    ),
    RouteFact(
        "low", "emitted", "thinking.budget_tokens", "2048",
        coupled=(("thinking.type", "enabled"), ("max_tokens", "10240")),
    ),
    RouteFact("default", "omitted"),
    RouteFact("None", "omitted"),
    RouteFact("other", "dropped"),
)

_CLI_DROP_FACTS = (
    RouteFact("default", "omitted"),
    RouteFact("None", "omitted"),
    RouteFact("other", "dropped"),
)

_OPENROUTER_COUPLED = (("extra_body.reasoning.include", "False"),)

# Complete external-daemon CLI backend inventory for the ask/resume phase,
# mirroring the availability split ``_BACKEND_SPECS`` gives
# ``_ask_reasoning_construction``: backends with an ask handler build a
# fixed argv (reasoning omitted, startup options never replayed); the
# others do not support the phase. The native ``lingtai`` backend is not an
# external CLI ask route — its ask path is the native-daemon integration.
_EXTERNAL_ASK_FIXED_ARGV_BACKENDS = (
    "claude",
    "claude-interactive",
    "claude-p",
    "claude-code",
    "codex",
    "opencode",
    "mimocode",
    "oh-my-pi",
    "cursor",
)
_EXTERNAL_ASK_UNSUPPORTED_BACKENDS = ("qwen-code", "kimicode")


def _external_ask_route(backend: str, *, supported: bool) -> ReasoningRoute:
    """External-daemon ask/resume-phase description for one CLI backend."""
    if backend in ("claude-code", "claude-p"):
        defects: tuple[str, ...] = ("CL-2",)
    elif backend == "kimicode":
        defects = ("K-4",)
    else:
        defects = ()
    if supported:
        return ReasoningRoute(
            route_id=f"external-daemon:{backend}:ask",
            integration="external-daemon",
            endpoint="local-cli",
            wire_route=f"external-daemon/{backend}/ask",
            providers=(backend,),
            wire="argv",
            phase="ask",
            admission="fixed-argv",
            baseline=(
                "ask/resume constructs a fixed argv via the backend's ask "
                "handler and never replays startup backend options "
                "(_BACKEND_SPECS)"
            ),
            facts=(RouteFact("any", "omitted"),),
            defects=defects,
        )
    return ReasoningRoute(
        route_id=f"external-daemon:{backend}:ask",
        integration="external-daemon",
        endpoint="local-cli",
        wire_route=f"external-daemon/{backend}/ask",
        providers=(backend,),
        wire="argv",
        phase="ask",
        admission="unsupported",
        baseline="ask is unavailable for this backend (_BACKEND_SPECS)",
        facts=(RouteFact("any", "error"),),
        defects=defects,
    )


REASONING_ROUTES: tuple[ReasoningRoute, ...] = (
    ReasoningRoute(
        route_id="main:codex:responses",
        integration="main",
        endpoint="codex-backend",
        providers=THINKING_PROVIDERS,
        api_compat="openai",
        wire="responses",  # REST or WebSocket transport; identical payload
        wire_route="codex/responses",
        admission="manifest",
        admission_keys=(
            ("codex", None, None),
            ("codex-pool", None, None),
            ("codex_pool", None, None),
        ),
        error_owner=_MANIFEST_ERROR_OWNER,
        baseline="main omission stays the 'default' sentinel (resolve_main_thinking)",
        facts=(
            RouteFact("default", "emitted", "reasoning.effort", "xhigh"),
            RouteFact("None", "emitted", "reasoning.effort", "xhigh"),
            RouteFact("explicit", "emitted", "reasoning.effort", "<input>"),
            RouteFact("invalid", "error"),
        ),
        defects=(*ADMISSION_DEFECTS, "G-7"),
    ),
    ReasoningRoute(
        route_id="main:openai:responses:official",
        integration="main",
        endpoint="official",
        providers=("openai",),
        api_compat="openai",
        wire="responses",
        wire_route="openai/responses",
        error_owner=_RESPONSES_ERROR_OWNER,
        baseline=_MAIN_HIGH_BASELINE,
        facts=_RESPONSES_FACTS,
        defects=("G-6",),
    ),
    ReasoningRoute(
        route_id="main:openai:responses:custom",
        integration="main",
        endpoint="custom-base-url",
        # The registered ``openai`` factory forwards ``base_url`` too; the
        # adapter selects this route solely from the base URL, so both
        # provider spellings can reach it. Manifest admission stays the
        # exact (custom, openai, responses) key below.
        providers=("custom", "openai"),
        api_compat="openai",
        wire="responses",
        wire_route="openai-compat:{host}/responses",
        admission="manifest",
        admission_keys=(("custom", "openai", "responses"),),
        error_owner=_RESPONSES_ERROR_OWNER,
        baseline=_MAIN_HIGH_BASELINE,
        facts=_RESPONSES_FACTS,
        defects=(*ADMISSION_DEFECTS, "G-6"),
    ),
    ReasoningRoute(
        route_id="main:openai:chat",
        integration="main",
        endpoint="official",
        providers=("openai",),
        api_compat="openai",
        wire="chat_completions",
        wire_route="openai/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CHAT_COLLAPSE_FACTS,
        defects=("G-3",),
    ),
    ReasoningRoute(
        route_id="main:custom-endpoint:chat",
        integration="main",
        endpoint="custom-base-url",
        # ``openai`` with a forwarded base_url lands on the same endpoint-keyed
        # Chat route as the custom/alias spellings.
        providers=("custom", "openai", "kimi", "grok", "qwen"),
        api_compat="openai",
        wire="chat_completions",
        wire_route="openai-compat:{host}/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CHAT_COLLAPSE_FACTS,
        defects=("G-3", "K-1", "K-2"),
    ),
    ReasoningRoute(
        route_id="main:openrouter:chat",
        integration="main",
        endpoint="provider-fixed",
        providers=("openrouter",),
        api_compat="openai",
        wire="chat_completions",
        wire_route="openrouter/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=(
            RouteFact("default", "omitted", coupled=_OPENROUTER_COUPLED),
            RouteFact(
                "high", "emitted", "reasoning_effort", "high",
                coupled=_OPENROUTER_COUPLED,
            ),
            RouteFact(
                "None", "emitted", "reasoning_effort", "low",
                coupled=_OPENROUTER_COUPLED,
            ),
            RouteFact(
                "other", "emitted", "reasoning_effort", "low",
                coupled=_OPENROUTER_COUPLED,
            ),
        ),
        defects=("G-3",),
    ),
    ReasoningRoute(
        route_id="main:zhipu:chat",
        integration="main",
        endpoint="provider-fixed",
        providers=("zhipu", "glm"),
        api_compat="openai",
        wire="chat_completions",
        wire_route="zhipu/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CHAT_COLLAPSE_FACTS,
        defects=("G-3", "Z-1", "Z-2"),
    ),
    ReasoningRoute(
        route_id="main:deepseek:chat",
        integration="main",
        endpoint="provider-fixed",
        providers=("deepseek",),
        api_compat="openai",
        wire="chat_completions",
        wire_route="deepseek/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CHAT_COLLAPSE_FACTS,
        defects=("G-3", "DS-1"),
    ),
    ReasoningRoute(
        route_id="main:deepseek:responses",
        integration="main",
        endpoint="provider-fixed",
        providers=("deepseek",),
        api_compat="openai",
        wire="responses",
        wire_route="deepseek/responses",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_RESPONSES_ERROR_OWNER,
        facts=_RESPONSES_FACTS,
        defects=("DS-2", "DS-3"),
    ),
    ReasoningRoute(
        route_id="main:mimo:chat",
        integration="main",
        endpoint="provider-fixed",
        providers=("mimo",),
        api_compat="openai",
        wire="chat_completions",
        wire_route="mimo/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CHAT_COLLAPSE_FACTS,
        defects=("G-3",),
    ),
    ReasoningRoute(
        route_id="main:mimo:responses",
        integration="main",
        endpoint="provider-fixed",
        providers=("mimo",),
        api_compat="openai",
        wire="responses",
        wire_route="mimo/responses",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_RESPONSES_ERROR_OWNER,
        facts=_RESPONSES_FACTS,
    ),
    ReasoningRoute(
        route_id="main:anthropic:messages",
        integration="main",
        endpoint="official",
        providers=("anthropic",),
        api_compat="anthropic",
        wire="messages",
        wire_route="anthropic/messages",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_MESSAGES_FACTS,
    ),
    ReasoningRoute(
        route_id="main:minimax:messages",
        integration="main",
        endpoint="provider-fixed",
        providers=("minimax",),
        api_compat="anthropic",
        wire="messages",
        wire_route="minimax/messages",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_MESSAGES_FACTS,
    ),
    ReasoningRoute(
        route_id="main:custom-anthropic:messages",
        integration="main",
        endpoint="custom-base-url",
        # The registered ``anthropic`` factory forwards ``base_url`` too;
        # ``_messages_route_id()`` selects this route solely from it.
        providers=("custom", "anthropic"),
        api_compat="anthropic",
        wire="messages",
        wire_route="anthropic-compat/messages",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_MESSAGES_FACTS,
    ),
    ReasoningRoute(
        route_id="main:gemini:interactions:3plus",
        integration="main",
        endpoint="google-api",
        providers=("gemini", "custom"),
        api_compat="gemini",
        wire="interactions",
        model_predicate="gemini-major>=3",
        wire_route="gemini/interactions",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=(
            RouteFact(
                "any", "emitted", "generation_config.thinking_level", "high"
            ),
        ),
    ),
    ReasoningRoute(
        route_id="main:gemini:interactions:pre3",
        integration="main",
        endpoint="google-api",
        providers=("gemini", "custom"),
        api_compat="gemini",
        wire="interactions",
        model_predicate="gemini-major<3",
        wire_route="gemini/interactions",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CLI_DROP_FACTS,
    ),
    ReasoningRoute(
        route_id="main:gemini:chat:3plus",
        integration="main",
        endpoint="google-api",
        providers=("gemini", "custom"),
        api_compat="gemini",
        wire="chat",  # json_schema-selected native Chat branch
        model_predicate="gemini-major>=3",
        wire_route="gemini/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=(
            RouteFact(
                "any", "emitted", "thinking_config.thinking_level", "HIGH",
                coupled=(("thinking_config.include_thoughts", "True"),),
            ),
        ),
    ),
    ReasoningRoute(
        route_id="main:gemini:chat:pre3",
        integration="main",
        endpoint="google-api",
        providers=("gemini", "custom"),
        api_compat="gemini",
        wire="chat",
        model_predicate="gemini-major<3",
        wire_route="gemini/chat",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CLI_DROP_FACTS,
    ),
    ReasoningRoute(
        route_id="main:claude-code:cli:first",
        integration="main",
        endpoint="local-cli",
        providers=("claude-code", "claude_code"),
        wire="cli",
        phase="first",
        wire_route="claude-code/cli",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CLI_DROP_FACTS,
        defects=("CL-1",),
    ),
    ReasoningRoute(
        route_id="main:claude-code:cli:resume",
        integration="main",
        endpoint="local-cli",
        providers=("claude-code", "claude_code"),
        wire="cli",
        phase="resume",
        wire_route="claude-code/cli",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CLI_DROP_FACTS,
        defects=("CL-1",),
    ),
    ReasoningRoute(
        route_id="main:kimi-code:cli:first",
        integration="main",
        endpoint="local-cli",
        providers=("kimi-code", "kimi_code"),
        wire="cli",
        phase="first",
        wire_route="kimi-code/cli",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CLI_DROP_FACTS,
        defects=("K-3",),
    ),
    ReasoningRoute(
        route_id="main:kimi-code:cli:resume",
        integration="main",
        endpoint="local-cli",
        providers=("kimi-code", "kimi_code"),
        wire="cli",
        phase="resume",
        wire_route="kimi-code/cli",
        baseline=_MAIN_HIGH_BASELINE,
        error_owner=_MANIFEST_ERROR_OWNER,
        facts=_CLI_DROP_FACTS,
        defects=("K-3",),
    ),
    ReasoningRoute(
        route_id="native-daemon:session:default",
        integration="native-daemon",
        endpoint="in-process",
        wire_route="native-daemon/session",
        providers=("*",),
        wire="session",
        admission="forced-default",
        baseline=(
            "all four initial/rebuild construction sites force literal "
            "'default' (_native_daemon_thinking), so provider routes take "
            "their own default branch and may differ from main hydration"
        ),
        facts=(
            RouteFact("any", "forced", "create_session.thinking", "default"),
        ),
        defects=("C-2", "G-7"),
    ),
    ReasoningRoute(
        route_id="external-daemon:startup:argv",
        integration="external-daemon",
        endpoint="local-cli",
        wire_route="external-daemon/startup",
        providers=("*",),
        wire="argv",
        phase="startup",
        admission="raw",
        baseline=(
            "no manifest gate: free-form backend_options (including any "
            "effort flag) become CLI argv verbatim at startup only "
            "(_startup_backend_options_argv); the exact per-input "
            "disposition/path/value/coupled description is callable via "
            "describe_startup_reasoning over the same construction result"
        ),
        # Typed-domain categories only — the exact emitted path/value is
        # per-call (repeats/presence/ambiguous tokens use indexed argv
        # positions), so the static record deliberately claims no fixed
        # path; the callable description owns exactness.
        facts=(
            RouteFact("absent", "omitted"),
            RouteFact("False", "omitted"),
            RouteFact("None", "omitted"),
            RouteFact("empty-list", "omitted"),
            RouteFact("True", "emitted"),
            RouteFact("scalar", "emitted"),
            RouteFact("list", "emitted"),
        ),
        defects=("CL-2", "K-4"),
    ),
    *(
        _external_ask_route(backend, supported=True)
        for backend in _EXTERNAL_ASK_FIXED_ARGV_BACKENDS
    ),
    *(
        _external_ask_route(backend, supported=False)
        for backend in _EXTERNAL_ASK_UNSUPPORTED_BACKENDS
    ),
)

# Named wrapper providers accept a supported explicit ``base_url`` through
# the registered LLMService factories while keeping their provider-fixed
# adapter route ids, so each such default record has an exact
# custom-endpoint variant differing only in endpoint identity and route id.
_WRAPPER_CUSTOM_ENDPOINT_BASE_IDS = (
    "main:openrouter:chat",
    "main:zhipu:chat",
    "main:deepseek:chat",
    "main:deepseek:responses",
    "main:mimo:chat",
    "main:mimo:responses",
    "main:minimax:messages",
)
REASONING_ROUTES = REASONING_ROUTES + tuple(
    replace(route, route_id=f"{route.route_id}:custom", endpoint="custom-base-url")
    for route in REASONING_ROUTES
    if route.route_id in _WRAPPER_CUSTOM_ENDPOINT_BASE_IDS
)


def describe_reasoning_construction(construction) -> RouteFact:
    """Project one route-local construction result into an exact immutable fact.

    This is the callable-description bridge for routes whose exact emitted
    path/value depends on the typed input (e.g. external-daemon startup):
    the fact carries the same disposition, requested input, emitted
    path/value, and every coupled occurrence that the carrier itself
    verified — one owner, no second heuristic.
    """
    return RouteFact(
        input=construction.requested,
        disposition=construction.disposition,
        path=construction.emitted_path,
        value=construction.emitted_value,
        coupled=construction.coupled,
    )


def reasoning_route(route_id: str) -> ReasoningRoute:
    """Return the catalogue description for ``route_id`` (KeyError if unknown)."""
    for route in REASONING_ROUTES:
        if route.route_id == route_id:
            return route
    raise KeyError(route_id)


def _gemini_major(model: str) -> int | None:
    """Extract the Gemini major version from a model name, mirroring the
    Gemini adapter's model gate exactly (agreement is test-enforced)."""
    parts = model.lower().replace("models/", "").split("-")
    if len(parts) >= 2 and parts[0] == "gemini":
        try:
            return int(parts[1].split(".")[0])
        except (ValueError, IndexError):
            return None
    return None


def _model_predicate_matches(predicate: str, model: str) -> bool:
    """Evaluate a catalogue model predicate against a concrete model name."""
    if predicate == "gemini-major>=3":
        major = _gemini_major(model)
        return major is not None and major >= 3
    if predicate == "gemini-major<3":
        return not _model_predicate_matches("gemini-major>=3", model)
    raise KeyError(predicate)


def find_reasoning_routes(
    *,
    integration: str | None = None,
    provider: str | None = None,
    endpoint: str | None = None,
    api_compat: str | None = None,
    wire: str | None = None,
    phase: str | None = None,
    model: str | None = None,
) -> tuple[ReasoningRoute, ...]:
    """Look up catalogue records by the identity dimensions themselves.

    Each supplied dimension filters; omitted dimensions match everything.
    ``provider`` honors the ``("*",)`` wildcard, and ``model`` evaluates a
    record's model predicate so callers can resolve the model-gated route
    without knowing route IDs.
    """
    matches: list[ReasoningRoute] = []
    for route in REASONING_ROUTES:
        if integration is not None and route.integration != integration:
            continue
        if (
            provider is not None
            and route.providers != ("*",)
            and provider not in route.providers
        ):
            continue
        if endpoint is not None and route.endpoint != endpoint:
            continue
        if api_compat is not None and route.api_compat != api_compat:
            continue
        if wire is not None and route.wire != wire:
            continue
        if phase is not None and route.phase != phase:
            continue
        if (
            model is not None
            and route.model_predicate is not None
            and not _model_predicate_matches(route.model_predicate, model)
        ):
            continue
        matches.append(route)
    return tuple(matches)


def llm_supports_thinking(llm: dict) -> bool:
    """Return whether a manifest LLM block accepts explicit thinking effort.

    Projection over the catalogue's manifest-admission keys; the accepted and
    rejected sets are exactly the historical gate's.
    """
    provider = str(llm.get("provider") or "").lower()
    api_compat = str(llm.get("api_compat") or "").lower()
    wire_api = str(llm.get("wire_api") or "").lower()
    for route in REASONING_ROUTES:
        for key_provider, key_compat, key_wire in route.admission_keys:
            if provider != key_provider:
                continue
            if key_compat is not None and api_compat != key_compat:
                continue
            if key_wire is not None and wire_api != key_wire:
                continue
            return True
    return False
