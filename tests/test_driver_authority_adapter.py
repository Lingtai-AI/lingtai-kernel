"""Isolated protocol tests for the Driver authority client."""
from __future__ import annotations

import array
import json
import os
import select
import socket
import struct
import threading
import time
from unittest.mock import patch

from lingtai.adapters.acp.driver_authority import (
    DriverAuthorityClient,
    DriverAuthorityTransportError,
    RejectedDriverAuthorityAdapter,
    UnavailableDriverAuthorityAdapter,
    authority_adapter_from_environment,
)
from lingtai.kernel.provider_admission import (
    DerivedLaunchCapability,
    ProviderAdmissionDecisionSource,
    ProviderAdmissionState,
    ProviderCallClass,
    RootProviderAdmission,
    begin_derived_provider_admission,
)


def _recv(sock):
    size = struct.unpack("!I", sock.recv(4))[0]
    body = bytearray()
    while len(body) < size:
        body.extend(sock.recv(size - len(body)))
    return json.loads(body.decode("utf-8"))


def _send(sock, payload, *, fd=None):
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    frame = struct.pack("!I", len(encoded)) + encoded
    if fd is None:
        sock.sendall(frame)
    else:
        sock.sendmsg([frame], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])


def _server(handler):
    client, server = socket.socketpair()
    errors = []
    def run():
        try:
            handler(server)
        except BaseException as exc:  # retained for the caller assertion
            errors.append(exc)
        finally:
            server.close()
    thread = threading.Thread(target=run)
    thread.start()
    return client, thread, errors


def _assert_peer_closed(peer):
    peer.settimeout(2)
    assert peer.recv(1) == b""


def _assert_fd_closed(fd):
    try:
        os.fstat(fd)
    except OSError as exc:
        assert exc.errno == 9  # EBADF
    else:
        raise AssertionError("rejected inherited descriptor remained open")


def _hello(sock, *, role="root", capability=None):
    request = _recv(sock)
    assert request["op"] == "hello"
    assert isinstance(request["call_id"], str) and request["call_id"]
    _send(sock, {"version": 1, "call_id": request["call_id"], "role": role, "launch_id": "launch-1", "capability": capability})


def test_hello_and_provider_request_are_correlated():
    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        assert request["op"] == "authorize_provider_call"
        assert request["capability"] == "root"
        _send(sock, {"version": 1, "call_id": request["call_id"], "state": "granted", "reason_code": "allowed"})
    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.authorize_provider_call(RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT)
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.GRANTED


def test_profile_authority_configuration_consumes_a_root_endpoint(monkeypatch):
    def handler(sock):
        _hello(sock)

    endpoint, thread, errors = _server(handler)
    monkeypatch.setenv("LINGTAI_DRIVER_AUTHORITY_FD", str(endpoint.detach()))

    authority = authority_adapter_from_environment()

    thread.join(2)
    assert not errors
    assert isinstance(authority, DriverAuthorityClient)
    assert authority.identity.role == "root"
    assert "LINGTAI_DRIVER_AUTHORITY_FD" not in os.environ
    authority.close()


def test_profile_authority_configuration_fails_closed_without_a_usable_root_endpoint(monkeypatch):
    monkeypatch.setenv("LINGTAI_DRIVER_AUTHORITY_FD", "not-a-fd")

    authority = authority_adapter_from_environment()

    assert isinstance(authority, UnavailableDriverAuthorityAdapter)
    assert "LINGTAI_DRIVER_AUTHORITY_FD" not in os.environ
    provider = authority.authorize_provider_call(
        RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT
    )
    launch = authority.authorize_derived_launch(
        RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.DAEMON
    )
    assert provider.state is ProviderAdmissionState.INDETERMINATE
    assert launch.state is ProviderAdmissionState.INDETERMINATE


def test_profile_hello_rejection_preserves_the_driver_decision_source(monkeypatch):
    def handler(sock):
        request = _recv(sock)
        assert request["op"] == "hello"
        _send(sock, {
            "version": 1,
            "call_id": request["call_id"],
            "state": "denied",
            "reason_code": "endpoint_already_claimed",
            "audit_id": "audit-hello-1",
        })

    endpoint, thread, errors = _server(handler)
    monkeypatch.setenv("LINGTAI_DRIVER_AUTHORITY_FD", str(endpoint.detach()))

    authority = authority_adapter_from_environment()

    thread.join(2)
    assert not errors
    assert isinstance(authority, RejectedDriverAuthorityAdapter)
    provider = authority.authorize_provider_call(
        RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT
    )
    launch = authority.authorize_derived_launch(
        RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.DAEMON
    )
    assert provider.state is ProviderAdmissionState.DENIED
    assert provider.reason_code == "endpoint_already_claimed"
    assert provider.source is ProviderAdmissionDecisionSource.DRIVER
    assert launch.state is ProviderAdmissionState.DENIED
    assert launch.reason_code == "endpoint_already_claimed"
    assert launch.audit_id == "audit-hello-1"
    assert launch.source is ProviderAdmissionDecisionSource.DRIVER


def test_profile_authority_configuration_closes_rejected_non_socket_descriptors(monkeypatch):
    read_fd, write_fd = os.pipe()
    file_fd = os.open(__file__, os.O_RDONLY)
    try:
        for fd in (read_fd, file_fd):
            os.set_inheritable(fd, True)
            monkeypatch.setenv("LINGTAI_DRIVER_AUTHORITY_FD", str(fd))

            authority = authority_adapter_from_environment()

            assert isinstance(authority, UnavailableDriverAuthorityAdapter)
            assert "LINGTAI_DRIVER_AUTHORITY_FD" not in os.environ
            _assert_fd_closed(fd)
    finally:
        for fd in (read_fd, write_fd, file_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def test_profile_authority_configuration_normalizes_an_out_of_range_fd(monkeypatch):
    monkeypatch.setenv("LINGTAI_DRIVER_AUTHORITY_FD", str(2**63))

    authority = authority_adapter_from_environment()

    assert isinstance(authority, UnavailableDriverAuthorityAdapter)
    assert "LINGTAI_DRIVER_AUTHORITY_FD" not in os.environ


def test_profile_authority_configuration_rejects_a_derived_endpoint(monkeypatch):
    def handler(sock):
        _hello(sock, role="derived", capability="daemon")
        assert sock.recv(1) == b""

    endpoint, thread, errors = _server(handler)
    monkeypatch.setenv("LINGTAI_DRIVER_AUTHORITY_FD", str(endpoint.detach()))

    authority = authority_adapter_from_environment()

    thread.join(2)
    assert not errors
    assert isinstance(authority, UnavailableDriverAuthorityAdapter)


def test_mismatched_call_id_closes_received_endpoint_and_fails_closed():
    peer, driver_end = socket.socketpair()
    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        _send(sock, {"version": 1, "call_id": "stale", "state": "granted", "reason_code": "allowed"}, fd=driver_end.fileno())
        driver_end.close()
    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.request_derived_launch(RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.DAEMON)
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.INDETERMINATE
    _assert_peer_closed(peer)
    peer.close()


def test_malformed_derived_decision_closes_received_endpoint_and_fails_closed():
    peer, driver_end = socket.socketpair()

    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        _send(sock, {
            "version": 1, "call_id": request["call_id"], "state": "granted",
            "reason_code": "",  # invalid decision field
        }, fd=driver_end.fileno())
        driver_end.close()

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.request_derived_launch(RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.DAEMON)
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.INDETERMINATE
    _assert_peer_closed(peer)
    peer.close()


def test_granted_launch_has_one_linear_inheritable_false_lease():
    child, driver_end = socket.socketpair()
    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        _send(sock, {"version": 1, "call_id": request["call_id"], "state": "granted", "reason_code": "allowed", "audit_id": "audit-1"}, fd=driver_end.fileno())
        driver_end.close()
    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    grant = client.request_derived_launch(RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.AVATAR)
    thread.join(2)
    assert not errors and grant.state is ProviderAdmissionState.GRANTED
    fd = grant.child_endpoint_lease.consume_for_posix_spawn()
    try:
        assert os.get_inheritable(fd) is False
    finally:
        os.close(fd); child.close()
    try:
        grant.child_endpoint_lease.consume_for_posix_spawn()
    except DriverAuthorityTransportError:
        pass
    else:
        raise AssertionError("lease was reusable")


def test_detach_failure_leaves_lease_closable():
    endpoint, peer = socket.socketpair()
    from lingtai.adapters.acp.driver_authority import DriverChildEndpointLease

    lease = DriverChildEndpointLease(endpoint)
    with patch.object(socket.socket, "detach", side_effect=OSError("injected detach failure")):
        try:
            lease.consume_for_posix_spawn()
        except DriverAuthorityTransportError:
            pass
        else:
            raise AssertionError("detach failure did not surface")

    lease.close()
    ready, _, _ = select.select([peer], [], [], 0.2)
    assert ready == [peer]
    _assert_peer_closed(peer)
    peer.close()


def test_truncated_ancillary_data_closes_every_delivered_descriptor():
    from lingtai.adapters.acp import driver_authority as authority_module

    frame = struct.pack("!I", len(b'{"version":1}')) + b'{"version":1}'
    delivered_fd = 731
    client = object.__new__(DriverAuthorityClient)
    client._socket = type("FakeSocket", (), {
        "recvmsg": lambda _self, *_args: (frame, [(
            socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [delivered_fd]).tobytes(),
        )], socket.MSG_CTRUNC, None),
    })()
    client._buffer = bytearray()
    with patch.object(authority_module.os, "close") as close:
        try:
            client._recv_frame()
        except DriverAuthorityTransportError as exc:
            assert "ancillary data was truncated" in str(exc)
        else:
            raise AssertionError("truncated ancillary data was accepted")
    close.assert_called_once_with(delivered_fd)


def test_denied_child_endpoint_is_closed_without_erasing_driver_reason():
    peer, driver_end = socket.socketpair()

    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        _send(sock, {
            "version": 1, "call_id": request["call_id"], "state": "denied",
            "reason_code": "endpoint_already_claimed", "audit_id": "audit-1",
        }, fd=driver_end.fileno())
        driver_end.close()

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.request_derived_launch(RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.AVATAR)
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.DENIED
    assert decision.reason_code == "endpoint_already_claimed"
    assert decision.audit_id == "audit-1"
    assert decision.source is ProviderAdmissionDecisionSource.DRIVER
    _assert_peer_closed(peer)
    peer.close()


def test_unconnected_unix_endpoint_is_closed_by_the_grant_parser():
    endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    fd = endpoint.detach()
    try:
        DriverAuthorityClient._checked_endpoint(fd)
    except DriverAuthorityTransportError:
        pass
    else:
        raise AssertionError("unconnected endpoint became a grant")
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        os.close(fd)
        raise AssertionError("rejected endpoint descriptor leaked")


def test_grant_parser_closes_its_socket_wrapper_after_descriptor_adoption():
    endpoint, peer = socket.socketpair()
    fd = endpoint.detach()
    with patch.object(os, "set_inheritable", side_effect=OSError("injected inheritable failure")):
        try:
            DriverAuthorityClient._checked_endpoint(fd)
        except DriverAuthorityTransportError:
            pass
        else:
            raise AssertionError("invalid adopted endpoint became a grant")
    _assert_peer_closed(peer)
    peer.close()


def test_derived_endpoint_cannot_mint_a_second_child_even_with_a_socket():
    def handler(sock):
        _hello(sock, role="derived", capability="daemon")

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.request_derived_launch(RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.DAEMON)
    client.close()
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.DENIED
    assert decision.reason_code == "nested_derived_launch_denied"
    assert decision.source is ProviderAdmissionDecisionSource.LOCAL_POLICY


def test_nested_derived_launch_denial_uses_source_not_reason_code_for_origin():
    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        assert request["op"] == "authorize_derived_launch"
        _send(sock, {
            "version": 1,
            "call_id": request["call_id"],
            "state": "denied",
            "reason_code": "nested_derived_launch_denied",
        })

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.request_derived_launch(
        RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.DAEMON
    )
    client.close()
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.DENIED
    assert decision.reason_code == "nested_derived_launch_denied"
    assert decision.source is ProviderAdmissionDecisionSource.DRIVER


def test_unknown_driver_reason_is_downgraded_without_becoming_a_local_transport_failure():
    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        _send(sock, {
            "version": 1,
            "call_id": request["call_id"],
            "state": "denied",
            "reason_code": "future_driver_policy_denial",
        })

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.authorize_provider_call(
        RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT
    )
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.INDETERMINATE
    assert decision.reason_code == "unknown_denial"
    assert decision.source is ProviderAdmissionDecisionSource.DRIVER


def test_transport_failure_is_distinguished_from_a_driver_denial():
    def handler(sock):
        _hello(sock)
        _recv(sock)

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.authorize_provider_call(
        RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT
    )
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.INDETERMINATE
    assert decision.reason_code == "driver_authority_unavailable"
    assert decision.source is ProviderAdmissionDecisionSource.TRANSPORT


def test_local_child_mode_must_match_driver_endpoint_capability():
    def handler(sock):
        _hello(sock, role="derived", capability="daemon")

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    try:
        client.derived_provider_parent(ProviderCallClass.AVATAR_CHILD)
    except DriverAuthorityTransportError:
        pass
    else:
        raise AssertionError("wrong derived endpoint capability was accepted")
    client.close()
    thread.join(2)
    assert not errors


def test_derived_provider_call_uses_its_own_endpoint_and_driver_known_fields():
    def handler(sock):
        _hello(sock, role="derived", capability="daemon")
        request = _recv(sock)
        assert request["op"] == "authorize_provider_call"
        assert request["launch_id"] == "launch-1"
        assert request["provider"] == "llm"
        assert request["capability"] == "daemon"
        _send(sock, {"version": 1, "call_id": request["call_id"], "state": "granted", "reason_code": "allowed"})

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.authorize_provider_call(client.derived_provider_parent(), ProviderCallClass.DAEMON)
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.GRANTED


def test_daemon_endpoint_cannot_admit_avatar_child_call_class():
    def handler(sock):
        _hello(sock, role="derived", capability="daemon")
        ready, _, _ = select.select([sock], [], [], 0.2)
        if ready:
            request = _recv(sock)
            _send(sock, {
                "version": 1, "call_id": request["call_id"], "state": "granted",
                "reason_code": "unexpected_avatar_child_grant",
            })

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    avatar_parent = begin_derived_provider_admission(
        RootProviderAdmission("turn", "v1"), ProviderCallClass.AVATAR_CHILD,
    )
    decision = client.authorize_provider_call(avatar_parent, ProviderCallClass.AVATAR_CHILD)
    thread.join(2)
    client.close()
    assert not errors
    assert decision.state is ProviderAdmissionState.DENIED
    assert decision.reason_code == "provider_parent_endpoint_mismatch"


def test_denied_endpoint_reply_invalidates_authority_before_a_second_request():
    peer, denied_driver_end = socket.socketpair()
    granted_peer, granted_driver_end = socket.socketpair()

    def handler(sock):
        _hello(sock)
        first = _recv(sock)
        _send(sock, {
            "version": 1, "call_id": first["call_id"], "state": "denied",
            "reason_code": "endpoint_already_claimed",
        }, fd=denied_driver_end.fileno())
        denied_driver_end.close()
        try:
            second = _recv(sock)
        except struct.error:
            return
        _send(sock, {
            "version": 1, "call_id": second["call_id"], "state": "granted",
            "reason_code": "unexpected_second_grant",
        }, fd=granted_driver_end.fileno())
        granted_driver_end.close()

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    first = client.request_derived_launch(RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.DAEMON)
    second = client.request_derived_launch(RootProviderAdmission("turn", "v1"), DerivedLaunchCapability.AVATAR)
    thread.join(2)
    assert not errors
    assert first.state is ProviderAdmissionState.DENIED
    assert first.source is ProviderAdmissionDecisionSource.DRIVER
    assert second.state is ProviderAdmissionState.INDETERMINATE
    _assert_peer_closed(peer)
    peer.close()
    granted_peer.close()


def test_boolean_protocol_version_cannot_construct_client():
    def handler(sock):
        request = _recv(sock)
        _send(sock, {
            "version": True, "call_id": request["call_id"], "role": "root",
            "launch_id": "launch-1", "capability": None,
        })

    endpoint, thread, errors = _server(handler)
    try:
        DriverAuthorityClient(endpoint)
    except DriverAuthorityTransportError:
        pass
    else:
        raise AssertionError("boolean protocol version constructed a client")
    thread.join(2)
    assert not errors


def test_malformed_driver_provider_reply_fails_closed_before_provider_io():
    def handler(sock):
        _hello(sock)
        _recv(sock)
        payload = b'{"version":1,"state":"granted","reason_code":""}'
        sock.sendall(struct.pack("!I", len(payload)) + payload)

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.authorize_provider_call(RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT)
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.INDETERMINATE


def test_provider_reply_with_an_endpoint_fails_closed_and_closes_it():
    peer, driver_end = socket.socketpair()

    def handler(sock):
        _hello(sock)
        request = _recv(sock)
        _send(sock, {"version": 1, "call_id": request["call_id"], "state": "granted", "reason_code": "allowed"}, fd=driver_end.fileno())
        driver_end.close()

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint)
    decision = client.authorize_provider_call(RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT)
    thread.join(2)
    assert not errors
    assert decision.state is ProviderAdmissionState.INDETERMINATE
    _assert_peer_closed(peer)
    peer.close()


def test_closed_truncated_or_timed_out_driver_reply_is_indeterminate():
    for failure in ("closed", "truncated", "timeout"):
        def handler(sock, failure=failure):
            _hello(sock)
            _recv(sock)
            if failure == "truncated":
                sock.sendall(struct.pack("!I", 12) + b"{}")
            elif failure == "timeout":
                time.sleep(0.05)

        endpoint, thread, errors = _server(handler)
        client = DriverAuthorityClient(endpoint, timeout=0.01)
        decision = client.authorize_provider_call(RootProviderAdmission("turn", "v1"), ProviderCallClass.ROOT)
        thread.join(2)
        assert not errors
        assert decision.state is ProviderAdmissionState.INDETERMINATE


def test_timeout_invalidates_endpoint_so_late_grant_cannot_admit_next_call():
    def handler(sock):
        _hello(sock)
        first = _recv(sock)
        assert first["op"] == "authorize_provider_call"
        time.sleep(0.03)
        try:
            _send(sock, {"version": 1, "call_id": first["call_id"], "state": "granted", "reason_code": "allowed"})
            _recv(sock)
        except OSError:
            pass

    endpoint, thread, errors = _server(handler)
    client = DriverAuthorityClient(endpoint, timeout=0.02)
    parent = RootProviderAdmission("turn", "v1")
    first = client.authorize_provider_call(parent, ProviderCallClass.ROOT)
    second = client.authorize_provider_call(parent, ProviderCallClass.ROOT)
    client.close()
    thread.join(2)
    assert not errors
    assert first.state is ProviderAdmissionState.INDETERMINATE
    assert second.state is ProviderAdmissionState.INDETERMINATE


def test_bad_driver_handshake_is_not_a_client():
    def handler(sock):
        request = _recv(sock)
        _send(sock, {"version": 2, "call_id": request["call_id"], "role": "root", "launch_id": "launch-1", "capability": None})

    endpoint, thread, errors = _server(handler)
    try:
        DriverAuthorityClient(endpoint)
    except DriverAuthorityTransportError:
        pass
    else:
        raise AssertionError("invalid handshake constructed a client")
    thread.join(2)
    assert not errors
