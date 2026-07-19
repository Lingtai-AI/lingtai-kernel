---
name: agent-runtime
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/process_match.py
  - src/lingtai/kernel/process_scan.py
  - src/lingtai/adapters/posix/process_scan.py
  - src/lingtai/adapters/windows/process_scan.py
  - src/lingtai/adapters/process_scan.py
  - src/lingtai/kernel/workdir_lease/CONTRACT.md
  - src/lingtai/kernel/agent_presence/CONTRACT.md
  - src/lingtai/kernel/refresh_watcher/CONTRACT.md
  - src/lingtai/kernel/lifecycle_clock/CONTRACT.md
  - src/lingtai/kernel/notification_store/CONTRACT.md
  - src/lingtai/kernel/event_journal/CONTRACT.md
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - docs/references/windows-support.md
  - tests/test_agent.py
  - tests/test_lifecycle_daemon_shutdown.py
  - tests/test_process_scan.py
  - tests/test_process_match.py
  - tests/test_windows_import_graph.py
  - tests/test_cli.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Core Ports,
  every production Adapter, selector, contract tests, and directly relevant
  component contracts belong here. Re-read this contract whenever a linked
  boundary changes. Update the Ports, affected Adapters, selector, contract
  tests, and this contract in the same change; update the paired Anatomy when
  structure or composition also changes; bump contract_version for a breaking
  Port-contract change. If code and contract disagree, treat the disagreement
  as a defect—do not silently rewrite the normative contract to match the
  implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Agent Runtime

Stable entry: `lingtai.kernel.agent-runtime.v1`.

## Purpose

Agent runtime is the composed lifecycle promise of one LingTai agent process:
what it means for an agent to be constructed, launched, observably alive,
refreshed, and stopped in a working directory — on every supported platform.
This contract owns the *composition* of the capability contracts it links
(workdir lease, agent presence, refresh watcher, lifecycle clock, notification
store, event journal) plus the runtime surfaces that previously had no
normative owner: the CLI lifecycle host's duplicate-launch guard and stop
signals, the canonical agent-run process matcher, the CPR relaunch mechanism,
and the per-platform capability profile. It states each promise once and links
onward; the linked capability contracts remain the normative source for their
own boundaries.

This is also the kernel counterpart of the cross-repository runtime
coordination with the LingTai TUI. Clause acceptance state is explicit: no
clause is marked mutually `accepted` until both repositories' contract
revisions reference each other (see `## Contract rules`).

## Behavior

Agents and coding agents MUST treat the composed lifecycle as one ordered
truth. Construction acquires the workdir lease exactly once (10 s grace) and
rolls back on any construction failure. A live agent's observability is
manifest-first presence plus heartbeat freshness (`.agent.json`,
`.agent.heartbeat`) — never process-table visibility or lock-file existence.
Stop is ordered: daemon teardown → session/mail/journal close → final manifest
write → heartbeat withdrawal → lease release, and the heartbeat stays fresh
through teardown. Refresh is the `.refresh` → `.refresh.taken` handshake with
exactly one detached watcher spawn. Cooperative stop requests are the signal
files (`.suspend`, `.sleep`, `.interrupt`), consumed by the agent's own
heartbeat loop; OS signals and console events are translated into that channel
by the CLI host, never handled ad hoc elsewhere. Capability support is a
per-platform matrix (see the
[`windows-support`](../../../../docs/references/windows-support.md) manual);
an unsupported capability fails loudly at its own selector or composition
gate — never silently degrades, never no-ops.

## Port

This contract owns one new Core Port and composes the linked ones:

- `AgentProcessScanPort` (`src/lingtai/kernel/process_scan.py`) — best-effort
  observation of visible processes as `(pid, command_line)` pairs for the
  duplicate-launch guard. One operation, `iter_process_commands()`; yields
  nothing when the process table is unavailable. It is defense-in-depth beside
  the workdir lease, which remains the exclusion authority.
- `match_agent_run(cmdline, working_dir)`
  (`src/lingtai/kernel/process_match.py`) — the canonical pure matcher for
  agent-run command lines (module/console/legacy forms; both path separators
  as program anchors). The doctor skill carries a stdlib-only copy pinned to
  the same test matrix.
- Composed Ports: `WorkdirLeasePort`, `AgentPresenceStorePort`,
  `RefreshWatcherPort`/`RefreshWatcherProcessPort`, `LifecycleClockPort`,
  `NotificationStorePort`, `EventJournalPort` — each normatively owned by its
  linked contract.

## Adapters

Platform profiles are selector-composed at the composition roots
(`lingtai.cli.build_agent`, `lingtai.Agent`); Core never branches on platform:

- **POSIX profile** — `PosixWorkdirLeaseAdapter` (flock),
  `PosixRefreshWatcherAdapter` (+ POSIX entrypoint/process mechanism),
  `PosixAgentProcessScanAdapter` (`ps`), CLI stop signals SIGTERM/SIGINT,
  CPR relaunch via `start_new_session` detachment.
- **Windows profile** — `WindowsWorkdirLeaseAdapter` (msvcrt byte-0/length-1),
  `WindowsRefreshWatcherAdapter` (+ Windows entrypoint/process mechanism),
  `WindowsAgentProcessScanAdapter` (CIM query), CLI stop signals
  SIGINT/SIGBREAK, CPR relaunch via the shared detached creation flags
  (`lingtai.adapters.windows._win32`).
- **Portable transports** — the presence, notification, event-journal, mail,
  migration-workspace, and Git adapters under `adapters/posix/` are portable
  filesystem/subprocess implementations selected on both platforms; their
  `Posix*` names are historical. Certification is per-platform contract-test
  execution, not duplicate classes.
- Any other platform fails loudly at the first selector
  (`NotImplementedError`), and the process-scan selector returns `None`
  (guard honestly absent; the lease remains the authority).

## Contract rules

Clause IDs are stable; each rule composes the linked normative source.

1. `agent-runtime.paths.v1` — One working directory owns one agent. The
   runtime artifacts are `.agent.json` (manifest), `.agent.heartbeat`
   (liveness), `.agent.lock` (lease), the signal files
   (`.suspend`/`.sleep`/`.interrupt`/`.refresh`/`.refresh.taken`/`.prompt`/
   `.clear`/`.inquiry`/`.rules`), `.notification/`, `logs/`, and
   `history/`. Artifact names and meanings are frozen; observers may read,
   only the owning agent/watcher mutates.
2. `agent-runtime.presence.v1` — Liveness truth is manifest-first presence
   plus heartbeat freshness, per
   [`agent_presence/CONTRACT.md`](../agent_presence/CONTRACT.md). Process
   visibility, PID files, and lock-file existence are never liveness.
3. `agent-runtime.launch.v1` — Construction requires the six injected Ports
   with no defaults, acquires the lease once with 10 s grace per
   [`workdir_lease/CONTRACT.md`](../workdir_lease/CONTRACT.md), and rolls
   back the lease on construction failure. The CLI host refuses to boot when
   the duplicate-launch scan observes a live same-workdir agent run
   (canonical matcher over `AgentProcessScanPort` observations, own PID
   excluded); an unavailable scan falls through to the lease.
4. `agent-runtime.stop.v1` — Stop order is daemon teardown → session/journal
   close → final manifest → heartbeat withdrawal → lease release; the
   heartbeat file's lifetime equals the process's lifetime. Cooperative stop
   arrives via signal files; the CLI host hooks each platform's real stop
   signals (POSIX SIGTERM/SIGINT; Windows SIGINT/SIGBREAK) and translates
   them into `.suspend` + shutdown, nothing else.
5. `agent-runtime.refresh.v1` — Refresh is the `.refresh` → `.refresh.taken`
   handshake with exactly one detached watcher spawn per
   [`refresh_watcher/CONTRACT.md`](../refresh_watcher/CONTRACT.md), on every
   supported platform. Permanent failure publishes the bounded, redacted
   artifact + high-priority notification; it never deletes `.agent.lock`.
6. `agent-runtime.process-identity.v1` — An agent-run process is identified
   by its command line through the canonical matcher; runtime relaunches
   (watcher, CPR, avatar) always use the module form
   (`<python> -m lingtai run <dir>`). PID alone is never authority. CPR
   success is asserted only through fresh target presence/heartbeat, never
   through the child PID. Detachment is `start_new_session` on POSIX and the
   shared detached creation flags on Windows.
7. `agent-runtime.failure.v1` — Unsupported capability = loud failure at the
   owning selector/composition gate with an exact reason. There is no silent
   fallthrough to another platform's mechanism, no no-op adapter, and no
   capability flag. Known degradations are named in the capability matrix.
8. `agent-runtime.compat.v1` — Platform support is published only as the
   per-capability matrix in
   [`docs/references/windows-support.md`](../../../../docs/references/windows-support.md),
   backed by named test suites/CI lanes; there is no single boolean
   "Windows supported" claim. Cross-repository counterpart state: the LingTai
   TUI's `.agent.lock` byte-0/length-1 duplicate-launch probe (TUI PR #687)
   is the accepted *interop target* consumed by
   [`workdir_lease/CONTRACT.md`](../workdir_lease/CONTRACT.md); every
   `agent-runtime.*` clause above is `proposed` toward the TUI's future
   full-lifecycle counterpart and none is mutually `accepted` until both
   repositories' contract revisions cross-reference each other. This clause
   MUST NOT be marked accepted unilaterally.

## Contract tests

Composed behavior is pinned by the linked capability suites plus:
`tests/test_agent.py` (construction/start/stop),
`tests/test_lifecycle_daemon_shutdown.py` (stop ordering incl.
heartbeat-before-release), `tests/test_workdir_lease.py` (lease composition
at the CLI roots), `tests/test_perform_refresh_handshake.py` +
`tests/test_refresh_watcher_windows.py` (refresh on both platforms),
`tests/test_process_match.py` (canonical matcher matrix incl. Windows-shaped
command lines and doctor parity), `tests/test_process_scan.py` (scan Port,
platform selector, CLI stop signals, CPR spawn kwargs, and the Windows
scan→guard wiring), `tests/test_windows_import_graph.py` (the boot-path import
graph survives missing POSIX mechanisms — the construction-gate proof), and
`tests/test_cli.py` (duplicate-guard policy). The
Windows CI lane (`.github/workflows/kernel-windows-pr.yml`) executes the
platform-marked tiers natively; the capability matrix cites which rows carry
native receipts.

## Maintenance

Follow the canonical maintenance block in frontmatter. This contract composes
other contracts: when a linked capability contract changes its own promises,
re-read the composing clause here and update only the composition statement —
never fork or duplicate the capability's normative text into this file.
Cross-repository acceptance state changes (proposed → accepted) require both
repositories' revisions to reference each other and explicit maintainer
authorization.
