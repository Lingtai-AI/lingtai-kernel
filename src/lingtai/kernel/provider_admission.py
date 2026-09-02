"""Provider-call admission port for profiles that restrict model initiation.

The port protects *structural* provider-call paths.  It deliberately does not
claim to sandbox code that already runs in the same OS trust domain as a
full-tool agent.  A driving adapter supplies the policy/transport; Core owns
the typed parent context and makes every concrete ``send``/``generate`` call
cross this one boundary.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ProviderCallClass(str, Enum):
    """The execution shape requesting one actual model-provider call."""

    ROOT = "root"
    DAEMON = "daemon"
    AVATAR_CHILD = "avatar_child"


class DerivedLaunchCapability(str, Enum):
    """One production tool route that can create a derived execution."""

    DAEMON = "daemon"
    AVATAR = "avatar"


class ProviderAdmissionState(str, Enum):
    """The driver-visible result of deciding one provider-call admission.

    ``INDETERMINATE`` is deliberately distinct from a policy denial: it means
    the driving authority was unavailable, malformed, or otherwise unable to
    establish the current call's authority.  Core fails closed for both states,
    but keeping the distinction makes an unconnected integration visible rather
    than silently equivalent to an ordinary rejection.
    """

    GRANTED = "granted"
    DENIED = "denied"
    INDETERMINATE = "indeterminate"


class ProviderAdmissionDecisionSource(str, Enum):
    """Where an admission decision's non-grant outcome originated.

    A caller must not infer this from a reason string: a Driver policy denial
    and a local transport failure are both fail-closed, but only the former is
    a response from the remote authority.  ``LOCAL_POLICY`` covers checks made
    before a Driver exchange (for example, a parent/capability mismatch).
    """

    DRIVER = "driver"
    TRANSPORT = "transport"
    LOCAL_POLICY = "local_policy"


class DerivedLaunchEndpointLease(Protocol):
    """Opaque one-use child handoff with exactly one required cleanup action."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RootProviderAdmission:
    """Core-private context for one admitted root turn.

    ``correlation_id`` is audit/routing material only.  It is never a bearer
    credential: production driver adapters must bind it to their own protected
    in-memory admission state before granting a call.
    """

    correlation_id: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class _DerivedAdmissionHandle:
    """Core-private, non-serializable identity for a derived execution.

    This is deliberately an object identity, not a path, runtime id,
    correlation id, or digest.  The future driver bridge may retain this
    handle in an in-memory mapping, but it must never serialize it into the
    Agent-visible process state.
    """

    marker: object = field(default_factory=object, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class DerivedProviderAdmission:
    """Core-private parent for a daemon/avatar provider call.

    ``_handle`` is a Core-private in-memory identity.  It contains no path,
    agent directory, workspace, argv, environment, correlation id, digest, or
    reusable grant.
    """

    root: RootProviderAdmission
    call_class: ProviderCallClass
    _handle: _DerivedAdmissionHandle = field(
        default_factory=_DerivedAdmissionHandle, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.root, RootProviderAdmission):
            raise TypeError("derived admission requires a root admission")
        if self.call_class not in (
            ProviderCallClass.DAEMON,
            ProviderCallClass.AVATAR_CHILD,
        ):
            raise ValueError("derived admission requires daemon or avatar_child class")
        if not isinstance(self._handle, _DerivedAdmissionHandle):
            raise TypeError("derived admission handle must stay Core-private")


def begin_derived_provider_admission(
    root: RootProviderAdmission,
    call_class: ProviderCallClass,
) -> DerivedProviderAdmission:
    """Issue a Core-private derived parent for one host-mediated execution.

    The caller deliberately supplies no route, workspace, agent directory, or
    string token.  The driver bridge may associate the returned object's hidden
    handle with its own in-memory transport state, but authority is re-decided
    when the actual provider call reaches :func:`require_provider_admission`.

    v0 permits exactly one derived hop. This factory accepts a root admission,
    never an existing derived admission, so Core cannot mint a nested
    daemon/avatar parent by accident. The Driver must enforce the same rule at
    its launch boundary; a future recursive profile needs an explicitly new
    contract rather than relaxing this type boundary.
    """

    return DerivedProviderAdmission(root=root, call_class=call_class)


ProviderAdmissionParent = RootProviderAdmission | DerivedProviderAdmission


@dataclass(frozen=True, slots=True)
class ProviderCallDecision:
    """A safe result of attempting one provider-call admission."""

    state: ProviderAdmissionState
    reason_code: str
    source: ProviderAdmissionDecisionSource = ProviderAdmissionDecisionSource.LOCAL_POLICY

    @property
    def allowed(self) -> bool:
        """Compatibility projection; only an explicit grant is allowed."""

        return self.state is ProviderAdmissionState.GRANTED


@dataclass(frozen=True, slots=True)
class DerivedLaunchDecision:
    """A Driver-owned decision before a daemon/avatar process can launch.

    ``audit_id`` is correlation material only.  It is never a grant or a
    substitute for the Core-private admission parent passed to the Port.
    """

    state: ProviderAdmissionState
    reason_code: str
    audit_id: str | None = None
    # An adapter-owned, non-serializable, one-use child handoff.  Core neither
    # inspects nor reconstructs it; a consumer must either hand it to the
    # exact supported process boundary or release it before returning.
    child_endpoint_lease: DerivedLaunchEndpointLease | None = field(
        default=None, repr=False, compare=False
    )
    source: ProviderAdmissionDecisionSource = ProviderAdmissionDecisionSource.LOCAL_POLICY

    @property
    def allowed(self) -> bool:
        return self.state is ProviderAdmissionState.GRANTED


class ProviderCallAdmissionPort(Protocol):
    """Driver-facing Core port, invoked once per actual provider call.

    Returning ``GRANTED`` is the adapter's linearization point for that one
    call.  Implementations must decide against current authority, not a
    turn-start snapshot or an implicit cache; Core calls this method again for
    the next ``send`` or ``generate``.  Unavailable or malformed authority is
    ``INDETERMINATE`` and fails closed before provider I/O.
    """

    def authorize_provider_call(
        self,
        parent: ProviderAdmissionParent,
        call_class: ProviderCallClass,
    ) -> ProviderCallDecision: ...


class DerivedLaunchAdmissionPort(Protocol):
    """Driver-facing decision port for one daemon/avatar launch request.

    The root parent is a Core-private object rather than a path, registry ref,
    correlation id, or user-provided depth.  A future Driver bridge resolves
    all durable identity/lineage facts itself before returning a decision.
    """

    def authorize_derived_launch(
        self,
        parent: RootProviderAdmission,
        capability: DerivedLaunchCapability,
    ) -> DerivedLaunchDecision: ...


class ProviderAdmissionError(PermissionError):
    """Raised before a provider request when admission is absent or denied."""

    def __init__(
        self,
        reason_code: str,
        state: ProviderAdmissionState = ProviderAdmissionState.DENIED,
        source: ProviderAdmissionDecisionSource = ProviderAdmissionDecisionSource.LOCAL_POLICY,
    ):
        self.reason_code = reason_code
        self.state = state
        self.source = source
        super().__init__(f"provider call was not admitted: {reason_code}")


class DerivedLaunchAdmissionError(PermissionError):
    """Raised before a derived process launch when authority is unavailable."""

    def __init__(self, decision: DerivedLaunchDecision):
        self.decision = decision
        super().__init__(f"derived launch was not admitted: {decision.reason_code}")


def _discard_derived_launch_decision(
    decision: object,
    *,
    reason_code: str,
) -> DerivedLaunchDecision:
    """Replace an untrusted Port decision without stranding its lease.

    Core does not inspect or reconstruct the opaque child endpoint.  It does,
    however, own the last reference when it rejects an adapter decision before
    a concrete launch consumer can receive it.
    """

    if isinstance(decision, DerivedLaunchDecision):
        lease = decision.child_endpoint_lease
        if lease is not None:
            try:
                lease.close()
            except OSError:
                pass
    return DerivedLaunchDecision(ProviderAdmissionState.INDETERMINATE, reason_code)


_current_parent: ContextVar[ProviderAdmissionParent | None] = ContextVar(
    "lingtai_current_provider_admission", default=None
)


def bind_provider_admission(parent: ProviderAdmissionParent) -> Token:
    """Install one Core-private parent for the current execution context."""

    if not isinstance(parent, (RootProviderAdmission, DerivedProviderAdmission)):
        raise TypeError("parent must be a provider-admission context")
    return _current_parent.set(parent)


def clear_provider_admission(token: Token) -> None:
    """Restore the previous execution context after a turn/call completes."""

    _current_parent.reset(token)


def clear_current_provider_admission() -> None:
    """Fail closed when an exceptional path ends a Core execution context.

    Normal completion restores the exact prior context through the ``Token``
    returned by :func:`bind_provider_admission`.  The agent run-loop also
    calls this defensive cleanup in its outer ``finally`` so an exception
    cannot leave a root admission installed in a reused worker context.
    """

    _current_parent.set(None)


def current_provider_admission() -> ProviderAdmissionParent | None:
    """Return the current Core-private parent, if provider work is admitted."""

    return _current_parent.get()


def current_provider_call_class() -> ProviderCallClass:
    """Classify the current request without trusting caller-provided strings."""

    parent = current_provider_admission()
    if isinstance(parent, DerivedProviderAdmission):
        return parent.call_class
    return ProviderCallClass.ROOT


def require_derived_launch_admission(
    port: DerivedLaunchAdmissionPort | None,
    capability: DerivedLaunchCapability,
    *,
    required: bool = False,
) -> DerivedLaunchDecision:
    """Decide one daemon/avatar launch before it can create process state.

    Generic LingTai compositions retain their historical behavior when no
    derived-launch port is configured.  A constrained profile supplies a port;
    missing root authority, malformed/raising ports, and all non-grants then
    fail closed with a structured domain error before launch side effects.
    """

    if not isinstance(capability, DerivedLaunchCapability):
        raise TypeError("derived launch capability must be typed")
    if port is None:
        if required:
            raise DerivedLaunchAdmissionError(
                DerivedLaunchDecision(
                    ProviderAdmissionState.INDETERMINATE,
                    "required_derived_launch_admission_port_missing",
                )
            )
        return DerivedLaunchDecision(ProviderAdmissionState.GRANTED, "legacy_default")

    parent = current_provider_admission()
    if not isinstance(parent, RootProviderAdmission):
        reason = (
            "nested_derived_launch_denied"
            if isinstance(parent, DerivedProviderAdmission)
            else "missing_root_provider_admission"
        )
        raise DerivedLaunchAdmissionError(
            DerivedLaunchDecision(ProviderAdmissionState.DENIED, reason)
        )
    try:
        decision = port.authorize_derived_launch(parent, capability)
    except DerivedLaunchAdmissionError:
        # A structured adapter denial can carry an opaque child-endpoint
        # lease.  Preserve it for the concrete launch consumer, which owns
        # releasing that lease before surfacing the failure.
        raise
    except Exception:
        decision = DerivedLaunchDecision(
            ProviderAdmissionState.INDETERMINATE,
            "derived_launch_admission_port_error",
        )
    if (
        not isinstance(decision, DerivedLaunchDecision)
        or not isinstance(decision.state, ProviderAdmissionState)
        or not isinstance(decision.reason_code, str)
        or not decision.reason_code
        or not isinstance(decision.source, ProviderAdmissionDecisionSource)
        or (
            decision.audit_id is not None
            and (not isinstance(decision.audit_id, str) or not decision.audit_id)
        )
    ):
        decision = _discard_derived_launch_decision(
            decision,
            reason_code="malformed_derived_launch_admission_decision",
        )
    if not decision.allowed:
        raise DerivedLaunchAdmissionError(decision)
    return decision


def require_provider_admission(port: ProviderCallAdmissionPort | None) -> None:
    """Cross the one structural provider boundary or fail before provider I/O.

    ``None`` retains generic LingTai behavior.  A constrained profile injects a
    real port and therefore fails closed for calls without a bound parent,
    malformed adapter decisions, or explicit denial.
    """

    if port is None:
        return
    parent = current_provider_admission()
    if parent is None:
        raise ProviderAdmissionError("missing_provider_admission")
    call_class = current_provider_call_class()
    try:
        decision = port.authorize_provider_call(parent, call_class)
    except Exception:
        decision = ProviderCallDecision(
            ProviderAdmissionState.INDETERMINATE,
            "provider_admission_port_error",
        )
    if (
        not isinstance(decision, ProviderCallDecision)
        or not isinstance(decision.state, ProviderAdmissionState)
        or not isinstance(decision.reason_code, str)
        or not decision.reason_code
        or not isinstance(decision.source, ProviderAdmissionDecisionSource)
        or decision.state is not ProviderAdmissionState.GRANTED
    ):
        reason = (
            decision.reason_code
            if isinstance(decision, ProviderCallDecision)
            and isinstance(decision.reason_code, str)
            and decision.reason_code
            else "provider_call_not_admitted"
        )
        state = (
            decision.state
            if isinstance(decision, ProviderCallDecision)
            and isinstance(decision.state, ProviderAdmissionState)
            else ProviderAdmissionState.INDETERMINATE
        )
        source = (
            decision.source
            if isinstance(decision, ProviderCallDecision)
            and isinstance(decision.source, ProviderAdmissionDecisionSource)
            else ProviderAdmissionDecisionSource.LOCAL_POLICY
        )
        raise ProviderAdmissionError(reason, state, source)


class ProviderAdmittedChatSession:
    """Transparent session proxy that checks admission for every send path."""

    __slots__ = ("_inner", "_port")

    def __init__(self, inner: Any, port: ProviderCallAdmissionPort):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_port", port)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    @property
    def interface(self):
        return self._inner.interface

    @property
    def pre_request_hook(self):
        return self._inner.pre_request_hook

    @pre_request_hook.setter
    def pre_request_hook(self, value) -> None:
        self._inner.pre_request_hook = value

    @staticmethod
    def reset_provider_turn_state(session) -> None:
        inner = getattr(session, "_inner", session)
        reset = getattr(type(inner), "reset_provider_turn_state", None)
        if callable(reset):
            reset(inner)

    def send(self, message):
        require_provider_admission(self._port)
        return self._inner.send(message)

    def send_stream(self, message, on_chunk=None):
        require_provider_admission(self._port)
        return self._inner.send_stream(message, on_chunk=on_chunk)


class ProviderAdmittedLLMService:
    """Service proxy ensuring every session and direct generation is gated."""

    __slots__ = ("_inner", "_port")

    def __init__(self, inner: Any, port: ProviderCallAdmissionPort):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_port", port)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create_session(self, *args, **kwargs) -> ProviderAdmittedChatSession:
        return ProviderAdmittedChatSession(
            self._inner.create_session(*args, **kwargs), self._port
        )

    def get_session(self, session_id: str):
        session = self._inner.get_session(session_id)
        return (
            None
            if session is None
            else ProviderAdmittedChatSession(session, self._port)
        )

    def generate(self, *args, **kwargs):
        require_provider_admission(self._port)
        return self._inner.generate(*args, **kwargs)


__all__ = [
    "DerivedProviderAdmission",
    "ProviderAdmittedChatSession",
    "ProviderAdmittedLLMService",
    "DerivedLaunchAdmissionError",
    "DerivedLaunchAdmissionPort",
    "DerivedLaunchCapability",
    "DerivedLaunchDecision",
    "DerivedLaunchEndpointLease",
    "ProviderAdmissionError",
    "ProviderAdmissionParent",
    "ProviderAdmissionState",
    "ProviderCallAdmissionPort",
    "ProviderCallClass",
    "ProviderCallDecision",
    "RootProviderAdmission",
    "begin_derived_provider_admission",
    "bind_provider_admission",
    "clear_provider_admission",
    "clear_current_provider_admission",
    "current_provider_admission",
    "require_derived_launch_admission",
    "require_provider_admission",
]
