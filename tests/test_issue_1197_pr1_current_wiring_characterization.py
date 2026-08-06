"""Issue #1197 PR1 characterization of the current effort wiring.

These tests lock the current production behavior before the PR1 extraction.
They intentionally assert provider bytes, omissions, errors, construction
timing, and route inequalities; they do not assert provider effectiveness.
The later PR names in the locked-defect docstrings are the only authorized
replacement points for these observations.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingtai.agent import build_agent_config
from lingtai.init_schema import validate_init
from lingtai.kernel.config import (
    THINKING_LEVELS,
    THINKING_PROVIDERS,
    llm_supports_thinking,
)
from lingtai.kernel.llm.base import FunctionSchema
from lingtai.kernel.session import SessionManager
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.base_agent.identity import _status
from lingtai.llm.anthropic.adapter import AnthropicAdapter
from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter
from lingtai.llm.custom.adapter import create_custom_adapter
from lingtai.llm.deepseek.adapter import DeepSeekAdapter
from lingtai.llm.gemini.adapter import GeminiAdapter
from lingtai.llm.kimi_code.adapter import KimiCodeAdapter
from lingtai.llm.minimax.adapter import MiniMaxAdapter
from lingtai.llm.mimo.adapter import MimoAdapter
from lingtai.llm.openai.adapter import CodexOpenAIAdapter, OpenAIAdapter
from lingtai.llm.openrouter.adapter import OpenRouterAdapter
from lingtai.llm.service import LLMService as ConcreteLLMService
from lingtai.llm.zhipu.adapter import ZhipuAdapter
from lingtai.kernel.presets import load_preset
from lingtai.tools.daemon import _BACKEND_SPECS, _backend_options_to_argv


ROOT = Path(__file__).resolve().parents[1]


class _FakeResponses:
    def __init__(self, events):
        self.events = list(events)
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        yield from self.events


class _FakeClient:
    def __init__(self, events):
        self.responses = _FakeResponses(events)


def _completed_event():
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            id="resp_issue_1197",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=20,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
        ),
    )


def _valid_init() -> dict:
    return {
        "manifest": {
            "agent_name": "alice",
            "language": "en",
            "llm": {
                "provider": "anthropic",
                "model": "claude-sonnet",
                "api_key": None,
                "base_url": None,
            },
            "capabilities": {},
            "soul": {"delay": 120},
            "stamina": 3600,
            "context_limit": None,
            "max_turns": 50,
            "admin": {"karma": True},
            "streaming": False,
        },
        "covenant": "",
        "pad": "",
        "lingtai": "",
        "soul": "",
    }


def _tool() -> FunctionSchema:
    return FunctionSchema(
        name="report_answer",
        description="Report an answer",
        parameters={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )


def _chat_extra(adapter, thinking: str):
    session = adapter.create_chat("issue-1197-model", "system", thinking=thinking)
    return session._extra_kwargs


_UNSET = object()


def _responses_wire(adapter, thinking=_UNSET):
    adapter._client = _FakeClient([_completed_event()])
    kwargs = {} if thinking is _UNSET else {"thinking": thinking}
    session = adapter.create_chat("issue-1197-model", "system", **kwargs)
    session.send_stream("hello")
    return adapter._client.responses.kwargs[-1]


def test_G1_G2_global_gate_and_validation_remain_exact():
    """G-1/G-2 — #1197 config.py:14-30; later PR A1 replaces this only when route descriptors own validation and error text."""
    assert THINKING_LEVELS == ("none", "minimal", "low", "medium", "high", "xhigh")
    assert THINKING_PROVIDERS == ("codex", "codex-pool", "codex_pool")
    assert llm_supports_thinking({"provider": "codex"}) is True
    assert llm_supports_thinking({"provider": "codex_pool"}) is True
    assert llm_supports_thinking(
        {"provider": "custom", "api_compat": "openai", "wire_api": "responses"}
    ) is True
    assert llm_supports_thinking(
        {"provider": "custom", "api_compat": "openai", "wire_api": "chat_completions"}
    ) is False
    assert llm_supports_thinking({"provider": "anthropic"}) is False

    for value in THINKING_LEVELS:
        data = _valid_init()
        data["manifest"]["llm"] = {"provider": "codex", "model": "m", "thinking": value}
        validate_init(data)

    data = _valid_init()
    data["manifest"]["llm"]["provider"] = "anthropic"
    data["manifest"]["llm"]["thinking"] = "high"
    with pytest.raises(ValueError) as excinfo:
        validate_init(data)
    assert str(excinfo.value) == (
        "manifest.llm.thinking is currently supported only for the Codex providers "
        "(codex, codex-pool, codex_pool) or custom OpenAI-compatible Responses"
    )

    data = _valid_init()
    data["manifest"]["llm"] = {"provider": "codex", "model": "m", "thinking": "HIGH"}
    with pytest.raises(ValueError) as excinfo:
        validate_init(data)
    assert str(excinfo.value) == (
        "manifest.llm.thinking: expected one of none, minimal, low, medium, high, xhigh"
    )


def test_G1_G2_preset_prefix_and_scope_errors_remain_distinct(tmp_path):
    """G-1/G-2 — #1197 presets.py:350-374; later PR A1 replaces this only when shared validation preserves the preset prefix and ordering."""
    path = tmp_path / "issue-1197.json"
    path.write_text(
        json.dumps(
            {
                "name": "issue-1197",
                "description": {"summary": "characterization"},
                "manifest": {
                    "llm": {"provider": "anthropic", "model": "m", "thinking": "high"}
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        load_preset(str(path), run_migrations=lambda _path: None)
    assert str(excinfo.value) == (
        f"preset {str(path)!r} ({path}): manifest.llm.thinking is currently "
        "supported only for the Codex providers (codex, codex-pool, codex_pool) "
        "or custom OpenAI-compatible Responses"
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("codex", "default"),
        ("codex-pool", "default"),
        ("codex_pool", "default"),
        ("anthropic", "high"),
        ("minimax", "high"),
        ("gemini", "high"),
        ("deepseek", "high"),
        ("mimo", "high"),
        ("glm", "high"),
        ("openrouter", "high"),
        ("kimi", "high"),
        ("claude-code", "high"),
        ("kimi-code", "high"),
    ],
)
def test_G6_main_agent_omission_hydrates_current_baseline(provider, expected):
    """G-6 — #1197 agent.py:97-106; later PR A1/provider repair replaces this only when each route owns an authorized baseline."""
    config = build_agent_config({"llm": {"provider": provider}}, max_rpm=0)
    assert config.thinking == expected


def test_G6_custom_responses_omission_serializes_nested_high_after_hydration():
    """G-6 — #1197 agent.py:97-106 plus openai/adapter.py:2556-2598; later A1/provider repair replaces this only when an authorized route baseline changes."""
    config = build_agent_config(
        {
            "llm": {
                "provider": "custom",
                "api_compat": "openai",
                "wire_api": "responses",
            }
        },
        max_rpm=0,
    )
    adapter = OpenAIAdapter(api_key="fake", wire_api="responses")
    sent = _responses_wire(adapter, config.thinking)
    assert config.thinking == "high"
    assert sent["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in sent


def test_G3_generic_chat_mapper_preserves_current_collapse_including_unknown():
    """G-3 — #1197 openai/adapter.py:2608-2612; later PR A2/A3/A4 replaces this only with intentional route mappers."""
    expected = {
        None: {"reasoning_effort": "low"},
        "default": {},
        "high": {"reasoning_effort": "high"},
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "low"},
        "none": {"reasoning_effort": "low"},
        "minimal": {"reasoning_effort": "low"},
        "xhigh": {"reasoning_effort": "low"},
        "unknown": {"reasoning_effort": "low"},
    }
    for thinking, expected_extra in expected.items():
        extra = _chat_extra(OpenAIAdapter(api_key="fake"), thinking)
        assert extra == expected_extra


def test_G3_inherited_chat_routes_keep_bytes_and_route_identity():
    """G-3 — #1197 openai/adapter.py:2580-2616; later PR A2/A3/A4 replaces this only when the inherited provider routes intentionally diverge."""
    adapters = [
        OpenRouterAdapter(api_key="fake"),
        DeepSeekAdapter(api_key="fake", wire_api="chat_completions"),
        MimoAdapter(api_key="fake", wire_api="chat_completions"),
        ZhipuAdapter(api_key="fake"),
        create_custom_adapter(
            api_key="fake", api_compat="openai", base_url="https://kimi.example/v1"
        ),
    ]
    for adapter in adapters:
        extra = _chat_extra(adapter, "medium")
        assert extra.get("reasoning_effort") == "low"
    assert OpenRouterAdapter(api_key="fake")._adapter_extra_body() == {
        "reasoning": {"include": False}
    }
    assert isinstance(
        create_custom_adapter(
            api_key="fake", api_compat="openai", base_url="https://kimi.example/v1"
        ),
        OpenAIAdapter,
    )


@pytest.mark.parametrize("value", ["none", "minimal", "low", "medium", "high", "xhigh"])
def test_responses_wire_preserves_all_six_values(value):
    """#1197 current source anchor openai/adapter.py:922-931; later route PRs may replace only this route's deliberate mapper."""
    sent = _responses_wire(OpenAIAdapter(api_key="fake", use_responses=True), value)
    assert sent["reasoning"] == {"effort": value}
    assert "reasoning_effort" not in sent


def test_responses_wire_default_and_none_omit_exactly():
    """#1197 current source anchor openai/adapter.py:922-926; later PR A1/provider repair may replace this only with an authorized default."""
    for kwargs in ({}, {"thinking": "default"}, {"thinking": None}):
        adapter = OpenAIAdapter(api_key="fake", use_responses=True)
        sent = _responses_wire(adapter, **kwargs)
        assert "reasoning" not in sent
        assert "reasoning_effort" not in sent


def test_responses_wire_invalid_value_keeps_exact_error():
    """G-1/G-2 — #1197 openai/adapter.py:926-930; later PR A1 replaces this only when validation owns the same error contract."""
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    with pytest.raises(ValueError) as excinfo:
        adapter.create_chat("m", "system", thinking="ultra")
    assert str(excinfo.value) == (
        "OpenAI Responses thinking must be one of "
        "none, minimal, low, medium, high, xhigh, or default"
    )


def test_codex_default_is_explicit_xhigh_and_main_native_diverge():
    """G-7/C-2 — #1197 openai/adapter.py:5840-5848 and daemon wiring; later PR A6 replaces this only after explicit daemon parity proof."""
    adapter = CodexOpenAIAdapter(
        api_key="fake", base_url="http://codex.example", use_responses=True, force_responses=True
    )
    for kwargs in ({}, {"thinking": "default"}, {"thinking": None}):
        sent = _responses_wire(adapter, **kwargs)
        assert sent["reasoning"] == {"effort": "xhigh"}
    assert build_agent_config({"llm": {"provider": "codex"}}, max_rpm=0).thinking == "default"


def test_G8_uninstrumented_old_llm_call_has_no_effort_claim_and_correlation_is_exact():
    """G-8 — #1197 kernel/session.py:396-400; later PR1 structural tests add only exact construction records and never provider-default claims."""
    events = []
    service = MagicMock()
    service.model = "test-model"
    chat = MagicMock()
    chat.context_window.return_value = 100000
    chat.interface.estimate_context_tokens.return_value = 5000
    chat.interface.current_system_prompt = "system"
    chat.send.return_value = MagicMock(
        text="answer",
        tool_calls=[],
        thoughts=[],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=10,
            cached_tokens=20,
            extra={},
        ),
    )
    service.create_session.return_value = chat
    manager = SessionManager(
        llm_service=service,
        config=AgentConfig(),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "system",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event, **fields: events.append((event, fields)),
    )
    manager.send("hello")
    call = next(fields for event, fields in events if event == "llm_call")
    response = next(fields for event, fields in events if event == "llm_response")
    assert set(call) == {"model", "api_call_id"}
    assert response["api_call_id"] == call["api_call_id"]
    assert "reasoning" not in call


def test_construction_capture_is_before_send_and_rebuild_uses_current_value():
    """#1197 current source anchor kernel/session.py:288-315; PR1 must preserve capture-before-send and rebuild timing exactly."""
    service = MagicMock()
    service.model = "test-model"
    service.create_session.return_value = MagicMock()
    manager = SessionManager(
        llm_service=service,
        config=AgentConfig(thinking="medium"),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "system",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    manager.ensure_session()
    assert service.create_session.call_args.kwargs["thinking"] == "medium"
    manager._config.thinking = "xhigh"
    manager.ensure_session()
    assert service.create_session.call_count == 1
    manager._rebuild_session(MagicMock())
    assert service.create_session.call_args.kwargs["thinking"] == "xhigh"


def test_anthropic_current_budget_and_disabled_branches_are_characterized():
    """#1197 current source anchor anthropic/adapter.py:675-688; later PR A2/A3/A4 may repair provider vocabulary only with a dedicated mapper."""
    adapter = AnthropicAdapter(api_key="fake")
    high = adapter.create_chat("claude", "system", thinking="high")
    assert high._extra_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert high._extra_kwargs["max_tokens"] == 32768
    low = adapter.create_chat("claude", "system", thinking="low")
    assert low._extra_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert low._extra_kwargs["max_tokens"] == 10240
    for value in ("default", None, "minimal", "medium", "xhigh", "none"):
        session = adapter.create_chat("claude", "system", thinking=value)
        assert "thinking" not in session._extra_kwargs
        assert "max_tokens" not in session._extra_kwargs


def test_gemini_hardcoded_high_ignored_value_omitted_branch_and_custom_identity(monkeypatch):
    """#1197 current source anchor gemini/adapter.py:823-854; later PR A2/A4 replaces only with a Gemini-owned mapper and wire contract."""
    class FakeGeminiClient:
        pass

    import lingtai.llm.gemini.adapter as gemini_module

    monkeypatch.setattr(gemini_module.genai, "Client", lambda **_kwargs: FakeGeminiClient())
    adapter = GeminiAdapter(api_key="fake")
    high = adapter.create_chat("gemini-3-pro", "system", thinking="none")
    assert high._config_kwargs["generation_config"] == {"thinking_level": "high"}
    old = adapter.create_chat("gemini-2.5-pro", "system", thinking="xhigh")
    assert "generation_config" not in old._config_kwargs
    custom = create_custom_adapter(
        api_key="fake", api_compat="gemini", base_url="https://custom.example/v1"
    )
    assert isinstance(custom, GeminiAdapter)
    assert not hasattr(custom, "_wire_api")


def test_gemini_json_schema_chat_branch_hardcodes_high_and_old_models_omit(monkeypatch):
    """#1197 current source anchor gemini/adapter.py:763-799; later PR A2/A4 replaces only with a Gemini-owned mapper and wire contract.

    The JSON-schema Chat branch mirrors Interactions: Gemini 3+ hard-codes a
    native high ThinkingConfig regardless of the caller's value; older models
    omit thinking config entirely. Locks wire construction only.
    """

    class _FakeChats:
        def __init__(self):
            self.kwargs = []

        def create(self, **kwargs):
            self.kwargs.append(kwargs)
            return SimpleNamespace()

    class _FakeGeminiClient:
        def __init__(self):
            self.chats = _FakeChats()

    import lingtai.llm.gemini.adapter as gemini_module

    monkeypatch.setattr(
        gemini_module.genai, "Client", lambda **_kwargs: _FakeGeminiClient()
    )
    adapter = GeminiAdapter(api_key="fake")
    schema = {"title": "answer", "type": "object", "properties": {}}

    adapter.create_chat("gemini-3-pro", "system", thinking="none", json_schema=schema)
    config = adapter._client.chats.kwargs[-1]["config"]
    assert config.thinking_config.thinking_level == "HIGH"
    assert config.thinking_config.include_thoughts is True
    assert config.response_mime_type == "application/json"

    adapter.create_chat("gemini-2.5-pro", "system", thinking="xhigh", json_schema=schema)
    old_config = adapter._client.chats.kwargs[-1]["config"]
    assert old_config.thinking_config is None


def test_deepseek_mimo_and_responses_native_defects_remain_unrepaired():
    """DS-1/DS-2/DS-3 — #1197 deepseek/mimo adapters; later PR A2 supplies dedicated mode/max/model-wire guards only when explicitly authorized."""
    deepseek_chat = DeepSeekAdapter(api_key="fake", wire_api="chat_completions")
    assert _chat_extra(deepseek_chat, "high")["reasoning_effort"] == "high"
    assert _chat_extra(deepseek_chat, "unknown")["reasoning_effort"] == "low"
    deepseek_responses = DeepSeekAdapter(api_key="fake", wire_api="responses")
    ds_session = deepseek_responses.create_chat("deepseek-reasoner", "system", thinking="high")
    assert ds_session._extra_kwargs["reasoning"] == {"effort": "high"}
    assert "max" not in ds_session._extra_kwargs
    assert "thinking" not in ds_session._extra_kwargs

    mimo_chat = MimoAdapter(api_key="fake", wire_api="chat_completions")
    assert _chat_extra(mimo_chat, "medium")["reasoning_effort"] == "low"
    mimo_responses = MimoAdapter(api_key="fake")
    mimo_session = mimo_responses.create_chat("mimo-v2", "system", thinking="high")
    assert mimo_session._extra_kwargs["reasoning"] == {"effort": "high"}
    assert getattr(mimo_session, "_response_id", None) is None


def test_DS3_deepseek_pro_responses_is_accepted_by_real_init_validation():
    """DS-3 — #1197 init_schema.py wire_api scope; later A2 replaces this only when a dedicated DeepSeek model/wire guard owns the acceptance change."""
    data = _valid_init()
    data["manifest"]["llm"] = {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "wire_api": "responses",
    }
    validate_init(data)


def test_zhipu_openrouter_and_kimi_inherit_current_mapper_without_repair():
    """Z-1/Z-2/K-1/K-2 — #1197 inherited OpenAI anchors; later PR A3/A4 owns route descriptors and exact endpoint/model defaults."""
    zhipu = ZhipuAdapter(api_key="fake")
    assert _chat_extra(zhipu, "high")["reasoning_effort"] == "high"
    assert _chat_extra(zhipu, "medium")["reasoning_effort"] == "low"
    assert build_agent_config({"llm": {"provider": "glm"}}, max_rpm=0).thinking == "high"

    openrouter = OpenRouterAdapter(api_key="fake")
    assert _chat_extra(openrouter, "default") == {
        "extra_body": {"reasoning": {"include": False}}
    }
    kimi_http = create_custom_adapter(
        api_key="fake", api_compat="openai", base_url="https://api.moonshot.cn/v1"
    )
    assert _chat_extra(kimi_http, "high")["reasoning_effort"] == "high"
    assert _chat_extra(kimi_http, "unknown")["reasoning_effort"] == "low"


def test_K1_K2_built_in_kimi_k27_route_emits_current_flat_high():
    """K-1/K-2 — #1197 _register.py:268 and OpenAI Chat construction; later A3 owns the exact Kimi model/endpoint route defaults."""
    service = ConcreteLLMService(
        provider="kimi",
        model="Kimi-K2.7",
        api_key="fake",
        base_url="https://api.moonshot.cn/v1",
        provider_defaults={"kimi": {"api_compat": "openai"}},
    )
    adapter = service.get_adapter("kimi")
    session = service.create_session(
        system_prompt="system", thinking="high", tracked=False
    )
    assert isinstance(adapter, OpenAIAdapter)
    assert session._extra_kwargs == {"reasoning_effort": "high"}


def test_Z1_Z2_main_high_differs_from_native_default_and_all_daemon_sites_are_anchored():
    """Z-1/Z-2 — #1197 Zhipu/GLM and the daemon default-handoff owner; later A4/A6 replace only after explicit route/parity proof."""
    from lingtai.tools.daemon import _native_daemon_thinking

    daemon_source = (ROOT / "src/lingtai/tools/daemon/__init__.py").read_text(encoding="utf-8")
    assert _native_daemon_thinking() == "default"
    assert daemon_source.count("thinking=_native_daemon_thinking()") == 4

    zhipu_main = _chat_extra(ZhipuAdapter(api_key="fake"), "high")
    zhipu_native = _chat_extra(ZhipuAdapter(api_key="fake"), "default")
    assert zhipu_main == {"reasoning_effort": "high"}
    assert zhipu_native == {}
    assert zhipu_main != zhipu_native

    kimi = ConcreteLLMService(
        provider="kimi",
        model="Kimi-K2.7",
        api_key="fake",
        base_url="https://api.moonshot.cn/v1",
        provider_defaults={"kimi": {"api_compat": "openai"}},
    )
    kimi_main = kimi.create_session(
        system_prompt="system", thinking="high", tracked=False
    )._extra_kwargs
    kimi_native = kimi.create_session(
        system_prompt="system", thinking="default", tracked=False
    )._extra_kwargs
    assert kimi_main == {"reasoning_effort": "high"}
    assert kimi_native == {}
    assert kimi_main != kimi_native


def test_minimax_and_custom_anthropic_inherit_current_native_budget_wiring():
    """#1197 minimax/adapter.py and custom/adapter.py; later A2/A4 replace native route mappers only with dedicated provider contracts."""
    minimax = MiniMaxAdapter(api_key="fake")
    custom = create_custom_adapter(
        api_key="fake",
        api_compat="anthropic",
        base_url="https://bedrock.example",
    )
    assert isinstance(minimax, AnthropicAdapter)
    assert isinstance(custom, AnthropicAdapter)
    for adapter in (minimax, custom):
        high = adapter.create_chat("model", "system", thinking="high")
        disabled = adapter.create_chat("model", "system", thinking="medium")
        assert high._extra_kwargs["thinking"] == {
            "type": "enabled",
            "budget_tokens": 16384,
        }
        assert high._extra_kwargs["max_tokens"] == 32768
        assert "thinking" not in disabled._extra_kwargs
        assert "max_tokens" not in disabled._extra_kwargs


def _claude_result(text="ok") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps({"action": "final", "text": text}),
            "session_id": "claude-issue-1197",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def _kimi_result(text="ok") -> str:
    return "\n".join(
        [
            json.dumps({"role": "assistant", "content": json.dumps({"action": "final", "text": text})}),
            json.dumps(
                {
                    "role": "meta",
                    "type": "session.resume_hint",
                    "session_id": "kimi-issue-1197",
                }
            ),
        ]
    ) + "\n"


def test_CL1_claude_code_first_and_resume_argv_omit_effort():
    """CL-1 — #1197 claude_code/adapter.py:679-690; later PR A5 emits validated effort only when both paths have explicit omission/clear semantics."""
    adapter = ClaudeCodeAdapter(model="sonnet")
    session = adapter.create_chat("sonnet", "system", thinking="xhigh")
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append((list(cmd), kwargs))
        return SimpleNamespace(stdout=_claude_result(), stderr="", returncode=0)

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        session.send("first")
        session.send("second")
    assert len(captured) == 2
    assert all("--effort" not in cmd for cmd, _kwargs in captured)
    assert "--resume" not in captured[0][0]
    assert "--resume" in captured[1][0]
    assert all("effort" not in env for _cmd, kwargs in captured for env in [kwargs["env"]])


def test_K3_kimi_code_first_and_resume_argv_env_omit_effort(tmp_path):
    """K-3 — #1197 kimi_code/adapter.py:718-736; later PR A3 consumes or explicitly rejects the control on both paths."""
    adapter = KimiCodeAdapter(model="kimi-for-coding", cwd=tmp_path / "kimi")
    session = adapter.create_chat("kimi-for-coding", "system", thinking="high")
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append((list(cmd), kwargs))
        return SimpleNamespace(stdout=_kimi_result(), stderr="", returncode=0)

    with patch("lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=fake_run):
        session.send("first")
        session.send("second")
    assert len(captured) == 2
    assert all("--effort" not in cmd for cmd, _kwargs in captured)
    assert "--session" not in captured[0][0]
    assert "--session" in captured[1][0]
    assert all("effort" not in env for _cmd, kwargs in captured for env in [kwargs["env"]])


def test_G7_C2_native_daemon_initial_and_all_rebuild_sites_keep_default():
    """G-7/C-2 — #1197 native default handoff (_native_daemon_thinking); later PR A6 replaces this only after explicit daemon preservation/parity proof.

    The four initial/rebuild construction sites now consume one named owner;
    the named-site AST proof lives in the structural suite. This fence locks
    the wire expectation: every native construction passes literal "default".
    """
    from lingtai.tools.daemon import _NATIVE_DAEMON_THINKING, _native_daemon_thinking

    source = (ROOT / "src/lingtai/tools/daemon/__init__.py").read_text(encoding="utf-8")
    assert _NATIVE_DAEMON_THINKING == "default"
    assert _native_daemon_thinking() == "default"
    assert source.count("thinking=_native_daemon_thinking()") == 4
    assert source.count("service.create_session(") >= 5


def test_avatar_default_preset_rematerialization_is_only_characterized():
    """#1197 avatar contract anchor tools/avatar/ANATOMY.md:124; PR1 must not reroute or alter avatar construction."""
    anatomy = (ROOT / "src/lingtai/tools/avatar/ANATOMY.md").read_text(encoding="utf-8")
    assert "Avatars always spawn on the parent's DEFAULT preset" in anatomy
    assert "Materialized `llm` + `capabilities` are stripped" in anatomy


def test_CL2_K4_external_daemon_raw_effort_and_ask_availability_remain_exact():
    """CL-2/K-4 — #1197 daemon backend-options anchors; later PR A5/D2 types startup/follow-up controls only when explicitly authorized."""
    assert _backend_options_to_argv({"effort": "high"}) == ["--effort", "high"]
    assert _BACKEND_SPECS["claude-code"].ask_handler_attr == "_handle_ask_cli"
    assert _BACKEND_SPECS["kimicode"].ask_handler_attr is None
    assert "ask" in (_BACKEND_SPECS["kimicode"].ask_unsupported_msg or "")
    ask_source = inspect.getsource(
        __import__("lingtai.tools.daemon", fromlist=["DaemonManager"]).DaemonManager._handle_ask_detached
    )
    assert "backend_argv" not in ask_source


def test_DS_status_shape_comes_from_real_identity_status_helper():
    """#1197 current status owner base_agent/identity.py:197-328; PR1 leaves the real status shape unrelated to effort."""
    usage = {
        "input_tokens": 1,
        "output_tokens": 2,
        "thinking_tokens": 3,
        "cached_tokens": 4,
        "total_tokens": 6,
        "api_calls": 1,
        "ctx_system_tokens": 7,
        "ctx_tools_tokens": 8,
        "ctx_history_tokens": 9,
        "ctx_total_tokens": 24,
    }
    agent = SimpleNamespace(
        _mail_service=None,
        _lifecycle_clock=SimpleNamespace(
            monotonic_seconds=lambda: 10.0,
            wall_seconds=lambda: 20.0,
        ),
        _uptime_anchor=0.0,
        get_token_usage=lambda: usage,
        _chat=SimpleNamespace(context_window=lambda: 1000),
        _config=SimpleNamespace(
            context_limit=None,
            time_awareness=False,
            timezone_awareness=False,
        ),
        _state_changed_at=None,
        _last_progress_at=None,
        _active_turn_kind=None,
        _deferred_notifications_count=0,
        _heartbeat=0.0,
        _started_at=None,
        _state=SimpleNamespace(value="idle"),
        _runtime_fingerprint=None,
        _agent_id="agent-id",
        _working_dir=Path("/tmp/issue-1197-status"),
        agent_name="alice",
        _session=SimpleNamespace(_token_fallback_warned=False),
    )
    payload = _status(agent)
    assert set(payload) == {"identity", "runtime", "tokens"}
    assert set(payload["identity"]) == {
        "agent_id", "address", "agent_name", "mail_address"
    }
    assert set(payload["tokens"]) == {
        "input_tokens", "output_tokens", "thinking_tokens", "cached_tokens",
        "total_tokens", "api_calls", "estimated", "context",
    }
    assert payload["identity"]["agent_name"] == "alice"
    assert payload["runtime"]["state"] == "idle"
    assert "effort" not in json.dumps(payload)


def test_codex_ws_env_selects_transport_and_keeps_current_nested_xhigh_payload(monkeypatch):
    """#1197 reconciliation: canonical openai/adapter.py:3140-3163 and 5877-5884.

    This locks only construction-time selector/payload facts on the current
    wire. It does not send a provider request or claim that the provider uses
    the requested effort effectively.
    """
    monkeypatch.setenv("LINGTAI_CODEX_TRANSPORT", "websocket")
    adapter = CodexOpenAIAdapter(
        api_key="fake", use_responses=True, force_responses=True
    )

    session = adapter.create_chat("model", "system")

    assert session._transport == "websocket"
    assert session._extra_kwargs["reasoning"] == {"effort": "xhigh"}
    assert "reasoning_effort" not in session._extra_kwargs
