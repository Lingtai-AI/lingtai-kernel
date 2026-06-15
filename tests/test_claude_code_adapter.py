"""Tests for the claude-code provider adapter.

These mock the ``claude`` CLI subprocess so they run in CI without the binary.
A live end-to-end check against the real CLI lives in
``tests/integration_test_claude_code.py``.
"""

import json
from unittest.mock import patch

import pytest

from lingtai.llm.claude_code.adapter import (
    ClaudeCodeAdapter,
    ClaudeCodeAuthError,
    ClaudeCodeContextOverflow,
    ClaudeCodeError,
    _extract_json_object,
)
from lingtai_kernel.llm.base import FunctionSchema


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _envelope(result_str, *, is_error=False, usage=None, subtype="success"):
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "result": result_str,
            "session_id": "sess-123",
            "usage": usage
            or {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 10,
            },
        }
    )


def _weather_tool():
    return FunctionSchema(
        name="get_weather",
        description="Get the current weather for a city.",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )


# ---------------------------------------------------------------------------
# JSON action extraction
# ---------------------------------------------------------------------------


def test_extract_plain_object():
    assert _extract_json_object('{"action":"final","text":"hi"}') == {
        "action": "final",
        "text": "hi",
    }


def test_extract_fenced_object():
    raw = '```json\n{"action":"tool_call","name":"x","input":{"a":1}}\n```'
    assert _extract_json_object(raw) == {
        "action": "tool_call",
        "name": "x",
        "input": {"a": 1},
    }


def test_extract_object_with_surrounding_prose():
    raw = 'Sure, here you go: {"action":"final","text":"done"} hope that helps'
    assert _extract_json_object(raw) == {"action": "final", "text": "done"}


def test_extract_object_with_nested_braces_and_strings():
    raw = '{"action":"tool_call","name":"f","input":{"q":"a } b","n":{"x":1}}}'
    assert _extract_json_object(raw) == {
        "action": "tool_call",
        "name": "f",
        "input": {"q": "a } b", "n": {"x": 1}},
    }


def test_extract_returns_none_on_garbage():
    assert _extract_json_object("no json here at all") is None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_claude_code_is_registered():
    import lingtai.llm  # noqa: F401 — triggers register_all_adapters
    from lingtai.llm.service import LLMService

    assert "claude-code" in LLMService._adapter_registry


def test_service_builds_keyless():
    from lingtai.llm.service import LLMService

    svc = LLMService(provider="claude-code", model="sonnet", api_key=None)
    assert isinstance(svc.get_adapter("claude-code"), ClaudeCodeAdapter)


# ---------------------------------------------------------------------------
# send(): tool call / final / tool results
# ---------------------------------------------------------------------------


def test_send_returns_tool_call():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    out = _envelope('{"action":"tool_call","name":"get_weather","input":{"city":"Paris"}}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out)):
        resp = sess.send("weather in paris?")
    assert resp.text == ""
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.name == "get_weather" and tc.args == {"city": "Paris"}
    assert tc.id and tc.id.startswith("cc_")
    # usage mapping: input includes cache read + creation
    assert resp.usage.input_tokens == 160
    assert resp.usage.output_tokens == 20
    assert resp.usage.cached_tokens == 50


def test_send_returns_final_text():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    out = _envelope('{"action":"final","text":"It is sunny."}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out)):
        resp = sess.send("hi")
    assert resp.tool_calls == []
    assert resp.text == "It is sunny."


def test_non_json_reply_falls_back_to_final_text():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    out = _envelope("just some prose, no json")
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out)):
        resp = sess.send("hi")
    assert resp.tool_calls == []
    assert resp.text == "just some prose, no json"


def test_parallel_tool_calls():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    out = _envelope(
        '{"action":"tool_calls","calls":[{"name":"get_weather","input":{"city":"A"}},'
        '{"name":"get_weather","input":{"city":"B"}}]}'
    )
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out)):
        resp = sess.send("two cities")
    assert [c.args["city"] for c in resp.tool_calls] == ["A", "B"]
    assert resp.tool_calls[0].id != resp.tool_calls[1].id


def test_tool_result_roundtrip_updates_interface():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    out1 = _envelope('{"action":"tool_call","name":"get_weather","input":{"city":"Paris"}}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out1)):
        r1 = sess.send("weather?")
    tc = r1.tool_calls[0]
    tr = ad.make_tool_result_message("get_weather", {"temp_c": 18}, tool_call_id=tc.id)
    assert tr.id == tc.id
    out2 = _envelope('{"action":"final","text":"18C"}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out2)):
        r2 = sess.send([tr])
    assert r2.text == "18C"
    roles = [e.role for e in sess.interface._entries]
    assert roles == ["system", "user", "assistant", "user", "assistant"]


# ---------------------------------------------------------------------------
# Command line + environment
# ---------------------------------------------------------------------------


def test_command_includes_print_json_model_and_disallowed_tools():
    ad = ClaudeCodeAdapter(model="opus")
    sess = ad.create_chat("opus", "sys", None)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("hi")
    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd and "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd and "opus" in cmd
    assert "--disallowedTools" in cmd and "Bash" in cmd
    # prompt is piped via stdin, not argv
    assert captured["kw"]["input"]
    assert "AVAILABLE TOOLS" in captured["kw"]["input"]


def test_env_strips_api_keys_but_keeps_oauth_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-keep")
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    captured = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw["env"]
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("hi")
    env = captured["env"]
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "oauth-keep"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_cli_raises_auth_error():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(ClaudeCodeAuthError):
            sess.send("hi")


def test_not_logged_in_raises_auth_error():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    proc = _FakeProc(stdout="", stderr="Please run /login to authenticate", returncode=1)
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=proc):
        with pytest.raises(ClaudeCodeAuthError):
            sess.send("hi")


def test_context_overflow_detected_from_stderr():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    proc = _FakeProc(stdout="", stderr="Error: prompt is too long for this model", returncode=1)
    # Overflow recovery will try to trim; with a tiny interface it can't, so it re-raises.
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=proc):
        with pytest.raises(ClaudeCodeContextOverflow):
            sess.send("hi")


def test_generic_cli_error_raises():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    proc = _FakeProc(stdout="", stderr="some unexpected failure", returncode=2)
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=proc):
        with pytest.raises(ClaudeCodeError):
            sess.send("hi")


def test_is_quota_error():
    ad = ClaudeCodeAdapter(model="sonnet")
    assert ad.is_quota_error(Exception("hit usage limit")) is True
    assert ad.is_quota_error(Exception("429 too many requests")) is True
    assert ad.is_quota_error(Exception("some other error")) is False
