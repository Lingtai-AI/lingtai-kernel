"""Preemptive parallel steering lane.

When a high-priority (human channel) message arrives while the agent is
mid-tool-call, the kernel dispatches a lightweight STEERING LANE instead of
only deferring the notification: a bounded, isolated LLM sub-turn seeded with
the recent conversation tail + the new message + a steering prompt. The lane
replies on the original channel and may request an interrupt by writing
``<lane run_dir>/steering_interrupt.json`` ``{reason, by}``; the main
tool-call await loop checks for that file at a safe boundary and aborts the
current tool call when present.

Design doc: stations/control-total/work/steering_design.md (control-total).
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ._fsutil import atomic_write_json

# Steering lane budget (design: max_turns ~10, timeout ~300s).
STEERING_MAX_TURNS_DEFAULT = 10
STEERING_TIMEOUT_S_DEFAULT = 300.0
STEERING_TAIL_MESSAGES_DEFAULT = 8

# Channels treated as "human" by default: any MCP-hosted chat channel plus the
# legacy email surface. Configurable per-agent via ``steering.priority_channels``.
DEFAULT_PRIORITY_CHANNELS = (
    "mcp.telegram",
    "mcp.whatsapp",
    "mcp.wechat",
    "mcp.feishu",
    "email",
)

# Interrupt contract file name inside a steering lane run dir.
STEERING_INTERRUPT_FILENAME = "steering_interrupt.json"

STEERING_PROMPT = (
    "You are the agent's steering voice. A human sent a high-priority message "
    "while the agent is busy running a tool call. Answer the human now, in the "
    "first person as the agent, concisely and helpfully, using only the recent "
    "conversation tail and the new message below. Do not start new long-running "
    "work. If the new message requires stopping or redirecting the agent's "
    "current work (e.g. an explicit stop/cancel, a security or safety concern, "
    "or a hard direction change), end your reply with a line containing exactly "
    "`[INTERRUPT] <reason>`; otherwise end with `[CONTINUE]`. The kernel will "
    "deliver your text to the original channel."
)


# ---------------------------------------------------------------------------
# Channel / payload helpers
# ---------------------------------------------------------------------------


def is_priority_human_channel(channel: str, config: Any = None) -> bool:
    """Return True when *channel* is a steering-eligible human channel."""
    if not channel:
        return False
    priority = tuple(getattr(config, "steering_priority_channels", None) or ())
    if not priority:
        priority = DEFAULT_PRIORITY_CHANNELS
    return channel in priority


def extract_message_text(payload: Any) -> str:
    """Best-effort extraction of human-readable text from a notification payload."""
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "message", "body", "content", "caption"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("text", "message", "body", "content", "caption"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = data.get("message")
        if isinstance(nested, dict):
            for key in ("text", "message", "body", "content"):
                value = nested.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    # Fall back to a compact serialization of the payload.
    try:
        dump = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        dump = str(payload)
    return dump[:2000]


def _recent_conversation_tail(agent: Any, limit: int) -> str:
    """Read the last ``limit`` messages of the agent's chat history as text.

    Uses the persisted ``history/chat_history.jsonl`` (defensive; never
    touches the live session wire). Returns "" when unavailable.
    """
    try:
        history_path = Path(agent.working_dir) / "history" / "chat_history.jsonl"
    except Exception:
        return ""
    if not history_path.is_file():
        return ""
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return ""
    out: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        role = entry.get("role") or entry.get("type") or ""
        content = entry.get("content") or entry.get("text") or ""
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(
                        str(block.get("text") or block.get("content") or "")
                    )
            content = "\n".join(p for p in parts if p)
        if isinstance(content, str) and content.strip():
            out.append(f"{role}: {content[:500]}")
    return "\n".join(out[-limit:])


# ---------------------------------------------------------------------------
# Steering lane dispatch
# ---------------------------------------------------------------------------


def _lane_run_dir(agent: Any) -> Path:
    """Directory that owns steering lane run dirs (mirrors daemons/ layout)."""
    return Path(agent.working_dir) / "daemons"


def dispatch_steering_lane(
    agent: Any,
    notifications: dict[str, Any],
    *,
    runner: Callable | None = None,
) -> dict | None:
    """Dispatch one steering lane for the first priority human notification.

    Returns a result dict (``{"status": "dispatched", "run_id": ...,
    "channel": ...}``) when a lane was spawned, or ``None`` when there is
    nothing to steer (no eligible channel / steering disabled / lane spawn
    failed). ``runner`` is an injectable lane body for tests; the default
    executes the bounded LLM sub-turn (see ``_default_lane_runner``).
    """
    if not getattr(agent, "_config", None):
        return None
    if not getattr(agent._config, "steering_enabled", True):
        return None
    priority = tuple(getattr(agent._config, "steering_priority_channels", None) or ())
    if not priority:
        priority = DEFAULT_PRIORITY_CHANNELS
    # Only steering-eligible (priority human) channels are considered, in the
    # configured priority order so e.g. mcp.telegram wins over email.
    ordered = [ch for ch in priority if ch in notifications]
    ordered += [
        ch for ch in sorted(notifications.keys())
        if ch not in priority and is_priority_human_channel(ch, agent._config)
    ]
    for channel in ordered:
        payload = notifications[channel]
        run_id = f"steer-{secrets.token_hex(4)}"
        run_dir = _lane_run_dir(agent) / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except OSError:
            return None
        seed = {
            "channel": channel,
            "message": extract_message_text(payload),
            "tail": _recent_conversation_tail(
                agent, getattr(agent._config, "steering_tail_messages", STEERING_TAIL_MESSAGES_DEFAULT)
            ),
            "dispatched_at": time.time(),
            "max_turns": getattr(
                agent._config, "steering_max_turns", STEERING_MAX_TURNS_DEFAULT
            ),
            "timeout_s": getattr(
                agent._config, "steering_timeout_s", STEERING_TIMEOUT_S_DEFAULT
            ),
        }
        try:
            atomic_write_json(run_dir / "steering_seed.json", seed)
            (run_dir / "steering_prompt.txt").write_text(
                _build_lane_prompt(seed), encoding="utf-8"
            )
        except OSError:
            return None
        lane_runner = runner or _default_lane_runner
        thread = threading.Thread(
            target=_run_lane_safely,
            args=(agent, run_dir, seed, lane_runner),
            name=f"steering-{run_id}",
            daemon=True,
        )
        thread.start()
        try:
            agent._log("steering_lane_dispatched", run_id=run_id, channel=channel)
        except Exception:
            pass
        return {"status": "dispatched", "run_id": run_id, "channel": channel}
    return None


def _build_lane_prompt(seed: dict) -> str:
    tail = seed.get("tail") or "(no recent conversation available)"
    message = seed.get("message") or "(no message text available)"
    return (
        f"{STEERING_PROMPT}\n\n"
        f"Recent conversation tail:\n{tail}\n\n"
        f"New high-priority message (channel {seed.get('channel')}):\n{message}\n"
    )


def _run_lane_safely(agent: Any, run_dir: Path, seed: dict, runner: Callable) -> None:
    """Run the lane body with a bounded timeout; never raise into the agent."""
    try:
        runner(agent, run_dir, seed)
    except Exception as exc:
        try:
            agent._log("steering_lane_error", run_id=run_dir.name, error=str(exc)[:300])
        except Exception:
            pass
        try:
            atomic_write_json(
                run_dir / "steering_result.json",
                {"status": "error", "error": str(exc)[:500]},
            )
        except OSError:
            pass


def _resolve_llm_service_class():
    """Lazily resolve the LLMService class (kept out of import time to avoid
    kernel/tools import cycles; overridable seam for tests)."""
    from lingtai.llm.service import LLMService

    return LLMService


def _default_lane_runner(agent: Any, run_dir: Path, seed: dict) -> None:
    """Bounded LLM sub-turn: answer + optional interrupt decision.

    Builds an isolated session off the agent's LLM service (same provider
    config, no shared wire), sends the seeded steering prompt, writes the
    reply into the run dir, delivers it on the original channel, and requests
    an interrupt when the model's reply carries the ``[INTERRUPT]`` marker.
    """
    LLMService = _resolve_llm_service_class()

    service = getattr(agent, "service", None)
    if service is None:
        raise RuntimeError("agent has no LLM service; steering lane cannot run")
    provider = getattr(service, "provider", None)
    model = getattr(service, "model", None)
    api_key = getattr(service, "api_key", None)
    base_url = getattr(service, "base_url", None)
    llm = LLMService(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        context_window=getattr(service, "context_window", None),
    )
    session = llm.create_session(
        system_prompt=STEERING_PROMPT,
        tools=None,
        model=model,
        thinking="default",
        tracked=False,
    )
    prompt = (run_dir / "steering_prompt.txt").read_text(encoding="utf-8")
    deadline = time.monotonic() + float(seed.get("timeout_s") or STEERING_TIMEOUT_S_DEFAULT)
    response = session.send(prompt, timeout=max(1.0, deadline - time.monotonic()))
    reply = getattr(response, "text", None) or ""
    atomic_write_json(
        run_dir / "steering_result.json",
        {"status": "done", "reply": reply, "finished_at": time.time()},
    )
    interrupt_reason = _interrupt_reason_from_reply(reply)
    if interrupt_reason:
        atomic_write_json(
            run_dir / STEERING_INTERRUPT_FILENAME,
            {"reason": interrupt_reason, "by": "steering_lane"},
        )
    deliver_steering_reply(agent, seed.get("channel"), seed.get("message"), reply)


def _interrupt_reason_from_reply(reply: str) -> str | None:
    """Extract the ``[INTERRUPT] <reason>`` decision from the lane reply."""
    if not reply:
        return None
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.startswith("[INTERRUPT]"):
            reason = stripped[len("[INTERRUPT]"):].strip()
            return reason or "steering requested interruption"
    return None


def deliver_steering_reply(agent: Any, channel: str, message: str, reply: str) -> None:
    """Deliver the lane's reply on the original channel (best effort).

    Order of attempts:
    1. ``agent._steering_reply_hook`` if set (test seam / station bridge).
    2. The agent's telegram MCP client ``send`` action when channel is
       ``mcp.telegram`` and a client is registered.
    3. Durable ``steering_reply.txt`` in the lane run dir + event log.
    """
    if not reply:
        return
    hook = getattr(agent, "_steering_reply_hook", None)
    if callable(hook):
        try:
            hook(channel, message, reply)
            return
        except Exception:
            pass
    if channel == "mcp.telegram":
        client = None
        try:
            client = getattr(agent, "_mcp_clients_by_tool", {}).get("telegram")
        except Exception:
            client = None
        if client is not None and hasattr(client, "call_tool"):
            try:
                client.call_tool(
                    "telegram",
                    {"action": "send", "chat_id": None, "text": reply},
                )
                return
            except Exception:
                pass
    try:
        atomic_write_json(
            Path(agent.working_dir) / "daemons" / "steering_reply.txt",
            {"channel": channel, "reply": reply, "ts": time.time()},
        )
        agent._log("steering_reply_stored", channel=channel, chars=len(reply))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Interrupt contract (checked by the main tool-call await loop)
# ---------------------------------------------------------------------------


def iter_steering_interrupts(agent: Any):
    """Yield ``(run_dir, interrupt_dict)`` for every unconsumed interrupt file."""
    root = _lane_run_dir(agent)
    if not root.is_dir():
        return
    for run_dir in sorted(root.glob("steer-*")):
        target = run_dir / STEERING_INTERRUPT_FILENAME
        if not target.is_file():
            continue
        try:
            interrupt = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(interrupt, dict):
            yield run_dir, interrupt


def check_steering_interrupt(agent: Any) -> dict | None:
    """Return the first pending steering interrupt and consume it.

    Called by the main tool-call await loop at a safe boundary. Consuming
    renames the file to ``<name>.consumed`` so a single interrupt fires once.
    """
    for run_dir, interrupt in iter_steering_interrupts(agent):
        target = run_dir / STEERING_INTERRUPT_FILENAME
        try:
            target.rename(target.with_name(target.name + ".consumed"))
        except OSError:
            pass
        interrupt.setdefault("run_id", run_dir.name)
        return interrupt
    return None


def abort_current_tool_call(agent: Any, interrupt: dict) -> None:
    """Abort the current tool call at the safe boundary.

    Marks the turn interrupted, cancels the executor, kills any tracked tool
    child process tree (Windows: ``taskkill /T /F``), and enqueues the
    steering message for the next turn.
    """
    try:
        agent._cancel_event.set()
    except Exception:
        pass
    _kill_tracked_tool_process_tree(agent)
    try:
        agent._log(
            "turn_interrupted_by_steering",
            run_id=interrupt.get("run_id"),
            reason=interrupt.get("reason"),
        )
        agent._active_turn_kind = "interrupted"
    except Exception:
        pass
    _enqueue_steering_message(agent, interrupt)


def _kill_tracked_tool_process_tree(agent: Any) -> None:
    """Kill the current tool's child process tree if the executor tracks one."""
    pid = None
    try:
        pid = getattr(agent, "_steering_current_tool_pid", None)
    except Exception:
        pid = None
    if not pid:
        return
    if os.name == "nt":
        _taskkill_tree_windows(pid)
    else:
        try:
            os.killpg(pid, 9)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def _taskkill_tree_windows(pid: int) -> None:
    import subprocess

    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def _enqueue_steering_message(agent: Any, interrupt: dict) -> None:
    """Surface the steering decision to the next agent turn."""
    from .message import MSG_REQUEST, _make_message

    reason = interrupt.get("reason") or "steering requested interruption"
    text = (
        "[steering] A high-priority message required interrupting your previous "
        f"turn. Steering reason: {reason}. Re-read the steering lane run dir "
        f"({interrupt.get('run_id')}) and the pending channel message before "
        "continuing."
    )
    try:
        agent.inbox.put(_make_message(MSG_REQUEST, "system", text))
        agent._wake_nap("steering_interrupt")
    except Exception:
        pass
