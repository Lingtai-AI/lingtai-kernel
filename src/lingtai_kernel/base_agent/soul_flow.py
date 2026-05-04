"""Soul flow — kernel-side glue for the soul-flow mechanism.

Timer management, fire callback, tc_inbox drain, persistence helpers,
consultation fire orchestration, and appendix rehydration.
"""
from __future__ import annotations

import json
import time


def _start_soul_timer(agent) -> None:
    """Start the soul cadence timer.

    Runs only while the agent is fire-eligible (ACTIVE or IDLE).
    Cancelled by _set_state on entry to STUCK / ASLEEP / SUSPENDED;
    restarted by _set_state on return to ACTIVE / IDLE. Reschedules
    itself in the timer callback (also gated on fire-eligibility).
    """
    import threading

    if agent._shutdown.is_set():
        return
    _cancel_soul_timer(agent)
    agent._soul_timer = threading.Timer(agent._soul_delay, _soul_whisper, args=(agent,))
    agent._soul_timer.daemon = True
    agent._soul_timer.name = f"soul-{agent.agent_name or agent._working_dir.name}"
    agent._soul_timer.start()


def _cancel_soul_timer(agent) -> None:
    """Cancel any pending soul timer."""
    if agent._soul_timer is not None:
        agent._soul_timer.cancel()
        agent._soul_timer = None


def _soul_whisper(agent) -> None:
    """Cadence timer callback. Fires past-self consultation on the
    soul_delay wall clock, then reschedules itself.

    Only fires under ACTIVE or IDLE. The timer is normally cancelled
    on entry to STUCK/ASLEEP/SUSPENDED via _set_state, so the state
    check here is defensive.
    """
    from ..state import AgentState

    agent._soul_timer = None
    try:
        if agent._state in (AgentState.ACTIVE, AgentState.IDLE):
            _run_consultation_fire(agent)
        else:
            agent._log("soul_whisper_skipped", reason=agent._state.value)
    except Exception as e:
        agent._log("soul_whisper_error", error=str(e))
    finally:
        if agent._state in (AgentState.ACTIVE, AgentState.IDLE):
            _start_soul_timer(agent)


def _drain_tc_inbox(agent) -> None:
    """Splice queued involuntary tool-call pairs into the wire chat.

    Called at safe boundaries — top of ``_handle_request``, before the
    next ``send()`` composes its payload.
    """
    if agent._chat is None:
        try:
            agent._session.ensure_session()
        except Exception:
            return
    iface = agent._chat.interface
    if iface.has_pending_tool_calls():
        return
    items = agent._tc_inbox.drain()
    if not items:
        return
    for item in items:
        if getattr(item, "replace_in_history", False):
            prior_id = agent._appendix_ids_by_source.get(item.source)
            if prior_id is not None:
                iface.remove_pair_by_call_id(prior_id)
            agent._appendix_ids_by_source.pop(item.source, None)
        iface.add_assistant_message(content=[item.call])
        iface.add_tool_results([item.result])
        if getattr(item, "replace_in_history", False):
            agent._appendix_ids_by_source[item.source] = item.call.id
    agent._save_chat_history()
    agent._log(
        "tc_inbox_drain",
        count=len(items),
        sources=[i.source for i in items],
    )


def _persist_soul_entry(agent, result: dict, mode: str = "flow", source: str = "agent") -> None:
    """Append a soul entry to the appropriate log file."""
    from datetime import datetime, timezone

    filename = f"soul_{mode}.jsonl"
    soul_file = agent._working_dir / "logs" / filename
    soul_file.parent.mkdir(exist_ok=True)
    entry = json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "source": source,
        "prompt": result["prompt"],
        "thinking": result["thinking"],
        "voice": result["voice"],
    }, ensure_ascii=False)
    with open(soul_file, "a") as f:
        f.write(entry + "\n")


def _append_soul_flow_record(agent, record: dict) -> None:
    """Append one record to logs/soul_flow.jsonl."""
    soul_file = agent._working_dir / "logs" / "soul_flow.jsonl"
    soul_file.parent.mkdir(exist_ok=True)
    with open(soul_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_inquiry(agent, question: str, source: str = "agent") -> None:
    """Run soul.inquiry and log result as insight event."""
    try:
        from ..intrinsics.soul import soul_inquiry
        result = soul_inquiry(agent, question)
        if result:
            agent._log("insight", text=result["voice"], question=question, source=source)
            _persist_soul_entry(agent, result, mode="inquiry", source=source)
        else:
            agent._log("insight", text="(silence)", question=question, source=source)
    except Exception as e:
        agent._log("insight_error", error=str(e)[:200], question=question)


def _flatten_v3_for_pair(agent, voice: dict) -> dict:
    """Bridge v3 consultation blocks to the legacy appendix renderer."""
    from ..llm.interface import TextBlock, ThinkingBlock, ToolCallBlock

    voice_text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_attempt_lines: list[str] = []

    for b in voice.get("blocks", []):
        if isinstance(b, TextBlock):
            if b.text:
                voice_text_parts.append(b.text)
        elif isinstance(b, ThinkingBlock):
            if b.text:
                thinking_parts.append(b.text)
        elif isinstance(b, ToolCallBlock):
            try:
                tool_attempt_lines.append(f"Wanted to: {b.name}({b.args})")
            except Exception:
                tool_attempt_lines.append(f"Wanted to: {getattr(b, 'name', 'tool')}")

    if tool_attempt_lines:
        voice_text_parts.append("\n".join(tool_attempt_lines))

    return {
        "source": voice.get("source", "unknown"),
        "voice": "\n".join(part for part in voice_text_parts if part).strip(),
        "thinking": thinking_parts,
    }


def _run_consultation_fire(agent) -> None:
    """Run one consultation batch and persist the result.

    Side effects: logs/events.jsonl, logs/soul_flow.jsonl,
    logs/token_ledger.jsonl, tc_inbox.
    """
    from datetime import datetime, timezone
    import secrets as _secrets
    from ..message import _make_message, MSG_TC_WAKE

    fire_id = f"fire_{int(time.time())}_{_secrets.token_hex(2)}"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        from ..intrinsics.soul import (
            _render_current_diary,
            _run_consultation_batch,
            build_consultation_pair,
        )
        from ..tc_inbox import InvoluntaryToolCall

        diary = _render_current_diary(agent)
        voices = _run_consultation_batch(agent)

        sources = [v.get("source", "unknown") for v in voices]
        outcome = "ok" if voices else "empty"

        # Fire record
        try:
            _append_soul_flow_record(agent, {
                "kind": "fire",
                "schema_version": 3,
                "ts": ts,
                "fire_id": fire_id,
                "tc_id": fire_id,
                "diary": diary,
                "sources": sources,
                "outcome": outcome,
            })
        except Exception as e:
            agent._log("soul_flow_persist_error", phase="fire",
                      fire_id=fire_id, error=str(e)[:200])

        # Per-voice records.
        for v in voices:
            try:
                src = v.get("source", "unknown")
                blocks_serialized = [
                    b.to_dict() if hasattr(b, "to_dict") else b
                    for b in v.get("blocks", [])
                ]
                _append_soul_flow_record(agent, {
                    "kind": "voice",
                    "schema_version": 3,
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "fire_id": fire_id,
                    "source": src,
                    "blocks": blocks_serialized,
                })
            except Exception as e:
                agent._log(
                    "soul_flow_persist_error",
                    phase="voice",
                    fire_id=fire_id,
                    source=v.get("source", "unknown"),
                    error=str(e)[:200],
                )

        if not voices:
            agent._log("consultation_fire_empty", fire_id=fire_id)
            return

        voices_for_pair = [_flatten_v3_for_pair(agent, v) for v in voices]
        call, result = build_consultation_pair(agent, voices_for_pair, tc_id=fire_id)
        agent._tc_inbox.enqueue(InvoluntaryToolCall(
            call=call,
            result=result,
            source="soul.flow",
            enqueued_at=time.time(),
            coalesce=True,
            replace_in_history=True,
        ))
        voices_inline = [
            {"source": v.get("source", "unknown"), "voice": v.get("voice", "")}
            for v in voices_for_pair
            if v.get("voice")
        ]
        agent._log(
            "consultation_fire",
            fire_id=fire_id,
            count=len(voices),
            sources=sources,
            voices=voices_inline,
        )

        # Wake the run loop
        try:
            wake_msg = _make_message(MSG_TC_WAKE, "system", "")
            agent.inbox.put(wake_msg)
            agent._wake_nap("soul_flow_fired")
        except Exception as e:
            agent._log("tc_wake_post_error",
                      fire_id=fire_id, error=str(e)[:200])
    except Exception as e:
        agent._log("consultation_fire_error",
                  fire_id=fire_id, error=str(e)[:200])
        try:
            _append_soul_flow_record(agent, {
                "kind": "fire",
                "schema_version": 3,
                "ts": ts,
                "fire_id": fire_id,
                "tc_id": fire_id,
                "diary": "",
                "sources": [],
                "outcome": "error",
                "error": str(e)[:500],
            })
        except Exception:
            pass


def _rehydrate_appendix_tracking(agent) -> None:
    """Scan rehydrated chat history for an existing soul.flow synthetic
    pair and re-track its call_id, so the next consultation fire
    knows what to remove. Idempotent.
    """
    if agent._chat is None:
        return
    try:
        iface = agent._chat.interface
    except Exception:
        return
    from ..llm.interface import ToolCallBlock, ToolResultBlock
    entries = iface.entries
    for i in range(len(entries) - 1):
        a = entries[i]
        u = entries[i + 1]
        if a.role != "assistant" or u.role != "user":
            continue
        if len(a.content) != 1 or len(u.content) != 1:
            continue
        cblock = a.content[0]
        rblock = u.content[0]
        if not isinstance(cblock, ToolCallBlock):
            continue
        if not isinstance(rblock, ToolResultBlock):
            continue
        if cblock.name != "soul":
            continue
        if not isinstance(cblock.args, dict):
            continue
        if cblock.args.get("action") != "flow":
            continue
        if cblock.id != rblock.id:
            continue
        agent._appendix_ids_by_source["soul.flow"] = cblock.id
        return
