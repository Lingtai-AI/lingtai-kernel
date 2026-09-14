"""DeepSeek reasoning-effort contract: model/wire vocabulary, omission, wire shape.

Every assertion here is driven through seams that already exist — the manifest
validator, the shared preset loader, the registered ``deepseek`` factory, and
the kernel ``SessionManager`` record — so the file collects and runs against a
production tree that does not yet implement the contract. What fails is a
behavior assertion, never an import.

The official surface being pinned (api-docs.deepseek.com, current as of
2026-08-10):

  Chat Completions  flash: none | low | high | max   (medium->high, xhigh->high)
                    pro:   none | high | max         (medium->high, xhigh->max)
  Responses         flash: low | high | max          (no alias, no disable)
                    pro:   unsupported route today

An omitted level (absent / ``None`` / the literal ``default`` sentinel) emits no
DeepSeek reasoning field at all and lets the provider default stand.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lingtai.agent import build_agent_config, load_preset
from lingtai.init_schema import validate_init
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.session import SessionManager
from lingtai.llm._register import register_all_adapters
from lingtai.llm.service import LLMService


FLASH = "deepseek-v4-flash"
PRO = "deepseek-v4-pro"
UNKNOWN = "deepseek-v9-nonexistent"


# ---------------------------------------------------------------------------
# helpers — real factory, real adapter, real request construction
# ---------------------------------------------------------------------------


def _deepseek_session(model: str, *, thinking: str = "default", wire: str | None = None):
    """Build a session through the registered deepseek factory (no network)."""
    register_all_adapters()
    factory = LLMService._adapter_registry["deepseek"]
    defaults: dict = {}
    if wire is not None:
        defaults["wire_api"] = wire
    adapter = factory(model=model, api_key="sk-test", defaults=defaults)
    return adapter.create_chat(model=model, system_prompt="sys", thinking=thinking)


def _wire_kwargs(session) -> dict:
    """The exact extra kwargs the SDK request will be built from."""
    return session._extra_kwargs


def _thinking_switch(kwargs: dict):
    return (kwargs.get("extra_body") or {}).get("thinking")


def _applied(session):
    return getattr(session, "reasoning_application", None)


# ---------------------------------------------------------------------------
# Chat Completions — canonical levels, official aliases, exact wire shape
# ---------------------------------------------------------------------------

# (model, requested, normalized effort on the wire, thinking switch)
CHAT_ENABLED = [
    (FLASH, "low", "low"),
    (FLASH, "high", "high"),
    (FLASH, "max", "max"),
    (FLASH, "medium", "high"),   # documented compatibility alias
    (FLASH, "xhigh", "high"),    # documented compatibility alias
    (PRO, "high", "high"),
    (PRO, "max", "max"),
    (PRO, "medium", "high"),     # documented compatibility alias
    (PRO, "xhigh", "max"),       # documented compatibility alias
]


@pytest.mark.parametrize("model,requested,emitted", CHAT_ENABLED)
def test_chat_enabled_effort_serializes_thinking_and_flat_effort(model, requested, emitted):
    kwargs = _wire_kwargs(_deepseek_session(model, thinking=requested))
    assert kwargs["reasoning_effort"] == emitted
    assert _thinking_switch(kwargs) == {"type": "enabled"}


@pytest.mark.parametrize("model", [FLASH, PRO])
def test_chat_none_disables_thinking_and_sends_no_effort(model):
    """``none`` is a real requested value: thinking off, no effort field."""
    kwargs = _wire_kwargs(_deepseek_session(model, thinking="none"))
    assert _thinking_switch(kwargs) == {"type": "disabled"}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("model", [FLASH, PRO, UNKNOWN])
@pytest.mark.parametrize("omission", ["default", None])
def test_chat_omission_emits_no_deepseek_reasoning_fields(model, omission):
    """Omission delegates to the DeepSeek default (thinking on, effort high)."""
    kwargs = _wire_kwargs(_deepseek_session(model, thinking=omission))
    assert "reasoning_effort" not in kwargs
    assert _thinking_switch(kwargs) is None


CHAT_INVALID = [
    (FLASH, "minimal"),
    (FLASH, "ultra"),
    (PRO, "low"),        # Pro has no real low tier today
    (PRO, "minimal"),
    (UNKNOWN, "high"),   # unknown model + explicit effort fails closed
    (UNKNOWN, "none"),
]


@pytest.mark.parametrize("model,requested", CHAT_INVALID)
def test_chat_invalid_effort_fails_closed_before_dispatch(model, requested):
    with pytest.raises(ValueError):
        _deepseek_session(model, thinking=requested)


@pytest.mark.parametrize("bad", [123, 1.5, True, [], {}, ""])
def test_chat_non_string_explicit_value_fails_closed(bad):
    with pytest.raises(ValueError):
        _deepseek_session(FLASH, thinking=bad)


# ---------------------------------------------------------------------------
# Responses — flash only, low|high|max, no alias, no disable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("requested", ["low", "high", "max"])
def test_responses_flash_serializes_reasoning_effort(requested):
    kwargs = _wire_kwargs(_deepseek_session(FLASH, thinking=requested, wire="responses"))
    assert kwargs["reasoning"] == {"effort": requested}


def test_responses_flash_omission_emits_no_reasoning_field():
    kwargs = _wire_kwargs(_deepseek_session(FLASH, thinking="default", wire="responses"))
    assert "reasoning" not in kwargs


@pytest.mark.parametrize("requested", ["none", "minimal", "medium", "xhigh", "ultra"])
def test_responses_flash_rejects_values_outside_its_real_set(requested):
    with pytest.raises(ValueError):
        _deepseek_session(FLASH, thinking=requested, wire="responses")


@pytest.mark.parametrize("requested", ["default", None, "high", "max", "low"])
def test_responses_pro_route_fails_closed_including_omission(requested):
    """Pro is not served on Responses at all — omission cannot rescue it."""
    with pytest.raises(ValueError):
        _deepseek_session(PRO, thinking=requested, wire="responses")


def test_responses_unknown_model_omission_still_emits_nothing():
    """An unrecognized future model keeps working on Auto without a release."""
    kwargs = _wire_kwargs(_deepseek_session(UNKNOWN, thinking="default", wire="responses"))
    assert "reasoning" not in kwargs


# ---------------------------------------------------------------------------
# Observation — requested vs normalized/emitted must stay honest
# ---------------------------------------------------------------------------


def test_alias_observation_does_not_claim_requested_equals_emitted():
    fields = _applied(_deepseek_session(PRO, thinking="xhigh")).observation_fields()
    assert fields["provider"] == "deepseek"
    assert fields["wire"] == "chat_completions"
    assert fields["effort_requested"] == "xhigh"
    assert fields["effort_normalized"] == "max"
    assert fields["effort_emitted"] == "max"


def test_omission_and_disabled_reasoning_are_distinguishable():
    omitted = _applied(_deepseek_session(FLASH, thinking="default")).observation_fields()
    disabled = _applied(_deepseek_session(FLASH, thinking="none")).observation_fields()
    assert omitted["effort_emitted"] == "omitted"
    assert disabled["effort_emitted"] == "disabled"
    assert omitted["effort_emitted"] != disabled["effort_emitted"]


def test_explicit_canonical_observation_reports_identity():
    fields = _applied(
        _deepseek_session(FLASH, thinking="max", wire="responses")
    ).observation_fields()
    assert fields["wire"] == "responses"
    assert fields["effort_requested"] == "max"
    assert fields["effort_emitted"] == "max"


def test_observation_carries_no_payload_or_secret_values():
    fields = _applied(_deepseek_session(FLASH, thinking="high")).observation_fields()
    assert all(isinstance(v, str) for v in fields.values())
    blob = json.dumps(fields)
    assert "sk-test" not in blob
    assert "api_key" not in blob


class _Applied:
    """Stand-in for the applied result the adapter captures on the session."""

    def observation_fields(self):
        return {
            "provider": "deepseek",
            "wire": "chat_completions",
            "effort_requested": "medium",
            "effort_normalized": "high",
            "effort_emitted": "high",
            "effort_provenance": "compat_alias",
        }


def _llm_call_record(reasoning_application) -> dict:
    """Drive one real ``SessionManager.send`` and return its llm_call fields."""
    svc = MagicMock()
    svc.model = "test-model"
    session = MagicMock()
    session.context_window.return_value = 100_000
    session.interface.estimate_context_tokens.return_value = 1_000
    session.interface.has_pending_tool_calls.return_value = False
    session.send.return_value = MagicMock(
        text="ok",
        tool_calls=[],
        thoughts=[],
        usage=MagicMock(
            input_tokens=10, output_tokens=5, thinking_tokens=0,
            cached_tokens=0, extra={},
        ),
    )
    session.reasoning_application = reasoning_application
    svc.create_session.return_value = session

    events: list[tuple[str, dict]] = []
    SessionManager(
        llm_service=svc,
        config=AgentConfig(model="deepseek-v4-flash"),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "sys",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event, **fields: events.append((event, fields)),
    ).send("hi")
    return next(fields for event, fields in events if event == "llm_call")


def test_llm_call_record_carries_the_applied_reasoning_fields():
    """The kernel ``llm_call`` record reads the one applied result, if present."""
    llm_call = _llm_call_record(_Applied())
    assert llm_call["effort_requested"] == "medium"
    assert llm_call["effort_emitted"] == "high"
    assert llm_call["effort_provenance"] == "compat_alias"


def test_llm_call_record_omits_reasoning_fields_without_an_application():
    assert "effort_emitted" not in _llm_call_record(None)


# ---------------------------------------------------------------------------
# SessionManager integration — explicit ``None`` must survive to the wire
# ---------------------------------------------------------------------------


def _session_thinking_passed(config: AgentConfig):
    """Return the ``thinking`` arg SessionManager passes to ``create_session``."""
    svc = MagicMock()
    svc.model = "test-model"
    svc.create_session.return_value = MagicMock(
        context_window=lambda: 100_000,
        interface=MagicMock(
            estimate_context_tokens=lambda: 1_000,
            has_pending_tool_calls=lambda: False,
        ),
        reasoning_effort_capability=lambda: None,
    )
    SessionManager(
        llm_service=svc,
        config=config,
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "sys",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event, **fields: None,
    ).ensure_session()
    return svc.create_session.call_args.kwargs["thinking"]


def test_session_keeps_explicit_none_on_deepseek_owned_route():
    """Provider-owned DeepSeek ``thinking: null`` stays omission, not "high"."""
    passed = _session_thinking_passed(
        AgentConfig(model="deepseek-v4-flash", provider="deepseek", thinking=None)
    )
    assert passed is None


def test_session_promotes_none_for_non_owned_providers():
    """Legacy behavior unchanged: programmatic None -> "high" elsewhere."""
    passed = _session_thinking_passed(
        AgentConfig(model="some-model", provider="openai", thinking=None)
    )
    assert passed == "high"


def test_session_keeps_explicit_value_for_non_owned_providers():
    passed = _session_thinking_passed(
        AgentConfig(model="some-model", provider="openai", thinking="low")
    )
    assert passed == "low"


# ---------------------------------------------------------------------------
# Configuration entry — init manifest and the shared preset loader
# ---------------------------------------------------------------------------


def _init_with_llm(**llm) -> dict:
    return {
        "manifest": {
            "agent_name": "alice",
            "language": "en",
            "llm": {"provider": "deepseek", "api_key": None, "base_url": None, **llm},
            "capabilities": {},
            "soul": {"delay": 120},
            "stamina": 3600,
            "context_limit": None,
            "max_turns": 50,
            "admin": {"karma": True},
            "streaming": False,
        },
        "covenant": "",
        "pad": "",
        "lingtai": "",
        "soul": "",
    }


@pytest.mark.parametrize(
    "model,thinking",
    [
        (FLASH, "none"), (FLASH, "low"), (FLASH, "high"), (FLASH, "max"),
        (FLASH, "medium"), (FLASH, "xhigh"),
        (PRO, "none"), (PRO, "high"), (PRO, "max"), (PRO, "medium"), (PRO, "xhigh"),
    ],
)
def test_init_schema_accepts_real_deepseek_chat_levels(model, thinking):
    """The default DeepSeek wire is Chat, which was rejected outright before."""
    validate_init(_init_with_llm(model=model, thinking=thinking))


@pytest.mark.parametrize(
    "model,thinking",
    [(FLASH, "minimal"), (FLASH, "ultra"), (PRO, "low"), (UNKNOWN, "high")],
)
def test_init_schema_rejects_values_the_route_does_not_have(model, thinking):
    with pytest.raises(ValueError, match="deepseek"):
        validate_init(_init_with_llm(model=model, thinking=thinking))


@pytest.mark.parametrize("thinking", ["none", "minimal", "medium", "xhigh"])
def test_init_schema_rejects_non_responses_values_on_the_responses_wire(thinking):
    with pytest.raises(ValueError, match="deepseek"):
        validate_init(
            _init_with_llm(
                model=FLASH, api_compat="openai", wire_api="responses", thinking=thinking
            )
        )


def test_init_schema_rejects_pro_on_the_responses_wire():
    with pytest.raises(ValueError, match="deepseek"):
        validate_init(
            _init_with_llm(
                model=PRO, api_compat="openai", wire_api="responses", thinking="high"
            )
        )


@pytest.mark.parametrize("omission", ["default", None])
def test_init_schema_treats_default_and_none_as_omission(omission):
    """``None`` and the literal ``default`` are omission, not "disable"."""
    validate_init(_init_with_llm(model=FLASH, thinking=omission))


@pytest.mark.parametrize("bad", [123, True, ["high"], ""])
def test_init_schema_rejects_non_string_thinking(bad):
    with pytest.raises(ValueError):
        validate_init(_init_with_llm(model=FLASH, thinking=bad))


def _write_preset(tmp_path: Path, thinking, *, model: str = FLASH, **llm) -> str:
    preset = {
        "name": "ds",
        "description": {"summary": "deepseek preset"},
        "manifest": {
            "llm": {
                "provider": "deepseek",
                "model": model,
                "api_key": None,
                "api_key_env": "DEEPSEEK_API_KEY",
                **llm,
            },
            "capabilities": {"shell": {}},
        },
    }
    if thinking is not ...:
        preset["manifest"]["llm"]["thinking"] = thinking
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(preset))
    return str(path)


@pytest.mark.parametrize("thinking", ["none", "low", "high", "max", "medium", "xhigh"])
def test_preset_loader_accepts_real_deepseek_chat_levels(tmp_path, thinking):
    loaded = load_preset(_write_preset(tmp_path, thinking))
    assert loaded["manifest"]["llm"]["thinking"] == thinking


@pytest.mark.parametrize(
    "model,thinking", [(FLASH, "minimal"), (PRO, "low"), (UNKNOWN, "high")]
)
def test_preset_loader_rejects_values_the_route_does_not_have(tmp_path, model, thinking):
    with pytest.raises(ValueError, match="deepseek"):
        load_preset(_write_preset(tmp_path, thinking, model=model))


# ---------------------------------------------------------------------------
# Omission provenance through manifest hydration
# ---------------------------------------------------------------------------


def test_omitted_manifest_thinking_hydrates_to_the_omission_sentinel():
    config = build_agent_config(
        {"llm": {"provider": "deepseek", "model": FLASH}}, max_rpm=0
    )
    assert config.thinking == "default"


def test_explicit_manifest_thinking_hydrates_verbatim():
    config = build_agent_config(
        {"llm": {"provider": "deepseek", "model": FLASH, "thinking": "max"}}, max_rpm=0
    )
    assert config.thinking == "max"


def test_non_deepseek_manifest_omission_keeps_the_legacy_default():
    """Unrelated providers must keep hydrating omission to the legacy level."""
    config = build_agent_config(
        {"llm": {"provider": "openai", "model": "gpt-4o"}}, max_rpm=0
    )
    assert config.thinking == AgentConfig().thinking


# ---------------------------------------------------------------------------
# Neighbouring routes stay untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,extra",
    [("openai", {}), ("custom", {"base_url": "https://example.invalid/v1"})],
)
def test_other_openai_compatible_routes_apply_no_reasoning_control(provider, extra):
    register_all_adapters()
    factory = LLMService._adapter_registry[provider]
    adapter = factory(model="gpt-4o", api_key="sk-test", defaults={}, **extra)
    session = adapter.create_chat(model="gpt-4o", system_prompt="sys", thinking="max")
    assert _applied(session) is None
    # Unchanged generic OpenAI vocabulary projection: max clamps to high.
    assert _wire_kwargs(session)["reasoning_effort"] == "high"
    assert _thinking_switch(_wire_kwargs(session)) is None
