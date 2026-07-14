"""Tests for OpenAI Responses API streaming reasoning capture."""

from __future__ import annotations

import json

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from lingtai.llm.interface_converters import to_responses_input
from lingtai.llm.openai.adapter import (
    CodexOpenAIAdapter,
    OpenAIAdapter,
    OpenAIResponsesSession,
)
from lingtai.kernel.llm.base import FunctionSchema
from lingtai.kernel.llm.interface import TextBlock, ThinkingBlock, ToolCallBlock


@dataclass
class Event:
    type: str
    delta: str | None = None
    item: object | None = None
    response: object | None = None
    item_id: str | None = None
    text: str | None = None
    arguments: str | None = None


class FakeResponses:
    def __init__(self, events: list[Event]):
        self.events = events
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        yield from self.events


class FakeClient:
    def __init__(self, events: list[Event]):
        self.responses = FakeResponses(events)


def _usage(*, reasoning_tokens: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=10,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )


def _completed() -> Event:
    return Event(
        "response.completed",
        response=SimpleNamespace(id="resp_fake", usage=_usage()),
    )


def _function_schema() -> FunctionSchema:
    return FunctionSchema(
        name="report_answer",
        description="Report answer",
        parameters={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )


def _function_call_events() -> list[Event]:
    return [
        Event(
            "response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_fake123",
                name="report_answer",
            ),
        ),
        Event("response.function_call_arguments.delta", delta='{"answer"'),
        Event("response.function_call_arguments.delta", delta=':"42"}'),
        Event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_fake123",
                name="report_answer",
                arguments='{"answer":"42"}',
            ),
        ),
    ]


def _function_call_delta_only_events() -> list[Event]:
    """Complete tool args arrive only through argument deltas."""
    return [
        Event(
            "response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_delta_only",
                name="report_answer",
            ),
        ),
        Event("response.function_call_arguments.delta", delta='{"answer"'),
        Event("response.function_call_arguments.delta", delta=':"42"}'),
        Event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_delta_only",
                name="report_answer",
            ),
        ),
    ]


def _function_call_done_only_events() -> list[Event]:
    """Complete tool args arrive only on the two terminal done events."""
    return [
        Event(
            "response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_spark",
                name="report_answer",
                arguments="",
            ),
        ),
        Event(
            "response.function_call_arguments.done",
            arguments='{"answer":"42"}',
        ),
        Event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_spark",
                name="report_answer",
                arguments='{"answer":"42"}',
            ),
        ),
    ]


def _function_call_output_item_only_events() -> list[Event]:
    """Complete tool args arrive only on output_item.done.item.arguments."""
    return [
        Event(
            "response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_item_done",
                name="report_answer",
                arguments="",
            ),
        ),
        Event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_item_done",
                name="report_answer",
                arguments='{"answer":"42"}',
            ),
        ),
    ]


def _function_call_arguments_done_only_events() -> list[Event]:
    """The args-done source is the only terminal source carrying arguments."""
    return [
        Event(
            "response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_arguments_done",
                name="report_answer",
                arguments="",
            ),
        ),
        Event(
            "response.function_call_arguments.done",
            arguments='{"answer":"42"}',
        ),
        Event(
            "response.output_item.done",
            # Omit arguments to isolate the top-level args-done fallback.
            item=SimpleNamespace(
                type="function_call",
                call_id="call_arguments_done",
                name="report_answer",
            ),
        ),
    ]


def _function_call_done_only_multi_tool_events() -> list[Event]:
    """Two done-only tools preserve the provider's order and IDs."""
    events: list[Event] = []
    for call_id, name, args in [
        ("call_first", "first_tool", '{"order":1}'),
        ("call_second", "second_tool", '{"order":2}'),
    ]:
        events.extend(
            [
                Event(
                    "response.output_item.added",
                    item=SimpleNamespace(
                        type="function_call",
                        call_id=call_id,
                        name=name,
                        arguments="",
                    ),
                ),
                Event("response.function_call_arguments.done", arguments=args),
                Event(
                    "response.output_item.done",
                    item=SimpleNamespace(
                        type="function_call",
                        call_id=call_id,
                        name=name,
                        arguments=args,
                    ),
                ),
            ]
        )
    return events


def _reasoning_events() -> list[Event]:
    return [
        Event(
            "response.output_item.added",
            item=SimpleNamespace(type="reasoning", id="rs_fake"),
        ),
        Event(
            "response.reasoning_summary_text.delta",
            delta="I should call ",
            item_id="rs_fake",
        ),
        Event(
            "response.reasoning_summary_text.delta",
            delta="the report tool.",
            item_id="rs_fake",
        ),
        Event(
            "response.reasoning_summary_text.done",
            item_id="rs_fake",
            text="I should call the report tool.",
        ),
        Event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="reasoning",
                id="rs_fake",
                summary=[
                    SimpleNamespace(
                        type="summary_text",
                        text="I should call the report tool.",
                    )
                ],
            ),
        ),
    ]


def _create_codex_session(events: list[Event], *, thinking: str = "high"):
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
    )
    adapter._client = FakeClient(events)
    return adapter.create_chat(
        "gpt-5.5",
        "system prompt",
        tools=[_function_schema()],
        force_tool_call=True,
        thinking=thinking,
    )


@pytest.mark.parametrize("thinking", ["none", "minimal", "low", "medium", "high", "xhigh"])
def test_openai_responses_sends_exact_reasoning_effort(thinking):
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    adapter._client = FakeClient([_completed()])
    session = adapter.create_chat("gpt-5.5", "system prompt", thinking=thinking)

    session.send_stream("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert sent["reasoning"] == {"effort": thinking}
    assert "reasoning_effort" not in sent


@pytest.mark.parametrize("thinking", ["none", "minimal", "low", "medium", "high", "xhigh"])
def test_codex_responses_sends_exact_reasoning_effort(thinking):
    session = _create_codex_session([_completed()], thinking=thinking)

    session.send_stream("hello")

    sent = session._client.responses.kwargs[-1]
    assert sent["reasoning"] == {"effort": thinking}
    assert "reasoning_effort" not in sent


@pytest.mark.parametrize("thinking_kwargs", [{}, {"thinking": "default"}, {"thinking": None}])
def test_codex_responses_default_thinking_sends_xhigh(thinking_kwargs):
    """Codex maps an omitted/``default`` thinking level to explicit ``xhigh``."""
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
    )
    adapter._client = FakeClient([_completed()])
    session = adapter.create_chat("gpt-5.5", "system prompt", **thinking_kwargs)

    session.send_stream("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert sent["reasoning"] == {"effort": "xhigh"}
    assert "reasoning_effort" not in sent


@pytest.mark.parametrize("thinking_kwargs", [{}, {"thinking": "default"}])
def test_openai_responses_default_thinking_omits_reasoning(thinking_kwargs):
    """Generic OpenAI keeps omit-on-default; the xhigh default is Codex-only."""
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    adapter._client = FakeClient([_completed()])
    session = adapter.create_chat("gpt-5.5", "system prompt", **thinking_kwargs)

    session.send_stream("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert "reasoning" not in sent


@pytest.mark.parametrize("adapter_cls", [OpenAIAdapter, CodexOpenAIAdapter])
def test_responses_rejects_unsupported_thinking(adapter_cls):
    kwargs = {"api_key": "fake", "use_responses": True}
    if adapter_cls is CodexOpenAIAdapter:
        kwargs.update({"base_url": "http://fake", "force_responses": True})
    adapter = adapter_cls(**kwargs)
    adapter._client = FakeClient([_completed()])

    with pytest.raises(ValueError, match="OpenAI Responses thinking"):
        adapter.create_chat("gpt-5.5", "system prompt", thinking="ultra")


def test_codex_stream_captures_reasoning_and_persists_thinking_block():
    session = _create_codex_session(_reasoning_events() + _function_call_events() + [_completed()])

    result = session.send("please answer via tool")

    assert result.thoughts == ["I should call the report tool."]
    assert result.tool_calls[0].name == "report_answer"

    assistant_entry = session.interface.entries[-1]
    assert isinstance(assistant_entry.content[0], ThinkingBlock)
    assert assistant_entry.content[0].text == "I should call the report tool."
    assert isinstance(assistant_entry.content[1], ToolCallBlock)
    assert not any(
        isinstance(block, TextBlock) and block.text == ""
        for block in assistant_entry.content
    )


def test_codex_replay_includes_reasoning_before_function_call():
    session = _create_codex_session(_reasoning_events() + _function_call_events() + [_completed()])

    session.send("please answer via tool")

    items = to_responses_input(session.interface)
    reasoning_index = next(
        idx for idx, item in enumerate(items) if item.get("type") == "reasoning"
    )
    call_index = next(
        idx for idx, item in enumerate(items) if item.get("type") == "function_call"
    )
    assert reasoning_index < call_index
    assert items[reasoning_index] == {
        "type": "reasoning",
        "summary": [
            {"type": "summary_text", "text": "I should call the report tool."}
        ],
    }


def test_reasoning_done_text_is_not_duplicated_after_delta():
    session = _create_codex_session(_reasoning_events() + [_completed()])

    result = session.send("think only")

    assert result.thoughts == ["I should call the report tool."]
    assistant_entry = session.interface.entries[-1]
    thinking_blocks = [
        block for block in assistant_entry.content if isinstance(block, ThinkingBlock)
    ]
    assert [block.text for block in thinking_blocks] == [
        "I should call the report tool."
    ]


def test_reasoning_done_text_is_used_as_fallback_without_delta():
    session = _create_codex_session(
        [
            Event(
                "response.reasoning_summary_text.done",
                item_id="rs_done_only",
                text="Done-only summary.",
            ),
            _completed(),
        ]
    )

    result = session.send("think only")

    assert result.thoughts == ["Done-only summary."]
    assistant_entry = session.interface.entries[-1]
    assert isinstance(assistant_entry.content[0], ThinkingBlock)
    assert assistant_entry.content[0].text == "Done-only summary."


def test_done_only_summary_is_not_duplicated_by_output_item_done():
    session = _create_codex_session(
        [
            Event(
                "response.reasoning_summary_text.done",
                item_id="rs_done_only",
                text="Done-only summary.",
            ),
            Event(
                "response.output_item.done",
                item=SimpleNamespace(
                    type="reasoning",
                    id="rs_done_only",
                    summary=[
                        SimpleNamespace(
                            type="summary_text",
                            text="Done-only summary.",
                        )
                    ],
                ),
            ),
            _completed(),
        ]
    )

    result = session.send("think only")

    assert result.thoughts == ["Done-only summary."]
    assistant_entry = session.interface.entries[-1]
    thinking_blocks = [
        block for block in assistant_entry.content if isinstance(block, ThinkingBlock)
    ]
    assert [block.text for block in thinking_blocks] == ["Done-only summary."]



def _create_openai_responses_session(events: list[Event]):
    """Create a generic Responses session that must emit a tool call."""
    return OpenAIResponsesSession(
        client=FakeClient(events),
        model="gpt-5.5",
        instructions="system prompt",
        tools=[
            {
                "type": "function",
                "name": "report_answer",
                "description": "Report answer",
                "parameters": _function_schema().parameters,
            }
        ],
        tool_choice="required",
        extra_kwargs={},
    )


# -- Done-only tool arguments -------------------------------------------------


def _assert_done_only_tool_result(result, *, ids, names, args):
    assert [tool.id for tool in result.tool_calls] == ids
    assert [tool.name for tool in result.tool_calls] == names
    assert [tool.args for tool in result.tool_calls] == args


@pytest.mark.parametrize("session_kind", ["generic", "codex"])
def test_done_only_function_call_arguments_are_reconstructed(session_kind):
    """Both Responses loops recover args from terminal done-only fields."""
    events = _function_call_done_only_events() + [_completed()]
    if session_kind == "codex":
        session = _create_codex_session(events)
        result = session.send("please answer via tool")
    else:
        session = _create_openai_responses_session(events)
        result = session.send_stream("please answer via tool")

    _assert_done_only_tool_result(
        result,
        ids=["call_spark"],
        names=["report_answer"],
        args=[{"answer": "42"}],
    )


@pytest.mark.parametrize("session_kind", ["generic", "codex"])
def test_output_item_done_arguments_are_used_without_arguments_done(session_kind):
    """The item.done fallback works symmetrically when args-done is absent."""
    events = _function_call_output_item_only_events() + [_completed()]
    if session_kind == "codex":
        result = _create_codex_session(events).send("please answer via tool")
    else:
        result = _create_openai_responses_session(events).send_stream(
            "please answer via tool"
        )

    _assert_done_only_tool_result(
        result,
        ids=["call_item_done"],
        names=["report_answer"],
        args=[{"answer": "42"}],
    )


@pytest.mark.parametrize("session_kind", ["generic", "codex"])
def test_function_call_arguments_done_is_used_when_final_item_omits_arguments(session_kind):
    """The top-level args-done fallback is not rescued by item.done data."""
    events = _function_call_arguments_done_only_events() + [_completed()]
    if session_kind == "codex":
        result = _create_codex_session(events).send("please answer via tool")
    else:
        result = _create_openai_responses_session(events).send_stream(
            "please answer via tool"
        )

    _assert_done_only_tool_result(
        result,
        ids=["call_arguments_done"],
        names=["report_answer"],
        args=[{"answer": "42"}],
    )


@pytest.mark.parametrize("session_kind", ["generic", "codex"])
def test_delta_only_function_call_arguments_remain_unchanged(session_kind):
    """Existing delta-only assembly remains byte-for-byte the same."""
    events = _function_call_delta_only_events() + [_completed()]
    if session_kind == "codex":
        result = _create_codex_session(events).send("please answer via tool")
    else:
        result = _create_openai_responses_session(events).send_stream(
            "please answer via tool"
        )

    _assert_done_only_tool_result(
        result,
        ids=["call_delta_only"],
        names=["report_answer"],
        args=[{"answer": "42"}],
    )


@pytest.mark.parametrize("session_kind", ["generic", "codex"])
def test_delta_plus_done_does_not_duplicate_or_clobber_arguments(session_kind):
    """A non-empty delta buffer wins over complete or malformed terminal data."""
    events = _function_call_events() + [_completed()]
    # Replace the final item with malformed terminal JSON to prove the fallback
    # never overwrites the already-complete delta buffer.
    events[-2] = Event(
        "response.output_item.done",
        item=SimpleNamespace(
            type="function_call",
            call_id="call_fake123",
            name="report_answer",
            arguments="{malformed",
        ),
    )
    if session_kind == "codex":
        result = _create_codex_session(events).send("please answer via tool")
    else:
        result = _create_openai_responses_session(events).send_stream(
            "please answer via tool"
        )

    _assert_done_only_tool_result(
        result,
        ids=["call_fake123"],
        names=["report_answer"],
        args=[{"answer": "42"}],
    )


@pytest.mark.parametrize("session_kind", ["generic", "codex"])
def test_done_only_multiple_tools_preserve_order_and_ids(session_kind):
    """Sequential done-only tools retain provider order, names, IDs, and args."""
    events = _function_call_done_only_multi_tool_events() + [_completed()]
    if session_kind == "codex":
        result = _create_codex_session(events).send("please answer via tool")
    else:
        result = _create_openai_responses_session(events).send_stream(
            "please answer via tool"
        )

    _assert_done_only_tool_result(
        result,
        ids=["call_first", "call_second"],
        names=["first_tool", "second_tool"],
        args=[{"order": 1}, {"order": 2}],
    )


def test_codex_responses_trace_disabled_by_default(tmp_path, monkeypatch):
    trace_path = tmp_path / "codex_responses_trace.jsonl"
    monkeypatch.delenv("LINGTAI_CODEX_RESPONSES_TRACE", raising=False)
    monkeypatch.setenv("LINGTAI_CODEX_RESPONSES_TRACE_PATH", str(trace_path))
    session = _create_codex_session(_reasoning_events() + _function_call_events() + [_completed()])

    result = session.send("please answer via tool")

    assert result.thoughts == ["I should call the report tool."]
    assert result.tool_calls[0].name == "report_answer"
    assert not trace_path.exists()


def test_codex_responses_trace_records_safe_metadata_when_enabled(tmp_path, monkeypatch):
    trace_path = tmp_path / "codex_responses_trace.jsonl"
    monkeypatch.setenv("LINGTAI_CODEX_RESPONSES_TRACE", "1")
    monkeypatch.setenv("LINGTAI_CODEX_RESPONSES_TRACE_PATH", str(trace_path))
    session = _create_codex_session(_reasoning_events() + _function_call_events() + [_completed()])

    result = session.send("please answer via tool")

    assert result.thoughts == ["I should call the report tool."]
    assert result.text == ""
    assert result.tool_calls[0].name == "report_answer"
    assistant_entry = session.interface.entries[-1]
    assert isinstance(assistant_entry.content[0], ThinkingBlock)
    assert isinstance(assistant_entry.content[1], ToolCallBlock)

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    event_types = [record["event_type"] for record in records]
    assert "response.reasoning_summary_text.delta" in event_types
    assert "response.output_item.added" in event_types
    assert "response.function_call_arguments.delta" in event_types
    assert "response.completed" in event_types

    reasoning_delta = next(
        record
        for record in records
        if record["event_type"] == "response.reasoning_summary_text.delta"
    )
    assert reasoning_delta["accepted_reasoning"] is True
    assert reasoning_delta["item_id"] == "rs_fake"
    assert reasoning_delta["delta"]["length"] == len("I should call ")
    assert "sha256_12" in reasoning_delta["delta"]
    assert "I should call" not in json.dumps(reasoning_delta)

    function_arg_delta = next(
        record
        for record in records
        if record["event_type"] == "response.function_call_arguments.delta"
    )
    assert function_arg_delta["accepted_reasoning"] is False
    assert function_arg_delta["delta"]["length"] == len('{"answer"')
    assert "answer" not in json.dumps(function_arg_delta)

    completed = next(
        record for record in records if record["event_type"] == "response.completed"
    )
    assert completed["usage"] == {
        "input_tokens": 10,
        "output_tokens": 20,
        "cached_tokens": 0,
        "reasoning_tokens": 7,
    }
    assert completed["thoughts"]["after_count"] == 1


def test_openai_responses_stream_captures_summary_thoughts():
    session = OpenAIResponsesSession(
        client=FakeClient(_reasoning_events() + [_completed()]),
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )

    result = session.send_stream("think")

    assert result.thoughts == ["I should call the report tool."]
