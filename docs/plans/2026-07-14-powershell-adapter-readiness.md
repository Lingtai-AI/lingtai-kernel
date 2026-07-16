---
related_files:
- CONTRACT.md
- src/lingtai/kernel/refresh_watcher/CONTRACT.md
- src/lingtai/kernel/workdir_lease/CONTRACT.md
- src/lingtai/kernel/agent_presence/CONTRACT.md
- src/lingtai/kernel/mail_transport/CONTRACT.md
- src/lingtai/kernel/migrate/CONTRACT.md
- src/lingtai/kernel/notification_store/CONTRACT.md
maintenance: |
  Keep the readiness matrix, stage checkboxes, exact acceptance criteria, and
  current slice synchronized with production code in every owning PR. A checked
  stage means its current production adapter, composition, focused contract
  tests, and paired Contract/Anatomy evidence all landed together.
lifecycle: temporary
temporary_until: |
  Archive this execution plan only after the supported Windows tier passes
  native acceptance and every remaining stage is either completed or recorded
  as an explicit non-goal in permanent platform-support documentation.
---

# PowerShell / Windows adapter-readiness workflow

## Goal

Reach a source state where Windows and PowerShell support is added through
capability-native adapters and one outer Windows composition profile, without
rewriting Core policy.

This does **not** mean one universal Windows class. File persistence, workdir
leasing, shell language, process supervision, and interactive terminals vary on
different axes. The completion rule for each platform-sensitive capability is:

1. Core owns a technology-neutral Port and normative Contract.
2. Current behavior is provided by a real production adapter outside Core.
3. An outer selector/composition root chooses and injects that adapter.
4. A small shared contract suite proves every production adapter against the
   same observable semantics.
5. Adding Windows support changes only the matching adapter, its registration,
   native acceptance, and synchronized structural documentation — not Core.

A portable Python filesystem adapter may be reused on Windows. A `Posix` class or
package name is not evidence that a second implementation is necessary.

## Execution rules

- **One complete runnable boundary per PR.** A slice travels with its Contract,
  production adapter, selector/composition, minimum contract tests, and paired
  Anatomy updates.
- **Production code first.** Do not substitute simulators, source-keyword
  matrices, copy-parity suites, receipt frameworks, or stage validators for the
  requested boundary.
- **Skip already-ready capabilities.** Do not manufacture a PR merely to satisfy
  this checklist or rename a class in isolation.
- **Preserve the correct variation axis.** Shell language and process
  supervision are separate; Core refresh policy is separate from both.
- **Keep unsupported behavior explicit.** Fail loudly until a real production
  adapter exists; never add a no-op or pretend a POSIX mechanism is portable.
- **Continue after each slice.** Completing one PR is not completion of this
  program. Return to the first unchecked stage unless a real gate blocks it.

## Audited baseline

The initial source audit used live `main` at
`3e93ae790945314351507c46cc9875c3d035627e` after cleanup PR #935. The Stage 1
review candidate was then ported without cleanup onto live `main` at
`c9b5a45b39fa60d19df7647d578415a7cdbf6d93` after PRs #936 and #937 moved the
base; the preserved old-base worktree is snapshot evidence only.

| Capability | Current classification | Evidence and Windows consequence |
| --- | --- | --- |
| LifecycleClock | **READY / portable** | The system clock adapter is platform-neutral. Reuse and certify. |
| WorkdirLease | **READY boundary / Windows adapter missing** | Core has `WorkdirLeasePort`, POSIX `flock` is outside Core, and `select_workdir_lease` owns platform choice. Windows needs a real cross-process lease adapter plus selector registration; Core must not change. |
| AgentPresence | **READY mechanism / composition consolidation pending** | The implementation is pathlib/JSON/UTF-8/read-write/unlink with no POSIX syscall. Treat it as a portable filesystem adapter pending native Windows acceptance; do not create a duplicate class from the stale name alone. Current Agent/CLI/CPR/karma/avatar construction is scattered and must eventually route through the Windows profile. |
| MailTransport | **READY mechanism / composition consolidation pending** | The filesystem transport uses pathlib, threading, shutil, and same-volume `os.replace` claims. Reuse only after native Windows tests prove file/directory replace and open-handle preconditions; do not assume a second implementation merely from the current package name. |
| MigrationWorkspace | **READY / portable** | Core owns seven logical operations. The filesystem adapter uses sibling temp files and same-volume `os.replace`; register the existing implementation in the Windows profile and certify it natively. Do not add a cross-process migration lock. |
| NotificationStore | **READY / portable** | Core owns seven persistence families. The filesystem adapter uses a documented per-instance `threading.Lock` and sibling atomic replace. Reuse and certify; do not invent a lock file, named mutex, or caller-held transaction. Open issue #742 remains a separate current-source concurrency problem and is not silently absorbed by this workflow. |
| Snapshot / SourceRevision | **READY boundary / certification pending** | Git CLI behavior is outside Core. Prove the selected Windows `git.exe` path and native behavior; do not refactor Core speculatively. |
| RefreshWatcher | **READY boundary / Windows adapters missing** | Core retains refresh policy behind outer hand-off and watcher-local process Ports; POSIX adapters now own process observation, liveness, stop, detached relaunch, and platform selection. Windows needs matching outer/inner adapters and selector registration; Core must not change. |
| Bash synchronous shell | **CORE MECHANISM LEAK** | `shell=True` and POSIX command parsing are concrete shell-language semantics. Earn a Bash-local `ShellDialect` boundary before adding PowerShell. |
| Bash asynchronous execution | **CORE MECHANISM LEAK** | `fcntl`, `/proc`/`ps`, `killpg`, process groups, and detached launch remain in the supervisor. Keep the durable async state machine; extract only process mechanism. |
| External daemon CLI backends | **CORE MECHANISM LEAK** | Backend argv/parsing and process supervision are still directly coupled to `Popen` and POSIX process-group termination. Migrate one headless backend family at a time. The in-process LingTai backend is a separate portable sub-surface. |
| Avatar launcher | **CORE MECHANISM LEAK** | Validation, boot handshake, and ledger policy are independent, but launch still directly owns POSIX detached-process mechanics. |
| Interactive daemon / PTY | **DEFERRED** | ConPTY and interactive Claude are a third axis. They do not block the headless support tier. |
| Windows composition profile | **WIRING GAP** | Create the profile only after capability-native boundaries exist. Reuse portable filesystem adapters and register genuine Windows lease/shell/process implementations. |

## Dependency-ordered stages

### Stage 1 — make RefreshWatcher process ownership honest

- [x] Add the smallest watcher-local process-mechanism Port/value objects needed
  by existing policy: process/command observation, detached relaunch, graceful
  termination, forced termination.
- [x] Keep refresh retry, heartbeat, stale-duplicate decision, canonical
  `match_agent_run`, ACK/lock deadlines, redaction, and permanent-alert policy in
  Core.
- [x] Move current `ps`/PID observation, TERM/KILL, and relaunch `Popen`/
  session detachment into the POSIX watcher adapter/entrypoint.
- [x] Add one fail-loud selector for the RefreshWatcher capability and route
  `lingtai.Agent` and CLI composition through it.
- [x] Update RefreshWatcher Contract/Anatomy and adapter Anatomy in the same PR.
- [x] Prove the Core policy with a small fake mechanism and retain the existing
  real POSIX entrypoint/refresh tests.

**Stage 1 non-goals:** no global `ProcessSupervisor`, no Bash/daemon/avatar
migration, no Windows implementation, no rewrite of retry/handshake/alert
policy, and no source-keyword harness.

**Stage 1 minimum validation:**

```text
python -m pytest \
  tests/test_refresh_watcher_process.py \
  tests/test_perform_refresh_handshake.py \
  tests/test_deep_refresh.py \
  tests/test_process_match.py -q
python -m pytest tests/test_architecture_documents.py -q
# plus the repository Anatomy drift check and git diff --check
```

**Stage 1 candidate evidence (2026-07-14):** exact-worktree focused +
architecture suite `104 passed in 7.43s`; post-review architecture suite `3
passed`; Anatomy drift `0`; `git diff --check` clean. An independent read-only
Terra review through Codex CLI found only stale Anatomy line ranges; those
ranges and one current-main baseline citation were repaired and revalidated.

### Stage 2 — separate Bash shell language

- [x] Define a Bash-local shell-language Port for policy-command extraction and
  script/argv invocation formation. Encoding/error settings may be included
  only when genuinely dialect-specific; stdout/stderr capture and truncation,
  timeout, and exit truth remain manager/supervisor responsibilities.
- [x] Wire the current POSIX shell behavior as the first production adapter.
- [x] Keep process-tree cancellation outside this Port.

**Stage 2 candidate evidence (2026-07-14):** an exact-worktree parent run with
the repository venv recorded **140 passed in 18.42s** across the new shell
contract plus the existing synchronous and asynchronous Bash suites.
`git diff --check` and targeted compile checks were clean. The actual PowerShell
adapter is deferred to Stage 6, after Stage 3 process supervision exists. Every
dialect must provide its own policy parser and may not silently bypass policy.

### Stage 3 — separate Bash asynchronous process supervision

- [x] Extract spawn, identity, wait, exit attribution, and tree cancellation from
  the async supervisor into a Bash-local process Port.
- [x] Keep durable leases, reminders, poll/cancel state, and terminal-result
  fidelity in the existing policy layer.
- [x] Wire POSIX process groups and the Bash-local state lock as production
  adapters. Windows Job Object and Windows state-lock implementations are
  intentionally deferred to Stage 6, where Windows composition and native
  acceptance register and certify them behind these fixed contracts.

Stage 3 does not claim Windows process or locking support; its dependency
correction is to cut and certify the Bash-local contracts and POSIX production
composition before Stage 6 native Windows work.

**Stage 3 candidate evidence (2026-07-14):** the exact-worktree parent run recorded
**147 passed in 19.15s** across Bash async, process-Port contract, shared shell
invocation, and layer tests. Architecture/docs recorded **73 passed in 0.93s**;
docs governance validated **249 documents**, Anatomy drift was **0/49**, targeted
compile and `git diff --check` were clean. Terra's first exact-candidate review
found one live-supervisor identity transition blocker; nullable neutral identity
plus an exact transition test closed it, and Terra re-review returned **ACCEPT**
on patch SHA-256 `63382a6fd2998cb226380d7b94ca7c3ce5b1f70555589c0346887a891274c351`.

### Stage 4 — migrate headless external daemon supervision

- [x] Start with Codex CLI as the representative backend and preserve its
  backend-specific argv/MCP/session/JSONL/usage/result parsing for both initial
  `emanate` and `exec resume` ask.
- [x] Move Codex process ownership to a daemon-local Port with the current POSIX
  production adapter; watchdog group timeout and lifecycle reclaim/agent-stop
  sweep both Port-owned Codex processes and transitional legacy processes.
- [x] Move the shared OpenCode/MiMo/Oh-My-Pi initial and resume lifecycle to the
  same Port; preserve their Manager-owned command, JSONL, session, and usage
  policy. Other headless families remain transitional.
- [x] Migrate remaining headless backend families incrementally.
- [ ] Leave the in-process LingTai backend unchanged.

**Stage 4 first-slice candidate evidence (2026-07-15):** the final exact
focused Port/Codex/watchdog/lifecycle/runtime/Contract/attribution slice recorded
**56 passed in 1.58s**; full `tests/test_daemon.py` recorded **105 passed in
24.19s**; the other backend-options cases recorded **57 passed / 1 deselected**,
with the sole helper-arity failure reproduced identically on committed HEAD.
Architecture + Contract recorded **7 passed**; docs governance validated **249
documents**, Anatomy drift was **0/49**, and compile/import/diff checks were
clean. Terra found one snapshot/release lifecycle-sweep blocker; the narrow
per-handle repair plus deterministic group/all regressions closed it, and exact
re-review returned **ACCEPT** on patch SHA-256
`a06dd15a0ebc7b1fc0a9326ca35f5dd812468567f86929e5e71905f8457f39d9`.
A mechanically verified Claude Opus 4.8 final exact-candidate review also
returned **ACCEPT**. Production checkpoint commit: `64efa057`. Remaining
headless families and native Windows support are explicitly pending.

**Stage 4 Qwen/Kimi slice (2026-07-15):** Qwen Code and Kimi Code initial
headless one-shot runners now use the injected daemon-local Process Port with
batch `group_id` ownership and no local stdout deadline. Manager retains their
backend-specific argv, environment/MCP policy, output/completion gates, and
terminal classification. Focused injected-Port coverage proves command/env,
opaque-handle release, and Port receipt attribution; remaining headless
families and native Windows support remain pending.

**Stage 4 Claude slice (2026-07-15):** Claude print-mode initial and
ask/resume lifecycle now use the injected daemon-local Process Port. Interactive
Claude PTY and Cursor remain transitional and out of scope; remaining headless
families and native Windows support remain pending.

**Stage 4 Cursor slice (2026-07-15):** Cursor Agent CLI headless initial and
ask/resume lifecycle now use the injected daemon-local Process Port. Manager
retains exact argv/options, working-directory, stream-json, session/model/usage,
result/error, completion, and notification policy. This closes the remaining
headless process migrations; interactive Claude PTY/ConPTY and native Windows
support remain pending.

### Stage 5 — extract the avatar launcher

- [x] Preserve avatar validation, init preparation, ledger/rules, and heartbeat
  boot policy.
- [x] Move only interpreter launch, detachment, liveness, and termination behind
  an avatar-local launcher Port.
- [ ] Add a Windows launcher adapter. This re-cut intentionally keeps only the
  POSIX reference adapter and the fail-loud selector; no controlled-double or
  native Windows acceptance is evidence of Windows support.

Stage 5 parent-validated candidate evidence (2026-07-15): the shared launcher
Contract covered the POSIX reference adapter; focused avatar/rules/preset/timezone
suites including the manager release integration recorded 81 passed;
architecture-document and drift-checker suites recorded 12 passed; glossary
validation covered 54 resources; isolated imports and touched-file AST parsing
were clean. This re-cut deliberately withdraws the Windows adapter, selector
wiring, and acceptance test; native Windows support remains pending Stage 6.

### Stage 6 — compose and certify the Windows profile

- [ ] Reuse the portable LifecycleClock, AgentPresence, MailTransport,
  MigrationWorkspace, NotificationStore, and other proven portable adapters.
- [ ] Register genuine Windows implementations for WorkdirLease, ShellDialect,
  process supervision, RefreshWatcher, and avatar launch.
- [ ] Keep compatibility-preserving neutral adapter renames/re-homing inside this
  production composition slice, not in standalone rename PRs.
- [ ] Run native acceptance in tiers:
  1. import, construct, lease;
  2. start, heartbeat, migration, notification, internal email, stop, lease
     reacquire;
  3. PowerShell argv/UTF-8/stdout/stderr/timeout/exit truth;
  4. process-tree cancellation, async Bash, headless daemon, refresh/CPR,
     avatar;
  5. optional ConPTY/interactive surfaces.

## Program non-goals

- No universal `Platform`, `ProcessSupervisor`, `Shell`, `CommandRunner`, or
  generic filesystem/KV Port merely because several call sites use stdlib APIs.
- No clean rewrite of existing policy before its real mechanism boundary is
  extracted and behavior remains locked.
- No duplicate Windows filesystem adapter when the current mechanism is portable
  and native evidence confirms it.
- No weakening of error, persistence, atomic-replace, lock, identity,
  notification, or exit-status truth to make tests pass.
- No ConPTY requirement for the first headless support tier.
- No release, deployment, or platform-support claim based only on mocked POSIX
  tests; the final claim requires native Windows evidence.

## Updating this file

Every owning PR must update only the status and evidence it actually changes:

1. Check a stage item only after production code, focused tests, and paired
   documentation have landed together.
2. Add the exact PR/link and native evidence beside the checked item.
3. If source evidence disproves a planned abstraction, revise the workflow before
   coding rather than forcing the source through the old plan.
4. Return to the first unchecked dependency after each merge; do not treat a
   local subtask or one PR as program completion.
