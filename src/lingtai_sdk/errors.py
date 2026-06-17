"""SDK error surface.

A single SDK base error, runtime-scoped subclasses, plus a re-export of the
kernel's ``UnknownToolError``. Kept in a leaf module with no heavy imports so
``import lingtai_sdk`` stays cheap.
"""
from __future__ import annotations

from lingtai_kernel.types import UnknownToolError


class LingTaiSDKError(Exception):
    """Base class for all SDK-level errors."""


class NativeRuntimeConfigurationError(LingTaiSDKError):
    """Raised when ``NativeRuntime`` cannot build a session from its options.

    The default ``agent_factory`` builds an ``LLMService`` from
    ``RuntimeOptions.provider``/``model`` (with optional ``base_url``/
    ``api_key``); this error is raised — *before* any agent is constructed —
    when that LLM config is partial or absent and no ``agent_factory`` was
    injected to supply a ready service. Its message never echoes ``api_key``.
    """


class NativeRuntimeStartError(LingTaiSDKError):
    """Raised when ``NativeRuntimeSession.start()`` fails to boot the agent.

    Distinct from :class:`NativeRuntimeConfigurationError` (raised *before* any
    agent is constructed, for partial/absent LLM config): this covers failures
    that happen *during* boot — the agent factory raising, agent construction
    raising, or ``agent.start()`` raising. In all cases the session rolls back
    to a safe ``PENDING``/no-agent state (so it can be retried) before this is
    raised. The original failure is chained via ``__cause__`` for diagnosis;
    this error's own message is generic and never echoes ``api_key`` or other
    secrets that may appear in the underlying error.
    """


class BundleLoadError(LingTaiSDKError):
    """Raised when a declared ``BundleManifest`` cannot be loaded from data.

    Covers both shape errors (an unrecognized ``backend_replaceability`` value,
    a non-mapping nested block) and the manifest's own ``validate()`` invariants
    failing — ``load_manifest`` validates before returning, so a loaded manifest
    is always a *valid* manifest.
    """


class BundleHostError(LingTaiSDKError):
    """Raised when a ``BundleHost`` refuses to host or invoke a bundle.

    Refusals are part of the load/host *boundary* contract: a privileged or
    native-only manifest (only the native runtime may host those), a
    manifest/handler mismatch (a declared tool with no handler, or a handler for
    an undeclared tool), or an ``invoke`` of a tool the bundle does not declare.
    """


__all__ = [
    "LingTaiSDKError",
    "NativeRuntimeConfigurationError",
    "NativeRuntimeStartError",
    "BundleLoadError",
    "BundleHostError",
    "UnknownToolError",
]
