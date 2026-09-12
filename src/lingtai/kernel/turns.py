"""Protocol-neutral correlated inbound-turn values and BaseAgent helpers.

This module is the small synchronous boundary used by driving adapters that need
one terminal result for one submitted text turn.  It deliberately knows nothing
about ACP, JSON-RPC, sessions, workspaces, or transport framing.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from .message import MSG_CORRELATED_TURN, MSG_REQUEST, Message, _make_message
from .execution_workspace import ExecutionWorkspace
from .turn_events import TurnToolObserver
from .turn_permissions import TurnPermissionBroker


class TurnOutcome(str, Enum):
    """Terminal classification of a correlated inbound turn."""

    NORMAL = "normal"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TurnOrigin(str, Enum):
    """The admission provenance of a request that may call a provider.

    ``AUTHENTICATED_ADAPTER`` deliberately names a protocol-neutral boundary.
    ACP is one adapter that may hold this origin; ordinary inbox messages and
    direct callers do not acquire it by choosing a sender string.
    """

    LEGACY = "legacy"
    INTERNAL_EVENT = "internal_event"
    AUTHENTICATED_ADAPTER = "authenticated_adapter"


@dataclass(frozen=True, slots=True)
class TurnAdmissionDecision:
    """A safe, structured origin-admission result for one provider turn."""

    allowed: bool
    origin: TurnOrigin
    policy_version: str
    reason_code: str


class TurnOriginPolicy(Protocol):
    """Outer policy that admits a typed origin before provider work begins."""

    def admit_turn_origin(self, origin: TurnOrigin) -> TurnAdmissionDecision: ...


class TurnAdmissionError(PermissionError):
    """A policy denied an attempted provider-turn origin."""

    def __init__(self, decision: TurnAdmissionDecision):
        self.decision = decision
        super().__init__(f"turn origin rejected: {decision.reason_code}")


@dataclass(frozen=True, slots=True)
class TurnResult:
    """The exactly-once terminal receipt returned by :class:`TurnHandle`."""

    correlation_id: str
    outcome: TurnOutcome
    text: str = ""
    error: str | None = None
    errors: tuple[str, ...] = ()


@dataclass(slots=True)
class _TurnControl:
    correlation_id: str
    content: str
    sender: str
    origin: TurnOrigin
    execution_workspace: ExecutionWorkspace | None = None
    tool_observer: TurnToolObserver | None = None
    permission_broker: TurnPermissionBroker | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    future: Future[TurnResult] = field(default_factory=Future)
    cancel_callback: Callable[[str], bool] | None = None
    settlement_claimed: bool = False


@dataclass(frozen=True, slots=True)
class _TurnEnvelope:
    control: _TurnControl


class TurnHandle:
    """Caller-owned handle for one correlated turn.

    ``cancel()`` requests cooperative cancellation.  It does not claim provider
    abort or running-tool preemption; ``result()`` remains the settlement point.
    """

    __slots__ = ("_control",)

    def __init__(self, control: _TurnControl):
        self._control = control

    @property
    def correlation_id(self) -> str:
        return self._control.correlation_id

    def cancel(self) -> bool:
        callback = self._control.cancel_callback
        return False if callback is None else callback(self.correlation_id)

    def cancel_requested(self) -> bool:
        return self._control.cancel_requested.is_set()

    def done(self) -> bool:
        return self._control.future.done()

    def result(self, timeout: float | None = None) -> TurnResult:
        return self._control.future.result(timeout=timeout)


def _ensure_turn_state(agent) -> tuple[threading.Lock, dict[str, _TurnControl]]:
    """Return turn state, lazily installing it for narrow test doubles."""

    lock = getattr(agent, "_turn_controls_lock", None)
    if lock is None:
        lock = threading.Lock()
        agent._turn_controls_lock = lock
    controls = getattr(agent, "_turn_controls", None)
    if controls is None:
        controls = {}
        agent._turn_controls = controls
    if not hasattr(agent, "_current_turn_control"):
        agent._current_turn_control = None
    return lock, controls


def admit_turn_origin(agent, origin: TurnOrigin) -> TurnAdmissionDecision:
    """Apply an optional typed origin policy without trusting sender strings.

    Generic ``BaseAgent`` callers retain the historical default-allow behavior.
    A constrained profile sets ``_requires_turn_origin_policy`` at composition;
    if its policy is absent, admission fails closed instead of silently taking
    the generic default. Malformed policy output also denies rather than
    allowing an unclassified turn to reach the provider.
    """

    policy = getattr(agent, "_turn_origin_policy", None)
    if policy is None:
        if getattr(agent, "_requires_turn_origin_policy", False):
            decision = TurnAdmissionDecision(
                False, origin, "required-policy-missing", "required_policy_missing"
            )
            logger = getattr(agent, "_log", None)
            if callable(logger):
                logger(
                    "turn_origin_rejected",
                    origin=origin.value,
                    policy_version=decision.policy_version,
                    reason_code=decision.reason_code,
                )
            raise TurnAdmissionError(decision)
        return TurnAdmissionDecision(True, origin, "legacy-default", "allowed")
    try:
        decision = policy.admit_turn_origin(origin)
    except Exception:
        decision = TurnAdmissionDecision(False, origin, "policy-error", "policy_error")
    if (
        not isinstance(decision, TurnAdmissionDecision)
        or decision.origin is not origin
        or not isinstance(decision.policy_version, str)
        or not decision.policy_version
        or not isinstance(decision.reason_code, str)
        or not decision.reason_code
    ):
        decision = TurnAdmissionDecision(False, origin, "policy-error", "invalid_policy_decision")
    if not decision.allowed:
        logger = getattr(agent, "_log", None)
        if callable(logger):
            logger(
                "turn_origin_rejected",
                origin=origin.value,
                policy_version=decision.policy_version,
                reason_code=decision.reason_code,
            )
        raise TurnAdmissionError(decision)
    return decision


def submit_turn(
    agent,
    content: str,
    *,
    sender: str = "user",
    correlation_id: str | None = None,
    execution_workspace: str | Path | ExecutionWorkspace | None = None,
    tool_observer: TurnToolObserver | None = None,
    permission_broker: TurnPermissionBroker | None = None,
    origin: TurnOrigin = TurnOrigin.LEGACY,
) -> TurnHandle:
    """Queue one text turn and return its correlated terminal handle."""

    if not isinstance(content, str):
        raise TypeError("turn content must be a string")
    if not isinstance(sender, str) or not sender:
        raise ValueError("turn sender must be a non-empty string")
    if not isinstance(origin, TurnOrigin):
        raise TypeError("origin must be a TurnOrigin")
    if correlation_id is None:
        correlation_id = f"turn_{uuid4().hex}"
    if not isinstance(correlation_id, str) or not correlation_id:
        raise ValueError("correlation_id must be a non-empty string")
    if tool_observer is not None and not callable(
        getattr(tool_observer, "on_tool_lifecycle", None)
    ):
        raise TypeError("tool_observer must define on_tool_lifecycle(event)")
    if permission_broker is not None and not callable(
        getattr(permission_broker, "request_permission", None)
    ):
        raise TypeError("permission_broker must define request_permission(request)")

    shutdown = getattr(agent, "_shutdown", None)
    if shutdown is not None and shutdown.is_set():
        raise RuntimeError("agent is stopping")

    admit_turn_origin(agent, origin)

    lock, controls = _ensure_turn_state(agent)
    if execution_workspace is not None and not isinstance(
        execution_workspace, ExecutionWorkspace
    ):
        execution_workspace = ExecutionWorkspace(Path(execution_workspace))
    control = _TurnControl(
        correlation_id=correlation_id,
        content=content,
        sender=sender,
        origin=origin,
        execution_workspace=execution_workspace,
        tool_observer=tool_observer,
        permission_broker=permission_broker,
    )
    control.cancel_callback = lambda requested_id: cancel_turn(agent, requested_id)
    with lock:
        # Linearize registration against lifecycle.stop: if stop set shutdown
        # before acquiring this lock, reject; if it sets shutdown afterward,
        # cancel_all_turns will observe and settle this registered control.
        if shutdown is not None and shutdown.is_set():
            raise RuntimeError("agent is stopping")
        if correlation_id in controls:
            raise ValueError(f"duplicate live correlation_id: {correlation_id}")
        controls[correlation_id] = control

    try:
        agent.inbox.put(
            _make_message(
                MSG_CORRELATED_TURN,
                sender,
                _TurnEnvelope(control),
            )
        )
    except BaseException:
        with lock:
            if controls.get(correlation_id) is control:
                controls.pop(correlation_id, None)
            control.cancel_callback = None
        raise

    wake = getattr(agent, "_wake_nap", None)
    if callable(wake):
        try:
            wake("correlated_turn_received")
        except Exception:
            # Inbox publication is the acceptance boundary. Nap wake is an
            # optimization and cannot be allowed to orphan an already queued
            # envelope by making submit appear to fail.
            pass
    return TurnHandle(control)


def cancel_turn(agent, correlation_id: str) -> bool:
    """Request cancellation only for the matching live correlated turn."""

    lock, controls = _ensure_turn_state(agent)
    with lock:
        control = controls.get(correlation_id)
        if control is None or control.settlement_claimed:
            return False
        control.cancel_requested.set()
        if getattr(agent, "_current_turn_control", None) is control:
            # Latch while holding the same lock that protects settlement/current
            # identity. Otherwise settlement could claim this turn, the next turn
            # could clear its fresh-dequeue latch, and this delayed set could leak
            # cancellation into that unrelated next turn.
            agent._request_turn_cancel()
    return True


def control_from_message(msg: Message) -> _TurnControl | None:
    """Extract the private correlated control from a turn inbox message."""

    envelope = msg.content
    if msg.type != MSG_CORRELATED_TURN or not isinstance(envelope, _TurnEnvelope):
        return None
    return envelope.control


def begin_turn(agent, msg: Message) -> _TurnControl | None:
    """Bind a freshly dequeued correlated turn as the current logical turn."""

    control = control_from_message(msg)
    if control is None:
        return None
    lock, controls = _ensure_turn_state(agent)
    with lock:
        if (
            controls.get(control.correlation_id) is not control
            or control.settlement_claimed
        ):
            return None
        agent._current_turn_control = control
        cancelled = control.cancel_requested.is_set()
    if cancelled:
        # The normal fresh-dequeue stale-latch clear has already happened.  A
        # pending handle's own cancellation now becomes the current cooperative
        # latch and is never allowed to leak into a later turn.
        agent._request_turn_cancel()
    return control


def correlated_message_text(msg: Message) -> Message:
    """Translate a bound envelope to the existing request-processing shape."""

    control = control_from_message(msg)
    if control is None:
        return msg
    return _make_message(MSG_CORRELATED_TURN, control.sender, control.content)


def correlated_retry_message(agent, control, msg: Message) -> Message | None:
    """Keep an internal retry inside its still-current, admitted logical turn.

    Only the serialized run loop supplies the original control object. Message
    ids, sender strings and correlated type labels cannot supply this authority.
    This translates an in-loop retry; it neither queues nor registers a turn.
    """
    if (
        not isinstance(control, _TurnControl)
        or msg.type != MSG_REQUEST
        or not isinstance(msg.content, str)
    ):
        return None
    try:
        admit_turn_origin(agent, control.origin)
    except TurnAdmissionError:
        return None
    lock, controls = _ensure_turn_state(agent)
    with lock:
        if (
            controls.get(control.correlation_id) is not control
            or agent._current_turn_control is not control
            or control.settlement_claimed
            or control.cancel_requested.is_set()
            or agent._cancel_event.is_set()
            or agent._shutdown.is_set()
        ):
            return None
        return _make_message(MSG_CORRELATED_TURN, msg.sender, msg.content)


def settle_turn(
    agent,
    control: _TurnControl,
    *,
    outcome: TurnOutcome,
    text: str = "",
    error: str | None = None,
    errors: tuple[str, ...] = (),
    cooperative_cancelled: bool = False,
) -> bool:
    """Linearize one terminal receipt; cancellation wins until this claim."""

    lock, controls = _ensure_turn_state(agent)
    with lock:
        if control.settlement_claimed:
            return False
        control.settlement_claimed = True
        control.cancel_callback = None
        cancelled = control.cancel_requested.is_set() or cooperative_cancelled
        if controls.get(control.correlation_id) is control:
            controls.pop(control.correlation_id, None)
        if getattr(agent, "_current_turn_control", None) is control:
            agent._current_turn_control = None

    if cancelled:
        result = TurnResult(
            correlation_id=control.correlation_id,
            outcome=TurnOutcome.CANCELLED,
        )
    else:
        result = TurnResult(
            correlation_id=control.correlation_id,
            outcome=outcome,
            text=text,
            error=error,
            errors=errors,
        )
    control.future.set_result(result)
    return True


def cancel_all_turns(agent, *, reason: str = "agent stopped") -> int:
    """Settle every live handle during terminal process teardown."""

    lock, controls = _ensure_turn_state(agent)
    claimed: list[_TurnControl] = []
    had_current = False
    with lock:
        current = getattr(agent, "_current_turn_control", None)
        for control in list(controls.values()):
            if control.settlement_claimed:
                continue
            control.cancel_requested.set()
            control.settlement_claimed = True
            control.cancel_callback = None
            claimed.append(control)
            if control is current:
                had_current = True
        controls.clear()
        agent._current_turn_control = None

    if had_current:
        agent._request_turn_cancel()
    for control in claimed:
        control.future.set_result(
            TurnResult(
                correlation_id=control.correlation_id,
                outcome=TurnOutcome.CANCELLED,
                error=reason,
            )
        )
    return len(claimed)


__all__ = [
    "TurnAdmissionDecision",
    "TurnAdmissionError",
    "TurnHandle",
    "TurnOrigin",
    "TurnOriginPolicy",
    "TurnOutcome",
    "TurnResult",
    "admit_turn_origin",
]
