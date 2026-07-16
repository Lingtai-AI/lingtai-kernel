import json

from tests._notification_store_helpers import notification_store_for, snapshot_notifications
from lingtai.kernel.nudge import upsert
from lingtai.kernel.nudge import kernel_version as kv


class _Agent:
    def __init__(self, workdir):
        self._working_dir = workdir
        self._notification_store = notification_store_for(workdir)
        self.logs = []

    def _log(self, event, **fields):
        self.logs.append((event, fields))


def _entries(workdir):
    return (
        snapshot_notifications(workdir)
        .get("nudge", {})
        .get("data", {})
        .get("nudges", [])
    )


def _reset_fast_gate(agent):
    agent._nudge_kernel_version_state["last_probe_ts"] = 0.0


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
    assert entry["skill"] == "https://lingtai.ai/skill.md"
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
    from lingtai.kernel.nudge import remove

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

    from lingtai.kernel.nudge import remove

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

    kv.check(agent)

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

    assert "nudge" not in snapshot_notifications(tmp_path)
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

    kv.check(agent)

    assert "nudge" not in snapshot_notifications(tmp_path)
    state = json.loads((tmp_path / ".notification" / ".nudge_state.json").read_text())
    assert state["kernel_version"]["latest_seen"] == "0.14.1"
    assert state["kernel_version"]["emitted_for_latest"] is None


def test_runtime_info_uses_loaded_wrapper_version(tmp_path, monkeypatch):
    """_runtime_info reads running_version from sys.modules['lingtai'] without importing it."""
    import sys
    import types
    from importlib import metadata

    fake_wrapper = types.ModuleType("lingtai")
    fake_wrapper.__version__ = "0.14.1"
    fake_wrapper.__file__ = str(tmp_path / "site-packages" / "lingtai" / "__init__.py")
    monkeypatch.setitem(sys.modules, "lingtai", fake_wrapper)

    class FakeDist:
        version = "0.14.2"

        def read_text(self, name):
            return None

        def locate_file(self, path):
            return tmp_path / "site-packages" / path

    monkeypatch.setattr(
        metadata, "distribution", lambda name: FakeDist() if name == "lingtai" else None
    )

    info = kv._runtime_info()
    assert info.running_version == "0.14.1"
    assert info.installed_version == "0.14.2"
    assert info.dev_reason is None


def test_runtime_info_wrapper_absent_falls_back_to_metadata(tmp_path, monkeypatch):
    """When the wrapper module is not loaded, running_version falls back to installed metadata."""
    import sys
    from importlib import metadata

    monkeypatch.delitem(sys.modules, "lingtai", raising=False)

    class FakeDist:
        version = "0.14.2"

        def read_text(self, name):
            return None

        def locate_file(self, path):
            return tmp_path / "site-packages" / path

    monkeypatch.setattr(
        metadata, "distribution", lambda name: FakeDist() if name == "lingtai" else None
    )

    info = kv._runtime_info()
    assert info.running_version == "0.14.2"
    assert info.installed_version == "0.14.2"


def test_runtime_info_detects_source_checkout_from_wrapper_file(tmp_path, monkeypatch):
    """Source-checkout detection uses the already-loaded wrapper's __file__."""
    import sys
    import types
    from importlib import metadata

    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("")
    wrapper_file = checkout / "src" / "lingtai" / "__init__.py"
    wrapper_file.parent.mkdir(parents=True)
    wrapper_file.write_text("")

    fake_wrapper = types.ModuleType("lingtai")
    fake_wrapper.__version__ = "0.14.1"
    fake_wrapper.__file__ = str(wrapper_file)
    monkeypatch.setitem(sys.modules, "lingtai", fake_wrapper)

    class FakeDist:
        version = "0.14.1"

        def read_text(self, name):
            return None

        def locate_file(self, path):
            return tmp_path / "site-packages" / path

    monkeypatch.setattr(
        metadata, "distribution", lambda name: FakeDist() if name == "lingtai" else None
    )

    info = kv._runtime_info()
    assert info.dev_reason == "source-checkout"


def test_runtime_info_wrapper_without_file_is_not_source_checkout(tmp_path, monkeypatch):
    """A loaded wrapper lacking __file__ must not be misclassified as source-checkout."""
    import sys
    import types
    from importlib import metadata

    fake_wrapper = types.ModuleType("lingtai")
    fake_wrapper.__version__ = "0.14.1"
    # No __file__ attribute.
    monkeypatch.setitem(sys.modules, "lingtai", fake_wrapper)

    class FakeDist:
        version = "0.14.1"

        def read_text(self, name):
            return None

        def locate_file(self, path):
            return tmp_path / "site-packages" / path

    monkeypatch.setattr(
        metadata, "distribution", lambda name: FakeDist() if name == "lingtai" else None
    )

    info = kv._runtime_info()
    assert info.dev_reason is None


def test_runtime_info_loaded_wrapper_triggers_local_refresh_nudge(tmp_path, monkeypatch):
    """A loaded wrapper at X plus installed metadata at Y emits the local refresh nudge."""
    import sys
    import types
    from importlib import metadata

    fake_wrapper = types.ModuleType("lingtai")
    fake_wrapper.__version__ = "0.14.1"
    fake_wrapper.__file__ = str(tmp_path / "site-packages" / "lingtai" / "__init__.py")
    monkeypatch.setitem(sys.modules, "lingtai", fake_wrapper)

    class FakeDist:
        version = "0.14.2"

        def read_text(self, name):
            return None

        def locate_file(self, path):
            return tmp_path / "site-packages" / path

    monkeypatch.setattr(
        metadata, "distribution", lambda name: FakeDist() if name == "lingtai" else None
    )
    monkeypatch.setattr(
        kv,
        "_fetch_latest_version",
        lambda: (_ for _ in ()).throw(AssertionError("remote should not be queried")),
    )

    agent = _Agent(tmp_path)
    kv.check(agent)

    entries = _entries(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kind"] == "kernel_version"
    assert entry["source"] == "installed-distribution"
    assert entry["running"] == "0.14.1"
    assert entry["installed"] == "0.14.2"
