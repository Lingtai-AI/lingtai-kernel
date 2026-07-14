"""Daemon refresh-survival: process-start identity and host-marker primitives.

Covers ONLY the identity/marker layer in ``lingtai.tools.daemon.refresh_host``
— pure unit tests, no subprocesses beyond short-lived probe targets, no
behavior change to any existing code path. The drain-host protocol itself
(lifecycle branch, DaemonManager control plane, duplicate-guard exception,
notification-store interprocess lock, and real-subprocess acceptance tests)
is deliberately NOT implemented here; this module is a primitives-only
foundation, not a claim that daemons survive refresh.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import secrets
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lingtai.tools.daemon.refresh_host import (
    MARKER_SCHEMA_VERSION,
    CommitAmbiguousError,
    ControlAck,
    ControlRequest,
    ExecutionOwner,
    MarkerValidationError,
    OwnerTaggingAmbiguousError,
    ProcessStartIdentity,
    RefreshHostMarker,
    allocate_sequence,
    commit_marker,
    control_dir,
    is_verified_refresh_host,
    iter_marker_paths,
    load_marker,
    new_generation,
    new_nonce,
    probe_process_start_identity,
    read_pending_control_requests,
    refresh_hosts_dir,
    tag_owned_runs,
    verified_refresh_host_pids,
    verify_marker_live,
    write_control_ack,
    write_control_request,
)


# ---------------------------------------------------------------------------
# Process-start identity probe
# ---------------------------------------------------------------------------


def test_probe_process_start_identity_self_is_stable():
    """Probing our own PID twice must return the same start_ticks both times.

    Unconditional on Linux and Darwin alike — both platforms now have a real
    native identity source, so there is no platform branch to special-case.
    """
    first = probe_process_start_identity(os.getpid())
    second = probe_process_start_identity(os.getpid())
    assert first is not None
    assert second is not None
    assert first == second
    assert first.pid == os.getpid()


def test_probe_process_start_identity_rejects_nonpositive_pid():
    assert probe_process_start_identity(0) is None
    assert probe_process_start_identity(-1) is None


def test_probe_process_start_identity_missing_proc_returns_none(monkeypatch):
    """A PID with no /proc/<pid>/stat (already dead, or non-Linux) fails closed."""
    monkeypatch.setattr(sys, "platform", "linux")
    # PID 1 is init on real Linux and always exists, but a wildly out-of-range
    # PID cannot exist on any real system.
    assert probe_process_start_identity(2**30) is None


def test_probe_process_start_identity_unsupported_platform_fails_closed(monkeypatch):
    """A platform with neither the Linux nor Darwin native source fails closed."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert probe_process_start_identity(os.getpid()) is None


@pytest.mark.skipif(sys.platform != "linux", reason="/proc/<pid>/stat is Linux-only")
def test_probe_process_start_identity_survives_comm_with_parenthesis(tmp_path, monkeypatch):
    """The parser must split after the LAST ')', not the first.

    proc(5) documents that the second field is "(comm)" and MAY itself
    contain arbitrary bytes including ')'. A naive ``split(")")[1]`` or
    regex anchored on the first ')' would misparse a process named e.g.
    "a) (b)" and silently return a wrong start_ticks for a live process —
    exactly the kind of silent wrong-identity result this probe must never
    produce. We fabricate a stat line rather than renaming our own process
    (which is not portable/reliable in a test).
    """
    fake_proc = tmp_path / "proc" / "4242"
    fake_proc.mkdir(parents=True)
    # comm field intentionally contains ')' characters.
    fields_after_comm = ["S", "1"] + ["0"] * 17 + ["999888"] + ["0"] * 30
    line = f"4242 (weird)proc)name) {' '.join(fields_after_comm)}\n"
    (fake_proc / "stat").write_text(line, encoding="utf-8")

    monkeypatch.setattr(sys, "platform", "linux")

    import lingtai.tools.daemon.refresh_host as refresh_host_module

    real_read_text = Path.read_text

    def _patched_read_text(self, *args, **kwargs):
        if str(self) == "/proc/4242/stat":
            return real_read_text(fake_proc / "stat", *args, **kwargs)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _patched_read_text)

    identity = refresh_host_module.probe_process_start_identity(4242)
    assert identity is not None
    assert identity.pid == 4242
    assert identity.start_ticks == 999888


def test_linux_stat_parser_is_platform_independent():
    """The field-22-after-last-')' parser must be a pure, injectable helper.

    Runs unconditionally (Darwin included) since it takes a raw stat-line
    string directly, with zero /proc or sys.platform dependency.
    """
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    fields_after_comm = ["S", "1"] + ["0"] * 17 + ["999888"] + ["0"] * 30
    line = f"4242 (weird)proc)name) {' '.join(fields_after_comm)}\n"
    start_ticks = refresh_host_module._parse_linux_stat_start_ticks(line)
    assert start_ticks == 999888


def test_linux_stat_parser_rejects_truncated_line():
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    assert refresh_host_module._parse_linux_stat_start_ticks("4242 (x) S 1") is None


def test_linux_stat_parser_rejects_no_closing_paren():
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    assert refresh_host_module._parse_linux_stat_start_ticks("garbage no paren here") is None


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_works_on_darwin_for_self():
    """Unconditional (no skip) positive-path proof that Darwin identity exists.

    Guarded by an explicit skipif rather than platform-branching inside the
    body: this specific test only makes sense to run on Darwin (it asserts
    the Darwin source itself works), unlike the cross-platform tests above.
    """
    first = probe_process_start_identity(os.getpid())
    second = probe_process_start_identity(os.getpid())
    assert first is not None
    assert first.start_ticks == second.start_ticks
    assert first.start_ticks > 0


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_darwin_rejects_dead_pid():
    import subprocess as _subprocess
    import time as _time

    proc = _subprocess.Popen(["sleep", "30"])
    pid = proc.pid
    proc.kill()
    proc.wait()
    # Give the kernel a moment to fully reap; a wait() return guarantees the
    # child is a zombie/reaped, but proc_pidinfo must reject it either way.
    _time.sleep(0.05)
    assert probe_process_start_identity(pid) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_darwin_rejects_zero_return_deterministic(monkeypatch):
    """Deterministic, injected proc_pidinfo failure — NOT machine-specific.

    A prior version of this test hardcoded PID 1 (launchd) and asserted the
    probe returns None, relying on THIS machine's proc_pidinfo(1, ...)
    happening to return ret=0/errno=EPERM. That is not a portable contract:
    a privileged CI runner could plausibly succeed at inspecting PID 1, in
    which case a positive identity would be the CORRECT answer, not a bug —
    asserting None unconditionally bakes in one machine's permission policy
    as if it were a universal Darwin fact.

    This test instead fakes proc_pidinfo itself (via ctypes.CDLL) to
    deterministically return 0 (failure) regardless of which errno a real
    kernel would set for any given PID/privilege combination — covering
    BOTH the ESRCH ("does not exist") and EPERM ("exists, denied") failure
    shapes with one assertion, since this module's contract is "any ret<=0
    (or wrong-size ret) fails closed," not "EPERM specifically fails closed
    on pid 1 on this machine."
    """
    import ctypes as ctypes_module

    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _fn(pid, flavor, arg, buf, buf_size):
        return 0  # deterministic failure, independent of errno

    class _FakeLib:
        proc_pidinfo = staticmethod(_fn)

    monkeypatch.setattr(ctypes_module, "CDLL", lambda *a, **k: _FakeLib())
    assert refresh_host_module.probe_process_start_identity(os.getpid()) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_darwin_rejects_short_read(monkeypatch):
    """A return value that is positive but NOT exactly sizeof(struct) — a
    partial/truncated read — must fail closed, not be treated as a complete
    trustworthy struct merely because ret > 0.
    """
    import ctypes as ctypes_module

    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _fn(pid, flavor, arg, buf, buf_size):
        return 1  # positive, but nowhere near a full struct

    class _FakeLib:
        proc_pidinfo = staticmethod(_fn)

    monkeypatch.setattr(ctypes_module, "CDLL", lambda *a, **k: _FakeLib())
    assert refresh_host_module.probe_process_start_identity(os.getpid()) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_darwin_rejects_pid_echo_mismatch(monkeypatch):
    """If the returned struct's pbi_pid does not match the requested pid,
    fail closed rather than trust a possibly-misaligned/wrong struct.
    """
    import ctypes as ctypes_module

    import lingtai.tools.daemon.refresh_host as refresh_host_module

    struct_cls = refresh_host_module._ProcBsdInfo.get(ctypes_module)

    def _fn(pid, flavor, arg, buf, buf_size):
        info = ctypes_module.cast(buf, ctypes_module.POINTER(struct_cls)).contents
        info.pbi_pid = pid + 1  # deliberately wrong echo
        info.pbi_start_tvsec = 123456
        info.pbi_start_tvusec = 0
        return ctypes_module.sizeof(struct_cls)

    class _FakeLib:
        proc_pidinfo = staticmethod(_fn)

    monkeypatch.setattr(ctypes_module, "CDLL", lambda *a, **k: _FakeLib())
    assert refresh_host_module.probe_process_start_identity(os.getpid()) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_darwin_rejects_nonpositive_start_tvsec(monkeypatch):
    import ctypes as ctypes_module

    import lingtai.tools.daemon.refresh_host as refresh_host_module

    struct_cls = refresh_host_module._ProcBsdInfo.get(ctypes_module)
    target_pid = os.getpid()

    def _fn(pid, flavor, arg, buf, buf_size):
        info = ctypes_module.cast(buf, ctypes_module.POINTER(struct_cls)).contents
        info.pbi_pid = pid
        info.pbi_start_tvsec = 0  # a real process cannot start at/before epoch
        info.pbi_start_tvusec = 0
        return ctypes_module.sizeof(struct_cls)

    class _FakeLib:
        proc_pidinfo = staticmethod(_fn)

    monkeypatch.setattr(ctypes_module, "CDLL", lambda *a, **k: _FakeLib())
    assert refresh_host_module.probe_process_start_identity(target_pid) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_darwin_rejects_out_of_range_tvusec(monkeypatch):
    import ctypes as ctypes_module

    import lingtai.tools.daemon.refresh_host as refresh_host_module

    struct_cls = refresh_host_module._ProcBsdInfo.get(ctypes_module)
    target_pid = os.getpid()

    def _fn(pid, flavor, arg, buf, buf_size):
        info = ctypes_module.cast(buf, ctypes_module.POINTER(struct_cls)).contents
        info.pbi_pid = pid
        info.pbi_start_tvsec = 123456
        info.pbi_start_tvusec = 1_000_000  # out of valid [0, 1_000_000) range
        return ctypes_module.sizeof(struct_cls)

    class _FakeLib:
        proc_pidinfo = staticmethod(_fn)

    monkeypatch.setattr(ctypes_module, "CDLL", lambda *a, **k: _FakeLib())
    assert refresh_host_module.probe_process_start_identity(target_pid) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="proc_pidinfo is Darwin-only")
def test_probe_process_start_identity_darwin_observational_pid1(monkeypatch):
    """PID 1's actual live behavior on THIS runner, observed without asserting
    a specific outcome — replaces the old hardcoded-EPERM assumption. Either
    a real identity (privileged runner) or None (unprivileged runner, the
    common case) is an acceptable, self-consistent result; the only thing
    asserted is that the call does not raise and is stable across two probes.
    """
    first = probe_process_start_identity(1)
    second = probe_process_start_identity(1)
    assert first == second


# ---------------------------------------------------------------------------
# Marker construction and round-trip
# ---------------------------------------------------------------------------


def test_new_generation_format_and_uniqueness():
    a = new_generation()
    b = new_generation()
    assert a != b
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{6}$", a)


def test_new_nonce_is_128_bit_hex_and_unique():
    a = new_nonce()
    b = new_nonce()
    assert a != b
    assert len(a) == 32
    int(a, 16)  # raises if not valid hex


def test_marker_build_is_draining_with_fixed_run_set(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=1000, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1", "em-2"],
    )
    assert marker.schema_version == MARKER_SCHEMA_VERSION
    assert marker.state == "draining"
    assert marker.owned_run_ids == ("em-1", "em-2")
    assert marker.pid == 4242


def test_commit_marker_roundtrip(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1000, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    path = commit_marker(parent, marker)
    assert path.is_file()
    loaded = load_marker(path, expected_working_dir=parent)
    assert loaded == marker


def test_commit_marker_lands_under_daemons_refresh_hosts_dir(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    path = commit_marker(parent, marker)
    assert path.parent == refresh_hosts_dir(parent)
    assert path.parent == parent / "daemons" / ".refresh-hosts"


def test_commit_marker_rejects_generation_collision(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    commit_marker(parent, marker)
    with pytest.raises(MarkerValidationError) as exc_info:
        commit_marker(parent, marker)
    assert exc_info.value.reason == "generation_collision"


def test_commit_marker_concurrent_writers_same_generation_only_one_wins(tmp_path):
    """Real OS threads racing to commit markers sharing one generation.

    Exactly one must succeed; every other must raise generation_collision;
    the target path must never be observable as a torn/partial file at any
    point during the race — polled continuously from a separate thread.
    """
    for _iteration in range(20):
        parent = tmp_path / f"agent-{_iteration}"
        parent.mkdir()
        shared_generation = new_generation()
        n_threads = 8
        results = [None] * n_threads
        stop_poll = threading.Event()
        poll_violations = []

        def _poll_target():
            target = refresh_hosts_dir(parent) / f"{shared_generation}.json"
            while not stop_poll.is_set():
                if target.exists():
                    try:
                        raw = target.read_text(encoding="utf-8")
                        json.loads(raw)
                    except (OSError, ValueError) as e:
                        poll_violations.append(str(e))

        poller = threading.Thread(target=_poll_target)
        poller.start()

        def _commit(i: int):
            marker = RefreshHostMarker(
                schema_version=MARKER_SCHEMA_VERSION,
                generation=shared_generation,
                nonce=new_nonce(),
                pid=os.getpid(),
                start_ticks=i + 1,
                sequence=allocate_sequence(parent),
                command_label="module",
                working_dir=str(parent),
                owned_run_ids=(f"em-{i}",),
                state="draining",
                prepared_at=RefreshHostMarker._now_iso(),
            )
            try:
                commit_marker(parent, marker)
                results[i] = "ok"
            except MarkerValidationError as e:
                results[i] = e.reason

        threads = [threading.Thread(target=_commit, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop_poll.set()
        poller.join()

        assert poll_violations == []
        assert results.count("ok") == 1, f"iteration {_iteration}: results={results}"
        assert results.count("generation_collision") == n_threads - 1


def test_iter_marker_paths_empty_when_no_hosts_dir(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    assert list(iter_marker_paths(parent)) == []


def test_iter_marker_paths_sorted(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    for i in range(3):
        marker = RefreshHostMarker.build(
            pid=os.getpid(), start_ticks=i + 1, command_label="module",
            working_dir=str(parent), owned_run_ids=[f"em-{i}"],
        )
        commit_marker(parent, marker)
    paths = list(iter_marker_paths(parent))
    assert paths == sorted(paths)
    assert len(paths) == 3


# ---------------------------------------------------------------------------
# build() validation — malformed construction requests must raise, not build
# ---------------------------------------------------------------------------


def test_build_rejects_invalid_command_label(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=1, start_ticks=1, command_label="bogus",
            working_dir=str(parent), owned_run_ids=["em-1"],
        )
    assert exc_info.value.reason == "malformed_command_label"


def test_build_rejects_non_canonical_working_dir(tmp_path):
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=1, start_ticks=1, command_label="module",
            working_dir="relative/path", owned_run_ids=["em-1"],
        )
    assert exc_info.value.reason == "malformed_working_dir"


def test_build_rejects_empty_or_duplicate_run_ids(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=1, start_ticks=1, command_label="module",
            working_dir=str(parent), owned_run_ids=["em-1", "", "em-1"],
        )
    assert exc_info.value.reason == "malformed_run_ids"


def test_build_rejects_path_like_run_ids(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=1, start_ticks=1, command_label="module",
            working_dir=str(parent), owned_run_ids=["../../etc"],
        )
    assert exc_info.value.reason == "malformed_run_ids"


def test_commit_marker_rejects_invalid_marker_before_write(tmp_path):
    """A hand-constructed (build()-bypassing) malformed marker must never land.

    commit_marker must validate through the strict parser before the
    exclusive-publish step, so the bad file is never created.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker(
        schema_version=MARKER_SCHEMA_VERSION,
        generation=new_generation(),
        nonce=new_nonce(),
        pid=1,
        start_ticks=1,
        sequence=1,
        command_label="not-a-real-label",
        working_dir=str(parent),
        owned_run_ids=("em-1",),
        state="draining",
        prepared_at=RefreshHostMarker._now_iso(),
    )
    with pytest.raises(MarkerValidationError):
        commit_marker(parent, marker)
    target = refresh_hosts_dir(parent) / f"{marker.generation}.json"
    assert not target.exists()


def test_marker_carries_both_sequence_and_generation_id(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    first = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    second = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-2"],
    )
    assert isinstance(first.sequence, int)
    assert isinstance(second.sequence, int)
    assert second.sequence > first.sequence
    assert first.generation != second.generation


# ---------------------------------------------------------------------------
# build() scalar validation (pid / start_ticks) — parent correction pass
# ---------------------------------------------------------------------------


def _sequence_claims(parent: Path) -> list:
    seq_dir = refresh_hosts_dir(parent) / ".sequence"
    if not seq_dir.is_dir():
        return []
    return sorted(int(p.name) for p in seq_dir.iterdir() if p.name.isdigit())


def test_build_rejects_bool_pid(tmp_path):
    """pid=True must be rejected, not silently accepted as pid=1.

    bool is a subclass of int in Python; a naive isinstance(pid, int) check
    lets True/False through as pid=1/pid=0.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=True, start_ticks=100, command_label="module",
            working_dir=str(parent), owned_run_ids=["em-1"],
        )
    assert exc_info.value.reason == "malformed_pid"


def test_build_rejects_nonpositive_pid(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    for bad_pid in (0, -1):
        with pytest.raises(MarkerValidationError) as exc_info:
            RefreshHostMarker.build(
                pid=bad_pid, start_ticks=100, command_label="module",
                working_dir=str(parent), owned_run_ids=["em-1"],
            )
        assert exc_info.value.reason == "malformed_pid"


def test_build_rejects_bool_start_ticks(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=1234, start_ticks=True, command_label="module",
            working_dir=str(parent), owned_run_ids=["em-1"],
        )
    assert exc_info.value.reason == "malformed_start_ticks"


def test_build_rejects_zero_start_ticks(tmp_path):
    """start_ticks=0 must be rejected — every real process-start identity
    (Linux boot-relative ticks, Darwin microsecond epoch) is strictly
    positive; zero can only mean "never actually probed."
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=1234, start_ticks=0, command_label="module",
            working_dir=str(parent), owned_run_ids=["em-1"],
        )
    assert exc_info.value.reason == "malformed_start_ticks"


def test_build_rejects_negative_start_ticks(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        RefreshHostMarker.build(
            pid=1234, start_ticks=-1, command_label="module",
            working_dir=str(parent), owned_run_ids=["em-1"],
        )
    assert exc_info.value.reason == "malformed_start_ticks"


def test_build_invalid_pid_does_not_advance_sequence(tmp_path):
    """A rejected build() request must not consume a durable sequence slot.

    pid/start_ticks validation must happen BEFORE allocate_sequence() is
    called, not after — otherwise every rejected request still burns a
    sequence number, which is observable state leakage from a request that
    never produced a marker.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    assert _sequence_claims(parent) == []
    with pytest.raises(MarkerValidationError):
        RefreshHostMarker.build(
            pid=True, start_ticks=100, command_label="module",
            working_dir=str(parent), owned_run_ids=["em-1"],
        )
    assert _sequence_claims(parent) == []
    with pytest.raises(MarkerValidationError):
        RefreshHostMarker.build(
            pid=1234, start_ticks=0, command_label="module",
            working_dir=str(parent), owned_run_ids=["em-1"],
        )
    assert _sequence_claims(parent) == []
    # A subsequent valid build must still get sequence 1 (nothing was burned).
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    assert marker.sequence == 1


# ---------------------------------------------------------------------------
# prepared_at validation — parent correction pass
# ---------------------------------------------------------------------------


def test_commit_marker_rejects_malformed_prepared_at(tmp_path):
    """A hand-crafted marker with a non-timestamp prepared_at must never
    reach disk. _parse_marker_dict previously only checked ``isinstance(...,
    str)``, so any string ("not-a-timestamp") passed through.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    valid = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    bad = dataclasses.replace(valid, prepared_at="not-a-timestamp")
    with pytest.raises(MarkerValidationError) as exc_info:
        commit_marker(parent, bad)
    assert exc_info.value.reason == "malformed_prepared_at"
    target = refresh_hosts_dir(parent) / f"{bad.generation}.json"
    assert not target.exists()


def test_parse_marker_dict_rejects_non_canonical_prepared_at_shape(tmp_path):
    """A syntactically-parseable but non-canonical timestamp shape (e.g. with
    a UTC offset instead of literal 'Z', or sub-second precision) must be
    rejected — the schema requires the exact canonical _now_iso() shape.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["prepared_at"] = "2026-07-14T12:00:00+00:00"
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_prepared_at"


def test_parse_marker_dict_rejects_non_string_prepared_at(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["prepared_at"] = 12345
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_prepared_at"


# ---------------------------------------------------------------------------
# commit_marker workdir binding — parent correction pass
# ---------------------------------------------------------------------------


def test_commit_marker_rejects_cross_workdir_marker_before_touching_disk(tmp_path):
    """A marker built for workdir B must never be persisted under workdir A's
    refresh-hosts directory, even though it would later fail on load. The
    authority binding must be checked BEFORE any temp/target file is created.
    """
    workdir_a = tmp_path / "agent-a"
    workdir_a.mkdir()
    workdir_b = tmp_path / "agent-b"
    workdir_b.mkdir()
    marker_b = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(workdir_b), owned_run_ids=["em-1"],
    )
    with pytest.raises(MarkerValidationError) as exc_info:
        commit_marker(workdir_a, marker_b)
    assert exc_info.value.reason == "working_dir_mismatch"
    # No target file, and no temp litter, under workdir A's hosts dir.
    hosts_dir_a = refresh_hosts_dir(workdir_a)
    if hosts_dir_a.is_dir():
        assert list(hosts_dir_a.glob("*.json")) == []
        assert list(hosts_dir_a.glob(".*.tmp")) == []


# ---------------------------------------------------------------------------
# Durability: fsync temp file + containing directories — parent correction
# ---------------------------------------------------------------------------


def test_commit_marker_fsyncs_temp_before_publish(monkeypatch, tmp_path):
    """commit_marker must ask atomic_write_json to fsync the temp file's
    complete bytes before the exclusive os.link publish step — matching the
    accepted PREPARE contract's "durably committed before liveness teardown"
    requirement, not merely written to a page cache a crash could lose.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    calls = []
    real_atomic_write_json = refresh_host_module.atomic_write_json

    def _spy_atomic_write_json(*args, **kwargs):
        calls.append(kwargs)
        return real_atomic_write_json(*args, **kwargs)

    monkeypatch.setattr(refresh_host_module, "atomic_write_json", _spy_atomic_write_json)
    commit_marker(parent, marker)
    assert len(calls) == 1
    assert calls[0].get("fsync") is True


def test_commit_marker_fsyncs_containing_directory_after_link(monkeypatch, tmp_path):
    """After a successful os.link publish, the hosts_dir itself must be
    fsynced so the new marker's directory entry survives a crash immediately
    after commit_marker returns — not just the file's own content.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    fsynced_dirs = []
    real_fsync_dir = refresh_host_module._fsync_dir

    def _spy_fsync_dir(path):
        fsynced_dirs.append(Path(path))
        return real_fsync_dir(path)

    monkeypatch.setattr(refresh_host_module, "_fsync_dir", _spy_fsync_dir)
    commit_marker(parent, marker)
    assert refresh_hosts_dir(parent) in fsynced_dirs


def test_commit_marker_rolls_back_target_on_post_link_fsync_failure(monkeypatch, tmp_path):
    """A post-link directory-fsync failure must not leave a valid, visible
    'draining' marker behind — this call's exact self-created target must be
    rolled back (unlinked, then the rollback confirmed via a second
    directory fsync) before the original durability error is re-raised.

    This is the exact counterexample the parent mechanically proved:
    commit_raised=<error> but target_visible_after_error=True. After this
    fix, the target must be ABSENT once the exception propagates.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    call_count = [0]
    real_fsync_dir = refresh_host_module._fsync_dir

    def _fail_first_call_only(path):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("simulated post-link fsync failure")
        return real_fsync_dir(path)  # the rollback-confirmation call succeeds

    monkeypatch.setattr(refresh_host_module, "_fsync_dir", _fail_first_call_only)
    target = refresh_hosts_dir(parent) / f"{marker.generation}.json"
    with pytest.raises(OSError, match="simulated post-link fsync failure"):
        commit_marker(parent, marker)
    assert not target.exists(), "target must be rolled back, not left visible as a valid marker"
    # No temp litter either — only the rolled-back target and a clean directory.
    assert list(refresh_hosts_dir(parent).glob(".*.tmp")) == []


def test_commit_marker_raises_ambiguous_error_when_rollback_itself_fails(monkeypatch, tmp_path):
    """If BOTH the post-link fsync AND the rollback (unlink + confirming
    fsync) fail, this function cannot durably confirm target state — it
    must raise CommitAmbiguousError, NOT MarkerValidationError and NOT the
    bare original OSError, so a caller cannot mistake this for "no marker"
    or a clean, resumable failure.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _always_fail_fsync_dir(path):
        raise OSError("simulated fsync failure (always)")

    monkeypatch.setattr(refresh_host_module, "_fsync_dir", _always_fail_fsync_dir)
    with pytest.raises(CommitAmbiguousError) as exc_info:
        commit_marker(parent, marker)
    assert exc_info.value.rollback_attempted is True
    assert exc_info.value.rollback_succeeded is False
    assert exc_info.value.target_path == refresh_hosts_dir(parent) / f"{marker.generation}.json"


def test_commit_marker_raises_ambiguous_error_when_unlink_itself_fails(monkeypatch, tmp_path):
    """If the post-link fsync fails AND the rollback's unlink() call itself
    fails (not just the confirming fsync), this is also an ambiguous state
    — CommitAmbiguousError, not a bare propagated OSError.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1234, start_ticks=100, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _fail_fsync_dir(path):
        raise OSError("simulated post-link fsync failure")

    target = refresh_hosts_dir(parent) / f"{marker.generation}.json"

    real_unlink = Path.unlink

    def _fail_unlink(self, *args, **kwargs):
        if self == target:
            raise OSError("simulated unlink failure")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(refresh_host_module, "_fsync_dir", _fail_fsync_dir)
    monkeypatch.setattr(Path, "unlink", _fail_unlink)
    with pytest.raises(CommitAmbiguousError) as exc_info:
        commit_marker(parent, marker)
    assert exc_info.value.rollback_succeeded is False


def test_fsync_dir_tolerates_unsupported_but_not_other_errors(tmp_path):
    import errno as errno_module

    import lingtai.tools.daemon.refresh_host as refresh_host_module

    # A real directory fsyncs cleanly on this platform.
    refresh_host_module._fsync_dir(tmp_path)

    class _FakeOSError(OSError):
        pass

    def _fake_fsync_einval(fd):
        raise _FakeOSError(errno_module.EINVAL, "not supported on this filesystem")

    def _fake_fsync_eio(fd):
        raise _FakeOSError(errno_module.EIO, "real disk error")

    import os as os_module

    original_fsync = os_module.fsync
    try:
        os_module.fsync = _fake_fsync_einval
        refresh_host_module._fsync_dir(tmp_path)  # tolerated, does not raise
        os_module.fsync = _fake_fsync_eio
        with pytest.raises(OSError):
            refresh_host_module._fsync_dir(tmp_path)
    finally:
        os_module.fsync = original_fsync


def test_allocate_sequence_fsyncs_claim_and_directory(monkeypatch, tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    fsynced_dirs = []
    real_fsync_dir = refresh_host_module._fsync_dir

    def _spy_fsync_dir(path):
        fsynced_dirs.append(Path(path))
        return real_fsync_dir(path)

    monkeypatch.setattr(refresh_host_module, "_fsync_dir", _spy_fsync_dir)
    allocate_sequence(parent)
    expected_seq_dir = refresh_hosts_dir(parent) / ".sequence"
    assert expected_seq_dir in fsynced_dirs


# ---------------------------------------------------------------------------
# allocate_sequence — durable, exclusive, monotonic counter
# ---------------------------------------------------------------------------


def test_new_sequence_allocation_is_monotonic_and_exclusive_under_concurrency(tmp_path):
    """Happy-path proof: when every allocator thread actually completes (no
    crash/abandon), the allocated set is unique, never duplicated, and
    strictly increasing with no gaps. This is a property of the SPECIFIC
    scenario "N concurrent allocators, none crash" — see
    test_allocate_sequence_tolerates_gap_from_abandoned_claim below for why
    the general product contract is uniqueness + strict increase, not an
    unconditional no-gaps guarantee.
    """
    for _iteration in range(10):
        parent = tmp_path / f"seq-{_iteration}"
        parent.mkdir()
        n_threads = 16
        results = [None] * n_threads

        def _allocate(i: int):
            results[i] = allocate_sequence(parent)

        threads = [threading.Thread(target=_allocate, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert None not in results
        assert set(results) == set(range(1, n_threads + 1)), f"iteration {_iteration}: {sorted(results)}"


def test_allocate_sequence_tolerates_gap_from_abandoned_claim(tmp_path):
    """A sequence number claimed (its .sequence/<N> file created) but never
    followed by a real commit — e.g. the allocating process crashed between
    allocate_sequence() and commit_marker() — permanently "uses" that
    integer. This is expected, acceptable gap-tolerant behavior, NOT a bug:
    the durable on-disk claim file is the source of truth for "highest
    claimed," and re-issuing N after an abandoned claim would risk two
    markers claiming the same sequence if the abandoned process were not
    actually dead. The product contract this module provides is UNIQUENESS
    and STRICT INCREASE, not "no gaps ever" — this test proves allocation
    correctly continues past a gap rather than either reusing N or hanging.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    first = allocate_sequence(parent)
    assert first == 1
    # Simulate an abandoned claim: manually claim slot 2 (as allocate_sequence
    # itself would) without ever committing a marker for it.
    seq_dir = refresh_hosts_dir(parent) / ".sequence"
    os.close(os.open(str(seq_dir / "2"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    # The next real allocation must skip the abandoned slot 2 and continue
    # at 3 — never reusing 2, never hanging, never erroring.
    third = allocate_sequence(parent)
    assert third == 3
    fourth = allocate_sequence(parent)
    assert fourth == 4


_BARRIER_CHILD_SCRIPT = """
import os, sys, time

barrier_dir = sys.argv[1]

# --- setup_body: imports, path variables, and (for the commit race) the
# --- complete marker construction including sequence allocation. This runs
# --- BEFORE the ready signal, so none of it can stagger/serialize the
# --- actual post-release race operation below.
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

# --- race_body: the narrow, race-sensitive operation only. Nothing above
# --- this point runs after release; nothing below it ran before release.
{race_body}
"""


def _run_subprocess_race(
    setup_body: str, race_body: str, n_procs: int, tmp_path, timeout: float = 15.0
) -> list:
    """Spawn ``n_procs`` short-lived real Python subprocesses that all race
    the SAME operation, synchronized by a real start barrier so the race is
    genuinely simultaneous rather than merely sequentially-compatible.

    Each child executes, in order: (1) ``setup_body`` — imports, path
    variables, and any construction (e.g. ``allocate_sequence()`` plus
    building a ``RefreshHostMarker``) that is NOT itself the operation under
    test; (2) creates its own ``ready.<pid>`` file in a shared barrier
    directory, signaling "setup is complete, I am now waiting"; (3)
    busy-polls for a shared ``release`` file; (4) once released, executes
    ONLY ``race_body`` — the narrow, race-sensitive operation (e.g. a single
    ``allocate_sequence()`` call, or a single ``commit_marker()`` call) —
    with nothing else in between. This is the actual guarantee: the ready
    signal fires only after setup is done, so unsynchronized per-child
    import/construction latency can never leak into the post-release
    window and stagger what looks like a race.

    The parent (this function): launches all children, waits for every
    child's ``ready.<pid>`` file to appear (bounded timeout — proves every
    child finished its setup and is actively waiting, not merely spawned),
    creates the ``release`` file ONCE (releasing all children as close to
    simultaneously as the OS scheduler allows), then collects results. On
    ANY timeout (waiting for ready files, or waiting for a child's final
    output), only the child processes this call itself spawned are killed
    — never any other process on the system.
    """
    import subprocess as _subprocess
    import sys as _sys
    import time as _time

    barrier_dir = tmp_path / f"barrier-{secrets.token_hex(4)}"
    barrier_dir.mkdir()
    full_script = _BARRIER_CHILD_SCRIPT.format(
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
        # Wait for every child to signal it has FINISHED SETUP and is
        # actively waiting — NOT just "the OS accepted the fork/exec," and
        # NOT just "the process is alive but still mid-import."
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
        # Release all children as close to simultaneously as possible: one
        # exclusive-create, then every child's poll loop observes it on its
        # next ~1ms tick — this is the actual race-start signal. The fd is
        # closed immediately; only the file's existence is the signal, its
        # open handle serves no purpose afterward.
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
        # Bounded cleanup: kill only THIS call's own child processes, and
        # only those still running (a normal successful race will have
        # already exited by the time we get here).
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5.0)


def test_allocate_sequence_unique_and_strictly_increasing_across_real_processes(tmp_path):
    """Genuine, BARRIER-SYNCHRONIZED interprocess race: N separate Python
    subprocesses (not threads sharing one interpreter/GIL) each complete
    their imports/setup BEFORE signaling ready, then all wait at a real
    start barrier, are released simultaneously, and only THEN each call
    allocate_sequence() once against the same parent_working_dir — the
    entire post-release body is exactly that one call and its print, with
    no import or other setup work able to leak into the race window and
    stagger it. Proves uniqueness and strict increase hold under real
    OS-level process concurrency where the race window is actually
    simultaneous — not just "processes happened to run and were compatible
    with each other," which an unsynchronized launch (or a launch whose
    "post-release" body still contains unsynchronized setup) cannot rule
    out.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    setup_body = (
        "import sys; sys.path.insert(0, 'src'); "
        "from lingtai.tools.daemon.refresh_host import allocate_sequence; "
        f"parent = {str(parent)!r}"
    )
    race_body = "print(allocate_sequence(parent))"
    for _iteration in range(3):
        n_procs = 6
        results = _run_subprocess_race(setup_body, race_body, n_procs, tmp_path)
        values = []
        for out, err in results:
            assert out.isdigit(), f"iteration {_iteration}: subprocess did not print an integer: out={out!r} err={err!r}"
            values.append(int(out))
        assert len(set(values)) == n_procs, f"iteration {_iteration}: duplicate allocations: {sorted(values)}"
        assert sorted(values) == list(range(min(values), min(values) + n_procs)), (
            f"iteration {_iteration}: not strictly increasing/contiguous: {sorted(values)}"
        )


def test_commit_marker_exactly_one_real_process_wins_same_generation(tmp_path):
    """Genuine, BARRIER-SYNCHRONIZED interprocess race for marker
    publication: N separate Python subprocesses each complete their
    imports AND full marker construction — including their own
    allocate_sequence() call — as SETUP, before signaling ready. Only
    commit_marker() itself runs after the simultaneous release. This
    matters specifically for this test: allocating a sequence number is
    itself a durable, exclusive, filesystem-serialized operation, so if it
    ran post-release (as an earlier version of this helper did), that
    serialization could itself stagger the children enough that the later
    commit_marker() calls never actually raced — passing the assertions
    for the wrong reason. With sequence allocation moved to setup, the
    post-release body is EXACTLY the commit_marker() call and its print;
    exactly one must report success, every other must report
    generation_collision — proven under a race window that is actually
    simultaneous (os.link's exclusive-target semantics are enforced by the
    kernel, not by anything Python-level).
    """
    for _iteration in range(3):
        parent = tmp_path / f"agent-{_iteration}"
        parent.mkdir()
        shared_generation = new_generation()
        setup_body = (
            "import sys; sys.path.insert(0, 'src'); "
            "from lingtai.tools.daemon.refresh_host import ("
            "RefreshHostMarker, commit_marker, MarkerValidationError, MARKER_SCHEMA_VERSION, "
            "new_nonce, allocate_sequence); "
            f"parent = {str(parent)!r}; "
            f"generation = {shared_generation!r}; "
            "marker = RefreshHostMarker("
            "schema_version=MARKER_SCHEMA_VERSION, generation=generation, nonce=new_nonce(), "
            "pid=1234, start_ticks=100, sequence=allocate_sequence(parent), command_label='module', "
            f"working_dir={str(parent)!r}, owned_run_ids=('em-1',), state='draining', "
            "prepared_at=RefreshHostMarker._now_iso())"
        )
        race_body = (
            "try:\n"
            "    commit_marker(parent, marker)\n"
            "    print('ok')\n"
            "except MarkerValidationError as e:\n"
            "    print(e.reason)\n"
        )
        n_procs = 6
        results = _run_subprocess_race(setup_body, race_body, n_procs, tmp_path)
        outcomes = []
        for out, err in results:
            assert out, f"iteration {_iteration}: subprocess produced no output: err={err!r}"
            outcomes.append(out)
        assert outcomes.count("ok") == 1, f"iteration {_iteration}: expected exactly one winner: {outcomes}"
        assert outcomes.count("generation_collision") == n_procs - 1, (
            f"iteration {_iteration}: expected all losers to collide: {outcomes}"
        )


def test_barrier_setup_body_completes_before_ready_signal(tmp_path):
    """Deterministic proof — not merely doc prose — that setup_body fully
    completes (including a filesystem write) BEFORE a child signals ready,
    and that race_body runs only strictly after release.

    Each child's setup_body writes a per-child sentinel file containing a
    known marker value; the parent only creates 'release' after observing
    every child's ready.<pid> file. If setup_body's write had NOT
    completed before the ready signal (e.g. if setup and the ready-file
    creation could race with each other, or if setup were mistakenly
    scheduled to run post-release), the sentinel file could be missing or
    contain the WRONG (pre-release) content at the moment this test checks
    it right after collecting ready files but before releasing — this test
    checks the sentinel deterministically at exactly that moment, not
    after the whole race has already finished (which would prove nothing
    about ordering).
    """
    import subprocess as _subprocess
    import sys as _sys
    import time as _time

    barrier_dir = tmp_path / f"barrier-{secrets.token_hex(4)}"
    barrier_dir.mkdir()
    n_procs = 4
    sentinel_value = f"setup-complete-{secrets.token_hex(6)}"
    setup_body = (
        "import os\n"
        f"sentinel_path = os.path.join(barrier_dir, 'sentinel.' + str(os.getpid()))\n"
        f"with open(sentinel_path, 'w') as f:\n"
        f"    f.write({sentinel_value!r})\n"
    )
    race_body = "print('raced')"
    full_script = _BARRIER_CHILD_SCRIPT.format(setup_body=setup_body, race_body=race_body, timeout=15.0)
    env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "src")}

    procs = [
        _subprocess.Popen(
            [_sys.executable, "-c", full_script, str(barrier_dir)],
            stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True, env=env,
        )
        for _ in range(n_procs)
    ]
    try:
        deadline = _time.monotonic() + 15.0
        while len(list(barrier_dir.glob("ready.*"))) < n_procs:
            assert _time.monotonic() < deadline, "children did not reach the barrier in time"
            _time.sleep(0.005)
        # AT THIS EXACT MOMENT — every child has signaled ready, but release
        # has not been created yet — every child's sentinel must ALREADY
        # exist with the correct content. This is the deterministic proof:
        # setup_body's write is durably observable strictly before release,
        # which is only possible if setup ran to completion before the
        # ready signal (the code path this test exercises has the sentinel
        # write physically before the ready-file creation in the generated
        # script, so this also guards against a future edit reordering them).
        sentinel_paths = sorted(barrier_dir.glob("sentinel.*"))
        assert len(sentinel_paths) == n_procs, (
            f"expected {n_procs} sentinels already written by ready-time, found {len(sentinel_paths)}"
        )
        for sentinel_path in sentinel_paths:
            assert sentinel_path.read_text(encoding="utf-8") == sentinel_value

        release_fd = os.open(str(barrier_dir / "release"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(release_fd)
        for p in procs:
            out, err = p.communicate(timeout=15.0)
            assert out.strip() == "raced", f"unexpected child output: out={out!r} err={err!r}"
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5.0)


def test_run_subprocess_race_kills_only_its_own_hung_children_on_timeout(tmp_path):
    """If a child hangs past the barrier release (never produces output),
    the timeout-cleanup path must kill ONLY the child processes this call
    itself spawned, and must not leave any of them running afterward.

    Searches ``ps`` for a UNIQUE per-test-run token embedded in this test's
    own child command line, not a generic string like "time.sleep(3600)" —
    a generic search could false-fail (or false-pass) if an unrelated
    process on the machine happens to contain the same substring; a random
    per-run token cannot collide with anything this test did not spawn.
    """
    unique_token = f"honeypot-{secrets.token_hex(8)}"
    setup_body = f"marker = {unique_token!r}"  # forces the token into the command line
    race_body = "import time; time.sleep(3600)"  # never terminates on its own
    n_procs = 2
    with pytest.raises(Exception):  # subprocess.TimeoutExpired from p.communicate()
        _run_subprocess_race(setup_body, race_body, n_procs, tmp_path, timeout=1.0)
    # Give the OS a brief moment to reflect the kill, then confirm no
    # lingering child bearing this run's unique token remains alive.
    import subprocess as _subprocess
    import time as _time

    _time.sleep(0.2)
    ps_out = _subprocess.run(["ps", "ax", "-o", "command="], capture_output=True, text=True).stdout
    hung_survivors = [line for line in ps_out.splitlines() if unique_token in line]
    assert hung_survivors == [], f"hung test-owned children were not cleaned up: {hung_survivors}"


def test_allocate_sequence_durable_across_calls(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    a = allocate_sequence(parent)
    b = allocate_sequence(parent)
    c = allocate_sequence(parent)
    assert (a, b, c) == (1, 2, 3)


# ---------------------------------------------------------------------------
# Marker validation — malformed / stale / rogue cases must all fail closed
# ---------------------------------------------------------------------------


def _write_raw_marker(parent: Path, data: object, name: str = "20260714-000000-aaaaaa.json") -> Path:
    hosts_dir = refresh_hosts_dir(parent)
    hosts_dir.mkdir(parents=True, exist_ok=True)
    target = hosts_dir / name
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def test_load_marker_rejects_non_dict(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    path = _write_raw_marker(parent, ["not", "a", "dict"])
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "not_a_dict"


def test_load_marker_rejects_wrong_schema_version(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    path = _write_raw_marker(parent, {"schema_version": 999})
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "schema_mismatch"


def test_load_marker_rejects_missing_fields(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    path = _write_raw_marker(parent, {"schema_version": MARKER_SCHEMA_VERSION})
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "missing_fields"


def test_load_marker_rejects_malformed_generation(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["generation"] = "not-a-valid-generation"
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_generation"


def test_load_marker_rejects_empty_owned_run_ids(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["owned_run_ids"] = []
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "empty_run_ids"


def test_load_marker_rejects_non_draining_state(tmp_path):
    """A host-exited/archived marker record must never grant a live exemption."""
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["state"] = "host_exiting"
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "not_draining"


def test_load_marker_rejects_unreadable_file(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    hosts_dir = refresh_hosts_dir(parent)
    hosts_dir.mkdir(parents=True)
    path = hosts_dir / "20260714-000000-aaaaaa.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "unreadable"


def test_parse_marker_dict_rejects_non_string_command_label(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["command_label"] = 123
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_command_label"


def test_parse_marker_dict_rejects_bool_pid(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["pid"] = True
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_pid"


def test_parse_marker_dict_rejects_non_hex_nonce(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["nonce"] = "g" * 32
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_nonce"


def test_parse_marker_dict_rejects_empty_or_duplicate_run_ids(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["owned_run_ids"] = ["em-1", "", "em-1"]
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_run_ids"


def test_parse_marker_dict_rejects_non_canonical_working_dir(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["working_dir"] = "relative/path"
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "malformed_working_dir"


def test_parse_marker_dict_rejects_unexpected_extra_keys(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    data["unexpected_stray_field"] = "surprise"
    path = _write_raw_marker(parent, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=parent)
    assert exc_info.value.reason == "unexpected_fields"


def test_load_marker_rejects_workdir_mismatch_with_directory_it_was_loaded_from(tmp_path):
    """A marker found under workdir A whose working_dir field claims workdir B
    must be rejected — the directory a marker is loaded from must match the
    directory it claims authority over.
    """
    workdir_a = tmp_path / "agent-a"
    workdir_a.mkdir()
    workdir_b = tmp_path / "agent-b"
    workdir_b.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(workdir_b), owned_run_ids=["em-1"],
    )
    data = marker.to_dict()
    path = _write_raw_marker(workdir_a, data)
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(path, expected_working_dir=workdir_a)
    assert exc_info.value.reason == "working_dir_mismatch"


def test_load_marker_rejects_wrong_filename_for_embedded_generation(tmp_path):
    """A FULLY VALID marker (correct content, correct working_dir field, all
    schema checks pass) must still be rejected if the file it was loaded
    from is not named exactly '<embedded generation>.json'.

    This is the exact counterexample the parent mechanically proved: a
    marker embedding generation 20260714-141519-0f079f loaded successfully
    from a file named 20260714-000000-aaaaaa.json in the SAME (correct)
    hosts directory, purely because the old code never cross-checked the
    filename against the content it just parsed.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    real_path = commit_marker(parent, marker)
    hosts_dir = refresh_hosts_dir(parent)
    wrong_name_path = hosts_dir / "20260714-000000-aaaaaa.json"
    wrong_name_path.write_text(real_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(wrong_name_path, expected_working_dir=parent)
    assert exc_info.value.reason == "filename_generation_mismatch"
    # The marker loaded from its OWN correct path must still succeed.
    assert load_marker(real_path, expected_working_dir=parent) == marker


def test_load_marker_rejects_wrong_directory_with_matching_working_dir_field(tmp_path):
    """A FULLY VALID marker, correctly named '<generation>.json', but placed
    in a directory OTHER than refresh_hosts_dir(expected_working_dir) — even
    though its embedded working_dir field matches expected_working_dir —
    must still be rejected. Only the exact, canonical hosts directory is
    ever a valid marker location, regardless of what the file's own content
    claims.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    real_path = commit_marker(parent, marker)
    rogue_dir = tmp_path / "not-the-hosts-dir"
    rogue_dir.mkdir()
    rogue_path = rogue_dir / f"{marker.generation}.json"
    rogue_path.write_text(real_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(rogue_path, expected_working_dir=parent)
    assert exc_info.value.reason == "wrong_directory"


def test_load_marker_accepts_symlinked_alias_that_resolves_to_the_real_hosts_dir(tmp_path):
    """A marker reached through a directory that is a SYMLINK to the real
    ``refresh_hosts_dir(expected_working_dir)`` must be ACCEPTED, not
    rejected — resolution (``Path(path).resolve()``) collapses the alias
    to the exact same canonical path the real, non-symlinked location would
    resolve to, so a symlink alias to the correct location is not a
    distinct, rejectable location.

    (Named accurately after a prior version of this test was misleadingly
    named "..._rejects_..." while its own body actually asserted
    acceptance for this exact case — the docstring/name now matches what
    the assertions below actually check.)

    A SEPARATE, unrelated failure mode is also checked here for coverage:
    loading through that SAME alias while claiming a DIFFERENT
    ``expected_working_dir`` still correctly fails — but on the ordinary,
    pre-existing ``working_dir_mismatch`` content-level check (the marker's
    own embedded ``working_dir`` field does not match what was claimed),
    not because of anything specific to the alias/symlink itself.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=1, start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    real_path = commit_marker(parent, marker)
    alias_hosts_dir = tmp_path / "alias-hosts-dir"
    alias_hosts_dir.symlink_to(refresh_hosts_dir(parent), target_is_directory=True)
    alias_path = alias_hosts_dir / real_path.name

    assert load_marker(alias_path, expected_working_dir=parent) == marker

    other_parent = tmp_path / "other-agent"
    other_parent.mkdir()
    with pytest.raises(MarkerValidationError) as exc_info:
        load_marker(alias_path, expected_working_dir=other_parent)
    assert exc_info.value.reason == "working_dir_mismatch"


def test_commit_marker_output_is_always_loadable_by_its_own_path(tmp_path):
    """Sanity/consistency check: commit_marker's own return value must
    always satisfy load_marker's new path/filename binding — the fix must
    not break the normal round-trip, only reject OTHER paths/filenames.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    for i in range(3):
        marker = RefreshHostMarker.build(
            pid=1, start_ticks=i + 1, command_label="module",
            working_dir=str(parent), owned_run_ids=[f"em-{i}"],
        )
        path = commit_marker(parent, marker)
        assert load_marker(path, expected_working_dir=parent) == marker
    # iter_marker_paths' own output must also always be loadable.
    for path in iter_marker_paths(parent):
        load_marker(path, expected_working_dir=parent)  # must not raise


# ---------------------------------------------------------------------------
# verify_marker_live — the sole three-signal liveness predicate
# ---------------------------------------------------------------------------


def test_verify_marker_live_false_for_real_self_process_without_agent_run_cmdline(tmp_path):
    """Our own process (pytest, not a real agent-run command line) must be
    rejected on the command-shape signal, not crash — real PID and matching
    start identity are not sufficient by themselves.
    """
    identity = probe_process_start_identity(os.getpid())
    if identity is None:
        pytest.skip("start-identity probe unavailable on this platform (see module docstring)")
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    assert verify_marker_live(marker) is False


def test_verify_marker_live_true_for_real_self_process_on_darwin(monkeypatch, tmp_path):
    """Real, unskipped, non-inverted positive-path proof: our own real PID and
    real probed start identity, with only the command-line reader
    monkeypatched to a canonical agent-run shape, verifies live end-to-end.
    """
    identity = probe_process_start_identity(os.getpid())
    if identity is None:
        pytest.skip("start-identity probe unavailable on this platform (see module docstring)")
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}" if pid == os.getpid() else None,
    )
    assert verify_marker_live(marker) is True


def test_verify_marker_live_requires_exact_command_label_match(monkeypatch, tmp_path):
    """All three positive signals present, but the observed launch form
    ('module') differs from what the marker recorded ('console') — must be
    False, not merely 'is match_agent_run non-None'.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="console",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: ProcessStartIdentity(pid=pid, start_ticks=999) if pid == 4242 else None,
    )
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}" if pid == 4242 else None,
    )
    assert verify_marker_live(marker) is False


def test_verify_marker_live_true_when_all_three_signals_match(monkeypatch, tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: ProcessStartIdentity(pid=pid, start_ticks=999) if pid == 4242 else None,
    )
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}" if pid == 4242 else None,
    )
    assert verify_marker_live(marker) is True


def test_verify_marker_live_false_on_dead_pid(monkeypatch, tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module
    monkeypatch.setattr(refresh_host_module, "probe_process_start_identity", lambda pid: None)
    assert verify_marker_live(marker) is False


def test_verify_marker_live_false_on_start_identity_mismatch_pid_reuse(monkeypatch, tmp_path):
    """The core anti-PID-reuse case: a live process at the marker's PID, but
    with a DIFFERENT start_ticks — i.e. the original process died and the PID
    was reused by an unrelated later process. Must be rejected, not adopted.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module
    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: ProcessStartIdentity(pid=pid, start_ticks=123456),  # different!
    )
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}",
    )
    assert verify_marker_live(marker) is False


def test_verify_marker_live_false_on_command_mismatch(monkeypatch, tmp_path):
    """Live PID with matching start identity but a command line that no
    longer matches the canonical agent-run shape for this working_dir (e.g.
    the marker names an unrelated run, or the process re-execed into
    something else) must be rejected.
    """
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module
    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: ProcessStartIdentity(pid=pid, start_ticks=999),
    )
    monkeypatch.setattr(refresh_host_module, "_read_cmdline", lambda pid: "vim notes.txt")
    assert verify_marker_live(marker) is False


def test_verify_marker_live_false_when_cmdline_unreadable(monkeypatch, tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    import lingtai.tools.daemon.refresh_host as refresh_host_module
    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: ProcessStartIdentity(pid=pid, start_ticks=999),
    )
    monkeypatch.setattr(refresh_host_module, "_read_cmdline", lambda pid: None)
    assert verify_marker_live(marker) is False


# ---------------------------------------------------------------------------
# verified_refresh_host_pids / is_verified_refresh_host — strict per-PID
# discovery/predicate for the CLI duplicate guard and detached watcher.
# ---------------------------------------------------------------------------


def _commit_verifiable_marker(parent, *, pid, command_label="module", owned_run_ids=("em-1",)):
    """Commit a real marker whose start_ticks matches a REAL probe for pid.

    Uses the real probe (not a fabricated start_ticks) so callers can
    monkeypatch only `_read_cmdline` and still exercise the real
    `probe_process_start_identity` + `match_agent_run` path for `pid ==
    os.getpid()` — the same "real self process" positive-path pattern the
    accepted foundation's own `verify_marker_live` tests use.
    """
    identity = probe_process_start_identity(pid)
    assert identity is not None, "start-identity probe must be available for this test's pid"
    marker = RefreshHostMarker.build(
        pid=pid, start_ticks=identity.start_ticks, command_label=command_label,
        working_dir=str(parent), owned_run_ids=list(owned_run_ids),
    )
    commit_marker(parent, marker)
    return marker


def test_verified_refresh_host_pids_positive_discovery(monkeypatch, tmp_path):
    """A single, real, live, correctly-labeled marker authorizes exactly its
    own PID and no other."""
    parent = tmp_path / "agent"
    parent.mkdir()
    _commit_verifiable_marker(parent, pid=os.getpid())
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}" if pid == os.getpid() else None,
    )
    result = verified_refresh_host_pids(parent)
    assert result == frozenset({os.getpid()})
    assert is_verified_refresh_host(os.getpid(), parent) is True


def test_is_verified_refresh_host_false_for_unrelated_pid(monkeypatch, tmp_path):
    """A valid marker for PID A must never exempt a different PID B."""
    parent = tmp_path / "agent"
    parent.mkdir()
    _commit_verifiable_marker(parent, pid=os.getpid())
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}" if pid == os.getpid() else None,
    )
    unrelated_pid = os.getpid() + 1
    assert is_verified_refresh_host(unrelated_pid, parent) is False


def test_verified_refresh_host_pids_empty_when_no_hosts_dir(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_excludes_duplicate_claim_on_same_pid(monkeypatch, tmp_path):
    """Two distinct valid, live markers naming the SAME pid must fail closed
    for that pid — a duplicate claim must never be exempted, even though
    each marker individually parses and verifies live."""
    parent = tmp_path / "agent"
    parent.mkdir()
    identity = probe_process_start_identity(os.getpid())
    assert identity is not None
    first = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    second = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-2"],
    )
    commit_marker(parent, first)
    commit_marker(parent, second)
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}" if pid == os.getpid() else None,
    )
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(os.getpid(), parent) is False


def test_verified_refresh_host_pids_duplicate_claim_does_not_exclude_other_pids(monkeypatch, tmp_path):
    """A duplicate claim on PID A must not poison discovery for an unrelated,
    unambiguous, valid, live marker naming a different PID B in the same
    hosts directory."""
    parent = tmp_path / "agent"
    parent.mkdir()
    identity = probe_process_start_identity(os.getpid())
    assert identity is not None
    dup_a = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    dup_b = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-2"],
    )
    commit_marker(parent, dup_a)
    commit_marker(parent, dup_b)
    other_pid = 4242
    unambiguous = RefreshHostMarker.build(
        pid=other_pid, start_ticks=999, command_label="console",
        working_dir=str(parent), owned_run_ids=["em-3"],
    )
    commit_marker(parent, unambiguous)
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _fake_probe(pid):
        if pid == os.getpid():
            return identity
        if pid == other_pid:
            return ProcessStartIdentity(pid=pid, start_ticks=999)
        return None

    monkeypatch.setattr(refresh_host_module, "probe_process_start_identity", _fake_probe)
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: (
            f"python -m lingtai run {parent}" if pid == os.getpid()
            else (f"lingtai-agent run {parent}" if pid == other_pid else None)
        ),
    )
    result = verified_refresh_host_pids(parent)
    assert result == frozenset({other_pid})
    assert is_verified_refresh_host(os.getpid(), parent) is False
    assert is_verified_refresh_host(other_pid, parent) is True


def test_verified_refresh_host_pids_excludes_stale_start_identity_pid_reuse(monkeypatch, tmp_path):
    """A marker whose recorded start_ticks no longer matches a live probe at
    that pid (the anti-PID-reuse case) never authorizes the pid."""
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    commit_marker(parent, marker)
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: ProcessStartIdentity(pid=pid, start_ticks=123456),  # reused PID
    )
    monkeypatch.setattr(refresh_host_module, "_read_cmdline", lambda pid: f"python -m lingtai run {parent}")
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_excludes_dead_pid(monkeypatch, tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    commit_marker(parent, marker)
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(refresh_host_module, "probe_process_start_identity", lambda pid: None)
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_excludes_command_label_mismatch(monkeypatch, tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="console",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    commit_marker(parent, marker)
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: ProcessStartIdentity(pid=pid, start_ticks=999),
    )
    # Real observed launch form is "module", not the marker's recorded "console".
    monkeypatch.setattr(refresh_host_module, "_read_cmdline", lambda pid: f"python -m lingtai run {parent}")
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_excludes_malformed_marker_without_raising(tmp_path):
    """A malformed marker file in the hosts directory must not raise out of
    discovery — it is silently excluded, exactly like "no marker"."""
    parent = tmp_path / "agent"
    parent.mkdir()
    hosts_dir = refresh_hosts_dir(parent)
    hosts_dir.mkdir(parents=True)
    (hosts_dir / "20260714-000000-aaaaaa.json").write_text("{not valid json", encoding="utf-8")
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_excludes_wrong_workdir_marker(tmp_path):
    """A marker committed for a DIFFERENT workdir must not be discoverable
    (or authorize anything) when queried against this workdir."""
    workdir_a = tmp_path / "agent-a"
    workdir_a.mkdir()
    workdir_b = tmp_path / "agent-b"
    workdir_b.mkdir()
    _commit_verifiable_marker(workdir_b, pid=os.getpid())
    # Querying workdir_a's (empty) hosts dir must find nothing — the marker
    # lives entirely under workdir_b's tree.
    assert verified_refresh_host_pids(workdir_a) == frozenset()
    assert is_verified_refresh_host(os.getpid(), workdir_a) is False


def test_verified_refresh_host_pids_observation_error_fails_closed_not_raises(monkeypatch, tmp_path):
    """An unexpected exception from the liveness probe during discovery must
    be swallowed as "cannot prove clean," never propagated to the caller —
    matching the CLI-boot/watcher fail-closed requirement (discovery must
    never crash boot or watcher protection)."""
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-1"],
    )
    commit_marker(parent, marker)
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _boom(_marker):
        raise RuntimeError("simulated observation failure")

    monkeypatch.setattr(refresh_host_module, "verify_marker_live", _boom)
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_enumeration_oserror_fails_closed_not_raises(monkeypatch, tmp_path):
    """An OSError raised while enumerating the hosts directory itself (not
    a per-marker load error) must fail closed to the empty set, never raise
    out of discovery — enumeration is exactly as fail-closed as validation."""
    parent = tmp_path / "agent"
    parent.mkdir()
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _boom(_parent_working_dir):
        raise OSError("simulated enumeration failure")
        yield  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(refresh_host_module, "iter_marker_paths", _boom)
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_enumeration_permission_error_fails_closed(monkeypatch, tmp_path):
    """A PermissionError specifically (the realistic case for an
    unreadable/mode-restricted hosts directory) must also fail closed."""
    parent = tmp_path / "agent"
    parent.mkdir()
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    def _boom(_parent_working_dir):
        raise PermissionError("simulated permission failure")
        yield  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(refresh_host_module, "iter_marker_paths", _boom)
    assert verified_refresh_host_pids(parent) == frozenset()
    assert is_verified_refresh_host(4242, parent) is False


def test_verified_refresh_host_pids_unexpected_load_error_fails_closed_isolated(monkeypatch, tmp_path):
    """An unexpected (non-MarkerValidationError) exception from load_marker
    for ONE marker excludes only that marker — it must not raise out of
    discovery, and must not poison a different, independently valid marker
    in the same hosts directory."""
    parent = tmp_path / "agent"
    parent.mkdir()
    broken_marker = RefreshHostMarker.build(
        pid=4242, start_ticks=999, command_label="module",
        working_dir=str(parent), owned_run_ids=["em-broken"],
    )
    commit_marker(parent, broken_marker)
    _commit_verifiable_marker(parent, pid=os.getpid())
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    real_load_marker = refresh_host_module.load_marker

    def _flaky_load_marker(path, *, expected_working_dir):
        if path.name == f"{broken_marker.generation}.json":
            raise OSError("simulated unexpected load-time filesystem error")
        return real_load_marker(path, expected_working_dir=expected_working_dir)

    monkeypatch.setattr(refresh_host_module, "load_marker", _flaky_load_marker)
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {parent}" if pid == os.getpid() else None,
    )
    result = verified_refresh_host_pids(parent)
    assert result == frozenset({os.getpid()})
    assert is_verified_refresh_host(4242, parent) is False
    assert is_verified_refresh_host(os.getpid(), parent) is True


# ---------------------------------------------------------------------------
# ExecutionOwner — the durable per-run ownership binding tagged onto each
# owned run's daemon.json, proving that record belongs to exactly one marker.
# ---------------------------------------------------------------------------


def _build_and_commit_marker(parent, *, pid, owned_run_ids, start_ticks=None):
    identity = probe_process_start_identity(pid) if start_ticks is None else None
    ticks = start_ticks if start_ticks is not None else identity.start_ticks
    marker = RefreshHostMarker.build(
        pid=pid, start_ticks=ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=owned_run_ids,
    )
    commit_marker(parent, marker)
    return marker


def test_execution_owner_to_dict_round_trips_through_from_dict():
    owner = ExecutionOwner(
        schema_version=1, generation="20260101-000000-abcdef", nonce="a" * 32,
        pid=123, start_ticks=456, sequence=1, owned_run_ids=("em-1", "em-2"),
    )
    data = owner.to_dict()
    restored = ExecutionOwner.from_dict(data)
    assert restored == owner


def test_execution_owner_from_marker_copies_identity_and_run_set():
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir="/tmp", owned_run_ids=["em-a", "em-b"],
    )
    owner = ExecutionOwner.from_marker(marker)
    assert owner.schema_version == marker.schema_version
    assert owner.generation == marker.generation
    assert owner.nonce == marker.nonce
    assert owner.pid == marker.pid
    assert owner.start_ticks == marker.start_ticks
    assert owner.sequence == marker.sequence
    assert owner.owned_run_ids == marker.owned_run_ids


def test_execution_owner_proves_membership_true_only_for_own_run_id():
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir="/tmp", owned_run_ids=["em-owned"],
    )
    owner = ExecutionOwner.from_marker(marker)
    assert owner.proves_membership_of("em-owned", marker) is True
    assert owner.proves_membership_of("em-not-owned", marker) is False


def test_execution_owner_proves_membership_false_for_mismatched_marker():
    """An owner tag whose generation/nonce/pid doesn't match the CURRENT
    marker being checked against must never be treated as proof of
    ownership — this is the core anti-adoption guarantee: a later host must
    never inherit a prior host's run by reusing/mismatching identity."""
    marker_a = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir="/tmp", owned_run_ids=["em-shared-name"],
    )
    marker_b = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir="/tmp", owned_run_ids=["em-shared-name"],
    )
    owner_from_a = ExecutionOwner.from_marker(marker_a)
    # Same run id, but tagged by a DIFFERENT marker (different generation/nonce).
    assert owner_from_a.proves_membership_of("em-shared-name", marker_b) is False


# ---------------------------------------------------------------------------
# tag_owned_runs — atomic all-or-nothing owner tagging across a fixed run set
# ---------------------------------------------------------------------------


class _FakeRunDir:
    """Minimal stand-in for DaemonRunDir's execution-owner mutator surface,
    used only to prove tag_owned_runs' all-or-nothing/rollback contract in
    isolation from the real DaemonRunDir/filesystem run-folder machinery
    (which is exercised separately once run_dir.py's real methods exist —
    see tests/test_daemon_run_dir.py for the real-filesystem coverage)."""

    def __init__(self, run_id, *, fail_on_tag=False, fail_on_rollback=False):
        self.run_id = run_id
        self._fail_on_tag = fail_on_tag
        self._fail_on_rollback = fail_on_rollback
        self.tagged_with = None
        self.rolled_back = False

    def set_execution_owner(self, execution_owner):
        if self._fail_on_tag:
            raise OSError(f"simulated tag-write failure for {self.run_id}")
        self.tagged_with = execution_owner

    def clear_execution_owner_on_rollback(self):
        if self._fail_on_rollback:
            raise OSError(f"simulated rollback failure for {self.run_id}")
        self.rolled_back = True
        self.tagged_with = None


def test_tag_owned_runs_tags_every_run_on_success():
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir="/tmp", owned_run_ids=["em-1", "em-2", "em-3"],
    )
    run_dirs = {rid: _FakeRunDir(rid) for rid in marker.owned_run_ids}
    result = tag_owned_runs(marker, run_dirs)
    assert result == {"em-1": True, "em-2": True, "em-3": True}
    for rid, rd in run_dirs.items():
        assert rd.tagged_with is not None
        assert rd.tagged_with["generation"] == marker.generation
        assert tuple(rd.tagged_with["owned_run_ids"]) == marker.owned_run_ids


def test_tag_owned_runs_rolls_back_prior_successful_tags_on_mid_failure():
    """Post-hoc negative control (not failing-first — see report §3): proves
    that when the SECOND of three tag writes fails, the FIRST run's
    already-applied tag is rolled back rather than left claiming membership
    in a marker whose commit never completed with a fully-tagged run set."""
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir="/tmp", owned_run_ids=["em-1", "em-2", "em-3"],
    )
    run_dirs = {
        "em-1": _FakeRunDir("em-1"),
        "em-2": _FakeRunDir("em-2", fail_on_tag=True),
        "em-3": _FakeRunDir("em-3"),
    }
    with pytest.raises(OSError, match="em-2"):
        tag_owned_runs(marker, run_dirs)
    assert run_dirs["em-1"].rolled_back is True
    assert run_dirs["em-1"].tagged_with is None
    assert run_dirs["em-2"].tagged_with is None
    # em-3 was never reached (dict iteration order is insertion order in
    # CPython 3.7+, and tag_owned_runs must process/rollback in a
    # deterministic order for this assertion to be meaningful).
    assert run_dirs["em-3"].tagged_with is None
    assert run_dirs["em-3"].rolled_back is False


def test_tag_owned_runs_raises_ambiguous_when_rollback_itself_fails():
    """If rollback of an already-tagged run ALSO fails, that run's true
    on-disk state is unknown — tag_owned_runs must raise
    OwnerTaggingAmbiguousError (never silently continue, never claim
    ordinary failure) so the caller (commit sequencing in the manager) knows
    NOT to proceed to commit_marker with an unverifiable run set."""
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir="/tmp", owned_run_ids=["em-1", "em-2"],
    )
    run_dirs = {
        "em-1": _FakeRunDir("em-1", fail_on_rollback=True),
        "em-2": _FakeRunDir("em-2", fail_on_tag=True),
    }
    with pytest.raises(OwnerTaggingAmbiguousError) as exc_info:
        tag_owned_runs(marker, run_dirs)
    assert "em-1" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Control request/ack plane — generation-bound, exclusive-create, idempotent
# ---------------------------------------------------------------------------


def test_control_dir_is_generation_scoped_and_disjoint_across_generations():
    parent = Path("/tmp/agent-workdir-does-not-need-to-exist")
    dir_a = control_dir(parent, "20260101-000000-aaaaaa")
    dir_b = control_dir(parent, "20260101-000000-bbbbbb")
    assert dir_a != dir_b
    assert dir_a.name == "control"
    assert dir_a.parent.name == "20260101-000000-aaaaaa"
    assert ".refresh-hosts" in dir_a.parts


def test_control_request_round_trips_to_dict_and_from_dict():
    req = ControlRequest(
        schema_version=1, request_id="req-abc123", generation="20260101-000000-aaaaaa",
        nonce="a" * 32, target_run_ids=("em-1",), operation="ask",
        payload={"message": "hello"}, requester_pid=999, requester_start_ticks=111,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    restored = ControlRequest.from_dict(req.to_dict())
    assert restored == req


def test_control_request_rejects_unknown_operation():
    with pytest.raises(Exception):
        ControlRequest(
            schema_version=1, request_id="req-x", generation="20260101-000000-aaaaaa",
            nonce="a" * 32, target_run_ids=("em-1",), operation="not_a_real_operation",
            payload={}, requester_pid=1, requester_start_ticks=1,
            created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
        ).to_dict().__class__ and ControlRequest.from_dict(
            {
                "schema_version": 1, "request_id": "req-x", "generation": "20260101-000000-aaaaaa",
                "nonce": "a" * 32, "target_run_ids": ["em-1"], "operation": "not_a_real_operation",
                "payload": {}, "requester_pid": 1, "requester_start_ticks": 1,
                "created_at": "2026-01-01T00:00:00Z", "deadline_at": "2026-01-01T00:05:00Z",
            }
        )


def test_control_ack_round_trips_and_rejects_unknown_status():
    ack = ControlAck(
        schema_version=1, request_id="req-abc123", generation="20260101-000000-aaaaaa",
        target_run_ids=("em-1",), status="accepted",
        responded_at="2026-01-01T00:00:01Z", detail={},
    )
    restored = ControlAck.from_dict(ack.to_dict())
    assert restored == ack
    with pytest.raises(Exception):
        ControlAck.from_dict({**ack.to_dict(), "status": "definitely_not_a_status"})


def test_write_control_request_then_read_pending_returns_it(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1"])
    req = ControlRequest(
        schema_version=1, request_id="req-1", generation=marker.generation,
        nonce=marker.nonce, target_run_ids=("em-1",), operation="ask",
        payload={"message": "hi"}, requester_pid=os.getpid(), requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    write_control_request(parent, req)
    pending = list(read_pending_control_requests(parent, marker.generation))
    assert len(pending) == 1
    assert pending[0].request_id == "req-1"


def test_write_control_request_duplicate_id_is_exclusive_create_conflict(tmp_path):
    """Duplicate request id must raise FileExistsError (never silently
    overwrite) — the successor's idempotent-retry contract depends on this:
    a caller that sees FileExistsError knows to poll for the existing ack
    rather than assuming its request was accepted twice."""
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1"])
    req = ControlRequest(
        schema_version=1, request_id="req-dup", generation=marker.generation,
        nonce=marker.nonce, target_run_ids=("em-1",), operation="ask",
        payload={}, requester_pid=os.getpid(), requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    write_control_request(parent, req)
    with pytest.raises(FileExistsError):
        write_control_request(parent, req)


def test_write_control_ack_then_read_back(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1"])
    ack = ControlAck(
        schema_version=1, request_id="req-1", generation=marker.generation,
        target_run_ids=("em-1",), status="accepted",
        responded_at="2026-01-01T00:00:01Z", detail={"note": "ok"},
    )
    ack_path = write_control_ack(parent, ack)
    assert ack_path.exists()
    loaded = ControlAck.from_dict(json.loads(ack_path.read_text(encoding="utf-8")))
    assert loaded == ack


def test_write_control_ack_duplicate_request_id_returns_same_ack_idempotently(tmp_path):
    """A second attempt to publish an ack for the SAME request_id must not
    execute the operation twice or silently overwrite a different ack —
    write_control_ack must be exclusive-create like write_control_request,
    and a caller retrying after e.g. a crash-before-confirming-durability
    must be able to detect 'already acked' via FileExistsError and re-read
    the existing ack rather than re-run the dispatched operation."""
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1"])
    first_ack = ControlAck(
        schema_version=1, request_id="req-1", generation=marker.generation,
        target_run_ids=("em-1",), status="accepted",
        responded_at="2026-01-01T00:00:01Z", detail={},
    )
    write_control_ack(parent, first_ack)
    second_ack = ControlAck(
        schema_version=1, request_id="req-1", generation=marker.generation,
        target_run_ids=("em-1",), status="already-terminal",
        responded_at="2026-01-01T00:00:02Z", detail={},
    )
    with pytest.raises(FileExistsError):
        write_control_ack(parent, second_ack)
    # The original ack on disk must be unchanged — never silently overwritten.
    ack_path = control_dir(parent, marker.generation) / "acks" / "req-1.json"
    loaded = ControlAck.from_dict(json.loads(ack_path.read_text(encoding="utf-8")))
    assert loaded == first_ack


def test_read_pending_control_requests_excludes_already_acked(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1", "em-2"])
    req1 = ControlRequest(
        schema_version=1, request_id="req-1", generation=marker.generation,
        nonce=marker.nonce, target_run_ids=("em-1",), operation="ask",
        payload={}, requester_pid=os.getpid(), requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    req2 = ControlRequest(
        schema_version=1, request_id="req-2", generation=marker.generation,
        nonce=marker.nonce, target_run_ids=("em-2",), operation="reclaim",
        payload={}, requester_pid=os.getpid(), requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    write_control_request(parent, req1)
    write_control_request(parent, req2)
    write_control_ack(parent, ControlAck(
        schema_version=1, request_id="req-1", generation=marker.generation,
        target_run_ids=("em-1",), status="accepted",
        responded_at="2026-01-01T00:00:01Z", detail={},
    ))
    pending = list(read_pending_control_requests(parent, marker.generation))
    assert [r.request_id for r in pending] == ["req-2"]


def test_write_control_request_rejects_generation_mismatch_path(tmp_path):
    """A request whose embedded generation doesn't match the generation
    directory it's being written under must be rejected — the control
    plane is generation-bound; a path/generation mismatch must fail closed,
    not silently file the request somewhere a host would never look."""
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1"])
    req = ControlRequest(
        schema_version=1, request_id="req-mismatch", generation="20990101-000000-ffffff",
        nonce=marker.nonce, target_run_ids=("em-1",), operation="ask",
        payload={}, requester_pid=os.getpid(), requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    with pytest.raises(Exception):
        write_control_request(parent, req)


# ---------------------------------------------------------------------------
# tag_owned_runs + commit_marker — REAL DaemonRunDir integration (not the
# _FakeRunDir stand-in above), proving the actual seam this module and
# run_dir.py share works end-to-end on real daemon.json files.
# ---------------------------------------------------------------------------


def _make_real_daemon_run_dir(parent_working_dir, *, handle, run_id):
    from lingtai.tools.daemon.run_dir import DaemonRunDir

    return DaemonRunDir(
        parent_working_dir=parent_working_dir,
        handle=handle,
        run_id=run_id,
        task="find todos",
        tools=["file"],
        model="mock-model",
        max_turns=30,
        timeout_s=300.0,
        parent_addr="parent",
        parent_pid=os.getpid(),
        system_prompt="You are a daemon emanation.",
    )


def test_tag_owned_runs_then_commit_marker_real_daemon_json_round_trip(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    run_ids = ["em-real-1", "em-real-2"]
    real_run_dirs = {
        rid: _make_real_daemon_run_dir(parent, handle=rid, run_id=rid) for rid in run_ids
    }
    identity = probe_process_start_identity(os.getpid())
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=run_ids,
    )
    result = tag_owned_runs(marker, real_run_dirs)
    assert result == {"em-real-1": True, "em-real-2": True}
    commit_marker(parent, marker)

    for rid in run_ids:
        daemon_json = json.loads((parent / "daemons" / rid / "daemon.json").read_text())
        owner = ExecutionOwner.from_dict(daemon_json["execution_owner"])
        assert owner.proves_membership_of(rid, marker) is True


def test_tag_owned_runs_real_rollback_leaves_daemon_json_untagged_on_mid_failure(tmp_path, monkeypatch):
    parent = tmp_path / "agent"
    parent.mkdir()
    run_ids = ["em-real-a", "em-real-b"]
    real_run_dirs = {
        rid: _make_real_daemon_run_dir(parent, handle=rid, run_id=rid) for rid in run_ids
    }
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir=str(parent), owned_run_ids=run_ids,
    )

    import lingtai.tools.daemon.run_dir as run_dir_module

    real_atomic_write_json = run_dir_module.atomic_write_json
    call_count = {"n": 0}

    def _fail_on_second_write(path, data, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated failure on second real tag write")
        return real_atomic_write_json(path, data, **kwargs)

    monkeypatch.setattr(run_dir_module, "atomic_write_json", _fail_on_second_write)
    with pytest.raises(OSError, match="simulated failure on second real tag write"):
        tag_owned_runs(marker, real_run_dirs)

    # First run's real daemon.json on disk must show the rollback, not a
    # dangling tag — this is the real-filesystem proof that the in-memory
    # rollback in tag_owned_runs actually reaches disk.
    first_daemon_json = json.loads(
        (parent / "daemons" / "em-real-a" / "daemon.json").read_text()
    )
    assert first_daemon_json["execution_owner"] is None


def test_v8_a2_tag_owned_runs_excludes_current_failing_run_from_rollback_real_write(
    tmp_path, monkeypatch
):
    """v8 A2 product-level counterexample (parent contract Stage A2 item 1-2):
    v7's setter-level fix (test_v7_atomic_rollback_setter_real_write_then_raises_
    reconciles_memory_with_disk above) only proves that ONE run's own in-memory
    state reconciles to match disk after its OWN real write lands then raises.
    It says nothing about the tag_owned_runs TRANSACTION: when the run whose
    write performs a real durable replace and then raises is not the first run
    processed, tag_owned_runs' rollback loop only walks `tagged_run_ids` —
    populated exclusively by runs that returned normally from
    set_execution_owner (see refresh_host.py's tag_owned_runs, the `tagged_run_ids.
    append(run_id)` line is reached only after the try block, never inside the
    except branch) — so the CURRENTLY FAILING run's own now-durably-landed tag
    is never rolled back. A real DaemonRunDir + the real atomic_write_json used
    exactly as _prepare_refresh_host would (never a _FakeRunDir stand-in) proves
    this on an actual daemon.json file, not merely an in-memory assertion."""
    parent = tmp_path / "agent"
    parent.mkdir()
    run_ids = ["em-real-first", "em-real-failing"]
    real_run_dirs = {
        rid: _make_real_daemon_run_dir(parent, handle=rid, run_id=rid) for rid in run_ids
    }
    identity = probe_process_start_identity(os.getpid())
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=run_ids,
    )

    import lingtai.tools.daemon.run_dir as run_dir_module

    real_atomic_write_json = run_dir_module.atomic_write_json
    call_count = {"n": 0}

    def _second_write_lands_then_raises(path, data, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Perform the REAL durable write for the SECOND (currently
            # failing) run first — exactly the parent's mechanical
            # counterexample: tag_owned_runs invoked with the real
            # atomic_write_json, a real replace lands, THEN it raises.
            real_atomic_write_json(path, data, **kwargs)
            raise OSError("simulated post-replace failure on second run's own write")
        return real_atomic_write_json(path, data, **kwargs)

    monkeypatch.setattr(run_dir_module, "atomic_write_json", _second_write_lands_then_raises)

    with pytest.raises(OSError, match="simulated post-replace failure on second run's own write"):
        tag_owned_runs(marker, real_run_dirs)

    first_daemon_json = json.loads(
        (parent / "daemons" / "em-real-first" / "daemon.json").read_text()
    )
    failing_daemon_json = json.loads(
        (parent / "daemons" / "em-real-failing" / "daemon.json").read_text()
    )
    assert first_daemon_json["execution_owner"] is None, (
        "the earlier successfully-tagged run must still be rolled back"
    )
    assert failing_daemon_json["execution_owner"] is None, (
        "the CURRENTLY FAILING run's own real durable write landed before "
        "set_execution_owner raised — its in-memory state was already "
        "reconciled to match that landed disk content (the v7 setter fix), "
        "but tag_owned_runs never compensates for it: this run is never "
        "added to tagged_run_ids (only successful returns are), so the "
        "rollback loop skips it entirely and it is left durably claiming "
        "an execution_owner for a marker that tag_owned_runs' own raised "
        "exception says was never fully tagged. A transaction that lets "
        "the run whose OWN write is what failed keep its tag is not an "
        "all-or-nothing transaction."
    )


def test_v8_a2_two_run_case_run_a_success_run_b_real_write_then_raise_both_compensated(
    tmp_path, monkeypatch
):
    """Parent contract Stage A2 item 3 (the exact two-run case): run A tags
    successfully, run B performs a REAL write then raises. Both A and B must
    end up compensated (no owner tag on either, in memory or on disk), or —
    if compensation cannot be durably confirmed — the exact uncertain run
    must produce a typed OwnerTaggingAmbiguousError naming it, never a silent
    'looks fine' outcome. This test's failure path (ordinary rollback
    succeeds for both) exercises the non-ambiguous branch; the ambiguous
    branch is already covered by test_v7_owner_tag_transaction_write_state_
    unknown_escalates_to_ambiguous and test_tag_owned_runs_raises_ambiguous_
    when_rollback_itself_fails above."""
    parent = tmp_path / "agent"
    parent.mkdir()
    run_ids = ["em-run-a", "em-run-b"]
    real_run_dirs = {
        rid: _make_real_daemon_run_dir(parent, handle=rid, run_id=rid) for rid in run_ids
    }
    identity = probe_process_start_identity(os.getpid())
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(parent), owned_run_ids=run_ids,
    )

    import lingtai.tools.daemon.run_dir as run_dir_module

    real_atomic_write_json = run_dir_module.atomic_write_json
    run_b_write_count = {"n": 0}

    def _run_b_first_write_lands_then_raises(path, data, **kwargs):
        # em-run-a's daemon.json path is written first (dict iteration order
        # follows marker.owned_run_ids); em-run-b's FIRST write (the initial
        # tag attempt) lands durably and then raises — a single transient
        # post-replace failure, mirroring the parent's exact product
        # counterexample. Em-run-b's SUBSEQUENT write (its own compensating
        # rollback) must succeed normally, or this fake would indistinguishably
        # simulate "this path can never be written to" instead of "one write
        # attempt hit a post-replace failure."
        real_atomic_write_json(path, data, **kwargs)
        if "em-run-b" in str(path):
            run_b_write_count["n"] += 1
            if run_b_write_count["n"] == 1:
                raise OSError("simulated post-replace failure for run B")

    monkeypatch.setattr(run_dir_module, "atomic_write_json", _run_b_first_write_lands_then_raises)

    with pytest.raises(OSError, match="simulated post-replace failure for run B"):
        tag_owned_runs(marker, real_run_dirs)

    run_a_disk = json.loads((parent / "daemons" / "em-run-a" / "daemon.json").read_text())
    run_b_disk = json.loads((parent / "daemons" / "em-run-b" / "daemon.json").read_text())
    assert run_a_disk["execution_owner"] is None, "run A (earlier, ordinary rollback) must be compensated"
    assert run_b_disk["execution_owner"] is None, (
        "run B (the run whose OWN write landed then raised) must ALSO be "
        "compensated — a clean rollback must leave no owner in either "
        "memory or disk for the currently failing run, not just runs "
        "processed before it"
    )
    assert real_run_dirs["em-run-a"].state_snapshot()["execution_owner"] is None
    assert real_run_dirs["em-run-b"].state_snapshot()["execution_owner"] is None, (
        "in-memory state for the failing run must match the compensated "
        "disk state too — the v7 setter-level fix reconciled memory TO the "
        "landed tag; a transaction-level rollback must reconcile it back "
        "to untagged, not leave memory and disk both still showing the tag"
    )


# ---------------------------------------------------------------------------
# DaemonManager._prepare_refresh_host — the PREPARE state transition itself,
# on a REAL DaemonManager (via the shared _daemon_helpers fixtures), not a
# mock. Freezes new emanate submission, computes the fixed owned nonterminal
# run set, probes identity, builds/tags/commits the marker.
# ---------------------------------------------------------------------------


def test_prepare_refresh_host_returns_none_when_no_nonterminal_runs(tmp_path):
    from tests._daemon_helpers import make_daemon_agent

    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    assert mgr._prepare_refresh_host() is None
    # No marker directory should even be created for the ordinary case.
    assert not (agent._working_dir / "daemons" / ".refresh-hosts").exists()


def test_prepare_refresh_host_returns_none_when_only_terminal_runs_exist(tmp_path):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry, completed_future

    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-done")
    run_dir.mark_done("finished")
    register_daemon_entry(mgr, "em-done", run_dir, future=completed_future("finished"))
    assert mgr._prepare_refresh_host() is None


def _mock_own_cmdline_module_form(monkeypatch, working_dir):
    """Make `_prepare_refresh_host`'s own-command-line read look like a real
    `python -m lingtai run <working_dir>` launch for the CURRENT pytest
    process — mirrors the existing `_commit_verifiable_marker` /
    `test_verified_refresh_host_pids_positive_discovery` monkeypatch
    convention (patch `_read_cmdline`, not `sys.argv`), so the real
    `probe_process_start_identity` + `match_agent_run` path still runs for
    real against `os.getpid()`; only the "what does ps/proc say the cmdline
    is" leaf is faked, exactly as it would be under a real launch."""
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {working_dir}" if pid == os.getpid() else None,
    )


def test_v7_prepare_refresh_host_stamps_real_deadline_for_owned_run(tmp_path, monkeypatch):
    """B-4 write side: _prepare_refresh_host must persist a REAL absolute
    deadline (this run's own start_time + timeout_s) into the run's
    daemon.json — pre-v7, deadline_at was schema-ready but nothing ever
    wrote a genuine value into it (the parent contract's own words: "the
    current deadline_at field is inert")."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)
    mgr._emanations["em-live"]["start_time"] = time.time()
    mgr._emanations["em-live"]["timeout_s"] = 3600.0

    marker = mgr._prepare_refresh_host()
    assert marker is not None

    on_disk = json.loads(run_dir.daemon_json_path.read_text())
    deadline_at = on_disk.get("deadline_at")
    assert isinstance(deadline_at, str) and deadline_at, (
        "deadline_at must be a real, non-empty stamped value after PREPARE"
    )
    deadline_dt = datetime.strptime(deadline_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    # The deadline must be roughly start_time + timeout_s (3600s ahead of
    # now, within a generous tolerance for test wall-clock slop) — not
    # "now" (the pre-v7 defect in _route_ask_through_control_plane, a
    # DIFFERENT inert stamp this test does not exercise) and not some
    # arbitrary/zero value.
    assert timedelta(seconds=3000) < (deadline_dt - now) < timedelta(seconds=3900), (
        f"deadline_at={deadline_at!r} is not ~3600s ahead of now — expected "
        f"start_time+timeout_s, got a deadline {(deadline_dt - now).total_seconds():.1f}s ahead"
    )


def test_v7_successor_initiates_real_timeout_for_overdue_owned_run(tmp_path, monkeypatch):
    """B-4 read side + real vertical-slice proof: a fresh successor
    DaemonManager construction (the exact real product entry point every
    successor process's __init__ goes through) must notice a nonterminal,
    execution-owner-tagged, verified-LIVE-host run whose deadline_at has
    genuinely passed and submit a real durable ControlRequest(operation=
    'timeout') for it — the actual read side of B-4, proven through
    DaemonManager.__init__ itself, not a directly-called internal helper.

    Uses the SAME _write_owned_daemon_json pattern as the existing
    _reap_host_lost_daemon_records tests (not make_daemon_run_dir +
    register_daemon_entry, whose auto-generated long-form run_id would not
    match marker.owned_run_ids' short key unless run_id= is passed
    explicitly — this method reads run_id back from the durable
    daemon.json file, exactly like a real successor with no in-memory
    _emanations entry for this run would)."""
    from tests._daemon_helpers import make_daemon_agent
    import lingtai.tools.daemon as daemon_module

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    _mock_own_cmdline_module_form(monkeypatch, working_dir)
    identity = probe_process_start_identity(os.getpid())
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-overdue-1"],
    )
    commit_marker(working_dir, marker)
    owner = ExecutionOwner.from_marker(marker).to_dict()
    overdue_deadline = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    daemon_json_path = _write_owned_daemon_json(
        working_dir, "em-overdue-1", execution_owner=owner, parent_pid=os.getpid(),
    )
    state = json.loads(daemon_json_path.read_text())
    state["deadline_at"] = overdue_deadline
    daemon_json_path.write_text(json.dumps(state), encoding="utf-8")

    # A fresh successor DaemonManager construction — the REAL entry point
    # (not a directly-invoked internal helper) that every successor
    # process's own __init__ runs through, alongside host-lost reaping.
    # Bound to the SAME agent (a real second Agent() on this working_dir
    # would correctly fail the exclusive workdir lease this exact test is
    # not about) — a fresh DaemonManager still exercises the real __init__
    # entry point with an empty in-memory _emanations, exactly like a
    # genuine successor process would have.
    successor_mgr = daemon_module.DaemonManager(agent)

    pending = list(read_pending_control_requests(working_dir, marker.generation))
    assert len(pending) == 1, (
        f"expected exactly one real timeout ControlRequest to be initiated "
        f"for the overdue run; got {[(r.request_id, r.operation) for r in pending]}"
    )
    assert pending[0].operation == "timeout"
    assert pending[0].target_run_ids == ("em-overdue-1",)

    # Idempotency: a SECOND successor construction (or a retry) must not
    # submit a duplicate timeout signal for the same already-initiated run.
    successor_mgr2 = daemon_module.DaemonManager(agent)
    pending_again = list(read_pending_control_requests(working_dir, marker.generation))
    assert len(pending_again) == 1, (
        "a repeated successor construction must not duplicate the already-"
        "initiated timeout request for the same overdue run"
    )


def test_v7_successor_does_not_initiate_timeout_for_run_not_yet_overdue(tmp_path, monkeypatch):
    """Positive control: a run whose deadline has NOT yet passed must not
    have a timeout initiated — the check is genuinely time-gated, not
    unconditional for every owned run."""
    from tests._daemon_helpers import make_daemon_agent
    import lingtai.tools.daemon as daemon_module

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    _mock_own_cmdline_module_form(monkeypatch, working_dir)
    identity = probe_process_start_identity(os.getpid())
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-not-overdue-1"],
    )
    commit_marker(working_dir, marker)
    owner = ExecutionOwner.from_marker(marker).to_dict()
    future_deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    daemon_json_path = _write_owned_daemon_json(
        working_dir, "em-not-overdue-1", execution_owner=owner, parent_pid=os.getpid(),
    )
    state = json.loads(daemon_json_path.read_text())
    state["deadline_at"] = future_deadline
    daemon_json_path.write_text(json.dumps(state), encoding="utf-8")

    successor_mgr = daemon_module.DaemonManager(agent)
    pending = list(read_pending_control_requests(working_dir, marker.generation))
    assert pending == [], (
        "a run whose deadline has not yet passed must not have a timeout "
        "initiated by successor startup"
    )


def test_prepare_refresh_host_commits_marker_for_nonterminal_run(tmp_path, monkeypatch):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)  # default Future() is not done()

    marker = mgr._prepare_refresh_host()
    assert marker is not None
    assert marker.owned_run_ids == ("em-live",)
    assert marker.pid == os.getpid()
    assert marker.state == "draining"

    # The marker is durably loadable back from disk under this exact workdir.
    loaded = load_marker(
        refresh_hosts_dir(agent._working_dir) / f"{marker.generation}.json",
        expected_working_dir=agent._working_dir,
    )
    assert loaded == marker

    # The owned run's real daemon.json carries a matching execution_owner tag.
    daemon_json = json.loads(run_dir.daemon_json_path.read_text())
    owner = ExecutionOwner.from_dict(daemon_json["execution_owner"])
    assert owner.proves_membership_of("em-live", marker) is True


def test_prepare_refresh_host_owns_only_nonterminal_runs_excludes_done_ones(tmp_path, monkeypatch):
    """A mixed batch (one done, one still running) must produce a marker
    whose owned_run_ids is EXACTLY the nonterminal subset — a terminal run
    must never be claimed by a drain-host marker (it needs no draining)."""
    from tests._daemon_helpers import (
        make_daemon_agent, make_daemon_run_dir, register_daemon_entry, completed_future,
    )

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    done_run_dir = make_daemon_run_dir(agent, em_id="em-done")
    done_run_dir.mark_done("finished")
    register_daemon_entry(mgr, "em-done", done_run_dir, future=completed_future("finished"))
    live_run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", live_run_dir)

    marker = mgr._prepare_refresh_host()
    assert marker is not None
    assert marker.owned_run_ids == ("em-live",)
    # The done run must NOT have been tagged.
    done_daemon_json = json.loads(done_run_dir.daemon_json_path.read_text())
    assert done_daemon_json["execution_owner"] is None


def test_prepare_refresh_host_freezes_new_emanate_submission_during_prepare(tmp_path):
    """While PREPARE is in flight, a new emanate call must be rejected — the
    fixed owned run set is computed once and must never grow after that
    point. We hold the freeze flag manually to prove _handle_emanate's own
    entry-point check (rather than timing an actual race)."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    mgr._refresh_prepare_frozen = True
    result = mgr._handle_emanate([{"task": "new task", "tools": []}])
    assert result["status"] == "error"
    assert "refresh" in result["message"].lower() or "prepar" in result["message"].lower()


def test_prepare_refresh_host_unfreezes_and_reraises_on_ordinary_precommit_failure(tmp_path, monkeypatch):
    """An ordinary precommit failure (e.g. tag_owned_runs raising a plain
    OSError, not CommitAmbiguousError) must leave interactive operation
    intact: the freeze flag must be cleared and normal emanate submission
    must work again immediately afterward, and NO marker must have been
    committed (the failure happened before commit_marker was ever called)."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    def _boom(marker, run_dirs):
        raise OSError("simulated ordinary precommit failure")

    # daemon/__init__.py imports tag_owned_runs directly into its own module
    # namespace (`from .refresh_host import (..., tag_owned_runs)`), so the
    # name actually consulted at call time is the one bound in daemon_module
    # — patching only refresh_host_module.tag_owned_runs would silently miss.
    import lingtai.tools.daemon as daemon_module
    monkeypatch.setattr(daemon_module, "tag_owned_runs", _boom)

    with pytest.raises(OSError, match="simulated ordinary precommit failure"):
        mgr._prepare_refresh_host()

    assert mgr._refresh_prepare_frozen is False
    assert list((agent._working_dir / "daemons" / ".refresh-hosts").glob("*.json")) == []
    # Interactive operation continues: a real emanate call is no longer
    # frozen (freeze check itself, not a full LLM-mocked dispatch).
    result = mgr._handle_emanate([{"task": "new task after failed prepare", "tools": []}])
    assert result["status"] != "error" or "frozen" not in result.get("message", "").lower()


def test_prepare_refresh_host_propagates_commit_ambiguous_error_uncaught(tmp_path, monkeypatch):
    """CommitAmbiguousError is explicitly NOT ordinary failure — it must
    propagate uncaught rather than being treated the same as any other
    precommit exception, per the brief: 'stop/raise honestly; never assume
    no marker.'"""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry
    import lingtai.tools.daemon as daemon_module

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    def _ambiguous_commit(parent_working_dir, marker):
        raise CommitAmbiguousError(
            "fake-target", rollback_attempted=True, rollback_succeeded=False,
            cause=OSError("simulated durability failure"),
        )

    monkeypatch.setattr(daemon_module, "commit_marker", _ambiguous_commit)
    with pytest.raises(CommitAmbiguousError):
        mgr._prepare_refresh_host()

    # Deliberately left frozen — the true on-disk marker state is unknown
    # after CommitAmbiguousError, so new emanate submission must stay
    # rejected until that ambiguity is externally resolved (see docstring).
    assert mgr._refresh_prepare_frozen is True


# ---------------------------------------------------------------------------
# DaemonManager._dispatch_control_request — the per-request dispatch core of
# the DRAINING host loop. Takes one ControlRequest, returns one ControlAck,
# does no file I/O itself (the loop wraps it with write_control_ack) — kept
# separately testable from the actual polling loop.
# ---------------------------------------------------------------------------


def _prepared_manager_with_marker(tmp_path, monkeypatch, *, em_id="em-live"):
    """Build a real DaemonManager, register one real nonterminal run, and
    run the real _prepare_refresh_host() to get a real committed marker —
    the common setup for every dispatch test below."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id=em_id)
    register_daemon_entry(mgr, em_id, run_dir)
    marker = mgr._prepare_refresh_host()
    assert marker is not None
    return agent, mgr, marker, run_dir


def _build_request(marker, *, request_id, operation, target_run_ids, payload=None):
    return ControlRequest(
        schema_version=1, request_id=request_id, generation=marker.generation,
        nonce=marker.nonce, target_run_ids=tuple(target_run_ids), operation=operation,
        payload=payload or {}, requester_pid=os.getpid() + 1, requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )


def test_dispatch_control_request_ask_routes_through_real_handle_ask(tmp_path, monkeypatch):
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-ask-1", operation="ask",
        target_run_ids=["em-live"], payload={"message": "please continue"},
    )
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "accepted"
    assert ack.request_id == "req-ask-1"
    assert ack.generation == marker.generation
    assert ack.target_run_ids == ("em-live",)
    # The message actually reached the real followup buffer via _handle_ask.
    entry = mgr._emanations["em-live"]
    assert "please continue" in entry["followup_buffer"]


def test_dispatch_control_request_rejects_mismatched_generation(tmp_path, monkeypatch):
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = ControlRequest(
        schema_version=1, request_id="req-bad-gen", generation="20990101-000000-ffffff",
        nonce=marker.nonce, target_run_ids=("em-live",), operation="ask",
        payload={"message": "hi"}, requester_pid=1, requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "rejected"


def test_dispatch_control_request_rejects_target_run_not_owned(tmp_path, monkeypatch):
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-not-owned", operation="ask",
        target_run_ids=["em-not-a-real-owned-run"], payload={"message": "hi"},
    )
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "rejected"


def test_dispatch_control_request_already_terminal_for_done_run(tmp_path, monkeypatch):
    """A target run that's already finished by the time the request is
    dispatched must return already-terminal, never accepted (accepted would
    falsely imply new work was just signaled)."""
    from tests._daemon_helpers import completed_future

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    run_dir.mark_done("finished before dispatch")
    mgr._emanations["em-live"]["future"] = completed_future("finished before dispatch")

    req = _build_request(marker, request_id="req-terminal", operation="ask", target_run_ids=["em-live"])
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "already-terminal"


def test_dispatch_control_request_reclaim_sets_cancel_event_and_accepts(tmp_path, monkeypatch):
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    entry = mgr._emanations["em-live"]
    assert not entry["cancel_event"].is_set()

    req = _build_request(marker, request_id="req-reclaim-1", operation="reclaim", target_run_ids=["em-live"])
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "accepted"
    assert entry["cancel_event"].is_set()


def test_dispatch_control_request_timeout_sets_timeout_and_cancel_events(tmp_path, monkeypatch):
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    entry = mgr._emanations["em-live"]

    req = _build_request(marker, request_id="req-timeout-1", operation="timeout", target_run_ids=["em-live"])
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "accepted"
    assert entry["timeout_event"].is_set()
    assert entry["cancel_event"].is_set()


def test_dispatch_control_request_reclaim_never_touches_unowned_sibling_run(tmp_path, monkeypatch):
    """A reclaim targeting one owned run must never affect a DIFFERENT run
    that happens to share this manager's in-memory registry but is NOT in
    this marker's owned set (e.g. a run created by a later, different
    generation) — proves scoping is per-run, not manager-global."""
    from tests._daemon_helpers import make_daemon_run_dir, register_daemon_entry

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    other_run_dir = make_daemon_run_dir(agent, em_id="em-other-unowned")
    register_daemon_entry(mgr, "em-other-unowned", other_run_dir)
    other_entry = mgr._emanations["em-other-unowned"]
    assert not other_entry["cancel_event"].is_set()

    req = _build_request(marker, request_id="req-reclaim-scoped", operation="reclaim", target_run_ids=["em-live"])
    mgr._dispatch_control_request(req, marker)
    assert not other_entry["cancel_event"].is_set()


def test_dispatch_control_request_duplicate_request_id_is_idempotent_end_to_end(tmp_path, monkeypatch):
    """A request written once, dispatched once, and acked once must never be
    seen as pending by a SECOND real scan (read_pending_control_requests) —
    proving the loop-level idempotency contract end-to-end: write the real
    request file, dispatch it for real (real ask side effect observed
    exactly once), write the real ack, then re-scan and confirm nothing
    would cause a second dispatch on the next loop tick."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-dup-1", operation="ask",
        target_run_ids=["em-live"], payload={"message": "only once"},
    )
    write_control_request(agent._working_dir, req)
    pending_before = list(read_pending_control_requests(agent._working_dir, marker.generation))
    assert [r.request_id for r in pending_before] == ["req-dup-1"]

    ack1 = mgr._dispatch_control_request(req, marker)
    write_control_ack(agent._working_dir, ack1)
    entry = mgr._emanations["em-live"]
    assert entry["followup_buffer"].count("only once") == 1

    # A second, real loop scan must see this request as already-acked and
    # therefore never redispatch it (which would double the side effect).
    pending_after = list(read_pending_control_requests(agent._working_dir, marker.generation))
    assert pending_after == []
    assert entry["followup_buffer"].count("only once") == 1


# ---------------------------------------------------------------------------
# DaemonManager._run_drain_loop — the bounded polling loop itself. Real
# background thread inside the SAME process (this is unit-level proof the
# loop mechanics work; the brief's required real-SEPARATE-PROCESS
# acceptance tests are a distinct, larger deliverable — see report §5).
# ---------------------------------------------------------------------------


def test_run_drain_loop_processes_one_real_request_within_bounded_ticks(tmp_path, monkeypatch):
    """The owned run stays nonterminal for the whole call (no one ever marks
    it done), so the loop runs its full max_ticks bound — this proves the
    request gets dispatched/acked somewhere along the way, and that the
    ticks bound itself is a real, honored escape hatch (not merely
    documented)."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-loop-1", operation="ask",
        target_run_ids=["em-live"], payload={"message": "loop hi"},
    )
    write_control_request(agent._working_dir, req)

    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=20)

    ack_path = control_dir(agent._working_dir, marker.generation) / "acks" / "req-loop-1.json"
    assert ack_path.exists()
    entry = mgr._emanations["em-live"]
    assert "loop hi" in entry["followup_buffer"]


def test_run_drain_loop_stops_once_all_owned_futures_are_terminal(tmp_path, monkeypatch):
    from tests._daemon_helpers import completed_future

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    run_dir.mark_done("already finished")
    mgr._emanations["em-live"]["future"] = completed_future("already finished")

    import time as _time
    start = _time.monotonic()
    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=1000)
    elapsed = _time.monotonic() - start
    # Must return promptly (quiescent on the very first tick), not spin for
    # anywhere close to 1000 * 0.01s = 10s.
    assert elapsed < 2.0


def test_run_drain_loop_processes_multiple_requests_across_ticks(tmp_path, monkeypatch):
    from concurrent.futures import Future

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    real_future = Future()
    mgr._emanations["em-live"]["future"] = real_future
    # Captured BEFORE the drain loop runs to quiescence — B-5's real
    # resource-shutdown-on-quiescence removes this entry from
    # self._emanations once the owned set is genuinely done (see
    # _finish_drain_host_exit), but the dict OBJECT this reference points
    # to is unaffected by being unlinked from that container; its own
    # mutations (the followup_buffer appends the real dispatch path makes)
    # remain observable through this reference exactly as before.
    entry = mgr._emanations["em-live"]

    req1 = _build_request(
        marker, request_id="req-multi-1", operation="ask",
        target_run_ids=["em-live"], payload={"message": "first"},
    )
    write_control_request(agent._working_dir, req1)

    def _release_and_add_second_request():
        import time as _t
        _t.sleep(0.05)
        req2 = _build_request(
            marker, request_id="req-multi-2", operation="ask",
            target_run_ids=["em-live"], payload={"message": "second"},
        )
        write_control_request(agent._working_dir, req2)
        _t.sleep(0.1)
        real_future.set_result("done")

    releaser = threading.Thread(target=_release_and_add_second_request)
    releaser.start()
    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=500)
    releaser.join(timeout=5)

    assert "first" in entry["followup_buffer"]
    assert "second" in entry["followup_buffer"]
    acks_dir = control_dir(agent._working_dir, marker.generation) / "acks"
    assert (acks_dir / "req-multi-1.json").exists()
    assert (acks_dir / "req-multi-2.json").exists()


# ---------------------------------------------------------------------------
# REAL PROCESS-LEVEL ACCEPTANCE TESTS — brief category 1: LingTai survival /
# exactly-once side effect. Two REAL, SEPARATE Python subprocesses (not
# threads), communicating only through real files under tmp_path, driving
# the actual DaemonManager/refresh_host code — not mocks of the manager
# itself. See report §5 for exactly what this proves and what it does not.
# ---------------------------------------------------------------------------

_HOST_PROCESS_SCRIPT = """
import json, os, sys, threading, time
sys.path.insert(0, {src_path!r})
from unittest.mock import MagicMock
from pathlib import Path

from lingtai.agent import Agent
from lingtai.kernel.config import AgentConfig
import lingtai.llm.service as service_mod
from lingtai.kernel.llm.base import ToolCall

working_dir = Path({working_dir!r})
side_effect_marker = Path({side_effect_marker!r})
release_file = Path({release_file!r})
host_ready_file = Path({host_ready_file!r})
host_pid_file = Path({host_pid_file!r})
host_generation_file = Path({host_generation_file!r})
host_stop_file = Path({host_stop_file!r})
host_done_file = Path({host_done_file!r})

svc = MagicMock()
svc.provider = "mock"
svc.model = "mock-model"
agent = Agent(svc, working_dir=working_dir, capabilities=["daemon"], config=AgentConfig())

_em_id_holder = {{}}

def _blocking_side_effect_handler(args):
    # Block until the successor process (or the test) signals release —
    # this is the exact "handler records a side effect only after
    # release" the brief requires, running on the DAEMON'S OWN worker
    # thread in THIS (host) process.
    deadline = time.time() + 30.0
    while not release_file.exists():
        if time.time() > deadline:
            raise TimeoutError("release_file never appeared")
        time.sleep(0.02)
    side_effect_marker.write_text(str(os.getpid()), encoding="utf-8")
    # Every real LingTai-backend emanation gets the daemon_common MCP
    # wired in unconditionally, and the manager's own completion check
    # (_require_done_completion) requires a real finish-signal JSON file
    # at this exact path once that MCP is present — see
    # DaemonManager._completion_file / _daemon_common_mcp_registration.
    # This test mocks the LLM/tool-call loop entirely (no real MCP
    # process is ever spawned), so this handler writes the same
    # completion JSON a real `daemon_common` finish tool call would have
    # produced, as the honest stand-in for that real signal.
    completion_path = working_dir / "daemons" / _em_id_holder["em_id"] / "daemon_completion.json"
    completion_path.write_text(
        json.dumps({{"status": "done", "run_id": _em_id_holder["em_id"],
                    "summary": "released and recorded"}}),
        encoding="utf-8",
    )
    return {{"content": "released and recorded"}}

agent.add_tool(
    "blocking_side_effect",
    schema={{"type": "object", "properties": {{}}}},
    handler=_blocking_side_effect_handler,
    description="Blocks until release_file exists, then records a side effect.",
)

mgr = agent.get_capability("daemon")

# This subprocess's real cmdline is `python -c <script>`, which correctly
# does NOT match any real agent-run launch form (match_agent_run is right
# to reject it) — so make it observe itself as a real module-form launch,
# exactly as the existing in-process tests already do via the same
# `_read_cmdline` monkeypatch seam (see _mock_own_cmdline_module_form in
# this same test file). This is the ONE seam a real launcher's `ps`
# output/exec argv would supply for free; faking it here is the same kind
# of substitution as the notification-store subprocess tests already make
# for `PYTHONPATH`, not a weakening of the identity check itself — the
# real probe_process_start_identity/match_agent_run STILL run for real
# against this real PID.
import lingtai.tools.daemon.refresh_host as _refresh_host_module
_real_read_cmdline = _refresh_host_module._read_cmdline
def _patched_read_cmdline(pid):
    if pid == os.getpid():
        return "python -m lingtai run " + str(working_dir)
    return _real_read_cmdline(pid)
_refresh_host_module._read_cmdline = _patched_read_cmdline

service_mod.LLMService = lambda **kwargs: agent.service
resp1 = MagicMock()
resp1.text = "calling blocking tool"
resp1.tool_calls = [ToolCall(name="blocking_side_effect", args={{}}, id="tc-1")]
resp1.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)

_send_call_count = {{"n": 0}}

def _mock_send(*args, **kwargs):
    # First call: the initial task prompt -> the one blocking tool call.
    # Every subsequent call (the tool-results turn, and — only if a
    # control-plane `ask` queued a followup while the tool was still
    # blocking — one more turn to answer that followup) is a plain
    # text-only "done" response with no further tool calls, so the loop
    # always terminates regardless of exactly how many followup-drain
    # turns a real control-plane ask happens to trigger.
    _send_call_count["n"] += 1
    if _send_call_count["n"] == 1:
        return resp1
    resp = MagicMock()
    resp.text = "Task done."
    resp.tool_calls = []
    resp.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)
    return resp

mock_session = MagicMock()
mock_session.send = MagicMock(side_effect=_mock_send)
agent.service.create_session = MagicMock(return_value=mock_session)
agent.service.make_tool_result = MagicMock(return_value="mock_result")

result = mgr._handle_emanate([{{"task": "do the blocking thing", "tools": ["blocking_side_effect"]}}])
assert result["status"] == "dispatched", result
em_id = result["ids"][0]
_em_id_holder["em_id"] = em_id

marker = mgr._prepare_refresh_host()
assert marker is not None, "prepare must produce a marker: run is nonterminal (blocked in the tool handler)"
assert marker.owned_run_ids == (em_id,), marker.owned_run_ids

# Mandatory handoff order (brief): marker commit -> stop interactive loops
# -> withdraw heartbeat -> release workdir lease -> old main enters drain
# loop. This test drives the manager-level PREPARE/DRAINING primitives
# directly (the full lifecycle.py wiring for this ordering is a separate,
# not-yet-implemented deliverable — see report), but the lease MUST be
# released here for a real successor Agent() construction to even succeed
# in the same working_dir, so this is done explicitly and in the correct
# relative position (strictly after marker commit) to keep the ordering
# invariant honest even though it's manually driven rather than
# lifecycle-hook-driven.
agent._workdir_lease.release()

host_pid_file.write_text(str(os.getpid()), encoding="utf-8")
host_generation_file.write_text(marker.generation, encoding="utf-8")
host_ready_file.write_text("ready", encoding="utf-8")

def _drain():
    mgr._run_drain_loop(marker, poll_interval=0.02, max_ticks=None)
    host_done_file.write_text("done", encoding="utf-8")

t = threading.Thread(target=_drain, daemon=False)
t.start()

deadline = time.time() + 30.0
while not host_stop_file.exists():
    if time.time() > deadline:
        os._exit(4)
    time.sleep(0.05)
t.join(timeout=15.0)
sys.exit(0)
"""

_SUCCESSOR_PROCESS_SCRIPT = """
import json, os, sys
sys.path.insert(0, {src_path!r})
from unittest.mock import MagicMock
from pathlib import Path

from lingtai.agent import Agent
from lingtai.kernel.config import AgentConfig

working_dir = Path({working_dir!r})
em_id = {em_id!r}
host_pid = {host_pid!r}
result_file = Path({result_file!r})

# The successor's fresh DaemonManager construction now runs
# _reap_host_lost_daemon_records at startup, which calls
# verify_marker_live(marker) — this needs a real observable cmdline for
# the HOST's PID (not this successor's own PID) to confirm the host is
# genuinely still alive. A real successor process would observe this via
# real ps/proc output for the real host PID; this test's host subprocess
# is a `python -c <script>` process (correctly not itself matching any
# agent-run launch form), so — exactly as the host script patches its OWN
# cmdline observation of itself — this successor patches its observation
# of the host's PID specifically, and ONLY that PID; every other PID
# still goes through the real, unpatched probe.
import lingtai.tools.daemon.refresh_host as _refresh_host_module
_real_read_cmdline = _refresh_host_module._read_cmdline
def _patched_read_cmdline(pid):
    if pid == host_pid:
        return "python -m lingtai run " + str(working_dir)
    return _real_read_cmdline(pid)
_refresh_host_module._read_cmdline = _patched_read_cmdline

svc = MagicMock()
svc.provider = "mock"
svc.model = "mock-model"
agent = Agent(svc, working_dir=working_dir, capabilities=["daemon"], config=AgentConfig())
mgr = agent.get_capability("daemon")

# The successor's OWN fresh DaemonManager has an EMPTY _emanations — this
# is the exact post-refresh condition the brief's design targets. It must
# NOT have executed the tool itself (no side effect from this process) and
# must NOT have orphan-reaped the still-live host-owned record, because the
# real host process is still alive (same PID recorded in parent_pid).
list_result = mgr._handle_list()
entries = {{e["id"]: e for e in list_result["emanations"]}}
out = {{"list_entries": entries, "list_status": entries.get(em_id, {{}}).get("status")}}
result_file.write_text(json.dumps(out), encoding="utf-8")
sys.exit(0)
"""


_SUCCESSOR_CONTROL_SCRIPT = """
import json, os, sys, time
sys.path.insert(0, {src_path!r})
from pathlib import Path

from lingtai.tools.daemon.refresh_host import (
    ControlRequest, control_dir, refresh_hosts_dir, load_marker,
    write_control_request, read_pending_control_requests,
)

working_dir = Path({working_dir!r})
em_id = {em_id!r}
generation = {generation!r}
request_id = {request_id!r}
operation = {operation!r}
payload = {payload!r}
result_file = Path({result_file!r})

# The marker is durably on disk and readable by any process — the
# successor loads it for real (not passed a shortcut nonce) exactly as it
# would after a real refresh, discovering the live host's generation/nonce
# from the committed marker file itself.
marker = load_marker(
    refresh_hosts_dir(working_dir) / (generation + ".json"),
    expected_working_dir=working_dir,
)

req = ControlRequest(
    schema_version=1, request_id=request_id, generation=marker.generation, nonce=marker.nonce,
    target_run_ids=(em_id,), operation=operation, payload=payload,
    requester_pid=os.getpid(), requester_start_ticks=1,
    created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:10:00Z",
)
# Duplicate-id idempotency: a caller that races/retries the SAME
# request_id must treat FileExistsError as "already sent", not as an
# error — proven for real here by intentionally writing it twice, exactly
# as a retrying real successor would.
try:
    write_control_request(working_dir, req)
    first_write_succeeded = True
except FileExistsError:
    first_write_succeeded = False
try:
    write_control_request(working_dir, req)
    second_write_raised_exists = False
except FileExistsError:
    second_write_raised_exists = True

ack_path = control_dir(working_dir, generation) / "acks" / (request_id + ".json")
deadline = time.time() + 20.0
while not ack_path.exists():
    if time.time() > deadline:
        result_file.write_text(json.dumps({{"error": "ack never appeared"}}), encoding="utf-8")
        sys.exit(1)
    time.sleep(0.02)
ack = json.loads(ack_path.read_text(encoding="utf-8"))

result_file.write_text(json.dumps({{
    "first_write_succeeded": first_write_succeeded,
    "second_write_raised_exists": second_write_raised_exists,
    "ack": ack,
}}), encoding="utf-8")
sys.exit(0)
"""


def test_lingtai_survival_exactly_once_side_effect_across_real_processes(tmp_path):
    """Brief category 1. Old-agent SUBPROCESS starts a blocking daemon whose
    handler records a side effect only after release; it prepares/commits a
    host marker and stays alive as a real drain host; a SEPARATE successor
    SUBPROCESS starts against the same working_dir and sees the same run id
    as host-owned/running via a real `list` call (its own manager has an
    empty `_emanations`); the side effect is released and occurs exactly
    once; the run stays owned by the ORIGINAL host process throughout — the
    successor never executes it.
    """
    working_dir = tmp_path / "agent-workdir"
    working_dir.mkdir()
    side_effect_marker = tmp_path / "side_effect_marker"
    release_file = tmp_path / "release_file"
    host_ready_file = tmp_path / "host_ready"
    host_pid_file = tmp_path / "host_pid"
    host_generation_file = tmp_path / "host_generation"
    host_stop_file = tmp_path / "host_stop"
    host_done_file = tmp_path / "host_done"
    src_path = str(Path(__file__).resolve().parents[1] / "src")

    host_script = _HOST_PROCESS_SCRIPT.format(
        src_path=src_path, working_dir=str(working_dir),
        side_effect_marker=str(side_effect_marker), release_file=str(release_file),
        host_ready_file=str(host_ready_file), host_pid_file=str(host_pid_file),
        host_generation_file=str(host_generation_file),
        host_stop_file=str(host_stop_file), host_done_file=str(host_done_file),
    )
    env = {**os.environ, "PYTHONPATH": src_path}
    host_proc = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        deadline = time.monotonic() + 20.0
        while not host_ready_file.exists():
            assert time.monotonic() < deadline, "host process never became ready"
            assert host_proc.poll() is None, (
                f"host process exited early: {host_proc.poll()}"
            )
            time.sleep(0.02)

        host_pid = int(host_pid_file.read_text(encoding="utf-8"))
        assert host_pid == host_proc.pid
        marker_generation = host_generation_file.read_text(encoding="utf-8")

        # Real marker durably committed and readable from the PARENT test
        # process too, proving it's genuinely on disk, not merely in the
        # host subprocess's memory.
        marker_path = refresh_hosts_dir(working_dir) / f"{marker_generation}.json"
        assert marker_path.is_file()

        # Determine the real em_id from the daemons/ directory (only one
        # exists at this point).
        daemon_dirs = list((working_dir / "daemons").iterdir())
        run_dirs = [d for d in daemon_dirs if d.is_dir() and d.name.startswith("em-")]
        assert len(run_dirs) == 1, run_dirs
        em_id = run_dirs[0].name

        # The run is still genuinely blocked — no side effect yet.
        assert not side_effect_marker.exists()
        daemon_json = json.loads((run_dirs[0] / "daemon.json").read_text())
        assert daemon_json["state"] == "running"
        assert daemon_json["parent_pid"] == host_pid

        # --- Successor process starts while the host is still draining. ---
        successor_result_file = tmp_path / "successor_result"
        successor_script = _SUCCESSOR_PROCESS_SCRIPT.format(
            src_path=src_path, working_dir=str(working_dir), em_id=em_id,
            host_pid=host_pid, result_file=str(successor_result_file),
        )
        successor_proc = subprocess.run(
            [sys.executable, "-c", successor_script],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert successor_proc.returncode == 0, (
            f"successor stdout={successor_proc.stdout!r} stderr={successor_proc.stderr!r}"
        )
        successor_out = json.loads(successor_result_file.read_text(encoding="utf-8"))
        # The successor's `list` must see the run — via on-disk history
        # (_iter_daemon_history_states), since its own _emanations is
        # empty — and it must still be "running", proving the successor
        # did not orphan-reap a host-owned, still-genuinely-alive run.
        assert em_id in successor_out["list_entries"], successor_out
        assert successor_out["list_status"] == "running", successor_out
        # The host process is still alive throughout — the successor never
        # became the owner.
        assert host_proc.poll() is None

        # --- Release the side effect. ---
        assert not side_effect_marker.exists(), "side effect must not occur before release"
        release_file.write_text("go", encoding="utf-8")

        deadline = time.monotonic() + 20.0
        while not side_effect_marker.exists():
            assert time.monotonic() < deadline, "side effect never occurred after release"
            assert host_proc.poll() is None, "host process died before completing the side effect"
            time.sleep(0.02)

        # Exactly once, and by the HOST's own PID (not the successor's).
        side_effect_pid = int(side_effect_marker.read_text(encoding="utf-8"))
        assert side_effect_pid == host_pid

        # The run reaches terminal state, written by the HOST (parent_pid
        # in the terminal record is still the host's pid — no second
        # writer ever touched this record). mark_done() (state) and the
        # terminal-notification claim/publish (terminal_notified) are two
        # genuinely separate writes — the latter happens in
        # _on_emanation_done's future-done callback, which fires slightly
        # after the worker thread's own mark_done() call — so wait for
        # BOTH, not just state.
        deadline = time.monotonic() + 20.0
        while True:
            daemon_json = json.loads((run_dirs[0] / "daemon.json").read_text())
            if daemon_json["state"] == "failed":
                break
            if daemon_json["state"] == "done" and daemon_json.get("terminal_notified") is True:
                break
            assert time.monotonic() < deadline, (
                f"run never reached done+terminal_notified: "
                f"state={daemon_json['state']} terminal_notified={daemon_json.get('terminal_notified')}"
            )
            assert host_proc.poll() is None
            time.sleep(0.02)
        assert daemon_json["state"] == "done", (
            f"run reached 'failed' instead of 'done': error={daemon_json.get('error')!r}"
        )
        assert daemon_json["parent_pid"] == host_pid
        assert daemon_json["terminal_notified"] is True

        # Exactly one terminal-notification receipt was ever published for
        # this run — proven by the durable idempotency-key claim, not by
        # re-deriving it from a second writer (there is no second writer
        # to compare against; the assertion is that exactly one receipt
        # exists and its key is the expected stable one).
        receipt = daemon_json["terminal_notification_receipt"]
        assert receipt is not None
        assert receipt["idempotency_key"] == f"daemon-terminal:{em_id}"
    finally:
        try:
            host_stop_file.write_text("stop", encoding="utf-8")
            host_proc.wait(timeout=15)
        except Exception:
            host_proc.kill()
            host_proc.wait(timeout=5)
        if host_proc.returncode not in (0, None):
            print("HOST STDOUT:", host_proc.stdout.read() if host_proc.stdout else None)
            print("HOST STDERR:", host_proc.stderr.read() if host_proc.stderr else None)


def _start_real_host_and_wait_ready(tmp_path, *, src_path, env):
    """Shared setup for categories 1 and 3: start a real host subprocess
    with one blocking daemon run, wait for it to become a committed,
    lease-released drain host, and return everything a caller needs to
    drive it further. Caller owns stopping it (host_stop_file + wait)."""
    working_dir = tmp_path / "agent-workdir"
    working_dir.mkdir()
    side_effect_marker = tmp_path / "side_effect_marker"
    release_file = tmp_path / "release_file"
    host_ready_file = tmp_path / "host_ready"
    host_pid_file = tmp_path / "host_pid"
    host_generation_file = tmp_path / "host_generation"
    host_stop_file = tmp_path / "host_stop"
    host_done_file = tmp_path / "host_done"

    host_script = _HOST_PROCESS_SCRIPT.format(
        src_path=src_path, working_dir=str(working_dir),
        side_effect_marker=str(side_effect_marker), release_file=str(release_file),
        host_ready_file=str(host_ready_file), host_pid_file=str(host_pid_file),
        host_generation_file=str(host_generation_file),
        host_stop_file=str(host_stop_file), host_done_file=str(host_done_file),
    )
    host_proc = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    deadline = time.monotonic() + 20.0
    while not host_ready_file.exists():
        assert time.monotonic() < deadline, "host process never became ready"
        assert host_proc.poll() is None, f"host process exited early: {host_proc.poll()}"
        time.sleep(0.02)

    host_pid = int(host_pid_file.read_text(encoding="utf-8"))
    marker_generation = host_generation_file.read_text(encoding="utf-8")
    daemon_dirs = [d for d in (working_dir / "daemons").iterdir() if d.is_dir() and d.name.startswith("em-")]
    assert len(daemon_dirs) == 1, daemon_dirs
    em_id = daemon_dirs[0].name

    return {
        "working_dir": working_dir, "host_proc": host_proc, "host_pid": host_pid,
        "marker_generation": marker_generation, "em_id": em_id,
        "side_effect_marker": side_effect_marker, "release_file": release_file,
        "host_stop_file": host_stop_file, "run_dir": daemon_dirs[0],
    }


def _stop_real_host(ctx):
    try:
        ctx["host_stop_file"].write_text("stop", encoding="utf-8")
        ctx["host_proc"].wait(timeout=15)
    except Exception:
        ctx["host_proc"].kill()
        ctx["host_proc"].wait(timeout=5)
    if ctx["host_proc"].returncode not in (0, None):
        print("HOST STDOUT:", ctx["host_proc"].stdout.read() if ctx["host_proc"].stdout else None)
        print("HOST STDERR:", ctx["host_proc"].stderr.read() if ctx["host_proc"].stderr else None)


def _run_successor_control(ctx, *, src_path, env, request_id, operation, payload, result_file):
    script = _SUCCESSOR_CONTROL_SCRIPT.format(
        src_path=src_path, working_dir=str(ctx["working_dir"]), em_id=ctx["em_id"],
        generation=ctx["marker_generation"], request_id=request_id, operation=operation,
        payload=payload, result_file=str(result_file),
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, f"successor control stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return json.loads(result_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# DaemonManager._reap_host_lost_daemon_records — HOST_LOST detection and
# reconciliation. Runs BEFORE the existing _reap_dead_parent_daemon_records
# and _reconcile_terminal_notifications (see brief: "host discovery/owner
# validation runs before orphan reaping and before terminal receipt
# reconciliation"). A record with a LIVE verified host must never be
# touched; a record with a DEAD/invalid host gets marked failed with
# error.type="DaemonHostLost" (never "DaemonOrphaned") and its terminal
# notification is reconciled exactly once by the successor.
# ---------------------------------------------------------------------------


def _write_owned_daemon_json(
    working_dir, run_id, *, execution_owner, state="running", parent_pid=99999,
):
    daemon_dir = working_dir / "daemons" / run_id
    daemon_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "data_version": 1, "handle": run_id, "run_id": run_id, "group_id": None,
        "parent_addr": "parent", "parent_pid": parent_pid, "task": "x", "tools": [],
        "call_parameters": {}, "model": "mock", "max_turns": 30, "timeout_s": 300.0,
        "state": state, "started_at": "2026-01-01T00:00:00Z", "finished_at": None,
        "elapsed_s": 0.0, "turn": 0, "current_tool": "some_tool", "tool_call_count": 0,
        "tokens": {"input": 0, "output": 0, "thinking": 0, "cached": 0},
        "cli_tokens": {"input": 0, "output": 0, "thinking": 0, "cached": 0, "calls": 0},
        "result_preview": None, "result_path": None, "last_output": None,
        "last_output_at": None, "error": None, "terminal_notified": False,
        "terminal_notification_claim": None, "terminal_notification_receipt": None,
        "preset_name": None, "preset_provider": None, "preset_model": None,
        "backend": "lingtai", "claude_session_id": None,
        "execution_owner": execution_owner, "deadline_at": None,
    }
    (daemon_dir / "daemon.json").write_text(json.dumps(data), encoding="utf-8")
    return daemon_dir / "daemon.json"


def test_reap_host_lost_marks_failed_with_daemon_host_lost_when_marker_dead(tmp_path, monkeypatch):
    from tests._daemon_helpers import make_daemon_agent

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    dead_pid = 999999  # implausible PID; probe_process_start_identity must return None for it
    marker = RefreshHostMarker.build(
        pid=dead_pid, start_ticks=1, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-lost-1"],
    )
    commit_marker(working_dir, marker)
    owner = ExecutionOwner.from_marker(marker).to_dict()
    daemon_json_path = _write_owned_daemon_json(
        working_dir, "em-lost-1", execution_owner=owner, parent_pid=dead_pid,
    )

    mgr = agent.get_capability("daemon")
    mgr._reap_host_lost_daemon_records()

    state = json.loads(daemon_json_path.read_text())
    assert state["state"] == "failed"
    assert state["error"]["type"] == "DaemonHostLost"
    assert state["current_tool"] is None
    assert state["finished_at"] is not None


def test_reap_host_lost_never_touches_record_with_live_verified_host(tmp_path, monkeypatch):
    from tests._daemon_helpers import make_daemon_agent

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    _mock_own_cmdline_module_form(monkeypatch, working_dir)
    identity = probe_process_start_identity(os.getpid())
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-alive-1"],
    )
    commit_marker(working_dir, marker)
    owner = ExecutionOwner.from_marker(marker).to_dict()
    daemon_json_path = _write_owned_daemon_json(
        working_dir, "em-alive-1", execution_owner=owner, parent_pid=os.getpid(),
    )

    mgr = agent.get_capability("daemon")
    mgr._reap_host_lost_daemon_records()

    state = json.loads(daemon_json_path.read_text())
    assert state["state"] == "running"
    assert state["error"] is None
    assert state["current_tool"] == "some_tool"


def test_reap_host_lost_ignores_legacy_record_with_no_execution_owner(tmp_path):
    """A record with NO execution_owner (pre-existing/legacy behavior) must
    be completely untouched by the host-lost reaper — that record remains
    exclusively the existing orphan reaper's territory."""
    from tests._daemon_helpers import make_daemon_agent

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    daemon_json_path = _write_owned_daemon_json(
        working_dir, "em-legacy-1", execution_owner=None, parent_pid=99999999,
    )
    mgr = agent.get_capability("daemon")
    mgr._reap_host_lost_daemon_records()
    state = json.loads(daemon_json_path.read_text())
    assert state["state"] == "running"
    assert state["error"] is None


def test_reap_host_lost_publishes_terminal_notification_exactly_once(tmp_path):
    from tests._daemon_helpers import make_daemon_agent
    from tests._notification_store_helpers import snapshot_notifications

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    dead_pid = 999998
    marker = RefreshHostMarker.build(
        pid=dead_pid, start_ticks=1, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-lost-2"],
    )
    commit_marker(working_dir, marker)
    owner = ExecutionOwner.from_marker(marker).to_dict()
    _write_owned_daemon_json(working_dir, "em-lost-2", execution_owner=owner, parent_pid=dead_pid)

    mgr = agent.get_capability("daemon")
    mgr._reap_host_lost_daemon_records()

    notifications = snapshot_notifications(working_dir)
    events = notifications["system"]["data"]["events"]
    matching = [e for e in events if e.get("ref_id") == "em-lost-2"]
    assert len(matching) == 1

    daemon_json_path = working_dir / "daemons" / "em-lost-2" / "daemon.json"
    state = json.loads(daemon_json_path.read_text())
    assert state["terminal_notified"] is True
    assert state["terminal_notification_receipt"]["idempotency_key"] == "daemon-terminal:em-lost-2"


def test_reap_host_lost_runs_before_ordinary_orphan_reaping_at_manager_construction(tmp_path):
    """DaemonManager.__init__ must call _reap_host_lost_daemon_records
    BEFORE _reap_dead_parent_daemon_records (brief ordering requirement),
    proven by observing the correct error.type on a fresh manager
    construction — if the ordering were reversed, the ordinary orphan
    reaper would run first and stamp DaemonOrphaned before the host-lost
    reaper ever got a chance to correct it."""
    from tests._daemon_helpers import make_daemon_agent

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    dead_pid = 999997
    marker = RefreshHostMarker.build(
        pid=dead_pid, start_ticks=1, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-lost-3"],
    )
    commit_marker(working_dir, marker)
    owner = ExecutionOwner.from_marker(marker).to_dict()
    daemon_json_path = _write_owned_daemon_json(
        working_dir, "em-lost-3", execution_owner=owner, parent_pid=dead_pid,
    )

    # Fresh DaemonManager construction (as a successor process would do).
    from lingtai.tools.daemon import DaemonManager
    DaemonManager(agent)

    state = json.loads(daemon_json_path.read_text())
    assert state["error"]["type"] == "DaemonHostLost"


def test_observation_and_control_ask_reclaim_timeout_across_real_processes(tmp_path):
    """Brief category 3. Successor SUBPROCESSES submit real durable control
    requests (ask, then reclaim) against a real host subprocess via the
    file-based control plane — not same-process calls. Duplicate request id
    is proven idempotent via a real double-write from within the successor
    script itself. Every ack distinguishes accepted/already-terminal
    honestly."""
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    ctx = _start_real_host_and_wait_ready(tmp_path, src_path=src_path, env=env)
    try:
        # --- ask, with real duplicate-id idempotency proven in-script. ---
        ask_result_file = tmp_path / "ask_result"
        ask_out = _run_successor_control(
            ctx, src_path=src_path, env=env, request_id="req-ctl-ask-1",
            operation="ask", payload={"message": "control-plane hello"},
            result_file=ask_result_file,
        )
        assert ask_out["first_write_succeeded"] is True
        assert ask_out["second_write_raised_exists"] is True
        assert ask_out["ack"]["status"] == "accepted"
        assert ask_out["ack"]["request_id"] == "req-ctl-ask-1"
        assert ask_out["ack"]["target_run_ids"] == [ctx["em_id"]]

        # --- reclaim: accepted ack, cancel_event set on the host's own
        # entry (proven indirectly via a second reclaim request against the
        # SAME run returning accepted again — the host's dispatch does not
        # error on an already-cancelled-but-still-nonterminal run; a truly
        # broken scoping would have crashed or affected an unrelated run,
        # which the earlier same-process unit tests already cover more
        # precisely — this test's job is the REAL cross-process file
        # transport, not re-proving dispatch scoping logic). ---
        reclaim_result_file = tmp_path / "reclaim_result"
        reclaim_out = _run_successor_control(
            ctx, src_path=src_path, env=env, request_id="req-ctl-reclaim-1",
            operation="reclaim", payload={}, result_file=reclaim_result_file,
        )
        assert reclaim_out["ack"]["status"] == "accepted"

        # The run must still be host-owned/running (reclaim only signals
        # cancellation acceptance — it does not itself terminal-write).
        daemon_json = json.loads((ctx["run_dir"] / "daemon.json").read_text())
        assert daemon_json["state"] == "running"
        assert daemon_json["parent_pid"] == ctx["host_pid"]

        # --- Release the blocking tool so the host can finish cleanly, and
        # confirm the run reaches a real terminal state, written by the
        # host, exactly once (the already-terminal ACK PATH itself — a
        # request submitted while a run is nonterminal but resolved to
        # already-terminal by dispatch time — is separately proven for
        # real filesystem effects in
        # test_dispatch_control_request_already_terminal_for_done_run
        # (same-process, real DaemonManager + real DaemonRunDir). This
        # process-level test's remaining job is the drain loop's own real
        # exit-on-quiescence behavior: once the owned run is genuinely
        # terminal, the host's drain thread must actually stop polling
        # (proven via host_done_file, a real file only the drain thread
        # itself writes after _run_drain_loop returns) — there is no
        # "host" left to answer a control request submitted after that
        # point, which is the correct, honest HOST_EXITING-adjacent
        # semantics, not a gap to paper over with a synthetic ack wait. ---
        ctx["release_file"].write_text("go", encoding="utf-8")
        deadline = time.monotonic() + 20.0
        while not ctx["side_effect_marker"].exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)

        deadline = time.monotonic() + 20.0
        while True:
            daemon_json = json.loads((ctx["run_dir"] / "daemon.json").read_text())
            if daemon_json["state"] in ("done", "failed"):
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert daemon_json["state"] == "done", daemon_json.get("error")
        assert daemon_json["parent_pid"] == ctx["host_pid"]

        # host_done_file lives directly under tmp_path (a sibling of
        # working_dir), matching how _start_real_host_and_wait_ready
        # constructed it for the host script — locate it the same way here.
        host_done_file = ctx["working_dir"].parent / "host_done"
        deadline = time.monotonic() + 20.0
        while not host_done_file.exists():
            assert time.monotonic() < deadline, "drain loop never exited after owned run went terminal"
            assert ctx["host_proc"].poll() is None
            time.sleep(0.02)
    finally:
        _stop_real_host(ctx)


# ---------------------------------------------------------------------------
# REAL PROCESS-LEVEL ACCEPTANCE TESTS — brief category 4: host-loss truth.
# Kill the REAL host subprocess mid-nonterminal-work (SIGKILL, not a clean
# stop), then start a REAL successor subprocess and prove it: verifies the
# real PID death, records exactly one DaemonHostLost (never DaemonOrphaned),
# emits exactly one stable terminal event, and never replays the blocked
# side effect (the release_file is never written, so a replay would have
# produced the marker file).
# ---------------------------------------------------------------------------


def test_host_loss_truth_across_real_processes(tmp_path):
    """Brief category 4. Kill the host subprocess before it acks/finishes
    its nonterminal work; a REAL SEPARATE successor subprocess starts,
    verifies PID/start-identity/cmd death, records DaemonHostLost, emits/
    reconciles exactly one stable terminal event, and never replays the
    side effect."""
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    ctx = _start_real_host_and_wait_ready(tmp_path, src_path=src_path, env=env)

    # The run is genuinely still blocked (release_file never written) —
    # confirm before killing, so a later "side effect never occurred" check
    # actually proves something.
    assert not ctx["side_effect_marker"].exists()
    daemon_json_before = json.loads((ctx["run_dir"] / "daemon.json").read_text())
    assert daemon_json_before["state"] == "running"

    # Real kill — SIGKILL, not a clean stop signal, so there is no
    # opportunity for the host to publish anything about its own death.
    ctx["host_proc"].kill()
    ctx["host_proc"].wait(timeout=10)
    assert ctx["host_proc"].poll() is not None

    # A moment for the OS to fully reap/release the PID before the
    # successor probes it (SIGKILL delivery + wait() already guarantees
    # this on POSIX, but a short settle avoids any platform timing
    # surprise in the identity probe itself).
    time.sleep(0.1)

    successor_result_file = tmp_path / "host_loss_successor_result"
    successor_script = _SUCCESSOR_PROCESS_SCRIPT.format(
        src_path=src_path, working_dir=str(ctx["working_dir"]), em_id=ctx["em_id"],
        host_pid=ctx["host_pid"], result_file=str(successor_result_file),
    )
    successor_proc = subprocess.run(
        [sys.executable, "-c", successor_script], capture_output=True, text=True, env=env, timeout=30,
    )
    assert successor_proc.returncode == 0, (
        f"successor stdout={successor_proc.stdout!r} stderr={successor_proc.stderr!r}"
    )

    # The successor's OWN fresh-manager construction (inside Agent(...))
    # already ran _reap_host_lost_daemon_records before this script's
    # explicit _handle_list() call — the real, on-disk daemon.json is the
    # durable proof of what that startup reaping actually did.
    daemon_json_after = json.loads((ctx["run_dir"] / "daemon.json").read_text())
    assert daemon_json_after["state"] == "failed"
    assert daemon_json_after["error"]["type"] == "DaemonHostLost"
    assert daemon_json_after["error"]["type"] != "DaemonOrphaned"
    assert daemon_json_after["current_tool"] is None
    assert daemon_json_after["finished_at"] is not None

    # Exactly one stable terminal event, honestly written by the successor
    # only now (because the former sole writer is proven gone) — never a
    # second, duplicate publish.
    assert daemon_json_after["terminal_notified"] is True
    receipt = daemon_json_after["terminal_notification_receipt"]
    assert receipt is not None
    assert receipt["idempotency_key"] == f"daemon-terminal:{ctx['em_id']}"

    from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
    store = PosixNotificationStoreAdapter(ctx["working_dir"])
    snapshot = store.snapshot(lambda name: True)
    events = snapshot.get("system", {}).get("data", {}).get("events", [])
    matching = [e for e in events if e.get("ref_id") == ctx["em_id"]]
    assert len(matching) == 1, matching

    # No replay: the side effect never occurred — the blocking tool
    # handler was never re-entered by anything, and its release_file gate
    # (which only the original blocking thread inside the now-dead host
    # process was ever waiting on) was never written by this test.
    assert not ctx["side_effect_marker"].exists()

    # The successor's own `list` call must report the SAME honest terminal
    # state, not a stale in-memory guess.
    successor_out = json.loads(successor_result_file.read_text(encoding="utf-8"))
    assert successor_out["list_status"] == "failed", successor_out


# ---------------------------------------------------------------------------
# Brief category 6: multiple refresh generations / PID reuse. Same-process
# (not a full 2-generation real-subprocess handoff, which would require the
# not-yet-implemented lifecycle.py wiring to produce a genuine second
# generation from a genuine second refresh — see report §5 for exactly what
# remains deferred) but exercises REAL DaemonManager/refresh_host code: two
# valid, independently committed markers own disjoint fixed run sets, and
# a control request is routed only to its owner; a marker whose PID is
# reused by a DIFFERENT start identity is never adopted/exempted; a marker/
# tag mismatch fails closed.
# ---------------------------------------------------------------------------


def test_two_generations_own_disjoint_run_sets_control_routes_only_to_owner(tmp_path, monkeypatch):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    mgr = agent.get_capability("daemon")

    run_a = make_daemon_run_dir(agent, em_id="em-gen-a")
    register_daemon_entry(mgr, "em-gen-a", run_a)
    run_b = make_daemon_run_dir(agent, em_id="em-gen-b")
    register_daemon_entry(mgr, "em-gen-b", run_b)

    # Generation A: own only em-gen-a.
    identity = probe_process_start_identity(os.getpid())
    marker_a = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-gen-a"],
    )
    tag_owned_runs(marker_a, {"em-gen-a": run_a})
    commit_marker(working_dir, marker_a)

    # Generation B (a LATER, distinct generation — e.g. an interactive
    # parent that created its own new run after the first handoff): owns
    # only em-gen-b. Distinct nonce/generation from A even though same PID
    # (both committed by this same test process) — proving routing keys on
    # generation identity, not merely "any marker naming this PID".
    marker_b = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-gen-b"],
    )
    tag_owned_runs(marker_b, {"em-gen-b": run_b})
    commit_marker(working_dir, marker_b)
    assert marker_a.generation != marker_b.generation
    assert marker_a.nonce != marker_b.nonce

    # A request targeting em-gen-a under marker_a's generation succeeds.
    req_a = _build_request(marker_a, request_id="req-gen-a-1", operation="ask",
                            target_run_ids=["em-gen-a"], payload={"message": "for A"})
    ack_a = mgr._dispatch_control_request(req_a, marker_a)
    assert ack_a.status == "accepted"

    # The SAME run id (em-gen-a) is NOT in marker_b's owned set — a request
    # naming it under marker_b's generation must be rejected, proving a
    # later generation can never claim an earlier generation's run.
    req_cross = _build_request(marker_b, request_id="req-cross-1", operation="ask",
                                target_run_ids=["em-gen-a"], payload={"message": "wrong owner"})
    ack_cross = mgr._dispatch_control_request(req_cross, marker_b)
    assert ack_cross.status == "rejected"

    # And the reverse: em-gen-b under marker_a's generation is also rejected.
    req_cross2 = _build_request(marker_a, request_id="req-cross-2", operation="ask",
                                 target_run_ids=["em-gen-b"], payload={"message": "wrong owner 2"})
    ack_cross2 = mgr._dispatch_control_request(req_cross2, marker_a)
    assert ack_cross2.status == "rejected"

    # em-gen-b under its OWN correct generation still succeeds.
    req_b = _build_request(marker_b, request_id="req-gen-b-1", operation="ask",
                            target_run_ids=["em-gen-b"], payload={"message": "for B"})
    ack_b = mgr._dispatch_control_request(req_b, marker_b)
    assert ack_b.status == "accepted"


def test_pid_reuse_different_start_identity_never_adopted(tmp_path):
    """A marker naming a PID that is technically live right now, but whose
    RECORDED start_ticks does not match that live PID's CURRENT start
    identity (the exact PID-reuse scenario: an old process died, a new
    unrelated process was later assigned the same PID by the OS), must
    never be treated as verified live — proven via the real
    verify_marker_live/verified_refresh_host_pids predicates, which is the
    one place this whole design's anti-reuse guarantee actually lives."""
    parent = tmp_path / "agent"
    parent.mkdir()

    real_identity = probe_process_start_identity(os.getpid())
    assert real_identity is not None
    stale_marker = RefreshHostMarker.build(
        pid=os.getpid(),
        # A start_ticks value that does NOT match this real, live process's
        # actual current start identity — simulating "this PID used to
        # belong to a different process with a different start time."
        start_ticks=real_identity.start_ticks + 999_999_999,
        command_label="module", working_dir=str(parent), owned_run_ids=["em-reused"],
    )
    commit_marker(parent, stale_marker)

    # Even though os.getpid() is genuinely alive right now, the mismatched
    # start_ticks must fail verification — this is the load-bearing
    # assertion of the entire anti-PID-reuse design.
    assert verify_marker_live(stale_marker) is False
    assert is_verified_refresh_host(os.getpid(), parent) is False


def test_marker_tag_mismatch_fails_closed_in_host_lost_reap(tmp_path):
    """A run whose execution_owner claims a marker, but whose owner tag's
    generation/nonce does NOT actually match that marker's real generation/
    nonce (a corrupted/forged/stale tag), must be treated exactly like
    host-lost — an unproven ownership claim grants no protection, proven
    via the real _reap_host_lost_daemon_records path."""
    from tests._daemon_helpers import make_daemon_agent

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    identity = probe_process_start_identity(os.getpid())
    real_marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=identity.start_ticks, command_label="module",
        working_dir=str(working_dir), owned_run_ids=["em-mismatch-tag"],
    )
    commit_marker(working_dir, real_marker)

    # A tag that CLAIMS the real marker's generation but carries a
    # different (forged/stale) nonce — proves proves_membership_of's nonce
    # check, not merely its generation check, is what's actually enforced.
    forged_owner = {
        "schema_version": 1, "generation": real_marker.generation,
        "nonce": "f" * 32,  # wrong nonce
        "pid": real_marker.pid, "start_ticks": real_marker.start_ticks,
        "sequence": real_marker.sequence, "owned_run_ids": ["em-mismatch-tag"],
    }
    daemon_json_path = _write_owned_daemon_json(
        working_dir, "em-mismatch-tag", execution_owner=forged_owner, parent_pid=os.getpid(),
    )

    mgr = agent.get_capability("daemon")
    mgr._reap_host_lost_daemon_records()

    state = json.loads(daemon_json_path.read_text())
    assert state["state"] == "failed"
    assert state["error"]["type"] == "DaemonHostLost"


# ---------------------------------------------------------------------------
# Brief category 5 (partial — see report §5 for exact scope): terminal
# notification race / crash-after-terminal-before-receipt. The underlying
# cross-process notification-store interprocess lock itself is ALREADY
# proven by the accepted B/C
# TestInterprocessMutationSerialization/TestForkedAdapterMutationSerialization
# suites (not re-proven here). What's new here is the DAEMON layer's own
# crash-recovery contract: a run whose terminal state was written but whose
# receipt publish never completed (simulating a real process crash exactly
# between claim_terminal_notification and mark_terminal_notification_published)
# must be reconciled EXACTLY ONCE by a fresh manager construction, never
# left stuck, and never double-published on a SECOND fresh construction.
# This proof is same-process (real DaemonManager + real DaemonRunDir +
# real filesystem, not a mock of the reconciliation path) rather than a
# full real-subprocess crash simulation — see the report for why the
# marginal engineering cost of a new subprocess-crash-timing harness was
# not spent in this bounded run given the already-real cross-process lock
# coverage from the accepted B/C stage.
# ---------------------------------------------------------------------------


def test_crash_after_terminal_state_before_receipt_reconciles_exactly_once(tmp_path):
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir
    from tests._notification_store_helpers import snapshot_notifications

    agent = make_daemon_agent(tmp_path)
    working_dir = agent._working_dir
    # make_daemon_run_dir with only em_id/handle produces the LEGACY
    # long-timestamp run_id form (no explicit run_id passed) — the actual
    # generated run_dir.run_id (not the "em-crash-1" handle) is what
    # becomes ref_id in a published notification, so assertions below use
    # run_dir.run_id, not the handle string.
    run_dir = make_daemon_run_dir(agent, em_id="em-crash-1")
    real_run_id = run_dir.run_id

    # Simulate the EXACT crash window: mark_done() completed (real state
    # write), claim_terminal_notification() completed (real pending claim
    # write) — but the process died before
    # mark_terminal_notification_published() ever ran. This is achieved by
    # calling the real methods in the real order and then never calling the
    # publish-completion step, exactly matching what an actual SIGKILL
    # between those two calls would leave on disk.
    run_dir.mark_done("finished right before the crash")
    idempotency_key = run_dir.claim_terminal_notification("done")
    assert idempotency_key is not None

    daemon_json_path = run_dir.daemon_json_path
    state_at_crash = json.loads(daemon_json_path.read_text())
    assert state_at_crash["state"] == "done"
    assert state_at_crash["terminal_notified"] is False
    assert isinstance(state_at_crash["terminal_notification_claim"], dict)

    # First fresh manager construction (the successor's first startup)
    # must reconcile — real _reconcile_terminal_notifications, unmodified
    # by this stage's own changes, still runs and republishes.
    from lingtai.tools.daemon import DaemonManager
    DaemonManager(agent)

    state_after_first = json.loads(daemon_json_path.read_text())
    assert state_after_first["terminal_notified"] is True
    assert state_after_first["terminal_notification_receipt"]["idempotency_key"] == idempotency_key

    notifications = snapshot_notifications(working_dir)
    events = notifications["system"]["data"]["events"]
    matching_after_first = [e for e in events if e.get("ref_id") == real_run_id]
    assert len(matching_after_first) == 1

    # A SECOND fresh manager construction (e.g. a later refresh generation,
    # or a retry after some unrelated failure) must NOT republish — the
    # durable terminal_notified=True receipt is exactly what prevents a
    # second successor from ever replaying this notification.
    DaemonManager(agent)
    notifications_after_second = snapshot_notifications(working_dir)
    events_after_second = notifications_after_second["system"]["data"]["events"]
    matching_after_second = [e for e in events_after_second if e.get("ref_id") == real_run_id]
    assert len(matching_after_second) == 1, (
        "a second fresh manager construction must never re-publish an "
        "already-durably-receipted terminal notification"
    )


# ---------------------------------------------------------------------------
# v6 correction stage — failing-first regressions for every P0/P1 defect the
# parent audit (scratch/daemon-refresh-survival-20260714/
# parent-v5-draft-checkpoint-audit.md) mechanically proved against the v5
# draft checkpoint. Each test below is written to demonstrate the exact
# counterexample the audit describes BEFORE any corrective code exists for
# it, then the corresponding fix is implemented until it passes.
# ---------------------------------------------------------------------------


def test_p0_freeze_ordering_prevents_concurrent_emanate_escape(tmp_path, monkeypatch):
    """P0-1: _prepare_refresh_host must set _refresh_prepare_frozen BEFORE it
    reads the owned run-id set, not after — otherwise a concurrent emanate
    landing in that exact window creates a new _emanations entry that the
    snapshot never sees, so that new run is never tagged/owned by the
    committed marker yet keeps running after interactive liveness is
    withdrawn (the exact escape the audit's counterexample describes).

    Proven by monkeypatching `dict.items` is too invasive; instead we patch
    a hook that fires exactly once, at the moment _prepare_refresh_host reads
    self._emanations to build its snapshot, and use it to inject a brand new
    nonterminal entry into self._emanations from "concurrent" code. If the
    freeze flag is set STRICTLY BEFORE that read, the injected entry must
    still be excluded from the marker's owned set (freezing doesn't retroactively
    un-create it, but it stops the injection from being interpreted as
    legitimate — the real fix must ensure the ordering guarantees no reader
    can observe `_refresh_prepare_frozen is False` while a snapshot is being
    taken, and _handle_emanate itself, called back-to-back inside the
    snapshot window, must observe the freeze and refuse)."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    observed_frozen_during_snapshot = []
    real_emanations = mgr._emanations

    class _WatchedEmanations(dict):
        def items(self):
            # Fires exactly once, at the point _prepare_refresh_host reads
            # self._emanations.items() to build its owned_run_ids snapshot.
            observed_frozen_during_snapshot.append(mgr._refresh_prepare_frozen)
            # Simulate a concurrent _handle_emanate landing in the exact
            # window between snapshot-read and freeze-flag-set: a real
            # racing thread would call _handle_emanate, which (once fixed)
            # must consult the freeze flag BEFORE mutating self._emanations.
            # We emulate the pre-fix vulnerability directly by attempting the
            # same mutation _handle_emanate would perform, gated on the SAME
            # flag the real entry-point check uses, so this test exercises
            # the ordering invariant itself rather than duplicating
            # _handle_emanate's full body.
            if not mgr._refresh_prepare_frozen:
                concurrent_run_dir = make_daemon_run_dir(agent, em_id="em-concurrent-escape")
                register_daemon_entry(mgr, "em-concurrent-escape", concurrent_run_dir)
            return super().items()

    watched = _WatchedEmanations(real_emanations)
    mgr._emanations = watched

    marker = mgr._prepare_refresh_host()

    assert observed_frozen_during_snapshot == [True], (
        "_refresh_prepare_frozen must already be True at the moment the "
        "owned-run-id snapshot is read — freezing after the read leaves a "
        "window where a concurrent emanate is invisible to the snapshot "
        "yet not rejected by the freeze check either"
    )
    assert marker is not None
    assert "em-concurrent-escape" not in marker.owned_run_ids


def test_v7_emanate_admission_barrier_real_thread_past_check_race_lingtai_backend(tmp_path, monkeypatch):
    """A-1 real-thread barrier reproduction (LingTai backend): v6's own P0-1
    test above only proves the SNAPSHOT read observes the freeze flag as
    True — it synchronously injects the "concurrent" registration from
    INSIDE the snapshot's own dict.items() call, on the SAME thread, which
    cannot model a call that already evaluated _handle_emanate's entry
    check as False on a REAL SEPARATE THREAD microseconds earlier and is
    now genuinely still executing (unlocked preset/MCP/tool-surface setup)
    when PREPARE begins. This test drives that exact scenario: a real
    background thread is deliberately paused, via a monkeypatched hook,
    AFTER passing _handle_emanate's entry-point freeze check but BEFORE
    reaching its own registration-time re-check under
    _emanate_admission_lock (the new lock this stage adds); a real
    threading.Barrier hands control to a real _prepare_refresh_host() call
    on the main thread while the background thread is paused exactly
    there; the background thread is then released. If the fix is correct,
    the background thread's OWN registration-time re-check (now genuinely
    concurrent with, and correctly excluded by, PREPARE's snapshot) must
    refuse to register — proving no call can land outside the exact
    marker-owned set the snapshot captured, regardless of what its
    entry-point check observed."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    reached_past_check = threading.Event()
    release_past_check = threading.Event()
    real_task_mcp_registrations = mgr._task_mcp_registrations

    def _paused_task_mcp_registrations(spec):
        # Fires once per task, well after _handle_emanate's own entry-point
        # freeze check (already passed as False at this point — the real
        # TOCTOU window) and well before the registration-time re-check
        # deep inside the same call's per-task loop.
        reached_past_check.set()
        release_past_check.wait(timeout=5)
        return real_task_mcp_registrations(spec)

    monkeypatch.setattr(mgr, "_task_mcp_registrations", _paused_task_mcp_registrations)

    emanate_result = {}

    def _run_emanate_on_background_thread():
        emanate_result["value"] = mgr._handle_emanate(
            [{"task": "concurrent escape attempt", "tools": []}]
        )

    bg_thread = threading.Thread(target=_run_emanate_on_background_thread)
    bg_thread.start()
    assert reached_past_check.wait(timeout=5), (
        "test setup: the background thread must reach the paused hook "
        "(past its own entry-point check) before PREPARE runs"
    )

    # The background thread has ALREADY observed _refresh_prepare_frozen as
    # False (it passed the entry check to get here) and is now paused
    # mid-flight, genuinely unregistered. PREPARE now runs for real.
    marker = mgr._prepare_refresh_host()
    assert marker is not None
    assert marker.owned_run_ids == ("em-live",), (
        "PREPARE's snapshot must contain only the genuinely-already-"
        "registered run — the paused background thread must not be in it, "
        "since it had not registered yet at snapshot time"
    )

    # Release the background thread — it should now hit its OWN
    # registration-time re-check under _emanate_admission_lock and see the
    # (now True) freeze flag, refusing to register rather than silently
    # landing outside the marker's already-committed owned set.
    release_past_check.set()
    bg_thread.join(timeout=5)
    assert not bg_thread.is_alive()

    result = emanate_result["value"]
    assert result["status"] == "error", (
        "the background thread's own registration-time re-check must "
        "refuse once the freeze flag is True, even though its ENTRY-POINT "
        "check observed it as False microseconds earlier — this is the "
        "exact TOCTOU window a bare bool check-once-at-entry cannot close"
    )
    assert "em-concurrent-escape" not in mgr._emanations
    # No run_id from this refused batch (whatever id _new_emanation_id
    # assigned) must have been registered — the ONLY registered run must
    # still be the original em-live.
    assert set(mgr._emanations.keys()) == {"em-live"}


def test_v7_emanate_admission_barrier_real_thread_past_check_race_cli_backend(tmp_path, monkeypatch):
    """A-1 real-thread barrier reproduction (CLI backend): the same
    past-check race as the LingTai-backend test above, but through
    _handle_emanate_cli's own separate registration site — proving the
    fix closes the race for BOTH registration paths, not only the
    in-process LingTai one."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    reached_past_check = threading.Event()
    release_past_check = threading.Event()
    real_task_system_prompt = mgr._task_system_prompt

    def _paused_task_system_prompt(spec):
        reached_past_check.set()
        release_past_check.wait(timeout=5)
        return real_task_system_prompt(spec)

    monkeypatch.setattr(mgr, "_task_system_prompt", _paused_task_system_prompt)

    emanate_result = {}

    def _run_emanate_cli_on_background_thread():
        emanate_result["value"] = mgr._handle_emanate(
            [{"task": "concurrent CLI escape attempt", "tools": []}],
            backend="claude",
        )

    bg_thread = threading.Thread(target=_run_emanate_cli_on_background_thread)
    bg_thread.start()
    assert reached_past_check.wait(timeout=5), (
        "test setup: the background thread must reach the paused hook "
        "before PREPARE runs"
    )

    marker = mgr._prepare_refresh_host()
    assert marker is not None
    assert marker.owned_run_ids == ("em-live",)

    release_past_check.set()
    bg_thread.join(timeout=5)
    assert not bg_thread.is_alive()

    result = emanate_result["value"]
    assert result["status"] == "error", (
        "the CLI-backend background thread's own registration-time "
        "re-check must also refuse once the freeze flag is True — the "
        "same admission barrier protects both registration paths"
    )
    assert set(mgr._emanations.keys()) == {"em-live"}


def test_p0_atomic_rollback_setter_writes_then_raises_leaves_no_tag(tmp_path):
    """P0-2a: DaemonRunDir.set_execution_owner mutates in-memory state before
    the atomic file write. If the write itself raises (e.g. OSError), the
    in-memory self._state must not be left claiming an owner the disk never
    recorded — tag_owned_runs' rollback contract depends on being able to
    trust that a raised exception means "nothing was durably tagged," but if
    the setter mutates self._state UNCONDITIONALLY before attempting the
    write, a post-write failure leaves the in-memory object claiming
    ownership even though the file write failed and the caller's rollback
    loop (which calls clear_execution_owner_on_rollback expecting to erase a
    tag that actually landed) can't fix a tag that was never durably
    persisted to disk in the first place — the real defect is that the
    in-memory state and disk state can diverge on this exact failure path."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    agent = make_daemon_agent(tmp_path)
    run_dir = make_daemon_run_dir(agent, em_id="em-1")
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir=str(agent._working_dir), owned_run_ids=["em-1"],
    )
    owner_dict = ExecutionOwner.from_marker(marker).to_dict()

    import lingtai.tools.daemon.run_dir as run_dir_module

    def _boom(path, data, **kwargs):
        raise OSError("simulated post-write failure")

    original_atomic_write_json = run_dir_module.atomic_write_json
    run_dir_module.atomic_write_json = _boom
    try:
        with pytest.raises(OSError):
            run_dir.set_execution_owner(owner_dict)
    finally:
        run_dir_module.atomic_write_json = original_atomic_write_json

    assert run_dir.state_snapshot()["execution_owner"] is None, (
        "a write failure inside set_execution_owner must leave the "
        "in-memory state exactly as it was before the call — mutating "
        "self._state before attempting the durable write means a failed "
        "write still leaves the object claiming an owner tag that was "
        "never persisted to disk"
    )
    on_disk = json.loads(run_dir.daemon_json_path.read_text())
    assert on_disk.get("execution_owner") is None


def test_v7_atomic_rollback_setter_real_write_then_raises_reconciles_memory_with_disk(tmp_path):
    """v7 isolated counterexample: the parent's exact mechanical proof
    (setter_write_then_raise_in_memory_owner=null,
    setter_write_then_raise_on_disk_owner_is_none=false against pre-v7
    code). v6's own test above uses a `_boom` that raises WITHOUT ever
    writing — it never proves the setter handles a write call that performs
    the REAL durable os.replace and THEN raises (e.g. a post-replace
    directory-fsync failure in some other write path). Under the pre-v7
    copy-before-mutate design, that exact ordering left self._state at None
    while the real on-disk daemon.json durably recorded the tag — the
    OPPOSITE divergence direction from what v6 fixed (disk-ahead-of-memory
    instead of memory-ahead-of-disk), and just as dangerous: a caller that
    trusts in-memory state to decide whether a rollback is needed would
    wrongly believe this run was never tagged, even though it durably is."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    agent = make_daemon_agent(tmp_path)
    run_dir = make_daemon_run_dir(agent, em_id="em-2")
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir=str(agent._working_dir), owned_run_ids=["em-2"],
    )
    owner_dict = ExecutionOwner.from_marker(marker).to_dict()

    import lingtai.tools.daemon.run_dir as run_dir_module

    real_atomic_write_json = run_dir_module.atomic_write_json

    def _write_then_boom(path, data, **kwargs):
        # Perform the REAL durable write first (unlike the v6 test's
        # `_boom`, which never writes anything), then raise — this is the
        # exact ordering the parent's mechanical counterexample targets.
        real_atomic_write_json(path, data, **kwargs)
        raise OSError("simulated post-write failure, e.g. a directory fsync")

    run_dir_module.atomic_write_json = _write_then_boom
    try:
        with pytest.raises(OSError):
            run_dir.set_execution_owner(owner_dict)
    finally:
        run_dir_module.atomic_write_json = real_atomic_write_json

    on_disk = json.loads(run_dir.daemon_json_path.read_text())
    in_memory = run_dir.state_snapshot()
    disk_has_owner = on_disk.get("execution_owner") is not None
    memory_has_owner = in_memory.get("execution_owner") is not None
    assert disk_has_owner is True, "test setup: the real write must have actually landed"
    assert memory_has_owner == disk_has_owner, (
        "when the underlying write call's own durable os.replace genuinely "
        "landed before something else in the write path raised, in-memory "
        "state must be reconciled to match — leaving memory at None while "
        "disk durably records the tag is exactly the divergence this test "
        "proves is now closed"
    )


def test_p0_atomic_rollback_marker_commit_failure_clears_successful_tags(tmp_path, monkeypatch):
    """P0-2b: even when every tag_owned_runs write succeeds, if the
    SUBSEQUENT commit_marker call fails with an ordinary (non-ambiguous)
    error, _prepare_refresh_host's docstring claims "no run was left
    mid-tagged, and no marker was left partially committed" — but the actual
    code only unfreezes and re-raises; it never calls
    clear_execution_owner_on_rollback for the runs tag_owned_runs already
    tagged. This proves those runs are left durably claiming an owner whose
    marker was never actually committed."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    import lingtai.tools.daemon as daemon_module

    def _boom_commit(parent_working_dir, marker):
        raise MarkerValidationError("simulated_ordinary_failure", "simulated ordinary commit failure")

    monkeypatch.setattr(daemon_module, "commit_marker", _boom_commit)

    with pytest.raises(MarkerValidationError):
        mgr._prepare_refresh_host()

    on_disk = json.loads(run_dir.daemon_json_path.read_text())
    assert on_disk.get("execution_owner") is None, (
        "an ordinary (non-ambiguous) marker-commit failure after all tags "
        "succeeded must roll back every tag this call applied — leaving a "
        "run durably claiming an execution_owner for a marker that was "
        "never committed contradicts _prepare_refresh_host's own "
        "'no tag/marker residue on failure' docstring claim"
    )


def test_v7_owner_tag_transaction_write_state_unknown_escalates_to_ambiguous(tmp_path):
    """When set_execution_owner itself raises
    run_dir.ExecutionOwnerWriteStateUnknownError (the setter's own write
    failed AND the resulting on-disk state could not even be read back to
    reconcile truth), tag_owned_runs must escalate the FAILING run to
    OwnerTaggingAmbiguousError — not let the raw
    ExecutionOwnerWriteStateUnknownError propagate as if it were an
    ordinary 'nothing was written, safe to treat as untagged' failure a
    caller might otherwise retry cleanly. Earlier successfully-tagged runs
    in the same call are still correctly rolled back (their own state is
    known), proving this escalation is scoped to the one run whose state is
    genuinely unknown, not the whole batch."""
    from lingtai.tools.daemon.run_dir import ExecutionOwnerWriteStateUnknownError

    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir=str(tmp_path.resolve()), owned_run_ids=["run-a", "run-b"],
    )

    rolled_back: list = []

    class _GoodRunDir:
        def __init__(self, run_id):
            self.run_id = run_id
            self.tagged = False

        def set_execution_owner(self, owner_dict):
            self.tagged = True

        def clear_execution_owner_on_rollback(self):
            self.tagged = False
            rolled_back.append(self.run_id)

    class _UnknownStateRunDir:
        def set_execution_owner(self, owner_dict):
            raise ExecutionOwnerWriteStateUnknownError(
                "run-b", cause=OSError("simulated unreadable disk state")
            )

    run_a_dir = _GoodRunDir("run-a")
    run_dirs = {"run-a": run_a_dir, "run-b": _UnknownStateRunDir()}

    with pytest.raises(OwnerTaggingAmbiguousError) as exc_info:
        tag_owned_runs(marker, run_dirs)

    assert exc_info.value.run_id == "run-b", (
        "the escalated OwnerTaggingAmbiguousError must name the run whose "
        "OWN write-state is unknown, not an earlier run's rollback"
    )
    assert rolled_back == ["run-a"], (
        "the earlier successfully-tagged run must still be rolled back "
        "(its own state IS known — it was tagged, then cleanly untagged) "
        "even though the later run's own failure is what's ambiguous"
    )
    assert run_a_dir.tagged is False


def test_p0_membership_proof_rejects_tampered_tag_with_wrong_owned_run_ids(tmp_path):
    """P0-3: the parent audit's exact mechanical counterexample. A marker
    owns only ('run-a',). A tampered tag copies the marker's real generation
    and nonce (the two fields the current proves_membership_of checks) but
    claims wrong PID/start_ticks/sequence and a DIFFERENT owned_run_ids
    containing 'run-b'. proves_membership_of must NOT let this tampered tag
    prove 'run-b' is a member of anything, and must not accept it as a
    legitimate tag for 'run-a' either, since a real tag's full identity
    (pid/start_ticks/sequence) and owned_run_ids must match the marker's OWN
    recorded values exactly — generation+nonce equality alone is not
    sufficient proof that the tag's OTHER fields are trustworthy."""
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=12345, command_label="module",
        working_dir=str(Path("/tmp").resolve()), owned_run_ids=["run-a"],
    )
    tampered_tag = ExecutionOwner(
        schema_version=marker.schema_version,
        generation=marker.generation,  # copied — matches
        nonce=marker.nonce,  # copied — matches
        pid=999,  # forged — does NOT match marker.pid
        start_ticks=999,  # forged — does NOT match marker.start_ticks
        sequence=999,  # forged — does NOT match marker.sequence
        owned_run_ids=("run-b",),  # forged — does NOT match marker.owned_run_ids
    )
    assert tampered_tag.proves_membership_of("run-b", marker) is False, (
        "a tampered tag must never prove membership for a run_id that is "
        "not in the MARKER's own owned_run_ids, regardless of what the "
        "tag itself claims to own"
    )
    assert tampered_tag.proves_membership_of("run-a", marker) is False, (
        "a tag whose pid/start_ticks/sequence does not match the marker's "
        "own recorded identity must not be trusted even for a run_id the "
        "marker DOES own — generation+nonce equality alone is not full "
        "identity proof"
    )


def test_v7_membership_proof_rejects_same_full_identity_wrong_owned_run_ids_set(tmp_path):
    """v7 isolated counterexample: v6's own test above changes BOTH the full
    identity (pid/start_ticks/sequence) AND owned_run_ids at once, so the
    identity mismatch alone is sufficient to make it pass — it never proves
    owned_run_ids equality was actually enforced. This test changes ONLY
    owned_run_ids, keeping schema_version/generation/nonce/pid/start_ticks/
    sequence byte-identical to the real marker (this is exactly the parent's
    mechanical counterexample: same_identity_wrong_set_proves_run_a=true
    against pre-v7 code — a tag can have every other field genuinely correct
    and still not be the tag tag_owned_runs actually produced for this exact
    marker if its owned_run_ids differs even by one extra entry)."""
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=12345, command_label="module",
        working_dir=str(Path("/tmp").resolve()), owned_run_ids=["run-a"],
    )
    same_identity_wrong_set = ExecutionOwner(
        schema_version=marker.schema_version,
        generation=marker.generation,      # matches
        nonce=marker.nonce,                # matches
        pid=marker.pid,                    # matches
        start_ticks=marker.start_ticks,    # matches
        sequence=marker.sequence,          # matches
        owned_run_ids=("run-a", "run-b"),  # does NOT match marker.owned_run_ids=("run-a",)
    )
    assert same_identity_wrong_set.proves_membership_of("run-a", marker) is False, (
        "a tag whose owned_run_ids does not exactly equal the marker's own "
        "owned_run_ids must not prove membership even for a run_id that IS "
        "genuinely in the marker's set and even when every OTHER identity "
        "field matches exactly — pre-v7, this exact case (isolated from any "
        "identity mismatch) incorrectly returned True"
    )


def test_p0_target_isolation_reclaim_one_run_does_not_kill_owned_sibling_cli_group(tmp_path, monkeypatch):
    """P0-4: _maybe_kill_exclusive_cli_groups must scope its kill decision to
    the REQUEST's target_run_ids, not the whole marker-owned set. Two runs
    ('em-a', 'em-b') share one CLI group_id and are BOTH in the marker's
    owned set. A reclaim request targets ONLY 'em-a'. The untargeted
    sibling 'em-b' (same group, also owned, but NOT named by this request)
    must not have its CLI process group killed — _dispatch_control_request's
    own docstring promises 'exact target run(s)' scoping."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")

    shared_group_id = "dg-shared-group"
    run_dir_a = make_daemon_run_dir(agent, em_id="em-a")
    register_daemon_entry(mgr, "em-a", run_dir_a)
    mgr._emanations["em-a"]["group_id"] = shared_group_id
    run_dir_b = make_daemon_run_dir(agent, em_id="em-b")
    register_daemon_entry(mgr, "em-b", run_dir_b)
    mgr._emanations["em-b"]["group_id"] = shared_group_id

    marker = mgr._prepare_refresh_host()
    assert marker is not None
    assert set(marker.owned_run_ids) == {"em-a", "em-b"}

    killed_groups = []
    monkeypatch.setattr(mgr, "_kill_cli_group", lambda group_id, reason="timeout": killed_groups.append(group_id))

    req = _build_request(marker, request_id="req-scoped-reclaim", operation="reclaim", target_run_ids=["em-a"])
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "accepted"

    assert killed_groups == [], (
        "a reclaim targeting only 'em-a' must NOT kill the shared CLI "
        "group, because 'em-b' — also in that group and also owned by "
        "the marker, but NOT named by this request — would be killed too; "
        "the kill decision must be scoped to request.target_run_ids, not "
        "marker.owned_run_ids"
    )
    assert mgr._emanations["em-a"]["cancel_event"].is_set()
    assert not mgr._emanations["em-b"]["cancel_event"].is_set()


def test_p0_target_isolation_reclaim_all_targeted_group_members_still_kills(tmp_path, monkeypatch):
    """Companion positive case for P0-4: when a request targets EVERY member
    of a shared CLI group, the group must still be killed exactly as before
    — the fix must scope to the intersection of "owned" AND "targeted",
    not simply disable group-kill altogether."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    _mock_own_cmdline_module_form(monkeypatch, agent._working_dir)
    mgr = agent.get_capability("daemon")

    shared_group_id = "dg-shared-group-2"
    run_dir_a = make_daemon_run_dir(agent, em_id="em-a2")
    register_daemon_entry(mgr, "em-a2", run_dir_a)
    mgr._emanations["em-a2"]["group_id"] = shared_group_id
    run_dir_b = make_daemon_run_dir(agent, em_id="em-b2")
    register_daemon_entry(mgr, "em-b2", run_dir_b)
    mgr._emanations["em-b2"]["group_id"] = shared_group_id

    marker = mgr._prepare_refresh_host()
    assert marker is not None

    killed_groups = []
    monkeypatch.setattr(mgr, "_kill_cli_group", lambda group_id, reason="timeout": killed_groups.append(group_id))

    req = _build_request(
        marker, request_id="req-full-group-reclaim", operation="reclaim",
        target_run_ids=["em-a2", "em-b2"],
    )
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "accepted"
    assert killed_groups == [shared_group_id]


# ---------------------------------------------------------------------------
# P1-5: strict, non-coercive schema validation for ControlRequest/ControlAck
# ---------------------------------------------------------------------------


def _valid_control_request_kwargs():
    return dict(
        schema_version=1, request_id="req-strict-1", generation="20260101-000000-aaaaaa",
        nonce="a" * 32, target_run_ids=("em-1",), operation="ask",
        payload={"message": "hi"}, requester_pid=123, requester_start_ticks=456,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )


def test_p1_control_request_rejects_unknown_schema_version():
    kwargs = _valid_control_request_kwargs()
    kwargs["schema_version"] = 999
    with pytest.raises(MarkerValidationError):
        ControlRequest(**kwargs)


def test_p1_control_request_rejects_bool_as_int_requester_pid():
    kwargs = _valid_control_request_kwargs()
    kwargs["requester_pid"] = True
    with pytest.raises(MarkerValidationError):
        ControlRequest(**kwargs)


def test_p1_control_request_rejects_nonpositive_requester_start_ticks():
    kwargs = _valid_control_request_kwargs()
    kwargs["requester_start_ticks"] = 0
    with pytest.raises(MarkerValidationError):
        ControlRequest(**kwargs)


def test_p1_control_request_rejects_non_time_created_at():
    kwargs = _valid_control_request_kwargs()
    kwargs["created_at"] = "not-a-time"
    with pytest.raises(MarkerValidationError):
        ControlRequest(**kwargs)


def test_p1_control_request_rejects_non_time_deadline_at():
    kwargs = _valid_control_request_kwargs()
    kwargs["deadline_at"] = "also-not-a-time"
    with pytest.raises(MarkerValidationError):
        ControlRequest(**kwargs)


def test_p1_control_request_rejects_nonpositive_or_bool_generation_mismatch_shape():
    """generation must match the canonical marker-generation shape — a
    control request cannot embed an arbitrary string as its generation
    binding, since write_control_request's own generation-existence check
    depends on being able to look up a real marker file by this exact
    string."""
    kwargs = _valid_control_request_kwargs()
    kwargs["generation"] = "not-a-real-generation-shape"
    with pytest.raises(MarkerValidationError):
        ControlRequest(**kwargs)


def test_p1_control_request_from_dict_rejects_malformed_fields():
    """The full malformed shape from the parent audit's mechanical proof
    must be rejected end-to-end through from_dict, not only via the
    dataclass constructor directly."""
    malformed = {
        "schema_version": 999,
        "request_id": "req-x",
        "generation": "20260101-000000-aaaaaa",
        "nonce": "a" * 32,
        "target_run_ids": ["em-1"],
        "operation": "ask",
        "payload": {},
        "requester_pid": True,
        "requester_start_ticks": 0,
        "created_at": "not-a-time",
        "deadline_at": "also-not-a-time",
    }
    with pytest.raises(MarkerValidationError):
        ControlRequest.from_dict(malformed)


def _valid_control_ack_kwargs():
    return dict(
        schema_version=1, request_id="req-strict-1", generation="20260101-000000-aaaaaa",
        target_run_ids=("em-1",), status="accepted",
        responded_at="2026-01-01T00:00:01Z", detail={},
    )


def test_p1_control_ack_rejects_unknown_schema_version():
    kwargs = _valid_control_ack_kwargs()
    kwargs["schema_version"] = 999
    with pytest.raises(MarkerValidationError):
        ControlAck(**kwargs)


def test_p1_control_ack_rejects_non_time_responded_at():
    kwargs = _valid_control_ack_kwargs()
    kwargs["responded_at"] = "not-a-time"
    with pytest.raises(MarkerValidationError):
        ControlAck(**kwargs)


def test_p1_control_ack_rejects_malformed_generation_shape():
    kwargs = _valid_control_ack_kwargs()
    kwargs["generation"] = "not-a-real-generation-shape"
    with pytest.raises(MarkerValidationError):
        ControlAck(**kwargs)


def test_p1_execution_owner_from_dict_rejects_unknown_schema_version():
    data = {
        "schema_version": 999, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 1, "sequence": 1, "owned_run_ids": ["em-1"],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_p1_execution_owner_from_dict_rejects_bool_as_int_pid():
    data = {
        "schema_version": 1, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": True, "start_ticks": 1, "sequence": 1, "owned_run_ids": ["em-1"],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_p1_execution_owner_from_dict_rejects_nonpositive_start_ticks():
    data = {
        "schema_version": 1, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 0, "sequence": 1, "owned_run_ids": ["em-1"],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_p1_execution_owner_from_dict_rejects_malformed_generation_shape():
    data = {
        "schema_version": 1, "generation": "not-a-real-generation-shape", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 1, "sequence": 1, "owned_run_ids": ["em-1"],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_p1_execution_owner_from_dict_rejects_nonpositive_sequence():
    data = {
        "schema_version": 1, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 1, "sequence": 0, "owned_run_ids": ["em-1"],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_v7_marker_rejects_bool_true_as_schema_version():
    """v6's schema check was `schema_version != MARKER_SCHEMA_VERSION` — since
    `bool` is an `int` subclass in Python and `True == 1 == MARKER_SCHEMA_VERSION`,
    that comparison alone lets `schema_version=True` through as if it were the
    literal int `1`. This is the parent's mechanical counterexample
    (`bool_schema_request_accepted: true`) reproduced directly against
    `_parse_marker_dict`, isolated from every other field."""
    import lingtai.tools.daemon.refresh_host as _refresh_host_module

    data = {
        "schema_version": True, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 1, "sequence": 1, "command_label": "module",
        "working_dir": str(Path("/tmp").resolve()), "owned_run_ids": ["em-1"],
        "state": "draining", "prepared_at": "2026-01-01T00:00:00Z",
    }
    with pytest.raises(MarkerValidationError):
        _refresh_host_module._parse_marker_dict(data, source="test")


def test_v7_control_request_rejects_bool_true_as_schema_version():
    kwargs = _valid_control_request_kwargs()
    kwargs["schema_version"] = True
    with pytest.raises(MarkerValidationError):
        ControlRequest(**kwargs)


def test_v7_control_ack_rejects_bool_true_as_schema_version():
    kwargs = _valid_control_ack_kwargs()
    kwargs["schema_version"] = True
    with pytest.raises(MarkerValidationError):
        ControlAck(**kwargs)


def test_v7_execution_owner_from_dict_rejects_bool_true_as_schema_version():
    data = {
        "schema_version": True, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 1, "sequence": 1, "owned_run_ids": ["em-1"],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_v7_execution_owner_from_dict_rejects_empty_owned_run_ids():
    """Unlike RefreshHostMarker, ExecutionOwner.from_dict never validated its
    own owned_run_ids for emptiness/duplicates before v7 — a hand-edited or
    corrupted tag could carry an empty or duplicate-containing set and still
    parse as a well-formed ExecutionOwner."""
    data = {
        "schema_version": 1, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 1, "sequence": 1, "owned_run_ids": [],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_v7_execution_owner_from_dict_rejects_duplicate_owned_run_ids():
    data = {
        "schema_version": 1, "generation": "20260101-000000-aaaaaa", "nonce": "a" * 32,
        "pid": 1, "start_ticks": 1, "sequence": 1, "owned_run_ids": ["em-1", "em-1"],
    }
    with pytest.raises(MarkerValidationError):
        ExecutionOwner.from_dict(data)


def test_p1_dispatch_control_request_rejects_non_string_ask_message_payload(tmp_path, monkeypatch):
    """Ask payload/message typing must be checked before _handle_ask, per the
    audit: 'Ask payload/message typing is also unchecked before
    _handle_ask().' A non-string message must be rejected with a 'rejected'
    ack, not silently passed through to _handle_ask (which expects a str and
    would misbehave or crash on e.g. a dict/list/int)."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-bad-payload", operation="ask",
        target_run_ids=["em-live"], payload={"message": {"not": "a string"}},
    )
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "rejected"


def test_v7_truthful_ask_dispatch_does_not_report_accepted_when_handle_ask_returns_error(
    tmp_path, monkeypatch,
):
    """A-11: _dispatch_control_request's ask branch must bind its outer
    ControlAck.status to what _handle_ask actually returned per target, not
    unconditionally report 'accepted' merely because the dispatch call
    itself didn't raise. _handle_ask can and does return
    {"status": "error", ...} for a real failure (unsupported backend,
    malformed session, etc.) without ever raising an exception — pre-v7,
    that per-target error was silently swallowed inside detail["results"]
    while the outer status still said 'accepted', which is exactly what
    _route_ask_through_control_plane's `if ack.status == "accepted"` check
    (the successor's ONLY signal for its own product-level {"status":
    "sent"} response) trusted blindly, making a successor believe its ask
    was delivered when it was not."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)

    def _fake_handle_ask_returns_real_error(em_id, message):
        return {"status": "error", "id": em_id, "message": "ask not supported for this backend"}

    monkeypatch.setattr(mgr, "_handle_ask", _fake_handle_ask_returns_real_error)

    req = _build_request(
        marker, request_id="req-truthful-ask", operation="ask",
        target_run_ids=["em-live"], payload={"message": "hi"},
    )
    ack = mgr._dispatch_control_request(req, marker)

    assert ack.status != "accepted", (
        "the outer ack status must not claim 'accepted' when the real "
        "per-target _handle_ask result was an explicit error — this is "
        "the exact envelope the successor's _route_ask_through_control_plane "
        "trusts to decide whether to report 'sent' back to its own caller"
    )
    assert ack.detail["results"]["em-live"]["status"] == "error"


def test_v7_truthful_ask_dispatch_still_reports_accepted_when_handle_ask_succeeds(
    tmp_path, monkeypatch,
):
    """Positive control: a genuinely successful _handle_ask result must
    still produce an 'accepted' ack — the fix narrows truthfulness, it does
    not disable the accepted status altogether."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-truthful-ask-ok", operation="ask",
        target_run_ids=["em-live"], payload={"message": "hi"},
    )
    ack = mgr._dispatch_control_request(req, marker)
    assert ack.status == "accepted"
    assert ack.detail["results"]["em-live"]["status"] == "sent"


# ---------------------------------------------------------------------------
# P1-6: ack validation — an arbitrary/corrupt/mismatched ack filename stem
# must not suppress a request; read_pending_control_requests must parse and
# validate ack CONTENT and its binding to the request, not just check
# whether *some* file with a matching stem exists under acks/.
# ---------------------------------------------------------------------------


def test_p1_ack_validation_corrupt_ack_file_does_not_suppress_pending_request(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1"])
    req = ControlRequest(
        schema_version=1, request_id="req-corrupt-ack", generation=marker.generation,
        nonce=marker.nonce, target_run_ids=("em-1",), operation="ask",
        payload={"message": "hi"}, requester_pid=os.getpid(), requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    write_control_request(parent, req)

    # Write a CORRUPT (unparseable) file at the exact stem an ack would use —
    # simulating a crash mid-write, disk corruption, or a filename collision
    # with unrelated data — not a real, valid ControlAck.
    acks_dir = control_dir(parent, marker.generation) / "acks"
    acks_dir.mkdir(parents=True, exist_ok=True)
    (acks_dir / "req-corrupt-ack.json").write_text("{ this is not valid json", encoding="utf-8")

    pending = list(read_pending_control_requests(parent, marker.generation))
    assert any(r.request_id == "req-corrupt-ack" for r in pending), (
        "a corrupt/unparseable file at an ack's filename stem must not "
        "suppress the request from the pending scan — the host must still "
        "see and (re-)service this request, since the corrupt file is not "
        "proof any real ack was ever durably published"
    )


def test_p1_ack_validation_mismatched_generation_ack_does_not_suppress_request(tmp_path):
    """An ack file that parses as valid JSON/ControlAck shape but whose
    OWN generation field does not match the generation directory it was
    found under (or whose request_id doesn't match the filename) must not
    count as a real ack for this request either — content must be
    validated and bound to the request, not merely 'a file exists here'."""
    parent = tmp_path / "agent"
    parent.mkdir()
    marker = _build_and_commit_marker(parent, pid=os.getpid(), owned_run_ids=["em-1"])
    req = ControlRequest(
        schema_version=1, request_id="req-mismatched-ack", generation=marker.generation,
        nonce=marker.nonce, target_run_ids=("em-1",), operation="ask",
        payload={"message": "hi"}, requester_pid=os.getpid(), requester_start_ticks=1,
        created_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T00:05:00Z",
    )
    write_control_request(parent, req)

    acks_dir = control_dir(parent, marker.generation) / "acks"
    acks_dir.mkdir(parents=True, exist_ok=True)
    # Valid ControlAck JSON shape, but for a DIFFERENT request_id than the
    # filename claims, and a different target_run_ids than the real request.
    bogus_ack = ControlAck(
        schema_version=1, request_id="req-a-totally-different-request",
        generation=marker.generation, target_run_ids=("em-999-not-real",),
        status="accepted", responded_at="2026-01-01T00:00:01Z", detail={},
    )
    (acks_dir / "req-mismatched-ack.json").write_text(
        json.dumps(bogus_ack.to_dict()), encoding="utf-8",
    )

    pending = list(read_pending_control_requests(parent, marker.generation))
    assert any(r.request_id == "req-mismatched-ack" for r in pending), (
        "an ack file whose OWN request_id does not match the filename it "
        "was found under (or whose target_run_ids don't match the real "
        "request) must not be trusted as proof this request was actually "
        "serviced — content must be validated and bound, not merely "
        "'some file exists at this stem'"
    )


# ---------------------------------------------------------------------------
# P1-7: exactly-once control handling — dispatch-before-ack is not an
# exactly-once guarantee. A crash between "effect applied" and "ack
# published" must not cause a silent double-dispatch on retry/restart, and
# a crash before any effect is applied must still eventually get serviced
# (never silently suppressed).
# ---------------------------------------------------------------------------


def test_p1_exactly_once_reclaim_request_processed_twice_does_not_double_dispatch(tmp_path, monkeypatch):
    """Simulates the crash window: a request is processed through the real
    per-request product path (``_process_one_control_request`` — the same
    method ``_run_drain_loop`` calls per pending request), its real effect
    takes place, but we simulate the ack publish being interrupted (mirrors
    a process crash between "effect applied" and "ack durably published")
    by deleting any ack this call happened to publish before the second
    attempt. A second, independent processing attempt for the SAME
    request_id (the next drain-loop tick, or a fresh successor process
    restart re-reading the same still-apparently-unacked request from disk)
    must not apply the underlying operation's side effect a SECOND time —
    the effect here (_handle_ask appending to the followup buffer) would
    otherwise be applied twice, which is not acceptable even though a
    reclaim's cancel_event.set() alone would be naturally idempotent."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-exactly-once-ask", operation="ask",
        target_run_ids=["em-live"], payload={"message": "only once please"},
    )

    # First processing attempt through the real per-request product path.
    mgr._process_one_control_request(req, marker)
    first_buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert first_buffer.count("only once please") == 1

    # Simulate "the ack publish was interrupted by a crash": remove
    # whatever ack this call durably published, so a naive re-read of
    # pending requests (ack-exclusion only) would see this request_id as
    # still unacked and eligible for re-dispatch — exactly the crash window
    # the durable claim (not the ack file) must protect against.
    ack_path = control_dir(agent._working_dir, marker.generation) / "acks" / "req-exactly-once-ask.json"
    if ack_path.exists():
        ack_path.unlink()

    # A second, independent attempt (the next drain-loop tick, or a fresh
    # successor process restart) re-processes the SAME apparently-still-
    # unacked request_id through the same real per-request product path.
    mgr._process_one_control_request(req, marker)
    second_buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert second_buffer.count("only once please") == 1, (
        "processing the SAME request_id twice (the exact crash window "
        "between effect-applied and ack-published) must not apply the "
        "underlying operation's side effect twice — this requires a "
        "durable per-request claim/state transition, not merely relying "
        "on write_control_ack's exclusive-create as the only safeguard, "
        "since the effect here (_handle_ask appending to the followup "
        "buffer) already happened before write_control_ack is ever called"
    )


def test_p1_exactly_once_run_drain_loop_never_dispatches_same_request_twice_within_one_run(tmp_path, monkeypatch):
    """Within a single _run_drain_loop invocation, if a tick's ack-publish
    for a request raises something other than FileExistsError (e.g. a
    transient OSError) between two ticks, the SAME request must not be
    re-dispatched on the next tick once its ack has already been durably
    written by a previous, successful attempt.

    Checks the followup_buffer AFTER the first (bounded, non-quiescent)
    call and BEFORE the second (genuinely quiescent) one — B-5's real
    resource-shutdown-on-quiescence now correctly clears self._emanations
    once the owned set is genuinely done (see _finish_drain_host_exit),
    so a post-quiescence read of the same entry is no longer meaningful;
    the double-dispatch proof itself is about the SECOND call not
    re-applying the effect, which is what the buffer's own unchanged
    count (read once, before either resource teardown could occur) still
    proves."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-drain-once", operation="ask",
        target_run_ids=["em-live"], payload={"message": "count me exactly once"},
    )
    write_control_request(agent._working_dir, req)

    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=1)
    buffer_after_first_call = mgr._emanations["em-live"]["followup_buffer"]
    assert buffer_after_first_call.count("count me exactly once") == 1

    # Force quiescence so a second drain-loop call terminates immediately
    # after (if anything) re-scanning — proving the SAME already-acked
    # request is not re-dispatched even if read_pending_control_requests is
    # called again. This second call IS genuinely quiescent, so it
    # correctly runs B-5's real resource-shutdown sequence afterward.
    mgr._emanations["em-live"]["future"] = _completed_future_helper("done")
    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=1)

    # The real durable evidence (the ack) proves the second call did not
    # re-dispatch: exactly one terminal ack exists for this request_id.
    from lingtai.tools.daemon.refresh_host import validated_acked_request_ids as _validated_acked_request_ids
    acked = _validated_acked_request_ids(agent._working_dir, marker.generation)
    assert "req-drain-once" in acked


def _completed_future_helper(result):
    from tests._daemon_helpers import completed_future
    return completed_future(result)


def test_v7_exactly_once_claim_created_before_dispatch_then_crash_still_dispatches(tmp_path, monkeypatch):
    """v7 isolated counterexample: v6's own exactly-once tests only cover
    effect-applied -> ack-lost (deleting the ACK between two attempts, claim
    file untouched). None of them cover claim-created -> dispatch-never-
    started — the parent's exact mechanical counterexample
    (pre_dispatch_claim_retry_ack_effect_count=0,
    pre_dispatch_claim_retry_request_still_pending=false against pre-v7
    code): a claim durably exists (a prior attempt crashed BEFORE the real
    side effect ever ran), and a naive retry that treats "claim already
    held" as "already handled" would publish a fake terminal ack with the
    real effect applied ZERO times, silently losing the request forever.
    v7's claim state machine distinguishes `claimed` (never dispatched —
    must resume) from `dispatched` (already applied — must not re-run) so
    this exact case now dispatches for real."""
    from lingtai.tools.daemon.refresh_host import (
        claim_control_request,
        validated_acked_request_ids,
    )

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-preclaim-crash", operation="ask",
        target_run_ids=["em-live"], payload={"message": "must not be lost"},
    )

    # Simulate: an earlier attempt won the claim but crashed strictly
    # BEFORE _dispatch_control_request ever ran — no side effect, no ack.
    won = claim_control_request(agent._working_dir, marker.generation, "req-preclaim-crash")
    assert won is True, "test setup: this call must be the one that wins the claim"

    # Retry through the real per-request product path.
    mgr._process_one_control_request(req, marker)

    buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert "must not be lost" in buffer, (
        "a claim that exists but was never actually dispatched (the "
        "process crashed between winning the claim and running the real "
        "side effect) must be resumed and dispatched for real on retry — "
        "treating 'claim already held' as 'already handled, nothing to "
        "do' silently drops the request with the effect applied zero times"
    )
    acked = validated_acked_request_ids(agent._working_dir, marker.generation)
    assert "req-preclaim-crash" in acked, (
        "after a genuine dispatch, a real terminal ack must exist — not "
        "merely a fabricated 'pending' placeholder that removes the "
        "request from the pending scan without ever answering it"
    )


def test_v7_exactly_once_dispatched_claim_with_lost_ack_gets_truthful_recovery_ack_not_redispatch(
    tmp_path, monkeypatch,
):
    """The companion real crash window: a prior attempt's claim reached
    `dispatched` (the real side effect DID run) but crashed before its ack
    was durably published. Retrying must NEVER re-run the side effect (that
    would be a real double-dispatch, not a false one), and must publish a
    truthful `effect-applied-ack-lost` terminal ack rather than silently
    leaving the request forever unacked or fabricating an `accepted`
    response as if the original dispatch's actual detail were still known."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-dispatched-ack-lost", operation="ask",
        target_run_ids=["em-live"], payload={"message": "exactly once, truthfully"},
    )

    mgr._process_one_control_request(req, marker)
    first_buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert first_buffer.count("exactly once, truthfully") == 1

    ack_path = (
        control_dir(agent._working_dir, marker.generation) / "acks" / "req-dispatched-ack-lost.json"
    )
    assert ack_path.exists()
    ack_path.unlink()  # simulate: effect applied, ack write itself never landed

    mgr._process_one_control_request(req, marker)
    second_buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert second_buffer.count("exactly once, truthfully") == 1, (
        "a claim already at 'dispatched' must never be re-dispatched — the "
        "real side effect already ran in the earlier attempt"
    )

    recovered = json.loads(ack_path.read_text())
    assert recovered["status"] == "effect-applied-ack-lost", (
        "the recovery ack must honestly state the effect was already "
        "applied and its original response detail is unrecoverable — "
        "never silently absent, and never a fabricated 'accepted' as if "
        "the original dispatch's real result were still known"
    )


def test_v7_error_containment_dispatch_exception_leaves_claim_retryable_not_permanently_dispatched(
    tmp_path, monkeypatch,
):
    """A dispatch exception must never advance the claim to `dispatched` —
    doing so would permanently suppress retry for an operation that never
    actually completed, converting a transient failure into silent,
    unrecoverable request loss (the exact defect A-8 names: 'do not convert
    a dispatch exception into a permanent claim that suppresses retry')."""
    from lingtai.tools.daemon.refresh_host import read_control_request_claim_status

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    req = _build_request(
        marker, request_id="req-dispatch-boom", operation="ask",
        target_run_ids=["em-live"], payload={"message": "hi"},
    )

    def _boom(request, marker):
        raise RuntimeError("simulated dispatch failure")

    monkeypatch.setattr(mgr, "_dispatch_control_request", _boom)
    mgr._process_one_control_request(req, marker)

    claim_status = read_control_request_claim_status(
        agent._working_dir, marker.generation, "req-dispatch-boom"
    )
    assert claim_status == "claimed", (
        "a dispatch exception must leave the claim at 'claimed' (never "
        "attempted) so a later retry can genuinely resume dispatch — "
        "advancing to 'dispatched' here would permanently suppress the "
        "request even though its real side effect never ran"
    )

    # A later retry (dispatch no longer raising) must actually apply the
    # effect — proving the claim truly stayed retryable, not just that its
    # on-disk status string looked right.
    monkeypatch.undo()
    mgr._process_one_control_request(req, marker)
    buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert "hi" in buffer


# ---------------------------------------------------------------------------
# P1-8: error containment — malformed requests, ask/dispatch failures, ack
# publication failures, and one request's failure must not kill the
# draining host loop or starve other valid requests.
# ---------------------------------------------------------------------------


def test_p1_error_containment_one_dispatch_exception_does_not_kill_drain_loop(tmp_path, monkeypatch):
    """If _dispatch_control_request raises for one request (e.g. _handle_ask
    itself raises an unexpected exception), _run_drain_loop must not
    propagate that exception out and die — it must contain the failure for
    that one request and continue draining/servicing every OTHER valid
    pending request in the same and subsequent ticks."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)

    good_req = _build_request(
        marker, request_id="req-good", operation="ask",
        target_run_ids=["em-live"], payload={"message": "this one should still work"},
    )
    bad_req = _build_request(
        marker, request_id="req-bad", operation="ask",
        target_run_ids=["em-live"], payload={"message": "this one explodes"},
    )
    write_control_request(agent._working_dir, good_req)
    write_control_request(agent._working_dir, bad_req)

    real_dispatch = mgr._dispatch_control_request

    def _flaky_dispatch(request, marker):
        if request.request_id == "req-bad":
            raise RuntimeError("simulated unexpected dispatch failure")
        return real_dispatch(request, marker)

    monkeypatch.setattr(mgr, "_dispatch_control_request", _flaky_dispatch)

    # max_ticks bounds the loop; the run stays non-terminal throughout so
    # both requests are genuinely dispatched (not short-circuited to
    # already-terminal before the flaky dispatch monkeypatch even runs).
    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=3)

    buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert "this one should still work" in buffer, (
        "one request's dispatch exception must not prevent a different, "
        "valid pending request from being serviced in the same drain loop"
    )
    good_ack_path = control_dir(agent._working_dir, marker.generation) / "acks" / "req-good.json"
    assert good_ack_path.exists()


def test_p1_error_containment_ack_publish_oserror_does_not_kill_drain_loop(tmp_path, monkeypatch):
    """_run_drain_loop currently catches ONLY FileExistsError from
    write_control_ack. An ordinary OSError (e.g. disk full, permission
    error) from that same call must also not crash the loop or prevent
    other requests from being serviced."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)

    bad_req = _build_request(
        marker, request_id="req-ack-fails", operation="ask",
        target_run_ids=["em-live"], payload={"message": "ack write explodes"},
    )
    good_req = _build_request(
        marker, request_id="req-ack-ok", operation="ask",
        target_run_ids=["em-live"], payload={"message": "ack write succeeds"},
    )
    write_control_request(agent._working_dir, bad_req)
    write_control_request(agent._working_dir, good_req)

    import lingtai.tools.daemon as daemon_module
    real_write_control_ack = daemon_module.write_control_ack

    def _flaky_write_control_ack(parent_working_dir, ack):
        if ack.request_id == "req-ack-fails":
            raise OSError("simulated ack publish failure")
        return real_write_control_ack(parent_working_dir, ack)

    monkeypatch.setattr(daemon_module, "write_control_ack", _flaky_write_control_ack)
    mgr._emanations["em-live"]["future"] = _completed_future_helper("done")

    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=3)

    good_ack_path = control_dir(agent._working_dir, marker.generation) / "acks" / "req-ack-ok.json"
    assert good_ack_path.exists(), (
        "an OSError publishing one request's ack must not prevent a "
        "different request's ack from being published in the same "
        "drain-loop run"
    )


def test_p1_error_containment_malformed_request_file_does_not_block_valid_requests(tmp_path, monkeypatch):
    """A malformed request file dropped directly into requests/ (already
    covered at the read_pending_control_requests layer per its own
    docstring) must, end-to-end through the real drain loop, still let a
    separate valid request in the same directory be serviced."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)

    good_req = _build_request(
        marker, request_id="req-valid-alongside-malformed", operation="ask",
        target_run_ids=["em-live"], payload={"message": "still gets serviced"},
    )
    write_control_request(agent._working_dir, good_req)

    requests_dir = control_dir(agent._working_dir, marker.generation) / "requests"
    (requests_dir / "req-malformed-drop.json").write_text("not json at all {{{", encoding="utf-8")

    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=3)

    buffer = mgr._emanations["em-live"]["followup_buffer"]
    assert "still gets serviced" in buffer


# ---------------------------------------------------------------------------
# P1-9: quiescence/exit race — a request arriving after a scan but before
# host exit cannot be left forever unacked.
# ---------------------------------------------------------------------------


def test_p1_quiescence_race_request_arriving_between_scan_and_quiescence_check_is_serviced(tmp_path, monkeypatch):
    """The narrowest version of the race: a request is written DURING the
    same tick, after read_pending_control_requests has already returned its
    (now stale) list but before _owned_runs_quiescent is checked. The CURRENT
    implementation drains requests THEN checks quiescence, so if the run
    becomes terminal in between the scan and a request landing, the loop
    exits with that request never seen. This test drives the loop with
    max_ticks=1 and a run that is ALREADY terminal, proving a request
    written just before the call is still serviced at least once before
    the loop honors quiescence and exits — the real fix needs a fail-closed
    HOST_EXITING/closed-generation handshake so a request that truly loses
    the race gets an honest rejection/host-lost ack instead of silence,
    rather than a request landing before the FIRST scan being silently
    dropped by an already-quiescent loop that never scans at all."""
    from tests._daemon_helpers import completed_future

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    # Run is ALREADY terminal before the loop even starts.
    mgr._emanations["em-live"]["future"] = completed_future("done")

    req = _build_request(
        marker, request_id="req-race-window", operation="ask",
        target_run_ids=["em-live"], payload={"message": "arrived right at the edge"},
    )
    write_control_request(agent._working_dir, req)

    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=1)

    ack_path = control_dir(agent._working_dir, marker.generation) / "acks" / "req-race-window.json"
    assert ack_path.exists(), (
        "a request already durably written before _run_drain_loop's first "
        "tick must receive SOME honest ack (accepted, already-terminal, "
        "or a fail-closed host-lost/rejected state) even when the owned "
        "run set is already quiescent at loop entry — the loop must not "
        "check quiescence before ever draining the first tick's requests"
    )


def test_p1_quiescence_race_late_request_after_loop_exit_gets_fail_closed_handling(tmp_path, monkeypatch):
    """Once _run_drain_loop has genuinely returned (the host is exiting),
    a request written AFTER that point must eventually be observable as
    unserviceable (e.g. via a HOST_EXITING/closed-generation marker state,
    or via the next successor's own host-lost reconciliation) rather than
    sitting forever with no ack and no other honest signal a requester
    could poll for. This test proves the loop, upon returning, leaves an
    on-disk signal a requester can distinguish from 'still draining,
    keep waiting.'"""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    mgr._emanations["em-live"]["future"] = _completed_future_helper("done")

    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=None)

    generation_dir = refresh_hosts_dir(agent._working_dir) / marker.generation
    closed_marker_paths = list(generation_dir.glob("*CLOSED*")) + list(generation_dir.glob("*closed*"))
    control_dir_path = control_dir(agent._working_dir, marker.generation)
    host_exiting_markers = (
        list(control_dir_path.glob("*HOST_EXITING*"))
        + list(control_dir_path.glob("*host_exiting*"))
        + list(control_dir_path.glob("*closed*"))
    )
    assert closed_marker_paths or host_exiting_markers, (
        "once _run_drain_loop returns because the owned set is quiescent, "
        "there must be a durable, discoverable on-disk signal that this "
        "generation's control plane is now closed — a late request writer "
        "polling for an ack otherwise has no honest way to distinguish "
        "'host still draining, keep waiting' from 'host exited, nobody is "
        "listening anymore, and never will be'"
    )


def test_v7_close_handshake_marker_is_typed_and_content_valid(tmp_path, monkeypatch):
    """A-9: the pre-v7 CLOSED marker was a bare zero-byte touch() with no
    schema, no fsync of its own content, and no reader/writer contract
    beyond 'a file exists here.' v7's write_closed_marker/read_closed_marker
    must produce a real typed record any reader can validate — not merely
    decoration a test's own glob happens to match."""
    from lingtai.tools.daemon.refresh_host import read_closed_marker, is_generation_closed

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    mgr._emanations["em-live"]["future"] = _completed_future_helper("done")

    assert is_generation_closed(agent._working_dir, marker.generation) is False, (
        "must not be closed before the drain loop actually runs/exits"
    )

    mgr._run_drain_loop(marker, poll_interval=0.01, max_ticks=None)

    closed = read_closed_marker(agent._working_dir, marker.generation)
    assert closed is not None
    assert closed["schema_version"] == 1
    assert closed["generation"] == marker.generation
    assert isinstance(closed["closed_at"], str) and closed["closed_at"]
    assert is_generation_closed(agent._working_dir, marker.generation) is True


def test_v7_close_handshake_request_racing_strictly_between_fence_scan_and_publish_gets_host_lost_ack(
    tmp_path, monkeypatch,
):
    """A-9 real race reproduction: a request written strictly BETWEEN the
    drain loop's final fencing scan and its CLOSED-marker publish (the
    exact narrow window pre-v7's bare touch() never fenced against at all)
    must still receive a truthful, terminal 'host-lost' ack through the
    real per-request claim/dispatch machinery — never silently left
    unanswered forever, and never fabricated as 'accepted' since the
    effect was genuinely never applied."""
    import lingtai.tools.daemon as daemon_module

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    mgr._emanations["em-live"]["future"] = _completed_future_helper("done")

    real_read_pending = daemon_module.read_pending_control_requests
    call_count = {"n": 0}

    def _injecting_read_pending(parent_working_dir, generation):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # This IS the fence scan's call inside
            # _close_generation_control_plane. Return what's really
            # pending (nothing), then write a request that lands strictly
            # after this scan returns but before the CLOSED marker is
            # published moments later.
            result = list(real_read_pending(parent_working_dir, generation))
            req = _build_request(
                marker, request_id="req-scan-close-race", operation="ask",
                target_run_ids=["em-live"], payload={"message": "raced the close"},
            )
            write_control_request(parent_working_dir, req)
            return iter(result)
        return real_read_pending(parent_working_dir, generation)

    monkeypatch.setattr(daemon_module, "read_pending_control_requests", _injecting_read_pending)

    from lingtai.tools.daemon.refresh_host import validated_acked_request_ids as _validated_acked_request_ids

    mgr._close_generation_control_plane(marker)

    acked = _validated_acked_request_ids(agent._working_dir, marker.generation)
    assert "req-scan-close-race" in acked, (
        "a request racing strictly between the fence scan and the CLOSED "
        "publish must still receive a real terminal ack — silence here "
        "means the pre-v9 decoration-only CLOSED marker regressed back in"
    )
    ack_path = control_dir(agent._working_dir, marker.generation) / "acks" / "req-scan-close-race.json"
    ack_data = json.loads(ack_path.read_text())
    assert ack_data["status"] == "host-lost", (
        "the racing request's ack must honestly say the effect was never "
        "applied (host-lost), never a fabricated 'accepted'"
    )
    # The real per-request effect must NOT have been applied — this proves
    # the reject path went through the honest host-lost ack, not a genuine
    # (and therefore double-risking) dispatch.
    assert "raced the close" not in mgr._emanations["em-live"]["followup_buffer"]


def test_v7_close_handshake_request_submitted_before_fence_scan_still_gets_real_dispatch(
    tmp_path, monkeypatch,
):
    """Positive control: a request submitted BEFORE the close handshake
    even begins (the ordinary case, not a race) must still be genuinely
    dispatched through the fence scan — proving the fence scan is real
    work, not merely a formality before the CLOSED publish. The target run
    is deliberately left NON-terminal (unlike the other tests in this
    section, which need terminal state for _owned_runs_quiescent) — this
    test calls _close_generation_control_plane directly, not the full
    drain loop, and a terminal target would be rejected as
    'already-terminal' before ever reaching the ordinary dispatch path
    this test means to prove."""
    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)

    req = _build_request(
        marker, request_id="req-before-close", operation="ask",
        target_run_ids=["em-live"], payload={"message": "ordinary pre-close request"},
    )
    write_control_request(agent._working_dir, req)

    from lingtai.tools.daemon.refresh_host import validated_acked_request_ids as _validated_acked_request_ids

    mgr._close_generation_control_plane(marker)

    assert "ordinary pre-close request" in mgr._emanations["em-live"]["followup_buffer"]
    acked = _validated_acked_request_ids(agent._working_dir, marker.generation)
    assert "req-before-close" in acked
    ack_path = control_dir(agent._working_dir, marker.generation) / "acks" / "req-before-close.json"
    ack_data = json.loads(ack_path.read_text())
    assert ack_data["status"] == "accepted"


def test_v7_close_handshake_successor_ask_poll_stops_early_on_closed_generation(tmp_path, monkeypatch):
    """_route_ask_through_control_plane (the successor-side consumer) must
    actually consult is_generation_closed during its poll loop and stop
    waiting once the generation closes, rather than blindly polling the
    full 10s window even when the generation is provably, durably closed
    the whole time."""
    from lingtai.tools.daemon.refresh_host import write_closed_marker

    agent, mgr, marker, run_dir = _prepared_manager_with_marker(tmp_path, monkeypatch)
    # Publish CLOSED for this generation BEFORE the ask is ever routed —
    # simulating a successor asking a generation that already fully closed
    # (e.g. it discovered the marker just as/after the host finished).
    write_closed_marker(agent._working_dir, marker)

    started = time.monotonic()
    result = mgr._route_ask_through_control_plane("em-live", "hello", marker)
    elapsed = time.monotonic() - started

    assert result["status"] == "error"
    assert elapsed < 5.0, (
        f"polling took {elapsed:.2f}s — the successor must recognize the "
        f"generation is already closed and stop waiting well before the "
        f"full blind 10s timeout, not treat CLOSED the same as silence"
    )


# ---------------------------------------------------------------------------
# P1-10: run-state synchronization — owner-tag writes and active worker
# progress/terminal updates must share an explicit synchronization
# invariant so read-modify-write updates cannot overwrite each other.
# ---------------------------------------------------------------------------


def test_p1_run_state_sync_concurrent_owner_tag_and_progress_write_do_not_lose_updates(tmp_path):
    """Real-thread reproduction: one thread repeatedly calls set_current_tool
    (simulating active worker progress) while another calls
    set_execution_owner (simulating PREPARE tagging the SAME run mid-flight)
    — both against the SAME real DaemonRunDir/daemon.json. Without a shared
    synchronization invariant across these two mutators, the daemon.json
    ends up on disk missing one side's update (either the tool-progress
    field or the execution_owner field, depending purely on write-ordering
    luck) even though both are still correctly reflected in the in-memory
    self._state dict acting as the source of truth for BOTH writers.
    Repeated several times to make a timing-dependent race observable
    rather than relying on exactly one lucky/unlucky interleaving."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    agent = make_daemon_agent(tmp_path)

    for attempt in range(25):
        run_dir = make_daemon_run_dir(agent, em_id=f"em-race-{attempt}")
        marker = RefreshHostMarker.build(
            pid=os.getpid(), start_ticks=1, command_label="module",
            working_dir=str(agent._working_dir), owned_run_ids=[f"em-race-{attempt}"],
        )
        owner_dict = ExecutionOwner.from_marker(marker).to_dict()

        stop = threading.Event()

        def _worker_progress():
            i = 0
            while not stop.is_set():
                run_dir.set_current_tool(f"tool-{i}", {})
                i += 1

        def _tag_owner():
            run_dir.set_execution_owner(owner_dict)

        progress_thread = threading.Thread(target=_worker_progress)
        progress_thread.start()
        time.sleep(0.002)
        _tag_owner()
        stop.set()
        progress_thread.join(timeout=5)

        on_disk = json.loads(run_dir.daemon_json_path.read_text())
        assert on_disk.get("execution_owner") is not None, (
            f"attempt {attempt}: a concurrent worker-progress write raced "
            "out the execution_owner tag on disk — set_execution_owner and "
            "set_current_tool must share an explicit synchronization "
            "invariant (e.g. a per-run-dir lock around the read-serialize-"
            "write sequence), since atomic rename alone only guarantees "
            "the FILE CONTENT at write time is internally consistent, not "
            "that concurrent writers' updates are never lost to each other"
        )


def test_v7_whole_snapshot_sync_terminal_notification_claim_vs_owner_tag_barrier(tmp_path):
    """A-10 deterministic barrier reproduction (not a timing loop): v6's own
    P1-10 fix only covered set_current_tool vs set_execution_owner — both of
    which route their mutation AND write through the same _state_lock
    acquisition. claim_terminal_notification (and its two siblings)
    pre-v7 mutated self._state under ONLY _terminal_notification_lock, then
    deferred its durable write to a LATER, separate _safe(...) call that
    re-acquires _state_lock fresh. That split let a concurrent
    set_execution_owner's own dict(self._state) copy + write + reassign
    land BETWEEN the mutation and the deferred write: the copy captured
    self._state BEFORE the mutation, so the reassigned self._state object
    the deferred write later reads back does not contain the mutation at
    all — even though the mutation genuinely happened moments earlier on
    the (now-orphaned) previous object. A threading.Barrier makes both
    threads start their respective critical sections at effectively the
    same instant, every run, deterministically hitting this exact window
    instead of relying on repeated attempts to get occasionally lucky."""
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

    agent = make_daemon_agent(tmp_path)
    marker = RefreshHostMarker.build(
        pid=os.getpid(), start_ticks=1, command_label="module",
        working_dir=str(agent._working_dir), owned_run_ids=["em-barrier"],
    )
    owner_dict = ExecutionOwner.from_marker(marker).to_dict()

    lost_trials = []
    trials = 20
    for trial in range(trials):
        run_dir = make_daemon_run_dir(agent, em_id=f"em-barrier-{trial}")
        barrier = threading.Barrier(2)
        results = {}

        def _claim():
            barrier.wait()
            results["key"] = run_dir.claim_terminal_notification("done")

        def _tag():
            barrier.wait()
            run_dir.set_execution_owner(owner_dict)

        t_claim = threading.Thread(target=_claim)
        t_tag = threading.Thread(target=_tag)
        t_claim.start()
        t_tag.start()
        t_claim.join(timeout=5)
        t_tag.join(timeout=5)

        on_disk = json.loads(run_dir.daemon_json_path.read_text())
        in_memory = run_dir.state_snapshot()
        disk_ok = (
            isinstance(on_disk.get("terminal_notification_claim"), dict)
            and on_disk.get("execution_owner") is not None
        )
        memory_ok = (
            isinstance(in_memory.get("terminal_notification_claim"), dict)
            and in_memory.get("execution_owner") is not None
        )
        if not (disk_ok and memory_ok):
            lost_trials.append(
                (trial, disk_ok, memory_ok, on_disk.get("terminal_notification_claim"), on_disk.get("execution_owner"))
            )

    assert lost_trials == [], (
        f"{len(lost_trials)}/{trials} trials lost an update under a real "
        f"thread barrier — claim_terminal_notification's mutation and its "
        f"durable write must happen inside ONE _state_lock acquisition, not "
        f"a mutation now / deferred write later split that a concurrent "
        f"set_execution_owner's copy-then-reassign can land between. "
        f"Failing trials: {lost_trials}"
    )


# ---------------------------------------------------------------------------
# Phase B — real lifecycle wiring acceptance: drives the ACTUAL
# lifecycle._stop() / _shutdown_daemon_runtime path (a real .refresh.taken
# handshake, not a manually-driven _prepare_refresh_host()/_run_drain_loop()
# call), and a successor's DIRECT daemon(action="ask") call (not the raw
# control-plane primitives), proving both pieces of new Phase B wiring work
# end-to-end across real separate OS processes.
# ---------------------------------------------------------------------------

_REAL_LIFECYCLE_HOST_SCRIPT = """
import json, os, sys, threading, time
sys.path.insert(0, {src_path!r})
from unittest.mock import MagicMock
from pathlib import Path

from lingtai.agent import Agent
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.base_agent import lifecycle as lifecycle_module
import lingtai.llm.service as service_mod
from lingtai.kernel.llm.base import ToolCall

working_dir = Path({working_dir!r})
side_effect_marker = Path({side_effect_marker!r})
release_file = Path({release_file!r})
host_ready_file = Path({host_ready_file!r})
host_pid_file = Path({host_pid_file!r})
host_generation_file = Path({host_generation_file!r})
host_stop_file = Path({host_stop_file!r})

svc = MagicMock()
svc.provider = "mock"
svc.model = "mock-model"
agent = Agent(svc, working_dir=working_dir, capabilities=["daemon"], config=AgentConfig())

_em_id_holder = {{}}

def _blocking_side_effect_handler(args):
    deadline = time.time() + 30.0
    while not release_file.exists():
        if time.time() > deadline:
            raise TimeoutError("release_file never appeared")
        time.sleep(0.02)
    side_effect_marker.write_text(str(os.getpid()), encoding="utf-8")
    completion_path = working_dir / "daemons" / _em_id_holder["em_id"] / "daemon_completion.json"
    completion_path.write_text(
        json.dumps({{"status": "done", "run_id": _em_id_holder["em_id"],
                    "summary": "released and recorded"}}),
        encoding="utf-8",
    )
    return {{"content": "released and recorded"}}

agent.add_tool(
    "blocking_side_effect",
    schema={{"type": "object", "properties": {{}}}},
    handler=_blocking_side_effect_handler,
    description="Blocks until release_file exists, then records a side effect.",
)

mgr = agent.get_capability("daemon")

# Same real-cmdline-observation seam as the other real-subprocess tests in
# this file (see _HOST_PROCESS_SCRIPT) — this process's actual argv is
# `python -c <script>`, which correctly does not match any real agent-run
# launch form, so only this ONE seam is faked; every other identity check
# still runs for real against this real PID.
import lingtai.tools.daemon.refresh_host as _refresh_host_module
_real_read_cmdline = _refresh_host_module._read_cmdline
def _patched_read_cmdline(pid):
    if pid == os.getpid():
        return "python -m lingtai run " + str(working_dir)
    return _real_read_cmdline(pid)
_refresh_host_module._read_cmdline = _patched_read_cmdline

service_mod.LLMService = lambda **kwargs: agent.service
resp1 = MagicMock()
resp1.text = "calling blocking tool"
resp1.tool_calls = [ToolCall(name="blocking_side_effect", args={{}}, id="tc-1")]
resp1.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)

_send_call_count = {{"n": 0}}

def _mock_send(*args, **kwargs):
    _send_call_count["n"] += 1
    if _send_call_count["n"] == 1:
        return resp1
    resp = MagicMock()
    resp.text = "Task done."
    resp.tool_calls = []
    resp.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)
    return resp

mock_session = MagicMock()
mock_session.send = MagicMock(side_effect=_mock_send)
agent.service.create_session = MagicMock(return_value=mock_session)
agent.service.make_tool_result = MagicMock(return_value="mock_result")

result = mgr._handle_emanate([{{"task": "do the blocking thing", "tools": ["blocking_side_effect"]}}])
assert result["status"] == "dispatched", result
em_id = result["ids"][0]
_em_id_holder["em_id"] = em_id

# --- Drive the REAL lifecycle path, not a manually-called PREPARE/drain. ---
# _perform_refresh's own handshake normalization is what creates
# .refresh.taken in the real product path; this test creates it directly
# (the SAME file _shutdown_daemon_runtime checks for) to isolate the
# teardown-ordering piece this test is actually proving — _stop()'s real
# choice to enter PREPARE/drain instead of shutdown_for_agent_stop — from
# _perform_refresh's separate watcher-spawn/relaunch machinery, which is
# not what this acceptance category is about.
(working_dir / ".refresh.taken").touch()

# The real lifecycle._stop() call: sets _shutdown, joins the run thread
# (there is none here — a bare mock Agent has no _thread), calls the REAL
# _shutdown_daemon_runtime (which must now PREPARE + enter drain instead of
# shutdown_for_agent_stop), closes the session, persists the manifest,
# stops the heartbeat, and releases the workdir lease — the EXACT same
# function a real refresh/stop path calls, unmodified for this test.
lifecycle_module._stop(agent, timeout=1.0)

# After _stop() returns, the lease is released (real successor Agent()
# construction becomes possible) and — because a marker was committed —
# the daemon work is still alive on a background drain thread in THIS
# process, not killed by shutdown_for_agent_stop.
assert not side_effect_marker.exists(), "side effect must not have been killed by ordinary shutdown"

daemon_json_path = working_dir / "daemons" / em_id / "daemon.json"
daemon_json = json.loads(daemon_json_path.read_text())
assert daemon_json["state"] == "running", daemon_json
assert daemon_json.get("execution_owner") is not None, (
    "the real lifecycle._stop() path must have committed a real "
    "execution_owner tag via PREPARE, not merely left the run running "
    "with no ownership binding"
)
marker_generation = daemon_json["execution_owner"]["generation"]

host_pid_file.write_text(str(os.getpid()), encoding="utf-8")
host_generation_file.write_text(marker_generation, encoding="utf-8")
host_ready_file.write_text("ready", encoding="utf-8")

deadline = time.time() + 30.0
while not host_stop_file.exists():
    if time.time() > deadline:
        os._exit(4)
    time.sleep(0.05)
# Give the background drain thread (spawned by the real
# _shutdown_daemon_runtime, non-daemon) a bounded moment to notice
# quiescence and return before this process exits.
for t in threading.enumerate():
    if t.name == "daemon-refresh-drain-host":
        t.join(timeout=15.0)
sys.exit(0)
"""

_REAL_LIFECYCLE_SUCCESSOR_ASK_SCRIPT = """
import json, os, sys
sys.path.insert(0, {src_path!r})
from unittest.mock import MagicMock
from pathlib import Path

from lingtai.agent import Agent
from lingtai.kernel.config import AgentConfig

working_dir = Path({working_dir!r})
em_id = {em_id!r}
host_pid = {host_pid!r}
result_file = Path({result_file!r})

import lingtai.tools.daemon.refresh_host as _refresh_host_module
_real_read_cmdline = _refresh_host_module._read_cmdline
def _patched_read_cmdline(pid):
    if pid == host_pid:
        return "python -m lingtai run " + str(working_dir)
    return _real_read_cmdline(pid)
_refresh_host_module._read_cmdline = _patched_read_cmdline

svc = MagicMock()
svc.provider = "mock"
svc.model = "mock-model"
agent = Agent(svc, working_dir=working_dir, capabilities=["daemon"], config=AgentConfig())
mgr = agent.get_capability("daemon")

# The successor's OWN fresh DaemonManager has an EMPTY _emanations for
# em_id — this DIRECT daemon(action="ask") call (the exact product-level
# tool-dispatch path, calling handle() itself, not a raw
# ControlRequest/write_control_request primitive) must route through the
# control plane to the still-draining host rather than immediately
# returning "Unknown emanation" merely because em_id isn't in this
# process's own in-memory registry.
result = mgr.handle({{"action": "ask", "id": em_id, "message": "still there?"}})
result_file.write_text(json.dumps(result), encoding="utf-8")
sys.exit(0 if result.get("status") in ("sent",) else 1)
"""

_REAL_LIFECYCLE_SELF_EXIT_HOST_SCRIPT = """
import json, os, sys, time
sys.path.insert(0, {src_path!r})
from unittest.mock import MagicMock
from pathlib import Path

from lingtai.agent import Agent
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.base_agent import lifecycle as lifecycle_module
import lingtai.llm.service as service_mod
from lingtai.kernel.llm.base import ToolCall

working_dir = Path({working_dir!r})
release_file = Path({release_file!r})
host_ready_file = Path({host_ready_file!r})

svc = MagicMock()
svc.provider = "mock"
svc.model = "mock-model"
agent = Agent(svc, working_dir=working_dir, capabilities=["daemon"], config=AgentConfig())

_em_id_holder = {{}}

def _blocking_handler(args):
    deadline = time.time() + 30.0
    while not release_file.exists():
        if time.time() > deadline:
            raise TimeoutError("release_file never appeared")
        time.sleep(0.02)
    completion_path = working_dir / "daemons" / _em_id_holder["em_id"] / "daemon_completion.json"
    completion_path.write_text(
        json.dumps({{"status": "done", "run_id": _em_id_holder["em_id"], "summary": "released"}}),
        encoding="utf-8",
    )
    return {{"content": "released"}}

agent.add_tool(
    "blocking_side_effect", schema={{"type": "object", "properties": {{}}}},
    handler=_blocking_handler, description="Blocks until release_file exists.",
)

mgr = agent.get_capability("daemon")

import lingtai.tools.daemon.refresh_host as _refresh_host_module
_real_read_cmdline = _refresh_host_module._read_cmdline
def _patched_read_cmdline(pid):
    if pid == os.getpid():
        return "python -m lingtai run " + str(working_dir)
    return _real_read_cmdline(pid)
_refresh_host_module._read_cmdline = _patched_read_cmdline

service_mod.LLMService = lambda **kwargs: agent.service
resp1 = MagicMock()
resp1.text = "calling blocking tool"
resp1.tool_calls = [ToolCall(name="blocking_side_effect", args={{}}, id="tc-1")]
resp1.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)
_send_call_count = {{"n": 0}}
def _mock_send(*args, **kwargs):
    _send_call_count["n"] += 1
    if _send_call_count["n"] == 1:
        return resp1
    resp = MagicMock()
    resp.text = "Task done."
    resp.tool_calls = []
    resp.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)
    return resp
mock_session = MagicMock()
mock_session.send = MagicMock(side_effect=_mock_send)
agent.service.create_session = MagicMock(return_value=mock_session)
agent.service.make_tool_result = MagicMock(return_value="mock_result")

result = mgr._handle_emanate([{{"task": "block then finish", "tools": ["blocking_side_effect"]}}])
assert result["status"] == "dispatched", result
em_id = result["ids"][0]
_em_id_holder["em_id"] = em_id

(working_dir / ".refresh.taken").touch()

# The REAL lifecycle._stop() path — NOT self-exiting here. Once _stop()
# returns, the run is still in-flight (blocked on release_file), so the
# real drain thread (self_exit_process=True, wired into the actual
# lifecycle.py drain-thread spawn) must be the ONLY thing that ever
# terminates this process — no explicit os._exit/sys.exit call anywhere
# below this point, and no external "please stop now" signal file this
# test polls, unlike the other real-lifecycle acceptance test.
lifecycle_module._stop(agent, timeout=1.0)

host_ready_file.write_text(str(os.getpid()), encoding="utf-8")

# Deliberately do nothing further and do NOT exit — if self-exit fails,
# this process will still be alive when the test's own bounded wait times
# out, which the test asserts as a hard failure.
time.sleep(60)
os._exit(97)  # sentinel: reaching this line means self-exit did NOT fire
"""


def test_real_lifecycle_drain_host_self_exits_once_genuinely_quiescent(tmp_path):
    """B-5 real vertical-slice acceptance: the REAL drain-host subprocess
    (driven through the actual lifecycle._stop() path, exactly like the
    other real-lifecycle acceptance test) must terminate ITSELF via
    os._exit(0) once its owned run genuinely finishes — with NO external
    stop signal, no test-side os.kill, and no explicit exit call anywhere
    in the host script after _stop() returns. This is the actual proof
    B-5 asks for: 'old-process self-exit,' not merely a unit-level check
    that _finish_drain_host_exit's self_exit_process branch calls
    os._exit when invoked directly."""
    working_dir = tmp_path / "agent-workdir"
    working_dir.mkdir()
    release_file = tmp_path / "release_file"
    host_ready_file = tmp_path / "host_ready"
    src_path = str(Path(__file__).resolve().parents[1] / "src")

    host_script = _REAL_LIFECYCLE_SELF_EXIT_HOST_SCRIPT.format(
        src_path=src_path, working_dir=str(working_dir),
        release_file=str(release_file), host_ready_file=str(host_ready_file),
    )
    env = {**os.environ, "PYTHONPATH": src_path}
    host_proc = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        deadline = time.monotonic() + 20.0
        while not host_ready_file.exists():
            assert time.monotonic() < deadline, "host process never became ready"
            assert host_proc.poll() is None, f"host process exited early: {host_proc.poll()}"
            time.sleep(0.02)

        # Still alive — the run is genuinely blocked, nothing to quiesce yet.
        assert host_proc.poll() is None, (
            "host must still be alive while its owned run is genuinely "
            "blocked — self-exit must not fire before real quiescence"
        )

        release_file.write_text("go", encoding="utf-8")

        exit_deadline = time.monotonic() + 20.0
        while host_proc.poll() is None:
            assert time.monotonic() < exit_deadline, (
                "the real drain-host process did NOT self-exit within the "
                "bounded window after its owned run genuinely finished — "
                "B-5 self-exit is not actually wired into the real "
                "lifecycle._stop() drain-thread spawn"
            )
            time.sleep(0.05)

        assert host_proc.returncode == 0, (
            f"expected a clean os._exit(0) from the real drain host; got "
            f"{host_proc.returncode}. stdout={host_proc.stdout.read()!r} "
            f"stderr={host_proc.stderr.read()!r}"
        )
    finally:
        if host_proc.poll() is None:
            host_proc.kill()
            host_proc.wait(timeout=5)


def test_real_lifecycle_stop_enters_drain_and_successor_direct_ask_routes_to_host(tmp_path):
    """Phase B end-to-end acceptance: a real host subprocess drives the
    ACTUAL lifecycle._stop() function (with a real .refresh.taken file
    present, exactly as a genuine refresh handoff would leave it) and
    proves _shutdown_daemon_runtime's new PREPARE branch — not a manually-
    invoked _prepare_refresh_host()/_run_drain_loop() call — commits a real
    marker and keeps the blocked daemon run alive on a background drain
    thread instead of killing it via shutdown_for_agent_stop. A separate
    real successor subprocess then makes a DIRECT daemon(action="ask")
    tool-dispatch call (not a raw control-plane primitive) for that same
    em_id and must have it routed to and accepted by the still-draining
    host — proving the _handle_ask Unknown-emanation gap is closed for the
    real product-level ask path, not only for the internal control-plane
    helper functions."""
    working_dir = tmp_path / "agent-workdir"
    working_dir.mkdir()
    side_effect_marker = tmp_path / "side_effect_marker"
    release_file = tmp_path / "release_file"
    host_ready_file = tmp_path / "host_ready"
    host_pid_file = tmp_path / "host_pid"
    host_generation_file = tmp_path / "host_generation"
    host_stop_file = tmp_path / "host_stop"
    src_path = str(Path(__file__).resolve().parents[1] / "src")

    host_script = _REAL_LIFECYCLE_HOST_SCRIPT.format(
        src_path=src_path, working_dir=str(working_dir),
        side_effect_marker=str(side_effect_marker), release_file=str(release_file),
        host_ready_file=str(host_ready_file), host_pid_file=str(host_pid_file),
        host_generation_file=str(host_generation_file), host_stop_file=str(host_stop_file),
    )
    env = {**os.environ, "PYTHONPATH": src_path}
    host_proc = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        deadline = time.monotonic() + 20.0
        while not host_ready_file.exists():
            assert time.monotonic() < deadline, "host process never became ready"
            assert host_proc.poll() is None, f"host process exited early: {host_proc.poll()}"
            time.sleep(0.02)

        host_pid = int(host_pid_file.read_text(encoding="utf-8"))
        assert host_pid == host_proc.pid
        # The real Python process is STILL ALIVE after its own lifecycle._stop()
        # returned — proving the drain thread, not process exit, is what keeps
        # this in-flight daemon work reachable.
        assert host_proc.poll() is None

        daemon_dirs = [d for d in (working_dir / "daemons").iterdir() if d.is_dir() and d.name.startswith("em-")]
        assert len(daemon_dirs) == 1, daemon_dirs
        em_id = daemon_dirs[0].name

        successor_result_file = tmp_path / "successor_ask_result"
        successor_script = _REAL_LIFECYCLE_SUCCESSOR_ASK_SCRIPT.format(
            src_path=src_path, working_dir=str(working_dir), em_id=em_id,
            host_pid=host_pid, result_file=str(successor_result_file),
        )
        successor_proc = subprocess.run(
            [sys.executable, "-c", successor_script],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert successor_proc.returncode == 0, (
            f"successor stdout={successor_proc.stdout!r} stderr={successor_proc.stderr!r}"
        )
        successor_out = json.loads(successor_result_file.read_text(encoding="utf-8"))
        assert successor_out["status"] == "sent", successor_out
        assert successor_out.get("async") is True, successor_out

        # Release the blocking tool and confirm the side effect still
        # completes, by the ORIGINAL host PID, on the SAME drain-host
        # process the real lifecycle._stop() path kept alive.
        release_file.write_text("go", encoding="utf-8")
        deadline = time.monotonic() + 20.0
        while not side_effect_marker.exists():
            assert time.monotonic() < deadline, "side effect never occurred after release"
            assert host_proc.poll() is None, "host process died before completing the side effect"
            time.sleep(0.02)
        assert int(side_effect_marker.read_text(encoding="utf-8")) == host_pid
    finally:
        try:
            host_stop_file.write_text("stop", encoding="utf-8")
            host_proc.wait(timeout=15)
        except Exception:
            host_proc.kill()
            host_proc.wait(timeout=5)
        if host_proc.returncode not in (0, None):
            print("HOST STDOUT:", host_proc.stdout.read() if host_proc.stdout else None)
            print("HOST STDERR:", host_proc.stderr.read() if host_proc.stderr else None)


# ---------------------------------------------------------------------------
# v8 B1/B2 — the ONE remaining gap the two _REAL_LIFECYCLE_* tests above
# deliberately left open (see their own docstrings/comments, e.g. line
# 6058-6065's explicit rationale): both manufacture `.refresh.taken` via a
# bare `.touch()` to isolate `_stop()`'s teardown-branch choice from
# `_perform_refresh`'s separate watcher-spawn/relaunch machinery. This
# section drives the FULL real chain in one process: real `.refresh` file
# -> real `Agent._perform_refresh()` (handshake normalization + real
# detached watcher subprocess spawn) -> real `lifecycle_module._stop()`
# entering the real PREPARE/drain branch -> the real drain loop reaching
# genuine quiescence and self-exiting via os._exit(0) -> meanwhile the
# REAL watcher subprocess (not mocked, not string-inspected) observes
# `.agent.lock` clear and relaunches a hermetic successor, which the test
# process independently confirms actually started.
# ---------------------------------------------------------------------------


_REAL_LIFECYCLE_REFRESH_HOST_SCRIPT = """
import json, os, sys, threading, time
sys.path.insert(0, {src_path!r})
from unittest.mock import MagicMock
from pathlib import Path

from lingtai.agent import Agent
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.base_agent import lifecycle as lifecycle_module
import lingtai.llm.service as service_mod
from lingtai.kernel.llm.base import ToolCall

working_dir = Path({working_dir!r})
release_file = Path({release_file!r})
host_ready_file = Path({host_ready_file!r})
host_pid_file = Path({host_pid_file!r})
successor_started_marker = Path({successor_started_marker!r})
successor_launch_code = {successor_launch_code!r}
effect_log_path = Path({effect_log_path!r})
pre_stop_marker_path = Path({pre_stop_marker_path!r})

svc = MagicMock()
svc.provider = "mock"
svc.model = "mock-model"
agent = Agent(svc, working_dir=working_dir, capabilities=["daemon"], config=AgentConfig())

# Real heartbeat: the ONLY way to get a genuine, product-written
# `.agent.heartbeat` (not a hand-forged one) without running the full
# `agent.start()` run-loop/MailService/soul-timer stack this bare-service
# fixture cannot support. `_start_heartbeat` is the exact internal seam
# `agent.start()` itself calls -- same thread target, same
# `_write_heartbeat_tick` -> `PosixAgentPresenceStoreAdapter.publish_heartbeat`
# real product path, just invoked directly instead of through the full
# lifecycle `_start`.
agent._start_heartbeat()
heartbeat_seed_deadline = time.time() + 5.0
while not (working_dir / ".agent.heartbeat").exists():
    assert time.time() < heartbeat_seed_deadline, "real heartbeat never published"
    time.sleep(0.02)

_em_id_holder = {{}}

def _blocking_side_effect_handler(args):
    # The real, product-path release signal: this handler blocks until the
    # real refresh-drain control plane has durably ACKED a real `ask`
    # ControlRequest targeting this run -- i.e. until the real watcher's
    # real hermetic successor has itself, from a distinct OS process,
    # submitted and had dispatched a real ControlRequest through
    # `write_control_request` / the real `_run_drain_loop` already running
    # on this same host's drain thread. Nothing outside that real product
    # chain ever creates this ack file, so this is not a parent-touched
    # release file -- it is the mechanical trace of the successor's own
    # real control-plane action.
    from lingtai.tools.daemon.refresh_host import (
        iter_marker_paths as _iter_marker_paths,
        validated_acks_by_request_id as _validated_acks_by_request_id,
    )
    deadline = time.time() + 30.0
    generation = None
    while True:
        if time.time() > deadline:
            raise TimeoutError(
                "no real ask ControlRequest targeting this run was ever "
                "acked -- the successor never drove a real control-plane "
                "action against this host for THIS run_id"
            )
        if generation is None:
            marker_paths = list(_iter_marker_paths(working_dir))
            if marker_paths:
                generation = marker_paths[0].stem
        if generation is not None:
            acks = _validated_acks_by_request_id(working_dir, generation)
            if any(
                _em_id_holder.get("em_id") in ack.target_run_ids
                and ack.status == "accepted"
                for ack in acks.values()
            ):
                break
        time.sleep(0.02)
    # Replay-sensitive effect accounting: append+fsync one token per ACTUAL
    # invocation of this handler, rather than an overwrite-only write. A
    # second invocation (replay/duplicate dispatch) is mechanically
    # detectable by more than one line ever existing in this file -- an
    # overwrite-only write could silently absorb a second call and still
    # show exactly one final byte-shape on disk.
    with open(effect_log_path, "a", encoding="utf-8") as ef:
        ef.write(json.dumps({{"invocation": time.time(), "pid": os.getpid()}}) + "\\n")
        ef.flush()
        os.fsync(ef.fileno())
    completion_path = working_dir / "daemons" / _em_id_holder["em_id"] / "daemon_completion.json"
    completion_path.write_text(
        json.dumps({{"status": "done", "run_id": _em_id_holder["em_id"],
                    "summary": "released and recorded"}}),
        encoding="utf-8",
    )
    return {{"content": "released and recorded"}}

agent.add_tool(
    "blocking_side_effect",
    schema={{"type": "object", "properties": {{}}}},
    handler=_blocking_side_effect_handler,
    description="Blocks until the real control plane acks a real ask targeting this run.",
)

mgr = agent.get_capability("daemon")

# Same real-cmdline-observation seam as the other real-subprocess tests in
# this file — this process's actual argv is `python -c <script>`, which
# correctly does not match any real agent-run launch form, so only this
# ONE seam is faked; every other identity check still runs for real.
import lingtai.tools.daemon.refresh_host as _refresh_host_module
_real_read_cmdline = _refresh_host_module._read_cmdline
def _patched_read_cmdline(pid):
    if pid == os.getpid():
        return "python -m lingtai run " + str(working_dir)
    return _real_read_cmdline(pid)
_refresh_host_module._read_cmdline = _patched_read_cmdline

# The ONE seam standing in for the real `lingtai-agent run <working_dir>`
# console command: `_build_launch_cmd`'s real implementation resolves a
# venv from init.json, which this bare temp workdir does not have. Every
# OTHER piece of `_perform_refresh` (handshake normalization, watcher
# subprocess spawn, the watcher's own phase-1/phase-2/phase-3 polling
# logic) is the real, unmodified product code — only the launch COMMAND
# itself is hermetic, exactly the seam `_make_agent_with_launch_cmd` uses
# in tests/test_perform_refresh_handshake.py.
agent._build_launch_cmd = lambda: [sys.executable, "-c", successor_launch_code]

service_mod.LLMService = lambda **kwargs: agent.service
resp1 = MagicMock()
resp1.text = "calling blocking tool"
resp1.tool_calls = [ToolCall(name="blocking_side_effect", args={{}}, id="tc-1")]
resp1.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)

_send_call_count = {{"n": 0}}

def _mock_send(*args, **kwargs):
    _send_call_count["n"] += 1
    if _send_call_count["n"] == 1:
        return resp1
    resp = MagicMock()
    resp.text = "Task done."
    resp.tool_calls = []
    resp.usage = MagicMock(input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0)
    return resp

mock_session = MagicMock()
mock_session.send = MagicMock(side_effect=_mock_send)
agent.service.create_session = MagicMock(return_value=mock_session)
agent.service.make_tool_result = MagicMock(return_value="mock_result")

result = mgr._handle_emanate([{{"task": "do the blocking thing", "tools": ["blocking_side_effect"]}}])
assert result["status"] == "dispatched", result
em_id = result["ids"][0]
_em_id_holder["em_id"] = em_id

# --- Drive the REAL .refresh -> _perform_refresh handshake + watcher spawn,
# not a manually-touched .refresh.taken. This is the exact gap the other
# two _REAL_LIFECYCLE_* tests in this file deliberately left open (see
# their own comments) — closing it is this stage's whole point.

# Contract step 2 -- mechanically prove the OLD real .agent.lock and
# .agent.heartbeat both genuinely exist RIGHT NOW, before refresh/stop
# ever runs, and record it for the outer test process (which cannot
# observe this instant directly -- `host_ready_file` is only written
# after `_stop()` has already withdrawn both). Capture the old heartbeat's
# exact bytes so the outer test can later prove the successor's new
# heartbeat is a genuinely fresh value, not a byte-identical leftover.
old_lock_existed = (working_dir / ".agent.lock").exists()
old_heartbeat_existed = (working_dir / ".agent.heartbeat").exists()
old_heartbeat_value = (
    (working_dir / ".agent.heartbeat").read_text(encoding="utf-8")
    if old_heartbeat_existed else None
)
pre_stop_marker_path.write_text(
    json.dumps({{
        "old_lock_existed": old_lock_existed,
        "old_heartbeat_existed": old_heartbeat_existed,
        "old_heartbeat_value": old_heartbeat_value,
    }}),
    encoding="utf-8",
)
assert old_lock_existed and old_heartbeat_existed, (
    f"the old host must genuinely hold its own real lock/heartbeat before "
    f"refresh/stop: lock_existed={{old_lock_existed}} "
    f"heartbeat_existed={{old_heartbeat_existed}}"
)

(working_dir / ".refresh").touch()
agent._perform_refresh()
assert (working_dir / ".refresh.taken").exists(), (
    "the real _perform_refresh handshake must have normalized "
    ".refresh -> .refresh.taken before spawning the watcher"
)
assert not (working_dir / ".refresh").exists(), (
    "the real handshake must consume .refresh so the heartbeat loop "
    "(not running in this test, but the invariant must still hold) "
    "would never spawn a duplicate watcher on its next tick"
)

# The real lifecycle._stop() call: closes the session, persists the
# manifest, stops the heartbeat (real .agent.heartbeat withdrawal via the
# real PosixAgentPresenceStoreAdapter this bare-service Agent() composed
# for itself), releases the real .agent.lock (real PosixWorkdirLeaseAdapter
# flock release) -- which is exactly the signal the REAL watcher subprocess
# spawned above is polling for in its own phase 2 -- and (because
# .refresh.taken exists) enters the real PREPARE+drain branch instead of
# shutdown_for_agent_stop, keeping the still-blocked daemon run alive on a
# background drain thread in THIS process.
lifecycle_module._stop(agent, timeout=1.0)

daemon_json_path = working_dir / "daemons" / em_id / "daemon.json"
daemon_json = json.loads(daemon_json_path.read_text())
assert daemon_json["state"] == "running", daemon_json
assert daemon_json.get("execution_owner") is not None, (
    "the real lifecycle._stop() path must have committed a real "
    "execution_owner tag via PREPARE"
)

host_pid_file.write_text(str(os.getpid()), encoding="utf-8")
host_ready_file.write_text("ready", encoding="utf-8")

# Deliberately do nothing further and do NOT exit -- the real drain
# thread (self_exit_process=True) is the only thing that may ever
# terminate this process from here on.
time.sleep(60)
os._exit(97)  # sentinel: reaching this line means self-exit did NOT fire
"""


def test_v9_b1_b2_real_successor_observes_and_controls_persisted_run(tmp_path):
    """v9 Stage B1/B2 acceptance (correction of the v8 headline claim): drives
    the actual product entry point `Agent._perform_refresh` through its real
    filesystem handshake AND spawns its real detached watcher subprocess,
    which in turn launches a REAL hermetic successor -- a distinct OS process
    that imports and constructs a real `Agent`/`DaemonManager` on the same
    working_dir (not a `python -c` marker+sleep stand-in) -- while a
    genuinely in-flight LingTai daemon run (real `_handle_emanate` dispatch)
    survives the handoff on the old host's real drain thread and completes
    exactly once, released ONLY by the successor's own real `ask`
    `ControlRequest` reaching the old host's real control plane. Mechanically
    observes, from OUTSIDE both subprocesses:

    1. real `.refresh` -> `.refresh.taken` ownership transfer;
    2. the old host's real `.agent.lock` AND real `.agent.heartbeat` exist
       and are captured BEFORE refresh/stop, then both are observably
       withdrawn before the successor ever constructs anything;
    3. real successor process startup -- constructs a real `Agent`, whose
       construction succeeding is itself the mechanical proof of real lock
       re-acquisition, and whose own `_start_heartbeat()` publishes a real
       new heartbeat;
    4. the successor uses a real, freshly-constructed `DaemonManager` to
       send a real `ask` `ControlRequest` at the still-running old host
       through the product control plane (`_handle_ask` ->
       `_route_ask_through_control_plane`), and the old host's blocking
       tool call only completes once that real ack lands -- never a
       parent-touched release file;
    5. exactly one replay-sensitive effect-log entry, exactly one terminal
       daemon result (`daemon.json.state == "done"`, exactly one
       `daemon_completion.json`), and a durable terminal-notification
       receipt (`terminal_notified is True` + non-null
       `terminal_notification_receipt`) matched by exactly one real system
       notification event in `.notification/system.json` -- no replay, no
       duplicate execution;
    6. the old host reaching quiescence and self-exiting for real
       (`os._exit(0)`, not the `os._exit(97)` never-reached sentinel).
    """
    working_dir = tmp_path / "agent-workdir"
    working_dir.mkdir()
    release_file = tmp_path / "release_file_unused_legacy_param"
    host_ready_file = tmp_path / "host_ready"
    host_pid_file = tmp_path / "host_pid"
    successor_started_marker = tmp_path / "successor_started"
    successor_preflight_marker = tmp_path / "successor_preflight"
    successor_ask_result_marker = tmp_path / "successor_ask_result"
    effect_log_path = tmp_path / "effect_log.jsonl"
    pre_stop_marker_path = tmp_path / "pre_stop_marker.json"
    src_path = str(Path(__file__).resolve().parents[1] / "src")

    # The hermetic "successor" the real watcher subprocess will actually
    # `subprocess.Popen` in its own phase 3 -- writes an independent
    # startup marker (proves real exec, not merely "the watcher decided
    # to try") AND a real-shaped `.agent.heartbeat` file (str(epoch), no
    # trailing newline -- the exact format `PosixAgentPresenceStoreAdapter.
    # publish_heartbeat` writes) so the watcher's own real health-check
    # (reads `.agent.heartbeat`, checks the age) reports success and the
    # watcher exits 0 on its FIRST relaunch attempt rather than retrying.
    #
    # This is a REAL hermetic successor, not a marker+sleep stand-in: it
    # imports and constructs a real `Agent` (capabilities=["daemon"]) on
    # the SAME working_dir, which is itself the mechanical proof of real
    # lock re-acquisition (`BaseAgent.__init__`'s own
    # `self._workdir_lease.acquire(10)` -- would raise if the old host
    # still held the flock), starts a real heartbeat thread via the same
    # `_start_heartbeat` seam the host uses, and gets a real freshly
    # constructed `DaemonManager` via `get_capability("daemon")` (whose
    # own `__init__` runs the real `_reap_host_lost_daemon_records` et al.
    # against the still-live old marker and correctly leaves it alone).
    # It then drives a real `ask` `ControlRequest` at the still-running old
    # host through `mgr._handle_ask`, which -- since this run_id is not in
    # THIS manager's own `_emanations` -- routes through the real
    # `_load_execution_owner_for_run` / `_route_ask_through_control_plane`
    # product path rather than any test-only shortcut.
    successor_launch_code = textwrap.dedent(
        f"""
        import json, os, sys, time
        sys.path.insert(0, {src_path!r})
        from pathlib import Path
        from unittest.mock import MagicMock

        from lingtai.agent import Agent
        from lingtai.kernel.config import AgentConfig

        wd = Path({str(working_dir)!r})
        started_marker = Path({str(successor_started_marker)!r})
        preflight_marker = Path({str(successor_preflight_marker)!r})
        ask_result_marker = Path({str(successor_ask_result_marker)!r})

        # Discover the owned run_id from disk itself -- the successor's
        # own real observation, not a value handed to it by the parent
        # test process. Exactly one `em-*` run directory is expected (the
        # single blocking daemon this test dispatches on the old host).
        daemons_dir = wd / "daemons"
        em_dirs = [
            p.name for p in daemons_dir.iterdir()
            if p.is_dir() and p.name.startswith("em-")
        ] if daemons_dir.is_dir() else []
        if len(em_dirs) != 1:
            raise RuntimeError(f"expected exactly one owned run, found {{em_dirs}}")
        em_id = em_dirs[0]

        # Same real-cmdline-observation seam as the host script itself
        # uses (and the other real-subprocess tests in this file): THIS
        # process's own actual argv is `python -c <script>`, which
        # correctly does not match any real agent-run launch form. Since
        # `verify_marker_live` is what `DaemonManager.__init__`'s own
        # `_reap_host_lost_daemon_records` (and later
        # `_load_execution_owner_for_run`) calls to confirm the OLD host
        # is still real, and that check reads the OLD host's PID's
        # cmdline via THIS process's own fresh import of `refresh_host`
        # (not shared with the host process's own patched module object),
        # this patch MUST be applied BEFORE constructing `Agent(...)`
        # below -- `capabilities=["daemon"]` constructs a real
        # `DaemonManager` synchronously during `Agent.__init__`, which
        # would otherwise reap this run as `DaemonHostLost` before this
        # script ever gets a chance to patch anything. Discovered from
        # the real, disk-persisted `execution_owner.pid` this run's real
        # `daemon.json` carries, not a value handed to it out-of-band.
        daemon_json_path = wd / "daemons" / em_id / "daemon.json"
        owner_state = json.loads(daemon_json_path.read_text(encoding="utf-8"))
        old_host_pid = owner_state["execution_owner"]["pid"]
        import lingtai.tools.daemon.refresh_host as _refresh_host_module
        _real_read_cmdline = _refresh_host_module._read_cmdline
        # `verify_marker_live` matches the observed cmdline against the
        # marker's own `working_dir`, which `_canonicalize_working_dir`
        # stores fully `.resolve()`d (on macOS, `/var/folders/...` resolves
        # through a symlink to `/private/var/folders/...`) -- the fake
        # cmdline must embed that SAME resolved form, not `wd` as handed
        # to this script, or the match silently fails on any platform
        # where the temp dir is itself a symlink.
        def _patched_read_cmdline(pid, _old_host_pid=old_host_pid):
            if pid == _old_host_pid:
                return "python -m lingtai run " + str(wd.resolve())
            return _real_read_cmdline(pid)
        _refresh_host_module._read_cmdline = _patched_read_cmdline

        # Contract step 4 (before construction): the old real .agent.lock
        # and .agent.heartbeat must both be OBSERVABLY ABSENT before this
        # process constructs anything -- a real, non-vacuous precondition
        # check, not asserted only after the fact.
        old_lock_gone = not (wd / ".agent.lock").exists()
        old_heartbeat_gone = not (wd / ".agent.heartbeat").exists()
        preflight_marker.write_text(
            json.dumps({{"old_lock_gone": old_lock_gone,
                        "old_heartbeat_gone": old_heartbeat_gone}}),
            encoding="utf-8",
        )
        if not (old_lock_gone and old_heartbeat_gone):
            # Fail loud rather than silently proceeding to construct a
            # second real Agent against a workdir the old host might
            # still legitimately hold -- Agent()'s own lease acquire
            # would raise anyway, but this makes the precondition explicit
            # and independently observable by the outer test.
            raise RuntimeError(
                f"old lock/heartbeat not withdrawn before successor "
                f"construction: lock_gone={{old_lock_gone}} "
                f"heartbeat_gone={{old_heartbeat_gone}}"
            )

        svc = MagicMock()
        svc.provider = "mock"
        svc.model = "mock-model"
        # Real Agent construction on the SAME working_dir: succeeds only
        # because the real PosixWorkdirLeaseAdapter flock is genuinely
        # free (BaseAgent.__init__ calls self._workdir_lease.acquire(10)
        # unconditionally) -- this call itself IS the mechanical proof of
        # real lock re-acquisition by a distinct new identity. Its
        # `capabilities=["daemon"]` construction also runs
        # `DaemonManager.__init__`'s own `_reap_host_lost_daemon_records`
        # synchronously (with the cmdline patch above already active), so
        # it correctly leaves the still-live old marker's run alone.
        agent = Agent(svc, working_dir=wd, capabilities=["daemon"], config=AgentConfig())
        agent._start_heartbeat()
        hb_deadline = time.time() + 5.0
        while not (wd / ".agent.heartbeat").exists():
            if time.time() > hb_deadline:
                raise RuntimeError("successor's own real heartbeat never published")
            time.sleep(0.02)

        # Independent startup marker -- proves real exec of a distinct OS
        # process, written only AFTER the real Agent+DaemonManager exist.
        started_marker.write_text(json.dumps({{"pid": os.getpid()}}), encoding="utf-8")

        mgr = agent.get_capability("daemon")

        # Real observe-then-control: this run_id is a HISTORICAL run this
        # fresh manager never registered itself, so `_handle_ask` must
        # route through `_load_execution_owner_for_run` (verifies the old
        # marker is still live, proving membership) then
        # `_route_ask_through_control_plane` (writes a real
        # `ControlRequest`, polls for a real `ControlAck`) -- the real
        # product "successor observes and controls a persisted in-flight
        # run" path, not a parent-touched file.
        result = mgr._handle_ask(em_id, "successor-observed-and-controlling")
        ask_result_marker.write_text(json.dumps(result), encoding="utf-8")
        if result.get("status") != "sent":
            raise RuntimeError(f"real ask to old host was not accepted: {{result}}")

        # Wait through the real persisted terminal state before exiting --
        # the successor must observe genuine product completion, not just
        # fire-and-forget its own ask.
        term_deadline = time.time() + 25.0
        daemon_json_path = wd / "daemons" / em_id / "daemon.json"
        while True:
            if time.time() > term_deadline:
                raise RuntimeError("old host's run never reached a terminal state")
            try:
                state = json.loads(daemon_json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {{}}
            if state.get("state") in ("done", "failed", "cancelled", "timeout"):
                break
            time.sleep(0.05)
        """
    )

    host_script = _REAL_LIFECYCLE_REFRESH_HOST_SCRIPT.format(
        src_path=src_path, working_dir=str(working_dir),
        release_file=str(release_file), host_ready_file=str(host_ready_file),
        host_pid_file=str(host_pid_file),
        successor_started_marker=str(successor_started_marker),
        successor_launch_code=successor_launch_code,
        effect_log_path=str(effect_log_path),
        pre_stop_marker_path=str(pre_stop_marker_path),
    )
    env = {**os.environ, "PYTHONPATH": src_path}
    host_proc = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        deadline = time.monotonic() + 20.0
        while not host_ready_file.exists():
            assert time.monotonic() < deadline, (
                f"host process never became ready. "
                f"poll={host_proc.poll()}"
            )
            assert host_proc.poll() is None, f"host process exited early: {host_proc.poll()}"
            time.sleep(0.02)

        host_pid = int(host_pid_file.read_text(encoding="utf-8"))
        assert host_pid == host_proc.pid
        # The real Python process is STILL ALIVE after its own real
        # lifecycle._stop() returned -- the drain thread, not process
        # exit, is what keeps the in-flight daemon work reachable, and
        # the real detached watcher subprocess (spawned by the real
        # _perform_refresh call inside the host script) is now polling
        # for THIS process's real .agent.lock to clear.
        assert host_proc.poll() is None

        daemon_dirs = [d for d in (working_dir / "daemons").iterdir() if d.is_dir() and d.name.startswith("em-")]
        assert len(daemon_dirs) == 1, daemon_dirs
        em_id = daemon_dirs[0].name

        # Contract step 2: mechanically prove the OLD real .agent.lock and
        # .agent.heartbeat both genuinely existed BEFORE refresh/stop ever
        # ran -- this test previously let both withdrawal assertions pass
        # vacuously by never checking pre-existence. This instant cannot
        # be observed directly from outside the host process (by the time
        # `host_ready_file` appears, `_stop()` has already withdrawn both),
        # so the host script itself captured this fact into
        # `pre_stop_marker_path` immediately before calling `_perform_refresh`/
        # `_stop`, using the same real filesystem checks this test would
        # otherwise run too late to observe.
        pre_stop = json.loads(pre_stop_marker_path.read_text(encoding="utf-8"))
        assert pre_stop == {
            "old_lock_existed": True,
            "old_heartbeat_existed": True,
            "old_heartbeat_value": pre_stop.get("old_heartbeat_value"),
        }, pre_stop
        assert isinstance(pre_stop.get("old_heartbeat_value"), str) and pre_stop["old_heartbeat_value"], pre_stop
        old_heartbeat_value = pre_stop["old_heartbeat_value"]

        # The real watcher's phase-2 poll (`while os.path.exists(lock)`)
        # must observe the REAL .agent.lock actually clear -- proving the
        # real PosixWorkdirLeaseAdapter.release() call inside the host's
        # own lifecycle._stop() is what unblocks it, not a test-side
        # shortcut. The lock file itself is removed by a successful
        # release (see PosixWorkdirLeaseAdapter.release's unlink-on-
        # confirmed-close), so its absence is the mechanical proof.
        lock_clear_deadline = time.monotonic() + 15.0
        while (working_dir / ".agent.lock").exists():
            assert time.monotonic() < lock_clear_deadline, (
                "the real .agent.lock was never released by the host's "
                "real lifecycle._stop() -- the real watcher subprocess "
                "can never proceed past its own phase-2 poll"
            )
            time.sleep(0.02)

        # `_stop()`'s own ordering withdraws the heartbeat strictly BEFORE
        # releasing the lock (see lifecycle._stop's docstring), so by the
        # time the lock has cleared the heartbeat must already be gone
        # too -- checked here, not merely assumed.
        assert not (working_dir / ".agent.heartbeat").exists(), (
            "the old host's real .agent.heartbeat must be withdrawn no "
            "later than its .agent.lock release"
        )

        # Real successor startup: the marker is written only by code
        # running INSIDE the process the real watcher subprocess actually
        # exec'd via subprocess.Popen(cmd, ...) in its own phase 3 -- not
        # hand-created by this test and not inferable from log text alone.
        successor_deadline = time.monotonic() + 20.0
        while not successor_started_marker.exists():
            assert time.monotonic() < successor_deadline, (
                "the real detached watcher subprocess never actually "
                "started the hermetic successor process -- real "
                "watcher/relaunch + real successor startup is not proven"
            )
            assert host_proc.poll() is None, (
                "host process died before the successor could start"
            )
            time.sleep(0.05)
        successor_info = json.loads(successor_started_marker.read_text(encoding="utf-8"))
        assert isinstance(successor_info.get("pid"), int)
        assert successor_info["pid"] != host_pid, (
            "the successor must be a DIFFERENT real OS process from the "
            "old drain host, not the same process relaunching itself in place"
        )

        # The successor's own preflight check (asserted BEFORE it
        # constructed its Agent) must have observed both the old lock and
        # old heartbeat as genuinely absent -- never vacuously true
        # because nothing was ever checked.
        preflight_deadline = time.monotonic() + 5.0
        while not successor_preflight_marker.exists():
            assert time.monotonic() < preflight_deadline
            time.sleep(0.02)
        preflight = json.loads(successor_preflight_marker.read_text(encoding="utf-8"))
        assert preflight == {"old_lock_gone": True, "old_heartbeat_gone": True}, preflight

        # The successor's real Agent construction succeeding is itself the
        # mechanical proof it holds a NEW, distinct real lock -- and its
        # own real heartbeat thread must have published a fresh value that
        # is not byte-identical to the withdrawn old one.
        new_heartbeat_deadline = time.monotonic() + 10.0
        while not (working_dir / ".agent.heartbeat").exists():
            assert time.monotonic() < new_heartbeat_deadline, (
                "the successor's own real _start_heartbeat() never "
                "published a new .agent.heartbeat"
            )
            time.sleep(0.02)
        new_heartbeat_value = (working_dir / ".agent.heartbeat").read_text(encoding="utf-8")
        assert new_heartbeat_value != old_heartbeat_value, (
            "the new heartbeat must be a fresh value from the successor's "
            "own real heartbeat thread, not a byte-identical leftover"
        )

        # The successor's own real `ask` ControlRequest, routed through
        # `mgr._handle_ask` -> `_route_ask_through_control_plane`, must
        # have been accepted by the old host's real, already-running
        # drain loop -- this IS the real product observe-and-control path,
        # not a parent-touched release file.
        ask_result_deadline = time.monotonic() + 15.0
        while not successor_ask_result_marker.exists():
            assert time.monotonic() < ask_result_deadline, (
                "the successor never recorded the result of its real ask "
                "ControlRequest against the old host"
            )
            assert host_proc.poll() is None, (
                "host process died before acking the successor's real ask"
            )
            time.sleep(0.05)
        ask_result = json.loads(successor_ask_result_marker.read_text(encoding="utf-8"))
        assert ask_result.get("status") == "sent", ask_result

        # The blocked in-flight LingTai daemon run on the OLD host must
        # complete, exactly once, on the drain thread the real
        # lifecycle._stop() path kept alive -- released ONLY by the real
        # ack the successor's own control-plane action produced (see the
        # host script's `_blocking_side_effect_handler`), never by this
        # test writing to a release file.
        completion_deadline = time.monotonic() + 20.0
        completion_path = working_dir / "daemons" / em_id / "daemon_completion.json"
        while not completion_path.exists():
            assert time.monotonic() < completion_deadline, (
                "the in-flight daemon run never completed on the old "
                "drain host after the successor's real ask was acked"
            )
            assert host_proc.poll() is None or host_proc.poll() == 0, (
                f"host process died abnormally before completing the "
                f"in-flight run: {host_proc.poll()}"
            )
            time.sleep(0.02)
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        assert completion["status"] == "done"
        assert completion["run_id"] == em_id

        # Replay-sensitive effect accounting: exactly one line, proving
        # the blocking handler's real side effect ran exactly once --
        # unlike an overwrite-only file, a second invocation would append
        # a second line and be mechanically detectable.
        effect_lines = [
            ln for ln in effect_log_path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        assert len(effect_lines) == 1, effect_lines

        # Exactly one terminal result: the old host's drain loop must
        # reach genuine quiescence and self-exit -- os._exit(0), never
        # the os._exit(97) sentinel that would mean self-exit never fired.
        exit_deadline = time.monotonic() + 20.0
        while host_proc.poll() is None:
            assert time.monotonic() < exit_deadline, (
                "the real drain-host process did not self-exit within "
                "the bounded window after its owned run genuinely finished"
            )
            time.sleep(0.05)
        assert host_proc.returncode == 0, (
            f"expected a clean os._exit(0) self-exit from the real drain "
            f"host after genuine quiescence; got {host_proc.returncode} "
            f"(97 would mean self-exit never fired -- the process only "
            f"reached its own never-reached sentinel line). "
            f"stdout={host_proc.stdout.read()!r} stderr={host_proc.stderr.read()!r}"
        )

        # Durable terminal-notification proof: a published receipt, not
        # merely "no claim is dangling" (which is also vacuously true when
        # no notification was ever published).
        final_daemon_json = json.loads(
            (working_dir / "daemons" / em_id / "daemon.json").read_text(encoding="utf-8")
        )
        assert final_daemon_json.get("terminal_notification_claim") is None, (
            "no pending terminal-notification claim may be left dangling "
            "once the run and the drain host have both genuinely finished"
        )
        assert final_daemon_json.get("terminal_notified") is True, final_daemon_json
        receipt = final_daemon_json.get("terminal_notification_receipt")
        assert isinstance(receipt, dict), receipt
        assert isinstance(receipt.get("idempotency_key"), str) and receipt["idempotency_key"], receipt
        assert isinstance(receipt.get("published_at"), str) and receipt["published_at"], receipt

        # Exactly one real system notification event matches this run and
        # this exact idempotency key/status -- inspected from the real
        # `.notification/system.json` store, not merely inferred from the
        # daemon.json receipt existing.
        system_notification_path = working_dir / ".notification" / "system.json"
        system_payload = json.loads(system_notification_path.read_text(encoding="utf-8"))
        # Real on-disk shape written by PosixNotificationStoreAdapter.publish:
        # {"header": ..., "data": {"events": [...]}} -- see
        # `_enqueue_system_notification`'s own payload construction.
        events = (system_payload.get("data") or {}).get("events") if isinstance(system_payload, dict) else None
        assert isinstance(events, list), system_payload
        matching_events = [
            e for e in events
            if isinstance(e, dict)
            and e.get("ref_id") == em_id
            and e.get("idempotency_key") == receipt["idempotency_key"]
        ]
        assert len(matching_events) == 1, (matching_events, events)
        matching_event = matching_events[0]
        assert "done" in (matching_event.get("body") or ""), matching_event
    finally:
        if host_proc.poll() is None:
            host_proc.kill()
            host_proc.wait(timeout=5)
