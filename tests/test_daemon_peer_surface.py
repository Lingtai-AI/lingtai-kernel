"""Checkpoint C1 — in-process ``peer_send`` tool surface + call-time denial.

Scope (narrow): prove that

- in-process ``peer_author: true`` makes the native ``peer_send`` schema +
  handler present from session start, in BOTH ``_build_tool_surface`` paths;
- ``peer_author: false`` does not include ``peer_send``;
- ``_handle_emanate`` actually threads ``peer_author`` and ``source_em_id`` into
  ``_build_tool_surface`` (the real feature wiring, not just the builder flag);
- the handler closure derives source identity from the bound ``source_em_id`` /
  current run, never from daemon-supplied args;
- calling the handler fails closed (denial) when the source run is not in an
  active group: before any group, after ``group_reclaim``, and after global
  ``reclaim``.

No successful routing/delivery is implemented in C1; the in-active-group branch
returns an explicit deferral (``delivery_not_implemented``) which later
checkpoints replace with the router. CLI sentinel authoring is out of scope.

Tests drive the DaemonManager directly against injected emanation entries (and,
for the wiring tests, the public ``emanate`` path with a parked runner) so no
real LLM, subprocess, or pool work is required.
"""
import threading
import time
from concurrent.futures import Future
from unittest.mock import MagicMock

from lingtai_kernel.config import AgentConfig


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
        capabilities=capabilities or ["file", "daemon"],
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


def _make_group(mgr, agent, *, peer_author=True):
    """Create a two-member active group of in-process authors; return ids."""
    _inject(mgr, agent, em_id="em-1", backend="lingtai", peer_author=peer_author)
    _inject(mgr, agent, em_id="em-2", backend="lingtai", peer_author=peer_author)
    gc = mgr.handle({"action": "group_create", "members": [
        _member_spec("em-1", "alpha", author=peer_author),
        _member_spec("em-2", "beta", author=peer_author),
    ], "policy": {"allow_pairs": None}})
    assert gc["status"] == "created", gc
    return gc["group_id"]


# ---------------------------------------------------------------------------
# 1. Builder respects peer_author in both surface paths
# ---------------------------------------------------------------------------

def test_peer_send_schema_present_when_peer_author(tmp_path):
    """Default (parent-registered) surface path appends peer_send when opted in."""
    agent, mgr = _make_mgr(tmp_path)
    schemas, dispatch = mgr._build_tool_surface(
        ["file"], peer_author=True, source_em_id="em-1")
    names = {s.name for s in schemas}
    assert "peer_send" in names
    assert "peer_send" in dispatch
    assert callable(dispatch["peer_send"])


def test_peer_send_absent_without_peer_author(tmp_path):
    """No peer_send when peer_author is false (the default)."""
    agent, mgr = _make_mgr(tmp_path)
    schemas, dispatch = mgr._build_tool_surface(["file"])
    names = {s.name for s in schemas}
    assert "peer_send" not in names
    assert "peer_send" not in dispatch


def test_peer_send_added_in_preset_surface_path(tmp_path):
    """The preset-backed surface path also appends peer_send when opted in."""
    agent, mgr = _make_mgr(tmp_path)
    # An empty preset sandbox (no capabilities) — peer_send must still attach.
    schemas, dispatch = mgr._build_tool_surface(
        [], preset_surface=({}, {}), peer_author=True, source_em_id="em-1")
    names = {s.name for s in schemas}
    assert "peer_send" in names
    assert "peer_send" in dispatch


def test_preset_surface_path_omits_peer_send_without_optin(tmp_path):
    """Preset path stays clean when peer_author is false."""
    agent, mgr = _make_mgr(tmp_path)
    schemas, dispatch = mgr._build_tool_surface([], preset_surface=({}, {}))
    assert "peer_send" not in {s.name for s in schemas}
    assert "peer_send" not in dispatch


def test_peer_send_schema_shape(tmp_path):
    """The appended schema is the canonical peer.build_peer_send_schema()."""
    agent, mgr = _make_mgr(tmp_path)
    schemas, _ = mgr._build_tool_surface(
        ["file"], peer_author=True, source_em_id="em-1")
    sch = next(s for s in schemas if s.name == "peer_send")
    props = sch.parameters["properties"]
    assert set(props) == {"to_handle", "body", "in_reply_to"}
    assert sch.parameters["required"] == ["to_handle", "body"]


# ---------------------------------------------------------------------------
# 2. Real wiring: _handle_emanate threads the flags into _build_tool_surface
# ---------------------------------------------------------------------------

def test_emanate_threads_peer_author_and_source_em_id(tmp_path, monkeypatch):
    """emanate(peer_author=True) must call _build_tool_surface with the flag
    AND a non-None source_em_id — otherwise peer_send never attaches at session
    start even though the builder supports it."""
    agent, mgr = _make_mgr(tmp_path)

    calls = []
    orig = mgr._build_tool_surface

    def spy(requested, preset_surface=None, **kwargs):
        calls.append(kwargs)
        return orig(requested, preset_surface=preset_surface, **kwargs)

    monkeypatch.setattr(mgr, "_build_tool_surface", spy)
    park = threading.Event()
    monkeypatch.setattr(mgr, "_run_emanation", lambda *a, **k: park.wait(5) or "")
    monkeypatch.setattr(mgr, "_on_emanation_done", lambda *a, **k: None)

    try:
        res = mgr.handle({"action": "emanate", "tasks": [
            {"task": "a", "tools": ["file"], "peer_author": True},
        ]})
        assert res["status"] == "dispatched"
        assert len(calls) == 1
        assert calls[0].get("peer_author") is True
        assert calls[0].get("source_em_id") is not None
        # The peer_send tool is on the live dispatch for the entry.
        em_id = res["ids"][0]
        assert calls[0]["source_em_id"] == em_id
    finally:
        park.set()


def test_emanate_without_peer_author_passes_false(tmp_path, monkeypatch):
    """emanate without peer_author threads peer_author=False (no peer_send)."""
    agent, mgr = _make_mgr(tmp_path)

    calls = []
    orig = mgr._build_tool_surface

    def spy(requested, preset_surface=None, **kwargs):
        calls.append(kwargs)
        return orig(requested, preset_surface=preset_surface, **kwargs)

    monkeypatch.setattr(mgr, "_build_tool_surface", spy)
    park = threading.Event()
    monkeypatch.setattr(mgr, "_run_emanation", lambda *a, **k: park.wait(5) or "")
    monkeypatch.setattr(mgr, "_on_emanation_done", lambda *a, **k: None)

    try:
        res = mgr.handle({"action": "emanate", "tasks": [
            {"task": "a", "tools": ["file"]},
        ]})
        assert res["status"] == "dispatched"
        assert len(calls) == 1
        assert calls[0].get("peer_author") is False
    finally:
        park.set()


# ---------------------------------------------------------------------------
# 3. Handler fails closed when source is not deliverable (call-time denial)
# ---------------------------------------------------------------------------

def test_handler_not_in_group_denial(tmp_path):
    """Before any group exists, the handler returns a not_in_group denial."""
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="lingtai", peer_author=True)
    handler = mgr._make_peer_send_handler("em-1")
    res = handler({"to_handle": "beta", "body": "hi"})
    assert res["status"] == "not_in_group"


def test_handler_no_entry_fails_closed(tmp_path):
    """A handler bound to an unknown/cleared em_id fails closed (not_in_group)."""
    agent, mgr = _make_mgr(tmp_path)
    handler = mgr._make_peer_send_handler("em-does-not-exist")
    res = handler({"to_handle": "beta", "body": "hi"})
    assert res["status"] == "not_in_group"


def test_handler_ignores_daemon_supplied_identity_in_group(tmp_path):
    """In an active group, the handler resolves the real run_id from the bound
    source_em_id and ignores daemon-supplied identity/routing keys.

    Discriminating test: a handler that read identity from args would route as
    the fabricated source; one that derives it from the bound em_id sees em-1's
    real run in the active group and delivers (``sent``) instead of failing
    closed with not_in_group."""
    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)
    handler = mgr._make_peer_send_handler("em-1")
    res = handler({
        "to_handle": "beta", "body": "hi",
        # Daemon-supplied identity/routing keys — must all be ignored.
        "run_id": "fake-run", "from": "beta", "from_run_id": "fake",
        "group_id": "dg-fake", "source_adapter": "inproc",
    })
    # Reaching the authorized delivery branch proves identity was derived from
    # the bound em_id, not the fabricated args. (Delivery itself is covered by
    # test_daemon_peer_delivery.)
    assert res["status"] == "sent"


def test_handler_fails_closed_after_group_reclaim(tmp_path):
    """After group_reclaim, the handler fails closed (group_reclaimed)."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_group(mgr, agent)
    handler = mgr._make_peer_send_handler("em-1")
    # Sanity: deliverable (in-group) before reclaim.
    assert handler({"to_handle": "beta", "body": "x"})["status"] != "not_in_group"

    rc = mgr.handle({"action": "group_reclaim", "group_id": group_id})
    assert rc["status"] == "reclaimed"
    res = handler({"to_handle": "beta", "body": "x"})
    assert res["status"] == "group_reclaimed"


def test_handler_fails_closed_after_global_reclaim(tmp_path):
    """After global reclaim, the handler fails closed (not_in_group)."""
    agent, mgr = _make_mgr(tmp_path)
    _make_group(mgr, agent)
    handler = mgr._make_peer_send_handler("em-1")
    assert handler({"to_handle": "beta", "body": "x"})["status"] != "not_in_group"

    mgr.handle({"action": "reclaim"})
    res = handler({"to_handle": "beta", "body": "x"})
    # Groups + run-id indexes are cleared; source is no longer in any group.
    assert res["status"] in ("not_in_group", "group_reclaimed")


def test_handler_from_live_dispatch_is_wired(tmp_path):
    """The closure pulled off the live dispatch map behaves as the gate."""
    agent, mgr = _make_mgr(tmp_path)
    run_id = _inject(mgr, agent, em_id="em-1", backend="lingtai", peer_author=True)
    _, dispatch = mgr._build_tool_surface(
        ["file"], peer_author=True, source_em_id="em-1")
    res = dispatch["peer_send"]({"to_handle": "beta", "body": "hi"})
    assert res["status"] == "not_in_group"
