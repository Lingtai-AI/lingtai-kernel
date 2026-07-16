"""S4 Notification Store conformance and hostile red-counterexamples.

Filesystem cases use ordinary portable pytest ``tmp_path`` composition.  Local
execution remains a parent-owned safety gate and is not encoded into the test
suite's source semantics.
"""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel.base_agent.messaging import _enqueue_system_notification
from lingtai.kernel.notification_store import (
    CompareUpdateResult,
    NotificationStorePort,
    UNCONDITIONAL,
    UpdateAckRefsResult,
)
from lingtai.kernel.notifications import (
    ack_large_result_refs,
    dismiss_channel,
    is_channel_allowed,
    purge_stale_large_result_acks,
    register_generic_dismiss_guard,
    submit,
)
from lingtai.kernel.nudge import remove as remove_nudge
from lingtai.kernel.nudge import upsert as upsert_nudge
from tests._notification_store_helpers import FakeNotificationStore


def _allow_all(_channel: str) -> bool:
    return True


def _posix_store(workdir: Path) -> PosixNotificationStoreAdapter:
    workdir.mkdir(parents=True, exist_ok=True)
    return PosixNotificationStoreAdapter(workdir)


@pytest.fixture(params=("fake", "posix"))
def conforming_store(request, tmp_path) -> NotificationStorePort:
    if request.param == "fake":
        return FakeNotificationStore()
    return _posix_store(tmp_path / "posix-conformance")


class TestSevenFamilyConformance:
    def test_exact_seven_operation_families(self):
        assert NotificationStorePort.__abstractmethods__ == {
            "snapshot",
            "fingerprint",
            "publish",
            "clear",
            "compare_update_channel",
            "load_ack_refs",
            "update_ack_refs",
        }

    def test_snapshot_fingerprint_publish_and_clear(self, conforming_store):
        store = conforming_store
        assert store.snapshot(_allow_all) == {}
        assert store.fingerprint(_allow_all) == ()
        store.publish("email", {"nested": {"count": 1}})
        snapshot = store.snapshot(_allow_all)
        snapshot["email"]["nested"]["count"] = 99
        assert store.snapshot(_allow_all)["email"]["nested"]["count"] == 1
        entry = store.fingerprint(_allow_all)[0]
        assert entry[0] == "email.json"
        assert store.snapshot(lambda channel: channel == "soul") == {}
        assert store.clear("email") is True
        assert store.clear("email") is False

    def test_unconditional_is_not_expected_absence(self, conforming_store):
        store = conforming_store
        store.publish("email", {"version": 1})
        called = []

        conflict = store.compare_update_channel(
            "email",
            None,
            lambda current: (called.append(current) or current, False, "wrong"),
        )
        assert isinstance(conflict, CompareUpdateResult)
        assert conflict.conflict is True
        assert conflict.applied is False
        assert conflict.value is None
        assert called == []

        applied = store.compare_update_channel(
            "email",
            UNCONDITIONAL,
            lambda current: ({**current, "version": 2}, True, ("policy", 2)),
        )
        assert applied == CompareUpdateResult(
            applied=True,
            conflict=False,
            changed=True,
            cleared=False,
            value=("policy", 2),
            current_version=applied.current_version,
            previous_version=applied.previous_version,
        )
        assert applied.current_version != applied.previous_version

    def test_expected_absence_applies_only_when_missing(self, conforming_store):
        result = conforming_store.compare_update_channel(
            "email", None, lambda _current: ({"created": True}, True, "created")
        )
        assert result.applied and not result.conflict and result.value == "created"

    def test_stale_version_refuses_without_calling_mutator(self, conforming_store):
        store = conforming_store
        store.publish("system", {"version": 1})
        delivered = store.fingerprint(_allow_all)[0]
        store.publish("system", {"version": 2})
        called = False

        def forbidden(_current):
            nonlocal called
            called = True
            return None, True, None

        result = store.compare_update_channel("system", delivered, forbidden)
        assert result.conflict and not result.applied and not result.changed
        assert called is False
        assert store.snapshot(_allow_all)["system"] == {"version": 2}

    def test_no_change_is_applied_and_carries_policy_value(self, conforming_store):
        conforming_store.publish("email", {"version": 1})
        result = conforming_store.compare_update_channel(
            "email", UNCONDITIONAL, lambda current: (current, False, {"why": "same"})
        )
        assert result.applied and not result.conflict
        assert result.changed is False and result.cleared is False
        assert result.value == {"why": "same"}
        assert result.current_version == result.previous_version

    def test_clear_result_reports_actual_absence(self, conforming_store):
        result = conforming_store.compare_update_channel(
            "email", UNCONDITIONAL, lambda _current: (None, True, "clear")
        )
        assert result.applied and not result.changed and not result.cleared
        assert result.value == "clear"

    def test_atomic_ack_result_and_value(self, conforming_store):
        store = conforming_store
        result = store.update_ack_refs(
            lambda current: (current | {"ref-a"}, True, ("union", "ref-a"))
        )
        assert isinstance(result, UpdateAckRefsResult)
        assert result.changed is True
        assert result.value == ("union", "ref-a")
        assert store.load_ack_refs() == {"ref-a"}
        noop = store.update_ack_refs(
            lambda current: (current, False, "already-present")
        )
        assert noop == UpdateAckRefsResult(False, "already-present")


class TestPosixContractErrorsAndEnvelope:
    def test_missing_state_contract(self, tmp_path):
        store = _posix_store(tmp_path / "missing")
        assert store.snapshot(_allow_all) == {}
        assert store.fingerprint(_allow_all) == ()
        assert store.load_ack_refs() == set()
        assert store.clear("email") is False
        result = store.compare_update_channel(
            "email", None, lambda current: ({**current, "new": True}, True, None)
        )
        assert result.applied and result.changed

    def test_malformed_channel_has_version_and_cannot_match_absence(self, tmp_path):
        workdir = tmp_path / "malformed-channel"
        channel = workdir / ".notification" / "email.json"
        channel.parent.mkdir(parents=True)
        channel.write_text("{not-json", encoding="utf-8")
        store = _posix_store(workdir)
        assert store.snapshot(_allow_all) == {}
        delivered = store.fingerprint(_allow_all)[0]
        absent = store.compare_update_channel(
            "email", None, lambda _current: ({"bad": "overwrite"}, True, None)
        )
        assert absent.conflict and channel.read_text(encoding="utf-8") == "{not-json"
        seen = []
        matched = store.compare_update_channel(
            "email",
            delivered,
            lambda current: (seen.append(current) or {"recovered": True}, True, "ok"),
        )
        assert matched.applied and seen == [{}]

    def test_malformed_ack_is_legacy_empty(self, tmp_path):
        ack = tmp_path / "malformed-ack" / ".notification" / "large_result_acks.json"
        ack.parent.mkdir(parents=True)
        ack.write_text("not-json", encoding="utf-8")
        assert _posix_store(tmp_path / "malformed-ack").load_ack_refs() == set()

    def test_unreadable_entries_skip_reads_but_compare_propagates(
        self, tmp_path, monkeypatch
    ):
        workdir = tmp_path / "unreadable"
        target = workdir / ".notification" / "email.json"
        target.parent.mkdir(parents=True)
        target.write_text("{}", encoding="utf-8")
        original = Path.read_bytes

        def unreadable(path):
            if path == target:
                raise PermissionError("denied")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", unreadable)
        store = _posix_store(workdir)
        assert store.snapshot(_allow_all) == {}
        assert store.fingerprint(_allow_all) == ()
        with pytest.raises(PermissionError, match="denied"):
            store.compare_update_channel(
                "email", None, lambda current: (current, False, None)
            )

    def test_publish_and_clear_non_enoent_errors_propagate(self, tmp_path, monkeypatch):
        import lingtai.adapters.posix.notification_store as adapter_module

        store = _posix_store(tmp_path / "write-errors")

        def write_error(*_args, **_kwargs):
            raise OSError("write failed")

        monkeypatch.setattr(adapter_module, "atomic_write_json", write_error)
        with pytest.raises(OSError, match="write failed"):
            store.publish("email", {})

        def unlink_error(_path):
            raise PermissionError("clear denied")

        monkeypatch.setattr(Path, "unlink", unlink_error)
        with pytest.raises(PermissionError, match="clear denied"):
            store.clear("email")

    def test_ack_unreadable_is_empty_and_empty_clear_is_best_effort(
        self, tmp_path, monkeypatch
    ):
        store = _posix_store(tmp_path / "ack-errors")

        def read_error(_path, *_args, **_kwargs):
            raise PermissionError("ack denied")

        monkeypatch.setattr(Path, "read_text", read_error)
        assert store.load_ack_refs() == set()

        def unlink_error(_path):
            raise PermissionError("ack clear denied")

        monkeypatch.setattr(Path, "unlink", unlink_error)
        result = store.update_ack_refs(lambda _current: (set(), True, "purged"))
        assert result == UpdateAckRefsResult(changed=False, value="purged")

    def test_external_mcp_envelope_and_licc_layout_remain_visible(self, tmp_path):
        workdir = tmp_path / "external-envelope"
        notification_dir = workdir / ".notification"
        notification_dir.mkdir(parents=True)
        envelope = {
            "header": "2 new events from MCP 'telegram'",
            "icon": "💬",
            "priority": "high",
            "published_at": "2026-07-12T00:00:00Z",
            "instructions": "Call the MCP read action.",
            "data": {"count": 2, "source": "telegram", "previews": []},
        }
        (notification_dir / "mcp.telegram.json").write_text(
            json.dumps(envelope), encoding="utf-8"
        )
        snapshot = _posix_store(workdir).snapshot(is_channel_allowed)
        assert snapshot == {"mcp.telegram": envelope}


@dataclass
class _CoreAgent:
    _notification_store: NotificationStorePort
    _notification_fp: tuple = ()
    _logs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _wake_reasons: list[str] = field(default_factory=list)

    def _log(self, event: str, **fields: Any) -> None:
        self._logs.append((event, fields))

    def _wake_nap(self, reason: str) -> None:
        self._wake_reasons.append(reason)


def _system_events(store: NotificationStorePort) -> list[dict]:
    payload = store.snapshot(_allow_all).get("system", {})
    return payload.get("data", {}).get("events", []) if isinstance(payload, dict) else []


class TestAtomicCoreRedCounterexamples:
    def test_real_adapter_concurrent_channel_updates_are_serialized(self, tmp_path):
        store = _posix_store(tmp_path / "channel-concurrency")
        barrier = threading.Barrier(13)

        def increment() -> None:
            barrier.wait()
            store.compare_update_channel(
                "system",
                UNCONDITIONAL,
                lambda current: (
                    {"count": int(current.get("count", 0)) + 1},
                    True,
                    None,
                ),
            )

        threads = [threading.Thread(target=increment) for _ in range(12)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert store.snapshot(_allow_all)["system"]["count"] == 12

    def test_concurrent_ack_unions_use_atomic_family_seven(self, tmp_path):
        store = _posix_store(tmp_path / "ack-unions")
        agent = _CoreAgent(store)
        barrier = threading.Barrier(17)

        def union(index: int) -> None:
            barrier.wait()
            ack_large_result_refs(agent, {f"ref-{index}"})

        threads = [threading.Thread(target=union, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert store.load_ack_refs() == {f"ref-{i}" for i in range(16)}

    def test_concurrent_ack_union_and_purge_has_serial_result(self, tmp_path):
        store = _posix_store(tmp_path / "ack-union-purge")
        agent = _CoreAgent(store)
        store.update_ack_refs(
            lambda _current: ({"keep", "stale"}, True, "seed")
        )
        barrier = threading.Barrier(3)

        def union() -> None:
            barrier.wait()
            ack_large_result_refs(agent, {"new"})

        def purge() -> None:
            barrier.wait()
            purge_stale_large_result_acks(agent, {"keep", "new"})

        threads = [threading.Thread(target=union), threading.Thread(target=purge)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert store.load_ack_refs() == {"keep", "new"}

    @pytest.mark.parametrize("selector", ("event_id", "ref_id"))
    def test_concurrent_enqueue_vs_force_event_or_ref_dismiss_keeps_unrelated(
        self, selector
    ):
        store = FakeNotificationStore()
        agent = _CoreAgent(store)
        old_id = _enqueue_system_notification(
            agent, source="daemon", ref_id="old-ref", body="old"
        )
        barrier = threading.Barrier(3)
        dismiss_result = []

        def enqueue() -> None:
            barrier.wait()
            _enqueue_system_notification(
                agent, source="daemon", ref_id="concurrent-ref", body="new"
            )

        def dismiss() -> None:
            barrier.wait()
            kwargs = {selector: old_id if selector == "event_id" else "old-ref"}
            dismiss_result.append(
                dismiss_channel(
                    agent,
                    "system",
                    invoked_by="notification",
                    force=True,
                    **kwargs,
                )
            )

        threads = [threading.Thread(target=enqueue), threading.Thread(target=dismiss)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert dismiss_result[0]["status"] == "ok"
        refs = {event["ref_id"] for event in _system_events(store)}
        assert "old-ref" not in refs
        assert "concurrent-ref" in refs

    def test_concurrent_enqueue_makes_whole_nonforce_dismiss_stale(self):
        store = FakeNotificationStore()
        agent = _CoreAgent(store)
        _enqueue_system_notification(agent, source="daemon", ref_id="old", body="old")
        agent._notification_fp = store.fingerprint(is_channel_allowed)
        start = threading.Barrier(3)
        enqueued = threading.Event()
        result = []

        def producer() -> None:
            start.wait()
            _enqueue_system_notification(agent, source="daemon", ref_id="new", body="new")
            enqueued.set()

        def dismiss() -> None:
            start.wait()
            assert enqueued.wait(timeout=5)
            result.append(
                dismiss_channel(agent, "system", invoked_by="notification")
            )

        threads = [threading.Thread(target=producer), threading.Thread(target=dismiss)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert result[0]["reason"] == "stale_channel_version"
        assert {event["ref_id"] for event in _system_events(store)} == {"old", "new"}

    def test_guarded_force_and_protected_goal_are_actual_dismissals(self):
        store = FakeNotificationStore()
        agent = _CoreAgent(store)
        register_generic_dismiss_guard("email", "email(action='read')")
        store.publish("email", {"header": "unread"})
        guarded = dismiss_channel(agent, "email", invoked_by="notification")
        assert guarded["reason"] == "guarded"
        assert "email" in store.snapshot(_allow_all)
        forced = dismiss_channel(
            agent, "email", invoked_by="notification", force=True
        )
        assert forced["status"] == "ok" and forced["cleared"] is True

        store.publish("goal", {"data": {"status": "active"}})
        protected = dismiss_channel(
            agent, "goal", invoked_by="notification", force=True
        )
        assert protected["reason"] == "protected_channel"
        assert "goal" in store.snapshot(_allow_all)

    def test_stale_expected_absence_refusal_at_core_boundary(self):
        store = FakeNotificationStore()
        agent = _CoreAgent(store, _notification_fp=())
        store.publish("soul", {"header": "arrived after delivery"})
        result = dismiss_channel(agent, "soul", invoked_by="notification")
        assert result["reason"] == "stale_channel_version"
        assert store.snapshot(_allow_all)["soul"]["header"] == "arrived after delivery"

    def test_nudge_updates_share_store_serialization_and_interact_with_dismiss(self):
        store = FakeNotificationStore()
        agent = _CoreAgent(store)
        barrier = threading.Barrier(9)

        def upsert(index: int) -> None:
            barrier.wait()
            upsert_nudge(agent, f"kind-{index}", {"body": str(index)})

        threads = [threading.Thread(target=upsert, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        nudges = store.snapshot(_allow_all)["nudge"]["data"]["nudges"]
        assert {entry["kind"] for entry in nudges} == {f"kind-{i}" for i in range(8)}
        assert store.channel_mutations.count("nudge") == 8

        dismiss = dismiss_channel(
            agent, "nudge", invoked_by="notification", force=True
        )
        assert dismiss["cleared"] is True
        upsert_nudge(agent, "after-dismiss", {"body": "current"})
        remove_nudge(agent, "missing")
        nudges = store.snapshot(_allow_all)["nudge"]["data"]["nudges"]
        assert len(nudges) == 1
        assert nudges[0]["body"] == "current"
        assert nudges[0]["kind"] == "after-dismiss"
        assert nudges[0]["policy"]["enabled"] == "on"
        assert nudges[0]["policy"]["repeat_after_dismiss"] == "24h"


class TestCompositionAndProvenance:
    def test_submit_signature_is_strict_and_store_backed(self):
        signature = inspect.signature(submit)
        assert list(signature.parameters) == [
            "agent",
            "tool_name",
            "data",
            "header",
            "icon",
            "priority",
            "instructions",
        ]
        with pytest.raises(AttributeError):
            submit(Path("not-an-agent"), "email", data={}, header="strict")
        store = FakeNotificationStore()
        submit(SimpleNamespace(_notification_store=store), "email", data={"x": 1}, header="ok")
        assert store.snapshot(_allow_all)["email"]["data"] == {"x": 1}

    def test_import_provenance_is_current_worktree(self):
        import lingtai
        import lingtai.kernel.notification_store as port_module

        repo = Path(__file__).resolve().parents[1]
        assert Path(lingtai.__file__).resolve().is_relative_to(repo / "src")
        assert Path(port_module.__file__).resolve() == (
            repo / "src/lingtai/kernel/notification_store/__init__.py"
        )

    def test_soul_inquiry_calls_strict_submit_with_agent(self):
        import ast
        import lingtai.tools.soul.inquiry as inquiry

        source = Path(inquiry.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "submit"
        ]
        assert calls
        assert all(
            isinstance(call.args[0], ast.Name) and call.args[0].id == "agent"
            for call in calls
        )

    def test_telegram_server_composes_one_store_and_injects_same_instance(
        self, tmp_path, monkeypatch
    ):
        import lingtai.mcp_servers.telegram.server as server

        sentinel_store = FakeNotificationStore()
        captured = {}

        class Service:
            def __init__(self, **kwargs):
                captured["service_kwargs"] = kwargs

        class Manager:
            def __init__(self, **kwargs):
                captured["manager_kwargs"] = kwargs

        monkeypatch.setattr(server, "load_config", lambda: {"accounts": [{"alias": "main"}]})
        monkeypatch.setattr(server, "TelegramService", Service)
        monkeypatch.setattr(server, "TelegramManager", Manager)
        monkeypatch.setattr(
            server, "PosixNotificationStoreAdapter", lambda workdir: sentinel_store
        )
        monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path / "telegram-server"))
        manager, working_dir = server.build_manager()
        assert manager.__class__ is Manager
        assert captured["manager_kwargs"]["notification_store"] is sentinel_store
        assert captured["manager_kwargs"]["working_dir"] == working_dir
