---
name: agent-guardian
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/agent_guardian/ANATOMY.md
  - src/lingtai/kernel/agent_guardian/BEHAVIORS.md
  - src/lingtai/kernel/agent_guardian/MANUAL.md
  - src/lingtai/kernel/agent_guardian/__init__.py
  - src/lingtai/adapters/agent_guardian.py
  - src/lingtai/adapters/windows/_win32.py
  - src/lingtai/cli_guardian.py
  - src/lingtai/cli.py
  - src/lingtai/agent.py
  - src/lingtai/tools/system/karma.py
  - src/lingtai/tools/system/CONTRACT.md
  - tests/test_agent_guardian.py
  - tests/test_karma.py
  - pyproject.toml
  - MANIFEST.in
maintenance: |
  Governed by root CONTRACT.md. Keep the paired Anatomy, Core Ports/policy,
  production adapter, composition roots, lifecycle integrations, tests, and
  manual synchronized. Preserve fail-closed durability and the no-actuator
  boundary; bump the version for a breaking Port or event contract.
---
# Agent Guardian Contract

## Purpose

Own one append-only lifecycle/intent ledger and one external observer that
emits `alive|frozen|dead|unknown` plus a shadow recovery plan. It does not alter
public `liveness`. Operator use is taught by [MANUAL.md](MANUAL.md).

## Behavior

Guarded by [G001](BEHAVIORS.md#behavior-g001). A guardian must preserve exact
incarnation, command/workdir, agent-lease, Agent Record, and active-intent
evidence. It must report uncertainty rather than infer death from a stale
heartbeat, must refuse corrupt history and a second guardian, and must never
deliver a signal, launch, CPR, install, configure, or construct an Agent/
provider/tool. Repeated shadow plans are audit-coalesced, not acted upon; the
macOS signal-zero existence probe is read-only evidence, not actuation.

## Port

`LifecycleLedgerPort` owns snapshot, boot, suspend, CPR-clear, and
guardian-verdict operations. `GuardianHostPort` owns guardian lease, clock,
sleep, and one read-only process/lease/manifest/Agent-Record evidence sample.
No action Port exists. Adapter-bound reads and appends use the stable agent
address `agent_dir.name`; copied or mixed foreign-address history fails with
`agent_address_mismatch` before a snapshot is returned.

## Adapters

`FilesystemLifecycleLedgerAdapter` serializes on
`logs/.agent_lifecycle.lock`, appends one compact UTF-8 line, flushes and
file-fsyncs it, and directory-fsyncs first creation where the platform exposes
directory descriptors. After acquiring that shared lock, every mutation fsyncs
the already-existing agent directory so a fresh `logs/` link is durable and a
retry repairs an earlier post-`mkdir` parent-fsync failure; a new event then
fsyncs its file and `logs/`. Reads use one binary descriptor, descriptor
metadata, and a cumulative raw-byte bound; appends read, preflight, append, and
fsync one descriptor under the lock. Windows file fsync and OS byte-range locks
remain in force, but Python
cannot directory-fsync there. `LocalAgentGuardianHostAdapter`
uses OS-native observation without subprocesses or delivered signals and owns
only the separate `system/.agent_guardian.lock`; unavailable safe evidence
becomes `unknown`. Linux supplies the full safe observation. On macOS only,
a libproc miss falls back to `os.kill(pid, 0)`: signal zero is an existence-only
probe that delivers no signal; only `ESRCH` proves absence, while success,
`EPERM`/`EACCES`, and other errors remain unavailable without exact libproc
identity. Windows cannot safely obtain exact command, executable, or stopped-
state evidence here, so an existing PID without those facts remains `unknown`.
Its narrow Win32 query distinguishes `alive|absent|unknown`: invalid-parameter
open failure or an acquired non-active handle proves absence, while access
denial, other open errors, and exit-code query failure stay unknown. Linux
likewise treats missing/inaccessible procfs and mid-observation failure as
unknown; only literal signal-zero `ESRCH` may prove an otherwise missing PID.
Ledger parent and guardian-lease directory/create/open/lock failures are mapped
to stable typed errors; they never escape the CLI or System lifecycle seams as
raw filesystem exceptions.

## Contract rules

1. The exact envelope is `lingtai.agent_lifecycle_event/v1` with version `1`,
   UUID event id, UTC timestamp, agent address, bounded actor/reason, event, and
   payload. Boot carries runtime UUID, PID, start identity, resolved workdir,
   executable, and safe structured program/subcommand/directory evidence. PID
   is an integer in `1..2147483647` (the conservative signed 32-bit domain safe
   for Darwin `ctypes.c_int` and a subset of Windows DWORD); bool is invalid.
   The three durable paths are canonical resolved absolute strings, working-dir
   and command agent-dir are exactly equal, and path/command evidence contains
   no control characters. All persisted text is bounded UTF-8 Unicode scalar
   text and rejects NUL/lone surrogates; human `reason` text alone intentionally
   retains ordinary whitespace. Suspend carries a fresh
   intent UUID; CPR carries the matching `clears_intent_id`; verdict
   carries only bounded classification/digest facts.
2. The active intent is the latest non-overlapping suspend not explicitly
   cleared by matching CPR. Boot, run, crash, heartbeat, signal cleanup,
   and time do not clear it. Ordinary boot is refused atomically under the
   ledger lock with `explicit_suspend_active`; a matching explicit CPR must
   clear the intent before a later boot can register. Duplicate suspend while
   active is coalesced. Any physical or raw-appended `boot_registered` while an
   intent is active is semantic corruption with `boot_while_suspend_active`;
   the production helper additionally retains its early/locked
   `explicit_suspend_active` refusals.
3. Reads are bounded on the opened descriptor to 4 MiB cumulative raw bytes,
   64 KiB/physical record, and 4096 physical records.
   Malformed UTF-8/
   JSON, unsupported schema/event, interior corruption, semantic mismatch, and
   a non-newline final record fail closed; no repair/truncate/backfill exists.
   A unique append is refused before writing if it would exceed any bound, so a
   failed append leaves the existing ledger readable and byte-identical; an
   exact duplicate retry remains byte-identical even at the limit and re-fsyncs
   the existing file/directory reachability before success. There is no
   compaction or retention in this slice. Unchanged guardian policy checkpoints
   once per 24 hours, changed policy records immediately, and backward wall-clock
   movement forces a checkpoint. At that cadence the 4096-record count alone is
   about 11.2 years, before boot/intent/policy-change facts; reaching either
   finite limit fails closed and future retention remains separate work. JSON
   integer-conversion/recursion/Unicode failures are `malformed_record`;
   descriptor growth/shrink and total/row/count overflow are typed corruption,
   and an event that cannot encode is `invalid_event_encoding` before a write.
4. Boot records after Agent construction acquired the workdir lease and before
   `start()` publishes heartbeat/Agent Record. Registration failure stops the
   unstarted Agent and emits one mechanical error. Either a legacy `.suspend`
   marker or durable intent refuses boot before construction; ordinary cleanup
   removes only stale `.sleep`/`.refresh`, preserves `.suspend`, and rechecks that
   marker immediately before the decisive locked registration. The append
   transaction separately repeats the durable active-intent check. Suspend and
   CPR ledger writes precede their existing signal cleanup/action and fail closed.
5. Guardian freshness is `<=120s`; stale/uncertain evidence takes a second
   independently read sample after 2s. Only identical ownership conclusions
   can confirm `dead` or stale `frozen`. Stale heartbeat alone is never dead.
   Linux and macOS exact process observation reads incarnation before and after
   state/argv/executable evidence and accepts exact-running/stopped only when
   both tokens are present, equal to each other, and equal to durable boot.
6. Plans map active intent→`hold_explicit_suspend`; alive→`none`; exact stopped→
   `would_sigcont`; confirmed absent+free→`would_launch`; all uncertainty→
   `observe_only`. Guardian rows require a guardian actor and strict payload
   semantics: alive is exact-running/held/valid/fresh/not-required; frozen is
   exact-stopped/held/valid with fresh/not-required or stale-or-missing/
   confirmed; dead is absent/free/valid/stale-or-missing/confirmed; unknown has
   only changed/unavailable confirmation and a non-decisive evidence tuple.
   Guardian actor id must equal payload guardian id. A Core
   `PresenceSample` is checked before classification: exact presence requires a
   runtime incarnation, coherent exact identity/command/workdir, valid manifest,
   and coherent heartbeat category/age; malformed combinations fail with
   `invalid_presence_sample` and the guardian renders that typed failure.
   Guardian setup reads `.agent.json`; each sample rereads that manifest and
   Agent Record. Each individual read uses one binary descriptor with a 1 MiB
   bound; descriptor growth, oversized/deep JSON, invalid encoding, and
   allocation failure become conservative `malformed`/`unreadable` evidence
   rather than a raw traceback. Active intent
   changes only the plan to `hold_explicit_suspend`. These values
   are data only. A future actuator requires its own recovery lease and durable
   crash-loop budget/backoff.

Public mechanical setup/taxonomy additions in v2 are
`ledger_agent_dir_unavailable`, `ledger_changed_during_read`,
`invalid_event_encoding`, `boot_while_suspend_active`,
`guardian_actor_id_mismatch`, and `invalid_presence_sample`. Existing
field-specific corruption codes and `explicit_suspend_active` remain stable.

## Contract tests

`tests/test_agent_guardian.py` proves durability, physical-row bounds and
duplicate idempotency, descriptor replacement/growth bounds, parent-link and
event-link fsync order, strict boot/verdict/text semantics, bound addresses and
workdirs, both serialized boot/suspend orders, intent reduction and impossible
timeline refusal, every verdict/plan and supported sample round-trip,
confirmation/contradictions, two-token Linux/macOS identity observation,
Linux/Windows tri-state failures, singleton ownership, stable CLI/setup errors,
deep/large-integer JSON containment, Python 3.14 warning-clean control flow,
daily coalescing, macOS signal-zero-only absence probing, and absence of any
delivered signal or subprocess launch. `tests/test_karma.py` proves ledger-first
suspend/CPR failure ordering plus structured filesystem and adversarial-JSON
refusal; existing CLI/liveness/process/lease suites guard regressions.

## Maintenance

Read the paired Anatomy for ownership and the manual for operation. Keep Core
technology-neutral and the filesystem/OS adapter outside it. Do not add an
actuator, attempt schema, service manager, TTL, repair, or public liveness
change without a separately authorized contract revision.
