import os
import sys
from pathlib import Path

from lingtai import cli


class _Flag:
    def __init__(self):
        self.was_set = False

    def set(self):
        self.was_set = True


class _Shutdown:
    def wait(self):
        return None


class _FakeAgent:
    def __init__(self):
        self._asleep = _Flag()
        self._shutdown = _Shutdown()
        self._state = None
        self._venv_path = None
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout=10.0):
        self.stopped = True


def test_run_exports_live_runtime_python_env(monkeypatch, tmp_path):
    runtime_venv = tmp_path / "runtime-venv"
    agent = _FakeAgent()

    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr(cli, "_clean_signal_files", lambda working_dir: None)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda working_dir, agent: None)
    monkeypatch.setattr(cli, "load_init", lambda working_dir: {})
    monkeypatch.setattr(cli, "build_agent", lambda data, working_dir: agent)

    import lingtai.venv_resolve as venv_resolve

    monkeypatch.setattr(venv_resolve, "resolve_venv", lambda data: runtime_venv)
    monkeypatch.setenv("LINGTAI_RUNTIME_PYTHON", "stale-python")
    monkeypatch.setenv("LINGTAI_RUNTIME_VENV", "stale-venv")

    cli.run(tmp_path)

    assert os.environ["LINGTAI_RUNTIME_PYTHON"] == sys.executable
    assert os.environ["LINGTAI_RUNTIME_VENV"] == str(runtime_venv)
    assert agent._venv_path == str(runtime_venv)
    assert agent._asleep.was_set
    assert agent.started
    assert agent.stopped
    assert (tmp_path / "init.json").is_file()
