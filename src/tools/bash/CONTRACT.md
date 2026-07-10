---
name: bash-contract
tool: bash
contract_version: 1
related_files:
  - src/tools/bash/__init__.py
  - src/tools/bash/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Bash capability contract

`bash` runs shell commands for an agent that has explicitly opted into shell
access. It is a capability, not an intrinsic, because shell access is powerful
and should be granted deliberately. The implementation lives in
`src/tools/bash/`; the code is the source of truth.

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
  `src/tools/daemon/CONTRACT.md`) — `bash` async jobs are plain background
  processes, not reasoning agents.
- Code navigation only: read `src/tools/bash/ANATOMY.md`.

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

`get_schema` requires nothing at the schema level (`required: []`); the handler
enforces per-action requirements. `action` defaults to `run`.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `run` (sync) | `command` | `working_dir`, `timeout` (default 30), `summary` | `{status: "ok", exit_code, stdout, stderr, ok, command_status, warning?}` | `{status: "error", message}` — empty command, policy-denied, cwd outside sandbox, timeout (with broad-scan hint), or spawn failure |
| `run` (async) | `command`, `async: true` | `working_dir`, `summary` | `{status: "ok", job_id, pid, message}` | `{status: "error", message}` — same validation errors, plus `Failed to start async job: ...` |
| `poll` | `job_id` | — | running: `{status: "running", job_id, pid}`; finished: `{status: "done", exit_code, stdout, stderr, ok, command_status, warning?}` | `{status: "error", message}` — missing/invalid `job_id`, `Job not found`, or `Job already finished (...)` |
| `cancel` | `job_id` | — | `{status: "cancelled", job_id}` | `{status: "error", message}` — missing/invalid `job_id`, `Job not found`, or `Job already finished (...)` |

Fidelity fields are additive and keyed off `exit_code`: `ok` is `True` only when
`exit_code == 0`; `command_status` is `"success"`/`"failed"`; `warning` is
present on nonzero exit *or* a zero exit whose output carries a traceback /
missing-module signature. The top-level `status` intentionally stays `ok`/`done`
even when the inner command failed — downstream callers branch on `status`, so
inner failure is surfaced through the additive fields, not by changing `status`.

Unknown/invalid `job_id` values containing `/`, `\`, or `..` are rejected before
any filesystem access (path-traversal guard).

## State & storage

All paths are relative to the agent working directory (`<agent>/`):

```text
<agent>/system/jobs/<job_id>/
  command       # the command string
  status        # "running" (dir is removed once poll/cancel reaps the job)
  pid           # child PID (also the process-group id; see below)
  stdout.log    # streamed child stdout
  stderr.log    # streamed child stderr
<agent>/.notification/bash.json   # written by the async watcher on job exit
```

`job_id` is `job-<8 hex>`. On `poll`-to-completion or `cancel`, the job
directory is removed with `shutil.rmtree(..., ignore_errors=True)`. The async
watcher thread writes `.notification/bash.json` atomically (`.tmp` + rename) when
the process exits, so completion reaches the agent through the same notification
channel as email/soul/molt. `stdout`/`stderr` are truncated to `max_output`
(default 50_000 chars) with a trailing `... (truncated, N chars total)` marker.

## Cross-platform invariants

DOCUMENT ONLY — do not change these assumptions and do not propose Windows work.

- Sync execution uses `subprocess.run(command, shell=True, ...)` — POSIX shell
  string semantics.
- Async execution uses `subprocess.Popen(command, shell=True,
  start_new_session=True, ...)`, making the child PID its own process-group
  leader (pgid == pid).
- `cancel` relies on that invariant: it sends `SIGTERM` to the whole group via
  `os.killpg(os.getpgid(pid), signal.SIGTERM)`, then reaps the `Popen` handle if
  held (2s wait, then `kill()`), avoiding zombies and orphaned children.
- `poll` falls back to `os.waitpid(pid, os.WNOHANG)` / `os.kill(pid, 0)` when the
  in-process `Popen` handle is not held (different manager instance, same PID
  file).

These POSIX process-group, signal, and `shell=True` assumptions are load-bearing
for cancellation correctness.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| Async `run` returns a `job_id` + `pid` immediately | `src/tools/bash/__init__.py` | `tests/test_bash_async.py::test_async_run_returns_job_id_and_pid` |
| `poll` returns `running` then `done` with captured output | `src/tools/bash/__init__.py` | `tests/test_bash_async.py::test_poll_returns_running`, `::test_poll_returns_done_with_output` |
| `cancel` kills the process (group) and reports `cancelled` | `src/tools/bash/__init__.py` | `tests/test_bash_async.py::test_cancel_kills_process` |
| Nonzero exit is flagged failed with a `warning`, `status` stays `ok`/`done` | `src/tools/bash/__init__.py` | `tests/test_bash_async.py::test_nonzero_exit_is_flagged_failed_with_warning`, `::test_poll_nonzero_exit_is_flagged_failed` |
| A missing-module / traceback signature is detected in output | `src/tools/bash/__init__.py` | `tests/test_bash_async.py::test_missing_module_is_detected` |
| Warning-tail redaction fails open when the redactor is unavailable | `src/tools/bash/__init__.py` | `tests/test_bash_async.py::test_fail_open_returns_input_when_redactor_unavailable` |
| Allowlist mode permits only listed commands; denylist blocks listed ones | `src/tools/bash/__init__.py` | `tests/test_layers_bash.py::test_allow_only`, `::test_deny_only`, `::test_pipe_awareness` |
| Policy is enforced on async runs too | `src/tools/bash/__init__.py` | `tests/test_bash_async.py::test_policy_applies_to_async` |
| `yolo=True` allows all commands | `src/tools/bash/__init__.py` | `tests/test_layers_bash.py::test_add_capability_bash_yolo` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Async job lifecycle (start/poll/cancel) is correct | `tests/test_bash_async.py` | Run a `sleep 30` async job, poll it, then cancel it | Orphaned processes / zombies; agent cannot stop background work |
| Cancel kills the whole process group | `tests/test_bash_async.py::test_cancel_kills_process` | Start a job that forks children, cancel it, confirm all die | Runaway child processes survive cancellation |
| Policy allow/deny (incl. pipes/chains) is enforced | `tests/test_layers_bash.py` | Configure an allowlist, try a denied command, confirm refusal | Sandbox escape via unlisted or piped commands |
| Inner command failure is surfaced despite `status: ok` | `tests/test_bash_async.py::test_nonzero_exit_is_flagged_failed_with_warning` | Run `python -c 'import nope'`, confirm `ok=false` + `warning` | Agents proceed on silent inner failures |
| `working_dir` stays inside the agent sandbox | `tests/test_layers_bash.py` | Pass an outside path as `working_dir`, confirm error | Commands escape the agent working directory |

Run before merging bash changes:

```bash
python -m pytest tests/test_bash_async.py tests/test_layers_bash.py -q
```
