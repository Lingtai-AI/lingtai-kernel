"""Tests for the shared filesystem / JSON / JSONL helpers (issue #510)."""

import json
import os
import stat

import pytest

from lingtai_kernel import _fsutil


# --------------------------------------------------------------------------- #
# atomic_write_text / atomic_write_json
# --------------------------------------------------------------------------- #


def test_atomic_write_text_roundtrip(tmp_path):
    target = tmp_path / "a.txt"
    _fsutil.atomic_write_text(target, "héllo wörld")
    assert target.read_text(encoding="utf-8") == "héllo wörld"


def test_atomic_write_text_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "deep" / "a.txt"
    _fsutil.atomic_write_text(target, "x")
    assert target.read_text() == "x"


def test_atomic_write_text_overwrites_existing(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old")
    _fsutil.atomic_write_text(target, "new")
    assert target.read_text() == "new"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits required")
def test_atomic_write_text_preserves_existing_mode_when_requested(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old")
    target.chmod(0o600)

    _fsutil.atomic_write_text(target, "new", preserve_existing_mode=True)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits required")
def test_atomic_write_text_default_still_uses_umask_for_replacement(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old")
    target.chmod(0o600)
    old_umask = os.umask(0o022)
    try:
        _fsutil.atomic_write_text(target, "new")
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_atomic_write_temp_is_sibling_of_target(tmp_path, monkeypatch):
    # The temp file must live in the target's directory so os.replace is atomic
    # (same filesystem).  Capture the temp path os.replace sees.
    target = tmp_path / "sub" / "a.txt"
    target.parent.mkdir(parents=True)
    seen = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        seen["src"] = src
        return real_replace(src, dst)

    monkeypatch.setattr(_fsutil.os, "replace", spy_replace)
    _fsutil.atomic_write_text(target, "x")
    assert os.path.dirname(seen["src"]) == str(target.parent)


def test_atomic_write_leaves_no_temp_litter(tmp_path):
    target = tmp_path / "a.txt"
    _fsutil.atomic_write_text(target, "x")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "a.txt"]
    assert leftovers == []


def test_atomic_write_json_failure_does_not_clobber_or_litter(tmp_path):
    target = tmp_path / "a.json"
    target.write_text('{"keep": true}')
    with pytest.raises(TypeError):
        _fsutil.atomic_write_json(target, {"bad": object()})
    # Original preserved, no temp file left behind.
    assert json.loads(target.read_text()) == {"keep": True}
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "a.json"]
    assert leftovers == []


def test_atomic_write_json_preserves_utf8_by_default(tmp_path):
    target = tmp_path / "a.json"
    _fsutil.atomic_write_json(target, {"name": "灵台"})
    raw = target.read_text(encoding="utf-8")
    assert "灵台" in raw  # not \uXXXX-escaped
    assert json.loads(raw) == {"name": "灵台"}


def test_atomic_write_json_default_format_has_no_trailing_newline(tmp_path):
    target = tmp_path / "a.json"
    _fsutil.atomic_write_json(target, {"b": 1, "a": 2})
    expected = json.dumps({"b": 1, "a": 2}, ensure_ascii=False, indent=2)
    assert target.read_text(encoding="utf-8") == expected
    assert not expected.endswith("\n")


def test_atomic_write_json_fsync_opt_in(tmp_path):
    # fsync=True must not change file content; just exercises the durability path.
    target = tmp_path / "a.json"
    _fsutil.atomic_write_json(target, {"x": 1}, fsync=True)
    assert json.loads(target.read_text()) == {"x": 1}


def test_unique_tmp_differs_for_same_target_in_one_process(tmp_path):
    # Two temp paths for the same target within one process must differ, or two
    # concurrent same-process writers would race on a shared temp file.  A
    # pid-only suffix would (incorrectly) return identical paths here.
    target = tmp_path / "state.json"
    paths = {_fsutil._unique_tmp(target) for _ in range(100)}
    assert len(paths) == 100
    # All siblings of the target (so os.replace stays same-filesystem atomic).
    assert all(p.parent == target.parent for p in paths)


def test_concurrent_same_target_writes_all_succeed(tmp_path):
    # Many threads writing the same target concurrently must all complete
    # without raising (no shared temp path / no FileNotFoundError at replace),
    # the final file must be a complete one of the written payloads, and no
    # temp litter may survive.
    import threading

    target = tmp_path / "state.json"
    n = 24
    errors = []
    barrier = threading.Barrier(n)

    def writer(i):
        try:
            barrier.wait()  # maximise overlap on the same target
            _fsutil.atomic_write_text(target, f"payload-{i:03d}")
        except BaseException as e:  # noqa: BLE001 - record, assert in caller
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = target.read_text()
    assert final in {f"payload-{i:03d}" for i in range(n)}  # never torn
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftovers == []


# --------------------------------------------------------------------------- #
# read_json
# --------------------------------------------------------------------------- #


def test_read_json_roundtrip(tmp_path):
    target = tmp_path / "a.json"
    _fsutil.atomic_write_json(target, {"k": "v"})
    assert _fsutil.read_json(target) == {"k": "v"}


def test_read_json_missing_returns_default(tmp_path):
    assert _fsutil.read_json(tmp_path / "missing.json", default={}) == {}


def test_read_json_missing_raises_without_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        _fsutil.read_json(tmp_path / "missing.json")


def test_read_json_malformed_returns_default(tmp_path):
    target = tmp_path / "a.json"
    target.write_text("{not json")
    assert _fsutil.read_json(target, default=None) is None


def test_read_json_malformed_raises_without_default(tmp_path):
    target = tmp_path / "a.json"
    target.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        _fsutil.read_json(target)


def test_read_json_expect_type_mismatch_returns_default(tmp_path):
    target = tmp_path / "a.json"
    target.write_text("[1, 2, 3]")
    assert _fsutil.read_json(target, default={}, expect=dict) == {}


def test_read_json_expect_type_mismatch_raises_without_default(tmp_path):
    target = tmp_path / "a.json"
    target.write_text("[1, 2, 3]")
    with pytest.raises(TypeError):
        _fsutil.read_json(target, expect=dict)


def test_read_json_expect_type_match(tmp_path):
    target = tmp_path / "a.json"
    target.write_text("[1, 2, 3]")
    assert _fsutil.read_json(target, expect=list) == [1, 2, 3]


# --------------------------------------------------------------------------- #
# append_jsonl / iter_jsonl_records / tail_jsonl_records
# --------------------------------------------------------------------------- #


def test_append_jsonl_returns_record_offset(tmp_path):
    target = tmp_path / "log.jsonl"
    off0 = _fsutil.append_jsonl(target, {"i": 0})
    off1 = _fsutil.append_jsonl(target, {"i": 1})
    assert off0 == 0
    # Second record begins right after the first record's bytes (incl newline).
    first_len = len((json.dumps({"i": 0}) + "\n").encode("utf-8"))
    assert off1 == first_len
    # Offsets actually index the record's first byte.
    raw = target.read_bytes()
    assert raw[off1:].startswith(b'{"i": 1}')


def test_append_jsonl_ascii_escaped_by_default(tmp_path):
    target = tmp_path / "log.jsonl"
    _fsutil.append_jsonl(target, {"name": "灵台"})
    raw = target.read_text(encoding="utf-8")
    assert "\\u" in raw  # ensure_ascii=True default escapes non-ASCII


def test_append_jsonl_can_preserve_utf8(tmp_path):
    target = tmp_path / "log.jsonl"
    _fsutil.append_jsonl(target, {"name": "灵台"}, ensure_ascii=False)
    raw = target.read_text(encoding="utf-8")
    assert "灵台" in raw


def test_append_jsonl_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "log.jsonl"
    _fsutil.append_jsonl(target, {"i": 0})
    assert target.is_file()


def test_iter_jsonl_records_in_order(tmp_path):
    target = tmp_path / "log.jsonl"
    for i in range(3):
        _fsutil.append_jsonl(target, {"i": i})
    assert [r["i"] for r in _fsutil.iter_jsonl_records(target)] == [0, 1, 2]


def test_iter_jsonl_missing_file_yields_nothing(tmp_path):
    assert list(_fsutil.iter_jsonl_records(tmp_path / "nope.jsonl")) == []


def test_iter_jsonl_skips_blank_and_torn_lines(tmp_path):
    target = tmp_path / "log.jsonl"
    target.write_text('{"i": 0}\n\n{"i": 1}\n{"torn":')
    assert [r["i"] for r in _fsutil.iter_jsonl_records(target)] == [0, 1]


def test_iter_jsonl_can_surface_invalid(tmp_path):
    target = tmp_path / "log.jsonl"
    target.write_text('{"i": 0}\n{bad}\n')
    with pytest.raises(json.JSONDecodeError):
        list(_fsutil.iter_jsonl_records(target, skip_invalid=False))


def test_tail_jsonl_records(tmp_path):
    target = tmp_path / "log.jsonl"
    for i in range(5):
        _fsutil.append_jsonl(target, {"i": i})
    assert [r["i"] for r in _fsutil.tail_jsonl_records(target, 2)] == [3, 4]


def test_tail_jsonl_zero_or_negative(tmp_path):
    target = tmp_path / "log.jsonl"
    _fsutil.append_jsonl(target, {"i": 0})
    assert _fsutil.tail_jsonl_records(target, 0) == []
    assert _fsutil.tail_jsonl_records(target, -1) == []


# --------------------------------------------------------------------------- #
# utc_now_iso
# --------------------------------------------------------------------------- #


def test_utc_now_iso_is_timezone_aware_utc():
    from datetime import datetime

    s = _fsutil.utc_now_iso()
    parsed = datetime.fromisoformat(s)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


# --------------------------------------------------------------------------- #
# Migration parity: Workdir.write_manifest must stay byte-identical (issue #510)
# --------------------------------------------------------------------------- #


def test_write_manifest_byte_identical_to_legacy_format(tmp_path):
    """Golden test proving the first migration preserves the public file format.

    The legacy implementation wrote
    ``json.dumps(manifest, indent=2, ensure_ascii=False)`` (no trailing
    newline) via a temp file + os.replace.  After migrating ``write_manifest``
    to ``_fsutil.atomic_write_json`` the on-disk bytes must be unchanged.
    """
    from lingtai_kernel.workdir import WorkingDir

    manifest = {
        "name": "灵台",          # non-ASCII must be preserved, not escaped
        "z": 1,
        "a": [1, 2, {"nested": "ünïcode"}],
    }
    legacy_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")

    wd = WorkingDir(tmp_path)
    wd.write_manifest(manifest)
    written = (tmp_path / ".agent.json").read_bytes()

    assert written == legacy_bytes
    # Round-trips through the kernel's own reader.
    assert wd.read_full_manifest() == manifest
