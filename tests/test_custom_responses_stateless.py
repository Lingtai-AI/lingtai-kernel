"""Custom OpenAI-compatible Responses sessions replay canonical history."""

from __future__ import annotations

import json
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


def _text_raw_assistant_list(response_id: str, text: str):
    """A raw output message with the SDK's assistant role and list content."""
    return SimpleNamespace(
        id=response_id,
        output=[
            SimpleNamespace(
                type="message",
                role="assistant",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=_usage(),
    )


def _tool_raw_with_assistant_message_role():
    """Raw tool turn whose output message carries the SDK role field."""
    raw = _tool_raw()
    raw.output[1].role = "assistant"
    return raw


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


def _stream_raw_tool_turn(response_id: str):
    """Stream a reasoning + function-call turn with a complete raw trailer."""
    output = [
        SimpleNamespace(
            type="reasoning",
            id="rs_raw_tool",
            summary=[SimpleNamespace(type="summary_text", text="Need tool.")],
        ),
        SimpleNamespace(
            type="message",
            id="msg_raw_tool",
            role="assistant",
            content=[SimpleNamespace(type="output_text", text="Checking.")],
        ),
        SimpleNamespace(
            type="function_call",
            id="fc_raw_tool",
            call_id="call_1",
            name="lookup",
            arguments='{"query":"x"}',
        ),
    ]
    return [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(type="reasoning", id="rs_raw_tool"),
        ),
        SimpleNamespace(
            type="response.reasoning_summary_text.delta",
            delta="Need tool.",
            item_id="rs_raw_tool",
        ),
        SimpleNamespace(
            type="response.reasoning_summary_text.done",
            text="Need tool.",
            item_id="rs_raw_tool",
        ),
        SimpleNamespace(type="response.output_text.delta", delta="Checking."),
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="lookup",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            delta='{"query":"x"}',
            item_id="call_1",
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=output[-1],
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id=response_id,
                output=output,
                usage=_usage(reasoning_tokens=2),
            ),
        ),
    ]


def _stream_raw_plain_list_turn(response_id: str, text: str = "final"):
    output = [
        SimpleNamespace(
            type="message",
            id="msg_raw_plain",
            role="assistant",
            content=[SimpleNamespace(type="output_text", text=text)],
        )
    ]
    return [
        SimpleNamespace(type="response.output_text.delta", delta=text),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id=response_id,
                output=output,
                usage=_usage(),
            ),
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


def _stream_raw_mixed_events(response_id: str):
    output = [
        SimpleNamespace(
            type="message",
            id="msg_mixed",
            phase="commentary",
            content=[SimpleNamespace(type="output_text", text="visible")],
        ),
        SimpleNamespace(type="reasoning", id="rs_mixed", summary=[]),
        SimpleNamespace(
            type="function_call",
            id="fc_mixed",
            status="completed",
            call_id="call_mixed",
            name="lookup",
            arguments='{"query":"x","n":1}',
        ),
    ]
    return [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(type="message", id="msg_mixed"),
        ),
        SimpleNamespace(type="response.output_text.delta", delta="visible"),
        SimpleNamespace(
            type="response.output_item.done",
            item=output[0],
        ),
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(type="reasoning", id="rs_mixed"),
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=output[1],
        ),
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                id="fc_mixed",
                call_id="call_mixed",
                name="lookup",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            delta='{"query"',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            delta=':"x","n":1}',
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=output[2],
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id=response_id,
                output=output,
                usage=_usage(input_tokens=15, output_tokens=8, reasoning_tokens=2),
            ),
        ),
    ]


def _forced_sse_tool_response(response_id: str) -> str:
    """SSE body from a provider that ignored a non-streaming request."""
    events = [
        {
            "type": "response.reasoning_summary_text.delta",
            "delta": "Need forced stream.",
            "item_id": "rs_forced",
        },
        {
            "type": "response.reasoning_summary_text.done",
            "text": "Need forced stream.",
            "item_id": "rs_forced",
        },
        {"type": "response.output_text.delta", "delta": "Checking forced stream."},
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "call_id": "call_forced",
                "name": "lookup",
            },
        },
        {
            "type": "response.function_call_arguments.done",
            "arguments": '{"query":"forced"}',
        },
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "call_forced",
                "name": "lookup",
                "arguments": '{"query":"forced"}',
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {
                    "input_tokens": 21,
                    "output_tokens": 9,
                    "input_tokens_details": {"cached_tokens": 4},
                    "output_tokens_details": {"reasoning_tokens": 3},
                },
            },
        },
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        for event in events
    )


def _forced_sse_without_usage(response_id: str) -> str:
    """Forced SSE body whose ``response.completed`` omits ``usage`` entirely."""
    events = [
        {"type": "response.output_text.delta", "delta": "No usage here."},
        {"type": "response.completed", "response": {"id": response_id}},
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        for event in events
    )


def _forced_sse_without_completed(response_id: str) -> str:
    """Forced SSE body that never emits ``response.completed``."""
    events = [
        {"type": "response.created", "response": {"id": response_id}},
        {"type": "response.output_text.delta", "delta": "Truncated."},
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        for event in events
    )


def _stream_without_completed(response_id: str):
    return [
        SimpleNamespace(
            type="response.created",
            response=SimpleNamespace(id=response_id),
        ),
        SimpleNamespace(type="response.output_text.delta", delta="truncated"),
    ]


def _stream_without_any_response_id():
    return [SimpleNamespace(type="response.output_text.delta", delta="orphan")]


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
        {
            "type": "message",
            "content": [
                {"type": "output_text", "text": "Checking."},
            ],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"query":"x"}',
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


def test_custom_responses_nonstreaming_parses_provider_forced_sse_without_retry():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_forced_sse_tool_response("resp_forced")]))
    session = adapter.create_chat("gpt-test", "system", tools=[_tool()])

    result = session.send("start")

    assert result.text == "Checking forced stream."
    assert result.thoughts == ["Need forced stream."]
    assert [(call.id, call.name, call.args) for call in result.tool_calls] == [
        ("call_forced", "lookup", {"query": "forced"}),
    ]
    assert result.usage.input_tokens == 21
    assert result.usage.output_tokens == 9
    assert result.usage.thinking_tokens == 3
    assert result.usage.cached_tokens == 4
    assert len(adapter._client.responses.kwargs) == 1
    assert "stream" not in adapter._client.responses.kwargs[0]

    assistant = session.interface.entries[-1]
    assert assistant.usage == {
        "input_tokens": 21,
        "output_tokens": 9,
        "thinking_tokens": 3,
        "cached_tokens": 4,
    }


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_nonstream_noncompleted_status_does_not_commit_raw_snapshot(status):
    raw = _text_raw("resp_partial", "partial")
    raw.status = status
    adapter = create_custom_adapter(
        api_key="fake", api_compat="openai",
        base_url="https://sub2api.example/v1", wire_api="responses",
    )
    adapter._client = _Client(_Responses([raw]))
    session = adapter.create_chat("gpt-test", "system")
    session.send("first")
    assert "openai_responses_output_items" not in (
        session.interface.entries[2].provider_data or {}
    )


def test_forced_sse_partial_item_done_does_not_commit_partial_raw_replay():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(
        _Responses([
            _forced_sse_tool_response("resp_forced"),
            _text_raw("resp_next", "done"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system", tools=[_tool()])

    session.send("first")
    session.send([
        ToolResultBlock(id="call_forced", name="lookup", content={"ok": True}),
    ])

    replay = adapter._client.responses.kwargs[1]["input"]
    assert {item["type"] for item in replay if "type" in item} >= {
        "reasoning",
        "function_call",
    }
    assert not any(item.get("type") == "message" for item in replay)


def test_forced_sse_completed_without_usage_does_not_crash():
    """Gateway JSON has no guaranteed fields — a missing ``usage`` must not raise."""
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(_Responses([_forced_sse_without_usage("resp_no_usage")]))
    session = adapter.create_chat("gpt-test", "system")

    result = session.send("start")

    assert result.text == "No usage here."
    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0
    assert result.usage.thinking_tokens == 0
    assert result.usage.cached_tokens == 0


def test_forced_sse_without_completed_still_latches_continuation_id():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    adapter._client = _Client(
        _Responses([
            _forced_sse_without_completed("resp_created"),
            _text_raw("resp_next", "second"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system")

    assert session.send("first").text == "Truncated."
    assert session.session_resume_id == "resp_created"

    session.send("second")
    assert adapter._client.responses.kwargs[1]["previous_response_id"] == "resp_created"


def test_stream_without_completed_latches_continuation_id_from_created():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    adapter._client = _Client(
        _StreamResponses([
            _stream_without_completed("resp_s_created"),
            _stream_text("resp_s2", "second"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system")

    session.send_stream("first")
    assert session.session_resume_id == "resp_s_created"

    session.send_stream("second")
    assert adapter._client.responses.kwargs[1]["previous_response_id"] == "resp_s_created"


def test_stream_with_no_response_id_keeps_previous_continuation_id():
    """An id-less stream must not silently wipe the server-side chain."""
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    adapter._client = _Client(
        _StreamResponses([
            _stream_text("resp_first", "one"),
            _stream_without_any_response_id(),
            _stream_text("resp_third", "three"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system")

    session.send_stream("first")
    session.send_stream("second")

    assert session.session_resume_id == "resp_first"

    session.send_stream("third")
    assert adapter._client.responses.kwargs[2]["previous_response_id"] == "resp_first"


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


def _stream_empty_completion_after_visible_delta(response_id: str):
    return [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="message",
                id="msg_empty_trailer",
                content=[SimpleNamespace(type="output_text", text="visible")],
            ),
        ),
        SimpleNamespace(type="response.output_text.delta", delta="visible"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id=response_id,
                output=[],
                usage=_usage(),
            ),
        ),
    ]


def _stream_trailer_only_output(response_id: str):
    return [
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id=response_id,
                output=[
                    SimpleNamespace(
                        type="message",
                        id="msg_trailer_only",
                        content=[SimpleNamespace(type="output_text", text="trailer text")],
                    ),
                    SimpleNamespace(
                        type="function_call",
                        id="fc_trailer_only",
                        call_id="call_trailer_only",
                        name="lookup",
                        arguments='{"query":"trailer"}',
                    ),
                ],
                usage=_usage(),
            ),
        ),
    ]


def _stream_done_only_indexed(response_id: str, rows):
    events = [
        SimpleNamespace(
            type="response.output_item.done",
            output_index=index,
            item=SimpleNamespace(
                type="message",
                id=item_id,
                content=[SimpleNamespace(type="output_text", text=text)],
            ),
        )
        for index, item_id, text in rows
    ]
    events.append(
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(id=response_id, usage=_usage()),
        )
    )
    return events


def test_custom_stream_empty_completion_output_keeps_observed_projection():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(
        _StreamResponses([
            _stream_empty_completion_after_visible_delta("resp_empty"),
            _stream_text("resp_next", "done"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system")

    first = session.send_stream("first")
    assert first.text == "visible"
    session.send_stream("second")

    assert adapter._client.responses.kwargs[1]["input"][1] == {
        "type": "message",
        "id": "msg_empty_trailer",
        "content": [{"type": "output_text", "text": "visible"}],
    }
    assert session.interface.entries[2].provider_data["openai_responses_output_items"]


def test_custom_stream_trailer_only_normalizes_visible_output_and_tool_call():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(
        _StreamResponses([
            _stream_trailer_only_output("resp_trailer_only"),
            _stream_text("resp_next", "done"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system", tools=[_tool()])

    chunks: list[str] = []
    first = session.send_stream("first", on_chunk=chunks.append)
    assert first.text == "trailer text"
    assert chunks == ["trailer text"]
    assert [(call.id, call.name, call.args) for call in first.tool_calls] == [
        ("call_trailer_only", "lookup", {"query": "trailer"}),
    ]
    session.send_stream([
        ToolResultBlock(id="call_trailer_only", name="lookup", content={"ok": True}),
    ])

    assert adapter._client.responses.kwargs[1]["input"][1:4] == [
        {
            "type": "message",
            "id": "msg_trailer_only",
            "content": [{"type": "output_text", "text": "trailer text"}],
        },
        {
            "type": "function_call",
            "id": "fc_trailer_only",
            "call_id": "call_trailer_only",
            "name": "lookup",
            "arguments": '{"query":"trailer"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_trailer_only",
            "output": '{"ok": true}',
        },
    ]
    assert [type(block) for block in session.interface.entries[2].content] == [
        TextBlock,
        ToolCallBlock,
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [(0, "msg_gap_0", "zero"), (2, "msg_gap_2", "two")],
        [(0, "msg_duplicate", "first"), (1, "msg_duplicate", "second")],
    ],
    ids=["indexed_gap", "duplicate_id"],
)
def test_custom_stream_done_only_unsafe_identity_falls_back_to_canonical(rows):
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(
        _StreamResponses([
            _stream_done_only_indexed("resp_unsafe", rows),
            _stream_text("resp_next", "done"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system")

    session.send_stream("first")
    session.send_stream("second")

    # Empty canonical assistant projections were already omitted by the
    # converter. Unsafe raw item identities must not reappear in the request.
    assert adapter._client.responses.kwargs[1]["input"] == [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
    ]
    assert "openai_responses_output_items" not in (
        session.interface.entries[2].provider_data or {}
    )


@pytest.mark.parametrize("separate_messages", [False, True])
def test_custom_stream_multiple_text_parts_preserve_raw_boundaries(separate_messages):
    parts = [
        SimpleNamespace(type="output_text", text="first"),
        SimpleNamespace(type="output_text", text="second"),
    ]
    output = [SimpleNamespace(type="message", id="msg_1", content=parts)]
    expected = [{
        "type": "message", "id": "msg_1",
        "content": [{"type": "output_text", "text": part.text} for part in parts],
    }]
    if separate_messages:
        output = [
            SimpleNamespace(type="message", id=f"msg_{index}", phase=phase, content=[part])
            for index, (phase, part) in enumerate(
                zip(("commentary", "final_answer"), parts), 1,
            )
        ]
        expected = [
            {"type": "message", "id": item.id, "phase": item.phase,
             "content": [{"type": "output_text", "text": item.content[0].text}]}
            for item in output
        ]
    events = [
        SimpleNamespace(type="response.output_text.delta", delta=part.text)
        for part in parts
    ]
    events.append(SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(id="resp_parts", output=output, usage=_usage()),
    ))
    adapter = create_custom_adapter(
        api_key="fake", api_compat="openai",
        base_url="https://sub2api.example/v1", wire_api="responses",
    )
    adapter._client = _Client(_StreamResponses([events, _stream_text("resp_next", "done")]))
    session = adapter.create_chat("gpt-test", "system")
    chunks = []
    response = session.send_stream("first", on_chunk=chunks.append)
    assert response.text == "firstsecond"
    assert chunks == ["first", "second"]
    session.send_stream("next")
    assert adapter._client.responses.kwargs[1]["input"][1:-1] == expected


def test_custom_stream_done_only_uses_output_index_order():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(
        _StreamResponses([
            _stream_done_only_indexed(
                "resp_indexed",
                [(1, "msg_one", "one"), (0, "msg_zero", "zero")],
            ),
            _stream_text("resp_next", "done"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system")

    session.send_stream("first")
    session.send_stream("second")

    assert adapter._client.responses.kwargs[1]["input"][1:3] == [
        {
            "type": "message",
            "id": "msg_zero",
            "content": [{"type": "output_text", "text": "zero"}],
        },
        {
            "type": "message",
            "id": "msg_one",
            "content": [{"type": "output_text", "text": "one"}],
        },
    ]


def test_custom_stream_replays_completion_output_items_without_projection_or_duplication():
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    adapter._client = _Client(
        _StreamResponses([
            _stream_raw_mixed_events("resp_mixed"),
            _stream_text("resp_next", "done"),
        ])
    )
    session = adapter.create_chat("gpt-test", "system", tools=[_tool()])

    session.send_stream("first")
    session.send_stream([
        ToolResultBlock(id="call_mixed", name="lookup", content={"ok": True}),
    ])

    assert adapter._client.responses.kwargs[1]["input"] == [
        {"role": "user", "content": "first"},
        {
            "type": "message",
            "id": "msg_mixed",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "visible"}],
        },
        {"type": "reasoning", "id": "rs_mixed", "summary": []},
        {
            "type": "function_call",
            "id": "fc_mixed",
            "status": "completed",
            "call_id": "call_mixed",
            "name": "lookup",
            "arguments": '{"query":"x","n":1}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_mixed",
            "output": '{"ok": true}',
        },
    ]


@pytest.mark.parametrize("streaming", [False, True])
def test_raw_message_without_reasoning_stays_authoritative_across_three_calls(
    monkeypatch,
    streaming,
):
    """Raw output remains an exact prefix; fallback only repairs canonical text."""
    # This exercises the shipped default rather than an explicit opt-out.
    monkeypatch.delenv("LINGTAI_INJECT_REASONING_FALLBACK", raising=False)
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://sub2api.example/v1",
        wire_api="responses",
    )
    if streaming:
        responses = _StreamResponses([
            _stream_raw_tool_turn("resp_1"),
            _stream_raw_plain_list_turn("resp_2"),
            _stream_text("resp_3", "next"),
        ])
    else:
        responses = _Responses([
            _tool_raw_with_assistant_message_role(),
            _text_raw_assistant_list("resp_2", "final"),
            _text_raw_assistant_list("resp_3", "next"),
        ])
    adapter._client = _Client(responses)
    session = adapter.create_chat("gpt-test", "system", tools=[_tool()])
    send = session.send_stream if streaming else session.send

    first = send("start")
    assert first.tool_calls[0].id == "call_1"
    second = send([
        ToolResultBlock(id="call_1", name="lookup", content={"value": 1}),
    ])
    assert second.text == "final"
    send("next")

    requests = adapter._client.responses.kwargs
    assert len(requests) == 3
    tool_request = requests[1]["input"]
    continuation_request = requests[2]["input"]
    # The first assistant output is replayed exactly, including item order and
    # list-valued message content, before the real tool result.
    assert tool_request[0] == {"role": "user", "content": "start"}
    assert [item.get("type") for item in tool_request[1:4]] == [
        "reasoning", "message", "function_call",
    ]
    assert tool_request[2]["content"] == [
        {"type": "output_text", "text": "Checking."},
    ]
    assert tool_request[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"value": 1}',
    }

    expected_plain = (
        {
            "type": "message",
            "id": "msg_raw_plain",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "final"}],
        }
        if streaming
        else {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "final"}],
        }
    )
    assert continuation_request == [*tool_request, expected_plain, {
        "role": "user", "content": "next",
    }]


@pytest.mark.parametrize("content", ["legacy final", "", None])
def test_reasoning_fallback_only_repairs_legacy_string_assistant_items(content):
    raw_message = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "raw final"}],
        "phase": "final_answer",
    }
    items = [
        {"type": "function_call", "call_id": "c1", "name": "f1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        {"role": "assistant", "content": content},
        raw_message,
    ]

    out = openai_adapter_module._inject_responses_reasoning_fallback(items)

    assert out[2]["type"] == "reasoning"
    assert out[3] == {"role": "assistant", "content": content}
    assert out[4] is raw_message
    assert out[4] == raw_message


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
        def fail_serialize(_iface, *, replay_raw_output_items=False):
            assert replay_raw_output_items is True
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
        {
            "type": "message",
            "content": [{"type": "output_text", "text": "one"}],
        },
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
