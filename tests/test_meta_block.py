"""Tests for meta_block — unified per-turn metadata injection."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
import lingtai.kernel.meta_block as meta_block

from lingtai.kernel.meta_block import (
    GUIDANCE_KEY,
    TOOL_META_TOKEN_USAGE_PENDING_KEY,
    GuidanceSchemaError,
    attach_active_notifications,
    attach_active_runtime,
    build_cache_miss_budget_context,
    build_meta,
    build_meta_guidance,
    build_meta_readme,
    build_context_rebuild_hint,
    build_molt_context,
    build_notification_payload,
    build_synthetic_meta_envelope,
    build_tool_meta_token_usage,
    build_guidance_with_meta_readme,
    build_runtime_guidance,
    clear_active_notification_holder,
    current_tool_result_chars,
    render_meta,
    slim_adapter_comment_for_tail,
    stamp_meta,
    static_adapter_comment,
    dynamic_adapter_comment,
    validate_runtime_guidance,
)
from lingtai.kernel.llm.interface import ToolResultBlock


def _fake_agent(*, time_awareness: bool = True, timezone_awareness: bool = True):
    """Minimal agent stand-in: build_meta only reads agent._config.*."""
    return SimpleNamespace(
        _config=SimpleNamespace(
            time_awareness=time_awareness,
            timezone_awareness=timezone_awareness,
        )
    )


def test_build_meta_time_aware_local_tz_has_offset():
    agent = _fake_agent(time_awareness=True, timezone_awareness=True)
    meta = build_meta(agent)
    assert "current_time" in meta
    ts = meta["current_time"]
    assert not ts.endswith("Z"), f"expected local offset, got {ts!r}"
    assert re.search(r"[+-]\d{2}:\d{2}$", ts), f"no ±HH:MM suffix in {ts!r}"


def test_build_meta_time_aware_utc_uses_z_suffix():
    agent = _fake_agent(time_awareness=True, timezone_awareness=False)
    meta = build_meta(agent)
    assert meta["current_time"].endswith("Z")


def test_build_meta_time_blind_omits_context_without_warning():
    agent = _fake_agent(time_awareness=False)
    meta = build_meta(agent)
    assert "current_time" not in meta
    assert "context" not in meta


def test_build_meta_time_blind_regardless_of_timezone_awareness():
    # time_awareness=False short-circuits even when timezone_awareness=True.
    agent = _fake_agent(time_awareness=False, timezone_awareness=True)
    meta = build_meta(agent)
    assert "current_time" not in meta
    assert "context" not in meta


def test_build_meta_includes_adapter_comment_when_chat_provides_one():
    agent = _fake_agent()
    calls = {"legacy": 0, "dynamic": 0}

    def legacy_comment():
        calls["legacy"] += 1
        return {
            "adapter": "fake",
            "summary": "legacy static provider note",
            "cache_note": "legacy static cache prose",
        }

    def dynamic_comment():
        calls["dynamic"] += 1
        return {
            "adapter": "fake",
            "summary": "dynamic summary is not kernel-guessed static",
            "turns_since_epoch_reset": 2,
        }

    agent._session = SimpleNamespace(
        chat=SimpleNamespace(
            adapter_comment=legacy_comment,
            dynamic_adapter_comment=dynamic_comment,
        ),
        _token_decomp_dirty=True,
        _system_prompt_tokens=0,
        _tools_tokens=0,
        _latest_input_tokens=0,
        _update_token_decomposition=lambda: None,
    )

    meta = build_meta(agent)

    tail = meta["adapter_comment"]
    assert calls == {"legacy": 0, "dynamic": 1}
    assert tail["adapter"] == "fake"
    assert tail["summary"] == "dynamic summary is not kernel-guessed static"
    assert tail["turns_since_epoch_reset"] == 2
    assert "cache_note" not in tail
    assert "meta_guidance_ref" not in tail

def test_build_meta_omits_empty_adapter_comment():
    agent = _fake_agent()
    agent._session = SimpleNamespace(
        chat=SimpleNamespace(adapter_comment=lambda: None),
        _token_decomp_dirty=True,
        _system_prompt_tokens=0,
        _tools_tokens=0,
        _latest_input_tokens=0,
        _update_token_decomposition=lambda: None,
    )

    meta = build_meta(agent)

    assert "adapter_comment" not in meta


def test_build_meta_counts_current_tool_result_chars_excluding_meta():
    formal_payload = {"payload": "X" * 1200}
    tool_block = ToolResultBlock(
        id="tc-history",
        name="bash",
        content={
            **formal_payload,
            "_meta": {
                "notifications": {"system": {"body": "N" * 1000}},
                "guidance": {
                    "sections": [
                        {"id": "meta_readme", "title": "_meta envelope readme", "body": ""}
                    ]
                },
            },
        },
    )
    agent = _fake_agent()
    agent._config.context_limit = 1_000_000
    agent._cached_sys_prompt_tokens = 0
    agent._cached_tool_schema_tokens = 0
    agent._session = SimpleNamespace(
        _token_decomp_dirty=False,
        _system_prompt_tokens=0,
        _tools_tokens=0,
        _context_tokens=0,
        _latest_input_tokens=0,
        _tool_schema_tokens=0,
        _context_section_tokens=0,
        chat=SimpleNamespace(
            interface=SimpleNamespace(_entries=[SimpleNamespace(content=[tool_block])]),
            context_window=lambda: 1_000_000,
        ),
    )

    meta = build_meta(agent)

    current = meta["current_tool_result_chars"]
    expected = len(json.dumps(formal_payload, ensure_ascii=False, default=str))
    assert "_readme" not in current
    assert current["total_chars"] == expected
    assert current["top_results"] == [
        {
            "id": "tc-history",
            "tool_name": "bash",
            "chars": expected,
        }
    ]


def _agent_with_history(blocks):
    """Agent stand-in whose chat history yields the given tool-result blocks."""
    agent = _fake_agent()
    agent._session = SimpleNamespace(
        chat=SimpleNamespace(
            interface=SimpleNamespace(
                _entries=[SimpleNamespace(content=list(blocks))]
            ),
        ),
    )
    return agent


def test_current_tool_result_chars_lists_top_5():
    # 15 prior results of strictly decreasing length; expect the 5 longest.
    blocks = [
        ToolResultBlock(id=f"tc-{i}", name="bash", content="X" * (1500 - i))
        for i in range(15)
    ]
    agent = _agent_with_history(blocks)

    current = current_tool_result_chars(agent)

    assert len(current["top_results"]) == 5
    ids = [entry["id"] for entry in current["top_results"]]
    assert ids == [f"tc-{i}" for i in range(5)]
    assert all(entry["tool_name"] == "bash" for entry in current["top_results"])
    assert all("preview" not in entry for entry in current["top_results"])


def test_current_tool_result_chars_filters_results_at_or_below_1000_chars():
    blocks = [
        ToolResultBlock(id="tc-short", name="bash", content="A" * 1000),
        ToolResultBlock(id="tc-long", name="read", content="B" * 1001),
    ]
    agent = _agent_with_history(blocks)

    current = current_tool_result_chars(agent)

    assert current["top_results"] == [
        {"id": "tc-long", "tool_name": "read", "chars": 1001}
    ]


def test_current_tool_result_chars_entries_include_tool_name_and_no_preview():
    block = ToolResultBlock(id="tc-preview", name="bash", content="Z" * 1200)
    agent = _agent_with_history([block])

    current = current_tool_result_chars(agent)

    assert current["top_results"] == [
        {"id": "tc-preview", "tool_name": "bash", "chars": 1200}
    ]


def test_current_tool_result_chars_tail_omits_readme_and_resident_readme_describes_fields():
    agent = SimpleNamespace(_conversation=[])

    current = current_tool_result_chars(agent)

    assert current["total_chars"] == 0
    assert current["top_results"] == []
    assert "_readme" not in current
    readme = json.dumps(build_meta_readme())
    assert "top_results" in readme
    assert "no preview" in readme
    assert "top 5" not in readme

def test_current_tool_result_chars_readme_is_resident_not_tail_state():
    agent = SimpleNamespace(_conversation=[])

    current = current_tool_result_chars(agent)

    assert "_readme" not in current
    readme = json.dumps(build_meta_readme())
    assert "proactive summarization" in readme
    assert "top_results" in readme
    assert "ids/previews" not in readme

def test_build_meta_readme_mentions_tool_result_char_count_and_summarize():
    readme = build_meta_readme()

    assert "token_usage" in readme["tool_meta"]
    # current_call documents this provider call's own facts.
    assert "own token/cache/output facts" in readme["tool_meta"]
    # The block documents both halves: current_call + since-last-molt session.
    assert "session_cache_rate" in readme["tool_meta"]
    assert "api_calls" in readme["tool_meta"]
    assert "agent_meta.agent_state.token_usage" in readme["agent_meta"]["agent_state"]


def test_build_meta_readme_documents_nested_current_call_and_session_split():
    """The tool_meta readme must describe token_usage as a NESTED block split
    into a current_call half and a session half (not one flat dict), so the
    confusing flat `input` vs `input_tokens` no longer sit side by side."""
    tool_meta_doc = build_meta_readme()["tool_meta"]
    # Both nested half keys are named.
    assert "current_call" in tool_meta_doc
    assert "session" in tool_meta_doc
    # The readme describes the block as nested, not flat.
    lowered = tool_meta_doc.lower()
    assert "nested" in lowered
    # It must not POSITIVELY claim the block is one flat dict (saying "not one
    # flat dict" is fine and expected).
    assert "is one flat dict" not in lowered
    assert "single flat" not in lowered


def test_build_meta_readme_documents_cache_miss_budget_guard():
    """The resident tool_meta readme must document the cache-miss budget guard:
    the "molt now" warning at context.molt and the cache_miss_budget field."""
    readme = build_meta_readme()
    tool_meta_doc = readme["tool_meta"]
    assert "cache_miss_budget" in tool_meta_doc
    assert "molt now" in tool_meta_doc
    # agent_meta no longer carries a token_efficiency block of its own.
    assert "token_efficiency block" not in readme["agent_meta"]
    assert "current_tool_result_chars" in readme["agent_meta"]
    assert "top" in readme["agent_meta"]
    assert "proactive summarization candidates" in readme["agent_meta"]
    assert "adapter_comment" in readme["agent_meta"]


def test_build_meta_readme_documents_always_on_session_cache_miss_telemetry():
    """The tool_meta readme must tell agents that token_usage carries always-on
    since-last-molt cache-miss/budget fields, and to molt proactively (not
    summarize/reconstruct) when at/nearing budget."""
    tool_meta_doc = build_meta_readme()["tool_meta"]
    # The three always-on field names are documented.
    assert "cache_miss_tokens" in tool_meta_doc
    assert "cache_miss_budget" in tool_meta_doc
    assert "cache_miss_remaining_tokens" in tool_meta_doc
    # And they are described as riding on the session half of token_usage.
    assert "ALWAYS-ON" in tool_meta_doc
    # Jason's proactive-molt guidance is present in spirit.
    lowered = tool_meta_doc.lower()
    assert "molt proactively" in lowered
    assert "reconstruct" in lowered


def test_build_meta_readme_documents_timely_latest_only_semantics():
    """agent_meta and notifications are timely transient state: older payloads
    may remain in historical context/logs as traces (canonical history is no
    longer retroactively stripped), and only the NEWEST emission is current —
    old payloads are not current instructions/state, and full-history replay
    does not strip them out."""
    readme = build_meta_readme()
    for key in ("agent_meta", "notifications"):
        doc = readme[key]
        assert "timely" in doc.lower(), key
        assert "only the NEWEST" in doc or "Only the LATEST" in doc, key
        assert "historical trace" in doc, key
        # Replay preserves historical holders rather than stripping their keys.
        assert "preserv" in doc.lower(), key
        assert "does not strip" in doc.lower(), key
    # Old notification payloads must never read as new/unhandled instructions,
    # and the producer channel remains authoritative for actionable content.
    assert "not current instructions" in readme["notifications"]
    assert "source of truth" in readme["notifications"]


def test_build_guidance_with_meta_readme_keeps_section_shape_without_packaged_guidance():
    guidance = build_guidance_with_meta_readme({})

    assert guidance["schema_version"] == 1
    assert guidance["guidance_version"] == "runtime-meta-readme"
    assert guidance["render_mode"] == "latest_tool_result_only"
    assert "meta_readme" not in guidance
    assert [section["id"] for section in guidance["sections"]] == ["meta_readme"]


# ---------------------------------------------------------------------------
# meta_guidance — resident system-prompt section + slimmed tail _meta.
# ---------------------------------------------------------------------------


def _meta_guidance_agent(static_comment=None):
    """Agent stand-in whose chat exposes static_adapter_comment()."""
    chat = SimpleNamespace(static_adapter_comment=lambda: static_comment)
    return SimpleNamespace(_session=SimpleNamespace(chat=chat))


def test_static_adapter_comment_reads_chat_static_method():
    agent = _meta_guidance_agent(static_comment={"summary": "adapter rules"})

    comment = static_adapter_comment(agent)

    assert comment == {"summary": "adapter rules"}


def test_dynamic_adapter_comment_prefers_chat_dynamic_method():
    agent = _fake_agent()
    calls = {"legacy": 0, "dynamic": 0}

    def legacy_comment():
        calls["legacy"] += 1
        return {"adapter": "fake", "summary": "legacy static"}

    def dynamic_comment():
        calls["dynamic"] += 1
        return {"adapter": "fake", "next_reset_in": 7}

    agent._session = SimpleNamespace(
        chat=SimpleNamespace(
            adapter_comment=legacy_comment,
            dynamic_adapter_comment=dynamic_comment,
        )
    )

    assert dynamic_adapter_comment(agent) == {"adapter": "fake", "next_reset_in": 7}
    assert calls == {"legacy": 0, "dynamic": 1}

def test_static_adapter_comment_none_without_method():
    agent = SimpleNamespace(_session=SimpleNamespace(chat=SimpleNamespace()))
    assert static_adapter_comment(agent) is None


def test_build_meta_guidance_renders_guidance_meta_readme_and_adapter():
    static_comment = {
        "adapter": "codex",
        "summary": "Codex plans turns as full or incremental.",
        "summarize_note": (
            "Summarize breaks the incremental prefix and opens a fresh full epoch; "
            "it is an investment, so keep the full:incremental ratio at or below "
            "1:10 and defer non-urgent summarize until the savings justify the "
            "cache miss; summarize immediately under high context pressure."
        ),
    }
    agent = _meta_guidance_agent(static_comment)

    section = build_meta_guidance(agent)

    assert isinstance(section, str) and section.strip()
    # Packaged guidance section body is present.
    assert "progressive disclosure" in section
    assert "Delayed summarization reconstruction threshold" in section
    assert "0.75" in section
    assert "1.0" in section
    assert "Do not call `refresh` just to apply a summarize" in section
    assert "does not mean the active provider-side context" in section
    # meta_readme content (the _meta envelope explanation) is present.
    assert "_meta envelope" in section or "_meta` envelope" in section
    assert "tool_meta" in section
    assert "agent_meta" in section
    assert "Token efficiency state" in section
    assert "Notification handling hook" in section
    assert "Review delegation instruction check" in section
    assert "recent human-channel instructions" in section
    # Static adapter rules are present (the 4 required Codex points).
    assert "full epoch" in section
    assert "1:10" in section


def test_build_meta_guidance_without_adapter_comment_still_renders():
    agent = _meta_guidance_agent(None)
    section = build_meta_guidance(agent)
    assert isinstance(section, str) and section.strip()
    assert "tool_meta" in section


def test_slim_adapter_comment_for_tail_trims_ledger_without_static_key_guessing():
    comment = {
        "adapter": "codex",
        "turns_since_epoch_reset": 3,
        "last_full_api_calls_ago": 2,
        "summary": "dynamic summary that should survive",
        "cache_note": "adapter-owned dynamic value that should survive",
        "summarize_full_note": "adapter-owned dynamic value that should survive",
        "cache_ledger": {
            "rows": [[0, "F", 0.5, 100.0, 50.0, "sum"]],
            "summary": {"api_calls": 1, "cache_rate": 0.5},
        },
        "maintenance_hint": {
            "summarize_economy": "reduce_summarize_frequency",
            "full_to_incremental_ratio": "1:1",
            "reason": "long prose reason",
        },
    }

    slim = slim_adapter_comment_for_tail(comment)

    # Dynamic scalars and arbitrary adapter keys survive: the kernel no longer
    # guesses static-vs-dynamic from Codex-specific key names.
    assert slim["turns_since_epoch_reset"] == 3
    assert slim["last_full_api_calls_ago"] == 2
    assert slim["summary"] == "dynamic summary that should survive"
    assert slim["cache_note"] == "adapter-owned dynamic value that should survive"
    assert slim["summarize_full_note"] == "adapter-owned dynamic value that should survive"
    # The heavy 20-call cache history rows are size-trimmed generically.
    assert "cache_ledger" not in slim
    assert "rows" not in json.dumps(slim)
    assert slim["cache_ledger_summary"] == {"api_calls": 1, "cache_rate": 0.5}
    # maintenance decision survives, long prose reason dropped.
    assert slim["maintenance_hint"]["summarize_economy"] == "reduce_summarize_frequency"
    assert "reason" not in slim["maintenance_hint"]
    # A hook points at the resident meta_guidance section.
    assert "meta_guidance_ref" not in slim

def test_attach_active_runtime_tail_guidance_is_ref_not_full_sections():
    agent = _runtime_agent(total_calls=1)
    block = _stamped_result({"current_time": "T"}, 12)

    attach_active_runtime(agent, [block], prior_holder=None)

    guidance = block.metadata["agent_meta"]["guidance"]["persistent"]
    # Tail guidance is a lightweight ref/hook, not the full ordered sections.
    assert "sections" not in guidance
    assert "meta_guidance" in guidance.get("ref", "") + json.dumps(guidance)


def test_attach_active_runtime_tail_adapter_comment_has_no_ledger_rows():
    calls = {"legacy": 0, "dynamic": 0}

    def legacy_comment():
        calls["legacy"] += 1
        return {
            "adapter": "codex",
            "summary": "legacy static summary",
            "cache_note": "legacy static prose",
        }

    def dynamic_comment():
        calls["dynamic"] += 1
        return {
            "adapter": "codex",
            "turns_since_epoch_reset": 4,
            "cache_ledger": {
                "rows": [[0, "F", 0.5, 100.0, 50.0, "sum"]],
                "summary": {"api_calls": 1},
            },
            "maintenance_hint": {"non_urgent_summarize": "wait", "reason": "long"},
        }

    agent = _runtime_agent(total_calls=1)
    agent._session = SimpleNamespace(
        chat=SimpleNamespace(
            adapter_comment=legacy_comment,
            dynamic_adapter_comment=dynamic_comment,
        )
    )
    block = _stamped_result({"current_time": "T"}, 12, id="t-adapter")

    attach_active_runtime(agent, [block])

    tail = block.metadata["agent_meta"]["agent_state"]["adapter_comment"]
    assert calls == {"legacy": 0, "dynamic": 1}
    assert tail["adapter"] == "codex"
    assert tail["turns_since_epoch_reset"] == 4
    assert "summary" not in tail
    assert "cache_note" not in tail
    assert "cache_ledger" not in tail
    assert "rows" not in json.dumps(tail)
    assert tail["cache_ledger_summary"] == {"api_calls": 1}
    assert "reason" not in tail["maintenance_hint"]
    assert "meta_guidance_ref" not in tail

def _fake_agent_with_lang(lang: str, *, time_awareness: bool = True):
    return SimpleNamespace(
        _config=SimpleNamespace(
            time_awareness=time_awareness,
            timezone_awareness=True,
            language=lang,
        )
    )


def test_render_meta_empty_dict_returns_empty_string():
    agent = _fake_agent_with_lang("en")
    assert render_meta(agent, {}) == ""


def test_render_meta_en_uses_existing_current_time_template():
    agent = _fake_agent_with_lang("en")
    meta = {
        "current_time": "2026-04-20T10:15:23-07:00",
        "context": {
            "system_tokens": 4720,
            "history_tokens": 9450,
            "usage": 0.071,
        },
    }
    assert render_meta(agent, meta) == "[Current time: 2026-04-20T10:15:23-07:00 | context: 7.1% (sys 4720 + ctx 9450)]"


def test_render_meta_zh_uses_existing_current_time_template():
    agent = _fake_agent_with_lang("zh")
    meta = {
        "current_time": "2026-04-20T10:15:23-07:00",
        "context": {
            "system_tokens": 4720,
            "history_tokens": 9450,
            "usage": 0.071,
        },
    }
    assert render_meta(agent, meta) == "[此时：2026-04-20T10:15:23-07:00 | 上下文：7.1% (系统 4720 + 对话 9450)]"


def test_render_meta_wen_uses_existing_current_time_template():
    agent = _fake_agent_with_lang("wen")
    meta = {
        "current_time": "2026-04-20T10:15:23-07:00",
        "context": {
            "system_tokens": 4720,
            "history_tokens": 9450,
            "usage": 0.071,
        },
    }
    assert render_meta(agent, meta) == "[此时：2026-04-20T10:15:23-07:00 | 上下文：7.1% (系统 4720 + 对话 9450)]"


def test_render_meta_non_empty_without_current_time_returns_empty():
    # Verifies render_meta ignores keys it doesn't know how to render
    # (neither current_time nor any context field). Produces '' so the
    # caller can omit the prefix entirely.
    agent = _fake_agent_with_lang("en")
    assert render_meta(agent, {"future_field": 123}) == ""


def test_render_meta_context_unknown_sentinel_en():
    agent = _fake_agent_with_lang("en")
    meta = {
        "current_time": "2026-04-20T10:15:23-07:00",
        "context": {
            "system_tokens": -1,
            "history_tokens": -1,
            "usage": -1.0,
        },
    }
    assert render_meta(agent, meta) == "[Current time: 2026-04-20T10:15:23-07:00 | context: unavailable]"


def test_render_meta_context_unknown_sentinel_zh():
    agent = _fake_agent_with_lang("zh")
    meta = {
        "current_time": "2026-04-20T10:15:23-07:00",
        "context": {
            "system_tokens": -1,
            "history_tokens": -1,
            "usage": -1.0,
        },
    }
    assert render_meta(agent, meta) == "[此时：2026-04-20T10:15:23-07:00 | 上下文：未知]"


def test_render_meta_rounds_usage_to_one_decimal():
    """Usage ratios round to one decimal place, not raw float."""
    agent = _fake_agent_with_lang("en")
    meta = {
        "current_time": "T",
        "context": {
            "system_tokens": 1000,
            "history_tokens": 500,
            "usage": 0.0723456,
        },
    }
    result = render_meta(agent, meta)
    assert "7.2%" in result


def test_stamp_meta_is_deprecated_noop():
    # Runtime capture belongs to ToolResultBlock._agent_pending; stamp_meta
    # remains only as a compatibility import and must not create transport state.
    result = {"status": "ok"}
    out = stamp_meta(result, {"current_time": "2026-04-20T10:15:23-07:00"}, 42)
    assert out is result
    assert out["status"] == "ok"
    assert out == {"status": "ok"}
    assert "_runtime_pending" not in out
    assert "_agent_pending" not in out
    assert "current_time" not in out
    assert "_elapsed_ms" not in out


def test_stamp_meta_empty_meta_records_nothing():
    # Time-blind case: empty meta ⇒ no pending snapshot, no live _meta block.
    result = {"status": "ok"}
    out = stamp_meta(result, {}, 42)
    assert out is result
    assert "_runtime" not in out
    assert "_runtime_pending" not in out
    assert "current_time" not in out
    assert "_elapsed_ms" not in out
    assert out == {"status": "ok"}


def test_stamp_meta_future_fields_are_not_carried():
    # Forward-compatible runtime capture is owned by ToolResultBlock sidecars.
    result = {"status": "ok"}
    meta = {"current_time": "2026-04-20T10:15:23-07:00", "future_field": 123}
    assert stamp_meta(result, meta, 7) == result
    assert result == {"status": "ok"}


def test_stamp_meta_does_not_write_elapsed_ms():
    result = {}
    stamp_meta(result, {"current_time": "T"}, 7)
    assert "_runtime_pending" not in result
    assert "_elapsed_ms" not in result


def _fake_agent_with_session(
    *,
    time_awareness=True,
    timezone_awareness=True,
    language="en",
    system_prompt_tokens=0,
    tools_tokens=0,
    history_tokens=0,
    context_limit=100000,
    decomp_ran=True,
):
    """Agent stand-in that exposes the session state build_meta reads."""
    class _Chat:
        def context_window(self_):
            return 200000  # model default

        class _iface:
            @staticmethod
            def estimate_context_tokens():
                # Real interface.estimate_context_tokens() returns
                # system + tools + conversation — match that contract.
                return system_prompt_tokens + tools_tokens + history_tokens

        interface = _iface()

    chat_obj = _Chat() if decomp_ran else None
    # Server-authoritative wire-count: system + tools + history.
    # This is the invariant our production code relies on
    # (history = latest_input - system - tools).
    latest_input = system_prompt_tokens + tools_tokens + history_tokens

    return SimpleNamespace(
        _config=SimpleNamespace(
            time_awareness=time_awareness,
            timezone_awareness=timezone_awareness,
            language=language,
            context_limit=context_limit,
        ),
        _session=SimpleNamespace(
            _system_prompt_tokens=system_prompt_tokens,
            _tools_tokens=tools_tokens,
            _latest_input_tokens=latest_input,
            _token_decomp_dirty=not decomp_ran,
            _chat=chat_obj,
            chat=chat_obj,
        ),
    )


def test_build_meta_omits_numeric_context_fields_when_decomp_ran():
    agent = _fake_agent_with_session(
        system_prompt_tokens=5000,
        tools_tokens=500,
        history_tokens=200,
        context_limit=100000,
    )
    meta = build_meta(agent)
    assert "context" not in meta
    assert meta_block._current_context_usage(agent) == pytest.approx(0.057)


def test_build_meta_carries_latest_token_usage_for_tool_meta_only():
    # The full provider-round snapshot is the source; only the compact subset is
    # placed into the transit key destined for agent_meta.agent_state.token_usage. With
    # no get_token_usage on the agent, only the provider-round half is emitted.
    snapshot = {
        "scope": "provider_round",
        "api_call_index": 3,
        "input_tokens": 190_000,
        "cache_miss_tokens": 22_000,
        "output_tokens": 636,
        "thinking_tokens": 40,
        "cached_tokens": 168_000,
        "cache_rate": 0.882,
        "context_tokens": 190_000,
        "context_window": 250_000,
        "context_usage": 0.759,
        "estimated": False,
        "api_call_id": "call-abc",
    }
    agent = _fake_agent()
    agent._session = SimpleNamespace(
        _token_decomp_dirty=True,
        latest_token_usage_snapshot=lambda: snapshot,
    )

    meta = build_meta(agent)

    # The token_usage block is NESTED: a `current_call` half (this result's own
    # provider round — ONLY its own token/cache/output facts, no context state)
    # and a `session` half (since-last-molt cumulative aggregate), each under its
    # own explicit key, plus a shared `ref`. No session getter here, so only
    # `current_call` + `ref` are present (the `session` half — which is where
    # context_usage/window now live — is omitted entirely rather than left empty).
    assert meta[TOOL_META_TOKEN_USAGE_PENDING_KEY] == {
        "current_call": {
            "input": 190_000,
            "cache_miss": 22_000,
            "cache_rate": 0.882,
            "output": 636,
            "thinking": 40,
        },
        "ref": "See meta_guidance.token_efficiency for details.",
    }
    # The unified token_usage block is the sole token diagnostics carrier; the
    # separate token_efficiency block must be gone.
    assert "token_efficiency" not in meta


# The two nested-half keys and the shared ref hook on the token_usage block.
_TOKEN_USAGE_CURRENT_CALL_KEY = "current_call"
_TOKEN_USAGE_SESSION_KEY = "session"

# Keys carried INSIDE the `current_call` half (snapshot-derived). This half is
# ONLY this provider call's own token/cache/output facts — context state
# (context_usage/window) moved to the `session` half, which is where current
# context lives.
_PROVIDER_TOKEN_USAGE_KEYS = {
    "input",
    "cache_miss",
    "cache_rate",
    "output",
    "thinking",
}
# Keys carried INSIDE the `session` half (cumulative get_token_usage-derived —
# since last molt, surviving refresh). ``cache_miss_tokens`` is always present
# (derivable from the cumulative counters); ``cache_miss_budget`` /
# ``cache_miss_remaining_tokens`` ride along only when a positive-int budget is
# resolvable from agent._config. Current context state (context_tokens/
# context_window/context_usage) rides here too whenever it is resolvable.
_SESSION_TOKEN_USAGE_KEYS = {
    "session_cache_rate",
    "api_calls",
    "input_tokens",
    "cached_tokens",
    "avg_input_tokens_per_api_call",
    "cache_miss_tokens",
}
# Current-context state keys under the `session` half (present when resolvable).
_SESSION_CONTEXT_STATE_KEYS = {
    "context_tokens",
    "context_window",
    "context_usage",
}
# The two budget-derived always-on fields (present only with a configured budget).
_SESSION_CACHE_MISS_BUDGET_KEYS = {
    "cache_miss_budget",
    "cache_miss_remaining_tokens",
}


def _session_half(compact):
    """Return the nested `session` half of a token_usage block.

    Raises KeyError if the block is missing the nested `session` key — which is
    itself the contract under test: session stats must live under
    token_usage.session, never flattened at the top level.
    """
    return compact["session"]


def _current_call_half(compact):
    """Return the nested `current_call` half of a token_usage block."""
    return compact["current_call"]


def test_build_tool_meta_token_usage_compacts_full_snapshot_to_exact_keys():
    # A full provider-round snapshot (the internal-logging shape) must compact to
    # exactly the five current_call keys — this call's OWN token/cache/output
    # facts — dropping scope/api_call_index/cached_tokens/context_tokens/
    # context_window/context_usage/estimated/api_call_id and the long names.
    # context_usage/window are NO LONGER in current_call (they belong with
    # current context state under the session half). With no get_token_usage,
    # the session half is omitted.
    snapshot = {
        "scope": "provider_round",
        "api_call_index": 3,
        "input_tokens": 190_000,
        "cache_miss_tokens": 22_000,
        "output_tokens": 636,
        "thinking_tokens": 40,
        "cached_tokens": 168_000,
        "cache_rate": 0.882,
        "context_tokens": 190_000,
        "context_window": 250_000,
        "context_usage": 0.759,
        "estimated": False,
        "api_call_id": "call-abc",
    }
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: snapshot)
    )

    compact = build_tool_meta_token_usage(agent)

    # Only the current_call half + ref (no session getter on this stub).
    assert set(compact) == {_TOKEN_USAGE_CURRENT_CALL_KEY, "ref"}
    assert set(compact[_TOKEN_USAGE_CURRENT_CALL_KEY]) == _PROVIDER_TOKEN_USAGE_KEYS
    assert compact == {
        "current_call": {
            "input": 190_000,
            "cache_miss": 22_000,
            "cache_rate": 0.882,
            "output": 636,
            "thinking": 40,
        },
        "ref": "See meta_guidance.token_efficiency for details.",
    }
    # current_call carries none of the context-state keys anymore.
    assert not (_SESSION_CONTEXT_STATE_KEYS & set(compact[_TOKEN_USAGE_CURRENT_CALL_KEY]))


def test_build_tool_meta_token_usage_merges_session_aggregate_into_one_block():
    # The block nests BOTH halves under explicit keys: current_call (from the
    # snapshot) and session (from get_token_usage) — there is no separate
    # token_efficiency block anywhere, and the two halves never mingle their
    # confusingly-similar keys (`input` vs `input_tokens`) at one level.
    snapshot = {
        "input_tokens": 190_000,
        "cache_miss_tokens": 22_000,
        "output_tokens": 636,
        "thinking_tokens": 40,
        "cache_rate": 0.882,
        "context_window": 250_000,
        "context_usage": 0.759,
    }
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: snapshot),
        get_token_usage=lambda: {
            "api_calls": 4,
            "input_tokens": 22_000,
            "cached_tokens": 5_500,
        },
    )

    compact = build_tool_meta_token_usage(agent)

    assert set(compact) == {
        _TOKEN_USAGE_CURRENT_CALL_KEY,
        _TOKEN_USAGE_SESSION_KEY,
        "ref",
    }
    assert compact == {
        "current_call": {
            "input": 190_000,
            "cache_miss": 22_000,
            "cache_rate": 0.882,
            "output": 636,
            "thinking": 40,
        },
        "session": {
            "session_cache_rate": 0.25,
            "api_calls": 4,
            "input_tokens": 22_000,
            "cached_tokens": 5_500,
            "avg_input_tokens_per_api_call": 5_500,
            # always-on cache-miss telemetry (no _config -> no budget-derived
            # fields, but cache_miss_tokens is always present: 22_000 - 5_500)
            "cache_miss_tokens": 16_500,
            # context state is omitted here — get_token_usage() carried no
            # ctx_total_tokens, so context_tokens/context_usage are unresolvable.
        },
        # short guidance hook, shared across both halves
        "ref": "See meta_guidance.token_efficiency for details.",
    }
    # No dropped/noisy keys leak into either half — and the hook is the short
    # `ref`, never the long `guidance_ref`. context_usage/window no longer sit
    # in current_call.
    flat = json.dumps(compact)
    for noisy in ("scope", "guidance_ref", "estimated", "api_call_id"):
        assert noisy not in flat
    assert "window" not in compact["current_call"]
    assert "context_usage" not in compact["current_call"]


def test_build_tool_meta_token_usage_session_only_when_no_snapshot():
    # When no provider-round snapshot exists but session data does, only the
    # session half is emitted (the block is never invented from nothing), and
    # the current_call half is omitted rather than left empty.
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": 2,
            "input_tokens": 1_000,
            "cached_tokens": 1_200,  # cached > input clamps to 1.0
        },
    )

    compact = build_tool_meta_token_usage(agent)

    assert set(compact) == {_TOKEN_USAGE_SESSION_KEY, "ref"}
    assert _TOKEN_USAGE_CURRENT_CALL_KEY not in compact
    assert set(compact[_TOKEN_USAGE_SESSION_KEY]) == _SESSION_TOKEN_USAGE_KEYS
    assert compact[_TOKEN_USAGE_SESSION_KEY]["session_cache_rate"] == 1.0
    assert compact[_TOKEN_USAGE_SESSION_KEY]["avg_input_tokens_per_api_call"] == 500


def test_build_tool_meta_token_usage_preserves_zero_and_sentinel_values():
    # Existing numeric zero / sentinel values are kept, not dropped or invented,
    # inside the current_call half. The provider snapshot's context_window/
    # context_usage are IGNORED for current_call now (context state is a session
    # concern), so a zero/sentinel window does not leak into current_call.
    snapshot = {
        "input_tokens": 0,
        "cache_miss_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cache_rate": 0.0,
        "context_window": 0,
        "context_usage": -1.0,
    }
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: snapshot)
    )

    compact = build_tool_meta_token_usage(agent)

    assert compact == {
        "current_call": {
            "input": 0,
            "cache_miss": 0,
            "cache_rate": 0.0,
            "output": 0,
            "thinking": 0,
        },
        "ref": "See meta_guidance.token_efficiency for details.",
    }


def test_build_tool_meta_token_usage_robust_to_missing_fields():
    # Partial snapshot: only present fields are emitted inside current_call;
    # absent ones are omitted rather than invented.
    snapshot = {"input_tokens": 100, "cache_rate": 0.5}
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: snapshot)
    )

    compact = build_tool_meta_token_usage(agent)

    assert compact == {
        "current_call": {"input": 100, "cache_rate": 0.5},
        "ref": "See meta_guidance.token_efficiency for details.",
    }


def test_build_meta_folds_session_economy_into_token_usage_not_efficiency():
    agent = _fake_agent_with_session(
        system_prompt_tokens=1000,
        tools_tokens=500,
        history_tokens=5500,
        context_limit=10000,
    )
    agent.get_token_usage = lambda: {
        "api_calls": 4,
        "input_tokens": 22000,
        "cached_tokens": 5500,
        "ctx_total_tokens": 99999,
    }

    meta = build_meta(agent)

    assert "context" not in meta
    # There is NO token_efficiency block anywhere — the session economy now lives
    # inside the `session` half of the nested token_usage transit block.
    assert "token_efficiency" not in meta
    session = meta[TOOL_META_TOKEN_USAGE_PENDING_KEY]["session"]
    assert session["api_calls"] == 4
    assert session["input_tokens"] == 22000
    assert session["cached_tokens"] == 5500
    assert session["session_cache_rate"] == 0.25
    assert session["avg_input_tokens_per_api_call"] == 5500
    # Current context state now rides on the session half: context_tokens from
    # ctx_total_tokens, context_window from the configured limit, context_usage
    # from tokens/window.
    assert session["context_tokens"] == 99999
    assert session["context_window"] == 10000
    assert session["context_usage"] == round(99999 / 10000, 5)
    # Genuinely-noisy internal-logging fields never reappear in the session half.
    for noisy in ("scope", "guidance_ref"):
        assert noisy not in session


def test_injected_session_survives_refresh_baseline_reset():
    """Headline #679-correction contract (Jason FINAL): after a refresh restores
    the cumulative totals AND re-anchors the runtime-session (since-refresh)
    baseline to zero, the injected token_usage.session must STILL report the
    restored/since-last-molt totals — not the zeroed since-refresh deltas.

    Uses a real SessionManager so the restore_token_state re-baselining path is
    exercised, not a stub.
    """
    from unittest.mock import MagicMock
    from lingtai.kernel.session import SessionManager
    from lingtai.kernel.config import AgentConfig

    svc = MagicMock()
    svc.model = "test-model"
    session = SessionManager(
        llm_service=svc,
        config=AgentConfig(),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "p",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    # Simulate a refresh: restore since-last-molt session totals from persisted token state.
    # restore_token_state re-anchors the since-refresh baseline to these totals,
    # so get_runtime_session_token_usage() is now ~0.
    session.restore_token_state({
        "input_tokens": 3_000_000,
        "output_tokens": 100_000,
        "thinking_tokens": 10_000,
        "cached_tokens": 2_400_000,
        "api_calls": 512,
    })
    assert session.get_runtime_session_token_usage()["input_tokens"] == 0

    agent = SimpleNamespace(
        _config=SimpleNamespace(
            time_awareness=False,
            timezone_awareness=True,
            cache_miss_budget=1_000_000,
        ),
        _session=session,
        _intrinsics=set(),
        get_token_usage=session.get_token_usage,
        get_runtime_session_token_usage=session.get_runtime_session_token_usage,
    )

    meta = build_meta(agent)
    injected = meta[TOOL_META_TOKEN_USAGE_PENDING_KEY]["session"]

    # The since-last-molt totals survive the refresh — NOT the zeroed deltas.
    assert injected["input_tokens"] == 3_000_000
    assert injected["cached_tokens"] == 2_400_000
    assert injected["api_calls"] == 512
    # session_cache_rate = cached/input over the surviving cumulative totals.
    assert injected["session_cache_rate"] == round(2_400_000 / 3_000_000, 5)  # 0.8
    # cache-miss telemetry is also on the surviving cumulative basis, so the
    # remaining budget did NOT reset to the full 1M on refresh.
    assert injected["cache_miss_tokens"] == 600_000  # 3.0M - 2.4M
    assert injected["cache_miss_remaining_tokens"] == 400_000  # 1M - 600k


def test_build_meta_session_cache_rate_clamps_to_fraction():
    agent = _fake_agent_with_session(
        system_prompt_tokens=100,
        tools_tokens=0,
        history_tokens=900,
        context_limit=2000,
    )
    agent.get_token_usage = lambda: {
        "api_calls": 1,
        "input_tokens": 1000,
        "cached_tokens": 1200,
        "ctx_total_tokens": 1000,
    }

    meta = build_meta(agent)

    session = meta[TOOL_META_TOKEN_USAGE_PENDING_KEY]["session"]
    assert session["session_cache_rate"] == 1.0


def test_synthetic_meta_envelope_shows_token_usage_in_agent_state():
    # /notification synthetic raw meta carries token diagnostics under
    # agent_meta.agent_state when pending/session data is available. The nested
    # current_call/session split is preserved on that current-state axis.
    snapshot = {
        "input_tokens": 190_000,
        "cache_miss_tokens": 22_000,
        "cache_rate": 0.882,
        "context_window": 250_000,
        "context_usage": 0.759,
        "output_tokens": 636,
        "thinking_tokens": 40,
    }
    agent = _fake_agent_with_session()
    agent._session.latest_token_usage_snapshot = lambda: snapshot
    agent.get_token_usage = lambda: {
        "api_calls": 4,
        "input_tokens": 22_000,
        "cached_tokens": 5_500,
    }
    payload = build_notification_payload({"system": {"events": [{"body": "ping"}]}})

    envelope = build_synthetic_meta_envelope(agent, payload, call_id="c1")

    tool_meta = envelope["tool_meta"]
    assert tool_meta["synthetic"] is True
    agent_meta = envelope["agent_meta"]
    state = agent_meta["agent_state"]
    assert state["token_usage"]["current_call"]["input"] == 190_000
    assert state["token_usage"]["session"]["session_cache_rate"] == 0.25
    assert state["token_usage"]["session"]["api_calls"] == 4
    assert "token_efficiency" not in agent_meta
    assert "token_usage" in state
    assert TOOL_META_TOKEN_USAGE_PENDING_KEY not in agent_meta


def test_synthetic_meta_envelope_omits_token_usage_when_no_data():
    # No snapshot and no session usage → no token_usage key on synthetic tool_meta.
    agent = _fake_agent_with_session()
    agent._session.latest_token_usage_snapshot = lambda: None
    payload = build_notification_payload({"system": {"events": [{"body": "ping"}]}})

    envelope = build_synthetic_meta_envelope(agent, payload, call_id="c1")

    assert "token_usage" not in envelope["tool_meta"]


def test_session_half_uses_cumulative_totals_not_since_refresh_deltas():
    # Jason FINAL: `token_usage.session` means "since last molt" and MUST read the
    # cumulative/restored get_token_usage() totals, which SURVIVE refresh — NOT
    # the since-refresh get_runtime_session_token_usage() deltas (which reset to
    # ~0 on every refresh and were the #679 bug). Here get_token_usage carries the
    # restored cumulative totals and the runtime-session getter reports the small
    # post-refresh deltas; the injected session half must reflect the cumulative
    # totals, so a refresh does not zero it out.
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": 27_863,
            "input_tokens": 5_000_000_000,
            "cached_tokens": 4_000_000_000,
        },
        get_runtime_session_token_usage=lambda: {
            "api_calls": 2,
            "input_tokens": 200,
            "cached_tokens": 40,
            "session_cache_rate": 0.2,
            "avg_input_tokens_per_api_call": 100,
        },
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    # Cumulative/restored totals — the since-molt-surviving numbers.
    assert session["api_calls"] == 27_863
    assert session["input_tokens"] == 5_000_000_000
    assert session["cached_tokens"] == 4_000_000_000
    # session_cache_rate/avg are recomputed from the cumulative counters.
    assert session["session_cache_rate"] == 0.8  # 4e9 / 5e9
    assert session["avg_input_tokens_per_api_call"] == round(5_000_000_000 / 27_863)
    # The small since-refresh deltas never leak in.
    assert session["api_calls"] != 2
    assert session["input_tokens"] != 200


def test_session_half_ignores_runtime_getter_entirely():
    # Even with the runtime/since-refresh getter present, the session half is
    # built purely from get_token_usage(); the runtime getter is never consulted.
    runtime_called = {"n": 0}

    def runtime_getter():
        runtime_called["n"] += 1
        return {"api_calls": 999, "input_tokens": 1, "cached_tokens": 0}

    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": 4,
            "input_tokens": 22_000,
            "cached_tokens": 5_500,
        },
        get_runtime_session_token_usage=runtime_getter,
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    assert session["api_calls"] == 4
    assert session["input_tokens"] == 22_000
    assert session["cached_tokens"] == 5_500
    assert runtime_called["n"] == 0


def test_session_half_session_cache_rate_is_cached_over_input_cumulative():
    # session_cache_rate must equal cached_tokens / input_tokens over the
    # cumulative/since-molt totals (rounded to 5 dp, clamped to <= 1.0).
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": 10,
            "input_tokens": 800_000,
            "cached_tokens": 600_000,
        },
    )

    session = _session_half(build_tool_meta_token_usage(agent))
    assert session["session_cache_rate"] == round(600_000 / 800_000, 5)  # 0.75
    assert session["avg_input_tokens_per_api_call"] == 80_000


def test_session_half_carries_current_context_state_from_get_token_usage():
    # context_usage belongs with session/current context state, NOT current_call.
    # context_tokens comes from get_token_usage()'s ctx_total_tokens; context_window
    # from the provider snapshot (or configured window); context_usage = tokens/window.
    snapshot = {
        "input_tokens": 190_000,
        "cache_miss_tokens": 22_000,
        "cache_rate": 0.882,
        "context_window": 250_000,
        "context_usage": 0.759,  # snapshot's own value — session recomputes from tokens/window
        "output_tokens": 636,
        "thinking_tokens": 40,
    }
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: snapshot),
        get_token_usage=lambda: {
            "api_calls": 4,
            "input_tokens": 22_000,
            "cached_tokens": 5_500,
            "ctx_total_tokens": 190_000,
        },
    )

    compact = build_tool_meta_token_usage(agent)

    # current_call has NO context state.
    assert "context_usage" not in compact["current_call"]
    assert "window" not in compact["current_call"]
    assert "context_tokens" not in compact["current_call"]
    # session carries the current context state.
    session = _session_half(compact)
    assert session["context_tokens"] == 190_000
    assert session["context_window"] == 250_000
    assert session["context_usage"] == round(190_000 / 250_000, 5)  # 0.76
    assert _SESSION_CONTEXT_STATE_KEYS <= set(session)


def test_session_half_context_window_falls_back_to_configured_limit():
    # With no provider snapshot window, context_window uses the configured
    # context_limit and context_usage is computed against it.
    agent = SimpleNamespace(
        _config=SimpleNamespace(context_limit=500_000),
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": 4,
            "input_tokens": 22_000,
            "cached_tokens": 5_500,
            "ctx_total_tokens": 250_000,
        },
    )

    session = _session_half(build_tool_meta_token_usage(agent))
    assert session["context_window"] == 500_000
    assert session["context_tokens"] == 250_000
    assert session["context_usage"] == round(250_000 / 500_000, 5)  # 0.5


def test_session_half_omits_context_state_when_unresolvable():
    # No ctx_total_tokens and no window -> context state fields are omitted, never
    # invented; the economy fields still emit.
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": 4,
            "input_tokens": 22_000,
            "cached_tokens": 5_500,
        },
    )

    session = _session_half(build_tool_meta_token_usage(agent))
    assert not (_SESSION_CONTEXT_STATE_KEYS & set(session))
    assert session["api_calls"] == 4


def test_token_usage_block_carries_short_guidance_ref():
    # The token_usage block always carries a short `ref` hook (NOT `guidance_ref`)
    # — a short sentence, not a bare path — pointing at the resident guidance
    # section.  The ref lives at the TOP level of the block, shared across both
    # halves, not inside current_call/session.
    snapshot = {"input_tokens": 100, "cache_rate": 0.5}
    agent = SimpleNamespace(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: snapshot)
    )

    compact = build_tool_meta_token_usage(agent)

    assert compact["ref"] == "See meta_guidance.token_efficiency for details."
    assert "guidance_ref" not in json.dumps(compact)
    # The ref is not duplicated inside the halves.
    assert "ref" not in _current_call_half(compact)


# ---------------------------------------------------------------------------
# Always-on since-last-molt cache-miss/budget telemetry in the session half of
# token_usage (Jason's follow-up to PR #641).  Distinct from the
# agent_meta.agent_state.context guard (build_cache_miss_budget_context), which
# surfaces only at/above budget: these three fields ride on EVERY result whenever
# the session aggregate is available so agents can always read current cache
# miss + budget.
# ---------------------------------------------------------------------------


def _session_agent_with_budget(
    *, input_tokens, cached_tokens, api_calls=1, budget=1_000_000, with_config=True
):
    """SimpleNamespace agent exposing the cumulative token getter and a budget.

    The session half (and its always-on cache-miss telemetry) now reads the
    cumulative/since-molt ``get_token_usage()`` totals, so this helper exposes
    that getter. ``with_config=False`` drops ``_config`` entirely so the
    config-less-stub path (cache_miss_tokens present; budget-derived fields
    omitted) is exercised.
    """
    kwargs = dict(
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": api_calls,
            "input_tokens": input_tokens,
            "cached_tokens": cached_tokens,
        },
    )
    if with_config:
        kwargs["_config"] = SimpleNamespace(cache_miss_budget=budget)
    return SimpleNamespace(**kwargs)


def test_session_half_always_carries_cache_miss_tokens_and_budget_fields():
    # With a configured budget, all three always-on fields appear even though the
    # cache-miss total is far below budget (contrast the context guard, which
    # would stay silent here).
    agent = _session_agent_with_budget(
        input_tokens=300_000, cached_tokens=100_000, budget=1_000_000
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    assert session["cache_miss_tokens"] == 200_000  # 300k - 100k
    assert session["cache_miss_budget"] == 1_000_000
    assert session["cache_miss_remaining_tokens"] == 800_000  # 1M - 200k
    # The full session half plus the two budget-derived fields are all present.
    assert (_SESSION_TOKEN_USAGE_KEYS | _SESSION_CACHE_MISS_BUDGET_KEYS) <= set(session)


def test_session_half_cache_miss_tokens_clamps_to_zero():
    # cached > input (odd provider accounting) -> cache_miss clamps to 0, and
    # remaining is the full budget.
    agent = _session_agent_with_budget(
        input_tokens=100, cached_tokens=500, budget=1_000_000
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    assert session["cache_miss_tokens"] == 0
    assert session["cache_miss_remaining_tokens"] == 1_000_000


def test_session_half_remaining_clamps_to_zero_above_budget():
    # cache_miss above budget -> remaining floors at 0, never negative.  The
    # always-on fields keep reporting even past the guard trip point.
    agent = _session_agent_with_budget(
        input_tokens=1_500_000, cached_tokens=200_000, budget=1_000_000
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    assert session["cache_miss_tokens"] == 1_300_000
    assert session["cache_miss_remaining_tokens"] == 0


def test_session_half_omits_budget_fields_without_config():
    # A config-less stub still gets cache_miss_tokens (session-derivable) but the
    # budget-derived fields are omitted, never invented.
    agent = _session_agent_with_budget(
        input_tokens=300_000, cached_tokens=100_000, with_config=False
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    assert session["cache_miss_tokens"] == 200_000
    assert "cache_miss_budget" not in session
    assert "cache_miss_remaining_tokens" not in session


def test_session_half_omits_budget_fields_for_nonpositive_budget():
    # A non-positive / non-int / bool budget disables the budget-derived fields,
    # matching build_cache_miss_budget_context semantics; cache_miss_tokens stays.
    for bad in (0, -5, None, True, "1000000"):
        agent = _session_agent_with_budget(
            input_tokens=300_000, cached_tokens=100_000, budget=bad
        )
        compact = build_tool_meta_token_usage(agent)
        session = _session_half(compact)
        assert session["cache_miss_tokens"] == 200_000
        assert "cache_miss_budget" not in session
        assert "cache_miss_remaining_tokens" not in session


def test_session_half_honors_custom_budget():
    agent = _session_agent_with_budget(
        input_tokens=300_000, cached_tokens=100_000, budget=250_000
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    assert session["cache_miss_budget"] == 250_000
    assert session["cache_miss_remaining_tokens"] == 50_000  # 250k - 200k


def test_session_half_cache_miss_uses_cumulative_totals_surviving_refresh():
    # Jason FINAL: the always-on cache-miss telemetry is SINCE LAST MOLT — it
    # derives from the cumulative/restored get_token_usage() totals so a refresh
    # does not reset cache_miss_remaining_tokens. It must NOT use the
    # since-refresh runtime deltas (which would drop cache_miss back near zero on
    # every restart).
    agent = SimpleNamespace(
        _config=SimpleNamespace(cache_miss_budget=1_000_000),
        _session=SimpleNamespace(latest_token_usage_snapshot=lambda: None),
        get_token_usage=lambda: {
            "api_calls": 30,
            "input_tokens": 900_000,
            "cached_tokens": 100_000,
        },
        get_runtime_session_token_usage=lambda: {
            "api_calls": 2,
            "input_tokens": 200,
            "cached_tokens": 40,
        },
    )

    compact = build_tool_meta_token_usage(agent)

    session = _session_half(compact)
    # cache_miss from cumulative totals: 900k - 100k = 800k (not the tiny
    # since-refresh 200-40 delta).
    assert session["cache_miss_tokens"] == 800_000
    assert session["cache_miss_remaining_tokens"] == 200_000  # 1M - 800k


def test_build_meta_token_usage_carries_always_on_cache_miss_below_budget():
    # Through build_meta: below the budget there is NO context guard, but the
    # always-on session-half telemetry still reports current cache miss + budget.
    agent = _budget_agent(budget=1_000_000, input_tokens=300_000, cached_tokens=100_000)

    meta = build_meta(agent)

    # No context guard below budget.
    assert meta_block.TOOL_META_CONTEXT_PENDING_KEY not in meta
    session = meta[TOOL_META_TOKEN_USAGE_PENDING_KEY]["session"]
    assert session["cache_miss_tokens"] == 200_000
    assert session["cache_miss_budget"] == 1_000_000
    assert session["cache_miss_remaining_tokens"] == 800_000


def test_build_meta_omits_context_before_decomp_runs():
    # When decomposition has never run (dirty flag True) and no chat yet,
    # we do not emit stale/unknown numeric context diagnostics in agent_meta.
    agent = _fake_agent_with_session(decomp_ran=False)
    meta = build_meta(agent)
    assert "context" not in meta
    assert meta_block._current_context_usage(agent) == -1.0


def test_build_meta_history_falls_back_to_interface_estimate_after_restore():
    """After start() rehydrates the wire ChatInterface from chat_history.jsonl,
    _latest_input_tokens is still 0 until the first LLM call completes. The
    meta-line must fall back to interface.estimate_context_tokens() so the
    first post-refresh text_input shows the restored history, not '对话 0'."""
    agent = _fake_agent_with_session(
        system_prompt_tokens=5000,
        tools_tokens=500,
        history_tokens=50000,  # restored from JSONL
    )
    # Simulate pre-first-LLM-call state: interface has history but server
    # has not reported an input count yet.
    agent._session._latest_input_tokens = 0
    meta = build_meta(agent)
    # The local usage helper still falls back to interface.estimate_context_tokens(),
    # but the numeric breakdown is no longer duplicated in agent_meta.
    assert "context" not in meta
    assert meta_block._current_context_usage(agent) == pytest.approx(0.555)


def test_build_meta_time_blind_still_omits_numeric_context_fields():
    agent = _fake_agent_with_session(
        time_awareness=False,
        system_prompt_tokens=5000,
        tools_tokens=500,
        history_tokens=200,
    )
    meta = build_meta(agent)
    assert "current_time" not in meta
    assert "context" not in meta
    assert meta_block._current_context_usage(agent) == pytest.approx(0.057)


def test_render_meta_time_blind_with_context_present_emits_empty_time_slot():
    """Known edge case (documented in spec): a time-blind agent whose session
    has context data produces '[Current time:  | context: ...]' with an empty
    time slot. This is intentional — the spec accepts this and defers a
    time-blind-specific template to a follow-up. If future work changes the
    behavior, this test must be updated together with the spec."""
    agent = _fake_agent_with_lang("en")
    meta = {
        "context": {
            "system_tokens": 4720,
            "history_tokens": 9450,
            "usage": 0.071,
        },
    }
    assert render_meta(agent, meta) == "[Current time:  | context: 7.1% (sys 4720 + ctx 9450)]"


def test_build_meta_history_tokens_does_not_double_count_system_and_tools():
    """Regression: history_tokens must NOT include the system prompt or tool
    schema tokens (they belong to system_tokens). Computed from the server's
    authoritative input count minus system + tools, mirroring
    SessionManager.get_token_usage's ctx_history_tokens."""
    agent = _fake_agent_with_session(
        system_prompt_tokens=5000,
        tools_tokens=500,
        history_tokens=200,
    )
    meta = build_meta(agent)
    # The numeric context breakdown is no longer duplicated in agent_meta, but
    # the local warning/reconstruction estimate must still avoid double-counting
    # system+tools. usage = (5500 + 200) / 100000 = 0.057.
    assert "context" not in meta
    assert meta_block._current_context_usage(agent) == pytest.approx(0.057)


def test_build_meta_usage_matches_get_context_pressure_after_restore():
    """Regression: on the very first turn after a restore (before the first
    LLM call returns), the meta-prefix usage% must match what
    SessionManager.get_context_pressure() would report for the same state.
    Otherwise the molt warning and the injected '[... | context: X%]'
    prefix show different numbers on the same turn, confusing the agent.

    Pre-fix bug: build_meta treated estimate_context_tokens() as
    history-only, but the real method returns system + tools + conversation.
    That made history_tokens = full estimate, which then double-counted
    system + tools when added to system_tokens in the usage calculation.
    """
    sys_prompt = 5000
    tools = 500
    history = 50000
    limit = 100000
    agent = _fake_agent_with_session(
        system_prompt_tokens=sys_prompt,
        tools_tokens=tools,
        history_tokens=history,
        context_limit=limit,
    )
    # Simulate post-restore state: wire chat rehydrated from JSONL,
    # but no LLM response has landed yet for this run.
    agent._session._latest_input_tokens = 0
    meta = build_meta(agent)

    # The numeric context breakdown is no longer duplicated in agent_meta.
    assert "context" not in meta

    # The local usage helper must still match get_context_pressure():
    # pressure = estimate_context_tokens() / limit = (sys+tools+history) / limit
    expected_pressure = (sys_prompt + tools + history) / limit
    assert meta_block._current_context_usage(agent) == pytest.approx(expected_pressure)


# ---------------------------------------------------------------------------
# build_reconstruction_tool_meta — one-shot delayed-summarize reconstruction
# event (channel A), permanent evidence on _meta.tool_meta.
#
# The adapter records the before-context (A) when an actual reconstruction
# fires; the kernel pops it once, fills the after-context (B) from the live
# context decomposition, and attaches the A->B event to the next visible tool
# result. If B is still >= the 0.6 recovery target, a molt reminder is
# included; otherwise the A->B event is attached without a warning.
# ---------------------------------------------------------------------------


def _recon_agent(
    *,
    raw_event,
    after_usage,
    context_limit=100000,
    local_usage=None,
):
    """Agent stand-in whose session yields a pending reconstruction event.

    ``after_usage`` drives the PROVIDER-reported after-context (B): it is set as
    ``_latest_input_tokens`` (the post-reconstruction provider request input).
    ``local_usage`` (defaults to ``after_usage``) drives the local
    compacted-history estimate via ``interface.estimate_context_tokens()``. When
    the two differ, tests can prove which source B prefers; setting
    ``after_usage`` semantics:
      * ``>= 0``  -> _latest_input_tokens reflects that provider usage.
      * ``None``  -> _latest_input_tokens = 0 (provider input unavailable),
                     forcing the local-estimate fallback.
    """
    if local_usage is None:
        local_usage = after_usage if after_usage is not None else 0.0
    local_history = int(round(local_usage * context_limit))
    provider_input = (
        0 if after_usage is None else int(round(after_usage * context_limit))
    )
    fake_iface = SimpleNamespace(estimate_context_tokens=lambda: local_history)

    class _Chat:
        interface = fake_iface

        def context_window(self_):
            return context_limit

    taken = {"count": 0}

    def _take():
        taken["count"] += 1
        return raw_event if taken["count"] == 1 else None

    chat = _Chat()
    chat.take_pending_reconstruction_event = _take

    session = SimpleNamespace(
        _token_decomp_dirty=False,
        _system_prompt_tokens=0,
        _tools_tokens=0,
        _latest_input_tokens=provider_input,
        chat=chat,
        context_pressure_warning_active=False,
        context_pressure_streak=0,
    )
    agent = SimpleNamespace(
        _intrinsics={"psyche": object()},
        _config=SimpleNamespace(
            context_limit=context_limit, time_awareness=True, timezone_awareness=True
        ),
        _session=session,
        _uptime_anchor=None,
    )
    return agent


# The 1.0 FORCED rebuild event. trigger_threshold is now the 1.0 hard boundary,
# before-context is at/above full (100%).
_RAW_EVENT = {
    "type": "delayed_summarize_reconstruction",
    "reason": "delayed_summarize_reconstruction",
    "trigger_threshold": 1.0,
    "recovery_target": 0.60,
    "context_window": 100000,
    "before": {"context_tokens": 100000, "usage": 1.0},
}

# The MANUAL rebuild=true event still uses the recovery molt (not the forced
# unified warning).
_RAW_MANUAL_EVENT = {
    "type": "summarize_rebuild_only_reconstruction",
    "reason": "summarize_rebuild_only_reconstruction",
    "trigger_threshold": 1.0,
    "recovery_target": 0.60,
    "context_window": 100000,
    "before": {"context_tokens": 85000, "usage": 0.85},
}


def test_reconstruction_tool_meta_none_when_no_pending_event():
    agent = _recon_agent(raw_event=None, after_usage=0.40)
    assert meta_block.build_reconstruction_tool_meta(agent) is None


def test_forced_rebuild_always_carries_unified_warning_even_when_low():
    # 1.0 forced rebuild: the unified warning is ALWAYS present, even when the
    # rebuilt context dropped well below the recovery target. No separate molt or
    # proactive_hint field — one unified string.
    agent = _recon_agent(raw_event=dict(_RAW_EVENT), after_usage=0.40)
    event = meta_block.build_reconstruction_tool_meta(agent)
    assert event is not None
    assert event["type"] == "delayed_summarize_reconstruction"
    assert event["trigger_threshold"] == 1.0
    assert event["recovery_target"] == 0.60
    assert event["before"]["usage"] == 1.0
    assert event["after"]["usage"] == pytest.approx(0.40)
    assert event["after"]["context_tokens"] == 40000
    assert event["after"]["source"] == "provider_input_tokens"
    # Unified warning, always present; no branching molt/proactive_hint.
    assert "molt" not in event
    assert "proactive_hint" not in event
    warning = event["warning"]
    assert "Forced provider-context rebuild applied at the 100% hard context boundary" in warning
    assert "100000 tokens (100%) before" in warning
    assert "40000 tokens (40%) after" in warning
    assert "prefer a proactive" in warning
    assert "rebuild=true" in warning
    assert "0.75" in warning or "75%" in warning
    assert "60%" in warning or "0.6" in warning
    assert "molt" in warning
    assert "meta_guidance" in warning


def test_forced_rebuild_unified_warning_present_when_still_high():
    # Same unified warning whether after landed low or stayed high — no branching.
    agent = _recon_agent(raw_event=dict(_RAW_EVENT), after_usage=0.80)
    event = meta_block.build_reconstruction_tool_meta(agent)
    assert "warning" in event
    assert "molt" not in event
    assert "proactive_hint" not in event
    warning = event["warning"]
    assert "Forced provider-context rebuild" in warning
    assert "80000 tokens (80%) after" in warning
    # The conditional molt instruction is inside the one unified string.
    assert "molt" in warning


def test_reconstruction_tool_meta_after_prefers_provider_input_tokens():
    """B must be the PROVIDER-reported post-reconstruction input
    (_latest_input_tokens / window), not the local compacted-history estimate.
    Here provider says 0.70 while the local estimate says 0.30; B must be 0.70."""
    agent = _recon_agent(
        raw_event=dict(_RAW_EVENT), after_usage=0.70, local_usage=0.30
    )
    event = meta_block.build_reconstruction_tool_meta(agent)
    assert event["after"]["usage"] == pytest.approx(0.70)
    assert event["after"]["context_tokens"] == 70000
    assert event["after"]["source"] == "provider_input_tokens"
    # Provider value (not the local 0.30) is reported in the unified warning.
    assert "70000 tokens (70%) after" in event["warning"]


def test_reconstruction_tool_meta_after_falls_back_to_local_estimate():
    """When the provider input is unavailable (_latest_input_tokens == 0), B
    falls back to the local compacted-history estimate and records that source."""
    agent = _recon_agent(
        raw_event=dict(_RAW_EVENT), after_usage=None, local_usage=0.55
    )
    event = meta_block.build_reconstruction_tool_meta(agent)
    assert event["after"]["usage"] == pytest.approx(0.55)
    assert event["after"]["context_tokens"] == 55000
    assert event["after"]["source"] == "local_estimate"
    assert "55000 tokens (55%) after" in event["warning"]


def test_manual_rebuild_event_uses_recovery_molt_not_forced_warning():
    # The manual rebuild=true event carries NO forced-rebuild warning; when the
    # rebuilt context is still above the recovery target it carries the recovery
    # molt reminder instead.
    agent = _recon_agent(raw_event=dict(_RAW_MANUAL_EVENT), after_usage=0.70)
    event = meta_block.build_reconstruction_tool_meta(agent)
    assert event["type"] == "summarize_rebuild_only_reconstruction"
    assert "warning" not in event
    assert "proactive_hint" not in event
    molt = event["molt"]
    assert isinstance(molt, str)
    assert "runtime already rebuilt the provider context" in molt
    assert "70%" in molt
    assert "60%" in molt
    assert "molt deliberately" in molt


def test_manual_rebuild_event_below_recovery_target_no_molt():
    agent = _recon_agent(raw_event=dict(_RAW_MANUAL_EVENT), after_usage=0.40)
    event = meta_block.build_reconstruction_tool_meta(agent)
    assert event["type"] == "summarize_rebuild_only_reconstruction"
    assert "molt" not in event
    assert "warning" not in event


def test_reconstruction_tool_meta_is_one_shot():
    agent = _recon_agent(raw_event=dict(_RAW_EVENT), after_usage=0.40)
    first = meta_block.build_reconstruction_tool_meta(agent)
    assert first is not None
    # The session's take_pending_reconstruction_event already returned None on
    # the second call, so the kernel must not re-emit.
    assert meta_block.build_reconstruction_tool_meta(agent) is None


# ---------------------------------------------------------------------------
# notifications field removed 2026-05-02 (Task 11 of system-notification-as-
# tool-call redesign). System-source notifications are now delivered as
# synthetic notification(action="check") tool-call pairs spliced by
# BaseAgent._inject_notification_pair (the legacy tc_inbox splice path is
# dormant); see docs/plans/2026-05-02-system-notification-as-tool-call.md. Tests for the
# old inbox-drain path lived here and have been removed alongside the field.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# attach_active_notifications — moving single-slot, SPARSE / update-driven
# stamping.  The payload attaches on first appearance and re-attaches only when
# it materially changes (or on a deliberate notification(action=check) read);
# an unchanged payload is NOT chased onto every newest ordinary tool result.
# ---------------------------------------------------------------------------


def _notif_agent(working_dir):
    """Minimal agent stand-in. ``attach_active_notifications`` reads
    ``agent._working_dir`` and, on successful stamping, commits the
    current notification fingerprint to ``agent._notification_fp`` so
    the IDLE-path synthesized pair does not re-deliver the same state.

    ``_notification_payload_signature`` starts ``None`` (no payload emitted yet)
    so the first active payload always attaches; the sparse change-gate in
    ``attach_active_notifications`` updates it thereafter."""
    from tests._notification_store_helpers import notification_store_for

    return SimpleNamespace(
        _working_dir=working_dir,
        _notification_store=notification_store_for(working_dir),
        _notification_fp=(),
        _notification_payload_signature=None,
    )


def _write_email_notif(
    tmp_path,
    *,
    message: str = "Full email body",
    email_id: str = "email-1",
    subject: str = "Email subject",
    count: int = 1,
):
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "header": f"{count} unread",
        "icon": "📬",
        "priority": "normal",
        "data": {
            "count": count,
            "newest_received_at": "2026-07-06T07:00:00Z",
            "email_ids": [email_id],
            "emails": [
                {
                    "id": email_id,
                    "from": "human",
                    "to": ["mimo-1"],
                    "subject": subject,
                    "message": message,
                    "message_chars": len(message),
                    "message_truncated": False,
                    "time": "2026-07-06T07:00:00Z",
                    "unread": True,
                    "received_at": "2026-07-06T07:00:00Z",
                }
            ],
        },
    }
    (notif_dir / "email.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _telegram_message(message_id: int, *, text: str | None = None) -> dict:
    return {
        "id": f"main:123:{message_id}",
        "direction": "incoming",
        "sender": "Jason",
        "date": f"2026-07-05T09:00:{message_id % 60:02d}Z",
        "relative_time": "just now",
        "text": text or f"message {message_id}",
        "text_truncated": False,
    }


def _write_telegram_notif(tmp_path, messages: list[dict]) -> None:
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir(parents=True, exist_ok=True)
    latest = dict(messages[-1])
    latest["is_current"] = True
    payload = {
        "header": "1 new event from MCP 'telegram'",
        "icon": "💬",
        "priority": "high",
        "data": {
            "count": 1,
            "source": "telegram",
            "has_human_messages": True,
            "previews": [
                {
                    "from": "Jason",
                    "subject": "telegram message from Jason via main",
                    "preview": latest["text"],
                    "preview_truncated": False,
                    "platform": "telegram",
                    "conversation_ref": "main:123",
                    "message_ref": latest["id"],
                    "recent_messages": messages,
                    "latest_incoming": latest,
                }
            ],
        },
    }
    (notif_dir / "mcp.telegram.json").write_text(json.dumps(payload), encoding="utf-8")


def test_attach_active_notifications_first_payload_attaches(tmp_path):
    from tests._notification_store_helpers import fingerprint_notifications

    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)
    assert agent._notification_fp == ()

    # First batch: a single ToolResultBlock, no prior holder.  The final block
    # is the metadata carrier regardless of its handler content.  The very
    # first active payload always attaches (no prior signature to compare).
    first = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert holder is first
    assert "_notifications" not in first.content
    # The canonical notification payload nests under the carrier sidecar.
    assert "notifications" not in first.content  # not top-level anymore
    assert first.metadata["agent_meta"]["notifications"]["attention"] == {
        "email": {
            "header": "Email event",
            "icon": "📬",
            "priority": "normal",
            "data": {"email_ids": ["email-1"]},
            "instructions": (
                "High-attention email hook: full unread content lives in "
                "notification_persistent.email. Prefer email.dismiss after handling; "
                "use email.read/reply for source-of-truth mailbox actions. When "
                "handled through the email tool, the producer mirror updates or "
                "clears this notification."
            ),
        }
    }
    persistent_email = first.metadata["agent_meta"]["notifications"]["persistent"]["email"]
    assert persistent_email["email_ids"] == ["email-1"]
    assert persistent_email["emails"][0]["subject"] == "Email subject"
    assert "digest" not in persistent_email
    assert persistent_email["emails"][0]["message"] == "Full email body"
    assert first.metadata["agent_meta"]["guidance"]["transient"] == {
        "ref": "meta_guidance.notification_handling",
        "sources": ["email"],
    }
    assert "notification_guidance" not in first.metadata["agent_meta"]["notifications"]["attention"]["email"]
    # The sparse change-gate recorded a non-null signature for this payload.
    assert agent._notification_payload_signature is not None
    # Successful stamping must commit the fingerprint, so the IDLE-path
    # synthesized pair will treat this same state as already delivered.
    expected_fp = fingerprint_notifications(tmp_path)
    assert expected_fp != ()
    assert agent._notification_fp == expected_fp


def test_attach_active_notifications_unchanged_payload_not_restamped(tmp_path):
    # The complete current notification snapshot is repeated on the final
    # carrier, even when its payload is unchanged.
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert "notifications" in first.metadata["agent_meta"]

    # Second batch: the notification files are unchanged.  An ordinary tool
    # result must NOT receive the payload; the prior holder keeps it.
    second = ToolResultBlock(id="t2", name="x", content={"ok": False})
    new_holder = attach_active_notifications(agent, [second], prior_holder=holder)

    assert new_holder is second
    assert "notifications" in second.metadata["agent_meta"]
    # Prior holder remains historical and is not rewritten.
    assert "notifications" in first.metadata["agent_meta"]
    assert first.metadata["agent_meta"]["notifications"]["attention"]["email"]["data"] == {
        "email_ids": ["email-1"]
    }


def test_attach_active_notifications_changed_payload_reattaches_and_retains_prior(tmp_path):
    # When the notification payload materially changes, it re-attaches to the
    # newest result. The prior holder KEEPS its old payload as a historical
    # trace — notification payloads are timely transient state, and canonical
    # history is no longer retroactively stripped (Jason #4307); only the
    # newest emitted payload is current.
    from tests._notification_store_helpers import fingerprint_notifications

    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert "notifications" in first.metadata["agent_meta"]
    first_sig = agent._notification_payload_signature

    # Materially change the email channel payload.
    _write_email_notif(
        tmp_path,
        message="Three new email body",
        email_id="email-2",
        subject="Changed email",
        count=3,
    )

    second = ToolResultBlock(id="t2", name="x", content={"ok": False})
    new_holder = attach_active_notifications(agent, [second], prior_holder=holder)

    assert new_holder is second
    # The signature advanced with the material change.
    assert agent._notification_payload_signature != first_sig
    # First holder RETAINS its original payload as a historical trace.
    assert first.metadata["agent_meta"]["notifications"]["attention"]["email"]["data"] == {
        "email_ids": ["email-1"]
    }
    assert "guidance" in first.metadata["agent_meta"]
    assert second.metadata["agent_meta"]["notifications"]["attention"]["email"]["data"] == {
        "email_ids": ["email-2"]
    }
    persistent_email = second.metadata["agent_meta"]["notifications"]["persistent"]["email"]
    assert "digest" not in persistent_email
    assert persistent_email["emails"][0]["message"] == "Three new email body"
    assert persistent_email["emails"][0]["id"] == "email-2"
    assert agent._notification_fp == fingerprint_notifications(tmp_path)


def test_attach_active_notifications_unchanged_commits_fp_to_avoid_retry(tmp_path):
    # Even when an unchanged payload is not restamped, the fingerprint is
    # committed so an equivalent rewrite / same-material payload does not retry
    # forever against the IDLE-path synthesized pair.
    from tests._notification_store_helpers import fingerprint_notifications

    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [first], prior_holder=None)

    # Rewrite the same material payload with different JSON bytes.  The
    # byte-content fingerprint changes, but the canonical payload signature is
    # identical.
    email_path = tmp_path / ".notification" / "email.json"
    same_payload = json.loads(email_path.read_text(encoding="utf-8"))
    email_path.write_text(
        json.dumps(same_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    agent._notification_fp = (("stale.json", 1, 1),)

    second = ToolResultBlock(id="t2", name="x", content={"ok": False})
    new_holder = attach_active_notifications(agent, [second], prior_holder=holder)

    # The current whole snapshot is present and the fingerprint is committed.
    assert new_holder is second
    assert "notifications" in second.metadata["agent_meta"]
    assert agent._notification_fp == fingerprint_notifications(tmp_path)


def test_attach_active_notifications_unchanged_signature_without_holder_reattaches(tmp_path):
    # Defensive regression: if the signature says "unchanged" but the live
    # holder was lost (e.g. after unusual recovery), do NOT commit an invisible
    # notification state. Fall through and attach the payload to the target.
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert holder is first
    assert "notifications" in first.metadata["agent_meta"]

    # Simulate holder loss while the material signature remains recorded.
    agent._notification_live_holder = None
    second = ToolResultBlock(id="t2", name="x", content={"ok": False})
    new_holder = attach_active_notifications(agent, [second], prior_holder=None)

    assert new_holder is second
    assert "notifications" in second.metadata["agent_meta"]



def test_attach_active_notifications_check_read_receives_unchanged_payload(tmp_path):
    # A deliberate notification(action=check) placeholder result is a read
    # request: it must receive the current payload even when unchanged.
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    # First, an ordinary batch establishes the holder + signature.
    first = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert "notifications" in first.metadata["agent_meta"]

    # Now the agent voluntarily calls notification(action=check): its result is
    # the placeholder dict.  Even though the payload is unchanged, the check
    # result must receive the payload (deliberate read) and become the holder.
    check_result = ToolResultBlock(
        id="t2",
        name="notification",
        content={"_notification_placeholder": True, "message": "voluntary check"},
    )
    new_holder = attach_active_notifications(agent, [check_result], prior_holder=holder)

    assert new_holder is check_result
    assert "notifications" in check_result.metadata["agent_meta"]
    assert check_result.metadata["agent_meta"]["notifications"]["attention"]["email"]["data"] == {
        "email_ids": ["email-1"]
    }
    # The prior ordinary holder RETAINS its payload as a historical trace; the
    # check result is simply the newest (current) emission.
    assert "notifications" in first.metadata["agent_meta"]


def test_attach_active_notifications_empty_resets_signature_for_reappearance(tmp_path):
    # When notifications go empty the signature resets to None, so a later
    # reappearance of the SAME payload attaches again as the first active one.
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert agent._notification_payload_signature is not None

    # Notifications cleared through the injected Store.
    assert agent._notification_store.clear("email") is True
    empty_batch = ToolResultBlock(id="t2", name="x", content={"ok": False})
    result = attach_active_notifications(agent, [empty_batch], prior_holder=holder)
    assert result is None
    assert agent._notification_payload_signature is None
    # Prior holder RETAINS its payload as a historical trace (no retroactive
    # strip); it is simply no longer the live holder.
    assert "notifications" in first.metadata["agent_meta"]

    # Same payload reappears — must attach afresh (first-active semantics).
    _write_email_notif(tmp_path)
    third = ToolResultBlock(id="t3", name="x", content={"ok": True})
    new_holder = attach_active_notifications(agent, [third], prior_holder=None)
    assert new_holder is third
    assert "notifications" in third.metadata["agent_meta"]


def test_attach_active_notifications_adds_telegram_persistent_snapshot(tmp_path):
    messages = [_telegram_message(i) for i in range(1, 22)]
    _write_telegram_notif(tmp_path, messages)
    agent = _notif_agent(tmp_path)

    block = ToolResultBlock(
        id="t1",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-first"}}},
    )
    holder = attach_active_notifications(agent, [block], prior_holder=None)

    assert holder is block
    # Required path is _meta.notification_persistent.mcp.telegram (Jason #6148).
    telegram = block.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    # Seed block carries the English range comment; historical last-20 context
    # must not be mistaken for a burst of multiple new incoming messages.
    assert set(telegram.keys()) == {
        "messages",
        "events",
        "previous_block",
        "context_comment",
    }
    assert len(telegram["messages"]) == 20
    assert telegram["messages"][0]["id"] == "main:123:2"
    assert telegram["messages"][-1]["id"] == "main:123:21"
    assert telegram["context_comment"] == (
        "Messages 2–20 are historical context from the recent Telegram "
        "conversation. The current/new message is 21."
    )
    assert "burst_comment" not in telegram
    # First block: explicit hook with no predecessor.
    assert telegram["previous_block"] == {
        "path": "_meta.notification_persistent.mcp.telegram",
        "tool_result_id": None,
        "is_first_block": True,
    }
    assert telegram["events"] == [
        {
            "from": "Jason",
            "subject": "telegram message from Jason via main",
            "conversation_ref": "main:123",
            "message_ref": "main:123:21",
            "platform": "telegram",
        }
    ]
    assert agent._notification_persistent_telegram_message_ids[-1] == "main:123:21"
    assert agent._notification_persistent_telegram_last_tool_id == "call-first"


def test_attach_active_notifications_first_block_reseeds_with_retained_ids(tmp_path):
    messages = [_telegram_message(i) for i in range(101, 122)]
    _write_telegram_notif(tmp_path, messages)
    agent = _notif_agent(tmp_path)
    # Simulate a fresh provider context after molt/restart where the previous
    # block hook was reset, but the delivered-id cache retained enough old ids
    # that the old code incorrectly treated the first block as a delta.
    agent._notification_persistent_telegram_message_ids = [
        f"main:123:{i}" for i in range(1, 25)
    ]

    block = ToolResultBlock(
        id="t1",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-reseed"}}},
    )
    attach_active_notifications(agent, [block], prior_holder=None)

    telegram = block.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    assert len(telegram["messages"]) == 20
    assert telegram["messages"][0]["id"] == "main:123:102"
    assert telegram["messages"][-1]["id"] == "main:123:121"
    assert telegram["context_comment"] == (
        "Messages 102–120 are historical context from the recent Telegram "
        "conversation. The current/new message is 121."
    )
    assert telegram["previous_block"] == {
        "path": "_meta.notification_persistent.mcp.telegram",
        "tool_result_id": None,
        "is_first_block": True,
    }
    assert "burst_comment" not in telegram

    # Move (not duplicate): the ephemeral notifications.mcp.telegram lane is now
    # only a short high-attention identity hook.  Content and routing hooks live
    # in persistent.
    ephemeral = block.metadata["agent_meta"]["notifications"]["attention"]["mcp.telegram"]
    assert ephemeral["data"] == {"message_ids": ["main:123:121"]}
    assert "previews" not in ephemeral["data"]
    assert "source" not in ephemeral["data"]
    assert "count" not in ephemeral["data"]
    assert "has_human_messages" not in ephemeral["data"]
    assert "telegram message from Jason" not in ephemeral["instructions"]


def test_attach_active_notifications_adds_telegram_persistent_delta_with_comment(tmp_path):
    first_messages = [_telegram_message(i) for i in range(1, 21)]
    _write_telegram_notif(tmp_path, first_messages)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(
        id="t1",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-first"}}},
    )
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    first_tg = first.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    assert first_tg["messages"][-1]["id"] == "main:123:20"
    # First block hook: no predecessor.
    assert first_tg["previous_block"]["is_first_block"] is True
    assert first_tg["previous_block"]["tool_result_id"] is None

    second_messages = [_telegram_message(i) for i in range(2, 22)]
    _write_telegram_notif(tmp_path, second_messages)
    second = ToolResultBlock(
        id="t2",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-second"}}},
    )
    new_holder = attach_active_notifications(agent, [second], prior_holder=holder)

    assert new_holder is second
    # The previous holder keeps its persistent context AND its old ephemeral
    # Legacy root-notifications payload is retained only as a historical trace;
    # the current carrier uses agent_meta.notifications.
    assert "notifications" in first.metadata["agent_meta"]
    assert first.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]["messages"]
    telegram = second.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["telegram"]
    assert [message["id"] for message in telegram["messages"]] == ["main:123:21"]
    # Every non-first block hooks to the previous block via the prior tool id.
    previous_block = telegram["previous_block"]
    assert previous_block["path"] == "_meta.notification_persistent.mcp.telegram"
    assert previous_block["tool_result_id"] == "call-first"
    assert "is_first_block" not in previous_block
    assert previous_block["comment"] == (
        "For earlier Telegram context, see tool result call-first "
        "at _meta.notification_persistent.mcp.telegram."
    )
    assert agent._notification_persistent_telegram_last_tool_id == "call-second"


def test_attach_active_notifications_sanitizes_telegram_without_new_persistent_block(
    tmp_path,
):
    """Deliberate checks with already-delivered ids still keep notifications thin."""
    messages = [_telegram_message(i) for i in range(1, 21)]
    _write_telegram_notif(tmp_path, messages)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(
        id="t1",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-first"}}},
    )
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    assert "persistent" in first.metadata["agent_meta"]["notifications"]

    check_result = ToolResultBlock(
        id="t2",
        name="notification",
        content={"_notification_placeholder": True, "message": "voluntary check"},
    )
    new_holder = attach_active_notifications(agent, [check_result], prior_holder=holder)

    assert new_holder is check_result
    meta = check_result.metadata["agent_meta"]
    # No new message ids, but the routing event hook is Telegram content too, so
    # it is emitted in persistent while the transient lane stays generic.
    telegram = meta["notification_persistent"]["mcp"]["telegram"]
    assert telegram["messages"] == []
    assert telegram["events"] == [
        {
            "from": "Jason",
            "subject": "telegram message from Jason via main",
            "conversation_ref": "main:123",
            "message_ref": "main:123:20",
            "platform": "telegram",
        }
    ]
    assert telegram["previous_block"]["tool_result_id"] == "call-first"
    ephemeral = meta["notifications"]["mcp.telegram"]
    assert ephemeral["data"] == {"message_ids": ["main:123:20"]}
    assert "previews" not in ephemeral["data"]
    assert "count" not in ephemeral["data"]
    assert "has_human_messages" not in ephemeral["data"]


def test_build_notification_persistent_payload_lands_at_mcp_telegram_path():
    # Unit-level lock on the exact required key path and hook shape (Jason #6148).
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    messages = [_telegram_message(i) for i in range(1, 4)]
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {"previews": [{"recent_messages": messages, "latest_incoming": messages[-1]}]}
            }
        }
    }
    persistent = meta_block.build_notification_persistent_payload(agent, notification_payload)

    # Path is notification_persistent.mcp.telegram, NOT notification_persistent.telegram.
    assert "telegram" not in persistent["notification_persistent"]
    telegram = persistent["notification_persistent"]["mcp"]["telegram"]
    assert [m["id"] for m in telegram["messages"]] == ["main:123:1", "main:123:2", "main:123:3"]
    # First block always carries an explicit hook, even with no predecessor.
    assert telegram["previous_block"] == {
        "path": "_meta.notification_persistent.mcp.telegram",
        "tool_result_id": None,
        "is_first_block": True,
    }


def test_build_notification_persistent_payload_boundary_19_vs_20_delivered():
    messages = [_telegram_message(i) for i in range(1, 26)]
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {"previews": [{"recent_messages": messages}]}
            }
        }
    }

    nineteen = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[f"main:123:{i}" for i in range(1, 20)],
        _notification_persistent_telegram_last_tool_id="call-prev",
    )
    nineteen_payload = meta_block.build_notification_persistent_payload(
        nineteen, notification_payload
    )
    nineteen_tg = nineteen_payload["notification_persistent"]["mcp"]["telegram"]
    # With fewer than 20 in-context messages, seed with the last 20 messages.
    assert len(nineteen_tg["messages"]) == 20
    assert [m["id"] for m in nineteen_tg["messages"]][0] == "main:123:6"
    assert [m["id"] for m in nineteen_tg["messages"]][-1] == "main:123:25"

    twenty = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[f"main:123:{i}" for i in range(1, 21)],
        _notification_persistent_telegram_last_tool_id="call-prev",
    )
    twenty_payload = meta_block.build_notification_persistent_payload(
        twenty, notification_payload
    )
    twenty_tg = twenty_payload["notification_persistent"]["mcp"]["telegram"]
    # At 20 delivered messages, switch to delta-only delivery.
    assert [m["id"] for m in twenty_tg["messages"]] == [
        "main:123:21",
        "main:123:22",
        "main:123:23",
        "main:123:24",
        "main:123:25",
    ]
    assert twenty_tg["previous_block"]["tool_result_id"] == "call-prev"
    # Five new incoming messages arrived at once -> burst comment.
    assert twenty_tg["burst_comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_BURST_COMMENT
    )
    # Delta blocks (already have >=20 context) do not repeat the seed range
    # comment.
    assert "context_comment" not in twenty_tg


def test_build_notification_persistent_payload_seed_uses_notification_count_for_burst():
    messages = [_telegram_message(i) for i in range(1, 21)]
    messages[-1]["is_current"] = True
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "count": 2,
                    "previews": [{"recent_messages": messages, "latest_incoming": messages[-1]}],
                }
            }
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    assert telegram["context_comment"] == (
        "Messages 1–19 are historical context from the recent Telegram "
        "conversation. The current/new message is 20."
    )
    assert telegram["burst_comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_BURST_COMMENT
    )


def test_build_notification_persistent_payload_range_comment_uses_is_current():
    # When the producer flags is_current, the range comment identifies that id
    # as the new message and describes the rest as historical context.
    messages = [_telegram_message(i) for i in range(1, 21)]
    messages[-1]["is_current"] = True
    notification_payload = {
        "notifications": {
            "mcp.telegram": {"data": {"previews": [{"recent_messages": messages}]}}
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    assert telegram["context_comment"] == (
        "Messages 1–19 are historical context from the recent Telegram "
        "conversation. The current/new message is 20."
    )


def test_build_notification_persistent_payload_single_new_message_no_burst():
    # A single new incoming message must not be flagged as a burst.
    messages = [_telegram_message(i) for i in range(1, 26)]
    notification_payload = {
        "notifications": {
            "mcp.telegram": {"data": {"previews": [{"recent_messages": messages}]}}
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[f"main:123:{i}" for i in range(1, 25)],
        _notification_persistent_telegram_last_tool_id="call-prev",
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    assert [m["id"] for m in telegram["messages"]] == ["main:123:25"]
    assert "burst_comment" not in telegram


def test_build_notification_persistent_payload_self_outgoing_comment():
    # The agent's own outgoing message carries the continuity comment.
    incoming = _telegram_message(1)
    outgoing = _telegram_message(2)
    outgoing["direction"] = "outgoing"
    outgoing["sender"] = "me"
    messages = [incoming, outgoing]
    notification_payload = {
        "notifications": {
            "mcp.telegram": {"data": {"previews": [{"recent_messages": messages}]}}
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    out_msg = next(m for m in telegram["messages"] if m["direction"] == "outgoing")
    assert out_msg["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_SELF_OUTGOING_COMMENT
    )
    in_msg = next(m for m in telegram["messages"] if m["direction"] == "incoming")
    assert "comment" not in in_msg


def test_build_notification_persistent_payload_truncated_comment():
    # A truncated message directs the agent to telegram.read for full state.
    truncated = _telegram_message(1)
    truncated["text_truncated"] = True
    notification_payload = {
        "notifications": {
            "mcp.telegram": {"data": {"previews": [{"recent_messages": [truncated]}]}}
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    assert telegram["messages"][0]["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_TRUNCATED_COMMENT
    )


def test_build_notification_persistent_payload_truncated_outgoing_combines_comments():
    # A truncated outgoing message carries both hints joined, dropping neither.
    msg = _telegram_message(1)
    msg["direction"] = "outgoing"
    msg["text_truncated"] = True
    notification_payload = {
        "notifications": {
            "mcp.telegram": {"data": {"previews": [{"recent_messages": [msg]}]}}
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    comment = telegram["messages"][0]["comment"]
    assert meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_SELF_OUTGOING_COMMENT in comment
    assert meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_TRUNCATED_COMMENT in comment


def test_build_notification_persistent_payload_referenced_messages():
    # The full reply target, absent from messages, is carried under
    # referenced_messages with the English referenced comment.
    current = _telegram_message(25)
    current["is_current"] = True
    current["reply_to"] = "main:123:3"
    referenced = _telegram_message(3, text="the referenced original")
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "previews": [
                        {
                            "recent_messages": [current],
                            "referenced_messages": [referenced],
                        }
                    ]
                }
            }
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    assert "referenced_messages" in telegram
    ref = telegram["referenced_messages"][0]
    assert ref["id"] == "main:123:3"
    assert ref["text"] == "the referenced original"
    assert ref["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_REFERENCED_COMMENT
    )


def test_build_notification_persistent_payload_referenced_skipped_when_present():
    # If the reply target is already in messages, it is not duplicated into
    # referenced_messages.
    target = _telegram_message(3)
    current = _telegram_message(4)
    current["reply_to"] = "main:123:3"
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "previews": [
                        {
                            "recent_messages": [target, current],
                            "referenced_messages": [target],
                        }
                    ]
                }
            }
        }
    }
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    payload = meta_block.build_notification_persistent_payload(agent, notification_payload)
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    assert "referenced_messages" not in telegram


def test_sanitize_telegram_notification_after_persistent_strips_durable_text():
    messages = [_telegram_message(i) for i in range(1, 4)]
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "count": 3,
                    "source": "telegram",
                    "has_human_messages": True,
                    "previews": [
                        {
                            "from": "Jason",
                            "subject": "telegram message",
                            "preview": "the last-20 conversation transcript body",
                            "preview_truncated": False,
                            "platform": "telegram",
                            "conversation_ref": "main:123",
                            "message_ref": "main:123:3",
                            "recent_messages": messages,
                            "latest_incoming": messages[-1],
                        }
                    ],
                }
            }
        }
    }

    meta_block.sanitize_telegram_notification_after_persistent(notification_payload)

    telegram = notification_payload["notifications"]["mcp.telegram"]
    data = telegram["data"]
    # Durable message text, routing hooks, counts, and summaries are gone from the
    # ephemeral lane; only the event identity remains.
    assert data == {"message_ids": ["main:123:3"]}
    assert "previews" not in data
    assert "source" not in data
    assert "count" not in data
    assert "has_human_messages" not in data
    assert "telegram message" not in telegram["instructions"]


def test_sanitize_telegram_notification_after_persistent_uses_latest_incoming_id_fallback():
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "previews": [
                        {
                            # Partial older structured payload: no event message_ref,
                            # but latest_incoming still has a stable compound id.
                            "latest_incoming": _telegram_message(9),
                        }
                    ]
                }
            }
        }
    }

    meta_block.sanitize_telegram_notification_after_persistent(notification_payload)

    telegram = notification_payload["notifications"]["mcp.telegram"]
    assert telegram["data"] == {"message_ids": ["main:123:9"]}
    assert "previews" not in telegram["data"]


def test_sanitize_telegram_notification_after_persistent_is_noop_without_telegram():
    # No telegram notification → safe no-op, does not raise.
    payload = {"notifications": {"email": {"data": {"previews": [{"preview": "x"}]}}}}
    meta_block.sanitize_telegram_notification_after_persistent(payload)
    assert payload["notifications"]["email"]["data"]["previews"][0]["preview"] == "x"
    meta_block.sanitize_telegram_notification_after_persistent({})


# ---------------------------------------------------------------------------
# WeChat persistent lane — mirrors the Telegram lane through the shared
# parametrized IM machinery: seed/delta boundary at the producer's 10-message
# preview window, `_meta.notification_persistent.mcp.wechat` path, WeChat
# comment wording (wechat.read), and the transient
# `agent_meta.notifications.attention.mcp.wechat` lane reduced to a
# message_ids identity hook.
# ---------------------------------------------------------------------------


def _wechat_message(
    n: int,
    *,
    text: str | None = None,
    truncated: bool = False,
    direction: str = "incoming",
) -> dict:
    return {
        "id": f"wc-{n}",
        "direction": direction,
        "sender": "me" if direction == "outgoing" else "Jason",
        "date": f"2026-07-06T02:00:{n % 60:02d}+00:00",
        "relative_time": "just now",
        "text": text or f"wechat message {n}",
        "text_truncated": truncated,
    }


def _feishu_message(
    n: int,
    *,
    direction: str = "incoming",
    truncated: bool = False,
    current: bool = False,
) -> dict:
    msg = {
        "id": f"main:oc_chat:om_{n}",
        "direction": direction,
        "sender": "Jason" if direction == "incoming" else "me",
        "date": f"2026-07-06T09:00:{n:02d}Z",
        "text": f"feishu message {n}",
        "text_truncated": truncated,
    }
    if current:
        msg["is_current"] = True
    return msg


def _whatsapp_message(
    n: int,
    *,
    direction: str = "incoming",
    truncated: bool = False,
    message_type: str = "text",
    text: str | None = None,
    current: bool = False,
) -> dict:
    msg = {
        "id": f"default:15551234567:wamid.{n}",
        "direction": direction,
        "wa_id": "15551234567",
        "type": message_type,
        "text": f"whatsapp message {n}" if text is None else text,
        "text_truncated": truncated,
        "stored_at": f"2026-07-06T09:00:{n:02d}+00:00",
    }
    if current:
        msg["is_current"] = True
    return msg


def _write_wechat_notif(tmp_path, messages: list[dict]) -> None:
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir(parents=True, exist_ok=True)
    latest = dict(messages[-1])
    latest["is_current"] = True
    payload = {
        "header": "1 new event from MCP 'wechat'",
        "icon": "💬",
        "priority": "high",
        "data": {
            "count": 1,
            "source": "wechat",
            "has_human_messages": True,
            "previews": [
                {
                    "from": "Jason",
                    "subject": "wechat message from Jason",
                    "preview": latest["text"],
                    "preview_truncated": False,
                    "platform": "wechat",
                    "conversation_ref": "wxid_jason",
                    "message_ref": latest["id"],
                    "recent_messages": messages,
                    "latest_incoming": latest,
                }
            ],
        },
    }
    (notif_dir / "mcp.wechat.json").write_text(json.dumps(payload), encoding="utf-8")


def test_attach_active_notifications_adds_wechat_persistent_snapshot(tmp_path):
    messages = [_wechat_message(i) for i in range(1, 13)]
    _write_wechat_notif(tmp_path, messages)
    agent = _notif_agent(tmp_path)

    block = ToolResultBlock(
        id="t1",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-first"}}},
    )
    holder = attach_active_notifications(agent, [block], prior_holder=None)

    assert holder is block
    # Required path mirrors Telegram: _meta.notification_persistent.mcp.wechat.
    assert "wechat" not in block.metadata["agent_meta"]["notifications"]["persistent"]
    wechat = block.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["wechat"]
    assert set(wechat.keys()) == {
        "messages",
        "events",
        "previous_block",
        "context_comment",
    }
    # Seed block is bounded by the producer's 10-message preview window, not
    # Telegram's 20.
    assert len(wechat["messages"]) == 10
    assert wechat["messages"][0]["id"] == "wc-3"
    assert wechat["messages"][-1]["id"] == "wc-12"
    # WeChat local ids have no compound account:chat:message shape, so the
    # range comment falls back to the raw producer ids.
    assert wechat["context_comment"] == (
        "Messages wc-3–wc-11 are historical context from the recent WeChat "
        "conversation. The current/new message is wc-12."
    )
    assert "burst_comment" not in wechat
    assert wechat["previous_block"] == {
        "path": "_meta.notification_persistent.mcp.wechat",
        "tool_result_id": None,
        "is_first_block": True,
    }
    assert wechat["events"] == [
        {
            "from": "Jason",
            "subject": "wechat message from Jason",
            "conversation_ref": "wxid_jason",
            "message_ref": "wc-12",
            "platform": "wechat",
        }
    ]
    assert agent._notification_persistent_wechat_message_ids[-1] == "wc-12"
    assert agent._notification_persistent_wechat_last_tool_id == "call-first"

    # Move (not duplicate): the ephemeral notifications.mcp.wechat lane is now
    # only a short high-attention identity hook.
    ephemeral = block.metadata["agent_meta"]["notifications"]["attention"]["mcp.wechat"]
    assert ephemeral["data"] == {"message_ids": ["wc-12"]}
    assert ephemeral["header"] == "WeChat event"
    assert "previews" not in ephemeral["data"]
    assert "count" not in ephemeral["data"]
    assert "wechat message from Jason" not in ephemeral["instructions"]


def test_attach_active_notifications_adds_wechat_persistent_delta_with_comment(tmp_path):
    first_messages = [_wechat_message(i) for i in range(1, 11)]
    _write_wechat_notif(tmp_path, first_messages)
    agent = _notif_agent(tmp_path)

    first = ToolResultBlock(
        id="t1",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-first"}}},
    )
    holder = attach_active_notifications(agent, [first], prior_holder=None)
    first_wc = first.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["wechat"]
    assert first_wc["messages"][-1]["id"] == "wc-10"
    assert first_wc["previous_block"]["is_first_block"] is True

    second_messages = [_wechat_message(i) for i in range(2, 12)]
    _write_wechat_notif(tmp_path, second_messages)
    second = ToolResultBlock(
        id="t2",
        name="x",
        content={"ok": True, "_meta": {"tool_meta": {"id": "call-second"}}},
    )
    new_holder = attach_active_notifications(agent, [second], prior_holder=holder)

    assert new_holder is second
    # The previous holder keeps its persistent context AND its old ephemeral
    # Legacy root-notifications payload is retained only as a historical trace;
    # the current carrier uses agent_meta.notifications.
    assert "notifications" in first.metadata["agent_meta"]
    assert first.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["wechat"]["messages"]
    wechat = second.metadata["agent_meta"]["notifications"]["persistent"]["mcp"]["wechat"]
    assert [message["id"] for message in wechat["messages"]] == ["wc-11"]
    previous_block = wechat["previous_block"]
    assert previous_block["path"] == "_meta.notification_persistent.mcp.wechat"
    assert previous_block["tool_result_id"] == "call-first"
    assert "is_first_block" not in previous_block
    assert previous_block["comment"] == (
        "For earlier WeChat context, see tool result call-first "
        "at _meta.notification_persistent.mcp.wechat."
    )
    assert agent._notification_persistent_wechat_last_tool_id == "call-second"


def test_build_notification_persistent_payload_wechat_and_telegram_coexist():
    agent = SimpleNamespace(
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
        _notification_persistent_wechat_message_ids=[],
        _notification_persistent_wechat_last_tool_id=None,
    )
    tg_messages = [_telegram_message(i) for i in range(1, 4)]
    wc_messages = [_wechat_message(i) for i in range(1, 3)]
    notification_payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "previews": [
                        {
                            "recent_messages": tg_messages,
                            "latest_incoming": tg_messages[-1],
                        }
                    ]
                }
            },
            "mcp.wechat": {
                "data": {
                    "previews": [
                        {
                            "recent_messages": wc_messages,
                            "latest_incoming": wc_messages[-1],
                        }
                    ]
                }
            },
        }
    }
    persistent = meta_block.build_notification_persistent_payload(
        agent, notification_payload
    )

    mcp = persistent["notification_persistent"]["mcp"]
    assert [m["id"] for m in mcp["telegram"]["messages"]] == [
        "main:123:1", "main:123:2", "main:123:3",
    ]
    assert [m["id"] for m in mcp["wechat"]["messages"]] == ["wc-1", "wc-2"]
    # Each lane hooks to its own previous block path.
    assert mcp["telegram"]["previous_block"]["path"] == (
        "_meta.notification_persistent.mcp.telegram"
    )
    assert mcp["wechat"]["previous_block"]["path"] == (
        "_meta.notification_persistent.mcp.wechat"
    )


def test_wechat_persistent_message_comments_use_wechat_wording():
    agent = SimpleNamespace(
        _notification_persistent_wechat_message_ids=[],
        _notification_persistent_wechat_last_tool_id=None,
    )
    outgoing = _wechat_message(1, direction="outgoing")
    truncated = _wechat_message(2, truncated=True)
    notification_payload = {
        "notifications": {
            "mcp.wechat": {
                "data": {
                    "previews": [
                        {
                            "recent_messages": [outgoing, truncated],
                            "latest_incoming": truncated,
                        }
                    ]
                }
            }
        }
    }
    persistent = meta_block.build_notification_persistent_payload(
        agent, notification_payload
    )

    messages = persistent["notification_persistent"]["mcp"]["wechat"]["messages"]
    assert messages[0]["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_WECHAT_SELF_OUTGOING_COMMENT
    )
    assert messages[1]["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_WECHAT_TRUNCATED_COMMENT
    )
    # The truncation hint must point at the WeChat producer read tool.
    assert "wechat.read" in messages[1]["comment"]


def test_sanitize_wechat_notification_after_persistent_strips_durable_text():
    messages = [_wechat_message(i) for i in range(1, 4)]
    notification_payload = {
        "notifications": {
            "mcp.wechat": {
                "data": {
                    "count": 3,
                    "source": "wechat",
                    "has_human_messages": True,
                    "previews": [
                        {
                            "from": "Jason",
                            "subject": "wechat message",
                            "preview": "the last-10 conversation transcript body",
                            "preview_truncated": False,
                            "platform": "wechat",
                            "conversation_ref": "wxid_jason",
                            "message_ref": "wc-3",
                            "recent_messages": messages,
                            "latest_incoming": messages[-1],
                        }
                    ],
                }
            }
        }
    }

    meta_block.sanitize_wechat_notification_after_persistent(notification_payload)

    wechat = notification_payload["notifications"]["mcp.wechat"]
    data = wechat["data"]
    assert data == {"message_ids": ["wc-3"]}
    assert "previews" not in data
    assert "source" not in data
    assert "count" not in data
    assert "has_human_messages" not in data
    assert wechat["header"] == "WeChat event"
    assert "wechat message" not in wechat["instructions"]


def test_sanitize_wechat_notification_after_persistent_is_noop_without_wechat():
    # No wechat notification → safe no-op, does not raise; telegram untouched.
    payload = {
        "notifications": {
            "mcp.telegram": {"data": {"previews": [{"preview": "x"}]}}
        }
    }
    meta_block.sanitize_wechat_notification_after_persistent(payload)
    previews = payload["notifications"]["mcp.telegram"]["data"]["previews"]
    assert previews[0]["preview"] == "x"
    meta_block.sanitize_wechat_notification_after_persistent({})


def test_build_notification_persistent_payload_feishu_delta_lane():
    agent = SimpleNamespace(
        _notification_persistent_feishu_message_ids=[],
        _notification_persistent_feishu_last_tool_id=None,
    )
    messages = [_feishu_message(i) for i in range(1, 4)]
    messages[-1]["is_current"] = True
    notification_payload = {
        "notifications": {
            "mcp.feishu": {
                "data": {
                    "count": 2,
                    "previews": [
                        {
                            "from": "Jason",
                            "subject": "feishu message",
                            "platform": "feishu",
                            "conversation_ref": "main:oc_chat",
                            "message_ref": messages[-1]["id"],
                            "recent_messages": messages,
                            "latest_incoming": messages[-1],
                        }
                    ],
                }
            }
        }
    }

    persistent = meta_block.build_notification_persistent_payload(
        agent, notification_payload
    )
    feishu = persistent["notification_persistent"]["mcp"]["feishu"]

    assert [m["id"] for m in feishu["messages"]] == [
        "main:oc_chat:om_1",
        "main:oc_chat:om_2",
        "main:oc_chat:om_3",
    ]
    assert feishu["previous_block"] == {
        "path": "_meta.notification_persistent.mcp.feishu",
        "tool_result_id": None,
        "is_first_block": True,
    }
    assert feishu["burst_comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_FEISHU_BURST_COMMENT
    )
    assert feishu["events"] == [
        {
            "from": "Jason",
            "subject": "feishu message",
            "conversation_ref": "main:oc_chat",
            "message_ref": "main:oc_chat:om_3",
            "platform": "feishu",
        }
    ]

    meta_block.record_notification_persistent_delivery(
        agent, persistent, tool_call_id="call-feishu"
    )
    assert agent._notification_persistent_feishu_message_ids[-1] == (
        "main:oc_chat:om_3"
    )
    assert agent._notification_persistent_feishu_last_tool_id == "call-feishu"


def test_sanitize_feishu_notification_after_persistent_strips_durable_text():
    messages = [_feishu_message(i) for i in range(1, 3)]
    notification_payload = {
        "notifications": {
            "mcp.feishu": {
                "data": {
                    "count": 2,
                    "previews": [
                        {
                            "from": "Jason",
                            "subject": "feishu message",
                            "preview": "conversation preview",
                            "platform": "feishu",
                            "conversation_ref": "main:oc_chat",
                            "message_ref": messages[-1]["id"],
                            "recent_messages": messages,
                            "latest_incoming": messages[-1],
                        }
                    ],
                }
            }
        }
    }

    meta_block.sanitize_feishu_notification_after_persistent(notification_payload)

    feishu = notification_payload["notifications"]["mcp.feishu"]
    assert feishu["data"] == {"message_ids": ["main:oc_chat:om_2"]}
    assert feishu["header"] == "Feishu event"
    assert "conversation preview" not in feishu["instructions"]


def test_build_notification_persistent_payload_whatsapp_snapshot_lane():
    agent = SimpleNamespace()
    current = _whatsapp_message(3, current=True)
    notification_payload = {
        "notifications": {
            "mcp.whatsapp": {
                "data": {
                    "count": 1,
                    "previews": [
                        {
                            "from": "WhatsApp +15551234567",
                            "subject": "whatsapp message",
                            "platform": "whatsapp",
                            "conversation_ref": "default:15551234567",
                            "message_ref": current["id"],
                            "recent_messages": [
                                _whatsapp_message(1),
                                _whatsapp_message(2, direction="outgoing"),
                                current,
                            ],
                            "latest_incoming": current,
                        }
                    ],
                }
            }
        }
    }

    persistent = meta_block.build_notification_persistent_payload(
        agent, notification_payload
    )
    whatsapp = persistent["notification_persistent"]["mcp"]["whatsapp"]

    assert whatsapp["context_comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_WHATSAPP_CONTEXT_COMMENT
    )
    assert [m["id"] for m in whatsapp["messages"]] == [
        "default:15551234567:wamid.1",
        "default:15551234567:wamid.2",
        "default:15551234567:wamid.3",
    ]
    assert "previous_block" not in whatsapp
    assert "burst_comment" not in whatsapp
    assert whatsapp["messages"][1]["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_WHATSAPP_SELF_OUTGOING_COMMENT
    )
    assert whatsapp["events"] == [
        {
            "from": "WhatsApp +15551234567",
            "subject": "whatsapp message",
            "conversation_ref": "default:15551234567",
            "message_ref": "default:15551234567:wamid.3",
            "platform": "whatsapp",
        }
    ]

    meta_block.record_notification_persistent_delivery(
        agent, persistent, tool_call_id="call-whatsapp"
    )
    assert not hasattr(agent, "_notification_persistent_whatsapp_message_ids")
    assert not hasattr(agent, "_notification_persistent_whatsapp_last_tool_id")


def test_whatsapp_snapshot_message_comments_cover_truncated_and_media():
    agent = SimpleNamespace()
    media_message = _whatsapp_message(2, message_type="image")
    media_message["text"] = None
    notification_payload = {
        "notifications": {
            "mcp.whatsapp": {
                "data": {
                    "previews": [
                        {
                            "message_ref": "default:15551234567:wamid.2",
                            "recent_messages": [
                                _whatsapp_message(1, truncated=True),
                                media_message,
                            ],
                        }
                    ]
                }
            }
        }
    }

    persistent = meta_block.build_notification_persistent_payload(
        agent, notification_payload
    )
    messages = persistent["notification_persistent"]["mcp"]["whatsapp"]["messages"]

    assert messages[0]["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_WHATSAPP_TRUNCATED_COMMENT
    )
    assert messages[1]["comment"] == (
        meta_block.NOTIFICATION_PERSISTENT_WHATSAPP_MEDIA_COMMENT
    )


def test_sanitize_whatsapp_notification_after_persistent_strips_durable_text():
    current = _whatsapp_message(1)
    notification_payload = {
        "notifications": {
            "mcp.whatsapp": {
                "data": {
                    "count": 1,
                    "previews": [
                        {
                            "from": "WhatsApp +15551234567",
                            "subject": "whatsapp message",
                            "preview": "conversation preview",
                            "platform": "whatsapp",
                            "conversation_ref": "default:15551234567",
                            "message_ref": current["id"],
                            "recent_messages": [current],
                            "latest_incoming": current,
                        }
                    ],
                }
            }
        }
    }

    meta_block.sanitize_whatsapp_notification_after_persistent(notification_payload)

    whatsapp = notification_payload["notifications"]["mcp.whatsapp"]
    assert whatsapp["data"] == {"message_ids": ["default:15551234567:wamid.1"]}
    assert whatsapp["header"] == "WhatsApp event"
    assert "conversation preview" not in whatsapp["instructions"]


def test_attach_active_notifications_uses_canonical_mcp_payload(tmp_path):
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir(parents=True, exist_ok=True)
    (notif_dir / "mcp.telegram.json").write_text(
        '{"header": "2 new events", "icon": "💬", "priority": "normal", '
        '"data": {"previews": ['
        '{"from": "alice", "subject": "hello", "preview": "first body"}, '
        '{"from": "bob", "subject": "status", "preview": "second body"}'
        ']}}'
    )
    agent = _notif_agent(tmp_path)
    block = ToolResultBlock(id="t1", name="x", content={"ok": True})

    attach_active_notifications(agent, [block], prior_holder=None)

    meta = block.metadata["agent_meta"]
    payload = meta["notifications"]["attention"]["mcp.telegram"]
    assert "_notifications" not in block.content
    assert payload["data"] == {"message_ids": []}
    assert "previews" not in payload["data"]
    # Legacy preview-only Telegram payloads are preserved as persistent fallback
    # messages rather than staying in the transient notification lane.
    telegram = meta["notifications"]["persistent"]["mcp"]["telegram"]
    assert [message["text"] for message in telegram["messages"]] == [
        "first body",
        "second body",
    ]
    assert all(message["source"] == "notification_preview" for message in telegram["messages"])
    assert telegram["events"] == [
        {"from": "alice", "subject": "hello"},
        {"from": "bob", "subject": "status"},
    ]
    assert "notification_guidance" not in payload
    assert meta["guidance"]["transient"] == {
        "ref": "meta_guidance.notification_handling",
        "sources": ["mcp.telegram"],
    }


def test_attach_active_notifications_uses_canonical_system_payload(tmp_path):
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir(parents=True, exist_ok=True)
    (notif_dir / "system.json").write_text(
        '{"header": "1 system notification", "icon": "🔔", "priority": "normal", '
        '"data": {"events": ['
        '{"source": "daemon", "body": "Daemon finished with useful details"}'
        ']}}'
    )
    agent = _notif_agent(tmp_path)
    block = ToolResultBlock(id="t1", name="x", content={"ok": True})

    attach_active_notifications(agent, [block], prior_holder=None)

    payload = block.metadata["agent_meta"]["notifications"]["attention"]["system"]
    assert "_notifications" not in block.content
    assert payload["data"]["events"] == [
        {"source": "daemon", "body": "Daemon finished with useful details"}
    ]


def test_attach_active_notifications_uses_canonical_soul_payload(tmp_path):
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir(parents=True, exist_ok=True)
    (notif_dir / "soul.json").write_text(
        '{"header": "soul flow", "icon": "🌊", "priority": "normal", '
        '"data": {"voices": ['
        '{"source": "insights", "voice": "Remember to verify by email."}'
        ']}}'
    )
    agent = _notif_agent(tmp_path)
    block = ToolResultBlock(id="t1", name="x", content={"ok": True})

    attach_active_notifications(agent, [block], prior_holder=None)

    payload = block.metadata["agent_meta"]["notifications"]["attention"]["soul"]
    assert "_notifications" not in block.content
    assert payload["data"]["voices"] == [
        {"source": "insights", "voice": "Remember to verify by email."}
    ]


def test_attach_active_notifications_no_active_releases_prior_without_strip(tmp_path):
    # No `.notification/` directory at all → no active notifications.
    agent = _notif_agent(tmp_path)
    # Pre-existing fingerprint from a hypothetical earlier delivery; the
    # no-active path must NOT touch it (preserves IDLE-path semantics).
    sentinel_fp = (("sentinel.json", 1, 1),)
    agent._notification_fp = sentinel_fp

    # Explicit legacy-holder input: an old raw dict is accepted and must not be
    # mutated.  Current outputs use ToolResultBlock metadata sidecars.
    legacy_prior = {"ok": True, "_meta": {"notifications": {"email": {"header": "stale"}}}}
    new_block = ToolResultBlock(id="t1", name="x", content={"ok": "new"})

    result = attach_active_notifications(
        agent, [new_block], prior_holder=legacy_prior
    )
    assert result is None
    # The prior normal result RETAINS its old payload as a historical trace
    # (no retroactive strip); it just stops being the live holder.
    assert legacy_prior["_meta"]["notifications"] == {"email": {"header": "stale"}}
    assert agent._notification_live_holder is None
    assert "_meta" not in new_block.content
    # Crucially: with no active notifications, we leave the fp alone so
    # the IDLE-path synthesized pair retains whatever guard state it had.
    assert agent._notification_fp == sentinel_fp


def test_attach_active_notifications_empty_batch_preserves_fp(tmp_path):
    # An actually empty batch has no carrier, so it must not commit
    # `_notification_fp` — otherwise the IDLE-path could skip this state.
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)
    sentinel_fp = (("sentinel.json", 1, 1),)
    agent._notification_fp = sentinel_fp

    assert attach_active_notifications(agent, [], prior_holder=None) is None
    assert agent._notification_fp == sentinel_fp
    prior = ToolResultBlock(id="prior", name="x", content={"ok": True})
    assert attach_active_notifications(agent, [], prior_holder=prior) is prior
    assert agent._notification_fp == sentinel_fp


def test_attach_active_notifications_string_content_is_final_carrier(tmp_path):
    # A string-content ToolResultBlock is still the final carrier.  Eligibility
    # depends on the block, not on whether its handler content is a dict.
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)
    sentinel_fp = (("sentinel.json", 1, 1),)
    agent._notification_fp = sentinel_fp

    string_only = ToolResultBlock(id="t1", name="x", content="plain text")
    holder = attach_active_notifications(
        agent, [string_only], prior_holder=None
    )
    assert holder is string_only
    assert "notifications" in string_only.metadata["agent_meta"]
    assert agent._notification_fp != sentinel_fp
    assert string_only.content == "plain text"


def test_attach_active_notifications_uses_final_block_as_carrier(tmp_path):
    _write_email_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    # The final ToolResultBlock is the carrier even when its content is a
    # string; earlier blocks do not receive this batch's current agent_meta.
    earlier = ToolResultBlock(id="t1", name="x", content={"k": "earlier"})
    middle = ToolResultBlock(id="t2", name="x", content={"k": "middle"})
    string_tail = ToolResultBlock(id="t3", name="x", content="plain text")

    holder = attach_active_notifications(
        agent, [earlier, middle, string_tail], prior_holder=None
    )

    assert holder is string_tail
    assert "agent_meta" not in earlier.metadata
    assert "agent_meta" not in middle.metadata
    sidecar = string_tail.metadata["agent_meta"]
    assert "email" in sidecar["notifications"]["attention"]
    assert "email" in sidecar["notifications"]["persistent"]
    assert sidecar["guidance"]["transient"]["sources"] == ["email"]
    assert string_tail.content == "plain text"


# ---------------------------------------------------------------------------
# skeletonize_notification_holder / clear_active_notification_holder — release
# the live-holder reference WITHOUT mutating the released holder's content.
# Both a synthesized notification pair and a normal tool result RETAIN their
# payload as a historical trace — notification payloads are timely transient
# state and canonical history is never retroactively stripped or rewritten
# (Jason #4307).
# ---------------------------------------------------------------------------


def test_clear_active_notification_holder_retains_normal_live_holder_payload():
    # A normal tool result keeps its notification keys as a historical trace;
    # only the live-holder reference is dropped.
    stamped = {
        "ok": True,
        "_meta": {
            "tool_meta": {"id": "t1"},
            "notifications": {"email": {"data": {}}},
            "notification_guidance": "live guidance",
        },
    }
    agent = SimpleNamespace(_notification_live_holder=stamped)

    clear_active_notification_holder(agent)

    assert stamped == {
        "ok": True,
        "_meta": {
            "tool_meta": {"id": "t1"},
            "notifications": {"email": {"data": {}}},
            "notification_guidance": "live guidance",
        },
    }
    assert agent._notification_live_holder is None


def test_clear_active_notification_holder_retains_synthesized_holder_payload():
    # A synthesized holder is released from live tracking WITHOUT mutation —
    # skeletonization no longer replaces its content with a placeholder.
    # Historical notifications/notification_guidance on a synthesized pair
    # must survive full-history replay exactly like a normal tool result's.
    synthesized = {
        "_synthesized": True,
        "_meta": {
            "notification_guidance": "live guidance",
            "notifications": {"email": {"data": {"count": 1}}},
        },
        "current_time": "2026-05-13T00:00:00Z",
    }
    agent = SimpleNamespace(_notification_live_holder=synthesized)

    clear_active_notification_holder(agent)

    assert synthesized == {
        "_synthesized": True,
        "_meta": {
            "notification_guidance": "live guidance",
            "notifications": {"email": {"data": {"count": 1}}},
        },
        "current_time": "2026-05-13T00:00:00Z",
    }
    assert agent._notification_live_holder is None


def test_clear_active_notification_holder_handles_none_holder():
    agent = SimpleNamespace(_notification_live_holder=None)
    clear_active_notification_holder(agent)
    assert agent._notification_live_holder is None


def test_clear_active_notification_holder_handles_missing_key():
    holder = {"ok": True}  # no notification keys
    agent = SimpleNamespace(_notification_live_holder=holder)
    clear_active_notification_holder(agent)
    assert holder == {"ok": True}
    assert agent._notification_live_holder is None


# ---------------------------------------------------------------------------
# Post-molt active stamping regression.
#
# ``post-molt`` itself is an ordinary notification channel for active stamping.
# The race is narrower: the *same* ``psyche.molt`` result batch that publishes
# post-molt must skip stamping/committing it.  That per-batch deferral lives in
# ``base_agent.turn``; once a later ACTIVE tool batch exists, the post-molt
# notification may be consumed normally.
# ---------------------------------------------------------------------------


def _write_post_molt_notif(tmp_path):
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir(parents=True, exist_ok=True)
    (notif_dir / "post-molt.json").write_text(
        '{"header": "post-molt #1 — resume work", "icon": "🌱", '
        '"priority": "high", "data": {"molt_count": 1, '
        '"reminder": "continue the task"}}'
    )


def test_attach_active_notifications_can_stamp_post_molt_after_molt_batch(tmp_path):
    """Post-molt is not globally idle-only; later ACTIVE batches may consume it."""
    from tests._notification_store_helpers import fingerprint_notifications

    _write_post_molt_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    block = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [block], prior_holder=None)

    assert holder is block
    assert "post-molt" in block.metadata["agent_meta"]["notifications"]["attention"]
    assert agent._notification_fp == fingerprint_notifications(tmp_path)


def test_attach_active_notifications_stamps_post_molt_with_other_channels(tmp_path):
    """Mixed ordinary channels and post-molt stamp together on non-molt batches."""
    from tests._notification_store_helpers import fingerprint_notifications

    _write_email_notif(tmp_path)
    _write_post_molt_notif(tmp_path)
    agent = _notif_agent(tmp_path)

    block = ToolResultBlock(id="t1", name="x", content={"ok": True})
    holder = attach_active_notifications(agent, [block], prior_holder=None)

    assert holder is block
    assert "email" in block.metadata["agent_meta"]["notifications"]["attention"]
    assert "post-molt" in block.metadata["agent_meta"]["notifications"]["attention"]
    assert agent._notification_fp == fingerprint_notifications(tmp_path)


# ---------------------------------------------------------------------------
# attach_active_runtime — latest-only moving agent/guidance meta (mirrors the
# notification holder).  These cover the acceptance criteria directly:
#   * latest provider-visible result has _meta.agent_meta and _meta.guidance
#   * previous results lose _runtime when a newer dict result exists
#   * active_turn_tool_calls lives under _meta.agent_meta (not top-level)
# ---------------------------------------------------------------------------


def _runtime_agent(*, total_calls: int | None = None):
    """Agent stand-in: attach_active_runtime reads agent._executor.guard.total_calls."""
    guard = SimpleNamespace(total_calls=total_calls) if total_calls is not None else None
    executor = SimpleNamespace(guard=guard) if guard is not None else None
    return SimpleNamespace(_executor=executor)


def _stamped_result(meta, elapsed_ms, *, result=None, id="t1", name="x"):
    """A ToolResultBlock carrying runtime capture in its private sidecar."""
    content = {"status": "ok"} if result is None else result
    pending = dict(meta)
    pending["elapsed_ms"] = elapsed_ms
    return ToolResultBlock(
        id=id,
        name=name,
        content=content,
        metadata={"tool_meta": {"id": id}},
        _agent_pending={"agent_state": pending},
    )


def test_attach_active_runtime_counts_current_batch_tool_result_chars():
    agent = _fake_agent()
    block = _stamped_result(
        build_meta(agent), 12, result={"payload": "B" * 1200}, id="tc-batch", name="bash"
    )

    attach_active_runtime(agent, [block])

    agent_meta = block.metadata["agent_meta"]
    current = agent_meta["agent_state"]["current_tool_result_chars"]
    expected = len(json.dumps({"payload": "B" * 1200}, ensure_ascii=False, default=str))
    assert current["total_chars"] == expected
    assert current["top_results"] == [
        {
            "id": "tc-batch",
            "tool_name": "bash",
            "chars": expected,
        }
    ]


def test_attach_active_runtime_does_not_leak_tool_meta_token_usage_to_agent_meta():
    agent = _runtime_agent(total_calls=1)
    snapshot = {"scope": "provider_round", "input_tokens": 100}
    block = _stamped_result(
        {"current_time": "T", TOOL_META_TOKEN_USAGE_PENDING_KEY: snapshot},
        12,
        id="tc-token",
        name="bash",
    )

    attach_active_runtime(agent, [block])

    agent_meta = block.metadata["agent_meta"]
    assert TOOL_META_TOKEN_USAGE_PENDING_KEY not in agent_meta
    assert "_agent_pending" not in block.to_dict()

def test_attach_active_runtime_keeps_no_token_efficiency_in_agent_meta():
    # agent_meta must NOT carry any token diagnostics at its top level — those
    # live in agent_meta.agent_state only. Even if a stale token_efficiency snapshot
    # somehow rode along in pending, it is not promoted into agent_meta.
    agent = _runtime_agent(total_calls=3)
    block = _stamped_result(
        {"current_time": "T"},
        elapsed_ms=12,
        id="tc-eff",
        name="bash",
    )

    attach_active_runtime(agent, [block])

    agent_meta = block.metadata["agent_meta"]
    assert "token_efficiency" not in agent_meta
    assert agent_meta["agent_state"]["active_turn_tool_calls"] == 3


def test_attach_active_runtime_stamps_latest_with_state_and_guidance():
    agent = _runtime_agent(total_calls=3)
    block = _stamped_result({"current_time": "T", "context": {"usage": 0.1}}, 12)

    holder = attach_active_runtime(agent, [block], prior_holder=None)

    assert holder is block
    meta = block.metadata
    agent_meta = meta["agent_meta"]
    assert "current_time" not in agent_meta
    state = agent_meta["agent_state"]
    assert state["current_time"] == "T"
    assert state["context"] == {"usage": 0.1}
    assert state["elapsed_ms"] == 12
    # active_turn_tool_calls is sourced from the guard and lives under agent_state.
    assert state["active_turn_tool_calls"] == 3
    # Tail guidance is now a lightweight ref/hook pointing at the resident
    # meta_guidance system-prompt section — NOT the full ordered sections,
    # which moved into the system prompt to stop riding on every tail _meta.
    guidance = meta["agent_meta"]["guidance"]["persistent"]
    assert "sections" not in guidance
    assert "meta_guidance" in json.dumps(guidance)
    # The transient scaffolding is consumed.
    assert "_agent_pending" not in block.to_dict()
    # No top-level active_turn_tool_calls repetition, and no legacy _runtime key.
    assert "active_turn_tool_calls" not in block.content
    assert "_runtime" not in block.content



def test_attach_active_runtime_refreshes_adapter_comment_at_batch_boundary():
    agent = _runtime_agent(total_calls=1)

    def dynamic_comment():
        return {"adapter": "fake", "next_reset_in": 5}

    agent._session = SimpleNamespace(
        chat=SimpleNamespace(
            adapter_comment=lambda: {"adapter": "fake", "summary": "legacy provider note"},
            dynamic_adapter_comment=dynamic_comment,
        )
    )
    block = _stamped_result({"current_time": "T"}, 12, id="t-adapter")

    attach_active_runtime(agent, [block])

    agent_meta = block.metadata["agent_meta"]["agent_state"]
    tail = agent_meta["adapter_comment"]
    assert tail["adapter"] == "fake"
    assert tail["next_reset_in"] == 5
    assert "summary" not in tail
    assert "meta_guidance_ref" not in tail

def test_attach_active_runtime_moves_to_latest_and_retains_prior():
    agent = _runtime_agent(total_calls=1)

    first = _stamped_result({"current_time": "T1"}, 5, id="t1")
    holder = attach_active_runtime(agent, [first], prior_holder=None)
    assert "agent_meta" in first.metadata

    # Second batch: the complete current snapshot is carried by the final block;
    # the prior block remains historical and is not rewritten.
    agent = _runtime_agent(total_calls=2)
    second = _stamped_result({"current_time": "T2"}, 6, id="t2")
    new_holder = attach_active_runtime(agent, [second], prior_holder=holder)

    assert new_holder is second
    # Previous KEEPS its agent_meta/guidance as a historical trace.
    assert "agent_meta" in first.metadata
    assert "guidance" in first.metadata["agent_meta"]
    assert second.metadata["agent_meta"]["agent_state"]["current_time"] == "T2"
    assert second.metadata["agent_meta"]["agent_state"]["active_turn_tool_calls"] == 2


def test_attach_active_runtime_uses_final_block_as_carrier():
    agent = _runtime_agent(total_calls=4)
    earlier = _stamped_result({"current_time": "E"}, 1, id="t1")
    middle = _stamped_result({"current_time": "M"}, 2, id="t2")
    string_tail = _stamped_result(
        {"current_time": "S"}, 3, result="plain text", id="t3"
    )

    holder = attach_active_runtime(agent, [earlier, middle, string_tail], prior_holder=None)

    assert holder is string_tail
    assert "agent_meta" not in earlier.metadata
    assert "agent_meta" not in middle.metadata
    state = string_tail.metadata["agent_meta"]["agent_state"]
    assert state["elapsed_ms"] == 3
    assert state["active_turn_tool_calls"] == 4
    # Earlier blocks get no current agent_meta, and their pending scaffolding is stripped.
    assert "_agent_pending" not in earlier.to_dict()
    assert string_tail.content == "plain text"


def test_attach_active_runtime_empty_meta_keeps_prior_snapshot():
    # Negative invariant: a time-blind agent's results carry no _runtime_pending
    # (stamp_meta is a no-op).
    agent = _runtime_agent(total_calls=1)
    prior = _stamped_result({"current_time": "T1"}, 5, id="t1")
    holder = attach_active_runtime(agent, [prior], prior_holder=None)
    assert "agent_meta" in prior.metadata

    # Next batch: result was NOT stamped (no pending) — there is no new snapshot
    # to emit. Under the sparse contract the prior holder's agent_meta stays put
    # as the most recent emitted update point rather than being stripped with no
    # replacement.
    blind = ToolResultBlock(id="t2", name="x", content={"status": "ok"})
    new_holder = attach_active_runtime(agent, [blind], prior_holder=holder)

    assert new_holder is holder
    assert "agent_meta" in prior.metadata
    assert "agent_meta" not in blind.metadata


# ---------------------------------------------------------------------------
# Sparse / update-driven agent_meta: agent_meta is attached only when the
# material snapshot changes since the last emitted agent_meta, not re-stamped
# onto every latest tool result when unchanged.
# ---------------------------------------------------------------------------


def test_attach_active_runtime_first_snapshot_is_attached():
    # The very first material snapshot always attaches — there is no prior
    # signature to compare against.
    agent = _runtime_agent(total_calls=1)
    block = _stamped_result({"current_time": "T1"}, 5)

    holder = attach_active_runtime(agent, [block], prior_holder=None)

    assert holder is block
    assert "agent_meta" in block.metadata
    assert "guidance" in block.metadata["agent_meta"]


def test_attach_active_runtime_unchanged_snapshot_not_restamped_on_latest():
    # Same agent, a second batch whose material snapshot is identical. The
    # complete current snapshot still belongs to the final carrier.
    agent = _runtime_agent(total_calls=1)
    first = _stamped_result({"current_time": "T1"}, 5)
    holder = attach_active_runtime(agent, [first], prior_holder=None)
    assert "agent_meta" in first.metadata

    # Volatile-only change: counter ticks, elapsed differs, time differs.
    agent._executor.guard.total_calls = 2
    second = _stamped_result({"current_time": "T2"}, 6, id="t2")
    new_holder = attach_active_runtime(agent, [second], prior_holder=holder)

    # No material change still produces the explicit latest snapshot.
    assert "agent_meta" in second.metadata
    # Prior snapshot stays as a historical update point (not stripped).
    assert "agent_meta" in first.metadata
    assert new_holder is second
    # Private scaffolding is still cleared from the carrier serialization.
    assert "_agent_pending" not in second.to_dict()


def test_attach_active_runtime_material_change_reattaches():
    # After an unchanged batch, a genuinely material change (here: a new
    # adapter_comment scalar) re-attaches agent_meta to the newest result; the
    # older holder keeps its snapshot as a historical trace.  (The
    # sustained-pressure molt reminder is NO longer an agent_meta signal — it
    # lives on agent_meta.agent_state.context.molt now — so a neutral agent_meta
    # material field drives this mechanism test.)
    agent = _runtime_agent(total_calls=1)
    first = _stamped_result({"current_time": "T1"}, 5)
    holder = attach_active_runtime(agent, [first], prior_holder=None)

    # Unchanged batch still emits the explicit latest whole snapshot.
    agent._executor.guard.total_calls = 2
    second = _stamped_result({"current_time": "T2"}, 6, id="t2")
    holder2 = attach_active_runtime(agent, [second], prior_holder=holder)
    assert holder2 is second
    assert "agent_meta" in second.metadata

    # Material change: a new adapter_comment scalar appears in the snapshot.
    agent._executor.guard.total_calls = 3
    third = _stamped_result(
        {"current_time": "T3", "adapter_comment": {"note": "materially new"}}, 7, id="t3"
    )
    new_holder = attach_active_runtime(agent, [third], prior_holder=holder2)

    assert new_holder is third
    assert "agent_meta" in third.metadata
    assert third.metadata["agent_meta"]["agent_state"]["adapter_comment"] == {"note": "materially new"}
    # The older holder RETAINS its earlier snapshot as a historical update
    # point (no retroactive strip); the newest emission is the current one.
    assert "agent_meta" in first.metadata
    assert "adapter_comment" not in first.metadata["agent_meta"]["agent_state"]


def test_attach_active_runtime_new_large_result_is_material():
    # A new large tool result appearing in current_tool_result_chars.top_results
    # is a material change worth re-surfacing agent_meta, even if nothing else
    # changed.
    agent = _fake_agent()
    small = _stamped_result(build_meta(agent), 5, id="t1")
    holder = attach_active_runtime(agent, [small], prior_holder=None)
    assert "agent_meta" in small.metadata

    # A big result enters the batch — top_results changes materially.
    big = _stamped_result(build_meta(agent), 6, result={"payload": "B" * 5000}, id="t2", name="bash")
    new_holder = attach_active_runtime(agent, [big], prior_holder=holder)

    assert new_holder is big
    assert "agent_meta" in big.metadata
    top = big.metadata["agent_meta"]["agent_state"]["current_tool_result_chars"]["top_results"]
    assert any(entry["id"] == "t2" for entry in top)


def test_agent_meta_signature_ignores_volatile_bookkeeping():
    # The material signature must be identical when only volatile fields differ.
    from lingtai.kernel.meta_block import agent_meta_signature

    base = {
        "elapsed_ms": 5,
        "active_turn_tool_calls": 1,
        "current_time": "T1",
        "current_tool_result_chars": {
            "total_chars": 100,
            "threshold": 3000,
            "over_threshold_count": 0,
            "top_results": [],
        },
        "context": {"molt": "reminder"},
    }
    volatile_changed = {
        "elapsed_ms": 999,
        "active_turn_tool_calls": 42,
        "current_time": "T2",
        "current_tool_result_chars": {
            "total_chars": 999999,
            "threshold": 3000,
            "over_threshold_count": 0,
            "top_results": [],
        },
        "context": {"molt": "reminder"},
    }
    assert agent_meta_signature(base) == agent_meta_signature(volatile_changed)

    material_changed = dict(base)
    material_changed["context"] = {"molt": "different reminder"}
    assert agent_meta_signature(base) != agent_meta_signature(material_changed)


def test_attach_active_runtime_final_block_without_pending_keeps_prior_snapshot():
    # A final block without private pending capture has no new snapshot to
    # attach.  The behavior is due to missing capture, never content type.
    agent = _runtime_agent(total_calls=1)
    prior = _stamped_result({"current_time": "T1"}, 5, id="t1")
    holder = attach_active_runtime(agent, [prior], prior_holder=None)

    final_without_pending = ToolResultBlock(id="t2", name="x", content="text")
    new_holder = attach_active_runtime(
        agent, [final_without_pending], prior_holder=holder
    )

    assert new_holder is holder
    assert "agent_meta" in prior.metadata
    assert final_without_pending.content == "text"


def test_attach_active_runtime_omits_counter_when_no_guard():
    agent = _runtime_agent(total_calls=None)  # no executor/guard
    block = _stamped_result({"current_time": "T"}, 9)

    holder = attach_active_runtime(agent, [block], prior_holder=None)

    assert holder is block
    agent_meta = block.metadata["agent_meta"]["agent_state"]
    assert agent_meta["current_time"] == "T"
    assert "active_turn_tool_calls" not in agent_meta


# ---------------------------------------------------------------------------
# Runtime guidance payload/catalog schema validation.
# ---------------------------------------------------------------------------


def _valid_guidance():
    return {
        "schema_version": 1,
        "guidance_version": "0.1.0",
        "priority": "tail",
        "render_mode": "latest_tool_result_only",
        "sections": [
            {"id": "a", "title": "A", "body": "body a"},
            {"id": "b", "title": "B", "body": "body b"},
        ],
    }


def test_packaged_guidance_resource_is_valid():
    # The shipped guidance catalog must validate — this is the test that catches a
    # malformed packaged resource (build_runtime_guidance degrades silently).
    guidance = build_runtime_guidance()
    assert guidance != {}, "packaged guidance catalog failed to load/validate"
    validate_runtime_guidance(guidance)  # must not raise
    ids = [s["id"] for s in guidance["sections"]]
    assert len(ids) == len(set(ids)), "section ids must be unique"
    titles = [s["title"] for s in guidance["sections"]]
    assert len(titles) == len(set(titles)), "section titles must be unique"
    assert "summarize_reconstruction_threshold" in ids
    assert "Delayed summarization reconstruction threshold" in titles
    body = "\n".join(section["body"] for section in guidance["sections"])
    assert "summarize completed tool results" in body
    assert "raw text no longer needs inspection" in body
    assert "carrying more into each provider request" in body
    assert "Apply the token-efficiency principle" in body
    assert "do not molt automatically" in body
    assert "api_calls > 100" in body
    assert "mini molt for consumed tool results" in body
    assert "stronger whole-conversation boundary" in body
    assert "skip pre-molt summarize" in body
    assert "0.75" in body
    assert "1.0" in body
    assert "Do not call `refresh` just to apply a summarize" in body
    assert "does not mean the active provider-side context" in body
    assert "0.6 * context_window" in body
    # Unified contract: token diagnostics live in agent_meta.agent_state.token_usage; the
    # guidance points there and describes the since-last-molt session aggregate
    # half (cumulative/restored, surviving refresh — Jason FINAL correction).
    assert "token_usage" in body
    assert "since-last-molt" in body
    assert "session_cache_rate" in body
    # Current context state now documented under the session half.
    assert "context_usage" in body
    assert "guiding_avg_input_tokens_per_api_call" not in body
    assert "recent human-channel instructions" in body
    assert "last 30 Telegram messages" in body
    assert "not a personal standing rule file" in body


def test_validate_runtime_guidance_accepts_well_formed():
    data = _valid_guidance()
    assert validate_runtime_guidance(data) is data


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("schema_version"),
    lambda d: d.pop("sections"),
    lambda d: d.update(schema_version="1"),   # wrong type
    lambda d: d.update(schema_version=True),  # bool is not a valid int here
    lambda d: d.update(priority=""),          # empty string
    lambda d: d.update(sections=[]),          # empty list
    lambda d: d.update(sections="nope"),      # wrong type
])
def test_validate_runtime_guidance_rejects_malformed_top_level(mutate):
    data = _valid_guidance()
    mutate(data)
    with pytest.raises(GuidanceSchemaError):
        validate_runtime_guidance(data)


def test_validate_runtime_guidance_rejects_section_missing_field():
    data = _valid_guidance()
    data["sections"][0].pop("body")
    with pytest.raises(GuidanceSchemaError):
        validate_runtime_guidance(data)


def test_validate_runtime_guidance_rejects_duplicate_section_id():
    data = _valid_guidance()
    data["sections"][1]["id"] = "a"  # duplicate of sections[0].id
    with pytest.raises(GuidanceSchemaError):
        validate_runtime_guidance(data)


def test_validate_runtime_guidance_rejects_duplicate_section_title():
    data = _valid_guidance()
    data["sections"][1]["title"] = "A"  # duplicate of sections[0].title
    with pytest.raises(GuidanceSchemaError):
        validate_runtime_guidance(data)


def test_validate_runtime_guidance_rejects_non_dict():
    with pytest.raises(GuidanceSchemaError):
        validate_runtime_guidance(["not", "a", "dict"])


# ---------------------------------------------------------------------------
# Regression guard for the parent-identified blocker #1: move_runtime_block was
# defined but had NO call site, so _runtime was never injected. attach_active_runtime
# replaces it and MUST be wired into the tool-batch boundary in base_agent.turn.
# This catches a future "function defined but never called" regression cheaply
# without standing up a full turn harness.
# ---------------------------------------------------------------------------


def test_attach_active_runtime_is_wired_into_turn_boundary():
    import inspect
    from lingtai.kernel.base_agent import turn as _turn

    src = inspect.getsource(_turn)
    assert "attach_active_runtime(" in src, (
        "attach_active_runtime must be CALLED at the tool-batch boundary in "
        "base_agent/turn.py — otherwise _runtime is never injected (blocker #1)."
    )
    # The holder attribute the boundary mutates must be referenced too.
    assert "_runtime_live_holder" in src


# ---------------------------------------------------------------------------
# build_molt_context / context.molt — SUSTAINED context-pressure warning
# surfaced under current agent_meta.agent_state.context.molt (persists on every
# final carrier while active; routed via the _tool_meta_context transit key), not a
# dismissible notification.
#
# Corrected contract (channel B): the warning is NOT the old immediate
# ``usage >= 0.60`` trip-wire.  It is driven by the SessionManager
# sustained-pressure streak — context must be high (>= 0.75) for
# CONTEXT_PRESSURE_WARN_AFTER_ROUNDS (3) consecutive *fresh provider rounds*
# before the warning appears, giving summarize/reconstruction time to relieve
# pressure first.  A drop below 0.75 resets the streak and clears the warning.
# Wording directs: summarize first; if context cannot be brought below the 0.6
# recovery target, consider/perform molt.
# ---------------------------------------------------------------------------


def _molt_agent(*, warning_active=False, streak=0, psyche=True):
    """Minimal agent stand-in for build_molt_context.

    build_molt_context reads agent._intrinsics (must contain 'psyche') and the
    session's sustained-pressure streak state (set by SessionManager).
    """
    return SimpleNamespace(
        _intrinsics={"psyche": object()} if psyche else {},
        _config=SimpleNamespace(
            context_limit=None,
            time_awareness=True,
            timezone_awareness=True,
        ),
        _session=SimpleNamespace(
            context_pressure_warning_active=warning_active,
            context_pressure_streak=streak,
        ),
    )


def test_build_molt_context_absent_without_psyche():
    agent = _molt_agent(warning_active=True, streak=5, psyche=False)
    # Even with a fully-armed streak, no molt context when psyche is absent.
    assert build_molt_context(agent, 0.95) is None


def test_build_molt_context_absent_for_first_two_high_rounds():
    # Streak below the warn threshold (3) -> no warning yet, even at high usage.
    assert build_molt_context(_molt_agent(warning_active=False, streak=1), 0.90) is None
    assert build_molt_context(_molt_agent(warning_active=False, streak=2), 0.92) is None


def test_build_molt_context_old_immediate_0_60_no_longer_trips():
    """Regression: 0.61 (above the retired 0.60 trip-wire) with no sustained
    streak must NOT produce a warning anymore."""
    assert build_molt_context(_molt_agent(warning_active=False, streak=1), 0.61) is None


def test_build_molt_context_warns_from_third_high_round():
    agent = _molt_agent(warning_active=True, streak=3)
    molt = build_molt_context(agent, 0.90)
    assert isinstance(molt, str)
    assert "Context has stayed high" in molt
    assert "3 consecutive fresh model calls" in molt
    assert "90%" in molt
    assert "recovery target is 60%" in molt
    assert "batch tool results" in molt
    assert "Repeated summarize calls while context stays above 75%" in molt
    assert "substantially hurt token efficiency" in molt
    assert "batched summarize/reconstruction pass" in molt
    assert "stop repeating summarize" in molt
    assert "molt deliberately" in molt
    assert "psyche-manual" in molt


def test_build_molt_context_keeps_warning_while_streak_sustained():
    for streak in (3, 4, 7):
        molt = build_molt_context(_molt_agent(warning_active=True, streak=streak), 0.95)
        assert molt is not None
        assert f"{streak} consecutive fresh model calls" in molt
        assert "95%" in molt


def test_build_molt_context_is_natural_language_not_tag_payload():
    molt = build_molt_context(_molt_agent(warning_active=True, streak=3), 0.90)

    assert isinstance(molt, str)
    assert "stage" not in molt
    assert '"threshold"' not in molt
    assert "recovery_target" not in molt
    assert "summarize_then_molt" not in molt
    assert "procedures.md#performing-a-molt" not in molt
    serialized = json.dumps({"molt": molt})
    assert len(serialized) < 650


def test_build_molt_context_handles_missing_session_gracefully():
    agent = SimpleNamespace(_intrinsics={"psyche": object()})
    # No _session attribute at all -> no warning, no crash.
    assert build_molt_context(agent, 0.90) is None


def test_build_meta_attaches_context_molt_only_when_streak_armed():
    """build_meta integrates build_molt_context: context.molt is absent while the
    streak is below the warn threshold and present once the streak is armed,
    independent of the instantaneous usage on this particular build_meta call."""
    fake_iface = SimpleNamespace(estimate_context_tokens=lambda: 90)
    fake_session = SimpleNamespace(
        _token_decomp_dirty=False,
        _system_prompt_tokens=10,
        _tools_tokens=0,
        _latest_input_tokens=0,
        chat=SimpleNamespace(interface=fake_iface, context_window=lambda: 100),
        context_pressure_warning_active=False,
        context_pressure_streak=2,
    )
    agent = _molt_agent(warning_active=False, streak=2)
    agent._session = fake_session
    agent._uptime_anchor = None

    meta = build_meta(agent)
    assert meta_block._current_context_usage(agent) == pytest.approx(0.9)
    # Streak not yet armed -> no context reminder is emitted even though usage is
    # 0.9.  The molt reminder now rides on a transit key destined for the
    # Current agent_meta.agent_state.context block, so
    # neither the transit key nor a plain "context" key is present.
    assert meta_block.TOOL_META_CONTEXT_PENDING_KEY not in meta
    assert "context" not in meta

    # Arm the streak; same high usage now surfaces the warning under the transit
    # key (ToolExecutor._attach_tool_block promotes it into agent_state.context).
    fake_session.context_pressure_warning_active = True
    fake_session.context_pressure_streak = 3
    meta = build_meta(agent)
    context_transit = meta[meta_block.TOOL_META_CONTEXT_PENDING_KEY]
    assert isinstance(context_transit["molt"], str)
    assert "Context has stayed high" in context_transit["molt"]
    assert "3 consecutive fresh model calls" in context_transit["molt"]
    # It must NOT land in a plain agent-facing "context" key on the meta dict.
    assert "context" not in meta


def _molt_agent_with_reminder(reminder):
    """Agent stand-in whose session exposes a real ContextPressureReminder plus
    the live token-decomposition attributes build_meta reads for usage."""
    fake_iface = SimpleNamespace(estimate_context_tokens=lambda: 90)
    session = SimpleNamespace(
        _token_decomp_dirty=False,
        _system_prompt_tokens=10,
        _tools_tokens=0,
        _latest_input_tokens=0,
        chat=SimpleNamespace(interface=fake_iface, context_window=lambda: 100),
        context_pressure_reminder=reminder,
    )
    return SimpleNamespace(
        _intrinsics={"psyche": object()},
        _config=SimpleNamespace(
            context_limit=None, time_awareness=True, timezone_awareness=True
        ),
        _session=session,
        _uptime_anchor=None,
    )


def test_build_meta_current_molt_carries_reminder_and_event_payload():
    from lingtai.kernel.reminders.context_pressure import ContextPressureReminder

    reminder = ContextPressureReminder()
    for rid in (1, 2, 3):
        reminder.note_round(0.90, round_id=rid)
    agent = _molt_agent_with_reminder(reminder)

    # build_meta is SIDE-EFFECT-FREE and always carries the reminder text (transit
    # key, destined for agent_meta.agent_state.context.molt) AND the emission-event
    # payload while the warning is active — the DEDUP happens later, in
    # ToolExecutor._attach_tool_block (keyed on payload.last_round_id), not here.
    meta1 = build_meta(agent)
    assert "molt" in meta1[meta_block.TOOL_META_CONTEXT_PENDING_KEY]
    assert meta_block.TOOL_META_CONTEXT_EVENT_PENDING_KEY in meta1
    payload = meta1[meta_block.TOOL_META_CONTEXT_EVENT_PENDING_KEY]["payload"]
    assert payload["last_round_id"] == 3

    # Called again in the same round, build_meta STILL carries the payload (no
    # mutation / no dedup at this layer — the render-path text-prefix call and the
    # per-result stamp call must both be pure).
    meta2 = build_meta(agent)
    assert meta_block.TOOL_META_CONTEXT_EVENT_PENDING_KEY in meta2
    assert (
        meta2[meta_block.TOOL_META_CONTEXT_EVENT_PENDING_KEY]["payload"]["last_round_id"]
        == 3
    )


# ---------------------------------------------------------------------------
# build_cache_miss_budget_context / cache-miss budget guard.
#
# A since-last-molt soft cap on total cache-miss (uncached input) tokens. The
# cache-miss total is derived from the cumulative/restored
# agent.get_token_usage() totals as max(input_tokens - cached_tokens, 0), so a
# refresh does NOT reset it (Jason FINAL). Once it reaches/exceeds
# agent._config.cache_miss_budget, build_meta restamps a "cache miss budget
# {budget} reached, molt now" reminder into the _tool_meta_context transit
# sub-object (promoted to agent_meta.agent_state.context.molt) and surfaces
# cache_miss_budget / cache_miss_tokens under agent_meta.agent_state.context. It
# is a soft signal, not a new event route.
# ---------------------------------------------------------------------------


def _budget_agent(
    *,
    budget=1_000_000,
    input_tokens=0,
    cached_tokens=0,
    psyche=True,
    warning_active=False,
    streak=0,
    has_getter=True,
):
    """Minimal agent stand-in for build_cache_miss_budget_context / build_meta.

    Carries a get_token_usage() returning the given cumulative input/cached
    token totals (the since-last-molt basis the guard now reads), plus the
    streak fields build_molt_context reads (so the "both warnings active" case
    can be exercised through build_meta).
    """
    session = SimpleNamespace(
        _token_decomp_dirty=True,
        context_pressure_warning_active=warning_active,
        context_pressure_streak=streak,
    )
    agent = SimpleNamespace(
        _intrinsics={"psyche": object()} if psyche else {},
        _config=SimpleNamespace(
            cache_miss_budget=budget,
            context_limit=None,
            time_awareness=True,
            timezone_awareness=True,
        ),
        _session=session,
    )
    if has_getter:
        agent.get_token_usage = lambda: {
            "input_tokens": input_tokens,
            "cached_tokens": cached_tokens,
            "api_calls": 1,
        }
    return agent


def test_cache_miss_budget_context_none_below_budget():
    # cache_miss = 900k - 100k = 800k < 1M -> no context.
    agent = _budget_agent(budget=1_000_000, input_tokens=900_000, cached_tokens=100_000)
    assert build_cache_miss_budget_context(agent) is None


def test_cache_miss_budget_context_present_at_budget():
    # cache_miss = 1.0M - 0 = 1.0M == budget -> reminder (inclusive >=).
    agent = _budget_agent(budget=1_000_000, input_tokens=1_000_000, cached_tokens=0)
    ctx = build_cache_miss_budget_context(agent)
    assert isinstance(ctx, dict)
    assert ctx["molt"] == "cache miss budget 1000000 reached, molt now"
    assert ctx["cache_miss_budget"] == 1_000_000
    assert ctx["cache_miss_tokens"] == 1_000_000


def test_cache_miss_budget_context_present_above_budget_with_cache():
    # cache_miss = 1.5M - 200k = 1.3M >= 1M budget.
    agent = _budget_agent(budget=1_000_000, input_tokens=1_500_000, cached_tokens=200_000)
    ctx = build_cache_miss_budget_context(agent)
    assert ctx["cache_miss_tokens"] == 1_300_000
    assert ctx["cache_miss_budget"] == 1_000_000
    assert ctx["molt"] == "cache miss budget 1000000 reached, molt now"


def test_cache_miss_budget_context_clamps_negative_cache_miss_to_zero():
    # cached > input (odd provider accounting) -> cache_miss clamps to 0, no warn.
    agent = _budget_agent(budget=1, input_tokens=100, cached_tokens=500)
    assert build_cache_miss_budget_context(agent) is None


def test_cache_miss_budget_context_honors_custom_budget():
    agent = _budget_agent(budget=250_000, input_tokens=250_000, cached_tokens=0)
    ctx = build_cache_miss_budget_context(agent)
    assert ctx["molt"] == "cache miss budget 250000 reached, molt now"
    assert ctx["cache_miss_budget"] == 250_000


def test_cache_miss_budget_context_absent_without_psyche():
    # Consistent with build_molt_context: no psyche intrinsic -> no reminder.
    agent = _budget_agent(input_tokens=2_000_000, cached_tokens=0, psyche=False)
    assert build_cache_miss_budget_context(agent) is None


def test_cache_miss_budget_context_graceful_without_getter():
    agent = _budget_agent(input_tokens=2_000_000, cached_tokens=0, has_getter=False)
    assert build_cache_miss_budget_context(agent) is None


def test_cache_miss_budget_context_absent_for_nonpositive_budget():
    # Defensive: a non-positive / non-int budget disables the guard, never warns.
    for bad in (0, -1, None, "1000000"):
        agent = _budget_agent(budget=bad, input_tokens=5_000_000, cached_tokens=0)
        assert build_cache_miss_budget_context(agent) is None


def test_build_meta_attaches_budget_context_at_budget():
    """build_meta integrates the budget guard: at/above budget the transit
    sub-object carries the molt warning plus the budget fields."""
    agent = _budget_agent(budget=1_000_000, input_tokens=1_200_000, cached_tokens=0)
    meta = build_meta(agent)
    ctx = meta[meta_block.TOOL_META_CONTEXT_PENDING_KEY]
    assert ctx["molt"] == "cache miss budget 1000000 reached, molt now"
    assert ctx["cache_miss_budget"] == 1_000_000
    assert ctx["cache_miss_tokens"] == 1_200_000
    # Budget guard is not a new event route: no emission-event payload.
    assert meta_block.TOOL_META_CONTEXT_EVENT_PENDING_KEY not in meta


def test_build_meta_no_budget_context_below_budget():
    agent = _budget_agent(budget=1_000_000, input_tokens=500_000, cached_tokens=0)
    meta = build_meta(agent)
    assert meta_block.TOOL_META_CONTEXT_PENDING_KEY not in meta


def test_build_meta_preserves_both_warnings_when_context_pressure_also_active():
    """When the sustained context-pressure warning AND the cache-miss budget
    warning are both active, both must survive in agent_meta.agent_state.context.molt — the
    budget line is appended and the context-pressure prose is preserved — and the
    budget fields ride alongside."""
    from lingtai.kernel.reminders.context_pressure import ContextPressureReminder

    # Drive a real context decomposition (usage 0.9) with an armed real reminder,
    # plus a cache-miss total over budget.
    fake_iface = SimpleNamespace(estimate_context_tokens=lambda: 90)
    reminder = ContextPressureReminder()
    reminder.streak = 3  # >= warn_after_rounds (3) -> active
    reminder.last_round_id = 7
    agent = _budget_agent(budget=1_000_000, input_tokens=1_200_000, cached_tokens=0)
    agent._session._token_decomp_dirty = False
    agent._session._system_prompt_tokens = 10
    agent._session._tools_tokens = 0
    agent._session._latest_input_tokens = 0
    agent._session.context_pressure_reminder = reminder
    agent._session.chat = SimpleNamespace(
        interface=fake_iface, context_window=lambda: 100
    )

    meta = build_meta(agent)
    ctx = meta[meta_block.TOOL_META_CONTEXT_PENDING_KEY]
    molt = ctx["molt"]
    # Context-pressure prose preserved.
    assert "Context has stayed high" in molt
    assert "3 consecutive fresh model calls" in molt
    # Budget warning also present, appended on its own line.
    assert "cache miss budget 1000000 reached, molt now" in molt
    assert molt.endswith("cache miss budget 1000000 reached, molt now")
    # Budget fields present alongside.
    assert ctx["cache_miss_budget"] == 1_000_000
    assert ctx["cache_miss_tokens"] == 1_200_000
    # The context-pressure emission event still hashes ONLY the pressure message,
    # not the combined text (channel-B dedup/logging semantics are unchanged).
    from lingtai.kernel.reminders.context_pressure import reminder_message_hash
    pressure_only = reminder.current_molt_context(0.9)
    payload = meta[meta_block.TOOL_META_CONTEXT_EVENT_PENDING_KEY]["payload"]
    assert payload["message_hash"] == reminder_message_hash(pressure_only)


def test_attach_tool_block_promotes_budget_context_and_pops_transit_key():
    """_attach_tool_block promotes the budget sub-object (molt + budget fields)
    into agent_meta.agent_state.context and pops the transit key from the
    private pending capture so it never lands as a transit key on the wire."""
    from lingtai.kernel.loop_guard import LoopGuard
    from lingtai.kernel.tool_executor import _DEFAULT_MAX_RESULT_CHARS, ToolExecutor

    agent = _budget_agent(budget=1_000_000, input_tokens=1_200_000, cached_tokens=0)
    meta = build_meta(agent)
    executor = ToolExecutor(
        dispatch_fn=lambda name, args: {},
        make_tool_result_fn=lambda name, result, **kw: result,
        guard=LoopGuard(max_total_calls=50),
        working_dir="/tmp",
        max_result_chars=_DEFAULT_MAX_RESULT_CHARS,
    )
    # Runtime capture is staged by the executor, not written into handler content.
    executor._pending_meta_by_call_id["tc1"] = dict(meta)
    result = {"ok": True}
    wire = executor._attach_tool_block(result, tool_call_id="tc1", elapsed_ms=5)
    context = wire["_meta"]["agent_meta"]["agent_state"]["context"]
    assert context["molt"] == "cache miss budget 1000000 reached, molt now"
    assert context["cache_miss_budget"] == 1_000_000
    assert context["cache_miss_tokens"] == 1_200_000
    # The transit key is consumed before agent_meta.agent_state is returned.
    assert meta_block.TOOL_META_CONTEXT_PENDING_KEY not in wire


def test_build_context_rebuild_hint_stamps_after_high_ratio():
    agent = SimpleNamespace(_intrinsics={"system"})

    assert build_context_rebuild_hint(agent, 0.7499) is None
    hint = build_context_rebuild_hint(agent, 0.75)

    assert hint is not None
    assert "context now above 75%" in hint
    assert "rebuild=true" in hint
    # The hint clarifies that recording summaries does not itself rebuild the
    # provider context, that rebuild=true is a permitted option (not required),
    # and that the runtime forces a rebuild at the 1.0 hard boundary otherwise.
    assert "does NOT itself rebuild the active provider context" in hint
    assert "forces a rebuild at the 1.0 hard boundary" in hint
    assert "meta_guidance" in hint
    assert build_context_rebuild_hint(SimpleNamespace(_intrinsics=set()), 0.90) is None


# ---------------------------------------------------------------------------
# Persistent post-forced-rebuild overflow warning routed to the permanent
# current-state channel agent_meta.agent_state.context.molt (Jason, 2026-07-12). The adapter
# owns the one-shot latch + verification; build_meta only renders + merges the
# exact sentence, preserving any coexisting sustained-pressure / cache-miss lines.
# ---------------------------------------------------------------------------


def _overflow_chat(usage):
    """Minimal chat stand-in exposing context_overflow_status + decomposition."""
    class _Chat:
        def context_window(self_):
            return 1000

        def context_overflow_status(self_):
            return None if usage is None else {"usage": usage}

        class _iface:
            @staticmethod
            def estimate_context_tokens():
                return 1000

        interface = _iface()

    return _Chat()


def test_build_context_overflow_warning_present_only_when_status_active():
    from lingtai.kernel.meta_block import build_context_overflow_warning
    from lingtai.kernel.reminders.context_pressure import (
        render_forced_rebuild_failed_warning,
    )

    active = SimpleNamespace(_session=SimpleNamespace(chat=_overflow_chat(1000 / 900)))
    assert build_context_overflow_warning(active) == (
        render_forced_rebuild_failed_warning(1000 / 900)
    )

    # No status / no chat / no session -> no warning (never invented).
    assert build_context_overflow_warning(
        SimpleNamespace(_session=SimpleNamespace(chat=_overflow_chat(None)))
    ) is None
    assert build_context_overflow_warning(
        SimpleNamespace(_session=SimpleNamespace(chat=None))
    ) is None
    assert build_context_overflow_warning(SimpleNamespace(_session=None)) is None


def test_build_meta_preserves_sustained_overflow_and_budget_molt_lines():
    from lingtai.kernel.meta_block import TOOL_META_CONTEXT_PENDING_KEY
    from lingtai.kernel.reminders.context_pressure import (
        render_forced_rebuild_failed_warning,
    )

    chat = _overflow_chat(1000 / 900)  # ~1.111 > 1.0 -> overflow warning active
    agent = SimpleNamespace(
        _intrinsics={"psyche", "system"},
        _config=SimpleNamespace(
            time_awareness=False,
            timezone_awareness=False,
            language="en",
            context_limit=1000,
            cache_miss_budget=1000,
        ),
        get_token_usage=lambda: {"input_tokens": 5000, "cached_tokens": 0},
        _session=SimpleNamespace(
            _system_prompt_tokens=100,
            _tools_tokens=0,
            _latest_input_tokens=1000,
            _token_decomp_dirty=False,
            chat=chat,
            _chat=chat,
            # Compat sustained-pressure surface (no real reminder object needed).
            context_pressure_reminder=None,
            context_pressure_warning_active=True,
            context_pressure_streak=3,
        ),
    )

    molt = build_meta(agent)[TOOL_META_CONTEXT_PENDING_KEY]["molt"]
    overflow = render_forced_rebuild_failed_warning(1000 / 900)

    # All three warnings coexist, each on its own newline.
    lines = molt.split("\n")
    assert overflow in lines                       # exact overflow sentence, verbatim
    assert any("Context has stayed high" in ln for ln in lines)  # sustained-pressure
    assert any("cache miss budget" in ln for ln in lines)        # cache-miss budget
