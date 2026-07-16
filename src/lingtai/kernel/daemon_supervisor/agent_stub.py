"""Minimal duck-typed stand-in for the daemon-scoped runtime inputs a
supervisor process needs to reuse existing daemon/tool-registry/meta-block
helpers that were written against a live ``Agent``.

The detached supervisor deliberately does NOT construct a full
``lingtai.agent.Agent`` bound to the parent's working directory: a second
full ``Agent`` would try to take the parent's workdir lease, write a second
``.agent.heartbeat``, and race the duplicate-process guard — exactly the
failure mode this whole redesign exists to avoid on the *agent* side, now
reintroduced on the *daemon* side if done carelessly. Every helper this stub
must satisfy (``lingtai.tools.registry.setup_capability`` capability
``setup()`` functions, ``lingtai.kernel.meta_block.build_meta`` and its
sub-helpers, ``lingtai.tools.daemon.DaemonManager._build_emanation_prompt``)
only reads a small, defensively-``getattr``'d attribute surface — this class
provides exactly that surface and nothing else. It is not registered with any
workdir lease, heartbeat, or notification store; it never claims agent
identity.
"""
from __future__ import annotations

from pathlib import Path

from lingtai.kernel.config import AgentConfig


class DaemonSupervisorAgentStub:
    """Bare object exposing the attributes daemon/tool-setup helpers read.

    Only ``_working_dir``, ``_config``, and ``_log`` are guaranteed non-empty;
    everything else defaults to values that make the defensive ``getattr(...,
    default)`` call sites in ``meta_block.py`` and capability ``setup()``
    functions behave as if no live session/history/notification state exists
    yet (which is true — this stand-in never runs a parent conversational
    turn loop).
    """

    def __init__(self, working_dir: Path, *, log_fn=None) -> None:
        self._working_dir = Path(working_dir)
        self._config = AgentConfig()
        self._session = None
        self._intrinsics = {}
        self._intrinsic_modules = {}
        self._tool_schemas = []
        self._tool_handlers = {}
        # Detached composition injects a correctly rooted service only when a
        # requested file capability needs it; unlike a full Agent, this stub
        # does not own any other host-service construction.
        self._file_io = None
        self._mcp_tool_names = set()
        self._tool_call_guard = None
        self._log_fn = log_fn

    def _log(self, event_type: str, **fields) -> None:
        if self._log_fn is not None:
            self._log_fn(event_type, **fields)


__all__ = ["DaemonSupervisorAgentStub"]
