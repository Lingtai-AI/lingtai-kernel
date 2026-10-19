"""Core post-response review and single-use, call-bound execution receipts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import json
from time import monotonic
from threading import Lock
from typing import Callable, Protocol

from .llm.base import LLMResponse, ToolCall
from .tool_call_guard import GuardDecision


@dataclass(frozen=True)
class ToolDecisionResult:
    """Classification, not proof of authorization. Only ``passed`` gates dispatch."""

    dangerous: bool
    passed: bool
    confidence: float

    def __post_init__(self) -> None:
        if type(self.dangerous) is not bool or type(self.passed) is not bool:
            raise ValueError("Tool decision flags must be booleans")
        if (type(self.confidence) not in (int, float)
                or not math.isfinite(self.confidence)
                or not 0 <= self.confidence <= 1):
            raise ValueError("Tool decision confidence must be finite and in [0,1]")

    def to_payload(self) -> dict:
        return {"dangerous": self.dangerous, "pass": self.passed,
                "confidence": self.confidence}


class ToolDecisionPhase(Protocol):
    """SDK injection seam: only one detached proposed call, never Agent context.

    Implementations must not dispatch tools. The call id is local correlation;
    the default LLM adapter sends only name and arguments to its reviewer.
    """

    def review(self, call: ToolCall) -> ToolDecisionResult: ...


@dataclass(frozen=True)
class _ReviewedCall:
    call: ToolCall
    signature: str | None
    decision: GuardDecision


class ToolDecisionBatch:
    """One response's receipts, consumed once; never invokes a reviewer.

    Bind object identity AND typed id/name/arguments AND API round. A missing,
    changed, stale or replayed call fails closed, including absent provider ids.
    The lock also protects consumers in parallel dispatch.
    """

    def __init__(self, api_call_id: str | None, entries: list[_ReviewedCall]):
        self._api_call_id = api_call_id
        self._entries = entries
        self._reviewed_calls = tuple(entry.call for entry in entries)
        self._lock = Lock()

    def reviewed(self, call: ToolCall) -> bool:
        """Keep reviewed objects gated even if their name is later mutated."""
        return any(original is call for original in self._reviewed_calls)

    def consume(self, call: ToolCall, api_call_id: str | None) -> GuardDecision:
        with self._lock:
            for i, entry in enumerate(self._entries):
                if entry.call is call:
                    self._entries.pop(i)
                    try:
                        matches = entry.signature is not None and _call_signature(call) == entry.signature
                    except Exception:
                        matches = False
                    if api_call_id == self._api_call_id and matches:
                        return entry.decision
                    break
        return missing_tool_decision()


def _call_signature(call: ToolCall) -> str:
    # JSON preserves types that Python equality conflates (e.g. True == 1).
    return json.dumps([call.id, call.name, call.args], sort_keys=True,
                      allow_nan=False, ensure_ascii=False, separators=(",", ":"))


def missing_tool_decision() -> GuardDecision:
    return GuardDecision.deny(
        check_name="tool_decision", reason="Missing, changed or consumed tool decision; dispatch blocked",
    )


def review_tool_response(
    response: LLMResponse,
    phase_factory: Callable[[], ToolDecisionPhase | None],
    logger_fn: Callable | None = None,
    cancelled: Callable[[], bool] | None = None,
    *, tool_decision_tools: list[str] | None = None,
) -> LLMResponse:
    """Review a COMPLETE API response before returning it to any dispatch loop.

    Exact configured names select calls; empty/no matches constructs no phase.
    Failures become per-call denials, not send failures/retries; assistant text,
    calls and provider history are left intact. Every selected sibling is reviewed
    before this returns. The reviewer sees only a detached raw typed call.
    """
    selected = [call for call in response.tool_calls
                if call.name in (tool_decision_tools or [])]
    if not selected:
        return response
    try:
        phase = phase_factory()
    except Exception:
        phase = None
    entries = []
    for call in selected:
        started = monotonic()
        payload = None
        failure = None
        signature = None
        try:
            signature = _call_signature(call)
            snapshot = deepcopy(call)
            if phase is None:
                raise RuntimeError("No tool decision phase was injected")
            result = phase.review(deepcopy(snapshot))
            if type(result) is not ToolDecisionResult:
                raise ValueError("Unexpected tool decision result")
            result.__post_init__()
            payload = result.to_payload()
            if cancelled is not None and cancelled():
                failure = "CancelledBeforeDispatch"
        except Exception as exc:
            failure = type(exc).__name__
        metadata = {
            "schema": "lingtai.tool_decision/v1", "decision": payload,
            "failure": failure, "latency_ms": round((monotonic() - started) * 1000, 3),
        }
        allowed = failure is None and payload is not None and payload["pass"] is True
        decision = GuardDecision(
            allowed=allowed, check_name="tool_decision",
            reason=("Cancelled during tool decision; dispatch blocked"
                    if failure == "CancelledBeforeDispatch" else
                    "Tool decision inference failed; dispatch blocked" if failure else
                    "" if allowed else "Tool decision pass=false; dispatch blocked"),
            action="allow" if allowed else "deny",
            severity="info" if allowed else "error", metadata=metadata,
        )
        entries.append(_ReviewedCall(call, signature, decision))
        if logger_fn is not None:
            try:
                logger_fn("tool_decision", tool_name=call.name, tool_call_id=call.id,
                          api_call_id=response.api_call_id, allowed=allowed, **metadata)
            except Exception:
                pass
    response.tool_decisions = ToolDecisionBatch(response.api_call_id, entries)
    return response
