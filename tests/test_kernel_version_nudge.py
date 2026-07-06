import json
import threading
import time

from lingtai_kernel.notifications import collect_notifications
from lingtai_kernel.nudge import upsert
from lingtai_kernel.nudge import kernel_version as kv


class _Agent:
    def __init__(self, workdir):
        self._working_dir = workdir
        self.logs = []

    def _log(self, event, **fields):
        self.logs.append((event, fields))


def _entries(workdir):
    return (
        collect_notifications(workdir)
        .get("nudge", {})
        .get("data", {})
        .get("nudges", [])
    )


def _reset_fast_gate(agent):
    agent._nudge_kernel_version_state["last_probe_ts"] = 0.0


def _await_fetch(agent, timeout=2.0):
    """Block the test (never the heartbeat) until the in-flight fetch finishes."""
    pending = kv._fetch_slot(agent)
    assert pending is not None, "expected a fetch to be in flight"
    assert pending.done.wait(timeout), "worker thread did not finish"


def _drain_remote_fetch(agent):
    """Drive the async remote check to completion.

    The first due ``check()`` only spawns the worker; a later probe (past the
    fast gate) consumes the result. Tests that patch ``_fetch_latest_version``
    to return promptly use this to observe the emitted nudge synchronously.
    """
    kv.check(agent)
    _await_fetch(agent)
    _reset_fast_gate(agent)
    kv.check(agent)


def test_installed_runtime_refresh_nudge_does_not_hit_remote(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1",
            installed_version="0.14.2",
            dev_reason=None,
        ),
    )
    monkeypatch.setattr(
        kv,
        "_fetch_latest_version",
        lambda: (_ for _ in ()).throw(AssertionError("remote should not be queried")),
    )

    kv.check(agent)

    entries = _entries(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kind"] == "kernel_version"
    assert entry["source"] == "installed-distribution"
    assert entry["cadence"] == "at-most-once-per-utc-day"
    assert entry["running"] == "0.14.1"
    assert entry["installed"] == "0.14.2"
    assert entry["suggested_action"] == "read-runtime-update-skill-then-refresh-if-safe"
    assert "runtime-update-checks" in entry["skill"]
    assert "system(action='refresh')" in entry["detail"]


def test_local_refresh_mismatch_does_not_re_emit_same_utc_day(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1",
            installed_version="0.14.2",
            dev_reason=None,
        ),
    )
    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-06-30")

    kv.check(agent)
    assert len(_entries(tmp_path)) == 1

    # Agent dismisses the nudge (deletes nudge.json).
    from lingtai_kernel.nudge import remove

    remove(agent, "kernel_version")
    assert _entries(tmp_path) == []

    # Same UTC day, same mismatch still present -> must NOT re-emit.
    _reset_fast_gate(agent)
    kv.check(agent)
    assert _entries(tmp_path) == []


def test_local_refresh_mismatch_re_emits_next_utc_day(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1",
            installed_version="0.14.2",
            dev_reason=None,
        ),
    )

    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-06-30")
    kv.check(agent)
    assert len(_entries(tmp_path)) == 1

    from lingtai_kernel.nudge import remove

    remove(agent, "kernel_version")
    assert _entries(tmp_path) == []

    # Next UTC day, same mismatch still present -> re-emit once for that day.
    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-07-01")
    _reset_fast_gate(agent)
    kv.check(agent)
    assert len(_entries(tmp_path)) == 1
    assert _entries(tmp_path)[0]["source"] == "installed-distribution"

    # Still the same day after another dismissal -> no re-emit.
    remove(agent, "kernel_version")
    _reset_fast_gate(agent)
    kv.check(agent)
    assert _entries(tmp_path) == []


def test_local_refresh_match_clears_mismatch_tracking_and_stale_nudge(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-06-30")

    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1",
            installed_version="0.14.2",
            dev_reason=None,
        ),
    )
    kv.check(agent)
    assert len(_entries(tmp_path)) == 1

    # Versions now match (a refresh happened). This must clear the stale
    # nudge and the mismatch tracking so a future mismatch emits again.
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.2",
            installed_version="0.14.2",
            dev_reason=None,
        ),
    )
    monkeypatch.setattr(kv, "_fetch_latest_version", lambda: "0.14.2")
    _reset_fast_gate(agent)
    kv.check(agent)
    assert _entries(tmp_path) == []

    state = json.loads((tmp_path / ".notification" / ".nudge_state.json").read_text())
    assert "emitted_for_mismatch" not in state["kernel_version"]
    assert "mismatch_emitted_date" not in state["kernel_version"]

    # A fresh mismatch on the same day must emit again.
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.2",
            installed_version="0.14.3",
            dev_reason=None,
        ),
    )
    _reset_fast_gate(agent)
    kv.check(agent)
    assert len(_entries(tmp_path)) == 1
    assert _entries(tmp_path)[0]["installed"] == "0.14.3"


def test_remote_update_check_is_daily_and_persistent(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    calls = {"n": 0}
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1",
            installed_version="0.14.1",
            dev_reason=None,
        ),
    )
    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-06-24")

    def latest():
        calls["n"] += 1
        return "0.14.2"

    monkeypatch.setattr(kv, "_fetch_latest_version", latest)

    _drain_remote_fetch(agent)

    entries = _entries(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "pypi-json"
    assert entry["cadence"] == "at-most-once-per-utc-day"
    assert entry["latest"] == "0.14.2"
    assert entry["suggested_action"] == "read-runtime-update-skill-and-ask-human"
    assert "human" in entry["detail"].lower()
    assert calls["n"] == 1

    state = json.loads((tmp_path / ".notification" / ".nudge_state.json").read_text())
    assert state["kernel_version"]["last_remote_check_date"] == "2026-06-24"
    assert state["kernel_version"]["checked_installed_version"] == "0.14.1"
    assert state["kernel_version"]["latest_seen"] == "0.14.2"

    _reset_fast_gate(agent)
    monkeypatch.setattr(
        kv,
        "_fetch_latest_version",
        lambda: (_ for _ in ()).throw(AssertionError("daily throttle failed")),
    )
    kv.check(agent)
    assert calls["n"] == 1
    assert _entries(tmp_path)[0]["latest"] == "0.14.2"


def test_dev_or_editable_runtime_skips_and_clears_kernel_nudge(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    upsert(agent, "kernel_version", {"title": "old", "source": "pypi-json"})
    assert _entries(tmp_path)
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1.dev0",
            installed_version="0.14.1.dev0",
            dev_reason="editable-install",
        ),
    )
    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-06-24")
    monkeypatch.setattr(
        kv,
        "_fetch_latest_version",
        lambda: (_ for _ in ()).throw(AssertionError("dev mode should skip remote")),
    )

    kv.check(agent)

    assert "nudge" not in collect_notifications(tmp_path)
    state = json.loads((tmp_path / ".notification" / ".nudge_state.json").read_text())
    assert state["kernel_version"]["last_skip_date"] == "2026-06-24"
    assert state["kernel_version"]["skip_reason"] == "editable-install"


def test_current_remote_version_clears_existing_kernel_nudge(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    upsert(agent, "kernel_version", {"title": "old", "source": "pypi-json"})
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1",
            installed_version="0.14.1",
            dev_reason=None,
        ),
    )
    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-06-24")
    monkeypatch.setattr(kv, "_fetch_latest_version", lambda: "0.14.1")

    _drain_remote_fetch(agent)

    assert "nudge" not in collect_notifications(tmp_path)
    state = json.loads((tmp_path / ".notification" / ".nudge_state.json").read_text())
    assert state["kernel_version"]["latest_seen"] == "0.14.1"
    assert state["kernel_version"]["emitted_for_latest"] is None


def _remote_agent(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    monkeypatch.setattr(
        kv,
        "_runtime_info",
        lambda: kv._RuntimeInfo(
            running_version="0.14.1",
            installed_version="0.14.1",
            dev_reason=None,
        ),
    )
    monkeypatch.setattr(kv, "_today_utc", lambda: "2026-06-24")
    return agent


def test_check_does_not_block_on_slow_fetch(tmp_path, monkeypatch):
    # Regression for #730: the heartbeat thread is the sole writer of
    # .agent.heartbeat and is_alive treats a >2s-old tick as dead. A slow
    # PyPI probe on the heartbeat thread stalls the tick past that threshold.
    # check() must hand the network work to a worker thread and return at once.
    agent = _remote_agent(tmp_path, monkeypatch)

    release = threading.Event()

    def _slow_fetch():
        release.wait(5.0)
        return "0.14.2"

    monkeypatch.setattr(kv, "_fetch_latest_version", _slow_fetch)

    start = time.monotonic()
    kv.check(agent)
    elapsed = time.monotonic() - start

    try:
        assert elapsed < 0.5, f"check() blocked on the fetch for {elapsed:.2f}s"
        # No state or nudge emitted yet — the worker is still running.
        assert _entries(tmp_path) == []
        assert not (tmp_path / ".notification" / ".nudge_state.json").exists()
    finally:
        release.set()
        _await_fetch(agent)


def test_fetch_result_consumed_on_later_probe(tmp_path, monkeypatch):
    agent = _remote_agent(tmp_path, monkeypatch)
    monkeypatch.setattr(kv, "_fetch_latest_version", lambda: "0.14.2")

    # First due probe only spawns the worker; no nudge yet.
    kv.check(agent)
    assert _entries(tmp_path) == []
    _await_fetch(agent)

    # A later probe (past the fast gate) consumes the result and emits.
    _reset_fast_gate(agent)
    kv.check(agent)

    entries = _entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["source"] == "pypi-json"
    assert entries[0]["latest"] == "0.14.2"
    state = json.loads((tmp_path / ".notification" / ".nudge_state.json").read_text())
    assert state["kernel_version"]["latest_seen"] == "0.14.2"
    assert state["kernel_version"]["last_remote_check_date"] == "2026-06-24"
    assert kv._fetch_slot(agent) is None


def test_fetch_error_recorded_asynchronously(tmp_path, monkeypatch):
    agent = _remote_agent(tmp_path, monkeypatch)

    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(kv, "_fetch_latest_version", _boom)

    kv.check(agent)
    _await_fetch(agent)
    _reset_fast_gate(agent)
    kv.check(agent)

    state = json.loads((tmp_path / ".notification" / ".nudge_state.json").read_text())
    ks = state["kernel_version"]
    # The day is recorded so the failed probe is not retried in a tight loop.
    assert ks["last_remote_check_date"] == "2026-06-24"
    assert "network down" in ks["last_error"]
    assert any(e == "kernel_version_update_check_error" for e, _ in agent.logs)
    assert kv._fetch_slot(agent) is None


def test_single_flight(tmp_path, monkeypatch):
    agent = _remote_agent(tmp_path, monkeypatch)

    starts = {"n": 0}
    release = threading.Event()

    def _slow_fetch():
        starts["n"] += 1
        release.wait(5.0)
        return "0.14.2"

    monkeypatch.setattr(kv, "_fetch_latest_version", _slow_fetch)

    try:
        kv.check(agent)
        first_slot = kv._fetch_slot(agent)
        assert first_slot is not None

        # Repeated due probes while a fetch is in flight spawn no second worker.
        for _ in range(3):
            _reset_fast_gate(agent)
            kv.check(agent)
            assert kv._fetch_slot(agent) is first_slot
    finally:
        release.set()
        _await_fetch(agent)

    assert starts["n"] == 1


def test_fetch_deadline_abandons_stuck_worker(tmp_path, monkeypatch):
    agent = _remote_agent(tmp_path, monkeypatch)

    release = threading.Event()

    def _wedged_fetch():
        release.wait(30.0)  # never released within the test window
        return "0.14.2"

    monkeypatch.setattr(kv, "_fetch_latest_version", _wedged_fetch)

    now = [1_000_000.0]
    monkeypatch.setattr(kv.time, "time", lambda: now[0])

    kv.check(agent)
    slot = kv._fetch_slot(agent)
    assert slot is not None

    # Advance past the abandon deadline; the next probe reclaims the slot.
    now[0] += kv._FETCH_DEADLINE_SECONDS + 1.0
    _reset_fast_gate(agent)
    kv.check(agent)

    try:
        assert kv._fetch_slot(agent) is None
        state = json.loads(
            (tmp_path / ".notification" / ".nudge_state.json").read_text()
        )
        ks = state["kernel_version"]
        assert ks["last_remote_check_date"] == "2026-06-24"
        assert "timed out" in ks["last_error"]
    finally:
        release.set()
