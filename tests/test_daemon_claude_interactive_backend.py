"""Tests for the interactive Claude daemon backend."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import textwrap
import threading
from unittest.mock import patch

import pytest

from lingtai.tools.daemon import get_schema
from lingtai.tools.daemon.claude_interactive import (
    ClaudeInteractiveBridge,
    ClaudeInteractiveError,
    run_claude_interactive,
)
from tests._daemon_helpers import (
    install_fake_detached_owner,
    make_daemon_agent,
    make_daemon_run_dir,
    wait_daemon_terminal,
)


def test_claude_interactive_bridge_import_does_not_require_posix_pty():
    script = """
import builtins
original_import = builtins.__import__
def import_without_pty(name, *args, **kwargs):
    if name == 'pty':
        raise ModuleNotFoundError('pty deliberately unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = import_without_pty
import lingtai.tools.daemon.claude_interactive
"""
    env = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (source_root, env.get("PYTHONPATH")) if path
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def _make_run_dir(tmp_path: Path, *, backend: str = "claude"):
    parent = tmp_path / "daemon-agent"
    return make_daemon_run_dir(
        parent_working_dir=parent,
        handle="em-1",
        task="interactive task",
        tools=[],
        model=backend,
        max_turns=30,
        timeout_s=30,
        parent_addr="daemon-agent",
        parent_pid=os.getpid(),
        system_prompt="[claude interactive backend]",
        backend=backend,
    )


class _NoSpawnTerminalPort:
    """Explicit injected Port used by tests whose boundary forbids spawn."""

    def __init__(self):
        self.spawn_calls = 0

    def spawn(self, command, *, group_id=None):
        self.spawn_calls += 1
        raise AssertionError("terminal spawn must not be reached")


def _make_test_terminal_port():
    """Use a real explicit POSIX Port without importing ``pty`` on Windows."""
    if os.name != "posix":
        pytest.skip("POSIX interactive terminal adapter tests")
    from lingtai.adapters.posix.interactive_terminal import (
        PosixInteractiveTerminalAdapter,
    )
    return PosixInteractiveTerminalAdapter()


def _write_fake_claude(bin_dir: Path, transcript_text: str = "fake interactive answer") -> Path:
    fake = bin_dir / "claude"
    fake.write_text(textwrap.dedent(f"""
        #!/usr/bin/env python3
        from __future__ import annotations
        import json
        from pathlib import Path
        import subprocess
        import sys
        import time

        args = sys.argv[1:]
        settings = None
        resume_session = None
        i = 0
        while i < len(args):
            if args[i] == "--settings":
                settings = json.loads(args[i + 1])
                i += 2
            elif args[i] == "--resume":
                resume_session = args[i + 1]
                i += 2
            else:
                i += 1
        if settings is None:
            raise SystemExit("missing --settings")

        def hook_command(event):
            for group in settings["hooks"][event]:
                for hook in group["hooks"]:
                    return hook["command"]
            raise SystemExit(f"missing hook {{event}}")

        session_id = resume_session or "claude-session-123"
        transcript = Path.cwd() / "fake-claude-transcript.jsonl"

        # Exercise the bridge's terminal probe responder.  The fake does not
        # need the responses; real Claude/Ink does.
        sys.stdout.buffer.write(b"\\x1b[c\\x1b[>c\\x1b[6n\\x1b[>q\\x1b[18t")
        sys.stdout.buffer.flush()

        start_payload = {{"session_id": session_id}}
        subprocess.run(
            hook_command("SessionStart"),
            input=json.dumps(start_payload),
            text=True,
            shell=True,
            check=True,
        )

        # Read the prompt pasted by the bridge.  It arrives as bracketed paste
        # plus CR; stop at CR/LF so the process can finish deterministically.
        got = bytearray()
        deadline = time.time() + 5
        while time.time() < deadline:
            ch = sys.stdin.buffer.read(1)
            if not ch:
                time.sleep(0.01)
                continue
            got += ch
            if ch in (b"\\r", b"\\n"):
                break
        if b"interactive task" not in got and b"follow-up message" not in got:
            raise SystemExit(f"prompt not received: {{got!r}}")

        with transcript.open("w", encoding="utf-8") as f:
            f.write(json.dumps({{"type": "custom-title", "customTitle": "em-1", "sessionId": session_id}}) + "\\n")
            f.write(json.dumps({{
                "type": "assistant",
                "session_id": session_id,
                "message": {{
                    "role": "assistant",
                    "content": [{{"type": "text", "text": {transcript_text!r}}}],
                }},
            }}) + "\\n")

        stop_payload = {{
            "session_id": session_id,
            "transcript_path": str(transcript),
            "last_assistant_message": {transcript_text!r},
        }}
        subprocess.run(
            hook_command("Stop"),
            input=json.dumps(stop_payload),
            text=True,
            shell=True,
            check=True,
        )
    """).lstrip(), encoding="utf-8")
    fake.chmod(0o755)
    return fake


def test_emanate_claude_dispatches_interactive_runner(tmp_path, monkeypatch):
    agent = make_daemon_agent(
        tmp_path, capabilities={"daemon": {"manager_pool_size": 0}}
    )
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    result = mgr.handle({
        "action": "emanate",
        "backend": "claude",
        "tasks": [{
            "task": "Use interactive Claude",
            "tools": [],
            "backend_options": {"model": "opus", "verbose": True},
        }],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    state = wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    manifest = records[0]["manifest"]
    assert manifest["backend"] == "claude"
    assert manifest["task"] == "Use interactive Claude"
    assert manifest["backend_argv"] == ["--model", "opus", "--verbose"]
    assert state["backend"] == "claude"
    assert state["backend_options"] == {"model": "opus", "verbose": True}
    assert "future" not in mgr._emanations[em_id]


@pytest.mark.parametrize(
    ("backend", "expected_runner"),
    [
        ("claude", "interactive"),
        ("claude-interactive", "interactive"),
        ("claude-p", "print"),
        ("claude-code", "print"),
    ],
)
def test_claude_backend_ids_are_preserved_while_sharing_runners(
    tmp_path, monkeypatch, backend, expected_runner,
):
    agent = make_daemon_agent(
        tmp_path, capabilities={"daemon": {"manager_pool_size": 0}}
    )
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    result = mgr.handle({
        "action": "emanate",
        "backend": backend,
        "tasks": [{"task": "Use Claude", "tools": []}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    state = wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    manifest = records[0]["manifest"]
    assert manifest["backend"] == backend
    assert state["backend"] == backend
    if expected_runner == "interactive":
        assert manifest["task"] == "Use Claude"
        assert "--mcp-config" not in manifest["backend_argv"]
    else:
        assert manifest["task"].endswith("Task:\nUse Claude")
        assert "call the MCP tool `finish`" in manifest["task"]
        assert "--mcp-config" in manifest["backend_argv"]
    assert "future" not in mgr._emanations[em_id]



def test_claude_reserved_backend_options_are_rejected(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    result = mgr.handle({
        "action": "emanate",
        "backend": "claude",
        "tasks": [{
            "task": "should not spawn",
            "tools": [],
            "backend_options": {"settings": "{}"},
        }],
    })

    assert result["status"] == "error"
    assert "--settings is reserved" in result["message"]
    assert mgr._emanations == {}


def test_claude_interactive_system_prompt_backend_option_is_rejected(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    result = mgr.handle({
        "action": "emanate",
        "backend": "claude",
        "tasks": [{
            "task": "should not spawn",
            "tools": [],
            "backend_options": {"append_system_prompt_file": "/tmp/override.md"},
        }],
    })

    assert result["status"] == "error"
    assert "--append-system-prompt-file is reserved" in result["message"]
    assert mgr._emanations == {}


def test_run_claude_interactive_fake_cli_hooks_and_transcript(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_claude(bin_dir, transcript_text="fake interactive answer")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    run_dir = _make_run_dir(tmp_path)
    result = run_claude_interactive(
        em_id="em-1",
        run_dir=run_dir,
        working_dir=tmp_path / "daemon-agent",
        task="interactive task",
        cancel_event=threading.Event(),
        env=os.environ.copy(),
        terminal_port=_make_test_terminal_port(),
    )

    assert result.final_text == "fake interactive answer"
    assert result.session_id == "claude-session-123"
    assert result.transcript_path is not None
    assert result.raw_pty_log_path is not None

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["claude_session_id"] == "claude-session-123"
    assert state["claude_interactive_transcript_path"] == result.transcript_path
    assert state["claude_interactive_prompt_sent"] is True
    assert Path(state["claude_interactive_raw_pty_log"]).exists()

    events = run_dir.events_path.read_text(encoding="utf-8")
    assert "fake interactive answer" in events
    assert "claude interactive SessionStart" in events
    assert "claude interactive Stop" in events


def test_missing_terminal_port_fails_before_workspace_or_spawn(tmp_path, monkeypatch):
    managed_root = tmp_path / "managed-claude"
    env = os.environ.copy()
    env["LINGTAI_CLAUDE_MANAGED_ROOT"] = str(managed_root)
    prep_calls = []
    port = _NoSpawnTerminalPort()

    def record_workspace(self):
        prep_calls.append("workspace")

    def record_harness(self):
        prep_calls.append("harness")
        return "{}"

    monkeypatch.setattr(
        ClaudeInteractiveBridge, "_prepare_managed_workspace", record_workspace
    )
    monkeypatch.setattr(ClaudeInteractiveBridge, "_prepare_harness", record_harness)
    if os.name == "posix":
        from lingtai.adapters.posix import interactive_terminal as posix_terminal
        monkeypatch.setattr(
            posix_terminal,
            "PosixInteractiveTerminalAdapter",
            lambda: port,
        )

    run_dir = _make_run_dir(tmp_path)
    with pytest.raises(
        ClaudeInteractiveError,
        match="requires an injected InteractiveTerminalPort",
    ):
        run_claude_interactive(
            em_id="em-1",
            run_dir=run_dir,
            working_dir=tmp_path / "daemon-agent",
            task="interactive task",
            cancel_event=threading.Event(),
            env=env,
            terminal_port=None,
        )

    assert prep_calls == []
    assert not managed_root.exists()
    assert port.spawn_calls == 0


def test_run_claude_interactive_rejects_invalid_managed_worktree_source(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text("#!/bin/sh\necho should-not-run >&2\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("LINGTAI_CLAUDE_MANAGED_ROOT", str(tmp_path / "managed-claude"))

    run_dir = _make_run_dir(tmp_path)
    with pytest.raises(ClaudeInteractiveError, match="managed-worktree-from must point inside a git repository"):
        run_claude_interactive(
            em_id="em-1",
            run_dir=run_dir,
            working_dir=tmp_path / "daemon-agent",
            task="interactive task",
            cancel_event=threading.Event(),
            backend_argv=["--managed-worktree-from", str(tmp_path / "not-a-repo")],
            env=os.environ.copy(),
            terminal_port=_NoSpawnTerminalPort(),
        )


def test_run_claude_interactive_checks_out_explicit_managed_worktree_source(tmp_path, monkeypatch):
    source = tmp_path / "source-repo"
    source.mkdir()
    subprocess.run(["git", "-C", str(source), "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Tester"], check=True)
    (source / "tracked.txt").write_text("from explicit source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "seed"], check=True, stdout=subprocess.PIPE)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_claude(bin_dir, transcript_text="explicit managed source answer")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("LINGTAI_CLAUDE_MANAGED_ROOT", str(tmp_path / "managed-claude"))

    run_dir = _make_run_dir(tmp_path)
    result = run_claude_interactive(
        em_id="em-1",
        run_dir=run_dir,
        working_dir=tmp_path / "daemon-agent",
        task="interactive task",
        cancel_event=threading.Event(),
        backend_argv=["--managed-worktree-from", str(source)],
        env=os.environ.copy(),
        terminal_port=_make_test_terminal_port(),
    )

    assert result.final_text == "explicit managed source answer"
    state = json.loads(run_dir.daemon_json_path.read_text())
    worktree = Path(state["claude_interactive_managed_worktree"])
    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "from explicit source\n"
    assert state["claude_interactive_managed_source"] == str(source)
    assert state["claude_interactive_managed_source_request"] == str(source)

def test_run_claude_interactive_auto_trusts_only_managed_workspace(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(r'''#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
settings = None
system_prompt_path = None
i = 0
while i < len(args):
    if args[i] == "--settings":
        settings = json.loads(args[i + 1])
        i += 2
    elif args[i] == "--append-system-prompt-file":
        system_prompt_path = Path(args[i + 1])
        i += 2
    else:
        i += 1
if settings is None:
    raise SystemExit("missing --settings")
if system_prompt_path is None or not system_prompt_path.exists():
    raise SystemExit("missing managed system prompt")
if "LingTai-managed ephemeral workspace" not in system_prompt_path.read_text():
    raise SystemExit("unexpected system prompt")

managed_root = Path(os.environ["LINGTAI_CLAUDE_MANAGED_ROOT"]).resolve()
cwd = Path.cwd().resolve()
if not cwd.is_relative_to(managed_root / "runs"):
    raise SystemExit(f"cwd not in managed root: {cwd}")
if cwd.name != "worktree":
    raise SystemExit(f"cwd is not managed worktree: {cwd}")

def hook_command(event):
    for group in settings["hooks"][event]:
        for hook in group["hooks"]:
            return hook["command"]
    raise SystemExit(f"missing hook {event}")

# Simulate Claude Code's workspace trust prompt. The bridge may answer
# this only because cwd is inside the LingTai-managed workspace root.
# Claude's Ink TUI may repaint the same prompt before consuming the answer;
# duplicate frames must not make LingTai fail as if this were an arbitrary cwd.
for _ in range(2):
    sys.stdout.write("Quick safety check: Is this a project you created or one you trust?\n")
    sys.stdout.write("1. Yes, I trust this folder\n2. No, exit\n")
    sys.stdout.flush()
    time.sleep(0.05)
answer = sys.stdin.buffer.read(2)
if answer not in (b"1\r", b"1\n"):
    raise SystemExit(f"trust answer not received: {answer!r}")

session_id = "managed-session-123"
transcript = Path.cwd() / "managed-transcript.jsonl"
subprocess.run(
    hook_command("SessionStart"),
    input=json.dumps({"session_id": session_id}),
    text=True,
    shell=True,
    check=True,
)

got = bytearray()
deadline = time.time() + 5
while time.time() < deadline:
    ch = sys.stdin.buffer.read(1)
    if not ch:
        time.sleep(0.01)
        continue
    got += ch
    if ch in (b"\r", b"\n"):
        break
if b"interactive task" not in got:
    raise SystemExit(f"prompt not received: {got!r}")

with transcript.open("w", encoding="utf-8") as f:
    f.write(json.dumps({
        "type": "assistant",
        "session_id": session_id,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "managed trust answer"}],
        },
    }) + "\n")
subprocess.run(
    hook_command("Stop"),
    input=json.dumps({
        "session_id": session_id,
        "transcript_path": str(transcript),
        "last_assistant_message": "managed trust answer",
    }),
    text=True,
    shell=True,
    check=True,
)
''', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("LINGTAI_CLAUDE_MANAGED_ROOT", str(tmp_path / "managed-claude"))

    run_dir = _make_run_dir(tmp_path)
    result = run_claude_interactive(
        em_id="em-1",
        run_dir=run_dir,
        working_dir=tmp_path / "daemon-agent",
        task="interactive task",
        cancel_event=threading.Event(),
        env=os.environ.copy(),
        terminal_port=_make_test_terminal_port(),
    )

    assert result.final_text == "managed trust answer"
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["claude_interactive_managed_trust_answered"] is True
    assert Path(state["claude_interactive_managed_worktree"]).exists()
    assert Path(state["claude_interactive_system_prompt"]).exists()
    assert Path(state["claude_interactive_raw_pty_log"]).is_relative_to(
        tmp_path / "managed-claude" / "runs"
    )
    events = run_dir.events_path.read_text(encoding="utf-8")
    assert "auto-selected workspace trust" in events


def test_first_run_theme_picker_counts_as_an_onboarding_prompt():
    """A fresh private CLAUDE_CONFIG_DIR (setup-token mode) opens Claude Code's
    first-run theme picker; the bridge reports it rather than waiting silently."""
    from lingtai.tools.daemon import claude_interactive as bridge

    frame = (
        b"Welcome to Claude Code\x1b[2CLet's get started.\x1b[1B"
        b"Choose\x1b[1Cthe\x1b[1Ctext\x1b[1Cstyle that looks best with your terminal"
    )
    text, normalized = bridge.ClaudeInteractiveBridge._normalized_prompt_text(None, frame)
    assert bridge.ClaudeInteractiveBridge._contains_marker(
        text, normalized, bridge._AUTH_OR_ONBOARDING_PROMPTS
    )


# ---------------------------------------------------------------------------
# Workspace-trust dialog answer: select "Yes … trust" by label, never a
# hardcoded key, and press nothing when it cannot be found.
# ---------------------------------------------------------------------------

# Captured from a real Claude Code 2.1.285 PTY (scratch config, no request):
# un-numbered options, the "❯" cursor on "No, exit", which is listed FIRST.
_TRUST_DIALOG_2_1_285 = (
    b"\x1b[2GQuick\x1b[8Gsafety\x1b[15Gcheck:\x1b[22GIs\x1b[25Gthis\x1b[30Ga"
    b"\x1b[32Gproject\x1b[40Gyou\x1b[44Gcreated\x1b[52Gor\x1b[55Gone\x1b[59Gyou"
    b"\x1b[63Gtrust?\x1b[70G(Like\x1b[76Gyour\r\r\n\x1b[2Gown\x1b[6Gcode,\x1b[12Ga"
    b"\x1b[14Gwell-known\x1b[25Gopen\x1b[30Gsource\x1b[37Gproject,\x1b[46Gor"
    b"\x1b[49Gwork\x1b[54Gfrom\x1b[59Gyour\x1b[64Gteam).\x1b[71GIf\x1b[74Gnot,\r\r\n"
    b"\x1b[2Gtake\x1b[7Ga\x1b[9Gmoment\x1b[16Gto\x1b[19Greview\x1b[26Gwhat's\x1b[33Gin"
    b"\x1b[36Gthis\x1b[41Gfolder\x1b[48Gfirst.\r\r\n\r\r\n\x1b[2GClaude\x1b[9GCode'll"
    b"\x1b[17Gbe\x1b[20Gable\x1b[25Gto\x1b[28Gread,\x1b[34Gedit,\x1b[40Gand"
    b"\x1b[44Gexecute\x1b[52Gfiles\x1b[58Ghere.\r\r\n\r\r\n\x1b[2G\x1b[38;5;246m"
    b"Security\x1b[11Gguide\x1b[39m\r\r\n\r\r\n\x1b[2G\x1b[38;5;153m\xe2\x9d\xaf"
    b"\x1b[4GNo,\x1b[8Gexit\x1b[39m\r\r\n\x1b[4GYes,\x1b[9GI\x1b[11Gtrust\x1b[17Gthis"
    b"\x1b[22Gfolder\r\r\n\r\r\n\x1b[2G\x1b[38;5;246mEnter\x1b[8Gto\x1b[11Gconfirm"
    b"\x1b[19G\xc2\xb7\x1b[21GEsc\x1b[25Gto\x1b[28Gcancel\x1b[39m\r\r\n"
).decode("utf-8")


def test_trust_answer_for_2_1_285_moves_down_to_yes_below_no_exit():
    from lingtai.tools.daemon.claude_interactive import (
        parse_trust_dialog_options,
        trust_dialog_answer,
        trust_dialog_is_complete,
    )

    options = parse_trust_dialog_options(_TRUST_DIALOG_2_1_285)
    assert [(o.label, o.highlighted, o.number) for o in options] == [
        ("No, exit", True, None),
        ("Yes, I trust this folder", False, None),
    ]
    assert trust_dialog_is_complete(_TRUST_DIALOG_2_1_285)
    # One Down from the highlighted "No, exit" — never "1", and no Enter yet.
    assert trust_dialog_answer(_TRUST_DIALOG_2_1_285) == b"\x1b[B"
    # A repaint re-renders the dialog; only the last render counts.
    assert trust_dialog_answer(_TRUST_DIALOG_2_1_285 * 2) == b"\x1b[B"
    # Once a later render shows the cursor on "Yes … trust", confirm.
    moved = _TRUST_DIALOG_2_1_285.replace(
        "\u276f\x1b[4GNo,", "\x1b[4GNo,"
    ).replace("\x1b[4GYes,", "\u276f\x1b[4GYes,")
    assert moved != _TRUST_DIALOG_2_1_285
    assert trust_dialog_answer(moved) == b"\r"


@pytest.mark.parametrize(
    "frame, expected",
    [
        # Older ordering: highlighted, numbered "Yes" first -> confirm in place.
        ("Do you trust the files in this folder?\n"
         "❯ 1. Yes, I trust this folder\n  2. No, exit\n", b"\r"),
        # Numbered without a rendered cursor -> press Yes's own number.
        ("Quick safety check: trust?\n1. Yes, I trust this folder\n2. No, exit\n", b"1\r"),
        ("Quick safety check: trust?\n1. No, exit\n2. Yes, I trust this folder\n", b"2\r"),
        # Cursor below Yes -> move up (confirmed on a later settled render).
        ("Quick safety check: trust?\n  Yes, I trust this folder\n❯ No, exit\n", b"\x1b[A"),
    ],
    ids=["yes-first-highlighted", "numbered-yes-first", "numbered-yes-second", "cursor-below"],
)
def test_trust_answer_old_orderings_select_yes_by_label(frame, expected):
    from lingtai.tools.daemon.claude_interactive import trust_dialog_answer

    assert trust_dialog_answer(frame) == expected


@pytest.mark.parametrize(
    "frame",
    [
        "Quick safety check: trust?\n❯ No, exit\n  Yes, proceed\nEnter to confirm · Esc to cancel\n",
        "Quick safety check: trust?\n❯ No, exit\nEnter to confirm · Esc to cancel\n",
        # "Yes … trust" exists but its position cannot be reached unambiguously.
        "Quick safety check: trust?\nNo, exit\nYes, I trust this folder\nEnter to confirm\n",
    ],
    ids=["yes-without-trust", "only-no", "no-cursor-no-number"],
)
def test_trust_answer_presses_nothing_without_a_selectable_yes_trust(frame):
    from lingtai.tools.daemon.claude_interactive import (
        trust_dialog_answer,
        trust_dialog_is_complete,
    )

    assert trust_dialog_answer(frame) is None
    assert trust_dialog_is_complete(frame)


class _RecordingTerminal:
    def __init__(self):
        self.writes = []

    def write(self, handle, data):
        self.writes.append(data)


def _managed_bridge(tmp_path):
    terminal = _RecordingTerminal()
    bridge = ClaudeInteractiveBridge(
        em_id="em-1", run_dir=_make_run_dir(tmp_path), working_dir=tmp_path,
        task="interactive task", cancel_event=threading.Event(),
        env={"LINGTAI_CLAUDE_MANAGED_ROOT": str(tmp_path / "managed")},
        terminal_port=terminal,
    )
    bridge._auto_trust_workspace = True  # set by _prepare_managed_workspace
    return bridge, terminal


def _settle(bridge):
    from lingtai.tools.daemon import claude_interactive as bridge_mod

    bridge._trust_output_at -= bridge_mod._TRUST_SETTLE_S + 0.1


def test_bridge_waits_for_a_settled_dialog_then_selects_yes_in_the_managed_workspace(tmp_path):
    bridge, terminal = _managed_bridge(tmp_path)
    handle = object()
    question, options = _TRUST_DIALOG_2_1_285.split("\x1b[2G\x1b[38;5;246mSecurity")
    bridge._handle_auth_or_trust_prompt(handle, question.encode("utf-8"))
    _settle(bridge)
    bridge._advance_managed_trust(handle)
    assert terminal.writes == [] and bridge._prompt_warning is None  # not rendered yet
    bridge._handle_auth_or_trust_prompt(
        handle, ("\x1b[2G\x1b[38;5;246mSecurity" + options).encode("utf-8")
    )
    bridge._advance_managed_trust(handle)
    assert terminal.writes == []  # not settled yet: the TUI may still reset
    _settle(bridge)
    bridge._advance_managed_trust(handle)
    assert terminal.writes == [b"\x1b[B"]  # move only
    # The TUI resets the selection to "No, exit" (seen on 2.1.285): move again.
    bridge._handle_auth_or_trust_prompt(handle, "❯ No, exit\n  Yes, I trust this folder\n".encode())
    _settle(bridge)
    bridge._advance_managed_trust(handle)
    assert terminal.writes == [b"\x1b[B", b"\x1b[B"]
    # A settled render with the cursor on "Yes … trust" -> confirm.
    bridge._handle_auth_or_trust_prompt(handle, "  No, exit\n❯ Yes, I trust this folder\n".encode())
    _settle(bridge)
    bridge._advance_managed_trust(handle)
    assert terminal.writes == [b"\x1b[B", b"\x1b[B", b"\r"]
    assert bridge._trust_prompt_answered is True
    # A repainted frame after the answer is ignored.
    bridge._handle_auth_or_trust_prompt(handle, _TRUST_DIALOG_2_1_285.encode("utf-8"))
    _settle(bridge)
    bridge._advance_managed_trust(handle)
    assert terminal.writes == [b"\x1b[B", b"\x1b[B", b"\r"]


def test_bridge_fails_clearly_and_presses_nothing_without_yes_trust(tmp_path):
    bridge, terminal = _managed_bridge(tmp_path)
    frame = "Quick safety check: do you trust?\n❯ No, exit\nEnter to confirm · Esc to cancel\n"
    bridge._handle_auth_or_trust_prompt(object(), frame.encode("utf-8"))
    _settle(bridge)
    bridge._advance_managed_trust(object())
    assert terminal.writes == []
    assert "No, exit" in bridge._prompt_warning
    assert bridge._trust_prompt_answered is False
