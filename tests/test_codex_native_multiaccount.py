"""Native Codex multi-account request-path regressions.

These tests deliberately exercise one ``CodexOpenAIAdapter`` and one
``CodexResponsesSession``. ``codex-pool`` remains only a registry spelling for
the same factory; there is no pool chat wrapper or SessionManager selection hook.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lingtai.auth.codex_account_source import (
    AccountCandidate,
    NoCandidateError,
    WeightedAccountSource,
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


class _SequenceSource:
    def __init__(self, *paths: str):
        self._candidates = [
            AccountCandidate(path, f"account-{i}.json", i, 2 if i == 0 else 1)
            for i, path in enumerate(paths)
        ]
        self.calls = []

    def snapshot(self):
        return list(self._candidates)

    def select(self, exclude=None, quota_left_snapshot=None, snapshot=None):
        excluded = exclude or set()
        candidates = list(self._candidates if snapshot is None else snapshot)
        if not candidates:
            raise RuntimeError("no candidate")
        start = len(self.calls) % len(candidates)
        for offset in range(len(candidates)):
            candidate = candidates[(start + offset) % len(candidates)]
            if candidate.auth_path_sha8 not in excluded:
                self.calls.append(candidate)
                return candidate
        raise RuntimeError("no candidate")

    def quota_targets(self, exclude=None, snapshot=None):
        excluded = exclude or set()
        candidates = self._candidates if snapshot is None else snapshot
        return [
            (c.auth_ref, c.auth_path_sha8)
            for c in candidates
            if c.auth_path_sha8 not in excluded
        ]


class _NoneSnapshotSource(_SequenceSource):
    def snapshot(self):
        return None


class _Responses:
    def __init__(self, events_or_errors):
        self.events_or_errors = list(events_or_errors)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.events_or_errors.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item() if callable(item) else item


class _Client:
    def __init__(self, responses):
        self.responses = responses
        self.api_key = "boot"


def _adapter(source, managers, responses, **kwargs):
    def manager_factory(*, token_path=None):
        return managers[token_path]

    fallback_auth_path = kwargs.pop("codex_fallback_auth_path", "a.json")
    adapter = CodexOpenAIAdapter(
        api_key="boot",
        base_url="http://codex.test",
        use_responses=True,
        force_responses=True,
        codex_account_source=source,
        codex_token_manager_factory=manager_factory,
        codex_fallback_auth_path=fallback_auth_path,
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


def _write_weighted_pool(tmp_path, *paths: str):
    pool = tmp_path / "codex-auth-pool.json"
    pool.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {"path": path, "weight": 1}
                    for path in paths
                ],
            }
        ),
        encoding="utf-8",
    )
    return pool


def test_codex_pool_spellings_are_only_aliases_for_native_codex_factory():
    from lingtai.llm.service import LLMService

    native = LLMService._adapter_registry["codex"]
    assert LLMService._adapter_registry["codex-pool"] is native
    assert LLMService._adapter_registry["codex_pool"] is native


def test_native_codex_single_account_uses_normal_chat_path():
    source = _SequenceSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json"), responses)

    chat = adapter.create_chat("gpt-5.5", "system")
    assert chat.interface is not None
    assert source.calls == []  # chat construction consumes no account draw

    response = chat.send("hello")
    assert response.text == "ok"
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert len(responses.calls) == 1
    assert responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"] == "acct-one.json"
    assert chat.codex_pool_selection["source_index"] == 0
    assert (
        chat.codex_pool_selection["auth_path_sha8"]
        == source._candidates[0].auth_path_sha8
    )
    assert "secret-one.json" not in repr(chat.codex_pool_selection)


def test_native_codex_keeps_one_account_sticky_within_context_epoch():
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
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
    for call in responses.calls:
        assert "secret-one.json" not in repr(call)
        assert "secret-two.json" not in repr(call)


def test_native_codex_rebuild_is_scoped_to_its_chat_context():
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
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

    source_a = _SequenceSource("one.json")
    responses_a = _Responses([_success_events])
    adapter_a = _adapter(source_a, _managers("one.json"), responses_a)
    chat_a = adapter_a.create_chat("gpt-5.5", "system", interface=shared)
    context_a = shared._lingtai_codex_account_context
    chat_a.send("from A")

    source_b = _SequenceSource("two.json")
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
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    chat.send("before rebuild")
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]

    assert chat.request_history_rebuild() is True
    chat.send("after rebuild")
    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "two.json",
    ]


def test_native_codex_no_summary_hard_boundary_redraws_once_then_sticks():
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_success_events, _success_events, _success_events])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system", context_window=10)

    chat.send("first")
    chat.send("100% forced rebuild without a summary")
    chat.send("ordinary request after rebuild")

    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "two.json",
    ]
    assert [call["extra_headers"]["ChatGPT-Account-ID"] for call in responses.calls] == [
        "acct-one.json",
        "acct-two.json",
        "acct-two.json",
    ]


def test_native_codex_technical_epoch_reset_keeps_account_sticky():
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    chat.send("before technical reset")
    chat._reset_ws_epoch("encrypted_reasoning_self_heal")
    chat.send("after technical reset")

    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]


def test_native_codex_molt_starts_one_fresh_account_epoch():
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(
        source,
        _managers("one.json", "two.json"),
        responses,
        codex_molt_count=0,
    )
    chat = adapter.create_chat("gpt-5.5", "system")

    chat.send("before molt")
    adapter._codex_molt_count_override = 1
    chat.send("after molt")

    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "two.json",
    ]


def test_native_codex_refreshes_bound_quota_without_redrawing(monkeypatch):
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_success_events, _success_events])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
    quota_reads = iter([70.0, 30.0, None])
    monkeypatch.setattr(
        "lingtai.llm.openai.codex_quota.read_remaining_percent",
        lambda _auth_ref: next(quota_reads),
    )
    chat = adapter.create_chat("gpt-5.5", "system")

    chat.send("first")
    assert chat.codex_pool_selection["quota_left"] == 70.0
    chat.send("second")

    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    assert "quota_left" not in chat.codex_pool_selection
    assert chat.codex_pool_selection.get("quota_left") != 0


def test_native_codex_service_tier_fast_reaches_provider_request():
    source = _SequenceSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(
        source, _managers("one.json"), responses, codex_service_tier="priority"
    )
    chat = adapter.create_chat("gpt-5.5", "system")
    chat.send("hello")
    assert responses.calls[0]["service_tier"] == "priority"


def test_native_codex_one_shot_uses_native_request_shape_and_safe_metadata():
    source = _SequenceSource("one.json")
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
    assert result.usage.extra["codex_pool_source_index"] == "0"
    assert result.usage.extra["codex_auth_path_sha8"] == source._candidates[0].auth_path_sha8
    assert "secret-one.json" not in repr(request)


def test_native_codex_one_shot_preserves_list_content_user_envelope():
    source = _SequenceSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json"), responses)
    contents = [{"type": "input_text", "text": "list-content"}]

    adapter.generate("gpt-5.5", contents, system_prompt="system")

    assert responses.calls[0]["input"] == [
        {"role": "user", "content": contents}
    ]


def test_native_codex_usage_limit_marks_account_for_aed_rebuild_without_pool_retry():
    source = _SequenceSource("one.json", "two.json")
    responses = _Responses([_UsageLimit(), _success_events])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(_UsageLimit):
        chat.send("hello")
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]

    rebuilt = adapter.create_chat("gpt-5.5", "system", interface=chat.interface)
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]
    rebuilt.send("aed recovery")

    assert [candidate.auth_ref for candidate in source.calls] == [
        "one.json",
        "two.json",
    ]
    assert len(responses.calls) == 2
    assert (
        rebuilt.codex_pool_selection["auth_path_sha8"]
        == source._candidates[1].auth_path_sha8
    )


def test_native_codex_does_not_retry_after_partial_stream_output():
    source = _SequenceSource("one.json", "two.json")

    def partial_then_fail():
        yield _Event("response.output_text.delta", delta="partial")
        raise _UsageLimit()

    responses = _Responses([partial_then_fail])
    adapter = _adapter(source, _managers("one.json", "two.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    chunks = []
    with pytest.raises(_UsageLimit) as excinfo:
        chat.send_stream("hello", on_chunk=chunks.append)

    assert getattr(excinfo.value, "_lingtai_partial_stream", False) is True
    assert chunks == ["partial"]
    assert len(responses.calls) == 1
    assert [candidate.auth_ref for candidate in source.calls] == ["one.json"]


def test_native_codex_empty_pool_falls_back_to_legacy_account():
    source = _SequenceSource()
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("a.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    response = chat.send("hello")

    assert response.text == "ok"
    assert source.calls == []
    assert responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"] == "acct-a.json"
    assert chat.codex_pool_selection["fallback"] == "legacy_default"


def test_native_codex_weighted_empty_tuple_falls_back_to_legacy_account(tmp_path):
    source = WeightedAccountSource(tmp_path / "codex-auth-pool.json", tmp_path)
    assert source.snapshot() == ()
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("a.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    response = chat.send("hello")

    assert response.text == "ok"
    assert responses.calls[0]["extra_headers"]["ChatGPT-Account-ID"] == "acct-a.json"
    assert chat.codex_pool_selection["fallback"] == "legacy_default"


def test_native_codex_nonempty_exhausted_pool_never_falls_back():
    source = _SequenceSource("one.json")
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json", "a.json"), responses)
    adapter._codex_excluded_accounts.add(source._candidates[0].auth_path_sha8)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(RuntimeError, match="no candidate"):
        chat.send("hello")

    assert source.calls == []
    assert responses.calls == []


def test_native_codex_empty_pool_without_fallback_reports_diagnostics(tmp_path):
    source = WeightedAccountSource(tmp_path / "missing-pool.json", tmp_path)
    responses = _Responses([_success_events])
    adapter = _adapter(
        source,
        _managers(),
        responses,
        codex_fallback_auth_path=None,
    )
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(NoCandidateError) as excinfo:
        chat.send("hello")

    fields = excinfo.value.diagnostic_fields()
    assert fields["codex_account_pool_size"] == 0
    assert fields["codex_account_existing_excluded_count"] == 0
    assert fields["codex_account_zero_quota_count"] == 0
    assert fields["codex_account_eligible_count"] == 0
    assert fields["codex_account_legacy_fallback_allowed"] is False
    assert responses.calls == []


def test_native_codex_all_preexcluded_reports_diagnostics(tmp_path):
    pool = _write_weighted_pool(tmp_path, "one.json", "two.json")
    source = WeightedAccountSource(pool, tmp_path)
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("one.json", "two.json", "a.json"), responses)
    adapter._codex_excluded_accounts.update(account.sha8 for account in source.snapshot())
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(NoCandidateError) as excinfo:
        chat.send("hello")

    fields = excinfo.value.diagnostic_fields()
    assert fields["codex_account_pool_size"] == 2
    assert fields["codex_account_existing_excluded_count"] == 2
    assert fields["codex_account_zero_quota_count"] == 0
    assert fields["codex_account_combined_excluded_count"] == 2
    assert fields["codex_account_eligible_count"] == 0
    assert fields["codex_account_quota_target_count"] == 0
    assert fields["codex_account_legacy_fallback_allowed"] is False
    assert "pre_excluded=2" in str(excinfo.value)
    assert responses.calls == []


def test_native_codex_all_zero_quota_preflight_does_not_hide_available_pool(
    tmp_path, monkeypatch
):
    auth_refs = [str(tmp_path / "one.json"), str(tmp_path / "two.json")]
    pool = _write_weighted_pool(tmp_path, *auth_refs)
    source = WeightedAccountSource(pool, tmp_path)
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers(*auth_refs, "a.json"), responses)
    monkeypatch.setattr(
        "lingtai.llm.openai.codex_quota.read_remaining_percent",
        lambda _auth_ref: 0.0,
    )
    chat = adapter.create_chat("gpt-5.5", "system")

    response = chat.send("hello")
    assert response.text == "ok"

    # The local quota preflight can be stale or more conservative than the real
    # request path.  It may shape weighting/exclusion, but when it would erase
    # every otherwise-available configured candidate, the adapter must still let
    # the provider adjudicate rather than producing a turn-0 NoCandidate false
    # negative before any request is made.
    assert len(responses.calls) == 1
    assert chat.codex_pool_selection["auth_path_sha8"] in {
        candidate.sha8 for candidate in source.snapshot()
    }


def test_native_codex_none_snapshot_never_falls_back():
    source = _NoneSnapshotSource()
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("a.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(RuntimeError, match="no candidate"):
        chat.send("hello")

    assert source.calls == []
    assert responses.calls == []


@pytest.mark.parametrize("snapshot", ["", {}])
def test_native_codex_non_collection_falsy_snapshot_never_falls_back(snapshot):
    class _FalsySnapshotSource(_SequenceSource):
        def snapshot(self):
            return snapshot

    source = _FalsySnapshotSource()
    responses = _Responses([_success_events])
    adapter = _adapter(source, _managers("a.json"), responses)
    chat = adapter.create_chat("gpt-5.5", "system")

    with pytest.raises(RuntimeError, match="no candidate"):
        chat.send("hello")

    assert source.calls == []
    assert responses.calls == []
