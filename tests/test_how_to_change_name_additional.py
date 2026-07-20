"""Failure-order and validation coverage for the POSIX rename supervisor."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py"
spec = importlib.util.spec_from_file_location("lingtai_change_name_additional", SCRIPT)
change_name = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = change_name
assert spec.loader is not None
spec.loader.exec_module(change_name)


def _manifest(root: Path, *, address: str | None = None) -> None:
    (root / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "true-name", "address": address or root.name,
    }), encoding="utf-8")


def _valid_tree(tmp_path: Path) -> tuple[Path, change_name.Preflight]:
    old = tmp_path / "old"
    old.mkdir()
    (old / "init.json").write_text("{}", encoding="utf-8")
    _manifest(old)
    (old / ".agent.heartbeat").write_text(str(time.time()), encoding="utf-8")
    pf = change_name.Preflight(
        old, tmp_path / "new", change_name.Identity("id", "true-name", "old"), "{}",
        change_name.RuntimeChoice(Path(sys.executable), Path(sys.executable), "test"), 42,
    )
    return old, pf


def test_preflight_rejects_path_destination_heartbeat_process_lease_and_runtime(tmp_path: Path, monkeypatch):
    old, _ = _valid_tree(tmp_path)
    with pytest.raises(change_name.ChangeNameError, match="absolute"):
        change_name.preflight(str(old.relative_to(tmp_path)), "new")
    with pytest.raises(change_name.ChangeNameError, match="unchanged"):
        change_name.preflight(old, "old")
    (tmp_path / "new").mkdir()
    with pytest.raises(change_name.ChangeNameError, match="destination"):
        change_name.preflight(old, "new")
    (tmp_path / "new").rmdir()

    monkeypatch.setattr(change_name, "heartbeat_fresh", lambda root: False)
    with pytest.raises(change_name.ChangeNameError, match="heartbeat"):
        change_name.preflight(old, "new")
    monkeypatch.setattr(change_name, "heartbeat_fresh", lambda root: True)
    monkeypatch.setattr(change_name, "exact_processes", lambda root: [])
    with pytest.raises(change_name.ChangeNameError, match="exact agent process"):
        change_name.preflight(old, "new")
    monkeypatch.setattr(change_name, "exact_processes", lambda root: [(42, "run")])
    monkeypatch.setattr(change_name, "_lock_is_held", lambda root: False)
    with pytest.raises(change_name.ChangeNameError, match=".agent.lock"):
        change_name.preflight(old, "new")
    monkeypatch.setattr(change_name, "_lock_is_held", lambda root: True)
    monkeypatch.setattr(change_name, "select_runtime", lambda data, root: (_ for _ in ()).throw(
        change_name.ChangeNameError("no runtime")))
    with pytest.raises(change_name.ChangeNameError, match="no runtime"):
        change_name.preflight(old, "new")


def test_preflight_rejects_symlink_old_and_malformed_init(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(change_name.ChangeNameError, match="non-symlink"):
        change_name.preflight(link, "new")

    old = tmp_path / "old"
    old.mkdir()
    _manifest(old)
    (old / "init.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(change_name.ChangeNameError, match="valid init"):
        change_name.preflight(old, "new")


def test_preflight_rejects_dotdot_and_symlinked_parent_spellings(tmp_path: Path):
    old = tmp_path / "old"
    old.mkdir()
    middle = tmp_path / "middle"
    middle.mkdir()
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(tmp_path, target_is_directory=True)

    for noncanonical in (middle / ".." / "old", parent_link / "old"):
        assert noncanonical.is_dir()
        with pytest.raises(change_name.ChangeNameError, match="canonical path"):
            change_name.preflight(noncanonical, "new")


def test_supervisor_event_order_touches_marker_only_after_preflight(tmp_path: Path, monkeypatch):
    old, pf = _valid_tree(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(change_name, "preflight", lambda *args: events.append("preflight") or pf)

    def wait(root, pid, timeout):
        events.append("wait")
        assert (root / ".suspend").exists()
        return object()

    def launch(pf_arg, lease, timeout, **kwargs):
        events.append("launch")
        assert (pf_arg.old / ".suspend").exists()
        assert callable(kwargs["on_rename"])
        return 99, pf_arg.new / "logs/relaunch.log"

    monkeypatch.setattr(change_name, "_wait_shutdown", wait)
    monkeypatch.setattr(change_name, "_launch_and_verify", launch)
    original_touch = Path.touch

    def touch(path, *args, **kwargs):
        if path == old / ".suspend":
            events.append("touch")
        return original_touch(path, *args, **kwargs)

    monkeypatch.setattr(Path, "touch", touch)
    monkeypatch.setattr(change_name, "write_receipt", lambda *args, **kwargs: events.append("receipt") or old / "logs/name-change.json")
    (old / "logs").mkdir()
    assert change_name.supervise(old, "new", timeout=1) == 0
    assert events == ["preflight", "touch", "wait", "launch", "receipt"]


def test_post_rename_failure_retains_new_directory_without_rollback(tmp_path: Path, monkeypatch):
    old, pf = _valid_tree(tmp_path)
    lease = object()
    monkeypatch.setattr(change_name, "_atomic_write", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(change_name.ChangeNameError, match="post-rename"):
        change_name._launch_and_verify(pf, lease, timeout=1)
    assert not old.exists()
    assert pf.new.is_dir()


def test_atomic_rename_does_not_replace_racing_empty_destination(tmp_path: Path):
    old, pf = _valid_tree(tmp_path)
    pf.new.mkdir()

    class Lease:
        closed = False

        def close(self):
            self.closed = True

    lease = Lease()
    with pytest.raises(change_name.ChangeNameError, match="no-replace rename failed"):
        change_name._launch_and_verify(pf, lease, timeout=1)
    assert lease.closed
    assert old.is_dir()
    assert pf.new.is_dir() and list(pf.new.iterdir()) == []


def test_retry_after_completed_rename_does_not_recreate_missing_old(tmp_path: Path):
    old = tmp_path / "old"
    assert change_name.supervise(old, "new", timeout=0.01) == 1
    assert not old.exists()
    assert not (tmp_path / "new").exists()


def test_invalid_old_with_existing_destination_does_not_touch_destination_receipt(tmp_path: Path):
    old = tmp_path / "missing-old"
    new = tmp_path / "new"
    (new / "logs").mkdir(parents=True)
    receipt = new / "logs" / change_name.RECEIPT_NAME
    receipt.write_text("pre-existing destination receipt\n", encoding="utf-8")

    assert change_name.supervise(old, "new", timeout=0.01) == 1
    assert receipt.read_text(encoding="utf-8") == "pre-existing destination receipt\n"


@pytest.mark.parametrize("failure", [
    change_name.subprocess.CalledProcessError(1, ["ps"]),
    change_name.subprocess.TimeoutExpired(["ps"], 2),
    OSError("ps unavailable"),
])
def test_process_scan_failures_are_not_reported_as_empty(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(change_name.subprocess, "run", fail)
    with pytest.raises(change_name.ChangeNameError, match="process scan failed"):
        change_name._processes()


def test_process_scan_failure_after_shutdown_observations_does_not_rename(tmp_path: Path, monkeypatch):
    old, pf = _valid_tree(tmp_path)
    (old / ".agent.lock").touch()
    (old / "logs").mkdir()
    monkeypatch.setattr(change_name, "preflight", lambda *args: pf)
    withdrawn: list[str] = []

    def withdrawn_heartbeat(root):
        withdrawn.append("heartbeat")
        return False

    def failed_scan(root):
        # Model the lifecycle's already-withdrawn heartbeat and released lease
        # before ps becomes unavailable.  The supervisor must still fail closed.
        assert not change_name.heartbeat_fresh(root)
        lease = change_name._acquire_lock(root)
        lease.close()
        withdrawn.append("lease")
        raise change_name.ChangeNameError("ps unavailable", phase="process-scan")

    monkeypatch.setattr(change_name, "heartbeat_fresh", withdrawn_heartbeat)
    monkeypatch.setattr(change_name, "exact_processes", failed_scan)

    assert change_name.supervise(old, "new", timeout=1) == 1
    assert withdrawn == ["heartbeat", "lease"]
    assert old.is_dir() and not (tmp_path / "new").exists()
    receipt = json.loads((old / "logs" / change_name.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert receipt["phase"] == "shutdown"
    assert "process scan failed" in receipt["error"]


def test_wait_shutdown_requires_exact_process_absent_stale_heartbeat_and_lease(tmp_path: Path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    (old / ".agent.lock").touch()
    process_states = iter([[(10, "python -m lingtai run " + str(old))], []])
    monkeypatch.setattr(change_name, "exact_processes", lambda root: next(process_states))
    class Lease:
        def close(self):
            pass
    # Keep heartbeat and lease observations synchronized with process states.
    heartbeat_states = iter([True, False])
    lease_states = iter([None, Lease()])
    monkeypatch.setattr(change_name, "heartbeat_fresh", lambda root: next(heartbeat_states))
    monkeypatch.setattr(change_name, "_acquire_lock", lambda root: next(lease_states))
    lease = change_name._wait_shutdown(old, 10, timeout=1, poll=0)
    assert lease is not None
