"""Soul intrinsic — the agent's inner voice.

Three actions:
    flow    — past-self consultation appendix. Every ``_soul_delay`` seconds,
              fires M=1+K parallel LLM calls (1 stepped-back read of the
              current chat as "insights", K random past-snapshot
              consultations sampled from history/snapshots/). Voices are
              written to ``.notification/soul.json`` via
              ``publish_notification``; the kernel's ``_sync_notifications``
              picks up the fingerprint change and surfaces them inside the
              single-slot synthesized ``notification(action="check")`` wire
              pair. Mechanical — agent cannot invoke manually.
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
from lingtai_kernel.config import DEFAULT_SOUL_DELAY_SECONDS
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
    _build_consultation_tool_refusal,
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
from .inquiry import soul_inquiry, _run_inquiry

# Re-export flow (soul cadence, fire, persistence, appendix tracking).
# These functions are the soul intrinsic's kernel-facing hook surface: after the
# tools consolidation the kernel resolves them through the injected intrinsic
# registry (``BaseAgent._intrinsic_hook("soul", ...)``) instead of importing
# them directly, since the kernel cannot import ``tools``.
from .flow import (
    _start_soul_timer,
    _cancel_soul_timer,
    _soul_whisper,
    _persist_soul_entry,
    _append_soul_flow_record,
    _flatten_v3_for_pair,
    _run_consultation_fire,
    _rehydrate_appendix_tracking,
)


def get_description(lang: str = "en") -> str:
    return "Your inner voice. flow is OPT-IN and DISABLED by default: it runs only when the operator sets env LINGTAI_SOUL_FLOW_ENABLED=1 (then refreshes). While disabled, soul(action='flow') returns status='disabled' (not an error — do not retry); inquiry/config/voice/dismiss still work. When enabled, flow fires periodic past-self consultation every soul_delay seconds while IDLE — M=1+K parallel LLM calls (1 stepped-back read of current chat + K past-snapshot voices) arrive as an involuntary soul(action='flow') pair. delay_seconds is only the cadence after opt-in, NOT an off switch. inquiry: ask a deep copy of yourself a question; answer returns in the tool result. config: tune flow knobs at runtime (delay_seconds, consultation_past_count) — does not enable flow. dismiss: clear the current flow notification. See soul-manual skill."


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["inquiry", "flow", "config", "voice", "dismiss"],
                "description": "inquiry: ask yourself a question — the answer returns in the tool result. Requires 'inquiry' parameter. Always available. flow: OPT-IN, DISABLED by default. Only runs when the operator sets env LINGTAI_SOUL_FLOW_ENABLED=1 (case-insensitive true/yes/on) and refreshes/restarts. While DISABLED, invoking flow returns {status:'disabled', enabled:false} with an explanation — this is expected config state, NOT an error, so do NOT retry it; the operator must set the env var first. When ENABLED, flow fires automatically every soul_delay seconds while IDLE — appears in history as a soul(action='flow') call you did not initiate, with voices from past selves and a stepped-back read of your current chat. You may ALSO invoke flow voluntarily while ACTIVE: the call returns immediately with a success acknowledgement, and the actual voices arrive shortly after as a separate involuntary soul(action='flow') pair. If a fire is already running when you invoke, the call is rejected with 'soul flow ongoing, request rejected' — wait for the current fire to land, then try again. config: tune flow knobs — pass any subset of delay_seconds (wall-clock cadence, min 30s), consultation_past_count (K voices per fire, 0–5). At least one field required. Persists to init.json. config does NOT enable flow — delay_seconds is only cadence after the env opt-in, never an off switch. voice: choose how your own soul-flow voice sounds. Bare (no 'set') reads the current voice + the resolved prompt. Pass set='inner' or set='observer' to switch presets. Pass set='custom' with a 'prompt' field to write your own — speak to yourself as the soul, describe how you want to be framed when reading your own diary. Persists to init.json. This is yours; the operator does not choose it for you. dismiss: clear the current soul flow notification from the notification panel. Use when you've read the voices and want to dismiss them before the next fire replaces them. inquiry/config/voice/dismiss all work whether or not flow is enabled. See soul-manual skill for enabling/disabling, troubleshooting, and the privacy/cost rationale.",
            },
            "inquiry": {
                "type": "string",
                "description": "Your self-inquiry — a question to yourself. Required for action='inquiry'. This is you asking yourself a question, not prompting someone else.",
            },
            "delay_seconds": {
                "type": "number",
                "minimum": SOUL_DELAY_MIN_SECONDS,
                "description": "Wall-clock delay between soul flow fires, in seconds. This is ONLY the cadence AFTER soul flow is enabled via env LINGTAI_SOUL_FLOW_ENABLED=1 — it is NOT an off switch. If the env var is unset, soul flow is disabled entirely and NO fires occur regardless of this value (a large delay no longer even half-suppresses flow; the env gate does). Soul flow is your periodic inner reflection — when enabled and the timer fires, past versions of yourself (from molt snapshots) and a stepped-back read of your current work speak to you as voices, surfacing patterns, blind spots, and perspective you might miss while busy. Optional for action='config'. Minimum 30s. Lower for more frequent reflection (e.g. 300 = every 5 minutes; 7200 = every 2 hours). When flow is enabled, the currently-pending fire is cancelled and the timer restarts on the new schedule. See soul-manual skill.",
            },
            "consultation_past_count": {
                "type": "integer",
                "minimum": CONSULTATION_PAST_COUNT_MIN,
                "maximum": CONSULTATION_PAST_COUNT_MAX,
                "description": "K — number of past-self voices sampled per fire. Optional for action='config'. Each fire runs M=1+K parallel LLM calls (1 stepped-back diary reader + K random past-snapshot voices). Range [0, 5]. 0 = insights-only fires (cheapest, no past-self voices). Higher K is costlier per fire and fills more chat-history with voice content; lower K is faster and quieter.",
            },
            "set": {
                "type": "string",
                "description": "Which voice profile to switch to. For action='voice'. Built-ins: 'inner' (terse — 'you are the soul, speak as inner voice') or 'observer' (structured stepped-back hook framing). Or 'custom', which requires a 'prompt' field with your own system-prompt text. Omit 'set' to read the current voice and resolved prompt without changing anything.",
            },
            "prompt": {
                "type": "string",
                "maxLength": SOUL_VOICE_PROMPT_MAX,
                "description": "Custom system prompt for soul-flow voice. Required when set='custom'; ignored otherwise. Length capped at 4000 characters. Speak to yourself as the soul — describe how you want to be framed when reading your own diary. The same prompt is used for both insights (current self) and past (frozen earlier self) consultations; the per-fire cue text differentiates whose diary you're reading.",
            },
        },
        "required": ["action"],
    }


def handle(agent, args: dict) -> dict:
    """Handle soul tool — inquiry, config, voice are agent-invocable.
    Flow is invocable too, but the call returns immediately with a
    synthesized success result; the actual voices arrive shortly by
    writing ``.notification/soul.json``, which the kernel's notification
    sync surfaces inside the synthesized ``notification(action="check")``
    wire pair.
    """
    action = args.get("action", "")

    if action == "flow":
        # Opt-in gate: soul flow is disabled by default. When disabled,
        # return an explicit, stable "disabled" status BEFORE touching the
        # lock or spawning a fire thread — so a disabled agent burns no
        # thread and does not wait for IDLE. This is expected config state,
        # not an error to retry (see soul-manual).
        from .flow import _soul_flow_enabled, SOUL_FLOW_ENABLED_ENV
        if not _soul_flow_enabled():
            agent._log("soul_flow_voluntary_disabled")
            return {
                "status": "disabled",
                "enabled": False,
                "env_var": SOUL_FLOW_ENABLED_ENV,
                "message": (
                    "Soul flow is disabled by default on this agent. It is "
                    "opt-in: set the environment variable "
                    f"{SOUL_FLOW_ENABLED_ENV}=1 (also true/yes/on), then "
                    "refresh/restart, to enable periodic and voluntary "
                    "past-self consultation. delay_seconds is only the "
                    "cadence AFTER this opt-in — it is not an off switch, "
                    "and soul(action='config') does not enable flow. "
                    "inquiry, config, voice, and dismiss remain available "
                    "while flow is disabled. Do not retry flow blindly; the "
                    "operator must set the env var first. See soul-manual "
                    "skill for how to enable/disable, troubleshoot, and the "
                    "privacy/cost rationale."
                ),
            }

        # Voluntary trigger: try-acquire the fire lock non-blocking. If
        # held, another fire is already in flight (timer-fired or a prior
        # voluntary call) — refuse so the agent isn't surprised by a
        # silent no-op. If free, release immediately and kick off the
        # real fire on a daemon thread; _run_consultation_fire will
        # re-acquire under the same gate.
        lock = getattr(agent, "_soul_fire_lock", None)
        if lock is not None:
            if not lock.acquire(blocking=False):
                agent._log("soul_flow_voluntary_rejected", reason="ongoing")
                return {"error": "soul flow ongoing, request rejected"}
            lock.release()

        import threading
        from .flow import _run_consultation_fire

        def _fire():
            try:
                # Wait for IDLE before firing — voluntary flow is triggered
                # while ACTIVE (inside a tool call), but _run_consultation_fire
                # gates on IDLE.  _idle is a threading.Event set on every
                # non-ACTIVE transition (see base_agent._set_state).
                idle_event = getattr(agent, "_idle", None)
                if idle_event is not None:
                    agent._log("soul_flow_voluntary_waiting_idle")
                    # Wait up to soul_delay seconds; if the agent never goes
                    # IDLE (stuck in ACTIVE), give up rather than hang.
                    timeout = getattr(agent, "_soul_delay", DEFAULT_SOUL_DELAY_SECONDS)
                    if not idle_event.wait(timeout=timeout):
                        agent._log("soul_flow_voluntary_timeout",
                                   timeout=timeout)
                        return
                _run_consultation_fire(agent)
            except Exception as e:
                try:
                    agent._log("soul_flow_voluntary_error", error=str(e)[:200])
                except Exception:
                    pass

        t = threading.Thread(target=_fire, daemon=True, name="soul-flow-voluntary")
        t.start()
        agent._log("soul_flow_voluntary_triggered")
        return {
            "status": "ok",
            "message": (
                "Soul flow triggered. Voices will arrive shortly as a "
                "separate soul(action='flow') tool-call pair appended to "
                "your chat history (replacing any prior soul-flow pair)."
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

    if action == "dismiss":
        from lingtai_kernel.notifications import dismiss_channel
        result = dismiss_channel(agent, "soul", invoked_by="soul")
        if result.get("status") == "ok":
            result.setdefault("message", "Soul flow notification dismissed.")
        return result

    return {
        "error": (
            f"Unknown soul action: {action}. Use inquiry, config, voice, dismiss, "
            "or wait for flow (mechanical)."
        )
    }
