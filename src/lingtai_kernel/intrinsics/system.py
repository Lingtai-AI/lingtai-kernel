"""System intrinsic — runtime, lifecycle, and synchronization.

Actions:
    show     — display agent identity, runtime, and resource usage
    sleep    — pause execution; wakes on incoming message or timeout
    shutdown — initiate graceful self-termination
    restart  — stop, reload MCP servers and config from working dir, restart
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

def get_description(lang: str = "en") -> str:
    from ..i18n import t
    return t(lang, "system_tool.description")


def get_schema(lang: str = "en") -> dict:
    from ..i18n import t
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["show", "sleep", "shutdown", "restart", "silence", "quell", "revive", "annihilate"],
                "description": t(lang, "system_tool.action_description"),
            },
            "seconds": {
                "type": "number",
                "description": t(lang, "system_tool.seconds_description"),
            },
            "reason": {
                "type": "string",
                "description": t(lang, "system_tool.reason_description"),
            },
            "address": {
                "type": "string",
                "description": t(lang, "system_tool.address_description"),
            },
        },
        "required": ["action"],
    }


# Backward compat
SCHEMA = get_schema("en")
DESCRIPTION = get_description("en")


def handle(agent, args: dict) -> dict:
    """Handle system tool — runtime, lifecycle, synchronization."""
    action = args.get("action", "show")
    handler = {
        "show": _show,
        "sleep": _sleep,
        "shutdown": _shutdown,
        "restart": _restart,
        "silence": _silence,
        "quell": _quell,
        "revive": _revive,
        "annihilate": _annihilate,
    }.get(action)
    if handler is None:
        return {"status": "error", "message": f"Unknown system action: {action}"}
    return handler(agent, args)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def _show(agent, args: dict) -> dict:
    mail_addr = None
    if agent._mail_service is not None and agent._mail_service.address:
        mail_addr = agent._mail_service.address

    uptime = time.monotonic() - agent._uptime_anchor if agent._uptime_anchor is not None else 0.0
    life_left = max(0.0, agent._config.lifetime - uptime) if agent._uptime_anchor is not None else None

    usage = agent.get_token_usage()

    if agent._chat is not None:
        try:
            window_size = agent._chat.context_window()
            ctx_total = usage["ctx_total_tokens"]
            usage_pct = round(ctx_total / window_size * 100, 1) if window_size else 0.0
        except Exception:
            window_size = None
            usage_pct = None
    else:
        window_size = None
        usage_pct = None

    return {
        "status": "ok",
        "identity": {
            "agent_id": agent.agent_id,
            "agent_name": agent.agent_name,
            "working_dir": str(agent._working_dir),
            "mail_address": mail_addr,
        },
        "runtime": {
            "current_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "started_at": agent._started_at,
            "uptime_seconds": round(uptime, 1),
            "lifetime": agent._config.lifetime,
            "life_left": round(life_left, 1) if life_left is not None else None,
        },
        "tokens": {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "thinking_tokens": usage["thinking_tokens"],
            "cached_tokens": usage["cached_tokens"],
            "total_tokens": usage["total_tokens"],
            "api_calls": usage["api_calls"],
            "context": {
                "system_tokens": usage["ctx_system_tokens"],
                "tools_tokens": usage["ctx_tools_tokens"],
                "history_tokens": usage["ctx_history_tokens"],
                "total_tokens": usage["ctx_total_tokens"],
                "window_size": window_size,
                "usage_pct": usage_pct,
            },
        },
    }


# ---------------------------------------------------------------------------
# sleep (ported from clock.wait)
# ---------------------------------------------------------------------------

def _sleep(agent, args: dict) -> dict:
    max_wait = 300
    seconds = args.get("seconds")
    if seconds is not None:
        seconds = float(seconds)
        if seconds < 0:
            return {"status": "error", "message": "seconds must be non-negative"}
        seconds = min(seconds, max_wait)

    agent._log("system_sleep_start", seconds=seconds)

    if agent._cancel_event.is_set():
        agent._log("system_sleep_end", reason="silenced", waited=0.0)
        return {"status": "ok", "reason": "silenced", "waited": 0.0}
    if agent._mail_arrived.is_set():
        agent._log("system_sleep_end", reason="mail_arrived", waited=0.0)
        return {"status": "ok", "reason": "mail_arrived", "waited": 0.0}

    agent._mail_arrived.clear()

    poll_interval = 0.5
    t0 = time.monotonic()

    while True:
        waited = time.monotonic() - t0

        if agent._cancel_event.is_set():
            agent._log("system_sleep_end", reason="silenced", waited=waited)
            return {"status": "ok", "reason": "silenced", "waited": waited}

        if agent._mail_arrived.is_set():
            agent._log("system_sleep_end", reason="mail_arrived", waited=waited)
            return {"status": "ok", "reason": "mail_arrived", "waited": waited}

        if seconds is not None and waited >= seconds:
            agent._log("system_sleep_end", reason="timeout", waited=waited)
            return {"status": "ok", "reason": "timeout", "waited": waited}

        if seconds is not None:
            remaining = seconds - waited
            sleep_time = min(poll_interval, remaining)
        else:
            sleep_time = poll_interval

        agent._mail_arrived.wait(timeout=sleep_time)


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------

def _shutdown(agent, args: dict) -> dict:
    from ..i18n import t
    reason = args.get("reason", "")
    agent._log("shutdown_requested", reason=reason)
    agent._shutdown.set()
    return {
        "status": "ok",
        "message": t(agent._config.language, "system_tool.shutdown_message"),
    }


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------

def _restart(agent, args: dict) -> dict:
    from ..i18n import t
    reason = args.get("reason", "")
    agent._log("restart_requested", reason=reason)
    agent._restart_requested = True
    agent._shutdown.set()
    return {
        "status": "ok",
        "message": t(agent._config.language, "system_tool.restart_message"),
    }


# ---------------------------------------------------------------------------
# Admin gate mapping
# ---------------------------------------------------------------------------

_KARMA_ACTIONS = {"silence", "quell", "revive"}
_NIRVANA_ACTIONS = {"annihilate"}


def _check_karma_gate(agent, action: str, args: dict) -> dict | None:
    from ..handshake import is_agent
    if action in _KARMA_ACTIONS and not agent._admin.get("karma"):
        return {"error": True, "message": f"Not authorized for {action} (requires admin.karma=True)"}
    if action in _NIRVANA_ACTIONS and not (agent._admin.get("karma") and agent._admin.get("nirvana")):
        return {"error": True, "message": f"Not authorized for {action} (requires admin.nirvana=True)"}
    address = args.get("address")
    if not address:
        return {"error": True, "message": f"{action} requires an address"}
    if str(agent._working_dir) == str(address):
        return {"error": True, "message": f"Cannot {action} self — use shutdown/restart instead"}
    if not is_agent(address):
        return {"error": True, "message": f"No agent at {address}"}
    return None


def _silence(agent, args: dict) -> dict:
    from pathlib import Path
    from ..handshake import is_alive
    err = _check_karma_gate(agent, "silence", args)
    if err:
        return err
    address = args["address"]
    if not is_alive(address):
        return {"error": True, "message": f"Agent at {address} is not running"}
    (Path(address) / ".silence").write_text("")
    agent._log("karma_silence", target=address)
    return {"status": "silenced", "address": address}


def _quell(agent, args: dict) -> dict:
    from pathlib import Path
    from ..handshake import is_alive
    err = _check_karma_gate(agent, "quell", args)
    if err:
        return err
    address = args["address"]
    if not is_alive(address):
        return {"error": True, "message": f"Agent at {address} is not running — already dormant?"}
    (Path(address) / ".quell").write_text("")
    agent._log("karma_quell", target=address)
    return {"status": "quelled", "address": address}


def _revive(agent, args: dict) -> dict:
    from ..handshake import is_alive
    err = _check_karma_gate(agent, "revive", args)
    if err:
        return err
    address = args["address"]
    if is_alive(address):
        return {"error": True, "message": f"Agent at {address} is already running"}
    revived = agent._revive_agent(address)
    if revived is None:
        return {"error": True, "message": "Revive not supported — no _revive_agent handler"}
    agent._log("karma_revive", target=address)
    return {"status": "revived", "address": address}


def _annihilate(agent, args: dict) -> dict:
    import shutil
    from pathlib import Path
    from ..handshake import is_alive
    err = _check_karma_gate(agent, "annihilate", args)
    if err:
        return err
    address = args["address"]
    if is_alive(address):
        (Path(address) / ".quell").write_text("")
        import time as _time
        deadline = _time.time() + 10.0
        while _time.time() < deadline:
            if not is_alive(address):
                break
            _time.sleep(0.5)
        else:
            if is_alive(address):
                return {"error": True, "message": f"Agent at {address} did not quell within timeout"}
    shutil.rmtree(address)
    agent._log("karma_annihilate", target=address)
    return {"status": "annihilated", "address": address}
