---
related_files:
  - src/lingtai/kernel/agent_presence/CONTRACT.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/agent_presence/__init__.py
  - src/lingtai/adapters/posix/agent_presence.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/handshake.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - src/lingtai/adapters/posix/mail.py
  - src/lingtai/tools/system/karma.py
  - src/lingtai/tools/avatar/__init__.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Agent Presence Store Anatomy

The Agent Presence Store is the Core-owned presence/liveness boundary for one
agent working directory's `.agent.json` manifest and `.agent.heartbeat`
heartbeat, plus the agent's own heartbeat publication and withdrawal.

## Components

- `AgentPresenceStorePort` defines exactly four construction-bound operations
  (`observe_manifest`, `observe_heartbeat`, `publish_heartbeat`,
  `withdraw_heartbeat`) with no address argument
  (`src/lingtai/kernel/agent_presence/__init__.py`).
- `ManifestObservation` (`ABSENT`/`MALFORMED`/`VALID` + `admin_absent_or_null` +
  parsed `data`) and `HeartbeatObservation` (`ABSENT`/`MALFORMED`/`PRESENT` +
  `wall_seconds`) are the technology-neutral typed evidence values
  (`src/lingtai/kernel/agent_presence/__init__.py`).
- Pure Core policy `is_agent` / `is_human` / `is_alive` (default threshold
  `DEFAULT_LIVENESS_THRESHOLD_SECONDS = 2.0`) derives presence, human, and
  freshness decisions from observations. Core use case `observe_alive` preserves
  manifest-first ordering and skips heartbeat observation for valid humans
  (`src/lingtai/kernel/agent_presence/__init__.py`).
- `PosixAgentPresenceStoreAdapter` maps the Port onto `.agent.json` /
  `.agent.heartbeat` and owns UTF-8/JSON reads, the tri-state mapping, byte-exact
  heartbeat writes, and best-effort withdrawal
  (`src/lingtai/adapters/posix/agent_presence.py`).

## Connections

`BaseAgent` receives the Port as a required constructor dependency
(`self._agent_presence`) and uses it from the heartbeat loop and teardown
(`src/lingtai/kernel/base_agent/__init__.py`,
`src/lingtai/kernel/base_agent/lifecycle.py`). `Agent` and the CLI construct the
POSIX adapter at the outer composition roots (`src/lingtai/agent.py`,
`src/lingtai/cli.py`). Foreign-address observers construct a target-bound adapter
per resolved address and apply the ordered Core `observe_alive` use case: `src/lingtai/adapters/posix/mail.py`
(delivery handshake), `src/lingtai/tools/system/karma.py` (lifecycle gates),
`src/lingtai/tools/avatar/__init__.py` (spawn liveness), and `src/lingtai/agent.py`
(cpr). Address resolution stays in `src/lingtai/kernel/handshake.py`
(`resolve_address`), which no longer owns presence functions.

## Composition

The parent map is `src/lingtai/kernel/ANATOMY.md`; the paired normative interface
is `src/lingtai/kernel/agent_presence/CONTRACT.md`. Core depends inward on the
Port. The POSIX adapter depends on that Port and the kernel-owned
`workdir_layout`, and outer roots inject it. Core never imports the adapter.

## State

Persistent state is the existing `.agent.json` manifest and `.agent.heartbeat`
files under each agent working directory. The POSIX adapter holds only its bound
`WorkdirLayout`; it keeps no long-lived handle or lock. Core retains the freshness
threshold and human-alive policy, not the adapter. The heartbeat wall-clock value
is supplied by the caller (`base_agent/lifecycle` `time.time()`), a deliberate
S7a temporary the S7b clocks will extract.

## Notes

The store does not own address resolution, mail routing, retention's separate
10-second / symlink policy, or lifecycle clocks. It is bound to one directory
(no address argument), does not split foreign observation from own-heartbeat
persistence, and is not a generic filesystem, KV, or service-locator abstraction.
A present-but-malformed manifest still counts as an agent; a JSON manifest value
that is not an object is explicitly malformed. Valid human manifests return alive
without heartbeat observation. Future / `NaN` / `±inf` heartbeat timestamps flow
through the raw freshness comparison unchanged.
