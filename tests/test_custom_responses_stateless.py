"""Custom OpenAI-compatible Responses sessions replay canonical history."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from lingtai.llm.custom.adapter import create_custom_adapter
import lingtai.llm.openai.adapter as openai_adapter_module
from lingtai.llm.openai.adapter import OpenAIAdapter


def _usage(input_tokens: int = 10, output_tokens: int = 5, reasoning_tokens: int = 0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=1),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )


def _text_raw(response_id: str, text: str = "ok"):
    return SimpleNamespace(
        id=response_id,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=_usage(),
    )


def _tool_raw():
    return SimpleNamespace(
        id="resp_tool",
        output=[
            SimpleNamespace(
                type="reasoning",
                summary=[SimpleNamespace(type="summary_text", text="Need tool.")],
            ),
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="Checking.")],
            ),
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="lookup",
                arguments='{"query":"x"}',
            ),
        ],
        usage=_usage(reasoning_tokens=3),
    )


class _Responses:
    def __init__(self, results):
        self._results = list(results)
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result() if callable(result) else result


class _StreamResponses(_Responses):
    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            result = result()
        return iter(result)


class _Client:
    def __init__(self, responses):
        self.responses = responses
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: None),
        )


def _stream_text(response_id: str, text: str = "ok"):
    return [
        SimpleNamespace(type="response.output_text.delta", delta=text),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(id=response_id, usage=_usage()),
        ),
    ]


def _stream_tool_events(response_id: str):
    return [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(type="reasoning", id="rs_1"),
        ),
        SimpleNamespace(
            type="response.reasoning_summary_text.delta",
            delta="Need streamed tool.",
            item_id="rs_1",
        ),
        SimpleNamespace(
            type="response.reasoning_summary_text.done",
            text="Need streamed tool.",
            item_id="rs_1",
        ),
        SimpleNamespace(type="response.output_text.delta", delta="Checking stream."),
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_stream",
                name="lookup",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            delta='{"query"',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            delta=':"stream"}',
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_stream",
                name="lookup",
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id=response_id,
                usage=_usage(input_tokens=15, output_tokens=8, reasoning_tokens=2),
            ),
        ),
    ]


def _broken_stream():
    yield SimpleNamespace(type="response.output_text.delta", delta="partial")
    raise RuntimeError("stream")


def _tool() -> FunctionSchema:
    return FunctionSchema(
        name="lookup",
        description="Lookup",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )


def test_custom_factory_marks_openai_compatible_responses_stateless_for_explicit_and_legacy():
    explicit = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    legacy = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        use_responses=True,
        force_responses=True,
    )

    assert explicit._responses_stateless_replay is True
    assert legacy._responses_stateless_replay is True
    assert explicit._should_use_responses() is True
    assert legacy._should_use_responses() is True


def test_custom_responses_nonstreaming_replays_full_history_and_records_assistant():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_tool_raw(), _text_raw("resp_2", "done")]))
    session = adapter.create_chat("gpt-test", "system", tools=[_tool()])

    first = session.send("start")
    assert first.thoughts == ["Need tool."]
    assert first.text == "Checking."
    assert first.tool_calls[0].id == "call_1"

    result = session.send([
        ToolResultBlock(id="call_1", name="lookup", content={"value": 1}),
    ])
    assert result.text == "done"

    second = adapter._client.responses.kwargs[1]
    assert "previous_response_id" not in second
    assert second["input"] == [
        {"role": "user", "content": "start"},
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Need tool."}],
        },
        {"role": "assistant", "content": "Checking."},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"query": "x"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"value": 1}',
        },
    ]
    assistant_entries = [e for e in session.interface.entries if e.role == "assistant"]
    assert isinstance(assistant_entries[0].content[0], ThinkingBlock)
    assert isinstance(assistant_entries[0].content[1], TextBlock)
    assert isinstance(assistant_entries[0].content[2], ToolCallBlock)
    assert assistant_entries[-1].usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "thinking_tokens": 0,
        "cached_tokens": 1,
    }


def test_custom_responses_streaming_replays_reasoning_tool_result_full_history():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(
        _StreamResponses([
            _stream_tool_events("resp_1"),
            _stream_text("resp_2", "done"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system", tools=[_tool()])

    first = session.send_stream("first")
    assert first.thoughts == ["Need streamed tool."]
    assert first.text == "Checking stream."
    assert first.tool_calls[0].id == "call_stream"

    result = session.send_stream([
        ToolResultBlock(id="call_stream", name="lookup", content={"value": "streamed"}),
    ])
    assert result.text == "done"

    second = adapter._client.responses.kwargs[1]
    assert "previous_response_id" not in second
    assert second["input"] == [
        {"role": "user", "content": "first"},
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Need streamed tool."}],
        },
        {"role": "assistant", "content": "Checking stream."},
        {
            "type": "function_call",
            "call_id": "call_stream",
            "name": "lookup",
            "arguments": '{"query": "stream"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_stream",
            "output": '{"value": "streamed"}',
        },
    ]
    assistant_entries = [e for e in session.interface.entries if e.role == "assistant"]
    assert isinstance(assistant_entries[0].content[0], ThinkingBlock)
    assert isinstance(assistant_entries[0].content[1], TextBlock)
    assert isinstance(assistant_entries[0].content[2], ToolCallBlock)
    assert assistant_entries[0].usage == {
        "input_tokens": 15,
        "output_tokens": 8,
        "thinking_tokens": 2,
        "cached_tokens": 1,
    }


def test_send_none_replays_pre_staged_notification_style_pair():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_text_raw("resp_1", "noticed")]))
    iface = ChatInterface()
    iface.add_system("system")
    iface.add_assistant_message([
        ToolCallBlock(id="notif_1", name="notification", args={"action": "check"}),
    ])
    iface.add_tool_results([
        ToolResultBlock(id="notif_1", name="notification", content={"email": {"count": 1}}),
    ])
    session = adapter.create_chat("gpt-test", "system", interface=iface)

    assert session.send(None).text == "noticed"

    sent = adapter._client.responses.kwargs[0]
    assert sent["input"][:2] == [
        {
            "type": "function_call",
            "call_id": "notif_1",
            "name": "notification",
            "arguments": '{"action": "check"}',
        },
        {
            "type": "function_call_output",
            "call_id": "notif_1",
            "output": '{"email": {"count": 1}}',
        },
    ]


def test_send_none_failure_preserves_pre_staged_notification_style_pair():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([RuntimeError("transport")]))
    iface = ChatInterface()
    iface.add_system("system")
    iface.add_assistant_message([
        ToolCallBlock(id="notif_1", name="notification", args={"action": "check"}),
    ])
    iface.add_tool_results([
        ToolResultBlock(id="notif_1", name="notification", content={"email": {"count": 1}}),
    ])
    session = adapter.create_chat("gpt-test", "system", interface=iface)
    before = session.interface.to_dict()

    with pytest.raises(RuntimeError, match="transport"):
        session.send(None)

    assert session.interface.to_dict() == before


def test_pre_request_hook_entries_replay_on_same_stateless_responses_request():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_text_raw("resp_1", "noticed")]))
    session = adapter.create_chat("gpt-test", "system")

    def hook(iface):
        iface.add_assistant_message([
            ToolCallBlock(id="notif_1", name="notification", args={"action": "check"}),
        ])
        iface.add_tool_results([
            ToolResultBlock(id="notif_1", name="notification", content={"count": 1}),
        ])

    session.pre_request_hook = hook
    session.send("hello")

    assert adapter._client.responses.kwargs[0]["input"][:3] == [
        {"role": "user", "content": "hello"},
        {
            "type": "function_call",
            "call_id": "notif_1",
            "name": "notification",
            "arguments": '{"action": "check"}',
        },
        {
            "type": "function_call_output",
            "call_id": "notif_1",
            "output": '{"count": 1}',
        },
    ]


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("transport"),
        lambda: SimpleNamespace(id="bad", usage=_usage()),
    ],
)
def test_stateless_send_rolls_back_staged_user_input_on_transport_or_parse_failure(failure):
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([failure]))
    session = adapter.create_chat("gpt-test", "system")

    with pytest.raises(Exception):
        session.send("will fail")

    assert [e.role for e in session.interface.entries] == ["system"]


@pytest.mark.parametrize("failure_point", ["enforce", "serialize"])
def test_stateless_send_rolls_back_if_enforce_or_serialize_fails_after_staging(
    monkeypatch,
    failure_point,
):
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_text_raw("unused")]))
    session = adapter.create_chat("gpt-test", "system")
    before = session.interface.to_dict()

    if failure_point == "enforce":
        def fail_enforce():
            raise RuntimeError("enforce")

        monkeypatch.setattr(session.interface, "enforce_tool_pairing", fail_enforce)
    else:
        def fail_serialize(_iface):
            raise RuntimeError("serialize")

        monkeypatch.setattr(openai_adapter_module, "to_responses_input", fail_serialize)

    with pytest.raises(RuntimeError, match=failure_point):
        session.send("will fail after staging")

    assert session.interface.to_dict() == before


def test_stateless_stream_rolls_back_on_iteration_and_callback_failure():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_StreamResponses([_broken_stream]))
    session = adapter.create_chat("gpt-test", "system")

    with pytest.raises(RuntimeError):
        session.send_stream("will fail")
    assert [e.role for e in session.interface.entries] == ["system"]

    session._client = _Client(_StreamResponses([_stream_text("resp_1", "x")]))
    with pytest.raises(RuntimeError):
        session.send_stream("callback fail", on_chunk=lambda _text: (_ for _ in ()).throw(RuntimeError("callback")))
    assert [e.role for e in session.interface.entries] == ["system"]


def test_stateless_rolls_back_replaced_synthesized_tool_result_on_record_failure(monkeypatch):
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_text_raw("resp_1", "after tool")]))
    iface = ChatInterface()
    iface.add_system("system")
    iface.add_user_message("run")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="lookup", args={})])
    iface.close_pending_tool_calls("restore path", tool_completed=True)
    recovery_lookup = lambda _call: None
    iface.tool_result_recovery_lookup = recovery_lookup
    session = adapter.create_chat("gpt-test", "system", interface=iface)
    before = session.interface.to_dict()

    def fail_record(*_args, **_kwargs):
        raise RuntimeError("record")

    monkeypatch.setattr(session.interface, "add_assistant_message", fail_record)
    with pytest.raises(RuntimeError, match="record"):
        session.send([ToolResultBlock(id="call_1", name="lookup", content={"real": True})])

    assert session.interface.to_dict() == before
    assert session.interface.tool_result_recovery_lookup is recovery_lookup

    monkeypatch.undo()
    session._client = _Client(_Responses([_text_raw("resp_2", "after tool")]))
    session.send([ToolResultBlock(id="call_1", name="lookup", content={"real": True})])

    sent_outputs = [
        item
        for item in session._client.responses.kwargs[0]["input"]
        if item.get("type") == "function_call_output" and item.get("call_id") == "call_1"
    ]
    assert sent_outputs == [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"real": true}',
        }
    ]
    assert "kernel notice" not in sent_outputs[0]["output"]


def test_stateless_history_round_trips_for_recreated_session_restart():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_text_raw("resp_1", "one")]))
    session = adapter.create_chat("gpt-test", "system")
    session.send("first")
    history = session.get_history()
    assert session.session_resume_id is None

    adapter._client = _Client(_Responses([_text_raw("resp_2", "two")]))
    restarted = adapter.create_chat(
        "gpt-test",
        "system",
        interface=ChatInterface.from_dict(history),
    )
    restarted.send("second")

    assert adapter._client.responses.kwargs[0]["input"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
    ]


def test_stateless_prompt_and_tool_updates_affect_replayed_request():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_text_raw("resp_1", "ok")]))
    session = adapter.create_chat("gpt-test", "old")
    session.update_system_prompt("new")
    session.update_tools([_tool()])

    session.send("hi")

    sent = adapter._client.responses.kwargs[0]
    assert sent["instructions"] == "new"
    assert sent["tools"][0]["name"] == "lookup"
    assert session.interface.current_system_prompt == "new"
    assert session.interface.current_tools == [_tool().to_dict()]


def test_stateless_custom_responses_preserves_context_management_compaction_request():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
        compact_threshold=12345,
    )
    adapter._client = _Client(_Responses([_text_raw("resp_1", "ok")]))
    session = adapter.create_chat("gpt-test", "system")

    session.send("hi")

    assert adapter._client.responses.kwargs[0]["context_management"] == [
        {"type": "compaction", "compact_threshold": 12345}
    ]


def test_official_openai_responses_remains_stateful_nonstreaming_and_streaming():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    adapter._client = _Client(_Responses([_text_raw("resp_1"), _text_raw("resp_2")]))
    session = adapter.create_chat("gpt-test", "system")

    session.send("first")
    session.send("second")

    assert "previous_response_id" not in adapter._client.responses.kwargs[0]
    assert adapter._client.responses.kwargs[0]["input"] == [
        {"role": "user", "content": "first"}
    ]
    assert adapter._client.responses.kwargs[1]["input"] == [
        {"role": "user", "content": "second"}
    ]
    assert adapter._client.responses.kwargs[1]["previous_response_id"] == "resp_1"
    assert session.session_resume_id == "resp_2"

    adapter._client = _Client(
        _StreamResponses([_stream_text("resp_s1"), _stream_text("resp_s2")])
    )
    stream_session = adapter.create_chat("gpt-test", "system")
    stream_session.send_stream("first")
    stream_session.send_stream("second")

    assert "previous_response_id" not in adapter._client.responses.kwargs[0]
    assert adapter._client.responses.kwargs[1]["input"] == [
        {"role": "user", "content": "second"}
    ]
    assert adapter._client.responses.kwargs[1]["previous_response_id"] == "resp_s1"
    assert stream_session.session_resume_id == "resp_s2"
