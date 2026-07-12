---
name: workdir-lease
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/workdir_lease/ANATOMY.md
  - src/lingtai/kernel/workdir_lease/__init__.py
  - src/lingtai/adapters/posix/workdir_lease.py
  - src/lingtai/adapters/workdir_lease.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/services/logging.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - tests/test_workdir_lease.py
  - tests/test_workdir_lease_posix_only.py
  - tests/test_services_logging.py
  - tests/test_lifecycle_daemon_shutdown.py
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
# Workdir Lease

## Purpose

Workdir lease is Core's outbound boundary for claiming exclusive use of an
agent's working directory. It separates the safety invariant *"exactly one live
process drives a working directory"* from the concrete exclusion mechanism, which
today is a POSIX `flock` on `.agent.lock`. Core acquires and releases the lease
without knowing the lock file, file descriptor, `fcntl` calls, poll cadence, or
platform. The current authority-bearing consumers are `BaseAgent` construction
and the SQLite event-index rebuild.

## Behavior

Agents and coding agents MUST preserve the current observable semantics: an
exclusive non-blocking claim when `timeout_seconds=0` (one immediate attempt,
raise on contention); a bounded wait that polls until a monotonic deadline for a
positive timeout; the exact contention error `RuntimeError` message
`Working directory '<path>' is already in use by another agent. Each agent needs
its own directory.` from the POSIX adapter; and an idempotent `release` that is
safe to call when the lease is not held. A consumer that receives a lease
receives a real exclusion promise: there is NO disabled, `None`-means-unlocked,
or no-op lease. `BaseAgent` requires an explicit lease at construction and fails
loudly at construction/signature time when none is supplied; it MUST NOT
construct one implicitly or fall back to unlocked. They MUST NOT let concrete
exclusion identities (POSIX vs Windows, `fcntl`, lock-file paths, file
descriptors, `Path`, poll cadence) leak up through this Port, and MUST NOT
construct a concrete adapter inside Core. Lock-file *existence* is not authority —
authority is holding the OS-level exclusive lock.

## Port

`WorkdirLeasePort` exposes exactly two observable operations:

- `acquire(timeout_seconds: float = 0) -> None` — acquire the exclusive lease,
  waiting at most `timeout_seconds` for a held lease to free. `0` (default) makes
  one attempt and raises `RuntimeError` immediately on contention; a positive
  timeout polls until a monotonic deadline and then raises `RuntimeError`. The
  wait/poll mechanism is the adapter's.
- `release() -> None` — release a held lease; idempotent and safe when not held.

The Port names no filesystem or platform vocabulary (`path`, file descriptor,
`fcntl`, `flock`, `msvcrt`, `.agent.lock`, `Path`, poll interval). Those are
adapter construction concerns.

## Adapters

`PosixWorkdirLeaseAdapter` is the only production adapter
(`src/lingtai/adapters/posix/workdir_lease.py`). It leases a working directory
with an exclusive non-blocking `fcntl.flock` on `<workdir>/.agent.lock`, polling
at 250 ms until a monotonic deadline, closing and resetting the file handle on
each failed attempt, and — on `release` — attempting unlock and close before a best-effort `.agent.lock` unlink. It
swallows OS errors and resets the internal handle to `None`, but unlinks only
when the handle reports confirmed closed; a close failure or uncertain closure
leaves the named inode in place so a second holder cannot bind a fresh inode
while the old descriptor may still be locked. `select_workdir_lease` in
`src/lingtai/adapters/workdir_lease.py` is the outer platform selector: on a
supported POSIX platform it returns the POSIX adapter; on any unsupported
platform it fails loudly with `NotImplementedError`. Core never imports the
selector or the adapter. A deterministic in-memory fake in
`tests/_workdir_lease_helpers.py` implements the same Port to prove
substitutability. A Windows production adapter is explicitly out of scope for
this slice; the old `msvcrt` branch is removed rather than retained without a
conformance suite.

## Contract rules

1. `acquire(0)` makes exactly one attempt and raises `RuntimeError` on
   contention. `acquire(timeout>0)` polls at the adapter cadence until a
   monotonic deadline, then raises `RuntimeError`. The POSIX adapter's error
   message is the exact current text (`already in use by another agent`).
2. A held lease excludes a second acquire of the same directory; releasing it
   allows a subsequent acquire to succeed.
3. `release` is idempotent: it attempts unlock, then attempts close in a
   `finally` even when explicit unlock raises, swallows OS errors, and resets its
   internal handle so repeated calls are safe. It unlinks the lock file only
   after the handle reports confirmed closed. If close fails or closure cannot be
   confirmed, the named inode remains so a second holder cannot bind a fresh
   inode while the old descriptor may still be open and locked.
4. There is no disabled or no-op lease. `BaseAgent` requires an explicit lease at
   construction (fail-loud when absent); it acquires exactly once with a 10-second
   grace and releases at teardown, preserving the manifest → heartbeat → release
   ordering even on best-effort error paths. Acquisition is transactional: any
   failure after a successful acquire during construction releases the lease
   (best-effort) and re-raises the original exception, so a partially built agent
   never strands the working directory.
5. The SQLite rebuild receives an explicit lease, acquires with `timeout=0`
   (fails immediately on contention with the current rebuild error), and releases
   it in a single outer `finally` that covers every post-acquire step
   (directory/temp creation and the rebuild itself), so no setup failure strands
   the lease.
6. Outer platform selection fails loudly on unsupported platforms
   (`NotImplementedError`); no `msvcrt` import or Windows production adapter
   exists in this slice.
7. Lock-file existence is not authority; holding the OS-level exclusive lock is.
   Read-only lock *observers* (maintenance retention, doctor diagnostics) inspect
   lock state without acquiring a production lease and are not authority-bearing
   consumers of this Port.
8. Core imports, receives, invokes, and releases only the Port. Concrete POSIX
   construction and platform selection belong to outer composition roots; Core
   never names the adapter or the selector.

## Contract tests

`tests/test_workdir_lease.py` runs the production adapter and an independent
in-memory fake through the shared Port contract (collision, delayed release
before timeout, immediate zero-timeout failure, expiry, idempotent release), and
asserts the POSIX adapter's close-before-unlink release order and exact
contention error text. It behaviorally pins the consumer contracts rather than
searching source text: `BaseAgent` construction calls `acquire` exactly once with
`10` and, on a deliberate post-acquire construction fault, releases the lease
exactly once so a fresh production adapter can re-acquire the same directory
(rollback preserves the original exception). It also proves the outer `Agent`
wrapper closes its owned event journal if default lease selection fails and does
not let a journal-cleanup error replace the original BaseAgent construction
fault. POSIX release regressions cover unlock-only failure by observing the real
handle closed at the unlink boundary — proving
close-before-unlink holds on the unlock-only path. A combined unlock+close
failure leaves the named inode in place and proves a second adapter remains
excluded while the old descriptor is open. It asserts the Port
surface is exactly `acquire`/`release` with no filesystem/platform vocabulary,
proves Core `base_agent`, `base_agent/lifecycle`, and `kernel/services/logging`
never name the concrete adapter or import `lingtai.adapters`, proves the Core
`workdir_lease` package imports no `fcntl`/`flock`/platform module and constructs
no adapter, proves the old `WorkingDir` lock methods are retired (single lock
authority), proves the fake alone cannot satisfy conformance (the production
adapter is always exercised), rejects lock-file existence as authority, and
asserts the CLI composition roots inject `PosixWorkdirLeaseAdapter`.
`tests/test_services_logging.py` pins the rebuild's exact CLI-visible contention
wording and asserts the lease is released exactly once on both a post-acquire
setup failure (temp-dir creation) and an in-rebuild failure.
`tests/test_lifecycle_daemon_shutdown.py` asserts the full
`manifest → heartbeat → release` teardown order, including the
heartbeat-before-release edge. `tests/test_workdir_lease_posix_only.py` proves
unsupported-platform selection fails loudly with the selector's explicit
`NotImplementedError`, that the package facade does not eagerly import `fcntl`
(so importing it or a portable sibling survives a missing `fcntl`, and the
selector — not a bare `ModuleNotFoundError` — owns the failure), and that no
`msvcrt` import or Windows production adapter remains. Exact-checkout provenance
asserts `lingtai.__file__` resolves to this checkout's `src/lingtai/__init__.py`.

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes require
synchronized Port, adapter, contract-test, and contract updates; structural or
composition changes also update the paired Anatomy and reciprocal parents.
