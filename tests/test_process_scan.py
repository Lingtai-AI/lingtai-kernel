"""Duplicate-launch process scan: Port adapters, selector, and CLI-host wiring."""
from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from lingtai.adapters.posix.process_scan import PosixAgentProcessScanAdapter
from lingtai.adapters.process_scan import select_agent_process_scan
from lingtai.adapters.windows.process_scan import WindowsAgentProcessScanAdapter
from lingtai.kernel.process_scan import AgentProcessScanPort


def test_port_is_a_single_best_effort_observation():
    assert AgentProcessScanPort.__abstractmethods__ == frozenset({"iter_process_commands"})
    assert issubclass(PosixAgentProcessScanAdapter, AgentProcessScanPort)
    assert issubclass(WindowsAgentProcessScanAdapter, AgentProcessScanPort)


def test_posix_adapter_parses_ps_rows(monkeypatch):
    rows = "\n".join(
        [
            "  42 /v/bin/python -m lingtai run /a/wd",
            "",
            "notanumber /bin/thing",
            "  7 ",
            "  99 sleep 60",
        ]
    )
    recorded: dict = {}

    def fake_check_output(argv, **kwargs):
        recorded["argv"] = argv
        return rows + "\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    observations = list(PosixAgentProcessScanAdapter().iter_process_commands())
    assert recorded["argv"] == ["ps", "-eo", "pid=,command="]
    assert observations == [
        (42, "/v/bin/python -m lingtai run /a/wd"),
        (99, "sleep 60"),
    ]


def test_posix_adapter_yields_nothing_when_ps_unavailable(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no ps")

    monkeypatch.setattr(subprocess, "check_output", boom)
    assert list(PosixAgentProcessScanAdapter().iter_process_commands()) == []


def test_windows_adapter_parses_cim_json(monkeypatch):
    import lingtai.adapters.windows.process_scan as module

    payload = (
        '[{"ProcessId": 42, "CommandLine": "C:\\\\py\\\\python.exe -m lingtai run C:\\\\a\\\\wd"},'
        '{"ProcessId": 7, "CommandLine": null},'
        '{"ProcessId": "bad", "CommandLine": "x"},'
        '{"ProcessId": 99, "CommandLine": "notepad.exe"}]'
    )
    recorded: dict = {}

    def fake_check_output(argv, **kwargs):
        recorded["argv"] = argv
        return payload

    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh" if name == "pwsh" else None)
    monkeypatch.setattr(module.subprocess, "check_output", fake_check_output)
    observations = list(WindowsAgentProcessScanAdapter().iter_process_commands())
    assert observations == [
        (42, "C:\\py\\python.exe -m lingtai run C:\\a\\wd"),
        (99, "notepad.exe"),
    ]
    assert recorded["argv"][0] == "pwsh"
    assert "Win32_Process" in recorded["argv"][-1]
    assert "ConvertTo-Json" in recorded["argv"][-1]


def test_windows_adapter_handles_single_object_and_failures(monkeypatch):
    import lingtai.adapters.windows.process_scan as module

    adapter = WindowsAgentProcessScanAdapter()

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    assert list(adapter.iter_process_commands()) == []

    monkeypatch.setattr(module.shutil, "which", lambda name: "powershell")
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda *a, **k: '{"ProcessId": 5, "CommandLine": "one.exe"}',
    )
    assert list(adapter.iter_process_commands()) == [(5, "one.exe")]

    monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **k: "{not json")
    assert list(adapter.iter_process_commands()) == []

    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "pwsh")),
    )
    assert list(adapter.iter_process_commands()) == []


def test_selector_returns_platform_adapter(monkeypatch):
    import lingtai.adapters.process_scan as selector

    if os.name == "posix":
        assert isinstance(select_agent_process_scan(), PosixAgentProcessScanAdapter)
    monkeypatch.setattr(selector.sys, "platform", "win32")
    assert isinstance(select_agent_process_scan(), WindowsAgentProcessScanAdapter)
    monkeypatch.setattr(selector.sys, "platform", "haiku1")
    monkeypatch.setattr(selector.os, "name", "java")
    assert select_agent_process_scan() is None


def test_cli_stop_signals_per_platform(monkeypatch):
    import lingtai.cli as cli

    if os.name == "posix":
        assert cli._stop_signal_numbers() == [signal.SIGTERM, signal.SIGINT]
    monkeypatch.setattr(os, "name", "nt")
    numbers = cli._stop_signal_numbers()
    assert numbers[0] == signal.SIGINT
    assert signal.SIGTERM not in numbers


def test_agent_detached_spawn_kwargs_per_platform(monkeypatch):
    import lingtai.agent as agent_module

    if os.name == "posix":
        assert agent_module._detached_spawn_kwargs() == {"start_new_session": True}
    monkeypatch.setattr(os, "name", "nt")
    kwargs = agent_module._detached_spawn_kwargs()
    from lingtai.adapters.windows._win32 import DETACHED_CREATIONFLAGS

    assert kwargs == {"creationflags": DETACHED_CREATIONFLAGS, "close_fds": True}


def test_windows_scan_feeds_cli_duplicate_guard(tmp_path, monkeypatch):
    """End-to-end wiring: a Windows-shaped CIM observation triggers the same
    refusal the POSIX ps row does, through the selector and canonical matcher."""
    import lingtai.adapters.process_scan as selector
    import lingtai.adapters.windows.process_scan as win_module
    from lingtai.cli import _check_duplicate_process

    import json

    working_dir = tmp_path / "agent"
    working_dir.mkdir()
    command = f"C:\\py\\python.exe -m lingtai run {working_dir.resolve()}"
    payload = json.dumps([{"ProcessId": 4242, "CommandLine": command}])

    monkeypatch.setattr(selector.sys, "platform", "win32")
    monkeypatch.setattr(win_module.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(win_module.subprocess, "check_output", lambda *a, **k: payload)
    with pytest.raises(SystemExit):
        _check_duplicate_process(working_dir)
