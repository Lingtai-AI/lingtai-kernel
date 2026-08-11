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
from lingtai.kernel.llm.base import FunctionSchema
from lingtai.kernel.llm.interface import TextBlock, ToolCallBlock, ToolResultBlock
from lingtai.tools.context import _rebuild_action, _summarize_action
from lingtai.tools.system.summarize import SUMMARY_STATUS_DONE, SUMMARY_STATUS_PENDING


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


class _RebuildAgent:
    def __init__(self, chat, *, system_prompt="sys", tools=None):
        self._chat = chat
        self._system_prompt = system_prompt
        self._tools = list(tools or [])
        self._summarize_notification_threshold = 3000
        self.saved_history = []
        self.logs = []

    def _reconstruct_context(self):
        self._chat.update_system_prompt(self._system_prompt)
        self._chat.update_tools(self._tools)

    def _save_chat_history(self, **kwargs):
        self.saved_history.append(kwargs)

    def _log(self, event, **kwargs):
        self.logs.append((event, kwargs))


def _summarize_marker(iface, tool_call_id):
    for entry in iface._entries:
        for block in entry.content:
            if isinstance(block, ToolResultBlock) and block.id == tool_call_id:
                return block.content
    raise AssertionError(f"missing marker for {tool_call_id}")


def _append_context_call_result(sess, content):
    call_id = "ctx-rebuild"
    sess.interface.add_assistant_message(
        [ToolCallBlock(id=call_id, name="context", args={"action": "rebuild"})]
    )
    return ToolResultBlock(id=call_id, name="context", content=content)


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

# Exact provider names, including both ``claude-code`` spellings, and their
# concrete classes are owned by
# ``tests/contracts/llm_conversation_input/test_regime_inventory.py``'s registry
# matrix. Keep registration assertions there rather than regrowing a weaker
# provider-local name-only copy.


def test_service_builds_keyless():
    from lingtai.llm.service import LLMService

    svc = LLMService(provider="claude-code", model="sonnet", api_key=None)
    assert isinstance(svc.get_adapter("claude-code"), ClaudeCodeAdapter)


def test_service_builds_keyless_underscore_alias():
    from lingtai.llm.service import LLMService

    svc = LLMService(provider="claude_code", model="sonnet", api_key=None)
    assert isinstance(svc.get_adapter("claude_code"), ClaudeCodeAdapter)


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
    # Stable context (protocol/system/tools) lives in the system-prompt file
    # passed via --system-prompt-file (for prompt caching); the stdin
    # user message carries only the conversation.
    assert "# CONVERSATION" in captured["kw"]["input"]
    assert "# NOW" in captured["kw"]["input"]
    assert "# AVAILABLE TOOLS" not in captured["kw"]["input"]
    assert "--system-prompt-file" in cmd
    assert "--append-system-prompt-file" not in cmd
    sp_idx = cmd.index("--system-prompt-file")
    sp_path = cmd[sp_idx + 1]
    with open(sp_path, encoding="utf-8") as f:
        sp_content = f.read()
    # The system block carries protocol + system prompt + tools.
    assert "# AVAILABLE TOOLS" in sp_content
    assert "(no tools available" in sp_content  # create_chat called with None
    assert "sys" in sp_content  # the system prompt text
    assert "You are the REASONING CORE" in sp_content  # the action protocol


def test_append_system_prompt_mode_keeps_legacy_flag():
    ad = ClaudeCodeAdapter(model="sonnet", system_prompt_mode="append")
    sess = ad.create_chat("sonnet", "sys", None)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("hi")

    assert "--append-system-prompt-file" in captured["cmd"]
    assert "--system-prompt-file" not in captured["cmd"]


def test_invalid_system_prompt_mode_fails_closed():
    with pytest.raises(ValueError, match="system_prompt_mode"):
        ClaudeCodeAdapter(system_prompt_mode="discard")


def test_session_resume_keeps_system_block_stable_and_sends_only_new_history():
    """The CLI session owns prior turns while the canonical interface remains local.

    The first command creates a normal CLI session. Later commands resume the
    returned session ID and send only entries committed after the preceding
    successful response, so history is neither duplicated remotely nor rewritten
    in the mutable stdin prompt.
    """
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    captured = []

    def fake_run(cmd, **kw):
        captured.append((cmd, kw))
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("first message")
        sess.send("second message")
        sess.send("third message")

    assert len(captured) == 3
    assert "--resume" not in captured[0][0]
    for cmd, _kw in captured[1:]:
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "sess-123"

    sp_paths = set()
    for cmd, _kw in captured:
        sp_idx = cmd.index("--system-prompt-file")
        sp_paths.add(cmd[sp_idx + 1])
    assert len(sp_paths) == 1  # same stable file every turn

    contents = []
    for cmd, _kw in captured:
        sp_idx = cmd.index("--system-prompt-file")
        with open(cmd[sp_idx + 1], encoding="utf-8") as f:
            contents.append(f.read())
    assert contents[0] == contents[1] == contents[2]  # byte-identical system block

    stdins = [kw["input"] for _cmd, kw in captured]
    assert "first message" in stdins[0]
    assert "second message" in stdins[1] and "first message" not in stdins[1]
    assert "third message" in stdins[2] and "second message" not in stdins[2]
    assert all("# CONVERSATION" in stdin for stdin in stdins)


def test_per_turn_resync_with_identical_content_keeps_resuming():
    """Regression: the per-turn system/tool resync must not drop the CLI session.

    ``SessionManager.send`` rebuilds the system prompt and tools before *every*
    turn because they may have changed. When the rebuilt content is identical
    that resync must stay inert; an unconditional reset here cleared
    ``_remote_session_id`` before each call, so ``--resume`` was never sent and
    every turn replayed the whole conversation into a cold cache.
    """
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    captured = []

    def fake_run(cmd, **kw):
        captured.append((cmd, kw))
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        for i in range(4):
            # Exactly what SessionManager.send does each turn, same content.
            sess.update_system_prompt("sys")
            sess.update_tools([_weather_tool()])
            sess.send(f"message {i}")

    assert "--resume" not in captured[0][0]
    for cmd, _kw in captured[1:]:
        assert cmd[cmd.index("--resume") + 1] == "sess-123"

    # The cached system block stays byte-identical across the resyncs.
    contents = []
    for cmd, _kw in captured:
        with open(cmd[cmd.index("--system-prompt-file") + 1], encoding="utf-8") as f:
            contents.append(f.read())
    assert len(set(contents)) == 1


def test_changed_system_prompt_or_tools_resets_remote_session():
    """A real content change must still invalidate the CLI session.

    The resumed conversation carries the old system block, so continuing it
    after a genuine change would run the turn against stale context.
    """
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    captured = []

    def fake_run(cmd, **kw):
        captured.append((cmd, kw))
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    other_tool = FunctionSchema(
        name="other", description="o", parameters={"type": "object", "properties": {}}
    )

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.update_system_prompt("sys")
        sess.send("m0")
        sess.update_system_prompt("sys")
        sess.send("m1")
        sess.update_system_prompt("CHANGED")  # real system-prompt change
        sess.send("m2")
        sess.update_system_prompt("CHANGED")
        sess.send("m3")
        sess.update_tools([other_tool])  # real tool change
        sess.send("m4")

    resumed = ["--resume" in cmd for cmd, _kw in captured]
    assert resumed == [False, True, False, True, False]


def test_resume_survives_a_tool_call_round_trip():
    """The tool-result turn shape must resume like any other turn.

    Real agent turns are ``send(user) -> tool_call -> send(tool_results)``; the
    prior resume tests only exercised plain string sends.
    """
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    captured = []
    replies = [
        '{"action":"tool_call","name":"get_weather","arguments":{"city":"SF"}}',
        '{"action":"final","text":"sunny"}',
    ]

    def fake_run(cmd, **kw):
        captured.append((cmd, kw))
        return _FakeProc(stdout=_envelope(replies[len(captured) - 1]))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        resp = sess.send("what is the weather in SF?")
        assert resp.tool_calls
        sess.update_system_prompt("sys")
        sess.update_tools([_weather_tool()])
        sess.send([
            ToolResultBlock(
                id=resp.tool_calls[0].id, name="get_weather", content="sunny, 20C"
            ),
        ])

    assert "--resume" not in captured[0][0]
    assert captured[1][0][captured[1][0].index("--resume") + 1] == "sess-123"
    # The resumed turn carries only the new tool result, not the original ask.
    assert "sunny, 20C" in captured[1][1]["input"]
    assert "what is the weather in SF?" not in captured[1][1]["input"]


def test_explicit_rebuild_on_resumed_session_replays_summarized_history():
    ad = ClaudeCodeAdapter(model="sonnet")
    tool = _weather_tool()
    sess = ad.create_chat("sonnet", "sys", [tool])
    agent = _RebuildAgent(sess, system_prompt="sys", tools=[tool])
    raw_sentinel = "RAW-CLAUDE-REBUILD-SENTINEL"
    summary_sentinel = "SUMMARY-CLAUDE-REBUILD-SENTINEL"
    captured = []
    replies = [
        '{"action":"tool_call","name":"get_weather","input":{"city":"SF"}}',
        '{"action":"final","text":"recorded"}',
        '{"action":"final","text":"rebuilt"}',
    ]

    def fake_run(cmd, **kw):
        captured.append((cmd, kw))
        return _FakeProc(stdout=_envelope(replies[len(captured) - 1]))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        response = sess.send("collect raw")
        tool_call_id = response.tool_calls[0].id
        sess.send([
            ToolResultBlock(
                id=tool_call_id,
                name="get_weather",
                content={"raw": raw_sentinel},
            )
        ])

        summarize = _summarize_action(
            agent,
            {"items": [{"tool_call_id": tool_call_id, "summary": summary_sentinel}]},
        )
        assert summarize["status"] == "ok"
        assert _summarize_marker(sess.interface, tool_call_id)["status"] == SUMMARY_STATUS_PENDING

        rebuild = _rebuild_action(agent, {})
        assert rebuild["status"] == "ok"
        assert rebuild["rebuild_requested"] is True
        assert rebuild["marked_done"] == [tool_call_id]
        assert _summarize_marker(sess.interface, tool_call_id)["status"] == SUMMARY_STATUS_DONE
        assert sess._remote_session_id is None
        assert sess._remote_entry_count == 0

        sess.send([_append_context_call_result(sess, rebuild)])

    assert "--resume" not in captured[0][0]
    assert "--resume" in captured[1][0]
    assert "--resume" not in captured[2][0]
    replay_prompt = captured[2][1]["input"]
    assert summary_sentinel in replay_prompt
    assert raw_sentinel not in replay_prompt
    assert "Rebuild successful" in replay_prompt


def test_explicit_zero_pending_rebuild_clears_resumed_session():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    agent = _RebuildAgent(sess)
    captured = []

    def fake_run(cmd, **kw):
        captured.append((cmd, kw))
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("first")
        result = _rebuild_action(agent, {})
        assert result["status"] == "ok"
        assert result["rebuild_requested"] is True
        assert result["marked_done"] == []
        assert sess._remote_session_id is None
        sess.send([_append_context_call_result(sess, result)])

    assert "--resume" not in captured[0][0]
    assert "--resume" not in captured[1][0]
    assert "first" in captured[1][1]["input"]


def test_explicit_rebuild_without_remote_id_is_accepted():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)

    assert sess._remote_session_id is None
    assert sess.request_history_rebuild() is True
    assert sess._remote_session_id is None
    assert sess._remote_entry_count == 0


def test_generate_omits_system_prompt_file():
    """One-shot generate() must not reuse (or poison) the chat cache file."""
    ad = ClaudeCodeAdapter(model="sonnet")
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc(stdout=_envelope("plain answer"))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        resp = ad.generate("sonnet", "hello", system_prompt="sys")
    assert resp.text == "plain answer"
    assert "--system-prompt-file" not in captured["cmd"]


def test_update_system_prompt_rewrites_system_file():
    """Changing the system prompt must refresh the cached system block."""
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys-v1", [_weather_tool()])
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc(stdout=_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("hi")
    sp_idx = captured["cmd"].index("--system-prompt-file")
    sp_path = captured["cmd"][sp_idx + 1]
    with open(sp_path, encoding="utf-8") as f:
        assert "sys-v1" in f.read()

    sess.update_system_prompt("sys-v2")
    with open(sp_path, encoding="utf-8") as f:
        assert "sys-v2" in f.read()


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


# ---------------------------------------------------------------------------
# Interface rollback: a failed turn must not leave the just-added user /
# tool-result message in the canonical history.
# ---------------------------------------------------------------------------


def _entry_kinds(interface):
    """(role, [block-type-name...]) per entry — easy to compare in asserts."""
    return [
        (e.role, [type(b).__name__ for b in e.content])
        for e in interface._entries
    ]


def test_failed_cli_rolls_back_added_user_message():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    before = _entry_kinds(sess.interface)
    before_len = len(sess.interface._entries)

    proc = _FakeProc(stdout="", stderr="some unexpected failure", returncode=2)
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=proc):
        with pytest.raises(ClaudeCodeError):
            sess.send("weather in paris?")

    # The user message added at the top of send() must be gone again.
    assert len(sess.interface._entries) == before_len
    assert _entry_kinds(sess.interface) == before
    assert not sess.interface.has_pending_tool_calls()


def test_failed_cli_rolls_back_added_tool_results():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    # Turn 1: succeed and leave a pending tool call.
    out1 = _envelope('{"action":"tool_call","name":"get_weather","input":{"city":"Paris"}}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out1)):
        r1 = sess.send("weather?")
    tc = r1.tool_calls[0]
    snapshot = _entry_kinds(sess.interface)
    snap_len = len(sess.interface._entries)

    # Turn 2: deliver the tool result, but the CLI fails. The tool-result
    # user entry must not survive the failure.
    tr = ad.make_tool_result_message("get_weather", {"temp_c": 18}, tool_call_id=tc.id)
    proc = _FakeProc(stdout="", stderr="some unexpected failure", returncode=2)
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=proc):
        with pytest.raises(ClaudeCodeError):
            sess.send([tr])

    assert len(sess.interface._entries) == snap_len
    assert _entry_kinds(sess.interface) == snapshot


def test_pre_request_hook_failure_rolls_back():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    before = _entry_kinds(sess.interface)
    before_len = len(sess.interface._entries)

    def boom(_interface):
        raise RuntimeError("hook exploded")

    sess.pre_request_hook = boom
    # subprocess.run must never be reached, but patch it so a leak would be loud.
    with patch("lingtai.llm.claude_code.adapter.subprocess.run") as run:
        with pytest.raises(RuntimeError, match="hook exploded"):
            sess.send("hi")
        run.assert_not_called()

    assert len(sess.interface._entries) == before_len
    assert _entry_kinds(sess.interface) == before


def test_successful_send_does_not_roll_back():
    """Guard: the rollback path must not fire on the happy path."""
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", [_weather_tool()])
    out = _envelope('{"action":"final","text":"sunny"}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out)):
        sess.send("hi")
    roles = [e.role for e in sess.interface._entries]
    assert roles == ["system", "user", "assistant"]


# ---------------------------------------------------------------------------
# Overflow recovery: a successful recovery must inject a [kernel] notice and
# still return the final assistant response.
# ---------------------------------------------------------------------------


def _seed_conversation(sess, turns=3):
    """Run a few successful final turns so the interface has trimmable history."""
    out = _envelope('{"action":"final","text":"ok"}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out)):
        for i in range(turns):
            sess.send(f"message {i}")


def test_successful_overflow_recovery_injects_notice():
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    _seed_conversation(sess, turns=3)
    before = len(sess.interface._entries)
    assert before > 2  # enough non-system entries that a trim can drop one

    overflow = _FakeProc(stdout="", stderr="Error: prompt is too long", returncode=1)
    success = _FakeProc(stdout=_envelope('{"action":"final","text":"recovered"}'))
    # First CLI call overflows; after the kernel trims, the retry succeeds.
    with patch(
        "lingtai.llm.claude_code.adapter.subprocess.run",
        side_effect=[overflow, success],
    ):
        resp = sess.send("the message that overflows")

    # The final response is still returned.
    assert resp.text == "recovered"
    assert resp.tool_calls == []

    # A [kernel] overflow notice was injected as a user entry, and it sits
    # before the recorded assistant response (notice, then assistant).
    texts = [
        b.text
        for e in sess.interface._entries
        for b in e.content
        if isinstance(b, TextBlock)
    ]
    notice = [t for t in texts if t.startswith("[kernel] Context exceeded")]
    assert len(notice) == 1
    # Tail is the assistant response; the entry just before it is the notice.
    assert sess.interface._entries[-1].role == "assistant"
    assert sess.interface._entries[-2].role == "user"
    assert sess.interface._entries[-2].content[0].text.startswith("[kernel] Context exceeded")

    # The local notice was added after the successful fresh retry, so the next
    # request must rebuild canonical history rather than resume divergent remote
    # history that never received that notice.
    next_call = {}

    def capture_next(cmd, **kw):
        next_call["cmd"] = cmd
        next_call["input"] = kw["input"]
        return _FakeProc(stdout=_envelope('{"action":"final","text":"after recovery"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=capture_next):
        sess.send("after recovery")
    assert "--resume" not in next_call["cmd"]
    assert "[kernel] Context exceeded" in next_call["input"]


def test_no_overflow_means_no_notice():
    """Guard: a clean turn (0 rounds) injects no overflow notice."""
    ad = ClaudeCodeAdapter(model="sonnet")
    sess = ad.create_chat("sonnet", "sys", None)
    out = _envelope('{"action":"final","text":"fine"}')
    with patch("lingtai.llm.claude_code.adapter.subprocess.run", return_value=_FakeProc(stdout=out)):
        sess.send("hi")
    texts = [
        b.text
        for e in sess.interface._entries
        for b in e.content
        if isinstance(b, TextBlock)
    ]
    assert not any(t.startswith("[kernel] Context exceeded") for t in texts)
