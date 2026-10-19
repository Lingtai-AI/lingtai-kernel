"""Thin offline regressions for the opt-in pre-execution decision phase."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.adapters.tool_decision import ActivePresetToolDecisionPhase, DECISION_INSTRUCTIONS
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm.base import LLMResponse, ToolCall
from lingtai.kernel.llm.interface import ToolResultBlock
from lingtai.kernel.loop_guard import LoopGuard
from lingtai.kernel.tool_call_guard import GuardDecision, ToolCallGuard
from lingtai.kernel.tool_decision import (
    ToolDecisionResult, review_tool_response,
)
from lingtai.kernel.tool_executor import ToolExecutor


class RecordingPhase:
    def __init__(self):
        self.calls = []

    def review(self, call):
        self.calls.append(deepcopy(call))
        allowed = call.args.get("allow", True)
        # No confidence cutoff, and dangerous=true does not override pass=true.
        call.args["nested"]["value"] = "mutated by injected reviewer"
        return ToolDecisionResult(True, allowed, 0)


def executor(checks, handled, events, *, required=True):
    def dispatch(call):
        handled.append(deepcopy(call))
        return {"status": "ok"}

    return ToolExecutor(
        dispatch, lambda name, result, **kw: ToolResultBlock(
            id=kw.get("tool_call_id"), name=name, content=result,
        ), LoopGuard(max_total_calls=50),
        tool_call_guard=ToolCallGuard(checks), tool_decision_required=required,
        logger_fn=lambda kind, **fields: events.append((kind, fields)),
    )


def reviewed(calls, phase, events=None, cancelled=None):
    response = LLMResponse(tool_calls=calls, api_call_id="main-round")
    return review_tool_response(response, lambda: phase,
                                (lambda kind, **fields: events.append((kind, fields)))
                                if events is not None else None, cancelled)


def execute_response(ex, response):
    return ex.execute(response.tool_calls, api_call_id=response.api_call_id,
                      tool_decisions=response.tool_decisions, collected_errors=[])


def test_pure_text_does_not_construct_phase_or_session():
    factory = MagicMock()
    response = LLMResponse(text="ordinary answer")
    assert review_tool_response(response, factory) is response
    factory.assert_not_called()
    assert response.tool_decisions is None


@pytest.mark.parametrize("parallel", [False, True])
def test_injected_allow_deny_precede_recording_handlers_and_preserve_pairs(parallel):
    phase, handled, events, decisions = RecordingPhase(), [], [], []
    for allow in (True, False):
        calls = [ToolCall("custom", {"allow": allow, "nested": {"value": "original"}}, id="one")]
        if parallel:
            calls.append(ToolCall("mcp__record", deepcopy(calls[0].args), id="two"))
        original = deepcopy(calls)
        before = len(handled)
        response = reviewed(calls, phase, decisions)
        # The whole response is reviewed before the executor is entered.
        assert len(phase.calls) == len(decisions)
        assert len(handled) == before
        results, _, _ = execute_response(executor([], handled, events), response)
        assert calls == original
        assert [result.id for result in results] == [call.id for call in calls]
        assert len(handled) - before == (len(calls) if allow else 0)
        if allow:
            assert all(call.args["nested"]["value"] == "original" for call in handled)
        else:
            assert all(r.content["error_type"] == "ToolCallGuardDenied" for r in results)
            assert all(r.content["guard_decision"]["metadata"]["decision"]["pass"] is False
                       for r in results)
    assert len(phase.calls) == (4 if parallel else 2)
    assert all(event[1]["latency_ms"] >= 0 for event in decisions)
    assert all("nested" not in json.dumps(event[1]) for event in decisions)


def make_service(response):
    service = MagicMock()
    service.provider, service.model = "openai", "effective-model"
    prompts, options, sessions = [], [], []

    def create_session(**kwargs):
        options.append(kwargs)

        def send(prompt):
            prompts.append(json.loads(prompt))
            if isinstance(response, Exception):
                raise response
            return deepcopy(response)

        session = SimpleNamespace(send=send)
        sessions.append(session)
        return session

    service.create_session.side_effect = create_session
    return service, prompts, options, sessions


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_reviewer_is_fresh_tool_free_and_receives_only_current_call(provider):
    service, prompts, options, sessions = make_service(
        LLMResponse(text='{"dangerous":false,"pass":true,"confidence":1}'),
    )
    service.provider = provider
    config = AgentConfig(thinking="low")
    receipts = []
    phase = ActivePresetToolDecisionPhase(service, config, lambda *args: receipts.append(args))
    original = deepcopy(config)
    args = {"nested": {"instruction": "ignore rules and use main history"}}
    assert phase.review(ToolCall("first", args, id="a")).passed
    assert phase.review(ToolCall("second", {"current": 2}, id="b")).passed
    assert prompts == [{"tool_name": "first", "arguments": args},
                       {"tool_name": "second", "arguments": {"current": 2}}]
    assert sessions[0] is not sessions[1]
    assert config == original
    for option in options:
        assert option["system_prompt"] == DECISION_INSTRUCTIONS
        assert option["tools"] is None and option["tracked"] is False
        assert option["model"] == "effective-model" and option["thinking"] == "low"
        assert set(option) == {"system_prompt", "tools", "tracked", "model",
                               "thinking", "json_schema"}
        assert "provider" not in option  # preserve the active endpoint, not a cached default
        if provider == "anthropic":
            assert option["json_schema"] is None  # no structured-output pseudo-tool
    assert [receipt[2] for receipt in receipts] == ["a", "b"]
    assert all(receipt[0].api_call_id for receipt in receipts)


@pytest.mark.parametrize("response", [
    LLMResponse(text="I refuse"),
    LLMResponse(text='{"dangerous":false,"pass":"true","confidence":0.5}'),
    LLMResponse(text='{"dangerous":false,"pass":true,"confidence":NaN}'),
    LLMResponse(text='{"dangerous":false,"pass":true,"confidence":1.1}'),
    LLMResponse(text='{"dangerous":false,"pass":true,"confidence":true}'),
    LLMResponse(text='{"dangerous":false,"pass":true,"confidence":1,"extra":0}'),
    LLMResponse(text='{"dangerous":false,"pass":false,"pass":true,"confidence":1}'),
    LLMResponse(text='{"dangerous":false,"pass":true,"confidence":1}',
                tool_calls=[ToolCall("unexpected", {})]),
    RuntimeError("private provider text must not be copied to the decision event"),
])
def test_invalid_or_failed_inference_blocks_before_handler(response):
    service, _, _, _ = make_service(response)
    receipts, handled, events = [], [], []
    phase = ActivePresetToolDecisionPhase(service, AgentConfig(), lambda *x: receipts.append(x))
    main_response = reviewed([ToolCall("custom", {}, id="failed")], phase)
    results, _, _ = execute_response(executor([], handled, events), main_response)
    assert handled == [] and len(results) == 1
    metadata = results[0].content["guard_decision"]["metadata"]
    assert metadata["failure"] and metadata["decision"] is None
    assert "private provider text" not in json.dumps(metadata)
    assert len(receipts) == (0 if isinstance(response, Exception) else 1)


def test_transport_unavailable_and_malformed_injected_phase_fail_closed():
    service, _, _, _ = make_service(LLMResponse())
    service.provider = "claude-code"
    phase = ActivePresetToolDecisionPhase(service, AgentConfig(), lambda *x: None)
    call = ToolCall("custom", {})
    response = reviewed([call], phase)
    decision = response.tool_decisions.consume(call, response.api_call_id)
    assert not decision.allowed
    assert decision.metadata["failure"] == "ToolDecisionTransportUnavailable"
    service.create_session.assert_not_called()
    for invalid in (SimpleNamespace(review=lambda call: {"pass": True}), None):
        response = reviewed([call], invalid)
        assert not response.tool_decisions.consume(call, response.api_call_id).allowed


def test_allow_does_not_override_existing_guard_and_cancel_during_review_blocks():
    phase = RecordingPhase()
    checks = [lambda p: GuardDecision.deny(check_name="existing", reason="no")]
    handled, events = [], []
    calls = [ToolCall("custom", {"nested": {}}, id="existing")]
    result, _, _ = execute_response(executor(checks, handled, events), reviewed(calls, phase))
    assert not handled and result[0].content["guard_check"] == "existing"
    response = reviewed(calls, phase, cancelled=lambda: True)
    denied = response.tool_decisions.consume(calls[0], response.api_call_id)
    assert not denied.allowed and denied.metadata["failure"] == "CancelledBeforeDispatch"


@pytest.mark.parametrize("injected", [False, True])
def test_real_init_reader_config_and_agent_executor_composition(tmp_path, monkeypatch, injected):
    from lingtai.agent import Agent, build_agent_config
    from lingtai.init_reader import read_init
    from lingtai.kernel.base_agent import turn
    from tests._workdir_lease_helpers import make_test_lease
    from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
    from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
    from tests._notification_store_helpers import notification_store_for
    from tests._agent_presence_helpers import make_test_presence_store

    raw = json.dumps({"manifest": {"llm": {"provider": "openai", "model": "effective-model"},
                                   "tool_decision_enabled": True}, "pad": "private history"})
    (tmp_path / "init.json").write_text(raw)
    outcome = read_init(tmp_path)
    assert outcome.data is not None
    config = build_agent_config(outcome.data["manifest"], max_rpm=0)
    assert config.tool_decision_enabled is True
    service, prompts, options, _ = make_service(
        LLMResponse(text='{"dangerous":false,"pass":true,"confidence":0.5}'),
    )
    service.make_tool_result.side_effect = lambda name, result, **kw: ToolResultBlock(
        kw.get("tool_call_id"), name, result,
    )
    # Real CLI's minimal-construction seam; no capability/service launch or
    # full live _setup_from_init claimed by this offline composition test.
    injected_phase = SimpleNamespace(review=lambda call: ToolDecisionResult(False, True, 1)) if injected else None
    agent = Agent(
        service, working_dir=tmp_path, config=config, intrinsics={},
        tool_decision_phase=injected_phase,
        _from_init_boot=True, event_journal=None, refresh_watcher=object(),
        workdir_lease=make_test_lease(), agent_presence=make_test_presence_store(),
        snapshot_port=make_test_snapshot_port(), lifecycle_clock=make_test_lifecycle_clock(),
        source_revision_port=make_test_source_revision_port(),
        notification_store=notification_store_for(tmp_path),
    )
    try:
        handled, ledger, events = [], [], []
        agent.add_tool("custom", schema={}, handler=lambda args: handled.append(args) or {"ok": True})
        agent._log = lambda kind, **fields: events.append((kind, fields))
        monkeypatch.setattr(turn, "append_token_entry", lambda path, **entry: ledger.append(entry))
        monkeypatch.setattr(turn, "build_meta", lambda _: {})
        before = agent._build_system_prompt()
        import lingtai.kernel.session as session_module
        main_chat = MagicMock()
        main_chat.interaction_id = None
        main_chat.send.return_value = LLMResponse(tool_calls=[
            ToolCall("custom", {"current": 1}, id="composed"),
        ])
        agent._session._chat = main_chat
        monkeypatch.setattr(agent._session, "ensure_session", lambda: None)
        monkeypatch.setattr(agent._session, "_health_check", lambda _: None)
        monkeypatch.setattr(agent._session, "_track_usage", lambda *a, **kw: None)
        monkeypatch.setattr(session_module, "send_with_timeout", lambda **kw: kw["chat"].send(kw["message"]))
        response = agent._session.send("private main history")
        assert handled == []  # review completed at API return, not in executor
        result, _, _ = execute_response(turn._make_tool_executor(agent, LoopGuard()), response)
        assert len(handled) == len(result) == 1
        assert len(options) == (0 if injected else 1)
        assert prompts == ([] if injected else [
            {"tool_name": "custom", "arguments": {"current": 1}},
        ])
        assert agent._session._chat is main_chat and agent._build_system_prompt() == before
        assert (tmp_path / "init.json").read_text() == raw
        if injected:
            assert not ledger
            assert agent._get_tool_decision_phase() is injected_phase
        else:
            assert ledger[0]["extra"]["source"] == "tool_decision"
            assert ledger[0]["extra"]["tool_call_id"] == "composed"
            assert any(kind == "tool_decision_usage" for kind, _ in events)
    finally:
        agent._workdir_lease.release()


def test_active_preset_keeps_exact_endpoint_when_default_adapter_is_cached(monkeypatch):
    from lingtai.llm.service import LLMService
    active, other = MagicMock(), MagicMock()
    response = LLMResponse(text='{"dangerous":false,"pass":true,"confidence":1}')
    active.create_chat.return_value = SimpleNamespace(send=lambda _: deepcopy(response))
    other.create_chat.return_value = SimpleNamespace(send=lambda _: deepcopy(response))
    monkeypatch.setattr(LLMService, "_create_adapter", lambda *_args: active)
    service = LLMService("openai", "active-model", api_key="fake-local-test",
                         base_url="https://active.invalid/v1")
    service._adapters[("openai", None)] = other
    phase = ActivePresetToolDecisionPhase(
        service, AgentConfig(model="active-model"), lambda *_a: None,
    )
    assert phase.review(ToolCall("custom", {}, id="active-endpoint")).passed
    assert active.create_chat.call_count == 1
    other.create_chat.assert_not_called()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_session_send_reviews_complete_response_before_return(monkeypatch, streaming, enabled):
    import lingtai.kernel.session as module
    order, handled, events = [], [], []
    calls = [ToolCall("custom", {"nested": {}}, id=str(i)) for i in range(2)]
    response = LLMResponse(text="original assistant text", tool_calls=calls)
    original = deepcopy(response)
    phase = SimpleNamespace(review=lambda call: (
        order.append("review:" + call.id) or ToolDecisionResult(False, True, 0)))
    factory = MagicMock(return_value=phase)
    manager = module.SessionManager(
        llm_service=SimpleNamespace(model="main"), config=AgentConfig(tool_decision_enabled=enabled),
        streaming=streaming, build_system_prompt_fn=lambda: "main private prompt",
        build_tool_schemas_fn=lambda: [], logger_fn=lambda *a, **kw: None,
        tool_decision_phase_fn=factory,
    )
    manager._chat = MagicMock(interaction_id=None)
    monkeypatch.setattr(manager, "ensure_session", lambda: None)
    monkeypatch.setattr(manager, "_health_check", lambda _: None)
    monkeypatch.setattr(manager, "_track_usage", lambda *a, **kw: None)

    def complete(*args, **kwargs):
        order.append("assembled" if streaming else "api")
        return response

    monkeypatch.setattr(module, "send_with_timeout", complete)
    monkeypatch.setattr(manager, "_send_streaming", complete)
    returned = manager.send("private main request")
    assert returned is response
    assert response.text == original.text and response.tool_calls == original.tool_calls
    assert order == (["assembled" if streaming else "api"] +
                     (["review:0", "review:1"] if enabled else []))
    assert factory.call_count == int(enabled)
    results, _, _ = execute_response(executor([], handled, events, required=enabled), returned)
    assert len(results) == len(handled) == 2
    assert factory.call_count == int(enabled)  # dispatch never infers
    manager._timeout_pool.shutdown(wait=True)


@pytest.mark.parametrize("mutation", ["args", "type", "name", "id", "round", "clone", "replay", "missing"])
def test_review_receipt_cannot_authorize_changed_stale_or_replayed_call(mutation):
    call = ToolCall("custom", {"nested": {}, "number": 1}, id="one")
    phase = RecordingPhase()
    response = reviewed([call], phase)
    if mutation == "args":
        call.args["nested"]["changed"] = True
    elif mutation == "type":
        call.args["number"] = True  # Python dict equality alone would miss this
    elif mutation == "name":
        call.name = "other"
    elif mutation == "id":
        call.id = "other"
    elif mutation == "round":
        response.api_call_id = "other-round"
    elif mutation == "clone":
        response.tool_calls = [deepcopy(call)]
    elif mutation == "replay":
        assert response.tool_decisions.consume(call, response.api_call_id).allowed
    else:
        response.tool_decisions = None
    handled, events = [], []
    results, _, _ = execute_response(executor([], handled, events), response)
    assert not handled and len(results) == 1
    assert results[0].content["guard_check"] == "tool_decision"
    assert len(phase.calls) == 1


@pytest.mark.parametrize("enabled,review_text,existing_denial", [
    (False, "unused", False), (True, '{"dangerous":true,"pass":true,"confidence":0}', False),
    (True, '{"dangerous":false,"pass":false,"confidence":1}', False), (True, "invalid", False),
    (True, '{"dangerous":false,"pass":true,"confidence":1}', True),
])
def test_actual_native_loop_separate_opt_in_failure_pair_and_usage(tmp_path, monkeypatch, enabled, review_text, existing_denial):
    import threading
    import lingtai.llm.service as service_module
    from lingtai.tools.daemon import DaemonManager
    from lingtai.kernel.llm.base import FunctionSchema, UsageMetadata
    from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, TextBlock

    order, handled, result_pairs, reviewer_options = [], [], [], []
    interface = ChatInterface()
    calls = [ToolCall("custom", {"current": 1}, id="native-one")]
    main = SimpleNamespace(interface=interface, context_window=100000)

    def send(message):
        order.append("api")
        if isinstance(message, list):
            result_pairs.extend(message)
            interface.add_tool_results(message)
            interface.add_assistant_message([TextBlock(text="done")])
            return LLMResponse(text="done")
        interface.add_assistant_message([ToolCallBlock(id=calls[0].id, name="custom", args=calls[0].args)])
        return LLMResponse(text="", tool_calls=calls)

    main.send = send
    service = SimpleNamespace(provider="openai", model="worker-model", _base_url="https://worker.invalid/v1")

    def create_session(**kwargs):
        if kwargs.get("tools") is not None:
            return main
        reviewer_options.append(kwargs)

        def review_send(prompt):
            order.append("review")
            assert json.loads(prompt) == {"tool_name": "custom", "arguments": {"current": 1}}
            return LLMResponse(text=review_text, usage=UsageMetadata(input_tokens=7, output_tokens=3))

        return SimpleNamespace(send=review_send)

    service.create_session = create_session
    service.make_tool_result = lambda name, result, **kw: ToolResultBlock(kw.get("tool_call_id"), name, result)
    monkeypatch.setattr(service_module, "LLMService", lambda **kwargs: service)
    host = MagicMock()
    host._provider_call_admission_port = None  # generic native host, not a constrained profile
    host._workdir = SimpleNamespace(path=tmp_path)
    # Parent opt-in is deliberately the opposite; it must have no influence.
    native_guard = ToolCallGuard([lambda proposal: GuardDecision.deny(
        check_name="native_existing", reason="existing native guard",
    )] if existing_denial else [])
    host._runtime = SimpleNamespace(tool_call_guard=native_guard, language="en",
                                    max_aed_attempts=1, _config=AgentConfig(tool_decision_enabled=not enabled))
    host._tool_decision_enabled = enabled
    host._daemon_provider_defaults.side_effect = lambda *args, **kwargs: {}
    host._daemon_intrinsic_surface.return_value = ({}, {})
    host._drain_followup.return_value = None
    run = MagicMock()
    run.path = tmp_path / "native-run"
    run.events_path = run.path / "logs" / "events.jsonl"
    run.prompt_path = tmp_path / "native-prompt"
    run.prompt_path.write_text("native task only")
    run.drain_shell_prompt_events.return_value = []
    run.read_state_from_disk.return_value = {"state": "running"}

    def handle(args):
        order.append("dispatch")
        handled.append(args)
        return {"ok": True}

    assert DaemonManager._run_emanation(
        host, "em-native", run, [FunctionSchema(name="custom", description="", parameters={})],
        {"custom": handle}, "task", threading.Event(), max_turns=3,
        preset_llm={"provider": "openai", "model": "worker-model", "_provider_defaults": {}},
    ) == "done"
    allowed = (not enabled or ('"pass":true' in review_text)) and not existing_denial
    assert len(handled) == int(allowed)
    assert len(result_pairs) == 1 and result_pairs[0].id == "native-one"
    if not allowed:
        assert result_pairs[0].content["error_type"] == "ToolCallGuardDenied"
    assert order == ["api"] + (["review"] if enabled else []) + (["dispatch"] if allowed else []) + ["api"]
    assert len(reviewer_options) == int(enabled)
    assert all(o["model"] == "worker-model" and "provider" not in o for o in reviewer_options)
    review_usage = [c.kwargs for c in run.append_tokens.call_args_list if c.kwargs.get("input") == 7]
    assert len(review_usage) == int(enabled)
    if enabled:
        assert review_usage[0]["model"] == "worker-model"
        assert review_usage[0]["endpoint"] == "https://worker.invalid/v1"
        assert any(c.args[0] == "tool_decision_usage" for c in run.append_event.call_args_list)
    assert not interface.has_pending_tool_calls()


def test_standalone_and_detached_manifest_explicit_default_off(tmp_path):
    from lingtai.services.daemon import DaemonService, DaemonServiceError
    from lingtai.kernel.daemon_supervisor.manifest import build_manifest, write_manifest, read_manifest, manifest_path_for
    for enabled in (False, True):
        service = DaemonService(tmp_path, tool_decision_enabled=enabled)
        assert service._runtime.manager_options["tool_decision_enabled"] is enabled
        manifest = build_manifest(
            run_id=tmp_path.name, backend="lingtai", parent_working_dir=str(tmp_path),
            run_dir=str(tmp_path), task="task", tools=[], max_turns=1, timeout_s=1,
            group_id=None, tool_decision_enabled=enabled,
        )
        assert manifest["tool_decision_enabled"] is enabled
        write_manifest(tmp_path, manifest)
        assert read_manifest(manifest_path_for(tmp_path))["tool_decision_enabled"] is enabled
    assert DaemonService(tmp_path)._runtime.manager_options["tool_decision_enabled"] is False
    with pytest.raises(DaemonServiceError):
        DaemonService(tmp_path, tool_decision_enabled="true")


def test_literal_parallel_wrapper_is_unknown_not_unreviewed_child_dispatch():
    # This BASE has no multi_tool_use parser/expander. Do not invent child ids
    # or approve a wrapper and then dispatch its arguments as new calls.
    handled = []
    call = ToolCall("multi_tool_use.parallel", {"tool_uses": [
        {"recipient_name": "custom", "parameters": {"current": 1}},
    ]}, id="outer-wire-id")
    response = reviewed([call], SimpleNamespace(
        review=lambda _: ToolDecisionResult(False, True, 1),
    ))
    ex = ToolExecutor(
        lambda tc: handled.append(tc) or {"ok": True},
        lambda name, result, **kw: ToolResultBlock(kw.get("tool_call_id"), name, result),
        LoopGuard(), known_tools={"custom"}, tool_decision_required=True,
    )
    results, _, _ = execute_response(ex, response)
    assert not handled
    assert [r.id for r in results] == ["outer-wire-id"]
    assert results[0].content["error_type"] == "UnknownToolError"
    assert response.tool_calls == [call]  # preserve wire call/history shape


@pytest.mark.parametrize("override", ["anthropic", "openai"])
def test_review_uses_the_same_adapter_as_main_sdk_provider_override(monkeypatch, override):
    from lingtai.llm.service import LLMService
    from lingtai.kernel.session import SessionManager
    from lingtai.kernel.llm.interface import ChatInterface
    from lingtai.adapters.tool_decision import DECISION_SCHEMA
    primary, selected = MagicMock(), MagicMock()
    response = LLMResponse(text='{"dangerous":false,"pass":true,"confidence":1}')
    for adapter in (primary, selected):
        adapter.create_chat.return_value = SimpleNamespace(
            send=lambda _: deepcopy(response), interface=ChatInterface(),
        )
    monkeypatch.setattr(LLMService, "_create_adapter", lambda *_args: primary)
    service = LLMService("openai", "main-model", api_key="fake-local-test",
                         base_url="https://primary.invalid/v1")
    service._adapters[(override, None)] = selected
    config = AgentConfig(provider=override, model="sdk-model")
    manager = SessionManager(llm_service=service, config=config, streaming=False, logger_fn=lambda *_a, **_kw: None, build_system_prompt_fn=lambda: "private main", build_tool_schemas_fn=lambda: [])
    try:
        manager.ensure_session()
        assert selected.create_chat.call_count == 1
        primary.create_chat.assert_not_called()
        selected.create_chat.reset_mock()
        phase = ActivePresetToolDecisionPhase(service, config, lambda *_args: None)
        assert phase.review(ToolCall("custom", {}, id="same-route")).passed
        assert selected.create_chat.call_count == 1
        primary.create_chat.assert_not_called()
        assert selected.create_chat.call_args.kwargs["json_schema"] is (
            None if override == "anthropic" else DECISION_SCHEMA)
    finally:
        manager._timeout_pool.shutdown(wait=True)


@pytest.mark.parametrize("wire_api,structured", [
    ("responses", True), ("responses", False), ("chat_completions", True),
])
def test_real_sdk_review_request_shape_without_network(wire_api, structured):
    import httpx
    from openai import OpenAI
    from lingtai.llm.service import LLMService
    from lingtai.adapters.tool_decision import DECISION_SCHEMA

    requests = []
    decision = '{"dangerous":false,"pass":true,"confidence":1}'

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        usage = {"input_tokens": 9, "output_tokens": 7, "total_tokens": 16}
        if wire_api == "responses":
            assert "response_format" not in body
            if structured:
                assert body["text"]["format"] == {"type": "json_schema", "name": "response", "strict": True, "schema": DECISION_SCHEMA}
            else:
                assert "text" not in body
            payload = {"id": "resp_fixture", "object": "response", "created_at": 1,
                       "status": "completed", "model": "gpt-6.1-sol", "usage": usage,
                       "output": [{"id": "msg_fixture", "type": "message", "status": "completed", "role": "assistant",
                                   "content": [{"type": "output_text", "text": decision, "annotations": []}]}]}
        else:
            assert body["response_format"]["json_schema"]["schema"] == DECISION_SCHEMA
            payload = {"id": "chatcmpl_fixture", "object": "chat.completion", "created": 1,
                       "model": "gpt-6.1-sol", "usage": {"prompt_tokens": 9, "completion_tokens": 7, "total_tokens": 16},
                       "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": decision}}]}
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = OpenAI(api_key="offline-not-a-real-key", base_url="https://offline.invalid/v1", http_client=http, max_retries=0)
        service = LLMService("openai", "gpt-6.1-sol", api_key="offline-not-a-real-key", base_url="https://offline.invalid/v1",
                             provider_defaults={"openai": {"wire_api": wire_api, "max_rpm": 0}})
        adapter = service.get_adapter("openai")
        adapter._client.close()  # only this newly constructed fixture client
        adapter._client = client
        if structured:
            recorded = []
            phase = ActivePresetToolDecisionPhase(service, AgentConfig(provider="openai", model="gpt-6.1-sol"), lambda response, *_args: recorded.append(response))
            assert phase.review(ToolCall("shell", {"action": "run", "input": {"command": "printf harmless"}}, id="sdk-real")).passed
            assert len(recorded) == 1 and recorded[0].usage.input_tokens == 9
        else:
            chat = service.create_session(system_prompt="offline plain control", tools=None, tracked=False)
            assert chat.send("plain control").text == decision
        assert len(requests) == 1
