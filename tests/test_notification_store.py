"""S4 Notification Store conformance and hostile red-counterexamples.

Filesystem cases use ordinary portable pytest ``tmp_path`` composition.  Local
execution remains a parent-owned safety gate and is not encoded into the test
suite's source semantics.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import secrets
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
        assert store.snapshot(_allow_all)["nudge"]["data"]["nudges"] == [
            {"body": "current", "kind": "after-dismiss"}
        ]


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


# ---------------------------------------------------------------------------
# Interprocess mutation serialization — INDEPENDENT PosixNotificationStoreAdapter
# instances in SEPARATE OS processes (not threads sharing one interpreter/GIL
# and one in-process threading.Lock). Reuses the same barrier-synchronized
# real-subprocess design already proven in
# tests/test_daemon_refresh_survival.py::_run_subprocess_race: every child
# completes setup (imports, adapter construction) BEFORE signaling ready, then
# all children wait at a real filesystem barrier and are released as close to
# simultaneously as the OS scheduler allows, so the race window is genuinely
# simultaneous rather than merely sequentially-compatible.
# ---------------------------------------------------------------------------

_STORE_BARRIER_CHILD_SCRIPT = """
import os, sys, time

barrier_dir = sys.argv[1]

# --- setup_body: imports and adapter construction — completes BEFORE the
# --- ready signal, so none of it can stagger/serialize the actual
# --- post-release race operation below.
{setup_body}

ready_path = os.path.join(barrier_dir, 'ready.' + str(os.getpid()))
release_path = os.path.join(barrier_dir, 'release')
open(ready_path, 'x').close()
deadline = time.monotonic() + {timeout!r}
while not os.path.exists(release_path):
    if time.monotonic() > deadline:
        print('ERR:barrier_timeout')
        sys.exit(1)
    time.sleep(0.001)

# --- race_body: the narrow, race-sensitive Store operation only. Nothing
# --- above this point runs after release; nothing below it ran before it.
{race_body}
"""


def _run_store_subprocess_race(
    setup_body: str, race_body: str, n_procs: int, tmp_path, timeout: float = 15.0
) -> list:
    """Spawn ``n_procs`` real Python subprocesses that all race the SAME
    Store operation against the SAME workdir, synchronized by a real start
    barrier. See ``_STORE_BARRIER_CHILD_SCRIPT`` and the module-level
    docstring above for the exact ordering guarantee this provides.
    """
    import subprocess as _subprocess
    import sys as _sys
    import time as _time

    barrier_dir = tmp_path / f"store-barrier-{secrets.token_hex(4)}"
    barrier_dir.mkdir()
    full_script = _STORE_BARRIER_CHILD_SCRIPT.format(
        setup_body=setup_body, race_body=race_body, timeout=timeout
    )
    env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "src")}

    procs = [
        _subprocess.Popen(
            [_sys.executable, "-c", full_script, str(barrier_dir)],
            stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True,
            env=env,
        )
        for _ in range(n_procs)
    ]
    try:
        ready_deadline = _time.monotonic() + timeout
        while True:
            ready_count = len(list(barrier_dir.glob("ready.*")))
            if ready_count >= n_procs:
                break
            if _time.monotonic() > ready_deadline:
                raise TimeoutError(
                    f"only {ready_count}/{n_procs} child processes reached the start barrier "
                    f"within {timeout}s"
                )
            for p in procs:
                if p.poll() is not None:
                    raise RuntimeError(
                        f"child process exited before reaching the barrier (returncode={p.returncode})"
                    )
            _time.sleep(0.005)
        release_fd = os.open(str(barrier_dir / "release"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.close(release_fd)
        except OSError:
            pass
        results = []
        for p in procs:
            out, err = p.communicate(timeout=timeout)
            results.append((out.strip(), err))
        return results
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5.0)


class TestInterprocessMutationSerialization:
    def test_independent_processes_compare_update_one_channel_without_lost_updates(
        self, tmp_path
    ):
        """N separate OS processes, each with its OWN
        ``PosixNotificationStoreAdapter`` instance (no shared in-process
        lock possible), concurrently ``compare_update_channel`` the SAME
        channel with an unconditional increment. Without Store-owned
        interprocess serialization, two processes can race
        read-modify-write and lose an update. The final count must equal
        exactly N — proving no lost update under real process concurrency.
        """
        workdir = tmp_path / "interprocess-channel"
        workdir.mkdir()
        n_procs = 8
        setup_body = (
            "import sys; sys.path.insert(0, 'src'); "
            "from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter; "
            "from lingtai.kernel.notification_store import UNCONDITIONAL; "
            f"workdir = {str(workdir)!r}; "
            "store = PosixNotificationStoreAdapter(workdir)"
        )
        race_body = (
            "store.compare_update_channel(\n"
            "    'system', UNCONDITIONAL,\n"
            "    lambda current: ({'count': int(current.get('count', 0)) + 1}, True, None),\n"
            ")\n"
            "print('done')\n"
        )
        results = _run_store_subprocess_race(setup_body, race_body, n_procs, tmp_path)
        for out, err in results:
            assert out == "done", f"child did not complete cleanly: out={out!r} err={err!r}"

        final_store = PosixNotificationStoreAdapter(workdir)
        final = final_store.snapshot(_allow_all)["system"]["count"]
        assert final == n_procs, (
            f"expected exactly {n_procs} (no lost updates), got {final}"
        )

    def test_independent_processes_union_distinct_ack_refs_without_lost_updates(
        self, tmp_path
    ):
        """N separate OS processes, each with its OWN
        ``PosixNotificationStoreAdapter`` instance, concurrently
        ``update_ack_refs`` to add a DISTINCT ref each. The final ack-ref
        set must contain every distinct ref — proving no lost update
        under real process concurrency for family 7 (read/mutate/write-or-
        clear), not just family 3/5 (channel compare-update).
        """
        workdir = tmp_path / "interprocess-acks"
        workdir.mkdir()
        n_procs = 8
        setup_body = (
            "import sys; sys.path.insert(0, 'src'); "
            "from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter; "
            f"workdir = {str(workdir)!r}; "
            "store = PosixNotificationStoreAdapter(workdir); "
            "my_ref = 'ref-' + str(os.getpid())"
        )
        race_body = (
            "store.update_ack_refs(\n"
            "    lambda current: (current | {my_ref}, True, None),\n"
            ")\n"
            "print('done')\n"
        )
        results = _run_store_subprocess_race(setup_body, race_body, n_procs, tmp_path)
        for out, err in results:
            assert out == "done", f"child did not complete cleanly: out={out!r} err={err!r}"

        final_store = PosixNotificationStoreAdapter(workdir)
        final_refs = final_store.load_ack_refs()
        assert len(final_refs) == n_procs, (
            f"expected {n_procs} distinct refs (no lost updates), got {len(final_refs)}: {final_refs}"
        )
        assert all(ref.startswith("ref-") for ref in final_refs)


class TestInterprocessLockFileInvariants:
    def test_lock_file_persists_and_is_never_unlinked_after_mutation(self, tmp_path):
        """The interprocess lock authority must be a PERSISTENT file under
        ``.notification/`` — never unlinked after use (split-inode lock
        authority is forbidden per the design brief), and must not be
        picked up by snapshot/fingerprint's ``*.json`` channel glob (it
        must not carry a JSON-shaped name)."""
        workdir = tmp_path / "lock-file-invariant"
        store = _posix_store(workdir)
        store.publish("system", {"header": "hello"})
        store.clear("system")
        store.compare_update_channel(
            "system", UNCONDITIONAL, lambda current: ({"a": 1}, True, None)
        )
        store.update_ack_refs(lambda current: (current | {"x"}, True, None))

        notif_dir = workdir / ".notification"
        lock_candidates = [
            p for p in notif_dir.iterdir()
            if p.is_file() and not p.name.endswith(".json")
        ]
        assert lock_candidates, "expected a persistent non-JSON lock file under .notification/"
        for candidate in lock_candidates:
            assert candidate.exists(), "lock file must not be unlinked after mutation"

        # The lock file must never be picked up as a channel by snapshot/
        # fingerprint (both glob only *.json).
        snap = store.snapshot(_allow_all)
        assert all(name == "system" for name in snap.keys())
        fp = store.fingerprint(_allow_all)
        assert all(entry[0].endswith(".json") for entry in fp)

    def test_two_adapter_instances_same_process_still_serialize(self, tmp_path):
        """Same-process counterexample: TWO separate adapter INSTANCES (not
        the same instance, so the in-process ``threading.Lock`` on either
        instance alone cannot serialize the other) sharing one workdir must
        still be mutually exclusive via the shared interprocess lock file,
        proving the new lock authority — not merely the existing per-
        instance thread lock — is what provides cross-instance safety."""
        workdir = tmp_path / "two-instances-one-process"
        workdir.mkdir()
        store_a = PosixNotificationStoreAdapter(workdir)
        store_b = PosixNotificationStoreAdapter(workdir)
        # 20 worker threads + this main thread's own barrier.wait() below.
        barrier = threading.Barrier(21)

        def increment(store) -> None:
            barrier.wait()
            store.compare_update_channel(
                "system", UNCONDITIONAL,
                lambda current: ({"count": int(current.get("count", 0)) + 1}, True, None),
            )

        threads = [
            threading.Thread(target=increment, args=(store_a if i % 2 == 0 else store_b,))
            for i in range(20)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert store_a.snapshot(_allow_all)["system"]["count"] == 20


@pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork unavailable on this platform")
class TestForkedAdapterMutationSerialization:
    """Parent's real counterexample: forcing the lock fd open in the parent
    (via one real mutation) BEFORE fork, then inheriting the SAME adapter
    object into several children, must not silently split the lock
    authority. Before the PID-aware fix, every child inherited the parent's
    already-open flock fd (not mutually exclusive with the parent/siblings)
    and the parent's process-wide ``threading.Lock`` objects (unsafe if held
    at fork time) — this reproduced exactly as
    ``[2, 2, 2, 1, 2, 2, 1, 1, 1, 1]`` instead of 12 across 10 iterations.
    """

    def _run_fork_race(self, workdir: Path, n_children: int, barrier_dir: Path) -> list[int]:
        """Fork ``n_children`` real child processes, all using the SAME
        pre-forked, already-mutated adapter object, synchronized by a
        file-based barrier (a `threading.Barrier` does not survive fork).
        Returns each child's exit code; raises if any child hangs past a
        bounded deadline (proving no inherited-lock deadlock)."""
        import signal
        import time

        store = _posix_store(workdir)
        # Force the flock fd open and the process-wide lock object created
        # in the PARENT before any fork — this is the exact precondition
        # the parent's counterexample requires.
        store.publish("system", {"count": 0})
        store.compare_update_channel(
            "system", UNCONDITIONAL, lambda current: ({"count": 0}, True, None)
        )

        child_pids = []
        for i in range(n_children):
            pid = os.fork()
            if pid == 0:
                # Child: wait for the release barrier, then race the SAME
                # inherited adapter object's compare_update_channel once.
                ready_path = barrier_dir / f"ready.{os.getpid()}"
                release_path = barrier_dir / "release"
                ready_path.touch()
                deadline = time.monotonic() + 15.0
                while not release_path.exists():
                    if time.monotonic() > deadline:
                        os._exit(2)
                    time.sleep(0.001)
                try:
                    store.compare_update_channel(
                        "system", UNCONDITIONAL,
                        lambda current: ({"count": int(current.get("count", 0)) + 1}, True, None),
                    )
                except Exception:
                    os._exit(3)
                os._exit(0)
            child_pids.append(pid)

        ready_deadline = time.monotonic() + 15.0
        while len(list(barrier_dir.glob("ready.*"))) < n_children:
            if time.monotonic() > ready_deadline:
                for pid in child_pids:
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)
                raise TimeoutError("not all forked children reached the start barrier")
            time.sleep(0.005)
        (barrier_dir / "release").touch()

        exit_codes = []
        try:
            for pid in child_pids:
                _, status = os.waitpid(pid, 0)
                exit_codes.append(os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1)
        finally:
            for pid in child_pids:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.kill(pid, signal.SIGKILL)
        return exit_codes

    def test_forked_children_sharing_pre_forked_adapter_do_not_lose_updates(self, tmp_path):
        n_children = 12
        for iteration in range(10):
            workdir = tmp_path / f"fork-race-{iteration}"
            workdir.mkdir()
            barrier_dir = tmp_path / f"fork-barrier-{iteration}"
            barrier_dir.mkdir()

            exit_codes = self._run_fork_race(workdir, n_children, barrier_dir)
            assert exit_codes == [0] * n_children, (
                f"iteration {iteration}: not every forked child exited 0: {exit_codes}"
            )

            final_store = PosixNotificationStoreAdapter(workdir)
            final_count = final_store.snapshot(_allow_all)["system"]["count"]
            assert final_count == n_children, (
                f"iteration {iteration}: expected {n_children} (no lost updates "
                f"across forked children sharing one pre-forked adapter), got {final_count}"
            )

    def test_forked_child_does_not_deadlock_on_guard_held_at_fork_time(self, tmp_path):
        """Genuine held-lock-at-fork proof (replaces an earlier, insufficient
        version of this test that only forked AFTER every lock had already
        been released, which proved nothing about fork-while-locked safety).

        The PARENT thread directly acquires and KEEPS HOLDING
        `_PROCESS_LOCKS_GUARD` itself (the actual global guard object, not
        merely a `Store.held()` call that would release it) across the real
        `os.fork()` syscall, while the CHILD — which inherits that exact
        guard already locked, with no thread in the child that could ever
        release it — attempts one real Store mutation. Without the
        `os.register_at_fork(after_in_child=...)` reset, the child's first
        `_process_wide_lock()` call would try to acquire the inherited,
        permanently-locked guard and hang forever; this test proves it does
        not, under a bounded deadline, and kills+fails (rather than hanging
        the whole test run) if it ever does.
        """
        import signal
        import time

        from lingtai.adapters.posix import notification_store as ns_module

        workdir = tmp_path / "fork-guard-held-at-fork"
        workdir.mkdir()
        store = _posix_store(workdir)
        store.publish("system", {"count": 0})

        # Acquire the REAL module-level guard directly and hold it open
        # across fork — this is the exact danger case the brief requires:
        # some parent thread holds `_PROCESS_LOCKS_GUARD` at fork time.
        ns_module._PROCESS_LOCKS_GUARD.acquire()
        try:
            pid = os.fork()
            if pid == 0:
                # Child: the at-fork callback must already have replaced
                # its inherited (locked) guard with a fresh unlocked one
                # before this line runs — otherwise this call hangs forever
                # on a lock only the (nonexistent-in-this-process) parent
                # thread could release.
                try:
                    store.compare_update_channel(
                        "system", UNCONDITIONAL,
                        lambda current: ({"count": int(current.get("count", 0)) + 1}, True, None),
                    )
                except Exception:
                    os._exit(3)
                os._exit(0)

            deadline = time.monotonic() + 10.0
            child_finished = False
            while time.monotonic() < deadline:
                done_pid, status = os.waitpid(pid, os.WNOHANG)
                if done_pid == pid:
                    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
                        f"forked child did not exit 0: status={status}"
                    )
                    child_finished = True
                    break
                time.sleep(0.01)
            if not child_finished:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                pytest.fail(
                    "forked child deadlocked while the parent held "
                    "_PROCESS_LOCKS_GUARD across fork — the at-fork reset "
                    "did not take effect"
                )
        finally:
            # Release the guard the PARENT itself acquired, so this test
            # never leaves real global lock state held past its own scope.
            ns_module._PROCESS_LOCKS_GUARD.release()

        final_count = store.snapshot(_allow_all)["system"]["count"]
        assert final_count == 1
