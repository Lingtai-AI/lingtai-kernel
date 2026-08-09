"""Focused tests for the claude-code ``--effort`` argv normalization.

Claude Code is the one provider with its own reasoning-control flag: the
kernel thinking level is normalized to ``--effort <level>`` argv exactly once
per session (``_claude_effort_argv``), frozen for the session's life, and
never duplicated against a caller-supplied ``--effort`` in ``extra_argv``.
"""

import json
from unittest.mock import patch

import pytest

from lingtai.llm.claude_code.adapter import (
    CLAUDE_EFFORT_LEVELS,
    ClaudeCodeAdapter,
    _claude_effort_argv,
)


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _envelope(result_str):
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_str,
            "session_id": "sess-123",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    )


def _run_claude(thinking="default", extra_argv=None):
    captured = {}
    adapter = ClaudeCodeAdapter(extra_argv=extra_argv)

    def fake_run(cmd, **kw):
        captured["cmd"] = list(cmd)
        return _FakeProc(stdout=_envelope('{"action": "final", "text": "ok"}'))

    with patch(
        "lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run
    ):
        session = adapter.create_chat("opus", "sys", thinking=thinking)
        session.send("hello")
    return captured["cmd"]


@pytest.mark.parametrize("thinking", ["low", "medium", "high", "xhigh", "max"])
def test_explicit_thinking_becomes_effort_flag(thinking):
    cmd = _run_claude(thinking=thinking)
    assert cmd.count("--effort") == 1
    assert cmd[cmd.index("--effort") + 1] == thinking


@pytest.mark.parametrize("thinking", [None, "default"])
def test_omitted_thinking_sends_no_effort_flag(thinking):
    """The omission sentinel emits no ``--effort`` flag; the CLI's own default
    applies and the command stays byte-identical to pre-effort behavior."""
    assert "--effort" not in _run_claude(thinking=thinking)


def test_create_chat_without_thinking_sends_no_effort_flag():
    assert "--effort" not in _run_claude()


@pytest.mark.parametrize("bad", ["ultra", "", 42, "HIGH", "minimal", "none"])
def test_out_of_vocabulary_thinking_raises(bad):
    with pytest.raises(ValueError, match="claude-code effort"):
        _run_claude(thinking=bad)


def test_caller_effort_in_extra_argv_wins_no_duplicate():
    """A caller-supplied ``--effort`` via ``extra_argv`` suppresses the
    session's normalized flag: never two ``--effort`` on one command."""
    cmd = _run_claude(thinking="high", extra_argv=["--effort", "low"])
    assert cmd.count("--effort") == 1
    assert cmd[cmd.index("--effort") + 1] == "low"


def test_effort_flag_is_frozen_across_turns_and_resume():
    captured = []
    adapter = ClaudeCodeAdapter()

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        return _FakeProc(stdout=_envelope('{"action": "final", "text": "ok"}'))

    with patch(
        "lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run
    ):
        session = adapter.create_chat("opus", "sys", thinking="xhigh")
        session.send("hello")
        session.send("hello again")
    assert len(captured) == 2
    for cmd in captured:
        assert cmd.count("--effort") == 1
        assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_generate_omits_effort_flag():
    """The one-shot path has no session contract: exactly the pre-change
    command, with no ``--effort`` flag."""
    adapter = ClaudeCodeAdapter()
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = list(cmd)
        return _FakeProc(stdout=_envelope("plain reply"))

    with patch(
        "lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run
    ):
        adapter.generate("opus", "hello")
    assert "--effort" not in captured["cmd"]


def test_effort_vocab_is_provider_local():
    """Claude has no ``none``/``minimal`` (the Responses vocabulary does) and
    Responses has no ``max``; the local tuple is the single source of truth
    for both the acceptance gate and the emitted argv."""
    assert CLAUDE_EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")
    for level in CLAUDE_EFFORT_LEVELS:
        assert _claude_effort_argv(level) == ["--effort", level]
