from __future__ import annotations

import builtins
import errno
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel.daemon_supervisor.manifest import build_manifest, write_manifest
from lingtai.tools.daemon.return_observer_helper import main as helper_main
from lingtai.tools.daemon.return_observer_helper import observe as helper_observe
from lingtai.tools.daemon.return_observer_hook import (
    write_dispatch_intent_receipt,
    write_dispatch_result_receipt,
)
from lingtai.tools.daemon.run_dir import DaemonRunDir
from lingtai.tools.daemon.supervisor_runtime import (
    _publish_terminal_notification_if_needed,
    _return_observation_if_available,
)


def _make_run_dir(tmp_path: Path, *, run_id: str = "em-test") -> DaemonRunDir:
    parent = tmp_path / "agent"
    parent.mkdir(parents=True, exist_ok=True)
    return DaemonRunDir(
        parent_working_dir=parent,
        handle=run_id,
        run_id=run_id,
        task="observe me",
        tools=[],
        model="fake-model",
        max_turns=5,
        timeout_s=30,
        parent_addr=parent.name,
        parent_pid=os.getpid(),
        system_prompt="You are a test daemon.",
        call_parameters={"task": "observe me", "tools": []},
    )


def _manifest(run_dir: DaemonRunDir, *, enabled: bool) -> dict:
    manifest = build_manifest(
        run_id=run_dir.run_id,
        backend="lingtai",
        parent_working_dir=str(run_dir.path.parent.parent),
        run_dir=str(run_dir.path),
        task="observe me",
        tools=[],
        max_turns=5,
        timeout_s=30,
        group_id=None,
        llm={"provider": "fake", "model": "fake", "api_key": None, "base_url": None},
        return_observer_enabled=enabled,
    )
    write_manifest(run_dir.path, manifest)
    return manifest


def _matching_events(run_dir: DaemonRunDir) -> list[dict]:
    store = PosixNotificationStoreAdapter(run_dir.path.parent.parent)
    snap = store.snapshot(lambda ch: ch == "system")
    events = snap.get("system", {}).get("data", {}).get("events", [])
    return [ev for ev in events if ev.get("ref_id") == run_dir.handle]


def test_disabled_observer_does_not_import_spawn_or_write_sidecar(tmp_path, monkeypatch):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=False)
    run_dir.mark_done("baseline result")

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "lingtai.tools.daemon.return_observer_hook":
            raise AssertionError("observer hook imported while disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    _publish_terminal_notification_if_needed(run_dir, manifest)

    assert not (run_dir.path / ".supervisor" / "return-observation").exists()
    events = _matching_events(run_dir)
    assert len(events) == 1
    assert "return_observation" not in events[0]
    assert run_dir.result_path.read_text(encoding="utf-8") == "baseline result"


def test_enabled_observer_adds_safe_notification_block_and_sidecar(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("observed result")

    _publish_terminal_notification_if_needed(run_dir, manifest)

    events = _matching_events(run_dir)
    assert len(events) == 1
    block = events[0]["return_observation"]
    assert block == {
        "schema_version": "lingtai.return-observation-notice.v0",
        "state": "available",
        "generation": "g0000",
        "receipt_digest": block["receipt_digest"],
        "authority": "advisory_only",
        "raw_result_unchanged": True,
    }
    assert block["receipt_digest"].startswith("sha256:")
    side_dir = run_dir.path / ".supervisor" / "return-observation"
    portable = json.loads((side_dir / "g0000.portable.json").read_text(encoding="utf-8"))
    assert portable["notification_publication_state"] == "not_yet_attempted"
    assert portable["authority"] == "advisory_only"
    assert str(run_dir.path) not in json.dumps(portable)


def test_helper_binds_parent_dispatch_receipts_when_present(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    intent_digest = write_dispatch_intent_receipt(run_dir, manifest)
    result_digest = write_dispatch_result_receipt(
        run_dir,
        {"status": "dispatched", "count": 1, "ids": [run_dir.run_id], "group_id": None},
    )
    run_dir.mark_done("observed result")

    observed = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    assert observed["state"] == "available"
    portable = json.loads(
        (run_dir.path / ".supervisor" / "return-observation" / "g0000.portable.json").read_text(encoding="utf-8")
    )
    assert portable["dispatch_intent_ref"]["status"] == "observed"
    assert portable["dispatch_intent_ref"]["sha256"] == intent_digest
    assert portable["dispatch_result_ref"]["status"] == "observed"
    assert portable["dispatch_result_ref"]["sha256"] == result_digest


@pytest.mark.parametrize(
    "fault",
    [
        "import_error",
        "unexpected_exception",
        "keyboard_interrupt",
        "unavailable",
        "bad_schema",
    ],
)
def test_observer_failures_leave_terminal_notification_exactly_once(tmp_path, monkeypatch, fault):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw survives")

    if fault == "import_error":
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "lingtai.tools.daemon.return_observer_hook":
                raise ImportError("boom")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guarded_import)
    else:
        import lingtai.tools.daemon.return_observer_hook as hook

        if fault == "unexpected_exception":
            monkeypatch.setattr(hook, "observe_return_bounded", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        elif fault == "keyboard_interrupt":
            monkeypatch.setattr(hook, "observe_return_bounded", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        elif fault == "unavailable":
            monkeypatch.setattr(hook, "observe_return_bounded", lambda *a, **k: None)
        else:
            monkeypatch.setattr(hook, "observe_return_bounded", lambda *a, **k: {"state": "available", "receipt_digest": "not-a-digest"})

    _publish_terminal_notification_if_needed(run_dir, manifest)

    events = _matching_events(run_dir)
    assert len(events) == 1
    assert "return_observation" not in events[0]
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw survives"
    assert DaemonRunDir.read_state_from_disk(run_dir.path)["state"] == "done"


def test_helper_rejects_symlink_escape_without_notification_exception(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw survives")
    (run_dir.path / "result.txt").unlink()
    (run_dir.path / "result.txt").symlink_to(tmp_path / "outside.txt")

    rc = helper_main([
        "--run-dir",
        str(run_dir.path),
        "--manifest-path",
        str(run_dir.path / "supervisor_manifest.json"),
        "--generation",
        "g0000",
        "--terminal-state",
        "done",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unavailable"
    assert payload["reason_code"] in {"unsafe_sidecar", "not_regular_file", "path_escape"}


def test_helper_reuses_identical_generation_and_refuses_conflict(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("first")
    argv = [
        "--run-dir",
        str(run_dir.path),
        "--manifest-path",
        str(run_dir.path / "supervisor_manifest.json"),
        "--generation",
        "g0000",
        "--terminal-state",
        "done",
    ]

    assert helper_main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["state"] == "available"
    assert helper_main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert second == first

    portable = run_dir.path / ".supervisor" / "return-observation" / "g0000.portable.json"
    portable.write_text("{}", encoding="utf-8")
    assert helper_main(argv) == 0
    third = json.loads(capsys.readouterr().out)
    assert third == {"reason_code": "receipt_conflict", "state": "unavailable"}


@pytest.mark.parametrize("component", [".supervisor", "return-observation"])
def test_helper_rejects_sidecar_directory_symlinks(tmp_path, capsys, component):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == ".supervisor":
        (run_dir.path / ".supervisor").symlink_to(outside, target_is_directory=True)
    else:
        (run_dir.path / ".supervisor").mkdir()
        (run_dir.path / ".supervisor" / "return-observation").symlink_to(outside, target_is_directory=True)

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"reason_code": "unsafe_sidecar", "state": "unavailable"}
    assert not any(outside.iterdir())


def test_helper_rejects_final_file_symlink(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    side = run_dir.path / ".supervisor" / "return-observation"
    side.mkdir(parents=True)
    outside = tmp_path / "outside-host.json"
    outside.write_text("{}", encoding="utf-8")
    (side / "g0000.host.json").symlink_to(outside)

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"reason_code": "unsafe_sidecar", "state": "unavailable"}
    assert outside.read_text(encoding="utf-8") == "{}"


def test_same_snapshot_receipt_is_deterministic_across_time_change(tmp_path, monkeypatch):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("stable")
    manifest_path = run_dir.path / "supervisor_manifest.json"

    monkeypatch.setattr("lingtai.tools.daemon.return_observer_helper.time", None, raising=False)
    first = helper_observe(run_dir.path, manifest_path, "g0000", "done")
    second = helper_observe(run_dir.path, manifest_path, "g0000", "done")

    assert second == first


@pytest.mark.parametrize("fail_name", ["g0000.host.json", "g0000.portable.json", "g0000.status.json"])
def test_status_last_partial_write_recovers_matching_files(tmp_path, monkeypatch, fail_name):
    import lingtai.tools.daemon.return_observer_helper as helper

    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("recover")
    real_atomic = helper._atomic_json_at
    seen = {"failed": False}

    def flaky(dir_fd, name, payload):
        if name == fail_name and not seen["failed"]:
            seen["failed"] = True
            raise OSError("injected write failure")
        return real_atomic(dir_fd, name, payload)

    monkeypatch.setattr(helper, "_atomic_json_at", flaky)
    with pytest.raises(OSError):
        helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")

    monkeypatch.setattr(helper, "_atomic_json_at", real_atomic)
    recovered = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    assert recovered["state"] == "available"


@pytest.mark.parametrize("generation", ["", "../x", "g1/evil", "g1-XYZ", "g01-1234567890abcdef"])
def test_helper_rejects_unsafe_generation_names(tmp_path, capsys, generation):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", generation,
        "--terminal-state", "done",
    ])

    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "invalid_generation",
        "state": "unavailable",
    }


def test_helper_rejects_terminal_state_mismatch(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "failed",
    ])

    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "terminal_state_mismatch",
        "state": "unavailable",
    }


def test_followup_generation_uses_authoritative_state(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("initial")
    helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    generation = "g1-1234567890abcdef"
    run_dir.record_followup(generation, status="done", output="follow")
    state = DaemonRunDir.read_state_from_disk(run_dir.path)

    block = _return_observation_if_available(
        run_dir, manifest, status="follow-up done", state=state,
    )

    assert block is not None
    assert block["generation"] == generation


@pytest.mark.parametrize(
    "block",
    [
        {"schema_version": "lingtai.return-observation-notice.v0", "state": "available", "generation": "g0000", "receipt_digest": "sha256:abc", "authority": "advisory_only", "raw_result_unchanged": True},
        {"schema_version": "lingtai.return-observation-notice.v0", "state": "available", "generation": "../x", "receipt_digest": "sha256:" + "a" * 64, "authority": "advisory_only", "raw_result_unchanged": True},
        {"schema_version": "lingtai.return-observation-notice.v0", "state": "available", "generation": "g0000", "receipt_digest": "sha256:" + "a" * 64, "authority": "advisory_only", "raw_result_unchanged": False},
    ],
)
def test_supervisor_rejects_malformed_observation_blocks(tmp_path, monkeypatch, block):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")

    import lingtai.tools.daemon.return_observer_hook as hook

    monkeypatch.setattr(hook, "observe_return_bounded", lambda *a, **k: block)
    assert _return_observation_if_available(
        run_dir, manifest, status="done", state=DaemonRunDir.read_state_from_disk(run_dir.path),
    ) is None


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "timeout"])
def test_observer_preserves_non_done_terminal_states(tmp_path, terminal):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    if terminal == "failed":
        run_dir.mark_failed(RuntimeError("boom"))
    elif terminal == "cancelled":
        run_dir.mark_cancelled()
    else:
        run_dir.mark_timeout()

    _publish_terminal_notification_if_needed(run_dir, manifest)

    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    assert state["state"] == terminal
    events = _matching_events(run_dir)
    assert len(events) == 1
    assert events[0]["return_observation"]["generation"] == "g0000"


def test_missing_finish_physical_result_is_not_upgraded(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.result_path.write_text("physical failure evidence", encoding="utf-8")
    run_dir.mark_failed(RuntimeError("missing finish"))

    result = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "failed")
    portable = json.loads(
        (run_dir.path / ".supervisor" / "return-observation" / "g0000.portable.json").read_text(encoding="utf-8")
    )

    assert result["state"] == "available"
    assert portable["terminal_state"] == "failed"
    assert DaemonRunDir.read_state_from_disk(run_dir.path)["state"] == "failed"


def test_artifact_manifest_missing_and_truncated_are_explicit(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    run_dir.manifest_path.unlink()
    first = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    assert first["state"] == "available"
    portable = json.loads(
        (run_dir.path / ".supervisor" / "return-observation" / "g0000.portable.json").read_text(encoding="utf-8")
    )
    assert portable["declared_artifacts"]["status"] == "missing_or_unavailable"

    run_dir2 = _make_run_dir(tmp_path, run_id="em-test-truncated")
    _manifest(run_dir2, enabled=True)
    run_dir2.mark_done("raw")
    run_dir2.manifest_path.write_text(
        json.dumps({"artifacts": [], "truncated": True}),
        encoding="utf-8",
    )
    helper_main([
        "--run-dir", str(run_dir2.path),
        "--manifest-path", str(run_dir2.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])
    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "artifact_manifest_truncated",
        "state": "unavailable",
    }


@pytest.mark.parametrize(
    "fault",
    ["spawn_failure", "nonzero_exit", "timeout", "invalid_json", "oversized_stdout"],
)
def test_hook_subprocess_faults_fail_open(tmp_path, monkeypatch, fault):
    import lingtai.tools.daemon.return_observer_hook as hook

    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    state = DaemonRunDir.read_state_from_disk(run_dir.path)

    if fault == "spawn_failure":
        monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("spawn failed")))
    elif fault == "timeout":
        monkeypatch.setattr(
            hook.subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("helper", 1.5)),
        )
    elif fault == "nonzero_exit":
        monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 2, "{}", ""))
    elif fault == "invalid_json":
        monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "{", ""))
    else:
        monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "x" * 9000, ""))

    assert hook.observe_return_bounded(run_dir, manifest, status="done", state=state) is None
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw"
    assert DaemonRunDir.read_state_from_disk(run_dir.path)["state"] == "done"


@pytest.mark.parametrize(
    "reason",
    ["permission_denied", "enospc", "atomic_replace_failure", "mutation_detected", "file_count_cap", "total_byte_cap"],
)
def test_helper_injected_unavailable_reasons_do_not_mutate_raw_result(tmp_path, monkeypatch, reason):
    import lingtai.tools.daemon.return_observer_helper as helper

    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    if reason in {"permission_denied", "enospc", "atomic_replace_failure"}:
        monkeypatch.setattr(helper, "_atomic_json_at", lambda *a, **k: (_ for _ in ()).throw(helper.Unavailable(reason)))
    elif reason == "mutation_detected":
        monkeypatch.setattr(helper, "_hash_file", lambda *a, **k: (_ for _ in ()).throw(helper.Unavailable(reason)))
    elif reason == "file_count_cap":
        monkeypatch.setattr(helper, "MAX_FILES", 0)
    else:
        monkeypatch.setattr(helper, "MAX_TOTAL_BYTES", 1)

    with pytest.raises(helper.Unavailable) as exc:
        helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")

    assert exc.value.reason_code == reason
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw"
    assert DaemonRunDir.read_state_from_disk(run_dir.path)["state"] == "done"


def test_stale_tmp_file_does_not_block_observation(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    side = run_dir.path / ".supervisor" / "return-observation"
    side.mkdir(parents=True)
    (side / ".tmp-g0000.host.json-stale").write_text("stale", encoding="utf-8")

    result = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")

    assert result["state"] == "available"
    assert (side / ".tmp-g0000.host.json-stale").read_text(encoding="utf-8") == "stale"


def test_hook_keyboard_interrupt_and_generation_mismatch_fail_open(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_hook as hook

    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    state = DaemonRunDir.read_state_from_disk(run_dir.path)

    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert hook.observe_return_bounded(run_dir, manifest, status="done", state=state) is None

    generation = "g1-1234567890abcdef"
    state["followup_generation"] = generation
    state["followup_status"] = "done"
    stdout = json.dumps({
        "state": "available",
        "generation": "g0000",
        "receipt_digest": "sha256:" + "a" * 64,
    })
    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout, ""))
    assert hook.observe_return_bounded(run_dir, manifest, status="follow-up done", state=state) is None


def test_dispatch_writers_fail_open_on_baseexception(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_hook as hook

    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    monkeypatch.setattr(hook, "_write_sidecar_json", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert write_dispatch_intent_receipt(run_dir, manifest) is None
    assert write_dispatch_result_receipt(run_dir, {"status": "dispatched", "count": 1, "ids": [run_dir.run_id]}) is None


def test_existing_real_sidecar_dirs_are_chmodded_and_receipts_are_0600(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    side = run_dir.path / ".supervisor" / "return-observation"
    side.mkdir(parents=True)
    (run_dir.path / ".supervisor").chmod(0o777)
    side.chmod(0o777)

    helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")

    assert ((run_dir.path / ".supervisor").stat().st_mode & 0o777) == 0o700
    assert (side.stat().st_mode & 0o777) == 0o700
    for name in ("g0000.host.json", "g0000.portable.json", "g0000.status.json"):
        assert ((side / name).stat().st_mode & 0o777) == 0o600


def test_declared_artifact_bytes_are_hashed_and_same_generation_changes_conflict(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    declared = run_dir.path / "declared.bin"
    declared.write_bytes(b"AAAA")
    run_dir.mark_done("raw")
    run_dir.manifest_path.write_text(
        json.dumps({"artifacts": [{"path": "declared.bin", "size": 4, "role": "declared"}], "truncated": False}),
        encoding="utf-8",
    )

    first = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    assert first["state"] == "available"
    portable = json.loads(
        (run_dir.path / ".supervisor" / "return-observation" / "g0000.portable.json").read_text(encoding="utf-8")
    )
    declared_row = next(row for row in portable["observed_files"] if row["relative_ref"] == "declared.bin")
    assert declared_row["sha256"] == "sha256:" + hashlib.sha256(b"AAAA").hexdigest()

    declared.write_bytes(b"BBBB")
    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])
    assert json.loads(capsys.readouterr().out) == {"reason_code": "receipt_conflict", "state": "unavailable"}


@pytest.mark.parametrize(
    ("artifact_path", "reason"),
    [
        ("missing.bin", "artifact_missing"),
        ("../escape.bin", "path_escape"),
    ],
)
def test_bad_declared_artifacts_fail_certificate_only(tmp_path, capsys, artifact_path, reason):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    run_dir.manifest_path.write_text(
        json.dumps({"artifacts": [{"path": artifact_path, "size": 4}], "truncated": False}),
        encoding="utf-8",
    )

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unavailable"
    assert payload["reason_code"] == reason
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw"


def test_symlinked_declared_artifact_fails_certificate_only(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"AAAA")
    (run_dir.path / "declared.bin").symlink_to(outside)
    run_dir.mark_done("raw")
    run_dir.manifest_path.write_text(
        json.dumps({"artifacts": [{"path": "declared.bin", "size": 4}], "truncated": False}),
        encoding="utf-8",
    )

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    assert json.loads(capsys.readouterr().out) == {"reason_code": "path_escape", "state": "unavailable"}
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw"


def test_followup_supersedes_initial_generation_and_requires_predecessor(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("initial")
    initial = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    generation = "g1-1234567890abcdef"
    run_dir.record_followup(generation, status="done", output="follow")
    follow = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", generation, "follow-up done")
    assert follow["state"] == "available"
    portable = json.loads(
        (run_dir.path / ".supervisor" / "return-observation" / f"{generation}.portable.json").read_text(encoding="utf-8")
    )
    assert portable["supersedes"]["generation"] == "g0000"
    assert portable["supersedes"]["portable_digest"] == initial["receipt_digest"]

    run_dir2 = _make_run_dir(tmp_path, run_id="em-follow-missing")
    _manifest(run_dir2, enabled=True)
    run_dir2.mark_done("initial")
    run_dir2.record_followup(generation, status="done", output="follow")
    helper_main([
        "--run-dir", str(run_dir2.path),
        "--manifest-path", str(run_dir2.path / "supervisor_manifest.json"),
        "--generation", generation,
        "--terminal-state", "follow-up done",
    ])
    assert json.loads(capsys.readouterr().out) == {"reason_code": "missing_predecessor", "state": "unavailable"}


def test_atomic_json_loops_over_short_writes(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_helper as helper

    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    real_write = helper.os.write

    def short_write(fd, data):
        return real_write(fd, data[: max(1, len(data) // 3)])

    monkeypatch.setattr(helper.os, "write", short_write)
    result = helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    assert result["state"] == "available"
    json.loads((run_dir.path / ".supervisor" / "return-observation" / "g0000.portable.json").read_text(encoding="utf-8"))


def test_atomic_json_zero_write_fails_certificate(tmp_path, monkeypatch, capsys):
    import lingtai.tools.daemon.return_observer_helper as helper

    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    monkeypatch.setattr(helper.os, "write", lambda *a, **k: 0)

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    assert json.loads(capsys.readouterr().out) == {"reason_code": "short_write", "state": "unavailable"}


@pytest.mark.parametrize("oversized_bytes", [530_042, 892_071])
def test_oversized_declared_artifact_is_omitted_but_notice_remains_exactly_once(
    tmp_path, oversized_bytes
):
    """Regression for the two real failed transcript sizes.

    A declared artifact beyond the read cap is explicitly not content-observed;
    it must not disable the bounded generation or the additive terminal notice.
    """
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    private_marker = b"PRIVATE-OVERSIZED-CONTENT-MUST-NOT-BE-READ"
    too_large = run_dir.path / "too-large.bin"
    too_large.write_bytes(private_marker + b"x" * (oversized_bytes - len(private_marker)))
    run_dir.mark_done("raw survives")
    run_dir.manifest_path.write_text(
        json.dumps({"artifacts": [{"path": "too-large.bin", "size": oversized_bytes}], "truncated": False}),
        encoding="utf-8",
    )

    _publish_terminal_notification_if_needed(run_dir, manifest)
    _publish_terminal_notification_if_needed(run_dir, manifest)

    events = _matching_events(run_dir)
    assert len(events) == 1
    assert events[0]["return_observation"]["generation"] == "g0000"
    side = run_dir.path / ".supervisor" / "return-observation"
    assert sorted(path.name for path in side.glob("g0000.*.json")) == [
        "g0000.host.json",
        "g0000.portable.json",
        "g0000.status.json",
    ]
    portable = json.loads((side / "g0000.portable.json").read_text(encoding="utf-8"))
    assert {
        "path": "too-large.bin",
        "field": "content_observation",
        "declared": "present",
        "observed": "omitted",
        "reason_code": "per_file_cap",
        "size_bytes": oversized_bytes,
        "cap_bytes": 256 * 1024,
    } in portable["declared_artifacts"]["differences"]
    assert "too-large.bin" not in {row["relative_ref"] for row in portable["observed_files"]}
    rendered_receipts = b"".join(path.read_bytes() for path in side.glob("g0000.*.json"))
    assert private_marker not in rendered_receipts
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw survives"
    assert DaemonRunDir.read_state_from_disk(run_dir.path)["state"] == "done"


def test_oversized_declared_artifact_preserves_manifest_size_difference(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    too_large = run_dir.path / "too-large.bin"
    too_large.write_bytes(b"x" * 530_042)
    run_dir.mark_done("raw")
    run_dir.manifest_path.write_text(
        json.dumps({"artifacts": [{"path": "too-large.bin", "size": 1}], "truncated": False}),
        encoding="utf-8",
    )

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    assert json.loads(capsys.readouterr().out)["state"] == "available"
    portable = json.loads(
        (run_dir.path / ".supervisor" / "return-observation" / "g0000.portable.json").read_text(encoding="utf-8")
    )
    differences = portable["declared_artifacts"]["differences"]
    assert {
        "path": "too-large.bin",
        "field": "size",
        "declared": 1,
        "observed": 530_042,
    } in differences
    assert any(
        row.get("path") == "too-large.bin"
        and row.get("field") == "content_observation"
        and row.get("reason_code") == "per_file_cap"
        and row.get("size_bytes") == 530_042
        for row in differences
    )


def test_declared_artifact_total_budget_omits_content_without_disabling_generation(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    private_marker = b"PRIVATE-TOTAL-BUDGET-CONTENT-MUST-NOT-BE-READ"
    declared = []
    for index in range(5):
        path = run_dir.path / f"budget-{index}.bin"
        path.write_bytes(private_marker + b"x" * (250_000 - len(private_marker)))
        declared.append({"path": path.name, "size": path.stat().st_size})
    run_dir.mark_done("raw total survives")
    run_dir.manifest_path.write_text(
        json.dumps({"artifacts": declared, "truncated": False}),
        encoding="utf-8",
    )

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    assert json.loads(capsys.readouterr().out)["state"] == "available"
    side = run_dir.path / ".supervisor" / "return-observation"
    portable = json.loads((side / "g0000.portable.json").read_text(encoding="utf-8"))
    omissions = [
        row for row in portable["declared_artifacts"]["differences"]
        if row.get("field") == "content_observation" and row.get("reason_code") == "total_byte_cap"
    ]
    assert omissions
    observed = {row["relative_ref"] for row in portable["observed_files"]}
    assert all(row["path"] not in observed for row in omissions)
    assert all(row["size_bytes"] == 250_000 for row in omissions)
    rendered_receipts = b"".join(path.read_bytes() for path in side.glob("g0000.*.json"))
    assert private_marker not in rendered_receipts
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw total survives"


def test_concurrent_bounded_invocations_share_one_oversized_generation(tmp_path):
    import lingtai.tools.daemon.return_observer_hook as hook

    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    too_large = run_dir.path / "too-large.bin"
    too_large.write_bytes(b"x" * 530_042)
    run_dir.mark_done("raw concurrent")
    run_dir.manifest_path.write_text(
        json.dumps({"artifacts": [{"path": "too-large.bin", "size": too_large.stat().st_size}], "truncated": False}),
        encoding="utf-8",
    )
    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    barrier = threading.Barrier(3)
    blocks: list[dict | None] = []
    errors: list[BaseException] = []

    def invoke():
        try:
            barrier.wait(timeout=5)
            blocks.append(hook.observe_return_bounded(run_dir, manifest, status="done", state=state))
        except BaseException as exc:  # test thread must report, not disappear
            errors.append(exc)

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(blocks) == 2
    assert all(isinstance(block, dict) for block in blocks)
    assert blocks[0] == blocks[1]
    assert blocks[0]["generation"] == "g0000"
    side = run_dir.path / ".supervisor" / "return-observation"
    assert sorted(path.name for path in side.glob("g0000.*.json")) == [
        "g0000.host.json",
        "g0000.portable.json",
        "g0000.status.json",
    ]
    assert run_dir.result_path.read_text(encoding="utf-8") == "raw concurrent"


def test_dispatch_intent_manifest_mismatch_is_explicit_unavailable(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    write_dispatch_intent_receipt(run_dir, manifest)
    mutated = dict(manifest)
    mutated["task"] = "changed by parent after intent"
    (run_dir.path / "supervisor_manifest.json").write_text(json.dumps(mutated), encoding="utf-8")
    run_dir.mark_done("raw")

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unavailable"
    assert payload["reason_code"] == "dispatch_binding_mismatch"
    assert payload["details"]["mismatches"]


def test_manifest_path_must_be_confined_supervisor_manifest(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    outside = tmp_path / "supervisor_manifest.json"
    outside.write_text("{}", encoding="utf-8")

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(outside),
        "--generation", "g0000",
        "--terminal-state", "done",
    ])

    assert json.loads(capsys.readouterr().out) == {"reason_code": "manifest_path_mismatch", "state": "unavailable"}


def test_parse_uses_same_stable_bytes_as_hashed_snapshot(tmp_path, monkeypatch, capsys):
    import lingtai.tools.daemon.return_observer_helper as helper

    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw")
    real_hash = helper._hash_file_with_bytes
    flipped = {"done": False}

    def hash_then_flip(root_fd, rel):
        row, raw = real_hash(root_fd, rel)
        if rel == "daemon.json" and not flipped["done"]:
            flipped["done"] = True
            state = json.loads((run_dir.path / "daemon.json").read_text(encoding="utf-8"))
            state["state"] = "failed"
            (run_dir.path / "daemon.json").write_text(json.dumps(state), encoding="utf-8")
        return row, raw

    monkeypatch.setattr(helper, "_hash_file_with_bytes", hash_then_flip)
    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", "g0000",
        "--terminal-state", "failed",
    ])

    assert json.loads(capsys.readouterr().out) == {"reason_code": "terminal_state_mismatch", "state": "unavailable"}


def test_followup_rejects_tampered_predecessor_portable_bytes(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    _manifest(run_dir, enabled=True)
    run_dir.mark_done("initial")
    helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
    side = run_dir.path / ".supervisor" / "return-observation"
    (side / "g0000.portable.json").write_text("{}", encoding="utf-8")
    generation = "g1-1234567890abcdef"
    run_dir.record_followup(generation, status="done", output="follow")

    helper_main([
        "--run-dir", str(run_dir.path),
        "--manifest-path", str(run_dir.path / "supervisor_manifest.json"),
        "--generation", generation,
        "--terminal-state", "follow-up done",
    ])

    assert json.loads(capsys.readouterr().out) == {"reason_code": "predecessor_tamper", "state": "unavailable"}


def test_sidecar_read_loops_over_short_reads(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_helper as helper

    side = tmp_path / "side"
    side.mkdir()
    target = side / "receipt.json"
    target.write_bytes(b"0123456789abcde")
    side_fd = os.open(side, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    real_read = helper.os.read

    def one_byte_read(fd, count):
        return real_read(fd, min(count, 1))

    try:
        monkeypatch.setattr(helper.os, "read", one_byte_read)
        assert helper._read_file_at(side_fd, "receipt.json") == b"0123456789abcde"
    finally:
        os.close(side_fd)


def test_sidecar_read_detects_same_size_mutation(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_helper as helper

    side = tmp_path / "side"
    side.mkdir()
    target = side / "receipt.json"
    target.write_bytes(b"AAAA")
    side_fd = os.open(side, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    real_read = helper.os.read
    changed = {"done": False}

    def mutating_read(fd, count):
        data = real_read(fd, count)
        if data and not changed["done"]:
            changed["done"] = True
            target.write_bytes(b"BBBB")
        return data

    try:
        monkeypatch.setattr(helper.os, "read", mutating_read)
        with pytest.raises(helper.Unavailable) as exc:
            helper._read_file_at(side_fd, "receipt.json")
        assert exc.value.reason_code == "mutation_detected"
    finally:
        os.close(side_fd)


def test_conflicting_concurrent_final_writers_do_not_overwrite(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_helper as helper

    side = tmp_path / "side"
    side.mkdir()
    side_fd = os.open(side, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    real_link = helper.os.link
    barrier = threading.Barrier(2)
    results: list[str] = []

    def racing_link(*args, **kwargs):
        barrier.wait(timeout=5)
        return real_link(*args, **kwargs)

    def writer(payload):
        try:
            helper._ensure_final_at(side_fd, "receipt.json", payload)
            results.append("available")
        except helper.Unavailable as exc:
            results.append(exc.reason_code)

    try:
        monkeypatch.setattr(helper.os, "link", racing_link)
        threads = [
            threading.Thread(target=writer, args=({"writer": "a"},)),
            threading.Thread(target=writer, args=({"writer": "b"},)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert sorted(results) == ["available", "receipt_conflict"]
        final = json.loads((side / "receipt.json").read_text(encoding="utf-8"))
        assert final in [{"writer": "a"}, {"writer": "b"}]
    finally:
        os.close(side_fd)


def _assert_fail_open_notification_invariants(run_dir: DaemonRunDir, expected_result: str) -> None:
    events = _matching_events(run_dir)
    assert len(events) == 1
    assert "return_observation" not in events[0]
    assert run_dir.result_path.read_text(encoding="utf-8") == expected_result
    assert DaemonRunDir.read_state_from_disk(run_dir.path)["state"] == "done"


@pytest.mark.parametrize(
    "fault",
    ["permission_denied", "enospc", "finalize_link_failure"],
)
def test_syscall_faults_fail_open_through_terminal_notification(tmp_path, monkeypatch, fault):
    import lingtai.tools.daemon.return_observer_helper as helper
    import lingtai.tools.daemon.return_observer_hook as hook

    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done(f"raw {fault}")

    if fault == "permission_denied":
        real_open = helper.os.open

        def denied_open(path, flags, *args, **kwargs):
            if isinstance(path, str) and path.startswith(".tmp-"):
                raise PermissionError(errno.EACCES, "permission denied", path)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(helper.os, "open", denied_open)
        expected = "unexpected_exception"
    elif fault == "enospc":
        real_write = helper.os.write
        injected = {"done": False}

        def enospc_write(fd, data):
            if not injected["done"]:
                injected["done"] = True
                raise OSError(errno.ENOSPC, "no space left on device")
            return real_write(fd, data)

        monkeypatch.setattr(helper.os, "write", enospc_write)
        expected = "unexpected_exception"
    else:
        def failing_link(*args, **kwargs):
            raise OSError(errno.EIO, "link failed")

        monkeypatch.setattr(helper.os, "link", failing_link)
        expected = "unexpected_exception"

    def in_process_observer(*args, **kwargs):
        try:
            helper_observe(run_dir.path, run_dir.path / "supervisor_manifest.json", "g0000", "done")
        except helper.Unavailable as exc:
            assert exc.reason_code == expected
        except OSError:
            assert expected == "unexpected_exception"
        return None

    monkeypatch.setattr(hook, "observe_return_bounded", in_process_observer)
    _publish_terminal_notification_if_needed(run_dir, manifest)
    _assert_fail_open_notification_invariants(run_dir, f"raw {fault}")


def test_killed_helper_fail_open_through_terminal_notification(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_hook as hook

    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw killed")
    monkeypatch.setattr(
        hook.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], -9, "", ""),
    )

    _publish_terminal_notification_if_needed(run_dir, manifest)

    _assert_fail_open_notification_invariants(run_dir, "raw killed")


def test_safe_notification_block_construction_failure_keeps_ordinary_notification(tmp_path, monkeypatch):
    import lingtai.tools.daemon.return_observer_hook as hook

    run_dir = _make_run_dir(tmp_path)
    manifest = _manifest(run_dir, enabled=True)
    run_dir.mark_done("raw malformed block")
    monkeypatch.setattr(
        hook,
        "observe_return_bounded",
        lambda *a, **k: {
            "schema_version": "lingtai.return-observation-notice.v0",
            "state": "available",
            "generation": "g0000",
            "receipt_digest": "sha256:" + "z" * 64,
            "authority": "advisory_only",
            "raw_result_unchanged": True,
        },
    )

    _publish_terminal_notification_if_needed(run_dir, manifest)

    _assert_fail_open_notification_invariants(run_dir, "raw malformed block")
