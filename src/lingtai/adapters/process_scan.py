"""Outer platform selector for the duplicate-launch process scan.

Composition-root wiring only: the CLI lifecycle host calls
``select_agent_process_scan`` and applies Core policy (the canonical
``match_agent_run`` matcher and the refusal message) to the returned
observations. Unlike the lease and watcher selectors this one has no
fail-loud branch: the Port itself is best-effort defense-in-depth beside the
workdir lease, so an unknown platform gets the POSIX adapter's behavior of
yielding nothing only if it genuinely is POSIX — otherwise there is no
adapter to select and the guard is honestly absent.
"""
from __future__ import annotations

import os
import sys

from lingtai.kernel.process_scan import AgentProcessScanPort


def select_agent_process_scan() -> AgentProcessScanPort | None:
    """Return the platform's process-scan adapter, or ``None`` when none exists.

    ``None`` means the duplicate-launch guard is unavailable on this platform;
    the caller proceeds to the workdir lease, which is the actual exclusion
    authority. Returning ``None`` (rather than raising) is deliberate: this
    Port improves an error message and must never block boot on an exotic
    platform, and returning a fake empty scanner would falsely claim the
    process table was consulted.
    """
    if sys.platform == "win32":
        from lingtai.adapters.windows.process_scan import WindowsAgentProcessScanAdapter

        return WindowsAgentProcessScanAdapter()
    if os.name == "posix":
        from lingtai.adapters.posix.process_scan import PosixAgentProcessScanAdapter

        return PosixAgentProcessScanAdapter()
    return None


__all__ = ["select_agent_process_scan"]
