---
related_files:
  - src/lingtai/kernel/workdir_lease/CONTRACT.md
  - src/lingtai/kernel/workdir_lease/__init__.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/services/logging.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Workdir Lease Port Anatomy

This folder is the Core-owned working-directory lease boundary: the
technology-neutral Port that lets Core claim exclusive use of an agent's working
directory without knowing the concrete exclusion mechanism. The production POSIX
adapter that implements it lives outside Core; its promises are defined in the
paired [`CONTRACT.md`](CONTRACT.md).

## Components

- `WorkdirLeasePort` — abstract outbound Port with exactly `acquire` and
  `release` (`src/lingtai/kernel/workdir_lease/__init__.py:16-45`).

## Connections

- Core receives a `WorkdirLeasePort` as the `workdir_lease` constructor argument
  of `BaseAgent` and uses only its methods: `acquire(10)` at construction
  (`src/lingtai/kernel/base_agent/__init__.py:352-358`), guarded by the
  signature-preserving rollback decorator that releases only after a successful
  acquisition (`src/lingtai/kernel/base_agent/__init__.py:254-268`), and
  `release()` at teardown (`src/lingtai/kernel/base_agent/lifecycle.py:292`).
- The SQLite event-index rebuild receives a `WorkdirLeasePort`, acquires it with
  `timeout=0`, and releases it in a single outer `finally` that covers every
  post-acquire step (temp-dir creation and the rebuild itself)
  (`src/lingtai/kernel/services/logging.py:851-977`).
- The only production adapter is `PosixWorkdirLeaseAdapter`
  (`src/lingtai/adapters/posix/workdir_lease.py`), mapped structurally by
  [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).
  The outer selector `select_workdir_lease`
  (`src/lingtai/adapters/workdir_lease.py`) chooses it and fails loud on
  unsupported platforms.
- The composition roots `src/lingtai/agent.py` and `src/lingtai/cli.py` construct
  and inject the adapter.

## Composition

- **Parent:** `src/lingtai/kernel/` (see [`ANATOMY.md`](../ANATOMY.md)).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md) owns the Port's behavioral
  promises and lists the adapter, selector, and contract tests.
- **Adapter package:** [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).

## State

The Port itself owns no state; it is an abstract boundary. The exclusive lock on
`<workdir>/.agent.lock` (the open file descriptor, the OS-level `flock`, and the
best-effort lock-file unlink on release) is owned by the POSIX adapter and
described in its anatomy and the paired contract. Lock-file existence is not
authority; holding the OS lock is.

## Notes

This is a navigation-only Port anatomy; the concrete `flock` mechanism, its
ordering, and its recovery semantics are normative in the paired `CONTRACT.md`.
There is no second concrete lease mechanism in the kernel — the old
`WorkingDir.acquire_lock`/`release_lock` methods and their module-level
`msvcrt`/`fcntl` branch were retired from `kernel/workdir.py`, leaving a single
lock authority behind this Port. Read-only lock observers (maintenance retention,
doctor diagnostics) inspect lock state without acquiring this lease and are not
authority-bearing consumers.
