from __future__ import annotations

from types import SimpleNamespace

import pytest

from lingtai_kernel.config import AgentConfig
from lingtai_kernel.llm import LLMResponse
from lingtai_kernel.llm.base import UsageMetadata
from lingtai_kernel.llm.interface import ChatInterface, TextBlock, ToolCallBlock, ToolResultBlock
from lingtai_kernel.session import SessionManager


@pytest.fixture(autouse=True)
def deterministic_tokens(monkeypatch):
    def count(value):
        return len(str(value or ""))

    import lingtai_kernel.token_counter as token_counter
    import lingtai_kernel.session as session_mod

    monkeypatch.setattr(token_counter, "count_tokens", count)
    monkeypatch.setattr(token_counter, "count_tool_tokens", lambda schemas: 0)
    monkeypatch.setattr(session_mod, "count_tokens", count)
    monkeypatch.setattr(session_mod, "count_tool_tokens", lambda schemas: 0)


class FakeChat:
    def __init__(self, *, context_window=100):
        self.interface = ChatInterface()
        self.context_window_value = context_window
        self.sent_messages = []
        self.interaction_id = None

    def context_window(self):
        return self.context_window_value

    def update_system_prompt(self, system_prompt):
        self.interface.add_system(system_prompt, None)

    def update_system_prompt_batches(self, batches):
        self.interface.add_system("".join(batches), None)

    def update_tools(self, tools):
        self.interface._current_tools = tools

    def send(self, message):
        self.sent_messages.append(message)
        if isinstance(message, str):
            self.interface.add_user_message(message)
        elif isinstance(message, list):
            self.interface.add_tool_results(message)
        self.interface.add_assistant_message([TextBlock(text="ok")])
        return LLMResponse(text="ok", usage=UsageMetadata(input_tokens=1, output_tokens=1))

    def send_stream(self, message, on_chunk=None):
        response = self.send(message)
        if on_chunk:
            on_chunk(response.text)
        return response

    def commit_tool_results(self, tool_results):
        self.interface.add_tool_results(tool_results)


class FakeService:
    model = "fake-model"

    def create_session(self, **kwargs):
        chat = FakeChat()
        if kwargs.get("interface") is not None:
            chat.interface = kwargs["interface"]
        return chat


def make_manager(
    *,
    context_limit=100,
    hard_pressure=0.98,
    chat=None,
    compact=None,
    forget=None,
    save=None,
    streaming=False,
):
    events = []
    manager = SessionManager(
        llm_service=FakeService(),
        config=AgentConfig(
            context_limit=context_limit,
            context_hard_pressure=hard_pressure,
            model="fake-model",
        ),
        agent_name="tester",
        streaming=streaming,
        build_system_prompt_fn=lambda: "",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event_type, **fields: events.append((event_type, fields)),
        compact_history_fn=compact,
        force_context_forget_fn=forget,
        save_local_state_fn=save,
    )
    manager.chat = chat or FakeChat()
    return manager, manager.chat, events


def event_names(events):
    return [name for name, _ in events]


def test_pre_send_gate_allows_under_threshold_and_logs_llm_call():
    manager, chat, events = make_manager(context_limit=100, hard_pressure=0.9)

    response = manager.send("hello")

    assert response.text == "ok"
    assert chat.sent_messages == ["hello"]
    assert "llm_call" in event_names(events)
    assert not any(
        name == "context_hard_gate" and fields.get("action") == "blocked"
        for name, fields in events
    )


def test_pre_send_gate_blocks_before_provider_and_before_llm_call():
    manager, chat, events = make_manager(context_limit=10, hard_pressure=0.5)

    response = manager.send("x" * 20)

    assert chat.sent_messages == []
    assert "llm_call" not in event_names(events)
    assert response.raw["context_hard_gate"]["action"] == "blocked"
    assert response.usage is None
    assert any(
        name == "context_hard_gate" and fields.get("action") == "compact_attempt"
        for name, fields in events
    )
    assert any(
        name == "context_hard_gate" and fields.get("action") == "blocked"
        for name, fields in events
    )


def test_pre_send_gate_counts_none_for_wire_drive_without_pending_append():
    chat = FakeChat()
    chat.interface.add_user_message("x" * 20)
    manager, chat, events = make_manager(context_limit=10, hard_pressure=0.5, chat=chat)

    response = manager.send(None)

    assert chat.sent_messages == []
    assert "llm_call" not in event_names(events)
    assert response.raw["context_hard_gate"]["message_kind"] == "none"


def test_pre_send_gate_counts_pending_tool_results_and_commits_them_on_block():
    chat = FakeChat()
    call = ToolCallBlock(id="call-1", name="bash", args={"action": "run"})
    chat.interface.add_assistant_message([call])
    result = ToolResultBlock(id="call-1", name="bash", content="x" * 50)
    manager, chat, events = make_manager(context_limit=20, hard_pressure=0.5, chat=chat)

    response = manager.send([result])

    assert chat.sent_messages == []
    assert response.raw["context_hard_gate"]["message_kind"] == "tool_results"
    assert "llm_call" not in event_names(events)
    # The real tool result is preserved before the local terminal note, so a
    # later turn will not re-run the side-effecting tool or synthesize a fake result.
    assert manager.chat is not chat
    assert manager.chat.interface is chat.interface
    assert manager.chat.interface.entries[-2].role == "user"
    assert manager.chat.interface.entries[-2].content[0] is result
    assert manager.chat.interface.entries[-1].role == "assistant"


def test_pre_send_gate_compacts_once_then_allows_provider_call():
    chat = FakeChat()
    call = ToolCallBlock(id="call-1", name="bash", args={})
    result = ToolResultBlock(id="call-1", name="bash", content="x" * 40)
    chat.interface.add_assistant_message([call])
    chat.interface.add_tool_results([result])
    chat.interface.add_assistant_message([TextBlock(text="done")])

    def compact(*, source):
        assert source == "hard_ceiling"
        result.content = "ok"
        return SimpleNamespace(compacted_blocks=1, to_log_fields=lambda: {})

    manager, chat, events = make_manager(
        context_limit=20,
        hard_pressure=0.8,
        chat=chat,
        compact=compact,
    )

    response = manager.send("go")

    assert response.text == "ok"
    assert chat.sent_messages == ["go"]
    assert any(
        name == "context_hard_gate" and fields.get("action") == "allow_after_compaction"
        for name, fields in events
    )
    assert "llm_call" in event_names(events)


def test_pre_send_gate_forces_context_forget_for_replayable_text_then_allows():
    chat = FakeChat()
    chat.interface.add_user_message("x" * 50)
    manager, chat, events = make_manager(context_limit=40, hard_pressure=0.5, chat=chat)

    def forget(*, source):
        assert source == "hard_ceiling"
        manager.chat = FakeChat()
        return {"status": "ok", "molt_count": 3}

    manager._force_context_forget_fn = forget

    response = manager.send("go")

    assert response.text == "ok"
    assert manager.chat.sent_messages == ["go"]
    assert any(
        name == "context_hard_gate" and fields.get("action") == "forced_context_forget"
        for name, fields in events
    )
    assert any(
        name == "context_hard_gate" and fields.get("action") == "allow_after_forced_context_forget"
        for name, fields in events
    )


def test_streaming_path_uses_same_pre_send_gate_before_provider_dispatch():
    manager, chat, events = make_manager(
        context_limit=10,
        hard_pressure=0.5,
        streaming=True,
    )

    response = manager.send("x" * 20)

    assert chat.sent_messages == []
    assert response.raw["context_hard_gate"]["action"] == "blocked"
    assert "llm_call" not in event_names(events)


def test_pre_send_gate_defaults_invalid_runtime_threshold_and_logs():
    manager, chat, events = make_manager(context_limit=100, hard_pressure=float("nan"))

    response = manager.send("hello")

    assert response.text == "ok"
    assert chat.sent_messages == ["hello"]
    assert any(
        name == "context_hard_gate" and fields.get("action") == "invalid_threshold_defaulted"
        for name, fields in events
    )


def test_pre_send_gate_local_block_rebuilds_from_canonical_and_saves_state():
    saves = []
    manager, chat, events = make_manager(
        context_limit=10,
        hard_pressure=0.5,
        save=lambda **kwargs: saves.append(kwargs),
    )

    response = manager.send("x" * 20)

    assert response.raw["context_hard_gate"]["action"] == "blocked"
    assert manager.chat is not chat
    assert manager.chat.interface is chat.interface
    assert manager.chat.interface.entries[-1].role == "assistant"
    assert "hard context ceiling" in manager.chat.interface.entries[-1].content[0].text
    assert saves == [{"ledger_source": "context_hard_gate"}]
    assert any(
        name == "context_hard_gate" and fields.get("action") == "blocked"
        for name, fields in events
    )


def test_pre_send_gate_does_not_force_context_forget_for_tool_results():
    chat = FakeChat()
    call = ToolCallBlock(id="call-1", name="bash", args={})
    chat.interface.add_assistant_message([call])
    result = ToolResultBlock(id="call-1", name="bash", content="x" * 50)
    forget_calls = []
    manager, chat, events = make_manager(
        context_limit=20,
        hard_pressure=0.5,
        chat=chat,
        forget=lambda **kwargs: forget_calls.append(kwargs),
    )

    response = manager.send([result])

    assert response.raw["context_hard_gate"]["action"] == "blocked"
    assert forget_calls == []
    assert not any(
        name == "context_hard_gate" and fields.get("action") == "forced_context_forget"
        for name, fields in events
    )
