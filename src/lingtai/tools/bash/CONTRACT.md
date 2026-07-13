---
name: bash-contract
tool: bash
contract_version: 3
related_files:
  - src/lingtai/tools/bash/__init__.py
  - src/lingtai/tools/bash/_async_supervisor.py
  - src/lingtai/tools/bash/ANATOMY.md
  - src/lingtai/tools/bash/manual/SKILL.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Bash capability contract

`bash` runs shell commands for an agent that has explicitly opted into shell
access. It is a capability, not an intrinsic, because shell access is powerful
and should be granted deliberately. The implementation lives in
`src/lingtai/tools/bash/`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the shell-execution capability, its policy engine, or the
  async job lifecycle.
- You are reviewing sync-vs-async execution, the sandbox `working_dir` check,
  output truncation, or failure-fidelity warnings.
- You need to verify how async jobs are polled, cancelled, and how completion
  notifications reach the agent.

**Do not use this for:**
- Long-running peer/subagent work: use `daemon` (see
  `src/lingtai/tools/daemon/CONTRACT.md`) — `bash` async jobs are plain background
  processes, not reasoning agents.
- Code navigation only: read `src/lingtai/tools/bash/ANATOMY.md`.

**Fast paths:** tool schema -> §Tool surface; on-disk job layout -> §State &
storage; allow/deny policy -> §Scope; process-group semantics -> §Cross-platform
invariants.

## Scope

- Canonical tool name: `bash`.
- One tool exposes three actions: `run` (default), `poll`, `cancel`.
- Policy is file-based (`bash_policy.json` is the default). `yolo=True` at setup
  installs an allow-everything policy (unsandboxed command set) and is the
  documented default for trusted agents. Two policy modes exist: **allowlist**
  (only listed commands, active whenever an `allow` key is present) and
  **denylist** (everything except listed commands). The mode is implicit.

**Non-goals:** `bash` does not sandbox the command's own filesystem writes
beyond the `working_dir` scope check; it does not manage agent lifecycle; it
does not stream output incrementally (async jobs are polled, not streamed).

## Tool surface

`get_schema` marks `reminder` required at the top schema level to satisfy the
provider-facing required-option contract. Provider-validated sync `run`,
`poll`, and `cancel` calls therefore also carry `reminder` on the wire, but the
handler consumes and validates it only for async `run`; sync `run`, `poll`, and
`cancel` ignore it. Direct async runtime calls that omit it still default to
1800 seconds for compatibility. The handler enforces per-action requirements
for `command` and `job_id`. `action` defaults to `run`.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `run` (sync) | Provider schema: `command`, `reminder`; runtime consumes `command` only | `working_dir`, `timeout` (default 30), `summary` | `{status: "ok", exit_code, stdout, stderr, ok, command_status, warning?}` | `{status: "error", message}` — empty command, policy-denied, cwd outside sandbox, timeout (with broad-scan hint), or spawn failure |
| `run` (async) | Provider/runtime: `command`, `async: true`, `reminder` | `working_dir`, `summary` | `{status: "ok", job_id, pid, message}` | `{status: "error", message}` — same validation errors, invalid boolean/non-numeric/non-finite/negative/too-large `reminder`, plus `Failed to start async job: ...` |
| `poll` | Provider schema: `job_id`, `reminder`; runtime consumes `job_id` only | — | running: `{status: "running", job_id, pid?}` while the recorded supervisor may still commit; known finished: `{status: "done", exit_status_known: true, exit_code, stdout, stderr, ok, command_status, warning?}`; unrecoverable/legacy terminal: `{status: "done", exit_status_known: false, exit_code: null, stdout, stderr}` | `{status: "error", message}` — missing/invalid `job_id`, `Job not found`, or an already terminal-consumed job |
| `cancel` | Provider schema: `job_id`, `reminder`; runtime consumes `job_id` only | — | `{status: "cancelled", job_id}` only after the supervisor has committed the held child's exact terminal status and cancellation atomically consumes/suppresses the job | `{status: "error", message}` — missing/invalid `job_id`, `Job not found`, terminal job, legacy job, or a durable cancellation request still awaiting a terminal commit (which remains pollable/remindable) |

Fidelity fields are additive and keyed off `exit_code`: `ok` is `True` only when
`exit_code == 0`; `command_status` is `"success"`/`"failed"`; `warning` is
present on nonzero exit *or* a zero exit whose output carries a traceback /
missing-module signature. The top-level `status` intentionally stays `ok`/`done`
even when the inner command failed — downstream callers branch on `status`, so
inner failure is surfaced through the additive fields, not by changing `status`.

Unknown/invalid `job_id` values containing `/`, `\`, or `..` are rejected before
any filesystem access (path-traversal guard).

`reminder` is a finite non-negative number of seconds used only for async
`run`; booleans, non-numeric values, non-finite values (`NaN`, `Infinity`),
negative values, and values larger than `threading.TIMEOUT_MAX` are rejected
because the timer backend cannot accept them safely. The schema default is 1800
seconds. Direct runtime calls that omit it still get 1800 seconds, so older
callers keep working even though providers see the field as required.

## State & storage

All paths are relative to the agent working directory (`<agent>/`):

```text
<agent>/system/jobs/<job_id>/
  state.json    # atomically replaced authoritative state (command, cwd, timestamps,
                # start/return handoff leases, supervisor/command PID identities,
                # terminal result, reminder and completion publication claims)
  .state.lock   # POSIX lock serializing manager and supervisor state transitions
  stdout.log    # streamed child stdout, owned/closed by the supervisor
  stderr.log    # streamed child stderr, owned/closed by the supervisor
  command/status/pid  # legacy layout read only for honest unknown-exit recovery
<agent>/.notification/bash.json   # manager-published durable completion wake
<agent>/.notification/system.json # stable-ref last-resort async reminder events
```

New retained IDs are `job-<32 lowercase hex>` (a full UUID4 hex value). The
strict reader also accepts only the old `job-<8 lowercase hex>` form so existing
legacy records remain addressable; all other names are rejected before path use.
Async run uses collision-safe directory creation and first atomically records
`state.json`, including a tokenized finite supervisor-start lease and a bounded
`return_handoff` guard, before starting a detached private supervisor
(`_async_supervisor.py`). The launching manager immediately records the observed
supervisor PID/identity; the supervisor must claim the matching unexpired lease,
recheck it under the state lock immediately before `Popen`, then records its own
incarnation and the command PID. An expired/terminal lease cannot spawn a command.
The supervisor owns the unreaped `Popen` and `wait()` and atomically records the
exact wait status. If it exits before terminal commit, the owning parent reaper
marks the state unrecoverable; after parent loss, an expired launch lease or a
definitively absent recorded supervisor gives a fresh manager equivalent proof.

A missing command PID is never terminal proof while the recorded supervisor is
live/identifiable or cannot yet be disproved: poll reloads for a bounded commit
window and remains `running` if necessary. It records `unrecoverable` only after
the bounded start lease expires or the recorded supervisor incarnation is
definitively gone. Terminal poll and successful cancellation use one conditional
state claim (`terminal_polled` was false and state was terminal); that same atomic
write suppresses the reminder, so only one concurrent manager receives the
terminal response. Job directories and logs remain durable records.
`stdout`/`stderr` are truncated to `max_output` (default 50,000 chars) only when
returned, with a trailing `... (truncated, N chars total)` marker.
Retention/compaction limits for these records and logs are an explicit future
policy; this feature does not delete them.

`reminder.deadline_at`, its publication state, and `return_handoff` are durable.
Initial state records a crash-safe reminder deadline before supervisor launch and
marks the successful-return transition pending. A second manager whose old timer
becomes due while that bounded guard is valid must defer and re-read durable
state; it cannot publish the fallback before the first manager returns. The
successful-return mutation atomically writes `returned_at + reminder` and marks
the handoff armed, so neither the pre-`Popen` window nor the durable-`running`
/pre-return window consumes the caller's requested interval. The call may report
`status: ok` only when that lock-owned pending-to-armed transition wins before
expiry, or when exact completed/failed terminal truth wins under the still-valid
guard. A live owner resuming after expiry returns an explicit `status: error`
containing the durable `job_id`/`pid` and pollable-recovery message; it cannot
recall an already-published fallback and cannot falsely report start success. If
the bounded handoff expires, a live running job retains the crash fallback; an
expired start lease or definitively gone supervisor becomes unrecoverable and
completion owns the wake instead.

On every manager construction, a future non-terminal deadline is re-armed and an
overdue/stale publishing claim is retried. Cancellation uses a bounded durable
`suppressing` state: claims defer through the supervisor's commit window, then an
expired suppression returns to `pending` if the manager died or cancellation did
not commit. The final reminder-sink write and its acknowledgement are serialized
with terminal suppression by the job-state lock: after suppression wins, a stale
claim cannot publish. Exact supervisor terminal commit suppresses any
`pending`/`publishing`/`suppressing` watchdog, and the Bash completion channel owns
that wake-up; a reminder whose sink write linearized first may remain as
historical evidence, but terminal state prevents a later retry. A crash after a
sink write but before its acknowledgement can still retry while the job is
non-terminal. Stable `ref_id="bash.reminder:<job_id>"` deduplicates only while the
bounded/current system sink retains that reference; it is not a global exactly-once
ledger. Completion `ref_id="bash.completion:<job_id>"` likewise avoids an immediate
same-slot rewrite, but the latest-only `bash` sink can be overwritten by another
completion before a crash retry. Completion/ref IDs are correlation aids, not
durable delivery acknowledgements across bounded/latest-only sinks. This is Bash
job state, not a `.notification/cron.json` workflow reminder.

## Cross-platform invariants

DOCUMENT ONLY — do not change these assumptions and do not propose Windows work.

- Sync execution uses `subprocess.run(command, shell=True, ...)` — POSIX shell
  string semantics.
- The detached private supervisor uses `subprocess.Popen(command, shell=True,
  start_new_session=True, ...)`, making the command PID its own process-group
  leader (pgid == pid), while its durable `wait()` result survives a manager
  relaunch.
- Before reporting a command PID as running, Bash compares its persisted OS
  process-start identity with the live PID (Linux boot-id/start ticks; POSIX
  `ps lstart` fallback). A mismatch/unavailable observation is never terminal
  evidence while the recorded supervisor may still commit.
- `cancel` is a durable request, not a manager-side signal. The supervisor holds
  the direct child `Popen` unreaped through the full group `SIGTERM` grace, then
  targets the original group with `SIGKILL` even if the outer shell already
  exited. It reports `group_cancelled` only after proving no live non-zombie group
  member remains and preserves the direct child's exact wait status otherwise.
  Keeping the group leader unreaped prevents its PID/pgid from being recycled
  during that signal sequence. POSIX still cannot promise control of processes
  which deliberately leave the command group.
- A legacy directory without durable supervisor state cannot prove PID identity
  and is therefore never signalable. While its recorded PID is live it remains
  conservatively `running`/uncancellable; after that PID is gone, its explicit
  unknown terminal response is one-shot via a legacy consumption marker and never
  invents `-1` or a false `command_status: failed`.

These POSIX process-group, signal, and `shell=True` assumptions are load-bearing
for cancellation correctness.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| Async `run` returns a `job_id` + `pid` immediately | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_async_run_returns_job_id_and_pid` |
| `poll` returns `running` then `done` with captured output | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_poll_returns_running`, `::test_poll_returns_done_with_output` |
| `cancel` kills the process (group) and reports `cancelled` | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_cancel_kills_process` |
| Nonzero exit is flagged failed with a `warning`, `status` stays `ok`/`done` | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_nonzero_exit_is_flagged_failed_with_warning`, `::test_poll_nonzero_exit_is_flagged_failed` |
| A missing-module / traceback signature is detected in output | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_missing_module_is_detected` |
| Warning-tail redaction fails open when the redactor is unavailable | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_fail_open_returns_input_when_redactor_unavailable` |
| Allowlist mode permits only listed commands; denylist blocks listed ones | `src/lingtai/tools/bash/__init__.py` | `tests/test_layers_bash.py::test_allow_only`, `::test_deny_only`, `::test_pipe_awareness` |
| Policy is enforced on async runs too | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_policy_applies_to_async` |
| Async `reminder` defaults to 1800 for omitted direct calls while schema marks it required | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_schema_requires_reminder_with_runtime_default` |
| Last-resort deadlines are measured from successful async return, and a bounded durable handoff blocks both pre-`Popen` and durable-`running` pre-return publication windows while retaining crash fallback | `src/lingtai/tools/bash/__init__.py`, `_async_supervisor.py` | `tests/test_bash_async.py::test_reminder_deadline_starts_at_successful_async_return`, `::test_return_handoff_blocks_fallback_while_parent_popen_is_delayed`, `::test_return_handoff_blocks_fallback_after_running_before_return_arm`, `::test_owner_resuming_after_handoff_expiry_cannot_report_start_success`, `::test_stale_pre_return_reminder_timer_defers_to_latest_deadline` |
| Supervisor terminal truth, bounded start-lease recovery, parent-reaper/fresh-manager handling of an actual preclaim supervisor exit, PID identity refusal, and sink-idempotent completion wake survive manager loss | `src/lingtai/tools/bash/_async_supervisor.py`, `__init__.py` | `tests/test_bash_async.py::TestBashAsyncRelaunchDurability`, `::test_owned_parent_reaps_actual_supervisor_exit_before_start_claim`, `::test_fresh_manager_recovers_actual_preclaim_exit_after_owner_loss`, `::test_legacy_live_pid_remains_running_and_uncancellable` |
| Reminder validation rejects non-finite and backend-unsafe delays | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_async_reminder_rejects_invalid_values` |
| Deadline claim, bounded cancellation suppression/recovery, and terminal handling have deterministic lock-owned ordering | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_terminal_pop_before_deadline_claim_suppresses_reminder`, `::test_deadline_claim_before_terminal_pop_publishes_once`, `::test_expired_suppressing_reminder_recovers_after_manager_crash` |
| Direct-manager fallback appends remain multi-event safe across managers | `src/lingtai/tools/bash/__init__.py` | `tests/test_bash_async.py::test_direct_manager_fallback_is_serialized_by_shared_store` |
| `yolo=True` allows all commands | `src/lingtai/tools/bash/__init__.py` | `tests/test_layers_bash.py::test_add_capability_bash_yolo` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Async job lifecycle (start/poll/cancel) is correct | `tests/test_bash_async.py` | Run a `sleep 30` async job, poll it, then cancel it | Orphaned processes / zombies; agent cannot stop background work |
| Async reminder does not overwrite a sibling reminder | `tests/test_bash_async.py::test_async_reminder_does_not_overwrite_close_due_jobs` | Start two async `sleep` jobs with short reminders and inspect `.notification/system.json` | Close-due fallback reminders are lost instead of being preserved as separate system events |
| Cancel kills the whole process group | `tests/test_bash_async.py::test_cancel_kills_process` | Start a job that forks children, cancel it, confirm all die | Runaway child processes survive cancellation |
| Policy allow/deny (incl. pipes/chains) is enforced | `tests/test_layers_bash.py` | Configure an allowlist, try a denied command, confirm refusal | Sandbox escape via unlisted or piped commands |
| Inner command failure is surfaced despite `status: ok` | `tests/test_bash_async.py::test_nonzero_exit_is_flagged_failed_with_warning` | Run `python -c 'import nope'`, confirm `ok=false` + `warning` | Agents proceed on silent inner failures |
| `working_dir` stays inside the agent sandbox | `tests/test_layers_bash.py` | Pass an outside path as `working_dir`, confirm error | Commands escape the agent working directory |

Run before merging bash changes:

```bash
python -m pytest tests/test_bash_async.py tests/test_layers_bash.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
