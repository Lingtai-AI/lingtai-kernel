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
    "library": "lingtai.core.library",
    "skills": "lingtai.core.skills",
    # Deprecated compatibility names. Agent config normalization maps these
    # before setup; direct callers still resolve for one release window.
    "codex": "lingtai.core.library",
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


def canonical_capability_name(name: str) -> str:
    """Return the canonical post-rename capability name for *name*.

    Compatibility mapping:
    - ``codex`` (old durable knowledge) -> ``library``
    - ``library`` keeps its new meaning here; raw init/preset manifests that
      predate the rename are normalized by ``normalize_capabilities`` below so
      old ``library.paths`` becomes ``skills.paths`` before setup.
    """
    return "library" if name == "codex" else name


def normalize_capabilities(capabilities: dict[str, dict]) -> dict[str, dict]:
    """Normalize old capability names to the post-rename surface.

    Old manifests commonly contain both ``codex`` and ``library``. In that
    context the old ``library`` entry is the skill catalog, so it becomes
    ``skills`` while old ``codex`` becomes the new knowledge ``library``.
    New manifests should use ``library`` for knowledge and ``skills`` for the
    skill catalog. If both old and new keys are present, canonical keys win and
    missing ``paths`` from the old skill-catalog key are merged into ``skills``.
    """
    out: dict[str, dict] = {}
    has_old_codex = "codex" in capabilities

    def merge_dict(dst: str, value: object) -> None:
        if value is None:
            value = {}
        if dst not in out:
            out[dst] = value if isinstance(value, dict) else value  # type: ignore[assignment]
            return
        if isinstance(out[dst], dict) and isinstance(value, dict):
            merged = dict(value)
            merged.update(out[dst])
            # Preserve skill path extras from either spelling.
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
        if name == "codex":
            target = "library"
        elif (
            name == "library"
            and "skills" not in capabilities
            and (
                has_old_codex
                or (isinstance(kwargs, dict) and "paths" in kwargs)
            )
        ):
            # Old pair: codex + library, or old library-only config carrying
            # path extras. The library entry was the skill catalog.
            target = "skills"
        else:
            target = name
        merge_dict(target, kwargs)
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
        "library": "lingtai.core.library",
        "skills": "lingtai.core.skills",
        "codex": "lingtai.core.library",
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
