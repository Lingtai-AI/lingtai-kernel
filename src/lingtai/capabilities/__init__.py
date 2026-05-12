"""Composable agent capabilities — add via Agent(capabilities=[...])."""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lingtai_kernel.base_agent import BaseAgent

# Registry of built-in capability names → module paths.
# Entries starting with "." are relative to this package (lingtai.capabilities);
# absolute paths point at lingtai.core (the always-on agent floor). Both forms
# work because importlib.import_module honors the `package=` kwarg only for
# relative names.
_BUILTIN: dict[str, str] = {
    # Always-on floor (lingtai.core)
    "knowledge": "lingtai.core.knowledge",
    "skills": "lingtai.core.skills",
    "bash": "lingtai.core.bash",
    "avatar": "lingtai.core.avatar",
    "daemon": "lingtai.core.daemon",
    "mcp": "lingtai.core.mcp",
    "read": "lingtai.core.read",
    "write": "lingtai.core.write",
    "edit": "lingtai.core.edit",
    "glob": "lingtai.core.glob",
    "grep": "lingtai.core.grep",
    # Optional/multimodal capabilities (this package)
    "vision": ".vision",
    "web_search": ".web_search",
}

# Group names that expand to multiple capabilities.
_GROUPS: dict[str, list[str]] = {
    "file": ["read", "write", "edit", "glob", "grep"],
}


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

    Returns whatever the capability's ``setup`` function returns (typically
    a manager instance).

    Raises ``ValueError`` if the name is unknown or the module lacks ``setup``.
    """
    module_path = _BUILTIN.get(name)
    if module_path is None:
        raise ValueError(
            f"Unknown capability: {name!r}. "
            f"Available: {', '.join(sorted(_BUILTIN))}. "
            f"Groups: {', '.join(sorted(_GROUPS))}"
        )
    mod = importlib.import_module(module_path, package=__package__)
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
        "file": "lingtai.core.read",
        "bash": "lingtai.core.bash",
        "web_search": ".web_search",
        "knowledge": "lingtai.core.knowledge",
        "skills": "lingtai.core.skills",
        "vision": ".vision",
        "avatar": "lingtai.core.avatar",
        "daemon": "lingtai.core.daemon",
    }
    result = {}
    for name, module_path in _USER_FACING.items():
        mod = importlib.import_module(module_path, package=__package__)
        providers = getattr(mod, "PROVIDERS", None)
        if providers is not None:
            result[name] = dict(providers)
        else:
            result[name] = {"providers": [], "default": "builtin"}
    return result
