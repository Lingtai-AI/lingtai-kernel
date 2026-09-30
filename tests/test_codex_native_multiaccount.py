"""Native Codex account-binding request-path regressions.

These tests deliberately exercise one ``CodexOpenAIAdapter`` and one
``CodexResponsesSession`` bound to ONE OAuth account (``FixedAccountSource``).
The in-kernel account pool was removed; pooling is provided by the external
subs-pool proxy, so there is no pool chat wrapper, weighted draw, quota read, or
legacy fallback here. What remains is the per-context binding lifecycle: lazy
binding, stickiness within a context epoch, re-binding at approved boundaries
(rebuild, molt), token refresh/401 recovery, ``usage_limit_reached`` exclusion
(-> terminal ``NoCandidateError``), and safe attribution.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from lingtai.auth.codex import CodexAuthError
from lingtai.auth.codex_account_source import (
    AccountCandidate,
    FixedAccountSource,
    NoCandidateError,
)
from lingtai.kernel.llm.base import (
    LLMReplayTerminalError,
    llm_replay_terminal_flags,
)
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.llm.openai.adapter import CodexOpenAIAdapter


class _Event:
    def __init__(self, event_type: str, **fields):
        self.type = event_type
        self.__dict__.update(fields)


def _usage():
    return SimpleNamespace(
        input_tokens=10,
        output_tokens=2,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def _success_events(text: str = "ok"):
    return iter(
        [
            _Event("response.output_text.delta", delta=text),
            _Event(
                "response.completed",
                response=SimpleNamespace(id="resp", usage=_usage()),
            ),
        ]
    )


class _UsageLimit(Exception):
    status_code = 429
    body = {"error": {"code": "usage_limit_reached"}}


class _TokenExpired(Exception):
    status_code = 401
    body = {"error": {"code": "token_expired"}}


class _RefreshingManager:
    def __init__(self, path: str):
        self.path = path
        self.access_token = f"secret-{path}"
        self.refresh_calls: list[str] = []

    def get_access_token(self):
        return self.access_token

    def get_account_id(self):
        return f"acct-{self.path}"

    def refresh_access_token(self, rejected_access_token: str):
        self.refresh_calls.append(rejected_access_token)
        self.access_token = f"recovered-{self.path}"
        return self.access_token


class _RecordingSource:
    """The production ``FixedAccountSource`` plus a record of every draw.

    Each successful ``select`` is one account binding: the adapter binds at the
    first request of each context epoch and reuses that binding until an
    approved boundary (rebuild, molt) or exclusion.
    """

    def __init__(self, path: str):
        self._source = FixedAccountSource(path)
        self.candidate: AccountCandidate = self._source.select()
        self.calls: list[AccountCandidate] = []
        self.excludes: list[set[str] | None] = []

    def select(self, exclude=None):
        self.excludes.append(set(exclude) if exclude is not None else None)
        candidate = self._source.select(exclude=exclude)
        self.calls.append(candidate)
        return candidate


class _Responses:
    def __init__(self, events_or_errors):
        self.events_or_errors = list(events_or_errors)
        self.calls = []
        self.client_api_keys = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.events_or_errors.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item() if callable(item) else item


class _BoundResponses:
    def __init__(self, owner, backend):
        self._owner = owner
        self._backend = backend

    def create(self, **kwargs):
        self._backend.client_api_keys.append(self._owner.api_key)
        return self._backend.create(**kwargs)


class _Client:
    def __init__(self, responses):
        self._responses_backend = responses
        self.responses = _BoundResponses(self, responses)
        self.api_key = "boot"

    def __copy__(self):
        cloned = type(self)(self._responses_backend)
        cloned.api_key = self.api_key
        return cloned


def _adapter(source, managers, responses, **kwargs):
    def manager_factory(*, token_path=None):
        return managers[token_path]

    adapter = CodexOpenAIAdapter(
        api_key="boot",
        base_url="http://codex.test",
        wire_api="responses",
        codex_account_source=source,
        codex_token_manager_factory=manager_factory,
        **kwargs,
    )
    adapter._client = _Client(responses)
    return adapter


def _managers(*paths):
    return {
        path: SimpleNamespace(
            get_access_token=lambda path=path: f"secret-{path}",
            get_account_id=lambda path=path: f"acct-{path}",
        )
        for path in paths
    }


def test_native_codex_single_account_uses_normal_chat_path():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json"), responses)

    chat = adapter.create_chat("gpt-5.5", "system")
    assert chat.interface is not None
    assert source.calls == []  # chat construction consumes no account binding

    response = chat.send("hello")
    assert response.text == "ok"
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert source.excludes == [None]
    assert len(responses.calls) == 1
    assert responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"] == "acct-one.json"
    binding = chat.interface._lingtai_codex_account_context.binding
    assert binding["auth_path_sha8"] == source.candidate.auth_path_sha8
    assert binding["auth_path_source"] == "configured"
    assert binding["auth_ref"] == "one.json"
    assert "selection" not in binding
    assert chat._codex_auth_path_sha8 == source.candidate.auth_path_sha8
    assert chat._codex_auth_path_source == "configured"
    assert not hasattr(chat, "codex_pool_selection")
    extra = response.usage.extra
    assert extra["codex_auth_path_sha8"] == source.candidate.auth_path_sha8
    assert extra["codex_auth_path_source"] == "configured"
    assert not any(key.startswith("codex_pool") for key in extra)
    assert "secret-one.json" not in repr(extra)


def test_native_codex_without_source_or_factory_uses_constructor_identity():
    """A bare adapter (no source, no token factory) performs no per-request
    account resolution: requests use the client built from the constructor key
    and the adapter-level identity."""
    responses = _Responses([_success_events])
    adapter = CodexOpenAIAdapter(
        api_key="static-key",
        base_url="http://codex.test",
        wire_api="responses",
        codex_account_id="acct-static",
        codex_auth_path_sha8="0123abcd",
        codex_auth_path_source="configured",
    )
    assert adapter._codex_account_resolution_enabled is False
    client = _Client(responses)
    client.api_key = "static-key"  # the SDK client is built from the constructor key
    adapter._client = client
    chat = adapter.create_chat("gpt-5.5", "system")

    response = chat.send("hello")

    assert response.text == "ok"
    assert responses.client_api_keys == ["static-key"]
    assert responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"] == "acct-static"
    assert chat.interface._lingtai_codex_account_context.binding == {}
    assert response.usage.extra["codex_auth_path_sha8"] == "0123abcd"
    assert response.usage.extra["codex_auth_path_source"] == "configured"

    # The direct selection seam binds the constructor key without any token
    # manager or token file.
    binding = adapter._select_codex_account("gpt-5.5")
    assert binding["api_key"] == "static-key"
    assert binding["account_id"] == "acct-static"
    assert binding["auth_path_sha8"] == "0123abcd"
    assert "auth_ref" not in binding


def test_native_codex_factory_without_source_binds_default_token_path(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(tmp_path))
    default_path = str(tmp_path / "codex-auth.json")
    responses = _Responses([_success_events])
    adapter = _adapter(None, _managers(default_path), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    assert chat.send("hello").text == "ok"

    assert responses.client_api_keys == [f"secret-{default_path}"]
    assert (
        responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"]
        == f"acct-{default_path}"
    )
    binding = chat.interface._lingtai_codex_account_context.binding
    assert binding["auth_ref"] == default_path
    assert binding["auth_path_sha8"] == AccountCandidate(default_path).auth_path_sha8
    assert binding["auth_path_source"] == "configured"


def test_native_codex_keeps_one_account_sticky_within_context_epoch():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    interface = ChatInterface()
    interface.add_system("system")
    hook_calls = []

    chat = adapter.create_chat("gpt-5.5", "system", interface=interface)
    chat.pre_request_hook = lambda current: hook_calls.append(current)
    ws_resets = []
    chat._reset_ws_epoch = ws_resets.append
    assert source.calls == []

    chat.send("one")
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert ws_resets == []
    first_entries = len(interface.entries)

    chat.send("two")
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert ws_resets == []

    assert len(responses.calls) == 2
    assert responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"] == "acct-one.json"
    assert responses.calls[1]["extra_headers"]["ChatGPT-Account-ID"] == "acct-one.json"
    assert len(hook_calls) == 2  # exactly once per actual provider request
    assert chat.interface is interface
    assert len(interface.entries) > first_entries
    assert responses.client_api_keys == ["secret-one.json", "secret-one.json"]
    for call in responses.calls:
        assert "secret-one.json" not in repr(call)


def test_native_codex_rebuild_is_scoped_to_its_chat_context():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    main = adapter.create_chat("gpt-5.5", "main")
    other = adapter.create_chat("gpt-5.5", "other")

    main.send("before other rebuild")
    assert main._client is not other._client
    assert other.request_history_rebuild() is True
    main.send("ordinary main request")

    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert [call["extra_headers"]["ChatGPT-Account-ID"] for call in responses.calls] == [
        "acct-one.json",
        "acct-one.json",
    ]


def test_native_codex_adapter_owner_forces_fresh_shared_interface_context():
    shared = ChatInterface()
    shared.add_system("system")

    source_a = _RecordingSource("one.json")
    responses_a = _Responses([_success_events])
    adapter_a = _adapter(source_a, _managers("one.json"), responses_a)
    chat_a = adapter_a.create_chat("gpt-5.5", "system", interface=shared)
    context_a = shared._lingtai_codex_account_context
    chat_a.send("from A")

    source_b = _RecordingSource("two.json")
    responses_b = _Responses([_success_events, _success_events])
    adapter_b = _adapter(source_b, _managers("two.json"), responses_b)
    chat_b = adapter_b.create_chat("gpt-5.5", "system", interface=shared)
    context_b = shared._lingtai_codex_account_context
    chat_b.send("first from B")
    chat_b.send("ordinary B request")

    assert context_a is not context_b
    assert context_a.owner is adapter_a._codex_context_owner
    assert context_b.owner is adapter_b._codex_context_owner
    assert chat_a._client is not chat_b._client
    assert [candidate.auth_ref for candidate in source_a.calls] == ["one.json"]
    assert [candidate.auth_ref for candidate in source_b.calls] == ["two.json"]
    assert [call["extra_headers"]["ChatGPT-Account-ID"] for call in responses_a.calls] == [
        "acct-one.json"
    ]
    assert [call["extra_headers"]["ChatGPT-Account-ID"] for call in responses_b.calls] == [
        "acct-two.json",
        "acct-two.json",
    ]


def test_native_codex_rebuild_starts_one_fresh_account_epoch():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    chat.send("before rebuild")
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    # The token file changed on disk since the first binding.
    manager.access_token = "rotated-one.json"

    assert chat.request_history_rebuild() is True
    chat.send("after rebuild")
    # The approved boundary re-binds the one account and re-reads its token.
    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "one.json",
    ]
    assert responses.client_api_keys == ["secret-one.json", "rotated-one.json"]


def test_native_codex_no_summary_hard_boundary_redraws_once_then_sticks():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    responses = _Responses([_success_events, _success_events, _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system", context_window=10)

    chat.send("first")
    manager.access_token = "rotated-one.json"
    chat.send("100% forced rebuild without a summary")
    manager.access_token = "rotated-again-one.json"
    chat.send("ordinary request after rebuild")

    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "one.json",
    ]
    assert responses.client_api_keys == [
        "secret-one.json",
        "rotated-one.json",
        "rotated-one.json",
    ]
    assert [call["extra_headers"]["ChatGPT-Account-ID"] for call in responses.calls] == [
        "acct-one.json",
        "acct-one.json",
        "acct-one.json",
    ]


def test_native_codex_technical_epoch_reset_keeps_account_sticky():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    chat.send("before technical reset")
    manager.access_token = "rotated-one.json"
    chat._reset_ws_epoch("encrypted_reasoning_self_heal")
    chat.send("after technical reset")

    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert responses.client_api_keys == ["secret-one.json", "secret-one.json"]


def test_native_codex_molt_starts_one_fresh_account_epoch():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(
        source,
        {"one.json": manager},
        responses,
        codex_molt_count=0,
    )
    chat = adapter.create_chat("gpt-5.5", "system")

    chat.send("before molt")
    manager.access_token = "rotated-one.json"
    adapter._codex_molt_count_override = 1
    chat.send("after molt")

    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "one.json",
    ]
    assert responses.client_api_keys == ["secret-one.json", "rotated-one.json"]


def test_native_codex_service_tier_fast_reaches_provider_request():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(
        source, _managers("one.json"), responses, codex_service_tier="priority"
    )
    chat = adapter.create_chat("gpt-5.5", "system")
    chat.send("hello")
    assert responses.calls[0]["service_tier"] == "priority"


def test_native_codex_one_shot_uses_native_request_shape_and_safe_metadata():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(
        source,
        _managers("one.json"),
        responses,
        codex_service_tier="priority",
    )

    result = adapter.generate(
        "gpt-5.5",
        "one-shot",
        temperature=0.2,
        max_output_tokens=12,
    )
    request = responses.calls[0]

    assert result.text == "ok"
    assert request["service_tier"] == "priority"
    assert request["store"] is False
    assert request["temperature"] == 0.2
    assert request["max_output_tokens"] == 12
    assert request["extra_headers"]["ChatGPT-Account-ID"] == "acct-one.json"
    assert request["extra_headers"]["originator"] == "lingtai"
    assert result.usage.extra["codex_auth_path_sha8"] == source.candidate.auth_path_sha8
    assert result.usage.extra["codex_auth_path_source"] == "configured"
    assert not any(key.startswith("codex_pool") for key in result.usage.extra)
    assert "secret-one.json" not in repr(request)


def test_native_codex_one_shot_preserves_list_content_user_envelope():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    contents = [{"type": "input_text", "text": "list-content"}]

    adapter.generate("gpt-5.5", contents, system_prompt="system")

    assert responses.calls[0]["input"] == [
        {"role": "user", "content": contents}
    ]


def test_native_codex_token_expired_refreshes_same_binding_and_retries_once():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    responses = _Responses([_TokenExpired(), _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    class _OpenTransport:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    transport = _OpenTransport()
    chat._ws_transport = transport
    result = chat.send("hello")

    assert result.text == "ok"
    assert manager.refresh_calls == ["secret-one.json"]
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert len(responses.calls) == 2
    assert responses.calls[0] == responses.calls[1]
    assert chat._client.api_key == "recovered-one.json"
    assert chat._ws_api_key == "recovered-one.json"
    assert transport.closed is True
    assert chat._ws_transport is None
    assert chat._ws_epoch_reset_reason_pending == "codex_token_refresh"


def test_native_codex_repeated_token_expired_stops_after_one_retry():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    responses = _Responses([_TokenExpired(), _TokenExpired()])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert type(excinfo.value.original) is _TokenExpired
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]


def test_native_codex_retry_create_different_failure_is_terminal():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    retry_error = RuntimeError("provider unavailable after auth recovery")
    responses = _Responses([_TokenExpired(), retry_error])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert type(excinfo.value) is LLMReplayTerminalError
    assert excinfo.value.original is retry_error
    assert excinfo.value.__cause__ is retry_error
    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2


def test_native_codex_retry_stream_different_failure_is_terminal():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")

    def expire_before_event():
        if False:
            yield None
        raise _TokenExpired()

    retry_error = RuntimeError("retry stream disconnected")

    def retry_stream_fails():
        if False:
            yield None
        raise retry_error

    responses = _Responses([expire_before_event, retry_stream_fails])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert type(excinfo.value) is LLMReplayTerminalError
    assert excinfo.value.original is retry_error
    assert excinfo.value.__cause__ is retry_error
    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2


def test_native_codex_create_recovery_retry_iterator_failure_is_terminal():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    retry_error = RuntimeError("retry iterator disconnected before first event")

    def retry_stream_fails():
        if False:
            yield None
        raise retry_error

    responses = _Responses([_TokenExpired(), retry_stream_fails])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert type(excinfo.value) is LLMReplayTerminalError
    assert excinfo.value.original is retry_error
    assert excinfo.value.__cause__ is retry_error
    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2


def test_native_codex_finalize_failure_after_recovery_is_terminal(monkeypatch):
    from lingtai.llm.openai import adapter as openai_adapter

    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    finalize_error = RuntimeError("accumulator finalize failed after auth recovery")
    original_accumulator = openai_adapter.StreamingAccumulator

    class _FailingFinalizeAccumulator(original_accumulator):
        def finalize(self, *args, **kwargs):
            raise finalize_error

    monkeypatch.setattr(
        openai_adapter, "StreamingAccumulator", _FailingFinalizeAccumulator
    )
    responses = _Responses([_TokenExpired(), _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert type(excinfo.value) is LLMReplayTerminalError
    assert excinfo.value.original is finalize_error
    assert excinfo.value.__cause__ is finalize_error
    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2



def test_native_codex_unrenderable_post_recovery_tail_failure_rolls_back():
    class _SilentMarkerUnrenderableError(RuntimeError):
        @property
        def __dict__(self):
            raise RuntimeError("provider __dict__ failed")

        def __setattr__(self, name, value):
            if name == "_lingtai_no_aed_retry":
                return
            super().__setattr__(name, value)

        def __str__(self):
            raise RuntimeError("provider __str__ failed")

        def __repr__(self):
            raise RuntimeError("provider __repr__ failed")

    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    baseline_error = _SilentMarkerUnrenderableError(
        "baseline bookkeeping failed after auth recovery"
    )
    responses = _Responses([_TokenExpired(), _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    def fail_baseline():
        raise baseline_error

    chat._ws_record_baseline_from_interface = fail_baseline

    with pytest.raises(Exception) as excinfo:
        chat.send("hello")

    assert excinfo.value is not baseline_error
    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert getattr(excinfo.value, "original", None) is baseline_error
    assert excinfo.value.__cause__ is baseline_error
    assert str(excinfo.value) == "Provider recovery failed after bounded retry"
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2
    assert [entry.role for entry in chat.interface.entries] == ["system", "user"]
    assert chat._ws_epoch_reset_reason_pending == "provider_recovery_terminal"
    assert chat._ws_session.last_response is None
    assert chat._response_id is None


def test_native_codex_arbitrary_refresh_callback_failure_is_terminal():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    refresh_error = RuntimeError("credential store unavailable")

    def fail_refresh(rejected_access_token: str):
        manager.refresh_calls.append(rejected_access_token)
        raise refresh_error

    manager.refresh_access_token = fail_refresh
    responses = _Responses([_TokenExpired(), _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert type(excinfo.value) is LLMReplayTerminalError
    assert excinfo.value.original is refresh_error
    assert excinfo.value.__cause__ is refresh_error
    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 1


def test_native_codex_attribute_refusing_retry_failure_uses_terminal_wrapper():
    class _AttributeRefusingError(Exception):
        @property
        def __dict__(self):
            raise AttributeError("provider instance dictionary unavailable")

        def __setattr__(self, name, value):
            if name == "_lingtai_no_aed_retry":
                raise AttributeError("immutable provider exception")
            super().__setattr__(name, value)

    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    retry_error = _AttributeRefusingError("schema failure after auth recovery")
    responses = _Responses([_TokenExpired(), retry_error])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(Exception) as excinfo:
        chat.send("hello")

    assert type(excinfo.value) is LLMReplayTerminalError
    assert llm_replay_terminal_flags(excinfo.value) == (False, True)
    assert excinfo.value.original is retry_error
    assert excinfo.value.__cause__ is retry_error
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2



def test_native_codex_silent_marker_rejection_bypasses_attribute_hooks():
    class _SilentMarkerError(Exception):
        def __setattr__(self, name, value):
            if name == "_lingtai_no_aed_retry":
                return
            super().__setattr__(name, value)

    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    retry_error = _SilentMarkerError("provider exception silently ignored marker")
    responses = _Responses([_TokenExpired(), retry_error])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(Exception) as excinfo:
        chat.send("hello")

    assert type(excinfo.value) is LLMReplayTerminalError
    assert excinfo.value.original is retry_error
    assert excinfo.value.__cause__ is retry_error
    assert llm_replay_terminal_flags(excinfo.value) == (False, True)
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2


def test_native_codex_refresh_rejection_preserves_relogin_error_without_retry():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")

    def reject_refresh(rejected_access_token: str):
        manager.refresh_calls.append(rejected_access_token)
        raise CodexAuthError("Codex refresh token expired. Please run /login.")

    manager.refresh_access_token = reject_refresh
    responses = _Responses([_TokenExpired(), _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert type(excinfo.value.original) is CodexAuthError
    assert "/login" in str(excinfo.value.original)
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 1
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]


def test_native_codex_token_expired_during_stream_recovers_before_output():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")

    def fail_before_output():
        if False:
            yield None
        raise _TokenExpired()

    responses = _Responses([fail_before_output, _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    assert chat.send("hello").text == "ok"
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 2


def test_native_codex_reasoning_summary_event_prevents_token_replay():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")

    def reasoning_then_expire():
        yield _Event(
            "response.reasoning_summary_text.delta",
            item_id="reason-1",
            delta="hidden summary once",
        )
        raise _TokenExpired()

    responses = _Responses([reasoning_then_expire, _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert type(excinfo.value.original) is _TokenExpired
    assert manager.refresh_calls == []
    assert len(responses.calls) == 1
    assert "hidden summary once" not in repr(chat.interface.entries)
    assert "reason-1" not in repr(chat.interface.entries)


def test_native_codex_raw_reasoning_event_prevents_token_replay():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")

    def raw_reasoning_then_expire():
        yield _Event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="reasoning",
                id="reason-raw",
                summary=[SimpleNamespace(type="summary_text", text="raw summary once")],
                content=[],
                encrypted_content="opaque-reasoning-once",
            ),
        )
        raise _TokenExpired()

    responses = _Responses([raw_reasoning_then_expire, _success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert getattr(excinfo.value, "_lingtai_no_aed_retry", False) is True
    assert type(excinfo.value.original) is _TokenExpired
    assert manager.refresh_calls == []
    assert len(responses.calls) == 1
    interface_repr = repr(chat.interface.entries)
    assert "raw summary once" not in interface_repr
    assert "opaque-reasoning-once" not in interface_repr
    assert "reason-raw" not in interface_repr


def test_native_codex_late_token_failure_cannot_clear_newer_binding():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")
    adapter = _adapter(source, {"one.json": manager}, _Responses([_success_events]))
    chat = adapter.create_chat("gpt-5.5", "system")
    context = chat.interface._lingtai_codex_account_context
    rejected = adapter._codex_account_request(context)

    newer = dict(rejected)
    newer["api_key"] = "recovered-by-peer"
    context.client.api_key = "recovered-by-peer"
    newer = adapter._set_codex_account_binding(context, newer)

    recovered = adapter._codex_token_expired(
        context,
        _TokenExpired(),
        rejected["binding_generation"],
        rejected["api_key"],
        rejected["auth_path_sha8"],
    )

    assert recovered == newer
    assert context.binding == newer
    assert context.client.api_key == "recovered-by-peer"
    assert manager.refresh_calls == []
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]


@pytest.mark.parametrize("replacement_kind", ["same_account", "approved_boundary"])
def test_native_codex_recovery_publication_cannot_overwrite_newer_owner(
    replacement_kind,
):
    source = _RecordingSource("one.json")
    manager_one = _RefreshingManager("one.json")
    responses = _Responses([_TokenExpired(), _success_events])
    adapter = _adapter(source, {"one.json": manager_one}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")
    context = chat.interface._lingtai_codex_account_context

    recovery_apply_entered = threading.Event()
    release_recovery_apply = threading.Event()
    replacement_started = threading.Event()
    replacement_done = threading.Event()
    original_apply = chat._codex_apply_account_binding

    def gated_apply(binding):
        if binding.get("api_key") == "recovered-one.json":
            recovery_apply_entered.set()
            assert release_recovery_apply.wait(2), "test did not release recovery apply"
        original_apply(binding)

    chat._codex_apply_account_binding = gated_apply
    outcome = {}

    def run_send():
        try:
            outcome["result"] = chat.send("hello")
        except Exception as exc:  # assertion below preserves the unexpected cause
            outcome["error"] = exc

    def publish_replacement():
        assert recovery_apply_entered.wait(2), "recovery never reached publication"
        replacement_started.set()
        with context.lock:
            if replacement_kind == "same_account":
                replacement = dict(context.binding)
                replacement["api_key"] = "newest-same-account"
                context.client.api_key = replacement["api_key"]
                replacement = adapter._set_codex_account_binding(context, replacement)
                original_apply(replacement)
            else:
                # Exercise the real approved-boundary reset while owning the same
                # context RLock, then publish the fresh re-binding of the one
                # account. Its token file changed since the refresh, so the
                # newer owner is distinguishable from the recovery publication.
                chat._reset_ws_epoch("summarize_rebuild_only")
                manager_one.access_token = "rebound-one.json"
                replacement = adapter._select_codex_account(context)
                original_apply(replacement)
        replacement_done.set()

    send_thread = threading.Thread(target=run_send)
    send_thread.start()
    assert recovery_apply_entered.wait(2)
    replacement_thread = threading.Thread(target=publish_replacement)
    replacement_thread.start()
    assert replacement_started.wait(2)
    time.sleep(0.05)
    assert not replacement_done.is_set(), "replacement bypassed context ownership lock"
    release_recovery_apply.set()
    replacement_thread.join(2)
    send_thread.join(2)

    assert not replacement_thread.is_alive()
    assert not send_thread.is_alive()
    assert "error" not in outcome, repr(outcome.get("error"))
    assert outcome["result"].text == "ok"
    assert replacement_done.is_set()
    assert len(responses.calls) == 2
    assert responses.client_api_keys == ["secret-one.json", "recovered-one.json"]
    assert responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"] == "acct-one.json"
    assert responses.calls[1]["extra_headers"]["ChatGPT-Account-ID"] == "acct-one.json"
    assert manager_one.refresh_calls == ["secret-one.json"]

    expected_identity = source.candidate.auth_path_sha8
    if replacement_kind == "same_account":
        expected_token = "newest-same-account"
        expected_epoch = "codex_token_refresh"
        assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    else:
        expected_token = "rebound-one.json"
        expected_epoch = "summarize_rebuild_only"
        assert [candidate.auth_ref for candidate in source.calls] == [
            "one.json",
            "one.json",
        ]

    assert context.binding["api_key"] == expected_token
    assert context.binding["auth_path_sha8"] == expected_identity
    assert context.client.api_key == expected_token
    assert chat._client.api_key == expected_token
    assert chat._ws_api_key == expected_token
    assert chat._codex_auth_path_sha8 == expected_identity
    assert chat._codex_binding_generation == context.binding["binding_generation"]
    assert chat._ws_epoch_reset_reason_pending == expected_epoch


def test_native_codex_usage_limit_excludes_account_and_aed_rebuild_is_terminal():
    source = _RecordingSource("one.json")
    responses = _Responses([_UsageLimit(), _success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(_UsageLimit):
        chat.send("hello")
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    context = chat.interface._lingtai_codex_account_context
    assert context.excluded_accounts == {source.candidate.auth_path_sha8}

    # AED rebuild reuses the interface/context; construction binds nothing.
    rebuilt = adapter.create_chat("gpt-5.5", "system", interface=chat.interface)
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    with pytest.raises(NoCandidateError) as excinfo:
        rebuilt.send("aed recovery")

    # The one account is excluded: terminal, no silent retry on it, no
    # second wire request, and no pool/quota diagnostics.
    assert source.excludes[-1] == {source.candidate.auth_path_sha8}
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert excinfo.value.diagnostic_fields() == {}
    assert len(responses.calls) == 1


def test_native_codex_usage_limit_exclusion_is_scoped_to_its_context():
    source = _RecordingSource("one.json")
    responses = _Responses([_UsageLimit(), _success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    limited = adapter.create_chat("gpt-5.5", "limited")

    with pytest.raises(_UsageLimit):
        limited.send("hello")

    other = adapter.create_chat("gpt-5.5", "other")
    assert other.send("independent context").text == "ok"

    assert adapter._codex_excluded_accounts == set()
    assert other.interface._lingtai_codex_account_context.excluded_accounts == set()
    assert source.excludes == [None, None]
    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "one.json",
    ]
    assert len(responses.calls) == 2


def test_native_codex_does_not_retry_after_partial_stream_output():
    source = _RecordingSource("one.json")

    def partial_then_fail():
        yield _Event("response.output_text.delta", delta="partial")
        raise _UsageLimit()

    responses = _Responses([partial_then_fail])
    adapter = _adapter(source, _managers("one.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    chunks = []
    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send_stream("hello", on_chunk=chunks.append)

    assert getattr(excinfo.value, "_lingtai_partial_stream", False) is True
    assert type(excinfo.value.original) is _UsageLimit
    assert chunks == ["partial"]
    assert len(responses.calls) == 1
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]


def test_native_codex_token_expired_does_not_replay_partial_text():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")

    def partial_then_expire():
        yield _Event("response.output_text.delta", delta="visible")
        raise _TokenExpired()

    responses = _Responses([partial_then_expire])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")
    chunks = []

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send_stream("hello", on_chunk=chunks.append)

    assert chunks == ["visible"]
    assert getattr(excinfo.value, "_lingtai_partial_stream", False) is True
    assert type(excinfo.value.original) is _TokenExpired
    assert manager.refresh_calls == []
    assert len(responses.calls) == 1


def test_native_codex_token_expired_does_not_replay_partial_tool_call():
    source = _RecordingSource("one.json")
    manager = _RefreshingManager("one.json")

    def partial_tool_then_expire():
        yield _Event(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="call-1", name="shell"),
        )
        yield _Event("response.function_call_arguments.delta", delta='{"command":')
        raise _TokenExpired()

    responses = _Responses([partial_tool_then_expire])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert getattr(excinfo.value, "_lingtai_partial_stream", False) is True
    assert type(excinfo.value.original) is _TokenExpired
    assert manager.refresh_calls == []
    assert len(responses.calls) == 1


def test_native_codex_partial_text_cleanup_failure_keeps_terminal_wrapper():
    source = _RecordingSource("one.json")

    def partial_then_fail():
        yield _Event("response.output_text.delta", delta="visible")
        raise _UsageLimit()

    responses = _Responses([partial_then_fail])
    adapter = _adapter(source, _managers("one.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    def failing_drop_trailing(predicate):
        raise RuntimeError("drop_trailing failed")

    chat.interface.drop_trailing = failing_drop_trailing
    chunks = []

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send_stream("hello", on_chunk=chunks.append)

    assert llm_replay_terminal_flags(excinfo.value) == (True, False)
    assert type(excinfo.value.original) is _UsageLimit
    assert excinfo.value.__cause__ is excinfo.value.original
    assert chunks == ["visible"]
    assert len(responses.calls) == 1


def test_native_codex_partial_tool_cleanup_failure_keeps_terminal_wrapper():
    source = _RecordingSource("one.json")

    def partial_tool_then_fail():
        yield _Event(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="call-1", name="shell"),
        )
        yield _Event("response.function_call_arguments.delta", delta='{"command":')
        raise _UsageLimit()

    responses = _Responses([partial_tool_then_fail])
    adapter = _adapter(source, _managers("one.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    def failing_drop_trailing(predicate):
        raise RuntimeError("drop_trailing failed")

    chat.interface.drop_trailing = failing_drop_trailing

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert llm_replay_terminal_flags(excinfo.value) == (True, False)
    assert type(excinfo.value.original) is _UsageLimit
    assert excinfo.value.__cause__ is excinfo.value.original
    assert len(responses.calls) == 1


def test_native_codex_request_builder_propagates_watchdog_timeout():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")
    chat._request_timeout = 0.125

    response = chat.send("hello")

    assert response.text == "ok"
    wire_timeout = responses.calls[0].get("timeout")
    assert isinstance(wire_timeout, httpx.Timeout)
    # The wire call receives the REMAINING absolute-deadline budget: no more
    # than the watchdog timeout, minus whatever account binding and request
    # construction already consumed.
    assert 0 < wire_timeout.connect <= 0.125
    assert wire_timeout.read == wire_timeout.connect
    assert wire_timeout.write == wire_timeout.connect


def test_native_codex_recovery_past_deadline_fails_closed_without_second_call():
    source = _RecordingSource("one.json")

    class _SlowRefreshingManager(_RefreshingManager):
        def refresh_access_token(self, rejected_access_token):
            # Burn the entire logical-request budget inside recovery so the
            # bounded retry would start past the absolute deadline.
            time.sleep(0.08)
            return super().refresh_access_token(rejected_access_token)

    manager = _SlowRefreshingManager("one.json")
    responses = _Responses([_TokenExpired()])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")
    chat._request_timeout = 0.05

    with pytest.raises(LLMReplayTerminalError) as excinfo:
        chat.send("hello")

    assert llm_replay_terminal_flags(excinfo.value) == (False, True)
    assert type(excinfo.value.original) is TimeoutError
    assert manager.refresh_calls == ["secret-one.json"]
    assert len(responses.calls) == 1


def test_native_codex_initial_binding_past_deadline_fails_closed_no_wire_call():
    """Initial account binding consumes the whole logical budget: the FIRST
    provider request must not start at all, and the escape through the main
    watchdog must be the exact no-AED terminal wrapper, never a plain
    transient TimeoutError that would reopen replay."""
    from lingtai.kernel.base_agent.turn import _is_transient_provider_error
    from lingtai.kernel.llm_utils import send_with_timeout_stream

    source = _RecordingSource("one.json")

    class _SlowInitialManager(_RefreshingManager):
        def get_access_token(self):
            # Burn the entire logical-request budget inside initial account
            # binding so the first wire call would start past the deadline.
            time.sleep(0.08)
            return super().get_access_token()

    manager = _SlowInitialManager("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, {"one.json": manager}, responses)
    chat = adapter.create_chat("gpt-5.5", "system")
    chunks = []
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with pytest.raises(LLMReplayTerminalError) as excinfo:
            send_with_timeout_stream(
                chat, "hello", pool, 0.05, "test", None, chunks.append
            )
    finally:
        pool.shutdown(wait=True)

    assert llm_replay_terminal_flags(excinfo.value) == (False, True)
    assert type(excinfo.value.original) is TimeoutError
    assert _is_transient_provider_error(excinfo.value) is False
    # The wire call never started and no output reached the user or the
    # canonical interface.
    assert responses.calls == []
    assert chunks == []
    roles = [entry.role for entry in chat.interface.entries]
    assert "assistant" not in roles


def test_native_codex_settle_success_is_preserved_not_replaced_by_timeout():
    """The first request starts within budget, the outer watchdog fires, and
    the worker completes successfully during the settle grace: the successful
    response must be returned — exactly one provider call, chunks delivered,
    assistant committed — not discarded as a transient TimeoutError."""
    from lingtai.kernel.llm_utils import send_with_timeout_stream

    source = _RecordingSource("one.json")

    def slow_success():
        # Cross the 50ms main-thread watchdog inside the stream, then
        # complete successfully while the main thread waits in settle grace.
        time.sleep(0.08)
        yield from _success_events()

    responses = _Responses([slow_success])
    adapter = _adapter(source, _managers("one.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")
    chunks = []
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        response = send_with_timeout_stream(
            chat, "hello", pool, 0.05, "test", None, chunks.append
        )
    finally:
        pool.shutdown(wait=True)

    assert response.text == "ok"
    assert chunks == ["ok"]
    assert len(responses.calls) == 1
    # The first wire call received only the remaining deadline budget, not
    # the original full timeout.
    wire_timeout = responses.calls[0].get("timeout")
    assert isinstance(wire_timeout, httpx.Timeout)
    assert wire_timeout.connect is not None
    assert wire_timeout.connect <= 0.05
    roles = [entry.role for entry in chat.interface.entries]
    assert roles == ["system", "user", "assistant"]


def test_native_codex_excluded_account_raises_no_candidate_before_wire_call():
    source = _RecordingSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    adapter._codex_excluded_accounts.add(source.candidate.auth_path_sha8)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(NoCandidateError) as excinfo:
        chat.send("hello")

    assert excinfo.value.diagnostic_fields() == {}
    assert source.excludes == [{source.candidate.auth_path_sha8}]
    assert source.calls == []
    assert responses.calls == []
