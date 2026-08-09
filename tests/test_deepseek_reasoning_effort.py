"""DeepSeek reasoning-effort vertical — provider-local capability/omission/
validation/emission/observation contract.

The eventual ``/effort`` control must offer all and only the *real* canonical
choices of the current DeepSeek model on the current wire.  DeepSeek owns that
vocabulary inside its own route; the generic OpenAI transport stays neutral and
other providers keep current-main behavior byte-for-byte.

Official contract (api-docs.deepseek.com, verified 2026-08-09):

  Chat Completions
    * ``thinking.type`` is ``enabled|disabled``; omission defaults to enabled.
    * ``reasoning_effort`` accepts ``["low", "high", "max"]``; default ``high``.
    * "currently only ``deepseek-v4-flash`` supports the three effort levels"
    * "``deepseek-v4-pro`` temporarily supports only ``high`` and ``max``
      (``low`` is treated as ``high``, ``xhigh`` is treated as ``max``)"
    * "For compatibility, ``medium`` and ``xhigh`` are mapped to ``high``."

  Responses
    * "The Responses API currently only supports the ``deepseek-v4-flash``
      model, and does not yet support the ``deepseek-v4-pro`` model."
    * ``reasoning`` is partially supported: ``effort`` supported.  No
      compatibility alias and no thinking-disable is documented, so anything
      outside the canonical ``low|high|max`` fails closed locally.

``low`` on Pro is deliberately REJECTED rather than silently normalized: the
brief's product contract forbids advertising a level the model does not really
have, and the server-side "treated as high" leniency is not a documented
compatibility alias.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lingtai.agent import build_agent_config
from lingtai.init_schema import validate_init
from lingtai.kernel.config import AgentConfig, THINKING_LEVELS
from lingtai.kernel.llm.base import LLMResponse
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.kernel.session import SessionManager
from lingtai.llm._register import register_all_adapters
from lingtai.llm.service import LLMService


FLASH = "deepseek-v4-flash"
PRO = "deepseek-v4-pro"
UNKNOWN = "deepseek-v9-unreleased"


# ---------------------------------------------------------------------------
# helpers — real registered factory, real adapter, real session construction
# ---------------------------------------------------------------------------


def _deepseek_adapter(*, wire: str = "chat_completions"):
    """Build the REAL registered ``deepseek`` adapter for a wire."""
    register_all_adapters()
    factory = LLMService._adapter_registry["deepseek"]
    return factory(model=None, api_key="sk-test", defaults={"wire_api": wire})


def _extra(model: str, thinking: str, *, wire: str = "chat_completions") -> dict:
    """Return the exact per-request extra kwargs the adapter would send."""
    adapter = _deepseek_adapter(wire=wire)
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=model, system_prompt="sys", interface=iface, thinking=thinking
    )
    return dict(session._extra_kwargs)


def _thinking_switch(extra: dict):
    """Return DeepSeek's ``thinking`` switch as the SDK would send it.

    ``thinking`` is a DeepSeek body extension, NOT a named parameter of the
    OpenAI SDK's ``chat.completions.create``. It therefore travels in
    ``extra_body``, which the SDK merges into the top level of the request
    JSON — the shape DeepSeek requires.
    """
    return (extra.get("extra_body") or {}).get("thinking")


def _sdk_chat_signature():
    """The signature of the REAL installed OpenAI SDK Chat create call."""
    import inspect

    import openai

    client = openai.OpenAI(api_key="sk-test", base_url="https://api.deepseek.com")
    return inspect.signature(client.chat.completions.create)


def _applied(model: str, thinking: str, *, wire: str = "chat_completions"):
    """Return the immutable application result captured on the session."""
    adapter = _deepseek_adapter(wire=wire)
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=model, system_prompt="sys", interface=iface, thinking=thinking
    )
    return getattr(session, "reasoning_application", None)


def _init_data(llm: dict) -> dict:
    return {
        "manifest": {
            "agent_name": "test-agent",
            "language": "en",
            "llm": llm,
            "capabilities": {},
            "soul": {"delay": 60},
            "context_limit": None,
            "admin": {"karma": True},
            "streaming": False,
        },
        "principle": "",
        "covenant": "",
        "pad": "",
        "lingtai": "",
        "soul": "",
    }


# ---------------------------------------------------------------------------
# clause 2 — Chat Completions, deepseek-v4-flash
# ---------------------------------------------------------------------------


def test_chat_flash_auto_omits_every_reasoning_field():
    """Auto/omitted must send NO thinking switch and NO effort — DeepSeek's own
    provider default applies.  Current main fabricates ``xhigh``."""
    extra = _extra(FLASH, "default")
    assert "reasoning_effort" not in extra
    assert "thinking" not in extra


def test_chat_flash_none_disables_thinking_and_sends_no_effort():
    extra = _extra(FLASH, "none")
    assert _thinking_switch(extra) == {"type": "disabled"}
    assert "thinking" not in extra
    assert "reasoning_effort" not in extra


@pytest.mark.parametrize("level", ["low", "high", "max"])
def test_chat_flash_canonical_levels_enable_thinking_with_flat_effort(level):
    extra = _extra(FLASH, level)
    assert _thinking_switch(extra) == {"type": "enabled"}
    assert "thinking" not in extra
    assert extra["reasoning_effort"] == level


# ---------------------------------------------------------------------------
# clause 3 — Chat Completions, deepseek-v4-pro
# ---------------------------------------------------------------------------


def test_chat_pro_auto_omits_every_reasoning_field():
    extra = _extra(PRO, "default")
    assert "reasoning_effort" not in extra
    assert "thinking" not in extra


def test_chat_pro_none_disables_thinking_and_sends_no_effort():
    extra = _extra(PRO, "none")
    assert _thinking_switch(extra) == {"type": "disabled"}
    assert "thinking" not in extra
    assert "reasoning_effort" not in extra


@pytest.mark.parametrize("level", ["high", "max"])
def test_chat_pro_canonical_levels_enable_thinking_with_flat_effort(level):
    extra = _extra(PRO, level)
    assert _thinking_switch(extra) == {"type": "enabled"}
    assert "thinking" not in extra
    assert extra["reasoning_effort"] == level


def test_chat_pro_low_fails_before_any_sdk_call():
    """``low`` is not a real Pro capability; it must not reach the SDK."""
    with pytest.raises(ValueError) as exc:
        _extra(PRO, "low")
    msg = str(exc.value)
    assert "deepseek" in msg.lower()
    assert PRO in msg
    assert "low" in msg


# ---------------------------------------------------------------------------
# clause 4 — official Chat compatibility aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,alias,normalized",
    [
        (FLASH, "medium", "high"),
        (FLASH, "xhigh", "high"),
        (PRO, "medium", "high"),
        (PRO, "xhigh", "max"),
    ],
)
def test_chat_official_aliases_normalize_exactly_as_documented(model, alias, normalized):
    extra = _extra(model, alias)
    assert _thinking_switch(extra) == {"type": "enabled"}
    assert "thinking" not in extra
    assert extra["reasoning_effort"] == normalized


@pytest.mark.parametrize("model", [FLASH, PRO])
def test_chat_aliases_are_not_advertised_capability_levels(model):
    """Aliases are accepted on ingress but never offered as choices."""
    from lingtai.llm.deepseek.reasoning import canonical_levels

    levels = canonical_levels(model=model, wire="chat_completions")
    assert "medium" not in levels
    assert "xhigh" not in levels
    assert "minimal" not in levels
    assert levels == (("none", "low", "high", "max") if model == FLASH
                      else ("none", "high", "max"))


@pytest.mark.parametrize("model", [FLASH, PRO])
def test_chat_minimal_is_never_accepted(model):
    with pytest.raises(ValueError):
        _extra(model, "minimal")


# ---------------------------------------------------------------------------
# clause 5 — Responses wire, deepseek-v4-flash
# ---------------------------------------------------------------------------


def test_responses_flash_auto_omits_reasoning_entirely():
    extra = _extra(FLASH, "default", wire="responses")
    assert "reasoning" not in extra


@pytest.mark.parametrize("level", ["low", "high", "max"])
def test_responses_flash_canonical_levels_nest_under_reasoning_effort(level):
    extra = _extra(FLASH, level, wire="responses")
    assert extra["reasoning"] == {"effort": level}
    assert "reasoning_effort" not in extra


@pytest.mark.parametrize("value", ["none", "minimal", "medium", "xhigh", "insane", ""])
def test_responses_flash_rejects_undocumented_values(value):
    """No disable and no compatibility alias is documented for Responses."""
    with pytest.raises(ValueError):
        _extra(FLASH, value, wire="responses")


# ---------------------------------------------------------------------------
# clause 6 — Responses Pro and unknown DeepSeek models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["low", "high", "max", "none", "medium"])
def test_responses_pro_fails_before_sdk_for_any_explicit_effort(value):
    with pytest.raises(ValueError) as exc:
        _extra(PRO, value, wire="responses")
    msg = str(exc.value)
    assert PRO in msg
    assert "responses" in msg.lower()


def test_responses_pro_is_rejected_even_when_effort_is_omitted():
    """A2 (parent amendment 1): Responses supports Flash only.

    ``deepseek-v4-pro`` on the Responses wire is a KNOWN-unsupported route, so
    it must fail before adapter/session construction even with no effort
    configured — omission cannot make an impossible route succeed. This
    supersedes the earlier ``test_responses_pro_omission_adds_no_fields``,
    which asserted the opposite and contradicted the shipped claim that
    Pro+Responses fails at session construction.
    """
    with pytest.raises(ValueError) as exc:
        _extra(PRO, "default", wire="responses")
    msg = str(exc.value)
    assert PRO in msg
    assert "responses" in msg.lower()


def test_responses_pro_omission_is_rejected_through_real_session_ingress():
    manager, events = _session_manager(model=PRO, wire="responses")
    with pytest.raises(ValueError):
        manager.ensure_session()
    assert [t for t, _ in events if t == "llm_call"] == []


def test_unknown_model_omission_still_survives_on_both_wires():
    """A2 boundary: an UNKNOWN future model keeps omission/no-fields.

    Only known-unsupported routes fail closed on omission; the brief
    deliberately preserves unknown-model omission while rejecting explicit
    effort (already covered above).
    """
    for wire in ("chat_completions", "responses"):
        extra = _extra(UNKNOWN, "default", wire=wire)
        assert "reasoning" not in extra
        assert "reasoning_effort" not in extra
        assert "thinking" not in extra


@pytest.mark.parametrize("wire", ["chat_completions", "responses"])
def test_unknown_deepseek_model_omission_adds_no_reasoning_fields(wire):
    extra = _extra(UNKNOWN, "default", wire=wire)
    assert "reasoning" not in extra
    assert "reasoning_effort" not in extra
    assert "thinking" not in extra


@pytest.mark.parametrize("wire", ["chat_completions", "responses"])
def test_unknown_deepseek_model_explicit_effort_fails_before_sdk(wire):
    with pytest.raises(ValueError) as exc:
        _extra(UNKNOWN, "high", wire=wire)
    assert UNKNOWN in str(exc.value)


# ---------------------------------------------------------------------------
# clause 1 / clause 10 — real ingress omission (manifest, config, session)
# ---------------------------------------------------------------------------


def test_manifest_omitted_thinking_for_deepseek_hydrates_to_omission_sentinel():
    """DeepSeek owns its omitted-effort default, so hydration must not promote
    an omitted manifest value to the legacy cross-provider ``high``.

    This is also the native LingTai daemon's initial-omission parity route:
    the daemon builds its agent config through this same function.
    """
    manifest = _init_data({"provider": "deepseek", "model": FLASH, "api_key": "k"})["manifest"]
    cfg = build_agent_config(manifest, max_rpm=0)
    assert cfg.thinking == "default"


def test_manifest_explicit_thinking_for_deepseek_is_preserved():
    manifest = _init_data(
        {"provider": "deepseek", "model": FLASH, "api_key": "k", "thinking": "max"}
    )["manifest"]
    cfg = build_agent_config(manifest, max_rpm=0)
    assert cfg.thinking == "max"


#: Marks "no ``thinking=`` argument was passed at all" — distinct from an
#: explicit ``None``. Used to exercise pure constructor omission.
_UNSET = object()


def _session_manager(
    thinking=_UNSET, *, model=FLASH, wire="chat_completions", provider="deepseek"
):
    register_all_adapters()
    service = LLMService(
        provider=provider,
        model=model,
        api_key="sk-test",
        provider_defaults={provider: {"wire_api": wire}} if wire else None,
    )
    config_kw = {"provider": provider, "model": model}
    if thinking is not _UNSET:
        config_kw["thinking"] = thinking
    config = AgentConfig(**config_kw)
    events: list[tuple[str, dict]] = []
    manager = SessionManager(
        llm_service=service,
        config=config,
        agent_name="test-agent",
        streaming=False,
        build_system_prompt_fn=lambda: "sys",
        build_tool_schemas_fn=lambda: [],
        logger_fn=lambda event_type, **fields: events.append((event_type, fields)),
    )
    return manager, events


# --- A1: programmatic constructor omission (parent amendment 1) -------------


@pytest.mark.parametrize("wire", ["chat_completions", "responses"])
def test_programmatic_agentconfig_omission_reaches_deepseek_as_omission(wire):
    """``AgentConfig(provider="deepseek", model=...)`` with NO ``thinking=``.

    Constructor omission is Auto: it must be indistinguishable from an explicit
    ``None`` and must never be fabricated into ``high`` before the DeepSeek
    contract sees it.
    """
    manager, _ = _session_manager(wire=wire)
    chat = manager.ensure_session()
    extra = dict(chat._extra_kwargs)
    assert "reasoning" not in extra
    assert "reasoning_effort" not in extra
    assert "thinking" not in extra


def test_programmatic_agentconfig_omission_observes_omitted_provenance():
    manager, _ = _session_manager()
    chat = manager.ensure_session()
    applied = chat.reasoning_application
    assert applied.requested == "default"
    assert applied.normalized == "default"
    assert applied.emitted == "omitted"
    assert applied.provenance == "omitted"


def test_programmatic_agentconfig_omission_matches_explicit_none():
    """Constructor omission and explicit ``None`` must resolve identically."""
    omitted = _session_manager()[0].ensure_session()
    explicit_none = _session_manager(None)[0].ensure_session()
    assert dict(omitted._extra_kwargs) == dict(explicit_none._extra_kwargs)
    assert (
        omitted.reasoning_application.observation_fields()
        == explicit_none.reasoning_application.observation_fields()
    )


def test_programmatic_agentconfig_omission_keeps_legacy_high_off_deepseek():
    """A1 leakage guard: a default ``AgentConfig()`` on a non-DeepSeek route
    still reaches its adapter as the legacy ``high``."""
    manager, _ = _session_manager(model="gpt-4o", wire=None, provider="openai")
    chat = manager.ensure_session()
    assert chat._extra_kwargs["reasoning_effort"] == "high"
    assert getattr(chat, "reasoning_application", None) is None


def test_explicit_deepseek_high_survives_the_omission_change():
    """A1 regression: explicit values keep their existing intended semantics."""
    manager, _ = _session_manager("high")
    chat = manager.ensure_session()
    assert _thinking_switch(chat._extra_kwargs) == {"type": "enabled"}
    assert chat._extra_kwargs["reasoning_effort"] == "high"
    assert chat.reasoning_application.provenance == "explicit_config"


@pytest.mark.parametrize("wire", ["chat_completions", "responses"])
def test_config_none_thinking_reaches_deepseek_as_omission(wire):
    """An explicit ``None`` is Auto, not the legacy ``or "high"`` fallback."""
    manager, _ = _session_manager(None, wire=wire)
    chat = manager.ensure_session()
    extra = dict(chat._extra_kwargs)
    assert "reasoning" not in extra
    assert "reasoning_effort" not in extra
    assert "thinking" not in extra


@pytest.mark.parametrize("bad", ["", 0, False])
def test_config_falsey_non_none_thinking_is_invalid_for_deepseek(bad):
    """Empty string / False / 0 are invalid explicit values — never ``high``."""
    manager, _ = _session_manager(bad)
    with pytest.raises(ValueError):
        manager.ensure_session()


def test_non_deepseek_provider_keeps_legacy_high_fallback():
    """Leakage guard: the DeepSeek omission rule must not touch other providers.

    An OpenAI route with a falsey configured level still resolves to the legacy
    cross-provider ``high`` and still emits a flat ``reasoning_effort``.
    """
    register_all_adapters()
    service = LLMService(provider="openai", model="gpt-4o", api_key="sk-test")
    config = AgentConfig(provider="openai", model="gpt-4o", thinking=None)
    manager = SessionManager(
        llm_service=service,
        config=config,
        agent_name="a",
        streaming=False,
        build_system_prompt_fn=lambda: "sys",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    chat = manager.ensure_session()
    assert chat._extra_kwargs["reasoning_effort"] == "high"
    assert getattr(chat, "reasoning_application", None) is None


# ---------------------------------------------------------------------------
# clause 7 — manifest/init/preset validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,wire,value",
    [
        (FLASH, None, "none"),
        (FLASH, None, "low"),
        (FLASH, None, "high"),
        (FLASH, None, "max"),
        (FLASH, None, "medium"),
        (PRO, None, "max"),
        (PRO, None, "xhigh"),
        (FLASH, "responses", "low"),
        (FLASH, "responses", "max"),
    ],
)
def test_init_schema_accepts_the_routes_exact_raw_set(model, wire, value):
    llm = {"provider": "deepseek", "model": model, "api_key": "k", "thinking": value}
    if wire:
        llm["wire_api"] = wire
    validate_init(_init_data(llm))


@pytest.mark.parametrize(
    "model,wire,value",
    [
        (PRO, None, "low"),
        (FLASH, None, "minimal"),
        (PRO, "responses", "high"),
        (FLASH, "responses", "none"),
        (FLASH, "responses", "medium"),
        (UNKNOWN, None, "high"),
    ],
)
def test_init_schema_rejects_values_outside_the_route(model, wire, value):
    llm = {"provider": "deepseek", "model": model, "api_key": "k", "thinking": value}
    if wire:
        llm["wire_api"] = wire
    with pytest.raises(ValueError) as exc:
        validate_init(_init_data(llm))
    msg = str(exc.value)
    assert "deepseek" in msg.lower()
    assert model in msg


@pytest.mark.parametrize(
    "thinking,wire,ok",
    [
        ("max", None, True),
        ("none", None, True),
        ("xhigh", None, True),
        ("minimal", None, False),
        ("low", "responses", True),
        ("none", "responses", False),
    ],
)
def test_preset_validation_uses_the_deepseek_route(tmp_path, thinking, wire, ok):
    """A preset carrying DeepSeek thinking is validated against the real route.

    Chat Completions presets used to be rejected outright; Responses presets
    used to accept every kernel level.
    """
    import json

    from lingtai.agent import load_preset

    llm = {
        "provider": "deepseek",
        "model": FLASH,
        "api_key": "k",
        "thinking": thinking,
    }
    if wire:
        llm["wire_api"] = wire
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({
        "name": str(path),
        "description": {"summary": "deepseek preset"},
        "manifest": {"llm": llm, "capabilities": {}},
    }))

    if ok:
        assert load_preset(str(path))["manifest"]["llm"]["thinking"] == thinking
    else:
        with pytest.raises(ValueError, match="deepseek"):
            load_preset(str(path))


# --- A7: the superseded seven-tier projection knob (parent amendment 1) -----


def test_deepseek_factory_no_longer_defaults_the_dead_vocab_knob():
    """The provider-local controller owns model/wire semantics outright.

    ``reasoning_effort_vocab`` selects the GENERIC projection, which the
    DeepSeek route now bypasses entirely — defaulting it to ``seven_tier``
    advertised a control that no longer does anything.
    """
    adapter = _deepseek_adapter()
    assert adapter._reasoning_effort_vocab != "seven_tier"
    assert adapter._reasoning_effort_vocab == "openai"


def test_deepseek_factory_still_lifts_the_live_generic_knobs():
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    register_all_adapters()
    defaults = build_provider_defaults_from_manifest_llm(
        {
            "provider": "deepseek",
            "inject_reasoning_fallback": False,
            "prompt_cache_namespace": "custom-ns",
        },
        max_rpm=0,
    )
    factory = LLMService._adapter_registry["deepseek"]
    adapter = factory(model=FLASH, api_key="sk-test", defaults=defaults["deepseek"])
    assert adapter._inject_reasoning_fallback is False
    assert adapter._prompt_cache_namespace == "custom-ns"


def test_explicit_deepseek_reasoning_effort_vocab_fails_init_validation():
    """Fail closed and say why — never silently ignore a user's setting."""
    data = _init_data(
        {
            "provider": "deepseek",
            "model": FLASH,
            "api_key": "k",
            "reasoning_effort_vocab": "seven_tier",
        }
    )
    with pytest.raises(ValueError) as exc:
        validate_init(data)
    msg = str(exc.value)
    assert "reasoning_effort_vocab" in msg
    assert "deepseek" in msg.lower()


def test_explicit_deepseek_reasoning_effort_vocab_fails_preset_validation(tmp_path):
    import json

    from lingtai.agent import load_preset

    path = tmp_path / "ds-vocab.json"
    path.write_text(json.dumps({
        "name": str(path),
        "description": {"summary": "deepseek preset"},
        "manifest": {
            "llm": {
                "provider": "deepseek",
                "model": FLASH,
                "api_key": "k",
                "reasoning_effort_vocab": "seven_tier",
            },
            "capabilities": {},
        },
    }))
    with pytest.raises(ValueError, match="reasoning_effort_vocab"):
        load_preset(str(path))


@pytest.mark.parametrize("provider", ["openai", "custom"])
def test_reasoning_effort_vocab_still_works_for_other_routes(provider):
    """A7 leakage guard: the generic knob is untouched off the DeepSeek route."""
    register_all_adapters()
    data = _init_data(
        {
            "provider": provider,
            "model": "m",
            "api_key": "k",
            "base_url": "https://example.invalid/v1",
            "reasoning_effort_vocab": "seven_tier",
        }
    )
    validate_init(data)

    factory = LLMService._adapter_registry[provider]
    kw = {
        "model": "m",
        "api_key": "sk-test",
        "defaults": {"reasoning_effort_vocab": "seven_tier"},
    }
    if provider != "openai":
        kw["base_url"] = "https://example.invalid/v1"
    adapter = factory(**kw)
    assert adapter._reasoning_effort_vocab == "seven_tier"
    assert adapter._chat_reasoning_effort("default") == "xhigh"


# --- A8: generic service holds no provider branch (parent amendment 1) ------


def test_generic_llm_service_source_never_names_deepseek():
    """Structural: the provider switch must not live in the generic service.

    A provider name in ``llm/service.py`` is how Kimi/GLM/Claude branches would
    accumulate there. Resolution belongs to the already-selected adapter.
    """
    import pathlib

    import lingtai.llm.service as service_mod

    source = pathlib.Path(service_mod.__file__).read_text()
    assert "deepseek" not in source.lower()


def test_generic_llm_service_module_has_no_deepseek_import():
    """Dependency: no import of the provider package, at any nesting level."""
    import ast
    import pathlib

    import lingtai.llm.service as service_mod

    tree = ast.parse(pathlib.Path(service_mod.__file__).read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [f"{node.module or ''}.{a.name}" for a in node.names]
    assert not [m for m in imported if "deepseek" in m.lower()], imported


def test_adapter_hook_default_preserves_the_legacy_fallback():
    """Every adapter inherits the non-owning legacy rule unchanged."""
    from lingtai.kernel.config import LEGACY_MAIN_SESSION_THINKING

    register_all_adapters()
    adapter = LLMService._adapter_registry["openai"](
        model="m", api_key="sk-test", defaults={}
    )
    assert adapter.resolve_configured_thinking(None) == LEGACY_MAIN_SESSION_THINKING
    assert adapter.resolve_configured_thinking("") == LEGACY_MAIN_SESSION_THINKING
    assert adapter.resolve_configured_thinking("xhigh") == "xhigh"


def test_openai_adapter_delegates_the_hook_to_its_installed_controller():
    """DeepSeek's omission rule arrives through the controller, not a branch."""
    assert _deepseek_adapter().resolve_configured_thinking(None) == "default"
    # falsey-but-not-None stays verbatim so the contract can reject it
    assert _deepseek_adapter().resolve_configured_thinking("") == ""
    assert _deepseek_adapter().resolve_configured_thinking("max") == "max"


def test_create_session_consults_only_the_selected_adapter_hook():
    """Behavioral: whatever the selected adapter resolves is what is used."""
    from lingtai.llm.base import LLMAdapter

    seen: dict = {}

    class _StubAdapter(LLMAdapter):
        def resolve_configured_thinking(self, thinking):
            return "adapter-owned"

        def create_chat(self, model, system_prompt, **kwargs):
            seen["thinking"] = kwargs.get("thinking")
            return MagicMock()

        def generate(self, *a, **kw):  # pragma: no cover - unused
            raise NotImplementedError

        def make_tool_result_message(self, *a, **kw):  # pragma: no cover
            raise NotImplementedError

        def is_quota_error(self, *a, **kw):  # pragma: no cover
            return False

    service = LLMService(provider="openai", model="m", api_key="sk-test")
    service.get_adapter = lambda *a, **kw: _StubAdapter()
    service.create_session(system_prompt="sys", thinking=None, tracked=False)
    assert seen["thinking"] == "adapter-owned"


# --- A9: conflicting extra_body must not falsify observation ----------------


def _assert_decision_is_consistent(session, *, emitted, effort):
    """The wire, the captured application, and observation must agree."""
    body = session._extra_kwargs.get("extra_body") or {}
    applied = session.reasoning_application
    fields = applied.observation_fields()
    assert body["thinking"] == {"type": emitted}
    assert applied.payload["extra_body"]["thinking"] == {"type": emitted}
    assert fields["effort_emitted"] == effort
    assert session._extra_kwargs.get("reasoning_effort") == (
        None if effort == "disabled" else effort
    )


def test_conflicting_caller_extra_body_keeps_wire_and_observation_in_step():
    """A controller-owned key stays authoritative; unrelated keys survive."""
    adapter = _deepseek_adapter()
    extra_kwargs = {
        "extra_body": {
            "thinking": {"type": "disabled"},  # a lie the controller must win
            "unrelated_caller_key": "kept",
        }
    }
    applied = adapter._apply_reasoning_control(
        model=FLASH, wire="chat_completions", thinking="max",
        extra_kwargs=extra_kwargs,
    )
    body = extra_kwargs["extra_body"]
    assert body["thinking"] == {"type": "enabled"}
    assert body["unrelated_caller_key"] == "kept"
    assert extra_kwargs["reasoning_effort"] == "max"
    assert applied.observation_fields()["effort_emitted"] == "max"


def test_conflicting_subclass_extra_body_keeps_wire_and_observation_in_step():
    """A subclass ``_adapter_extra_body()`` must not rewrite the decision."""
    adapter = _deepseek_adapter()
    adapter._adapter_extra_body = lambda: {
        "thinking": {"type": "disabled"},
        "subclass_key": "kept",
    }
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=FLASH, system_prompt="sys", interface=iface, thinking="max"
    )
    assert session._extra_kwargs["extra_body"]["subclass_key"] == "kept"
    _assert_decision_is_consistent(session, emitted="enabled", effort="max")
    _sdk_chat_signature().bind(
        model=FLASH, messages=[], **dict(session._extra_kwargs)
    )


def test_conflicting_extra_body_on_a_disabled_decision_stays_consistent():
    adapter = _deepseek_adapter()
    adapter._adapter_extra_body = lambda: {
        "thinking": {"type": "enabled"},
        "subclass_key": "kept",
    }
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=FLASH, system_prompt="sys", interface=iface, thinking="none"
    )
    assert session._extra_kwargs["extra_body"]["subclass_key"] == "kept"
    _assert_decision_is_consistent(session, emitted="disabled", effort="disabled")


def test_conflicting_extra_body_leaves_responses_wire_untouched():
    adapter = _deepseek_adapter(wire="responses")
    adapter._adapter_extra_body = lambda: {"subclass_key": "kept"}
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=FLASH, system_prompt="sys", interface=iface, thinking="max"
    )
    assert session._extra_kwargs["reasoning"] == {"effort": "max"}
    assert session.reasoning_application.observation_fields()["effort_emitted"] == "max"


def test_global_thinking_levels_are_not_extended():
    """No DeepSeek vocabulary leaks into the kernel-global tuple."""
    assert THINKING_LEVELS == (
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    )


# ---------------------------------------------------------------------------
# clause 8 — exact observation derived from the real applied result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,wire,thinking,normalized,emitted,provenance",
    [
        (FLASH, "chat_completions", "default", "default", "omitted", "omitted"),
        (FLASH, "chat_completions", "none", "none", "disabled", "explicit_config"),
        (FLASH, "chat_completions", "low", "low", "low", "explicit_config"),
        (FLASH, "chat_completions", "xhigh", "high", "high", "compat_alias"),
        (PRO, "chat_completions", "xhigh", "max", "max", "compat_alias"),
        (FLASH, "responses", "default", "default", "omitted", "omitted"),
        (FLASH, "responses", "max", "max", "max", "explicit_config"),
    ],
)
def test_applied_result_records_requested_normalized_emitted(
    model, wire, thinking, normalized, emitted, provenance
):
    applied = _applied(model, thinking, wire=wire)
    assert applied is not None
    assert applied.provider == "deepseek"
    assert applied.wire == wire
    assert applied.requested == thinking
    assert applied.normalized == normalized
    assert applied.emitted == emitted
    assert applied.provenance == provenance


def test_llm_call_observation_comes_from_the_applied_result(monkeypatch):
    """The llm_call event must report the REAL applied patch, not raw config."""
    import lingtai.kernel.session as session_mod

    manager, events = _session_manager("xhigh", model=PRO)
    monkeypatch.setattr(
        session_mod, "send_with_timeout", lambda **kw: LLMResponse(text="ok")
    )
    manager.send("hello")

    calls = [f for t, f in events if t == "llm_call"]
    assert calls, "no llm_call event was logged"
    fields = calls[-1]
    assert fields["provider"] == "deepseek"
    assert fields["wire"] == "chat_completions"
    assert fields["effort_requested"] == "xhigh"
    assert fields["effort_normalized"] == "max"
    assert fields["effort_emitted"] == "max"
    assert fields["effort_provenance"] == "compat_alias"
    # bounded, plain strings only — no payloads, prompts, or credentials
    assert all(isinstance(v, str) for v in fields.values())


# --- A3: the captured application is deeply immutable (parent amendment 1) ---


@pytest.mark.parametrize(
    "model,wire,thinking,path",
    [
        (FLASH, "chat_completions", "max", ("extra_body", "thinking")),
        (FLASH, "responses", "max", ("reasoning",)),
    ],
)
def test_captured_payload_is_recursively_immutable(model, wire, thinking, path):
    """Every nesting level of the captured payload must reject mutation."""
    applied = _applied(model, thinking, wire=wire)
    node = applied.payload
    for key in path:
        with pytest.raises(TypeError):
            node["injected"] = "tampered"
        node = node[key]
    with pytest.raises(TypeError):
        node["type"] = "tampered"


def test_request_kwargs_returns_a_deep_mutable_copy():
    """The SDK needs plain mutable kwargs; the capture must stay frozen."""
    applied = _applied(FLASH, "max")
    kwargs = applied.request_kwargs()

    assert kwargs == {
        "reasoning_effort": "max",
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    assert type(kwargs) is dict
    assert type(kwargs["extra_body"]) is dict
    assert type(kwargs["extra_body"]["thinking"]) is dict

    kwargs["extra_body"]["thinking"]["type"] = "disabled"
    kwargs["reasoning_effort"] = "tampered"
    kwargs["injected"] = True

    assert applied.payload["extra_body"]["thinking"]["type"] == "enabled"
    assert applied.payload["reasoning_effort"] == "max"
    assert "injected" not in applied.payload
    assert applied.observation_fields()["effort_emitted"] == "max"

    # each call hands out an independent copy
    assert (
        applied.request_kwargs()["extra_body"]["thinking"]
        is not kwargs["extra_body"]["thinking"]
    )


def test_mutating_session_request_kwargs_cannot_alter_the_capture():
    """The adapter must merge a COPY, not alias the frozen payload."""
    adapter = _deepseek_adapter()
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=FLASH, system_prompt="sys", interface=iface, thinking="max"
    )
    applied = session.reasoning_application

    # what the SDK receives is plain and mutable...
    assert type(session._extra_kwargs["extra_body"]["thinking"]) is dict
    session._extra_kwargs["extra_body"]["thinking"]["type"] = "disabled"
    session._extra_kwargs["reasoning_effort"] = "tampered"

    # ...but the captured application and its observation are untouched
    assert applied.payload["extra_body"]["thinking"]["type"] == "enabled"
    assert applied.payload["reasoning_effort"] == "max"
    assert applied.observation_fields() == {
        "provider": "deepseek",
        "wire": "chat_completions",
        "effort_requested": "max",
        "effort_normalized": "max",
        "effort_emitted": "max",
        "effort_provenance": "explicit_config",
    }


# --- A6: the real OpenAI SDK Chat signature (parent amendment 1) ------------


def test_installed_sdk_chat_signature_has_no_thinking_and_no_var_kwargs():
    """Pin the constraint that makes A6 a real wire blocker, not a style point."""
    import inspect

    sig = _sdk_chat_signature()
    assert "thinking" not in sig.parameters
    assert "reasoning_effort" in sig.parameters
    assert "extra_body" in sig.parameters
    assert not any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


@pytest.mark.parametrize("thinking", ["max", "none", "high"])
def test_composed_chat_request_binds_against_the_real_sdk_signature(thinking):
    """The exact composed request must be callable on the REAL SDK.

    A permissive fake client accepts anything; the installed SDK raises
    ``TypeError`` before any HTTP request for an unexpected keyword. Binding the
    real signature is the only honest local proof that the request is well
    formed.
    """
    extra = _extra(FLASH, thinking)
    assert "thinking" not in extra, (
        "``thinking`` must never be a top-level SDK keyword — "
        "the installed SDK has no such parameter and no **kwargs"
    )
    _sdk_chat_signature().bind(model=FLASH, messages=[], **extra)


def test_chat_thinking_travels_in_extra_body_with_native_flat_effort():
    extra = _extra(FLASH, "max")
    assert extra["extra_body"] == {"thinking": {"type": "enabled"}}
    assert extra["reasoning_effort"] == "max"
    assert "thinking" not in extra


def test_chat_disabled_thinking_travels_in_extra_body():
    extra = _extra(FLASH, "none")
    assert extra["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in extra
    assert "thinking" not in extra


def test_responses_payload_shape_is_unchanged_by_the_extra_body_move():
    """A6 must not disturb the Responses wire, which has no ``thinking``."""
    extra = _extra(FLASH, "max", wire="responses")
    assert extra["reasoning"] == {"effort": "max"}
    assert "extra_body" not in extra


def test_reasoning_extra_body_merge_preserves_caller_and_subclass_fields():
    """Composition must not lose either side of ``extra_body``."""
    adapter = _deepseek_adapter()
    # a subclass/provider contribution, as OpenRouter does
    adapter._adapter_extra_body = lambda: {"subclass_key": "kept"}
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=FLASH, system_prompt="sys", interface=iface, thinking="max"
    )
    body = session._extra_kwargs["extra_body"]
    assert body["thinking"] == {"type": "enabled"}
    assert body["subclass_key"] == "kept"
    _sdk_chat_signature().bind(
        model=FLASH, messages=[], **dict(session._extra_kwargs)
    )


def test_reasoning_merge_does_not_clobber_a_preexisting_extra_body():
    """The neutral merge composes rather than overwrites."""
    from lingtai.llm.deepseek.reasoning import DeepSeekReasoningController

    adapter = _deepseek_adapter()
    extra_kwargs = {"extra_body": {"caller_key": "kept"}}
    applied = adapter._apply_reasoning_control(
        model=FLASH,
        wire="chat_completions",
        thinking="max",
        extra_kwargs=extra_kwargs,
    )
    assert applied is not None
    assert extra_kwargs["extra_body"]["caller_key"] == "kept"
    assert extra_kwargs["extra_body"]["thinking"] == {"type": "enabled"}
    assert "thinking" not in extra_kwargs
    assert isinstance(adapter._reasoning_controller, DeepSeekReasoningController)


def test_rejected_effort_is_never_observed_because_no_call_is_made():
    manager, events = _session_manager("low", model=PRO)
    with pytest.raises(ValueError):
        manager.ensure_session()
    assert [t for t, _ in events if t == "llm_call"] == []


# ---------------------------------------------------------------------------
# clause 9 — REST and streaming share one physical builder
# ---------------------------------------------------------------------------


def test_rest_and_streaming_emit_identical_reasoning_fields():
    """Both send paths read the ONE per-session extra-kwargs dict.

    There is no separate streaming reasoning builder to keep in sync, so this
    drives the real REST and streaming request builders and compares what each
    actually put on the wire.
    """
    from tests._chat_completion_helpers import make_raw_response

    adapter = _deepseek_adapter()
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter.create_chat(
        model=FLASH, system_prompt="sys", interface=iface, thinking="max"
    )

    client = MagicMock()
    client.chat.completions.create.return_value = make_raw_response(content="ok")
    session._client = client
    session.send("hello")
    rest_kwargs = client.chat.completions.create.call_args.kwargs

    stream_client = MagicMock()
    stream_client.chat.completions.create.return_value = iter(())
    session._client = stream_client
    session.send_stream("hello again")
    stream_kwargs = stream_client.chat.completions.create.call_args.kwargs

    reasoning_keys = ("thinking", "reasoning_effort", "extra_body")
    rest_reasoning = {k: rest_kwargs.get(k) for k in reasoning_keys}
    stream_reasoning = {k: stream_kwargs.get(k) for k in reasoning_keys}

    assert rest_reasoning == {
        "thinking": None,
        "reasoning_effort": "max",
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    assert stream_reasoning == rest_reasoning
    assert stream_kwargs["stream"] is True


# ---------------------------------------------------------------------------
# clause 11 — soul's explicit "high"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,wire,expected",
    [
        (FLASH, "chat_completions",
         {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "high"}),
        (PRO, "chat_completions",
         {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "high"}),
        (FLASH, "responses", {"reasoning": {"effort": "high"}}),
    ],
)
def test_soul_explicit_high_remains_valid(model, wire, expected):
    """Soul passes a literal ``thinking="high"``; it must stay supported.

    The exact emitted shape is pinned per wire so this cannot pass for the
    wrong reason.
    """
    extra = _extra(model, "high", wire=wire)
    assert {k: extra.get(k) for k in expected} == expected


# ---------------------------------------------------------------------------
# clause 12 — no leakage into other OpenAI-compatible routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "custom", "kimi", "grok", "qwen"])
def test_other_providers_get_no_deepseek_reasoning_controller(provider):
    register_all_adapters()
    factory = LLMService._adapter_registry[provider]
    kw: dict = {"model": "m", "api_key": "sk-test", "defaults": {}}
    if provider != "openai":
        # the custom-compat family requires an explicit endpoint
        kw["base_url"] = "https://example.invalid/v1"
    adapter = factory(**kw)
    assert getattr(adapter, "_reasoning_controller", None) is None


def test_generic_seven_tier_vocab_behavior_is_unchanged():
    """A non-DeepSeek provider on the seven_tier vocab keeps current-main
    semantics: omitted -> explicit xhigh, levels pass through."""
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="m", api_key="sk-test", defaults={"reasoning_effort_vocab": "seven_tier"}
    )
    assert adapter._chat_reasoning_effort("default") == "xhigh"
    assert adapter._chat_reasoning_effort("none") == "none"
    assert adapter._chat_reasoning_effort("medium") == "medium"


def test_codex_responses_reasoning_kwargs_are_unchanged():
    from lingtai.llm.openai.adapter import _responses_reasoning_kwargs

    assert _responses_reasoning_kwargs("default") == {"reasoning": {"effort": "xhigh"}}
    assert _responses_reasoning_kwargs("none") == {"reasoning": {"effort": "none"}}
    assert _responses_reasoning_kwargs("medium") == {"reasoning": {"effort": "medium"}}
