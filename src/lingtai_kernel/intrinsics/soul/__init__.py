"""Soul intrinsic — the agent's inner voice.

Three actions:
    flow    — past-self consultation appendix. Every ``_soul_delay`` seconds,
              fires M=1+K parallel LLM calls (1 stepped-back read of the
              current chat as "insights", K random past-snapshot
              consultations sampled from history/snapshots/). Voices bundle
              into one synthetic (assistant{tool_call}, user{tool_result})
              pair with action="flow"; the pair is enqueued on tc_inbox
              with replace_in_history=True so the drain side enforces a
              single-slot invariant in chat history. Mechanical — agent
              cannot invoke manually.
    inquiry — sync mirror session. Clones conversation (text+thinking only),
              sends question, returns answer in tool result. On-demand.
    config  — adjust soul flow knobs. Accepts any subset of two optional
              fields: delay_seconds (wall-clock cadence), consultation_past_count
              (K, number of past-self voices per fire). Updates live state,
              restarts the wall-clock timer if delay changed, persists to
              init.json.
"""
from __future__ import annotations

# Re-export constants from config.py
from .config import (
    SOUL_DELAY_MIN_SECONDS,
    CONSULTATION_PAST_COUNT_MIN,
    CONSULTATION_PAST_COUNT_MAX,
    SOUL_VOICE_BUILTINS,
    SOUL_VOICE_PROMPT_MAX,
)

# Re-export private helpers consumed by base_agent.py and tests
from .config import (
    _handle_config,
    _handle_voice,
    _persist_soul_config,
    _persist_soul_voice,
    _atomic_write_init,
    _build_soul_system_prompt,
)

# Re-export consultation pipeline
from .consultation import (
    _CONSULTATION_SYSTEM_PROMPT,
    _CONSULTATION_TOOL_REFUSAL,
    _CONSULTATION_MAX_ROUNDS,
    _DIARY_CUE_TOKEN_CAP,
    _send_with_timeout,
    _render_current_diary,
    _write_soul_tokens,
    _load_snapshot_interface,
    _fit_interface_to_window,
    _kind_for_source,
    _build_consultation_cue,
    _run_consultation,
    _list_snapshot_paths,
    _run_consultation_batch,
    build_consultation_pair,
)

# Re-export inquiry
from .inquiry import soul_inquiry


def get_description(lang: str = "en") -> str:
    from ...i18n import t
    return t(lang, "soul.description")


def get_schema(lang: str = "en") -> dict:
    from ...i18n import t
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["inquiry", "flow", "config", "voice"],
                "description": t(lang, "soul.action_description"),
            },
            "inquiry": {
                "type": "string",
                "description": t(lang, "soul.inquiry_description"),
            },
            "delay_seconds": {
                "type": "number",
                "minimum": SOUL_DELAY_MIN_SECONDS,
                "description": t(lang, "soul.delay_seconds_description"),
            },
            "consultation_past_count": {
                "type": "integer",
                "minimum": CONSULTATION_PAST_COUNT_MIN,
                "maximum": CONSULTATION_PAST_COUNT_MAX,
                "description": t(lang, "soul.consultation_past_count_description"),
            },
            "set": {
                "type": "string",
                "description": t(lang, "soul.voice_set_description"),
            },
            "prompt": {
                "type": "string",
                "maxLength": SOUL_VOICE_PROMPT_MAX,
                "description": t(lang, "soul.voice_prompt_description"),
            },
        },
        "required": ["action"],
    }


def handle(agent, args: dict) -> dict:
    """Handle soul tool — inquiry and config are agent-invocable; flow
    is mechanical and fires on a wall-clock timer (cannot be invoked
    manually).
    """
    action = args.get("action", "")

    if action == "flow":
        return {
            "error": (
                f"soul flow fires automatically every {agent._soul_delay}s. "
                "It cannot be invoked manually. Use inquiry for on-demand "
                "reflection, or config to change the cadence."
            ),
        }

    if action == "inquiry":
        inquiry = args.get("inquiry", "")
        if not isinstance(inquiry, str) or not inquiry.strip():
            return {"error": "inquiry is required — what do you want to reflect on?"}

        agent._log("soul_inquiry", inquiry=inquiry.strip()[:200])

        result = soul_inquiry(agent, inquiry.strip())

        if result:
            agent._persist_soul_entry(result, mode="inquiry")
            agent._log("soul_inquiry_done")
            return {"status": "ok", "voice": result["voice"]}
        else:
            agent._log("soul_inquiry_done")
            return {"status": "ok", "voice": "(silence)"}

    if action == "config":
        return _handle_config(agent, args)

    if action == "voice":
        return _handle_voice(agent, args)

    return {
        "error": (
            f"Unknown soul action: {action}. Use inquiry, config, voice, "
            "or wait for flow (mechanical)."
        )
    }
