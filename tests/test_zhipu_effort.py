"""Zhipu/GLM provider-owned reasoning contract (issue #1197).

Every wire assertion in this file is made against the **serialized HTTP request
body** captured at the ``httpx`` transport boundary, not against Python kwargs.
The audit (``glm-contract-source-audit-opus5.md`` §B.6/§B.7) established by
byte-capture that ``extra_body`` members are hoisted to top-level JSON siblings
and that an ``extra_body`` member silently overrides a same-named flat SDK
kwarg — so kwargs-level assertions would not prove the contract.

No socket is opened: the adapter's own ``_client_kwargs`` are reused to build an
``openai.OpenAI`` client bound to an ``httpx.MockTransport``. The API key is the
literal string ``"probe"``.

Contract under test (binding brief §2.2):

    omitted / "default" / None  ->  no ``thinking``, no ``reasoning_effort``
    "none"                      ->  thinking={"type": "disabled"}, no effort
    "high"                      ->  thinking={"type": "enabled"}, effort="high"
    "max"                       ->  thinking={"type": "enabled"}, effort="max"

Explicit values are gated fail-closed to normalized model id ``glm-5.2``;
omission is valid for every model.
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest

from lingtai.llm.zhipu.adapter import ZhipuAdapter, ZhipuChatSession

ZHIPU_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
MODEL = "GLM-5.2"

_COMPLETION = {
    "id": "chatcmpl-probe",
    "object": "chat.completion",
    "created": 0,
    "model": MODEL,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

_STREAM_CHUNKS = [
    {
        "id": "chatcmpl-probe",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-probe",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
]


class WireCapture:
    """Records every serialized request body that reaches the transport."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        body = json.loads(request.content)
        self.bodies.append(body)
        if body.get("stream"):
            payload = "".join(
                f"data: {json.dumps(chunk)}\n\n" for chunk in _STREAM_CHUNKS
            )
            payload += "data: [DONE]\n\n"
            return httpx.Response(
                200,
                content=payload.encode(),
                headers={"Content-Type": "text/event-stream"},
            )
        return httpx.Response(200, json=_COMPLETION)

    @property
    def last(self) -> dict:
        assert self.bodies, "no request reached the transport"
        return self.bodies[-1]


def make_adapter(adapter_cls=ZhipuAdapter, **kwargs):
    """Build a real adapter whose only substitution is a MockTransport client."""
    kwargs.setdefault("api_key", "probe")
    kwargs.setdefault("base_url", ZHIPU_BASE_URL)
    adapter = adapter_cls(**kwargs)
    capture = WireCapture()
    adapter._client = openai.OpenAI(
        **adapter._client_kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(capture.handler)),
    )
    return adapter, capture


def send_once(thinking, model: str = MODEL, adapter_cls=ZhipuAdapter):
    """Create a chat with ``thinking`` and send one message; return the body."""
    adapter, capture = make_adapter(adapter_cls)
    session = adapter.create_chat(
        model=model, system_prompt="sys", thinking=thinking
    )
    session.send("hello")
    return capture.last, session, capture


# ---------------------------------------------------------------------------
# 1-2. Omission stays omission — no reasoning fields on the wire
# ---------------------------------------------------------------------------


def test_omission_sentinel_emits_no_reasoning_fields():
    body, _session, _cap = send_once("default")
    assert "thinking" not in body
    assert "reasoning_effort" not in body
    assert set(body) == {"messages", "model", "prompt_cache_key"}


def test_python_none_is_treated_as_omission():
    """Pins the §B.10 latent defect: None used to emit ``reasoning_effort: low``."""
    body, _session, _cap = send_once(None)
    assert "thinking" not in body
    assert "reasoning_effort" not in body
    assert set(body) == {"messages", "model", "prompt_cache_key"}


# ---------------------------------------------------------------------------
# 3-5. The exact configured wire matrix
# ---------------------------------------------------------------------------


def test_none_maps_to_thinking_disabled_without_effort():
    body, _session, _cap = send_once("none")
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


def test_high_maps_to_enabled_plus_high_effort():
    body, _session, _cap = send_once("high")
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"


def test_max_maps_to_enabled_plus_max_effort():
    body, _session, _cap = send_once("max")
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "max"


# ---------------------------------------------------------------------------
# 6. Never the Responses nested shape, never a literal extra_body wrapper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("thinking", ["default", None, "none", "high", "max"])
def test_no_responses_reasoning_object_and_no_extra_body_wrapper(thinking):
    body, _session, _cap = send_once(thinking)
    assert "reasoning" not in body
    assert "extra_body" not in body


@pytest.mark.parametrize("thinking", ["default", None, "none", "high", "max"])
def test_clear_thinking_is_never_emitted(thinking):
    body, _session, _cap = send_once(thinking)
    assert "clear_thinking" not in json.dumps(body)


# ---------------------------------------------------------------------------
# 7. Invalid values are rejected before any transport request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["minimal", "low", "medium", "xhigh", "High", " high", "", "bogus", True, 1, 0],
)
def test_invalid_values_raise_before_dispatch(value):
    adapter, capture = make_adapter()
    with pytest.raises(ValueError):
        adapter.create_chat(model=MODEL, system_prompt="sys", thinking=value)
    assert capture.bodies == []


def test_invalid_value_error_names_provider_and_tokens():
    adapter, _capture = make_adapter()
    with pytest.raises(ValueError, match="none, high, max"):
        adapter.create_chat(model=MODEL, system_prompt="sys", thinking="medium")


# ---------------------------------------------------------------------------
# 8-9. Fail-closed model gate; configured wire spelling preserved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["GLM-5.1", "glm-4.7", "whatever"])
@pytest.mark.parametrize("thinking", ["none", "high", "max"])
def test_explicit_value_on_non_52_model_raises_before_dispatch(model, thinking):
    adapter, capture = make_adapter()
    with pytest.raises(ValueError):
        adapter.create_chat(model=model, system_prompt="sys", thinking=thinking)
    assert capture.bodies == []


@pytest.mark.parametrize("model", ["GLM-5.1", "glm-4.7", "whatever"])
def test_omission_is_valid_on_any_model_and_field_free(model):
    body, _session, _cap = send_once("default", model=model)
    assert "thinking" not in body
    assert "reasoning_effort" not in body
    assert body["model"] == model


@pytest.mark.parametrize("configured", ["GLM-5.2", "glm-5.2", " GLM-5.2 "])
def test_model_gate_normalizes_but_wire_spelling_is_preserved(configured):
    body, _session, _cap = send_once("max", model=configured)
    assert body["model"] == configured
    assert body["reasoning_effort"] == "max"


# ---------------------------------------------------------------------------
# 10. Both registered provider spellings behave identically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["zhipu", "glm"])
def test_both_provider_spellings_resolve_to_the_zhipu_adapter(provider):
    from lingtai.llm.service import LLMService
    from lingtai.llm import _register  # noqa: F401  (registers factories)

    factory = LLMService._adapter_registry[provider]
    adapter = factory(api_key="probe", base_url=ZHIPU_BASE_URL)
    assert isinstance(adapter, ZhipuAdapter)


@pytest.mark.parametrize("provider", ["zhipu", "glm"])
def test_both_provider_spellings_pass_schema_and_preset_validation(provider):
    from lingtai.kernel.config import llm_supports_thinking, thinking_levels_for_llm

    llm = {"provider": provider, "model": MODEL}
    assert llm_supports_thinking(llm) is True
    assert thinking_levels_for_llm(llm) == ("none", "high", "max")


# ---------------------------------------------------------------------------
# 11. Frozen at creation — repeated sends and streaming reuse one descriptor
# ---------------------------------------------------------------------------


def test_repeated_send_and_stream_carry_identical_reasoning_fields():
    adapter, capture = make_adapter()
    session = adapter.create_chat(model=MODEL, system_prompt="sys", thinking="max")

    session.send("first")
    session.send("second")
    session.send_stream("third")

    assert len(capture.bodies) == 3
    for body in capture.bodies:
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "max"
    assert capture.bodies[-1]["stream"] is True


def test_streaming_body_keeps_the_frozen_pair():
    adapter, capture = make_adapter()
    session = adapter.create_chat(model=MODEL, system_prompt="sys", thinking="none")
    session.send_stream("hello")

    body = capture.last
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


# ---------------------------------------------------------------------------
# 12. Generic OpenAI behavior stays byte-identical (protects the hook move)
# ---------------------------------------------------------------------------


def _generic_adapter_classes():
    from lingtai.llm.deepseek.adapter import DeepSeekAdapter
    from lingtai.llm.openai.adapter import OpenAIAdapter

    return [OpenAIAdapter, DeepSeekAdapter]


@pytest.mark.parametrize("thinking,expected", [("high", "high"), ("medium", "low")])
def test_generic_openai_compat_retains_pre_refactor_flat_mapping(thinking, expected):
    for adapter_cls in _generic_adapter_classes():
        adapter, capture = make_adapter(
            adapter_cls, base_url="https://example.invalid/v1"
        )
        session = adapter.create_chat(
            model="some-model", system_prompt="sys", thinking=thinking
        )
        session.send("hello")
        body = capture.last
        assert body["reasoning_effort"] == expected, adapter_cls.__name__
        assert "thinking" not in body, adapter_cls.__name__


def test_generic_openai_compat_omission_emits_nothing():
    for adapter_cls in _generic_adapter_classes():
        adapter, capture = make_adapter(
            adapter_cls, base_url="https://example.invalid/v1"
        )
        session = adapter.create_chat(
            model="some-model", system_prompt="sys", thinking="default"
        )
        session.send("hello")
        body = capture.last
        assert "reasoning_effort" not in body, adapter_cls.__name__
        assert "thinking" not in body, adapter_cls.__name__


# ---------------------------------------------------------------------------
# 7 (cont). No flat/extra_body double-emission; no private carrier on the wire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("thinking", ["default", None, "none", "high", "max"])
def test_zhipu_session_extra_kwargs_carry_no_flat_reasoning_effort(thinking):
    """The §B.7 clobber is structurally impossible: the generic branch never fires."""
    _body, session, _cap = send_once(thinking)
    assert "reasoning_effort" not in session._extra_kwargs


@pytest.mark.parametrize("thinking", ["default", None, "none", "high", "max"])
def test_private_effort_carrier_never_reaches_wire_kwargs(thinking):
    body, session, _cap = send_once(thinking)
    for key in session._extra_kwargs:
        assert not key.startswith("_"), key
    assert not any(k.startswith("_") for k in body), body


# ---------------------------------------------------------------------------
# 13-14. Preserved Zhipu behaviors compose with the new contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("thinking", ["default", None, "none", "high", "max"])
def test_prompt_cache_key_is_unchanged(thinking):
    body, _session, _cap = send_once(thinking)
    assert body["prompt_cache_key"] == f"lingtai-zhipu:{MODEL}:v1"


def test_same_role_merge_composes_with_explicit_max():
    adapter, capture = make_adapter()
    session = adapter.create_chat(model=MODEL, system_prompt="sys", thinking="max")
    session.interface.add_user_message("first")
    session.interface.add_user_message("second")
    session.send("third")

    body = capture.last
    roles = [m["role"] for m in body["messages"]]
    assert all(
        not (a == b and a == "user") for a, b in zip(roles, roles[1:])
    ), roles
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "max"


# ---------------------------------------------------------------------------
# 15. Safe observability derived from the one frozen decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "thinking,requested,normalized,actual,source",
    [
        ("default", "omitted", "omitted", "omitted", "lingtai_zhipu_omitted"),
        (None, "omitted", "omitted", "omitted", "lingtai_zhipu_omitted"),
        ("none", "none", "disabled", "thinking=disabled", "explicit_config"),
        (
            "high",
            "high",
            "high",
            "thinking=enabled,effort=high",
            "explicit_config",
        ),
        ("max", "max", "max", "thinking=enabled,effort=max", "explicit_config"),
    ],
)
def test_reasoning_observability_matches_the_frozen_wire_decision(
    thinking, requested, normalized, actual, source
):
    _body, session, _cap = send_once(thinking)
    fields = session.reasoning_observability()

    assert fields == {
        "reasoning_requested": requested,
        "reasoning_normalized": normalized,
        "reasoning_actual": actual,
        "reasoning_source": source,
        "reasoning_capability_source": "zhipu_docs_20260805",
    }


def test_reasoning_observability_leaks_no_secrets():
    _body, session, _cap = send_once("max")
    blob = json.dumps(session.reasoning_observability())
    for forbidden in ("probe", ZHIPU_BASE_URL, "api.z.ai", "sys", "hello"):
        assert forbidden not in blob


def test_zhipu_session_is_the_zhipu_session_class():
    _body, session, _cap = send_once("max")
    assert isinstance(session, ZhipuChatSession)


# ---------------------------------------------------------------------------
# 16. AgentConfig hydration — omission keeps the sentinel for zhipu/glm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["zhipu", "glm"])
def test_agent_omission_hydrates_to_default_sentinel(provider):
    from lingtai.agent import build_agent_config

    cfg = build_agent_config({"llm": {"provider": provider}}, max_rpm=0)
    assert cfg.thinking == "default"


@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek"])
def test_non_zhipu_non_codex_omission_still_hydrates_legacy_high(provider):
    from lingtai.agent import build_agent_config

    cfg = build_agent_config({"llm": {"provider": provider}}, max_rpm=0)
    assert cfg.thinking == "high"


@pytest.mark.parametrize("provider", ["zhipu", "glm"])
@pytest.mark.parametrize("value", ["none", "high", "max"])
def test_agent_explicit_value_hydrates_verbatim(provider, value):
    from lingtai.agent import build_agent_config

    cfg = build_agent_config(
        {"llm": {"provider": provider, "thinking": value}}, max_rpm=0
    )
    assert cfg.thinking == value


# ---------------------------------------------------------------------------
# 17. Daemon hard-coded "default" and main omission agree at the decision seam
# ---------------------------------------------------------------------------


def test_daemon_hardcoded_default_and_main_omission_share_one_payload():
    """The four daemon sites (audit §B.8) pass the literal ``"default"``.

    Asserted at the normalizer — the seam that actually decides the bytes —
    rather than by standing up a real daemon.
    """
    from lingtai.agent import build_agent_config
    from lingtai.llm.zhipu.effort import normalize_zhipu_effort

    daemon_value = "default"  # daemon/__init__.py:2830,3002,3184,3287
    main_value = build_agent_config({"llm": {"provider": "zhipu"}}, max_rpm=0).thinking

    daemon = normalize_zhipu_effort(daemon_value, MODEL)
    main = normalize_zhipu_effort(main_value, MODEL)

    assert daemon.extra_body == {}
    assert main.extra_body == {}
    assert daemon == main


# ---------------------------------------------------------------------------
# Normalizer unit surface (imports are function-local on purpose: a module-level
# import of the not-yet-existing module would abort collection of this whole
# file and destroy the adapter-level RED evidence above).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("thinking", ["default", None])
def test_normalizer_treats_default_and_none_as_omission(thinking):
    from lingtai.llm.zhipu.effort import normalize_zhipu_effort

    effort = normalize_zhipu_effort(thinking, MODEL)
    assert effort.extra_body == {}
    assert effort.requested == "omitted"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("none", {"thinking": {"type": "disabled"}}),
        ("high", {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}),
        ("max", {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}),
    ],
)
def test_normalizer_renders_the_exact_extra_body(value, expected):
    from lingtai.llm.zhipu.effort import normalize_zhipu_effort

    assert normalize_zhipu_effort(value, MODEL).extra_body == expected


def test_normalizer_descriptor_is_frozen():
    import dataclasses

    from lingtai.llm.zhipu.effort import normalize_zhipu_effort

    effort = normalize_zhipu_effort("max", MODEL)
    assert dataclasses.is_dataclass(effort)
    with pytest.raises(dataclasses.FrozenInstanceError):
        effort.requested = "none"


def test_capability_metadata_is_dated_and_unverified():
    from lingtai.llm.zhipu.effort import (
        ZHIPU_EFFORT_CAPABILITY_SOURCE,
        ZHIPU_EFFORT_CAPABLE_MODELS,
        ZHIPU_EFFORT_MODEL_VERIFIED,
    )

    assert ZHIPU_EFFORT_CAPABLE_MODELS == ("glm-5.2",)
    assert ZHIPU_EFFORT_CAPABILITY_SOURCE == "zhipu_docs_20260805"
    assert ZHIPU_EFFORT_MODEL_VERIFIED is False


def test_normalizer_rejects_out_of_vocabulary_values():
    from lingtai.llm.zhipu.effort import normalize_zhipu_effort

    for value in ("minimal", "low", "medium", "xhigh", "High", " high", "", 1, True):
        with pytest.raises(ValueError):
            normalize_zhipu_effort(value, MODEL)


# ---------------------------------------------------------------------------
# 15 (cont). The kernel `llm_call` seam itself
#
# `reasoning_observability()` existing is not the same as `llm_call` actually
# picking it up. AgentConfig.max_rpm defaults to 60, so the live session is
# normally the `_GatedSession` rate-gate proxy rather than ZhipuChatSession —
# these pin that the proxy forwards the accessor, and that every session
# without one leaves the log line byte-identical.
# ---------------------------------------------------------------------------


_OBSERVABILITY_KEYS = {
    "reasoning_requested",
    "reasoning_normalized",
    "reasoning_actual",
    "reasoning_source",
    "reasoning_capability_source",
}


def test_llm_call_seam_picks_up_a_zhipu_session():
    from lingtai.kernel.session import _reasoning_observability_fields

    _body, session, _cap = send_once("max")
    fields = _reasoning_observability_fields(session)

    assert set(fields) == _OBSERVABILITY_KEYS
    assert fields["reasoning_actual"] == "thinking=enabled,effort=max"


def test_llm_call_seam_sees_through_the_rate_gate_proxy():
    """max_rpm defaults to 60, so production normally holds the proxy."""
    from lingtai.llm.base import _GatedSession
    from lingtai.kernel.session import _reasoning_observability_fields

    _body, session, _cap = send_once("none")
    # Attribute forwarding never consults the gate, so a stand-in keeps this a
    # pure forwarding assertion with no rate-limiter threads.
    proxy = _GatedSession(session, gate=object())

    assert _reasoning_observability_fields(proxy) == session.reasoning_observability()
    assert _reasoning_observability_fields(proxy)["reasoning_actual"] == "thinking=disabled"


def test_llm_call_seam_contributes_nothing_for_a_generic_session():
    """Every non-zhipu provider keeps the exact {model, api_call_id} shape."""
    from lingtai.llm.openai.adapter import OpenAIAdapter
    from lingtai.kernel.session import _reasoning_observability_fields

    adapter, _capture = make_adapter(
        OpenAIAdapter, base_url="https://example.invalid/v1"
    )
    session = adapter.create_chat(
        model="some-model", system_prompt="sys", thinking="high"
    )

    assert not hasattr(session, "reasoning_observability")
    assert _reasoning_observability_fields(session) == {}


def test_llm_call_seam_never_lets_observability_break_a_turn():
    from lingtai.kernel.session import _reasoning_observability_fields

    class Exploding:
        def reasoning_observability(self):
            raise RuntimeError("boom")

    class NotADict:
        def reasoning_observability(self):
            return "not a dict"

    assert _reasoning_observability_fields(Exploding()) == {}
    assert _reasoning_observability_fields(NotADict()) == {}
    assert _reasoning_observability_fields(object()) == {}


# ===========================================================================
# Parent-review blockers (issue #1197)
# ===========================================================================


# ---------------------------------------------------------------------------
# BLOCKER 1 — the falsey rewrite must not run ahead of the provider contract.
#
# Both SessionManager call sites rewrite the configured value with
# ``self._config.thinking or "high"``. For zhipu/glm that silently converts a
# direct ``AgentConfig(thinking=""|False|0)`` into an explicit ``high`` — so the
# GLM normalizer never sees it and never fails closed — and converts ``None``
# into ``high`` instead of the bound direct-adapter omission. Every other
# provider must keep the legacy fallback byte-equivalent.
# ---------------------------------------------------------------------------


def _session_manager(*, config_provider, thinking, service_provider="zhipu"):
    """Build a SessionManager over a mock LLMService and return both."""
    from unittest.mock import MagicMock

    from lingtai.kernel.config import AgentConfig
    from lingtai.kernel.session import SessionManager

    service = MagicMock()
    service.model = MODEL
    service.provider = service_provider

    config = AgentConfig(provider=config_provider, model=MODEL)
    # Assigned after construction so falsey values survive any field default.
    config.thinking = thinking

    manager = SessionManager(
        llm_service=service,
        config=config,
        agent_name="probe-agent",
        streaming=False,
        build_system_prompt_fn=lambda: "sys",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    return manager, service


def _thinking_passed_to_create(service):
    return service.create_session.call_args.kwargs["thinking"]


@pytest.mark.parametrize("call_site", ["ensure", "rebuild"])
@pytest.mark.parametrize("provider", ["zhipu", "glm"])
@pytest.mark.parametrize("thinking", ["", False, 0])
def test_zhipu_falsey_thinking_is_not_rewritten_to_high(call_site, provider, thinking):
    """A falsey configured value must reach the GLM normalizer and fail closed."""
    from lingtai.kernel.llm.interface import ChatInterface

    manager, service = _session_manager(config_provider=provider, thinking=thinking)

    if call_site == "ensure":
        manager.ensure_session()
    else:
        manager._rebuild_session(ChatInterface())

    passed = _thinking_passed_to_create(service)
    assert passed == thinking and type(passed) is type(thinking), (
        f"{provider}/{call_site}: {thinking!r} was rewritten to {passed!r}"
    )


@pytest.mark.parametrize("call_site", ["ensure", "rebuild"])
@pytest.mark.parametrize("provider", ["zhipu", "glm"])
def test_zhipu_none_thinking_reaches_the_adapter_as_omission(call_site, provider):
    """``None`` is the bound direct-adapter omission, not an explicit high."""
    from lingtai.kernel.llm.interface import ChatInterface

    manager, service = _session_manager(config_provider=provider, thinking=None)

    if call_site == "ensure":
        manager.ensure_session()
    else:
        manager._rebuild_session(ChatInterface())

    assert _thinking_passed_to_create(service) is None


@pytest.mark.parametrize("call_site", ["ensure", "rebuild"])
@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek", "codex"])
@pytest.mark.parametrize("thinking", ["", False, 0, None])
def test_non_zhipu_falsey_thinking_keeps_the_legacy_high_fallback(
    call_site, provider, thinking
):
    """Byte-equivalent legacy behavior for every other provider."""
    from lingtai.kernel.llm.interface import ChatInterface

    manager, service = _session_manager(
        config_provider=provider, thinking=thinking, service_provider=provider
    )

    if call_site == "ensure":
        manager.ensure_session()
    else:
        manager._rebuild_session(ChatInterface())

    assert _thinking_passed_to_create(service) == "high"


@pytest.mark.parametrize("call_site", ["ensure", "rebuild"])
@pytest.mark.parametrize(
    "provider,thinking",
    [
        ("zhipu", "max"),
        ("glm", "none"),
        ("anthropic", "high"),
        # ``"default"`` is truthy, so the legacy ``or "high"`` already passed it
        # through — but it is the exact value Codex's adapter-owned omission
        # contract depends on, and the value `agent.py` now also hydrates for
        # zhipu/glm. Pin it on both sides of the new branch.
        ("codex", "default"),
        ("zhipu", "default"),
        ("glm", "default"),
    ],
)
def test_truthy_thinking_is_passed_through_unchanged(call_site, provider, thinking):
    from lingtai.kernel.llm.interface import ChatInterface

    manager, service = _session_manager(config_provider=provider, thinking=thinking)

    if call_site == "ensure":
        manager.ensure_session()
    else:
        manager._rebuild_session(ChatInterface())

    assert _thinking_passed_to_create(service) == thinking


@pytest.mark.parametrize("thinking", ["", False, 0, None])
def test_zhipu_rule_uses_the_service_provider_when_config_provider_is_none(thinking):
    """``AgentConfig.provider=None`` means "use the LLMService's provider"."""
    manager, service = _session_manager(
        config_provider=None, thinking=thinking, service_provider="glm"
    )

    manager.ensure_session()

    passed = _thinking_passed_to_create(service)
    assert passed == thinking and type(passed) is type(thinking)


def test_zhipu_falsey_value_actually_fails_closed_at_the_normalizer():
    """The point of preserving the value: the provider contract gets to reject it."""
    from lingtai.llm.zhipu.effort import normalize_zhipu_effort

    for value in ("", False, 0):
        with pytest.raises(ValueError):
            normalize_zhipu_effort(value, MODEL)


# ---------------------------------------------------------------------------
# BLOCKER 2 — observability is a trust boundary, not a passthrough.
#
# `_reasoning_observability_fields` merges a provider-supplied dict straight
# into `llm_call`. Without an allowlist an accessor could leak a credential,
# inject unknown fields, or overwrite `model` / `api_call_id`.
# ---------------------------------------------------------------------------


class _HostileSession:
    def __init__(self, payload):
        self._payload = payload

    def reasoning_observability(self):
        return self._payload


def test_observability_allowlist_drops_secrets_and_unknown_keys():
    from lingtai.kernel.session import _reasoning_observability_fields

    hostile = _HostileSession(
        {
            "reasoning_requested": "max",
            "api_key": "sk-live-SECRET",
            "base_url": "https://api.z.ai/api/coding/paas/v4",
            "prompt": "the whole system prompt",
            "session_id": "sess-123",
            "totally_unknown": "x",
        }
    )

    fields = _reasoning_observability_fields(hostile)

    assert fields == {"reasoning_requested": "max"}
    blob = json.dumps(fields)
    for forbidden in ("sk-live-SECRET", "api.z.ai", "system prompt", "sess-123", "totally_unknown"):
        assert forbidden not in blob


@pytest.mark.parametrize("collision", ["model", "api_call_id"])
def test_observability_cannot_overwrite_existing_llm_call_fields(collision):
    from lingtai.kernel.session import _reasoning_observability_fields

    hostile = _HostileSession({collision: "hijacked", "reasoning_source": "explicit_config"})

    fields = _reasoning_observability_fields(hostile)

    assert collision not in fields
    assert fields == {"reasoning_source": "explicit_config"}


@pytest.mark.parametrize(
    "bad_value", [123, 1.5, True, None, {"nested": "dict"}, ["a"], object()]
)
def test_observability_accepts_only_string_values(bad_value):
    from lingtai.kernel.session import _reasoning_observability_fields

    hostile = _HostileSession(
        {"reasoning_requested": bad_value, "reasoning_normalized": "max"}
    )

    fields = _reasoning_observability_fields(hostile)

    assert fields == {"reasoning_normalized": "max"}


def test_observability_rejects_unbounded_string_values():
    from lingtai.kernel.session import _reasoning_observability_fields

    hostile = _HostileSession({"reasoning_actual": "A" * 5000})

    assert _reasoning_observability_fields(hostile) == {}


def test_observability_allowlist_keeps_the_five_real_zhipu_fields_exact():
    from lingtai.kernel.session import _reasoning_observability_fields

    _body, session, _cap = send_once("max")

    assert _reasoning_observability_fields(session) == {
        "reasoning_requested": "max",
        "reasoning_normalized": "max",
        "reasoning_actual": "thinking=enabled,effort=max",
        "reasoning_source": "explicit_config",
        "reasoning_capability_source": "zhipu_docs_20260805",
    }


def test_observability_degradation_still_returns_empty():
    from lingtai.kernel.session import _reasoning_observability_fields

    class Exploding:
        def reasoning_observability(self):
            raise RuntimeError("boom")

    assert _reasoning_observability_fields(Exploding()) == {}
    assert _reasoning_observability_fields(_HostileSession("not a dict")) == {}
    assert _reasoning_observability_fields(_HostileSession(["a", "b"])) == {}
    assert _reasoning_observability_fields(object()) == {}
