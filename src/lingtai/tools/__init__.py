"""Built-in agent tools under the ``lingtai`` namespace (``lingtai.tools``).

Every concrete built-in Agent tool lives here, one directory per tool package,
flat — no ``intrinsics/`` / ``core/`` / ``capabilities/`` interior ownership
layers. The kernel (``lingtai.kernel``) owns the tool *machinery* (protocol,
schema build, registry wiring, guard, executor, dispatch, meta/notifications);
this package owns the *concrete tools* and the built-in registry that composes
them onto an agent.

Import DAG (enforced by ``tests/test_kernel_isolation.py``):

    lingtai  →  lingtai.tools  →  lingtai.kernel

- ``lingtai.kernel`` imports neither ``lingtai.agent`` nor ``lingtai.tools``.
- ``lingtai.tools`` may import ``lingtai.kernel`` freely (static).
- ``lingtai.agent`` imports ``lingtai.tools`` freely (static).
- ``lingtai.tools`` → ``lingtai`` (agent/services) is allowed **only lazily
  inside setup()/handlers**, never at module top. In particular
  ``import lingtai.tools`` must not transitively import ``lingtai.agent``.

The registry surface (``INTRINSICS``, ``BUILTIN_TOOLS``, ``CORE_DEFAULTS``,
``_GROUPS``, ``setup_capability``, ``apply_core_defaults``,
``normalize_capabilities``, ``expand_groups``, ``get_all_providers``,
``CAPABILITY_UNAVAILABLE``) lives in :mod:`lingtai.tools.registry`. It is intentionally
not re-exported here so that ``import lingtai.tools`` stays a cheap, dependency-light
operation; import from :mod:`lingtai.tools.registry` explicitly.
"""
from __future__ import annotations

__all__: list[str] = []
