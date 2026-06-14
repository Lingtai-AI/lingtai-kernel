"""Checkpoint C2a — authorized in-process ``peer_send`` success (delivery) path.

Scope (narrow): prove that a native ``peer_send`` from an in-process author,
once authorized via ``peer.authorize_peer_message``, is accepted into the live
target's existing follow-up buffer with a provenance banner, returns the
``sent`` status, and is logged with metadata only (status / size / ids) — never
the full message body.

Out of scope here (later checkpoints): the broad denial/status matrix beyond
what C1 already covers, CLI sentinel authoring/post-turn parsing, queues,
retries, durable outbox.

Tests drive the DaemonManager directly against injected in-process emanation
entries — no real LLM, subprocess, or pool work is required. The injected
entries carry the same ``followup_buffer`` / ``followup_lock`` discipline a live
LingTai emanation uses, so delivery exercises the real buffer path.
"""
import time
import threading
from concurrent.futures import Future
from unittest.mock import MagicMock

from lingtai_kernel.config import AgentConfig


# ---------------------------------------------------------------------------
# Harness (mirrors test_daemon_peer_surface, plus followup_lock for delivery)
# ---------------------------------------------------------------------------

def _make_agent(tmp_path, capabilities=None):
    from lingtai.agent import Agent
    svc = MagicMock()
    svc.provider = "mock"
    svc.model = "mock-model"
    svc.create_session = MagicMock()
    svc.make_tool_result = MagicMock()
    return Agent(
        svc,
        working_dir=tmp_path / "daemon-agent",
        capabilities=capabilities or ["file", "daemon"],
        config=AgentConfig(),
    )


def _make_mgr(tmp_path):
    agent = _make_agent(tmp_path)
    return agent, agent.get_capability("daemon")


def _inject(mgr, agent, *, em_id, backend="lingtai", peer_author=False, done=False):
    """Register a fake in-process emanation entry; return its stable run_id."""
    from lingtai.core.daemon.run_dir import DaemonRunDir
    run_dir = DaemonRunDir(
        parent_working_dir=agent._working_dir,
        handle=em_id,
        task="test task",
        tools=["file"],
        model="mock-model",
        max_turns=30,
        timeout_s=300.0,
        parent_addr=agent._working_dir.name,
        parent_pid=12345,
        system_prompt="You are a daemon.",
        backend=backend,
    )
    fut: Future = Future()
    if done:
        fut.set_result("done")
    mgr._emanations[em_id] = {
        "future": fut,
        "task": "test task",
        "start_time": time.time(),
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
        "backend": backend,
        "peer_author": peer_author,
    }
    return run_dir.run_id


def _member_spec(em_id, handle, *, role=None, author=True, receive=True):
    return {
        "id": em_id,
        "handle": handle,
        "role": role,
        "can_author_peer_send": author,
        "can_receive_peer_message": receive,
    }


def _make_group(mgr, agent):
    """Active two-member group: alpha (author) -> beta (receive-only)."""
    _inject(mgr, agent, em_id="em-1", backend="lingtai", peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend="lingtai", peer_author=False)
    gc = mgr.handle({"action": "group_create", "members": [
        _member_spec("em-1", "alpha", author=True, receive=False),
        _member_spec("em-2", "beta", author=False, receive=True),
    ], "policy": {"allow_pairs": None}})
    assert gc["status"] == "created", gc
    return gc["group_id"]


def _capture_logs(mgr):
    """Patch mgr._log to record (event_type, fields) tuples; return the list."""
    events = []
    orig = mgr._log

    def spy(event_type, **fields):
        events.append((event_type, fields))
        return orig(event_type, **fields)

    mgr._log = spy
    return events


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_authorized_peer_send_delivers_to_target_buffer(tmp_path):
    """An authorized in-process peer_send returns ``sent`` and lands a
    provenance-bannered message in the target's follow-up buffer."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    handler = mgr._make_peer_send_handler("em-1")

    body = "ping from alpha"
    res = handler({"to_handle": "beta", "body": body})

    assert res["status"] == "sent", res
    assert res.get("message_id"), res

    # Target's live follow-up buffer received the bannered message.
    target = mgr._emanations["em-2"]
    with target["followup_lock"]:
        delivered = target["followup_buffer"]
    assert delivered, "target follow-up buffer is empty"
    assert "[peer message]" in delivered
    assert "from: @alpha" in delivered
    assert group_id in delivered
    assert body in delivered  # full body IS delivered to the peer (banner payload)


def test_peer_send_event_logs_metadata_not_body(tmp_path):
    """The peer event records status / size / ids but never the full body."""
    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)
    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")

    body = "secret-body-do-not-log-verbatim"
    res = handler({"to_handle": "beta", "body": body})
    assert res["status"] == "sent"

    peer_events = [e for e in events if "peer" in e[0]]
    assert peer_events, f"no peer event logged; got {[e[0] for e in events]}"
    _etype, fields = peer_events[-1]
    # Metadata present.
    assert fields.get("status") == "sent"
    assert fields.get("message_id")
    assert fields.get("to_handle") == "beta"
    assert fields.get("from_handle") == "alpha"
    # Size recorded (any of the common keys).
    assert any(k in fields for k in ("body_bytes", "size", "body_size")), fields
    # Full body NOT logged verbatim in any field value.
    for v in fields.values():
        assert body not in str(v), f"body leaked into log field: {v!r}"


def test_in_reply_to_is_threaded_into_banner(tmp_path):
    """An optional in_reply_to is carried through onto the delivered banner."""
    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)
    handler = mgr._make_peer_send_handler("em-1")

    res = handler({"to_handle": "beta", "body": "re: hello", "in_reply_to": "pm-prev-001"})
    assert res["status"] == "sent"

    delivered = mgr._emanations["em-2"]["followup_buffer"]
    assert "pm-prev-001" in delivered


# ---------------------------------------------------------------------------
# Lock discipline (final-plan: ``_group_lock`` and a target ``followup_lock``
# must never nest). Instrumented locks let us assert the contract directly and
# drive the fail-closed counter branch deterministically, with no real threads.
# ---------------------------------------------------------------------------

class _TrackingLock:
    """Wrap a real lock, exposing whether it is currently held."""

    def __init__(self):
        self._lock = threading.Lock()
        self.held = False

    def __enter__(self):
        self._lock.acquire()
        self.held = True
        return self

    def __exit__(self, *exc):
        self.held = False
        self._lock.release()
        return False

    def acquire(self, *a, **k):
        acquired = self._lock.acquire(*a, **k)
        if acquired:
            self.held = True
        return acquired

    def release(self):
        self.held = False
        self._lock.release()


class _GuardedFollowupLock:
    """Target follow-up lock that records any acquisition made while the group
    lock is held, and can run a one-shot side effect on first entry."""

    def __init__(self, group_lock, on_enter=None):
        self._lock = threading.Lock()
        self._group_lock = group_lock
        self._on_enter = on_enter
        self.violations = []
        self.enter_count = 0

    def __enter__(self):
        if getattr(self._group_lock, "held", False):
            self.violations.append(
                "target followup_lock acquired while _group_lock was held")
        self._lock.acquire()
        self.enter_count += 1
        if self._on_enter is not None:
            cb, self._on_enter = self._on_enter, None
            cb()
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


def test_followup_lock_not_acquired_while_group_lock_held(tmp_path):
    """Regression for the C2a lock-order bug: the target ``followup_lock`` must
    never be acquired while ``_group_lock`` is held."""
    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)

    tracking = _TrackingLock()
    mgr._group_lock = tracking
    guarded = _GuardedFollowupLock(tracking)
    mgr._emanations["em-2"]["followup_lock"] = guarded

    handler = mgr._make_peer_send_handler("em-1")
    res = handler({"to_handle": "beta", "body": "ping from alpha"})

    assert res["status"] == "sent", res
    assert guarded.enter_count == 1, "delivery should take the followup lock once"
    assert not guarded.violations, guarded.violations
    # Delivery still landed.
    assert "[peer message]" in mgr._emanations["em-2"]["followup_buffer"]


def test_counter_update_fails_closed_when_group_reclaimed_mid_flight(tmp_path):
    """If the group is reclaimed after delivery but before the counter update,
    the message stays delivered (``sent``) and the counter is left untouched —
    no corruption, no resurrecting a dead group."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)

    tracking = _TrackingLock()
    mgr._group_lock = tracking

    # Reclaim the group the instant the target buffer lock is taken (i.e. after
    # authorization + snapshot under the group lock, before the counter update).
    def reclaim_now():
        mgr.handle({"action": "group_reclaim", "group_id": group_id})

    guarded = _GuardedFollowupLock(tracking, on_enter=reclaim_now)
    mgr._emanations["em-2"]["followup_lock"] = guarded

    handler = mgr._make_peer_send_handler("em-1")
    res = handler({"to_handle": "beta", "body": "ping"})

    # Delivery is honest: the banner was appended before the reclaim landed.
    assert res["status"] == "sent", res
    assert not guarded.violations, guarded.violations
    assert "[peer message]" in mgr._emanations["em-2"]["followup_buffer"]
    # Counter was NOT bumped on a now-reclaimed group.
    assert mgr._groups[group_id].state == "reclaimed"
    assert mgr._groups[group_id].message_count == 0


def test_peer_send_rechecks_target_run_id_outside_group_lock(tmp_path):
    """The live re-check (outside the group lock) rejects a target whose run_id
    no longer matches the authorized snapshot (e.g. reclaimed + re-emanated)."""
    from lingtai.core.daemon.run_dir import DaemonRunDir

    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)

    # Swap em-2's run_dir for a fresh one: the random run_id suffix guarantees a
    # different run_id than the roster captured at group_create time.
    stale_run_id = mgr._emanations["em-2"]["run_dir"].run_id
    fresh = DaemonRunDir(
        parent_working_dir=agent._working_dir,
        handle="em-2",
        task="test task",
        tools=["file"],
        model="mock-model",
        max_turns=30,
        timeout_s=300.0,
        parent_addr=agent._working_dir.name,
        parent_pid=12345,
        system_prompt="You are a daemon.",
        backend="lingtai",
    )
    assert fresh.run_id != stale_run_id
    mgr._emanations["em-2"]["run_dir"] = fresh

    handler = mgr._make_peer_send_handler("em-1")
    res = handler({"to_handle": "beta", "body": "ping"})

    assert res["status"] == "not_ready", res
    assert res["reason"] == "target_not_live"
    # Nothing was delivered.
    assert mgr._emanations["em-2"]["followup_buffer"] == ""


# ---------------------------------------------------------------------------
# Checkpoint C2b — targeted post-authorization live-target status matrix.
#
# After authorization passes, the live re-check (outside the group lock) splits
# into distinct, body-free statuses instead of one generic ``not_ready``:
#   - target completed (future resolved, run_id still matches) -> ``target_done``
#   - target entry gone / stale run_id / no follow-up channel   -> ``not_ready``
#   - target's follow-up buffer already has pending text        -> ``busy``
# None of these bump the per-group counter; only ``sent`` does. No queue/retry.
# ---------------------------------------------------------------------------

def test_target_completed_returns_target_done(tmp_path):
    """A target whose future is already resolved (same run_id) yields
    ``target_done`` — detected via ``future.done()``, never by string-matching
    ``_handle_ask`` output, and never the generic ``not_ready``."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    # Complete the EXISTING future so the run_id still matches the authorized
    # snapshot (a normally-finished daemon keeps its original run_dir).
    mgr._emanations["em-2"]["future"].set_result("done")

    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")
    res = handler({"to_handle": "beta", "body": "ping after done"})

    assert res["status"] == "target_done", res
    assert res["reason"] == "target_completed", res
    assert res.get("message_id"), res
    # Nothing delivered; counter untouched.
    assert mgr._emanations["em-2"]["followup_buffer"] == ""
    assert mgr._groups[group_id].message_count == 0
    # Logged as undelivered metadata, body-free.
    peer_events = [e for e in events if "peer" in e[0]]
    assert peer_events, "no peer event logged"
    _etype, fields = peer_events[-1]
    assert fields.get("status") == "target_done"
    assert fields.get("reason") == "target_completed"
    assert "ping after done" not in str(fields)


def test_missing_target_entry_returns_not_ready(tmp_path):
    """If the target's in-process entry has been cleared between authorization
    and delivery, the send is ``not_ready`` with a precise reason — no queue."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    # Drop the target entry after authorization basis was captured at create.
    del mgr._emanations["em-2"]

    handler = mgr._make_peer_send_handler("em-1")
    res = handler({"to_handle": "beta", "body": "ping into the void"})

    assert res["status"] == "not_ready", res
    assert res["reason"] == "target_missing", res
    assert res.get("message_id"), res
    assert mgr._groups[group_id].message_count == 0


def test_busy_target_does_not_append_and_returns_busy(tmp_path):
    """If the target's follow-up buffer already holds pending text, peer_send
    must NOT append a second hidden message — it returns ``busy`` and leaves the
    buffer untouched. No queue, counter untouched, body-free log."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    pending = "pre-existing unread follow-up"
    mgr._emanations["em-2"]["followup_buffer"] = pending

    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")
    res = handler({"to_handle": "beta", "body": "second message should not land"})

    assert res["status"] == "busy", res
    assert res["reason"] == "peer_busy", res
    assert res.get("message_id"), res
    # Buffer is exactly what it was: nothing appended.
    assert mgr._emanations["em-2"]["followup_buffer"] == pending
    # No counter bump on a busy target.
    assert mgr._groups[group_id].message_count == 0
    # Body-free log.
    peer_events = [e for e in events if "peer" in e[0]]
    assert peer_events, "no peer event logged"
    _etype, fields = peer_events[-1]
    assert fields.get("status") == "busy"
    for v in fields.values():
        assert "second message should not land" not in str(v)


# ---------------------------------------------------------------------------
# Policy/authorization denial surfaces. These already flow through the single
# ``peer.authorize_peer_message`` gate (regression locks, not red-first): assert
# the returned status / reason / message_id and the log event are stable and the
# body never leaks into any log field.
# ---------------------------------------------------------------------------

def _assert_denial_logged_body_free(events, *, status, body):
    peer_events = [e for e in events if "peer" in e[0]]
    assert peer_events, "no peer event logged"
    _etype, fields = peer_events[-1]
    assert fields.get("status") == status, fields
    assert fields.get("message_id"), fields
    for v in fields.values():
        assert body not in str(v), f"body leaked into log field: {v!r}"


def test_unknown_handle_is_denied_unknown_peer(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)
    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")

    body = "to-nobody"
    res = handler({"to_handle": "ghost", "body": body})
    assert res["status"] == "unknown_peer", res
    assert res["reason"] == "unknown_target_handle", res
    assert res.get("message_id"), res
    assert mgr._emanations["em-2"]["followup_buffer"] == ""
    _assert_denial_logged_body_free(events, status="unknown_peer", body=body)


def test_target_cannot_receive_is_denied(tmp_path):
    """alpha is receive-only=False (can_receive_peer_message=False); a send
    addressed to it is denied with ``target_cannot_receive``."""
    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)
    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")

    body = "to-a-non-receiver"
    res = handler({"to_handle": "alpha", "body": body})
    assert res["status"] == "denied", res
    assert res["reason"] == "target_cannot_receive", res
    assert res.get("message_id"), res
    _assert_denial_logged_body_free(events, status="denied", body=body)


def test_message_too_large_is_denied(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    cap = mgr._groups[group_id].policy.max_message_bytes
    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")

    body = "X" * (cap + 1)
    res = handler({"to_handle": "beta", "body": body})
    assert res["status"] == "message_too_large", res
    assert res["reason"] == "body_exceeds_max_bytes", res
    assert res.get("message_id"), res
    assert mgr._emanations["em-2"]["followup_buffer"] == ""
    _assert_denial_logged_body_free(events, status="message_too_large", body=body)


def test_rate_capped_is_denied(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    grp = mgr._groups[group_id]
    grp.message_count = grp.policy.max_messages_per_group  # at the ceiling
    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")

    body = "one-too-many"
    res = handler({"to_handle": "beta", "body": body})
    assert res["status"] == "rate_capped", res
    assert res["reason"] == "message_cap_reached", res
    assert res.get("message_id"), res
    assert mgr._emanations["em-2"]["followup_buffer"] == ""
    _assert_denial_logged_body_free(events, status="rate_capped", body=body)


def test_hop_exhausted_is_denied(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    mgr._groups[group_id].policy.default_hop_budget = 0  # zero-hop guard
    events = _capture_logs(mgr)
    handler = mgr._make_peer_send_handler("em-1")

    body = "no-hops-left"
    res = handler({"to_handle": "beta", "body": body})
    assert res["status"] == "hop_exhausted", res
    assert res["reason"] == "hop_budget_zero", res
    assert res.get("message_id"), res
    assert mgr._emanations["em-2"]["followup_buffer"] == ""
    _assert_denial_logged_body_free(events, status="hop_exhausted", body=body)
