"""One real temporary-agent rename E2E for the POSIX helper."""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import time
import urllib.parse
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/change_name.py"


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
            "capabilities": {}, "disable": ["mcp"], "soul": {"delay": 60}, "stamina": 10,
            "context_limit": None, "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 5,
            "admin": {}, "streaming": False,
        },
        "principle": "", "covenant": "No network.", "pad": "", "lingtai": "", "venv_path": str(venv),
        "mcp": {
            "local": {
                "type": "stdio", "command": str(root / "mcp" / "server"),
                "args": [str(root / "argument-must-stay-old")],
                "env": {"UNCHANGED": str(root)},
            },
            "external": {"type": "stdio", "command": sys.executable},
            "remote": {
                "type": "http", "url": "https://example.invalid/mcp",
                "command": str(root / "http-command-must-stay-old"),
            },
        },
    }), encoding="utf-8")


def _write_registry(root: Path) -> None:
    records = [
        {
            "name": "local", "summary": "local test server", "transport": "stdio",
            "command": str(root / "mcp" / "server"),
            "args": [str(root / "registry-argument-must-stay-old")],
            "source": "human", "extra": {"unchanged": str(root)},
        },
        {
            "name": "external", "summary": "external test server", "transport": "stdio",
            "command": sys.executable, "args": [], "source": "human",
        },
        {
            "name": "remote", "summary": "remote test server", "transport": "http",
            "url": "https://example.invalid/mcp",
            "command": str(root / "registry-http-command-must-stay-old"),
            "source": "human",
        },
    ]
    (root / "mcp_registry.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )


@pytest.mark.skipif(
    not (sys.platform == "darwin" or sys.platform.startswith("linux")),
    reason="requires the supported Linux/macOS no-replace rename lane",
)
def test_real_agent_suspend_rename_relocates_local_runtime_and_mcp_then_resumes(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    venv = old / "runtime" / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", str(venv)],
        check=True, capture_output=True, text=True, timeout=60,
    )

    source = old / "runtime" / "source"
    shutil.copytree(ROOT / "src" / "lingtai", source / "lingtai")
    decoy_root = tmp_path / "ambient-decoy"
    decoy_hit = tmp_path / "ambient-decoy-used"
    shutil.copytree(ROOT / "src" / "lingtai", decoy_root / "lingtai")
    (decoy_root / "lingtai" / "__main__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(decoy_hit)!r}).write_text('used', encoding='utf-8')\n"
        "raise SystemExit(86)\n",
        encoding="utf-8",
    )
    site = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    dependency_paths = [
        Path(path)
        for key in ("purelib", "platlib")
        if (path := sysconfig.get_paths().get(key)) and Path(path).is_dir()
    ]
    dependency_paths = list(dict.fromkeys(dependency_paths))
    editable_pth = site / "lingtai-editable.pth"
    editable_pth.write_text(
        "\n".join([str(source), *(str(path) for path in dependency_paths)]) + "\n",
        encoding="utf-8",
    )
    direct_url = site / "lingtai-test.dist-info" / "direct_url.json"
    direct_url.parent.mkdir()
    direct_url.write_text(json.dumps({
        "dir_info": {"editable": True, "unchanged": str(old)},
        "url": source.as_uri(),
    }), encoding="utf-8")

    launcher = venv / "bin" / "lingtai-agent"
    launcher.write_text(
        f"#!{venv / 'bin' / 'python'}\n"
        "from lingtai.cli import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    launcher.chmod(0o751)
    launcher_mode = stat.S_IMODE(launcher.stat().st_mode)
    _write_init(old, venv)
    _write_registry(old)
    registry_mode = stat.S_IMODE((old / "mcp_registry.jsonl").stat().st_mode)

    helper = old / "change_name.py"
    helper.write_bytes(SCRIPT.read_bytes())
    helper.chmod(0o755)
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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
        helper_env = env.copy()
        helper_env["PYTHONPATH"] = str(decoy_root)
        result = subprocess.run(
            [str(venv / "bin" / "python"), str(helper), str(old), "new", "--timeout", "20"],
            cwd=old, env=helper_env, text=True, capture_output=True, timeout=35,
        )
        assert result.returncode == 0, result.stdout + result.stderr + boot_log.read_text()
        assert not decoy_hit.exists()
        assert _wait_for(new / ".agent.heartbeat"), boot_log.read_text()
        after = json.loads((new / ".agent.json").read_text())
        assert not old.exists() and after["agent_id"] == before["agent_id"]
        assert after["agent_name"] == before["agent_name"] == "temporary-true-name"
        assert after["address"] == "new"

        new_venv = new / "runtime" / "venv"
        new_source = new / "runtime" / "source"
        init = json.loads((new / "init.json").read_text())
        assert init["venv_path"] == str(new_venv)
        assert init["mcp"]["local"]["command"] == str(new / "mcp" / "server")
        assert init["mcp"]["local"]["args"] == [str(old / "argument-must-stay-old")]
        assert init["mcp"]["local"]["env"] == {"UNCHANGED": str(old)}
        assert init["mcp"]["external"]["command"] == sys.executable
        assert init["mcp"]["remote"]["command"] == str(old / "http-command-must-stay-old")

        pth_lines = (new_venv / editable_pth.relative_to(venv)).read_text(
            encoding="utf-8"
        ).splitlines()
        assert pth_lines == [str(new_source), *(str(path) for path in dependency_paths)]
        moved_launcher = new_venv / "bin" / "lingtai-agent"
        assert moved_launcher.read_text(encoding="utf-8").splitlines()[0] == (
            f"#!{new_venv / 'bin' / 'python'}"
        )
        assert stat.S_IMODE(moved_launcher.stat().st_mode) == launcher_mode
        launcher_help = subprocess.run(
            [str(moved_launcher), "--help"], cwd=new, env=env,
            text=True, capture_output=True, timeout=10,
        )
        assert launcher_help.returncode == 0, launcher_help.stdout + launcher_help.stderr
        assert "usage:" in (launcher_help.stdout + launcher_help.stderr).lower()
        direct_data = json.loads(
            (new_venv / direct_url.relative_to(venv)).read_text(encoding="utf-8")
        )
        assert urllib.parse.unquote(urllib.parse.urlsplit(direct_data["url"]).path) == str(new_source)
        assert direct_data["dir_info"] == {"editable": True, "unchanged": str(old)}

        records = [
            json.loads(line)
            for line in (new / "mcp_registry.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert records[0]["command"] == str(new / "mcp" / "server")
        assert records[0]["args"] == [str(old / "registry-argument-must-stay-old")]
        assert records[0]["extra"] == {"unchanged": str(old)}
        assert records[1]["command"] == sys.executable
        assert records[2]["command"] == str(old / "registry-http-command-must-stay-old")
        assert stat.S_IMODE((new / "mcp_registry.jsonl").stat().st_mode) == registry_mode

        target_probe = subprocess.run(
            [str(new_venv / "bin" / "python"), "-c", "import lingtai; print(lingtai.__file__)"],
            cwd=new, env=env, text=True, capture_output=True, timeout=10, check=True,
        )
        assert Path(target_probe.stdout.strip()).is_relative_to(new_source)
        assert not (new / ".change-name-incomplete").exists()
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
