"""Hermetic contract tests for lifecycle intent and the shadow guardian."""
from __future__ import annotations

import io
import errno
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
from copy import deepcopy
from dataclasses import replace
from itertools import product
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import lingtai.adapters.agent_guardian as guardian_adapter
from lingtai.adapters.agent_guardian import (
    FilesystemLifecycleLedgerAdapter,
    LocalAgentGuardianHostAdapter,
    observe_guardian_manifest,
)
from lingtai.cli_guardian import (
    EXIT_ALREADY_RUNNING,
    EXIT_AMBIGUOUS,
    EXIT_LEDGER_UNSAFE,
    _checkpoint_due,
    run_guardian_cli,
)
from lingtai.kernel.agent_guardian import (
    GuardianAlreadyRunning,
    GuardianLeaseUnavailable,
    LifecycleLedgerCorruption,
    LifecycleLedgerError,
    PresenceSample,
    evaluate_presence,
    make_lifecycle_event,
    stable_json,
    validate_lifecycle_event,
)


def _id(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def _boot_event(root: Path, number: int = 1, *, address: str | None = None) -> dict:
    return make_lifecycle_event(
        "boot_registered",
        event_id=_id(number),
        recorded_at="2026-08-29T00:00:00.000Z",
        agent_address=address or root.name,
        actor_kind="runtime",
        actor_id=root.name,
        reason="runtime_boot",
        payload={
            "runtime_id": _id(number + 1000),
            "pid": 1234,
            "start_identity": "test:start:1",
            "working_dir": str(root.resolve()),
            "executable": str((root / "test-python").resolve()),
            "command": {"program": "python", "subcommand": "run", "agent_dir": str(root.resolve())},
        },
    )


def _adversarial_ledger_body(root: Path, kind: str) -> tuple[bytes, str]:
    if kind == "array_event":
        row = deepcopy(_boot_event(root))
        row["event"] = []
        return (stable_json(row) + "\n").encode("utf-8"), "event_unsupported"
    if kind == "huge_integer":
        text = stable_json(_boot_event(root)).replace(
            '"pid":1234',
            '"pid":' + ("9" * 5000),
        )
        return (text + "\n").encode("utf-8"), "malformed_record"
    if kind == "deep_json":
        return ("[" * 2000 + "0" + "]" * 2000 + "\n").encode("utf-8"), "malformed_record"
    raise AssertionError(kind)


def _ledger(root: Path) -> FilesystemLifecycleLedgerAdapter:
    ids = iter(_id(i) for i in range(10, 100))
    return FilesystemLifecycleLedgerAdapter(
        root,
        clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
        id_factory=lambda: next(ids),
    )


def test_ledger_stable_fsync_and_creation_directory_fsync(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(os, "fsync", lambda descriptor: calls.append(("file", descriptor)))
    monkeypatch.setattr(
        ledger,
        "_fsync_directory",
        lambda path: calls.append(("directory", path)),
    )
    monkeypatch.setattr(
        "lingtai.adapters.posix.process_identity.process_identity",
        lambda pid: "test:start:boot",
    )

    first = ledger.register_boot(agent_address=tmp_path.name, working_dir=str(tmp_path))
    assert first["payload"]["start_identity"] == "test:start:boot"
    assert first["payload"]["command"]["subcommand"] == "run"
    assert [kind for kind, _ in calls] == ["directory", "file", "directory"]
    assert calls[0] == ("directory", ledger.agent_dir)
    assert calls[2] == ("directory", ledger.path.parent)

    first_bytes = ledger.path.read_bytes()
    calls.clear()
    ledger.append_event(first)  # exact retry re-fsyncs but does not change bytes
    assert ledger.path.read_bytes() == first_bytes
    assert [kind for kind, _ in calls] == ["directory", "file", "directory"]
    assert calls[0] == ("directory", ledger.agent_dir)
    assert calls[2] == ("directory", ledger.path.parent)

    calls.clear()
    ledger.append_event(_boot_event(tmp_path, 2))
    assert [kind for kind, _ in calls] == ["directory", "file"]
    assert calls[0] == ("directory", ledger.agent_dir)
    assert ledger.path.read_bytes().endswith(b"\n")


def test_first_event_in_preexisting_logs_fsyncs_file_then_logs(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    ledger = _ledger(tmp_path)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(os, "fsync", lambda descriptor: calls.append(("file", descriptor)))
    monkeypatch.setattr(
        ledger,
        "_fsync_directory",
        lambda path: calls.append(("directory", path)),
    )

    ledger.append_event(_boot_event(tmp_path))

    assert [kind for kind, _ in calls] == ["directory", "file", "directory"]
    assert calls[0] == ("directory", ledger.agent_dir)
    assert calls[2] == ("directory", logs)


def test_duplicate_retry_repairs_failed_first_event_directory_fsync(
    tmp_path, monkeypatch,
):
    logs = tmp_path / "logs"
    logs.mkdir()
    ledger = _ledger(tmp_path)
    event = _boot_event(tmp_path)

    def fail_logs(directory):
        if directory == logs:
            raise OSError("directory fsync failed")
        assert directory == ledger.agent_dir

    monkeypatch.setattr(ledger, "_fsync_directory", fail_logs)
    with pytest.raises(LifecycleLedgerError) as raised:
        ledger.append_event(event)
    assert raised.value.code == "ledger_write_failed"
    written = ledger.path.read_bytes()

    calls: list[Path] = []
    monkeypatch.setattr(ledger, "_fsync_directory", calls.append)
    assert ledger.append_event(event) == event

    assert ledger.path.read_bytes() == written
    assert calls == [ledger.agent_dir, logs]


def test_retry_repairs_failed_fresh_logs_parent_fsync(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    event = _boot_event(tmp_path)
    calls: list[Path] = []

    def fail_first_parent_sync(directory):
        calls.append(directory)
        if directory == ledger.agent_dir and calls.count(directory) == 1:
            raise OSError("agent-dir fsync failed")

    monkeypatch.setattr(ledger, "_fsync_directory", fail_first_parent_sync)

    with pytest.raises(LifecycleLedgerError) as raised:
        ledger.append_event(event)
    assert raised.value.code == "ledger_io_error"
    assert ledger.path.parent.is_dir()
    assert not ledger.path.exists()

    assert ledger.append_event(event) == event
    assert calls == [ledger.agent_dir, ledger.agent_dir, ledger.path.parent]
    assert ledger.read_snapshot().records == (event,)


def test_read_only_missing_ledger_does_not_create_logs_or_agent_root(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    ledger = _ledger(agent_dir)

    assert ledger.read_snapshot().records == ()
    assert list(agent_dir.iterdir()) == []

    missing = _ledger(tmp_path / "missing")
    with pytest.raises(LifecycleLedgerError) as raised:
        missing.read_snapshot()
    assert raised.value.code == "ledger_agent_dir_unavailable"
    assert not missing.agent_dir.exists()


def test_ledger_concurrent_appends_are_complete_and_parent_sync_is_locked(
    tmp_path, monkeypatch,
):
    ledger = FilesystemLifecycleLedgerAdapter(tmp_path)
    events = [_boot_event(tmp_path, i) for i in range(1, 17)]
    root_syncs: list[Path] = []

    def assert_locked_sync(directory):
        if directory == ledger.agent_dir:
            assert ledger._lock.is_locked
            root_syncs.append(directory)

    monkeypatch.setattr(ledger, "_fsync_directory", assert_locked_sync)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(ledger.append_event, events))
    snapshot = ledger.read_snapshot()
    assert len(snapshot.records) == len(events)
    assert len(ledger.path.read_bytes().splitlines()) == len(events)
    assert root_syncs == [ledger.agent_dir] * len(events)


def test_active_intent_survives_marker_deletion_and_clears_only_matching_explicit_actions(tmp_path):
    ledger = _ledger(tmp_path)
    intent = ledger.request_suspend(agent_address=tmp_path.name, actor_id="operator", reason="maintenance")
    assert ledger.request_suspend(agent_address=tmp_path.name, actor_id="operator", reason="duplicate") == intent
    marker = tmp_path / ".suspend"
    marker.write_text("", encoding="utf-8")
    marker.unlink()
    assert ledger.read_snapshot().active_intent_id == intent
    assert ledger.request_cpr(agent_address=tmp_path.name, actor_id="operator", reason="resume") == intent
    assert ledger.read_snapshot().active_intent_id is None
    next_intent = ledger.request_suspend(agent_address=tmp_path.name, actor_id="operator", reason="again")
    assert ledger.request_cpr(agent_address=tmp_path.name, actor_id="reviver", reason="cpr") == next_intent
    assert ledger.read_snapshot().active_intent_id is None
    assert ledger.request_cpr(agent_address=tmp_path.name, actor_id="reviver", reason="duplicate") is None
    assert [row["event"] for row in ledger.read_snapshot().records].count("suspend_requested") == 2


def test_register_boot_rejects_active_intent_without_mutating_ledger_then_succeeds_after_cpr(
    tmp_path, monkeypatch,
):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(
        "lingtai.adapters.posix.process_identity.process_identity",
        lambda pid: "test:start:boot",
    )
    intent = ledger.request_suspend(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="maintenance",
    )
    before_bytes = ledger.path.read_bytes()
    before_snapshot = ledger.read_snapshot()

    with pytest.raises(LifecycleLedgerError) as raised:
        ledger.register_boot(agent_address=tmp_path.name, working_dir=str(tmp_path))

    assert raised.value.code == "explicit_suspend_active"
    assert ledger.path.read_bytes() == before_bytes
    assert ledger.read_snapshot() == before_snapshot

    assert ledger.request_cpr(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="resume",
    ) == intent
    boot = ledger.register_boot(agent_address=tmp_path.name, working_dir=str(tmp_path))
    assert boot["event"] == "boot_registered"
    assert ledger.read_snapshot().active_intent_id is None


def test_raw_append_rejects_boot_during_active_suspend_without_changing_bytes(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.request_suspend(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="maintenance",
    )
    before = ledger.path.read_bytes()

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        ledger.append_event(_boot_event(tmp_path))

    assert raised.value.code == "boot_while_suspend_active"
    assert ledger.path.read_bytes() == before


def test_physical_suspend_then_boot_history_is_semantically_corrupt(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.request_suspend(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="maintenance",
    )
    ledger.path.write_bytes(
        ledger.path.read_bytes()
        + (stable_json(_boot_event(tmp_path)) + "\n").encode("utf-8")
    )

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        ledger.read_snapshot()

    assert raised.value.code == "boot_while_suspend_active"


def test_suspend_matching_cpr_then_raw_boot_is_valid(tmp_path):
    ledger = _ledger(tmp_path)
    intent = ledger.request_suspend(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="maintenance",
    )
    assert ledger.request_cpr(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="resume",
    ) == intent

    boot = ledger.append_event(_boot_event(tmp_path))
    snapshot = ledger.read_snapshot()

    assert snapshot.active_intent_id is None
    assert snapshot.latest_boot == boot


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b'{"broken":\n', "malformed_record"),
        (b'{"broken":true}', "torn_final_record"),
        (b'{"schema":"future"}\n', "record_fields_unsupported"),
    ],
)
def test_malformed_torn_and_unsupported_records_fail_closed(tmp_path, body, code):
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_bytes(body)
    with pytest.raises(LifecycleLedgerCorruption, match=code):
        FilesystemLifecycleLedgerAdapter(tmp_path).read_snapshot()


def test_valid_json_array_event_is_typed_corruption(tmp_path):
    row = deepcopy(_boot_event(tmp_path))
    row["event"] = []
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_bytes((stable_json(row) + "\n").encode("utf-8"))

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        FilesystemLifecycleLedgerAdapter(tmp_path).read_snapshot()

    assert raised.value.code == "event_unsupported"


@pytest.mark.parametrize("body_kind", ["huge_integer", "deep_json"])
def test_json_conversion_and_recursion_fail_as_malformed_record(tmp_path, body_kind):
    if body_kind == "huge_integer":
        text = stable_json(_boot_event(tmp_path)).replace(
            '"pid":1234',
            '"pid":' + ("9" * 5000),
        )
    else:
        text = "[" * 2000 + "0" + "]" * 2000
    body = (text + "\n").encode("utf-8")
    assert len(body) < 64 * 1024
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_bytes(body)

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        FilesystemLifecycleLedgerAdapter(tmp_path).read_snapshot()

    assert raised.value.code == "malformed_record"


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("actor", "invalid_actor"),
        ("reason", "invalid_reason"),
        ("address", "invalid_agent_address"),
    ],
)
def test_lone_surrogate_persisted_text_is_rejected_before_io(
    tmp_path, field, expected_code,
):
    values = {
        "agent_address": tmp_path.name,
        "actor_id": "operator",
        "reason": "maintenance",
    }
    values[
        {"actor": "actor_id", "reason": "reason", "address": "agent_address"}[field]
    ] = "bad\ud800text"
    ledger = _ledger(tmp_path)

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        make_lifecycle_event(
            "suspend_requested",
            event_id=_id(998),
            recorded_at="2026-08-29T00:00:00.000Z",
            agent_address=values["agent_address"],
            actor_kind="agent",
            actor_id=values["actor_id"],
            reason=values["reason"],
            payload={"intent_id": _id(999)},
        )

    assert raised.value.code == expected_code
    assert not ledger.path.exists()


def test_append_encoding_failure_is_typed_before_file_creation(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    event = _boot_event(tmp_path)
    monkeypatch.setattr(
        guardian_adapter,
        "stable_json",
        lambda value: (_ for _ in ()).throw(
            UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogate")
        ),
    )

    with pytest.raises(LifecycleLedgerError) as raised:
        ledger.append_event(event)

    assert raised.value.code == "invalid_event_encoding"
    assert not ledger.path.exists()


@pytest.mark.parametrize(
    ("constant", "value", "body", "code"),
    [
        ("MAX_LEDGER_BYTES", 1, b"{}\n", "ledger_byte_limit_exceeded"),
        ("MAX_LEDGER_RECORD_BYTES", 2, b"{}\n", "ledger_record_limit_exceeded"),
    ],
)
def test_ledger_byte_and_record_bounds_fail_closed(tmp_path, monkeypatch, constant, value, body, code):
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_bytes(body)
    monkeypatch.setattr(guardian_adapter, constant, value)
    with pytest.raises(LifecycleLedgerCorruption, match=code):
        FilesystemLifecycleLedgerAdapter(tmp_path).read_snapshot()


def test_descriptor_fstat_sees_path_replacement_before_read(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    ledger.path.parent.mkdir()
    ledger.path.write_bytes(b"")
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes((stable_json(_boot_event(tmp_path)) + "\n").encode("utf-8"))
    assert replacement.stat().st_size > 100
    original_open = Path.open
    replaced = False

    def open_after_replace(path, mode="r", *args, **kwargs):
        nonlocal replaced
        if path == ledger.path and mode == "rb" and not replaced:
            os.replace(replacement, ledger.path)
            replaced = True
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_replace)
    monkeypatch.setattr(guardian_adapter, "MAX_LEDGER_BYTES", 100)

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        ledger.read_snapshot()

    assert raised.value.code == "ledger_byte_limit_exceeded"
    assert replaced


def test_cumulative_byte_bound_catches_growth_after_descriptor_fstat(
    tmp_path, monkeypatch,
):
    ledger = _ledger(tmp_path)
    ledger.path.parent.mkdir()
    ledger.path.write_bytes(b"")
    growth = (stable_json(_boot_event(tmp_path)) + "\n").encode("utf-8")
    assert len(growth) > 100
    original_fstat = os.fstat
    grew = False

    def fstat_then_grow(descriptor):
        nonlocal grew
        result = original_fstat(descriptor)
        if not grew:
            grew = True
            growth_descriptor = os.open(ledger.path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(growth_descriptor, growth)
            finally:
                os.close(growth_descriptor)
        return result

    monkeypatch.setattr(os, "fstat", fstat_then_grow)
    monkeypatch.setattr(guardian_adapter, "MAX_LEDGER_BYTES", 100)

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        ledger.read_snapshot()

    assert raised.value.code == "ledger_byte_limit_exceeded"
    assert grew and ledger.path.stat().st_size == len(growth)


def test_append_reads_preflights_and_writes_the_same_descriptor(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    first = _boot_event(tmp_path, 1)
    second = _boot_event(tmp_path, 2)
    ledger.append_event(first)
    read_descriptors: list[int] = []
    write_descriptors: list[int] = []
    original_read = ledger._read_handle
    original_write = ledger._write_append

    def read_handle(handle):
        read_descriptors.append(handle.fileno())
        return original_read(handle)

    def write_append(handle, encoded, *, created):
        write_descriptors.append(handle.fileno())
        return original_write(handle, encoded, created=created)

    monkeypatch.setattr(ledger, "_read_handle", read_handle)
    monkeypatch.setattr(ledger, "_write_append", write_append)

    ledger.append_event(second)

    assert read_descriptors == write_descriptors
    assert len(read_descriptors) == 1


def test_append_growth_after_descriptor_fstat_cannot_bypass_total_preflight(
    tmp_path, monkeypatch,
):
    ledger = _ledger(tmp_path)
    ledger.append_event(_boot_event(tmp_path, 1))
    before = ledger.path.read_bytes()
    external_growth = b"x" * 100
    original_fstat = os.fstat
    grew = False

    def fstat_then_grow(descriptor):
        nonlocal grew
        result = original_fstat(descriptor)
        if not grew:
            grew = True
            growth_descriptor = os.open(ledger.path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(growth_descriptor, external_growth)
            finally:
                os.close(growth_descriptor)
        return result

    monkeypatch.setattr(os, "fstat", fstat_then_grow)
    monkeypatch.setattr(
        guardian_adapter,
        "MAX_LEDGER_BYTES",
        len(before) + 50,
    )

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        ledger.append_event(_boot_event(tmp_path, 2))

    assert raised.value.code == "ledger_byte_limit_exceeded"
    assert ledger.path.read_bytes() == before + external_growth


def test_append_byte_preflight_preserves_bytes_and_duplicate_at_limit(
    tmp_path, monkeypatch,
):
    ledger = _ledger(tmp_path)
    first = _boot_event(tmp_path, 1)
    ledger.append_event(first)
    at_limit = ledger.path.read_bytes()
    monkeypatch.setattr(guardian_adapter, "MAX_LEDGER_BYTES", len(at_limit))

    assert ledger.append_event(first) == first
    with pytest.raises(LifecycleLedgerError) as raised:
        ledger.append_event(_boot_event(tmp_path, 2))

    assert raised.value.code == "ledger_byte_limit_exceeded"
    assert ledger.path.read_bytes() == at_limit


def test_ledger_record_count_bound_fails_closed(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    ledger.append_event(_boot_event(tmp_path))
    monkeypatch.setattr(guardian_adapter, "MAX_LEDGER_RECORDS", 0)
    with pytest.raises(LifecycleLedgerCorruption, match="ledger_record_count_exceeded"):
        ledger.read_snapshot()


def test_append_stops_at_last_readable_record_and_duplicate_at_limit_is_idempotent(
    tmp_path, monkeypatch,
):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(guardian_adapter, "MAX_LEDGER_RECORDS", 1)
    first = _boot_event(tmp_path, 1)
    assert ledger.append_event(first) == first
    at_limit = ledger.path.read_bytes()

    assert ledger.append_event(first) == first
    assert ledger.path.read_bytes() == at_limit

    with pytest.raises(LifecycleLedgerError) as raised:
        ledger.append_event(_boot_event(tmp_path, 2))

    assert raised.value.code == "ledger_record_count_exceeded"
    assert ledger.path.read_bytes() == at_limit
    assert ledger.read_snapshot().records == (first,)


def test_physical_duplicate_rows_cannot_bypass_append_count_preflight(
    tmp_path, monkeypatch,
):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(guardian_adapter, "MAX_LEDGER_RECORDS", 2)
    first = _boot_event(tmp_path, 1)
    ledger.append_event(first)
    physical_row = ledger.path.read_bytes()
    ledger.path.write_bytes(physical_row + physical_row)

    snapshot = ledger.read_snapshot()
    assert snapshot.records == (first,)
    assert snapshot.physical_record_count == 2
    at_limit = ledger.path.read_bytes()
    assert ledger.append_event(first) == first

    with pytest.raises(LifecycleLedgerError) as raised:
        ledger.append_event(_boot_event(tmp_path, 2))

    assert raised.value.code == "ledger_record_count_exceeded"
    assert ledger.path.read_bytes() == at_limit
    assert ledger.read_snapshot().physical_record_count == 2


def test_malformed_interior_and_mismatched_clear_fail_closed(tmp_path):
    interior_root = tmp_path / "interior"
    interior_root.mkdir()
    interior = _ledger(interior_root)
    event = _boot_event(interior.agent_dir)
    interior.append_event(event)
    interior.path.write_bytes(interior.path.read_bytes() + b"{broken\n" + (stable_json(event) + "\n").encode())
    with pytest.raises(LifecycleLedgerCorruption, match="malformed_record"):
        interior.read_snapshot()

    ledger = _ledger(tmp_path)
    intent = ledger.request_suspend(
        agent_address=ledger.agent_dir.name,
        actor_id="operator",
        reason="test",
    )
    bad_clear = make_lifecycle_event(
        "cpr_requested",
        event_id=_id(999),
        recorded_at="2026-08-29T00:00:01.000Z",
        agent_address=ledger.agent_dir.name,
        actor_kind="operator",
        actor_id="operator",
        reason="wrong",
        payload={"clears_intent_id": _id(998)},
    )
    ledger.path.write_bytes(ledger.path.read_bytes() + (stable_json(bad_clear) + "\n").encode())
    with pytest.raises(LifecycleLedgerCorruption, match="intent_clear_mismatch"):
        ledger.read_snapshot()
    assert intent != bad_clear["payload"]["clears_intent_id"]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda payload, root: payload.__setitem__("pid", int("9" * 101)), "invalid_pid"),
        (lambda payload, root: payload.__setitem__("working_dir", f"{root}\0bad"), "invalid_working_dir"),
        (
            lambda payload, root: payload["command"].__setitem__(
                "agent_dir", str((root / "other").resolve())
            ),
            "boot_agent_dir_mismatch",
        ),
    ],
)
def test_boot_payload_construction_rejects_unsafe_v1_shaped_evidence(
    tmp_path, mutation, code,
):
    template = _boot_event(tmp_path)
    payload = deepcopy(template["payload"])
    mutation(payload, tmp_path)

    with pytest.raises(LifecycleLedgerCorruption, match=code):
        make_lifecycle_event(
            "boot_registered",
            event_id=_id(900),
            recorded_at="2026-08-29T00:00:00.000Z",
            agent_address=tmp_path.name,
            actor_kind="runtime",
            actor_id=tmp_path.name,
            reason="runtime_boot",
            payload=payload,
        )


def test_adapter_rejects_single_and_mixed_foreign_address_ledgers(tmp_path):
    ledger = _ledger(tmp_path)
    local = _boot_event(tmp_path, 1)
    foreign = _boot_event(tmp_path, 2, address="copied-agent")
    ledger.path.parent.mkdir()

    for rows in ((foreign,), (local, foreign)):
        ledger.path.write_bytes(
            b"".join((stable_json(row) + "\n").encode("utf-8") for row in rows)
        )
        with pytest.raises(LifecycleLedgerCorruption) as raised:
            ledger.read_snapshot()
        assert raised.value.code == "agent_address_mismatch"

    ledger.path.unlink()
    with pytest.raises(LifecycleLedgerCorruption) as raised:
        ledger.append_event(foreign)
    assert raised.value.code == "agent_address_mismatch"
    assert not ledger.path.exists()


def test_ledger_parent_setup_error_is_typed(tmp_path):
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")

    with pytest.raises(LifecycleLedgerError) as raised:
        FilesystemLifecycleLedgerAdapter(tmp_path).read_snapshot()

    assert raised.value.code == "ledger_io_error"


def _sample(**overrides) -> PresenceSample:
    values = {
        "sampled_at": 1000.0,
        "runtime_id": _id(100),
        "pid": 123,
        "expected_start_identity": "start:1",
        "observed_start_identity": "start:1",
        "process": "exact_running",
        "agent_lease": "held",
        "agent_manifest": "valid",
        "heartbeat": "fresh",
        "heartbeat_age_seconds": 1.0,
        "command_match": True,
        "executable_match": True,
        "registered_workdir_match": True,
        "issues": (),
    }
    values.update(overrides)
    return PresenceSample(**values)


def _guardian_event(
    payload: dict,
    *,
    actor_kind: str = "guardian",
    actor_id: str | None = None,
    number: int = 700,
) -> dict:
    return make_lifecycle_event(
        "guardian_verdict",
        event_id=_id(number),
        recorded_at="2026-08-29T00:00:00.000Z",
        agent_address="target",
        actor_kind=actor_kind,
        actor_id=actor_id or str(payload["guardian_id"]),
        reason="shadow_presence_evaluation",
        payload=payload,
    )


def _decision_payload(
    first: PresenceSample,
    second: PresenceSample | None = None,
    *,
    intent: str | None = None,
) -> dict:
    decision = evaluate_presence(first, second, active_intent_id=intent)
    return decision.event_payload(_id(650))


@pytest.mark.parametrize(
    ("first", "second", "intent"),
    [
        (_sample(), None, None),
        (_sample(process="exact_stopped"), None, None),
        (
            _sample(process="exact_stopped", heartbeat="stale", heartbeat_age_seconds=121.0),
            _sample(process="exact_stopped", heartbeat="stale", heartbeat_age_seconds=123.0),
            None,
        ),
        (
            _sample(
                process="absent",
                agent_lease="free",
                heartbeat="stale",
                heartbeat_age_seconds=500.0,
                observed_start_identity=None,
                command_match=None,
                executable_match=None,
            ),
            _sample(
                process="absent",
                agent_lease="free",
                heartbeat="stale",
                heartbeat_age_seconds=502.0,
                observed_start_identity=None,
                command_match=None,
                executable_match=None,
            ),
            None,
        ),
        (
            _sample(
                process="command_mismatch",
                command_match=False,
                heartbeat="stale",
                heartbeat_age_seconds=500.0,
            ),
            _sample(
                process="command_mismatch",
                command_match=False,
                heartbeat="stale",
                heartbeat_age_seconds=502.0,
            ),
            None,
        ),
        (_sample(), None, _id(55)),
    ],
)
def test_real_guardian_policy_rows_pass_strict_json_round_trip(
    first, second, intent,
):
    payload = _decision_payload(first, second, intent=intent)
    event = _guardian_event(payload)
    assert validate_lifecycle_event(json.loads(stable_json(event))) == event


@pytest.mark.parametrize(
    "mutations",
    [
        {"recovery_plan": "would_launch", "process": "absent", "agent_lease": "free", "confirmation": "confirmed", "heartbeat_age_seconds": 999.0},
        {"runtime_id": None},
        {"intent": "active", "recovery_plan": "none"},
        {"verdict": "frozen", "recovery_plan": "would_sigcont", "process": "exact_running"},
        {"verdict": "frozen", "recovery_plan": "would_sigcont", "process": "exact_stopped", "confirmation": "not_required", "heartbeat_age_seconds": 999.0},
        {"verdict": "dead", "recovery_plan": "would_launch", "process": "absent", "agent_lease": "held", "confirmation": "confirmed", "heartbeat_age_seconds": 999.0},
        {"verdict": "dead", "recovery_plan": "would_launch", "process": "absent", "agent_lease": "free", "confirmation": "confirmed", "heartbeat_age_seconds": 1.0},
        {"verdict": "unknown", "recovery_plan": "would_launch", "confirmation": "unavailable"},
        {"verdict": "unknown", "recovery_plan": "observe_only", "confirmation": "unavailable"},
        {"verdict": "unknown", "recovery_plan": "observe_only", "process": "absent", "agent_lease": "free", "confirmation": "confirmed", "heartbeat_age_seconds": 999.0},
    ],
)
def test_guardian_semantic_cross_product_edges_are_rejected(mutations):
    payload = _decision_payload(_sample())
    payload.update(mutations)
    with pytest.raises(LifecycleLedgerCorruption, match="invalid_guardian_semantics"):
        _guardian_event(payload)


def test_guardian_verdict_requires_guardian_actor():
    with pytest.raises(LifecycleLedgerCorruption, match="invalid_guardian_actor"):
        _guardian_event(_decision_payload(_sample()), actor_kind="runtime")


def test_guardian_actor_id_must_equal_payload_guardian_id():
    with pytest.raises(LifecycleLedgerCorruption) as raised:
        _guardian_event(
            _decision_payload(_sample()),
            actor_id=_id(999),
        )
    assert raised.value.code == "guardian_actor_id_mismatch"


@pytest.mark.parametrize(
    ("target", "field", "expected_code"),
    [
        ("record", "event", "event_unsupported"),
        ("record", "actor", "invalid_actor"),
        ("actor", "kind", "invalid_actor"),
        ("actor", "id", "invalid_actor"),
        ("record", "payload", "invalid_payload"),
        ("payload", "verdict", "invalid_guardian_decision"),
        ("payload", "recovery_plan", "invalid_guardian_decision"),
        ("payload", "confirmation", "invalid_confirmation"),
        ("payload", "process", "invalid_guardian_evidence"),
        ("payload", "agent_lease", "invalid_guardian_evidence"),
        ("payload", "agent_manifest", "invalid_guardian_evidence"),
        ("payload", "intent", "invalid_guardian_intent"),
    ],
)
def test_array_valued_structural_fields_never_escape_typed_validation(
    target, field, expected_code,
):
    if target == "payload":
        row = deepcopy(_guardian_event(_decision_payload(_sample())))
        row["payload"][field] = []
    else:
        row = deepcopy(_boot_event(Path.cwd()))
        if target == "record":
            row[field] = []
        else:
            row[target][field] = []

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        validate_lifecycle_event(row)

    assert raised.value.code == expected_code


@pytest.mark.parametrize(
    "sample",
    [
        _sample(runtime_id=None, pid=None, expected_start_identity=None),
        _sample(agent_manifest="malformed"),
        _sample(heartbeat="fresh", heartbeat_age_seconds=None),
        _sample(heartbeat="stale", heartbeat_age_seconds=1.0),
    ],
)
def test_invalid_presence_samples_fail_with_one_stable_code(sample):
    with pytest.raises(LifecycleLedgerError) as raised:
        evaluate_presence(sample, None, active_intent_id=None)
    assert raised.value.code == "invalid_presence_sample"


def test_supported_presence_domain_always_emits_a_strict_round_trip_row():
    process_facts = {
        "exact_running": {},
        "exact_stopped": {},
        "absent": {
            "observed_start_identity": None,
            "command_match": None,
            "executable_match": None,
        },
        "identity_mismatch": {"observed_start_identity": "start:other"},
        "command_mismatch": {"command_match": False},
        "executable_mismatch": {"executable_match": False},
        "unavailable": {},
    }
    heartbeat_facts = {
        "fresh": 1.0,
        "stale": 121.0,
        "missing": None,
        "unreadable": None,
    }
    guardian_id = _id(650)
    checked = 0

    for process_result, lease, manifest, heartbeat, has_issue in product(
        process_facts,
        ("held", "free", "unknown"),
        ("valid", "malformed", "absent"),
        heartbeat_facts,
        (False, True),
    ):
        if process_result in {"exact_running", "exact_stopped"} and manifest != "valid":
            continue
        first = _sample(
            process=process_result,
            agent_lease=lease,
            agent_manifest=manifest,
            heartbeat=heartbeat,
            heartbeat_age_seconds=heartbeat_facts[heartbeat],
            issues=("sample_issue",) if has_issue else (),
            **process_facts[process_result],
        )
        second = None
        if not (
            heartbeat == "fresh"
            and process_result in {"exact_running", "exact_stopped"}
            and lease == "held"
            and not has_issue
        ):
            second = replace(
                first,
                sampled_at=first.sampled_at + 2.0,
                heartbeat_age_seconds=(
                    first.heartbeat_age_seconds + 2.0
                    if first.heartbeat_age_seconds is not None
                    else None
                ),
            )
        decision = evaluate_presence(first, second, active_intent_id=None)
        event = _guardian_event(decision.event_payload(guardian_id))
        assert validate_lifecycle_event(json.loads(stable_json(event))) == event
        checked += 1

    assert checked == 408


def test_bound_boot_rejects_foreign_workdir_before_process_observation(
    tmp_path, monkeypatch,
):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(
        "lingtai.adapters.posix.process_identity.process_identity",
        lambda pid: (_ for _ in ()).throw(AssertionError("process observed")),
    )

    with pytest.raises(LifecycleLedgerCorruption) as raised:
        ledger.register_boot(
            agent_address=tmp_path.name,
            working_dir=str(tmp_path / "foreign"),
        )

    assert raised.value.code == "boot_agent_dir_mismatch"
    assert not ledger.path.exists()


def test_strict_read_and_append_reject_impossible_guardian_row(tmp_path):
    valid = _guardian_event(_decision_payload(_sample()))
    impossible = deepcopy(valid)
    impossible["payload"].update(
        recovery_plan="would_launch",
        process="absent",
        agent_lease="free",
        confirmation="confirmed",
        heartbeat_age_seconds=999.0,
    )
    ledger = _ledger(tmp_path)
    impossible["agent_address"] = tmp_path.name
    ledger.path.parent.mkdir()
    ledger.path.write_bytes((stable_json(impossible) + "\n").encode("utf-8"))
    with pytest.raises(LifecycleLedgerCorruption, match="invalid_guardian_semantics"):
        ledger.read_snapshot()
    ledger.path.unlink()
    with pytest.raises(LifecycleLedgerCorruption, match="invalid_guardian_semantics"):
        ledger.append_event(impossible)


@pytest.mark.parametrize(
    ("first", "second", "intent", "verdict", "plan"),
    [
        (_sample(), None, None, "alive", "none"),
        (_sample(process="exact_stopped"), None, None, "frozen", "would_sigcont"),
        (_sample(process="absent", agent_lease="free", heartbeat="stale", heartbeat_age_seconds=500.0, observed_start_identity=None, command_match=None, executable_match=None),
         _sample(process="absent", agent_lease="free", heartbeat="stale", heartbeat_age_seconds=502.0, observed_start_identity=None, command_match=None, executable_match=None), None, "dead", "would_launch"),
        (_sample(process="identity_mismatch", observed_start_identity="start:2", heartbeat="stale", heartbeat_age_seconds=500.0),
         _sample(process="identity_mismatch", observed_start_identity="start:2", heartbeat="stale", heartbeat_age_seconds=502.0), None, "unknown", "observe_only"),
        (_sample(process="unavailable", registered_workdir_match=False, issues=("boot_registration_workdir_mismatch",)),
         _sample(process="unavailable", registered_workdir_match=False, issues=("boot_registration_workdir_mismatch",)), None, "unknown", "observe_only"),
        (_sample(agent_lease="free"), _sample(agent_lease="free"), None, "unknown", "observe_only"),
        (_sample(process="absent", agent_lease="free", heartbeat="stale", heartbeat_age_seconds=500.0, observed_start_identity=None, command_match=None, executable_match=None),
         _sample(process="absent", agent_lease="free", heartbeat="stale", heartbeat_age_seconds=502.0, observed_start_identity=None, command_match=None, executable_match=None), _id(55), "dead", "hold_explicit_suspend"),
    ],
)
def test_four_verdicts_and_five_shadow_plans(first, second, intent, verdict, plan):
    decision = evaluate_presence(first, second, active_intent_id=intent)
    assert (decision.verdict, decision.recovery_plan) == (verdict, plan)


def test_stale_stopped_requires_matching_second_sample_and_never_plans_launch():
    first = _sample(process="exact_stopped", heartbeat="stale", heartbeat_age_seconds=121.0)
    assert evaluate_presence(first, None, active_intent_id=None).verdict == "unknown"
    confirmed = evaluate_presence(first, _sample(process="exact_stopped", heartbeat="stale", heartbeat_age_seconds=123.0), active_intent_id=None)
    assert (confirmed.verdict, confirmed.recovery_plan, confirmed.confirmation) == ("frozen", "would_sigcont", "confirmed")
    changed = evaluate_presence(first, _sample(process="absent", agent_lease="free", observed_start_identity=None, command_match=None, executable_match=None, heartbeat="stale", heartbeat_age_seconds=123.0), active_intent_id=None)
    assert (changed.verdict, changed.recovery_plan, changed.confirmation) == ("unknown", "observe_only", "changed")


def test_host_maps_platform_stopped_state_without_signalling(tmp_path, monkeypatch):
    host = LocalAgentGuardianHostAdapter(tmp_path)
    boot = _boot_event(tmp_path)["payload"]
    command = f"python -m lingtai run {tmp_path.resolve()}"
    monkeypatch.setattr(guardian_adapter.sys, "platform", "linux")
    monkeypatch.setattr(host, "_linux_observation", lambda pid: (
        boot["start_identity"], "T", command, boot["executable"],
    ))
    assert host._process_observation(boot)[0] == "exact_stopped"


def test_agent_lease_stat_failure_is_unknown(tmp_path, monkeypatch):
    host = LocalAgentGuardianHostAdapter(tmp_path)
    lock_path = tmp_path / ".agent.lock"
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        if path == lock_path:
            raise PermissionError("lease stat denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert host._observe_agent_lease() == "unknown"


def test_host_rejects_out_of_domain_pid_before_platform_api(tmp_path, monkeypatch):
    host = LocalAgentGuardianHostAdapter(tmp_path)
    boot = deepcopy(_boot_event(tmp_path)["payload"])
    boot["pid"] = int("9" * 101)
    monkeypatch.setattr(guardian_adapter.sys, "platform", "darwin")
    monkeypatch.setattr(
        host,
        "_darwin_observation",
        lambda pid: (_ for _ in ()).throw(AssertionError("platform API called")),
    )

    result = host._process_observation(boot)

    assert result[0] == "unavailable"
    assert result[4] == ("recorded_pid_invalid",)


@pytest.mark.parametrize(
    ("probe_errno", "expected"),
    [
        (errno.ESRCH, "absent"),
        (errno.EPERM, "unavailable"),
        (errno.EACCES, "unavailable"),
        (None, "unavailable"),
    ],
)
def test_darwin_libproc_miss_uses_only_signal_zero_for_pid_existence(
    tmp_path, monkeypatch, probe_errno, expected,
):
    from lingtai.adapters.posix import process_identity as identity_module

    host = LocalAgentGuardianHostAdapter(tmp_path)
    boot = _boot_event(tmp_path)["payload"]
    calls: list[tuple[int, int]] = []

    monkeypatch.setattr(guardian_adapter.sys, "platform", "darwin")
    monkeypatch.setattr(
        LocalAgentGuardianHostAdapter,
        "_darwin_info",
        staticmethod(lambda pid: None),
    )
    monkeypatch.setattr(identity_module, "_darwin_process_identity", lambda pid: None)

    def probe(pid: int, signal_number: int) -> None:
        calls.append((pid, signal_number))
        if probe_errno is not None:
            raise OSError(probe_errno, "existence probe")

    monkeypatch.setattr(guardian_adapter.os, "kill", probe)
    assert host._process_observation(boot)[0] == expected
    assert calls == [(boot["pid"], 0)]


def test_darwin_exact_present_identity_and_windows_unavailable_evidence_are_preserved(
    tmp_path, monkeypatch,
):
    host = LocalAgentGuardianHostAdapter(tmp_path)
    boot = _boot_event(tmp_path)["payload"]
    command = f"python -m lingtai run {tmp_path.resolve()}"

    monkeypatch.setattr(guardian_adapter.sys, "platform", "darwin")
    monkeypatch.setattr(host, "_darwin_observation", lambda pid: (
        boot["start_identity"], "R", command, boot["executable"],
    ))
    assert host._process_observation(boot)[0] == "exact_running"

    monkeypatch.setattr(guardian_adapter.sys, "platform", "win32")
    monkeypatch.setattr(host, "_windows_observation", lambda pid: (
        boot["start_identity"], None, None, None,
    ))
    result = host._process_observation(boot)
    assert result[0] == "unavailable"
    assert result[4] == ("exact_process_state_or_command_unavailable",)

    source = Path(guardian_adapter.__file__).read_text(encoding="utf-8")
    assert source.count("os.kill(") == 1
    assert "os.kill(pid, 0)" in source


@pytest.mark.parametrize(
    ("identity_after", "expected_process"),
    [("test:start:2", "identity_mismatch"), (None, "unavailable")],
)
def test_linux_observation_rechecks_incarnation_after_process_evidence(
    tmp_path, monkeypatch, identity_after, expected_process,
):
    from lingtai.adapters.posix import process_identity as identity_module

    pid = 1234
    proc_root = tmp_path / "proc"
    process_root = proc_root / str(pid)
    process_root.mkdir(parents=True)
    (process_root / "stat").write_text(
        f"{pid} (python) R " + " ".join("0" for _ in range(30)),
        encoding="utf-8",
    )
    (process_root / "cmdline").write_bytes(b"python\0-m\0lingtai\0run\0/test\0")
    (process_root / "exe").write_text("executable", encoding="utf-8")
    identities = iter(["test:start:1", identity_after])
    monkeypatch.setattr(
        identity_module,
        "_linux_process_identity",
        lambda observed_pid: next(identities),
    )

    observation = LocalAgentGuardianHostAdapter._linux_observation(
        pid,
        proc_root=proc_root,
    )
    host = LocalAgentGuardianHostAdapter(tmp_path)
    boot = _boot_event(tmp_path)["payload"]
    monkeypatch.setattr(guardian_adapter.sys, "platform", "linux")
    monkeypatch.setattr(host, "_linux_observation", lambda observed_pid: observation)

    assert host._process_observation(boot)[0] == expected_process


@pytest.mark.parametrize("proc_state", ["missing", "inaccessible"])
def test_linux_missing_or_inaccessible_procfs_is_unavailable(
    tmp_path, monkeypatch, proc_state,
):
    proc_root = tmp_path / "proc"
    if proc_state == "inaccessible":
        proc_root.mkdir()
        original_stat = Path.stat

        def denied_stat(path, *args, **kwargs):
            if path == proc_root:
                raise PermissionError("procfs denied")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", denied_stat)
    monkeypatch.setattr(
        guardian_adapter.os,
        "kill",
        lambda pid, signal_number: (_ for _ in ()).throw(
            AssertionError("signal-zero must not decide missing procfs")
        ),
    )

    observation = LocalAgentGuardianHostAdapter._linux_observation(
        1234,
        proc_root=proc_root,
    )

    assert observation == (None, "?", None, None)


def test_linux_pid_absence_requires_literal_signal_zero_esrch(tmp_path, monkeypatch):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    calls: list[tuple[int, int]] = []

    def absent(pid, signal_number):
        calls.append((pid, signal_number))
        raise OSError(errno.ESRCH, "gone")

    monkeypatch.setattr(guardian_adapter.os, "kill", absent)

    assert LocalAgentGuardianHostAdapter._linux_observation(
        1234,
        proc_root=proc_root,
    ) == (None, None, None, None)
    assert calls == [(1234, 0)]


@pytest.mark.parametrize(
    ("open_error", "query_result", "exit_code", "expected"),
    [
        (87, None, None, "absent"),
        (5, None, None, "unknown"),
        (123, None, None, "unknown"),
        (None, False, None, "unknown"),
        (None, True, 1, "absent"),
        (None, True, 259, "alive"),
    ],
)
def test_windows_process_liveness_is_narrow_tri_state(
    monkeypatch, open_error, query_result, exit_code, expected,
):
    from ctypes import wintypes
    from lingtai.adapters.windows import _win32

    class Kernel:
        def __init__(self):
            self.closed: list[int] = []

        def OpenProcess(self, access, inherit, pid):
            return 0 if open_error is not None else 42

        def GetExitCodeProcess(self, handle, code_pointer):
            if query_result and exit_code is not None:
                ctypes.cast(
                    code_pointer,
                    ctypes.POINTER(wintypes.DWORD),
                ).contents.value = exit_code
            return query_result

        def CloseHandle(self, handle):
            self.closed.append(handle)
            return True

    kernel = Kernel()
    monkeypatch.setattr(_win32.os, "name", "nt")
    monkeypatch.setattr(_win32, "_kernel32", lambda: kernel)
    monkeypatch.setattr(
        ctypes,
        "get_last_error",
        lambda: open_error or 0,
        raising=False,
    )

    assert _win32.process_liveness(1234) == expected
    assert kernel.closed == ([] if open_error is not None else [42])


@pytest.mark.parametrize("liveness", ["unknown", "absent"])
def test_windows_guardian_preserves_unknown_vs_absent(tmp_path, monkeypatch, liveness):
    from lingtai.adapters.windows import _win32

    host = LocalAgentGuardianHostAdapter(tmp_path)
    boot = _boot_event(tmp_path)["payload"]
    monkeypatch.setattr(guardian_adapter.sys, "platform", "win32")
    monkeypatch.setattr(_win32, "process_liveness", lambda pid: liveness)
    monkeypatch.setattr(
        _win32,
        "process_creation_identity",
        lambda pid: (_ for _ in ()).throw(
            AssertionError("identity queried without alive proof")
        ),
    )

    result = host._process_observation(boot)

    assert result[0] == ("absent" if liveness == "absent" else "unavailable")


@pytest.mark.parametrize(
    ("identity_after", "expected_process"),
    [("test:start:2", "identity_mismatch"), (None, "unavailable")],
)
def test_darwin_observation_rechecks_incarnation_after_process_evidence(
    tmp_path, monkeypatch, identity_after, expected_process,
):
    from lingtai.adapters.posix import process_identity as identity_module

    host = LocalAgentGuardianHostAdapter(tmp_path)
    boot = _boot_event(tmp_path)["payload"]
    identities = iter([boot["start_identity"], identity_after])
    monkeypatch.setattr(guardian_adapter.sys, "platform", "darwin")
    monkeypatch.setattr(
        identity_module,
        "_darwin_process_identity",
        lambda observed_pid: next(identities),
    )
    monkeypatch.setattr(
        LocalAgentGuardianHostAdapter,
        "_darwin_info",
        staticmethod(lambda observed_pid: SimpleNamespace(pbi_status=1)),
    )
    monkeypatch.setattr(
        LocalAgentGuardianHostAdapter,
        "_darwin_command",
        staticmethod(lambda observed_pid: f"python -m lingtai run {tmp_path.resolve()}"),
    )
    monkeypatch.setattr(
        guardian_adapter.ctypes,
        "CDLL",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("libproc unavailable")),
    )

    assert host._process_observation(boot)[0] == expected_process


def test_second_guardian_is_refused(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    first = LocalAgentGuardianHostAdapter(tmp_path)
    second = LocalAgentGuardianHostAdapter(tmp_path)
    from lingtai.adapters.workdir_lease import select_workdir_lease

    agent_lease = select_workdir_lease(tmp_path)
    agent_lease.acquire()
    assert first._observe_agent_lease() == "held"
    agent_lease.release()
    assert first._observe_agent_lease() == "free"
    first.acquire_guardian_lease()
    try:
        with pytest.raises(GuardianAlreadyRunning, match="guardian_already_running"):
            second.acquire_guardian_lease()
        stderr = io.StringIO()
        assert run_guardian_cli(tmp_path, once=True, host=second, stderr=stderr) == EXIT_ALREADY_RUNNING
        assert json.loads(stderr.getvalue())["error"]["code"] == "guardian_already_running"
    finally:
        first.release_guardian_lease()


class _FakeHost:
    def __init__(self, samples):
        self.samples = iter(samples)
        self.sleeps: list[float] = []
        self.released = False

    def acquire_guardian_lease(self):
        return None

    def release_guardian_lease(self):
        self.released = True

    def wall_time(self):
        return 1002.0

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def sample(self, boot_record):
        assert boot_record is not None
        return next(self.samples)


def test_cli_once_confirms_dead_records_json_and_has_no_actuation(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    ledger = _ledger(tmp_path)
    boot = ledger.append_event(_boot_event(tmp_path))
    absent = _sample(runtime_id=boot["payload"]["runtime_id"], process="absent", agent_lease="free", heartbeat="stale", heartbeat_age_seconds=500.0, observed_start_identity=None, command_match=None, executable_match=None)
    host = _FakeHost([absent, _sample(**{**absent.__dict__, "sampled_at": 1002.0, "heartbeat_age_seconds": 502.0})])
    stdout, stderr = io.StringIO(), io.StringIO()
    with (
        patch.object(os, "kill", side_effect=AssertionError("signal API called")),
        patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess launched")),
        patch("lingtai.cli.build_agent", side_effect=AssertionError("Agent constructed")),
        patch("lingtai.cli.build_llm_service", side_effect=AssertionError("provider constructed")),
    ):
        code = run_guardian_cli(tmp_path, once=True, ledger=ledger, host=host, stdout=stdout, stderr=stderr)
    payload = json.loads(stdout.getvalue())
    assert code == 0 and not stderr.getvalue()
    assert payload["verdict"] == "dead" and payload["recovery_plan"] == "would_launch"
    assert payload["shadow_only"] is True and payload["recorded"] is True
    assert host.sleeps == [2.0] and host.released
    assert ledger.read_snapshot().latest_guardian["event"] == "guardian_verdict"


def test_cli_once_unknown_is_still_audited_but_exits_nonzero(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    ledger = _ledger(tmp_path)
    boot = ledger.append_event(_boot_event(tmp_path))
    mismatch = _sample(runtime_id=boot["payload"]["runtime_id"], process="command_mismatch", command_match=False, heartbeat="stale", heartbeat_age_seconds=500.0)
    host = _FakeHost([mismatch, mismatch])
    stdout = io.StringIO()
    code = run_guardian_cli(tmp_path, once=True, ledger=ledger, host=host, stdout=stdout, stderr=io.StringIO())
    assert code == EXIT_AMBIGUOUS
    assert json.loads(stdout.getvalue())["recovery_plan"] == "observe_only"
    assert ledger.read_snapshot().latest_guardian is not None


def test_cli_corrupt_ledger_fails_closed_without_verdict(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_text("{broken\n", encoding="utf-8")
    stdout, stderr = io.StringIO(), io.StringIO()
    host = _FakeHost([])
    code = run_guardian_cli(tmp_path, once=True, host=host, stdout=stdout, stderr=stderr)
    assert code == EXIT_LEDGER_UNSAFE and stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "malformed_record"
    assert host.released


@pytest.mark.parametrize("corruption_kind", ["array_event", "huge_integer", "deep_json"])
def test_public_run_adversarial_json_is_mechanical_before_construction(
    tmp_path, monkeypatch, capsys, corruption_kind,
):
    from lingtai import cli

    body, expected_code = _adversarial_ledger_body(tmp_path, corruption_kind)
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_bytes(body)
    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr(
        cli,
        "build_agent",
        lambda data, working_dir: (_ for _ in ()).throw(
            AssertionError("Agent/providers/MCP constructed")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "run", str(tmp_path)])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 1
    assert json.loads(capsys.readouterr().err) == {"error": {"code": expected_code}}


@pytest.mark.parametrize("corruption_kind", ["array_event", "huge_integer", "deep_json"])
def test_guardian_cli_adversarial_ledger_json_is_mechanical(
    tmp_path, corruption_kind,
):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    body, expected_code = _adversarial_ledger_body(tmp_path, corruption_kind)
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_bytes(body)
    stdout, stderr = io.StringIO(), io.StringIO()
    host = _FakeHost([])

    code = run_guardian_cli(
        tmp_path,
        once=True,
        host=host,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == EXIT_LEDGER_UNSAFE
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue()) == {"error": {"code": expected_code}}
    assert host.released


def test_guardian_deep_initial_manifest_is_typed_unknown_evidence(tmp_path):
    (tmp_path / ".agent.json").write_text(
        "[" * 2000 + "0" + "]" * 2000,
        encoding="utf-8",
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_guardian_cli(
        tmp_path,
        once=True,
        stdout=stdout,
        stderr=stderr,
    )

    output = json.loads(stdout.getvalue())
    assert code == EXIT_AMBIGUOUS
    assert stderr.getvalue() == ""
    assert output["verdict"] == "unknown"
    assert output["evidence"]["agent_manifest"] == "malformed"
    assert "agent_manifest_malformed" in output["evidence"]["issues"]


def test_guardian_oversized_manifest_is_descriptor_bounded_unknown_evidence(
    tmp_path, monkeypatch,
):
    (tmp_path / ".agent.json").write_bytes(b"{" + b"x" * (1024 * 1024))
    monkeypatch.setattr(
        guardian_adapter.os,
        "fstat",
        lambda descriptor: SimpleNamespace(st_size=0),
    )

    observation = observe_guardian_manifest(tmp_path)
    host = LocalAgentGuardianHostAdapter(tmp_path)
    sample = host.sample(None)

    assert observation.kind.value == "malformed"
    assert sample.agent_manifest == "malformed"
    assert "agent_manifest_malformed" in sample.issues


def test_guardian_manifest_memory_error_is_typed_malformed(tmp_path, monkeypatch):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        guardian_adapter,
        "json",
        SimpleNamespace(
            loads=lambda raw: (_ for _ in ()).throw(MemoryError("allocation")),
        ),
    )

    assert observe_guardian_manifest(tmp_path).kind.value == "malformed"


def test_guardian_deep_agent_record_becomes_unknown_evidence(tmp_path, monkeypatch):
    from lingtai.kernel.session_stats import agent_record_path

    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    ledger = _ledger(tmp_path)
    boot = ledger.append_event(_boot_event(tmp_path))
    record_path = agent_record_path(tmp_path)
    record_path.parent.mkdir(exist_ok=True)
    record_path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    host = LocalAgentGuardianHostAdapter(tmp_path, sleeper=lambda seconds: None)
    monkeypatch.setattr(
        host,
        "_process_observation",
        lambda payload: (
            "unavailable",
            None,
            None,
            None,
            ("process_observation_unavailable",),
        ),
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_guardian_cli(
        tmp_path,
        once=True,
        ledger=ledger,
        host=host,
        stdout=stdout,
        stderr=stderr,
    )

    output = json.loads(stdout.getvalue())
    assert boot["event"] == "boot_registered"
    assert code == EXIT_AMBIGUOUS
    assert stderr.getvalue() == ""
    assert output["verdict"] == "unknown"
    assert output["evidence"]["heartbeat"] == "unreadable"
    assert "agent_record_unreadable" in output["evidence"]["issues"]


def test_agent_record_array_state_is_typed_unreadable_evidence(tmp_path):
    from lingtai.kernel.session_stats import agent_record_path

    record_path = agent_record_path(tmp_path)
    record_path.parent.mkdir(parents=True)
    record_path.write_text(
        json.dumps(
            {
                "schema": "lingtai.agent_record/v1",
                "schema_version": 1,
                "session": {"state": []},
                "health": {"heartbeat_at": 1000.0},
            }
        ),
        encoding="utf-8",
    )
    host = LocalAgentGuardianHostAdapter(tmp_path)

    heartbeat, age, state, issues = host._heartbeat_observation(1001.0)

    assert (heartbeat, age, state) == ("unreadable", None, None)
    assert issues == ("agent_record_state_invalid",)


def test_guardian_release_failure_overrides_pending_once_output(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    ledger = _ledger(tmp_path)
    boot = ledger.append_event(_boot_event(tmp_path))

    class ReleaseFailingHost(_FakeHost):
        def release_guardian_lease(self):
            raise GuardianLeaseUnavailable("guardian_lease_unavailable")

    sample = _sample(runtime_id=boot["payload"]["runtime_id"])
    host = ReleaseFailingHost([sample])
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_guardian_cli(
        tmp_path,
        once=True,
        ledger=ledger,
        host=host,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == EXIT_LEDGER_UNSAFE
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue()) == {
        "error": {"code": "guardian_lease_unavailable"}
    }


@pytest.mark.skipif(shutil.which("python3.14") is None, reason="Python 3.14 unavailable")
def test_python314_guardian_compile_import_and_help_are_warning_clean():
    root = Path(__file__).parents[1]
    python = shutil.which("python3.14")
    environment = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    compile_check = subprocess.run(
        [
            str(python),
            "-Werror",
            "-c",
            (
                "from pathlib import Path; "
                "p=Path('src/lingtai/cli_guardian.py'); "
                "compile(p.read_text(encoding='utf-8'), str(p), 'exec')"
            ),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compile_check.returncode == 0, compile_check.stderr

    help_check = subprocess.run(
        [str(python), "-Werror", "-m", "lingtai", "guardian", "--help"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_check.returncode == 0, help_check.stderr
    assert "SyntaxWarning" not in help_check.stderr
    assert "--agent-dir" in help_check.stdout


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda row, root: row["payload"].__setitem__("pid", int("9" * 101)), "invalid_pid"),
        (lambda row, root: row["payload"].__setitem__("working_dir", f"{root}\0bad"), "invalid_working_dir"),
        (
            lambda row, root: row["payload"]["command"].__setitem__(
                "agent_dir", str((root / "different").resolve())
            ),
            "boot_agent_dir_mismatch",
        ),
    ],
)
def test_cli_malformed_v1_boot_evidence_is_mechanical(
    tmp_path, mutation, expected_code,
):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    row = deepcopy(_boot_event(tmp_path))
    mutation(row, tmp_path)
    path = tmp_path / "logs" / "agent_lifecycle.jsonl"
    path.parent.mkdir()
    path.write_bytes((stable_json(row) + "\n").encode("utf-8"))
    stdout, stderr = io.StringIO(), io.StringIO()
    host = _FakeHost([])

    code = run_guardian_cli(
        tmp_path,
        once=True,
        host=host,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == EXIT_LEDGER_UNSAFE
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == expected_code
    assert host.released


def test_cli_guardian_lease_not_a_directory_error_is_mechanical(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    (tmp_path / "system").write_text("not a directory", encoding="utf-8")
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_guardian_cli(
        tmp_path,
        once=True,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == EXIT_LEDGER_UNSAFE
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "guardian_lease_unavailable"


def test_cli_ledger_parent_not_a_directory_error_is_mechanical(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_guardian_cli(
        tmp_path,
        once=True,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == EXIT_LEDGER_UNSAFE
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "ledger_io_error"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_cli_guardian_lease_permission_error_is_mechanical(tmp_path):
    (tmp_path / ".agent.json").write_text("{}", encoding="utf-8")
    system = tmp_path / "system"
    system.mkdir()
    system.chmod(0)
    stderr = io.StringIO()
    try:
        code = run_guardian_cli(tmp_path, once=True, stderr=stderr)
    finally:
        system.chmod(0o700)

    assert code == EXIT_LEDGER_UNSAFE
    assert json.loads(stderr.getvalue())["error"]["code"] == "guardian_lease_unavailable"


def test_cli_rejects_non_agent_directory_without_mutation(tmp_path):
    stderr = io.StringIO()
    assert run_guardian_cli(tmp_path, once=True, stderr=stderr) == 2
    assert json.loads(stderr.getvalue())["error"]["code"] == "agent_dir_not_agent"
    assert list(tmp_path.iterdir()) == []


def test_loop_checkpoint_coalesces_unchanged_policy():
    recorded = datetime(2026, 8, 29, tzinfo=timezone.utc).timestamp()
    latest = {
        "recorded_at": "2026-08-29T00:00:00.000Z",
        "payload": {"policy_fingerprint": "same"},
    }
    assert not _checkpoint_due(latest, policy_fingerprint="same", wall_now=recorded + 86399)
    assert _checkpoint_due(latest, policy_fingerprint="same", wall_now=recorded + 86400)
    assert _checkpoint_due(latest, policy_fingerprint="changed", wall_now=recorded)
    assert _checkpoint_due(latest, policy_fingerprint="same", wall_now=recorded - 1)


def test_cli_run_refuses_active_intent_before_agent_construction_and_preserves_marker(
    tmp_path, monkeypatch, capsys,
):
    from lingtai import cli
    from lingtai.kernel.session_stats import agent_record_path

    ledger = _ledger(tmp_path)
    intent = ledger.request_suspend(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="maintenance",
    )
    marker = tmp_path / ".suspend"
    marker.write_text("legacy-marker", encoding="utf-8")
    before = ledger.path.read_bytes()

    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr(
        cli,
        "build_agent",
        lambda data, working_dir: (_ for _ in ()).throw(
            AssertionError("Agent/providers/MCP constructed")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "run", str(tmp_path)])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 1
    assert capsys.readouterr().err == '{"error":{"code":"explicit_suspend_active"}}\n'
    assert ledger.path.read_bytes() == before
    assert ledger.read_snapshot().active_intent_id == intent
    assert marker.read_text(encoding="utf-8") == "legacy-marker"
    assert not agent_record_path(tmp_path).exists()


def test_cli_run_refuses_legacy_suspend_without_ledger_and_preserves_marker(
    tmp_path, monkeypatch, capsys,
):
    from lingtai import cli

    marker = tmp_path / ".suspend"
    marker.write_text("legacy-only", encoding="utf-8")
    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr(
        cli,
        "build_agent",
        lambda data, working_dir: (_ for _ in ()).throw(
            AssertionError("Agent/providers/MCP constructed")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "run", str(tmp_path)])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 1
    assert capsys.readouterr().err == '{"error":{"code":"explicit_suspend_active"}}\n'
    assert marker.read_text(encoding="utf-8") == "legacy-only"
    assert not (tmp_path / "logs").exists()


def test_cli_run_rechecks_legacy_suspend_after_construction(tmp_path, monkeypatch):
    from lingtai import cli

    marker = tmp_path / ".suspend"

    class FakeAgent:
        def __init__(self):
            self.stop_calls: list[float] = []

        def stop(self, timeout=10.0):
            self.stop_calls.append(timeout)

    agent = FakeAgent()

    def suspend_during_cleanup(working_dir, **kwargs):
        assert kwargs == {"preserve_suspend": True}
        marker.write_text("arrived-during-construction", encoding="utf-8")

    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr("lingtai.kernel.logging.setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(cli, "load_init", lambda working_dir: {})
    monkeypatch.setattr("lingtai.venv_resolve.resolve_venv", lambda data: tmp_path / "venv")
    monkeypatch.setattr(cli, "build_agent", lambda data, working_dir: agent)
    monkeypatch.setattr(cli, "_clean_signal_files", suspend_during_cleanup)
    monkeypatch.setattr(
        cli,
        "_register_agent_boot",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("boot registered after legacy suspend")
        ),
    )

    with pytest.raises(LifecycleLedgerError, match="explicit_suspend_active"):
        cli.run(tmp_path)

    assert agent.stop_calls == [10.0]
    assert marker.read_text(encoding="utf-8") == "arrived-during-construction"
    assert FilesystemLifecycleLedgerAdapter(tmp_path).read_snapshot().latest_boot is None


def test_cli_run_ledger_setup_failure_is_structured_before_agent_construction(
    tmp_path, monkeypatch, capsys,
):
    from lingtai import cli

    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr(
        cli,
        "build_agent",
        lambda data, working_dir: (_ for _ in ()).throw(
            AssertionError("Agent/providers/MCP constructed")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "run", str(tmp_path)])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 1
    assert capsys.readouterr().err == '{"error":{"code":"ledger_io_error"}}\n'


def test_cli_run_real_suspend_wins_after_construction_and_stops_agent(
    tmp_path, monkeypatch,
):
    from lingtai import cli
    from lingtai.adapters.workdir_lease import select_workdir_lease

    class FakeAgent:
        def __init__(self):
            self.started = False
            self.stop_calls: list[float] = []
            self._lease = select_workdir_lease(tmp_path)
            self._lease.acquire()
            self._lease_acquired = True

        def start(self):
            self.started = True

        def stop(self, timeout=10.0):
            self.stop_calls.append(timeout)
            if self._lease_acquired:
                self._lease.release()
                self._lease_acquired = False

    agent = FakeAgent()
    cleaned = threading.Event()
    resume_boot = threading.Event()
    original_clean = cli._clean_signal_files
    errors: list[BaseException] = []

    def pause_after_cleanup(working_dir, **kwargs):
        original_clean(working_dir, **kwargs)
        cleaned.set()
        assert resume_boot.wait(5)

    def run_agent():
        try:
            cli.run(tmp_path)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr("lingtai.kernel.logging.setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(cli, "load_init", lambda working_dir: {})
    monkeypatch.setattr("lingtai.venv_resolve.resolve_venv", lambda data: tmp_path / "venv")
    monkeypatch.setattr(cli, "build_agent", lambda data, working_dir: agent)
    monkeypatch.setattr(
        "lingtai.adapters.posix.process_identity.process_identity",
        lambda pid: "test:start:boot",
    )
    monkeypatch.setattr(cli, "_clean_signal_files", pause_after_cleanup)

    thread = threading.Thread(target=run_agent)
    thread.start()
    assert cleaned.wait(5)
    ledger = FilesystemLifecycleLedgerAdapter(tmp_path)
    intent = ledger.request_suspend(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="explicit_suspend",
    )
    marker = tmp_path / ".suspend"
    marker.write_text("system-suspend", encoding="utf-8")
    resume_boot.set()
    thread.join(5)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], LifecycleLedgerError)
    assert errors[0].code == "explicit_suspend_active"
    assert agent.stop_calls == [10.0]
    assert agent.started is False
    snapshot = ledger.read_snapshot()
    assert [row["event"] for row in snapshot.records] == ["suspend_requested"]
    assert snapshot.active_intent_id == intent
    assert snapshot.latest_boot is None
    assert marker.read_text(encoding="utf-8") == "system-suspend"


def test_cli_run_boot_append_wins_before_real_suspend_and_timeline_stays_valid(
    tmp_path, monkeypatch,
):
    from lingtai import cli
    from lingtai.adapters.workdir_lease import select_workdir_lease

    registered = threading.Event()
    resume_start = threading.Event()
    errors: list[BaseException] = []

    class FakeAgent:
        def __init__(self):
            self.started = False
            self.stop_calls: list[float] = []
            self._asleep = threading.Event()
            self._shutdown = threading.Event()
            self._shutdown.set()
            self._state = None
            self._venv_path = None
            self._lease = select_workdir_lease(tmp_path)
            self._lease.acquire()
            self._lease_acquired = True

        def start(self):
            self.started = True

        def stop(self, timeout=10.0):
            self.stop_calls.append(timeout)
            if self._lease_acquired:
                self._lease.release()
                self._lease_acquired = False

    agent = FakeAgent()

    def pause_after_registration(working_dir, value):
        assert value is agent
        registered.set()
        assert resume_start.wait(5)

    def run_agent():
        try:
            cli.run(tmp_path)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr("lingtai.kernel.logging.setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(cli, "load_init", lambda working_dir: {})
    monkeypatch.setattr("lingtai.venv_resolve.resolve_venv", lambda data: tmp_path / "venv")
    monkeypatch.setattr(cli, "build_agent", lambda data, working_dir: agent)
    monkeypatch.setattr(
        "lingtai.adapters.posix.process_identity.process_identity",
        lambda pid: "test:start:boot",
    )
    monkeypatch.setattr(cli, "_install_signal_handlers", pause_after_registration)

    thread = threading.Thread(target=run_agent)
    thread.start()
    assert registered.wait(5)
    ledger = FilesystemLifecycleLedgerAdapter(tmp_path)
    intent = ledger.request_suspend(
        agent_address=tmp_path.name,
        actor_id="operator",
        reason="explicit_suspend",
    )
    marker = tmp_path / ".suspend"
    marker.write_text("system-suspend", encoding="utf-8")
    resume_start.set()
    thread.join(5)

    assert not thread.is_alive()
    assert errors == []
    assert agent.started is True
    assert agent.stop_calls == [10.0]
    snapshot = ledger.read_snapshot()
    assert [row["event"] for row in snapshot.records] == [
        "boot_registered",
        "suspend_requested",
    ]
    assert snapshot.active_intent_id == intent
    assert snapshot.latest_boot is not None
    assert marker.read_text(encoding="utf-8") == "system-suspend"


def test_cli_clean_boot_cleans_stale_sleep_and_refresh_before_registration(
    tmp_path, monkeypatch,
):
    from lingtai import cli

    signals = [tmp_path / name for name in (".sleep", ".refresh")]
    for path in signals:
        path.write_text("stale", encoding="utf-8")
    taken = tmp_path / ".refresh.taken"
    taken.write_text("refresh", encoding="utf-8")
    calls: list[str] = []

    class FakeAgent:
        def __init__(self):
            self._asleep = threading.Event()
            self._shutdown = threading.Event()
            self._shutdown.set()
            self._state = None
            self._venv_path = None
            self._config = SimpleNamespace(language="en")

        def start(self):
            assert all(not path.exists() for path in signals)
            calls.append("start")

        def send(self, *args, **kwargs):
            calls.append("refresh_send")

        def stop(self, timeout=10.0):
            calls.append("stop")

    agent = FakeAgent()

    def register(fake_agent, working_dir, *, ledger):
        assert fake_agent is agent
        assert all(not path.exists() for path in signals)
        assert ledger.read_snapshot().active_intent_id is None
        calls.append("register")
        return {}

    monkeypatch.setattr(cli, "_check_duplicate_process", lambda working_dir: None)
    monkeypatch.setattr("lingtai.kernel.logging.setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(cli, "load_init", lambda working_dir: {})
    monkeypatch.setattr("lingtai.venv_resolve.resolve_venv", lambda data: tmp_path / "venv")
    monkeypatch.setattr(cli, "build_agent", lambda data, working_dir: agent)
    monkeypatch.setattr(cli, "_register_agent_boot", register)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda working_dir, value: None)

    cli.run(tmp_path)

    assert calls == ["register", "start", "refresh_send", "stop"]
    assert all(not path.exists() for path in signals)
    assert not taken.exists()


def test_cli_help_and_boot_registration_seam(tmp_path, capsys):
    import argparse
    from lingtai.cli import _register_agent_boot
    from lingtai.cli_guardian import add_guardian_parser

    parser = argparse.ArgumentParser(prog="lingtai-agent")
    sub = parser.add_subparsers(dest="command")
    add_guardian_parser(sub)
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(["guardian", "--help"])
    assert exit_info.value.code == 0
    help_output = capsys.readouterr().out
    assert "--agent-dir" in help_output and "--once" in help_output

    calls = []
    fake_ledger = type("Ledger", (), {"register_boot": lambda self, **kwargs: calls.append(kwargs) or kwargs})()
    fake_agent = type("Agent", (), {"agent_name": "display-name", "_workdir_lease_acquired": True})()
    _register_agent_boot(fake_agent, tmp_path, ledger=fake_ledger)
    assert calls == [{"agent_address": tmp_path.name, "working_dir": str(tmp_path)}]

    root = Path(__file__).parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")
    assert '"lingtai.kernel.agent_guardian"' in pyproject
    assert "recursive-include src/lingtai/kernel/agent_guardian *.md" in manifest
    assert (root / "src/lingtai/kernel/agent_guardian/MANUAL.md").is_file()
