"""``system(action="meta")`` — read-only complete current runtime diagnostics.

The default tail ``agent_meta`` is intentionally small (time, context size,
active warnings).  This action returns the full current snapshot built by the
kernel's own ``build_full_runtime_meta`` — token ``current_call`` and
since-molt session/cache numbers, context warnings, result-size candidates,
adapter diagnostics — through the granted ``SystemRuntimePort``.  It never
refreshes, molts, changes configuration, or consumes one-shot events.
"""
from __future__ import annotations


def _meta(agent, args: dict) -> dict:
    runtime_meta = getattr(agent, "runtime_meta", None)
    if callable(runtime_meta):
        snapshot = runtime_meta()
    else:
        # Legacy direct-agent callers have no port bridge; use the same builder.
        from lingtai.kernel.meta_block import build_full_runtime_meta

        snapshot = build_full_runtime_meta(agent)
    return {"status": "ok", **dict(snapshot)}
