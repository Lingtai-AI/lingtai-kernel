"""System intrinsic — runtime, lifecycle, and synchronization.

Actions (voluntary, agent-callable):
    nap       — pause execution; wakes on incoming message or timeout
    refresh   — stop, reload MCP servers and config from working dir, restart
    sleep     — self only, go to sleep (no karma needed)
    lull      — put another agent to sleep (requires karma)
    suspend   — suspend another agent (requires karma)
    cpr       — resuscitate a suspended agent (requires karma)
    interrupt — interrupt a running agent's current turn (requires karma)
    clear     — force a full molt on another agent (requires karma)
    nirvana   — permanently destroy an agent's working directory (requires nirvana)
    presets   — list available presets in the agent's library
    dismiss   — dismiss one or more system notifications by notif_id

Action (involuntary, kernel-synthesized only — NOT callable by the agent):
    notification — synthesized by the kernel for mail arrival, bounce, and
                   future MCP listener events. Spliced into the wire chat
                   via tc_inbox. The public ``handle()`` dispatch rejects
                   this action with an error message.

Identity, runtime, and stamina state surface via other channels:
    - identity prompt section — every turn, cached prefix
    - meta line `context.{system,history}_tokens` + `stamina_left_seconds`
      on every tool result and text input
    - `.status.json` — written by the kernel; read with read({".status.json"})
      when the agent wants the deep dive

Sub-modules:
    nap.py           — _nap() function.
    preset.py        — _preset_ref_in(), _check_context_fits(), _refresh(), _presets().
    karma.py         — _KARMA_ACTIONS, _NIRVANA_ACTIONS, _check_karma_gate(),
                       _sleep(), _lull(), _suspend(), _cpr(), _interrupt(),
                       _clear(), _nirvana().
    notification.py  — _dismiss() function.
    schema.py        — get_description(), get_schema().
"""
from __future__ import annotations

# --- Re-exports from sub-modules for backward compatibility ---

# Schema (tool registration)
from .schema import get_description, get_schema  # noqa: F401

# Notification (dismiss — cross-module import from email/manager.py)
from .notification import _dismiss  # noqa: F401

# Nap
from .nap import _nap  # noqa: F401

# Preset
from .preset import _preset_ref_in, _check_context_fits, _refresh, _presets  # noqa: F401

# Karma
from .karma import (  # noqa: F401
    _KARMA_ACTIONS,
    _NIRVANA_ACTIONS,
    _check_karma_gate,
    _sleep,
    _lull,
    _suspend,
    _cpr,
    _interrupt,
    _clear,
    _nirvana,
)


# ---------------------------------------------------------------------------
# Module-level intrinsic protocol — handle()
# ---------------------------------------------------------------------------


def handle(agent, args: dict) -> dict:
    """Handle system tool — runtime, lifecycle, synchronization."""
    action = args.get("action")
    # Belt-and-suspenders: 'notification' is kernel-synthesized only.
    # Even if the LLM hallucinates this action, refuse to dispatch.
    if action == "notification":
        return {
            "status": "error",
            "message": (
                "system(action='notification', ...) is reserved for kernel-"
                "synthesized notifications and cannot be invoked directly. "
                "Use system(action='dismiss', ids=[...]) to dismiss "
                "notifications you have handled."
            ),
        }
    handler = {
        "nap": _nap,
        "refresh": _refresh,
        "sleep": _sleep,
        "lull": _lull,
        "suspend": _suspend,
        "cpr": _cpr,
        "interrupt": _interrupt,
        "clear": _clear,
        "nirvana": _nirvana,
        "presets": _presets,
        "dismiss": _dismiss,
    }.get(action)
    if handler is None:
        return {"status": "error", "message": f"Unknown system action: {action}"}
    return handler(agent, args)
