"""DeepSeek configured-effort contract — two axes (mode + effort), two wires.

DeepSeek V4 is a *hybrid* family: `deepseek-v4-flash` / `deepseek-v4-pro` is a
model choice, NOT the thinking switch. Mode and effort are independent, and the
two wires spell them differently:

* Chat Completions — mode is ``thinking: {"type": "enabled"|"disabled"}``; effort
  is the flat, canonical ``reasoning_effort`` ∈ ``low | high | max``.
* Responses (currently Flash-only upstream) — one nested field
  ``reasoning: {"effort": ...}`` accepting exactly
  ``none | minimal | low | medium | high | xhigh | max``; ``none`` disables.

Omission is a first-class state: an omitted ``manifest.llm.thinking`` resolves to
the internal ``"default"`` sentinel and DeepSeek must then emit **no** mode or
effort field at all, so the documented upstream default (thinking enabled,
effort high) applies. Omission must mean omission — not an accidental explicit
``reasoning_effort: "high"``.

These tests freeze that contract before any wire is sent. See
``workspace/effort-live-control/deepseek-effort-audit.md`` (source-verified
2026-08-05) for the upstream matrix and for the two headline defects this file
exists to prevent: the generic OpenAI Chat mapper collapses ``max`` to ``"low"``
and turns ``none`` into ``"low"`` instead of disabling thinking.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lingtai.kernel.config import (
    DEEPSEEK_CAPABILITY_SOURCE,
    DEEPSEEK_CHAT_THINKING_LEVELS,
    DEEPSEEK_RESPONSES_MODELS,
    DEEPSEEK_RESPONSES_THINKING_LEVELS,
    DEEPSEEK_THINKING_PROVIDERS,
    THINKING_LEVELS,
    THINKING_PROVIDERS,
    AgentConfig,
    deepseek_reasoning_log_fields,
    llm_supports_thinking,
    resolve_deepseek_wire,
    thinking_omitted,
)
from lingtai.llm.deepseek.effort import (
    deepseek_chat_reasoning_kwargs,
    deepseek_responses_model_supported,
    deepseek_responses_reasoning_kwargs,
)


# ---------------------------------------------------------------------------
# Vocabulary isolation — the global tuple must NOT learn DeepSeek's ``max``
# ---------------------------------------------------------------------------


def test_global_thinking_levels_unchanged():
    """DeepSeek's ``max`` must not leak into the cross-provider vocabulary."""
    assert THINKING_LEVELS == ("none", "minimal", "low", "medium", "high", "xhigh")
    assert "max" not in THINKING_LEVELS


def test_global_thinking_providers_unchanged():
    """DeepSeek is NOT a Codex-family provider; keep the Codex tuple exact."""
    assert THINKING_PROVIDERS == ("codex", "codex-pool", "codex_pool")
    assert "deepseek" not in THINKING_PROVIDERS


def test_deepseek_vocabularies_are_provider_local():
    assert DEEPSEEK_THINKING_PROVIDERS == ("deepseek",)
    assert DEEPSEEK_CHAT_THINKING_LEVELS == ("none", "low", "high", "max")
    assert DEEPSEEK_RESPONSES_THINKING_LEVELS == (
        "none", "minimal", "low", "medium", "high", "xhigh", "max",
    )
    # Source-dated capability claim: Responses is Flash-only as of the audit.
    assert DEEPSEEK_RESPONSES_MODELS == ("deepseek-v4-flash",)
    assert DEEPSEEK_CAPABILITY_SOURCE == "deepseek_docs_20260805"


# ---------------------------------------------------------------------------
# Capability gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "llm",
    [
        {"provider": "deepseek", "model": "deepseek-v4-pro"},
        {"provider": "deepseek", "model": "deepseek-v4-flash", "wire_api": "responses"},
        {"provider": "DeepSeek", "model": "deepseek-v4-pro", "wire_api": "auto"},
    ],
)
def test_llm_supports_thinking_accepts_deepseek(llm):
    assert llm_supports_thinking(llm) is True


@pytest.mark.parametrize(
    "llm",
    [
        {"provider": "anthropic", "model": "claude"},
        {"provider": "openai", "model": "gpt-5.5", "wire_api": "responses"},
        {"provider": "custom", "api_compat": "anthropic", "wire_api": "responses"},
    ],
)
def test_llm_supports_thinking_still_rejects_others(llm):
    assert llm_supports_thinking(llm) is False


@pytest.mark.parametrize(
    "wire_api,expected",
    [
        (None, "chat_completions"),
        ("auto", "chat_completions"),
        ("chat_completions", "chat_completions"),
        ("responses", "responses"),
        ("RESPONSES", "responses"),
    ],
)
def test_resolve_deepseek_wire(wire_api, expected):
    assert resolve_deepseek_wire(wire_api) == expected


# ---------------------------------------------------------------------------
# Chat Completions emission — mode + canonical flat effort
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("thinking", [None, "default"])
def test_chat_omission_emits_nothing(thinking):
    """Omission means omission: no mode field, no flat effort field."""
    assert deepseek_chat_reasoning_kwargs(thinking) == {}


def test_chat_none_disables_mode_and_sends_no_effort():
    """``none`` is the mode axis, not the effort axis (audit defect #3)."""
    kwargs = deepseek_chat_reasoning_kwargs("none")
    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("thinking", ["low", "high", "max"])
def test_chat_enabled_sends_mode_plus_exact_canonical_effort(thinking):
    """``max`` must never collapse to ``low`` (generic-mapper defect)."""
    assert deepseek_chat_reasoning_kwargs(thinking) == {
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": thinking,
    }


@pytest.mark.parametrize(
    "thinking",
    ["minimal", "medium", "xhigh", "High", "HIGH", " high ", "", "ultra", 1, True, 0.5, ["high"]],
)
def test_chat_rejects_non_canonical_values(thinking):
    """Chat aliases are rejected, never silently normalized."""
    with pytest.raises(ValueError, match="DeepSeek Chat Completions thinking"):
        deepseek_chat_reasoning_kwargs(thinking)


# ---------------------------------------------------------------------------
# Responses emission — nested seven-value effort, Flash-only guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("thinking", [None, "default"])
def test_responses_omission_emits_nothing(thinking):
    assert deepseek_responses_reasoning_kwargs(thinking, model="deepseek-v4-flash") == {}


@pytest.mark.parametrize(
    "thinking", ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
)
def test_responses_emits_exact_nested_effort(thinking):
    assert deepseek_responses_reasoning_kwargs(
        thinking, model="deepseek-v4-flash"
    ) == {"reasoning": {"effort": thinking}}


@pytest.mark.parametrize("thinking", ["Low", "ultra", "", " high ", 1, True, "enabled"])
def test_responses_rejects_out_of_vocabulary(thinking):
    with pytest.raises(ValueError, match="DeepSeek Responses thinking"):
        deepseek_responses_reasoning_kwargs(thinking, model="deepseek-v4-flash")


@pytest.mark.parametrize(
    "model,supported",
    [
        ("deepseek-v4-flash", True),
        ("DeepSeek-V4-Flash", True),
        ("deepseek-v4-pro", False),
        ("deepseek-chat", False),
        ("", False),
        (None, False),
    ],
)
def test_responses_model_support_table(model, supported):
    assert deepseek_responses_model_supported(model) is supported


def test_responses_rejects_pro_model():
    """Pro + Responses is upstream-unsupported today — reject, never coerce."""
    with pytest.raises(ValueError, match="DeepSeek Responses"):
        deepseek_responses_reasoning_kwargs("high", model="deepseek-v4-pro")


def test_responses_model_guard_skipped_when_model_unknown():
    """A caller with no model in hand still gets the vocabulary check."""
    assert deepseek_responses_reasoning_kwargs("high", model=None) == {
        "reasoning": {"effort": "high"}
    }


def test_responses_validates_vocabulary_before_model_guard():
    """A bad value on Pro reports the value, not the model."""
    with pytest.raises(ValueError, match="DeepSeek Responses thinking"):
        deepseek_responses_reasoning_kwargs("ultra", model="deepseek-v4-pro")


# ---------------------------------------------------------------------------
# Observability (brief §2.5) — derived only from the frozen construction value
# ---------------------------------------------------------------------------


def test_log_fields_for_omission():
    fields = deepseek_reasoning_log_fields("default")
    assert fields == {
        "reasoning_requested": "omitted",
        "reasoning_normalized": "omitted",
        "reasoning_actual": "omitted",
        "reasoning_source": "lingtai_deepseek_omitted",
        "reasoning_capability_source": DEEPSEEK_CAPABILITY_SOURCE,
    }


def test_log_fields_for_explicit_chat_value():
    fields = deepseek_reasoning_log_fields("max")
    assert fields["reasoning_requested"] == "max"
    assert fields["reasoning_normalized"] == "max"
    assert fields["reasoning_actual"] == "max"
    assert fields["reasoning_source"] == "explicit_config"
    assert fields["reasoning_capability_source"] == DEEPSEEK_CAPABILITY_SOURCE


def test_log_fields_carry_no_secrets():
    fields = deepseek_reasoning_log_fields("high")
    assert set(fields) == {
        "reasoning_requested",
        "reasoning_normalized",
        "reasoning_actual",
        "reasoning_source",
        "reasoning_capability_source",
    }


# ---------------------------------------------------------------------------
# Omission sentinel and session construction
# ---------------------------------------------------------------------------


def test_agent_config_default_thinking_is_the_omission_sentinel():
    """Byte-identical to ``"high"`` for every existing consumer, but detectable."""
    cfg = AgentConfig()
    assert cfg.thinking == "high"
    assert isinstance(cfg.thinking, str)
    assert (cfg.thinking or "high") == "high"
    assert thinking_omitted(cfg.thinking) is True


@pytest.mark.parametrize("value", ["high", "low", "default", "max"])
def test_explicit_thinking_is_not_omitted(value):
    assert thinking_omitted(AgentConfig(thinking=value).thinking) is False


def test_build_agent_config_maps_omitted_deepseek_thinking_to_default():
    from lingtai.agent import build_agent_config

    config = build_agent_config(
        {"llm": {"provider": "deepseek", "model": "deepseek-v4-pro"}}, max_rpm=0
    )
    assert config.thinking == "default"


def test_build_agent_config_passes_explicit_deepseek_thinking_through():
    from lingtai.agent import build_agent_config

    config = build_agent_config(
        {"llm": {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "max"}},
        max_rpm=0,
    )
    assert config.thinking == "max"


def test_build_agent_config_keeps_legacy_high_for_other_providers():
    from lingtai.agent import build_agent_config

    config = build_agent_config(
        {"llm": {"provider": "anthropic", "model": "claude"}}, max_rpm=0
    )
    assert config.thinking == "high"


def _session_manager(config):
    from lingtai.kernel.session import SessionManager

    service = MagicMock()
    service.model = "deepseek-v4-pro"
    service.provider = config.provider or "deepseek"
    return SessionManager(
        llm_service=service,
        config=config,
        agent_name="tester",
        streaming=False,
        build_system_prompt_fn=lambda: "sys",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    ), service


def test_session_construction_maps_deepseek_omission_to_default():
    """SessionManager must not promote DeepSeek omission to legacy ``"high"``."""
    manager, service = _session_manager(AgentConfig(provider="deepseek"))
    manager.ensure_session()
    assert service.create_session.call_args.kwargs["thinking"] == "default"


def test_session_construction_passes_explicit_deepseek_value():
    manager, service = _session_manager(
        AgentConfig(provider="deepseek", thinking="max")
    )
    manager.ensure_session()
    assert service.create_session.call_args.kwargs["thinking"] == "max"


def test_session_construction_keeps_legacy_high_for_other_providers():
    """Byte-identical legacy behavior for every other non-Codex provider."""
    manager, service = _session_manager(AgentConfig(provider="anthropic"))
    manager.ensure_session()
    assert service.create_session.call_args.kwargs["thinking"] == "high"


def test_session_rebuild_preserves_deepseek_omission():
    from lingtai.kernel.llm.interface import ChatInterface

    manager, service = _session_manager(AgentConfig(provider="deepseek"))
    manager._rebuild_session(ChatInterface())
    assert service.create_session.call_args.kwargs["thinking"] == "default"


# ---------------------------------------------------------------------------
# Soul call sites pass an explicit thinking="high" (tools/soul/inquiry.py:44,
# tools/soul/consultation.py:452). That value is VALID DeepSeek vocabulary on
# both wires, so this slice needs no soul change.
# ---------------------------------------------------------------------------


def test_soul_style_explicit_high_is_valid_on_both_wires():
    assert deepseek_chat_reasoning_kwargs("high") == {
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": "high",
    }
    assert deepseek_responses_reasoning_kwargs("high", model="deepseek-v4-flash") == {
        "reasoning": {"effort": "high"}
    }
