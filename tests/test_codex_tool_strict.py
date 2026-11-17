"""Unit/lifecycle regression: native Codex tool wire policy, without provider I/O."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.llm.openai.adapter import CodexOpenAIAdapter, OpenAIAdapter


def _schemas(prefix):
    parameters = {
        "type": "object",
        "properties": {"action": {"type": "string"}, "value": {"type": "string"}},
        "allOf": [{"if": {"properties": {"action": {"const": "set"}}},
                   "then": {"required": ["value"]}}],
    }
    return [FunctionSchema(prefix + name, "description", deepcopy(parameters))
            for name in ("one", "two")]


class _Responses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        return iter([
            SimpleNamespace(type="response.output_text.delta", delta="ok"),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(
                id=f"response_{len(self.calls)}", usage=None)),
        ])


@pytest.mark.parametrize("model", ["gpt-6.1-astra", "gpt-6.2-astra", "gpt-6.2-sol", "arbitrary-model"])
def test_codex_created_session_initial_and_updated_tool_payloads(monkeypatch, model):
    monkeypatch.setenv("LINGTAI_CODEX_TRANSPORT", "rest")
    monkeypatch.delenv("LINGTAI_CODEX_WS", raising=False)
    responses = _Responses()
    client = SimpleNamespace(responses=responses, api_key="inert")
    monkeypatch.setattr("lingtai.llm.openai.adapter.openai.OpenAI", lambda **kwargs: client)
    adapter = CodexOpenAIAdapter(api_key="inert", base_url="https://codex.invalid", wire_api="responses")
    original = _schemas("initial_")
    snapshot = deepcopy(original)
    session = adapter.create_chat(model, "system", tools=original)
    session.send("hello")
    replacement = _schemas("updated_")
    replacement_snapshot = deepcopy(replacement)
    session.update_tools(replacement)
    session.send("again")
    for payload, schemas in zip(responses.calls, (original, replacement)):
        assert len(payload["tools"]) == 2
        for tool, schema in zip(payload["tools"], schemas):
            assert tool.get("strict") is False
            assert tool["name"] == schema.name
            assert tool["parameters"] == schema.parameters
        # The actual WebSocket frame serializer consumes the same tool payload.
        assert session._ws_frame_request(payload, payload["input"])["tools"] == payload["tools"]
    assert original == snapshot
    assert replacement == replacement_snapshot
    session.update_tools([])
    session.send("without tools")
    assert "tools" not in responses.calls[-1]
    session.update_tools(original)
    assert all(tool.get("strict") is False for tool in session._tools)
    session.update_tools(None)
    assert session._tools is None


@pytest.mark.parametrize("wire_api", ["responses", "chat_completions"])
def test_generic_openai_initial_and_updated_tools_keep_strict_omitted(monkeypatch, wire_api):
    monkeypatch.setattr("lingtai.llm.openai.adapter.openai.OpenAI", lambda **kwargs: SimpleNamespace())
    adapter = OpenAIAdapter(api_key="inert", base_url="https://openai.invalid", wire_api=wire_api)
    schemas = _schemas("generic_")
    snapshot = deepcopy(schemas)
    session = adapter.create_chat("arbitrary-model", "system", tools=schemas)
    for tool, schema in zip(session._tools, schemas):
        function = tool.get("function", tool)
        assert "strict" not in function
        assert function["parameters"] == schema.parameters
    updated = _schemas("updated_")
    session.update_tools(updated)
    assert len(session._tools) == 2
    for tool, schema in zip(session._tools, updated):
        function = tool.get("function", tool)
        assert "strict" not in function
        assert function["name"] == schema.name
        assert function["parameters"] == schema.parameters
    assert schemas == snapshot
