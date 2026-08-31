---
related_files:
  - src/lingtai/kernel/agent_guardian/CONTRACT.md
  - src/lingtai/kernel/agent_guardian/BEHAVIORS.md
  - src/lingtai/kernel/agent_guardian/MANUAL.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/tools/system/ANATOMY.md
  - src/lingtai/kernel/agent_guardian/__init__.py
  - src/lingtai/adapters/agent_guardian.py
  - src/lingtai/adapters/windows/_win32.py
  - src/lingtai/cli_guardian.py
  - src/lingtai/cli.py
  - src/lingtai/agent.py
  - src/lingtai/tools/system/karma.py
  - tests/test_agent_guardian.py
  - tests/test_karma.py
  - pyproject.toml
  - MANIFEST.in
maintenance: |
  Keep related_files complete and reciprocal with the paired Contract and parent
  Anatomy. Update this map with the Core, adapter, CLI, lifecycle integrations,
  tests, and manual whenever ownership or state changes; verify citations and
  the architecture graph before merge.
---
# Agent Guardian Anatomy

This component owns durable explicit-suspend intent and a process-external,
shadow-only presence decision for one agent directory.

## Components

- `LifecycleLedgerPort`, immutable evidence, and `evaluate_presence` own the
  versioned event vocabulary, strict boot/verdict/sample validators, reducer,
  and pure policy (`src/lingtai/kernel/agent_guardian/__init__.py:148`,
  `src/lingtai/kernel/agent_guardian/__init__.py:244`,
  `src/lingtai/kernel/agent_guardian/__init__.py:372`,
  `src/lingtai/kernel/agent_guardian/__init__.py:432`,
  `src/lingtai/kernel/agent_guardian/__init__.py:477`,
  `src/lingtai/kernel/agent_guardian/__init__.py:655`); the complete matrix is
  [G001](BEHAVIORS.md#behavior-g001).
- `observe_guardian_manifest` bounds guardian `.agent.json` setup/loop evidence;
  `FilesystemLifecycleLedgerAdapter` owns locked/fsync JSONL, including parent-
  link fsync under the shared lock; the local host adapter owns read-only OS/
  process/lease/Agent-Record evidence and the guardian lock, using the existing
  Windows helper's narrow tri-state observation
  (`src/lingtai/adapters/agent_guardian.py:51`,
  `src/lingtai/adapters/agent_guardian.py:79`,
  `src/lingtai/adapters/agent_guardian.py:508`,
  `src/lingtai/adapters/windows/_win32.py:109`).
- `run_guardian_cli` owns singleton sampling, confirmation, coalescing, JSON,
  and loop cadence (`src/lingtai/cli_guardian.py:111`).
- Existing boot, suspend, and CPR owners append their lifecycle facts before
  the relevant runtime action; ordinary boot's early marker/intent read,
  construction, preserve-suspend cleanup/recheck, and locked append live at
  `src/lingtai/cli.py:352`, `src/lingtai/cli.py:390`, and
  `src/lingtai/cli.py:396` (`src/lingtai/cli.py:252`,
  `src/lingtai/tools/system/karma.py:264`,
  `src/lingtai/tools/system/karma.py:295`, `src/lingtai/agent.py:1386`).

## Connections

`lingtai.cli` composes the adapter for `guardian` and boot. System karma and
Agent CPR use only its intent operations. The host reads the canonical Agent
Record and existing process identity/command matcher; it probes, but never
takes, the agent workdir lease.

## Composition

Parent: `src/lingtai/kernel/ANATOMY.md`. The paired normative interface is
`CONTRACT.md`; `MANUAL.md` teaches the operator surface and `BEHAVIORS.md`
defines the guardian scenario matrix.

## State

Persistent state is `logs/agent_lifecycle.jsonl` plus its serialization lock
`logs/.agent_lifecycle.lock`. Guardian lifetime ownership is the separate
`system/.agent_guardian.lock`. No agent heartbeat, manifest, signal, or lease
file is written by guardian observation.

## Notes

The current public `liveness` command and its 10-second contract are unchanged.
There is no recovery actuator, service installer, provider, tool, or Agent
construction path in this component.
