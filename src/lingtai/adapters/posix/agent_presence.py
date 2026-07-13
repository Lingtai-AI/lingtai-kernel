"""POSIX filesystem agent-presence store adapter.

``PosixAgentPresenceStoreAdapter`` implements the Core-owned
``lingtai.kernel.agent_presence.AgentPresenceStorePort`` by observing and
publishing an agent's presence on the local filesystem. It is the only
production presence adapter; Core never constructs it.

The concrete filesystem protocol lives here, not in the technology-neutral Port:
``.agent.json`` / ``.agent.heartbeat`` (named via ``WorkdirLayout``), UTF-8
reads/writes, the JSON parsing mechanism, the exact heartbeat byte encoding
(``str(wall_seconds)`` with no trailing newline), and the best-effort unlink
withdrawal. Each adapter is bound to one working directory at construction, so
foreign-address observation constructs a target-bound adapter per address while
the agent's own runtime constructs one for its own directory.

Behavior is a faithful move of the former ``kernel.handshake`` presence readers
and the ``base_agent/lifecycle`` heartbeat writer/withdrawer: manifest presence
is decided by file existence (a malformed ``.agent.json`` still counts as an
agent), a heartbeat that is missing or unreadable/unparseable is dead, and the
heartbeat withdrawal swallows filesystem errors. This adapter deliberately does
not adopt retention's separate 10-second / symlink policy.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

from lingtai.kernel.agent_presence import (
    AgentPresenceStorePort,
    HeartbeatObservation,
    ManifestObservation,
)
from lingtai.kernel.workdir import workdir_layout


class PosixAgentPresenceStoreAdapter(AgentPresenceStorePort):
    """Filesystem-based agent presence bound to one working directory.

    Address = the agent working directory. Example::

        # Foreign observation: build a target-bound adapter per address.
        presence = PosixAgentPresenceStoreAdapter(peer_dir)
        if is_agent(presence.observe_manifest()):
            ...

        # Own presence: the runtime publishes and withdraws its heartbeat.
        presence = PosixAgentPresenceStoreAdapter(my_working_dir)
        presence.publish_heartbeat(wall_seconds)
        ...
        presence.withdraw_heartbeat()
    """

    def __init__(self, working_dir: str | Path) -> None:
        self._layout = workdir_layout(Path(working_dir))

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def observe_manifest(self) -> ManifestObservation:
        """Observe ``.agent.json`` and map it into tri-state manifest evidence.

        * File not present → ``absent``.
        * Present and parses into a JSON object → ``valid`` (carrying the
          parsed mapping and the ``admin`` missing-or-null fact).
        * Present but unreadable or not a JSON object → ``malformed``.

        Preserves the historical split where ``is_agent`` is decided by file
        existence (so a malformed manifest still counts as an agent) while
        human/identity policy requires a parsed object.
        """
        manifest_path = self._layout.agent_manifest
        if not manifest_path.is_file():
            return ManifestObservation.absent()
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            # Present on disk but unparseable/unreadable. Still an agent by
            # file existence; not a valid manifest for identity/human policy.
            return ManifestObservation.malformed()
        if not isinstance(data, dict):
            # Valid JSON that is not an object (e.g. a bare number or list):
            # the historical ``manifest`` reader returned it, but human policy
            # (``data.get("admin")``) is only meaningful for a mapping. Treat
            # it as present-but-not-a-usable-manifest so ``is_agent`` stays
            # True (file exists) and ``is_human`` stays False, without raising.
            return ManifestObservation.malformed()
        return ManifestObservation.valid(data)

    def observe_heartbeat(self) -> HeartbeatObservation:
        """Observe ``.agent.heartbeat`` and map it into tri-state evidence.

        * File not present → ``absent``.
        * Present and its UTF-8 text parses as a float → ``present`` with the
          raw parsed value (future / ``NaN`` / ``±inf`` preserved verbatim).
        * Present but unreadable or unparseable as a float → ``malformed``.

        Mirrors the former ``handshake.is_alive`` reader:
        ``float(hb.read_text(encoding="utf-8").strip())`` guarded by
        ``except (ValueError, OSError)``.
        """
        heartbeat_path = self._layout.heartbeat
        if not heartbeat_path.is_file():
            return HeartbeatObservation.absent()
        try:
            wall_seconds = float(
                heartbeat_path.read_text(encoding="utf-8").strip()
            )
        except (ValueError, OSError):
            return HeartbeatObservation.malformed()
        return HeartbeatObservation.present(wall_seconds)

    # ------------------------------------------------------------------
    # Own-presence publication
    # ------------------------------------------------------------------

    def publish_heartbeat(self, wall_seconds: float) -> None:
        """Publish the agent's own heartbeat as exact UTF-8 bytes.

        Writes ``str(wall_seconds)`` with no trailing newline, byte-identical to
        the former ``hb_file.write_text(str(agent._heartbeat), encoding="utf-8")``.
        Best-effort: a write ``OSError`` is swallowed, matching the historical
        heartbeat tick which never let a failed write break the loop.
        """
        with contextlib.suppress(OSError):
            self._layout.heartbeat.write_text(
                str(wall_seconds), encoding="utf-8"
            )

    def withdraw_heartbeat(self) -> None:
        """Withdraw the agent's own heartbeat publication.

        Best-effort, idempotent unlink of ``.agent.heartbeat`` swallowing
        ``OSError`` — a faithful move of ``_stop_heartbeat``'s
        ``hb_file.unlink(missing_ok=True)`` under ``except OSError``.
        """
        with contextlib.suppress(OSError):
            self._layout.heartbeat.unlink(missing_ok=True)


__all__ = ["PosixAgentPresenceStoreAdapter"]
