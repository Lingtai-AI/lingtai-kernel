"""Tests for daemon(action='check') falling back to historical run dirs.

Regression coverage for the post-refresh/molt bug: immediately after a
refresh or molt the parent gets a fresh ``DaemonManager`` whose in-memory
``_emanations`` registry is empty (``__init__`` deliberately does NOT
reconstruct registry entries from disk). A daemon terminal notification still
points at a valid ``daemons/<run_id>/result.txt``, but historical lookup used
to consult only the empty in-memory registry.

These tests build completed historical run dirs on disk, then construct a
fresh manager (empty registry, simulating post-refresh) and assert that
``check`` resolves compact exact run ids and unique legacy handles, while
rejecting ambiguous legacy short ids without returning an unbounded path list.
"""
import json

from tests._daemon_helpers import make_daemon_agent as _make_agent


def _make_completed_run_dir(agent, em_id="em-5", result="final report text", run_id=None):
    """Create a real on-disk run dir, mark it done, then forget it.

    Returns the DaemonRunDir so the test can read back its paths. The caller
    is expected NOT to register it in any manager's ``_emanations`` — this is
    a historical, completed run only present on disk.
    """
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    rd = DaemonRunDir(
        parent_working_dir=agent._working_dir,
        handle=em_id,
        run_id=run_id,
        task="historical task",
        tools=["file"],
        model="mock-model",
        max_turns=30,
        timeout_s=300.0,
        parent_addr=agent._working_dir.name,
        parent_pid=12345,
        system_prompt="You are a daemon.",
    )
    rd.set_current_tool("read", {"file_path": "/tmp/x"})
    rd.clear_current_tool("ok")
    rd.mark_done(result)
    return rd


def _fresh_manager(agent):
    """A brand-new DaemonManager with an empty in-memory registry.

    Mirrors the post-refresh/molt state: the run dirs are on disk but the
    manager has reconstructed no registry entries.
    """
    mgr = agent.get_capability("daemon")
    assert mgr._emanations == {}, "expected an empty registry for this fixture"
    return mgr


def test_check_resolves_completed_historical_run_by_short_id(tmp_path):
    agent = _make_agent(tmp_path)
    rd = _make_completed_run_dir(agent, "em-5", "completed daemon work")

    mgr = _fresh_manager(agent)
    out = mgr.handle({"action": "check", "id": "em-5"})

    assert out.get("status") != "error", out
    assert out["id"] == "em-5"
    assert out["state"] == "done"
    assert out["path"] == str(rd.path)
    assert out["result_path"] == str(rd.result_path)
    assert out["result_preview"] == "completed daemon work"
    # The historical lookup is flagged so the parent knows it came from disk.
    assert out.get("source") == "history"
    # Events must still be tailed from the historical run dir.
    event_types = {e.get("event") for e in out["events"]}
    assert "daemon_done" in event_types



def test_check_resolves_completed_historical_run_by_compact_id(tmp_path):
    agent = _make_agent(tmp_path)
    rd = _make_completed_run_dir(agent, "em-abcd", "compact daemon work", run_id="em-abcd")

    mgr = _fresh_manager(agent)
    out = mgr.handle({"action": "check", "id": "em-abcd"})

    assert out.get("status") != "error", out
    assert out["id"] == "em-abcd"
    assert out["run_id"] == "em-abcd"
    assert out["path"] == str(rd.path)
    assert out["result_preview"] == "compact daemon work"
    assert out.get("source") == "history"
    assert "other_run_dirs" not in out

def test_check_resolves_completed_historical_run_by_run_id(tmp_path):
    agent = _make_agent(tmp_path)
    rd = _make_completed_run_dir(agent, "em-6", "by run id")

    mgr = _fresh_manager(agent)
    out = mgr.handle({"action": "check", "id": rd.run_id})

    assert out.get("status") != "error", out
    assert out["run_id"] == rd.run_id
    assert out["state"] == "done"
    assert out["path"] == str(rd.path)
    assert out.get("source") == "history"


def test_check_rejects_ambiguous_legacy_short_id_without_path_list(tmp_path):
    """Ambiguous legacy short ids are rejected without bloating the result."""
    agent = _make_agent(tmp_path)

    # Two completed legacy runs share the short handle em-7. Exact run ids must
    # still work, but the ambiguous handle should not return every matching path.
    daemons = agent._working_dir / "daemons"
    daemons.mkdir(parents=True, exist_ok=True)

    def _write_run(run_id, result):
        run_path = daemons / run_id
        (run_path / "logs").mkdir(parents=True)
        state = {
            "handle": "em-7",
            "run_id": run_id,
            "state": "done",
            "backend": "lingtai",
            "data_version": _current_data_version(),
            "result_path": str(run_path / "result.txt"),
            "result_preview": result,
            "finished_at": "2026-06-23T00:00:00Z",
        }
        (run_path / "daemon.json").write_text(json.dumps(state), encoding="utf-8")
        (run_path / "result.txt").write_text(result, encoding="utf-8")
        (run_path / "logs" / "events.jsonl").write_text(
            json.dumps({"event": "daemon_done", "run_id": run_id}) + "\n",
            encoding="utf-8",
        )
        return run_path

    _write_run("em-7-20260623-100000-aaaaaa", "older run")
    newer = _write_run("em-7-20260623-110000-bbbbbb", "newer run")

    mgr = _fresh_manager(agent)
    out = mgr.handle({"action": "check", "id": "em-7"})

    assert out["status"] == "error"
    assert out.get("ambiguous") is True
    assert out.get("match_count") == 2
    assert out.get("latest_run_id") == "em-7-20260623-110000-bbbbbb"
    assert "use the exact run_id" in out["message"]
    assert "other_run_dirs" not in out

    exact = mgr.handle({"action": "check", "id": "em-7-20260623-110000-bbbbbb"})
    assert exact.get("status") != "error", exact
    assert exact["run_id"] == "em-7-20260623-110000-bbbbbb"
    assert exact["path"] == str(newer)
    assert exact["result_preview"] == "newer run"


def test_check_truly_unknown_id_still_errors(tmp_path):
    """A short id with no in-memory entry AND no run dir on disk still errors."""
    agent = _make_agent(tmp_path)
    mgr = _fresh_manager(agent)
    out = mgr.handle({"action": "check", "id": "em-999"})
    assert out["status"] == "error"
    assert "em-999" in out["message"]


def _current_data_version():
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    return getattr(DaemonRunDir, "DATA_VERSION", 1)
