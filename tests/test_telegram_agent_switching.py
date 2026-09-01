"""Focused tests for target-only Telegram Agent switching Simple V1."""
from __future__ import annotations

import errno
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.adapters.posix.channel_reply_state_lock import PosixChannelReplyStateLockAdapter
from lingtai.kernel.channel_reply import (
    CAPABILITY_MARKER,
    PROTOCOL_VERSION,
    ChannelReplySubmitRequest,
    ChannelReplyTargetCapsule,
    ChannelReplyTargetFileSubmitPort,
    OwnerReplyGrant,
)
from lingtai.mcp_servers.telegram import agent_switching as switching
from lingtai.mcp_servers.telegram.agent_switching import (
    AgentDirective,
    AgentSwitchingStateStore,
    EligibleTarget,
    TelegramAgentSwitchingRouter,
    compose_agent_commands,
    parse_agent_text,
    prepare_account_configs,
)
from lingtai.mcp_servers.telegram.channel_reply import TelegramChannelReplyAdapter


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


class _Account:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self._next_message_id = 900

    def public_identity(self):
        return {"bot_username": "OwnerBot"}

    def send_message(self, chat_id, text, **kwargs):
        self._next_message_id += 1
        record = {
            "chat_id": chat_id,
            "text": text,
            "message_id": self._next_message_id,
            **kwargs,
        }
        self.sent.append(record)
        return {"message_id": self._next_message_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        record = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            **kwargs,
        }
        self.edited.append(record)
        return {"message_id": message_id}


class _Service:
    def __init__(self) -> None:
        self.account = _Account()

    def get_account(self, alias):
        assert alias == "main"
        return self.account


def _owner(tmp_path: Path) -> Path:
    owner = tmp_path / "network" / "owner"
    owner.mkdir(parents=True)
    return owner


def _target(tmp_path: Path, name: str = "alpha", *, suffix: str = "a") -> EligibleTarget:
    workdir = tmp_path / "network" / name
    workdir.mkdir(parents=True, exist_ok=True)
    return EligibleTarget(
        name=name,
        agent_id=f"agent-{suffix}",
        workdir=workdir,
        manifest_digest=(suffix.lower()[0] if suffix.lower()[0] in "abcdef" else "a") * 64,
        ledger_chain_digest=("b" if suffix.lower()[0] != "b" else "c") * 64,
    )


def _router(tmp_path: Path, *, service: _Service | None = None) -> TelegramAgentSwitchingRouter:
    return TelegramAgentSwitchingRouter(
        owner_workdir=_owner(tmp_path),
        service=service or _Service(),
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )


def _reply_adapter(tmp_path: Path, service: _Service | None = None):
    return TelegramChannelReplyAdapter(
        state_root=tmp_path / "owner-reply-state",
        service=service or _Service(),
        target_agent_id="id-alpha",
        target_agent_name="alpha",
    )


def test_absent_target_reply_root_is_quiet_no_work_and_never_created(tmp_path):
    target_workdir = tmp_path / "alpha"
    target_workdir.mkdir()
    adapter = _reply_adapter(tmp_path)

    assert adapter.drain_target_outbox(target_workdir) == []
    assert adapter.drain_target_outbox(target_workdir) == []
    assert not (target_workdir / ".channel_reply").exists()


def test_existing_malformed_target_reply_root_still_fails_closed(tmp_path):
    adapter = _reply_adapter(tmp_path)

    partial_target = tmp_path / "partial"
    reply_root = partial_target / ".channel_reply"
    reply_root.mkdir(parents=True, mode=0o700)
    with pytest.raises(FileNotFoundError):
        adapter.drain_target_outbox(partial_target)

    symlink_target = tmp_path / "symlinked"
    symlink_target.mkdir()
    backing = tmp_path / "backing"
    backing.mkdir(mode=0o700)
    (symlink_target / ".channel_reply").symlink_to(
        backing,
        target_is_directory=True,
    )
    with pytest.raises(ValueError, match="state_directory_invalid"):
        adapter.drain_target_outbox(symlink_target)


def test_absent_reply_root_does_not_emit_router_drain_warning(
    tmp_path,
    monkeypatch,
    caplog,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    adapter = _reply_adapter(tmp_path, service)
    router._adapters[target.agent_id] = (target, adapter)
    monkeypatch.setattr(router, "_target_pin_is_current", lambda _target: True)
    monkeypatch.setattr(router, "list_targets", lambda: [])

    router._drain_registered_adapters_once()

    assert "target outbox drain failed" not in caplog.text
    assert not (target.workdir / ".channel_reply").exists()


def _message(text: str | None, *, message_id: int = 11, user_id: int = 22, chat_id: int = 33, **extra):
    message = {
        "message_id": message_id,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False},
        **extra,
    }
    if text is not None:
        message["text"] = text
    return {"update_id": 44, "message": message}


def _callback(data: str, *, message_id: int, user_id: int = 22, chat_id: int = 33):
    return {
        "update_id": 45,
        "callback_query": {
            "id": "cq-1",
            "data": data,
            "from": {"id": user_id, "is_bot": False},
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id, "type": "private"},
            },
        },
    }


@pytest.mark.parametrize(
    ("text", "bot_username", "expected"),
    [
        ("hello", None, AgentDirective("ordinary", body="hello")),
        (" \thello\nworld  ", None, AgentDirective("ordinary", body=" \thello\nworld  ")),
        ("@", None, AgentDirective("list")),
        ("/agent", None, AgentDirective("list")),
        ("/agent\tstatus", None, AgentDirective("status")),
        ("/agent\nreset", None, AgentDirective("reset")),
        ("/agent@OwnerBot \t status", "ownerbot", AgentDirective("status")),
        ("/agent@OwnerBot\nreset", "OwnerBot", AgentDirective("reset")),
        ("/agent@Other\tstatus", "OwnerBot", AgentDirective("invalid")),
        ("/agent@Other", "OwnerBot", AgentDirective("invalid")),
        ("/agentx", None, AgentDirective("invalid")),
        ("/agentx\tstatus", None, AgentDirective("invalid")),
        ("/agent/status", None, AgentDirective("invalid")),
        ("/agent status extra", None, AgentDirective("invalid")),
        ("/agent reset", None, AgentDirective("reset")),
        ("@current", None, AgentDirective("status")),
        ("@admin", None, AgentDirective("reset")),
        ("@alpha", None, AgentDirective("select", name="alpha")),
        ("@alpha\tdo this", None, AgentDirective("route_once", name="alpha", body="do this")),
        ("@alpha\ndo this", None, AgentDirective("route_once", name="alpha", body="do this")),
        (
            " \t@alpha\t  keep  \n inner \t spaces  ",
            None,
            AgentDirective("route_once", name="alpha", body="keep  \n inner \t spaces"),
        ),
        ("@bad.name", None, AgentDirective("invalid")),
        ("@caf\u00e9", None, AgentDirective("invalid")),
        ("@\u03b1lpha", None, AgentDirective("invalid")),
        ("@alpha \t\n  ", None, AgentDirective("select", name="alpha")),
    ],
)
def test_parse_agent_text_contract(text, bot_username, expected):
    assert parse_agent_text(text, bot_username=bot_username) == expected


def test_command_composition_preserves_disabled_custom_and_explicit_empty_contracts():
    defaults = [{"command": "help", "description": "Help"}]
    custom = [{"command": "agent", "description": "Custom picker"}]

    assert compose_agent_commands(None, enabled=False, defaults=defaults) is None
    assert compose_agent_commands([], enabled=True, defaults=defaults) == []
    assert compose_agent_commands(custom, enabled=True, defaults=defaults) == custom
    assert compose_agent_commands(None, enabled=True, defaults=defaults) == [
        *defaults,
        {"command": "agent", "description": "Choose the target Agent"},
    ]

    original = [
        {"alias": "disabled"},
        {"alias": "enabled", "agent_switching": True},
        {"alias": "empty", "agent_switching": {"enabled": True}, "commands": []},
    ]
    prepared = prepare_account_configs(original, default_commands=defaults)
    assert prepared[0] == original[0]
    assert prepared[1]["commands"][-1]["command"] == "agent"
    assert prepared[2]["commands"] == []
    assert original[1].get("commands") is None


def test_selection_scope_malformed_quarantine_and_menu_first_choice(tmp_path):
    store = AgentSwitchingStateStore(tmp_path / "state")
    alpha = EligibleTarget("alpha", "id-a", tmp_path / "alpha", _DIGEST_A, _DIGEST_B)
    beta = EligibleTarget("beta", "id-b", tmp_path / "beta", _DIGEST_B, _DIGEST_A)

    store.save_selection("main", 33, 22, alpha)
    assert store.load_selection("main", 33, 22)["target_name"] == "alpha"
    assert store.load_selection("other", 33, 22) is None
    assert store.load_selection("main", 34, 22) is None
    assert store.load_selection("main", 33, 23) is None

    token, _record = store.create_menu(
        account_alias="main", chat_id=33, user_id=22,
        targets=[alpha, beta], page=0,
    )
    assert store.bind_menu_message(token, 901) is not None
    committed = store.commit_menu_selection(
        token,
        account_alias="main", chat_id=33, user_id=22,
        bot_message_id=901, index=0, target=alpha,
    )
    assert committed is not None and committed[1] is True
    replay = store.commit_menu_selection(
        token,
        account_alias="main", chat_id=33, user_id=22,
        bot_message_id=901, index=1, target=beta,
    )
    assert replay is not None and replay[1] is False
    assert replay[0]["selected_index"] == 0
    assert store.load_selection("main", 33, 22)["target_name"] == "alpha"

    selection_path = store._selection_path("main", 33, 22)
    selection_path.write_text("not json", encoding="utf-8")
    assert store.load_selection("main", 33, 22) is None
    assert not selection_path.exists()
    assert len(list((tmp_path / "state" / ".dead").glob("*.dead"))) == 1


def test_create_once_zero_write_removes_partial_record(tmp_path, monkeypatch):
    path = tmp_path / "state" / "record.json"
    monkeypatch.setattr(switching.os, "write", lambda _fd, _data: 0)
    with pytest.raises(OSError, match="short_state_write"):
        switching._create_private_json_once(path, {"version": 1})
    assert not path.exists()


def _write_manifest(path: Path, name: str, agent_id: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "agent_name": name,
        "address": name,
        "agent_id": agent_id,
        "admin": "owner",
        "route_capabilities": {
            "channel_reply": {
                "marker": CAPABILITY_MARKER,
                "version": PROTOCOL_VERSION,
                "submit": "target-local-filesystem-capsule",
            }
        },
    }
    (path / ".agent.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    (path / ".agent.heartbeat").write_text(str(time.time()), encoding="utf-8")


def _ledger(parent: Path, records: list[dict]) -> None:
    ledger = parent / "delegates" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def _avatar_edge(name: str) -> dict:
    return {"event": "avatar", "boot_status": "ok", "name": name, "working_dir": name}


def _routed_event_authority(event: dict) -> dict[str, str]:
    authority: dict[str, str] = {}
    for line in event["body"].splitlines():
        key, separator, value = line.partition(": ")
        if separator and key in {"version", "grant_ref", "proof"}:
            authority[key] = value
    assert set(authority) == {"version", "grant_ref", "proof"}
    return authority


def _queue_real_routed_reply(
    router: TelegramAgentSwitchingRouter,
    service: _Service,
    *,
    message_id: int = 71,
):
    owner = router.owner_workdir
    target_dir = owner.parent / "alpha"
    _write_manifest(target_dir, "alpha", "id-alpha")
    _ledger(owner, [_avatar_edge("alpha")])
    targets = router.list_targets()
    assert len(targets) == 1
    target = targets[0]
    update = _message("route body", message_id=message_id)
    router._route("main", 33, 22, message_id, update, target, "route body")
    route_id = router._route_event_id("main", 33, 22, message_id, update)
    event = json.loads(
        router._target_event_path(target, "as-" + route_id).read_text(encoding="utf-8")
    )
    authority = _routed_event_authority(event)
    request = ChannelReplySubmitRequest.from_mapping(
        {
            "version": int(authority["version"]),
            "grant_ref": authority["grant_ref"],
            "request_id": "reply-after-route",
            "created_at": event["received_at"],
            "text": "queued target reply",
            "proof": authority["proof"],
        }
    )
    submitter = ChannelReplyTargetFileSubmitPort(target.workdir, mutation_lock=PosixChannelReplyStateLockAdapter())
    assert submitter.submit_channel_reply(request).status == "pending"
    old_adapter = router._adapters[target.agent_id][1]
    assert service.account.sent == []
    return target, request, submitter, old_adapter


def test_delayed_live_grant_uses_fresh_request_time_and_sends_at_most_once(
    tmp_path,
    monkeypatch,
):
    grant_time = "2026-08-12T00:00:00Z"
    submit_time = "2026-08-12T00:10:01Z"
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    monkeypatch.setattr(
        router,
        "_resolve_name",
        lambda name: (target, False) if name == target.name else (None, False),
    )
    monkeypatch.setattr(switching, "_utc_now", lambda: grant_time)
    update = _message("delayed route body", message_id=72)

    router._route("main", 33, 22, 72, update, target, "delayed route body")

    route_id = router._route_event_id("main", 33, 22, 72, update)
    event = json.loads(
        router._target_event_path(target, "as-" + route_id).read_text(encoding="utf-8")
    )
    authority = _routed_event_authority(event)
    grant = router._grant_store.get_grant(authority["grant_ref"])
    assert grant is not None
    assert grant.created_at == grant_time
    assert grant.expires_at == "2026-08-12T02:00:00Z"
    assert "created_at:" not in event["body"]
    assert "current UTC timestamp at the moment you submit" in event["body"]
    assert "not the grant's route or issuance time" in event["body"]

    adapter = router._adapters[target.agent_id][1]
    adapter._now = lambda: submit_time
    fresh = ChannelReplySubmitRequest.from_mapping(
        {
            "version": int(authority["version"]),
            "grant_ref": authority["grant_ref"],
            "request_id": "delayed-fresh-request",
            "created_at": submit_time,
            "text": "fresh delayed reply",
            "proof": authority["proof"],
        }
    )
    submitter = ChannelReplyTargetFileSubmitPort(
        target.workdir,
        now=lambda: submit_time,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    assert submitter.submit_channel_reply(fresh).status == "pending"
    monkeypatch.setattr(router, "_target_pin_is_current", lambda _target: True)
    monkeypatch.setattr(router, "list_targets", lambda: [target])
    router._drain_registered_adapters_once()

    sent = submitter.submit_channel_reply(fresh)
    assert sent.status == "sent"
    assert service.account.sent == [
        {
            "chat_id": 33,
            "text": "[alpha] fresh delayed reply",
            "message_id": 901,
            "reply_to_message_id": 72,
        }
    ]
    assert router._grant_store.get_grant(authority["grant_ref"]).consumed_request_id == (
        "delayed-fresh-request"
    )

    # The exact duplicate is a lookup only. A new request id cannot remint or
    # reuse the already consumed route authority, and replay never republishes it.
    assert submitter.submit_channel_reply(fresh) == sent
    replacement = ChannelReplySubmitRequest.from_mapping(
        {
            "version": int(authority["version"]),
            "grant_ref": authority["grant_ref"],
            "request_id": "delayed-replacement-request",
            "created_at": submit_time,
            "text": "must not send",
            "proof": authority["proof"],
        }
    )
    assert submitter.submit_channel_reply(replacement).status == "pending"
    router._drain_registered_adapters_once()
    assert submitter.submit_channel_reply(replacement).status == "dead"
    telegram_reply_count = sum(
        item.get("reply_to_message_id") == 72
        and item["text"] == "[alpha] fresh delayed reply"
        for item in service.account.sent
    )
    router._route("main", 33, 22, 72, update, target, "delayed route body")
    router._drain_registered_adapters_once()
    assert sum(
        item.get("reply_to_message_id") == 72
        and item["text"] == "[alpha] fresh delayed reply"
        for item in service.account.sent
    ) == telegram_reply_count == 1


def test_delayed_grant_time_and_genuinely_stale_request_remain_rejected(
    tmp_path,
    monkeypatch,
):
    grant_time = "2026-08-12T00:00:00Z"
    submit_time = "2026-08-12T00:10:01Z"
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    monkeypatch.setattr(
        router,
        "_resolve_name",
        lambda name: (target, False) if name == target.name else (None, False),
    )
    monkeypatch.setattr(switching, "_utc_now", lambda: grant_time)
    monkeypatch.setattr(router, "_target_pin_is_current", lambda _target: True)
    monkeypatch.setattr(router, "list_targets", lambda: [target])

    for message_id, request_id, created_at in (
        (73, "copied-grant-time", grant_time),
        (74, "genuinely-stale-time", "2026-08-11T23:59:59Z"),
    ):
        update = _message("must remain unsent", message_id=message_id)
        router._route("main", 33, 22, message_id, update, target, "must remain unsent")
        route_id = router._route_event_id("main", 33, 22, message_id, update)
        event = json.loads(
            router._target_event_path(target, "as-" + route_id).read_text(encoding="utf-8")
        )
        authority = _routed_event_authority(event)
        adapter = router._adapters[target.agent_id][1]
        adapter._now = lambda: submit_time
        request = ChannelReplySubmitRequest.from_mapping(
            {
                "version": int(authority["version"]),
                "grant_ref": authority["grant_ref"],
                "request_id": request_id,
                "created_at": created_at,
                "text": "must remain unsent",
                "proof": authority["proof"],
            }
        )
        submitter = ChannelReplyTargetFileSubmitPort(
            target.workdir, now=lambda: submit_time,
            mutation_lock=PosixChannelReplyStateLockAdapter(),
        )
        assert submitter.submit_channel_reply(request).status == "pending"
        router._drain_registered_adapters_once()
        rejected = submitter.submit_channel_reply(request)
        assert rejected.status == "dead"
        assert rejected.message == "request timestamp too old"
        rejected_grant = router._grant_store.get_grant(authority["grant_ref"])
        assert rejected_grant is not None
        assert rejected_grant.expires_at == "2026-08-12T02:00:00Z"
        assert rejected_grant.revoked is False
        assert rejected_grant.consumed_request_id is None

    assert service.account.sent == []


def _drift_routed_target(router: TelegramAgentSwitchingRouter, drift: str) -> None:
    target_dir = router.owner_workdir.parent / "alpha"
    manifest_path = target_dir / ".agent.json"
    if drift == "death":
        (target_dir / ".agent.heartbeat").unlink()
        return
    if drift == "ledger":
        _ledger(router.owner_workdir, [])
        return
    if drift == "liveness":
        (target_dir / ".agent.heartbeat").write_text(
            str(time.time() - 60.0), encoding="utf-8"
        )
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if drift == "replacement":
        manifest["agent_id"] = "id-replacement"
    elif drift == "capability":
        manifest["route_capabilities"]["channel_reply"]["submit"] = (
            "unsupported-submit"
        )
    elif drift == "protocol":
        manifest["route_capabilities"]["channel_reply"]["version"] = (
            PROTOCOL_VERSION + 1
        )
    else:  # pragma: no cover - the parametrization is the closed drift set
        raise AssertionError(drift)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "drift",
    ["death", "replacement", "ledger", "capability", "protocol", "liveness"],
)
def test_route_to_reply_revalidates_exact_target_pin_before_send_and_retires_cache(
    tmp_path,
    drift,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    target, request, submitter, old_adapter = _queue_real_routed_reply(router, service)

    _drift_routed_target(router, drift)
    router._drain_registered_adapters_once()

    receipt = submitter.submit_channel_reply(request)
    assert receipt.status == "failed"
    assert receipt.message == "reply could not be prepared"
    assert service.account.sent == []
    assert all(adapter is not old_adapter for _pin, adapter in router._adapters.values())
    cached = router._adapters.get(target.agent_id)
    assert cached is None or cached[0] != target


def test_list_targets_pins_direct_and_nested_descendants_and_rejects_duplicate_edge(tmp_path, monkeypatch):
    router = _router(tmp_path)
    owner = router.owner_workdir
    network = owner.parent
    _write_manifest(network / "alpha", "alpha", "id-alpha")
    _write_manifest(network / "beta", "beta", "id-beta")
    _ledger(owner, [_avatar_edge("alpha")])
    _ledger(network / "alpha", [_avatar_edge("beta")])
    monkeypatch.setattr(switching, "observe_alive", lambda *_args, **_kwargs: True)

    targets = router.list_targets()
    assert [target.name for target in targets] == ["alpha", "beta"]
    assert all(len(target.manifest_digest) == 64 for target in targets)
    assert targets[0].ledger_chain_digest != targets[1].ledger_chain_digest

    _ledger(owner, [_avatar_edge("alpha"), _avatar_edge("alpha")])
    assert router.list_targets() == []
    assert router.handle("main", _message("@alpha"), "message") is True
    assert router._service.account.sent[-1]["text"] == (
        "[admin] That Agent is not currently eligible."
    )


def test_handle_routes_only_after_selection_one_shot_does_not_mutate_and_media_fails_local(tmp_path, monkeypatch):
    service = _Service()
    router = _router(tmp_path, service=service)
    alpha = _target(tmp_path, "alpha", suffix="a")
    beta = _target(tmp_path, "beta", suffix="b")
    monkeypatch.setattr(router, "list_targets", lambda: [alpha, beta])
    monkeypatch.setattr(router, "_register_adapter", lambda _target: SimpleNamespace())
    routed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        router,
        "_route",
        lambda _account, _chat, _user, _message, _update, target, body: routed.append((target.name, body)),
    )

    assert router.handle("main", _message("ordinary"), "message") is False
    assert router.handle("main", _message("@alpha"), "message") is True
    assert router.handle("main", _message("selected body", message_id=12), "message") is True
    assert routed == [("alpha", "selected body")]

    exact_ordinary = " \tselected\n body  "
    assert router.handle("main", _message(exact_ordinary, message_id=13), "message") is True
    assert routed[-1] == ("alpha", exact_ordinary)

    assert router.handle("main", _message("@beta\t one \n shot ", message_id=14), "message") is True
    assert routed[-1] == ("beta", "one \n shot")
    assert router._state.load_selection("main", 33, 22)["target_name"] == "alpha"

    before_malformed = list(routed)
    for offset, malformed in enumerate(("/agentx", "/agentx\tstatus", "/agent@Other\nreset"), 15):
        assert router.handle("main", _message(malformed, message_id=offset), "message") is True
    assert routed == before_malformed
    assert all(item["text"] == "[admin] Invalid Agent command." for item in service.account.sent[-3:])

    assert router.handle("main", _message(None, message_id=18, photo=[{"file_id": "x"}]), "message") is True
    assert service.account.sent[-1]["text"] == "[admin] Agent routing supports non-empty plain text only."

    fresh = _router(tmp_path / "fresh", service=_Service())
    selector_media = _message(None, caption="@alpha", photo=[{"file_id": "x"}])
    assert fresh.handle("main", selector_media, "message") is True
    assert fresh._service.account.sent[-1]["text"] == "[admin] Agent routing supports non-empty plain text only."


def test_ordered_canary_message_matrix_has_only_explicit_target_decisions(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    alpha = _target(tmp_path, "alpha", suffix="a")
    beta = _target(tmp_path, "beta", suffix="b")
    monkeypatch.setattr(router, "list_targets", lambda: [alpha, beta])
    monkeypatch.setattr(router, "_register_adapter", lambda _target: SimpleNamespace())
    routed: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        router,
        "_route",
        lambda _account, _chat, _user, message_id, _update, target, body: (
            routed.append((message_id, target.name, body))
        ),
    )

    # Fresh-state setup traffic remains on the admin path.
    assert router.handle("main", _message("/start", message_id=101), "message") is False
    assert router.handle("main", _message("/start setup", message_id=102), "message") is False
    assert router.handle("main", _message("ordinary setup", message_id=103), "message") is False
    assert routed == []

    # The picker and callback are owner-local and create only selection state.
    assert router.handle("main", _message("/agent", message_id=104), "message") is True
    picker = service.account.sent[-1]
    callback_data = picker["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert router.handle(
        "main",
        _callback(callback_data, message_id=picker["message_id"]),
        "callback_query",
    ) is True
    assert router._state.load_selection("main", 33, 22)["target_name"] == "alpha"
    assert routed == []

    # Setup stays on the account/admin path after callback selection. No local
    # switching response or target decision is created and selection is retained.
    sent_before_start = list(service.account.sent)
    for offset, start_text in enumerate(
        ("/start", "/start setup", "/start@OwnerBot", "/start@OwnerBot setup"),
        114,
    ):
        assert router.handle(
            "main", _message(start_text, message_id=offset), "message"
        ) is False
    assert routed == []
    assert service.account.sent == sent_before_start
    assert router._state.load_selection("main", 33, 22)["target_name"] == "alpha"

    assert router.handle("main", _message("@current", message_id=105), "message") is True
    assert router.handle("main", _message("selected body", message_id=106), "message") is True
    assert routed == [(106, "alpha", "selected body")]

    # One-shot delivery never mutates the callback-selected target.
    assert router.handle(
        "main",
        _message("@beta one shot", message_id=107),
        "message",
    ) is True
    assert routed[-1] == (107, "beta", "one shot")
    assert router._state.load_selection("main", 33, 22)["target_name"] == "alpha"

    # Reset is local. An explicit one-shot still works from admin state, then a
    # bare selector persists a new target for later ordinary text.
    assert router.handle("main", _message("@admin", message_id=108), "message") is True
    assert router._state.load_selection("main", 33, 22) is None
    assert router.handle(
        "main",
        _message("@alpha isolated", message_id=109),
        "message",
    ) is True
    assert router._state.load_selection("main", 33, 22) is None
    assert router.handle("main", _message("@beta", message_id=110), "message") is True
    # Bare persistent selection has the same /start exception.
    assert router.handle(
        "main", _message("/start@OwnerBot configure", message_id=115), "message"
    ) is False
    assert router._state.load_selection("main", 33, 22)["target_name"] == "beta"
    assert router.handle("main", _message("later ordinary", message_id=111), "message") is True
    assert routed == [
        (106, "alpha", "selected body"),
        (107, "beta", "one shot"),
        (109, "alpha", "isolated"),
        (111, "beta", "later ordinary"),
    ]

    assert router.handle("main", _message("@admin", message_id=112), "message") is True
    assert router.handle("main", _message("back to admin", message_id=113), "message") is False
    assert len({message_id for message_id, _target_name, _body in routed}) == len(routed)


@pytest.mark.parametrize(
    ("text", "expected_fragment"),
    [
        ("/agent\tstatus", "[admin] Current target: @admin."),
        ("/agent\nreset", "Target reset to admin."),
        ("/agentx", "Invalid Agent command."),
        ("/agentx\tstatus", "Invalid Agent command."),
        ("/agent@Other\nreset", "Invalid Agent command."),
    ],
)
def test_whitespace_commands_and_malformed_agent_controls_are_always_local(
    tmp_path,
    monkeypatch,
    text,
    expected_fragment,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    monkeypatch.setattr(
        router,
        "_route",
        lambda *_args, **_kwargs: pytest.fail("control text reached target route"),
    )

    assert router.handle("main", _message(text), "message") is True
    assert expected_fragment in service.account.sent[-1]["text"]


@pytest.mark.parametrize(
    "forward_fields",
    [
        {"forward_origin": {"type": "user", "date": 1, "sender_user": {"id": 9}}},
        {"forward_from": {"id": 9}, "forward_date": 1},
        {"forward_from_chat": {"id": -9}, "forward_from_message_id": 7, "forward_date": 1},
    ],
    ids=("current-origin", "legacy-user", "legacy-chat"),
)
@pytest.mark.parametrize("route_mode", ["saved", "one-shot"])
def test_forwarded_text_is_local_when_switching_applies_and_never_routes(
    tmp_path,
    monkeypatch,
    forward_fields,
    route_mode,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    alpha = _target(tmp_path, "alpha", suffix="a")
    monkeypatch.setattr(router, "list_targets", lambda: [alpha])
    routed: list[str] = []
    monkeypatch.setattr(router, "_route", lambda *_args: routed.append("routed"))
    if route_mode == "saved":
        router._state.save_selection("main", 33, 22, alpha)
        text = "forwarded words"
    else:
        text = "@alpha forwarded words"

    assert router.handle("main", _message(text, **forward_fields), "message") is True
    assert routed == []
    assert service.account.sent[-1]["text"] == (
        "[admin] Agent routing supports non-forwarded plain text messages only."
    )

    ordinary_service = _Service()
    ordinary = _router(tmp_path / "ordinary", service=ordinary_service)
    assert ordinary.handle(
        "main",
        _message("ordinary forwarded admin text", **forward_fields),
        "message",
    ) is False
    assert ordinary_service.account.sent == []


def test_menu_callback_is_page_clamped_and_first_choice_is_replay_idempotent(tmp_path, monkeypatch):
    service = _Service()
    router = _router(tmp_path, service=service)
    targets = [_target(tmp_path, f"agent{i}", suffix="a") for i in range(10)]
    # Give each menu row a distinct pinned identity.
    targets = [
        EligibleTarget(t.name, f"id-{i}", t.workdir, f"{i:x}" * 64 if i < 16 else _DIGEST_A, _DIGEST_B)
        for i, t in enumerate(targets)
    ]
    monkeypatch.setattr(router, "list_targets", lambda: targets)
    monkeypatch.setattr(router, "_register_adapter", lambda _target: SimpleNamespace())

    assert router.handle("main", _message("@"), "message") is True
    sent = service.account.sent[-1]
    bot_message_id = sent["message_id"]
    first_data = sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    token = first_data.split(":")[1]

    assert router.handle("main", _callback(f"as:{token}:p999", message_id=bot_message_id), "callback_query") is True
    assert service.account.edited[-1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "@agent8"

    assert router.handle("main", _callback(f"as:{token}:s8", message_id=bot_message_id), "callback_query") is True
    assert router.handle("main", _callback(f"as:{token}:s9", message_id=bot_message_id), "callback_query") is True
    assert router._state.load_selection("main", 33, 22)["target_name"] == "agent8"
    assert service.account.edited[-1]["text"] == (
        "[admin] Selected @agent8. Current target: @agent8."
    )


def _wire_single_target(router: TelegramAgentSwitchingRouter, target: EligibleTarget, monkeypatch) -> None:
    monkeypatch.setattr(router, "_resolve_name", lambda name: (target, False) if name == target.name else (None, False))
    monkeypatch.setattr(router, "_register_adapter", lambda _target: SimpleNamespace())


def test_reserved_after_publish_recovery_matches_exact_licc_payload_and_never_repushes(tmp_path, monkeypatch):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    _wire_single_target(router, target, monkeypatch)
    update = _message("hello", message_id=77)

    original_mark = router._mark_target_route
    failed = {"done": False}

    def fail_first_published(target_arg, decision, status_value, now):
        if status_value == "published" and not failed["done"]:
            failed["done"] = True
            raise OSError("crash-after-publication")
        return original_mark(target_arg, decision, status_value, now)

    monkeypatch.setattr(router, "_mark_target_route", fail_first_published)
    router._route("main", 33, 22, 77, update, target, "hello")

    route_id = router._route_event_id("main", 33, 22, 77, update)
    decision_path = router._target_decision_path(target, route_id)
    assert json.loads(decision_path.read_text())["status"] == "reserved"
    assert router._target_event_path(target, "as-" + route_id).is_file()

    monkeypatch.setattr(router, "_mark_target_route", original_mark)
    repushes: list[dict] = []
    monkeypatch.setattr(switching, "push_inbox_event", lambda **kwargs: repushes.append(kwargs) or False)
    router._route("main", 33, 22, 77, update, target, "hello")

    assert repushes == []
    assert json.loads(decision_path.read_text())["status"] == "published"
    assert not any("could not be routed" in item["text"] for item in service.account.sent)


def test_successful_route_is_stable_duplicate_and_owner_state_excludes_human_body(tmp_path, monkeypatch):
    router = _router(tmp_path)
    target = _target(tmp_path, "alpha", suffix="a")
    _wire_single_target(router, target, monkeypatch)
    update = _message("private human words", message_id=88)
    real_push = switching.push_inbox_event
    pushes: list[dict] = []

    def counted_push(**kwargs):
        pushes.append(kwargs)
        return real_push(**kwargs)

    monkeypatch.setattr(switching, "push_inbox_event", counted_push)
    router._route("main", 33, 22, 88, update, target, "private human words")
    router._route("main", 33, 22, 88, update, target, "private human words")
    assert len(pushes) == 1

    route_id = router._route_event_id("main", 33, 22, 88, update)
    decision = json.loads(router._target_decision_path(target, route_id).read_text())
    assert decision["status"] == "published"
    assert decision["body_digest"] == switching.hashlib.sha256(b"private human words").hexdigest()

    owner_files = [p for p in router.owner_workdir.rglob("*") if p.is_file()]
    owner_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in owner_files)
    assert "private human words" not in owner_text
    target_event = json.loads(router._target_event_path(target, "as-" + route_id).read_text())
    assert target_event["metadata"]["delivery_semantics"] == "at-most-once/v1"
    assert "private human words" in target_event["body"]


def test_multiple_descendant_paths_make_target_and_its_branch_ineligible(tmp_path, monkeypatch):
    router = _router(tmp_path)
    owner = router.owner_workdir
    network = owner.parent
    for name in ("alpha", "beta", "gamma", "delta"):
        _write_manifest(network / name, name, f"id-{name}")
    _ledger(owner, [_avatar_edge("alpha"), _avatar_edge("beta")])
    _ledger(network / "alpha", [_avatar_edge("gamma")])
    _ledger(network / "beta", [_avatar_edge("gamma")])
    _ledger(network / "gamma", [_avatar_edge("delta")])
    monkeypatch.setattr(switching, "observe_alive", lambda *_args, **_kwargs: True)

    assert [target.name for target in router.list_targets()] == ["alpha", "beta"]



def test_selected_target_survives_mutable_manifest_state_publication(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    owner = router.owner_workdir
    target_dir = owner.parent / "alpha"
    _write_manifest(target_dir, "alpha", "id-alpha")
    _ledger(owner, [_avatar_edge("alpha")])
    monkeypatch.setattr(switching, "observe_alive", lambda *_args, **_kwargs: True)

    manifest_path = target_dir / ".agent.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["state"] = {"status": "active", "updated_at": "2026-08-31T19:50:08Z"}
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    active_bytes = manifest_path.read_bytes()
    active = router.list_targets()[0]
    router._state.save_selection("main", 33, 22, active)

    manifest["state"] = {"status": "idle", "updated_at": "2026-08-31T19:50:30Z"}
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    idle_bytes = manifest_path.read_bytes()
    idle = router.list_targets()[0]

    assert idle_bytes != active_bytes
    assert idle.agent_id == active.agent_id
    assert idle.manifest_digest == active.manifest_digest
    assert idle.ledger_chain_digest == active.ledger_chain_digest

    routed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        router,
        "_route",
        lambda _account, _chat, _user, _message, _update, target, body: (
            routed.append((target.name, body))
        ),
    )
    assert router.handle(
        "main", _message("ordinary after idle", message_id=12), "message"
    ) is True
    assert routed == [("alpha", "ordinary after idle")]
    assert router._state.load_selection("main", 33, 22)["target_name"] == "alpha"


def test_selected_target_pin_drift_clears_selection_but_temporary_unavailability_keeps_it(tmp_path, monkeypatch):
    service = _Service()
    router = _router(tmp_path, service=service)
    alpha = _target(tmp_path, "alpha", suffix="a")
    router._state.save_selection("main", 33, 22, alpha)

    monkeypatch.setattr(router, "list_targets", lambda: [])
    assert router.handle("main", _message("ordinary"), "message") is True
    assert router._state.load_selection("main", 33, 22) is not None
    assert "unavailable" in service.account.sent[-1]["text"]

    replacement = EligibleTarget(
        alpha.name,
        "agent-replacement",
        alpha.workdir,
        alpha.manifest_digest,
        alpha.ledger_chain_digest,
    )
    monkeypatch.setattr(router, "list_targets", lambda: [replacement])
    assert router.handle("main", _message("ordinary", message_id=12), "message") is True
    assert router._state.load_selection("main", 33, 22) is None
    assert service.account.sent[-1]["text"] == (
        "[admin] @alpha was replaced; selection was cleared. "
        "Current target: @admin."
    )


def test_expired_menu_cannot_change_selection(tmp_path):
    store = AgentSwitchingStateStore(tmp_path / "state")
    alpha = EligibleTarget("alpha", "id-a", tmp_path / "alpha", _DIGEST_A, _DIGEST_B)
    token, _record = store.create_menu(
        account_alias="main", chat_id=33, user_id=22,
        targets=[alpha], page=0,
    )
    assert store.bind_menu_message(token, 901) is not None
    path = store._menus / f"{token}.json"
    expired = json.loads(path.read_text())
    expired["expires_at"] = "2000-01-01T00:00:00Z"
    switching._atomic_private_json(path, expired)
    assert store.commit_menu_selection(
        token,
        account_alias="main", chat_id=33, user_id=22,
        bot_message_id=901, index=0, target=alpha,
    ) is None
    assert store.load_selection("main", 33, 22) is None


def test_conflicting_same_route_body_never_revokes_original_grant(tmp_path, monkeypatch):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    _wire_single_target(router, target, monkeypatch)
    update = _message("original", message_id=99)
    real_push = switching.push_inbox_event
    pushes: list[dict] = []

    def counted_push(**kwargs):
        pushes.append(kwargs)
        return real_push(**kwargs)

    monkeypatch.setattr(switching, "push_inbox_event", counted_push)
    router._route("main", 33, 22, 99, update, target, "original")
    route_id = router._route_event_id("main", 33, 22, 99, update)
    decision_path = router._target_decision_path(target, route_id)
    before = json.loads(decision_path.read_text())
    grant_ref = before["grant_ref"]

    router._route("main", 33, 22, 99, update, target, "different replay")

    assert len(pushes) == 1
    assert json.loads(decision_path.read_text()) == before
    grant = router._grant_store.get_grant(grant_ref)
    assert grant is not None and grant.revoked is False
    assert service.account.sent[-1]["text"] == "[admin] Agent routing is temporarily unavailable."


def test_missing_target_decision_after_consumption_never_republishes_reused_grant(tmp_path, monkeypatch):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    _wire_single_target(router, target, monkeypatch)
    update = _message("original", message_id=100)
    real_push = switching.push_inbox_event
    pushes: list[dict] = []

    def counted_push(**kwargs):
        pushes.append(kwargs)
        return real_push(**kwargs)

    monkeypatch.setattr(switching, "push_inbox_event", counted_push)
    router._route("main", 33, 22, 100, update, target, "original")
    route_id = router._route_event_id("main", 33, 22, 100, update)
    decision_path = router._target_decision_path(target, route_id)
    decision = json.loads(decision_path.read_text())
    grant_ref = decision["grant_ref"]
    decision_path.unlink()
    router._target_event_path(target, "as-" + route_id).unlink()

    router._route("main", 33, 22, 100, update, target, "original")

    assert len(pushes) == 1
    replacement = json.loads(decision_path.read_text())
    assert replacement["status"] == "failed"
    grant = router._grant_store.get_grant(grant_ref)
    assert grant is not None and grant.revoked is False
    assert service.account.sent[-1]["text"] == "[admin] Agent routing is temporarily unavailable."


def test_new_route_publication_failure_revokes_grant_and_marks_decision_failed(tmp_path, monkeypatch):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    _wire_single_target(router, target, monkeypatch)
    update = _message("body", message_id=101)
    monkeypatch.setattr(switching, "push_inbox_event", lambda **_kwargs: False)

    router._route("main", 33, 22, 101, update, target, "body")

    route_id = router._route_event_id("main", 33, 22, 101, update)
    decision = json.loads(router._target_decision_path(target, route_id).read_text())
    assert decision["status"] == "failed"
    grant = router._grant_store.get_grant(decision["grant_ref"])
    assert grant is not None and grant.revoked is True
    assert service.account.sent[-1]["text"] == "[admin] Agent routing is temporarily unavailable."


def test_selection_read_distinguishes_absent_valid_and_corrupt_unavailable(tmp_path):
    store = AgentSwitchingStateStore(tmp_path / "state")
    target = EligibleTarget("alpha", "agent-a", tmp_path / "alpha", _DIGEST_A, _DIGEST_B)

    absent = store.read_selection("main", 33, 22)
    assert (absent.status, absent.record) == ("absent", None)

    store.save_selection("main", 33, 22, target)
    valid = store.read_selection("main", 33, 22)
    assert valid.status == "valid"
    assert valid.record is not None and valid.record["target_name"] == "alpha"

    selection_path = store._selection_path("main", 33, 22)
    selection_path.write_text("{not-json", encoding="utf-8")
    unavailable = store.read_selection("main", 33, 22)
    assert (unavailable.status, unavailable.record) == ("unavailable", None)
    assert store._selection_unavailable_path("main", 33, 22).is_file()
    assert not selection_path.exists()
    assert any(store._dead.iterdir())
    # Quarantining the bad canonical record must not turn a later read into absence.
    assert store.read_selection("main", 33, 22).status == "unavailable"


def test_selection_wrong_fields_unsafe_shape_and_read_error_are_unavailable(
    tmp_path,
    monkeypatch,
):
    store = AgentSwitchingStateStore(tmp_path / "state")
    path = store._selection_path("main", 33, 22)
    path.write_text('{}', encoding="utf-8")
    assert store.read_selection("main", 33, 22).status == "unavailable"

    store.clear_selection("main", 33, 22)
    backing = tmp_path / "backing.json"
    backing.write_text('{}', encoding="utf-8")
    path.symlink_to(backing)
    assert store.read_selection("main", 33, 22).status == "unavailable"
    assert path.is_symlink()  # unsafe occupants are never followed or quarantined

    store.clear_selection("main", 33, 22)
    original_read = switching._read_private_json

    def injected_read(candidate):
        if candidate == path:
            raise OSError("synthetic read failure")
        return original_read(candidate)

    monkeypatch.setattr(switching, "_read_private_json", injected_read)
    assert store.read_selection("main", 33, 22).status == "unavailable"
    assert store._selection_unavailable_path("main", 33, 22).is_file()


def test_valid_bare_selection_replaces_unavailable_state_and_reset_clears_both(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    bad = router._state._selection_path("main", 33, 22)
    bad.write_text("bad", encoding="utf-8")
    assert router._state.read_selection("main", 33, 22).status == "unavailable"
    monkeypatch.setattr(router, "_resolve_name", lambda name: (target, False))
    monkeypatch.setattr(router, "_register_adapter", lambda _target: None)

    assert router.handle("main", _message("@alpha"), "message") is True
    assert router._state.read_selection("main", 33, 22).status == "valid"
    assert not router._state._selection_unavailable_path("main", 33, 22).exists()

    # Synthesize an interrupted stale marker next to a valid canonical selection;
    # reset owns both records and never touches already-issued grant authority.
    router._state._write_selection_unavailable("main", 33, 22)
    assert router.handle("main", _message("/agent reset", message_id=12), "message") is True
    assert router._state.read_selection("main", 33, 22).status == "absent"


def test_cleanup_retention_is_bounded_and_preserves_selection_truth(tmp_path):
    store = AgentSwitchingStateStore(tmp_path / "state")
    target = EligibleTarget("alpha", "agent-a", tmp_path / "alpha", _DIGEST_A, _DIGEST_B)
    tokens = []
    for _ in range(4):
        token, record = store.create_menu(
            account_alias="main", chat_id=33, user_id=22,
            targets=[target], page=0,
        )
        record["expires_at"] = "2000-01-01T00:00:00Z"
        switching._atomic_private_json(store._menus / f"{token}.json", record)
        tokens.append(token)
    store.save_selection("main", 33, 22, target)
    store._write_selection_unavailable("main", 44, 22)

    removed = store.cleanup_retained(
        now="2026-08-11T00:00:00Z", retention_seconds=0, max_items=2
    )

    assert removed <= 2
    assert sum((store._menus / f"{token}.json").exists() for token in tokens) >= 2
    assert store.read_selection("main", 33, 22).status == "valid"
    assert store.read_selection("main", 44, 22).status == "unavailable"


def test_menu_and_quarantine_cleanup_alternate_fairly_across_restart(tmp_path):
    root = tmp_path / "state"
    store = AgentSwitchingStateStore(root)
    target = EligibleTarget("alpha", "agent-a", tmp_path / "alpha", _DIGEST_A, _DIGEST_B)
    token, record = store.create_menu(
        account_alias="main", chat_id=33, user_id=22, targets=[target], page=0
    )
    record["expires_at"] = "2000-01-01T00:00:00Z"
    switching._atomic_private_json(store._menus / f"{token}.json", record)
    dead = store._dead / "old.dead"
    dead.write_text("proof-free", encoding="utf-8")
    dead.chmod(0o600)
    os.utime(dead, (1, 1))
    # First budget-one cycle services menus; a reconstructed store resumes at dead.
    assert store.cleanup_retained(now="2026-08-11T00:00:00Z", retention_seconds=0, max_items=1) == 1
    restarted = AgentSwitchingStateStore(root)
    assert restarted.cleanup_retained(now="2026-08-11T00:00:00Z", retention_seconds=0, max_items=1) == 1
    assert not dead.exists()


def test_target_cleanup_rotation_survives_restart_insertion_and_removal(tmp_path):
    root = tmp_path / "state"
    store = AgentSwitchingStateStore(root)
    assert store.select_cleanup_target_ids(["a", "b", "c"], max_items=2) == ["a", "b"]
    # Remove b, insert d, and reconstruct: durable cursor resumes at c rather than
    # replaying a fixed prefix.
    restarted = AgentSwitchingStateStore(root)
    assert restarted.select_cleanup_target_ids(["a", "c", "d"], max_items=2) == ["c", "d"]
    assert restarted.select_cleanup_target_ids(["a", "c", "d"], max_items=2) == ["a", "c"]


def test_bounded_target_cleanup_reuses_core_progress_and_does_not_starve_later_records(
    tmp_path,
):
    target = tmp_path / "Target"
    target.mkdir(mode=0o700)
    now = "2026-08-09T12:00:00Z"
    later = "2026-08-09T14:00:00Z"
    ChannelReplyTargetCapsule.create(
        target_workdir=target,
        target_agent_id="agent-1",
        target_agent_name="Target",
        created_at=now,
        expires_at=later,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    grant, proof = OwnerReplyGrant.issue(
        target_agent_id="agent-1",
        target_agent_name="Target",
        target_protocol_version=PROTOCOL_VERSION,
        channel="telegram",
        anchor={
            "account_alias": "main",
            "chat_id": 123,
            "reply_to_message_id": 55,
        },
        created_at=now,
        expires_at=later,
    )
    request = ChannelReplySubmitRequest(
        version=PROTOCOL_VERSION,
        grant_ref=grant.grant_ref,
        request_id="recent-pending",
        created_at=now,
        text="hello",
        proof=proof,
    )
    ChannelReplyTargetFileSubmitPort(
        target,
        now=lambda: now,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    ).submit_channel_reply(
        request
    )
    receipts = target / ".channel_reply" / "receipts"
    old_receipts: list[Path] = []
    for index in range(3):
        path = receipts / ((str(index) * 64) + ".json")
        path.write_text("{", encoding="utf-8")
        path.chmod(0o600)
        old_receipts.append(path)

    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path / "owner-state",
        service=_Service(),
        target_agent_id="agent-1",
        target_agent_name="Target",
        now=lambda: now,
    )
    for _ in range(20):
        adapter.cleanup_target_state(
            target,
            now=now,
            retention_seconds=365 * 24 * 60 * 60,
            max_records=1,
        )
        transport = adapter._target_cleanup_transports[target]
        assert transport.last_cleanup_inspections <= 1

    assert not any(path.exists() for path in old_receipts)

    # Whole-root absence is ordinary no-work and drops the stale progress owner.
    # A later target identity/root can therefore never reuse the old transport.
    reply_root = target / ".channel_reply"
    for child in sorted(reply_root.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink()
    reply_root.rmdir()
    assert adapter.cleanup_target_state(
        target,
        now=now,
        retention_seconds=0,
        max_records=1,
    ) == 0
    assert target not in adapter._target_cleanup_transports


def test_cleanup_cadence_is_monotonic_isolated_and_target_bounded(tmp_path, monkeypatch):
    now = [10.0]
    router = TelegramAgentSwitchingRouter(
        owner_workdir=_owner(tmp_path),
        service=_Service(),
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
        monotonic=lambda: now[0],
        cleanup_interval_seconds=300,
        cleanup_budget=2,
    )
    calls: list[str] = []

    def fail_state(**_kwargs):
        calls.append("state")
        raise RuntimeError("isolated")

    monkeypatch.setattr(router._state, "cleanup_retained", fail_state)
    def owner_cleanup(**kwargs):
        assert kwargs["max_records"] == 1
        calls.append("owner")

    monkeypatch.setattr(router._grant_store, "cleanup_retained", owner_cleanup)
    for index in range(3):
        target = EligibleTarget(
            f"target{index}", f"agent-{index}",
            tmp_path / "network" / f"target{index}", _DIGEST_A, _DIGEST_B,
        )
        target.workdir.mkdir()
        def cleanup_target(_workdir, *, _index=index, **kwargs):
            assert kwargs["max_records"] == 1
            calls.append(f"target{_index}")

        adapter = SimpleNamespace(cleanup_target_state=cleanup_target)
        router._adapters[target.agent_id] = (target, adapter)

    assert router._run_cleanup_if_due() is True
    assert calls == ["state", "owner", "target0", "target1"]
    assert router._next_cleanup_at == 310.0
    assert router._run_cleanup_if_due() is False
    assert calls == ["state", "owner", "target0", "target1"]
    now[0] = 310.0
    assert router._run_cleanup_if_due() is True
    # Root service resumes after the durable cursor instead of repeating a fixed
    # insertion prefix; the wrapped second item proves predictable rotation.
    assert calls[-4:] == ["state", "owner", "target2", "target0"]


def test_cleanup_never_deletes_permanent_target_decision_or_authorizes_republish(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    _wire_single_target(router, target, monkeypatch)
    update = _message("original", message_id=202)
    real_push = switching.push_inbox_event
    pushes: list[dict] = []
    monkeypatch.setattr(
        switching,
        "push_inbox_event",
        lambda **kwargs: pushes.append(kwargs) or real_push(**kwargs),
    )
    router._route("main", 33, 22, 202, update, target, "original")
    route_id = router._route_event_id("main", 33, 22, 202, update)
    decision_path = router._target_decision_path(target, route_id)
    before = decision_path.read_bytes()

    router._run_cleanup_if_due()
    assert decision_path.read_bytes() == before
    router._route("main", 33, 22, 202, update, target, "original")
    assert len(pushes) == 1
    assert switching.TELEGRAM_AGENT_SWITCHING_STATE_INVENTORY[-1][-1] == "permanent-no-republish"


def test_partial_second_worker_start_can_be_fully_cleaned_and_retried(
    tmp_path,
    monkeypatch,
):
    router = _router(tmp_path)
    monkeypatch.setattr(router, "list_targets", lambda: [])

    class _InjectedThread:
        created = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            type(self).created += 1
            self.number = type(self).created
            self.ident = None
            self._alive = False

        def start(self):
            if self.number == 2:
                raise RuntimeError("injected-second-worker-start-failure")
            self.ident = self.number
            self._alive = True

        def join(self, timeout=None):
            del timeout
            if self.ident is None:
                raise RuntimeError("cannot join thread before it is started")
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr(switching.threading, "Thread", _InjectedThread)

    with pytest.raises(RuntimeError, match="injected-second-worker-start-failure"):
        router.start()

    # The first worker is stopped and the assigned-but-never-started second
    # worker is discarded. A retained server cleanup retry is then idempotent.
    router.stop()
    assert router._drain_thread is None
    assert router._cleanup_thread is None
    router.stop()


def test_cached_target_cleanup_root_recreate_fails_one_cycle_then_recovers(tmp_path):
    import shutil
    from lingtai.kernel.channel_reply._mutation_lock import ChannelReplyExpectedRootMismatch

    target = tmp_path / "Target-recreated"
    target.mkdir(mode=0o700)
    now = "2026-08-09T12:00:00Z"
    later = "2026-08-09T14:00:00Z"

    def create_capsule():
        return ChannelReplyTargetCapsule.create(
            target_workdir=target,
            target_agent_id="agent-1",
            target_agent_name="Target",
            created_at=now,
            expires_at=later,
            mutation_lock=PosixChannelReplyStateLockAdapter(),
        )

    create_capsule()
    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path / "owner-state-recreated",
        service=_Service(),
        target_agent_id="agent-1",
        target_agent_name="Target",
        now=lambda: now,
    )
    adapter.cleanup_target_state(
        target, now=now, retention_seconds=0, max_records=1
    )
    stale = adapter._target_cleanup_transports[target]
    shutil.rmtree(target / ".channel_reply")
    create_capsule()

    with pytest.raises(ChannelReplyExpectedRootMismatch):
        adapter.cleanup_target_state(
            target, now=now, retention_seconds=0, max_records=1
        )
    assert target not in adapter._target_cleanup_transports

    assert adapter.cleanup_target_state(
        target, now=now, retention_seconds=0, max_records=1
    ) >= 0
    assert adapter._target_cleanup_transports[target] is not stale


def test_cached_target_cleanup_invalid_replacement_is_not_trusted_or_cached(tmp_path):
    import shutil
    from lingtai.kernel.channel_reply._mutation_lock import ChannelReplyExpectedRootMismatch

    target = tmp_path / "Target-invalid-replacement"
    target.mkdir(mode=0o700)
    now = "2026-08-09T12:00:00Z"
    later = "2026-08-09T14:00:00Z"
    ChannelReplyTargetCapsule.create(
        target_workdir=target,
        target_agent_id="agent-1",
        target_agent_name="Target",
        created_at=now,
        expires_at=later,
        mutation_lock=PosixChannelReplyStateLockAdapter(),
    )
    adapter = TelegramChannelReplyAdapter(
        state_root=tmp_path / "owner-state-invalid-replacement",
        service=_Service(),
        target_agent_id="agent-1",
        target_agent_name="Target",
        now=lambda: now,
    )
    adapter.cleanup_target_state(target, now=now, retention_seconds=0, max_records=1)
    shutil.rmtree(target / ".channel_reply")
    replacement = target / ".channel_reply"
    replacement.mkdir(mode=0o700)

    with pytest.raises(ChannelReplyExpectedRootMismatch):
        adapter.cleanup_target_state(target, now=now, retention_seconds=0, max_records=1)
    assert target not in adapter._target_cleanup_transports
    for _ in range(2):
        with pytest.raises((FileNotFoundError, OSError, ValueError)):
            adapter.cleanup_target_state(target, now=now, retention_seconds=0, max_records=1)
        assert target not in adapter._target_cleanup_transports
    assert not (replacement / "active_capsule.json").exists()


def test_switching_cleanup_actual_enumeration_is_bounded_and_durable_over_full_budget(
    tmp_path, monkeypatch
):
    root = tmp_path / "bounded-switching"
    store = AgentSwitchingStateStore(root)
    for index in range(260):
        menu = store._menus / f"token{index:04}.json"
        menu.write_text("{}", encoding="utf-8")
        menu.chmod(0o600)
        dead = store._dead / f"dead{index:04}.dead"
        dead.write_text("{}", encoding="utf-8")
        dead.chmod(0o600)

    real = AgentSwitchingStateStore._bounded_names
    charged: list[int] = []

    def observed(directory, *, suffix, cursor, inspections):
        names, next_cursor, count = real(
            directory, suffix=suffix, cursor=cursor, inspections=inspections
        )
        charged.append(count)
        assert count <= inspections
        return names, next_cursor, count

    monkeypatch.setattr(AgentSwitchingStateStore, "_bounded_names", staticmethod(observed))
    store.cleanup_retained(now="2126-08-09T12:00:00Z", retention_seconds=0, max_items=1)
    assert sum(charged) <= 1
    restarted = AgentSwitchingStateStore(root)
    for _ in range(2400):
        before = len(list(restarted._menus.glob("*.json"))) + len(list(restarted._dead.glob("*.dead")))
        restarted.cleanup_retained(
            now="2126-08-09T12:00:00Z", retention_seconds=0, max_items=1
        )
        after = len(list(restarted._menus.glob("*.json"))) + len(list(restarted._dead.glob("*.dead")))
        if after == 0:
            break
        assert after <= before
    assert not list(restarted._menus.glob("*.json"))
    assert not list(restarted._dead.glob("*.dead"))


@pytest.mark.parametrize("broken_class", ["menus", "dead"])
def test_switching_cleanup_open_failure_isolated_and_durable_budget_one(
    tmp_path, monkeypatch, broken_class
):
    root = tmp_path / f"switching-open-failure-{broken_class}"
    store = AgentSwitchingStateStore(root)
    target = EligibleTarget("alpha", "agent-a", tmp_path / "alpha", _DIGEST_A, _DIGEST_B)
    token, record = store.create_menu(
        account_alias="main", chat_id=33, user_id=22, targets=[target], page=0
    )
    record["expires_at"] = "2000-01-01T00:00:00Z"
    menu = store._menus / f"{token}.json"
    switching._atomic_private_json(menu, record)
    dead = store._dead / "healthy.dead"
    dead.write_text("proof-free", encoding="utf-8")
    dead.chmod(0o600)
    os.utime(dead, (1, 1))
    healthy = dead if broken_class == "menus" else menu
    failed_directory = store._menus if broken_class == "menus" else store._dead
    real_open = switching.os.open

    def fail_one_directory(path, flags, *args, **kwargs):
        if Path(path) == failed_directory and flags & getattr(os, "O_DIRECTORY", 0):
            raise NotADirectoryError(path)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(switching.os, "open", fail_one_directory)
    for _ in range(12):
        restarted = AgentSwitchingStateStore(root)
        restarted.cleanup_retained(
            now="2126-08-09T12:00:00Z", retention_seconds=0, max_items=1
        )
        progress = switching._read_private_json(restarted._cleanup_progress)
        assert progress is not None
        assert progress["next_class"] in {"menus", "dead", "original_ownership", "edit_rejections"}
        if not healthy.exists():
            break
    assert not healthy.exists()
    assert failed_directory.exists()


@pytest.mark.parametrize("failed_class", ["menus", "dead"])
def test_switching_cleanup_native_enumeration_failure_reserves_quota_and_isolates_class(
    tmp_path, monkeypatch, failed_class
):
    root = tmp_path / f"switching-native-failure-{failed_class}"
    store = AgentSwitchingStateStore(root)
    target = EligibleTarget("alpha", "agent-a", tmp_path / "alpha", _DIGEST_A, _DIGEST_B)
    token, record = store.create_menu(
        account_alias="main", chat_id=33, user_id=22, targets=[target], page=0
    )
    record["expires_at"] = "2000-01-01T00:00:00Z"
    menu = store._menus / f"{token}.json"
    switching._atomic_private_json(menu, record)
    dead = store._dead / "healthy.dead"
    dead.write_text("proof-free", encoding="utf-8")
    dead.chmod(0o600)
    os.utime(dead, (1, 1))
    failed_directory = store._menus if failed_class == "menus" else store._dead
    healthy = dead if failed_class == "menus" else menu
    real = AgentSwitchingStateStore._bounded_names
    assigned: list[int] = []
    actual: list[int] = []

    def injected(directory, *, suffix, cursor, inspections):
        assigned.append(inspections)
        if directory == failed_directory:
            # Model an EIO/malformed native record after unknowable partial work.
            actual.append(inspections)
            raise OSError(errno.EIO, "synthetic native enumeration failure")
        names, next_cursor, charged = real(
            directory, suffix=suffix, cursor=cursor, inspections=inspections
        )
        actual.append(charged)
        return names, next_cursor, charged

    monkeypatch.setattr(AgentSwitchingStateStore, "_bounded_names", staticmethod(injected))
    store.cleanup_retained(
        now="2126-08-09T12:00:00Z", retention_seconds=0, max_items=2
    )
    assert not healthy.exists()
    assert failed_directory.exists()
    assert sum(assigned) == 2
    assert sum(actual) <= 2
    progress = switching._read_private_json(store._cleanup_progress)
    assert progress is not None and progress["next_class"] == "original_ownership"


def test_switching_cleanup_candidate_failure_preserves_candidate_and_continues_other_work(
    tmp_path, monkeypatch
):
    store = AgentSwitchingStateStore(tmp_path / "switching-candidate-failure")
    failed = store._dead / "failed.dead"
    failed.write_text("proof-free", encoding="utf-8")
    failed.chmod(0o600)
    os.utime(failed, (1, 1))
    healthy_dead = store._dead / "healthy.dead"
    healthy_dead.write_text("proof-free", encoding="utf-8")
    healthy_dead.chmod(0o600)
    os.utime(healthy_dead, (1, 1))
    menu = store._menus / "healthy.json"
    menu.write_text("{}", encoding="utf-8")
    menu.chmod(0o600)
    real = AgentSwitchingStateStore._bounded_names
    actual: list[int] = []

    def deterministic(directory, *, suffix, cursor, inspections):
        if directory == store._dead:
            names = [name for name in (failed.name, healthy_dead.name) if (directory / name).exists()]
            selected = names[:inspections]
            actual.append(len(selected))
            return selected, dict(cursor), len(selected)
        names, next_cursor, charged = real(
            directory, suffix=suffix, cursor=cursor, inspections=inspections
        )
        actual.append(charged)
        return names, next_cursor, charged

    real_lstat = Path.lstat

    def fail_one_candidate(path):
        if path == failed:
            raise OSError(errno.EIO, "synthetic candidate metadata failure")
        return real_lstat(path)

    monkeypatch.setattr(AgentSwitchingStateStore, "_bounded_names", staticmethod(deterministic))
    monkeypatch.setattr(Path, "lstat", fail_one_candidate)
    store.cleanup_retained(
        now="2126-08-09T12:00:00Z", retention_seconds=0, max_items=6
    )
    assert failed.exists()
    assert not healthy_dead.exists()
    assert not menu.exists()
    assert sum(actual) <= 6



def _edited_message(
    text: str | None,
    *,
    update_id: int = 45,
    message_id: int = 11,
    user_id: int = 22,
    chat_id: int = 33,
    **extra,
):
    message = _message(
        text,
        message_id=message_id,
        user_id=user_id,
        chat_id=chat_id,
        **extra,
    )["message"]
    message["edit_date"] = 1781600100
    return {"update_id": update_id, "edited_message": message}


def test_edit_policy_is_generic_local_for_directive_selection_and_prior_ownership(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    monkeypatch.setattr(
        router,
        "_route",
        lambda *_args, **_kwargs: pytest.fail("edited message reached target route"),
    )

    ordinary = _edited_message("ordinary admin edit", update_id=100, message_id=70)
    assert router.handle("main", ordinary, "edited_message") is False
    assert service.account.sent == []

    for update_id, text in ((101, "@alpha changed"), (102, "/agent reset")):
        edited = _edited_message(text, update_id=update_id, message_id=70 + update_id)
        assert router.handle("main", edited, "edited_message") is True
        assert service.account.sent[-1]["text"] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"
        assert service.account.sent[-1]["reply_to_message_id"] == 70 + update_id

    router._state.save_selection("main", 33, 22, target)
    selected_edit = _edited_message("ordinary selected edit", update_id=103, message_id=73)
    assert router.handle("main", selected_edit, "edited_message") is True
    assert service.account.sent[-1]["text"] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"
    assert router._state.load_selection("main", 33, 22)["target_name"] == "alpha"

    assert router._state.clear_selection("main", 33, 22)
    marker_now = switching._utc_now()
    router._state.claim_original_ownership(
        "main",
        33,
        22,
        74,
        now=marker_now,
        retention_seconds=router._retention_seconds,
    )
    prior_edit = _edited_message("ordinary after reset", update_id=104, message_id=74)
    assert router.handle("main", prior_edit, "edited_message") is True
    assert service.account.sent[-1]["text"] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"


def test_edit_policy_selection_and_ownership_unavailability_never_fall_through(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    selection_path = router._state._selection_path("main", 33, 22)
    selection_path.write_text("{corrupt", encoding="utf-8")
    edited = _edited_message("ordinary", update_id=120, message_id=80)
    assert router.handle("main", edited, "edited_message") is True
    assert service.account.sent[-1]["text"] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"

    router._state.clear_selection("main", 33, 22)
    ownership_path = router._state._original_ownership_path("main", 33, 22, 81)
    ownership_path.write_text("{}", encoding="utf-8")
    unavailable_edit = _edited_message("ordinary", update_id=121, message_id=81)
    assert router.handle("main", unavailable_edit, "edited_message") is True
    assert service.account.sent[-1]["text"] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"
    assert ownership_path.read_text(encoding="utf-8") == "{}"

    original_read = switching._read_private_json

    def fail_ownership_read(candidate):
        if candidate == ownership_path:
            raise OSError("synthetic ownership read failure")
        return original_read(candidate)

    monkeypatch.setattr(switching, "_read_private_json", fail_ownership_read)
    assert router.handle("main", unavailable_edit, "edited_message") is True
    assert service.account.sent[-1]["text"] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"


def test_original_ownership_marker_is_body_free_strict_restart_safe_and_bounded(
    tmp_path,
):
    root = tmp_path / "ownership-state"
    store = AgentSwitchingStateStore(root)
    now = "2026-08-11T00:00:00Z"
    store.claim_original_ownership(
        "main", 33, 22, 90, now=now, retention_seconds=10
    )
    path = store._original_ownership_path("main", 33, 22, 90)
    first_bytes = path.read_bytes()
    record = json.loads(first_bytes)
    assert set(record) == {"version", "identity_digest", "created_at", "expires_at"}
    assert record["version"] == 1
    assert record["identity_digest"] == store._original_ownership_key("main", 33, 22, 90)
    assert all(value not in first_bytes for value in (b"main", b"private", b"username"))

    # Duplicate originals reuse the exact immutable truth; restart reads it O(1).
    store.claim_original_ownership(
        "main", 33, 22, 90, now=now, retention_seconds=10
    )
    assert path.read_bytes() == first_bytes
    restarted = AgentSwitchingStateStore(root)
    assert restarted.read_original_ownership(
        "main", 33, 22, 90,
        now="2026-08-11T00:00:09Z",
        retention_seconds=10,
    ).status == "owned"
    assert restarted.read_original_ownership(
        "main", 33, 22, 90,
        now="2026-08-11T00:00:10Z",
        retention_seconds=10,
    ).status == "absent"

    # The third fair cleanup class consumes at most its assigned share.
    assert restarted.cleanup_retained(
        now="2026-08-11T00:00:11Z",
        retention_seconds=10,
        max_items=3,
    ) == 1
    assert not path.exists()

    restarted.claim_original_ownership(
        "main", 33, 22, 91, now=now, retention_seconds=10
    )
    corrupt = restarted._original_ownership_path("main", 33, 22, 91)
    corrupt.write_text("{}", encoding="utf-8")
    assert restarted.read_original_ownership(
        "main", 33, 22, 91,
        now="2026-08-11T00:00:05Z",
        retention_seconds=10,
    ).status == "unavailable"
    restarted.cleanup_retained(
        now="2026-08-11T00:00:11Z",
        retention_seconds=10,
        max_items=3,
    )
    assert corrupt.exists()


def test_original_ownership_commit_failure_precedes_every_target_visible_write(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    _wire_single_target(router, target, monkeypatch)
    update = _message("private body", message_id=92)
    monkeypatch.setattr(
        router._state,
        "claim_original_ownership",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker write failed")),
    )
    monkeypatch.setattr(
        switching,
        "push_inbox_event",
        lambda **_kwargs: pytest.fail("target publication preceded owner marker"),
    )

    router._route("main", 33, 22, 92, update, target, "private body")

    assert service.account.sent[-1]["text"] == "[admin] Agent routing is temporarily unavailable."
    assert not (target.workdir / ".telegram-agent-switching").exists()
    assert not (target.workdir / ".mcp_inbox").exists()
    assert not (target.workdir / ".channel_reply").exists()


def test_successful_route_commits_sticky_ownership_before_reset_and_reselection(
    tmp_path,
    monkeypatch,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    alpha = _target(tmp_path, "alpha", suffix="a")
    beta = _target(tmp_path, "beta", suffix="b")
    monkeypatch.setattr(
        router,
        "_resolve_name",
        lambda name: ({"alpha": alpha, "beta": beta}.get(name), False),
    )
    monkeypatch.setattr(router, "_register_adapter", lambda _target: SimpleNamespace())
    update = _message("original body", message_id=93)
    router._route("main", 33, 22, 93, update, alpha, "original body")
    assert router._state.read_original_ownership(
        "main", 33, 22, 93,
        now=switching._utc_now(),
        retention_seconds=router._retention_seconds,
    ).status == "owned"

    router._state.clear_selection("main", 33, 22)
    after_reset = _edited_message("changed after reset", update_id=130, message_id=93)
    assert router.handle("main", after_reset, "edited_message") is True
    router._state.save_selection("main", 33, 22, beta)
    after_reselection = _edited_message(
        "changed after reselection", update_id=131, message_id=93
    )
    assert router.handle("main", after_reselection, "edited_message") is True
    assert [item["text"] for item in service.account.sent[-2:]] == [
        f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}",
        f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}",
    ]
    assert not (beta.workdir / ".mcp_inbox").exists()



def test_edit_rejection_decision_is_body_free_restart_idempotent_and_retained(
    tmp_path,
):
    service = _Service()
    router = _router(tmp_path, service=service)
    target = _target(tmp_path, "alpha", suffix="a")
    router._state.save_selection("main", 33, 22, target)
    update = _edited_message(
        "human edit body", update_id=150, message_id=95
    )

    assert router.handle("main", update, "edited_message") is True
    assert router.handle("main", update, "edited_message") is True
    assert [item["text"] for item in service.account.sent] == [
        f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"
    ]

    restart_service = _Service()
    restarted = TelegramAgentSwitchingRouter(
        owner_workdir=router.owner_workdir,
        service=restart_service,
        accounts_config=[{"alias": "main", "agent_switching": {"enabled": True}}],
    )
    assert restarted.handle("main", update, "edited_message") is True
    assert restart_service.account.sent == []

    distinct = _edited_message(
        "second human edit body", update_id=151, message_id=95
    )
    assert restarted.handle("main", distinct, "edited_message") is True
    assert restart_service.account.sent[-1]["text"] == f"[admin] {switching._EDIT_UNSUPPORTED_TEXT}"
    decisions = list(restarted._state._edit_rejections.glob("*.json"))
    assert len(decisions) == 2
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in decisions)
    assert "human edit body" not in serialized
    for decision in decisions:
        record = json.loads(decision.read_text(encoding="utf-8"))
        assert set(record) == {"version", "event_digest", "created_at", "expires_at"}
        assert record["event_digest"] == decision.stem
