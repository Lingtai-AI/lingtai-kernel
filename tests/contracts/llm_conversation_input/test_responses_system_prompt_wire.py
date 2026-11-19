"""The Responses wire carries the system prompt as the leading developer message.

Models starting with GPT-6.2 append top-level ``instructions`` after ``input``
and never serve them from the prompt cache, so a system prompt sent there is
billed uncached on every request. Every Responses request therefore puts the
system prompt in a ``developer`` message at the start of ``input`` and keeps
only a short constant in ``instructions`` — on the plain ``openai`` route and
on native ``codex`` alike, for session turns and one-shot generation.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.llm.openai.adapter import (
    _RESPONSES_INSTRUCTIONS_POINTER,
    OpenAIAdapter,
    _responses_wire_request,
    _system_prompt_input_item,
)

from tests.contracts.llm_conversation_input import regimes


def _developer(text: str) -> dict:
    return {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": text}],
    }


def test_wire_request_moves_the_system_prompt_into_the_first_input_item() -> None:
    request = {
        "model": "any-model",
        "instructions": "SYSTEM PROMPT",
        "input": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "name": "t"}],
    }
    snapshot = deepcopy(request)

    wire = _responses_wire_request(request)

    assert wire["instructions"] == _RESPONSES_INSTRUCTIONS_POINTER
    assert wire["input"] == [_developer("SYSTEM PROMPT"), {"role": "user", "content": "hi"}]
    assert _system_prompt_input_item("SYSTEM PROMPT") == _developer("SYSTEM PROMPT")
    # Everything else rides unchanged, and the session's own request is not mutated.
    assert {k: v for k, v in wire.items() if k not in ("instructions", "input")} == {
        k: v for k, v in request.items() if k not in ("instructions", "input")
    }
    assert request == snapshot


def test_wire_request_is_identical_for_identical_requests() -> None:
    """A stable prefix is the point: same request in, byte-identical wire out."""
    request = {"model": "m", "instructions": "S", "input": [{"role": "user", "content": "a"}]}
    assert _responses_wire_request(request) == _responses_wire_request(deepcopy(request))


def test_wire_request_leaves_a_server_side_continuation_delta_alone() -> None:
    """With ``previous_response_id`` the developer message is already in the chain."""
    delta = [{"type": "function_call_output", "call_id": "c1", "output": "ok"}]
    request = {"instructions": "S", "input": delta, "previous_response_id": "resp_1"}

    wire = _responses_wire_request(request)

    assert wire["input"] == delta
    assert wire["instructions"] == _RESPONSES_INSTRUCTIONS_POINTER


@pytest.mark.parametrize("instructions", [None, ""])
def test_wire_request_without_a_system_prompt_is_unchanged(instructions) -> None:
    request = {"model": "m", "input": [{"role": "user", "content": "a"}]}
    if instructions is not None:
        request["instructions"] = instructions
    assert _responses_wire_request(request) is request


def test_wire_request_wraps_plain_text_input() -> None:
    wire = _responses_wire_request({"instructions": "S", "input": "hello"})
    assert wire["input"] == [_developer("S"), {"role": "user", "content": "hello"}]


def _session_requests(regime_name: str):
    regime = next(r for r in regimes.ALL_REGIMES if r.name == regime_name)
    session, transport = regime.build()
    if regime_name == "codex_responses":
        return session, lambda: transport.responses.kwargs
    return session, lambda: [c.kwargs for c in transport.responses.create.call_args_list]


@pytest.mark.parametrize("regime_name", ["openai_responses", "codex_responses"])
def test_every_session_turn_leads_with_the_system_prompt(regime_name: str) -> None:
    session, requests = _session_requests(regime_name)

    session.send("first")
    session.send("second")

    sent = requests()
    assert len(sent) == 2
    for request in sent:
        assert request["instructions"] == _RESPONSES_INSTRUCTIONS_POINTER
        assert request["input"][0] == _developer("system")
        assert sum(1 for item in request["input"] if item.get("role") == "developer") == 1
        assert "previous_response_id" not in request
    # Turn 2 extends turn 1: the cached prefix starts with the system prompt.
    first, second = sent[0]["input"], sent[1]["input"]
    assert second[: len(first)] == first
    # The session keeps its own logical state: the prompt, not the pointer.
    assert session._instructions == "system"


def test_updated_system_prompt_replaces_the_leading_message() -> None:
    session, requests = _session_requests("openai_responses")

    session.send("first")
    session.update_system_prompt("system v2")
    session.send("second")

    assert requests()[0]["input"][0] == _developer("system")
    assert requests()[1]["input"][0] == _developer("system v2")
    assert requests()[1]["instructions"] == _RESPONSES_INSTRUCTIONS_POINTER


def test_one_shot_generation_uses_the_same_wire_shape(monkeypatch) -> None:
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(
        id="resp_1",
        output=[],
        output_text="ok",
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )
    monkeypatch.setattr("lingtai.llm.openai.adapter.openai.OpenAI", lambda **kw: client)
    adapter = OpenAIAdapter(
        api_key="inert", base_url="https://adapter.invalid", wire_api="responses",
    )

    adapter._generate_responses("any-model", "question", system_prompt="SYSTEM PROMPT")

    request = client.responses.create.call_args.kwargs
    assert request["instructions"] == _RESPONSES_INSTRUCTIONS_POINTER
    assert request["input"] == [
        _developer("SYSTEM PROMPT"),
        {"role": "user", "content": "question"},
    ]
