"""Regression tests for the hard 10,000-char inline Nudge payload cap.

Contract: inline model-visible Nudge payload is capped at INLINE_MAX_CHARS;
above it, the full body is externalized to a content-addressed file under
``<working_dir>/tmp/nudge-findings/`` and the persisted entry carries only a
summary, char/byte counts, path and SHA-256. Externalization failure is
fail-loud (``NudgeExternalizationError``, bounded message) and must never
partially persist or fall back to inline. See ``lingtai.kernel.nudge``.
"""
from __future__ import annotations

import hashlib
import json
import stat
import threading
import time
from pathlib import Path

import pytest

from lingtai.kernel.nudge import (
    ENTRY_CHANNEL_RELEASE_VERSION,
    INLINE_MAX_CHARS,
    NudgeExternalizationError,
    effective_policy,
    record_dismissal,
    upsert,
)
from lingtai.kernel.notifications import dismiss_channel
from lingtai.kernel.workdir import workdir_layout
from tests._notification_store_helpers import notification_store_for, snapshot_notifications


class _Agent:
    def __init__(self, workdir):
        self._working_dir = workdir
        self._notification_store = notification_store_for(workdir)
        self._notification_fp = ()
        self.logs = []

    def _log(self, event, **fields):
        self.logs.append((event, fields))


def _entry(workdir, kind=None):
    payload = snapshot_notifications(workdir).get("nudge", {})
    entries = payload.get("data", {}).get("nudges", [])
    if kind is None:
        assert len(entries) == 1
        return entries[0]
    return next(e for e in entries if e["kind"] == kind)


def _entry_chars(entry: dict) -> int:
    return len(json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str))


def _findings_dir(workdir) -> Path:
    return workdir_layout(workdir).nudge_findings_dir


def _pre_cap_chars(kind: str, title: str, detail: str, source: str) -> int:
    """Chars of the assembled entry before cap enforcement (what upsert compares to INLINE_MAX_CHARS)."""
    policy = effective_policy()
    entry = {
        "kind": kind,
        "title": title,
        "detail": f"{detail}\n\n{policy.message()}".strip(),
        "source": source,
        "policy": policy.payload(),
        "policy_message": policy.message(),
    }
    return len(json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str))


def _detail_len_for_pre_cap_chars(kind: str, target_chars: int) -> int:
    """Binary-search a detail length landing the pre-cap entry at exactly target_chars."""
    lo, hi = 0, target_chars
    best_len = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        chars = _pre_cap_chars(kind, "t", "x" * mid, "s")
        if chars <= target_chars:
            best_len = mid
            lo = mid + 1
        else:
            hi = mid - 1
    assert _pre_cap_chars(kind, "t", "x" * best_len, "s") == target_chars
    return best_len


def test_entry_at_10000_chars_inline_boundary_vs_10001_externalized(tmp_path):
    agent = _Agent(tmp_path)

    at_cap_len = _detail_len_for_pre_cap_chars("boundary", INLINE_MAX_CHARS)
    upsert(agent, "boundary", {"title": "t", "detail": "x" * at_cap_len, "source": "s"})
    entry = _entry(tmp_path, "boundary")
    assert _entry_chars(entry) == INLINE_MAX_CHARS
    assert "externalized" not in entry
    assert entry["detail"].startswith("x")
    assert not _findings_dir(tmp_path).exists()

    over_cap_len = _detail_len_for_pre_cap_chars("boundary2", INLINE_MAX_CHARS) + 1
    assert _pre_cap_chars("boundary2", "t", "x" * over_cap_len, "s") == INLINE_MAX_CHARS + 1
    upsert(agent, "boundary2", {"title": "t", "detail": "x" * over_cap_len, "source": "s"})
    entry2 = _entry(tmp_path, "boundary2")
    assert "externalized" in entry2
    assert entry2["externalized"]["original_char_count"] == INLINE_MAX_CHARS + 1
    assert _entry_chars(entry2) <= INLINE_MAX_CHARS


@pytest.mark.parametrize(
    "label, detail_char, byte_count_exceeds_char_count",
    [("ascii", "y", False), ("unicode", "中", True)],
)
def test_externalization_preserves_full_content_and_metadata(
    tmp_path, label, detail_char, byte_count_exceeds_char_count
):
    agent = _Agent(tmp_path)
    big_detail = detail_char * 20_000
    upsert(agent, "oversized", {"title": "Oversized finding", "detail": big_detail, "source": "producer"})

    entry = _entry(tmp_path)
    assert _entry_chars(entry) <= INLINE_MAX_CHARS
    assert "externalized" in entry
    ext = entry["externalized"]
    assert ext["path"] is not None
    assert ext["original_char_count"] > INLINE_MAX_CHARS
    assert "error" not in ext

    persisted_text = json.dumps(entry, ensure_ascii=False, default=str)
    assert big_detail not in persisted_text

    sidecar_path = Path(ext["path"])
    assert sidecar_path.is_absolute()
    assert sidecar_path.is_file()
    relative = sidecar_path.relative_to(Path(tmp_path))
    assert relative.parts[:2] == ("tmp", "nudge-findings")
    assert ".notification" not in relative.parts

    full_text = sidecar_path.read_text(encoding="utf-8")
    full = json.loads(full_text)
    assert full["detail"].startswith(big_detail)
    assert full["title"] == "Oversized finding"
    assert "nudge_channel" not in full

    raw_bytes = full_text.encode("utf-8")
    assert ext["original_char_count"] == len(full_text)
    assert ext["original_byte_count"] == len(raw_bytes)
    assert ext["sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert (ext["original_byte_count"] > ext["original_char_count"]) == byte_count_exceeds_char_count


def test_externalized_builtin_wire_entry_retains_fixed_channel(tmp_path):
    agent = _Agent(tmp_path)
    big_detail = "k" * 11_000

    upsert(
        agent,
        "kernel_version",
        {
            "title": "Oversized kernel update",
            "detail": big_detail,
            "source": "release-manifest",
        },
    )

    entry = _entry(tmp_path, "kernel_version")
    assert _entry_chars(entry) <= INLINE_MAX_CHARS
    assert entry["nudge_channel"] == ENTRY_CHANNEL_RELEASE_VERSION
    assert "externalized" in entry
    assert big_detail not in json.dumps(entry, ensure_ascii=False, default=str)

    sidecar = json.loads(Path(entry["externalized"]["path"]).read_text(encoding="utf-8"))
    assert sidecar["kind"] == "kernel_version"
    assert sidecar["nudge_channel"] == ENTRY_CHANNEL_RELEASE_VERSION


def test_content_addressed_reuse_and_distinct_files(tmp_path):
    agent = _Agent(tmp_path)
    findings_dir = _findings_dir(tmp_path)

    body = {"title": "Repeated finding", "detail": "w" * 20_000, "source": "producer"}
    upsert(agent, "repeated", dict(body))
    entry1 = _entry(tmp_path, "repeated")
    path1 = entry1["externalized"]["path"]
    sha1 = entry1["externalized"]["sha256"]
    files_after_first = sorted(findings_dir.iterdir())
    assert len(files_after_first) == 1

    upsert(agent, "repeated", dict(body))
    entry2 = _entry(tmp_path, "repeated")
    assert entry2["externalized"]["path"] == path1
    assert entry2["externalized"]["sha256"] == sha1
    assert sorted(findings_dir.iterdir()) == files_after_first, \
        "same finding must not create a new sidecar file"

    upsert(agent, "distinct", {"title": "B", "detail": "b" * 20_000, "source": "s"})
    entry3 = _entry(tmp_path, "distinct")
    assert entry3["externalized"]["path"] != path1
    assert len(list(findings_dir.iterdir())) == 2


def test_repeated_externalization_logs_once_per_digest(tmp_path):
    agent = _Agent(tmp_path)
    body = {"title": "Repeated finding", "detail": "w" * 20_000, "source": "producer"}

    upsert(agent, "repeated", dict(body))
    upsert(agent, "repeated", dict(body))
    events = [event for event, _fields in agent.logs if event == "nudge_finding_externalized"]
    assert len(events) == 1

    upsert(agent, "repeated", {**body, "detail": "x" * 20_000})
    events = [event for event, _fields in agent.logs if event == "nudge_finding_externalized"]
    assert len(events) == 2


def test_concurrent_same_digest_externalization_logs_once(tmp_path):
    agents = [_Agent(tmp_path), _Agent(tmp_path)]
    body = {"title": "Concurrent finding", "detail": "c" * 20_000, "source": "producer"}
    errors = []

    def _run(agent):
        try:
            upsert(agent, "concurrent", dict(body))
        except BaseException as exc:  # pragma: no cover - assertion reports any race
            errors.append(exc)

    threads = [threading.Thread(target=_run, args=(agent,)) for agent in agents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    events = [
        event
        for agent in agents
        for event, _fields in agent.logs
        if event == "nudge_finding_externalized"
    ]
    assert len(events) == 1
    assert len(list(_findings_dir(tmp_path).glob("concurrent-*.json"))) == 1


def test_cross_process_same_digest_has_one_sidecar_winner_and_event(tmp_path):
    """The content-addressed target is once-only across independent PIDs."""
    import os
    import subprocess
    import sys

    repo_root = Path(__file__).parents[1]
    workdir = tmp_path / "cross-process"
    workdir.mkdir()
    start = workdir / "start"
    child_result = r'''
import json
import sys
import time
from pathlib import Path

from lingtai.kernel.nudge import upsert
from tests._notification_store_helpers import notification_store_for

workdir = Path(sys.argv[1])
start = Path(sys.argv[2])
result = Path(sys.argv[3])
class Agent:
    def __init__(self):
        self._working_dir = workdir
        self._notification_store = notification_store_for(workdir)
        self._notification_fp = ()
        self.logs = []
    def _log(self, event, **fields):
        self.logs.append((event, fields))

agent = Agent()
print("READY", flush=True)
deadline = time.monotonic() + 5
while not start.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
if not start.exists():
    raise SystemExit("barrier was not released")
upsert(agent, "cross-process", {"title": "same", "detail": "c" * 20_000, "source": "producer"})
result.write_text(json.dumps({"externalized_events": sum(
    event == "nudge_finding_externalized" for event, _fields in agent.logs
)}))
print("DONE", flush=True)
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "src"), str(repo_root), env.get("PYTHONPATH", "")]
    )
    processes = []
    try:
        for index in range(2):
            result = workdir / f"child-{index}.json"
            process = subprocess.Popen(
                [sys.executable, "-c", child_result, str(workdir), str(start), str(result)],
                cwd=repo_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append((process, result))
        for process, _result in processes:
            assert process.stdout.readline().strip() == "READY"
        start.touch()
        for process, result in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, (stdout, stderr)
            assert result.is_file()
    finally:
        for process, _result in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    event_counts = [
        json.loads(result.read_text())["externalized_events"]
        for _process, result in processes
    ]
    assert sum(event_counts) == 1
    findings_dir = _findings_dir(workdir)
    assert len(list(findings_dir.glob("cross-process-*.json"))) == 1
    assert list(findings_dir.glob(".*.tmp")) == []


def test_sidecar_and_dir_permissions_owner_only_including_preexisting_loose_dir(tmp_path):
    findings_dir = _findings_dir(tmp_path)

    # Pre-existing looser-permission dir must be re-tightened, not just newly-created dirs.
    findings_dir.mkdir(parents=True)
    findings_dir.chmod(0o755)
    assert stat.S_IMODE(findings_dir.stat().st_mode) == 0o755

    agent = _Agent(tmp_path)
    upsert(agent, "loose-dir", {"title": "t", "detail": "z" * 20_000, "source": "s"})

    assert stat.S_IMODE(findings_dir.stat().st_mode) == 0o700
    entry = _entry(tmp_path)
    sidecar_path = Path(entry["externalized"]["path"])
    assert stat.S_IMODE(sidecar_path.stat().st_mode) == 0o600


def test_externalization_failure_raises_and_leaves_notification_state_unchanged(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    upsert(agent, "prior-finding", {"title": "Prior", "detail": "unrelated", "source": "s"})

    from lingtai.kernel import nudge as nudge_module

    kind = "fails-to-write"
    big_detail = "q" * 30_000
    policy_for_probe = effective_policy()
    probe_entry = {
        "kind": kind,
        "title": "t",
        "detail": f"{big_detail}\n\n{policy_for_probe.message()}".strip(),
        "source": "s",
    }
    fingerprint = nudge_module._finding_fingerprint(kind, probe_entry)
    state_path = tmp_path / ".notification" / ".nudge_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Expired dismissal (until in the past) lets upsert pass the mute guard and
    # reach _clear_dismissal — proving that step now runs only AFTER a successful
    # externalization, not before, so a raised error leaves it untouched.
    seeded_state = {
        "dismissed": {fingerprint: {"kind": kind, "dismissed_at": 1.0, "until": 2.0}}
    }
    state_path.write_text(json.dumps(seeded_state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    before_notification_raw = notification_store_for(tmp_path).snapshot(lambda _c: True)
    before_state_raw = state_path.read_bytes()
    findings_dir = _findings_dir(tmp_path)

    # Inject failure via the private write seam: production's own chmod(0o700)
    # repair would re-loosen a chmodded-read-only dir before the write is even
    # attempted, so a permissions-based injection would be unreliable here.
    def _raise_injected(target, text):
        raise OSError("injected")

    monkeypatch.setattr(nudge_module, "_write_sidecar_atomic", _raise_injected)

    with pytest.raises(NudgeExternalizationError) as excinfo:
        upsert(agent, kind, {"title": "t", "detail": big_detail, "source": "s"})

    message = str(excinfo.value)
    assert len(message) < 300
    assert big_detail not in message
    assert "t" != message
    assert kind not in message

    after_notification_raw = notification_store_for(tmp_path).snapshot(lambda _c: True)
    assert after_notification_raw == before_notification_raw
    after_state_raw = state_path.read_bytes()
    assert after_state_raw == before_state_raw

    if findings_dir.exists():
        assert list(findings_dir.glob(f"{kind}-*.json")) == []


def test_ordinary_small_nudge_behavior_is_unchanged(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "small", {"title": "Small finding", "detail": "short detail", "source": "producer"})
    entry = _entry(tmp_path)
    assert entry["title"] == "Small finding"
    assert "short detail" in entry["detail"]
    assert "externalized" not in entry
    assert entry["kind"] == "small"
    assert not _findings_dir(tmp_path).exists()
    assert "nudge_channel" not in entry
    # Internal bookkeeping field must never leak into the persisted payload.
    assert "_dismiss_fingerprint" not in entry

    # Current short built-in kinds are unaffected by the new validation.
    for kind in ("kernel_version", "source_drift", "init_config_shape"):
        upsert(agent, kind, {"title": "t", "detail": "short detail", "source": "s"})
    entries = snapshot_notifications(tmp_path)["nudge"]["data"]["nudges"]
    assert {e["kind"] for e in entries} >= {"small", "kernel_version", "source_drift", "init_config_shape"}
    channel_by_kind = {e["kind"]: e.get("nudge_channel") for e in entries}
    assert channel_by_kind["small"] is None
    assert channel_by_kind["kernel_version"] == "release_version"
    assert channel_by_kind["source_drift"] == "source_integrity"
    assert channel_by_kind["init_config_shape"] == "configuration_staleness"


def test_dismissal_round_trip_for_capped_finding(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    from types import SimpleNamespace
    from lingtai.kernel import nudge as nudge_module

    # Use a deterministic clock so the tiny repeat interval cannot make this
    # persistence round-trip flaky on a busy CI host.
    now = [100.0]
    monkeypatch.setattr(nudge_module, "time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setenv("LINGTAI_NUDGE_REPEAT_INTERVAL", "0.001s")
    body = {"title": "Capped finding", "detail": "r" * 30_000, "source": "s"}

    upsert(agent, "capped-dismiss", dict(body))
    entry = _entry(tmp_path)
    assert "externalized" in entry

    # Prove record_dismissal tolerates a capped (compact) persisted entry shape.
    record_dismissal(agent)

    result = dismiss_channel(agent, "nudge", invoked_by="notification", force=True)
    assert result["status"] == "ok"
    assert snapshot_notifications(tmp_path).get("nudge", {}) in ({}, None) or \
        snapshot_notifications(tmp_path)["nudge"].get("data", {}).get("nudges") in (None, [])

    upsert(agent, "capped-dismiss", dict(body))
    entries = snapshot_notifications(tmp_path).get("nudge", {}).get("data", {}).get("nudges", [])
    assert entries == []

    now[0] += 0.01
    upsert(agent, "capped-dismiss", dict(body))
    entries = snapshot_notifications(tmp_path).get("nudge", {}).get("data", {}).get("nudges", [])
    assert len(entries) == 1


@pytest.mark.parametrize(
    "label, kind, forbidden_substrings",
    [
        ("oversized", "k" * 5000, ["k" * 5000]),
        (
            "escape-heavy",
            "../../../etc/passwd\x00\n\r;rm -rf /",
            ["../../../etc/passwd\x00\n\r;rm -rf /", "/etc/passwd", "rm -rf"],
        ),
        ("short-but-malformed", "bad kind!", ["bad kind!"]),
    ],
)
def test_invalid_producer_kind_families_are_rejected(tmp_path, label, kind, forbidden_substrings):
    # Kind validation happens before size checks and any persistence, so even a
    # small body with a malformed kind gets the same fail-loud/state-untouched behavior.
    agent = _Agent(tmp_path)
    upsert(agent, "prior", {"title": "Prior", "detail": "unrelated", "source": "s"})
    before = notification_store_for(tmp_path).snapshot(lambda _c: True)

    with pytest.raises(NudgeExternalizationError) as excinfo:
        upsert(agent, kind, {"title": "t", "detail": "d" * 20_000, "source": "s"})

    message = str(excinfo.value)
    assert len(message) < 300
    for forbidden in forbidden_substrings:
        assert forbidden not in message

    after = notification_store_for(tmp_path).snapshot(lambda _c: True)
    assert after == before
    findings_dir = _findings_dir(tmp_path)
    assert not findings_dir.exists() or list(findings_dir.iterdir()) == []
