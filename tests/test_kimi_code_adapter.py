"""Focused tests for the local Kimi Code LLM provider adapter."""

import json
from unittest.mock import patch

import pytest

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.llm.kimi_code.adapter import (
    KimiCodeAdapter,
    KimiCodeAuthError,
    KimiCodeContextOverflow,
    KimiCodeError,
    _extract_json_object,
    _parse_stream_json,
)


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _stream(content, session_id="kimi-session-1"):
    lines = [json.dumps({"role": "assistant", "content": content})]
    if session_id:
        lines.append(
            json.dumps(
                {
                    "role": "meta",
                    "type": "session.resume_hint",
                    "session_id": session_id,
                    "command": f"kimi -r {session_id}",
                }
            )
        )
    return "\n".join(lines) + "\n"


def _weather_tool():
    return FunctionSchema(
        name="get_weather",
        description="Get weather for a city.",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )


def test_extract_json_object_handles_fence_and_nested_braces():
    assert _extract_json_object(
        'prefix ```json\n{"action":"tool_call","name":"x","input":{"q":"}"}}\n```'
    ) == {
        "action": "tool_call",
        "name": "x",
        "input": {"q": "}"},
    }


def test_stream_parser_reads_assistant_and_resume_hint_without_usage_claim():
    parsed = _parse_stream_json(_stream('{"action":"final","text":"ok"}'))
    assert parsed["assistant_text"] == '{"action":"final","text":"ok"}'
    assert parsed["resume_session_id"] == "kimi-session-1"
    assert parsed["event_count"] == 2
    assert parsed["native_tool_calls"] == []


def test_wire_usage_reader_maps_real_cache_counters_and_cursors(tmp_path):
    adapter = KimiCodeAdapter(model="kimi-k2", cwd=tmp_path / "cwd")
    session_id = "session-wire-test"
    session_dir = adapter._kimi_home / "sessions" / session_id
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    (adapter._kimi_home / "session_index.jsonl").write_text(
        json.dumps({"sessionId": session_id, "sessionDir": str(session_dir)}) + "\n",
        encoding="utf-8",
    )
    wire.write_text(
        json.dumps(
            {
                "type": "usage.record",
                "usage": {
                    "inputCacheCreation": 3,
                    "inputCacheRead": 11520,
                    "inputOther": 8064,
                    "output": 40,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    usage, records = adapter._read_session_usage(session_id)
    assert records == 1
    assert usage.input_tokens == 19587
    assert usage.cached_tokens == 11520
    assert usage.output_tokens == 40
    next_usage, next_records = adapter._read_session_usage(session_id)
    assert next_usage is None
    assert next_records == 0


def test_registry_and_keyless_service_build_both_aliases():
    import lingtai.llm  # noqa: F401 - triggers registration
    from lingtai.llm.service import LLMService

    assert "kimi-code" in LLMService._adapter_registry
    assert "kimi_code" in LLMService._adapter_registry
    for provider in ("kimi-code", "kimi_code"):
        service = LLMService(provider=provider, model="kimi-k2", api_key=None)
        assert isinstance(service.get_adapter(provider), KimiCodeAdapter)


def test_send_command_uses_stream_json_prompt_argument_and_isolated_env(
    tmp_path, monkeypatch
):
    for name in (
        "KIMI_MODEL_API_KEY",
        "KIMI_CODE_API_KEY",
        "KIMICODE_API_KEY",
        "KIMI_API_KEY",
        "MOONSHOT_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    adapter = KimiCodeAdapter(model="kimi-k2", cwd=tmp_path / "cwd")
    session = adapter.create_chat("kimi-k2", "system instructions", [_weather_tool()])
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc(stdout=_stream('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=fake_run):
        response = session.send("hello")

    cmd = captured["cmd"]
    assert cmd[0] == "kimi"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "kimi-k2"
    assert (
        "--prompt" in cmd
        and "# AGENT SYSTEM INSTRUCTIONS" in cmd[cmd.index("--prompt") + 1]
    )
    assert (
        "--output-format" in cmd
        and cmd[cmd.index("--output-format") + 1] == "stream-json"
    )
    assert "--skills-dir" in cmd
    assert "--session" not in cmd
    assert "--continue" not in cmd and "--yolo" not in cmd
    assert "input" not in captured["kwargs"]
    assert captured["kwargs"]["cwd"] == str(tmp_path / "cwd")
    assert captured["kwargs"]["env"]["KIMI_CODE_HOME"].startswith(str(tmp_path / "cwd"))
    assert captured["kwargs"]["env"]["KIMI_DISABLE_TELEMETRY"] == "1"
    assert response.text == "ok"
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.cached_tokens == 0
    assert session.kimi_session_id == "kimi-session-1"


def test_resume_hint_drives_explicit_session_and_only_new_delta(tmp_path):
    adapter = KimiCodeAdapter(model="kimi-k2", cwd=tmp_path / "cwd")
    session = adapter.create_chat("kimi-k2", "sys", [_weather_tool()])
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append((cmd, kwargs))
        text = "first" if len(captured) == 1 else "second"
        return _FakeProc(stdout=_stream(f'{{"action":"final","text":"{text}"}}'))

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=fake_run):
        session.send("first user turn")
        session.send("second user turn")

    second_cmd, second_kwargs = captured[1]
    assert "--session" in second_cmd
    assert second_cmd[second_cmd.index("--session") + 1] == "kimi-session-1"
    second_prompt = second_cmd[second_cmd.index("--prompt") + 1]
    assert "second user turn" in second_prompt
    assert "first user turn" not in second_prompt
    assert "# CONTINUATION" in second_prompt
    assert "input" not in second_kwargs
    assert session.kimi_session_id == "kimi-session-1"


def test_tool_call_and_tool_result_roundtrip():
    adapter = KimiCodeAdapter(model="kimi-k2")
    session = adapter.create_chat("kimi-k2", "sys", [_weather_tool()])
    first = _stream(
        '{"action":"tool_call","name":"get_weather","input":{"city":"Paris"}}'
    )
    second = _stream('{"action":"final","text":"18C"}')
    with patch(
        "lingtai.llm.kimi_code.adapter.subprocess.run",
        side_effect=[_FakeProc(stdout=first), _FakeProc(stdout=second)],
    ):
        response = session.send("weather?")
        call = response.tool_calls[0]
        result = adapter.make_tool_result_message(
            "get_weather", {"temp_c": 18}, tool_call_id=call.id
        )
        final = session.send([result])

    assert call.name == "get_weather"
    assert call.args == {"city": "Paris"}
    assert call.id.startswith("kc_")
    assert final.text == "18C"
    assert [entry.role for entry in session.interface._entries] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_cli_failure_rolls_back_history_and_remote_identity():
    adapter = KimiCodeAdapter(model="kimi-k2")
    session = adapter.create_chat("kimi-k2", "sys", None)
    failed = _FakeProc(stdout="", stderr="unexpected failure", returncode=2)
    before = [(entry.role, len(entry.content)) for entry in session.interface._entries]
    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", return_value=failed):
        with pytest.raises(KimiCodeError):
            session.send("will fail")
    assert [
        (entry.role, len(entry.content)) for entry in session.interface._entries
    ] == before
    assert session.kimi_session_id is None


def test_missing_cli_is_auth_error():
    adapter = KimiCodeAdapter(model="kimi-k2")
    session = adapter.create_chat("kimi-k2", "sys", None)
    with patch(
        "lingtai.llm.kimi_code.adapter.subprocess.run",
        side_effect=FileNotFoundError(),
    ):
        with pytest.raises(KimiCodeAuthError):
            session.send("hello")


def test_no_model_configured_is_auth_error():
    adapter = KimiCodeAdapter(model="kimi-k2")
    session = adapter.create_chat("kimi-k2", "sys", None)
    failed = _FakeProc(stdout="", stderr="No model configured", returncode=1)
    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", return_value=failed):
        with pytest.raises(KimiCodeAuthError):
            session.send("hello")


def test_native_kimi_tool_calls_fail_closed():
    adapter = KimiCodeAdapter(model="kimi-k2")
    session = adapter.create_chat("kimi-k2", "sys", None)
    output = "\n".join(
        [
            json.dumps(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "native-1",
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                }
            ),
            json.dumps(
                {"role": "meta", "type": "session.resume_hint", "session_id": "s"}
            ),
        ]
    )
    with patch(
        "lingtai.llm.kimi_code.adapter.subprocess.run",
        return_value=_FakeProc(stdout=output),
    ):
        with pytest.raises(KimiCodeError, match="native tool calls"):
            session.send("hello")


def test_prompt_size_is_typed_context_overflow():
    adapter = KimiCodeAdapter(model="kimi-k2", max_prompt_bytes=10)
    with pytest.raises(KimiCodeContextOverflow):
        adapter._invoke_raw("this is too long", "kimi-k2")


def test_context_overflow_stderr_is_typed():
    adapter = KimiCodeAdapter(model="kimi-k2")
    session = adapter.create_chat("kimi-k2", "sys", None)
    failed = _FakeProc(stdout="", stderr="prompt is too long for model", returncode=1)
    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", return_value=failed):
        with pytest.raises(KimiCodeContextOverflow):
            session.send("hello")


def test_update_system_prompt_invalidates_remote_session():
    adapter = KimiCodeAdapter(model="kimi-k2")
    session = adapter.create_chat("kimi-k2", "sys", None)
    with patch(
        "lingtai.llm.kimi_code.adapter.subprocess.run",
        return_value=_FakeProc(stdout=_stream('{"action":"final","text":"ok"}')),
    ):
        session.send("hello")
    assert session.kimi_session_id == "kimi-session-1"
    session.update_system_prompt("new system")
    assert session.kimi_session_id is None


def test_per_turn_resync_with_identical_content_keeps_session(tmp_path):
    """Regression: the per-turn system/tool resync must not drop the CLI session.

    ``SessionManager.send`` rebuilds the system prompt and tools before *every*
    turn because they may have changed. When the rebuilt content is identical
    that resync must stay inert; an unconditional reset here cleared
    ``_kimi_session_id`` before each call, so ``--session`` was never sent and
    every turn replayed the whole conversation without the provider cache
    identity. The CLI only emits ``session.resume_hint`` on the turn that opens
    a session, so a discarded id is not re-offered.
    """
    adapter = KimiCodeAdapter(model="kimi-k2", cwd=tmp_path / "cwd")
    session = adapter.create_chat("kimi-k2", "sys", [_weather_tool()])
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _FakeProc(stdout=_stream('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=fake_run):
        for i in range(4):
            # Exactly what SessionManager.send does each turn, same content.
            session.update_system_prompt("sys")
            session.update_tools([_weather_tool()])
            session.send(f"message {i}")

    assert "--session" not in captured[0]
    for cmd in captured[1:]:
        assert cmd[cmd.index("--session") + 1] == "kimi-session-1"
    assert session.kimi_session_id == "kimi-session-1"


def test_changed_system_prompt_or_tools_resets_remote_session(tmp_path):
    """A real content change must still invalidate the CLI session."""
    adapter = KimiCodeAdapter(model="kimi-k2", cwd=tmp_path / "cwd")
    session = adapter.create_chat("kimi-k2", "sys", [_weather_tool()])
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _FakeProc(stdout=_stream('{"action":"final","text":"ok"}'))

    other_tool = FunctionSchema(
        name="other", description="o", parameters={"type": "object", "properties": {}}
    )

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=fake_run):
        session.update_system_prompt("sys")
        session.send("m0")
        session.update_system_prompt("sys")
        session.send("m1")
        session.update_system_prompt("CHANGED")  # real system-prompt change
        session.send("m2")
        session.update_system_prompt("CHANGED")
        session.send("m3")
        session.update_tools([other_tool])  # real tool change
        session.send("m4")

    assert ["--session" in cmd for cmd in captured] == [False, True, False, True, False]


def test_env_maps_existing_kimi_alias_without_logging(monkeypatch):
    monkeypatch.delenv("KIMI_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("KIMI_CODE_API_KEY", "secret-for-child-only")
    adapter = KimiCodeAdapter(model="kimi-for-coding")
    env = adapter._build_env()
    assert env["KIMI_MODEL_API_KEY"] == "secret-for-child-only"
    assert env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    assert env["KIMI_MODEL_NAME"] == "kimi-for-coding"
    assert env["KIMI_MODEL_PROVIDER_TYPE"] == "kimi"
    assert env["KIMI_MODEL_BASE_URL"] == "https://api.kimi.com/coding/v1"


def test_generate_does_not_resume_remote_session():
    adapter = KimiCodeAdapter(model="kimi-k2")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(stdout=_stream("plain answer", session_id="one-shot"))

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=fake_run):
        result = adapter.generate("kimi-k2", "hello")
    assert result.text == "plain answer"
    assert "--session" not in captured["cmd"]


def test_extra_argv_cannot_override_owned_session_or_safety_flags():
    with pytest.raises(ValueError, match="reserved flag"):
        KimiCodeAdapter(extra_argv=["--continue"])
    with pytest.raises(ValueError, match="reserved flag"):
        KimiCodeAdapter(extra_argv=["--yolo"])
