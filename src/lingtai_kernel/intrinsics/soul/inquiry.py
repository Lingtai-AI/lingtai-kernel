"""Soul inquiry — synchronous mirror session + inquiry runner.

Clones the agent's conversation (text + thinking only, no tool calls/results),
sends a one-shot question, returns the answer. One-shot per invocation.
"""
from __future__ import annotations


def soul_inquiry(agent, question: str) -> dict | None:
    """Inquiry mode — one-shot mirror session with cloned conversation.

    Clones the agent's conversation (thinking + diary only, no tool
    calls/results), sends the question. Fresh session each time.
    """
    from ...llm.interface import ChatInterface, TextBlock, ThinkingBlock
    from .config import _build_soul_system_prompt
    from .consultation import _send_with_timeout, _write_soul_tokens

    cloned = ChatInterface()

    if agent._chat is not None:
        for entry in agent._chat.interface.entries:
            if entry.role == "system":
                continue
            stripped: list = []
            for block in entry.content:
                if isinstance(block, (TextBlock, ThinkingBlock)):
                    stripped.append(block)
            if stripped:
                if entry.role == "assistant":
                    cloned.add_assistant_message(stripped)
                else:
                    cloned.add_user_blocks(stripped)

    system_prompt = _build_soul_system_prompt(agent)
    system_prompt += "\n\nYou have no tools. Respond with plain text only. Never output tool calls or XML tags."

    try:
        session = agent.service.create_session(
            system_prompt=system_prompt,
            tools=None,
            model=agent._config.model or agent.service.model,
            thinking="high",
            tracked=False,
            interface=cloned,
        )
    except Exception as e:
        agent._log("soul_whisper_error", error=str(e)[:200])
        return None

    response = _send_with_timeout(agent, session, question)
    if not response or not response.text:
        return None

    _write_soul_tokens(agent, response)

    return {
        "prompt": question,
        "voice": response.text,
        "thinking": response.thoughts or [],
    }


def _run_inquiry(agent, question: str, source: str = "agent") -> None:
    """Run soul.inquiry and log result as insight event."""
    from .flow import _persist_soul_entry

    try:
        result = soul_inquiry(agent, question)
        if result:
            agent._log("insight", text=result["voice"], question=question, source=source)
            _persist_soul_entry(agent, result, mode="inquiry", source=source)
        else:
            agent._log("insight", text="(silence)", question=question, source=source)
    except Exception as e:
        agent._log("insight_error", error=str(e)[:200], question=question)
