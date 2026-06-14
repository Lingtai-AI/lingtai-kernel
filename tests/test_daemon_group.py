"""Checkpoint B — parent-owned DaemonGroup lifecycle + schema surface.

Covers the parent actions ``group_create`` / ``group_reclaim`` /
``group_status``, global ``reclaim`` group cleanup, and the additive daemon
schema. No peer delivery, router, ChatSession tool injection, or CLI sentinel
routing here — those are Checkpoint C/D.

Tests drive the DaemonManager directly against injected emanation entries so
no real LLM, subprocess, or pool work is required.
"""
import threading
import time
from concurrent.futures import Future
from unittest.mock import MagicMock

from lingtai_kernel.config import AgentConfig

from lingtai.core.daemon import peer, get_schema


# ---------------------------------------------------------------------------
# Harness
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
        capabilities=capabilities or ["daemon"],
        config=AgentConfig(),
    )


def _make_mgr(tmp_path):
    agent = _make_agent(tmp_path)
    return agent, agent.get_capability("daemon")


def _inject(mgr, agent, *, em_id, backend="lingtai", peer_author=False, done=False):
    """Register a fake emanation entry and return its stable run_id."""
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
        "run_dir": run_dir,
        "backend": backend,
        "peer_author": peer_author,
    }
    return run_dir.run_id


def _member_spec(em_id, handle, *, role=None, author=True, receive=True):
    spec = {
        "id": em_id,
        "handle": handle,
        "can_author_peer_send": author,
        "can_receive_peer_message": receive,
    }
    if role is not None:
        spec["role"] = role
    return spec


def _default_policy():
    return {
        "max_message_bytes": 8192,
        "default_hop_budget": 1,
        "max_messages_per_group": 32,
        "allow_pairs": [["codex", "claude"], ["claude", "codex"]],
    }


def _create_pair(mgr, agent, **policy_overrides):
    """Inject two peer-author daemons and create a group over them."""
    rc = _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    rl = _inject(mgr, agent, em_id="em-2", backend="claude-code", peer_author=True)
    policy = _default_policy()
    policy.update(policy_overrides)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex", role="planner"),
            _member_spec("em-2", "claude", role="reviewer"),
        ],
        "policy": policy,
    })
    return result, rc, rl


# ---------------------------------------------------------------------------
# Schema surface (additive)
# ---------------------------------------------------------------------------

def test_group_actions_appear_in_daemon_schema():
    schema = get_schema("en")
    actions = schema["properties"]["action"]["enum"]
    # Existing actions still present.
    for existing in ["emanate", "list", "ask", "check", "reclaim"]:
        assert existing in actions
    # New parent actions added.
    for new in ["group_create", "group_reclaim", "group_status"]:
        assert new in actions


def test_group_create_fields_present_in_schema():
    props = get_schema("en")["properties"]
    assert "members" in props
    assert "policy" in props
    assert "group_id" in props


def test_peer_author_field_present_in_tasks_schema():
    tasks = get_schema("en")["properties"]["tasks"]
    task_props = tasks["items"]["properties"]
    assert "peer_author" in task_props
    assert task_props["peer_author"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# group_create — happy path
# ---------------------------------------------------------------------------

def test_group_create_registers_run_ids_and_handles(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result, rc, rl = _create_pair(mgr, agent)

    assert result["status"] == "created"
    assert result["group_id"].startswith("dg-")
    gid = result["group_id"]

    # Group is registered and indexed by stable run_id, not just em_id.
    assert gid in mgr._groups
    group = mgr._groups[gid]
    assert group.state == "active"
    assert set(group.roster_by_handle) == {"codex", "claude"}
    assert set(group.roster_by_run_id) == {rc, rl}
    assert mgr._group_by_run_id[rc] == gid
    assert mgr._group_by_run_id[rl] == gid

    # Members report stable run_id in the response.
    by_handle = {m["handle"]: m for m in result["members"]}
    assert by_handle["codex"]["run_id"] == rc
    assert by_handle["claude"]["run_id"] == rl
    assert by_handle["codex"]["backend"] == "codex"
    assert by_handle["codex"]["role"] == "planner"


def test_group_create_returns_per_member_roster_notices(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result, rc, rl = _create_pair(mgr, agent)
    notices = result["roster_notices"]
    assert set(notices) == {"codex", "claude"}
    # Each notice names its own handle and the peer, plus the provenance rule.
    assert "codex" in notices["codex"]
    assert "claude" in notices["codex"]
    assert "untrusted" in notices["codex"].lower()


def test_group_create_emits_group_created_event(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    events = []
    agent._log = lambda evt, **f: events.append((evt, f))
    _create_pair(mgr, agent)
    created = [f for evt, f in events if evt == "group_created"]
    assert created
    assert created[0]["group_id"].startswith("dg-")


# ---------------------------------------------------------------------------
# group_create — validation / rejections
# ---------------------------------------------------------------------------

def test_group_create_rejects_fewer_than_two_members(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    result = mgr.handle({
        "action": "group_create",
        "members": [_member_spec("em-1", "codex")],
        "policy": _default_policy(),
    })
    assert result["status"] == "error"
    assert result["reason"] == "too_few_members"


def test_group_create_rejects_duplicate_handle(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend="codex", peer_author=True)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex"),
            _member_spec("em-2", "codex"),
        ],
        "policy": _default_policy(),
    })
    assert result["status"] == "error"
    assert result["reason"] == "duplicate_handle"


def test_group_create_rejects_unknown_member(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex"),
            _member_spec("em-ghost", "claude"),
        ],
        "policy": _default_policy(),
    })
    assert result["status"] == "error"
    assert result["reason"] == "unknown_member"


def test_group_create_rejects_unsafe_handle(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend="codex", peer_author=True)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex"),
            _member_spec("em-2", "bad handle!"),
        ],
        "policy": _default_policy(),
    })
    assert result["status"] == "error"
    assert result["reason"] == "unsafe_handle"


def test_group_create_rejects_author_without_peer_author_optin(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend="codex", peer_author=False)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex", author=True),
            _member_spec("em-2", "claude", author=True),
        ],
        "policy": _default_policy(),
    })
    assert result["status"] == "error"
    assert result["reason"] == "author_without_optin"


def test_group_create_rejects_unsupported_author_backend(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend="opencode", peer_author=True)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex", author=True),
            _member_spec("em-2", "oc", author=True),
        ],
        "policy": _default_policy(),
    })
    assert result["status"] == "error"
    assert result["reason"] == "unsupported_author_backend"


def test_group_create_allows_unsupported_backend_as_receiver(tmp_path):
    # opencode/cursor are receiver-only in v0 — fine as long as not authoring.
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend="opencode", peer_author=False)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex", author=True, receive=True),
            _member_spec("em-2", "oc", author=False, receive=True),
        ],
        "policy": {"allow_pairs": None},
    })
    assert result["status"] == "created"


def test_group_create_rejects_completed_lingtai_member(tmp_path):
    # Completed in-process LingTai sessions are not resumable -> not eligible.
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend="lingtai", peer_author=False, done=True)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex", author=True),
            _member_spec("em-2", "claude", author=False),
        ],
        "policy": {"allow_pairs": None},
    })
    assert result["status"] == "error"
    assert result["reason"] == "completed_lingtai_member"


def test_group_create_rejects_member_already_in_active_group(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _create_pair(mgr, agent)
    # em-1 is already grouped; try to add it to a new group with a fresh peer.
    _inject(mgr, agent, em_id="em-3", backend="codex", peer_author=True)
    result = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codexA"),
            _member_spec("em-3", "codexB"),
        ],
        "policy": {"allow_pairs": None},
    })
    assert result["status"] == "error"
    assert result["reason"] == "already_in_group"


# ---------------------------------------------------------------------------
# group_status
# ---------------------------------------------------------------------------

def test_group_status_reports_state_roster_and_sent_count(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result, rc, rl = _create_pair(mgr, agent)
    gid = result["group_id"]
    # Simulate one accepted send for observability.
    mgr._groups[gid].message_count = 1

    status = mgr.handle({"action": "group_status", "group_id": gid})
    assert status["status"] == "ok"
    assert status["group_id"] == gid
    assert status["state"] == "active"
    assert status["sent_count"] == 1
    handles = {m["handle"] for m in status["members"]}
    assert handles == {"codex", "claude"}
    by_handle = {m["handle"]: m for m in status["members"]}
    assert by_handle["codex"]["run_id"] == rc


def test_group_status_unknown_group_errors(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    status = mgr.handle({"action": "group_status", "group_id": "dg-nope"})
    assert status["status"] == "error"
    assert status["reason"] == "unknown_group"


def test_group_status_lists_all_groups_without_id(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result, _, _ = _create_pair(mgr, agent)
    status = mgr.handle({"action": "group_status"})
    assert status["status"] == "ok"
    ids = {g["group_id"] for g in status["groups"]}
    assert result["group_id"] in ids


# ---------------------------------------------------------------------------
# group_reclaim
# ---------------------------------------------------------------------------

def test_group_reclaim_marks_reclaimed_and_unmaps_run_ids(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result, rc, rl = _create_pair(mgr, agent)
    gid = result["group_id"]

    reclaimed = mgr.handle({"action": "group_reclaim", "group_id": gid})
    assert reclaimed["status"] == "reclaimed"
    assert reclaimed["group_id"] == gid
    assert reclaimed["members"] == 2

    assert mgr._groups[gid].state == "reclaimed"
    # Run-id indexes are removed so members can join a new group.
    assert rc not in mgr._group_by_run_id
    assert rl not in mgr._group_by_run_id


def test_group_reclaim_emits_event(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    events = []
    result, _, _ = _create_pair(mgr, agent)
    agent._log = lambda evt, **f: events.append((evt, f))
    mgr.handle({"action": "group_reclaim", "group_id": result["group_id"]})
    reclaimed = [f for evt, f in events if evt == "group_reclaimed"]
    assert reclaimed
    assert reclaimed[0]["member_count"] == 2


def test_group_reclaim_unknown_group_errors(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result = mgr.handle({"action": "group_reclaim", "group_id": "dg-nope"})
    assert result["status"] == "error"
    assert result["reason"] == "unknown_group"


def test_member_can_rejoin_after_group_reclaim(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result, _, _ = _create_pair(mgr, agent)
    mgr.handle({"action": "group_reclaim", "group_id": result["group_id"]})
    # Same em-1/em-2 entries should now be free to form a new group.
    again = mgr.handle({
        "action": "group_create",
        "members": [
            _member_spec("em-1", "codex"),
            _member_spec("em-2", "claude"),
        ],
        "policy": {"allow_pairs": None},
    })
    assert again["status"] == "created"
    assert again["group_id"] != result["group_id"]


# ---------------------------------------------------------------------------
# Integration: peer_author flows from the public emanate path into group_create
# ---------------------------------------------------------------------------

def test_emanate_persists_peer_author_and_group_create_accepts(tmp_path, monkeypatch):
    """Drive the public emanate -> group_create path with a parked runner.

    Regression for Checkpoint B review: real in-process emanate entries must
    persist `peer_author` so an author-capable group can be created without the
    injected-entry test shortcut.
    """
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")

    # Park the in-process runner so the futures stay non-done while we create
    # the group (a completed in-process member would be rejected as
    # non-resumable). Suppress the done-callback to avoid notification plumbing.
    park = threading.Event()
    monkeypatch.setattr(mgr, "_run_emanation", lambda *a, **k: park.wait(5) or "")
    monkeypatch.setattr(mgr, "_on_emanation_done", lambda *a, **k: None)

    try:
        res = mgr.handle({"action": "emanate", "tasks": [
            {"task": "a", "tools": ["file"], "peer_author": True},
            {"task": "b", "tools": ["file"], "peer_author": True},
        ]})
        assert res["status"] == "dispatched"
        ids = res["ids"]
        # The flag is persisted on the real emanation entries.
        assert [mgr._emanations[i]["peer_author"] for i in ids] == [True, True]

        gc = mgr.handle({"action": "group_create", "members": [
            _member_spec(ids[0], "codex", author=True),
            _member_spec(ids[1], "claude", author=True),
        ], "policy": {"allow_pairs": None}})
        assert gc["status"] == "created"
    finally:
        park.set()


def test_emanate_without_peer_author_defaults_false(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    park = threading.Event()
    monkeypatch.setattr(mgr, "_run_emanation", lambda *a, **k: park.wait(5) or "")
    monkeypatch.setattr(mgr, "_on_emanation_done", lambda *a, **k: None)
    try:
        res = mgr.handle({"action": "emanate", "tasks": [
            {"task": "a", "tools": ["file"]},
        ]})
        em_id = res["ids"][0]
        assert mgr._emanations[em_id]["peer_author"] is False
        # A second eligible author so the roster meets the two-member minimum;
        # the real opt-out member is listed first so it fails on author gating.
        _inject(mgr, agent, em_id="em-extra", backend="codex", peer_author=True)
        gc = mgr.handle({"action": "group_create", "members": [
            _member_spec(em_id, "codex", author=True),
            _member_spec("em-extra", "claude", author=True),
        ], "policy": {"allow_pairs": None}})
        assert gc["status"] == "error"
        assert gc["reason"] == "author_without_optin"
    finally:
        park.set()


def test_emanate_rejects_peer_author_for_unsupported_backend(tmp_path):
    # opencode/cursor cannot author peer sends in v0 — reject the whole batch
    # at emanate preflight (no run_dir or scheduling happens).
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    for backend in ("opencode", "cursor"):
        res = mgr.handle({
            "action": "emanate",
            "backend": backend,
            "tasks": [{"task": "a", "tools": ["file"], "peer_author": True}],
        })
        assert res["status"] == "error", backend
        assert res["reason"] == "unsupported_author_backend", backend
    assert mgr._emanations == {}


def test_emanate_cli_persists_peer_author(tmp_path, monkeypatch):
    # CLI entries must also persist the flag. Park the CLI runner so the entry
    # is created without spawning a subprocess.
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    park = threading.Event()
    monkeypatch.setattr(mgr, "_run_codex_emanation",
                        lambda *a, **k: park.wait(5) or "")
    monkeypatch.setattr(mgr, "_on_emanation_done", lambda *a, **k: None)
    try:
        res = mgr.handle({
            "action": "emanate",
            "backend": "codex",
            "tasks": [{"task": "a", "tools": ["file"], "peer_author": True}],
        })
        assert res["status"] == "dispatched"
        em_id = res["ids"][0]
        entry = mgr._emanations[em_id]
        assert entry["peer_author"] is True
        assert entry["backend"] == "codex"
    finally:
        park.set()


# ---------------------------------------------------------------------------
# global reclaim cleanup
# ---------------------------------------------------------------------------

def test_global_reclaim_clears_groups_and_indexes(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    result, rc, rl = _create_pair(mgr, agent)
    assert mgr._groups

    out = mgr.handle({"action": "reclaim"})
    assert out["status"] == "reclaimed"
    assert mgr._groups == {}
    assert mgr._group_by_run_id == {}
