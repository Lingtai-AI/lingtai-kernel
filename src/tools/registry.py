"""Built-in tool registry — the composition seam owned by ``tools``.

This is the tools' own catalog data plus the composition helpers that boot them
onto an agent. It owns two layers:

- :data:`INTRINSICS` — the mandatory-intrinsics mapping injected into
  ``BaseAgent(intrinsics=...)`` (the kernel reads it from ``tools.registry``).
- the dynamic-capability registry: :data:`BUILTIN_TOOLS` (capability name →
  ``tools.<pkg>`` module path), :data:`_GROUPS`, :data:`CORE_DEFAULTS`,
  :func:`setup_capability`, :func:`apply_core_defaults`,
  :func:`normalize_capabilities`, :func:`expand_groups`,
  :func:`get_all_providers`, :data:`CAPABILITY_UNAVAILABLE`.

Import discipline: capability modules are resolved with ``importlib`` *inside*
:func:`setup_capability` / :func:`get_all_providers`, never at module top, so
``import tools.registry`` does not eagerly import every tool (and, for the two
capability tools that lazily import ``lingtai`` services, does not pull
``lingtai``). The five intrinsic modules ARE imported statically below because
they are mandatory and cheap; they live under ``tools`` and import only
``lingtai_kernel``.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# Register the tool string catalogs into the kernel i18n cache. Importing the
# registry is the canonical "tools are in play" signal, so this is where the
# en/zh/wen tool strings get injected via lingtai_kernel.i18n.register_strings.
from . import i18n as _i18n  # noqa: F401  (import side effect: register_strings)

if TYPE_CHECKING:
    from lingtai_kernel.base_agent import BaseAgent


# ---------------------------------------------------------------------------
# Layer 1 — mandatory intrinsic tools (injected into BaseAgent)
# ---------------------------------------------------------------------------
#
# Each value has the shape ``{"module": <module>}``; the module exposes the
# duck-typed intrinsic protocol: ``get_schema(lang)``, ``get_description(lang)``,
# ``handle(agent, args)``, and optionally ``boot(agent)``. ``BaseAgent`` iterates
# this mapping in ``_wire_intrinsics``; membership here is the mandatory-include
# mechanism (there is no manifest gate for intrinsics).
from . import email, system, psyche, soul, notification  # noqa: E402  (tools.<pkg>)

INTRINSICS: dict[str, dict[str, Any]] = {
    "email": {"module": email},
    "system": {"module": system},
    "psyche": {"module": psyche},
    "soul": {"module": soul},
    "notification": {"module": notification},
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
# ``tools.<pkg>`` paths (this package is flat: no relative-vs-absolute split like
# the old capabilities/core divide). Resolved lazily by importlib inside
# setup_capability so importing the registry never imports every tool.
BUILTIN_TOOLS: dict[str, str] = {
    "knowledge": "tools.knowledge",
    "skills": "tools.skills",
    "bash": "tools.bash",
    "avatar": "tools.avatar",
    "daemon": "tools.daemon",
    "mcp": "tools.mcp",
    "read": "tools.read",
    "write": "tools.write",
    "edit": "tools.edit",
    "glob": "tools.glob",
    "grep": "tools.grep",
    "vision": "tools.vision",
    "web_search": "tools.web_search",
}

# Group names that expand to multiple capabilities.
_GROUPS: dict[str, list[str]] = {
    "file": ["read", "write", "edit", "glob", "grep"],
}

# Capabilities that boot by default on every Agent — the always-on floor.
# init.json's ``manifest.capabilities`` only needs to declare overrides (kwargs)
# or opt-ins beyond this set; ``manifest.disable`` is the opt-out channel.
#
# ``bash`` defaults to {"yolo": True} (unsandboxed). Hosts that want a sandbox
# pass {"policy_file": "..."} in init.json, which overrides the default kwargs.
# ``vision`` and ``web_search`` are NOT in this set — they require provider
# config and API keys, so they stay explicit opt-in.
CORE_DEFAULTS: dict[str, dict] = {
    "knowledge": {},
    "skills": {},
    "bash": {"yolo": True},
    "avatar": {},
    "daemon": {},
    "mcp": {},
    "read": {},
    "write": {},
    "edit": {},
    "glob": {},
    "grep": {},
}


def apply_core_defaults(
    capabilities: dict[str, dict] | None,
    disable: list[str] | None = None,
) -> dict[str, dict]:
    """Merge ``CORE_DEFAULTS`` with user-supplied capabilities and drop disabled.

    Resolution order (per capability name):
    1. Start with ``CORE_DEFAULTS``.
    2. Overlay ``capabilities`` from init.json — init.json kwargs win on conflict.
       Entries with name not in ``CORE_DEFAULTS`` (e.g. ``vision``,
       ``web_search``) pass through unchanged.
    3. Drop any name listed in ``disable``.

    Returns a fresh dict; does not mutate inputs.
    """
    out: dict[str, dict] = {name: dict(kwargs) for name, kwargs in CORE_DEFAULTS.items()}
    if capabilities:
        for name, kwargs in capabilities.items():
            if kwargs is None:
                # Explicit ``"name": null`` from JSON — disable without needing
                # the ``disable`` list. Useful for one-off opt-outs in init.json.
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
            out.pop(name, None)
    return out


def normalize_capabilities(capabilities: dict[str, dict]) -> dict[str, dict]:
    """Normalize capability configuration.

    ``knowledge`` is the only private durable knowledge capability name. The
    former ``library`` and ``codex`` names are intentionally not normalized:
    this is a breaking rename while the user base is still small. The only
    normalization left here is group expansion fallout and deterministic merge
    of duplicate ``skills.paths`` values.
    """
    out: dict[str, dict] = {}

    def merge_dict(dst: str, value: object) -> None:
        if value is None:
            value = {}
        if dst not in out:
            out[dst] = value if isinstance(value, dict) else value  # type: ignore[assignment]
            return
        if isinstance(out[dst], dict) and isinstance(value, dict):
            merged = dict(value)
            merged.update(out[dst])
            if dst == "skills":
                paths = []
                seen = set()
                for source in (value.get("paths", []), out[dst].get("paths", [])):
                    if not isinstance(source, list):
                        continue
                    for p in source:
                        if isinstance(p, str) and p not in seen:
                            paths.append(p)
                            seen.add(p)
                if paths:
                    merged["paths"] = paths
            out[dst] = merged

    for name, kwargs in capabilities.items():
        merge_dict(name, kwargs)
    return out


def expand_groups(names: list[str]) -> list[str]:
    """Expand group names (e.g. 'file') into individual capability names."""
    result = []
    for name in names:
        if name in _GROUPS:
            result.extend(_GROUPS[name])
        else:
            result.append(name)
    return result


def setup_capability(agent: "BaseAgent", name: str, **kwargs: Any) -> Any:
    """Look up a capability by *name* and call its ``setup(agent, **kwargs)``.

    A setup function returns a manager instance or ``None`` after successful
    registration. ``None`` is success for several core capabilities. To skip
    registration, setup must return ``CAPABILITY_UNAVAILABLE`` before calling
    ``add_tool()``.

    Raises ``ValueError`` if the name is unknown or the module lacks ``setup``.
    """
    module_path = BUILTIN_TOOLS.get(name)
    if module_path is None:
        raise ValueError(
            f"Unknown capability: {name!r}. "
            f"Available: {', '.join(sorted(BUILTIN_TOOLS))}. "
            f"Groups: {', '.join(sorted(_GROUPS))}"
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
        "file": "tools.read",
        "bash": "tools.bash",
        "web_search": "tools.web_search",
        "knowledge": "tools.knowledge",
        "skills": "tools.skills",
        "vision": "tools.vision",
        "avatar": "tools.avatar",
        "daemon": "tools.daemon",
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
