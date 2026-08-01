import json
from types import SimpleNamespace

import pytest

from lingtai.init_schema import validate_init
from lingtai.kernel.presets import load_preset
from lingtai.llm.custom.adapter import create_custom_adapter
from lingtai.llm.openai.adapter import (
    OpenAIAdapter,
    OpenAIResponsesSession,
    ResponsesWebSocketConfigurationError,
    ResponsesWebSocketRequestError,
    ResponsesWebSocketUnsupportedError,
)
from lingtai.llm.service import build_provider_defaults_from_manifest_llm
from lingtai.llm.service import LLMService


def event(event_type, **values):
    return SimpleNamespace(type=event_type, **values)


def completed(response_id):
    return event(
        "response.completed",
        response=SimpleNamespace(id=response_id, usage=None),
    )


class FakeConnection:
    def __init__(self, events=(), send_error=None):
        self.events = list(events)
        self.frames = []
        self.closed = False
        self.send_error = send_error

    def send_raw(self, payload):
        if self.send_error is not None:
            error = self.send_error
            self.send_error = None
            raise error
        self.frames.append(json.loads(payload))

    def recv(self):
        if not self.events:
            raise ConnectionError("test connection closed")
        next_event = self.events.pop(0)
        if isinstance(next_event, BaseException):
            raise next_event
        return next_event

    def close(self):
        self.closed = True


class FakeManager:
    def __init__(self, connection=None, error=None):
        self.connection = connection
        self.error = error

    def enter(self):
        if self.error:
            raise self.error
        return self.connection


class FakeHandshakeError(RuntimeError):
    def __init__(self, status):
        super().__init__(f"handshake status {status}")
        self.response = SimpleNamespace(status_code=status)


class FakeResponses:
    def __init__(self, manager=None, http_response=None):
        self.manager = manager
        self.http_response = http_response
        self.connect_calls = []
        self.create_calls = []

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        if isinstance(self.manager, list):
            return self.manager.pop(0)
        return self.manager

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.http_response


class FakeClient:
    def __init__(self, responses):
        self.responses = responses


def make_session(responses, *, websocket=True):
    return OpenAIResponsesSession(
        client=FakeClient(responses),
        model="test-model",
        instructions="system",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        compact_threshold=None,
        prompt_cache_key=None,
        stateless_replay=True,
        websocket_v2=websocket,
        upstream_base_url="https://gateway.example.test/v1",
    )


def test_http_transport_never_connects():
    raw = SimpleNamespace(
        id="resp-http",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="HTTP")],
            )
        ],
        usage=None,
    )
    responses = FakeResponses(http_response=raw)

    result = make_session(responses, websocket=False).send("hello")

    assert result.text == "HTTP"
    assert len(responses.create_calls) == 1
    assert responses.connect_calls == []


def test_websocket_reuses_connection_and_sends_delta_continuation():
    connection = FakeConnection(
        [
            event("response.output_text.delta", delta="one"),
            completed("resp-1"),
            event("response.output_text.delta", delta="two"),
            completed("resp-2"),
        ]
    )
    responses = FakeResponses(manager=FakeManager(connection))
    session = make_session(responses)

    assert session.send("first").text == "one"
    assert session.send("second").text == "two"

    assert len(responses.connect_calls) == 1
    connect_call = responses.connect_calls[0]
    assert connect_call["extra_headers"] == {
        "OpenAI-Beta": "responses_websockets=2026-02-06"
    }
    assert connect_call["websocket_connection_options"] == {"ping_interval": None}
    assert responses.create_calls == []
    assert connection.frames[0]["type"] == "response.create"
    assert "previous_response_id" not in connection.frames[0]
    assert connection.frames[1]["previous_response_id"] == "resp-1"
    assert len(connection.frames[1]["input"]) == 1
    assert connection.frames[1]["input"][0]["role"] == "user"


def test_websocket_non_prefix_history_starts_a_fresh_full_chain():
    connection = FakeConnection(
        [
            event("response.output_text.delta", delta="one"),
            completed("resp-1"),
            event("response.output_text.delta", delta="two"),
            completed("resp-2"),
        ]
    )
    session = make_session(FakeResponses(manager=FakeManager(connection)))
    session.send("first")

    # Simulate compaction/pruning that changes the already-synced prefix.
    session.interface._entries.pop(0)
    session.send("second")

    assert "previous_response_id" not in connection.frames[1]
    assert len(connection.frames[1]["input"]) > 1


def test_websocket_sync_state_is_published_atomically(monkeypatch):
    session = make_session(FakeResponses(manager=FakeManager(FakeConnection())))
    session._response_id = "resp-old"
    session._remote_prefix = [{"role": "user", "content": "old"}]
    session._remote_entry_count = 1

    def fail_snapshot():
        raise ValueError("canonical snapshot failed")

    monkeypatch.setattr(session.interface, "to_dict", fail_snapshot)

    with pytest.raises(ValueError, match="canonical snapshot failed"):
        session._mark_websocket_synced("resp-new")

    assert session._response_id == "resp-old"
    assert session._remote_prefix == [{"role": "user", "content": "old"}]
    assert session._remote_entry_count == 1


def test_missing_previous_response_rebuilds_full_chain_once():
    first = FakeConnection(
        [
            event("response.output_text.delta", delta="one"),
            completed("resp-1"),
            event(
                "error",
                error=SimpleNamespace(
                    message="previous_response_id not found",
                    code="response_not_found",
                ),
            ),
        ]
    )
    recovered = FakeConnection(
        [event("response.output_text.delta", delta="recovered"), completed("resp-2")]
    )
    responses = FakeResponses(
        manager=[FakeManager(first), FakeManager(recovered)]
    )
    session = make_session(responses)

    session.send("first")
    assert session.send("second").text == "recovered"

    assert first.frames[1]["previous_response_id"] == "resp-1"
    assert "previous_response_id" not in recovered.frames[0]
    assert len(recovered.frames[0]["input"]) > 1
    assert len(responses.connect_calls) == 2


@pytest.mark.parametrize("status", [400, 403, 404, 405, 406, 410, 426, 501])
def test_handshake_rejection_is_explicit_and_never_falls_back(status):
    responses = FakeResponses(manager=FakeManager(error=FakeHandshakeError(status)))

    with pytest.raises(ResponsesWebSocketUnsupportedError) as raised:
        make_session(responses).send("hello")

    message = str(raised.value)
    assert "gateway.example.test" in message
    assert "does not support Responses WebSocket v2" in message
    assert "responses_transport" in message
    assert responses.create_calls == []
    assert len(responses.connect_calls) == 1


def test_auth_handshake_failure_is_not_misreported_as_unsupported():
    responses = FakeResponses(
        manager=[
            FakeManager(error=FakeHandshakeError(401)),
            FakeManager(error=FakeHandshakeError(401)),
        ]
    )

    with pytest.raises(ResponsesWebSocketRequestError) as raised:
        make_session(responses).send("hello")

    assert "does not support" not in str(raised.value)
    assert "credentials" in str(raised.value)
    assert responses.create_calls == []


def test_transient_connect_failure_retries_once_without_http_fallback():
    responses = FakeResponses(
        manager=[
            FakeManager(error=TimeoutError("timed out")),
            FakeManager(error=TimeoutError("timed out again")),
        ]
    )

    with pytest.raises(ResponsesWebSocketRequestError) as raised:
        make_session(responses).send("hello")

    assert "does not support" not in str(raised.value)
    assert "check the network" in str(raised.value)
    assert len(responses.connect_calls) == 2
    assert responses.create_calls == []


def test_first_turn_transient_drop_reconnects_once():
    dropped = FakeConnection(send_error=ConnectionError("node dropped"))
    working = FakeConnection(
        [event("response.output_text.delta", delta="OK"), completed("resp-1")]
    )
    responses = FakeResponses(
        manager=[FakeManager(dropped), FakeManager(working)]
    )

    result = make_session(responses).send("hello")

    assert result.text == "OK"
    assert len(responses.connect_calls) == 2
    assert dropped.closed
    assert len(working.frames) == 1


def test_established_continuation_failure_is_not_automatically_resent():
    connection = FakeConnection(
        [event("response.output_text.delta", delta="one"), completed("resp-1")]
    )
    responses = FakeResponses(manager=FakeManager(connection))
    session = make_session(responses)
    session.send("first")
    connection.send_error = ConnectionError("node dropped")

    with pytest.raises(ResponsesWebSocketRequestError):
        session.send("second")

    assert len(responses.connect_calls) == 1
    assert len(connection.frames) == 1
    assert responses.create_calls == []


def test_partial_stream_failure_is_marked_terminal():
    connection = FakeConnection(
        [event("response.output_text.delta", delta="visible"), ConnectionError("drop")]
    )
    chunks = []

    with pytest.raises(ResponsesWebSocketRequestError) as raised:
        make_session(FakeResponses(manager=FakeManager(connection))).send_stream(
            "hello", chunks.append
        )

    assert chunks == ["visible"]
    assert getattr(raised.value, "_lingtai_partial_stream", False) is True


def test_reset_closes_connection_and_rebuilds_chain_on_next_request():
    first = FakeConnection(
        [event("response.output_text.delta", delta="one"), completed("resp-1")]
    )
    second = FakeConnection(
        [event("response.output_text.delta", delta="two"), completed("resp-2")]
    )
    responses = FakeResponses(manager=[FakeManager(first), FakeManager(second)])
    session = make_session(responses)
    session.send("first")

    session.reset()
    assert first.closed
    session.send("second")

    assert len(responses.connect_calls) == 2
    assert "previous_response_id" not in second.frames[0]
    assert len(second.frames[0]["input"]) > 1


def test_local_sdk_without_connect_reports_configuration_not_upstream():
    responses = SimpleNamespace(create=lambda **kwargs: None)

    with pytest.raises(ResponsesWebSocketConfigurationError) as raised:
        make_session(responses).send("hello")

    assert "openai[realtime]>=2.22.0" in str(raised.value)
    assert "upstream" not in str(raised.value).lower()


def test_adapter_rejects_websocket_without_explicit_responses():
    with pytest.raises(ValueError, match="requires wire_api='responses'"):
        OpenAIAdapter(
            api_key="not-a-real-key",
            base_url="https://gateway.example.test/v1",
            responses_stateless_replay=True,
            responses_transport="websocket",
        )


def test_adapter_rejects_websocket_without_stateless_replay():
    with pytest.raises(ValueError, match="requires stateless replay"):
        OpenAIAdapter(
            api_key="not-a-real-key",
            base_url="https://gateway.example.test/v1",
            wire_api="responses",
            responses_transport="websocket",
        )


def test_custom_factory_receives_transport():
    adapter = create_custom_adapter(
        api_key="not-a-real-key",
        api_compat="openai",
        base_url="https://gateway.example.test/v1",
        wire_api="responses",
        responses_transport="websocket",
    )

    assert adapter._wire_api == "responses"
    assert adapter._responses_transport == "websocket"


@pytest.mark.parametrize("api_compat", ["anthropic", "gemini"])
def test_custom_non_openai_factory_rejects_transport(api_compat):
    with pytest.raises(ValueError, match="responses_transport is scoped"):
        create_custom_adapter(
            api_key="not-a-real-key",
            api_compat=api_compat,
            base_url="https://gateway.example.test/v1",
            responses_transport="websocket",
        )


def valid_init_with_transport():
    return {
        "covenant": "test",
        "pad": "test",
        "manifest": {
            "llm": {
                "provider": "custom",
                "model": "test-model",
                "api_compat": "openai",
                "base_url": "https://gateway.example.test/v1",
                "wire_api": "responses",
                "responses_transport": "websocket",
            }
        },
    }


def test_init_schema_accepts_only_custom_openai_responses_transport():
    data = valid_init_with_transport()
    assert validate_init(data) == []

    data["manifest"]["llm"]["wire_api"] = "chat_completions"
    with pytest.raises(ValueError, match="responses_transport"):
        validate_init(data)


def test_init_schema_rejects_unknown_responses_transport():
    data = valid_init_with_transport()
    data["manifest"]["llm"]["responses_transport"] = "auto"

    with pytest.raises(ValueError, match="expected one of http, websocket"):
        validate_init(data)


def test_preset_loader_validates_responses_transport_scope(tmp_path):
    preset = {
        "name": "custom-ws",
        "description": {"summary": "test"},
        "manifest": {
            "llm": valid_init_with_transport()["manifest"]["llm"],
            "capabilities": {},
        },
    }
    path = tmp_path / "custom-ws.json"
    path.write_text(json.dumps(preset))

    loaded = load_preset(str(path), run_migrations=lambda _: None)
    assert loaded["manifest"]["llm"]["responses_transport"] == "websocket"

    preset["manifest"]["llm"]["api_compat"] = "anthropic"
    path.write_text(json.dumps(preset))
    with pytest.raises(ValueError, match="responses_transport"):
        load_preset(str(path), run_migrations=lambda _: None)


def test_manifest_provider_defaults_forward_responses_transport():
    defaults = build_provider_defaults_from_manifest_llm(
        valid_init_with_transport()["manifest"]["llm"], max_rpm=0
    )

    assert defaults == {
        "custom": {
            "api_compat": "openai",
            "wire_api": "responses",
            "responses_transport": "websocket",
        }
    }


def test_service_registry_forwards_transport_to_custom_adapter():
    llm = valid_init_with_transport()["manifest"]["llm"]
    defaults = build_provider_defaults_from_manifest_llm(llm, max_rpm=0)

    service = LLMService(
        provider="custom",
        model="test-model",
        api_key="not-a-real-key",
        base_url="https://gateway.example.test/v1",
        provider_defaults=defaults,
    )
    adapter = service.get_adapter(
        "custom", base_url="https://gateway.example.test/v1"
    )

    assert adapter._wire_api == "responses"
    assert adapter._responses_transport == "websocket"
