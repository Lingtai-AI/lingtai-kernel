"""Structural checks for the authorized #1197 PR1 construction refactor."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.kernel.config import resolve_main_thinking, thinking_is_valid
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm.policy import ReasoningEmission, ReasoningPolicy, reasoning_policy
from lingtai.kernel.session import SessionManager
from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter
from lingtai.llm.kimi_code.adapter import KimiCodeAdapter
from lingtai.llm.openai.adapter import CodexOpenAIAdapter, OpenAIAdapter
from lingtai.llm.service import LLMService as ConcreteLLMService


def test_reasoning_policy_is_small_frozen_legacy_compatible_record():
    policy = ReasoningPolicy("medium")
    assert policy.effort == "medium"
    assert policy.mode is None
    assert policy.keep is None
    assert set(policy.__dataclass_fields__) == {"effort", "mode", "keep"}
    with pytest.raises((AttributeError, TypeError)):
        policy.effort = "high"
    assert reasoning_policy("medium") == policy
    assert reasoning_policy(None) == ReasoningPolicy("default")
    assert policy == "medium"


def test_reasoning_policy_scalar_equality_is_explicitly_unhashable():
    """The legacy scalar equality contract must not coexist with a mismatched hash."""
    policy = ReasoningPolicy("medium")
    assert policy == "medium"
    assert policy.__hash__ is None
    with pytest.raises(TypeError):
        hash(policy)


def test_reasoning_emission_is_immutable_and_serializes_only_exact_event_shape():
    emitted = ReasoningEmission(
        requested="medium", emitted=("reasoning_effort", "low")
    )
    omitted = ReasoningEmission(requested="xhigh", omitted=True)
    assert emitted.as_dict() == {
        "requested": "medium",
        "emitted": {"path": "reasoning_effort", "value": "low"},
    }
    assert omitted.as_dict() == {"requested": "xhigh", "omitted": True}
    with pytest.raises(ValueError):
        ReasoningEmission(requested="medium")
    with pytest.raises(ValueError):
        ReasoningEmission(requested="medium", emitted=("path", "value"), omitted=True)


def test_shared_config_helpers_preserve_gate_and_main_baseline():
    assert all(thinking_is_valid(value) for value in ("none", "minimal", "low", "medium", "high", "xhigh"))
    assert not thinking_is_valid("HIGH")
    assert resolve_main_thinking({"provider": "codex"}) == "default"
    assert resolve_main_thinking({"provider": "codex_pool"}) == "default"
    assert resolve_main_thinking({"provider": "openai"}) == "high"
    assert resolve_main_thinking({"provider": "custom", "wire_api": "responses"}) == "high"
    assert resolve_main_thinking({"provider": "codex", "thinking": "low"}) == "low"


def test_session_manager_constructs_policy_at_ensure_and_rebuild_points():
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
    first = service.create_session.call_args.kwargs["thinking"]
    assert isinstance(first, ReasoningPolicy)
    assert first.effort == "medium"
    manager._config.thinking = "xhigh"
    manager._rebuild_session(MagicMock())
    second = service.create_session.call_args.kwargs["thinking"]
    assert isinstance(second, ReasoningPolicy)
    assert second.effort == "xhigh"
    assert first is not second


def test_concrete_service_accepts_structured_and_scalar_values_and_keeps_metadata_on_gate():
    service = ConcreteLLMService(
        provider="openai",
        model="test-model",
        api_key="fake",
        provider_defaults={"openai": {"max_rpm": 1, "wire_api": "chat_completions"}},
    )
    structured = ReasoningPolicy("medium")
    structured_chat = service.create_session(
        system_prompt="system", thinking=structured, tracked=False
    )
    scalar_chat = service.create_session(
        system_prompt="system", thinking="high", tracked=False
    )
    assert structured_chat.reasoning_policy is structured
    assert structured_chat.reasoning_emission.as_dict() == {
        "requested": "medium",
        "emitted": {"path": "reasoning_effort", "value": "low"},
        "route": "openai/chat",
    }
    assert scalar_chat.reasoning_policy == ReasoningPolicy("high")
    assert scalar_chat.reasoning_emission.as_dict() == {
        "requested": "high",
        "emitted": {"path": "reasoning_effort", "value": "high"},
        "route": "openai/chat",
    }


@pytest.mark.parametrize("thinking", ["none", "minimal", "low", "medium", "high", "xhigh"])
def test_openai_responses_emission_records_exact_nested_path(thinking):
    adapter = OpenAIAdapter(api_key="fake", wire_api="responses")
    session = adapter.create_chat("model", "system", thinking=thinking)
    assert session.reasoning_emission.as_dict() == {
        "requested": thinking,
        "emitted": {"path": "reasoning.effort", "value": thinking},
        "route": "openai/responses",
    }


def test_openai_responses_default_omission_and_codex_default_xhigh_are_distinct():
    generic = OpenAIAdapter(api_key="fake", wire_api="responses")
    assert generic.create_chat("model", "system").reasoning_emission.as_dict() == {
        "requested": "default",
        "omitted": True,
        "route": "openai/responses",
    }
    assert generic.create_chat("model", "system", thinking=None).reasoning_emission.as_dict() == {
        "requested": "default",
        "omitted": True,
        "route": "openai/responses",
    }
    codex = CodexOpenAIAdapter(api_key="fake", use_responses=True, force_responses=True)
    assert codex.create_chat("model", "system").reasoning_emission.as_dict() == {
        "requested": "default",
        "emitted": {"path": "reasoning.effort", "value": "xhigh"},
        "route": "codex/responses",
        "source": "codex-default",
    }


@pytest.mark.parametrize(
    ("thinking", "expected"),
    [
        ("default", {"requested": "default", "omitted": True, "route": "openai/chat"}),
        ("high", {"requested": "high", "emitted": {"path": "reasoning_effort", "value": "high"}, "route": "openai/chat"}),
        ("medium", {"requested": "medium", "emitted": {"path": "reasoning_effort", "value": "low"}, "route": "openai/chat"}),
        (None, {"requested": "default", "emitted": {"path": "reasoning_effort", "value": "low"}, "route": "openai/chat"}),
    ],
)
def test_openai_chat_emission_preserves_current_mapper_boundary(thinking, expected):
    adapter = OpenAIAdapter(api_key="fake", wire_api="chat_completions")
    assert adapter.create_chat("model", "system", thinking=thinking).reasoning_emission.as_dict() == expected


def test_explicit_cli_drop_records_omission_without_changing_cli_session_shape(tmp_path):
    claude = ClaudeCodeAdapter(model="claude")
    kimi = KimiCodeAdapter(model="kimi", cwd=tmp_path / "kimi")
    for adapter, route in ((claude, "claude-code/cli"), (kimi, "kimi-code/cli")):
        session = adapter.create_chat("model", "system", thinking="high")
        assert session.reasoning_emission.as_dict() == {
            "requested": "high",
            "omitted": True,
            "dropped": True,
            "route": route,
            "phase": "first",
        }


def test_llm_call_reasoning_is_additive_and_captured_not_recomputed():
    events = []
    service = MagicMock()
    service.model = "test-model"
    session = MagicMock()
    session.context_window.return_value = 100000
    session.interface.estimate_context_tokens.return_value = 5000
    session.interface.current_system_prompt = "system"
    session.reasoning_emission = ReasoningEmission(
        requested="medium", emitted=("reasoning_effort", "low")
    )
    session.send.return_value = SimpleNamespace(
        text="ok",
        tool_calls=[],
        thoughts=[],
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            thinking_tokens=0,
            cached_tokens=0,
            extra={},
        ),
        api_call_id=None,
    )
    service.create_session.return_value = session
    manager = SessionManager(
        llm_service=service,
        config=AgentConfig(thinking="high"),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "system",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event, **fields: events.append((event, fields)),
    )
    manager.send("hello")
    call = next(fields for event, fields in events if event == "llm_call")
    assert call == {
        "model": "test-model",
        "api_call_id": call["api_call_id"],
        "reasoning": {
            "requested": "medium",
            "emitted": {"path": "reasoning_effort", "value": "low"},
        },
    }


# ---------------------------------------------------------------------------
# Original-goal (msg1158) route-ownership repair — structural contract.
#
# Every test below binds one owner required by the six-responsibility
# decomposition restored in pr1-parent-original-goal-audit.md and
# pr1-original-goal-reconciliation.md. They are behavior-preserving: each
# asserts the exact current wire bytes next to the new ownership shape.
# ---------------------------------------------------------------------------


_REQUIRED_ROUTE_IDS = {
    "main:codex:responses",
    "main:openai:responses:official",
    "main:openai:responses:custom",
    "main:openai:chat",
    "main:custom-endpoint:chat",
    "main:openrouter:chat",
    "main:zhipu:chat",
    "main:deepseek:chat",
    "main:deepseek:responses",
    "main:mimo:chat",
    "main:mimo:responses",
    "main:anthropic:messages",
    "main:minimax:messages",
    "main:custom-anthropic:messages",
    "main:gemini:interactions:3plus",
    "main:gemini:interactions:pre3",
    "main:gemini:chat:3plus",
    "main:gemini:chat:pre3",
    "main:claude-code:cli:first",
    "main:claude-code:cli:resume",
    "main:kimi-code:cli:first",
    "main:kimi-code:cli:resume",
    "native-daemon:session:default",
    "external-daemon:startup:argv",
    "external-daemon:claude-code:ask",
    "external-daemon:kimicode:ask",
}


def _routes_module():
    import importlib.util

    spec = importlib.util.find_spec("lingtai.kernel.llm.routes")
    assert spec is not None, (
        "no production current-wiring route-description module exists "
        "(lingtai.kernel.llm.routes)"
    )
    import lingtai.kernel.llm.routes as routes

    return routes


def test_original_goal_route_catalogue_exists_and_covers_required_routes():
    routes = _routes_module()
    catalogue = {route.route_id: route for route in routes.REASONING_ROUTES}
    missing = _REQUIRED_ROUTE_IDS - set(catalogue)
    assert not missing, f"route catalogue is missing owners for: {sorted(missing)}"

    integrations = {route.integration for route in catalogue.values()}
    assert {"main", "native-daemon", "external-daemon"} <= integrations

    for route in catalogue.values():
        with pytest.raises((AttributeError, TypeError)):
            route.baseline = "mutated"
        assert route.baseline, f"{route.route_id} lacks an omission-baseline source"
        assert route.facts, f"{route.route_id} carries no mechanical facts"
        for fact in route.facts:
            assert fact.disposition in (
                "emitted", "omitted", "dropped", "error", "forced", "raw"
            )
            if (
                fact.disposition == "emitted"
                # The startup record's exact per-input path/value is owned by
                # the callable describe_startup_reasoning (B12); its static
                # facts are typed-domain categories with no fixed path claim.
                and route.route_id != "external-daemon:startup:argv"
            ):
                assert fact.path, f"{route.route_id}:{fact.input} lacks an exact path"
                assert fact.value is not None

    # Model-gated identity must be an explicit key, not prose.
    assert catalogue["main:gemini:interactions:3plus"].model_predicate
    assert catalogue["main:gemini:chat:3plus"].model_predicate
    # CLI phase is an identity dimension where command construction differs.
    assert catalogue["main:claude-code:cli:first"].phase == "first"
    assert catalogue["main:claude-code:cli:resume"].phase == "resume"
    assert catalogue["main:kimi-code:cli:first"].phase == "first"
    assert catalogue["main:kimi-code:cli:resume"].phase == "resume"

    # Exact current-defect fence stays greppable at the description layer.
    chat = catalogue["main:openai:chat"]
    assert "G-3" in chat.defects
    assert any(
        fact.input == "other" and fact.disposition == "emitted"
        and fact.path == "reasoning_effort" and fact.value == "low"
        for fact in chat.facts
    )
    codex = catalogue["main:codex:responses"]
    assert any(
        fact.input == "default" and fact.disposition == "emitted"
        and fact.path == "reasoning.effort" and fact.value == "xhigh"
        for fact in codex.facts
    )
    anthropic = catalogue["main:anthropic:messages"]
    assert any(
        fact.disposition == "emitted" and fact.path == "thinking.budget_tokens"
        and ("max_tokens", "32768") in fact.coupled
        for fact in anthropic.facts
    )
    openrouter = catalogue["main:openrouter:chat"]
    assert any(
        ("extra_body.reasoning.include", "False") in fact.coupled
        for fact in openrouter.facts
    )
    native = catalogue["native-daemon:session:default"]
    assert any(
        fact.disposition == "forced" and fact.value == "default"
        for fact in native.facts
    )
    startup = catalogue["external-daemon:startup:argv"]
    # Raw pass-through categories: omission and emission both occur; exact
    # per-input paths are owned by the callable description (B12).
    assert {fact.disposition for fact in startup.facts} == {"omitted", "emitted"}


def test_original_goal_manifest_gate_is_projection_of_route_admission():
    routes = _routes_module()
    from lingtai.kernel import config as kernel_config

    assert kernel_config.llm_supports_thinking is routes.llm_supports_thinking, (
        "llm_supports_thinking is not the route catalogue's admission projection"
    )

    admission_keys = {
        key
        for route in routes.REASONING_ROUTES
        for key in route.admission_keys
    }
    assert admission_keys == {
        ("codex", None, None),
        ("codex-pool", None, None),
        ("codex_pool", None, None),
        ("custom", "openai", "responses"),
    }

    # Exact accepted/rejected set preserved by the projection.
    gate = kernel_config.llm_supports_thinking
    assert gate({"provider": "codex"}) is True
    assert gate({"provider": "CODEX-POOL"}) is True
    assert gate({"provider": "codex_pool", "wire_api": "chat_completions"}) is True
    assert gate({"provider": "custom", "api_compat": "openai", "wire_api": "responses"}) is True
    assert gate({"provider": "custom", "api_compat": "OPENAI", "wire_api": "Responses"}) is True
    assert gate({"provider": "custom", "api_compat": "openai", "wire_api": "chat_completions"}) is False
    assert gate({"provider": "custom", "api_compat": "anthropic", "wire_api": "responses"}) is False
    assert gate({"provider": "kimi", "api_compat": "openai", "wire_api": "responses"}) is False
    assert gate({"provider": "anthropic"}) is False
    assert gate({}) is False


def test_original_goal_normalization_owner_is_explicit_empty_and_routed(monkeypatch):
    from lingtai.kernel import config as kernel_config

    assert hasattr(kernel_config, "THINKING_ALIASES"), (
        "no explicit input-normalization vocabulary exists beside validation"
    )
    assert hasattr(kernel_config, "normalize_thinking")
    assert dict(kernel_config.THINKING_ALIASES) == {}, (
        "the alias table must be empty/identity today"
    )
    with pytest.raises(TypeError):
        kernel_config.THINKING_ALIASES["alias"] = "high"
    for value in ("none", "minimal", "low", "medium", "high", "xhigh", "default"):
        assert kernel_config.normalize_thinking(value) == value
    # Chat's historical collapse must NOT live in normalization.
    assert kernel_config.normalize_thinking("medium") == "medium"

    # Both validation and the explicit baseline branch route through the
    # one normalization owner: an alias added there changes both at once.
    monkeypatch.setattr(
        kernel_config, "normalize_thinking", lambda value: "high"
    )
    assert kernel_config.thinking_is_valid("totally-unknown") is True
    assert (
        kernel_config.resolve_main_thinking(
            {"provider": "anthropic", "thinking": "totally-unknown"}
        )
        == "high"
    )


def test_original_goal_construction_result_carrier_is_neutral_and_selfconsistent():
    from lingtai.kernel.llm import policy

    assert hasattr(policy, "ReasoningConstruction"), (
        "no neutral immutable construction-result carrier exists"
    )
    carrier = policy.ReasoningConstruction

    emitted = carrier(
        route="openai/responses",
        requested="medium",
        disposition="emitted",
        json_delta=(("reasoning", {"effort": "medium"}),),
        emitted_path="reasoning.effort",
        emitted_value="medium",
    )
    with pytest.raises((AttributeError, TypeError)):
        emitted.disposition = "omitted"
    evidence = emitted.emission()
    assert isinstance(evidence, policy.ReasoningEmission)
    assert evidence.as_dict()["emitted"] == {
        "path": "reasoning.effort", "value": "medium"
    }

    # Contradictory disposition/delta/evidence shapes must not construct.
    with pytest.raises(ValueError):
        carrier(route="r", requested="high", disposition="emitted")
    with pytest.raises(ValueError):
        carrier(
            route="r", requested="high", disposition="omitted",
            emitted_path="reasoning.effort", emitted_value="high",
        )
    with pytest.raises(ValueError):
        carrier(
            route="r", requested="high", disposition="emitted",
            json_delta=(("reasoning", {"effort": "high"}),),
            emitted_path="reasoning.effort", emitted_value="low",
        )

    # Coupled fields ride one result (Anthropic-shaped) and verify against
    # the actual delta rather than a second table.
    coupled = carrier(
        route="anthropic/messages",
        requested="high",
        disposition="emitted",
        json_delta=(
            ("thinking", {"type": "enabled", "budget_tokens": 16384}),
            ("max_tokens", 32768),
        ),
        emitted_path="thinking.budget_tokens",
        emitted_value="16384",
        coupled=(("thinking.type", "enabled"), ("max_tokens", "32768")),
    )
    assert coupled.emission().as_dict()["coupled"] == [
        {"path": "thinking.type", "value": "enabled"},
        {"path": "max_tokens", "value": "32768"},
    ]
    with pytest.raises(ValueError):
        carrier(
            route="anthropic/messages", requested="high", disposition="emitted",
            json_delta=(("max_tokens", 32768),),
            emitted_path="max_tokens", emitted_value="32768",
            coupled=(("thinking.type", "enabled"),),
        )

    dropped = carrier(route="claude-code/cli", requested="high", disposition="dropped", phase="first")
    assert dropped.emission().as_dict() == {
        "requested": "high",
        "omitted": True,
        "dropped": True,
        "route": "claude-code/cli",
        "phase": "first",
    }


def test_original_goal_reasoning_emission_is_frozen_by_behavior_and_extended():
    emitted = ReasoningEmission(requested="medium", emitted=("reasoning_effort", "low"))
    with pytest.raises((AttributeError, TypeError)):
        emitted.requested = "high"
    with pytest.raises((AttributeError, TypeError)):
        emitted.emitted = ("reasoning_effort", "high")
    with pytest.raises((AttributeError, TypeError)):
        emitted.omitted = True

    extended = ReasoningEmission(
        requested="high",
        omitted=True,
        dropped=True,
        route="claude-code/cli",
        phase="resume",
    )
    assert extended.as_dict() == {
        "requested": "high",
        "omitted": True,
        "dropped": True,
        "route": "claude-code/cli",
        "phase": "resume",
    }
    # The legacy shape stays byte-identical when no expansion field is set.
    assert emitted.as_dict() == {
        "requested": "medium",
        "emitted": {"path": "reasoning_effort", "value": "low"},
    }
    with pytest.raises(ValueError):
        ReasoningEmission(
            requested="high",
            emitted=("reasoning_effort", "high"),
            dropped=True,
        )


def test_original_goal_openai_responses_bytes_and_record_share_one_owner(monkeypatch):
    import lingtai.llm.openai.adapter as oa

    assert hasattr(oa, "responses_reasoning_construction"), (
        "no route-local OpenAI Responses construction owner exists"
    )
    assert not hasattr(oa, "_responses_reasoning_kwargs"), (
        "superseded Responses kwargs shadow owner still exists"
    )
    assert not hasattr(oa, "_responses_reasoning_emission"), (
        "superseded Responses evidence shadow owner still exists"
    )

    calls = []
    real = oa.responses_reasoning_construction

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(oa, "responses_reasoning_construction", spy)
    adapter = oa.OpenAIAdapter(api_key="fake", wire_api="responses")
    session = adapter.create_chat("model", "system", thinking="medium")
    assert len(calls) == 1, "the route mapper must run exactly once per construction"
    assert session._extra_kwargs["reasoning"] == {"effort": "medium"}
    emission = session.reasoning_emission
    assert emission.route == "openai/responses"
    assert emission.as_dict()["emitted"] == {"path": "reasoning.effort", "value": "medium"}


def test_original_goal_openai_chat_bytes_and_record_share_one_owner(monkeypatch):
    import lingtai.llm.openai.adapter as oa

    assert hasattr(oa, "chat_reasoning_construction"), (
        "no route-local OpenAI Chat construction owner exists"
    )
    assert not hasattr(oa, "_chat_reasoning_emission"), (
        "superseded Chat evidence shadow owner still exists"
    )

    calls = []
    real = oa.chat_reasoning_construction

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(oa, "chat_reasoning_construction", spy)
    adapter = oa.OpenAIAdapter(api_key="fake", wire_api="chat_completions")
    session = adapter.create_chat("model", "system", thinking="medium")
    assert len(calls) == 1
    assert session._extra_kwargs == {"reasoning_effort": "low"}
    emission = session.reasoning_emission
    assert emission.route == "openai/chat"
    assert emission.as_dict()["emitted"] == {"path": "reasoning_effort", "value": "low"}


def test_original_goal_codex_route_owner_derives_default_xhigh_once(monkeypatch):
    import lingtai.llm.openai.adapter as oa

    calls = []
    real = oa.responses_reasoning_construction

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(oa, "responses_reasoning_construction", spy)
    codex = oa.CodexOpenAIAdapter(api_key="fake", use_responses=True, force_responses=True)
    session = codex.create_chat("model", "system")
    assert len(calls) == 1
    assert session._extra_kwargs["reasoning"] == {"effort": "xhigh"}
    emission = session.reasoning_emission
    assert emission.route == "codex/responses"
    assert emission.requested == "default"
    assert emission.emitted == ("reasoning.effort", "xhigh")


def test_original_goal_deepseek_and_mimo_responses_routes_own_result_and_record():
    from lingtai.llm.deepseek.adapter import DeepSeekAdapter
    from lingtai.llm.mimo.adapter import MimoAdapter

    ds = DeepSeekAdapter(api_key="fake", wire_api="responses")
    ds_session = ds.create_chat("deepseek-reasoner", "system", thinking="high")
    assert ds_session._extra_kwargs["reasoning"] == {"effort": "high"}
    ds_emission = ds_session.reasoning_emission
    assert ds_emission.route == "deepseek/responses"
    assert ds_emission.emitted == ("reasoning.effort", "high")

    mimo = MimoAdapter(api_key="fake")
    mimo_session = mimo.create_chat("mimo-v2", "system", thinking="high")
    assert mimo_session._extra_kwargs["reasoning"] == {"effort": "high"}
    mimo_emission = mimo_session.reasoning_emission
    assert mimo_emission.route == "mimo/responses"
    assert mimo_emission.emitted == ("reasoning.effort", "high")


def test_original_goal_openrouter_route_owns_coupled_extra_body():
    from lingtai.llm.openrouter.adapter import OpenRouterAdapter

    adapter = OpenRouterAdapter(api_key="fake")

    omitted = adapter.create_chat("model", "system", thinking="default")
    assert omitted._extra_kwargs == {"extra_body": {"reasoning": {"include": False}}}
    omitted_emission = omitted.reasoning_emission
    assert omitted_emission.route == "openrouter/chat"
    assert omitted_emission.omitted is True
    assert ("extra_body.reasoning.include", "False") in omitted_emission.coupled

    collapsed = adapter.create_chat("model", "system", thinking="medium")
    assert collapsed._extra_kwargs == {
        "reasoning_effort": "low",
        "extra_body": {"reasoning": {"include": False}},
    }
    collapsed_emission = collapsed.reasoning_emission
    assert collapsed_emission.emitted == ("reasoning_effort", "low")
    assert ("extra_body.reasoning.include", "False") in collapsed_emission.coupled


def test_original_goal_inherited_chat_wrappers_own_route_identity():
    from lingtai.llm.deepseek.adapter import DeepSeekAdapter
    from lingtai.llm.mimo.adapter import MimoAdapter
    from lingtai.llm.zhipu.adapter import ZhipuAdapter

    zhipu = ZhipuAdapter(api_key="fake").create_chat("glm", "system", thinking="high")
    assert zhipu._extra_kwargs == {"reasoning_effort": "high"}
    assert zhipu.reasoning_emission.route == "zhipu/chat"

    ds = DeepSeekAdapter(api_key="fake", wire_api="chat_completions")
    ds_session = ds.create_chat("deepseek-chat", "system", thinking="medium")
    assert ds_session._extra_kwargs == {"reasoning_effort": "low"}
    assert ds_session.reasoning_emission.route == "deepseek/chat"

    mimo = MimoAdapter(api_key="fake", wire_api="chat_completions")
    mimo_session = mimo.create_chat("mimo", "system", thinking="medium")
    assert mimo_session._extra_kwargs == {"reasoning_effort": "low"}
    assert mimo_session.reasoning_emission.route == "mimo/chat"

    from lingtai.llm.custom.adapter import create_custom_adapter

    kimi = create_custom_adapter(
        api_key="fake", api_compat="openai", base_url="https://api.moonshot.cn/v1"
    )
    kimi_session = kimi.create_chat("Kimi-K2.7", "system", thinking="high")
    assert kimi_session._extra_kwargs == {"reasoning_effort": "high"}
    assert kimi_session.reasoning_emission.route.startswith("openai-compat:")


def test_original_goal_anthropic_family_routes_own_coupled_result():
    from lingtai.llm.anthropic.adapter import AnthropicAdapter
    from lingtai.llm.custom.adapter import create_custom_adapter
    from lingtai.llm.minimax.adapter import MiniMaxAdapter

    anthropic = AnthropicAdapter(api_key="fake")
    high = anthropic.create_chat("claude", "system", thinking="high")
    assert high._extra_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert high._extra_kwargs["max_tokens"] == 32768
    emission = high.reasoning_emission
    assert emission.route == "anthropic/messages"
    assert emission.emitted == ("thinking.budget_tokens", "16384")
    assert ("max_tokens", "32768") in emission.coupled

    dropped = anthropic.create_chat("claude", "system", thinking="medium")
    assert "thinking" not in dropped._extra_kwargs
    dropped_emission = dropped.reasoning_emission
    assert dropped_emission.omitted is True
    assert dropped_emission.dropped is True

    minimax = MiniMaxAdapter(api_key="fake")
    minimax_session = minimax.create_chat("model", "system", thinking="high")
    assert minimax_session.reasoning_emission.route == "minimax/messages"
    assert minimax_session._extra_kwargs["max_tokens"] == 32768

    custom = create_custom_adapter(
        api_key="fake", api_compat="anthropic", base_url="https://bedrock.example"
    )
    custom_session = custom.create_chat("model", "system", thinking="low")
    assert custom_session._extra_kwargs["thinking"] == {
        "type": "enabled", "budget_tokens": 2048
    }
    assert custom_session._extra_kwargs["max_tokens"] == 10240
    assert custom_session.reasoning_emission.route == "anthropic-compat/messages"
    assert custom_session.reasoning_emission.emitted == ("thinking.budget_tokens", "2048")


def test_original_goal_gemini_interactions_and_chat_routes_own_result(monkeypatch):
    import lingtai.llm.gemini.adapter as gemini_module

    class FakeChats:
        def __init__(self):
            self.kwargs = []

        def create(self, **kwargs):
            self.kwargs.append(kwargs)
            return SimpleNamespace()

    class FakeGeminiClient:
        def __init__(self):
            self.chats = FakeChats()

    monkeypatch.setattr(
        gemini_module.genai, "Client", lambda **_kwargs: FakeGeminiClient()
    )
    adapter = gemini_module.GeminiAdapter(api_key="fake")

    # Interactions route (no json_schema): model-gated hard-coded high, the
    # caller's value is ignored and recorded as requested.
    interactions = adapter.create_chat("gemini-3-pro", "system", thinking="none")
    assert interactions._config_kwargs["generation_config"] == {"thinking_level": "high"}
    emission = interactions.reasoning_emission
    assert emission.route == "gemini/interactions"
    assert emission.requested == "none"
    assert emission.emitted == ("generation_config.thinking_level", "high")

    old_interactions = adapter.create_chat("gemini-2.5-pro", "system", thinking="xhigh")
    assert "generation_config" not in old_interactions._config_kwargs
    old_emission = old_interactions.reasoning_emission
    assert old_emission.omitted is True
    assert old_emission.dropped is True

    # JSON-schema Chat route: the native ThinkingConfig shape.
    schema = {"title": "answer", "type": "object", "properties": {}}
    chat = adapter.create_chat(
        "gemini-3-pro", "system", thinking="none", json_schema=schema
    )
    create_kwargs = adapter._client.chats.kwargs[-1]
    config = create_kwargs["config"]
    assert config.thinking_config.thinking_level == "HIGH"
    assert config.thinking_config.include_thoughts is True
    chat_emission = chat.reasoning_emission
    assert chat_emission.route == "gemini/chat"
    assert chat_emission.requested == "none"
    assert chat_emission.emitted == ("thinking_config.thinking_level", "HIGH")

    old_chat = adapter.create_chat(
        "gemini-2.5-pro", "system", thinking="xhigh", json_schema=schema
    )
    old_config = adapter._client.chats.kwargs[-1]["config"]
    assert old_config.thinking_config is None
    old_chat_emission = old_chat.reasoning_emission
    assert old_chat_emission.omitted is True
    assert old_chat_emission.dropped is True


def test_original_goal_cli_routes_consume_one_result_at_real_command_builders(tmp_path):
    from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter
    from lingtai.llm.kimi_code.adapter import KimiCodeAdapter

    claude = ClaudeCodeAdapter(model="sonnet")
    claude_session = claude.create_chat("sonnet", "system", thinking="high")
    first_record = claude_session.reasoning_emission
    assert first_record.route == "claude-code/cli"
    assert first_record.phase == "first"
    assert first_record.omitted is True and first_record.dropped is True

    captured = []

    def fake_run(cmd, **kwargs):
        captured.append((list(cmd), kwargs))
        return SimpleNamespace(stdout=_claude_result_json(), stderr="", returncode=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "lingtai.llm.claude_code.adapter.subprocess.run", fake_run
        )
        claude_session.send("first")
        claude_session.send("second")
    assert len(captured) == 2
    assert all("--effort" not in cmd for cmd, _ in captured)
    assert "--resume" in captured[1][0]
    resume_record = claude_session.reasoning_emission
    assert resume_record.phase == "resume"
    assert resume_record.dropped is True

    # A default policy now records mechanical omission (not silence) while
    # the argv/env bytes stay untouched.
    default_session = claude.create_chat("sonnet", "system", thinking="default")
    default_record = default_session.reasoning_emission
    assert default_record.requested == "default"
    assert default_record.omitted is True and default_record.dropped is False

    kimi = KimiCodeAdapter(model="kimi-for-coding", cwd=tmp_path / "kimi")
    kimi_session = kimi.create_chat("kimi-for-coding", "system", thinking="high")
    kimi_record = kimi_session.reasoning_emission
    assert kimi_record.route == "kimi-code/cli"
    assert kimi_record.phase == "first"
    assert kimi_record.dropped is True

    kimi_captured = []

    def kimi_fake_run(cmd, **kwargs):
        kimi_captured.append((list(cmd), kwargs))
        return SimpleNamespace(stdout=_kimi_result_json(), stderr="", returncode=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("lingtai.llm.kimi_code.adapter.subprocess.run", kimi_fake_run)
        kimi_session.send("first")
        kimi_session.send("second")
    assert len(kimi_captured) == 2
    assert all("--effort" not in cmd for cmd, _ in kimi_captured)
    assert "--session" in kimi_captured[1][0]
    assert kimi_session.reasoning_emission.phase == "resume"


def _claude_result_json(text="ok"):
    import json as _json

    return _json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": _json.dumps({"action": "final", "text": text}),
            "session_id": "claude-original-goal",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def _kimi_result_json(text="ok"):
    import json as _json

    return "\n".join(
        [
            _json.dumps(
                {
                    "role": "assistant",
                    "content": _json.dumps({"action": "final", "text": text}),
                }
            ),
            _json.dumps(
                {
                    "role": "meta",
                    "type": "session.resume_hint",
                    "session_id": "kimi-original-goal",
                }
            ),
        ]
    ) + "\n"


def test_original_goal_native_daemon_default_handoff_has_named_owner():
    import ast
    import inspect

    import lingtai.tools.daemon as daemon_module

    assert hasattr(daemon_module, "_native_daemon_thinking"), (
        "no production owner exists for the native-daemon default handoff"
    )
    assert daemon_module._native_daemon_thinking() == "default"

    source = inspect.getsource(daemon_module)
    tree = ast.parse(source)
    sites = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "create_session":
                for keyword in node.keywords:
                    if keyword.arg == "thinking":
                        sites.append((tuple(self.stack), keyword.value))
            self.generic_visit(node)

    Visitor().visit(tree)
    assert len(sites) == 4, (
        "the native daemon must construct sessions at exactly the four "
        f"initial/rebuild sites (found {len(sites)})"
    )
    # Initial session and the compact-reset rebuild live directly in
    # _run_emanation; the other rebuilds live in its nested mechanical
    # compact and AED empty-response recovery helpers.
    enclosing = sorted(stack[-1] for stack, _value in sites)
    assert enclosing == [
        "_mechanically_compact",
        "_recover_empty_response",
        "_run_emanation",
        "_run_emanation",
    ]
    for stack, value in sites:
        assert (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_native_daemon_thinking"
        ), f"site in {stack[-1]} does not consume the named default-handoff owner"


def test_original_goal_external_daemon_startup_phase_has_named_owner():
    import ast
    import inspect

    import lingtai.tools.daemon as daemon_module

    assert hasattr(daemon_module, "_startup_backend_options_argv"), (
        "no production owner exists for the external-daemon startup handoff"
    )
    assert daemon_module._startup_backend_options_argv({"effort": "high"}) == [
        "--effort", "high"
    ]
    assert daemon_module._startup_backend_options_argv(None) == []

    import textwrap

    source = inspect.getsource(daemon_module.DaemonManager._handle_emanate_cli)
    tree = ast.parse(textwrap.dedent(source))
    used = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_startup_backend_options_argv"
        for node in ast.walk(tree)
    )
    assert used, "_handle_emanate_cli does not consume the startup-phase owner"
    # The ask/resume phase stays a fixed-argv construction with no startup
    # backend options; the historical fence in the characterization file and
    # the functional daemon suites keep that inequality locked.
    ask_source = inspect.getsource(daemon_module.DaemonManager._handle_ask_cli)
    assert "backend_argv" not in ask_source


# ---------------------------------------------------------------------------
# Parent blocker amendment (pr1-original-goal-repair-parent-blockers.md).
# B1 deep immutability, B2 exact evidence path + endpoint-keyed/queryable
# description, B3 same-call CLI phase evidence, B4 daemon neutral-result
# ownership. All behavior-preserving.
# ---------------------------------------------------------------------------


def test_amendment_b1_construction_result_is_deeply_immutable():
    import lingtai.llm.openai.adapter as oa
    from collections.abc import Mapping

    from lingtai.kernel.llm.policy import ReasoningConstruction

    construction = oa.responses_reasoning_construction("high", route="openai/responses")
    assert hasattr(construction, "materialize_json"), (
        "no byte-identical materialization method exists on the stored result"
    )
    assert construction.materialize_json() == {"reasoning": {"effort": "high"}}

    # The stored nested delta must refuse mutation outright.
    fragment = dict(construction.json_delta)["reasoning"]
    with pytest.raises((TypeError, AttributeError)):
        fragment["effort"] = "low"

    # Mutating a materialized copy must never write back into the result.
    payload = construction.materialize_json()
    payload["reasoning"]["effort"] = "low"
    assert construction.materialize_json() == {"reasoning": {"effort": "high"}}
    assert construction.emission().as_dict()["emitted"]["value"] == "high"

    # Coupled Anthropic fragment: same deep guarantee, byte-identical shape.
    from lingtai.llm.anthropic.adapter import AnthropicAdapter

    anthropic = AnthropicAdapter(api_key="fake")._messages_reasoning_construction("high")
    with pytest.raises((TypeError, AttributeError)):
        dict(anthropic.json_delta)["thinking"]["budget_tokens"] = 1
    assert anthropic.materialize_json() == {
        "thinking": {"type": "enabled", "budget_tokens": 16384},
        "max_tokens": 32768,
    }

    # Gemini's native shape must not be stored as an opaque SDK object; the
    # stored form is an immutable mapping and the site materializes it.
    import lingtai.llm.gemini.adapter as gemini_module

    gemini = gemini_module._chat_reasoning_construction("gemini-3-pro", "none")
    stored = dict(gemini.json_delta)["thinking_config"]
    assert isinstance(stored, Mapping)
    with pytest.raises((TypeError, AttributeError)):
        stored["include_thoughts"] = False
    assert gemini.materialize_json() == {
        "thinking_config": {"include_thoughts": True, "thinking_level": "HIGH"}
    }

    # The carrier refuses values it cannot store immutably.
    with pytest.raises(ValueError):
        ReasoningConstruction(
            route="r", requested="x", disposition="emitted",
            json_delta=(("obj", object()),),
            emitted_path="obj", emitted_value="x",
        )


def test_amendment_b2_evidence_path_verification_is_exact():
    from lingtai.kernel.llm.policy import ReasoningConstruction

    # A false prefix on the recorded path must be rejected, not accepted by
    # prefix guessing (parent probe: totally.wrong.reasoning.effort).
    with pytest.raises(ValueError):
        ReasoningConstruction(
            route="r", requested="high", disposition="emitted",
            json_delta=(("reasoning", {"effort": "high"}),),
            emitted_path="totally.wrong.reasoning.effort", emitted_value="high",
        )

    # Legitimate nesting passes only through an explicit owner-declared root.
    nested = ReasoningConstruction(
        route="r", requested="x", disposition="emitted",
        json_delta=(("thinking_level", "high"),),
        merge_root="generation_config",
        emitted_path="generation_config.thinking_level", emitted_value="high",
    )
    assert nested.emission().emitted == ("generation_config.thinking_level", "high")

    with pytest.raises(ValueError):
        ReasoningConstruction(
            route="r", requested="x", disposition="emitted",
            json_delta=(("thinking_level", "high"),),
            merge_root="generation_config",
            emitted_path="wrong_root.thinking_level", emitted_value="high",
        )

    # Without a declared root, only the exact delta-rooted path resolves.
    with pytest.raises(ValueError):
        ReasoningConstruction(
            route="r", requested="x", disposition="emitted",
            json_delta=(("thinking_level", "high"),),
            emitted_path="generation_config.thinking_level", emitted_value="high",
        )


def test_amendment_b2_route_description_has_endpoint_key_and_query():
    routes = _routes_module()
    assert hasattr(routes, "find_reasoning_routes"), (
        "no callable identity-dimension lookup exists over the catalogue"
    )
    catalogue = {route.route_id: route for route in routes.REASONING_ROUTES}

    for route in catalogue.values():
        assert getattr(route, "endpoint", None), (
            f"{route.route_id} lacks an explicit endpoint identity"
        )

    # Official and custom OpenAI Responses must be mechanically distinct
    # records rather than one merged record with a drifting wire_route.
    official = routes.find_reasoning_routes(
        integration="main", provider="openai", wire="responses", endpoint="official"
    )
    assert [route.wire_route for route in official] == ["openai/responses"]
    custom = routes.find_reasoning_routes(
        integration="main", provider="custom", api_compat="openai", wire="responses"
    )
    assert len(custom) == 1
    custom_record = custom[0]
    assert custom_record.endpoint == "custom-base-url"
    assert custom_record.wire_route == "openai-compat:{host}/responses"

    # Mechanical accuracy: the described wire_route template matches the
    # actual construction route id for a real custom endpoint.
    import lingtai.llm.openai.adapter as oa

    adapter = oa.OpenAIAdapter(
        api_key="fake", base_url="https://kimi.example/v1", wire_api="responses"
    )
    expected = custom_record.wire_route.replace(
        "{host}", oa._base_url_namespace("https://kimi.example/v1")
    )
    assert adapter._responses_route_id() == expected
    session = adapter.create_chat("m", "s", thinking="high")
    assert session.reasoning_emission.route == expected

    # Model-gated identity is queryable, and the kernel predicate agrees with
    # the Gemini adapter's actual model gate.
    from lingtai.llm.gemini.adapter import _supports_thinking

    for model in (
        "gemini-3-pro", "gemini-3-flash-preview", "gemini-2.5-pro",
        "models/gemini-3-pro", "weird-model",
    ):
        matched = routes.find_reasoning_routes(
            integration="main", provider="gemini", wire="chat", model=model
        )
        assert len(matched) == 1, model
        expected_id = (
            "main:gemini:chat:3plus" if _supports_thinking(model)
            else "main:gemini:chat:pre3"
        )
        assert matched[0].route_id == expected_id, model

    # CLI phase is queryable.
    first = routes.find_reasoning_routes(
        integration="main", provider="claude-code", phase="first"
    )
    assert [route.route_id for route in first] == ["main:claude-code:cli:first"]

    # The manifest admission projection is preserved exactly.
    from lingtai.kernel import config as kernel_config

    assert kernel_config.llm_supports_thinking is routes.llm_supports_thinking
    assert kernel_config.llm_supports_thinking(
        {"provider": "custom", "api_compat": "openai", "wire_api": "responses"}
    ) is True
    assert kernel_config.llm_supports_thinking({"provider": "openai"}) is False
    admission_keys = {
        key for route in routes.REASONING_ROUTES for key in route.admission_keys
    }
    assert admission_keys == {
        ("codex", None, None),
        ("codex-pool", None, None),
        ("codex_pool", None, None),
        ("custom", "openai", "responses"),
    }


def test_amendment_b3_cli_llm_call_phase_matches_logged_command():
    from unittest.mock import patch as mock_patch

    from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter(model="sonnet")
    session = adapter.create_chat("sonnet", "system", thinking="high")

    events = []
    service = MagicMock()
    service.model = "sonnet"
    service.create_session.return_value = session
    manager = SessionManager(
        llm_service=service,
        config=AgentConfig(thinking="high"),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "system",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event, **fields: events.append((event, fields)),
    )

    captured = []

    def fake_run(cmd, **kwargs):
        captured.append((list(cmd), kwargs))
        return SimpleNamespace(stdout=_claude_result_json(), stderr="", returncode=0)

    with mock_patch(
        "lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run
    ):
        manager.send("first message")
        manager.send("second message")

    calls = [fields for event, fields in events if event == "llm_call"]
    assert len(calls) == 2
    # The event written before dispatch must describe the command about to
    # be constructed, phase included.
    assert calls[0]["reasoning"]["phase"] == "first"
    assert "--resume" not in captured[0][0]
    assert calls[1]["reasoning"]["phase"] == "resume"
    assert "--resume" in captured[1][0]
    assert all("--effort" not in cmd for cmd, _ in captured)


def test_amendment_b3_cli_sessions_prearm_and_rearm_next_phase(tmp_path):
    from unittest.mock import patch as mock_patch

    from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter
    from lingtai.llm.kimi_code.adapter import KimiCodeAdapter

    claude = ClaudeCodeAdapter(model="sonnet")
    claude_session = claude.create_chat("sonnet", "system", thinking="high")
    assert claude_session.reasoning_emission.phase == "first"

    def claude_fake_run(cmd, **kwargs):
        return SimpleNamespace(stdout=_claude_result_json(), stderr="", returncode=0)

    with mock_patch(
        "lingtai.llm.claude_code.adapter.subprocess.run", side_effect=claude_fake_run
    ):
        claude_session.send("hello")
    # After a successful send that retained the remote session id, the next
    # command's result is already armed as resume.
    assert claude_session._remote_session_id
    assert claude_session.reasoning_emission.phase == "resume"
    # Reset transitions re-arm first.
    claude_session._reset_remote_session()
    assert claude_session.reasoning_emission.phase == "first"

    kimi = KimiCodeAdapter(model="kimi-for-coding", cwd=tmp_path / "kimi")
    kimi_session = kimi.create_chat("kimi-for-coding", "system", thinking="high")
    assert kimi_session.reasoning_emission.phase == "first"

    def kimi_fake_run(cmd, **kwargs):
        return SimpleNamespace(stdout=_kimi_result_json(), stderr="", returncode=0)

    with mock_patch(
        "lingtai.llm.kimi_code.adapter.subprocess.run", side_effect=kimi_fake_run
    ):
        kimi_session.send("hello")
    assert kimi_session._kimi_session_id
    assert kimi_session.reasoning_emission.phase == "resume"
    kimi_session._reset_remote_session()
    assert kimi_session.reasoning_emission.phase == "first"


def test_amendment_b4_native_daemon_handoff_consumes_route_result(monkeypatch):
    import lingtai.tools.daemon as daemon_module

    from lingtai.kernel.llm.policy import ReasoningConstruction

    assert hasattr(daemon_module, "_native_daemon_reasoning_construction"), (
        "native daemon has no route-local construction result owner"
    )
    construction = daemon_module._native_daemon_reasoning_construction()
    assert isinstance(construction, ReasoningConstruction)
    assert construction.route == "native-daemon/session"
    assert construction.disposition == "emitted"
    assert construction.materialize_json() == {"thinking": "default"}
    assert construction.emission().emitted == ("create_session.thinking", "default")
    assert daemon_module._native_daemon_thinking() == "default"

    # The actual handoff value is derived from the one result, not from a
    # parallel constant.
    sentinel = ReasoningConstruction(
        route="native-daemon/session", requested="probe",
        disposition="emitted", json_delta=(("thinking", "probe-value"),),
        merge_root="create_session",
        emitted_path="create_session.thinking", emitted_value="probe-value",
    )
    monkeypatch.setattr(
        daemon_module, "_native_daemon_reasoning_construction", lambda: sentinel
    )
    assert daemon_module._native_daemon_thinking() == "probe-value"


def test_amendment_b4_external_startup_consumes_route_result(monkeypatch):
    import lingtai.tools.daemon as daemon_module

    from lingtai.kernel.llm.policy import ReasoningConstruction

    assert hasattr(daemon_module, "_startup_reasoning_construction"), (
        "external-daemon startup has no route-local construction result owner"
    )
    emitted = daemon_module._startup_reasoning_construction(
        {"model": "opus", "effort": "high"}
    )
    assert emitted.disposition == "emitted"
    assert list(emitted.argv_delta) == ["--model", "opus", "--effort", "high"]
    assert emitted.emission().emitted == ("argv:--effort", "high")
    assert emitted.phase == "startup"

    plain = daemon_module._startup_reasoning_construction({"model": "opus"})
    assert plain.disposition == "omitted"
    assert list(plain.argv_delta) == ["--model", "opus"]

    empty = daemon_module._startup_reasoning_construction(None)
    assert empty.disposition == "omitted"
    assert empty.argv_delta == ()

    # The actual startup argv contribution is the result's argv delta.
    assert daemon_module._startup_backend_options_argv({"effort": "high"}) == [
        "--effort", "high",
    ]
    sentinel = ReasoningConstruction(
        route="external-daemon/startup", requested="default",
        disposition="omitted", argv_delta=("--probe",), phase="startup",
    )
    monkeypatch.setattr(
        daemon_module, "_startup_reasoning_construction", lambda options: sentinel
    )
    assert daemon_module._startup_backend_options_argv({"anything": True}) == ["--probe"]


def test_amendment_b4_external_ask_phase_route_results():
    import ast
    import inspect
    import textwrap

    import lingtai.tools.daemon as daemon_module

    assert hasattr(daemon_module, "_ask_reasoning_construction"), (
        "external-daemon ask phases have no route-local result owner"
    )
    claude_ask = daemon_module._ask_reasoning_construction("claude-code")
    assert claude_ask.disposition == "omitted"
    assert claude_ask.phase == "ask"
    assert claude_ask.argv_delta == ()

    kimi_ask = daemon_module._ask_reasoning_construction("kimicode")
    assert kimi_ask.disposition == "unsupported"
    assert kimi_ask.phase == "ask"

    # Tied to the actual fixed-argv builder, not catalogue prose.
    source = inspect.getsource(daemon_module.DaemonManager._handle_ask_cli)
    tree = ast.parse(textwrap.dedent(source))
    used = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_ask_reasoning_construction"
        for node in ast.walk(tree)
    )
    assert used, "_handle_ask_cli does not consume the ask-phase route result"


def test_amendment_b5_official_providers_with_custom_endpoints_are_described():
    """B5 — supported provider + custom-endpoint combinations are queryable.

    The registered factories forward ``base_url`` for the named official
    providers too (``_register.py::_openai`` / ``_anthropic``), and the
    adapters select the compat route solely from the base URL — so the
    catalogue's provider+endpoint+wire key must resolve these combinations
    to the same custom-endpoint records, without touching admission.
    """
    routes = _routes_module()

    # provider=openai, custom endpoint, Responses.
    openai_responses = routes.find_reasoning_routes(
        integration="main", provider="openai",
        endpoint="custom-base-url", wire="responses",
    )
    assert [route.route_id for route in openai_responses] == [
        "main:openai:responses:custom"
    ], "openai + custom-base-url Responses is a supported combination"
    assert openai_responses[0].wire_route == "openai-compat:{host}/responses"

    # provider=openai, custom endpoint, Chat Completions.
    openai_chat = routes.find_reasoning_routes(
        integration="main", provider="openai",
        endpoint="custom-base-url", wire="chat_completions",
    )
    assert [route.route_id for route in openai_chat] == [
        "main:custom-endpoint:chat"
    ], "openai + custom-base-url Chat is a supported combination"
    # The alias spellings stay covered by the same record.
    for alias in ("custom", "kimi", "grok", "qwen"):
        assert alias in openai_chat[0].providers

    # provider=anthropic, custom endpoint, Messages.
    anthropic_messages = routes.find_reasoning_routes(
        integration="main", provider="anthropic",
        endpoint="custom-base-url", wire="messages",
    )
    assert [route.route_id for route in anthropic_messages] == [
        "main:custom-anthropic:messages"
    ], "anthropic + custom-base-url Messages is a supported combination"

    # The actual adapters emit exactly the described custom route ids for
    # these combinations (production mapping was already correct).
    import lingtai.llm.openai.adapter as oa

    namespace = oa._base_url_namespace("https://compat.example/v1")
    responses_session = oa.OpenAIAdapter(
        api_key="fake", base_url="https://compat.example/v1", wire_api="responses"
    ).create_chat("m", "s", thinking="high")
    assert responses_session.reasoning_emission.route == (
        openai_responses[0].wire_route.replace("{host}", namespace)
    )
    chat_session = oa.OpenAIAdapter(
        api_key="fake", base_url="https://compat.example/v1",
        wire_api="chat_completions",
    ).create_chat("m", "s", thinking="high")
    assert chat_session.reasoning_emission.route == (
        openai_chat[0].wire_route.replace("{host}", namespace)
    )

    from lingtai.llm.anthropic.adapter import AnthropicAdapter

    messages_session = AnthropicAdapter(
        api_key="fake", base_url="https://compat.example"
    ).create_chat("model", "system", thinking="high")
    assert messages_session.reasoning_emission.route == (
        anthropic_messages[0].wire_route
    )

    # Official-endpoint queries are unchanged and disjoint.
    assert [
        route.route_id
        for route in routes.find_reasoning_routes(
            integration="main", provider="openai",
            endpoint="official", wire="responses",
        )
    ] == ["main:openai:responses:official"]
    assert [
        route.route_id
        for route in routes.find_reasoning_routes(
            integration="main", provider="anthropic",
            endpoint="official", wire="messages",
        )
    ] == ["main:anthropic:messages"]

    # Admission is untouched: explicit manifest thinking stays admitted only
    # for provider custom + OpenAI Responses (plus the Codex family).
    from lingtai.kernel import config as kernel_config

    assert kernel_config.llm_supports_thinking(
        {"provider": "openai", "api_compat": "openai", "wire_api": "responses"}
    ) is False
    assert kernel_config.llm_supports_thinking(
        {"provider": "anthropic", "api_compat": "anthropic"}
    ) is False
    assert kernel_config.llm_supports_thinking(
        {"provider": "custom", "api_compat": "openai", "wire_api": "responses"}
    ) is True
    admission_keys = {
        key for route in routes.REASONING_ROUTES for key in route.admission_keys
    }
    assert admission_keys == {
        ("codex", None, None),
        ("codex-pool", None, None),
        ("codex_pool", None, None),
        ("custom", "openai", "responses"),
    }


# ---------------------------------------------------------------------------
# Final-review blockers (pr1-final-review-blockers.md): B6 wrapper-provider
# custom endpoints, B7 typed startup evidence, B8 runtime-frozen evidence
# tuples, B9 live admission defect ledger, B10 complete ask-phase catalogue.
# All behavior/byte-preserving.
# ---------------------------------------------------------------------------


def test_final_review_b6_wrapper_provider_custom_endpoints_are_described():
    """B6 — named wrapper providers accept explicit base_url through the
    registered LLMService factories; both endpoint identities must resolve to
    exact records whose wire_route matches the actual construction route."""
    routes = _routes_module()

    cases = [
        # provider, provider_defaults, catalogue wire, actual route id, default record id
        ("openrouter", None, "chat_completions", "openrouter/chat", "main:openrouter:chat"),
        ("zhipu", None, "chat_completions", "zhipu/chat", "main:zhipu:chat"),
        ("glm", None, "chat_completions", "zhipu/chat", "main:zhipu:chat"),
        ("deepseek", None, "chat_completions", "deepseek/chat", "main:deepseek:chat"),
        (
            "deepseek", {"deepseek": {"wire_api": "responses"}},
            "responses", "deepseek/responses", "main:deepseek:responses",
        ),
        ("mimo", None, "responses", "mimo/responses", "main:mimo:responses"),
        (
            "mimo", {"mimo": {"wire_api": "chat_completions"}},
            "chat_completions", "mimo/chat", "main:mimo:chat",
        ),
        ("minimax", None, "messages", "minimax/messages", "main:minimax:messages"),
    ]
    for provider, defaults, wire, expected_wire_route, default_record_id in cases:
        for base_url, endpoint, record_id in (
            (None, "provider-fixed", default_record_id),
            (
                "https://override.example/v1", "custom-base-url",
                f"{default_record_id}:custom",
            ),
        ):
            kwargs = dict(provider=provider, model="test-model", api_key="fake")
            if defaults:
                kwargs["provider_defaults"] = defaults
            if base_url:
                kwargs["base_url"] = base_url
            service = ConcreteLLMService(**kwargs)
            session = service.create_session(
                system_prompt="system", thinking="high", tracked=False
            )
            # The adapter route id is provider-fixed regardless of endpoint.
            assert session.reasoning_emission.route == expected_wire_route, (
                provider, wire, base_url,
            )
            matched = routes.find_reasoning_routes(
                integration="main", provider=provider, endpoint=endpoint, wire=wire
            )
            assert [route.route_id for route in matched] == [record_id], (
                provider, wire, endpoint,
            )
            assert matched[0].wire_route == expected_wire_route

    # Route IDs stay unique; endpoint-qualified queries stay disjoint.
    all_ids = [route.route_id for route in routes.REASONING_ROUTES]
    assert len(all_ids) == len(set(all_ids))


_B7_CASES = [
    # options, argv, disposition, emitted pair, coupled, requested
    ({"effort": "high"}, ["--effort", "high"], "emitted",
     ("argv:--effort", "high"), (), "high"),
    ({"effort": "--high"}, ["--effort", "--high"], "emitted",
     ("argv:--effort", "--high"), (), "--high"),
    ({"effort": 7}, ["--effort", "7"], "emitted",
     ("argv:--effort", "7"), (), "7"),
    ({"effort": 2.5}, ["--effort", "2.5"], "emitted",
     ("argv:--effort", "2.5"), (), "2.5"),
    ({"effort": ["high", "low"]},
     ["--effort", "high", "--effort", "low"], "emitted",
     ("argv:--effort", "high"), (("argv[3]", "low"),), "['high', 'low']"),
    ({"effort": ["high", "--weird"]},
     ["--effort", "high", "--effort", "--weird"], "emitted",
     ("argv:--effort", "high"), (("argv[3]", "--weird"),), "['high', '--weird']"),
    ({"effort": []}, [], "omitted", None, (), "[]"),
    ({"effort": True}, ["--effort"], "emitted",
     ("argv[0]", "--effort"), (), "True"),
    ({"effort": False}, [], "omitted", None, (), "False"),
    ({"effort": None}, [], "omitted", None, (), "None"),
    ({}, [], "omitted", None, (), "default"),
    (None, [], "omitted", None, (), "default"),
    ({"model": "opus", "effort": "high"},
     ["--model", "opus", "--effort", "high"], "emitted",
     ("argv:--effort", "high"), (), "high"),
    ({"model": "opus"}, ["--model", "opus"], "omitted", None, (), "default"),
]


@pytest.mark.parametrize(
    ("options", "argv", "disposition", "emitted", "coupled", "requested"),
    _B7_CASES,
    ids=[repr(case[0]) for case in _B7_CASES],
)
def test_final_review_b7_startup_evidence_covers_typed_domain(
    options, argv, disposition, emitted, coupled, requested
):
    """B7 — startup evidence derives from the validated typed option and
    represents every emitted occurrence exactly; argv bytes are untouched."""
    import lingtai.tools.daemon as daemon_module

    construction = daemon_module._startup_reasoning_construction(options)
    # The actual startup argv contribution is byte-for-byte the converter's.
    assert list(construction.argv_delta) == argv
    assert construction.argv_delta == tuple(
        daemon_module._backend_options_to_argv(options)
    )
    assert construction.disposition == disposition
    assert construction.requested == requested
    if emitted is None:
        assert construction.emitted_path is None
    else:
        assert (construction.emitted_path, construction.emitted_value) == emitted
    assert construction.coupled == coupled
    # The public wrapper returns the same result's argv delta.
    assert daemon_module._startup_backend_options_argv(options) == argv


def test_final_review_b7_startup_validation_order_and_errors_are_unchanged():
    import lingtai.tools.daemon as daemon_module

    with pytest.raises(ValueError) as excinfo:
        daemon_module._startup_reasoning_construction({"effort": {"nested": 1}})
    assert str(excinfo.value) == (
        "backend_options['effort'] has unsupported value type dict; "
        "expected bool/str/int/float/list of scalars/null"
    )
    with pytest.raises(ValueError):
        daemon_module._startup_reasoning_construction({"-bad": "x"})


def test_final_review_b8_evidence_tuples_are_runtime_frozen():
    """B8 — caller-owned lists must never change stored evidence records."""
    from lingtai.kernel.llm.policy import ReasoningConstruction

    inner = ["max_tokens", "32768"]
    outer = [("thinking.type", "enabled"), inner]
    construction = ReasoningConstruction(
        route="anthropic/messages", requested="high", disposition="emitted",
        json_delta=(
            ("thinking", {"type": "enabled", "budget_tokens": 16384}),
            ("max_tokens", 32768),
        ),
        emitted_path="thinking.budget_tokens", emitted_value="16384",
        coupled=outer,
    )
    inner[1] = "1"
    outer.append(("evil", "pair"))
    assert construction.coupled == (
        ("thinking.type", "enabled"), ("max_tokens", "32768"),
    )
    assert construction.emission().as_dict()["coupled"] == [
        {"path": "thinking.type", "value": "enabled"},
        {"path": "max_tokens", "value": "32768"},
    ]

    emitted_list = ["reasoning_effort", "high"]
    coupled_list = [["extra_body.reasoning.include", "False"]]
    emission = ReasoningEmission(
        requested="high", emitted=emitted_list, coupled=coupled_list
    )
    emitted_list[1] = "low"
    coupled_list[0][1] = "True"
    assert emission.emitted == ("reasoning_effort", "high")
    assert emission.as_dict() == {
        "requested": "high",
        "emitted": {"path": "reasoning_effort", "value": "high"},
        "coupled": [{"path": "extra_body.reasoning.include", "value": "False"}],
    }

    # Malformed pair shapes/types are rejected clearly.
    for bad_emitted in (("only-one",), ("a", "b", "c"), ("path", 123), "ab"):
        with pytest.raises(ValueError):
            ReasoningEmission(requested="x", emitted=bad_emitted)
    for bad_coupled in ((("only-one",),), (("path", 123),), ("ab",)):
        with pytest.raises(ValueError):
            ReasoningEmission(requested="x", omitted=True, coupled=bad_coupled)
        with pytest.raises(ValueError):
            ReasoningConstruction(
                route="r", requested="x", disposition="omitted",
                coupled=bad_coupled,
            )


def test_final_review_b9_admission_defects_live_on_production_ledger():
    """B9 — G-1/G-2 are owned by the callable production admission
    description, with the A1 deletion condition, not by tests alone."""
    routes = _routes_module()

    assert getattr(routes, "ADMISSION_DEFECTS", None) == ("G-1", "G-2"), (
        "no immutable admission-defect ledger exists on the route description"
    )
    union = {defect for route in routes.REASONING_ROUTES for defect in route.defects}
    assert {"G-1", "G-2"} <= union

    manifest_records = [
        route for route in routes.REASONING_ROUTES if route.admission == "manifest"
    ]
    assert manifest_records, "the admission-owning records must exist"
    for record in manifest_records:
        assert "G-1" in record.defects and "G-2" in record.defects, record.route_id

    # The gate, vocabulary, admitted keys, and caller errors stay byte-identical.
    from lingtai.kernel import config as kernel_config

    assert kernel_config.THINKING_LEVELS == (
        "none", "minimal", "low", "medium", "high", "xhigh",
    )
    assert kernel_config.llm_supports_thinking is routes.llm_supports_thinking
    assert kernel_config.llm_supports_thinking({"provider": "codex"}) is True
    assert kernel_config.llm_supports_thinking({"provider": "anthropic"}) is False
    admission_keys = {
        key for route in routes.REASONING_ROUTES for key in route.admission_keys
    }
    assert admission_keys == {
        ("codex", None, None),
        ("codex-pool", None, None),
        ("codex_pool", None, None),
        ("custom", "openai", "responses"),
    }


def test_final_review_b10_every_cli_backend_ask_phase_is_described():
    """B10 — the complete CLI backend-spec inventory has exact ask-phase
    descriptions matching the actual route-local constructions."""
    import lingtai.tools.daemon as daemon_module

    routes = _routes_module()
    specs = daemon_module._BACKEND_SPECS
    cli_backends = [backend for backend, spec in specs.items() if spec.is_cli]
    assert len(cli_backends) >= 11  # full current inventory, incl. cursor

    for backend in cli_backends:
        spec = specs[backend]
        construction = daemon_module._ask_reasoning_construction(backend)
        supported = spec.ask_handler_attr is not None
        assert construction.disposition == (
            "omitted" if supported else "unsupported"
        ), backend
        assert construction.route == f"external-daemon/{backend}/ask", backend
        assert construction.phase == "ask"
        assert construction.argv_delta == ()

        matched = routes.find_reasoning_routes(
            integration="external-daemon", provider=backend, phase="ask"
        )
        assert len(matched) == 1, f"{backend} lacks an exact ask-phase record"
        record = matched[0]
        assert record.wire_route == construction.route, backend
        assert record.admission == ("fixed-argv" if supported else "unsupported"), backend
        fact_dispositions = {fact.disposition for fact in record.facts}
        assert fact_dispositions == (
            {"omitted"} if supported else {"error"}
        ), backend

    # The native lingtai backend's ask path belongs to the native-daemon
    # integration, not the external CLI ask phase.
    assert specs["lingtai"].is_cli is False
    assert not routes.find_reasoning_routes(
        integration="external-daemon", provider="lingtai", phase="ask"
    )
    # Historical defect IDs stay on their backends.
    claude_record = routes.find_reasoning_routes(
        integration="external-daemon", provider="claude-code", phase="ask"
    )[0]
    assert "CL-2" in claude_record.defects
    kimi_record = routes.find_reasoning_routes(
        integration="external-daemon", provider="kimicode", phase="ask"
    )[0]
    assert "K-4" in kimi_record.defects


# ---------------------------------------------------------------------------
# Round-2 blockers (pr1-final-review-round2-blockers.md): B11 every real
# supported ask builder consumes one canonical-backend result on both
# dispatch paths; B12 callable exact startup description from the same
# construction result. All behavior/byte-preserving.
# ---------------------------------------------------------------------------

import threading as _threading
from pathlib import Path


_ASK_SESSION_ID = "session-123"
_ASK_SUPPORTED_ARGV = {
    "claude-p": (
        "claude", "--resume", _ASK_SESSION_ID, "--print",
        "--dangerously-skip-permissions", "--output-format", "stream-json",
        "--verbose", "hello",
    ),
    "claude-code": (
        "claude", "--resume", _ASK_SESSION_ID, "--print",
        "--dangerously-skip-permissions", "--output-format", "stream-json",
        "--verbose", "hello",
    ),
    "codex": (
        "codex", "exec", "resume", _ASK_SESSION_ID, "--json",
        "--dangerously-bypass-approvals-and-sandbox", "hello",
    ),
    "opencode": (
        "opencode", "run", "--session", _ASK_SESSION_ID, "--format", "json",
        "hello",
    ),
    "mimocode": (
        "mimo", "run", "--session", _ASK_SESSION_ID, "--format", "json",
        "hello",
    ),
    "oh-my-pi": (
        "omp", "--mode", "json", "--approval-mode", "yolo", "--session",
        _ASK_SESSION_ID, "hello",
    ),
    "cursor": (
        "agent", "-p", "--force", "--resume", _ASK_SESSION_ID,
        "--output-format", "stream-json", "hello",
    ),
}
# Interactive claude backends build their command inside the bridge worker,
# faked in the dispatcher harness and proven at the bridge constructor.
_ASK_INTERACTIVE_BACKENDS = ("claude", "claude-interactive")


class _FakeAskRunDir:
    def __init__(self, path):
        self.path = path
        self.handle = "em-ask"
        self.run_id = "run-ask"
        self._state = {
            "task": "initial task",
            "state": "done",
            "model": "test-model",
            "claude_session_id": _ASK_SESSION_ID,
            "codex_session_id": _ASK_SESSION_ID,
            "opencode_session_id": _ASK_SESSION_ID,
            "mimocode_session_id": _ASK_SESSION_ID,
            "oh_my_pi_session_id": _ASK_SESSION_ID,
            "cursor_session_id": _ASK_SESSION_ID,
        }
        self.followups = []

    def read_state_from_disk(self, path):
        return dict(self._state)

    def record_cli_output(self, *args, **kwargs):
        pass

    def record_followup(self, generation, **kwargs):
        self.followups.append((generation, kwargs))

    def append_event(self, *args, **kwargs):
        pass


class _FakeAskProcessPort:
    def __init__(self):
        self.commands = []

    def spawn(self, command, group_id=None):
        self.commands.append(command)
        return SimpleNamespace(pid=4242)


class _SyncAskPool:
    def submit(self, fn, *args, **kwargs):
        import concurrent.futures

        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # pragma: no cover - surfaced by tests
            future.set_exception(exc)
        return future


def _install_fake_ask_workers(monkeypatch, calls):
    """Fake the post-spawn stream workers on the production manager class so
    both the live dispatcher and the execution host resolve them."""
    from lingtai.tools.daemon import DaemonManager

    def worker(name):
        def _fake(self, *args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return {"status": "sent", "id": "em-ask", "output": "ok"}

        return _fake

    for name in (
        "_run_ask_claude_code_stream",
        "_run_ask_claude_interactive_stream",
        "_run_ask_codex_stream",
        "_run_ask_opencode_stream",
        "_run_ask_cursor_stream",
    ):
        monkeypatch.setattr(DaemonManager, name, worker(name))
    monkeypatch.setattr(DaemonManager, "_on_ask_done", lambda self, *a, **k: None)


def _sentinel_ask_mapper(monkeypatch, calls):
    """Sentinel the ask route mapper: record the backend identity and return
    a result whose argv/env deltas must reach the real spawned command."""
    import lingtai.tools.daemon as daemon_module

    from lingtai.kernel.llm.policy import ReasoningConstruction

    real = daemon_module._ask_reasoning_construction

    def sentinel(backend):
        calls.append(backend)
        result = real(backend)
        if result.disposition == "unsupported":
            return result
        return ReasoningConstruction(
            route=result.route,
            requested=result.requested,
            disposition="omitted",
            argv_delta=("--reasoning-probe",),
            env_delta=(("REASONING_PROBE", "1"),),
            source=result.source,
            phase=result.phase,
        )

    monkeypatch.setattr(daemon_module, "_ask_reasoning_construction", sentinel)


def _legacy_ask_manager(port, entry):
    from lingtai.tools.daemon import DaemonManager

    manager = DaemonManager.__new__(DaemonManager)
    manager._emanations = {"em-ask": entry}
    manager._process_port = port
    manager._ask_pool = _SyncAskPool()
    manager._agent = SimpleNamespace(_working_dir=Path("/tmp/ask-parent"))
    manager._log = lambda *args, **kwargs: None
    manager._timeout = 5.0
    manager._interactive_terminal_port = object()
    return manager


def _legacy_ask_entry(run_dir, backend):
    return {
        "backend": backend,
        "run_dir": run_dir,
        "followup_lock": _threading.Lock(),
        "ask_in_flight": False,
        "cancel_event": _threading.Event(),
    }


def test_round2_b11_legacy_dispatch_consumes_backend_result(tmp_path, monkeypatch):
    """B11 — the live dispatcher hands the canonical backend to every
    supported ask builder, which consumes exactly one route result whose
    argv/env deltas reach the real spawned command. A nonempty env delta
    OVERLAYS the inherited environment (``DaemonProcessCommand.environment``
    is a complete child environment) rather than replacing it."""
    import lingtai.tools.daemon as daemon_module

    worker_calls: dict = {}
    _install_fake_ask_workers(monkeypatch, worker_calls)
    monkeypatch.setenv("LINGTAI_INHERITED_PROBE", "inherited-1")

    supported = list(_ASK_SUPPORTED_ARGV) + list(_ASK_INTERACTIVE_BACKENDS)
    for backend in supported:
        mapper_calls: list = []
        _sentinel_ask_mapper(monkeypatch, mapper_calls)
        port = _FakeAskProcessPort()
        run_dir = _FakeAskRunDir(tmp_path)
        manager = _legacy_ask_manager(port, _legacy_ask_entry(run_dir, backend))
        result = manager._handle_ask("em-ask", "hello")
        assert result.get("status") == "sent", (backend, result)
        assert mapper_calls == [backend], (
            f"{backend}: the ask builder must consume exactly one result for "
            f"its canonical identity (got {mapper_calls})"
        )
        if backend in _ASK_INTERACTIVE_BACKENDS:
            # The interactive builder threads the one result into its real
            # command constructor (the bridge worker).
            (args, kwargs) = worker_calls["_run_ask_claude_interactive_stream"][-1]
            threaded = list(args) + list(kwargs.values())
            assert any(
                getattr(value, "argv_delta", None) == ("--reasoning-probe",)
                for value in threaded
            ), f"{backend}: bridge worker did not receive the one route result"
        else:
            command = port.commands[-1]
            assert "--reasoning-probe" in command.argv, (backend, command.argv)
            # The delta is spliced before the trailing message.
            assert command.argv[-1] == "hello"
            environment = dict(command.environment or ())
            assert environment.get("REASONING_PROBE") == "1", (backend, command)
            # The delta must not drop the inherited child environment.
            assert environment.get("LINGTAI_INHERITED_PROBE") == "inherited-1", (
                f"{backend}: nonempty env delta replaced the inherited "
                "environment instead of overlaying it"
            )


def test_round2_b11_execution_host_resume_consumes_backend_result(
    tmp_path, monkeypatch
):
    """B11 — the production detached ``run_resume`` path reaches the same
    single-result builders with the canonical backend identity."""
    from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost

    worker_calls: dict = {}
    _install_fake_ask_workers(monkeypatch, worker_calls)

    def build_host(backend, port, run_dir):
        manifest = {
            "backend": backend,
            "parent_working_dir": str(tmp_path),
            "max_turns": 1,
            "timeout_s": 5,
            "language": "en",
            "llm": {"model": "test-model"},
            "tools": [],
        }
        return DetachedDaemonExecutionHost(
            run_dir,
            manifest,
            _threading.Event(),
            _threading.Event(),
            capsule={"message": "hello"},
            process_port=port,
            interactive_terminal_port=object(),
        )

    monkeypatch.setenv("LINGTAI_INHERITED_PROBE", "inherited-1")
    supported = list(_ASK_SUPPORTED_ARGV) + list(_ASK_INTERACTIVE_BACKENDS)
    for backend in supported:
        mapper_calls: list = []
        _sentinel_ask_mapper(monkeypatch, mapper_calls)
        port = _FakeAskProcessPort()
        run_dir = _FakeAskRunDir(tmp_path)
        host = build_host(backend, port, run_dir)
        response = host.run_resume("gen-1")
        assert response.get("status") == "sent", (backend, response)
        assert run_dir.followups and run_dir.followups[-1][1]["status"] == "done"
        assert mapper_calls == [backend], (backend, mapper_calls)
        if backend in _ASK_INTERACTIVE_BACKENDS:
            (args, kwargs) = worker_calls["_run_ask_claude_interactive_stream"][-1]
            threaded = list(args) + list(kwargs.values())
            assert any(
                getattr(value, "argv_delta", None) == ("--reasoning-probe",)
                for value in threaded
            ), backend
        else:
            command = port.commands[-1]
            assert "--reasoning-probe" in command.argv, (backend, command.argv)
            environment = dict(command.environment or ())
            assert environment.get("REASONING_PROBE") == "1", backend
            # Nonempty env delta overlays the inherited environment.
            assert environment.get("LINGTAI_INHERITED_PROBE") == "inherited-1", backend

    # Unsupported backends stay unsupported with unchanged refusal.
    for backend in ("qwen-code", "kimicode"):
        port = _FakeAskProcessPort()
        run_dir = _FakeAskRunDir(tmp_path)
        host = build_host(backend, port, run_dir)
        with pytest.raises(ValueError) as excinfo:
            host.run_resume("gen-1")
        assert str(excinfo.value) == (
            f"detached resume is unsupported for backend {backend!r}"
        )
        assert port.commands == []


def test_round2_b11_ask_bytes_and_unsupported_messages_unchanged(
    tmp_path, monkeypatch
):
    """B11 — with the real (empty-delta) results, every ask command and its
    environment stay byte-identical, and unsupported dispatch is unchanged."""
    import lingtai.tools.daemon as daemon_module

    worker_calls: dict = {}
    _install_fake_ask_workers(monkeypatch, worker_calls)

    for backend, expected_argv in _ASK_SUPPORTED_ARGV.items():
        port = _FakeAskProcessPort()
        run_dir = _FakeAskRunDir(tmp_path)
        manager = _legacy_ask_manager(port, _legacy_ask_entry(run_dir, backend))
        result = manager._handle_ask("em-ask", "hello")
        assert result.get("status") == "sent", (backend, result)
        command = port.commands[-1]
        assert command.argv == expected_argv, backend
        if backend in ("claude-p", "claude-code"):
            assert dict(command.environment or ()) == daemon_module._claude_code_env()
        else:
            assert command.environment is None, backend

    for backend in ("qwen-code", "kimicode"):
        port = _FakeAskProcessPort()
        run_dir = _FakeAskRunDir(tmp_path)
        manager = _legacy_ask_manager(port, _legacy_ask_entry(run_dir, backend))
        result = manager._handle_ask("em-ask", "hello")
        spec = daemon_module._BACKEND_SPECS[backend]
        assert result == {
            "status": "error", "id": "em-ask",
            "message": spec.ask_unsupported_msg,
        }
        assert port.commands == []


def test_round2_b11_claude_interactive_bridge_consumes_result(tmp_path):
    """B11 — the interactive bridge's real command/env constructors consume
    the threaded route result's deltas."""
    from lingtai.kernel.llm.policy import ReasoningConstruction
    from lingtai.tools.daemon.claude_interactive import ClaudeInteractiveBridge

    sentinel = ReasoningConstruction(
        route="external-daemon/claude-interactive/ask",
        requested="default",
        disposition="omitted",
        argv_delta=("--reasoning-probe",),
        env_delta=(("REASONING_PROBE", "1"),),
        phase="ask",
    )
    bridge = ClaudeInteractiveBridge.__new__(ClaudeInteractiveBridge)
    bridge.resume_session_id = _ASK_SESSION_ID
    bridge.backend_argv = ["--model", "opus"]
    bridge.system_prompt_path = tmp_path / "sp.md"
    bridge.reasoning = sentinel
    cmd = bridge._command("settings.json")
    assert "--reasoning-probe" in cmd
    # Non-reasoning tokens keep their positions.
    assert cmd[:3] == ["claude", "--resume", _ASK_SESSION_ID]
    assert "--model" in cmd and "opus" in cmd
    env = bridge._reasoning_env({"BASE": "1"})
    assert env["REASONING_PROBE"] == "1" and env["BASE"] == "1"

    # Without a result the constructors are byte-identical to history.
    bridge.reasoning = None
    plain = bridge._command("settings.json")
    assert plain == [
        "claude", "--resume", _ASK_SESSION_ID, "--settings", "settings.json",
        "--append-system-prompt-file", str(tmp_path / "sp.md"),
        "--model", "opus",
    ]
    assert bridge._reasoning_env({"BASE": "1"}) == {"BASE": "1"}


def test_round2_b12_startup_description_is_callable_and_exact():
    """B12 — the startup route description is callable over the typed input
    and mechanically equal to the single construction owner's result."""
    import lingtai.tools.daemon as daemon_module

    routes = _routes_module()
    assert hasattr(routes, "describe_reasoning_construction"), (
        "no immutable projection from a construction result to a route fact"
    )
    assert hasattr(daemon_module, "describe_startup_reasoning"), (
        "no callable exact startup description exists"
    )

    for options, _argv, disposition, emitted, coupled, requested in _B7_CASES:
        fact = daemon_module.describe_startup_reasoning(options)
        construction = daemon_module._startup_reasoning_construction(options)
        assert isinstance(fact, routes.RouteFact)
        assert fact.disposition == construction.disposition == disposition
        assert fact.input == construction.requested == requested
        assert fact.path == construction.emitted_path
        assert fact.value == construction.emitted_value
        if emitted is not None:
            assert (fact.path, fact.value) == emitted
        assert fact.coupled == construction.coupled == coupled

    # The ambiguous-adjacent case from the Codex probe.
    probe = daemon_module.describe_startup_reasoning(
        {"prefix": "--effort", "effort": ["--high", "low"], "tail": True}
    )
    assert probe.disposition == "emitted"
    assert (probe.path, probe.value) == ("argv[3]", "--high")
    assert probe.coupled == (("argv[5]", "low"),)

    # The static catalogue record no longer claims an inexact fixed
    # path/value; identity/baseline/defects stay callable and stable.
    record = routes.reasoning_route("external-daemon:startup:argv")
    assert record.wire_route == "external-daemon/startup"
    assert record.baseline
    assert "CL-2" in record.defects and "K-4" in record.defects
    assert all(fact.path is None for fact in record.facts)
    assert all(fact.value is None for fact in record.facts)
    dispositions = {fact.disposition for fact in record.facts}
    assert dispositions == {"omitted", "emitted"}


@pytest.mark.parametrize("rebuild", [False, True], ids=["ensure", "rebuild"])
def test_session_manager_policy_capture_preserves_argument_evaluation_order(rebuild):
    """#1197 capture-order repair: both construction seams must see builder mutations."""
    service = MagicMock()
    service.model = "test-model"
    service.create_session.return_value = MagicMock()
    config = AgentConfig(thinking="medium")

    def build_system_prompt():
        config.thinking = "xhigh"
        return "system"

    manager = SessionManager(
        llm_service=service,
        config=config,
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=build_system_prompt,
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )

    if rebuild:
        manager._rebuild_session(MagicMock())
    else:
        manager.ensure_session()

    captured = service.create_session.call_args.kwargs["thinking"]
    assert isinstance(captured, ReasoningPolicy)
    assert captured.effort == "xhigh"
