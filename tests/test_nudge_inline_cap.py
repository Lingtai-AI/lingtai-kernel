"""Regression tests for the hard 10,000-char inline Nudge payload cap.

Product contract (2026-07-24 forward fix after v0.18.2, repaired 2026-07-24):

1. Nudge's inline model-visible payload has a hard maximum of 10,000 chars.
2. At or below 10,000 chars, existing inline behavior is unchanged.
3. Above 10,000 chars, the full original payload is externalized to an
   agent-readable file under ``<working_dir>/tmp/nudge-findings/`` (the
   ordinary agent temp namespace, consistent with ``tmp/tool-results/``);
   the persisted ``.notification/nudge.json`` entry carries only a compact
   summary plus the original character count, an absolute usable file path,
   and a SHA-256.
4. Failure to durably externalize never falls back to persisting the
   oversized body inline. It fails LOUD: ``upsert`` raises
   ``NudgeExternalizationError`` (a bounded static message, never the
   producer body or an escape-heavy kind) and does not mutate
   ``.notification/nudge.json`` at all, leaving prior state untouched for a
   later heartbeat retry.
5. A pathological (oversized or escape-heavy) ``kind`` is rejected before
   any file naming or persistence, with the same fail-loud, bounded-message,
   state-untouched behavior.
6. The sidecar directory is always owner-only (0700) after ``upsert``
   returns, even if it pre-existed with looser permissions.
"""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from lingtai.kernel.nudge import (
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


def _entry(workdir):
    payload = snapshot_notifications(workdir).get("nudge", {})
    entries = payload.get("data", {}).get("nudges", [])
    assert len(entries) == 1
    return entries[0]


def _entry_chars(entry: dict) -> int:
    return len(json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str))


def _pre_cap_chars(kind: str, title: str, detail: str, source: str) -> int:
    """Chars of the fully-assembled entry BEFORE cap enforcement — matches
    the exact quantity `upsert`/`_cap_inline_payload` compare against
    INLINE_MAX_CHARS (kind + policy fields added, cap not yet applied)."""
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


def test_entry_at_exactly_10000_chars_is_not_externalized(tmp_path):
    agent = _Agent(tmp_path)
    # Binary-search the detail length so the fully assembled PRE-CAP entry
    # (post-policy-fields, before cap enforcement) lands at exactly
    # INLINE_MAX_CHARS chars — the exact quantity the cap compares against.
    lo, hi = 0, INLINE_MAX_CHARS
    best_len = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        chars = _pre_cap_chars("boundary", "t", "x" * mid, "s")
        if chars <= INLINE_MAX_CHARS:
            best_len = mid
            lo = mid + 1
        else:
            hi = mid - 1

    assert _pre_cap_chars("boundary", "t", "x" * best_len, "s") == INLINE_MAX_CHARS

    upsert(agent, "boundary", {"title": "t", "detail": "x" * best_len, "source": "s"})
    entry = _entry(tmp_path)
    assert _entry_chars(entry) == INLINE_MAX_CHARS
    assert "externalized" not in entry
    assert entry["detail"].startswith("x")
    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    assert not findings_dir.exists()


def test_entry_at_10001_chars_is_externalized(tmp_path):
    agent = _Agent(tmp_path)
    lo, hi = 0, INLINE_MAX_CHARS
    best_len = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        chars = _pre_cap_chars("boundary2", "t", "x" * mid, "s")
        if chars <= INLINE_MAX_CHARS:
            best_len = mid
            lo = mid + 1
        else:
            hi = mid - 1
    over_len = best_len + 1
    assert _pre_cap_chars("boundary2", "t", "x" * over_len, "s") == INLINE_MAX_CHARS + 1

    upsert(agent, "boundary2", {"title": "t", "detail": "x" * over_len, "source": "s"})
    entry = _entry(tmp_path)
    assert "externalized" in entry
    assert entry["externalized"]["original_char_count"] == INLINE_MAX_CHARS + 1
    assert _entry_chars(entry) <= INLINE_MAX_CHARS


def test_entry_over_10000_chars_is_externalized_with_full_content_preserved(tmp_path):
    agent = _Agent(tmp_path)
    big_detail = "y" * 50_000
    upsert(agent, "oversized", {"title": "Oversized finding", "detail": big_detail, "source": "producer"})

    entry = _entry(tmp_path)
    assert _entry_chars(entry) <= INLINE_MAX_CHARS
    assert "externalized" in entry
    ext = entry["externalized"]
    assert ext["path"] is not None
    assert ext["original_char_count"] > INLINE_MAX_CHARS
    assert "error" not in ext

    # The persisted compact entry must not leak the oversized body anywhere.
    persisted_text = json.dumps(entry, ensure_ascii=False, default=str)
    assert big_detail not in persisted_text

    # The sidecar file is directly readable and contains the FULL original,
    # including the pre-cap detail text — nothing lost, not truncated.
    sidecar_path = ext["path"]
    full_text = open(sidecar_path, encoding="utf-8").read()
    full = json.loads(full_text)
    assert full["detail"].startswith(big_detail)
    assert full["title"] == "Oversized finding"

    # Exact character count and SHA-256 of exact persisted UTF-8 bytes.
    assert ext["original_char_count"] == len(full_text)
    raw_bytes = full_text.encode("utf-8")
    assert ext["original_byte_count"] == len(raw_bytes)
    assert ext["sha256"] == hashlib.sha256(raw_bytes).hexdigest()


def test_unicode_multibyte_chars_use_character_count_not_byte_count(tmp_path):
    agent = _Agent(tmp_path)
    # Each CJK char is 1 Python `str` character but 3 UTF-8 bytes, so a
    # detail of ~9000 chars stays *under* the char cap while its UTF-8 byte
    # count would be ~27000 — proving the cap counts characters, not bytes.
    detail = "中" * 9000
    upsert(agent, "unicode-small", {"title": "t", "detail": detail, "source": "s"})
    entry = _entry(tmp_path)
    assert "externalized" not in entry
    assert entry["detail"].startswith("中")

    # Now push the character count itself over the cap.
    big_detail = "中" * 20_000
    upsert(agent, "unicode-big", {"title": "t2", "detail": big_detail, "source": "s"})
    entries = snapshot_notifications(tmp_path)["nudge"]["data"]["nudges"]
    big_entry = next(e for e in entries if e["kind"] == "unicode-big")
    assert "externalized" in big_entry
    ext = big_entry["externalized"]
    sidecar_text = open(ext["path"], encoding="utf-8").read()
    full = json.loads(sidecar_text)
    assert full["detail"].startswith(big_detail)
    # Char count in the manifest is Python str length, byte count is UTF-8.
    assert ext["original_char_count"] == len(sidecar_text)
    assert ext["original_byte_count"] == len(sidecar_text.encode("utf-8"))
    assert ext["original_byte_count"] > ext["original_char_count"]


def test_sidecar_file_permissions_are_owner_only(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "perm-check", {"title": "t", "detail": "z" * 20_000, "source": "s"})
    entry = _entry(tmp_path)
    sidecar_path = Path(entry["externalized"]["path"])
    file_mode = stat.S_IMODE(sidecar_path.stat().st_mode)
    assert file_mode == 0o600

    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    dir_mode = stat.S_IMODE(findings_dir.stat().st_mode)
    assert dir_mode == 0o700


def test_stable_content_addressed_reuse_same_finding_no_new_file_each_heartbeat(tmp_path):
    agent = _Agent(tmp_path)
    body = {"title": "Repeated finding", "detail": "w" * 20_000, "source": "producer"}

    upsert(agent, "repeated", dict(body))
    entry1 = _entry(tmp_path)
    path1 = entry1["externalized"]["path"]
    sha1 = entry1["externalized"]["sha256"]

    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    files_after_first = sorted(findings_dir.iterdir())
    assert len(files_after_first) == 1

    # Simulate the next heartbeat re-upserting the SAME finding facts.
    upsert(agent, "repeated", dict(body))
    entry2 = _entry(tmp_path)
    assert entry2["externalized"]["path"] == path1
    assert entry2["externalized"]["sha256"] == sha1

    files_after_second = sorted(findings_dir.iterdir())
    assert files_after_second == files_after_first, "same finding must not create a new sidecar file"


def test_different_findings_get_distinct_content_addressed_files(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "kind-a", {"title": "A", "detail": "a" * 20_000, "source": "s"})
    upsert(agent, "kind-b", {"title": "B", "detail": "b" * 20_000, "source": "s"})

    entries = snapshot_notifications(tmp_path)["nudge"]["data"]["nudges"]
    paths = {e["kind"]: e["externalized"]["path"] for e in entries}
    assert paths["kind-a"] != paths["kind-b"]

    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    assert len(list(findings_dir.iterdir())) == 2


def test_externalization_failure_raises_and_leaves_notification_state_unchanged(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)

    # Seed a prior, unrelated small finding so there is real persisted
    # notification state to prove untouched byte-for-byte after the failure.
    upsert(agent, "prior-finding", {"title": "Prior", "detail": "unrelated", "source": "s"})

    # Also seed an EXPIRED dismissal record for the exact finding that is
    # about to fail to externalize. `_dismissed_until(...) > time.time()` is
    # False for an expired record, so `upsert` proceeds past the mute guard
    # and reaches `_clear_dismissal` — exactly the internal step whose
    # ordering relative to `_cap_inline_payload` this test protects. If
    # `_clear_dismissal` ran BEFORE externalization (the pre-fix ordering),
    # this record would be popped even though the upsert ultimately raises;
    # with the fix, a raised `NudgeExternalizationError` must leave it
    # completely untouched.
    from lingtai.kernel import nudge as nudge_module

    kind = "fails-to-write"
    big_detail = "q" * 30_000
    # Must match exactly what `upsert` builds before fingerprinting: the
    # producer detail with the policy message appended, not the raw detail.
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
    seeded_state = {
        "dismissed": {
            fingerprint: {
                "kind": kind,
                "dismissed_at": 1.0,
                "until": 2.0,  # far in the past — expired, so the mute guard does not short-circuit
            }
        }
    }
    state_path.write_text(json.dumps(seeded_state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    before_notification_raw = notification_store_for(tmp_path).snapshot(lambda _c: True)
    before_state_raw = state_path.read_bytes()

    findings_dir = workdir_layout(tmp_path).nudge_findings_dir

    # Inject the write failure through the narrow private I/O seam instead of
    # real directory permissions: production's own directory-permission
    # repair (`os.chmod(findings_dir, 0o700)` in `_cap_inline_payload`) would
    # otherwise re-loosen a chmodded-read-only directory before the write is
    # even attempted, making a permission-based injection unreliable.
    def _raise_injected(target, text):
        raise OSError("injected")

    monkeypatch.setattr(nudge_module, "_write_sidecar_atomic", _raise_injected)

    with pytest.raises(NudgeExternalizationError) as excinfo:
        upsert(agent, kind, {"title": "t", "detail": big_detail, "source": "s"})

    # Bounded, static message: no oversized body, no producer-controlled
    # text (title/detail/source) leaked into the exception.
    message = str(excinfo.value)
    assert len(message) < 300
    assert big_detail not in message
    assert "t" != message  # sanity: not accidentally the raw title
    assert kind not in message  # kind itself not required in message

    # `.notification/nudge.json` (and every other channel) must be
    # byte-for-byte identical to before the failed upsert — no partial
    # write, no compact-with-path=None fallback, nothing persisted.
    after_notification_raw = notification_store_for(tmp_path).snapshot(lambda _c: True)
    assert after_notification_raw == before_notification_raw

    # `.notification/.nudge_state.json` (the dismissal-mute state) must
    # ALSO be byte-for-byte identical — `_clear_dismissal` must not have
    # run, since it now only runs after externalization succeeds.
    after_state_raw = state_path.read_bytes()
    assert after_state_raw == before_state_raw

    # No FINAL content-addressed sidecar file exists for the failed finding
    # — the injected helper raised before any bytes were written, so there
    # is nothing at the content-addressed target path to read back.
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
    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    assert not findings_dir.exists()


def test_dismissal_mutes_capped_finding_using_stable_fingerprint(tmp_path, monkeypatch):
    agent = _Agent(tmp_path)
    monkeypatch.setenv("LINGTAI_NUDGE_REPEAT_INTERVAL", "0.001s")
    body = {"title": "Capped finding", "detail": "r" * 30_000, "source": "s"}

    upsert(agent, "capped-dismiss", dict(body))
    entry = _entry(tmp_path)
    assert "externalized" in entry

    result = dismiss_channel(agent, "nudge", invoked_by="notification", force=True)
    assert result["status"] == "ok"
    assert snapshot_notifications(tmp_path).get("nudge", {}) in ({}, None) or \
        snapshot_notifications(tmp_path)["nudge"].get("data", {}).get("nudges") in (None, [])

    # Dismissal is mute: re-upserting the SAME finding must not recreate it
    # immediately, exactly as for an ordinary (non-capped) finding.
    upsert(agent, "capped-dismiss", dict(body))
    entries = snapshot_notifications(tmp_path).get("nudge", {}).get("data", {}).get("nudges", [])
    assert entries == []

    import time
    time.sleep(0.01)
    upsert(agent, "capped-dismiss", dict(body))
    entries = snapshot_notifications(tmp_path).get("nudge", {}).get("data", {}).get("nudges", [])
    assert len(entries) == 1


def test_no_dismiss_fingerprint_sentinel_leaks_into_persisted_meta_visible_payload(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "small-fp", {"title": "t", "detail": "short", "source": "s"})
    entry = _entry(tmp_path)
    # The internal bookkeeping fingerprint field must never reach the
    # model/meta-visible persisted payload for an ordinary small finding.
    assert "_dismiss_fingerprint" not in entry


def test_record_dismissal_reads_capped_entry_without_crashing(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "capped-record", {"title": "t", "detail": "s" * 30_000, "source": "s"})
    # record_dismissal is called by notifications.dismiss_channel before it
    # clears the channel; call it directly to prove it tolerates a capped
    # (compact) persisted entry shape.
    record_dismissal(agent)


def test_oversized_kind_raises_and_leaves_state_untouched_and_writes_no_sidecar(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "prior", {"title": "Prior", "detail": "unrelated", "source": "s"})
    before = notification_store_for(tmp_path).snapshot(lambda _c: True)

    pathological_kind = "k" * 5000
    with pytest.raises(NudgeExternalizationError) as excinfo:
        upsert(agent, pathological_kind, {"title": "t", "detail": "d" * 20_000, "source": "s"})

    message = str(excinfo.value)
    assert len(message) < 300
    assert pathological_kind not in message

    after = notification_store_for(tmp_path).snapshot(lambda _c: True)
    assert after == before

    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    assert not findings_dir.exists() or list(findings_dir.iterdir()) == []


def test_escape_heavy_kind_raises_and_leaves_state_untouched_and_writes_no_sidecar(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "prior2", {"title": "Prior", "detail": "unrelated", "source": "s"})
    before = notification_store_for(tmp_path).snapshot(lambda _c: True)

    escape_heavy_kind = "../../../etc/passwd\x00\n\r;rm -rf /"
    with pytest.raises(NudgeExternalizationError) as excinfo:
        upsert(agent, escape_heavy_kind, {"title": "t", "detail": "d" * 20_000, "source": "s"})

    message = str(excinfo.value)
    assert len(message) < 300
    assert escape_heavy_kind not in message
    assert "/etc/passwd" not in message
    assert "rm -rf" not in message

    after = notification_store_for(tmp_path).snapshot(lambda _c: True)
    assert after == before

    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    assert not findings_dir.exists() or list(findings_dir.iterdir()) == []


def test_short_escape_heavy_kind_also_raises_even_when_finding_is_small(tmp_path):
    # Kind validation happens before the size check, so even a SMALL finding
    # body with a malformed kind is rejected rather than silently accepted.
    agent = _Agent(tmp_path)
    before = notification_store_for(tmp_path).snapshot(lambda _c: True)
    with pytest.raises(NudgeExternalizationError):
        upsert(agent, "bad kind!", {"title": "t", "detail": "short", "source": "s"})
    after = notification_store_for(tmp_path).snapshot(lambda _c: True)
    assert after == before


def test_builtin_kind_shapes_remain_valid(tmp_path):
    # Current short built-in kinds must be unaffected by the new validation.
    agent = _Agent(tmp_path)
    for kind in ("kernel_version", "source_drift", "init_config_shape"):
        upsert(agent, kind, {"title": "t", "detail": "short detail", "source": "s"})
    entries = snapshot_notifications(tmp_path)["nudge"]["data"]["nudges"]
    assert {e["kind"] for e in entries} == {"kernel_version", "source_drift", "init_config_shape"}


def test_findings_dir_permissions_enforced_even_when_preexisting_and_loose(tmp_path):
    findings_dir = workdir_layout(tmp_path).nudge_findings_dir
    findings_dir.mkdir(parents=True)
    # Simulate a directory that pre-existed with looser permissions (e.g.
    # created before this cap existed, or by an external process).
    findings_dir.chmod(0o755)
    assert stat.S_IMODE(findings_dir.stat().st_mode) == 0o755

    agent = _Agent(tmp_path)
    upsert(agent, "loose-dir", {"title": "t", "detail": "z" * 20_000, "source": "s"})

    # After upsert, the directory must be owner-only regardless of the
    # looser permissions it started with.
    assert stat.S_IMODE(findings_dir.stat().st_mode) == 0o700
    entry = _entry(tmp_path)
    sidecar_path = Path(entry["externalized"]["path"])
    assert stat.S_IMODE(sidecar_path.stat().st_mode) == 0o600


def test_sidecar_path_is_under_tmp_nudge_findings_not_notification_dir(tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "path-shape", {"title": "t", "detail": "v" * 20_000, "source": "s"})
    entry = _entry(tmp_path)
    sidecar_path = Path(entry["externalized"]["path"])
    assert sidecar_path.is_absolute()
    assert sidecar_path.is_file()
    relative = sidecar_path.relative_to(Path(tmp_path))
    assert relative.parts[:2] == ("tmp", "nudge-findings")
    assert ".notification" not in relative.parts
