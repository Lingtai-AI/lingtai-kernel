"""POSIX adapter for the Bash-local asynchronous process Port."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lingtai.tools.bash._async_process import (
    BashAsyncProcessPort, ProcessCompletion, ProcessObservation, ProcessRef,
)
from lingtai.tools.bash._shell_dialect import ShellInvocation


@dataclass
class _Owned:
    process: subprocess.Popen
    ref: ProcessRef
    stdout: object | None = None
    stderr: object | None = None

    def close_logs(self) -> None:
        for handle in (self.stdout, self.stderr):
            if handle is not None:
                handle.close()


def _identity(pid: int) -> str | None:
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        end = raw.rfind(")")
        fields = raw[end + 2 :].split()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        return f"linux:{boot}:{fields[19]}"
    except (OSError, IndexError, ValueError):
        try:
            result = subprocess.run(
                ["ps", "-o", "lstart=", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=1, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        line = result.stdout.strip()
        return f"ps:{line}" if result.returncode == 0 and line else None


def _ref(pid: int) -> ProcessRef:
    return ProcessRef(pid, _identity(pid))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        end = raw.rfind(")")
        return raw[end + 2 :].split(maxsplit=1)[0] != "Z"
    except (OSError, IndexError, ValueError):
        return True


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return True
    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            for entry in proc_root.iterdir():
                if not entry.name.isdigit():
                    continue
                raw = (entry / "stat").read_text(encoding="utf-8")
                end = raw.rfind(")")
                fields = raw[end + 2 :].split()
                if int(fields[2]) == group and fields[0] != "Z":
                    return True
            return False
        except (OSError, IndexError, ValueError):
            return True
    try:
        result = subprocess.run(["ps", "-axo", "pgid=,state="], capture_output=True,
                                text=True, timeout=1, check=False)
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    return any(
        len(fields := line.split(None, 1)) == 2 and fields[0] == str(group)
        and not fields[1].lstrip().startswith("Z")
        for line in result.stdout.splitlines()
    )


class PosixBashAsyncProcessAdapter(BashAsyncProcessPort):
    def launch_supervisor(self, job_dir: Path, start_token: str):
        process = subprocess.Popen(
            [sys.executable, "-m", "lingtai.tools.bash._async_supervisor", str(job_dir), start_token],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        ref = _ref(process.pid)
        return ref, _Owned(process, ref)

    def identify_current_process(self):
        return _ref(os.getpid())

    def spawn(self, invocation: ShellInvocation, cwd: str, stdout_path: Path, stderr_path: Path):
        stdout = open(stdout_path, "x", encoding="utf-8")
        stderr = open(stderr_path, "x", encoding="utf-8")
        try:
            args, kwargs = invocation.process_args()
            kwargs.update({"text": True, "encoding": invocation.encoding or "utf-8",
                           "errors": invocation.errors or "replace"})
            process = subprocess.Popen(args, stdout=stdout, stderr=stderr, cwd=cwd,
                                       start_new_session=True, **kwargs)
        except Exception:
            stdout.close(); stderr.close()
            raise
        ref = _ref(process.pid)
        return ref, _Owned(process, ref, stdout, stderr)

    def observe(self, process: ProcessRef):
        if not _alive(process.public_id):
            return ProcessObservation("gone")
        observed = _ref(process.public_id)
        if process.incarnation is None or observed.incarnation is None:
            return ProcessObservation("unknown")
        return ProcessObservation("same" if observed.incarnation == process.incarnation else "changed")

    def wait_supervisor(self, owned: _Owned) -> int:
        return owned.process.wait()

    def wait(self, owned: _Owned, cancellation_requested: Callable[[], bool]):
        process = owned.process
        try:
            while True:
                if cancellation_requested():
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        return ProcessCompletion(process.wait(), "natural_or_concurrent")
                    except OSError:
                        return ProcessCompletion(process.wait(), "unconfirmed")
                    deadline = time.monotonic() + 0.5
                    while time.monotonic() < deadline:
                        time.sleep(min(0.05, deadline - time.monotonic()))
                    group_absent = False
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        group_absent = True
                    except OSError:
                        group_absent = not _group_alive(process.pid)
                        if not group_absent:
                            return ProcessCompletion(process.wait(), "unconfirmed")
                    code = process.wait()
                    if not group_absent:
                        deadline = time.monotonic() + 1.0
                        while _group_alive(process.pid):
                            if time.monotonic() >= deadline:
                                return ProcessCompletion(code, "unconfirmed")
                            time.sleep(0.05)
                    return ProcessCompletion(code, "group_cancelled")
                try:
                    return ProcessCompletion(process.wait(timeout=0.05))
                except subprocess.TimeoutExpired:
                    continue
        finally:
            owned.close_logs()
