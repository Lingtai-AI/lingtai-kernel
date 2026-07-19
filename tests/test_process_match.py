"""Tests for LingTai agent process-command matching."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lingtai.kernel.process_match import match_agent_run


ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "src" / "lingtai" / "intrinsic_skills" / "lingtai-doctor" / "scripts" / "doctor.py"

MATCH_CASES = [
    ("/v/bin/python -m lingtai run /a/foo", "/a/foo", "module"),
    ("python -m lingtai run /a/foo", "/a/foo", "module"),
    ("/usr/local/bin/lingtai-agent run /a/foo", "/a/foo", "console"),
    ("lingtai-agent run /a/foo", "/a/foo", "console"),
    ("/usr/local/bin/lingtai run /a/foo", "/a/foo", "legacy"),
    ("lingtai run /a/foo", "/a/foo", "legacy"),
    ("/v/bin/python -m lingtai run /a/my agent", "/a/my agent", "module"),
    ("/usr/local/bin/lingtai-agent run /a/my agent", "/a/my agent", "console"),
    ("/v/bin/python -m lingtai run /a/foobar", "/a/foo", None),
    ("/usr/local/bin/lingtai-agent run /a/foobar", "/a/foo", None),
    ("/v/bin/python -m lingtai run /a/foo/", "/a/foo", "module"),
    ("/usr/local/bin/lingtai-agent run /a/foo/", "/a/foo", "console"),
    ("grep lingtai run /a/foo", "/a/foo", None),
    ("grep lingtai-agent run /a/foo", "/a/foo", None),
    ("tail -f /var/log/x lingtai run /a/foo", "/a/foo", None),
    ("vim /a/foo/notes about lingtai run", "/a/foo", None),
    ("/v/bin/python -m lingtai poll /a/foo", "/a/foo", None),
    # Windows-shaped command lines: the module form is what every runtime
    # relaunch path spawns, and backslash paths anchor the program forms.
    (r"C:\v\python.exe -m lingtai run C:\a\foo", r"C:\a\foo", "module"),
    (r"C:\v\Scripts\lingtai-agent run C:\a\foo", r"C:\a\foo", "console"),
    (r"C:\v\Scripts\lingtai run C:\a\foo", r"C:\a\foo", "legacy"),
    # Known residual limitation, pinned deliberately: the Windows console
    # script is `lingtai-agent.exe`, which the console token does not match.
    # Runtime-spawned processes always use the module form.
    (r"C:\v\Scripts\lingtai-agent.exe run C:\a\foo", r"C:\a\foo", None),
]


def _load_doctor_module():
    spec = importlib.util.spec_from_file_location("_lingtai_doctor_process_match", DOCTOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("cmdline", "working_dir", "expected"), MATCH_CASES)
def test_canonical_match_agent_run_matrix(cmdline, working_dir, expected):
    assert match_agent_run(cmdline, working_dir) == expected


def test_doctor_copy_matches_canonical_matrix():
    doctor = _load_doctor_module()
    for cmdline, working_dir, expected in MATCH_CASES:
        assert doctor.match_agent_run(cmdline, working_dir) == expected


def test_refresh_watcher_imports_canonical_match_agent_run(tmp_path):
    """The generated watcher program must import and use the canonical
    Core matcher (`lingtai.kernel.process_match.match_agent_run`) at
    runtime rather than embedding/maintaining a second local definition —
    the policy source of truth is `MATCH_CASES` above, exercised directly
    against the canonical function in `test_canonical_match_agent_run_matrix`.
    """
    from lingtai.kernel.refresh_watcher import RefreshWatcherRequest
    from lingtai.kernel.refresh_watcher.watcher_program import render_watcher_script

    request = RefreshWatcherRequest(
        taken_path="/wd/.refresh.taken",
        lock_path="/wd/.agent.lock",
        events_path="/wd/logs/events.jsonl",
        stderr_log="/wd/logs/refresh_relaunch.log",
        working_dir="/wd",
        cmd=("lingtai-agent", "run", "/wd"),
        agent_name="alice",
        address="wd",
    )
    script = render_watcher_script(request)

    assert "from lingtai.kernel.process_match import match_agent_run" in script
    assert "def match_agent_run" not in script


def test_cli_duplicate_process_detects_console_script(tmp_path):
    from lingtai.cli import _check_duplicate_process

    working_dir = tmp_path / "agent"
    working_dir.mkdir()

    ps_out = f"4242 /usr/local/bin/lingtai-agent run {working_dir.resolve()}\n"
    with patch("subprocess.check_output", return_value=ps_out):
        with pytest.raises(SystemExit):
            _check_duplicate_process(working_dir)


def test_cli_duplicate_process_rejects_argument_position_false_positive(tmp_path):
    from lingtai.cli import _check_duplicate_process

    working_dir = tmp_path / "agent"
    working_dir.mkdir()

    ps_out = f"4242 tail -f /var/log/x lingtai run {working_dir.resolve()}\n"
    with patch("subprocess.check_output", return_value=ps_out):
        _check_duplicate_process(working_dir)


def test_doctor_collect_process_detects_console_script(tmp_path):
    doctor = _load_doctor_module()

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    report = doctor.Report(agent_dir, None)
    stdout = f"4242 /usr/local/bin/lingtai-agent run {agent_dir}\n"

    with patch.object(
        doctor.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    ):
        doctor.collect_process(report)

    process_section = report.sections[-1]
    assert process_section.findings[0].severity == "OK"
    assert process_section.findings[0].title == "lingtai process found"


def test_doctor_collect_process_rejects_prefix_sibling(tmp_path):
    doctor = _load_doctor_module()

    agent_dir = tmp_path / "agent"
    sibling_dir = tmp_path / "agent_extra"
    agent_dir.mkdir()
    sibling_dir.mkdir()
    report = doctor.Report(agent_dir, None)
    stdout = f"4242 /usr/local/bin/lingtai-agent run {sibling_dir}\n"

    with patch.object(
        doctor.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    ):
        doctor.collect_process(report)

    process_section = report.sections[-1]
    assert process_section.findings[0].severity == "WARN"
    assert process_section.findings[0].title == "no lingtai process found"
