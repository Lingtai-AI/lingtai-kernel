"""Tests for the default ``prompt_cache_key`` on OpenAI-compatible paths.

LingTai sends a stable, namespaced ``prompt_cache_key`` by default on every
OpenAI-compatible request — both Chat Completions and the Responses API — so
successive turns of an agent hit the provider's cross-request prompt cache.

The key is derived from the adapter's identity (official OpenAI vs. a custom
base_url) and the model, so distinct endpoints/models never share a cache
namespace. The probe (``reports/prompt-cache-key-openai-compat-probe-*.json``)
confirmed several OpenAI-compatible vendors' Chat Completions accept the field.

Invariants asserted here:
  * Chat Completions sends a stable ``prompt_cache_key`` by default.
  * The Responses API (non-Codex) sends a stable ``prompt_cache_key`` by default.
  * Official OpenAI (no base_url) and custom-base_url paths get distinct,
    deterministic namespaces.
  * A configured ``prompt_cache_namespace`` gives a provider-scoped key.
  * ``prompt_cache_retention`` is never sent (Codex rejects it; we keep the
    whole OpenAI-compatible surface uniform).
  * No Anthropic-style ``cache_control`` leaks into the request.
  * An explicit key overrides the default; an explicit disable turns it off.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from lingtai.llm.openai.adapter import (
    OpenAIAdapter,
    OpenAIChatSession,
    OpenAIResponsesSession,
)
from lingtai.kernel.llm.base import FunctionSchema


# --- Chat Completions fakes -------------------------------------------------


def _make_chat_raw():
    """Build a minimal fake OpenAI ChatCompletion-like object."""
    msg = SimpleNamespace(content="ok", reasoning_content=None, tool_calls=[])
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )


def _chat_client():
    client = MagicMock()
    client.chat.completions.create.return_value = _make_chat_raw()
    return client


def _chat_kwargs(client) -> dict:
    return client.chat.completions.create.call_args.kwargs


def _no_cache_control(payload) -> bool:
    """Return True iff ``cache_control`` appears nowhere in ``payload``."""
    return "cache_control" not in json.dumps(payload, default=str)


# --- Responses fakes --------------------------------------------------------


def _resp_completed():
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(id="resp_fake", usage=usage),
        delta=None,
        item=None,
        item_id=None,
        text=None,
    )


class _FakeResponses:
    def __init__(self):
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        yield _resp_completed()


class _FakeResponsesClient:
    def __init__(self):
        self.responses = _FakeResponses()


# ---------------------------------------------------------------------------
# Chat Completions default key
# ---------------------------------------------------------------------------


def test_chat_completions_sends_default_prompt_cache_key_official_openai():
    adapter = OpenAIAdapter(api_key="fake")  # no base_url -> official OpenAI
    adapter._client = _chat_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    sent = _chat_kwargs(adapter._client)
    assert sent["prompt_cache_key"] == "lingtai-openai:gpt-5.5:v1"


def test_chat_completions_sends_default_prompt_cache_key_custom_base_url():
    adapter = OpenAIAdapter(api_key="fake", base_url="https://api.vendor.example/v1")
    adapter._client = _chat_client()
    session = adapter.create_chat("some-model", "system prompt")

    session.send("hello")

    sent = _chat_kwargs(adapter._client)
    # Custom base_url -> compat namespace scoped by host + model.
    assert sent["prompt_cache_key"] == "lingtai-openai-compat:api.vendor.example:some-model:v1"


def test_chat_completions_key_stable_across_requests():
    adapter = OpenAIAdapter(api_key="fake")
    adapter._client = _chat_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("first")
    session.send("second")

    keys = [
        c.kwargs["prompt_cache_key"]
        for c in adapter._client.chat.completions.create.call_args_list
    ]
    assert keys == ["lingtai-openai:gpt-5.5:v1", "lingtai-openai:gpt-5.5:v1"]


def test_chat_completions_omits_retention_and_cache_control():
    adapter = OpenAIAdapter(api_key="fake")
    adapter._client = _chat_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    sent = _chat_kwargs(adapter._client)
    assert "prompt_cache_retention" not in sent
    assert _no_cache_control(sent)


def test_chat_completions_streaming_sends_default_prompt_cache_key():
    adapter = OpenAIAdapter(api_key="fake")

    # Build a streaming client that records kwargs and yields a usage-only chunk.
    recorded: dict = {}

    def _create(**kwargs):
        recorded.update(kwargs)
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        yield SimpleNamespace(choices=[], usage=usage)

    client = MagicMock()
    client.chat.completions.create.side_effect = _create
    adapter._client = client
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send_stream("hello")

    assert recorded["prompt_cache_key"] == "lingtai-openai:gpt-5.5:v1"
    assert "prompt_cache_retention" not in recorded


# ---------------------------------------------------------------------------
# Responses API default key (non-Codex)
# ---------------------------------------------------------------------------


def test_responses_sends_default_prompt_cache_key():
    # Official OpenAI Responses path (no base_url, wire_api="responses").
    adapter = OpenAIAdapter(
        api_key="fake", wire_api="responses"
    )
    adapter._client = _FakeResponsesClient()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send_stream("hello")

    sent = session._client.responses.kwargs[0]
    assert sent["prompt_cache_key"] == "lingtai-openai:gpt-5.5:v1"
    assert "prompt_cache_retention" not in sent
    assert _no_cache_control(sent)


# ---------------------------------------------------------------------------
# Provider subclasses
# ---------------------------------------------------------------------------


def test_prompt_cache_namespace_chat_sends_provider_scoped_key():
    # A thinking-mode compatible vendor configured through the generic
    # ``openai`` provider with a fixed namespace.
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://api.deepseek.com",
        inject_reasoning_fallback=True,
        prompt_cache_namespace="deepseek",
    )
    adapter._client = _chat_client()
    session = adapter.create_chat("deepseek-v4", "system prompt")

    session.send("hello")

    sent = _chat_kwargs(adapter._client)
    assert sent["prompt_cache_key"] == "lingtai-deepseek:deepseek-v4:v1"


def test_compatible_base_url_without_namespace_uses_host_scoped_key():
    adapter = OpenAIAdapter(api_key="fake", base_url="https://open.bigmodel.cn/api/paas/v4")
    adapter._client = _chat_client()
    session = adapter.create_chat("glm-4.6", "system prompt")

    session.send("hello")

    sent = _chat_kwargs(adapter._client)
    assert sent["prompt_cache_key"] == "lingtai-openai-compat:open.bigmodel.cn:glm-4.6:v1"


# ---------------------------------------------------------------------------
# Override / disable
# ---------------------------------------------------------------------------


def test_explicit_prompt_cache_key_overrides_default_chat():
    adapter = OpenAIAdapter(api_key="fake", prompt_cache_key="my-key:v9")
    adapter._client = _chat_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    sent = _chat_kwargs(adapter._client)
    assert sent["prompt_cache_key"] == "my-key:v9"


def test_disabled_prompt_cache_key_omits_field_chat():
    adapter = OpenAIAdapter(api_key="fake", prompt_cache_key=False)
    adapter._client = _chat_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    sent = _chat_kwargs(adapter._client)
    assert "prompt_cache_key" not in sent


def test_disabled_prompt_cache_key_omits_field_responses():
    adapter = OpenAIAdapter(
        api_key="fake", wire_api="responses", prompt_cache_key=False
    )
    adapter._client = _FakeResponsesClient()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send_stream("hello")

    sent = session._client.responses.kwargs[0]
    assert "prompt_cache_key" not in sent


# ---------------------------------------------------------------------------
# Direct session-level default contract (Chat Completions)
# ---------------------------------------------------------------------------


def test_chat_session_omits_cache_key_when_unset():
    """A bare OpenAIChatSession with no key set sends nothing — the default
    lives in the adapter, not the session, so direct construction is opt-in."""
    from lingtai.kernel.llm.interface import ChatInterface

    iface = ChatInterface()
    iface.add_system("system prompt")
    session = OpenAIChatSession(
        client=_chat_client(),
        model="gpt-5.5",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )

    session.send("hello")

    sent = _chat_kwargs(session._client)
    assert "prompt_cache_key" not in sent


def test_chat_session_request_history_rebuild_reports_true_without_key():
    """request_history_rebuild on OpenAIChatSession returns True even when no
    prompt cache key is configured: the session is client-managed and the next
    request re-serializes the compacted interface regardless."""
    from lingtai.kernel.llm.interface import ChatInterface

    iface = ChatInterface()
    iface.add_system("system prompt")
    session = OpenAIChatSession(
        client=_chat_client(),
        model="gpt-5.5",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )

    assert session.request_history_rebuild() is True

    session.send("hello")
    sent = _chat_kwargs(session._client)
    assert "prompt_cache_key" not in sent


def test_chat_session_request_history_rebuild_keeps_stable_cache_key():
    """request_history_rebuild on OpenAIChatSession returns True and leaves the
    configured prompt_cache_key stable: the session is client-managed, the next
    request re-serializes the compacted interface, and the same cache-affinity
    key keeps routing to the same provider cache slot across rebuilds."""
    from lingtai.kernel.llm.interface import ChatInterface

    iface = ChatInterface()
    iface.add_system("system prompt")
    session = OpenAIChatSession(
        client=_chat_client(),
        model="gpt-5.5",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        prompt_cache_key="lingtai-test:gpt-5.5:v1",
    )

    # Before rebuild: stable key.
    session.send("hello")
    assert _chat_kwargs(session._client)["prompt_cache_key"] == "lingtai-test:gpt-5.5:v1"

    # Rebuild reports True and does not churn the cache key.
    assert session.request_history_rebuild() is True

    # After rebuild: same key (no epoch suffix), next request carries compacted history.
    session.send("again")
    assert _chat_kwargs(session._client)["prompt_cache_key"] == "lingtai-test:gpt-5.5:v1"
