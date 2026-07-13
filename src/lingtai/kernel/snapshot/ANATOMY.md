---
related_files:
  - ANATOMY.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/kernel/snapshot/CONTRACT.md
  - src/lingtai/kernel/snapshot/__init__.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/adapters/posix/git_cli.py
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/kernel/nudge/ANATOMY.md
  - src/lingtai/kernel/runtime_identity.py
  - src/lingtai/kernel/workdir.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Snapshot + Source Revision Port Anatomy

This folder is the Core-owned boundary for snapshot and source-revision
capabilities. Normative promises live in the paired
[`CONTRACT.md`](CONTRACT.md).

## Components

- `SnapshotPort` — three-family workdir initialization, capture, and maintenance
  boundary.
- `SourceRevisionPort` — two-family bounded revision and tracked-dirty query
  boundary.
- `PosixGitCliAdapter` — one outside fixed-command adapter class implementing
  both interfaces.

## Connections

Lifecycle uses `SnapshotPort` for opt-in initialization, periodic capture, and
daily maintenance. Runtime identity and source-drift capture use
`SourceRevisionPort` with separate caller-owned abbreviation/deadline policy.
`BaseAgent` receives both dependencies. The refresh watcher receives the
parent's captured event identity rather than importing identity helpers.

## Composition

- **Parent:** [`src/lingtai/kernel/ANATOMY.md`](../ANATOMY.md).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md).
- **Adapter package:** [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).
- **Composition roots:** `src/lingtai/agent.py` and `src/lingtai/cli.py` create
  distinct workdir and running-source adapter instances.

## State

The Ports own no mechanism state. Snapshot repository state and subprocess
outcomes remain in the adapter. Core retains snapshot clocks, runtime identity
cache, curated source digest/timestamp, and drift-nudge state.

## Notes

`WorkingDir` now owns layout and manifest duties only. Its five historical Git
methods are retired rather than carried into either Port. Daemon worktree Git is
an unrelated component outside this anatomy.
