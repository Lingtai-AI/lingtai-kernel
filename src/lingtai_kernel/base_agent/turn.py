"""Turn engine — main loop, message dispatch, LLM send, tool-call processing.

The core message lifecycle: receive → route → LLM → process → persist.
"""
from __future__ import annotations

import json
import queue
import time

from ..message import Message, _make_message, MSG_REQUEST, MSG_USER_INPUT, MSG_TC_WAKE
from ..i18n import t as _t
from ..logging import get_logger
from ..loop_guard import LoopGuard
from ..tool_executor import ToolExecutor
from ..meta_block import build_meta, render_meta
from ..time_veil import now_iso

logger = get_logger()


def _run_loop(agent) -> None:
    """Wait for messages, process them. Agent persists between messages."""
    from ..state import AgentState
    from .soul_flow import _cancel_soul_timer

    while True:
        while not agent._shutdown.is_set():
            # --- Asleep: soul off, wait for inbox message ---
            if agent._asleep.is_set():
                _cancel_soul_timer(agent)
                agent._log("sleep")

                # Block until a message arrives or shutdown
                msg = None
                while not agent._shutdown.is_set():
                    try:
                        msg = agent.inbox.get(timeout=1.0)
                        break
                    except queue.Empty:
                        continue

                if msg is None:
                    break  # shutdown was set — exit inner loop

                # Wake up
                agent._asleep.clear()
                agent._cancel_event.clear()  # clear stale sleep/stamina signal
                agent._set_state(AgentState.ACTIVE, reason=f"woke from asleep: {msg.type}")
                agent._log("wake", trigger=msg.type)
                agent._reset_uptime()
                msg = _concat_queued_messages(agent, msg)
                # Fall through to handle the message below
            else:
                try:
                    msg = agent.inbox.get(timeout=agent._inbox_timeout)
                except queue.Empty:
                    continue
                msg = _concat_queued_messages(agent, msg)
                agent._set_state(AgentState.ACTIVE, reason=f"received {msg.type}")

            # --- Process with AED (Automatic Error Detection) ---
            sleep_state = AgentState.IDLE
            aed_attempts = 0
            while True:
                try:
                    _handle_message(agent, msg)
                    break  # success (chat saved after each session.send inside)
                except Exception as e:
                    err_desc = str(e) or repr(e)
                    aed_attempts += 1

                    # Close any dangling tool_calls with synthetic error tool_results
                    if agent._session.chat is not None:
                        agent._session.chat.interface.close_pending_tool_calls(
                            reason=err_desc or "aed_recovery"
                        )

                    agent._set_state(AgentState.STUCK, reason=f"AED attempt {aed_attempts}: {err_desc}")
                    agent._log("aed_attempt", attempt=aed_attempts, error=err_desc)
                    logger.warning(
                        f"[{agent.agent_name}] AED attempt {aed_attempts}/{agent._config.max_aed_attempts}: {err_desc}",
                    )

                    if aed_attempts == agent._config.max_aed_attempts:
                        if not agent._preset_fallback_attempted and agent._can_fallback_preset():
                            agent._preset_fallback_attempted = True
                            agent._log("preset_auto_fallback",
                                      reason=err_desc,
                                      failed_attempts=aed_attempts)
                            try:
                                agent._activate_default_preset()
                            except Exception as e:
                                agent._log("preset_auto_fallback_failed", error=str(e))
                                # fall through to ASLEEP
                            else:
                                agent._perform_refresh()
                                return

                        agent._log("aed_exhausted", attempts=aed_attempts, error=err_desc)
                        sleep_state = AgentState.ASLEEP
                        agent._asleep.set()
                        break

                    # Rebuild session with current config, preserving history
                    if agent._session.chat is not None:
                        agent._session._rebuild_session(agent._session.chat.interface)

                    # Inject recovery message
                    ts = now_iso(agent)
                    aed_msg = _t(agent._config.language, "system.stuck_revive", ts=ts, tool_calls=err_desc)
                    msg = _make_message(MSG_REQUEST, "system", aed_msg)

            if not agent._asleep.is_set():
                agent._set_state(sleep_state)
            agent._save_chat_history()

            # Auto-insight: fire after N turns
            if agent._config.insights_interval > 0:
                agent._insight_turn_counter += 1
                if agent._insight_turn_counter >= agent._config.insights_interval:
                    agent._insight_turn_counter = 0
                    from ..i18n import t as _ti
                    from .soul_flow import _run_inquiry
                    _run_inquiry(
                        agent,
                        _ti(agent._config.language, "insight.auto_question"),
                        source="auto",
                    )

        break


def _concat_queued_messages(agent, msg: Message) -> Message:
    """Drain any additional queued messages and concatenate into one.

    If nothing else is queued, returns the original message unchanged.
    Otherwise, joins all message contents with blank lines and returns
    a new merged message.
    """
    extra: list[Message] = []
    while True:
        try:
            queued = agent.inbox.get_nowait()
        except queue.Empty:
            break
        extra.append(queued)

    if not extra:
        return msg

    all_msgs = [msg] + extra
    parts = [m.content if isinstance(m.content, str) else str(m.content)
             for m in all_msgs]
    merged_content = "\n\n".join(parts)
    merged = _make_message(MSG_REQUEST, msg.sender, merged_content)
    agent._log("messages_concatenated", count=len(all_msgs))
    return merged


def _handle_message(agent, msg: Message) -> None:
    """Route message by type. Subclasses may override for routing."""
    if msg.type in (MSG_REQUEST, MSG_USER_INPUT):
        _handle_request(agent, msg)
    elif msg.type == MSG_TC_WAKE:
        _handle_tc_wake(agent, msg)
    else:
        logger.warning(f"[{agent.agent_name}] Unknown message type: {msg.type}")


def _handle_request(agent, msg: Message) -> None:
    """Send request to LLM, process response with tool calls."""
    from ..llm import LLMResponse

    # Splice any queued involuntary tool-call pairs
    agent._drain_tc_inbox()

    max_calls, dup_free, dup_hard = _get_guard_limits(agent)
    guard = LoopGuard(
        max_total_calls=max_calls,
        dup_free_passes=dup_free,
        dup_hard_block=dup_hard,
    )
    agent._executor = ToolExecutor(
        dispatch_fn=agent._dispatch_tool,
        make_tool_result_fn=lambda name, result, **kw: agent.service.make_tool_result(
            name, result, provider=agent._config.provider, **kw
        ),
        guard=guard,
        known_tools=set(agent._intrinsics) | set(agent._tool_handlers),
        parallel_safe_tools=agent._PARALLEL_SAFE_TOOLS,
        logger_fn=agent._log,
        meta_fn=lambda: build_meta(agent),
    )
    content = agent._pre_request(msg)
    meta = build_meta(agent)

    # Molt pressure — warn agent when context is getting full
    has_molt = "psyche" in agent._intrinsics
    pressure = agent._session.get_context_pressure()

    # Hard ceiling — unconditional force-wipe
    if pressure >= agent._config.molt_hard_ceiling and has_molt:
        lang = agent._config.language
        agent._log("auto_forget", reason="hard ceiling", pressure=pressure, ceiling=agent._config.molt_hard_ceiling)
        from ..intrinsics import psyche as _psyche
        _psyche.context_forget(agent)
        agent._session._compaction_warnings = 0
        content = f"{_t(lang, 'system.molt_wiped')}\n\n{content}"
    elif pressure >= agent._config.molt_pressure and has_molt:
        max_warnings = agent._config.molt_warnings
        agent._session._compaction_warnings += 1
        warnings = agent._session._compaction_warnings
        remaining = max(0, max_warnings - warnings)
        lang = agent._config.language
        if warnings > max_warnings:
            agent._log("auto_forget", reason=f"ignored {max_warnings} molt warnings", pressure=pressure)
            from ..intrinsics import psyche as _psyche
            _psyche.context_forget(agent)
            agent._session._compaction_warnings = 0
            content = f"{_t(lang, 'system.molt_wiped')}\n\n{content}"
        else:
            level = min(warnings, 3)
            level_prompt = _t(
                lang,
                f"system.molt_warning_level{level}",
                pressure=f"{pressure:.0%}",
                remaining=remaining,
            )
            if level >= 2:
                level_prompt = level_prompt + "\n\n" + _t(lang, "system.molt_procedure")
            molt_prompt = agent._config.molt_prompt or level_prompt
            status = f"[context: {pressure:.0%} | {remaining}/{max_warnings}]"
            content = f"{molt_prompt}\n{status}\n\n{content}"

    prefix = render_meta(agent, meta)
    if prefix:
        content = f"{prefix}\n\n{content}"
    agent._log("text_input", text=content)
    response = agent._session.send(content)
    agent._last_usage = response.usage
    agent._save_chat_history()
    result = _process_response(agent, response)
    agent._post_request(msg, result)


def _handle_tc_wake(agent, msg: Message) -> None:
    """Process queued involuntary tool-call pairs by driving them
    through the LLM as if the tools just returned — no fake user prompt.
    """
    from ..llm import LLMResponse

    items = agent._tc_inbox.drain()
    if not items:
        agent._log("tc_wake_noop", reason="tc_inbox_empty")
        return
    if agent._chat is None:
        try:
            agent._session.ensure_session()
        except Exception as e:
            for item in items:
                agent._tc_inbox.enqueue(item)
            agent._log(
                "tc_wake_noop",
                reason="ensure_session_failed",
                error=str(e)[:300],
            )
            return
    iface = agent._chat.interface
    if iface.has_pending_tool_calls():
        for item in items:
            agent._tc_inbox.enqueue(item)
        agent._log("tc_wake_noop", reason="pending_tool_calls")
        return

    try:
        agent._executor = ToolExecutor(
            dispatch_fn=agent._dispatch_tool,
            make_tool_result_fn=lambda name, result, **kw: agent.service.make_tool_result(
                name, result, provider=agent._config.provider, **kw
            ),
            guard=LoopGuard(
                max_total_calls=agent._config.max_turns,
                dup_free_passes=2,
                dup_hard_block=8,
            ),
            known_tools=set(agent._intrinsics) | set(agent._tool_handlers),
            parallel_safe_tools=agent._PARALLEL_SAFE_TOOLS,
            logger_fn=agent._log,
            meta_fn=lambda: build_meta(agent),
        )
        for idx, item in enumerate(items):
            try:
                if getattr(item, "replace_in_history", False):
                    prior_id = agent._appendix_ids_by_source.get(item.source)
                    if prior_id is not None:
                        iface.remove_pair_by_call_id(prior_id)
                    agent._appendix_ids_by_source.pop(item.source, None)
                iface.add_assistant_message(content=[item.call])
                if getattr(item, "replace_in_history", False):
                    agent._appendix_ids_by_source[item.source] = item.call.id
                agent._save_chat_history()

                agent._log("tc_wake_dispatch", source=item.source, call_id=item.call.id)
                response = agent._session.send([item.result])
                agent._last_usage = response.usage
                agent._save_chat_history(ledger_source="tc_wake")
                _process_response(agent, response, ledger_source="tc_wake")
            except Exception as splice_err:
                if iface.has_pending_tool_calls():
                    iface.close_pending_tool_calls(
                        reason=f"tc_wake splice failed: {str(splice_err)[:200]}",
                    )
                    agent._save_chat_history()
                agent._log(
                    "tc_wake_send_error",
                    source=item.source,
                    call_id=item.call.id,
                    error=str(splice_err)[:300],
                )
                for remaining in items[idx + 1:]:
                    agent._tc_inbox.enqueue(remaining)
                raise
    except Exception as e:
        if agent._chat is not None and agent._chat.interface.has_pending_tool_calls():
            agent._chat.interface.close_pending_tool_calls(
                reason=f"tc_wake outer-error heal: {str(e)[:200]}",
            )
            agent._save_chat_history()
        agent._log("tc_wake_error", error=str(e)[:300])
        raise


def _get_guard_limits(agent) -> tuple[int, int, int]:
    """Return (max_total_calls, dup_free_passes, dup_hard_block).

    Uses config.max_turns as the basis.
    """
    max_turns = agent._config.max_turns
    return (max_turns, 2, 8)


def _process_response(agent, response, *, ledger_source: str = "main") -> dict:
    """Handle tool calls and collect text output.

    Returns a result dict: {"text": ..., "failed": ..., "errors": [...]}.

    ``ledger_source`` propagates to ``_save_chat_history`` for any
    tool-loop continuation LLM round-trips.
    """
    agent._cancel_event.clear()

    guard = agent._executor.guard
    collected_text_parts: list[str] = []
    collected_errors: list[str] = []

    while True:
        if response.text:
            collected_text_parts.append(response.text)
            agent._log("diary", text=response.text)
            if response.tool_calls:
                agent._intermediate_text_streamed = False

        if response.thoughts:
            for thought in response.thoughts:
                agent._log("thinking", text=thought)

        if not response.tool_calls:
            break

        if agent._cancel_event.is_set():
            agent._cancel_event.clear()
            return {"text": "", "failed": False, "errors": []}

        stop_reason = guard.check_limit(len(response.tool_calls))
        if stop_reason:
            break

        invalid_reason = guard.check_invalid_tool_limit()
        if invalid_reason:
            break

        # Delegate to ToolExecutor
        tool_results, intercepted, intercept_text = agent._executor.execute(
            response.tool_calls,
            on_result_hook=agent._on_tool_result_hook,
            cancel_event=agent._cancel_event,
            collected_errors=collected_errors,
        )

        if intercepted:
            if tool_results and agent._chat:
                agent._chat.commit_tool_results(tool_results)
            return {
                "text": intercept_text,
                "failed": False,
                "errors": [],
            }

        guard.record_calls(len(response.tool_calls))

        # Break on repeated identical errors
        if (
            len(collected_errors) >= 2
            and collected_errors[-1] == collected_errors[-2]
        ):
            logger.warning(
                "[%s] Same error repeated, breaking early: %s",
                agent.agent_name,
                collected_errors[-1],
            )
            break

        response = agent._session.send(tool_results)
        agent._last_usage = response.usage
        agent._save_chat_history(ledger_source=ledger_source)

    final_text = "\n".join(collected_text_parts)
    has_errors = bool(collected_errors)
    no_useful_output = not final_text.strip()
    return {
        "text": final_text,
        "failed": has_errors and no_useful_output,
        "errors": collected_errors,
    }
