"""Core-owned outbound Port for observing visible process command lines.

The CLI lifecycle host refuses to boot a second agent for a working directory
that another live agent-run process already drives. The *authority* for that
exclusion is the workdir lease (`lingtai.kernel.workdir_lease`); this scan is
deliberate defense-in-depth beside it — it catches the window where an old
process is mid-teardown (heartbeat withdrawn, lease about to release) but
still visible in the process table, and turns it into an exact operator-facing
message instead of a confusing lease error.

This boundary is capability-local to that duplicate-launch guard. It is not a
process-supervision framework: one observation operation, no liveness, no
signaling, no launch. The concrete process-table mechanism (`ps` on POSIX, a
CIM query on Windows) lives entirely in outside adapters; Core sees only
``(pid, command_line)`` pairs and applies the canonical
`lingtai.kernel.process_match.match_agent_run` matcher to them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class AgentProcessScanPort(ABC):
    """Best-effort observation of visible processes' command lines.

    An adapter translates one concrete process-table mechanism into
    ``(pid, command_line)`` observations. Core receives an instance and never
    constructs, imports, or names a concrete adapter.
    """

    @abstractmethod
    def iter_process_commands(self) -> Iterator[tuple[int, str]]:
        """Yield ``(pid, command_line)`` for processes visible to this host.

        Best-effort by contract: when the process table is unavailable the
        adapter yields nothing rather than raising — the workdir lease remains
        the exclusion authority, and an unavailable scan must not block boot.
        A yielded observation is honest: adapters never fabricate or pad
        command lines.
        """
        ...


__all__ = ["AgentProcessScanPort"]
