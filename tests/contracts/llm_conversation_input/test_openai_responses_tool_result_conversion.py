"""Red/green regression for the OpenAI Responses tool-result conversion fix.

Defect (pre-fix): ``OpenAIResponsesSession._convert_input`` recognized only
already-wire dicts. A canonical ``ToolResultBlock`` — the shape
``LLMService.make_tool_result`` -> ``adapter.make_tool_result_message`` actually
produces and ``SessionManager.send`` hands down — fell through the ``else``
branch and was forwarded to ``responses.create(input=[...])`` *unconverted*
(``openai/adapter.py:1798-1799`` before the fix). This is a live defect for a
non-Codex ``openai`` provider on the Responses wire (``wire_api=responses`` /
no base_url + ``use_responses`` / ``force_responses``), because that session
commits nothing to the canonical interface, so no replay path rescues it.

Fix: ``_convert_input`` now converts a ``ToolResultBlock`` to the
``function_call_output`` wire item using the same mapping as
``interface_converters.to_responses_input`` — without disturbing the existing
already-wire dict branches.

This module asserts both directions: the canonical block converts (the fix),
and pre-built wire dicts still pass through untouched (no regression).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from lingtai.kernel.llm.interface import ChatInterface, ToolResultBlock
from lingtai.llm.openai.adapter import OpenAIResponsesSession


def _responses_raw():
    text_block = SimpleNamespace(type="output_text", text="ok")
    message = SimpleNamespace(type="message", content=[text_block])
    return SimpleNamespace(
        id="resp_1",
        output=[message],
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


def _make_session():
    client = MagicMock()
    client.responses.create.return_value = _responses_raw()
    session = OpenAIResponsesSession(
        client=client,
        model="gpt-5.5",
        instructions="system",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )
    return session, client


def _sent_input(client) -> list:
    return client.responses.create.call_args.kwargs["input"]


def test_canonical_tool_result_becomes_function_call_output() -> None:
    """The fix: a canonical ``ToolResultBlock`` serializes to the wire shape."""
    session, client = _make_session()
    block = ToolResultBlock(
        id="call_abc123", name="bash", content={"stdout": "hi", "exit_code": 0}
    )

    session.send([block])

    sent = _sent_input(client)
    assert sent == [
        {
            "type": "function_call_output",
            "call_id": "call_abc123",
            "output": '{"stdout": "hi", "exit_code": 0}',
        }
    ]
    # And no unconverted dataclass leaked onto the wire.
    assert all(isinstance(item, dict) for item in sent)


def test_string_tool_result_content_is_passed_through_verbatim() -> None:
    """A string ``content`` is emitted as ``output`` verbatim (no re-encoding)."""
    session, client = _make_session()
    block = ToolResultBlock(id="call_x", name="echo", content="plain text")

    session.send([block])

    assert _sent_input(client) == [
        {"type": "function_call_output", "call_id": "call_x", "output": "plain text"}
    ]


def test_prebuilt_function_call_output_dict_passes_through_unchanged() -> None:
    """No regression: an already-wire ``function_call_output`` dict is untouched."""
    session, client = _make_session()
    wire_item = {
        "type": "function_call_output",
        "call_id": "call_pre",
        "output": "already wire",
    }

    session.send([wire_item])

    assert _sent_input(client) == [wire_item]


def test_legacy_role_tool_dict_still_converts() -> None:
    """No regression: the legacy Chat-Completions ``role=tool`` dict still maps."""
    session, client = _make_session()

    session.send([{"role": "tool", "tool_call_id": "call_legacy", "content": "out"}])

    assert _sent_input(client) == [
        {
            "type": "function_call_output",
            "call_id": "call_legacy",
            "output": "out",
        }
    ]


def test_str_message_unaffected() -> None:
    """No regression: a plain string turn still becomes a user input item."""
    session, client = _make_session()

    session.send("hello")

    assert _sent_input(client) == [{"role": "user", "content": "hello"}]
