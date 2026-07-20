"""One real temporary-agent rename E2E for the POSIX helper."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py"


def _wait_for(path: Path, timeout: float = 25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def _agents_at(root: Path) -> list[int]:
    result = subprocess.run(["ps", "-ax", "-o", "pid=,command="], text=True, capture_output=True, check=True)
    suffix = f" -m lingtai run {root}"
    return [int(line.split(None, 1)[0]) for line in result.stdout.splitlines() if line.endswith(suffix)]


def _write_init(root: Path, venv: Path) -> None:
    (root / "init.json").write_text(json.dumps({
        "manifest": {
            "agent_name": "temporary-true-name", "language": "en",
            "llm": {"provider": "gemini", "model": "test", "api_key": "fake", "base_url": None},
            "capabilities": {}, "soul": {"delay": 60}, "stamina": 10,
            "context_limit": None, "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 5,
            "admin": {}, "streaming": False,
        },
        "principle": "", "covenant": "No network.", "pad": "", "lingtai": "", "venv_path": str(venv),
    }), encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_real_agent_suspend_rename_rebase_and_resume(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    venv = old / "runtime" / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", "--system-site-packages", str(venv)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    _write_init(old, venv)
    helper = old / "change_name.py"
    helper.write_bytes(SCRIPT.read_bytes())
    helper.chmod(0o755)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH", "")]))
    boot_log = tmp_path / "boot.log"
    stream = boot_log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(venv / "bin" / "python"), "-m", "lingtai", "run", str(old)],
        cwd=old, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
    )
    try:
        assert _wait_for(old / ".agent.heartbeat"), boot_log.read_text()
        assert _wait_for(old / ".agent.lock"), boot_log.read_text()
        assert _wait_for(old / ".agent.json"), boot_log.read_text()
        before = json.loads((old / ".agent.json").read_text())
        result = subprocess.run(
            [str(venv / "bin" / "python"), str(helper), str(old), "new", "--timeout", "20"],
            cwd=old, env=env, text=True, capture_output=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr + boot_log.read_text()
        assert _wait_for(new / ".agent.heartbeat"), boot_log.read_text()
        after = json.loads((new / ".agent.json").read_text())
        assert not old.exists() and after["agent_id"] == before["agent_id"]
        assert after["agent_name"] == before["agent_name"] == "temporary-true-name"
        assert after["address"] == "new"
        assert json.loads((new / "init.json").read_text())["venv_path"] == str(new / "runtime" / "venv")
        assert _agents_at(new)
    finally:
        stream.close()
        target = new if new.is_dir() else old
        if target.is_dir():
            (target / ".suspend").touch()
            deadline = time.monotonic() + 20
            while _agents_at(target) and time.monotonic() < deadline:
                time.sleep(0.1)
            for pid in _agents_at(target):
                os.kill(pid, signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
