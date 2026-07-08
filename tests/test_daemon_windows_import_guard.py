"""Native-Windows import/load guards for the daemon capability.

These pin the minimal Windows-portability contract:

* importing ``lingtai.core.daemon`` must NOT eagerly import the POSIX-only
  ``lingtai.core.daemon.claude_interactive`` module (which imports ``pty``);
* on native Windows, ``backend="lingtai"`` stays available while CLI/PTY
  backends reject early with a clear ConPTY/pywinpty message rather than
  silently falling back;
* the runtime process-group cleanup helper must not raise ``AttributeError``
  from ``os.killpg`` when it is absent (native Windows).

No real Windows and no real subprocesses are required.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lingtai.core import daemon
from lingtai.core.daemon import runtime
from tests._daemon_helpers import make_daemon_agent


def test_importing_daemon_does_not_import_claude_interactive():
    # Regression: ``daemon/__init__.py`` used to ``from .claude_interactive
    # import ...`` at module-import time, so merely importing the daemon
    # capability pulled in the POSIX-only ``pty`` module. Run in a clean
    # subprocess so a prior import in this session can't mask the regression.
    #
    # Pin the subprocess to the SAME ``lingtai`` package this test session
    # loaded (pytest's ``pythonpath=["src"]`` makes that the worktree under
    # test, which may differ from an editable install pointing elsewhere).
    import lingtai
    src_dir = str(Path(lingtai.__file__).resolve().parents[1])
    code = textwrap.dedent(
        """
        import sys
        import lingtai.core.daemon  # noqa: F401
        loaded = "lingtai.core.daemon.claude_interactive" in sys.modules
        print("LOADED" if loaded else "NOT_LOADED")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": src_dir + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert proc.returncode == 0, proc.stderr
    # Confirm the subprocess loaded the package under test, not an unrelated
    # editable install, so the assertion below is meaningful.
    which = subprocess.run(
        [sys.executable, "-c", "import lingtai; print(lingtai.__file__)"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": src_dir + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert src_dir in which.stdout, which.stdout + which.stderr
    assert proc.stdout.strip() == "NOT_LOADED", proc.stdout + proc.stderr


@pytest.mark.skipif(os.name == "nt", reason="legacy interactive Claude backend is POSIX-only until ConPTY/pywinpty exists")
def test_lazy_interactive_import_still_resolves_symbols():
    # The lazy accessor returns the same two names the module used to import
    # at the top level, so the interactive backend paths keep working on POSIX.
    error_cls, run_fn = daemon._lazy_claude_interactive()
    assert error_cls.__name__ == "ClaudeInteractiveError"
    assert run_fn.__name__ == "run_claude_interactive"


def test_native_windows_predicate_reads_os_name(monkeypatch):
    monkeypatch.setattr(daemon.os, "name", "nt")
    assert daemon._native_windows() is True
    monkeypatch.setattr(daemon.os, "name", "posix")
    assert daemon._native_windows() is False


def test_emanate_cli_backend_rejected_on_native_windows(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    monkeypatch.setattr(daemon, "_native_windows", lambda: True)

    result = mgr.handle({
        "action": "emanate",
        "backend": "claude-p",
        "tasks": [{"task": "should not spawn on windows", "tools": []}],
    })

    assert result["status"] == "error"
    msg = result["message"].lower()
    assert "windows" in msg
    assert "wsl" in msg
    # Names the missing runtime slice honestly rather than faking support.
    assert "conpty" in msg or "pywinpty" in msg
    # No emanation was scheduled.
    assert mgr._emanations == {}


def test_ask_cli_backend_rejected_on_native_windows(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    monkeypatch.setattr(daemon, "_native_windows", lambda: True)
    mgr._emanations["em-win-cli"] = {"backend": "claude-p"}

    result = mgr._handle_ask("em-win-cli", "hello from windows")

    assert result["status"] == "error"
    assert result["id"] == "em-win-cli"
    msg = result["message"].lower()
    assert "windows" in msg
    assert "conpty" in msg or "pywinpty" in msg



def test_emanate_lingtai_backend_not_rejected_by_windows_guard(tmp_path, monkeypatch):
    # The lingtai (in-process, non-CLI) backend stays available on native
    # Windows: the CLI guard must not touch it. We patch the LingTai worker so
    # the test doesn't need a real LLM — the point is that the Windows guard
    # does not short-circuit the lingtai path with an error.
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    monkeypatch.setattr(daemon, "_native_windows", lambda: True)

    def fake_run(*args, **kwargs):
        return "ok"

    monkeypatch.setattr(mgr, "_run_emanation", fake_run)

    result = mgr.handle({
        "action": "emanate",
        "backend": "lingtai",
        "tasks": [{"task": "lingtai backend on windows", "tools": []}],
    })

    # Whatever the lingtai path does, it is NOT the Windows CLI rejection.
    if result["status"] == "error":
        assert "windows" not in result["message"].lower()


@pytest.mark.parametrize(
    "backend",
    ["claude", "claude-interactive", "claude-p", "claude-code", "codex",
     "opencode", "mimo", "qwen", "omp", "kimi", "cursor"],
)
def test_all_cli_backends_and_aliases_reject_on_windows(tmp_path, monkeypatch, backend):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    monkeypatch.setattr(daemon, "_native_windows", lambda: True)

    result = mgr.handle({
        "action": "emanate",
        "backend": backend,
        "tasks": [{"task": "no cli on native windows", "tools": []}],
    })

    assert result["status"] == "error"
    assert "windows" in result["message"].lower()
    assert mgr._emanations == {}


def test_kill_process_group_windows_fallback_uses_terminate(monkeypatch):
    # On native Windows there is no os.killpg. The helper must fall back to
    # proc.terminate() -> wait -> proc.kill() without raising AttributeError,
    # and must NOT claim process-tree teardown.
    monkeypatch.setattr(runtime, "_supports_killpg", lambda: False)

    class _FakeProc:
        pid = 4321

        def __init__(self):
            self.calls: list[str] = []
            self._wait_calls = 0

        def terminate(self):
            self.calls.append("terminate")

        def kill(self):
            self.calls.append("kill")

        def wait(self, timeout):
            self._wait_calls += 1
            self.calls.append(f"wait({timeout})")
            if self._wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return 0

    proc = _FakeProc()
    runtime.kill_process_group(proc, term_timeout=2.0, kill_timeout=1.0)

    assert proc.calls == ["terminate", "wait(2.0)", "kill", "wait(1.0)"]


def test_kill_process_group_posix_still_uses_killpg(monkeypatch):
    # POSIX behavior is unchanged: process-group signals via os.killpg.
    monkeypatch.setattr(runtime, "_supports_killpg", lambda: True)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(runtime.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)),
                        raising=False)

    class _ExitsProc:
        pid = 7777

        def wait(self, timeout):
            return 0

    runtime.kill_process_group(_ExitsProc())
    assert signals == [(7777, runtime.signal.SIGTERM)]
