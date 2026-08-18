"""Tests for daemon CLI backend free-form options (`backend_options`).

Covers:
- The pure argv conversion helper (`_backend_options_to_argv`).
- Per-task backend_options validation in `_handle_emanate_cli`.
- CLI runners (`_run_claude_code_emanation`, `_run_codex_emanation`,
  `_run_mimocode_emanation`, `_run_qwen_code_emanation`) appending
  backend_argv between required flags and the task prompt.
- Persistence: resolved options land in daemon.json.
- The lingtai backend ignoring the field (no schema breakage).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingtai.tools.daemon import (
    _BACKEND_ALIASES,
    _backend_options_to_argv,
    _backend_options_to_argv_and_env,
    _BACKEND_SCHEMA_ENUM,
    _BACKEND_SPECS,
    _cli_backend_loads_common_mcp as _source_cli_backend_loads_common_mcp,
    _normalize_backend,
)
from lingtai.tools.daemon.process_port import DaemonProcessExit
from lingtai.tools.daemon.run_dir import DaemonRunDir
from tests._daemon_helpers import (
    FiniteFakeProc,
    completed_future,
    install_fake_detached_owner,
    make_daemon_agent,
    make_daemon_run_dir,
    register_daemon_entry,
    wait_daemon_terminal,
)


class _OneShotRecordingPort:
    """Small injected Port double for the two raw one-shot families."""

    def __init__(self, lines=("output\n",), *, exit_receipt=None):
        self.commands = []
        self.deadlines = []
        self.waited = []
        self.terminated = []
        self.released = []
        self._lines = list(lines)
        self._exit_receipt = exit_receipt or DaemonProcessExit(0)
        self.handle = object()

    def spawn(self, command, *, group_id=None):
        self.commands.append((command, group_id))
        return self.handle

    def iter_stdout(self, handle, *, deadline=None):
        assert handle is self.handle
        self.deadlines.append(deadline)
        return iter(self._lines)

    def drain_stderr(self, handle, *, on_line=None, thread_name="daemon-stderr"):
        class Drain:
            lines = []

            def join(self, timeout=2.0):
                return None

        return Drain()

    def wait(self, handle, *, timeout=None):
        assert handle is self.handle
        self.waited.append(handle)
        return self._exit_receipt

    def terminate(self, handle, *, reason=None):
        assert handle is self.handle
        self.terminated.append((handle, reason))
        return DaemonProcessExit(-15, reason)

    def release(self, handle):
        assert handle is self.handle
        self.released.append(handle)
        return True


# ---------------------------------------------------------------------------
# Pure helper: _backend_options_to_argv
# ---------------------------------------------------------------------------


def test_argv_none_and_empty_return_empty():
    assert _backend_options_to_argv(None) == []
    assert _backend_options_to_argv({}) == []


def test_argv_bool_true_emits_flag_only():
    assert _backend_options_to_argv({"search": True}) == ["--search"]


def test_argv_bool_false_and_null_are_omitted():
    assert _backend_options_to_argv({"search": False, "verbose": None}) == []


def test_argv_string_int_float():
    out = _backend_options_to_argv({"config": "model_reasoning_effort=ultra"})
    assert out == ["--config", "model_reasoning_effort=ultra"]

    out = _backend_options_to_argv({"model": "gpt-5"})
    assert out == ["--model", "gpt-5"]

    out = _backend_options_to_argv({"retries": 3})
    assert out == ["--retries", "3"]

    out = _backend_options_to_argv({"temperature": 0.5})
    assert out == ["--temperature", "0.5"]


def test_argv_list_repeats_flag():
    out = _backend_options_to_argv({"include": ["src", "tests"]})
    assert out == ["--include", "src", "--include", "tests"]


def test_argv_underscore_key_becomes_dash():
    out = _backend_options_to_argv({"output_format": "json"})
    assert out == ["--output-format", "json"]


def test_argv_mixed_options_preserve_key_order():
    out = _backend_options_to_argv({
        "model": "claude-opus-4-7",
        "effort": "high",
        "search": True,
    })
    # dict iteration is insertion-ordered in Python 3.7+
    assert out == [
        "--model", "claude-opus-4-7",
        "--effort", "high",
        "--search",
    ]


def test_argv_rejects_leading_dash_key():
    with pytest.raises(ValueError, match="safe CLI flag name"):
        _backend_options_to_argv({"-model": "x"})


def test_argv_rejects_empty_key():
    with pytest.raises(ValueError, match="safe CLI flag name"):
        _backend_options_to_argv({"": "x"})


def test_argv_rejects_space_in_key():
    with pytest.raises(ValueError, match="safe CLI flag name"):
        _backend_options_to_argv({"output format": "json"})


def test_argv_rejects_shell_metachar_in_key():
    with pytest.raises(ValueError, match="safe CLI flag name"):
        _backend_options_to_argv({"model;rm -rf": "x"})


def test_argv_rejects_nested_object_value():
    with pytest.raises(ValueError, match="unsupported value type"):
        _backend_options_to_argv({"config": {"nested": True}})


def test_argv_rejects_list_with_nested_object():
    with pytest.raises(ValueError, match="list items must be"):
        _backend_options_to_argv({"include": [{"path": "src"}]})


def test_argv_rejects_list_with_bool_item():
    with pytest.raises(ValueError, match="list items must be"):
        _backend_options_to_argv({"flags": [True, False]})


def test_argv_rejects_non_dict_root():
    with pytest.raises(ValueError, match="must be a JSON object"):
        _backend_options_to_argv("--search")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reserved `env` key: carved out of argv, returned as a spawn-env overlay
# ---------------------------------------------------------------------------


def test_env_key_is_carved_out_of_argv_and_returned_as_overlay():
    argv, env = _backend_options_to_argv_and_env({
        "env": {"CLAUDE_CONFIG_DIR": "/tmp/profile/config"},
        "model": "opus",
    })
    assert argv == ["--model", "opus"]
    assert env == {"CLAUDE_CONFIG_DIR": "/tmp/profile/config"}
    # The reserved key must never leak into the flag conversion.
    assert "--env" not in argv


def test_env_key_absent_returns_empty_overlay():
    assert _backend_options_to_argv_and_env(None) == ([], {})
    assert _backend_options_to_argv_and_env({"model": "opus"}) == (
        ["--model", "opus"], {},
    )


def test_argv_only_wrapper_drops_the_env_overlay():
    """The legacy helper keeps its argv-only contract; `env` emits no flag."""
    assert _backend_options_to_argv(
        {"env": {"CLAUDE_CONFIG_DIR": "/tmp/p"}, "search": True}
    ) == ["--search"]


def test_env_rejects_non_dict_value():
    with pytest.raises(ValueError, match="must be a JSON object"):
        _backend_options_to_argv_and_env({"env": "CLAUDE_CONFIG_DIR=/tmp/p"})


def test_env_rejects_invalid_variable_name():
    with pytest.raises(ValueError, match="valid environment variable name"):
        _backend_options_to_argv_and_env({"env": {"9BAD": "x"}})
    with pytest.raises(ValueError, match="valid environment variable name"):
        _backend_options_to_argv_and_env({"env": {"HAS-DASH": "x"}})


def test_env_rejects_non_string_value():
    with pytest.raises(ValueError, match="must be a string"):
        _backend_options_to_argv_and_env({"env": {"RETRIES": 3}})
    with pytest.raises(ValueError, match="must be a string"):
        _backend_options_to_argv_and_env({"env": {"NESTED": {"a": "b"}}})


# ---------------------------------------------------------------------------
# Integration: _handle_emanate_cli validation + persistence
# ---------------------------------------------------------------------------


def test_emanate_cli_rejects_bad_backend_options(tmp_path):
    """A single invalid backend_options spec refuses the whole batch
    with a tool-level error mentioning the offending index."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    result = mgr.handle({
        "action": "emanate",
        "backend": "claude-code",
        "tasks": [
            {"task": "ok task", "tools": [], "backend_options": {"effort": "high"}},
            {"task": "bad task", "tools": [], "backend_options": {"-model": "x"}},
        ],
    })
    assert result["status"] == "error"
    assert "tasks[1].backend_options" in result["message"]
    # Nothing was scheduled
    assert mgr._emanations == {}


def test_emanate_cli_persists_resolved_options(tmp_path, monkeypatch):
    """Detached manifest persists and hands resolved user/harness argv to its owner."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate",
        "backend": "claude-code",
        "tasks": [{
            "task": "Refactor auth.",
            "tools": [],
            "backend_options": {"config": "model_reasoning_effort=ultra"},
        }],
    })
    assert result["status"] == "dispatched"
    assert "future" not in mgr._emanations[result["ids"][0]]
    state = wait_daemon_terminal(mgr._emanations[result["ids"][0]]["run_dir"])

    user_argv = [
        "--config", "model_reasoning_effort=ultra",
    ]
    manifest = records[0]["manifest"]
    assert manifest["backend_argv"][:len(user_argv)] == user_argv
    assert "--mcp-config" in manifest["backend_argv"]
    assert "--strict-mcp-config" in manifest["backend_argv"]
    assert state["backend"] == "claude-code"
    assert state["backend_options"] == {
        "config": "model_reasoning_effort=ultra",
    }
    assert state["backend_argv"] == user_argv
    assert "--mcp-config" in state["backend_harness_argv"]
    assert "--strict-mcp-config" in state["backend_harness_argv"]
    events = [json.loads(line) for line in records[0]["run_dir"].events_path.read_text().splitlines()]
    event = [e for e in events if e.get("event") == "test_detached_backend_invocation"]
    assert event[0]["argv"] == manifest["backend_argv"]


def test_emanate_cli_no_options_omits_fields(tmp_path, monkeypatch):
    """No backend_options omits user fields in durable detached state."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "claude-code",
        "tasks": [{"task": "no options", "tools": []}],
    })
    assert result["status"] == "dispatched"
    assert result["handoff"] == (
        "While waiting, go idle or call system(action='sleep'); the terminal result "
        "will arrive and wake you as a notification; read daemon-manual and "
        "notification-manual for details. If Telegram is connected and a Task Card "
        "is available for the current turn, use it to report progress; call "
        "`telegram(action='manual')` and follow its `Programmable Task Card` "
        "section for details."
    )
    state = wait_daemon_terminal(mgr._emanations[result["ids"][0]]["run_dir"])

    assert "--mcp-config" in records[0]["manifest"]["backend_argv"]
    assert "--strict-mcp-config" in records[0]["manifest"]["backend_argv"]
    assert "backend_options" not in state
    assert "backend_argv" not in state
    assert "--mcp-config" in state["backend_harness_argv"]
    assert "--strict-mcp-config" in state["backend_harness_argv"]


def test_emanate_cli_persists_and_hands_off_env_overlay(tmp_path, monkeypatch):
    """The `env` overlay survives to durable state and reaches the detached
    owner through the one-shot capsule — never as an argv token."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate",
        "backend": "claude-p",
        "tasks": [{
            "task": "Refactor auth.",
            "tools": [],
            "backend_options": {
                "env": {"CLAUDE_CONFIG_DIR": "/tmp/profile/config"},
                "model": "opus",
            },
        }],
    })
    assert result["status"] == "dispatched"
    state = wait_daemon_terminal(mgr._emanations[result["ids"][0]]["run_dir"])

    # The env overlay is a spawn-time value, not a flag.
    manifest = records[0]["manifest"]
    assert manifest["backend_argv"][:2] == ["--model", "opus"]
    assert "--env" not in manifest["backend_argv"]
    assert "/tmp/profile/config" not in manifest["backend_argv"]
    assert records[0]["capsule"]["backend_env"] == {
        "CLAUDE_CONFIG_DIR": "/tmp/profile/config",
    }
    # Durable state retains the resolved options including the `env` key. Its
    # values stay redacted there — every durable `env` container is redacted,
    # and this one is no exception.
    assert state["backend_options"] == {
        "env": {"CLAUDE_CONFIG_DIR": "<redacted>"},
        "model": "opus",
    }
    assert state["backend_argv"] == ["--model", "opus"]
    # call_parameters goes through the stricter run-record pass (every scalar
    # under backend_options is redacted there already); the `env` key and its
    # variable names still survive as structure.
    call_options = state["call_parameters"]["backend_options"]
    assert set(call_options) == {"env", "model"}
    assert set(call_options["env"]) == {"CLAUDE_CONFIG_DIR"}


def test_emanate_cli_rejects_bad_env_overlay(tmp_path):
    """A malformed `env` object refuses the whole batch before any spawn."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    result = mgr.handle({
        "action": "emanate",
        "backend": "claude-p",
        "tasks": [{
            "task": "bad env",
            "tools": [],
            "backend_options": {"env": {"CLAUDE_CONFIG_DIR": 7}},
        }],
    })
    assert result["status"] == "error"
    assert "tasks[0].backend_options" in result["message"]
    assert "must be a string" in result["message"]
    assert mgr._emanations == {}


def test_lingtai_backend_ignores_env_overlay(tmp_path):
    """The lingtai backend spawns no CLI, so an `env` overlay is ignored
    rather than validated — matching today's backend_options behavior."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "task done"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_resp)
    agent.service.create_session = MagicMock(return_value=mock_session)

    result = mgr.handle({
        "action": "emanate",
        "tasks": [{
            "task": "lingtai task",
            "tools": ["file"],
            # Invalid for a CLI backend; the lingtai backend never reads it.
            "backend_options": {"env": {"9BAD": 7}},
        }],
    })
    assert result["status"] == "dispatched"


def test_lingtai_backend_ignores_backend_options(tmp_path):
    """The lingtai backend has no CLI process — backend_options must be
    silently ignored, never raised against the schema."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    # Force preset path off and mock create_session so the worker is a no-op.
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "task done"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_resp)
    agent.service.create_session = MagicMock(return_value=mock_session)

    result = mgr.handle({
        "action": "emanate",
        # backend defaults to "lingtai"
        "tasks": [{
            "task": "lingtai task",
            "tools": ["file"],
            # This must be ignored, not validated. Even an "invalid" object
            # would be accepted because the lingtai backend never reads it.
            "backend_options": {"effort": "high"},
        }],
    })
    assert result["status"] == "dispatched"


def test_unknown_backend_falls_back_to_lingtai_path(tmp_path, monkeypatch):
    """Unknown backend is normalized to the detached LingTai execution path."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "not-real",
        "tasks": [{"task": "fall back", "tools": []}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    state = wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])
    assert records[0]["manifest"]["backend"] == "lingtai"
    assert state["backend"] == "lingtai"
    assert "future" not in mgr._emanations[em_id]


# ---------------------------------------------------------------------------
# Runner cmd construction: backend_argv lands before the task prompt
# ---------------------------------------------------------------------------


def test_claude_code_cmd_appends_backend_argv_before_task(tmp_path):
    """The Claude Code runner must put backend_argv after the required
    infrastructure flags and immediately before the task positional."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    captured_cmd: list[list[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.append(list(cmd))
        return FiniteFakeProc()

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-test",
        task="dummy task",
        tools=[],
        model="claude-code",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="claude-code",
    )

    cancel = threading.Event()
    timeout = threading.Event()

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_claude_code_emanation(
            "em-test", run_dir, "Refactor auth.",
            cancel, timeout,
            backend_argv=["--effort", "high", "--model", "claude-opus-4-7"],
        )

    assert len(captured_cmd) == 1
    cmd = captured_cmd[0]
    # Required prefix preserved
    assert cmd[0] == "claude"
    assert "--print" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--output-format" in cmd and "stream-json" in cmd
    assert "--verbose" in cmd
    assert "--name" in cmd
    # backend_argv lives somewhere after --name and before the trailing task
    effort_idx = cmd.index("--effort")
    model_idx = cmd.index("--model")
    name_idx = cmd.index("--name")
    task_idx = cmd.index("Refactor auth.")
    assert name_idx < effort_idx < task_idx
    assert name_idx < model_idx < task_idx
    # The task itself is the very last token
    assert cmd[-1] == "Refactor auth."


def test_claude_code_spawn_env_carries_backend_env_overlay(tmp_path):
    """`backend_options.env` reaches the spawned Claude subprocess as an
    environment variable, never as an argv token."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    port = _OneShotRecordingPort(lines=(
        '{"type":"system","subtype":"init","session_id":"sess-env"}\n',
        '{"type":"result","subtype":"success","is_error":false,'
        '"result":"done"}\n',
    ))
    mgr._process_port = port
    run_dir = make_daemon_run_dir(agent, backend="claude-code")

    mgr._run_claude_code_emanation(
        "em-claude-env", run_dir, "Refactor auth.",
        threading.Event(), threading.Event(),
        backend_argv=["--model", "opus"],
        backend_env={"CLAUDE_CONFIG_DIR": "/tmp/profile/config"},
    )

    command, group_id = port.commands[0]
    env = dict(command.environment or ())
    assert env["CLAUDE_CONFIG_DIR"] == "/tmp/profile/config"
    # The overlay wins over whatever the parent process inherited.
    assert env["PATH"] == os.environ["PATH"]
    # The reserved key never becomes a flag, and no env value rides on argv.
    assert "--env" not in command.argv
    assert "CLAUDE_CONFIG_DIR" not in " ".join(command.argv)
    assert command.argv[:2] == ("claude", "--print")
    assert command.argv[-1] == "Refactor auth."
    assert group_id == run_dir.group_id


def test_claude_code_spawn_env_unchanged_without_overlay(tmp_path):
    """No `env` overlay leaves the sanitized Claude spawn env untouched."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    port = _OneShotRecordingPort(lines=(
        '{"type":"result","subtype":"success","is_error":false,'
        '"result":"done"}\n',
    ))
    mgr._process_port = port
    run_dir = make_daemon_run_dir(agent, backend="claude-code")

    mgr._run_claude_code_emanation(
        "em-claude-noenv", run_dir, "Refactor auth.",
        threading.Event(), threading.Event(),
    )

    command, _ = port.commands[0]
    env = dict(command.environment or ())
    # Whatever the parent inherited is passed through untouched — the runner
    # adds nothing of its own when no overlay is supplied.
    assert env.get("CLAUDE_CONFIG_DIR") == os.environ.get("CLAUDE_CONFIG_DIR")
    # The pre-existing credential strip list is unaffected by the env plumbing.
    for stripped in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                     "CLAUDE_CODE_OAUTH_TOKEN"):
        assert stripped not in env


def test_codex_cmd_appends_backend_argv_before_task(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    captured_cmd: list[list[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.append(list(cmd))
        return FiniteFakeProc()

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-codex",
        task="dummy",
        tools=[],
        model="codex",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="codex",
    )

    cancel = threading.Event()
    timeout = threading.Event()

    # Codex needs a `turn.completed` event to consider the run successful;
    # feed a minimal valid stream.
    fake_stdout_lines = [
        '{"type":"thread.started","thread_id":"thr-xyz"}\n',
        '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n',
        '{"type":"turn.completed"}\n',
    ]

    with patch("lingtai.tools.daemon.subprocess.Popen",
               side_effect=lambda cmd, *a, **kw: (captured_cmd.append(list(cmd))
                                                  or FiniteFakeProc(
                                                      stdout_lines=fake_stdout_lines,
                                                  ))):
        mgr._run_codex_emanation(
            "em-codex", run_dir, "Find the breaking change.",
            cancel, timeout,
            backend_argv=["--model", "gpt-5", "--search"],
        )

    assert len(captured_cmd) == 1
    cmd = captured_cmd[0]
    assert cmd[:4] == ["codex", "exec", "--json",
                       "--dangerously-bypass-approvals-and-sandbox"]
    # backend_argv tokens are present, in order, and before the task
    assert cmd[4:6] == ["--model", "gpt-5"]
    assert cmd[6] == "--search"
    assert cmd[-1] == "Find the breaking change."


# ---------------------------------------------------------------------------
# Schema surface
# ---------------------------------------------------------------------------


def test_schema_includes_backend_options():
    from tests._daemon_helpers import daemon_emanate_task_schema
    task_props = daemon_emanate_task_schema("en")["properties"]
    assert "backend_options" in task_props
    backend_options = task_props["backend_options"]
    assert backend_options["type"] == "object"
    passthrough = backend_options["additionalProperties"]
    assert set(backend_options["properties"]) == {"config", "env"}
    env_schema = backend_options["properties"]["env"]
    assert env_schema["type"] == "object"
    assert env_schema["additionalProperties"] == {"type": "string"}
    assert env_schema["propertyNames"]["pattern"] == "^[A-Za-z_][A-Za-z0-9_]*$"
    config = backend_options["properties"]["config"]
    assert config is not passthrough
    assert config["anyOf"] == passthrough["anyOf"]
    assert "constrained tool-schema providers" in config["description"]
    alternatives = passthrough["anyOf"]
    assert {item.get("type") for item in alternatives} == {
        "boolean", "string", "integer", "number", "null", "array",
    }
    array_schema = next(item for item in alternatives if item.get("type") == "array")
    assert {item["type"] for item in array_schema["items"]["anyOf"]} == {
        "string", "integer", "number",
    }
    # Nested objects and booleans in lists are intentionally not representable.
    assert all(item.get("type") != "object" for item in alternatives)
    assert all(
        item["type"] != "boolean"
        for item in array_schema["items"]["anyOf"]
    )
    # The free-form description should mention discovery via --help so
    # agents know not to expect a fixed list here.
    assert "--help" in task_props["backend_options"]["description"]


def test_backend_schema_enum_matches_ordered_contract():
    from lingtai.tools.daemon import get_schema

    expected = [
        "lingtai",
        "claude-p",
        "claude-code",
        "codex",
        "opencode",
        "mimocode",
        "mimo",
        "qwen-code",
        "qwen",
        "oh-my-pi",
        "omp",
        "kimicode",
        "kimi",
        "cursor",
        "deepseek",
    ]
    assert list(_BACKEND_SCHEMA_ENUM) == expected
    from tests._daemon_helpers import daemon_action_input_schema
    assert (
        daemon_action_input_schema("emanate", "en")["properties"]["backend"]["enum"]
        # ``backend`` is required-nullable in the strict child schema; ``None``
        # means "absent" and the engine then applies its own ``lingtai`` default.
        == [*expected, None]
    )


@pytest.mark.parametrize(
    ("backend_label", "expected"),
    [
        ("MiMo Code", True),
        ("Qwen Code", True),
        ("Kimi Code", True),
        ("Oh-My-Pi", True),
        ("DeepSeek Harness", True),
        ("cursor", True),
        ("opencode", True),
        ("claude-p", True),
        ("claude-code", True),
        ("claude-interactive", False),
        ("interactive", False),
    ],
)
def test_backend_schema_description_matches_supported_surface(
    backend_label, expected,
):
    from tests._daemon_helpers import daemon_action_input_schema

    description = daemon_action_input_schema(
        "emanate", "en"
    )["properties"]["backend"]["description"]
    assert (backend_label.lower() in description.lower()) is expected


def test_backend_metadata_consistency_keeps_hidden_legacy_claude():
    hidden = {"claude", "claude-interactive"}
    assert set(_BACKEND_SCHEMA_ENUM) == (
        (set(_BACKEND_SPECS) - hidden) | set(_BACKEND_ALIASES)
    )
    assert hidden.isdisjoint(_BACKEND_SCHEMA_ENUM)
    assert _BACKEND_ALIASES == {
        "mimo": "mimocode",
        "qwen": "qwen-code",
        "omp": "oh-my-pi",
        "kimi": "kimicode",
    }
    assert all(target in _BACKEND_SPECS for target in _BACKEND_ALIASES.values())
    assert _BACKEND_SPECS["claude-code"].runner_attr == "_run_claude_code_emanation"
    assert _BACKEND_SPECS["claude-p"].runner_attr == "_run_claude_code_emanation"


def test_normalize_backend_aliases_only_true_aliases():
    assert _normalize_backend("mimo") == "mimocode"
    assert _normalize_backend("qwen") == "qwen-code"
    assert _normalize_backend("omp") == "oh-my-pi"
    assert _normalize_backend("kimi") == "kimicode"
    assert _normalize_backend(None) == "lingtai"
    assert _normalize_backend("") == "lingtai"
    assert _normalize_backend("claude-code") == "claude-code"
    assert _normalize_backend("not-real") == "not-real"


def test_mimocode_alias_dispatches_to_canonical_backend(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "mimo",
        "tasks": [{"task": "Use MiMo Code.", "tools": [],
                   "backend_options": {"model": "mimo-auto"}}],
    })
    assert result["status"] == "dispatched"
    state = wait_daemon_terminal(mgr._emanations[result["ids"][0]]["run_dir"])
    assert records[0]["manifest"]["backend"] == "mimocode"
    assert state["model"] == "mimocode"
    assert records[0]["manifest"]["backend_argv"] == ["--model", "mimo-auto"]


def test_cli_contexts_keep_per_task_argv_and_passive_mcp(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    with patch.object(
        mgr,
        "_connect_task_mcp_registrations",
        side_effect=AssertionError("CLI backend must not connect MCP clients"),
    ):
        result = mgr.handle({
            "action": "emanate", "backend": "claude-code",
            "tasks": [
                {"task": "task with argv", "tools": [],
                 "backend_options": {"model": "claude-opus-4-7"}},
                {"task": "task with mcp", "tools": [],
                 "mcp": [{"name": "demo", "command": "demo-mcp",
                           "args": ["--serve"], "env": {"TOKEN": "secret"}}]},
            ],
        })
    assert result["status"] == "dispatched"
    states = {
        DaemonRunDir.read_state_from_disk(record["run_dir"].path)["task"]:
            wait_daemon_terminal(record["run_dir"])
        for record in records
    }
    manifests = {
        DaemonRunDir.read_state_from_disk(record["run_dir"].path)["task"]:
            record["manifest"]
        for record in records
    }
    argv_with_model = manifests["task with argv"]["backend_argv"]
    assert argv_with_model[:2] == ["--model", "claude-opus-4-7"]
    assert "--mcp-config" in argv_with_model
    assert "--strict-mcp-config" in argv_with_model
    assert states["task with argv"]["call_parameters"]["mcp"][0]["name"] == "daemon_common"
    argv_with_mcp = manifests["task with mcp"]["backend_argv"]
    assert "--mcp-config" in argv_with_mcp
    assert "--strict-mcp-config" in argv_with_mcp
    assert "backend_argv" not in states["task with mcp"]
    assert "--mcp-config" in states["task with mcp"]["backend_harness_argv"]
    assert "--strict-mcp-config" in states["task with mcp"]["backend_harness_argv"]
    mcp_params = states["task with mcp"]["call_parameters"]["mcp"]
    assert mcp_params[0]["name"] == "daemon_common"
    assert mcp_params[1] == {
        "name": "demo",
        "command": "demo-mcp",
        "args": ["--serve"],
        "env": {"TOKEN": "<redacted>"},
        "transport": "stdio",
    }


def test_mimocode_cmd_appends_backend_argv_before_prompt(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_cmd: list[list[str]] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-mimo",
        task="dummy",
        tools=[],
        model="mimocode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="mimocode",
    )
    stdout_lines = [
        '{"type":"session.created","sessionID":"sess-mimo"}\n',
        '{"type":"message.completed","text":"done"}\n',
    ]

    with patch("lingtai.tools.daemon.subprocess.Popen",
               side_effect=lambda cmd, *a, **kw: (captured_cmd.append(list(cmd))
                                                  or FiniteFakeProc(
                                                      stdout_lines=stdout_lines,
                                                  ))):
        mgr._run_mimocode_emanation(
            "em-mimo", run_dir, "Refactor with MiMo.",
            threading.Event(), threading.Event(),
            backend_argv=["--model", "mimo-auto", "--agent", "build"],
        )

    cmd = captured_cmd[0]
    assert cmd[:4] == ["mimo", "run", "--format", "json"]
    assert cmd[4:8] == ["--model", "mimo-auto", "--agent", "build"]
    assert "Refactor with MiMo." in cmd[-1]
    assert run_dir._state["mimocode_session_id"] == "sess-mimo"


def test_qwen_code_cmd_appends_backend_argv_before_prompt(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_cmd: list[list[str]] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-qwen",
        task="dummy",
        tools=[],
        model="qwen-code",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="qwen-code",
    )

    with patch("lingtai.tools.daemon.subprocess.Popen",
               side_effect=lambda cmd, *a, **kw: (captured_cmd.append(list(cmd))
                                                  or FiniteFakeProc(
                                                      stdout_lines=["qwen done\n"],
                                                  ))):
        mgr._run_qwen_code_emanation(
            "em-qwen", run_dir, "Refactor with Qwen.",
            threading.Event(), threading.Event(),
            backend_argv=["--model", "qwen3-coder-plus"],
        )

    cmd = captured_cmd[0]
    assert cmd[:2] == ["qwen", "--yolo"]
    assert cmd[2:4] == ["--model", "qwen3-coder-plus"]
    assert cmd[-2] == "-p"
    assert "Refactor with Qwen." in cmd[-1]
    assert run_dir._state["last_output"] == "qwen done"


def test_qwen_initial_run_uses_injected_port_without_local_deadline(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    port = _OneShotRecordingPort(lines=("qwen done\n", "\n"))
    mgr._process_port = port
    run_dir = make_daemon_run_dir(agent, backend="qwen-code")

    mgr._run_qwen_code_emanation(
        "em-qwen-port", run_dir, "Refactor with Qwen.",
        threading.Event(), threading.Event(),
        backend_argv=["--model", "qwen3-coder-plus"],
    )

    command, group_id = port.commands[0]
    assert command.argv[:5] == (
        "qwen", "--yolo", "--model", "qwen3-coder-plus", "-p",
    )
    assert "Refactor with Qwen." in command.argv[-1]
    assert command.cwd == agent._working_dir
    assert command.environment is not None
    assert port.deadlines == [None]
    assert port.waited == [port.handle]
    assert group_id == run_dir.group_id
    assert port.released == [port.handle]
    assert run_dir._state["last_output"] == "qwen done"


def test_kimicode_initial_run_uses_injected_port_and_private_environment(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    port = _OneShotRecordingPort(lines=("kimi done\n",))
    mgr._process_port = port
    run_dir = make_daemon_run_dir(agent, backend="kimicode")

    mgr._run_kimicode_emanation(
        "em-kimi-port", run_dir, "Refactor with Kimi.",
        threading.Event(), threading.Event(), backend_argv=["--model", "kimi-for-coding"],
    )

    command, group_id = port.commands[0]
    env = dict(command.environment or ())
    assert command.argv[:4] == ("kimi", "--model", "kimi-for-coding", "--prompt")
    assert "Refactor with Kimi." in command.argv[4]
    assert command.argv[5:] == ("--output-format", "text")
    assert command.cwd == agent._working_dir
    assert env["KIMI_CODE_HOME"].startswith(str(run_dir.path))
    assert env["KIMI_DISABLE_TELEMETRY"] == "1"
    assert env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    assert env["KIMI_MODEL_NAME"] == "kimi-for-coding"
    assert port.deadlines == [None]
    assert port.waited == [port.handle]
    assert group_id == run_dir.group_id
    assert port.released == [port.handle]
    assert run_dir._state["last_output"] == "kimi done"


@pytest.mark.parametrize(
    ("runner", "backend", "label"),
    [
        ("_run_qwen_code_emanation", "qwen-code", "qwen-code"),
        ("_run_kimicode_emanation", "kimicode", "kimicode"),
        ("_run_deepseek_emanation", "deepseek", "deepseek"),
    ],
)
def test_one_shot_cancellation_uses_port_termination_attribution(
    tmp_path, monkeypatch, runner, backend, label,
):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    cancel_event = threading.Event()
    timeout_event = threading.Event()
    port = _OneShotRecordingPort(lines=("partial\n",), exit_receipt=DaemonProcessExit(-15, "timeout"))
    mgr._process_port = port
    run_dir = make_daemon_run_dir(agent, backend=backend)
    # Cancellation arrives after stdout closes; the terminal Port receipt must
    # still win classification and preserve its local timeout attribution.
    def iter_stdout(handle, *, deadline=None):
        port.deadlines.append(deadline)
        yield "partial\n"
        timeout_event.set()
        cancel_event.set()

    port.iter_stdout = iter_stdout
    monkeypatch.setattr(
        "lingtai.tools.daemon._kill_process_group",
        lambda proc: pytest.fail("legacy kill used"),
    )
    result = getattr(mgr, runner)(
        "em-cancel", run_dir, "cancel me", cancel_event, timeout_event,
    )
    assert result == "[cancelled]"
    assert run_dir._state["state"] == "timeout"
    assert run_dir._state["cli_termination"]["reason"] == "timeout"
    assert port.deadlines == [None]
    assert port.waited == [port.handle]
    assert port.released == [port.handle]


def test_qwen_code_rejects_harness_owned_backend_options(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    result = mgr.handle({
        "action": "emanate",
        "backend": "qwen-code",
        "tasks": [{"task": "bad", "tools": [],
                   "backend_options": {"prompt": "override"}}],
    })

    assert result["status"] == "error"
    assert "--prompt is reserved by the qwen-code daemon backend" in result["message"]
    assert mgr._emanations == {}


def test_qwen_code_ask_is_explicitly_unsupported(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "qwen-code",
        "tasks": [{"task": "Qwen once.", "tools": []}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    ask = mgr.handle({"action": "ask", "id": em_id, "message": "follow up"})

    assert ask["status"] == "error"
    assert ask["message"] == (
        "qwen-code daemon backend does not support daemon(action='ask') yet; "
        "start a new qwen-code emanation instead."
    )


# ---------------------------------------------------------------------------
# DeepSeek Harness backend
# ---------------------------------------------------------------------------


def test_dsh_initial_run_uses_native_headless_profile_and_env_overlay(
    tmp_path, monkeypatch,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    port = _OneShotRecordingPort(lines=("dsh done\n", "\n"))
    mgr._process_port = port
    monkeypatch.setattr(mgr, "_dsh_command_prefix", lambda env: ["dsh"])
    run_dir = make_daemon_run_dir(agent, backend="deepseek")

    result = mgr._run_dsh_emanation(
        "em-dsh", run_dir, "Refactor with DSH.",
        threading.Event(), threading.Event(),
        backend_argv=["--patch", "D:/rawle/dsh-extra.yml"],
        backend_env={"DSH_TEST_VALUE": "native-powershell"},
    )

    command, group_id = port.commands[0]
    assert command.argv[:5] == (
        "dsh", "--profile", "headless", "--patch",
        "D:/rawle/dsh-extra.yml",
    )
    assert command.argv[5:7] == ("--patch", str(run_dir.path / "dsh.patch.yml"))
    assert "Refactor with DSH." in command.argv[-1]
    assert command.cwd == agent._working_dir
    assert dict(command.environment)["DSH_TEST_VALUE"] == "native-powershell"
    assert port.deadlines == [None]
    assert port.waited == [port.handle]
    assert port.released == [port.handle]
    assert run_dir._state["state"] == "done"
    assert run_dir._state["last_output"] == "dsh done"
    assert result == "dsh done"


def test_dsh_run_generates_patch_uses_workspace_and_records_independent_acceptance(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "--quiet"], cwd=workspace, check=True,
        capture_output=True, text=True,
    )
    skill_dir = tmp_path / "selected-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: selected-skill\ndescription: selected\n---\nUse it.\n",
        encoding="utf-8",
    )

    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    mgr._manifest = {
        "mcp": [
            {
                "name": "docs",
                "transport": "stdio",
                "command": "docs-mcp",
                "args": ["--stdio"],
                "env": {"DOC_TOKEN": "secret"},
            }
        ]
    }
    port = _OneShotRecordingPort(lines=("verified\n",))
    mgr._process_port = port
    monkeypatch.setattr(mgr, "_dsh_command_prefix", lambda env: ["dsh"])
    run_dir = make_daemon_run_dir(agent, backend="deepseek")
    call_parameters = dict(run_dir._state["call_parameters"])
    call_parameters.update(
        {
            "workspace": str(workspace),
            "allowed_paths": ["src"],
            "required_checks": [
                [sys.executable, "-c", "raise SystemExit(0)"]
            ],
            "skills": [str(skill_dir)],
        }
    )
    run_dir.update_state(call_parameters=call_parameters)

    result = mgr._run_dsh_emanation(
        "em-dsh-contract", run_dir, "Implement the scoped change.",
        threading.Event(), threading.Event(),
    )

    command, _ = port.commands[0]
    assert command.cwd == workspace
    patch_path = run_dir.path / "dsh.patch.yml"
    assert command.argv[3:5] == ("--patch", str(patch_path))
    patch_text = patch_path.read_text(encoding="utf-8")
    assert '"mode": !!js process.env.DSH_PERMISSION_MODE ?? \'workspace-write\'' in patch_text
    assert "session-persistence-jsonl" in patch_text
    assert "dsh-skills" in patch_text
    assert "@deepseek-ai/dsh-mcp-client" in patch_text
    assert '"serverName": "docs"' in patch_text
    assert '"failOnStartupError": true' in patch_text
    assert 'DOC_TOKEN: !!js process.env.LINGTAI_DSH_MCP_0_DOC_TOKEN' in patch_text
    assert "secret" not in patch_text
    assert dict(command.environment)["LINGTAI_DSH_MCP_0_DOC_TOKEN"] == "secret"
    assert run_dir._state["execution_acceptance"]["status"] == "accepted"
    assert run_dir._state["execution_acceptance"]["checks"][0]["returncode"] == 0
    assert result == "verified"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("allowed_paths", ["../outside"], "allowed_paths[0]"),
        ("required_checks", ["python -m pytest"], "required_checks[0]"),
    ],
)
def test_dsh_execution_contract_rejects_unsafe_or_shell_shaped_input_before_dispatch(
    tmp_path, field, value, message,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    result = mgr.handle(
        {
            "action": "emanate",
            "backend": "deepseek",
            "tasks": [
                {
                    "task": "scoped",
                    "tools": [],
                    "workspace": str(tmp_path),
                    field: value,
                }
            ],
        }
    )

    assert result["status"] == "error"
    assert message in result["message"]
    assert mgr._emanations == {}


def test_dsh_failed_required_check_records_rejected_acceptance(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    port = _OneShotRecordingPort(lines=("looks done\n",))
    mgr._process_port = port
    monkeypatch.setattr(mgr, "_dsh_command_prefix", lambda env: ["dsh"])
    run_dir = make_daemon_run_dir(agent, backend="deepseek")
    call_parameters = dict(run_dir._state["call_parameters"])
    call_parameters.update(
        {
            "workspace": str(workspace),
            "allowed_paths": [],
            "required_checks": [
                [sys.executable, "-c", "raise SystemExit(3)"]
            ],
        }
    )
    run_dir.update_state(call_parameters=call_parameters)

    mgr._run_dsh_emanation(
        "em-dsh-rejected", run_dir, "Do it.",
        threading.Event(), threading.Event(),
    )

    acceptance = run_dir._state["execution_acceptance"]
    assert acceptance["status"] == "rejected"
    assert acceptance["checks"][0]["returncode"] == 3


def test_dsh_rejects_owned_profile_option_before_dispatch(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    result = mgr.handle({
        "action": "emanate",
        "backend": "deepseek",
        "tasks": [{
            "task": "bad",
            "tools": [],
            "backend_options": {"profile": "tui"},
        }],
    })

    assert result["status"] == "error"
    assert "--profile is reserved by the deepseek daemon backend" in result["message"]
    assert mgr._emanations == {}


def test_dsh_does_not_claim_native_common_mcp_loading():
    assert _source_cli_backend_loads_common_mcp("deepseek") is False


def test_dsh_finds_powershell_shim_without_relying_on_pathext(tmp_path):
    shim_dir = tmp_path / "node_modules" / ".bin"
    shim_dir.mkdir(parents=True)
    shim = shim_dir / "dsh.ps1"
    shim.write_text("# npm PowerShell shim\n", encoding="utf-8")
    entrypoint = tmp_path / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// dsh entrypoint\n", encoding="utf-8")

    found = _BACKEND_SPECS["deepseek"]
    assert found.id == "deepseek"
    from lingtai.tools.daemon import DaemonManager
    assert DaemonManager._dsh_powershell_script(str(shim_dir)) == str(shim)
    assert DaemonManager._dsh_node_entrypoint(str(shim)) == str(entrypoint)


def test_dsh_ask_is_explicitly_unsupported(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "deepseek",
        "tasks": [{"task": "DSH once.", "tools": []}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    ask = mgr.handle({"action": "ask", "id": em_id, "message": "follow up"})

    assert ask["status"] == "error"
    assert ask["message"] == (
        "deepseek daemon backend does not support daemon(action='ask') yet; "
        "start a new deepseek emanation instead."
    )


# ---------------------------------------------------------------------------
# Kimi Code backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["kimi", "kimicode"])
def test_kimicode_alias_and_canonical_dispatch_to_backend(tmp_path, monkeypatch, backend):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": backend,
        "tasks": [{"task": "Use Kimi Code.", "tools": [],
                   "backend_options": {"model": "kimi-for-coding"}}],
    })
    assert result["status"] == "dispatched"
    state = wait_daemon_terminal(mgr._emanations[result["ids"][0]]["run_dir"])
    assert records[0]["manifest"]["backend"] == "kimicode"
    assert state["model"] == "kimicode"
    assert records[0]["manifest"]["backend_argv"] == ["--model", "kimi-for-coding"]
    assert state["call_parameters"]["mcp"][0]["name"] == "daemon_common"


def test_kimicode_cmd_appends_backend_argv_before_owned_flags(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_cmd: list[list[str]] = []
    captured_env: list[dict] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-kimi",
        task="dummy",
        tools=[],
        model="kimicode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="kimicode",
    )

    def fake_popen(cmd, *a, **kw):
        captured_cmd.append(list(cmd))
        captured_env.append(dict(kw.get("env") or {}))
        return FiniteFakeProc(stdout_lines=["kimi done\n"])

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_kimicode_emanation(
            "em-kimi", run_dir, "Refactor with Kimi.",
            threading.Event(), threading.Event(),
            backend_argv=["--model", "kimi-for-coding"],
        )

    cmd = captured_cmd[0]
    assert cmd[0] == "kimi"
    # Free-form backend_argv comes right after the executable...
    assert cmd[1:3] == ["--model", "kimi-for-coding"]
    # ...and the harness-owned flags come last, with the prompt behind --prompt.
    assert cmd[-2:] == ["--output-format", "text"]
    assert cmd[-4] == "--prompt"
    assert "Refactor with Kimi." in cmd[-3]
    assert "--yolo" not in cmd
    assert run_dir._state["last_output"] == "kimi done"


def test_kimicode_run_env_defaults_and_home(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_env: list[dict] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-kimi-env",
        task="dummy",
        tools=[],
        model="kimicode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="kimicode",
    )

    # No canonical key set; a source key is present and must be mapped.
    monkeypatch.delenv("KIMI_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_MODEL_NAME", raising=False)
    monkeypatch.setenv("KIMICODE_API_KEY", "sk-secret-kimi")

    def fake_popen(cmd, *a, **kw):
        captured_env.append(dict(kw.get("env") or {}))
        return FiniteFakeProc(stdout_lines=["ok\n"])

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_kimicode_emanation(
            "em-kimi-env", run_dir, "Do it.",
            threading.Event(), threading.Event(),
        )

    env = captured_env[0]
    # Run-private KIMI_CODE_HOME lives under the run dir.
    assert env["KIMI_CODE_HOME"].startswith(str(run_dir.path))
    assert env["KIMI_DISABLE_TELEMETRY"] == "1"
    assert env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    # Source key mapped onto the canonical var.
    assert env["KIMI_MODEL_API_KEY"] == "sk-secret-kimi"
    # Provider defaults applied when absent.
    assert env["KIMI_MODEL_NAME"] == "kimi-for-coding"
    assert env["KIMI_MODEL_PROVIDER_TYPE"] == "kimi"
    assert env["KIMI_MODEL_BASE_URL"] == "https://api.kimi.com/coding/v1"
    assert env["KIMI_MODEL_MAX_CONTEXT_SIZE"] == "262144"


def test_kimicode_run_env_respects_existing_operator_values(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_env: list[dict] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-kimi-op",
        task="dummy",
        tools=[],
        model="kimicode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="kimicode",
    )

    # Operator already set the canonical key and a model name — never override.
    monkeypatch.setenv("KIMI_MODEL_API_KEY", "operator-key")
    monkeypatch.setenv("KIMICODE_API_KEY", "should-be-ignored")
    monkeypatch.setenv("KIMI_MODEL_NAME", "operator-model")

    def fake_popen(cmd, *a, **kw):
        captured_env.append(dict(kw.get("env") or {}))
        return FiniteFakeProc(stdout_lines=["ok\n"])

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_kimicode_emanation(
            "em-kimi-op", run_dir, "Do it.",
            threading.Event(), threading.Event(),
        )

    env = captured_env[0]
    assert env["KIMI_MODEL_API_KEY"] == "operator-key"
    assert env["KIMI_MODEL_NAME"] == "operator-model"


@pytest.mark.parametrize(
    ("present_key", "expected_value"),
    [
        ("KIMI_API_KEY", "sk-kimi-fallback"),
        ("MOONSHOT_API_KEY", "sk-moonshot-fallback"),
    ],
)
def test_kimicode_run_env_api_key_fallback_sources(
    tmp_path, monkeypatch, present_key, expected_value
):
    """When ``KIMICODE_API_KEY`` is absent, the next source in the fallback
    order (``KIMI_API_KEY`` then ``MOONSHOT_API_KEY``) maps onto the canonical
    ``KIMI_MODEL_API_KEY``."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_env: list[dict] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-kimi-fallback",
        task="dummy",
        tools=[],
        model="kimicode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="kimicode",
    )

    # No canonical key and no higher-priority source; only the parametrized
    # fallback source is present and must be mapped.
    monkeypatch.delenv("KIMI_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("KIMICODE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setenv(present_key, expected_value)

    def fake_popen(cmd, *a, **kw):
        captured_env.append(dict(kw.get("env") or {}))
        return FiniteFakeProc(stdout_lines=["ok\n"])

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_kimicode_emanation(
            "em-kimi-fallback", run_dir, "Do it.",
            threading.Event(), threading.Event(),
        )

    env = captured_env[0]
    assert env["KIMI_MODEL_API_KEY"] == expected_value


def test_kimicode_run_env_api_key_fallback_precedence(tmp_path, monkeypatch):
    """When multiple source keys are present, the fallback order is honored:
    ``KIMICODE_API_KEY`` beats ``KIMI_API_KEY`` beats ``MOONSHOT_API_KEY``."""
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_env: list[dict] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-kimi-precedence",
        task="dummy",
        tools=[],
        model="kimicode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="kimicode",
    )

    monkeypatch.delenv("KIMI_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("KIMICODE_API_KEY", "sk-kimicode-wins")
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-loses")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-moonshot-loses")

    def fake_popen(cmd, *a, **kw):
        captured_env.append(dict(kw.get("env") or {}))
        return FiniteFakeProc(stdout_lines=["ok\n"])

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_kimicode_emanation(
            "em-kimi-precedence", run_dir, "Do it.",
            threading.Event(), threading.Event(),
        )

    env = captured_env[0]
    # Highest-priority source wins over the two lower-priority fallbacks.
    assert env["KIMI_MODEL_API_KEY"] == "sk-kimicode-wins"


def test_kimicode_in_common_mcp_loading_set():
    assert _source_cli_backend_loads_common_mcp("kimicode") is True
    assert _source_cli_backend_loads_common_mcp("qwen-code") is True


def test_kimicode_writes_run_private_mcp_json_for_common_and_parent_mcp(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "kimicode",
        "tasks": [{"task": "Use Kimi MCP.", "tools": [], "mcp": [
            {"name": "parent-docs", "transport": "stdio", "command": "/bin/echo",
             "args": ["docs"], "env": {"DOC_TOKEN": "dummy"}},
            {"name": "parent_http", "transport": "http",
             "url": "https://mcp.example.test/mcp",
             "headers": {"Authorization": "Bearer dummy"}},
        ]}],
    })
    assert result["status"] == "dispatched"
    record = records[0]
    state = wait_daemon_terminal(record["run_dir"])
    task_prompt = record["run_dir"].prompt_path.read_text(encoding="utf-8")
    assert "call the MCP tool `finish`" in task_prompt
    assert "Bearer dummy" not in task_prompt
    assert "DOC_TOKEN: <redacted>" in task_prompt
    assert state["call_parameters"]["mcp"][1]["env"] == {"DOC_TOKEN": "<redacted>"}
    assert state["call_parameters"]["mcp"][2]["headers"] == {
        "Authorization": "<redacted>"
    }

    mcp_path = Path(state["backend_harness_files"]["kimicode_mcp_config"])
    assert mcp_path.name == "mcp.json"
    assert mcp_path.parent.name == "kimi-code-home"
    config = json.loads(mcp_path.read_text(encoding="utf-8"))
    common = config["mcpServers"]["daemon_common"]
    assert common["transport"] == "stdio"
    assert common["args"] == ["-m", "lingtai.mcp_servers.daemon_common"]
    assert common["env"]["LINGTAI_DAEMON_COMPLETION_FILE"].endswith(
        "daemon_completion.json"
    )
    docs = config["mcpServers"]["parent-docs"]
    assert docs == {
        "transport": "stdio",
        "command": "/bin/echo",
        "args": ["docs"],
        "env": {"DOC_TOKEN": "dummy"},
    }
    parent_http = config["mcpServers"]["parent_http"]
    assert parent_http == {
        "transport": "http",
        "url": "https://mcp.example.test/mcp",
        "headers": {"Authorization": "Bearer dummy"},
    }


def test_kimicode_missing_completion_signal_prevents_done(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(
        agent,
        handle="em-kimi-completion",
        task="dummy",
        tools=[],
        model="kimicode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="kimicode",
    )
    run_dir._state.setdefault("call_parameters", {})["mcp"] = [
        {"name": "daemon_common", "transport": "stdio"}
    ]
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)

    def fake_popen(cmd, *a, **kw):
        return FiniteFakeProc(stdout_lines=["kimi says done\n"])

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        with pytest.raises(RuntimeError, match="missing completion"):
            mgr._run_kimicode_emanation(
                "em-kimi-completion",
                run_dir,
                "Do it.",
                threading.Event(),
                threading.Event(),
            )

    data = json.loads(run_dir.daemon_json_path.read_text(encoding="utf-8"))
    assert data["state"] == "failed"
    assert "kimi says done" in (run_dir.path / "result.txt").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("bad_flag", ["prompt", "output-format", "yolo", "session", "continue"])
def test_kimicode_rejects_harness_owned_backend_options(tmp_path, bad_flag):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    result = mgr.handle({
        "action": "emanate",
        "backend": "kimicode",
        "tasks": [{"task": "bad", "tools": [],
                   "backend_options": {bad_flag: "override"}}],
    })

    assert result["status"] == "error"
    assert f"--{bad_flag} is reserved by the kimicode daemon backend" in result["message"]
    assert mgr._emanations == {}


def test_kimicode_ask_is_explicitly_unsupported(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "kimicode",
        "tasks": [{"task": "Kimi once.", "tools": []}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    ask = mgr.handle({"action": "ask", "id": em_id, "message": "follow up"})

    assert ask["status"] == "error"
    assert ask["message"] == (
        "kimicode daemon backend does not support daemon(action='ask') yet; "
        "start a new kimicode emanation instead."
    )


# ---------------------------------------------------------------------------
# Oh-My-Pi backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["omp", "oh-my-pi"])
def test_oh_my_pi_alias_and_canonical_dispatch_to_backend(tmp_path, monkeypatch, backend):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": backend,
        "tasks": [{"task": "Use Oh-My-Pi.", "tools": [],
                   "backend_options": {"provider": "anthropic"}}],
    })
    assert result["status"] == "dispatched"
    state = wait_daemon_terminal(mgr._emanations[result["ids"][0]]["run_dir"])
    assert records[0]["manifest"]["backend"] == "oh-my-pi"
    assert state["model"] == "oh-my-pi"
    assert records[0]["manifest"]["backend_argv"] == ["--provider", "anthropic"]


def test_oh_my_pi_cmd_includes_mode_json_and_session_id_from_header(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_cmd: list[list[str]] = []

    # Oh-My-Pi JSON mode: a `type:session` header (bare top-level id)
    # followed by agent events.
    stdout_lines = [
        '{"type":"session","id":"omp-sess-1","cwd":"/tmp"}\n',
        # Event ids that arrive after the session header must not overwrite
        # the resumable session id.
        '{"type":"session.updated","id":"not-the-session-id"}\n',
        '{"type":"message.completed","text":"all done"}\n',
    ]
    run_dir = make_daemon_run_dir(
        agent,
        handle="em-omp",
        task="dummy",
        tools=[],
        model="oh-my-pi",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="oh-my-pi",
    )

    with patch("lingtai.tools.daemon.subprocess.Popen",
               side_effect=lambda cmd, *a, **kw: (captured_cmd.append(list(cmd))
                                                  or FiniteFakeProc(
                                                      stdout_lines=stdout_lines,
                                                  ))):
        mgr._run_oh_my_pi_emanation(
            "em-omp", run_dir, "Refactor with Oh-My-Pi.",
            threading.Event(), threading.Event(),
            backend_argv=["--provider", "anthropic", "--model", "claude-x"],
        )

    cmd = captured_cmd[0]
    # `omp --mode json --approval-mode yolo` prefix, then backend_argv, then prompt.
    assert cmd[:5] == ["omp", "--mode", "json", "--approval-mode", "yolo"]
    assert cmd[5:9] == ["--provider", "anthropic", "--model", "claude-x"]
    assert "Refactor with Oh-My-Pi." in cmd[-1]
    # Session id captured from the `type:session` header, stored under the
    # Oh-My-Pi-specific key.
    assert run_dir._state["oh_my_pi_session_id"] == "omp-sess-1"


def test_oh_my_pi_ask_resume_uses_session_flag(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    captured_cmd: list[list[str]] = []

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-omp-ask",
        task="dummy",
        tools=[],
        model="oh-my-pi",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="oh-my-pi",
    )
    run_dir._state["oh_my_pi_session_id"] = "omp-sess-9"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)

    em_id = "em-omp-ask"
    entry = register_daemon_entry(
        mgr,
        em_id,
        run_dir,
        future=completed_future("[fake done]"),
        task="x",
        backend="oh-my-pi",
        ask_in_flight=False,
    )

    with patch("lingtai.tools.daemon.subprocess.Popen",
               side_effect=lambda cmd, *a, **kw: (captured_cmd.append(list(cmd))
                                                  or FiniteFakeProc(
                                                      stdout_lines=[
                                                          '{"type":"message.completed","text":"resumed"}\n',
                                                      ],
                                                  ))):
        resp = mgr.handle({"action": "ask", "id": em_id, "message": "keep going"})
        # ask is async; wait for the ask worker to finish before asserting.
        fut = entry.get("ask_future")
        if fut is not None:
            fut.result(timeout=5)

    assert resp["status"] == "sent"
    cmd = captured_cmd[0]
    assert cmd == [
        "omp", "--mode", "json", "--approval-mode", "yolo",
        "--session", "omp-sess-9", "keep going",
    ]


def test_oh_my_pi_ask_before_session_id_returns_initializing_error(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    run_dir = make_daemon_run_dir(
        agent,
        handle="em-omp-no-session",
        task="dummy",
        tools=[],
        model="oh-my-pi",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="oh-my-pi",
    )
    em_id = "em-omp-no-session"
    register_daemon_entry(
        mgr,
        em_id,
        run_dir,
        future=completed_future("[fake done]"),
        task="x",
        backend="oh-my-pi",
        ask_in_flight=False,
    )

    resp = mgr.handle({"action": "ask", "id": em_id, "message": "continue"})

    assert resp["status"] == "error"
    assert "No oh-my-pi session ID found" in resp["message"]
    assert "may still be initializing" in resp["message"]


def test_oh_my_pi_rejects_harness_owned_backend_options(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    for flag, key, value in (
        ("--mode", "mode", "text"),
        ("--print", "print", True),
        ("--approval-mode", "approval_mode", "yolo"),
        ("--auto-approve", "auto_approve", True),
        ("--yolo", "yolo", True),
        ("--session", "session", "omp-sess-1"),
        ("--resume", "resume", "omp-sess-1"),
        ("--continue", "continue", True),
        ("--no-session", "no_session", True),
        ("--session-dir", "session_dir", "/tmp/omp-session"),
    ):
        result = mgr.handle({
            "action": "emanate",
            "backend": "oh-my-pi",
            "tasks": [{"task": "bad", "tools": [],
                       "backend_options": {key: value}}],
        })
        assert result["status"] == "error", flag
        assert f"{flag} is reserved by the oh-my-pi daemon backend" in result["message"], flag
        assert mgr._emanations == {}, flag


def test_backend_options_env_schema_validates_payload():
    """The provider-facing schema accepts the reserved env overlay."""
    import jsonschema
    from tests._daemon_helpers import daemon_emanate_task_schema
    schema = daemon_emanate_task_schema("en")
    backend_options = schema["properties"]["backend_options"]
    payload = {"env": {"CLAUDE_CONFIG_DIR": "/tmp/profile"}, "model": "haiku"}
    # Valid: nested env object with string values and legal names.
    jsonschema.validate(payload, backend_options)
    # Invalid env name must fail the propertyNames pattern.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"env": {"9BAD": "x"}}, backend_options)
    # Invalid non-string env value must fail additionalProperties type.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"env": {"PATH": 7}}, backend_options)


def test_backend_env_redaction_values_extracts_strings():
    """backend_env_redaction_values returns non-empty string values only."""
    from lingtai.kernel.daemon_supervisor.manifest import (
        backend_env_redaction_values,
    )
    assert backend_env_redaction_values(None) == []
    assert backend_env_redaction_values({}) == []
    assert backend_env_redaction_values({"backend_env": "nope"}) == []
    assert backend_env_redaction_values({"backend_env": {"A": "secret-1"}}) == ["secret-1"]
    # Empty and non-string values are excluded; ordering is preserved.
    got = backend_env_redaction_values({
        "backend_env": {
            "A": "secret-1",
            "B": "",
            "C": 7,
            "D": "secret-2",
        },
    })
    assert got == ["secret-1", "secret-2"]


# ---------------------------------------------------------------------------
# DeepSeek Harness backend
# ---------------------------------------------------------------------------


def test_deepseek_cmd_appends_backend_argv_before_profile_lock(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    port = _OneShotRecordingPort(lines=("deepseek done\n",))
    mgr._process_port = port
    run_dir = make_daemon_run_dir(agent, backend="deepseek")

    mgr._run_deepseek_emanation(
        "em-deepseek-port", run_dir, "Refactor with DeepSeek.",
        threading.Event(), threading.Event(),
        backend_argv=["--patch", "./dsh-model.yml"],
    )

    command, group_id = port.commands[0]
    env = dict(command.environment or ())
    assert command.argv[:3] == ("dsh", "--patch", "./dsh-model.yml")
    # Harness-owned launcher flags lock the headless one-shot profile, and the
    # prompt is the headless app's trailing positional argument.
    assert command.argv[3:5] == ("--profile", "headless")
    assert "Refactor with DeepSeek." in command.argv[5]
    assert command.cwd == agent._working_dir
    # Run-private DSH_HOME keeps the first-use profile auto-initialization
    # inside the run dir; telemetry is hard-disabled for the headless run.
    assert env["DSH_HOME"].startswith(str(run_dir.path))
    assert env["DSH_TELEMETRY_DISABLED"] == "1"
    assert port.deadlines == [None]
    assert port.waited == [port.handle]
    assert group_id == run_dir.group_id
    assert port.released == [port.handle]
    assert run_dir._state["last_output"] == "deepseek done"


@pytest.mark.parametrize(
    ("lines", "exit_receipt", "cancel_after_stdout", "expected_state", "expected_result"),
    [
        # ``dsh`` prints its final answer once at the very end, and upstream
        # turns LingTai's SIGTERM into exit 0
        # (`apps/cli/src/profile-boot.ts`: `process.on('SIGTERM', () =>
        # interrupt(0))`), so a timed-out/reclaimed run is reaped as a zero
        # receipt with no stdout. Only the cancellation events can tell that
        # receipt from a genuinely completed session.
        ((), DaemonProcessExit(0, "timeout"), True, "timeout", "[cancelled]"),
        # A zero receipt without cancellation is ordinary success: exit 0 with
        # the final answer on stdout must still record done.
        (("final answer\n",), DaemonProcessExit(0), False, "done", "final answer"),
    ],
)
def test_deepseek_zero_receipt_classified_by_cancellation_not_exit_code(
    tmp_path, monkeypatch, lines, exit_receipt, cancel_after_stdout,
    expected_state, expected_result,
):
    """Regression for the review blocker: a SIGTERMed ``dsh`` exits 0, so a
    zero receipt after LingTai initiated termination must be timeout/cancelled,
    never ``done`` -- while a plain exit-0 receipt stays a successful run.
    """
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    cancel_event = threading.Event()
    timeout_event = threading.Event()
    port = _OneShotRecordingPort(lines=lines, exit_receipt=exit_receipt)
    mgr._process_port = port

    def wait(handle, *, timeout=None):
        port.waited.append(handle)
        # Cancellation arrives while the runner is blocked (the external group
        # sweep SIGTERMs dsh, stdout hits EOF, wait() reaps the receipt) --
        # i.e. after the stdout loop, before terminal classification.
        if cancel_after_stdout:
            cancel_event.set()
            timeout_event.set()
        return exit_receipt

    port.wait = wait
    run_dir = make_daemon_run_dir(agent, backend="deepseek")
    monkeypatch.setattr(
        "lingtai.tools.daemon._kill_process_group",
        lambda proc: pytest.fail("legacy kill used"),
    )
    result = mgr._run_deepseek_emanation(
        "em-deepseek-sigterm", run_dir, "cancel me", cancel_event, timeout_event,
    )

    assert result == expected_result
    assert run_dir._state["state"] == expected_state
    assert port.deadlines == [None]
    assert port.waited == [port.handle]
    assert port.released == [port.handle]
    if cancel_after_stdout:
        # The zero receipt still carries the local cause; the forensic record
        # must survive so daemon(check) can attribute the termination.
        assert run_dir._state["cli_termination"]["reason"] == "timeout"
        assert run_dir._state["cli_termination"]["signal"] == "SIGTERM"
        assert run_dir._state["cli_termination"]["returncode"] == 0
    else:
        assert run_dir._state["last_output"] == "final answer"


def test_deepseek_rejects_harness_owned_backend_options(tmp_path):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")

    for flag, key, value in (
        ("--profile", "profile", "tui"),
        ("--dump-default-config", "dump_default_config", True),
        ("--dump-config", "dump_config", True),
        ("--version", "version", True),
        ("--help", "help", True),
    ):
        result = mgr.handle({
            "action": "emanate",
            "backend": "deepseek",
            "tasks": [{"task": "bad", "tools": [],
                       "backend_options": {key: value}}],
        })
        assert result["status"] == "error", flag
        assert f"{flag} is reserved by the deepseek daemon backend" in result["message"], flag
        assert mgr._emanations == {}, flag


def test_deepseek_patch_overlay_survives_validation(tmp_path, monkeypatch):
    # ``--patch`` is deliberately NOT reserved: it is the official launcher
    # overlay for model/provider selection on a one-shot run.
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "deepseek",
        "tasks": [{"task": "Use DeepSeek Harness.", "tools": [],
                   "backend_options": {"patch": "./dsh-model.yml"}}],
    })
    assert result["status"] == "dispatched"
    state = wait_daemon_terminal(mgr._emanations[result["ids"][0]]["run_dir"])
    assert records[0]["manifest"]["backend"] == "deepseek"
    assert state["model"] == "deepseek"
    assert records[0]["manifest"]["backend_argv"] == ["--patch", "./dsh-model.yml"]


def test_deepseek_ask_is_explicitly_unsupported(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path, {"daemon": {"manager_pool_size": 0}})
    mgr = agent.get_capability("daemon")
    install_fake_detached_owner(monkeypatch)
    result = mgr.handle({
        "action": "emanate", "backend": "deepseek",
        "tasks": [{"task": "DeepSeek once.", "tools": []}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    ask = mgr.handle({"action": "ask", "id": em_id, "message": "follow up"})

    assert ask["status"] == "error"
    assert ask["message"] == (
        "deepseek daemon backend does not support daemon(action='ask') yet; "
        "start a new deepseek emanation instead."
    )
