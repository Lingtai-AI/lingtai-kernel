---
related_files:
  - src/lingtai/ANATOMY.md
  - src/tools/bash/__init__.py
  - src/tools/bash/bash_policy.json
  - src/tools/bash/manual/SKILL.md
  - tests/test_bash_async.py
  - tests/test_layers_bash.py
  - src/tools/bash/glossary-en.md
  - src/tools/bash/glossary-zh.md
  - src/tools/bash/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# core/bash

Bash capability — shell command execution with file-based policy. Adds the
ability to run shell commands. This is a capability (not intrinsic) because
not every agent should have shell access — it's a powerful ability that should
be explicitly opted into.

## Components

- `bash/__init__.py` — the entire capability in a single file. `get_description` (`__init__.py:175`), `get_schema` (`__init__.py:179-223`), `setup` (`__init__.py:822-860`). Two core classes: `BashPolicy` for command filtering (`__init__.py:227-315`), `BashManager` for execution (`__init__.py:318-819`). Module-level result helpers: `_augment_command_result` adds `ok`/`command_status`/`warning` fidelity fields (`__init__.py:121-173`), `_detect_failure_signature` labels `python_traceback`/`missing_module` (`__init__.py:64-78`), `_broad_scan_hint` returns timeout recipe text for broad recursive walks (`__init__.py:91-118`).
- `bash/bash_policy.json` — default denylist policy shipped with the kernel. Denies destructive (`rm`, `rmdir`, `shred`, `dd`), privilege escalation (`sudo`, `su`, `doas`), permission changes (`chmod`, `chown`, `chgrp`), disk management (`mount`, `umount`, `mkfs`, `fdisk`), package managers (`apt`, `apt-get`, `yum`, `dnf`, `brew`), process control (`kill`, `killall`, `pkill`, `shutdown`, `reboot`, `systemctl`), network (`nc`, `ncat`), and code execution (`eval`, `exec`).

## Public API

The `bash` tool supports synchronous and asynchronous execution:

| Parameter      | Type     | Description |
|----------------|----------|-------------|
| `command`      | string   | Shell command to execute (required for `run`) |
| `timeout`      | number   | Timeout in seconds (default: 30, sync only) |
| `working_dir`  | string   | Working directory for execution (default: agent's working dir) |
| `action`       | string   | `run` (default), `poll`, or `cancel` |
| `async`        | boolean  | If true, run in background and return job_id immediately (default: false) |
| `reminder`     | number   | Top-level schema-required field; provider calls carry it for every action, but runtime consumes/validates it only for async `run` (default 1800) |
| `job_id`       | string   | Job ID for `poll` and `cancel` actions |

**Sync mode** (`async=false`, default): Returns `{status, exit_code, stdout, stderr, ok, command_status[, warning]}` once the command completes, or `{status: "error", message}` only when the shell itself could not run it (empty command, policy denial, timeout, spawn failure).

**Async mode** (`async=true`): Returns `{status: "ok", job_id, pid, message}` immediately. Use `action="poll"` with the job_id to check status: returns `{status: "running", job_id, pid}` while running, or `{status: "done", exit_code, stdout, stderr, ok, command_status[, warning]}` once finished. Use `action="cancel"` to kill the process group. Async run validates `reminder` as a finite non-negative number bounded by `threading.TIMEOUT_MAX` and defaults omitted direct calls to 1800 seconds (`__init__.py:385-408`, `__init__.py:436-441`).

**Result fidelity — top-level `status` vs. inner command success.** The top-level `status` (`ok`/`done`) reflects only that the shell *spawned* the command; it stays `ok`/`done` even when the inner command exits nonzero. To make inner failures impossible to skim past *without* changing the `status` contract that downstream recovery/telemetry branch on (`tool_executor.py` enriches/logs/collects on `status == "error"`), `_augment_command_result` (`__init__.py`) adds three additive, model-visible fields keyed off `exit_code`:

- `ok` (bool) — `True` only when `exit_code == 0`.
- `command_status` (str) — `"success"` or `"failed"`.
- `warning` (str, present on failure or a suspicious zero-exit) — one-line summary: the nonzero exit code, any detected `python_traceback`/`missing_module` signature (`_detect_failure_signature`), and a bounded stderr tail. The hoisted tail is routed through `lingtai.kernel.trace_redaction.redact_text` (`_redact_warning_tail`, fail-open) so a secret-shaped error line is not made more prominent in the top-level `warning` than it already is in the raw `stderr` field; the raw `stderr`/`stdout` fields are never altered.

On a still-running poll there is no `exit_code`, so no fidelity fields are added.

**Timeout hint.** On a sync timeout whose command resembles a broad recursive scan (`find … -name/-path/-type`, `rglob(`, `os.walk(`, `glob('**…')` — `_broad_scan_hint`), the timeout `message` appends an `rg --files`-based recipe. The hint is advisory text only; it never blocks or rewrites the command.

Job files are stored under `system/jobs/{job_id}/` (stdout.log, stderr.log, pid, status). Cleaned up automatically on poll-completion or cancel. The process-exit watcher still writes single-slot `.notification/bash.json` with a bounded command preview (`__init__.py:651-700`); the last-resort reminder uses `.notification/system.json` multi-event append with stable `ref_id="bash.reminder:<job_id>"` so close-due jobs do not overwrite one another (`__init__.py:531-649`, `src/lingtai/kernel/base_agent/messaging.py:66-180`).

## Internal Module Layout

```
bash/__init__.py
  ├── _detect_failure_signature()    — labels python_traceback / missing_module in output
  ├── _broad_scan_hint()             — rg recipe hint for broad-recursive-walk timeouts
  ├── _augment_command_result()      — adds ok / command_status / warning to completed results
  │
  ├── BashPolicy                     — command execution policy
  │   ├── __init__(allow, deny)      — two modes: allowlist (if allow present) or denylist
  │   ├── from_file(path)            — loads policy from JSON file
  │   ├── yolo()                     — creates a policy that allows everything
  │   ├── describe()                 — human-readable summary of policy rules
  │   ├── is_allowed(command)        — checks command against policy
  │   ├── _check_single(cmd)         — checks a single command name
  │   └── _extract_commands(command) — parses pipes, chains, subshells to extract all command names
  │
  ├── BashManager                    — execution manager
  │   ├── __init__(policy, working_dir, max_output, agent) — stores policy, config, notification locks
  │   ├── handle(args)               — dispatches to _handle_run / _handle_poll / _handle_cancel
  │   ├── _handle_run(args)          — validates + runs sync or async
  │   ├── _run_sync(command, cwd, timeout) — subprocess.run path; augments result + timeout hint
  │   ├── _run_async(command, cwd, reminder) — subprocess.Popen with start_new_session, returns job_id
  │   ├── _run_reminder_timer(...)   — daemon-threaded last-resort poll reminder
  │   ├── _claim_reminder_timer(...) — lock-owned terminal-vs-deadline claim
  │   ├── _publish_async_reminder(job_id) — appends bash.reminder system event
  │   ├── _handle_poll(args)         — checks job status; augments completed result
  │   ├── _handle_cancel(args)       — SIGTERM to process group, cleanup
  │   └── _close_handles(job_id)     — closes open file handles for a job
  │
  └── setup(agent, policy_file, yolo) — resolves policy, registers bash tool
```

## Key Invariants

- **Two policy modes:** Allowlist mode (when `allow` key is present in policy) — only listed commands permitted, everything else blocked. Denylist mode (only `deny` key) — everything allowed except denied commands. The mode is implicit.
- **Pipe-aware command extraction:** `_extract_commands()` parses `|`, `&&`, `||`, `;`, newlines, `$()`, backticks, and env-var prefixes to find every command name in a compound expression.
- **Working directory sandbox:** `working_dir` is validated to be under the agent's working directory. Paths are resolved and checked with `startswith(sandbox + "/")`.
- **Result fidelity is additive, never status-changing:** A completed command always returns top-level `status: "ok"`/`"done"` regardless of `exit_code`. The pass/fail signal lives in additive `ok`/`command_status`/`warning` fields. `status: "error"` is reserved for the shell failing to run the command at all (empty/denied command, timeout, spawn failure) — this preserves the `tool_executor` contract that branches on `status == "error"` for error enrichment, lifecycle logging, and `collected_errors`.
- **Output truncation:** `max_output = 50_000` chars. Both stdout and stderr are truncated with a note showing total length.
- **Subprocess isolation:** Commands run via `subprocess.run(shell=True, capture_output=True, text=True, timeout=...)` in the agent's working directory by default.
- **Async subprocess:** Async commands use `subprocess.Popen(shell=True, start_new_session=True)` with stdout/stderr redirected to files under `system/jobs/{job_id}/`. `start_new_session=True` ensures the process gets its own session, enabling `os.killpg()` for clean cancellation.
- **Async reminder lifecycle:** `_run_async()` arms one daemon timer per job (`__init__.py:512-541`). The timer and terminal handling share `_reminder_lock`: `_claim_reminder_timer()` atomically pops the pending reminder before publish, while terminal `poll -> done` / successful `cancel` suppress by popping first (`__init__.py:543-577`, `__init__.py:742-744`, `__init__.py:800-801`). Process exit and `.notification/bash.json` completion do not cancel the timer.
- **Job lifecycle:** Jobs are created on async run, tracked via PID files, and cleaned up (directory deleted) after poll-completion or cancel. File handles are closed via `_close_handles()` to avoid resource leaks.
- **Policy file location:** Default policy is `bash/bash_policy.json` (shipped with the kernel). Can be overridden via `policy_file` arg or bypassed with `yolo=True`.

## Dependencies

- `lingtai.i18n` — `t()` for localized strings
- `lingtai.kernel.base_agent.BaseAgent` — agent type (TYPE_CHECKING only)
- `lingtai.kernel.base_agent.messaging._enqueue_system_notification` — canonical `.notification/system.json` multi-event append path when Bash is installed on an agent (`src/lingtai/kernel/base_agent/messaging.py:66-180`).
- `lingtai.kernel.notifications.collect_notifications` / `submit` — direct-manager fallback for tests and programmatic BashManager use without a BaseAgent; protected by the agent's shared system lock when present, otherwise a module-global fallback lock (`__init__.py:605-649`).
- `lingtai.kernel.trace_redaction.redact_text` — mechanical secret redaction for the stderr tail hoisted into `warning` (imported lazily inside `_redact_warning_tail`, fail-open).

## Composition

- **Parent:** `src/tools/` (tool package).
- **Siblings:** `daemon/`, `avatar/`, `mcp/`, `knowledge/` (private durable memory), `skills/` (skill catalog).
- **Manual:** `bash/manual/SKILL.md` — operational guide for agents (currently focused on scheduled / cron-driven work — when to schedule, the wake-by-mailbox-drop contract, hygiene rules, OS-specific recipes for launchd / systemd / crontab, and debugging walkthroughs).
- **Kernel hooks:** `setup()` is called during capability initialization; `BashManager.handle()` is registered as the `bash` tool handler.
