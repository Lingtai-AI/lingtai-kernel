"""Built-in tool registry — the composition seam owned by ``lingtai.tools``.

This is the tools' own catalog data plus the composition helpers that boot them
onto an agent. It owns two layers:

- :data:`INTRINSICS` — the mandatory-intrinsics mapping injected into
  ``BaseAgent(intrinsics=...)`` (the kernel reads it from ``lingtai.tools.registry``).
- the dynamic-capability registry: :data:`BUILTIN_TOOLS` (capability name →
  ``lingtai.tools.<pkg>`` module path), :data:`CORE_DEFAULTS`,
  :func:`setup_capability`, :func:`apply_core_defaults`,
  :func:`normalize_capabilities`,
  :func:`get_all_providers`, :data:`CAPABILITY_UNAVAILABLE`.

Import discipline: capability modules are resolved with ``importlib`` *inside*
:func:`setup_capability` / :func:`get_all_providers`, never at module top, so
``import lingtai.tools.registry`` does not eagerly import every tool (and, for the two
capability tools that lazily import ``lingtai`` services, does not pull
``lingtai``). The six intrinsic modules ARE imported statically below because
they are mandatory and cheap; they live under ``lingtai.tools`` and import only
``lingtai.kernel``.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

# Register the tool string catalogs into the kernel i18n cache. Importing the
# registry is the canonical "tools are in play" signal, so this is where the
# en/zh/wen tool strings get injected via lingtai.kernel.i18n.register_strings.
from . import i18n as _i18n  # noqa: F401  (import side effect: register_strings)

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent


# ---------------------------------------------------------------------------
# Layer 1 — mandatory intrinsic tools (injected into BaseAgent)
# ---------------------------------------------------------------------------
#
# Each value has the shape ``{"module": <module>}``; the module exposes the
# duck-typed intrinsic protocol: ``get_schema(lang)``, ``get_description(lang)``,
# ``handle(agent, args)``, and optionally ``boot(agent)``. ``BaseAgent`` iterates
# this mapping in ``_wire_intrinsics``; membership here is the mandatory-include
# mechanism (there is no manifest gate for intrinsics).
from . import email, system, context, soul  # noqa: E402  (lingtai.tools.<pkg>)
# ``psyche`` is the single model-visible root for the four durable domains:
# ``pad + lingtai + knowledge + skills = psyche``. It replaced the four former
# public roots as a clean break: those tool names are unknown and fail loudly,
# and there is no alias for any of them or for the retired ``pad.append`` /
# ``skills.info`` / ``knowledge.info`` actions.
#
# The root name was previously used by a different family (``lingtai_update``,
# ``pad_edit``, ``context_molt``, ``name_set``, ...). Reusing the name grants
# none of those actions: they were dissolved into ``context`` and ``system``
# and are not aliases here. The current family's only actions are five
# strict-empty manual loaders. Root reuse is not action compatibility.
#
# The four domain packages still exist — but as PRIVATE owners only.
# ``pad``/``lingtai`` keep their canonical prompt composers and no longer define
# ``boot()``; ``psyche.boot`` invokes those composers instead, since the
# kernel's boot loop only reaches registered intrinsics.
# ``knowledge``/``skills`` keep their capability ``setup()``, catalog
# composition, configured paths, and one-time legacy migration. None of them
# registers a public tool, and none is imported at this seam any more.
#
# ``context`` is the department that owns the agent's context (molt, summarize,
# rebuild). It absorbed the OLD ``psyche`` family's lifecycle actions, whose
# remaining name actions moved to ``system``; no old ``psyche`` action is
# reachable on the current root of that name.
from . import psyche  # noqa: E402  (lingtai.tools.psyche)

INTRINSICS: dict[str, dict[str, Any]] = {
    "email": {"module": email, "official_plugin": True},
    "system": {"module": system, "official_plugin": True},
    "context": {"module": context, "official_plugin": True},
    "psyche": {"module": psyche, "official_plugin": True},
    "soul": {"module": soul},
}


# ---------------------------------------------------------------------------
# Layer 2 — dynamic capability tools (composed via setup_capability)
# ---------------------------------------------------------------------------


class _CapabilityUnavailable:
    """Signal that a capability setup skipped before registering tools."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "CAPABILITY_UNAVAILABLE"


CAPABILITY_UNAVAILABLE = _CapabilityUnavailable()

# Registry of built-in capability names → module paths. All entries are absolute
# ``lingtai.tools.<pkg>`` paths (this package is flat: no relative-vs-absolute split like
# the old capabilities/core divide). Resolved lazily by importlib inside
# setup_capability so importing the registry never imports every tool.
BUILTIN_TOOLS: dict[str, str] = {
    "knowledge": "lingtai.tools.knowledge",
    "skills": "lingtai.tools.skills",
    # ``bash`` remains a one-way read-only input alias only; the public
    # capability is canonically named ``shell`` while its implementation stays
    # in the retained internal package.
    "shell": "lingtai.tools.bash",
    "avatar": "lingtai.tools.avatar",
    "daemon": "lingtai.tools.daemon",
    "mcp": "lingtai.tools.mcp",
    # Notification is always-on like the former intrinsic, but now mounts only
    # through its declared official host-plugin route.
    "notification": "lingtai.tools.notification",
    # Agent Plugins (agent-plugins.org v1.0.0) catalog. The *tool* is a twin of
    # ``mcp``: pure presentation, zero side effects — it renders the catalog and
    # reports the boot registration snapshot, and no action it exposes mounts
    # anything. Mounting itself happens once per boot/refresh, outside this tool,
    # in ``services.plugin_registry.register_plugins``: a plugin declared in
    # ``init.json`` ``manifest.plugins`` has its validated skill directories
    # composed into the skills catalog and its ``mcp.json`` servers written to
    # ``mcp_registry.jsonl`` as ``source="plugin:<name>"`` records. Registry-level
    # only — nothing is executed, and activating a server still needs an
    # ``init.json`` top-level ``mcp`` entry. A plugin merely *discovered* on an
    # inherited skills path mounts nothing at all.
    "plugin": "lingtai.tools.plugin",
    "task_card": "lingtai.tools.task_card",
    # Unified public file capability: one package owning the composed schema,
    # the envelope dispatch, and all five operation implementations. The
    # pre-migration ``read``/``write``/``edit``/``glob``/``grep`` capabilities
    # and packages are gone, with no alias — those names now fail loudly.
    "file": "lingtai.tools.file",
    "vision": "lingtai.tools.vision",
    # Unified public web capability.  ``web_search`` is a one-way input alias
    # below so old presets materialize this single handler.
    "web": "lingtai.tools.web_search",
}

# Capabilities that boot by default on every Agent — the always-on floor.
# init.json's ``manifest.capabilities`` only needs to declare overrides (kwargs)
# or opt-ins beyond this set; ``manifest.disable`` is the opt-out channel.
#
# ``shell`` defaults to {"yolo": True} (unsandboxed). Hosts that want a sandbox
# pass {"policy_file": "..."} in init.json, which overrides the default kwargs.
# ``vision`` is always registered: its provider defaults to the active LLM
# (the agent's own Responses API), and the analyze call may explicitly borrow
# another preset's vision service via the ``preset`` option. ``web_search``
# is NOT in this set — it requires provider config and API keys, so it stays
# explicit opt-in.
CORE_DEFAULTS: dict[str, dict] = {
    "knowledge": {},
    "skills": {},
    "shell": {"yolo": True},
    "avatar": {},
    "daemon": {},
    "mcp": {},
    # Notification keeps its former mandatory availability, now through the
    # declared official-plugin registrar rather than direct intrinsic wiring.
    "notification": {},
    # Default-on for the same reason ``mcp`` is: the capability is pure
    # presentation. It renders a read-only catalog and writes nothing at all,
    # so booting it on every agent costs one directory scan and risks nothing.
    "plugin": {},
    "task_card": {},
    "file": {},
    "vision": {},
}


def apply_core_defaults(
    capabilities: dict[str, dict] | None,
    disable: list[str] | None = None,
) -> dict[str, dict]:
    """Merge ``CORE_DEFAULTS`` with user-supplied capabilities and drop disabled.

    Resolution order (per capability name):
    1. Start with ``CORE_DEFAULTS``.
    2. Overlay ``capabilities`` from init.json — init.json kwargs win on conflict.
       Entries with name not in ``CORE_DEFAULTS`` (e.g. ``web_search``)
       pass through unchanged.
    3. Drop any ordinary name listed in ``disable``; the official
       ``notification`` mount is retained regardless of capability opt-out
       spelling.

    Returns a fresh dict; does not mutate inputs.
    """
    out: dict[str, dict] = {name: dict(kwargs) for name, kwargs in CORE_DEFAULTS.items()}
    # Notification is an always-on official mount, not a user-selectable
    # capability. Keep this small protected set here because both construction
    # and refresh resolve capability maps through this helper; otherwise either
    # ``disable=["notification"]`` or ``{"notification": null}`` can silently
    # remove the declaration before the official registrar runs.
    always_on_official = {"notification"}
    if capabilities:
        # Normalize here too because callers loading init/preset data may call
        # this helper directly without first passing through Agent.
        for name, kwargs in normalize_capabilities(capabilities).items():
            if kwargs is None:
                # Explicit null is an opt-out for ordinary capabilities only.
                if name in always_on_official:
                    continue
                out.pop(name, None)
                continue
            if name in out and isinstance(out[name], dict) and isinstance(kwargs, dict):
                merged = dict(out[name])
                merged.update(kwargs)
                out[name] = merged
            else:
                out[name] = kwargs
    if disable:
        for name in disable:
            canonical = canonical_capability_name(name)
            if canonical in always_on_official:
                continue
            out.pop(canonical, None)
    for name in always_on_official:
        out.setdefault(name, {})
    return out


# One-way configuration input aliases: a retained legacy config key on the left,
# the canonical public capability it materializes on the right. Never emitted as
# a public capability or tool name. The five pre-migration file capabilities
# (``read``/``write``/``edit``/``glob``/``grep``) are deliberately absent — the
# ``file`` migration was a clean break, so those names are unknown capabilities
# and fail loudly rather than resolving silently.
_LEGACY_CAPABILITY_ALIASES: dict[str, str] = {
    "bash": "shell",
    "web_search": "web",
}


class CapabilityShapeDecision(str, Enum):
    """Typed result of the one canonical/legacy capability-shape evaluator."""

    PASS = "PASS"
    NUDGE = "NUDGE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CapabilityShapeEvidence:
    """Redaction-safe raw/effective evidence for capability compatibility."""

    decision: CapabilityShapeDecision
    compatibility_paths: tuple[dict[str, str], ...] = ()
    conflict_paths: tuple[str, ...] = ()


class CapabilityShapeConflict(ValueError):
    """Raised when canonical and legacy capability values disagree."""


def canonical_capability_name(name: str) -> str:
    """Return the public capability name for a retained legacy input key."""
    return _LEGACY_CAPABILITY_ALIASES.get(name, name)


def _copy_configuration(value: Any) -> Any:
    """Copy JSON-like containers while preserving opaque injected dependencies.

    Browser ports, fake services, locks, clients, and other runtime objects are
    caller-owned identity-bearing values.  ``deepcopy`` silently detached them
    during capability normalization; only configuration containers are copied.
    """
    if isinstance(value, dict):
        return {key: _copy_configuration(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_configuration(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_configuration(item) for item in value)
    if isinstance(value, set):
        return {_copy_configuration(item) for item in value}
    return value


def classify_capabilities(
    capabilities: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], CapabilityShapeEvidence]:
    """Evaluate and materialize the sole ``bash`` -> ``shell`` compatibility rule.

    The input is never mutated. A legacy-only or equal dual spelling is copied to
    one effective ``shell`` entry and produces ``NUDGE`` evidence. Differing dual
    values fail closed rather than allowing canonical-wins normalization. The
    returned mapping is the exact in-memory input for downstream capability setup.
    """
    if capabilities is None:
        return {}, CapabilityShapeEvidence(CapabilityShapeDecision.UNKNOWN)
    if not isinstance(capabilities, Mapping):
        return {}, CapabilityShapeEvidence(CapabilityShapeDecision.UNKNOWN)

    out = {key: _copy_configuration(value) for key, value in capabilities.items()}
    compatibility: list[dict[str, str]] = []
    conflicts: list[str] = []
    for legacy, canonical in _LEGACY_CAPABILITY_ALIASES.items():
        if legacy not in out:
            continue
        mapping = {
            "raw_path": f"manifest.capabilities.{legacy}",
            "effective_path": f"manifest.capabilities.{canonical}",
        }
        compatibility.append(mapping)
        if canonical in out and out[legacy] != out[canonical]:
            conflicts.extend((mapping["raw_path"], mapping["effective_path"]))
    if conflicts:
        return out, CapabilityShapeEvidence(
            CapabilityShapeDecision.BLOCKED,
            compatibility_paths=tuple(compatibility),
            conflict_paths=tuple(conflicts),
        )
    if compatibility:
        for legacy, canonical in _LEGACY_CAPABILITY_ALIASES.items():
            if legacy in out:
                if canonical not in out:
                    out[canonical] = out[legacy]
                out.pop(legacy, None)
        return out, CapabilityShapeEvidence(
            CapabilityShapeDecision.NUDGE,
            compatibility_paths=tuple(compatibility),
        )
    return out, CapabilityShapeEvidence(CapabilityShapeDecision.PASS)


def normalize_capabilities(capabilities: dict[str, dict]) -> dict[str, dict]:
    """Normalize capability configuration using the reader's shape decision.

    Legacy ``bash`` is accepted only as read-only input. Equal dual values and
    legacy-only input materialize one canonical ``shell`` mapping; a differing
    dual pair raises :class:`CapabilityShapeConflict` instead of silently
    choosing one side.
    """
    normalized, evidence = classify_capabilities(capabilities)
    if evidence.decision is CapabilityShapeDecision.BLOCKED:
        raise CapabilityShapeConflict(
            "conflicting capability aliases: "
            + ", ".join(evidence.conflict_paths)
        )
    if evidence.decision is CapabilityShapeDecision.UNKNOWN:
        raise CapabilityShapeConflict("unclassifiable manifest.capabilities shape")

    out: dict[str, dict] = {}
    for name, kwargs in normalized.items():
        destination = canonical_capability_name(name)
        if destination in out:
            # This is defensive for future aliases. The current evaluator has
            # already collapsed bash/shell and therefore cannot reach here.
            existing = out[destination]
            if isinstance(existing, dict) and isinstance(kwargs, dict):
                merged = dict(existing)
                merged.update(kwargs)
                out[destination] = merged
            continue
        out[destination] = kwargs
    return out


def setup_capability(agent: "BaseAgent", name: str, **kwargs: Any) -> Any:
    """Look up a capability by *name* and call its ``setup(agent, **kwargs)``.

    A setup function returns a manager instance or ``None`` after successful
    registration. ``None`` is success for several core capabilities. To skip
    registration, setup must return ``CAPABILITY_UNAVAILABLE`` before calling
    ``add_tool()``.

    Raises ``ValueError`` if the name is unknown or the module lacks ``setup``.
    """
    name = canonical_capability_name(name)
    module_path = BUILTIN_TOOLS.get(name)
    if module_path is None:
        raise ValueError(
            f"Unknown capability: {name!r}. "
            f"Available: {', '.join(sorted(BUILTIN_TOOLS))}."
        )
    mod = importlib.import_module(module_path)
    setup_fn = getattr(mod, "setup", None)
    if setup_fn is None:
        raise ValueError(
            f"Capability module {name!r} does not export a setup() function"
        )
    return setup_fn(agent, **kwargs)


def get_all_providers() -> dict[str, dict]:
    """Return provider metadata for all user-facing capabilities.

    Returns a dict mapping capability name to
    ``{"providers": [...], "default": ... }``.
    Used by ``lingtai-agent check-caps`` CLI.
    """
    _USER_FACING: dict[str, str] = {
        "file": "lingtai.tools.file",
        "shell": "lingtai.tools.bash",
        "web": "lingtai.tools.web_search",
        "knowledge": "lingtai.tools.knowledge",
        "skills": "lingtai.tools.skills",
        "vision": "lingtai.tools.vision",
        "avatar": "lingtai.tools.avatar",
        "daemon": "lingtai.tools.daemon",
        "task_card": "lingtai.tools.task_card",
    }
    result = {}
    for name, module_path in _USER_FACING.items():
        mod = importlib.import_module(module_path)
        providers = getattr(mod, "PROVIDERS", None)
        if providers is not None:
            result[name] = dict(providers)
        else:
            result[name] = {"providers": [], "default": "builtin"}
    return result
