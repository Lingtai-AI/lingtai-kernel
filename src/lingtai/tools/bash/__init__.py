"""Bash capability — shell command execution with file-based policy.

Adds the ability to run shell commands. This is a capability (not intrinsic)
because not every agent should have shell access — it's a powerful
capability that should be explicitly opted into.

Usage:
    agent.add_capability("bash", policy_file="path/to/policy.json")
    agent.add_capability("bash", yolo=True)  # no restrictions
"""
from __future__ import annotations

import json
import math
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ._shell_dialect import ShellDialect, ShellInvocation, extract_posix_commands

from ._async_supervisor import (
    load_state,
    publish_reminder_if_claimed,
    update_state,
    write_initial_state,
)
from ._async_process import (
    BashAsyncProcessPort,
    ProcessObservation,
    ProcessRef,
    process_ref_from_state,
)


if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent

PROVIDERS = {"providers": [], "default": "builtin"}

_DEFAULT_POLICY_FILE = Path(__file__).parent / "bash_policy.json"
_DEFAULT_ASYNC_REMINDER_SECONDS = 1800.0
_SUPERVISOR_START_LEASE_SECONDS = 3.0
# The parent may spend one start lease launching the supervisor and another
# waiting for its durable PID before it can atomically arm the user-visible
# reminder from the successful-return boundary.  During that bounded handoff,
# another manager must not publish the earlier crash-fallback deadline.
_RETURN_HANDOFF_LEASE_SECONDS = _SUPERVISOR_START_LEASE_SECONDS * 2
_RETURN_HANDOFF_RECHECK_SECONDS = 0.05
_SUPERVISOR_COMMIT_GRACE_SECONDS = 0.25
_CANCEL_COMMIT_TIMEOUT_SECONDS = 3.0
_JOB_ID_RE = re.compile(r"job-(?:[0-9a-f]{32}|[0-9a-f]{8})\Z")


def _select_bash_shell_dialect() -> ShellDialect:
    """Load the outer selector lazily to keep adapter → Port imports acyclic."""
    from lingtai.adapters.bash import select_bash_shell_dialect

    return select_bash_shell_dialect()


def _select_bash_async_process() -> BashAsyncProcessPort:
    """Load the process adapter lazily so the capability remains composition-led."""
    from lingtai.adapters.bash_process import select_bash_async_process
    return select_bash_async_process()

# Length of the stderr tail surfaced in the failure warning. Short on purpose:
# the full stderr is already present in the result; the tail just makes the
# failure impossible to miss when an agent skims the top-level fields.
_WARNING_STDERR_TAIL = 600


def _redact_warning_tail(text: str) -> str:
    """Best-effort secret redaction for the stderr tail copied into ``warning``.

    The raw ``stderr``/``stdout`` fields already mirror the command output
    verbatim; this only touches the bounded tail that gets hoisted into the
    top-level ``warning`` string, where a secret-shaped error line would be made
    *more* prominent. Routes through the kernel's mechanical
    ``trace_redaction.redact_text`` so the warning surface gets the same
    high-confidence token/key redaction as durable trajectory writes.

    Fail-open: if the redactor cannot be imported or raises (it must never break
    a bash result), the original tail is returned unchanged — the raw stderr is
    already present in the result, so this introduces no new exposure beyond it.
    """
    try:
        from lingtai.kernel.trace_redaction import redact_text

        return redact_text(text)
    except Exception:
        return text

# Substrings that signal a "successful shell, failed program" — the failure the
# fidelity warning exists to surface. A Python traceback or a missing-module
# error commonly exits nonzero, but agents have been observed proceeding on the
# false success because the top-level status said "ok".
_FAILURE_SIGNATURES = (
    "Traceback (most recent call last)",
    "ModuleNotFoundError",
    "No module named",
)


def _detect_failure_signature(stdout: str, stderr: str) -> str | None:
    """Return a short label if stdout/stderr carries a known failure signature.

    Detection is best-effort and advisory only — it never changes ``exit_code``
    or whether the command is considered failed; that is driven solely by the
    exit code. It only enriches the human-/model-visible ``warning`` text so a
    Python traceback or missing-import under a zero/nonzero exit is named
    explicitly instead of being buried in the output.
    """
    haystack = f"{stderr}\n{stdout}"
    # Prefer the most specific, most actionable label. A missing-module error
    # also emits a full traceback, so check for it before the generic one.
    if _FAILURE_SIGNATURES[1] in haystack or _FAILURE_SIGNATURES[2] in haystack:
        return "missing_module"
    if _FAILURE_SIGNATURES[0] in haystack:
        return "python_traceback"
    return None


# Command shapes that frequently time out via unbounded recursive directory
# walks over large roots (work/projects/.lingtai). Matched only to *append a
# hint* on timeout — never to block or alter the command.
_BROAD_SCAN_RE = re.compile(
    r"""
    \bfind\s+[^|]*\s-(?:name|path|type|iname)\b   # find ... -name/-path/-type
    | \brglob\s*\(                                  # Path.rglob(
    | \bos\.walk\s*\(                               # os.walk(
    | \bglob\s*\(\s*['"][^'"]*\*\*                  # glob('**/...')
    """,
    re.VERBOSE,
)

_BROAD_SCAN_HINT = (
    "This looks like a broad recursive scan, the most common cause of bash "
    "timeouts. Prefer `rg --files --hidden -g '!**/{.git,node_modules,daemons,"
    ".worktrees}/**' <root>` (then filter), narrow the root, or raise `timeout` "
    "for a genuinely large tree."
)


def _broad_scan_hint(command: str) -> str | None:
    """Return a broad-scan recipe hint if the command resembles a recursive walk.

    Best-effort heuristic used only to enrich a timeout message. False positives
    are harmless (an extra sentence); it never blocks or rewrites the command.
    """
    return _BROAD_SCAN_HINT if _BROAD_SCAN_RE.search(command) else None


def _augment_command_result(result: dict) -> dict:
    """Add explicit pass/fail fidelity fields to a completed-command result.

    The top-level ``status`` of a bash result reflects only that the shell
    *spawned* — it stays ``ok``/``done`` even when the inner command failed.
    Agents have repeatedly missed inner failures because of this. To make a
    failure impossible to skim past *without* changing the ``status`` contract
    (which downstream recovery/telemetry branches on), this adds three additive,
    model-visible fields keyed off ``exit_code``:

    - ``ok`` (bool): ``True`` only when ``exit_code == 0``.
    - ``command_status`` (str): ``"success"`` or ``"failed"``.
    - ``warning`` (str, on failure *or* a suspicious zero-exit): one-line summary
      naming the nonzero exit, any detected traceback/missing-module signature,
      and a stderr tail. The tail is routed through the kernel redactor so a
      secret-shaped error line is not made more prominent than it already is in
      the raw ``stderr`` field.

    ``status`` itself is intentionally left untouched so existing callers and
    tests that branch on it keep working. The raw ``stderr``/``stdout`` fields
    are mirrored verbatim and never altered here.
    """
    exit_code = result.get("exit_code")
    if not isinstance(exit_code, int):
        return result
    failed = exit_code != 0
    result["ok"] = not failed
    result["command_status"] = "failed" if failed else "success"

    signature = _detect_failure_signature(
        result.get("stdout", "") or "", result.get("stderr", "") or ""
    )
    if not failed and signature is None:
        return result

    parts: list[str] = []
    if failed:
        parts.append(f"command exited with code {exit_code}")
    else:
        # Zero exit but a traceback/missing-module signature is present — the
        # command may have swallowed the error. Flag it without claiming failure.
        parts.append(f"command exited 0 but output contains a {signature}")
    if failed and signature is not None:
        parts.append(f"detected {signature}")
    stderr = (result.get("stderr") or "").strip()
    if stderr:
        tail = stderr[-_WARNING_STDERR_TAIL:]
        if len(stderr) > _WARNING_STDERR_TAIL:
            tail = "…" + tail
        # Redact the hoisted tail only — the raw stderr field is left verbatim.
        parts.append(f"stderr tail: {_redact_warning_tail(tail)}")
    result["warning"] = "; ".join(parts)
    return result

def get_description(lang: str = "en") -> str:
    return "Execute a shell command and return stdout/stderr. Any system program — scripts, git, curl, pip, data pipelines. Returns exit_code, stdout, stderr, plus ok (bool) and command_status ('success'/'failed'). IMPORTANT: top-level status stays 'ok' even when the command FAILS — it only means the shell ran. Always check exit_code/ok and read the warning field (it names nonzero exits, Python tracebacks, and missing modules); never assume success from status alone. Avoid broad recursive scans (find … -name, rglob, os.walk, glob('**')) — they time out; prefer `rg --files`. Parse JSONL line-by-line, not as one JSON blob. Supports async mode (async=true → job_id, then poll/cancel). Before using this tool, read the `bash-manual` skill — it covers cron setup, async hygiene, and advanced usage; no exceptions."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "poll", "cancel"],
                "description": "Action to perform: 'run' (default) executes a command, 'poll' checks async job status, 'cancel' kills an async job",
                "default": "run",
            },
            "command": {
                "type": "string",
                "description": 'The shell command to execute',
            },
            "timeout": {
                "type": "number",
                "description": 'Timeout in seconds (default: 30, only for sync execution)',
                "default": 30,
            },
            "working_dir": {
                "type": "string",
                "description": 'Working directory for the command (optional). Leave it out (or pass an empty string) to use the agent working directory. Must be inside the agent working directory sandbox; paths outside it are rejected. For external repos/paths, keep working_dir at the agent dir and put an explicit cd in command, e.g. cd /absolute/path && ...',
            },
            "async": {
                "type": "boolean",
                "description": "Run command in background and return immediately with a job_id (default: false, only for action='run')",
                "default": False,
            },
            "reminder": {
                "type": "number",
                "description": "Last-resort async wake delay in seconds (default: 1800). For async run only: if the job is still non-terminal when the durable deadline expires, publish a system notification reminding you to poll it; exact completion suppresses this stale watchdog and publishes the Bash completion wake instead.",
                "default": _DEFAULT_ASYNC_REMINDER_SECONDS,
            },
            "job_id": {
                "type": "string",
                "description": 'Job ID for poll/cancel actions (returned by async run)',
            },
            "summary": {
                "type": "boolean",
                "description": 'Optional. Default false. When true, this tool runs normally and the raw result is preserved in the durable log (retrievable by tool_call_id), but before the result enters your context it is replaced by an LLM-generated summary driven by your `reasoning` field — so make `reasoning` specific about what to retain. Set true only when the output is expected to be large (>10k chars) and you do NOT need the exact raw text. Leave false when you need exact line/file/diff/stderr text. The summary is non-canonical; if the raw exceeds 500,000 chars no summary is generated and you get a refusal pointing at the preserved raw.',
                "default": False,
            },
        },
        "required": ["reminder"],  # command/job_id are enforced per action; handler defaults omitted reminder for runtime compatibility
    }



class BashPolicy:
    """Command execution policy — allow/deny lists with pipe awareness.

    Two modes, determined by the policy file content:
    - **Denylist mode** (only ``deny`` key): everything allowed except denied commands.
    - **Allowlist mode** (``allow`` key present): only listed commands allowed,
      everything else blocked. ``deny`` key is ignored in this mode.

    The mode is implicit — if ``allow`` is present, it's allowlist mode.
    """

    def __init__(self, allow: list[str] | None = None, deny: list[str] | None = None):
        self._allow = set(allow) if allow else None
        # deny is only used in denylist mode (when allow is absent)
        self._deny = set(deny) if deny and not allow else None

    @classmethod
    def from_file(cls, path: str) -> "BashPolicy":
        """Load policy from a JSON file with allow/deny lists."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Policy file not found: {path}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(allow=data.get("allow"), deny=data.get("deny"))

    @classmethod
    def yolo(cls) -> "BashPolicy":
        """Create a policy that allows everything."""
        return cls()

    def describe(self) -> str:
        """Return a human-readable summary of the policy rules."""
        if self._allow is None and self._deny is None:
            return ""
        if self._allow is not None:
            return (
                f"ALLOWLIST MODE: Only these commands are permitted (all others blocked): "
                f"{', '.join(sorted(self._allow))}"
            )
        return (
            f"DENYLIST MODE: All commands are allowed except: "
            f"{', '.join(sorted(self._deny))}"
        )

    def is_allowed(self, command: str) -> bool:
        """Check if a command string is allowed by this policy.

        Parses pipes, chains, and subshells to check every command.
        """
        if self._allow is None and self._deny is None:
            return True
        commands = self._extract_commands(command)
        return all(self._check_single(cmd) for cmd in commands)

    def _check_single(self, cmd: str) -> bool:
        """Check a single command name against policy.

        Allowlist mode: command must be in allow set.
        Denylist mode: command must not be in deny set.
        """
        if self._allow is not None:
            return cmd in self._allow
        if self._deny is not None:
            return cmd not in self._deny
        return True

    @staticmethod
    def _extract_commands(command: str) -> list[str]:
        """Extract all command names from a potentially chained command string.

        Handles: |, &&, ||, ;, newlines, $(), backticks, env-var prefixes.
        Returns the first actual command word of each sub-command.
        """
        return list(extract_posix_commands(command))


class BashManager:
    """Manages shell commands; async terminal truth belongs to a durable child."""

    def __init__(
        self,
        policy: BashPolicy,
        working_dir: str,
        agent: "BaseAgent",
        max_output: int = 50_000,
        dialect: ShellDialect | None = None,
        async_process: BashAsyncProcessPort | None = None,
    ):
        self._policy = policy
        self._working_dir = working_dir
        self._max_output = max_output
        self._agent = agent
        self._dialect = dialect or _select_bash_shell_dialect()
        self._async_process = async_process or _select_bash_async_process()
        self._jobs_dir: Path | None = None
        self._reminder_lock = threading.Lock()
        self._reminder_cancel_events: dict[str, threading.Event] = {}
        self._completion_lock = threading.Lock()
        self._completion_watchers: set[str] = set()
        self._rehydrate_async_jobs()

    def _jobs_path(self) -> Path:
        return self._jobs_dir or Path(self._working_dir) / "system" / "jobs"

    def _ensure_jobs_dir(self) -> Path:
        """Create and return the jobs directory (only for an async run)."""
        if self._jobs_dir is None:
            self._jobs_dir = Path(self._working_dir) / "system" / "jobs"
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        return self._jobs_dir

    def _validate_working_dir(self, cwd: str) -> dict | None:
        """Validate cwd is under the agent sandbox. Returns error dict or None."""
        try:
            resolved = str(Path(cwd).resolve())
            sandbox = str(Path(self._working_dir).resolve())
            if not (resolved == sandbox or resolved.startswith(sandbox + "/")):
                return {
                    "status": "error",
                    "message": (
                        f"working_dir must be under agent working directory: "
                        f"{self._working_dir}. To operate on an external path, "
                        f"use an allowed working_dir and put `cd {resolved} && ...` "
                        f"inside the command."
                    ),
                }
        except (ValueError, OSError):
            return {"status": "error", "message": "Invalid working_dir path"}
        return None

    def _validate_command(self, command: str) -> dict | None:
        """Validate command is non-empty and allowed by policy. Returns error dict or None."""
        if not command.strip():
            return {"status": "error", "message": "command is required"}
        commands = self._dialect.extract_commands(command)
        if not all(self._policy._check_single(cmd) for cmd in commands):
            denied = commands
            return {
                "status": "error",
                "message": f"Command not allowed by policy. "
                f"Denied command(s): {', '.join(denied)}",
            }
        return None

    @staticmethod
    def _validate_job_id(job_id: str) -> dict | None:
        """Accept only retained full UUID IDs and the old eight-hex legacy form."""
        if not isinstance(job_id, str) or not job_id:
            return {"status": "error", "message": "job_id is required"}
        if _JOB_ID_RE.fullmatch(job_id) is None:
            return {"status": "error", "message": f"Invalid job_id: {job_id}"}
        return None

    @staticmethod
    def _validate_reminder(value) -> tuple[float | None, dict | None]:
        """Validate async reminder delay, defaulting omitted values for runtime compatibility."""
        if value is None:
            return _DEFAULT_ASYNC_REMINDER_SECONDS, None
        if isinstance(value, bool):
            return None, {"status": "error", "message": "reminder must be a finite non-negative number of seconds"}
        try:
            delay = float(value)
        except (TypeError, ValueError):
            return None, {"status": "error", "message": "reminder must be a finite non-negative number of seconds"}
        if delay < 0 or not math.isfinite(delay) or delay > threading.TIMEOUT_MAX:
            return None, {
                "status": "error",
                "message": (
                    "reminder must be a finite non-negative number of seconds "
                    f"not greater than {threading.TIMEOUT_MAX}"
                ),
            }
        return delay, None

    def handle(self, args: dict) -> dict:
        action = args.get("action", "run")
        if action == "poll":
            return self._handle_poll(args)
        if action == "cancel":
            return self._handle_cancel(args)
        return self._handle_run(args)

    def _handle_run(self, args: dict) -> dict:
        command = args.get("command", "")
        err = self._validate_command(command)
        if err:
            return err
        cwd = args.get("working_dir") or self._working_dir
        if isinstance(cwd, str) and not cwd.strip():
            cwd = self._working_dir
        err = self._validate_working_dir(cwd)
        if err:
            return err
        invocation = self._dialect.make_invocation(command)
        if args.get("async", False):
            reminder, err = self._validate_reminder(args.get("reminder"))
            if err:
                return err
            return self._run_async(command, cwd, reminder, invocation)
        return self._run_sync(command, cwd, args.get("timeout", 30), invocation)

    def _run_sync(self, command: str, cwd: str, timeout: float, invocation: ShellInvocation) -> dict:
        """Run the selected invocation; timeout/capture/result policy stays here."""
        try:
            process_args, process_kwargs = invocation.process_args()
            if invocation.encoding is not None:
                process_kwargs["encoding"] = invocation.encoding
            if invocation.errors is not None:
                process_kwargs["errors"] = invocation.errors
            result = subprocess.run(
                process_args, capture_output=True, text=True,
                timeout=timeout, cwd=cwd, **process_kwargs,
            )
            stdout, stderr = result.stdout, result.stderr
            if len(stdout) > self._max_output:
                stdout = stdout[: self._max_output] + f"\n... (truncated, {len(result.stdout)} chars total)"
            if len(stderr) > self._max_output:
                stderr = stderr[: self._max_output] + f"\n... (truncated, {len(result.stderr)} chars total)"
            return _augment_command_result({
                "status": "ok", "exit_code": result.returncode,
                "stdout": stdout, "stderr": stderr,
            })
        except subprocess.TimeoutExpired:
            msg = f"Command timed out after {timeout}s"
            hint = _broad_scan_hint(command)
            return {"status": "error", "message": f"{msg}. {hint}" if hint else msg}
        except Exception as e:
            return {"status": "error", "message": f"Command failed: {e}"}

    @staticmethod
    def _terminal(status: object) -> bool:
        return status in {"completed", "unrecoverable"}

    def _rehydrate_async_jobs(self) -> None:
        """Restore deadline/completion publication work from durable job state."""
        jobs_dir = self._jobs_path()
        if not jobs_dir.is_dir():
            return
        for job_dir in jobs_dir.iterdir():
            if not job_dir.is_dir() or _JOB_ID_RE.fullmatch(job_dir.name) is None:
                continue
            state = load_state(job_dir)
            if state is None:
                continue  # Legacy jobs remain readable by _handle_poll.
            if not self._terminal(state.get("status")):
                state = self._mark_unrecoverable_if_supervisor_gone(job_dir) or load_state(job_dir) or state
            job_id = job_dir.name
            reminder = state.get("reminder")
            if self._terminal(state.get("status")):
                # Completion owns the wake-up once terminal truth exists.  A
                # watchdog saying the job "may still be running" is stale and
                # must not be re-armed by a fresh manager.
                def suppress_terminal_reminder(current: dict) -> dict:
                    durable_reminder = current.get("reminder")
                    if isinstance(durable_reminder, dict) and durable_reminder.get("state") in {
                        "pending", "publishing", "suppressing"
                    }:
                        durable_reminder.update({
                            "state": "suppressed",
                            "suppressed_at": time.time(),
                        })
                        durable_reminder.pop("claim_token", None)
                        durable_reminder.pop("suppressing_at", None)
                        durable_reminder.pop("suppressing_until", None)
                    return current

                update_state(job_dir, suppress_terminal_reminder)
                self._publish_completion_if_due(job_id, job_dir)
            else:
                if isinstance(reminder, dict) and reminder.get("state") in {"pending", "publishing", "suppressing"}:
                    self._start_reminder_timer(job_id, job_dir)
                self._start_completion_watcher(job_id, job_dir)

    def _initial_async_state(
        self, job_id: str, command: str, cwd: str, reminder: float,
        invocation: ShellInvocation | None = None,
    ) -> dict:
        now = time.time()
        invocation = invocation or self._dialect.make_invocation(command)
        return {
            "version": 3,
            "job_id": job_id,
            "command": command,
            "shell_dialect": self._dialect.state_key(),
            "invocation": invocation.to_dict(),
            "cwd": cwd,
            "status": "launching",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "pid": None,
            "pid_identity": None,
            "pid_start_time": None,
            "process_group": None,
            "supervisor_process": None,
            "command_process": None,
            "supervisor_start_lease": {
                "token": secrets.token_hex(16),
                "deadline_at": now + _SUPERVISOR_START_LEASE_SECONDS,
                "state": "pending",
            },
            "return_handoff": {
                "state": "pending",
                "deadline_at": now + _RETURN_HANDOFF_LEASE_SECONDS,
            },
            "exit_status_known": False,
            "exit_code": None,
            "terminal_polled": False,
            "reminder": {
                "deadline_at": now + reminder,
                "state": "pending",
                "ref_id": f"bash.reminder:{job_id}",
            },
            "completion": {
                "state": "pending",
                "ref_id": f"bash.completion:{job_id}",
            },
        }

    def _run_async(
        self, command: str, cwd: str, reminder: float,
        invocation: ShellInvocation | None = None,
    ) -> dict:
        """Start a detached durable supervisor and return its command PID."""
        jobs_dir = self._ensure_jobs_dir()
        job_dir: Path | None = None
        job_id = ""
        # Retained records make collision handling a correctness requirement, not
        # cleanup hygiene.  A full UUID has ample entropy; mkdir remains the
        # collision-safe authority if a hostile or extraordinarily unlikely name
        # is already present.
        for _ in range(8):
            candidate = f"job-{uuid.uuid4().hex}"
            try:
                candidate_dir = jobs_dir / candidate
                candidate_dir.mkdir()
            except FileExistsError:
                continue
            except OSError as exc:
                return {"status": "error", "message": f"Failed to create async job: {exc}"}
            job_id, job_dir = candidate, candidate_dir
            break
        if job_dir is None:
            return {"status": "error", "message": "Failed to allocate a unique async job ID"}
        initial_state = self._initial_async_state(job_id, command, cwd, reminder, invocation)
        start_lease = initial_state["supervisor_start_lease"]
        start_token = start_lease["token"]
        try:
            write_initial_state(job_dir, initial_state)
        except Exception as exc:
            return {"status": "error", "message": f"Failed to initialize async job: {exc}"}
        try:
            supervisor_ref, supervisor = self._async_process.launch_supervisor(job_dir, start_token)
        except Exception as exc:
            def mark_failed(state: dict) -> dict:
                state.update({
                    "status": "unrecoverable", "finished_at": time.time(),
                    "supervisor_error": f"cannot start supervisor: {exc}",
                })
                return state
            update_state(job_dir, mark_failed)
            return {"status": "error", "message": f"Failed to start async job: {exc}"}

        # Record the launched supervisor PID from the owning parent even when an
        # OS incarnation identity cannot be observed.  The child must still claim
        # the matching durable lease before it can spawn the command.
        supervisor_identity = supervisor_ref.incarnation

        def record_supervisor(state: dict) -> dict:
            lease = state.get("supervisor_start_lease")
            if (
                self._terminal(state.get("status"))
                or not isinstance(lease, dict)
                or lease.get("token") != start_token
            ):
                return state
            state["supervisor_pid"] = supervisor_ref.public_id
            if supervisor_identity is not None:
                state["supervisor_identity"] = supervisor_identity
            state["supervisor_process"] = supervisor_ref.to_dict()
            return state

        update_state(job_dir, record_supervisor)

        # If this owned supervisor exits before a terminal commit, its parent has
        # stronger evidence than any PID heuristic and closes the state itself.
        threading.Thread(
            target=self._reap_supervisor,
            args=(supervisor, job_dir),
            daemon=True,
        ).start()

        deadline = time.monotonic() + _SUPERVISOR_START_LEASE_SECONDS
        state = load_state(job_dir)
        while time.monotonic() < deadline:
            state = load_state(job_dir)
            pid = state.get("pid") if state else None
            if isinstance(pid, int):
                # Preserve the historical/user-facing meaning of `reminder=N`:
                # the caller gets N seconds after a successful async-start return,
                # rather than losing supervisor-startup time from that interval.
                # The initial deadline remains a crash-safe fallback if this
                # manager disappears before reaching the return path.
                return_armed = False

                def arm_from_return(current: dict) -> dict:
                    # This lock-owned mutation is the successful-return boundary.
                    # Success is conditional on winning the still-valid handoff:
                    # after expiry another manager is entitled to publish the
                    # crash fallback, and that already-published event cannot be
                    # recalled by a late owner.
                    nonlocal return_armed
                    returned_at = time.time()
                    return_handoff = current.get("return_handoff")
                    handoff_pending = (
                        isinstance(return_handoff, dict)
                        and return_handoff.get("state") == "pending"
                    )
                    handoff_deadline = (
                        return_handoff.get("deadline_at")
                        if isinstance(return_handoff, dict)
                        else None
                    )
                    handoff_valid = (
                        handoff_pending
                        and isinstance(handoff_deadline, (int, float))
                        and not isinstance(handoff_deadline, bool)
                        and returned_at < float(handoff_deadline)
                    )
                    if self._terminal(current.get("status")):
                        # A very short command may finish exactly before the start
                        # call returns.  That is still a successful start only when
                        # exact terminal truth won while the handoff was valid; its
                        # terminal commit already suppressed the fallback reminder.
                        if (
                            handoff_valid
                            and current.get("status") in {"completed", "failed"}
                            and current.get("exit_status_known") is True
                        ):
                            return_handoff.update({
                                "state": "completed_before_return",
                                "returned_at": returned_at,
                            })
                            return_armed = True
                        elif handoff_pending:
                            return_handoff.update({
                                "state": "aborted",
                                "resolved_at": returned_at,
                            })
                        return current
                    if not handoff_pending:
                        return current
                    if not handoff_valid:
                        return_handoff.update({
                            "state": "expired",
                            "expired_at": returned_at,
                        })
                        return current
                    durable_reminder = current.get("reminder")
                    if not (
                        isinstance(durable_reminder, dict)
                        and durable_reminder.get("state") in {
                            "pending", "publishing", "suppressing"
                        }
                    ):
                        return current
                    durable_reminder["deadline_at"] = returned_at + reminder
                    # A pre-return publisher should have been deferred by the
                    # handoff guard.  Recover conservatively if a stale claim from
                    # an older implementation nevertheless exists.
                    if durable_reminder.get("state") == "publishing":
                        durable_reminder["state"] = "pending"
                        durable_reminder.pop("claim_token", None)
                        durable_reminder.pop("claimed_at", None)
                    return_handoff.update({
                        "state": "armed",
                        "returned_at": returned_at,
                    })
                    return_armed = True
                    return current

                update_state(job_dir, arm_from_return)
                self._start_reminder_timer(job_id, job_dir)
                self._start_completion_watcher(job_id, job_dir)
                if not return_armed:
                    return {
                        "status": "error",
                        "job_id": job_id,
                        "pid": pid,
                        "message": (
                            "Async job started, but its successful-return handoff "
                            "expired or was superseded. The job remains pollable "
                            "by job_id and its crash-fallback reminder remains authoritative."
                        ),
                    }
                return {
                    "status": "ok", "job_id": job_id, "pid": pid,
                    "message": f'Job started. Use bash(action="poll", job_id="{job_id}") to check.',
                    "handoff": "While waiting, go idle or call system(action='sleep'); the terminal result will arrive and wake you as a notification; read bash-manual and notification-manual for details.",
                }
            if state and self._terminal(state.get("status")):
                break
            time.sleep(0.01)
        self._mark_unrecoverable_if_supervisor_gone(job_dir)
        return {"status": "error", "message": "Failed to start async job supervisor"}

    def _reap_supervisor(self, supervisor, job_dir: Path) -> None:
        try:
            returncode = self._async_process.wait_supervisor(supervisor)
        except Exception:
            return

        def close_abandoned_start(state: dict) -> dict:
            if self._terminal(state.get("status")):
                return state
            state.update({
                "status": "unrecoverable",
                "exit_status_known": False,
                "exit_code": None,
                "finished_at": time.time(),
                "supervisor_error": (
                    f"owned supervisor exited with code {returncode} before terminal commit"
                ),
            })
            return state

        update_state(job_dir, close_abandoned_start)

    def _start_reminder_timer(self, job_id: str, job_dir: Path, delay: float | None = None) -> None:
        """Arm/re-arm the persisted deadline; a new manager can resume it."""
        if delay is None:
            state = load_state(job_dir)
            reminder = state.get("reminder") if state else None
            if not isinstance(reminder, dict) or not isinstance(reminder.get("deadline_at"), (int, float)):
                return
            delay = max(0.0, float(reminder["deadline_at"]) - time.time())
        with self._reminder_lock:
            if job_id in self._reminder_cancel_events:
                return
            cancel_event = threading.Event()
            self._reminder_cancel_events[job_id] = cancel_event
        threading.Thread(
            target=self._run_reminder_timer,
            args=(job_id, job_dir, delay, cancel_event), daemon=True,
        ).start()

    def _run_reminder_timer(
        self, job_id: str, job_dir: Path, delay: float, cancel_event: threading.Event,
    ) -> None:
        if cancel_event.wait(delay):
            return
        claim_token = self._claim_reminder_timer(job_id, job_dir, cancel_event)
        if claim_token is None:
            return
        # The helper retains the cross-manager state lock through the final
        # pre-publish suppression check, sink write, and acknowledgement.  A
        # terminal claim which wins that lock makes this stale token a no-op.
        self._publish_claimed_reminder(job_id, job_dir, claim_token)

    def _claim_reminder_timer(
        self, job_id: str, job_dir: Path, cancel_event: threading.Event,
    ) -> str | None:
        """Claim only a currently due reminder; stale timers defer to durable truth."""
        with self._reminder_lock:
            current = self._reminder_cancel_events.get(job_id)
            if current is not cancel_event or cancel_event.is_set() or not job_dir.is_dir():
                return None
            self._reminder_cancel_events.pop(job_id, None)
            cancel_event.set()
        state = load_state(job_dir)
        if state is None:  # Compatibility for the original private race tests.
            return "legacy-private-race"
        claim_token = uuid.uuid4().hex
        claimed = False
        defer_seconds: float | None = None

        def claim(current_state: dict) -> dict:
            nonlocal claimed, defer_seconds
            reminder = current_state.get("reminder")
            if not isinstance(reminder, dict) or reminder.get("state") in {
                "suppressed", "published"
            }:
                return current_state
            now = time.time()
            if reminder.get("state") == "suppressing":
                suppressing_until = reminder.get("suppressing_until")
                if (
                    isinstance(suppressing_until, (int, float))
                    and not isinstance(suppressing_until, bool)
                    and float(suppressing_until) > now
                ):
                    defer_seconds = float(suppressing_until) - now
                    return current_state
                reminder["state"] = "pending"
                reminder.pop("suppressing_at", None)
                reminder.pop("suppressing_until", None)

            return_handoff = current_state.get("return_handoff")
            if (
                isinstance(return_handoff, dict)
                and return_handoff.get("state") == "pending"
            ):
                handoff_deadline = return_handoff.get("deadline_at")
                if (
                    isinstance(handoff_deadline, (int, float))
                    and not isinstance(handoff_deadline, bool)
                    and float(handoff_deadline) > now
                ):
                    # The manager which owns the synchronous start response has not
                    # yet durably moved the reminder to returned_at + delay.  Check
                    # the cross-process state again soon rather than sleeping until
                    # the whole lease expires, so a crash immediately after arming
                    # still recovers close to the requested deadline.
                    defer_seconds = min(
                        float(handoff_deadline) - now,
                        _RETURN_HANDOFF_RECHECK_SECONDS,
                    )
                    return current_state
                return_handoff.update({"state": "expired", "expired_at": now})

                # Once the bounded handoff fails, do not emit a misleading
                # may-still-be-running reminder for a start which is already known
                # to be unrecoverable.  The completion channel owns that wake-up.
                lease_expired = self._supervisor_start_lease_expired(current_state)
                supervisor_gone = self._supervisor_definitively_gone(current_state)
                if lease_expired or supervisor_gone:
                    reason = (
                        "supervisor start lease expired before command spawn"
                        if lease_expired
                        else "recorded supervisor is definitively gone before terminal commit"
                    )
                    current_state.update({
                        "status": "unrecoverable",
                        "exit_status_known": False,
                        "exit_code": None,
                        "finished_at": now,
                        "supervisor_error": reason,
                    })
                    reminder.update({"state": "suppressed", "suppressed_at": now})
                    reminder.pop("claim_token", None)
                    reminder.pop("claimed_at", None)
                    return current_state

            deadline_at = reminder.get("deadline_at")
            if (
                isinstance(deadline_at, (int, float))
                and not isinstance(deadline_at, bool)
                and float(deadline_at) > now
            ):
                # Another manager may have moved the crash-fallback deadline to
                # the successful-return boundary after this timer was armed.
                # Revert any stale publishing claim and let a fresh timer own the
                # later durable deadline.
                reminder["state"] = "pending"
                reminder.pop("claim_token", None)
                reminder.pop("claimed_at", None)
                defer_seconds = float(deadline_at) - now
                return current_state
            # A stale ``publishing`` claim is recoverable after a crash.  Replacing
            # its token makes concurrent rehydrators mutually exclusive at the
            # final publication gate below.
            reminder.update({
                "state": "publishing",
                "claimed_at": now,
                "claim_token": claim_token,
            })
            claimed = True
            return current_state

        update_state(job_dir, claim)
        if defer_seconds is not None:
            self._start_reminder_timer(job_id, job_dir, defer_seconds)
        return claim_token if claimed else None

    def _publish_claimed_reminder(self, job_id: str, job_dir: Path, claim_token: str) -> bool:
        if claim_token == "legacy-private-race":
            return self._publish_async_reminder(job_id) is not False
        return publish_reminder_if_claimed(
            job_dir, claim_token, lambda: self._publish_async_reminder(job_id),
        )

    def _cancel_reminder_timer(self, job_id: str) -> None:
        """Stop this manager's local deadline worker; durable suppression is a terminal claim."""
        with self._reminder_lock:
            cancel_event = self._reminder_cancel_events.pop(job_id, None)
        if cancel_event is not None:
            cancel_event.set()

    def _publish_async_reminder(self, job_id: str) -> bool:
        body = (
            f"Bash async job {job_id} may still be running. "
            f"Poll it with bash(action=\"poll\", job_id=\"{job_id}\")."
        )
        agent = self._agent
        if hasattr(agent, "_enqueue_system_notification"):
            try:
                agent._enqueue_system_notification(
                    source="bash.reminder", ref_id=f"bash.reminder:{job_id}",
                    body=body, skip_if_ref_id_exists=True,
                )
                return True
            except Exception:
                pass
        try:
            self._append_system_notification_fallback(
                source="bash.reminder", ref_id=f"bash.reminder:{job_id}", body=body,
            )
            return True
        except Exception:
            return False

    def _append_system_notification_fallback(
        self, *, source: str, ref_id: str, body: str,
    ) -> str:
        """Append a system event using the agent's serialized store."""
        import secrets
        from datetime import datetime, timezone
        from lingtai.kernel.notification_store import UNCONDITIONAL

        store = self._agent._notification_store
        event_id = f"evt_{int(time.time()*1000):x}_{secrets.token_hex(8)}"
        received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        def mutate(current_payload: dict) -> tuple[dict | None, bool, str]:
            current = current_payload if isinstance(current_payload, dict) else {}
            events = list(current.get("data", {}).get("events", []))
            if any(isinstance(event, dict) and event.get("ref_id") == ref_id for event in events):
                return current_payload, False, ""
            events.append({
                "event_id": event_id, "source": source, "ref_id": ref_id,
                "body": body, "at": received_at,
            })
            events = events[-20:]
            return ({
                "header": f"{len(events)} system notification{'s' if len(events) != 1 else ''}",
                "icon": "🔔", "priority": "normal", "published_at": received_at,
                "data": {"events": events},
            }, True, event_id)
        result = store.compare_update_channel("system", UNCONDITIONAL, mutate)
        return result.value if isinstance(result.value, str) else ""

    def _start_completion_watcher(self, job_id: str, job_dir: Path) -> None:
        with self._completion_lock:
            if job_id in self._completion_watchers:
                return
            self._completion_watchers.add(job_id)
        threading.Thread(
            target=self._watch_durable_job, args=(job_id, job_dir), daemon=True,
        ).start()

    def _watch_durable_job(self, job_id: str, job_dir: Path) -> None:
        try:
            while True:
                state = load_state(job_dir)
                if state is None:
                    return
                if self._terminal(state.get("status")):
                    self._publish_completion_if_due(job_id, job_dir)
                    return
                time.sleep(0.05)
        finally:
            with self._completion_lock:
                self._completion_watchers.discard(job_id)

    def _publish_completion_if_due(self, job_id: str, job_dir: Path) -> None:
        claimed = False
        def claim(state: dict) -> dict:
            nonlocal claimed
            completion = state.get("completion")
            if not self._terminal(state.get("status")) or not isinstance(completion, dict):
                return state
            if completion.get("state") == "published":
                return state
            completion["state"] = "publishing"
            claimed = True
            return state
        state = update_state(job_dir, claim)
        if not claimed or state is None:
            return
        if self._publish_async_completion(job_id, job_dir, state):
            def published(current: dict) -> dict:
                completion = current.get("completion")
                if isinstance(completion, dict) and completion.get("state") == "publishing":
                    completion["state"] = "published"
                    completion["published_at"] = time.time()
                return current
            update_state(job_dir, published)

    def _publish_async_completion(self, job_id: str, job_dir: Path, state: dict) -> bool:
        try:
            from datetime import datetime, timezone
            stdout, _ = self._read_logs(job_dir)
            exit_code = state.get("exit_code") if state.get("exit_status_known") else None
            payload = {
                "header": f"Job {job_id} completed (exit {exit_code})",
                "icon": "⚡", "priority": "normal",
                "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data": {
                    "job_id": job_id,
                    "command": str(state.get("command", ""))[:200],
                    "exit_code": exit_code,
                    "exit_status_known": bool(state.get("exit_status_known")),
                    "stdout_preview": stdout[:200],
                    "ref_id": f"bash.completion:{job_id}",
                },
            }
            store = self._agent._notification_store
            if hasattr(store, "compare_update_channel"):
                from lingtai.kernel.notification_store import UNCONDITIONAL

                ref_id = f"bash.completion:{job_id}"

                def mutate(current_payload: dict) -> tuple[dict | None, bool, bool]:
                    current = current_payload if isinstance(current_payload, dict) else {}
                    data = current.get("data")
                    if isinstance(data, dict) and data.get("ref_id") == ref_id:
                        return current_payload, False, True
                    return payload, True, True

                result = store.compare_update_channel("bash", UNCONDITIONAL, mutate)
                return bool(result.value)
            store.publish("bash", payload)
            return True
        except Exception:
            return False

    def _read_logs(self, job_dir: Path) -> tuple[str, str]:
        try:
            stdout = (job_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            stdout = ""
        try:
            stderr = (job_dir / "stderr.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            stderr = ""
        if len(stdout) > self._max_output:
            stdout = stdout[: self._max_output] + f"\n... (truncated, {len(stdout)} chars total)"
        if len(stderr) > self._max_output:
            stderr = stderr[: self._max_output] + f"\n... (truncated, {len(stderr)} chars total)"
        return stdout, stderr

    def _already_finished(self, state: dict) -> dict:
        label = "cancelled" if state.get("terminal_consumed_by") == "cancel" else state.get("status")
        return {"status": "error", "message": f"Job already finished ({label})"}

    def _claim_terminal(self, job_dir: Path, consumer: str) -> dict | None:
        """Atomically consume a terminal result and suppress its reminder.

        The conditional state transition is the one-shot linearization point for
        both poll and cancel.  Suppression belongs in this same durable write so
        no successful terminal consumer can leave a later deadline publication.
        """
        claimed = False

        def claim(current: dict) -> dict:
            nonlocal claimed
            if not self._terminal(current.get("status")) or current.get("terminal_polled"):
                return current
            now = time.time()
            current.update({
                "terminal_polled": True,
                "terminal_polled_at": now,
                "terminal_consumed_by": consumer,
            })
            reminder = current.get("reminder")
            if isinstance(reminder, dict):
                # Even a previously published reminder is terminally suppressed
                # for future retries; its published_at remains historical evidence.
                reminder.update({"state": "suppressed", "suppressed_at": now})
                reminder.pop("claim_token", None)
                reminder.pop("suppressing_at", None)
                reminder.pop("suppressing_until", None)
            claimed = True
            return current

        state = update_state(job_dir, claim)
        return state if claimed else None

    def _terminal_result(self, job_id: str, job_dir: Path) -> dict | None:
        state = self._claim_terminal(job_dir, "poll")
        if state is None:
            return None
        self._cancel_reminder_timer(job_id)
        stdout, stderr = self._read_logs(job_dir)
        if state.get("exit_status_known") and isinstance(state.get("exit_code"), int):
            return _augment_command_result({
                "status": "done", "exit_status_known": True,
                "exit_code": state["exit_code"], "stdout": stdout, "stderr": stderr,
            })
        return {
            "status": "done", "job_id": job_id, "exit_status_known": False,
            "exit_code": None, "stdout": stdout, "stderr": stderr,
            "message": "Async job terminated but its exit status is unavailable",
        }

    def _claim_legacy_terminal(self, job_dir: Path) -> bool:
        """Preserve old unknown-exit one-shot behavior without creating an exit code."""
        try:
            marker = job_dir / ".legacy-terminal-polled"
            with marker.open("x", encoding="utf-8") as handle:
                handle.write(f"{time.time()}\n")
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    def _legacy_unknown(self, job_id: str, job_dir: Path, message: str | None = None) -> dict:
        if not self._claim_legacy_terminal(job_dir):
            return {"status": "error", "message": "Job already finished (legacy unknown)"}
        stdout, stderr = self._read_logs(job_dir)
        return {
            "status": "done", "job_id": job_id, "exit_status_known": False,
            "exit_code": None, "stdout": stdout, "stderr": stderr,
            "message": message or "Legacy async job has no recoverable exit status",
        }

    def _handle_legacy_poll(self, job_id: str, job_dir: Path) -> dict:
        if (job_dir / ".legacy-terminal-polled").exists():
            return {"status": "error", "message": "Job already finished (legacy unknown)"}
        try:
            status = (job_dir / "status").read_text(encoding="utf-8").strip()
            pid = int((job_dir / "pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return self._legacy_unknown(job_id, job_dir)
        if status != "running" or self._async_process.observe(ProcessRef(pid, "legacy")).kind == "gone":
            return self._legacy_unknown(job_id, job_dir)
        # A legacy record cannot prove the PID incarnation, so it is never safe
        # to signal.  But a still-live PID is not evidence that the old command
        # has terminated either: keep the job pollable instead of consuming a
        # fabricated unknown terminal result while it may still be running.
        return {
            "status": "running",
            "job_id": job_id,
            "pid": pid,
            "message": "Legacy async job may still be running; cancellation is unavailable without durable supervisor ownership",
        }

    @staticmethod
    def _supervisor_start_lease_expired(state: dict) -> bool:
        """Whether a version-3 job missed its bounded pre-command start lease."""
        if state.get("status") != "launching":
            return False
        lease = state.get("supervisor_start_lease")
        if not isinstance(lease, dict) or lease.get("state") not in {"pending", "claimed"}:
            return False
        deadline_at = lease.get("deadline_at")
        return (
            isinstance(deadline_at, (int, float))
            and not isinstance(deadline_at, bool)
            and time.time() >= float(deadline_at)
        )

    @staticmethod
    def _durable_process_ref(state: dict, prefix: str) -> ProcessRef | None:
        """Prefer the neutral state contract, retaining v3 PID fields as fallback."""
        process = process_ref_from_state(state, prefix)
        if process is not None:
            return process
        if prefix == "command":
            public_id = state.get("pid")
            incarnation = state.get("pid_identity")
        elif prefix == "supervisor":
            public_id = state.get("supervisor_pid")
            incarnation = state.get("supervisor_identity")
        else:
            return None
        if (
            not isinstance(public_id, int)
            or isinstance(public_id, bool)
            or public_id <= 0
            or not isinstance(incarnation, str)
            or not incarnation
        ):
            return None
        return ProcessRef(public_id, incarnation)

    def _supervisor_definitively_gone(self, state: dict) -> bool:
        """True when an owned supervisor is absent or its incarnation changed."""
        process = self._durable_process_ref(state, "supervisor")
        if process is not None:
            return self._async_process.observe(process).kind in {"gone", "changed"}
        public_id = state.get("supervisor_pid")
        if not isinstance(public_id, int) or isinstance(public_id, bool) or public_id <= 0:
            return False
        # Absence remains proof for retained states that could not capture an
        # incarnation; a still-live diagnostic ID alone is never ownership proof.
        return self._async_process.observe(ProcessRef(public_id, "legacy")).kind == "gone"

    def _mark_unrecoverable_if_supervisor_gone(self, job_dir: Path) -> dict | None:
        """Resolve a lost supervisor or expired start lease under the state lock."""
        marked = False

        def mark(current: dict) -> dict:
            nonlocal marked
            if self._terminal(current.get("status")):
                return current
            lease_expired = self._supervisor_start_lease_expired(current)
            supervisor_gone = self._supervisor_definitively_gone(current)
            if not lease_expired and not supervisor_gone:
                return current
            reason = (
                "supervisor start lease expired before command spawn"
                if lease_expired
                else "recorded supervisor is definitively gone before terminal commit"
            )
            current.update({
                "status": "unrecoverable",
                "exit_status_known": False,
                "exit_code": None,
                "finished_at": time.time(),
                "supervisor_error": reason,
            })
            marked = True
            return current

        state = update_state(job_dir, mark)
        return state if marked else None

    def _await_supervisor_commit(self, job_dir: Path, timeout: float) -> dict | None:
        """Reload terminal truth while a supervisor or valid start lease can commit."""
        deadline = time.monotonic() + timeout
        state = load_state(job_dir)
        while state is not None:
            if self._terminal(state.get("status")):
                return state
            resolved = self._mark_unrecoverable_if_supervisor_gone(job_dir)
            if resolved is not None:
                return resolved
            if time.monotonic() >= deadline:
                return state
            time.sleep(0.01)
            state = load_state(job_dir)
        return None

    def _running_result(self, job_id: str, state: dict) -> dict:
        process = self._durable_process_ref(state, "command")
        if process is not None and self._async_process.observe(process).kind == "same":
            return {"status": "running", "job_id": job_id, "pid": process.public_id}
        return {
            "status": "running",
            "job_id": job_id,
            "message": "Awaiting the durable supervisor terminal commit",
        }

    def _handle_poll(self, args: dict) -> dict:
        job_id = args.get("job_id", "")
        err = self._validate_job_id(job_id)
        if err:
            return err
        job_dir = self._jobs_path() / job_id
        if not job_dir.is_dir():
            return {"status": "error", "message": f"Job not found: {job_id}"}
        state = load_state(job_dir)
        if state is None:
            return self._handle_legacy_poll(job_id, job_dir)
        if state.get("terminal_polled"):
            return self._already_finished(state)
        if self._terminal(state.get("status")):
            result = self._terminal_result(job_id, job_dir)
            return result if result is not None else self._already_finished(load_state(job_dir) or state)

        process = self._durable_process_ref(state, "command")
        if process is not None and self._async_process.observe(process).kind == "same":
            return {"status": "running", "job_id": job_id, "pid": process.public_id}

        # A dead/mismatched command PID is not terminal evidence: its detached
        # supervisor may have already obtained the exact wait result but not yet
        # committed it.  Give that verified supervisor a bounded commit window.
        state = self._await_supervisor_commit(job_dir, _SUPERVISOR_COMMIT_GRACE_SECONDS) or state
        if state.get("terminal_polled"):
            return self._already_finished(state)
        if self._terminal(state.get("status")):
            result = self._terminal_result(job_id, job_dir)
            return result if result is not None else self._already_finished(load_state(job_dir) or state)
        return self._running_result(job_id, state)

    def _handle_cancel(self, args: dict) -> dict:
        job_id = args.get("job_id", "")
        err = self._validate_job_id(job_id)
        if err:
            return err
        job_dir = self._jobs_path() / job_id
        if not job_dir.is_dir():
            return {"status": "error", "message": f"Job not found: {job_id}"}
        state = load_state(job_dir)
        if state is None:
            return {"status": "error", "message": "Cannot cancel legacy async job without durable supervisor ownership"}
        if self._terminal(state.get("status")) or state.get("terminal_polled"):
            return self._already_finished(state)
        supervisor = self._durable_process_ref(state, "supervisor")
        if supervisor is None:
            return {
                "status": "error",
                "message": "Cannot cancel async job: durable supervisor identity is unavailable",
            }
        supervisor_observation = self._async_process.observe(supervisor).kind
        if supervisor_observation in {"gone", "changed"}:
            self._mark_unrecoverable_if_supervisor_gone(job_dir)
            return {
                "status": "error",
                "message": "Cannot cancel async job: recorded supervisor identity is no longer live; poll for the durable terminal result",
            }
        if supervisor_observation != "same":
            return {
                "status": "error",
                "message": "Cannot cancel async job: durable supervisor identity cannot be verified",
            }

        requested = False

        def request_cancel(current: dict) -> dict:
            nonlocal requested
            if self._terminal(current.get("status")) or current.get("terminal_polled"):
                return current
            now = time.time()
            if not current.get("cancel_requested_at"):
                current["cancel_requested_at"] = now
            reminder = current.get("reminder")
            if isinstance(reminder, dict) and reminder.get("state") in {
                "pending", "publishing"
            }:
                reminder.update({
                    "state": "suppressing",
                    "suppressing_at": now,
                    "suppressing_until": now + _CANCEL_COMMIT_TIMEOUT_SECONDS,
                })
                reminder.pop("claim_token", None)
                reminder.pop("claimed_at", None)
            requested = True
            return current

        state = update_state(job_dir, request_cancel)
        if not requested or state is None:
            return self._already_finished(state or {})
        self._cancel_reminder_timer(job_id)

        # The detached supervisor owns the unreaped Popen and performs TERM/KILL.
        # A manager only requests that protocol, then waits for its exact commit.
        state = self._await_supervisor_commit(job_dir, _CANCEL_COMMIT_TIMEOUT_SECONDS)
        if state is not None and state.get("status") == "completed":
            if state.get("cancellation_outcome") != "group_cancelled":
                return {
                    "status": "error",
                    "message": (
                        "Cancellation did not confirm process-group termination; "
                        "poll for the exact durable terminal result"
                    ),
                }
            claimed = self._claim_terminal(job_dir, "cancel")
            if claimed is not None:
                self._cancel_reminder_timer(job_id)
                return {"status": "cancelled", "job_id": job_id}
            return self._already_finished(load_state(job_dir) or state)
        if state is not None and state.get("terminal_polled"):
            return self._already_finished(state)
        reminder_restored = False

        def restore_reminder(current: dict) -> dict:
            nonlocal reminder_restored
            if self._terminal(current.get("status")):
                return current
            reminder = current.get("reminder")
            if isinstance(reminder, dict) and reminder.get("state") == "suppressing":
                reminder["state"] = "pending"
                reminder.pop("suppressing_at", None)
                reminder.pop("suppressing_until", None)
                reminder_restored = True
            return current

        update_state(job_dir, restore_reminder)
        if reminder_restored:
            self._start_reminder_timer(job_id, job_dir)
        return {
            "status": "error",
            "message": (
                "Cancellation requested; awaiting supervisor terminal commit. "
                "The job remains pollable and its reminder remains recoverable."
            ),
        }

    def _close_handles(self, job_id: str) -> None:
        """Compatibility no-op: durable supervisors own and close their logs."""
        return None

def setup(
    agent: "BaseAgent",
    policy_file: str | None = None,
    yolo: bool = False,
) -> BashManager:
    """Set up the bash capability on an agent.

    Args:
        agent: The agent to extend.
        policy_file: Path to JSON policy file (required unless yolo=True).
        yolo: If True, allow all commands (no policy file needed).

    Returns:
        The BashManager instance for programmatic access.
    """
    # Resolve policy: explicit arg or default
    resolved_policy_file = policy_file

    if yolo:
        policy = BashPolicy.yolo()
    elif resolved_policy_file is not None:
        policy = BashPolicy.from_file(resolved_policy_file)
    else:
        policy = BashPolicy.from_file(str(_DEFAULT_POLICY_FILE))


    dialect = _select_bash_shell_dialect()
    mgr = BashManager(
        policy=policy,
        working_dir=str(agent._working_dir),
        agent=agent,
        dialect=dialect,
    )
    # Build description with policy rules
    desc = get_description()
    policy_summary = policy.describe()
    if policy_summary:
        desc = f"{desc}\n\n{policy_summary}"

    agent.add_tool("bash", schema=get_schema(), handler=mgr.handle, description=desc, glossary_package=__package__)
    return mgr
