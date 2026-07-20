"""Real POSIX subprocess coverage for how-to-change-name."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py"


def _write_init(root: Path, venv_dir: Path) -> None:
    data = {
        "manifest": {
            "agent_name": "real-e2e-true-name", "language": "en",
            "llm": {"provider": "gemini", "model": "test-model", "api_key": "fake-key", "base_url": None},
            "capabilities": {}, "soul": {"delay": 60}, "stamina": 10,
            "context_limit": None, "molt_pressure": 0.8, "molt_prompt": "",
            "max_turns": 5, "admin": {}, "streaming": False,
        },
        "principle": "", "covenant": "No network activity.", "pad": "", "lingtai": "",
        "venv_path": str(venv_dir),
    }
    (root / "init.json").write_text(json.dumps(data), encoding="utf-8")


def _wait_for(path: Path, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.1)
    return False


def _process_executable(pid: int) -> str | None:
    proc_exe = Path("/proc") / str(pid) / "exe"
    try:
        return os.readlink(proc_exe)
    except OSError:
        try:
            lsof = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "txt", "-Fn"],
                text=True, capture_output=True, timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if lsof.returncode != 0:
            return None
        return next((entry[1:] for entry in lsof.stdout.splitlines() if entry.startswith("n")), None)


def _owned_process_identity(pid: int, root: Path) -> tuple[str, str, str, str] | None:
    """Return command, cwd, observed txt, and invoked executable evidence."""
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "ppid=,lstart=,command="],
        text=True, capture_output=True, timeout=3,
    )
    line = result.stdout.strip()
    if result.returncode != 0 or not line or not line.endswith(f" -m lingtai run {root}"):
        return None

    cwd: str | None = None
    proc_cwd = Path("/proc") / str(pid) / "cwd"
    try:
        cwd = os.readlink(proc_cwd)
    except OSError:
        try:
            lsof = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                text=True, capture_output=True, timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            lsof = None
        if lsof is not None and lsof.returncode == 0:
            cwd = next((entry[1:] for entry in lsof.stdout.splitlines() if entry.startswith("n")), None)
    if cwd is None or Path(cwd).resolve() != root.resolve():
        return None
    executable = _process_executable(pid)
    if executable is None:
        return None
    command = re.search(r"(?P<executable>\S+) -m lingtai run " + re.escape(str(root)) + r"$", line)
    if command is None:
        return None
    return line, cwd, executable, command.group("executable")


@pytest.mark.skipif(os.name != "posix", reason="first version is POSIX-only")
def test_real_agent_suspend_rename_resume_and_exact_stop(tmp_path: Path):
    old = tmp_path / "real-old"
    old.mkdir()
    venv = old / "runtime" / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", "--system-site-packages", str(venv)],
        check=True, text=True, capture_output=True, timeout=60,
    )
    _write_init(old, venv)
    executable_probe = tmp_path / "resumed-executable.txt"
    probe_site = tmp_path / "probe-site"
    probe_site.mkdir()
    (probe_site / "sitecustomize.py").write_text(
        "import os, sys\n"
        "path = os.environ.get('LINGTAI_E2E_EXECUTABLE_PROBE')\n"
        "if path:\n"
        "    with open(path, 'a', encoding='utf-8') as stream:\n"
        "        stream.write(f'{os.getpid()}\\t{sys.executable}\\n')\n",
        encoding="utf-8",
    )
    helper = old / "change_name.py"
    helper.write_bytes(SCRIPT.read_bytes())
    helper.chmod(0o755)
    venv_python = venv / "bin" / "python"
    env = os.environ.copy()
    inherited_paths = os.pathsep.join(path for path in sys.path if path)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(probe_site), str(ROOT / "src"), inherited_paths, env.get("PYTHONPATH", "")]
    )
    env["LINGTAI_E2E_EXECUTABLE_PROBE"] = str(executable_probe)
    log = tmp_path / "boot.log"
    stream = log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(venv_python), "-m", "lingtai", "run", str(old)],
        cwd=str(old), env=env, stdin=subprocess.DEVNULL,
        stdout=stream, stderr=subprocess.STDOUT,
    )
    new = tmp_path / "real-new"
    resumed_pid: int | None = None
    resumed_identity: tuple[str, str, str, str] | None = None
    try:
        assert _wait_for(old / ".agent.heartbeat"), log.read_text()
        assert _wait_for(old / ".agent.lock"), log.read_text()
        assert _wait_for(old / ".agent.json"), log.read_text()
        before = json.loads((old / ".agent.json").read_text())
        # Normal mode must exercise the detached external supervisor; the
        # terminal receipt, not this handoff's quick return, is completion.
        result = subprocess.run(
            [str(venv_python), str(helper), str(old), "real-new", "--timeout", "20"],
            cwd=str(old), env=env, text=True, capture_output=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr + log.read_text()
        assert "supervisor handed off" in result.stdout
        receipt = new / "logs" / "name-change.json"
        assert _wait_for(receipt, timeout=35), result.stdout + result.stderr + log.read_text()
        terminal = json.loads(receipt.read_text())
        assert terminal["status"] == "success", terminal
        assert not old.exists() and new.is_dir()
        after = json.loads((new / ".agent.json").read_text())
        assert after["agent_id"] == before["agent_id"]
        assert after["agent_name"] == before["agent_name"] == "real-e2e-true-name"
        assert after["address"] == "real-new"
        assert json.loads((new / "init.json").read_text())["venv_path"] == str(new / "runtime" / "venv")
        assert _wait_for(new / ".agent.heartbeat")
        resumed_pid = terminal["pid"]
        assert isinstance(resumed_pid, int)
        resumed_identity = _owned_process_identity(resumed_pid, new)
        assert resumed_identity is not None
        expected_runtime = (new / "runtime" / "venv" / "bin" / "python").resolve()
        # Linux /proc reports the exact executable.  macOS lsof reports the
        # framework binary behind its Python launcher; in that case the venv
        # interpreter's own sys.executable is the safe equivalent.
        assert Path(resumed_identity[2]).is_file()
        if (Path("/proc") / str(resumed_pid) / "exe").is_symlink():
            assert Path(resumed_identity[2]).resolve() == expected_runtime
        probes = [line.split("\t", 1) for line in executable_probe.read_text().splitlines() if "\t" in line]
        assert any(pid == str(resumed_pid) and Path(executable).resolve() == expected_runtime
                   for pid, executable in probes)
    finally:
        stream.close()
        # Stop only the exact process recorded by the helper. Cooperative stop
        # is primary. Any fallback signal is gated by unchanged parent/start,
        # full command, and cwd evidence so PID reuse cannot hit another process.
        target = new if new.is_dir() else old
        if target.is_dir() and (target / ".agent.heartbeat").exists():
            (target / ".suspend").touch()
        if new.is_dir():
            receipt = new / "logs" / "name-change.json"
            if receipt.exists() and resumed_pid is None:
                try:
                    candidate = json.loads(receipt.read_text()).get("pid")
                except (OSError, ValueError):
                    candidate = None
                if isinstance(candidate, int):
                    resumed_pid = candidate
                    resumed_identity = _owned_process_identity(candidate, new)
            if resumed_pid is not None and resumed_identity is not None:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if _owned_process_identity(resumed_pid, new) is None:
                        break
                    time.sleep(0.1)
                else:
                    current = _owned_process_identity(resumed_pid, new)
                    assert current == resumed_identity, "refusing SIGTERM: resumed PID identity changed"
                    os.kill(resumed_pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if _owned_process_identity(resumed_pid, new) is None:
                            break
                        time.sleep(0.1)
                    else:
                        current = _owned_process_identity(resumed_pid, new)
                        assert current == resumed_identity, "refusing SIGKILL: resumed PID identity changed"
                        os.kill(resumed_pid, signal.SIGKILL)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
