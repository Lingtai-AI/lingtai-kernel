"""Shared contract and production-composition tests for mail transport.

The Core-owned Port is ``lingtai.kernel.mail_transport.MailTransportPort``. Its
only production adapter is
``lingtai.adapters.posix.mail.PosixFilesystemMailAdapter``. These tests exercise
the *observable Port semantics* (``send``/``listen``/``stop``/``address``, string
error/result behavior) against both the production adapter and an independent
in-memory fake, and prove the architecture: Core never names the concrete
adapter, and the composition root injects it.

POSIX-mechanism specifics (handshake strings, atomic ``message.json`` write,
attachment copy, pseudo-agent outbox claim/rollback, 0.5s polling, probe ack)
are characterized in ``test_filesystem_mail.py`` and ``test_services_mail.py``;
this module does not duplicate them.
"""
from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

import pytest

from lingtai.kernel.mail_transport import MailTransportPort
from lingtai.adapters.posix.mail import PosixFilesystemMailAdapter

from ._agent_dir_helpers import make_agent_dir


# --------------------------------------------------------------------------
# An independent in-memory fake that implements the same Port. It proves the
# Port is substitutable and that consumers depend only on the Port surface.
# --------------------------------------------------------------------------


class FakeMailTransport(MailTransportPort):
    """In-memory transport: a shared address book of inbox lists.

    ``send`` appends to the recipient's inbox and (if that recipient is
    listening) dispatches synchronously. Unknown address → error string, mirroring
    the Port's ``str | None`` result contract without any filesystem.
    """

    def __init__(self, address: str, registry: dict[str, "FakeMailTransport"]):
        self._address = address
        self._registry = registry
        self._on_message = None
        self._stopped = False
        self._inbox: list[dict] = []
        registry[address] = self

    @property
    def address(self) -> str:
        return self._address

    def send(self, address: str, message: dict, *, mode: str = "peer") -> str | None:
        target = self._registry.get(address)
        if target is None:
            return f"No agent at {address}"
        delivered = {**message, "_mailbox_id": f"fake-{len(target._inbox)}"}
        target._inbox.append(delivered)
        if target._on_message is not None:
            target._on_message(delivered)
        return None

    def listen(self, on_message) -> None:
        self._on_message = on_message

    def stop(self) -> None:
        self._stopped = True
        self._on_message = None


def _posix_pair(base: Path):
    """Build a (sender, listener) production-adapter pair with fresh heartbeats."""
    sender_dir = make_agent_dir(base, "sender", mailbox=True)
    receiver_dir = make_agent_dir(base, "receiver", mailbox=True)
    stop = threading.Event()

    def _hb():
        hb = receiver_dir / ".agent.heartbeat"
        while not stop.is_set():
            try:
                hb.write_text(str(time.time()))
            except OSError:
                pass
            stop.wait(0.25)

    threading.Thread(target=_hb, daemon=True).start()
    sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
    listener = PosixFilesystemMailAdapter(working_dir=receiver_dir)
    return sender, listener, str(receiver_dir), stop


# --------------------------------------------------------------------------
# Port surface — technology-neutral, no filesystem vocabulary.
# --------------------------------------------------------------------------


def test_port_exposes_only_send_listen_stop_address():
    # The abstract surface is exactly the four observable operations.
    assert MailTransportPort.__abstractmethods__ == frozenset(
        {"send", "listen", "stop", "address"}
    )


def test_port_send_signature_is_technology_neutral():
    sig = inspect.signature(MailTransportPort.send)
    assert list(sig.parameters) == ["self", "address", "message", "mode"]
    assert sig.parameters["mode"].default == "peer"
    # The Port module names no filesystem vocabulary and never imports pathlib —
    # concrete storage lives only in the adapter.
    import lingtai.kernel.mail_transport as port_mod

    text = Path(port_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("working_dir", "mailbox_rel", "pseudo_agent", "import Path", "pathlib"):
        assert forbidden not in text, forbidden


def test_production_adapter_is_a_port():
    assert issubclass(PosixFilesystemMailAdapter, MailTransportPort)


# --------------------------------------------------------------------------
# Shared contract — the SAME assertions run against the production adapter and
# the independent in-memory fake.
# --------------------------------------------------------------------------


@pytest.fixture(params=("fake", "posix"), ids=("fake", "posix-adapter"))
def transport_pair(request, tmp_path):
    if request.param == "fake":
        registry: dict[str, FakeMailTransport] = {}
        sender = FakeMailTransport("sender", registry)
        listener = FakeMailTransport("receiver", registry)
        try:
            yield sender, listener, "receiver", "nobody"
        finally:
            listener.stop()
        return

    sender, listener, receiver_address, heartbeat_stop = _posix_pair(tmp_path)
    try:
        yield sender, listener, receiver_address, str(tmp_path / "ghost")
    finally:
        listener.stop()
        heartbeat_stop.set()


def test_contract_send_success_returns_none_and_delivers(transport_pair):
    sender, listener, receiver_address, _ = transport_pair
    received: list[dict] = []
    delivered = threading.Event()
    listener.listen(lambda message: (received.append(message), delivered.set()))

    result = sender.send(
        receiver_address,
        {"from": "sender", "to": receiver_address, "message": "hi"},
    )

    assert result is None
    assert delivered.wait(timeout=5.0)
    assert [message["message"] for message in received] == ["hi"]


def test_contract_send_unknown_address_returns_error_string(transport_pair):
    sender, _, _, missing_address = transport_pair
    result = sender.send(missing_address, {"message": "hi"})
    assert isinstance(result, str)
    assert "No agent" in result


def test_contract_address_is_str_and_stop_is_idempotent(transport_pair):
    _, listener, _, _ = transport_pair
    assert isinstance(listener.address, str)
    listener.stop()
    listener.stop()


# --------------------------------------------------------------------------
# Architecture — Core is concrete-transport-free; composition root injects.
# --------------------------------------------------------------------------


def test_core_base_agent_source_never_names_the_concrete_adapter():
    import lingtai.kernel.base_agent as ba

    source = Path(ba.__file__).read_text(encoding="utf-8")
    assert "PosixFilesystemMailAdapter" not in source
    assert "FilesystemMailService" not in source
    # Core uses the injected port through its observable methods only.
    assert "self._mail_service = mail_service" in source


def test_kernel_services_mail_has_no_concrete_transport():
    import lingtai.kernel.services.mail as km

    assert not hasattr(km, "FilesystemMailService")
    assert not hasattr(km, "MailService")
    assert hasattr(km, "_new_mailbox_id")
    # The kernel module must not IMPORT the adapter (prose may reference it).
    # Static import check mirrors tests/test_kernel_isolation.py's AST rule.
    import ast

    tree = ast.parse(Path(km.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("lingtai.adapters"), node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("lingtai.adapters"), alias.name


def test_public_names_resolve_to_port_and_adapter():
    import lingtai
    import lingtai.services.mail as wrapper

    assert lingtai.MailService is MailTransportPort
    assert lingtai.FilesystemMailService is PosixFilesystemMailAdapter
    assert wrapper.MailService is MailTransportPort
    assert wrapper.FilesystemMailService is PosixFilesystemMailAdapter


def test_cli_build_agent_injects_the_production_adapter(tmp_path, monkeypatch):
    import lingtai.cli as cli

    captured: dict = {}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self._molt_count = 0

        def _setup_from_init(self):
            return None

    monkeypatch.setattr(cli, "Agent", FakeAgent)
    monkeypatch.setattr(cli, "LLMService", lambda **kwargs: object())
    monkeypatch.setattr(cli, "PosixJsonlEventJournalAdapter", lambda *a, **k: object())
    monkeypatch.setattr(
        cli, "build_provider_defaults_from_manifest_llm", lambda *a, **k: {}
    )
    data = {
        "manifest": {
            "llm": {"provider": "test", "model": "test-model"},
            "agent_name": "cli-agent",
        }
    }

    cli.build_agent(data, tmp_path)
    mail = captured["mail_service"]
    assert isinstance(mail, PosixFilesystemMailAdapter)
    assert mail.address == tmp_path.name
