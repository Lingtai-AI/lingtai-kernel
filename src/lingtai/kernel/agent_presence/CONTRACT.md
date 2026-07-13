---
name: agent-presence-store
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/agent_presence/ANATOMY.md
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
  - tests/_agent_presence_helpers.py
  - tests/test_agent_presence.py
  - tests/test_handshake.py
  - tests/test_lifecycle_daemon_shutdown.py
  - tests/test_architecture_documents.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Agent Presence Store Contract

## Purpose

The Agent Presence Store owns the kernel's presence and liveness boundary for a
single agent working directory: whether the directory holds an agent, whether
that agent is a human, whether it is currently alive by a fresh heartbeat, and
the agent's own heartbeat publication and withdrawal. Core owns the freshness
threshold, the human-always-alive rule, and the absent/malformed/valid
observation split; the Port expresses observation and own-liveness in
technology-neutral terms. The store does not own address resolution, mail
routing, retention's separate stale/symlink policy, or lifecycle clocks.

## Behavior

Runtime and coding agents MUST observe presence through the injected Port and
Core policy rather than reading `.agent.json` / `.agent.heartbeat` or importing
presence helpers from `handshake`. They MUST preserve the established external
filesystem protocol and its characterized semantics: a present-but-malformed
manifest still counts as an agent; a JSON manifest value that is not an object
is classified as malformed; a human manifest (valid object, `admin`
missing-or-null) is always alive without observing heartbeat; an absent,
unreadable, or unparseable heartbeat is dead; and a non-human agent is alive only when its heartbeat is
strictly fresher than the threshold, with future / `NaN` / `±inf` timestamps
flowing through the raw comparison unchanged. Own-heartbeat publication MUST emit
the exact bytes `str(wall_seconds)` with no trailing newline, and withdrawal MUST
remain best-effort and idempotent, preserving the manifest-persist →
heartbeat-withdraw → workdir-lease-release teardown order. They MUST NOT add a
nullable/no-op store, Path-or-Port overload, address argument, service locator,
hidden Core adapter construction, a fifth operation family, or a direct-heartbeat
dual route, and MUST NOT split foreign observation from own-heartbeat persistence
into separate boundaries.

## Port

`AgentPresenceStorePort` is bound at construction to one observed working
directory and exposes exactly four operations:

1. `observe_manifest() -> ManifestObservation`;
2. `observe_heartbeat() -> HeartbeatObservation`;
3. `publish_heartbeat(wall_seconds: float) -> None`;
4. `withdraw_heartbeat() -> None`.

`ManifestObservation` distinguishes `ABSENT` / `MALFORMED` / `VALID` and, for
`VALID`, carries `admin_absent_or_null` (whether `admin` is missing or null) and
the parsed `data` mapping for identity consumers. `HeartbeatObservation`
distinguishes `ABSENT` / `MALFORMED` / `PRESENT` and carries the parsed
`wall_seconds` float when present. No argument, result, or the Port and its
value objects expose `Path`, files, JSON, symlinks, POSIX names, `time`,
threading, or temp names. There is no fifth family and no address argument.

Pure Core policy lives beside the Port: `is_agent(manifest_obs)` is true for any
non-absent manifest; `is_human(manifest_obs)` is true only for a valid
`admin`-missing-or-null manifest; `is_alive(heartbeat_obs, manifest_obs,
wall_now, threshold=DEFAULT_LIVENESS_THRESHOLD_SECONDS)` returns human-always-alive,
else a strict `wall_now - wall_seconds < threshold` freshness check on a present
heartbeat. `observe_alive(store, wall_now, threshold)` is the mechanism-neutral
Core use case: it observes manifest first, returns human-always-alive without
observing heartbeat, and only then observes heartbeat for non-humans before
delegating to pure `is_alive`. The default threshold is `2.0` seconds; callers
pass their own (e.g. cpr uses `3.0`).

## Adapters

`PosixAgentPresenceStoreAdapter`
(`src/lingtai/adapters/posix/agent_presence.py`) is the one production adapter.
Bound to a working directory via `workdir_layout`, it owns `.agent.json` /
`.agent.heartbeat`, UTF-8 reads/writes, the JSON parsing mechanism, the tri-state
mapping into observations, byte-exact `str(wall_seconds)` heartbeat writes, and
the best-effort `unlink(missing_ok=True)` withdrawal. It is a faithful move of
the former `handshake` presence readers and the `base_agent/lifecycle` heartbeat
writer/withdrawer and does not adopt retention's separate 10-second / symlink
policy. A conforming in-memory fake lives in `tests/_agent_presence_helpers.py`.
Core never imports the adapter package.

## Contract rules

1. `BaseAgent.__init__` accepts a required `agent_presence` Port — never a path,
   optional, default, or no-op adapter — stored as `self._agent_presence`;
   `lifecycle._write_heartbeat_tick` publishes through it and
   `lifecycle._stop_heartbeat` withdraws through it, preserving teardown order.
2. `lingtai.Agent` and `lingtai.cli.build_agent` are composition roots that
   construct `PosixAgentPresenceStoreAdapter(working_dir)`; Core constructs none.
3. Foreign-address observation (`adapters/posix/mail.py`, `tools/system/karma.py`,
   `tools/avatar/__init__.py`, `agent.py` cpr) constructs a target-bound
   `PosixAgentPresenceStoreAdapter` per resolved address and applies
   `observe_alive`, never importing presence functions from `handshake` and never
   observing heartbeat for a valid human manifest.
4. `handshake` retains only pure `resolve_address` path arithmetic; its former
   `is_agent` / `is_human` / `is_alive` / `manifest` presence functions are
   removed, with no shim, Path-or-Port overload, or dual route.
5. Own-heartbeat publication writes exactly `str(wall_seconds)` with no newline;
   withdrawal is best-effort/idempotent. The direct wall clock (`time.time()`)
   feeding `publish_heartbeat` is a deliberate S7a temporary; S7b extracts the
   clocks.
6. The presence Core keeps no service locator, module singleton, hidden adapter
   construction, nullable store, fifth operation family, or foreign/own split.

## Contract tests

`tests/test_agent_presence.py` runs the shared fake/production conformance suite
and locks the observation tri-states, `is_agent`/`is_human`/`is_alive` policy,
manifest-first `observe_alive` ordering (including never observing heartbeat for
a valid human), the strict freshness threshold, malformed/absent heartbeats as
dead, the NaN/±inf/future characterization, exact
`str(wall_seconds)`-no-newline heartbeat bytes, and best-effort idempotent
withdrawal. `tests/test_handshake.py` locks removal of the former presence API;
`tests/test_lifecycle_daemon_shutdown.py` locks manifest-persist →
heartbeat-withdraw → lease-release teardown order.
`tests/test_architecture_documents.py` enforces the governed twin, heading order,
canonical maintenance, and reciprocal links.

## Maintenance

Read the paired Anatomy for locations and composition. Port, adapter, Core
callers, shared conformance tests, and this contract change together. Breaking
Port or semantic changes bump `contract_version`; implementation drift is a
defect, not permission to weaken this contract.
