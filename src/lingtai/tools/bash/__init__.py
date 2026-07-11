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
import os
import re
import signal
import subprocess
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent

PROVIDERS = {"providers": [], "default": "builtin"}

_DEFAULT_POLICY_FILE = Path(__file__).parent / "bash_policy.json"
_DEFAULT_ASYNC_REMINDER_SECONDS = 1800.0
_SYSTEM_NOTIFICATION_FALLBACK_LOCK = threading.Lock()

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
                "description": "Last-resort async wake delay in seconds (default: 1800). For async run only: if the job has not been terminally polled or cancelled by then, publish a system notification reminding you to poll it.",
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
        flat = command
        # Expand $(...) subshells into the command chain
        flat = re.sub(r'\$\([^)]*\)', lambda m: '; ' + m.group()[2:-1] + ' ;', flat)
        # Expand backtick subshells
        flat = re.sub(r'`[^`]*`', lambda m: '; ' + m.group()[1:-1] + ' ;', flat)
        # Split on pipe/chain operators AND newlines
        parts = re.split(r'\|{1,2}|&&|;|\n', flat)
        commands = []
        for part in parts:
            tokens = part.strip().split()
            # Skip env-var assignments (FOO=bar cmd ...) to find the real command
            while tokens and re.fullmatch(r'[A-Za-z_]\w*=\S*', tokens[0]):
                tokens = tokens[1:]
            if tokens:
                commands.append(tokens[0])
        return commands


class BashManager:
    """Manages shell command execution for an agent."""

    def __init__(
        self,
        policy: BashPolicy,
        working_dir: str,
        max_output: int = 50_000,
        agent: "BaseAgent | None" = None,
    ):
        self._policy = policy
        self._working_dir = working_dir
        self._max_output = max_output
        self._agent = agent
        self._jobs_dir: Path | None = None
        self._reminder_lock = threading.Lock()
        self._reminder_cancel_events: dict[str, threading.Event] = {}

    def _ensure_jobs_dir(self) -> Path:
        """Create and return the jobs directory (lazy init)."""
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
        if not self._policy.is_allowed(command):
            denied = BashPolicy._extract_commands(command)
            return {
                "status": "error",
                "message": f"Command not allowed by policy. "
                f"Denied command(s): {', '.join(denied)}",
            }
        return None

    @staticmethod
    def _validate_job_id(job_id: str) -> dict | None:
        """Validate job_id is safe (no path traversal). Returns error dict or None."""
        if not job_id:
            return {"status": "error", "message": "job_id is required"}
        # Reject path traversal attempts
        if "/" in job_id or "\\" in job_id or ".." in job_id:
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
        if (
            delay < 0
            or not math.isfinite(delay)
            or delay > threading.TIMEOUT_MAX
        ):
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
        # action == "run"
        return self._handle_run(args)

    def _handle_run(self, args: dict) -> dict:
        command = args.get("command", "")
        err = self._validate_command(command)
        if err:
            return err

        # Treat an empty/whitespace-only working_dir the same as omitting it:
        # run in the agent working directory rather than failing the sandbox
        # check (models commonly pass working_dir="" to mean "default").
        cwd = args.get("working_dir") or self._working_dir
        if isinstance(cwd, str) and not cwd.strip():
            cwd = self._working_dir
        err = self._validate_working_dir(cwd)
        if err:
            return err

        is_async = args.get("async", False)
        if is_async:
            reminder, err = self._validate_reminder(args.get("reminder"))
            if err:
                return err
            return self._run_async(command, cwd, reminder)
        return self._run_sync(command, cwd, args.get("timeout", 30))

    def _run_sync(self, command: str, cwd: str, timeout: float) -> dict:
        """Synchronous execution — original behavior, unchanged."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            stdout = result.stdout
            stderr = result.stderr
            if len(stdout) > self._max_output:
                stdout = stdout[: self._max_output] + f"\n... (truncated, {len(result.stdout)} chars total)"
            if len(stderr) > self._max_output:
                stderr = stderr[: self._max_output] + f"\n... (truncated, {len(result.stderr)} chars total)"

            return _augment_command_result({
                "status": "ok",
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
            })
        except subprocess.TimeoutExpired:
            msg = f"Command timed out after {timeout}s"
            hint = _broad_scan_hint(command)
            if hint:
                msg = f"{msg}. {hint}"
            return {"status": "error", "message": msg}
        except Exception as e:
            return {"status": "error", "message": f"Command failed: {e}"}

    def _run_async(self, command: str, cwd: str, reminder: float) -> dict:
        """Start command in background, return job_id immediately."""
        jobs_dir = self._ensure_jobs_dir()
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job_dir = jobs_dir / job_id
        job_dir.mkdir()

        (job_dir / "command").write_text(command)
        (job_dir / "status").write_text("running")

        stdout_f = open(job_dir / "stdout.log", "w", encoding="utf-8")
        stderr_f = open(job_dir / "stderr.log", "w", encoding="utf-8")

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=stdout_f,
                stderr=stderr_f,
                cwd=cwd,
                start_new_session=True,
            )
        except Exception as e:
            stdout_f.close()
            stderr_f.close()
            # Clean up on launch failure
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)
            return {"status": "error", "message": f"Failed to start async job: {e}"}

        (job_dir / "pid").write_text(str(proc.pid))
        # Store Popen + file handles in-process so we can reap and close them.
        if not hasattr(self, "_open_handles"):
            self._open_handles: dict[str, tuple] = {}
        self._open_handles[job_id] = (proc, stdout_f, stderr_f)
        self._start_reminder_timer(job_id, job_dir, reminder)

        # Start background watcher — writes .notification/bash.json when
        # the process exits, so the agent gets notified via the standard
        # notification sync mechanism (same channel as email/soul/molt).
        watcher = threading.Thread(
            target=self._watch_async_job,
            args=(job_id, command, proc, job_dir, stdout_f, stderr_f),
            daemon=True,
        )
        watcher.start()

        return {
            "status": "ok",
            "job_id": job_id,
            "pid": proc.pid,
            "message": f'Job started. Use bash(action="poll", job_id="{job_id}") to check.',
        }

    def _start_reminder_timer(self, job_id: str, job_dir: Path, delay: float) -> None:
        """Arm the last-resort async poll reminder for a job."""
        cancel_event = threading.Event()
        with self._reminder_lock:
            self._reminder_cancel_events[job_id] = cancel_event
        timer = threading.Thread(
            target=self._run_reminder_timer,
            args=(job_id, job_dir, delay, cancel_event),
            daemon=True,
        )
        timer.start()

    def _run_reminder_timer(
        self,
        job_id: str,
        job_dir: Path,
        delay: float,
        cancel_event: threading.Event,
    ) -> None:
        """Publish one reminder unless terminal poll/cancel handles the job first."""
        if cancel_event.wait(delay):
            return
        if not self._claim_reminder_timer(job_id, job_dir, cancel_event):
            return
        self._publish_async_reminder(job_id)

    def _claim_reminder_timer(
        self,
        job_id: str,
        job_dir: Path,
        cancel_event: threading.Event,
    ) -> bool:
        """Atomically claim the reminder deadline before publishing."""
        with self._reminder_lock:
            current = self._reminder_cancel_events.get(job_id)
            if current is not cancel_event or cancel_event.is_set() or not job_dir.is_dir():
                return False
            self._reminder_cancel_events.pop(job_id, None)
            cancel_event.set()
            return True

    def _cancel_reminder_timer(self, job_id: str) -> None:
        """Suppress and forget a pending reminder after terminal poll/cancel."""
        with self._reminder_lock:
            cancel_event = self._reminder_cancel_events.pop(job_id, None)
        if cancel_event is not None:
            cancel_event.set()

    def _publish_async_reminder(self, job_id: str) -> None:
        """Append the last-resort reminder to the durable multi-event system channel."""
        body = (
            f"Bash async job {job_id} may still be running. "
            f"Poll it with bash(action=\"poll\", job_id=\"{job_id}\")."
        )
        agent = self._agent
        if agent is not None and hasattr(agent, "_enqueue_system_notification"):
            try:
                agent._enqueue_system_notification(
                    source="bash.reminder",
                    ref_id=f"bash.reminder:{job_id}",
                    body=body,
                    skip_if_ref_id_exists=True,
                )
                return
            except Exception:
                pass
        fallback_lock = getattr(agent, "_system_notification_lock", None)
        self._append_system_notification_fallback(
            source="bash.reminder",
            ref_id=f"bash.reminder:{job_id}",
            body=body,
            lock=fallback_lock,
        )

    def _append_system_notification_fallback(
        self,
        *,
        source: str,
        ref_id: str,
        body: str,
        lock: threading.Lock | None = None,
    ) -> str:
        """Small direct-manager equivalent of BaseAgent._enqueue_system_notification."""
        import secrets
        import time
        from datetime import datetime, timezone

        from lingtai.kernel.notifications import collect_notifications
        from lingtai.kernel.notifications import submit as publish_notification

        event_id = f"evt_{int(time.time()*1000):x}_{secrets.token_hex(8)}"
        received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        actual_lock = lock or _SYSTEM_NOTIFICATION_FALLBACK_LOCK
        with actual_lock:
            current = collect_notifications(Path(self._working_dir)).get("system", {})
            events = list(current.get("data", {}).get("events", []))
            if any(isinstance(ev, dict) and ev.get("ref_id") == ref_id for ev in events):
                return ""
            events.append({
                "event_id": event_id,
                "source": source,
                "ref_id": ref_id,
                "body": body,
                "at": received_at,
            })
            events = events[-20:]
            publish_notification(
                Path(self._working_dir),
                "system",
                header=(
                    f"{len(events)} system notification"
                    f"{'s' if len(events) != 1 else ''}"
                ),
                icon="🔔",
                priority="normal",
                data={"events": events},
            )
        return event_id

    def _watch_async_job(
        self, job_id: str, command: str, proc: subprocess.Popen,
        job_dir: Path, stdout_f, stderr_f,
    ) -> None:
        """Background thread: wait for async job, then write notification."""
        try:
            returncode = proc.wait()
        except Exception:
            returncode = -1

        # Close file handles
        try:
            stdout_f.close()
            stderr_f.close()
        except Exception:
            pass

        # Read stdout preview
        stdout_preview = ""
        try:
            stdout_text = (job_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
            stdout_preview = stdout_text[:200]
        except Exception:
            pass

        # Write notification to .notification/bash.json
        try:
            from datetime import datetime, timezone
            notif_dir = Path(self._working_dir) / ".notification"
            notif_dir.mkdir(exist_ok=True)
            payload = {
                "header": f"Job {job_id} completed (exit {returncode})",
                "icon": "⚡",
                "priority": "normal",
                "published_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "data": {
                    "job_id": job_id,
                    "command": command[:200],
                    "exit_code": returncode,
                    "stdout_preview": stdout_preview,
                },
            }
            target = notif_dir / "bash.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.rename(target)
        except Exception:
            pass  # Notification failure should not break the job

    def _handle_poll(self, args: dict) -> dict:
        """Check status of an async job."""
        job_id = args.get("job_id", "")
        err = self._validate_job_id(job_id)
        if err:
            return err

        jobs_dir = self._ensure_jobs_dir()
        job_dir = jobs_dir / job_id
        if not job_dir.is_dir():
            return {"status": "error", "message": f"Job not found: {job_id}"}

        status = (job_dir / "status").read_text(encoding="utf-8").strip()
        if status != "running":
            return {"status": "error", "message": f"Job already finished ({status})"}

        pid = int((job_dir / "pid").read_text(encoding="utf-8").strip())

        # Use Popen.poll() if we have the handle (same process), else os.waitpid
        handles = getattr(self, "_open_handles", {})
        entry = handles.get(job_id)
        if entry:
            proc = entry[0]
            returncode = proc.poll()
        else:
            # Fallback: try waitpid (different manager instance, same PID file)
            try:
                wpid, wait_status = os.waitpid(pid, os.WNOHANG)
                returncode = os.waitstatus_to_exitcode(wait_status) if wpid != 0 else None
            except ChildProcessError:
                # Not our child — check if alive via signal 0
                try:
                    os.kill(pid, 0)
                    returncode = None  # still alive
                except OSError:
                    returncode = -1  # dead but we can't get the code

        if returncode is None:
            return {"status": "running", "job_id": job_id, "pid": pid}

        # Process finished — close file handles, read output
        self._cancel_reminder_timer(job_id)
        self._close_handles(job_id)

        stdout = (job_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
        stderr = (job_dir / "stderr.log").read_text(encoding="utf-8", errors="replace")

        if len(stdout) > self._max_output:
            stdout = stdout[: self._max_output] + f"\n... (truncated, {len(stdout)} chars total)"
        if len(stderr) > self._max_output:
            stderr = stderr[: self._max_output] + f"\n... (truncated, {len(stderr)} chars total)"

        # Clean up
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

        return _augment_command_result({
            "status": "done",
            "exit_code": returncode,
            "stdout": stdout,
            "stderr": stderr,
        })

    def _handle_cancel(self, args: dict) -> dict:
        """Kill an async job."""
        job_id = args.get("job_id", "")
        err = self._validate_job_id(job_id)
        if err:
            return err

        jobs_dir = self._ensure_jobs_dir()
        job_dir = jobs_dir / job_id
        if not job_dir.is_dir():
            return {"status": "error", "message": f"Job not found: {job_id}"}

        status = (job_dir / "status").read_text(encoding="utf-8").strip()
        if status != "running":
            return {"status": "error", "message": f"Job already finished ({status})"}

        pid = int((job_dir / "pid").read_text(encoding="utf-8").strip())

        # Kill the entire process group (start_new_session=True makes pid the pgid)
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass  # Already dead

        # Reap via Popen if we have the handle, to avoid zombies
        handles = getattr(self, "_open_handles", {})
        entry = handles.get(job_id)
        if entry:
            proc = entry[0]
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        self._close_handles(job_id)
        self._cancel_reminder_timer(job_id)

        # Clean up
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

        return {"status": "cancelled", "job_id": job_id}

    def _close_handles(self, job_id: str) -> None:
        """Close open file handles for a job if we hold them."""
        handles = getattr(self, "_open_handles", {})
        entry = handles.pop(job_id, None)
        if entry:
            # entry is (Popen, stdout_file, stderr_file)
            for fh in entry[1:]:
                try:
                    fh.close()
                except Exception:
                    pass


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


    mgr = BashManager(
        policy=policy,
        working_dir=str(agent._working_dir),
        agent=agent,
    )
    # Build description with policy rules
    desc = get_description()
    policy_summary = policy.describe()
    if policy_summary:
        desc = f"{desc}\n\n{policy_summary}"

    agent.add_tool("bash", schema=get_schema(), handler=mgr.handle, description=desc, glossary_package=__package__)
    return mgr
