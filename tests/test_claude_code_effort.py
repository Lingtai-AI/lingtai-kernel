"""Claude Code configured-effort contract (issue #1197).

The installed ``claude`` CLI exposes ``--effort <low|medium|high|xhigh|max>``
for the current session. This suite pins the LingTai side of that contract:

* an **omitted** manifest/preset ``thinking`` stays omitted — no ``--effort``
  flag reaches the CLI, byte-identical to the pre-contract command;
* an **explicit** five-value level is frozen at chat creation and re-emitted on
  every physical CLI invocation of that session, first call and ``--resume``
  alike;
* anything outside the exact five-value vocabulary is rejected *before*
  dispatch.

The CLI subprocess is always mocked; no real ``claude`` process is started.
"""

import json
from unittest.mock import patch

import pytest

from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter
from lingtai.llm.service import LLMService
from lingtai.kernel.llm.base import FunctionSchema


CLAUDE_FIVE_VALUES = ("low", "medium", "high", "xhigh", "max")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _envelope(result_str='{"action":"final","text":"ok"}', *, session_id="sess-123"):
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_str,
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 2},
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


class _CapturingRun:
    """Drop-in for ``subprocess.run`` that records every command list."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        return _FakeProc(stdout=_envelope())

    @property
    def commands(self):
        return [cmd for cmd, _kw in self.calls]


def _effort_values(cmd):
    """Every value following an ``--effort`` token in *cmd*."""
    return [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--effort"]


def _service_session(thinking, *, provider="claude-code", model="opus", tools=None):
    """Create a chat through the registered provider factory + LLMService."""
    service = LLMService(provider=provider, model=model)
    return service.create_session(
        system_prompt="sys",
        tools=tools,
        model=model,
        thinking=thinking,
        provider=provider,
    )


# ---------------------------------------------------------------------------
# Phase A — explicit configured effort must reach the CLI
# ---------------------------------------------------------------------------


def test_explicit_thinking_max_emits_effort_flag_on_first_command():
    """A factory-created claude-code session with thinking='max' must send it.

    RED before the contract: ``ClaudeCodeAdapter.create_chat`` accepts the
    generic ``thinking`` argument and discards it, so no ``--effort`` token is
    ever constructed.
    """
    sess = _service_session("max")
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    cmd = run.commands[0]
    assert "--effort" in cmd, f"no --effort in {cmd}"
    assert _effort_values(cmd) == ["max"]


@pytest.mark.parametrize("value", CLAUDE_FIVE_VALUES)
def test_every_claude_level_reaches_the_cli_exactly_once(value):
    sess = _service_session(value)
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    assert _effort_values(run.commands[0]) == [value]


# ---------------------------------------------------------------------------
# Phase A — omission stays omission (preservation)
# ---------------------------------------------------------------------------


def test_omitted_thinking_builds_the_pre_contract_command():
    """Omission must remain byte-identical to today's command.

    ``LLMService.create_session`` defaults ``thinking`` to the internal
    ``"default"`` omission sentinel; the Claude route must translate that to
    *no flag at all*, not to an upstream-default guess.
    """
    sess = _service_session("default", tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    cmd = run.commands[0]
    assert "--effort" not in cmd
    # The rest of the command is exactly the pre-contract shape.
    assert cmd[0] == "claude"
    assert cmd[1:4] == ["-p", "--output-format", "json"]
    assert "--model" in cmd and "opus" in cmd
    assert "--disallowedTools" in cmd
    assert "--append-system-prompt-file" in cmd


def test_direct_adapter_with_thinking_none_builds_the_pre_contract_command():
    """An unregistered/direct adapter with no contract injected stays inert."""
    ad = ClaudeCodeAdapter(model="sonnet")
    baseline = ad.create_chat("sonnet", "sys", None)
    contracted = ad.create_chat("sonnet", "sys", None, thinking=None)
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        baseline.send("hi")
        contracted.send("hi")

    assert "--effort" not in run.commands[0]
    assert run.commands[0] == run.commands[1]


# ---------------------------------------------------------------------------
# Phase C — resume, immutability, overflow, observability, rejection
# ---------------------------------------------------------------------------


def test_resumed_invocations_repeat_the_same_effort():
    """Every physical invocation carries the flag — first call and --resume."""
    sess = _service_session("xhigh", tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        sess.send("first")
        sess.send("second")
        sess.send("third")

    first, *resumed = run.commands
    assert "--resume" not in first
    assert _effort_values(first) == ["xhigh"]
    for cmd in resumed:
        assert cmd[cmd.index("--resume") + 1] == "sess-123"
        assert _effort_values(cmd) == ["xhigh"]


def test_effort_does_not_disturb_remote_session_or_stable_context():
    """The effort flag rides along; it must not reset resume or rewrite the
    cached system block."""
    plain = _service_session("default", tools=[_weather_tool()])
    effortful = _service_session("low", tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        for sess in (plain, effortful):
            sess.send("first")
            sess.send("second")

    plain_cmds, effort_cmds = run.commands[:2], run.commands[2:]
    # Same resume acceleration on both.
    assert "--resume" not in plain_cmds[0] and "--resume" not in effort_cmds[0]
    for cmds in (plain_cmds, effort_cmds):
        assert cmds[1][cmds[1].index("--resume") + 1] == "sess-123"
    # One stable system-prompt file per session, unchanged across turns.
    for cmds in (plain_cmds, effort_cmds):
        paths = {cmd[cmd.index("--append-system-prompt-file") + 1] for cmd in cmds}
        assert len(paths) == 1
    # Removing the two --effort tokens yields exactly the omitted command.
    def _shape(cmd):
        without_effort = cmd
        if "--effort" in cmd:
            idx = cmd.index("--effort")
            without_effort = cmd[:idx] + cmd[idx + 2 :]
        sp = without_effort.index("--append-system-prompt-file")
        return without_effort[:sp] + without_effort[sp + 2 :]

    assert [_shape(c) for c in effort_cmds] == [_shape(c) for c in plain_cmds]
    assert _effort_values(effort_cmds[1]) == ["low"]


def test_effort_is_frozen_and_not_adapter_global_state():
    """Two chats from one adapter keep independent, immutable decisions."""
    ad = ClaudeCodeAdapter(model="sonnet")
    low = ad.create_chat("sonnet", "sys", None, thinking="low")
    omitted = ad.create_chat("sonnet", "sys", None)
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        low.send("a")
        omitted.send("a")
        low.send("b")

    assert _effort_values(run.commands[0]) == ["low"]
    assert "--effort" not in run.commands[1]
    assert _effort_values(run.commands[2]) == ["low"]
    # The decision object itself is immutable and never leaks into adapter argv.
    with pytest.raises(Exception):
        low._effort.level = "max"
    assert ad._extra_argv == []


def test_overflow_recovery_reuses_one_frozen_snapshot():
    """Every physical invocation of ONE logical send carries the same effort."""
    sess = _service_session("high")
    # Enough trimmable history that overflow recovery can retry rather than
    # give up immediately.
    with patch(
        "lingtai.llm.claude_code.adapter.subprocess.run",
        return_value=_FakeProc(stdout=_envelope()),
    ):
        for i in range(3):
            sess.send(f"message {i}")

    commands = []
    overflow = _FakeProc(stdout="", stderr="Error: prompt is too long", returncode=1)
    success = _FakeProc(stdout=_envelope())
    replies = [overflow, success]

    def fake_run(cmd, **kw):
        commands.append(list(cmd))
        return replies.pop(0)

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("the message that overflows")

    assert len(commands) == 2
    assert [_effort_values(cmd) for cmd in commands] == [["high"], ["high"]]


@pytest.mark.parametrize(
    "value",
    ["none", "minimal", "High", "MAX", " high", "high ", "", "ultra", 1, True, 0.5, []],
)
def test_invalid_effort_rejected_before_any_dispatch(value):
    ad = ClaudeCodeAdapter(model="sonnet")

    with patch("lingtai.llm.claude_code.adapter.subprocess.run") as run:
        with pytest.raises(ValueError) as excinfo:
            ad.create_chat("sonnet", "sys", None, thinking=value)

    run.assert_not_called()
    message = str(excinfo.value)
    # The offered vocabulary is exactly the five CLI levels — the Responses
    # values ``none``/``minimal`` are never presented as valid alternatives.
    assert ", ".join(CLAUDE_FIVE_VALUES) in message, message
    offered = message.split("exactly one of ", 1)[1].split(" (omit", 1)[0]
    assert offered.split(", ") == list(CLAUDE_FIVE_VALUES), offered


def test_rejection_message_does_not_echo_an_unbounded_value():
    ad = ClaudeCodeAdapter(model="sonnet")
    blob = "x" * 5000

    with pytest.raises(ValueError) as excinfo:
        ad.create_chat("sonnet", "sys", None, thinking=blob)

    assert blob not in str(excinfo.value)
    assert len(str(excinfo.value)) < 400


def test_llm_call_observability_fields_for_explicit_and_omitted():
    explicit = _service_session("max").reasoning_observability()
    assert explicit == {
        "reasoning_requested": "max",
        "reasoning_normalized": "max",
        "reasoning_actual": "max",
        "reasoning_source": "explicit_config",
        "reasoning_capability_source": "claude_cli_2.1.220_help",
    }

    omitted = _service_session("default").reasoning_observability()
    assert omitted == {
        "reasoning_requested": "omitted",
        "reasoning_normalized": "omitted",
        "reasoning_actual": "omitted",
        "reasoning_source": "lingtai_claude_omitted",
        "reasoning_capability_source": "claude_cli_2.1.220_help",
    }


def test_observability_survives_the_rate_gate_proxy():
    """A gated session must not silently drop the frozen decision."""
    ad = ClaudeCodeAdapter(model="sonnet", max_rpm=600)
    gated = ad.create_chat("sonnet", "sys", None, thinking="medium")

    assert type(gated).__name__ == "_GatedSession"
    assert gated.reasoning_observability()["reasoning_actual"] == "medium"


def _session_manager(provider, model, *, thinking=None, agent_config=None):
    """A real SessionManager over a real LLMService, with a capturing logger."""
    from lingtai.kernel.config import AgentConfig
    from lingtai.kernel.session import SessionManager

    if agent_config is None:
        kwargs = {"provider": provider, "model": model}
        if thinking is not None:
            kwargs["thinking"] = thinking
        agent_config = AgentConfig(**kwargs)
    events = []
    manager = SessionManager(
        llm_service=LLMService(provider=provider, model=model),
        config=agent_config,
        agent_name="probe",
        streaming=False,
        build_system_prompt_fn=lambda: "sys",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event_type, **fields: events.append((event_type, fields)),
    )
    return manager, events


def _llm_call_fields(events):
    return [fields for kind, fields in events if kind == "llm_call"]


def test_llm_call_record_gains_exactly_the_safe_fields_on_a_real_send():
    """End-to-end through SessionManager.send, not just the helper."""
    manager, events = _session_manager("claude-code", "opus", thinking="xhigh")
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        manager.send("hi")

    (fields,) = _llm_call_fields(events)
    assert set(fields) == {
        "model",
        "api_call_id",
        "reasoning_requested",
        "reasoning_normalized",
        "reasoning_actual",
        "reasoning_source",
        "reasoning_capability_source",
    }
    assert fields["model"] == "opus"
    assert fields["reasoning_actual"] == "xhigh"
    assert fields["reasoning_source"] == "explicit_config"
    # The frozen decision really did reach the wire on this same send.
    assert _effort_values(run.commands[0]) == ["xhigh"]


def test_llm_call_record_is_unchanged_for_a_provider_without_the_hook():
    """A non-Claude session keeps exactly the two pre-contract fields."""
    from lingtai.kernel.llm.base import LLMResponse, UsageMetadata
    from lingtai.kernel.llm.interface import ChatInterface

    class _PlainSession:
        session_id = ""
        interface = ChatInterface()
        pre_request_hook = None

        def update_system_prompt(self, prompt):
            pass

        def update_system_prompt_batches(self, batches):
            pass

        def update_tools(self, tools):
            pass

        def send(self, message):
            return LLMResponse(text="ok", usage=UsageMetadata())

    manager, events = _session_manager("claude-code", "opus")
    manager._chat = _PlainSession()
    manager.send("hi")

    (fields,) = _llm_call_fields(events)
    assert set(fields) == {"model", "api_call_id"}


def test_omitted_claude_config_logs_omitted_and_sends_no_flag():
    """The real production shape: AgentConfig built from a Claude manifest with
    no thinking must log ``omitted`` and construct no ``--effort``."""
    from lingtai.agent import build_agent_config

    config = build_agent_config(
        {"llm": {"provider": "claude-code", "model": "opus"}}, max_rpm=0
    )
    manager, events = _session_manager("claude-code", "opus", agent_config=config)
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        manager.send("hi")

    (fields,) = _llm_call_fields(events)
    assert fields["reasoning_actual"] == "omitted"
    assert fields["reasoning_source"] == "lingtai_claude_omitted"
    assert "--effort" not in run.commands[0]


def test_constructor_omitted_claude_config_does_not_become_effort_high():
    """A bare AgentConfig() against a Claude service must stay omitted — this
    is the silent-behavior-change the sentinel exists to prevent."""
    from lingtai.kernel.config import AgentConfig

    manager, events = _session_manager(
        "claude-code", "opus", agent_config=AgentConfig()
    )
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        manager.send("hi")

    assert "--effort" not in run.commands[0]
    assert _llm_call_fields(events)[0]["reasoning_actual"] == "omitted"


def test_session_manager_merges_fields_only_for_providers_that_offer_them():
    """A session with no hook leaves the llm_call record exactly as before."""
    from lingtai.kernel.session import _reasoning_observability_fields

    class _NoHook:
        pass

    class _Raising:
        def reasoning_observability(self):
            raise RuntimeError("boom")

    class _NotADict:
        def reasoning_observability(self):
            return ["nope"]

    assert _reasoning_observability_fields(_NoHook()) == {}
    assert _reasoning_observability_fields(_Raising()) == {}
    assert _reasoning_observability_fields(_NotADict()) == {}
    assert _reasoning_observability_fields(
        _service_session("low")
    )["reasoning_actual"] == "low"


# ---------------------------------------------------------------------------
# Phase D — isolation and negative evidence
# ---------------------------------------------------------------------------


def test_generate_one_shot_never_emits_effort():
    """``generate`` has no session contract; its command stays pre-contract."""
    ad = ClaudeCodeAdapter(model="sonnet")
    # A chat with an explicit effort exists on the same adapter — the one-shot
    # path must not pick it up.
    ad.create_chat("sonnet", "sys", None, thinking="max")
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        ad.generate("sonnet", "hello", system_prompt="sys")

    cmd = run.commands[0]
    assert "--effort" not in cmd
    assert "--append-system-prompt-file" not in cmd


@pytest.mark.parametrize("value", ["default", "max"])
def test_both_provider_spellings_behave_identically(value):
    dashed = _service_session(value, provider="claude-code")
    underscored = _service_session(value, provider="claude_code")
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        dashed.send("hi")
        underscored.send("hi")

    a, b = run.commands
    assert _effort_values(a) == _effort_values(b)
    # Same argv shape apart from the per-adapter system-prompt file path.
    def _shape(cmd):
        idx = cmd.index("--append-system-prompt-file")
        return cmd[:idx] + cmd[idx + 2 :]

    assert _shape(a) == _shape(b)


def test_effort_does_not_disturb_auth_env_or_disallowed_tools(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-keep")
    sess = _service_session("max")
    run = _CapturingRun()

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    cmd, kw = run.calls[0]
    assert "ANTHROPIC_API_KEY" not in kw["env"]
    assert kw["env"].get("CLAUDE_CODE_OAUTH_TOKEN") == "oauth-keep"
    # --effort lands after the infrastructure flags, never inside the
    # variadic --disallowedTools list.
    assert cmd.index("--effort") > cmd.index("--append-system-prompt-file")
    assert cmd.index("--disallowedTools") < cmd.index("--append-system-prompt-file")
    # No credential or prompt content reaches argv.
    assert not any("secret" in tok or "oauth-keep" in tok for tok in cmd)


def test_agent_config_omission_provenance_is_provider_scoped():
    """Omission must not become an explicit flag for Claude, and must stay
    exactly ``high`` for every legacy provider."""
    from lingtai.agent import build_agent_config
    from lingtai.kernel.config import AgentConfig

    def _thinking(provider, **llm):
        manifest = {"llm": {"provider": provider, "model": "m", **llm}}
        return build_agent_config(manifest, max_rpm=0).thinking

    assert _thinking("claude-code") == "default"
    assert _thinking("claude_code") == "default"
    assert _thinking("claude-code", thinking="max") == "max"
    assert _thinking("codex") == "default"
    assert _thinking("anthropic") == "high"
    assert _thinking("openai") == "high"

    # Constructor-omitted resolution keeps the historical default surface.
    assert AgentConfig().thinking == "high"
    assert AgentConfig(provider="anthropic").thinking == "high"
    assert AgentConfig(provider="claude-code").thinking == "default"
    assert AgentConfig(provider="claude-code", thinking="max").thinking == "max"
    assert AgentConfig(thinking="high").thinking_omitted is False
    assert AgentConfig().thinking_omitted is True
