"""Narrow POSIX client for Puffo Driver admission authority.

This module deliberately owns only the wire protocol and linear endpoint
leases.  It does not start daemons, avatars, managers, supervisors, or an ACP
profile; those are separate Kernel consumer layers.
"""
from __future__ import annotations

import array
import json
import os
import socket
import struct
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from lingtai.kernel.provider_admission import (
    DerivedLaunchCapability,
    DerivedLaunchDecision,
    DerivedProviderAdmission,
    ProviderAdmissionParent,
    ProviderAdmissionDecisionSource,
    ProviderAdmissionState,
    ProviderCallAdmissionPort,
    ProviderCallClass,
    ProviderCallDecision,
    RootProviderAdmission,
    begin_derived_provider_admission,
)


DRIVER_AUTHORITY_FD_ENV = "LINGTAI_DRIVER_AUTHORITY_FD"
_PROTOCOL_VERSION = 1
_MAX_FRAME_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 2.0
_DRIVER_DECISION_STATES = {
    "allowed": ProviderAdmissionState.GRANTED,
    "endpoint_already_claimed": ProviderAdmissionState.DENIED,
    "endpoint_binding_mismatch": ProviderAdmissionState.DENIED,
    "nested_derived_launch_denied": ProviderAdmissionState.DENIED,
    "malformed_request": ProviderAdmissionState.INDETERMINATE,
    "unsupported_operation": ProviderAdmissionState.INDETERMINATE,
}
_UNKNOWN_DENIAL = "unknown_denial"


class DriverAuthorityTransportError(RuntimeError):
    """A malformed, stale, or unavailable Driver exchange."""


class DriverAuthorityEndpointBindingMismatch(DriverAuthorityTransportError):
    """The Driver endpoint role cannot serve the local execution mode."""


class DriverAuthorityHelloRejected(RuntimeError):
    """The Driver rejected the endpoint before it could identify its role."""

    def __init__(
        self,
        state: ProviderAdmissionState,
        reason_code: str,
        audit_id: str | None,
    ) -> None:
        self.state = state
        self.reason_code = reason_code
        self.audit_id = audit_id
        super().__init__(f"driver authority hello was rejected: {reason_code}")


@dataclass(frozen=True, slots=True)
class DriverAuthorityIdentity:
    role: str
    launch_id: str
    capability: str | None


class DriverChildEndpointLease:
    """One-use ownership of a Driver-created AF_UNIX child endpoint."""

    __slots__ = ("_socket", "_consumed")

    def __init__(self, endpoint: socket.socket) -> None:
        self._socket = endpoint
        self._consumed = False

    def consume_for_posix_spawn(self) -> int:
        if self._consumed:
            raise DriverAuthorityTransportError("child endpoint lease already consumed")
        try:
            endpoint_fd = self._socket.detach()
        except OSError as exc:
            raise DriverAuthorityTransportError("child endpoint lease unavailable") from exc
        self._consumed = True
        return endpoint_fd

    def close(self) -> None:
        if self._consumed:
            return
        self._consumed = True
        try:
            self._socket.close()
        except OSError:
            pass

    def __del__(self) -> None:
        self.close()


def consume_posix_child_endpoint_lease(lease: object) -> int:
    """Transfer the one Driver child endpoint into the POSIX spawn adapter.

    The opaque Core field may only be interpreted at this concrete adapter
    boundary.  Rejecting another object here keeps the Driver handoff format
    out of Avatar/Core while preserving the lease's one-use semantics.
    """
    if not isinstance(lease, DriverChildEndpointLease):
        raise TypeError("expected a DriverChildEndpointLease for POSIX spawn")
    return lease.consume_for_posix_spawn()


@dataclass(frozen=True, slots=True)
class DriverDerivedLaunchGrant:
    """A Driver result kept private until a later Kernel consumer owns it."""

    state: ProviderAdmissionState
    reason_code: str
    audit_id: str | None = None
    child_endpoint_lease: DriverChildEndpointLease | None = None
    source: ProviderAdmissionDecisionSource = ProviderAdmissionDecisionSource.LOCAL_POLICY


class DriverDerivedLaunchAdmissionAdapter:
    """Project one Driver grant into Core's derived-launch decision port.

    This adapter owns no daemon, manager, supervisor, or child startup.  It
    only preserves the Driver's opaque one-use lease alongside the typed Core
    decision until a later consumer can either hand it off or close it.
    """

    __slots__ = ("_authority",)

    def __init__(self, authority: "DriverAuthorityClient") -> None:
        self._authority = authority

    def authorize_derived_launch(
        self,
        parent: RootProviderAdmission,
        capability: DerivedLaunchCapability,
    ) -> DerivedLaunchDecision:
        grant = self._authority.request_derived_launch(parent, capability)
        return DerivedLaunchDecision(
            grant.state,
            grant.reason_code,
            audit_id=grant.audit_id,
            source=grant.source,
            child_endpoint_lease=grant.child_endpoint_lease,
        )


class UnavailableDriverAuthorityAdapter:
    """Fail-closed Port pair used when constrained composition lacks authority.

    This is deliberately a composition result, not a fallback policy.  A
    ``puffo-v0`` process without a usable inherited Driver endpoint must retain
    its provider and derived-launch gates rather than reverting to generic
    LingTai behavior.
    """

    __slots__ = ()

    def authorize_provider_call(
        self,
        _parent: ProviderAdmissionParent,
        _call_class: ProviderCallClass,
    ) -> ProviderCallDecision:
        return ProviderCallDecision(
            ProviderAdmissionState.INDETERMINATE,
            "driver_authority_unavailable",
            ProviderAdmissionDecisionSource.TRANSPORT,
        )

    def authorize_derived_launch(
        self,
        _parent: RootProviderAdmission,
        _capability: DerivedLaunchCapability,
    ) -> DerivedLaunchDecision:
        return DerivedLaunchDecision(
            ProviderAdmissionState.INDETERMINATE,
            "driver_authority_unavailable",
            source=ProviderAdmissionDecisionSource.TRANSPORT,
        )


class RejectedDriverAuthorityAdapter:
    """Fail-closed Port pair preserving a Driver hello rejection.

    A Driver can reject an inherited endpoint before returning its identity
    (for example, because that endpoint was already claimed).  There is no
    usable stream to retain in that case, but the policy response must remain
    distinguishable from an unavailable transport at both Core boundaries.
    """

    __slots__ = ("_state", "_reason_code", "_audit_id")

    def __init__(
        self,
        state: ProviderAdmissionState,
        reason_code: str,
        audit_id: str | None,
    ) -> None:
        self._state = state
        self._reason_code = reason_code
        self._audit_id = audit_id

    def authorize_provider_call(
        self,
        _parent: ProviderAdmissionParent,
        _call_class: ProviderCallClass,
    ) -> ProviderCallDecision:
        return ProviderCallDecision(
            self._state,
            self._reason_code,
            ProviderAdmissionDecisionSource.DRIVER,
        )

    def authorize_derived_launch(
        self,
        _parent: RootProviderAdmission,
        _capability: DerivedLaunchCapability,
    ) -> DerivedLaunchDecision:
        return DerivedLaunchDecision(
            self._state,
            self._reason_code,
            audit_id=self._audit_id,
            source=ProviderAdmissionDecisionSource.DRIVER,
        )


class DriverAuthorityClient(ProviderCallAdmissionPort):
    """Authenticated request/response client; no lifecycle integration."""

    __slots__ = ("_socket", "_identity", "_lock", "_timeout", "_buffer")

    def __init__(self, endpoint: socket.socket, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS):
        if endpoint.family != socket.AF_UNIX or (endpoint.type & socket.SOCK_STREAM) != socket.SOCK_STREAM:
            endpoint.close()
            raise DriverAuthorityTransportError("authority endpoint must be an AF_UNIX stream socket")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            endpoint.close()
            raise DriverAuthorityTransportError("authority timeout must be positive")
        self._socket = endpoint
        self._timeout = float(timeout)
        self._lock = threading.Lock()
        self._buffer = bytearray()
        try:
            os.set_inheritable(endpoint.fileno(), False)
            endpoint.settimeout(self._timeout)
            response, received_fd = self._exchange({"op": "hello", "call_id": self._call_id()}, expect_fd=False)
            if received_fd is not None:
                os.close(received_fd)
                raise DriverAuthorityTransportError("hello must not receive a child endpoint")
            if "state" in response or "reason_code" in response:
                state, reason_code, audit_id = self._decision(response)
                if state is ProviderAdmissionState.GRANTED:
                    raise DriverAuthorityTransportError("granted hello must identify an authority endpoint")
                raise DriverAuthorityHelloRejected(state, reason_code, audit_id)
            self._identity = self._parse_identity(response)
        except Exception:
            self._close_locked()
            raise

    @classmethod
    def from_inherited_fd(cls, fd: int, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> "DriverAuthorityClient":
        if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
            raise DriverAuthorityTransportError("authority fd is invalid")
        endpoint: socket.socket | None = None
        try:
            endpoint = socket.socket(fileno=fd)
            return cls(endpoint, timeout=timeout)
        except (OSError, OverflowError) as exc:
            # ``fileno=`` can reject a live non-socket descriptor before a
            # socket object owns it.  This is still an inherited authority
            # locator, so consume it on every rejected path rather than
            # leaving it open (or inheritable) for a later child.
            try:
                if endpoint is None:
                    os.close(fd)
                else:
                    endpoint.close()
            except (OSError, OverflowError):
                pass
            raise DriverAuthorityTransportError("authority fd is unavailable") from exc

    @property
    def identity(self) -> DriverAuthorityIdentity:
        return self._identity

    def derived_provider_parent(self, expected_call_class: ProviderCallClass | None = None) -> DerivedProviderAdmission:
        if self._identity.role != "derived" or self._identity.capability is None:
            raise DriverAuthorityTransportError("root endpoint cannot become a derived parent")
        capability = DerivedLaunchCapability(self._identity.capability)
        call_class = ProviderCallClass.DAEMON if capability is DerivedLaunchCapability.DAEMON else ProviderCallClass.AVATAR_CHILD
        if expected_call_class is not None and call_class is not expected_call_class:
            raise DriverAuthorityEndpointBindingMismatch("authority endpoint capability does not match local child mode")
        return begin_derived_provider_admission(
            RootProviderAdmission(f"driver-launch:{self._identity.launch_id}", "driver-authority.v1"), call_class
        )

    def authorize_provider_call(self, parent: ProviderAdmissionParent, call_class: ProviderCallClass) -> ProviderCallDecision:
        if not self._valid_provider_parent(parent, call_class):
            return ProviderCallDecision(
                ProviderAdmissionState.DENIED,
                "provider_parent_endpoint_mismatch",
                ProviderAdmissionDecisionSource.LOCAL_POLICY,
            )
        with self._lock:
            try:
                response, received_fd = self._exchange({
                    "op": "authorize_provider_call", "call_id": self._call_id(),
                    "launch_id": self._identity.launch_id, "provider": "llm", "capability": call_class.value,
                }, expect_fd=False)
                if received_fd is not None:
                    os.close(received_fd)
                    raise DriverAuthorityTransportError("provider decision must not return an endpoint")
                state, reason, _audit = self._decision(response)
                return ProviderCallDecision(state, reason, ProviderAdmissionDecisionSource.DRIVER)
            except DriverAuthorityTransportError:
                self._close_locked()
                return ProviderCallDecision(
                    ProviderAdmissionState.INDETERMINATE,
                    "driver_authority_unavailable",
                    ProviderAdmissionDecisionSource.TRANSPORT,
                )

    def request_derived_launch(self, parent: RootProviderAdmission, capability: DerivedLaunchCapability) -> DriverDerivedLaunchGrant:
        if not isinstance(parent, RootProviderAdmission) or self._identity.role != "root":
            return DriverDerivedLaunchGrant(
                ProviderAdmissionState.DENIED,
                "nested_derived_launch_denied",
                source=ProviderAdmissionDecisionSource.LOCAL_POLICY,
            )
        if not isinstance(capability, DerivedLaunchCapability):
            return DriverDerivedLaunchGrant(
                ProviderAdmissionState.INDETERMINATE,
                "driver_authority_unavailable",
                source=ProviderAdmissionDecisionSource.LOCAL_POLICY,
            )
        with self._lock:
            received_fd: int | None = None
            try:
                response, received_fd = self._exchange({
                    "op": "authorize_derived_launch", "call_id": self._call_id(),
                    "launch_id": self._identity.launch_id, "capability": capability.value,
                }, expect_fd=None)
                state, reason, audit_id = self._decision(response)
                if state is not ProviderAdmissionState.GRANTED:
                    if received_fd is not None:
                        # The policy outcome is authoritative even if the
                        # response also violates endpoint framing. Release the
                        # unexpected descriptor without erasing its reason or
                        # audit evidence.
                        try:
                            os.close(received_fd)
                        except OSError:
                            pass
                        received_fd = None
                        # A non-grant still retains its policy evidence, but
                        # an endpoint on that frame violates framing. Retire
                        # the authority stream before any later request.
                        self._close_locked()
                    return DriverDerivedLaunchGrant(
                        state,
                        reason,
                        audit_id,
                        source=ProviderAdmissionDecisionSource.DRIVER,
                    )
                if received_fd is None:
                    raise DriverAuthorityTransportError("granted launch omitted child endpoint")
                # _checked_endpoint takes ownership of the descriptor, whether
                # validation succeeds or fails. Clear this cleanup handle first
                # so an error cannot close a recycled descriptor a second time.
                endpoint_fd, received_fd = received_fd, None
                endpoint = self._checked_endpoint(endpoint_fd)
                return DriverDerivedLaunchGrant(
                    state,
                    reason,
                    audit_id,
                    DriverChildEndpointLease(endpoint),
                    ProviderAdmissionDecisionSource.DRIVER,
                )
            except DriverAuthorityTransportError:
                if received_fd is not None:
                    try:
                        os.close(received_fd)
                    except OSError:
                        pass
                self._close_locked()
                return DriverDerivedLaunchGrant(
                    ProviderAdmissionState.INDETERMINATE,
                    "driver_authority_unavailable",
                    source=ProviderAdmissionDecisionSource.TRANSPORT,
                )

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _exchange(self, request: dict[str, Any], *, expect_fd: bool | None) -> tuple[dict[str, Any], int | None]:
        call_id = request.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            raise DriverAuthorityTransportError("authority request requires call_id")
        self._send_frame({"version": _PROTOCOL_VERSION, **request})
        response, received_fd = self._recv_frame()
        if response.get("call_id") != call_id:
            if received_fd is not None:
                os.close(received_fd)
            raise DriverAuthorityTransportError("authority response call_id does not match request")
        if expect_fd is not None and expect_fd != (received_fd is not None):
            if received_fd is not None:
                os.close(received_fd)
            raise DriverAuthorityTransportError("authority response carried an unexpected endpoint")
        return response, received_fd

    @staticmethod
    def _call_id() -> str:
        return uuid.uuid4().hex

    def _send_frame(self, payload: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            if len(encoded) > _MAX_FRAME_BYTES:
                raise ValueError
            self._socket.sendall(struct.pack("!I", len(encoded)) + encoded)
        except (OSError, TimeoutError, TypeError, ValueError) as exc:
            raise DriverAuthorityTransportError("authority request transport failed") from exc

    def _recv_frame(self) -> tuple[dict[str, Any], int | None]:
        fds = array.array("i")
        try:
            def read_exact(count: int) -> bytes:
                while len(self._buffer) < count:
                    data, ancdata, flags, _ = self._socket.recvmsg(_MAX_FRAME_BYTES + 4, socket.CMSG_SPACE(fds.itemsize))
                    for level, kind, payload in ancdata:
                        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                            fds.frombytes(payload[:len(payload) - len(payload) % fds.itemsize])
                    if not data:
                        raise DriverAuthorityTransportError("authority peer closed")
                    if flags & socket.MSG_CTRUNC:
                        raise DriverAuthorityTransportError("authority ancillary data was truncated")
                    self._buffer.extend(data)
                value = bytes(self._buffer[:count]); del self._buffer[:count]
                return value
            size = struct.unpack("!I", read_exact(4))[0]
            if size <= 0 or size > _MAX_FRAME_BYTES:
                raise DriverAuthorityTransportError("authority response frame is out of bounds")
            response = json.loads(read_exact(size).decode("utf-8"))
            if not isinstance(response, dict) or len(fds) > 1:
                raise DriverAuthorityTransportError("authority response is malformed")
            return response, fds[0] if fds else None
        except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, struct.error, DriverAuthorityTransportError) as exc:
            for fd in fds:
                try: os.close(fd)
                except OSError: pass
            if isinstance(exc, DriverAuthorityTransportError):
                raise
            raise DriverAuthorityTransportError("authority response transport failed") from exc

    def _close_locked(self) -> None:
        self._buffer.clear()
        try: self._socket.close()
        except OSError: pass

    @staticmethod
    def _parse_identity(response: dict[str, Any]) -> DriverAuthorityIdentity:
        if not DriverAuthorityClient._has_protocol_version(response):
            raise DriverAuthorityTransportError("authority protocol version mismatch")
        role, launch_id, capability = response.get("role"), response.get("launch_id"), response.get("capability")
        if role not in {"root", "derived"} or not isinstance(launch_id, str) or not launch_id:
            raise DriverAuthorityTransportError("authority hello identity is invalid")
        if capability is not None and capability not in {item.value for item in DerivedLaunchCapability}:
            raise DriverAuthorityTransportError("authority hello capability is invalid")
        if (role == "root") != (capability is None):
            raise DriverAuthorityTransportError("authority hello role/capability mismatch")
        return DriverAuthorityIdentity(role, launch_id, capability)

    @staticmethod
    def _decision(response: dict[str, Any]) -> tuple[ProviderAdmissionState, str, str | None]:
        if not DriverAuthorityClient._has_protocol_version(response):
            raise DriverAuthorityTransportError("authority protocol version mismatch")
        try: state = ProviderAdmissionState(response.get("state"))
        except (TypeError, ValueError) as exc: raise DriverAuthorityTransportError("authority decision state is invalid") from exc
        reason, audit_id = response.get("reason_code"), response.get("audit_id")
        if not isinstance(reason, str) or not reason or (audit_id is not None and (not isinstance(audit_id, str) or not audit_id)):
            raise DriverAuthorityTransportError("authority decision fields are invalid")
        expected_state = _DRIVER_DECISION_STATES.get(reason)
        if expected_state is None or state is not expected_state:
            return ProviderAdmissionState.INDETERMINATE, _UNKNOWN_DENIAL, audit_id
        return state, reason, audit_id

    @staticmethod
    def _has_protocol_version(response: dict[str, Any]) -> bool:
        version = response.get("version")
        return isinstance(version, int) and not isinstance(version, bool) and version == _PROTOCOL_VERSION

    @staticmethod
    def _checked_endpoint(fd: int) -> socket.socket:
        endpoint: socket.socket | None = None
        try:
            endpoint = socket.socket(fileno=fd)
            if endpoint.family != socket.AF_UNIX or (endpoint.type & socket.SOCK_STREAM) != socket.SOCK_STREAM:
                raise OSError
            endpoint.getpeername(); os.set_inheritable(endpoint.fileno(), False)
            return endpoint
        except OSError as exc:
            try:
                if endpoint is None: os.close(fd)
                else: endpoint.close()
            except OSError: pass
            raise DriverAuthorityTransportError("granted child endpoint is invalid") from exc

    def _valid_provider_parent(self, parent: ProviderAdmissionParent, call_class: ProviderCallClass) -> bool:
        if not isinstance(call_class, ProviderCallClass): return False
        if self._identity.role == "root": return isinstance(parent, RootProviderAdmission) and call_class is ProviderCallClass.ROOT
        if not isinstance(parent, DerivedProviderAdmission) or parent.call_class is not call_class:
            return False
        endpoint_capability = DerivedLaunchCapability(self._identity.capability)
        endpoint_call_class = ProviderCallClass.DAEMON if endpoint_capability is DerivedLaunchCapability.DAEMON else ProviderCallClass.AVATAR_CHILD
        return call_class is endpoint_call_class


def authority_adapter_from_environment(
    *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> DriverAuthorityClient | RejectedDriverAuthorityAdapter | UnavailableDriverAuthorityAdapter:
    """Consume the one inherited Driver FD for constrained ACP composition.

    The environment value is merely a one-time local descriptor locator.  It
    is removed before Agent construction so a child process cannot rediscover
    authority from inherited configuration.  Any absent, malformed, unusable,
    or derived-role endpoint becomes a typed fail-closed Port pair.
    """

    raw_fd = os.environ.pop(DRIVER_AUTHORITY_FD_ENV, None)
    if raw_fd is None:
        return UnavailableDriverAuthorityAdapter()
    try:
        fd = int(raw_fd)
        authority = DriverAuthorityClient.from_inherited_fd(fd, timeout=timeout)
        if authority.identity.role != "root":
            authority.close()
            raise DriverAuthorityTransportError("ACP profile requires a root authority endpoint")
        return authority
    except DriverAuthorityHelloRejected as exc:
        return RejectedDriverAuthorityAdapter(exc.state, exc.reason_code, exc.audit_id)
    except (TypeError, ValueError, OverflowError, DriverAuthorityTransportError):
        return UnavailableDriverAuthorityAdapter()


__all__ = [
    "DRIVER_AUTHORITY_FD_ENV",
    "DriverAuthorityClient",
    "DriverAuthorityEndpointBindingMismatch",
    "DriverAuthorityHelloRejected",
    "DriverAuthorityIdentity",
    "DriverAuthorityTransportError",
    "DriverChildEndpointLease",
    "consume_posix_child_endpoint_lease",
    "DriverDerivedLaunchAdmissionAdapter",
    "DriverDerivedLaunchGrant",
    "RejectedDriverAuthorityAdapter",
    "UnavailableDriverAuthorityAdapter",
    "authority_adapter_from_environment",
]
