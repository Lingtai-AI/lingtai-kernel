"""Subconscious engine (Architecture B) — 30s timer with inline conversation injection.

The subconscious fires ONLY while the agent is in a tool-call loop
(ACTIVE mid-turn). It runs a cheap model against the current context
plus the last 3 tool call results, producing a single insight per fire.
Insights are stored in-memory and injected as system messages into the
conversation between tool calls.

Lifecycle:
  _start_subconscious_timer  — called from turn._handle_request (turn start)
  _cancel_subconscious_timer — called from turn._handle_request (turn end)
  _clear_subconscious_insights — called from turn._handle_request (turn start)

The timer is a 30-second wall-clock daemon; it fires _subconscious_tick
which runs one LLM call against the current context.
"""
from __future__ import annotations

import json
import re
import threading
import time

_SUBCONSCIOUS_FIRE_INTERVAL = 30.0
_SUBCONSCIOUS_MAX_TOOL_RESULTS = 3


# ── Timer management ──────────────────────────────────────────────────────

def _start_subconscious_timer(agent) -> None:
    """Start the subconscious cadence timer (30s wall-clock).

    Runs only while the agent is in an active tool-call loop (ACTIVE state
    mid-turn). Cancelled by _cancel_subconscious_timer at turn end.
    """
    if not getattr(agent._config, "subconscious_enabled", False):
        return
    if agent._shutdown.is_set():
        return
    _cancel_subconscious_timer(agent)
    agent._subconscious_timer = threading.Timer(
        _SUBCONSCIOUS_FIRE_INTERVAL,
        _subconscious_tick,
        args=(agent,),
    )
    agent._subconscious_timer.daemon = True
    agent._subconscious_timer.name = (
        f"subconscious-{agent.agent_name or agent._working_dir.name}"
    )
    agent._subconscious_timer.start()


def _cancel_subconscious_timer(agent) -> None:
    """Cancel any pending subconscious timer."""
    timer = getattr(agent, "_subconscious_timer", None)
    if timer is not None:
        timer.cancel()
        agent._subconscious_timer = None


# ── Fire orchestration ────────────────────────────────────────────────────

def _subconscious_tick(agent) -> None:
    """Timer callback — fire the subconscious if still mid-turn.

    Only fires while agent state is ACTIVE (mid-tool-call-loop).
    Reschedules in the finally block if still in an active turn.
    """
    from ...state import AgentState

    agent._subconscious_timer = None
    try:
        if agent._state == AgentState.ACTIVE:
            _run_subconscious_fire(agent)
        else:
            agent._log("subconscious_tick_skipped", state=agent._state.value)
    except Exception as e:
        agent._log("subconscious_tick_error", error=str(e)[:200])
    finally:
        if agent._state == AgentState.ACTIVE:
            _start_subconscious_timer(agent)


def _run_subconscious_fire(agent) -> None:
    """Fire one subconscious — single model call with current context.

    Uses _subconscious_fire_lock (try-acquire non-blocking) to prevent
    overlapping fires. Reads the current chat context plus the last 3
    tool call results. Stores the insight in-memory on the agent.
    """
    from ...state import AgentState
    from .consultation import (
        _SUBCONSCIOUS_SYSTEM_PROMPT,
        _run_consultation_voice,
        _render_current_diary,
    )

    # Fire lock — skip if a previous fire is still running.
    lock = getattr(agent, "_subconscious_fire_lock", None)
    if lock is not None and not lock.acquire(blocking=False):
        agent._log("subconscious_skipped_inflight")
        return

    fire_id = f"sub_{int(time.time())}_{_secrets_hex(4)}"

    try:
        # Pre-check.
        if agent._state != AgentState.ACTIVE:
            agent._log("subconscious_discarded_state",
                       fire_id=fire_id, state=agent._state.value)
            return

        # Build spark from current diary.
        spark = _render_current_diary(agent)
        if not spark:
            agent._log("subconscious_fire_empty", fire_id=fire_id)
            return

        # Build context from current chat + last N tool call results.
        iface = _build_current_context(agent)
        if iface is None or not iface.entries:
            agent._log("subconscious_fire_no_context", fire_id=fire_id)
            return

        agent._log("subconscious_fire_start", fire_id=fire_id)

        # Session overrides from subconscious config.
        session_overrides = _build_session_overrides(agent)

        # Single model call.
        result = _run_consultation_voice(
            agent, iface, "subconscious",
            system_prompt=_SUBCONSCIOUS_SYSTEM_PROMPT,
            spark=spark,
            session_overrides=session_overrides,
            allow_tool_recommendations=False,
            max_rounds=1,
        )

        if result is None:
            agent._log("subconscious_fire_no_result", fire_id=fire_id)
            return

        # Extract text from blocks.
        text = _extract_text_from_blocks(result.get("blocks", []))
        if not text:
            agent._log("subconscious_fire_no_text", fire_id=fire_id)
            return

        # Parse structured JSON response.
        insight_data = _parse_subconscious_response(text)
        if insight_data is None:
            agent._log("subconscious_fire_null_insight", fire_id=fire_id)
            return  # Model said nothing relevant.

        # Store insight in-memory.
        record = {
            "ts": time.time(),
            "fire_id": fire_id,
            "insight": insight_data["insight"],
            "confidence": insight_data.get("confidence", 0.5),
            "source_memory": insight_data.get("source_memory", "unstructured"),
            "model_used": session_overrides.get("model", "unknown"),
        }
        _store_subconscious_insight(agent, record)

        agent._log("subconscious_fire_done",
                   fire_id=fire_id, insight=insight_data["insight"][:100])

    except Exception as e:
        agent._log("subconscious_fire_error",
                   fire_id=fire_id, error=str(e)[:200])
    finally:
        if lock is not None:
            try:
                lock.release()
            except RuntimeError:
                pass


def _build_current_context(agent):
    """Build a ChatInterface from the current context plus last N tool results.

    Clones the current chat interface (if available) and returns it for
    use as the subconscious consultation substrate. The last 3 tool call
    results are naturally included in the cloned interface since they are
    part of the current conversation.
    """
    from ...llm.interface import ChatInterface

    if getattr(agent, "_chat", None) is None:
        return None

    try:
        iface = agent._chat.interface
        # Clone the interface so the consultation doesn't mutate the live chat.
        cloned = ChatInterface.from_dict(iface.to_dict())
        if not cloned.entries:
            return None
        return cloned
    except Exception as e:
        try:
            agent._log("subconscious_context_build_error", error=str(e)[:200])
        except Exception:
            pass
        return None


# ── In-memory insight storage ─────────────────────────────────────────────

def _store_subconscious_insight(agent, record: dict) -> None:
    """Store a subconscious insight in-memory on the agent.

    Thread-safe: appends to a list with a lock.
    """
    insights = getattr(agent, "_subconscious_insights", None)
    if insights is None:
        agent._subconscious_insights = []
        insights = agent._subconscious_insights

    lock = getattr(agent, "_subconscious_insights_lock", None)
    if lock is None:
        import threading
        lock = threading.Lock()
        agent._subconscious_insights_lock = lock

    with lock:
        insights.append(record)


def _get_subconscious_insights(agent) -> list[dict]:
    """Get all stored subconscious insights (thread-safe read)."""
    lock = getattr(agent, "_subconscious_insights_lock", None)
    insights = getattr(agent, "_subconscious_insights", [])

    if lock is not None:
        with lock:
            return list(insights)
    return list(insights)


def _clear_subconscious_insights(agent) -> None:
    """Clear all stored subconscious insights (called at turn start and end).

    Insights are ephemeral — they exist only for the current tool-call loop.
    """
    lock = getattr(agent, "_subconscious_insights_lock", None)
    if lock is not None:
        with lock:
            agent._subconscious_insights = []
    else:
        agent._subconscious_insights = []


def _inject_subconscious_inline(agent) -> None:
    """Inject the latest subconscious insight as a system message.

    Called at the start of the next tool-call iteration (before LLM send).
    The insight appears as a system message in the conversation, visible
    to the agent as if the system is reminding it.

    Uses the notification system to inject the insight. If no insights
    are available, this is a no-op.
    """
    insights = _get_subconscious_insights(agent)
    if not insights:
        return

    # Take the most recent insight.
    latest = insights[-1]
    insight_text = latest.get("insight", "")
    confidence = latest.get("confidence", 0.5)
    source = latest.get("source_memory", "unstructured")

    if not insight_text:
        return

    # Format the insight as a system reminder.
    formatted = (
        f"[Subconscious insight — confidence={confidence:.1f}]\n"
        f"{insight_text}"
    )
    if source and source != "unstructured":
        formatted += f"\n(Source: {source})"

    # Inject as a notification so the agent sees it on the next turn.
    from ..system import publish_notification
    publish_notification(
        agent._working_dir, "subconscious",
        header="subconscious insight",
        icon="🧠",
        instructions=(
            "This is a subconscious insight — a pattern or connection "
            "your background processing noticed. Treat it as a gentle "
            "reminder, not a command. It may point to something worth "
            "checking, a connection you missed, or a pattern from your "
            "past experience. Use your judgment on whether to act on it."
        ),
        data={
            "fire_id": latest.get("fire_id", "unknown"),
            "insight": insight_text,
            "confidence": confidence,
            "source_memory": source,
        },
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _build_session_overrides(agent) -> dict:
    """Build session_overrides dict from agent config."""
    overrides: dict = {}
    provider = getattr(agent._config, "subconscious_provider", None)
    model = getattr(agent._config, "subconscious_model", None)
    base_url = getattr(agent._config, "subconscious_base_url", None)
    ctx_window = getattr(agent._config, "subconscious_context_window", None)
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["model"] = model
    if base_url:
        overrides["base_url"] = base_url
    if ctx_window:
        overrides["context_window"] = ctx_window
    return overrides


def _extract_text_from_blocks(blocks: list) -> str:
    """Extract text content from a list of content blocks."""
    from ...llm.interface import TextBlock, ThinkingBlock

    parts: list[str] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            if b.text:
                parts.append(b.text)
        elif isinstance(b, ThinkingBlock):
            pass  # Skip thinking blocks.
        else:
            # Generic fallback for dict-like blocks.
            text = getattr(b, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _parse_subconscious_response(text: str) -> dict | None:
    """Parse the subconscious LLM response into structured insight data.

    Returns {"insight": str, "confidence": float, "source_memory": str}
    or None if insight is null/empty. Handles markdown-wrapped JSON.
    """
    # Strip markdown code fences.
    cleaned = re.sub(r'^```(?:json)?\s*\n?|\n?```\s*$', '', text.strip())

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Unstructured text — treat as an insight with default confidence.
        if text.strip():
            return {
                "insight": text.strip(),
                "confidence": 0.5,
                "source_memory": "unstructured",
            }
        return None

    if not isinstance(data, dict):
        return None

    insight = data.get("insight")
    if insight is None or (isinstance(insight, str) and not insight.strip()):
        return None

    confidence = data.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))

    source_memory = data.get("source_memory", "")
    if not isinstance(source_memory, str):
        source_memory = str(source_memory)

    return {
        "insight": str(insight).strip(),
        "confidence": confidence,
        "source_memory": source_memory,
    }


def _secrets_hex(n: int) -> str:
    """Generate n random hex characters."""
    import secrets
    return secrets.token_hex(n // 2 + 1)[:n]
