from __future__ import annotations

import ast
import contextlib
import errno
import inspect
import json
import os
import re
import stat
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import lingtai.adapters.channel_reply_state_lock as channel_reply_lock_selector
from lingtai.adapters.posix.channel_reply_state_lock import PosixChannelReplyStateLockAdapter
import lingtai.kernel.channel_reply as channel_reply_core
from lingtai.agent import Agent
from lingtai.kernel.channel_reply import (
    CAPABILITY_MARKER,
    ChannelReplyFileStore,
    ChannelReplyOwnerFileTransport,
    ClosedChannelReplySubmitPort,
    ChannelReplyReceipt,
    ChannelReplyStatus,
    ChannelReplySubmitRequest,
    ChannelReplyTargetCapsule,
    ChannelReplyTargetFileSubmitPort,
    DEFAULT_GRANT_TTL_SECONDS,
    OwnerChannelReplyController,
    OwnerReplyGrant,
    PROTOCOL_VERSION,
    ReplyRequestRecord,
    channel_reply_capability_marker,
)
from lingtai.kernel.tool_result_summary import (
    _LTP_V2_MIGRATED_FAMILIES,
    summary_requested,
)
from lingtai.kernel.channel_reply._mutation_lock import DirectoryScanBudget, OwnedRemovalBudget
from lingtai.mcp_servers.telegram.account import TelegramAccount
from lingtai.mcp_servers.telegram.channel_reply import TelegramChannelReplyAdapter
from lingtai.tools import channel_reply
from lingtai.tools.registry import INTRINSICS


NOW = "2026-08-09T12:00:00Z"
SOON = "2026-08-09T12:05:00Z"
LATER = "2026-08-09T14:00:00Z"
OLD = "2026-08-09T11:00:00Z"
FUTURE = "2026-08-09T12:30:00Z"


def _child_crash_outbox_publication(root: Path, canonical_name: str, payload: dict, cut: str) -> None:
    """Abrupt POSIX child exit at one production hard-link publication cut."""
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    try:
        lock = PosixChannelReplyStateLockAdapter()
        with lock.exclusive(root):
            outbox = root / "outbox"
            hidden = outbox / f".{canonical_name}.{os.getpid()}.{'a' * 32}.tmp"
            data = json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            fd = os.open(hidden, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short test write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            if cut == "after-temp-fsync-before-link":
                os._exit(0)
            canonical = outbox / canonical_name
            os.link(hidden, canonical)
            if cut == "after-link-before-directory-fsync":
                os._exit(0)
            channel_reply_core._fsync_dir(outbox)
            if cut == "after-directory-fsync-before-hidden-unlink":
                os._exit(0)
            hidden.unlink()
            if cut == "after-hidden-unlink":
                os._exit(0)
            os._exit(98)
    except BaseException:
        os._exit(97)


class _Agent:
    pass


class _Account:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_message(self, chat_id, text, *, reply_to_message_id=None, **kwargs):
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "kwargs": kwargs,
            }
        )
        return {"message_id": 77}


class _Service:
    def __init__(self) -> None:
        self.account = _Account()

    def get_account(self, alias):
        assert alias == "owner"
        return self.account


def _issue_grant(store: ChannelReplyFileStore, *, expires_at=LATER, **overrides):
    grant, proof = OwnerReplyGrant.issue(
        target_agent_id=overrides.pop("target_agent_id", "agent-1"),
        target_agent_name=overrides.pop("target_agent_name", "Target"),
        target_protocol_version=overrides.pop(
            "target_protocol_version", PROTOCOL_VERSION
        ),
        channel=overrides.pop("channel", "telegram"),
        anchor=overrides.pop(
            "anchor",
            {
                "account_alias": "owner",
                "chat_id": 12345,
                "reply_to_message_id": 55,
            },
        ),
        created_at=NOW,
        expires_at=expires_at,
        ttl_seconds=overrides.pop("ttl_seconds", DEFAULT_GRANT_TTL_SECONDS),
        route_event_id=overrides.pop("route_event_id", None),
    )
    if overrides:
        grant = replace(grant, **overrides)
    store.save_grant(grant)
    return grant, proof


def _request(grant, proof, *, request_id="req-1", text="hello", created_at=NOW):
    return ChannelReplySubmitRequest(
        version=PROTOCOL_VERSION,
        grant_ref=grant.grant_ref,
        request_id=request_id,
        created_at=created_at,
        text=text,
        proof=proof,
    )


def _controller(store, *, sender=None, target_name="Target", now=lambda: NOW):
    sent: list[str] = []

    def default_sender(_grant, text):
        sent.append(text)
        return "owner-private-result"

    return (
        OwnerChannelReplyController(
            store=store,
            target_agent_id="agent-1",
            target_agent_name=target_name,
            now=now,
            send_plain_text_reply=sender or default_sender,
        ),
        sent,
    )


def test_channel_reply_lock_selector_accepts_exact_darwin(monkeypatch):
    monkeypatch.setattr(
        channel_reply_lock_selector, "_platform_identity", lambda: ("posix", "darwin")
    )
    selected = channel_reply_lock_selector.select_channel_reply_state_lock()
    assert type(selected).__name__ == "PosixChannelReplyStateLockAdapter"
    assert type(selected).__module__ == "lingtai.adapters.posix.channel_reply_state_lock"


@pytest.mark.parametrize(
    "identity",
    [("posix", "linux"), ("nt", "win32"), ("posix", "freebsd14")],
    ids=("linux", "windows", "freebsd"),
)
def test_channel_reply_lock_selector_rejects_every_non_darwin_identity(
    monkeypatch, identity,
):
    module_name = "lingtai.adapters.windows.channel_reply_state_lock"
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setattr(
        channel_reply_lock_selector, "_platform_identity", lambda: identity
    )
    with pytest.raises(
        channel_reply_lock_selector.UnsupportedChannelReplyPlatform,
        match="supported on macOS only",
    ) as caught:
        channel_reply_lock_selector.select_channel_reply_state_lock()
    assert str(caught.value) == (
        "channel_reply is closed: file-backed submission is supported on macOS "
        f"only (unsupported platform os.name={identity[0]!r}, "
        f"sys.platform={identity[1]!r})"
    )
    assert module_name not in sys.modules

def test_supported_selector_propagates_posix_adapter_construction_failure(monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock

    class BrokenPosixLock:
        def __init__(self):
            raise OSError("native POSIX lock construction failed")

    monkeypatch.setattr(
        channel_reply_lock_selector,
        "_platform_identity",
        lambda: ("posix", "darwin"),
    )
    monkeypatch.setattr(posix_lock, "PosixChannelReplyStateLockAdapter", BrokenPosixLock)

    with pytest.raises(OSError, match="native POSIX lock construction failed"):
        channel_reply_lock_selector.select_channel_reply_state_lock()


@pytest.mark.parametrize(
    "identity",
    [("posix", "linux"), ("nt", "win32")],
    ids=("linux", "windows"),
)
def test_unsupported_agent_starts_closed_without_marker_or_side_effects(
    tmp_path,
    monkeypatch,
    identity,
):
    # v1.0.3 defaults the resident tool-prose section off and carries the full
    # description on the provider schema instead. Opt in here because this
    # candidate test specifically exercises resident prompt composition.
    monkeypatch.setenv("LINGTAI_TOOL_PROSE_SECTION_ENABLED", "1")
    monkeypatch.setattr(
        channel_reply_lock_selector,
        "_platform_identity",
        lambda: identity,
    )
    service = MagicMock()
    service.get_adapter.return_value = MagicMock()
    service.provider = "probe"
    service.model = "probe-no-provider-call"
    service._base_url = None
    workdir = tmp_path / "unsupported-channel-reply-agent"

    agent = Agent(
        service=service,
        agent_name="unsupported-channel-reply-probe",
        working_dir=workdir,
    )
    try:
        assert "channel_reply" in agent._intrinsics
        assert "channel_reply" in [schema.name for schema in agent._build_tool_schemas()]
        assert "### channel_reply" in agent._build_system_prompt()
        assert isinstance(agent._channel_reply_submit_port, ClosedChannelReplySubmitPort)
        assert not (workdir / ".channel_reply").exists()

        request = ChannelReplySubmitRequest(
            version=PROTOCOL_VERSION,
            grant_ref="channel-reply-v1:unsupported",
            request_id="req-unsupported",
            created_at=NOW,
            text="must remain local",
            proof="opaque-proof",
        )
        receipt = agent._channel_reply_submit_port.submit_channel_reply(request)

        assert receipt.to_public_dict() == {
            "status": ChannelReplyStatus.DEAD.value,
            "grant_ref": "channel-reply-v1:unsupported",
            "request_id": "req-unsupported",
            "message": (
                "channel_reply is closed: file-backed submission is supported on macOS "
                f"only (unsupported platform os.name={identity[0]!r}, "
                f"sys.platform={identity[1]!r})"
            ),
        }
        assert not (workdir / ".channel_reply").exists()
        assert "channel_reply" not in agent._build_manifest().get("route_capabilities", {})
        disk_manifest = json.loads((workdir / ".agent.json").read_text(encoding="utf-8"))
        assert "channel_reply" not in disk_manifest.get("route_capabilities", {})
        assert service.send.call_count == 0
    finally:
        agent.stop(timeout=1.0)


def test_channel_reply_platform_scope_package_and_docs_are_truthful():
    repo_root = Path(__file__).resolve().parents[1]
    removed = "src/lingtai/adapters/windows/channel_reply_state_lock.py"
    assert not (repo_root / removed).exists()

    docs = {
        "src/lingtai/ANATOMY.md",
        "src/lingtai/adapters/posix/ANATOMY.md",
        "src/lingtai/adapters/windows/ANATOMY.md",
        "src/lingtai/kernel/ANATOMY.md",
        "src/lingtai/kernel/base_agent/ANATOMY.md",
        "src/lingtai/tools/ANATOMY.md",
        "src/lingtai/tools/channel_reply/ANATOMY.md",
        "src/lingtai/tools/channel_reply/CONTRACT.md",
        "src/lingtai/tools/channel_reply/manual/SKILL.md",
    }
    text_by_path = {
        path: (repo_root / path).read_text(encoding="utf-8") for path in docs
    }
    for text in text_by_path.values():
        assert removed not in text
        assert "WindowsChannelReplyStateLockAdapter" not in text
    assert "channel_reply" not in text_by_path[
        "src/lingtai/adapters/windows/ANATOMY.md"
    ]
    for path in {
        "src/lingtai/ANATOMY.md",
        "src/lingtai/tools/ANATOMY.md",
        "src/lingtai/tools/channel_reply/ANATOMY.md",
        "src/lingtai/tools/channel_reply/CONTRACT.md",
        "src/lingtai/tools/channel_reply/manual/SKILL.md",
    }:
        assert "macOS only" in text_by_path[path]


@pytest.mark.skipif(
    (os.name, sys.platform) != ("posix", "darwin"),
    reason="native file-backed channel_reply is macOS only",
)
def test_real_agent_composes_schema_prompt_and_route_marker(tmp_path, monkeypatch):
    # v1.0.3 defaults the resident tool-prose section off; opt in because this
    # test intentionally verifies the optional resident prompt projection.
    monkeypatch.setenv("LINGTAI_TOOL_PROSE_SECTION_ENABLED", "1")
    service = MagicMock()
    service.get_adapter.return_value = MagicMock()
    service.provider = "probe"
    service.model = "probe-no-provider-call"
    service._base_url = None
    workdir = tmp_path / "channel-reply-agent"
    agent = Agent(
        service=service,
        agent_name="channel-reply-probe",
        working_dir=workdir,
    )
    try:
        schema_names = [schema.name for schema in agent._build_tool_schemas()]
        assert schema_names.count("channel_reply") == 1
        assert "### channel_reply" in agent._build_system_prompt()
        marker = channel_reply_capability_marker()
        assert agent._build_manifest()["route_capabilities"]["channel_reply"] == marker
        disk_manifest = json.loads((workdir / ".agent.json").read_text(encoding="utf-8"))
        assert disk_manifest["route_capabilities"]["channel_reply"] == marker
        assert service.send.call_count == 0
    finally:
        agent.stop(timeout=1.0)


@pytest.mark.skipif(
    (os.name, sys.platform) != ("posix", "darwin"),
    reason="native file-backed channel_reply is macOS only",
)
def test_real_agent_submitter_is_inert_then_queues_owner_capsule_without_provider(tmp_path):
    service = MagicMock()
    service.get_adapter.return_value = MagicMock()
    service.provider = "probe"
    service.model = "probe-no-provider-call"
    service._base_url = None
    workdir = tmp_path / "ordinary-target"
    workdir.mkdir(mode=0o751)
    workdir.chmod(0o751)

    agent = Agent(service=service, agent_name="Target", working_dir=workdir)
    try:
        assert stat.S_IMODE(workdir.stat().st_mode) == 0o751
        assert isinstance(agent._channel_reply_submit_port, ChannelReplyTargetFileSubmitPort)
        assert (
            type(agent._channel_reply_submit_port._mutation_lock).__module__
            == "lingtai.adapters.posix.channel_reply_state_lock"
        )
        assert not (workdir / ".channel_reply").exists()
        grant, proof = OwnerReplyGrant.issue(
            target_agent_id=agent._agent_id,
            target_agent_name="Target",
            target_protocol_version=PROTOCOL_VERSION,
            channel="telegram",
            anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
            created_at=NOW,
            expires_at=LATER,
        )
        request = _request(grant, proof)
        assert agent._channel_reply_submit_port.submit_channel_reply(request).status == "dead"
        assert not (workdir / ".channel_reply").exists()

        ChannelReplyTargetCapsule.create(
            target_workdir=workdir,
            target_agent_id=agent._agent_id,
            target_agent_name="Target",
            created_at=NOW,
            expires_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
            mutation_lock=PosixChannelReplyStateLockAdapter(),
        )
        queued = agent._channel_reply_submit_port.submit_channel_reply(request)
        assert queued.status == "pending"
        assert len(list((workdir / ".channel_reply" / "outbox").glob("*.json"))) == 1
        assert service.send.call_count == 0
    finally:
        agent.stop(timeout=1.0)


def test_agent_omitting_final_intrinsic_omits_submitter_and_route_marker(tmp_path):
    service = MagicMock()
    service.get_adapter.return_value = MagicMock()
    service.provider = "probe"
    service.model = "probe-no-provider-call"
    service._base_url = None
    intrinsics = {name: value for name, value in INTRINSICS.items() if name != "channel_reply"}
    agent = Agent(
        service=service,
        agent_name="no-channel-reply",
        working_dir=tmp_path / "without-intrinsic",
        intrinsics=intrinsics,
    )
    try:
        assert agent._channel_reply_submit_port is None
        assert "channel_reply" not in agent._build_manifest().get("route_capabilities", {})
        disk = json.loads((agent._working_dir / ".agent.json").read_text(encoding="utf-8"))
        assert "channel_reply" not in disk.get("route_capabilities", {})
        assert service.send.call_count == 0
    finally:
        agent.stop(timeout=1.0)


def test_agent_injected_active_port_retains_route_marker_on_unsupported_identity(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        channel_reply_lock_selector,
        "_platform_identity",
        lambda: ("nt", "win32"),
    )
    service = MagicMock()
    service.get_adapter.return_value = MagicMock()
    service.provider = "probe"
    service.model = "probe-no-provider-call"
    service._base_url = None
    workdir = tmp_path / "injected-active-port"
    workdir.mkdir()
    injected = ChannelReplyTargetFileSubmitPort(
        workdir,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )

    agent = Agent(
        service=service,
        agent_name="injected-active-channel-reply",
        working_dir=workdir,
        channel_reply_submit_port=injected,
    )
    try:
        marker = channel_reply_capability_marker()
        assert agent._channel_reply_submit_port is injected
        assert agent._build_manifest()["route_capabilities"]["channel_reply"] == marker
        disk = json.loads((agent._working_dir / ".agent.json").read_text(encoding="utf-8"))
        assert disk["route_capabilities"]["channel_reply"] == marker
        assert not (workdir / ".channel_reply").exists()
        assert service.send.call_count == 0
    finally:
        agent.stop(timeout=1.0)


def test_agent_explicit_closed_port_does_not_advertise_route_marker(tmp_path):
    service = MagicMock()
    service.get_adapter.return_value = MagicMock()
    service.provider = "probe"
    service.model = "probe-no-provider-call"
    service._base_url = None
    agent = Agent(
        service=service,
        agent_name="closed-channel-reply",
        working_dir=tmp_path / "closed-port",
        channel_reply_submit_port=ClosedChannelReplySubmitPort(),
    )
    try:
        assert "channel_reply" in agent._intrinsics
        assert "channel_reply" not in agent._build_manifest().get("route_capabilities", {})
        disk = json.loads((agent._working_dir / ".agent.json").read_text(encoding="utf-8"))
        assert "channel_reply" not in disk.get("route_capabilities", {})
        assert service.send.call_count == 0
    finally:
        agent.stop(timeout=1.0)


def test_channel_reply_manual_distinguishes_request_time_from_grant_time():
    repo_root = Path(__file__).resolve().parents[1]
    manual = (
        repo_root / "src/lingtai/tools/channel_reply/manual/SKILL.md"
    ).read_text(encoding="utf-8")
    contract = (
        repo_root / "src/lingtai/tools/channel_reply/CONTRACT.md"
    ).read_text(encoding="utf-8")

    assert "current UTC timestamp at the" in manual
    assert "moment you make this concrete submit attempt" in manual
    assert "not an exact owner authority field" in manual
    assert "not the grant's earlier issuance/route" in manual
    assert "Core still rejects stale or future request timestamps" in manual
    assert "fresh" in contract
    assert "target-authored request `created_at`" in contract
    assert "distinct from immutable owner" in contract
    assert "grant issuance time" in contract


def test_channel_reply_intrinsic_is_registered_and_normally_closed():
    assert "channel_reply" in INTRINSICS
    result = channel_reply.handle(
        _Agent(),
        {
            "action": "submit",
            "input": {
                "version": PROTOCOL_VERSION,
                "grant_ref": "channel-reply-v1:missing",
                "request_id": "req-1",
                "created_at": NOW,
                "text": "plain reply",
                "proof": "proof",
            },
            "reasoning": "reply to routed message",
        },
    )
    assert result["status"] == ChannelReplyStatus.DEAD.value
    assert "closed" in result["message"]


def test_submit_schema_and_domain_exclude_authority_fields():
    schema_props = channel_reply.get_schema()["properties"]["input"]["oneOf"][0]["properties"]
    assert set(schema_props) == {
        "version",
        "grant_ref",
        "request_id",
        "created_at",
        "text",
        "proof",
    }
    created_at_description = schema_props["created_at"]["description"]
    assert "Target-authored current UTC timestamp" in created_at_description
    assert "when this concrete submit request is made" in created_at_description
    assert "never copy the grant's issuance/route time" in created_at_description
    with pytest.raises(ValueError, match="authority_fields_not_allowed"):
        ChannelReplySubmitRequest.from_mapping(
            {
                "version": PROTOCOL_VERSION,
                "grant_ref": "channel-reply-v1:grant",
                "request_id": "req-1",
                "created_at": NOW,
                "text": "plain reply",
                "proof": "proof",
                "message_id": 55,
            }
        )


def test_bool_version_and_noncanonical_timestamp_are_rejected():
    base = {
        "version": True,
        "grant_ref": "channel-reply-v1:grant",
        "request_id": "req-1",
        "created_at": NOW,
        "text": "plain reply",
        "proof": "proof",
    }
    with pytest.raises(ValueError, match="unsupported_version"):
        ChannelReplySubmitRequest.from_mapping(base)
    base["version"] = PROTOCOL_VERSION
    base["created_at"] = "2026-08-09T12:00:00+00:00"
    with pytest.raises(ValueError, match="timestamp"):
        ChannelReplySubmitRequest.from_mapping(base)


def test_valid_request_sends_once_dedupes_and_redacts_receipt(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    controller, sent = _controller(store)

    receipt = controller.submit_channel_reply(_request(grant, proof))
    duplicate = controller.submit_channel_reply(_request(grant, proof, text="changed"))

    assert receipt.status == ChannelReplyStatus.SENT.value
    assert duplicate == receipt
    assert sent == ["hello"]
    public = receipt.to_public_dict()
    assert set(public) == {"status", "grant_ref", "request_id", "message", "public_ref"}
    assert isinstance(public["public_ref"], str)
    assert re.fullmatch(r"channel-reply:[A-Za-z0-9_-]{16}", public["public_ref"])
    assert public["public_ref"] not in {"owner", "12345", "55", "77", "hello", proof}
    rendered = json.dumps(public, sort_keys=True)
    for sensitive in ("owner", "12345", "hello", proof):
        assert sensitive not in rendered


def test_different_request_after_claim_or_ambiguous_never_sends(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    store.put_request(
        ReplyRequestRecord(
            grant_id=grant.grant_id,
            request_id="req-1",
            target_agent_id="agent-1",
            status=ChannelReplyStatus.SENDING,
            created_at=NOW,
        )
    )
    store.save_grant(replace(grant, claimed_request_id="req-1"))

    recovered = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    controller, sent = _controller(recovered)
    same = controller.submit_channel_reply(_request(grant, proof, request_id="req-1"))
    other = controller.submit_channel_reply(_request(grant, proof, request_id="req-2"))

    assert same.status == ChannelReplyStatus.AMBIGUOUS.value
    assert other.status == ChannelReplyStatus.DEAD.value
    assert "claimed" in other.message or "consumed" in other.message
    assert sent == []


def test_crash_after_external_success_before_final_accounting_blocks_second_request(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    claimed, terminal, claim_token = store.claim_request(
        grant_ref=grant.grant_ref,
        request=_request(grant, proof),
        target_agent_id="agent-1",
        now=NOW,
        request_max_age_seconds=600,
        future_skew_seconds=120,
    )
    assert terminal is None
    assert claim_token is not None
    assert store.mark_prepared(claimed, _request(grant, proof), NOW, claim_token)
    assert store.mark_sending(claimed, _request(grant, proof), claim_token)

    restarted = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    controller, sent = _controller(restarted)
    other = controller.submit_channel_reply(_request(grant, proof, request_id="req-2"))

    assert other.status == ChannelReplyStatus.DEAD.value
    assert sent == []


def test_missing_request_after_consumed_grant_recovers_ambiguous_without_resend(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    store.save_grant(replace(grant, claimed_request_id="req-1", consumed_request_id="req-1"))
    controller, sent = _controller(store)

    receipt = controller.submit_channel_reply(_request(grant, proof))

    assert receipt.status == ChannelReplyStatus.AMBIGUOUS.value
    assert sent == []


def test_independent_store_instances_share_claim_boundary(tmp_path):
    store_a = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    store_b = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store_a)
    barrier = threading.Barrier(2)
    sent: list[str] = []

    def submit(store, request_id):
        controller, _ = _controller(store, sender=lambda _grant, text: sent.append(request_id) or "ok")
        barrier.wait(timeout=3)
        return controller.submit_channel_reply(_request(grant, proof, request_id=request_id))

    out: list = []
    threads = [
        threading.Thread(target=lambda: out.append(submit(store_a, "req-a"))),
        threading.Thread(target=lambda: out.append(submit(store_b, "req-b"))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(sent) == 1
    assert sorted(receipt.status for receipt in out) == ["dead", "sent"]


def test_same_request_overlap_has_one_dispatch_owner_and_safe_receipts(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    controller, sent = _controller(store)
    entered = threading.Event()
    release = threading.Event()
    original_mark_prepared = store.mark_prepared

    def blocked_mark_prepared(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original_mark_prepared(*args, **kwargs)

    store.mark_prepared = blocked_mark_prepared
    results: list[ChannelReplyReceipt] = []
    first = threading.Thread(
        target=lambda: results.append(controller.submit_channel_reply(_request(grant, proof)))
    )
    first.start()
    assert entered.wait(timeout=3)
    overlap = controller.submit_channel_reply(_request(grant, proof, text="changed"))
    assert overlap.status == ChannelReplyStatus.PENDING.value
    release.set()
    first.join(timeout=5)
    assert not first.is_alive()
    assert [receipt.status for receipt in results] == [ChannelReplyStatus.SENT.value]
    assert sent == ["hello"]
    committed = controller.submit_channel_reply(_request(grant, proof))
    assert committed == results[0]


def test_same_request_prepared_overlap_cannot_dispatch(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    controller, sent = _controller(store)
    entered = threading.Event()
    release = threading.Event()
    original_mark_sending = store.mark_sending

    def blocked_mark_sending(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original_mark_sending(*args, **kwargs)

    store.mark_sending = blocked_mark_sending
    results: list[ChannelReplyReceipt] = []
    first = threading.Thread(
        target=lambda: results.append(controller.submit_channel_reply(_request(grant, proof)))
    )
    first.start()
    assert entered.wait(timeout=3)
    overlap = controller.submit_channel_reply(_request(grant, proof, text="changed"))
    assert overlap.status == ChannelReplyStatus.PENDING.value
    release.set()
    first.join(timeout=5)
    assert not first.is_alive()
    assert [receipt.status for receipt in results] == [ChannelReplyStatus.SENT.value]
    assert sent == ["hello"]


def test_same_request_sending_overlap_commits_ambiguity_without_resend(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    entered = threading.Event()
    release = threading.Event()
    sends: list[str] = []

    def sender(_grant, text):
        sends.append(text)
        entered.set()
        assert release.wait(timeout=3)
        return "owner-private-result"

    controller, _ = _controller(store, sender=sender)
    results: list[ChannelReplyReceipt] = []
    first = threading.Thread(
        target=lambda: results.append(controller.submit_channel_reply(_request(grant, proof)))
    )
    first.start()
    assert entered.wait(timeout=3)
    overlap = controller.submit_channel_reply(_request(grant, proof))
    assert overlap.status == ChannelReplyStatus.AMBIGUOUS.value
    release.set()
    first.join(timeout=5)
    assert not first.is_alive()
    assert results[0].status == ChannelReplyStatus.AMBIGUOUS.value
    assert sends == ["hello"]
    assert controller.submit_channel_reply(_request(grant, proof)) == overlap


@pytest.mark.parametrize("status", list(ChannelReplyStatus))
def test_recovery_covers_every_state(tmp_path, status):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, _proof = _issue_grant(store)
    receipt = None
    if status in {
        ChannelReplyStatus.SENT,
        ChannelReplyStatus.FAILED,
        ChannelReplyStatus.DEAD,
        ChannelReplyStatus.AMBIGUOUS,
    }:
        from lingtai.kernel.channel_reply import ChannelReplyReceipt

        receipt = ChannelReplyReceipt(
            status=status.value,
            grant_ref=grant.grant_ref,
            request_id="req-state",
            message=status.value,
        )
    store.put_request(
        ReplyRequestRecord(
            grant_id=grant.grant_id,
            request_id="req-state",
            target_agent_id="agent-1",
            status=status,
            created_at=NOW,
            receipt=receipt,
            claim_token=(
                "claim-token"
                if status in {
                    ChannelReplyStatus.CLAIMED,
                    ChannelReplyStatus.PREPARED,
                    ChannelReplyStatus.SENDING,
                }
                else None
            ),
        )
    )
    recovered = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    record = recovered.get_request(grant.grant_id, "req-state")
    if status is ChannelReplyStatus.SENDING:
        assert record.status is ChannelReplyStatus.AMBIGUOUS
    elif status in {ChannelReplyStatus.PENDING, ChannelReplyStatus.CLAIMED, ChannelReplyStatus.PREPARED}:
        assert record.status is ChannelReplyStatus.PENDING
    else:
        assert record.status is status


def test_timestamp_age_future_and_rollback_policy(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store)
    controller, sent = _controller(store)

    assert controller.submit_channel_reply(_request(grant, proof, created_at=OLD)).status == "dead"
    assert controller.submit_channel_reply(_request(grant, proof, request_id="future", created_at=FUTURE)).status == "dead"

    rollback_store = ChannelReplyFileStore(tmp_path / "rollback", mutation_lock=PosixChannelReplyStateLockAdapter())
    rollback_grant, rollback_proof = _issue_grant(rollback_store)
    rollback_controller, _ = _controller(rollback_store, now=lambda: "2026-08-09T11:00:00Z")
    assert rollback_controller.submit_channel_reply(_request(rollback_grant, rollback_proof)).status == "dead"
    assert sent == []


def test_invalid_revoked_wrong_target_wrong_name_and_wrong_proof_fail_closed(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    cases = [
        ({"target_agent_id": "agent-2"}, "real", "Target", "target mismatch"),
        ({"target_agent_name": "Other"}, "real", "Target", "identity mismatch"),
        ({"revoked": True}, "real", "Target", "revoked"),
        ({}, "wrong", "Target", "proof rejected"),
    ]
    for index, (grant_kwargs, proof_kind, target_name, message) in enumerate(cases):
        grant, real_proof = _issue_grant(store, **grant_kwargs)
        controller, sent = _controller(store, target_name=target_name)
        receipt = controller.submit_channel_reply(
            _request(
                grant,
                real_proof if proof_kind == "real" else "wrong",
                request_id=f"req-{index}",
            )
        )
        assert receipt.status == ChannelReplyStatus.DEAD.value
        assert message in receipt.message
        assert sent == []


def test_expired_consumed_revoke_and_route_event_dedupe(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    expired, proof = _issue_grant(store, expires_at=OLD)
    controller, sent = _controller(store)
    assert controller.submit_channel_reply(_request(expired, proof)).status == "dead"

    live, live_proof = _issue_grant(store)
    assert controller.submit_channel_reply(_request(live, live_proof, request_id="one")).status == "sent"
    assert controller.submit_channel_reply(_request(live, live_proof, request_id="two")).status == "dead"
    assert sent == ["hello"]

    revoked, _ = _issue_grant(store)
    assert store.revoke_grant(revoked.grant_ref) is True
    assert store.get_grant(revoked.grant_ref).revoked is True

    def factory():
        return OwnerReplyGrant.issue(
            target_agent_id="agent-1",
            target_agent_name="Target",
            target_protocol_version=PROTOCOL_VERSION,
            channel="telegram",
            anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
            created_at=NOW,
        )

    first_grant, first_proof, created = store.issue_or_reuse_grant(
        route_event_id="route-1",
        grant_factory=factory,
        now=NOW,
    )
    again_grant, again_proof, again_created = store.issue_or_reuse_grant(
        route_event_id="route-1",
        grant_factory=factory,
        now=NOW,
    )
    assert created is True
    assert again_created is False
    assert again_grant.grant_id == first_grant.grant_id
    assert again_proof == first_proof


def _route_factory(*, expires_at=LATER, calls=None):
    def factory():
        if calls is not None:
            calls.append("factory")
        return OwnerReplyGrant.issue(
            target_agent_id="agent-1",
            target_agent_name="Target",
            target_protocol_version=PROTOCOL_VERSION,
            channel="telegram",
            anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
            created_at=NOW,
            expires_at=expires_at,
        )

    return factory


def test_route_event_expired_replay_is_proof_free_tombstone(tmp_path):
    calls: list[str] = []
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id="route-expired",
        grant_factory=_route_factory(expires_at=SOON, calls=calls),
        now=NOW,
    )
    assert grant is not None and proof and created
    replay_grant, replay_proof, replay_created = store.issue_or_reuse_grant(
        route_event_id="route-expired",
        grant_factory=_route_factory(calls=calls),
        now=LATER,
    )
    assert (replay_grant, replay_proof, replay_created) == (None, None, False)
    assert calls == ["factory"]
    event = json.loads((tmp_path / "route_events" / "route-expired.json").read_text())
    assert event["decision"] == "expired"
    assert event["proof"] == ""
    restarted = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    assert restarted.issue_or_reuse_grant(
        route_event_id="route-expired",
        grant_factory=_route_factory(calls=calls),
        now=LATER,
    ) == (None, None, False)
    assert calls == ["factory"]


def test_route_event_revoked_missing_and_quarantined_replays_never_remint(tmp_path):
    for case in ("revoked", "missing", "quarantined"):
        root = tmp_path / case
        calls: list[str] = []
        store = ChannelReplyFileStore(root, mutation_lock=PosixChannelReplyStateLockAdapter())
        grant, _proof, _created = store.issue_or_reuse_grant(
            route_event_id=f"route-{case}",
            grant_factory=_route_factory(calls=calls),
            now=NOW,
        )
        assert grant is not None
        grant_path = root / "grants" / f"{grant.grant_id}.json"
        if case == "revoked":
            assert store.revoke_grant(grant.grant_ref)
        elif case == "missing":
            grant_path.unlink()
        else:
            grant_path.write_text("{}", encoding="utf-8")
        assert store.issue_or_reuse_grant(
            route_event_id=f"route-{case}",
            grant_factory=_route_factory(calls=calls),
            now=NOW,
        ) == (None, None, False)
        assert calls == ["factory"]
        event = json.loads((root / "route_events" / f"route-{case}.json").read_text())
        assert event["decision"] in {case, "missing"}
        assert event["proof"] == ""


def test_existing_malformed_route_event_commits_quarantine_tombstone(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    path = tmp_path / "route_events" / "route-bad.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)
    calls: list[str] = []
    assert store.issue_or_reuse_grant(
        route_event_id="route-bad",
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == []
    tombstone = json.loads(path.read_text(encoding="utf-8"))
    assert tombstone["decision"] == "quarantined"
    assert tombstone["grant_id"] is None
    assert tombstone["proof"] == ""


def test_concurrent_route_event_duplicates_call_factory_once(tmp_path):
    store_a = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    store_b = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    calls: list[str] = []
    start = threading.Barrier(2)

    def factory():
        calls.append("factory")
        time.sleep(0.05)
        return _route_factory()()

    results: list[tuple] = []

    def issue(store):
        start.wait(timeout=3)
        results.append(
            store.issue_or_reuse_grant(
                route_event_id="route-concurrent",
                grant_factory=factory,
                now=NOW,
            )
        )

    threads = [threading.Thread(target=issue, args=(store,)) for store in (store_a, store_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert calls == ["factory"]
    assert sorted(created for _grant, _proof, created in results) == [False, True]
    assert results[0][0].grant_id == results[1][0].grant_id
    assert results[0][1] == results[1][1]


def test_strict_store_modes_quarantine_symlink_nonregular_oversize_duplicate_unknown_version(tmp_path):
    store = ChannelReplyFileStore(tmp_path, max_record_bytes=256, mutation_lock=PosixChannelReplyStateLockAdapter())
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    grant, _proof = _issue_grant(store)
    grant_path = tmp_path / "grants" / f"{grant.grant_id}.json"
    assert stat.S_IMODE(grant_path.stat().st_mode) == 0o600

    grant_path.write_text('{"version":1,"version":1}', encoding="utf-8")
    assert store.get_grant(grant.grant_ref) is None
    assert list((tmp_path / ".dead" / "grants").glob("*.dead"))

    bad, _ = _issue_grant(store)
    bad_path = tmp_path / "grants" / f"{bad.grant_id}.json"
    data = json.loads(bad_path.read_text(encoding="utf-8"))
    data["extra"] = True
    bad_path.write_text(json.dumps(data), encoding="utf-8")
    assert store.get_grant(bad.grant_ref) is None

    bad2, _ = _issue_grant(store)
    bad2_path = tmp_path / "grants" / f"{bad2.grant_id}.json"
    data = json.loads(bad2_path.read_text(encoding="utf-8"))
    data["version"] = 999
    bad2_path.write_text(json.dumps(data), encoding="utf-8")
    assert store.get_grant(bad2.grant_ref) is None

    too_large, _ = _issue_grant(store)
    too_large_path = tmp_path / "grants" / f"{too_large.grant_id}.json"
    too_large_path.write_text(" " * 300, encoding="utf-8")
    assert store.get_grant(too_large.grant_ref) is None

    nonregular, _ = _issue_grant(store)
    nonregular_path = tmp_path / "grants" / f"{nonregular.grant_id}.json"
    nonregular_path.unlink()
    nonregular_path.mkdir()
    assert store.get_grant(nonregular.grant_ref) is None

    if hasattr(os, "symlink"):
        link_root = tmp_path / "link-root"
        os.symlink(tmp_path, link_root)
        with pytest.raises(ValueError, match="symlink"):
            ChannelReplyFileStore(link_root, mutation_lock=PosixChannelReplyStateLockAdapter())


def test_misnamed_grant_cannot_select_embedded_other_anchor(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant_a, _proof_a = _issue_grant(
        store,
        anchor={"account_alias": "owner", "chat_id": 111, "reply_to_message_id": 11},
    )
    grant_b, proof_b = _issue_grant(
        store,
        anchor={"account_alias": "owner", "chat_id": 222, "reply_to_message_id": 22},
    )
    path_a = tmp_path / "grants" / f"{grant_a.grant_id}.json"
    path_b = tmp_path / "grants" / f"{grant_b.grant_id}.json"
    path_a.write_bytes(path_b.read_bytes())
    path_a.chmod(0o600)
    controller, sent = _controller(store)
    receipt = controller.submit_channel_reply(_request(grant_a, proof_b))
    assert receipt.status == ChannelReplyStatus.DEAD.value
    assert sent == []
    assert list((tmp_path / ".dead" / "grants").glob(f"{path_a.name}.*.dead"))


def test_request_filename_embedded_tuple_and_receipt_must_agree(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, _proof = _issue_grant(store)
    receipt = ChannelReplyReceipt(
        status=ChannelReplyStatus.SENT.value,
        grant_ref=grant.grant_ref,
        request_id="req-bind",
        message="sent",
    )
    store.put_request(
        ReplyRequestRecord(
            grant_id=grant.grant_id,
            request_id="req-bind",
            target_agent_id="agent-1",
            status=ChannelReplyStatus.SENT,
            created_at=NOW,
            receipt=receipt,
        )
    )
    path = next((tmp_path / "requests").glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["receipt"]["request_id"] = "other"
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    assert store.get_request(grant.grant_id, "req-bind") is None
    assert list((tmp_path / ".dead" / "requests").glob("*.dead"))


def test_route_event_input_embedded_identity_and_proof_digest_must_agree(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, _proof, _created = store.issue_or_reuse_grant(
        route_event_id="route-bind",
        grant_factory=_route_factory(),
        now=NOW,
    )
    assert grant is not None
    path = tmp_path / "route_events" / "route-bind.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["route_event_id"] = "route-other"
    data["proof_digest"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    calls: list[str] = []
    assert store.issue_or_reuse_grant(
        route_event_id="route-bind",
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == []
    tombstone = json.loads(path.read_text(encoding="utf-8"))
    assert tombstone["route_event_id"] == "route-bind"
    assert tombstone["decision"] == "quarantined"


def test_strict_store_rejects_hardlink_mode_and_record_symlink(tmp_path):
    hard_root = tmp_path / "hard"
    hard_store = ChannelReplyFileStore(hard_root, mutation_lock=PosixChannelReplyStateLockAdapter())
    hard_grant, _ = _issue_grant(hard_store)
    hard_path = hard_root / "grants" / f"{hard_grant.grant_id}.json"
    os.link(hard_path, hard_root / "extra-link.json")
    assert hard_store.get_grant(hard_grant.grant_ref) is None

    mode_root = tmp_path / "mode"
    mode_store = ChannelReplyFileStore(mode_root, mutation_lock=PosixChannelReplyStateLockAdapter())
    mode_grant, _ = _issue_grant(mode_store)
    mode_path = mode_root / "grants" / f"{mode_grant.grant_id}.json"
    mode_path.chmod(0o644)
    assert mode_store.get_grant(mode_grant.grant_ref) is None

    link_root = tmp_path / "file-link"
    link_store = ChannelReplyFileStore(link_root, mutation_lock=PosixChannelReplyStateLockAdapter())
    link_grant, _ = _issue_grant(link_store)
    link_path = link_root / "grants" / f"{link_grant.grant_id}.json"
    backing = link_root / "backing.json"
    os.replace(link_path, backing)
    os.symlink(backing, link_path)
    assert link_store.get_grant(link_grant.grant_ref) is None


def test_terminal_and_expired_cleanup(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof = _issue_grant(store, expires_at=OLD)
    controller, _sent = _controller(store)
    controller.submit_channel_reply(_request(grant, proof, created_at=OLD))
    removed = store.cleanup_retained(now="2026-08-20T12:00:00Z", retention_seconds=24 * 60 * 60)
    assert removed >= 1


def test_owner_cleanup_enforces_hard_record_inspection_budget_across_surfaces(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    for index in range(12):
        _issue_grant(store, target_agent_id=f"agent-{index}")

    store.cleanup_retained(now=NOW, max_records=3)
    assert store.last_cleanup_inspections <= 3
    store.cleanup_retained(now=NOW, max_records=3)
    assert store.last_cleanup_inspections <= 3


def test_route_event_cleanup_strips_proof_then_preserves_and_expires_tombstone(tmp_path):
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id="route-retain",
        grant_factory=_route_factory(expires_at=SOON),
        now=NOW,
    )
    assert grant is not None and proof and created
    event_path = tmp_path / "route_events" / "route-retain.json"
    removed = store.cleanup_retained(
        now="2026-08-20T12:00:00Z",
        retention_seconds=24 * 60 * 60,
    )
    assert removed >= 1
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["decision"] == "expired"
    assert event["proof"] == ""
    assert event_path.exists()
    store.cleanup_retained(
        now="2026-10-20T12:00:00Z",
        retention_seconds=24 * 60 * 60,
        route_event_tombstone_seconds=30 * 24 * 60 * 60,
    )
    assert not event_path.exists()



def test_core_session_binding_verifies_exceptional_exit_and_resets_facade():
    session = MagicMock()
    session.protocol_marker = "channel-reply-mutation-session/v1"
    session.root = object()
    session.root_identity = object()
    invalid = False

    def verify():
        if invalid:
            raise OSError("probe mutation session replaced")

    session.verify.side_effect = verify
    with pytest.raises(OSError, match="mutation session replaced") as excinfo:
        with channel_reply_core._bind_mutation_session(Path("/virtual-channel-reply-root"), session):
            invalid = True
            raise RuntimeError("probe body failure")

    assert isinstance(excinfo.value.__context__, RuntimeError)
    assert session.verify.call_count == 2
    assert channel_reply_core._session_filesystem() is None


def test_core_state_consumers_require_explicit_mutation_lock():
    consumers = (
        ChannelReplyFileStore,
        ChannelReplyTargetCapsule.create,
        ChannelReplyTargetFileSubmitPort,
        ChannelReplyOwnerFileTransport,
    )
    for consumer in consumers:
        parameter = inspect.signature(consumer).parameters["mutation_lock"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_production_transaction_rejects_missing_verified_session_without_path_fallback(tmp_path):
    class InvalidMutationLock:
        @contextlib.contextmanager
        def exclusive(self, _state_dir, *, expected_root=None):
            assert expected_root is None
            yield None

    root = tmp_path / "invalid-session"
    with pytest.raises(OSError, match="invalid session marker"):
        ChannelReplyFileStore(root, mutation_lock=InvalidMutationLock())
    assert list(root.rglob("*.json")) == []


class _ObservedSession:
    def __init__(self, session, calls: list[str], before=None):
        self._session = session
        self._calls = calls
        self._before = before

    def __getattr__(self, name):
        value = getattr(self._session, name)
        if not callable(value):
            return value

        def observed(*args, **kwargs):
            self._calls.append(name)
            if self._before is not None:
                self._before(name, args, kwargs)
            return value(*args, **kwargs)

        return observed


class _ObservedMutationLock:
    def __init__(self, *, before=None):
        from lingtai.adapters.posix.channel_reply_state_lock import (
            PosixChannelReplyStateLockAdapter,
        )

        self._delegate = PosixChannelReplyStateLockAdapter()
        self.before = before
        self.operations: list[str] = []
        self.expected_roots: list[object] = []

    @contextlib.contextmanager
    def exclusive(self, state_dir, *, expected_root=None):
        self.expected_roots.append(expected_root)
        with self._delegate.exclusive(
            state_dir, expected_root=expected_root
        ) as session:
            yield _ObservedSession(session, self.operations, self.before)


def test_all_production_consumers_use_yielded_verified_session(tmp_path):
    owner_lock = _ObservedMutationLock()
    store = ChannelReplyFileStore(tmp_path / "owner", mutation_lock=owner_lock)
    owner_lock.operations.clear()
    grant, proof = _issue_grant(store)
    assert {"scan", "atomic_write_bytes", "fsync_directory"}.issubset(
        owner_lock.operations
    )
    assert owner_lock.expected_roots[-1] == store._root_identity

    target = tmp_path / "target"
    target.mkdir()
    capsule_lock = _ObservedMutationLock()
    ChannelReplyTargetCapsule.create(
        target_workdir=target,
        target_agent_id="agent-1",
        target_agent_name="Target",
        created_at=NOW,
        expires_at=LATER,
        mutation_lock=capsule_lock,
    )
    assert "atomic_write_bytes" in capsule_lock.operations
    assert "scan" in capsule_lock.operations

    submit_lock = _ObservedMutationLock()
    submitter = ChannelReplyTargetFileSubmitPort(
        target, mutation_lock=submit_lock, now=lambda: NOW
    )
    request = _request(grant, proof, request_id="session-consumer")
    assert submitter.submit_channel_reply(request).status == "pending"
    assert {"scan", "open_directory", "inspect", "read_bytes", "atomic_write_bytes"}.issubset(
        submit_lock.operations
    )
    assert submit_lock.expected_roots[-1] == submitter._root_identity

    transport_lock = _ObservedMutationLock()
    terminal = _RecordingSubmitPort()
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=terminal,
        mutation_lock=transport_lock,
        now=lambda: NOW,
    )
    transport_lock.operations.clear()
    assert transport.drain_once().status == "sent"
    assert len(terminal.calls) == 1
    assert {
        "scan", "inspect", "read_bytes", "move_entry", "atomic_write_bytes",
        "remove_owned_entry", "fsync_directory",
    }.issubset(transport_lock.operations)
    assert transport_lock.expected_roots[-1] == transport._root_identity


def test_production_store_child_replacement_during_session_fails_closed_without_deleting_replacement(
    tmp_path,
):
    root = tmp_path / "owner-race"
    swapped = False

    def replace_before_read(name, _args, _kwargs):
        nonlocal swapped
        if (
            name != "read_bytes"
            or swapped
            or len(_args) < 2
            or _args[1] == "owner-maintenance-progress.json"
        ):
            return
        swapped = True
        grants = root / "grants"
        grants.rename(root / "grants-displaced")
        grants.mkdir(mode=0o700)
        sentinel = grants / "sentinel"
        sentinel.write_text("replacement-owned", encoding="utf-8")
        sentinel.chmod(0o600)

    lock = _ObservedMutationLock(before=replace_before_read)
    store = ChannelReplyFileStore(root, mutation_lock=lock)
    grant, _proof = _issue_grant(store)
    assert store.get_grant(grant.grant_ref) is None
    assert (root / "grants" / "sentinel").read_text(encoding="utf-8") == "replacement-owned"


def test_production_store_canonical_file_replacement_is_rejected_without_deleting_occupant(
    tmp_path,
):
    root = tmp_path / "owner-canonical-race"
    lock = _ObservedMutationLock()
    store = ChannelReplyFileStore(root, mutation_lock=lock)
    grant, _proof = _issue_grant(store)
    canonical = root / "grants" / f"{grant.grant_id}.json"
    displaced = root / "displaced-grant.json"
    replacement = root / "same-uid-replacement.json"
    replacement_bytes = b'{"same_uid_replacement":true}'
    replacement.write_bytes(replacement_bytes)
    replacement.chmod(0o600)
    swapped = False

    def replace_before_read(name, _args, _kwargs):
        nonlocal swapped
        if (
            name != "read_bytes"
            or swapped
            or len(_args) < 2
            or _args[1] == "owner-maintenance-progress.json"
        ):
            return
        swapped = True
        canonical.rename(displaced)
        replacement.rename(canonical)

    lock.before = replace_before_read
    assert store.get_grant(grant.grant_ref) is None
    assert swapped is True
    assert canonical.read_bytes() == replacement_bytes
    assert displaced.is_file()


def test_production_store_root_replacement_after_session_acquisition_fails_closed(
    tmp_path,
):
    root = tmp_path / "owner-root-race"
    swapped = False

    def replace_before_inspect(name, _args, _kwargs):
        nonlocal swapped
        if name != "scan" or swapped:
            return
        swapped = True
        root.rename(tmp_path / "owner-root-displaced")
        root.mkdir(mode=0o700)
        sentinel = root / "sentinel"
        sentinel.write_text("replacement-root", encoding="utf-8")
        sentinel.chmod(0o600)

    # Construction's recovery scan is itself a production transaction.
    lock = _ObservedMutationLock(before=replace_before_inspect)
    with pytest.raises(OSError, match="root|replaced|identity"):
        ChannelReplyFileStore(root, mutation_lock=lock)
    assert (root / "sentinel").read_text(encoding="utf-8") == "replacement-root"


def test_production_store_rejects_injected_early_eof_through_yielded_session(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock

    root = tmp_path / "owner-short-read"
    store = ChannelReplyFileStore(root, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant, _proof = _issue_grant(store)
    canonical = root / "grants" / f"{grant.grant_id}.json"
    real_read = posix_lock.os.read
    first = True

    def early_eof(fd, size):
        nonlocal first
        if first:
            first = False
            return real_read(fd, min(size, 2))
        return b""

    monkeypatch.setattr(posix_lock.os, "read", early_eof)
    assert store.get_grant(grant.grant_ref) is None
    assert first is False
    assert not canonical.exists()


@pytest.mark.parametrize("fault", ["short_write", "file_fsync"])
def test_production_target_injected_io_fault_never_publishes_partial_queue(
    tmp_path,
    monkeypatch,
    fault,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock

    target = tmp_path / f"target-{fault}"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target, request_id=fault)
    faulted = False
    if fault == "short_write":
        real_write_all = posix_lock._write_all

        def short_write_then_fail(fd, data):
            nonlocal faulted
            if not faulted:
                faulted = True
                real_write_all(fd, data[:2])
                raise OSError("synthetic production short write")
            return real_write_all(fd, data)

        monkeypatch.setattr(posix_lock, "_write_all", short_write_then_fail)
    else:
        real_fsync = posix_lock.os.fsync

        def fail_file_fsync(fd):
            nonlocal faulted
            st = os.fstat(fd)
            if not faulted and stat.S_ISREG(st.st_mode) and st.st_size > 0:
                faulted = True
                raise OSError("synthetic production file fsync")
            return real_fsync(fd)

        monkeypatch.setattr(posix_lock.os, "fsync", fail_file_fsync)

    receipt = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    ).submit_channel_reply(
        request
    )
    outbox = target / ".channel_reply" / "outbox"
    assert receipt.status == ChannelReplyStatus.DEAD.value
    assert faulted is True
    assert list(outbox.glob("*.json")) == []
    assert [path for path in outbox.iterdir() if path.name.endswith(".tmp")] == []


def test_production_target_write_fault_is_closed_and_never_publishes_partial_queue(
    tmp_path,
):
    target = tmp_path / "target-write-fault"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target, request_id="write-fault")

    def fail_publication(name, args, _kwargs):
        if name == "atomic_write_bytes" and args[1].endswith(".json"):
            raise OSError("synthetic publication fault")

    lock = _ObservedMutationLock(before=fail_publication)
    submitter = ChannelReplyTargetFileSubmitPort(
        target, mutation_lock=lock, now=lambda: NOW
    )
    receipt = submitter.submit_channel_reply(request)
    assert receipt.status == "dead"
    assert list((target / ".channel_reply" / "outbox").glob("*.json")) == []


class _RecordingSubmitPort:
    def __init__(self, *, status=ChannelReplyStatus.SENT.value):
        self.status = status
        self.calls: list[ChannelReplySubmitRequest] = []

    def submit_channel_reply(self, request):
        self.calls.append(request)
        return ChannelReplyReceipt(
            status=self.status,
            grant_ref=request.grant_ref,
            request_id=request.request_id,
            message=self.status,
        )


class _ExplodingSubmitPort:
    def submit_channel_reply(self, request):
        raise RuntimeError("simulated owner crash")


def _make_target_request(target, *, request_id="req-target"):
    grant, proof = OwnerReplyGrant.issue(
        target_agent_id="agent-1",
        target_agent_name="Target",
        target_protocol_version=PROTOCOL_VERSION,
        channel="telegram",
        anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
        created_at=NOW,
        expires_at=LATER,
    )
    request = _request(grant, proof, request_id=request_id)
    return grant, proof, request


def _create_target_capsule(target):
    return ChannelReplyTargetCapsule.create(
        target_workdir=target,
        target_agent_id="agent-1",
        target_agent_name="Target",
        created_at=NOW,
        expires_at=LATER,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )


def test_target_outbox_full_tuple_avoids_same_request_id_collision(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    first_grant, first_proof, first = _make_target_request(target, request_id="same")
    second_grant, second_proof, second = _make_target_request(target, request_id="same")
    assert first_grant.grant_id != second_grant.grant_id
    assert submitter.submit_channel_reply(first).status == "pending"
    assert submitter.submit_channel_reply(second).status == "pending"
    files = list((target / ".channel_reply" / "outbox").glob("*.json"))
    assert len(files) == 2
    stored_refs = {
        json.loads(path.read_text(encoding="utf-8"))["request"]["grant_ref"]
        for path in files
    }
    assert stored_refs == {first_grant.grant_ref, second_grant.grant_ref}
    assert first_proof != second_proof


def test_target_claim_pre_send_rollback_then_dispatches_once(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target)
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == "pending"
    outbox = next((target / ".channel_reply" / "outbox").glob("*.json"))
    claim = target / ".channel_reply" / "claims" / outbox.name
    os.replace(outbox, claim)  # crash immediately after atomic claim, before parse/dispatch
    port = _RecordingSubmitPort()
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert not claim.exists()
    assert (target / ".channel_reply" / "outbox" / outbox.name).exists()
    receipt = transport.drain_once()
    assert receipt.status == "sent"
    assert [call.request_id for call in port.calls] == [request.request_id]
    assert submitter.submit_channel_reply(request) == receipt


def test_target_dispatch_crash_recovers_ambiguous_without_calling_new_owner(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target)
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == "pending"
    crashing = ChannelReplyOwnerFileTransport(
        target,
        submit_port=_ExplodingSubmitPort(),
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    with pytest.raises(RuntimeError, match="simulated owner crash"):
        crashing.drain_once()
    assert len(list((target / ".channel_reply" / "claims").glob("*.json"))) == 1

    safe_port = _RecordingSubmitPort()
    ChannelReplyOwnerFileTransport(
        target,
        submit_port=safe_port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert safe_port.calls == []
    receipt = submitter.submit_channel_reply(request)
    assert receipt.status == ChannelReplyStatus.AMBIGUOUS.value
    assert len(list((target / ".channel_reply" / "claims").glob("*.json"))) == 0
    assert len(list((target / ".channel_reply" / "consumed").glob("*.json"))) == 1


def test_target_invalid_claim_is_sanitized_dead_letter_without_dispatch(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target)
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == "pending"
    outbox = next((target / ".channel_reply" / "outbox").glob("*.json"))
    outbox.write_text("{}", encoding="utf-8")
    outbox.chmod(0o600)
    port = _RecordingSubmitPort()
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert transport.drain_once() is None
    assert port.calls == []
    dead = next((target / ".channel_reply" / ".dead").glob("*.json"))
    rendered = dead.read_text(encoding="utf-8")
    assert "hello" not in rendered
    assert "proof" not in rendered
    duplicate = submitter.submit_channel_reply(request)
    assert duplicate.status == ChannelReplyStatus.DEAD.value
    assert list((target / ".channel_reply" / "outbox").glob("*.json")) == []


def test_target_consumed_without_receipt_is_terminal_and_never_requeued(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target)
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == "pending"
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=_RecordingSubmitPort(),
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert transport.drain_once().status == "sent"
    receipt_path = next((target / ".channel_reply" / "receipts").glob("*.json"))
    receipt_path.unlink()

    duplicate = submitter.submit_channel_reply(request)
    assert duplicate.status == ChannelReplyStatus.DEAD.value
    assert "terminal" in duplicate.message
    assert list((target / ".channel_reply" / "outbox").glob("*.json")) == []


def test_target_malformed_receipt_commits_dead_without_dispatch(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target)
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == "pending"
    outbox_path = next((target / ".channel_reply" / "outbox").glob("*.json"))
    receipt_path = target / ".channel_reply" / "receipts" / outbox_path.name
    receipt_path.write_text("{}", encoding="utf-8")
    receipt_path.chmod(0o600)
    port = _RecordingSubmitPort()
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )

    receipt = transport.drain_once()
    assert receipt.status == ChannelReplyStatus.DEAD.value
    assert port.calls == []
    assert submitter.submit_channel_reply(request) == receipt
    assert list((target / ".channel_reply" / ".dead").glob("*.json"))


def test_target_transport_retention_bounds_capsule_outbox_receipt_consumed_and_dead(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target, request_id="sent")
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    submitter.submit_channel_reply(request)
    port = _RecordingSubmitPort()
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert transport.drain_once().status == "sent"

    _grant2, _proof2, queued = _make_target_request(target, request_id="queued")
    submitter.submit_channel_reply(queued)
    queued_path = next(
        path
        for path in (target / ".channel_reply" / "outbox").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["request"]["request_id"] == "queued"
    )
    queued_path.write_text("{}", encoding="utf-8")
    queued_path.chmod(0o600)
    transport.drain_once()
    assert list((target / ".channel_reply" / ".dead").glob("*.json"))

    removed = transport.cleanup_retained(
        now="2026-08-20T12:00:00Z",
        retention_seconds=24 * 60 * 60,
    )
    assert removed >= 4
    assert not (target / ".channel_reply" / "active_capsule.json").exists()
    for name in ("outbox", "claims", "receipts", "consumed", ".dead"):
        assert list((target / ".channel_reply" / name).glob("*.json")) == []


def test_target_capsule_outbox_absent_valid_and_invalid(tmp_path):
    request_grant, proof = OwnerReplyGrant.issue(
        target_agent_id="agent-1",
        target_agent_name="Target",
        target_protocol_version=PROTOCOL_VERSION,
        channel="telegram",
        anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
        created_at=NOW,
        expires_at=LATER,
    )
    request = _request(request_grant, proof)
    submitter = ChannelReplyTargetFileSubmitPort(
        tmp_path,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == ChannelReplyStatus.DEAD.value

    capsule = ChannelReplyTargetCapsule.create(
        target_workdir=tmp_path,
        target_agent_id="agent-1",
        target_agent_name="Target",
        created_at=NOW,
        expires_at=LATER,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    queued = submitter.submit_channel_reply(request)
    assert queued.status == ChannelReplyStatus.PENDING.value
    outbox_files = list((tmp_path / ".channel_reply" / capsule.outbox_dir).glob("*.json"))
    assert len(outbox_files) == 1
    payload = json.loads(outbox_files[0].read_text(encoding="utf-8"))
    assert payload["request"]["text"] == "hello"

    active = tmp_path / ".channel_reply" / "active_capsule.json"
    payload = json.loads(active.read_text(encoding="utf-8"))
    payload["capability_marker"] = "old"
    active.write_text(json.dumps(payload), encoding="utf-8")
    assert submitter.submit_channel_reply(_request(request_grant, proof, request_id="req-2")).status == "dead"


def test_capability_marker_is_stable_and_reachable():
    marker = channel_reply_capability_marker()
    assert marker == {
        "marker": CAPABILITY_MARKER,
        "version": PROTOCOL_VERSION,
        "submit": "target-local-filesystem-capsule",
    }


def test_telegram_adapter_derives_anchor_and_returns_opaque_public_receipt(tmp_path):
    service = _Service()
    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path,
        service=service,
        target_agent_id="agent-1",
        target_agent_name="Target",
        now=lambda: NOW,
    )
    grant, proof = _issue_grant(adapter.store)

    receipt = adapter.submit_channel_reply(_request(grant, proof, text="reply text"))

    assert receipt.status == ChannelReplyStatus.SENT.value
    assert service.account.calls == [
        {
            "chat_id": 12345,
            "text": "[Target] reply text",
            "reply_to_message_id": 55,
            "kwargs": {},
        }
    ]
    public = receipt.to_public_dict()
    assert set(public) == {"status", "grant_ref", "request_id", "message", "public_ref"}
    assert isinstance(public["public_ref"], str)
    assert re.fullmatch(r"channel-reply:[A-Za-z0-9_-]{16}", public["public_ref"])
    assert public["public_ref"] not in {"telegram", "12345", "55", "77", "reply text", proof}
    rendered = json.dumps(public, sort_keys=True)
    for sensitive in ("telegram", "12345", "reply text", proof):
        assert sensitive not in rendered


def test_telegram_adapter_revalidates_at_sending_barrier_and_again_at_send_call(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    order: list[str] = []
    eligibility = iter((True, False))

    def validate_target_eligibility() -> bool:
        order.append("eligibility")
        return next(eligibility)

    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path,
        service=service,
        target_agent_id="agent-1",
        target_agent_name="Target",
        validate_target_eligibility=validate_target_eligibility,
        now=lambda: NOW,
    )
    grant, proof = _issue_grant(adapter.store)
    request = _request(grant, proof, text="reply text")
    original_mark_prepared = adapter.store.mark_prepared
    original_mark_sending = adapter.store.mark_sending

    def mark_prepared(*args, **kwargs):
        order.append("mark_prepared")
        return original_mark_prepared(*args, **kwargs)

    def mark_sending(*args, **kwargs):
        order.append("mark_sending")
        return original_mark_sending(*args, **kwargs)

    monkeypatch.setattr(adapter.store, "mark_prepared", mark_prepared)
    monkeypatch.setattr(adapter.store, "mark_sending", mark_sending)

    receipt = adapter.submit_channel_reply(request)

    assert receipt.status == "failed"
    assert receipt.message == "reply could not be prepared"
    assert order == [
        "mark_prepared",
        "eligibility",
        "mark_sending",
        "eligibility",
    ]
    assert service.account.calls == []


def test_end_to_end_target_outbox_owner_telegram_adapter_target_receipt(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    service = _Service()
    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path / "owner",
        service=service,
        target_agent_id="agent-1",
        target_agent_name="Target",
        now=lambda: NOW,
    )
    grant, proof = _issue_grant(adapter.store)
    request = _request(grant, proof, text="target reply")
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )

    assert submitter.submit_channel_reply(request).status == "pending"
    receipts = adapter.drain_target_outbox(target)
    assert [receipt.status for receipt in receipts] == ["sent"]
    assert service.account.calls == [
        {
            "chat_id": 12345,
            "text": "[Target] target reply",
            "reply_to_message_id": 55,
            "kwargs": {},
        }
    ]
    assert submitter.submit_channel_reply(request) == receipts[0]
    assert adapter.drain_target_outbox(target) == []
    assert len(service.account.calls) == 1
    assert len(list((target / ".channel_reply" / "receipts").glob("*.json"))) == 1
    assert len(list((target / ".channel_reply" / "consumed").glob("*.json"))) == 1


def test_ordinary_existing_behavior_surface_unchanged_except_new_intrinsic():
    intrinsic_names = tuple(INTRINSICS)
    assert "channel_reply" in intrinsic_names
    for existing in ("email", "system", "context", "psyche", "soul"):
        assert existing in intrinsic_names
    # Notification migrated to an always-on official host plugin on current main.
    assert "notification" not in intrinsic_names


class _CaptureTelegramAccount:
    """No-network double that deliberately reuses the real payload builder."""

    send_message = TelegramAccount.send_message

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []

    def _request(self, method: str, **kwargs):
        self.requests.append((method, kwargs))
        return {"message_id": 77}

    def _note_chat_message_id(self, _chat_id, _message_id) -> None:
        pass


class _CaptureTelegramService:
    def __init__(self) -> None:
        self.account = _CaptureTelegramAccount()
        self.account_lookups: list[str] = []

    def get_account(self, alias: str):
        self.account_lookups.append(alias)
        return self.account


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("chat_id", 0),
        ("chat_id", -1),
        ("chat_id", True),
        ("reply_to_message_id", 0),
        ("reply_to_message_id", -1),
        ("reply_to_message_id", True),
        ("account_alias", ""),
        ("account_alias", " owner "),
    ],
)
def test_telegram_invalid_private_anchor_fails_before_real_payload_builder(
    tmp_path,
    field,
    invalid,
):
    service = _CaptureTelegramService()
    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path,
        service=service,
        target_agent_id="agent-1",
        target_agent_name="Target",
        now=lambda: NOW,
    )
    anchor = {"account_alias": "owner", "chat_id": 12345, "reply_to_message_id": 55}
    anchor[field] = invalid
    grant, proof = _issue_grant(adapter.store, anchor=anchor)
    request = _request(grant, proof, request_id=f"bad-{field}")

    receipt = adapter.submit_channel_reply(request)

    assert receipt.status == ChannelReplyStatus.FAILED.value
    record = adapter.store.get_request(grant.grant_id, request.request_id)
    assert record is not None
    assert record.status is ChannelReplyStatus.FAILED
    assert service.account_lookups == []
    assert service.account.requests == []


def test_telegram_positive_anchor_is_mandatory_in_real_send_message_payload(tmp_path):
    service = _CaptureTelegramService()
    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path,
        service=service,
        target_agent_id="agent-1",
        target_agent_name="Target",
        now=lambda: NOW,
    )
    grant, proof = _issue_grant(adapter.store)

    receipt = adapter.submit_channel_reply(_request(grant, proof, request_id="anchored"))

    assert receipt.status == ChannelReplyStatus.SENT.value
    assert service.account_lookups == ["owner"]
    assert service.account.requests == [
        (
            "sendMessage",
            {
                "json": {
                    "chat_id": 12345,
                    "text": "[Target] hello",
                    "reply_to_message_id": 55,
                }
            },
        )
    ]


def test_target_publish_is_complete_before_owner_can_enumerate_it(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target, request_id="publication")
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    owner_port = _RecordingSubmitPort()
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=owner_port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    write_entered = threading.Event()
    release_write = threading.Event()
    original_write = channel_reply_core.os.write
    blocked = False

    def blocked_write(fd, data):
        nonlocal blocked
        maintenance_payload = b'"scope":"target"' in bytes(data)
        if not blocked and not maintenance_payload:
            blocked = True
            write_entered.set()
            assert release_write.wait(timeout=5)
        return original_write(fd, data)

    monkeypatch.setattr(channel_reply_core.os, "write", blocked_write)
    submitted: list[ChannelReplyReceipt] = []
    drained: list[ChannelReplyReceipt | None] = []
    producer = threading.Thread(
        target=lambda: submitted.append(submitter.submit_channel_reply(request))
    )
    producer.start()
    assert write_entered.wait(timeout=3)

    outbox = target / ".channel_reply" / "outbox"
    assert list(outbox.glob("*.json")) == []
    hidden = list(outbox.iterdir())
    assert len(hidden) == 1
    assert hidden[0].name.startswith(".") and hidden[0].name.endswith(".tmp")

    owner = threading.Thread(target=lambda: drained.append(transport.drain_once()))
    owner.start()
    time.sleep(0.05)
    assert owner.is_alive()  # serialized behind the incomplete hidden sibling
    release_write.set()
    producer.join(timeout=5)
    owner.join(timeout=5)

    assert not producer.is_alive()
    assert not owner.is_alive()
    assert [receipt.status for receipt in submitted] == [ChannelReplyStatus.PENDING.value]
    assert drained[0] is not None
    assert drained[0].status == ChannelReplyStatus.SENT.value
    assert [call.request_id for call in owner_port.calls] == [request.request_id]
    assert submitter.submit_channel_reply(request) == drained[0]
    assert list(outbox.iterdir()) == []


class _BlockedSubmitPort(_RecordingSubmitPort):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def submit_channel_reply(self, request):
        self.calls.append(request)
        self.entered.set()
        assert self.release.wait(timeout=5)
        return ChannelReplyReceipt(
            status=ChannelReplyStatus.SENT.value,
            grant_ref=request.grant_ref,
            request_id=request.request_id,
            message="sent",
        )


def test_target_second_constructor_terminalizes_blocked_dispatch_immutably(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target, request_id="blocked")
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == ChannelReplyStatus.PENDING.value
    blocked_port = _BlockedSubmitPort()
    first_transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=blocked_port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    returned: list[ChannelReplyReceipt] = []
    first = threading.Thread(target=lambda: returned.append(first_transport.drain_once()))
    first.start()
    assert blocked_port.entered.wait(timeout=3)

    recovery_port = _RecordingSubmitPort()
    ChannelReplyOwnerFileTransport(
        target,
        submit_port=recovery_port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    receipt_path = next((target / ".channel_reply" / "receipts").glob("*.json"))
    committed_bytes = receipt_path.read_bytes()
    committed = submitter.submit_channel_reply(request)
    assert committed.status == ChannelReplyStatus.AMBIGUOUS.value

    blocked_port.release.set()
    first.join(timeout=5)
    assert not first.is_alive()
    assert len(returned) == 1
    assert returned[0] == committed
    assert receipt_path.read_bytes() == committed_bytes
    assert recovery_port.calls == []
    assert [call.request_id for call in blocked_port.calls] == [request.request_id]
    assert submitter.submit_channel_reply(request) == committed


@pytest.mark.parametrize("fault_point", ["after_receipt", "after_consumed", "before_claim_removal"])
def test_target_terminal_crash_cuts_preserve_first_receipt_bytes(
    tmp_path,
    monkeypatch,
    fault_point,
):
    target = tmp_path / fault_point
    target.mkdir()
    _create_target_capsule(target)
    _grant, _proof, request = _make_target_request(target, request_id=fault_point)
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(request).status == ChannelReplyStatus.PENDING.value
    sending_port = _RecordingSubmitPort()
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=sending_port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    state_root = target / ".channel_reply"

    with monkeypatch.context() as fault:
        if fault_point in {"after_receipt", "after_consumed"}:
            original_atomic = channel_reply_core._atomic_private_json
            faulted = False
            fault_dir = "receipts" if fault_point == "after_receipt" else "consumed"

            def write_then_crash(path, payload):
                nonlocal faulted
                original_atomic(path, payload)
                if not faulted and path.parent.name == fault_dir:
                    faulted = True
                    raise RuntimeError(fault_point)

            fault.setattr(channel_reply_core, "_atomic_private_json", write_then_crash)
        else:
            faulted = False

            def crash_before_claim_removal(name, _args, _kwargs):
                nonlocal faulted
                if not faulted and name == "remove_owned_entry":
                    faulted = True
                    raise RuntimeError(fault_point)

            fault.setattr(
                transport,
                "_mutation_lock",
                _ObservedMutationLock(before=crash_before_claim_removal),
            )

        with pytest.raises(RuntimeError, match=fault_point):
            transport.drain_once()
        assert faulted is True

    receipt_path = next((state_root / "receipts").glob("*.json"))
    first_bytes = receipt_path.read_bytes()
    first_receipt = submitter.submit_channel_reply(request)
    assert first_receipt.status == ChannelReplyStatus.SENT.value
    assert len(list((state_root / "claims").glob("*.json"))) == 1

    recovery_port = _RecordingSubmitPort()
    ChannelReplyOwnerFileTransport(
        target,
        submit_port=recovery_port,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )

    assert recovery_port.calls == []
    assert receipt_path.read_bytes() == first_bytes
    assert submitter.submit_channel_reply(request) == first_receipt
    assert list((state_root / "claims").glob("*.json")) == []
    assert len(list((state_root / "consumed").glob("*.json"))) == 1
    assert [call.request_id for call in sending_port.calls] == [request.request_id]


@pytest.mark.parametrize("operation", ["revoke", "cleanup"])
@pytest.mark.parametrize(
    "corruption",
    ["malformed", "embedded_id", "mode", "hardlink", "symlink"],
)
def test_every_route_event_quarantine_caller_installs_nonrotating_tombstone(
    tmp_path,
    operation,
    corruption,
):
    if corruption == "symlink" and not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    root = tmp_path / f"{operation}-{corruption}"
    calls: list[str] = []
    store = ChannelReplyFileStore(root, mutation_lock=PosixChannelReplyStateLockAdapter())
    route_event_id = f"route-{operation}-{corruption}"
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    )
    assert grant is not None and proof and created
    event_path = root / "route_events" / f"{route_event_id}.json"
    backing: Path | None = None
    backing_bytes: bytes | None = None
    backing_mode: int | None = None

    if corruption == "malformed":
        event_path.write_text("{", encoding="utf-8")
    elif corruption == "embedded_id":
        data = json.loads(event_path.read_text(encoding="utf-8"))
        data["route_event_id"] = "different-route"
        event_path.write_text(json.dumps(data), encoding="utf-8")
    elif corruption == "mode":
        event_path.chmod(0o644)
    elif corruption == "hardlink":
        backing = tmp_path / f"{operation}-hardlink-backing.json"
        os.link(event_path, backing)
        backing_bytes = backing.read_bytes()
        backing_mode = stat.S_IMODE(backing.stat().st_mode)
    elif corruption == "symlink":
        backing = tmp_path / f"{operation}-symlink-backing.json"
        os.replace(event_path, backing)
        backing.chmod(0o644)
        backing_bytes = backing.read_bytes()
        backing_mode = stat.S_IMODE(backing.stat().st_mode)
        os.symlink(backing, event_path)

    if operation == "revoke":
        assert store.revoke_grant(grant.grant_ref) is True
    else:
        store.cleanup_retained(now=NOW)

    if backing is not None:
        assert backing.read_bytes() == backing_bytes
        assert stat.S_IMODE(backing.stat().st_mode) == backing_mode

    tombstone = json.loads(event_path.read_text(encoding="utf-8"))
    assert tombstone["route_event_id"] == route_event_id
    assert tombstone["decision"] == "quarantined"
    assert tombstone["grant_id"] is None
    assert tombstone["grant_ref"] is None
    assert tombstone["proof_digest"] is None
    assert tombstone["proof"] == ""
    assert store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == ["factory"]


@pytest.mark.parametrize("outcome", ["sent", "ambiguous"])
def test_one_use_terminal_outcome_immediately_retires_route_proof(tmp_path, outcome):
    calls: list[str] = []
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    route_event_id = f"route-{outcome}"
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    )
    assert grant is not None and proof and created

    def sender(_grant, _text):
        if outcome == "ambiguous":
            raise RuntimeError("possible send")
        return "owner-private-result"

    controller, _sent = _controller(store, sender=sender)
    receipt = controller.submit_channel_reply(_request(grant, proof, request_id=outcome))
    assert receipt.status == outcome

    event_path = tmp_path / "route_events" / f"{route_event_id}.json"
    raw = event_path.read_bytes()
    event = json.loads(raw)
    assert event["decision"] == "retired"
    assert event["proof"] == ""
    assert proof.encode("utf-8") not in raw
    assert store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == ["factory"]


def test_owner_quarantine_is_proof_free_and_far_future_cleanup_is_recursive(tmp_path):
    calls: list[str] = []
    store = ChannelReplyFileStore(tmp_path, mutation_lock=PosixChannelReplyStateLockAdapter())
    route_event_id = "route-proof-quarantine"
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    )
    assert grant is not None and proof and created
    event_path = tmp_path / "route_events" / f"{route_event_id}.json"
    corrupt = json.loads(event_path.read_text(encoding="utf-8"))
    corrupt["route_event_id"] = "wrong-embedded-id"
    event_path.write_text(json.dumps(corrupt), encoding="utf-8")
    event_path.chmod(0o600)

    assert store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == ["factory"]
    inspected = [event_path, *(path for path in (tmp_path / ".dead").rglob("*") if path.is_file())]
    assert len(inspected) >= 2
    for path in inspected:
        assert proof.encode("utf-8") not in path.read_bytes()

    removed = store.cleanup_retained(
        now="2126-08-09T12:00:00Z",
        retention_seconds=0,
        route_event_tombstone_seconds=0,
    )
    assert removed >= len(inspected)
    assert not event_path.exists()
    assert [path for path in (tmp_path / ".dead").rglob("*") if path.is_file()] == []


def test_channel_reply_ltp_v2_summary_control_schema_and_manual_are_truthful():
    assert "channel_reply" in _LTP_V2_MIGRATED_FAMILIES
    assert summary_requested({"summarize": True}, "channel_reply") is True
    assert summary_requested({"summarize": False}, "channel_reply") is False
    assert summary_requested({"summarize": "true"}, "channel_reply") is False
    assert summary_requested({"summarize": True}, "submit") is False
    assert summary_requested({"summarize": True}, "channel-reply") is False

    schema = channel_reply.get_schema()
    assert "summarize" in schema["properties"]
    for action_input in schema["properties"]["input"]["oneOf"]:
        assert "summarize" not in action_input.get("properties", {})
    manual = (
        Path(channel_reply.__file__).parent / "manual" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "short-result family" in manual
    assert "summarize=false" in manual
    assert "exact submit receipt or manual text" in manual
    assert "family has no settings file" in manual
    assert "Neither `submit` nor `manual`" in manual


def test_state_inventory_is_complete_and_drives_atomic_writers_and_temp_grammar():
    inventory = channel_reply_core.CHANNEL_REPLY_STATE_INVENTORY
    by_kind = channel_reply_core.CHANNEL_REPLY_STATE_BY_KIND
    expected_kinds = {
        "owner_mutation_lock",
        "owner_grant",
        "owner_request",
        "owner_route_event",
        "owner_route_decision",
        "owner_dead",
        "owner_maintenance",
        "owner_cleanup_progress",
        "target_mutation_lock",
        "target_maintenance",
        "target_cleanup_progress",
        "target_capsule",
        "target_outbox",
        "target_claim",
        "target_receipt",
        "target_consumed",
        "target_dead",
    }
    assert {item.kind for item in inventory} == expected_kinds
    assert set(by_kind) == expected_kinds
    assert channel_reply_core.OWNER_STATE_DIRECTORIES == {
        "grants",
        "requests",
        "route_events",
        "route_decisions",
        ".dead",
    }
    assert channel_reply_core.TARGET_STATE_DIRECTORIES == {
        "outbox",
        "claims",
        "receipts",
        "consumed",
        ".dead",
    }
    for item in inventory:
        assert item.scope in {"owner", "target"}
        assert item.directory
        assert item.canonical_pattern
        assert item.owned_temp_pattern
        assert item.sensitivity
        assert item.writer_algorithm
        assert item.interruption_cuts
        assert item.recovery_owner
        assert item.terminalization
        assert item.retention

    canonical_examples = {
        "owner_grant": "grant_1.json",
        "owner_request": f"{'a' * 64}.json",
        "owner_route_event": "route_1.json",
        "owner_route_decision": "route_1.json",
        "owner_dead": f"bad.json.{'b' * 32}.dead",
        "owner_maintenance": "owner-maintenance-progress.json",
        "target_maintenance": "target-maintenance-progress.json",
        "target_capsule": "active_capsule.json",
        "target_outbox": f"{'c' * 64}.json",
        "target_claim": f"{'c' * 64}.json",
        "target_receipt": f"{'c' * 64}.json",
        "target_consumed": f"{'c' * 64}.json",
        "target_dead": f"{'d' * 64}.{'e' * 32}.json",
    }
    for kind, canonical in canonical_examples.items():
        spec = by_kind[kind]
        assert re.fullmatch(spec.canonical_pattern, canonical)
        hidden = f".{canonical}.123.{'f' * 32}.tmp"
        match = re.fullmatch(spec.owned_temp_pattern, hidden)
        assert match is not None and match.group("canonical") == canonical

    source_path = Path(channel_reply_core.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("_atomic_private_json", "_atomic_create_private_json"):
        calls = [
            node
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_state_kind_for_path"
        ]
        assert len(calls) == 1
    assert "_owned_temp_canonical" in {
        node.func.id
        for node in ast.walk(functions["_reconcile_owned_temps_page"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_parent_ltp_v2_inventory_markers_match_executable_exact_set():
    expected = {
        "web",
        "mcp",
        "plugin",
        "file",
        "vision",
        "avatar",
        "soul",
        "shell",
        "notification",
        "system",
        "daemon",
        "email",
        "task_card",
        "channel_reply",
        "context",
        "psyche",
    }
    assert set(_LTP_V2_MIGRATED_FAMILIES) == expected
    root = Path(channel_reply_core.__file__).parents[2] / "tools"
    marker = re.compile(r"<!-- LTP_V2_CURRENT_FAMILIES: ([a-z_,]+) -->")
    names = {entry.name for entry in root.iterdir()}
    assert {"CONTRACT.md", "ANATOMY.md"}.issubset(names)
    assert "Anatomy.md" not in names
    for path in (root / "CONTRACT.md", root / "ANATOMY.md"):
        assert path.is_file()
        matches = marker.findall(path.read_text(encoding="utf-8"))
        assert len(matches) == 1
        assert set(matches[0].split(",")) == expected


@pytest.mark.parametrize("timestamp_shape", ["absent", "non-string", "unparsable"])
def test_cleanup_immediately_sanitizes_malformed_proof_text_canonical_state(
    tmp_path, timestamp_shape
):
    sentinel = f"SECRET-PROOF-TEXT-{timestamp_shape}"
    owner_root = tmp_path / "owner"
    store = ChannelReplyFileStore(owner_root, mutation_lock=PosixChannelReplyStateLockAdapter())
    grant_payload = {
        "version": PROTOCOL_VERSION,
        "grant_id": f"bad_{timestamp_shape.replace('-', '_')}",
        "anchor": {"note": sentinel},
        "proof": sentinel,
    }
    request_payload = {
        "version": PROTOCOL_VERSION,
        "grant_id": "bad",
        "request_id": "bad",
        "text": sentinel,
        "proof": sentinel,
    }
    if timestamp_shape == "non-string":
        grant_payload["expires_at"] = 123
        request_payload["created_at"] = 123
    elif timestamp_shape == "unparsable":
        grant_payload["expires_at"] = "not-a-time"
        request_payload["created_at"] = "not-a-time"
    grant_path = owner_root / "grants" / f"{grant_payload['grant_id']}.json"
    request_path = owner_root / "requests" / f"{'a' * 64}.json"
    for path, payload in ((grant_path, grant_payload), (request_path, request_payload)):
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)

    store.cleanup_retained(now=NOW, retention_seconds=7 * 24 * 60 * 60)
    assert not grant_path.exists()
    assert not request_path.exists()
    for path in owner_root.rglob("*"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes()

    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    target_root = target / ".channel_reply"
    malformed_outbox = target_root / "outbox" / f"{'b' * 64}.json"
    malformed_outbox.write_text(
        json.dumps(
            {
                "version": PROTOCOL_VERSION,
                "submitted_at": NOW,
                "text": sentinel,
                "proof": sentinel,
            }
        ),
        encoding="utf-8",
    )
    malformed_outbox.chmod(0o600)
    unrelated = target_root / "outbox" / "operator-note.json"
    unrelated.write_text(sentinel, encoding="utf-8")
    unrelated.chmod(0o600)
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=ClosedChannelReplySubmitPort(),
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    transport.cleanup_retained(now=NOW)
    assert not malformed_outbox.exists()
    assert unrelated.read_text(encoding="utf-8") == sentinel


def test_exact_owned_orphan_temps_are_removed_without_touching_unknown_hidden_names(tmp_path):
    sentinel = b"ORPHAN-PROOF-AND-REPLY-TEXT"
    owner_root = tmp_path / "owner"
    store = ChannelReplyFileStore(owner_root, mutation_lock=PosixChannelReplyStateLockAdapter())
    owner_dead_source = owner_root / ".dead" / "grants"
    owner_dead_source.mkdir(mode=0o700)
    owner_temp_specs = (
        (owner_root / "grants", "orphan.json"),
        (owner_root / "requests", f"{'a' * 64}.json"),
        (owner_root / "route_events", "route-orphan.json"),
        (owner_root / "route_decisions", "route-orphan.json"),
        (owner_dead_source, f"bad.json.{'b' * 32}.dead"),
    )
    owner_hidden = []
    for directory, canonical in owner_temp_specs:
        path = directory / f".{canonical}.4242.{'c' * 32}.tmp"
        path.write_bytes(sentinel)
        path.chmod(0o600)
        owner_hidden.append(path)
    owner_unknown = owner_root / "grants" / ".operator-owned.tmp"
    owner_unknown.write_bytes(sentinel)
    owner_unknown.chmod(0o600)
    for _ in range(160):
        assert store.get_grant("channel-reply-v1:absent") is None
        if all(not path.exists() for path in owner_hidden):
            break
    assert all(not path.exists() for path in owner_hidden)
    assert owner_unknown.read_bytes() == sentinel

    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    target_root = target / ".channel_reply"
    target_temp_specs = (
        (target_root, "active_capsule.json"),
        (target_root / "outbox", f"{'d' * 64}.json"),
        (target_root / "claims", f"{'d' * 64}.json"),
        (target_root / "receipts", f"{'d' * 64}.json"),
        (target_root / "consumed", f"{'d' * 64}.json"),
        (target_root / ".dead", f"{'d' * 64}.{'e' * 32}.json"),
    )
    target_hidden = []
    for directory, canonical in target_temp_specs:
        path = directory / f".{canonical}.4343.{'f' * 32}.tmp"
        path.write_bytes(sentinel)
        path.chmod(0o600)
        target_hidden.append(path)
    target_unknown = target_root / ".operator-owned.tmp"
    target_unknown.write_bytes(sentinel)
    target_unknown.chmod(0o600)
    grant, proof = OwnerReplyGrant.issue(
        target_agent_id="agent-1",
        target_agent_name="Target",
        target_protocol_version=PROTOCOL_VERSION,
        channel="telegram",
        anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
        created_at=NOW,
        expires_at=LATER,
    )
    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    request = _request(grant, proof)
    for _ in range(160):
        assert submitter.submit_channel_reply(request).status == "pending"
        if all(not path.exists() for path in target_hidden):
            break
    assert all(not path.exists() for path in target_hidden)
    assert target_unknown.read_bytes() == sentinel

    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=ClosedChannelReplySubmitPort(),
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    transport.cleanup_retained(now="2126-08-09T12:00:00Z", retention_seconds=0)
    assert not any(
        path.name.endswith(".tmp") and path != target_unknown
        for path in target_root.rglob("*")
    )
    assert target_unknown.read_bytes() == sentinel


@pytest.mark.parametrize(
    "operation",
    ["issue", "revoke", "cleanup", "retire", "constructor", "sent", "ambiguous"],
)
def test_nonempty_nested_route_event_obstruction_never_remints_or_interrupts_terminal_truth(
    tmp_path, operation
):
    calls: list[str] = []
    root = tmp_path / operation
    store = ChannelReplyFileStore(root, mutation_lock=PosixChannelReplyStateLockAdapter())
    route_event_id = f"route-obstruct-{operation}"
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    )
    assert grant is not None and proof and created
    event_path = root / "route_events" / f"{route_event_id}.json"
    event_path.unlink()
    event_path.mkdir(mode=0o700)
    nested = event_path / "nested"
    nested.mkdir(mode=0o700)
    child = nested / "do-not-touch"
    child.write_bytes(b"BACKING-DIRECTORY-CHILD")
    child.chmod(0o600)
    child_before = (child.read_bytes(), stat.S_IMODE(child.stat().st_mode))

    terminal_receipt = None
    send_calls: list[str] = []
    if operation == "issue":
        assert store.issue_or_reuse_grant(
            route_event_id=route_event_id,
            grant_factory=_route_factory(calls=calls),
            now=NOW,
        ) == (None, None, False)
    elif operation == "revoke":
        assert store.revoke_grant(grant.grant_ref) is True
    elif operation == "cleanup":
        store.cleanup_retained(now=NOW)
    elif operation == "retire":
        store._retire_route_event_for_grant(
            replace(grant, claimed_request_id="req-retire", consumed_request_id="req-retire")
        )
    elif operation == "constructor":
        store = ChannelReplyFileStore(root, mutation_lock=PosixChannelReplyStateLockAdapter())
    else:
        def sender(_grant, text):
            send_calls.append(text)
            if operation == "ambiguous":
                raise RuntimeError("possible send")
            return "owner-private-result"

        controller, _ = _controller(store, sender=sender)
        request = _request(grant, proof, request_id=f"req-{operation}")
        terminal_receipt = controller.submit_channel_reply(request)
        assert terminal_receipt.status == operation
        assert send_calls == ["hello"]
        assert controller.submit_channel_reply(request) == terminal_receipt
        assert send_calls == ["hello"]
        terminal_record = store.get_request(grant.grant_id, request.request_id)
        assert terminal_record is not None and terminal_record.receipt == terminal_receipt
        persisted_grant = store.get_grant(grant.grant_ref)
        assert persisted_grant is not None
        assert persisted_grant.consumed_request_id == request.request_id

    assert (child.read_bytes(), stat.S_IMODE(child.stat().st_mode)) == child_before
    decision_path = root / "route_decisions" / f"{route_event_id}.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["decision"] in {"quarantined", "revoked", "retired"}
    assert set(decision) == {
        "version",
        "route_event_id",
        "route_event_digest",
        "authority_digest",
        "decision",
        "created_at",
        "updated_at",
    }
    decision_raw = decision_path.read_bytes()
    assert proof.encode() not in decision_raw
    assert b"BACKING-DIRECTORY-CHILD" not in decision_raw

    child.unlink()
    nested.rmdir()
    event_path.rmdir()
    assert store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == ["factory"]


@pytest.mark.parametrize("shape", ["symlink", "hardlink"])
def test_rejected_route_backing_inode_is_not_read_chmoded_or_modified(tmp_path, shape):
    calls: list[str] = []
    root = tmp_path / shape
    store = ChannelReplyFileStore(root, mutation_lock=PosixChannelReplyStateLockAdapter())
    route_event_id = f"route-backing-{shape}"
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    )
    assert grant is not None and proof and created
    event_path = root / "route_events" / f"{route_event_id}.json"
    event_path.unlink()
    outside = tmp_path / f"outside-{shape}"
    outside.write_bytes(b"OUTSIDE-BACKING-SENTINEL")
    outside.chmod(0o640)
    before = (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode))
    if shape == "symlink":
        event_path.symlink_to(outside)
    else:
        os.link(outside, event_path)

    assert store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == ["factory"]
    assert (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode)) == before
    assert not event_path.is_symlink()
    event_path.unlink()
    assert store.issue_or_reuse_grant(
        route_event_id=route_event_id,
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    ) == (None, None, False)
    assert calls == ["factory"]


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "fork"), reason="native POSIX fork proof")
@pytest.mark.parametrize(
    "cut",
    [
        "after-temp-fsync-before-link",
        "after-link-before-directory-fsync",
        "after-directory-fsync-before-hidden-unlink",
        "after-hidden-unlink",
    ],
)
def test_posix_child_process_outbox_publication_cuts_recover_exactly_once(tmp_path, cut):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    lock = PosixChannelReplyStateLockAdapter()
    target = tmp_path / cut
    target.mkdir()
    ChannelReplyTargetCapsule.create(
        target_workdir=target,
        target_agent_id="agent-1",
        target_agent_name="Target",
        created_at=NOW,
        expires_at=LATER,
        mutation_lock=lock,
    )
    owner_store = ChannelReplyFileStore(tmp_path / f"owner-{cut}", mutation_lock=lock)
    grant, proof = _issue_grant(owner_store)
    request = _request(grant, proof, request_id=f"req-{cut}")
    identity = channel_reply_core._request_identity_digest(grant.grant_id, request.request_id)
    canonical_name = f"{identity}.json"
    payload = {
        "version": PROTOCOL_VERSION,
        "request": channel_reply_core.request_to_record(request),
        "submitted_at": NOW,
    }

    pid = os.fork()
    if pid == 0:
        _child_crash_outbox_publication(target / ".channel_reply", canonical_name, payload, cut)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    controller, sent = _controller(owner_store)
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=controller,
        mutation_lock=lock,
        now=lambda: NOW,
    )
    root = target / ".channel_reply"
    for _ in range(40):
        hidden = list((root / "outbox").glob(f".{canonical_name}.*.tmp"))
        if not hidden:
            break
        transport.recover_claims(max_records=0)
    assert hidden == []
    canonical = root / "outbox" / canonical_name
    if cut == "after-temp-fsync-before-link":
        assert not canonical.exists()
        assert transport.drain_once() is None
        assert sent == []
    else:
        assert canonical.exists()
        assert canonical.stat().st_nlink == 1
        parsed = channel_reply_core._read_outbox_request(
            canonical,
            expected_identity=identity,
        )
        assert parsed == request
        receipt = transport.drain_once()
        assert receipt is not None and receipt.status == ChannelReplyStatus.SENT.value
        assert sent == ["hello"]
        duplicate = ChannelReplyTargetFileSubmitPort(
            target,
            mutation_lock=lock,
            now=lambda: NOW,
        ).submit_channel_reply(request)
        assert duplicate == receipt
        transport.recover_claims()
        assert transport.drain_once() is None
        assert sent == ["hello"]

    transport.cleanup_retained(
        now="2126-08-09T12:00:00Z",
        retention_seconds=0,
    )
    assert not any(path.name.endswith(".tmp") for path in root.rglob("*"))


@pytest.mark.parametrize("shape", ["symlink", "hardlink"])
def test_exact_owned_hidden_conflict_is_unlinked_without_publishing_backing_bytes(
    tmp_path, shape
):
    target = tmp_path / shape
    target.mkdir()
    _create_target_capsule(target)
    grant, proof = OwnerReplyGrant.issue(
        target_agent_id="agent-1",
        target_agent_name="Target",
        target_protocol_version=PROTOCOL_VERSION,
        channel="telegram",
        anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
        created_at=NOW,
        expires_at=LATER,
    )
    request = _request(grant, proof, request_id=f"hidden-{shape}")
    identity = channel_reply_core._request_identity_digest(grant.grant_id, request.request_id)
    outbox = target / ".channel_reply" / "outbox"
    hidden = outbox / f".{identity}.json.5151.{'a' * 32}.tmp"
    outside = tmp_path / f"outside-hidden-{shape}"
    outside.write_bytes(b"ATTACKER-HIDDEN-PROOF-TEXT")
    outside.chmod(0o640)
    before = (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode))
    if shape == "symlink":
        hidden.symlink_to(outside)
    else:
        os.link(outside, hidden)

    submitter = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    for _ in range(40):
        receipt = submitter.submit_channel_reply(request)
        assert receipt.status == ChannelReplyStatus.PENDING.value
        if not channel_reply_core._path_lexists(hidden):
            break
    assert not channel_reply_core._path_lexists(hidden)
    assert (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode)) == before
    canonical = outbox / f"{identity}.json"
    assert channel_reply_core._read_outbox_request(
        canonical,
        expected_identity=identity,
    ) == request
    assert b"ATTACKER-HIDDEN-PROOF-TEXT" not in canonical.read_bytes()


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "fork"), reason="native POSIX fork proof")
def test_posix_native_mutation_lock_blocks_an_independent_process(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "native-lock"
    root.mkdir(mode=0o700)
    ready_read, ready_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_read)
        try:
            with PosixChannelReplyStateLockAdapter().exclusive(root):
                os.write(ready_write, b"1")
                time.sleep(0.25)
            os.close(ready_write)
            os._exit(0)
        except BaseException:
            os._exit(97)
    os.close(ready_write)
    try:
        assert os.read(ready_read, 1) == b"1"
    finally:
        os.close(ready_read)
    started = time.monotonic()
    with PosixChannelReplyStateLockAdapter().exclusive(root):
        elapsed = time.monotonic() - started
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    assert elapsed >= 0.15
    lock_path = root / ".channel-reply.lock"
    st = lock_path.lstat()
    assert stat.S_ISREG(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert getattr(st, "st_nlink", 1) == 1
    assert lock_path.read_bytes() == b""


@pytest.mark.skipif(os.name != "posix", reason="native POSIX lock proof")
def test_posix_lock_rejects_missing_root_without_creating_any_name(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "missing-root"
    before = {entry.name for entry in tmp_path.iterdir()}
    with pytest.raises(OSError):
        with PosixChannelReplyStateLockAdapter().exclusive(root):
            raise AssertionError("lock yielded for missing root")
    assert {entry.name for entry in tmp_path.iterdir()} == before
    assert not root.exists()


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "symlink"), reason="native POSIX symlink proof")
def test_target_submit_rejects_symlink_state_root_without_outside_mutation(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"OUTSIDE")
    sentinel.chmod(0o640)
    os.symlink(outside, target / ".channel_reply")
    before_names = sorted(path.relative_to(outside).as_posix() for path in outside.rglob("*"))
    before = (sentinel.read_bytes(), stat.S_IMODE(sentinel.stat().st_mode))

    grant, proof = OwnerReplyGrant.issue(
        target_agent_id="agent-1",
        target_agent_name="Target",
        target_protocol_version=PROTOCOL_VERSION,
        channel="telegram",
        anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
        created_at=NOW,
        expires_at=LATER,
    )
    receipt = ChannelReplyTargetFileSubmitPort(
        target,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
        now=lambda: NOW,
    ).submit_channel_reply(_request(grant, proof, request_id="root-link"))

    assert receipt.status == ChannelReplyStatus.DEAD.value
    assert sorted(path.relative_to(outside).as_posix() for path in outside.rglob("*")) == before_names
    assert (sentinel.read_bytes(), stat.S_IMODE(sentinel.stat().st_mode)) == before
    assert not (outside / ".channel-reply.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="native POSIX lock proof")
@pytest.mark.parametrize("shape", ["wrong-mode", "hardlink", "directory", "fifo", "symlink"])
def test_posix_lock_rejects_unsafe_existing_leaf_without_mutating_backing(tmp_path, shape):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    if shape == "symlink" and not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    if shape == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("fifo unavailable")
    root = tmp_path / f"root-{shape}"
    root.mkdir(mode=0o700)
    lock_path = root / ".channel-reply.lock"
    outside = tmp_path / f"outside-{shape}"
    if shape == "wrong-mode":
        lock_path.write_bytes(b"LOCK")
        lock_path.chmod(0o644)
        backing = lock_path
    elif shape == "hardlink":
        outside.write_bytes(b"LOCK")
        outside.chmod(0o600)
        os.link(outside, lock_path)
        backing = outside
    elif shape == "directory":
        lock_path.mkdir(mode=0o700)
        backing = lock_path
    elif shape == "fifo":
        os.mkfifo(lock_path, 0o600)
        backing = lock_path
    else:
        outside.write_bytes(b"LOCK")
        outside.chmod(0o600)
        os.symlink(outside, lock_path)
        backing = outside
    before_names = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    before_lstat = lock_path.lstat()
    before_bytes = backing.read_bytes() if backing.is_file() and not backing.is_symlink() else None
    before_mode = stat.S_IMODE(backing.stat().st_mode) if backing.exists() else None

    with pytest.raises(OSError):
        with PosixChannelReplyStateLockAdapter().exclusive(root):
            raise AssertionError("lock yielded for unsafe leaf")

    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before_names
    after_lstat = lock_path.lstat()
    assert (after_lstat.st_mode, after_lstat.st_ino, getattr(after_lstat, "st_nlink", 1)) == (
        before_lstat.st_mode,
        before_lstat.st_ino,
        getattr(before_lstat, "st_nlink", 1),
    )
    if before_bytes is not None:
        assert backing.read_bytes() == before_bytes
        assert stat.S_IMODE(backing.stat().st_mode) == before_mode


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_mutation_session_marker_identity_names_and_token_lifetime(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "session-root"
    root.mkdir(mode=0o700)
    adapter = PosixChannelReplyStateLockAdapter()
    with adapter.exclusive(root) as session:
        assert session.protocol_marker == "channel-reply-mutation-session/v1"
        assert session.root_identity.scheme == "posix"
        assert session.root is not None
        session.verify()
        for bad_name in ("", ".", "..", "a/b", "a\\b", "a\x00b", "C:ads"):
            with pytest.raises((TypeError, ValueError, OSError)):
                session.inspect(session.root, bad_name)
        child = session.open_directory(session.root, "child", create_private=True)
        assert session.inspect(session.root, "child").kind == "directory"
    with pytest.raises(OSError):
        session.inspect(session.root, "child")
    with adapter.exclusive(root) as other:
        with pytest.raises((TypeError, OSError)):
            other.inspect(child, "anything")


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_expected_root_and_lock_bytes_are_unchanged(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    lock_path = root / ".channel-reply.lock"
    lock_path.write_bytes(b"LOCK-BYTES")
    lock_path.chmod(0o600)
    adapter = PosixChannelReplyStateLockAdapter()
    with adapter.exclusive(root) as session:
        identity = session.root_identity
        assert lock_path.read_bytes() == b"LOCK-BYTES"
    replacement = tmp_path / "replacement"
    replacement.mkdir(mode=0o700)
    with pytest.raises(OSError):
        with adapter.exclusive(replacement, expected_root=identity):
            raise AssertionError("expected-root mismatch yielded")
    assert not (replacement / ".channel-reply.lock").exists()
    assert lock_path.read_bytes() == b"LOCK-BYTES"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_open_directory_rejects_renamed_or_replaced_child_token(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        child = session.open_directory(session.root, "outbox", create_private=True)
        (root / "outbox").rename(root / "outbox-original")
        (root / "outbox").mkdir(mode=0o700)
        with pytest.raises(OSError):
            session.scan(child, budget=DirectoryScanBudget())


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_inspect_scan_and_strict_read_are_metadata_first(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    (root / "ok").write_bytes(b"abc")
    (root / "ok").chmod(0o600)
    (root / "oversize").write_bytes(b"abcdef")
    (root / "oversize").chmod(0o600)
    (root / "hardlink-source").write_bytes(b"hard")
    (root / "hardlink-source").chmod(0o600)
    os.link(root / "hardlink-source", root / "hardlink")
    if hasattr(os, "symlink"):
        os.symlink(root / "ok", root / "link")
        os.symlink(root / "missing", root / "dangling")
    if hasattr(os, "mkfifo"):
        os.mkfifo(root / "fifo", 0o600)
    (root / "dir").mkdir(mode=0o700)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        batch = session.scan(session.root, budget=DirectoryScanBudget(inspections=3, candidates=2))
        assert batch.inspections <= 3
        assert len(batch.entries) <= 2
        assert not batch.complete
        ok = session.inspect(session.root, "ok")
        assert ok is not None and ok.private_regular_single_link
        assert session.read_bytes(session.root, "ok", max_bytes=3, expected=ok.identity) == b"abc"
        with pytest.raises(ValueError):
            session.read_bytes(session.root, "oversize", max_bytes=3)
        for name in ("hardlink", "dir", "link", "dangling", "fifo"):
            if (root / name).exists() or channel_reply_core._path_lexists(root / name):
                with pytest.raises((OSError, ValueError)):
                    session.read_bytes(session.root, name, max_bytes=16)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_atomic_write_move_remove_and_fsync(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"OUTSIDE")
    outside.chmod(0o600)
    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        replace = session.atomic_write_bytes(
            session.root,
            "entry.json",
            b"one",
            mode="atomic-replace",
        )
        assert replace.state == "created"
        assert (root / "entry.json").read_bytes() == b"one"
        replace = session.atomic_write_bytes(
            session.root,
            "entry.json",
            b"two",
            mode="atomic-replace",
        )
        assert replace.state == "replaced"
        assert (root / "entry.json").read_bytes() == b"two"
        created = session.atomic_write_bytes(
            session.root,
            "created.json",
            b"created",
            mode="atomic-create-hard-link",
        )
        assert created.state == "created"
        exists = session.atomic_write_bytes(
            session.root,
            "created.json",
            b"ignored",
            mode="atomic-create-hard-link",
        )
        assert exists.state == "exists"
        assert (root / "created.json").read_bytes() == b"created"
        info = session.inspect(session.root, "entry.json")
        moved = session.move_entry(
            session.root,
            "entry.json",
            session.root,
            "claim.json",
            expected_source=info.identity,
            disposition="destination-must-be-absent",
        )
        assert moved.state == "moved"
        assert not (root / "entry.json").exists()
        assert (root / "claim.json").read_bytes() == b"two"
        changed = session.move_entry(
            session.root,
            "claim.json",
            session.root,
            "other.json",
            expected_source=created.entry.identity,
            disposition="destination-must-be-absent",
        )
        assert changed.state == "source-changed"
        tree = session.open_directory(session.root, "tree", create_private=True)
        session.atomic_write_bytes(tree, "leaf", b"x", mode="atomic-replace")
        os.link(outside, root / "tree" / "outside-hardlink")
        if hasattr(os, "symlink"):
            os.symlink(outside, root / "tree" / "outside-link")
        result = session.remove_owned_entry(
            session.root,
            "tree",
            budget=OwnedRemovalBudget(inspections=2, removals=1, max_depth=8, candidates=8),
        )
        assert result.state in {"progress", "retryable"}
        for _ in range(10):
            result = session.remove_owned_entry(
                session.root,
                "tree",
                budget=OwnedRemovalBudget(inspections=16, removals=4, max_depth=8, candidates=8),
            )
            if result.state in {"absent", "removed"}:
                break
        assert not (root / "tree").exists()
        assert outside.read_bytes() == b"OUTSIDE"
        session.fsync_directory(session.root)
    assert not list(root.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_exceptional_exit_still_verifies_named_root(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    original = tmp_path / "root-original"
    with pytest.raises(OSError, match="named root replaced") as excinfo:
        with PosixChannelReplyStateLockAdapter().exclusive(root):
            root.rename(original)
            root.mkdir(mode=0o700)
            sentinel = root / "sentinel"
            sentinel.write_bytes(b"replacement")
            sentinel.chmod(0o600)
            raise RuntimeError("probe body failure")

    assert isinstance(excinfo.value.__context__, RuntimeError)
    assert (root / "sentinel").read_bytes() == b"replacement"
    assert original.is_dir()


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "symlink"), reason="native POSIX session proof")
def test_posix_session_rejects_named_root_and_lock_leaf_replacement_after_flock(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "sentinel").write_bytes(b"OUTSIDE")
    (outside / "sentinel").chmod(0o600)
    original = tmp_path / "root-original"
    with pytest.raises(OSError):
        with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
            root.rename(original)
            os.symlink(outside, root)
            with pytest.raises(OSError):
                session.atomic_write_bytes(session.root, "should-not-appear", b"x", mode="atomic-replace")
    assert not (outside / "should-not-appear").exists()
    assert (outside / "sentinel").read_bytes() == b"OUTSIDE"

    root.unlink()
    original.rename(root)
    with pytest.raises(OSError):
        with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
            (root / ".channel-reply.lock").unlink()
            replacement = root / ".channel-reply.lock"
            replacement.write_bytes(b"new")
            replacement.chmod(0o600)
            with pytest.raises(OSError):
                session.inspect(session.root, "anything")


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_strict_read_loops_and_rejects_eof_or_growth(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    payload = root / "payload"
    payload.write_bytes(b"abcdef")
    payload.chmod(0o600)
    real_read = posix_lock.os.read
    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        monkeypatch.setattr(posix_lock.os, "read", lambda fd, n: real_read(fd, min(n, 2)))
        assert session.read_bytes(session.root, "payload", max_bytes=6) == b"abcdef"

        first = True

        def early_eof(fd, n):
            nonlocal first
            if first:
                first = False
                return real_read(fd, min(n, 2))
            return b""

        monkeypatch.setattr(posix_lock.os, "read", early_eof)
        with pytest.raises(OSError, match="ended before expected size"):
            session.read_bytes(session.root, "payload", max_bytes=6)

        appended = False

        def grow_after_expected(fd, n):
            nonlocal appended
            data = real_read(fd, n)
            if not appended and data == b"abcdef":
                appended = True
                payload.write_bytes(b"abcdefg")
                payload.chmod(0o600)
            return data

        monkeypatch.setattr(posix_lock.os, "read", grow_after_expected)
        with pytest.raises(ValueError, match="exceeds read limit"):
            session.read_bytes(session.root, "payload", max_bytes=6)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
@pytest.mark.parametrize("fault", ["write", "fchmod", "file_fsync", "temp_validation", "publication"])
def test_posix_session_atomic_write_cleans_hidden_temp_on_failures(tmp_path, monkeypatch, fault):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / fault
    root.mkdir(mode=0o700)
    real_write_all = posix_lock._write_all
    real_fchmod = posix_lock.os.fchmod
    real_fsync = posix_lock.os.fsync
    real_require_private_lock = posix_lock._require_private_lock
    real_rename = posix_lock.os.rename

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        if fault == "write":
            def fail_write(fd, data):
                real_write_all(fd, data[:2])
                raise OSError("probe write fail")

            monkeypatch.setattr(posix_lock, "_write_all", fail_write)
        elif fault == "fchmod":
            def fail_fchmod(fd, mode):
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("probe fchmod fail")
                return real_fchmod(fd, mode)

            monkeypatch.setattr(posix_lock.os, "fchmod", fail_fchmod)
        elif fault == "file_fsync":
            def fail_file_fsync(fd):
                st = os.fstat(fd)
                if stat.S_ISREG(st.st_mode) and st.st_size == 4:
                    raise OSError("probe fsync fail")
                return real_fsync(fd)

            monkeypatch.setattr(posix_lock.os, "fsync", fail_file_fsync)
        elif fault == "temp_validation":
            def fail_validation(st):
                if stat.S_ISREG(st.st_mode) and st.st_size == 4:
                    raise OSError("probe validation fail")
                return real_require_private_lock(st)

            monkeypatch.setattr(posix_lock, "_require_private_lock", fail_validation)
        else:
            def fail_rename(src, dst, *args, **kwargs):
                if src.startswith(".target."):
                    raise OSError("probe publication fail")
                return real_rename(src, dst, *args, **kwargs)

            monkeypatch.setattr(posix_lock.os, "rename", fail_rename)
        with pytest.raises(OSError, match="probe|cleanup"):
            session.atomic_write_bytes(session.root, "target", b"body", mode="atomic-replace")

    assert not (root / "target").exists()
    assert [path.name for path in root.iterdir() if path.name.startswith(".target.")] == []


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_open_directory_create_fsyncs_child_then_parent_and_fails_closed(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    real_fsync = posix_lock.os.fsync
    calls: list[str] = []

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        root_ino = root.stat().st_ino

        def recording_fsync(fd):
            st = os.fstat(fd)
            calls.append("parent" if st.st_ino == root_ino else "child")
            return real_fsync(fd)

        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        session.open_directory(session.root, "created", create_private=True)
        assert calls[:2] == ["child", "parent"]

    for failing_call in ("child", "parent"):
        failing_root = tmp_path / f"fail-{failing_call}"
        failing_root.mkdir(mode=0o700)
        calls.clear()
        with PosixChannelReplyStateLockAdapter().exclusive(failing_root) as session:
            root_ino = failing_root.stat().st_ino

            def failing_fsync(fd):
                st = os.fstat(fd)
                label = "parent" if st.st_ino == root_ino else "child"
                calls.append(label)
                if label == failing_call:
                    raise OSError(f"probe {failing_call} fsync fail")
                return real_fsync(fd)

            monkeypatch.setattr(posix_lock.os, "fsync", failing_fsync)
            with pytest.raises(OSError, match=f"probe {failing_call} fsync fail"):
                session.open_directory(session.root, "created", create_private=True)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
@pytest.mark.parametrize(
    ("boundary", "destination_bytes"),
    [("source-to-staging-link", None), ("source-to-removal-rename", b"original")],
)
def test_posix_session_move_source_swap_preserves_replacement_and_destination(
    tmp_path,
    monkeypatch,
    boundary,
    destination_bytes,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / boundary
    root.mkdir(mode=0o700)
    source = root / "source"
    source.write_bytes(b"original")
    source.chmod(0o600)
    real_link = posix_lock.os.link
    real_rename = posix_lock.os.rename

    def replace_source(dir_fd):
        posix_lock.os.unlink("source", dir_fd=dir_fd)
        fd = posix_lock.os.open("source", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
        try:
            posix_lock.os.write(fd, b"swapped")
        finally:
            posix_lock.os.close(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        if boundary == "source-to-staging-link":
            def swap_before_link(src, dst, *args, **kwargs):
                if src == "source" and dst.startswith(".source."):
                    replace_source(kwargs["src_dir_fd"])
                return real_link(src, dst, *args, **kwargs)

            monkeypatch.setattr(posix_lock.os, "link", swap_before_link)
        else:
            def swap_before_rename(src, dst, *args, **kwargs):
                if src == "source" and ".source.remove." in dst:
                    replace_source(kwargs["src_dir_fd"])
                return real_rename(src, dst, *args, **kwargs)

            monkeypatch.setattr(posix_lock.os, "rename", swap_before_rename)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="destination-must-be-absent",
        )

    assert result.state == "source-changed"
    assert source.read_bytes() == b"swapped"
    if destination_bytes is None:
        assert not (root / "dest").exists()
    else:
        assert (root / "dest").read_bytes() == destination_bytes
    assert [path.name for path in root.iterdir() if path.name.startswith(".source.")] == []


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_expected_remove_source_swap_restores_replacement(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    victim = root / "victim"
    victim.write_bytes(b"original")
    victim.chmod(0o600)
    real_rename = posix_lock.os.rename

    def swap_before_quarantine(src, dst, *args, **kwargs):
        if src == "victim" and ".victim.remove." in dst:
            posix_lock.os.unlink("victim", dir_fd=kwargs["src_dir_fd"])
            fd = posix_lock.os.open("victim", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs["src_dir_fd"])
            try:
                posix_lock.os.write(fd, b"swapped")
            finally:
                posix_lock.os.close(fd)
        return real_rename(src, dst, *args, **kwargs)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "victim").identity
        monkeypatch.setattr(posix_lock.os, "rename", swap_before_quarantine)
        result = session.remove_owned_entry(
            session.root,
            "victim",
            budget=OwnedRemovalBudget(inspections=8, removals=4),
            expected=expected,
        )

    assert result.state == "retryable"
    assert result.error == "entry_changed"
    assert victim.read_bytes() == b"swapped"
    assert [path.name for path in root.iterdir() if path.name.startswith(".victim.")] == []


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_session_methods_post_verify_on_early_failures(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    (root / "large").write_bytes(b"abcdef")
    (root / "large").chmod(0o600)
    real_write_all = posix_lock._write_all

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        real_verify = session.verify
        calls: list[str] = []

        def counted_verify():
            calls.append("verify")
            return real_verify()

        monkeypatch.setattr(session, "verify", counted_verify)
        with pytest.raises(ValueError):
            session.read_bytes(session.root, "large", max_bytes=3)
        assert len(calls) == 2

        calls.clear()
        absent = session.move_entry(
            session.root,
            "absent",
            session.root,
            "dest",
            expected_source=session.root_identity,
            disposition="destination-must-be-absent",
        )
        assert absent.state == "source-absent"
        assert len(calls) == 3

        calls.clear()

        def fail_write(fd, data):
            real_write_all(fd, data[:1])
            raise OSError("probe write fail")

        monkeypatch.setattr(posix_lock, "_write_all", fail_write)
        with pytest.raises(OSError, match="probe write fail"):
            session.atomic_write_bytes(session.root, "out", b"data", mode="atomic-replace")
        assert len(calls) >= 2


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_descriptor_scandir_capability_failure_prevents_session(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    real_scandir = posix_lock.os.scandir

    def fail_fd_scandir(path):
        if isinstance(path, int):
            raise NotADirectoryError("probe descriptor scandir unsupported")
        return real_scandir(path)

    monkeypatch.setattr(posix_lock.os, "scandir", fail_fd_scandir)
    with pytest.raises(NotADirectoryError, match="descriptor scandir unsupported"):
        with PosixChannelReplyStateLockAdapter().exclusive(root):
            raise AssertionError("session yielded without descriptor scandir")
    assert not (root / ".channel-reply.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_descriptor_scandir_dup_is_closed_after_acquisition(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    real_dup = posix_lock.os.dup
    duplicated: list[int] = []

    def recording_dup(fd):
        out = real_dup(fd)
        duplicated.append(out)
        return out

    monkeypatch.setattr(posix_lock.os, "dup", recording_dup)
    for _ in range(8):
        with PosixChannelReplyStateLockAdapter().exclusive(root):
            pass

    assert len(duplicated) == 8
    for fd in duplicated:
        with pytest.raises(OSError):
            os.fstat(fd)


def _posix_private_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _posix_replace_name(posix_lock, dir_fd: int, name: str, data: bytes = b"swapped") -> None:
    posix_lock.os.unlink(name, dir_fd=dir_fd)
    fd = posix_lock.os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
    try:
        posix_lock.os.write(fd, data)
    finally:
        posix_lock.os.close(fd)


def _posix_hidden_bytes(root: Path, prefix: str) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.startswith(prefix) and path.is_file()
    }


@pytest.mark.skipif(
    os.name != "posix" or not (sys.platform == "darwin" or sys.platform.startswith("linux")),
    reason="native descriptor-relative no-replace proof",
)
@pytest.mark.parametrize("shape", ["regular", "directory"])
@pytest.mark.parametrize("collision", [False, True])
def test_posix_native_no_replace_rename_preserves_source_and_destination(
    tmp_path,
    shape,
    collision,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock

    source_parent = tmp_path / "source-parent"
    destination_parent = tmp_path / "destination-parent"
    source_parent.mkdir(mode=0o700)
    destination_parent.mkdir(mode=0o700)
    source = source_parent / "entry"
    destination = destination_parent / "entry"
    if shape == "regular":
        _posix_private_file(source, b"source-bytes")
        if collision:
            _posix_private_file(destination, b"destination-bytes")
    else:
        source.mkdir(mode=0o700)
        _posix_private_file(source / "payload", b"source-directory-bytes")
        if collision:
            destination.mkdir(mode=0o700)
            _posix_private_file(destination / "payload", b"destination-directory-bytes")

    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    source_fd = os.open(source_parent, flags)
    destination_fd = os.open(destination_parent, flags)
    try:
        if collision:
            with pytest.raises(FileExistsError) as caught:
                posix_lock._rename_no_replace(source_fd, "entry", destination_fd, "entry")
            assert caught.value.errno == errno.EEXIST
        else:
            posix_lock._rename_no_replace(source_fd, "entry", destination_fd, "entry")
    finally:
        os.close(destination_fd)
        os.close(source_fd)

    if collision:
        assert source.exists()
        assert destination.exists()
        if shape == "regular":
            assert source.read_bytes() == b"source-bytes"
            assert destination.read_bytes() == b"destination-bytes"
        else:
            assert (source / "payload").read_bytes() == b"source-directory-bytes"
            assert (destination / "payload").read_bytes() == b"destination-directory-bytes"
    else:
        assert not source.exists()
        assert destination.exists()
        if shape == "regular":
            assert destination.read_bytes() == b"source-bytes"
        else:
            assert (destination / "payload").read_bytes() == b"source-directory-bytes"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_no_replace_enosys_fails_before_session_mutation(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "sentinel", b"unchanged")

    def unavailable(*args):
        raise OSError(errno.ENOSYS, "probe no-replace unavailable")

    monkeypatch.setattr(posix_lock, "_rename_no_replace", unavailable)
    with pytest.raises(OSError) as caught:
        with PosixChannelReplyStateLockAdapter().exclusive(root):
            raise AssertionError("session yielded without no-replace capability")

    assert caught.value.errno == errno.ENOSYS
    assert "requires native no-replace rename" in str(caught.value)
    assert sorted(path.name for path in root.iterdir()) == ["sentinel"]
    assert (root / "sentinel").read_bytes() == b"unchanged"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_move_absent_destination_replacement_before_final_inspect_restores_source(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_no_replace = posix_lock._rename_no_replace
    real_fsync = posix_lock.os.fsync
    real_unlink = posix_lock.os.unlink
    events: list[tuple[str, str]] = []

    def recording_no_replace(source_fd, source_name, destination_fd, destination_name):
        result = real_no_replace(source_fd, source_name, destination_fd, destination_name)
        events.append(("no-replace", f"{source_name}->{destination_name}"))
        return result

    def recording_unlink(name, *args, **kwargs):
        result = real_unlink(name, *args, **kwargs)
        if isinstance(name, str) and name.startswith(".source."):
            events.append(("unlink", name))
        return result

    def recording_fsync(fd):
        events.append(("fsync", str(os.fstat(fd).st_ino)))
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        real_inspect = session.inspect
        replaced = False

        def replacing_final_inspect(parent, name):
            nonlocal replaced
            if name == "dest" and not replaced:
                replaced = True
                _posix_replace_name(posix_lock, parent.fd, "dest", b"postpublication-replacement")
            return real_inspect(parent, name)

        monkeypatch.setattr(posix_lock, "_rename_no_replace", recording_no_replace)
        monkeypatch.setattr(posix_lock.os, "unlink", recording_unlink)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        monkeypatch.setattr(session, "inspect", replacing_final_inspect)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="destination-must-be-absent",
        )

    assert replaced
    assert result.state == "source-changed"
    assert result.entry is not None
    assert (root / "source").read_bytes() == b"original"
    assert (root / "dest").read_bytes() == b"postpublication-replacement"
    assert _posix_hidden_bytes(root, ".source.") == {}
    durable_indices = [index for index, event in enumerate(events) if event[0] in {"no-replace", "unlink"}]
    assert durable_indices
    assert all(any(event[0] == "fsync" for event in events[index + 1:]) for index in durable_indices)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_replace_destination_replacement_before_final_inspect_restores_source(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    _posix_private_file(root / "dest", b"old-destination")
    real_no_replace = posix_lock._rename_no_replace
    real_fsync = posix_lock.os.fsync
    events: list[str] = []

    def recording_no_replace(source_fd, source_name, destination_fd, destination_name):
        result = real_no_replace(source_fd, source_name, destination_fd, destination_name)
        events.append("no-replace")
        return result

    def recording_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        real_inspect = session.inspect
        replaced = False

        def replacing_final_inspect(parent, name):
            nonlocal replaced
            if name == "dest" and not replaced:
                replaced = True
                _posix_replace_name(posix_lock, parent.fd, "dest", b"postpublication-replacement")
            return real_inspect(parent, name)

        monkeypatch.setattr(posix_lock, "_rename_no_replace", recording_no_replace)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        monkeypatch.setattr(session, "inspect", replacing_final_inspect)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="replace-destination-entry",
        )

    assert replaced
    assert result.state == "source-changed"
    assert result.entry is not None
    assert (root / "source").read_bytes() == b"original"
    assert (root / "dest").read_bytes() == b"postpublication-replacement"
    assert _posix_hidden_bytes(root, ".source.") == {}
    restore_index = events.index("no-replace")
    assert "fsync" in events[restore_index + 1:]


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_replace_destination_normal_success_cleans_source_backup(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"new-destination")
    _posix_private_file(root / "dest", b"old-destination")
    real_unlink = posix_lock.os.unlink
    real_fsync = posix_lock.os.fsync
    events: list[str] = []

    def recording_unlink(name, *args, **kwargs):
        result = real_unlink(name, *args, **kwargs)
        if isinstance(name, str) and name.startswith(".source.move.backup."):
            events.append("backup-unlink")
        return result

    def recording_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "unlink", recording_unlink)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="replace-destination-entry",
        )

    assert result.state == "moved"
    assert result.entry is not None and result.entry.identity == expected
    assert not (root / "source").exists()
    assert (root / "dest").read_bytes() == b"new-destination"
    assert _posix_hidden_bytes(root, ".source.") == {}
    backup_unlink_index = events.index("backup-unlink")
    assert "fsync" in events[backup_unlink_index + 1:]


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_move_absent_recovery_collision_preserves_canonical_hidden_and_destination(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_rename = posix_lock.os.rename
    real_stat = posix_lock.os.stat
    quarantined = False

    def colliding_rename(src, dst, *args, **kwargs):
        nonlocal quarantined
        result = real_rename(src, dst, *args, **kwargs)
        if src == "source" and ".source.remove." in dst:
            _posix_private_file(root / "source", b"canonical-collision")
            quarantined = True
        return result

    def fail_post_quarantine_stat(path, *args, **kwargs):
        if quarantined and isinstance(path, str) and ".source.remove." in path:
            raise OSError(errno.EIO, "injected post-quarantine inspection failure")
        return real_stat(path, *args, **kwargs)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", colliding_rename)
        monkeypatch.setattr(posix_lock.os, "stat", fail_post_quarantine_stat)
        with pytest.raises(OSError) as caught:
            session.move_entry(
                session.root,
                "source",
                session.root,
                "dest",
                expected_source=expected,
                disposition="destination-must-be-absent",
            )

    assert caught.value.errno == errno.EEXIST
    assert "collision" in str(caught.value)
    assert (root / "source").read_bytes() == b"canonical-collision"
    assert (root / "dest").read_bytes() == b"original"
    hidden = _posix_hidden_bytes(root, ".source.")
    assert len(hidden) == 2
    assert set(hidden.values()) == {b"original"}


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_replace_move_recovery_collision_preserves_both_objects(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    _posix_private_file(root / "dest", b"old-destination")
    real_rename = posix_lock.os.rename

    def fail_publication_with_collision(src, dst, *args, **kwargs):
        if isinstance(src, str) and src.startswith(".source.move.") and ".backup." not in src and dst == "dest":
            _posix_private_file(root / "source", b"canonical-collision")
            raise OSError(errno.EIO, "injected destination publication failure")
        return real_rename(src, dst, *args, **kwargs)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", fail_publication_with_collision)
        with pytest.raises(OSError) as caught:
            session.move_entry(
                session.root,
                "source",
                session.root,
                "dest",
                expected_source=expected,
                disposition="replace-destination-entry",
            )

    assert caught.value.errno == errno.EEXIST
    assert "collision" in str(caught.value)
    assert (root / "source").read_bytes() == b"canonical-collision"
    assert (root / "dest").read_bytes() == b"old-destination"
    hidden = _posix_hidden_bytes(root, ".source.move.")
    assert len(hidden) == 2
    assert set(hidden.values()) == {b"original"}


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_expected_remove_recovery_collision_raises_and_retains_quarantine(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "victim", b"original")
    real_rename = posix_lock.os.rename

    def quarantine_then_collide(src, dst, *args, **kwargs):
        result = real_rename(src, dst, *args, **kwargs)
        if src == "victim" and ".victim.remove." in dst:
            _posix_private_file(root / "victim", b"canonical-collision")
        return result

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "victim").identity
        monkeypatch.setattr(posix_lock.os, "rename", quarantine_then_collide)
        with pytest.raises(OSError) as caught:
            session.remove_owned_entry(
                session.root,
                "victim",
                budget=OwnedRemovalBudget(inspections=8, removals=0),
                expected=expected,
            )

    assert caught.value.errno == errno.EEXIST
    assert "collision" in str(caught.value)
    assert (root / "victim").read_bytes() == b"canonical-collision"
    hidden = _posix_hidden_bytes(root, ".victim.remove.")
    assert len(hidden) == 1
    assert list(hidden.values()) == [b"original"]


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_move_absent_rollback_never_unlinks_current_destination(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_rename = posix_lock.os.rename
    real_unlink = posix_lock.os.unlink
    swapped = False
    destination_unlink_attempted = False

    def swapping_rename(src, dst, *args, **kwargs):
        nonlocal swapped
        if src == "source" and ".source.remove." in dst and not swapped:
            _posix_replace_name(posix_lock, kwargs["src_dir_fd"], "source")
            swapped = True
        return real_rename(src, dst, *args, **kwargs)

    def reject_dest_unlink(name, *args, **kwargs):
        nonlocal destination_unlink_attempted
        if name == "dest":
            destination_unlink_attempted = True
            raise AssertionError("rollback must not unlink the current destination")
        return real_unlink(name, *args, **kwargs)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", swapping_rename)
        monkeypatch.setattr(posix_lock.os, "unlink", reject_dest_unlink)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="destination-must-be-absent",
        )

    assert swapped
    assert not destination_unlink_attempted
    assert result.state == "source-changed"
    assert result.entry is None
    assert (root / "source").read_bytes() == b"swapped"
    assert (root / "dest").read_bytes() == b"original"
    assert _posix_hidden_bytes(root, ".source.") == {}


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_move_absent_source_replacement_before_removal_preserves_expected_destination(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_rename = posix_lock.os.rename
    real_no_replace = posix_lock._rename_no_replace
    real_unlink = posix_lock.os.unlink
    real_fsync = posix_lock.os.fsync
    replaced = False
    events: list[str] = []

    def replace_source_before_removal(src, dst, *args, **kwargs):
        nonlocal replaced
        if src == "source" and ".source.remove." in dst and not replaced:
            _posix_replace_name(posix_lock, kwargs["src_dir_fd"], "source", b"source-replacement")
            replaced = True
        return real_rename(src, dst, *args, **kwargs)

    def recording_no_replace(source_fd, source_name, destination_fd, destination_name):
        result = real_no_replace(source_fd, source_name, destination_fd, destination_name)
        if ".source.remove." in source_name and destination_name == "source":
            events.append("restore")
        return result

    def recording_unlink(name, *args, **kwargs):
        result = real_unlink(name, *args, **kwargs)
        if isinstance(name, str) and name.startswith(".source.") and ".remove." not in name:
            events.append("staging-unlink")
        return result

    def recording_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", replace_source_before_removal)
        monkeypatch.setattr(posix_lock, "_rename_no_replace", recording_no_replace)
        monkeypatch.setattr(posix_lock.os, "unlink", recording_unlink)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="destination-must-be-absent",
        )

    assert replaced
    assert result.state == "source-changed"
    assert result.entry is None
    assert (root / "source").read_bytes() == b"source-replacement"
    assert (root / "dest").read_bytes() == b"original"
    assert _posix_hidden_bytes(root, ".source.") == {}
    for mutation in ("restore", "staging-unlink"):
        index = events.index(mutation)
        assert events[index + 1] == "fsync"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_move_absent_destination_replacement_before_source_quarantine_error_is_preserved(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_rename = posix_lock.os.rename
    real_unlink = posix_lock.os.unlink
    real_fsync = posix_lock.os.fsync
    replaced = False
    events: list[str] = []

    def replace_destination_then_fail(src, dst, *args, **kwargs):
        nonlocal replaced
        if src == "source" and ".source.remove." in dst and not replaced:
            _posix_replace_name(
                posix_lock,
                kwargs["src_dir_fd"],
                "dest",
                b"destination-replacement",
            )
            replaced = True
            raise OSError(errno.EIO, "injected source quarantine failure")
        return real_rename(src, dst, *args, **kwargs)

    def recording_unlink(name, *args, **kwargs):
        result = real_unlink(name, *args, **kwargs)
        if isinstance(name, str) and name.startswith(".source.") and ".remove." not in name:
            events.append("staging-unlink")
        return result

    def recording_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", replace_destination_then_fail)
        monkeypatch.setattr(posix_lock.os, "unlink", recording_unlink)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="destination-must-be-absent",
        )

    assert replaced
    assert result.state == "rejected"
    assert result.entry is None
    assert (root / "source").read_bytes() == b"original"
    assert (root / "dest").read_bytes() == b"destination-replacement"
    assert _posix_hidden_bytes(root, ".source.") == {}
    cleanup_index = events.index("staging-unlink")
    assert events[cleanup_index + 1] == "fsync"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_move_absent_both_canonical_names_replaced_retains_hidden_expected_source(
    tmp_path,
    monkeypatch,
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_rename = posix_lock.os.rename
    real_no_replace = posix_lock._rename_no_replace
    real_unlink = posix_lock.os.unlink
    real_fsync = posix_lock.os.fsync
    replaced = False
    hidden_unlinks: list[str] = []
    events: list[str] = []

    def replace_both_before_removal(src, dst, *args, **kwargs):
        nonlocal replaced
        if src == "source" and ".source.remove." in dst and not replaced:
            source_fd = kwargs["src_dir_fd"]
            _posix_replace_name(posix_lock, source_fd, "source", b"source-replacement")
            _posix_replace_name(posix_lock, source_fd, "dest", b"destination-replacement")
            replaced = True
        return real_rename(src, dst, *args, **kwargs)

    def recording_no_replace(source_fd, source_name, destination_fd, destination_name):
        result = real_no_replace(source_fd, source_name, destination_fd, destination_name)
        if ".source.remove." in source_name and destination_name == "source":
            events.append("restore")
        return result

    def recording_unlink(name, *args, **kwargs):
        result = real_unlink(name, *args, **kwargs)
        if isinstance(name, str) and name.startswith(".source."):
            hidden_unlinks.append(name)
        return result

    def recording_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", replace_both_before_removal)
        monkeypatch.setattr(posix_lock, "_rename_no_replace", recording_no_replace)
        monkeypatch.setattr(posix_lock.os, "unlink", recording_unlink)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        with pytest.raises(OSError) as caught:
            session.move_entry(
                session.root,
                "source",
                session.root,
                "dest",
                expected_source=expected,
                disposition="destination-must-be-absent",
            )

    assert replaced
    assert caught.value.errno == errno.EIO
    assert "rollback ambiguous" in str(caught.value)
    assert "hidden staging entry" in str(caught.value)
    assert hidden_unlinks == []
    restore_index = events.index("restore")
    assert events[restore_index + 1] == "fsync"
    assert (root / "source").read_bytes() == b"source-replacement"
    assert (root / "dest").read_bytes() == b"destination-replacement"
    hidden = _posix_hidden_bytes(root, ".source.")
    assert len(hidden) == 1
    hidden_name, hidden_bytes = next(iter(hidden.items()))
    assert hidden_name.startswith(".source.")
    assert ".remove." not in hidden_name
    assert hidden_name.endswith(".tmp")
    assert hidden_bytes == b"original"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
@pytest.mark.parametrize("failure", ["unlink", "fsync"])
def test_posix_move_absent_staging_cleanup_failure_is_not_moved(tmp_path, monkeypatch, failure):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / failure
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_unlink = posix_lock.os.unlink
    real_fsync = posix_lock.os.fsync
    staging_deleted = False

    def maybe_fail_unlink(name, *args, **kwargs):
        nonlocal staging_deleted
        if isinstance(name, str) and name.startswith(".source.") and ".remove." not in name:
            if failure == "unlink":
                raise OSError("probe staging cleanup fail")
            staging_deleted = True
        return real_unlink(name, *args, **kwargs)

    def maybe_fail_fsync(fd):
        if failure == "fsync" and staging_deleted:
            raise OSError("probe staging cleanup fsync fail")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "unlink", maybe_fail_unlink)
        monkeypatch.setattr(posix_lock.os, "fsync", maybe_fail_fsync)
        with pytest.raises(OSError, match="staging cleanup"):
            session.move_entry(
                session.root,
                "source",
                session.root,
                "dest",
                expected_source=expected,
                disposition="destination-must-be-absent",
            )

    assert (root / "dest").read_bytes() == b"original"
    assert not (root / "source").exists()
    hidden = [path.name for path in root.iterdir() if path.name.startswith(".source.")]
    assert (failure == "unlink" and hidden) or (failure == "fsync" and hidden == [])


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
@pytest.mark.parametrize("failure", ["rename", "fsync"])
def test_posix_move_absent_canonical_restore_failure_raises(tmp_path, monkeypatch, failure):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / failure
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_rename = posix_lock.os.rename
    real_no_replace = posix_lock._rename_no_replace
    real_fsync = posix_lock.os.fsync
    restore_happened = False

    def swapping_rename(src, dst, *args, **kwargs):
        if src == "source" and ".source.remove." in dst:
            _posix_replace_name(posix_lock, kwargs["src_dir_fd"], "source")
        return real_rename(src, dst, *args, **kwargs)

    def failing_no_replace(source_fd, source_name, destination_fd, destination_name):
        nonlocal restore_happened
        if ".source.remove." in source_name and destination_name == "source":
            if failure == "rename":
                raise OSError(errno.EIO, "probe move restore rename fail")
            restore_happened = True
        return real_no_replace(source_fd, source_name, destination_fd, destination_name)

    def maybe_fail_fsync(fd):
        if failure == "fsync" and restore_happened:
            raise OSError(errno.EIO, "probe move restore fsync fail")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", swapping_rename)
        monkeypatch.setattr(posix_lock, "_rename_no_replace", failing_no_replace)
        monkeypatch.setattr(posix_lock.os, "fsync", maybe_fail_fsync)
        with pytest.raises(OSError, match="absent-move source restore"):
            session.move_entry(
                session.root,
                "source",
                session.root,
                "dest",
                expected_source=expected,
                disposition="destination-must-be-absent",
            )

    assert (root / "dest").read_bytes() == b"original"
    hidden = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.startswith(".source.")
    }
    if failure == "rename":
        assert not (root / "source").exists()
        assert set(hidden.values()) == {b"original", b"swapped"}
    else:
        assert (root / "source").read_bytes() == b"swapped"
        assert list(hidden.values()) == [b"original"]


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_move_absent_cleanup_deletion_is_followed_by_parent_fsync(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    real_unlink = posix_lock.os.unlink
    real_fsync = posix_lock.os.fsync
    events: list[tuple[str, str]] = []

    def recording_unlink(name, *args, **kwargs):
        events.append(("unlink", str(name)))
        return real_unlink(name, *args, **kwargs)

    def recording_fsync(fd):
        events.append(("fsync", str(os.fstat(fd).st_ino)))
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "unlink", recording_unlink)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        result = session.move_entry(
            session.root,
            "source",
            session.root,
            "dest",
            expected_source=expected,
            disposition="destination-must-be-absent",
        )

    assert result.state == "moved"
    staging_unlinks = [
        index
        for index, event in enumerate(events)
        if event[0] == "unlink" and event[1].startswith(".source.") and ".remove." not in event[1]
    ]
    assert staging_unlinks
    assert all(any(event[0] == "fsync" for event in events[index + 1:]) for index in staging_unlinks)


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
@pytest.mark.parametrize("failure", ["rename", "fsync"])
def test_posix_expected_remove_restore_failure_is_not_retryable(tmp_path, monkeypatch, failure):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / failure
    root.mkdir(mode=0o700)
    _posix_private_file(root / "victim", b"original")
    real_rename = posix_lock.os.rename
    real_no_replace = posix_lock._rename_no_replace
    real_fsync = posix_lock.os.fsync
    restore_happened = False

    def swapping_rename(src, dst, *args, **kwargs):
        if src == "victim" and ".victim.remove." in dst:
            _posix_replace_name(posix_lock, kwargs["src_dir_fd"], "victim")
        return real_rename(src, dst, *args, **kwargs)

    def failing_no_replace(source_fd, source_name, destination_fd, destination_name):
        nonlocal restore_happened
        if ".victim.remove." in source_name and destination_name == "victim":
            if failure == "rename":
                raise OSError(errno.EIO, "probe restore rename fail")
            restore_happened = True
        return real_no_replace(source_fd, source_name, destination_fd, destination_name)

    def maybe_fail_fsync(fd):
        if failure == "fsync" and restore_happened:
            raise OSError(errno.EIO, "probe restore fsync fail")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "victim").identity
        monkeypatch.setattr(posix_lock.os, "rename", swapping_rename)
        monkeypatch.setattr(posix_lock, "_rename_no_replace", failing_no_replace)
        monkeypatch.setattr(posix_lock.os, "fsync", maybe_fail_fsync)
        with pytest.raises(OSError, match="expected-removal restore"):
            session.remove_owned_entry(
                session.root,
                "victim",
                budget=OwnedRemovalBudget(inspections=8, removals=4),
                expected=expected,
            )

    if failure == "rename":
        assert not (root / "victim").exists()
        hidden = [path for path in root.iterdir() if path.name.startswith(".victim.remove.")]
        assert len(hidden) == 1
        assert hidden[0].read_bytes() == b"swapped"
    else:
        assert (root / "victim").read_bytes() == b"swapped"
        assert [path.name for path in root.iterdir() if path.name.startswith(".victim.")] == []


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_expected_remove_restore_is_durable_before_retryable(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "victim", b"original")
    real_rename = posix_lock.os.rename
    real_no_replace = posix_lock._rename_no_replace
    real_fsync = posix_lock.os.fsync
    events: list[tuple[str, str]] = []

    def recording_rename(src, dst, *args, **kwargs):
        if src == "victim" and ".victim.remove." in dst:
            _posix_replace_name(posix_lock, kwargs["src_dir_fd"], "victim")
        result = real_rename(src, dst, *args, **kwargs)
        events.append(("rename", f"{src}->{dst}"))
        return result

    def recording_no_replace(source_fd, source_name, destination_fd, destination_name):
        result = real_no_replace(source_fd, source_name, destination_fd, destination_name)
        events.append(("no-replace", f"{source_name}->{destination_name}"))
        return result

    def recording_fsync(fd):
        events.append(("fsync", str(os.fstat(fd).st_ino)))
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "victim").identity
        monkeypatch.setattr(posix_lock.os, "rename", recording_rename)
        monkeypatch.setattr(posix_lock, "_rename_no_replace", recording_no_replace)
        monkeypatch.setattr(posix_lock.os, "fsync", recording_fsync)
        result = session.remove_owned_entry(
            session.root,
            "victim",
            budget=OwnedRemovalBudget(inspections=8, removals=4),
            expected=expected,
        )

    assert result.state == "retryable"
    assert result.error == "entry_changed"
    restore_indices = [
        index
        for index, event in enumerate(events)
        if event[0] == "no-replace" and event[1].endswith("->victim")
    ]
    assert len(restore_indices) == 1
    assert any(event[0] == "fsync" for event in events[restore_indices[0] + 1:])
    assert (root / "victim").read_bytes() == b"swapped"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
def test_posix_expected_remove_quarantine_cleanup_failure_raises(tmp_path, monkeypatch):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _posix_private_file(root / "victim", b"original")
    real_unlink = posix_lock.os.unlink

    def fail_quarantine_unlink(name, *args, **kwargs):
        if isinstance(name, str) and ".victim.remove." in name:
            raise OSError("probe quarantine cleanup fail")
        return real_unlink(name, *args, **kwargs)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "victim").identity
        monkeypatch.setattr(posix_lock.os, "unlink", fail_quarantine_unlink)
        with pytest.raises(OSError, match="expected removal cleanup ambiguous"):
            session.remove_owned_entry(
                session.root,
                "victim",
                budget=OwnedRemovalBudget(inspections=8, removals=4),
                expected=expected,
            )

    assert (root / "victim").read_bytes() == b"original"


@pytest.mark.skipif(os.name != "posix", reason="native POSIX session proof")
@pytest.mark.parametrize("failure", ["rename", "fsync"])
def test_posix_replace_destination_restore_failure_raises(tmp_path, monkeypatch, failure):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock
    from lingtai.adapters.posix.channel_reply_state_lock import (
        PosixChannelReplyStateLockAdapter,
    )

    root = tmp_path / failure
    root.mkdir(mode=0o700)
    _posix_private_file(root / "source", b"original")
    _posix_private_file(root / "dest", b"dest")
    real_rename = posix_lock.os.rename
    real_no_replace = posix_lock._rename_no_replace
    real_fsync = posix_lock.os.fsync
    restore_happened = False

    def swapping_rename(src, dst, *args, **kwargs):
        if src == "source" and ".source.move." in dst:
            _posix_replace_name(posix_lock, kwargs["src_dir_fd"], "source")
        return real_rename(src, dst, *args, **kwargs)

    def failing_no_replace(source_fd, source_name, destination_fd, destination_name):
        nonlocal restore_happened
        if ".source.move." in source_name and destination_name == "source":
            if failure == "rename":
                raise OSError(errno.EIO, "probe replace restore rename fail")
            restore_happened = True
        return real_no_replace(source_fd, source_name, destination_fd, destination_name)

    def maybe_fail_fsync(fd):
        if failure == "fsync" and restore_happened:
            raise OSError(errno.EIO, "probe replace restore fsync fail")
        return real_fsync(fd)

    with PosixChannelReplyStateLockAdapter().exclusive(root) as session:
        expected = session.inspect(session.root, "source").identity
        monkeypatch.setattr(posix_lock.os, "rename", swapping_rename)
        monkeypatch.setattr(posix_lock, "_rename_no_replace", failing_no_replace)
        monkeypatch.setattr(posix_lock.os, "fsync", maybe_fail_fsync)
        with pytest.raises(OSError, match="replace-move source restore"):
            session.move_entry(
                session.root,
                "source",
                session.root,
                "dest",
                expected_source=expected,
                disposition="replace-destination-entry",
            )

    assert (root / "dest").read_bytes() == b"dest"
    if failure == "fsync":
        assert (root / "source").read_bytes() == b"swapped"
        assert [path.name for path in root.iterdir() if path.name.startswith(".source.")] == []
    else:
        assert not (root / "source").exists()
        hidden = [path for path in root.iterdir() if path.name.startswith(".source.move.")]
        assert len(hidden) == 1
        assert hidden[0].read_bytes() == b"swapped"


def test_exact_canonical_outbox_directory_is_not_claimed_and_dead_marker_is_stable(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    root = target / ".channel_reply"
    identity = "a" * 64
    outbox_dir = root / "outbox" / f"{identity}.json"
    nested = outbox_dir / "nested"
    nested.mkdir(parents=True)
    sentinel = nested / "proof-body"
    sentinel.write_bytes(b"RAW-PROOF-AND-REPLY-BODY")
    outside = tmp_path / "outside"
    outside.write_bytes(b"OUTSIDE")
    outside.chmod(0o640)
    if hasattr(os, "symlink"):
        (nested / "outside-link").symlink_to(outside)
    os.link(outside, nested / "outside-hardlink")

    calls: list[ChannelReplySubmitRequest] = []
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=ClosedChannelReplySubmitPort(),
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    transport._submit_port = MagicMock(
        submit_channel_reply=lambda request: calls.append(request) or _fail_receipt(request, "nope")
    )

    for _ in range(3):
        assert transport.drain_once() is None
        assert calls == []
    dead_names = sorted(path.name for path in (root / ".dead").glob(f"{identity}.*.json"))
    assert len(dead_names) == 1
    marker_digest = channel_reply_core.hashlib.sha256(
        b"target-dead-v1\0" + identity.encode()
    ).hexdigest()[:32]
    assert dead_names[0] == f"{identity}.{marker_digest}.json"
    assert outbox_dir.is_dir()
    assert sentinel.read_bytes() == b"RAW-PROOF-AND-REPLY-BODY"
    assert not (root / "claims" / f"{identity}.json").exists()
    assert outside.read_bytes() == b"OUTSIDE"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o640


def test_exact_owned_temp_nonempty_directory_is_preserved_without_reading_backing(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    _create_target_capsule(target)
    outbox = target / ".channel_reply" / "outbox"
    identity = "b" * 64
    hidden = outbox / f".{identity}.json.4242.{'c' * 32}.tmp"
    nested = hidden / "nested"
    nested.mkdir(parents=True)
    (nested / "proof-body").write_bytes(b"OWNED-TEMP-RAW-PROOF-AND-REPLY")
    outside = tmp_path / "outside-temp"
    outside.write_bytes(b"OUTSIDE-TEMP")
    outside.chmod(0o640)
    os.link(outside, nested / "outside-hardlink")
    before = (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode))

    grant, proof = OwnerReplyGrant.issue(
        target_agent_id="agent-1",
        target_agent_name="Target",
        target_protocol_version=PROTOCOL_VERSION,
        channel="telegram",
        anchor={"account_alias": "owner", "chat_id": 1, "reply_to_message_id": 2},
        created_at=NOW,
        expires_at=LATER,
    )
    receipt = ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: NOW,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    ).submit_channel_reply(
        _request(grant, proof, request_id="temp-dir")
    )

    assert receipt.status == ChannelReplyStatus.PENDING.value
    assert hidden.is_dir()
    assert (nested / "proof-body").read_bytes() == b"OWNED-TEMP-RAW-PROOF-AND-REPLY"
    assert (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode)) == before


def test_bounded_owned_remover_reports_progress_and_is_idempotent(tmp_path):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    owned = root / "owned"
    current = owned
    for index in range(4):
        current.mkdir(mode=0o700)
        (current / f"leaf-{index}").write_bytes(b"RAW")
        current = current / f"dir-{index}"
    current.mkdir(mode=0o700)
    (current / "leaf-final").write_bytes(b"RAW")

    result = channel_reply_core._remove_owned_path(
        owned,
        budget=channel_reply_core.OwnedRemovalBudget(inspections=16, removals=2, max_depth=8),
    )
    assert result.state == "progress"
    assert result.inspections <= 16
    assert result.removals <= 2
    assert owned.exists()

    for _ in range(10):
        result = channel_reply_core._remove_owned_path(
            owned,
            budget=channel_reply_core.OwnedRemovalBudget(inspections=16, removals=2, max_depth=8),
        )
        if result.state in {"absent", "removed"}:
            break
    assert not owned.exists()
    assert channel_reply_core._remove_owned_path(owned).state == "absent"


@pytest.mark.parametrize("shape", ["regular", "empty-directory"])
def test_inventory_generated_exact_owned_temp_leaves_converge(tmp_path, shape):
    from lingtai.adapters.posix.channel_reply_state_lock import PosixChannelReplyStateLockAdapter

    root = tmp_path / f"root-{shape}"
    root.mkdir(mode=0o700)
    directory = root / "records"
    directory.mkdir(mode=0o700)
    canonical = f"{'a' * 64}.json"
    hidden = directory / f".{canonical}.1111.{'3' * 32}.tmp"
    if shape == "regular":
        hidden.write_bytes(b"RAW-PROOF-TEXT")
        hidden.chmod(0o600)
    else:
        hidden.mkdir(mode=0o700)
    unknown = directory / ".unknown-target-outbox.tmp"
    unknown.mkdir(mode=0o700)
    assert channel_reply_core._owned_temp_canonical(hidden.name, ("target_outbox",)) == canonical

    progress = root / "target-maintenance-progress.json"
    lock = PosixChannelReplyStateLockAdapter()
    for _ in range(16):
        with lock.exclusive(root) as session:
            with channel_reply_core._bind_mutation_session(root, session):
                channel_reply_core._reconcile_owned_temps_page(
                    progress,
                    scope="target",
                    surfaces=(("records", directory, ("target_outbox",)),),
                    max_inspections=1,
                )
        if not hidden.exists():
            break
    assert not hidden.exists()
    assert unknown.exists()


@pytest.mark.parametrize("scope", ["owner", "target"])
def test_scheduled_owned_temp_nested_obstruction_is_constant_bounded_and_preserved(
    tmp_path, monkeypatch, scope
):
    from lingtai.adapters.posix import channel_reply_state_lock as posix_lock

    if scope == "owner":
        root = tmp_path / "owner-nested-obstruction"
        store = ChannelReplyFileStore(root, mutation_lock=PosixChannelReplyStateLockAdapter())
        canonical = "owner-maintenance-progress.json"
        hidden = root / f".{canonical}.4242.{'c' * 32}.tmp"
    else:
        target = tmp_path / "target-nested-obstruction"
        target.mkdir(mode=0o700)
        _create_target_capsule(target)
        store = ChannelReplyOwnerFileTransport(
            target,
            submit_port=ClosedChannelReplySubmitPort(),
            now=lambda: NOW,
            recover_on_init=False,
            mutation_lock=PosixChannelReplyStateLockAdapter(),
        )
        root = target / ".channel_reply"
        canonical = "target-maintenance-progress.json"
        hidden = root / f".{canonical}.4242.{'c' * 32}.tmp"
    hidden.mkdir(mode=0o700)
    expected = {}
    for index in range(600):
        child = hidden / f"child-{index:04}"
        payload = f"payload-{index}".encode()
        child.write_bytes(payload)
        child.chmod(0o600)
        expected[child.name] = payload

    counts = {
        "scan_calls": 0,
        "scan_inspections": 0,
        "nested_remove_calls": 0,
        "nested_remove_scan_calls": 0,
    }
    real_scan = posix_lock._PosixChannelReplyMutationSession.scan
    real_remove = posix_lock._PosixChannelReplyMutationSession.remove_owned_entry

    def observed_scan(self, directory, *, budget, cursor=None):
        result = real_scan(self, directory, budget=budget, cursor=cursor)
        counts["scan_calls"] += 1
        counts["scan_inspections"] += result.inspections
        assert result.inspections <= budget.inspections <= 1
        return result

    def observed_remove(self, parent, name, *, budget, expected=None):
        if name != hidden.name:
            return real_remove(self, parent, name, budget=budget, expected=expected)
        counts["nested_remove_calls"] += 1
        scans_before = counts["scan_calls"]
        assert budget.inspections == 1
        assert budget.removals == 1
        assert budget.max_depth == 1
        result = real_remove(self, parent, name, budget=budget, expected=expected)
        counts["nested_remove_scan_calls"] += counts["scan_calls"] - scans_before
        assert result.inspections <= 1
        assert result.removals == 0
        return result

    monkeypatch.setattr(posix_lock._PosixChannelReplyMutationSession, "scan", observed_scan)
    monkeypatch.setattr(
        posix_lock._PosixChannelReplyMutationSession,
        "remove_owned_entry",
        observed_remove,
    )

    for _ in range(160):
        store.cleanup_retained(
            now=NOW,
            retention_seconds=0,
            max_records=1,
        )
        assert store.last_cleanup_inspections <= 1
        if counts["nested_remove_calls"]:
            break
    assert counts["nested_remove_calls"] == 1
    assert counts["nested_remove_scan_calls"] == 0
    assert counts["scan_inspections"] <= counts["scan_calls"]
    assert hidden.is_dir()
    assert {path.name: path.read_bytes() for path in hidden.iterdir()} == expected


def test_posix_resumable_scan_over_512_is_one_inspection_and_restart_safe(tmp_path):
    from lingtai.adapters.posix.channel_reply_state_lock import PosixChannelReplyStateLockAdapter
    from lingtai.kernel.channel_reply._mutation_lock import DirectoryScanCursor

    root = tmp_path / "paged-session"
    root.mkdir(mode=0o700)
    for index in range(530):
        path = root / f"{index:04}.json"
        path.write_bytes(b"{}")
        path.chmod(0o600)
    lock = PosixChannelReplyStateLockAdapter()
    cursor = None
    names: set[str] = set()
    for index in range(800):
        with lock.exclusive(root) as session:
            batch = session.scan(
                session.root,
                budget=DirectoryScanBudget(inspections=1, candidates=1),
                cursor=cursor,
            )
            assert batch.inspections <= 1
            names.update(entry.name for entry in batch.entries)
            if batch.complete:
                break
            assert isinstance(batch.next_cursor, DirectoryScanCursor)
            cursor = batch.next_cursor
        if index == 100:
            inserted = root / "inserted.json"
            inserted.write_bytes(b"{}")
            inserted.chmod(0o600)
        if index == 200:
            (root / "0000.json").unlink(missing_ok=True)
    assert "0529.json" in names
    # A second sweep eventually observes an insertion that may have landed behind
    # the first Darwin cookie during mutation.
    cursor = None
    for _ in range(800):
        with lock.exclusive(root) as session:
            batch = session.scan(
                session.root,
                budget=DirectoryScanBudget(inspections=1, candidates=1),
                cursor=cursor,
            )
            names.update(entry.name for entry in batch.entries)
            if batch.complete:
                break
            cursor = batch.next_cursor
    assert "inserted.json" in names


def test_owner_route_reservation_remains_usable_above_old_scan_ceiling(tmp_path):
    store = ChannelReplyFileStore(
        tmp_path / "owner-high-cardinality",
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    decision_dir = store.root / "route_decisions"
    before: dict[str, bytes] = {}
    for index in range(530):
        route = f"old-{index}"
        path = decision_dir / f"{route}.json"
        payload = {
            "version": 1,
            "route_event_id": route,
            "route_event_digest": channel_reply_core._route_event_identity_digest(route),
            "authority_digest": None,
            "decision": "reserved",
            "created_at": NOW,
            "updated_at": NOW,
        }
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        path.chmod(0o600)
        if index in {0, 529}:
            before[path.name] = path.read_bytes()
    calls: list[str] = []
    grant, proof, created = store.issue_or_reuse_grant(
        route_event_id="new-route-above-512",
        grant_factory=_route_factory(calls=calls),
        now=NOW,
    )
    assert grant is not None and proof and created
    assert calls == ["factory"]
    assert all((decision_dir / name).read_bytes() == data for name, data in before.items())
    again = store.issue_or_reuse_grant(
        route_event_id="new-route-above-512",
        grant_factory=lambda: pytest.fail("route decision reminted"),
        now=NOW,
    )
    assert again[0] == grant and again[1] == proof and again[2] is False


def test_sustained_claim_arrival_does_not_starve_receipt_consumed_dead_or_capsule(tmp_path):
    target = tmp_path / "sustained-claims"
    target.mkdir(mode=0o700)
    ChannelReplyTargetCapsule.create(
        target_workdir=target,
        target_agent_id="agent-1",
        target_agent_name="Target",
        created_at=OLD,
        expires_at=OLD,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    transport = ChannelReplyOwnerFileTransport(
        target,
        submit_port=ClosedChannelReplySubmitPort(),
        now=lambda: NOW,
        recover_on_init=False,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    root = target / ".channel_reply"
    old_files = []
    for directory, name in (
        (root / "receipts", f"{'a' * 64}.json"),
        (root / "consumed", f"{'b' * 64}.json"),
        (root / ".dead", f"{'c' * 64}.{'d' * 32}.json"),
    ):
        path = directory / name
        path.write_text("{", encoding="utf-8")
        path.chmod(0o600)
        old_files.append(path)

    claims = root / "claims"
    for cycle in range(80):
        # One new malformed canonical claim arrives before every budget=1 cycle,
        # and the transport process restarts. Durable class progress must prevent
        # each reconstruction from returning to claim recovery.
        claim = claims / f"{cycle:064x}.json"
        claim.write_text("{", encoding="utf-8")
        claim.chmod(0o600)
        transport = ChannelReplyOwnerFileTransport(
            target,
            submit_port=ClosedChannelReplySubmitPort(),
            now=lambda: NOW,
            recover_on_init=False,
            mutation_lock=PosixChannelReplyStateLockAdapter(),
        )
        transport.cleanup_retained(
            now="2126-08-09T12:00:00Z",
            retention_seconds=0,
            max_records=1,
        )
        assert transport.last_cleanup_inspections <= 1
        if not any(path.exists() for path in old_files) and not (root / "active_capsule.json").exists():
            break
    assert not any(path.exists() for path in old_files)
    assert not (root / "active_capsule.json").exists()
