"""Kimi Code configured-effort contract (issue #1197).

The Kimi coding service exposes reasoning effort through the
``KIMI_MODEL_THINKING_EFFORT`` environment variable rather than a CLI flag,
over the K3 coding vocabulary ``low|high|max``. This suite pins the LingTai
side of that contract:

* an **omitted** manifest/preset ``thinking`` stays omitted — no
  ``KIMI_MODEL_THINKING_EFFORT`` is injected, byte-identical to the
  pre-contract invocation (critical: the default ``kimi-for-coding`` model is
  always-thinking and would reject an effort);
* an **explicit** level is frozen at chat creation and re-applied to the
  private env of every physical CLI invocation of that session — first call,
  ``--session`` resumed call, and each overflow-recovery retry;
* the model capability gate **fails closed**: explicit effort is accepted only
  for the known effort-capable K3 coding ids, always-thinking ids raise, and
  an unknown id raises rather than being optimistically allowed;
* anything outside the exact three-value vocabulary is rejected *before* any
  subprocess is dispatched, with a bounded message.

The CLI subprocess is always mocked; no real ``kimi`` process is started.
"""

import json
from unittest.mock import patch

import pytest

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.llm.kimi_code.adapter import KimiCodeAdapter
from lingtai.llm.service import LLMService


KIMI_THREE_VALUES = ("low", "high", "max")
EFFORT_ENV = "KIMI_MODEL_THINKING_EFFORT"
CAPABILITY_SOURCE = "kimi_coding_service_models_page_2026-08-05"

# Known effort-capable K3 coding ids and the always-thinking defaults.
CAPABLE_MODEL = "k3"
ALWAYS_THINKING_MODELS = ("kimi-for-coding", "kimi-for-coding-highspeed")


@pytest.fixture(autouse=True)
def _no_ambient_effort(monkeypatch):
    """The adapter copies ``os.environ``; a stray real value would mask bugs.

    Every omission assertion in this file is "the adapter injected nothing",
    which is only meaningful if the ambient environment carries nothing either.
    Production deliberately does NOT strip an operator-set value — "omission
    stays omission" means leaving ``os.environ`` alone.
    """
    monkeypatch.delenv(EFFORT_ENV, raising=False)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _stream(content='{"action":"final","text":"ok"}', *, session_id="kimi-session-1"):
    lines = [json.dumps({"role": "assistant", "content": content})]
    if session_id:
        lines.append(
            json.dumps(
                {
                    "role": "meta",
                    "type": "session.resume_hint",
                    "session_id": session_id,
                }
            )
        )
    return "\n".join(lines) + "\n"


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
    """Drop-in for ``subprocess.run`` recording every command AND private env."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        return _FakeProc(stdout=_stream())

    @property
    def commands(self):
        return [cmd for cmd, _kw in self.calls]

    @property
    def envs(self):
        return [kw["env"] for _cmd, kw in self.calls]


def _effort_env(env):
    """The injected effort level, or None when nothing was injected."""
    return env.get(EFFORT_ENV)


def _service_session(
    thinking, *, provider="kimi-code", model=CAPABLE_MODEL, tools=None
):
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
# (1)(6) Omission stays omission
# ---------------------------------------------------------------------------


def test_omitted_thinking_injects_no_effort_env_var():
    """The internal ``"default"`` sentinel must produce no env var at all.

    ``LLMService.create_session`` defaults ``thinking`` to that sentinel, so
    this is the shape every existing kimi_code agent already runs.
    """
    sess = _service_session("default", tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    env = run.envs[0]
    assert EFFORT_ENV not in env
    # The rest of the private env is exactly the pre-contract shape.
    assert env["KIMI_DISABLE_TELEMETRY"] == "1"
    assert env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    assert "KIMI_CODE_HOME" in env


def test_direct_adapter_omission_is_byte_identical_to_pre_contract():
    """A baseline chat and an explicitly-``None`` chat build the same call."""
    ad = KimiCodeAdapter(model=CAPABLE_MODEL)
    baseline = ad.create_chat(CAPABLE_MODEL, "sys", None)
    contracted = ad.create_chat(CAPABLE_MODEL, "sys", None, thinking=None)
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        baseline.send("hi")
        contracted.send("hi")

    assert EFFORT_ENV not in run.envs[0]
    assert run.envs[0] == run.envs[1]
    assert run.commands[0] == run.commands[1]


@pytest.mark.parametrize("model", ALWAYS_THINKING_MODELS)
def test_omitted_thinking_stays_valid_for_always_thinking_models(model):
    """No regression for the default model: omission never raises anywhere.

    ``kimi-for-coding`` is always-thinking and has no effort dimension, so
    promoting an omitted value to an explicit level would break every existing
    kimi_code agent. Omission must remain valid for every model.
    """
    ad = KimiCodeAdapter(model=model)
    sess = ad.create_chat(model, "sys", None, thinking="default")
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    assert EFFORT_ENV not in run.envs[0]


# ---------------------------------------------------------------------------
# (2)(3) Explicit effort reaches the private env — and only there
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", KIMI_THREE_VALUES)
def test_every_kimi_level_reaches_the_env_exactly(value):
    sess = _service_session(value)
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    assert _effort_env(run.envs[0]) == value


@pytest.mark.parametrize("value", KIMI_THREE_VALUES)
def test_effort_never_reaches_argv_or_the_prompt(value):
    """Effort is an env-var contract; the CLI command must stay pre-contract.

    Asserted on the env-var *name*, not the level value — ``--prompt <text>``
    is part of argv and may incidentally contain the word ``high``.
    """
    sess = _service_session(value, tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        sess.send("hi")

    cmd = run.commands[0]
    assert EFFORT_ENV not in " ".join(str(tok) for tok in cmd)
    assert "--effort" not in cmd
    assert cmd[0] == "kimi"
    assert "--prompt" in cmd
    assert "--output-format" in cmd and "stream-json" in cmd


def test_explicit_effort_leaves_the_command_identical_to_omission():
    """The only difference an explicit level makes is one env entry."""
    plain = _service_session("default", tools=[_weather_tool()])
    effortful = _service_session("high", tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        plain.send("hi")
        effortful.send("hi")

    assert run.commands[0] == run.commands[1]
    plain_env, effort_env = run.envs
    assert _effort_env(effort_env) == "high"
    assert set(effort_env) - set(plain_env) == {EFFORT_ENV}
    assert {k: v for k, v in effort_env.items() if k != EFFORT_ENV} == plain_env


# ---------------------------------------------------------------------------
# (4)(5) Model capability gate — fail closed, before any spawn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ALWAYS_THINKING_MODELS)
def test_always_thinking_model_with_explicit_effort_raises_before_spawn(model):
    ad = KimiCodeAdapter(model=model)

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run") as run:
        with pytest.raises(ValueError) as excinfo:
            ad.create_chat(model, "sys", None, thinking="high")

    run.assert_not_called()
    message = str(excinfo.value)
    assert "always-thinking" in message, message
    assert model in message, message


@pytest.mark.parametrize("model", ["whatever", "kimi-k2", "gpt-5.5", "k3-turbo"])
def test_unknown_model_with_explicit_effort_fails_closed(model):
    """Capability is not assumed for an unrecognised id (audit Phase 1)."""
    ad = KimiCodeAdapter(model=model)

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run") as run:
        with pytest.raises(ValueError) as excinfo:
            ad.create_chat(model, "sys", None, thinking="high")

    run.assert_not_called()
    message = str(excinfo.value)
    assert "capability" in message.lower(), message


def test_empty_model_is_gated_by_the_adapter_default():
    """``create_chat(model="")`` routes to the adapter's own default model.

    The gate must judge the model that will actually be used, so an empty
    argument is resolved against the adapter default before the capability
    lookup rather than treated as an unknown id in its own right.
    """
    capable = KimiCodeAdapter(model=CAPABLE_MODEL)
    sess = capable.create_chat("", "sys", None, thinking="high")
    assert sess.reasoning_observability()["reasoning_actual"] == "high"

    # The adapter's own default (``kimi-for-coding``) is always-thinking.
    default_adapter = KimiCodeAdapter()
    with pytest.raises(ValueError, match="always-thinking"):
        default_adapter.create_chat("", "sys", None, thinking="high")


@pytest.mark.parametrize("model", ("k3", "k3-256k"))
def test_known_effort_capable_models_accept_every_level(model):
    ad = KimiCodeAdapter(model=model)
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        for value in KIMI_THREE_VALUES:
            ad.create_chat(model, "sys", None, thinking=value).send("hi")

    assert [_effort_env(env) for env in run.envs] == list(KIMI_THREE_VALUES)


# ---------------------------------------------------------------------------
# (7) Out-of-vocabulary rejection, bounded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["medium", "xhigh", "minimal", "none", "High", "MAX", " high", "high ", "",
     "ultra", 1, True, 0.5, []],
)
def test_invalid_effort_rejected_before_any_dispatch(value):
    """Compatibility aliases (medium/xhigh) are deliberately NOT surfaced."""
    ad = KimiCodeAdapter(model=CAPABLE_MODEL)

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run") as run:
        with pytest.raises(ValueError) as excinfo:
            ad.create_chat(CAPABLE_MODEL, "sys", None, thinking=value)

    run.assert_not_called()
    message = str(excinfo.value)
    # The offered vocabulary is exactly the three K3 coding levels — the
    # Responses values are never presented as valid alternatives.
    assert "exactly one of " in message, message
    offered = message.split("exactly one of ", 1)[1].split(" (omit", 1)[0]
    assert offered.split(", ") == list(KIMI_THREE_VALUES), message


def test_vocabulary_is_checked_before_the_model_gate():
    """A bad value on a bad model reports the vocabulary, not the capability.

    Keeping the two rejection paths separable is what lets the parametrized
    case above assert one stable message regardless of model.
    """
    ad = KimiCodeAdapter(model="kimi-for-coding")

    with pytest.raises(ValueError) as excinfo:
        ad.create_chat("kimi-for-coding", "sys", None, thinking="medium")

    assert "exactly one of " in str(excinfo.value), str(excinfo.value)


def test_rejection_message_does_not_echo_an_unbounded_value():
    ad = KimiCodeAdapter(model=CAPABLE_MODEL)
    blob = "x" * 5000

    with pytest.raises(ValueError) as excinfo:
        ad.create_chat(CAPABLE_MODEL, "sys", None, thinking=blob)

    assert blob not in str(excinfo.value)
    assert len(str(excinfo.value)) < 400


# ---------------------------------------------------------------------------
# (8) Frozen at creation — resume and overflow reuse one snapshot
# ---------------------------------------------------------------------------


def test_resumed_invocations_repeat_the_same_effort():
    """Every physical invocation carries it — first call and ``--session``."""
    sess = _service_session("max", tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        sess.send("first")
        sess.send("second")
        sess.send("third")

    first, *resumed = run.commands
    assert "--session" not in first
    assert resumed, "expected the CLI session identity to be reused"
    for cmd in resumed:
        assert cmd[cmd.index("--session") + 1] == "kimi-session-1"
    assert [_effort_env(env) for env in run.envs] == ["max", "max", "max"]


def test_effort_is_frozen_and_not_adapter_global_state():
    """Two chats from one adapter keep independent, immutable decisions."""
    ad = KimiCodeAdapter(model=CAPABLE_MODEL)
    low = ad.create_chat(CAPABLE_MODEL, "sys", None, thinking="low")
    omitted = ad.create_chat(CAPABLE_MODEL, "sys", None)
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        low.send("a")
        omitted.send("a")
        low.send("b")

    assert _effort_env(run.envs[0]) == "low"
    assert EFFORT_ENV not in run.envs[1]
    assert _effort_env(run.envs[2]) == "low"
    # The decision object itself is immutable and never leaks into argv.
    with pytest.raises(Exception):
        low._effort.level = "max"
    assert ad._extra_argv == []


def test_effort_does_not_reset_the_cli_session_identity():
    """Effort rides along; it must not clear ``_kimi_session_id``.

    This slice freezes effort at creation, so there is no reset rule to apply
    — a configured level and an omitted one accelerate identically.
    """
    plain = _service_session("default", tools=[_weather_tool()])
    effortful = _service_session("low", tools=[_weather_tool()])
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        for sess in (plain, effortful):
            sess.send("first")
            sess.send("second")

    plain_cmds, effort_cmds = run.commands[:2], run.commands[2:]
    assert "--session" not in plain_cmds[0] and "--session" not in effort_cmds[0]
    for cmds in (plain_cmds, effort_cmds):
        assert cmds[1][cmds[1].index("--session") + 1] == "kimi-session-1"
    assert effort_cmds == plain_cmds


def test_overflow_recovery_reuses_one_frozen_snapshot():
    """Every physical invocation of ONE logical send carries the same effort."""
    sess = _service_session("high")
    # Enough trimmable history that overflow recovery retries rather than
    # giving up immediately.
    with patch(
        "lingtai.llm.kimi_code.adapter.subprocess.run",
        return_value=_FakeProc(stdout=_stream()),
    ):
        for i in range(3):
            sess.send(f"message {i}")

    envs = []
    overflow = _FakeProc(stdout="", stderr="Error: prompt is too long", returncode=1)
    success = _FakeProc(stdout=_stream())
    replies = [overflow, success]

    def fake_run(cmd, **kw):
        envs.append(kw["env"])
        return replies.pop(0)

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=fake_run):
        sess.send("the message that overflows")

    assert len(envs) == 2
    assert [_effort_env(env) for env in envs] == ["high", "high"]


# ---------------------------------------------------------------------------
# (9) generate() has no session contract
# ---------------------------------------------------------------------------


def test_generate_one_shot_never_injects_effort():
    ad = KimiCodeAdapter(model=CAPABLE_MODEL)
    # A chat with an explicit effort exists on the same adapter — the one-shot
    # path must not pick it up.
    ad.create_chat(CAPABLE_MODEL, "sys", None, thinking="max")
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        ad.generate(CAPABLE_MODEL, "hello", system_prompt="sys")

    assert EFFORT_ENV not in run.envs[0]


# ---------------------------------------------------------------------------
# (10) Observability
# ---------------------------------------------------------------------------


def test_reasoning_observability_fields_for_explicit_and_omitted():
    explicit = _service_session("max").reasoning_observability()
    assert explicit == {
        "reasoning_requested": "max",
        "reasoning_normalized": "max",
        "reasoning_actual": "max",
        "reasoning_source": "explicit_config",
        "reasoning_capability_source": CAPABILITY_SOURCE,
    }

    omitted = _service_session("default").reasoning_observability()
    assert omitted == {
        "reasoning_requested": "omitted",
        "reasoning_normalized": "omitted",
        "reasoning_actual": "omitted",
        "reasoning_source": "lingtai_kimi_omitted",
        "reasoning_capability_source": CAPABILITY_SOURCE,
    }


def test_observability_carries_no_credential_or_session_identity():
    sess = _service_session("low")
    fields = sess.reasoning_observability()

    blob = " ".join(f"{k}={v}" for k, v in fields.items()).lower()
    for forbidden in ("api_key", "sk-", "token", "kimi-session", "prompt", "--"):
        assert forbidden not in blob, fields


def test_observability_survives_the_rate_gate_proxy():
    """A gated session must not silently drop the frozen decision."""
    ad = KimiCodeAdapter(model=CAPABLE_MODEL, max_rpm=600)
    gated = ad.create_chat(CAPABLE_MODEL, "sys", None, thinking="high")

    assert type(gated).__name__ == "_GatedSession"
    assert gated.reasoning_observability()["reasoning_actual"] == "high"


# ---------------------------------------------------------------------------
# (11) Both registered spellings behave identically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["default", "low", "high", "max"])
def test_both_provider_spellings_behave_identically(value):
    dashed = _service_session(value, provider="kimi-code")
    underscored = _service_session(value, provider="kimi_code")
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        dashed.send("hi")
        underscored.send("hi")

    assert _effort_env(run.envs[0]) == _effort_env(run.envs[1])
    assert run.commands[0] == run.commands[1]


# ---------------------------------------------------------------------------
# Phase C — session level: omission provenance and passthrough
# ---------------------------------------------------------------------------


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


def test_build_agent_config_keeps_kimi_omission_as_the_sentinel():
    """A kimi manifest with no thinking must NOT become the legacy ``high``.

    ``high`` would be an explicit level, and the default ``kimi-for-coding``
    model is always-thinking — so promotion would raise at ``create_chat`` for
    every existing kimi_code agent.
    """
    from lingtai.agent import build_agent_config
    from lingtai.kernel.config import AgentConfig

    def _thinking(provider, **llm):
        manifest = {"llm": {"provider": provider, "model": "m", **llm}}
        return build_agent_config(manifest, max_rpm=0).thinking

    assert _thinking("kimi-code") == "default"
    assert _thinking("kimi_code") == "default"
    assert _thinking("kimi-code", thinking="max") == "max"
    assert _thinking("codex") == "default"
    # Untouched neighbours — this slice must not move any other provider.
    assert _thinking("anthropic") == "high"
    assert _thinking("openai") == "high"
    assert _thinking("kimi") == "high"

    # Constructor-omitted resolution keeps the historical default surface.
    assert AgentConfig().thinking == "high"
    assert AgentConfig(provider="anthropic").thinking == "high"
    assert AgentConfig(provider="kimi-code").thinking == "default"
    assert AgentConfig(provider="kimi_code").thinking == "default"
    assert AgentConfig(provider="kimi-code", thinking="max").thinking == "max"
    assert AgentConfig(thinking="high").thinking_omitted is False
    assert AgentConfig().thinking_omitted is True


def test_session_thinking_omits_for_kimi_and_passes_explicit_through():
    """``_session_thinking`` is the chat-creation side of the same rule."""
    from lingtai.kernel.config import AgentConfig

    omitted, _ = _session_manager("kimi-code", CAPABLE_MODEL)
    assert omitted._session_thinking() == "default"

    explicit, _ = _session_manager("kimi-code", CAPABLE_MODEL, thinking="high")
    assert explicit._session_thinking() == "high"

    # The effective provider comes from the LLMService when the config has
    # none, so a bare AgentConfig() against a kimi service still omits.
    bare, _ = _session_manager(
        "kimi-code", CAPABLE_MODEL, agent_config=AgentConfig()
    )
    assert bare._session_thinking() == "default"

    # Every other provider keeps the historical ``or "high"``. The effective
    # provider comes from the config when it has one, so this exercises the
    # legacy branch without needing a credentialed non-kimi service.
    legacy, _ = _session_manager(
        "kimi-code",
        CAPABLE_MODEL,
        agent_config=AgentConfig(provider="anthropic", model="claude"),
    )
    assert legacy._effective_provider() == "anthropic"
    assert legacy._session_thinking() == "high"


def test_omitted_kimi_config_logs_omitted_and_injects_nothing():
    """The real production shape, end to end through ``SessionManager.send``."""
    from lingtai.agent import build_agent_config

    config = build_agent_config(
        {"llm": {"provider": "kimi-code", "model": CAPABLE_MODEL}}, max_rpm=0
    )
    manager, events = _session_manager(
        "kimi-code", CAPABLE_MODEL, agent_config=config
    )
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        manager.send("hi")

    (fields,) = _llm_call_fields(events)
    assert fields["reasoning_actual"] == "omitted"
    assert fields["reasoning_source"] == "lingtai_kimi_omitted"
    assert EFFORT_ENV not in run.envs[0]


def test_constructor_omitted_kimi_config_does_not_become_effort_high():
    """A bare ``AgentConfig()`` against a kimi service must stay omitted."""
    from lingtai.kernel.config import AgentConfig

    manager, events = _session_manager(
        "kimi-code", CAPABLE_MODEL, agent_config=AgentConfig()
    )
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        manager.send("hi")

    assert EFFORT_ENV not in run.envs[0]
    assert _llm_call_fields(events)[0]["reasoning_actual"] == "omitted"


def test_llm_call_record_gains_exactly_the_safe_fields_on_a_real_send():
    manager, events = _session_manager(
        "kimi-code", CAPABLE_MODEL, thinking="high"
    )
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
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
    assert fields["model"] == CAPABLE_MODEL
    assert fields["reasoning_actual"] == "high"
    assert fields["reasoning_source"] == "explicit_config"
    # The frozen decision really did reach the wire on this same send.
    assert _effort_env(run.envs[0]) == "high"


def test_llm_call_record_is_unchanged_for_a_provider_without_the_hook():
    """A session with no hook keeps exactly the two pre-contract fields."""
    from lingtai.kernel.llm.base import LLMResponse, UsageMetadata
    from lingtai.kernel.llm.interface import ChatInterface
    from lingtai.kernel.session import _reasoning_observability_fields

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

    manager, events = _session_manager("kimi-code", CAPABLE_MODEL)
    manager._chat = _PlainSession()
    manager.send("hi")

    (fields,) = _llm_call_fields(events)
    assert set(fields) == {"model", "api_call_id"}

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
    assert (
        _reasoning_observability_fields(_service_session("low"))["reasoning_actual"]
        == "low"
    )


def test_gate_judges_every_model_the_cli_could_actually_run(monkeypatch):
    """The gate must not authorize an effort against a model that never runs.

    With an API key available, ``_invoke_raw`` deliberately drops ``--model``
    because Kimi's env-model synthesis owns the alias — and that synthesis uses
    the *adapter's* model (``KIMI_MODEL_NAME``), not this chat's. An adapter
    defaulting to the always-thinking ``kimi-for-coding`` with a chat model of
    ``k3`` would otherwise ship ``KIMI_MODEL_THINKING_EFFORT`` on an invocation
    whose effective model has no effort dimension at all. A divergent pair
    fails closed.
    """
    monkeypatch.setenv("KIMI_MODEL_API_KEY", "k")
    ad = KimiCodeAdapter(model="kimi-for-coding")

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run") as run:
        with pytest.raises(ValueError, match="always-thinking"):
            ad.create_chat(CAPABLE_MODEL, "sys", None, thinking="high")

    run.assert_not_called()


def test_divergent_but_capable_model_pair_is_allowed(monkeypatch):
    """Both candidates effort-capable — no reason to refuse."""
    monkeypatch.setenv("KIMI_MODEL_API_KEY", "k")
    ad = KimiCodeAdapter(model="k3-256k")
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        ad.create_chat(CAPABLE_MODEL, "sys", None, thinking="max").send("hi")

    assert _effort_env(run.envs[0]) == "max"


def test_matching_model_pair_is_unaffected_by_the_second_gate(monkeypatch):
    """The common case (adapter model == chat model) gains no strictness."""
    monkeypatch.setenv("KIMI_MODEL_API_KEY", "k")
    ad = KimiCodeAdapter(model=CAPABLE_MODEL)
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        ad.create_chat(CAPABLE_MODEL, "sys", None, thinking="low").send("hi")

    assert _effort_env(run.envs[0]) == "low"


def test_omission_never_triggers_the_divergent_model_gate(monkeypatch):
    """Omission stays valid for every pair, however divergent."""
    monkeypatch.setenv("KIMI_MODEL_API_KEY", "k")
    ad = KimiCodeAdapter(model="kimi-for-coding")
    run = _CapturingRun()

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=run):
        ad.create_chat("whatever", "sys", None, thinking="default").send("hi")

    assert EFFORT_ENV not in run.envs[0]


# ---------------------------------------------------------------------------
# Regression: LingTai-internal ancillary sessions must be provider-scoped
# (Opus 5 review F1 — soul inquiry/consultation hardcoded thinking="high").
# ---------------------------------------------------------------------------


def test_ancillary_session_thinking_helper_is_provider_scoped():
    from lingtai.kernel.config import (
        DEFAULT_THINKING,
        THINKING_OMITTED,
        ancillary_session_thinking,
    )

    # Kimi Code owns its omission: a LingTai-internal ancillary session gets
    # the sentinel (no KIMI_MODEL_THINKING_EFFORT at all), never a level.
    assert ancillary_session_thinking("kimi-code") == THINKING_OMITTED
    assert ancillary_session_thinking("kimi_code") == THINKING_OMITTED
    assert ancillary_session_thinking("KIMI-CODE") == THINKING_OMITTED
    # Every other provider keeps the legacy cross-provider default.
    assert ancillary_session_thinking("anthropic") == DEFAULT_THINKING
    assert ancillary_session_thinking("codex") == DEFAULT_THINKING
    assert ancillary_session_thinking("custom") == DEFAULT_THINKING
    assert ancillary_session_thinking(None) == DEFAULT_THINKING
    assert ancillary_session_thinking("") == DEFAULT_THINKING


def test_soul_inquiry_uses_omission_sentinel_on_kimi_route(monkeypatch):
    """soul(action='inquiry') must not inject a hardcoded level on kimi."""
    import types

    from lingtai.kernel.config import THINKING_OMITTED
    from lingtai.tools.soul import config as soul_config
    from lingtai.tools.soul import consultation as soul_consultation
    from lingtai.tools.soul import inquiry as soul_inquiry

    captured: dict = {}

    class FakeService:
        provider = "kimi-code"
        model = "kimi-for-coding"

        def create_session(self, **kwargs):
            captured.update(kwargs)
            return object()

    agent = types.SimpleNamespace(
        _chat=None,
        _config=types.SimpleNamespace(model=None, provider="kimi-code"),
        service=FakeService(),
        _log=lambda *a, **k: None,
    )
    monkeypatch.setattr(soul_config, "_build_soul_system_prompt", lambda agent, **kw: "sys")
    monkeypatch.setattr(
        soul_consultation, "_send_with_timeout", lambda agent, session, question: None
    )
    monkeypatch.setattr(soul_consultation, "_write_soul_tokens", lambda agent, response: None)

    soul_inquiry.soul_inquiry(agent, "question")
    assert captured["thinking"] == THINKING_OMITTED


def test_soul_inquiry_keeps_legacy_high_on_non_kimi_route(monkeypatch):
    import types

    from lingtai.kernel.config import DEFAULT_THINKING
    from lingtai.tools.soul import config as soul_config
    from lingtai.tools.soul import consultation as soul_consultation
    from lingtai.tools.soul import inquiry as soul_inquiry

    captured: dict = {}

    class FakeService:
        provider = "anthropic"
        model = "claude"

        def create_session(self, **kwargs):
            captured.update(kwargs)
            return object()

    agent = types.SimpleNamespace(
        _chat=None,
        _config=types.SimpleNamespace(model=None, provider="anthropic"),
        service=FakeService(),
        _log=lambda *a, **k: None,
    )
    monkeypatch.setattr(soul_config, "_build_soul_system_prompt", lambda agent, **kw: "sys")
    monkeypatch.setattr(
        soul_consultation, "_send_with_timeout", lambda agent, session, question: None
    )
    monkeypatch.setattr(soul_consultation, "_write_soul_tokens", lambda agent, response: None)

    soul_inquiry.soul_inquiry(agent, "question")
    assert captured["thinking"] == DEFAULT_THINKING


def test_soul_consultation_uses_omission_sentinel_on_kimi_route(monkeypatch):
    """soul consultation mirrors must also be provider-scoped, not hardcoded."""
    import types

    from lingtai.kernel.config import THINKING_OMITTED
    from lingtai.kernel.llm.interface import ChatInterface
    from lingtai.tools.soul import consultation as soul_consultation

    captured: dict = {}

    class FakeService:
        provider = "kimi-code"
        model = "kimi-for-coding"

        def create_session(self, **kwargs):
            captured.update(kwargs)
            return object()

    agent = types.SimpleNamespace(
        _chat=None,
        _config=types.SimpleNamespace(
            model=None, provider="kimi-code", context_limit=200_000
        ),
        service=FakeService(),
        _log=lambda *a, **k: None,
        _session=types.SimpleNamespace(
            _build_tool_schemas_fn=lambda: None,
        ),
    )
    iface = ChatInterface()
    iface.add_user_message("diary cue")
    from lingtai.tools.soul import config as soul_config

    monkeypatch.setattr(
        soul_consultation, "_fit_interface_to_window", lambda iface, target: iface
    )
    monkeypatch.setattr(
        soul_config, "_build_soul_system_prompt", lambda agent, kind="inquiry": "sys"
    )
    monkeypatch.setattr(soul_consultation, "_render_current_diary", lambda agent: "diary")
    monkeypatch.setattr(soul_consultation, "_build_consultation_cue", lambda agent, kind, diary: "cue")
    monkeypatch.setattr(
        soul_consultation, "_send_with_timeout", lambda agent, session, content: None
    )
    monkeypatch.setattr(soul_consultation, "_write_soul_tokens", lambda agent, response: None)

    soul_consultation._run_consultation(agent, iface, "test")
    assert captured["thinking"] == THINKING_OMITTED
