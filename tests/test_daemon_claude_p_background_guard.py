"""Guard against claude-p daemons exiting while 'waiting' for a background job.

The claude-p backend is one-shot ``claude --print``: there is no interactive
session, no stdin, and no ``<task-notification>`` re-prompt. When the inner
model backgrounds a job (``run_in_background``/``&``/wait-loop) and yields its
turn expecting a completion notification, the process exits cleanly and the
daemon used to mark the run ``done`` even though validation/commit/push were
never finished. These tests pin the completion guard that refuses that false
``done`` and the prevention warning in the CLI oneshot preamble.

See reports/daemon-background-wait-root-cause-20260630.md.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from lingtai.core.daemon import _looks_like_background_wait_exit
from tests._daemon_helpers import (
    FiniteFakeProc,
    make_daemon_agent,
    make_daemon_run_dir,
)


# Real terminal final-message strings from the root-cause corpus (§3).
BACKGROUND_WAIT_FINALS = [
    "Both background jobs will notify me. Let me wait.",
    (
        "I'll stop polling and wait for the background task's completion "
        "notification, which arrives automatically. Standing by for the full "
        "suite result before committing."
    ),
    "Both background tasks will notify me on completion. I'll wait.",
    "The background full-suite task will notify me when it completes.",
    "I'll wait for the pytest completion notification.",
    "Waiting for the full suite to complete. The background task will "
    "re-invoke me on completion.",
]

# Normal successful / benign final messages must NOT trip the guard.
NORMAL_FINALS = [
    "Done. All 412 tests pass; committed 3e29cea and pushed the branch.",
    "Validation complete: pytest green, branch pushed, PR ready.",
    "I ran the suite synchronously with a 600s timeout and it passed.",
    "Fixed the bug, added a regression test, and committed locally.",
    "[no output]",
    "",
    "Summary: refactored the parser; no background jobs were used.",
]


@pytest.mark.parametrize("final", BACKGROUND_WAIT_FINALS)
def test_detector_flags_background_wait_finals(final):
    assert _looks_like_background_wait_exit(final) is True


@pytest.mark.parametrize("final", NORMAL_FINALS)
def test_detector_passes_normal_finals(final):
    assert _looks_like_background_wait_exit(final) is False


def test_detector_anchors_on_final_message_not_mid_stream():
    # A run that *mentions* notifications mid-thought but ends by actually
    # reporting completion must pass — the guard keys on the wait intent in
    # the final result, not on the word "notify" appearing anywhere.
    final = (
        "Earlier I considered waiting for a background notification, but "
        "instead I re-ran the suite synchronously. It passed and I committed "
        "and pushed. Done."
    )
    assert _looks_like_background_wait_exit(final) is False


def _drive_print_runner(mgr, run_dir, em_id, final_text, monkeypatch):
    """Drive the real ``_run_claude_code_emanation`` over a fake stream-json proc.

    The fake proc emits one assistant text block then a ``result`` event whose
    ``result`` is *final_text*, exits 0, and never spawns a real process.
    """
    stdout_lines = [
        json.dumps({"type": "system", "session_id": "sess-1"}),
        json.dumps({
            "type": "assistant",
            "session_id": "sess-1",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "working..."}]},
        }),
        json.dumps({
            "type": "result",
            "session_id": "sess-1",
            "result": final_text,
            "is_error": False,
        }),
    ]
    fake = FiniteFakeProc(
        stdout_lines=[line + "\n" for line in stdout_lines],
        stderr_lines=[],
        returncode=0,
        pid=4321,
    )
    import lingtai.core.daemon as daemon_mod

    monkeypatch.setattr(
        daemon_mod.subprocess, "Popen", lambda *a, **k: fake,
    )
    return mgr._run_claude_code_emanation(
        em_id,
        run_dir,
        "do the task",
        threading.Event(),
        threading.Event(),
    )


def test_print_runner_refuses_done_on_background_wait(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent=agent, handle="em-1", backend="claude-p")

    with pytest.raises(RuntimeError):
        _drive_print_runner(
            mgr, run_dir, "em-1",
            "Both background jobs will notify me. Let me wait.",
            monkeypatch,
        )

    data = json.loads((run_dir.path / "daemon.json").read_text())
    assert data["state"] == "failed"
    # The full final text is preserved for the parent to inspect.
    assert "background" in (run_dir.path / "result.txt").read_text().lower()


def test_print_runner_marks_done_on_normal_success(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent=agent, handle="em-2", backend="claude-p")

    result = _drive_print_runner(
        mgr, run_dir, "em-2",
        "Done. Suite green, committed and pushed.",
        monkeypatch,
    )

    assert result == "Done. Suite green, committed and pushed."
    data = json.loads((run_dir.path / "daemon.json").read_text())
    assert data["state"] == "done"


def test_compose_cli_task_warns_print_backends_about_background_jobs():
    from lingtai.core.daemon import DaemonManager

    composed = DaemonManager._compose_cli_task(
        "Run the suite and push.", None, backend="claude-p",
    )
    low = composed.lower()
    assert "Run the suite and push." in composed
    assert "background" in low
    assert "synchron" in low  # "synchronously"


def test_compose_cli_task_no_warning_for_non_print_backends():
    from lingtai.core.daemon import DaemonManager

    composed = DaemonManager._compose_cli_task(
        "Run the suite and push.", None, backend="codex",
    )
    # Non-Claude-print backends do not get the claude-print-specific warning.
    assert composed == "Run the suite and push."
